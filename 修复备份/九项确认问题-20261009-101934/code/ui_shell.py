"""Native desktop window for the loopback-only PVZ launcher frontend.

The controller owns all data and services. This process only displays its local
HTTP page, so it deliberately exposes no Python API to page JavaScript.
"""
from __future__ import annotations

import argparse
import ctypes
import http.client
import importlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import queue
import socket
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit


TITLE = "植物大战僵尸 OL"
LOCALIZATION = {
    "global.quitConfirmation": "确定退出启动器吗？退出后将关闭本次启动的游戏服务。",
    "global.ok": "确定",
    "global.quit": "退出",
    "global.cancel": "取消",
    "global.saveFile": "保存文件",
    "windows.fileFilter.allFiles": "所有文件",
    "windows.fileFilter.otherFiles": "其他文件类型",
}


def validate_local_url(value: str) -> str:
    """Accept literal loopback HTTP addresses only; never resolve remote DNS."""
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise ValueError("启动器地址必须是本机 HTTP 地址。") from exc
    if (
        parsed.scheme.lower() != "http"
        or not address.is_loopback
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or any(char in value for char in "\r\n\t")
    ):
        raise ValueError("启动器只能打开带端口的本机 HTTP 地址。")
    return value


class TrayError(RuntimeError):
    """A safe, user-readable failure that never includes the session URL."""


def center_geometry(width, height, work_area):
    """Center physical form bounds inside a monitor's usable work area."""
    left, top, available_width, available_height = map(int, work_area)
    width = max(1, min(int(width), max(1, available_width)))
    height = max(1, min(int(height), max(1, available_height)))
    return (left + (available_width - width) // 2,
            top + (available_height - height) // 2, width, height)


def _account_id(account):
    if not isinstance(account, dict):
        return ''
    return str(account.get('id') or account.get('uin') or account.get('account_id') or '')


def select_start_account(state, accounts, selected_id=None):
    """Choose a still-existing page selection, then the controller's last role."""
    valid = {_account_id(account) for account in accounts if _account_id(account)}
    choices = (selected_id, _account_id(state.get('currentAccount')),
               state.get('activeAccountId'), _account_id(state.get('activeAccount')),
               _account_id(accounts[0]) if accounts else None)
    return next((str(choice) for choice in choices if choice is not None and str(choice) in valid), None)


def allow_controller_foreground(pid):
    """Let this launcher session's exact controller display its game window."""
    if type(pid) is not int or not 0 < pid < 0xffffffff:  # 0xffffffff is ASFW_ANY
        return False
    try:
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.AllowSetForegroundWindow.argtypes = [ctypes.c_uint32]
        user.AllowSetForegroundWindow.restype = ctypes.c_int
        return bool(user.AllowSetForegroundWindow(pid))
    except (AttributeError, OSError):
        return False


class LoopbackClient:
    """Direct authenticated requests; no proxies, URL tokens or redirects."""

    PATHS = frozenset(('/api/state', '/api/accounts', '/api/start', '/api/stop'))

    def __init__(self, url, timeout=3.0):
        parsed = urlsplit(validate_local_url(url))
        tokens = parse_qs(parsed.query, keep_blank_values=True).get('token', [])
        if len(tokens) != 1 or not tokens[0] or any(ch in tokens[0] for ch in '\r\n\0'):
            raise TrayError('启动器会话无效，请重新打开启动器。')
        self._host, self._port, self._token = parsed.hostname, parsed.port, tokens[0]
        self._timeout = timeout
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._connection = None
        self._socket = None

    def _safe_message(self, message):
        return str(message).replace(self._token, '[已隐藏]')[:240]

    def request(self, method, path, data=None):
        if (method not in ('GET', 'POST') or path not in self.PATHS
                or (method == 'GET') != (path in ('/api/state', '/api/accounts'))):
            raise TrayError('不支持的启动器操作。')
        con = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        with self._lock:
            if self._closed.is_set():
                con.close()
                raise TrayError('启动器正在退出。')
            self._connection = con
        response = None
        try:
            con.connect()
            with self._lock:
                if self._closed.is_set():
                    con.close()
                    raise TrayError('启动器正在退出。')
                self._socket = con.sock
            headers = {'X-PVZ-Token': self._token, 'Accept': 'application/json'}
            body = None
            if method == 'POST':
                body = json.dumps(data or {}, ensure_ascii=False).encode('utf-8')
                headers['Content-Type'] = 'application/json'
            con.request(method, path, body=body, headers=headers)
            response = con.getresponse()
            if 300 <= response.status < 400:
                raise TrayError('启动器连接已失效，请重新打开启动器。')
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise TrayError('启动器返回的数据过大。')
            payload = json.loads(raw.decode('utf-8'))
            if not isinstance(payload, dict) or 'ok' not in payload:
                raise TrayError('启动器返回的数据无效。')
            if response.status >= 400 or payload.get('ok') is not True:
                raise TrayError(self._safe_message(payload.get('error') or '启动器操作失败。'))
            return payload.get('data')
        except TrayError:
            raise
        except (OSError, http.client.HTTPException, ValueError, UnicodeError):
            raise TrayError('暂时无法连接启动器，请稍后重试。') from None
        finally:
            if response is not None:
                response.close()
            con.close()
            with self._lock:
                if self._connection is con:
                    self._connection = None
                    self._socket = None

    def close(self):
        self._closed.set()
        with self._lock:
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            if self._connection is not None:
                self._connection.close()


class TrayActions:
    """Lifecycle actions run serially outside the Windows message loop."""

    def __init__(self, client, selected_account=lambda: None):
        self.client, self.selected_account = client, selected_account
        self.state = {}

    def refresh(self):
        state = self.client.request('GET', '/api/state')
        if not isinstance(state, dict):
            raise TrayError('启动器状态无效。')
        self.state = state
        return state

    def start(self):
        state = self.refresh()
        if state.get('running') or state.get('busy'):
            return state
        selected = self.selected_account()
        accounts = self.client.request('GET', '/api/accounts')
        if not isinstance(accounts, list):
            raise TrayError('角色列表无效。')
        account = select_start_account(state, accounts, selected)
        if account is None:
            raise TrayError('请先在启动器创建一个角色。')
        self.client.request('POST', '/api/start', {'account_id': account})
        return self.refresh()

    def stop(self):
        state = self.refresh()
        active = state.get('busy') or state.get('running') or any(service.get('status') == 'running'
                                             for service in state.get('services', []))
        if not active:
            return state
        self.client.request('POST', '/api/stop', {})
        return self.refresh()


class TrayWorker:
    """One bounded HTTP worker, with duplicate clicks and teardown handled."""

    def __init__(self, actions, on_result):
        self.actions, self.on_result = actions, on_result
        self._queue = queue.Queue()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self.pending = False
        self.thread = threading.Thread(target=self._run, name='PVZ tray requests')

    def start(self):
        self.thread.start()

    def submit(self, action):
        if action not in ('start', 'stop', 'refresh'):
            return False
        with self._lock:
            if self._closed.is_set() or self.pending:
                return False
            self.pending = True
            self._queue.put(action)
            return True

    def _run(self):
        while not self._closed.is_set():
            action = self._queue.get()
            if action is None or self._closed.is_set():
                break
            state, error = None, None
            try:
                state = getattr(self.actions, action)()
            except TrayError as exc:
                error = str(exc)
            except Exception:
                # Do not log raw request errors or URLs containing a session token.
                error = '启动器操作失败，请稍后重试。'
            finally:
                with self._lock:
                    self.pending = False
            if not self._closed.is_set():
                self.on_result(action, state, error)

    def close(self):
        self._closed.set()
        self.actions.client.close()
        self._queue.put(None)
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join()


class ActivationWatcher:
    """Receive a controller-owned auto-reset event for a second launch."""

    def __init__(self, event_name, callback):
        self._closed = threading.Event()
        self._handle = None
        self.thread = None
        self._kernel = None
        if not event_name:
            return
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        kernel.OpenEventW.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.WaitForSingleObject.restype = ctypes.c_uint32
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int
        handle = kernel.OpenEventW(0x00100000, False, event_name)  # SYNCHRONIZE
        if not handle:
            raise TrayError('启动器唤醒通道不可用，请重新打开启动器。')
        self._kernel, self._handle = kernel, handle

        def watch():
            while not self._closed.is_set():
                result = kernel.WaitForSingleObject(handle, 200)
                if result == 0 and not self._closed.is_set():
                    callback()
                elif result not in (0, 258):
                    break

        self.thread = threading.Thread(target=watch, name='PVZ launcher activation')
        self.thread.start()

    def close(self):
        self._closed.set()
        if self.thread is not None and threading.current_thread() is not self.thread:
            self.thread.join()
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


class NativeTray:
    """WinForms tray and placement, sharing the existing WebView message loop."""

    SELECTION_SCRIPT = """(() => {
      try {
        if (typeof currentAccount === 'function') {
          const a = currentAccount();
          if (a) return String(a.id ?? a.uin ?? a.account_id ?? '');
        }
        return typeof selectedId === 'string' ? selectedId : null;
      } catch (_) { return null; }
    })()"""

    def __init__(self, window, client, window_file=None, diagnostic_file=None, auto_close=False):
        self.window, self.client = window, client
        self.window_file = Path(window_file) if window_file else None
        self.diagnostic_file = Path(diagnostic_file) if diagnostic_file else None
        self.auto_close = auto_close
        self._diagnostics = {}
        self.form = self.icon = self.menu = self.timer = self.watcher = None
        self._tray_disposed = False
        self._closed = threading.Event()
        self._delegates = []
        self._available = False
        self.worker = TrayWorker(TrayActions(client, self._selected_account), self._on_result)

    def attach(self):
        from System.Drawing import Icon, Rectangle, Size
        from System.Windows.Forms import (ContextMenuStrip, Cursor, FormStartPosition,
                                          NotifyIcon, Screen, Timer, ToolStripMenuItem)

        self.form = form = self.window.native
        area = Screen.FromPoint(Cursor.Position).WorkingArea
        form.StartPosition = FormStartPosition.Manual
        # pywebview already converts its logical requested size to physical pixels.
        # WorkArea and native Bounds use those same physical pixels, including DPI.
        bounds = center_geometry(form.Width, form.Height,
                                 (area.Left, area.Top, area.Width, area.Height))
        form.MinimumSize = Size(min(form.MinimumSize.Width, bounds[2]),
                                min(form.MinimumSize.Height, bounds[3]))
        form.Bounds = Rectangle(*bounds)
        self._publish_window()
        self.menu = ContextMenuStrip()
        self.show_item = ToolStripMenuItem('显示启动器')
        self.start_item = ToolStripMenuItem('启动游戏')
        self.stop_item = ToolStripMenuItem('停止游戏')
        self.exit_item = ToolStripMenuItem('退出')
        for item in (self.show_item, self.start_item, self.stop_item, self.exit_item):
            self.menu.Items.Add(item)
        self.show_item.Click += self._show_event
        self.start_item.Click += self._start_event
        self.stop_item.Click += self._stop_event
        self.exit_item.Click += self._exit_event
        self.menu.Opening += self._menu_opening
        self.icon = NotifyIcon()
        self._owned_icon = Icon.ExtractAssociatedIcon(sys.executable)
        if self._owned_icon is None:
            self._owned_icon = form.Icon.Clone()
        self.icon.Icon = self._owned_icon
        self.icon.Text = TITLE
        self.icon.ContextMenuStrip = self.menu
        self.icon.DoubleClick += self._show_event
        self.icon.Visible = True
        form.FormClosed += self._form_closed
        form.Shown += self._form_shown
        form.Activated += self._activated_event
        self.timer = Timer()
        self.timer.Interval = 2500
        self.timer.Tick += self._poll
        self._update_menu()
        self.worker.start()
        self.worker.submit('refresh')
        self.timer.Start()
        self.watcher = ActivationWatcher(os.environ.get('PVZ_LAUNCHER_ACTIVATE_EVENT'), self.show)

    @staticmethod
    def _write_json(path, data):
        if not path.is_absolute():
            raise TrayError('桌面窗口记录路径必须为绝对路径。')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _publish_window(self):
        if self.window_file is not None:
            self._write_json(self.window_file, {
                'pid': os.getpid(), 'hwnd': int(self.form.Handle.ToInt64())})

    def _form_shown(self, *_):
        if self.diagnostic_file is not None:
            from System.Windows.Forms import Screen
            area = Screen.FromControl(self.form).WorkingArea
            bounds = self.form.Bounds
            self._diagnostics['shown'] = {
                'pid': os.getpid(), 'hwnd': int(self.form.Handle.ToInt64()),
                'work_area': [area.Left, area.Top, area.Width, area.Height],
                'bounds': [bounds.Left, bounds.Top, bounds.Width, bounds.Height],
                'tray_visible': bool(self.icon.Visible),
            }
            self._write_json(self.diagnostic_file, self._diagnostics)
        if self.auto_close:
            from System.Windows.Forms import Timer
            self._test_timer = Timer()
            self._test_timer.Interval = 2500
            self._test_timer.Tick += self._test_close
            self._test_timer.Start()

    def _test_close(self, *_):
        self._test_timer.Stop()
        self.form.Close()

    def _invoke(self, callback):
        if self._closed.is_set() or self.form is None or self.form.IsDisposed:
            return
        from System import Action
        try:
            if self.form.InvokeRequired:
                self.form.BeginInvoke(Action(callback))
            else:
                callback()
        except Exception:
            # Native disposal can race a background result after the first check.
            if not self._closed.is_set():
                logging.warning('桌面窗口暂时无法接收托盘更新。')

    def _selected_account(self):
        result, done = [None], threading.Event()

        def read():
            if self._closed.is_set() or not self.window.events.loaded.is_set():
                done.set()
                return
            try:
                from System import Action, String
                from System.Threading.Tasks import Task
                browser = self.form.browser
                task = browser.webview.ExecuteScriptAsync(self.SELECTION_SCRIPT)

                def completed(completed_task):
                    try:
                        if not completed_task.IsFaulted and not completed_task.IsCanceled:
                            value = json.loads(str(completed_task.Result))
                            if isinstance(value, str):
                                result[0] = value
                    finally:
                        done.set()

                task.ContinueWith(Action[Task[String]](completed), browser.syncContextTaskScheduler)
            except Exception:
                done.set()

        self._invoke(read)
        # WebView's own synchronous evaluate_js has an unbounded semaphore wait.
        # Async script reading lets shutdown cancel promptly even during navigation.
        for _ in range(15):
            if done.wait(.1) or self._closed.is_set():
                break
        return result[0]

    def _update_menu(self):
        state = self.worker.actions.state
        active = state.get('busy') or state.get('running') or any(service.get('status') == 'running'
                                             for service in state.get('services', []))
        ready = self._available and not self.worker.pending
        for item, enabled in ((self.start_item, bool(ready and not state.get('busy') and not state.get('running'))),
                              (self.stop_item, bool(ready and active))):
            if bool(item.Enabled) != enabled:
                item.Enabled = enabled

    def _menu_opening(self, *_):
        self._update_menu()

    def _poll(self, *_):
        # A background read is brief and does not change the service state itself.
        # Keep the existing menu stable until its result arrives.
        self.worker.submit('refresh')

    def _start_event(self, *_):
        # A tray click is user input owned by this process even if the form is hidden.
        self._activated_event()
        if self.worker.submit('start'):
            self._update_menu()

    def _activated_event(self, *_):
        allow_controller_foreground(self.worker.actions.state.get('controllerPid'))

    def _stop_event(self, *_):
        if self.worker.submit('stop'):
            self._update_menu()

    def _on_result(self, action, state, error):
        def display():
            self._available = error is None
            self._update_menu()
            if self.form.ContainsFocus:
                # The first state response can arrive after initial Form.Activated.
                # Grant only the authenticated controller, without raising this form.
                self._activated_event()
            if error and action in ('start', 'stop'):
                from System.Windows.Forms import ToolTipIcon
                self.icon.ShowBalloonTip(3500, TITLE, error, ToolTipIcon.Warning)
                self.show()
        self._invoke(display)

    def show(self):
        def restore():
            from System.Windows.Forms import FormWindowState
            if self.form.WindowState == FormWindowState.Minimized:
                self.form.WindowState = FormWindowState.Normal
            self.form.Show()
            self.form.BringToFront()
            self.form.Activate()
            try:
                user = ctypes.WinDLL('user32', use_last_error=True)
                user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
                user.SetForegroundWindow.restype = ctypes.c_int
                user.SetForegroundWindow(self.form.Handle.ToInt64())
            except (OSError, AttributeError):
                pass
        self._invoke(restore)

    def _show_event(self, *_):
        self.show()

    def _exit_event(self, *_):
        # Preserve pywebview's localized quit confirmation and controller teardown.
        self.show()
        self.form.Close()

    def _form_closed(self, *_):
        self.close()

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        # Cancel API operations immediately, before joining the activation watcher.
        # A start waiting for its JS selection must not continue during teardown.
        self.client.close()
        if getattr(self, '_test_timer', None) is not None:
            self._test_timer.Stop()
            self._test_timer.Dispose()
        if self.timer is not None:
            self.timer.Stop()
            self.timer.Dispose()
        if self.icon is not None:
            self.icon.Visible = False
            self.icon.Dispose()
            self._tray_disposed = True
        if self.menu is not None:
            self.menu.Dispose()
        if getattr(self, '_owned_icon', None) is not None:
            self._owned_icon.Dispose()
        if self.watcher is not None:
            self.watcher.close()
        self.worker.close()
        if self.window_file is not None:
            try:
                record = json.loads(self.window_file.read_text(encoding='utf-8'))
                if record.get('pid') == os.getpid():
                    self.window_file.unlink()
            except (OSError, ValueError):
                pass
        if self.diagnostic_file is not None:
            self._diagnostics['closed'] = {
                'tray_visible': False, 'tray_disposed': self.icon is None or self._tray_disposed,
                'worker_alive': self.worker.thread.is_alive(),
                'activation_alive': bool(self.watcher and self.watcher.thread
                                         and self.watcher.thread.is_alive()),
                'window_record_removed': not self.window_file or not self.window_file.exists(),
            }
            self._write_json(self.diagnostic_file, self._diagnostics)


def check_dependencies() -> dict[str, object]:
    """Import the shipped Windows renderer without creating any window."""
    import webview
    import pythonnet
    import clr
    from webview.util import interop_dll_path

    importlib.import_module("webview.platforms.edgechromium")
    forms = importlib.import_module("webview.platforms.winforms")
    dependencies = {
        name: str(Path(interop_dll_path(name)).resolve())
        for name in (
            "Microsoft.Web.WebView2.Core.dll",
            "Microsoft.Web.WebView2.WinForms.dll",
        )
    }
    for name, location in dependencies.items():
        if not Path(location).is_file():
            raise RuntimeError(f"缺少桌面运行组件：{name}")
    if not forms.is_chromium:
        raise RuntimeError("未找到 Microsoft Edge WebView2 Runtime，请安装后重试。")
    return {
        "ok": True,
        "renderer": "edgechromium",
        "python": sys.version.split()[0],
        "webview": str(Path(webview.__file__).resolve()),
        "pythonnet": str(Path(pythonnet.__file__).resolve()),
        "dependencies": dependencies,
    }


def show_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)
    except (AttributeError, OSError):
        if sys.stderr:
            print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PVZ OL 桌面启动器窗口")
    parser.add_argument("--url", help="控制器提供的本机 HTTP 页面地址")
    parser.add_argument("--data-dir", type=Path, help="项目缓存目录的父目录")
    parser.add_argument("--self-test", action="store_true", help="仅导入桌面组件，不打开窗口")
    parser.add_argument("--native-self-test-report", type=Path,
                        help="隔离 QA：记录原生位置和托盘状态，显示后自动关闭")
    args = parser.parse_args(argv)

    if args.self_test:
        try:
            report = check_dependencies()
            if sys.stdout:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            if sys.stderr:
                print(f"Desktop dependency check failed: {exc}", file=sys.stderr)
            return 2

    if not args.url or args.data_dir is None:
        parser.error("请提供 --url 和 --data-dir。")
    tray = None
    try:
        url = validate_local_url(args.url)
        data_dir = args.data_dir.resolve()
        cache_dir = data_dir / "cache" / "webview"
        cache_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=data_dir / "ui-shell.log",
            encoding="utf-8",
            level=logging.WARNING,
            format="%(asctime)s %(levelname)s %(message)s",
        )
        check_dependencies()
        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        window = webview.create_window(
            TITLE,
            url=url,
            width=1280,
            height=860,
            min_size=(960, 700),
            resizable=True,
            frameless=False,
            confirm_close=not bool(args.native_self_test_report),
            localization=LOCALIZATION,
            background_color="#f8fafc",
            text_select=True,
        )
        tray = NativeTray(window, LoopbackClient(url),
                          window_file=os.environ.get('PVZ_LAUNCHER_WINDOW_FILE'),
                          diagnostic_file=args.native_self_test_report,
                          auto_close=bool(args.native_self_test_report))
        window.events.before_show += tray.attach
        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=True,
            storage_path=str(cache_dir),
            localization=LOCALIZATION,
        )
        return 0
    except Exception as exc:
        # Renderer errors can echo the navigation URL, whose query has a token.
        logging.error('桌面窗口启动失败（%s）。', type(exc).__name__)
        show_error('桌面窗口启动失败。\n\n请检查项目内的 ui-shell.log。')
        return 1
    finally:
        if tray is not None:
            tray.close()


if __name__ == "__main__":
    raise SystemExit(main())
