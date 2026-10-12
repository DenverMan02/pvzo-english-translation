"""Combat display protocol: nominal heals, and no future shield at setup."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as game


def unit(pos, **values):
    result = dict(pos=pos, card_id=101, hp=10_000, atk=100, def_=0,
                  ability=0, skill1=51, skill2=0, is_leader=False,
                  level=1, power=100)
    result.update(values)
    return result


def signed(value):
    return value - (1 << 64) if value >= 1 << 63 else value


def wire_events(rounds):
    for turn in game.parse_repeated_submessages(game.encode_fight_replay(rounds), 4):
        for action in game.parse_repeated_submessages(turn, 2):
            for damage in game.parse_repeated_submessages(action, 2):
                yield game.parse_varint_fields(damage)
        for damage in game.parse_repeated_submessages(turn, 3):
            yield game.parse_varint_fields(damage)


class HealShieldDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        game._CLIENTDB_PATH = str(Path(__file__).resolve().parents[2] / "webroot/clientDB.txt")
        game.load_battle_cards()
        game.load_skill_info()

    def test_full_hp_and_overheal_show_nominal_amount_but_match_live_hp(self):
        for start, actual in ((10_000, 0), (9_975, 25), (1_000, 100)):
            with self.subTest(start=start):
                left = [unit(3, skill1=61, initial_hp=start)]
                right = [unit(4)]
                state = {}
                rounds = game.simulate_battle(left, right, None, max_rounds=1,
                                              result_state=state)
                healing = rounds[0][0][0]["damages"][0]
                self.assertEqual(healing["dmg"], -actual)
                events = list(wire_events(rounds))
                self.assertEqual(events[0][1], 3)
                self.assertEqual(events[0][2], 100)  # displayed before Flash's clamp

                # CostarChangeHP draws the delta, then clamps hp + delta to
                # maxHp before updateCardHP. Replaying its rule must agree
                # with the server, even for a wholly wasted heal.
                native = {3: start, 4: 10_000}
                for event in events:
                    target = event[1]
                    native[target] = min(10_000, max(0, native[target] + signed(event[2])))
                self.assertEqual(native, state["hp"])

    def test_multi_target_heal_retains_number_for_full_health_allies(self):
        left = [unit(3, skill1=61), unit(9), unit(15, initial_hp=9_990)]
        with patch.dict(game._SKILL_INFO, {61: ("heal", 0)}):
            rounds = game.simulate_battle(left, [unit(4)], None, max_rounds=1)
        healing = rounds[0][0][0]["damages"]
        self.assertEqual({event["target"] for event in healing}, {3, 9, 15})
        self.assertEqual(sum(-event["dmg"] for event in healing), 10)
        self.assertEqual([game._replay_hp_delta(event) for event in healing], [100, 100, 100])

    def test_disabled_healing_does_not_emit_false_cures(self):
        rounds = game.simulate_battle([unit(3, skill1=61, initial_hp=1_000)],
                                      [unit(4)], None, max_rounds=1, allow_healing=False)
        self.assertEqual(next(wire_events(rounds))[2], 0)

    def test_later_plant_and_zombie_shields_do_not_create_opening_blue_bar(self):
        left = [unit(3, skill1=4001)]
        right = [unit(4, skill1=4001)]
        rounds = game.simulate_battle(left, right, None, max_rounds=1)
        self.assertTrue(any(event.get(6, 0) == 5_000 for event in wire_events(rounds)))
        self.assertEqual([u["max_shield"] for u in left + right], [5_000, 5_000])
        for side in (left, right):
            opening = game.parse_repeated_submessages(game.encode_before_card_list(side), 2)[0]
            self.assertEqual(game.parse_varint_fields(opening)[5], 0)
            self.assertEqual(game.parse_varint_fields(opening)[8], 0)
        before, after = game.encode_left_card_lists({"level": 10}, left, 0)
        self.assertEqual(game.parse_varint_fields(game.parse_repeated_submessages(before, 2)[0])[8], 0)
        self.assertEqual(game.parse_varint_fields(game.parse_repeated_submessages(after, 4)[0])[8], 5_000)

    def test_selected_captain_shield_appears_in_round_zero_grant(self):
        left = [unit(3, card_id=1102, is_leader=True), unit(9)]
        rounds = game.simulate_battle(left, [unit(4)], None, max_rounds=0,
                                      leader_sides=("left",))
        self.assertTrue(rounds[0][0][0]["leader"])
        self.assertEqual([event[6] for event in wire_events(rounds)], [300, 300])
        opening = game.parse_repeated_submessages(game.encode_before_card_list(left), 2)
        self.assertEqual([game.parse_varint_fields(card)[8] for card in opening], [0, 0])
        self.assertEqual([u["final_shield"] for u in left], [300, 300])

    def test_intrinsic_shield_remains_available_at_setup_and_absorbs(self):
        left = [unit(3, shield=500)]
        state = {}
        game.simulate_battle(left, [unit(4)], None, max_rounds=1, result_state=state)
        opening = game.parse_repeated_submessages(game.encode_before_card_list(left), 2)[0]
        fields = game.parse_varint_fields(opening)
        self.assertEqual((fields[5], fields[8]), (500, 500))
        self.assertEqual((state["hp"][3], state["shield"][3]), (10_000, 400))

    def test_unplayed_snapshot_does_not_use_a_stale_future_capacity(self):
        before = game.encode_before_card_list([unit(3, max_shield=5_000)])
        fields = game.parse_varint_fields(game.parse_repeated_submessages(before, 2)[0])
        self.assertEqual((fields[5], fields[8]), (0, 0))


if __name__ == "__main__":
    unittest.main()
