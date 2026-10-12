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
        self.player_kind, self.player_path = legacy.resolve_game_player()
        if str(self.server_dir) not in sys.path:
            sys.path.insert(0, str(self.server_dir))
        self.guard_path = self.root / 'launcher_cache' / ('lifecycle-%s.json' % os.getpid())
        self._publish_guard()
        self.log('启动器已就绪，选择角色后开始游戏。')

    def _publish_guard(self):
        from launcher_guard import write_guard
        write_guard(self.guard_path, busy=self.busy, running=self.running)

    def _load_settings(self):
        result = {'theme': 'light', 'scale': 100}
        try:
            value = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                if value.get('theme') in ('light', 'dark', 'auto'):
                    result['theme'] = value['theme']
                if type(value.get('scale')) is int and 80 <= value['scale'] <= 150:
                    result['scale'] = value['scale']
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

    def _read_process(self, process, session, kind):
        try:
            if process.stdout:
                for line in process.stdout:
                    if session == self.session:
                        self.log(line)
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
            number = self._number(account_id)
            if any(self._alive(p) for p in (self.server, self.game)):
                raise ValueError('上次进程尚未停止，请先点击停止。')
            loader = 'loader_n.swf' if self.player_kind == 'ruffle' else 'loader_p.swf'
            if not (self.server_dir / 'run_all.py').is_file() or not (self.root / 'webroot' / loader).is_file():
                raise ValueError('游戏服务或 loader 文件缺失，请检查完整游戏目录。')
            if self.launch_player and not self.player_path:
                raise ValueError('未找到游戏播放器，请检查 flashplayer_sa.exe。')
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
        while not cancel.is_set() and time.monotonic() < deadline:
            checks = [legacy.web_ready(loader), legacy.admin_ready(), legacy.port_open(legacy.GAME_PORT), legacy.policy_ready()]
            with self.lock:
                if not self._current(process, session):
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
                    self.log('四项服务已就绪。')
                    if self.launch_player:
                        url = legacy.game_url(self.active_account, loader)
                        try:
                            self.game = subprocess.Popen(legacy.game_player_command(self.player_kind, self.player_path, url),
                                                         cwd=self.root, env=legacy.game_player_environment(self.player_kind), **legacy.HIDDEN_PROCESS)
                            self.status = '游戏已打开 · 祝你玩得开心'
                        except Exception as exc:
                            self.error = '播放器未能打开：%s' % exc
                            self.status = '服务就绪，播放器启动失败'
                            self.log('!! ' + self.error)
                    return
            cancel.wait(.3)
        with self.lock:
            if self._current(process, session) and not cancel.is_set():
                self.stop()
                self.error = '启动超时，已清理本次进程。请查看日志后重试。'
                self.status = self.error
                self.log('!! ' + self.error)

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
        account['avatar_url'] = self.avatar_url(account)
        return account

    def avatar_url(self, account):
        custom = str(account.get('qq_face_url') or '')
        parsed = urlsplit(custom)
        if parsed.hostname in ('127.0.0.1', 'localhost') and parsed.path.startswith('/image/head/'):
            return '/avatar/' + parsed.path.removeprefix('/image/head/')
        face = int(account.get('face_id') or 0)
        return '/avatar/%d.png' % face

    def accounts(self, query=''):
        if not self.db_path.exists():
            return []
        con = self._connection()
        try:
            rows = con.execute('SELECT uin,name,signature,face_id,qq_face_url,level,coin,sun,vip_level,last_login FROM players ORDER BY last_login DESC,uin')
            result = [self._account_summary(row) for row in rows]
            if query:
                result = [row for row in result if query.casefold() in row['name'].casefold() or query in row['uin']]
            return result
        finally:
            con.close()

    def account(self, account_id):
        number = self._number(account_id)
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
            number = self._number(value)
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

    def save_settings(self, data):
        with self.lock:
            theme = data.get('theme', self.settings['theme'])
            scale = data.get('scale', self.settings['scale'])
            if theme not in ('light', 'dark', 'auto') or type(scale) is not int or not 80 <= scale <= 150:
                raise ValueError('外观设置无效。')
            value = {'theme': theme, 'scale': scale}
            temporary = self.settings_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, self.settings_path)
            self.settings = value
            return value

    def state(self):
        with self.lock:
            ports = [('web', '游戏资源', legacy.WEB_PORT), ('admin', 'GM 后台', legacy.ADMIN_PORT),
                     ('game', '游戏服务', legacy.GAME_PORT), ('policy', '连接策略', legacy.POLICY_PORT)]
            services = []
            for name, title, port in ports:
                active = legacy.port_open(port, timeout=.08)
                status = 'running' if active else 'starting' if self.busy else 'stopped'
                services.append({'id': name, 'name': title, 'port': port, 'status': status,
                                 'detail': '已连接' if active else '初始化中' if self.busy else '未启动'})
            try:
                current = self.account(self.active_account) if self.active_account else None
            except ValueError:
                current = None
            return {'running': self.running, 'busy': self.busy, 'statusMessage': self.status,
                    'progress': self.progress, 'error': self.error, 'activeAccountId': self.active_account,
                    'currentAccount': current, 'services': services, 'logCount': self.log_id,
                    'version': '2.0', 'player': 'Flash' if self.player_kind == 'flash' else 'Ruffle'}

    def close(self):
        with self.lock:
            self.closed = True
            try:
                self.stop()
            except ValueError:
                pass
