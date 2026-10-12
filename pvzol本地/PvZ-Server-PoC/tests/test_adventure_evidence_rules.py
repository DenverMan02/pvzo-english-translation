"""Regression coverage for mode isolation and the adventure settlement gate."""

from contextlib import ExitStack
from pathlib import Path
import atexit
import tempfile
import unittest
from unittest.mock import patch

from worldmap_test_support import IsolatedServer

# Redirect storage before any game import, including when run from the live
# installation. The test never opens the user's default player database.
_qa = IsolatedServer()
atexit.register(_qa.close)
game = _qa.game
import adventure_evidence_rules as rules


class AdventureEvidenceRulesTests(unittest.TestCase):
    def setUp(self):
        self.chapters = [(1, 1), (2, 4), (5, 15)]
        self.stages = {(1, 1): [1, 2, 3], (1, 2): [1, 2, 3],
                       (2, 1): [1, 2], (2, 2): [1, 2],
                       (5, 1): [10, 20], (5, 3): [10, 20]}
        self.passed = {}

    def allowed(self, chapter, mode, stage, level=99):
        return rules.stage_allowed(self.chapters, self.stages,
                                   lambda ch, md: self.passed.get((ch, md), {}),
                                   level, chapter, mode, stage)

    def test_unknown_stage_mode_and_chapter_never_unlock(self):
        for target in ((1, 1, 999), (1, 99, 1), (999, 1, 1), (0, 0, 0)):
            self.assertFalse(self.allowed(*target))

    def test_normal_progress_requires_actual_previous_stages(self):
        self.assertTrue(self.allowed(1, 1, 1))
        self.assertFalse(self.allowed(1, 1, 2))
        self.passed[(1, 1)] = {1: 3, 99: 3}
        self.assertTrue(self.allowed(1, 1, 2))
        self.assertFalse(self.allowed(1, 1, 3))
        self.passed[(1, 1)][2] = 0
        self.assertFalse(self.allowed(1, 1, 3))
        self.passed[(1, 1)][2] = 2
        self.assertTrue(self.allowed(1, 1, 3))

    def test_row_count_cannot_clear_chapter_with_missing_or_zero_star_stage(self):
        self.passed[(1, 1)] = {1: 3, 2: 3, 99: 3}
        self.assertFalse(self.allowed(2, 1, 1))
        self.passed[(1, 1)] = {1: 3, 2: 3, 3: 0}
        self.assertFalse(self.allowed(2, 1, 1))
        self.passed[(1, 1)][3] = 1
        self.assertTrue(self.allowed(2, 1, 1, level=4))
        self.assertFalse(self.allowed(2, 1, 1, level=3))

    def test_elite_progress_does_not_borrow_normal_stage_clears(self):
        self.passed[(1, 1)] = {1: 3, 2: 3, 3: 3}
        self.assertTrue(self.allowed(1, 2, 1))
        self.assertFalse(self.allowed(1, 2, 2))
        self.passed[(1, 2)] = {1: 1}
        self.assertTrue(self.allowed(1, 2, 2))

    def test_configured_alliance_mode_and_nonconsecutive_stage_ids_are_valid(self):
        self.passed[(1, 1)] = {1: 3, 2: 3, 3: 3}
        self.passed[(2, 1)] = {1: 3, 2: 3}
        self.assertTrue(self.allowed(5, 3, 10, level=15))
        self.assertFalse(self.allowed(5, 3, 20))
        self.passed[(5, 3)] = {10: 1}
        self.assertTrue(self.allowed(5, 3, 20))
        self.assertFalse(self.allowed(5, 2, 10))

    def test_configured_legacy_clear_is_replayable_even_when_earlier_save_rows_missing(self):
        self.passed[(1, 1)] = {3: 2}
        self.assertTrue(self.allowed(1, 1, 3))
        self.assertFalse(self.allowed(1, 1, 2))

    def test_normal_only_wiki_does_not_leak_into_elite_or_alliance(self):
        roster = [(111, 3, 2970, 330)]
        levels = {1: {1: roster}}
        self.assertIs(rules.normal_wiki_level(levels, 1, 1, 1), roster)
        self.assertIsNone(rules.normal_wiki_level(levels, 1, 2, 1))
        self.assertIsNone(rules.normal_wiki_level(levels, 1, 3, 1))
        self.assertIsNone(rules.normal_wiki_level(levels, 2, 1, 1))

    def test_normal_seed_stays_compatible_other_modes_are_isolated(self):
        self.assertEqual(rules.fallback_seed_parts(2, 1, 7), ('fallback', 2, 7))
        self.assertNotEqual(rules.fallback_seed_parts(2, 1, 7),
                            rules.fallback_seed_parts(2, 2, 7))
        self.assertNotEqual(rules.fallback_seed_parts(2, 2, 7),
                            rules.fallback_seed_parts(2, 3, 7))


class AdventureRequestRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_adventure_rules_')
        self.addCleanup(self.tmp.cleanup)
        previous_db = game.store.DB_PATH
        self.addCleanup(lambda: game.store.set_db_path(previous_db))
        self.addCleanup(game.store.close_thread_db)
        game.store.set_db_path(str(Path(self.tmp.name) / 'test.db'))
        game.store.init_db()
        self.player, _new = game.store.get_or_create_player(700001010)
        game.store.save_player(self.player['uin'], level=60, energy=80, coin=2000)
        self.player.update(level=60, energy=80, coin=2000)
        self.mocks = ExitStack()
        self.addCleanup(self.mocks.close)
        self.mocks.enter_context(patch.object(game, 'load_fuben_chapters',
                                             return_value=[(1, 1), (2, 4)]))
        self.configured = {(1, 1): [1, 2, 3], (1, 2): [1, 2, 3],
                           (2, 1): [1, 2], (2, 2): [1, 2]}
        self.mocks.enter_context(patch.object(game, 'load_fuben_stages',
                                             return_value=self.configured))
        game._PENDING_PREVIEW.pop(self.player['uin'], None)

    @staticmethod
    def target(chapter, mode, stage):
        return game.pb_bytes(1, game.pb_varint_field(1, 1)
                             + game.pb_varint_field(2, chapter)
                             + game.pb_varint_field(3, mode)
                             + game.pb_varint_field(4, stage))

    def test_unconfigured_and_locked_fights_leave_save_and_economy_untouched(self):
        before = game.store.get_or_create_player(self.player['uin'])[0]
        for chapter, mode, stage in ((1, 1, 999), (1, 1, 3), (2, 1, 1),
                                    (1, 77, 1), (999, 1, 1), (0, 0, 0)):
            with self.subTest(target=(chapter, mode, stage)), \
                    patch.object(game, 'consume_energy') as consume, \
                    patch.object(game, 'grant_fuben_reward') as grant, \
                    patch.object(game, 'run_battle') as battle:
                messages = game.handle_battle_fight(self.target(chapter, mode, stage), self.player)
                consume.assert_not_called()
                grant.assert_not_called()
                battle.assert_not_called()
                self.assertNotIn('PVZ.Cmd.Cmd_Battle_Fight_SC', dict(messages))
                self.assertEqual(before, game.store.get_or_create_player(self.player['uin'])[0])
                self.assertFalse(game.store.get_fuben_passed(self.player['uin'], chapter, mode))

    def test_invalid_saved_sweep_stage_cannot_charge_or_grant_rewards(self):
        game.store.pass_fuben_stage(self.player['uin'], 1, 1, 999, 3)
        command = (game.pb_varint_field(1, 1) + game.pb_varint_field(2, 1)
                   + game.pb_varint_field(3, 999) + game.pb_varint_field(4, 1))
        with patch.object(game, 'consume_energy') as consume, \
                patch.object(game, 'grant_fuben_reward') as grant:
            messages = game.handle_fuben_sweep(command, self.player)
            self.assertNotIn('PVZ.Cmd.Cmd_Fuben_Sweep_SC', dict(messages))
            consume.assert_not_called()
            grant.assert_not_called()

    def test_invalid_saved_stars_cannot_open_or_claim_chapter_reward_box(self):
        uin = self.player['uin']
        game.store.pass_fuben_stage(uin, 1, 1, 1, 3)
        game.store.pass_fuben_stage(uin, 1, 1, 999, 3)
        command = game.pb_varint_field(1, 1) + game.pb_varint_field(2, 1)
        with patch.object(game, 'load_fuben_bonus', return_value={(1, 1): (6, 4, 100)}), \
                patch.object(game, 'grant_fuben_reward') as grant:
            self.assertEqual(game.handle_fuben_requestrewardbox(command, self.player), [])
            grant.assert_not_called()
        self.assertFalse(game.store.is_fuben_box_taken(uin, 1, 1))

    def test_invalid_save_rows_neither_open_next_chapter_nor_inflate_chapter_dto(self):
        uin = self.player['uin']
        for stage, stars in ((1, 2), (98, 3), (99, 3)):
            game.store.pass_fuben_stage(uin, 1, 1, stage, stars)
        body = game.handle_fuben_getchapterinfo(b'', self.player)
        chapters = [game.parse_varint_fields(raw)
                    for raw in game.parse_repeated_submessages(body, 1)]
        self.assertEqual([row[1] for row in chapters], [1])
        self.assertEqual(chapters[0][2], 2)
        self.assertEqual(chapters[0][3], 1)
        self.assertFalse(game._fuben_chapter_cleared(uin, 1))
        stage_body = game.handle_fuben_getstageinfo(
            game.pb_varint_field(1, 1) + game.pb_varint_field(2, 1), self.player)
        rows = [game.parse_varint_fields(raw)
                for raw in game.parse_repeated_submessages(stage_body, 1)]
        self.assertTrue(all(row[3] in self.configured[(1, 1)] for row in rows))

    def test_elite_preview_matches_fight_while_normal_wiki_stays_normal_only(self):
        game.load_battle_cards()
        self.assertTrue(game._build_wiki_enemies(1, 1, 1))
        self.assertIsNone(game._build_wiki_enemies(1, 1, 2))
        for stage in (1, 2, 3):
            game.store.pass_fuben_stage(self.player['uin'], 1, 1, stage, 3)
        target = self.target(1, 2, 1)
        captured = []

        def simulate(left, right, **kwargs):
            captured.extend(dict(unit) for unit in right)
            return False, 0, sum(unit['power'] for unit in right), []

        with ExitStack() as stack:
            for name, value in (('_fuben_formation_entries', ({}, {})),
                                ('build_formation_info_fuben', b''),
                                ('build_player_units', []),
                                ('_auto_puppets', []), ('_auto_puppet_list', b''),
                                ('build_fight_report', b''), ('encode_fight_replay', b'')):
                stack.enter_context(patch.object(game, name, return_value=value))
            preview_body = game.handle_battle_getfightinfo(target, self.player)
            stack.enter_context(patch.object(game, 'run_battle', side_effect=simulate))
            fight = game.handle_battle_fight(target, self.player)
        preview = [game.parse_varint_fields(raw)
                   for raw in game.parse_repeated_submessages(preview_body, 4)]
        self.assertTrue(preview)
        self.assertIn('PVZ.Cmd.Cmd_Battle_Fight_SC', dict(fight))
        self.assertEqual([(row[1], row[2]) for row in preview],
                         [(unit['pos'], unit['card_id']) for unit in captured])
        self.assertEqual(game.parse_varint_fields(preview_body)[5],
                         sum(unit['power'] for unit in captured))

    def test_space_chapter_uses_future_pool_and_next_chapter_uses_sea_pool(self):
        self.assertEqual(game.chapter_enemy_cards(16), game._WORLD_ZOMBIES['future'])
        self.assertEqual(game.chapter_enemy_cards(17), game._WORLD_ZOMBIES['sea'])

    def test_mode_specific_preview_ids_do_not_enter_adventure_gate(self):
        targets = [
            game.pb_varint_field(1, 22) + game.pb_varint_field(18, 1)
            + game.pb_varint_field(19, 1) + game.pb_varint_field(20, 1)
            + game.pb_varint_field(21, 1),
            game.pb_varint_field(1, 26) + game.pb_varint_field(22, 1)
            + game.pb_varint_field(23, 1),
            game.pb_varint_field(1, 9), game.pb_varint_field(1, 21),
            game.pb_varint_field(1, 11) + game.pb_varint_field(5, 1),
            game.pb_varint_field(1, 20) + game.pb_varint_field(17, 1),
        ]
        with ExitStack() as stack:
            gate = stack.enter_context(patch.object(game, '_fuben_stage_allowed',
                                                     side_effect=AssertionError('wrong target family')))
            preview = stack.enter_context(patch.object(game, 'preview_enemy_units', return_value=[]))
            for name, value in (('build_formation_info', b''),
                                ('_puppet_fuben_placements', {}), ('puppet_lineup', []),
                                ('_noend_fuben_placements', {}), ('_noend_fuben_leader_uid', 0),
                                ('_zi_player_placements', {}), ('default_formation_placements', {})):
                stack.enter_context(patch.object(game, name, return_value=value))
            for target in targets:
                with self.subTest(type=game.parse_varint_fields(target)[1]):
                    body = game.handle_battle_getfightinfo(game.pb_bytes(1, target), self.player)
                    self.assertIsInstance(body, bytes)
            gate.assert_not_called()
            self.assertEqual(preview.call_count, len(targets))


if __name__ == '__main__':
    unittest.main()
