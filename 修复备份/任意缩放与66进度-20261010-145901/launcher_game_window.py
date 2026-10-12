"""Place this launcher's own game window in its monitor's work area."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import zlib


def game_startup_options(player_kind):
    """Keep Adobe's first window hidden until it can be shown at its final position."""
    if sys.platform != 'win32' or player_kind != 'flash':
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # SW_HIDE; CREATE_NO_WINDOW only hides consoles.
    return {'startupinfo': startup}


def read_swf_stage(path):
    """Read only the bounded SWF RECT header, in logical stage pixels."""
    with Path(path).open('rb') as stream:
        raw = stream.read(4096)
    if len(raw) < 12 or raw[:3] not in (b'FWS', b'CWS'):
        raise ValueError('无法读取游戏加载器的窗口尺寸')
    try:
        data = (zlib.decompressobj().decompress(raw[8:], 64)
                if raw[:3] == b'CWS' else raw[8:72])
        bits = ''.join(format(byte, '08b') for byte in data)
        width = int(bits[:5], 2)
        if not 1 <= width <= 31 or len(bits) < 5 + width * 4:
            raise ValueError('Invalid SWF RECT')
        values = []
        for index in range(4):
            chunk = bits[5 + index * width:5 + (index + 1) * width]
            value = int(chunk, 2)
            values.append(value - (1 << width) if chunk[0] == '1' else value)
        size = (round((values[1] - values[0]) / 20),
                round((values[3] - values[2]) / 20))
        if not all(1 <= value <= 16384 for value in size):
            raise ValueError('Invalid SWF stage size')
        return size
    except (ValueError, zlib.error) as exc:
        raise ValueError('无法读取游戏加载器的窗口尺寸') from exc


def centered_position(work_area, size):
    left, top, right, bottom = work_area
    width, height = size
    return (left + max(0, (right - left - width) // 2),
            top + max(0, (bottom - top - height) // 2))


def read_owner_window(path, expected_pid, api):
    if not path or type(expected_pid) is not int or expected_pid <= 0:
        return 0
    try:
        with Path(path).open('r', encoding='utf-8') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return 0
        value = json.loads(raw)
        if (not isinstance(value, dict) or type(value.get('pid')) is not int or
                value['pid'] != expected_pid or type(value.get('hwnd')) is not int or
                value['hwnd'] <= 0):
            return 0
        hwnd = value['hwnd']
        return hwnd if api.window_pid(hwnd) == expected_pid else 0
    except (OSError, ValueError, TypeError):
        return 0


class WindowsWindowApi:
    """Win32 definitions use primitive ctypes supported by the portable runtime."""
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        handle, integer, boolean = ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32
        uint = ctypes.c_uint32

        class Rect(ctypes.Structure):
            _fields_ = [(key, integer) for key in ('left', 'top', 'right', 'bottom')]

        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', uint), ('monitor', Rect), ('work', Rect), ('flags', uint)]

        self.Rect, self.MonitorInfo = Rect, MonitorInfo
        self.enum_callback = ctypes.WINFUNCTYPE(boolean, handle, ctypes.c_ssize_t)
        signatures = {
            'EnumWindows': ([self.enum_callback, ctypes.c_ssize_t], boolean),
            'IsWindowVisible': ([handle], boolean),
            'IsIconic': ([handle], boolean),
            'GetWindow': ([handle, uint], handle),
            'GetWindowThreadProcessId': ([handle, ctypes.POINTER(uint)], uint),
            'GetWindowRect': ([handle, ctypes.POINTER(Rect)], boolean),
            'GetClientRect': ([handle, ctypes.POINTER(Rect)], boolean),
            'GetClassNameW': ([handle, ctypes.c_wchar_p, integer], integer),
            'MonitorFromWindow': ([handle, uint], handle),
            'GetMonitorInfoW': ([handle, ctypes.POINTER(MonitorInfo)], boolean),
            'ShowWindow': ([handle, integer], boolean),
            'SetWindowPos': ([handle, handle, integer, integer, integer, integer, uint], boolean),
            'BringWindowToTop': ([handle], boolean),
            'SetForegroundWindow': ([handle], boolean),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        if hasattr(self.user, 'SetThreadDpiAwarenessContext'):
            self.user.SetThreadDpiAwarenessContext.argtypes = [handle]
            self.user.SetThreadDpiAwarenessContext.restype = handle

    def window_pid(self, hwnd):
        pid = ctypes.c_uint32()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def bounds(self, hwnd):
        rect = self.Rect()
        if not self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom

    def window_class(self, hwnd):
        name = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, name, len(name))
        return name.value

    def client_size(self, hwnd):
        # Adobe is DPI-unaware. Its SWF stage uses logical pixels even when its
        # physical bounds are scaled on a 150%/200% display.
        function = getattr(self.user, 'SetThreadDpiAwarenessContext', None)
        previous = function(ctypes.c_void_p(-1)) if function else None
        try:
            rect = self.Rect()
            if not self.user.GetClientRect(hwnd, ctypes.byref(rect)):
                return None
            return rect.right - rect.left, rect.bottom - rect.top
        finally:
            self.restore_coordinates(previous)

    def find_window(self, pid, *, include_hidden=False):
        candidates = []
        def collect(hwnd, _data):
            if (self.window_pid(hwnd) == pid and
                    (include_hidden or self.user.IsWindowVisible(hwnd)) and
                    not self.user.GetWindow(hwnd, 4)):
                kind = self.window_class(hwnd)
                # Adobe also creates a large hidden Messaging/Msg helper. Never
                # reveal that window; hidden startup is used only for Adobe.
                if kind == 'Messaging' or (include_hidden and kind != 'ShockwaveFlash'):
                    return True
                rect = self.bounds(hwnd)
                if rect and rect[2] - rect[0] >= 250 and rect[3] - rect[1] >= 180:
                    candidates.append((kind == 'ShockwaveFlash',
                                       (rect[2] - rect[0]) * (rect[3] - rect[1]), hwnd))
            return True
        callback = self.enum_callback(collect)
        self.user.EnumWindows(callback, 0)
        return max(candidates, default=(False, 0, 0))[2]

    def work_area(self, owner, game):
        monitor = self.user.MonitorFromWindow(owner or game, 2)
        info = self.MonitorInfo()
        info.size = ctypes.sizeof(info)
        if not monitor or not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return info.work.left, info.work.top, info.work.right, info.work.bottom

    def position(self, hwnd, x, y, *, activate):
        if activate and self.user.IsIconic(hwnd):
            self.user.ShowWindow(hwnd, 9)
        # HWND_TOP raises the game above the launcher, without making it a
        # permanently topmost window over the user's other applications.
        flags = 0x0001 | (0x0040 if activate else 0x0010)
        if not self.user.SetWindowPos(hwnd, None, x, y, 0, 0, flags):
            raise OSError(ctypes.get_last_error(), 'Cannot position game window')
        if activate:
            self.user.BringWindowToTop(hwnd)
            self.user.SetForegroundWindow(hwnd)

    def physical_coordinates(self):
        function = getattr(self.user, 'SetThreadDpiAwarenessContext', None)
        return function(ctypes.c_void_p(-4)) if function else None

    def restore_coordinates(self, previous):
        if previous:
            self.user.SetThreadDpiAwarenessContext(previous)


def place_game_window(pid, *, owner_file=None, owner_pid=None, is_current=lambda: True,
                      cancel=None, timeout=12, api=None, clock=time.monotonic,
                      initially_hidden=False, stage_size=None):
    """Wait for the owned game HWND and its initial client-driven resize.

    Adobe stays hidden while its loader determines the window size. The first
    SetWindowPos both centers and reveals it; no visible follow-up move occurs.
    Other players retain the existing visible-window placement behavior.
    """
    if type(pid) is not int or pid <= 0 or (api is None and sys.platform != 'win32'):
        return False
    if initially_hidden and (not isinstance(stage_size, tuple) or len(stage_size) != 2 or
                             not all(type(value) is int and 1 <= value <= 16384 for value in stage_size)):
        raise ValueError('隐藏启动需要加载器的窗口尺寸')
    api = api or WindowsWindowApi()
    previous = api.physical_coordinates()
    last_size = None
    stable_since = None
    last_centered = None
    first_placed = None
    deadline = clock() + timeout
    try:
        while clock() < deadline and is_current() and not (cancel and cancel.is_set()):
            hwnd = (api.find_window(pid, include_hidden=True) if initially_hidden
                    else api.find_window(pid))
            rect = api.bounds(hwnd) if hwnd else None
            if rect and api.window_pid(hwnd) == pid:
                size = (rect[2] - rect[0], rect[3] - rect[1])
                if (hwnd, size) != last_size:
                    last_size, stable_since = (hwnd, size), clock()
                client = api.client_size(hwnd) if initially_hidden else None
                ready = (not initially_hidden or
                         (client and all(abs(actual - expected) <= 1
                                         for actual, expected in zip(client, stage_size))))
                if ready and last_centered != (hwnd, size) and clock() - stable_since >= .4:
                    owner = read_owner_window(owner_file, owner_pid, api)
                    area = api.work_area(owner, hwnd)
                    if area and is_current() and not (cancel and cancel.is_set()):
                        x, y = centered_position(area, size)
                        api.position(hwnd, x, y, activate=first_placed is None)
                        last_centered = (hwnd, size)
                        if first_placed is None:
                            first_placed = clock()
                        if initially_hidden:
                            return True
            if first_placed is not None and clock() - first_placed >= 4:
                return True
            if cancel:
                cancel.wait(.1)
            else:
                time.sleep(.1)
        return first_placed is not None
    finally:
        api.restore_coordinates(previous)
