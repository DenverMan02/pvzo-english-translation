"""Original-client probability/slot contracts for the local tavern reward roll.

This reads clientDB and calls reward helpers. Module registration is isolated
in a disposable database before importing the game server. Set PVZ_TAVERN_SERVER_DIR to
test an isolated server checkout instead of the requested D: installation.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SERVER_DIR = Path(os.environ.get(
    "PVZ_TAVERN_SERVER_DIR",
    str(Path(__file__).resolve().parents[1]),
))
sys.path.insert(0, str(SERVER_DIR))
# Server adapters initialize schemas during import; isolate those too.
import player_store as _import_store
_IMPORT_TEMP = tempfile.TemporaryDirectory(prefix='pvz_tavern_import_')
_import_store.set_db_path(str(Path(_IMPORT_TEMP.name) / 'import.db'))
_import_store.init_db()
import pvz_poc_server as server


def client_items():
    rows = server._read_clientdb_table("ItemInfo.txt")
    indices = {name: index for index, name in enumerate(rows[0])}
    return {
        int(row[indices["id"]]): {
            name: row[index] for name, index in indices.items() if index < len(row)
        }
        for row in rows[1:]
    }


class CardMarketRewardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.load_battle_cards()
        cls.templates = client_items()
        cls.card_by_item = dict(server.card_market_pool())

    def player(self, **changes):
        player = {
            "uin": 990077,
            "card_market_single_draws": 1,
            "card_market_big_draws": 0,
            "card_market_friend_draws": 1,
            "card_market_lucky_flower_num": 0,
            "card_market_friend_day": "",
            "card_market_friend_free_used": 1,
            "card_market_friend_next_free_at": 0,
        }
        player.update(changes)
        return player

    def card(self, item_id):
        return server._BATTLE_CARDS[self.card_by_item[item_id]]

    def assert_valid_rewards(self, rewards, slots):
        self.assertEqual(len(rewards), slots)
        for item_id, count in rewards:
            self.assertIn(item_id, self.templates)
            self.assertIsInstance(count, int)
            self.assertGreater(count, 0)

    def test_single_probability_boundaries(self):
        weights = ((4, 1), (3, 1), (2, 3), (1, 15), (None, 80))
        expected = {1: 4, 2: 3, 3: 2, 5: 2, 6: 1, 20: 1, 21: None, 100: None}
        for roll, rarity in expected.items():
            with self.subTest(roll=roll), patch.object(server.random, "randint", return_value=roll):
                self.assertEqual(server._card_market_quality(weights), rarity)

    def test_guaranteed_single_probability_boundaries(self):
        weights = ((4, 8), (3, 25), (2, 35), (1, 32))
        expected = {1: 4, 8: 4, 9: 3, 33: 3, 34: 2, 68: 2, 69: 1, 100: 1}
        for roll, rarity in expected.items():
            with self.subTest(roll=roll), patch.object(server.random, "randint", return_value=roll):
                self.assertEqual(server._card_market_quality(weights), rarity)

    def test_regular_single_can_reveal_other_item(self):
        with patch.object(server.random, "randint", return_value=100):
            rewards = server._card_market_roll_rewards(1, self.player())
        self.assert_valid_rewards(rewards, 1)
        self.assertNotIn(rewards[0][0], self.card_by_item)

    def test_tavern_fragment_lookup_accepts_original_and_translated_values(self):
        rules = server.card_market_rules
        self.assertTrue(rules._is_tavern_gain_way("酒吧"))
        self.assertTrue(rules._is_tavern_gain_way("Tavern"))
        self.assertTrue(rules._is_tavern_gain_way("BAR"))
        self.assertFalse(rules._is_tavern_gain_way("Magic Exchange"))

    def test_first_single_matches_original_yangtao_promise(self):
        rewards = server._card_market_roll_rewards(1, self.player(card_market_single_draws=0))
        self.assert_valid_rewards(rewards, 1)
        self.assertIn(int(self.card(rewards[0][0])["tdId"]),
                      server.card_market_rules.STARFRUIT_TD_IDS)
        self.assertEqual(self.card(rewards[0][0])["star"], 3)

    def test_tenth_single_guarantees_three_star(self):
        with patch.object(server.random, "randint", return_value=100):
            rewards = server._card_market_roll_rewards(1, self.player(card_market_single_draws=9))
        self.assert_valid_rewards(rewards, 1)
        self.assertEqual(self.card(rewards[0][0])["star"], 3)
        self.assertEqual(self.card(rewards[0][0])["rarity"], 1)

    def test_big_packet_has_original_guaranteed_categories(self):
        rewards = server._card_market_roll_rewards(2, self.player())
        self.assert_valid_rewards(rewards, 10)
        cards = [self.card(item_id) for item_id, _ in rewards if item_id in self.card_by_item]
        self.assertTrue({1, 2, 3}.issubset({card["star"] for card in cards}))
        noncards = [item_id for item_id, _ in rewards if item_id not in self.card_by_item]
        self.assertIn(210, noncards)
        self.assertIn(2103, noncards)
        self.assertTrue(any(
            self.templates[item_id]["type"] == "5"
            and self.templates[item_id]["subType"] == "501"
            and self.templates[item_id]["quality"] == "4"
            for item_id in noncards
        ))

    def test_big_packet_probability_boundaries(self):
        weights = ((4, 10), (3, 25), (2, 35), (1, 30))
        expected = {1: 4, 10: 4, 11: 3, 35: 3, 36: 2, 70: 2, 71: 1, 100: 1}
        for roll, rarity in expected.items():
            with self.subTest(roll=roll), patch.object(server.random, "randint", return_value=roll):
                self.assertEqual(server._card_market_quality(weights), rarity)

    def test_nine_lucky_flowers_force_rare_three_star(self):
        with patch.object(server.random, "randint", return_value=100):
            rewards = server._card_market_roll_rewards(
                2, self.player(card_market_lucky_flower_num=9)
            )
        self.assert_valid_rewards(rewards, 10)
        self.assertEqual(self.card(rewards[0][0])["star"], 3)
        self.assertEqual(self.card(rewards[0][0])["rarity"], 4)

    def test_first_daily_free_friend_draw_matches_plant_promise(self):
        with patch.object(server, "_card_market_friend_state", return_value=("2099-01-01", 0, 0)):
            rewards = server._card_market_roll_rewards(3, self.player())
        self.assert_valid_rewards(rewards, 5)
        self.assertIn(rewards[0][0], self.card_by_item)

    def test_later_friend_reward_contains_fragment_and_no_plants(self):
        with patch.object(server, "_card_market_friend_state", return_value=("2099-01-01", 1, 0)):
            for _ in range(20):
                rewards = server._card_market_roll_rewards(3, self.player())
                self.assert_valid_rewards(rewards, 5)
                self.assertFalse(any(item_id in self.card_by_item for item_id, _ in rewards))
                self.assertTrue(any(
                    self.templates[item_id]["type"] == "5"
                    and self.templates[item_id]["subType"] == "501"
                    for item_id, _ in rewards
                ))

    def test_roll_does_not_mutate_player(self):
        for group in (1, 2, 3):
            player = self.player()
            before = dict(player)
            server._card_market_roll_rewards(group, player)
            self.assertEqual(player, before)


if __name__ == '__main__':
    try:
        result = unittest.main(verbosity=2, exit=False).result
    finally:
        _import_store.close_thread_db()
        _IMPORT_TEMP.cleanup()
    sys.exit(0 if result.wasSuccessful() else 1)
