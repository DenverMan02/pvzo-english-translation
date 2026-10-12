"""Reference-three/four video: three teams, selected captain, town defence lock.

All players and saves are disposable. The three UI labels index formations;
legacy IDs 1/1001 are retained and 1002 supplies the third attack formation.
"""
import importlib
import json
import unittest
from unittest.mock import patch

from worldmap_test_support import IsolatedServer, many, message, one


class ThreeTeamsVideoTests(unittest.TestCase):
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
        clock = patch.object(self.wm,'_now',side_effect=lambda:self.now)
        clock.start(); self.addCleanup(clock.stop)
        self.p = self.qa.player(99710000+self.serial,level=21,coin=20000,role_created=1)
        self.g.rebuild_td_roster(self.p); self.g.ensure_player_cards(self.p)
        self.uids = [r[0] for r in self.qa.store.get_cards(self.p['uin'])]
        self.assertGreaterEqual(len(self.uids),3)
        for index,team in enumerate(self.a.unlocked_attack_teams(self.p)):
            self.assign(team,{self.g._FORMATION_POS[index]:self.uids[index]},self.uids[index])

    def v(self,f,val): return self.g.pb_varint_field(f,val)
    def m(self,f,val): return self.g.pb_bytes(f,val)
    def call(self,name,data=b'',player=None): return self.qa.invoke(name,data,player or self.p)
    def assign(self,team,placements,leader=0,player=None):
        dto = b''.join(self.m(1,self.v(1,p)+self.v(2,u)) for p,u in placements.items())
        return self.call('Battle_SetFormation',self.m(1,dto+self.v(5,team)+self.v(3,leader)),player)
    def dto(self,team):
        return one(message(self.call('Battle_GetFormation',self.v(1,team)),'Battle_GetFormation'),1)
    def dispatch(self,team,npc_index=0):
        npc = self.wm.NPCS[npc_index]
        reply = message(self.call('War_DeclareWar',self.v(1,npc['role_id'])),'War_DeclareWar')
        self.now = max(self.now,one(reply,2)+2)
        pos = self.v(1,npc['pos'][0])+self.v(2,npc['pos'][1])
        return self.call('War_SetOut',self.v(1,2)+self.m(2,pos)+self.v(3,team))
    def assert_error(self,replies,code): self.assertEqual(one(message(replies,'LogicError'),1),code)

    def test_three_visible_unique_slots_preserve_original_home_and_map_storage(self):
        reply = message(self.call('Battle_GetFormation'),'Battle_GetFormation')
        teams = many(reply,2)
        self.assertEqual(one(reply,3),5)
        self.assertEqual([one(d,5) for d in teams],[1,1001,1002])
        uids = [one(card,2) for d in teams for card in many(d,1)]
        self.assertEqual(len(uids),len(set(uids)))
        self.assertEqual(set(uids),set(self.uids[:3]))
        self.assertEqual(self.qa.store.get_formation(self.p['uin'],1),{self.g._FORMATION_POS[0]:self.uids[0]})
        for team in self.a.unlocked_attack_teams(self.p):
            self.assertEqual(one(self.dto(team),5),team)

    def test_third_team_default_selection_and_captain_clear_are_authoritative(self):
        team = 1002
        self.assertEqual(one(message(self.call('Battle_SetDefaultTeam',self.v(1,team)),'Battle_SetDefaultTeam'),1),team)
        reply = message(self.call('Battle_GetFormation'),'Battle_GetFormation')
        self.assertEqual(one(reply,4),team)
        placements = self.a.placements(self.p,team)
        self.assertEqual(one(self.dto(team),3),self.uids[2])
        self.assign(team,placements,0)
        self.assertEqual(one(self.dto(team),3),0)
        _,report,_ = self.a.make_report_details(self.p,dict(self.wm.NPCS[0],area=1),placements,team_id=team)
        self.assertTrue(all(not one(card,9,0) for card in many(one(report,2),2)))

    def test_transfer_between_map_slots_clears_old_captain_without_cloning(self):
        uid = self.uids[1]
        third = self.a.placements(self.p,1002)
        third[self.g._FORMATION_POS[3]] = uid
        message(self.assign(1002,third,uid),'Battle_GetFormation')
        self.assertEqual(self.a.placements(self.p,1001),{})
        self.assertEqual(one(self.dto(1001),3),0)
        self.assertEqual(one(self.dto(1002),3),uid)
        all_uids = [one(c,2) for d in many(message(self.call('Battle_GetFormation'),'Battle_GetFormation'),2) for c in many(d,1)]
        self.assertEqual(len(all_uids),len(set(all_uids)))

    def test_third_team_town_defence_blocks_dispatch_and_emptying_until_cancel(self):
        self.call('Battle_SetDefendTeam',self.v(1,1002))
        self.assertEqual(one(self.dto(1002),8),1)
        self.assert_error(self.dispatch(1002),4721)
        self.assert_error(self.assign(1002,{}),5202)
        self.assert_error(self.assign(1001,{self.g._FORMATION_POS[2]:self.uids[2]}),5202)
        self.call('Battle_CancelDefendTeam',self.v(1,1002))
        self.assertEqual(one(self.dto(1002),8),0)
        message(self.dispatch(1002),'War_SetOut')

    def test_third_team_dispatch_freezes_only_its_team_and_supports_speedup_recall(self):
        before = {team:self.a.placements(self.p,team) for team in self.wm.ATTACK_TEAMS}
        message(self.dispatch(1002),'War_SetOut')
        self.assertEqual(one(self.dto(1002),6),1)
        self.assertEqual(one(self.dto(1),6),0)
        self.assertEqual(one(self.dto(1001),6),0)
        self.assert_error(self.assign(1001,before[1002]),4702)
        self.assertEqual({team:self.a.placements(self.p,team) for team in self.wm.ATTACK_TEAMS},before)
        with self.a.db(self.p) as con:
            con.execute('INSERT INTO items(uin,item_id,count) VALUES(?,?,2) ON CONFLICT(uin,item_id) DO UPDATE SET count=2',
                        (self.p['uin'],self.wm.SPEEDUP_ITEM))
        message(self.call('War_SpeedUpTeam',self.v(1,1002)+self.v(2,self.wm.SPEEDUP_ITEM)),'War_SpeedUpTeam')
        self.now += 1
        message(self.call('War_RecallTeam',self.v(1,1002)+self.v(2,1)),'War_RecallTeam')
        with self.a.db(self.p) as con: row=dict(self.a.mission(con,self.p,1002))
        self.assertEqual(row['cancelled'],1)
        self.now = row['home_at']+1
        self.a.advance(self.p)
        self.assertEqual(one(self.dto(1002),6),0)

    def test_all_three_disjoint_teams_can_dispatch_concurrently(self):
        for i,team in enumerate(self.a.unlocked_attack_teams(self.p)):
            message(self.dispatch(team,i),'War_SetOut')
        with self.a.db(self.p) as con:
            rows=con.execute('SELECT team_id FROM local_worldmap_missions WHERE uin=? AND state<>?',
                             (self.p['uin'],self.wm.HOME)).fetchall()
        self.assertEqual({r[0] for r in rows},set(self.a.unlocked_attack_teams(self.p)))
        self.assertTrue(all(one(self.dto(team),6)==1 for team in self.a.unlocked_attack_teams(self.p)))

    def test_third_team_health_recovery_charges_only_its_injured_card(self):
        with self.a.db(self.p) as con:
            health={str(self.uids[0]):6000,str(self.uids[2]):5000}
            con.execute('UPDATE local_worldmap_state SET health=? WHERE uin=?',(json.dumps(health),self.p['uin']))
        before_coin=self.qa.store.get_or_create_player(self.p['uin'])[0]['coin']
        message(self.call('War_RecoverHp',self.v(1,1002)),'War_RecoverHp')
        with self.a.db(self.p) as con: health=json.loads(self.a.state(con,self.p)['health'])
        self.assertEqual(health[str(self.uids[2])],10000)
        self.assertEqual(health[str(self.uids[0])],6000)
        self.assertEqual(self.qa.store.get_or_create_player(self.p['uin'])[0]['coin'],before_coin-self.wm.HEAL_COST_PER_CARD)

    def test_saved_third_team_defender_uses_its_cards_captain_and_injury_read_only(self):
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET defend_team=1002,health=? WHERE uin=?',
                        (json.dumps({str(self.uids[2]):5000}),self.p['uin']))
            before=[tuple(r) for r in con.execute('SELECT * FROM local_worldmap_state WHERE uin=?',(self.p['uin'],))]
            cards=[tuple(r) for r in con.execute('SELECT * FROM cards WHERE uin=?',(self.p['uin'],))]
        units=self.a.enemies({'town_uin':self.p['uin']})
        self.assertEqual([u['uid'] for u in units],[self.uids[2]])
        self.assertTrue(units[0]['is_leader'])
        self.assertEqual(units[0]['initial_hp'],units[0]['max_hp']//2)
        with self.a.db(self.p) as con:
            self.assertEqual(before,[tuple(r) for r in con.execute('SELECT * FROM local_worldmap_state WHERE uin=?',(self.p['uin'],))])
            self.assertEqual(cards,[tuple(r) for r in con.execute('SELECT * FROM cards WHERE uin=?',(self.p['uin'],))])


if __name__=='__main__': unittest.main()
