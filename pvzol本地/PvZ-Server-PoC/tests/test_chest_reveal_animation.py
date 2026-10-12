"""OpenChest's optional preview list must not replay the actual reward reveal.

Use real server protobuf handlers and a disposable inventory; never touch a
player save. The shipped client displays allItems before opening and uses
openedItems for the actual reveal, so actual loot belongs only in field 3.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
import chest_reward_repairs as repairs


OPEN_CHEST = 'PVZ.Cmd.Cmd_Item_OpenChest_SC'
OPEN_GIFT = 'PVZ.Cmd.Cmd_Item_OpenGift_SC'
OPEN_BATCH = 'PVZ.Cmd.Cmd_Item_OpenGiftChestBatch_SC'
USE_ACK = 'PVZ.Cmd.Cmd_Item_Use_SC'
CARD_SYNC = 'PVZ.Cmd.Cmd_Card_SyncFullInfo_SC'
REVEALS = {OPEN_CHEST, OPEN_GIFT, OPEN_BATCH}


class ChestRevealAnimationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_path = g.store.DB_PATH
        self.addCleanup(lambda: g.store.set_db_path(old_path))
        g.store.set_db_path(str(Path(self.tmp.name) / 'chest-animation.db'))
        g.store.init_db()
        self.player, _ = g.store.get_or_create_player(88009131)
        g.store.save_player(self.player['uin'], level=300, energy=100, coin=100)
        self.player.update(level=300, energy=100, coin=100)

    def stock(self, item_id, count=1):
        g.store.add_item(self.player['uin'], item_id, count)

    def count(self, item_id):
        return g.store.get_item_count(self.player['uin'], item_id)

    def use(self, item_id, count=1):
        return g.handle_item_use(g.pb_varint_field(1, item_id)
                                 + g.pb_varint_field(2, count), self.player)

    def pairs(self, body, field):
        return [g.parse_varint_fields(dto)
                for dto in g.parse_repeated_submessages(body, field)]

    def reveal(self, messages, response, item_id, count, expected):
        reveals = [(name, body) for name, body in messages if name in REVEALS]
        self.assertEqual([name for name, _ in reveals], [response])
        acks = [body for name, body in messages if name == USE_ACK]
        self.assertEqual(len(acks), 1)
        self.assertEqual(g.parse_varint_fields(acks[0]), {1: item_id, 2: count})
        body = reveals[0][1]
        fields = g.parse_varint_fields(body)
        self.assertEqual(fields[1], item_id)
        if response == OPEN_BATCH:
            self.assertEqual(fields[2], count)
        reward_field = 2 if response == OPEN_GIFT else 3
        self.assertEqual(self.pairs(body, reward_field),
                         [{1: iid, 2: amount} for iid, amount in sorted(expected.items())])
        if response == OPEN_CHEST:
            self.assertEqual(self.pairs(body, 2), [],
                             'allItems previews actual loot before the opening and then retracts it')

    def test_reported_two_plants_and_coins_reveal_once_after_opening(self):
        self.stock(3447, 2)
        self.stock(211, 7)
        loot = [(211, 11), (29603, 1), (29703, 1)]
        with patch.object(repairs, 'roll', return_value=loot) as roll:
            messages = self.use(3447)
        roll.assert_called_once_with(g._activity_server_api(), 3447)
        self.assertEqual(self.count(3447), 1)
        self.assertEqual(self.count(211), 18)
        cards = g.store.get_cards(self.player['uin'])
        expected_cards = sorted(int(repairs.config(g)['items'][iid]['cardItemId'].split(':')[0])
                                for iid in (29603, 29703))
        self.assertEqual(sorted(card[1] for card in cards), expected_cards)
        self.assertEqual(len({card[0] for card in cards}), 2)
        self.assertEqual((self.count(29603), self.count(29703)), (0, 0))
        syncs = [body for name, body in messages if name == CARD_SYNC]
        self.assertEqual(len(syncs), 1)
        self.assertEqual(len(g.parse_repeated_submessages(syncs[0], 2)), 2)
        self.assertEqual(g.parse_varint_fields(syncs[0])[4], 40)
        self.reveal(messages, OPEN_CHEST, 3447, 1, dict(loot))

    def test_material_only_chest_does_not_preview_or_replay_rewards(self):
        self.stock(3447, 2)
        self.stock(211, 7)
        with patch.object(repairs, 'roll', return_value=[(211, 35)]):
            messages = self.use(3447)
        self.assertEqual((self.count(3447), self.count(211)), (1, 42))
        self.assertEqual(g.store.get_cards(self.player['uin']), [])
        self.assertNotIn(CARD_SYNC, dict(messages))
        self.reveal(messages, OPEN_CHEST, 3447, 1, {211: 35})

    def test_wallet_chest_credits_and_reveals_reward_once(self):
        self.stock(3447)
        with patch.object(repairs, 'roll', return_value=[(1, 250), (211, 5)]):
            messages = self.use(3447)
        self.assertEqual(self.count(3447), 0)
        self.assertEqual(self.count(211), 5)
        self.assertEqual(self.player['coin'], 350)
        self.assertEqual(g.store.get_or_create_player(self.player['uin'])[0]['coin'], 350)
        self.assertEqual(sum(name == 'PVZ.Cmd.Cmd_SyncCoin_SC' for name, _ in messages), 1)
        self.reveal(messages, OPEN_CHEST, 3447, 1, {1: 250, 211: 5})

    def test_single_gift_keeps_original_field_2_reward_contract(self):
        self.stock(3336, 2)
        self.stock(2018, 7)
        self.stock(2004, 3)
        messages = self.use(3336)
        self.assertEqual((self.count(3336), self.count(2018), self.count(2004)), (1, 37, 8))
        self.reveal(messages, OPEN_GIFT, 3336, 1, {2018: 30, 2004: 5})

    def test_batch_chest_aggregates_exactly_once_without_single_reveal(self):
        self.stock(3447, 3)
        self.stock(211, 7)
        with patch.object(repairs, 'roll', side_effect=[[(211, 11)], [(211, 13)]]) as roll:
            messages = self.use(3447, 2)
        self.assertEqual(roll.call_count, 2)
        self.assertEqual((self.count(3447), self.count(211)), (1, 31))
        self.reveal(messages, OPEN_BATCH, 3447, 2, {211: 24})

    def test_rejected_use_neither_consumes_nor_starts_reveal(self):
        self.stock(3447)
        self.stock(211, 7)
        with patch.object(repairs, 'roll') as roll:
            messages = self.use(3447, 2)
        roll.assert_not_called()
        self.assertEqual((self.count(3447), self.count(211)), (1, 7))
        self.assertEqual(g.store.get_cards(self.player['uin']), [])
        self.assertFalse(any(name in REVEALS for name, _ in messages))
        self.assertIn('PVZ.Cmd.Cmd_LogicError_SC', dict(messages))
        self.assertEqual(g.parse_varint_fields(dict(messages)[USE_ACK]), {1: 3447, 2: 0})

    def test_select_one_exchange_never_starts_chest_reveal(self):
        self.stock(4091, 2)
        messages = g.handle_item_exchange(g.pb_varint_field(1, 4091)
                                          + g.pb_varint_field(2, 2)
                                          + g.pb_varint_field(3, 1), self.player)
        self.assertEqual(self.count(4091), 0)
        self.assertEqual(self.player['coin'], 400100)
        self.assertEqual(g.store.get_or_create_player(self.player['uin'])[0]['coin'], 400100)
        self.assertFalse(any(name in REVEALS for name, _ in messages))
        self.assertNotIn(USE_ACK, dict(messages))
        acks = [body for name, body in messages if name == 'PVZ.Cmd.Cmd_Item_Exchange_SC']
        self.assertEqual(len(acks), 1)
        self.assertEqual(g.parse_varint_fields(acks[0]), {1: 4091, 2: 2, 3: 1, 4: 400000})


if __name__ == '__main__':
    unittest.main()
