"""OpExecutor — backend-agnostic executor interface.

Two backends:
  FridaExecutor — invokes AutoChessPlayerOpManager directly (deterministic,
                  needs rooted device/emulator + frida-server).
  AdbExecutor   — input swipe emulation via calibrated grid (works on any
                  device incl. the non-rooted phone).
"""
from __future__ import annotations

import abc
from typing import Optional

from ..model import BattleState, Direction, GridPos, OpResult, OpType


class OpExecutor(abc.ABC):
    name = "base"

    # ---- state ----
    @abc.abstractmethod
    def get_state(self) -> Optional[BattleState]:
        """Structured battle state, or None if unreadable (e.g. not in battle)."""

    @abc.abstractmethod
    def screenshot(self, out_path: str) -> str:
        """PNG screenshot → out_path. Returns path."""

    # ---- battle ops (AutoChessOperationSnapshot semantics) ----
    @abc.abstractmethod
    def do_op(self, op: OpType, start: GridPos, end: GridPos,
              direction: Direction = Direction.INVALID) -> OpResult:
        """ConstructOperation + DoOperation equivalent."""

    @abc.abstractmethod
    def sell_or_destroy(self, pos: GridPos) -> OpResult:
        """OpManager.DoSellOrDestroy — refund a hand/board piece."""

    # ---- meta ops (shop/prepare-phase, UI-driven) ----
    @abc.abstractmethod
    def tap_ui(self, name: str) -> OpResult:
        """Tap a calibrated UI element by name (ready, speed2, shop_refresh...)."""

    @abc.abstractmethod
    def tap_raw(self, x: int, y: int) -> OpResult:
        """Raw pixel tap — escape hatch for uncalibrated UI."""

    def get_team(self) -> Optional[dict]:
        """Frida only: team-phase context (strategy picks, players, deadlines)."""
        return None

    # ---- server protocol ops (AutoChessService.SendRequest) ----
    def send_request(self, name: str, args: dict) -> OpResult:
        """Frida only: send an AutoChessServiceRequest packet.
        Names: shop_buy/shop_refresh/shop_froze/shop_upgrade, prep_ready,
        sp_pick/self_pick, destroy_chess, use_equip, change_position,
        use_magic, choose_strategy, team_ready, start_match,
        enemy_assign_ready, diy_ready, diy_revoke, observe."""
        return OpResult(False, name, "backend cannot send protocol requests — use tap_ui", self.name)

    def alive(self) -> bool:
        return True
