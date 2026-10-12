"""One-click card upgrade regressions, using disposable SQLite databases only."""

import asyncio
import copy
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import player_store as store  # noqa: E402
import pvz_poc_server as server  # noqa: E402


FAST_SC = "PVZ.Cmd.Cmd_Card_FasterLevelUp_SC"
ITEM_SC = "PVZ.Cmd.Cmd_Item_Update_SC"
CARD_SC = "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC"


class MemoryReader:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    async def read(self, _size):
        return next(self.chunks, b"")


class MemoryWriter:
    def __init__(self):
        self.output = bytearray()
        self.closed = False

    def get_extra_info(self, name):
        return ("127.0.0.1", 9001 if name == "sockname" else 12345)

    def write(self, data):
        self.output.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


def client_packet(uin, cmd_name, cmd_data=b""):
    body = server.xor7(server.build_cmdcommon(cmd_name, cmd_data))
    skey = b"test\0"
    user = uin.to_bytes(4, "big") + len(skey).to_bytes(4, "big") + skey
    return (bytes([server.TWEB_MAGIC])
            + (12 + len(user) + len(body)).to_bytes(4, "big")
            + len(body).to_bytes(4, "big")
            + bytes([1, server.CMD_UPLOAD, 1]) + user + body)


def bean_entry(item_id, count, *, omit_zero=False, legacy=False):
    if legacy:
        return server.build_item_dto(item_id, count)
    body = server.pb_varint_field(1, item_id)
    if not (omit_zero and count == 0):
        body += server.pb_varint_field(2, count)
    return body


def upgrade_request(uid, entries):
    return server.pb_int64_field(1, uid) + b"".join(
        server.pb_tag(2, 2) + server.uvarint(len(entry)) + entry
        for entry in entries
    )


class CardFasterLevelUpTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = store.DB_PATH
        self.tmp = tempfile.TemporaryDirectory(prefix="pvz_fast_level_")
        store.set_db_path(str(Path(self.tmp.name) / "test.db"))
        store.init_db()
        self.player, _ = store.get_or_create_player(700001238)
        store.save_player(self.player["uin"], level=50)
        self.player["level"] = 50
        self.uin = self.player["uin"]
        self.uid = 10001101
        store.add_card(self.uin, self.uid, 1101, 1)

    def tearDown(self):
        store.close_thread_db()
        store.set_db_path(self.previous_db)
        self.tmp.cleanup()

    def card(self, uid=None):
        target = self.uid if uid is None else uid
        return next(row for row in store.get_cards(self.uin) if row[0] == target)

    def upgrade(self, items, *, uid=None, omit_zero=False, legacy=False):
        target = self.uid if uid is None else uid
        request = upgrade_request(target, [
            bean_entry(item_id, count, omit_zero=omit_zero, legacy=legacy)
            for item_id, count in items
        ])
        return server.handle_card_fasterlevelup(request, self.player)

    def assert_success_sync(self, messages, uid=None):
        target = self.uid if uid is None else uid
        names = [name for name, _ in messages]
        self.assertIn(FAST_SC, names)
        self.assertIn(ITEM_SC, names)
        self.assertIn(CARD_SC, names)
        # Card sync triggers a redraw of the bean panel. Its bag cache must
        # already contain the consumed counts when that redraw happens.
        self.assertLess(names.index(ITEM_SC), names.index(CARD_SC))
        self.assertLess(names.index(ITEM_SC), names.index(FAST_SC))
        fast_body = next(body for name, body in messages if name == FAST_SC)
        self.assertEqual(server.parse_varint_fields(fast_body).get(1), target)
        item_body = next(body for name, body in messages if name == ITEM_SC)
        return {
            fields[2]: fields.get(3, 0)
            for fields in (
                server.parse_varint_fields(entry)
                for entry in server.parse_repeated_submessages(item_body, 1)
            )
        }

    def test_single_positive_dto_idnum_original_regression(self):
        store.add_item(self.uin, 2002, 2)
        messages = self.upgrade([(2002, 2)])
        self.assertEqual(self.assert_success_sync(messages), {2002: 0})
        self.assertEqual(store.get_item_count(self.uin, 2002), 0)
        self.assertEqual(self.card()[3], 20000)

    def test_screenshot_level20_mixed_zero_beans_upgrades_to21(self):
        uid = 10001215
        store.add_card(self.uin, uid, 1215, 20, 71625)
        store.add_item(self.uin, 2002, 8)
        store.add_item(self.uin, 2003, 21)
        # The client lists all three bean types even when only one is used.
        messages = self.upgrade([(2002, 1), (2003, 0), (2004, 0)], uid=uid)
        self.assertEqual(self.assert_success_sync(messages, uid), {2002: 7})
        self.assertEqual(self.card(uid)[2:], (21, 81625))
        self.assertEqual(store.get_item_count(self.uin, 2002), 7)
        self.assertEqual(store.get_item_count(self.uin, 2003), 21)
        self.assertEqual(store.get_item_count(self.uin, 2004), 0)

    def test_zero_count_omitted_on_wire_is_skipped(self):
        store.add_item(self.uin, 2002, 1)
        messages = self.upgrade([(2003, 0), (2002, 1), (2004, 0)], omit_zero=True)
        self.assertEqual(self.assert_success_sync(messages), {2002: 0})
        self.assertEqual(self.card()[3], 10000)

    def test_zero_entry_order_and_consecutive_clicks_keep_counts_fresh(self):
        store.add_item(self.uin, 2002, 3)
        first = self.upgrade([(2003, 0), (2002, 1), (2004, 0)])
        self.assertEqual(self.assert_success_sync(first), {2002: 2})
        self.assertEqual(self.card()[3], 10000)
        second = self.upgrade([(2004, 0), (2003, 0), (2002, 1)])
        self.assertEqual(self.assert_success_sync(second), {2002: 1})
        self.assertEqual(self.card()[3], 20000)
        self.assertEqual(store.get_item_count(self.uin, 2002), 1)

    def test_empty_and_all_zero_requests_change_nothing(self):
        store.add_item(self.uin, 2002, 3)
        before_card, before_items = self.card(), store.get_items(self.uin)
        for items in ([], [(2002, 0), (2003, 0), (2004, 0)]):
            with self.subTest(items=items):
                self.assertEqual(self.upgrade(items), [])
                self.assertEqual(self.card(), before_card)
                self.assertEqual(store.get_items(self.uin), before_items)

    def test_unknown_positive_bean_rejects_whole_request(self):
        store.add_item(self.uin, 2002, 3)
        store.add_item(self.uin, 999999, 1)
        before_card, before_items = self.card(), store.get_items(self.uin)
        self.assertEqual(self.upgrade([(2002, 1), (999999, 1)]), [])
        self.assertEqual(self.card(), before_card)
        self.assertEqual(store.get_items(self.uin), before_items)

    def test_unknown_zero_bean_is_not_a_valid_placeholder(self):
        store.add_item(self.uin, 2002, 3)
        before_card, before_items = self.card(), store.get_items(self.uin)
        self.assertEqual(self.upgrade([(2002, 1), (999999, 0)]), [])
        self.assertEqual(self.card(), before_card)
        self.assertEqual(store.get_items(self.uin), before_items)

    def test_insufficient_stock_rejects_whole_request(self):
        store.add_item(self.uin, 2002, 1)
        store.add_item(self.uin, 2003, 1)
        before_card, before_items = self.card(), store.get_items(self.uin)
        self.assertEqual(self.upgrade([(2003, 1), (2002, 2)]), [])
        self.assertEqual(self.card(), before_card)
        self.assertEqual(store.get_items(self.uin), before_items)

    def test_duplicate_total_over_stock_rejects_without_partial_consumption(self):
        store.add_item(self.uin, 2002, 2)
        before_card, before_items = self.card(), store.get_items(self.uin)
        self.assertEqual(self.upgrade([(2002, 1), (2002, 2)]), [])
        self.assertEqual(self.card(), before_card)
        self.assertEqual(store.get_items(self.uin), before_items)

    def test_valid_duplicate_entries_are_aggregated(self):
        store.add_item(self.uin, 2002, 3)
        messages = self.upgrade([(2002, 1), (2002, 2)])
        self.assertEqual(self.assert_success_sync(messages), {2002: 0})
        self.assertEqual(store.get_item_count(self.uin, 2002), 0)
        self.assertEqual(self.card()[3], 30000)
        body = next(body for name, body in messages if name == ITEM_SC)
        self.assertEqual(len(server.parse_repeated_submessages(body, 1)), 1)

    def test_legacy_dto_iteminfo_fields2_and3_are_still_accepted(self):
        store.add_item(self.uin, 2002, 1)
        messages = self.upgrade([(2002, 1)], legacy=True)
        self.assertEqual(self.assert_success_sync(messages), {2002: 0})
        self.assertEqual(self.card()[3], 10000)

    def test_nonowned_card_uid_does_not_consume_beans(self):
        store.add_item(self.uin, 2002, 1)
        before_card, before_items = self.card(), store.get_items(self.uin)
        self.assertEqual(self.upgrade([(2002, 1)], uid=99999999), [])
        self.assertEqual(self.card(), before_card)
        self.assertEqual(store.get_items(self.uin), before_items)

    def test_failure_after_consumption_or_exp_write_rolls_back_all_state(self):
        store.add_item(self.uin, 2002, 2)
        for failing_step in ("add_card_exp", "build_card_sync_sc"):
            with self.subTest(failing_step=failing_step):
                before_card = self.card()
                before_items = store.get_items(self.uin)
                before_player = copy.deepcopy(self.player)
                with patch.object(server, failing_step, side_effect=RuntimeError("injected failure")):
                    with self.assertRaisesRegex(RuntimeError, "injected failure"):
                        self.upgrade([(2002, 1)])
                self.assertEqual(self.card(), before_card)
                self.assertEqual(store.get_items(self.uin), before_items)
                self.assertEqual(self.player, before_player)

    def test_real_packet_dispatch_preserves_inventory_ack_and_card_order(self):
        store.add_item(self.uin, 2002, 3)
        request = upgrade_request(self.uid, [
            bean_entry(2002, 1), bean_entry(2003, 0), bean_entry(2004, 0)
        ])
        packets = [
            client_packet(self.uin, "PVZ.Cmd.Cmd_CheckOldUser_CS"),
            client_packet(self.uin, "PVZ.Cmd.Cmd_Init_CS"),
            client_packet(self.uin, "PVZ.Cmd.Cmd_Card_FasterLevelUp_CS", request),
            client_packet(self.uin, "PVZ.Cmd.Cmd_Card_FasterLevelUp_CS", request),
        ]
        writer = MemoryWriter()
        # Suppress unrelated login grants; use the real framing, dispatch,
        # upgrade transaction and response serializer without opening sockets.
        login_values = {
            "rebuild_td_roster": [], "ensure_player_cards": None,
            "sanitize_td_progress": None, "baptize_login_cleanup": None,
            "card_swap_login_grant": None, "normalize_level": False,
            "vip_login_messages": [], "active_vip_level": 0,
            "td_privilege_login_messages": [], "td_privilege_active": False,
            "diamond_login_messages": [], "make_server_config_entries": b"",
            "puppet_skill_val": 0, "puppet_energy": 0,
        }
        with ExitStack() as patches:
            for name, value in login_values.items():
                patches.enter_context(patch.object(server, name, return_value=value))
            patches.enter_context(patch.object(server, "log"))
            asyncio.run(server.handle_client(MemoryReader(packets), writer))
        self.assertTrue(writer.closed)
        replies = []
        for packet in server.frame_packets(writer.output):
            length = int.from_bytes(packet[5:9], "big")
            parsed = server.parse_cmdcommon(packet[-length:])
            if parsed["cmdName"] in {ITEM_SC, FAST_SC, CARD_SC}:
                replies.append((parsed["cmdName"], parsed["cmdData"]))
        self.assertEqual([name for name, _ in replies],
                         [ITEM_SC, FAST_SC, CARD_SC, ITEM_SC, FAST_SC, CARD_SC])
        self.assertEqual(self.assert_success_sync(replies[:3]), {2002: 2})
        self.assertEqual(self.assert_success_sync(replies[3:]), {2002: 1})
        self.assertEqual(store.get_item_count(self.uin, 2002), 1)
        self.assertEqual(self.card()[3], 20000)


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CardFasterLevelUpTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
