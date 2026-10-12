import ctypes, ctypes.wintypes as wt
u = ctypes.windll.user32
# 找 ruffle 的 PID
import subprocess
out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ruffle.exe', '/FO', 'CSV'], capture_output=True, text=True).stdout
pids = []
for line in out.strip().splitlines()[1:]:
    parts = line.split('","')
    if len(parts) > 1:
        pids.append(int(parts[1].strip('"')))
print('ruffle pids:', pids)
pidset = set(pids)
found = []
@ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
def cb(h, l):
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    if pid.value in pidset:
        n = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(h, n, 256)
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, cls, 256)
        found.append((h, bool(u.IsWindowVisible(h)), n.value, cls.value))
    return True
u.EnumWindows(cb, 0)
print('windows:', found)
