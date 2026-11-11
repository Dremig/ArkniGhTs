"""Frida backend — invokes il2cpp methods in the game process directly.

JS side (frida/agent.ts → frida/dist/agent.js) uses frida-il2cpp-bridge
against the game's managed classes:
  AutoChessGameManager.instanceOrNull
    .dataCenter    → ChessMapInfo / playerDataModel / shopDataModel (state read)
    .playerOpManager → ConstructOperation + DoOperation (board ops)
  AutoChessService.SendRequest (shop/strategy/ready protocol ops)
"""
from __future__ import annotations

import json
from typing import Optional

import frida

from ..config import FRIDA_AGENT_JS, Config
from ..grid import BoardCalib
from ..model import (BattleState, ChessPiece, Direction, GameState, GridPos,
                     OpResult, OpType, PlayerState)
from .adb_exec import AdbExecutor


class FridaExecutor:
    name = "frida"

    def __init__(self, cfg: Config, calib: Optional[BoardCalib] = None):
        self._cfg = cfg
        self._adb = AdbExecutor(cfg.adb_serial, calib)  # screenshot fallback
        self._session: Optional[frida.core.Session] = None
        self._script: Optional[frida.core.Script] = None
        self._connect()

    # -- attach -------------------------------------------------------------
    def _connect(self) -> None:
        dev = (frida.get_usb_device(timeout=10)
               if self._cfg.frida_host == "usb"
               else frida.get_device_manager().add_remote_device(
                   self._cfg.frida_host))
        if self._cfg.spawn_game:
            pid = dev.spawn([self._cfg.frida_target])
            self._session = dev.attach(pid)
            dev.resume(pid)
        else:
            self._session = dev.attach(self._cfg.frida_target)
        src = FRIDA_AGENT_JS.read_text()
        self._script = self._session.create_script(src)
        self._script.on("message", self._on_msg)
        self._script.load()

    def _on_msg(self, msg, data):
        if msg.get("type") == "error":
            print(f"[frida] {msg.get('description','?')}")
        elif msg.get("type") == "send":
            print(f"[frida] {msg.get('payload')}")

    def _rpc(self, fn: str, *args):
        assert self._script is not None, "frida not attached"
        return getattr(self._script.exports_sync, fn)(*args)

    # -- executor surface ---------------------------------------------------
    def get_state(self) -> Optional[BattleState]:
        raw = self._rpc("getstate")
        if raw is None:
            return None
        d = json.loads(raw) if isinstance(raw, str) else raw
        if not d.get("in_battle"):
            return None
        st = BattleState(phase=GameState(d["game_state"]).name,
                         round=d.get("round", 0),
                         stage_id=d.get("stage_id", ""))
        if d.get("player"):
            p = d["player"]
            st.me = PlayerState(index=p["index"], hp=p["hp"],
                                shop_level=p["shop_level"],
                                shop_coin=p["shop_coin"],
                                max_deploy=p["max_deploy"],
                                streak=p["streak"],
                                game_state=p["game_state"],
                                garrison_bonds=p.get("bonds", {}))
            st.max_deploy = p["max_deploy"]
        for c in d.get("chess", []):
            piece = ChessPiece(
                inst_id=c["inst_id"], chess_id=c["chess_id"],
                pos=GridPos(c["row"], c["col"]), direction=c["dir"],
                item_type=c["item_type"], player_index=c["player_index"],
                side=c["side"], in_hand=c["in_hand"], in_battle=c["in_battle"],
                is_golden=c.get("golden", False))
            (st.hand if piece.in_hand else st.board).append(piece)
        st.deploy_count = sum(1 for p in st.board if p.item_type == 0)
        if d.get("shop"):
            st.shop = d["shop"]
        return st

    def screenshot(self, out_path: str) -> str:
        return self._adb.screenshot(out_path)

    def do_op(self, op: OpType, start: GridPos, end: GridPos,
              direction: Direction = Direction.INVALID) -> OpResult:
        try:
            ok = bool(self._rpc("doop", int(op), start.row, start.col,
                                end.row, end.col, int(direction)))
            return OpResult(ok, op.name,
                            f"{start.row},{start.col}->{end.row},{end.col} d={direction.name}",
                            self.name)
        except Exception as e:  # noqa: BLE001
            return OpResult(False, op.name, str(e), self.name)

    def sell_or_destroy(self, pos: GridPos) -> OpResult:
        try:
            ok = bool(self._rpc("sell", pos.row, pos.col))
            return OpResult(ok, "SELL", f"{pos.row},{pos.col}", self.name)
        except Exception as e:  # noqa: BLE001
            return OpResult(False, "SELL", str(e), self.name)

    def get_team(self) -> Optional[dict]:
        try:
            r = self._rpc("getteam")
            d = json.loads(r) if isinstance(r, str) else r
            return d if d and d.get("in_team") else None
        except Exception:  # noqa: BLE001
            return None

    def send_request(self, name: str, args: dict) -> OpResult:
        try:
            r = self._rpc("sendreq", name, json.dumps(args or {}))
            ok = bool(r.get("ok")) if isinstance(r, dict) else False
            return OpResult(ok, name, r.get("err", "sent") if isinstance(r, dict) else str(r),
                            self.name)
        except Exception as e:  # noqa: BLE001
            return OpResult(False, name, str(e), self.name)

    def tap_ui(self, name: str) -> OpResult:
        # meta-phase UI taps still go through adb calibration
        return self._adb.tap_ui(name)

    def tap_raw(self, x: int, y: int) -> OpResult:
        return self._adb.tap_raw(x, y)

    def alive(self) -> bool:
        return self._session is not None and not self._session.is_detached
