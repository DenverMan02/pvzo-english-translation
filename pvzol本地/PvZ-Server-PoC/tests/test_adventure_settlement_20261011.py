"""Adventure reward settlement and the Flash sweep-row wire contract.

All storage is redirected before importing the game; never open an installed
player database. Battle outcomes are controlled so these tests cover settlement,
not the independent combat engine.
"""
from contextlib import ExitStack
from pathlib import Path
import atexit
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store

_temporary = tempfile.TemporaryDirectory(prefix="pvzol_adventure_settlement_")
store.set_db_path(str(Path(_temporary.name) / "synthetic_players.db"))
store.init_db()
import pvz_poc_server as game


def _cleanup():
    store.close_thread_db()
    _temporary.cleanup()


atexit.register(_cleanup)


def _items(blob, field):
    result = {}
    for dto in game.parse_repeated_submessages(blob, field):
        fields = game.parse_varint_fields(dto)
        iid, amount = fields[2], fields[3]
        result[iid] = result.get(iid, 0) + amount
    return result


def _reward_items(blob, field):
    result = {}
    for reward in game.parse_repeated_submessages(blob, field):
        for iid, amount in _items(reward, 4).items():
            result[iid] = result.get(iid, 0) + amount
    return result


class AdventureSettlementTests(unittest.TestCase):
    next_uin = 700010100

    def setUp(self):
        type(self).next_uin += 1
        self.player, _ = store.get_or_create_player(self.next_uin)
        values = dict(level=100, exp=0, energy=150, energy_time=int(time.time()),
                      coin=10000, vip_level=3)
        store.save_player(self.player["uin"], **values)
        self.player.update(values)
        game.ensure_player_cards(self.player)
        self.mocks = ExitStack()
        self.addCleanup(self.mocks.close)
        for name in ("_quest_record_condition", "_quest_observe_card_progress",
                     "_quest_sync_messages"):
            self.mocks.enter_context(patch.object(game, name, return_value=[]))

    def unlock(self, chapter, mode, stage, cleared=True):
        with store.atomic_player(self.player):
            for earlier, _need in game.load_fuben_chapters():
                if earlier >= chapter:
                    break
                for passed in game.load_fuben_stages().get((earlier, 1), []):
                    store.pass_fuben_stage(self.player["uin"], earlier, 1, passed, 3)
            if cleared:
                store.pass_fuben_stage(self.player["uin"], chapter, mode, stage, 3)

    def request(self, chapter=1, mode=1, stage=1, times=1):
        self.unlock(chapter, mode, stage)
        return (game.pb_varint_field(1, chapter) + game.pb_varint_field(2, mode)
                + game.pb_varint_field(3, stage) + game.pb_varint_field(4, times)
                + game.pb_varint_field(5, int(times > 1)))

    def sweep(self, **values):
        return game.REQUEST_HANDLERS["PVZ.Cmd.Cmd_Fuben_Sweep_CS"](
            self.request(**values), self.player)

    def fight(self, chapter=12, mode=2, stage=1, won=True, first=False):
        # Seed genuine chapter prerequisites, keeping the real difficulty
        # decoder, settlement gate and reward/report path under test.
        self.unlock(chapter, mode, stage, cleared=not first)
        target = (game.pb_varint_field(1, 1) + game.pb_varint_field(2, chapter)
                  + game.pb_varint_field(3, mode) + game.pb_varint_field(4, stage))
        with (patch.object(game, "run_battle", return_value=(won, 1, 1, [])),
              patch.object(game.random, "randint", return_value=1)):
            return game.REQUEST_HANDLERS["PVZ.Cmd.Cmd_Battle_Fight_CS"](
                game.pb_bytes(1, target), self.player)

    def test_ten_runs_have_ten_rows_and_matching_energy_experience_and_loot(self):
        before_exp = sum(c[3] for c in store.get_cards(self.player["uin"]))
        with patch.object(game.random, "randint", return_value=10000):
            messages = self.sweep(times=10)
        rows = game.parse_repeated_submessages(dict(messages)["PVZ.Cmd.Cmd_Fuben_Sweep_SC"], 1)
        self.assertEqual(len(rows), 10)
        for row in rows:
            rewards = _reward_items(row, 1)
            self.assertEqual(rewards[1], 139)
            self.assertEqual(rewards[5], 416)
            self.assertEqual(rewards[2], game.role_exp_for_clear(1, False))
            self.assertEqual(rewards[game.hotspot_events.SHELL], 1)
            self.assertEqual(len(game.parse_repeated_submessages(row, 2)), 1)
        saved = store.get_or_create_player(self.player["uin"])[0]
        self.assertEqual(saved["energy"], 100)
        self.assertEqual(saved["coin"], 10000 + 139 * 10)
        self.assertEqual(saved["exp"], game.role_exp_for_clear(1, False) * 10)
        self.assertEqual(store.get_item_count(self.player["uin"], 2), 0)
        self.assertEqual(store.get_item_count(self.player["uin"], game.hotspot_events.SHELL), 10)
        self.assertGreater(sum(c[3] for c in store.get_cards(self.player["uin"])), before_exp)
        self.assertIn("PVZ.Cmd.Cmd_SyncExp_SC", dict(messages))

    def test_partial_sweep_keeps_each_independent_roll_visible(self):
        rolls = [[(1, 10), (2121, 2)], [(1, 20)], [(1, 30), (2121, 2)]]
        with (patch.object(game, "fuben_stage_reward", side_effect=rolls),
              patch.object(game, "fuben_lab_material_reward", return_value=[])):
            messages = self.sweep(times=3)
        rows = game.parse_repeated_submessages(dict(messages)["PVZ.Cmd.Cmd_Fuben_Sweep_SC"], 1)
        self.assertEqual([_reward_items(row, 1).get(2121, 0) for row in rows], [2, 0, 2])
        self.assertEqual([_reward_items(row, 1)[1] for row in rows], [10, 20, 30])
        self.assertEqual(store.get_item_count(self.player["uin"], 2121), 4)
        self.assertEqual(self.player["energy"], 135)
        self.assertEqual(self.player["exp"], game.role_exp_for_clear(1, False) * 3)

    def test_real_elite_star_soul_sweep_lands_in_every_row_bag_and_sync(self):
        with patch.object(game.random, "randint", return_value=1):
            messages = self.sweep(chapter=12, mode=2, times=10)
        rows = game.parse_repeated_submessages(dict(messages)["PVZ.Cmd.Cmd_Fuben_Sweep_SC"], 1)
        self.assertEqual([_reward_items(row, 1)[2121] for row in rows], [2] * 10)
        self.assertEqual(store.get_item_count(self.player["uin"], 2121), 20)
        updates = _items(dict(messages)["PVZ.Cmd.Cmd_Item_Update_SC"], 1)
        self.assertEqual(updates[2121], 20)
        self.assertEqual(self.player["energy"], 50)

    def test_real_elite_fight_preserves_mode_and_star_soul_chest_bag_sync(self):
        messages = self.fight()
        report = game.parse_repeated_submessages(dict(messages)["PVZ.Cmd.Cmd_Battle_Fight_SC"], 1)[0]
        left = game.parse_repeated_submessages(report, 2)[0]
        self.assertEqual(_reward_items(left, 3)[2121], 2)
        self.assertEqual(store.get_item_count(self.player["uin"], 2121), 2)
        self.assertEqual(_items(dict(messages)["PVZ.Cmd.Cmd_Item_Update_SC"], 1)[2121], 2)

    def test_first_elite_clear_also_grants_star_soul(self):
        messages = self.fight(first=True)
        self.assertEqual(store.get_item_count(self.player["uin"], 2121), 2)
        self.assertEqual(store.get_fuben_passed(self.player["uin"], 12, 2), {1: 3})
        self.assertEqual(self.player["exp"], game.role_exp_for_clear(12, True))
        self.assertIn("PVZ.Cmd.Cmd_Fuben_SyncStageInfo_SC", dict(messages))

    def test_sweep_town_level_up_is_saved_and_synchronized(self):
        threshold = game.need_exp(1)
        store.save_player(self.player["uin"], level=1, exp=threshold - 5)
        self.player.update(level=1, exp=threshold - 5)
        with patch.object(game.random, "randint", return_value=10000):
            messages = self.sweep()
        saved = store.get_or_create_player(self.player["uin"])[0]
        self.assertEqual(saved["level"], 2)
        self.assertEqual(saved["exp"], game.role_exp_for_clear(1, False) - 5)
        self.assertEqual(game.parse_varint_fields(dict(messages)["PVZ.Cmd.Cmd_SyncLevel_SC"])[1], 2)

    def test_authored_star_soul_gate_and_chance_boundary(self):
        required, first, optional = game.load_fuben_rewards()[(12, 2, 1)]
        self.assertIn((2121, 2, 2000), optional)
        for roll, expected in ((2000, 2), (2001, 0)):
            with patch.object(game.random, "randint", return_value=roll):
                self.assertEqual(dict(game.fuben_stage_reward(12, 2, 1, False)).get(2121, 0), expected)
        with patch.object(game.random, "randint", return_value=1):
            for chapter, mode in ((11, 2), (12, 1), (12, 3), (999, 2)):
                self.assertNotIn(2121, dict(game.fuben_stage_reward(chapter, mode, 1, False)))

    def test_defeat_has_no_star_soul_reward_or_town_experience(self):
        messages = self.fight(won=False)
        self.assertEqual(store.get_item_count(self.player["uin"], 2121), 0)
        self.assertEqual(self.player["exp"], 0)
        report = game.parse_repeated_submessages(dict(messages)["PVZ.Cmd.Cmd_Battle_Fight_SC"], 1)[0]
        left = game.parse_repeated_submessages(report, 2)[0]
        self.assertEqual(_reward_items(left, 3), {})

    def test_failed_reward_roll_and_failed_grant_refund_whole_sweep(self):
        request = self.request(times=10)
        before = store.get_or_create_player(self.player["uin"])[0]
        for failure in ("fuben_stage_reward", "grant_role_exp"):
            with (patch.object(game.random, "randint", return_value=1),
                  patch.object(game, failure, side_effect=RuntimeError("synthetic failure"))):
                with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                    game.handle_fuben_sweep(request, self.player)
            saved = store.get_or_create_player(self.player["uin"])[0]
            self.assertEqual((saved["energy"], saved["coin"], saved["exp"]),
                             (before["energy"], before["coin"], before["exp"]))
            self.assertEqual(self.player["energy"], before["energy"])
            self.assertEqual(store.get_item_count(self.player["uin"], game.hotspot_events.SHELL), 0)

    def test_insufficient_energy_returns_no_results_and_grants_nothing(self):
        store.save_player(self.player["uin"], energy=4)
        self.player["energy"] = 4
        self.assertEqual(self.sweep(), [])
        self.assertEqual(self.player["energy"], 4)
        self.assertEqual(self.player["exp"], 0)
        self.assertEqual(self.player["coin"], 10000)


if __name__ == "__main__":
    unittest.main()
