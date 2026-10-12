"""Windows multi-process single-instance and isolated controller boot coverage."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
import launcher_instance as single
import launcher_desktop as desktop

BOOT = "import sys; sys.path.insert(0, sys.argv[1]); from launcher_instance import LauncherInstance, wait_for_activation; import json\n"
OWNER = BOOT + """
instance = LauncherInstance(sys.argv[2])
print(json.dumps({'owned': instance.acquire(), 'event': instance.activation_event_name}), flush=True)
try:
    for line in sys.stdin:
        if line.strip() == 'activate':
            print(json.dumps({'activation': wait_for_activation(instance.activation_event_name, 1000)}), flush=True)
        elif line.strip() == 'empty':
            print(json.dumps({'activation': wait_for_activation(instance.activation_event_name, 0)}), flush=True)
        else:
            break
finally:
    instance.close()
"""
PROBE = BOOT + """
instance = LauncherInstance(sys.argv[2])
try:
    print(json.dumps({'owned': instance.acquire()}), flush=True)
finally:
    instance.close()
"""
RACE = BOOT + """
import time
from pathlib import Path
deadline = time.monotonic() + 10
while not Path(sys.argv[3]).exists():
    if time.monotonic() > deadline: raise RuntimeError('barrier timed out')
    time.sleep(.01)
instance = LauncherInstance(sys.argv[2])
try:
    owned = instance.acquire()
    print(json.dumps({'owned': owned}), flush=True)
    if owned: sys.stdin.readline()
finally:
    instance.close()
"""


@unittest.skipUnless(os.name == 'nt', 'Windows named kernel object coverage')
class WindowsInstanceTests(unittest.TestCase):
    def setUp(self):
        self.name = 'PvZQA.' + uuid.uuid4().hex
        qa = PROJECT.parent / 'qa'
        qa.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='instance-test-', dir=qa)
        self.addCleanup(self.tmp.cleanup)

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()

    def start(self, script=OWNER, name=None, *extra, cwd=None):
        process = subprocess.Popen([sys.executable, '-B', '-u', '-c', script, str(PROJECT), name or self.name, *extra],
                                   cwd=cwd or self.tmp.name, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(self.stop, process)
        return process

    def read(self, process):
        line = process.stdout.readline()
        if not line:
            self.fail('Worker produced no result: ' + process.stderr.read())
        return json.loads(line)

    def probe(self, name=None, cwd=None):
        process = self.start(PROBE, name, cwd=cwd)
        result = self.read(process)
        self.assertEqual(process.wait(timeout=3), 0)
        return result['owned']

    def test_second_process_exits_without_ownership_and_can_activate_existing_window(self):
        owner = self.start()
        self.assertTrue(self.read(owner)['owned'])
        self.assertFalse(self.probe())
        owner.stdin.write('activate\n'); owner.stdin.flush()
        self.assertTrue(self.read(owner)['activation'])
        owner.stdin.write('empty\n'); owner.stdin.flush()
        self.assertFalse(self.read(owner)['activation'], 'Activation event must auto-reset')

    def test_simultaneous_start_has_exactly_one_owner(self):
        barrier = str(Path(self.tmp.name) / 'go')
        processes = [self.start(RACE, None, barrier) for _ in range(4)]
        Path(barrier).write_text('go', encoding='utf-8')
        outcomes = [self.read(process)['owned'] for process in processes]
        self.assertEqual(sum(outcomes), 1)
        for process, owned in zip(processes, outcomes):
            if owned:
                process.stdin.write('close\n'); process.stdin.flush()
            self.assertEqual(process.wait(timeout=3), 0)
        self.assertTrue(self.probe())

    def test_normal_release_allows_a_new_controller(self):
        owner = self.start(); self.assertTrue(self.read(owner)['owned'])
        owner.stdin.write('close\n'); owner.stdin.flush()
        self.assertEqual(owner.wait(timeout=3), 0)
        self.assertTrue(self.probe())

    def test_acquire_is_idempotent_and_failed_controller_boot_releases_real_mutex(self):
        instance = single.LauncherInstance(self.name)
        self.addCleanup(instance.close)
        self.assertTrue(instance.acquire())
        self.assertTrue(instance.acquire())
        instance.close(); instance.close()
        self.assertTrue(self.probe())
        with patch.object(desktop, 'LauncherInstance', return_value=instance), \
                patch.object(desktop, 'LauncherService', side_effect=RuntimeError('fake boot failure')):
            with self.assertRaisesRegex(RuntimeError, 'fake boot failure'):
                desktop.run([])
        self.assertTrue(self.probe(), 'An exception before API/UI boot retained the native mutex')

    def test_process_death_releases_abandoned_mutex_even_if_an_observer_keeps_it_open(self):
        owner = self.start(); self.assertTrue(self.read(owner)['owned'])
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_wchar_p]
        kernel.OpenMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        observer = kernel.OpenMutexW(single.SYNCHRONIZE, False, 'Local\\' + self.name + '.Controller')
        self.assertTrue(observer)
        try:
            owner.kill(); owner.wait(timeout=3)
            self.assertTrue(self.probe())
        finally:
            kernel.CloseHandle(observer)

    def test_product_name_is_independent_of_working_directory_and_qa_names_are_isolated(self):
        owner = self.start(); self.assertTrue(self.read(owner)['owned'])
        other_root = Path(self.tmp.name) / 'another-game-directory'
        other_root.mkdir()
        self.assertFalse(self.probe(cwd=str(other_root)))
        self.assertTrue(self.probe(self.name + '.isolated'))
        with patch.dict(os.environ, {single.INSTANCE_ENV: self.name + '.env'}):
            instance = single.LauncherInstance()
            try:
                self.assertTrue(instance.acquire())
                self.assertEqual(instance.name, self.name + '.env')
            finally:
                instance.close(); instance.close()

    def test_module_does_not_require_optional_ctypes_wintypes(self):
        script = """
import builtins, sys
original = builtins.__import__
def checked(name, globals=None, locals=None, fromlist=(), level=0):
    if name == 'ctypes.wintypes' or (name == 'ctypes' and 'wintypes' in (fromlist or ())):
        raise ImportError('portable runtime lacks wintypes')
    return original(name, globals, locals, fromlist, level)
builtins.__import__ = checked
""" + PROBE
        process = self.start(script)
        self.assertTrue(self.read(process)['owned'])
        self.assertEqual(process.wait(timeout=3), 0)

    def test_partial_native_initialization_failure_closes_created_event(self):
        kernel = Mock()
        kernel.CreateEventW.return_value = 1234
        kernel.CreateMutexW.return_value = None
        with patch.object(single, '_kernel', return_value=kernel):
            instance = single.LauncherInstance(self.name)
            with self.assertRaises(OSError):
                instance.acquire()
            kernel.CloseHandle.assert_called_once_with(1234)
            kernel.ReleaseMutex.assert_not_called()

    def test_invalid_custom_names_are_rejected_before_native_handles(self):
        for name in ('', 'Global\\something', '../root', 'a/b', 'a'*129, 1):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    single.LauncherInstance(name)


class ControllerInstanceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.instance = Mock(activation_event_name='Local\\PvZQA.fixture.Activate')
        self.instance.acquire.return_value = True
        self.service = Mock(window_info_path=PROJECT.parent / 'qa' / 'fixture.window.json')
        self.server = Mock(url='http://127.0.0.1:12345/?token=fixture', origin='http://127.0.0.1:12345', token='fixture')
        self.thread = Mock()
        self.patches = []
        self.bind('LauncherInstance', return_value=self.instance)
        self.make_service = self.bind('LauncherService', return_value=self.service)
        self.make_server = self.bind('LauncherHTTPServer', return_value=self.server)
        self.make_thread = self.bind('threading.Thread', return_value=self.thread)
        shell = Mock(pid=1234, stderr=None)
        shell.wait.return_value = 0
        self.make_shell = self.bind('subprocess.Popen', return_value=shell)
        self.bind('os.environ', new={})

    def bind(self, name, **kwargs):
        item = patch('launcher_desktop.' + name, **kwargs)
        result = item.start()
        self.addCleanup(item.stop)
        return result

    def test_second_instance_does_not_create_service_api_guard_shell_or_connection_file(self):
        self.instance.acquire.return_value = False
        with patch.object(Path, 'write_text') as write:
            self.assertEqual(desktop.run(['--connection-file', 'never-created.json']), 0)
        for constructor in (self.make_service, self.make_server, self.make_thread, self.make_shell):
            constructor.assert_not_called()
        write.assert_not_called()

    def test_service_boot_failure_releases_lock_before_another_start(self):
        self.make_service.side_effect = RuntimeError('fake boot failure')
        with self.assertRaisesRegex(RuntimeError, 'fake boot failure'):
            desktop.run([])
        self.instance.close.assert_called_once()
        self.make_server.assert_not_called(); self.make_shell.assert_not_called()

    def test_api_constructor_failure_closes_created_service_and_lock(self):
        self.make_server.side_effect = OSError('fake bind failure')
        with self.assertRaisesRegex(OSError, 'fake bind failure'):
            desktop.run([])
        self.service.close.assert_called_once(); self.instance.close.assert_called_once()

    def test_thread_start_failure_closes_socket_without_shutdown_deadlock(self):
        self.thread.start.side_effect = RuntimeError('fake thread failure')
        with self.assertRaisesRegex(RuntimeError, 'fake thread failure'):
            desktop.run([])
        self.server.shutdown.assert_not_called()
        self.server.server_close.assert_called_once(); self.service.close.assert_called_once()
        self.instance.close.assert_called_once()

    def test_connection_file_write_failure_cleans_api_and_lock(self):
        with patch.object(Path, 'write_text', side_effect=OSError('fake metadata failure')):
            with self.assertRaisesRegex(OSError, 'fake metadata failure'):
                desktop.run(['--connection-file', 'fixture.json'])
        self.server.shutdown.assert_called_once(); self.server.server_close.assert_called_once()
        self.thread.join.assert_called_once_with(timeout=2)
        self.instance.close.assert_called_once(); self.make_shell.assert_not_called()

    def test_shell_receives_activation_event_and_monitor_record_path(self):
        with patch.object(Path, 'is_file', return_value=True):
            self.assertEqual(desktop.run([]), 0)
        env = self.make_shell.call_args.kwargs['env']
        self.assertEqual(env[single.ACTIVATION_ENV], self.instance.activation_event_name)
        self.assertEqual(env['PVZ_LAUNCHER_WINDOW_FILE'], str(self.service.window_info_path))
        self.assertEqual(self.service.ui_pid, 1234)
        self.service.close.assert_called_once(); self.server.server_close.assert_called_once()
        self.instance.close.assert_called_once()

    def test_cleanup_failure_still_closes_api_and_releases_lock(self):
        self.service.close.side_effect = RuntimeError('fake cleanup failure')
        with patch.object(Path, 'is_file', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'fake cleanup failure'):
                desktop.run([])
        self.server.shutdown.assert_called_once(); self.server.server_close.assert_called_once()
        self.instance.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
