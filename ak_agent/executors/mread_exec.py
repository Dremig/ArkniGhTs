"""Live structured state from mread, with separately verified UI actions."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from ..grid import BoardCalib
from ..model import Direction, GridPos, OpResult, OpType
from .adb_exec import AdbExecutor


@dataclass(frozen=True)
class LiveSnapshot:
    payload: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return deepcopy(self.payload)


class MreadExecutor(AdbExecutor):
    """Use passive process memory as the source of truth.

    State reads never use screenshots. Actions remain normal Android input and
    are reported successful only after a fresh memory snapshot confirms their
    expected effect. This class does not write game memory or send protocols.
    """
    name = "mread+adb"
    structured_only = True
    package = "com.hypergryph.arknights.bilibili"

    def __init__(self, *, serial: str, calib: BoardCalib | None,
                 state_reader: Path, mread_port: int, adb_path: str):
        super().__init__(serial, calib, adb_path)
        self._state_reader = state_reader
        self._mread_port = mread_port
        self._adb_path = adb_path
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self._game_pid: int | None = None
        self._validate_display()
        self._ensure_runtime()

    def _validate_display(self) -> None:
        if self._calib is None:
            raise RuntimeError("AK_CALIB is required for the live action backend")
        output = self._adb("shell", "wm", "size")
        sizes = re.findall(r"(\d+)x(\d+)", output)
        if not sizes:
            raise RuntimeError(f"cannot parse device display size: {output.strip()}")
        width, height = map(int, sizes[-1])
        if sorted((width, height)) != sorted((self._calib.width, self._calib.height)):
            raise RuntimeError(
                f"calibration is {self._calib.width}x{self._calib.height}, "
                f"device is {width}x{height}; set AK_CALIB to a verified profile")

    def _pidof(self, process: str) -> list[int]:
        cmd = [self._adb_path, "-s", self._serial, "shell", "pidof", process]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 1:
            return []
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"pidof {process} failed")
        return [int(value) for value in result.stdout.split()]

    def _ensure_root(self) -> None:
        if self._adb("shell", "id", "-u").strip() == "0":
            return
        result = subprocess.run([self._adb_path, "-s", self._serial, "root"],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "adb root failed")
        subprocess.run([self._adb_path, "-s", self._serial, "wait-for-device"],
                       check=True, timeout=20)

    def _ensure_runtime(self) -> int:
        self._ensure_root()
        pids = self._pidof(self.package)
        if len(pids) != 1:
            raise RuntimeError(f"expected one {self.package} pid, got {pids}")
        game_pid = pids[0]
        self._adb("forward", f"tcp:{self._mread_port}", f"tcp:{self._mread_port}")
        helpers = self._pidof("mread")
        correct = False
        for helper in helpers:
            command = self._adb("shell", "cat", f"/proc/{helper}/cmdline")
            if f"mread\x00{game_pid}\x00serve\x00{self._mread_port}" in command:
                correct = True
            else:
                self._adb("shell", "kill", str(helper))
        if not correct:
            remote = "/data/local/tmp/mread"
            if "mread" not in self._adb("shell", "ls", "-l", remote):
                raise RuntimeError(f"device helper missing: {remote}")
            launch = (f"nohup {remote} {game_pid} serve {self._mread_port} "
                      f">/data/local/tmp/mread.log 2>&1 </dev/null &")
            self._adb("shell", launch)
            for attempt in range(20):
                helpers = self._pidof("mread")
                if helpers:
                    break
                if attempt == 19:
                    raise RuntimeError("mread helper did not start")
                time.sleep(0.1)
        if game_pid != self._game_pid:
            self._cache = None
            self._game_pid = game_pid
        return game_pid

    def _read_live(self, *, cached: bool = True, team_only: bool = False) -> dict[str, Any]:
        if (not team_only and cached and self._cache is not None and
                time.monotonic() - self._cache_at < 0.25):
            return deepcopy(self._cache)
        if not self._state_reader.is_file():
            raise RuntimeError(f"state reader not found: {self._state_reader}")
        game_pid = self._ensure_runtime()
        cmd = [sys.executable, str(self._state_reader), "--serial", self._serial,
               "--adb", self._adb_path, "--port", str(self._mread_port),
               "--pid", str(game_pid)]
        if team_only:
            cmd.append("--team-only")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(result.stderr.strip() or "state reader returned invalid JSON") from exc
        if result.returncode or not payload.get("available") or not payload.get("live"):
            raise RuntimeError(payload.get("error") or "live memory state is unavailable")
        if payload.get("source") != "process_memory":
            raise RuntimeError(f"unexpected state source: {payload.get('source')}")
        if not team_only:
            self._cache, self._cache_at = payload, time.monotonic()
        return deepcopy(payload)

    def get_state(self) -> LiveSnapshot:
        return LiveSnapshot(self._read_live())

    def get_team(self) -> dict | None:
        team = self._read_live(cached=False, team_only=True).get("team")
        return deepcopy(team) if team else None

    @staticmethod
    def _piece_at(snapshot: dict, pos: GridPos) -> dict | None:
        for piece in (snapshot.get("board", []) + snapshot.get("hand", []) +
                      snapshot.get("other_pieces", [])):
            if piece.get("pos") == {"row": pos.row, "col": pos.col}:
                return piece
        return None

    def _after_dispatch(self, before: dict, operation: str,
                        dispatched: OpResult, predicate, *, team_only: bool = False) -> OpResult:
        if not dispatched.ok:
            return dispatched
        time.sleep(0.45)
        try:
            last_error = None
            for attempt in range(3):
                try:
                    after = self._read_live(cached=False, team_only=team_only)
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt == 2:
                        raise
                    time.sleep(0.45)
            else:  # pragma: no cover
                raise last_error or RuntimeError("verification retry exhausted")
            if after.get("unavailable"):
                raise RuntimeError(f"partial snapshot: {after['unavailable']}")
            if (after.get("process", {}).get("pid") != before.get("process", {}).get("pid") or
                    after.get("process", {}).get("layout_build_id") !=
                    before.get("process", {}).get("layout_build_id") or
                    after.get("act_id") != before.get("act_id") or
                    after.get("stage_id") != before.get("stage_id")):
                raise RuntimeError("runtime identity changed during action verification")
            verified, detail = predicate(before, after)
        except Exception as exc:  # action was sent; never retry it automatically
            return OpResult(False, operation, f"dispatched but verification failed: {exc}", self.name,
                            dispatched=True, verified=False,
                            before_snapshot_id=before.get("snapshot_id", ""))
        return OpResult(bool(verified), operation, detail, self.name,
                        dispatched=True, verified=bool(verified),
                        before_snapshot_id=before.get("snapshot_id", ""),
                        after_snapshot_id=after.get("snapshot_id", ""))

    def do_op(self, op: OpType, start: GridPos, end: GridPos,
              direction: Direction = Direction.INVALID) -> OpResult:
        before = self._read_live()
        source = self._piece_at(before, start)
        if source is None:
            return OpResult(False, op.name, "source position is absent from live state", self.name)
        if before.get("shop_opened"):
            closed = super().tap_ui("shop_close")
            if not closed.ok:
                return closed
            time.sleep(0.25)
        if op == OpType.WITHDRAW:
            if not source.get("in_battle"):
                return OpResult(False, op.name, "source is not deployed", self.name)
            x, y = self._pos_px(start, pick=True)
            dispatched = super().tap_raw(x, y)
            if dispatched.ok:
                time.sleep(0.35)
                dispatched = super().tap_ui("withdraw_board_button")
        else:
            dispatched = super().do_op(op, start, end, direction)

        def verify(_before, after):
            if source is None:
                return False, "input was dispatched, but the source position was absent before it"
            inst_id = source["inst_id"]
            if after.get("round") != _before.get("round"):
                return False, "round changed before the board operation could be verified"
            all_after = (after.get("board", []) + after.get("hand", []) +
                         after.get("other_pieces", []))
            current = next((piece for piece in all_after if piece["inst_id"] == inst_id), None)
            if op == OpType.WITHDRAW:
                ok = current is not None and current.get("in_hand")
            elif op == OpType.MOVE_ONLY:
                ok = current is not None and current.get("pos") == {"row": end.row, "col": end.col}
                if ok and direction != Direction.INVALID:
                    ok = current.get("direction") == int(direction)
            elif op in (OpType.EQUIP_ITEM, OpType.REPLACE_EQUIP):
                target = self._piece_at(after, end)
                ok = (current is None and target is not None and
                      any(item.get("inst_id") == inst_id for item in target.get("equipment", [])))
            else:
                ok = current is None  # consumed magic item
            return ok, ("confirmed by live memory" if ok else
                        "input was dispatched, but live memory did not show the expected piece transition")

        return self._after_dispatch(before, op.name, dispatched, verify)

    def sell_or_destroy(self, pos: GridPos) -> OpResult:
        before = self._read_live()
        source = self._piece_at(before, pos)
        if source is None:
            return OpResult(False, "sell_or_destroy", "source position is absent from live state", self.name)
        x, y = self._pos_px(pos, pick=True)
        dispatched = super().tap_raw(x, y)
        if dispatched.ok:
            time.sleep(0.35)
            menu = "sell_board_button" if source.get("in_battle") else "sell_hand_button"
            dispatched = super().tap_ui(menu)

        def verify(_before, after):
            remains = source is not None and any(
                piece["inst_id"] == source["inst_id"]
                for piece in (after.get("board", []) + after.get("hand", []) +
                              after.get("other_pieces", [])))
            refunded = after.get("shop", {}).get("coin", 0) > _before.get("shop", {}).get("coin", 0)
            ok = not remains and refunded
            return ok, ("confirmed by live memory" if ok else
                        "input was dispatched, but the piece still exists in live memory")

        return self._after_dispatch(before, "sell_or_destroy", dispatched, verify)

    def send_request(self, name: str, args: dict) -> OpResult:
        """Compatibility with semantic tools; dispatches calibrated UI, not a protocol."""
        team_action = name in {"choose_strategy", "team_ready", "start_match",
                               "enemy_assign_ready", "diy_ready", "diy_revoke"}
        before = self._read_live(cached=False, team_only=True) if team_action else self._read_live()
        shop_before = before.get("shop", {})
        if name.startswith("shop_") and not before.get("shop_opened"):
            opened = super().tap_ui("shop_open")
            if not opened.ok:
                return opened
            time.sleep(0.3)
        if name == "choose_strategy":
            if args.get("skip"):
                return OpResult(False, name, "skip UI mapping is not verified", self.name)
            strategy = args.get("strategy")
            actor_uid = (before.get("team", {}).get("strategy_decision", {})
                         .get("current_uid"))
            if not actor_uid:
                return OpResult(False, name, "team state has no current strategy chooser", self.name)
            ui_name = f"strategy_{strategy}"

            def verify(_before, after):
                team = after.get("team") or {}
                selected = any(player.get("uid") == actor_uid and
                               player.get("strategy") == strategy
                               for player in team.get("players", []))
                recorded = any(choice.get("uid") == actor_uid and
                               choice.get("strategy") == strategy
                               for choice in team.get("strategy_decision", {}).get("choices", []))
                ok = selected or recorded
                return ok, ("strategy confirmed by live team state" if ok else
                            "tap dispatched, but strategy was not confirmed")
        elif name == "shop_buy":
            slot = args.get("slotId")
            good = next((value for value in shop_before.get("goods", [])
                         if value.get("slot_id") == slot), None)
            if good is None:
                return OpResult(False, name, f"slot {slot} is absent from live shop state", self.name)
            if shop_before.get("coin", 0) < good["price"]:
                return OpResult(False, name, "insufficient shop coin", self.name)
            ui_name = f"buy_slot_{slot}"

            def verify(_before, after):
                coin_ok = after["shop"]["coin"] == shop_before["coin"] - good["price"]
                gained = len(after.get("hand", [])) > len(_before.get("hand", []))
                changed = next((value for value in after["shop"]["goods"]
                                if value.get("slot_id") == slot), None) != good
                ok = coin_ok and (gained or changed)
                return ok, ("purchase confirmed by coin and inventory/shop state" if ok else
                            "tap dispatched, but purchase was not confirmed by live memory")
        elif name == "shop_refresh":
            if (shop_before.get("free_refresh_count", 0) <= 0 and
                    shop_before.get("coin", 0) < shop_before.get("refresh_price", 0)):
                return OpResult(False, name, "insufficient shop coin", self.name)
            ui_name = "shop_refresh"

            def verify(_before, after):
                changed = (after["shop"]["refresh_sequence"] != shop_before["refresh_sequence"] or
                           after["shop"]["goods"] != shop_before["goods"])
                expected_coin = shop_before["coin"] - shop_before["refresh_price"]
                ok = changed and after["shop"]["coin"] in (expected_coin, shop_before["coin"])
                return ok, ("refresh confirmed by live shop state" if ok else
                            "tap dispatched, but refresh was not confirmed by live memory")
        elif name == "shop_froze":
            desired = args.get("isFrozen")
            if type(desired) is not bool:
                return OpResult(False, name, "isFrozen must be boolean", self.name)
            goods_before = shop_before.get("goods", [])
            if goods_before and all(value.get("frozen") == desired for value in goods_before):
                snapshot = before.get("snapshot_id", "")
                return OpResult(True, name, "requested freeze state already active", self.name,
                                dispatched=False, verified=True,
                                before_snapshot_id=snapshot, after_snapshot_id=snapshot)
            ui_name = "shop_freeze"

            def verify(_before, after):
                goods = after["shop"]["goods"]
                ok = bool(goods) and all(value["frozen"] == desired for value in goods)
                return ok, ("freeze state confirmed by live memory" if ok else
                            "tap dispatched, but freeze state was not confirmed")
        elif name == "shop_upgrade":
            if shop_before.get("coin", 0) < shop_before.get("upgrade_price", 0):
                return OpResult(False, name, "insufficient shop coin", self.name)
            ui_name = "shop_upgrade"

            def verify(_before, after):
                ok = after["shop"]["level"] > shop_before["level"]
                return ok, ("shop level confirmed by live memory" if ok else
                            "tap dispatched, but shop level did not change")
        elif name == "prep_ready":
            desired = args.get("readyType", 1) == 1
            if before.get("self_ready") == desired:
                snapshot = before.get("snapshot_id", "")
                return OpResult(True, name, "requested ready state already active", self.name,
                                dispatched=False, verified=True,
                                before_snapshot_id=snapshot, after_snapshot_id=snapshot)
            ui_name = "ready"

            def verify(_before, after):
                ok = (after.get("self_ready") == desired or
                      after.get("phase") != _before.get("phase") or
                      after.get("round") != _before.get("round"))
                return ok, ("ready state confirmed by live memory" if ok else
                            "tap dispatched, but ready/phase state did not change")
        elif name in ("self_pick", "sp_pick"):
            slot = args.get("slotId")
            if type(slot) is not int:
                return OpResult(False, name, "slotId must be integer", self.name)
            ui_name = f"buff_{slot}"

            def verify(_before, after):
                ok = after.get("choice") != _before.get("choice") or after.get("phase") != _before.get("phase")
                return ok, ("choice confirmed by live memory" if ok else
                            "tap dispatched, but choice/phase state did not change")
        else:
            return OpResult(False, name,
                            "unsupported without a verified UI mapping; no protocol was sent", self.name)
        dispatched = super().tap_ui(ui_name)
        if name == "choose_strategy" and dispatched.ok:
            time.sleep(0.2)
            confirmed = super().tap_ui("strategy_ready")
            if not confirmed.ok:
                return confirmed
        if name == "prep_ready" and dispatched.ok and desired:
            time.sleep(0.2)
            confirmed = super().tap_ui("confirm")
            if not confirmed.ok:
                return confirmed
        if name == "shop_upgrade" and dispatched.ok:
            time.sleep(0.2)
            confirmed = super().tap_ui("shop_upgrade_confirm")
            if not confirmed.ok:
                return confirmed
        if name == "shop_buy" and dispatched.ok:
            # Every goods type opens a detail card. The second calibrated tap
            # confirms the purchase; the first tap alone never changes state.
            time.sleep(0.2)
            confirmed = super().tap_ui(f"buy_confirm_slot_{slot}")
            if not confirmed.ok:
                return confirmed
        return self._after_dispatch(before, name, dispatched, verify, team_only=team_action)
