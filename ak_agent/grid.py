"""GridPosition <-> screen-pixel mapping for the ADB backend.

SSS board is an isometric-ish grid. We calibrate with 4 corner tile
screen centers and bilinear-interpolate. Hand slots get their own
calibration row. Calibration JSON lives in agent/calib/.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .model import GridPos


@dataclass
class BoardCalib:
    """Perspective-ish calibration for one resolution."""
    width: int
    height: int
    board_rows: int
    board_cols: int
    board_row_min: int
    board_col_min: int
    # screen centers of corner tiles: TL, TR, BL, BR  (x,y)
    corners: list          # [[x,y]x4]
    board_pick_offset: list
    hand_slots: list       # [[x,y]...] screen center of each hand slot
    hand_rows: dict        # {"row": {"origin": [x,y], "step": [dx,dy]}}
    ui: dict               # named buttons: {"ready": [x,y], "speed2": [...], ...}

    @staticmethod
    def load(path: Path) -> "BoardCalib":
        d = json.loads(Path(path).read_text())
        return BoardCalib(
            width=d["width"], height=d["height"],
            board_rows=d["board_rows"], board_cols=d["board_cols"],
            board_row_min=d.get("board_row_min", 0),
            board_col_min=d.get("board_col_min", 0),
            corners=d["corners"], hand_slots=d.get("hand_slots", []),
            board_pick_offset=d.get("board_pick_offset", [0, 0]),
            hand_rows=d.get("hand_rows", {}),
            ui=d.get("ui", {}),
        )

    def grid_to_px(self, pos: GridPos) -> tuple[int, int]:
        """Bilinear interp over the 4 corner tile centers."""
        tl, tr, bl, br = self.corners
        if any(point == [0, 0] for point in self.corners):
            raise ValueError("board grid is not calibrated")
        local_row = pos.row - self.board_row_min
        local_col = pos.col - self.board_col_min
        if not (0 <= local_row < self.board_rows and 0 <= local_col < self.board_cols):
            raise ValueError(f"grid position outside calibrated board: {pos.row},{pos.col}")
        r = local_row / max(self.board_rows - 1, 1)
        c = local_col / max(self.board_cols - 1, 1)
        x = (tl[0]*(1-c) + tr[0]*c) * (1-r) + (bl[0]*(1-c) + br[0]*c) * r
        y = (tl[1]*(1-c) + tr[1]*c) * (1-r) + (bl[1]*(1-c) + br[1]*c) * r
        return int(x), int(y)

    def hand_slot_px(self, idx: int) -> Optional[tuple[int, int]]:
        if 0 <= idx < len(self.hand_slots):
            x, y = self.hand_slots[idx]
            return int(x), int(y)
        return None

    def hand_grid_px(self, pos: GridPos) -> Optional[tuple[int, int]]:
        row = self.hand_rows.get(str(pos.row))
        if not row:
            return None
        origin, step = row["origin"], row["step"]
        return (int(origin[0] + step[0] * pos.col),
                int(origin[1] + step[1] * pos.col))

    def grid_pick_px(self, pos: GridPos) -> tuple[int, int]:
        x, y = self.grid_to_px(pos)
        return x + int(self.board_pick_offset[0]), y + int(self.board_pick_offset[1])

    def ui_px(self, name: str) -> Optional[tuple[int, int]]:
        v = self.ui.get(name)
        return (int(v[0]), int(v[1])) if v and v != [0, 0] else None
