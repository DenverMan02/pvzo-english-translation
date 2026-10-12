"""Original swap protocol regressions; all mutations use disposable SQLite.

Set PVZ_PUPPET_SOURCE to a staged pvz_poc_server.py to validate a candidate.
No game save, log, or Python bytecode is written inside the game directory.
"""
from pathlib import Path
import copy
import logging
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
SERVER = Path(r'D:\pvzol本地最新最新ol\pvzol本地\PvZ-Server-PoC')
WORK = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get('PVZ_PUPPET_SOURCE', str(SERVER / 'pvz_poc_server.py')))
sys.path.insert(0, str(SERVER))
import player_store as store

DB = tempfile.TemporaryDirectory(prefix='pvz-puppet-swap-', dir=WORK)
store.set_db_path(str(Path(DB.name) / 'test.db'))
store.init_db()
g = types.ModuleType('pvz_puppet_swap_under_test')
g.__file__ = str(SERVER / 'pvz_poc_server.py')
sys.modules[g.__name__] = g
source = SOURCE.read_text(encoding='utf-8').replace(
    'LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_runtime.log")',
    'LOG_PATH = ' + repr(str(WORK / 'test-runtime.log')))
exec(compile(source, str(SOURCE), 'exec'), g.__dict__)
g.log.setLevel(logging.WARNING)
g._CLIENTDB_PATH = str(SERVER.parent / 'webroot' / 'clientDB.txt')
g._DUMMY = None

SRC, DST = 1000001, 1000002
SWAP_SC = 'PVZ.Cmd.Cmd_Puppet_Swap_SC'
ERROR_SC = 'PVZ.Cmd.Cmd_LogicError_SC'
ITEM_SC = 'PVZ.Cmd.Cmd_Item_Update_SC'


def body(messages, name):
    return next((payload for actual, payload in messages if actual == name), None)


class PuppetSwapTests(unittest.TestCase):
    seq = 0

    def setUp(self):
        type(self).seq += 1
        self.p, _ = store.get_or_create_player(97002500 + self.seq)
        self.uin = self.p['uin']
        self.p.update(level=200, coin=1000000000, puppet_granted=1)
        store.save_player(self.uin, level=200, coin=self.p['coin'], puppet_granted=1)
        self.cfg = g.load_dummy_config()
        for base, level, exp, quality, star in ((SRC, 60, 100, 301, 2),
                                               (DST, 40, 5, 300, 1)):
            info = self.cfg['zombies'][base]
            store.save_puppet(self.uin, base, level=level, exp=exp, quality=quality,
                              star=star, active_skill=info['active'], active_level=star,
                              passive_skill=info['passive'], passive_level=star,
                              spars='1:%d:1;2:%d:1' % (level, level))
        self.cost = self.cfg['quality'][301]['swap_cost']
        self.assertEqual(self.cost, [(95542, 2)])

    def call(self, src=SRC, dst=DST):
        data = g.pb_varint_field(1, src) + g.pb_varint_field(2, dst)
        return g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Puppet_Swap_CS'](data, self.p)

    def assert_refused(self, messages, code):
        self.assertIsNone(body(messages, SWAP_SC),
                          'A swap acknowledgement always triggers the original client success event')
        self.assertEqual(g.parse_varint_fields(body(messages, ERROR_SC)), {1: code})

    def test_missing_card_keeps_both_puppets_inventory_and_wallet(self):
        before_rows = copy.deepcopy(store.get_puppets(self.uin))
        before_items = copy.deepcopy(store.get_items(self.uin))
        before_wallet = self.p['coin']
        messages = self.call()
        self.assertEqual(store.get_puppets(self.uin), before_rows)
        self.assertEqual(store.get_items(self.uin), before_items)
        self.assertEqual(self.p['coin'], before_wallet)
        self.assert_refused(messages, 204)

    def test_enough_cards_swaps_only_authored_fields_and_synchronizes_deduction(self):
        store.add_item(self.uin, 95542, 3)
        before = copy.deepcopy(store.get_puppets(self.uin))
        messages = self.call()
        after = store.get_puppets(self.uin)
        exchanged = {'level', 'exp', 'quality', 'spars'}
        for a, b in ((SRC, DST), (DST, SRC)):
            self.assertEqual({k: after[a][k] for k in exchanged},
                             {k: before[b][k] for k in exchanged})
            self.assertEqual({k: v for k, v in after[a].items() if k not in exchanged},
                             {k: v for k, v in before[a].items() if k not in exchanged})
        self.assertEqual(store.get_item_count(self.uin, 95542), 1)
        self.assertEqual(g.parse_varint_fields(body(messages, SWAP_SC)), {1: SRC, 2: DST})
        self.assertIsNone(body(messages, ERROR_SC))
        sync = body(messages, ITEM_SC)
        self.assertIsNotNone(sync, 'The server must push the post-payment bag count')
        items = [g.parse_varint_fields(raw) for raw in g.parse_repeated_submessages(sync, 1)]
        self.assertTrue(any(item.get(2) == 95542 and item.get(3) == 1 for item in items), items)

    def test_partial_card_balance_cannot_swap_or_charge(self):
        store.add_item(self.uin, 95542, 1)
        before = copy.deepcopy(store.get_puppets(self.uin))
        messages = self.call()
        self.assertEqual(store.get_puppets(self.uin), before)
        self.assertEqual(store.get_item_count(self.uin, 95542), 1)
        self.assert_refused(messages, 204)

    def test_invalid_pair_has_no_success_ack_or_deduction(self):
        store.add_item(self.uin, 95542, 3)
        before = copy.deepcopy(store.get_puppets(self.uin))
        for pair in ((SRC, SRC), (SRC, 9999999)):
            with self.subTest(pair=pair):
                self.assert_refused(self.call(*pair), 12201)
                self.assertEqual(store.get_puppets(self.uin), before)
                self.assertEqual(store.get_item_count(self.uin, 95542), 3)

    def test_quality_refusal_has_no_success_ack_or_deduction(self):
        store.save_puppet(self.uin, SRC, quality=100)
        store.add_item(self.uin, 95542, 3)
        before = copy.deepcopy(store.get_puppets(self.uin))
        self.assert_refused(self.call(), 12214)
        self.assertEqual(store.get_puppets(self.uin), before)
        self.assertEqual(store.get_item_count(self.uin, 95542), 3)

    def test_unknown_quality_does_not_allow_a_free_swap(self):
        store.save_puppet(self.uin, DST, quality=999)
        store.add_item(self.uin, 95542, 3)
        before = copy.deepcopy(store.get_puppets(self.uin))
        self.assert_refused(self.call(), 12205)
        self.assertEqual(store.get_puppets(self.uin), before)
        self.assertEqual(store.get_item_count(self.uin, 95542), 3)

    def test_absent_authored_swap_price_is_refused(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['quality'][301]['swap_cost'] = []
        cfg['quality'][300]['swap_cost'] = []
        store.add_item(self.uin, 95542, 3)
        before = copy.deepcopy(store.get_puppets(self.uin))
        with patch.object(g, 'load_dummy_config', return_value=cfg):
            self.assert_refused(self.call(), 12205)
        self.assertEqual(store.get_puppets(self.uin), before)
        self.assertEqual(store.get_item_count(self.uin, 95542), 3)

    def test_equipment_swap_price_matches_original_maximum_of_each_sides_total(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['equip_quality'][1]['exchange'] = [(95542, 2)]
        cfg['equip_quality'][2]['exchange'] = [(95542, 3)]
        # Left equipment total 2 + 3 = 5; right 3 + 3 = 6.
        # Original client charges max(puppet 2, 1) + max(equipment 5, 6) = 8.
        store.save_puppet(self.uin, SRC, spars='1:10:1;2:20:2')
        store.save_puppet(self.uin, DST, spars='1:20:2;2:20:2')
        store.add_item(self.uin, 95542, 10)
        with patch.object(g, 'load_dummy_config', return_value=cfg):
            messages = self.call()
        self.assertIsNotNone(body(messages, SWAP_SC))
        self.assertEqual(store.get_item_count(self.uin, 95542), 2)

    def test_second_save_failure_rolls_back_first_puppet_and_card_deduction(self):
        store.add_item(self.uin, 95542, 3)
        before = copy.deepcopy(store.get_puppets(self.uin))
        before_player = copy.deepcopy(self.p)
        real = store.save_puppet

        def fail_second(uin, base, **values):
            if base == DST:
                raise RuntimeError('injected second-save failure')
            return real(uin, base, **values)

        with patch.object(store, 'save_puppet', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'injected second-save failure'):
                self.call()
        self.assertEqual(store.get_puppets(self.uin), before)
        self.assertEqual(store.get_item_count(self.uin, 95542), 3)
        self.assertEqual(self.p, before_player)

    def test_registered_swap_route_keeps_the_existing_atomic_wrapper(self):
        self.assertTrue(hasattr(g.handle_puppet_swap, '__wrapped__'))
        self.assertIs(g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Puppet_Swap_CS'], g.handle_puppet_swap)


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        for handler in list(g.log.handlers):
            handler.close()
            g.log.removeHandler(handler)
        store.close_thread_db()
        DB.cleanup()
