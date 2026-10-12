"""Original group-1 free/paid flow, 24-hour boundaries and bean animation DTO."""
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SERVER = Path(os.environ.get('PVZ_TEST_SERVER_ROOT', str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(SERVER))
import player_store as store
_IMPORT_TEMP = tempfile.TemporaryDirectory(prefix='pvz_single_import_')
store.set_db_path(str(Path(_IMPORT_TEMP.name) / 'import.db'))
store.init_db()
import pvz_poc_server as g


class SinglePackCooldownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_single_cooldown_')
        store.set_db_path(str(Path(self.tmp.name) / 'test.db'))
        store.init_db()
        self.p, _ = store.get_or_create_player(88101024)
        self.now = int(datetime.fromisoformat('2026-10-10T23:58:00+08:00').timestamp())
        self.save(present_ticket=1000, ticket=0, card_market_granted=1,
                  card_market_single_draws=1, card_market_next_free_at=0)

    def tearDown(self):
        store.set_db_path(str(Path(_IMPORT_TEMP.name) / 'import.db'))
        self.tmp.cleanup()

    def save(self, **values):
        self.p.update(values)
        store.save_player(self.p['uin'], **values)

    def info(self, when):
        with patch.object(g.time, 'time', return_value=when):
            return g.parse_varint_fields(g._card_market_freeinfo_dto(1, self.p))

    def draw(self, when):
        with patch.object(g.time, 'time', return_value=when), \
                patch.object(g, '_card_market_roll_rewards', return_value=[(2002, 1)]):
            return g.handle_card_extract(g.pb_varint_field(1, 1), self.p)

    def reply(self, msgs):
        return next(body for name, body in msgs if name == 'PVZ.Cmd.Cmd_Card_Extract_SC')

    def test_free_buy_starts_exact_24_hours_and_animates_one_bean(self):
        self.assertEqual(g._CARD_MARKET_FREE_SECONDS, 24 * 60 * 60)
        self.assertEqual(self.info(self.now), {1: 1, 2: 1, 3: 0})
        before = store.get_item_count(self.p['uin'], 2003)
        msgs = self.draw(self.now)
        self.assertEqual(self.p['present_ticket'], 1000)
        self.assertEqual(self.p['card_market_next_free_at'], self.now + 86400)
        free = g.parse_repeated_submessages(self.reply(msgs), 3)[0]
        self.assertEqual(g.parse_varint_fields(free), {1: 1, 2: 1, 3: 86400})
        animation = g.parse_repeated_submessages(self.reply(msgs), 6)
        self.assertEqual(len(animation), 1)
        self.assertEqual(g.parse_varint_fields(animation[0]), {1: 2003, 2: 1})
        self.assertEqual(store.get_item_count(self.p['uin'], 2003), before + 1)

    def test_paid_240_diamonds_during_cooldown_does_not_move_free_deadline(self):
        self.draw(self.now)
        deadline = self.p['card_market_next_free_at']
        msgs = self.draw(self.now + 3600)
        self.assertEqual(self.p['present_ticket'], 760)
        self.assertEqual(self.p['card_market_next_free_at'], deadline)
        self.assertEqual(self.info(self.now + 3600)[3], 23 * 3600)
        self.assertEqual(g.parse_varint_fields(g.parse_repeated_submessages(self.reply(msgs), 6)[0]),
                         {1: 2003, 2: 1})

    def test_coupon_during_cooldown_does_not_move_deadline(self):
        self.draw(self.now)
        deadline = self.p['card_market_next_free_at']
        store.add_item(self.p['uin'], 2025, 1)
        self.draw(self.now + 300)
        self.assertEqual(store.get_item_count(self.p['uin'], 2025), 0)
        self.assertEqual(self.p['present_ticket'], 1000)
        self.assertEqual(self.p['card_market_next_free_at'], deadline)

    def test_86399_seconds_is_not_free_and_86400_is_free(self):
        self.draw(self.now)
        self.save(present_ticket=0, ticket=0)
        before = store.get_item_count(self.p['uin'], 2003)
        self.assertEqual(self.info(self.now + 86399)[3], 1)
        self.assertEqual(self.draw(self.now + 86399), [])
        self.assertEqual(store.get_item_count(self.p['uin'], 2003), before)
        msgs = self.draw(self.now + 86400)
        self.assertTrue(msgs)
        self.assertEqual(self.p['card_market_next_free_at'], self.now + 2 * 86400)
        self.assertEqual(store.get_item_count(self.p['uin'], 2003), before + 1)
        self.assertEqual(self.p['present_ticket'], 0)

    def test_midnight_and_relogin_do_not_reset_24_hour_cooldown(self):
        self.draw(self.now)
        self.p, _ = store.get_or_create_player(self.p['uin'])
        self.assertEqual(self.info(self.now + 120)[3], 86400 - 120)
        self.assertEqual(self.p['card_market_next_free_at'], self.now + 86400)

    def test_stale_cached_deadline_cannot_get_another_free_buy(self):
        self.draw(self.now)
        self.p['card_market_next_free_at'] = 0
        self.draw(self.now + 1)
        self.assertEqual(self.p['present_ticket'], 760)
        self.assertEqual(self.p['card_market_next_free_at'], self.now + 86400)


if __name__ == '__main__':
    try:
        result = unittest.main(verbosity=2, exit=False).result
    finally:
        store.close_thread_db()
        _IMPORT_TEMP.cleanup()
    sys.exit(0 if result.wasSuccessful() else 1)
