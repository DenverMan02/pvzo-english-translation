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
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

import launcher as legacy
from launcher_ports import DEFAULT_PORTS, PORT_TITLES, validate_ports, port_environment, ensure_port_available
from launcher_runtime_status import RuntimeMonitor

PROFILE_KEYS = ('name', 'signature', 'face_id', 'qq_face_url')
DISPLAY_TIMEZONE = dt.timezone(dt.timedelta(hours=8))


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
        self.runtime_monitor = RuntimeMonitor()
        self.started_monotonic = None
        self.startup_started = self.startup_ended = None
        self.startup_started_at = None
        self.startup_phase = 'idle'
        self.startup_action = ''
        self.startup_checks = {key: False for key in DEFAULT_PORTS}
        self.startup_failure = None
        self.server_output = deque(maxlen=40)
        self.server_output_done = threading.Event()
        self.player_kind, self.player_path = legacy.resolve_game_player()
        if str(self.server_dir) not in sys.path:
            sys.path.insert(0, str(self.server_dir))
        self.guard_path = self.root / 'launcher_cache' / ('lifecycle-%s.json' % os.getpid())
        self.window_info_path = self.guard_path.with_suffix('.window.json')
        self.ui_pid = None
        self._publish_guard()
        self.log('启动器已就绪，选择角色后开始游戏。')

    def _publish_guard(self):
        from launcher_guard import write_guard
        write_guard(self.guard_path, busy=self.busy, running=self.running)

    def _load_settings(self):
        result = {'theme': 'light', 'scale': 100, 'ports': dict(DEFAULT_PORTS)}
        try:
            value = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                if value.get('theme') in ('light', 'dark', 'auto'):
                    result['theme'] = value['theme']
                if type(value.get('scale')) is int and 80 <= value['scale'] <= 150:
                    result['scale'] = value['scale']
                try:
                    result['ports'] = validate_ports(value.get('ports', {}))
                except ValueError:
                    pass
        except (OSError, ValueError):
            pass
        return result

    def log(self, message, level=None):
        message = str(message).rstrip()
        if not message:
            return
        if level is None:
            text = message.lower()
            level = 'error' if message.startswith('!!') or any(v in text for v in ('traceback', 'exception', 'crashed')) else 'info'
        with self.log_lock:
            self.log_id += 1
            stamp = dt.datetime.now(DISPLAY_TIMEZONE)
            self.logs.append({'id': self.log_id, 'time': stamp.strftime('%H:%M:%S'), 'level': level, 'message': message})
            path = self.root / 'portable_launcher.log'
            try:
                if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                    os.replace(path, str(path) + '.1')
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(stamp.strftime('%Y-%m-%d %H:%M:%S ') + message + '\n')
            except OSError:
                pass

    def get_logs(self, after=0, level='', query=''):
        with self.log_lock:
            entries = [dict(row) for row in self.logs if row['id'] > after
                       and (not level or row['level'] == level)
                       and (not query or query.casefold() in row['message'].casefold())]
            return {'entries': entries, 'lastId': self.log_id}

    def _number(self, value):
        from account_management import _account_number
        return _account_number(value)

    def _player_number(self, value):
        number = self._number(value)
        if not legacy.valid_player_uin(str(number)):
            raise ValueError('这个编号属于系统人机，请选择玩家账号。')
        return number

    def _offline(self):
        if self.running or self.busy or legacy.port_open(self.ports['game']):
            raise ValueError('请先停止游戏服务，再修改角色、存档或恢复备份。')

    def _alive(self, process):
        return process is not None and process.poll() is None

    def _server_session_current(self, process, session):
        return not self.closed and session == self.session and process is self.server

    def _current(self, process, session):
        return self._server_session_current(process, session) and self._alive(process)

    def _command(self, script, *args):
        # The existing portable interpreter executes the current external source.
        return legacy.python_command(str(self.server_dir / script), *args)

    def _environment(self):
        return {**os.environ, **port_environment(self.ports), 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1',
                'PVZ_LAUNCHER_ORIGIN': self.origin,
                'PVZ_LAUNCHER_GUARD': str(self.guard_path),
                'PVZ_LAUNCHER_OWNER_PID': str(os.getpid())}

    def _spawn(self, script):
        return subprocess.Popen(self._command(script), cwd=self.server_dir,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', bufsize=1,
                                env=self._environment(), **legacy.HIDDEN_PROCESS)

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

    def _finish_server_exit(self, process, session):
        """Report an owned service exit only after its output reader can drain."""
        if process.poll() is None:
            return False
        with self.lock:
            if not self._server_session_current(process, session):
                return True
            output = self.server_output
            output_done = self.server_output_done
        # poll() can observe exit before the reader reaches the traceback's last
        # line. Do not hold the lifecycle lock while giving that reader time.
        output_done.wait(.5)
        with self.lock:
            if not self._server_session_current(process, session):
                return True
            code = process.poll()
            if code is None:
                return False
            lines = [line for line in output if line]
            detail = next((line for line in reversed(lines)
                           if any(word in line.casefold() for word in
                                  ('error:', 'exception:', '[launcher-error]', 'could not bind'))),
                          lines[-1] if lines else '')
            reason = '游戏服务已退出（代码 %s）' % code
            reason += '：' + detail[:500] if detail else '，未能保持运行。'
            action = '查看服务日志中的具体错误，检查游戏资源、配置与运行权限后重新启动。'
            if any(word in detail.casefold() for word in ('bind', 'address in use', '端口')):
                action = '检查端口占用，在首页选择空闲端口或关闭占用程序后重试。'
            self.log('!! 游戏服务已退出，正在回收本次游戏进程。')
            try:
                self.stop()
            except ValueError:
                reason += '；部分服务未停止，请重试停止。'
            self._startup_error(reason, action)
            self.log('!! ' + self.error)
            return True

    def _watch_server(self, process, session, cancel):
        # Continue watching after readiness and even if stdout was closed early.
        while not cancel.is_set():
            with self.lock:
                if not self._server_session_current(process, session):
                    return
            if self._finish_server_exit(process, session):
                return
            cancel.wait(.1)

    def _read_process(self, process, session, kind):
        with self.lock:
            output_done = self.server_output_done if kind == 'server' and self._server_session_current(process, session) else None
        try:
            if process.stdout:
                for line in process.stdout:
                    with self.lock:
                        current = (self._server_session_current(process, session) if kind == 'server'
                                   else not self.closed and session == self.session and process is self.admin)
                        if not current:
                            continue
                        self.log(line)
                        text = line.casefold()
                        if kind == 'server':
                            self.server_output.append(line.strip()[:1000])
                            if any(word in text for word in ('could not bind', '[launcher-error]', '[web] crashed', '[admin] crashed')):
                                self.startup_failure = ('本地服务启动失败：' + line.strip()[:500],
                                                        '检查端口占用与服务日志；修改空闲端口或处理资源、权限错误后重试。')
        except (OSError, ValueError):
            pass
        finally:
            if output_done is not None:
                output_done.set()
            if kind == 'server':
                self._finish_server_exit(process, session)
            else:
                with self.lock:
                    if process is self.admin and session == self.session and not self._alive(process):
                        self.admin = None
                        self.log('GM 后台已停止。')

    @property
    def ports(self):
        return getattr(self, 'settings', {}).get('ports', DEFAULT_PORTS)

    def _startup_error(self, reason, action='查看服务日志，处理错误后重新启动。'):
        self.error = str(reason)
        self.status = self.error
        self.startup_phase = 'error'
        self.startup_action = action
        self.startup_ended = time.monotonic()

    def start(self, account_id):
        with self.lock:
            try:
                return self._start(account_id)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                action = '检查启动文件与所选账号后重试。'
                if '端口' in str(exc):
                    action = '在首页端口设置中选择空闲端口，或关闭占用端口的程序后重试。策略端口 843 固定。'
                elif '停止' in str(exc) or '已经启动' in str(exc):
                    action = '先点击停止服务，待游戏与 GM 后台完全停止后重试。'
                self._startup_error(str(exc), action)
                raise ValueError(str(exc)) from exc

    def _start(self, account_id):
        with self.lock:
            if self.closed:
                raise ValueError('启动器正在关闭。')
            if self.running or self.busy:
                raise ValueError('游戏已经启动或正在初始化。')
            number = self._player_number(account_id)
            if any(self._alive(p) for p in (self.server, self.game)):
                raise ValueError('上次进程尚未停止，请先点击停止。')
            loader = 'loader_n.swf' if self.player_kind == 'ruffle' else 'loader_p.swf'
            if not (self.server_dir / 'run_all.py').is_file() or not (self.root / 'webroot' / loader).is_file():
                raise ValueError('游戏服务或 loader 文件缺失，请检查完整游戏目录。')
            if self.launch_player and not self.player_path:
                raise ValueError('未找到游戏播放器，请检查 flashplayer_sa.exe。')
            self.startup_started = time.monotonic()
            self.startup_ended = None
            self.startup_started_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
            self.startup_phase = 'services'
            self.startup_action = ''
            self.startup_checks = {key: False for key in DEFAULT_PORTS}
            self.startup_failure = None
            self.server_output = deque(maxlen=40)
            self.server_output_done = threading.Event()
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
                busy_ports = [p for p in self.ports.values() if legacy.port_open(p, timeout=.15)]
                if busy_ports:
                    snapshot = self.port_status()
                    conflicts = []
                    for row in snapshot['services']:
                        if row['port'] in busy_ports:
                            owner = '（%s，PID %s）' % (row.get('process_name') or '其他程序', row['pid']) if row.get('pid') else ''
                            conflicts.append('%s %s%s' % (row['name'], row['port'], owner))
                    raise ValueError('端口已被占用：%s。请关闭占用程序或修改端口后重试。' % '、'.join(conflicts))
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
            self.started_monotonic = time.monotonic()
            self.running = self.busy = True
            try:
                self._publish_guard()
            except OSError as exc:
                self.log('!! 运行状态未更新，保持启动写保护：%s' % exc)
            self.log('开始初始化游戏 · 账号 %s' % number)
            threading.Thread(target=self._read_process, args=(process, self.session, 'server'), daemon=True).start()
            threading.Thread(target=self._watch_server, args=(process, self.session, self.cancel), daemon=True).start()
            threading.Thread(target=self._wait_ready, args=(process, self.session, loader, self.cancel), daemon=True).start()
            return {'status': self.status}

    def _wait_ready(self, process, session, loader, cancel):
        deadline = time.monotonic() + legacy.START_TIMEOUT
        while not cancel.is_set() and time.monotonic() < deadline:
            if self._finish_server_exit(process, session):
                return
            checks = [legacy.web_ready(loader, self.ports['web']), legacy.admin_ready(self.ports['admin']),
                      legacy.port_open(self.ports['game']), legacy.policy_ready(self.ports['policy'], self.ports['game'])]
            with self.lock:
                if not self._server_session_current(process, session):
                    return
                if not self._alive(process):
                    continue
                self.startup_checks = dict(zip(('web', 'admin', 'game', 'policy'), checks))
                if self.startup_failure:
                    reason, action = self.startup_failure
                    try:
                        self.stop()
                    except ValueError:
                        reason += '；部分服务未停止，请重试停止。'
                    self._startup_error(reason, action)
                    return
                self.progress = 12 + 18 * sum(checks)
                if all(checks):
                    self.busy = False
                    try:
                        self._publish_guard()
                    except OSError as exc:
                        self.log('!! 服务状态未更新，保持写保护：%s' % exc)
                    self.progress = 100
                    self.status = '游戏服务运行中'
                    self.startup_phase = 'player' if self.launch_player else 'running'
                    if not self.launch_player:
                        self.startup_ended = time.monotonic()
                    self.log('四项服务已就绪。')
                    if self.launch_player:
                        url = legacy.game_url(self.active_account, loader, web_port=self.ports['web'], game_port=self.ports['game'])
                        try:
                            from launcher_game_window import game_startup_options, read_swf_stage
                            window_options = game_startup_options(self.player_kind)
                            stage_size = read_swf_stage(self.root / 'webroot' / loader) if window_options else None
                            self.game = subprocess.Popen(legacy.game_player_command(self.player_kind, self.player_path, url, game_port=self.ports['game']),
                                                         cwd=self.root, env=legacy.game_player_environment(self.player_kind),
                                                         **legacy.HIDDEN_PROCESS, **window_options)
                            self.status = '游戏已打开 · 祝你玩得开心'
                            self.startup_phase = 'running'
                            self.startup_ended = time.monotonic()
                            game = self.game
                            threading.Thread(target=self._watch_game, args=(game, process, session, cancel), daemon=True).start()
                            threading.Thread(target=self._place_game_window, args=(game, process, session, cancel),
                                             kwargs={'initially_hidden': bool(window_options), 'stage_size': stage_size}, daemon=True).start()
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
                            self._startup_error(reason, '检查游戏播放器文件与服务日志后重试。')
                            self.log('!! ' + self.error)
                    return
            cancel.wait(.3)
        if self._finish_server_exit(process, session):
            return
        with self.lock:
            if self._current(process, session) and not cancel.is_set():
                missing = [('%s（端口 %s）' % (PORT_TITLES[key], self.ports[key])) for key, ready in self.startup_checks.items() if not ready]
                try:
                    self.stop()
                except ValueError:
                    pass
                self._startup_error('启动超时：%s未就绪。已尝试清理本次进程。' % '、'.join(missing),
                                    '检查服务日志中的端口绑定、资源缺失或权限错误，然后重试。')
                self.log('!! ' + self.error)

    def _game_current(self, game, server, session, cancel):
        return (not self.closed and not cancel.is_set() and session == self.session and
                game is self.game and server is self.server)

    def _watch_game(self, game, server, session, cancel):
        while not cancel.is_set():
            with self.lock:
                if not self._game_current(game, server, session, cancel):
                    return
                code = game.poll()
                if code is not None:
                    self.log('游戏窗口已关闭，正在停止本次游戏服务。')
                    try:
                        self.stop()
                    except ValueError:
                        return
                    self.status = '游戏已关闭，服务已停止'
                    if code:
                        self._startup_error('游戏播放器已退出（代码 %s），本次服务已停止。' % code,
                                            '检查播放器与服务日志；确认游戏资源完整后重新启动。')
                        self.log('!! ' + self.error)
                    return
            cancel.wait(.2)

    def _place_game_window(self, game, server, session, cancel, *, initially_hidden=False, stage_size=None):
        def is_current():
            with self.lock:
                return self._game_current(game, server, session, cancel) and self._alive(game)
        failure = '未找到可以居中显示的游戏窗口'
        try:
            from launcher_game_window import place_game_window
            if place_game_window(game.pid, owner_file=self.window_info_path, owner_pid=self.ui_pid,
                                 is_current=is_current, cancel=cancel, initially_hidden=initially_hidden,
                                 stage_size=stage_size):
                self.log('游戏窗口已居中显示。')
                return
        except (AttributeError, OSError, ValueError) as exc:
            failure = '游戏窗口未能居中显示：%s' % exc
            self.log('游戏窗口位置未能调整：%s' % exc, 'warning')
        if initially_hidden:
            # A failed reveal must not leave an invisible player holding ports.
            # Check the exact session under the same lock used by stop/start.
            with self.lock:
                if not is_current():
                    return
                try:
                    self.stop()
                except ValueError:
                    failure += '；部分服务未能停止，请重试停止。'
                self._startup_error(failure, '检查播放器窗口与启动器权限后重试；服务日志中保留了窗口错误。')
                self.status = '游戏窗口未能显示'
                self.log('!! ' + failure)

    def stop(self):
        with self.lock:
            self.session += 1
            self.cancel.set()
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
                else:
                    ok = False
            self.running = any(self._alive(p) for p in (self.server, self.game))
            self.busy = False
            self.progress = 0
            self.status = '服务已停止' if ok else '部分服务未停止，请重试'
            self.error = '' if ok else self.status
            self.startup_phase = 'stopped' if ok else 'error'
            self.startup_action = '' if ok else '再次点击停止，检查服务日志中的进程错误。'
            self.startup_ended = time.monotonic()
            if ok:
                self.started_monotonic = None
                self.startup_checks = {key: False for key in DEFAULT_PORTS}
            try:
                self._publish_guard()
            except OSError as exc:
                self.log('!! 写保护状态未能更新：%s' % exc)
            self.log(self.status, 'info' if ok else 'error')
            if not ok:
                raise ValueError(self.status)
            return {'status': self.status}

    def admin_info(self):
        owned = self._alive(self.admin) or self._alive(self.server)
        return {'url': 'http://127.0.0.1:%d/?embedded=1' % self.ports['admin'], 'ready': owned and legacy.admin_ready(self.ports['admin'])}

    def start_admin(self):
        with self.lock:
            if self.closed:
                raise ValueError('启动器正在关闭。')
            if legacy.admin_ready(self.ports['admin']):
                if not (self._alive(self.admin) or self._alive(self.server)):
                    raise ValueError('GM 后台端口已被其他启动器占用，请关闭后重试。')
                return self.admin_info()
            else:
                if self.running:
                    raise ValueError('游戏服务正在初始化，请稍后进入 GM 管理。')
                if not self._alive(self.admin):
                    if legacy.port_open(self.ports['admin']):
                        raise ValueError('GM 后台端口已被其他程序占用。')
                    try:
                        self.admin = self._spawn('admin_server.py')
                    except (OSError, subprocess.SubprocessError) as exc:
                        self.log('!! GM 后台未能启动：%s' % exc)
                        raise ValueError('GM 后台未能启动：%s' % exc) from exc
                    threading.Thread(target=self._read_process, args=(self.admin, self.session, 'admin'), daemon=True).start()
                    self.log('正在开启内嵌 GM 后台…')
                process = self.admin
                session = self.session
        deadline = time.monotonic() + legacy.ADMIN_TIMEOUT
        while time.monotonic() < deadline:
            with self.lock:
                if self.closed or session != self.session:
                    raise ValueError('后台启动已取消。')
            if legacy.admin_ready(self.ports['admin']):
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
        from launcher_avatar import avatar_relative
        return '/avatar/' + avatar_relative(account, self.root)

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
            if legacy.port_open(self.ports['admin']) and not self._alive(self.admin):
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

    def save_settings(self, data):
        with self.lock:
            theme = data.get('theme', self.settings['theme'])
            scale = data.get('scale', self.settings['scale'])
            if theme not in ('light', 'dark', 'auto') or type(scale) is not int or not 80 <= scale <= 150:
                raise ValueError('外观设置无效。')
            ports = validate_ports(data.get('ports', {}), self.ports)
            if ports != self.ports:
                if (self.running or self.busy or any(self._alive(process) for process in (self.server, self.game, self.admin))
                        or legacy.port_open(self.ports['game'], timeout=.08)):
                    raise ValueError('请先停止游戏与 GM 后台，再修改端口。首页的停止服务会同时停止本启动器开启的 GM 后台。')
                if self.origin and int(urlsplit(self.origin).port or 0) in ports.values():
                    raise ValueError('此端口正在用于启动器界面，请选择其他端口。')
                for key, port in ports.items():
                    if port != self.ports[key]:
                        ensure_port_available(key, port)
            value = {'theme': theme, 'scale': scale, 'ports': ports}
            temporary = self.settings_path.with_suffix('.json.tmp')
            try:
                temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporary, self.settings_path)
            except OSError as exc:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ValueError('设置未能保存，请检查启动器目录的写入权限。') from exc
            self.settings = value
            return value

    def _runtime_snapshot(self):
        roles = {os.getpid(): ('controller', '启动器服务')}
        active_roles = set()
        ui_pid = getattr(self, 'ui_pid', None)
        if isinstance(ui_pid, int) and ui_pid > 0:
            roles[ui_pid] = ('ui', '启动器界面')
        for key, title in (('server', '本地游戏服务'), ('admin', 'GM 后台'), ('game', '游戏播放器')):
            process = getattr(self, key, None)
            pid = getattr(process, 'pid', None)
            if self._alive(process):
                active_roles.add(key)
                if type(pid) is int and pid > 0:
                    roles[pid] = (key, title)
        monitor = getattr(self, 'runtime_monitor', None)
        if monitor is None:
            self.runtime_monitor = monitor = RuntimeMonitor()
        bindings = {port: ('0.0.0.0' if key in ('game', 'policy') else '127.0.0.1')
                    for key, port in self.ports.items()}
        runtime = monitor.snapshot(bindings, list(roles))
        for process in runtime['processes']:
            role, title = roles.get(process['pid'], ('foreign', process.get('name') or '其他程序'))
            process['role'], process['title'] = role, title
        def memory(role_set):
            expected = active_roles & role_set if role_set <= {'server', 'admin', 'game'} else role_set
            if not expected:
                return 0
            values = [row.get('memory_bytes') for row in runtime['processes'] if row.get('role') in expected]
            return sum(value for value in values if isinstance(value, int)) if any(isinstance(value, int) for value in values) else None
        tracked = [row for row in runtime['processes'] if row.get('role') in {'server', 'admin', 'game'}]
        sampled_roles = {row['role'] for row in tracked if isinstance(row.get('memory_bytes'), int)}
        runtime['memory_complete'] = active_roles <= sampled_roles
        runtime['game_memory_bytes'] = memory({'game'})
        runtime['service_memory_bytes'] = memory({'server', 'admin'})
        runtime['launcher_memory_bytes'] = memory({'controller', 'ui'})
        known_memory = [row.get('memory_bytes') for row in tracked if isinstance(row.get('memory_bytes'), int)]
        runtime['memory_bytes'] = sum(known_memory) if known_memory else (0 if not active_roles else None)
        if runtime.get('stale'):
            runtime['memory_complete'] = False
            for field in ('memory_bytes', 'game_memory_bytes', 'service_memory_bytes', 'launcher_memory_bytes'):
                runtime[field] = None
        started = getattr(self, 'started_monotonic', None)
        runtime['uptime_seconds'] = max(0, time.monotonic() - started) if started is not None and self.running else 0
        return runtime

    def port_status(self):
        with self.lock:
            runtime = self._runtime_snapshot()
            services = []
            checks = getattr(self, 'startup_checks', {})
            service_pids = {getattr(process, 'pid', None) for process in (self.server, self.admin) if self._alive(process)}
            for key, port in self.ports.items():
                row = runtime['ports'].get(port, {})
                occupied = row.get('occupied')
                listener_pids = row.get('pids') or ([row['pid']] if row.get('pid') else [])
                owned = bool(listener_pids) and all(pid in service_pids for pid in listener_pids)
                stale = bool(runtime.get('stale'))
                detail = row.get('detail') or '正在检测'
                if stale:
                    status = 'error'
                    detail = '端口检测失败：%s。请刷新重试。' % (runtime.get('detail') or '无法获取当前占用状态')
                elif occupied and not owned:
                    status = 'conflict'
                elif occupied and owned:
                    status = 'running' if checks.get(key) or not self.busy else 'starting'
                elif occupied is None:
                    status = 'error' if row.get('status') == 'unknown' else 'checking'
                else:
                    status = 'starting' if self.busy else 'stopped'
                services.append({'id': key, 'name': PORT_TITLES[key], 'port': port, 'status': status,
                                 'occupied': occupied, 'owned': owned, 'pid': row.get('pid'),
                                 'process_name': row.get('name'), 'detail': detail, 'stale': stale,
                                 'editable': key != 'policy', 'ready': bool(checks.get(key)) and not stale})
            return {'ports': dict(self.ports), 'services': services, 'runtime': runtime}

    def _startup_state(self):
        started = getattr(self, 'startup_started', None)
        end = getattr(self, 'startup_ended', None)
        elapsed = max(0, (end or time.monotonic()) - started) if started is not None else 0
        return {'phase': getattr(self, 'startup_phase', 'idle'),
                'started_at': getattr(self, 'startup_started_at', None), 'elapsed_seconds': elapsed,
                'checks': [{'id': key, 'name': PORT_TITLES[key], 'port': self.ports[key], 'ready': bool(ready)}
                           for key, ready in getattr(self, 'startup_checks', {key: False for key in DEFAULT_PORTS}).items()],
                'error': self.error, 'action': getattr(self, 'startup_action', '')}

    def state(self):
        with self.lock:
            snapshot = self.port_status()
            try:
                current = self.account(self.active_account) if self.active_account else None
            except ValueError:
                current = None
            return {'running': self.running, 'busy': self.busy, 'statusMessage': self.status,
                    'progress': self.progress, 'error': self.error, 'activeAccountId': self.active_account,
                    'currentAccount': current, 'services': snapshot['services'], 'ports': dict(self.ports),
                    'runtime': snapshot['runtime'], 'startup': self._startup_state(),
                    'logCount': self.log_id, 'version': '2.0',
                    'player': 'Flash' if self.player_kind == 'flash' else 'Ruffle',
                    'controllerPid': os.getpid(), 'gamePath': str(self.root)}

    def close(self):
        with self.lock:
            self.closed = True
            try:
                self.stop()
            except ValueError:
                pass
