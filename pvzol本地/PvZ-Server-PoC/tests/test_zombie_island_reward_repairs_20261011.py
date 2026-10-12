"""Guard claims and bag uses obey the same real ItemInfo random-item contract."""
from collections import Counter
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
import admin_server as admin
import chest_reward_repairs as chests
import zombie_island_reward_repairs_20261011 as repairs

repairs.install(g)


class IslandRewardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        previous_path = g.store.DB_PATH
        self.addCleanup(lambda: g.store.set_db_path(previous_path))
        g.store.set_db_path(str(Path(self.tmp.name) / 'island-rewards.db'))
        g.store.init_db()
        self.player, _ = g.store.get_or_create_player(88009211)
        self.player.update(level=100, coin=100)
        g.store.save_player(self.player['uin'], level=100, coin=100)
        g.store.save_island_progress(self.player['uin'], g._ZI_NORMAL, level=2, max=1)
        g.store.save_island_progress(self.player['uin'], g._ZI_ADV, level=2, max=1)

    @staticmethod
    def idnums(body, field):
        result = Counter()
        for raw in g.parse_repeated_submessages(body, field):
            fields = g.parse_varint_fields(raw)
            result[fields[1]] += fields[2]
        return result

    def claim(self, mode):
        data = g.pb_varint_field(1, 1) + g.pb_varint_field(2, 1)
        return g.handle_zi_takereward(data, self.player, mode)

    @staticmethod
    def candidate(item_id):
        return lambda pool: next(row for row in pool if row[0] == item_id)

    def test_admin_catalog_establishes_chest_contract_and_reward_identities(self):
        row = admin.ITEM_CATALOG_BY_ID[4071]
        self.assertEqual((row['name'], row['kind']), ('僵尸岛宝箱', 'stack'))
        self.assertIn('获得一件随机物品', row['desc'])
        self.assertNotIn(4071, chests.config(g)['fixed'])
        self.assertEqual(admin.ITEM_NAMES[2013], '冒险勋章')
        self.assertEqual(admin.ITEM_NAMES[2002], '小经验豆')
        self.assertEqual(admin.ITEM_NAMES[53900], '碎片-火龙草')
        self.assertEqual(admin.ITEM_NAMES[1], '金币')

    def test_normal_and_advanced_guard_claims_draw_once_and_match_saved_reward(self):
        for mode in (g._ZI_NORMAL, g._ZI_ADV):
            with self.subTest(mode=mode), patch.object(chests.random, 'choice',
                    side_effect=self.candidate(2002)) as choice:
                before = g.store.get_item_count(self.player['uin'], 2002)
                messages = self.claim(mode)
                self.assertEqual(choice.call_count, 1)
                field = 2 if mode == g._ZI_ADV else 4
                reward = self.idnums(messages[0][1], field)
                expected_medals = 50 if mode == g._ZI_ADV else 25
                self.assertEqual(reward, {2013: expected_medals, 2002: 3})
                self.assertEqual(g.store.get_item_count(self.player['uin'], 2002) - before, 3)
                self.assertEqual(self.player['coin'], 100)
                self.assertEqual(g.store.get_item_count(self.player['uin'], 53900), 0)
                self.assertEqual(g.store.get_item_count(self.player['uin'], 4071), 0)
                self.assertEqual(self.idnums(self.claim(mode)[0][1], field), {})
                self.assertEqual(choice.call_count, 1, 'an already-claimed box must not reroll')

    def test_island_and_backpack_use_the_same_candidate_and_amount(self):
        for item_id, amount in g._CHEST_OPEN_REWARDS[4071]:
            with self.subTest(item_id=item_id), patch.object(chests.random, 'choice',
                    side_effect=self.candidate(item_id)) as choice:
                island = Counter(dict(g._zi_box_rewards(g._ZI_NORMAL, 1, 1)))
                island[2013] -= 25
                if not island[2013]:
                    del island[2013]
                g.store.add_item(self.player['uin'], 4071, 1)
                reply = g.handle_item_use(g.pb_varint_field(1, 4071)
                                         + g.pb_varint_field(2, 1), self.player)
                bag_reward = Counter()
                for name, body in reply:
                    if name == 'PVZ.Cmd.Cmd_Item_OpenChest_SC':
                        bag_reward = self.idnums(body, 3)
                self.assertEqual(island, {item_id: amount})
                self.assertEqual(bag_reward, island)
                self.assertEqual(g.store.get_item_count(self.player['uin'], 4071), 0)
                self.assertEqual(choice.call_count, 2)

    def test_take_all_advanced_rolls_once_per_guard_box(self):
        g.store.save_island_progress(self.player['uin'], g._ZI_ADV, level=3, max=2)
        with patch.object(chests.random, 'choice', side_effect=self.candidate(53900)) as choice:
            reply = g.handle_newzi_takeallreward(b'', self.player)
            self.assertEqual(choice.call_count, 2)
            self.assertEqual(self.idnums(reply[0][1], 1)[53900], 20)
            self.assertEqual(g.store.get_item_count(self.player['uin'], 53900), 20)
            self.assertEqual(len(g.store.island_boxes(self.player['uin'], g._ZI_ADV)), 6)
            self.assertEqual(self.idnums(g.handle_newzi_takeallreward(b'', self.player)[0][1], 1), {})
            self.assertEqual(choice.call_count, 2)


if __name__ == '__main__':
    unittest.main()
