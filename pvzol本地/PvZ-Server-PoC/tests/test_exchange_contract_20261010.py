"""Exercise the server contract consumed by the compiled ModuleExchange client."""
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g


class ExchangeContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = g.store.DB_PATH
        self.addCleanup(lambda: g.store.set_db_path(old))
        g.store.set_db_path(str(Path(self.tmp.name) / 'exchange.db'))
        g.store.init_db()
        self.player, _ = g.store.get_or_create_player(88009719)

    def test_client_refresh_time_matches_the_daily_purchase_rollover(self):
        # ModuleExchange.ExchangeService resets DateUtils.getServerDate() to
        # midnight and adds field 2 * 1000; it does not read a Unix timestamp.
        for hour, minute in ((0, 1), (20, 59), (21, 0), (23, 59)):
            now = time.mktime((2026, 10, 10, hour, minute, 0, 0, 0, -1))
            midnight = time.mktime((2026, 10, 10, 0, 0, 0, 0, 0, -1))
            with self.subTest(hour=hour), patch.object(g.time, 'time', return_value=now):
                body = g.exchange_store_itemlist(5, self.player)
                wire = g.parse_varint_fields(body)
                # After today's reset, the compiled client labels a passed
                # time-of-day "tomorrow"; the actual day-key changes at 00:00.
                displayed_time = midnight + wire[2]
                displayed_day = 'tomorrow' if now > displayed_time else 'today'
                self.assertEqual((displayed_day, wire[2]), ('tomorrow', 0))

    def test_every_magic_shop_page_fits_the_six_client_slots(self):
        for page in range(g.plant_coin_shop_page_count()):
            self.player['plant_shop_page'] = page
            body = g.exchange_store_itemlist(5, self.player)
            rows = [g.parse_varint_fields(x) for x in g.parse_repeated_submessages(body, 1)]
            with self.subTest(page=page):
                self.assertEqual(len(rows), 6)
                self.assertEqual(len({row[2] for row in rows}), 6)
                self.assertEqual(g.parse_varint_fields(body)[4], 210)
                self.assertTrue(all(row[5] in (210, 211) and row[6] > 0 for row in rows))

    def test_both_currencies_pay_and_grant_once(self):
        for coin in (210, 211):
            entry = next(e for e in g.exchange_store_entries(5) if e[4] == coin)
            eid, _, item, count, _, price = entry
            with self.subTest(currency=coin):
                initial_item = g.store.get_item_count(self.player['uin'], item)
                g.store.add_item(self.player['uin'], coin, 2 * price)
                request = g.pb_varint_field(1, eid)
                messages = g.handle_exchangestore_exchange(request, self.player)
                self.assertIn('PVZ.Cmd.Cmd_ExchangeStore_Exchange_SC', dict(messages))
                self.assertEqual(g.store.get_item_count(self.player['uin'], coin), price)
                self.assertEqual(g.store.get_item_count(self.player['uin'], item), initial_item + count)
                g.handle_exchangestore_exchange(request, self.player)
                self.assertEqual(g.store.get_item_count(self.player['uin'], coin), price)
                self.assertEqual(g.store.get_item_count(self.player['uin'], item), initial_item + count)


if __name__ == '__main__':
    unittest.main(verbosity=2)
