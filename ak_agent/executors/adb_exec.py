"""ADB backend — screencap + input emulation via calibrated grid."""
from __future__ import annotations

import subprocess
import time
from typing import Optional

from ..grid import BoardCalib
from ..model import (BattleState, Direction, GridPos, OpResult, OpType)


class AdbExecutor:
    name = "adb"

    def __init__(self, serial: str = "", calib: Optional[BoardCalib] = None):
        self._serial = serial
        self._calib = calib
        if calib is None:
            # still usable for raw taps / screenshots
            self._warn_calib = True

    # -- adb plumbing ----------------------------------------------------
    def _adb(self, *args: str, timeout: int = 15) -> str:
        cmd = ["adb"]
        if self._serial:
            cmd += ["-s", self._serial]
        cmd += list(args)
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.stdout.decode(errors="replace")

    def _adb_bin(self, *args: str, timeout: int = 30) -> bytes:
        cmd = ["adb"]
        if self._serial:
            cmd += ["-s", self._serial]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, timeout=timeout).stdout

    # -- executor surface -------------------------------------------------
    def get_state(self) -> Optional[BattleState]:
        # No memory access without root — state comes from CV pipeline
        # (screenshot + template/OCR). Return None; tools fall back to
        # screenshot path.
        return None

    def screenshot(self, out_path: str) -> str:
        data = self._adb_bin("exec-out", "screencap", "-p")
        with open(out_path, "wb") as f:
            f.write(data)
        return out_path

    def do_op(self, op: OpType, start: GridPos, end: GridPos,
              direction: Direction = Direction.INVALID) -> OpResult:
        if self._calib is None:
            return OpResult(False, op.name, "no board calibration", self.name)
        c = self._calib
        try:
            if op in (OpType.MOVE_ONLY, OpType.EQUIP_ITEM,
                      OpType.REPLACE_EQUIP, OpType.USE_MAGIC):
                sx, sy = self._pos_px(start)
                ex, ey = self._pos_px(end)
                self._drag(sx, sy, ex, ey)
                # deploy direction pick: small extra swipe for facing
                if direction != Direction.INVALID:
                    dx, dy = {Direction.UP: (0, -60), Direction.RIGHT: (60, 0),
                              Direction.DOWN: (0, 60), Direction.LEFT: (-60, 0)
                              }[direction]
                    self._drag(ex, ey, ex + dx, ey + dy, ms=120)
            elif op == OpType.WITHDRAW:
                x, y = self._pos_px(start)
                # retreat: long-press then confirm — refine vs live UX
                self._adb("shell", "input", "swipe",
                          str(x), str(y), str(x), str(y), "800")
                ok = c.ui_px("withdraw_confirm")
                if ok:
                    self._tap(*ok)
            else:
                return OpResult(False, op.name, "unknown op", self.name)
            return OpResult(True, op.name,
                            f"{start.row},{start.col}->{end.row},{end.col}",
                            self.name)
        except Exception as e:  # noqa: BLE001
            return OpResult(False, op.name, str(e), self.name)

    def sell_or_destroy(self, pos: GridPos) -> OpResult:
        if self._calib is None:
            return OpResult(False, "SELL", "no board calibration", self.name)
        x, y = self._pos_px(pos)
        sell = self._calib.ui_px("sell_zone")
        if sell is None:
            return OpResult(False, "SELL", "no sell_zone calibration", self.name)
        self._drag(x, y, *sell)
        return OpResult(True, "SELL", f"{pos.row},{pos.col}", self.name)

    def tap_ui(self, name: str) -> OpResult:
        if self._calib is None:
            return OpResult(False, "UI", "no calibration", self.name)
        px = self._calib.ui_px(name)
        if px is None:
            return OpResult(False, "UI", f"ui '{name}' not calibrated", self.name)
        self._tap(*px)
        return OpResult(True, "UI", f"{name}@{px[0]},{px[1]}", self.name)

    def tap_raw(self, x: int, y: int) -> OpResult:
        self._tap(x, y)
        return OpResult(True, "TAP", f"{x},{y}", self.name)

    # -- input primitives ---------------------------------------------------
    def _pos_px(self, pos: GridPos) -> tuple[int, int]:
        c = self._calib
        assert c is not None
        # hand positions: row == board_rows convention → hand_slots[col]
        if pos.row >= c.board_rows and 0 <= pos.col < len(c.hand_slots):
            px = c.hand_slot_px(pos.col)
            if px:
                return px
        return c.grid_to_px(pos)

    def _tap(self, x: int, y: int) -> None:
        self._adb("shell", "input", "tap", str(x), str(y))
        time.sleep(0.15)

    def _drag(self, x1: int, y1: int, x2: int, y2: int, ms: int = 350) -> None:
        self._adb("shell", "input", "swipe",
                  str(x1), str(y1), str(x2), str(y2), str(ms))
        time.sleep(0.25)
