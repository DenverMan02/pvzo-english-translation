"""Injectable API regressions; never change process or OS DPI here."""
from pathlib import Path
import importlib.util
import json
import sys
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent/'dpi-fix'/'staged'/'launcher_dpi.py'
spec = importlib.util.spec_from_file_location('candidate_dpi', SOURCE)
dpi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dpi)

class Api:
    def __init__(self, supported=(), results=None, current=None, window=115):
        self.supported=set(supported)
        self.results=results or {}
        self.current=current
        self.window=window
        self.calls=[]
    def supports(self,name):
        return name in self.supported
    def reply(self,name,value=None):
        self.calls.append((name,value))
        answer=self.results.get((name,value),self.results.get(name, (False,87)))
        if isinstance(answer,BaseException):
            raise answer
        return answer
    def set_context(self,value):
        return self.reply('set_context',value)
    def set_shcore(self,value):
        return self.reply('set_shcore',value)
    def set_system(self):
        return self.reply('set_system')
    def current_mode(self):
        if isinstance(self.current,BaseException):
            raise self.current
        return self.current
    def window_dpi(self,hwnd):
        self.calls.append(('window_dpi',hwnd))
        if isinstance(self.window,BaseException):
            raise self.window
        return self.window

class DpiHelperTests(unittest.TestCase):
    def test_per_monitor_v2_is_first_and_stops_after_success(self):
        api=Api(('set_context','set_shcore','set_system'),{('set_context',-4):(True,0)})
        result=dpi.initialize_dpi_awareness(api)
        self.assertTrue(result['ok'])
        self.assertEqual(result['mode'],'per_monitor_v2')
        self.assertEqual(api.calls,[('set_context',-4)])

    def test_every_fallback_can_succeed(self):
        scenarios=[(('set_context',),{('set_context',-3):(True,0)},'per_monitor'),
                   (('set_shcore',),{('set_shcore',2):0},'per_monitor'),
                   (('set_shcore',),{('set_shcore',2):-2147024809,('set_shcore',1):0},'system'),
                   (('set_system',),{'set_system':(True,0)},'system')]
        for supported,results,mode in scenarios:
            with self.subTest(supported=supported,mode=mode):
                result=dpi.initialize_dpi_awareness(Api(supported,results))
                self.assertTrue(result['ok'])
                self.assertEqual(result['mode'],mode)

    def test_manifest_access_denied_keeps_existing_awareness(self):
        for mode in ('per_monitor_v2','per_monitor','system','unaware',None):
            with self.subTest(mode=mode):
                api=Api(('set_context','set_shcore','set_system'),{'set_context':(False,5)},mode)
                result=dpi.initialize_dpi_awareness(api)
                self.assertEqual(result['source'],'existing')
                self.assertEqual(result['ok'],mode in ('per_monitor_v2','per_monitor','system'))
                self.assertEqual(api.calls,[('set_context',-4)])

    def test_shcore_signed_and_unsigned_access_denied_are_recognized(self):
        for hr in (-2147024891,0x80070005):
            api=Api(('set_shcore','set_system'),{'set_shcore':hr},'per_monitor_v2')
            result=dpi.initialize_dpi_awareness(api)
            self.assertTrue(result['ok'])
            self.assertEqual(result['source'],'existing')
            self.assertEqual(api.calls,[('set_shcore',2)])

    def test_missing_broken_or_failed_apis_never_raise(self):
        for api in (Api(),Api(('set_context','set_shcore','set_system'),
                     {'set_context':OSError('unsupported'),'set_shcore':OSError('unsupported'),
                      'set_system':OSError('unsupported')},OSError('no context')),
                    Api(('set_context','set_shcore','set_system'),
                     {'set_context':(False,87),'set_shcore':-2147024809,'set_system':(False,87)})):
            with self.subTest(api=api):
                result=dpi.initialize_dpi_awareness(api)
                self.assertFalse(result['ok'])
                self.assertEqual(result['source'],'fallback')
                self.assertEqual(len(result['attempts']),5)

    def test_fractional_and_arbitrary_dpi_are_not_quantized(self):
        hwnd=0x123456789ABC
        for value in (96,110,114,115,116,120,144,168,192,240,288,384,480):
            api=Api(('window_dpi',),window=value)
            self.assertEqual(dpi.get_window_dpi(hwnd,api),value)
            self.assertEqual(api.calls,[('window_dpi',hwnd)])

    def test_invalid_window_and_apis_have_safe_default(self):
        for hwnd in (0,-1,None,'not an HWND'):
            self.assertEqual(dpi.get_window_dpi(hwnd,Api(('window_dpi',))),96)
        for api in (Api(),Api(('window_dpi',),window=0),Api(('window_dpi',),window=-5),
                    Api(('window_dpi',),window=None),Api(('window_dpi',),window=OSError('missing'))):
            self.assertEqual(dpi.get_window_dpi(123,api),96)
        self.assertEqual(dpi.get_window_dpi(0,Api(),default=115),115)

    def test_exception_diagnostics_preserve_error_and_hide_credentials(self):
        secret='fixture-secret-value'
        exception=RuntimeError('WebView failed http://user:password@127.0.0.1:1234/launch?token='+secret+
                               '&account=alice#private X-PVZ-Token: '+secret+' Authorization: Bearer '+secret)
        result=dpi.safe_exception_detail(exception,sensitive_values=(secret,))
        self.assertIn('RuntimeError',result)
        self.assertIn('/launch',result)
        for private in (secret,'password','alice','#private'):
            self.assertNotIn(private,result)
        self.assertLessEqual(len(dpi.safe_exception_detail(RuntimeError('x'*20000))),12000)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DpiHelperTests))
    summary=dict(passed=result.wasSuccessful(),test_count=result.testsRun,
                 source=str(SOURCE),api_injected=True,system_settings_changed=False,
                 fractional_dpi_simulated=[114,115,116],
                 failures=[dict(test=str(t),traceback=trace) for t,trace in result.failures],
                 errors=[dict(test=str(t),traceback=trace) for t,trace in result.errors])
    (HERE/'dpi_helper_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() else 1)
