"""One-use loopback loader response held until Ruffle's hidden window is ready."""
from __future__ import annotations

import http.server
from pathlib import Path
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit


class RuffleLoaderGate:
    """Keep SWF metadata unavailable until the owned HWND is pre-positioned.

    The CLI spoofs ``original_url`` and retains its original resource base; this
    listener only supplies the initial loader bytes. Failure is closed (503),
    so a late placement worker cannot reveal an unpositioned game window.
    """

    def __init__(self, loader_path, original_url, *, timeout=12):
        parsed = urlsplit(original_url)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
                parsed.port is None or parsed.username or parsed.password or
                not parsed.path.endswith('/loader_n.swf') or parsed.fragment):
            raise ValueError('Ruffle 启动门只能加载本机游戏加载器')
        with Path(loader_path).open('rb') as stream:
            payload = stream.read(32 * 1024 * 1024 + 1)
        if len(payload) < 8 or len(payload) > 32 * 1024 * 1024 or payload[:3] not in (b'FWS', b'CWS', b'ZWS'):
            raise ValueError('Ruffle 游戏加载器数据无效')
        self._payload = payload
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._claimed = False
        self._connections = set()
        self._deadline = time.monotonic() + max(.05, float(timeout))
        nonce = secrets.token_urlsafe(24)
        self._target = '/' + nonce + '/loader_n.swf' + ('?' + parsed.query if parsed.query else '')
        gate = self

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # Neither the nonce nor private flashvars go into logs.

            def do_GET(self):
                with gate._lock:
                    valid = (not gate._closed.is_set() and self.path == gate._target and
                             self.headers.get('Host') == '127.0.0.1:%d' % self.server.server_port and
                             not gate._claimed)
                    if valid:
                        gate._claimed = True
                        gate._connections.add(self.connection)
                if not valid:
                    self.send_error(404)
                    return
                try:
                    while not gate._ready.is_set() and not gate._closed.is_set():
                        remaining = gate._deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        gate._ready.wait(min(.05, remaining))
                    with gate._lock:
                        payload = gate._payload if gate._ready.is_set() and not gate._closed.is_set() else None
                    if payload is None:
                        self.send_error(503, 'Game window initialization did not complete')
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/x-shockwave-flash')
                    self.send_header('Content-Length', str(len(payload)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(payload)
                except (OSError, ValueError):
                    pass  # Cancellation may close this exact owned connection.
                finally:
                    with gate._lock:
                        gate._connections.discard(self.connection)

        self._server = Server(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d%s' % (self._server.server_port, self._target)
        try:
            self._thread = threading.Thread(target=self._server.serve_forever,
                                            kwargs={'poll_interval': .05}, name='Ruffle loader gate', daemon=True)
            self._thread.start()
        except BaseException:
            self._closed.set()
            self._server.server_close()
            self._payload, self._target, self.url = b'', None, ''
            raise

    def release(self):
        with self._lock:
            if self._closed.is_set() or time.monotonic() >= self._deadline:
                return False
            self._ready.set()
            return True

    @property
    def expired(self):
        return (not self._closed.is_set() and not self._ready.is_set() and
                time.monotonic() >= self._deadline)

    def close(self):
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
            self._ready.set()
            self._target = None
            self.url = ''
            self._payload = b''
            connections = tuple(self._connections)
        # Wake the HTTP wait first, then close only this gate's sockets/listener.
        self._server.shutdown()
        self._server.server_close()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        self._thread.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
