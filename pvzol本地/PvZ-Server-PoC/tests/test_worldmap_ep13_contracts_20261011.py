"""EP13 town quota, formation, mine timer and legacy mission contracts.

Real handlers and battle engine, copied sources, disposable accounts only.
No game, socket, real player database or external message is opened.
"""
import contextlib, importlib, json, sqlite3, unittest
from unittest.mock import patch
from worldmap_test_support import IsolatedServer

def body(messages,suffix):
    return next(b for n,b in messages if n.endswith('.Cmd_'+suffix+'_SC'))
def names(messages):return [n for n,b in messages]

class FlowTests(unittest.TestCase):
    serial=0
    @classmethod
    def setUpClass(cls):
        global g, wm, resources, a, r, store, DB, query_worldmap_mail_report
        cls.qa = IsolatedServer()
        g, store = cls.qa.game, cls.qa.store
        DB = store.DB_PATH
        wm = importlib.import_module('worldmap_local')
        resources = importlib.import_module('worldmap_resources')
        query_worldmap_mail_report = importlib.import_module('missing_features_local').query_worldmap_mail_report
        a, r = g._worldmap_adapter, g._worldmap_adapter.resources
    @classmethod
    def tearDownClass(cls):
        cls.qa.close()
    def setUp(self):
        type(self).serial+=1
        self.uin=99906100+self.serial
        p,_=store.get_or_create_player(self.uin)
        store.save_player(self.uin,name='Synthetic EP13',level=21,coin=100000,present_ticket=1000,role_created=1)
        self.p,_=store.get_or_create_player(self.uin)
        self.p['plants']=store.get_player_plants(self.uin)
        g.rebuild_td_roster(self.p);g.ensure_player_cards(self.p)
        with contextlib.closing(sqlite3.connect(DB)) as con,con:
            con.execute('UPDATE cards SET level=100 WHERE uin=?',(self.uin,))
        self.now=1800000000
        clock=patch.object(wm,'_now',side_effect=lambda:self.now);clock.start();self.addCleanup(clock.stop)
        self.pos=(20,20); self.req=wm._m(1,wm._position(self.pos))
        self.team=1
        a.get_formation(b'',self.p)
    def call(self,name,data=b'',player=None):
        return g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_'+name+'_CS'](data,player or self.p)
    def mine_row(self):
        with r.db(self.p) as con:return r.mine(con,self.p,self.pos)
    def dispatch(self):return self.call('Mine_SetOutTeam',self.req+wm._v(2,self.team))
    def job(self):
        with r.db(self.p) as con:
            return dict(con.execute('SELECT * FROM local_worldmap_mine_missions WHERE uin=? ORDER BY id DESC LIMIT 1',(self.uin,)).fetchone())
    def capture(self):
        self.dispatch();job=self.job();self.now=job['arrive_at'];return r.advance_missions(self.p)
    def test_video_five_town_attempts_with_purchased_bonus(self):
        dto=body(self.call('Setout_GetSetoutTimes',wm._v(1,1)),'Setout_GetSetoutTimes')
        self.assertEqual((wm._one(dto,1),wm._one(dto,2),wm._one(dto,3)),(1,5,5))
        with a.db(self.p) as con:con.execute('UPDATE local_worldmap_state SET used=5,bonus=2 WHERE uin=?',(self.uin,))
        dto=body(self.call('Setout_GetSetoutTimes',wm._v(1,1)),'Setout_GetSetoutTimes')
        self.assertEqual(wm._one(dto,3),2)
    def test_mine_does_not_spend_or_require_town_allowance(self):
        with a.db(self.p) as con:con.execute('UPDATE local_worldmap_state SET used=5 WHERE uin=?',(self.uin,))
        out=self.dispatch()
        self.assertIn('PVZ.Cmd.Cmd_Mine_SetOutTeam_SC',names(out))
        with a.db(self.p) as con:self.assertEqual(a.state(con,self.p)['used'],5)
    def test_garrison_must_be_cancelled_before_mine_dispatch(self):
        self.call('Battle_SetDefendTeam',wm._v(1,self.team))
        dto=body(self.call('Battle_GetFormation',wm._v(1,self.team)),'Battle_GetFormation')
        self.assertEqual(wm._one(wm._one(dto,1,2),8),1)
        self.assertEqual(wm._one(body(self.dispatch(),'LogicError'),1),4721)
        self.call('Battle_CancelDefendTeam',wm._v(1,self.team))
        dto=body(self.call('Battle_GetFormation',wm._v(1,self.team)),'Battle_GetFormation')
        self.assertEqual(wm._one(wm._one(dto,1,2),8),0)
        self.assertIn('PVZ.Cmd.Cmd_Mine_SetOutTeam_SC',names(self.dispatch()))
    def test_same_marching_or_guard_team_cannot_dispatch_again(self):
        self.dispatch()
        error=self.call('Mine_SetOutTeam',wm._m(1,wm._position((26,20)))+wm._v(2,self.team))
        self.assertEqual(wm._one(body(error,'LogicError'),1),4702)
        self.now=self.job()['arrive_at'];r.advance_missions(self.p)
        self.assertEqual(self.job()['state'],resources.GUARDING)
        error=self.call('Mine_SetOutTeam',wm._m(1,wm._position((26,20)))+wm._v(2,self.team))
        self.assertEqual(wm._one(body(error,'LogicError'),1),4702)
    def test_arrival_mail_once_and_no_automatic_battle_scene(self):
        out=self.capture();job=self.job();self.assertTrue(job['won'])
        self.assertNotIn('PVZ.Cmd.Cmd_Battle_QueryReport_SC',names(out))
        self.assertIn('PVZ.Cmd.Cmd_Mail_NewMessage_SC',names(out))
        with r.db(self.p) as con:
            row=con.execute('SELECT * FROM local_mails WHERE uin=?',(self.uin,)).fetchone()
            self.assertEqual(row['mail_type'],10)
            self.assertIn('(20,20)',row['title'])
            self.assertIn('event:battleId#'+str(job['report_id']),row['content'])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_mail_events WHERE uin=?',(self.uin,)).fetchone()[0],1)
        self.assertEqual(r.advance_missions(self.p),[])
        with r.db(self.p) as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM local_mails WHERE uin=?',(self.uin,)).fetchone()[0],1)
        replay=r.query_report(job['report_id'],self.p)
        persisted=query_worldmap_mail_report(job['report_id'],db_path=str(DB))
        self.assertEqual(replay,persisted)
        self.assertTrue(wm._fields(replay))
    def test_capture_mining_and_exact_eight_hour_collect_boundary(self):
        self.capture();dto=body(self.call('Mine_GetInfo',self.req),'Mine_GetInfo')
        holder=wm._fields(dto)[5][0][1]
        self.assertEqual(wm._one(dto,2),2);self.assertEqual(wm._one(holder,6),0);self.assertEqual(wm._one(holder,7),1)
        self.call('Mine_Mining',self.req);started=self.mine_row()['started']
        self.assertEqual(int(g._cfg_value('iMineCollectCDTime',self.p)),28800)
        self.now=started+28799
        self.assertEqual(wm._one(body(self.call('Mine_CollectProduct',self.req),'LogicError'),1),4760)
        self.now+=1
        self.assertIn('PVZ.Cmd.Cmd_Mine_CollectProduct_SC',names(self.call('Mine_CollectProduct',self.req)))
        self.assertEqual(self.mine_row()['started'],self.now)
    def test_mail_failure_rolls_back_capture_and_can_retry(self):
        self.dispatch();self.now=self.job()['arrive_at']
        import missing_features_local as mail
        with patch.object(mail,'queue_worldmap_mine_mail',side_effect=RuntimeError('synthetic mail failure')):
            with self.assertRaisesRegex(RuntimeError,'synthetic mail failure'):r.advance_missions(self.p)
        self.assertEqual(self.job()['settled'],0);self.assertEqual(self.mine_row()['held'],0)
        with r.db(self.p) as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_resource_reports WHERE uin=?',(self.uin,)).fetchone()[0],0)
        self.assertIn('PVZ.Cmd.Cmd_Mail_NewMessage_SC',names(r.advance_missions(self.p)))
    def test_guard_recall_returns_army_but_preserves_independent_mine_cart(self):
        self.capture();self.call('Mine_Mining',self.req)
        started=self.mine_row()['started']
        out=self.call('Mine_RecallTeam',self.req)
        self.assertIn('PVZ.Cmd.Cmd_Mine_RecallTeam_SC',names(out))
        self.assertEqual(self.mine_row()['team_id'],0)
        self.assertEqual(self.mine_row()['started'],started)
        self.assertEqual(self.job()['state'],wm.RECALLING)
        self.now=self.job()['home_at'];r.advance_missions(self.p)
        self.assertEqual(self.job()['state'],wm.HOME)
        with a.db(self.p) as con:self.assertFalse(a.team_busy(con,self.p,self.team))
    def test_original_24_hour_guard_deadline_then_returns_without_canceling_cart(self):
        self.capture();self.call('Mine_Mining',self.req)
        job=self.job();started=self.mine_row()['started']
        self.assertEqual(job['guard_until']-job['arrive_at'],86400)
        dto=r.mission_history_dto(self.p,job)
        self.assertEqual(wm._one(dto,7),job['guard_until'])
        self.now=job['guard_until']-1;self.assertEqual(r.advance_missions(self.p),[])
        self.now+=1;out=r.advance_missions(self.p)
        self.assertEqual(self.job()['state'],wm.RETURNING)
        self.assertEqual(self.mine_row()['team_id'],0);self.assertEqual(self.mine_row()['started'],started)
        dto=r.mission_history_dto(self.p,self.job())
        self.assertEqual(wm._one(dto,6),job['guard_until'])
        self.assertEqual(wm._one(dto,7),job['guard_until']+job['period'])
        self.assertNotIn('PVZ.Cmd.Cmd_Mail_NewMessage_SC',names(out))
        self.now=self.job()['home_at'];r.advance_missions(self.p)
        self.assertEqual(self.job()['state'],wm.HOME)
    def test_reconnect_polls_timed_guard_after_deadline_and_offline_return(self):
        self.capture();job=self.job()
        a.seen_users.discard(self.uin);a.active_users.discard(self.uin)
        self.now=job['guard_until']+job['period']
        out=wm.poll(g,self.p)
        self.assertEqual(self.job()['state'],wm.HOME)
        self.assertEqual(self.mine_row()['team_id'],0)
        self.assertIn('PVZ.Cmd.Cmd_War_SyncHistory_SC',names(out))
    def test_timestamped_old_guard_migrates_once_and_reconnect_returns_offline(self):
        self.capture();self.call('Mine_Mining',self.req)
        old_job=self.job();started=self.mine_row()['started']
        # Recreate the real previous schema, without touching a real account.
        with contextlib.closing(sqlite3.connect(DB)) as con,con:
            con.execute('ALTER TABLE local_worldmap_mine_missions DROP COLUMN guard_until')
        a.seen_users.discard(self.uin);a.active_users.discard(self.uin)
        self.now=old_job['arrive_at']+86400+old_job['period']
        out=wm.poll(g,self.p)
        migrated=self.job()
        self.assertEqual(migrated['guard_until'],old_job['arrive_at']+86400)
        self.assertEqual(migrated['state'],wm.HOME)
        self.assertEqual(self.mine_row()['team_id'],0)
        self.assertEqual(self.mine_row()['started'],started)
        self.assertIn('PVZ.Cmd.Cmd_War_SyncHistory_SC',names(out))
        self.p['level']=200
        with r.db(self.p):pass
        self.assertEqual(self.job()['guard_until'],migrated['guard_until'])
    def test_old_guard_migration_uses_high_level_template_and_is_account_scoped(self):
        self.capture();old_job=self.job()
        with contextlib.closing(sqlite3.connect(DB)) as con,con:
            con.execute('UPDATE local_worldmap_mine_missions SET guard_until=0 WHERE uin=?',(self.uin,))
            con.execute('INSERT INTO local_worldmap_mine_missions('
                        'uin,area,x,y,team_id,origin_x,origin_y,state,begin_at,arrive_at,home_at,period,'
                        'formation,placements,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (self.uin+200000,1,26,20,1,18,18,resources.GUARDING,self.now-100,self.now,0,20,b'','{}',self.now))
            con.execute('INSERT INTO local_worldmap_mine_missions('
                        'uin,area,x,y,team_id,origin_x,origin_y,state,begin_at,arrive_at,home_at,period,'
                        'formation,placements,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (self.uin,1,20,26,2,18,18,resources.GUARDING,0,0,0,20,b'','{}',0))
        self.p['level']=100
        self.assertEqual(r.cart_limit(self.p)[1],86400)
        self.p['level']=101
        self.assertEqual(r.cart_limit(self.p)[1],87000)
        self.p['level']=200
        self.assertEqual(r.cart_limit(self.p)[1],92400)
        with r.db(self.p) as con:
            mine=con.execute('SELECT guard_until FROM local_worldmap_mine_missions WHERE id=?',(old_job['id'],)).fetchone()[0]
            self.assertEqual(mine,old_job['arrive_at']+92400)
            self.assertEqual(con.execute('SELECT guard_until FROM local_worldmap_mine_missions WHERE uin=?',(self.uin+200000,)).fetchone()[0],0)
            self.assertEqual(con.execute('SELECT guard_until FROM local_worldmap_mine_missions WHERE uin=? AND team_id=2',(self.uin,)).fetchone()[0],0)
        self.p['level']=225
        with r.db(self.p) as con:
            self.assertEqual(con.execute('SELECT guard_until FROM local_worldmap_mine_missions WHERE id=?',(old_job['id'],)).fetchone()[0],mine)
    def test_overlapping_training_residence_hidden_without_moving_real_town(self):
        target_uin=self.uin+100000
        other,_=store.get_or_create_player(target_uin)
        store.save_player(target_uin,name='Synthetic real town',level=21,role_created=1)
        other,_=store.get_or_create_player(target_uin)
        with a.db(other) as con:con.execute('UPDATE local_worldmap_state SET x=23,y=17 WHERE uin=?',(target_uin,))
        dto=body(self.call('WorldMap_GetInfo',wm._v(5,1)),'WorldMap_GetInfo')
        roles=[wm._one(c,3) for wire,c in wm._fields(dto).get(1,[]) if wire==2 and wm._one(c,1)==1]
        self.assertIn(other['role_id'],roles)
        self.assertNotIn(wm.NPCS[0]['role_id'],roles)
        with a.db(other) as con:self.assertEqual((a.state(con,other)['x'],a.state(con,other)['y']),(23,17))
        target=a.target(self.p,role_id=wm.NPCS[0]['role_id'])
        self.assertEqual(target['pos'],(24,18))
        self.call('War_DeclareWar',wm._v(1,target['role_id']))
        self.now+=wm.DECLARE_WAIT_SECONDS+2
        self.assertIn('PVZ.Cmd.Cmd_War_SetOut_SC',names(self.call('War_SetOut',wm._v(1,2)+wm._m(2,wm._position(target['pos']))+wm._v(3,self.team))))

if __name__ == '__main__':
    unittest.main(verbosity=2)
