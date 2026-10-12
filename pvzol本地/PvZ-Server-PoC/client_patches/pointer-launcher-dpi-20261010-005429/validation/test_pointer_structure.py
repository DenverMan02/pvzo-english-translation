"""Independent preservation/stack check for the tower pointer methods."""
from pathlib import Path
import json
import sys
import unittest
from abc_tools import find_abc,parse_abc
from avm2_validate import unpack_swf,body_tail,verify_method
from actual_vm import VM
from test_actual_tower import execute_checksize

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent/'plant-alignment'
BEFORE=ROOT/'ModuleTD_p.current.swf'
AFTER=ROOT/'ModuleTD_p.pointer.swf'
MANIFEST=json.loads((ROOT/'ModuleTD_p.pointer.json').read_text(encoding='utf-8'))

def read(path):
    raw=unpack_swf(path)
    a,b,*_=find_abc(raw)
    abc=raw[a:b]
    return abc,parse_abc(abc)

class PointerStructureTests(unittest.TestCase):
    def test_all_twenty_before_after_methods_have_valid_control_flow_stacks(self):
        records=[]
        for path in (BEFORE,AFTER):
            abc,info=read(path)
            for target in MANIFEST['methods']:
                mid=target['method_id']
                with self.subTest(file=path.name,classname=target['classname'],method=target['name']):
                    _,at,size=info.method_bodies[mid]
                    exceptions,_,_=body_tail(abc,info,mid)
                    result=verify_method(abc[at:at+size],info.method_headers[mid],info,exceptions)
                    records.append(dict(file=path.name,method_id=mid,**result))
        (HERE/'pointer_stack_report.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')

    def test_only_ten_methods_change_and_checksize_is_identical(self):
        before,bi=read(BEFORE)
        after,ai=read(AFTER)
        changes={entry['method_id'] for entry in MANIFEST['methods']}
        self.assertEqual(len(changes),13)
        first=min(v[0] for v in bi.method_headers.values())
        self.assertEqual(before[:first],after[:first])
        self.assertEqual(bi.class_methods,ai.class_methods)
        self.assertEqual(bi.method_bodies.keys(),ai.method_bodies.keys())
        for mid in bi.method_bodies:
            with self.subTest(mid=mid):
                if mid in (5612,5323):
                    oldhead,newhead=bi.method_headers[mid],ai.method_headers[mid]
                    self.assertEqual(oldhead[1:3],(2,2))
                    self.assertEqual(newhead[1:3],(4,3))
                    self.assertEqual(oldhead[3:],newhead[3:])
                else:
                    self.assertEqual(bi.method_headers[mid][1:],ai.method_headers[mid][1:])
                ex,at,end=body_tail(before,bi,mid)
                nex,nat,nend=body_tail(after,ai,mid)
                self.assertEqual(ex,nex)
                self.assertEqual(before[at:end],after[nat:nend])
                if mid not in changes:
                    self.assertEqual(before[bi.method_headers[mid][0]:end],after[ai.method_headers[mid][0]:nend])
        check=bi.class_methods[('com.qq.modules.td.view.game.TDBGViewMediator','checkSize')]
        self.assertNotIn(check,changes)

    def test_current_checksize_viewport_fix_passes_real_vm_in_new_candidate(self):
        for native in ((1440,900),(1920,1080),(3840,2088),(2162,1360),(800,600)):
            for debug in (False,True):
                with self.subTest(native=native,debug=debug):
                    old=execute_checksize(BEFORE,native,debug)
                    new=execute_checksize(AFTER,native,debug)
                    self.assertIs(new[0].viewPort,new[1])
                    self.assertAlmostEqual(new[0].stage.stageWidth,1080)
                    self.assertEqual(new[0].stage.stageHeight,675)
                    self.assertEqual(vars(old[1]),vars(new[1]))
                    self.assertEqual(old[4:],new[4:])

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(PointerStructureTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    summary=dict(passed=result.wasSuccessful(),test_count=result.testsRun,
                 before_after_stack_methods=26,unchanged_methods=7988,
                 checksize_real_vm_scenarios=10,real_windows_rendering_tested=False,
                 failures=[dict(test=str(t),traceback=trace) for t,trace in result.failures],
                 errors=[dict(test=str(t),traceback=trace) for t,trace in result.errors])
    (HERE/'pointer_structure_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() else 1)
