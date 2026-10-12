#!/usr/bin/env python3
"""
植物大战僵尸OL — 私人服务器启动器 (图形界面，Windows / macOS)。

一个按钮：启动网页服务器 + GM 后台 + 游戏服务器 (PvZ-Server-PoC/run_all.py)，待服务器就绪后，
自动打开游戏播放器并进入游戏。Windows 使用捆绑的 Flash Projector，
macOS 使用 Ruffle。再次点击则停止所有服务。

这里不包含任何游戏逻辑 —— 它只是封装了原本需要手动输入的命令：
  1. python PvZ-Server-PoC/run_all.py      (网页 :8080 + 后台 :8081 + 游戏 :9001 + Flash 策略 :843)
  2. Windows: flashplayer_sa.exe http://127.0.0.1:8080/loader_p.swf
     macOS:   Ruffle.app http://127.0.0.1:8080/loader_n.swf

只需双击 Windows 的 Play PvZ Online.bat，或 macOS 的
Play PvZ Online.command (也可运行 python launcher.py) 即可。
"""
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(HERE, "PvZ-Server-PoC")
RUN_ALL = os.path.join(SERVER_DIR, "run_all.py")
WINDOWS_PROJECTOR = os.path.join(HERE, "flashplayer_sa.exe")
WEB_PORT, ADMIN_PORT, GAME_PORT, POLICY_PORT = 8080, 8081, 9001, 843
ADMIN_URL = "http://127.0.0.1:%d/" % ADMIN_PORT
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
HIDDEN_PROCESS = ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
                  if IS_WINDOWS else {})

# loader_p.swf 是 Stage3D (GPU) 版本，适合原生 Flash Projector。
# Ruffle 下使用不依赖 Stage3D 的 loader_n.swf，兼容性更稳定。
# 客户端需要在 URL 中通过 flashvars 传递登录参数：server (socket 地址), uin (账号 ID = 存档),
# skey (会话密钥；PoC 服务器不验证此值)。每个 uin 对应一个独立的存档。
DEFAULT_UIN = "700000200"
GAME_SKEY = "ABCDEFGHIJ"
CONFIG_PATH = os.path.join(HERE, "launcher_config.json")
START_TIMEOUT = 180.0
ADMIN_TIMEOUT = 30.0
LAUNCHER_LOG = os.path.join(HERE, "portable_launcher.log")


def valid_uin(value):
    """The account must fit the server's SQLite signed integer key."""
    return (isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value) is not None
            and 0 < int(value) <= 9223372036854775807)


def python_command(script, *args):
    """Use the bundled interpreter when launched by the portable EXE."""
    runtime = getattr(sys, "_pvz_runtime", None)
    if not runtime:
        candidate = os.path.join(HERE, "运行环境", "PvZRuntime.exe")
        runtime = candidate if os.path.isfile(candidate) else None
    if runtime:
        return [runtime, "--script", os.path.basename(script), *map(str, args)]
    executable = sys.executable
    if executable.lower().endswith("pythonw.exe"):
        console = executable[:-len("pythonw.exe")] + "python.exe"
        if os.path.isfile(console):
            executable = console
    return [executable, script, *map(str, args)]


def game_url(uin, loader="loader_p.swf"):
    # zoneID= (空) 是必须的。客户端的 ExternalVars.initialize 会将 server= 读入 SOCKET_PATH，
    # 但当 ZONE_ID 非空时 (默认为 "0")，它会用硬编码的区域主机
    # "s<ZONE_ID>.zone.pvz.qq.com:8001" 覆盖 SOCKET_PATH/TGW_PATH。
    # 所以 server=127.0.0.1 会被忽略，游戏只有在机器的 hosts 文件将
    # s0.zone.pvz.qq.com -> 127.0.0.1 别名映射时才能工作。
    # 传入 zoneID= 空字符串会使 ZONE_ID 为空，从而跳过覆盖逻辑，
    # 客户端实际连接到 server= (127.0.0.1) —— 玩家机器上无需配置 hosts 条目。
    return ("http://127.0.0.1:%d/%s?server=127.0.0.1:%d&zoneID=&uin=%s&skey=%s"
            % (WEB_PORT, loader, GAME_PORT, uin, GAME_SKEY))


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return {}
        if not valid_uin(cfg.get("last_uin")):
            cfg["last_uin"] = DEFAULT_UIN
        recent = cfg.get("recent", [])
        cfg["recent"] = list(dict.fromkeys(u for u in recent if valid_uin(u)))[:8] if isinstance(recent, list) else []
        return cfg
    except Exception:
        return {}


def save_config(cfg):
    temporary = CONFIG_PATH + ".tmp.%d" % os.getpid()
    try:
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(temporary, CONFIG_PATH)
        return True
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return False


def _ruffle_executable(path):
    """将 Ruffle 命令或 .app 路径转成可执行文件路径。"""
    if not path:
        return None
    path = os.path.abspath(os.path.expanduser(path))
    if path.lower().endswith(".app"):
        path = os.path.join(path, "Contents", "MacOS", "ruffle")
    if os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    return None


def find_ruffle():
    """查找 Ruffle：配置/环境变量 > 游戏目录 > macOS 应用 > PATH。"""
    cfg = load_config()
    candidates = [
        os.environ.get("RUFFLE_PATH"),
        cfg.get("ruffle_path"),
        os.path.join(HERE, "Ruffle.app"),
        os.path.join(HERE, "ruffle"),
    ]
    if IS_MACOS:
        candidates.extend([
            "/Applications/Ruffle.app",
            os.path.expanduser("~/Applications/Ruffle.app"),
        ])
    on_path = shutil.which("ruffle")
    if on_path:
        candidates.append(on_path)
    for candidate in candidates:
        executable = _ruffle_executable(candidate) if candidate else None
        if executable:
            return executable
    return None


def resolve_game_player():
    """返回 (player_kind, executable)；未找到则 executable 为 None。"""
    if IS_WINDOWS:
        return "flash", WINDOWS_PROJECTOR if os.path.isfile(WINDOWS_PROJECTOR) else None
    return "ruffle", find_ruffle()


def game_player_command(player_kind, executable, url):
    """构造播放器命令；Ruffle 显式允许本机游戏套接字。"""
    if player_kind == "ruffle":
        return [
            executable,
            "--socket-allow", "127.0.0.1:%d" % GAME_PORT,
            url,
        ]
    return [executable, url]


def game_player_environment(player_kind):
    """确保 Ruffle 访问本机 HTTP 服务时不经过系统代理。"""
    env = os.environ.copy()
    if player_kind != "ruffle":
        return env
    local_hosts = ("127.0.0.1", "localhost", "::1")
    for key in ("NO_PROXY", "no_proxy"):
        values = [item.strip() for item in env.get(key, "").split(",") if item.strip()]
        for host in local_hosts:
            if host not in values:
                values.append(host)
        env[key] = ",".join(values)
    return env

# 颜色 (深色，花园风格)
BG = "#1f2d16"
PANEL = "#28381d"
FG = "#e8f0dc"
MUTED = "#9db083"
ACCENT = "#7cc242"
ACCENT_HOVER = "#8fd450"
STOP = "#c0532f"
LOGBG = "#141d0d"


def port_open(port, host="127.0.0.1", timeout=0.35):
    """如果 host:port 上有程序正在接受连接，返回 True (服务器已启动 / 端口被占用)。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def admin_ready():
    """确认 8081 上是本项目的后台，而不只是判断端口被某个程序占用。"""
    try:
        with socket.create_connection(("127.0.0.1", ADMIN_PORT), timeout=0.4) as sock:
            sock.sendall(
                b"GET /healthz HTTP/1.0\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n"
                % ADMIN_PORT)
            response = b""
            while len(response) < 4096:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        return b" 200 " in response.split(b"\r\n", 1)[0] and response.endswith(b"ok\n")
    except OSError:
        return False


def web_ready(loader):
    """Check that the selected loader is actually served, not just an open port."""
    try:
        with open(os.path.join(HERE, "webroot", loader), "rb") as source:
            expected = source.read(8)
        if len(expected) != 8 or expected[:3] not in (b"FWS", b"CWS", b"ZWS"):
            return False
        with socket.create_connection(("127.0.0.1", WEB_PORT), timeout=0.4) as sock:
            sock.sendall(("GET /%s HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n" % loader).encode("ascii"))
            response = b""
            while len(response) < 16384:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\r\n\r\n" in response and len(response.split(b"\r\n\r\n", 1)[1]) >= 8:
                    break
        header, body = response.split(b"\r\n\r\n", 1)
        return b" 200 " in header.split(b"\r\n", 1)[0] and body[:8] == expected
    except (OSError, ValueError):
        return False


def policy_ready():
    try:
        with socket.create_connection(("127.0.0.1", POLICY_PORT), timeout=0.4) as sock:
            sock.sendall(b"<policy-file-request/>\x00")
            response = b""
            while len(response) < 8192:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\x00" in response:
                    break
        if b"<cross-domain-policy>" not in response or b"</cross-domain-policy>" not in response:
            return False
        for tag in re.findall(rb"<allow-access-from\b[^>]*>", response):
            if not re.search(rb"domain=[\"']\*[\"']", tag):
                continue
            match = re.search(rb"to-ports=[\"']([^\"']+)[\"']", tag)
            if not match:
                continue
            for part in match[1].decode("ascii").split(","):
                part = part.strip()
                if part == "*":
                    return True
                bounds = part.split("-")
                if len(bounds) == 1 and int(bounds[0]) == GAME_PORT:
                    return True
                if len(bounds) == 2 and int(bounds[0]) <= GAME_PORT <= int(bounds[1]):
                    return True
        return False
    except (OSError, ValueError, UnicodeError):
        return False






class Launcher:
    def __init__(self, root):
        self.root = root
        self.server = None           # run_all.py 子进程
        self.admin = None            # 单独打开后台时的 admin_server.py 子进程
        self.game = None             # Flash Projector / Ruffle 子进程
        self.game_url = None         # 点击 Play 时根据所选账号生成
        self.player_kind, self.player_path = resolve_game_player()
        self.log_q = queue.Queue()
        self.running = False
        self._session = 0
        self._cancel = threading.Event()
        self._closed = False
        self._admin_waiting = None
        self._can_start = True
        self._build_ui()
        self._sanity_check()
        self.root.after(120, self._poll_log)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- 界面 ----------------
    def _build_ui(self):
        r = self.root
        r.title("植物大战僵尸OL — 启动器")
        r.configure(bg=BG)
        r.geometry("580x520")
        r.minsize(520, 460)

        header = tk.Frame(r, bg=BG)
        header.pack(fill="x", padx=20, pady=(18, 6))
        tk.Label(header, text="植物大战僵尸OL", bg=BG, fg=FG,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(header, text="私人服务器启动器", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w")

        status = tk.Frame(r, bg=PANEL, highlightthickness=0)
        status.pack(fill="x", padx=20, pady=8)
        self.dot = tk.Canvas(status, width=16, height=16, bg=PANEL, highlightthickness=0)
        self.dot.pack(side="left", padx=(12, 8), pady=10)
        self._dot_id = self.dot.create_oval(2, 2, 14, 14, fill="#888", outline="")
        self.status_lbl = tk.Label(status, text="已停止", bg=PANEL, fg=FG,
                                   font=("Segoe UI", 11, "bold"))
        self.status_lbl.pack(side="left", pady=10)

        # 账号选择器 (可编辑下拉框，会记住最近使用的账号)
        style = ttk.Style(r)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dark.TCombobox", fieldbackground=PANEL, background=PANEL, foreground=FG,
                        arrowcolor=FG, bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL,
                        selectbackground=PANEL, selectforeground=FG, padding=4)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
        # 下拉列表是一个独立的 Tk Listbox -> 通过选项数据库设置主题
        r.option_add("*TCombobox*Listbox.background", LOGBG)
        r.option_add("*TCombobox*Listbox.foreground", FG)
        r.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        r.option_add("*TCombobox*Listbox.selectForeground", "#12220a")
        cfg = load_config()
        acct = tk.Frame(r, bg=BG)
        acct.pack(fill="x", padx=20, pady=(8, 2))
        tk.Label(acct, text="账号", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        self.uin_var = tk.StringVar(value=cfg.get("last_uin", DEFAULT_UIN))
        self.uin_box = ttk.Combobox(acct, textvariable=self.uin_var, values=cfg.get("recent", []),
                                    style="Dark.TCombobox", font=("Segoe UI", 11))
        self.uin_box.pack(fill="x", pady=(3, 0))

        btns = tk.Frame(r, bg=BG)
        btns.pack(fill="x", padx=20, pady=6)
        self.play_btn = tk.Button(btns, text="开始", command=self.start,
                                  bg=ACCENT, fg="#12220a", activebackground=ACCENT_HOVER,
                                  activeforeground="#12220a", relief="flat", cursor="hand2",
                                  font=("Segoe UI", 13, "bold"), padx=18, pady=10, bd=0)
        self.play_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.stop_btn = tk.Button(btns, text="停止", command=self.stop,
                                  bg=STOP, fg="#ffffff", activebackground="#d5643c",
                                  activeforeground="#ffffff", relief="flat", cursor="hand2",
                                  font=("Segoe UI", 13, "bold"), padx=18, pady=10, bd=0,
                                  state="disabled")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(6, 0))

        # 辅助功能：网页 GM 后台 + 旧版存档编辑器。
        tools = tk.Frame(r, bg=BG)
        tools.pack(fill="x", padx=20, pady=(0, 2))
        self.admin_btn = tk.Button(tools, text="打开后台", command=self.open_admin,
                                   bg=PANEL, fg=FG, activebackground="#33481f", activeforeground=FG,
                                   relief="flat", cursor="hand2", font=("Segoe UI", 10, "bold"),
                                   padx=10, pady=6, bd=0)
        self.admin_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.edit_btn = tk.Button(tools, text="编辑存档", command=self.open_save_editor,
                                  bg=PANEL, fg=FG, activebackground="#33481f", activeforeground=FG,
                                  relief="flat", cursor="hand2", font=("Segoe UI", 10),
                                  padx=10, pady=6, bd=0)
        self.edit_btn.pack(side="left", expand=True, fill="x", padx=(5, 0))

        # 详情 / 日志
        toggle = tk.Frame(r, bg=BG)
        toggle.pack(fill="x", padx=20, pady=(10, 0))
        self.log_shown = tk.BooleanVar(value=False)
        self.toggle_btn = tk.Label(toggle, text="服务器日志", bg=BG, fg=MUTED,
                                   font=("Segoe UI", 9), cursor="hand2")
        self.toggle_btn.pack(anchor="w")
        self.toggle_btn.bind("<Button-1>", lambda e: self._toggle_log())

        self.log_frame = tk.Frame(r, bg=BG)
        self.log = scrolledtext.ScrolledText(self.log_frame, height=10, bg=LOGBG, fg="#bcd39a",
                                             insertbackground=FG, relief="flat", wrap="word",
                                             font=("Consolas", 8))
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        foot = tk.Frame(r, bg=BG)
        foot.pack(side="bottom", fill="x", padx=20, pady=10)
        tk.Label(foot, text="在本机运行   ·   网页 :%d   后台 :%d   游戏 :%d   策略 :%d"
                 % (WEB_PORT, ADMIN_PORT, GAME_PORT, POLICY_PORT),
                 bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

    # ---------------- GM 网页后台 ----------------
    def open_admin(self):
        if self._closed or self._admin_waiting:
            return
        if admin_ready():
            webbrowser.open(ADMIN_URL)
            self._log("已打开 GM 后台: %s" % ADMIN_URL)
            return
        if self.running and self.server:
            self._begin_admin_wait(self.server, self._session)
            return
        if self.admin and self.admin.poll() is None:
            self._begin_admin_wait(self.admin, self._session)
            return
        if port_open(ADMIN_PORT):
            messagebox.showerror("无法打开后台", "端口 %d 已被其他程序占用，请关闭对应程序后重试。" % ADMIN_PORT)
            return
        admin_script = os.path.join(SERVER_DIR, "admin_server.py")
        if not os.path.isfile(admin_script):
            messagebox.showerror("无法打开后台", "缺少文件:\n%s" % admin_script)
            return
        self._session += 1
        self._cancel.set()
        self._cancel = threading.Event()
        try:
            proc = subprocess.Popen(
                python_command(admin_script), cwd=SERVER_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}, **HIDDEN_PROCESS)
        except Exception as exc:
            messagebox.showerror("无法启动后台", repr(exc))
            return
        self.admin = proc
        self.stop_btn.configure(state="normal")
        self._log("--- 正在单独启动 GM 后台 ---")
        threading.Thread(target=self._admin_reader, args=(proc, self._session), daemon=True).start()
        self._begin_admin_wait(proc, self._session)

    def _begin_admin_wait(self, proc, session):
        self._admin_waiting = (session, proc)
        self.admin_btn.configure(state="disabled")
        threading.Thread(target=self._wait_admin_and_open, args=(proc, session), daemon=True).start()

    def _admin_opened(self, proc, session):
        if not self._active(session, proc):
            return
        self._admin_waiting = None
        self.admin_btn.configure(state="normal")
        webbrowser.open(ADMIN_URL)
        self._log("已打开 GM 后台: %s" % ADMIN_URL)

    def _admin_failed(self, proc, session):
        if self._closed or session != self._session or proc not in (self.admin, self.server):
            return
        self._admin_waiting = None
        self.admin_btn.configure(state="normal")
        self._log("!! 后台未能就绪，请检查日志或端口 %d。" % ADMIN_PORT)
        if proc is self.admin and self._terminate_owned(proc, "GM 后台"):
            self.admin = None
            if not self.running:
                self.stop_btn.configure(state="disabled")


    def _admin_reader(self, proc, session):
        try:
            if proc.stdout:
                for line in proc.stdout:
                    self.log_q.put(("__LOG__", session, line))
        except (OSError, ValueError):
            pass
        finally:
            self.log_q.put(("__ADMIN_EXITED__", session, proc))


    def _wait_admin_and_open(self, proc, session):
        cancel = self._cancel
        deadline = time.monotonic() + ADMIN_TIMEOUT
        while self._active(session, proc) and not cancel.is_set() and time.monotonic() < deadline:
            if admin_ready():
                self._post(session, self._admin_opened, proc, session)
                return
            cancel.wait(0.25)
        if not cancel.is_set():
            self._post(session, self._admin_failed, proc, session)


    # ---------------- 存档编辑器 ----------------
    def _run_gm(self, args):
        """运行 PvZ-Server-PoC/gm.py 并传入参数 (列表)，针对当前账号的存档操作；返回 (是否成功, 输出)。"""
        if self.running or port_open(GAME_PORT):
            return False, "请先停止游戏服务，再修改存档。"
        try:
            p = subprocess.run(python_command(os.path.join(SERVER_DIR, "gm.py"), *args),
                               cwd=SERVER_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                               **HIDDEN_PROCESS)
            return (p.returncode == 0, (p.stdout or "") + (p.stderr or ""))
        except Exception as exc:
            return (False, "错误: %r" % exc)

    def open_save_editor(self):
        if self.running or port_open(GAME_PORT):
            messagebox.showwarning("存档编辑器", "请先停止游戏服务，再打开存档编辑器。")
            return
        uin = self.uin_var.get().strip()
        if not valid_uin(uin):
            messagebox.showwarning("存档编辑器", "请先输入一个数字账号。")
            return
        # 直接读取当前存档 (player_store 的数据库路径固定在 PvZ-Server-PoC/players.db)
        try:
            if SERVER_DIR not in sys.path:
                sys.path.insert(0, SERVER_DIR)
            import player_store as store
            store.init_db()   # 如果是全新安装，创建 players.db + 表结构
            player, _ = store.get_or_create_player(int(uin))
        except Exception as exc:
            messagebox.showerror("存档编辑器", "无法打开存档:\n%r" % exc)
            return

        win = tk.Toplevel(self.root)
        win.title("存档编辑器 — 账号 %s" % uin)
        win.configure(bg=BG)
        win.geometry("500x880")
        win.transient(self.root)

        tk.Label(win, text="存档编辑器", bg=BG, fg=FG,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=16, pady=(14, 0))
        note = ("游戏正在运行 — 请先停止，编辑后再点击开始以应用更改。"
                if self.running else "账号 %s   ·   更改将在下次点击开始时生效。" % uin)
        tk.Label(win, text=note, bg=BG, fg=(STOP if self.running else MUTED),
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(2, 8))

        status = tk.Label(win, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=430,
                          justify="left")

        def gm(args, ok_msg):
            ok, out = self._run_gm(args)
            status.configure(text=(ok_msg if ok else "失败 — " + out.strip().splitlines()[-1:][0]
                                   if out.strip() else "失败"), fg=(ACCENT if ok else STOP))
            return ok

        # --- 货币 / 属性字段 -------------------------------------------------
        FIELDS = [("等级", "level"), ("金币", "coin"), ("阳光", "sun"), ("蓝钻", "present_ticket"),
                  ("金钻", "ticket"), ("声望", "fame"), ("体力", "energy"),
                  ("好友点", "friend_point")]
        grid = tk.Frame(win, bg=PANEL)
        grid.pack(fill="x", padx=16, pady=4)
        ed_vars = {}
        for i, (label, key) in enumerate(FIELDS):
            row, col = divmod(i, 2)
            grid.columnconfigure(col, weight=1)
            cell = tk.Frame(grid, bg=PANEL)
            cell.grid(row=row, column=col, sticky="ew", padx=8, pady=6)
            tk.Label(cell, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            v = tk.StringVar(value=str(player.get(key, 0)))
            tk.Entry(cell, textvariable=v, bg=LOGBG, fg=FG, insertbackground=FG, relief="flat",
                     font=("Consolas", 10)).pack(fill="x")
            ed_vars[key] = v

        # --- VIP 等级 ---------------------------------------------------------------
        vipf = tk.Frame(win, bg=BG)
        vipf.pack(fill="x", padx=16, pady=(8, 2))
        tk.Label(vipf, text="VIP 等级", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        VIP_OPTS = ["关闭", "VIP1", "VIP2", "VIP3"]
        cur_vip = int(player.get("vip_level", 0) or 0)
        vip_var = tk.StringVar(value=VIP_OPTS[cur_vip] if 0 <= cur_vip <= 3 else "关闭")
        ttk.Combobox(vipf, textvariable=vip_var, values=VIP_OPTS, state="readonly",
                     style="Dark.TCombobox", width=8).pack(side="left", padx=8)
        # 塔防 特权 (塔防特权) — 同一行右侧
        tk.Label(vipf, text="塔防 特权", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(24, 0))
        TD_OPTS = ["关闭", "开启"]
        cur_td = int(player.get("td_privilege", 0) or 0)
        td_var = tk.StringVar(value=TD_OPTS[cur_td] if 0 <= cur_td <= 1 else "关闭")
        ttk.Combobox(vipf, textvariable=td_var, values=TD_OPTS, state="readonly",
                     style="Dark.TCombobox", width=6).pack(side="left", padx=8)

        def apply_main():
            args = [uin]
            for _label, key in FIELDS:
                val = ed_vars[key].get().strip()
                if val.lstrip("-").isdigit():
                    args += [key, val]
            ok = True
            if len(args) > 1:
                ok = self._run_gm(args)[0]
            ok = self._run_gm([uin, "vip", str(VIP_OPTS.index(vip_var.get()))])[0] and ok
            ok = self._run_gm([uin, "tdpriv", str(TD_OPTS.index(td_var.get()))])[0] and ok
            status.configure(text=("已保存。停止后重新开始以应用更改。" if ok else "存档保存失败 — 请查看服务器日志"),
                             fg=(ACCENT if ok else STOP))

        tk.Button(win, text="应用更改", command=apply_main, bg=ACCENT, fg="#12220a",
                  activebackground=ACCENT_HOVER, activeforeground="#12220a", relief="flat",
                  cursor="hand2", font=("Segoe UI", 11, "bold"), padx=12, pady=8, bd=0).pack(
            fill="x", padx=16, pady=(10, 6))

        # --- 快捷操作 (每个按钮独立应用) -------------------------
        ttk.Separator(win, orient="horizontal").pack(fill="x", padx=16, pady=4)

        def action_row(label, fields, on_go):
            fr = tk.Frame(win, bg=BG)
            fr.pack(fill="x", padx=16, pady=3)
            tk.Label(fr, text=label, bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
            entries = []
            for placeholder, width, default in fields:
                tk.Label(fr, text=placeholder, bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(6, 1))
                e = tk.Entry(fr, bg=LOGBG, fg=FG, width=width, relief="flat", insertbackground=FG,
                             font=("Consolas", 10))
                if default:
                    e.insert(0, default)
                e.pack(side="left")
                entries.append(e)
            tk.Button(fr, text="执行", command=lambda: on_go([e.get().strip() for e in entries]),
                      bg=PANEL, fg=FG, activebackground="#33481f", activeforeground=FG, relief="flat",
                      cursor="hand2", font=("Segoe UI", 9), padx=10, pady=3, bd=0).pack(side="right")

        def give_item(vals):
            iid, ct = vals
            if iid.isdigit() and ct.lstrip("-").isdigit():
                gm([uin, "item", iid, ct], "已给予物品 %s x %s" % (iid, ct))
        action_row("给予物品", [("ID", 8, ""), ("数量", 6, "1")], give_item)

        def clear_levels(vals):
            st, lv = vals
            if st.isdigit() and lv.isdigit():
                gm([uin, "passto", st, lv], "已通关至 %s-%s (解锁门和植物)" % (st, lv))
        action_row("通关到指定关卡", [("章节", 5, "1"), ("关卡", 5, "")], clear_levels)

        def level_cards(vals):
            lv = vals[0]
            if lv.isdigit():
                gm([uin, "cardlevel", lv], "已将所有植物卡设为 %s 级 (受账号等级上限限制)" % lv)
        action_row("设置所有植物卡等级", [("等级", 5, "")], level_cards)

        # --- 钻石卡 (拥有钻石卡) -------------------------------------------
        # 玩家拥有的 Dto_DiamondInfo — 登录时通过 Cmd_Vip_SyncVipInfo_SC 推送，
        # ModuleBlueDiamond.swf 据此渲染卡片。flag=1 表示拥有；blue/yellow/xinyue = 钻石等级
        # (0 = 未获得该钻石)。购买 (flag=1) 会消耗 20 钻石。
        tk.Label(win, text="钻石卡", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(10, 0))

        def apply_diamond(vals):
            f, b, y, x = vals
            if f.isdigit() and b.isdigit() and y.isdigit() and x.isdigit():
                gm([uin, "diamond", f, b, y, x], "钻石卡已设置 — 重新启动以应用")
        action_row("拥有钻石卡",
                   [("标志", 4, str(player.get("diamond_flag", 1) or 0)),
                    ("蓝钻", 4, str(player.get("diamond_blue_lvl", 0) or 0)),
                    ("黄钻", 4, str(player.get("diamond_yellow_lvl", 0) or 0)),
                    ("好友点", 4, str(player.get("diamond_xinyue_lvl", 0) or 0))],
                   apply_diamond)

        # --- 好友 (好友 + 好友卡) ---------------------------------------------
        # 每个好友会显示在好友列表中，附带 TD "好友卡" (helpPlant =
        # Dto_CardFullInfo 由所选 tdPlantId 构建) 以及每个好友独立的钻石卡。
        tk.Label(win, text="好友 (好友卡)", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(10, 0))

        fr_box = tk.Frame(win, bg=PANEL)
        fr_box.pack(fill="x", padx=16, pady=2)

        fr_rid = tk.StringVar(); fr_name = tk.StringVar()
        fr_plv = tk.StringVar()
        fr_df = tk.StringVar(value="1"); fr_db = tk.StringVar()
        fr_dy = tk.StringVar(); fr_dx = tk.StringVar()

        def fr_entry(parent, label, var, width):
            tk.Label(parent, text=label, bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 8)).pack(side="left", padx=(4, 1))
            tk.Entry(parent, textvariable=var, bg=LOGBG, fg=FG, width=width, relief="flat",
                     insertbackground=FG, font=("Consolas", 10)).pack(side="left", padx=(0, 6))

        # 完整植物列表 (TD 植物 ID → 显示名称)
        _TD_PLANTS = [
            (1200, "1200  单发豌豆"),
            (1210, "1210  双发豌豆"),
            (1300, "1300  矮坚果"),
            (1100, "1100  向日葵"),
            (1500, "1500  土豆地雷"),
            (1600, "1600  回旋镖"),
            (1400, "1400  卷心菜"),
            (1110, "1110  双头向日"),
            (1800, "1800  白菜战士"),
            (1700, "1700  冰冻生菜"),
            (1900, "1900  墓碑吞噬"),
            (2000, "2000  魔藤"),
            (3100, "3100  竹小龙"),
            (3300, "3300  蒲公英"),
            (3200, "3200  胡萝卜火"),
            (3400, "3400  冬瓜先生"),
            (3500, "3500  草垛"),
            (3600, "3600  捕蝇草"),
            (3700, "3700  倭瓜"),
            (3800, "3800  保龄球"),
            (3850, "3850  爆炸保龄"),
            (3900, "3900  火龙草"),
            (4000, "4000  小游戏胡"),
            (4100, "4100  地刺"),
            (4200, "4200  地刺王"),
            (4300, "4300  玉米"),
            (4400, "4400  弹簧豆"),
            (4500, "4500  三发豌豆"),
            (4600, "4600  椰子加农"),
            (4700, "4700  石榴"),
            (4800, "4800  水仙花"),
            (4900, "4900  榴莲"),
            (5000, "5000  菠萝投手"),
            (5100, "5100  重置"),
            (5200, "5200  填坑"),
            (5300, "5300  寒冰射手"),
            (5400, "5400  双头豌豆"),
            (5500, "5500  豌豆荚"),
            (5600, "5600  高坚果"),
            (5700, "5700  樱桃炸弹"),
            (5900, "5900  巴豆"),
            (6000, "6000  火桩"),
            (6100, "6100  电草"),
            (6200, "6200  杨桃"),
            (6300, "6300  激光豆子"),
            (6400, "6400  充能柚子"),
            (6500, "6500  全息坚果"),
            (6600, "6600  棱镜草"),
            (6700, "6700  辣椒"),
            (6800, "6800  冰豆"),
            (6900, "6900  冰冻西瓜"),
            (7100, "7100  三叶草"),
            (7200, "7200  脉冲黄桃"),
            (7400, "7400  硝化菇"),
            (7500, "7500  食人花"),
            (7600, "7600  瓷砖萝卜"),
            (7700, "7700  西瓜投手"),
            (7800, "7800  香蒲"),
            (7900, "7900  灵芝"),
            (8000, "8000  芭蕉"),
            (8100, "8100  氧气藻"),
            (8200, "8200  大蒜"),
            (8300, "8300  电海松"),
            (8400, "8400  泡泡花"),
            (8500, "8500  海星果"),
            (8600, "8600  钻地荔枝"),
            (8700, "8700  海葵花"),
            (8800, "8800  水雷果"),
            (8900, "8900  玫瑰剑客"),
            (9000, "9000  君子兰"),
            (9100, "9100  树脂发射"),
            (9200, "9200  强酸柠檬"),
            (9300, "9300  魔术菇"),
            (9400, "9400  火焰射手"),
            (9500, "9500  幽灵辣椒"),
            (9600, "9600  鳄梨"),
            (9700, "9700  飓风甘蓝"),
            (9800, "9800  路灯花"),
            (9900, "9900  橡木射手"),
            (1120, "1120  刺头花"),
            (1130, "1130  甜椒投手"),
            (1140, "1140  南瓜小巫"),
            (1150, "1150  功夫萝卜"),
            (1160, "1160  金蟾菇"),
            (1170, "1170  铃儿草"),
            (1180, "1180  红针花"),
            (1220, "1220  闪电蓝莓"),
            (1190, "1190  阿开木"),
            (1230, "1230  龙舌兰"),
            (1240, "1240  月光花"),
            (1250, "1250  芦笋战机"),
        ]
        _PLANT_DISPLAY = [d for _pid, d in _TD_PLANTS]   # 显示名称列表
        _PLANT_BY_DISPLAY = {d: _pid for _pid, d in _TD_PLANTS}

        fr_pid_var = tk.StringVar()
        fr_pid_idx = tk.IntVar(value=0)   # _TD_PLANTS 中的索引 (默认 1100 向日葵)

        fr_row1 = tk.Frame(fr_box, bg=PANEL)
        fr_row1.pack(fill="x", pady=(2, 1))
        fr_entry(fr_row1, "角色ID", fr_rid, 12)
        fr_entry(fr_row1, "名称", fr_name, 12)
        fr_entry(fr_row1, "等级", fr_plv, 4)

        fr_row1b = tk.Frame(fr_box, bg=PANEL)
        fr_row1b.pack(fill="x", pady=(1, 1))
        tk.Label(fr_row1b, text="植物", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(4, 1))
        ttk.Combobox(fr_row1b, textvariable=fr_pid_var, values=_PLANT_DISPLAY,
                     state="readonly", style="Dark.TCombobox", width=22).pack(side="left", padx=(0, 6))

        fr_row2 = tk.Frame(fr_box, bg=PANEL)
        fr_row2.pack(fill="x", pady=(1, 2))
        fr_entry(fr_row2, "标志", fr_df, 4)
        fr_entry(fr_row2, "蓝钻", fr_db, 4)
        fr_entry(fr_row2, "黄钻", fr_dy, 4)
        fr_entry(fr_row2, "好友点", fr_dx, 4)

        fr_list = tk.Listbox(win, bg=LOGBG, fg=FG, selectbackground=ACCENT,
                             selectforeground="#12220a", relief="flat", height=4,
                             font=("Consolas", 9), activestyle="none")
        fr_list.pack(fill="x", padx=16, pady=2)

        def refresh_friends():
            fr_list.delete(0, "end")
            for f in store.get_friends(int(uin)):
                fr_list.insert("end", "%d | %s | 植物 %d Lv%d | 钻石: 标志=%d 蓝=%d 黄=%d 心悦=%d" % (
                    f["friend_role_id"], f["friend_name"], f["help_plant_id"],
                    f["help_plant_level"], f["diamond_flag"], f["diamond_blue_lvl"],
                    f["diamond_yellow_lvl"], f["diamond_xinyue_lvl"]))
        refresh_friends()

        fr_row3 = tk.Frame(win, bg=BG)
        fr_row3.pack(fill="x", padx=16, pady=(2, 4))

        def add_friend():
            rid = fr_rid.get().strip(); name = fr_name.get().strip()
            pid = str(_PLANT_BY_DISPLAY.get(fr_pid_var.get().strip(), 0))
            plv = fr_plv.get().strip()
            df = fr_df.get().strip() or "1"; db = fr_db.get().strip() or "0"
            dy = fr_dy.get().strip() or "0"; dx = fr_dx.get().strip() or "0"
            if not (rid.isdigit() and pid.isdigit() and plv.isdigit() and name):
                status.configure(text="需要角色ID、名称、植物、等级", fg=STOP); return
            if not (df.isdigit() and db.isdigit() and dy.isdigit() and dx.isdigit()):
                status.configure(text="钻石字段必须为数字", fg=STOP); return
            if gm([uin, "addfriend", rid, name, pid, plv, df, db, dy, dx],
                  "已添加好友 %s — 重新启动以应用" % name):
                refresh_friends()

        def del_friend():
            sel = fr_list.curselection()
            if not sel:
                status.configure(text="请先选择一个好友", fg=STOP); return
            rid = fr_list.get(sel[0]).split(" | ", 1)[0]
            if rid.isdigit() and gm([uin, "delfriend", rid],
                                     "已移除好友 %s — 重新启动以应用" % rid):
                refresh_friends()

        tk.Button(fr_row3, text="添加 / 更新", command=add_friend, bg=ACCENT, fg="#12220a",
                  activebackground=ACCENT_HOVER, activeforeground="#12220a", relief="flat",
                  cursor="hand2", font=("Segoe UI", 9, "bold"), padx=10, pady=4, bd=0).pack(side="left")
        tk.Button(fr_row3, text="移除", command=del_friend, bg=PANEL, fg=FG,
                  activebackground="#33481f", activeforeground=FG, relief="flat", cursor="hand2",
                  font=("Segoe UI", 9), padx=10, pady=4, bd=0).pack(side="left", padx=6)
        tk.Button(fr_row3, text="刷新", command=refresh_friends, bg=PANEL, fg=FG,
                  activebackground="#33481f", activeforeground=FG, relief="flat", cursor="hand2",
                  font=("Segoe UI", 9), padx=10, pady=4, bd=0).pack(side="left")

        tk.Label(win, text="提示：先提升等级 — 等级会限制植物卡等级上限并解锁城镇建筑。",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=470, justify="left").pack(
            anchor="w", padx=16, pady=(8, 2))
        status.pack(anchor="w", padx=16, pady=(4, 10))

    def _toggle_log(self):
        if self.log_shown.get():
            self.log_frame.pack_forget()
            self.toggle_btn.configure(text="服务器日志")
        else:
            self.log_frame.pack(fill="both", expand=True, padx=20, pady=(2, 0))
            self.toggle_btn.configure(text="服务器日志")
        self.log_shown.set(not self.log_shown.get())

    def _set_status(self, text, colour):
        self.status_lbl.configure(text=text)
        self.dot.itemconfigure(self._dot_id, fill=colour)

    def _log(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        try:
            if os.path.isfile(LAUNCHER_LOG) and os.path.getsize(LAUNCHER_LOG) > 5 * 1024 * 1024:
                os.replace(LAUNCHER_LOG, LAUNCHER_LOG + ".1")
            with open(LAUNCHER_LOG, "a", encoding="utf-8") as stream:
                stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line.rstrip() + "\n")
        except OSError:
            pass

    def _post(self, session, callback, *args):
        # Worker threads never call Tk, including root.after.
        self.log_q.put(("__CALL__", session, callback, args))

    def _active(self, session, proc):
        return (not self._closed and session == self._session and proc is not None
                and proc in (self.server, self.admin) and proc.poll() is None)

    def _terminate_owned(self, proc, name):
        if not proc or proc.poll() is not None:
            return True
        try:
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.5)
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            self._log("!! 无法停止%s: %r" % (name, exc))
            return proc.poll() is not None

    # ---------------- 生命周期 ----------------
    def _sanity_check(self):
        loader = "loader_n.swf" if self.player_kind == "ruffle" else "loader_p.swf"
        missing = [p for p in (RUN_ALL, os.path.join(HERE, "webroot", loader))
                   if not os.path.exists(p)]
        if missing:
            self._log("!! 缺少文件:\n   " + "\n   ".join(missing))
            self._log("!! 请确保 launcher.py 仍在游戏根目录。")
            self._can_start = False
            self.play_btn.configure(state="disabled")
            return
        if not self.player_path:
            if self.player_kind == "ruffle":
                self._log("!! 未找到 Ruffle。请将 Ruffle.app 放入 /Applications，")
                self._log("   或在 launcher_config.json 中设置 ruffle_path。")
            else:
                self._log("!! 缺少播放器: %s" % WINDOWS_PROJECTOR)
            self._can_start = False
            self.play_btn.configure(state="disabled")
            return
        player_name = "Ruffle" if self.player_kind == "ruffle" else "Flash Projector"
        self._log("已找到 %s: %s" % (player_name, self.player_path))

    def start(self):
        if self.running or self._closed or not self._can_start:
            return
        if any(proc and proc.poll() is None for proc in (self.server, self.game)):
            self._log("!! 上一次进程尚未停止，请先点击停止。")
            return
        uin = self.uin_var.get().strip()
        if not valid_uin(uin):
            messagebox.showwarning("账号", "请输入有效的纯数字账号（1 到 9223372036854775807）。")
            return
        # Retire only the standalone admin process owned by this launcher.
        if self.admin and not self._terminate_owned(self.admin, "GM 后台"):
            return
        self.admin = None
        self._admin_waiting = None
        self._session += 1
        self._cancel.set()
        self.admin_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        busy_ports = [p for p in (WEB_PORT, ADMIN_PORT, GAME_PORT, POLICY_PORT) if port_open(p)]
        if busy_ports:
            messagebox.showerror("端口已被占用", "以下端口已被占用：%s。\n请关闭旧启动器或对应服务后重试。" % ", ".join(map(str, busy_ports)))
            return
        loader = "loader_n.swf" if self.player_kind == "ruffle" else "loader_p.swf"
        self.game_url = game_url(uin, loader)
        cfg = load_config()
        recent = [uin] + [u for u in cfg.get("recent", []) if u != uin]
        cfg.update({"last_uin": uin, "recent": recent[:8]})
        if not save_config(cfg):
            self._log("!! 无法保存最近账号，请检查游戏目录的写入权限。")
        self.uin_box.configure(values=recent[:8], state="disabled")
        self.play_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.admin_btn.configure(state="normal")
        self._set_status("正在初始化服务器…", "#e0b83a")
        self._log("--- 启动网页、后台、游戏和 Flash 策略服务；首次初始化可能需要数分钟 ---")
        self._session += 1
        self._cancel.set()
        self._cancel = threading.Event()
        try:
            proc = subprocess.Popen(
                python_command(RUN_ALL), cwd=SERVER_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}, **HIDDEN_PROCESS)
        except Exception as exc:
            self._log("!! 无法启动服务器: %r" % exc)
            self._reset_stopped()
            return
        self.server = proc
        self.running = True
        threading.Thread(target=self._reader, args=(proc, self._session), daemon=True).start()
        threading.Thread(target=self._wait_ready, args=(proc, self._session), daemon=True).start()


    def _reader(self, proc, session):
        try:
            if proc.stdout:
                for line in proc.stdout:
                    self.log_q.put(("__LOG__", session, line))
        except (OSError, ValueError):
            pass
        finally:
            self.log_q.put(("__SERVER_EXITED__", session, proc))


    def _wait_ready(self, proc, session):
        cancel = self._cancel
        loader = "loader_n.swf" if self.player_kind == "ruffle" else "loader_p.swf"
        deadline = time.monotonic() + START_TIMEOUT
        while self.running and self._active(session, proc) and not cancel.is_set() and time.monotonic() < deadline:
            if port_open(GAME_PORT) and web_ready(loader) and admin_ready() and policy_ready():
                self._post(session, self._ready, proc, session)
                return
            cancel.wait(0.25)
        if not cancel.is_set() and self._active(session, proc):
            self._post(session, self._startup_failed, proc, session)

    def _ready(self, proc, session):
        if self.running and self._active(session, proc):
            self._launch_game()

    def _startup_failed(self, proc, session):
        if not self.running or not self._active(session, proc):
            return
        self._log("!! 服务器初始化超时：网页、后台、9001 游戏或 843 策略服务未就绪。正在清理本次进程，可重新点击开始。")
        self.stop()


    def _launch_game(self):
        if not self.running:
            return
        self._set_status("运行中 — 正在启动游戏…", ACCENT)
        player_name = "Ruffle" if self.player_kind == "ruffle" else "Flash Projector"
        self._log("--- 服务器已就绪 · 正在用 %s 打开游戏 (账号 %s) ---"
                  % (player_name, self.uin_var.get().strip()))
        try:
            command = game_player_command(
                self.player_kind, self.player_path, self.game_url)
            self.game = subprocess.Popen(
                command, cwd=HERE, env=game_player_environment(self.player_kind),
                **HIDDEN_PROCESS)
            self._set_status("运行中 — 游戏已打开", ACCENT)
        except Exception as exc:
            self._log("!! 无法打开 %s: %r" % (player_name, exc))
            self._log("   请手动打开: %s" % self.game_url)
            self._set_status("运行中 (请手动打开游戏)", ACCENT)

    def stop(self):
        self._session += 1
        self._cancel.set()
        self.running = False
        self._admin_waiting = None
        self._set_status("正在停止…", "#e0b83a")
        ok = True
        for field, name in (("game", "游戏"), ("server", "服务器"), ("admin", "GM 后台")):
            proc = getattr(self, field)
            if self._terminate_owned(proc, name):
                setattr(self, field, None)
            else:
                ok = False
        if ok:
            self._reset_stopped()
            self._log("--- 已停止 ---")
        else:
            self._set_status("停止失败，请查看日志后重试", STOP)
            self.play_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.admin_btn.configure(state="normal")
            self._log("!! 部分进程仍在运行，未释放启动状态。")
        return ok


    def _reset_stopped(self):
        self.running = False
        self._admin_waiting = None
        self.play_btn.configure(state="normal" if self._can_start else "disabled")
        self.stop_btn.configure(state="normal" if self.admin and self.admin.poll() is None else "disabled")
        self.admin_btn.configure(state="normal")
        self.uin_box.configure(state="normal")
        self._set_status("已停止", "#888")


    def _poll_log(self):
        if self._closed:
            return
        try:
            # Bound each pass so heavy asset logs cannot starve button/close events.
            for _ in range(200):
                event = self.log_q.get_nowait()
                kind, session = event[:2]
                if session != self._session:
                    continue
                if kind == "__CALL__":
                    callback, args = event[2:]
                    try:
                        callback(*args)
                    except Exception as exc:
                        self._log("!! 启动器操作失败: %r" % exc)
                elif kind == "__SERVER_EXITED__":
                    if event[2] is self.server and self.running:
                        self._log("!! 服务器意外退出，正在清理游戏进程。")
                        self.stop()
                elif kind == "__ADMIN_EXITED__":
                    if event[2] is self.admin:
                        self._admin_failed(self.admin, session)
                elif kind == "__LOG__":
                    self._log(event[2])
        except queue.Empty:
            pass
        if not self._closed:
            self.root.after(150, self._poll_log)


    def on_close(self):
        if self.running and not messagebox.askokcancel("退出", "确定要停止服务器并关闭游戏吗？"):
            return
        if not self.stop():
            return
        self._closed = True
        self.root.destroy()



def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default="")   # 无自定义图标；忽略不支持的情况
    except Exception:
        pass
    app = Launcher(root)
    if getattr(sys, "_pvz_autoplay", False):
        root.after(500, app.start)
    root.mainloop()


if __name__ == "__main__":
    main()
