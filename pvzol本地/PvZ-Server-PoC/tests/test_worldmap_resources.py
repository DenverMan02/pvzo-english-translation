"""Mine/temple reconstruction acceptance using only disposable synthetic saves."""
import concurrent.futures
import copy
import importlib
import json
import sqlite3
from contextlib import closing
import threading
import time
import unittest
from unittest.mock import patch
from worldmap_test_support import IsolatedServer, fields, many, message, one


class ResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = IsolatedServer()
        cls.g = cls.qa.game
        cls.wm = importlib.import_module('worldmap_local')
        cls.res = importlib.import_module('worldmap_resources')
        cls.a = cls.g._worldmap_adapter
        if cls.a.resources is None:
            cls.g.REQUEST_HANDLERS.update(cls.res.make_handlers(cls.a))
        cls.r = cls.a.resources
        cls.serial = 0

    @classmethod
    def tearDownClass(cls):
        cls.qa.close()

    def setUp(self):
        type(self).serial += 1
        self.p = self.qa.player(99205000+self.serial, level=100, coin=1000, role_created=1)
        self.g.rebuild_td_roster(self.p)
        self.g.ensure_player_cards(self.p)
        # Current combat uses the real authored HP/stat scale. These tests need
        # successful capture to exercise mining and garrison state transitions.
        self.qa.train_owned_cards(self.p,level=100)
        self.now = 1800000000
        clock = patch.object(self.wm, '_now', side_effect=lambda: self.now)
        clock.start(); self.addCleanup(clock.stop)
        # Legacy transaction/replay tests use seconds of synthetic elapsed
        # time to keep their small-amount accounting assertions readable.
        # Real eight-hour admission and Init configuration are independently
        # covered by test_worldmap_video_mines_20261010 without this override.
        real_config = self.g._cfg_value
        accelerated = patch.object(self.g, '_cfg_value', side_effect=lambda cid, player=None:
            '1' if cid == 'iMineCollectCDTime' else real_config(cid, player))
        accelerated.start(); self.addCleanup(accelerated.stop)
        self.mine_pos = self.m(1, self.pos(20,20))
        self.starter = self.a.placements(self.p, 1)
        self.set_team(self.wm.TEAM_ID, self.starter)

    def v(self,n,v): return self.g.pb_varint_field(n,v)
    def m(self,n,v): return self.g.pb_bytes(n,v)
    def pos(self,x,y): return self.v(1,x)+self.v(2,y)
    def call(self,name,data=b'',player=None):
        messages = self.qa.invoke(name,data,player or self.p)
        # These established resource tests exercise completed operations. The
        # separate mine-march/video suites inspect raw dispatch and home ETAs.
        if (name == 'Mine_RecallTeam' and threading.current_thread() is threading.main_thread()
                and not any(k.endswith('LogicError_SC') for k,b in messages)):
            subject = player or self.p
            with self.r.db(subject) as con:
                job = con.execute('SELECT * FROM local_worldmap_mine_missions WHERE uin=? AND state=? '
                                  'ORDER BY id DESC LIMIT 1', (subject['uin'],self.wm.RECALLING)).fetchone()
            if job:
                self.now = max(self.now,job['home_at'])
                later = self.a.advance(subject)
                messages = [(k,b) for k,b in messages if not k.endswith('War_SyncHistory_SC')] + later
        for kind, body in messages:
            fields(body)
        return messages
    def error(self, messages):
        self.assertTrue(any(k.endswith('LogicError_SC') for k,b in messages), messages)
    def balance(self): return self.qa.store.get_or_create_player(self.p['uin'])[0]['coin']
    def set_team(self, team, placements):
        dto = self.v(5,team) + b''.join(self.m(1,self.v(1,pos)+self.v(2,uid)) for pos,uid in placements.items())
        message(self.call('Battle_SetFormation',self.m(1,dto)),'Battle_GetFormation')
    def split_teams(self):
        home = dict(list(self.starter.items())[:2])
        self.set_team(1,home)
        return home, {p:u for p,u in self.starter.items() if u not in home.values()}
    def occupy(self, team=None):
        if team == 1 and not self.a.placements(self.p,1):
            self.set_team(1,self.starter)
        msgs = self.call('Mine_SetOutTeam',self.mine_pos+self.v(2,team or self.wm.TEAM_ID))
        message(msgs,'Mine_SetOutTeam')
        with self.r.db(self.p) as con:
            job = con.execute('SELECT * FROM local_worldmap_mine_missions WHERE uin=? AND team_id=? AND state=?',
                              (self.p['uin'],team or self.wm.TEAM_ID,self.wm.MARCHING)).fetchone()
        if job:
            self.now = max(self.now,job['arrive_at'])
            later = self.a.advance(self.p)
            msgs = [(k,b) for k,b in msgs if not k.endswith('War_SyncHistory_SC')] + later
        row = self.mine()
        self.assertEqual(row['held'],1,'trained owned-card fixture should defeat local mine guard')
        return msgs
    def mine(self):
        with self.r.db(self.p) as con:return self.r.mine(con,self.p,(20,20))
    def latest_mine_report(self, arrival_messages):
        self.assertFalse(any(k.endswith('Battle_QueryReport_SC') for k,_ in arrival_messages))
        message(arrival_messages,'Mail_NewMessage')
        with self.r.db(self.p) as con:
            report_id=con.execute('SELECT report_id FROM local_worldmap_mine_missions WHERE uin=? ORDER BY id DESC LIMIT 1',
                                  (self.p['uin'],)).fetchone()[0]
        self.assertGreater(report_id,self.res.REPORT_BASE)
        return one(message(self.call('Battle_QueryReport',self.v(1,report_id)),'Battle_QueryReport'),1)
    def finish_mines(self, state=None):
        state = self.wm.MARCHING if state is None else state
        with self.r.db(self.p) as con:
            jobs = con.execute('SELECT * FROM local_worldmap_mine_missions WHERE uin=? AND state=?',
                               (self.p['uin'],state)).fetchall()
        if jobs:
            self.now=max(self.now,max(r['arrive_at'] if state==self.wm.MARCHING else r['home_at'] for r in jobs))
        return self.a.advance(self.p)
    def guild(self,level=5):
        with self.r.db(self.p) as con:
            cur=con.execute('INSERT INTO local_guilds(name,level,created_at) VALUES(?,?,?)',
                ('Resource QA '+str(self.p['uin']),level,self.now))
            con.execute('INSERT INTO local_guild_members(guild_id,uin,role_id,name,joined_at) VALUES(?,?,?,?,?)',
                (cur.lastrowid,self.p['uin'],self.p['role_id'],self.p['name'],self.now))
        return cur.lastrowid
    def capture(self, temple_id=6):
        self.call('City_ChangeArea',self.v(1,2))
        t=self.r.temple(temple_id=temple_id)
        return self.call('Temple_SetOut',self.m(1,self.pos(*t['pos']))+self.v(2,self.wm.TEAM_ID)+self.v(3,2))
    def count(self, item):
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con, con:
            row=con.execute('SELECT count FROM items WHERE uin=? AND item_id=?',(self.p['uin'],item)).fetchone()
        return row[0] if row else 0

    def test_map_cells_have_real_temple_ids_positions_and_mine_types(self):
        for area in (1,2):
            body=message(self.call('WorldMap_GetInfo',self.v(5,area)),'WorldMap_GetInfo')
            mines=[c for c in many(body,1) if one(c,1)==2]
            expected = {(x,y) for x in (2,8,14,20,26,32) for y in (2,8,14,20,26,32)}-{(26,26)}
            self.assertEqual({(one(one(c,2),1),one(one(c,2),2)) for c in mines},expected)
            self.assertTrue(all(one(one(c,2),1)%6==2 and one(one(c,2),2)%6==2 for c in mines))
            self.assertTrue(all(one(c,3)>0 for c in mines),'roleId0 is a noninteractive fake mine in AS')
            temples=[c for c in many(body,1) if one(c,1)==3]
            self.assertEqual(len(temples),0 if area==1 else 17)
            for cell in temples:
                t=self.r.temple(temple_id=one(cell,50))
                self.assertEqual(one(cell,2),self.pos(*t['pos']))
                self.assertEqual(one(cell,15),t['area'])

    def test_mine_capture_starts_real_report_and_locks_frozen_team(self):
        before=self.balance();msgs=self.occupy()
        report=self.latest_mine_report(msgs)
        self.assertEqual(one(report,5),7)
        self.assertTrue(many(report,4),'existing engine creates replay rounds')
        self.assertEqual(self.balance(),before,'capture is not a currency faucet')
        dto=one(message(self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID)),'Battle_GetFormation'),1)
        self.assertEqual(one(dto,6),1)
        self.error(self.call('Battle_SetFormation',self.m(1,dto)))
        self.error(self.call('War_SetOut',self.v(1,2)+self.m(2,self.pos(24,18))+self.v(3,self.wm.TEAM_ID)))
        self.error(self.call('City_ChangeArea',self.v(1,2)))
        self.call('Mine_RecallTeam',self.mine_pos)
        self.assertFalse(self.r.busy(self.p));self.assertEqual(self.balance(),before)

    def test_mining_collect_repeat_restart_cancel_and_recall(self):
        self.occupy();self.call('Mine_Mining',self.mine_pos)
        self.assertEqual(self.mine()['started'],self.now)
        self.now+=7;self.call('Mine_Mining',self.mine_pos)
        self.assertEqual(self.mine()['started'],self.now-7,'double start preserves clock')
        holder=many(message(self.call('Mine_GetInfo',self.mine_pos),'Mine_GetInfo'),5)[0]
        self.assertEqual(one(holder,4),self.now-7,'client subtracts this absolute timestamp')
        self.assertEqual(one(holder,5),70)
        before=self.balance();msgs=self.call('Mine_CollectProduct',self.mine_pos)
        self.assertEqual(one(message(msgs,'Mine_CollectProduct'),2),70)
        self.assertEqual(self.balance(),before+70)
        self.assertEqual(self.mine()['started'],self.now,'CollectProduct resets the AS mining epoch')
        for _ in range(3): self.call('Mine_CollectProduct',self.mine_pos,copy.deepcopy(self.p))
        self.assertEqual(self.balance(),before+70)
        self.now+=3;self.call('Mine_CancelMining',self.mine_pos)
        self.assertEqual(self.balance(),before+100);self.assertEqual(self.mine()['started'],0)
        self.call('Mine_CancelMining',self.mine_pos);self.assertEqual(self.balance(),before+100)
        self.call('Mine_Mining',self.mine_pos);self.now+=4
        recall=self.call('Mine_RecallTeam',self.mine_pos)
        self.assertFalse(any(k.endswith('Mine_CancelMining_SC') for k,b in recall))
        self.assertEqual(self.balance(),before+100,'recall only releases the defending team')
        self.assertEqual(self.mine()['team_id'],0)
        cart_epoch=self.mine()['started']
        self.call('Mine_RecallTeam',self.mine_pos);self.assertEqual(self.balance(),before+100)
        self.now+=2;self.call('Mine_CollectProduct',self.mine_pos)
        self.assertEqual(self.balance(),before+100+(self.now-cart_epoch)*self.res.MINING_RATE,
                         'unguarded mine cart also produces while the army returns')
        self.call('Mine_CancelMining',self.mine_pos);self.assertEqual(self.mine()['started'],0)
        self.call('Mine_Mining',self.mine_pos)
        self.assertEqual(self.mine()['started'],self.now,'held mine can start a cart without a guard')

    def test_long_offline_cap_and_concurrent_claims(self):
        self.occupy();self.call('Mine_Mining',self.mine_pos);before=self.balance()
        self.now+=86400*10
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _: self.call('Mine_CollectAllMine',player=copy.deepcopy(self.p)),range(4)))
        self.assertEqual(self.balance(),before+self.res.MINING_CAP)
        self.assertEqual(sum(one(message(r,'Mine_CollectAllMine'),1) for r in results),self.res.MINING_CAP)
        self.assertEqual(self.mine()['started'],self.now,'collect continues with a new client-aligned mining epoch')
        # A fresh adapter recreates no earned product or claimed receipt.
        reconnect=self.res.ResourceGameplay(self.a)
        self.assertEqual(one(message(reconnect.collect_all(b'',copy.deepcopy(self.p)),'Mine_CollectAllMine'),1),0)

    def test_resource_formation_home_team_lock_and_account_isolation(self):
        self.occupy(team=1)
        dto=one(message(self.call('Battle_GetFormation',self.v(1,1)),'Battle_GetFormation'),1)
        self.assertEqual(one(dto,6),1)
        self.error(self.call('Battle_SetFormation',self.m(1,dto)))
        other=self.qa.player(99305000+self.serial,level=100,coin=2345,role_created=1)
        self.error(self.call('Mine_Mining',self.mine_pos,other))
        self.assertFalse(self.r.busy(other));self.assertEqual(self.qa.store.get_or_create_player(other['uin'])[0]['coin'],2345)
        self.call('Mine_RecallTeam',self.mine_pos)
        self.assertFalse(self.r.busy(self.p))

    def test_invalid_cells_generic_fight_and_no_cart_under_level10(self):
        for cmd in ('Mine_GetInfo','Mine_Mining','Mine_CollectProduct','Mine_RecallTeam'):
            self.error(self.call(cmd,self.m(1,self.pos(999,999))))
        for kind in (6,7):
            self.error(self.call('Battle_GetFightInfo',self.m(1,self.v(1,kind))))
            self.error(self.call('Battle_Fight',self.m(1,self.v(1,kind))))
        self.p['level']=1
        self.qa.store.save_player(self.p['uin'],level=1)
        self.error(self.call('Mine_SetOutTeam',self.mine_pos+self.v(2,self.wm.TEAM_ID)))
        self.assertFalse(self.r.busy(self.p));self.assertEqual(self.balance(),1000)

    def test_temple_real_guild_gate_capture_and_atomic_daily_chest(self):
        self.error(self.capture())
        guild=self.guild(level=3);self.error(self.capture())
        with self.r.db(self.p) as con:con.execute('UPDATE local_guilds SET level=4 WHERE guild_id=?',(guild,))
        before=self.count(3904);msgs=self.capture()
        self.assertEqual(one(one(message(msgs,'Battle_QueryReport'),1),5),6)
        with self.r.db(self.p) as con:
            row=self.r.temple_row(con,self.p,self.r.temple(temple_id=6))
        self.assertTrue(row['captured_at'])
        self.assertEqual(self.balance(),1000)
        self.assertEqual(one(message(self.call('Temple_ShowDailyReward'),'Temple_ShowDailyReward'),1),0)
        self.call('Temple_SetOut',self.m(1,self.pos(77,77))+self.v(2,self.wm.TEAM_ID)+self.v(3,2))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.call('Temple_TakeDailyReward',player=copy.deepcopy(self.p)),range(4)))
        self.assertEqual(sum(any(k.endswith('Temple_TakeDailyReward_SC') for k,b in r) for r in results),1)
        self.assertEqual(self.count(3904),before+1)
        self.assertEqual(one(message(self.call('Temple_ShowDailyReward'),'Temple_ShowDailyReward'),1),1)
        self.now+=86400;self.call('Temple_TakeDailyReward');self.assertEqual(self.count(3904),before+2)

    def test_mixed_concurrent_collect_cancel_recall_grants_elapsed_once(self):
        self.occupy();self.call('Mine_Mining',self.mine_pos);before=self.balance()
        self.now+=25
        commands=['Mine_CollectAllMine','Mine_CollectProduct','Mine_CancelMining','Mine_RecallTeam']*2
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda command:self.call(command,b'' if command.endswith('AllMine') else self.mine_pos,
                                                   copy.deepcopy(self.p)),commands))
        self.assertEqual(self.balance(),before+250)
        self.assertEqual(self.mine()['started'],0)
        self.assertEqual(self.mine()['team_id'],0)
        self.finish_mines(self.wm.RECALLING)
        self.assertFalse(self.r.busy(self.p))

    def test_failed_capture_replay_cooldown_and_no_rewards(self):
        request=self.mine_pos+self.v(2,self.wm.TEAM_ID)
        with patch.object(self.a,'make_report_details',return_value=(False,self.v(1,1),{})):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:self.call('Mine_SetOutTeam',request,copy.deepcopy(self.p)),range(2)))
            self.assertEqual(sum(any(k.endswith('Battle_QueryReport_SC') for k,b in r) for r in results),0)
            self.latest_mine_report(self.finish_mines())
            self.assertEqual(self.mine()['held'],0);self.assertTrue(self.r.busy(self.p))
            self.finish_mines(self.wm.RETURNING)
            self.assertFalse(self.r.busy(self.p))
            self.assertEqual(self.balance(),1000)
            with self.a.db(self.p) as con:self.assertEqual(self.a.state(con,self.p)['used'],0)
            self.error(self.call('Mine_SetOutTeam',request))
            self.now+=self.res.RETRY_SECONDS
            message(self.call('Mine_SetOutTeam',request),'Mine_SetOutTeam')
            self.latest_mine_report(self.finish_mines())
            with self.a.db(self.p) as con:self.assertEqual(self.a.state(con,self.p)['used'],0)
            self.assertEqual(self.balance(),1000)

    def test_all_temple_reward_tiers_and_local_midnight_boundary(self):
        guild_id=self.guild()
        temples,rewards,limits=self.r.templates()
        self.assertEqual(set(rewards),{(kind,count) for kind in (1,2) for count in (1,2,3)})
        for count in (1,2,3,4):
            with self.r.db(self.p) as con:
                con.execute('DELETE FROM local_worldmap_temples WHERE uin=?',(self.p['uin'],))
                for kind in (1,2):
                    for temple in [t for t in temples if t['type']==kind][:count]:
                        con.execute('INSERT INTO local_worldmap_temples(uin,temple_id,captured_at,guild_id) VALUES(?,?,?,?)',
                                    (self.p['uin'],temple['id'],self.now,guild_id))
                day,claimed,counts,actual=self.r.reward_state(con,self.p)
                self.assertEqual(actual,[rewards[(kind,min(3,count))] for kind in (1,2)])
        local=time.localtime(self.now)
        midnight=int(time.mktime((local.tm_year,local.tm_mon,local.tm_mday+1,0,0,0,0,0,-1)))
        self.now=midnight-1
        self.call('Temple_TakeDailyReward')
        self.error(self.call('Temple_TakeDailyReward'))
        with self.r.db(self.p) as con:first_day=self.r.reward_state(con,self.p)[0]
        self.now=midnight
        with self.r.db(self.p) as con:
            next_day,claimed,counts,rewards=self.r.reward_state(con,self.p)
            self.assertNotEqual(first_day,next_day);self.assertFalse(claimed)
        self.call('Temple_TakeDailyReward')

    def test_mining_bank_totals_and_collect_cooldown_match_client(self):
        self.occupy();self.call('Mine_Mining',self.mine_pos)
        with patch.object(self.g,'_cfg_value',return_value='30'):
            self.now+=5
            dto=message(self.call('Mine_GetMiningInfo'),'Mine_GetMiningInfo')
            self.assertEqual(one(dto,1),1);self.assertEqual(one(dto,2),50)
            self.assertEqual(one(dto,3),0,'unripe gold is produced but not collectable')
            self.error(self.call('Mine_CollectProduct',self.mine_pos))
            self.assertEqual(one(message(self.call('Mine_CollectAllMine'),'Mine_CollectAllMine'),1),0)
            self.now+=25
            dto=message(self.call('Mine_GetMiningInfo'),'Mine_GetMiningInfo')
            self.assertEqual(one(dto,2),300);self.assertEqual(one(dto,3),300)
            self.call('Mine_CollectProduct',self.mine_pos)
            dto=message(self.call('Mine_GetMiningInfo'),'Mine_GetMiningInfo')
            self.assertEqual(one(dto,2),0);self.assertEqual(one(dto,3),0)

    def test_mine_capture_preserves_town_bonus_attempt_and_persists_frozen_injury(self):
        placements=self.a.placements(self.p,self.wm.TEAM_ID)
        after={str(uid):5000 for uid in placements.values()}
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET used=?,bonus=1 WHERE uin=?',
                        (self.wm.DAILY_LIMIT,self.p['uin']))
        with patch.object(self.a,'make_report_details',return_value=(True,self.v(1,0),after)):
            self.occupy()
        with self.a.db(self.p) as con:
            st=self.a.state(con,self.p)
            self.assertEqual(st['used'],self.wm.DAILY_LIMIT)
            self.assertEqual(st['bonus'],1)
            self.assertEqual(json.loads(st['health']),after)
        frozen=self.r.frozen_formation(self.p,self.wm.TEAM_ID)
        for card in many(frozen,1):self.assertEqual(one(card,4),one(card,5)//2)
        self.error(self.call('War_RecoverHp',self.v(1,self.wm.TEAM_ID)))
        before=self.balance();self.call('War_RecoverHp',self.v(1,1))
        self.assertEqual(self.balance(),before,'empty home team must not heal deployed plants')
        self.call('Mine_RecallTeam',self.mine_pos)
        ordinary=one(message(self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID)),'Battle_GetFormation'),1)
        for card in many(ordinary,1):self.assertEqual(one(card,4),one(card,5)//2)
        cost=one(ordinary,7)
        before=self.balance();self.call('War_RecoverHp',self.v(1,self.wm.TEAM_ID))
        self.assertEqual(self.balance(),before-cost)
        with self.a.db(self.p) as con:
            self.assertTrue(all(v==10000 for v in json.loads(self.a.state(con,self.p)['health']).values()))

    def test_temple_reward_cannot_follow_departed_or_switched_guild(self):
        guild=self.guild();self.capture()
        with self.r.db(self.p) as con:
            membership=dict(con.execute('SELECT * FROM local_guild_members WHERE uin=?',(self.p['uin'],)).fetchone())
            con.execute('DELETE FROM local_guild_members WHERE uin=?',(self.p['uin'],))
        self.error(self.call('Temple_TakeDailyReward'))
        self.assertEqual(one(message(self.call('Temple_ShowDailyReward'),'Temple_ShowDailyReward'),1),4)
        with self.r.db(self.p) as con:
            new=con.execute('INSERT INTO local_guilds(name,level,created_at) VALUES(?,?,?)',
                            ('Other resource QA '+str(self.p['uin']),5,self.now)).lastrowid
            con.execute('INSERT INTO local_guild_members(guild_id,uin,role_id,name,joined_at) VALUES(?,?,?,?,?)',
                        (new,self.p['uin'],self.p['role_id'],self.p['name'],self.now))
        self.error(self.call('Temple_TakeDailyReward'))
        with self.r.db(self.p) as con:con.execute('UPDATE local_guild_members SET guild_id=? WHERE uin=?',(guild,self.p['uin']))
        self.call('Temple_TakeDailyReward')
        self.now+=86400
        with self.r.db(self.p) as con:con.execute('UPDATE local_guilds SET level=3 WHERE guild_id=?',(guild,))
        self.error(self.call('Temple_TakeDailyReward'))

    def test_duplicate_uid_home_formation_cannot_capture_resource(self):
        self.set_team(1,self.starter)
        self.a.placements(self.p,1)
        with self.a.db(self.p) as con:
            rows=con.execute('SELECT pos_index,uid FROM formation WHERE uin=? AND team_id=1',
                             (self.p['uin'],)).fetchall()
            self.assertGreaterEqual(len(rows),2)
            con.execute('UPDATE formation SET uid=? WHERE uin=? AND team_id=1 AND pos_index=?',
                        (rows[0]['uid'],self.p['uin'],rows[1]['pos_index']))
        with patch.object(self.a,'make_report_details',side_effect=AssertionError('invalid lineup reached battle')):
            self.error(self.call('Mine_SetOutTeam',self.mine_pos+self.v(2,1)))
        self.assertEqual(self.balance(),1000);self.assertFalse(self.r.busy(self.p))

    def test_temple_report_reconnect_scoping_and_paging(self):
        self.guild();self.capture()
        with self.r.db(self.p) as con:
            row=con.execute('SELECT id,report FROM local_worldmap_resource_reports WHERE uin=?',(self.p['uin'],)).fetchone()
        report_id=self.res.REPORT_BASE+row['id']
        self.assertEqual(one(message(self.call('Battle_QueryReport',self.v(1,report_id)),'Battle_QueryReport'),1),bytes(row['report']))
        other=self.qa.player(99405000+self.serial,level=100,coin=11,role_created=1)
        self.error(self.call('Battle_QueryReport',self.v(1,report_id),other))
        listing=message(self.call('Setout_GetTempleList',self.v(2,0)+self.v(3,1)),'Setout_GetTempleList')
        self.assertEqual(one(listing,2),17);self.assertEqual(len(many(listing,1)),1)
        self.assertIsNotNone(self.res.ResourceGameplay(self.a).query_report(report_id,self.p))


    def test_two_disjoint_garrisons_commit_concurrently_and_free_one_team(self):
        home, expedition=self.split_teams()
        def combat(player,target,placements,**_kwargs):
            return True,self.v(1,0),{str(uid):5000 for uid in placements.values()}
        requests=[self.mine_pos+self.v(2,self.wm.TEAM_ID),
                  self.m(1,self.pos(26,20))+self.v(2,1)]
        with patch.object(self.a,'make_report_details',side_effect=combat):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda d:self.call('Mine_SetOutTeam',d,copy.deepcopy(self.p)),requests))
            for result in results:message(result,'Mine_SetOutTeam')
            with self.r.db(self.p) as con:
                self.now=max(r[0] for r in con.execute('SELECT arrive_at FROM local_worldmap_mine_missions WHERE uin=?',
                                                      (self.p['uin'],)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                settled=list(pool.map(lambda _:self.a.advance(copy.deepcopy(self.p)),range(2)))
            self.assertEqual(sum(k.endswith('Battle_QueryReport_SC') for r in settled for k,b in r),0)
            self.assertEqual(sum(k.endswith('Mail_NewMessage_SC') for r in settled for k,b in r),1)
            with self.r.db(self.p) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM local_mails WHERE uin=?',(self.p['uin'],)).fetchone()[0],2)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_resource_reports WHERE uin=?',(self.p['uin'],)).fetchone()[0],2)
        self.assertEqual(self.r.busy_cards(self.p),set(self.starter.values()))
        for team, expected in ((1,home),(self.wm.TEAM_ID,expedition)):
            dto=self.r.frozen_formation(self.p,team)
            self.assertEqual(one(dto,6),1)
            self.assertEqual({one(c,2) for c in many(dto,1)},set(expected.values()))
            self.assertTrue(all(one(c,4)==one(c,5)//2 for c in many(dto,1)))
        with self.a.db(self.p) as con:
            self.assertEqual(self.a.state(con,self.p)['used'],0)
            self.assertEqual(json.loads(self.a.state(con,self.p)['health']),
                             {str(uid):5000 for uid in self.starter.values()})
        for req in requests:message(self.call('Mine_SetOutTeam',req),'Mine_SetOutTeam')
        with self.a.db(self.p) as con:self.assertEqual(self.a.state(con,self.p)['used'],0)
        # Another team may not overwrite an existing defender.
        self.error(self.call('Mine_SetOutTeam',self.mine_pos+self.v(2,1)))
        self.call('Mine_RecallTeam',self.mine_pos)
        self.assertFalse(self.r.team_busy(self.p,self.wm.TEAM_ID))
        self.assertTrue(self.r.team_busy(self.p,1))
        self.assertEqual(self.r.busy_cards(self.p),set(home.values()))
        self.set_team(self.wm.TEAM_ID,expedition)

    def test_legacy_shared_card_cannot_enter_concurrent_garrisons(self):
        # Simulate a previously persisted overlapping lineup, bypassing the UI.
        with self.a.db(self.p) as con:
            con.execute('DELETE FROM formation WHERE uin=? AND team_id=1',(self.p['uin'],))
            con.executemany('INSERT INTO formation(uin,team_id,pos_index,uid,owner_role_id) VALUES(?,1,?,?,0)',
                            [(self.p['uin'],pos,uid) for pos,uid in self.starter.items()])
        barrier=threading.Barrier(2)
        def combat(player,target,placements,**_kwargs):
            barrier.wait(timeout=10)
            return True,self.v(1,0),{}
        requests=[self.mine_pos+self.v(2,self.wm.TEAM_ID),
                  self.m(1,self.pos(26,20))+self.v(2,1)]
        # Bypass lineup repair to specifically exercise atomic server admission.
        with patch.object(self.a,'placements',return_value=self.starter), \
             patch.object(self.a,'make_report_details',side_effect=combat):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda d:self.call('Mine_SetOutTeam',d,copy.deepcopy(self.p)),requests))
        self.assertEqual(sum(any(k.endswith('Mine_SetOutTeam_SC') for k,b in r) for r in results),1)
        self.assertEqual(sum(any(k.endswith('LogicError_SC') for k,b in r) for r in results),1)
        with patch.object(self.a,'make_report_details',return_value=(True,self.v(1,0),{})):
            self.latest_mine_report(self.finish_mines())
        with self.r.db(self.p) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_mines WHERE uin=? AND team_id<>0',
                                         (self.p['uin'],)).fetchone()[0],1)
            self.assertEqual(self.a.state(con,self.p)['used'],0)

    def test_disjoint_city_mission_and_mine_garrison_coexist(self):
        home,expedition=self.split_teams()
        declared=message(self.call('War_DeclareWar',self.v(1,self.wm.NPCS[0]['role_id'])),'War_DeclareWar')
        self.now=one(declared,2)+2
        with patch.object(self.a,'make_report_details',return_value=(True,self.v(1,0),{})):
            self.occupy()
            message(self.call('War_SetOut',self.v(1,2)+self.m(2,self.pos(24,18))+self.v(3,1)),'War_SetOut')
        with self.a.db(self.p) as con:
            self.assertIsNotNone(self.a.mission(con,self.p,1))
            self.assertIsNone(self.a.mission(con,self.p,self.wm.TEAM_ID))
            self.assertEqual(self.a.city_busy_cards(con,self.p),set(home.values()))
            self.assertEqual(self.a.busy_cards(con,self.p),set(self.starter.values()))
        self.call('Mine_RecallTeam',self.mine_pos)
        self.assertFalse(self.r.busy(self.p))
        with self.a.db(self.p) as con:self.assertEqual(self.a.busy_cards(con,self.p),set(home.values()))
        with patch.object(self.a,'make_report_details',return_value=(True,self.v(1,0),{})):
            self.occupy()

    def test_template_cart_limits_and_all_carts_operate_without_guards(self):
        for level,expected in ((9,0),(10,2),(19,2),(20,3),(29,3),(30,4),(100,4)):
            self.p['level']=level
            self.assertEqual(self.r.cart_limit(self.p)[0],expected)
        for level,maximum in ((10,2),(20,3),(30,4)):
            self.p['level']=level
            with self.r.db(self.p) as con:
                con.execute('DELETE FROM local_worldmap_mines WHERE uin=?',(self.p['uin'],))
                for pos in self.res.MINE_POSITIONS:
                    row=self.r.mine(con,self.p,pos)
                    con.execute('UPDATE local_worldmap_mines SET held=1 WHERE uin=? AND area=? AND x=? AND y=?',
                                self.r.mine_key(row))
            for pos in self.res.MINE_POSITIONS[:maximum]:
                message(self.call('Mine_Mining',self.m(1,self.pos(*pos))),'Mine_Mining')
            body=message(self.call('Mine_GetInfo',self.mine_pos),'Mine_GetInfo')
            self.assertEqual(one(body,6),0);self.assertEqual(one(body,7),maximum)
            self.assertFalse(self.r.busy(self.p),'carts never reserve plants')
            if maximum<len(self.res.MINE_POSITIONS):
                self.error(self.call('Mine_Mining',self.m(1,self.pos(*self.res.MINE_POSITIONS[maximum]))))
            start=self.now;self.now+=11;before=self.balance()
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                results=list(pool.map(lambda _:self.call('Mine_CollectAllMine',player=copy.deepcopy(self.p)),range(4)))
            self.assertEqual(self.balance(),before+maximum*110)
            self.assertEqual(sum(one(message(r,'Mine_CollectAllMine'),1) for r in results),maximum*110)
            for pos in self.res.MINE_POSITIONS[:maximum]:
                self.call('Mine_CancelMining',self.m(1,self.pos(*pos)))
            self.assertEqual(one(message(self.call('Mine_GetMiningInfo'),'Mine_GetMiningInfo'),1),0)

    def test_concurrent_cart_admission_respects_limit_and_individual_replays(self):
        self.p['level']=10
        with self.r.db(self.p) as con:
            for pos in self.res.MINE_POSITIONS:
                row=self.r.mine(con,self.p,pos)
                con.execute('UPDATE local_worldmap_mines SET held=1 WHERE uin=? AND area=? AND x=? AND y=?',
                            self.r.mine_key(row))
        requests=[self.m(1,self.pos(*pos)) for pos in self.res.MINE_POSITIONS]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda d:self.call('Mine_Mining',d,copy.deepcopy(self.p)),requests))
        self.assertEqual(sum(any(k.endswith('Mine_Mining_SC') for k,b in r) for r in results),2)
        self.assertEqual(one(message(self.call('Mine_GetMiningInfo'),'Mine_GetMiningInfo'),1),2)
        with self.r.db(self.p) as con:
            active=[(r['x'],r['y']) for r in con.execute('SELECT * FROM local_worldmap_mines WHERE uin=? AND started>0',
                                                        (self.p['uin'],))]
        self.now+=8;before=self.balance()
        claims=[self.m(1,self.pos(*pos)) for pos in active]*4
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda d:self.call('Mine_CollectProduct',d,copy.deepcopy(self.p)),claims))
        self.assertEqual(self.balance(),before+160)
        self.assertEqual(sum(one(message(r,'Mine_CollectProduct'),2) for r in results
                            if any(k.endswith('Mine_CollectProduct_SC') for k,b in r)),160)
        for pos in active:
            self.call('Mine_Mining',self.m(1,self.pos(*pos)))
        self.assertEqual(one(message(self.call('Mine_GetMiningInfo'),'Mine_GetMiningInfo'),1),2)



    def test_idle_collect_rejects_and_redeployment_does_not_spend_attempt(self):
        self.occupy()
        self.assertEqual(one(message(self.call('Mine_CollectProduct',self.mine_pos),'LogicError'),1),4753)
        self.call('Mine_RecallTeam',self.mine_pos)
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET used=? WHERE uin=?',(self.wm.DAILY_LIMIT,self.p['uin']))
        with patch.object(self.a,'make_report_details',side_effect=AssertionError('held mine fought again')):
            self.occupy()
        with self.a.db(self.p) as con:self.assertEqual(self.a.state(con,self.p)['used'],self.wm.DAILY_LIMIT)


    def test_resource_dispatch_auto_repairs_before_real_combat(self):
        self.guild()
        for kind in ('mine','temple'):
            with self.a.db(self.p) as con:
                con.execute('UPDATE local_worldmap_state SET health=? WHERE uin=?',
                            (json.dumps({str(uid):0 for uid in self.starter.values()}),self.p['uin']))
            before=self.balance()
            result=self.occupy() if kind=='mine' else self.capture()
            self.assertEqual(self.balance(),before-len(self.starter)*self.wm.HEAL_COST_PER_CARD)
            self.assertEqual(one(message(result,'SyncCoin'),1),self.balance())
            report=self.latest_mine_report(result) if kind=='mine' else one(message(result,'Battle_QueryReport'),1)
            for card in many(one(report,2),2):
                self.assertEqual(one(card,3),one(card,6),'confirmed dispatch starts combat at full HP')
            if kind=='mine':self.call('Mine_RecallTeam',self.mine_pos)

    def test_insufficient_repair_coins_do_not_spend_or_capture(self):
        self.guild()
        injury={str(uid):0 for uid in self.starter.values()}
        cost=len(injury)*self.wm.HEAL_COST_PER_CARD
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET health=? WHERE uin=?',(json.dumps(injury),self.p['uin']))
            con.execute('UPDATE players SET coin=? WHERE uin=?',(cost-1,self.p['uin']))
        with patch.object(self.a,'make_report_details',side_effect=AssertionError('unaffordable dispatch reached combat')):
            result=self.call('Mine_SetOutTeam',self.mine_pos+self.v(2,self.wm.TEAM_ID))
            self.assertEqual(one(message(result,'LogicError'),1),202)
            result=self.capture()
            self.assertEqual(one(message(result,'LogicError'),1),202)
        self.assertEqual(self.balance(),cost-1)
        with self.a.db(self.p) as con:
            self.assertEqual(self.a.state(con,self.p)['used'],0)
            self.assertEqual(json.loads(self.a.state(con,self.p)['health']),injury)
        self.assertFalse(self.r.busy(self.p))

    def test_held_mine_redeployment_repairs_without_new_battle(self):
        self.occupy();self.call('Mine_RecallTeam',self.mine_pos)
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET health=? WHERE uin=?',
                        (json.dumps({str(uid):5000 for uid in self.starter.values()}),self.p['uin']))
        before=self.balance()
        with patch.object(self.a,'make_report_details',side_effect=AssertionError('held mine fought again')):
            result=self.occupy()
        self.assertEqual(self.balance(),before-len(self.starter)*self.wm.HEAL_COST_PER_CARD)
        self.assertEqual(one(message(result,'SyncCoin'),1),self.balance())
        frozen=self.r.frozen_formation(self.p,self.wm.TEAM_ID)
        self.assertEqual(one(frozen,7),0)
        self.assertTrue(all(one(card,4)==one(card,5) for card in many(frozen,1)))
        with self.a.db(self.p) as con:
            self.assertEqual(self.a.state(con,self.p)['used'],0)
            self.assertEqual(json.loads(self.a.state(con,self.p)['health']),
                             {str(uid):10000 for uid in self.starter.values()})

    def test_parallel_repairs_cannot_overspend_shared_coin_balance(self):
        home,expedition=self.split_teams()
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET health=? WHERE uin=?',
                        (json.dumps({str(uid):0 for uid in self.starter.values()}),self.p['uin']))
            con.execute('UPDATE players SET coin=? WHERE uin=?',
                        (len(home)*self.wm.HEAL_COST_PER_CARD,self.p['uin']))
        barrier=threading.Barrier(2)
        def combat(player,target,placements,**_kwargs):
            barrier.wait(timeout=10)
            return True,self.v(1,0),{}
        requests=[self.mine_pos+self.v(2,self.wm.TEAM_ID),self.m(1,self.pos(26,20))+self.v(2,1)]
        with patch.object(self.a,'make_report_details',side_effect=combat):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda d:self.call('Mine_SetOutTeam',d,copy.deepcopy(self.p)),requests))
        self.assertEqual(sum(any(k.endswith('Mine_SetOutTeam_SC') for k,b in r) for r in results),1)
        self.assertEqual([one(b,1) for r in results for k,b in r if k.endswith('LogicError_SC')],[202])
        self.assertEqual(self.balance(),0)
        with self.a.db(self.p) as con:
            st=self.a.state(con,self.p)
            self.assertEqual(st['used'],0)
            self.assertEqual(sorted(json.loads(st['health']).values()),[0,0,10000,10000])


    def test_town_defender_blocks_only_its_own_resource_dispatch(self):
        self.split_teams();self.guild()
        message(self.call('Battle_SetDefendTeam',self.v(1,self.wm.TEAM_ID)),'Battle_GetFormation')
        rejected=self.call('Mine_SetOutTeam',self.mine_pos+self.v(2,self.wm.TEAM_ID))
        self.assertEqual(one(message(rejected,'LogicError'),1),4721)
        with patch.object(self.a,'make_report_details',return_value=(True,self.v(1,0),{})):
            self.occupy(team=1)
        self.call('Mine_RecallTeam',self.mine_pos)
        self.assertEqual(one(message(self.capture(),'LogicError'),1),4721)
        message(self.call('Battle_CancelDefendTeam',self.v(1,self.wm.TEAM_ID)),'Battle_GetFormation')
        with patch.object(self.a,'make_report_details',return_value=(True,self.v(1,0),{})):
            message(self.capture(),'Temple_SetOut')

    def test_four_cart_capacity_is_shared_across_areas_and_reconnect(self):
        self.p['level']=30
        with self.r.db(self.p) as con:
            for pos in self.res.MINE_POSITIONS:
                row=self.r.mine(con,self.p,pos)
                con.execute('UPDATE local_worldmap_mines SET held=1 WHERE uin=? AND area=? AND x=? AND y=?',
                            self.r.mine_key(row))
        for pos in self.res.MINE_POSITIONS:
            message(self.call('Mine_Mining',self.m(1,self.pos(*pos))),'Mine_Mining')
        message(self.call('City_ChangeArea',self.v(1,2)),'City_ChangeArea')
        with self.r.db(self.p) as con:
            row=self.r.mine(con,self.p,(20,20))
            self.assertEqual(row['area'],2)
            con.execute('UPDATE local_worldmap_mines SET held=1 WHERE uin=? AND area=? AND x=? AND y=?',
                        self.r.mine_key(row))
        self.assertEqual(one(message(self.call('Mine_Mining',self.mine_pos),'LogicError'),1),4755)
        info=message(self.call('Mine_GetInfo',self.mine_pos),'Mine_GetInfo')
        self.assertEqual(one(info,6),0);self.assertEqual(one(info,7),4)
        self.now+=9;before=self.balance()
        reconnect=self.res.ResourceGameplay(self.a)
        body=message(reconnect.collect_all(b'',copy.deepcopy(self.p)),'Mine_CollectAllMine')
        self.assertEqual(one(body,1),360);self.assertEqual(self.balance(),before+360)
        self.assertEqual(one(message(reconnect.mining_info(b'',self.p),'Mine_GetMiningInfo'),1),4)
        self.assertFalse(reconnect.busy(self.p))

if __name__=='__main__': unittest.main()
