"""Approved review repairs: final handlers, real client tables, disposable saves only."""
import atexit
import copy
import json
import logging
from pathlib import Path
import sys
import tempfile
import struct
import zlib
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store
BOOT = tempfile.TemporaryDirectory(prefix='pvz_review_repairs_boot_')
store.set_db_path(str(Path(BOOT.name) / 'boot.db'))
store.init_db()
atexit.register(BOOT.cleanup)
atexit.register(store.close_thread_db)
with patch.object(logging, 'FileHandler', return_value=logging.NullHandler()), patch.object(logging, 'basicConfig'):
    import pvz_poc_server as g
g._CLIENTDB_PATH = str(SERVER.parent / 'webroot' / 'clientDB.txt')


def message(messages, suffix):
    return next(body for name, body in messages if name.endswith(suffix))


class ReviewServerRepairs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_review_repairs_')
        self.addCleanup(self.tmp.cleanup)
        old = store.DB_PATH
        self.addCleanup(lambda: store.set_db_path(old))
        store.set_db_path(str(Path(self.tmp.name) / 'players.db'))
        store.init_db()
        self.addCleanup(store.close_thread_db)
        self.p, _ = store.get_or_create_player(88910001)
        self.p['plants'] = [1200, 1300, 1100, 1500]
        for pid in self.p['plants']:
            store.unlock_plant(self.p['uin'], pid)
        self.p['ticket'] = 1000
        store.save_player(self.p['uin'], ticket=1000)
        self.date_patch = patch.object(g.time, 'strftime', return_value='20261009')
        self.date_patch.start()
        self.addCleanup(self.date_patch.stop)
        g._noendtd_state(self.p)
        self.save(strength_point=500)

    def call(self, name, body=b''):
        return g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_NoEndTD_' + name + '_CS'](body, self.p)

    def state(self):
        return json.loads(self.p['noend_td_data'])

    def save(self, **values):
        state = self.state()
        state.update(values)
        g._save_json_state(self.p, 'noend_td_data', state)

    def info(self):
        return message(self.call('GetInfo'), 'GetInfo_SC')

    def plant(self, info, pid=1200):
        return next(g.parse_varint_fields(x) for x in g.parse_repeated_submessages(info, 3)
                    if g.parse_varint_fields(x)[1] == pid)

    def reload(self):
        store.close_thread_db()
        self.p, _ = store.get_or_create_player(self.p['uin'])
        self.p['plants'] = store.get_player_plants(self.p['uin'])

    def test_strength_costs_and_real_config_ratio(self):
        table = g._noendtd_strength_templates()
        self.assertEqual(table[1], {'cost': 100, 'score_ratio': 0})
        self.assertEqual(table[2], {'cost': 200, 'score_ratio': 250})
        self.assertEqual(table[100], {'cost': 10000, 'score_ratio': 24750})
        msgs = self.call('PlantUpgrade', g.pb_varint_field(1, 1200))
        self.assertEqual([n.rsplit('.', 1)[-1] for n, _ in msgs],
                         ['Cmd_NoEndTD_GetInfo_SC', 'Cmd_NoEndTD_PlantUpgrade_SC'])
        dto = message(msgs, 'GetInfo_SC')
        self.assertEqual(self.plant(dto)[4], 2)
        self.assertEqual(g.parse_varint_fields(dto)[4], 400)
        # The local TD client applies scoreRatio from this level, not server card ATK/HP.
        self.assertEqual(int(1000 * (10000 + table[self.plant(dto)[4]]['score_ratio']) / 10000), 1025)
        self.call('PlantUpgrade', g.pb_varint_field(1, 1200))
        self.assertEqual((self.state()['strength_point'], self.plant(self.info())[4]), (200, 3))
        self.reload()
        self.assertEqual((self.state()['strength_point'], self.plant(self.info())[4]), (200, 3))

    def test_strength_exact_balance_then_insufficient_is_not_success(self):
        self.save(strength_point=100)
        self.call('PlantUpgrade', g.pb_varint_field(1, 1200))
        before = self.state()
        self.assertEqual((before['strength_point'], before['plant_strength']['1200']), (0, 2))
        denied = self.call('PlantUpgrade', g.pb_varint_field(1, 1200))
        self.assertEqual([n for n, _ in denied], ['PVZ.Cmd.Cmd_NoEndTD_GetInfo_SC'])
        self.assertEqual(self.state(), before)

    def test_strength_max_boundary_uses_current_level_cost(self):
        self.save(plant_strength={'1200': 99}, strength_point=15000)
        self.call('PlantUpgrade', g.pb_varint_field(1, 1200))
        self.assertEqual((self.state()['strength_point'], self.plant(self.info())[4]), (5100, 100))
        before = self.state()
        denied = self.call('PlantUpgrade', g.pb_varint_field(1, 1200))
        self.assertFalse(any(n.endswith('PlantUpgrade_SC') for n, _ in denied))
        self.assertEqual(self.state(), before)

    def test_strength_unowned_invalid_and_non_attack_rejected(self):
        for pid in (0, 999999, 1210, 1300, 1100):
            with self.subTest(pid=pid):
                before = self.state()
                self.call('PlantUpgrade', g.pb_varint_field(1, pid))
                self.assertEqual(self.state(), before)

    def test_strength_bad_old_levels_are_bounded(self):
        self.save(plant_strength={'1200': 0, '1500': 900, 'bad': 'a', '13': None})
        self.assertEqual(self.plant(self.info())[4], 1)
        self.assertEqual(self.plant(self.info(), 1500)[4], 100)
        self.assertEqual(self.state()['plant_strength'], {'1200': 1, '1500': 100})

    def test_strength_without_config_is_not_free_or_successful(self):
        before = self.state()
        with patch.object(g, '_noendtd_strength_templates', return_value={}):
            msgs = self.call('PlantUpgrade', g.pb_varint_field(1, 1200))
        self.assertFalse(any(n.endswith('PlantUpgrade_SC') for n, _ in msgs))
        self.assertEqual(self.state(), before)

    def test_strength_daily_reset_keeps_upgrade_and_balance(self):
        self.call('PlantUpgrade', g.pb_varint_field(1, 1200))
        self.save(level=5, wave=1, active_level=6, challenge_times=1, score=9000)
        with patch.object(g.time, 'strftime', return_value='20261010'):
            info = self.info()
        self.assertEqual(self.plant(info)[4], 2)
        self.assertEqual((self.state()['strength_point'], self.state()['rain']), (400, 200))
        self.assertEqual((self.state()['level'], self.state()['active_level'], self.state()['challenge_times']), (0, 0, 0))

    def test_upgraded_dto_is_available_before_start_and_score_is_not_reapplied(self):
        self.call('PlantUpgrade', g.pb_varint_field(1, 1200))
        self.assertEqual(self.plant(self.info())[4], 2)
        self.call('StartTDGame', g.pb_varint_field(1, 1) + g.pb_varint_field(4, 1))
        report = message(self.call('ReportResult', g.pb_varint_field(1, 1) + g.pb_varint_field(2, 1)
                                  + g.pb_varint_field(3, 1025)), 'ReportResult_SC')
        mode = g.parse_varint_fields(g.parse_repeated_submessages(report, 2)[0])
        self.assertEqual(mode[5], 1025)  # The client already scored with the strengthening bonus.
        self.assertEqual(self.plant(self.info())[4], 2)

    def test_week_reward_list_matches_actual_grant_and_repeated_claim(self):
        displayed = [g.parse_varint_fields(x) for x in g.parse_repeated_submessages(self.info(), 5)]
        self.assertEqual(displayed, [{1: 1, 2: 5000}, {1: 4, 2: 10}])
        before = (self.p['coin'], self.p['present_ticket'])
        first = self.call('TakeWeekReward')
        claimed = [g.parse_varint_fields(x) for x in g.parse_repeated_submessages(message(first, 'TakeWeekReward_SC'), 1)]
        self.assertEqual(claimed, displayed)
        self.assertEqual((self.p['coin'], self.p['present_ticket']), (before[0] + 5000, before[1] + 10))
        self.reload()
        second = self.call('TakeWeekReward')
        self.assertFalse(any(n.endswith('TakeWeekReward_SC') for n, _ in second))
        self.assertEqual((self.p['coin'], self.p['present_ticket']), (before[0] + 5000, before[1] + 10))

    def test_week_reward_same_week_daily_boundary_does_not_reopen(self):
        self.call('TakeWeekReward')
        self.save(plant_strength={'1200': 8}, strength_point=123, rain=77)
        with patch.object(g.time, 'strftime', return_value='20261011'):
            self.info()
            msgs = self.call('TakeWeekReward')
        self.assertFalse(any(n.endswith('TakeWeekReward_SC') for n, _ in msgs))
        self.assertEqual((self.state()['plant_strength']['1200'], self.state()['strength_point'], self.state()['rain']), (8, 123, 77))

    def test_week_reward_next_monday_grants_once_without_backfill(self):
        self.call('TakeWeekReward')
        before = self.p['coin']
        with patch.object(g.time, 'strftime', return_value='20261102'):
            self.assertEqual(g.parse_varint_fields(self.info())[6], 0)
            self.call('TakeWeekReward')
            self.call('TakeWeekReward')
        self.assertEqual(self.p['coin'], before + 5000)
        self.assertEqual(self.state()['reward_week'], '2026-W45')

    def test_legacy_claim_without_week_does_not_pay_twice_on_upgrade(self):
        data = self.state()
        data.pop('reward_week')
        data['taken'] = True
        g._save_json_state(self.p, 'noend_td_data', data)
        before = self.p['coin']
        self.call('TakeWeekReward')
        self.assertEqual((self.state()['reward_week'], self.p['coin']), ('2026-W41', before))
        with patch.object(g.time, 'strftime', return_value='20261012'):
            self.call('TakeWeekReward')
            self.call('TakeWeekReward')
        self.assertEqual(self.p['coin'], before + 5000)

    def test_legacy_unclaimed_record_can_claim_current_week(self):
        data = self.state()
        data.pop('reward_week')
        data['taken'] = False
        g._save_json_state(self.p, 'noend_td_data', data)
        before = self.p['coin']
        self.call('TakeWeekReward')
        self.assertEqual(self.p['coin'], before + 5000)

    def test_refreshes_keep_real_saved_lawn_record_dto(self):
        self.save(level=1, wave=1, score=50)
        self.info()  # Opening the panel creates the installed feature-state table.
        lawn = zlib.compress(struct.pack(">I", 1) + bytes(7))
        saved = self.call("SaveRecord", g.pb_bytes(1, lawn))
        self.assertTrue(any(n.endswith("SaveRecord_SC") for n, _ in saved))
        record = g.parse_repeated_submessages(self.info(), 10)
        self.assertEqual(len(record), 1)
        for plant_id in (1200, 0, 1300):
            messages = self.call("PlantUpgrade", g.pb_varint_field(1, plant_id))
            self.assertEqual(g.parse_repeated_submessages(message(messages, "GetInfo_SC"), 10), record)
        self.call("TakeWeekReward")
        duplicate = self.call("TakeWeekReward")
        self.assertEqual(g.parse_repeated_submessages(message(duplicate, "GetInfo_SC"), 10), record)
        # Both public handlers also retain that record when called directly.
        direct = g.handle_noendtd_upgrade(g.pb_varint_field(1, 1300), self.p)
        self.assertEqual(g.parse_repeated_submessages(message(direct, "GetInfo_SC"), 10), record)

    def test_week_reward_second_currency_failure_rolls_back_and_retry_pays_once(self):
        before = copy.deepcopy(self.p)
        saved = store.save_player

        def fail_second(uin, **fields):
            if "present_ticket" in fields:
                raise RuntimeError("injected second reward write failure")
            return saved(uin, **fields)

        with patch.object(store, "save_player", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "second reward"):
                self.call("TakeWeekReward")
        self.assertEqual(self.p, before)
        self.reload()
        self.assertEqual((self.p["coin"], self.p["present_ticket"]), (before["coin"], before["present_ticket"]))
        self.assertFalse(self.state()["taken"])
        self.call("TakeWeekReward")
        self.call("TakeWeekReward")
        self.assertEqual((self.p["coin"], self.p["present_ticket"]), (before["coin"] + 5000, before["present_ticket"] + 10))

    def test_stale_second_session_cannot_claim_the_same_week_again(self):
        stale = copy.deepcopy(self.p)
        before = self.p["coin"]
        self.call("TakeWeekReward")
        messages = g.REQUEST_HANDLERS["PVZ.Cmd.Cmd_NoEndTD_TakeWeekReward_CS"](b"", stale)
        self.assertFalse(any(n.endswith("TakeWeekReward_SC") for n, _ in messages))
        self.assertEqual(stale["coin"], before + 5000)
        self.reload()
        self.assertEqual(self.p["coin"], before + 5000)

    def test_upgrade_state_write_failure_rolls_back_in_memory_and_database(self):
        before = copy.deepcopy(self.p)
        saved = store.save_player

        def fail_upgrade(uin, **fields):
            raw = fields.get("noend_td_data")
            if raw and json.loads(raw).get("plant_strength", {}).get("1200") == 2:
                raise RuntimeError("injected upgrade save failure")
            return saved(uin, **fields)

        with patch.object(store, "save_player", side_effect=fail_upgrade):
            with self.assertRaisesRegex(RuntimeError, "upgrade save"):
                self.call("PlantUpgrade", g.pb_varint_field(1, 1200))
        self.assertEqual(self.p, before)
        self.reload()
        self.assertEqual(self.state()["strength_point"], 500)
        self.assertEqual(self.state()["plant_strength"], {})

    def test_upgrade_getinfo_failure_rolls_back_cost_and_level(self):
        before = copy.deepcopy(self.p)
        with patch.dict(g.REQUEST_HANDLERS, {
                "PVZ.Cmd.Cmd_NoEndTD_GetInfo_CS": lambda *_: (_ for _ in ()).throw(RuntimeError("injected DTO failure"))}):
            with self.assertRaisesRegex(RuntimeError, "DTO failure"):
                self.call("PlantUpgrade", g.pb_varint_field(1, 1200))
        self.assertEqual(self.p, before)
        self.reload()
        self.assertEqual(self.state()["strength_point"], 500)
        self.assertEqual(self.state()["plant_strength"], {})
        self.call("PlantUpgrade", g.pb_varint_field(1, 1200))
        self.assertEqual((self.state()["strength_point"], self.state()["plant_strength"]["1200"]), (400, 2))

    def test_iso_week_uses_week_year_at_new_year(self):
        self.assertEqual(g._noendtd_week_key('20201231'), '2020-W53')
        self.assertEqual(g._noendtd_week_key('20210101'), '2020-W53')
        self.assertEqual(g._noendtd_week_key('20210104'), '2021-W01')

    def test_endless_privilege_exactly_matches_normal_td(self):
        for entitlement, expiry, now in ((1, 2000, 1000), (1, 0, 1000), (1, 1000, 1000), (0, 2000, 1000)):
            with self.subTest(entitlement=entitlement, expiry=expiry):
                self.p.update(td_privilege=entitlement, td_privilege_endtime=expiry, vip_level=0)
                store.save_player(self.p['uin'], td_privilege=entitlement, td_privilege_endtime=expiry, vip_level=0)
                with patch.object(g.time, 'time', return_value=now):
                    endless = self.info()
                    normal = g.handle_td_privilege_info(b'', self.p)
                e, n = g.parse_varint_fields(endless), g.parse_varint_fields(normal)
                self.assertEqual((e[7], e[9]), (n[4], n[6]))
                self.assertEqual(g.parse_repeated_varint(endless, 8), g.parse_repeated_varint(normal, 5))
                self.assertEqual(e[7], int(entitlement == 1 and (expiry == 0 or expiry > now)))

    def test_vip_without_td_entitlement_cannot_enable_privilege(self):
        self.p.update(td_privilege=0, td_privilege_endtime=0)
        with patch.object(g, 'active_vip_level', return_value=10):
            data = g.parse_varint_fields(self.info())
        self.assertEqual((data[7], data[9]), (0, 0))

    def test_avatar_shield_agrees_in_ui_package_actual_units_and_collection_power(self):
        uid = 33301
        store.add_card(self.p['uin'], uid, 1201, 10)
        store.set_card_avatar(self.p['uin'], uid, 1201, status=4, forge_level=0)
        g.ensure_player_cards(self.p)
        before = g.effective_card_stats(self.p, 1201, 10, g._ZERO_STAT_BONUS, uid=uid)
        power_before = g._player_fight_power(self.p)
        store.set_card_avatar(self.p['uin'], uid, 1201, status=4, forge_level=2)
        after = g.effective_card_stats(self.p, 1201, 10, g._ZERO_STAT_BONUS, uid=uid)
        wardrobe = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Card_GetAvatarInfo_CS'](g.pb_int64_field(1, uid), self.p)
        bonus = g.parse_varint_fields(g.parse_repeated_submessages(wardrobe, 2)[0])
        self.assertEqual(after['shield'] - before['shield'], bonus[4])
        self.assertEqual(bonus[4], 200)
        for field, wire in (('atk', 1), ('def_', 2), ('hp', 3), ('ability', 5)):
            self.assertEqual(after[field] - before[field], bonus[wire])
        self.assertEqual(g.parse_varint_fields(g.build_card_full_info(uid, 1201, 10, after))[6], after['shield'])
        package = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Card_GetPackage_CS'](b'', self.p)
        card = next(x for x in g.parse_repeated_submessages(package, 1)
                    if g.parse_varint_fields(g.parse_repeated_submessages(x, 1)[0])[1] == uid)
        self.assertEqual(g.parse_varint_fields(card)[6], after['shield'])
        unit = g.build_player_units(self.p, {3: uid})[0]
        self.assertEqual(unit['shield'], after['shield'])
        self.assertEqual(g._player_fight_power(self.p) - power_before, g.card_power(after) - g.card_power(before))
        # Original client power formula excludes shield; source breakdown must still sum exactly.
        sources = g._card_power_source_elements(self.p, uid, 1201, 10)
        self.assertEqual(sum(x[1] for x in sources if x[0] != 0), g.card_power(after))
        self.reload()
        self.assertEqual(g.build_player_units(self.p, {3: uid})[0]['shield'], unit['shield'])

    def test_avatar_added_shield_absorbs_real_combat_damage(self):
        uid = 33301
        store.add_card(self.p['uin'], uid, 1201, 10)
        store.set_card_avatar(self.p['uin'], uid, 1201, status=4, forge_level=2)
        left = g.build_player_units(self.p, {3: uid})
        self.assertGreaterEqual(left[0]['shield'], 200)
        left[0].update(skill1=0, skill2=0, is_leader=False, atk=1, def_=0, hit=0, dodge=0, critical=0, tough=0)
        right = [dict(pos=4, card_id=101, hp=100000, atk=100, skill1=0, skill2=0, def_=0, hit=0, dodge=0, critical=0, tough=0)]
        rounds = g.simulate_battle(left, right, False, max_rounds=1, leader_sides=())
        damages = [d for actions, _ in rounds for a in actions if a['actor'] == 4
                   for d in a['damages'] if d['target'] == 3]
        self.assertTrue(damages)
        self.assertEqual(damages[0]['dmg'], 0)
        self.assertLess(damages[0]['change_shield'], 0)
        self.assertEqual(left[0]['max_shield'], 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
