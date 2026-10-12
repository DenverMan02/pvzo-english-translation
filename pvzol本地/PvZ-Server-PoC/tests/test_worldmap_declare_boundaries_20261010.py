"""Original declaration/countdown DTO boundaries and independent mine flow."""
import copy
import unittest
import test_worldmap_resources as fixtures
from worldmap_test_support import many, message, one


class DeclareBoundaryTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ResourceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.ResourceTests.tearDownClass.__func__)
    for _name in ('v','m','pos','call','error','set_team','occupy','mine','balance'):
        locals()[_name] = getattr(fixtures.ResourceTests, _name)

    def setUp(self):
        fixtures.ResourceTests.setUp(self)
        self.call('City_Init')
        self.other = self.qa.player(self.p['uin']+10000,name='Declaration target',level=100,role_created=1)
        self.third = self.qa.player(self.p['uin']+20000,name='Neutral neighbour',level=100,role_created=1)
        self.call('City_Init',player=self.other)
        self.call('City_Init',player=self.third)
        with self.a.db(self.other) as con:
            self.other_pos = (self.a.state(con,self.other)['x'],self.a.state(con,self.other)['y'])

    def cell(self, target, viewer=None):
        return one(message(self.call('WorldMap_GetCellInfo',self.v(1,target['role_id']),viewer),'WorldMap_GetCellInfo'),1)

    def enemies(self, viewer=None):
        return many(message(self.call('WorldMap_GetEnemyList',player=viewer),'WorldMap_GetEnemyList'),1)

    def declaration(self):
        result = self.call('War_DeclareWar',self.v(1,self.other['role_id']))
        return one(message(result,'War_DeclareWar'),2)

    def remaining(self):
        return one(message(self.call('War_GetDeclareWarStat'),'War_GetDeclareWarStat'),2)

    def preflight(self):
        return self.call('War_PreSetOut',self.v(1,2)+self.m(2,self.pos(*self.other_pos)))

    def dispatch(self):
        return self.call('War_SetOut',self.v(1,2)+self.m(2,self.pos(*self.other_pos))+self.v(3,self.wm.TEAM_ID))

    def test_neutral_towns_are_not_all_marked_at_war_or_listed_as_enemies(self):
        for target in (self.other,self.third):
            self.assertEqual(one(self.cell(target),5),0)
            self.assertEqual(one(self.cell(target),8),1)
        roles = {one(c,3) for c in self.enemies()}
        self.assertNotIn(self.other['role_id'],roles)
        self.assertNotIn(self.third['role_id'],roles)
        listed = many(message(self.call('Setout_GetCityList',self.v(1,1)),'Setout_GetCityList'),1)
        self.assertNotIn(self.other['role_id'],{one(c,1) for c in listed})

    def test_declaration_is_target_specific_and_exposes_incoming_relation(self):
        end = self.declaration()
        self.assertGreater(end,self.now)
        target = self.cell(self.other)
        self.assertEqual((one(target,5),one(target,6)),(1,end))
        self.assertEqual(one(self.cell(self.p,self.other),5),2)
        self.assertEqual(one(self.cell(self.third),5),0)
        self.assertIn(self.other['role_id'],{one(c,3) for c in self.enemies()})
        self.assertNotIn(self.third['role_id'],{one(c,3) for c in self.enemies()})
        listed = many(message(self.call('Setout_GetCityList',self.v(1,1)),'Setout_GetCityList'),1)
        self.assertEqual(one(next(c for c in listed if one(c,1)==self.other['role_id']),6),3)

    def test_declaring_and_ready_match_original_end_time_plus_one_boundary(self):
        end = self.declaration()
        self.now = end+1
        self.assertEqual(one(self.cell(self.other),5),1)
        self.error(self.preflight())
        self.error(self.dispatch())
        self.now += 1
        self.assertEqual(one(self.cell(self.other),5),3)
        message(self.preflight(),'War_PreSetOut')
        message(self.dispatch(),'War_SetOut')

    def test_repeated_declaration_and_reconnect_preserve_countdown_and_count(self):
        before = self.remaining()
        end = self.declaration()
        self.assertEqual(self.remaining(),before-1)
        self.now += 1
        self.assertEqual(self.declaration(),end)
        self.assertEqual(self.remaining(),before-1)
        reconnected = copy.deepcopy(self.p)
        self.assertEqual(one(self.cell(self.other,reconnected),6),end)
        self.assertEqual(one(self.cell(self.other,reconnected),5),1)

    def test_undeclared_town_cannot_bypass_flow_with_direct_dispatch(self):
        before = self.balance()
        self.error(self.preflight())
        self.error(self.dispatch())
        with self.a.db(self.p) as con:
            self.assertEqual(self.a.state(con,self.p)['used'],0)
            self.assertIsNone(self.a.mission(con,self.p))
        self.assertEqual(self.balance(),before)

    def test_daily_count_resets_without_closing_an_unfinished_relationship(self):
        before = self.remaining()
        self.declaration()
        self.assertEqual(self.remaining(),before-1)
        self.now += 86400
        self.assertEqual(self.remaining(),before)
        self.assertEqual(one(self.cell(self.other),5),3)
        self.assertIn(self.other['role_id'],{one(c,3) for c in self.enemies()})
        result = self.call('War_DeclareWar',self.v(1,self.third['role_id']))
        self.assertGreater(one(message(result,'War_DeclareWar'),2),self.now)
        self.assertEqual(self.remaining(),before-1)

    def test_original_town_level_13_gate_applies_to_both_declaration_participants(self):
        self.qa.store.save_player(self.other['uin'],level=12)
        self.other['level'] = 12
        self.error(self.call('War_DeclareWar',self.v(1,self.other['role_id'])))
        self.qa.store.save_player(self.other['uin'],level=13)
        self.other['level'] = 13
        self.qa.store.save_player(self.p['uin'],level=12)
        self.p['level'] = 12
        self.error(self.call('War_DeclareWar',self.v(1,self.other['role_id'])))
        self.qa.store.save_player(self.p['uin'],level=13)
        self.p['level'] = 13
        message(self.call('War_DeclareWar',self.v(1,self.other['role_id'])),'War_DeclareWar')

    def test_mine_preflight_and_capture_do_not_require_town_declaration(self):
        before = self.remaining()
        message(self.call('War_PreSetOut',self.v(1,2)+self.m(2,self.pos(20,20))),'War_PreSetOut')
        self.occupy()
        self.assertEqual(self.mine()['held'],1)
        self.assertEqual(self.remaining(),before)
        self.assertEqual(one(self.cell(self.other),5),0)

    def test_battle_cooldown_marks_only_that_target_immune(self):
        end = self.declaration()
        self.now = end+2
        message(self.dispatch(),'War_SetOut')
        with self.a.db(self.p) as con:
            mission = self.a.mission(con,self.p)
        self.now = mission['arrive_at']
        self.a.advance(self.p)
        target = self.cell(self.other)
        self.assertEqual(one(target,8),2)
        self.assertGreater(one(target,9),self.now)
        self.assertEqual(one(self.cell(self.third),8),1)
        self.assertEqual(one(self.cell(self.third),5),0)
        self.error(self.preflight())


if __name__ == '__main__':
    unittest.main()
