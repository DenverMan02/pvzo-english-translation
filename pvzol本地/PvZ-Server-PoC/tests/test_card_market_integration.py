"""Tavern economic/protocol regressions; all state uses a disposable database."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SERVER = Path(os.environ.get('PVZ_TEST_SERVER_ROOT', str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(SERVER))
# Server adapters initialize schemas during import; isolate those too.
import player_store as _import_store
_IMPORT_TEMP = tempfile.TemporaryDirectory(prefix='pvz_tavern_import_')
_import_store.set_db_path(str(Path(_IMPORT_TEMP.name) / 'import.db'))
_import_store.init_db()
import pvz_poc_server as g


class MarketIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_tavern_integration_')
        self.old_db = g.store.DB_PATH
        g.store.set_db_path(str(Path(self.tmp.name) / 'test.db'))
        g.store.init_db()
        self.p, _ = g.store.get_or_create_player(88001010)
        self.save(coin=1234, present_ticket=10000, ticket=10000, friend_point=0,
                  card_market_single_draws=1, card_market_granted=1)
        g.load_battle_cards()

    def tearDown(self):
        g.store.set_db_path(self.old_db)
        self.tmp.cleanup()

    def save(self, **values):
        self.p.update(values)
        g.store.save_player(self.p['uin'], **values)

    def count(self, iid):
        return g.store.get_item_count(self.p['uin'], iid)

    def draw(self, gid, rewards):
        with patch.object(g, '_card_market_roll_rewards', return_value=rewards):
            return g.handle_card_extract(g.pb_varint_field(1, gid), self.p)

    def body(self, msgs, suffix):
        return next(body for name, body in msgs if name == 'PVZ.Cmd.' + suffix)

    def reveals(self, msgs):
        return [g.parse_varint_fields(raw) for raw in
                g.parse_repeated_submessages(self.body(msgs, 'Cmd_Card_Extract_SC'), 1)]

    def bean_updates(self, msgs):
        return [fields for name, body in msgs if name == 'PVZ.Cmd.Cmd_Item_Update_SC'
                for raw in g.parse_repeated_submessages(body, 1)
                for fields in [g.parse_varint_fields(raw)] if fields.get(2) == 2003]

    def big_rewards(self, rarity=1):
        plants = g.card_market_rules.reward_data(g)['plants']
        def plant(star, required=None):
            return next(bid for bid, cid in plants if g._BATTLE_CARDS[cid]['star'] == star
                        and (required is None or g._BATTLE_CARDS[cid]['rarity'] == required))
        frag = g.card_market_rules.reward_data(g)['rare_fragments'][0][0]
        return [(plant(3, rarity), 1), (plant(2), 1), (plant(1), 1), (frag, 2),
                (2103, 1), (210, 10), (1, 20000), (2003, 3), (2130, 2), (2002, 1)]

    def test_coin_reveal_and_fixed_bean_deposit_before_animation(self):
        before = self.count(2003)
        msgs = self.draw(1, [(1, 10000)])
        self.assertEqual(self.p['coin'], 11234)
        self.assertEqual(self.count(1), 0)
        self.assertEqual(self.count(2003), before + 1)
        self.assertEqual(self.reveals(msgs), [{1: 1, 2: 10000}])
        bonus = g.parse_repeated_submessages(self.body(msgs, 'Cmd_Card_Extract_SC'), 6)
        self.assertEqual(g.parse_varint_fields(bonus[0]), {1: 2003, 2: 1})
        names = [name for name, _ in msgs]
        self.assertLess(names.index('PVZ.Cmd.Cmd_Item_Update_SC'), names.index('PVZ.Cmd.Cmd_Card_Extract_SC'))
        self.assertEqual(self.bean_updates(msgs)[0][3], before + 1)
        self.assertEqual(len(g.store.get_cards(self.p['uin'])), 0)

    def test_random_beans_and_purchased_bean_merge_once(self):
        before = self.count(2003)
        msgs = self.draw(1, [(2003, 2)])
        self.assertEqual(self.count(2003), before + 3)
        self.assertEqual(len(self.bean_updates(msgs)), 1)
        self.assertEqual(self.bean_updates(msgs)[0][3], before + 3)
        self.assertEqual(self.reveals(msgs), [{1: 2003, 2: 2}])

    def test_big_pack_all_reveals_deposited_and_ten_beans_with_coupon(self):
        rewards = self.big_rewards(4)
        g.store.add_item(self.p['uin'], 2025, 10)
        before = {iid: self.count(iid) for iid, _ in rewards if iid != 1}
        bean_before = self.count(2003)
        msgs = self.draw(2, rewards)
        self.assertEqual(self.count(2025), 0)
        self.assertEqual(self.p['present_ticket'], 10000)
        self.assertEqual(self.p['ticket'], 10000)
        self.assertEqual(self.p['coin'], 21234)
        self.assertEqual(self.count(2003), bean_before + 13)
        self.assertEqual(len(self.bean_updates(msgs)), 1)
        self.assertEqual(len(g.store.get_cards(self.p['uin'])), 3)
        self.assertEqual(len(self.reveals(msgs)), 10)
        self.assertEqual(self.p['card_market_lucky_flower_num'], 0)
        self.assertEqual(g.parse_varint_fields(g.parse_repeated_submessages(
            self.body(msgs, 'Cmd_Card_Extract_SC'), 6)[0]), {1: 2003, 2: 10})
        cards = dict(g.card_market_pool())
        for iid, qty in rewards:
            if iid not in cards and iid not in (1, 2003):
                self.assertEqual(self.count(iid), before[iid] + qty)

    def test_paid_big_pack_charges_original_price_and_accumulates_lucky_flower(self):
        self.save(present_ticket=1000, ticket=1500, card_market_lucky_flower_num=4)
        self.draw(2, self.big_rewards(1))
        self.assertEqual((self.p['present_ticket'], self.p['ticket']), (0, 340))
        self.assertEqual(self.p['card_market_lucky_flower_num'], 5)
        reloaded, _ = g.store.get_or_create_player(self.p['uin'])
        info = self.body(g.handle_card_getmarketinfo(b'', reloaded), 'Cmd_Card_GetMarketInfo_SC')
        self.assertEqual(g.parse_varint_fields(info)[2], 5)

    def test_secondary_rare_plant_clears_lucky_flowers(self):
        rewards = self.big_rewards(1)
        rare_two = next(bid for bid, cid in g.card_market_rules.reward_data(g)['plants']
                        if g._BATTLE_CARDS[cid]['star'] == 2 and g._BATTLE_CARDS[cid]['rarity'] == 4)
        rewards[1] = (rare_two, 1)
        self.save(card_market_lucky_flower_num=7)
        self.draw(2, rewards)
        self.assertEqual(self.p['card_market_lucky_flower_num'], 0)

    def test_market_info_uses_persisted_counters_and_does_not_repeat_starter_tickets(self):
        self.save(card_market_single_draws=16, card_market_lucky_flower_num=5, card_market_granted=1)
        stale = dict(self.p, card_market_single_draws=0, card_market_lucky_flower_num=0, card_market_granted=0)
        before = self.count(2025)
        msgs = g.handle_card_getmarketinfo(b'', stale)
        info = g.parse_varint_fields(self.body(msgs, 'Cmd_Card_GetMarketInfo_SC'))
        self.assertEqual((info[2], info[5]), (5, 6))
        self.assertEqual(self.count(2025), before)

    def test_insufficient_balance_does_not_grant_or_advance(self):
        self.save(present_ticket=0, ticket=0)
        before = (g.store.get_cards(self.p['uin']), g.store.get_items(self.p['uin']), self.p['coin'])
        self.assertEqual(self.draw(2, self.big_rewards()), [])
        self.assertEqual((g.store.get_cards(self.p['uin']), g.store.get_items(self.p['uin']), self.p['coin']), before)
        self.assertEqual(self.p['card_market_lucky_flower_num'], 0)

    def test_failure_rolls_back_payment_cards_items_and_counters(self):
        before = copy.deepcopy(self.p)
        items = g.store.get_items(self.p['uin'])
        cards = g.store.get_cards(self.p['uin'])
        original_grant = g.grant_fuben_reward
        def failing_grant(player, rewards):
            original_grant(player, rewards)
            raise RuntimeError('simulated reward failure')
        with patch.object(g, 'grant_fuben_reward', side_effect=failing_grant):
            with self.assertRaisesRegex(RuntimeError, 'simulated'):
                self.draw(2, self.big_rewards())
        self.assertEqual(self.p, before)
        self.assertEqual(g.store.get_items(self.p['uin']), items)
        self.assertEqual(g.store.get_cards(self.p['uin']), cards)
        reloaded, _ = g.store.get_or_create_player(self.p['uin'])
        self.assertEqual(reloaded['present_ticket'], before['present_ticket'])
        self.assertEqual(reloaded['card_market_lucky_flower_num'], 0)

    def test_friend_rewards_do_not_buy_beans_or_charge_points_when_free(self):
        before = self.count(2003)
        msgs = self.draw(3, [(51200, 2), (1, 10000), (2002, 1), (2130, 1), (2015, 1)])
        self.assertEqual(self.count(2003), before)
        self.assertEqual(self.p['friend_point'], 0)
        self.assertEqual(len(self.reveals(msgs)), 5)
        self.assertEqual(g.parse_repeated_submessages(self.body(msgs, 'Cmd_Card_Extract_SC'), 6), [])

    def test_duplicate_plant_is_another_card_and_small_pity_progress_wraps(self):
        plant = g.card_market_rules.reward_data(g)['first'][0]
        self.save(card_market_single_draws=9)
        first = self.draw(1, [plant])
        self.assertEqual(g.parse_varint_fields(self.body(first, 'Cmd_Card_Extract_SC'))[5], 0)
        second = self.draw(1, [plant])
        cards = g.store.get_cards(self.p['uin'])
        self.assertEqual(len(cards), 2)
        self.assertEqual(len({row[0] for row in cards}), 2)
        self.assertEqual(cards[0][1], cards[1][1])
        self.assertEqual(g.parse_varint_fields(self.body(second, 'Cmd_Card_Extract_SC'))[5], 1)
        self.assertEqual(self.p['card_market_single_draws'], 11)

    def test_free_claim_is_rolled_back_on_reward_failure(self):
        with patch.object(g, 'grant_fuben_reward', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):
                self.draw(3, [(51200, 1)] * 5)
        reloaded, _ = g.store.get_or_create_player(self.p['uin'])
        self.assertEqual(reloaded['card_market_friend_free_used'], 0)
        self.assertEqual(reloaded['card_market_friend_next_free_at'], 0)
        self.assertEqual(self.count(51200), 0)


if __name__ == '__main__':
    try:
        result = unittest.main(verbosity=2, exit=False).result
    finally:
        _import_store.close_thread_db()
        _IMPORT_TEMP.cleanup()
    sys.exit(0 if result.wasSuccessful() else 1)
