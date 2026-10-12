"""All saved world-map towns exercise their real stored defenders and march lifecycle."""
import copy
import importlib
import json
import sqlite3
from contextlib import closing
import unittest
from unittest.mock import patch
from worldmap_test_support import IsolatedServer, many, message, one


class BotTownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = IsolatedServer()
        cls.g = cls.qa.game
        cls.wm = importlib.import_module('worldmap_local')
        cls.a = cls.g._worldmap_adapter
        cls.serial = 0

    @classmethod
    def tearDownClass(cls):
        cls.qa.close()

    def setUp(self):
        type(self).serial += 1
        self.now = 1800000000
        clock = patch.object(self.wm, '_now', side_effect=lambda: self.now)
        clock.start(); self.addCleanup(clock.stop)
        self.p = self.qa.player(99250000 + self.serial, level=100, coin=10000, role_created=1)
        self.g.rebuild_td_roster(self.p); self.g.ensure_player_cards(self.p)
        self.qa.train_owned_cards(self.p,level=100)
        self.cards = dict(zip(self.g._FORMATION_POS,[r[0] for r in self.qa.store.get_cards(self.p['uin'])]))
        self.assign(1, list(self.cards.values())[-1])
        self.bot = self.qa.player(900001, name='Saved Bot Defender', level=30, coin=1234, role_created=1)
        self.g.rebuild_td_roster(self.bot); self.g.ensure_player_cards(self.bot)
        self.qa.train_owned_cards(self.bot, level=1)
        self.g.default_formation_placements(self.bot)
        with self.a.db(self.p) as con:
            con.execute('CREATE TABLE IF NOT EXISTS bots_state(uin INTEGER PRIMARY KEY,score INTEGER DEFAULT 1000,'
                        'wins INTEGER DEFAULT 0,losses INTEGER DEFAULT 0,power INTEGER DEFAULT 0)')
            con.execute('INSERT OR IGNORE INTO bots_state(uin) VALUES (?)',(self.bot['uin'],))
        self.call('City_Init')

    def v(self,n,x): return self.wm._v(n,x)
    def m(self,n,x): return self.wm._m(n,x)
    def call(self,name,data=b''): return self.qa.invoke(name,data,self.p)
    def assign(self,team,leader=0):
        dto = b''.join(self.m(1,self.v(1,p)+self.v(2,u)) for p,u in self.cards.items())
        return self.call('Battle_SetFormation',self.m(1,dto+self.v(5,team)+self.v(3,leader)))
    def target(self):
        with self.a.db(self.p) as con:return self.a.target(self.p,role_id=self.bot['role_id'],con=con)
    def dispatch(self,target=None):
        t = target or self.target()
        if self.a.target(self.p,role_id=t['role_id']) is not None:
            declared=message(self.call('War_DeclareWar',self.v(1,t['role_id'])),'War_DeclareWar')
            self.now=max(self.now,one(declared,2)+2)
        return self.call('War_SetOut',self.v(1,2)+self.m(2,self.wm._position(t['pos']))+self.v(3,1))
    def saved_defender(self):
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con:
            return tuple((table,con.execute('SELECT * FROM '+table+' WHERE uin=?',(self.bot['uin'],)).fetchall())
                         for table in ('players','cards','formation','formation_leader','bots_state'))

    def test_real_bot_is_attackable_in_map_list_preflight_preview_and_dispatch(self):
        before = self.saved_defender()
        cells = many(message(self.call('WorldMap_GetInfo',self.v(5,1)),'WorldMap_GetInfo'),1)
        bot = next(c for c in cells if one(c,3)==self.bot['role_id'])
        self.assertEqual(one(bot,5),0)
        self.assertEqual(many(message(self.call('WorldMap_GetEnemyList'),'WorldMap_GetEnemyList'),1),[])
        declared=message(self.call('War_DeclareWar',self.v(1,self.bot['role_id'])),'War_DeclareWar')
        self.now=one(declared,2)+2
        enemy = next(c for c in many(message(self.call('WorldMap_GetEnemyList'),'WorldMap_GetEnemyList'),1)
                     if one(c,3)==self.bot['role_id'])
        self.assertEqual(one(enemy,2),one(bot,2))
        rows = many(message(self.call('Setout_GetCityList',self.v(1,1)+self.v(2,0)+self.v(3,20)),'Setout_GetCityList'),1)
        self.assertEqual(one(next(r for r in rows if one(r,1)==self.bot['role_id']),6),4)
        message(self.call('War_PreSetOut',self.v(1,2)+self.m(2,one(bot,2))),'War_PreSetOut')
        request = self.m(1,self.v(1,3)+self.v(8,self.bot['role_id']))
        preview = many(message(self.call('Battle_GetFightInfo',request),'Battle_GetFightInfo'),4)
        units = self.a.enemies(self.target())
        self.assertEqual([one(c,2) for c in preview],[u['card_id'] for u in units])
        message(self.dispatch(),'War_SetOut')
        with self.a.db(self.p) as con:
            row=dict(self.a.mission(con,self.p,1))
        report = bytes(row['report']); right=one(report,3)
        self.assertEqual([one(c,2) for c in many(right,2)],[u['card_id'] for u in units])
        self.assertTrue(all(one(c,1) in self.g._ENEMY_POS for c in many(right,2)))
        self.assertEqual(one(right,6),b'Saved Bot Defender')
        self.assertEqual(self.saved_defender(),before,'town battle must not regenerate or damage another save')
        self.now = row['home_at']+1
        history=message(self.call('War_GetHistory'),'War_GetHistory')
        item=next(r for r in many(history,1) if one(r,5)==row['id'])
        self.assertEqual(one(item,8),4)
        self.assertEqual(one(item,10),row['id'])
        replay=one(message(self.call('Battle_QueryReport',self.v(1,row['id'])),'Battle_QueryReport'),1)
        self.assertEqual(replay,report)

    def test_persisted_target_history_survives_bot_rename_move_and_removal(self):
        target=self.target();message(self.dispatch(target),'War_SetOut')
        with self.a.db(self.p) as con:
            row=dict(self.a.mission(con,self.p,1))
            con.execute('UPDATE players SET name=? WHERE uin=?',('Regenerated New Name',self.bot['uin']))
            con.execute('DELETE FROM bots_state WHERE uin=?',(self.bot['uin'],))
        self.now=row['home_at']+1
        history=many(message(self.call('War_GetHistory'),'War_GetHistory'),1)[0]
        detail=one(one(history,3),4)
        self.assertEqual(one(detail,4),target['name'].encode())
        self.assertEqual(one(one(history,3),3),self.wm._position(target['pos']))
        self.assertEqual(json.loads(row['target_snapshot'])['role_id'],target['role_id'])
        self.assertEqual(one(message(self.call('Battle_QueryReport',self.v(1,row['id'])),'Battle_QueryReport'),1),row['report'])

    def test_human_in_numeric_bot_range_is_attackable_but_cross_area_and_own_targets_are_not(self):
        human=self.qa.player(999999,name='Human in old bot range',coin=4567,level=100,role_created=1)
        with self.a.db(self.p) as con:
            con.execute('INSERT OR IGNORE INTO bots_state(uin) VALUES (?)',(human['uin'],))
        cells=many(message(self.call('WorldMap_GetInfo',self.v(5,1)),'WorldMap_GetInfo'),1)
        town=next(c for c in cells if one(c,3)==human['role_id'])
        self.assertEqual(one(town,5),0)
        message(self.call('War_DeclareWar',self.v(1,human['role_id'])),'War_DeclareWar')
        self.assertEqual(one(message(self.call('War_DeclareWar',self.v(1,self.p['role_id'])),'LogicError'),1),4706)
        target=self.target();self.call('City_ChangeArea',self.v(1,2))
        self.assertIsNone(self.target())
        self.assertEqual(one(message(self.dispatch(target),'LogicError'),1),4703)

    def test_home_leader_is_selected_and_cleared_in_actual_report(self):
        self.assertEqual(self.qa.store.get_formation_leader(self.p['uin'],1),list(self.cards.values())[-1])
        target=dict(self.wm.NPCS[0],area=1)
        _won,report,_after=self.a.make_report_details(self.p,target,self.cards,team_id=1)
        leaders=[one(c,1) for c in many(one(report,2),2) if one(c,9)]
        self.assertEqual(leaders,[list(self.cards.keys())[-1]])
        self.assign(1,0)
        self.assertEqual(self.qa.store.get_formation_leader(self.p['uin'],1),0)

    def test_viewport_is_validated_and_scoped_to_each_socket_session(self):
        first=self.m(1,self.wm._position((0,0)))+self.m(4,self.wm._position((35,35)))+self.v(5,1)
        second=self.m(1,self.wm._position((72,72)))+self.m(4,self.wm._position((107,107)))+self.v(5,1)
        self.call('WorldMap_GetInfo',first)
        other=copy.deepcopy(self.p)
        self.qa.invoke('WorldMap_GetInfo',second,other)
        self.assertEqual(self.p['_worldmap_viewport'],first)
        self.assertEqual(other['_worldmap_viewport'],second)
        self.assertEqual(one(message(self.call('WorldMap_GetInfo',self.v(1,99)+self.v(5,1)),'LogicError'),1),4703)
        self.assertEqual(self.p['_worldmap_viewport'],first)
        # Successful capture must sync the original session viewport despite
        # another socket viewing a distant chunk and a malformed intervening request.
        mine=(20,20)
        capture=self.call('Mine_SetOutTeam',self.m(1,self.wm._position(mine))+self.v(2,1))
        cells=many(message(capture,'WorldMap_GetInfo'),1)
        mine_cells=[c for c in cells if one(c,1)==2]
        self.assertTrue(mine_cells)
        self.assertTrue(all(0 <= self.wm._pos(c,2)[0] <= 35 and 0 <= self.wm._pos(c,2)[1] <= 35 for c in mine_cells))

    def test_legacy_mission_schema_gets_additive_snapshot_column(self):
        # Exercise an installed pre-fix save schema without deleting any rows.
        with self.a.db(self.p) as con:
            con.execute('ALTER TABLE local_worldmap_missions DROP COLUMN target_snapshot')
        with self.a.db(self.p) as con:
            columns={r[1] for r in con.execute('PRAGMA table_info(local_worldmap_missions)')}
            self.assertIn('target_snapshot',columns)
        message(self.dispatch(),'War_SetOut')

    def human_defender(self,name='Human Defender',empty=False):
        human=self.qa.player(99440000+self.serial,name=name,level=100,coin=4567,role_created=1)
        if not empty:
            self.g.rebuild_td_roster(human);self.g.ensure_player_cards(human)
            self.qa.train_owned_cards(human,level=1)
            self.g.default_formation_placements(human)
        self.qa.invoke('City_Init',b'',human)
        return human

    def human_target(self,human):
        with self.a.db(self.p) as con:return self.a.target(self.p,role_id=human['role_id'],con=con)

    def test_human_selected_map_defence_leader_injury_and_report_are_real_and_read_only(self):
        human=self.human_defender()
        cards=[r[0] for r in self.qa.store.get_cards(human['uin'])]
        home=dict(zip(self.g._FORMATION_POS,cards[:2]))
        guard=dict(zip(self.g._FORMATION_POS,cards[2:]))
        leader=list(guard.values())[-1]
        self.qa.store.set_formation(human['uin'],1,home)
        with self.a.db(human) as con:
            con.execute('UPDATE local_worldmap_state SET formation=?,leader=?,defend_team=?,health=? WHERE uin=?',
                        (json.dumps(guard),leader,self.wm.TEAM_ID,json.dumps({str(leader):5000}),human['uin']))
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con:
            before={t:con.execute('SELECT * FROM '+t+' WHERE uin=?',(human['uin'],)).fetchall()
                    for t in ('players','cards','formation','formation_leader','local_worldmap_state')}
        target=self.human_target(human);units=self.a.enemies(target)
        self.assertEqual({u['uid'] for u in units},set(guard.values()))
        self.assertEqual([u['uid'] for u in units if u['is_leader']],[leader])
        hurt=next(u for u in units if u['uid']==leader)
        self.assertEqual(hurt['initial_hp'],hurt['max_hp']//2)
        preview=message(self.call('Battle_GetFightInfo',self.m(1,self.v(1,3)+self.v(8,human['role_id']))),'Battle_GetFightInfo')
        self.assertEqual([one(c,2) for c in many(preview,4)],[u['card_id'] for u in units])
        hurt_preview=next(c for c in many(preview,4) if one(c,4))
        self.assertEqual(one(hurt_preview,7),one(hurt_preview,8)//2)
        message(self.dispatch(target),'War_SetOut')
        with self.a.db(self.p) as con: row=dict(self.a.mission(con,self.p,1))
        right=one(row['report'],3)
        self.assertEqual(one(right,6),b'Human Defender')
        self.assertEqual(one(right,8),human['level'])
        self.assertEqual([one(c,2) for c in many(right,2)],[u['card_id'] for u in units])
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con:
            after={t:con.execute('SELECT * FROM '+t+' WHERE uin=?',(human['uin'],)).fetchall() for t in before}
        self.assertEqual(after,before)

    def test_empty_human_town_has_no_fabricated_guard_and_wins_with_persisted_report(self):
        human=self.human_defender('Empty Town',empty=True)
        target=self.human_target(human)
        self.assertEqual(self.a.enemies(target),[])
        message(self.call('War_DeclareWar',self.v(1,human['role_id'])),'War_DeclareWar')
        message(self.dispatch(target),'War_SetOut')
        with self.a.db(self.p) as con: row=dict(self.a.mission(con,self.p,1))
        report=row['report']
        self.assertEqual((one(report,1),one(report,7)),(0,1))
        self.assertEqual(many(one(report,3),2),[])
        self.assertEqual(one(one(report,3),6),b'Empty Town')
        self.now=row['home_at']+1
        self.call('War_GetHistory')
        self.assertEqual(one(message(self.call('Battle_QueryReport',self.v(1,row['id'])),'Battle_QueryReport'),1),report)
        self.assertEqual(self.qa.store.get_cards(human['uin']),[])
        self.assertEqual(self.qa.store.get_or_create_player(human['uin'])[0]['coin'],4567)

    def test_fallback_home_defenders_exclude_plants_currently_away(self):
        human=self.human_defender()
        placements=self.a.placements(human,1)
        self.assertTrue(placements)
        declared=message(self.qa.invoke('War_DeclareWar',self.v(1,self.wm.NPCS[0]['role_id']),human),'War_DeclareWar')
        self.now=one(declared,2)+2
        outgoing=self.qa.invoke('War_SetOut',self.v(1,2)+self.m(2,self.wm._position(self.wm.NPCS[0]['pos']))+self.v(3,1),human)
        message(outgoing,'War_SetOut')
        self.assertEqual(self.a.enemies(self.human_target(human)),[])

    def test_explicit_empty_map_defence_does_not_borrow_other_home_plants(self):
        human=self.human_defender()
        self.assertTrue(self.qa.store.get_formation(human['uin'],1))
        with self.a.db(human) as con:
            con.execute('UPDATE local_worldmap_state SET defend_team=?,formation=? WHERE uin=?',
                        (self.wm.TEAM_ID,'{}',human['uin']))
        self.assertEqual(self.a.enemies(self.human_target(human)),[])
        self.assertTrue(self.qa.store.get_formation(human['uin'],1))

    def test_dead_defence_captain_is_encoded_dead_and_cannot_cast_round_zero(self):
        human=self.human_defender()
        placements=self.qa.store.get_formation(human['uin'],1)
        leader=list(placements.values())[-1]
        self.qa.store.set_formation_leader(human['uin'],1,leader)
        with self.a.db(human) as con:
            con.execute('UPDATE local_worldmap_state SET defend_team=1,health=? WHERE uin=?',
                        (json.dumps({str(uid):0 for uid in placements.values()}),human['uin']))
        target=self.human_target(human)
        with self.a.db(human) as con: before=dict(self.a.state(con,human))
        original=self.g.run_battle
        def check(left,right,**options):
            self.assertTrue(right)
            self.assertTrue(all(not u['is_live'] and not u['is_leader'] and u['initial_hp']==0 for u in right))
            return original(left,right,**options)
        with patch.object(self.g,'run_battle',side_effect=check):
            won,report,_after=self.a.make_report_details(self.p,target,self.cards,team_id=1)
        self.assertTrue(won)
        self.assertTrue(all(one(c,3)==0 and one(c,17)==0 and one(c,9)==0 for c in many(one(report,3),2)))
        with self.a.db(human) as con: self.assertEqual(self.a.state(con,human),before)


if __name__=='__main__': unittest.main(verbosity=2)
