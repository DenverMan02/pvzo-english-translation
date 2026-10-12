"""Reference-video mine rules with real eight-hour configuration and fake saves.

The clips show four mine holes, 0/3 carts at level 21, and an eight-hour
harvest interval. They do not show the official coin rate or garrison timing.
These tests therefore validate the observed limits and harvest admission,
without treating the explicitly local production economics as video evidence.
"""
import copy
import importlib
import unittest
from unittest.mock import patch
from worldmap_test_support import IsolatedServer, many, message, one


class VideoMineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = IsolatedServer()
        cls.g = cls.qa.game
        cls.wm = importlib.import_module('worldmap_local')
        cls.res = importlib.import_module('worldmap_resources')
        cls.a = cls.g._worldmap_adapter
        cls.r = cls.a.resources
        cls.serial = 0

    @classmethod
    def tearDownClass(cls):
        cls.qa.close()

    def setUp(self):
        type(self).serial += 1
        self.p = self.qa.player(99305000+self.serial, level=21, coin=1000,
                                role_created=1)
        self.g.rebuild_td_roster(self.p)
        self.g.ensure_player_cards(self.p)
        self.qa.train_owned_cards(self.p, level=100)
        self.now = 1800000000
        clock = patch.object(self.wm, '_now', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.request = self.m(1, self.pos(20, 20))

    def v(self, n, value):
        return self.g.pb_varint_field(n, value)

    def m(self, n, value):
        return self.g.pb_bytes(n, value)

    def pos(self, x, y):
        return self.v(1, x)+self.v(2, y)

    def call(self, name, data=b'', player=None):
        return self.qa.invoke(name, data, player or self.p)

    def balance(self):
        return self.qa.store.get_or_create_player(self.p['uin'])[0]['coin']

    def mark_held(self, pos=(20, 20)):
        # Admission tests do not depend on winning combat. Battle engine tests
        # already exercise actual capture; this creates only synthetic rows.
        with self.r.db(self.p) as con:
            row = self.r.mine(con, self.p, pos)
            con.execute('UPDATE local_worldmap_mines SET held=1 '
                        'WHERE uin=? AND area=? AND x=? AND y=?',
                        self.r.mine_key(row))

    def mine(self):
        with self.r.db(self.p) as con:
            return self.r.mine(con, self.p, (20, 20))

    def start(self):
        self.mark_held()
        return self.call('Mine_Mining', self.request)

    def assert_early_rejected(self):
        body = message(self.call('Mine_CollectProduct', self.request), 'LogicError')
        self.assertEqual(one(body, 1), 4760)

    def test_video_eight_hours_reaches_flash_server_config(self):
        self.assertEqual(int(self.g._cfg_value('iMineCollectCDTime', self.p)), 28800)
        entries = many(self.g.make_server_config_entries(self.p), 19)
        config = {one(e, 1).decode(): one(e, 2).decode() for e in entries}
        self.assertEqual(config['iMineCollectCDTime'], '28800')

    def test_initial_cart_blocks_harvest_until_exact_eight_hour_boundary(self):
        msgs = self.start()
        epoch = self.now
        holder = many(message(msgs, 'Mine_GetInfo'), 5)[0]
        self.assertEqual(one(holder, 4), epoch,
                         'Flash countdown subtracts this absolute miningTime')
        before = self.balance()
        self.assert_early_rejected()
        self.now = epoch+28799
        self.assert_early_rejected()
        dto = message(self.call('Mine_GetMiningInfo'), 'Mine_GetMiningInfo')
        self.assertGreater(one(dto, 2), 0)
        self.assertEqual(one(dto, 3), 0)
        self.assertEqual(self.balance(), before)
        self.assertEqual(self.mine()['started'], epoch)
        self.now += 1
        msgs = self.call('Mine_CollectProduct', self.request)
        paid = one(message(msgs, 'Mine_CollectProduct'), 2)
        self.assertGreater(paid, 0)
        self.assertEqual(self.balance(), before+paid)
        self.assertEqual(self.mine()['started'], self.now)

    def test_each_collection_starts_a_new_eight_hour_epoch_and_replays_pay_zero(self):
        self.start()
        self.now += 28800
        paid = one(message(self.call('Mine_CollectProduct', self.request),
                           'Mine_CollectProduct'), 2)
        epoch = self.now
        balance = self.balance()
        self.assert_early_rejected()
        self.assertEqual(one(message(self.call('Mine_CollectAllMine'),
                                     'Mine_CollectAllMine'), 1), 0)
        self.now += 28799
        self.assert_early_rejected()
        self.assertEqual(self.balance(), balance)
        self.assertEqual(self.mine()['started'], epoch)
        self.now += 1
        self.assertEqual(one(message(self.call('Mine_CollectProduct', self.request),
                                     'Mine_CollectProduct'), 2), paid)
        self.assertEqual(self.balance(), balance+paid)

    def test_collect_all_respects_each_cart_age_and_survives_reconnect(self):
        self.start()
        self.now += 120
        second = self.m(1, self.pos(26, 20))
        self.mark_held((26, 20))
        self.call('Mine_Mining', second)
        self.now += 28800-120
        before = self.balance()
        reconnect = self.res.ResourceGameplay(self.a)
        msgs = reconnect.collect_all(b'', copy.deepcopy(self.p))
        paid = one(message(msgs, 'Mine_CollectAllMine'), 1)
        self.assertGreater(paid, 0)
        self.assertEqual(self.balance(), before+paid)
        with self.r.db(self.p) as con:
            later = self.r.mine(con, self.p, (26, 20))
        self.assertEqual(later['started'], self.now-28800+120)
        self.assertEqual(self.mine()['started'], self.now)
        self.assertEqual(one(message(reconnect.collect_all(b'', self.p),
                                     'Mine_CollectAllMine'), 1), 0)
        self.now += 120
        self.assertEqual(one(message(reconnect.collect_all(b'', self.p),
                                     'Mine_CollectAllMine'), 1), paid)

    def test_video_level_21_three_carts_is_not_four_mine_holes(self):
        info = message(self.call('Mine_GetInfo', self.request), 'Mine_GetInfo')
        self.assertEqual(one(info, 2), 1,
                         'unclaimed mine uses original NPC state and attack button')
        self.assertEqual(many(info, 5), [])
        self.assertEqual((one(info, 6), one(info, 7)), (3, 3))
        for pos in ((20, 20), (26, 20), (20, 26), (14, 20)):
            self.mark_held(pos)
        for pos in ((20, 20), (26, 20), (20, 26)):
            message(self.call('Mine_Mining', self.m(1, self.pos(*pos))), 'Mine_Mining')
        body = message(self.call('Mine_Mining', self.m(1, self.pos(14, 20))), 'LogicError')
        self.assertEqual(one(body, 1), 4755)
        info = message(self.call('Mine_GetInfo', self.request), 'Mine_GetInfo')
        self.assertEqual((one(info, 6), one(info, 7)), (0, 3))
        self.assertEqual(self.r.cart_limit(self.p), (3, 86400),
                         'template timeLimit is separate from eight-hour collection')

    def third_formation(self):
        placements = self.a.placements(self.p, 1)
        dto = self.v(5, 1002)+b''.join(self.m(1, self.v(1, pos)+self.v(2, uid))
                                     for pos, uid in placements.items())
        message(self.call('Battle_SetFormation', self.m(1, dto)), 'Battle_GetFormation')
        return placements

    def test_third_map_team_captures_and_recall_unlocks_its_frozen_formation(self):
        placements = self.third_formation()
        with patch.object(self.a, 'make_report_details',
                          return_value=(True, self.v(1, 0), {})):
            message(self.call('Mine_SetOutTeam', self.request+self.v(2, 1002)),
                    'Mine_SetOutTeam')
            self.assertEqual(self.mine()['held'],0)
            with self.r.db(self.p) as con:
                job=con.execute('SELECT * FROM local_worldmap_mine_missions WHERE uin=? AND team_id=1002',
                                (self.p['uin'],)).fetchone()
            self.now=job['arrive_at']
            self.a.advance(self.p)
        self.assertEqual(self.mine()['team_id'], 1002)
        with self.r.db(self.p) as con:
            self.assertEqual(self.r.busy_cards(self.p, con), set(placements.values()))
        self.call('Mine_RecallTeam', self.request)
        self.assertEqual(self.mine()['team_id'], 0)
        self.assertTrue(self.r.busy(self.p),'army stays busy until its return ETA')
        with self.r.db(self.p) as con:
            self.now=con.execute('SELECT home_at FROM local_worldmap_mine_missions WHERE uin=? AND team_id=1002',
                                 (self.p['uin'],)).fetchone()[0]
        self.a.advance(self.p)
        self.assertFalse(self.r.busy(self.p))

    def test_third_defence_team_cannot_dispatch_to_mine(self):
        self.third_formation()
        message(self.call('Battle_SetDefendTeam', self.v(1, 1002)), 'Battle_GetFormation')
        with patch.object(self.a, 'make_report_details',
                          side_effect=AssertionError('defence team reached resource combat')):
            body = message(self.call('Mine_SetOutTeam', self.request+self.v(2, 1002)),
                           'LogicError')
        self.assertEqual(one(body, 1), 4721)
        self.assertEqual(self.mine()['held'], 0)


if __name__ == '__main__':
    unittest.main()
