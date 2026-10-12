"""Reported gameplay boundaries, using real clientDB and disposable SQLite saves."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
import missing_features_local as mf
from test_live_combat import unit


class GameplayAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = g.store.DB_PATH
        self.addCleanup(lambda: g.store.set_db_path(old))
        g.store.set_db_path(str(Path(self.tmp.name) / 'test.db'))
        g.store.init_db()
        self.player, _ = g.store.get_or_create_player(88007007)
        self.player.update(level=100, coin=100000000)
        g.store.save_player(self.player['uin'], level=100, coin=100000000)
        self.uin = self.player['uin']
        self.api = dict(mf._API, db_path=g.store.DB_PATH)
        self.routes = mf.register(self.api)
        g.load_battle_cards()
        g.load_skill_info()
        self.addCleanup(g.store.close_thread_db)

    def use(self, item_id, count=1):
        return dict(g.handle_item_use(g.pb_varint_field(1, item_id)
                                     + g.pb_varint_field(2, count), self.player))

    def puppet(self, level=100, active_level=1, passive_level=1):
        info = g.load_dummy_config()['zombies'][1000001]
        g.store.save_puppet(self.uin, 1000001, level=level, star=1, quality=100,
                            active_skill=info['active'], active_level=active_level,
                            passive_skill=info['passive'], passive_level=passive_level)
        g.save_puppet_lineup(self.player, [1000001])
        return g.store.get_puppet(self.uin, 1000001)

    def equipment(self, uid=20000001, level=10, is_clone=0):
        g.store.add_equipment(self.uin, uid, 30001, level=level, is_clone=is_clone)
        return g.store.get_equipment(self.uin, uid)

    def error(self, messages):
        return g.parse_varint_fields(dict(messages)['PVZ.Cmd.Cmd_LogicError_SC'])[1]

    def test_all_twenty_puppet_items_resolve_by_name(self):
        mapped = [g._puppet_consumable(i) for i in range(97001, 97021)]
        self.assertTrue(all(mapped))
        self.assertEqual(len({x[0] for x in mapped}), 20)
        self.assertIsNone(g._puppet_consumable(97021))

    def test_bag_puppet_is_consumed_into_real_storage(self):
        g.store.add_item(self.uin, 97001, 1)
        reply = self.use(97001)
        self.assertEqual(g.store.get_item_count(self.uin, 97001), 0)
        row = g.store.get_puppet(self.uin, 1000001)
        self.assertEqual((row['level'], row['active_level'], row['passive_level']), (1, 1, 1))
        self.assertIn('PVZ.Cmd.Cmd_Item_Update_SC', reply)
        self.assertIn('PVZ.Cmd.Cmd_Puppet_GetInfo_SC', reply)
        self.assertEqual(g.parse_varint_fields(reply['PVZ.Cmd.Cmd_Item_Use_SC'])[2], 1)

    def test_batch_puppet_first_unlock_and_duplicates_grant_configured_pieces(self):
        g.store.add_item(self.uin, 97003, 3)
        base_id, info, pieces, _ = g._puppet_consumable(97003)
        self.assertEqual(pieces, 80)
        # Current backpack validation rejects requests exceeding ownership.
        rejected = self.use(97003, 50)
        self.assertEqual(g.parse_varint_fields(rejected['PVZ.Cmd.Cmd_LogicError_SC'])[1], 204)
        self.assertEqual(g.store.get_item_count(self.uin, 97003), 3)
        self.assertIsNone(g.store.get_puppet(self.uin, base_id))
        self.use(97003, 3)
        self.assertIsNotNone(g.store.get_puppet(self.uin, base_id))
        self.assertEqual(g.store.get_item_count(self.uin, 97003), 0)
        self.assertEqual(g.store.get_item_count(self.uin, info['piece']), 160)
        reply = self.use(97003)
        self.assertEqual(g.parse_varint_fields(reply['PVZ.Cmd.Cmd_LogicError_SC'])[1], 204)
        self.assertEqual(g.store.get_item_count(self.uin, info['piece']), 160)

    def test_owned_puppet_keeps_upgrades_and_other_owned_puppets(self):
        self.puppet(level=70, active_level=19)
        second, info, _, _ = g._puppet_consumable(97002)
        g.store.save_puppet(self.uin, second, level=1, star=1, quality=100,
                            active_skill=info['active'], passive_skill=info['passive'])
        g.store.add_item(self.uin, 97001, 2)
        reply = self.use(97001, 2)
        self.assertEqual(g.store.get_puppet(self.uin, 1000001)['active_level'], 19)
        self.assertEqual(g.store.get_item_count(self.uin, 95601), 40)
        listed = g.parse_repeated_submessages(reply['PVZ.Cmd.Cmd_Puppet_GetInfo_SC'], 1)
        self.assertEqual({g.parse_varint_fields(x)[1] for x in listed}, {1000001, second})

    def test_puppet_use_rolls_back_if_inventory_sync_fails(self):
        g.store.add_item(self.uin, 97001, 1)
        with patch.object(g, 'grant_items', side_effect=RuntimeError('sync failed')):
            with self.assertRaises(RuntimeError):
                self.use(97001)
        self.assertIsNone(g.store.get_puppet(self.uin, 1000001))
        self.assertEqual(g.store.get_item_count(self.uin, 97001), 1)

    def test_skill_refusal_echoes_saved_19_for_active_and_passive(self):
        self.puppet(level=19, active_level=19, passive_level=19)
        for passive in (0, 1):
            for _ in range(2):
                req = g.pb_varint_field(1, 1000001) + g.pb_varint_field(2, passive)
                reply = dict(g.handle_puppet_upgradeskilllevel(req, self.player))
                fields = g.parse_varint_fields(reply['PVZ.Cmd.Cmd_Puppet_UpgradeSkillLevel_SC'])
                self.assertEqual((fields[2], fields[3]), (passive, 19))
        self.assertEqual(g.store.get_puppet(self.uin, 1000001)['active_level'], 19)

    def test_skill_19_to_20_persists_when_puppet_level_allows(self):
        self.puppet(active_level=19, passive_level=19)
        before_coin = self.player['coin']
        before_points = g.puppet_skill_val(self.player)
        for passive in (0, 1):
            req = g.pb_varint_field(1, 1000001) + g.pb_varint_field(2, passive)
            reply = dict(g.handle_puppet_upgradeskilllevel(req, self.player))
            self.assertNotIn('PVZ.Cmd.Cmd_LogicError_SC', reply)
            self.assertEqual(g.parse_varint_fields(reply['PVZ.Cmd.Cmd_Puppet_UpgradeSkillLevel_SC'])[3], 20)
        # Original clientDB row 19: 2409 coins + 1 skill point, no skill disc.
        self.assertEqual(before_coin - self.player['coin'], 2409 * 2)
        self.assertEqual(before_points - g.puppet_skill_val(self.player), 2)
        self.assertEqual(g.store.get_item_count(self.uin, 95532), 0)
        row = g.store.get_puppet(self.uin, 1000001)
        self.assertEqual((row['active_level'], row['passive_level']), (20, 20))
        g.store.close_thread_db()
        self.assertEqual(g.store.get_puppet(self.uin, 1000001)['active_level'], 20)

    def test_skill_insufficient_funds_echoes_saved_level_without_spend(self):
        self.puppet(active_level=19)
        self.player['coin'] = 0
        # The mutation transaction refreshes the authoritative saved balance.
        g.store.save_player(self.uin, coin=0)
        req = g.pb_varint_field(1, 1000001)
        reply = dict(g.handle_puppet_upgradeskilllevel(req, self.player))
        self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', reply)
        self.assertEqual(g.parse_varint_fields(reply['PVZ.Cmd.Cmd_LogicError_SC'])[1], 202)
        self.assertEqual(g.parse_varint_fields(reply['PVZ.Cmd.Cmd_Puppet_UpgradeSkillLevel_SC'])[3], 19)
        self.assertEqual(g.store.get_puppet(self.uin, 1000001)['active_level'], 19)

    def test_skill_20_to_21_requires_disc_and_does_not_consume_when_missing(self):
        self.puppet(active_level=20)
        before_points = g.puppet_skill_val(self.player)
        before_coin = self.player['coin']
        reply = dict(g.handle_puppet_upgradeskilllevel(g.pb_varint_field(1, 1000001), self.player))
        self.assertEqual(g.parse_varint_fields(reply['PVZ.Cmd.Cmd_LogicError_SC'])[1], 204)
        self.assertEqual(g.parse_varint_fields(reply['PVZ.Cmd.Cmd_Puppet_UpgradeSkillLevel_SC'])[3], 20)
        self.assertEqual(g.puppet_skill_val(self.player), before_points)
        self.assertEqual(self.player['coin'], before_coin)
        self.assertEqual(g.store.get_puppet(self.uin, 1000001)['active_level'], 20)

    def test_skill_20_to_21_charges_current_row_for_both_skills(self):
        self.puppet(active_level=20, passive_level=20)
        g.store.add_item(self.uin, 95532, 20)
        before_coin = self.player['coin']
        before_points = g.puppet_skill_val(self.player)
        for passive in (0, 1):
            req = g.pb_varint_field(1, 1000001) + g.pb_varint_field(2, passive)
            reply = dict(g.handle_puppet_upgradeskilllevel(req, self.player))
            self.assertNotIn('PVZ.Cmd.Cmd_LogicError_SC', reply)
            self.assertEqual(g.parse_varint_fields(reply['PVZ.Cmd.Cmd_Puppet_UpgradeSkillLevel_SC'])[3], 21)
        # Original clientDB row 20: 2529 coins + 1 skill point + 10 skill discs.
        self.assertEqual(before_coin - self.player['coin'], 2529 * 2)
        self.assertEqual(before_points - g.puppet_skill_val(self.player), 2)
        self.assertEqual(g.store.get_item_count(self.uin, 95532), 0)
        row = g.store.get_puppet(self.uin, 1000001)
        self.assertEqual((row['active_level'], row['passive_level']), (21, 21))

    def test_skill_first_upgrade_uses_row_one_price(self):
        self.puppet()
        before_coin = self.player['coin']
        reply = dict(g.handle_puppet_upgradeskilllevel(g.pb_varint_field(1, 1000001), self.player))
        self.assertNotIn('PVZ.Cmd.Cmd_LogicError_SC', reply)
        self.assertEqual(g.parse_varint_fields(reply['PVZ.Cmd.Cmd_Puppet_UpgradeSkillLevel_SC'])[3], 2)
        self.assertEqual(before_coin - self.player['coin'], 1000)

    def test_endless_actual_battle_report_preserves_picked_cards_and_type(self):
        self.set_pre_card()
        with patch.object(g, '_noend_fuben_squad', return_value=[unit(4, hp=10, atk=1)]):
            result = self.noend('Fight', g.pb_varint_field(1, 1))
        report = mf._wire2(result, 6)
        self.assertEqual(mf._wire0(report, 5), 26)
        self.assertEqual((mf._wire0(result, 1), mf._wire0(result, 3)), (0, 1))
        left = mf._wire2(report, 2)
        self.assertEqual([mf._wire0(card, 1) for card in mf._repeated_messages(left, 2)], [3, 7])
        self.assertTrue(mf._repeated_messages(report, 4))

    def test_pine_10_advances_to_birch_1_at_same_rank(self):
        row = self.equipment()
        g.store.update_equipment(self.uin, row['uid'], exp=9000)
        g.store.update_equipment_state(self.uin, current_energy=50000,
                                       last_energy_at=int(time.time()))
        reply = dict(g.handle_equip_add_exp(g.pb_int64_field(1, row['uid']), self.player))
        after = g.store.get_equipment(self.uin, row['uid'])
        self.assertEqual((after['item_id'], after['level']), (30001, 11))
        self.assertIn('PVZ.Cmd.Cmd_Equip_AddExp_SC', reply)
        self.assertEqual(g._equip_row('EquipmentLevel.txt', type=1, level=11)['name'], '桦木1级')

    def test_equipment_top_level_never_spends_energy(self):
        maximum = g._equipment_max_level(1)
        self.assertEqual(maximum, 500)
        row = self.equipment(level=maximum)
        g.store.update_equipment_state(self.uin, current_energy=50000,
                                       last_energy_at=int(time.time()))
        before = g.store.get_equipment_state(self.uin)['current_energy']
        self.assertEqual(g.handle_equip_add_all_exp(g.pb_int64_field(1, row['uid']), self.player), [])
        self.assertEqual(g.store.get_equipment_state(self.uin)['current_energy'], before)

    def test_one_key_equipment_leveling_passes_10_and_survives_reload(self):
        row = self.equipment()
        g.store.update_equipment_state(self.uin, current_energy=50000, auto_use_bead=1,
                                       last_energy_at=int(time.time()))
        g.store.add_item(self.uin, 2046, 30)
        g.handle_equip_add_all_exp(g.pb_int64_field(1, row['uid']), self.player)
        g.store.close_thread_db()
        self.assertGreater(g.store.get_equipment(self.uin, row['uid'])['level'], 11)

    def test_uncloneable_score_and_clone_source_are_refused(self):
        row = self.equipment(level=100)
        self.assertEqual(self.error(g.handle_equip_clone(g.pb_int64_field(1, row['uid']), self.player)), 2411)
        self.assertEqual(len(g.store.get_equipments(self.uin)), 1)
        clone = self.equipment(uid=20000002, is_clone=1)
        self.assertEqual(self.error(g.handle_equip_clone(g.pb_int64_field(1, clone['uid']), self.player)), 2410)

    def test_permitted_clone_keeps_source_stats_and_enforces_25_limit(self):
        for i in range(24):
            self.equipment(uid=g.store.next_equipment_uid(self.uin), level=100)
        req = g.pb_int64_field(1, 20000001)
        for _ in range(25):
            self.assertIn('PVZ.Cmd.Cmd_Equip_CloneEquip_SC', dict(g.handle_equip_clone(req, self.player)))
        rows = [r for r in g.store.get_equipments(self.uin) if r['is_clone']]
        self.assertEqual(len(rows), 25)
        self.assertTrue(all(r['level'] == 100 and r['card_uid'] == 0 for r in rows))
        self.assertEqual(self.error(g.handle_equip_clone(req, self.player)), 2434)

    def test_clones_cannot_level_or_rank_up(self):
        row = self.equipment(is_clone=1)
        req = g.pb_int64_field(1, row['uid'])
        self.assertEqual(self.error(g.handle_equip_add_exp(req, self.player)), 2429)
        self.assertEqual(self.error(g.handle_equip_upgrade(req, self.player)), 2430)
        self.assertEqual(g.store.get_equipment(self.uin, row['uid']), row)

    def noend(self, name, body=b''):
        return self.routes['PVZ.Cmd.Cmd_NoEndFuben_' + name + '_CS'](body, self.player)

    def set_pre_card(self):
        for uid in (9001, 9002):
            g.store.add_card(self.uin, uid, 101, 100)
        self.noend('SetPreCard', g.pb_int64_field(1, 9001) + g.pb_int64_field(1, 9002))

    def test_endless_win_confirm_next_floor_is_sequential_and_hp_persists(self):
        self.set_pre_card()
        self.assertEqual(mf._wire0(self.noend('Info'), 5), 0)
        report = g.build_fight_report(self.player, 1, 0, 1, True, left_units=[
            unit(3, final_hp=5000), unit(7, final_hp=0)], right_units=[unit(4)], fight_type=26)
        with patch.dict(mf._API, run_fight=lambda *_: [('PVZ.Cmd.Cmd_Battle_Fight_SC', g.pb_bytes(1, report))]):
            for floor in (1, 2, 3):
                result = self.noend('Fight', g.pb_varint_field(1, floor))
                saved = mf._wire0(result, 1)
                pending = mf._wire0(result, 3)
                self.assertEqual((saved, pending), (floor-1, 1))
                self.assertEqual(saved + pending + 1, floor + 1)
                confirmed = self.noend('ConfirmFight', g.pb_varint_field(1, floor))
                self.assertEqual(mf._wire0(confirmed, 1), floor)
                info = self.noend('Info')
                self.assertEqual((mf._wire0(info, 5), mf._wire0(info, 12)), (floor, 0))
                self.assertEqual(self.noend('ConfirmFight', g.pb_varint_field(1, floor)), confirmed)
        with mf._db() as con:
            row = mf._noend_row(con, self.uin)
            self.assertEqual(json.loads(row['hp_ratios']), {'9001': .5, '9002': 0})
        self.noend('Reset')
        self.assertEqual(mf._wire0(self.noend('Info'), 5), 0)

    def test_endless_loss_retry_does_not_advance_or_confirm_unwon_floor(self):
        self.set_pre_card()
        report = g.build_fight_report(self.player, 1, 0, 1, False, left_units=[unit(3, final_hp=0)],
                                     right_units=[unit(4)], fight_type=26)
        with patch.dict(mf._API, run_fight=lambda *_: [('PVZ.Cmd.Cmd_Battle_Fight_SC', g.pb_bytes(1, report))]):
            result = self.noend('Fight', g.pb_varint_field(1, 1))
        self.assertEqual((mf._wire0(result, 1), mf._wire0(result, 3), mf._wire0(result, 4)), (1, 0, 1))
        self.assertEqual(mf._wire0(self.noend('ConfirmFight', g.pb_varint_field(1, 99)), 1), 1)

    def test_endless_old_pending_progress_migrates_once(self):
        self.noend('Info')
        with mf._db() as con:
            con.execute('UPDATE local_noend_fuben SET stage_id=29,history_max_stage_id=29,'
                        'best_stage_id=28,has_pass_next_stage=1,progress_version=0 WHERE uin=?', (self.uin,))
        for _ in range(2):
            info = self.noend('Info')
            self.assertEqual((mf._wire0(info, 5), mf._wire0(info, 6), mf._wire0(info, 12)), (27, 28, 1))

    def test_arena_rank_and_cross_server_exclude_puppets_and_use_arena_sides(self):
        self.puppet()
        opponent = dict(uin=99001, name='test opponent', power=100, level=10,
                        face_id=4, qq_face_url='http://127.0.0.1:8080/image/head/4.png')
        encounter = dict(units=[unit(4, hp=5000, atk=1)], name='test opponent', level=10)
        self.assertFalse(g.run_battle([unit(3, hp=1000000, atk=1)],
                                     copy.deepcopy(encounter['units']))[0])
        with patch.object(g, 'build_player_units', side_effect=lambda *_a, **_k: [unit(3, hp=1000000, atk=1)]), \
                patch.object(g, '_npc_encounter', return_value=encounter), \
                patch.object(g, '_refresh_arena_npcs'), \
                patch.object(g.bots_local, 'pick_opponent', return_value=opponent), \
                patch.object(g.bots_local, 'bots', return_value=[opponent]), \
                patch.object(g.bots_local, 'is_bot', return_value=True), \
                patch.object(g.bots_local, 'apply_match_result'), \
                patch.object(g.bots_local, 'bot_state', return_value={}), \
                patch.object(g, '_asra_ranked', return_value=([(tuple([self.uin]), 1)], {self.uin: 1, opponent['uin']: 2})), \
                patch.object(g, '_player_formation_power', return_value=100), \
                patch.object(g, 'run_battle', wraps=g.run_battle) as run:
            for handler, target, sides in ((g.handle_arena_challenge, g._ARENA_TARGET, (10, 11)),
                                           (g.handle_rankarena_challenge, g._RANK_ARENA_TARGET, (19, 20)),
                                           (g.handle_allserver_rankarena_challenge, g._ALLSERVER_RANK_ARENA_TARGET, (45, 46))):
                with self.subTest(handler=handler.__name__):
                    messages = handler(g.pb_varint_field(1, opponent['uin']), self.player)
                    if target == g._ALLSERVER_RANK_ARENA_TARGET:
                        report = g.parse_repeated_submessages(messages[0][1], 7)[0]
                    else:
                        report = g._BATTLE_REPORTS[max(g._BATTLE_REPORTS)]
                    left = g.parse_repeated_submessages(report, 2)[0]
                    right = g.parse_repeated_submessages(report, 3)[0]
                    listed = g.parse_repeated_submessages(left, 16)
                    self.assertEqual(listed, [])
                    self.assertEqual(g.parse_repeated_submessages(right, 16), [])
                    self.assertEqual((g.parse_varint_fields(left)[1], g.parse_varint_fields(right)[1]), sides)
                    self.assertEqual(g.parse_repeated_submessages(right, 6), [b'test opponent'])
                    self.assertEqual(g.parse_varint_fields(right)[8], 10)
                    self.assertEqual(g.parse_varint_fields(right)[5], 4)
                    self.assertEqual(g.parse_repeated_submessages(right, 13), [opponent['qq_face_url'].encode()])
                    actions = [a for rnd in g.parse_repeated_submessages(report, 4)
                               for a in g.parse_repeated_submessages(rnd, 2)]
                    self.assertFalse(any(g.parse_repeated_submessages(a, 6) for a in actions))
                    self.assertEqual(g.parse_varint_fields(report)[5], target)
                    self.assertEqual(g.parse_varint_fields(report)[1], 1)
                    self.assertEqual(g.parse_varint_fields(report)[7], 2)
                    self.assertFalse(run.call_args.kwargs['puppets'])
                    self.assertEqual(run.call_args.kwargs['max_rounds'], 30)


if __name__ == '__main__':
    unittest.main()
