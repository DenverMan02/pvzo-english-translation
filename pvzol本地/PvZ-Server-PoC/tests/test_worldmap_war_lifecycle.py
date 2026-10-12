"""Saved declarations, timer pushes, transaction boundaries and old missions."""
import copy
import json
import unittest
from unittest.mock import patch
import test_worldmap_declare_boundaries_20261010 as fixtures
from worldmap_test_support import many, message, one


class WarLifecycleTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.DeclareBoundaryTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.DeclareBoundaryTests.tearDownClass.__func__)
    setUp = fixtures.DeclareBoundaryTests.setUp
    for _name in ('v','m','pos','call','error','set_team','occupy','mine','balance',
                  'cell','enemies','declaration','remaining','preflight','dispatch'):
        locals()[_name] = getattr(fixtures.DeclareBoundaryTests,_name)

    def current_mission(self):
        with self.a.db(self.p) as con:
            return dict(self.a.mission(con,self.p))

    def start(self):
        self.now = self.declaration()+2
        message(self.dispatch(),'War_SetOut')
        return self.current_mission()

    def test_daily_declaration_quota_is_independent_and_duplicate_ack_resyncs_count(self):
        old = self.g._cfg_value
        with patch.object(self.g,'_cfg_value',side_effect=lambda key,*args: '2' if key=='iDailyDeclareWarTimes' else old(key,*args)):
            self.declaration()
            self.call('War_DeclareWar',self.v(1,self.third['role_id']))
            self.assertEqual(self.remaining(),0)
            duplicate = self.call('War_DeclareWar',self.v(1,self.other['role_id']))
            self.assertEqual(one(message(duplicate,'War_GetDeclareWarStat'),2),0)
            self.error(self.call('War_DeclareWar',self.v(1,self.wm.NPCS[0]['role_id'])))
            with self.a.db(self.p) as con:
                state = self.a.state(con,self.p)
                self.assertEqual((state['declared_used'],state['used']),(2,0))

    def test_declaration_quota_and_relationship_roll_back_together(self):
        with self.a.db(self.p) as con:
            con.execute("CREATE TRIGGER abort_declare BEFORE UPDATE OF declared_used ON local_worldmap_state "
                        "WHEN NEW.declared_used>OLD.declared_used BEGIN SELECT RAISE(ABORT,'declaration quota failure'); END")
        try:
            self.error(self.call('War_DeclareWar',self.v(1,self.other['role_id'])))
            self.assertEqual(one(self.cell(self.other),5),0)
            with self.a.db(self.p) as con:
                self.assertEqual(self.a.state(con,self.p)['declared_used'],0)
                self.assertIsNone(self.a.war_record(con,self.p,self.other['role_id'],1))
        finally:
            with self.a.db(self.p) as con:
                con.execute('DROP TRIGGER abort_declare')

    def test_new_adapter_recovers_waiting_and_ready_relationships_from_save(self):
        deadline = self.declaration()
        fresh = self.wm.WorldMap(self.g)
        with fresh.db(self.p) as con:
            self.assertEqual(fresh.war_view(con,self.p,self.other['role_id']), (1,deadline,1,0))
        self.now = deadline+2
        fresh.advance(self.p)
        with fresh.db(self.p) as con:
            self.assertEqual(fresh.war_view(con,self.p,self.other['role_id'])[0],3)
        self.assertEqual(self.remaining(),19)

    def test_timer_pushes_incoming_wait_and_ready_to_opponent_session(self):
        end = self.declaration()
        first = self.wm.poll(self.g,self.other)
        cells = many(message(first,'WorldMap_GetInfo'),1)
        self.assertEqual(one(next(c for c in cells if one(c,3)==self.p['role_id']),5),2)
        self.now=end+2
        self.wm.poll(self.g,self.p)
        incoming = self.wm.poll(self.g,self.other)
        cells = many(message(incoming,'WorldMap_GetInfo'),1)
        self.assertEqual(one(next(c for c in cells if one(c,3)==self.p['role_id']),5),3)
        self.assertEqual(one(self.cell(self.third),5),0)

    def test_timer_push_preserves_each_socket_viewport(self):
        local = self.m(1,self.pos(0,0))+self.m(4,self.pos(35,35))+self.v(5,1)
        distant = self.m(1,self.pos(72,72))+self.m(4,self.pos(107,107))+self.v(5,1)
        self.call('WorldMap_GetInfo',local)
        other_socket=copy.deepcopy(self.p)
        self.call('WorldMap_GetInfo',distant,player=other_socket)
        deadline=self.declaration()
        self.now=deadline+2
        self.wm.poll(self.g,self.p)
        updates=self.wm.poll(self.g,other_socket)
        cells=many(message(updates,'WorldMap_GetInfo'),1)
        self.assertTrue(cells)
        self.assertTrue(all(72<=self.wm._pos(c,2)[0]<=107 and 72<=self.wm._pos(c,2)[1]<=107 for c in cells))
        self.assertEqual(other_socket['_worldmap_viewport'],distant)
        self.assertEqual(self.p['_worldmap_viewport'],local)

    def test_timer_push_preserves_another_viewed_continent(self):
        self.call('City_ChangeArea',self.v(1,2),self.third)
        deadline=self.declaration()
        viewed=self.m(1,self.pos(0,0))+self.m(4,self.pos(35,35))+self.v(5,2)
        self.call('WorldMap_GetInfo',viewed)
        self.now=deadline+2
        cells=many(message(self.wm.poll(self.g,self.p),'WorldMap_GetInfo'),1)
        self.assertTrue(cells)
        self.assertTrue(all(one(c,15)==2 for c in cells))
        self.assertEqual(self.p['_worldmap_viewport'],viewed)

    def test_completed_battle_closes_relation_and_cooldown_expires_to_neutral(self):
        mission=self.start()
        self.now=mission['arrive_at']
        self.wm.poll(self.g,self.p)
        self.assertEqual((one(self.cell(self.other),5),one(self.cell(self.other),8)),(0,2))
        self.assertEqual((one(self.cell(self.p,self.other),5),one(self.cell(self.p,self.other),8)),(0,2))
        self.now=mission['arrive_at']+self.wm.TARGET_COOLDOWN
        self.wm.poll(self.g,self.p)
        self.assertEqual((one(self.cell(self.other),5),one(self.cell(self.other),8)),(0,1))
        self.assertNotIn(self.other['role_id'],{one(c,3) for c in self.enemies()})
        self.error(self.preflight())
        new=self.declaration()
        self.assertGreater(new,self.now)

    def test_recall_closes_relationship_when_team_reaches_home_without_reward(self):
        mission=self.start()
        self.now=mission['begin_at']+5
        message(self.call('War_RecallTeam',self.v(1,self.wm.TEAM_ID)+self.v(2,1)),'War_RecallTeam')
        recalled=self.current_mission()
        self.assertEqual(one(self.cell(self.other),5),3)
        message(self.preflight(),'War_PreSetOut')
        self.now=recalled['home_at']
        self.wm.poll(self.g,self.p)
        self.assertEqual((one(self.cell(self.other),5),one(self.cell(self.other),8)),(0,1))
        self.assertNotIn(self.other['role_id'],{one(c,3) for c in self.enemies()})

    def test_mission_created_before_declaration_migration_still_settles_and_replays(self):
        mission=self.start()
        snapshot=json.loads(mission['target_snapshot'])
        snapshot.pop('_war_key')
        with self.a.db(self.p) as con:
            con.execute('DELETE FROM local_worldmap_wars WHERE uin=?',(self.p['uin'],))
            con.execute('UPDATE local_worldmap_missions SET target_snapshot=? WHERE id=?',
                        (json.dumps(snapshot),mission['id']))
        self.assertEqual(one(self.cell(self.other),5),3)
        self.now=mission['home_at']+self.wm.TARGET_COOLDOWN+1
        self.wm.poll(self.g,self.p)
        report=one(message(self.call('Battle_QueryReport',self.v(1,mission['id'])),'Battle_QueryReport'),1)
        self.assertEqual(report,mission['report'])
        self.assertEqual(one(self.cell(self.other),5),0)

    def two_city_teams(self):
        home=dict(list(self.starter.items())[:2])
        self.set_team(1,home)
        first=self.start()
        message(self.call('War_SetOut',self.v(1,2)+self.m(2,self.pos(*self.other_pos))+self.v(3,1)),'War_SetOut')
        with self.a.db(self.p) as con:
            second=dict(self.a.mission(con,self.p,1))
        return first,second

    def test_ready_relationship_permits_disjoint_second_team_and_opponent_counterattack(self):
        first,second=self.two_city_teams()
        self.assertNotEqual(first['id'],second['id'])
        message(self.call('War_PreSetOut',self.v(1,2)+self.m(2,self.pos(first['origin_x'],first['origin_y'])),self.other),'War_PreSetOut')
        self.g.rebuild_td_roster(self.other);self.g.ensure_player_cards(self.other)
        self.qa.train_owned_cards(self.other,level=100)
        message(self.call('War_SetOut',self.v(1,2)+self.m(2,self.pos(first['origin_x'],first['origin_y']))+self.v(3,1),self.other),'War_SetOut')
        self.assertEqual(one(self.cell(self.other),5),3)

    def test_old_delayed_battle_cannot_close_or_cool_a_new_declaration(self):
        first,second=self.two_city_teams()
        delayed_arrival=first['arrive_at']+300
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_missions SET arrive_at=?,home_at=? WHERE id=?',
                        (delayed_arrival,delayed_arrival+second['period'],second['id']))
        self.now=first['arrive_at']
        self.a.advance(self.p)
        self.now=first['arrive_at']+self.wm.TARGET_COOLDOWN+1
        new_deadline=self.declaration()
        self.now=delayed_arrival
        self.a.advance(self.p)
        self.assertGreaterEqual(self.now,new_deadline+2)
        self.assertEqual((one(self.cell(self.other),5),one(self.cell(self.other),8)),(3,1))
        with self.a.db(self.p) as con:
            relation=self.a.war_record(con,self.p,self.other['role_id'],1)
            self.assertEqual(relation['ready_at'],new_deadline+2)
            self.assertEqual(relation['phase'],3)

    def test_old_delayed_recall_cannot_close_a_new_declaration(self):
        first,second=self.two_city_teams()
        self.now=second['begin_at']+5
        message(self.call('War_RecallTeam',self.v(1,1)+self.v(2,1)),'War_RecallTeam')
        delayed_home=first['arrive_at']+300
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_missions SET home_at=? WHERE id=?',(delayed_home,second['id']))
        self.now=first['arrive_at']
        self.a.advance(self.p)
        self.now=first['arrive_at']+self.wm.TARGET_COOLDOWN+1
        new_deadline=self.declaration()
        self.now=delayed_home
        self.a.advance(self.p)
        self.assertEqual((one(self.cell(self.other),5),one(self.cell(self.other),8)),(3,1))
        with self.a.db(self.p) as con:
            self.assertEqual(self.a.war_record(con,self.p,self.other['role_id'],1)['ready_at'],new_deadline+2)


if __name__=='__main__':
    unittest.main()
