"""Guard robbery uses the same real puppet combat and replay as other card modes."""
import copy
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
import guard_local as guard
import test_card_battle_puppets_actions_20261009 as fixtures


def unit(pos, hp=1000000):
    return dict(pos=pos, uid=0, card_id=101 if pos%2 else 111, hp=hp,
                atk=1, def_=0, ability=0, skill1=0, skill2=0,
                is_leader=False, level=1, power=100, speed=0)


class GuardPuppetRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CardBattlePuppetTests(methodName='test_shared_player_battle_and_report_include_real_puppet')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.player = self.fixture.player
        self.uin = self.player['uin']
        self.addCleanup(lambda: guard._ROB_TARGETS.pop(self.uin, None))
        # Use the real route, settlement and battle simulation with a controlled
        # weak plant team, so a deployed puppet actually changes the result.
        with patch.object(g, 'build_player_units', side_effect=lambda *a,**kw: [unit(3), unit(7)]):
            self.handlers = guard.register(g)

    def fight(self):
        member = {'name': 'Controlled opponent', 'level': 1, 'face_id': 0,
                  'cards': [{'unit': unit(4, 10000)}]}
        guard._ROB_TARGETS[self.uin] = {'members': [member], 'deadline': int(time.time()) + 200}
        self.player['light'] = 1
        g.store.save_player(self.uin, light=1)
        messages = self.handlers['PVZ.Cmd.Cmd_Guard_FightRobTarget_CS'](g.pb_varint_field(1, 0), self.player)
        body = next(b for name,b in messages if name.endswith('FightRobTarget_SC'))
        report = g.parse_repeated_submessages(body, 1)[0]
        return body,report,member

    def test_deployed_puppet_changes_guard_result_and_has_real_actor_and_damage(self):
        g.save_puppet_lineup(self.player, [])
        without_body,without,_ = self.fight()
        self.assertEqual(g.parse_varint_fields(without)[1], 1)
        self.assertEqual(g.parse_varint_fields(without_body)[2], 0)
        g.save_puppet_lineup(self.player, [1000001])
        body,report,member = self.fight()
        self.assertEqual(g.parse_varint_fields(report)[1], 0)
        self.assertEqual(g.parse_varint_fields(body)[2], 1)
        self.assertTrue(member['is_beat'])
        self.fixture.assert_puppet_report(report)
        self.assertEqual(g.parse_varint_fields(report)[5], 18)
        actions = [a for r in g.parse_repeated_submessages(report,4)
                   for a in g.parse_repeated_submessages(r,2)
                   if g.parse_repeated_submessages(a,6)]
        self.assertTrue(actions)
        self.assertTrue(any(g.parse_varint_fields(d)[1] == 4 and g.parse_varint_fields(d).get(7) == 1
                            for a in actions for d in g.parse_repeated_submessages(a,2)))
        self.assertEqual(self.player['light'], 0)
        with g.store._db() as con:
            rows = con.execute('SELECT rob_win FROM local_guard_rob_history WHERE uin=? ORDER BY id', (self.uin,)).fetchall()
        self.assertEqual([row[0] for row in rows], [0, 1])

    def test_unknown_puppet_plan_does_not_change_result_or_emit_phantom_actor(self):
        with patch.object(g, '_BATTLECARD_IDS', {'101','111'}):
            _,report,_ = self.fight()
        self.assertEqual(g.parse_varint_fields(report)[1], 1)
        left = g.parse_repeated_submessages(report,2)[0]
        self.assertEqual(g.parse_repeated_submessages(left,16), [])
        self.assertFalse(any(g.parse_repeated_submessages(a,6)
                             for r in g.parse_repeated_submessages(report,4)
                             for a in g.parse_repeated_submessages(r,2)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
