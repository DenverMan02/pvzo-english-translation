import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gm
import player_store as store


class GMStackDeltaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = store.DB_PATH
        self.db_path = Path(self.tmp.name) / "players.db"
        store.set_db_path(str(self.db_path))
        gm._stack_item_rows.cache_clear()

    def tearDown(self):
        store.set_db_path(self.old_db)
        gm._stack_item_rows.cache_clear()
        self.tmp.cleanup()

    def create_player(self, uin=101):
        store.init_db()
        return store.get_or_create_player(uin)[0]

    def run_cli(self, *args):
        with mock.patch.object(sys, "argv", ["gm.py", *map(str, args)]), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            gm.main()

    def test_real_invalid_puzzle_cannot_create_db_or_account(self):
        self.assertNotIn(67813, gm._stack_item_rows())
        with mock.patch.object(store, "init_db", wraps=store.init_db) as init, \
                mock.patch.object(store, "get_or_create_player", wraps=store.get_or_create_player) as create:
            with self.assertRaises(SystemExit) as error:
                self.run_cli(101, "item", 67813, 1)
            self.assertEqual(error.exception.code, 1)
            init.assert_not_called()
            create.assert_not_called()
        self.assertFalse(self.db_path.exists())

    def test_invalid_puzzle_rejection_does_not_modify_existing_save(self):
        self.create_player()
        store.add_item(101, 67813, 999)
        before = dict(store._thread_db().execute("SELECT * FROM players WHERE uin=101").fetchone())
        with self.assertRaises(ValueError):
            gm.apply_stack_delta(101, 67813, 1)
        after = dict(store._thread_db().execute("SELECT * FROM players WHERE uin=101").fetchone())
        self.assertEqual(before, after)
        self.assertEqual(store.get_item_count(101, 67813), 999)

    def test_negative_delta_removes_historical_unknown_id(self):
        self.create_player()
        store.add_item(101, 67813, 999)
        self.assertEqual(gm.apply_stack_delta(101, 67813, -10), 989)
        self.assertEqual(gm.apply_stack_delta(101, 67813, -2_000_000_000), 0)
        self.assertNotIn((67813, 0), store.get_items(101))

    def test_negative_delta_does_not_create_unknown_account(self):
        with self.assertRaises(ValueError):
            gm.apply_stack_delta(101, 67813, -1)
        self.assertFalse(self.db_path.exists())

    def test_actual_shared_puzzle_material_can_be_added(self):
        row = gm.validate_stack_delta(67801, 1)
        self.assertEqual(int(row["id"]), 67801)
        self.assertEqual(gm.apply_stack_delta(101, 67801, 1), 1)
        self.assertIsNotNone(store._thread_db().execute("SELECT 1 FROM players WHERE uin=101").fetchone())

    def test_valid_cli_item_uses_the_validated_path(self):
        self.run_cli(101, "item", 67801, 2)
        self.assertEqual(store.get_item_count(101, 67801), 2)

    def test_currency_card_equipment_and_reagent_are_not_bag_stacks(self):
        rows = gm._stack_item_rows()
        ids = [1, 3, 37]
        ids.extend(next(iid for iid, row in rows.items() if int(row.get("type") or 0) == kind)
                   for kind in (4, 7, 11))
        for item_id in ids:
            with self.subTest(item_id=item_id), self.assertRaises(ValueError):
                gm.validate_stack_delta(item_id, 1)

    def test_bool_noninteger_zero_and_range_are_rejected(self):
        cases = [(True, 1, 0), (67801, True, 0), (67801, 1, True),
                 (67801, 1.5, 0), (67801, 0, 0), (0, 1, 0),
                 (100_000_000, 1, 0), (67801, 2_000_000_001, 0),
                 (67801, -2_000_000_001, 0), (67801, 1, -1)]
        for item_id, delta, current in cases:
            with self.subTest(values=(item_id, delta, current)), self.assertRaises(ValueError):
                gm.validate_stack_delta(item_id, delta, current)

    def test_client_stack_limit_includes_existing_count(self):
        with mock.patch.object(gm, "_stack_item_rows", return_value={
                4001: {"id": "4001", "name": "塔防钥匙", "type": "2", "stackNum": "10"}}):
            self.assertIsNotNone(gm.validate_stack_delta(4001, 2, 8))
            with self.assertRaises(ValueError):
                gm.validate_stack_delta(4001, 3, 8)

    def test_zero_client_limit_still_enforces_cumulative_two_billion(self):
        self.create_player()
        store.add_item(101, 43, 1_999_999_999)
        self.assertEqual(gm.apply_stack_delta(101, 43, 1), 2_000_000_000)
        with self.assertRaises(ValueError):
            gm.apply_stack_delta(101, 43, 1)
        self.assertEqual(store.get_item_count(101, 43), 2_000_000_000)

    def test_client_limit_above_safe_limit_cannot_bypass_two_billion(self):
        with mock.patch.object(gm, "_stack_item_rows", return_value={
                4001: {"id": "4001", "name": "塔防钥匙", "type": "2", "stackNum": "5000000000"}}):
            with self.assertRaises(ValueError):
                gm.validate_stack_delta(4001, 1, 2_000_000_000)

    def test_overlimit_historical_balance_can_be_reduced_in_steps(self):
        self.create_player()
        store.add_item(101, 43, 6_000_000_000)
        self.assertEqual(gm.apply_stack_delta(101, 43, -2_000_000_000), 4_000_000_000)
        self.assertEqual(gm.apply_stack_delta(101, 43, -2_000_000_000), 2_000_000_000)

    def test_prestige_cache_matches_stack_after_add_and_remove(self):
        self.create_player()
        self.assertEqual(gm.apply_stack_delta(101, 43, 5), 5)
        row = store._thread_db().execute("SELECT prestige FROM players WHERE uin=101").fetchone()
        self.assertEqual(row[0], 5)
        gm.apply_stack_delta(101, 43, -2)
        row = store._thread_db().execute("SELECT prestige FROM players WHERE uin=101").fetchone()
        self.assertEqual(row[0], 3)

    def test_prestige_cache_failure_rolls_back_the_stack_change(self):
        self.create_player()
        gm.apply_stack_delta(101, 43, 5)
        with mock.patch.object(store, "save_player", side_effect=ValueError("缓存写入失败")):
            with self.assertRaises(ValueError):
                gm.apply_stack_delta(101, 43, 1)
        self.assertEqual(store.get_item_count(101, 43), 5)
        row = store._thread_db().execute("SELECT prestige FROM players WHERE uin=101").fetchone()
        self.assertEqual(row[0], 5)

    def test_new_account_starter_balance_is_rechecked_and_rolled_back(self):
        real_create = store.get_or_create_player
        def starter_create(uin):
            result = real_create(uin)
            store.add_item(uin, 43, 1)
            return result
        with mock.patch.object(store, "get_or_create_player", side_effect=starter_create):
            with self.assertRaises(ValueError):
                gm.apply_stack_delta(101, 43, 2_000_000_000)
        con = store._thread_db()
        self.assertIsNone(con.execute("SELECT 1 FROM players WHERE uin=101").fetchone())
        self.assertIsNone(con.execute("SELECT 1 FROM items WHERE uin=101").fetchone())

    def test_concurrent_balance_change_is_rechecked_atomically(self):
        self.create_player()
        real_read = gm._readonly_stack_state
        def balance_changes_after_read(uin, item_id):
            result = real_read(uin, item_id)
            store.add_item(uin, item_id, 2_000_000_000)
            return result
        with mock.patch.object(gm, "_readonly_stack_state", side_effect=balance_changes_after_read):
            with self.assertRaises(ValueError):
                gm.apply_stack_delta(101, 43, 1)
        self.assertEqual(store.get_item_count(101, 43), 2_000_000_000)

    def test_malformed_cli_item_args_do_not_initialize_database(self):
        for args in (("item",), ("item", "67801"), ("item", "67801", "1", "extra"),
                     ("item", "67801", "1.5"), ("item", "67801", "0")):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.run_cli(101, *args)
            self.assertFalse(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
