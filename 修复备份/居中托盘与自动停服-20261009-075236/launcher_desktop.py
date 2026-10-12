"""Loopback API and portable desktop shell entry point for the PVZ launcher."""
from __future__ import annotations

import argparse
import http.server
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from launcher_service import LauncherService

ROOT = Path(__file__).resolve().parent


class LauncherHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, service, port=0):
        self.service = service
        self.token = secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1', port), LauncherHandler)
        self.origin = 'http://127.0.0.1:%d' % self.server_address[1]
        self.service.origin = self.origin

    @property
    def url(self):
        return self.origin + '/?token=' + self.token


class LauncherHandler(http.server.BaseHTTPRequestHandler):
    server_version = 'PvZLauncher/2.0'

    def log_message(self, *_args):
        pass

    def _host_allowed(self):
        if self.headers.get('Host', '') != '127.0.0.1:%d' % self.server.server_address[1]:
            self._json(None, '访问来源无效。', 403)
            return False
        origin = self.headers.get('Origin')
        if origin and origin != self.server.origin:
            self._json(None, '访问来源无效。', 403)
            return False
        return True

    def _authorized(self):
        token = self.headers.get('X-PVZ-Token', '')
        return bool(token) and secrets.compare_digest(token, self.server.token)

    def _cookie_authorized(self):
        # This cookie contains only a URL-safe random token; avoid requiring
        # http.cookies, absent from the existing portable interpreter bundle.
        for entry in self.headers.get('Cookie', '').split(';'):
            key, sep, token = entry.strip().partition('=')
            if key == 'PVZLauncherToken' and sep:
                return bool(token) and secrets.compare_digest(token, self.server.token)
        return False

    def _send(self, data, content_type, status=200, headers=None):
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-src http://127.0.0.1:8081; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data=None, error=None, status=200):
        result = {'ok': False, 'error': str(error)} if error is not None else {'ok': True, 'data': data}
        self._send(json.dumps(result, ensure_ascii=False), 'application/json; charset=utf-8', status)

    def _body(self):
        if not self.headers.get('Content-Type', '').startswith('application/json'):
            raise ValueError('请求格式必须为 JSON。')
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 8 * 1024 * 1024:
            raise ValueError('请求内容过大或为空。')
        value = json.loads(self.rfile.read(size).decode('utf-8'))
        if not isinstance(value, dict):
            raise ValueError('请求内容格式不正确。')
        return value

    def do_GET(self):
        self._request('GET')

    def do_POST(self):
        self._request('POST')

    def do_PATCH(self):
        self._request('PATCH')

    def do_DELETE(self):
        self._request('DELETE')

    def _request(self, method):
        if not self._host_allowed():
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        service = self.server.service
        try:
            if path.startswith('/api/'):
                if not self._authorized():
                    self._json(None, '会话已失效，请重新打开启动器。', 403)
                    return
                data = self._body() if method != 'GET' else {}
                parts = path.strip('/').split('/')
                result = None
                if path == '/api/state' and method == 'GET':
                    result = service.state()
                elif path == '/api/accounts' and method == 'GET':
                    result = service.accounts(query.get('q', [''])[0])
                elif path == '/api/accounts' and method == 'POST':
                    result = service.create_account(data.get('uin'))
                elif len(parts) == 3 and parts[1] == 'accounts':
                    if method == 'GET':
                        result = service.account(parts[2])
                    elif method == 'PATCH':
                        result = service.save_profile(parts[2], data)
                    elif method == 'DELETE':
                        result = service.delete_account(parts[2], data.get('confirm_uin'))
                    else:
                        raise ValueError('操作不支持。')
                elif path == '/api/start' and method == 'POST':
                    result = service.start(data.get('account_id'))
                elif path == '/api/stop' and method == 'POST':
                    result = service.stop()
                elif path == '/api/admin/start' and method == 'POST':
                    result = service.start_admin()
                elif path == '/api/admin-info' and method == 'GET':
                    result = service.admin_info()
                elif path == '/api/logs' and method == 'GET':
                    result = service.get_logs(int(query.get('after', ['0'])[0]), query.get('level', [''])[0], query.get('query', [''])[0])
                elif path == '/api/logs/export' and method == 'GET':
                    lines = ['%s [%s] %s' % (row['time'], row['level'].upper(), row['message']) for row in service.get_logs()['entries']]
                    self._send('\ufeff' + '\n'.join(lines), 'text/plain; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="pvzol-launcher.log"'})
                    return
                elif path == '/api/backups' and method == 'GET':
                    result = service.backups()
                elif path == '/api/backups' and method == 'POST':
                    result = service.create_backup(data.get('reason', 'manual'))
                elif len(parts) == 4 and parts[1] == 'backups' and parts[3] == 'restore' and method == 'POST':
                    result = service.restore_backup(parts[2], data.get('confirm'))
                elif path == '/api/settings' and method == 'GET':
                    result = dict(service.settings)
                elif path == '/api/settings' and method == 'PATCH':
                    result = service.save_settings(data)
                else:
                    self._json(None, '接口不存在。', 404)
                    return
                self._json(result)
            elif method == 'GET':
                if path.startswith('/avatar/'):
                    if not self._cookie_authorized() and not self._authorized():
                        self._send('', 'text/plain', 403)
                        return
                    directory = service.root / 'webroot' / 'image' / 'head'
                    name = path.removeprefix('/avatar/')
                    if name == '0.png' and not (directory / name).is_file():
                        name = 'unknow.png'
                else:
                    directory = service.root / 'launcher_ui'
                    name = 'index.html' if path in ('', '/') else path.lstrip('/')
                    if name not in ('index.html', 'app.js', 'styles.css') and not name.startswith('assets/'):
                        self._send('文件不存在。', 'text/plain; charset=utf-8', 404)
                        return
                target = (directory / name).resolve()
                if not target.is_relative_to(directory.resolve()) or not target.is_file():
                    self._send('文件不存在。', 'text/plain; charset=utf-8', 404)
                    return
                content_type = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
                if content_type.startswith('text/') or content_type in ('application/javascript', 'application/json'):
                    content_type += '; charset=utf-8'
                headers = {}
                supplied_token = query.get('token', [''])[0]
                if path == '/' and supplied_token and secrets.compare_digest(supplied_token, self.server.token):
                    headers['Set-Cookie'] = 'PVZLauncherToken=%s; Path=/; HttpOnly; SameSite=Strict' % self.server.token
                self._send(target.read_bytes(), content_type, headers=headers)
            else:
                self._json(None, '接口不存在。', 404)
        except (ValueError, TypeError, KeyError) as exc:
            self._json(None, str(exc), 400)
        except Exception as exc:
            service.log('!! 操作失败：%s' % exc)
            self._json(None, '操作失败：%s' % exc, 500)
        finally:
            try:
                import player_store
                player_store.close_thread_db()
            except ImportError:
                pass


def run(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--api-only', action='store_true')
    parser.add_argument('--no-player', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--connection-file')
    args, _ = parser.parse_known_args(argv)
    # The existing portable bootstrap normalizes sys.argv before loading source.
    # Environment switches support isolated QA without repackaging that bootstrap.
    args.api_only = args.api_only or os.environ.get('PVZ_LAUNCHER_API_ONLY') == '1'
    args.no_player = args.no_player or os.environ.get('PVZ_LAUNCHER_NO_PLAYER') == '1'
    args.connection_file = args.connection_file or os.environ.get('PVZ_LAUNCHER_CONNECTION_FILE')
    service = LauncherService(ROOT, launch_player=not args.no_player)
    server = LauncherHTTPServer(service, args.port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if args.connection_file:
        Path(args.connection_file).write_text(json.dumps({'url': server.url, 'origin': server.origin, 'token': server.token}), encoding='utf-8')
    try:
        if args.api_only:
            threading.Event().wait()
        else:
            executable = ROOT / '启动器界面' / 'PvZLauncherUI.exe'
            if executable.is_file():
                command = [str(executable), '--url', server.url, '--data-dir', str(ROOT / 'launcher_cache')]
            else:
                raise RuntimeError('新启动器界面运行文件缺失，请检查“启动器界面”文件夹。可使用 --legacy-ui 打开旧界面。')
            shell = subprocess.Popen(command, cwd=ROOT, **__import__('launcher').HIDDEN_PROCESS)
            shell.wait()
    finally:
        service.close()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    run()
