"""Open only the launcher UI against an ephemeral local fixture, then exit.

Tests the product's own self-test lifecycle. No game loader, real account,
service controller, user display settings or registry is touched.
"""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import argparse
import ctypes
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import threading

HERE = Path(__file__).resolve().parent
TOKEN = 'isolated-native-dpi-fixture'

def child_source(args):
    sys.path.insert(0,str(args.source.parent))
    spec=importlib.util.spec_from_file_location('ui_native_fixture_source',args.source)
    shell=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shell)
    if args.simulate_dpi:
        original=shell.check_dependencies
        def check():
            report=original()
            forms=importlib.import_module('webview.platforms.winforms')
            forms.BrowserView.BrowserForm._scale=property(lambda self:args.simulate_dpi/96)
            return report
        shell.check_dependencies=check
    shown=shell.NativeTray._form_shown
    def on_shown(self,*event_args):
        shown(self,*event_args)
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.GetDpiForWindow.argtypes=[ctypes.c_void_p]
        user.GetDpiForWindow.restype=ctypes.c_uint32
        self._diagnostics['dpi_fixture']=dict(
            os_window_dpi=int(user.GetDpiForWindow(self.form.Handle.ToInt64())),
            simulated_dpi=args.simulate_dpi,
            simulation_scope='BrowserForm logical-to-physical Size conversion only',
            system_settings_changed=False)
        self._write_json(self.diagnostic_file,self._diagnostics)
    shell.NativeTray._form_shown=on_shown
    return shell.main(['--url',args.child_url,'--data-dir',str(args.child_root),
                      '--native-self-test-report',str(args.child_root/'native-report.json')])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe',type=Path,default=Path('D:/pvzol本地最新最新ol/pvzol本地/启动器界面/PvZLauncherUI.exe'))
    parser.add_argument('--python',type=Path)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--name',default='baseline-ui')
    parser.add_argument('--simulate-dpi',type=int)
    parser.add_argument('--child-url')
    parser.add_argument('--child-root',type=Path)
    args = parser.parse_args()
    if args.source:
        args.source=args.source.resolve()
    if args.python:
        args.python=args.python.resolve()
    if args.child_url:
        return child_source(args)
    if args.simulate_dpi and not (args.python and args.source):
        parser.error('--simulate-dpi requires --python and --source')
    root = (HERE/args.name).resolve()
    if not root.is_relative_to(HERE):
        raise ValueError('Fixture output must stay inside dpi-tests')
    root.mkdir(parents=True,exist_ok=True)
    report_path = root/'native-report.json'
    report_path.unlink(missing_ok=True)
    (root/'window.json').unlink(missing_ok=True)
    requests = []
    state = dict(running=False,busy=False,statusMessage='隔离DPI启动测试',services=[],currentAccount=None)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):
            pass

        def do_GET(self):
            requests.append(('GET',self.path.split('?')[0]))
            if self.path.startswith('/api/'):
                if self.headers.get('X-PVZ-Token') != TOKEN:
                    self.send_error(403)
                    return
                data = [] if self.path == '/api/accounts' else state
                body = json.dumps(dict(ok=True,data=data),ensure_ascii=False).encode('utf-8')
                mime = 'application/json'
            else:
                body = b'<!doctype html><meta charset="utf-8"><title>Isolated DPI Startup Test</title><h1>Native launcher DPI startup test</h1><p>Local fixture only.</p>'
                mime = 'text/html'
            self.send_response(200)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests.append(('POST',self.path))
            self.send_error(405)

    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/?token={TOKEN}'
    env = dict(os.environ)
    env.pop('PVZ_LAUNCHER_ACTIVATE_EVENT',None)
    env['PVZ_LAUNCHER_WINDOW_FILE'] = str(root/'window.json')
    if args.python and args.source:
        command=[str(args.python),'-B',str(Path(__file__)), '--source',str(args.source),
                 '--child-url',url,'--child-root',str(root)]
        if args.simulate_dpi:
            command += ['--simulate-dpi',str(args.simulate_dpi)]
    else:
        command=[str(args.exe),'--url',url,'--data-dir',str(root),'--native-self-test-report',str(report_path)]
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    proc = None
    result = dict(ok=False,name=args.name,account_requests_sent=0,
                  system_settings_changed=False,registry_changed=False,simulated_dpi=args.simulate_dpi)
    try:
        proc = subprocess.Popen(command,cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                text=True,encoding='utf-8',errors='replace',startupinfo=startup,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            stdout,stderr = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            # A Windows venv python.exe can redirect into its base interpreter.
            # The product writes its exact child PID only to our fresh fixture
            # record, so stop that owned UI as well as the outer Popen handle.
            window_record=root/'window.json'
            if window_record.exists():
                record=json.loads(window_record.read_text(encoding='utf-8'))
                own_pid=record.get('pid')
                if type(own_pid) is int and own_pid>0:
                    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                    kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
                    kernel.OpenProcess.restype=ctypes.c_void_p
                    kernel.TerminateProcess.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
                    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
                    handle=kernel.OpenProcess(0x100001,False,own_pid)
                    if handle:
                        try:
                            kernel.TerminateProcess(handle,0)
                        finally:
                            kernel.CloseHandle(handle)
            proc.terminate()
            stdout,stderr = proc.communicate(timeout=5)
            result['timed_out'] = True
        result['exit_code'] = proc.returncode
        result['stdout'] = stdout.replace(TOKEN,'[fixture-token]')
        result['stderr'] = stderr.replace(TOKEN,'[fixture-token]')
        result['owned_pid'] = proc.pid
        result['owned_process_exited'] = proc.poll() is not None
        result['requests'] = requests
        if report_path.exists():
            native = json.loads(report_path.read_text(encoding='utf-8'))
            result['native'] = native
            shown,closed = native.get('shown',{}),native.get('closed',{})
            x,y,w,h = shown['bounds']
            left,top,width,height = shown['work_area']
            result['centered'] = (x,y) == (left+(width-w)//2,top+(height-h)//2)
            result['in_work_area'] = w<=width and h<=height and left<=x<=left+width-w and top<=y<=top+height-h
            result['clean_exit'] = closed.get('tray_disposed') and not closed.get('worker_alive') and not closed.get('activation_alive') and closed.get('window_record_removed')
            result['ok'] = bool(proc.returncode==0 and result['centered'] and result['in_work_area'] and result['clean_exit'] and shown.get('tray_visible') and not any(r[0]=='POST' for r in requests))
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join()
        (root/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
    return 0 if result['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
