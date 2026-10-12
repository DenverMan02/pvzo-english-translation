"""Final-route ladder regressions, using disposable SQLite and original assets.

These reproduce the reported fixed left-hand opponent and wins which did not
advance the player's place. Combat itself is stubbed; its durable settlement,
wire replies, opponent rotation and restart behavior are exercised together.
"""
import atexit
import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store

# Route registration performs migrations: importing must not touch players.db.
_bootstrap = tempfile.TemporaryDirectory(prefix='pvz_ladder_bootstrap_')
_previous_db = store.DB_PATH
store.set_db_path(str(Path(_bootstrap.name) / 'bootstrap.db'))
import pvz_poc_server as g
store.set_db_path(_previous_db)
atexit.register(_bootstrap.cleanup)

ORIGINAL_WEBROOT = Path(r'D:\pvzol本地最新最新ol\pvzol本地\webroot')
VIDEO_RANK_101_REWARD = [(4, 60), (2002, 50), (2014, 120)]


class LadderProgressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_ladder_routes_')
        self.addCleanup(self.tmp.cleanup)
        previous_db, previous_bot_db = store.DB_PATH, g.bots_local._DB_PATH
        self.addCleanup(lambda: store.set_db_path(previous_db))
        self.addCleanup(lambda: setattr(g.bots_local, '_DB_PATH', previous_bot_db))
        store.set_db_path(str(Path(self.tmp.name) / 'ladder.db'))
        store.init_db()
        self.player, _ = store.get_or_create_player(88011150)
        store.save_player(self.player['uin'], name='LadderRegression',
                          role_created=1, level=100, ticket=1000, present_ticket=0)
        self.player = store.get_or_create_player(self.player['uin'])[0]
        self.roster = [dict(uin=900001 + i, name='LadderBot%02d' % i,
                            level=100, score=2000 - i * 10, develop=0)
                       for i in range(50)]
        g.bots_local.initialize(db_path=store.DB_PATH)
        for bot in self.roster:
            store.get_or_create_player(bot['uin'])
            store.save_player(bot['uin'], name=bot['name'], role_created=1, level=100)
        with store._db() as con:
            g.bots_local._ensure_bots_state_table(con)
            con.executemany('INSERT INTO bots_state(uin,score,power) VALUES(?,?,?)',
                            [(b['uin'], b['score'], 20) for b in self.roster])
        self.left = [dict(pos=3, uid=10001, card_id=101, hp=100, atk=10,
                          def_=0, ability=0, level=1, is_leader=True, power=10)]
        self.right = [dict(self.left[0], pos=4, uid=20001)]
        self.now = time.mktime((2026, 10, 11, 12, 0, 0, 0, 0, -1))
        self.enterContext(patch.object(g.time, 'time', side_effect=lambda: self.now))
        self.enterContext(patch.object(g.bots_local, 'bots', return_value=self.roster))
        self.enterContext(patch.object(g, '_CLIENTDB_PATH',
                                       str(ORIGINAL_WEBROOT / 'clientDB.txt')))
        for name, replacement in {
            '_player_formation_power': lambda p: 10,
            'build_player_units': lambda *a, **kw: copy.deepcopy(self.left),
            '_npc_encounter': lambda *a: {'name': 'LadderBot', 'level': 100,
                                         'units': copy.deepcopy(self.right)},
            '_auto_puppets': lambda p: [], '_auto_puppet_list': lambda p: b'',
            '_daily_quest_touch': lambda *a: None,
            '_rank_guild_index': lambda: {},
        }.items():
            self.enterContext(patch.object(g, name, replacement))
        self.fight = self.enterContext(patch.object(
            g, 'run_battle', return_value=(True, 10, 20, [])))
        self.report = self.enterContext(patch.object(
            g, 'build_fight_report', return_value=b'ladder-regression-report'))
        self.enterContext(patch.dict(g._BATTLE_REPORTS, {}, clear=True))
        self.enterContext(patch.dict(g._NPC_ENCOUNTERS, {}, clear=True))
        self.enterContext(patch.object(g, '_RANK_BOT_CACHE', (0, [])))

    def route(self, name, body=b''):
        result = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + name + '_CS'](body, self.player)
        return result if isinstance(result, list) else [
            ('PVZ.Cmd.Cmd_' + name + '_SC', result)]

    def body(self, name, request=b''):
        replies = dict(self.route(name, request))
        response = 'PVZ.Cmd.Cmd_' + name + '_SC'
        self.assertIn(response, replies, list(replies))
        return replies[response]

    def rows(self, body, field):
        return [g.parse_varint_fields(raw)
                for raw in g.parse_repeated_submessages(body, field)]

    def info(self):
        return self.body('RankArena_GetMyInfo')

    def my_rank(self):
        return g.parse_varint_fields(self.info())[2]

    def ranks(self):
        return {r[1]: r[8] for r in self.rows(self.body('RankArena_GetRank'), 1)}

    def refresh(self):
        return self.rows(self.body('RankArena_GetOpponentInfo'), 1)

    def challenge(self, role, won=True, index=0):
        self.now += 301  # Original cooldown is five minutes.
        self.fight.return_value = (won, 10, 20, [])
        body = self.body('RankArena_Challenge', g.pb_int64_field(1, role)
                         + g.pb_varint_field(2, index))
        return g.parse_varint_fields(body), body

    def saved(self):
        fresh, _ = store.get_or_create_player(self.player['uin'])
        return g._load_json_state(fresh, 'rank_arena_data', {})

    def at(self, day, hour, minute, second=0):
        self.now = time.mktime((2026, 10, day, hour, minute, second, 0, 0, -1))

    def seed_rank_101(self):
        # EP81 at 02:55 supplies the authoritative rank-101 item preview.
        # Other rank tiers are absent from archived files.
        for i in range(50, 100):
            bot = dict(uin=900001 + i, name='LadderBot%02d' % i,
                       level=100, score=2000 - i * 10, develop=0)
            self.roster.append(bot)
            store.get_or_create_player(bot['uin'])
            store.save_player(bot['uin'], name=bot['name'], role_created=1, level=100)
            with store._db() as con:
                con.execute('INSERT INTO bots_state(uin,score,power) VALUES(?,?,?)',
                            (bot['uin'], bot['score'], 20))

    def rewards(self, body, field=12):
        return [(row[1], row[2]) for row in self.rows(body, field)]

    def assert_logic_error(self, reply, code=5006):
        self.assertEqual(reply[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertEqual(g.parse_varint_fields(reply[0][1])[1], code)

    def test_refresh_changes_all_three_slots_and_myinfo_keeps_the_batch(self):
        first = self.rows(self.info(), 10)
        for _ in range(5):
            next_batch = self.refresh()
            self.assertEqual(len(next_batch), 3)
            self.assertEqual(len({row[1] for row in next_batch}), 3)
            self.assertTrue(all(a[1] != b[1] for a, b in zip(first, next_batch)),
                            (first, next_batch))
            # The client performs an automatic status read after refreshing.
            self.assertEqual(self.rows(self.info(), 10), next_batch)
            self.assertEqual(self.rows(self.info(), 10), next_batch)
            first = next_batch

    def test_winning_swaps_places_and_every_rank_route_agrees(self):
        before = self.ranks()
        self.assertEqual(before[self.player['uin']], 51)
        offered = self.refresh()
        target = min(offered, key=lambda row: row[8])
        old_bot_scores = {uin: row['score'] for uin, row
                          in g.bots_local.bot_states().items()}
        fields, response = self.challenge(target[1], index=offered.index(target))
        after = self.ranks()
        self.assertEqual(fields[2], 1)
        self.assertEqual(fields[1], target[8])
        self.assertEqual(after[self.player['uin']], target[8])
        self.assertEqual(after[target[1]], before[self.player['uin']])
        self.assertEqual(sorted(after.values()), list(range(1, 52)))
        self.assertEqual(self.my_rank(), target[8])
        self.assertEqual(self.saved()['score'], 100)
        self.assertEqual({uin: row['score'] for uin, row
                          in g.bots_local.bot_states().items()}, old_bot_scores)
        generic_self = self.rows(self.body('GetSelfRank', g.pb_varint_field(1, 5)), 6)
        self.assertEqual((generic_self[0][1], generic_self[0][2]),
                         (target[8], self.player['uin']))
        page = self.body('GetRankList', g.pb_varint_field(1, 5)
                         + g.pb_varint_field(2, (target[8] - 1) // g._RANK_PAGE_SIZE + 1))
        page_ranks = {row[2]: row[1] for row in self.rows(page, 8) if row.get(1)}
        self.assertEqual(page_ranks[self.player['uin']], target[8])
        for row in self.refresh():
            self.assertEqual(row[8], after[row[1]])
        record = self.rows(response, 3)[0]
        self.assertEqual(g.parse_repeated_submessages(self.body(
            'Battle_QueryReport', g.pb_int64_field(1, record[1])), 1),
            [b'ladder-regression-report'])

    def test_loss_and_winning_against_lower_place_leave_rank_unchanged(self):
        before = self.ranks()
        high = next(uin for uin, rank in before.items() if rank == 1)
        fields, _ = self.challenge(high, won=False)
        self.assertEqual((fields[1], fields[2]), (51, 0))
        self.assertEqual(self.ranks(), before)
        self.challenge(high, won=True)
        self.assertEqual(self.my_rank(), 1)
        advanced = self.ranks()
        target = self.refresh()[0]
        fields, _ = self.challenge(target[1], won=True)
        self.assertEqual((fields[1], fields[2]), (1, 1))
        self.assertEqual(self.ranks(), advanced)

    def test_ordinary_arena_bot_scores_do_not_reorder_the_ladder(self):
        before = self.ranks()
        target = self.refresh()[0]
        self.challenge(target[1])
        progressed = self.ranks()
        self.assertNotEqual(progressed, before)
        with store._db() as con:
            con.execute('UPDATE bots_state SET score=9999999-uin')
        self.assertEqual(self.ranks(), progressed)
        self.player = store.get_or_create_player(self.player['uin'])[0]
        self.assertEqual(self.my_rank(), progressed[self.player['uin']])

    def test_rank_and_bot_displacement_survive_a_fresh_server_process(self):
        target = self.refresh()[0]
        self.challenge(target[1])
        expected = self.ranks()
        # A new interpreter has neither route closures nor ladder/opponent caches.
        code = '''import json,sys
sys.path.insert(0,sys.argv[1])
import player_store as store
store.set_db_path(sys.argv[2])
import pvz_poc_server as g
g.bots_local.initialize(db_path=store.DB_PATH)
g._player_formation_power=lambda p:10
g._CLIENTDB_PATH=sys.argv[3]
p=store.get_or_create_player(int(sys.argv[4]))[0]
messages=g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_RankArena_GetRank_CS'](b'',p)
body=dict(messages)['PVZ.Cmd.Cmd_RankArena_GetRank_SC']
rows=[g.parse_varint_fields(raw) for raw in g.parse_repeated_submessages(body,1)]
print('LADDER_RESULT:'+json.dumps({row[1]:row[8] for row in rows}))
'''
        result = subprocess.run([sys.executable, '-c', code, str(SERVER),
                                 store.DB_PATH, str(ORIGINAL_WEBROOT / 'clientDB.txt'),
                                 str(self.player['uin'])], cwd=SERVER,
                                capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        encoded = next(line[len('LADDER_RESULT:'):] for line in result.stdout.splitlines()
                       if line.startswith('LADDER_RESULT:'))
        actual = {int(uin): rank for uin, rank in json.loads(encoded).items()}
        self.assertEqual(actual, expected)

    def test_failed_report_does_not_commit_a_rank_swap_or_use_an_attempt(self):
        before = self.ranks()
        state_before = self.saved()
        bots_before = g.bots_local.bot_states()
        target = self.refresh()[0]
        with patch.object(g, 'build_fight_report', side_effect=RuntimeError('QA report failure')):
            with self.assertRaises(RuntimeError):
                self.challenge(target[1])
        self.assertEqual(self.ranks(), before)
        saved = self.saved()
        self.assertEqual(saved['challenges'], state_before['challenges'])
        self.assertEqual(saved['records'], state_before['records'])
        self.assertEqual(g.bots_local.bot_states(), bots_before)

    def test_five_free_then_five_paid_attempts_and_original_cooldown(self):
        for used in range(5):
            info = g.parse_varint_fields(self.info())
            self.assertEqual((info[5], info[6]), (5 - used, 5))
            self.challenge(self.refresh()[0][1], won=False)
            self.assertEqual(self.player['ticket'], 1000)
        with patch.dict(g.SERVER_CONFIG_OVERRIDES,
                        {'sRankArenaPayChallenge': '50,50,50,50,50'}):
            for paid in range(5):
                info = g.parse_varint_fields(self.info())
                self.assertEqual((info[5], info[6]), (0, 5 - paid))
                self.challenge(self.refresh()[0][1], won=False)
                self.assertEqual(self.player['ticket'], 1000 - 50 * (paid + 1))
            self.now += 301
            before = self.saved()
            reply = self.route('RankArena_Challenge',
                               g.pb_int64_field(1, self.roster[0]['uin']))
            self.assertEqual(reply[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
            self.assertEqual(g.parse_varint_fields(reply[0][1])[1], 7705)
            self.assertEqual(self.saved()['challenges'], 10)
            self.assertEqual(self.saved()['records'], before['records'])
            self.assertEqual(self.player['ticket'], 750)

    def test_cooldown_rejects_repeat_without_settlement_and_expires_after_five_minutes(self):
        target = self.refresh()[0][1]
        self.challenge(target, won=False)
        original = self.saved()
        before = self.ranks()
        self.assertEqual(g.parse_varint_fields(self.info())[7], 300)
        self.now += 299
        reply = self.route('RankArena_Challenge', g.pb_int64_field(1, target))
        self.assertEqual(reply[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertEqual(g.parse_varint_fields(reply[0][1])[1], 8010)
        self.assertEqual(self.saved()['challenges'], original['challenges'])
        self.assertEqual(self.ranks(), before)
        self.now += 1
        response = self.body('RankArena_Challenge', g.pb_int64_field(1, target))
        self.assertEqual(g.parse_varint_fields(response)[2], 0)
        self.assertEqual(self.saved()['challenges'], original['challenges'] + 1)

    def test_paid_attempt_spends_blue_diamonds_before_gold(self):
        self.info()
        state = self.saved()
        state['challenges'] = 5
        g._save_json_state(self.player, 'rank_arena_data', state)
        store.save_player(self.player['uin'], present_ticket=30, ticket=40)
        self.player.update(present_ticket=30, ticket=40)
        with patch.dict(g.SERVER_CONFIG_OVERRIDES,
                        {'sRankArenaPayChallenge': '50,50,50,50,50'}):
            target = self.refresh()[0][1]
            self.now += 301
            self.fight.return_value = (False, 10, 20, [])
            replies = dict(self.route('RankArena_Challenge', g.pb_int64_field(1, target)))
        self.assertIn('PVZ.Cmd.Cmd_RankArena_Challenge_SC', replies)
        self.assertEqual((self.player['present_ticket'], self.player['ticket']), (0, 20))
        self.assertEqual(g.parse_varint_fields(replies['PVZ.Cmd.Cmd_SyncPresentTicket_SC'])[1], 0)
        self.assertEqual(g.parse_varint_fields(replies['PVZ.Cmd.Cmd_SyncTicket_SC'])[1], 20)

    def test_rank_rewards_preview_settlement_window_replay_and_next_day(self):
        self.seed_rank_101()
        self.at(11, 20, 59)
        initial = self.info()
        self.assertEqual(g.parse_varint_fields(initial)[2], 101)
        self.assertEqual(self.rewards(initial), VIDEO_RANK_101_REWARD)
        prior = self.body('RankArena_TakeDailyReward')
        self.assertEqual(self.rewards(prior, 1), VIDEO_RANK_101_REWARD)
        self.assertEqual(self.player['present_ticket'], 60)
        self.assertEqual(store.get_item_count(self.player['uin'], 2002), 50)
        self.assertEqual(store.get_item_count(self.player['uin'], 2014), 120)

        for minute, second in ((0, 0), (9, 59)):
            self.at(11, 21, minute, second)
            self.assertEqual(g.parse_varint_fields(self.info())[13], 0)
            self.assert_logic_error(self.route('RankArena_TakeDailyReward'))
            self.assertEqual(self.player['present_ticket'], 60)
        self.at(11, 21, 10)
        self.assertEqual(g.parse_varint_fields(self.info())[13], 1)
        high = next(uin for uin, rank in self.ranks().items() if rank == 1)
        ladder = importlib.import_module('ladder_rules_repairs_20261011')
        choose = ladder.random.choice

        def bean_reward(population):
            if tuple(population) == ((1, 10000), (2002, 1)):
                return (2002, 1)
            return choose(population)

        # Isolate the daily award from the separate random battle reward.
        with patch.object(ladder.random, 'choice', side_effect=bean_reward):
            self.challenge(high)
        self.assertEqual(store.get_item_count(self.player['uin'], 2002), 51)
        post_win = self.info()
        self.assertEqual(g.parse_varint_fields(post_win)[2], 1)
        # The post-settlement victory cannot rewrite that day's rank-101 award.
        self.assertEqual(self.rewards(post_win), VIDEO_RANK_101_REWARD)
        claimed = self.body('RankArena_TakeDailyReward')
        self.assertEqual(self.rewards(claimed, 1), VIDEO_RANK_101_REWARD)
        self.assertEqual(self.player['present_ticket'], 120)
        self.assertEqual(store.get_item_count(self.player['uin'], 2002), 101)
        self.assertEqual(store.get_item_count(self.player['uin'], 2014), 240)
        self.assertEqual(g.parse_varint_fields(self.info())[13], 0)
        self.assert_logic_error(self.route('RankArena_TakeDailyReward'))
        self.player = store.get_or_create_player(self.player['uin'])[0]
        self.assert_logic_error(self.route('RankArena_TakeDailyReward'))
        self.assertEqual(self.player['present_ticket'], 120)

        for hour, minute in ((0, 1), (20, 59)):
            self.at(12, hour, minute)
            self.assertEqual(g.parse_varint_fields(self.info())[13], 0)
            self.assert_logic_error(self.route('RankArena_TakeDailyReward'))
            self.assertEqual(self.player['present_ticket'], 120)
        self.at(12, 21, 0)
        next_period = self.info()
        self.assertEqual(g.parse_varint_fields(next_period)[13], 0)
        # The existing offline tier is deliberately retained where no original
        # server table exists. The new period must use the latest occupied rank.
        self.assertEqual(self.rewards(next_period), [(1, 5000), (4, 10)])
        self.at(12, 21, 10)
        self.assertEqual(g.parse_varint_fields(self.info())[13], 1)
        next_claim = self.body('RankArena_TakeDailyReward')
        self.assertEqual(self.rewards(next_claim, 1), self.rewards(next_period))
        self.assertEqual(self.player['present_ticket'], 130)

    def test_reward_save_failure_rolls_back_wallet_and_allows_retry(self):
        self.seed_rank_101()
        self.at(11, 21, 10)
        self.assertEqual(self.rewards(self.info()), VIDEO_RANK_101_REWARD)
        save = g._save_json_state

        def fail_claim(player, field, state):
            if field == 'rank_arena_data' and state.get('reward_taken'):
                raise RuntimeError('QA reward persistence failure')
            return save(player, field, state)

        with patch.object(g, '_save_json_state', side_effect=fail_claim):
            with self.assertRaises(RuntimeError):
                self.route('RankArena_TakeDailyReward')
        self.assertEqual(self.player['present_ticket'], 0)
        self.assertEqual(store.get_item_count(self.player['uin'], 2002), 0)
        self.assertEqual(store.get_item_count(self.player['uin'], 2014), 0)
        fresh = store.get_or_create_player(self.player['uin'])[0]
        self.assertEqual(fresh['present_ticket'], 0)
        self.assertFalse(self.saved()['reward_taken'])
        self.assertEqual(g.parse_varint_fields(self.info())[13], 1)
        self.body('RankArena_TakeDailyReward')
        self.assertEqual(self.player['present_ticket'], 60)

    def test_legacy_taken_flag_survives_first_migration_across_midnight(self):
        self.seed_rank_101()
        self.at(11, 12, 0)
        legacy = dict(day='20261010', score=100, challenges=0, records=[],
                      wins=0, losses=0, taken=True, last_rank=101)
        g._save_json_state(self.player, 'rank_arena_data', legacy)
        self.assertEqual(g.parse_varint_fields(self.info())[13], 0)
        self.assert_logic_error(self.route('RankArena_TakeDailyReward'))
        self.assertEqual(self.player['present_ticket'], 0)
        self.assertEqual(store.get_item_count(self.player['uin'], 2002), 0)
        self.assertTrue(self.saved()['reward_taken'])
        self.at(11, 21, 10)
        self.assertEqual(g.parse_varint_fields(self.info())[13], 1)
        self.body('RankArena_TakeDailyReward')
        self.assertEqual(self.player['present_ticket'], 60)

    def test_updated_myinfo_precedes_challenge_result_for_the_victory_popup(self):
        offered = self.refresh()
        target = min(offered, key=lambda row: row[8])
        self.now += 301
        replies = self.route('RankArena_Challenge', g.pb_int64_field(1, target[1])
                             + g.pb_varint_field(2, offered.index(target)))
        names = [name for name, _ in replies]
        info_name = 'PVZ.Cmd.Cmd_RankArena_GetMyInfo_SC'
        result_name = 'PVZ.Cmd.Cmd_RankArena_Challenge_SC'
        self.assertLess(names.index(info_name), names.index(result_name))
        fields = g.parse_varint_fields(dict(replies)[info_name])
        self.assertEqual((fields[2], fields[3], fields[7]), (target[8], 1, 300))
        self.assertEqual(g.parse_varint_fields(dict(replies)[result_name])[1], target[8])

    def test_ladder_battle_report_remains_queryable_after_memory_cache_is_cleared(self):
        _, result = self.challenge(self.refresh()[0][1])
        report_uid = self.rows(result, 3)[0][1]
        awarded = (self.player['coin'], store.get_item_count(self.player['uin'], 2002))
        state = self.saved()
        g._BATTLE_REPORTS.clear()
        store.close_thread_db()
        self.player = store.get_or_create_player(self.player['uin'])[0]
        ladder = importlib.import_module('ladder_rules_repairs_20261011')
        with patch.object(ladder.random, 'choice', side_effect=AssertionError('replay redrew a reward')):
            for request in ('Battle_QueryReport', 'Battle_QueryRankReport'):
                with self.subTest(request=request):
                    result = self.body(request, g.pb_int64_field(1, report_uid))
                    self.assertEqual(g.parse_repeated_submessages(result, 1),
                                     [b'ladder-regression-report'])
                    fresh = store.get_or_create_player(self.player['uin'])[0]
                    self.assertEqual((fresh['coin'], store.get_item_count(fresh['uin'], 2002)), awarded)
                    self.assertEqual(self.saved(), state)


if __name__ == '__main__':
    unittest.main()
