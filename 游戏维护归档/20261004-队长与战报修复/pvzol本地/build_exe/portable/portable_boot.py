"""Portable bootstrap. Application code and saves stay beside the game assets."""
import ctypes
import json
import os
from pathlib import Path
import runpy
import sys
import traceback
WORKER_MODE = "--script" in sys.argv or "--diagnose" in sys.argv


def game_directory():
    base = Path(sys.executable).resolve().parent
    for folder in (base / 'pvzol本地', base, base.parent):
        if (folder / 'launcher.py').is_file() and (folder / 'PvZ-Server-PoC' / 'run_all.py').is_file():
            return folder
    raise RuntimeError('找不到游戏目录。请完整解压压缩包，并将启动程序与 pvzol本地 文件夹放在一起。')


def main():
    root = game_directory()
    server = root / 'PvZ-Server-PoC'
    runtime = root / '运行环境' / 'PvZRuntime.exe'
    args = sys.argv[1:]
    # Execute the shipped source with its real __file__. Legacy modules use
    # sys.frozen to relocate saves, which would split the existing save database.
    # The bootloader/runtime is already initialized; application code runs with
    # ordinary source semantics and an explicit subprocess interpreter path.
    sys.frozen = False
    sys._pvz_runtime = str(runtime)
    sys.path[:0] = [str(server), str(root)]
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding='utf-8', errors='replace')
    if sys.stdout is None or sys.stderr is None:
        stream = open(root / 'portable_launcher.log', 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = stream
    if args[:1] == ['--diagnose']:
        import sqlite3
        import tkinter
        import run_all
        report = {
            'root': str(root), 'runtime': str(runtime),
            'database': run_all.game.store.DB_PATH,
            'client_dir': run_all.serve_client.CLIENT_DIR,
            'server_source': run_all.game.__file__,
            'python': sys.version, 'sqlite': sqlite3.sqlite_version,
            'tk': tkinter.TkVersion,
            'runtime_exists': runtime.is_file(),
            'flash_exists': (root / 'flashplayer_sa.exe').is_file(),
        }
        view = tkinter.Tk()
        view.withdraw()
        view.update()
        view.destroy()
        report['tk_initialized'] = True
        Path(args[1]).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return
    if args[:1] == ['--script']:
        if len(args) < 2 or args[1] not in {'run_all.py','admin_server.py','gm.py'}:
            raise RuntimeError('不支持的服务启动参数。')
        target = server / args[1]
        os.chdir(server)
        sys.argv = [str(target), *args[2:]]
    else:
        if not runtime.is_file():
            raise RuntimeError('缺少 pvzol本地/运行环境/PvZRuntime.exe，请完整解压游戏压缩包。')
        target = root / 'launcher.py'
        os.chdir(root)
        sys.argv = [str(target)]
        sys._pvz_autoplay = True
    runpy.run_path(str(target), run_name='__main__')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        detail = traceback.format_exc()
        try:
            (Path(sys.executable).resolve().parent / '启动错误.log').write_text(detail,encoding='utf-8')
        except OSError:
            pass
        if WORKER_MODE:
            if sys.stderr is not None:
                sys.stderr.write(detail)
        else:
            ctypes.windll.user32.MessageBoxW(None,'启动失败，请查看程序旁的“启动错误.log”。\n\n'+detail[-1800:],'植物大战僵尸OL',0x10)
        sys.exit(1)

