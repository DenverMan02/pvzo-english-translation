"""Owned service exits use isolated scripts with the real system interpreter."""
from __future__ import annotations

from collections import deque
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2]
for path in (SOURCE, SOURCE / 'PvZ-Server-PoC'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import launcher as legacy
from launcher_guard import guard_blocks_writes
from test_launcher_desktop import FakeProcess, IsolatedLauncherTests


class ServiceExitTests(IsolatedLauncherTests):
    def wait_error(self, seconds=4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with self.service.lock:
                if self.service.startup_phase == 'error':
                    return
            time.sleep(.01)
        self.fail('The isolated service exit was not reported promptly')

    def system_command(self, script, *args):
        # Never call the production portable bootstrap from this fixture.
        return [sys.executable, '-u', str(self.server_dir / script), *args]

    def test_real_runtime_error_retains_last_traceback_line_and_releases_guard(self):
        (self.server_dir / 'run_all.py').write_text(
            "raise RuntimeError('QA: service configuration is missing')\n", encoding='utf-8')
        database = self.service.db_path.read_bytes()
        read_process = self.service._read_process

        def delayed_reader(*args):
            # The waiter can see process exit before the reader drains stderr.
            time.sleep(.15)
            read_process(*args)

        process = None
        try:
            with patch.object(self.service, '_command', side_effect=self.system_command), \
                 patch.object(self.service, '_read_process', side_effect=delayed_reader), \
                 patch.object(legacy, 'web_ready', return_value=False), \
                 patch.object(legacy, 'admin_ready', return_value=False), \
                 patch.object(legacy, 'policy_ready', return_value=False):
                self.service.start('101')
                process = self.service.server
                self.wait_error()
            self.assertEqual(process.poll(), 1)
            self.assertIn('RuntimeError: QA: service configuration is missing', self.service.error)
            self.assertIn('代码 1', self.service.error)
            self.assertTrue(self.service.startup_action)
            self.assertFalse(self.service.running)
            self.assertFalse(self.service.busy)
            self.assertIsNone(self.service.server)
            self.assertFalse(guard_blocks_writes(self.service._environment()))
            self.assertEqual(self.service.db_path.read_bytes(), database)
            self.assertEqual(self.service.state()['startup']['phase'], 'error')
        finally:
            self.service.stop()
            if process is not None and process.stdout:
                process.stdout.close()

    def test_real_service_exit_after_early_stdout_eof_and_readiness_is_reported(self):
        (self.server_dir / 'run_all.py').write_text(
            'import os, time\nos.close(1)\nos.close(2)\ntime.sleep(1)\nos._exit(7)\n',
            encoding='utf-8')
        process = None
        try:
            with patch.object(self.service, '_command', side_effect=self.system_command), \
                 patch.object(self.service, '_wait_ready'):
                self.service.start('101')
                process = self.service.server
            self.assertTrue(self.service.server_output_done.wait(.8))
            self.assertIsNone(process.poll())
            with patch.object(legacy, 'web_ready', return_value=True), \
                 patch.object(legacy, 'admin_ready', return_value=True), \
                 patch.object(legacy, 'port_open', return_value=True), \
                 patch.object(legacy, 'policy_ready', return_value=True):
                self.service._wait_ready(process, self.service.session, 'loader_p.swf', self.service.cancel)
            self.assertEqual(self.service.startup_phase, 'running')
            self.wait_error()
            self.assertEqual(process.poll(), 7)
            self.assertIn('代码 7', self.service.error)
            self.assertFalse(self.service.running)
            self.assertFalse(self.service.busy)
            self.assertFalse(guard_blocks_writes(self.service._environment()))
        finally:
            self.service.stop()
            if process is not None and process.stdout:
                process.stdout.close()

    def test_readiness_reports_dead_service_before_checking_ports(self):
        process, game, admin = FakeProcess(''), FakeProcess(), FakeProcess()
        process.code = 2
        self.service.server, self.service.game, self.service.admin = process, game, admin
        self.service.running = self.service.busy = True
        self.service.server_output.append('ValueError: QA fixture invalid configuration')
        self.service.server_output_done.set()
        with patch.object(legacy, 'web_ready', side_effect=AssertionError('dead service must not probe')):
            self.service._wait_ready(process, self.service.session, 'loader_p.swf', self.service.cancel)
        self.assertIn('QA fixture invalid configuration', self.service.error)
        self.assertEqual([game.terminated, admin.terminated], [1, 1])
        self.assertFalse(self.service.running)
        self.assertFalse(self.service.busy)

    def test_old_exit_waiting_for_output_cannot_stop_replacement_session(self):
        old, replacement = FakeProcess(''), FakeProcess('')
        old.code = 9
        self.service.server = old
        self.service.running = True
        session = self.service.session
        old_done = self.service.server_output_done
        entered, release = threading.Event(), threading.Event()

        def drain_wait(timeout):
            entered.set()
            return release.wait(timeout)

        with patch.object(old_done, 'wait', side_effect=drain_wait):
            watcher = threading.Thread(target=self.service._finish_server_exit, args=(old, session))
            watcher.start()
            self.assertTrue(entered.wait(1))
            with self.service.lock:
                self.service.session += 1
                self.service.server = replacement
                self.service.server_output = deque(maxlen=40)
                self.service.server_output_done = threading.Event()
            release.set()
            watcher.join(1)
        self.assertFalse(watcher.is_alive())
        self.assertIs(self.service.server, replacement)
        self.assertTrue(self.service.running)
        self.assertEqual(replacement.terminated, 0)
        self.assertEqual(self.service.error, '')


if __name__ == '__main__':
    unittest.main()
