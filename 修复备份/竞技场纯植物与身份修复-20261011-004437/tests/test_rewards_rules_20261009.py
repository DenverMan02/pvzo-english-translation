"""Gameplay regressions against original clientDB and a disposable SQLite DB."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
import chest_reward_repairs as chest
import rewards_rules_repairs_20261009 as repairs

repairs.install(g)


class ReportedRewardsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = g.store.DB_PATH
        self.addCleanup(lambda: g.store.set_db_path(old))
        g.store.set_db_path(str(Path(self.tmp.name) / 'rewards.db'))
        g.store.init_db()
        self.player, _ = g.store.get_or_create_player(88009711)
        self.save(level=100, coin=100, present_ticket=0, ticket=500, friend_point=10)

    def save(self, **values):
        g.store.save_player(self.player['uin'], **values)
        self.player.update(values)

    def stock(self, iid, amount):
        g.store.add_item(self.player['uin'], iid, amount)

    def count(self, iid):
        return g.store.get_item_count(self.player['uin'], iid)

    def use(self, iid, amount=1):
        return g.handle_item_use(g.pb_varint_field(1, iid) + g.pb_varint_field(2, amount), self.player)

    def buy(self, amount=1, iid=113):
        return g.handle_shop_buyitem(g.pb_varint_field(1, iid) + g.pb_varint_field(2, amount), self.player)

    def shop_buy(self, eid):
        return g.handle_exchangestore_exchange(g.pb_varint_field(1, eid), self.player)

    def damage_town(self, durability=10, at=0):
        repairs._state(g, self.player, 'town_durability')
        repairs._save(g, self.player, 'town_durability', {'durability': durability, 'rebuild_at': at})

    def test_magic_shop_purchase_disables_row_and_repeat_does_not_spend(self):
        entry = g.plant_coin_shop_page(self.player)[0]
        eid, _, iid, count, coin, price = entry
        self.stock(coin, price * 3)
        self.shop_buy(eid)
        before = (self.count(coin), self.count(iid))
        self.assertEqual(before, (price * 2, count))
        messages = self.shop_buy(eid)
        self.assertEqual((self.count(coin), self.count(iid)), before)
        dto = next(x for x in g.parse_repeated_submessages(dict(messages)['PVZ.Cmd.Cmd_ExchangeStore_ItemList_SC'], 1)
                   if g.parse_varint_fields(x)[1] == eid)
        self.assertEqual(g.parse_varint_fields(dto)[7], 1)

    def test_shop_page_cycle_and_relogin_preserve_purchase_flag(self):
        entry = g.plant_coin_shop_page(self.player)[0]
        self.stock(entry[4], entry[5] * 3)
        self.shop_buy(entry[0])
        for _ in range(g.plant_coin_shop_page_count()):
            g.handle_exchangestore_refresh(g.pb_varint_field(1, 5), self.player)
        self.player = g.store.get_or_create_player(self.player['uin'])[0]
        self.assertIn(entry[0], repairs.plant_shop_bought(g, self.player))
        self.shop_buy(entry[0])
        self.assertEqual(self.count(entry[2]), entry[3])

    def test_magic_shop_rolls_over_like_island_shop_next_day(self):
        entry = g.plant_coin_shop_page(self.player)[0]
        self.stock(entry[4], entry[5] * 2)
        with patch.object(g, '_zi_today', return_value='20261009'):
            self.shop_buy(entry[0])
        with patch.object(g, '_zi_today', return_value='20261010'):
            self.shop_buy(entry[0])
        self.assertEqual(self.count(entry[2]), entry[3] * 2)

    def test_purchase_grant_and_flag_roll_back_together(self):
        entry = g.plant_coin_shop_page(self.player)[0]
        self.stock(entry[4], entry[5])
        repairs.plant_shop_bought(g, self.player)
        with patch.object(repairs, '_save', side_effect=RuntimeError('write failure')):
            with self.assertRaises(RuntimeError):
                self.shop_buy(entry[0])
        self.assertEqual((self.count(entry[4]), self.count(entry[2])), (entry[5], 0))

    def test_fixed_box_batch_pays_exact_source_amounts(self):
        self.stock(3336, 3)
        messages = self.use(3336, 3)
        self.assertEqual((self.count(3336), self.count(2018), self.count(2004)), (0, 90, 15))
        self.assertEqual(g.parse_varint_fields(dict(messages)['PVZ.Cmd.Cmd_Item_OpenGiftChestBatch_SC'])[2], 3)
        self.assertEqual(g.SERVER_CONFIG_OVERRIDES['iBatchOpenChestGiftLimit'], '999')

    def test_fixed_box_999_allowed_and_1000_refused_without_spending(self):
        self.stock(3336, 1000)
        self.use(3336, 1000)
        self.assertEqual((self.count(3336), self.count(2018)), (1000, 0))
        self.use(3336, 999)
        self.assertEqual((self.count(3336), self.count(2018), self.count(2004)), (1, 29970, 4995))

    def test_choice_box_999_allowed_and_1000_refused(self):
        self.stock(4091, 1000)
        request = lambda n: g.pb_varint_field(1, 4091) + g.pb_varint_field(2, n) + g.pb_varint_field(3, 1)
        g.handle_item_exchange(request(1000), self.player)
        self.assertEqual((self.count(4091), self.player['coin']), (1000, 100))
        g.handle_item_exchange(request(999), self.player)
        self.assertEqual((self.count(4091), self.player['coin']), (1, 199800100))

    def test_gm_trial_box_uses_real_giftpackage_instead_of_one_coin(self):
        self.stock(3601, 2)
        self.use(3601, 2)
        self.assertEqual((self.count(3601), self.count(2020), self.count(2003), self.player['coin']),
                         (0, 160, 4, 120100))

    def test_trial_claim_matches_gm_box_and_prevents_repeat_in_both_modes(self):
        for hard in (False, True):
            key = 'tr_hard_max' if hard else 'tr_max'
            self.save(**{key: 5})
            handler = g.handle_newscrewup_takereward if hard else g.handle_screwup_takereward
            before = self.player['coin']
            reply = handler(g.pb_varint_field(1, 5), self.player)
            self.assertEqual(self.player['coin'], before + 60000)
            rewards = g.parse_repeated_submessages(reply[0][1], 2)
            self.assertEqual({g.parse_varint_fields(dto)[2]:g.parse_varint_fields(dto)[3] for dto in rewards},
                             {2020:80,2003:2,1:60000})
            self.assertEqual(handler(g.pb_varint_field(1, 5), self.player), [])

    def arena(self, won):
        from contextlib import ExitStack
        with ExitStack() as stack:
            values = {'_daily_quest_touch': [], 'build_player_units': [{'pos': 3}], '_npc_encounter': {'units': [{'pos': 4}], 'name': 'test'},
                      'run_battle': (won, 1, 1, []), 'build_fight_report': b'', '_store_battle_report': 11,
                      '_arena_recorder': b'', '_auto_puppets': [], '_auto_puppet_list': b''}
            for name, value in values.items():
                stack.enter_context(patch.object(g, name, return_value=value))
            stack.enter_context(patch.object(g.bots_local, 'pick_opponent', return_value={'uin': 900001}))
            stack.enter_context(patch.object(g.bots_local, 'bots', return_value=[{'uin': 900001, 'name': 'test'}]))
            stack.enter_context(patch.object(g.bots_local, 'apply_match_result'))
            return g.handle_rankarena_challenge(b'', self.player)

    def test_ladder_win_grants_two_friend_points_and_syncs_wallet(self):
        reply = self.arena(True)
        self.assertEqual(self.player['friend_point'], 12)
        self.assertEqual(g.store.get_or_create_player(self.player['uin'])[0]['friend_point'], 12)
        self.assertEqual(g.parse_varint_fields(dict(reply)['PVZ.Cmd.Cmd_SyncFriendPoint_SC'])[1], 12)

    def test_ladder_loss_and_exhausted_attempt_give_no_friend_points(self):
        self.arena(False)
        self.assertEqual(self.player['friend_point'], 10)
        g._save_json_state(self.player, 'rank_arena_data', {'day':g._zi_today(), 'challenges':10})
        self.arena(True)
        self.assertEqual(self.player['friend_point'], 10)

    def test_town_purchase_uses_source_price_and_inventory_precedes_callback(self):
        self.save(present_ticket=100, ticket=100)
        reply = self.buy()
        self.assertEqual((self.count(2051), self.player['present_ticket'], self.player['ticket']), (1, 0, 50))
        self.assertEqual(reply[-1][0], 'PVZ.Cmd.Cmd_Shop_BuyItem_SC')
        self.assertEqual(g.mall_catalog()[113], (2051, 150, False, 0))

    def test_town_shortage_and_invalid_buy_never_grant_or_charge(self):
        self.save(present_ticket=0, ticket=149)
        self.assertEqual(self.buy(), [])
        self.assertEqual(self.buy(1001), [])
        self.assertEqual((self.count(2051), self.player['ticket']), (0, 149))

    def test_rebuild_restores_100_and_enforces_ten_minutes(self):
        self.damage_town()
        self.stock(2051, 3)
        with patch.object(repairs.time, 'time', return_value=2000000000):
            reply = self.use(2051)
            self.assertEqual(g.parse_varint_fields(dict(reply)['PVZ.Cmd.Cmd_City_GetTownDurability_SC']),
                             {1:100,2:100,3:600})
            self.assertEqual(self.count(2051), 2)
            self.damage_town(40, 2000000000)
            self.use(2051)
            self.assertEqual(self.count(2051), 2)
        with patch.object(repairs.time, 'time', return_value=2000000600):
            self.use(2051)
            self.assertEqual(self.count(2051), 1)
            self.assertEqual(repairs.town_state(g, self.player)['durability'], 100)

    def test_rebuild_full_town_missing_token_or_batch_never_spends(self):
        self.stock(2051, 2)
        self.use(2051)
        self.assertEqual(self.count(2051), 2)
        self.damage_town()
        self.use(2051, 2)
        self.assertEqual(self.count(2051), 2)
        self.stock(2051, -2)
        self.use(2051)
        self.assertEqual(repairs.town_state(g, self.player)['durability'], 10)

    def test_durability_request_and_login_return_persistent_real_state(self):
        self.damage_town(42)
        command = 'PVZ.Cmd.Cmd_City_GetTownDurability_CS'
        reply = g.REQUEST_HANDLERS[command](b'', self.player)
        self.assertEqual(g.parse_varint_fields(reply[0][1]), {1:42,2:100,3:0})
        body = repairs._replace_init_durability(g, g.pb_string(1, 'unchanged') + g.pb_varint_field(20, 100)
                                              + g.pb_varint_field(21, 100) + g.pb_varint_field(22, 123), 42)
        self.assertEqual(g.parse_varint_fields(body), {20:42,21:100,22:123})
        self.assertTrue(body.startswith(g.pb_string(1, 'unchanged')))


if __name__ == '__main__':
    unittest.main()
