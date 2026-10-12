"""Isolated regression checks for the tavern's daily friendship free claims.

PVZ_TEST_SERVER_ROOT may point at a staged server checkout. All player state is
written to a temporary SQLite database; importing the server writes no pycache.
"""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.dont_write_bytecode = True
SERVER_ROOT = os.environ.get(
    "PVZ_TEST_SERVER_ROOT", str(Path(__file__).resolve().parents[1])
)
sys.path.insert(0, SERVER_ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store

_TEMP = tempfile.TemporaryDirectory(prefix="pvz_tavern_friend_")
store.DB_PATH = os.path.join(_TEMP.name, "test.db")
store.init_db()

import pvz_poc_server as srv


class FriendshipFreeClaimTests(unittest.TestCase):
    sequence = 700031000

    def setUp(self):
        type(self).sequence += 1
        self.uin = type(self).sequence
        self.player, _ = store.get_or_create_player(self.uin)
        self.now = int(time.mktime(time.strptime("20261010 12:00:00", "%Y%m%d %H:%M:%S")))
        store.save_player(
            self.uin,
            friend_point=0,
            card_market_friend_day="",
            card_market_friend_free_used=0,
            card_market_friend_next_free_at=0,
        )
        self.player["friend_point"] = 0

    def wallet(self):
        with store._db() as con:
            row = con.execute("SELECT * FROM players WHERE uin=?", (self.uin,)).fetchone()
            return dict(row)

    def free_info(self, when=None, player=None):
        with patch.object(srv.time, "time", return_value=self.now if when is None else when):
            data = srv._card_market_freeinfo_dto(3, self.player if player is None else player)
        fields = srv.parse_varint_fields(data)
        return fields.get(2, 0), fields.get(3, 0)

    def pay(self, when=None, player=None):
        with patch.object(srv.time, "time", return_value=self.now if when is None else when):
            return srv._card_market_pay(self.player if player is None else player, 3)

    def free_state(self):
        row = self.wallet()
        return tuple(row[k] for k in (
            "card_market_friend_day", "card_market_friend_free_used",
            "card_market_friend_next_free_at",
        ))

    def test_three_daily_claims_and_exact_cooldown_boundary(self):
        self.assertEqual(self.free_info(), (3, 0))
        self.assertEqual(self.pay(), [])
        self.assertEqual(self.free_info(), (2, 300))
        state = self.free_state()
        self.assertIsNone(self.pay(self.now + 299))
        self.assertEqual(self.free_state(), state)
        self.assertEqual(self.free_info(self.now + 299), (2, 1))
        self.assertEqual(self.pay(self.now + 300), [])
        self.assertEqual(self.free_info(self.now + 300), (1, 300))
        self.assertEqual(self.pay(self.now + 600), [])
        self.assertEqual(self.free_info(self.now + 600), (0, 0))
        state = self.free_state()
        self.assertIsNone(self.pay(self.now + 900))
        self.assertEqual(self.free_state(), state)
        self.assertEqual(self.wallet()["friend_point"], 0)

    def test_relogin_reads_persisted_remaining_count_and_cooldown(self):
        self.assertEqual(self.pay(), [])
        logged_in_again, _ = store.get_or_create_player(self.uin)
        self.assertEqual(self.free_info(self.now + 100, logged_in_again), (2, 200))
        self.assertIsNone(self.pay(self.now + 100, logged_in_again))
        self.assertEqual(self.free_info(self.now + 300, logged_in_again), (2, 0))

    def test_midnight_resets_exhausted_count_and_previous_day_cooldown(self):
        midnight = int(time.mktime(time.strptime("20261011", "%Y%m%d")))
        store.save_player(
            self.uin, card_market_friend_day="20261010",
            card_market_friend_free_used=3,
            card_market_friend_next_free_at=midnight + 200,
        )
        self.assertEqual(self.free_info(midnight - 1), (0, 0))
        self.assertEqual(self.free_info(midnight), (3, 0))
        self.assertEqual(self.pay(midnight), [])
        self.assertEqual(self.free_info(midnight), (2, 300))
        self.assertEqual(self.free_state(), ("20261011", 1, midnight + 300))

    def test_stale_player_cannot_repeat_free_claim_or_invent_currency(self):
        stale = dict(self.player)
        self.assertEqual(self.pay(), [])
        stale.update(
            friend_point=100000,
            card_market_friend_day="",
            card_market_friend_free_used=0,
            card_market_friend_next_free_at=0,
        )
        self.assertEqual(self.free_info(self.now + 1, stale), (2, 299))
        state = self.free_state()
        self.assertIsNone(self.pay(self.now + 1, stale))
        self.assertEqual(self.free_state(), state)
        self.assertEqual(self.wallet()["friend_point"], 0)

    def test_paid_claim_during_cooldown_costs_500_without_consuming_free(self):
        self.assertEqual(srv.load_card_market_cost()[3]["pay"], (6, 500))
        self.assertEqual(self.pay(), [])
        store.save_player(self.uin, friend_point=1000)
        state = self.free_state()
        messages = self.pay(self.now + 1)
        self.assertIsNotNone(messages)
        self.assertIn("PVZ.Cmd.Cmd_SyncFriendPoint_SC", [name for name, _ in messages])
        self.assertEqual(self.wallet()["friend_point"], 500)
        self.assertEqual(self.free_state(), state)
        self.assertEqual(self.free_info(self.now + 1), (2, 299))

    def test_paid_claim_after_daily_exhaustion_preserves_free_state(self):
        for offset in (0, 300, 600):
            self.assertEqual(self.pay(self.now + offset), [])
        store.save_player(self.uin, friend_point=500)
        state = self.free_state()
        self.assertIsNotNone(self.pay(self.now + 900))
        self.assertEqual(self.wallet()["friend_point"], 0)
        self.assertEqual(self.free_state(), state)
        self.assertEqual(self.free_info(self.now + 900), (0, 0))

    def test_insufficient_currency_rejects_without_changes(self):
        self.assertEqual(self.pay(), [])
        store.save_player(self.uin, friend_point=499)
        before = self.wallet()
        self.assertIsNone(self.pay(self.now + 1))
        self.assertEqual(self.wallet(), before)

    def test_group_one_free_timer_remains_independent(self):
        store.save_player(self.uin, card_market_next_free_at=self.now + 42)
        self.assertEqual(self.pay(), [])
        self.assertEqual(self.wallet()["card_market_next_free_at"], self.now + 42)

    def test_simultaneous_requests_cannot_double_claim_one_free_slot(self):
        srv.load_card_market_cost()
        clients = [dict(self.player), dict(self.player)]
        def claim(player):
            try:
                return srv._card_market_pay(player, 3)
            finally:
                store.close_thread_db()
        with patch.object(srv.time, "time", return_value=self.now):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(claim, clients))
        self.assertEqual(results.count([]), 1)
        self.assertEqual(results.count(None), 1)
        self.assertEqual(self.free_info(), (2, 300))
        self.assertEqual(self.wallet()["friend_point"], 0)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        store.close_thread_db()
        _TEMP.cleanup()
