"""Probe only our own hidden Flash process on a no-script offline SWF.

No account/game loader, server, save, registry or display setting is touched.
Thread DPI contexts are restored after every measurement and at exit.
"""
from pathlib import Path
import ctypes
import importlib.util
import json
import struct
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('window_original',HERE/'launcher_game_window.original.py')
window = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(window)

def make_empty_swf(path,width=1440,height=900):
    coords = (0,width*20,0,height*20)
    bit_count = max(value.bit_length()+1 for value in coords)
    bits = format(bit_count,'05b') + ''.join(format(v,f'0{bit_count}b') for v in coords)
    bits += '0' * (-len(bits)%8)
    rect = int(bits,2).to_bytes(len(bits)//8,'big')
    body = rect + struct.pack('<HH',12*256,1) + struct.pack('<H',(9<<6)|3) + b'\xc5\xe5\xd6' + struct.pack('<HH',64,0)
    path.write_bytes(b'FWS\x0a' + struct.pack('<I',8+len(body)) + body)

def bind(user,name,args,restype):
    function = getattr(user,name,None)
    if function:
        function.argtypes = args
        function.restype = restype
    return function

def main():
    swf = HERE/'empty-no-script.swf'
    make_empty_swf(swf)
    api = window.WindowsWindowApi()
    user = api.user
    handle,uint,integer = ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int32
    get_window_context = bind(user,'GetWindowDpiAwarenessContext',[handle],handle)
    get_awareness = bind(user,'GetAwarenessFromDpiAwarenessContext',[handle],integer)
    get_dpi = bind(user,'GetDpiForWindow',[handle],uint)
    get_system_dpi = bind(user,'GetDpiForSystem',[],uint)
    previous = api.physical_coordinates()
    proc = None
    result = dict(ok=False,account_requests_sent=0,system_settings_changed=False,
                  asset='1440x900 no-script/no-network SWF',measurements=[],trace=[])
    try:
        options = window.game_startup_options('flash')
        proc = subprocess.Popen([str(HERE/'flashplayer_sa.exe'),str(swf)],cwd=HERE,
                                creationflags=subprocess.CREATE_NO_WINDOW,**options)
        result['owned_pid'] = proc.pid
        started = time.monotonic()
        while time.monotonic()-started < 4:
            hwnd = api.find_window(proc.pid,include_hidden=True)
            if hwnd and api.window_pid(hwnd) == proc.pid:
                entry = dict(t=round(time.monotonic()-started,4),hwnd=int(hwnd),
                             physical_bounds=api.bounds(hwnd),
                             source_client_size=api.client_size(hwnd),
                             visible=bool(user.IsWindowVisible(hwnd)))
                if not result['trace'] or entry['physical_bounds'] != result['trace'][-1]['physical_bounds']:
                    result['trace'].append(entry)
                if time.monotonic()-started >= 1.5:
                    result['window_context'] = get_window_context(hwnd) if get_window_context else None
                    result['window_awareness'] = get_awareness(result['window_context']) if get_awareness and result['window_context'] else None
                    result['window_dpi'] = get_dpi(hwnd) if get_dpi else None
                    result['stage_size'] = window.read_swf_stage(swf)
                    for context in (-1,-2,-3,-4,result['window_context']):
                        if context is None:
                            continue
                        before = user.SetThreadDpiAwarenessContext(handle(context))
                        try:
                            rect = api.Rect()
                            success = bool(user.GetClientRect(hwnd,ctypes.byref(rect)))
                            result['measurements'].append(dict(context=int(context),set_success=bool(before),
                                  client_size=(rect.right-rect.left,rect.bottom-rect.top) if success else None,
                                  bounds=api.bounds(hwnd),system_dpi=get_system_dpi() if get_system_dpi else None))
                        finally:
                            api.restore_coordinates(before)
                    result['never_revealed'] = not any(e['visible'] for e in result['trace'])
                    result['ok'] = result['never_revealed'] and proc.poll() is None
                    break
            time.sleep(.05)
    finally:
        if proc:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
            result['owned_process_exited'] = proc.poll() is not None
        api.restore_coordinates(previous)
        (HERE/'native_dpi_probe_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
    return 0 if result['ok'] else 1

if __name__ == '__main__':
    sys.exit(main())
