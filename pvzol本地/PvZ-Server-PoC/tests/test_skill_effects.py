import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import player_store as store
import pvz_poc_server as game


CLIENTDB = Path(__file__).resolve().parents[2] / "webroot" / "clientDB.txt"


class SkillEffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        game._CLIENTDB_PATH = str(CLIENTDB)
        game.load_battle_cards()
        game.load_skill_info()

    def units(self):
        left_ids = (1705, 1245, 8105, 101, 101, 101)
        left = [dict(pos=pos, card_id=cid, hp=10000, atk=150,
                     skill1=game._BATTLE_CARDS[cid]["skill1"],
                     skill2=game._BATTLE_CARDS[cid]["skill2"],
                     is_leader=i == 0)
                for i, (pos, cid) in enumerate(zip((1, 3, 5, 7, 9, 11), left_ids))]
        right = [dict(pos=pos, card_id=101, hp=30000, atk=50,
                      skill1=51, skill2=1301, is_leader=i == 0)
                 for i, pos in enumerate((2, 4, 6, 8, 10, 12))]
        return left, right

    def test_target_rules_and_state_lifetime(self):
        self.assertEqual(game._SKILL_INFO[1005], ("aoe", 0))
        self.assertEqual(game._SKILL_INFO[12465], ("heal", 0))
        self.assertEqual(game._SKILL_INFO[9305], ("heal", 3))
        self.assertEqual(game._SKILL_EFFECTS[12455]["state"], 19940)
        self.assertEqual(game._SKILL_INFO[701], ("aoe", 0))
        self.assertEqual(game._SKILL_INFO[1901][1], 3)
        self.assertEqual(game._SKILL_INFO[81], ("aoe", 0))
        self.assertEqual(game._SKILL_INFO[8401], ("attack", 1))
        self.assertEqual(game._SKILL_INFO[12201], ("aoe", 3))
        self.assertEqual(game._SKILL_EFFECTS[12201]["turns"], 2)
        left, right = self.units()
        rounds = game.simulate_battle(left, right, True, max_rounds=4)
        actions = [a for action_list, _ in rounds for a in action_list]
        ice = next(a for a in actions if a["skill"] == 1005)
        moon = next(a for a in actions if a["skill"] == 12465)
        oxygen = next(a for a in actions if a["skill"] == 9305)
        self.assertEqual(len(ice["damages"]), 6)
        self.assertEqual(len(moon["damages"]), 6)
        self.assertEqual(len(oxygen["damages"]), 3)
        self.assertTrue(all(d.get("buff_id") == 19605 for d in moon["damages"]))
        self.assertTrue(all(d.get("buff_id") == 14805 for d in oxygen["damages"]))
        ticks = [d for _, buffs in rounds for d in buffs if d["dmg"] < 0]
        self.assertTrue(ticks)
        self.assertTrue(any(d.get("buff_id") == 14805 and d.get("is_add_buff") is False
                            for _, buffs in rounds for d in buffs))

    def test_leader_round_and_buff_wire(self):
        left, right = self.units()
        leader_effect = game._leader_effect
        self.assertEqual(leader_effect(10605)["chance"], 10)
        def guaranteed_proc(sid):
            effect = leader_effect(sid)
            return dict(effect, chance=100) if sid == 10605 else effect
        # This checks wire serialization when the 10% captain effect triggers;
        # a deterministic battle seed need not trigger it naturally.
        with patch.object(game, "_leader_effect", side_effect=guaranteed_proc):
            rounds = game.simulate_battle(left, right, True, max_rounds=3)
        self.assertEqual(rounds[0][0][0]["skill"], 10605)
        wire = game.encode_fight_replay(rounds)
        zero = game.parse_repeated_submessages(wire, 4)[0]
        self.assertEqual(game.parse_varint_fields(zero).get(1), 0)
        action = game.parse_repeated_submessages(zero, 2)[0]
        self.assertEqual(game.parse_varint_fields(
            game.parse_repeated_submessages(action, 1)[0]).get(3), 10605)
        self.assertTrue(any(game.parse_varint_fields(d).get(5) == 100
                            for d in game.parse_repeated_submessages(action, 2)))

    def test_adventure_can_suppress_synthetic_enemy_captain_and_keep_shield(self):
        # Sunflower leader 10301 grants state 2201 (a small shield) to the player side.
        # The fuben zombie card 111 has its own unrelated leader skill 10001; adventure must not
        # invent that opposing captain action, and the player's shield should be consumed by damage,
        # not expire at round start.
        left = [dict(pos=3, card_id=1101, hp=10000, atk=1,
                     skill1=0, skill2=0, is_leader=True)]
        right = [dict(pos=4, card_id=111, hp=50000, atk=1,
                      skill1=0, skill2=0, is_leader=True)]
        rounds = game.simulate_battle(left, right, True, max_rounds=1,
                                      leader_sides=("left",))
        captain_actions = [a for actions, _ in rounds for a in actions if a.get("leader")]
        self.assertEqual([a["skill"] for a in captain_actions], [10301])
        self.assertEqual(captain_actions[0]["damages"][0].get("buff_id"), 2201)
        self.assertEqual(captain_actions[0]["damages"][0].get("change_shield"), 200)
        absorbed = [d.get("change_shield") for actions, _ in rounds for a in actions
                    if not a.get("leader") for d in a["damages"]
                    if d.get("target") == 3 and d.get("change_shield")]
        self.assertEqual(absorbed, [-1])

    def test_adventure_replay_stops_at_final_kill_or_player_loss(self):
        # A resolved adventure battle must end on the lethal action, without a later puppet action,
        # charge filler, or a status tick. The generic/default simulator keeps its fade buffer for
        # other modes.
        left = [dict(pos=3, card_id=101, hp=10000, atk=100000,
                     skill1=51, skill2=0, is_leader=False)]
        right = [dict(pos=2, card_id=111, hp=10, atk=1,
                      skill1=51, skill2=0, is_leader=False)]
        puppets = {1: {"actor": 101, "skill": 0}, 2: {"actor": 103, "skill": 0}}

        won = game.simulate_battle(left, right, True, puppets=puppets,
                                   stop_on_elimination=True)
        self.assertEqual(len(won), 1)
        self.assertTrue(won[-1][0][-1]["damages"][0]["dead"])
        self.assertFalse(any(a.get("puppet") for actions, _ in won for a in actions))
        self.assertEqual(won[-1][0][-1]["actor"], 3)

        lost = game.simulate_battle(
            [dict(pos=3, card_id=101, hp=10, atk=1, skill1=51, skill2=0, is_leader=False)],
            [dict(pos=2, card_id=111, hp=50000, atk=1000, skill1=51, skill2=0, is_leader=False)],
            False, puppets=puppets, stop_on_elimination=True)
        self.assertEqual(len(lost), 1)
        self.assertTrue(lost[-1][0][-1]["damages"][0]["dead"])
        self.assertFalse(any(a.get("puppet") for actions, _ in lost for a in actions))
        self.assertEqual(lost[-1][0][-1]["actor"], 2)

        default = game.simulate_battle(left, right, True, puppets=puppets)
        # The client completes the final kill action without a synthetic tail
        # round. Replay settlement must stop at that real killing blow.
        self.assertEqual(len(default), 1)
        self.assertTrue(default[-1][0][-1]["damages"][0]["dead"])
        self.assertFalse(any(a.get("puppet") for actions, _ in default for a in actions))

    def test_nearby_targets_are_center_and_adjacent(self):
        left = [dict(pos=1, card_id=101, hp=10000, atk=100,
                     skill1=81, skill2=0, is_leader=False)]
        right = [dict(pos=p, card_id=101, hp=10000, atk=10,
                      skill1=51, skill2=0, is_leader=False)
                 for p in (2, 4, 6, 8, 10, 12)]
        rounds = game.simulate_battle(left, right, True, max_rounds=1)
        action = next(a for actions, _ in rounds for a in actions if a["skill"] == 81)
        self.assertEqual({d["target"] for d in action["damages"]}, {2, 4, 8})

    def test_selected_captain_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            original_path = store.DB_PATH
            store.DB_PATH = str(Path(temp) / "players.db")
            try:
                store.init_db()
                store.set_formation(12345, 1, {3: 1001, 7: 1002})
                store.set_formation_leader(12345, 1, 1002)
                self.assertEqual(store.get_formation_leader(12345, 1), 1002)
                self.assertEqual(store.get_formation_leader(12345, 2), 0)
            finally:
                store.close_thread_db()   # release players.db before the temp dir goes away
                store.DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()
