from pathlib import Path
import ctypes
import json
import sys
import tempfile
import threading
import unittest
import zlib
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import launcher_game_window as window


class FakeApi:
    def __init__(self):
        self.moves = []
        self.restored = []
        self.owner = 0
        self.rect = (0, 0, 1200, 800)
    def window_pid(self, hwnd): return {9: 90, 10: 100}.get(hwnd, 0)
    def physical_coordinates(self): return 77
    def restore_coordinates(self, previous): self.restored.append(previous)
    def find_window(self, pid): return 10 if pid == 100 else 0
    def bounds(self, hwnd): return self.rect if hwnd else None
    def client_size(self, hwnd): return (1440, 900)
    def work_area(self, owner, game):
        self.owner = owner
        return (-1920, 0, 0, 1040)
    def position(self, hwnd, x, y, activate): self.moves.append((hwnd, x, y, activate))


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class GameWindowTests(unittest.TestCase):
    def test_center_uses_work_area_and_handles_negative_monitor_coordinates(self):
        self.assertEqual(window.centered_position((0, 0, 3840, 2088), (2160, 1440)), (840, 324))
        self.assertEqual(window.centered_position((-1920, -1080, 0, -40), (1200, 800)), (-1560, -960))
        self.assertEqual(window.centered_position((0, 0, 800, 600), (1200, 900)), (0, 0))

    def owner_file(self, temp, data):
        path = Path(temp) / 'window.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_owner_record_requires_exact_live_shell_pid_and_valid_hwnd(self):
        api = FakeApi()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.owner_file(tmp, {'pid': 90, 'hwnd': 9})
            self.assertEqual(window.read_owner_window(path, 90, api), 9)
            for data in ({'pid': 91, 'hwnd': 9}, {'pid': 90, 'hwnd': 10},
                         {'pid': True, 'hwnd': 9}, {'pid': 90, 'hwnd': '9'},
                         {'pid': 90, 'hwnd': 0}, []):
                self.owner_file(tmp, data)
                self.assertEqual(window.read_owner_window(path, 90, api), 0)
            path.write_text('bad json', encoding='utf-8')
            self.assertEqual(window.read_owner_window(path, 90, api), 0)
            path.write_text(' ' * 5000, encoding='utf-8')
            self.assertEqual(window.read_owner_window(path, 90, api), 0)
            self.assertEqual(window.read_owner_window(None, 90, api), 0)

    def place(self, api, **kwargs):
        clock = Clock()
        with patch.object(window.time, 'sleep', clock.sleep):
            result = window.place_game_window(100, api=api, clock=clock, **kwargs)
        return result

    def test_centers_once_raises_to_front_and_restores_dpi_context(self):
        api = FakeApi()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.owner_file(tmp, {'pid': 90, 'hwnd': 9})
            self.assertTrue(self.place(api, owner_file=path, owner_pid=90))
        self.assertEqual(api.owner, 9)
        self.assertEqual(api.moves, [(10, -1560, 120, True)])
        self.assertEqual(api.restored, [77])

    def test_invalid_owner_falls_back_to_game_monitor(self):
        api = FakeApi()
        self.assertTrue(self.place(api, owner_file='missing.json', owner_pid=90))
        self.assertEqual(api.owner, 0)

    def test_initial_loader_resize_is_recentered_without_repeated_focus(self):
        api, clock = FakeApi(), Clock()
        def sleep(seconds):
            clock.sleep(seconds)
            if clock.now > 1:
                api.rect = (0, 0, 1600, 900)
        with patch.object(window.time, 'sleep', sleep):
            self.assertTrue(window.place_game_window(100, api=api, clock=clock))
        self.assertEqual(api.moves, [(10, -1560, 120, True), (10, -1760, 70, False)])

    def test_a_user_move_without_resize_is_not_undone(self):
        api, clock = FakeApi(), Clock()
        def sleep(seconds):
            clock.sleep(seconds)
            if clock.now > 1: api.rect = (80, 80, 1280, 880)
        with patch.object(window.time, 'sleep', sleep):
            self.assertTrue(window.place_game_window(100, api=api, clock=clock))
        self.assertEqual(len(api.moves), 1)

    def test_old_session_and_closed_game_do_not_move_any_window(self):
        api = FakeApi()
        self.assertFalse(self.place(api, is_current=lambda: False))
        self.assertEqual(api.moves, [])
        api = FakeApi()
        api.window_pid = lambda _hwnd: 999
        self.assertFalse(self.place(api, timeout=1))
        self.assertEqual(api.moves, [])

    def test_cancel_and_timeout_do_not_move_or_wait_indefinitely(self):
        api = FakeApi()
        cancel = threading.Event(); cancel.set()
        self.assertFalse(window.place_game_window(100, api=api, cancel=cancel))
        self.assertEqual(api.moves, [])
        api.find_window = lambda _pid: 0
        self.assertFalse(self.place(api, timeout=.5))
        self.assertEqual(api.restored, [77, 77])

    def test_a_positioning_error_still_restores_thread_dpi_context(self):
        api = FakeApi()
        def fail(*_args, **_kwargs): raise OSError('fixture fail')
        api.position = fail
        with self.assertRaises(OSError): self.place(api)
        self.assertEqual(api.restored, [77])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows startup info')
    def test_adobe_is_hidden_at_process_creation_but_other_players_are_unaffected(self):
        startup = window.game_startup_options('flash')['startupinfo']
        self.assertTrue(startup.dwFlags & window.subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(startup.wShowWindow, 0)
        self.assertEqual(window.game_startup_options('ruffle'), {})
        with patch.object(window.sys, 'platform', 'linux'):
            self.assertEqual(window.game_startup_options('flash'), {})

    def test_hidden_flash_reveals_only_after_initial_resize_and_never_moves_after_reveal(self):
        api, clock = FakeApi(), Clock()
        queries = []
        def find(pid, *, include_hidden=False):
            queries.append(include_hidden)
            return 10
        api.find_window = find
        def sleep(seconds):
            clock.sleep(seconds)
            if clock.now >= .2:
                api.rect = (80, 80, 1680, 980)
        with patch.object(window.time, 'sleep', sleep):
            self.assertTrue(window.place_game_window(100, api=api, clock=clock, initially_hidden=True,
                                                     stage_size=(1440, 900)))
        self.assertTrue(all(queries))
        self.assertEqual(api.moves, [(10, -1760, 70, True)])
        self.assertGreaterEqual(clock.now, .6)
        self.assertLess(clock.now, 1)
        self.assertEqual(api.restored, [77])

    def test_hidden_window_selection_never_reveals_adobe_messaging_helper(self):
        api = object.__new__(window.WindowsWindowApi)
        api.user = Mock()
        api.enum_callback = lambda callback: callback
        api.user.EnumWindows.side_effect = lambda callback, _data: [callback(hwnd, 0) for hwnd in (1, 2, 3, 4)]
        api.user.IsWindowVisible.return_value = False
        api.user.GetWindow.return_value = 0
        api.window_pid = lambda hwnd: 100 if hwnd != 4 else 200
        api.window_class = lambda hwnd: {1: 'Messaging', 2: 'ShockwaveFlash', 3: 'OtherDialog', 4: 'ShockwaveFlash'}[hwnd]
        api.bounds = lambda hwnd: (0, 0, 2880, 1523) if hwnd != 2 else (0, 0, 1560, 1241)
        self.assertEqual(api.find_window(100, include_hidden=True), 2)
        self.assertEqual(api.find_window(100), 0)
        api.window_class = lambda hwnd: 'Messaging'
        self.assertEqual(api.find_window(100, include_hidden=True), 0)

    def test_cancelled_hidden_start_never_reveals_a_window(self):
        api = FakeApi()
        cancel = threading.Event(); cancel.set()
        self.assertFalse(window.place_game_window(100, api=api, cancel=cancel, initially_hidden=True,
                                                 stage_size=(1440, 900)))
        self.assertEqual(api.moves, [])

    def test_slow_loader_default_size_is_never_shown_even_when_stable(self):
        api, clock = FakeApi(), Clock()
        api.find_window = lambda pid, **kwargs: 10
        api.rect = (78, 78, 2958, 1601)
        api.client_size = lambda hwnd: (1904, 956) if clock.now < 2 else (1440, 900)
        def sleep(seconds):
            clock.sleep(seconds)
            if clock.now >= 2:
                api.rect = (78, 78, 2262, 1517)
        with patch.object(window.time, 'sleep', sleep):
            self.assertTrue(window.place_game_window(100, api=api, clock=clock, initially_hidden=True,
                                                     stage_size=(1440, 900)))
        self.assertGreaterEqual(clock.now, 2.4)
        self.assertEqual(api.moves, [(10, -1920, 0, True)])

    def test_hidden_timeout_on_unloaded_stage_never_reveals_default_window(self):
        api = FakeApi()
        api.find_window = lambda pid, **kwargs: 10
        api.client_size = lambda hwnd: (1904, 956)
        self.assertFalse(self.place(api, initially_hidden=True, stage_size=(1440, 900), timeout=1))
        self.assertEqual(api.moves, [])
        with self.assertRaises(ValueError):
            self.place(api, initially_hidden=True)

    def test_reads_compressed_and_uncompressed_stage_with_signed_twips(self):
        values = (-100, 28700, -200, 17800)
        bits = '10000' + ''.join(format(value & 0xffff, '016b') for value in values)
        bits += '0' * (-len(bits) % 8)
        rect = int(bits, 2).to_bytes(len(bits) // 8, 'big') + b'\x00\x0c\x01\x00'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'loader.swf'
            for signature in (b'FWS', b'CWS'):
                payload = zlib.compress(rect) if signature == b'CWS' else rect
                path.write_bytes(signature + b'\x0a' + (8 + len(rect)).to_bytes(4, 'little') + payload)
                self.assertEqual(window.read_swf_stage(path), (1440, 900))
            for invalid in (b'', b'CWS bad-header', b'FWS\x0a\x0f\0\0\0\xff\xff\xff\xff'):
                path.write_bytes(invalid)
                with self.assertRaises(ValueError): window.read_swf_stage(path)

    @unittest.skipUnless(sys.platform == 'win32', 'Win32 bindings')
    def test_primitive_win32_bindings_load_in_the_native_runtime(self):
        api = window.WindowsWindowApi()
        self.assertEqual(ctypes.sizeof(api.Rect), 16)
        previous = api.physical_coordinates()
        try:
            area = api.work_area(0, 0)
            self.assertIsNotNone(area)
            self.assertGreater(area[2], area[0])
        finally:
            api.restore_coordinates(previous)


if __name__ == '__main__': unittest.main()
