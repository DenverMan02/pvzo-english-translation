import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as game

CLIENTDB = Path(__file__).resolve().parents[2] / "webroot" / "clientDB.txt"


def unit(pos, skill1, skill2=0, card_id=101, leader=False, atk=100, hp=10000):
    return {"pos": pos, "skill1": skill1, "skill2": skill2,
            "card_id": card_id, "is_leader": leader, "atk": atk, "hp": hp}


class EnergyShieldCleanseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        game._CLIENTDB_PATH = str(CLIENTDB)
        game.load_battle_cards()
        game.load_skill_info()

    def test_self_shield_uses_half_current_hp_and_wire_field(self):
        rounds = game.simulate_battle([unit(1, 4001)], [unit(2, 51, atk=10)], True, max_rounds=1)
        action = next(a for actions, _ in rounds for a in actions if a["skill"] == 4001)
        self.assertEqual(action["damages"][0]["change_shield"], 5000)
        wire = game.encode_fight_replay(rounds)
        first = game.parse_repeated_submessages(wire, 4)[0]
        shield_action = game.parse_repeated_submessages(first, 2)[0]
        shield_damage = game.parse_repeated_submessages(shield_action, 2)[0]
        self.assertEqual(game.parse_varint_fields(shield_damage).get(6), 5000)

    def test_end_round_energy_and_enemy_drain(self):
        rounds = game.simulate_battle([unit(1, 9105), unit(3, 4301, 4301)],
                                      [unit(2, 51, atk=10, hp=50000)], True, max_rounds=2)
        self.assertTrue(any(d.get("change_rage") == 2 for _, buffs in rounds for d in buffs))
        drain = [d for actions, _ in rounds for a in actions if a["skill"] == 9105
                 for d in a["damages"] if d.get("change_rage", 0) < 0]
        self.assertTrue(drain)
        wire = game.encode_fight_replay(rounds)
        self.assertTrue(any(4 in game.parse_varint_fields(dd)
                            for rr in game.parse_repeated_submessages(wire, 4)
                            for aa in game.parse_repeated_submessages(rr, 2)
                            for dd in game.parse_repeated_submessages(aa, 2)))

    def test_bonus_cleanse_keeps_shield_status(self):
        self.assertIn("清除目标的所有增益效果", game._SKILL_DESCS[12111])
        # The enemy captain puts an attack buff on the right side at round zero;
        # the dispeller's first hit must remove it and emit a removal record.
        rounds = game.simulate_battle([unit(1, 12111)],
                                      [unit(2, 51, card_id=101, leader=True, atk=10, hp=50000)],
                                      True, max_rounds=1)
        self.assertTrue(any(d.get("buff_id") == 2701 and d.get("is_add_buff") is False
                            for actions, _ in rounds for a in actions
                            for d in a["damages"]))

    def test_random_ally_energy_has_configured_limit(self):
        left = [unit(pos, 9205 if i == 0 else 51) for i, pos in enumerate((1, 3, 5, 7, 9))]
        rounds = game.simulate_battle(left, [unit(2, 51, atk=10, hp=50000)], True, max_rounds=1)
        action = next(a for actions, _ in rounds for a in actions if a["skill"] == 9205)
        self.assertEqual(len(action["damages"]), 3)
        self.assertTrue(all(d.get("change_rage", 0) <= 1 for d in action["damages"]))

    def test_energy_target_count_and_row_front(self):
        self.assertEqual(game._SKILL_INFO[12143], ("aoe", 3))
        self.assertEqual(game._SKILL_INFO[12161], ("aoe", 3))
        right = [unit(pos, 51, atk=10, hp=50000) for pos in (2, 4, 6, 8, 10, 12)]
        rounds = game.simulate_battle([unit(1, 12161)], right, True, max_rounds=2)
        action = next(a for actions, _ in rounds for a in actions if a["skill"] == 12161)
        self.assertEqual({d["target"] for d in action["damages"]}, {2, 4, 6})


if __name__ == "__main__":
    unittest.main()
