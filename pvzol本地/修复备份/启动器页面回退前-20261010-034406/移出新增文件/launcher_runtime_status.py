"""Read-only, dependency-free process and TCP listener metrics for the launcher.

RuntimeMonitor.snapshot() never waits for operating-system inspection: the first
call returns a checking state and starts one daemon worker, and subsequent calls
return a private copy of its cached result. All process handles are read-only.
"""
from __future__ import annotations

import copy
import ctypes
from ctypes import wintypes
import ipaddress
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
            "PagefileUsage", "PeakPagefileUsage",
        )
    ]


def _positive_int(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if number > 0 else None


def _windows_process_info(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    # VM_READ is needed on older Windows; limited query access still permits
    # image and creation-time inspection when memory access is restricted.
    handle = kernel.OpenProcess(0x1000 | 0x0010, False, pid)
    if not handle:
        handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "无法读取进程，进程已退出或权限不足")
    result = {"name": "", "memory_bytes": None, "uptime_seconds": None, "available": True}
    try:
        image = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(image))
        if kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
            result["name"] = image.value.replace("\\", "/").rsplit("/", 1)[-1]
        try:
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                                 ctypes.POINTER(_ProcessMemoryCounters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                result["memory_bytes"] = int(counters.WorkingSetSize)
        except (OSError, AttributeError):
            pass
        creation, exited, kernel_time, user_time = [wintypes.FILETIME() for _ in range(4)]
        if kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exited),
                                  ctypes.byref(kernel_time), ctypes.byref(user_time)):
            ticks = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
            created = ticks / 10_000_000 - 11_644_473_600
            result["uptime_seconds"] = max(0.0, time.time() - created)
    finally:
        kernel.CloseHandle(handle)
    return result


def _proc_process_info(pid):
    root = Path("/proc") / str(pid)
    result = {"name": "", "memory_bytes": None, "uptime_seconds": None, "available": False}
    try:
        result["name"] = (root / "comm").read_text().strip()
        result["available"] = True
    except OSError:
        pass
    try:
        resident_pages = int((root / "statm").read_text().split()[1])
        result["memory_bytes"] = max(0, resident_pages * os.sysconf("SC_PAGE_SIZE"))
        result["available"] = True
    except (OSError, ValueError, IndexError):
        pass
    try:
        stat = (root / "stat").read_text()
        # Process names may contain spaces and parentheses; starttime is field 22.
        start_ticks = int(stat[stat.rfind(")") + 2:].split()[19])
        machine_uptime = float(Path("/proc/uptime").read_text().split()[0])
        result["uptime_seconds"] = max(0.0, machine_uptime - start_ticks / os.sysconf("SC_CLK_TCK"))
        result["available"] = True
    except (OSError, ValueError, IndexError):
        pass
    if not result["available"]:
        raise OSError("无法读取进程，进程已退出或权限不足")
    return result


def process_info(pid):
    """Return best-effort working-set/RSS bytes and process creation-time uptime.

    Unreadable metrics use None (never a fabricated zero); exited or inaccessible
    processes retain their PID and return available=False instead of raising.
    """
    number = _positive_int(pid)
    result = {"pid": number, "name": "System" if number == 4 and os.name == "nt" else "",
              "memory_bytes": None, "uptime_seconds": None, "available": False, "detail": ""}
    if number is None or number > 0xFFFFFFFF:
        result["detail"] = "无效的进程编号"
        return result
    try:
        if os.name == "nt":
            result.update(_windows_process_info(number))
        elif Path("/proc").is_dir():
            result.update(_proc_process_info(number))
        else:
            result["detail"] = "当前系统不支持进程监测"
        if result["available"] and result["memory_bytes"] is None:
            result["detail"] = "进程内存暂时无法读取"
    except (OSError, ValueError, AttributeError, IndexError) as exc:
        result["detail"] = str(exc)
    return result


def _binding_matches(local_address, bind_host):
    """Match listening endpoints which overlap the requested bind address.

    An IPv6 wildcard can be dual-stack; the TCP ownership APIs do not expose
    IPV6_V6ONLY, so :: conservatively overlaps IPv4 binds. Exact IPv6 addresses
    such as ::1 do not occupy an IPv4-only 127.0.0.1 service.
    """
    if bind_host is None:
        return True
    local = ipaddress.ip_address(local_address.split("%", 1)[0])
    requested = ipaddress.ip_address(bind_host.split("%", 1)[0])
    if isinstance(local, ipaddress.IPv6Address) and local.ipv4_mapped is not None:
        local = local.ipv4_mapped
    if local.version == requested.version:
        return local.is_unspecified or requested.is_unspecified or local == requested
    return local.is_unspecified if requested.version == 4 else requested.is_unspecified


def _scoped_listener(port, local_address, scopes):
    return scopes is None or (port in scopes and _binding_matches(local_address, scopes[port]))


def _parse_windows_tcp_table(raw, family, scopes=None):
    if len(raw) < 4:
        raise OSError("TCP 端口表不完整")
    count = struct.unpack_from("<I", raw)[0]
    row_size = 24 if family == socket.AF_INET else 56
    if count > (len(raw) - 4) // row_size:
        raise OSError("TCP 端口表不完整")
    listeners = {}
    for index in range(count):
        offset = 4 + index * row_size
        if family == socket.AF_INET:
            state, _, port, _, _, pid = struct.unpack_from("<6I", raw, offset)
        else:
            port = struct.unpack_from("<I", raw, offset + 20)[0]
            state, pid = struct.unpack_from("<2I", raw, offset + 48)
        if state == 2:
            port = socket.ntohs(port & 0xFFFF)
            address_bytes = raw[offset + 4:offset + 8] if family == socket.AF_INET else raw[offset:offset + 16]
            address = socket.inet_ntop(family, address_bytes)
            if 0 < port <= 65535 and _scoped_listener(port, address, scopes):
                listeners.setdefault(port, set())
                if pid:
                    listeners[port].add(pid)
    return listeners


def _windows_listeners(scopes=None):
    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get_table = iphlpapi.GetExtendedTcpTable
    get_table.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                         wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    get_table.restype = wintypes.DWORD
    listeners = {}
    # Both tables are required: :: can own a port without any IPv4 row.
    for family in (socket.AF_INET, socket.AF_INET6):
        size = wintypes.DWORD(0)
        error = get_table(None, ctypes.byref(size), False, family, 3, 0)
        for _ in range(3):
            if error not in (0, 122) or size.value > 16 * 1024 * 1024:
                raise OSError(error, "无法读取 TCP 端口表")
            if size.value < 4:
                raise OSError("TCP 端口表不完整")
            buffer = ctypes.create_string_buffer(size.value)
            error = get_table(buffer, ctypes.byref(size), False, family, 3, 0)
            if error == 0:
                for port, pids in _parse_windows_tcp_table(buffer.raw, family, scopes).items():
                    listeners.setdefault(port, set()).update(pids)
                break
        else:
            raise OSError("TCP 端口表持续变化，请稍后重试")
        if error not in (0, 122):
            raise OSError(error, "无法读取 TCP 端口表")
    return listeners


def _parse_netstat(text, scopes=None):
    listeners = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 5 or fields[0].upper() != "TCP" or fields[3].upper() != "LISTENING":
            continue
        try:
            port = int(fields[1].rsplit(":", 1)[1])
            address = fields[1].rsplit(":", 1)[0].strip("[]")
            pid = int(fields[4])
            if not _scoped_listener(port, address, scopes):
                continue
        except (IndexError, ValueError):
            continue
        if 0 < port <= 65535:
            listeners.setdefault(port, set())
            if pid > 0:
                listeners[port].add(pid)
    return listeners


def _netstat_listeners(scopes=None):
    result = subprocess.run(["netstat", "-ano", "-p", "TCP"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, errors="replace", timeout=2,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise OSError("无法读取 TCP 端口状态")
    return _parse_netstat(result.stdout, scopes)


def _proc_listeners(ports, scopes=None):
    inode_ports = {}
    listeners = {}
    readable = False
    for filename in ("tcp", "tcp6"):
        try:
            lines = (Path("/proc/net") / filename).read_text().splitlines()[1:]
            readable = True
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            try:
                port = int(fields[1].rsplit(":", 1)[1], 16)
                if fields[3] != "0A" or port not in ports:
                    continue
                encoded = bytes.fromhex(fields[1].rsplit(":", 1)[0])
                # Linux /proc prints each address word in host byte order.
                address_bytes = b"".join(encoded[index:index + 4][::-1] if sys.byteorder == "little"
                                         else encoded[index:index + 4] for index in range(0, len(encoded), 4))
                family = socket.AF_INET if filename == "tcp" else socket.AF_INET6
                address = socket.inet_ntop(family, address_bytes)
                if not _scoped_listener(port, address, scopes):
                    continue
                inode_ports.setdefault(fields[9], set()).add(port)
                listeners.setdefault(port, set())
            except (ValueError, IndexError):
                continue
    if not readable:
        raise OSError("无法读取 TCP 端口状态")
    for process in Path("/proc").iterdir():
        if not inode_ports:
            break
        if not process.name.isdigit():
            continue
        try:
            for fd in (process / "fd").iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target.startswith("socket:[") and target.endswith("]"):
                    for port in inode_ports.get(target[8:-1], ()):
                        listeners[port].add(int(process.name))
        except OSError:
            continue
    return listeners


def _listeners(ports, scopes=None):
    if os.name == "nt":
        try:
            return _windows_listeners(scopes)
        except (OSError, AttributeError, ValueError):
            return _netstat_listeners(scopes)
    if Path("/proc/net").is_dir():
        return _proc_listeners(ports, scopes)
    raise OSError("当前系统不支持端口监测")


def _empty_snapshot(ports, detail="", status="checking"):
    return {"ports": {port: {"occupied": None, "pid": None, "pids": [], "name": "",
                              "owned": False, "status": status,
                              "detail": detail or "正在检测端口"} for port in ports},
            "processes": [], "memory_bytes": None, "memory_complete": False,
            "uptime_seconds": None, "sampled_at": None, "detail": detail, "stale": False}


def _collect_runtime(ports, owned_pids):
    owned = set(owned_pids)
    scopes = ports if isinstance(ports, dict) else None
    result = _empty_snapshot(ports)
    try:
        listeners = _listeners(set(ports), scopes)
    except (OSError, subprocess.SubprocessError, AttributeError, ValueError) as exc:
        listeners = None
        result["detail"] = str(exc)
    all_pids = owned | {pid for port in ports for pid in (listeners or {}).get(port, ())}
    by_pid = {pid: {**process_info(pid), "owned": pid in owned} for pid in sorted(all_pids)}
    result["processes"] = list(by_pid.values())
    for port in ports:
        row = result["ports"][port]
        if listeners is None:
            row.update(status="unknown", detail=result["detail"])
            continue
        pids = sorted(listeners.get(port, ()))
        occupied = port in listeners
        pid = next((value for value in pids if value not in owned), pids[0] if pids else None)
        is_owned = bool(pids) and all(value in owned for value in pids)
        name = by_pid.get(pid, {}).get("name", "")
        status = "idle" if not occupied else ("owned" if is_owned else ("conflict" if pid else "occupied"))
        detail = "端口空闲" if not occupied else "端口已占用，无法读取进程"
        if pid:
            detail = f"{name or '进程'}（PID {pid}）" + ("，由启动器管理" if is_owned else "占用端口")
        row.update(occupied=occupied, pid=pid, pids=pids, name=name, owned=is_owned,
                   status=status, detail=detail)
    owned_rows = [by_pid[pid] for pid in sorted(owned)]
    memory = [row["memory_bytes"] for row in owned_rows if row["memory_bytes"] is not None]
    uptimes = [row["uptime_seconds"] for row in owned_rows if row["uptime_seconds"] is not None]
    result.update(memory_bytes=sum(memory) if memory or not owned_rows else None,
                  memory_complete=len(memory) == len(owned_rows),
                  uptime_seconds=max(uptimes) if uptimes else (0.0 if not owned_rows else None),
                  sampled_at=time.time())
    return result


class RuntimeMonitor:
    """On-demand async cache. Call snapshot(ports, owned_pids) when rendering state.

    ports is either an iterable of TCP port numbers (all interfaces), or a
    {port: numeric_bind_address} mapping. owned_pids are processes owned by
    the launcher. No subprocess is killed, and the bounded fallback netstat
    command is launched without a console. collector is injectable for tests.
    """
    def __init__(self, refresh_seconds=1.0, *, collector=None, clock=None):
        self.refresh_seconds = max(0.05, float(refresh_seconds))
        self._collector = collector or _collect_runtime
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._key = None
        self._data = _empty_snapshot(())
        self._last_refresh = None
        self._worker = None

    def snapshot(self, ports, owned_pids=()):
        scopes = None
        if isinstance(ports, dict):
            scopes = tuple(sorted({number: str(ipaddress.ip_address(str(host))) for value, host in ports.items()
                                   if (number := _positive_int(value)) is not None and number <= 65535}.items()))
            clean_ports = tuple(port for port, _ in scopes)
        else:
            clean_ports = tuple(sorted({number for value in ports
                                       if (number := _positive_int(value)) is not None and number <= 65535}))
        clean_pids = tuple(sorted({number for value in owned_pids
                                  if (number := _positive_int(value)) is not None and number <= 0xFFFFFFFF}))
        key = (clean_ports, clean_pids, scopes)
        with self._lock:
            if key != self._key:
                self._key = key
                self._data = _empty_snapshot(clean_ports)
                self._last_refresh = None
            now = self._clock()
            if self._worker is None and (self._last_refresh is None or now - self._last_refresh >= self.refresh_seconds):
                worker = threading.Thread(target=self._refresh, args=(key,), name="launcher-runtime", daemon=True)
                self._worker = worker
                try:
                    worker.start()
                except RuntimeError as exc:
                    self._worker = None
                    self._last_refresh = now
                    self._data = _empty_snapshot(clean_ports, str(exc), "unknown")
            result = copy.deepcopy(self._data)
            result["pending"] = self._worker is not None
            return result

    def _refresh(self, key):
        try:
            data = self._collector(dict(key[2]) if key[2] is not None else key[0], key[1])
            if not isinstance(data, dict):
                raise ValueError("运行状态检测未返回有效结果")
            failure = ""
        except Exception as exc:
            data, failure = None, str(exc) or type(exc).__name__
        with self._lock:
            if key == self._key:
                if data is not None:
                    self._data = copy.deepcopy(data)
                elif self._data.get("sampled_at") is None:
                    self._data = _empty_snapshot(key[0], failure, "unknown")
                else:
                    self._data.update(detail=failure, stale=True)
                self._last_refresh = self._clock()
            self._worker = None
