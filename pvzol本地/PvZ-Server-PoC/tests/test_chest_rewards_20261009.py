"""Real clientDB contracts, disposable SQLite inventory, and original protobuf DTOs."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
import chest_reward_repairs as repairs


class BackpackChestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_path = g.store.DB_PATH
        self.addCleanup(lambda: g.store.set_db_path(old_path))
        g.store.set_db_path(str(Path(self.tmp.name) / 'chest-test.db'))
        g.store.init_db()
        self.player, _ = g.store.get_or_create_player(88009101)
        g.store.save_player(self.player['uin'], level=100, energy=20, coin=100)
        self.player.update(level=100, energy=20, coin=100)

    def use(self, item_id, count=1):
        return g.handle_item_use(g.pb_varint_field(1, item_id) + g.pb_varint_field(2, count), self.player)

    def exchange(self, item_id, reward, count=1):
        return g.handle_item_exchange(g.pb_varint_field(1, item_id) + g.pb_varint_field(2, count)
                                      + g.pb_varint_field(3, reward), self.player)

    def stock(self, item_id, amount=1):
        g.store.add_item(self.player['uin'], item_id, amount)

    def count(self, item_id):
        return g.store.get_item_count(self.player['uin'], item_id)

    def opened(self, messages):
        for name, body in messages:
            if name in {'PVZ.Cmd.Cmd_Item_OpenGift_SC', 'PVZ.Cmd.Cmd_Item_OpenChest_SC',
                        'PVZ.Cmd.Cmd_Item_OpenGiftChestBatch_SC'}:
                field = 2 if name.endswith('OpenGift_SC') else 3
                return {f[1]: f[2] for f in (g.parse_varint_fields(dto)
                        for dto in g.parse_repeated_submessages(body, field))}
        return {}

    def test_crystal_bean_gift_uses_exact_source_ids_and_amounts(self):
        self.stock(3336)
        reply = self.use(3336)
        self.assertEqual(self.opened(reply), {2018: 30, 2004: 5})
        self.assertEqual((self.count(3336), self.count(2018), self.count(2004)), (0, 30, 5))
        self.assertEqual(self.count(2002), 0)
        self.assertEqual(self.player['coin'], 100)
        self.assertIn('PVZ.Cmd.Cmd_Item_OpenGift_SC', dict(reply))

    def test_plant_gift_creates_real_card_instance_and_allows_duplicates(self):
        self.stock(3338, 2)
        self.use(3338)
        reply = self.use(3338)
        self.assertEqual(self.opened(reply), {24303: 1, 2104: 1})
        cards = g.store.get_cards(self.player['uin'])
        card_id = int(repairs.config(g)['items'][24303]['cardItemId'].split(':')[0])
        self.assertEqual([card[1] for card in cards], [card_id, card_id])
        self.assertEqual(len({card[0] for card in cards}), 2)
        self.assertEqual(self.count(24303), 0)
        self.assertEqual(self.count(2104), 2)
        self.assertIn('PVZ.Cmd.Cmd_Card_SyncFullInfo_SC', dict(reply))
        self.assertEqual(g.parse_varint_fields(dict(reply)['PVZ.Cmd.Cmd_Card_SyncFullInfo_SC'])[4], 39)

    def test_backpack_random_plant_box_honors_star_and_adds_card(self):
        self.stock(3203)
        reply = self.use(3203)
        loot = self.opened(reply)
        self.assertEqual(len(loot), 1)
        iid = next(iter(loot))
        self.assertTrue(repairs.config(g)['items'][iid]['name'].endswith('(2星)'))
        self.assertEqual(loot[iid], 1)
        self.assertEqual(len(g.store.get_cards(self.player['uin'])), 1)
        self.assertEqual(self.count(iid), 0)
        self.assertEqual(g.parse_varint_fields(dict(reply)['PVZ.Cmd.Cmd_Card_SyncFullInfo_SC'])[4], 40)

    def test_selected_plant_reward_uses_real_card_and_use_source(self):
        self.stock(4121)
        reply = self.exchange(4121, 28602)
        self.assertEqual(len(g.store.get_cards(self.player['uin'])), 1)
        self.assertEqual(self.count(28602), 0)
        self.assertEqual(g.parse_varint_fields(dict(reply)['PVZ.Cmd.Cmd_Card_SyncFullInfo_SC'])[4], 8)

    def test_selected_inferno_chest_grants_authored_200000_coins_only(self):
        self.stock(4091, 2)
        reply = self.exchange(4091, 1, 2)
        fields = g.parse_varint_fields(dict(reply)['PVZ.Cmd.Cmd_Item_Exchange_SC'])
        self.assertEqual(fields, {1: 4091, 2: 2, 3: 1, 4: 400000})
        self.assertEqual(self.player['coin'], 400100)
        self.assertEqual((self.count(4091), self.count(2002), self.count(2013)), (0, 0, 0))

    def test_all_inferno_selection_options_match_original_table(self):
        choices = repairs.config(g)['choices']
        for box in range(4091, 4095):
            for iid, amount in choices[box].items():
                with self.subTest(box=box, reward=iid):
                    self.stock(box)
                    before = self.player['coin'] if iid == 1 else self.count(iid)
                    reply = self.exchange(box, iid)
                    after = self.player['coin'] if iid == 1 else self.count(iid)
                    self.assertEqual(after - before, amount)
                    self.assertEqual(g.parse_varint_fields(dict(reply)['PVZ.Cmd.Cmd_Item_Exchange_SC'])[4], amount)

    def test_selection_chest_cannot_be_consumed_as_generic_chest(self):
        self.stock(4091)
        reply = self.use(4091)
        self.assertEqual(self.count(4091), 1)
        self.assertEqual(self.player['coin'], 100)
        self.assertEqual(self.opened(reply), {})
        self.assertEqual(g.parse_varint_fields(dict(reply)['PVZ.Cmd.Cmd_Item_Use_SC'])[2], 0)

    def test_invalid_selection_preserves_box(self):
        self.stock(4093)
        reply = self.exchange(4093, 2002)
        self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(reply))
        self.assertEqual(self.count(4093), 1)
        self.assertEqual(self.count(2002), 0)

    def test_selected_energy_updates_real_balance_instead_of_bag(self):
        self.stock(4028)
        before = g.current_energy(self.player)
        reply = self.exchange(4028, 9)
        self.assertEqual(self.player['energy'] - before, 50)
        self.assertEqual(self.count(9), 0)
        self.assertIn('PVZ.Cmd.Cmd_SyncEnergy_SC', dict(reply))

    def test_batch_gift_uses_source_counts_and_original_batch_response(self):
        self.stock(3105, 3)
        reply = self.use(3105, 3)
        self.assertEqual(self.opened(reply), {2102: 9, 54800: 60})
        self.assertEqual(self.count(3105), 0)
        body = dict(reply)['PVZ.Cmd.Cmd_Item_OpenGiftChestBatch_SC']
        self.assertEqual(g.parse_varint_fields(body)[2], 3)

    def test_20_percent_optional_reward_is_not_unconditional(self):
        for item_id, guaranteed, optional in ((3663, (2103, 5), (2104, 1)),
                                               (3665, (2017, 5), (2017, 1))):
            for chance, expected in ((.1999, guaranteed[1] + optional[1] if guaranteed[0] == optional[0] else None), (.2, guaranteed[1])):
                with self.subTest(item=item_id, chance=chance), patch.object(repairs.random, 'random', return_value=chance):
                    self.stock(item_id)
                    loot = self.opened(self.use(item_id))
                    if guaranteed[0] == optional[0]:
                        self.assertEqual(loot, {guaranteed[0]: expected})
                    else:
                        self.assertEqual(loot.get(optional[0], 0), 1 if chance < .2 else 0)
                        self.assertEqual(loot[guaranteed[0]], guaranteed[1])

    def test_quarter_probability_matches_equipment_and_puppet_materials(self):
        for box, (iid, amount) in repairs.QUARTER_BONUSES.items():
            for chance, multiplier in ((.2499, 2), (.25, 1)):
                with self.subTest(item=box, chance=chance), patch.object(repairs.random, 'random', return_value=chance):
                    self.stock(box)
                    self.assertEqual(self.opened(self.use(box)), {iid: amount * multiplier})

    def test_snowman_exact_20_percent_triple_and_30_percent_double(self):
        for chance, multiplier in ((0, 3), (.1999, 3), (.2, 2), (.4999, 2), (.5, 1), (.9999, 1)):
            with self.subTest(chance=chance), patch.object(repairs.random, 'random', return_value=chance):
                # These event-only boxes have isUse=0 in original ItemInfo;
                # check the recovered roll while respecting backpack usability.
                self.assertEqual(repairs.roll(g, 3641), [(2130, 20 * multiplier)])

    def test_event_only_snowman_box_remains_unusable_from_bag(self):
        self.stock(3641)
        self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(self.use(3641)))
        self.assertEqual(self.count(3641), 1)

    def test_energy_stone_and_alloy_boxes_honor_source_ranges(self):
        for box, iid, low, high in ((91000, 92001, 22, 32), (91006, 92004, 28, 38),
                                    (91506, 98904, 6, 8), (91510, 98904, 10, 12)):
            for boundary in (low, high):
                with self.subTest(box=box, boundary=boundary), patch.object(repairs.random, 'randint', return_value=boundary):
                    self.stock(box)
                    self.assertEqual(self.opened(self.use(box)), {iid: boundary})

    def test_blueprint_chest_honors_quality_and_quarter_bonus(self):
        for chance, total in ((.2499, 2), (.25, 1)):
            with patch.object(repairs.random, 'random', return_value=chance):
                self.stock(4057)
                loot = self.opened(self.use(4057))
                self.assertEqual(sum(loot.values()), total)
                self.assertTrue(all(repairs.config(g)['items'][iid]['quality'] == '3'
                                    and repairs.config(g)['items'][iid]['name'].endswith('图纸')
                                    for iid in loot))

    def test_explicit_rare_plant_pool_never_yields_other_plants(self):
        for box, allowed in ((3728, {'香蒲', '椰子加农炮', '火爆辣椒', '海葵花', '电海松', '海星果'}),
                             (3729, {'钻地荔枝果', '食人花', '氧气藻', '灵芝'})):
            self.stock(box)
            loot = self.opened(self.use(box))
            self.assertEqual(len(loot), 1)
            self.assertTrue(repairs.config(g)['items'][next(iter(loot))]['name'][:-4] in allowed)

    def test_every_usable_fixed_source_gift_has_real_rewards(self):
        data = repairs.config(g)
        for item_id, source_loot in data['fixed'].items():
            item = data['items'].get(item_id)
            if not item or item.get('isUse') != '1' or repairs.contract(g, item_id)[0] != 'fixed':
                continue
            with self.subTest(item=item_id):
                self.stock(item_id)
                expected = dict(repairs.FIXED_OVERRIDES.get(item_id, source_loot))
                reply = self.use(item_id)
                self.assertEqual(self.count(item_id), 0)
                self.assertEqual(self.opened(reply), expected)

    def test_every_finite_authored_random_contract_opens_real_items(self):
        data = repairs.config(g)
        for item_id, item in data['items'].items():
            if item.get('isUse') != '1' or repairs.contract(g, item_id)[0] != 'uniform':
                continue
            with self.subTest(item=item_id):
                self.stock(item_id)
                reply = self.use(item_id)
                self.assertEqual(self.count(item_id), 0)
                self.assertTrue(self.opened(reply))
                self.assertTrue(all(iid in data['items'] and amount > 0
                                    for iid, amount in self.opened(reply).items()))

    def test_every_selection_source_option_is_valid(self):
        data = repairs.config(g)
        for box, choices in data['choices'].items():
            for reward_id, amount in choices.items():
                with self.subTest(box=box, reward=reward_id):
                    self.stock(box)
                    before = self.count(box)
                    reply = self.exchange(box, reward_id)
                    self.assertEqual(self.count(box), before - 1)
                    body = dict(reply).get('PVZ.Cmd.Cmd_Item_Exchange_SC')
                    self.assertIsNotNone(body)
                    self.assertEqual(g.parse_varint_fields(body)[4], amount)

    def test_finite_puppet_capture_bundle_keeps_source_item_types(self):
        self.stock(4074)
        with patch.object(repairs.random, 'choice', return_value=100):
            reply = self.use(4074)
        self.assertEqual(self.opened(reply), {95537: 5, 95533: 100})
        self.assertEqual((self.count(95537), self.count(95533)), (5, 100))

    def test_stale_fixed_table_corrections_use_unique_current_item_names(self):
        for box, expected in ((3372, {3443: 1, 4029: 1}),
                              (4087, {4115: 1, 4028: 1, 4027: 1, 1047: 1})):
            self.stock(box)
            self.assertEqual(self.opened(self.use(box)), expected)

    def test_unknown_online_pool_preserves_box_and_inventory(self):
        for box in (3384, 3001, 3848, 4124):
            with self.subTest(item=box):
                self.stock(box)
                self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(self.use(box)))
                self.assertEqual(self.count(box), 1)
                self.assertEqual(self.player['coin'], 100)
                self.assertEqual(self.count(2002), 0)

    def test_reward_failure_rolls_back_currency_inventory_and_cards(self):
        self.stock(3338)
        with patch.object(g, 'grant_fuben_reward', side_effect=RuntimeError('injected failure')):
            with self.assertRaises(RuntimeError):
                self.use(3338)
        self.assertEqual(self.count(3338), 1)
        self.assertEqual(self.count(2104), 0)
        self.assertEqual(g.store.get_cards(self.player['uin']), [])

    def test_exchange_failure_rolls_back_consumed_chest(self):
        self.stock(4091)
        with patch.object(g, 'grant_fuben_reward', side_effect=RuntimeError('injected failure')):
            with self.assertRaises(RuntimeError):
                self.exchange(4091, 1)
        self.assertEqual(self.count(4091), 1)
        self.assertEqual(self.player['coin'], 100)

    def test_final_coin_overflow_preserves_chest_and_saved_balance(self):
        before = 4294967295 - 100000
        g.store.save_player(self.player['uin'], coin=before)
        self.player['coin'] = before
        self.stock(4091)
        reply = self.exchange(4091, 1)
        self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(reply))
        self.assertEqual(self.count(4091), 1)
        self.assertEqual(self.player['coin'], before)
        self.assertNotIn('PVZ.Cmd.Cmd_SyncCoin_SC', dict(reply))

    def test_final_bag_overflow_preserves_gift_and_stack(self):
        self.stock(2018, 4294967295 - 20)
        self.stock(3336)
        reply = self.use(3336)
        self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(reply))
        self.assertEqual(self.count(3336), 1)
        self.assertEqual(self.count(2018), 4294967295 - 20)
        self.assertEqual(self.count(2004), 0)

    def test_overstock_and_bad_count_requests_preserve_inventory(self):
        self.stock(3105, 2)
        for count in (0, 3, 1000):
            self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(self.use(3105, count)))
            self.assertEqual(self.count(3105), 2)

    def test_nonchest_uses_existing_consumable_handler(self):
        self.stock(2016)
        reply = self.use(2016)
        self.assertEqual(self.count(2016), 0)
        self.assertIn('PVZ.Cmd.Cmd_SyncEnergy_SC', dict(reply))


if __name__ == '__main__':
    unittest.main()
