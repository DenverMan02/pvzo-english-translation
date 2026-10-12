"""Configurable port behavior is tested against temporary settings and saves."""
from __future__ import annotations

from contextlib import closing
import copy
import http.client
import json
from pathlib import Path
import sys
import socket
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2]
for path in (SOURCE, SOURCE / 'PvZ-Server-PoC'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import launcher as legacy
import launcher_desktop as desktop
import launcher_service
import serve_client
from launcher_ports import DEFAULT_PORTS, port_environment, validate_ports
from test_launcher_desktop import FakeProcess, IsolatedLauncherTests


CUSTOM = {'web': 18080, 'admin': 18081, 'game': 19001, 'policy': 843}


class PortValidationTests(unittest.TestCase):
    def test_defaults_partial_and_environment_mapping(self):
        self.assertEqual(validate_ports({}), DEFAULT_PORTS)
        self.assertEqual(validate_ports({'web': 18080})['web'], 18080)
        self.assertEqual(port_environment(CUSTOM), {
            'PVZ_WEB_PORT': '18080', 'PVZ_ADMIN_PORT': '18081',
            'PVZ_GAME_PORT': '19001', 'PVZ_POLICY_PORT': '843'})

    def test_invalid_range_type_collision_and_policy_are_rejected(self):
        for value in ({'web': 0}, {'web': 65536}, {'web': True}, {'web': '8080'},
                      {'web': 8081}, {'game': 843}, {'policy': 1843}, {'unknown': 1}, [], None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_ports(value)

    def test_player_url_and_ruffle_command_use_actual_ports(self):
        url = legacy.game_url('001', web_port=18080, game_port=19001)
        self.assertIn('http://127.0.0.1:18080/loader_p.swf', url)
        self.assertIn('server=127.0.0.1:19001', url)
        self.assertIn('&uin=001&', url)
        command = legacy.game_player_command('ruffle', 'ruffle.exe', url, game_port=19001)
        self.assertEqual(command[2], '127.0.0.1:19001')

    def test_server_environment_urls_and_policy_constraints(self):
        import importlib.util
        with patch.dict('os.environ', port_environment(CUSTOM)):
            spec = importlib.util.spec_from_file_location('isolated_runtime_ports', SOURCE / 'PvZ-Server-PoC/runtime_ports.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.assertEqual((module.WEB_PORT, module.ADMIN_PORT, module.GAME_PORT, module.POLICY_PORT), (18080, 18081, 19001, 843))
        self.assertEqual(module.web_url('/image/head/2.png'), 'http://127.0.0.1:18080/image/head/2.png')
        self.assertEqual(module.local_avatar_url('http://127.0.0.1:8080/image/head/custom/a.png'),
                         'http://127.0.0.1:18080/image/head/custom/a.png')
        self.assertEqual(module.local_avatar_url('https://example.com/avatar.png'), 'https://example.com/avatar.png')
        self.assertEqual(module.local_avatar_url('http://[invalid'), 'http://[invalid')
        with patch.dict('os.environ', {'PVZ_POLICY_PORT': '844'}), self.assertRaisesRegex(ValueError, '843'):
            module.env_port('policy', 843)


class ConfiguredServiceTests(IsolatedLauncherTests):
    def test_custom_ports_persist_reload_and_propagate_to_children(self):
        result = self.service.save_settings({'ports': CUSTOM})
        self.assertEqual(result['ports'], CUSTOM)
        self.assertEqual(self.service._load_settings()['ports'], CUSTOM)
        self.assertEqual(self.service._environment()['PVZ_GAME_PORT'], '19001')
        self.assertEqual(self.service._environment()['PVZ_WEB_PORT'], '18080')
        self.assertIn(':18081/', self.service.admin_info()['url'])
        self.assertEqual(self.service.save_settings({'theme': 'dark'})['ports'], CUSTOM)

    def test_invalid_ports_preserve_settings_and_database(self):
        self.service.save_settings({'ports': CUSTOM})
        saved = self.service.settings_path.read_bytes()
        database = self.service.db_path.read_bytes()
        for ports in ({'admin': 18080}, {'policy': 844}, {'game': False}):
            with self.subTest(ports=ports), self.assertRaises(ValueError):
                self.service.save_settings({'ports': ports})
        self.assertEqual(self.service.settings_path.read_bytes(), saved)
        self.assertEqual(self.service.db_path.read_bytes(), database)

    def test_ports_cannot_change_while_owned_gm_game_or_foreign_game_runs(self):
        for owner in ('admin', 'server', 'game'):
            with self.subTest(owner=owner):
                setattr(self.service, owner, FakeProcess())
                with self.assertRaisesRegex(ValueError, '停止游戏与 GM'):
                    self.service.save_settings({'ports': CUSTOM})
                setattr(self.service, owner, None)
        with patch.object(legacy, 'port_open', return_value=True):
            with self.assertRaisesRegex(ValueError, '停止游戏与 GM'):
                self.service.save_settings({'ports': CUSTOM})
        self.assertFalse(self.service.settings_path.exists())

    def test_appearance_change_remains_allowed_while_service_is_running(self):
        self.service.running = True
        self.assertEqual(self.service.save_settings({'theme': 'dark'})['theme'], 'dark')
        self.service.running = False

    def test_occupied_new_target_cannot_be_saved_and_listener_is_preserved(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            target = listener.getsockname()[1]
            with self.assertRaisesRegex(ValueError, str(target)):
                self.service.save_settings({'ports': {'web': target}})
            self.assertFalse(self.service.settings_path.exists())
            with socket.create_connection(('127.0.0.1', target), timeout=1):
                connection, _ = listener.accept()
                connection.close()

    def test_standalone_gm_memory_is_unknown_until_sampled_or_when_unreadable(self):
        gm = FakeProcess()
        gm.pid = 12345
        self.service.admin = gm
        class Monitor:
            def __init__(self):
                self.processes = []
            def snapshot(self, *args):
                return {'ports': {}, 'processes': self.processes, 'pending': True}
        monitor = Monitor()
        self.service.runtime_monitor = monitor
        state = self.service.state()
        self.assertIsNone(state['runtime']['memory_bytes'])
        self.assertFalse(state['runtime']['memory_complete'])
        monitor.processes = [{'pid': gm.pid, 'name': 'gm.exe', 'memory_bytes': None}]
        state = self.service.state()
        self.assertIsNone(state['runtime']['memory_bytes'])
        self.assertFalse(state['runtime']['memory_complete'])
        self.assertEqual(state['gamePath'], str(self.root))

    def test_stop_clears_owned_standalone_gm_before_port_change(self):
        gm = FakeProcess()
        self.service.admin = gm
        self.service.stop()
        self.assertEqual(gm.terminated, 1)
        self.assertEqual(self.service.save_settings({'ports': CUSTOM})['ports'], CUSTOM)

    def test_start_conflict_is_actionable_state_and_never_spawns_game(self):
        self.create()
        self.service.save_settings({'ports': CUSTOM})
        with patch.object(legacy, 'port_open', side_effect=lambda port, **kwargs: port == 19001), \
             patch.object(self.service, '_spawn') as spawn:
            with self.assertRaisesRegex(ValueError, '19001'):
                self.service.start('101')
        spawn.assert_not_called()
        state = self.service.state()
        self.assertEqual(state['startup']['phase'], 'error')
        self.assertIn('19001', state['error'])
        self.assertIn('空闲端口', state['startup']['action'])
        self.assertFalse(state['busy'])

    def test_missing_files_error_is_reported_without_mutating_save(self):
        self.create()
        (self.root / 'webroot' / 'loader_p.swf').unlink()
        database = self.service.db_path.read_bytes()
        with self.assertRaisesRegex(ValueError, '文件缺失'):
            self.service.start('101')
        self.assertIn('文件缺失', self.service.state()['startup']['error'])
        self.assertEqual(self.service.db_path.read_bytes(), database)

    def test_configured_readiness_checks_use_actual_ports(self):
        self.create()
        self.service.save_settings({'ports': CUSTOM})
        process = FakeProcess()
        with patch.object(self.service, '_spawn', return_value=process), \
             patch.object(launcher_service.threading, 'Thread'):
            self.service.start('101')
        with patch.object(legacy, 'web_ready', return_value=True) as web, \
             patch.object(legacy, 'admin_ready', return_value=True) as admin, \
             patch.object(legacy, 'port_open', return_value=True) as game, \
             patch.object(legacy, 'policy_ready', return_value=True) as policy:
            self.service._wait_ready(process, self.service.session, 'loader_p.swf', self.service.cancel)
        web.assert_called_once_with('loader_p.swf', 18080)
        admin.assert_called_once_with(18081)
        game.assert_called_once_with(19001)
        policy.assert_called_once_with(843, 19001)
        state = self.service.state()
        self.assertEqual(state['startup']['phase'], 'running')
        self.assertTrue(all(check['ready'] for check in state['startup']['checks']))

    def test_service_log_bind_failure_stops_own_process_with_action(self):
        self.create()
        process = FakeProcess('[launcher-error] web: address in use\n')
        with patch.object(self.service, '_spawn', return_value=process), \
             patch.object(launcher_service.threading, 'Thread'):
            self.service.start('101')
        self.service._read_process(process, self.service.session, 'server')
        with patch.object(legacy, 'web_ready', return_value=False), \
             patch.object(legacy, 'admin_ready', return_value=False), \
             patch.object(legacy, 'port_open', return_value=False), \
             patch.object(legacy, 'policy_ready', return_value=False):
            self.service._wait_ready(process, self.service.session, 'loader_p.swf', self.service.cancel)
        self.assertEqual(process.terminated, 1)
        self.assertEqual(self.service.startup_phase, 'error')
        self.assertIn('address in use', self.service.error)

    def test_cached_runtime_labels_foreign_port_and_actual_memory(self):
        server, game = FakeProcess(), FakeProcess()
        server.pid, game.pid = 10001, 10002
        self.service.server, self.service.game = server, game
        self.service.running = True
        self.service.started_monotonic = time.monotonic() - 10
        snapshot = {'ports': {
            8080: {'occupied': True, 'owned': True, 'pid': 10001, 'name': 'server.exe', 'detail': 'owned'},
            8081: {'occupied': True, 'owned': False, 'pid': 9876, 'name': 'other.exe', 'detail': 'occupied'},
            9001: {'occupied': True, 'owned': True, 'pid': 10001, 'name': 'server.exe'},
            843: {'occupied': False, 'owned': False, 'pid': None}},
            'processes': [{'pid': 10001, 'name': 'server.exe', 'memory_bytes': 200},
                          {'pid': 10002, 'name': 'flash.exe', 'memory_bytes': 500},
                          {'pid': 9876, 'name': 'other.exe', 'memory_bytes': 999}],
            'memory_bytes': 700, 'pending': False}
        class Monitor:
            def snapshot(self, *args):
                return copy.deepcopy(snapshot)
        self.service.runtime_monitor = Monitor()
        state = self.service.state()
        self.assertEqual(state['services'][1]['status'], 'conflict')
        self.assertEqual(state['services'][1]['pid'], 9876)
        self.assertEqual(state['runtime']['memory_bytes'], 700)
        self.assertEqual(state['runtime']['game_memory_bytes'], 500)
        self.assertEqual(state['runtime']['service_memory_bytes'], 200)
        self.assertGreaterEqual(state['runtime']['uptime_seconds'], 10)
        self.assertEqual(state['runtime']['processes'][-1]['role'], 'foreign')

    def test_runtime_monitor_receives_actual_service_binding_scopes(self):
        from unittest.mock import Mock
        monitor = Mock()
        monitor.snapshot.return_value = {'ports': {}, 'processes': [], 'pending': True}
        self.service.runtime_monitor = monitor
        self.service.state()
        self.assertEqual(monitor.snapshot.call_args.args[0],
                         {8080: '127.0.0.1', 8081: '127.0.0.1', 9001: '0.0.0.0', 843: '0.0.0.0'})

    def test_controller_listener_is_a_conflict_for_game_service_ports(self):
        import os
        class Monitor:
            def snapshot(self, *args):
                return {'ports': {8080: {'occupied': True, 'owned': True, 'pid': os.getpid(),
                                        'pids': [os.getpid()], 'name': 'launcher.exe'}},
                        'processes': [], 'pending': False}
        self.service.runtime_monitor = Monitor()
        resource = self.service.state()['services'][0]
        self.assertEqual(resource['status'], 'conflict')
        self.assertFalse(resource['owned'])

    def test_stale_samples_cannot_claim_services_are_healthy_or_memory_current(self):
        server = FakeProcess()
        server.pid = 12345
        self.service.server = server
        self.service.running = True
        class Monitor:
            def snapshot(self, *args):
                return {'ports': {8080: {'occupied': True, 'owned': True, 'pid': 12345, 'detail': 'old record'}},
                        'processes': [{'pid': 12345, 'memory_bytes': 555}], 'stale': True,
                        'detail': 'probe failed', 'pending': False}
        self.service.runtime_monitor = Monitor()
        state = self.service.state()
        self.assertEqual(state['services'][0]['status'], 'error')
        self.assertIn('probe failed', state['services'][0]['detail'])
        self.assertFalse(state['runtime']['memory_complete'])
        self.assertIsNone(state['runtime']['memory_bytes'])


class ConfiguredHTTPTests(IsolatedLauncherTests):
    def setUp(self):
        super().setUp()
        self.api = desktop.LauncherHTTPServer(self.service)
        self.thread = threading.Thread(target=self.api.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.api.shutdown()
        self.api.server_close()
        self.thread.join(2)
        super().tearDown()

    def request(self, path, method='GET', data=None):
        headers = {'Host': '127.0.0.1:%d' % self.api.server_address[1], 'X-PVZ-Token': self.api.token}
        if data is not None:
            data = json.dumps(data).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection(*self.api.server_address, timeout=3)
        try:
            connection.request(method, path, data, headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_settings_ports_endpoint_and_csp_follow_saved_admin_port(self):
        status, headers, body = self.request('/api/settings', 'PATCH', {'ports': CUSTOM})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['data']['ports'], CUSTOM)
        self.assertIn('frame-src http://127.0.0.1:18081;', headers['Content-Security-Policy'])
        status, _, body = self.request('/api/ports')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['data']['ports'], CUSTOM)

    def test_launcher_control_port_cannot_be_reused(self):
        status, _, body = self.request('/api/settings', 'PATCH', {'ports': {'web': self.api.server_address[1]}})
        self.assertEqual(status, 400)
        self.assertIn('启动器界面', json.loads(body)['error'])

    def test_avatar_endpoint_only_serves_valid_approved_images(self):
        head = self.root / 'webroot' / 'image' / 'head'
        (head / 'secrets.txt').write_text('fixture secret', encoding='utf-8')
        (head / '8.png').write_bytes(b'invalid png')
        for path in ('/avatar/secrets.txt', '/avatar/8.png', '/avatar/%2e%2e/secret.txt'):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request('/avatar/0.png')[0], 200)


class ResourceWebPortTests(unittest.TestCase):
    def test_game_webpage_uses_runtime_game_port_without_modifying_file(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='resource-port-test-') as folder:
            root = Path(folder)
            original = b'<script>var gamePort = 9001;</script>'
            (root / 'index.html').write_bytes(original)
            with patch.object(serve_client, 'CLIENT_DIR', folder), \
                 patch.object(serve_client, 'GAME_PORT', 19001):
                server = serve_client.Server(('127.0.0.1', 0), serve_client.Handler)
                thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
                thread.start()
                try:
                    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                    try:
                        connection.request('GET', '/')
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertIn(b'var gamePort = 19001;', response.read())
                    finally:
                        connection.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(2)
            self.assertEqual((root / 'index.html').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
