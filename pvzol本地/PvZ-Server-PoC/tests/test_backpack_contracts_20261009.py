"""All shipped backpack contracts, including missing online drop policies.

Tests exercise the real protobuf handler against disposable SQLite databases.
"""
from collections import Counter
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
import chest_reward_repairs as repairs


class BackpackContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_path = g.store.DB_PATH
        self.addCleanup(lambda: g.store.set_db_path(old_path))
        g.store.set_db_path(str(Path(self.tmp.name) / 'contracts.db'))
        g.store.init_db()
        self.player, _ = g.store.get_or_create_player(88009119)
        g.store.save_player(self.player['uin'], level=300, energy=100, coin=100)
        self.player.update(level=300, energy=100, coin=100)

    def stock(self, iid, count=1):
        g.store.add_item(self.player['uin'], iid, count)

    def use(self, iid, count=1):
        return g.handle_item_use(g.pb_varint_field(1, iid) + g.pb_varint_field(2, count), self.player)

    def count(self, iid):
        return g.store.get_item_count(self.player['uin'], iid)

    def opened(self, messages):
        for name, body in messages:
            if name in {'PVZ.Cmd.Cmd_Item_OpenGift_SC', 'PVZ.Cmd.Cmd_Item_OpenChest_SC',
                        'PVZ.Cmd.Cmd_Item_OpenGiftChestBatch_SC'}:
                field = 2 if name.endswith('OpenGift_SC') else 3
                return {f[1]: f[2] for f in (g.parse_varint_fields(dto)
                        for dto in g.parse_repeated_submessages(body, field))}
        return {}

    def test_reported_new_legendary_box_has_three_independent_rolls(self):
        self.stock(3447)
        with patch.object(repairs.random, 'random', return_value=.5), \
                patch.object(repairs.random, 'randint', return_value=10), \
                patch.object(repairs.random, 'choice', wraps=repairs.random.choice) as choice:
            reply = self.use(3447)
        self.assertEqual(choice.call_count, 3)
        self.assertEqual(self.opened(reply), {211: 30})
        self.assertEqual(self.count(3447), 0)
        self.assertEqual(self.count(211), 30)
        self.assertEqual(g.parse_varint_fields(dict(reply)['PVZ.Cmd.Cmd_Item_Use_SC']), {1: 3447, 2: 1})

    def test_new_legendary_batch_runs_three_draws_per_box(self):
        self.stock(3447, 4)
        with patch.object(repairs.random, 'random', return_value=.5), \
                patch.object(repairs.random, 'randint', return_value=10), \
                patch.object(repairs.random, 'choice', wraps=repairs.random.choice) as choice:
            reply = self.use(3447, 4)
        self.assertEqual(choice.call_count, 12)
        self.assertEqual(self.opened(reply), {211: 120})
        self.assertEqual(self.count(3447), 0)
        self.assertEqual(g.parse_varint_fields(dict(reply)['PVZ.Cmd.Cmd_Item_OpenGiftChestBatch_SC'])[2], 4)

    def test_rare_plant_rolls_become_three_real_cards(self):
        self.stock(3447)
        with patch.object(repairs.random, 'random', return_value=0):
            reply = self.use(3447)
        cards = g.store.get_cards(self.player['uin'])
        self.assertEqual(len(cards), 3)
        self.assertEqual(len({row[0] for row in cards}), 3)
        self.assertEqual(sum(self.opened(reply).values()), 3)
        for iid in self.opened(reply):
            self.assertEqual(self.count(iid), 0)
            self.assertIn(iid, repairs._plant_pool(g, repairs.config(g), 3, {4}))

    def test_all_614_templates_are_exercised_without_silent_consumption(self):
        data = repairs.config(g)
        templates = {iid: row for iid, row in data['items'].items() if repairs._chest(row)}
        self.assertEqual(len(templates), 614)
        counts = Counter()
        for iid, row in templates.items():
            status, reason = repairs.contract(g, iid)
            with self.subTest(item=iid, name=row['name'], status=status):
                counts[status] += 1
                self.stock(iid)
                before = self.count(iid)
                messages = self.use(iid)
                after = self.count(iid)
                reveals = [(name, body) for name, body in messages if name in {
                    'PVZ.Cmd.Cmd_Item_OpenGift_SC', 'PVZ.Cmd.Cmd_Item_OpenChest_SC',
                    'PVZ.Cmd.Cmd_Item_OpenGiftChestBatch_SC'}]
                self.assertEqual(sum(name == 'PVZ.Cmd.Cmd_Item_Use_SC' for name, _ in messages), 1)
                if row['isUse'] != '1' or status in {'blocked', 'select'}:
                    self.assertFalse(reveals)
                    self.assertEqual(after, before, reason)
                    self.assertFalse(self.opened(messages))
                    self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(messages))
                    self.assertEqual(g.parse_varint_fields(dict(messages)['PVZ.Cmd.Cmd_Item_Use_SC'])[2], 0)
                else:
                    self.assertEqual(len(reveals), 1, reason)
                    if reveals[0][0] == 'PVZ.Cmd.Cmd_Item_OpenChest_SC':
                        self.assertEqual(g.parse_repeated_submessages(reveals[0][1], 2), [],
                                         'Actual loot must not appear in the pre-opening preview')
                    self.assertEqual(after, before - 1, reason)
                    self.assertTrue(self.opened(messages), reason)
                    self.assertTrue(repairs._validate(g, list(self.opened(messages).items())))
                    self.assertEqual(g.parse_varint_fields(dict(messages)['PVZ.Cmd.Cmd_Item_Use_SC'])[2], 1)
        self.assertEqual(sum(counts.values()), 614)

    def test_each_local_contract_rewards_and_all_bonus_branches_are_valid(self):
        data = repairs.config(g)
        for iid, row in data['items'].items():
            if not repairs._chest(row) or repairs.contract(g, iid)[0] != 'local':
                continue
            for chance in (0, .99999):
                with self.subTest(item=iid, chance=chance), patch.object(repairs.random, 'random', return_value=chance):
                    loot = repairs.roll(g, iid)
                    self.assertTrue(loot)
                    self.assertTrue(repairs._validate(g, loot))
                    if row['isUse'] == '1':
                        self.stock(iid)
                        before = self.count(iid)
                        self.assertTrue(self.opened(self.use(iid)))
                        self.assertEqual(self.count(iid), before - 1)

    def test_avocado_gift_uses_avocado_fragment_instead_of_stale_plant(self):
        self.stock(3781)
        with patch.object(repairs.random, 'random', return_value=.5), patch.object(repairs.random, 'randint', return_value=5):
            reply = self.use(3781)
        items = repairs.config(g)['items']
        loot = self.opened(reply)
        self.assertEqual(len(loot), 1)
        self.assertEqual(items[next(iter(loot))]['name'], '碎片-鳄梨')
        self.assertEqual(next(iter(loot.values())), 5)
        self.assertEqual(g.store.get_cards(self.player['uin']), [])

    def test_set_bundle_optional_plant_is_optional_and_guaranteed_plants_match_description(self):
        self.stock(3754, 2)
        with patch.object(repairs.random, 'random', return_value=.5):
            normal = self.opened(self.use(3754))
        items = repairs.config(g)['items']
        self.assertEqual({items[iid]['name'] for iid in normal}, {'芭蕉(3星)', '灵芝(3星)'})
        with patch.object(repairs.random, 'random', return_value=0):
            bonus = self.opened(self.use(3754))
        self.assertEqual({items[iid]['name'] for iid in bonus}, {'芭蕉(3星)', '灵芝(3星)', '香蒲(3星)'})

    def test_two_kind_material_gifts_do_not_duplicate_one_kind(self):
        for iid in (4102, 4106):
            self.stock(iid)
            with patch.object(repairs.random, 'random', return_value=.5):
                loot = self.opened(self.use(iid))
            self.assertEqual(len(loot), 2)
            self.assertEqual(set(loot.values()), {1})

    def test_jiaozi_grants_actual_energy(self):
        self.stock(4115)
        before = g.current_energy(self.player)
        reply = self.use(4115)
        self.assertEqual(self.player['energy'], before + 80)
        self.assertEqual(self.count(9), 0)
        self.assertIn('PVZ.Cmd.Cmd_SyncEnergy_SC', dict(reply))

    def test_treasure_experience_rewards_preserve_exact_exp_as_small_beans(self):
        for iid, low, high in ((3320, 1, 4), (3321, 2, 5), (3322, 3, 5), (3323, 4, 10), (3324, 5, 15)):
            spec = repairs.local_contract(g, iid)
            branch = spec['draws'][0]['families'][2]
            bean = branch['pool'][0]
            self.assertEqual(bean, (2002, low, high))
            for boundary in (low, high):
                self.stock(iid)
                with patch.object(repairs.random, 'choice', side_effect=[branch, bean]), \
                        patch.object(repairs.random, 'randint', return_value=boundary):
                    loot = self.opened(self.use(iid))
                self.assertEqual(loot, {2002: boundary})
        self.assertEqual(repairs.config(g)['items'][2002]['useEffect'], '1:10000')

    def test_new_legendary_failure_rolls_back_entire_box(self):
        self.stock(3447)
        with patch.object(repairs.random, 'random', return_value=.5), \
                patch.object(g, 'grant_fuben_reward', side_effect=RuntimeError('injected grant failure')):
            with self.assertRaises(RuntimeError):
                self.use(3447)
        self.assertEqual(self.count(3447), 1)
        self.assertEqual(self.count(211), 0)
        self.assertEqual(g.store.get_cards(self.player['uin']), [])


if __name__ == '__main__':
    unittest.main()
