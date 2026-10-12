"""World-map/ModuleExped wire, state, replay and isolation acceptance checks.

Uses synthetic saves only. These checks do not claim Flash UI gameplay success.
Run in a fresh process: python -m unittest discover -s tests -p test_worldmap_protocol.py -v
"""
import copy
import concurrent.futures
import hashlib
import importlib
import io
import json
from pathlib import Path
import sqlite3
import struct
from contextlib import closing
import threading
import unittest
from unittest.mock import patch
import urllib.request
from worldmap_test_support import IsolatedServer, fields, many, message, one


class WorldMapProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa=IsolatedServer()
        cls.g=cls.qa.game
        cls.wm=importlib.import_module('worldmap_local')
        cls.adapter=cls.g._worldmap_adapter
        cls.serial=0

    @classmethod
    def tearDownClass(cls):
        cls.qa.close()

    def setUp(self):
        type(self).serial+=1
        self.p=self.qa.player(99007000+self.serial,level=100,coin=20000,role_created=1)
        self.now=1800000000
        self.clock=patch.object(self.wm,'_now',side_effect=lambda:self.now)
        self.clock.start();self.addCleanup(self.clock.stop)
        self.g.rebuild_td_roster(self.p);self.g.ensure_player_cards(self.p)
        self.assign_team(self.wm.TEAM_ID)  # deliberate owned-card transfer; map team starts empty
        # This suite covers dispatch/replay after the real declaration flow.
        # Neutral/declaring boundary coverage lives in the lifecycle suite.
        for npc in self.wm.NPCS:
            self.call('War_DeclareWar',self.v(1,npc['role_id']))
        self.now += self.wm.DECLARE_WAIT_SECONDS+2

    def call(self,name,data=b'',player=None):
        result=self.qa.invoke(name,data,player or self.p)
        for kind,body in result:
            self.assertIsInstance(body,bytes,kind)
            self.assertTrue((self.qa.project / "Assets/src/PVZ/Cmd" / (kind.rsplit(".",1)[-1]+".as")).is_file(), "Unknown AS message: "+kind)
            fields(body)
        return result

    def v(self,field,value): return self.g.pb_varint_field(field,value)
    def m(self,field,value): return self.g.pb_bytes(field,value)
    def pos(self,x,y): return self.v(1,x)+self.v(2,y)
    def assign_team(self,team,placements=None):
        if placements is None:
            cards=self.qa.store.get_cards(self.p['uin'])
            placements=dict(zip(self.g._FORMATION_POS,[row[0] for row in cards]))
        dto=b''.join(self.m(1,self.v(1,pos)+self.g.pb_int64_field(2,uid)) for pos,uid in placements.items())+self.v(5,team)
        replies=self.call('Battle_SetFormation',self.m(1,dto))
        message(replies,'Battle_GetFormation')
        return placements

    def dispatch_data(self,npc=0,team=None):
        target=self.wm.NPCS[npc]['pos']
        return self.v(1,2)+self.m(2,self.pos(*target))+self.v(3,team or self.wm.TEAM_ID)
    def mission(self):
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.row_factory=sqlite3.Row
            row=con.execute('SELECT * FROM local_worldmap_missions WHERE uin=? ORDER BY id DESC LIMIT 1',(self.p['uin'],)).fetchone()
            return dict(row) if row else None
    def state(self):
        with self.adapter.db(self.p) as con:return self.adapter.state(con,self.p)
    def coin(self): return self.qa.store.get_or_create_player(self.p['uin'])[0]['coin']
    def empty_city_site(self,area=1):
        # The shared synthetic save now contains real towns from earlier tests.
        # Select a legal unoccupied site from the actual viewport response.
        cells=many(message(self.call('WorldMap_GetInfo',self.v(5,area)),'WorldMap_GetInfo'),1)
        occupied={(one(one(cell,2),1),one(one(cell,2),2)) for cell in cells}
        coordinates=range(self.wm.MAP_SIZES[area]-7,4,-6)
        return next((x,y) for x in coordinates for y in coordinates if (x,y) not in occupied)
    def assert_error(self,msgs): self.assertTrue(any(k.endswith('.Cmd_LogicError_SC') for k,_ in msgs),msgs)
    def dispatch(self,npc=0):
        messages=self.call('War_SetOut',self.dispatch_data(npc))
        self.assertEqual(one(message(messages,'War_SetOut'),1),self.wm.TEAM_ID)
        return self.mission()

    def test_high_field_protobuf_tags_match_server_codec(self):
        for number in (1,15,16,30,50,127,128):
            with self.subTest(field=number):
                encoded=self.wm._m(number,b"checked")
                self.assertEqual(encoded,self.g.pb_bytes(number,b"checked"))
                self.assertEqual(one(encoded,number),b"checked")
                self.assertEqual(self.wm._fields(encoded)[number],[(2,b"checked")])

    def test_city_viewport_cell_consistent_type_area_and_coordinates(self):
        init=message(self.call('City_Init'),'City_Init')
        self.assertEqual(one(init,2),1)
        view=message(self.call('WorldMap_GetInfo',self.v(5,1)),'WorldMap_GetInfo')
        cells=many(view,1);self.assertGreaterEqual(len(cells),2)
        for cell in cells:
            self.assertIn(one(cell,1),(1,2,3));self.assertEqual(one(cell,15),1)
            for nested in (2,13,14,16):self.assertIsInstance(one(cell,nested),bytes)
        own=next(c for c in cells if one(c,3)==self.p['role_id'])
        detail=one(message(self.call('WorldMap_GetCellInfo',self.v(1,self.p['role_id'])),'WorldMap_GetCellInfo'),1)
        self.assertEqual(one(own,2),one(detail,2));self.assertEqual(one(own,15),one(detail,15))
        self.assertEqual(one(own,2),one(init,1));self.assertEqual(one(own,1),1)

    def test_supported_area_switch_and_unsupported_area_no_invalid_cells(self):
        for area in (1,2):
            body=message(self.call('WorldMap_GetInfo',self.v(5,area)),'WorldMap_GetInfo')
            self.assertTrue(many(body,1))
            self.assertTrue(all(one(c,15)==area for c in many(body,1)))
        for area in (3,4,999):
            msgs=self.call('WorldMap_GetInfo',self.v(5,area))
            for kind,body in msgs:
                if kind.endswith('WorldMap_GetInfo_SC'):
                    self.assertTrue(all(one(c,15) in (1,2) for c in many(body,1)))
        self.assertEqual(self.state()['area'],1,'viewing another area must not move own city')

    def test_show_build_sites_never_sends_client_local_type_eleven_as_network_cell(self):
        self.call('City_SetNullShow',self.v(1,1))
        for area in (1,2):
            body=message(self.call('WorldMap_GetInfo',self.v(5,area)),'WorldMap_GetInfo')
            self.assertTrue(all(one(cell,1) in (1,2,3) for cell in many(body,1)),
                            'AS MapProxy network cell factory only accepts city/mine/temple')
        self.assertEqual(self.state()['show_null'],1)
        self.call('City_SetNullShow',self.v(1,0));self.assertEqual(self.state()['show_null'],0)

    def test_history_target_uint32_id_does_not_contain_int64_role_identity(self):
        row=self.dispatch()
        history=one(message(self.call('War_GetHistory'),'War_GetHistory'),1)
        for number,role in ((2,self.p['role_id']),(3,self.wm.NPCS[0]['role_id'])):
            target=one(history,number)
            self.assertLessEqual(one(target,2),0xffffffff,'Dto_War_Target.id is UINT32')
            self.assertEqual(one(one(target,4),3),role,'roleInfo.roleId preserves full Int64 identity')

    def test_resource_entry_preflight_matches_embattle_chain_without_spending(self):
        self.state();before_coin=self.coin();before_items=self.qa.store.get_item_count(self.p['uin'],self.wm.MOVE_ITEM)
        for war_type in (2,4):
            command=self.v(1,war_type)+self.m(2,self.pos(20,20))
            state=self.state()
            self.assertEqual(one(message(self.call('War_PreSetOut',command),'War_PreSetOut'),1),
                             self.adapter.period((state['x'],state['y']),(20,20)))
        self.call('City_ChangeArea',self.v(1,2))
        temple=self.adapter.resources.temple(temple_id=6)
        for war_type in (2,4):
            command=self.v(1,war_type)+self.m(2,self.pos(*temple['pos']))
            self.assertGreater(one(message(self.call('War_PreSetOut',command),'War_PreSetOut'),1),0)
        for war_type,pos in ((2,(719,719)),(99,(20,20)),(4,(24,18))):
            self.assert_error(self.call('War_PreSetOut',self.v(1,war_type)+self.m(2,self.pos(*pos))))
        self.assertEqual(self.coin(),before_coin);self.assertEqual(self.state()['used'],0);self.assertIsNone(self.mission())
        self.assertEqual(self.qa.store.get_item_count(self.p['uin'],self.wm.MOVE_ITEM),before_items)

    def test_all_network_cells_have_distinct_nonzero_int64_role_identities(self):
        for area in (1,2):
            cells=many(message(self.call('WorldMap_GetInfo',self.v(5,area)),'WorldMap_GetInfo'),1)
            ids=[one(c,3) for c in cells]
            self.assertTrue(all(0<rid<2**63 for rid in ids))
            self.assertEqual(len(ids),len(set(ids)),'AS compares roleId to decide cell equality')
            for cell in cells:
                if one(cell,1)==3:
                    template=self.adapter.resources.temple(temple_id=one(cell,50))
                    self.assertEqual(one(cell,7),{1:12,2:11}[template['type']], 'AS temple type maps to prop skin, not actor face')

    def test_viewport_bounds_and_unknown_city_never_impersonate_self(self):
        request=self.m(1,self.pos(0,0))+self.m(4,self.pos(1,1))+self.v(5,1)
        body=message(self.call('WorldMap_GetInfo',request),'WorldMap_GetInfo')
        self.assertEqual(many(body,1),[])
        for command in ('WorldMap_GetCellInfo','WorldMap_GetCityInfo'):
            self.assert_error(self.call(command,self.v(1,777777777777)))
        other=self.qa.player(99107000+self.serial,name='Unrelated synthetic player',coin=3456)
        # Current local maps include real saved accounts. A known account must
        # resolve its own identity; an unknown role must still fail above.
        detail=one(message(self.call('WorldMap_GetCellInfo',self.v(1,other['role_id'])),'WorldMap_GetCellInfo'),1)
        self.assertEqual(one(detail,3),other['role_id'])
        self.assertEqual(one(detail,4),b'Unrelated synthetic player')
        self.assertNotEqual(one(detail,3),self.p['role_id'])
        init=message(self.call('City_Init'),'City_Init')
        own_pos=one(init,1)
        bounded=self.m(1,own_pos)+self.m(4,own_pos)+self.v(5,one(init,2))
        cells=many(message(self.call('WorldMap_GetInfo',bounded),'WorldMap_GetInfo'),1)
        self.assertEqual([one(cell,3) for cell in cells],[self.p['role_id']])

    def test_edge_viewports_accept_signed_and_outside_corners(self):
        for area,lo,hi in ((1,(-10,-10),(30,30)),(1,(1900,1900),(2020,2020)),
                           (2,(-10,-10),(730,730)),(2,(-100,-100),(-10,-10))):
            with self.subTest(area=area,lo=lo,hi=hi):
                point=lambda xy:self.g.pb_int64_field(1,xy[0])+self.g.pb_int64_field(2,xy[1])
                req=self.m(1,point(lo))+self.m(4,point(hi))+self.v(5,area)
                msgs=self.call('WorldMap_GetInfo',req)
                self.assertFalse(any(k.endswith('LogicError_SC') for k,_ in msgs),msgs)
                body=message(msgs,'WorldMap_GetInfo')
                if hi[0]<0:self.assertEqual(many(body,1),[])
                if lo==(-10,-10):self.assertGreater(len(many(body,1)),0)
        bad=self.v(1,2)+self.m(2,self.g.pb_int64_field(1,-1)+self.v(2,18))+self.v(3,self.wm.TEAM_ID)
        self.assert_error(self.call('War_SetOut',bad))

    def test_client_default_team_one_dispatch_cannot_change_formation(self):
        self.assign_team(1)
        messages=self.call('War_SetOut',self.dispatch_data(team=1))
        self.assertEqual(one(message(messages,'War_SetOut'),1),1)
        body=message(self.call('Battle_GetFormation',self.v(1,1)),'Battle_GetFormation')
        home=next(x for x in many(body,2) if one(x,5)==1)
        self.assertEqual(one(home,6),1)
        before=dict(self.qa.store.get_formation(self.p['uin'],1))
        self.assert_error(self.call('Battle_SetFormation',self.m(1,home)))
        self.assertEqual(dict(self.qa.store.get_formation(self.p['uin'],1)),before)

    def test_default_home_team_rejects_invalid_duplicate_and_foreign_cards(self):
        self.assign_team(1)
        self.call('Battle_GetFormation',self.v(1,1))
        before=dict(self.qa.store.get_formation(self.p['uin'],1))
        uid=self.qa.store.get_cards(self.p['uin'])[0][0]
        for positions in (((999,uid),),((3,999999999),),((3,uid),(7,uid))):
            dto=b''.join(self.m(1,self.v(1,pos)+self.v(2,card)) for pos,card in positions)+self.v(5,1)
            self.assert_error(self.call('Battle_SetFormation',self.m(1,dto)))
            self.assertEqual(dict(self.qa.store.get_formation(self.p['uin'],1)),before)
        # A malformed legacy formation already on disk must not duplicate a plant at dispatch.
        self.qa.store.set_formation(self.p['uin'],1,{3:uid,7:uid})
        self.assert_error(self.call('War_SetOut',self.dispatch_data(team=1)))
        self.assertIsNone(self.mission())

    def test_heal_during_unlocked_combat_computation_is_not_overwritten_by_dispatch(self):
        placements=self.adapter.placements(self.p,self.wm.TEAM_ID)
        health={str(uid):5000 for uid in placements.values()}
        with self.adapter.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET health=? WHERE uin=?',(json.dumps(health),self.p['uin']))
        before=self.coin();original=self.adapter.make_report_details;called=[]
        def race(*args,**kwargs):
            report=original(*args,**kwargs)
            replies=self.call('War_RecoverHp',self.v(1,self.wm.TEAM_ID),copy.deepcopy(self.p))
            message(replies,'War_RecoverHp');called.append(True)
            return report
        with patch.object(self.adapter,'make_report_details',side_effect=race):
            self.assert_error(self.call('War_SetOut',self.dispatch_data()))
        self.assertEqual(called,[True]);self.assertIsNone(self.mission())
        self.assertEqual(self.coin(),before-len(placements)*self.wm.HEAL_COST_PER_CARD)
        self.assertTrue(all(v==10000 for v in json.loads(self.state()['health']).values()))

    def test_move_during_unlocked_combat_computation_invalidates_old_origin(self):
        destination=self.empty_city_site()
        self.qa.store.add_item(self.p['uin'],self.wm.MOVE_ITEM,1)
        before=self.qa.store.get_item_count(self.p['uin'],self.wm.MOVE_ITEM)
        original=self.adapter.make_report_details;called=[]
        def race(*args,**kwargs):
            report=original(*args,**kwargs)
            replies=self.call('City_FixedMove',self.v(1,1)+self.m(2,self.pos(*destination)),copy.deepcopy(self.p))
            message(replies,'City_FixedMove');called.append(True)
            return report
        with patch.object(self.adapter,'make_report_details',side_effect=race):
            self.assert_error(self.call('War_SetOut',self.dispatch_data()))
        self.assertEqual(called,[True]);self.assertIsNone(self.mission())
        self.assertEqual((self.state()['x'],self.state()['y']),destination)
        self.assertEqual(self.qa.store.get_item_count(self.p['uin'],self.wm.MOVE_ITEM),before-1)

    def split_teams(self,train=False):
        cards=[row[0] for row in self.qa.store.get_cards(self.p['uin'])]
        self.assertGreaterEqual(len(cards),4)
        if train:
            self.qa.train_owned_cards(self.p,level=100)
        first=dict(zip(self.g._FORMATION_POS,cards[:2]));second=dict(zip(self.g._FORMATION_POS,cards[2:4]))
        self.assign_team(1,first);self.assign_team(self.wm.TEAM_ID,second)
        return first,second

    def test_fresh_account_has_no_duplicate_default_map_team_cards(self):
        other=self.qa.player(99607000+self.serial,level=100,role_created=1)
        body=message(self.call('Battle_GetFormation',player=other),'Battle_GetFormation')
        teams=many(body,2);self.assertEqual([one(t,5) for t in teams],list(self.wm.ATTACK_TEAMS))
        home=next(t for t in teams if one(t,5)==1);extra=next(t for t in teams if one(t,5)==self.wm.TEAM_ID)
        self.assertTrue(many(home,1));self.assertEqual(many(extra,1),[])
        self.assertEqual(many(next(t for t in teams if one(t,5)==1002),1),[])
        ids=[one(c,2) for t in teams for c in many(t,1)]
        self.assertEqual(len(ids),len(set(ids)))

    def test_two_disjoint_city_missions_advance_and_return_together_once(self):
        primary,extra=self.split_teams(train=True);before=self.coin()
        first=self.call('War_SetOut',self.dispatch_data(npc=0,team=1))
        second=self.call('War_SetOut',self.dispatch_data(npc=1,team=self.wm.TEAM_ID))
        self.assertEqual(one(message(first,'War_SetOut'),1),1)
        self.assertEqual(one(message(second,'War_SetOut'),1),self.wm.TEAM_ID)
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.row_factory=sqlite3.Row;rows=[dict(r) for r in con.execute('SELECT * FROM local_worldmap_missions WHERE uin=?',(self.p['uin'],))]
        self.assertEqual(len(rows),2);self.assertEqual({r['team_id'] for r in rows},{1,self.wm.TEAM_ID})
        network=many(message(self.call('Battle_GetFormation'),'Battle_GetFormation'),2)
        self.assertEqual({one(t,5):one(t,6) for t in network},{1:1,self.wm.TEAM_ID:1,1002:0,1003:0,1004:0})
        ids=[one(c,2) for t in network for c in many(t,1)];self.assertEqual(len(ids),len(set(ids)))
        self.now=max(r['home_at'] for r in rows)+1
        history=many(message(self.call('War_GetHistory'),'War_GetHistory'),1)
        self.assertEqual(len(history),2);self.assertTrue(all(one(h,8)==self.wm.HOME for h in history))
        expected=before+sum(r['reward'] for r in rows if r['won'])
        self.assertEqual(self.coin(),expected)
        self.call('War_GetHistory',player=copy.deepcopy(self.p));self.assertEqual(self.coin(),expected)
        health=json.loads(self.state()['health']);self.assertTrue(set(map(str,set(primary.values())|set(extra.values())))<=set(health))

    def test_home_team_can_edit_and_heal_while_unrelated_disjoint_team_is_away(self):
        primary,extra=self.split_teams(train=True)
        self.call('War_SetOut',self.dispatch_data(team=1))
        health={str(uid):7000 for uid in extra.values()}
        with self.adapter.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET health=? WHERE uin=?',(json.dumps(health),self.p['uin']))
        before=self.coin();self.call('War_RecoverHp',self.v(1,self.wm.TEAM_ID))
        self.assertEqual(self.coin(),before-len(extra)*self.wm.HEAL_COST_PER_CARD)
        swapped=dict(zip(reversed(tuple(extra)),extra.values()));self.assign_team(self.wm.TEAM_ID,swapped)
        dto=one(message(self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID)),'Battle_GetFormation'),1)
        self.assertEqual(one(dto,6),0)
        self.assertEqual({one(c,1):one(c,2) for c in many(dto,1)},swapped)
        busy_uid=next(iter(primary.values()))
        bad=self.m(1,self.v(1,3)+self.g.pb_int64_field(2,busy_uid))+self.v(5,self.wm.TEAM_ID)
        self.assert_error(self.call('Battle_SetFormation',self.m(1,bad)))

    def test_city_mission_and_disjoint_mine_garrison_have_independent_locks(self):
        self.split_teams(train=True)
        mine=self.m(1,self.pos(20,20))
        capture=self.call('Mine_SetOutTeam',mine+self.v(2,self.wm.TEAM_ID))
        message(capture,'Mine_SetOutTeam')
        with self.adapter.resources.db(self.p) as con:
            journey=con.execute('SELECT arrive_at FROM local_worldmap_mine_missions WHERE uin=? AND team_id=?',
                                (self.p['uin'],self.wm.TEAM_ID)).fetchone()
            self.assertEqual(self.adapter.resources.mine(con,self.p,(20,20))['team_id'],0)
        self.now=journey['arrive_at']
        self.call('War_GetHistory')
        with self.adapter.resources.db(self.p) as con:
            self.assertEqual(self.adapter.resources.mine(con,self.p,(20,20))['team_id'],self.wm.TEAM_ID)
        city=self.call('War_SetOut',self.dispatch_data(team=1))
        self.assertEqual(one(message(city,'War_SetOut'),1),1)
        self.call('War_RecallTeam',self.v(1,1)+self.v(2,1))
        active=self.mission();self.now=active['home_at']+1;self.call('War_GetHistory')
        teams=many(message(self.call('Battle_GetFormation'),'Battle_GetFormation'),2)
        self.assertEqual({one(t,5):one(t,6) for t in teams},{1:0,self.wm.TEAM_ID:1,1002:0,1003:0,1004:0})
        self.call('Mine_RecallTeam',mine)
        with self.adapter.resources.db(self.p) as con:
            journey=con.execute('SELECT home_at FROM local_worldmap_mine_missions WHERE uin=? AND team_id=?',
                                (self.p['uin'],self.wm.TEAM_ID)).fetchone()
        self.now=journey['home_at']
        self.call('War_GetHistory')
        teams=many(message(self.call('Battle_GetFormation'),'Battle_GetFormation'),2)
        self.assertTrue(all(one(t,6)==0 for t in teams))

    def test_town_defense_selection_blocks_only_selected_team_and_emits_real_sync(self):
        self.split_teams(train=True)
        replies=self.call('Battle_SetDefendTeam',self.v(1,self.wm.TEAM_ID))
        self.assertTrue(all(not name.endswith('Battle_SetDefendTeam_SC') for name,_ in replies))
        teams=many(message(replies,'Battle_GetFormation'),2)
        self.assertEqual({one(t,5):one(t,8) for t in teams},{1:0,self.wm.TEAM_ID:1,1002:0,1003:0,1004:0})
        self.assertEqual(self.state()['defend_team'],self.wm.TEAM_ID)
        self.assert_error(self.call('War_SetOut',self.dispatch_data(team=self.wm.TEAM_ID)))
        self.assert_error(self.call('Mine_SetOutTeam',self.m(1,self.pos(20,20))+self.v(2,self.wm.TEAM_ID)))
        self.assert_error(self.call('Battle_CancelDefendTeam',self.v(1,1)))
        self.assertEqual(self.state()['defend_team'],self.wm.TEAM_ID)
        self.assertEqual(one(message(self.call('War_SetOut',self.dispatch_data(team=1)),'War_SetOut'),1),1)
        self.call('Battle_CancelDefendTeam',self.v(1,self.wm.TEAM_ID))
        self.assertEqual(self.state()['defend_team'],0)
        self.assertEqual(one(message(self.call('War_SetOut',self.dispatch_data(npc=1)),'War_SetOut'),1),self.wm.TEAM_ID)

    def test_town_defense_rejects_empty_or_away_team_and_only_one_flag_is_set(self):
        self.split_teams(train=True)
        self.call('Battle_SetDefendTeam',self.v(1,1))
        replies=self.call('Battle_SetDefendTeam',self.v(1,self.wm.TEAM_ID))
        teams=many(message(replies,'Battle_GetFormation'),2)
        self.assertEqual(sum(bool(one(t,8)) for t in teams),1)
        self.call('Battle_CancelDefendTeam',self.v(1,self.wm.TEAM_ID))
        self.call('War_SetOut',self.dispatch_data(team=1))
        self.assert_error(self.call('Battle_SetDefendTeam',self.v(1,1)))
        message(self.call('Battle_SetFormation',self.m(1,self.v(5,self.wm.TEAM_ID))),'Battle_GetFormation')
        self.assert_error(self.call('Battle_SetDefendTeam',self.v(1,self.wm.TEAM_ID)))

    def test_selected_map_leader_is_used_in_actual_combat_report(self):
        placements=self.adapter.placements(self.p,self.wm.TEAM_ID);leader=list(placements.values())[-1]
        dto=b''.join(self.m(1,self.v(1,pos)+self.g.pb_int64_field(2,uid)) for pos,uid in placements.items())+self.v(5,self.wm.TEAM_ID)+self.g.pb_int64_field(3,leader)
        self.call('Battle_SetFormation',self.m(1,dto))
        row=self.dispatch();before=many(one(bytes(row['report']),2),2)
        leaders=[one(card,1) for card in before if one(card,9)]
        self.assertEqual(leaders,[next(pos for pos,uid in placements.items() if uid==leader)])

    def test_leader_change_during_unlocked_combat_invalidates_report(self):
        placements=self.adapter.placements(self.p,self.wm.TEAM_ID);leader=list(placements.values())[-1]
        original=self.adapter.make_report_details
        def race(*args,**kwargs):
            result=original(*args,**kwargs)
            dto=b''.join(self.m(1,self.v(1,pos)+self.g.pb_int64_field(2,uid)) for pos,uid in placements.items())+self.v(5,self.wm.TEAM_ID)+self.g.pb_int64_field(3,leader)
            self.call('Battle_SetFormation',self.m(1,dto),copy.deepcopy(self.p))
            return result
        with patch.object(self.adapter,'make_report_details',side_effect=race):
            self.assert_error(self.call('War_SetOut',self.dispatch_data()))
        self.assertIsNone(self.mission());self.assertEqual(self.state()['leader'],leader)

    def test_concurrent_dispatch_creates_exactly_one_mission(self):
        self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID))
        def run(_):
            try:return self.qa.invoke('War_SetOut',self.dispatch_data(),copy.deepcopy(self.p))
            finally:self.qa.store.close_thread_db()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(run,range(4)))
        successes=sum(any(k.endswith('.Cmd_War_SetOut_SC') for k,_ in result) for result in results)
        self.assertEqual(successes,1);self.assertEqual(self.state()['used'],1)
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_missions WHERE uin=?',(self.p['uin'],)).fetchone()[0],1)

    def test_shared_non_map_preview_preserves_original_response(self):
        self.assign_team(1)
        target=self.v(1,1)+self.v(2,1)+self.v(3,1)+self.v(4,1)
        cmd=self.m(1,target)
        original=self.adapter.original_preview(cmd,self.p)
        if isinstance(original,list):original=message(original,'Battle_GetFightInfo')
        wrapped=message(self.call('Battle_GetFightInfo',cmd),'Battle_GetFightInfo')
        self.assertEqual(wrapped,original)
        self.assertTrue(many(wrapped,4))

    def test_trusted_large_report_transform_and_input_limit_separate(self):
        large=self.m(4,b'x'*70000)+self.v(5,3)
        updated=self.wm._replace(large,5,self.v(5,3))
        self.assertEqual(one(updated,4),b'x'*70000)
        self.assert_error(self.call('WorldMap_GetInfo',large))

    def test_malformed_requests_reject_without_state_or_money_changes(self):
        self.state();before=self.coin()
        for data in (b'\x80',b'\x12\x05\x08',b'\x00',b'\x0f',b'\x08\x80'*40000):
            self.assert_error(self.call('War_SetOut',data))
        self.assertEqual(self.coin(),before);self.assertIsNone(self.mission())

    def test_local_npc_ids_and_exclusive_city_paging(self):
        body=message(self.call('Setout_GetCityList',self.v(1,1)+self.v(2,0)+self.v(3,1)),'Setout_GetCityList')
        with self.adapter.db(self.p) as con:
            total=len(self.adapter.declared_targets(con,self.p,self.adapter.state(con,self.p)))
        self.assertEqual(one(body,2),total)
        self.assertEqual(len(many(body,1)),1,'AS endIndex is exclusive')
        self.assertEqual(one(many(body,1)[0],1),self.wm.NPCS[0]['role_id'])

    def test_dispatch_repeat_and_active_formation_lock(self):
        row=self.dispatch();self.assertEqual(row['state'],self.wm.MARCHING)
        self.assertEqual(self.state()['used'],1)
        self.assert_error(self.call('War_SetOut',self.dispatch_data(),copy.deepcopy(self.p)))
        self.assertEqual(self.state()['used'],1)
        formation=one(message(self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID)),'Battle_GetFormation'),1)
        self.assertEqual(one(formation,6),1)
        before=self.state()['formation']
        self.assert_error(self.call('Battle_SetFormation',self.m(1,formation)))
        self.assertEqual(self.state()['formation'],before)

    def test_combat_settlement_report_reward_return_and_replay_once(self):
        before=self.coin();row=self.dispatch();frozen=bytes(row['report'])
        self.assertTrue(frozen);self.assertEqual(one(frozen,5),3)
        self.assertIsInstance(one(frozen,2),bytes);self.assertIsInstance(one(frozen,3),bytes)
        self.assert_error(self.call('Battle_QueryReport',self.v(1,row['id'])))
        self.now=row['arrive_at'];self.call('War_GetHistory')
        expected=before+(row['reward'] if row['won'] else 0)
        self.assertEqual(self.coin(),expected);self.assertEqual(self.mission()['state'],self.wm.RETURNING)
        for _ in range(4):self.call('War_GetHistory',player=copy.deepcopy(self.p))
        self.assertEqual(self.coin(),expected);self.assertEqual(self.mission()['settled'],1)
        report=message(self.call('Battle_QueryReport',self.v(1,row['id'])),'Battle_QueryReport')
        self.assertEqual(one(report,1),frozen)
        self.now=row['home_at'];self.call('War_GetMapHistory')
        self.assertEqual(self.mission()['state'],self.wm.HOME)
        formation=one(message(self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID)),'Battle_GetFormation'),1)
        self.assertEqual(one(formation,6),0);self.assertEqual(self.coin(),expected)

    def test_recall_has_no_reward_or_report_and_returns(self):
        before=self.coin();row=self.dispatch();self.now+=5
        self.call('War_RecallTeam',self.v(1,self.wm.TEAM_ID)+self.v(2,1))
        recalled=self.mission();self.assertEqual(recalled['state'],self.wm.RECALLING)
        self.assertEqual(recalled['cancelled'],1);self.assertEqual(recalled['reward'],0)
        self.assert_error(self.call('War_RecallTeam',self.v(1,self.wm.TEAM_ID)+self.v(2,1)))
        self.now=recalled['home_at']+100;self.call('War_GetHistory')
        self.assertEqual(self.mission()['state'],self.wm.HOME);self.assertEqual(self.coin(),before)
        self.assert_error(self.call('Battle_QueryReport',self.v(1,row['id'])))

    def test_strong_camp_actual_loss_grants_no_coin(self):
        before=self.coin();row=self.dispatch(npc=2)
        self.assertEqual(row['won'],0,'starter-card fixture must actually lose to strongest local camp')
        self.assertEqual(one(bytes(row['report']),1),1)  # AS E_FightResult_Loss
        self.now=row['arrive_at'];self.call('War_GetHistory')
        returning=one(message(self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID)),'Battle_GetFormation'),1)
        self.assertEqual(one(returning,6),1)
        self.assertTrue(any(one(card,4)<one(card,5) for card in many(returning,1)), 'returning frozen formation reflects settled battle injury')
        self.now=row['home_at']+1;self.call('War_GetHistory')
        self.assertEqual(self.coin(),before);self.assertEqual(self.mission()['reward'],0)
        self.assertEqual(self.mission()['settled'],1)

    def test_cooldown_prevents_replay_then_allows_new_distinct_mission(self):
        row=self.dispatch();self.now=row['home_at'];self.call('War_GetHistory')
        self.assert_error(self.call('War_SetOut',self.dispatch_data()))
        self.assertEqual(self.state()['used'],1)
        self.now=row['arrive_at']+self.wm.TARGET_COOLDOWN+1
        declared=message(self.call('War_DeclareWar',self.v(1,self.wm.NPCS[0]['role_id'])),'War_DeclareWar')
        self.now=one(declared,2)+2
        next_row=self.dispatch();self.assertGreater(next_row['id'],row['id'])
        self.assertEqual(self.state()['used'],2)

    def test_unowned_duplicate_and_empty_map_formations_reject_safely(self):
        self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID))
        original=self.state()['formation']
        invalid=self.m(1,self.v(1,3)+self.v(2,999999999))+self.v(5,self.wm.TEAM_ID)
        self.assert_error(self.call('Battle_SetFormation',self.m(1,invalid)))
        self.assertEqual(self.state()['formation'],original)
        uid=self.qa.store.get_cards(self.p['uin'])[0][0]
        duplicate=(self.m(1,self.v(1,3)+self.v(2,uid))+self.m(1,self.v(1,7)+self.v(2,uid))
                   +self.v(5,self.wm.TEAM_ID))
        self.assert_error(self.call('Battle_SetFormation',self.m(1,duplicate)))
        self.assertEqual(self.state()['formation'],original)
        self.assign_team(1,{3:uid})  # keep one home card while clearing the independent map squad
        message(self.call('Battle_SetFormation',self.m(1,self.v(5,self.wm.TEAM_ID))),'Battle_GetFormation')
        self.assert_error(self.call('War_SetOut',self.dispatch_data()))
        self.assertIsNone(self.mission())

    def test_history_category_filters_do_not_mislabel_personal_camp_records(self):
        self.dispatch()
        own=message(self.call('War_GetHistory',self.v(1,1)),'War_GetHistory')
        self.assertEqual(len(many(own,1)),1)
        for kind in (2,3):
            other=message(self.call('War_GetHistory',self.v(1,kind)),'War_GetHistory')
            self.assertEqual(many(other,1),[])
        self.assert_error(self.call('War_GetHistory',self.v(1,99)))

    def test_report_and_map_state_isolation_between_accounts(self):
        row=self.dispatch();self.now=row['home_at']+1;self.call('War_GetHistory')
        other=self.qa.player(99108000+self.serial,name='Other QA',coin=4321,role_created=1)
        self.assert_error(self.call('Battle_QueryReport',self.v(1,row['id']),other))
        body=message(self.call('War_GetHistory',player=other),'War_GetHistory')
        self.assertEqual(many(body,1),[])
        self.assertEqual(self.qa.store.get_or_create_player(other['uin'])[0]['coin'],4321)

    def test_reconnect_adapter_and_offline_settlement_uses_current_db_coin(self):
        row=self.dispatch();self.qa.store.save_player(self.p['uin'],coin=123456)
        stale=copy.deepcopy(self.p);self.assertNotEqual(stale['coin'],123456)
        self.now=row['home_at']+1
        replacement=self.wm.WorldMap(self.g)
        replacement.advance(stale)
        expected=123456+(row['reward'] if row['won'] else 0)
        self.assertEqual(self.coin(),expected);self.assertEqual(stale['coin'],expected)
        replacement.advance(copy.deepcopy(self.p));self.assertEqual(self.coin(),expected)
        self.assertEqual(self.mission()['state'],self.wm.HOME)

    def test_settlement_database_failure_rolls_back_currency_and_receipt(self):
        before=self.coin();row=self.dispatch();self.now=row['arrive_at']
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.execute("CREATE TRIGGER qa_abort_settle BEFORE UPDATE OF settled ON local_worldmap_missions WHEN NEW.settled=1 BEGIN SELECT RAISE(ABORT,'QA injected failure'); END")
        try:
            with self.assertRaises(sqlite3.DatabaseError):self.adapter.advance(self.p)
            self.assertEqual(self.coin(),before);self.assertEqual(self.mission()['settled'],0)
            self.assertEqual(self.mission()['state'],self.wm.MARCHING)
            self.assertEqual(self.state()['wins'],0);self.assertEqual(json.loads(self.state()['cooldowns']),{})
        finally:
            with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:con.execute('DROP TRIGGER qa_abort_settle')
        self.adapter.advance(self.p);self.assertEqual(self.mission()['settled'],1)

    def test_concurrent_settlement_is_exactly_once(self):
        before=self.coin();row=self.dispatch();self.now=row['arrive_at']
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _:self.adapter.advance(copy.deepcopy(self.p)),range(4)))
        self.assertEqual(self.coin(),before+(row['reward'] if row['won'] else 0))
        self.assertEqual(self.mission()['settled'],1);self.assertEqual(self.state()['wins'],int(bool(row['won'])))

    def test_speedup_insufficient_item_and_stale_session_cannot_overdraw(self):
        row=self.dispatch();cmd=self.v(1,self.wm.TEAM_ID)+self.v(2,self.wm.SPEEDUP_ITEM)
        count=self.qa.store.get_item_count(self.p['uin'],self.wm.SPEEDUP_ITEM)
        if count:self.qa.store.add_item(self.p['uin'],self.wm.SPEEDUP_ITEM,-count)
        self.assert_error(self.call('War_SpeedUpTeam',cmd));self.assertEqual(self.mission()['arrive_at'],row['arrive_at'])
        self.qa.store.add_item(self.p['uin'],self.wm.SPEEDUP_ITEM,1)
        self.call('War_SpeedUpTeam',cmd);first=self.mission()['arrive_at']
        self.assertLess(first,row['arrive_at']);self.assertEqual(self.qa.store.get_item_count(self.p['uin'],self.wm.SPEEDUP_ITEM),0)
        self.assert_error(self.call('War_SpeedUpTeam',cmd,copy.deepcopy(self.p)))
        self.assertEqual(self.mission()['arrive_at'],first)

    def test_invalid_dispatch_and_free_battle_request_do_not_grant_reward(self):
        self.state();before=self.coin()
        bad=(self.v(1,2)+self.m(2,self.pos(719,719))+self.v(3,self.wm.TEAM_ID),
             self.v(1,2)+self.m(2,self.pos(72000,0))+self.v(3,self.wm.TEAM_ID),
             self.dispatch_data(team=99999),self.v(1,4)+self.m(2,self.pos(24,18))+self.v(3,self.wm.TEAM_ID))
        for cmd in bad:self.assert_error(self.call('War_SetOut',cmd))
        target=self.v(1,3)+self.v(8,self.wm.NPCS[0]['role_id'])
        self.assert_error(self.call('Battle_Fight',self.m(1,target)))
        self.assertEqual(self.coin(),before);self.assertIsNone(self.mission())

    def test_daily_limit_and_clock_rollover(self):
        self.state()
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.execute('UPDATE local_worldmap_state SET used=? WHERE uin=?',(self.wm.DAILY_LIMIT,self.p['uin']))
        self.assert_error(self.call('War_SetOut',self.dispatch_data()))
        self.assertIsNone(self.mission());self.now+=86400
        times=message(self.call('Setout_GetSetoutTimes',self.v(1,2)),'Setout_GetSetoutTimes')
        self.assertEqual(one(times,3),self.wm.DAILY_LIMIT)

    def test_fixed_move_cost_repeat_persistence_and_invalid_destinations(self):
        destination=self.empty_city_site();item=self.wm.MOVE_ITEM
        count=self.qa.store.get_item_count(self.p['uin'],item)
        if count:self.qa.store.add_item(self.p['uin'],item,-count)
        cmd=self.v(1,1)+self.m(2,self.pos(*destination))
        self.assert_error(self.call('City_FixedMove',cmd))
        self.qa.store.add_item(self.p['uin'],item,1)
        self.assertEqual(one(message(self.call('City_FixedMove',cmd),'City_FixedMove'),2),self.pos(*destination))
        self.assertEqual(self.qa.store.get_item_count(self.p['uin'],item),0)
        self.call('City_FixedMove',cmd,copy.deepcopy(self.p))
        self.assertEqual(self.qa.store.get_item_count(self.p['uin'],item),0)
        self.assertEqual((self.state()['x'],self.state()['y']),destination)
        init=message(self.call('City_Init',player=copy.deepcopy(self.p)),'City_Init')
        self.assertEqual(one(init,1),self.pos(*destination))
        for area,pos in ((1,(6,6)),(3,(5,5)),(1,(2015,2015)),(2,(719,719)),(1,(24,18))):
            self.assert_error(self.call('City_FixedMove',self.v(1,area)+self.m(2,self.pos(*pos))))
        self.assertEqual((self.state()['x'],self.state()['y']),destination)

    def test_move_database_failure_rolls_back_item_and_location(self):
        destination=self.empty_city_site();origin=(self.state()['x'],self.state()['y'])
        item=self.wm.MOVE_ITEM;self.qa.store.add_item(self.p['uin'],item,1)
        before=self.qa.store.get_item_count(self.p['uin'],item)
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.execute("CREATE TRIGGER qa_abort_move BEFORE UPDATE OF area ON local_worldmap_state BEGIN SELECT RAISE(ABORT,'QA move failure'); END")
        try:
            replies=self.call('City_FixedMove',self.v(1,1)+self.m(2,self.pos(*destination)))
            self.assertEqual(one(message(replies,'LogicError'),1),3001)
            self.assertEqual(self.qa.store.get_item_count(self.p['uin'],item),before)
            self.assertEqual((self.state()['x'],self.state()['y']),origin)
        finally:
            with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:con.execute('DROP TRIGGER qa_abort_move')

    def test_area_and_city_movement_blocked_while_outbound(self):
        destination=self.empty_city_site()
        self.dispatch();self.qa.store.add_item(self.p['uin'],self.wm.MOVE_ITEM,1)
        before=self.qa.store.get_item_count(self.p['uin'],self.wm.MOVE_ITEM)
        self.assert_error(self.call('City_FixedMove',self.v(1,1)+self.m(2,self.pos(*destination))))
        self.assert_error(self.call('City_ChangeArea',self.v(1,2)))
        self.assertEqual(self.state()['area'],1)
        self.assertEqual(self.qa.store.get_item_count(self.p['uin'],self.wm.MOVE_ITEM),before)

    def test_extra_attempt_purchase_uses_live_diamonds_gift_first_and_no_duplicate_charge(self):
        self.state();before_coin=self.coin()
        self.qa.store.save_player(self.p['uin'],present_ticket=15,ticket=100)
        gift,gold=15,100
        self.assert_error(self.call('Setout_BuyMapWarSetoutTimes'))
        for index,cost in enumerate(self.wm.BUY_COSTS):
            with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
                con.execute('UPDATE local_worldmap_state SET used=? WHERE uin=?',(index+1,self.p['uin']))
            msgs=self.call('Setout_BuyMapWarSetoutTimes',player=copy.deepcopy(self.p))
            spend_gift=min(gift,cost);gift-=spend_gift;gold-=cost-spend_gift
            self.assertEqual(one(message(msgs,'SyncPresentTicket'),1),gift)
            self.assertEqual(one(message(msgs,'SyncTicket'),1),gold)
            live=self.qa.store.get_or_create_player(self.p['uin'])[0]
            self.assertEqual((live['present_ticket'],live['ticket']),(gift,gold))
            self.assertEqual(self.coin(),before_coin)
            self.assertEqual(self.state()['bonus'],index+1)
            quota=message(self.call('Setout_GetSetoutTimes',self.v(1,2)),'Setout_GetSetoutTimes')
            self.assertEqual(one(quota,2),self.wm.DAILY_LIMIT)
            self.assertEqual(one(quota,3),self.wm.DAILY_LIMIT)
            self.assert_error(self.call('Setout_BuyMapWarSetoutTimes',player=copy.deepcopy(self.p)))
            live=self.qa.store.get_or_create_player(self.p['uin'])[0]
            self.assertEqual((live['present_ticket'],live['ticket']),(gift,gold))
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.execute('UPDATE local_worldmap_state SET used=? WHERE uin=?',(self.wm.DAILY_LIMIT+len(self.wm.BUY_COSTS),self.p['uin']))
        self.assert_error(self.call('Setout_BuyMapWarSetoutTimes'));self.assertEqual(self.coin(),before_coin)
        self.now+=86400;self.call('Setout_GetSetoutTimes')
        self.assertEqual(self.state()['bonus'],0);self.assertEqual(self.state()['bought'],0)

    def test_attempt_refill_is_available_below_full_not_only_when_exhausted(self):
        self.state();self.qa.store.save_player(self.p['uin'],present_ticket=100,ticket=0)
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.execute('UPDATE local_worldmap_state SET used=3 WHERE uin=?',(self.p['uin'],))
        for index in range(3):
            self.call('Setout_BuyMapWarSetoutTimes')
            quota=message(self.call('Setout_GetSetoutTimes',self.v(1,2)),'Setout_GetSetoutTimes')
            self.assertEqual(one(quota,2),20)
            self.assertEqual(one(quota,3),18+index)
        self.assert_error(self.call('Setout_BuyMapWarSetoutTimes'))
        self.assertEqual(self.qa.store.get_or_create_player(self.p['uin'])[0]['present_ticket'],100-sum(self.wm.BUY_COSTS))

    def test_parallel_refills_respect_full_capacity_wallet_and_price_steps(self):
        self.state();self.qa.store.save_player(self.p['uin'],present_ticket=100,ticket=0)
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.execute('UPDATE local_worldmap_state SET used=2 WHERE uin=?',(self.p['uin'],))
        def buy(_):
            try:return self.call('Setout_BuyMapWarSetoutTimes',player=copy.deepcopy(self.p))
            finally:self.qa.store.close_thread_db()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(buy,range(4)))
        self.assertEqual(sum(any(k.endswith('BuyMapWarSetoutTimes_SC') for k,_ in x) for x in results),2)
        self.assertEqual(self.state()['bonus'],2);self.assertEqual(self.state()['bought'],2)
        self.assertEqual(self.qa.store.get_or_create_player(self.p['uin'])[0]['present_ticket'],70)
        # Requests have no purchase token: two accepted requests below capacity are two real purchases.
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.execute('UPDATE local_worldmap_state SET used=5 WHERE uin=?',(self.p['uin'],))
        self.qa.store.save_player(self.p['uin'],present_ticket=29,ticket=0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(buy,range(4)))
        self.assertTrue(all(any(k.endswith('LogicError_SC') for k,_ in x) for x in results))
        self.assertEqual(self.state()['bought'],2)
        self.assertEqual(self.qa.store.get_or_create_player(self.p['uin'])[0]['present_ticket'],29)

    def test_purchase_insufficient_live_diamonds_and_sql_failure_are_atomic(self):
        self.state()
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.execute('UPDATE local_worldmap_state SET used=? WHERE uin=?',(self.wm.DAILY_LIMIT,self.p['uin']))
        self.qa.store.save_player(self.p['uin'],present_ticket=0,ticket=0)
        stale=copy.deepcopy(self.p);stale.update(present_ticket=100,ticket=100)
        self.assert_error(self.call('Setout_BuyMapWarSetoutTimes',player=stale))
        self.assertEqual(self.state()['bonus'],0)
        self.qa.store.save_player(self.p['uin'],present_ticket=100,ticket=20)
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.execute("CREATE TRIGGER qa_abort_buy BEFORE UPDATE OF bonus ON local_worldmap_state BEGIN SELECT RAISE(ABORT,'QA buy failure'); END")
        try:
            self.assert_error(self.call('Setout_BuyMapWarSetoutTimes',player=stale))
            live=self.qa.store.get_or_create_player(self.p['uin'])[0]
            self.assertEqual((live['present_ticket'],live['ticket']),(100,20))
            self.assertEqual(self.state()['bonus'],0)
        finally:
            with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:con.execute('DROP TRIGGER qa_abort_buy')

    def test_cross_area_move_uses_native_position_update_cooldown_and_saved_locations(self):
        destination=self.empty_city_site()
        self.qa.store.add_item(self.p['uin'],self.wm.MOVE_ITEM,1)
        message(self.call('City_FixedMove',self.v(1,1)+self.m(2,self.pos(*destination))),'City_FixedMove')
        before_coin=self.coin()
        msgs=self.call('City_ChangeArea',self.v(1,2))
        end=one(message(msgs,'City_ChangeArea'),1)
        self.assertEqual(end,self.now+self.wm.AREA_CHANGE_COOLDOWN)
        native=message(msgs,'City_RandomMove')
        target=one(native,1)
        self.assertTrue(all(0<=one(target,axis)<self.wm.MAP_SIZES[2]-1 and one(target,axis)%6==5 for axis in (1,2)))
        self.assertEqual(one(native,2),2)
        self.assertEqual(target,self.pos(self.state()['x'],self.state()['y']))
        self.assertEqual(self.state()['area'],2);self.assertEqual(self.coin(),before_coin)
        repeated=message(self.call('City_ChangeArea',self.v(1,2)),'City_ChangeArea')
        self.assertEqual(one(repeated,1),end)
        self.assert_error(self.call('City_ChangeArea',self.v(1,1)))
        self.now=end+1
        native=message(self.call('City_ChangeArea',self.v(1,1)),'City_RandomMove')
        self.assertEqual(one(native,1),self.pos(*destination));self.assertEqual(one(native,2),1)
        init=message(self.call('City_Init'),'City_Init')
        self.assertEqual(one(init,1),self.pos(*destination));self.assertEqual(one(init,2),1)

    def injure_map_team(self):
        placements=self.adapter.placements(self.p,self.wm.TEAM_ID)
        health={str(uid):5000 for uid in placements.values()}
        with self.adapter.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET health=? WHERE uin=?',(json.dumps(health),self.p['uin']))
        return health,len(placements)*self.wm.HEAL_COST_PER_CARD

    def test_confirmed_dispatch_automatically_heals_exactly_once_and_uses_full_hp(self):
        health,cost=self.injure_map_team();before=self.coin()
        dto=one(message(self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID)),'Battle_GetFormation'),1)
        self.assertEqual(one(dto,7),cost)
        row=self.dispatch()
        self.assertEqual(self.coin(),before-cost)
        self.assertTrue(all(json.loads(self.state()['health'])[uid]==10000 for uid in health))
        left=one(bytes(row['report']),2)
        self.assertTrue(all(one(card,3)==one(card,6) for card in many(left,2)), 'AS confirmation proceeds directly to SetOut; combat starts healed')
        self.assert_error(self.call('War_SetOut',self.dispatch_data(),copy.deepcopy(self.p)))
        self.assertEqual(self.coin(),before-cost)

    def test_automatic_heal_insufficient_live_coins_has_no_partial_dispatch(self):
        health,cost=self.injure_map_team();self.qa.store.save_player(self.p['uin'],coin=cost-1)
        stale=copy.deepcopy(self.p);stale['coin']=999999
        self.assert_error(self.call('War_SetOut',self.dispatch_data(),stale))
        self.assertIsNone(self.mission());self.assertEqual(self.state()['used'],0)
        self.assertEqual(json.loads(self.state()['health']),health);self.assertEqual(self.coin(),cost-1)

    def test_automatic_heal_and_mission_insert_rollback_together(self):
        health,cost=self.injure_map_team();before=self.coin()
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            con.execute("CREATE TRIGGER qa_abort_dispatch BEFORE INSERT ON local_worldmap_missions BEGIN SELECT RAISE(ABORT,'QA dispatch failure'); END")
        try:
            self.assert_error(self.call('War_SetOut',self.dispatch_data()))
            self.assertEqual(self.coin(),before);self.assertEqual(json.loads(self.state()['health']),health)
            self.assertIsNone(self.mission());self.assertEqual(self.state()['used'],0)
        finally:
            with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:con.execute('DROP TRIGGER qa_abort_dispatch')

    def test_actual_battle_injury_recovery_cost_repeat_and_insufficient_balance(self):
        row=self.dispatch(npc=2);self.now=row['home_at']+1;self.call('War_GetHistory')
        health=json.loads(self.state()['health'])
        self.assertTrue(any(int(x)<10000 for x in health.values()))
        formation=one(message(self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID)),'Battle_GetFormation'),1)
        cost=one(formation,7);self.assertGreater(cost,0)
        stale=copy.deepcopy(self.p);self.qa.store.save_player(self.p['uin'],coin=0)
        self.assert_error(self.call('War_RecoverHp',self.v(1,self.wm.TEAM_ID),stale))
        self.assertEqual(self.coin(),0);self.assertEqual(json.loads(self.state()['health']),health)
        self.qa.store.save_player(self.p['uin'],coin=cost+10)
        self.call('War_RecoverHp',self.v(1,self.wm.TEAM_ID),stale)
        self.assertEqual(self.coin(),10)
        self.assertTrue(all(int(x)==10000 for x in json.loads(self.state()['health']).values()))
        self.assert_error(self.call('War_RecoverHp',self.v(1,self.wm.TEAM_ID),stale))
        self.assertEqual(self.coin(),10)

    def test_cattail_roster_and_ordinary_formation_are_preserved(self):
        # Explicitly construct an owned TD plant fixture; map queries must not
        # turn that plant into an adventure card or grant it to other accounts.
        self.qa.store.unlock_plant(self.p['uin'],7800)
        self.p['plants']=self.qa.store.get_player_plants(self.p['uin'])
        self.assertIn(7800,self.qa.store.get_player_plants(self.p['uin']))
        self.assertFalse(any(row[1]==7801 for row in self.qa.store.get_cards(self.p['uin'])))
        self.call('Battle_GetFormation',self.v(1,1))  # legacy lazy home-team initialization
        before=dict(self.qa.store.get_formation(self.p['uin'],1))
        self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID))
        self.assertEqual(dict(self.qa.store.get_formation(self.p['uin'],1)),before)
        self.assertIn(7800,self.qa.store.get_player_plants(self.p['uin']))
        other=self.qa.player(99707000+self.serial,level=100,role_created=1)
        self.call('Battle_GetFormation',player=other)
        self.assertNotIn(7800,self.qa.store.get_player_plants(other['uin']))


class WorldMapAssetHTTPTests(unittest.TestCase):
    def test_all_reviewed_map_assets_are_real_http_payloads(self):
        with IsolatedServer() as qa:
            module=importlib.import_module('serve_client')
            # Frozen hashes retain the existing atlases and independently
            # reviewed sheets assembled from the user's original terrain PNGs.
            manifest=json.loads((Path(__file__).parent/'live_assets_sha256.json').read_text(encoding='utf8'))
            server=module.Server(('127.0.0.1',0),module.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                for asset in manifest['assets']:
                    with self.subTest(asset=asset['path']):
                        url='http://127.0.0.1:'+str(server.server_address[1])+'/'+asset['path']
                        with urllib.request.urlopen(url) as response:
                            data=response.read();self.assertEqual(response.status,200)
                        self.assertNotEqual(data,module.PLACEHOLDER_PNG)
                        self.assertEqual(hashlib.sha256(data).hexdigest(),asset['sha256'])
                        if asset['path'].endswith('.png'):
                            self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
                            self.assertEqual(struct.unpack('>II',data[16:24]),(asset['width'],asset['height']))
                        else:
                            self.assertEqual(data[:3], b'\xff\xd8\xff')
                        if asset['path'] in {f'assets/map/maoxian_2/{name}.png' for name in ('cd','cp','gm','sl','st','td')}:
                            self.assertEqual(data[25],6,'Restored sprites must preserve RGBA transparency')
                log=Path(module.ASSET_ERROR_LOG)
                self.assertFalse(log.exists() and log.read_text().strip())
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main(verbosity=2)
