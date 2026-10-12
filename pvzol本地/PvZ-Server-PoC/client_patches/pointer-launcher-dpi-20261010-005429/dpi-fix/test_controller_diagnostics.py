"""Isolated UI-controller diagnostics; no real launcher, accounts or services."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'staged'))
stubs = {
    'launcher_service': types.SimpleNamespace(LauncherService=Mock()),
    'launcher_instance': types.SimpleNamespace(ACTIVATION_ENV='fixture_activation', LauncherInstance=Mock()),
    'launcher': types.SimpleNamespace(HIDDEN_PROCESS={}),
}
with patch.dict(sys.modules, stubs):
    spec = importlib.util.spec_from_file_location('candidate_desktop', HERE / 'staged' / 'launcher_desktop.py')
    desktop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(desktop)


class ControllerDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='controller-qa-', dir=HERE)
        self.root = Path(self.temporary.name)
        self.service = Mock(ui_pid=None, window_info_path=self.root / 'window.json')
        self.token = 'fixture-session-secret-value'
        self.command = ['fixture-ui.exe', '--url', 'http://127.0.0.1:12345/?token=' + self.token]
        self.module_patch = patch.dict(sys.modules, stubs)
        self.module_patch.start()
        self.root_patch = patch.object(desktop, 'ROOT', self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.module_patch.stop()
        self.temporary.cleanup()

    def test_success_does_not_log_failure_and_sets_owned_ui_pid(self):
        stream = io.BytesIO(b'non-fatal renderer notice\n')
        process = Mock(pid=123, stderr=stream)
        process.wait.return_value = 0
        with patch.object(desktop.subprocess, 'Popen', return_value=process) as spawn:
            self.assertEqual(desktop._run_desktop_shell(self.command, self.service, {}, self.token), 0)
        self.assertEqual(self.service.ui_pid, 123)
        self.service.log.assert_not_called()
        self.assertIs(spawn.call_args.kwargs['stderr'], subprocess.PIPE)
        self.assertIs(spawn.call_args.kwargs['stdout'], subprocess.DEVNULL)
        self.assertTrue(stream.closed)

    def test_nonzero_exit_reports_code_and_safe_error_details(self):
        stream = io.BytesIO(('CLR failure at ' + self.command[-1] + '\nX-PVZ-Token: ' + self.token).encode())
        process = Mock(pid=321, stderr=stream)
        process.wait.return_value = 37
        with patch.object(desktop.subprocess, 'Popen', return_value=process):
            self.assertEqual(desktop._run_desktop_shell(self.command, self.service, {}, self.token), 37)
        message = self.service.log.call_args.args[0]
        self.assertIn('37', message)
        self.assertIn('CLR failure', message)
        self.assertNotIn(self.token, message)
        self.assertTrue(stream.closed)

    def test_spawn_error_is_logged_with_traceback_and_returns_failure(self):
        with patch.object(desktop.subprocess, 'Popen', side_effect=OSError('cannot open ' + self.command[-1])):
            self.assertEqual(desktop._run_desktop_shell(self.command, self.service, {}, self.token), 1)
        message = self.service.log.call_args.args[0]
        self.assertIn('OSError', message)
        self.assertIn('cannot open', message)
        self.assertNotIn(self.token, message)

    def test_large_stderr_pipe_is_drained_and_retained_tail_is_bounded(self):
        # More than a Windows pipe's buffer, so a missing drain would deadlock.
        code = "import sys; sys.stderr.write(('x'*1024+'\\n')*300); sys.stderr.write('fatal '+sys.argv[1]+'\\n'); sys.exit(7)"
        self.assertEqual(desktop._run_desktop_shell([sys.executable, '-c', code, self.token],
                                                  self.service, {}, self.token), 7)
        message = self.service.log.call_args.args[0]
        self.assertLess(len(message), 12500)
        self.assertNotIn(self.token, message)
        self.assertIn('fatal', message)
        # Independently verify that the tail buffer retains the final error.
        capture = desktop._BoundedStderr(io.BytesIO(b'x\n' * 20000 + b'final error\n'))
        text = capture.finish()
        self.assertIn('final error', text)
        self.assertLessEqual(len(text.encode()), 16384)

    def test_run_preserves_service_server_instance_cleanup_on_failure(self):
        ui_dir = self.root / '启动器界面'
        ui_dir.mkdir()
        (ui_dir / 'PvZLauncherUI.exe').write_bytes(b'fixture only')
        instance = Mock(activation_event_name='fixture-event')
        instance.acquire.return_value = True
        server = Mock(url=self.command[-1], token=self.token)
        thread = Mock()
        with patch.object(desktop, 'LauncherInstance', return_value=instance), \
             patch.object(desktop, 'LauncherService', return_value=self.service), \
             patch.object(desktop, 'LauncherHTTPServer', return_value=server), \
             patch.object(desktop.threading, 'Thread', return_value=thread), \
             patch.object(desktop, '_run_desktop_shell', return_value=11), \
             patch.dict(os.environ, {'PVZ_LAUNCHER_API_ONLY': '0', 'PVZ_LAUNCHER_CONNECTION_FILE': ''}):
            self.assertEqual(desktop.run([]), 11)
        thread.start.assert_called_once()
        self.service.close.assert_called_once()
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
        thread.join.assert_called_once_with(timeout=2)
        instance.close.assert_called_once()


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ControllerDiagnosticsTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {'passed': result.wasSuccessful(), 'test_count': result.testsRun,
               'failures': [str(test) + ': ' + detail for test, detail in result.failures],
               'errors': [str(test) + ': ' + detail for test, detail in result.errors],
               'isolation': {'real_ui_launched': False, 'real_services_launched': False,
                             'real_accounts_accessed': False, 'D_drive_modified': False,
                             'owned_synthetic_stderr_process': True}}
    (HERE / 'controller_test_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
