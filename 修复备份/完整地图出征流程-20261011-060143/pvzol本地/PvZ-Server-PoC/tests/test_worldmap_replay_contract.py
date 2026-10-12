"""Verify the real GameFlowProxy QueryReport consumer contract, not just combat generation.

Source evidence: reports/client/客户端大地图协议核对.md §7.7 and ModuleBattle/Game_n ABC.
Actual SWF rendering and return-to-map interaction are separately unverified.
"""
import importlib
import sqlite3
from contextlib import closing
import unittest
from unittest.mock import patch
from worldmap_test_support import IsolatedServer, fields, many, message, one


class WorldMapReplayContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa=IsolatedServer();cls.g=cls.qa.game;cls.wm=importlib.import_module('worldmap_local')
        cls.a=cls.g._worldmap_adapter;cls.serial=0
        cls.g.load_battle_cards()
        rows=cls.g._read_clientdb_table('ItemInfo.txt');index=rows[0].index('id')
        cls.item_ids={int(row[index]) for row in rows[1:] if row[index].isdigit()}
    @classmethod
    def tearDownClass(cls):cls.qa.close()
    def setUp(self):
        type(self).serial+=1;self.now=1800000000
        self.p=self.qa.player(99506000+self.serial,role_created=1,level=100,coin=10000)
        self.g.rebuild_td_roster(self.p);self.g.ensure_player_cards(self.p)
        clock=patch.object(self.wm,'_now',side_effect=lambda:self.now);clock.start();self.addCleanup(clock.stop)
        placements=dict(zip(self.g._FORMATION_POS,[row[0] for row in self.qa.store.get_cards(self.p['uin'])]))
        dto=b''.join(self.m(1,self.v(1,pos)+self.g.pb_int64_field(2,uid)) for pos,uid in placements.items())+self.v(5,self.wm.TEAM_ID)
        message(self.call('Battle_SetFormation',self.m(1,dto)),'Battle_GetFormation')
    def v(self,n,x):return self.g.pb_varint_field(n,x)
    def m(self,n,x):return self.g.pb_bytes(n,x)
    def pos(self,p):return self.v(1,p[0])+self.v(2,p[1])
    def call(self,name,data=b''):return self.qa.invoke(name,data,self.p)
    def contract(self,report,kind):
        self.assertEqual(one(report,5),kind)
        self.assertIn((one(report,1),one(report,7)),((0,1),(1,2)),'AS getSelfSide must resolve left')
        left,right=one(report,2),one(report,3)
        self.assertIsInstance(left,bytes);self.assertIsInstance(right,bytes)
        self.assertEqual(one(left,1),8);self.assertEqual(one(right,1),9)
        self.assertIsInstance(one(left,9),bytes,'self cardExp must not be null')
        self.assertIsInstance(one(left,10),bytes,'self roleAttr must not be null')
        self.assertLessEqual(len(many(left,2)),6)
        self.assertTrue(many(report,4),'actual replay rounds are required')
        for side in (left,right):
            self.assertTrue(many(side,2))
            for card in many(side,2)+many(side,4):
                self.assertIn(one(card,2),self.g._BATTLE_CARDS)
                for field in (3,6):self.assertEqual(fields(card)[field][0][0],0,'hp/maxHp must be UINT64 wire0')
                self.assertLessEqual(one(card,3),one(card,6))
            for reward in many(side,3):
                for item in many(reward,4):
                    self.assertIn(one(item,2),self.item_ids);self.assertGreater(one(item,3),0)
        # Map/Mine/Temple preload these existing backgrounds, not fuben chapter assets.
        for name in ('friend_bottom.swf','friend_top.swf'):
            self.assertTrue((self.qa.project/'webroot/swf/battle/battleMap'/name).is_file())
        # note is OPTIONAL: the only map result consumer checks for null explicitly.

    def test_map_win_and_loss_history_query_use_consumer_safe_report(self):
        for index in (0,2):
            with self.subTest(npc=index):
                # Exercise a real victory and a real defeat with the current
                # battle engine; no report or combat function is mocked here.
                self.qa.train_owned_cards(self.p,level=100 if index==0 else 1)
                target=self.wm.NPCS[index]
                declaration=message(self.call('War_DeclareWar',self.v(1,target['role_id'])),'War_DeclareWar')
                self.now=max(self.now,one(declaration,2)+2)
                self.call('War_SetOut',self.v(1,2)+self.m(2,self.pos(target['pos']))+self.v(3,self.wm.TEAM_ID))
                with closing(sqlite3.connect(self.qa.store.DB_PATH)) as db, db:
                    db.row_factory=sqlite3.Row
                    row=dict(db.execute('SELECT * FROM local_worldmap_missions WHERE uin=? ORDER BY id DESC LIMIT 1',(self.p['uin'],)).fetchone())
                self.now=row['home_at']+1
                self.assertEqual(bool(row['won']),index==0)
                history=message(self.call('War_GetHistory'),'War_GetHistory')
                entry=next(x for x in many(history,1) if one(x,5)==row['id'])
                self.assertEqual(one(entry,10),row['id']);self.assertGreater(one(entry,11),0)
                reply=message(self.call('Battle_QueryReport',self.v(1,one(entry,10))),'Battle_QueryReport')
                self.contract(one(reply,1),3)
                if index==0:self.now+=self.wm.TARGET_COOLDOWN

    def test_mine_success_emits_actual_query_report_consumer(self):
        self.qa.train_owned_cards(self.p,level=100)
        replies=self.call('Mine_SetOutTeam',self.m(1,self.pos((20,20)))+self.v(2,self.wm.TEAM_ID))
        self.assertFalse(any(k.endswith('Battle_QueryReport_SC') for k,b in replies),
                         'mine dispatch has no report until arrival')
        with self.a.resources.db(self.p) as con:
            self.now=con.execute('SELECT arrive_at FROM local_worldmap_mine_missions WHERE uin=? AND state=?',
                                 (self.p['uin'],self.wm.MARCHING)).fetchone()[0]
        replies=self.a.advance(self.p)
        self.contract(one(message(replies,'Battle_QueryReport'),1),7)

    def test_temple_success_emits_actual_query_report_consumer(self):
        self.qa.train_owned_cards(self.p,level=100)
        with self.a.resources.db(self.p) as con:
            cur=con.execute('INSERT INTO local_guilds(name,level,created_at) VALUES(?,?,?)',('Replay QA '+str(self.p['uin']),5,self.now))
            con.execute('INSERT INTO local_guild_members(guild_id,uin,role_id,name,joined_at) VALUES(?,?,?,?,?)',
                        (cur.lastrowid,self.p['uin'],self.p['role_id'],self.p['name'],self.now))
        self.call('City_ChangeArea',self.v(1,2))
        temple=self.a.resources.temple(temple_id=6)
        replies=self.call('Temple_SetOut',self.m(1,self.pos(temple['pos']))+self.v(2,self.wm.TEAM_ID)+self.v(3,2))
        self.contract(one(message(replies,'Battle_QueryReport'),1),6)
        self.now+=9
        info=message(self.call('Temple_GetTempleInfo',self.m(1,self.pos(temple['pos']))+self.v(2,2)),'Temple_GetTempleInfo')
        self.assertEqual(one(one(info,4),5),9,'AS captureTime is elapsed seconds, not epoch')


if __name__=='__main__':unittest.main(verbosity=2)
