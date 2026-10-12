"""Arena ranks and next-day reward snapshots, through final routes and disposable saves."""
import atexit
import copy
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
BOOT = tempfile.TemporaryDirectory(prefix='pvz_arena_consistency_boot_')
store.set_db_path(str(Path(BOOT.name) / 'boot.db'))
store.init_db()
atexit.register(BOOT.cleanup)
atexit.register(store.close_thread_db)
with patch.object(logging, 'FileHandler', return_value=logging.NullHandler()), patch.object(logging, 'basicConfig'):
    import pvz_poc_server as g
import arena_consistency_repairs_20261011
arena_consistency_repairs_20261011.install(g)


class ArenaConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_arena_consistency_')
        self.addCleanup(self.tmp.cleanup)
        old_db, old_bot_db = store.DB_PATH, g.bots_local._DB_PATH
        self.addCleanup(lambda: setattr(g.bots_local, '_DB_PATH', old_bot_db))
        self.addCleanup(lambda: store.set_db_path(old_db))
        store.set_db_path(str(Path(self.tmp.name) / 'players.db'))
        store.init_db()
        self.addCleanup(store.close_thread_db)
        self.p, _ = store.get_or_create_player(88011111)
        self.p.update(name='RankConsistency', level=100, ticket=100, present_ticket=0)
        store.save_player(self.p['uin'], name=self.p['name'], level=100, ticket=100, present_ticket=0)
        self.bot = {'uin': 900001, 'name': 'RankBot', 'level': 1, 'score': 105}
        g.bots_local.initialize(db_path=store.DB_PATH)
        with store._db() as con:
            g.bots_local._ensure_bots_state_table(con)
            con.execute('INSERT INTO bots_state(uin,score,power) VALUES(900001,105,10)')
        unit = {'pos': 3, 'uid': 10001, 'card_id': 101, 'hp': 100, 'atk': 10,
                'def_': 0, 'ability': 0, 'level': 1, 'is_leader': True, 'power': 10}
        self.report = b'playable-arena-report'
        replacements = {
            '_player_formation_power': lambda p: 10,
            'build_player_units': lambda *a, **kw: [copy.deepcopy(unit)],
            '_npc_encounter': lambda *a: {'name': self.bot['name'], 'level': 1, 'units': [copy.deepcopy(unit)]},
            '_auto_puppets': lambda p: [], '_auto_puppet_list': lambda p: b'',
            '_capture_arena_npcs': lambda *a, **kw: None,
            '_refresh_arena_npcs': lambda *a, **kw: None,
            '_daily_quest_touch': lambda *a: None,
            'build_player_diamond': lambda p: b'',
        }
        for name, replacement in replacements.items():
            self.enterContext(patch.object(g, name, replacement))
        self.enterContext(patch.object(g.bots_local, 'bots', return_value=[self.bot]))
        self.enterContext(patch.object(g.bots_local, 'pick_opponent', return_value={'uin': 900001, 'power': 10}))
        self.fight = self.enterContext(patch.object(g, 'run_battle', return_value=(True, 10, 10, [])))
        self.report_builder = self.enterContext(patch.object(g, 'build_fight_report', return_value=self.report))
        self.enterContext(patch.dict(g._BATTLE_REPORTS, {}, clear=True))
        self.enterContext(patch.dict(g.SERVER_CONFIG_OVERRIDES,
                                    {'iAllServerRankArenaRankFreeChallengeTimes': '5',
                                     'sAllServerRankArenaRankPayChallengeFee': ','.join(['50'] * 10)}))
        for field in ('arena_data', 'rank_arena_data', 'allserver_rank_arena_data'):
            self.save(field)

    def save(self, field, **values):
        state = {'day': time.strftime('%Y%m%d'), 'score': 100, 'challenges': 0,
                 'wins': 0, 'losses': 0, 'records': [], 'claim_schema': 1,
                 'daily_score': 0, 'claimed_score': 0, 'last_rank': 2}
        state.update(values)
        g._save_json_state(self.p, field, state)
        return state

    def route(self, name, body=b''):
        return g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + name + '_CS'](body, self.p)

    def body(self, name, request=b''):
        return dict(self.route(name, request))['PVZ.Cmd.Cmd_' + name + '_SC']

    def current(self):
        info = self.body('AllServerRankArena_Info')
        return g.parse_varint_fields(g.parse_repeated_submessages(info, 1)[0])[9]

    def saved(self):
        store.close_thread_db()
        loaded, _ = store.get_or_create_player(self.p['uin'])
        return g._load_json_state(loaded, 'allserver_rank_arena_data', {})

    def challenge(self):
        return g.parse_varint_fields(self.body('AllServerRankArena_Challenge', g.pb_varint_field(1, 900001)))

    def test_regular_info_and_challenge_use_board_position_and_preserve_grade_and_report(self):
        before = g.parse_varint_fields(self.body('Arena_GetMyInfo'))
        board = [g.parse_varint_fields(row)[1] for row in g.parse_repeated_submessages(self.body('Arena_GetRank'), 1)]
        self.assertEqual(before[1], board.index(self.p['uin']) + 1)
        self.assertEqual((before[3], before[4]), (1, 2))
        challenge = self.body('Arena_Challenge')
        after = g.parse_varint_fields(challenge)
        self.assertEqual((after[1], after[2]), (1, 105))
        self.assertEqual(g.parse_varint_fields(self.body('Arena_GetMyInfo'))[1], after[1])
        recorder = g.parse_repeated_submessages(challenge, 3)[0]
        report_uid = g.parse_varint_fields(recorder)[1]
        queried = self.body('Battle_QueryReport', g.pb_varint_field(1, report_uid))
        self.assertEqual(g.parse_repeated_submessages(queried, 1), [self.report])

    def test_cross_win_saves_current_rank_without_reopening_info_and_rolls_reward_forward(self):
        self.assertEqual(self.current(), 2)
        result = self.challenge()
        self.assertEqual((result[2], result[5]), (1, 2))
        saved = self.saved()
        self.assertEqual((saved['last_rank'], saved['max_rank']), (1, 1))
        # Simulate closing the client immediately after the battle; reload the
        # actual stored save rather than keeping the test dictionary alive.
        self.p, _ = store.get_or_create_player(self.p['uin'])
        with patch.object(g.time, 'strftime', return_value='20990102'):
            next_day = g._asra_state(self.p)
            self.assertEqual(next_day['daily_reward_rank'], 1)
            self.assertEqual((next_day['challenges'], next_day['daily_score']), (0, 0))

    def test_cross_105_list_and_self_rank_use_cross_state_and_refresh_immediately(self):
        self.save('rank_arena_data', score=10000, wins=99, losses=98)
        request = g.pb_varint_field(1, 105)
        with patch.object(g, '_rank_player_row', side_effect=AssertionError('ladder data must not feed cross-server board')):
            rows = g.parse_repeated_submessages(self.route('GetRankList', request), 105)
            real = [g.parse_varint_fields(row) for row in rows if g.parse_varint_fields(row).get(1)]
            self.assertEqual([(row[1], row[2]) for row in real], [(900001, 1), (self.p['uin'], 2)])
            mine = g.parse_varint_fields(g.parse_repeated_submessages(self.route('GetSelfRank', request), 105)[0])
            self.assertEqual((mine[2], mine[5], mine[6]), (self.current(), 0, 0))
            self.challenge()
            mine_after = g.parse_varint_fields(g.parse_repeated_submessages(self.route('GetSelfRank', request), 105)[0])
            self.assertEqual((mine_after[2], mine_after[5], mine_after[6]), (1, 1, 0))

    def test_cross_score_ties_keep_same_order_in_info_rank_and_generic_board(self):
        self.save('allserver_rank_arena_data', score=105)
        info_rank = self.current()
        direct_rank = g.parse_varint_fields(self.body('AllServerRankArena_Rank'))[1]
        request = g.pb_varint_field(1, 105)
        mine = g.parse_varint_fields(g.parse_repeated_submessages(self.route('GetSelfRank', request), 105)[0])
        self.assertEqual((info_rank, direct_rank, mine[2]), (2, 2, 2))

    def test_cross_rank_query_remembers_latest_observed_rank_but_keeps_yesterday_snapshot(self):
        self.save('allserver_rank_arena_data', score=200, daily_reward_rank=2, last_rank=2)
        self.assertEqual(g.parse_varint_fields(self.body('AllServerRankArena_Rank'))[1], 1)
        self.assertEqual((self.saved()['last_rank'], self.saved()['daily_reward_rank']), (1, 2))

    def test_cross_rejected_match_cannot_change_rank_snapshot(self):
        self.save('allserver_rank_arena_data', challenges=15, daily_reward_rank=1)
        denied = self.route('AllServerRankArena_Challenge', g.pb_varint_field(1, 900001))
        self.assertEqual(g.parse_varint_fields(denied[0][1])[1], 7705)
        state = self.saved()
        self.assertEqual((state['last_rank'], state['daily_reward_rank'], state['challenges']), (2, 1, 15))
        self.fight.assert_not_called()

    def test_failed_paid_report_rolls_back_wallet_scores_and_rank_snapshot(self):
        self.save('allserver_rank_arena_data', challenges=5)
        before_bot = g.bots_local.bot_state(900001)
        self.report_builder.side_effect = RuntimeError('report unavailable')
        with self.assertRaises(RuntimeError):
            self.challenge()
        state = self.saved()
        self.assertEqual((state['last_rank'], state['score'], state['challenges']), (2, 100, 5))
        self.assertEqual(self.p['ticket'], 100)
        self.assertEqual(g.bots_local.bot_state(900001), before_bot)


if __name__ == '__main__':
    unittest.main()
