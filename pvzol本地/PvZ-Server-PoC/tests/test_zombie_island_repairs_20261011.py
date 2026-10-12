"""Real island protocol/persistence regression with a disposable database."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store

_scratch = tempfile.TemporaryDirectory(prefix='pvz-island-progress-')
store.set_db_path(str(Path(_scratch.name) / 'players.db'))
store.init_db()
import pvz_poc_server as game
import zombie_island_repairs_20261011 as repair
repair.install(game)


class ZombieIslandProgressTests(unittest.TestCase):
    counter = 0

    @classmethod
    def tearDownClass(cls):
        store.close_thread_db()
        _scratch.cleanup()

    def setUp(self):
        type(self).counter += 1
        self.player, _ = store.get_or_create_player(98761100 + type(self).counter)
        self.uin = self.player['uin']
        self.player['level'] = 200
        store.save_player(self.uin, level=200)

    def seed(self, mode=0, level=1, maximum=7, used=0, role_level=200):
        store.save_island_progress(self.uin, mode, level=level, max=maximum,
                                   refresh_used=used, day=game._zi_today(),
                                   last_role_level=role_level)

    def next_level(self, messages, advanced=False):
        name = ('PVZ.Cmd.Cmd_NewZombieIsland_GetIslandInfo_SC' if advanced
                else 'PVZ.Cmd.Cmd_ZombieIsland_GetIslandInfo_SC')
        body = next(body for route, body in messages if route == name)
        return game.parse_varint_fields(body)[2]

    def sweep_rows(self, messages):
        body = next(body for name, body in messages
                    if name == 'PVZ.Cmd.Cmd_ZombieIsland_Sweep_SC')
        return game.parse_repeated_submessages(body, 1)

    def test_restart_pushes_current_stage_and_reset_boxes_without_reopening(self):
        self.seed(level=5)
        self.seed(mode=1, level=4, maximum=3)
        store.take_island_box(self.uin, 0, 1, 1)
        store.take_island_box(self.uin, 1, 1, 1)
        reply = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_ZombieIsland_RefreshChallenge_CS'](
            b'', self.player)
        self.assertEqual(reply[0][0], 'PVZ.Cmd.Cmd_ZombieIsland_GetRefreshInfo_SC')
        self.assertNotIn('PVZ.Cmd.Cmd_ZombieIsland_RefreshChallenge_SC',
                         [name for name, _ in reply])
        self.assertEqual(game.parse_varint_fields(reply[0][1]), {1: 1, 2: 0})
        self.assertEqual(self.next_level(reply), 1)
        info = next(body for name, body in reply
                    if name == 'PVZ.Cmd.Cmd_ZombieIsland_GetIslandInfo_SC')
        boxes = game.parse_repeated_submessages(info, 1)
        self.assertEqual(len(boxes), game.zi_island_count() * game._ZI_BOXES)
        self.assertTrue(all(game.parse_varint_fields(box)[3] == 0 for box in boxes))
        self.assertEqual(store.island_progress(self.uin, 0)[:3], (1, 7, 1))
        self.assertEqual(store.island_progress(self.uin, 1)[:3], (4, 3, 0))
        self.assertEqual(store.island_boxes(self.uin, 1), {(1, 1)})
        store.close_thread_db()
        self.assertEqual(game.parse_varint_fields(game.handle_zi_getinfo(b'', self.player))[2], 1)

    def test_refused_second_restart_still_pushes_the_actual_progress(self):
        self.seed(level=4, used=1)
        store.take_island_box(self.uin, 0, 1, 1)
        reply = game.handle_zi_refreshchallenge(b'', self.player)
        self.assertEqual(self.next_level(reply), 4)
        self.assertEqual(store.island_boxes(self.uin, 0), {(1, 1)})

    def test_advanced_restart_keeps_its_native_info_reply(self):
        self.seed(level=5)
        self.seed(mode=1, level=4, maximum=3)
        reply = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_NewZombieIsland_RefreshChallenge_CS'](
            b'', self.player)
        self.assertEqual(reply[0][0], 'PVZ.Cmd.Cmd_NewZombieIsland_GetIslandInfo_SC')
        self.assertEqual(self.next_level(reply, advanced=True), 1)
        self.assertEqual(store.island_progress(self.uin, 0)[:3], (5, 7, 0))

    def test_sweep_advances_to_recorded_next_island_and_rewards_once(self):
        self.seed()
        self.seed(mode=1, level=4, maximum=3)
        before = store.get_item_count(self.uin, game._ZI_MEDAL_ITEM)
        reply = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_ZombieIsland_Sweep_CS'](b'', self.player)
        rows = self.sweep_rows(reply)
        self.assertEqual([game.parse_varint_fields(row)[1] for row in rows], list(range(1, 8)))
        self.assertEqual(store.get_item_count(self.uin, game._ZI_MEDAL_ITEM) - before, 56)
        self.assertEqual(store.island_progress(self.uin, 0)[:2], (8, 7))
        self.assertEqual(self.next_level(reply), 8)
        self.assertEqual(store.island_progress(self.uin, 1)[:2], (4, 3))
        # A second cached player cannot bypass the persisted daily claim marker.
        stale_player = dict(self.player, zi_sweep_day='')
        self.assertEqual(self.sweep_rows(game.handle_zi_sweep(b'', stale_player)), [])
        self.assertEqual(store.get_item_count(self.uin, game._ZI_MEDAL_ITEM) - before, 56)
        store.close_thread_db()
        self.assertEqual(game.parse_varint_fields(game.handle_zi_getinfo(b'', self.player))[2], 8)

    def test_empty_or_ineligible_sweeps_do_not_consume_daily_opportunity(self):
        for level, maximum, role_level in ((1, 0, 200), (3, 7, 200), (1, 7, 199)):
            with self.subTest(level=level, maximum=maximum, role_level=role_level):
                self.seed(level=level, maximum=maximum, role_level=role_level)
                before = store.get_item_count(self.uin, game._ZI_MEDAL_ITEM)
                reply = game.handle_zi_sweep(b'', self.player)
                self.assertEqual(self.sweep_rows(reply), [])
                self.assertEqual(self.next_level(reply), level)
                self.assertEqual(store.get_item_count(self.uin, game._ZI_MEDAL_ITEM), before)
                self.assertEqual(store.get_or_create_player(self.uin)[0]['zi_sweep_day'], '')
        self.seed()
        self.assertEqual(len(self.sweep_rows(game.handle_zi_sweep(b'', self.player))), 7)

    def test_sweep_failure_rolls_back_reward_progress_and_daily_marker(self):
        self.seed()
        before = store.get_item_count(self.uin, game._ZI_MEDAL_ITEM)

        def fail_grant(player, rewards):
            store.add_item(player['uin'], game._ZI_MEDAL_ITEM, 999)
            raise RuntimeError('simulated reward failure')

        with patch.object(game, 'grant_fuben_reward', side_effect=fail_grant):
            with self.assertRaisesRegex(RuntimeError, 'simulated reward failure'):
                game.handle_zi_sweep(b'', self.player)
        self.assertEqual(store.island_progress(self.uin, 0)[:2], (1, 7))
        self.assertEqual(store.get_or_create_player(self.uin)[0]['zi_sweep_day'], '')
        self.assertEqual(self.player['zi_sweep_day'], '')
        self.assertEqual(store.get_item_count(self.uin, game._ZI_MEDAL_ITEM), before)

    def test_final_island_sweep_marks_all_twelve_cleared(self):
        self.seed(maximum=12)
        reply = game.handle_zi_sweep(b'', self.player)
        self.assertEqual(len(self.sweep_rows(reply)), 12)
        self.assertEqual(self.next_level(reply), 13)
        self.assertEqual(game._zi_state(self.player, 0)['level'], 13)
        # This is the compiled model's cleared count and box-opening predicate.
        self.assertEqual(self.next_level(reply) - 1, 12)
        self.assertTrue(12 < self.next_level(reply))

    def test_final_island_win_persists_completed_sentinel_for_both_modes(self):
        game.ensure_player_cards(self.player)
        for mode in (0, 1):
            with self.subTest(mode=mode):
                self.seed(mode=mode, level=12, maximum=11)
                with patch.object(game, 'run_battle', return_value=(True, 0, 0, [])):
                    reply = game.handle_zi_fight(game.pb_varint_field(1, 12), self.player, mode)
                self.assertEqual(store.island_progress(self.uin, mode)[:2], (13, 12))
                self.assertEqual(self.next_level(reply, advanced=mode == 1), 13)
                store.close_thread_db()
                self.assertEqual(game._zi_state(self.player, mode)['level'], 13)
                self.assertTrue(12 < self.next_level(reply, advanced=mode == 1))
                status, _rewards = game._zi_claim_box(self.player, mode, 12, 1)
                self.assertEqual(status, 1)

    def test_completed_ladder_has_no_next_preview_or_repeat_fight_reward(self):
        for mode in (0, 1):
            with self.subTest(mode=mode):
                self.seed(mode=mode, level=13, maximum=12)
                target = game.pb_bytes(1, game.pb_varint_field(1, game._zi_target_type(mode)))
                preview = game.handle_battle_getfightinfo(target, self.player)
                self.assertEqual(game.parse_repeated_submessages(preview, 4), [])
                before = store.get_item_count(self.uin, game._ZI_MEDAL_ITEM)
                with patch.object(game, 'run_battle', side_effect=AssertionError('no fight expected')):
                    replies = [game.handle_battle_fight(target, self.player),
                               game.handle_zi_fight(b'', self.player, mode),
                               game.handle_zi_fight(game.pb_varint_field(1, 12), self.player, mode)]
                for reply in replies:
                    self.assertEqual(self.next_level(reply, advanced=mode == 1), 13)
                    self.assertFalse(any(name.endswith('_Fight_SC') for name, _ in reply))
                self.assertEqual(store.get_item_count(self.uin, game._ZI_MEDAL_ITEM), before)


if __name__ == '__main__':
    unittest.main()
