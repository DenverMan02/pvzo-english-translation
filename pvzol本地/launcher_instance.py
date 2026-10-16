"""One PVZ launcher controller per Windows logon session.

Named kernel objects use a product-wide name, independent of game directories.
Only primitive ctypes types are used because the portable interpreter does not
include ctypes.wintypes. The controller keeps the owner handles until it exits.
"""
from __future__ import annotations

import ctypes
import os
import re

INSTANCE_ENV = 'PVZ_LAUNCHER_INSTANCE_NAME'
ACTIVATION_ENV = 'PVZ_LAUNCHER_ACTIVATE_EVENT'
DEFAULT_NAME = 'PvZOLLauncher'
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 0x102
SYNCHRONIZE = 0x00100000


def _kernel():
    if os.name != 'nt':
        raise OSError('Single-instance protection for the desktop launcher requires Windows.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = ctypes.c_void_p
    dword = ctypes.c_uint32
    boolean = ctypes.c_int32
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, boolean, boolean, ctypes.c_wchar_p]
    kernel.CreateEventW.restype = handle
    kernel.OpenEventW.argtypes = [dword, boolean, ctypes.c_wchar_p]
    kernel.OpenEventW.restype = handle
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, boolean, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = handle
    kernel.WaitForSingleObject.argtypes = [handle, dword]
    kernel.WaitForSingleObject.restype = dword
    for name in ('ReleaseMutex', 'SetEvent', 'CloseHandle'):
        function = getattr(kernel, name)
        function.argtypes = [handle]
        function.restype = boolean
    return kernel


def _failure(operation):
    return ctypes.WinError(ctypes.get_last_error(), operation + ' failed')


def _allow_foreground_activation():
    # The user's second double-click grants the already-running window permission
    # to take foreground focus, including when it was minimized to the tray.
    try:
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.AllowSetForegroundWindow.argtypes = [ctypes.c_uint32]
        user.AllowSetForegroundWindow.restype = ctypes.c_int32
        user.AllowSetForegroundWindow(0xffffffff)  # ASFW_ANY
    except (AttributeError, OSError):
        pass


class LauncherInstance:
    def __init__(self, name=None):
        self.name = os.environ.get(INSTANCE_ENV, DEFAULT_NAME) if name is None else name
        if not isinstance(self.name, str) or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', self.name) is None:
            raise ValueError('Launcher instance names must be 1–128 characters and may contain letters, digits, dots, hyphens, or underscores.')
        self.mutex_name = 'Local\\' + self.name + '.Controller'
        self.activation_event_name = 'Local\\' + self.name + '.Activate'
        self._kernel = None
        self._mutex = None
        self._event = None
        self._owned = False

    def acquire(self):
        """Acquire ownership, or signal the existing UI and return False."""
        if self._owned:
            return True
        kernel = self._kernel = _kernel()
        try:
            # Publish the event first. Both racing launchers hold it before the
            # winner owns the mutex, so an early activation signal stays alive
            # until the winner's desktop shell opens the event.
            self._event = kernel.CreateEventW(None, False, False, self.activation_event_name)
            if not self._event:
                raise _failure('CreateEventW')
            self._mutex = kernel.CreateMutexW(None, False, self.mutex_name)
            if not self._mutex:
                raise _failure('CreateMutexW')
            result = kernel.WaitForSingleObject(self._mutex, 0)
            if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
                self._owned = True
                return True
            if result != WAIT_TIMEOUT:
                raise _failure('WaitForSingleObject')
            _allow_foreground_activation()
            if not kernel.SetEvent(self._event):
                raise _failure('SetEvent')
            self.close()
            return False
        except BaseException:
            self.close()
            raise

    def close(self):
        """Release ownership and all handles. Repeated cleanup is harmless."""
        kernel = self._kernel
        if kernel is None:
            return
        try:
            if self._owned and self._mutex:
                kernel.ReleaseMutex(self._mutex)
        finally:
            self._owned = False
            if self._mutex:
                kernel.CloseHandle(self._mutex)
                self._mutex = None
            if self._event:
                kernel.CloseHandle(self._event)
                self._event = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_args):
        self.close()


def wait_for_activation(event_name, timeout_ms=1000):
    """Open an owner's auto-reset event and consume one activation signal."""
    if not isinstance(event_name, str) or not event_name.startswith('Local\\'):
        raise ValueError('Activation events must be in the current Windows session.')
    if type(timeout_ms) is not int or not 0 <= timeout_ms < 0xffffffff:
        raise ValueError('Invalid activation timeout.')
    kernel = _kernel()
    handle = kernel.OpenEventW(SYNCHRONIZE, False, event_name)
    if not handle:
        raise _failure('OpenEventW')
    try:
        result = kernel.WaitForSingleObject(handle, timeout_ms)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        raise _failure('WaitForSingleObject')
    finally:
        kernel.CloseHandle(handle)
