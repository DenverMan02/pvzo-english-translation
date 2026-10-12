"""Startup migrations must be serialized and preserve existing saves."""
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import player_store as store


class StartupMigrationTests(unittest.TestCase):
    def setUp(self):
        self.original_path = store.DB_PATH
        self.temp = tempfile.TemporaryDirectory(prefix='pvz_startup_')
        self.path = os.path.join(self.temp.name, 'players.db')
        store.set_db_path(self.path)

    def tearDown(self):
        store.set_db_path(self.original_path)
        self.temp.cleanup()

    def _legacy_save(self):
        # A real update introduced these columns; two initializers must migrate
        # them without duplicate-column errors or changes to player currencies.
        missing = {'card_market_friend_day', 'card_market_friend_free_used',
                   'card_market_friend_next_free_at'}
        cols = ', '.join('%s %s' % (key, 'TEXT' if isinstance(value, str) else 'INTEGER')
                         for key, value in store.DEFAULTS.items() if key not in missing)
        with closing(sqlite3.connect(self.path)) as con:
            con.execute('CREATE TABLE players (uin INTEGER PRIMARY KEY, %s)' % cols)
            con.execute('INSERT INTO players (uin, name, coin, ticket) VALUES (?, ?, ?, ?)',
                        (700000204, 'Existing save', 87654321, 9876))
            con.commit()

    def test_concurrent_initializers_upgrade_same_save_once(self):
        self._legacy_save()
        first_snapshot = threading.Event()
        second_snapshot = threading.Event()
        gate_lock = threading.Lock()
        snapshots = []
        failures = []
        results = []

        class GatedConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                result = super().execute(sql, *args, **kwargs)
                if sql == 'PRAGMA table_info(players)':
                    rows = list(result)
                    with gate_lock:
                        snapshots.append({row['name'] for row in rows})
                        first = len(snapshots) == 1
                    if first:
                        first_snapshot.set()
                        # On the old implementation the second thread takes a
                        # stale snapshot while this one waits. BEGIN IMMEDIATE
                        # now blocks that thread before it reads the schema.
                        second_snapshot.wait(.3)
                    else:
                        second_snapshot.set()
                    return rows
                return result

        def connect():
            con = sqlite3.connect(store.DB_PATH, timeout=5, factory=GatedConnection)
            con.row_factory = sqlite3.Row
            return con

        def initialize():
            try:
                results.append(store.init_db())
            except Exception as exc:
                failures.append(exc)
            finally:
                store.close_thread_db()

        with patch.object(store, '_connect', connect):
            first = threading.Thread(target=initialize)
            second = threading.Thread(target=initialize)
            first.start()
            self.assertTrue(first_snapshot.wait(5), 'first migration did not inspect the schema')
            second.start()
            first.join(10)
            second.join(10)
            self.assertFalse(first.is_alive() or second.is_alive(), 'migration worker hung')

        self.assertEqual(failures, [], [repr(exc) for exc in failures])
        self.assertEqual(results, [self.path, self.path])
        self.assertEqual(len(snapshots), 2)
        self.assertIn('card_market_friend_day', snapshots[1])
        with closing(sqlite3.connect(self.path)) as con:
            con.row_factory = sqlite3.Row
            row = con.execute('SELECT * FROM players WHERE uin=700000204').fetchone()
            self.assertEqual((row['name'], row['coin'], row['ticket']),
                             ('Existing save', 87654321, 9876))
            self.assertEqual((row['card_market_friend_day'], row['card_market_friend_free_used'],
                              row['card_market_friend_next_free_at']), ('', 0, 0))
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_failed_migration_rolls_back_and_surfaces_actual_error(self):
        self._legacy_save()
        with patch.object(store, '_ensure_columns',
                          side_effect=sqlite3.OperationalError('migration failure')):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'migration failure'):
                store.init_db()
        with closing(sqlite3.connect(self.path)) as con:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {'players'})
            self.assertEqual(con.execute('SELECT coin, ticket FROM players').fetchone(), (87654321, 9876))
        # A failed migration releases its writer so a normal retry can finish.
        self.assertEqual(store.init_db(), self.path)


if __name__ == '__main__':
    unittest.main()
