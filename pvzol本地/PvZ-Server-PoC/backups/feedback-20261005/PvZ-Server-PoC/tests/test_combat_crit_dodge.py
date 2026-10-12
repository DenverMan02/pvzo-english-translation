import importlib.util
import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

SERVER = str(Path(__file__).resolve().parents[1])
HERE = os.path.dirname(__file__)
sys.path.insert(0, SERVER)
SOURCE = os.environ.get("PVZ_COMBAT_SOURCE", os.path.join(SERVER, "pvz_poc_server.py"))
spec = importlib.util.spec_from_file_location("combat_under_test", SOURCE)
combat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(combat)
combat._CLIENTDB_PATH = os.path.join(os.path.dirname(SERVER), "webroot", "clientDB.txt")


class CombatRatesTest(unittest.TestCase):
    def test_authored_rates_keep_basis_points(self):
        combat._BATTLE_CARDS = None
        combat.load_battle_cards()
        crit = combat.card_stats_at_level(7401, 1)
        dodge = combat.card_stats_at_level(1801, 1)
        self.assertEqual(crit["critical"], 120)
        self.assertEqual(dodge["dodge"], 100)
        self.assertEqual(combat._combat_proc_chance(120, 0, 7500), 120)

    def test_equipped_reagent_rates_and_formation_units(self):
        reagents = [{"slot_id": 1, "position": 1, "base_id": 60107, "level": 1}]
        with patch.object(combat.store, "get_reagents", return_value=reagents):
            bonus = combat.nutrition_stat_bonus({"uin": 42})
        self.assertEqual(bonus["critical"], 220)
        player = {"uin": 42, "role_id": 42}
        def add_gene(stats, *_):
            stats["critical"] += 400
            return stats
        with ExitStack() as stack:
            stack.enter_context(patch.object(combat.store, "get_formation_leader", return_value=0))
            stack.enter_context(patch.object(combat, "_card_power_source_elements", return_value=[]))
            stack.enter_context(patch.object(combat.store, "get_cards", return_value=[(10001, 7401, 1, 0)]))
            stack.enter_context(patch.object(combat.store, "get_card_fill_quality", return_value=1))
            stack.enter_context(patch.object(combat.store, "get_card_forge_level", return_value=0))
            stack.enter_context(patch.object(combat, "deployed_stat_bonus", return_value=bonus))
            stack.enter_context(patch.object(combat, "card_puzzle_finish_bonus",
                                             return_value={"atk": 0, "def_": 0, "hp": 0, "ability": 0}))
            stack.enter_context(patch.object(combat, "_apply_card_gene_bonus", side_effect=add_gene))
            stack.enter_context(patch.object(combat, "_card_avatar_selected", return_value=0))
            for name in ("_apply_card_fruit_bonus", "_apply_card_baptize_bonus",
                         "_apply_prestige_bonus", "_apply_armor_bonus", "_apply_card_avatar_bonus"):
                # **k: some of these now take optional kwargs (e.g. growth=) — mocks must accept them
                stack.enter_context(patch.object(combat, name, side_effect=lambda stats, *a, **k: stats))
            units = combat.build_player_units(player, {3: 10001})
        self.assertEqual(units[0]["critical"], 740)
        self.assertIn("tough", units[0])

    def test_crit_and_dodge_change_damage_with_counters(self):
        combat.load_battle_cards()
        combat.load_skill_info()
        left = [{"pos": 3, "card_id": 7401, "hp": 100000, "atk": 50, "skill1": 51,
                 "critical": 10000}]
        right = [{"pos": 4, "card_id": 111, "hp": 100000, "atk": 50, "skill1": 51}]
        def loss():
            state = {}
            combat.simulate_battle(left, right, None, max_rounds=20, result_state=state)
            return 100000 - state["hp"][4]
        critical_loss = loss()
        right[0]["tough"] = 10000
        normal_loss = loss()
        self.assertGreater(critical_loss, normal_loss)
        right[0]["dodge"] = 10000
        dodge_loss = loss()
        self.assertLess(dodge_loss, normal_loss)
        left[0]["hit"] = 10000
        self.assertEqual(loss(), normal_loss)

    def test_replay_is_deterministic_and_marks_critical_dodge(self):
        combat._SKILL_INFO = {1: ("attack", 1)}
        left = [{"pos": 3, "card_id": 7401, "hp": 10000, "atk": 50, "skill1": 1,
                 "skill2": 0, "critical": 10000}]
        right = [{"pos": 4, "card_id": 111, "hp": 10000, "atk": 50, "skill1": 1,
                  "skill2": 0, "dodge": 10000}]
        first = combat.simulate_battle(left, right, True, max_rounds=30)
        self.assertEqual(first, combat.simulate_battle(left, right, True, max_rounds=30))
        types = [damage.get("damage_type", 1)
                 for actions, _ in first for action in actions for damage in action["damages"]]
        self.assertIn(2, types)
        self.assertIn(3, types)
        replay = combat.encode_fight_replay(first)
        wire_types = [combat.parse_varint_fields(damage)[3]
                      for round_ in combat.parse_repeated_submessages(replay, 4)
                      for action in combat.parse_repeated_submessages(round_, 2)
                      for damage in combat.parse_repeated_submessages(action, 2)]
        self.assertIn(2, wire_types)
        self.assertIn(3, wire_types)


if __name__ == "__main__":
    unittest.main()
