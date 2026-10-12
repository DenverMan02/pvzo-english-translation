"""Atomic mine arrival, recall races and offline recovery on synthetic saves."""
import concurrent.futures
import copy
import json
import threading
import unittest
from unittest.mock import patch

import test_worldmap_mine_march_hooks_20261010 as fixtures
from worldmap_test_support import many, message, one


class MineMarchBoundaries(unittest.TestCase):
    setUpClass = classmethod(fixtures.MineMarchHookTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.MineMarchHookTests.tearDownClass.__func__)
    setUp = fixtures.MineMarchHookTests.setUp
    for _name in ('v','m','position','call','dispatch','job','mine','history','poll','dto'):
        locals()[_name] = getattr(fixtures.MineMarchHookTests,_name)

    def test_same_action_follows_march_guard_recall_home_without_duplicate_guard_history(self):
        h=one(message(self.dispatch(),'War_SyncHistory'),1)
        action=one(h,5)
        self.now=self.job()['arrive_at']
        guard=one(message(self.poll(),'War_SyncHistory'),1)
        self.assertEqual((one(guard,5),one(guard,8)),(action,2))
        self.assertEqual(len(self.history()),1,'legacy stationary DTO must not duplicate new guard')
        recalled=one(message(self.call('Mine_RecallTeam',self.mine_data),'War_SyncHistory'),1)
        self.assertEqual((one(recalled,5),one(recalled,8)),(action,self.wm.RECALLING))
        self.assertEqual(one(recalled,10),one(guard,10))
        self.now=self.job()['home_at']
        home=one(message(self.poll(),'War_SyncHistory'),1)
        self.assertEqual((one(home,5),one(home,8)),(action,self.wm.HOME))
        self.assertEqual(self.history(),[])
        self.assertEqual([one(dto,5) for dto in self.history('War_GetHistory')],[action])

    def test_duplicate_dispatch_never_spends_attempt_twice_or_changes_arrival(self):
        first=self.dispatch();job=self.job()
        with self.a.db(self.p) as con:
            used=self.a.state(con,self.p)['used']
        for _ in range(3):
            retry=self.dispatch()
            self.assertEqual(one(one(message(retry,'War_SyncHistory'),1),5),
                             one(one(message(first,'War_SyncHistory'),1),5))
        self.assertEqual(self.job(),job)
        with self.a.db(self.p) as con:
            self.assertEqual(self.a.state(con,self.p)['used'],used)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_mine_missions WHERE uin=?',
                                         (self.p['uin'],)).fetchone()[0],1)
        invalid=self.m(1,self.position((21,20)))+self.v(2,1002)
        message(self.call('Mine_SetOutTeam',invalid),'LogicError')
        with self.r.db(self.p) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_mines WHERE uin=? AND x=21 AND y=20',
                                         (self.p['uin'],)).fetchone()[0],0)

    def test_dispatch_pays_injury_repair_once_and_arrival_alone_commits_battle_injury(self):
        injured={str(uid):5000 for uid in self.placements.values()}
        after={str(uid):2500 for uid in self.placements.values()}
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET health=? WHERE uin=?',(json.dumps(injured),self.p['uin']))
        before=self.qa.store.get_or_create_player(self.p['uin'])[0]['coin']
        self.dispatch();self.dispatch()
        expected=before-len(injured)*self.wm.HEAL_COST_PER_CARD
        self.assertEqual(self.qa.store.get_or_create_player(self.p['uin'])[0]['coin'],expected)
        with self.a.db(self.p) as con:
            self.assertEqual(json.loads(self.a.state(con,self.p)['health']),dict.fromkeys(injured,10000))
        self.now=self.job()['arrive_at']
        with patch.object(self.a,'make_report_details',return_value=(True,self.v(1,0),after)):
            self.poll();self.poll()
        self.assertEqual(self.qa.store.get_or_create_player(self.p['uin'])[0]['coin'],expected)
        with self.a.db(self.p) as con:
            self.assertEqual(json.loads(self.a.state(con,self.p)['health']),after)

    def test_concurrent_arrivals_simulate_and_commit_one_battle_report(self):
        self.dispatch();self.now=self.job()['arrive_at']
        with patch.object(self.g,'run_battle',wraps=self.g.run_battle) as combat:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                replies=list(pool.map(lambda _:self.a.advance(copy.deepcopy(self.p)),range(4)))
            self.assertEqual(combat.call_count,1)
        self.assertEqual(sum(k.endswith('Battle_QueryReport_SC') for r in replies for k,b in r),1)
        with self.r.db(self.p) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_resource_reports WHERE uin=?',
                                         (self.p['uin'],)).fetchone()[0],1)
        self.assertEqual((self.job()['settled'],self.mine()['held']),(1,1))

    def test_recall_during_arrival_calculation_discards_stale_battle_without_capture_or_report(self):
        self.dispatch();self.now=self.job()['arrive_at']
        entered,release=threading.Event(),threading.Event()
        def slow(*_args,**_options):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError('test recall never released battle')
            return True,self.v(1,0),{str(uid):0 for uid in self.placements.values()}
        with patch.object(self.a,'make_report_details',side_effect=slow):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(self.a.advance,copy.deepcopy(self.p))
                self.assertTrue(entered.wait(timeout=5))
                # Direct resource entry models an already admitted callback
                # racing the costly calculation; public routes advance first.
                result=self.r.recall_mission(self.v(1,1002)+self.v(2,1),self.p)
                message(result,'War_RecallTeam')
                release.set()
                self.assertFalse(any(k.endswith('Battle_QueryReport_SC') for k,b in pending.result(timeout=5)))
        self.assertEqual((self.job()['state'],self.job()['report_id'],self.mine()['held']),
                         (self.wm.RECALLING,0,0))
        with self.r.db(self.p) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_resource_reports WHERE uin=?',
                                         (self.p['uin'],)).fetchone()[0],0)
        self.now=self.job()['home_at'];self.poll()
        self.assertEqual(one(self.dto(),6),0)

    def test_offline_loss_settles_and_returns_home_in_one_reconnect_poll(self):
        self.dispatch();job=self.job()
        self.a.active_users.discard(self.p['uin']);self.a.seen_users.discard(self.p['uin'])
        self.now=job['home_at']+100
        after={str(uid):0 for uid in self.placements.values()}
        with patch.object(self.a,'make_report_details',return_value=(False,self.v(1,1),after)) as battle:
            message(self.poll(),'Battle_QueryReport')
            self.assertEqual(battle.call_count,1)
            self.assertEqual(self.poll(),[])
        self.assertEqual((self.job()['state'],self.job()['settled'],self.job()['won']),
                         (self.wm.HOME,1,0))
        self.assertEqual((self.mine()['held'],one(self.dto(),6)),(0,0))
        self.assertGreater(self.job()['report_id'],0)
        with self.a.db(self.p) as con:
            self.assertEqual(json.loads(self.a.state(con,self.p)['health']),after)

    def test_offline_win_remains_guarding_until_explicit_recall(self):
        self.dispatch();job=self.job()
        self.a.active_users.discard(self.p['uin']);self.a.seen_users.discard(self.p['uin'])
        self.now=job['home_at']+86400
        self.poll()
        self.assertEqual((self.job()['state'],self.mine()['held']),(2,1))
        self.assertFalse(self.r.has_active_missions(self.p),'stationary guard does not require repeated polling')
        self.assertEqual(one(self.dto(),6),1)

    def test_imported_old_guard_is_preserved_and_distinct_from_new_guard_history(self):
        self.dispatch();self.now=self.job()['arrive_at'];self.poll()
        with self.r.db(self.p) as con:
            old=self.r.mine(con,self.p,(26,20))
            con.execute('UPDATE local_worldmap_mines SET held=1,team_id=1,formation=? '
                        'WHERE uin=? AND area=? AND x=? AND y=?',
                        (self.v(5,1),*self.r.mine_key(old)))
        histories=self.r.mine_histories(self.p)
        self.assertEqual(len(histories),2)
        actions={one(h,5) for h in histories}
        self.assertEqual(len(actions),2)
        self.assertTrue(any(action<4000000000 for action in actions))
        self.assertTrue(any(action>=4000000000 for action in actions))
        self.assertTrue(all(one(h,8)==2 for h in histories))


if __name__=='__main__':
    unittest.main()
