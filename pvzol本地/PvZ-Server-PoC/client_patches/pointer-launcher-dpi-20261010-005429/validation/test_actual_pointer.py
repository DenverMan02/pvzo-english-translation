"""Execute all patched mouse callbacks and the actual AVM2 tile search.

Native display transforms and inert business command endpoints are doubles.
Every callback and nested getRoadTileByScreenPos executes real SWF bytecode.
No renderer, game server, player state or account is used.
"""
from pathlib import Path
from types import SimpleNamespace as NS
import hashlib
import json
import sys
import unittest
from actual_vm import VM

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent/'plant-alignment'
BEFORE=ROOT/'ModuleTD_p.current.swf'
AFTER=ROOT/'ModuleTD_p.pointer.swf'
MANIFEST=json.loads((ROOT/'ModuleTD_p.pointer.json').read_text(encoding='utf-8'))
NATIVES=((1440,900),(1920,1080),(3840,2088),(2162,1360),(800,600))
SCALE=4/3
SCENE_X=-50

class Point(NS):
    def __init__(self,x=0,y=0):super().__init__(x=x,y=y)
class Rect(NS):
    def __init__(self,x,y,w,h):super().__init__(x=x,y=y,width=w,height=h)
    def contains(self,x,y):return self.x<=x<self.x+self.width and self.y<=y<self.y+self.height
class Jewel:pass

def fixture(path,native):
    vm=VM(path)
    window_scale=min(native[0]/1440,native[1]/900)
    offset=Point((native[0]-1440*window_scale)/2,(native[1]-900*window_scale)/2)
    transforms,queries,commands=[],[],[]
    def normalize(p):
        result=Point((p.x-offset.x)/window_scale,(p.y-offset.y)/window_scale)
        transforms.append((p.x,p.y,result.x,result.y))
        return result
    effect=NS(globalToLocal=lambda p:Point(p.x-SCENE_X,p.y),
              localToGlobal=lambda p:Point(p.x+SCENE_X,p.y))
    tiles=[NS(index=r*9+c,roadRow=r,roadCol=c,rect=Rect(230+c*75,90+r*100,75,100))
           for r in range(5) for c in range(9)]
    model=NS(roadList=[NS(tileList=tiles[r*9:(r+1)*9]) for r in range(5)],
             isRunningGame=True,dragMode=0,
             getPlantByTileIndex=lambda index:[],getEffectRectByRoadTile=lambda *a:Rect(0,0,1,1))
    abc=vm.abcs[0]
    def query(x,y):
        tile=vm.execute_body(abc,abc.body_of_method[1058],model,(x,y),label=('TDGameInfo','getRoadTileByScreenPos'))
        queries.append((x,y,tile.index if tile else None))
        return tile
    model.getRoadTileByScreenPos=query
    stage=NS(mouseX=0,mouseY=0)
    this=NS(cropPlant=Point(),shovel=Point(),energyTile=NS(x=0,y=0,contentW=80,contentH=90),
            targetFlag=Point(),_cursor=Point(),_point=Point(),cropPlantID=1,cropCardID=1,
            cardSlotRect=None,isUseSun=False,tipPlant=None,lastShowTileData=None,
            rangeCol=-1,rangeRow=-1,_selectedPlant=None,_counter=1,
            getShovelSelectedPlant=lambda *a:None,col=1,row=1,isTileAllowPlant=lambda tile:False,
            _curTileIndex=-1,hideSpecialSkillPanel=lambda:None)
    no=lambda *a:commands.append(a)
    manager=NS(removeCondition=no,addCondition=no,getCondition=lambda *a:False)
    jewel=Jewel()
    this.controler=NS(gemWar=NS(getGemNodeAt=lambda *a:NS(gem=jewel)))
    vm.globals.update({
        'flash.geom::Point':Point,'asgui.managers::SystemManager':NS(instance=NS(globalToLocal=normalize)),
        'com.qq::GameGloble':NS(stage=stage,scaleCurrent=SCALE,xForScene=SCENE_X,injector=NS(getInstance=lambda *a:this)),
        'com.qq.modules.td.logic::TDUILayerManager':NS(effectLayer=effect),
        'com.qq.modules.td.model::TDGameInfo':NS(getInstance=lambda:model),
        'com.qq.modules.td.logic::TDCropUtil':NS(isPermitAddPlant=lambda *a:0),
        'com.qq.modules.td.logic.mouseAction::TDCropPlantAction':object,
        'com.qq.modules.td.data::TDDataFactory':NS(instance=NS(getPlantInfoByID=lambda *a:NS(baseInfo=NS(id=1)))),
        'com.qq.modules.td.logic.talent::TalentManager':NS(instance=NS(getMeragePlanType=lambda *a:0)),
        'com.qq.modules.td.logic::TDConditionTriggerManager':NS(instance=manager),
        'com.qq.constant.td::TDConditionID':NS(GUIDE_PLANT_CARD_GRAB_UP=1,PLANT_CARD_GRAB_UP=2,GUIDE_PLANT_CARD_DROP_DOWN=3,PLANT_CARD_DROP_DOWN=4),
        'com.qq.constant.td::TDConstant':NS(MouseDragMode_None=0),
        'com.qq.modules.td.command.game::TDGameObjectCmd':NS(addPlant=no),
        'com.qq.modules.td.logic::TDSpecialSkillTrigger':NS(getInstance=lambda:NS(updateState=no)),
        'org.as3commons.logging.api::getLogger':lambda *a:NS(info=no),
        'com.qq.modules.td.command.game.sound::TDSoundCmd':NS(cleanPlant=no),
        'com.qq.modules.td.command.game.drag::TDMouseActionCmd':NS(endUseShovel=no,endChooseSkillArea=no),
        'com.qq.utils::CommandDispatcher':NS(send=no),
        'com.qq::CommandName':NS(TD_HideSkillArea='hide',TD_ShowSkillArea='show'),
        'com.qq.modules.td.model::TDMiniGameJewelsData':NS(instance=NS(getIdxyData=lambda tile:Point(tile.roadCol,tile.roadRow))),
        'com.qq.modules.td.logic.miniGame.jewels::JewelBase':Jewel,
    })
    def input_point(tile_point):
        app=Point(SCALE*(tile_point.x+SCENE_X),SCALE*tile_point.y)
        physical=Point(offset.x+window_scale*app.x,offset.y+window_scale*app.y)
        return app,physical
    def run(target,physical):
        stage.mouseX,stage.mouseY=physical.x,physical.y
        this._point=Point()
        this._curTileIndex=-1
        this.lastShowTileData=None
        event=NS(stageX=physical.x,stageY=physical.y)
        args=(False,) if target['method_id']==1489 else (() if target['method_id']==7586 else (event,))
        return vm.execute_body(abc,abc.body_of_method[target['method_id']],this,args,label=(target['classname'],target['name']))
    return NS(vm=vm,this=this,tiles=tiles,queries=queries,transforms=transforms,commands=commands,
              run=run,input=input_point,normalize=normalize,offset=offset,window_scale=window_scale)

class PointerBehaviorTests(unittest.TestCase):
    def test_all_eleven_tile_callbacks_match_identity_window_and_real_tile_search(self):
        count=0
        records=[]
        for native in NATIVES:
            f=fixture(AFTER,native)
            reference=fixture(BEFORE,(1440,900))
            for row,col in ((0,0),(0,8),(2,4),(4,0),(4,8)):
                tile=f.tiles[row*9+col]
                for dx,dy in ((1,1),(37.5,50),(74,99)):
                    app,physical=f.input(Point(tile.rect.x+dx,tile.rect.y+dy))
                    for target in MANIFEST['methods']:
                        if target['detail'].get('native_cursor_only'):continue
                        with self.subTest(native=native,mid=target['method_id'],tile=tile.index,dx=dx,dy=dy):
                            old_result=reference.run(target,app)
                            result=f.run(target,physical)
                            self.assertEqual(f.queries[-1][2],tile.index)
                            rx,ry,index=reference.queries[-1]
                            x,y,_=f.queries[-1]
                            self.assertAlmostEqual(x,rx,places=7)
                            self.assertAlmostEqual(y,ry,places=7)
                            self.assertEqual(index,tile.index)
                            if target['method_id']==7586:self.assertIsInstance(result,Jewel)
                            if target['method_id']==472:
                                self.assertAlmostEqual(f.this.cropPlant.x+SCENE_X,app.x)
                                self.assertAlmostEqual(f.this.cropPlant.y,app.y)
                                self.assertAlmostEqual(f.this.cropPlant.scaleX,SCALE)
                            if target['method_id']==722:
                                self.assertAlmostEqual(f.this.shovel.x,app.x)
                                self.assertAlmostEqual(f.this.shovel.y,app.y)
                            if target['method_id']==317:
                                self.assertAlmostEqual(f.this.targetFlag.x,reference.this.targetFlag.x)
                                self.assertAlmostEqual(f.this.targetFlag.y,reference.this.targetFlag.y)
                            count+=1
                            records.append(dict(native=native,mid=target['method_id'],tile=index,x=x,y=y))
        self.assertEqual(count,825)
        (HERE/'pointer_tile_cases.json').write_text(json.dumps(records,indent=2),encoding='utf-8')

    def test_area_outside_tile_normalizes_fallback_once_and_valid_tile_skips_second_conversion(self):
        target=next(t for t in MANIFEST['methods'] if t['method_id']==317)
        for native in NATIVES:
            f=fixture(AFTER,native)
            reference=fixture(BEFORE,(1440,900))
            for point,expected_calls in ((Point(500,250),1),(Point(10,10),2)):
                app,physical=f.input(point)
                start=len(f.transforms)
                f.run(target,physical)
                reference.run(target,app)
                self.assertEqual(len(f.transforms)-start,expected_calls)
                self.assertAlmostEqual(f.this.targetFlag.x,reference.this.targetFlag.x)
                self.assertAlmostEqual(f.this.targetFlag.y,reference.this.targetFlag.y)

    def test_both_native_hammer_cursors_match_mouse_under_outer_transform(self):
        count=0
        for native in NATIVES:
            f=fixture(AFTER,native)
            for app in (Point(0,0),Point(1440,900),Point(720,450),Point(30,870),Point(1410,30)):
                physical=Point(f.offset.x+f.window_scale*app.x,f.offset.y+f.window_scale*app.y)
                for target in MANIFEST['methods']:
                    if not target['detail'].get('native_cursor_only'):continue
                    f.run(target,physical)
                    self.assertAlmostEqual(f.this._cursor.x,app.x)
                    self.assertAlmostEqual(f.this._cursor.y,app.y)
                    count+=1
        self.assertEqual(count,50)

    def test_original_thirteen_methods_reproduce_fullscreen_position_errors(self):
        failures=[]
        point=Point(230+4*75+37.5,90+2*100+50)
        for target in MANIFEST['methods']:
            f=fixture(BEFORE,(3840,2088))
            app,physical=f.input(point)
            f.run(target,physical)
            if target['detail'].get('native_cursor_only'):
                wrong=abs(f.this._cursor.x-app.x)>1 or abs(f.this._cursor.y-app.y)>1
            else:
                wrong=f.queries[-1][2]!=22
            self.assertTrue(wrong,target)
            failures.append(target['method_id'])
        self.assertEqual(len(failures),13)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PointerBehaviorTests))
    summary=dict(passed=result.wasSuccessful(),test_count=result.testsRun,
                 true_avm2_callback_cases=825,true_avm2_reference_cases=825,
                 true_avm2_native_cursor_cases=50,true_avm2_area_branch_cases=10,
                 old_bytecode_error_reproductions=13,
                 actual_nested_tile_method='TDGameInfo.getRoadTileByScreenPos (method1058)',
                 native_display_transforms='synthetic SystemManager and MixLayer affine interfaces',
                 tile_map='synthetic 5 roads x 9 Rectangle tiles; actual bytecode enumeration and contains calls',
                 window_sizes=NATIVES,internal_scale=SCALE,scene_x=SCENE_X,
                 real_flash_gpu_tested=False,real_accounts_used=False,
                 candidate_sha256=hashlib.sha256(AFTER.read_bytes()).hexdigest(),
                 failures=[dict(test=str(t),traceback=trace) for t,trace in result.failures],
                 errors=[dict(test=str(t),traceback=trace) for t,trace in result.errors])
    (HERE/'pointer_behavior_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() else 1)
