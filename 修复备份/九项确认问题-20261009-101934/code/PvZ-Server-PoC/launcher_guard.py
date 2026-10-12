"""Share the launcher's lifecycle write lock with its local GM subprocess.

Only the controller creates the environment and state path. HTTP input must not
select either. A missing or invalid state fails closed while the owner is alive;
after its exit the GM server can fall back to its own game-port check.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import tempfile

GUARD_ENV = 'PVZ_LAUNCHER_GUARD'
OWNER_ENV = 'PVZ_LAUNCHER_OWNER_PID'
MAX_GUARD_BYTES = 4096
_MAX_PID = 0xffffffff
# The portable runtime includes ctypes but omits its optional wintypes module.
# Windows DWORD/BOOL stay 32-bit, while HANDLE follows the process pointer size.
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int32
_HANDLE = ctypes.c_void_p


def _valid_pid(value):
    return type(value) is int and 0 < value <= _MAX_PID


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate lifecycle field')
        result[key] = value
    return result


def _windows_process_alive(pid):
    """Query Windows without sending a signal or requesting termination rights."""
    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [_DWORD, _BOOL, _DWORD]
        kernel.OpenProcess.restype = _HANDLE
        kernel.GetExitCodeProcess.argtypes = [_HANDLE, ctypes.POINTER(_DWORD)]
        kernel.GetExitCodeProcess.restype = _BOOL
        kernel.CloseHandle.argtypes = [_HANDLE]
        kernel.CloseHandle.restype = _BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            # ERROR_INVALID_PARAMETER means that this PID no longer exists.
            # Access denied and other failures do not establish an owner's exit.
            return ctypes.get_last_error() != 87
        try:
            exit_code = _DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    except (AttributeError, OSError):
        return True


def _process_alive(pid):
    if pid == os.getpid():
        return True
    if os.name == 'nt':
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def write_guard(path, busy, running, pid=None):
    """Atomically replace an absolute lifecycle state file.

    Raises on write failure so the controller can avoid advancing an unprotected
    lifecycle transition. The previous complete state stays in place on failure.
    """
    if type(busy) is not bool or type(running) is not bool:
        raise ValueError('busy and running must be booleans')
    owner_pid = os.getpid() if pid is None else pid
    if not _valid_pid(owner_pid):
        raise ValueError('pid must be a positive process ID')
    target = Path(path)
    if not target.is_absolute():
        raise ValueError('guard path must be absolute')
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + '.', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump({'version': 1, 'pid': owner_pid, 'busy': busy, 'running': running},
                      stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def guard_blocks_writes(environ=None):
    """Return whether this inherited launcher state prevents GM save mutations."""
    environment = os.environ if environ is None else environ
    if GUARD_ENV not in environment:
        return False
    try:
        owner_text = environment.get(OWNER_ENV, '')
        if not isinstance(owner_text, str) or not owner_text.isascii() or not owner_text.isdecimal():
            return True
        owner_pid = int(owner_text)
        if not _valid_pid(owner_pid):
            return True
        if not _process_alive(owner_pid):
            return False
        guard_path = Path(environment[GUARD_ENV])
        if not guard_path.is_absolute():
            return True
        with guard_path.open('rb') as stream:
            raw = stream.read(MAX_GUARD_BYTES + 1)
        if len(raw) > MAX_GUARD_BYTES:
            return True
        state = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_fields)
        if (not isinstance(state, dict) or set(state) != {'version', 'pid', 'busy', 'running'} or
                type(state.get('version')) is not int or
                state['version'] != 1 or not _valid_pid(state.get('pid')) or
                state['pid'] != owner_pid or type(state.get('busy')) is not bool or
                type(state.get('running')) is not bool):
            return True
        return state['busy'] or state['running']
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return True
