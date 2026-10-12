"""Use the actual trial handlers and wire DTOs with an isolated player DB."""
from pathlib import Path
import logging
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import player_store as store

# Set the storage path before importing adapters which register their DB path.
_BOOT_DB = tempfile.TemporaryDirectory(prefix="pvz_trial_boot_")
store.set_db_path(str(Path(_BOOT_DB.name) / "players.db"))
store.init_db()
import pvz_poc_server as g
import trial_road_repairs_20261011 as repairs

repairs.install(g)
logging.getLogger("pvz").setLevel(logging.WARNING)


class TrialRoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pvz_trial_")
        self.addCleanup(self.tmp.cleanup)
        old = store.DB_PATH
        self.addCleanup(lambda: store.set_db_path(old))
        store.set_db_path(str(Path(self.tmp.name) / "players.db"))
        store.init_db()
        self.player, _ = store.get_or_create_player(88011173)
        self.save(level=100)
        g.ensure_player_cards(self.player)
        g._PENDING_PREVIEW.pop(self.player["uin"], None)
        g._BATTLE_SCENES.pop(self.player["uin"], None)

    def save(self, **values):
        store.save_player(self.player["uin"], **values)
        self.player.update(values)

    def preview(self, mode, stage):
        target = g.pb_varint_field(1, 23 if mode else 11) + g.pb_varint_field(7, stage)
        return g.REQUEST_HANDLERS["PVZ.Cmd.Cmd_Battle_GetFightInfo_CS"](
            g.pb_bytes(1, target), self.player)

    def cards(self, body, field):
        return [(f.get(1), f.get(2)) for raw in g.parse_repeated_submessages(body, field)
                for f in [g.parse_varint_fields(raw)]]

    def fight(self, mode, stage, won=True):
        captured = {}

        def battle(left, right, **kwargs):
            captured["right"] = [dict(unit) for unit in right]
            return won, sum(u["power"] for u in left), sum(u["power"] for u in right), []

        prefix = "NewScrewUp" if mode else "ScrewUp"
        with patch.object(g, "run_battle", side_effect=battle):
            messages = g.REQUEST_HANDLERS["PVZ.Cmd.Cmd_%s_Fight_CS" % prefix](
                g.pb_varint_field(1, stage), self.player)
        return messages, captured

    def test_first_and_following_win_caption_uses_cleared_stage_in_both_modes(self):
        for mode in (0, 1):
            prefix = "NewScrewUp" if mode else "ScrewUp"
            field = "tr_hard_current" if mode else "tr_current"
            maximum = "tr_hard_max" if mode else "tr_max"
            for stage in (1, 2, 3):
                with self.subTest(mode=mode, stage=stage):
                    messages, _ = self.fight(mode, stage)
                    cached_stage = stage - 1
                    caption_stage = None
                    for name, body in messages:
                        if name == "PVZ.Cmd.Cmd_%s_GetInfo_SC" % prefix:
                            cached_stage = g.parse_varint_fields(body)[1]
                        elif name == "PVZ.Cmd.Cmd_%s_Fight_SC" % prefix:
                            caption_stage = cached_stage
                    self.assertEqual(caption_stage, stage)
                    self.assertEqual(self.player[field], stage)
                    persisted = store.get_or_create_player(self.player["uin"])[0]
                    self.assertEqual(persisted[field], stage)
                    self.assertEqual(persisted[maximum], stage)

    def test_loss_and_invalid_stage_do_not_advance_or_add_caption_update(self):
        for mode in (0, 1):
            with self.subTest(mode=mode):
                field = "tr_hard_current" if mode else "tr_current"
                used = "tr_hard_battles_used" if mode else "tr_battles_used"
                messages, _ = self.fight(mode, 1, False)
                self.assertEqual(self.player[field], 0)
                self.assertEqual(int(self.player.get(used) or 0), 0)
                self.assertFalse(any(name.endswith("_GetInfo_SC") for name, _ in messages))
                fight = messages[0][1]
                self.assertEqual(g.parse_varint_fields(fight)[5], 0)
                self.assertFalse(g.parse_repeated_submessages(fight, 6))
                invalid, captured = self.fight(mode, 2)
                self.assertEqual(invalid, [])
                self.assertEqual(captured, {})

    def test_explicit_target_mode_and_stage_override_stale_scene_and_progress_hint(self):
        self.save(tr_current=0, tr_hard_current=0)
        g.note_battle_scene(self.player, "gene")
        for mode, stage in ((0, 83), (1, 45)):
            with self.subTest(mode=mode, stage=stage):
                body = self.preview(mode, stage)
                expected = g._tr_enemy_squad(g.load_trial_road()[stage]["power"] * (2 if mode else 1), stage)
                self.assertEqual(self.cards(body, 4), [(u["pos"], u["card_id"]) for u in expected])
                self.assertEqual(g.parse_varint_fields(body)[5], sum(u["power"] for u in expected))
                self.assertEqual(g._PENDING_PREVIEW[self.player["uin"]]["key"],
                                 repairs.trial_key("newscrewup" if mode else "screwup", stage))

    def test_continuous_preparation_and_fight_rosters_match_in_both_modes(self):
        for mode in (0, 1):
            field = "tr_hard_current" if mode else "tr_current"
            maximum = "tr_hard_max" if mode else "tr_max"
            self.save(**{field: 38, maximum: 50})
            for stage in (39, 40):
                with self.subTest(mode=mode, stage=stage):
                    self.preview(mode, stage)
                    body = self.preview(mode, stage)
                    messages, captured = self.fight(mode, stage)
                    self.assertEqual(self.cards(body, 4),
                                     [(u["pos"], u["card_id"]) for u in captured["right"]])
                    prefix = "NewScrewUp" if mode else "ScrewUp"
                    fight = dict(messages)["PVZ.Cmd.Cmd_%s_Fight_SC" % prefix]
                    report = g.parse_repeated_submessages(fight, 3)[0]
                    side = g.parse_repeated_submessages(report, 3)[0]
                    self.assertEqual(self.cards(body, 4), self.cards(side, 2))
                    self.assertEqual(g.parse_varint_fields(body)[5], g.parse_varint_fields(side)[7])

    def test_different_stage_or_mode_preview_cannot_replace_fight_roster(self):
        self.save(tr_current=0, tr_max=5, tr_hard_current=82, tr_hard_max=100)
        for mode, stage in ((1, 83), (0, 83)):
            with self.subTest(preview_mode=mode):
                self.preview(mode, stage)
                self.save(tr_current=0)
                _, captured = self.fight(0, 1)
                expected = g._tr_enemy_squad(g.load_trial_road()[1]["power"], 1)
                self.assertEqual([(u["pos"], u["card_id"]) for u in captured["right"]],
                                 [(u["pos"], u["card_id"]) for u in expected])

    def test_response_reorder_preserves_wallet_and_quest_relative_order(self):
        # The real handler grants first-clear items and emits quest pushes.
        with patch.object(g, "grant_fuben_reward", return_value=[("wallet", b"w"), ("inventory", b"i")]), \
                patch.object(g, "_quest_record_condition", return_value=[("quest", b"q")]):
            messages, _ = self.fight(0, 1)
        self.assertEqual([name for name, _ in messages], [
            "PVZ.Cmd.Cmd_ScrewUp_GetInfo_SC", "PVZ.Cmd.Cmd_ScrewUp_Fight_SC",
            "wallet", "inventory", "quest"])

    def test_nontrial_preview_keeps_existing_route_and_helper_delegates(self):
        helper_result = b"ordinary preview bytes"
        route_result = [("PVZ.Cmd.Cmd_Battle_GetFightInfo_SC", b"world-map preview")]
        seen = []

        def helper(data, player):
            seen.append("helper")
            return helper_result

        def route(data, player):
            seen.append("route")
            return route_result

        request = "PVZ.Cmd.Cmd_Battle_GetFightInfo_CS"
        api = SimpleNamespace(**vars(g))
        api._trial_road_repairs_20261011 = False
        api.REQUEST_HANDLERS = dict(g.REQUEST_HANDLERS)
        api.REQUEST_HANDLERS[request] = route
        api.handle_battle_getfightinfo = helper
        repairs.install(api)
        data = g.pb_bytes(1, g.pb_varint_field(1, 3) + g.pb_varint_field(8, 901))
        self.assertIs(api.REQUEST_HANDLERS[request](data, self.player), route_result)
        self.assertIs(api.handle_battle_getfightinfo(data, self.player), helper_result)
        self.assertEqual(seen, ["route", "helper"])


if __name__ == "__main__":
    unittest.main()
