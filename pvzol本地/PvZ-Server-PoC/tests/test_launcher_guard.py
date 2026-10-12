"""Lifecycle guard regressions without a game process or production database."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SERVER_SOURCE = Path(__file__).resolve().parents[1]
if str(SERVER_SOURCE) not in sys.path:
    sys.path.insert(0, str(SERVER_SOURCE))

import launcher_guard as guard


class LauncherGuardTests(unittest.TestCase):
    def setUp(self):
        qa_root = SERVER_SOURCE.parent.parent / 'qa'
        qa_root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='guard-test-', dir=qa_root)
        self.path = Path(self.tmp.name) / 'state.json'
        self.pid = 12345
        self.environment = {guard.GUARD_ENV: str(self.path), guard.OWNER_ENV: str(self.pid)}
        self.alive = patch.object(guard, '_process_alive', return_value=True)
        self.alive_mock = self.alive.start()

    def tearDown(self):
        self.alive.stop()
        self.tmp.cleanup()

    def test_standalone_gm_has_no_launcher_lock(self):
        self.assertFalse(guard.guard_blocks_writes({}))
        self.alive_mock.assert_not_called()

    def test_only_idle_state_allows_mutations(self):
        for busy, running in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(busy=busy, running=running):
                guard.write_guard(self.path, busy, running, self.pid)
                self.assertEqual(guard.guard_blocks_writes(self.environment), busy or running)

    def test_missing_state_blocks_during_controller_transition(self):
        self.assertTrue(guard.guard_blocks_writes(self.environment))

    def test_corrupt_state_fails_closed(self):
        for content in (b'', b'{', b'null', b'[]', b'\xff', b' ' * (guard.MAX_GUARD_BYTES + 1)):
            with self.subTest(content=content[:10]):
                self.path.write_bytes(content)
                self.assertTrue(guard.guard_blocks_writes(self.environment))

    def test_bad_shape_cannot_unlock_owner(self):
        good = {'version': 1, 'pid': self.pid, 'busy': False, 'running': False}
        changes = ({'version': True}, {'version': 2}, {'pid': self.pid + 1},
                   {'pid': True}, {'busy': 0}, {'running': 'false'})
        for changeset in changes:
            with self.subTest(changes=changeset):
                self.path.write_text(json.dumps(dict(good, **changeset)), encoding='utf-8')
                self.assertTrue(guard.guard_blocks_writes(self.environment))
        for field in good:
            state = dict(good)
            del state[field]
            self.path.write_text(json.dumps(state), encoding='utf-8')
            self.assertTrue(guard.guard_blocks_writes(self.environment))

    def test_unreadable_state_fails_closed(self):
        with patch.object(Path, 'open', side_effect=PermissionError('locked')):
            self.assertTrue(guard.guard_blocks_writes(self.environment))

    def test_duplicate_fields_or_excessive_nesting_fail_closed(self):
        duplicate = '{"version":1,"pid":12345,"busy":true,"busy":false,"running":false}'
        deep = '[' * 1500 + '0' + ']' * 1500
        for text in (duplicate, deep):
            with self.subTest(content=text[:80]):
                self.path.write_text(text, encoding='utf-8')
                self.assertTrue(guard.guard_blocks_writes(self.environment))

    def test_exited_owner_releases_missing_and_busy_state(self):
        self.alive_mock.return_value = False
        self.assertFalse(guard.guard_blocks_writes(self.environment))
        guard.write_guard(self.path, True, True, self.pid)
        self.assertFalse(guard.guard_blocks_writes(self.environment))

    def test_invalid_owner_cannot_unlock(self):
        for owner in ('', '-1', '0', str(2 ** 32), 'no-pid', '\uff11\uff12', ' 12345', 12345):
            with self.subTest(owner=owner):
                env = dict(self.environment, **{guard.OWNER_ENV: owner})
                self.assertTrue(guard.guard_blocks_writes(env))

    def test_relative_or_empty_path_fails_closed(self):
        for path in ('relative.json', '', None):
            with self.subTest(path=path):
                env = dict(self.environment, **{guard.GUARD_ENV: path})
                self.assertTrue(guard.guard_blocks_writes(env))

    def test_atomic_replace_failure_preserves_last_busy_lock(self):
        guard.write_guard(self.path, True, False, self.pid)
        before = self.path.read_bytes()
        with patch.object(guard.os, 'replace', side_effect=PermissionError('busy file')):
            with self.assertRaises(PermissionError):
                guard.write_guard(self.path, False, False, self.pid)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])
        self.assertTrue(guard.guard_blocks_writes(self.environment))

    def test_writer_rejects_relative_path_or_non_boolean_state(self):
        with self.assertRaises(ValueError):
            guard.write_guard('relative.json', False, False, self.pid)
        for busy, running, pid in ((1, False, self.pid), (False, 'yes', self.pid),
                                   (False, False, True), (False, False, 0)):
            with self.subTest(busy=busy, running=running, pid=pid):
                with self.assertRaises(ValueError):
                    guard.write_guard(self.path, busy, running, pid)
        self.assertFalse(self.path.exists())

    def test_default_owner_is_current_process(self):
        guard.write_guard(self.path, False, False)
        state = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(state['pid'], os.getpid())
        env = dict(self.environment, **{guard.OWNER_ENV: str(os.getpid())})
        self.assertFalse(guard.guard_blocks_writes(env))


class WindowsProcessQueryTests(unittest.TestCase):
    def kernel(self, handle=101, exit_code=259, success=True):
        kernel = Mock()
        kernel.OpenProcess.return_value = handle
        kernel.CloseHandle.return_value = True

        def get_exit(_handle, pointer):
            pointer._obj.value = exit_code
            return success

        kernel.GetExitCodeProcess.side_effect = get_exit
        return kernel

    def test_query_requests_read_only_rights_and_closes_handle(self):
        self.assertEqual(ctypes.sizeof(guard._DWORD), 4)
        self.assertEqual(ctypes.sizeof(guard._BOOL), 4)
        self.assertEqual(ctypes.sizeof(guard._HANDLE), ctypes.sizeof(ctypes.c_void_p))
        kernel = self.kernel()
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
                patch.object(os, 'kill', side_effect=AssertionError('must not signal Windows')):
            self.assertTrue(guard._windows_process_alive(12345))
        kernel.OpenProcess.assert_called_once_with(0x1000, False, 12345)
        self.assertEqual(kernel.OpenProcess.argtypes,
                         [ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint32])
        kernel.CloseHandle.assert_called_once_with(101)

    def test_finished_process_is_not_alive(self):
        kernel = self.kernel(exit_code=0)
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            self.assertFalse(guard._windows_process_alive(12345))
        kernel.CloseHandle.assert_called_once_with(101)

    def test_missing_process_and_denied_query_are_distinguished(self):
        for error, expected in ((87, False), (5, True), (8, True)):
            with self.subTest(error=error):
                kernel = self.kernel(handle=0)
                with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
                        patch.object(ctypes, 'get_last_error', return_value=error, create=True):
                    self.assertEqual(guard._windows_process_alive(12345), expected)
                kernel.CloseHandle.assert_not_called()

    def test_failed_exit_query_keeps_lock_and_closes_handle(self):
        kernel = self.kernel(success=False)
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            self.assertTrue(guard._windows_process_alive(12345))
        kernel.CloseHandle.assert_called_once_with(101)

    def test_windows_dispatch_does_not_send_signals(self):
        with patch.object(os, 'name', 'nt'), \
                patch.object(guard, '_windows_process_alive', return_value=True) as query, \
                patch.object(os, 'kill', side_effect=AssertionError('must not signal Windows')):
            self.assertTrue(guard._process_alive(os.getpid() + 1))
        query.assert_called_once_with(os.getpid() + 1)


if __name__ == '__main__':
    unittest.main()
