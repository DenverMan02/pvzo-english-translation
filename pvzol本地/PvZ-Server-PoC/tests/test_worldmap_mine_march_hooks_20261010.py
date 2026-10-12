"""Video reference-four/five: a mine expedition has an actual arrival countdown.

Integration exercises the public Mine/War routes and the normal server poll,
using only synthetic accounts. It does not fast-forward ordinary API requests.
"""
import copy
import importlib
import unittest
from unittest.mock import patch

from worldmap_test_support import IsolatedServer, many, message, one


class MineMarchHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa=IsolatedServer()
        cls.g=cls.qa.game
        cls.wm=importlib.import_module('worldmap_local')
        cls.a=cls.g._worldmap_adapter
        cls.r=cls.a.resources
        cls.serial=0
    @classmethod
    def tearDownClass(cls): cls.qa.close()

    def setUp(self):
        type(self).serial+=1
        self.now=1800000000
        clock=patch.object(self.wm,'_now',side_effect=lambda:self.now)
        clock.start();self.addCleanup(clock.stop)
        self.p=self.qa.player(99810000+self.serial,level=100,coin=20000,role_created=1)
        self.g.rebuild_td_roster(self.p);self.g.ensure_player_cards(self.p)
        self.qa.train_owned_cards(self.p,level=100)
        uids=[r[0] for r in self.qa.store.get_cards(self.p['uin'])]
        self.placements=dict(zip(self.g._FORMATION_POS,uids))
        self.captain=list(self.placements.values())[-1]
        dto=self.v(5,1002)+self.v(3,self.captain)
        dto+=b''.join(self.m(1,self.v(1,p)+self.v(2,u)) for p,u in self.placements.items())
        message(self.call('Battle_SetFormation',self.m(1,dto)),'Battle_GetFormation')
        self.pos=(20,20)
        self.mine_data=self.m(1,self.position(self.pos))

    def v(self,f,v):return self.g.pb_varint_field(f,v)
    def m(self,f,v):return self.g.pb_bytes(f,v)
    def position(self,pos):return self.v(1,pos[0])+self.v(2,pos[1])
    def call(self,name,data=b'',player=None):return self.qa.invoke(name,data,player or self.p)
    def dispatch(self,team=1002):return self.call('Mine_SetOutTeam',self.mine_data+self.v(2,team))
    def job(self):
        with self.r.db(self.p) as con:
            row=con.execute('SELECT * FROM local_worldmap_mine_missions WHERE uin=? ORDER BY id DESC LIMIT 1',
                            (self.p['uin'],)).fetchone()
        return dict(row) if row else None
    def mine(self):
        with self.r.db(self.p) as con:return self.r.mine(con,self.p,self.pos)
    def history(self,name='War_GetMapHistory'):
        return many(message(self.call(name,self.v(1,1) if name=='War_GetHistory' else b''),name),1)
    def poll(self):return self.wm.poll(self.g,self.p)
    def dto(self):return one(message(self.call('Battle_GetFormation',self.v(1,1002)),'Battle_GetFormation'),1)

    def test_dispatch_quotes_real_eta_and_cannot_fight_capture_mine_or_make_report_early(self):
        quoted=one(message(self.call('War_PreSetOut',self.v(1,2)+self.m(2,self.position(self.pos))),
                           'War_PreSetOut'),1)
        with patch.object(self.g,'run_battle',wraps=self.g.run_battle) as run:
            replies=self.dispatch()
            message(replies,'Mine_SetOutTeam')
            self.assertFalse(any(k.endswith('Battle_QueryReport_SC') for k,_ in replies))
            history=one(message(replies,'War_SyncHistory'),1)
            self.assertEqual(one(history,8),self.wm.MARCHING)
            self.assertEqual(one(history,7)-one(history,6),quoted)
            self.assertEqual(one(history,11),quoted)
            self.assertEqual(one(one(history,3),1),3)
            self.assertIsNone(one(one(history,3),4), 'missing NPC role data gives original caption 僵尸')
            self.assertEqual(one(history,10),0)
            self.assertEqual(self.mine()['held'],0)
            self.assertEqual(one(self.dto(),6),1)
            self.assertEqual(one(message(self.call('Mine_Mining',self.mine_data),'LogicError'),1),4756)
            self.now=self.job()['arrive_at']-1
            self.assertEqual(self.poll(),[])
            self.assertEqual(run.call_count,0)
        with self.a.db(self.p) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_wars WHERE uin=?',
                                         (self.p['uin'],)).fetchone()[0],0)

    def test_arrival_runs_one_real_battle_then_guards_and_keeps_one_replay(self):
        dispatched=self.dispatch();action=one(one(message(dispatched,'War_SyncHistory'),1),5)
        self.now=self.job()['arrive_at']
        with patch.object(self.g,'run_battle',wraps=self.g.run_battle) as run:
            replies=self.poll()
            self.assertFalse(any(k.endswith('Battle_QueryReport_SC') for k,_ in replies))
            message(replies,'Mail_NewMessage')
            report=one(message(self.call('Battle_QueryReport',self.v(1,self.job()['report_id'])),'Battle_QueryReport'),1)
            self.assertEqual(run.call_count,1)
            self.assertEqual(self.poll(),[])
            self.assertEqual(run.call_count,1)
        job=self.job()
        self.assertEqual((job['state'],job['settled'],job['won']),(2,1,1))
        self.assertEqual((self.mine()['held'],self.mine()['team_id']),(1,1002))
        h=next(h for h in self.history() if one(h,4)==1002)
        self.assertEqual((one(h,5),one(h,8),one(h,7)),(action,2,job['guard_until']))
        self.assertEqual(job['guard_until']-job['arrive_at'],86400)
        self.assertEqual(one(h,10),job['report_id'])
        replay=one(message(self.call('Battle_QueryReport',self.v(1,job['report_id'])),'Battle_QueryReport'),1)
        self.assertEqual(replay,report)
        self.assertEqual(one(self.dto(),6),1)
        message(self.call('Mine_Mining',self.mine_data),'Mine_Mining')

    def test_marching_callback_uses_generic_war_recall_and_never_generates_a_battle(self):
        replies=self.dispatch();action=one(one(message(replies,'War_SyncHistory'),1),5)
        self.now+=5
        with patch.object(self.g,'run_battle',wraps=self.g.run_battle) as run:
            recalled=self.call('War_RecallTeam',self.v(1,1002)+self.v(2,1))
            message(recalled,'War_RecallTeam')
            h=one(message(recalled,'War_SyncHistory'),1)
            self.assertEqual((one(h,5),one(h,8)),(action,self.wm.RECALLING))
            job=self.job();self.assertEqual(job['cancelled'],1)
            self.assertEqual(one(self.dto(),6),1)
            self.now=job['home_at']
            self.poll()
            self.assertEqual(run.call_count,0)
        self.assertEqual((self.job()['state'],self.job()['report_id']),(self.wm.HOME,0))
        self.assertEqual((self.mine()['held'],one(self.dto(),6)),(0,0))
        self.assertEqual(self.history(),[])
        self.assertEqual(one(self.history('War_GetHistory')[0],5),action)

    def test_original_mine_recall_from_guard_marches_home_without_stopping_the_cart(self):
        replies=self.dispatch();action=one(one(message(replies,'War_SyncHistory'),1),5)
        self.now=self.job()['arrive_at'];self.poll()
        message(self.call('Mine_Mining',self.mine_data),'Mine_Mining')
        recalled=self.call('Mine_RecallTeam',self.mine_data)
        message(recalled,'Mine_RecallTeam')
        self.assertEqual(one(one(message(recalled,'War_SyncHistory'),1),5),action)
        self.assertIn(self.job()['state'],(self.wm.RECALLING,self.wm.RETURNING))
        self.assertEqual(one(self.dto(),6),1)
        self.assertGreater(self.mine()['started'],0)
        self.now=self.job()['home_at'];self.poll()
        self.assertEqual(self.job()['state'],self.wm.HOME)
        self.assertEqual(one(self.dto(),6),0)
        self.assertGreater(self.mine()['started'],0)

    def test_mine_march_generic_speedup_shortens_eta_and_spends_one_item(self):
        self.dispatch();before=self.job()
        with self.a.db(self.p) as con:
            con.execute('INSERT INTO items(uin,item_id,count) VALUES(?,?,2) ON CONFLICT(uin,item_id) DO UPDATE SET count=2',
                        (self.p['uin'],self.wm.SPEEDUP_ITEM))
        replies=self.call('War_SpeedUpTeam',self.v(1,1002)+self.v(2,self.wm.SPEEDUP_ITEM))
        message(replies,'War_SpeedUpTeam')
        self.assertLess(self.job()['arrive_at'],before['arrive_at'])
        self.assertEqual(self.qa.store.get_item_count(self.p['uin'],self.wm.SPEEDUP_ITEM),1)
        self.assertEqual(one(one(message(replies,'War_SyncHistory'),1),8),self.wm.MARCHING)
        self.assertEqual(self.mine()['held'],0)

    def test_reconnect_poll_finds_the_persisted_mine_march_and_resolves_its_captain_snapshot(self):
        self.dispatch();job=self.job()
        self.a.active_users.discard(self.p['uin']);self.a.seen_users.discard(self.p['uin'])
        self.assertNotIn(self.p['uin'],self.a.active_users)
        # Direct edit simulates an imported save; real UI cannot edit a busy team.
        with self.a.db(self.p) as con:
            con.execute('UPDATE formation_leader SET uid=0 WHERE uin=? AND team_id=1002',(self.p['uin'],))
        self.now=job['arrive_at']
        captured=[]
        original=self.g.run_battle
        def observe(left,right,**options):
            captured.extend(u['uid'] for u in left if u['is_leader'])
            return original(left,right,**options)
        with patch.object(self.g,'run_battle',side_effect=observe):self.poll()
        self.assertEqual(captured,[self.captain])
        self.assertEqual(self.job()['state'],2)
        self.assertEqual(self.mine()['held'],1)

    def test_arriving_loss_keeps_the_team_busy_until_return_home(self):
        self.dispatch();self.now=self.job()['arrive_at']
        original=self.g._tr_enemy_squad
        with patch.object(self.g,'_tr_enemy_squad',side_effect=lambda _power,_stage:original(100000000,31)):
            self.poll()
        job=self.job()
        self.assertEqual((job['state'],job['won'],job['settled']),(self.wm.RETURNING,0,1))
        self.assertEqual(self.mine()['held'],0)
        self.assertEqual(one(self.dto(),6),1)
        self.assertGreater(job['report_id'],0)
        self.now=job['home_at'];self.poll()
        self.assertEqual(self.job()['state'],self.wm.HOME)
        self.assertEqual(one(self.dto(),6),0)

    def test_pending_mine_army_is_excluded_from_a_town_defender_snapshot(self):
        # Select the PoC home team and send it away. Town defence fallback must
        # not field the same plants while the mine expedition is still moving.
        dto=self.v(5,1)+self.v(3,self.captain)
        dto+=b''.join(self.m(1,self.v(1,p)+self.v(2,u)) for p,u in self.placements.items())
        message(self.call('Battle_SetFormation',self.m(1,dto)),'Battle_GetFormation')
        self.dispatch(team=1)
        self.assertEqual(self.a.enemies({'town_uin':self.p['uin']}),[])


if __name__=='__main__':unittest.main()
