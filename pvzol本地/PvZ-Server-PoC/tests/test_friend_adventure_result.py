"""Wire contract for a borrowed card in the ordinary adventure result window."""

import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
SOURCE = Path(os.environ.get("PVZ_FRIEND_RESULT_SOURCE", SERVER / "pvz_poc_server.py"))
spec = importlib.util.spec_from_file_location("friend_result_under_test", SOURCE)
game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(game)


class FriendAdventureResultTest(unittest.TestCase):
    def setUp(self):
        self.player = {"uin": 1, "role_id": 1, "level": 50, "name": "test", "face_id": 1}
        # Friend sits at the first battle position. The result view pairs
        # before/after lists by index after splicing the friend from before.
        self.units = [{"pos": 3, "card_id": 1001, "hp": 100, "level": 1,
                       "is_leader": True, "power": 100, "exp": 0,
                       "is_help_card": True}]
        for index in range(6):
            self.units.append({"pos": 7 + 2 * index, "card_id": 2001 + index,
                               "hp": 100, "level": 1, "is_leader": False,
                               "power": 100, "exp": 0, "is_help_card": False})

    def _cards(self, won):
        with patch.object(game, "card_level_from_exp", side_effect=lambda exp, cid, cap: (1, exp)):
            report = game.build_fight_report(self.player, 1, 1, 1, won=won,
                                              left_units=self.units, right_units=[],
                                              card_exp_gain=10)
        left = game.parse_repeated_submessages(report, 2)[0]
        before = [game.parse_varint_fields(c)
                  for c in game.parse_repeated_submessages(left, 2)]
        after = [game.parse_varint_fields(c)
                 for c in game.parse_repeated_submessages(left, 4)]
        return before, after

    def test_win_and_loss_keep_friend_last_and_mark_help_card(self):
        for won in (True, False):
            before, after = self._cards(won)
            self.assertEqual(len(before), 7)
            self.assertEqual([c[2] for c in before[:6]], list(range(2001, 2007)))
            self.assertEqual([c[2] for c in after[:6]], list(range(2001, 2007)))
            self.assertTrue(all(c.get(12, 0) == 0 for c in before[:6]))
            self.assertEqual(before[6][12], 1)
            self.assertEqual(after[6][12], 1)
            # This is exactly the Flash mediator's splice, followed by its
            # six-slot rendering loop; paired cards must remain identical.
            visible = [c for c in before if not c.get(12, 0)]
            self.assertEqual(len(visible), 6)
            self.assertEqual([c[2] for c in visible], [c[2] for c in after[:6]])

    def test_no_friend_uses_all_six_result_bars(self):
        self.units.pop(0)
        before, after = self._cards(True)
        self.assertEqual(len(before), len(after))
        self.assertEqual(len(before), 6)
        self.assertFalse(any(c.get(12, 0) for c in before))


if __name__ == "__main__":
    unittest.main()
