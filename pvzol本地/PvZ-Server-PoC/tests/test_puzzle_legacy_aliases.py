"""Isolated regressions for historical fake per-cell gold/red puzzle IDs.

Imports workspace source, reads the workspace clientDB, and writes only temporary
SQLite databases. No running service or formal D: save is used.
"""
from __future__ import annotations

import gc
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
spec = importlib.util.spec_from_file_location('puzzle_legacy_alias_server', SERVER / 'pvz_poc_server.py')
game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(game)
game._CLIENTDB_PATH = str(SERVER.parent / 'webroot' / 'clientDB.txt')
store = game.store
FAKE_IDS = set(range(61010, 61017)) | set(range(62013, 62017)) | {
    group * 100 + offset for group in range(660, 680) for offset in range(2, 26)}


class PuzzleLegacyAliasTests(unittest.TestCase):
    def setUp(self):
        qa = SERVER.parent.parent / 'qa'
        qa.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='puzzle-alias-test-', dir=qa)
        self.old_db = store.DB_PATH
        store.set_db_path(str(Path(self.tmp.name) / 'players.db'))
        store.init_db()
        self.player = store.get_or_create_player(101)[0]
        self.player['level'] = 200
        store.save_player(101, level=200)
        self.uid = 10006504
        store.add_card(101, self.uid, 6504, 300, 0)
        self.observer = patch.object(game, '_quest_observe_card_progress', return_value=[])
        self.observer.start()

    def tearDown(self):
        self.observer.stop()
        store.set_db_path(self.old_db)
        gc.collect()
        self.tmp.cleanup()

    def set_stage(self, stage=43):
        store.set_card_fill_quality(101, self.uid, stage)
        store.clear_card_filled_indices(101, self.uid)

    def fill(self, index):
        return game.handle_card_fill(game.pb_int64_field(1, self.uid) + game.pb_varint_field(2, index), self.player)

    def test_fake_id_whitelist_is_absent_from_every_current_definition(self):
        items = game._read_clientdb_table('ItemInfo.txt')
        header = {name: index for index, name in enumerate(items[0])}
        item_ids = {int(row[header['id']]) for row in items[1:] if row[header['id']].isdigit()}
        prices = set(game.load_puzzle_prices())
        recipe_ids = {item for board in game.load_card_puzzle().values() for item, amount in board['cost']}
        self.assertEqual(len(FAKE_IDS), 491)
        self.assertFalse(FAKE_IDS & (item_ids | prices | recipe_ids))
        self.assertTrue(recipe_ids <= item_ids & prices)
        # A future available-cost field must also be reviewed before migrating.
        table = game._read_clientdb_table('cardPuzzle.txt')
        cost_columns = [index for index, name in enumerate(table[0]) if 'cost' in name.lower()]
        for row in table[1:]:
            for column in cost_columns:
                ids = {int(piece.split(':', 1)[0]) for piece in row[column].split(';')
                       if piece and piece.split(':', 1)[0].isdigit()}
                self.assertFalse(ids & FAKE_IDS)

    def test_opening_every_puzzle_stage_cannot_create_or_replenish_legacy_aliases(self):
        store.add_item(101, 67813, 999)
        before = store.get_items(101)
        for stage in game.load_card_puzzle():
            with self.subTest(stage=stage):
                self.set_stage(stage)
                game.handle_card_getfillinfo(game.pb_int64_field(1, self.uid), self.player)
                self.assertEqual(store.get_items(101), before)

    def test_fake_red_eight_stacks_cannot_pay_for_fill_or_batch(self):
        self.set_stage()
        for item in range(67802, 67826):
            store.add_item(101, item, 999)
        before = store.get_items(101)
        self.assertFalse(self.fill(12))
        request = game.pb_int64_field(1, self.uid) + b''.join(game.pb_varint_field(2, index) for index in range(25))
        messages = game.handle_card_batchfill(request, self.player)
        self.assertNotIn('PVZ.Cmd.Cmd_Card_BatchFill_SC', dict(messages))
        self.assertEqual(store.get_card_filled_indices(101, self.uid), [])
        self.assertEqual(store.get_items(101), before)

    def test_buy_and_fill_cell_twelve_use_only_67801_even_when_67813_exists(self):
        self.set_stage()
        self.assertEqual(game.load_card_puzzle()[43]['cost'][12], (67801, 53))
        store.add_item(101, 67813, 999)
        store.add_item(101, 2020, 10000)
        request = game.pb_varint_field(1, 12) + game.pb_varint_field(2, 53) + game.pb_int64_field(3, self.uid)
        self.assertTrue(game.handle_card_exchangepiece(request, self.player))
        self.assertEqual(store.get_item_count(101, 67801), 53)
        self.assertEqual(store.get_item_count(101, 2020), 10000 - 53 * 65)
        self.assertTrue(self.fill(12))
        self.assertEqual(store.get_card_filled_indices(101, self.uid), [12])
        self.assertEqual(store.get_item_count(101, 67801), 0)
        self.assertEqual(store.get_item_count(101, 67813), 999)
        self.assertFalse({item for item, count in store.get_items(101)} & (FAKE_IDS - {67813}))

    def test_recycle_refunds_configured_materials_instead_of_per_cell_aliases(self):
        self.set_stage()
        store.add_card_filled_index(101, self.uid, 12)
        bag_before = store.get_items(101)
        items, coins, notes = game._card_recycle_reward(self.player, self.uid, (self.uid, 6504, 300, 0))
        self.assertEqual(items[67801], 53)
        self.assertFalse(set(items) & FAKE_IDS)
        self.assertEqual(store.get_items(101), bag_before)


if __name__ == '__main__':
    unittest.main()
