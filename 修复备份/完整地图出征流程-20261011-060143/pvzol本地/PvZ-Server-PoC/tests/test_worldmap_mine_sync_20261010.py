"""Original mine sites and MapProxy cart/snapshot contracts, synthetic saves only."""
import copy
import sqlite3
from contextlib import closing
import unittest
import test_worldmap_resources as fixtures
from worldmap_test_support import many, message, one


class MineSyncTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ResourceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.ResourceTests.tearDownClass.__func__)
    setUp = fixtures.ResourceTests.setUp
    for _name in ('v','m','pos','call','error','balance','set_team','occupy','mine'):
        locals()[_name] = getattr(fixtures.ResourceTests, _name)

    def snapshot(self, messages):
        return many(message(messages, 'WorldMap_GetInfo'), 1)

    def own_city(self, messages):
        return next(c for c in self.snapshot(messages)
                    if one(c,1) == 1 and one(c,3) == self.p['role_id'])

    def mine_count(self):
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con:
            return con.execute('SELECT COUNT(*) FROM local_worldmap_mines WHERE uin=?',
                               (self.p['uin'],)).fetchone()[0]

    def test_distant_viewport_has_all_authored_mines_without_creating_rows(self):
        request = self.m(1,self.pos(64,64))+self.m(2,self.pos(75,75))+self.v(5,1)
        before = self.mine_count()
        cells = self.snapshot(self.call('WorldMap_GetInfo', request))
        positions = {(one(one(c,2),1),one(one(c,2),2)) for c in cells if one(c,1) == 2}
        self.assertEqual(positions, {(68,68),(68,74),(74,68),(74,74)})
        self.assertEqual(self.mine_count(), before)

    def test_authored_mine_in_distant_chunk_can_dispatch_and_capture(self):
        target = self.m(1,self.pos(68,74))
        message(self.call('War_PreSetOut', self.v(1,2)+self.m(2,self.pos(68,74))), 'War_PreSetOut')
        result = self.call('Mine_SetOutTeam', target+self.v(2,self.wm.TEAM_ID))
        message(result,'Mine_SetOutTeam')
        with self.r.db(self.p) as con:
            self.assertEqual(self.r.mine(con,self.p,(68,74))['held'],0,'dispatch alone cannot capture')
            self.now=con.execute('SELECT arrive_at FROM local_worldmap_mine_missions WHERE uin=? AND state=?',
                                 (self.p['uin'],self.wm.MARCHING)).fetchone()[0]
        message(self.a.advance(self.p),'Battle_QueryReport')
        with self.r.db(self.p) as con:
            self.assertEqual(self.r.mine(con,self.p,(68,74))['held'],1)
        self.assertTrue(self.r.team_busy(self.p,self.wm.TEAM_ID))
        for invalid in ((67,74),(68,75),(self.wm.MAP_SIZES[1]+2,2),(26,26)):
            self.error(self.call('Mine_GetInfo',self.m(1,self.pos(*invalid))))

    def test_mining_sync_and_reload_restore_city_cart_and_real_mine(self):
        captured = self.occupy()
        self.assertEqual(many(self.own_city(captured),10),[],'capture alone does not start a cart')
        started = self.call('Mine_Mining',self.mine_pos)
        state = message(started,'Mine_StateChange')
        self.assertEqual(one(state,1),2)
        self.assertEqual(one(state,4),self.pos(20,20))
        carts = many(self.own_city(started),10)
        self.assertEqual(len(carts),1)
        self.assertEqual(one(carts[0],1),self.pos(20,20))
        self.assertEqual(one(carts[0],3),self.res.MINING_CAP)
        self.now += 9
        reloaded = self.call('WorldMap_GetInfo',self.v(5,1))
        cart = many(self.own_city(reloaded),10)[0]
        self.assertEqual(one(cart,2),90)
        for cell in self.snapshot(reloaded):
            if one(cell,1) != 1:
                self.assertEqual(many(cell,10),[],'mine/temple DTOs must not inherit own-city carts')

    def test_collection_recall_cancel_keep_cart_and_map_consistent(self):
        self.occupy(); self.call('Mine_Mining',self.mine_pos); self.now += 10
        collected = self.call('Mine_CollectProduct',self.mine_pos)
        self.assertEqual(one(message(collected,'Mine_StateChange'),1),4)
        self.assertEqual(one(many(self.own_city(collected),10)[0],2),0)
        recalled = self.call('Mine_RecallTeam',self.mine_pos)
        self.assertEqual(len(many(self.own_city(recalled),10)),1,'recall frees only the guard')
        self.assertFalse(self.r.team_busy(self.p,self.wm.TEAM_ID))
        self.now += 2
        cancelled = self.call('Mine_CancelMining',self.mine_pos)
        self.assertEqual(one(message(cancelled,'Mine_StateChange'),1),3)
        self.assertEqual(many(self.own_city(cancelled),10),[])

    def test_snapshot_refresh_preserves_last_requested_viewport(self):
        self.occupy()
        request = self.m(1,self.pos(10,10))+self.m(2,self.pos(28,28))+self.v(5,1)
        expected = self.snapshot(self.call('WorldMap_GetInfo',request))
        result = self.call('Mine_Mining',self.mine_pos)
        actual = self.snapshot(result)
        keys = lambda cells: {(one(c,1),one(c,3),one(c,2)) for c in cells}
        self.assertEqual(keys(actual),keys(expected),'GetInfo replaces every client cell, so sync must retain snapshot')
        self.assertGreater(len(actual),1)

    def test_two_sessions_keep_their_own_map_viewports(self):
        self.occupy()
        first, second = copy.deepcopy(self.p), copy.deepcopy(self.p)
        near = self.m(1,self.pos(10,10))+self.m(2,self.pos(28,28))+self.v(5,1)
        far = self.m(1,self.pos(64,64))+self.m(2,self.pos(75,75))+self.v(5,1)
        first_cells = self.snapshot(self.call('WorldMap_GetInfo',near,first))
        second_cells = self.snapshot(self.call('WorldMap_GetInfo',far,second))
        keys = lambda cells: {(one(c,1),one(c,3),one(c,2)) for c in cells}
        self.assertEqual(keys(self.snapshot(self.call('Mine_Mining',self.mine_pos,first))),keys(first_cells))
        self.assertEqual(keys(self.snapshot(self.call('Mine_Mining',self.mine_pos,second))),keys(second_cells))

    def test_remote_city_cart_query_never_initializes_remote_gameplay_state(self):
        other = self.qa.player(self.p['uin']+10000,name='Readonly town',level=100)
        with self.a.db(self.p) as con:
            before = con.execute('SELECT COUNT(*) FROM local_worldmap_state WHERE uin=?',(other['uin'],)).fetchone()[0]
            self.assertEqual(self.r.mine_cars(other,{'area':1}),b'')
            after = con.execute('SELECT COUNT(*) FROM local_worldmap_state WHERE uin=?',(other['uin'],)).fetchone()[0]
        self.assertEqual(before,0)
        self.assertEqual(after,0)


if __name__ == '__main__':
    unittest.main()
