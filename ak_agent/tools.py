"""MCP tool surface exposed to the LLM.

Every tool maps to either an AutoChessOperationSnapshot op (frida path:
ConstructOperation+DoOperation; adb path: calibrated drag) or a state/UI
read. Tool names are stable across backends — the executor abstracts.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from .executors import OpExecutor
from .model import Direction, GridPos, OpType

_SHOT_DIR = Path("/tmp/ak_shots")
_SHOT_DIR.mkdir(exist_ok=True)


def _ok(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}]}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {msg}"}], "is_error": True}


def build_server(executor: OpExecutor):
    ex = executor

    @tool("get_game_state",
          "Get the full structured SSS battle state: phase, round, your hp/shop_coin/shop_level, "
          "all chess on board and in hand (chess_id, row/col, direction, type). "
          "Returns null-ish when not inside a battle scene — then use screenshot instead.",
          {})
    async def get_game_state(_a):
        st = ex.get_state()
        if st is None:
            return _ok({"in_battle": False,
                        "hint": "no memory state — take a screenshot or you are outside the battle scene"})
        return _ok(st.to_json())

    @tool("get_hand",
          "List chess pieces currently in your hand (deployable). "
          "Each has inst_id, chess_id, item_type and its hand position (row is the hand row, col is the slot index).",
          {})
    async def get_hand(_a):
        st = ex.get_state()
        if st is None:
            return _err("state unavailable (adb backend or not in battle)")
        return _ok([c.__dict__ | {"pos": vars(c.pos)} for c in st.hand])

    @tool("deploy_chess",
          "Deploy a chess piece from hand onto a board tile. "
          "hand_row/hand_col: the piece's position in hand (from get_hand). "
          "to_row/to_col: target board tile. direction: facing UP/RIGHT/DOWN/LEFT (default RIGHT).",
          {"hand_row": int, "hand_col": int, "to_row": int, "to_col": int,
           "direction": str})
    async def deploy_chess(a):
        d = Direction[a.get("direction", "RIGHT").upper()]
        r = ex.do_op(OpType.MOVE_ONLY,
                     GridPos(a["hand_row"], a["hand_col"]),
                     GridPos(a["to_row"], a["to_col"]), d)
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("move_chess",
          "Move an already-deployed chess to another tile (and optionally change facing).",
          {"from_row": int, "from_col": int, "to_row": int, "to_col": int,
           "direction": str})
    async def move_chess(a):
        d = Direction[a.get("direction", "INVALID").upper()]
        r = ex.do_op(OpType.MOVE_ONLY, GridPos(a["from_row"], a["from_col"]),
                     GridPos(a["to_row"], a["to_col"]), d)
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("withdraw_chess",
          "Withdraw/retreat a deployed chess back to hand (WITHDRAW op).",
          {"row": int, "col": int})
    async def withdraw_chess(a):
        r = ex.do_op(OpType.WITHDRAW, GridPos(a["row"], a["col"]),
                     GridPos(a["row"], a["col"]), Direction.INVALID)
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("equip_item",
          "Equip an equipment item from hand onto a deployed chess (EQUIP_ITEM). "
          "item position = its hand slot; target = the chess tile.",
          {"item_row": int, "item_col": int, "target_row": int, "target_col": int})
    async def equip_item(a):
        r = ex.do_op(OpType.EQUIP_ITEM, GridPos(a["item_row"], a["item_col"]),
                     GridPos(a["target_row"], a["target_col"]), Direction.INVALID)
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("replace_equip",
          "Replace existing equipment on a chess (REPLACE_EQUIP).",
          {"item_row": int, "item_col": int, "target_row": int, "target_col": int})
    async def replace_equip(a):
        r = ex.do_op(OpType.REPLACE_EQUIP, GridPos(a["item_row"], a["item_col"]),
                     GridPos(a["target_row"], a["target_col"]), Direction.INVALID)
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("cast_magic",
          "Cast a magic/skill item from hand onto a target tile or chess (USE_MAGIC).",
          {"magic_row": int, "magic_col": int, "target_row": int, "target_col": int})
    async def cast_magic(a):
        r = ex.do_op(OpType.USE_MAGIC, GridPos(a["magic_row"], a["magic_col"]),
                     GridPos(a["target_row"], a["target_col"]), Direction.INVALID)
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("sell_or_destroy",
          "Sell/destroy a chess at a position for refund (DoSellOrDestroy).",
          {"row": int, "col": int})
    async def sell_or_destroy(a):
        r = ex.sell_or_destroy(GridPos(a["row"], a["col"]))
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("shop_buy",
          "Buy a chess from the shop by slot_id (from get_game_state shop.goods). "
          "is_special: buy the special/dragged goods row. Frida backend only; on adb use tap_ui('buy_slot_N').",
          {"slot_id": int, "is_special": bool})
    async def shop_buy(a):
        r = ex.send_request("shop_buy", {"slotId": a["slot_id"],
                                       "isSpecial": bool(a.get("is_special", False))})
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("shop_refresh",
          "Reroll the shop goods (costs refresh_price). Frida backend only.",
          {})
    async def shop_refresh(_a):
        r = ex.send_request("shop_refresh", {})
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("shop_freeze",
          "Freeze/unfreeze the shop so goods persist next round. Frida backend only.",
          {"frozen": bool})
    async def shop_freeze(a):
        r = ex.send_request("shop_froze", {"isFrozen": bool(a["frozen"])})
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("shop_upgrade",
          "Upgrade shop level (costs upgrade_price). Frida backend only.",
          {})
    async def shop_upgrade(_a):
        r = ex.send_request("shop_upgrade", {})
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("confirm_ready",
          "Signal READY (or cancel) for the preparation phase countdown — the 'ready' button. "
          "Frida backend only; on adb use tap_ui('ready').",
          {"ready": bool})
    async def confirm_ready(a):
        r = ex.send_request("prep_ready", {"readyType": 1 if a.get("ready", True) else 0})
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("pick_slot",
          "Pick slot_id in a choose-one-of-N prompt (special preparation / self-choice rewards).",
          {"slot_id": int, "kind": str})
    async def pick_slot(a):
        name = "self_pick" if a.get("kind", "self").lower().startswith("self") else "sp_pick"
        r = ex.send_request(name, {"slotId": a["slot_id"]})
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("choose_strategy",
          "Choose a strategy in the timed '选择策略' phase. strategy = strategy id string; "
          "set skip=true to give up choosing. Frida backend only.",
          {"strategy": str, "skip": bool})
    async def choose_strategy(a):
        r = ex.send_request("choose_strategy", {"skip": bool(a.get("skip", False)),
                                                "strategy": a.get("strategy", "")})
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("send_game_request",
          "Escape hatch — send any other AutoChessServiceRequest by name. "
          "team_ready{ready}, start_match{}, enemy_assign_ready{ready}, "
          "diy_ready{identifiers:[int]}, diy_revoke{}, change_position{deploy_count, units:[{inst_id,row,col,chess_type,direction}]}, "
          "use_magic{inst_id, deploy_count, units:[...]}, observe{}. Frida backend only.",
          {"name": str, "args": dict})
    async def send_game_request(a):
        r = ex.send_request(a["name"], a.get("args") or {})
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("tap_ui",
          "Tap a named UI element. Calibrated names: ready/confirm, speed1, speed2, pause, "
          "shop_refresh, buy_slot_0..4, buff_0..2, withdraw_confirm, sell_zone. "
          "Use for prepare-phase UI (shop, buff picks) that isn't a board op.",
          {"name": str})
    async def tap_ui(a):
        r = ex.tap_ui(a["name"])
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("tap_raw",
          "Raw pixel tap at screen x,y — escape hatch for uncalibrated UI.",
          {"x": int, "y": int})
    async def tap_raw(a):
        r = ex.tap_raw(a["x"], a["y"])
        return _ok(r.__dict__) if r.ok else _err(r.detail)

    @tool("screenshot",
          "Take a screenshot of the device screen. Returns the file path — "
          "read it to see the current UI when structured state is unavailable.",
          {})
    async def screenshot(_a):
        p = str(_SHOT_DIR / f"shot_{int(time.time())}.png")
        ex.screenshot(p)
        return _ok({"path": p})

    return create_sdk_mcp_server(
        "arknights",
        tools=[get_game_state, get_hand, deploy_chess, move_chess,
               withdraw_chess, equip_item, replace_equip, cast_magic,
               sell_or_destroy, shop_buy, shop_refresh, shop_freeze,
               shop_upgrade, confirm_ready, pick_slot, choose_strategy,
               send_game_request, tap_ui, tap_raw, screenshot],
    )


TOOL_NAMES = [f"mcp__arknights__{n}" for n in (
    "get_game_state", "get_hand", "deploy_chess", "move_chess",
    "withdraw_chess", "equip_item", "replace_equip", "cast_magic",
    "sell_or_destroy", "shop_buy", "shop_refresh", "shop_freeze",
    "shop_upgrade", "confirm_ready", "pick_slot", "choose_strategy",
    "send_game_request", "tap_ui", "tap_raw", "screenshot")]
