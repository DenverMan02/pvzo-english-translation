"""Formation turns alternate; combat and the wire replay share that order."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g


def unit(pos, **values):
    result = dict(pos=pos, card_id=101, hp=10000, atk=100, def_=0,
                  ability=0, skill1=51, skill2=0, is_leader=False,
                  level=1, power=100, speed=0)
    result.update(values)
    return result


class BattleTurnOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g._CLIENTDB_PATH = str(Path(__file__).resolve().parents[2] / 'webroot/clientDB.txt')
        g.load_battle_cards()
        g.load_skill_info()

    def fight(self, left, right, **options):
        state = {}
        rounds = g.simulate_battle(copy.deepcopy(left), copy.deepcopy(right), None,
                                   result_state=state,
                                   max_rounds=options.pop('max_rounds', 1), **options)
        return rounds, state

    def actors(self, rounds):
        return [[action['actor'] for action in actions if not action.get('update')]
                for actions, _buffs in rounds]

    def lineup(self):
        return [unit(p) for p in (3, 9, 15)], [unit(p) for p in (4, 10, 16)]

    def wire_actors(self, wire):
        result = []
        for round_body in g.parse_repeated_submessages(wire, 4):
            actors = []
            for action in g.parse_repeated_submessages(round_body, 2):
                if g.parse_varint_fields(action).get(5, 0) == 1:
                    continue
                actor = (g.parse_repeated_submessages(action, 1)
                         or g.parse_repeated_submessages(action, 6))[0]
                actors.append(g.parse_varint_fields(actor)[1])
            result.append(actors)
        return result

    def test_three_by_three_alternates_one_formation_slot_each(self):
        rounds, _ = self.fight(*self.lineup())
        self.assertEqual(self.actors(rounds), [[3, 4, 9, 10, 15, 16]])

    def test_total_initiative_selects_starting_side(self):
        left, right = self.lineup()
        left[0]['speed'] = 100
        right[0]['speed'] = right[1]['speed'] = 60
        rounds, _ = self.fight(left, right)
        self.assertEqual(self.actors(rounds), [[4, 3, 10, 9, 16, 15]])

    def test_equal_total_initiative_starts_left(self):
        left, right = self.lineup()
        left[0]['speed'], left[1]['speed'] = 20, 10
        right[0]['speed'] = 30
        rounds, _ = self.fight(left, right)
        self.assertEqual(self.actors(rounds), [[3, 4, 9, 10, 15, 16]])

    def test_formation_queues_restart_in_each_round(self):
        left, right = self.lineup()
        right[0]['speed'] = 1
        rounds, state = self.fight(left, right, max_rounds=3)
        self.assertEqual(self.actors(rounds), [[4, 3, 10, 9, 16, 15]] * 3)
        self.assertEqual(state['hp'], {3: 9100, 9: 10000, 15: 10000,
                                      4: 9100, 10: 10000, 16: 10000})
        self.assertTrue(state['timed_out'])

    def test_side_with_more_units_finishes_its_remaining_queue(self):
        for left, right, expected in (
                ([unit(3), unit(9), unit(15)], [unit(4)], [3, 4, 9, 15]),
                ([unit(3)], [unit(4), unit(10), unit(16)], [3, 4, 10, 16]),
                ([unit(3)], [unit(4, speed=1), unit(10), unit(16)], [4, 3, 10, 16])):
            with self.subTest(expected=expected):
                rounds, _ = self.fight(left, right)
                self.assertEqual(self.actors(rounds), [expected])

    def test_dead_unacted_unit_is_replaced_in_same_side_opportunity(self):
        left, right = self.lineup()
        right[0]['hp'] = 50
        rounds, state = self.fight(left, right)
        self.assertEqual(self.actors(rounds), [[3, 10, 9, 16, 15]])
        self.assertEqual(state['hp'][4], 0)
        self.assertEqual(state['hp'][3], 9800)

    def test_replacement_also_works_when_right_side_starts(self):
        left, right = self.lineup()
        left[0]['hp'] = 50
        right[0]['speed'] = 1
        rounds, state = self.fight(left, right)
        self.assertEqual(self.actors(rounds), [[4, 9, 10, 15, 16]])
        self.assertEqual(state['hp'][3], 0)

    def test_multiple_dead_slots_do_not_spend_side_opportunities(self):
        left, right = self.lineup()
        right[0]['initial_hp'] = right[1]['initial_hp'] = 0
        rounds, _ = self.fight(left, right)
        self.assertEqual(self.actors(rounds), [[3, 16, 9, 15]])

    def test_freeze_consumes_its_slot_instead_of_replacing_it(self):
        left, right = self.lineup()
        left[0]['skill1'] = 3401  # Guaranteed freeze on the current target.
        rounds, state = self.fight(left, right)
        # Right 4 misses its opportunity; right 10 acts after left 9.
        self.assertEqual(self.actors(rounds), [[3, 9, 10, 15, 16]])
        self.assertGreater(state['hp'][4], 0)
        self.assertEqual(state['hp'][3], 9800)
        self.assertTrue(any(event.get('buff_id') == 100
                            and not event.get('is_add_buff')
                            for event in rounds[0][1]))

    def test_late_freeze_is_consumed_on_the_next_round(self):
        left, right = self.lineup()
        right[0]['skill1'] = 3401
        rounds, _ = self.fight(left, right, max_rounds=2)
        self.assertEqual(self.actors(rounds), [[3, 4, 9, 10, 15, 16],
                                             [4, 9, 10, 15, 16]])

    def test_input_order_cannot_reorder_formation_turns(self):
        left, right = self.lineup()
        ordered, ordered_state = self.fight(left, right, max_rounds=2)
        shuffled, shuffled_state = self.fight([left[2], left[0], left[1]],
                                               [right[1], right[2], right[0]],
                                               max_rounds=2)
        self.assertEqual(self.actors(shuffled), [[3, 4, 9, 10, 15, 16]] * 2)
        self.assertEqual(shuffled, ordered)
        self.assertEqual(shuffled_state, ordered_state)

    def test_elimination_stops_before_survivors_or_puppet_can_act(self):
        rounds, state = self.fight([unit(3), unit(9), unit(15)], [unit(4, hp=50)],
                                   max_rounds=3, stop_on_elimination=True,
                                   puppets={1: {'actor': 101, 'skill': 101}})
        self.assertEqual(self.actors(rounds), [[3]])
        self.assertEqual(rounds[0][1], [])
        self.assertTrue(state['won'])
        self.assertFalse(state['timed_out'])

    def test_area_and_repeated_hits_stay_within_one_action(self):
        for skill, hits in ((1, 3), (4701, 3)):
            with self.subTest(skill=skill):
                left, right = self.lineup()
                left[0]['skill1'] = skill
                rounds, _ = self.fight(left, right)
                self.assertEqual(self.actors(rounds), [[3, 4, 9, 10, 15, 16]])
                first_action = rounds[0][0][0]
                self.assertEqual(len([damage for damage in first_action['damages']
                                      if damage['dmg'] > 0]), hits)

    def test_captains_keep_round_zero_and_puppet_finishes_normal_round(self):
        left = [unit(3, card_id=11, is_leader=True), unit(9)]
        right = [unit(4, card_id=11, is_leader=True, speed=1), unit(10)]
        rounds, _ = self.fight(left, right, puppets={1: {'actor': 101, 'skill': 101}})
        self.assertEqual(self.actors(rounds), [[3, 4], [4, 3, 10, 9, 101]])
        self.assertTrue(all(action.get('leader') for action in rounds[0][0]))
        self.assertTrue(rounds[1][0][-1].get('puppet'))
        wire = g.encode_fight_replay(rounds)
        self.assertEqual([g.parse_varint_fields(body)[1]
                          for body in g.parse_repeated_submessages(wire, 4)], [0, 1])
        self.assertEqual(self.wire_actors(wire), self.actors(rounds))

    def test_encoded_action_order_and_hp_match_live_settlement(self):
        left = [unit(3, hp=150), unit(9, hp=150)]
        right = [unit(4, hp=150), unit(10, hp=150)]
        rounds, state = self.fight(left, right, max_rounds=3)
        self.assertEqual(self.actors(rounds), [[3, 4, 9, 10], [9, 10], [9]])
        self.assertEqual(state['hp'], {3: 0, 9: 50, 4: 0, 10: 0})
        self.assertTrue(state['won'])
        wire = g.encode_fight_replay(rounds)
        self.assertEqual(self.wire_actors(wire), self.actors(rounds))
        wire_hp = {u['pos']: u['hp'] for u in left + right}
        def signed(value):
            return value - (1 << 64) if value >= (1 << 63) else value
        for round_body in g.parse_repeated_submessages(wire, 4):
            damage_bodies = [damage
                             for action in g.parse_repeated_submessages(round_body, 2)
                             for damage in g.parse_repeated_submessages(action, 2)]
            damage_bodies += g.parse_repeated_submessages(round_body, 3)
            for damage in damage_bodies:
                fields = g.parse_varint_fields(damage)
                wire_hp[fields[1]] += signed(fields.get(2, 0))
        self.assertEqual(wire_hp, state['hp'])
        won, _left_power, _right_power, live_rounds = g.run_battle(left, right, max_rounds=3)
        self.assertTrue(won)
        self.assertEqual(live_rounds, rounds)
        self.assertEqual({u['pos']: u['final_hp'] for u in left + right}, state['hp'])


if __name__ == '__main__':
    unittest.main()
