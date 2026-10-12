from pathlib import Path
import ctypes
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

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
