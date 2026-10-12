"""HTTP and archive regressions for obsolete puzzle cleanup, using a temporary DB."""
from __future__ import annotations

from contextlib import closing
import http.client
import http.server
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

SOURCE = Path(__file__).resolve().parents[1]
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

import admin_server as admin
import player_store as store

INVALID_IDS = tuple(range(67813, 67818))


class QuietHandler(admin.AdminHandler):
    def log_message(self, *_args):
        pass


class PartialDeleteFailure:
    """Allow one deletion, then simulate SQLite failing the rest of the batch."""
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, key):
        return getattr(self.connection, key)

    def executemany(self, sql, parameters):
        if sql.startswith('DELETE FROM items'):
            self.connection.execute(sql, parameters[0])
            raise sqlite3.OperationalError('injected partial DELETE failure')
        return self.connection.executemany(sql, parameters)


class AdminPuzzleRepairTests(unittest.TestCase):
    def setUp(self):
        qa_root = SOURCE.parent.parent / 'qa'
        qa_root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='puzzle-repair-test-', dir=qa_root)
        self.root = Path(self.tmp.name)
        self.backups = self.root / 'backups'
        self.old_db = store.DB_PATH
        store.set_db_path(str(self.root / 'players.db'))
        store.init_db()
        store.create_new_player(101)
        store.create_new_player(102)
        for item_id in INVALID_IDS:
            store.add_item(101, item_id, 999)
            store.add_item(102, item_id, 17)
        for item_id, count in ((67801, 321), (61001, 7), (1140, 5), (98765432, 9)):
            store.add_item(101, item_id, count)
        self.card_uid = 7654321
        store.add_card(101, self.card_uid, min(admin.CARD_NAMES), level=290)
        store.set_card_fill_quality(101, self.card_uid, 43)
        store.add_card_filled_index(101, self.card_uid, 0)
        store.add_card_filled_index(101, self.card_uid, 12)
        self.backup_patch = patch.object(admin, 'BACKUP_DIR', str(self.backups))
        self.port_patch = patch.object(admin, '_port_open', return_value=False)
        self.guard_patch = patch.object(admin, 'guard_blocks_writes', return_value=False)
        self.origin_patch = patch.dict(os.environ, {'PVZ_LAUNCHER_ORIGIN': 'http://127.0.0.1:43817'})
        self.backup_patch.start()
        self.port_mock = self.port_patch.start()
        self.guard_mock = self.guard_patch.start()
        self.origin_patch.start()
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()
        self.guard_patch.stop()
        self.origin_patch.stop()
        self.port_patch.stop()
        self.backup_patch.stop()
        store.set_db_path(self.old_db)
        self.tmp.cleanup()

    def request(self, path, fields=None, csrf=True):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            if fields is None:
                client.request('GET', path)
            else:
                posted = dict(fields)
                if csrf:
                    posted['csrf'] = admin.CSRF_TOKEN
                client.request('POST', path, urlencode(posted),
                               {'Content-Type': 'application/x-www-form-urlencoded'})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode('utf-8')
        finally:
            client.close()

    def assert_result(self, result, kind='ok'):
        status, headers, _body = result
        self.assertEqual(status, 303)
        query = parse_qs(urlsplit(headers['Location']).query)
        self.assertEqual(query['kind'], [kind])
        return query

    def rows(self):
        with closing(sqlite3.connect(store.DB_PATH)) as con:
            return list(con.execute('SELECT uin,item_id,count FROM items ORDER BY uin,item_id'))

    def dump(self, path=None):
        with closing(sqlite3.connect(str(path or store.DB_PATH))) as con:
            # Reinserted archived rows can have different physical row order.
            # Compare all schema and value statements, independent of that order.
            return sorted(con.iterdump())

    def artifacts(self):
        return sorted(path.name for path in self.backups.glob('*'))

    def test_removes_only_proven_ids_and_keeps_quality_filled_slots_and_other_accounts(self):
        before = self.rows()
        player = admin._player(101)
        expected = [row for row in before if not (row[0] == 101 and row[1] in INVALID_IDS)]
        self.assert_result(self.request('/player/repair-puzzles', {'uin': 101}))
        self.assertEqual(self.rows(), expected)
        self.assertEqual(admin._player(101), player)
        self.assertEqual(store.get_item_count(101, 67801), 321)
        self.assertEqual(store.get_item_count(101, 1140), 5)
        self.assertEqual(store.get_item_count(101, 98765432), 9)
        self.assertEqual(store.get_card_fill_quality(101, self.card_uid), 43)
        self.assertEqual(store.get_card_filled_indices(101, self.card_uid), [0, 12])

    def test_database_backup_and_json_can_restore_the_exact_pre_cleanup_state(self):
        before = self.dump()
        original_rows = self.rows()
        self.assert_result(self.request('/player/repair-puzzles', {'uin': 101}))
        databases = list(self.backups.glob('*.db'))
        self.assertEqual(len(databases), 1)
        backup = databases[0]
        self.assertEqual(self.dump(backup), before)
        manifest = json.loads(Path(str(backup) + '.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['uin'], 101)
        self.assertEqual(manifest['backup'], backup.name)
        self.assertEqual(manifest['items'], [{'item_id': iid, 'count': 999} for iid in INVALID_IDS])
        # Restore only the archived bag rows in the disposable DB.
        with closing(sqlite3.connect(store.DB_PATH)) as con:
            con.executemany('INSERT INTO items(uin,item_id,count) VALUES(?,?,?)',
                            [(manifest['uin'], row['item_id'], row['count']) for row in manifest['items']])
            con.commit()
        self.assertEqual(self.rows(), original_rows)
        self.assertEqual(self.dump(), before)

    def test_repeated_repair_is_a_noop_without_a_second_backup(self):
        self.assert_result(self.request('/player/repair-puzzles', {'uin': 101}))
        artifacts, rows = self.artifacts(), self.rows()
        query = self.assert_result(self.request('/player/repair-puzzles', {'uin': 101}))
        self.assertIn('没有需要整理', query['msg'][0])
        self.assertEqual(self.rows(), rows)
        self.assertEqual(self.artifacts(), artifacts)

    def test_get_displays_repair_action_without_mutating_or_backing_up(self):
        before = self.dump()
        status, _headers, body = self.request('/player?uin=101&embedded=1&theme=light&section=inventory')
        self.assertEqual(status, 200)
        self.assertIn('/player/repair-puzzles', body)
        self.assertIn('67813', body)
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.artifacts(), [])

    def test_backup_failure_leaves_every_row_unchanged(self):
        before = self.dump()
        with patch.object(admin, '_backup_database', side_effect=OSError('backup unavailable')), \
                patch.object(admin.traceback, 'print_exc'):
            self.assertEqual(self.request('/player/repair-puzzles', {'uin': 101})[0], 500)
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.artifacts(), [])

    def test_manifest_open_failure_leaves_every_row_unchanged(self):
        before = self.dump()
        with patch.object(admin, 'open', side_effect=PermissionError('manifest unavailable'), create=True), \
                patch.object(admin.traceback, 'print_exc'):
            self.assertEqual(self.request('/player/repair-puzzles', {'uin': 101})[0], 500)
        self.assertEqual(self.dump(), before)
        self.assertEqual(len(list(self.backups.glob('*.db'))), 1)

    def test_manifest_write_failure_leaves_every_row_unchanged(self):
        before = self.dump()
        with patch.object(admin.json, 'dump', side_effect=OSError('manifest disk full')), \
                patch.object(admin.traceback, 'print_exc'):
            self.assertEqual(self.request('/player/repair-puzzles', {'uin': 101})[0], 500)
        self.assertEqual(self.dump(), before)

    def test_partial_sql_delete_failure_rolls_back_all_deleted_rows(self):
        before = self.dump()
        original_connect = admin._connect
        with patch.object(admin, '_connect', side_effect=lambda: PartialDeleteFailure(original_connect())), \
                patch.object(admin.traceback, 'print_exc'):
            self.assertEqual(self.request('/player/repair-puzzles', {'uin': 101})[0], 500)
        self.assertEqual(self.dump(), before)
        self.assertEqual(len(self.artifacts()), 2)

    def test_invalid_csrf_cannot_clean_or_create_a_backup(self):
        before = self.dump()
        for fields, csrf in (({'uin': 101}, False), ({'uin': 101, 'csrf': 'wrong'}, False)):
            with self.subTest(fields=fields):
                self.assert_result(self.request('/player/repair-puzzles', fields, csrf), 'error')
                self.assertEqual(self.dump(), before)
                self.assertEqual(self.artifacts(), [])

    def test_game_port_blocks_cleanup(self):
        before = self.dump()
        self.port_mock.return_value = True
        self.assert_result(self.request('/player/repair-puzzles', {'uin': 101}), 'error')
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.artifacts(), [])

    def test_launcher_transition_blocks_cleanup_without_game_port(self):
        before = self.dump()
        self.guard_mock.return_value = True
        self.assert_result(self.request('/player/repair-puzzles', {'uin': 101}), 'error')
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.artifacts(), [])

    def test_transition_that_starts_during_backup_blocks_delete(self):
        before = self.dump()
        calls = []

        def transition_state():
            calls.append(True)
            return len(calls) >= 3

        self.guard_mock.side_effect = transition_state
        self.assert_result(self.request('/player/repair-puzzles', {'uin': 101}), 'error')
        self.assertEqual(self.dump(), before)
        self.assertEqual(len(self.artifacts()), 2)

    def test_invalid_or_nonexistent_player_never_creates_or_cleans(self):
        before = self.dump()
        for uin in ('0', '4294967296', '9999'):
            with self.subTest(uin=uin):
                self.assert_result(self.request('/player/repair-puzzles', {'uin': uin}), 'error')
                self.assertEqual(self.dump(), before)
                self.assertEqual(self.artifacts(), [])
        self.assertIsNone(admin._player(9999))

    def test_embedded_theme_and_inventory_tab_survive_redirect(self):
        path = '/player/repair-puzzles?embedded=1&theme=dark&section=inventory'
        query = self.assert_result(self.request(path, {'uin': 101}))
        self.assertEqual(query['embedded'], ['1'])
        self.assertEqual(query['theme'], ['dark'])
        self.assertEqual(query['section'], ['inventory'])
        self.assertEqual(query['uin'], ['101'])

    def test_future_recipe_reference_protects_a_previously_invalid_id(self):
        references = admin.PUZZLE_REFERENCED_ITEMS | {67813}
        with patch.object(admin, 'PUZZLE_REFERENCED_ITEMS', references):
            self.assertIsNone(admin._invalid_puzzle_details(67813))
            self.assert_result(self.request('/player/repair-puzzles', {'uin': 101}))
        self.assertEqual(store.get_item_count(101, 67813), 999)
        for iid in INVALID_IDS[1:]:
            self.assertEqual(store.get_item_count(101, iid), 0)

    def test_future_item_definition_protects_a_previously_invalid_id(self):
        with patch.dict(admin.ITEM_NAMES, {67813: '新配置拼图'}):
            self.assertIsNone(admin._invalid_puzzle_details(67813))
            self.assert_result(self.request('/player/repair-puzzles', {'uin': 101}))
        self.assertEqual(store.get_item_count(101, 67813), 999)

    def test_cumulative_prestige_limit_rejects_both_entry_points_without_cache_change(self):
        self.assert_result(self.request('/player/grant-item',
                                        {'uin': 101, 'item_id': 43, 'quantity': 2_000_000_000}))
        for path, field in (('/player/grant-item', 'quantity'), ('/player/item', 'delta')):
            with self.subTest(path=path):
                self.assert_result(self.request(path, {'uin': 101, 'item_id': 43, field: 1}), 'error')
                self.assertEqual(store.get_item_count(101, 43), 2_000_000_000)
                self.assertEqual(admin._player(101)['prestige'], 2_000_000_000)

    def test_negative_delta_can_repair_an_existing_overlimit_prestige_in_steps(self):
        store.add_item(101, 43, 6_000_000_000)
        store.save_player(101, prestige=6_000_000_000)
        for expected in (4_000_000_000, 2_000_000_000, 0):
            self.assert_result(self.request('/player/item',
                                            {'uin': 101, 'item_id': 43, 'delta': -2_000_000_000}))
            self.assertEqual(store.get_item_count(101, 43), expected)
            self.assertEqual(admin._player(101)['prestige'], expected)


if __name__ == '__main__':
    unittest.main()
