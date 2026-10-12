"""NPC login visibility tests use only generated temporary saves, never real data."""
import hashlib
import http.client
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[2]
for path in (SOURCE, SOURCE / "PvZ-Server-PoC"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import launcher as legacy
import launcher_desktop as desktop
import launcher_service as service_module
from launcher_service import LauncherService
import player_store as store
import profile_editor


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NpcVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="npc-test-")
        self.root = Path(self.temp.name)
        self.db = self.root / "players.db"
        self.config = self.root / "launcher_config.json"
        self.original_db = store.DB_PATH
        store.set_db_path(str(self.db))
        self.patches = [
            patch.object(legacy, "CONFIG_PATH", str(self.config)),
            patch.object(legacy, "port_open", return_value=False),
            patch.object(profile_editor, "avatar_choices", return_value=[]),
        ]
        for item in self.patches:
            item.start()
        store.init_db()
        for number in (1, 700000204, 899999, 900000, 900001, 999999, 1000000):
            store.create_new_player(number)
        store.close_thread_db()
        self.service = service_module.LauncherService.__new__(service_module.LauncherService)
        self.service.db_path = self.db
        self.service.root = self.root
        self.service.lock = threading.RLock()
        self.service.running = self.service.busy = self.service.closed = False
        self.service.server = self.service.game = None
        self.service.config = {"last_uin": "001", "recent": ["001", "700000204"]}
        self.service.active_account = "001"
        self.service.log = Mock()
        self.config.write_text(json.dumps(self.service.config), encoding="utf-8")

    def tearDown(self):
        store.set_db_path(self.original_db)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_reserved_range_and_leading_zeroes(self):
        for value in ("900000", "900001", "999999", "0000900000", "0000999999"):
            self.assertTrue(legacy.valid_uin(value))
            self.assertFalse(legacy.valid_player_uin(value))
        for value in ("001", "700000204", "899999", "1000000", "0001000000", "4294967295"):
            self.assertTrue(legacy.valid_player_uin(value))
        for value in ("", "0", "4294967296", "１２", "1&uin=2", None, True):
            self.assertFalse(legacy.valid_player_uin(value))

    def test_account_list_and_search_hide_npcs_without_writing_save(self):
        before = digest(self.db)
        self.assertEqual({row["uin"] for row in self.service.accounts()},
                         {"1", "700000204", "899999", "1000000"})
        self.assertEqual(self.service.accounts("900001"), [])
        self.assertEqual(self.service.accounts("999999"), [])
        self.assertEqual([row["uin"] for row in self.service.accounts("700000204")], ["700000204"])
        self.assertEqual(digest(self.db), before)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM players").fetchone()[0], 7)

    def test_service_direct_reserved_selection_cannot_write_or_spawn(self):
        before = digest(self.db)
        config = self.config.read_bytes()
        with patch.object(self.service, "_spawn") as spawn:
            for value in ("900000", "900001", "999999", "0000900000", "0000999999"):
                for method in (self.service.start, self.service.create_account, self.service.account):
                    with self.subTest(value=value, method=method.__name__):
                        with self.assertRaisesRegex(ValueError, "系统人机"):
                            method(value)
            spawn.assert_not_called()
        self.assertEqual(digest(self.db), before)
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual(self.service.active_account, "001")

    def test_reserved_creation_does_not_initialize_missing_database(self):
        self.service.db_path = self.root / "missing.db"
        store.set_db_path(str(self.service.db_path))
        with patch.object(store, "init_db") as init:
            with self.assertRaisesRegex(ValueError, "系统人机"):
                self.service.create_account("0000900000")
            init.assert_not_called()
        self.assertFalse(self.service.db_path.exists())

    def test_ordinary_accounts_stay_readable_and_new_accounts_can_be_created(self):
        self.assertEqual(self.service.account("001")["uin"], "1")
        self.assertEqual(self.service.account("1000000")["uin"], "1000000")
        result = self.service.create_account("700000205")
        self.assertEqual(result["uin"], "700000205")
        self.assertEqual(self.service.active_account, "700000205")
        store.save_player(700000205, coin=424242)
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.service.create_account("0700000205")
        self.assertEqual(self.service.account("700000205")["coin"], 424242)

    def test_config_filter_preserves_001_spelling_and_empty_selection(self):
        self.config.write_text(json.dumps({"last_uin": "0000900041", "recent":
            ["900000", "700000204", "001", "0000999999", "001", "1000000"]}), encoding="utf-8")
        self.assertEqual(legacy.load_config(), {"last_uin": "001", "recent": ["700000204", "001", "1000000"]})
        self.config.write_text(json.dumps({"last_uin": "001", "recent": ["001", "900001"]}), encoding="utf-8")
        self.assertEqual(legacy.load_config(), {"last_uin": "001", "recent": ["001"]})
        self.config.write_text(json.dumps({"last_uin": "", "recent": []}), encoding="utf-8")
        self.assertEqual(legacy.load_config(), {"last_uin": "", "recent": []})
        self.assertIn("&uin=001&", legacy.game_url("001"))

    def make_tk(self, number):
        app = legacy.Launcher.__new__(legacy.Launcher)
        app.uin_var = Mock()
        app.uin_var.get.return_value = number
        app.uin_box = Mock()
        app._log = Mock()
        app.running = app._closed = False
        app._can_start = True
        app.server = app.game = None
        return app

    def test_old_tk_entry_points_reject_reserved_accounts_before_mutation(self):
        before = digest(self.db)
        config = self.config.read_bytes()
        with patch.object(legacy.messagebox, "showwarning") as warning, \
             patch.object(legacy.subprocess, "Popen") as spawn, \
             patch.object(store, "init_db") as init:
            for value in ("900000", "999999", "0000900000"):
                app = self.make_tk(value)
                with self.assertRaisesRegex(ValueError, "系统人机"):
                    app._create_account(value)
                app.start()
                app.open_profile_editor()
                app.open_save_editor()
            self.assertEqual(warning.call_count, 9)
            spawn.assert_not_called()
            init.assert_not_called()
        self.assertEqual(digest(self.db), before)
        self.assertEqual(self.config.read_bytes(), config)

    def test_old_tk_ordinary_creation_is_unchanged(self):
        app = self.make_tk("001")
        self.assertEqual(app._create_account(" 700000205 "), "700000205")
        app.uin_var.set.assert_called_once_with("700000205")
        self.assertEqual(legacy.load_config()["recent"], ["700000205", "001", "700000204"])
        self.assertEqual(self.service.account("700000205")["uin"], "700000205")


# Optional NPC visibility still keeps existing login and mutation safeguards.
class RobotAccountFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='launcher-account-visibility-')
        self.root = Path(self.temporary.name)
        self.db_path = self.root / 'players.db'
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.execute('''CREATE TABLE players (
                uin INTEGER PRIMARY KEY, name TEXT, signature TEXT,
                face_id INTEGER, qq_face_url TEXT, level INTEGER, coin INTEGER,
                sun INTEGER, vip_level INTEGER, last_login INTEGER)''')
            con.executemany('INSERT INTO players VALUES (?,?,?,?,?,?,?,?,?,?)', [
                (899999, 'PlayerBefore', '', 0, '', 12, 500, 0, 0, 100),
                (900000, 'ReservedStart', '', 1, '', 30, 500, 0, 0, 200),
                (900001, 'RobotA', '', 2, '', 40, 500, 0, 0, 300),
                (999999, 'ReservedEnd', '', 3, '', 50, 500, 0, 0, 400),
                (1000000, 'PlayerAfter', '', 4, '', 60, 500, 0, 0, 500),
            ])
        self.original_database = self.db_path.read_bytes()
        # Listing needs no lifecycle initialization or real project paths.
        self.service = LauncherService.__new__(LauncherService)
        self.service.db_path = self.db_path
        self.service.lock = threading.RLock()
        self.service.closed = self.service.running = self.service.busy = False

    def tearDown(self):
        self.assertEqual(self.original_database, self.db_path.read_bytes(),
                         'Visibility/read-only requests must preserve the fixture database.')
        self.temporary.cleanup()


class ServiceVisibilityTests(RobotAccountFixture):
    def test_default_list_hides_the_entire_reserved_range(self):
        result = self.service.accounts()
        self.assertEqual([row['uin'] for row in result], ['1000000', '899999'])
        self.assertTrue(all(row['is_robot'] is False for row in result))

    def test_opt_in_includes_robots_with_explicit_metadata(self):
        result = self.service.accounts(include_robots=True)
        self.assertEqual([row['uin'] for row in result],
                         ['1000000', '999999', '900001', '900000', '899999'])
        self.assertEqual([row['is_robot'] for row in result],
                         [False, True, True, True, False])
        self.assertTrue(all(row['id'] == row['uin'] for row in result))

    def test_search_respects_visibility_and_can_find_visible_robot(self):
        self.assertEqual(self.service.accounts('robota'), [])
        self.assertEqual(self.service.accounts('900001'), [])
        result = self.service.accounts('RoBoTa', include_robots=True)
        self.assertEqual([row['uin'] for row in result], ['900001'])
        self.assertEqual(self.service.accounts('player')[0]['uin'], '1000000')
        self.assertEqual(self.service.accounts('900001', include_robots=True)[0]['uin'], '900001')

    def test_showing_robots_does_not_remove_login_or_mutation_protection(self):
        self.service.accounts(include_robots=True)
        callbacks = (
            lambda: self.service.account('900001'),
            lambda: self.service.start('900001'),
            lambda: self.service.create_account('900001'),
            lambda: self.service.save_profile('900001', {'name': 'Changed'}),
            lambda: self.service.delete_account('900001', '900001'),
        )
        with patch.object(legacy, 'port_open', return_value=False):
            for callback in callbacks:
                with self.subTest(callback=callback):
                    with self.assertRaisesRegex(ValueError, '系统人机'):
                        callback()

    def test_missing_database_stays_missing(self):
        missing = self.root / 'missing.db'
        self.service.db_path = missing
        self.assertEqual(self.service.accounts(include_robots=True), [])
        self.assertFalse(missing.exists())


class HTTPVisibilityTests(RobotAccountFixture):
    def setUp(self):
        super().setUp()
        self.api = desktop.LauncherHTTPServer(self.service)
        self.thread = threading.Thread(target=self.api.serve_forever,
                                       kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.api.shutdown()
        self.api.server_close()
        self.thread.join(2)
        super().tearDown()

    def request(self, path, *, authenticated=True):
        headers = {'Host': '127.0.0.1:%d' % self.api.server_address[1]}
        if authenticated:
            headers['X-PVZ-Token'] = self.api.token
        connection = http.client.HTTPConnection(*self.api.server_address, timeout=3)
        try:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_endpoint_defaults_to_hidden_and_supports_opt_in(self):
        status, body = self.request('/api/accounts')
        self.assertEqual(status, 200)
        self.assertEqual([row['uin'] for row in body['data']], ['1000000', '899999'])
        status, body = self.request('/api/accounts?include_robots=1')
        self.assertEqual(status, 200)
        self.assertEqual(sum(row['is_robot'] for row in body['data']), 3)

    def test_endpoint_requires_explicit_one_to_enable_robots(self):
        for value in ('0', '', 'false', 'true', 'off', 'unexpected'):
            with self.subTest(value=value):
                status, body = self.request('/api/accounts?include_robots=' + value)
                self.assertEqual(status, 200)
                self.assertTrue(all(row['is_robot'] is False for row in body['data']))

    def test_robot_search_requires_opt_in(self):
        self.assertEqual(self.request('/api/accounts?q=900001')[1]['data'], [])
        status, body = self.request('/api/accounts?q=900001&include_robots=1')
        self.assertEqual(status, 200)
        self.assertEqual(body['data'][0]['uin'], '900001')
        self.assertIs(body['data'][0]['is_robot'], True)

    def test_opt_in_keeps_session_authentication(self):
        status, body = self.request('/api/accounts?include_robots=1', authenticated=False)
        self.assertEqual(status, 403)
        self.assertFalse(body['ok'])


if __name__ == "__main__":
    unittest.main()
