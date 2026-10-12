"""Runtime metrics inspect generated/mocked data and never launch game processes."""
import ctypes
from ctypes import wintypes
import importlib.util
import os
from pathlib import Path
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[2] / "launcher_runtime_status.py"
if not SOURCE.exists():
    SOURCE = Path(__file__).with_name("launcher_runtime_status.py")
SPEC = importlib.util.spec_from_file_location("launcher_runtime_status_under_test", SOURCE)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def metric(pid, memory=100, uptime=12, name="python.exe", available=True):
    return {"pid": pid, "name": name, "memory_bytes": memory,
            "uptime_seconds": uptime, "available": available, "detail": ""}


def wait_refresh(monitor):
    worker = monitor._worker
    if worker is not None:
        worker.join(2)
        if worker.is_alive():
            raise AssertionError("monitor worker did not finish")


class RuntimeCollectionTests(unittest.TestCase):
    def test_external_port_owner_is_not_counted_as_launcher_memory(self):
        with patch.object(runtime, "_listeners", return_value={8080: {20}, 1000: {10}}), \
             patch.object(runtime, "process_info", side_effect=lambda pid: metric(pid, pid * 100)):
            result = runtime._collect_runtime((8080, 1000, 843), (10,))
        self.assertEqual(result["memory_bytes"], 1000)
        self.assertEqual(result["ports"][8080]["status"], "conflict")
        self.assertEqual(result["ports"][1000]["status"], "owned")
        self.assertEqual(result["ports"][843]["status"], "idle")
        self.assertEqual([row["pid"] for row in result["processes"]], [10, 20])
        self.assertFalse(result["processes"][1]["owned"])

    def test_mixed_port_owners_prioritize_conflicting_process(self):
        with patch.object(runtime, "_listeners", return_value={8080: {10, 20}}), \
             patch.object(runtime, "process_info", side_effect=metric):
            result = runtime._collect_runtime((8080,), (10,))
        row = result["ports"][8080]
        self.assertEqual(row["pid"], 20)
        self.assertEqual(row["pids"], [10, 20])
        self.assertFalse(row["owned"])
        self.assertEqual(row["status"], "conflict")

    def test_failed_port_inspection_does_not_report_free_or_lose_memory(self):
        with patch.object(runtime, "_listeners", side_effect=OSError("permission denied")), \
             patch.object(runtime, "process_info", return_value=metric(10, 4096)):
            result = runtime._collect_runtime((8080,), (10,))
        self.assertIsNone(result["ports"][8080]["occupied"])
        self.assertEqual(result["ports"][8080]["status"], "unknown")
        self.assertEqual(result["memory_bytes"], 4096)

    def test_missing_process_metrics_are_unknown_instead_of_zero(self):
        with patch.object(runtime, "_listeners", return_value={}), \
             patch.object(runtime, "process_info", return_value=metric(10, None, None, available=False)):
            result = runtime._collect_runtime((8080,), (10,))
        self.assertIsNone(result["memory_bytes"])
        self.assertIsNone(result["uptime_seconds"])
        self.assertFalse(result["memory_complete"])

    def test_memory_totals_deduplicate_owned_pids_and_uptime_is_oldest(self):
        with patch.object(runtime, "_listeners", return_value={8080: {10}}), \
             patch.object(runtime, "process_info", side_effect=lambda pid: metric(pid, 100, pid)):
            result = runtime._collect_runtime((8080,), (10, 10, 20))
        self.assertEqual(result["memory_bytes"], 200)
        self.assertEqual(result["uptime_seconds"], 20)
        self.assertTrue(result["memory_complete"])

    def test_listener_without_readable_pid_stays_occupied(self):
        with patch.object(runtime, "_listeners", return_value={8080: set()}):
            result = runtime._collect_runtime((8080,), ())
        self.assertTrue(result["ports"][8080]["occupied"])
        self.assertEqual(result["ports"][8080]["status"], "occupied")
        self.assertIsNone(result["ports"][8080]["pid"])
        self.assertEqual(result["memory_bytes"], 0)


class NativeDataTests(unittest.TestCase):
    def test_ipv4_address_scope_keeps_matching_and_wildcard_listeners(self):
        port = socket.htons(62322)
        def row(address, pid):
            encoded = struct.unpack("<I", socket.inet_pton(socket.AF_INET, address))[0]
            return struct.pack("<6I", 2, encoded, port, 0, 0, pid)
        raw = struct.pack("<I", 3) + row("127.0.0.1", 10) + row("127.0.0.2", 20) + row("0.0.0.0", 30)
        self.assertEqual(runtime._parse_windows_tcp_table(raw, socket.AF_INET, {62322: "127.0.0.1"}), {62322: {10, 30}})
        self.assertEqual(runtime._parse_windows_tcp_table(raw, socket.AF_INET, {62322: "0.0.0.0"}), {62322: {10, 20, 30}})
        self.assertEqual(runtime._parse_windows_tcp_table(raw, socket.AF_INET), {62322: {10, 20, 30}})

    def test_netstat_scope_filters_unrelated_loopback_and_ipv6_specific_bindings(self):
        output = """TCP 127.0.0.1:8080 0.0.0.0:0 LISTENING 10
TCP 127.0.0.2:8080 0.0.0.0:0 LISTENING 20
TCP [::1]:8080 [::]:0 LISTENING 30
TCP [::]:843 [::]:0 LISTENING 40
"""
        self.assertEqual(runtime._parse_netstat(output, {8080: "127.0.0.1", 843: "0.0.0.0"}),
                         {8080: {10}, 843: {40}})
        self.assertTrue(runtime._binding_matches("::", "127.0.0.1"))
        self.assertFalse(runtime._binding_matches("::1", "127.0.0.1"))
        self.assertTrue(runtime._binding_matches("::ffff:127.0.0.1", "127.0.0.1"))

    @unittest.skipUnless(os.name == "nt", "Windows API test")
    def test_real_two_loopback_bindings_scope_ownership_and_foreign_process_rows(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            command = [sys.executable, "-c", "import socket,sys,time; s=socket.socket(); "
                       "s.bind(('127.0.0.2',int(sys.argv[1]))); s.listen(1); "
                       "print('READY',flush=True); time.sleep(15)", str(port)]
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ready = queue.Queue()
            reader = threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
            reader.start()
            try:
                self.assertEqual(ready.get(timeout=3).strip(), "READY")
                scoped = runtime._collect_runtime({port: "127.0.0.1"}, (os.getpid(),))
                self.assertEqual(scoped["ports"][port]["pids"], [os.getpid()])
                self.assertTrue(scoped["ports"][port]["owned"])
                self.assertNotIn(child.pid, [row["pid"] for row in scoped["processes"]])
                all_ipv4 = runtime._collect_runtime({port: "0.0.0.0"}, (os.getpid(),))
                self.assertEqual(all_ipv4["ports"][port]["status"], "conflict")
                self.assertIn(child.pid, all_ipv4["ports"][port]["pids"])
                broad = runtime._collect_runtime((port,), (os.getpid(),))
                self.assertEqual(broad["ports"][port]["status"], "conflict")
            finally:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
                reader.join(1)
                child.stdout.close()
                child.stderr.close()

    def test_ipv4_and_ipv6_table_port_byte_order(self):
        ipv4 = struct.pack("<I6I", 1, 2, 0, socket.htons(8080), 0, 0, 42)
        self.assertEqual(runtime._parse_windows_tcp_table(ipv4, socket.AF_INET), {8080: {42}})
        ipv6 = bytearray(60)
        struct.pack_into("<I", ipv6, 0, 1)
        struct.pack_into("<I", ipv6, 4 + 20, socket.htons(1000))
        struct.pack_into("<2I", ipv6, 4 + 48, 2, 99)
        self.assertEqual(runtime._parse_windows_tcp_table(ipv6, socket.AF_INET6), {1000: {99}})

    def test_truncated_tcp_table_rejected(self):
        for data in (b"", struct.pack("<I", 1000), struct.pack("<I", 1) + bytes(20)):
            with self.assertRaises(OSError):
                runtime._parse_windows_tcp_table(data, socket.AF_INET)

    def test_netstat_ignores_connections_and_parses_ipv6(self):
        output = """  TCP    0.0.0.0:8080    0.0.0.0:0    LISTENING    42
  TCP    [::]:1000      [::]:0         LISTENING    99
  TCP    127.0.0.1:60000 127.0.0.1:8080 ESTABLISHED 42
  UDP    0.0.0.0:843    *:*           0
  TCP    malformed     0.0.0.0:0     LISTENING    42
"""
        self.assertEqual(runtime._parse_netstat(output), {8080: {42}, 1000: {99}})

    def test_netstat_fallback_is_hidden_and_timeout_bounded(self):
        with patch.object(runtime.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")) as run:
            runtime._netstat_listeners()
        self.assertEqual(run.call_args.kwargs["timeout"], 2)
        self.assertEqual(run.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))

    @unittest.skipUnless(os.name == "nt", "Windows API test")
    def test_windows_tcp_table_retries_size_change_and_merges_ipv6(self):
        ipv4 = struct.pack("<I6I", 1, 2, 0, socket.htons(8080), 0, 0, 42)
        ipv6 = bytearray(60)
        struct.pack_into("<I", ipv6, 0, 1)
        struct.pack_into("<I", ipv6, 24, socket.htons(8080))
        struct.pack_into("<2I", ipv6, 52, 2, 99)
        changed = [False]

        def table(buffer, size_ptr, order, family, table_class, reserved):
            size = ctypes.cast(size_ptr, ctypes.POINTER(wintypes.DWORD)).contents
            data = ipv4 if family == socket.AF_INET else bytes(ipv6)
            self.assertEqual(table_class, 3)
            if buffer is None:
                size.value = 4 if family == socket.AF_INET else len(data)
                return 122
            if family == socket.AF_INET and not changed[0]:
                changed[0] = True
                size.value = len(data)
                return 122
            ctypes.memmove(buffer, data, len(data))
            return 0

        iphlpapi = SimpleNamespace(GetExtendedTcpTable=Mock(side_effect=table))
        with patch.object(runtime.ctypes, "WinDLL", return_value=iphlpapi):
            self.assertEqual(runtime._windows_listeners(), {8080: {42, 99}})

    @unittest.skipUnless(os.name == "nt", "Windows API test")
    def test_real_windows_listener_reports_current_process_pid(self):
        # A temporary loopback listener proves the API captures occupancy/PID;
        # it closes at test completion and never touches a game service.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            snapshot = runtime._collect_runtime((port,), (os.getpid(),))
            self.assertTrue(snapshot["ports"][port]["occupied"])
            self.assertEqual(snapshot["ports"][port]["pid"], os.getpid())
            self.assertTrue(snapshot["ports"][port]["owned"])

    def test_process_info_handles_invalid_and_disappearing_pids(self):
        for pid in (None, 0, -1, "not a PID", True, 1 << 40):
            self.assertFalse(runtime.process_info(pid)["available"])
        with patch.object(runtime.os, "name", "nt"), \
             patch.object(runtime, "_windows_process_info", side_effect=OSError("exited")):
            result = runtime.process_info(42)
        self.assertEqual(result["pid"], 42)
        self.assertIsNone(result["memory_bytes"])
        self.assertFalse(result["available"])

    @unittest.skipUnless(os.name == "nt", "Windows API test")
    def test_windows_memory_image_uptime_and_handle_cleanup(self):
        def memory(handle, counters, size):
            ctypes.cast(counters, ctypes.POINTER(runtime._ProcessMemoryCounters)).contents.WorkingSetSize = 321000
            return 1

        def image(handle, flags, buffer, size):
            buffer.value = "C:\\runtime\\python.exe"
            return 1

        def times(handle, creation, *unused):
            ticks = int((time.time() - 120 + 11_644_473_600) * 10_000_000)
            stamp = ctypes.cast(creation, ctypes.POINTER(wintypes.FILETIME)).contents
            stamp.dwLowDateTime = ticks & 0xFFFFFFFF
            stamp.dwHighDateTime = ticks >> 32
            return 1

        kernel = SimpleNamespace(OpenProcess=Mock(return_value=123), CloseHandle=Mock(return_value=1),
                                 QueryFullProcessImageNameW=Mock(side_effect=image), GetProcessTimes=Mock(side_effect=times))
        psapi = SimpleNamespace(GetProcessMemoryInfo=Mock(side_effect=memory))
        with patch.object(runtime.ctypes, "WinDLL", side_effect=lambda name, **kw: kernel if name == "kernel32" else psapi):
            result = runtime.process_info(42)
        self.assertEqual(result["name"], "python.exe")
        self.assertEqual(result["memory_bytes"], 321000)
        self.assertAlmostEqual(result["uptime_seconds"], 120, delta=1)
        kernel.CloseHandle.assert_called_once_with(123)

    @unittest.skipUnless(os.name == "nt", "Windows API test")
    def test_windows_handle_closes_when_memory_query_fails(self):
        kernel = SimpleNamespace(OpenProcess=Mock(return_value=123), CloseHandle=Mock(return_value=1),
                                 QueryFullProcessImageNameW=Mock(return_value=0), GetProcessTimes=Mock(return_value=0))
        psapi = SimpleNamespace(GetProcessMemoryInfo=Mock(side_effect=OSError("denied")))
        with patch.object(runtime.ctypes, "WinDLL", side_effect=lambda name, **kw: kernel if name == "kernel32" else psapi):
            result = runtime.process_info(42)
        self.assertTrue(result["available"])
        self.assertIsNone(result["memory_bytes"])
        kernel.CloseHandle.assert_called_once_with(123)

    @unittest.skipUnless(os.name == "nt", "Windows API test")
    def test_denied_windows_process_tries_limited_query_and_does_not_close_null(self):
        kernel = SimpleNamespace(OpenProcess=Mock(return_value=0), CloseHandle=Mock(),
                                 QueryFullProcessImageNameW=Mock(), GetProcessTimes=Mock())
        with patch.object(runtime.ctypes, "WinDLL", return_value=kernel), \
             patch.object(runtime.ctypes, "get_last_error", return_value=5):
            result = runtime.process_info(42)
        self.assertFalse(result["available"])
        self.assertEqual(kernel.OpenProcess.call_count, 2)
        self.assertEqual(kernel.OpenProcess.call_args.args[0], 0x1000)
        kernel.CloseHandle.assert_not_called()

    def test_current_process_has_real_readonly_memory_metrics(self):
        result = runtime.process_info(os.getpid())
        self.assertTrue(result["available"], result["detail"])
        self.assertGreater(result["memory_bytes"], 0)
        self.assertGreaterEqual(result["uptime_seconds"], 0)


class RuntimeMonitorTests(unittest.TestCase):
    def test_binding_scope_change_invalidates_cache_and_preserves_list_api(self):
        collector = Mock(side_effect=lambda ports, pids: {**runtime._empty_snapshot(ports), "sampled_at": 1})
        monitor = runtime.RuntimeMonitor(collector=collector, clock=lambda: 0)
        monitor.snapshot({8080: "127.0.0.1"}, (10,))
        wait_refresh(monitor)
        monitor.snapshot({8080: "127.0.0.2"}, (10,))
        wait_refresh(monitor)
        monitor.snapshot((8080,), (10,))
        wait_refresh(monitor)
        self.assertEqual(collector.call_count, 3)
        self.assertEqual(collector.call_args_list[0].args[0], {8080: "127.0.0.1"})
        self.assertEqual(collector.call_args_list[1].args[0], {8080: "127.0.0.2"})
        self.assertEqual(collector.call_args_list[2].args[0], (8080,))

    def test_slow_inspection_does_not_block_snapshot_or_start_duplicate_workers(self):
        release = threading.Event()
        entered = threading.Event()
        calls = []

        def collect(ports, pids):
            calls.append((ports, pids))
            entered.set()
            release.wait(2)
            return {**runtime._empty_snapshot(ports), "sampled_at": 1, "memory_bytes": 500}

        monitor = runtime.RuntimeMonitor(collector=collect)
        self.addCleanup(release.set)
        started = time.monotonic()
        first = monitor.snapshot((8080,), (10,))
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertTrue(entered.wait(1))
        second = monitor.snapshot((8080,), (10,))
        self.assertTrue(first["pending"])
        self.assertTrue(second["pending"])
        self.assertIsNone(first["ports"][8080]["occupied"])
        self.assertEqual(len(calls), 1)
        release.set()
        wait_refresh(monitor)
        self.assertEqual(monitor.snapshot((8080,), (10,))["memory_bytes"], 500)

    def test_cache_refresh_interval_and_returned_data_are_independent(self):
        now = [0.0]
        collector = Mock(side_effect=lambda ports, pids: {**runtime._empty_snapshot(ports), "sampled_at": 1})
        monitor = runtime.RuntimeMonitor(collector=collector, clock=lambda: now[0])
        monitor.snapshot((8080,), (10,))
        wait_refresh(monitor)
        first = monitor.snapshot((8080,), (10,))
        first["ports"][8080]["name"] = "tampered"
        now[0] = 0.99
        self.assertEqual(monitor.snapshot((8080,), (10,))["ports"][8080]["name"], "")
        self.assertEqual(collector.call_count, 1)
        now[0] = 1.01
        monitor.snapshot((8080,), (10,))
        wait_refresh(monitor)
        self.assertEqual(collector.call_count, 2)

    def test_changed_owned_pid_does_not_publish_old_worker_result(self):
        entered, release = threading.Event(), threading.Event()

        def collect(ports, pids):
            entered.set()
            release.wait(2)
            return {**runtime._empty_snapshot(ports), "sampled_at": 1, "memory_bytes": pids[0]}

        monitor = runtime.RuntimeMonitor(collector=collect)
        self.addCleanup(release.set)
        monitor.snapshot((8080,), (10,))
        self.assertTrue(entered.wait(1))
        fresh = monitor.snapshot((8080,), (20,))
        self.assertIsNone(fresh["memory_bytes"])
        release.set()
        wait_refresh(monitor)
        self.assertIsNone(monitor.snapshot((8080,), (20,))["memory_bytes"])
        wait_refresh(monitor)
        self.assertEqual(monitor.snapshot((8080,), (20,))["memory_bytes"], 20)

    def test_first_failure_reports_unknown_and_recovers(self):
        now = [0.0]
        collector = Mock(side_effect=[OSError("unavailable"), {**runtime._empty_snapshot((8080,)), "sampled_at": 1}])
        monitor = runtime.RuntimeMonitor(collector=collector, clock=lambda: now[0])
        monitor.snapshot((8080,), ())
        wait_refresh(monitor)
        result = monitor.snapshot((8080,), ())
        self.assertEqual(result["ports"][8080]["status"], "unknown")
        self.assertFalse(result["pending"])
        now[0] = 2
        monitor.snapshot((8080,), ())
        wait_refresh(monitor)
        self.assertEqual(monitor.snapshot((8080,), ())["sampled_at"], 1)

    def test_failure_keeps_last_sample_with_stale_flag(self):
        now = [0.0]
        valid = {**runtime._empty_snapshot((8080,)), "sampled_at": 123, "memory_bytes": 4096}
        collector = Mock(side_effect=[valid, OSError("denied")])
        monitor = runtime.RuntimeMonitor(collector=collector, clock=lambda: now[0])
        monitor.snapshot((8080,), ())
        wait_refresh(monitor)
        now[0] = 2
        monitor.snapshot((8080,), ())
        wait_refresh(monitor)
        result = monitor.snapshot((8080,), ())
        self.assertEqual(result["memory_bytes"], 4096)
        self.assertTrue(result["stale"])
        self.assertEqual(result["detail"], "denied")


if __name__ == "__main__":
    unittest.main()
