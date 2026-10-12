"""Independent structural verification for the tower-defense SWF candidates."""
from pathlib import Path
import json
import os
import sys
import unittest
from abc_tools import parse_abc, find_abc
from avm2_validate import unpack_swf, body_tail, verify_method

HERE = Path(__file__).resolve().parent
BASELINE = Path(os.environ.get('PVZ_BASELINE_WEBROOT',str(HERE.parent/'baseline')))
TOWER = Path(os.environ.get('PVZ_TOWER_CANDIDATE_WEBROOT',str(HERE.parent/'tower')))
TARGETS = {
    'Game_p.swf': ('Game_p.tower.swf', [('starling.core.Starling','initialize'),
                                     ('starling.display.QuadBatch','createBuffers')]),
    'ModuleTD_p.swf': ('ModuleTD_p.tower.swf', [('com.qq.modules.td.view.game.TDBGViewMediator','checkSize')]),
}

def read(path):
    raw = unpack_swf(path)
    a,b,*_ = find_abc(raw)
    abc = raw[a:b]
    return raw,abc,parse_abc(abc)


class TowerStructureRegression(unittest.TestCase):
    def test_original_and_candidate_target_methods_have_valid_control_flow(self):
        reports = []
        for filename,(candidate,targets) in TARGETS.items():
            for path in (BASELINE/filename,TOWER/candidate):
                _,abc,info = read(path)
                for classname,methodname in targets:
                    with self.subTest(file=path.name,method=methodname):
                        mid = info.class_methods[(classname,methodname)]
                        _,at,size = info.method_bodies[mid]
                        exceptions,_,_ = body_tail(abc,info,mid)
                        report = verify_method(abc[at:at+size],info.method_headers[mid],info,exceptions)
                        reports.append(dict(file=path.name,classname=classname,method=methodname,
                                            method_id=mid,code_size=size,**report))
        (HERE/'tower_stack_report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')

    def test_constant_pools_class_traits_and_untargeted_full_bodies_preserved(self):
        for filename,(candidate,targets) in TARGETS.items():
            _,before,bi = read(BASELINE/filename)
            _,after,ai = read(TOWER/candidate)
            changed = {bi.class_methods[t] for t in targets}
            first = min(h[0] for h in bi.method_headers.values())
            self.assertEqual(before[:first],after[:first],filename)
            self.assertEqual(bi.method_bodies.keys(),ai.method_bodies.keys())
            self.assertEqual(bi.class_methods,ai.class_methods)
            for mid in bi.method_bodies:
                with self.subTest(file=filename,method_id=mid):
                    self.assertEqual(bi.method_headers[mid][1:],ai.method_headers[mid][1:])
                    ex,ta,end = body_tail(before,bi,mid)
                    cex,cta,cend = body_tail(after,ai,mid)
                    self.assertEqual(ex,cex)
                    self.assertEqual(before[ta:end],after[cta:cend])
                    if mid not in changed:
                        self.assertEqual(before[bi.method_headers[mid][0]:end],after[ai.method_headers[mid][0]:cend])

    def test_non_abc_assets_and_tags_are_preserved(self):
        for filename,(candidate,_) in TARGETS.items():
            before,_,_ = read(BASELINE/filename)
            after,_,_ = read(TOWER/candidate)
            a,b,la,hs,_ = find_abc(before)
            ca,cb,cla,chs,_ = find_abc(after)
            self.assertEqual((a,la,hs),(ca,cla,chs))
            # Ignore the two lengths that must change when createBuffers grows.
            prefix_before,prefix_after = bytearray(before[:a]),bytearray(after[:ca])
            for offset in (4,la):
                prefix_before[offset:offset+4] = b'\0'*4
                prefix_after[offset:offset+4] = b'\0'*4
            prefix_before[:3] = prefix_after[:3] = b'FWS'
            self.assertEqual(prefix_before,prefix_after,filename)
            self.assertEqual(before[b:],after[cb:],filename)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TowerStructureRegression)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = dict(passed=result.wasSuccessful(),test_count=result.testsRun,
                   failures=[dict(test=t.id(),traceback=trace) for t,trace in result.failures],
                   errors=[dict(test=t.id(),traceback=trace) for t,trace in result.errors],
                   baseline=str(BASELINE),candidate=str(TOWER),real_flash_ui_tested=False)
    (HERE/'tower_structure_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() else 1)
