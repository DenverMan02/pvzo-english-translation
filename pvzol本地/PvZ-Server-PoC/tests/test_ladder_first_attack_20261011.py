"""Ladder's authored attacker-first rule must override only this mode."""
import copy
import atexit
import unittest
from test_enemy_initiative_20261010 import game as g
atexit.register(g.store.close_thread_db)


class LadderOpeningTests(unittest.TestCase):
    def test_ladder_attacks_first_while_other_modes_keep_initiative(self):
        def unit(pos, speed):
            return dict(pos=pos, card_id=101, hp=10000, atk=100, def_=0,
                        ability=0, skill1=51, skill2=0, is_leader=False,
                        level=1, power=100, speed=speed)
        left, right = [unit(3, 1)], [unit(4, 9999)]
        normal = g.run_battle(copy.deepcopy(left), copy.deepcopy(right), max_rounds=1)[3]
        ladder = g.run_battle(copy.deepcopy(left), copy.deepcopy(right), max_rounds=1, attacker_first=True)[3]
        self.assertEqual(normal[0][0][0]['actor'], 4)
        self.assertEqual(ladder[0][0][0]['actor'], 3)


if __name__ == '__main__':
    unittest.main()
