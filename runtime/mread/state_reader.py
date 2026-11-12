#!/usr/bin/env python3
"""Read a live AutoChess state snapshot through the passive mread service.

This reader only observes the running process. It never calls game methods,
writes process memory, sends game protocol messages, or fabricates defaults.
Fields that cannot be validated are omitted and listed in ``unavailable``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
from typing import Any

from walk import Mem, scan_ptrs


PACKAGE = "com.hypergryph.arknights.bilibili"
BUILD_ID = "4456cd5a68dfe1277f1dff86221db41a2d6da116"
TYPEINFO_BATTLE_CONTROLLER = 0xCF2D378
TYPEINFO_AUTO_CHESS_MANAGER = 0xCF2C4F8
TYPEINFO_AUTO_CHESS_SERVICE = 0xCF2C7F8
TYPEINFO_SETTLE_ROOT = 0xCF2C968
PHASES = {
    0: "NONE", 1: "LOADING", 2: "SP_PREPARE", 3: "PREPARE", 4: "BATTLE",
    5: "HELP_BATTLE", 6: "BOSS_BATTLE", 7: "SETTLE", 8: "END", 9: "PAUSE",
    100: "BATTLE_WAITING", 101: "BOSS_PREPARE_WAITING", 102: "PREPARE_RESTART",
    103: "SP_PREPARE_RESTART", 104: "BOSS_BATTLE_RESTART",
}
TEAM_STATES = {
    0: "NONE", 1: "TEAM_BUILDING", 2: "TEAM_MATCHING", 3: "TEAM_MATCH_SUC",
    4: "ENTER", 5: "INFO_SHOW", 6: "STRATEGY_CHOOSE",
    7: "ENTER_BATTLE_COUNT_DOWN", 8: "IN_BATTLE", 9: "END", 10: "DIY_CHOOSE",
}


class StateReadError(RuntimeError):
    pass


class LiveMemory:
    def __init__(self, pid: int, serial: str, adb: str, port: int):
        self.pid = pid
        self.mem = Mem(port=port, serial=serial, adb=adb)
        self.maps = self.mem.load_maps(pid)
        if not self.maps:
            raise StateReadError(f"no maps returned for pid {pid}")
        self.mem.connect()
        self.module_base = self._module_base("libil2cpp.so")

    def _module_base(self, basename: str) -> int:
        candidates = [m for m in self.maps if m["path"].endswith(basename)
                      and m.get("offset") == 0 and "x" in m["perm"]]
        if len(candidates) != 1:
            raise StateReadError(f"expected one executable {basename} load base, got {len(candidates)}")
        return candidates[0]["lo"]

    @staticmethod
    def untag(value: int) -> int:
        return value & 0x00FFFFFFFFFFFFFF

    def read(self, address: int, size: int) -> bytes:
        if not address:
            raise StateReadError("attempted to read a null pointer")
        data = self.mem.read(address, size)
        if data is None or len(data) != size:
            got = 0 if data is None else len(data)
            raise StateReadError(f"short read at {address:#x}: expected {size}, got {got}")
        return data

    def u8(self, address: int) -> int:
        return self.read(address, 1)[0]

    def i32(self, address: int) -> int:
        return struct.unpack("<i", self.read(address, 4))[0]

    def u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    def i64(self, address: int) -> int:
        return struct.unpack("<q", self.read(address, 8))[0]

    def u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]

    def ptr(self, address: int, *, nullable: bool = True) -> int:
        value = self.untag(self.u64(address))
        if not value and not nullable:
            raise StateReadError(f"null pointer at {address:#x}")
        return value

    def cstr(self, address: int, maxlen: int = 128) -> str:
        data = self.read(address, maxlen)
        end = data.find(b"\0")
        if end < 0:
            raise StateReadError(f"unterminated c-string at {address:#x}")
        try:
            return data[:end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StateReadError(f"invalid utf-8 at {address:#x}") from exc

    def string(self, obj: int | None) -> str | None:
        if not obj:
            return None
        length = self.i32(obj + 0x10)
        if not 0 <= length <= 8192:
            raise StateReadError(f"invalid string length {length} at {obj:#x}")
        if length == 0:
            return ""
        try:
            return self.read(obj + 0x14, length * 2).decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise StateReadError(f"invalid utf-16 at {obj:#x}") from exc

    def string_field(self, obj: int, offset: int) -> str | None:
        return self.string(self.ptr(obj + offset))

    def class_name_from_class(self, klass: int) -> str:
        return self.cstr(self.ptr(klass + 0x10, nullable=False))

    def class_name(self, obj: int) -> str:
        return self.class_name_from_class(self.ptr(obj, nullable=False))

    def require_class(self, obj: int, expected: str) -> None:
        actual = self.class_name(obj)
        if actual != expected:
            raise StateReadError(f"expected {expected} at {obj:#x}, got {actual}")

    def typeinfo(self, rva: int, expected: str) -> int:
        klass = self.ptr(self.module_base + rva, nullable=False)
        actual = self.class_name_from_class(klass)
        if actual != expected:
            raise StateReadError(f"TypeInfo {rva:#x}: expected {expected}, got {actual}")
        return klass

    def list_items(self, obj: int | None, *, limit: int = 4096,
                   required: bool = False) -> list[int]:
        if not obj:
            if required:
                raise StateReadError("required List is null")
            return []
        items = self.ptr(obj + 0x10, nullable=False)
        size = self.i32(obj + 0x18)
        capacity = self.u64(items + 0x18)
        if not 0 <= size <= min(capacity, limit):
            raise StateReadError(f"invalid List size/capacity {size}/{capacity} at {obj:#x}")
        return [self.ptr(items + 0x20 + index * 8) for index in range(size)]

    def list_ints(self, obj: int | None, *, limit: int = 4096) -> list[int]:
        if not obj:
            return []
        items = self.ptr(obj + 0x10, nullable=False)
        size = self.i32(obj + 0x18)
        capacity = self.u64(items + 0x18)
        if not 0 <= size <= min(capacity, limit):
            raise StateReadError(f"invalid List<int> size/capacity {size}/{capacity}")
        return [self.i32(items + 0x20 + index * 4) for index in range(size)]

    def listdict_int_object(self, obj: int | None, *, limit: int = 4096,
                            required: bool = False) -> list[tuple[int, int]]:
        if not obj:
            if required:
                raise StateReadError("required ListDict<int,object> is null")
            return []
        items = self.ptr(obj + 0x10, nullable=False)
        size = self.i32(obj + 0x18)
        capacity = self.u64(items + 0x18)
        if not 0 <= size <= min(capacity, limit):
            raise StateReadError(f"invalid ListDict size/capacity {size}/{capacity} at {obj:#x}")
        return [(self.i32(items + 0x20 + index * 0x10),
                 self.ptr(items + 0x28 + index * 0x10)) for index in range(size)]

    def listdict_object_object(self, obj: int | None, *, limit: int = 4096) -> list[tuple[int, int]]:
        if not obj:
            return []
        items = self.ptr(obj + 0x10, nullable=False)
        size = self.i32(obj + 0x18)
        capacity = self.u64(items + 0x18)
        if not 0 <= size <= min(capacity, limit):
            raise StateReadError(f"invalid ListDict size/capacity {size}/{capacity} at {obj:#x}")
        return [(self.ptr(items + 0x20 + index * 0x10),
                 self.ptr(items + 0x28 + index * 0x10)) for index in range(size)]

    def hashset_grid_positions(self, obj: int | None, *, limit: int = 4096,
                               required: bool = False) -> set[tuple[int, int]]:
        if not obj:
            if required:
                raise StateReadError("required grid HashSet is null")
            return set()
        slots = self.ptr(obj + 0x18, nullable=False)
        count = self.i32(obj + 0x20)
        last_index = self.i32(obj + 0x24)
        capacity = self.u64(slots + 0x18)
        if not 0 <= count <= last_index <= min(capacity, limit):
            raise StateReadError(f"invalid HashSet count/index/capacity {count}/{last_index}/{capacity}")
        values = set()
        for index in range(last_index):
            slot = slots + 0x20 + index * 0x10
            if self.i32(slot) >= 0:
                values.add((self.i32(slot + 8), self.i32(slot + 12)))
        if len(values) != count:
            raise StateReadError(f"HashSet live count mismatch: header={count}, parsed={len(values)}")
        return values


class AutoChessReader:
    def __init__(self, memory: LiveMemory):
        self.m = memory
        self.unavailable: list[str] = []

    def _optional(self, label: str, reader, fallback=None):
        try:
            return reader()
        except StateReadError as exc:
            self.unavailable.append(f"{label}: {exc}")
            return fallback

    def _manager(self) -> tuple[int, int]:
        m = self.m
        battle_class = m.typeinfo(TYPEINFO_BATTLE_CONTROLLER, "BattleController")
        manager_class = m.typeinfo(TYPEINFO_AUTO_CHESS_MANAGER, "AutoChessGameManager")
        singleton_class = m.ptr(battle_class + 0x58, nullable=False)
        if m.class_name_from_class(singleton_class) != "SingletonMonoBehaviour`1":
            raise StateReadError("BattleController parent is not SingletonMonoBehaviour`1")
        static_fields = m.ptr(singleton_class + 0xB8, nullable=False)
        battle = m.ptr(static_fields, nullable=False)
        m.require_class(battle, "BattleController")
        host = m.ptr(battle + 0x2E8, nullable=False)
        m.require_class(host, "SingletonHost")
        dictionary = m.ptr(host + 0x10, nullable=False)
        entries = m.ptr(dictionary + 0x18, nullable=False)
        count = m.i32(dictionary + 0x20)
        capacity = m.u64(entries + 0x18)
        if not 0 <= count <= min(capacity, 1024):
            raise StateReadError(f"invalid singleton dictionary count/capacity {count}/{capacity}")
        matches = []
        for index in range(count):
            entry = entries + 0x20 + index * 0x18
            if m.i32(entry) < 0:
                continue
            value = m.ptr(entry + 0x10)
            if value and m.ptr(value) == manager_class:
                matches.append(value)
        if len(matches) != 1:
            raise StateReadError(f"expected one live AutoChessGameManager, got {len(matches)}")
        manager = matches[0]
        data_center = m.ptr(manager + 0x40, nullable=False)
        m.require_class(data_center, "AutoChessDataCenter")
        return manager, data_center

    def _phase(self, manager: int) -> dict[str, Any]:
        m = self.m
        game_status = m.ptr(manager + 0x38, nullable=False)
        m.require_class(game_status, "AutoChessGameStatus")
        model = m.ptr(game_status + 0x10, nullable=False)
        m.require_class(model, "AutoChessGameStateModel")
        machine = m.ptr(model + 0x18, nullable=False)
        m.require_class(machine, "AutoChessGameStateMachine")
        current_id = m.i32(machine + 0x5C)
        current = m.ptr(machine + 0x60)
        substate = None
        if current:
            m.require_class(current, "AutoChessGameState")
            if m.i32(current + 0x14) != current_id:
                raise StateReadError("state machine id and current state object disagree")
            substate = m.i32(current + 0x18)
        server_state = m.i32(manager + 0x20)
        ui_model = m.ptr(game_status + 0x18)
        self_ready = None
        shop_opened = None
        if ui_model:
            m.require_class(ui_model, "AutoChessUIStateModel")
            shop_opened = bool(m.u8(ui_model + 0x14))
            self_ready = bool(m.u8(ui_model + 0x38))
        return {"phase": PHASES.get(current_id, f"UNKNOWN_{current_id}"),
                "phase_id": current_id, "server_state_id": server_state,
                "server_state": PHASES.get(server_state, f"UNKNOWN_{server_state}"),
                "substate_id": substate, "shop_opened": shop_opened,
                "self_ready": self_ready}

    def _shop(self, data_center: int) -> dict[str, Any]:
        m = self.m
        obj = m.ptr(data_center + 0x100, nullable=False)
        m.require_class(obj, "AutoChessShopDataModel")
        goods = []
        for good in m.list_items(m.ptr(obj + 0x30)):
            if not good:
                continue
            m.require_class(good, "ChessGoods")
            goods.append({"chess_id": m.string_field(good, 0x10),
                          "slot_id": m.i32(good + 0x18),
                          "price": m.i32(good + 0x1C),
                          "frozen": bool(m.u8(good + 0x20))})
        return {"level": m.i32(obj + 0x14), "coin": m.i32(obj + 0x18),
                "upgrade_price": m.i32(obj + 0x1C), "refresh_price": m.i32(obj + 0x20),
                "goods_type": m.i32(obj + 0x24), "free_refresh_count": m.i32(obj + 0x28),
                "special_refresh": bool(m.u8(obj + 0x2C)), "refresh_sequence": m.i32(obj + 0x38),
                "goods": sorted(goods, key=lambda item: item["slot_id"])}

    def _bond(self, obj: int) -> dict[str, Any]:
        m = self.m
        m.require_class(obj, "GarrisonBond")
        return {"bond_id": m.string_field(obj, 0x10), "character_count": m.i32(obj + 0x18),
                "active": bool(m.u8(obj + 0x1C)), "persistent_stacks": m.i32(obj + 0x20),
                "temporary_stacks": m.i32(obj + 0x24), "stacks": m.i32(obj + 0x28)}

    def _player(self, obj: int) -> dict[str, Any]:
        m = self.m
        m.require_class(obj, "ScenePlayerData")
        bonds = []
        for _, bond in m.listdict_object_object(m.ptr(obj + 0x98)):
            if bond:
                parsed = self._bond(bond)
                if parsed["character_count"] or parsed["stacks"] or parsed["active"]:
                    bonds.append(parsed)
        active_bonds = []
        for bond in m.list_items(m.ptr(obj + 0xA0)):
            if bond:
                active_bonds.append(m.string_field(bond, 0x10))
        battle_data = m.ptr(obj + 0x80)
        inventory = {"characters": [], "equipment": [], "magic": []}
        if battle_data:
            m.require_class(battle_data, "PlayerBattleData")
            for char in m.list_items(m.ptr(battle_data + 0x10)):
                if char:
                    m.require_class(char, "CharChess")
                    inventory["characters"].append({"inst_id": m.i32(char + 0x10),
                        "chess_id": m.string_field(char, 0x20),
                        "equipment_inst_ids": m.list_ints(m.ptr(char + 0x18)),
                        "bond_max_add": m.i32(char + 0x28), "sold_price": m.i32(char + 0x2C)})
            for key, offset in (("equipment", 0x18), ("magic", 0x20)):
                for item in m.list_items(m.ptr(battle_data + offset)):
                    if item:
                        m.require_class(item, "EquipChess")
                        inventory[key].append({"inst_id": m.i32(item + 0x10),
                                               "chess_id": m.string_field(item, 0x18)})
        return {"index": m.i32(obj + 0x10), "uid": m.string_field(obj, 0x18),
                "band_id": m.string_field(obj, 0x20), "nickname_alias": m.string_field(obj, 0x28),
                "hp": m.i32(obj + 0x50), "boss_damage_ratio": struct.unpack("<f", m.read(obj + 0x54, 4))[0],
                "shop_level": m.i32(obj + 0x58), "shop_coin": m.i32(obj + 0x5C),
                "finished": bool(m.u8(obj + 0x60)), "max_deploy": m.i32(obj + 0x64),
                "streak": m.i32(obj + 0x68), "game_state": m.i32(obj + 0x74),
                "connect_state": m.i32(obj + 0x78), "garrison_bonds": bonds,
                "active_bonds": [value for value in active_bonds if value],
                "inventory": inventory}

    def _players(self, data_center: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        m = self.m
        model = m.ptr(data_center + 0xF8, nullable=False)
        m.require_class(model, "AutoChessPlayerDataModel")
        players = [self._player(value) for _, value in
                   m.listdict_int_object(m.ptr(model + 0x18)) if value]
        self_index = m.i32(data_center + 0x38)
        mine = next((player for player in players if player["index"] == self_index), None)
        if mine is None:
            raise StateReadError(f"self player index {self_index} absent from player model")
        return players, mine

    def _piece(self, obj: int, *, in_hand: bool, in_battle: bool) -> dict[str, Any]:
        m = self.m
        m.require_class(obj, "ChessInst")
        squad = m.ptr(obj + 0x38)
        golden = bool(m.u8(squad + 0x20)) if squad else None
        return {"inst_id": m.i32(obj + 0x10), "chess_id": m.string_field(obj, 0x18),
                "direction": m.i32(obj + 0x20),
                "pos": {"row": m.i32(obj + 0x24), "col": m.i32(obj + 0x28)},
                "item_type": m.i32(obj + 0x2C), "player_index": m.i32(obj + 0x30),
                "side": m.i32(obj + 0x40), "card_uid": m.u32(obj + 0x44),
                "in_hand": in_hand, "in_battle": in_battle, "is_golden": golden}

    def _pieces(self, manager: int, data_center: int) -> tuple[
            list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        m = self.m
        battle_data = m.ptr(data_center + 0xE8, nullable=False)
        m.require_class(battle_data, "AutoChessBattleDataModel")
        chess_map = m.ptr(battle_data + 0x18, nullable=False)
        m.require_class(chess_map, "ChessMapInfo")
        map_manager = m.ptr(manager + 0x70, nullable=False)
        m.require_class(map_manager, "AutoChessMapAreaManager")
        hand_grids = m.hashset_grid_positions(m.ptr(map_manager + 0x18), required=True)
        battle_grids = m.hashset_grid_positions(m.ptr(map_manager + 0x10), required=True)
        board, hand, other = [], [], []
        for encoded, item in m.listdict_int_object(m.ptr(chess_map + 0x18), required=True):
            if not item:
                continue
            row, col = m.i32(item + 0x24), m.i32(item + 0x28)
            if encoded != ((row << 16) + (col & 0xFFFF)):
                raise StateReadError("chess map key and ChessInst position disagree")
            in_hand = (row, col) in hand_grids
            in_battle = (row, col) in battle_grids
            parsed = self._piece(item, in_hand=in_hand, in_battle=in_battle)
            (hand if in_hand else board if in_battle else other).append(parsed)
        grids = {"battle": [{"row": row, "col": col} for row, col in sorted(battle_grids)],
                 "hand": [{"row": row, "col": col} for row, col in sorted(hand_grids)]}
        return board, hand, other, grids

    def _enemy_preview(self, data_center: int) -> list[dict[str, Any]]:
        m = self.m
        data = m.ptr(data_center + 0x78)
        if not data:
            return []
        m.require_class(data, "SelfBattleData")
        rounds = []
        for round_number, enemies in m.listdict_int_object(m.ptr(data + 0x10)):
            entries = []
            for enemy in m.list_items(enemies):
                if not enemy:
                    continue
                actual = m.class_name(enemy)
                if actual != "SelfEnemyInfo":
                    raise StateReadError(f"expected SelfEnemyInfo, got {actual}")
                entries.append({"inst_id": m.i32(enemy + 0x10),
                                "enemy_id": m.string_field(enemy, 0x18),
                                "special": bool(m.u8(enemy + 0x20)),
                                "effect_inst_id": m.i32(enemy + 0x24),
                                "action_index": m.i32(enemy + 0x28)})
            rounds.append({"round": round_number, "enemies": entries})
        return rounds

    def _choices(self, data_center: int) -> dict[str, Any] | None:
        m = self.m
        model = m.ptr(data_center + 0x108)
        if not model:
            return None
        m.require_class(model, "AutoChessEffectChooseDataModel")
        options = []
        for option in m.list_items(m.ptr(model + 0x20)):
            if option:
                m.require_class(option, "ChooseStateSlot")
                options.append({"id": m.string_field(option, 0x10),
                                "slot_id": m.i32(option + 0x18), "price": m.i32(option + 0x1C)})
        if not options and not m.string_field(model, 0x18):
            return None
        return {"choice_id": m.string_field(model, 0x18), "options": options,
                "finish_timestamp": m.i64(model + 0x30), "current_queue_index": m.i32(model + 0x38),
                "cooperative": bool(m.u8(model + 0x3C))}

    def _team(self) -> dict[str, Any] | None:
        m = self.m
        _, mode = self._service_mode()
        if not mode:
            return None
        info = m.ptr(mode + 0x50)
        if not info:
            return None
        m.require_class(info, "AutoChessServiceTeamInfo")
        state = m.i32(info + 0x18)
        players = []
        for player in m.list_items(m.ptr(info + 0x40)):
            if not player:
                continue
            m.require_class(player, "MsgAutoChessPlayerStatus")
            players.append({"uid": m.string_field(player, 0x10), "nickname": m.string_field(player, 0x18),
                            "nickname_number": m.string_field(player, 0x20),
                            "strategy": m.string_field(player, 0x68), "level": m.i32(player + 0x70),
                            "state": m.i32(player + 0x74), "connect_state": m.i32(player + 0x78),
                            "current_option_id": m.i32(player + 0x88)})
        brief = info + 0x60
        choices = []
        for choice in m.list_items(m.ptr(brief + 0x18)):
            if choice:
                m.require_class(choice, "MsgStrategyChoice")
                choices.append({"uid": m.string_field(choice, 0x10),
                                "strategy": m.string_field(choice, 0x18),
                                "pass_count": m.i32(choice + 0x20)})
        return {"available": state != 0, "state_id": state,
                "state": TEAM_STATES.get(state, f"UNKNOWN_{state}"),
                "end_timestamp": m.i64(info + 0x10), "mode_id": m.string_field(info, 0x20),
                "owner_uid": m.string_field(info, 0x28),
                "monster_keys": [m.string(value) for value in m.list_items(m.ptr(info + 0x30))],
                "boss_id": m.string_field(info, 0x38), "players": players,
                "match": bool(m.u8(info + 0x80)), "stage_id": m.string_field(info, 0x88),
                "team_id": m.string_field(info, 0x90),
                "strategy_decision": {"finish_timestamp": m.i64(brief),
                                      "can_skip": bool(m.u8(brief + 8)),
                                      "current_uid": m.string_field(brief, 0x10),
                                      "choices": choices}}

    def _service_mode(self) -> tuple[int, int]:
        m = self.m
        service_class = m.typeinfo(TYPEINFO_AUTO_CHESS_SERVICE, "AutoChessService")
        parent = m.ptr(service_class + 0x58, nullable=False)
        core_base = m.ptr(parent + 0x58, nullable=False)
        if m.class_name_from_class(core_base) != "ServiceCoreBase`1":
            raise StateReadError("AutoChessService parent chain changed")
        static_fields = m.ptr(core_base + 0xB8, nullable=False)
        service = m.ptr(static_fields)
        if not service:
            return 0, 0
        m.require_class(service, "AutoChessService")
        mode = m.ptr(service + 0x28)
        if not mode:
            return service, 0
        m.require_class(mode, "AutoChessServiceMode")
        return service, mode

    def service_battle_snapshot(self) -> dict[str, Any]:
        m = self.m
        _, mode = self._service_mode()
        if not mode:
            raise StateReadError("AutoChessServiceMode is unavailable")
        server = m.ptr(mode + 0x28, nullable=False)
        m.require_class(server, "AutoChessBattleServer")
        info = m.ptr(server + 0xC8, nullable=False)
        m.require_class(info, "AutoChessServiceBattleInfo")
        scene = m.ptr(info + 0x30, nullable=False)
        m.require_class(scene, "AutoChessBattleSceneData")
        state_module = m.ptr(scene + 0x28, nullable=False)
        state_data = m.ptr(state_module + 0x18, nullable=False)
        m.require_class(state_data, "SceneStateData")
        phase_id = m.i32(state_data + 0x10)
        settle = None
        settle_module = m.ptr(scene + 0x60)
        if settle_module:
            settle_data = m.ptr(settle_module + 0x18)
            if settle_data:
                m.require_class(settle_data, "SettleData")
                settle = {"active": bool(m.u8(settle_data + 0x10)),
                          "state_id": m.i32(settle_data + 0x14),
                          "state": "SUCCESS" if m.i32(settle_data + 0x14) == 1 else "FAILED"}
        observed_ns = time.time_ns()
        return {"available": True, "live": True, "source": "process_memory",
                "observed_at_unix_ns": observed_ns,
                "snapshot_id": hashlib.sha256(f"{m.pid}:{observed_ns}:service-battle".encode()).hexdigest()[:20],
                "process": {"pid": m.pid, "module_base": f"0x{m.module_base:x}",
                            "layout_build_id": BUILD_ID},
                "phase_id": phase_id, "phase": PHASES.get(phase_id, f"UNKNOWN_{phase_id}"),
                "round": m.i32(state_data + 0x14), "force_end_time": m.i64(state_data + 0x18),
                "stage_id": m.string_field(info, 0x18), "mode_id": m.string_field(info, 0x28),
                "settle": settle, "unavailable": ["battle object graph disposed; service snapshot only"]}

    def settle_view_snapshot(self) -> dict[str, Any]:
        m = self.m
        root_class = m.typeinfo(TYPEINFO_SETTLE_ROOT, "AutoChessSettleGameViewModel")
        tagged = root_class | 0xB400000000000000
        regions = [region for region in m.maps
                   if region["perm"] == "rw-p" and region["path"] == ""]
        candidates = []
        for address in scan_ptrs(m.mem, {tagged}, regions):
            try:
                personal = m.ptr(address + 0x30)
                team = m.ptr(address + 0x38)
                act_id = m.string_field(address, 0x10)
                mode_name = m.string_field(address, 0x20)
                if (act_id and mode_name and personal and team and
                        m.class_name(personal) == "AutoChessSettleGamePersonalViewModel" and
                        m.class_name(team) == "AutoChessSettleGameTeamViewModel"):
                    candidates.append((address, personal, team, act_id, mode_name))
            except StateReadError:
                continue
        if len(candidates) != 1:
            raise StateReadError(f"expected one settle view model, got {len(candidates)}")
        root, personal, team, act_id, mode_name = candidates[0]
        status_id = m.i32(personal + 0x30)
        team_state_id = m.i32(team + 0x1C)
        finished = bool(m.u8(team + 0x19))
        status_names = {-1: "NONE", 0: "INTERRUPT", 1: "DEAD", 2: "PASS", 3: "BATTLING"}
        team_names = {0: "NONE", 1: "BATTLING", 2: "FAIL", 3: "SUCCESS"}
        observed_ns = time.time_ns()
        return {"available": True, "live": True, "source": "process_memory",
                "observed_at_unix_ns": observed_ns,
                "snapshot_id": hashlib.sha256(f"{m.pid}:{observed_ns}:settle-view".encode()).hexdigest()[:20],
                "process": {"pid": m.pid, "module_base": f"0x{m.module_base:x}",
                            "layout_build_id": BUILD_ID},
                "phase": "END" if finished else "SETTLE", "phase_id": 8 if finished else 7,
                "act_id": act_id, "mode_name": mode_name,
                "round": m.i32(personal + 0x38),
                "outcome": {"player_status_id": status_id,
                            "player_status": status_names.get(status_id, f"UNKNOWN_{status_id}"),
                            "team_state_id": team_state_id,
                            "team_state": team_names.get(team_state_id, f"UNKNOWN_{team_state_id}"),
                            "reward_count": m.i32(personal + 0xA0),
                            "show_home": bool(m.u8(personal + 0xC9))},
                "verified": {"settle_view_model": f"0x{root:x}"},
                "unavailable": ["battle object graph disposed; settle view snapshot only"]}

    def snapshot(self) -> dict[str, Any]:
        m = self.m
        observed_ns = time.time_ns()
        manager, data_center = self._manager()
        phase = self._phase(manager)
        players, mine = self._players(data_center)
        shop = self._shop(data_center)
        if shop["coin"] != mine["shop_coin"] or shop["level"] != mine["shop_level"]:
            raise StateReadError("shop model and self player economy disagree")
        board, hand, other_pieces, valid_grids = self._pieces(manager, data_center)
        equipment = {item["inst_id"]: item for item in mine["inventory"]["equipment"]}
        characters = {item["inst_id"]: item for item in mine["inventory"]["characters"]}
        for piece in board + hand + other_pieces:
            char = characters.get(piece["inst_id"])
            piece["equipment"] = ([equipment[value] for value in char["equipment_inst_ids"]
                                    if value in equipment] if char else [])
        self_board = [piece for piece in board if piece["player_index"] == mine["index"]]
        snapshot = {
            "available": True, "live": True, "source": "process_memory",
            "observed_at_unix_ns": observed_ns,
            "snapshot_id": hashlib.sha256(f"{m.pid}:{observed_ns}:{manager:x}:{data_center:x}".encode()).hexdigest()[:20],
            "process": {"pid": m.pid, "module_base": f"0x{m.module_base:x}",
                        "layout_build_id": BUILD_ID},
            "verified": {"manager_class": "AutoChessGameManager", "data_center_class": "AutoChessDataCenter",
                         "manager_address": f"0x{manager:x}", "data_center_address": f"0x{data_center:x}"},
            **phase,
            "act_id": m.string_field(data_center, 0x10), "round": m.i32(data_center + 0x3C),
            "mode_id": m.string_field(data_center, 0x50), "boss_id": m.string_field(data_center, 0x60),
            "stage_id": m.string_field(data_center, 0xA0), "self_player_index": m.i32(data_center + 0x38),
            "me": mine, "players": players, "board": board, "hand": hand,
            "other_pieces": other_pieces,
            "valid_grids": valid_grids,
            "deploy_count": len(self_board), "max_deploy": mine["max_deploy"], "shop": shop,
            "enemy_preview": self._optional("enemy_preview", lambda: self._enemy_preview(data_center), []),
            "choice": self._optional("choice", lambda: self._choices(data_center)),
            "team": self._optional("team", self._team),
            "unavailable": self.unavailable,
        }
        return snapshot

    def team_snapshot(self) -> dict[str, Any]:
        observed_ns = time.time_ns()
        team = self._team()
        if team is None:
            team = {"available": False, "state": None, "reason": "AutoChessService is not running"}
        return {"available": True, "live": True, "source": "process_memory",
                "observed_at_unix_ns": observed_ns,
                "snapshot_id": hashlib.sha256(f"{self.m.pid}:{observed_ns}:team".encode()).hexdigest()[:20],
                "process": {"pid": self.m.pid, "module_base": f"0x{self.m.module_base:x}",
                            "layout_build_id": BUILD_ID}, "team": team,
                "unavailable": self.unavailable}


def _adb_path(explicit: str | None) -> str:
    if explicit:
        return explicit
    sdk_root = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if sdk_root:
        executable = "adb.exe" if os.name == "nt" else "adb"
        candidate = Path(sdk_root) / "platform-tools" / executable
        if candidate.is_file():
            return str(candidate)
    return "adb.exe" if os.name == "nt" else "adb"


def _serial(adb: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    result = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=10)
    serials = [line.split()[0] for line in result.stdout.splitlines()
               if line.strip().endswith("\tdevice")]
    if len(serials) != 1:
        raise StateReadError(f"--serial is required when adb has {len(serials)} devices")
    return serials[0]


def _pid(adb: str, serial: str, explicit: int | None) -> int:
    if explicit:
        return explicit
    result = subprocess.run([adb, "-s", serial, "shell", "pidof", PACKAGE],
                            capture_output=True, text=True, timeout=10)
    values = result.stdout.split()
    if result.returncode or len(values) != 1:
        raise StateReadError(f"expected one {PACKAGE} pid, got {values}")
    return int(values[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=os.environ.get("AK_ADB_SERIAL"))
    parser.add_argument("--adb")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--port", type=int, default=27047)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--team-only", action="store_true",
                        help="Read AutoChessService team/strategy state without requiring a battle instance")
    args = parser.parse_args()
    try:
        adb = _adb_path(args.adb)
        serial = _serial(adb, args.serial)
        pid = _pid(adb, serial, args.pid)
        reader = AutoChessReader(LiveMemory(pid, serial, adb, args.port))
        last_error = None
        for attempt in range(3):
            try:
                snapshot = reader.team_snapshot() if args.team_only else reader.snapshot()
                break
            except StateReadError as exc:
                last_error = exc
                if attempt == 2:
                    if not args.team_only:
                        try:
                            fallback = reader.service_battle_snapshot()
                        except StateReadError:
                            pass
                        else:
                            if fallback.get("phase") in ("SETTLE", "END"):
                                snapshot = fallback
                                break
                            # A live service with a non-terminal phase is
                            # authoritative. Never let an old, uncollected
                            # settle view model override the current battle.
                            raise exc
                        try:
                            snapshot = reader.settle_view_snapshot()
                            break
                        except StateReadError:
                            pass
                    raise
                # Managed objects can be replaced while a phase transition is
                # committing. Re-read the whole graph; never mix old/new nodes.
                reader.unavailable.clear()
                time.sleep(0.12)
        else:  # pragma: no cover - loop either breaks or raises
            raise last_error or StateReadError("snapshot retry exhausted")
    except (StateReadError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"available": False, "live": False, "source": "process_memory",
                          "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(snapshot, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
