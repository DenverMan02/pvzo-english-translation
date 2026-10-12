"""Original-table NoEndFuben regressions, using only a disposable player DB."""
from pathlib import Path
import copy
import json
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = Path(__file__).resolve().parent
SERVER = HERE.parent if (HERE.parent / 'pvz_poc_server.py').exists() else Path(r'D:\pvzol本地最新最新ol\pvzol本地\PvZ-Server-PoC')
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(HERE))
import player_store as store

DB = tempfile.TemporaryDirectory(prefix='pvz-noend-rules-')
store.set_db_path(str(Path(DB.name) / 'test.db'))
store.init_db()
g = types.ModuleType('pvz_noend_rules_under_test')
g.__file__ = str(SERVER / 'pvz_poc_server.py')
sys.modules[g.__name__] = g
source = Path(g.__file__).read_text(encoding='utf-8')
if '--preview-combat' in sys.argv:
    sys.argv.remove('--preview-combat')
    from patch_endless_combat_flags_20261009 import patched_source
    source = patched_source(source)
exec(compile(source, g.__file__, 'exec'), g.__dict__)
import missing_features_local as mf
import endless_rules_repairs_20261009 as repair
repair.install(g)
g.load_battle_cards()
g.load_skill_info()


def unit(pos, **changes):
    u = dict(pos=pos, card_id=101, hp=1000, initial_hp=100, atk=10, def_=0,
             ability=0, skill1=51, skill2=0, is_leader=False, level=1, power=100,
             uid=999000 + pos, is_live=True)
    u.update(changes)
    return u


class EndlessRulesTests(unittest.TestCase):
    seq = 0

    def setUp(self):
        type(self).seq += 1
        self.player, _ = store.get_or_create_player(91203000 + type(self).seq)
        self.uin = self.player['uin']
        self.player.update(level=100, coin=100000000, ticket=1000)
        store.save_player(self.uin, level=100, coin=100000000, ticket=1000)
        store.add_card(self.uin, 9001, 101, 100)
        store.add_card(self.uin, 9002, 101, 100)
        self.call('Info')
        self.call('SetPreCard', g.pb_int64_field(1, 9001) + g.pb_int64_field(1, 9002))

    def call(self, action, data=b''):
        return g.REQUEST_HANDLERS[repair.PREFIX + action + '_CS'](data, self.player)

    def body(self, action, value):
        return dict(value)[repair.PREFIX + action + '_SC'] if isinstance(value, list) else value

    def row(self):
        with mf._db() as con:
            return mf._noend_row(con, self.uin)

    def change(self, **changes):
        with mf._db() as con:
            con.execute('UPDATE local_noend_fuben SET ' + ','.join(k + '=?' for k in changes) + ' WHERE uin=?',
                        tuple(changes.values()) + (self.uin,))

    def gift(self, body, field):
        return [(mf._wire0(dto, 1), mf._wire0(dto, 2)) for dto in mf._repeated_messages(body, field)]

    def test_original_difficulty_rules_and_chest_ids(self):
        for mode in range(1, 8):
            with self.subTest(mode=mode):
                rules = repair.mode_rules(g, mode)
                self.assertEqual((rules['healing'], rules['puppets']),
                                 (mode >= 2, mode >= 3))
                self.assertEqual(rules['pass_reward'], [(4107 + mode, 1)])

    def test_sweep_uses_history_minus_twenty_and_advances_stored_cursor(self):
        self.change(history_max_stage_id=256, best_stage_id=100)
        before = self.player['coin']
        result = self.body('Sweep', self.call('Sweep'))
        self.assertEqual(mf._wire0(result, 1), 236)
        self.assertEqual(self.gift(result, 2), [(4108, 236)])
        row = self.row()
        self.assertEqual((row['stage_id'], row['reward_stage_id'], row['last_sweep_stage']), (236, 236, 236))
        self.assertEqual(row['history_max_stage_id'], 256)
        self.assertEqual(store.get_item_count(self.uin, 4108), 236)
        self.assertEqual(self.player['coin'], before)
        second = self.body('Sweep', self.call('Sweep'))
        self.assertEqual(mf._wire0(second, 1), 236)
        self.assertEqual(self.gift(second, 2), [])
        self.assertEqual(store.get_item_count(self.uin, 4108), 236)

    def test_sweep_threshold_and_no_backward_progress(self):
        for history in (0, 1, 19, 20):
            self.change(history_max_stage_id=history)
            self.assertEqual(mf._wire0(self.body('Sweep', self.call('Sweep')), 1), 0)
        self.change(history_max_stage_id=256, stage_id=250, best_stage_id=250)
        self.assertEqual(mf._wire0(self.body('Sweep', self.call('Sweep')), 1), 250)
        self.assertEqual(self.row()['stage_id'], 250)

    def test_old_inflated_sweep_marker_does_not_block_correct_new_run(self):
        self.change(history_max_stage_id=256, last_sweep_stage=256, reward_stage_id=256)
        self.assertEqual(mf._wire0(self.body('Sweep', self.call('Sweep')), 1), 236)
        self.assertEqual(self.row()['stage_id'], 236)
        self.assertEqual(store.get_item_count(self.uin, 4108), 0)

    def test_sweep_failed_reward_rolls_back_progress_and_inventory(self):
        self.change(history_max_stage_id=256)
        before = dict(self.row())
        original = g.grant_fuben_reward
        def failed(player, items):
            original(player, items)
            raise RuntimeError('simulated reward failure')
        with patch.object(g, 'grant_fuben_reward', failed), self.assertRaises(RuntimeError):
            self.call('Sweep')
        self.assertEqual(dict(self.row()), before)
        self.assertEqual(store.get_item_count(self.uin, 4108), 0)

    def test_info_and_claim_use_each_modes_real_rank_chests(self):
        for mode, item_id in ((1,4091),(2,4091),(3,4092),(4,4092),(5,4093),(6,4093),(7,4094)):
            with self.subTest(mode=mode):
                self.change(mode_id=mode, best_stage_id=1, has_take_reward=0)
                info = self.call('Info')
                prizes = self.gift(info, 10)
                self.assertEqual(prizes[0][0], item_id)
                expected = dict(prizes)[item_id]
                before = store.get_item_count(self.uin, item_id)
                result = self.body('TakeReward', self.call('TakeReward'))
                self.assertEqual(self.gift(result, 1), prizes)
                self.assertEqual(store.get_item_count(self.uin, item_id), before + expected)
                self.assertEqual(self.row()['has_take_reward'], 1)
                self.assertEqual(self.body('TakeReward', self.call('TakeReward')), b'')
                self.assertEqual(store.get_item_count(self.uin, item_id), before + expected)

    def test_no_clear_has_no_rank_prize(self):
        self.assertEqual(self.gift(self.call('Info'), 10), [])
        self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(self.call('TakeReward')))
        self.assertEqual(self.row()['has_take_reward'], 0)

    def test_failed_rank_claim_is_retryable(self):
        self.change(best_stage_id=1)
        with patch.object(g, 'grant_fuben_reward', side_effect=RuntimeError('failure')), self.assertRaises(RuntimeError):
            self.call('TakeReward')
        self.assertEqual(self.row()['has_take_reward'], 0)
        self.assertTrue(self.gift(self.body('TakeReward', self.call('TakeReward')), 1))

    def test_dead_plants_removed_from_formation_and_preview_but_kept_in_roster(self):
        self.change(hp_ratios=json.dumps({'9001': .5, '9002': 0}))
        formation = mf._wire2(dict(self.call('GetFormation'))[repair.PREFIX + 'SyncFormation_SC'], 1)
        placed = [mf._wire0(c, 2) for c in mf._repeated_messages(formation, 1)]
        self.assertEqual(placed, [9001])
        self.assertEqual(g._noend_fuben_placements(self.uin), {3: 9001})
        self.assertEqual(mf._pre_cards(self.row()), [9001, 9002])
        self.assertEqual([uid for uid, _ in mf._noend_cards(self.row())], [9001, 9002])
        target = g.pb_varint_field(1, 26) + g.pb_varint_field(22, 1) + g.pb_varint_field(23, 1)
        preview = g.handle_battle_getfightinfo(g.pb_bytes(1, target), self.player)
        placed = [mf._wire0(c, 2) for c in mf._repeated_messages(mf._wire2(preview, 1), 1)]
        self.assertEqual(placed, [9001])

    def test_all_dead_does_not_fall_back_to_adventure_team(self):
        self.change(hp_ratios=json.dumps({'9001': 0, '9002': 0}))
        self.assertEqual(g._noend_fuben_placements(self.uin), {})
        self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(self.call('Fight', g.pb_varint_field(1, 1))))

    def test_cannot_replace_run_roster_or_redeploy_dead_plant(self):
        self.change(hp_ratios=json.dumps({'9001': .5, '9002': 0}))
        store.add_card(self.uin, 9003, 101, 100)
        request = g.pb_int64_field(1, 9001) + g.pb_int64_field(1, 9003)
        self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(self.call('SetPreCard', request)))
        self.assertEqual(mf._pre_cards(self.row()), [9001, 9002])
        requested = g.pb_bytes(1, g.pb_varint_field(1, 7) + g.pb_int64_field(2, 9002))
        self.call('SetFormation', g.pb_bytes(1, requested))
        self.assertEqual(g._noend_fuben_placements(self.uin), {3: 9001})

    def test_first_clear_grants_correct_loot_once_and_confirm_pushes_live_formation(self):
        left = [unit(3, uid=9001, final_hp=50), unit(7, uid=9002, final_hp=0)]
        report = g.build_fight_report(self.player, 1, 0, 1, True, left_units=left,
                                      right_units=[unit(4)], fight_type=26, puppet_bytes=b'')
        with patch.dict(mf._API, run_fight=lambda *_: [('PVZ.Cmd.Cmd_Battle_Fight_SC', g.pb_bytes(1, report))]):
            first = self.body('Fight', self.call('Fight', g.pb_varint_field(1, 1)))
            self.assertEqual(self.gift(first, 7), [(4108, 1)])
            self.assertEqual(store.get_item_count(self.uin, 4108), 1)
            second = self.body('Fight', self.call('Fight', g.pb_varint_field(1, 1)))
            self.assertEqual(self.gift(second, 7), [])
            self.assertEqual(store.get_item_count(self.uin, 4108), 1)
        confirmed = dict(self.call('ConfirmFight', g.pb_varint_field(1, 1)))
        self.assertIn(repair.PREFIX + 'SyncFormation_SC', confirmed)
        placed = mf._repeated_messages(mf._wire2(confirmed[repair.PREFIX + 'SyncFormation_SC'], 1), 1)
        self.assertEqual([mf._wire0(c, 2) for c in placed], [9001])

    def test_winning_hp_is_saved_only_after_confirm_and_retries_use_starting_hp(self):
        self.change(hp_ratios=json.dumps({'9001': .5, '9002': .8}))
        starts = []
        def battle(*_):
            starts.append(mf._hp_ratios(self.row()))
            report = g.build_fight_report(self.player, 1, 0, 1, True,
                left_units=[unit(3, uid=9001, final_hp=100), unit(7, uid=9002, final_hp=0)],
                right_units=[unit(4)], fight_type=26, puppet_bytes=b'')
            return [('PVZ.Cmd.Cmd_Battle_Fight_SC', g.pb_bytes(1, report))]
        with patch.dict(mf._API, run_fight=battle):
            self.call('Fight', g.pb_varint_field(1, 1))
            self.assertEqual(mf._hp_ratios(self.row()), {9001:.5,9002:.8})
            self.call('Fight', g.pb_varint_field(1, 1))
        self.assertEqual(starts, [{9001:.5,9002:.8}] * 2)
        self.call('ConfirmFight', g.pb_varint_field(1, 99))
        self.assertEqual(mf._hp_ratios(self.row()), {9001:.5,9002:.8})
        self.call('ConfirmFight', g.pb_varint_field(1, 1))
        self.assertEqual(mf._hp_ratios(self.row()), {9001:.1,9002:0})
        self.assertEqual(self.row()['pending_hp_ratios'], '')

    def test_failure_limit_rejects_fight_without_simulation(self):
        self.change(lose_times=5)
        with patch.dict(mf._API, run_fight=lambda *_: self.fail('exhausted run entered combat')):
            self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(self.call('Fight', g.pb_varint_field(1, 1))))

    def test_reset_discards_only_provisional_hp_and_preserves_history(self):
        self.change(history_max_stage_id=256, stage_id=1, has_pass_next_stage=1,
                    hp_ratios=json.dumps({'9001':.5}), pending_hp_ratios=json.dumps({'9001':0}))
        self.call('Reset')
        self.assertEqual(self.row()['pending_hp_ratios'], '')
        self.assertEqual(self.row()['history_max_stage_id'], 256)

    def puppet(self):
        info = g.load_dummy_config()['zombies'][1000001]
        store.save_puppet(self.uin, 1000001, level=100, star=1, quality=100,
                          active_skill=info['active'], active_level=1,
                          passive_skill=info['passive'], passive_level=1)
        g.save_puppet_lineup(self.player, [1000001])

    def test_stage_one_report_and_stats_exclude_deployed_puppets(self):
        plain = g.build_player_units(self.player, {3:9001,7:9002})
        self.puppet()
        self.assertGreater(g.build_player_units(self.player, {3:9001,7:9002})[0]['hp'], plain[0]['hp'])
        with patch.object(g, '_auto_puppets', side_effect=AssertionError('forbidden puppet simulation')), \
                patch.object(g, '_auto_puppet_list', side_effect=AssertionError('forbidden puppet report')), \
                patch.object(g, 'run_battle', wraps=g.run_battle) as run:
            response = g._noend_fuben_run_fight(self.player, 3, 1)  # Forged mode does not change current layer.
        report = mf._wire2(response[0][1], 1)
        self.assertEqual(mf._wire0(report, 5), 26)
        self.assertEqual(mf._repeated_messages(mf._wire2(report, 2), 16), [])
        self.assertEqual(mf._repeated_messages(mf._wire2(report, 3), 16), [])
        self.assertFalse(run.call_args.kwargs['allow_healing'])
        self.assertIsNone(run.call_args.kwargs['puppets'])
        self.assertEqual([u['hp'] for u in run.call_args.args[0]], [u['hp'] for u in plain])
        self.assertEqual(self.player['puppet_lineup'], '1000001')

    def test_stage_two_and_three_follow_original_different_rules(self):
        self.puppet()
        for mode in (2, 3):
            self.change(mode_id=mode)
            with patch.object(g, 'run_battle', wraps=g.run_battle) as run:
                g._noend_fuben_run_fight(self.player, mode, 1)
            self.assertTrue(run.call_args.kwargs['allow_healing'])
            self.assertEqual(bool(run.call_args.kwargs['puppets']), mode == 3)


class NoHealingSimulationTests(unittest.TestCase):
    def simulate(self, left, right, healing, **options):
        result = {}
        replay = g.simulate_battle(left, right, None, max_rounds=options.pop('max_rounds', 2),
                                   allow_healing=healing, leader_sides=(), result_state=result, **options)
        events = [d for actions, buffs in replay for d in [e for a in actions for e in a['damages']] + buffs]
        return result, events

    def test_direct_healing_disabled_only_when_requested(self):
        with patch.dict(g._SKILL_INFO, {990001: ('heal', 1)}):
            left = [unit(3, skill1=990001, atk=100)]
            right = [unit(4, atk=1, initial_hp=1000)]
            banned, events = self.simulate(copy.deepcopy(left), copy.deepcopy(right), False)
            enabled, normal = self.simulate(copy.deepcopy(left), copy.deepcopy(right), True)
        self.assertLessEqual(banned['hp'][3], 100)
        self.assertGreater(enabled['hp'][3], 100)
        self.assertFalse(any(e['dmg'] < 0 for e in events))
        self.assertTrue(any(e['dmg'] < 0 for e in normal))

    def test_lifesteal_and_periodic_regeneration_disabled(self):
        for state_param in ((10,0,0,10000),(3,3,206,5000)):
            with self.subTest(param=state_param), \
                    patch.object(g, 'load_battle_states', return_value={990002:state_param}), \
                    patch.object(g, '_SKILL_EFFECTS', {990003:dict(state=990002,turns=3,side='self')}), \
                    patch.dict(g._SKILL_INFO, {990003:('attack',1)}):
                left = [unit(3, skill1=990003, atk=20)]
                right = [unit(4, atk=1, initial_hp=1000)]
                banned, events = self.simulate(copy.deepcopy(left), copy.deepcopy(right), False)
                normal, enabled = self.simulate(copy.deepcopy(left), copy.deepcopy(right), True)
                self.assertLessEqual(banned['hp'][3], 100)
                self.assertGreater(normal['hp'][3], 100)
                self.assertFalse(any(e['dmg'] < 0 for e in events))
                self.assertTrue(any(e['dmg'] < 0 for e in enabled))

    def test_max_hp_buff_does_not_refill_current_hp_and_replay_cancels_auto_refill(self):
        with patch.object(g, 'load_battle_states', return_value={990004:(3,6,206,1000)}), \
                patch.object(g, '_SKILL_EFFECTS', {990005:dict(state=990004,turns=3,side='self')}), \
                patch.dict(g._SKILL_INFO, {990005:('attack',1)}):
            result, events = self.simulate([unit(3, skill1=990005)], [unit(4, atk=1, initial_hp=1000)], False)
        self.assertLessEqual(result['hp'][3], 100)
        change = next(e for e in events if e.get('change_max_hp'))
        self.assertEqual(g._replay_hp_delta(change), -change['change_max_hp'])

    def test_revive_and_world_boss_self_heal_respect_flag(self):
        left = [unit(3, atk=1, initial_hp=1000)]
        right = [unit(4, card_id=998, atk=1, skill2=990006),
                 unit(8, card_id=997, atk=1, initial_hp=0, is_live=False)]
        with patch.object(g, 'RAGE_START', g.RAGE_MAX):
            banned, events = self.simulate(copy.deepcopy(left), copy.deepcopy(right), False, world_boss_id=998)
            normal, enabled = self.simulate(copy.deepcopy(left), copy.deepcopy(right), True, world_boss_id=998)
        self.assertEqual(banned['hp'][8], 0)
        self.assertGreater(normal['hp'][8], 0)
        self.assertFalse(any(e.get('is_relive') for e in events))
        self.assertTrue(any(e.get('is_relive') for e in enabled))

    def test_puppet_healing_also_respects_simulator_flag(self):
        left = [unit(3, atk=1)]
        right = [unit(4, atk=1, initial_hp=1000)]
        puppets = {1:dict(actor=101,skill=990007,skill_lv=1)}
        with patch.object(g.puppet_skill_repairs, 'effect', return_value=dict(kind='heal',fixed=100,percent=0)), \
                patch.object(g.puppet_skill_repairs, 'targets', return_value=[3]):
            banned, events = self.simulate(copy.deepcopy(left), copy.deepcopy(right), False, puppets=puppets)
            normal, enabled = self.simulate(copy.deepcopy(left), copy.deepcopy(right), True, puppets=puppets)
        self.assertLessEqual(banned['hp'][3], 100)
        self.assertGreater(normal['hp'][3], 100)
        self.assertFalse(any(e['dmg'] < 0 for e in events))
        self.assertTrue(any(e['dmg'] < 0 for e in enabled))


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        store.close_thread_db()
        DB.cleanup()
