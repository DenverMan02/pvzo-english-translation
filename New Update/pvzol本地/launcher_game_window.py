"""Place this launcher's own game window in its monitor's work area."""
from __future__ import annotations

import ctypes
import json
import math
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


def fitted_window_size(work_area, outer_size, client_size):
    """Fit the viewport proportionally, keeping the native frame unscaled."""
    width, height = outer_size
    if not client_size or min(client_size) <= 0:
        return outer_size
    client_width, client_height = client_size
    frame_width, frame_height = max(0, width - client_width), max(0, height - client_height)
    available_width = max(1, work_area[2] - work_area[0] - frame_width)
    available_height = max(1, work_area[3] - work_area[1] - frame_height)
    scale = min(1, available_width / client_width, available_height / client_height)
    return (max(1, math.floor(client_width * scale)) + frame_width,
            max(1, math.floor(client_height * scale)) + frame_height)


def ruffle_viewport_size(stage_size, work_area, dpi, frame_size):
    """Match Adobe's DPI-scaled stage, preserving its ratio on small monitors."""
    width, height = stage_size
    scale = max(1, dpi) / 96
    menu = round(24 * scale)  # Ruffle desktop 0.6's egui MENU_HEIGHT.
    available_width = max(1, work_area[2] - work_area[0] - frame_size[0])
    available_height = max(1, work_area[3] - work_area[1] - frame_size[1] - menu)
    scale = min(scale, available_width / width, available_height / height)
    return max(1, math.floor(width * scale)), max(1, math.floor(height * scale))


def ruffle_initial_size(stage_size, *, owner_file=None, owner_pid=None, api=None):
    """Choose physical CLI viewport dimensions on the launcher's monitor."""
    if api is None and sys.platform != 'win32':
        return stage_size
    api = api or WindowsWindowApi()
    previous = api.physical_coordinates()
    try:
        owner = read_owner_window(owner_file, owner_pid, api)
        dpi = api.window_dpi(owner)
        area = api.work_area(owner, 0)
        return (ruffle_viewport_size(stage_size, area, dpi, api.frame_size(dpi))
                if area else stage_size)
    finally:
        api.restore_coordinates(previous)


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
            'GetDpiForWindow': ([handle], uint),
            'MonitorFromWindow': ([handle, uint], handle),
            'GetMonitorInfoW': ([handle, ctypes.POINTER(MonitorInfo)], boolean),
            'ShowWindow': ([handle, integer], boolean),
            'SetWindowPos': ([handle, handle, integer, integer, integer, integer, uint], boolean),
            'BringWindowToTop': ([handle], boolean),
            'SetForegroundWindow': ([handle], boolean),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name, None)
            if function is None:
                continue
            function.argtypes, function.restype = args, result
        metrics = getattr(self.user, 'GetSystemMetricsForDpi', None)
        if metrics:
            metrics.argtypes, metrics.restype = [integer, uint], integer
        self.user.GetSystemMetrics.argtypes = [integer]
        self.user.GetSystemMetrics.restype = integer
        if hasattr(self.user, 'SetThreadDpiAwarenessContext'):
            self.user.SetThreadDpiAwarenessContext.argtypes = [handle]
            self.user.SetThreadDpiAwarenessContext.restype = handle
        if hasattr(self.user, 'GetWindowDpiAwarenessContext'):
            self.user.GetWindowDpiAwarenessContext.argtypes = [handle]
            self.user.GetWindowDpiAwarenessContext.restype = handle

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

    def window_dpi(self, hwnd):
        get_dpi = getattr(self.user, 'GetDpiForWindow', None)
        if hwnd and get_dpi:
            dpi = get_dpi(hwnd)
            if dpi:
                return dpi
        # GetDpiForSystem is virtualized for the portable controller. Query the
        # selected monitor while our placement thread is per-monitor aware.
        try:
            shcore = ctypes.WinDLL('shcore', use_last_error=True)
            shcore.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                               ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
            shcore.GetDpiForMonitor.restype = ctypes.c_long
            x, y = ctypes.c_uint(), ctypes.c_uint()
            monitor = self.user.MonitorFromWindow(hwnd, 2)
            if shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(x), ctypes.byref(y)) == 0 and x.value:
                return x.value
        except (AttributeError, OSError):
            pass
        return 96

    def frame_size(self, dpi):
        get_metrics = getattr(self.user, 'GetSystemMetricsForDpi', None)
        metric = (lambda index: get_metrics(index, dpi)) if get_metrics else self.user.GetSystemMetrics
        horizontal = metric(32) + metric(92)
        vertical = metric(33) + metric(92)
        return 2 * horizontal, 2 * vertical + metric(4)

    def visible(self, hwnd):
        return bool(self.user.IsWindowVisible(hwnd))

    def client_size(self, hwnd):
        # Measure in the player's own coordinate space. App compatibility can
        # override Adobe's usual DPI-unaware mode, even on the same monitor.
        get_context = getattr(self.user, 'GetWindowDpiAwarenessContext', None)
        context = get_context(hwnd) if get_context else None
        function = getattr(self.user, 'SetThreadDpiAwarenessContext', None)
        previous = function(ctypes.c_void_p(context or -1)) if function else None
        try:
            return self.physical_client_size(hwnd)
        finally:
            self.restore_coordinates(previous)

    def physical_client_size(self, hwnd):
        # Uses the caller's context: placement runs in physical coordinates;
        # client_size above temporarily uses the player's logical coordinates.
        rect = self.Rect()
        if not self.user.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        return rect.right - rect.left, rect.bottom - rect.top

    def find_window(self, pid, *, include_hidden=False, player_kind=None):
        candidates = []
        def collect(hwnd, _data):
            if (self.window_pid(hwnd) == pid and
                    (include_hidden or self.user.IsWindowVisible(hwnd)) and
                    not self.user.GetWindow(hwnd, 4)):
                kind = self.window_class(hwnd)
                # Adobe also creates a large hidden Messaging/Msg helper. Never
                # reveal that window; hidden startup is used only for Adobe.
                expected = 'Window Class' if player_kind == 'ruffle' else 'ShockwaveFlash'
                if kind == 'Messaging' or (include_hidden and kind != expected):
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

    def move_hidden(self, hwnd, x, y):
        self.position(hwnd, x, y, activate=False)

    def position(self, hwnd, x, y, *, activate, size=None):
        if activate and self.user.IsIconic(hwnd):
            self.user.ShowWindow(hwnd, 9)
        # HWND_TOP raises the game above the launcher, without making it a
        # permanently topmost window over the user's other applications.
        flags = (0x0001 if size is None else 0) | (0x0040 if activate else 0x0010)
        width, height = size or (0, 0)
        if not self.user.SetWindowPos(hwnd, None, x, y, width, height, flags):
            raise OSError(ctypes.get_last_error(), 'Cannot position game window')
        if activate:
            self.focus(hwnd)

    def focus(self, hwnd):
        # A visible Ruffle window needs no SetWindowPos or second WM_SIZE.
        self.user.BringWindowToTop(hwnd)
        self.user.SetForegroundWindow(hwnd)

    def physical_coordinates(self):
        function = getattr(self.user, 'SetThreadDpiAwarenessContext', None)
        if function:
            previous = function(ctypes.c_void_p(-4))
            # Windows 10 1607 supports per-monitor v1, but not v2 yet.
            return previous or function(ctypes.c_void_p(-3))
        return None

    def restore_coordinates(self, previous):
        if previous:
            self.user.SetThreadDpiAwarenessContext(previous)


def place_game_window(pid, *, owner_file=None, owner_pid=None, is_current=lambda: True,
                      cancel=None, timeout=12, api=None, clock=time.monotonic,
                      initially_hidden=False, stage_size=None, ruffle_size=None,
                      on_prepared=None):
    """Wait for the owned game HWND and its initial client-driven resize.

    Adobe stays hidden while its loader determines the window size. The first
    SetWindowPos both centers and reveals it; no visible follow-up move occurs.
    Visible players are fitted and centered once stable, then observed briefly
    for loader-driven size changes without undoing same-size user moves.
    """
    if type(pid) is not int or pid <= 0 or (api is None and sys.platform != 'win32'):
        return False
    if initially_hidden and (not isinstance(stage_size, tuple) or len(stage_size) != 2 or
                             not all(type(value) is int and 1 <= value <= 16384 for value in stage_size)):
        raise ValueError('隐藏启动需要加载器的窗口尺寸')
    api = api or WindowsWindowApi()
    previous = api.physical_coordinates()
    if ruffle_size is not None:
        try:
            return _place_ruffle_window(pid, ruffle_size, owner_file, owner_pid,
                                        is_current, cancel, timeout, api, clock, on_prepared)
        finally:
            api.restore_coordinates(previous)
    last_size = None
    stable_since = None
    found_since = None
    found_hwnd = None
    relocated = None
    last_centered = None
    first_placed = None
    deadline = clock() + timeout
    try:
        while clock() < deadline and is_current() and not (cancel and cancel.is_set()):
            hwnd = (api.find_window(pid, include_hidden=True) if initially_hidden
                    else api.find_window(pid))
            rect = api.bounds(hwnd) if hwnd else None
            if rect and api.window_pid(hwnd) == pid:
                if hwnd != found_hwnd:
                    found_hwnd = hwnd
                    found_since = clock()
                size = (rect[2] - rect[0], rect[3] - rect[1])
                if (hwnd, size) != last_size:
                    last_size, stable_since = (hwnd, size), clock()
                client = api.client_size(hwnd) if initially_hidden else None
                ready = (not initially_hidden or
                         (client and all(abs(actual - expected) <= 2
                                         for actual, expected in zip(client, stage_size))))
                # Loader dimensions are a useful early-ready hint, never a
                # requirement for opening the game. Fractional DPI rounding,
                # compatibility overrides and a small work area can all change
                # the final client size. Reveal the stable, owned Flash window
                # after a short bounded wait instead of killing a healthy game.
                if initially_hidden and clock() - found_since >= 2:
                    ready = True
                if ready and last_centered != (hwnd, size) and clock() - stable_since >= .4:
                    owner = read_owner_window(owner_file, owner_pid, api)
                    area = api.work_area(owner, hwnd)
                    if area and is_current() and not (cancel and cancel.is_set()):
                        # Moving between monitors can change Adobe's physical
                        # size. Let that settle while still hidden, then measure
                        # the destination's frame and viewport before revealing.
                        if initially_hidden and relocated != (hwnd, area) and hasattr(api, 'move_hidden'):
                            x, y = centered_position(area, size)
                            api.move_hidden(hwnd, x, y)
                            relocated = (hwnd, area)
                            stable_since = clock()
                            continue
                        fitted = (fitted_window_size(area, size, api.physical_client_size(hwnd))
                                  if hasattr(api, 'physical_client_size') else size)
                        x, y = centered_position(area, fitted)
                        options = {'size': fitted} if fitted != size else {}
                        api.position(hwnd, x, y, activate=first_placed is None, **options)
                        # Record the requested size, so our own fit does not
                        # trigger another placement after the native resize.
                        last_centered = (hwnd, fitted)
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


def _place_ruffle_window(pid, viewport, owner_file, owner_pid, is_current,
                         cancel, timeout, api, clock, on_prepared=None):
    """Position Ruffle's owned hidden HWND before it reveals the SWF metadata.

    Ruffle creates its main window hidden while it initializes its renderer.
    Its later metadata resize uses our exact physical CLI dimensions; moving
    that hidden window first avoids a visible jump from Windows' cascade slot.
    Ruffle retains ownership of revealing it, including on cancellation/error.
    """
    if (not isinstance(viewport, tuple) or len(viewport) != 2 or
            not all(type(value) is int and 1 <= value <= 16384 for value in viewport)):
        raise ValueError('Ruffle 启动需要有效的物理窗口尺寸')
    deadline = clock() + timeout
    positioned = False
    while clock() < deadline and is_current() and not (cancel and cancel.is_set()):
        hwnd = api.find_window(pid, include_hidden=True, player_kind='ruffle')
        rect = api.bounds(hwnd) if hwnd else None
        if rect and api.window_pid(hwnd) == pid:
            if not positioned:
                if on_prepared is not None and api.visible(hwnd):
                    raise ValueError('Ruffle 在启动定位完成前已显示，启动门未生效')
                client = api.physical_client_size(hwnd)
                if client:
                    frame = (rect[2] - rect[0] - client[0], rect[3] - rect[1] - client[1])
                    owner = read_owner_window(owner_file, owner_pid, api)
                    game_dpi = api.window_dpi(hwnd)
                    target_dpi = api.window_dpi(owner) if owner else game_dpi
                    # The newly created window may start on another monitor.
                    # Its current frame is measured at that monitor's DPI;
                    # use destination metrics before its first hidden move.
                    if target_dpi != game_dpi:
                        frame = api.frame_size(target_dpi)
                    menu = round(24 * target_dpi / 96)
                    outer = (viewport[0] + frame[0], viewport[1] + menu + frame[1])
                    area = api.work_area(owner, hwnd)
                    if area and is_current() and not (cancel and cancel.is_set()):
                        x, y = centered_position(area, outer)
                        api.position(hwnd, x, y, activate=False, size=outer)
                        positioned = True
                        if on_prepared is not None:
                            if not is_current() or (cancel and cancel.is_set()):
                                return False
                            if not on_prepared():
                                raise ValueError('Ruffle 启动定位已超时，请重试')
            if positioned and api.visible(hwnd) and is_current() and not (cancel and cancel.is_set()):
                # The player has revealed itself. No native move/resize call
                # follows: a fast user maximize or drag is never undone.
                api.focus(hwnd)
                return True
        if cancel:
            cancel.wait(.01)
        else:
            time.sleep(.01)
    return False
