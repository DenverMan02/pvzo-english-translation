"""Execute real NativeTray.attach against typed .NET interface doubles.

Exercise physical form/work-area coordinates at fractional and high DPI,
including negative monitor origins. No real native UI/account is involved.
"""
from pathlib import Path
from types import ModuleType,SimpleNamespace as NS
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get('PVZ_UI_SOURCE','D:/pvzol本地最新最新ol/pvzol本地/ui_shell.py'))
spec = importlib.util.spec_from_file_location('ui_dpi_under_test',SOURCE)
shell = importlib.util.module_from_spec(spec)
sys.path.insert(0,str(SOURCE.parent))
spec.loader.exec_module(shell)

class Event:
    def __init__(self): self.callbacks=[]
    def __iadd__(self,callback): self.callbacks.append(callback);return self

class Size(NS):
    def __init__(self,w,h):
        assert type(w) is int and type(h) is int
        super().__init__(Width=w,Height=h)

class Rectangle(NS):
    def __init__(self,x,y,w,h):
        assert all(type(v) is int for v in (x,y,w,h))
        super().__init__(Left=x,Top=y,Width=w,Height=h)

class Menu:
    def __init__(self): self.Items=NS(Add=Mock());self.Opening=Event()

class Item:
    def __init__(self,text): self.Text=text;self.Click=Event()

class Icon:
    def __init__(self): self.DoubleClick=Event();self.Visible=False

class Timer:
    def __init__(self): self.Tick=Event();self.Start=Mock()

class UiGeometryTests(unittest.TestCase):
    def test_dpi_changed_constrains_after_native_layout_and_accepts_negative_monitor_origins(self):
        scenarios=0
        for dpi in (114,115,116,144,192,384):
            for area in ((-2560,80,2560,1360),(1920,-1080,1920,1040),(100,50,900,600)):
                with self.subTest(dpi=dpi,area=area):
                    system,drawing,forms=ModuleType('System'),ModuleType('System.Drawing'),ModuleType('System.Windows.Forms')
                    system.Action=lambda fn:fn
                    drawing.Rectangle,drawing.Size=Rectangle,Size
                    forms.Screen=NS(FromControl=lambda form:NS(WorkingArea=Rectangle(*area)))
                    queue=[]
                    form=NS(Width=1280,Height=860,MinimumSize=Size(960,700),IsDisposed=False,
                            BeginInvoke=lambda callback:queue.append(callback),Bounds=object())
                    tray=shell.NativeTray.__new__(shell.NativeTray)
                    tray.form,tray._closed=form,threading.Event()
                    original=form.Bounds
                    with patch.dict(sys.modules,{'System':system,'System.Drawing':drawing,'System.Windows.Forms':forms}):
                        tray._dpi_changed()
                        self.assertEqual(len(queue),1)
                        self.assertIs(form.Bounds,original)
                        form.Width,form.Height=int(1280*dpi/96),int(860*dpi/96)
                        queue.pop()()
                    if form.Width>area[2] or form.Height>area[3]:
                        bounds=form.Bounds
                        self.assertEqual((bounds.Width,bounds.Height),(min(form.Width,area[2]),min(form.Height,area[3])))
                        self.assertEqual((bounds.Left,bounds.Top),(area[0]+(area[2]-bounds.Width)//2,area[1]+(area[3]-bounds.Height)//2))
                        self.assertLessEqual(form.MinimumSize.Width,bounds.Width)
                        self.assertLessEqual(form.MinimumSize.Height,bounds.Height)
                    else:
                        self.assertIs(form.Bounds,original)
                    scenarios+=1
        self.assertEqual(scenarios,18)

    def test_dpi_changed_handles_disposal_before_and_after_queued_callback(self):
        system,drawing,forms=ModuleType('System'),ModuleType('System.Drawing'),ModuleType('System.Windows.Forms')
        system.Action=lambda fn:fn
        drawing.Rectangle,drawing.Size=Rectangle,Size
        forms.Screen=NS(FromControl=Mock(side_effect=AssertionError('disposed form was queried')))
        tray=shell.NativeTray.__new__(shell.NativeTray)
        tray._closed=threading.Event()
        tray.form=NS(IsDisposed=True,BeginInvoke=Mock())
        with patch.dict(sys.modules,{'System':system,'System.Drawing':drawing,'System.Windows.Forms':forms}):
            tray._dpi_changed()
            tray.form.BeginInvoke.assert_not_called()
            tray.form.IsDisposed=False
            queue=[]
            tray.form.BeginInvoke=lambda callback:queue.append(callback)
            tray._dpi_changed()
            tray._closed.set()
            queue.pop()()
            forms.Screen.FromControl.assert_not_called()

    def test_main_initializes_dpi_before_clr_and_continues_when_api_unavailable(self):
        for diagnostic in (dict(ok=True,mode='per_monitor_v2',source='existing',attempts=[]),
                           dict(ok=False,mode='unknown',source='fallback',attempts=[])):
            order=[]
            webview=ModuleType('webview')
            webview.settings={}
            before_show=Event()
            webview.create_window=Mock(return_value=NS(events=NS(before_show=before_show)))
            webview.start=Mock(side_effect=lambda **kw:order.append('start'))
            tray=NS(attach=lambda:None,_diagnostics={},close=Mock())
            with tempfile.TemporaryDirectory(dir=HERE) as root, \
                 patch.object(shell,'initialize_dpi_awareness',side_effect=lambda:order.append('dpi') or diagnostic), \
                 patch.object(shell,'check_dependencies',side_effect=lambda:order.append('clr') or {}), \
                 patch.object(shell,'LoopbackClient',return_value=object()), \
                 patch.object(shell,'NativeTray',return_value=tray), \
                 patch.dict(sys.modules,{'webview':webview}),patch.object(shell.logging,'basicConfig'):
                self.assertEqual(shell.main(['--url','http://127.0.0.1:12345/?token=fixture','--data-dir',root]),0)
            self.assertEqual(order,['dpi','clr','start'])
            self.assertEqual(tray._diagnostics['dpi'],diagnostic)
            self.assertEqual(before_show.callbacks,[tray.attach])
            tray.close.assert_called_once()

    def test_actual_attach_centers_and_clamps_physical_sizes_at_fractional_dpi(self):
        checked=0
        for dpi in (96,110,114,115,116,120,144,168,192,240,288,384,480):
            for area in ((0,0,3840,2088),(-2560,80,2560,1360),(1920,-1080,1920,1040),(100,50,900,600)):
                with self.subTest(dpi=dpi,area=area):
                    drawing,forms=ModuleType('System.Drawing'),ModuleType('System.Windows.Forms')
                    drawing.Icon=NS(ExtractAssociatedIcon=lambda _:object())
                    drawing.Rectangle,drawing.Size=Rectangle,Size
                    forms.ContextMenuStrip,forms.NotifyIcon,forms.Timer,forms.ToolStripMenuItem=Menu,Icon,Timer,Item
                    forms.Cursor=NS(Position=object())
                    forms.FormStartPosition=NS(Manual=object())
                    working_area=Rectangle(*area)
                    forms.Screen=NS(FromPoint=lambda _:NS(WorkingArea=working_area))
                    form=NS(Width=int(1280*dpi/96),Height=int(860*dpi/96),
                            MinimumSize=Size(int(960*dpi/96),int(700*dpi/96)),
                            FormClosed=Event(),Shown=Event(),Activated=Event())
                    tray=shell.NativeTray.__new__(shell.NativeTray)
                    tray.window=NS(native=form)
                    tray.worker=Mock()
                    tray._publish_window=Mock()
                    tray._update_menu=Mock()
                    with patch.dict(sys.modules,{'System.Drawing':drawing,'System.Windows.Forms':forms}), \
                         patch.object(shell,'ActivationWatcher',return_value=Mock()), \
                         patch.dict(os.environ,{},clear=False):
                        tray.attach()
                    bounds=form.Bounds
                    self.assertEqual((bounds.Left,bounds.Top),(area[0]+(area[2]-bounds.Width)//2,area[1]+(area[3]-bounds.Height)//2))
                    self.assertEqual((bounds.Width,bounds.Height),(min(int(1280*dpi/96),area[2]),min(int(860*dpi/96),area[3])))
                    self.assertLessEqual(form.MinimumSize.Width,bounds.Width)
                    self.assertLessEqual(form.MinimumSize.Height,bounds.Height)
                    self.assertTrue(tray.icon.Visible)
                    tray.worker.start.assert_called_once()
                    tray.worker.submit.assert_called_once_with('refresh')
                    tray._publish_window.assert_called_once()
                    checked+=1
        self.assertEqual(checked,52)

if __name__ == '__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(UiGeometryTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    summary=dict(passed=result.wasSuccessful(),test_count=result.testsRun,physical_geometry_scenarios=52,
                 dpi_change_geometry_scenarios=18,main_initialization_scenarios=2,
                 fractional_120_percent_dpi=[114,115,116],source=str(SOURCE),
                 actual_os_scaling_changed=False,
                 errors=[dict(test=str(test),traceback=trace) for test,trace in result.errors],
                 failures=[dict(test=str(test),traceback=trace) for test,trace in result.failures])
    (HERE/'ui_geometry_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() else 1)
