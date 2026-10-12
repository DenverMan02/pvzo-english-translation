"""Research compares the ordinary arena score displayed by the real client."""
import atexit
import copy
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store

# Keep both import-time initialization and test data away from a live save.
BOOT = tempfile.TemporaryDirectory(prefix='pvz_talent_boot_')
store.set_db_path(str(Path(BOOT.name) / 'players.db'))
store.init_db()
atexit.register(BOOT.cleanup)
atexit.register(store.close_thread_db)
with patch.object(logging, 'FileHandler', return_value=logging.NullHandler()), patch.object(logging, 'basicConfig'):
    import pvz_poc_server as g


class TalentArenaScoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_talent_score_')
        self.addCleanup(self.tmp.cleanup)
        old = store.DB_PATH
        self.addCleanup(lambda: store.set_db_path(old))
        store.set_db_path(str(Path(self.tmp.name) / 'players.db'))
        store.init_db()
        self.addCleanup(store.close_thread_db)
        self.player, _ = store.get_or_create_player(88011370)
        store.unlock_plant(self.player['uin'], 1100)
        for tid in (1101, 1102, 1103, 1104):
            store.study_talent(self.player['uin'], tid)
        self.info = g.load_plant_talents()['talent'][1105]
        self.assertEqual(self.info['cond'], '18::130')
        self.power = patch.object(g, 'plant_card_power', return_value=self.info['power'])
        self.power.start()
        self.addCleanup(self.power.stop)
        self.quest = patch.object(g, '_quest_observe_talent_progress', return_value=[])
        self.quest.start()
        self.addCleanup(self.quest.stop)

    def save_score(self, score, gains=None, day=None):
        state = {'day': day or time.strftime('%Y%m%d'), 'score': score,
                 'daily_score': max(0, score - 100) if gains is None else gains}
        self.player['arena_data'] = json.dumps(state)
        store.save_player(self.player['uin'], arena_data=self.player['arena_data'])

    def get_talent(self, player=None):
        body = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Talent_GetList_CS'](
            g.pb_varint_field(1, self.info['stage']), player or self.player)
        talents = [g.parse_varint_fields(dto) for dto in g.parse_repeated_submessages(body, 4)]
        return next(dto for dto in talents if dto[1] == 1105)

    def study(self, player=None):
        return g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Talent_Study_CS'](
            g.pb_varint_field(1, 1105), player or self.player)

    def test_visible_129_130_and_reported_137_share_getlist_and_study_gates(self):
        for score, allowed in ((129, False), (130, True), (137, True)):
            with self.subTest(score=score):
                with store._db() as con:
                    con.execute('DELETE FROM talents WHERE uin=? AND talent_id=1105', (self.player['uin'],))
                self.save_score(score)
                with patch.object(g, '_bot_roster_with_state', return_value=[]):
                    messages = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Arena_GetMyInfo_CS'](b'', self.player)
                visible = next(body for name, body in messages if name == 'PVZ.Cmd.Cmd_Arena_GetMyInfo_SC')
                self.assertEqual(g.parse_varint_fields(visible)[2], score)
                dto = self.get_talent()
                self.assertEqual(dto[3], 1)
                self.assertEqual(dto[4], allowed)
                messages = self.study()
                self.assertEqual(store.has_talent(self.player['uin'], 1105), allowed)
                self.assertEqual(any(name.endswith('Talent_Study_SC') for name, _ in messages), allowed)

    def test_today_gain_counter_does_not_replace_displayed_score(self):
        self.save_score(129, gains=200)
        self.assertEqual(self.get_talent()[4], 0)
        self.study()
        self.assertFalse(store.has_talent(self.player['uin'], 1105))

    def test_yesterdays_score_does_not_satisfy_today_condition(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
        self.save_score(999, gains=999, day=yesterday)
        self.assertEqual(self.get_talent()[4], 0)
        self.study()
        self.assertFalse(store.has_talent(self.player['uin'], 1105))

    def test_old_socket_refreshes_committed_score_in_both_routes(self):
        self.save_score(129)
        stale = copy.deepcopy(self.player)
        self.save_score(137)
        self.assertEqual(self.get_talent(stale)[4], 1)
        self.assertEqual(json.loads(stale['arena_data'])['score'], 137)
        stale['arena_data'] = json.dumps({'day': time.strftime('%Y%m%d'), 'score': 129})
        self.study(stale)
        self.assertTrue(store.has_talent(self.player['uin'], 1105))

    def test_stale_satisfied_snapshot_cannot_bypass_current_gate(self):
        self.save_score(137)
        stale = copy.deepcopy(self.player)
        self.save_score(129)
        self.assertEqual(self.get_talent(stale)[4], 0)
        stale['arena_data'] = json.dumps({'day': time.strftime('%Y%m%d'), 'score': 137})
        self.study(stale)
        self.assertFalse(store.has_talent(self.player['uin'], 1105))

    def test_score_does_not_bypass_power_ownership_or_parent(self):
        self.save_score(137)
        with patch.object(g, 'plant_card_power', return_value=self.info['power'] - 1):
            self.assertEqual(self.get_talent()[3], 0)
            self.study()
        self.assertFalse(store.has_talent(self.player['uin'], 1105))
        store.remove_plant(self.player['uin'], 1100)
        self.study()
        self.assertFalse(store.has_talent(self.player['uin'], 1105))
        store.unlock_plant(self.player['uin'], 1100)
        with store._db() as con:
            con.execute('DELETE FROM talents WHERE uin=? AND talent_id=1104', (self.player['uin'],))
        self.study()
        self.assertFalse(store.has_talent(self.player['uin'], 1105))


if __name__ == '__main__':
    unittest.main()
