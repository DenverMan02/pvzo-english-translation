"""Client-contract regressions using disposable SQLite saves only."""
from pathlib import Path
import copy
import logging
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
SERVER = Path(r'D:\pvzol本地最新最新ol\pvzol本地\PvZ-Server-PoC')
WORK = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get('PVZ_PUPPET_SOURCE', str(WORK / 'pvz_poc_server.py'
              if (WORK / 'pvz_poc_server.py').exists() else SERVER / 'pvz_poc_server.py')))
sys.path.insert(0, str(SERVER))
import player_store as store

DB = tempfile.TemporaryDirectory(prefix='pvz-puppet-promotion-')
store.set_db_path(str(Path(DB.name) / 'test.db'))
store.init_db()
g = types.ModuleType('pvz_puppet_promotion_under_test')
g.__file__ = str(SERVER / 'pvz_poc_server.py')
sys.modules[g.__name__] = g
source = SOURCE.read_text(encoding='utf-8').replace(
    'LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_runtime.log")',
    'LOG_PATH = ' + repr(str(WORK / 'test-runtime.log')))
exec(compile(source, str(SOURCE), 'exec'), g.__dict__)
g.log.setLevel(logging.WARNING)
g._CLIENTDB_PATH = str(SERVER.parent / 'webroot' / 'clientDB.txt')
g._DUMMY = None

BASE = 1000001
QUALITY_SC = 'PVZ.Cmd.Cmd_Puppet_UpgradeQuality_SC'
SPAR_SC = 'PVZ.Cmd.Cmd_Puppet_UpgradeSparQuality_SC'
SNAPSHOT_SC = 'PVZ.Cmd.Cmd_Puppet_GetInfo_SC'
ERROR_SC = 'PVZ.Cmd.Cmd_LogicError_SC'

def body(messages, name):
    return next((payload for actual, payload in messages if actual == name), None)

class PuppetPromotionTests(unittest.TestCase):
    seq = 0

    def setUp(self):
        type(self).seq += 1
        self.p, _ = store.get_or_create_player(97001000 + self.seq)
        self.uin = self.p['uin']
        self.p.update(level=200, coin=1000000000, puppet_granted=1)
        store.save_player(self.uin, level=200, coin=self.p['coin'], puppet_granted=1)
        self.cfg = g.load_dummy_config()
        info = self.cfg['zombies'][BASE]
        store.save_puppet(self.uin, BASE, level=10, quality=100, exp=0, star=1,
                          active_skill=info['active'], active_level=1,
                          passive_skill=info['passive'], passive_level=1, spars=self.gear(100))

    def gear(self, quality):
        return g.puppet_spars_text({part: (level, q) for part, q, level
                                   in self.cfg['quality'][quality]['equip_need']})

    def row(self):
        return store.get_puppet(self.uin, BASE)

    def call(self, action, **fields):
        data = b''.join(g.pb_varint_field(int(k), value) for k, value in fields.items())
        return g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Puppet_' + action + '_CS'](data, self.p)

    def promote(self, use_item=False):
        return self.call('UpgradeQuality', **{'1': BASE, '2': int(use_item)})

    def spar(self, part=1):
        return self.call('UpgradeSparQuality', **{'1': BASE, '2': part})

    def feed(self, item=95536, num=1):
        return self.call('UpgradeLevel', **{'1': BASE, '2': item, '3': num})

    def snapshot(self, messages):
        outer = body(messages, SNAPSHOT_SC)
        self.assertIsNotNone(outer)
        dtos = g.parse_repeated_submessages(outer, 1)
        self.assertEqual(len(dtos), 1)
        self.assertEqual(g.parse_varint_fields(dtos[0])[1], BASE)
        parts = g.parse_repeated_submessages(dtos[0], 8)
        self.assertEqual(len(parts), 6)
        return g.parse_varint_fields(dtos[0]), [g.parse_varint_fields(x) for x in parts]

    def assert_refused(self, messages, ack=QUALITY_SC):
        self.assertIsNone(body(messages, ack), 'A rejected operation must not enter the client success handler')
        self.assertIsNotNone(body(messages, ERROR_SC))

    def test_all_authored_qualities_have_positive_level_caps_and_valid_successors(self):
        self.assertEqual(len(self.cfg['quality']), 20)
        for quality, entry in self.cfg['quality'].items():
            with self.subTest(quality=quality):
                self.assertGreater(entry['max_lv'], 0)
                if entry['next']:
                    self.assertIn(entry['next'], self.cfg['quality'])
                    self.assertGreater(self.cfg['quality'][entry['next']]['max_lv'], entry['max_lv'])
                    self.assertEqual({part for part, _, _ in entry['equip_need']}, set(range(1, 7)))
                for part, q, level in entry['equip_need']:
                    self.assertIn((part, q), self.cfg['equip'])
                    self.assertLessEqual(level, self.cfg['equip_quality'][q]['max_lv'])

    def test_no_stone_promotion_checks_current_gear_and_consumes_it(self):
        messages = self.promote()
        self.assertEqual(g.parse_varint_fields(body(messages, QUALITY_SC)), {1: BASE, 2: 0, 3: 101})
        self.assertEqual((self.row()['quality'], self.row()['spars']), (101, ''))
        full, parts = self.snapshot(messages)
        self.assertEqual(full[5], 101)
        self.assertTrue(all(x == {1: 0, 2: 0} for x in parts))

    def test_stone_is_charged_even_when_all_equipment_is_ready_and_keeps_gear(self):
        before = self.row()['spars']
        store.add_item(self.uin, 95539, 3)
        messages = self.promote(True)
        self.assertEqual(g.parse_varint_fields(body(messages, QUALITY_SC)), {1: BASE, 2: 1, 3: 101})
        self.assertEqual(store.get_item_count(self.uin, 95539), 2)
        self.assertEqual(self.row()['spars'], before)
        _, parts = self.snapshot(messages)
        self.assertTrue(all(x == {1: 10, 2: 1} for x in parts))

    def test_missing_stone_has_no_success_ack_and_keeps_all_equipment(self):
        before = self.row()
        messages = self.promote(True)
        self.assert_refused(messages)
        self.assertEqual(self.row(), before)
        _, parts = self.snapshot(messages)
        self.assertTrue(all(x == {1: 10, 2: 1} for x in parts))

    def test_insufficient_equipment_refusal_cannot_clear_client_gear(self):
        store.save_puppet(self.uin, BASE, spars='1:9:1;2:10:1;3:10:1;4:10:1;5:10:1;6:10:1')
        before = self.row()
        messages = self.promote(False)
        self.assert_refused(messages)
        self.assertEqual(self.row(), before)
        _, parts = self.snapshot(messages)
        self.assertEqual(parts[0], {1: 9, 2: 1})

    def test_stone_does_not_bypass_the_original_equipment_prerequisite(self):
        store.save_puppet(self.uin, BASE, spars='')
        store.add_item(self.uin, 95539, 5)
        messages = self.promote(True)
        self.assert_refused(messages)
        self.assertEqual((self.row()['quality'], store.get_item_count(self.uin, 95539)), (100, 5))

    def test_no_stone_consumption_can_rebuild_only_white_gear_with_white_drawing(self):
        self.promote(False)
        store.add_item(self.uin, 95501, 1)
        store.add_item(self.uin, 95531, 5000)
        messages = self.spar()
        self.assertEqual(g.parse_varint_fields(body(messages, SPAR_SC)), {1: BASE, 2: 1, 3: 1, 4: 1})
        self.assertEqual(g.puppet_spars(self.row()), {1: (1, 1)})
        self.assertEqual(store.get_item_count(self.uin, 95501), 0)
        self.assertEqual(store.get_item_count(self.uin, 95531), 4500)
        messages = self.spar()
        self.assert_refused(messages, SPAR_SC)
        self.assertEqual(g.puppet_spars(self.row()), {1: (1, 1)})

    def test_missing_white_drawing_cannot_create_quality_zero_phantom_equipment(self):
        self.promote(False)
        store.add_item(self.uin, 95531, 5000)
        store.add_item(self.uin, 95502, 1)
        messages = self.spar()
        self.assert_refused(messages, SPAR_SC)
        self.assertEqual(self.row()['spars'], '')
        _, parts = self.snapshot(messages)
        self.assertTrue(all(x == {1: 0, 2: 0} for x in parts))
        self.assertEqual(store.get_item_count(self.uin, 95502), 1)

    def test_green_equipment_requires_its_green_drawing(self):
        self.promote(False)
        store.save_puppet(self.uin, BASE, spars='1:10:1')
        store.add_item(self.uin, 95531, 5000)
        before = self.row()
        messages = self.spar()
        self.assert_refused(messages, SPAR_SC)
        self.assertEqual(self.row(), before)
        self.assertEqual(store.get_item_count(self.uin, 95531), 5000)
        store.add_item(self.uin, 95502, 1)
        messages = self.spar()
        self.assertEqual(g.parse_varint_fields(body(messages, SPAR_SC))[3], 2)
        self.assertEqual(g.puppet_spars(self.row()), {1: (10, 2)})
        self.assertEqual(store.get_item_count(self.uin, 95502), 0)
        messages = self.call('UpgradeSparLevel', **{'1': BASE, '2': 1, '3': 1})
        self.assertEqual(g.puppet_spars(self.row()), {1: (11, 2)})

    def test_current_quality_caps_feeding_then_promotion_unlocks_further_levels(self):
        store.add_item(self.uin, 95536, 3)
        before = self.row()
        messages = self.feed()
        self.assertIsNotNone(body(messages, ERROR_SC))
        self.assertEqual(self.row(), before)
        self.assertEqual(store.get_item_count(self.uin, 95536), 3)
        self.promote(False)
        self.feed()
        self.assertGreater(self.row()['level'], 10)
        self.assertLessEqual(self.row()['level'], 20)
        self.assertEqual(store.get_item_count(self.uin, 95536), 2)

    def test_missing_cultivation_liquid_cannot_reset_client_level_or_experience(self):
        store.save_puppet(self.uin, BASE, quality=101, level=15, exp=37)
        before = self.row()
        messages = self.feed()
        self.assert_refused(messages, 'PVZ.Cmd.Cmd_Puppet_UpgradeLevel_SC')
        full, _ = self.snapshot(messages)
        self.assertEqual((full[3], full[2]), (15, 37))
        self.assertEqual(self.row(), before)

    def test_invalid_feed_item_cannot_reset_client_level_or_experience(self):
        store.save_puppet(self.uin, BASE, quality=101, level=15, exp=37)
        store.add_item(self.uin, 95539, 1)
        before = self.row()
        messages = self.feed(item=95539)
        self.assert_refused(messages, 'PVZ.Cmd.Cmd_Puppet_UpgradeLevel_SC')
        full, _ = self.snapshot(messages)
        self.assertEqual((full[3], full[2]), (15, 37))
        self.assertEqual(self.row(), before)
        self.assertEqual(store.get_item_count(self.uin, 95539), 1)

    def test_equipment_promotion_charges_only_the_original_recipe(self):
        store.save_puppet(self.uin, BASE, quality=101, level=20, spars='1:10:1')
        store.add_item(self.uin, 95502, 1)
        store.add_item(self.uin, 95531, 1000)
        self.p['coin'] = 0
        store.save_player(self.uin, coin=0)
        messages = self.spar()
        self.assertEqual(g.parse_varint_fields(body(messages, SPAR_SC)),
                         {1: BASE, 2: 1, 3: 2, 4: 10})
        self.assertEqual(g.puppet_spars(self.row()), {1: (10, 2)})
        self.assertEqual(store.get_item_count(self.uin, 95502), 0)
        self.assertEqual(store.get_item_count(self.uin, 95531), 0)
        self.assertEqual(self.p['coin'], 0)

    def test_bulk_experience_cannot_exceed_authored_quality_cap(self):
        store.save_puppet(self.uin, BASE, level=1)
        store.add_item(self.uin, 95536, 10000)
        self.feed(num=10000)
        self.assertEqual(self.row()['level'], 10)

    def test_town_level_rejection_preserves_experience_and_cultivation_liquid(self):
        store.save_puppet(self.uin, BASE, level=15, quality=101)
        self.p['level'] = self.cfg['levels'][16]['role_lv'] - 1
        store.save_player(self.uin, level=self.p['level'])
        store.add_item(self.uin, 95536, 1)
        before = self.row()
        messages = self.feed()
        self.assertIsNotNone(body(messages, ERROR_SC))
        self.assertEqual(self.row(), before)
        self.assertEqual(store.get_item_count(self.uin, 95536), 1)

    def test_unknown_and_max_quality_rejections_have_no_success_ack(self):
        for quality in (999, 505):
            with self.subTest(quality=quality):
                store.save_puppet(self.uin, BASE, quality=quality)
                before = self.row()
                messages = self.promote(False)
                self.assert_refused(messages)
                self.assertEqual(self.row(), before)

    def test_missing_puppet_or_invalid_part_cannot_create_a_phantom_owned_puppet(self):
        messages = self.call('UpgradeQuality', **{'1': 123456, '2': 0})
        self.assert_refused(messages)
        messages = self.call('UpgradeSparQuality', **{'1': 123456, '2': 1})
        self.assert_refused(messages, SPAR_SC)
        self.assertIsNone(store.get_puppet(self.uin, 123456))
        before = self.row()
        messages = self.spar(7)
        self.assert_refused(messages, SPAR_SC)
        self.assertEqual(self.row(), before)

    def test_stone_consumption_and_quality_write_roll_back_together(self):
        store.add_item(self.uin, 95539, 2)
        before = self.row()
        player_before = copy.deepcopy(self.p)
        with patch.object(store, 'save_puppet', side_effect=RuntimeError('injected save failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected save failure'):
                self.promote(True)
        self.assertEqual(store.get_item_count(self.uin, 95539), 2)
        self.assertEqual(self.row(), before)
        self.assertEqual(self.p, player_before)

    def test_successful_write_rolls_back_if_dependent_formation_refresh_fails(self):
        store.add_item(self.uin, 95539, 2)
        before = self.row()
        with patch.object(g, '_puppet_deployment_signature', side_effect=[(0,), (1,)]), \
             patch.object(g, 'handle_battle_getformation', side_effect=RuntimeError('injected refresh failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected refresh failure'):
                self.promote(True)
        self.assertEqual(store.get_item_count(self.uin, 95539), 2)
        self.assertEqual(self.row(), before)

    def test_second_promotion_costs_current_quality_stones_and_keeps_green_gear(self):
        store.save_puppet(self.uin, BASE, quality=101, level=20, spars=self.gear(101))
        store.add_item(self.uin, 95539, 4)
        before_gear = self.row()['spars']
        messages = self.promote(True)
        self.assertEqual(g.parse_varint_fields(body(messages, QUALITY_SC))[3], 200)
        self.assertEqual(store.get_item_count(self.uin, 95539), 2)
        self.assertEqual(self.row()['spars'], before_gear)
        store.add_item(self.uin, 95536, 1)
        self.feed()
        self.assertGreater(self.row()['level'], 20)
        self.assertLessEqual(self.row()['level'], 30)

    def test_existing_overshoot_is_preserved_and_unlocked_by_a_valid_promotion(self):
        store.save_puppet(self.uin, BASE, level=11, exp=25)
        store.add_item(self.uin, 95533, 1)
        before = self.row()
        self.feed(item=95533)
        self.assertEqual(self.row(), before)
        self.assertEqual(store.get_item_count(self.uin, 95533), 1)
        self.promote(False)
        self.feed(item=95533)
        self.assertEqual((self.row()['quality'], self.row()['level']), (101, 11))
        self.assertEqual(store.get_item_count(self.uin, 95533), 0)

    def test_registered_routes_have_the_existing_atomic_wrapper(self):
        for action, name in (('UpgradeQuality', 'handle_puppet_upgradequality'),
                             ('UpgradeLevel', 'handle_puppet_upgradelevel'),
                             ('UpgradeSparQuality', 'handle_puppet_upgradesparquality')):
            handler = getattr(g, name)
            self.assertTrue(hasattr(handler, '__wrapped__'))
            self.assertIs(g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Puppet_' + action + '_CS'], handler)

if __name__ == '__main__':
    try:
        result = unittest.main(verbosity=2, exit=False).result
    finally:
        store.close_thread_db()
        DB.cleanup()
    sys.exit(0 if result.wasSuccessful() else 1)
