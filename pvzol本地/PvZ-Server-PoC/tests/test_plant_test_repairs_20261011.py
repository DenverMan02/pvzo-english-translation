"""Awakening gates and plant-test lifecycle against disposable player databases."""
import copy
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import player_store as store
BOOT = tempfile.TemporaryDirectory(prefix='pvz_plant_test_boot_')
store.set_db_path(str(Path(BOOT.name) / 'players.db'))
store.init_db()
import pvz_poc_server as g
import plant_test_repairs_20261011 as repairs
repairs.install(g)
logging.getLogger('pvz').setLevel(logging.WARNING)


class PlantTestRepairsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_plant_test_')
        old = store.DB_PATH
        store.set_db_path(str(Path(self.tmp.name) / 'players.db'))
        store.init_db()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: store.set_db_path(old))
        self.player, _ = store.get_or_create_player(88011191)
        self.uid = 123456
        store.add_card(self.player['uin'], self.uid, 1205, 100)
        store.set_formation(self.player['uin'], 1, {3: self.uid})

    def progress(self, **changes):
        data = g._plant_test_state(self.player)
        data['stages']['1'].update(changes)
        g._save_json_state(self.player, 'plant_test_data', data)
        return data

    def restart(self):
        return g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_PlantTest_RestartStage_CS'](
            g.pb_varint_field(1, 1), self.player)

    def fight(self, sub, won):
        captured = {}
        def battle(left, right, **kwargs):
            captured['right'] = copy.deepcopy(right)
            return won, sum(unit['power'] for unit in left), sum(unit['power'] for unit in right), []
        with patch.object(g, 'run_battle', side_effect=battle):
            messages = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_PlantTest_Fight_CS'](
                g.pb_varint_field(1, 1) + g.pb_varint_field(2, sub), self.player)
        return messages, captured

    def source(self, uin=88011192, bot=False):
        owner, _ = store.get_or_create_player(900011 if bot else uin)
        owner['name'] = '原始阵容玩家' if not bot else '机器人玩家'
        store.save_player(owner['uin'], name=owner['name'])
        placements = {}
        for pos, cid in zip((3, 7, 9), (1205, 1305, 1505)):
            uid = owner['uin'] * 100 + pos
            store.add_card(owner['uin'], uid, cid, 77)
            placements[pos] = uid
        store.set_formation(owner['uin'], 1, placements)
        store.set_formation_leader(owner['uin'], 1, placements[7])
        return owner, placements

    def test_three_attempts_in_hud_and_three_losses_exhaust_group(self):
        self.assertEqual(g.SERVER_CONFIG_OVERRIDES['iPlantTestDailyChallengeTimes'], '3')
        info = g.handle_plant_test_getstageinfo(g.pb_varint_field(1, 1), self.player)
        self.assertEqual(g.parse_varint_fields(info)[4], 3)
        for remain in (2, 1, 0):
            messages, _ = self.fight(1, False)
            self.assertEqual(messages[0][0], 'PVZ.Cmd.Cmd_PlantTest_Fight_SC')
            self.assertEqual(g._plant_test_state(self.player)['stages']['1']['remain'], remain)
        messages, captured = self.fight(1, True)
        self.assertEqual(messages[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertFalse(captured)

    def test_existing_one_attempt_state_migrates_without_erasing_spent_loss(self):
        data = g._plant_test_state(self.player)
        data.pop('rules_version')
        data['stages']['1']['remain'] = 0
        data['stages']['2']['remain'] = 1
        g._save_json_state(self.player, 'plant_test_data', data)
        repaired = g._plant_test_state(self.player)
        self.assertEqual(repaired['stages']['1']['remain'], 2)
        self.assertEqual(repaired['stages']['2']['remain'], 3)
        self.assertEqual(g._plant_test_state(self.player)['stages']['1']['remain'], 2)

    def test_failed_reset_returns_to_first_chapter_and_keeps_weekly_best(self):
        self.progress(current=4, best=7, remain=2, rewards={'2111': 12})
        messages = self.restart()
        self.assertEqual(messages[0][0], 'PVZ.Cmd.Cmd_PlantTest_RestartStage_SC')
        current = g._plant_test_state(self.player)['stages']['1']
        self.assertEqual((current['current'], current['best'], current['remain']), (0, 7, 3))
        self.assertEqual(current['rewards'], {})
        stage = dict(messages)['PVZ.Cmd.Cmd_PlantTest_GetStageInfo_SC']
        self.assertEqual(g.parse_varint_fields(stage)[2], 0)
        self.assertEqual(g.parse_varint_fields(stage)[4], 3)

    def test_completed_group_can_reset_with_full_attempts(self):
        self.progress(current=30, best=30, remain=3, completed=True)
        self.assertEqual(self.restart()[0][0], 'PVZ.Cmd.Cmd_PlantTest_RestartStage_SC')
        current = g._plant_test_state(self.player)['stages']['1']
        self.assertEqual((current['current'], current['best'], current['remain']), (0, 30, 3))
        self.assertTrue(current['completed'])

    def test_reset_untouched_group_is_rejected_without_spending_free_reset(self):
        self.assertEqual(self.restart()[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertEqual(g._plant_test_state(self.player)['resets'], 0)

    def test_paid_reset_debits_gold_diamonds_and_keeps_gift_diamonds(self):
        data = self.progress(current=5, best=8, remain=0)
        data['resets'] = 1
        g._save_json_state(self.player, 'plant_test_data', data)
        store.save_player(self.player['uin'], ticket=150, present_ticket=500)
        self.player.update(ticket=150, present_ticket=500)
        messages = self.restart()
        self.assertIn('PVZ.Cmd.Cmd_SyncTicket_SC', dict(messages))
        self.assertNotIn('PVZ.Cmd.Cmd_SyncPresentTicket_SC', dict(messages))
        self.assertEqual((self.player['ticket'], self.player['present_ticket']), (50, 500))
        with store._db() as con:
            saved = con.execute('SELECT ticket, present_ticket FROM players WHERE uin=?',
                                (self.player['uin'],)).fetchone()
        self.assertEqual(tuple(saved), (50, 500))
        self.assertEqual(g._plant_test_state(self.player)['resets'], 2)

    def test_insufficient_gold_changes_neither_progress_nor_gift_balance(self):
        data = self.progress(current=9, best=12, remain=0, rewards={'2111': 15})
        data['resets'] = 1
        g._save_json_state(self.player, 'plant_test_data', data)
        store.save_player(self.player['uin'], ticket=99, present_ticket=500)
        self.player.update(ticket=99, present_ticket=500)
        before = g._plant_test_state(self.player)
        self.assertEqual(self.restart()[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertEqual(g._plant_test_state(self.player), before)
        self.assertEqual(self.player['present_ticket'], 500)

    def test_sweep_all_recorded_stages_awards_once_and_preserves_restart_progress(self):
        self.progress(current=30, best=30, remain=3, completed=True)
        self.restart()
        before = store.get_item_count(self.player['uin'], 2111)
        with patch.object(g, '_plant_test_roll', return_value=(2111, 2)):
            messages = g.handle_plant_test_sweep(g.pb_varint_field(1, 1), self.player)
        self.assertEqual(messages[0][0], 'PVZ.Cmd.Cmd_PlantTest_Sweep_SC')
        self.assertEqual(store.get_item_count(self.player['uin'], 2111), before + 60)
        self.assertEqual(g._plant_test_state(self.player)['stages']['1']['current'], 30)
        self.assertEqual(g.handle_plant_test_sweep(g.pb_varint_field(1, 1), self.player)[0][0],
                         'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertEqual(store.get_item_count(self.player['uin'], 2111), before + 60)

    def test_copies_other_player_over_bot_and_preserves_leader_skills_stats(self):
        owner, placements = self.source()
        self.source(bot=True)
        expected = g.build_player_units(owner, placements)
        for unit in expected:
            unit['pos'] += 1
        actual = g._plant_test_enemies(self.player, 1, 1)
        self.assertEqual(actual, expected)
        data = g._plant_test_state(self.player)
        self.assertEqual(data['opponents']['1']['1']['source'], 'player')
        self.assertEqual(data['opponents']['1']['1']['name'], owner['name'])

    def test_bot_saved_formation_is_fallback_when_no_other_players(self):
        owner, placements = self.source(bot=True)
        expected = g.build_player_units(owner, placements)
        for unit in expected:
            unit['pos'] += 1
        self.assertEqual(g._plant_test_enemies(self.player, 1, 1), expected)
        self.assertEqual(g._plant_test_state(self.player)['opponents']['1']['1']['source'], 'bot')

    def test_empty_world_generates_real_unique_bot_plants_with_actual_power(self):
        units = g._plant_test_enemies(self.player, 1, 1)
        self.assertEqual(len(units), 6)
        self.assertEqual(len({g._BATTLE_CARDS[unit['card_id']]['tdId'] for unit in units}), 6)
        self.assertTrue(all(1 <= unit['level'] <= 300 for unit in units))
        self.assertTrue(all(unit['power'] == g.card_power(unit) for unit in units))

    def test_tooltip_preview_and_fight_use_same_immutable_snapshot_across_relogin(self):
        owner, placements = self.source()
        stage = g.handle_plant_test_getstageinfo(g.pb_varint_field(1, 1), self.player)
        shown = g.parse_varint_fields(g.parse_repeated_submessages(stage, 6)[0])
        snapshot = g._plant_test_enemies(self.player, 1, 1)
        # Source growth after the chapter list appears must not change this fight.
        store.set_card_level(owner['uin'], next(iter(placements.values())), 120, 0)
        loaded = store.get_player_by_role_id(self.player['role_id'])
        self.assertEqual(g._plant_test_enemies(loaded, 1, 1), snapshot)
        target = g.pb_varint_field(1, 16) + g.pb_varint_field(12, 1) + g.pb_varint_field(13, 1)
        preview = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Battle_GetFightInfo_CS'](g.pb_bytes(1, target), self.player)
        self.assertEqual(g.parse_varint_fields(preview)[5], shown[3])
        self.assertEqual(shown[4], 1)
        self.assertEqual(shown[3], sum(unit['power'] for unit in snapshot))
        messages, captured = self.fight(1, True)
        self.assertEqual(captured['right'], snapshot)
        self.assertEqual(messages[0][0], 'PVZ.Cmd.Cmd_PlantTest_Fight_SC')

    def test_rare_awakening_rejects_99_then_accepts_100_with_atomic_cost(self):
        # This high-rarity family's authoritative client table requires 100.
        cid = 1255
        rule = g.load_awake_rules()[cid]
        self.assertEqual(rule['card_level'], 100)
        uid = self.uid + 1
        store.add_card(self.player['uin'], uid, cid, 99)
        store.set_card_forge_level(self.player['uin'], uid, 5)
        for iid, count in rule['cost']:
            store.add_item(self.player['uin'], iid, count)
        before = {iid: store.get_item_count(self.player['uin'], iid) for iid, _ in rule['cost']}
        self.assertEqual(g.handle_card_usecardawaken(g.pb_int64_field(1, uid), self.player)[0][0],
                         'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertEqual({iid: store.get_item_count(self.player['uin'], iid) for iid in before}, before)
        store.set_card_level(self.player['uin'], uid, 100, 0)
        messages = g.handle_card_usecardawaken(g.pb_int64_field(1, uid), self.player)
        self.assertIn('PVZ.Cmd.Cmd_Card_UseCardAwaken_SC', dict(messages))
        self.assertEqual(next(card for card in store.get_cards(self.player['uin']) if card[0] == uid)[1], rule['awake_id'])
        self.assertEqual(store.get_card_forge_level(self.player['uin'], uid), 5)


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        store.close_thread_db()
        BOOT.cleanup()
