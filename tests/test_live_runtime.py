"""Read-only integration checks against a running real game process.

Run explicitly with AK_LIVE_TEST=1. These tests never substitute fixtures or
fabricated state and never dispatch game actions.
"""
from __future__ import annotations

import os
import unittest

from ak_agent.config import load
from ak_agent.executors import build
from ak_agent.tools import tool_names


@unittest.skipUnless(os.environ.get("AK_LIVE_TEST") == "1",
                     "set AK_LIVE_TEST=1 with the real game in AutoChess")
class LiveRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executor = build(load())
        cls.state = cls.executor.get_state().to_json()

    def test_snapshot_is_live_process_memory(self):
        self.assertTrue(self.state["available"])
        self.assertTrue(self.state["live"])
        self.assertEqual(self.state["source"], "process_memory")
        self.assertTrue(self.state["snapshot_id"])
        self.assertGreater(self.state["process"]["pid"], 0)
        if self.state["phase"] in ("SETTLE", "END"):
            self.assertEqual(self.state["unavailable"],
                             ["battle object graph disposed; settle view snapshot only"])
        else:
            self.assertFalse(self.state["unavailable"])

    def test_object_graph_consistency(self):
        if self.state["phase"] in ("SETTLE", "END"):
            self.skipTest("battle object graph is intentionally disposed at settlement")
        self.assertEqual(self.state["shop"]["coin"], self.state["me"]["shop_coin"])
        self.assertEqual(self.state["shop"]["level"], self.state["me"]["shop_level"])
        self.assertEqual(self.state["deploy_count"],
                         sum(piece["player_index"] == self.state["self_player_index"]
                             for piece in self.state["board"]))
        positions = [(piece["pos"]["row"], piece["pos"]["col"])
                     for piece in self.state["board"] + self.state["hand"]]
        self.assertEqual(len(positions), len(set(positions)))

    def test_default_tools_are_structured_only(self):
        names = set(tool_names(self.executor))
        self.assertTrue({"mcp__arknights__get_game_state",
                         "mcp__arknights__get_team_state",
                         "mcp__arknights__deploy_chess"}.issubset(names))
        self.assertTrue(names.isdisjoint({"mcp__arknights__screenshot",
                                          "mcp__arknights__tap_raw",
                                          "mcp__arknights__tap_ui",
                                          "mcp__arknights__send_game_request"}))


if __name__ == "__main__":
    unittest.main()
