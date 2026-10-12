"""Isolated regression coverage for the integrated desktop launcher.

All saves, assets, configuration and logs belong to a temporary workspace tree.
Real game/Flash processes and the original D: project are never launched.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import gc
import http.client
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


PROJECT = Path(__file__).resolve().parents[2]
SERVER_SOURCE = PROJECT / 'PvZ-Server-PoC'
for path in (PROJECT, SERVER_SOURCE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import launcher as legacy
import launcher_desktop as desktop
import launcher_service
import player_store as store
import profile_editor
from account_management import get_account


class FakeProcess:
    def __init__(self, text='fake process log\n'):
        self.code = None
        self.stdout = io.StringIO(text)
        self.terminated = self.killed = 0
        self.wait_calls = []

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated += 1
        self.code = 0

    def kill(self):
        self.killed += 1
        self.code = -9

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return self.code


class IsolatedLauncherTests(unittest.TestCase):
    def setUp(self):
        qa_root = PROJECT.parent / 'qa'
        qa_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='desktop-test-', dir=qa_root)
        self.root = Path(self.temporary.name)
        self.server_dir = self.root / 'PvZ-Server-PoC'
        self.server_dir.mkdir()
        (self.server_dir / 'run_all.py').write_text('# fixture only\n', encoding='utf-8')
        (self.server_dir / 'admin_server.py').write_text('# fixture only\n', encoding='utf-8')
        (self.root / 'webroot' / 'image' / 'head').mkdir(parents=True)
        (self.root / 'webroot' / 'loader_p.swf').write_bytes(b'CWS fixture')
        (self.root / 'webroot' / 'image' / 'head' / '0.png').write_bytes(b'fixture-avatar')
        (self.root / 'launcher_ui').mkdir()
        (self.root / 'launcher_ui' / 'index.html').write_text('<!doctype html><title>Fixture</title>', encoding='utf-8')
        (self.root / 'launcher_ui' / 'app.js').write_text('window.fixture=true;', encoding='utf-8')
        (self.root / 'secret.txt').write_text('private fixture', encoding='utf-8')
        self.patches = []
        self.original_db_path = store.DB_PATH
        store.close_thread_db()
        store.DB_PATH = str(self.server_dir / 'players.db')
        for name, value in {
            'HERE': str(self.root), 'SERVER_DIR': str(self.server_dir),
            'RUN_ALL': str(self.server_dir / 'run_all.py'),
            'CONFIG_PATH': str(self.root / 'launcher_config.json'),
            'LAUNCHER_LOG': str(self.root / 'portable_launcher.log'),
        }.items():
            self.patch(legacy, name, value)
        self.patch(legacy, 'port_open', return_value=False)
        self.patch(legacy, 'resolve_game_player', return_value=('flash', 'fixture-flash.exe'))
        self.patch(profile_editor, 'GAME_ROOT', self.root)
        store.init_db()
        self.service = launcher_service.LauncherService(self.root, launch_player=False)

    def patch(self, target, name, value=None, **kwargs):
        item = patch.object(target, name, value, **kwargs) if not kwargs else patch.object(target, name, **kwargs)
        result = item.start()
        self.patches.append(item)
        return result

    def tearDown(self):
        try:
            self.service.close()
            store.close_thread_db()
            store.DB_PATH = self.original_db_path
            temporary_server = str(self.server_dir)
            if temporary_server in sys.path:
                sys.path.remove(temporary_server)
            for item in reversed(self.patches):
                item.stop()
        finally:
            gc.collect()
            self.temporary.cleanup()

    def create(self, number=101):
        return self.service.create_account(str(number))

    def save_coin(self, number, coin):
        store.save_player(number, coin=coin)

    def assert_mutation_rejected(self, callback):
        before = (self.server_dir / 'players.db').read_bytes()
        with self.assertRaisesRegex(ValueError, '停止'):
            callback()
        self.assertEqual(before, (self.server_dir / 'players.db').read_bytes())


class AccountAndBackupTests(IsolatedLauncherTests):
    def test_account_create_normalizes_number_and_duplicate_preserves_save(self):
        account = self.service.create_account('000101')
        self.assertEqual(account['id'], '101')
        self.save_coin(101, 4242)
        with self.assertRaisesRegex(ValueError, '已存在'):
            self.service.create_account('101')
        self.assertEqual(get_account(101)['coin'], 4242)
        self.assertEqual(len(self.service.accounts()), 1)

    def test_invalid_account_values_do_not_write_saves(self):
        for number in ('', '0', '4294967296', '１２', '1&uin=2', True, None):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    self.service.create_account(number)
        self.assertEqual(self.service.accounts(), [])

    def test_profile_incremental_update_preserves_unrelated_fields(self):
        account = self.create()
        self.save_coin(101, 999)
        result = self.service.save_profile('101', {
            'name': '豌豆勇士', 'profile_expected': account['profile_expected'],
        })
        self.assertEqual(result['name'], '豌豆勇士')
        self.assertEqual(result['coin'], 999)
        self.assertEqual(result['signature'], account['signature'])

    def test_stale_profile_snapshot_cannot_overwrite_newer_edit(self):
        account = self.create()
        store.save_player(101, signature='新的签名')
        with self.assertRaisesRegex(ValueError, '其他操作修改'):
            self.service.save_profile('101', {
                'signature': '旧页面提交', 'profile_expected': account['profile_expected'],
            })
        self.assertEqual(get_account(101)['signature'], '新的签名')

    def test_profile_patch_for_missing_account_does_not_create_it(self):
        with self.assertRaises(ValueError):
            self.service.save_profile('999', {'name': '无效角色', 'profile_expected': {}})
        self.assertIsNone(get_account(999))

    def test_delete_confirmation_mismatch_creates_no_backup_or_deletion(self):
        self.create()
        with self.assertRaisesRegex(ValueError, '不匹配'):
            self.service.delete_account('101', '102')
        self.assertIsNotNone(get_account(101))
        self.assertEqual(self.service.backups(), [])

    def test_delete_creates_recoverable_snapshot_before_removing_account(self):
        self.create()
        self.save_coin(101, 4567)
        result = self.service.delete_account('101', '000101')
        self.assertIsNone(get_account(101))
        self.assertEqual(result['deleted'], '101')
        snapshot = self.service.backup_dir / result['backup'] / 'players.db'
        with closing(sqlite3.connect(snapshot)) as con:
            self.assertEqual(con.execute('SELECT coin FROM players WHERE uin=101').fetchone()[0], 4567)
        self.assertNotIn('101', self.service.config.get('recent', []))

    def test_backup_covers_player_and_guild_data_and_records_hashes(self):
        self.create()
        guild_path = self.server_dir / 'guild_local.db'
        with closing(sqlite3.connect(guild_path)) as con:
            con.execute('CREATE TABLE guild_test (value TEXT)')
            con.execute("INSERT INTO guild_test VALUES ('snapshot guild')")
            con.commit()
        snapshot = self.service.create_backup('qa manual')
        self.assertEqual({row['name'] for row in snapshot['files']}, {'players.db', 'guild_local.db'})
        for row in snapshot['files']:
            content = (self.service.backup_dir / snapshot['id'] / row['name']).read_bytes()
            self.assertEqual(row['sha256'], hashlib.sha256(content).hexdigest())
            self.assertEqual(row['size'], len(content))
        self.assertEqual(self.service.backups()[0]['id'], snapshot['id'])

    def test_tampered_backup_is_rejected_without_current_save_changes(self):
        self.create()
        snapshot = self.service.create_backup()
        self.save_coin(101, 5678)
        saved = self.service.backup_dir / snapshot['id'] / 'players.db'
        saved.write_bytes(saved.read_bytes() + b'tamper')
        before = (self.server_dir / 'players.db').read_bytes()
        with self.assertRaisesRegex(ValueError, '校验失败'):
            self.service.restore_backup(snapshot['id'], True)
        self.assertEqual(before, (self.server_dir / 'players.db').read_bytes())
        self.assertEqual(get_account(101)['coin'], 5678)
        self.assertEqual(len(self.service.backups()), 1)

    def test_restore_requires_confirmation_and_safe_snapshot_id(self):
        self.create()
        snapshot = self.service.create_backup()
        for identifier, confirmation in ((snapshot['id'], False), (snapshot['id'], 1), ('../private', True)):
            with self.subTest(identifier=identifier, confirmation=confirmation):
                with self.assertRaises(ValueError):
                    self.service.restore_backup(identifier, confirmation)

    def test_restore_creates_snapshot_of_current_state_before_overwrite(self):
        self.create()
        self.save_coin(101, 10)
        snapshot = self.service.create_backup()
        self.save_coin(101, 20)
        admin = FakeProcess()
        self.service.admin = admin
        result = self.service.restore_backup(snapshot['id'], True)
        self.assertEqual(get_account(101)['coin'], 10)
        self.assertEqual(admin.terminated, 1)
        self.assertIsNone(self.service.admin)
        self.assertNotEqual(result['backup'], snapshot['id'])
        safeguard = self.service.backup_dir / result['backup'] / 'players.db'
        with closing(sqlite3.connect(safeguard)) as con:
            self.assertEqual(con.execute('SELECT coin FROM players WHERE uin=101').fetchone()[0], 20)
        self.assertIn('before-restore', [row['reason'] for row in self.service.backups()])

    def test_backup_and_restore_explicitly_release_sqlite_handles(self):
        self.create()
        connections = []
        real_connect = sqlite3.connect

        class TrackedConnection(sqlite3.Connection):
            explicitly_closed = False

            def close(self):
                self.explicitly_closed = True
                return super().close()

        def connect(*args, **kwargs):
            kwargs['factory'] = TrackedConnection
            connection = real_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        try:
            with patch.object(sqlite3, 'connect', side_effect=connect):
                snapshot = self.service.create_backup()
                self.service.restore_backup(snapshot['id'], True)
            self.assertTrue(connections)
            self.assertTrue(all(connection.explicitly_closed for connection in connections),
                            'Backup/restore must explicitly close every SQLite connection on Windows.')
        finally:
            for connection in connections:
                connection.close()

    def test_second_database_restore_failure_preserves_both_current_databases(self):
        self.create()
        self.save_coin(101, 10)
        guild = self.server_dir / 'guild_local.db'
        with closing(sqlite3.connect(guild)) as con:
            con.execute('CREATE TABLE guild_test (value TEXT)')
            con.execute("INSERT INTO guild_test VALUES ('old guild')")
            con.commit()
        snapshot = self.service.create_backup()
        self.save_coin(101, 20)
        with closing(sqlite3.connect(guild)) as con:
            con.execute("UPDATE guild_test SET value='current guild'")
            con.commit()
        real_connect = sqlite3.connect
        fault = {'raised': False}

        class FailingBackupConnection(sqlite3.Connection):
            def __init__(self, database, *args, **kwargs):
                self.fixture_database = str(database)
                super().__init__(database, *args, **kwargs)

            def backup(self, target, *args, **kwargs):
                result = super().backup(target, *args, **kwargs)
                if (not fault['raised'] and snapshot['id'] in self.fixture_database
                        and 'guild_local.db' in self.fixture_database):
                    fault['raised'] = True
                    raise sqlite3.OperationalError('fixture second-database write failure')
                return result

        def connect(*args, **kwargs):
            kwargs['factory'] = FailingBackupConnection
            return real_connect(*args, **kwargs)

        with patch.object(sqlite3, 'connect', side_effect=connect):
            with self.assertRaises(Exception):
                self.service.restore_backup(snapshot['id'], True)
        self.assertTrue(fault['raised'], 'The test must reach the second database restore.')
        self.assertEqual(get_account(101)['coin'], 20)
        with closing(sqlite3.connect(guild)) as con:
            self.assertEqual(con.execute('SELECT value FROM guild_test').fetchone()[0], 'current guild')
        self.assertIn('before-restore', [row['reason'] for row in self.service.backups()])

    def test_all_save_mutations_are_rejected_during_owned_or_external_game(self):
        account = self.create()
        snapshot = self.service.create_backup()
        actions = (
            lambda: self.service.create_account('102'),
            lambda: self.service.save_profile('101', {'name': '拒绝修改', 'profile_expected': account['profile_expected']}),
            lambda: self.service.delete_account('101', '101'),
            lambda: self.service.create_backup(),
            lambda: self.service.restore_backup(snapshot['id'], True),
        )
        for state in ('running', 'busy', 'external'):
            self.service.running = state == 'running'
            self.service.busy = state == 'busy'
            with patch.object(legacy, 'port_open', return_value=state == 'external'):
                for callback in actions:
                    with self.subTest(state=state, callback=callback):
                        self.assert_mutation_rejected(callback)
        self.service.running = self.service.busy = False


class DesktopAPITests(IsolatedLauncherTests):
    def setUp(self):
        super().setUp()
        self.api = desktop.LauncherHTTPServer(self.service)
        self.thread = threading.Thread(target=self.api.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.api.shutdown()
        self.api.server_close()
        self.thread.join(2)
        super().tearDown()

    def request(self, path, method='GET', data=None, headers=None, token=True):
        all_headers = {'Host': '127.0.0.1:%s' % self.api.server_address[1]}
        if token:
            all_headers['X-PVZ-Token'] = self.api.token
        if data is not None:
            data = json.dumps(data).encode('utf-8')
            all_headers['Content-Type'] = 'application/json'
        all_headers.update(headers or {})
        connection = http.client.HTTPConnection(*self.api.server_address, timeout=3)
        try:
            connection.request(method, path, data, all_headers)
            response = connection.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def test_api_requires_header_token_and_rejects_cookie_as_substitute(self):
        for headers in ({}, {'X-PVZ-Token': 'wrong'}, {'Cookie': 'PVZLauncherToken=' + self.api.token}):
            with self.subTest(headers=headers):
                status, _, body = self.request('/api/accounts', token=False, headers=headers)
                self.assertEqual(status, 403)
                self.assertFalse(json.loads(body)['ok'])
        self.assertEqual(self.request('/api/accounts')[0], 200)

    def test_wrong_host_or_cross_origin_is_rejected_despite_valid_token(self):
        for headers in ({'Host': 'evil.example'}, {'Host': 'localhost:%d' % self.api.server_address[1]}, {'Origin': 'http://evil.example'}, {'Origin': 'null'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/accounts', headers=headers)[0], 403)
        self.assertEqual(self.request('/api/accounts', headers={'Origin': self.api.origin})[0], 200)

    def test_token_failure_cannot_mutate_account(self):
        status, _, _ = self.request('/api/accounts', 'POST', {'uin': '123'}, token=False)
        self.assertEqual(status, 403)
        self.assertIsNone(get_account(123))

    def test_static_and_avatar_path_traversal_cannot_escape_allowed_directory(self):
        for path in ('/../secret.txt', '/%2e%2e/secret.txt', '/..%5csecret.txt', '/avatar/%2e%2e/%2e%2e/%2e%2e/secret.txt'):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b'private fixture', body)

    def test_ui_cache_is_not_served_as_static_asset(self):
        cache = self.root / 'launcher_ui' / 'cache'
        cache.mkdir()
        (cache / 'private.db').write_bytes(b'private browser data')
        status, _, body = self.request('/cache/private.db', token=False)
        self.assertEqual(status, 404)
        self.assertNotIn(b'private browser data', body)

    def test_page_sets_http_only_cookie_and_avatar_accepts_it(self):
        status, headers, _ = self.request('/?token=' + self.api.token, token=False)
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.assertEqual(self.request('/avatar/0.png', token=False)[0], 403)
        self.assertEqual(self.request('/avatar/0.png', token=False, headers={'Cookie': headers['Set-Cookie'].split(';')[0]})[0], 200)

    def test_json_body_limits_and_shape_fail_without_create(self):
        cases = (
            (b'{}', {'Content-Type': 'text/plain'}),
            (b'[]', {'Content-Type': 'application/json'}),
            (b'{broken', {'Content-Type': 'application/json'}),
            (b'', {'Content-Type': 'application/json', 'Content-Length': '8388609'}),
        )
        for body, extra in cases:
            connection = http.client.HTTPConnection(*self.api.server_address, timeout=3)
            try:
                headers = {'X-PVZ-Token': self.api.token, **extra}
                connection.request('POST', '/api/accounts', body, headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
            finally:
                connection.close()
        self.assertEqual(self.service.accounts(), [])


class DesktopLifecycleTests(IsolatedLauncherTests):
    def start_fake(self, process=None):
        process = process or FakeProcess()
        with patch.object(self.service, '_spawn', return_value=process), patch.object(launcher_service.threading, 'Thread'):
            self.service.start('101')
        return process

    def test_stale_readiness_and_exit_cannot_stop_replacement_session(self):
        old = self.start_fake()
        generation = self.service.session
        cancel = self.service.cancel
        self.service.stop()
        new = self.start_fake()
        self.service._wait_ready(old, generation, 'loader_p.swf', cancel)
        old.code = 1
        self.service._read_process(old, generation, 'server')
        self.assertIs(self.service.server, new)
        self.assertTrue(self.service.running)
        self.assertEqual(new.terminated, 0)
        self.assertNotIn('游戏服务已退出', self.service.status)

    def test_stop_terminates_only_owned_handles(self):
        owned = [FakeProcess() for _ in range(3)]
        unrelated = FakeProcess()
        self.service.server, self.service.game, self.service.admin = owned
        self.service.running = True
        generation = self.service.session
        result = self.service.stop()
        self.assertEqual([process.terminated for process in owned], [1, 1, 1])
        self.assertEqual(unrelated.terminated, 0)
        self.assertEqual(self.service.session, generation + 1)
        self.assertTrue(self.service.cancel.is_set())
        self.assertEqual(result['status'], '服务已停止')
        self.assertFalse(self.service.running)

    def test_failed_stop_keeps_handle_and_blocks_restart(self):
        process = self.start_fake()
        process.terminate = Mock(side_effect=OSError('fixture denied'))
        with self.assertRaisesRegex(ValueError, '未停止'):
            self.service.stop()
        self.assertIs(self.service.server, process)
        self.assertTrue(self.service.running)
        with patch.object(self.service, '_spawn') as spawn:
            with self.assertRaises(ValueError):
                self.service.start('102')
        spawn.assert_not_called()
        process.code = 0

    def test_terminate_escalates_only_after_graceful_timeout(self):
        process = FakeProcess()
        process.terminate = Mock()
        process.wait = Mock(side_effect=[subprocess.TimeoutExpired('fixture', 2), 0])
        self.assertTrue(self.service._terminate(process, 'fixture'))
        self.assertEqual(process.killed, 1)
        self.assertEqual(process.wait.call_count, 2)

    def test_readiness_timeout_cleans_own_process_without_player_spawn(self):
        process = self.start_fake()
        with patch.object(legacy, 'START_TIMEOUT', 0), patch.object(launcher_service.subprocess, 'Popen') as player_spawn:
            self.service._wait_ready(process, self.service.session, 'loader_p.swf', self.service.cancel)
        self.assertEqual(process.terminated, 1)
        self.assertIsNone(self.service.server)
        self.assertFalse(self.service.running)
        self.assertIn('超时', self.service.error)
        player_spawn.assert_not_called()

    def test_server_exit_cleans_owned_game_and_prevents_old_reader_logs(self):
        server = self.start_fake()
        game = FakeProcess()
        self.service.game = game
        server.code = 1
        self.service._read_process(server, self.service.session, 'server')
        self.assertEqual(game.terminated, 1)
        self.assertFalse(self.service.running)
        old_log_count = self.service.log_id
        self.service._read_process(FakeProcess('stale-secret-line\n'), self.service.session - 1, 'server')
        self.assertEqual(self.service.log_id, old_log_count)

    def test_close_prevents_game_and_admin_restart(self):
        process = self.start_fake()
        self.service.close()
        self.assertEqual(process.terminated, 1)
        with self.assertRaisesRegex(ValueError, '关闭'):
            self.service.start('101')
        with self.assertRaisesRegex(ValueError, '关闭'):
            self.service.start_admin()


if __name__ == '__main__':
    unittest.main(verbosity=2)
