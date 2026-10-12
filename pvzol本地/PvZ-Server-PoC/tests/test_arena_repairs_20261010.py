"""Exercise final arena routes against disposable player/bot SQLite storage."""
import atexit
import copy
from contextlib import closing
from pathlib import Path
import sys
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Server registration performs additive migrations. Point the store at a
# disposable DB before importing it, including when this test runs standalone.
import player_store as store
_bootstrap = tempfile.TemporaryDirectory(prefix='pvz_arena_bootstrap_')
_old_bootstrap_db = store.DB_PATH
store.set_db_path(str(Path(_bootstrap.name) / 'bootstrap.db'))
import pvz_poc_server as g
store.set_db_path(_old_bootstrap_db)
atexit.register(_bootstrap.cleanup)


class ArenaRouteRepairsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_arena_routes_')
        self.addCleanup(self.tmp.cleanup)
        self.old_db = store.DB_PATH
        self.old_bot_db = g.bots_local._DB_PATH
        self.addCleanup(lambda: store.set_db_path(self.old_db))
        self.addCleanup(lambda: setattr(g.bots_local, '_DB_PATH', self.old_bot_db))
        store.set_db_path(str(Path(self.tmp.name) / 'arena.db'))
        store.init_db()
        self.player, _ = store.get_or_create_player(88010010)
        self.player.update(level=100, ticket=100, present_ticket=0)
        store.save_player(self.player['uin'], level=100, ticket=100, present_ticket=0)
        self.bot = {'uin': 900001, 'name': 'ArenaTestBot', 'level': 1, 'score': 105}
        g.bots_local.initialize(db_path=store.DB_PATH)
        with store._db() as con:
            g.bots_local._ensure_bots_state_table(con)
            con.execute('INSERT INTO bots_state(uin,score,power) VALUES(900001,105,10)')
        self.left = [{'pos': 3, 'uid': 10001, 'card_id': 101, 'hp': 100, 'atk': 10,
                      'def_': 0, 'ability': 0, 'level': 1, 'is_leader': True, 'power': 10}]
        self.right = [dict(self.left[0], pos=4, uid=20001)]
        self.report = b'encoded-arena-report'
        patches = {
            '_player_formation_power': lambda p: 10,
            'build_player_units': lambda *a, **kw: copy.deepcopy(self.left),
            '_npc_encounter': lambda *a: {'name': self.bot['name'], 'level': 1,
                                        'units': copy.deepcopy(self.right)},
            '_auto_puppets': lambda p: [], '_auto_puppet_list': lambda p: b'',
            '_daily_quest_touch': lambda *a: None,
        }
        for name, replacement in patches.items():
            self.enterContext(patch.object(g, name, replacement))
        self.enterContext(patch.object(g.bots_local, 'bots', return_value=[self.bot]))
        self.fight = self.enterContext(patch.object(g, 'run_battle', return_value=(True, 10, 10, [])))
        self.report_builder = self.enterContext(patch.object(g, 'build_fight_report', return_value=self.report))
        self.enterContext(patch.dict(g.SERVER_CONFIG_OVERRIDES,
                                    {'iAllServerRankArenaRankFreeChallengeTimes': '5',
                                     'sAllServerRankArenaRankPayChallengeFee': ','.join(['50'] * 10)}))
        self.enterContext(patch.dict(g._BATTLE_REPORTS, {}, clear=True))
        self.state(challenges=0)

    def state(self, field='allserver_rank_arena_data', **values):
        data = {'day': time.strftime('%Y%m%d'), 'score': 100, 'challenges': 0,
                'records': [], 'wins': 0, 'losses': 0}
        data.update(values)
        g._save_json_state(self.player, field, data)
        return data

    def wallet(self, gift=0, gold=0):
        self.player.update(present_ticket=gift, ticket=gold)
        store.save_player(self.player['uin'], present_ticket=gift, ticket=gold)

    def route(self, name, body=b''):
        return g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + name + '_CS'](body, self.player)

    def challenge(self, role=900001, requested=None):
        body = g.pb_varint_field(1, role)
        if requested is not None:
            body += g.pb_varint_field(3, requested)
        return self.route('AllServerRankArena_Challenge', body)

    def saved(self, field='allserver_rank_arena_data'):
        player, _ = store.get_or_create_player(self.player['uin'])
        return g._load_json_state(player, field, {})

    def error(self, messages):
        self.assertEqual(messages[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
        return g.parse_varint_fields(messages[0][1])[1]

    def test_final_route_commits_bot_result_current_ranks_and_queryable_reports(self):
        self.wallet()
        messages = dict(self.challenge(requested=1))
        fields = g.parse_varint_fields(messages['PVZ.Cmd.Cmd_AllServerRankArena_Challenge_SC'])
        self.assertEqual((fields[2], fields[4]), (1, 1))
        opponent = g.parse_varint_fields(g.parse_repeated_submessages(
            messages['PVZ.Cmd.Cmd_AllServerRankArena_Challenge_SC'], 6)[0])
        self.assertEqual(opponent[9], 2)
        self.assertEqual(g.bots_local.bot_state(900001)['losses'], 1)
        self.assertEqual(self.saved()['max_rank'], 1)
        recorder = self.route('AllServerRankArena_GetRecorder')[0][1]
        uid = g.parse_varint_fields(g.parse_repeated_submessages(recorder, 1)[0])[1]
        self.assertNotEqual(uid, self.player['uin'])
        for request, response in (('Battle_QueryReport', 'Battle_QueryReport'),
                                  ('Battle_QueryRankReport', 'Battle_QueryRankReport')):
            reply = self.route(request, g.pb_varint_field(1, uid))
            self.assertEqual(reply[0][0], 'PVZ.Cmd.Cmd_' + response + '_SC')
            self.assertEqual(g.parse_repeated_submessages(reply[0][1], 1), [self.report])

    def test_paid_match_uses_gift_then_gold_and_preserves_wallet_sync_messages(self):
        self.state(challenges=5)
        self.wallet(gift=30, gold=40)
        reply = dict(self.challenge(requested=6))
        self.assertEqual((self.player['present_ticket'], self.player['ticket']), (0, 20))
        self.assertIn('PVZ.Cmd.Cmd_SyncPresentTicket_SC', reply)
        self.assertIn('PVZ.Cmd.Cmd_SyncTicket_SC', reply)
        fields = g.parse_varint_fields(reply['PVZ.Cmd.Cmd_AllServerRankArena_Challenge_SC'])
        self.assertEqual((fields[4], fields[5]), (6, 2))

    def test_paid_fee_matches_client_remaining_attempt_index_and_fallback(self):
        for used, spec, expected in ((5, '7,8,9,10,11,12,13,14,15,16', 16),
                                     (14, '7,8,9,10,11,12,13,14,15,16', 7),
                                     (5, '7', 50)):
            with self.subTest(used=used, spec=spec):
                self.state(challenges=used)
                self.wallet(gold=100)
                with patch.dict(g.SERVER_CONFIG_OVERRIDES,
                                {'sAllServerRankArenaRankPayChallengeFee': spec}):
                    self.challenge()
                self.assertEqual(self.player['ticket'], 100 - expected)

    def test_exhausted_or_unaffordable_match_changes_no_counter_wallet_or_bot(self):
        for used, expected in ((15, 7705), (5, 5005)):
            with self.subTest(used=used):
                self.state(challenges=used)
                self.wallet()
                before = g.bots_local.bot_state(900001)
                self.fight.reset_mock()
                self.assertEqual(self.error(self.challenge()), expected)
                self.assertEqual(self.saved()['challenges'], used)
                self.assertEqual(self.saved()['records'], [])
                self.assertEqual(g.bots_local.bot_state(900001), before)
                self.fight.assert_not_called()

    def test_invalid_or_empty_opponents_cannot_grant_win_or_consume_attempt(self):
        for request, field in (('RankArena_Challenge', 'rank_arena_data'),
                               ('AllServerRankArena_Challenge', 'allserver_rank_arena_data')):
            for invalid in (self.player['uin'], 900999):
                with self.subTest(request=request, invalid=invalid):
                    self.state(field=field)
                    self.assertEqual(self.error(self.route(request, g.pb_varint_field(1, invalid))), 5006)
                    self.assertEqual(self.saved(field)['challenges'], 0)
                    self.assertEqual(self.saved(field)['records'], [])
            self.state(field=field)
            with patch.object(g, '_npc_encounter', return_value={'name': 'empty', 'level': 1, 'units': []}):
                self.assertEqual(self.error(self.route(request, g.pb_varint_field(1, 900001))), 5006)
            self.assertEqual(self.saved(field)['challenges'], 0)

    def test_failed_report_rolls_back_paid_wallet_player_and_bot_settlement(self):
        self.state(challenges=5)
        self.wallet(gift=30, gold=40)
        before = g.bots_local.bot_state(900001)
        with patch.object(g, 'build_fight_report', side_effect=RuntimeError('test report failure')):
            with self.assertRaises(RuntimeError):
                self.challenge()
        self.assertEqual((self.player['present_ticket'], self.player['ticket']), (30, 40))
        self.assertEqual((self.saved()['score'], self.saved()['challenges']), (100, 5))
        self.assertEqual(g.bots_local.bot_state(900001), before)

    def test_repeated_client_challenge_counter_cannot_charge_or_settle_twice(self):
        self.state(challenges=5)
        self.wallet(gold=100)
        self.challenge(requested=6)
        self.assertEqual(self.error(self.challenge(requested=6)), 5006)
        self.assertEqual((self.saved()['challenges'], self.player['ticket']), (6, 50))
        self.assertEqual(g.bots_local.bot_state(900001)['losses'], 1)

    def test_regular_empty_or_unmatched_opponent_does_not_consume_or_touch_quest(self):
        for pick, units in ((None, self.right), ({'uin': 900001, 'power': 10}, [])):
            with self.subTest(pick=pick):
                self.state(field='arena_data')
                before = g.bots_local.bot_state(900001)
                self.fight.reset_mock()
                with patch.object(g.bots_local, 'pick_opponent', return_value=pick), \
                        patch.object(g, '_refresh_arena_npcs'), \
                        patch.object(g, '_npc_encounter', return_value={'name': 'empty', 'level': 1, 'units': units}), \
                        patch.object(g, '_daily_quest_touch') as quest:
                    self.assertEqual(self.error(self.route('Arena_Challenge')), 5006)
                    quest.assert_not_called()
                self.assertEqual((self.saved('arena_data')['score'],
                                  self.saved('arena_data')['challenges']), (100, 0))
                self.assertEqual(self.saved('arena_data')['records'], [])
                self.assertEqual(g.bots_local.bot_state(900001), before)
                self.fight.assert_not_called()

    def test_regular_failed_report_rolls_back_scores_attempts_and_rewards(self):
        self.state(field='arena_data')
        before = g.bots_local.bot_state(900001)
        medals = store.get_item_count(self.player['uin'], g._ARENA_MEDAL_ITEM)
        def refresh_before_transaction(*args):
            self.assertFalse(getattr(store._THREAD_STATE, 'atomic_depth', 0))
            # The guild refresh uses an independent SQLite connection. It must
            # be able to finish before player settlement reserves the writer.
            with closing(sqlite3.connect(store.DB_PATH, timeout=0)) as con, con:
                con.execute('UPDATE bots_state SET power=power WHERE uin=900001')
        with patch.object(g.bots_local, 'pick_opponent', return_value={'uin': 900001, 'power': 10}), \
                patch.object(g, '_refresh_arena_npcs', side_effect=refresh_before_transaction) as refresh, \
                patch.object(g, 'build_fight_report', side_effect=RuntimeError('test report failure')):
            with self.assertRaises(RuntimeError):
                self.route('Arena_Challenge')
            refresh.assert_called_once()
        self.assertEqual((self.saved('arena_data')['score'], self.saved('arena_data')['challenges']), (100, 0))
        self.assertEqual(self.saved('arena_data')['records'], [])
        self.assertEqual(g.bots_local.bot_state(900001), before)
        self.assertEqual(store.get_item_count(self.player['uin'], g._ARENA_MEDAL_ITEM), medals)


if __name__ == '__main__':
    unittest.main()
