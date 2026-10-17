"""Local launcher application services, independent of the desktop renderer."""
from __future__ import annotations

import base64
from contextlib import closing
from collections import deque
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

import launcher as legacy

PROFILE_KEYS = ('name', 'signature', 'face_id', 'qq_face_url')
DISPLAY_TIMEZONE = dt.timezone(dt.timedelta(hours=8))

# Log entries carry a level and a source so the launcher can filter them.
# Game server lines look like "12:00:01  WARNING  message"; run_all prefixes
# its web/admin threads with "[web]" / "[admin]"; the policy server logs "[policy]".
LOG_SOURCES = ('launcher', 'game', 'web', 'admin', 'policy')
LOG_LEVELS = ('debug', 'info', 'warning', 'error')
_GAME_LOG_LINE = re.compile(r'^\d{2}:\d{2}:\d{2}\s+(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s+(.*)$', re.S)
_GAME_LEVELS = {'DEBUG': 'debug', 'INFO': 'info', 'WARNING': 'warning', 'WARN': 'warning',
                'ERROR': 'error', 'CRITICAL': 'error'}
# serve_client.py request lines: "  GET /conf/init.xml HTTP/1.1 -> 404  <<< NOT FOUND"
_WEB_REQUEST_LINE = re.compile(r'^\s+[A-Z]{3,7} \S+ HTTP/\d(?:\.\d)? -> (\d{3})')
_WEB_NOTICES = ('CARD IMAGE FALLBACK', 'CARD ICON ALIAS', 'PLANT FRAGMENT ICON', 'PUZZLE ICON SYMBOL',
                'ITEM ICON FALLBACK', 'MODULEHOLE ALIAS', 'ASSET FALLBACK', 'FRAGMENT ICON CONFIG ERROR')
STATUS_FRESH_SECONDS = 6.0   # runtime_status.py rewrites at least every 3 s

# Units that the stop dialog can address. Ports are attributes of the
# service group, never separate processes.
UNIT_IDS = ('services', 'player', 'admin')
SERVICE_COMPONENTS = (
    ('web', '游戏资源网页', 'WEB_PORT', '向播放器提供游戏 SWF、配置表和图片'),
    ('admin', 'GM 后台（内置）', 'ADMIN_PORT', '启动器“GM 管理”页面使用的后台'),
    ('game', '游戏逻辑服务', 'GAME_PORT', '登录、战斗、背包等全部游戏协议；读写 players.db'),
    ('policy', 'Flash 连接策略', 'POLICY_PORT', 'Flash 连接游戏端口前需要的授权（843）'),
)


def classify_log_line(message, origin='launcher'):
    """Return (source, level) for one log line; origin is the producing process."""
    source = {'server': 'game', 'admin': 'admin'}.get(origin, 'launcher')
    level = None
    body = message
    match = _GAME_LOG_LINE.match(message)
    if match:
        level, body = _GAME_LEVELS[match[1]], match[2]
    tag = body.lstrip()
    for prefix, name in (('[web]', 'web'), ('[admin]', 'admin'), ('[policy]', 'policy'), ('[status]', 'game')):
        if tag.startswith(prefix):
            source = name
            break
    else:
        request = _WEB_REQUEST_LINE.match(body) if origin == 'server' else None
        if request:
            source = 'web'
            code = int(request[1])
            level = level or ('error' if code >= 500 else 'warning' if code >= 400 else 'info')
        elif origin == 'server' and tag.startswith(_WEB_NOTICES):
            source = 'web'
            level = level or ('error' if 'ERROR' in tag else 'warning')
    text = message.lower()
    if message.startswith('!!') or any(v in text for v in ('traceback', 'exception', 'crashed')):
        level = 'error'
    return source, level or 'info'


class LauncherService:
    """Owns processes and serializes changes to local saves."""
    def __init__(self, root=None, *, launch_player=True):
        self.root = Path(root or __file__).resolve()
        if self.root.is_file():
            self.root = self.root.parent
        self.server_dir = self.root / 'PvZ-Server-PoC'
        self.db_path = self.server_dir / 'players.db'
        self.backup_dir = self.server_dir / 'backups' / 'launcher'
        self.settings_path = self.root / 'launcher_settings.json'
        self.lock = threading.RLock()
        self.log_lock = threading.Lock()
        self.logs = deque(maxlen=1800)
        self.log_id = 0
        self.server = self.admin = self.game = None
        self._loader_gate = None
        self.session = 0
        self.cancel = threading.Event()
        self.running = self.busy = self.closed = False
        self.status = '准备就绪'
        self.error = ''
        self.progress = 0
        self.origin = ''
        self.launch_player = launch_player
        self.config = legacy.load_config()
        self.active_account = self.config.get('last_uin', legacy.DEFAULT_UIN)
        self.settings = self._load_settings()
        self.player_kind, self.player_path = legacy.resolve_game_player(self.settings['player'], root=self.root)
        if str(self.server_dir) not in sys.path:
            sys.path.insert(0, str(self.server_dir))
        self.cache_dir = self.root / 'launcher_cache'
        self.status_files = {}
        self.started_at = {}
        self.loader = ''
        self._port_cache = None
        self._confirmed_ports = set()
        self.guard_path = self.root / 'launcher_cache' / ('lifecycle-%s.json' % os.getpid())
        self.window_info_path = self.guard_path.with_suffix('.window.json')
        self.ui_pid = None
        self._publish_guard()
        self.log('启动器已就绪，选择角色后开始游戏。')

    def _publish_guard(self):
        from launcher_guard import write_guard
        write_guard(self.guard_path, busy=self.busy, running=self.running)

    def _load_settings(self):
        result = {'theme': 'light', 'scale': 100, 'player': 'flash' if legacy.IS_WINDOWS else 'ruffle'}
        try:
            value = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                if value.get('theme') in ('light', 'dark', 'auto'):
                    result['theme'] = value['theme']
                if type(value.get('scale')) is int and 80 <= value['scale'] <= 150:
                    result['scale'] = value['scale']
                if value.get('player') in ('flash', 'ruffle'):
                    result['player'] = value['player']
        except (OSError, ValueError):
            pass
        return result

    def log(self, message, level=None, origin='launcher'):
        message = str(message).rstrip()
        if not message:
            return
        source, detected = classify_log_line(message, origin)
        level = level if level in LOG_LEVELS else detected
        with self.log_lock:
            self.log_id += 1
            stamp = dt.datetime.now(DISPLAY_TIMEZONE)
            self.logs.append({'id': self.log_id, 'time': stamp.strftime('%H:%M:%S'), 'level': level,
                              'source': source, 'message': message})
            path = self.root / 'portable_launcher.log'
            try:
                if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                    os.replace(path, str(path) + '.1')
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(stamp.strftime('%Y-%m-%d %H:%M:%S ') + message + '\n')
            except OSError:
                pass

    def get_logs(self, after=0, level='', query='', source=''):
        """Filter the in-memory log. ``level`` may be a comma list; "warn" means warning."""
        levels = {('warning' if value == 'warn' else value) for value in str(level or '').split(',') if value}
        sources = {value for value in str(source or '').split(',') if value}
        terms = str(query or '').casefold().split()
        with self.log_lock:
            entries = [dict(row) for row in self.logs if row['id'] > after
                       and (not levels or row['level'] in levels)
                       and (not sources or row.get('source', 'launcher') in sources)
                       and all(term in row['message'].casefold() for term in terms)]
            return {'entries': entries, 'lastId': self.log_id, 'capacity': self.logs.maxlen}

    def _number(self, value):
        from account_management import _account_number
        return _account_number(value)

    def _player_number(self, value):
        number = self._number(value)
        if not legacy.valid_player_uin(str(number)):
            raise ValueError('这个编号属于系统人机，请选择玩家账号。')
        return number

    def _offline(self):
        if self.running or self.busy or legacy.port_open(legacy.GAME_PORT):
            raise ValueError('请先停止游戏服务，再修改角色、存档或恢复备份。')

    def _alive(self, process):
        return process is not None and process.poll() is None

    def _current(self, process, session):
        return not self.closed and session == self.session and process is self.server and self._alive(process)

    def _command(self, script, *args):
        # The existing portable interpreter executes the current external source.
        return legacy.python_command(str(self.server_dir / script), *args)

    def _environment(self):
        return {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1',
                'PVZ_LAUNCHER_ORIGIN': self.origin,
                'PVZ_LAUNCHER_GUARD': str(self.guard_path),
                'PVZ_LAUNCHER_OWNER_PID': str(os.getpid())}

    def _spawn(self, script):
        # Each managed process reports what it can observe (open game
        # connections, request totals) into its own status file.
        stem = Path(script).stem
        status_file = self.cache_dir / ('runtime-%s-%s.json' % (os.getpid(), stem))
        try:
            status_file.parent.mkdir(parents=True, exist_ok=True)
            status_file.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.log('活动状态文件未能重置：%s' % exc, 'warning')
        process = subprocess.Popen(self._command(script), cwd=self.server_dir,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace', bufsize=1,
                                   env={**self._environment(), 'PVZ_LAUNCHER_STATUS_FILE': str(status_file)},
                                   **legacy.HIDDEN_PROCESS)
        self.status_files[stem] = (status_file, process, time.time())
        return process

    def _terminate(self, process, name):
        if not self._alive(process):
            return True
        try:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            self.log('!! 无法停止%s：%s' % (name, exc))
            return not self._alive(process)

    def _read_process(self, process, session, kind):
        try:
            if process.stdout:
                for line in process.stdout:
                    if session == self.session:
                        self.log(line, origin=kind)
                        if kind == 'server' and line.startswith(('[web] crashed:', '[admin] crashed:')):
                            self._fail_startup(process, session, '本地服务启动失败：' + line.strip())
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                if kind == 'server' and self._current(process, session):
                    return
                if kind == 'server' and process is self.server and session == self.session and not self.closed:
                    self.log('!! 游戏服务已退出，正在回收本次游戏进程。')
                    self.stop()
                    self.error = '游戏服务已退出，请查看日志后重新启动。'
                    self.status = self.error
                elif kind == 'admin' and process is self.admin and session == self.session and not self._alive(process):
                    self.admin = None
                    self.log('GM 后台已停止。')

    def start(self, account_id):
        with self.lock:
            if self.closed:
                raise ValueError('启动器正在关闭。')
            if self.running or self.busy:
                raise ValueError('游戏已经启动或正在初始化。')
            number = self._player_number(account_id)
            if any(self._alive(p) for p in (self.server, self.game)):
                raise ValueError('上次进程尚未停止，请先点击停止。')
            self.player_kind, self.player_path = legacy.resolve_game_player(self.player_kind, root=self.root)
            if self.launch_player and not self.player_path:
                raise ValueError(self._missing_player(self.player_kind))
            loader = 'loader_n.swf' if self.player_kind == 'ruffle' else 'loader_p.swf'
            if not (self.server_dir / 'run_all.py').is_file() or not (self.root / 'webroot' / loader).is_file():
                raise ValueError('游戏服务或 loader 文件缺失，请检查完整游戏目录。')
            self.session += 1
            self.cancel.set()
            self.cancel = threading.Event()
            self.active_account = str(number)
            self.config.update(last_uin=self.active_account, recent=([self.active_account] + [v for v in self.config.get('recent', []) if v != self.active_account])[:8])
            if not legacy.save_config(self.config):
                self.log('最近账号未能写入配置。', 'warning')
            self.error = ''
            self.progress = 8
            self.status = '正在启动本地服务…'
            self.busy = True
            try:
                self._publish_guard()
                if self.admin and not self._terminate(self.admin, 'GM 后台'):
                    raise ValueError('GM 后台尚未停止，请重试。')
                self.admin = None
                busy_ports = [p for p in (legacy.WEB_PORT, legacy.ADMIN_PORT, legacy.GAME_PORT, legacy.POLICY_PORT) if legacy.port_open(p)]
                if busy_ports:
                    raise ValueError('端口 %s 已被占用，请关闭旧启动器或对应服务后重试。' % '、'.join(map(str, busy_ports)))
                process = self._spawn('run_all.py')
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                self.running = self.busy = False
                self.progress = 0
                self.error = '游戏服务未能启动：%s' % exc
                self.status = self.error
                self.log('!! ' + self.error)
                try:
                    self._publish_guard()
                except OSError:
                    pass
                raise ValueError(self.error) from exc
            self.server = process
            self._confirmed_ports = set()
            self.loader = loader
            self.started_at['services'] = time.time()
            self.running = self.busy = True
            try:
                self._publish_guard()
            except OSError as exc:
                self.log('!! 运行状态未更新，保持启动写保护：%s' % exc)
            self.log('开始初始化游戏 · 账号 %s' % number)
            threading.Thread(target=self._read_process, args=(process, self.session, 'server'), daemon=True).start()
            threading.Thread(target=self._wait_ready, args=(process, self.session, loader, self.cancel), daemon=True).start()
            return {'status': self.status}

    def _wait_ready(self, process, session, loader, cancel):
        deadline = time.monotonic() + legacy.START_TIMEOUT
        pending = None
        names = ('资源服务 (8080)', 'GM 后台 (8081)', '游戏服务 (9001)', 'Flash 策略 (843)')
        confirmed = [False] * 4
        probes = (lambda: legacy.web_ready(loader), legacy.admin_ready, self._game_port_ready, legacy.policy_ready)
        while not cancel.is_set() and time.monotonic() < deadline:
            # A component stays ready once confirmed (the process itself is still
            # watched), so later loops do not keep adding probe lines to the log.
            confirmed = [done or probe() for done, probe in zip(confirmed, probes)]
            checks = list(confirmed)
            with self.lock:
                if not self._current(process, session):
                    return
                self.progress = 12 + 18 * sum(checks)
                waiting = tuple(name for name, ready in zip(names, checks) if not ready)
                if waiting:
                    self.status = '正在等待：' + '、'.join(waiting)
                    if waiting != pending:
                        self.log(self.status)
                        pending = waiting
                if all(checks):
                    self._confirmed_ports = {'game', 'policy'}
                    self.busy = False
                    try:
                        self._publish_guard()
                    except OSError as exc:
                        self.log('!! 服务状态未更新，保持写保护：%s' % exc)
                    self.progress = 100
                    self.status = '游戏服务运行中'
                    self.log('四项服务已就绪。')
                    if self.launch_player:
                        try:
                            self._open_player(process, session, loader, cancel)
                        except Exception as exc:
                            reason = '播放器未能打开：%s' % exc
                            try:
                                self.stop()
                            except ValueError:
                                reason += '；部分服务未能停止，请重试停止。'
                            self.error = reason
                            self.status = '服务就绪，播放器启动失败'
                            if not self.running:
                                self.status = '播放器启动失败，服务已停止'
                            self.log('!! ' + self.error)
                    return
            cancel.wait(.3)
        with self.lock:
            if self._current(process, session) and not cancel.is_set():
                self.stop()
                self.error = '启动超时，未就绪：%s。已清理本次进程，请查看日志后重试。' % '、'.join(pending or names)
                self.status = self.error
                self.log('!! ' + self.error)

    def _game_port_ready(self):
        status = self._runtime_status('run_all')
        if status and status.get('fresh'):
            return legacy.GAME_PORT in (status['components'].get('game') or {}).get('ports', [])
        return legacy.port_open(legacy.GAME_PORT)

    def _open_player(self, process, session, loader, cancel):
        """Start the player for the running service group (caller holds self.lock)."""
        url = legacy.game_url(self.active_account, loader)
        from launcher_game_window import game_startup_options, read_swf_stage, ruffle_initial_size
        window_options = game_startup_options(self.player_kind)
        native_ruffle = self.player_kind == 'ruffle' and sys.platform == 'win32'
        stage_size = (read_swf_stage(self.root / 'webroot' / loader)
                      if window_options or native_ruffle else None)
        ruffle_size = None
        gate = None
        if native_ruffle:
            from launcher_ruffle_gate import RuffleLoaderGate
            ruffle_size = ruffle_initial_size(stage_size, owner_file=self.window_info_path,
                                              owner_pid=self.ui_pid)
            gate = self._loader_gate = RuffleLoaderGate(self.root / 'webroot' / loader, url)
        command = legacy.game_player_command(self.player_kind, self.player_path,
                                              gate.url if gate else url, initial_size=ruffle_size)
        if gate:
            # Root ActionScript URL/flashvars and resource origin retain
            # their normal 8080 values; only the response is delayed.
            command[-1:-1] = ['--spoof-url', url]
        self.game = subprocess.Popen(command,
                                     cwd=self.root, env=legacy.game_player_environment(self.player_kind),
                                     **legacy.HIDDEN_PROCESS, **window_options)
        self.started_at['player'] = time.time()
        self.status = '正在显示游戏窗口…' if window_options or gate else '游戏已打开 · 祝你玩得开心'
        game = self.game
        threading.Thread(target=self._watch_game, args=(game, process, session, cancel), daemon=True).start()
        placement = {'initially_hidden': bool(window_options), 'stage_size': stage_size}
        if gate:
            placement.update(ruffle_size=ruffle_size, loader_gate=gate)
        threading.Thread(target=self._place_game_window, args=(game, process, session, cancel),
                         kwargs=placement, daemon=True).start()
        return game

    def reopen_player(self):
        """Open the game window again while the service group keeps running."""
        with self.lock:
            if self.closed:
                raise ValueError('启动器正在关闭。')
            if not self.launch_player:
                raise ValueError('当前为仅服务模式，不会打开游戏窗口。')
            if not self._alive(self.server) or not self.running:
                raise ValueError('游戏服务未运行，请先启动游戏。')
            if self.busy:
                raise ValueError('游戏服务仍在初始化，请稍候。')
            if self._alive(self.game):
                raise ValueError('游戏窗口已经打开。')
            self.player_kind, self.player_path = legacy.resolve_game_player(self.player_kind, root=self.root)
            if not self.player_path:
                raise ValueError(self._missing_player(self.player_kind))
            try:
                self._open_player(self.server, self.session, self.loader or (
                    'loader_n.swf' if self.player_kind == 'ruffle' else 'loader_p.swf'), self.cancel)
            except Exception as exc:
                self.game = None
                self.error = '游戏窗口未能打开：%s' % exc
                self.log('!! ' + self.error)
                raise ValueError(self.error) from exc
            self.error = ''
            self.log('已重新打开游戏窗口 · 账号 %s' % self.active_account)
            return {'status': self.status}

    def _fail_startup(self, process, session, reason):
        """A dead required service cannot become ready by waiting longer."""
        with self.lock:
            if not self.busy or not self._current(process, session):
                return
            try:
                self.stop()
            except ValueError:
                reason += '；部分服务未能停止，请重试停止。'
            self.error = self.status = reason
            self.log('!! ' + reason)

    def _game_current(self, game, server, session, cancel):
        return (not self.closed and not cancel.is_set() and session == self.session and
                game is self.game and server is self.server)

    def _watch_game(self, game, server, session, cancel):
        while not cancel.is_set():
            with self.lock:
                if not self._game_current(game, server, session, cancel):
                    return
                if self._loader_gate is not None and self._loader_gate.expired:
                    self.stop()
                    self.status = '游戏窗口未能显示'
                    self.error = 'Ruffle 启动定位超时，已停止本次游戏服务，请重试。'
                    self.log('!! ' + self.error)
                    return
                code = game.poll()
                if code is not None:
                    self.log('游戏窗口已关闭，正在停止本次游戏服务（如只想关闭窗口，可在“停止服务”中只选择“游戏窗口”）。')
                    try:
                        self.stop()
                    except ValueError:
                        return
                    self.status = '游戏已关闭，服务已停止'
                    if code:
                        self.error = '游戏播放器已退出（代码 %s），本次服务已停止。' % code
                        self.log('!! ' + self.error)
                    return
            cancel.wait(.2)

    def _place_game_window(self, game, server, session, cancel, *, initially_hidden=False, stage_size=None,
                           ruffle_size=None, loader_gate=None):
        def is_current():
            with self.lock:
                return self._game_current(game, server, session, cancel) and self._alive(game)
        failure = '未找到可以居中显示的游戏窗口'
        try:
            from launcher_game_window import place_game_window
            options = {'ruffle_size': ruffle_size, 'on_prepared': loader_gate.release} if loader_gate else {}
            if place_game_window(game.pid, owner_file=self.window_info_path, owner_pid=self.ui_pid,
                                 is_current=is_current, cancel=cancel, initially_hidden=initially_hidden,
                                 stage_size=stage_size, **options):
                with self.lock:
                    if is_current():
                        self.status = '游戏已打开 · 祝你玩得开心'
                        self.log('游戏窗口已居中显示。')
                return
        except (AttributeError, OSError, ValueError) as exc:
            failure = '游戏窗口未能居中显示：%s' % exc
            self.log('游戏窗口位置未能调整：%s' % exc, 'warning')
        finally:
            if loader_gate:
                loader_gate.close()
                with self.lock:
                    if self._loader_gate is loader_gate:
                        self._loader_gate = None
        if initially_hidden or loader_gate:
            # A failed reveal must not leave an invisible player holding ports.
            # Check the exact session under the same lock used by stop/start.
            with self.lock:
                if not is_current():
                    return
                try:
                    self.stop()
                except ValueError:
                    failure += '；部分服务未能停止，请重试停止。'
                self.status = '游戏窗口未能显示'
                self.error = failure
                self.log('!! ' + failure)

    def stop(self):
        with self.lock:
            self.session += 1
            self.cancel.set()
            if self._loader_gate:
                self._loader_gate.close()
                self._loader_gate = None
            self.busy = True
            self.status = '正在停止服务…'
            try:
                self._publish_guard()
            except OSError as exc:
                self.log('!! 更新停止状态失败，仍将清理本次进程：%s' % exc)
            ok = True
            for attr, name in (('game', '游戏播放器'), ('server', '游戏服务'), ('admin', 'GM 后台')):
                process = getattr(self, attr)
                if self._terminate(process, name):
                    setattr(self, attr, None)
                    self.started_at.pop({'game': 'player', 'server': 'services'}.get(attr, attr), None)
                else:
                    ok = False
            self.running = any(self._alive(p) for p in (self.server, self.game))
            self.busy = False
            self.progress = 0
            self.status = '服务已停止' if ok else '部分服务未停止，请重试'
            self.error = '' if ok else self.status
            try:
                self._publish_guard()
            except OSError as exc:
                self.log('!! 写保护状态未能更新：%s' % exc)
            self.log(self.status, 'info' if ok else 'error')
            if not ok:
                raise ValueError(self.status)
            return {'status': self.status}

    # -- observable service units (stop dialog, services page, diagnostics) ----
    def _runtime_status(self, stem):
        """Parse the status file a managed process writes; None when unavailable."""
        record = self.status_files.get(stem)
        if not record:
            return None
        path, process, spawned = record
        if not self._alive(process):
            return None
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get('components'), dict):
            return None
        updated = float(data.get('updated') or 0)
        if updated < spawned - 5:
            return None  # left over from an earlier process
        data['fresh'] = time.time() - updated <= STATUS_FRESH_SECONDS
        data['age'] = max(0.0, time.time() - updated)
        return data

    def _service_ports(self, status=None):
        """Port state for the four service components.

        While our own service group runs and reports which ports it bound, the
        game (9001) and policy (843) sockets are not probed: every TCP probe is
        a real connection there and used to add "client connected" / "served
        policy" lines to the log on each 1.5 s state poll.
        """
        now = time.monotonic()
        own = self._alive(self.server)
        if status is None and own:
            status = self._runtime_status('run_all')
        cached = self._port_cache
        reported = {}
        if own and status and status.get('fresh'):
            for name in ('game', 'policy'):
                reported[name] = [int(p) for p in (status['components'].get(name) or {}).get('ports', [])]
                if name in self._confirmed_ports and not self.busy:
                    reported[name].append(getattr(legacy, 'GAME_PORT' if name == 'game' else 'POLICY_PORT'))
        result = {}
        for name, _title, attr, _role in SERVICE_COMPONENTS:
            port = getattr(legacy, attr)
            if name in reported:
                result[name] = port in reported[name]
            elif own and name in ('game', 'policy') and not self.busy and cached and now - cached[0] < 10:
                result[name] = cached[1].get(name, False)   # no fresh report: probe at most every 10 s
            else:
                result[name] = legacy.port_open(port, timeout=.08)
        if not reported:
            self._port_cache = (now, dict(result))
        return result

    @staticmethod
    def _activity(component, fresh):
        """Human readable activity; never invent data that was not observed."""
        if component is None or not fresh:
            return {'known': False, 'text': '未知（该进程未报告活动）'}
        last = float(component.get('last') or 0)
        ago = max(0, int(time.time() - last)) if last else None
        return {'known': True, 'active': int(component.get('active') or 0), 'total': int(component.get('total') or 0),
                'lastSeconds': ago, 'ports': [int(p) for p in component.get('ports', [])]}

    def _unit_alive(self):
        return {'services': self._alive(self.server), 'player': self._alive(self.game), 'admin': self._alive(self.admin)}

    def services_overview(self):
        """Everything the stop dialog needs; probes run outside the main lock."""
        with self.lock:
            alive = self._unit_alive()
            server, game, admin = self.server, self.game, self.admin
            busy, running = self.busy, self.running
            started = dict(self.started_at)
            player_name = 'Flash' if self.player_kind == 'flash' else 'Ruffle'
        server_status = self._runtime_status('run_all') if alive['services'] else None
        admin_status = self._runtime_status('admin_server') if alive['admin'] else None
        with self.lock:
            ports = self._service_ports(server_status)
        admin_ready = legacy.admin_ready() if (alive['services'] or alive['admin']) else False
        now = time.time()

        def uptime(key, active):
            return int(now - started[key]) if active and started.get(key) else None

        components = []
        fresh = bool(server_status and server_status.get('fresh'))
        for name, title, attr, role in SERVICE_COMPONENTS:
            port = getattr(legacy, attr)
            ready = admin_ready if name == 'admin' else ports[name]
            component = (server_status or {}).get('components', {}).get(name) if server_status else None
            activity = self._activity(component, fresh) if alive['services'] else {'known': False, 'text': '未运行'}
            listening = activity.get('ports') if activity.get('known') else None
            components.append({'id': name, 'name': title, 'port': port, 'ports': listening or [port], 'role': role,
                               'ready': bool(ready and alive['services']), 'activity': activity})
        player_state = '运行中' if alive['player'] else ('未打开（服务运行中）' if alive['services'] else '未运行')
        units = [
            {'id': 'services', 'name': '游戏服务组', 'process': 'run_all.py',
             'pid': server.pid if alive['services'] else None, 'running': alive['services'],
             'starting': bool(busy and alive['services']), 'uptime': uptime('services', alive['services']),
             'role': '一个进程同时提供下列 4 项服务（端口只是它的组成部分，不是独立进程）。',
             'components': components,
             'impact': '游戏立即断开并回到登录前状态；游戏窗口会一并关闭；启动器内的 GM 页面暂时不可用。'
                       '已写入 players.db 的数据不受影响。',
             'dependsOn': [], 'requiredBy': ['player']},
            {'id': 'player', 'name': '游戏窗口', 'process': player_name,
             'pid': game.pid if alive['player'] else None, 'running': alive['player'], 'state': player_state,
             'uptime': uptime('player', alive['player']),
             'role': '%s 播放器，显示游戏画面并连接游戏服务组。' % player_name,
             'activity': {'known': False, 'text': '未知（播放器不报告活动）'},
             'impact': '只关闭游戏画面，服务组继续运行；之后可点“重新打开游戏窗口”。',
             'dependsOn': ['services'], 'requiredBy': []},
            {'id': 'admin', 'name': '独立 GM 后台', 'process': 'admin_server.py',
             'pid': admin.pid if alive['admin'] else None, 'running': alive['admin'],
             'uptime': uptime('admin', alive['admin']),
             'role': '未启动游戏时，供“GM 管理”页面单独使用的后台（8081）。',
             'activity': self._activity((admin_status or {}).get('components', {}).get('admin'),
                                        bool(admin_status and admin_status.get('fresh'))) if alive['admin']
             else {'known': False, 'text': '未运行'},
             'impact': '“GM 管理”页面会断开，需要时可再次打开；不影响存档。',
             'dependsOn': [], 'requiredBy': []},
        ]
        foreign = []
        for name, title, attr, _role in SERVICE_COMPONENTS:
            port = getattr(legacy, attr)
            owned = alive['services'] or (name == 'admin' and alive['admin'])
            if not owned and ports[name]:
                foreign.append({'port': port, 'name': title,
                                'detail': '端口 %d 正被其他程序占用（不是本启动器启动的），本启动器不会停止它。' % port})
        return {'units': units, 'foreign': foreign, 'busy': busy, 'running': running,
                'checkedAt': dt.datetime.now(DISPLAY_TIMEZONE).strftime('%H:%M:%S')}

    def _close_player_locked(self):
        game = self.game
        if not self._alive(game):
            self.game = None
            return 'not_running', '游戏窗口没有打开，无需关闭。'
        # Detach first: the watcher treats a player exit as "end the session"
        # and would otherwise stop the whole service group.
        self.game = None
        if self._loader_gate:
            self._loader_gate.close()
            self._loader_gate = None
        if self._terminate(game, '游戏播放器'):
            self.started_at.pop('player', None)
            self.status = '游戏窗口已关闭 · 服务仍在运行'
            return 'stopped', '已关闭游戏窗口（PID %s）；服务组继续运行。' % game.pid
        self.game = game
        return 'failed', '游戏窗口（PID %s）未能关闭，请重试。' % game.pid

    def stop_units(self, units):
        """Stop the selected units and report what really happened to each one.

        Stopping the service group also closes the game window that depends on
        it. Processes not started by this launcher are never touched.
        """
        if units in ('all', ['all']):
            units = list(UNIT_IDS)
        if not isinstance(units, list) or not units or any(unit not in UNIT_IDS for unit in units):
            raise ValueError('请选择要停止的项目。')
        requested = [unit for unit in UNIT_IDS if unit in units]
        results = {}
        implied = []
        with self.lock:
            alive = self._unit_alive()
            plan = set(requested)
            if 'services' in plan and 'player' not in plan and alive['player']:
                plan.add('player')
                implied.append('player')
            names = {'services': '游戏服务组', 'player': '游戏窗口', 'admin': '独立 GM 后台'}
            if 'services' in plan:
                # Same sequence as stop(): end the session first so watchers and
                # the startup wait do not react to the processes we terminate.
                self.session += 1
                self.cancel.set()
                if self._loader_gate:
                    self._loader_gate.close()
                    self._loader_gate = None
                self.busy = True
                self.status = '正在停止服务…'
                try:
                    self._publish_guard()
                except OSError as exc:
                    self.log('!! 更新停止状态失败，仍将清理本次进程：%s' % exc)
                for unit, attr in (('player', 'game'), ('services', 'server')):
                    process = getattr(self, attr)
                    if not self._alive(process):
                        setattr(self, attr, None)
                        if unit in plan:
                            results[unit] = ('not_running', '%s未在运行。' % names[unit])
                    elif self._terminate(process, names[unit]):
                        setattr(self, attr, None)
                        self.started_at.pop(unit, None)
                        results[unit] = ('stopped', '已停止%s（PID %s）。' % (names[unit], process.pid))
                    else:
                        results[unit] = ('failed', '%s（PID %s）未能停止。' % (names[unit], process.pid))
                self.running = any(self._alive(p) for p in (self.server, self.game))
                self.busy = False
                self.progress = 0
            elif 'player' in plan:
                results['player'] = self._close_player_locked()
            if 'admin' in plan:
                process = self.admin
                if not self._alive(process):
                    self.admin = None
                    results['admin'] = ('not_running', '独立 GM 后台未在运行。')
                elif self._terminate(process, '独立 GM 后台'):
                    self.admin = None
                    self.started_at.pop('admin', None)
                    results['admin'] = ('stopped', '已停止独立 GM 后台（PID %s）。' % process.pid)
                else:
                    results['admin'] = ('failed', '独立 GM 后台（PID %s）未能停止。' % process.pid)
            failed = [unit for unit, (outcome, _d) in results.items() if outcome == 'failed']
            if 'services' in plan:
                self.status = '服务已停止' if not failed else '部分服务未停止，请重试'
                self.error = '' if not failed else self.status
            try:
                self._publish_guard()
            except OSError as exc:
                self.log('!! 写保护状态未能更新：%s' % exc)
        # Verify outside the lock that the stopped group really released its ports.
        if results.get('services', ('',))[0] == 'stopped' or results.get('admin', ('',))[0] == 'stopped':
            watch = [getattr(legacy, attr) for name, _t, attr, _r in SERVICE_COMPONENTS
                     if results.get('services', ('',))[0] == 'stopped' or name == 'admin']
            deadline = time.monotonic() + 3
            still = watch
            while still and time.monotonic() < deadline:
                still = [port for port in still if legacy.port_open(port, timeout=.08)]
                if still:
                    time.sleep(.2)
            if still:
                note = '；端口 %s 仍在监听，可能被其他程序占用' % '、'.join(map(str, still))
                for unit in ('services', 'admin'):
                    if results.get(unit, ('',))[0] == 'stopped':
                        results[unit] = ('stopped', results[unit][1] + note)
        report = []
        for unit in UNIT_IDS:
            if unit in results:
                outcome, detail = results[unit]
                if unit in implied:
                    detail += '（依赖游戏服务组，随之关闭）'
                report.append({'id': unit, 'outcome': outcome, 'detail': detail})
                self.log(detail, 'error' if outcome == 'failed' else 'info')
        if not report:
            raise ValueError('没有可停止的项目。')
        return {'results': report, 'ok': not any(row['outcome'] == 'failed' for row in report),
                'state': self.state()}

    def diagnostics(self):
        """One-click health check. Every row says what was checked and how."""
        checks = []

        def add(check_id, name, status, detail, action=''):
            checks.append({'id': check_id, 'name': name, 'status': status, 'detail': detail, 'action': action})

        missing = [name for name in ('run_all.py', 'admin_server.py', 'pvz_poc_server.py', 'serve_client.py')
                   if not (self.server_dir / name).is_file()]
        loaders = [name for name in ('loader_p.swf', 'loader_n.swf') if (self.root / 'webroot' / name).is_file()]
        add('files', '游戏文件', 'error' if missing or not loaders else 'ok',
            ('缺少 ' + '、'.join(missing)) if missing else ('缺少 webroot/loader_*.swf' if not loaders else
                                                          '服务脚本齐全；loader：' + '、'.join(loaders)),
            '请使用完整的游戏目录。' if missing or not loaders else '')
        for option in self._player_options():
            add('player-' + option['id'], '播放器 · ' + option['name'],
                'ok' if option['available'] else ('warn' if option['id'] != self.player_kind else 'error'),
                ('可用' if option['available'] else option['detail']) + ('（当前选择）' if option['id'] == self.player_kind else ''))
        if not self.db_path.is_file():
            add('database', '玩家存档 players.db', 'warn', '尚未创建；新建角色时会自动创建。')
        else:
            try:
                with closing(sqlite3.connect(self.db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as con:
                    verdict = con.execute('PRAGMA quick_check(1)').fetchone()[0]
                    players = con.execute('SELECT COUNT(*) FROM players').fetchone()[0]
                add('database', '玩家存档 players.db', 'ok' if verdict == 'ok' else 'error',
                    '完整性检查 %s · %s 个账号 · %.1f MB' % ('通过' if verdict == 'ok' else '失败：' + str(verdict),
                                                     players, self.db_path.stat().st_size / 1048576),
                    '' if verdict == 'ok' else '请在“存档备份”中恢复最近的备份。')
            except sqlite3.Error as exc:
                add('database', '玩家存档 players.db', 'error', '无法读取：%s' % exc, '确认没有其他程序锁住存档。')
        overview = self.services_overview()
        services = overview['units'][0]
        if services['running']:
            not_ready = [c['name'] for c in services['components'] if not c['ready']]
            add('services', '游戏服务组', 'warn' if not_ready else 'ok',
                ('未就绪：' + '、'.join(not_ready)) if not_ready else '4 项服务均已就绪（PID %s）' % services['pid'])
        else:
            add('services', '游戏服务组', 'info', '未运行。')
        for row in overview['foreign']:
            add('port-%d' % row['port'], '端口 %d' % row['port'], 'error', row['detail'],
                '关闭占用该端口的旧启动器或其他程序后再启动游戏。')
        if not overview['foreign'] and not services['running']:
            if overview['units'][2]['running']:
                add('ports', '端口 8080 / 8081 / 9001 / 843', 'ok',
                    '8081 由本启动器的独立 GM 后台使用，启动游戏时会自动换成服务组内置的 GM；其余端口空闲。')
            else:
                add('ports', '端口 8080 / 8081 / 9001 / 843', 'ok', '均未被占用，可以启动。')
        try:
            free = shutil.disk_usage(self.server_dir).free
            add('disk', '磁盘空间', 'ok' if free > 500 * 1048576 else 'warn', '剩余 %.1f GB' % (free / 1073741824),
                '' if free > 500 * 1048576 else '空间不足时备份和存档写入可能失败。')
        except OSError as exc:
            add('disk', '磁盘空间', 'warn', '无法读取：%s' % exc)
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            probe = self.backup_dir / ('.write-test-%d' % os.getpid())
            probe.write_bytes(b'')
            probe.unlink()
            latest = self.backups()[:1]
            add('backups', '存档备份', 'ok', '目录可写；' + ('最近一次：%s' % latest[0]['id'] if latest else '还没有备份'))
        except OSError as exc:
            add('backups', '存档备份', 'error', '备份目录不可写：%s' % exc)
        with self.log_lock:
            recent_errors = [row for row in list(self.logs)[-400:] if row['level'] == 'error']
        add('logs', '最近日志', 'warn' if recent_errors else 'ok',
            ('最近 400 条中有 %d 条错误，最新：%s' % (len(recent_errors), recent_errors[-1]['message'][:120]))
            if recent_errors else '最近 400 条中没有错误。', '在“服务与日志”中筛选“错误”查看详情。' if recent_errors else '')
        worst = 'error' if any(c['status'] == 'error' for c in checks) else 'warn' if any(c['status'] == 'warn' for c in checks) else 'ok'
        return {'status': worst, 'checks': checks, 'checkedAt': overview['checkedAt']}

    def admin_info(self):
        owned = self._alive(self.admin) or self._alive(self.server)
        return {'url': 'http://127.0.0.1:%d/?embedded=1' % legacy.ADMIN_PORT, 'ready': owned and legacy.admin_ready()}

    def start_admin(self):
        with self.lock:
            if self.closed:
                raise ValueError('启动器正在关闭。')
            if legacy.admin_ready():
                if not (self._alive(self.admin) or self._alive(self.server)):
                    raise ValueError('GM 后台端口已被其他启动器占用，请关闭后重试。')
                return self.admin_info()
            else:
                if self.running:
                    raise ValueError('游戏服务正在初始化，请稍后进入 GM 管理。')
                if not self._alive(self.admin):
                    if legacy.port_open(legacy.ADMIN_PORT):
                        raise ValueError('GM 后台端口已被其他程序占用。')
                    try:
                        self.admin = self._spawn('admin_server.py')
                    except (OSError, subprocess.SubprocessError) as exc:
                        self.log('!! GM 后台未能启动：%s' % exc)
                        raise ValueError('GM 后台未能启动：%s' % exc) from exc
                    self.started_at['admin'] = time.time()
                    threading.Thread(target=self._read_process, args=(self.admin, self.session, 'admin'), daemon=True).start()
                    self.log('正在开启内嵌 GM 后台…')
                process = self.admin
                session = self.session
        deadline = time.monotonic() + legacy.ADMIN_TIMEOUT
        while time.monotonic() < deadline:
            with self.lock:
                if self.closed or session != self.session:
                    raise ValueError('后台启动已取消。')
            if legacy.admin_ready():
                return self.admin_info()
            if not self._alive(process):
                break
            time.sleep(.15)
        with self.lock:
            if process is self.admin:
                if self._terminate(process, 'GM 后台'):
                    self.admin = None
                else:
                    self.log('!! GM 后台未就绪且无法停止，已保留进程以便重试停止。')
        raise ValueError('GM 后台未能就绪，请查看服务日志。')

    def _connection(self):
        con = sqlite3.connect(self.db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        return con

    def _account_summary(self, row):
        account = dict(row)
        account['id'] = str(account['uin'])
        account['uin'] = str(account['uin'])
        account['is_robot'] = 900000 <= int(account['uin']) < 1000000
        account['avatar_url'] = self.avatar_url(account)
        return account

    def avatar_url(self, account):
        custom = str(account.get('qq_face_url') or '')
        parsed = urlsplit(custom)
        if parsed.hostname in ('127.0.0.1', 'localhost') and parsed.path.startswith('/image/head/'):
            return '/avatar/' + parsed.path.removeprefix('/image/head/')
        face = int(account.get('face_id') or 0)
        return '/avatar/%d.png' % face

    def accounts(self, query='', include_robots=False):
        if not self.db_path.exists():
            return []
        con = self._connection()
        try:
            where = '' if include_robots else ' WHERE uin < 900000 OR uin >= 1000000'
            rows = con.execute('SELECT uin,name,signature,face_id,qq_face_url,level,coin,sun,vip_level,last_login FROM players' + where + ' ORDER BY last_login DESC,uin')
            result = [self._account_summary(row) for row in rows]
            if query:
                result = [row for row in result if query.casefold() in row['name'].casefold() or query in row['uin']]
            return result
        finally:
            con.close()

    def account(self, account_id):
        number = self._player_number(account_id)
        from account_management import get_account
        player = get_account(number)
        if not player:
            raise ValueError('这个账号不存在，请先新建账号。')
        from profile_editor import avatar_choices
        result = self._account_summary(player)
        result['profile_expected'] = {key: player.get(key) for key in PROFILE_KEYS}
        result['avatarChoices'] = [{'id': face, 'label': label, 'url': '/avatar/%s.png' % face}
                                   for face, label in avatar_choices(self.root)]
        return result

    def create_account(self, value):
        with self.lock:
            self._offline()
            number = self._player_number(value)
            import player_store as store
            store.init_db()
            store.create_new_player(number)
            self.active_account = str(value)
            self.config.update(last_uin=self.active_account, recent=([self.active_account] + [v for v in self.config.get('recent', []) if v != self.active_account])[:8])
            legacy.save_config(self.config)
            self.log('已创建新角色 · %s' % number)
            return self.account(number)

    def save_profile(self, account_id, data):
        with self.lock:
            self._offline()
            number = self._number(account_id)
            self.account(number)
            expected = data.get('profile_expected', {})
            if not isinstance(expected, dict):
                raise ValueError('资料快照无效，请重新读取角色。')
            profile = {key: data[key] for key in ('name', 'signature') if key in data and data[key] != expected.get(key)}
            if data.get('avatar', 'keep') != 'keep':
                profile['avatar'] = data['avatar']
            temporary = None
            try:
                if profile.get('avatar') == 'custom':
                    encoded = data.get('image_data', '')
                    if not isinstance(encoded, str) or not encoded.startswith('data:image/png;base64,') or len(encoded) > 7 * 1024 * 1024:
                        raise ValueError('请上传并裁剪有效的 PNG 头像。')
                    image = base64.b64decode(encoded.split(',', 1)[1], validate=True)
                    if not image.startswith(b'\x89PNG\r\n\x1a\n') or len(image) > 5 * 1024 * 1024:
                        raise ValueError('头像格式或大小不正确。')
                    with tempfile.NamedTemporaryFile(dir=self.root, prefix='.avatar-', suffix='.png', delete=False) as stream:
                        stream.write(image)
                        temporary = Path(stream.name)
                    profile['image_path'] = str(temporary)
                import gm
                gm.apply_editor_patch(number, {'fields': {}, 'expected': {}, 'profile': profile,
                                              'profile_expected': data.get('profile_expected', {})})
                self.log('已保存角色资料 · %s' % number)
                return self.account(number)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()

    def delete_account(self, account_id, confirmation):
        with self.lock:
            self._offline()
            number = self._number(account_id)
            if self._number(confirmation) != number:
                raise ValueError('确认账号编号不匹配，未删除任何数据。')
            self.account(number)
            backup = self.create_backup('before-delete-%s' % number)
            from account_management import delete_account
            result = delete_account(number)
            self.config['recent'] = [v for v in self.config.get('recent', []) if self._number(v) != number]
            if self.active_account and self._number(self.active_account) == number:
                self.active_account = self.config['recent'][0] if self.config['recent'] else ''
                self.config['last_uin'] = self.active_account
            legacy.save_config(self.config)
            self.log('已删除角色 %s，已保留操作前备份。' % number)
            return {'deleted': str(number), 'backup': backup['id']}

    def create_backup(self, reason='manual'):
        with self.lock:
            self._offline()
            if not self.db_path.is_file():
                raise ValueError('还没有存档数据库。')
            stamp = dt.datetime.now(DISPLAY_TIMEZONE).strftime('%Y%m%d-%H%M%S-%f')
            folder = self.backup_dir / stamp
            folder.mkdir(parents=True)
            files = []
            try:
                for name in ('players.db', 'guild_local.db'):
                    source = self.server_dir / name
                    if not source.exists():
                        continue
                    target = folder / name
                    if source.stat().st_size:
                        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as con, closing(sqlite3.connect(target)) as out:
                            con.backup(out)
                    else:
                        target.write_bytes(b'')
                    files.append({'name': name, 'size': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
                manifest = {'id': stamp, 'name': '存档快照', 'created': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                            'reason': str(reason)[:80], 'files': files, 'size': sum(f['size'] for f in files)}
                (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            except BaseException:
                shutil.rmtree(folder)
                raise
            self.log('已备份玩家与公会存档 · %s' % stamp)
            return manifest

    def backups(self):
        rows = []
        if self.backup_dir.exists():
            for path in sorted(self.backup_dir.glob('*/manifest.json'), reverse=True):
                try:
                    row = json.loads(path.read_text(encoding='utf-8'))
                    if row.get('id') == path.parent.name:
                        rows.append(row)
                except (OSError, ValueError, AttributeError):
                    pass
        return rows

    def restore_backup(self, backup_id, confirmed):
        with self.lock:
            self._offline()
            if confirmed is not True:
                raise ValueError('请确认恢复备份。')
            if legacy.port_open(legacy.ADMIN_PORT) and not self._alive(self.admin):
                raise ValueError('另一个 GM 后台正在运行，请先关闭后恢复备份。')
            if not isinstance(backup_id, str) or not backup_id.replace('-', '').isdigit():
                raise ValueError('备份编号无效。')
            folder = (self.backup_dir / backup_id).resolve()
            if not folder.is_relative_to(self.backup_dir.resolve()) or not (folder / 'manifest.json').is_file():
                raise ValueError('备份不存在。')
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            files = manifest.get('files', [])
            if not files or 'players.db' not in [row.get('name') for row in files]:
                raise ValueError('备份缺少玩家存档。')
            for entry in files:
                if entry.get('name') not in ('players.db', 'guild_local.db'):
                    raise ValueError('备份文件类型无效。')
                source = folder / entry['name']
                if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != entry.get('sha256'):
                    raise ValueError('备份校验失败，未覆盖当前存档。')
                if source.stat().st_size:
                    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as con:
                        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                            raise ValueError('备份数据库损坏。')
            safeguard = self.create_backup('before-restore')
            if self.admin and not self._terminate(self.admin, 'GM 后台'):
                raise ValueError('GM 后台未能停止，恢复已取消。')
            self.admin = None
            import player_store as store
            store.close_thread_db()
            def restore_file(source, target):
                # SQLite backup overwrites live DB pages without orphaning WAL files.
                if source.stat().st_size:
                    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as con, closing(sqlite3.connect(target)) as out:
                        con.backup(out)
                elif target.exists() and target.stat().st_size:
                    with closing(sqlite3.connect(target)) as out, out:
                        for (name,) in out.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                            out.execute('DROP TABLE "' + name.replace('"', '""') + '"')
                else:
                    target.touch()
            try:
                for entry in files:
                    restore_file(folder / entry['name'], self.server_dir / entry['name'])
            except BaseException:
                rollback = self.backup_dir / safeguard['id']
                for entry in safeguard['files']:
                    restore_file(rollback / entry['name'], self.server_dir / entry['name'])
                raise
            self.log('已恢复存档快照 · %s' % backup_id)
            return {'restored': backup_id, 'backup': safeguard['id']}

    def _write_settings(self, value):
        temporary = self.settings_path.with_suffix('.json.tmp')
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, self.settings_path)
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass
        self.settings = value

    @staticmethod
    def _missing_player(player_kind):
        if player_kind == 'ruffle':
            return '未找到 Ruffle，请检查游戏目录中的 ruffle.exe 或配置的 Ruffle 路径。'
        return '未找到 Flash，请检查游戏目录中的 flashplayer_sa.exe。'

    def _player_options(self):
        result = []
        for kind, name in (('flash', 'Flash'), ('ruffle', 'Ruffle')):
            _, path = legacy.resolve_game_player(kind, root=self.root)
            result.append({'id': kind, 'name': name, 'available': bool(path),
                           'detail': ('使用 Ruffle 播放游戏。' if kind == 'ruffle'
                                      else '使用经典 Flash 播放器。') if path else self._missing_player(kind)})
        return result

    def set_player_kind(self, player_kind):
        with self.lock:
            if player_kind not in ('flash', 'ruffle'):
                raise ValueError('播放器选择无效，请选择 Flash 或 Ruffle。')
            if self.closed:
                raise ValueError('启动器正在关闭。')
            if self.running or self.busy or any(self._alive(p) for p in (self.server, self.game)):
                raise ValueError('请先停止游戏，再切换播放器。')
            kind, path = legacy.resolve_game_player(player_kind, root=self.root)
            if not path:
                raise ValueError(self._missing_player(player_kind))
            self._write_settings({**self.settings, 'player': kind})
            self.player_kind, self.player_path = kind, path
            self.log('游戏播放器已切换为 %s。' % ('Flash' if kind == 'flash' else 'Ruffle'))
            return {'playerKind': kind, 'player': 'Flash' if kind == 'flash' else 'Ruffle',
                    'playerOptions': self._player_options()}

    def save_settings(self, data):
        with self.lock:
            theme = data.get('theme', self.settings['theme'])
            scale = data.get('scale', self.settings['scale'])
            if theme not in ('light', 'dark', 'auto') or type(scale) is not int or not 80 <= scale <= 150:
                raise ValueError('外观设置无效。')
            value = {**self.settings, 'theme': theme, 'scale': scale}
            self._write_settings(value)
            return value

    def state(self):
        with self.lock:
            ports = [('web', '游戏资源', legacy.WEB_PORT), ('admin', 'GM 后台', legacy.ADMIN_PORT),
                     ('game', '游戏服务', legacy.GAME_PORT), ('policy', '连接策略', legacy.POLICY_PORT)]
            services = []
            observed = self._service_ports()
            alive = self._unit_alive()
            for name, title, port in ports:
                active = observed[name]
                status = 'running' if active else 'starting' if self.busy else 'stopped'
                owned = alive['services'] or (name == 'admin' and alive['admin'])
                services.append({'id': name, 'name': title, 'port': port, 'status': status, 'owned': bool(active and owned),
                                 'detail': ('已连接' if owned else '被其他程序占用') if active else '初始化中' if self.busy else '未启动'})
            try:
                current = self.account(self.active_account) if self.active_account else None
            except ValueError:
                current = None
            return {'running': self.running, 'busy': self.busy, 'statusMessage': self.status,
                    'progress': self.progress, 'error': self.error, 'activeAccountId': self.active_account,
                    'currentAccount': current, 'services': services, 'logCount': self.log_id,
                    'version': '2.0', 'player': 'Flash' if self.player_kind == 'flash' else 'Ruffle',
                    'playerKind': self.player_kind, 'playerOptions': self._player_options(),
                    'controllerPid': os.getpid(),
                    # Additive fields for the redesigned UI (older shells ignore them).
                    'units': alive, 'playerRunning': alive['player'], 'adminRunning': alive['admin'],
                    'canReopenPlayer': bool(self.launch_player and alive['services'] and self.running
                                            and not self.busy and not alive['player']),
                    'recentAccountIds': [str(v) for v in self.config.get('recent', [])][:8],
                    'startedAt': self.started_at.get('services') if alive['services'] else None,
                    'serverTime': time.time(), 'gamePath': str(self.root)}

    def close(self):
        with self.lock:
            self.closed = True
            try:
                self.stop()
            except ValueError:
                pass
