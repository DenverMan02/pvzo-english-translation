"""Regression against real before/after AVM2, with synthetic UI objects.

No account, server request, asset use, or real UI action occurs in these tests.
"""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import os
import sys
import unittest

from actual_vm import VM
from abc_tools import find_abc, parse_abc
from avm2_validate import body_tail, instructions, unpack_swf, verify_method

HERE = Path(__file__).resolve().parent
WEB = Path(os.environ.get('PVZ_BASELINE_WEBROOT', str(HERE.parent/'baseline')))
CANDIDATES = Path(os.environ.get('PVZ_CANDIDATE_WEBROOT',str(HERE.parent/'bag'/'candidates')))
FILES = ('ModuleAct3years.swf', 'ModuleBag.swf', 'ModuleCard.swf', 'ModuleChat.swf', 'ModuleUnion.swf')
CLASS = 'com.qq.display::QPopupMenu'
INIT_CLASS = 'com.qq.modules.bag.view.components::BagItemOpenEffectViewMediator'


class Menu(NS):
    def __init__(self):
        super().__init__(children=[])

    def addChild(self, value):
        self.children.append(value)


class Button(NS):
    def __init__(self):
        super().__init__(enabled=True)


class Point(NS):
    def __init__(self, x=0, y=0):
        super().__init__(x=x, y=y)


class BagVM(VM):
    def get(self, obj, name):
        key = self.short(name)
        if isinstance(obj, list) and key == 'push':
            return lambda value: obj.append(value) or len(obj)
        if isinstance(obj, list) and key == 'indexOf':
            return lambda value: obj.index(value) if value in obj else -1
        return super().get(obj, name)


def execute_menu(path, native_size, mouse=(850, 510), labels=('使用',), explicit=None, disabled=None):
    vm = BagVM(path)
    logical_width, logical_height = 1440, 900
    width, height = native_size
    scale = min(width/logical_width, height/logical_height)
    left, top = (width-logical_width*scale)/2, (height-logical_height*scale)/2
    stage = NS(mouseX=left+mouse[0]*scale, mouseY=top+mouse[1]*scale,
               stageWidth=width, stageHeight=height)
    app = NS(mouseX=mouse[0], mouseY=mouse[1], width=logical_width,
             height=logical_height, stage=stage)
    calls, timers, callbacks, disposals = [], [], [], []
    vm.globals.update({
        'asgui.core::Application': NS(application=app),
        'com.qq.display::QCanvas': Menu,
        'asgui.controls::Button': Button,
        'flash.geom::Point': Point,
        'asgui.managers::PopUpManager': NS(AddPopUp=lambda *args: calls.append(args)),
        'flash.utils::setTimeout': lambda *args: timers.append(args) or 7,
    })
    this = NS(_callback=None, _menuSp=None, _btnArr=[], _functionId=0,
              ITEM_HEIGHT=24, ITEM_GAP=2, addClickListener=lambda:None,
              Dispose=lambda:disposals.append(True))
    callback = lambda value:callbacks.append(value)
    point = Point(*explicit) if explicit else None
    vm.execute(CLASS, 'show', this, (list(labels), callback, point, True, 75, disabled))
    assert not callbacks, 'Opening a menu must not invoke an item callback'
    return this, calls, stage, app, disposals, timers


def execute_init(path, native_size):
    vm = BagVM(path)
    stage = NS(stageWidth=native_size[0], stageHeight=native_size[1])
    app = NS(width=1440, height=900, stage=stage)
    children, supers = [], []
    class MovieClip(NS):
        pass
    display = MovieClip(mouseEnabled=True)
    img = {'$addChild': lambda child:children.append(child)}
    view = NS(alpha=1, img=img, width=0, height=0)
    this = NS(view=view, cacheCardList=None, cacheCardMCList=None, display=None)
    setattr(this, '__super_init', lambda:supers.append(True))
    vm.globals.update({
        'asgui.core::Application': NS(application=app),
        'com.qq::GameGloble': NS(stage=stage),
        'asgui.resources::AssetManager': NS(GetNewClass=lambda name:display),
        'flash.display::MovieClip': MovieClip,
    })
    vm.execute(INIT_CLASS, 'init', this)
    return this, children, supers


def read(path):
    raw = unpack_swf(path)
    a,b,*_ = find_abc(raw)
    abc = raw[a:b]
    return abc, parse_abc(abc)


class BagActualBytecodeRegression(unittest.TestCase):
    def test_original_menu_reproduces_physical_coordinate_error(self):
        this, _, stage, app, _, _ = execute_menu(WEB/'ModuleBag.swf', (3840,2088))
        self.assertEqual((this._menuSp.x, this._menuSp.y), (stage.mouseX-5, stage.mouseY-5))
        self.assertNotEqual((this._menuSp.x, this._menuSp.y), (app.mouseX-5, app.mouseY-5))

    def test_all_real_menu_candidates_follow_mouse_at_window_scales(self):
        checks = 0
        for filename in FILES:
            for native in ((1440,900),(1920,1080),(3840,2088),(2162,1360),(800,600)):
                for mouse in ((300,200),(850,510),(1220,720)):
                    for labels in (('使用',),('使用','批量使用')):
                        with self.subTest(file=filename,native=native,mouse=mouse,labels=labels):
                            this, calls, _, _, disposals, timers = execute_menu(CANDIDATES/filename,native,mouse,labels)
                            self.assertEqual((this._menuSp.x,this._menuSp.y),(mouse[0]-5,mouse[1]-5))
                            self.assertEqual([b.label for b in this._menuSp.children],list(labels))
                            self.assertEqual(this._menuSp.width,75)
                            self.assertEqual(len(calls),1)
                            self.assertEqual((calls[0][4].x,calls[0][4].y),(mouse[0]-5,mouse[1]-5))
                            self.assertEqual(disposals,[True])
                            self.assertEqual(len(timers),1)
                            checks += 1
        self.assertEqual(checks,150)

    def test_explicit_positions_and_disabled_actions_preserved(self):
        for filename in FILES:
            with self.subTest(file=filename):
                this, calls, _, _, _, _ = execute_menu(CANDIDATES/filename,(3840,2088),
                                                      labels=('使用','批量使用'), explicit=(240,330),
                                                      disabled=['批量使用'])
                self.assertEqual((this._menuSp.x,this._menuSp.y),(240,330))
                self.assertEqual([b.enabled for b in this._menuSp.children],[True,False])
                self.assertEqual((calls[0][4].x,calls[0][4].y),(240,330))

    def test_original_chest_init_reproduces_oversized_view(self):
        this, _, _ = execute_init(WEB/'ModuleBag.swf',(3840,2088))
        self.assertEqual((this.view.width,this.view.height),(3840,2088))
        self.assertNotEqual((this.view.width,this.view.height),(1440,900))

    def test_candidate_chest_view_uses_logical_size_at_all_scales(self):
        for native in ((1440,900),(1920,1080),(3840,2088),(2162,1360),(800,600)):
            with self.subTest(native=native):
                this, children, supers = execute_init(CANDIDATES/'ModuleBag.swf',native)
                self.assertEqual((this.view.width,this.view.height),(1440,900))
                self.assertEqual(this.view.alpha,0)
                self.assertEqual(this.cacheCardList,[])
                self.assertEqual(this.cacheCardMCList,[])
                self.assertEqual(children,[this.display])
                self.assertFalse(this.display.mouseEnabled)
                self.assertEqual(supers,[True])

    def test_menu_and_init_methods_have_valid_control_flow_stacks(self):
        for filename in FILES:
            path = CANDIDATES/filename
            runtime_vm = BagVM(path)
            abc, info = read(path)
            cls, body = runtime_vm.method(CLASS,'show')
            mid = body['method']
            self.assertEqual(abc[info.method_bodies[mid][1]:info.method_bodies[mid][1]+info.method_bodies[mid][2]],body['code'])
            ex,_,_ = body_tail(abc,info,mid)
            result = verify_method(body['code'],info.method_headers[mid],info,ex)
            self.assertGreater(result['reachable_count'],50)
            if filename == 'ModuleBag.swf':
                _, body = runtime_vm.method(INIT_CLASS,'init')
                mid = body['method']
                ex,_,_ = body_tail(abc,info,mid)
                verify_method(body['code'],info.method_headers[mid],info,ex)

    def test_every_other_body_header_pool_and_trait_remains_identical(self):
        for filename in FILES:
            before, bi = read(WEB/filename)
            after, ai = read(CANDIDATES/filename)
            runtime_vm = BagVM(CANDIDATES/filename)
            _, body = runtime_vm.method(CLASS,'show')
            changed = {body['method']}
            if filename == 'ModuleBag.swf':
                _, body = runtime_vm.method(INIT_CLASS,'init')
                changed.add(body['method'])
            self.assertEqual(bi.strings,ai.strings)
            self.assertEqual(bi.namespaces,ai.namespaces)
            self.assertEqual(bi.multinames,ai.multinames)
            self.assertEqual(bi.class_methods,ai.class_methods)
            self.assertEqual(bi.method_bodies.keys(),ai.method_bodies.keys())
            self.assertEqual(len(before),len(after))
            allowed = set()
            for mid in changed:
                _,at,size = bi.method_bodies[mid]
                self.assertEqual(bi.method_bodies[mid],ai.method_bodies[mid])
                allowed.update(range(at,at+size))
            altered = {i for i,(old,new) in enumerate(zip(before,after)) if old != new}
            self.assertTrue(altered)
            self.assertTrue(altered <= allowed,(filename,sorted(altered-allowed)))
            for mid in bi.method_bodies:
                self.assertEqual(bi.method_headers[mid][1:],ai.method_headers[mid][1:])
                if mid in changed:
                    continue
                a = bi.method_headers[mid][0]
                _,_,b = body_tail(before,bi,mid)
                ca = ai.method_headers[mid][0]
                _,_,cb = body_tail(after,ai,mid)
                self.assertEqual(before[a:b],after[ca:cb],(filename,mid))

if __name__ == '__main__':
    class JsonResult(unittest.TextTestResult):
        records = []

        def addSuccess(self, test):
            super().addSuccess(test)
            self.records.append(dict(test=test.id(),status='passed'))

        def addFailure(self, test, err):
            super().addFailure(test,err)
            self.records.append(dict(test=test.id(),status='failed',error=self._exc_info_to_string(err,test)))

        def addError(self, test, err):
            super().addError(test,err)
            self.records.append(dict(test=test.id(),status='error',error=self._exc_info_to_string(err,test)))

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test,subtest,err)
            if err:
                self.records.append(dict(test=test.id(),subtest=str(subtest),status='failed',
                                         error=self._exc_info_to_string(err,test)))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BagActualBytecodeRegression)
    result = unittest.TextTestRunner(verbosity=2,resultclass=JsonResult).run(suite)
    summary = dict(passed=result.wasSuccessful(),test_count=result.testsRun,
                   menu_coordinate_scenarios=150,explicit_position_scenarios=5,
                   chest_size_scenarios=5,original_error_reproductions=2,
                   canonical_scan_files=154,qpopup_definitions=5,
                   baseline_webroot=str(WEB),candidate_webroot=str(CANDIDATES),
                   real_flash_ui_tested=False,account_requests_sent=0,
                   results=result.records)
    (HERE/'bag_validation_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() else 1)
