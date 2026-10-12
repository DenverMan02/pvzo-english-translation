import unittest
from pathlib import Path

import pvz_poc_server as game


CLIENTDB = Path(__file__).resolve().parents[2] / "webroot" / "clientDB.txt"


def signed_int64(value):
    return value - (1 << 64) if value & (1 << 63) else value


class CaptainShieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        game._CLIENTDB_PATH = str(CLIENTDB)
        game.load_battle_cards()

    def test_captain_shield_is_real_side_correct_absorbs_damage_and_serializes(self):
        # Sunflower's middle captain skill (10302) should give 3% max HP as a
        # real shield to every ally. Deliberately omit the state row: the captain
        # effect itself is enough to mutate combat state and produce the DTO.
        left = [
            dict(pos=3, card_id=1102, hp=10_000, atk=1,
                 skill1=0, skill2=0, is_leader=True),
            dict(pos=5, card_id=101, hp=10_000, atk=1,
                 skill1=0, skill2=0, is_leader=False),
        ]
        # Use the same captain card on the opposing side; adventure's
        # leader_sides setting must not cast the player's skill for enemies.
        right = [dict(pos=4, card_id=1102, hp=100_000, atk=250,
                      skill1=0, skill2=0, is_leader=True)]

        original_states = game._BATTLE_STATES
        game._BATTLE_STATES = {}
        try:
            rounds = game.simulate_battle(
                left, right, won=False, max_rounds=2, leader_sides=("left",))
        finally:
            game._BATTLE_STATES = original_states

        actions = [action for turn, _ in rounds for action in turn]
        captains = [action for action in actions if action.get("leader")]
        self.assertEqual([action["skill"] for action in captains], [10302])
        captain_deltas = captains[0]["damages"]
        self.assertEqual({damage["target"] for damage in captain_deltas}, {3, 5})
        self.assertEqual([damage["change_shield"] for damage in captain_deltas], [300, 300])
        self.assertTrue(all(damage["buff_id"] == 2202 for damage in captain_deltas))
        self.assertFalse(any(damage.get("change_shield", 0) > 0
                             for action in actions if action["actor"] == 4
                             for damage in action["damages"]))

        # The opponent's attack is first entirely absorbed by the first 250
        # shield points; the next hit consumes the remaining 50 and damages HP.
        shield_losses = [damage["change_shield"] for action in actions
                         if not action.get("leader")
                         for damage in action["damages"]
                         if damage.get("target") == 3 and damage.get("change_shield")]
        self.assertEqual(shield_losses, [-250, -50])
        combat_actions = [action for turn, _ in rounds[1:3] for action in turn]
        hp_changes = [damage["dmg"] for action in combat_actions if action["actor"] == 4
                      for damage in action["damages"] if damage.get("target") == 3]
        self.assertEqual(hp_changes, [0, 200])

        replay = game.encode_fight_replay(rounds)
        captain_round = game.parse_repeated_submessages(replay, 4)[0]
        captain_action = game.parse_repeated_submessages(captain_round, 2)[0]
        wire_damages = game.parse_repeated_submessages(captain_action, 2)
        self.assertEqual([game.parse_varint_fields(damage).get(6)
                          for damage in wire_damages], [300, 300])

        later_damage = [damage for round_data in game.parse_repeated_submessages(replay, 4)
                        for action in game.parse_repeated_submessages(round_data, 2)
                        for damage in game.parse_repeated_submessages(action, 2)
                        if game.parse_varint_fields(damage).get(1) == 3
                        and signed_int64(game.parse_varint_fields(damage).get(6, 0)) < 0]
        self.assertEqual([signed_int64(game.parse_varint_fields(damage)[6])
                          for damage in later_damage], [-250, -50])


if __name__ == "__main__":
    unittest.main()
