"""Stationary mine guards use original history DTOs without invented ETAs."""
import copy
import sqlite3
from contextlib import closing
import unittest
import test_worldmap_resources as fixtures
from worldmap_test_support import many, message, one


class MineHistoryTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ResourceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.ResourceTests.tearDownClass.__func__)
    setUp = fixtures.ResourceTests.setUp
    for _name in ('v','m','pos','call','error','set_team','occupy','mine'):
        locals()[_name] = getattr(fixtures.ResourceTests, _name)

    def histories(self, command='War_GetHistory', player=None):
        request = self.v(1,1) if command == 'War_GetHistory' else b''
        return many(message(self.call(command,request,player),command),1)

    def guard(self, messages):
        return one(message(messages,'War_SyncHistory'),1)

    def test_capture_synchronizes_precise_stationary_guard_and_actual_team(self):
        dto = self.guard(self.occupy())
        self.assertEqual(one(dto,1),2)  # E_WarType_Attack
        self.assertEqual(one(dto,4),self.wm.TEAM_ID)
        self.assertEqual(one(dto,8),2)  # E_TeamState_Guarding
        self.assertEqual(one(one(dto,3),1),3)  # E_WarTargetType_Mine
        self.assertEqual(one(one(dto,3),3),self.pos(20,20))
        self.assertEqual(one(one(one(dto,2),4),3),self.p['role_id'])
        self.assertEqual(one(dto,6),self.now)
        self.assertEqual((one(dto,7),one(dto,10),one(dto,11)),(0,0,0))
        self.assertGreaterEqual(one(dto,5),self.res.MINE_ACTION_BASE)
        self.error(self.call('Battle_QueryReport',self.v(1,one(dto,5))),)

    def test_every_personal_history_query_contains_the_busy_guard_on_reconnect(self):
        expected = self.guard(self.occupy())
        reconnect = copy.deepcopy(self.p)
        for command in ('War_GetHistory','War_GetMapHistory','War_GetSimpleHistory'):
            self.assertEqual(self.histories(command,reconnect),[expected],command)
        formation = one(message(self.call('Battle_GetFormation',self.v(1,self.wm.TEAM_ID)),
                                'Battle_GetFormation'),1)
        self.assertEqual(one(formation,6),1)
        for kind in (2,3):
            self.assertEqual(many(message(self.call('War_GetHistory',self.v(1,kind)),
                                         'War_GetHistory'),1),[])

    def test_duplicate_dispatch_preserves_existing_guard_timestamp_and_identity(self):
        first = self.guard(self.occupy())
        self.now += 20
        self.occupy()
        self.assertEqual(self.histories(),[first])

    def test_recall_synchronizes_home_and_removes_army_task_without_canceling_cart(self):
        first = self.guard(self.occupy())
        self.call('Mine_Mining',self.mine_pos)
        self.now += 5
        home = self.guard(self.call('Mine_RecallTeam',self.mine_pos))
        self.assertEqual(one(home,5),one(first,5))
        self.assertEqual(one(home,4),one(first,4))
        self.assertEqual(one(home,8),4)
        for command in ('War_GetHistory','War_GetMapHistory','War_GetSimpleHistory'):
            self.assertEqual(self.histories(command),[])
        self.assertGreater(self.mine()['started'],0)
        self.assertFalse(self.r.team_busy(self.p,self.wm.TEAM_ID))
        self.call('Mine_CancelMining',self.mine_pos)
        self.assertEqual(self.histories(),[])

    def test_cart_operations_preserve_guard_until_explicit_army_recall(self):
        first = self.guard(self.occupy())
        self.call('Mine_Mining',self.mine_pos)
        self.now += 10
        self.call('Mine_CollectProduct',self.mine_pos)
        self.call('Mine_CancelMining',self.mine_pos)
        self.assertEqual(self.histories(),[first])

    def test_distant_mine_has_stable_distinct_action_and_exact_coordinate(self):
        first = self.guard(self.occupy())
        self.call('Mine_RecallTeam',self.mine_pos)
        self.now += 1
        distant = self.guard(self.call('Mine_SetOutTeam',self.m(1,self.pos(68,74))
                                      +self.v(2,self.wm.TEAM_ID)))
        self.assertEqual(one(one(distant,3),3),self.pos(68,74))
        self.assertNotEqual(one(first,5),one(distant,5))
        self.assertEqual(self.histories('War_GetMapHistory'),[distant])

    def test_history_generation_is_read_only_and_does_not_create_remote_rows(self):
        self.occupy()
        other = self.qa.player(self.p['uin']+10000,role_created=1,level=100)
        def counts():
            with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con:
                return tuple(con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                             for table in ('local_worldmap_mines','local_worldmap_state',
                                           'local_worldmap_resource_reports'))
        before = counts()
        self.assertEqual(self.r.mine_histories(other),[])
        self.assertEqual(len(self.r.mine_histories(self.p)),1)
        self.assertEqual(counts(),before)


if __name__ == '__main__':
    unittest.main()
