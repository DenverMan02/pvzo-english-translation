"""Native launcher helpers tested without .NET, windows or game databases.

HTTP traffic targets an ephemeral fixture bound only to 127.0.0.1.  Tray
actions use in-memory state, and worker tests coordinate with events rather
than launching the actual native tray or game process.
"""
from __future__ import annotations

import copy
import ctypes
import gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import warnings
from unittest.mock import Mock, patch


PROJECT = Path(__file__).resolve().parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import ui_shell as shell


def fixture_state(**changes):
    state = {
        'running': False,
        'busy': False,
        'currentAccount': {'id': '101', 'name': '角色 A'},
        'activeAccountId': '101',
        'services': [{'id': 'admin', 'status': 'stopped'}],
    }
    state.update(changes)
    return state


class GeometryAndSelectionTests(unittest.TestCase):
    def test_center_uses_work_area_including_taskbar_offset(self):
        self.assertEqual(shell.center_geometry(1280, 860, (0, 40, 1920, 1040)),
                         (320, 130, 1280, 860))

    def test_center_supports_monitor_left_of_primary(self):
        self.assertEqual(shell.center_geometry(1280, 860, (-1920, 80, 1920, 1040)),
                         (-1600, 170, 1280, 860))

    def test_center_clamps_window_to_small_work_area(self):
        self.assertEqual(shell.center_geometry(1280, 860, (100, 50, 900, 600)),
                         (100, 50, 900, 600))

    def test_current_page_selection_wins_over_previous_started_account(self):
        accounts = [{'id': '101'}, {'id': '102'}, {'id': '103'}]
        self.assertEqual(shell.select_start_account(fixture_state(), accounts, '102'), '102')

    def test_deleted_selection_falls_back_only_to_remaining_account(self):
        accounts = [{'id': '102'}, {'id': '103'}]
        state = fixture_state(currentAccount={'id': '101'}, activeAccountId='103')
        self.assertEqual(shell.select_start_account(state, accounts, '101'), '103')

    def test_current_account_has_priority_over_active_account_id(self):
        state = fixture_state(currentAccount={'uin': 102}, activeAccountId='103')
        self.assertEqual(shell.select_start_account(state, [{'uin': 102}, {'id': '103'}]), '102')

    def test_missing_saved_account_uses_first_existing_account(self):
        self.assertEqual(shell.select_start_account(fixture_state(), [{'id': '102'}]), '102')

    def test_empty_accounts_never_reuses_stale_current_account(self):
        self.assertIsNone(shell.select_start_account(fixture_state(), [], '101'))


class StartupErrorTests(unittest.TestCase):
    def test_renderer_exception_cannot_put_session_url_in_log_or_error_dialog(self):
        token = 'fixture-startup-secret-token'
        url = 'http://127.0.0.1:9/?token=' + token
        webview = SimpleNamespace(
            settings={},
            create_window=Mock(side_effect=RuntimeError('renderer rejected ' + url)),
            start=Mock(),
        )
        logger = Mock()
        qa_root = PROJECT.parent / 'qa'
        qa_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='native-startup-', dir=qa_root) as temporary, \
                patch.dict(sys.modules, {'webview': webview}), \
                patch.object(shell, 'check_dependencies', return_value={'ok': True}), \
                patch.object(shell, 'logging', logger), \
                patch.object(shell, 'show_error') as dialog:
            self.assertEqual(shell.main(['--url', url, '--data-dir', temporary]), 1)
        dialog.assert_called_once()
        for output in (str(dialog.call_args), str(logger.mock_calls)):
            self.assertNotIn(token, output)
            self.assertNotIn(url, output)
        logger.exception.assert_not_called()
        self.assertFalse(any(call.kwargs.get('exc_info') for call in logger.mock_calls))
        webview.start.assert_not_called()


class ForegroundAuthorizationTests(unittest.TestCase):
    def test_valid_pid_uses_exact_uint32_allow_call_and_no_other_window_api(self):
        for pid in (1, 12345, 0xfffffffe):
            with self.subTest(pid=pid):
                user = Mock()
                user.AllowSetForegroundWindow.return_value = 1
                with patch.object(shell.ctypes, 'WinDLL', return_value=user, create=True) as load:
                    self.assertTrue(shell.allow_controller_foreground(pid))
                load.assert_called_once_with('user32', use_last_error=True)
                user.AllowSetForegroundWindow.assert_called_once_with(pid)
                self.assertEqual(user.AllowSetForegroundWindow.argtypes, [ctypes.c_uint32])
                self.assertEqual(user.AllowSetForegroundWindow.restype, ctypes.c_int)
                self.assertEqual([call[0] for call in user.mock_calls], ['AllowSetForegroundWindow'])

    def test_invalid_pid_and_asfw_any_are_rejected_before_loading_win32(self):
        with patch.object(shell.ctypes, 'WinDLL', create=True) as load:
            for pid in (None, True, False, '12345', 12345.0, 0, -1, 0xffffffff, 0x100000000):
                with self.subTest(pid=pid):
                    self.assertFalse(shell.allow_controller_foreground(pid))
        load.assert_not_called()

    def test_win32_denial_returns_false_without_broadening_authorization(self):
        user = Mock()
        user.AllowSetForegroundWindow.return_value = 0
        with patch.object(shell.ctypes, 'WinDLL', return_value=user, create=True):
            self.assertFalse(shell.allow_controller_foreground(12345))
        user.AllowSetForegroundWindow.assert_called_once_with(12345)
        self.assertEqual([call[0] for call in user.mock_calls], ['AllowSetForegroundWindow'])

    def test_library_api_errors_or_missing_api_return_false(self):
        with patch.object(shell.ctypes, 'WinDLL', side_effect=OSError('fixture unavailable'), create=True):
            self.assertFalse(shell.allow_controller_foreground(12345))
        user = Mock()
        user.AllowSetForegroundWindow.side_effect = OSError('fixture denied')
        with patch.object(shell.ctypes, 'WinDLL', return_value=user, create=True):
            self.assertFalse(shell.allow_controller_foreground(12345))
        user.AllowSetForegroundWindow.assert_called_once_with(12345)
        with patch.object(shell.ctypes, 'WinDLL', return_value=SimpleNamespace(), create=True):
            self.assertFalse(shell.allow_controller_foreground(12345))

    def fixture_tray(self, focused=True):
        # Exercise callback logic without constructing any .NET/native object.
        tray = shell.NativeTray.__new__(shell.NativeTray)
        tray.client = Mock()
        tray.worker = SimpleNamespace(
            actions=SimpleNamespace(state={'controllerPid': 12345}),
            submit=Mock(return_value=True),
        )
        tray.form = SimpleNamespace(ContainsFocus=focused)
        tray._update_menu = Mock()
        tray._invoke = lambda callback: callback()
        tray.show = Mock()
        return tray

    def test_activation_and_tray_start_authorize_without_show_or_extra_request(self):
        tray = self.fixture_tray(focused=False)
        with patch.object(shell, 'allow_controller_foreground', return_value=True) as allow:
            tray._activated_event()
            allow.assert_called_once_with(12345)
            tray.worker.submit.assert_not_called()
            allow.reset_mock()
            tray._start_event()
            allow.assert_called_once_with(12345)
        tray.worker.submit.assert_called_once_with('start')
        tray.client.request.assert_not_called()
        tray.show.assert_not_called()
        self.assertEqual(vars(tray.form), {'ContainsFocus': False})

    def test_first_or_refresh_state_grants_only_while_launcher_already_focused(self):
        for focused in (False, True):
            with self.subTest(focused=focused):
                tray = self.fixture_tray(focused)
                with patch.object(shell, 'allow_controller_foreground', return_value=True) as allow:
                    tray._on_result('refresh', tray.worker.actions.state, None)
                if focused:
                    allow.assert_called_once_with(12345)
                else:
                    allow.assert_not_called()
                tray.worker.submit.assert_not_called()
                tray.client.request.assert_not_called()
                tray.show.assert_not_called()
                self.assertEqual(vars(tray.form), {'ContainsFocus': focused})


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *args):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        body = json.loads(raw) if raw else None
        self.server.requests.append((self.command, self.path, dict(self.headers), body))
        authorized = (self.headers.get('X-PVZ-Token') == self.server.token
                      and self.headers.get('Host') == '127.0.0.1:%d' % self.server.server_port)
        if not authorized:
            status, headers, content = 403, {}, {'ok': False, 'error': 'fixture unauthorized'}
        else:
            status, headers, content = self.server.reply
        payload = content if isinstance(content, bytes) else json.dumps(content).encode('utf-8')
        phase = getattr(self.server, 'slow_phase', None)
        started = getattr(self.server, 'response_started', None)
        release = getattr(self.server, 'release_response', None)
        if phase == 'headers':
            started.set()
            release.wait(5)
            return
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            if phase == 'body':
                self.wfile.write(payload[:10])
                self.wfile.flush()
                started.set()
                if not release.wait(5):
                    return
                self.wfile.write(payload[10:])
            else:
                self.wfile.write(payload)
        except OSError:
            # Cancellation intentionally disconnects the slow response fixture.
            pass


class LoopbackClientTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        self.server.daemon_threads = True
        self.server.token = 'fixture-native-secret-token'
        self.server.requests = []
        self.server.reply = (200, {}, {'ok': True, 'data': fixture_state()})
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:%d/?token=%s' % (self.server.server_port, self.server.token)
        self.client = shell.LoopbackClient(self.url)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def test_authenticated_request_keeps_token_out_of_path_and_json(self):
        self.assertEqual(self.client.request('GET', '/api/state'), fixture_state())
        method, path, headers, data = self.server.requests[0]
        self.assertEqual((method, path, data), ('GET', '/api/state', None))
        self.assertEqual(headers['X-PVZ-Token'], self.server.token)
        self.assertNotIn(self.server.token, path)

    def test_start_sends_json_to_exact_authorized_loopback_origin(self):
        self.client.request('POST', '/api/start', {'account_id': '102'})
        method, path, headers, data = self.server.requests[0]
        self.assertEqual((method, path, data), ('POST', '/api/start', {'account_id': '102'}))
        self.assertTrue(headers['Content-Type'].startswith('application/json'))
        self.assertEqual(headers['Host'], '127.0.0.1:%d' % self.server.server_port)

    def test_remote_dns_userinfo_and_bad_scheme_urls_are_rejected(self):
        for value in ('https://127.0.0.1:9/?token=x', 'http://localhost:9/?token=x',
                      'http://example.com:9/?token=x', 'http://192.168.1.2:9/?token=x',
                      'http://user:pass@127.0.0.1:9/?token=x'):
            with self.subTest(value=value), self.assertRaises((ValueError, shell.TrayError)):
                shell.LoopbackClient(value)

    def test_non_api_or_query_paths_are_rejected_before_any_network_request(self):
        for path in ('/api/settings', '/api/state?token=x', '//example.com/api/state',
                     'http://127.0.0.1:9/api/state', '/api/../api/state'):
            with self.subTest(path=path), self.assertRaises(shell.TrayError):
                self.client.request('GET', path)
        self.assertEqual(self.server.requests, [])

    def test_wrong_methods_cannot_reach_an_allowed_endpoint(self):
        for method, path in (('GET', '/api/start'), ('POST', '/api/state'),
                             ('DELETE', '/api/stop'), ('POST', '/api/accounts')):
            with self.subTest(method=method, path=path), self.assertRaises(shell.TrayError):
                self.client.request(method, path)
        self.assertEqual(self.server.requests, [])

    def test_missing_duplicate_or_decoded_header_injection_token_is_rejected(self):
        origin = 'http://127.0.0.1:%d/' % self.server.server_port
        for query in ('', '?token=', '?token=a&token=b', '?token=a%0d%0ab', '?token=a%00b'):
            with self.subTest(query=query), self.assertRaises(shell.TrayError):
                shell.LoopbackClient(origin + query)
        self.assertEqual(self.server.requests, [])

    def test_cookie_cannot_replace_session_header(self):
        wrong = shell.LoopbackClient(self.url.replace(self.server.token, 'wrong'))
        try:
            with self.assertRaises(shell.TrayError):
                wrong.request('GET', '/api/state')
            self.assertEqual(self.server.requests[0][2]['X-PVZ-Token'], 'wrong')
            self.assertNotIn('Cookie', self.server.requests[0][2])
        finally:
            wrong.close()

    def test_proxy_environment_cannot_route_authenticated_request_elsewhere(self):
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1',
                                     'http_proxy': 'http://127.0.0.1:1',
                                     'NO_PROXY': '', 'no_proxy': ''}):
            self.assertEqual(self.client.request('GET', '/api/state'), fixture_state())
        self.assertEqual(len(self.server.requests), 1)

    def test_redirect_is_never_followed_or_sent_token(self):
        self.server.reply = (302, {'Location': 'http://127.0.0.1:%d/api/accounts' % self.server.server_port},
                             {'ok': False, 'error': 'fixture redirect'})
        with self.assertRaises(shell.TrayError):
            self.client.request('GET', '/api/state')
        self.assertEqual(len(self.server.requests), 1)

    def test_http_and_application_error_messages_do_not_reveal_token(self):
        for status, payload in ((403, {'ok': False, 'error': self.server.token}),
                                (200, {'ok': False, 'error': self.url})):
            with self.subTest(status=status):
                self.server.reply = (status, {}, payload)
                with self.assertRaises(shell.TrayError) as result:
                    self.client.request('GET', '/api/state')
                self.assertNotIn(self.server.token, str(result.exception))
                self.assertNotIn(self.url, str(result.exception))

    def test_invalid_json_is_sanitized_and_does_not_poison_next_request(self):
        self.server.reply = (200, {}, ('broken-json ' + self.server.token).encode())
        with self.assertRaises(shell.TrayError) as result:
            self.client.request('GET', '/api/state')
        self.assertNotIn(self.server.token, str(result.exception))
        self.server.reply = (200, {}, {'ok': True, 'data': fixture_state()})
        self.assertEqual(self.client.request('GET', '/api/state'), fixture_state())

    def test_oversized_response_is_rejected_without_returning_body(self):
        self.server.reply = (200, {}, b' ' * (1024 * 1024 + 1))
        with self.assertRaises(shell.TrayError) as result:
            self.client.request('GET', '/api/state')
        self.assertLess(len(str(result.exception)), 240)

    def test_close_is_idempotent_and_prevents_further_http_requests(self):
        self.client.close()
        self.client.close()
        with self.assertRaises(shell.TrayError):
            self.client.request('GET', '/api/state')
        self.assertEqual(self.server.requests, [])

    def test_close_bounds_actual_blocked_header_and_body_reads_without_server_release(self):
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter('always', ResourceWarning)
            for phase in ('headers', 'body'):
                with self.subTest(phase=phase):
                    self.server.slow_phase = phase
                    self.server.response_started = threading.Event()
                    self.server.release_response = threading.Event()
                    client = shell.LoopbackClient(self.url, timeout=3)
                    failures = []

                    def request():
                        try:
                            client.request('GET', '/api/state')
                        except BaseException as exc:
                            failures.append(exc)

                    thread = threading.Thread(target=request, name='fixture blocked HTTP request')
                    thread.start()
                    try:
                        self.assertTrue(self.server.response_started.wait(2))
                        client.close()
                        # Windows' timeout-mode socket select is not immediately
                        # woken by shutdown.  The request must still drain by its
                        # bounded 3-second HTTP timeout before the server releases.
                        thread.join(3.5)
                        self.assertFalse(thread.is_alive(),
                                         'closed request must drain within the HTTP timeout plus scheduling margin')
                        self.assertFalse(self.server.release_response.is_set())
                        self.assertEqual(len(failures), 1)
                        self.assertIsInstance(failures[0], shell.TrayError)
                        self.assertNotIn(self.server.token, str(failures[0]))
                    finally:
                        self.server.release_response.set()
                        client.close()
                        thread.join(4)
                        self.server.slow_phase = None
                        # Discard exception frames so delayed file/socket cleanup
                        # cannot hide behind Python's exception context references.
                        failures.clear()
                        gc.collect()
        leaked = [str(item.message) for item in observed
                  if issubclass(item.category, ResourceWarning) and 'unclosed' in str(item.message)]
        self.assertEqual(leaked, [], 'cancelled HTTP responses must explicitly release their readers')


class MemoryClient:
    def __init__(self, state=None, accounts=None):
        self.state = state if state is not None else fixture_state()
        self.accounts = accounts if accounts is not None else [{'id': '101'}, {'id': '102'}]
        self.calls = []
        self.closed = False

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if path == '/api/state':
            return copy.deepcopy(self.state)
        if path == '/api/accounts':
            return copy.deepcopy(self.accounts)
        if path == '/api/start':
            self.state['busy'] = True
            self.state['activeAccountId'] = data['account_id']
        elif path == '/api/stop':
            self.state.update(running=False, busy=False)
            self.state['services'] = [{'id': 'admin', 'status': 'stopped'}]
        return {'request_accepted': True}

    def close(self):
        self.closed = True


class TrayActionTests(unittest.TestCase):
    def actions(self, state=None, accounts=None, selected='102'):
        client = MemoryClient(state, accounts)
        actions = shell.TrayActions(client, selected_account=lambda: selected)
        return client, actions

    def test_start_uses_live_selection_and_returns_fresh_state(self):
        client, actions = self.actions()
        result = actions.start()
        self.assertIn(('POST', '/api/start', {'account_id': '102'}), client.calls)
        self.assertEqual(client.calls[-1], ('GET', '/api/state', None))
        self.assertTrue(result['busy'])
        self.assertEqual(actions.state, result)

    def test_start_refuses_running_or_busy_even_when_cached_state_is_idle(self):
        for changes in ({'running': True}, {'busy': True}):
            with self.subTest(changes=changes):
                client, actions = self.actions(fixture_state(**changes))
                actions.state = fixture_state()
                actions.start()
                self.assertFalse(any(method == 'POST' for method, _path, _body in client.calls))

    def test_start_after_selected_role_deleted_uses_remaining_role(self):
        client, actions = self.actions(accounts=[{'id': '103'}])
        actions.start()
        self.assertIn(('POST', '/api/start', {'account_id': '103'}), client.calls)

    def test_start_without_any_role_does_not_create_or_start_account(self):
        client, actions = self.actions(accounts=[])
        with self.assertRaises(shell.TrayError):
            actions.start()
        self.assertFalse(any(method == 'POST' for method, _path, _body in client.calls))

    def test_stop_busy_cancels_start_and_returns_latest_idle_state(self):
        for running in (False, True):
            with self.subTest(running=running):
                client, actions = self.actions(fixture_state(running=running, busy=True))
                result = actions.stop()
                self.assertEqual(client.calls, [('GET', '/api/state', None),
                                                ('POST', '/api/stop', {}),
                                                ('GET', '/api/state', None)])
                self.assertFalse(result['running'])
                self.assertFalse(result['busy'])
                self.assertEqual(actions.state, result)

    def test_stop_running_and_admin_only_return_refreshed_state(self):
        for state in (fixture_state(running=True),
                      fixture_state(services=[{'id': 'admin', 'status': 'running'}])):
            with self.subTest(state=state):
                client, actions = self.actions(state)
                result = actions.stop()
                self.assertIn(('POST', '/api/stop', {}), client.calls)
                self.assertEqual(client.calls[-1], ('GET', '/api/state', None))
                self.assertFalse(result['running'])
                self.assertEqual(actions.state, result)

    def test_stop_idle_and_refresh_only_fetch_state(self):
        client, actions = self.actions()
        self.assertEqual(actions.stop(), fixture_state())
        self.assertEqual(client.calls, [('GET', '/api/state', None)])
        client.calls.clear()
        self.assertEqual(actions.refresh(), fixture_state())
        self.assertEqual(client.calls, [('GET', '/api/state', None)])


class TrayMenuTests(unittest.TestCase):
    def test_busy_state_allows_cancel_but_pending_request_disables_both_actions(self):
        tray = shell.NativeTray.__new__(shell.NativeTray)
        tray._available = True
        tray.start_item = SimpleNamespace(Enabled=None)
        tray.stop_item = SimpleNamespace(Enabled=None)
        tray.worker = SimpleNamespace(
            actions=SimpleNamespace(state=fixture_state(busy=True, running=False)),
            pending=False,
        )
        tray._update_menu()
        self.assertFalse(tray.start_item.Enabled)
        self.assertTrue(tray.stop_item.Enabled)
        tray.worker.pending = True
        tray._update_menu()
        self.assertFalse(tray.start_item.Enabled)
        self.assertFalse(tray.stop_item.Enabled)
        tray.worker.pending = False
        tray._available = False
        tray._update_menu()
        self.assertFalse(tray.start_item.Enabled)
        self.assertFalse(tray.stop_item.Enabled)


class BlockingActions:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.called = []
        self.client = Mock()
        self.client.close.side_effect = self.release.set

    def start(self):
        self.called.append('start')
        self.entered.set()
        if not self.release.wait(2):
            raise RuntimeError('fixture synchronization timeout')
        return fixture_state(running=True)

    def refresh(self):
        self.called.append('refresh')
        return fixture_state()

    def stop(self):
        self.called.append('stop')
        return fixture_state()


class TrayWorkerTests(unittest.TestCase):
    def test_pending_action_blocks_double_clicks_and_callback_contains_fresh_state(self):
        actions = BlockingActions()
        completed = threading.Event()
        results = []

        def callback(action, state, error):
            results.append((action, state, error))
            completed.set()

        worker = shell.TrayWorker(actions, callback)
        worker.start()
        try:
            self.assertTrue(worker.submit('start'))
            self.assertTrue(actions.entered.wait(2), 'worker must execute the queued action')
            self.assertFalse(worker.submit('start'))
            self.assertFalse(worker.submit('stop'))
            self.assertFalse(worker.submit('refresh'))
            actions.release.set()
            self.assertTrue(completed.wait(2))
            self.assertEqual(actions.called, ['start'])
            self.assertEqual(results, [('start', fixture_state(running=True), None)])
        finally:
            actions.release.set()
            worker.close()
            self.assertFalse(worker.thread.is_alive())

    def test_close_cancels_client_and_waits_for_inflight_work_without_late_callback(self):
        actions = BlockingActions()
        callback = Mock()
        worker = shell.TrayWorker(actions, callback)
        worker.start()
        self.assertTrue(worker.submit('start'))
        self.assertTrue(actions.entered.wait(2))
        worker.close()
        actions.client.close.assert_called()
        callback.assert_not_called()
        self.assertFalse(worker.submit('start'))
        worker.close()
        self.assertFalse(worker.thread.is_alive())

    def test_action_error_is_reported_and_next_request_can_run(self):
        actions = BlockingActions()
        actions.start = Mock(side_effect=shell.TrayError('fixture request failed'))
        completed = threading.Event()
        results = []

        def callback(action, state, error):
            results.append((action, state, error))
            completed.set()

        worker = shell.TrayWorker(actions, callback)
        worker.start()
        try:
            self.assertTrue(worker.submit('start'))
            self.assertTrue(completed.wait(2))
            self.assertEqual(results[0][0], 'start')
            self.assertIn('fixture request failed', str(results[0][2]))
            completed.clear()
            self.assertTrue(worker.submit('refresh'))
            self.assertTrue(completed.wait(2))
            self.assertEqual(results[-1], ('refresh', fixture_state(), None))
        finally:
            worker.close()
            self.assertFalse(worker.thread.is_alive())

    def test_unexpected_error_is_sanitized_and_worker_can_close(self):
        actions = BlockingActions()
        actions.start = Mock(side_effect=RuntimeError('http://127.0.0.1:9/?token=fixture-secret'))
        completed = threading.Event()
        results = []

        def callback(action, state, error):
            results.append((action, state, error))
            completed.set()

        worker = shell.TrayWorker(actions, callback)
        worker.start()
        try:
            self.assertTrue(worker.submit('start'))
            self.assertTrue(completed.wait(2))
            self.assertEqual(results[0][:2], ('start', None))
            self.assertNotIn('fixture-secret', str(results[0][2]))
            self.assertFalse(worker.submit('unsupported'))
        finally:
            worker.close()
            self.assertFalse(worker.thread.is_alive())


if __name__ == '__main__':
    unittest.main(verbosity=2)
