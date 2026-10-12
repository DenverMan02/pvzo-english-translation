"""Original-reference mystery pack checks. All writes use a disposable SQLite database."""
import os
import sys
import tempfile
import unittest
from collections import Counter
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import player_store as store
import pvz_poc_server as srv


def message(messages, name):
    return next((body for kind, body in messages if kind == "PVZ.Cmd." + name), b"")


def reveals(messages):
    body = message(messages, "Cmd_Card_BuyMysteryCard_SC")
    return [(f[1], f[2]) for raw in srv.parse_repeated_submessages(body, 1)
            for f in [srv.parse_varint_fields(raw)]]


class MysteryPackTests(unittest.TestCase):
    def setUp(self):
        store.close_thread_db()
        self.original_db = store.DB_PATH
        self.temp = tempfile.TemporaryDirectory(prefix="pvz_mystery_")
        store.DB_PATH = os.path.join(self.temp.name, "test.db")
        store.init_db()
        self.player, _ = store.get_or_create_player(700009029)
        self.uin = self.player["uin"]
        self.set_player(ticket=10000, coin=1000000, mystery_version=1, mystery_luck=0,
                        mystery_board="28303,59400,2004,1091", vip_level=3)

    def tearDown(self):
        store.close_thread_db()
        store.DB_PATH = self.original_db
        self.temp.cleanup()

    def set_player(self, **fields):
        self.player.update(fields)
        store.save_player(self.uin, **fields)

    def buy_materials(self, rolls, fragments=2):
        # choices: fragment count, then each material template and its amount
        choices = [fragments]
        for iid, count in rolls:
            choices.extend([(iid, (count,)), count])
        with patch.object(srv.random, "random", return_value=1.0), \
                patch.object(srv.random, "choice", side_effect=choices):
            return srv.handle_card_buymysterycard(b"", self.player)

    def test_four_reference_reveals_and_exact_deposit(self):
        references = [
            [(2018, 3), (211, 10), (210, 50)],
            [(210, 30), (2131, 30), (210, 50)],
            [(211, 15), (2018, 3), (210, 30)],
            [(211, 10), (1, 150000), (210, 50)],
        ]
        totals = Counter()
        for index, rolls in enumerate(references, 1):
            fragments = 4 if index == 4 else 2
            msgs = self.buy_materials(rolls, fragments=fragments)
            expected = [(59400, fragments), (2004, 1)] + rolls
            self.assertEqual(reveals(msgs), expected)
            totals.update(dict(expected[:2]))
            for iid, count in rolls:
                totals[iid] += count
            self.assertEqual(self.player["mystery_luck"], index * 500)
            self.assertEqual(self.player["ticket"], 10000 - index * 500)
            self.assertFalse(message(msgs, "Cmd_Card_SyncFullInfo_SC"))
            for raw in srv.parse_repeated_submessages(message(msgs, "Cmd_Item_Update_SC"), 1):
                fields = srv.parse_varint_fields(raw)
                self.assertEqual(fields[3], store.get_item_count(self.uin, fields[2]))
        for iid, count in totals.items():
            if iid == 1:
                self.assertEqual(self.player["coin"], 1000000 + count)
                self.assertEqual(store.get_item_count(self.uin, 1), 0)  # currency is not a bag stack
            else:
                self.assertEqual(store.get_item_count(self.uin, iid), count)
        self.assertEqual(store.get_item_count(self.uin, 1091), 0)  # scroll unpacked into 3 cards
        self.assertEqual(len(store.get_cards(self.uin)), 0)

    def test_client_shop_ticket_path_does_not_double_charge(self):
        self.assertEqual(srv.mall_catalog()[103][:3], (1103, 500, True))
        purchase = srv.handle_shop_buyitem(srv.pb_varint_field(1, 103) + srv.pb_varint_field(2, 1), self.player)
        self.assertTrue(message(purchase, "Cmd_Shop_BuyItem_SC"))
        self.assertEqual(self.player["ticket"], 9500)
        self.assertEqual(store.get_item_count(self.uin, 1103), 1)
        msgs = self.buy_materials([(210, 30)] * 3)
        self.assertEqual(len(reveals(msgs)), 5)
        self.assertEqual(self.player["ticket"], 9500)
        self.assertEqual(store.get_item_count(self.uin, 1103), 0)

    def test_board_pool_fragment_mapping_and_no_free_open_rewards(self):
        features = srv._mystery_features()
        self.assertEqual(len(features), 16)
        self.assertEqual(features[28303], (1223, 59400))
        body = message(srv.handle_card_getmysterycardinfo(b"", self.player), "Cmd_Card_GetMysteryCardInfo_SC")
        board = srv.parse_repeated_submessages(body, 1)[0]
        self.assertEqual(srv.parse_varint_fields(board)[1], 28303)
        self.assertEqual(srv.parse_repeated_varint(board, 2), [59400, 2004, 1091])
        self.assertEqual(store.get_item_count(self.uin, 1103), 0)
        self.assertEqual(store.get_item_count(self.uin, 2004), 0)
        self.assertEqual(srv.SERVER_CONFIG_OVERRIDES["iMysteryCardPrice"], "500")
        self.assertEqual(srv.SERVER_CONFIG_OVERRIDES["iMysteryCardLuckValueMax"], "10000")

    def test_twentieth_purchase_guarantees_current_feature_and_duplicate_stored(self):
        for _ in range(19):
            self.buy_materials([(210, 30)] * 3)
        self.assertEqual(self.player["mystery_luck"], 9500)
        existing_uid = srv._CARD_UID_BASE + 1223
        store.add_card(self.uin, existing_uid, 1223, 9)
        with patch.object(srv.random, "random", return_value=1.0):
            msgs = srv.handle_card_buymysterycard(b"", self.player)
        self.assertEqual(len(reveals(msgs)), 5)
        self.assertEqual(reveals(msgs)[0], (28303, 1))
        self.assertEqual(self.player["mystery_luck"], 0)
        self.assertEqual(self.player["ticket"], 0)
        cards = store.get_cards(self.uin)
        self.assertEqual(len(cards), 2)
        self.assertEqual({card[1] for card in cards}, {1223})
        self.assertEqual(len({card[0] for card in cards}), 2)
        self.assertIn((existing_uid, 1223, 9, 0), cards)
        self.assertEqual(store.get_item_count(self.uin, 28303), 0)
        self.assertEqual(len(srv.parse_repeated_submessages(message(msgs, "Cmd_Card_SyncFullInfo_SC"), 2)), 1)

    def test_refresh_changes_feature_keeps_luck_and_survives_reentry(self):
        self.set_player(mystery_luck=3000)
        msgs = srv.handle_card_refreshmysterycard(b"", self.player)
        self.assertTrue(message(msgs, "Cmd_Card_RefreshMysteryCard_SC"))
        weekly, dailies = srv._mystery_get_board(self.player)
        self.assertNotEqual(weekly, 28303)
        self.assertEqual(dailies, [srv._mystery_features()[weekly][1], 2004, 1091])
        self.assertEqual(self.player["mystery_luck"], 3000)
        self.assertEqual(self.player["coin"], 1000000 - 5000)
        reloaded, _ = store.get_or_create_player(self.uin)
        self.assertEqual(srv._mystery_get_board(reloaded), (weekly, dailies))
        self.assertEqual(reloaded["mystery_luck"], 3000)

    def test_old_board_migrates_only_once_without_touching_collection(self):
        store.add_card(self.uin, 123456, 1223, 40)
        self.set_player(mystery_version=0, mystery_luck=30, mystery_board="28303,21102,21201,21301")
        srv.handle_card_getmysterycardinfo(b"", self.player)
        self.assertEqual(self.player["mystery_luck"], 3000)
        self.assertEqual(self.player["mystery_board"], "28303,59400,2004,1091")
        again, _ = store.get_or_create_player(self.uin)
        srv.handle_card_getmysterycardinfo(b"", again)
        self.assertEqual(again["mystery_luck"], 3000)
        self.assertEqual(store.get_cards(self.uin), [(123456, 1223, 40, 0)])

    def test_no_funds_no_rewards_no_progress_and_no_sun_charge(self):
        self.set_player(ticket=499, sun=999999, present_ticket=999999)
        self.assertEqual(self.buy_materials([(210, 50)] * 3), [])
        self.assertEqual(self.player["ticket"], 499)
        self.assertEqual(self.player["mystery_luck"], 0)
        self.assertEqual(store.get_item_count(self.uin, 2004), 0)
        self.assertEqual(store.get_cards(self.uin), [])

    def test_atomic_rollback_on_card_insert_error(self):
        with store._db() as con:
            con.execute("CREATE TRIGGER fail_test_insert BEFORE INSERT ON cards "
                        "BEGIN SELECT RAISE(ABORT, 'test insert failure'); END")
        self.set_player(mystery_luck=9500)
        with self.assertRaises(Exception):
            srv.handle_card_buymysterycard(b"", self.player)
        persisted, _ = store.get_or_create_player(self.uin)
        self.assertEqual(persisted["ticket"], 10000)
        self.assertEqual(persisted["mystery_luck"], 9500)
        self.assertEqual(store.get_item_count(self.uin, 2004), 0)
        self.assertEqual(store.get_cards(self.uin), [])

    def test_random_feature_roll_is_not_required_every_purchase(self):
        with patch.object(srv.random, "random", return_value=0.0):
            msgs = srv.handle_card_buymysterycard(b"", self.player)
        self.assertEqual(reveals(msgs)[0], (28303, 1))
        self.assertEqual(len(reveals(msgs)), 5)
        self.assertEqual(len(store.get_cards(self.uin)), 1)
        self.assertEqual(store.get_item_count(self.uin, 59400), 0)

    def test_stale_purchase_does_not_award_again(self):
        store.save_player(self.uin, mystery_luck=500)
        self.assertEqual(self.buy_materials([(210, 30)] * 3), [])
        persisted, _ = store.get_or_create_player(self.uin)
        self.assertEqual(persisted["ticket"], 10000)
        self.assertEqual(persisted["mystery_luck"], 500)
        self.assertEqual(store.get_item_count(self.uin, 2004), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
