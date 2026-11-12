"""Normalized game-state model for the SSS (AutoChess) mode.

Mirrors the game's managed classes:
  Torappu.Battle.AutoChess.{AutoChessGameManager, AutoChessDataCenter,
  ChessMapInfo, ChessInst, AutoChessPlayerDataModel.ScenePlayerData}
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Optional


class OpType(enum.IntEnum):
    """AutoChessOperationType — the complete battle-op vocabulary."""
    MOVE_ONLY = 0
    WITHDRAW = 1
    EQUIP_ITEM = 2
    REPLACE_EQUIP = 3
    USE_MAGIC = 4


class Direction(enum.IntEnum):
    """SharedConsts.Direction"""
    UP = 0
    RIGHT = 1
    DOWN = 2
    LEFT = 3
    INVALID = 4


class ItemType(enum.IntEnum):
    """AutoChessItemType"""
    CHAR = 0
    EQUIP = 1
    MAGIC = 2
    TOKEN = 3


class GameState(enum.IntEnum):
    """AutoChessGameStateType"""
    NONE = 0
    LOADING = 1
    SP_PREPARE = 2
    PREPARE = 3
    BATTLE = 4
    HELP_BATTLE = 5
    BOSS_BATTLE = 6
    SETTLE = 7
    END = 8
    PAUSE = 9
    BATTLE_WAITING = 100
    BOSS_PREPARE_WAITING = 101
    PREPARE_RESTART = 102
    SP_PREPARE_RESTART = 103
    BOSS_BATTLE_RESTART = 104


@dataclass
class GridPos:
    """GridPosition { row:int, col:int }"""
    row: int
    col: int

    def code(self) -> int:
        """posCode — verified via GridPosition.Encode disasm: (row << 16) + col."""
        return (self.row << 16) + self.col

    @classmethod
    def decode(cls, code: int) -> "GridPos":
        """GridPosition.Decode: row = code >> 16, col = code & 0xffff."""
        return cls(row=code >> 16, col=code & 0xFFFF)


@dataclass
class ChessPiece:
    """ChessInst — one chess/token/equip/magic on board or in hand."""
    inst_id: int
    chess_id: str            # e.g. "char_xxx" / token id — maps to gamedata
    pos: GridPos
    direction: int
    item_type: int           # ItemType
    player_index: int
    side: int
    in_hand: bool
    in_battle: bool
    is_golden: bool = False
    # filled lazily: hp/atk from battleCharacterData when needed


@dataclass
class PlayerState:
    """ScenePlayerData"""
    index: int
    hp: int
    shop_level: int
    shop_coin: int
    max_deploy: int
    streak: int
    game_state: int
    garrison_bonds: dict = field(default_factory=dict)
    active_bonds: list = field(default_factory=list)


@dataclass
class BattleState:
    """Snapshot handed to the LLM."""
    phase: str                       # GameState name
    round: int = 0
    stage_id: str = ""
    me: Optional[PlayerState] = None
    board: list = field(default_factory=list)   # ChessPiece on board
    hand: list = field(default_factory=list)    # ChessPiece in hand
    enemy_preview: list = field(default_factory=list)
    deploy_count: int = 0
    max_deploy: int = 0
    cost: int = 0
    shop: dict = field(default_factory=dict)    # {level, coin, upgrade_price, refresh_price, goods:[{chess_id, slot_id, price, frozen}]}

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class OpResult:
    ok: bool
    op: str
    detail: str = ""
    backend: str = ""
    dispatched: bool = False
    verified: bool = False
    before_snapshot_id: str = ""
    after_snapshot_id: str = ""
