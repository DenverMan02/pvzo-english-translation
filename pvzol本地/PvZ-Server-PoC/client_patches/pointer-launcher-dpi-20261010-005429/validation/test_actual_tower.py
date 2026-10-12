"""Real AVM2 regressions for viewport ownership and context/buffer recovery.

Synthetic Context3D/buffers emulate the exact disposed-resource contract from
the attached error, with no account, native renderer or game requests.
"""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import os
import sys
import unittest
from actual_vm import VM, Rectangle

HERE = Path(__file__).resolve().parent
BASELINE = Path(os.environ.get('PVZ_BASELINE_WEBROOT',str(HERE.parent/'baseline')))
TOWER = Path(os.environ.get('PVZ_TOWER_CANDIDATE_WEBROOT',str(HERE.parent/'tower')))
STARLING = 'starling.core::Starling'
QUAD = 'starling.display::QuadBatch'
TD = 'com.qq.modules.td.view.game::TDBGViewMediator'


class Buffer(NS):
    def __init__(self, kind):
        super().__init__(kind=kind,disposed=False,uploads=[],dispose_count=0)

    def dispose(self):
        self.disposed = True
        self.dispose_count += 1

    def uploadFromVector(self, *args):
        if self.disposed:
            raise RuntimeError('Error #3694: Object previously disposed before uploadFromVector')
        self.uploads.append(args)


class Context(NS):
    def __init__(self):
        super().__init__(allocated=[])

    def createVertexBuffer(self, count, elements):
        buffer = Buffer('vertex')
        self.allocated.append((count,elements,buffer))
        return buffer

    def createIndexBuffer(self, count):
        buffer = Buffer('index')
        self.allocated.append((count,buffer))
        return buffer


class Layer(NS):
    def __init__(self, **kwargs):
        super().__init__(x=0,y=0,scaleX=1,scaleY=1,normalX=0,**kwargs)

    def setLocation(self,x,y):
        self.x,self.y = x,y

    def setNormalX(self,x):
        self.normalX = x

    def updateLocation(self,force):
        self.updated = force


class Point(NS):
    def __init__(self,x=0,y=0):
        super().__init__(x=x,y=y)


def quad_fixture(path, vertices=4):
    vm = VM(path)
    context = Context()
    old_vertex,old_index = Buffer('vertex'),Buffer('index')
    this = NS(mVertexData=NS(numVertices=vertices,rawData=[0.0]*vertices*8),
              mIndexData=[0,1,2,1,3,2] if vertices else [],
              mVertexBuffer=old_vertex,mIndexBuffer=old_index,mSyncRequired=True)
    programs = []
    this.registerPrograms = lambda:programs.append(True)
    this.createBuffers = lambda:vm.execute(QUAD,'createBuffers',this)
    vm.globals.update({'starling.core::Starling':NS(context=context),
                       'starling.utils::VertexData':NS(ELEMENTS_PER_VERTEX=8),
                       'starling.errors::MissingContextError':RuntimeError})
    return vm,this,context,old_vertex,old_index,programs


def starling_fixture(vm,quad,shared=False,rendering=True):
    init_calls,renders = [],[]
    def render():
        renders.append(True)
        vm.execute(QUAD,'syncBuffers',quad)
    this = NS(makeCurrent=lambda:init_calls.append('current'),
              initializeGraphicsAPI=lambda:init_calls.append('graphics'),
              initializeRoot=lambda:init_calls.append('root'),
              mTouchProcessor=NS(simulateMultitouch=None),mSimulateMultitouch=True,
              mShareContext=shared,mRendering=rendering,render=render,mLastFrameTimestamp=None)
    vm.globals['flash.utils::getTimer'] = lambda:2500
    return this,init_calls,renders


def execute_checksize(path,native,debug=False):
    vm = VM(path)
    scale = min(native[0]/1440,native[1]/900)
    viewport = Rectangle((native[0]-1440*scale)/2,(native[1]-900*scale)/2,
                         1440*scale,900*scale)
    starling = NS(viewPort=viewport,stage=NS(stageWidth=1440,stageHeight=900))
    globle = NS(starling=starling,TDScreenW=900,TDScreenH=675,debugUIGraphics=debug)
    graphics_calls,logs = [],[]
    graphics = NS(clear=lambda:graphics_calls.append(('clear',)),
                  lineStyle=lambda *args:graphics_calls.append(('lineStyle',*args)),
                  drawRect=lambda *args:graphics_calls.append(('drawRect',*args)))
    scene = Layer(unitLayer=Layer(),gameUILayer=Layer(),skyLayer=Layer())
    this = NS(view=NS(scene=scene,topScence=Layer(),debugLayer=NS(graphics=graphics)))
    vm.globals.update({'com.qq::GameGloble':globle,
                       'flash.geom::Point':Point,
                       'com.qq.constant.td::TDConstant':NS(passWarfieldPosX=100),
                       'org.as3commons.logging.api::getLogger':lambda name:NS(info=lambda value:logs.append(value)),
                       'com.qq.modules.td.model::TDGameInfo':NS(getInstance=lambda:NS(roadList=None))})
    vm.execute(TD,'checkSize',this,(900/675,1440,900))
    return starling,viewport,this,globle,graphics_calls,logs


class TowerActualBytecodeRegression(unittest.TestCase):
    def test_original_td_overwrites_fullscreen_viewport(self):
        starling,viewport,_,_,_,_ = execute_checksize(BASELINE/'ModuleTD_p.swf',(3840,2088))
        self.assertIsNot(starling.viewPort,viewport)
        self.assertEqual((starling.viewPort.x,starling.viewPort.y,starling.viewPort.width,starling.viewPort.height),(0,0,1440,900))
        self.assertNotEqual((viewport.width,viewport.height),(1440,900))

    def test_candidate_td_preserves_physical_viewport_and_scene_layout(self):
        for native in ((1440,900),(1920,1080),(3840,2088),(2162,1360),(800,600)):
            for debug in (False,True):
                with self.subTest(native=native,debug=debug):
                    old = execute_checksize(BASELINE/'ModuleTD_p.swf',native,debug)
                    new = execute_checksize(TOWER/'ModuleTD_p.tower.swf',native,debug)
                    starling,viewport,this,globle,graphics_calls,logs = new
                    self.assertIs(starling.viewPort,viewport)
                    self.assertAlmostEqual(starling.stage.stageWidth,1080)
                    self.assertEqual(starling.stage.stageHeight,675)
                    self.assertEqual((this.view.scene.x,this.view.scene.y),(old[2].view.scene.x,old[2].view.scene.y))
                    for layername in ('unitLayer','gameUILayer','skyLayer'):
                        a,b = getattr(this.view.scene,layername),getattr(old[2].view.scene,layername)
                        for key in ('x','y','normalX','scaleX','scaleY'):
                            self.assertEqual(getattr(a,key),getattr(b,key))
                    self.assertEqual((this.view.topScence.x,this.view.topScence.scaleX,this.view.topScence.scaleY),
                                     (old[2].view.topScence.x,old[2].view.topScence.scaleX,old[2].view.topScence.scaleY))
                    self.assertEqual((globle.xForScene,globle.xAddForUI),(old[3].xForScene,old[3].xAddForUI))
                    self.assertEqual(graphics_calls,old[4])
                    self.assertEqual(len(logs),1)

    def test_original_context_initialize_renders_disposed_quad_early(self):
        vm,quad,_,old_vertex,old_index,_ = quad_fixture(BASELINE/'Game_p.swf')
        old_vertex.dispose()
        old_index.dispose()
        this,_,_ = starling_fixture(vm,quad)
        with self.assertRaisesRegex(RuntimeError,'#3694'):
            vm.execute(STARLING,'initialize',this)

    def test_candidate_initialize_waits_for_quad_context_callbacks(self):
        vm,quad,context,_,_,programs = quad_fixture(TOWER/'Game_p.tower.swf')
        this,init_calls,renders = starling_fixture(vm,quad)
        # Repeated context losses model maximize, restore and minimize/return.
        for _ in range(4):
            quad.mVertexBuffer.dispose()
            quad.mIndexBuffer.dispose()
            before = len(renders)
            vm.execute(STARLING,'initialize',this)
            self.assertEqual(len(renders),before)
            self.assertEqual(init_calls[-3:],['current','graphics','root'])
            self.assertTrue(this.mTouchProcessor.simulateMultitouch)
            self.assertEqual(this.mLastFrameTimestamp,2.5)
            vm.execute(QUAD,'onContextCreated',quad,(object(),))
            this.render()
            self.assertEqual(len(renders),before+1)
            self.assertFalse(quad.mVertexBuffer.disposed)
            self.assertFalse(quad.mIndexBuffer.disposed)
        self.assertEqual(len(context.allocated),8)
        self.assertEqual(len(programs),4)

    def test_original_empty_quad_retains_disposed_buffers(self):
        vm,this,_,old_vertex,old_index,_ = quad_fixture(BASELINE/'Game_p.swf',0)
        vm.execute(QUAD,'createBuffers',this)
        self.assertIs(this.mVertexBuffer,old_vertex)
        self.assertIs(this.mIndexBuffer,old_index)
        self.assertTrue(old_vertex.disposed)
        self.assertTrue(old_index.disposed)
        with self.assertRaisesRegex(RuntimeError,'#3694'):
            vm.execute(QUAD,'syncBuffers',this)

    def test_candidate_empty_quad_clears_buffers_and_sync_is_safe(self):
        vm,this,context,old_vertex,old_index,_ = quad_fixture(TOWER/'Game_p.tower.swf',0)
        vm.execute(QUAD,'createBuffers',this)
        self.assertIsNone(this.mVertexBuffer)
        self.assertIsNone(this.mIndexBuffer)
        self.assertEqual((old_vertex.dispose_count,old_index.dispose_count),(1,1))
        self.assertEqual(context.allocated,[])
        vm.execute(QUAD,'syncBuffers',this)
        self.assertEqual((old_vertex.dispose_count,old_index.dispose_count),(1,1))
        self.assertEqual(context.allocated,[])

    def test_candidate_positive_quad_allocates_and_uploads_fresh_buffers(self):
        vm,this,context,old_vertex,old_index,_ = quad_fixture(TOWER/'Game_p.tower.swf',4)
        vm.execute(QUAD,'createBuffers',this)
        self.assertIsNot(this.mVertexBuffer,old_vertex)
        self.assertIsNot(this.mIndexBuffer,old_index)
        self.assertEqual((old_vertex.dispose_count,old_index.dispose_count),(1,1))
        self.assertEqual(this.mVertexBuffer.uploads,[(this.mVertexData.rawData,0,4)])
        self.assertEqual(this.mIndexBuffer.uploads,[(this.mIndexData,0,6)])
        self.assertFalse(this.mSyncRequired)
        self.assertEqual(len(context.allocated),2)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TowerActualBytecodeRegression)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = dict(passed=result.wasSuccessful(),test_count=result.testsRun,
                   old_error_reproductions=3,viewport_scenarios=10,context_recovery_cycles=4,
                   baseline=str(BASELINE),candidate=str(TOWER),real_flash_ui_tested=False,
                   account_requests_sent=0,
                   failures=[dict(test=t.id(),traceback=trace) for t,trace in result.failures],
                   errors=[dict(test=t.id(),traceback=trace) for t,trace in result.errors])
    (HERE/'tower_behavior_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() else 1)
