#!/usr/bin/env python3
"""
PvZOL Localhost Launcher (GUI，Windows / macOS)。

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
from pathlib import Path
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
DEFAULT_UIN = "001"
GAME_SKEY = "ABCDEFGHIJ"
CONFIG_PATH = os.path.join(HERE, "launcher_config.json")
START_TIMEOUT = 180.0
ADMIN_TIMEOUT = 30.0
LAUNCHER_LOG = os.path.join(HERE, "portable_launcher.log")


def window_work_area(window=None):
    """Use the primary work area, or the screen containing a parent window."""
    if IS_WINDOWS:
        try:
            import ctypes

            class Rect(ctypes.Structure):
                _fields_ = [(name, ctypes.c_long) for name in ("left", "top", "right", "bottom")]

            class MonitorInfo(ctypes.Structure):
                _fields_ = [("size", ctypes.c_ulong), ("monitor", Rect),
                            ("work", Rect), ("flags", ctypes.c_ulong)]

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            user32.MonitorFromWindow.restype = ctypes.c_void_p
            user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]
            user32.GetMonitorInfoW.restype = ctypes.c_int
            monitor = user32.MonitorFromWindow(window.winfo_id() if window else None,
                                               2 if window else 1)
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return info.work.left, info.work.top, info.work.right, info.work.bottom
        except (ImportError, AttributeError, OSError, tk.TclError):
            pass
    return None


def center_window(window, parent=None, size=None):
    """Place a finished layout centrally before showing the window."""
    window.update_idletasks()
    width, height = size or (window.winfo_reqwidth(), window.winfo_reqheight())
    left, top, right, bottom = window_work_area(parent) or (
        0, 0, window.winfo_screenwidth(), window.winfo_screenheight())
    x = left + max(0, (right - left - width) // 2)
    y = top + max(0, (bottom - top - height) // 2)
    # A leading '+' also preserves absolute coordinates on screens to the
    # left/top of the primary screen (Tk accepts '+-1800', for example).
    dimensions = "%dx%d" % (width, height) if size else ""
    window.geometry("%s+%d+%d" % (dimensions, x, y))


def select_avatar_image(parent):
    # Use Tk's bundled native dialog directly; portable builds need no new
    # Python tkinter.filedialog module or runtime repackaging.
    return str(parent.tk.call("tk_getOpenFile", "-parent", str(parent), "-title", "Choose Avatar Image",
                              "-filetypes", (("Avatar Image", ("*.png", "*.jpg", "*.jpeg")),
                                             ("PNG Image", "*.png"), ("JPG Image", ("*.jpg", "*.jpeg")))))


def valid_uin(value):
    """The account must fit the game's unsigned 32-bit transport field."""
    return (isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value) is not None
            and 0 < int(value) <= 4294967295)


def valid_player_uin(value):
    """Reserved arena/guild NPC identities cannot be selected as login accounts."""
    return valid_uin(value) and not 900000 <= int(value) < 1000000


def refresh_editor_fields(variables, baseline, latest):
    for key, variable in variables.items():
        if variable.get().strip() == str(baseline[key]):
            variable.set(str(latest[key]))
            baseline[key] = latest[key]


PRIVILEGE_DEPENDENCIES = {"vip_level": ("vip_card_id", "vip_endtime"),
                          "td_privilege": ("td_privilege_endtime",)}


def editor_privilege_levels(player, now=None):
    now = int(time.time()) if now is None else now
    vip = int(player.get("vip_level") or 0)
    if int(player.get("vip_card_id") or 0) and int(player.get("vip_endtime") or 0) <= now:
        vip = 0
    td = int(player.get("td_privilege") or 0)
    end = int(player.get("td_privilege_endtime") or 0)
    return {"vip_level": vip, "td_privilege": td if end == 0 or end > now else 0}


def editor_privilege_fields(values, shown_baseline, dirty):
    return {key: value for key, value in values.items()
            if value != shown_baseline[key] or key in dirty}


def refresh_editor_privileges(variables, options, baseline, shown_baseline, dirty, latest, committed=()):
    levels = editor_privilege_levels(latest)
    for key, variable in variables.items():
        if key in committed or (key not in dirty and options[key].index(variable.get()) == shown_baseline[key]):
            baseline[key] = latest.get(key, 0)
            for dependency in PRIVILEGE_DEPENDENCIES[key]:
                baseline[dependency] = latest.get(dependency, 0)
            shown_baseline[key] = levels[key]
            variable.set(options[key][levels[key]])
            dirty.discard(key)


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
        if cfg.get("last_uin") != "" and not valid_player_uin(cfg.get("last_uin")):
            cfg["last_uin"] = DEFAULT_UIN
        recent = cfg.get("recent", [])
        cfg["recent"] = list(dict.fromkeys(u for u in recent if valid_player_uin(u)))[:8] if isinstance(recent, list) else []
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
ACCOUNT_FONT = ("Microsoft YaHei UI", 11, "normal")


def themed_dialog(parent, title, message, detail="", confirm=False):
    """Use the launcher's garden theme for account actions and confirmation."""
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title)
    win.configure(bg=BG)
    win.resizable(False, False)
    win.transient(parent)
    result = [False]

    def finish(accepted=False):
        if not win.winfo_exists():
            return "break"
        result[0] = accepted
        win.grab_release()
        win.destroy()
        return "break"

    body = tk.Frame(win, bg=BG)
    body.pack(fill="both", expand=True, padx=22, pady=20)
    tk.Label(body, text=title, bg=BG, fg=FG, anchor="w",
             font=(ACCOUNT_FONT[0], 14, "normal")).pack(fill="x", pady=(0, 14))
    card = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground="#3b522b")
    card.pack(fill="x", pady=(0, 12))
    tk.Label(card, text=message, bg=PANEL, fg=FG, anchor="w", justify="left",
             wraplength=390, font=ACCOUNT_FONT).pack(fill="x", padx=14, pady=12)
    if detail:
        tk.Label(body, text=detail, bg=BG, fg=MUTED, anchor="w", justify="left",
                 wraplength=420, font=(ACCOUNT_FONT[0], 10, "normal")).pack(fill="x", pady=(0, 18))
    buttons = tk.Frame(body, bg=BG)
    buttons.pack(fill="x", pady=(4, 0))
    if confirm:
        cancel = tk.Button(buttons, text="Cancel", command=lambda: finish(False),
                           bg=PANEL, fg=FG, activebackground="#33481f", activeforeground=FG,
                           font=ACCOUNT_FONT, relief="flat", cursor="hand2", bd=0, padx=18, pady=8)
        cancel.pack(side="left", fill="x", expand=True, padx=(0, 6))
        accept = tk.Button(buttons, text="Confirm Deletion", command=lambda: finish(True),
                           bg=STOP, fg="#ffffff", activebackground="#d5643c", activeforeground="#ffffff",
                           font=ACCOUNT_FONT, relief="flat", cursor="hand2", bd=0, padx=18, pady=8)
        accept.pack(side="left", fill="x", expand=True, padx=(6, 0))
        cancel.bind("<Return>", lambda event: finish(False))
        accept.bind("<Return>", lambda event: finish(True))
        default_button = cancel
    else:
        default_button = tk.Button(buttons, text="OK", command=lambda: finish(True),
                                   bg=ACCENT, fg="#12220a", activebackground=ACCENT_HOVER,
                                   activeforeground="#12220a", font=ACCOUNT_FONT,
                                   relief="flat", cursor="hand2", bd=0, padx=18, pady=8)
        default_button.pack(fill="x")
        win.bind("<Return>", lambda event: finish(True))
    win.bind("<Escape>", lambda event: finish(False))
    win.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    center_window(win, parent=parent)
    win.deiconify()
    win.grab_set()
    default_button.focus_set()
    parent.wait_window(win)
    return result[0]


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
        r.title("Plants vs. Zombies Online — Launcher")
        r.configure(bg=BG)
        r.geometry("580x520")
        r.minsize(520, 460)

        header = tk.Frame(r, bg=BG)
        header.pack(fill="x", padx=20, pady=(18, 6))
        tk.Label(header, text="Plants vs. Zombies Online", bg=BG, fg=FG,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(header, text="Private Server Launcher", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w")

        status = tk.Frame(r, bg=PANEL, highlightthickness=0)
        status.pack(fill="x", padx=20, pady=8)
        self.dot = tk.Canvas(status, width=16, height=16, bg=PANEL, highlightthickness=0)
        self.dot.pack(side="left", padx=(12, 8), pady=10)
        self._dot_id = self.dot.create_oval(2, 2, 14, 14, fill="#888", outline="")
        self.status_lbl = tk.Label(status, text="Stopped", bg=PANEL, fg=FG,
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
        tk.Label(acct, text="Account", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        self.uin_var = tk.StringVar(value=cfg.get("last_uin", DEFAULT_UIN))
        account_row = tk.Frame(acct, bg=BG)
        account_row.pack(fill="x", pady=(3, 0))
        self.uin_box = ttk.Combobox(account_row, textvariable=self.uin_var, values=cfg.get("recent", []),
                                    style="Dark.TCombobox", font=("Segoe UI", 11))
        self.uin_box.pack(side="left", fill="x", expand=True)
        self.create_btn = tk.Button(account_row, text="New Account", command=self.create_account,
                                    bg=ACCENT, fg="#12220a", activebackground=ACCENT_HOVER,
                                    relief="flat", cursor="hand2", font=ACCOUNT_FONT,
                                    padx=12, pady=6, bd=0)
        self.create_btn.pack(side="left", padx=(8, 0))
        self.delete_btn = tk.Button(account_row, text="Delete Account", command=self.delete_account,
                                    bg=STOP, fg="#ffffff", activebackground="#d5643c",
                                    relief="flat", cursor="hand2", font=ACCOUNT_FONT,
                                    padx=12, pady=6, bd=0)
        self.delete_btn.pack(side="left", padx=(8, 0))

        btns = tk.Frame(r, bg=BG)
        btns.pack(fill="x", padx=20, pady=6)
        self.play_btn = tk.Button(btns, text="Play", command=self.start,
                                  bg=ACCENT, fg="#12220a", activebackground=ACCENT_HOVER,
                                  activeforeground="#12220a", relief="flat", cursor="hand2",
                                  font=("Segoe UI", 13, "bold"), padx=18, pady=10, bd=0)
        self.play_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.stop_btn = tk.Button(btns, text="Stop", command=self.stop,
                                  bg=STOP, fg="#ffffff", activebackground="#d5643c",
                                  activeforeground="#ffffff", relief="flat", cursor="hand2",
                                  font=("Segoe UI", 13, "bold"), padx=18, pady=10, bd=0,
                                  state="disabled")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(6, 0))

        # 辅助功能：网页 GM 后台 + 旧版存档编辑器。
        tools = tk.Frame(r, bg=BG)
        tools.pack(fill="x", padx=20, pady=(0, 2))
        self.admin_btn = tk.Button(tools, text="Open Admin Panel", command=self.open_admin,
                                   bg=PANEL, fg=FG, activebackground="#33481f", activeforeground=FG,
                                   relief="flat", cursor="hand2", font=("Segoe UI", 10, "bold"),
                                   padx=10, pady=6, bd=0)
        self.admin_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.profile_btn = tk.Button(tools, text="Edit Profile", command=self.open_profile_editor,
                                     bg=PANEL, fg=FG, activebackground="#33481f", activeforeground=FG,
                                     relief="flat", cursor="hand2", font=("Segoe UI", 10),
                                     padx=10, pady=6, bd=0)
        self.profile_btn.pack(side="left", expand=True, fill="x", padx=5)
        self.edit_btn = tk.Button(tools, text="Edit Save", command=self.open_save_editor,
                                  bg=PANEL, fg=FG, activebackground="#33481f", activeforeground=FG,
                                  relief="flat", cursor="hand2", font=("Segoe UI", 10),
                                  padx=10, pady=6, bd=0)
        self.edit_btn.pack(side="left", expand=True, fill="x", padx=(5, 0))

        # 详情 / 日志
        toggle = tk.Frame(r, bg=BG)
        toggle.pack(fill="x", padx=20, pady=(10, 0))
        self.log_shown = tk.BooleanVar(value=False)
        self.toggle_btn = tk.Label(toggle, text="Server Log", bg=BG, fg=MUTED,
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
        tk.Label(foot, text="Running locally   ·   Web :%d   Admin :%d   Game :%d   Policy :%d"
                 % (WEB_PORT, ADMIN_PORT, GAME_PORT, POLICY_PORT),
                 bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        center_window(r, size=(580, 520))

    # ---------------- 创建账号 ----------------
    def _create_account(self, raw_uin):
        uin = raw_uin.strip()
        if not valid_uin(uin):
            raise ValueError("Enter a valid numeric account ID (1–4294967295).")
        if not valid_player_uin(uin):
            raise ValueError("This ID is reserved for a system bot. Choose another account ID.")
        if SERVER_DIR not in sys.path:
            sys.path.insert(0, SERVER_DIR)
        import player_store as store
        try:
            store.init_db()
            store.create_new_player(uin)
        finally:
            store.close_thread_db()
        cfg = load_config()
        recent = [uin] + [u for u in cfg.get("recent", []) if u != uin]
        cfg.update({"last_uin": uin, "recent": recent[:8]})
        if not save_config(cfg):
            self._log("!! Account created, but recent accounts could not be saved. Check directory write permissions.")
        self.uin_var.set(uin)
        self.uin_box.configure(values=recent[:8])
        self._log("Account %s created. Click Play to start the tutorial." % uin)
        return uin

    def create_account(self):
        if self.running or port_open(GAME_PORT):
            messagebox.showwarning("New Account", "Stop the game services before creating or switching accounts.")
            return
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title("New Account")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)
        body = tk.Frame(win, bg=BG)
        body.pack(padx=22, pady=18)
        tk.Label(body, text="Enter a numeric account ID (1–4294967295)", bg=BG, fg=FG,
                 font=("Segoe UI", 10)).pack(anchor="w")
        tk.Label(body, text="Each account has its own save; account IDs cannot be reused.", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 10))
        entry = tk.Entry(body, bg=PANEL, fg=FG, insertbackground=FG,
                         relief="flat", font=("Segoe UI", 12), width=34)
        entry.pack(fill="x", ipady=5)
        error = tk.StringVar()
        tk.Label(body, textvariable=error, bg=BG, fg="#f0a080", wraplength=350,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 4))

        def submit(event=None):
            try:
                uin = self._create_account(entry.get())
            except Exception as exc:
                error.set(str(exc))
                return
            win.destroy()
            messagebox.showinfo("New Account", "Account %s was created and selected.\nClick Play to enter the game, set your character name, and complete the tutorial." % uin)

        tk.Button(body, text="Create & Select", command=submit, bg=ACCENT, fg="#12220a",
                  activebackground=ACCENT_HOVER, relief="flat", cursor="hand2",
                  font=("Segoe UI", 10, "bold"), pady=7).pack(fill="x", pady=(4, 0))
        entry.bind("<Return>", submit)
        win.bind("<Escape>", lambda event: win.destroy())
        center_window(win, parent=self.root)
        win.deiconify()
        win.grab_set()
        entry.focus_set()

    # ---------------- 删除账号 ----------------
    def _delete_account(self, raw_uin):
        if self.running or port_open(GAME_PORT) or port_open(ADMIN_PORT):
            raise ValueError("Click Stop first to close the game services and admin panel before deleting an account.")
        uin = raw_uin.strip()
        if not valid_uin(uin):
            raise ValueError("Select a valid numeric account ID (1–4294967295).")
        if SERVER_DIR not in sys.path:
            sys.path.insert(0, SERVER_DIR)
        import account_management
        result = account_management.delete_account(uin)
        for window in self.root.winfo_children():
            if getattr(window, "_account_uin", None) == int(uin):
                window.destroy()
        cfg = load_config()
        recent = [value for value in cfg.get("recent", []) if int(value) != int(uin)]
        selected = recent[0] if recent else ""
        cfg.update({"last_uin": selected, "recent": recent})
        result["config_saved"] = save_config(cfg)
        if not result["config_saved"]:
            self._log("!! Account deleted, but the recent-account list could not be saved. Check directory write permissions.")
        self.uin_var.set(selected)
        self.uin_box.configure(values=recent)
        self._log("Account %s deleted." % uin)
        return result

    def delete_account(self):
        if self.running or port_open(GAME_PORT) or port_open(ADMIN_PORT):
            themed_dialog(self.root, "Delete Account", "Click Stop first to close the game services and admin panel before deleting an account.")
            return
        uin = self.uin_var.get().strip()
        if not valid_uin(uin):
            themed_dialog(self.root, "Delete Account", "Select a valid account to delete first.")
            return
        try:
            if SERVER_DIR not in sys.path:
                sys.path.insert(0, SERVER_DIR)
            import account_management
            player = account_management.get_account(uin)
            if not player:
                themed_dialog(self.root, "Delete Account", "Account %s has no save data and cannot be deleted." % uin)
                return
        except Exception as exc:
            themed_dialog(self.root, "Delete Account", "Could not read account:\n%s" % exc)
            return
        confirmed = themed_dialog(
            self.root, "Confirm Account Deletion", "Account: %s\nCharacter: %s" % (uin, player["name"]),
            detail="Delete this account?\nAll game progress, inventory, plant cards, and related data for this account will be deleted.\nThis cannot be undone.",
            confirm=True)
        if not confirmed:
            return
        try:
            result = self._delete_account(uin)
        except Exception as exc:
            themed_dialog(self.root, "Delete Account", "Deletion failed:\n%s" % exc)
            return
        notice = "Account %s deleted." % uin
        if not result["config_saved"]:
            notice += "\n\nThe recent-account list could not be saved. Check directory write permissions before reopening."
        themed_dialog(self.root, "Delete Account", notice)

    # ---------------- GM 网页后台 ----------------
    def open_admin(self):
        if self._closed or self._admin_waiting:
            return
        if admin_ready():
            webbrowser.open(ADMIN_URL)
            self._log("Opened GM admin panel: %s" % ADMIN_URL)
            return
        if self.running and self.server:
            self._begin_admin_wait(self.server, self._session)
            return
        if self.admin and self.admin.poll() is None:
            self._begin_admin_wait(self.admin, self._session)
            return
        if port_open(ADMIN_PORT):
            messagebox.showerror("Unable to Open Admin Panel", "Port %d is already in use. Close the other process and try again." % ADMIN_PORT)
            return
        admin_script = os.path.join(SERVER_DIR, "admin_server.py")
        if not os.path.isfile(admin_script):
            messagebox.showerror("Unable to Open Admin Panel", "Missing file:\n%s" % admin_script)
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
            messagebox.showerror("Unable to Start Admin Panel", repr(exc))
            return
        self.admin = proc
        self.stop_btn.configure(state="normal")
        self._log("--- Starting the GM admin panel separately ---")
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
        self._log("Opened GM admin panel: %s" % ADMIN_URL)

    def _admin_failed(self, proc, session):
        if self._closed or session != self._session or proc not in (self.admin, self.server):
            return
        self._admin_waiting = None
        self.admin_btn.configure(state="normal")
        self._log("!! The admin panel did not become ready. Check the logs or port %d." % ADMIN_PORT)
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
            return False, "Stop the game services before editing the save."
        try:
            p = subprocess.run(python_command(os.path.join(SERVER_DIR, "gm.py"), *args),
                               cwd=SERVER_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                               **HIDDEN_PROCESS)
            return (p.returncode == 0, (p.stdout or "") + (p.stderr or ""))
        except Exception as exc:
            return (False, "Error: %r" % exc)

    def open_profile_editor(self):
        if self.running or port_open(GAME_PORT):
            messagebox.showwarning("Edit Profile", "Stop the game services before editing your profile.")
            return
        uin = self.uin_var.get().strip()
        if not valid_uin(uin):
            messagebox.showwarning("Edit Profile", "Enter a valid numeric account ID (1–4294967295).")
            return
        if not valid_player_uin(uin):
            messagebox.showwarning("Edit Profile", "This ID belongs to a system bot. Select a player account.")
            return
        try:
            if SERVER_DIR not in sys.path:
                sys.path.insert(0, SERVER_DIR)
            import player_store as store
            from profile_ui import open_profile_window, save_profile_draft
            store.init_db()
            player, _ = store.get_or_create_player(int(uin))
            theme = {key: globals()[key] for key in
                     ("BG", "PANEL", "LOGBG", "FG", "MUTED", "ACCENT", "ACCENT_HOVER", "STOP")}
            return open_profile_window(self.root, player, HERE, theme, select_avatar_image, center_window,
                lambda request, baseline: save_profile_draft(self._run_gm, uin, request, baseline))
        except (ImportError, RuntimeError, OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Edit Profile", "Could not open the profile page:\n%s" % exc)
            return

    def open_save_editor(self):
        if self.running or port_open(GAME_PORT):
            messagebox.showwarning("Save Editor", "Stop the game services before opening the save editor.")
            return
        uin = self.uin_var.get().strip()
        if not valid_uin(uin):
            messagebox.showwarning("Save Editor", "Enter a valid numeric account ID (1–4294967295).")
            return
        if not valid_player_uin(uin):
            messagebox.showwarning("Save Editor", "This ID belongs to a system bot. Select a player account.")
            return
        # 直接读取当前存档 (player_store 的数据库路径固定在 PvZ-Server-PoC/players.db)
        try:
            if SERVER_DIR not in sys.path:
                sys.path.insert(0, SERVER_DIR)
            import player_store as store
            store.init_db()   # 如果是全新安装，创建 players.db + 表结构
            player, _ = store.get_or_create_player(int(uin))
        except Exception as exc:
            messagebox.showerror("Save Editor", "Could not open the save:\n%r" % exc)
            return

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title("Save Editor — Account %s" % uin)
        win._account_uin = int(uin)
        win.configure(bg=BG)
        editor_size = (560, min(820, max(540, win.winfo_screenheight() - 140)))
        win.geometry("%dx%d" % editor_size)
        win.minsize(520, 540)
        win.transient(self.root)

        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        body = tk.Frame(canvas, bg=BG)
        content = canvas.create_window(0, 0, window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(content, width=e.width))
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units"))

        tk.Label(body, text="Save Editor", bg=BG, fg=FG,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=16, pady=(14, 0))
        note = ("The game is running — stop it first, edit the save, then click Play to apply changes."
                if self.running else "Account %s · Changes take effect the next time you click Play." % uin)
        tk.Label(body, text=note, bg=BG, fg=(STOP if self.running else MUTED),
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(2, 8))

        status = tk.Label(body, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=430,
                          justify="left")

        def gm(args, ok_msg):
            ok, out = self._run_gm(args)
            status.configure(text=(ok_msg if ok else "Failed — " + out.strip().splitlines()[-1:][0]
                                   if out.strip() else "Failed"), fg=(ACCENT if ok else STOP))
            if ok:
                with store._db() as con:
                    latest = dict(con.execute("SELECT * FROM players WHERE uin=?", (int(uin),)).fetchone())
                refresh_editor_fields(ed_vars, baseline, latest)
                refresh_editor_privileges({"vip_level": vip_var, "td_privilege": td_var},
                    {"vip_level": VIP_OPTS, "td_privilege": TD_OPTS}, baseline, shown_baseline, privilege_dirty, latest)
                player.update(latest)
            return ok

        # --- 个人资料 -------------------------------------------------------
        from profile_ui import ProfileEditorPanel, save_profile_draft
        theme = {key: globals()[key] for key in
                 ("BG", "PANEL", "LOGBG", "FG", "MUTED", "ACCENT", "ACCENT_HOVER", "STOP")}

        def save_profile_only(request, snapshot):
            ok, result = save_profile_draft(self._run_gm, uin, request, snapshot)
            if ok:
                player.update(result)
            return ok, result

        profile_panel = ProfileEditorPanel(body, player, HERE, theme, select_avatar_image,
                                          center_window, on_save=save_profile_only)
        profile_panel.pack(fill="x", padx=16, pady=(4, 8))
        status.pack(anchor="w", padx=16, pady=(0, 4))

        # --- 货币 / 属性字段 -------------------------------------------------
        FIELDS = [("Level", "level"), ("Coins", "coin"), ("Sun", "sun"), ("Blue Diamonds", "present_ticket"),
                  ("Gold Diamonds", "ticket"), ("Fame", "fame"), ("Stamina", "energy"),
                  ("Friend Points", "friend_point")]
        grid = tk.Frame(body, bg=PANEL)
        grid.pack(fill="x", padx=16, pady=4)
        ed_vars = {}
        baseline = {key: player.get(key, 0) for _label, key in FIELDS}
        baseline.update(vip_level=player.get("vip_level", 0), td_privilege=player.get("td_privilege", 0))
        baseline.update({key: player.get(key, 0) for deps in PRIVILEGE_DEPENDENCIES.values() for key in deps})
        shown_baseline = editor_privilege_levels(player)
        privilege_dirty = set()
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
        vipf = tk.Frame(body, bg=BG)
        vipf.pack(fill="x", padx=16, pady=(8, 2))
        tk.Label(vipf, text="VIP Level", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        VIP_OPTS = ["Off", "VIP1", "VIP2", "VIP3"]
        cur_vip = shown_baseline["vip_level"]
        vip_var = tk.StringVar(value=VIP_OPTS[cur_vip] if 0 <= cur_vip <= 3 else "Off")
        vip_box = ttk.Combobox(vipf, textvariable=vip_var, values=VIP_OPTS, state="readonly",
                     style="Dark.TCombobox", width=8)
        vip_box.pack(side="left", padx=8)
        vip_box.bind("<<ComboboxSelected>>", lambda _event: privilege_dirty.add("vip_level"))
        # 塔防 特权 (塔防特权) — 同一行右侧
        tk.Label(vipf, text="Tower Defense Perk", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(24, 0))
        TD_OPTS = ["Off", "On"]
        cur_td = shown_baseline["td_privilege"]
        td_var = tk.StringVar(value=TD_OPTS[cur_td] if 0 <= cur_td <= 1 else "Off")
        td_box = ttk.Combobox(vipf, textvariable=td_var, values=TD_OPTS, state="readonly",
                     style="Dark.TCombobox", width=6)
        td_box.pack(side="left", padx=8)
        td_box.bind("<<ComboboxSelected>>", lambda _event: privilege_dirty.add("td_privilege"))

        def apply_main():
            from gm import validate_player_fields
            numeric = {}
            for _label, key in FIELDS:
                val = ed_vars[key].get().strip()
                if re.fullmatch(r"[+-]?[0-9]+", val) is None:
                    status.configure(text="%s must be an integer." % _label, fg=STOP)
                    return
                numeric[key] = int(val)
            try:
                validate_player_fields(numeric)
            except ValueError as exc:
                status.configure(text=str(exc), fg=STOP)
                return
            fields = {key: value for key, value in numeric.items() if value != baseline[key]}
            fields.update(editor_privilege_fields(
                {"vip_level": VIP_OPTS.index(vip_var.get()), "td_privilege": TD_OPTS.index(td_var.get())},
                shown_baseline, privilege_dirty))
            try:
                profile_request = profile_panel.request()
            except ValueError as exc:
                status.configure(text=str(exc), fg=STOP)
                return
            expected = {key: baseline[key] for key in fields}
            for key in fields:
                for dependency in PRIVILEGE_DEPENDENCIES.get(key, ()):
                    expected[dependency] = baseline[dependency]
            request = {"fields": fields, "expected": expected,
                       "profile": profile_request, "profile_expected": dict(profile_panel.baseline)}
            ok, out = self._run_gm([uin, "editor", json.dumps(request, ensure_ascii=False)])
            if not ok:
                status.configure(text="Failed to save the game data: " + out.strip(), fg=STOP)
                return
            latest = json.loads(out)
            for key in fields:
                baseline[key] = latest[key]
                if key in ed_vars:
                    ed_vars[key].set(str(latest[key]))
            refresh_editor_fields(ed_vars, baseline, latest)
            refresh_editor_privileges({"vip_level": vip_var, "td_privilege": td_var},
                {"vip_level": VIP_OPTS, "td_privilege": TD_OPTS}, baseline, shown_baseline, privilege_dirty, latest,
                committed=fields)
            player.update(latest)
            profile_panel.reset_from_player(latest)
            status.configure(text="Saved. Profile and settings will sync the next time you launch the game.", fg=ACCENT)

        tk.Button(body, text="Apply Changes", command=apply_main, bg=ACCENT, fg="#12220a",
                  activebackground=ACCENT_HOVER, activeforeground="#12220a", relief="flat",
                  cursor="hand2", font=("Segoe UI", 11, "bold"), padx=12, pady=8, bd=0).pack(
            fill="x", padx=16, pady=(10, 6))

        # --- 快捷操作 (每个按钮独立应用) -------------------------
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=16, pady=4)

        def action_row(label, fields, on_go):
            fr = tk.Frame(body, bg=BG)
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
            tk.Button(fr, text="Run", command=lambda: on_go([e.get().strip() for e in entries]),
                      bg=PANEL, fg=FG, activebackground="#33481f", activeforeground=FG, relief="flat",
                      cursor="hand2", font=("Segoe UI", 9), padx=10, pady=3, bd=0).pack(side="right")

        def give_item(vals):
            iid, ct = vals
            if iid.isdigit() and ct.lstrip("-").isdigit():
                gm([uin, "item", iid, ct], "Granted item %s x %s" % (iid, ct))
        action_row("Grant Item", [("ID", 8, ""), ("Quantity", 6, "1")], give_item)

        def clear_levels(vals):
            st, lv = vals
            if st.isdigit() and lv.isdigit():
                gm([uin, "passto", st, lv], "Cleared through %s-%s (unlocking gates and plants)" % (st, lv))
        action_row("Clear to Stage", [("World", 5, "1"), ("Stage", 5, "")], clear_levels)

        def level_cards(vals):
            lv = vals[0]
            if lv.isdigit():
                gm([uin, "cardlevel", lv], "Set all plant cards to level %s (limited by the account level cap)" % lv)
        action_row("Set All Plant Card Levels", [("Level", 5, "")], level_cards)

        # --- 钻石卡 (拥有钻石卡) -------------------------------------------
        # 玩家拥有的 Dto_DiamondInfo — 登录时通过 Cmd_Vip_SyncVipInfo_SC 推送，
        # ModuleBlueDiamond.swf 据此渲染卡片。flag=1 表示拥有；blue/yellow/xinyue = 钻石等级
        # (0 = 未获得该钻石)。购买 (flag=1) 会消耗 20 钻石。
        tk.Label(body, text="Diamond Membership", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(10, 0))

        def apply_diamond(vals):
            f, b, y, x = vals
            if f.isdigit() and b.isdigit() and y.isdigit() and x.isdigit():
                gm([uin, "diamond", f, b, y, x], "Diamond membership updated — restart to apply changes")
        action_row("Diamond Membership Active",
                   [("Flag", 4, str(player.get("diamond_flag", 1) or 0)),
                    ("Blue Diamonds", 4, str(player.get("diamond_blue_lvl", 0) or 0)),
                    ("Yellow Diamond", 4, str(player.get("diamond_yellow_lvl", 0) or 0)),
                    ("Friend Points", 4, str(player.get("diamond_xinyue_lvl", 0) or 0))],
                   apply_diamond)

        # --- 好友 (好友 + 好友卡) ---------------------------------------------
        # 每个好友会显示在好友列表中，附带 TD "好友卡" (helpPlant =
        # Dto_CardFullInfo 由所选 tdPlantId 构建) 以及每个好友独立的钻石卡。
        tk.Label(body, text="Friends (Friend Cards)", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(10, 0))

        fr_box = tk.Frame(body, bg=PANEL)
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
            (1200, "1200  Peashooter"),
            (1210, "1210  Repeater"),
            (1300, "1300  Small Wall-nut"),
            (1100, "1100  Sunflower"),
            (1500, "1500  Potato Mine"),
            (1600, "1600  Boomerang"),
            (1400, "1400  Cabbage-pult"),
            (1110, "1110  Twin Sunflower"),
            (1800, "1800  Bok Choy"),
            (1700, "1700  Iceberg Lettuce"),
            (1900, "1900  Grave Buster"),
            (2000, "2000  Magic Vine"),
            (3100, "3100  Bamboo Dragon"),
            (3300, "3300  Dandelion"),
            (3200, "3200  Fire Carrot"),
            (3400, "3400  Mr. Winter Melon"),
            (3500, "3500  Haystack"),
            (3600, "3600  Venus Flytrap"),
            (3700, "3700  Squash"),
            (3800, "3800  Bowling Nut"),
            (3850, "3850  Exploding Bowling Nut"),
            (3900, "3900  Snapdragon"),
            (4000, "4000  Mini-game Carrot"),
            (4100, "4100  Spikeweed"),
            (4200, "4200  Spikerock"),
            (4300, "4300  Corn"),
            (4400, "4400  Spring Bean"),
            (4500, "4500  Threepeater"),
            (4600, "4600  Coconut Cannon"),
            (4700, "4700  Pomegranate"),
            (4800, "4800  Daffodil"),
            (4900, "4900  Durian"),
            (5000, "5000  Pineapple-pult"),
            (5100, "5100  Reset"),
            (5200, "5200  Pit Filler"),
            (5300, "5300  Snow Pea"),
            (5400, "5400  Twin Peashooter"),
            (5500, "5500  Pea Pod"),
            (5600, "5600  Tall-nut"),
            (5700, "5700  Cherry Bomb"),
            (5900, "5900  Chili Bean"),
            (6000, "6000  Torchwood"),
            (6100, "6100  Electric Grass"),
            (6200, "6200  Starfruit"),
            (6300, "6300  Laser Bean"),
            (6400, "6400  Citron"),
            (6500, "6500  Holo-nut"),
            (6600, "6600  Prism Grass"),
            (6700, "6700  Jalapeño"),
            (6800, "6800  Ice Pea"),
            (6900, "6900  Winter Melon"),
            (7100, "7100  Blover"),
            (7200, "7200  EMPeach"),
            (7400, "7400  Nitrifying Mushroom"),
            (7500, "7500  Chomper"),
            (7600, "7600  Tile Turnip"),
            (7700, "7700  Melon-pult"),
            (7800, "7800  Cattail"),
            (7900, "7900  Reishi Mushroom"),
            (8000, "8000  Banana"),
            (8100, "8100  Oxygen Algae"),
            (8200, "8200  Garlic"),
            (8300, "8300  Electric Pine"),
            (8400, "8400  Bubble Flower"),
            (8500, "8500  Sea Starfruit"),
            (8600, "8600  Burrowing Lychee"),
            (8700, "8700  Sea Anemone"),
            (8800, "8800  Water Mine Fruit"),
            (8900, "8900  Rose Swordsman"),
            (9000, "9000  Clivia"),
            (9100, "9100  Resin Shooter"),
            (9200, "9200  Acid Lemon"),
            (9300, "9300  Magic Mushroom"),
            (9400, "9400  Fire Peashooter"),
            (9500, "9500  Ghost Pepper"),
            (9600, "9600  Avocado"),
            (9700, "9700  Hurricane Kale"),
            (9800, "9800  Lamp Flower"),
            (9900, "9900  Oak Archer"),
            (1120, "1120  Thorny Flower"),
            (1130, "1130  Sweet Pepper-pult"),
            (1140, "1140  Pumpkin Witch"),
            (1150, "1150  Kung Fu Radish"),
            (1160, "1160  Toadstool"),
            (1170, "1170  Bellflower"),
            (1180, "1180  Red Stinger"),
            (1220, "1220  Electric Blueberry"),
            (1190, "1190  Akee Tree"),
            (1230, "1230  Agave"),
            (1240, "1240  Moonflower"),
            (1250, "1250  Asparagus Jet"),
        ]
        _PLANT_DISPLAY = [d for _pid, d in _TD_PLANTS]   # 显示名称列表
        _PLANT_BY_DISPLAY = {d: _pid for _pid, d in _TD_PLANTS}

        fr_pid_var = tk.StringVar()
        fr_pid_idx = tk.IntVar(value=0)   # _TD_PLANTS 中的索引 (默认 1100 向日葵)

        fr_row1 = tk.Frame(fr_box, bg=PANEL)
        fr_row1.pack(fill="x", pady=(2, 1))
        fr_entry(fr_row1, "Character ID", fr_rid, 12)
        fr_entry(fr_row1, "Name", fr_name, 12)
        fr_entry(fr_row1, "Level", fr_plv, 4)

        fr_row1b = tk.Frame(fr_box, bg=PANEL)
        fr_row1b.pack(fill="x", pady=(1, 1))
        tk.Label(fr_row1b, text="Plant", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(4, 1))
        ttk.Combobox(fr_row1b, textvariable=fr_pid_var, values=_PLANT_DISPLAY,
                     state="readonly", style="Dark.TCombobox", width=22).pack(side="left", padx=(0, 6))

        fr_row2 = tk.Frame(fr_box, bg=PANEL)
        fr_row2.pack(fill="x", pady=(1, 2))
        fr_entry(fr_row2, "Flag", fr_df, 4)
        fr_entry(fr_row2, "Blue Diamonds", fr_db, 4)
        fr_entry(fr_row2, "Yellow Diamond", fr_dy, 4)
        fr_entry(fr_row2, "Friend Points", fr_dx, 4)

        fr_list = tk.Listbox(body, bg=LOGBG, fg=FG, selectbackground=ACCENT,
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

        fr_row3 = tk.Frame(body, bg=BG)
        fr_row3.pack(fill="x", padx=16, pady=(2, 4))

        def add_friend():
            rid = fr_rid.get().strip(); name = fr_name.get().strip()
            pid = str(_PLANT_BY_DISPLAY.get(fr_pid_var.get().strip(), 0))
            plv = fr_plv.get().strip()
            df = fr_df.get().strip() or "1"; db = fr_db.get().strip() or "0"
            dy = fr_dy.get().strip() or "0"; dx = fr_dx.get().strip() or "0"
            if not (rid.isdigit() and pid.isdigit() and plv.isdigit() and name):
                status.configure(text="Character ID, name, plant, and level are required", fg=STOP); return
            if not (df.isdigit() and db.isdigit() and dy.isdigit() and dx.isdigit()):
                status.configure(text="Membership fields must be numeric", fg=STOP); return
            if gm([uin, "addfriend", rid, name, pid, plv, df, db, dy, dx],
                  "Friend %s added — restart to apply" % name):
                refresh_friends()

        def del_friend():
            sel = fr_list.curselection()
            if not sel:
                status.configure(text="Select a friend first", fg=STOP); return
            rid = fr_list.get(sel[0]).split(" | ", 1)[0]
            if rid.isdigit() and gm([uin, "delfriend", rid],
                                     "Friend %s removed — restart to apply" % rid):
                refresh_friends()

        tk.Button(fr_row3, text="Add / Update", command=add_friend, bg=ACCENT, fg="#12220a",
                  activebackground=ACCENT_HOVER, activeforeground="#12220a", relief="flat",
                  cursor="hand2", font=("Segoe UI", 9, "bold"), padx=10, pady=4, bd=0).pack(side="left")
        tk.Button(fr_row3, text="Remove", command=del_friend, bg=PANEL, fg=FG,
                  activebackground="#33481f", activeforeground=FG, relief="flat", cursor="hand2",
                  font=("Segoe UI", 9), padx=10, pady=4, bd=0).pack(side="left", padx=6)
        tk.Button(fr_row3, text="Refresh", command=refresh_friends, bg=PANEL, fg=FG,
                  activebackground="#33481f", activeforeground=FG, relief="flat", cursor="hand2",
                  font=("Segoe UI", 9), padx=10, pady=4, bd=0).pack(side="left")

        tk.Label(body, text="Tip: Level up first — account level caps plant card levels and unlocks town buildings.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=470, justify="left").pack(
            anchor="w", padx=16, pady=(8, 2))

        center_window(win, parent=self.root, size=editor_size)
        win.deiconify()

    def _toggle_log(self):
        if self.log_shown.get():
            self.log_frame.pack_forget()
            self.toggle_btn.configure(text="Server Log")
        else:
            self.log_frame.pack(fill="both", expand=True, padx=20, pady=(2, 0))
            self.toggle_btn.configure(text="Server Log")
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
            self._log("!! Could not stop %s: %r" % (name, exc))
            return proc.poll() is not None

    # ---------------- 生命周期 ----------------
    def _sanity_check(self):
        loader = "loader_n.swf" if self.player_kind == "ruffle" else "loader_p.swf"
        missing = [p for p in (RUN_ALL, os.path.join(HERE, "webroot", loader))
                   if not os.path.exists(p)]
        if missing:
            self._log("!! Missing files:\n   " + "\n   ".join(missing))
            self._log("!! Make sure launcher.py is still in the game root directory.")
            self._can_start = False
            self.play_btn.configure(state="disabled")
            return
        if not self.player_path:
            if self.player_kind == "ruffle":
                self._log("!! Ruffle was not found. Move Ruffle.app into /Applications,")
                self._log("   or set ruffle_path in launcher_config.json.")
            else:
                self._log("!! Missing player: %s" % WINDOWS_PROJECTOR)
            self._can_start = False
            self.play_btn.configure(state="disabled")
            return
        player_name = "Ruffle" if self.player_kind == "ruffle" else "Flash Projector"
        self._log("Found %s: %s" % (player_name, self.player_path))

    def start(self):
        if self.running or self._closed or not self._can_start:
            return
        if any(proc and proc.poll() is None for proc in (self.server, self.game)):
            self._log("!! The previous process has not stopped. Click Stop first.")
            return
        uin = self.uin_var.get().strip()
        if not valid_uin(uin):
            messagebox.showwarning("Account", "Enter a valid numeric account ID (1–4294967295).")
            return
        if not valid_player_uin(uin):
            messagebox.showwarning("Account", "This ID belongs to a system bot. Select a player account.")
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
            messagebox.showerror("Port Already in Use", "以下Port Already in Use：%s。\n请关闭旧启动器或对应服务后重试。" % ", ".join(map(str, busy_ports)))
            return
        loader = "loader_n.swf" if self.player_kind == "ruffle" else "loader_p.swf"
        self.game_url = game_url(uin, loader)
        cfg = load_config()
        recent = [uin] + [u for u in cfg.get("recent", []) if u != uin]
        cfg.update({"last_uin": uin, "recent": recent[:8]})
        if not save_config(cfg):
            self._log("!! Could not save recent accounts. Check write permissions for the game directory.")
        self.uin_box.configure(values=recent[:8], state="disabled")
        if hasattr(self, "create_btn"):
            self.create_btn.configure(state="disabled")
        if hasattr(self, "delete_btn"):
            self.delete_btn.configure(state="disabled")
        self.play_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.admin_btn.configure(state="normal")
        self._set_status("Initializing server…", "#e0b83a")
        self._log("--- Starting the web, admin, game, and Flash policy services; first-time setup may take several minutes ---")
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
            self._log("!! Could not start server: %r" % exc)
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
        self._log("!! Server startup timed out: the web, admin, game (9001), or policy (843) service did not become ready. Cleaning up processes; you can click Play again.")
        self.stop()


    def _launch_game(self):
        if not self.running:
            return
        self._set_status("Running — starting the game…", ACCENT)
        player_name = "Ruffle" if self.player_kind == "ruffle" else "Flash Projector"
        self._log("--- Server ready · opening the game with %s (account %s) ---"
                  % (player_name, self.uin_var.get().strip()))
        try:
            command = game_player_command(
                self.player_kind, self.player_path, self.game_url)
            self.game = subprocess.Popen(
                command, cwd=HERE, env=game_player_environment(self.player_kind),
                **HIDDEN_PROCESS)
            self._set_status("Running — game opened", ACCENT)
        except Exception as exc:
            self._log("!! Could not open %s: %r" % (player_name, exc))
            self._log("   Open manually: %s" % self.game_url)
            self._set_status("Running (open the game manually)", ACCENT)

    def stop(self):
        self._session += 1
        self._cancel.set()
        self.running = False
        self._admin_waiting = None
        self._set_status("Stopping…", "#e0b83a")
        ok = True
        for field, name in (("game", "游戏"), ("server", "服务器"), ("admin", "GM 后台")):
            proc = getattr(self, field)
            if self._terminate_owned(proc, name):
                setattr(self, field, None)
            else:
                ok = False
        if ok:
            self._reset_stopped()
            self._log("--- Stopped ---")
        else:
            self._set_status("Failed to stop. Check the logs and try again.", STOP)
            self.play_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.admin_btn.configure(state="normal")
            self._log("!! Some processes are still running; the launcher remains in the running state.")
        return ok


    def _reset_stopped(self):
        self.running = False
        self._admin_waiting = None
        self.play_btn.configure(state="normal" if self._can_start else "disabled")
        self.stop_btn.configure(state="normal" if self.admin and self.admin.poll() is None else "disabled")
        self.admin_btn.configure(state="normal")
        self.uin_box.configure(state="normal")
        if hasattr(self, "create_btn"):
            self.create_btn.configure(state="normal")
        if hasattr(self, "delete_btn"):
            self.delete_btn.configure(state="normal")
        self._set_status("Stopped", "#888")


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
                        self._log("!! Launcher operation failed: %r" % exc)
                elif kind == "__SERVER_EXITED__":
                    if event[2] is self.server and self.running:
                        self._log("!! The server exited unexpectedly. Cleaning up game processes.")
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
        if self.running and not messagebox.askokcancel("Exit", "Stop the server and close the game?"):
            return
        if not self.stop():
            return
        self._closed = True
        self.root.destroy()



def legacy_main():
    root = tk.Tk()
    root.withdraw()
    try:
        root.iconbitmap(default="")   # 无自定义图标；忽略不支持的情况
    except Exception:
        pass
    app = Launcher(root)
    root.deiconify()
    if getattr(sys, "_pvz_autoplay", False) and valid_player_uin(app.uin_var.get().strip()):
        root.after(500, app.start)
    root.mainloop()


def main():
    # Keep the portable EXE and old lifecycle test API compatible. The new
    # renderer is a separate, bundled host; gameplay still runs in PvZRuntime.
    if '--legacy-ui' in sys.argv or os.environ.get('PVZ_LAUNCHER_LEGACY_UI') == '1' or not os.path.isfile(os.path.join(HERE, 'launcher_desktop.py')):
        return legacy_main()
    from launcher_desktop import run
    return run()


if __name__ == "__main__":
    main()
