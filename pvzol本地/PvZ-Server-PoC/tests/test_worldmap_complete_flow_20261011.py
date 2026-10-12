"""EP13 mine expedition, letter and shared replay through real public handlers.

All Python modules are copied before import by IsolatedServer. Accounts and
their database are disposable; no game, socket, real mail or real chat opens.
"""
import importlib
import sqlite3
from contextlib import closing
import unittest
from unittest.mock import patch

from worldmap_test_support import IsolatedServer, fields, many, message, one


class CompleteWorldMapFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = IsolatedServer()
        cls.g = cls.qa.game
        cls.wm = importlib.import_module('worldmap_local')
        cls.resources = importlib.import_module('worldmap_resources')
        cls.mail = importlib.import_module('missing_features_local')
        cls.a = cls.g._worldmap_adapter
        cls.r = cls.a.resources
        cls.serial = 0

    @classmethod
    def tearDownClass(cls):
        cls.qa.close()

    def setUp(self):
        type(self).serial += 1
        self.p = self.qa.player(99927000 + self.serial, name='合成矿区甲',
            level=21, coin=10000, role_created=1)
        self.other = self.qa.player(99928000 + self.serial, name='合成矿区乙',
            level=21, coin=10000, role_created=1)
        self.g.rebuild_td_roster(self.p)
        self.g.ensure_player_cards(self.p)
        self.qa.train_owned_cards(self.p, level=100)
        self.now = 1800000000
        clock = patch.object(self.wm, '_now', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.pos = (20, 20)
        self.request = self.m(1, self.v(1, 20) + self.v(2, 20))
        self.call('Battle_GetFormation')

    def v(self, field, value):
        return self.g.pb_varint_field(field, value)

    def m(self, field, value):
        return self.g.pb_bytes(field, value)

    def call(self, name, data=b'', player=None):
        result = self.qa.invoke(name, data, player or self.p)
        for _, body in result:
            fields(body)
        return result

    def latest_job(self):
        with self.r.db(self.p) as con:
            return dict(con.execute('SELECT * FROM local_worldmap_mine_missions '
                'WHERE uin=? ORDER BY id DESC LIMIT 1', (self.p['uin'],)).fetchone())

    def capture(self):
        # Public dispatch must schedule, rather than play an immediate battle.
        dispatch = self.call('Mine_SetOutTeam', self.request + self.v(2, 1))
        message(dispatch, 'Mine_SetOutTeam')
        self.assertFalse(any(n.endswith('Battle_QueryReport_SC') for n, _ in dispatch))
        job = self.latest_job()
        self.assertEqual(job['state'], self.wm.MARCHING)
        self.assertEqual(job['settled'], 0)
        self.assertGreater(job['arrive_at'], self.now)
        with self.r.db(self.p) as con:
            self.assertEqual(self.r.mine(con, self.p, self.pos)['held'], 0)
        self.now = job['arrive_at']
        # This is the real background tick used by the server, with the real
        # authored combat engine and no mocked fight/report/reward functions.
        arrival = self.wm.poll(self.g, self.p)
        job = self.latest_job()
        self.assertEqual(job['settled'], 1)
        self.assertEqual(job['won'], 1)
        self.assertEqual(job['state'], self.resources.GUARDING)
        self.assertFalse(any(n.endswith('Battle_QueryReport_SC') for n, _ in arrival))
        notifications = [b for n, b in arrival if n.endswith('Mail_NewMessage_SC')]
        self.assertEqual(len(notifications), 1)
        self.assertEqual(one(notifications[0], 1), 1)
        self.assertEqual(one(notifications[0], 2), 0)
        return job, arrival

    def test_real_capture_letter_read_share_and_cross_account_persisted_replay(self):
        job, _ = self.capture()
        info = message(self.call('Mine_GetInfo', self.request), 'Mine_GetInfo')
        self.assertEqual(one(info, 2), 2)
        # The newly occupied mine now exposes the original mining entry.
        message(self.call('Mine_Mining', self.request), 'Mine_Mining')

        outline = message(self.call('Mail_GetOutline', self.v(1, 100)
            + self.v(2, 0) + self.v(3, 4)), 'Mail_GetOutline')
        self.assertEqual(one(outline, 1), 1)
        dto = many(outline, 2)[0]
        mail_id = one(dto, 1)
        self.assertEqual(one(dto, 4), 10)
        self.assertEqual(one(dto, 8), 0)
        self.assertEqual(one(dto, 9).decode(), '你成功驱逐了僵尸，占领了矿区(20,20)')
        self.assertEqual(many(dto, 10), [])
        content = message(self.call('Mail_GetContent', self.v(1, mail_id)), 'Mail_GetContent')
        text = one(content, 2).decode()
        self.assertIn('你现在可以开采矿区(20,20)', text)
        self.assertIn('event:battleId#' + str(job['report_id']), text)
        self.assertIn('event:addToChat#' + str(job['report_id']), text)
        metadata = one(one(content, 3), 1)
        self.assertEqual(one(metadata, 4), job['report_id'])
        self.assertEqual(one(metadata, 1), 1)
        read = many(message(self.call('Mail_GetOutline', self.v(1, 1)), 'Mail_GetOutline'), 2)[0]
        self.assertEqual(one(read, 8), 1)
        self.assertEqual(one(self.mail.mail_unread_notification(self.p)[0][1], 1), 0)

        self.g._BATTLE_REPORTS.clear()  # No in-memory arena/report cache survives restart.
        request = self.v(1, job['report_id'])
        report = one(message(self.call('Battle_QueryReport', request), 'Battle_QueryReport'), 1)
        self.assertEqual(one(report, 5), 7)
        self.assertEqual(one(report, 1), 0)
        self.assertTrue(many(report, 4), 'Actual combat rounds, not a score-only fake replay')
        self.assertEqual(report, self.mail.query_worldmap_mail_report(
            job['report_id'], db_path=self.qa.store.DB_PATH))
        shared = one(message(self.call('Battle_QueryReport', request, self.other), 'Battle_QueryReport'), 1)
        self.assertEqual(shared, report)
        self.assertEqual(message(self.call('Mail_GetContent', self.v(1, mail_id), self.other),
            'Mail_GetContent'), b'', 'Sharing a battle does not expose another player\'s letter')

        share_request = (self.m(1, self.v(1, 1) + self.g.pb_string(6, ',大家都来点点看吧！'))
            + self.v(2, 6) + self.v(3, job['report_id']))
        chat = message(self.call('Chat', share_request), 'Chat')
        native = one(one(chat, 1), 6).decode()
        self.assertIn('{event:6|' + str(job['report_id']) + '|', native)
        self.assertTrue(native.endswith('},大家都来点点看吧！'))
        self.assertNotIn('<a ', native)
        history = message(self.call('Chat_GetHistory', player=self.other), 'Chat_GetHistory')
        self.assertIn(native, [one(row, 6).decode() for row in many(history, 1)])
        shared_again = one(message(self.call('Battle_QueryReport', request, self.other), 'Battle_QueryReport'), 1)
        self.assertEqual(shared_again, report)

    def test_deleted_letter_never_regenerated_by_duplicate_poll_or_reconnect(self):
        job, _ = self.capture()
        outline = message(self.call('Mail_GetOutline', self.v(1, 100)), 'Mail_GetOutline')
        mail_id = one(many(outline, 2)[0], 1)
        message(self.call('Mail_Delete', self.v(1, mail_id)), 'Mail_Delete')
        self.a.active_users.discard(self.p['uin'])
        self.a.seen_users.discard(self.p['uin'])
        self.now += 10
        self.wm.poll(self.g, self.p)
        reconnect = self.resources.ResourceGameplay(self.a)
        self.assertEqual(reconnect.advance_missions(self.p), [])
        outline = message(self.call('Mail_GetOutline', self.v(1, 100)), 'Mail_GetOutline')
        self.assertEqual(one(outline, 1), 0)
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_mail_events '
                'WHERE uin=?', (self.p['uin'],)).fetchone()[0], 1)
        self.g._BATTLE_REPORTS.clear()
        report = one(message(self.call('Battle_QueryReport', self.v(1, job['report_id']),
            self.other), 'Battle_QueryReport'), 1)
        self.assertTrue(many(report, 4))


if __name__ == '__main__':
    unittest.main(verbosity=2)
