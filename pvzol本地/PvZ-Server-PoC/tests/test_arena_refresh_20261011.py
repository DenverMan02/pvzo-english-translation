"""Arena opponent batches and immediate post-battle counters on final routes.

Random sampling is replaced by a seeded sampler with deliberately unsorted
output. Assertions therefore do not depend on a lucky live random draw.
"""
import atexit
import copy
import importlib
from pathlib import Path
import random
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store

_bootstrap = tempfile.TemporaryDirectory(prefix='pvz_arena_refresh_bootstrap_')
_previous_db = store.DB_PATH
store.set_db_path(str(Path(_bootstrap.name) / 'bootstrap.db'))
import pvz_poc_server as g
store.set_db_path(_previous_db)
atexit.register(_bootstrap.cleanup)


class ArenaRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_arena_refresh_')
        self.addCleanup(self.tmp.cleanup)
        old_db, old_bot_db = store.DB_PATH, g.bots_local._DB_PATH
        self.addCleanup(lambda: store.set_db_path(old_db))
        self.addCleanup(lambda: setattr(g.bots_local, '_DB_PATH', old_bot_db))
        store.set_db_path(str(Path(self.tmp.name) / 'arena.db'))
        store.init_db()
        self.player, _ = store.get_or_create_player(88011151)
        store.save_player(self.player['uin'], name='ArenaRefreshQA', role_created=1,
                          level=100, ticket=1000, present_ticket=0)
        self.player = store.get_or_create_player(self.player['uin'])[0]
        self.roster = [dict(uin=900001 + i, name='RefreshBot%02d' % i,
                            level=60 + i, score=2000 - 10 * i, develop=0)
                       for i in range(50)]
        g.bots_local.initialize(db_path=store.DB_PATH)
        for bot in self.roster:
            store.get_or_create_player(bot['uin'])
            store.save_player(bot['uin'], name=bot['name'], role_created=1, level=bot['level'])
        with store._db() as con:
            g.bots_local._ensure_bots_state_table(con)
            con.executemany('INSERT INTO bots_state(uin,score,power) VALUES(?,?,?)',
                            [(bot['uin'], bot['score'], 20) for bot in self.roster])
        self.left = [dict(pos=3, uid=10001, card_id=101, hp=100, atk=10,
                          def_=0, ability=0, level=1, is_leader=True, power=10)]
        self.right = [dict(self.left[0], pos=4, uid=20001)]
        self.now = time.mktime((2026, 10, 11, 12, 0, 0, 0, 0, -1))
        self.enterContext(patch.object(g.time, 'time', side_effect=lambda: self.now))
        self.enterContext(patch.object(g.bots_local, 'bots', return_value=self.roster))
        self.enterContext(patch.object(g, '_CLIENTDB_PATH',
            r'D:\pvzol本地最新最新ol\pvzol本地\webroot\clientDB.txt'))
        for name, replacement in {
            '_player_formation_power': lambda p: 10,
            'build_player_units': lambda *a, **kw: copy.deepcopy(self.left),
            '_npc_encounter': lambda *a: dict(name='RefreshBot', level=100,
                                             units=copy.deepcopy(self.right)),
            '_auto_puppets': lambda p: [], '_auto_puppet_list': lambda p: b'',
            '_daily_quest_touch': lambda *a: None, '_rank_guild_index': lambda: {},
        }.items():
            self.enterContext(patch.object(g, name, replacement))
        self.enterContext(patch.object(g, 'run_battle', return_value=(True, 10, 20, [])))
        self.real_report_builder = g.build_fight_report
        self.enterContext(patch.object(g, 'build_fight_report',
                                       return_value=b'arena-refresh-report'))
        self.enterContext(patch.dict(g._BATTLE_REPORTS, {}, clear=True))
        self.enterContext(patch.dict(g._NPC_ENCOUNTERS, {}, clear=True))
        self.enterContext(patch.object(g, '_RANK_BOT_CACHE', (0, [])))
        self.enterContext(patch.dict(g.SERVER_CONFIG_OVERRIDES,
            {'iAllServerRankArenaRankFreeChallengeTimes': '5',
             'sAllServerRankArenaRankPayChallengeFee': ','.join(['50'] * 10)}))
        self.bot_ids = {bot['uin'] for bot in self.roster}
        self.bot_levels = {bot['uin']: bot['level'] for bot in self.roster}

    def route(self, name, body=b''):
        result = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + name + '_CS'](body, self.player)
        return result if isinstance(result, list) else [
            ('PVZ.Cmd.Cmd_' + name + '_SC', result)]

    def body(self, name, request=b''):
        replies = dict(self.route(name, request))
        sc = 'PVZ.Cmd.Cmd_' + name + '_SC'
        self.assertIn(sc, replies, list(replies))
        return replies[sc]

    def rows(self, body, field):
        return [g.parse_varint_fields(raw)
                for raw in g.parse_repeated_submessages(body, field)]

    def ids(self, rows):
        return tuple(row[1] for row in rows)

    def ladder_info(self):
        return self.body('RankArena_GetMyInfo')

    def cross_info(self):
        return self.body('AllServerRankArena_Info')

    def cross_board(self):
        result, page, count = {}, 1, None
        while count is None or len(result) < count:
            body = self.body('GetRankList', g.pb_varint_field(1, 105)
                             + g.pb_varint_field(2, page))
            count = g.parse_varint_fields(body)[3]
            actual = [row for row in self.rows(body, 105) if row[1]]
            self.assertTrue(actual or count == 0, (page, count))
            for row in actual:
                self.assertNotIn(row[1], result)
                result[row[1]] = row[2]
            page += 1
        self.assertEqual(len(set(result.values())), count)
        self.assertEqual(sorted(result.values()), list(range(1, count + 1)))
        return result

    def assert_cross_ranks(self, expected):
        self.assertEqual(self.cross_board(), expected)
        info = self.cross_info()
        own_rank = expected[self.player['uin']]
        self.assertEqual(self.rows(info, 1)[0][9], own_rank)
        self.assertEqual(g.parse_varint_fields(self.body('AllServerRankArena_Rank'))[1], own_rank)
        self_rank = self.body('GetSelfRank', g.pb_varint_field(1, 105))
        self.assertEqual(self.rows(self_rank, 105)[0][2], own_rank)
        podium = self.rows(info, 8)
        self.assertEqual(self.ids(podium), tuple(sorted(expected, key=expected.get)[:5]))
        for row in podium + self.rows(info, 9):
            self.assertEqual(row[9], expected[row[1]])
        return info

    def assert_valid(self, rows, size, rank_field):
        self.assertEqual(len(rows), size)
        self.assertEqual(len(set(self.ids(rows))), size)
        self.assertTrue(set(self.ids(rows)).issubset(self.bot_ids), rows)
        self.assertNotIn(self.player['uin'], self.ids(rows))
        self.assertTrue(all(row[rank_field] > 0 for row in rows), rows)
        level_field = 4 if rank_field == 8 else 5
        self.assertTrue(all(row[level_field] == self.bot_levels[row[1]] for row in rows), rows)

    def seeded_sample(self, module_name, seed=20261011):
        module = importlib.import_module(module_name)
        rng = random.Random(seed)

        def role(row):
            if isinstance(row, int):
                return row
            if isinstance(row, dict):
                return int(row['uin'])
            # Cross-server leaderboard rows are (row_tuple, rank) pairs.
            return int(row[0][0]) if isinstance(row[0], (tuple, list)) else int(row[0])

        def sample(population, count):
            selected = rng.sample(list(population), count)
            # Fixed unsorted output makes an implementation that silently
            # replaces a random draw with consecutive rank rows detectable.
            return sorted(selected, key=role, reverse=True)

        return self.enterContext(patch.object(module.random, 'sample', side_effect=sample))

    def test_ladder_uses_random_draws_changes_each_slot_and_status_reads_keep_the_batch(self):
        draws = self.seeded_sample('ladder_rules_repairs_20261011')
        previous = self.rows(self.ladder_info(), 10)
        groups = []
        for _ in range(10):
            calls = draws.call_count
            rows = self.rows(self.body('RankArena_GetOpponentInfo'), 1)
            self.assertGreater(draws.call_count, calls)
            self.assert_valid(rows, 3, 8)
            self.assertTrue(all(a[1] != b[1] for a, b in zip(previous, rows)),
                            (previous, rows))
            ranks = [row[8] for row in rows]
            self.assertNotEqual(ranks, sorted(ranks))
            self.assertGreater(max(ranks) - min(ranks), 2)
            groups.append(self.ids(rows))
            calls = draws.call_count
            self.assertEqual(self.rows(self.ladder_info(), 10), rows)
            self.player = store.get_or_create_player(self.player['uin'])[0]
            self.assertEqual(self.rows(self.ladder_info(), 10), rows)
            self.assertEqual(draws.call_count, calls)
            previous = rows
        self.assertGreater(len(set(groups)), 1)

    def test_ladder_consumes_attempt_before_report_and_retains_it_after_report_queries(self):
        self.seeded_sample('ladder_rules_repairs_20261011')
        for remaining in (4, 3):
            target = self.rows(self.ladder_info(), 10)[0]
            self.now += 301
            replies = self.route('RankArena_Challenge', g.pb_int64_field(1, target[1])
                                 + g.pb_varint_field(2, 0))
            names = [name for name, _ in replies]
            info_name = 'PVZ.Cmd.Cmd_RankArena_GetMyInfo_SC'
            challenge_name = 'PVZ.Cmd.Cmd_RankArena_Challenge_SC'
            self.assertLess(names.index(info_name), names.index(challenge_name))
            updated = g.parse_varint_fields(dict(replies)[info_name])
            self.assertEqual((updated[5], updated[6]), (remaining, 5))
            report_uid = self.rows(dict(replies)[challenge_name], 3)[0][1]
            report = self.body('Battle_QueryReport', g.pb_int64_field(1, report_uid))
            self.assertEqual(g.parse_repeated_submessages(report, 1), [b'arena-refresh-report'])
            self.assertEqual(g.parse_varint_fields(self.ladder_info())[5], remaining)
            self.player = store.get_or_create_player(self.player['uin'])[0]
            self.assertEqual(g.parse_varint_fields(self.ladder_info())[5], remaining)

    def test_ladder_rank_one_refreshes_three_random_lower_opponents(self):
        draws = self.seeded_sample('ladder_rules_repairs_20261011')
        self.ladder_info()
        self.body('RankArena_Challenge', g.pb_int64_field(1, self.roster[0]['uin']))
        initial = self.ladder_info()
        self.assertEqual(g.parse_varint_fields(initial)[2], 1)
        previous = self.rows(initial, 10)
        seen = []
        for _ in range(10):
            rows = self.rows(self.body('RankArena_GetOpponentInfo'), 1)
            self.assert_valid(rows, 3, 8)
            self.assertNotEqual(set(self.ids(rows)), set(self.ids(previous)))
            self.assertTrue(all(row[8] > 1 for row in rows))
            seen.append({row[8] for row in rows})
            calls = draws.call_count
            info = self.ladder_info()
            self.assertEqual(self.rows(info, 10), rows)
            self.assertEqual(g.parse_varint_fields(info)[5], 4)
            self.assertEqual(draws.call_count, calls)
            previous = rows
        self.assertTrue(any(ranks != {2, 3, 4} for ranks in seen), seen)

    def test_ladder_rank_four_refresh_mixes_promotion_targets_with_random_lower_rank(self):
        draws = self.seeded_sample('ladder_rules_repairs_20261011')
        self.ladder_info()
        self.body('RankArena_Challenge', g.pb_int64_field(1, self.roster[3]['uin']))
        initial = self.ladder_info()
        self.assertEqual(g.parse_varint_fields(initial)[2], 4)
        previous = self.rows(initial, 10)
        for _ in range(10):
            rows = self.rows(self.body('RankArena_GetOpponentInfo'), 1)
            self.assert_valid(rows, 3, 8)
            self.assertNotEqual(set(self.ids(rows)), set(self.ids(previous)))
            self.assertEqual(sum(row[8] < 4 for row in rows), 2)
            self.assertEqual(sum(row[8] > 4 for row in rows), 1)
            self.assertNotEqual({row[8] for row in rows}, {1, 2, 3})
            calls = draws.call_count
            info = self.ladder_info()
            self.assertEqual(self.rows(info, 10), rows)
            self.assertEqual(g.parse_varint_fields(info)[5], 4)
            self.assertEqual(draws.call_count, calls)
            previous = rows

    def test_ordinary_arena_sends_immediate_attempt_status_after_challenge(self):
        # All eight original ArenaGroupSetting groups allow ten daily fights.
        self.assertEqual(g.parse_varint_fields(self.body('Arena_GetMyInfo'))[5], 10)
        with patch.object(g, '_refresh_arena_npcs'), patch.object(
                g.bots_local, 'pick_opponent', return_value={'uin': self.roster[0]['uin'],
                                                            'score': 2000, 'power': 20}):
            for remaining in range(9, -1, -1):
                replies = self.route('Arena_Challenge')
                names = [name for name, _ in replies]
                info_name = 'PVZ.Cmd.Cmd_Arena_GetMyInfo_SC'
                challenge_name = 'PVZ.Cmd.Cmd_Arena_Challenge_SC'
                # Ordinary ArenaService adds the recorder on Challenge; the
                # following MyInfo replaces its list and writes leftFightNum.
                self.assertLess(names.index(challenge_name), names.index(info_name))
                self.assertEqual(g.parse_varint_fields(dict(replies)[info_name])[5], remaining)
                report_uid = self.rows(dict(replies)[challenge_name], 3)[0][1]
                report = self.body('Battle_QueryReport', g.pb_int64_field(1, report_uid))
                self.assertEqual(g.parse_repeated_submessages(report, 1), [b'arena-refresh-report'])
                self.assertEqual(g.parse_varint_fields(self.body('Arena_GetMyInfo'))[5], remaining)
            exhausted = self.route('Arena_Challenge')
            self.assertEqual([name for name, _ in exhausted], ['PVZ.Cmd.Cmd_LogicError_SC'])
            self.assertEqual(g.parse_varint_fields(exhausted[0][1])[1], 7705)
            self.assertEqual(g.parse_varint_fields(self.body('Arena_GetMyInfo'))[5], 0)

    def test_failed_ordinary_challenge_keeps_attempt_count_and_sends_no_fake_status(self):
        self.assertEqual(g.parse_varint_fields(self.body('Arena_GetMyInfo'))[5], 10)
        with patch.object(g.bots_local, 'pick_opponent', return_value=None):
            replies = self.route('Arena_Challenge')
        self.assertEqual([name for name, _ in replies], ['PVZ.Cmd.Cmd_LogicError_SC'])
        self.assertEqual(g.parse_varint_fields(replies[0][1])[1], 5006)
        self.assertEqual(g.parse_varint_fields(self.body('Arena_GetMyInfo'))[5], 10)

    def test_stale_ordinary_reader_cannot_restore_pre_battle_counter_score_or_record(self):
        self.body('Arena_GetMyInfo')
        stale = copy.deepcopy(self.player)
        with patch.object(g, '_refresh_arena_npcs'), patch.object(
                g.bots_local, 'pick_opponent', return_value={'uin': self.roster[0]['uin'],
                                                            'score': 2000, 'power': 20}):
            self.body('Arena_Challenge')
        fresh = store.get_or_create_player(self.player['uin'])[0]
        expected = g._load_json_state(fresh, 'arena_data', {})
        self.assertEqual(expected['challenges'], 1)
        for request in ('Arena_GetMyInfo', 'Arena_GetRank', 'Arena_GetTopPlayers',
                        'Arena_ShowDailyReward'):
            with self.subTest(request=request):
                reader = copy.deepcopy(stale)
                replies = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + request + '_CS'](b'', reader)
                self.assertTrue(replies)
                fresh = store.get_or_create_player(self.player['uin'])[0]
                actual = g._load_json_state(fresh, 'arena_data', {})
                for key in ('challenges', 'score', 'daily_score', 'wins', 'losses', 'records'):
                    self.assertEqual(actual[key], expected[key], (request, key))

    def test_stale_ordinary_challenge_consumes_one_more_attempt_instead_of_restarting_count(self):
        self.body('Arena_GetMyInfo')
        stale = copy.deepcopy(self.player)
        with patch.object(g, '_refresh_arena_npcs'), patch.object(
                g.bots_local, 'pick_opponent', return_value={'uin': self.roster[0]['uin'],
                                                            'score': 2000, 'power': 20}), \
                patch.object(g, 'run_battle', return_value=(True, 10, 20, [])) as fights:
            first = self.route('Arena_Challenge')
            self.assertEqual(g.parse_varint_fields(dict(first)[
                'PVZ.Cmd.Cmd_Arena_GetMyInfo_SC'])[5], 9)
            second = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Arena_Challenge_CS'](b'', stale)
            self.assertEqual(g.parse_varint_fields(dict(second)[
                'PVZ.Cmd.Cmd_Arena_GetMyInfo_SC'])[5], 8)
            self.assertEqual(fights.call_count, 2)
        fresh = store.get_or_create_player(self.player['uin'])[0]
        saved = g._load_json_state(fresh, 'arena_data', {})
        self.assertEqual((saved['challenges'], saved['wins'], len(saved['records'])), (2, 2, 2))
        self.assertEqual(len({record['report_uid'] for record in saved['records']}), 2)

    def real_reports(self):
        self.enterContext(patch.object(g, 'build_fight_report', self.real_report_builder))
        self.enterContext(patch.object(g, '_refresh_arena_npcs'))
        self.enterContext(patch.object(g.bots_local, 'pick_opponent', return_value={
            'uin': self.roster[0]['uin'], 'score': 2000, 'power': 20}))

    def report_rewards(self, report):
        side = g.parse_repeated_submessages(report, 2)[0]
        rewards = []
        for block in g.parse_repeated_submessages(side, 3):
            for item in self.rows(block, 4):
                rewards.append((item[2], item[3]))
        return rewards

    def ladder_reward_draws(self, expected):
        module = importlib.import_module('ladder_rules_repairs_20261011')
        choose = module.random.choice
        draws = []

        def choice(population):
            if tuple(population) == ((1, 10000), (2002, 1)):
                self.assertLess(len(draws), len(expected), 'unexpected victory reward draw')
                item = expected[len(draws)]
                draws.append(item)
                return item
            # Refresh can use choice to permute a batch. Keep those draws real.
            return choose(population)

        self.enterContext(patch.object(module.random, 'choice', side_effect=choice))
        return draws

    def test_ordinary_real_reports_and_inventory_agree_on_perfect_wins_and_loss(self):
        self.real_reports()
        initial = g.parse_varint_fields(self.body('Arena_GetMyInfo'))
        initial_score = initial[2]
        initial_hearts = int(self.player['friend_point'])
        expected_score = initial_score
        for used, (won, rating, base, streak, win_streak) in enumerate((
                (True, 3, 5, 0, 1), (True, 3, 5, 1, 2),
                (False, 0, 1, 0, 0), (True, 3, 5, 0, 1),
                (True, 3, 5, 1, 2)), 1):
            with self.subTest(won=won, used=used):
                def battle(left, right, **kwargs):
                    if not won:
                        for unit in left:
                            unit['final_hp'] = 0
                    # Wins intentionally omit final_hp: the hp fallback must
                    # still grade a surviving stub formation as perfect.
                    return won, 10, 20, []
                with patch.object(g, 'run_battle', side_effect=battle):
                    replies = dict(self.route('Arena_Challenge'))
                challenge = replies['PVZ.Cmd.Cmd_Arena_Challenge_SC']
                recorder = self.rows(challenge, 3)[0]
                g._BATTLE_REPORTS.clear()
                store.close_thread_db()
                self.player = store.get_or_create_player(self.player['uin'])[0]
                queried = self.body('Battle_QueryReport', g.pb_int64_field(1, recorder[1]))
                report = g.parse_repeated_submessages(queried, 1)[0]
                outcome = g.parse_varint_fields(report)
                side = g.parse_repeated_submessages(report, 2)[0]
                side_fields = g.parse_varint_fields(side)
                self.assertEqual((outcome[1], outcome[6], outcome[7]),
                                 (0 if won else 1, rating, 1 if won else 2))
                self.assertEqual((side_fields[11], side_fields[12], side_fields[14]),
                                 (base, streak, win_streak))
                self.assertEqual(self.report_rewards(report), [(6, 10)])
                self.assertEqual(recorder[6], base + streak)
                expected_score += base + streak
                self.assertEqual(g.parse_varint_fields(challenge)[2], expected_score)
                status = g.parse_varint_fields(replies['PVZ.Cmd.Cmd_Arena_GetMyInfo_SC'])
                self.assertEqual((status[2], status[5]), (expected_score, 10 - used))
                fresh = store.get_or_create_player(self.player['uin'])[0]
                self.assertEqual(fresh['friend_point'], initial_hearts + 10 * used)
                self.assertEqual(g.parse_varint_fields(replies[
                    'PVZ.Cmd.Cmd_SyncFriendPoint_SC'])[1], fresh['friend_point'])
                self.now += 31
        saved = g._load_json_state(fresh, 'arena_data', {})
        self.assertEqual((saved['wins'], saved['losses'], saved['daily_score']), (4, 1, 23))

    def test_ordinary_real_report_grades_use_final_survivors_and_original_score_table(self):
        self.real_reports()
        self.left = [dict(self.left[0], pos=i + 1, uid=10001 + i) for i in range(3)]
        expected_score = g.parse_varint_fields(self.body('Arena_GetMyInfo'))[2]
        for surviving, rating, base, streak in ((2, 2, 4, 0), (1, 1, 3, 1)):
            with self.subTest(surviving=surviving):
                def battle(left, right, **kwargs):
                    for index, unit in enumerate(left):
                        unit['final_hp'] = 1 if index < surviving else 0
                    return True, 10, 20, []
                with patch.object(g, 'run_battle', side_effect=battle):
                    challenge = self.body('Arena_Challenge')
                recorder = self.rows(challenge, 3)[0]
                queried = self.body('Battle_QueryReport', g.pb_int64_field(1, recorder[1]))
                report = g.parse_repeated_submessages(queried, 1)[0]
                side = g.parse_repeated_submessages(report, 2)[0]
                values = g.parse_varint_fields(side)
                self.assertEqual(g.parse_varint_fields(report)[6], rating)
                self.assertEqual((values[11], values[12], recorder[6]),
                                 (base, streak, base + streak))
                self.assertEqual(self.report_rewards(report), [(6, 10)])
                expected_score += base + streak
                self.assertEqual(g.parse_varint_fields(challenge)[2], expected_score)
                self.now += 31

    def test_ordinary_legacy_records_migrate_only_the_trailing_win_streak(self):
        self.real_reports()
        initial = copy.deepcopy(g._arena_state(self.player, 'arena_data'))
        for past_results, streak, bonus in (((False, True, True), 3, 1),
                                            ((True, True, False), 1, 0)):
            with self.subTest(past_results=past_results):
                legacy = copy.deepcopy(initial)
                legacy.pop('win_streak', None)
                legacy.update(wins=2, losses=1, records=[
                    {'win': won, 'name': 'HistoricalBot', 'opponent': self.roster[0]['uin'],
                     'change': 5 if won else 1, 'report_uid': index + 1}
                    for index, won in enumerate(past_results)])
                g._save_json_state(self.player, 'arena_data', legacy)
                challenge = self.body('Arena_Challenge')
                recorder = self.rows(challenge, 3)[0]
                queried = self.body('Battle_QueryReport', g.pb_int64_field(1, recorder[1]))
                report = g.parse_repeated_submessages(queried, 1)[0]
                side = g.parse_repeated_submessages(report, 2)[0]
                values = g.parse_varint_fields(side)
                self.assertEqual((values[14], values[12], recorder[6]), (streak, bonus, 5 + bonus))
                fresh = store.get_or_create_player(self.player['uin'])[0]
                saved = g._load_json_state(fresh, 'arena_data', {})
                self.assertEqual(saved['win_streak'], streak)
                self.now += 31

    def test_ladder_random_rewards_match_reports_inventory_and_reloaded_replays(self):
        self.real_reports()
        expected = [(1, 10000), (2002, 1), (1, 10000), (2002, 1)]
        draws = self.ladder_reward_draws(expected)
        coin = int(self.player['coin'])
        beans = store.get_item_count(self.player['uin'], 2002)
        hearts = int(self.player['friend_point'])
        medals = store.get_item_count(self.player['uin'], 2014)
        # Promote once, beat the displaced lower-ranked player, then do both
        # actions at rank one. Every victory is eligible for one random reward.
        targets = [self.roster[24]['uin']] * 2 + [self.roster[0]['uin']] * 2
        for used, (target, item, rank) in enumerate(zip(targets, expected, (25, 25, 1, 1)), 1):
            with self.subTest(used=used, item=item, rank=rank):
                self.now += 301
                replies = dict(self.route('RankArena_Challenge', g.pb_int64_field(1, target)))
                challenge = replies['PVZ.Cmd.Cmd_RankArena_Challenge_SC']
                self.assertEqual(g.parse_varint_fields(challenge)[1], rank)
                self.assertEqual(draws, expected[:used])
                recorder = self.rows(challenge, 3)[0]
                queried = self.body('Battle_QueryReport', g.pb_int64_field(1, recorder[1]))
                report = g.parse_repeated_submessages(queried, 1)[0]
                self.assertEqual(self.report_rewards(report), [item])
                coin += item[1] if item[0] == 1 else 0
                beans += item[1] if item[0] == 2002 else 0
                if item[0] == 1:
                    self.assertEqual(g.parse_varint_fields(replies['PVZ.Cmd.Cmd_SyncCoin_SC'])[1], coin)
                    self.assertNotIn('PVZ.Cmd.Cmd_Item_Update_SC', replies)
                else:
                    self.assertEqual(self.rows(replies['PVZ.Cmd.Cmd_Item_Update_SC'], 1),
                                     [{1: 2002, 2: 2002, 3: beans}])
                    self.assertNotIn('PVZ.Cmd.Cmd_SyncCoin_SC', replies)
                g._BATTLE_REPORTS.clear()
                store.close_thread_db()
                self.player = store.get_or_create_player(self.player['uin'])[0]
                for request in ('Battle_QueryReport', 'Battle_QueryRankReport'):
                    replay = self.body(request, g.pb_int64_field(1, recorder[1]))
                    self.assertEqual(g.parse_repeated_submessages(replay, 1), [report])
                self.assertEqual(draws, expected[:used])
                fresh = store.get_or_create_player(self.player['uin'])[0]
                self.assertEqual((fresh['coin'], store.get_item_count(fresh['uin'], 2002)),
                                 (coin, beans))
                self.assertEqual(fresh['friend_point'], hearts)
                self.assertEqual(store.get_item_count(fresh['uin'], 2014), medals)
                self.assertEqual(g._load_json_state(fresh, 'rank_arena_data', {})['challenges'], used)

    def test_ladder_loss_does_not_draw_or_grant_a_victory_reward(self):
        self.real_reports()
        draws = self.ladder_reward_draws([])
        before_info = self.ladder_info()
        target = self.rows(before_info, 10)[0]
        coin, beans = self.player['coin'], store.get_item_count(self.player['uin'], 2002)
        with patch.object(g, 'run_battle', return_value=(False, 10, 20, [])), \
                patch.object(g, 'grant_fuben_reward', wraps=g.grant_fuben_reward) as grant:
            replies = dict(self.route('RankArena_Challenge', g.pb_int64_field(1, target[1])))
        grant.assert_not_called()
        challenge = replies['PVZ.Cmd.Cmd_RankArena_Challenge_SC']
        self.assertEqual(g.parse_varint_fields(challenge)[2], 0)
        self.assertEqual(g.parse_varint_fields(challenge)[1], g.parse_varint_fields(before_info)[2])
        recorder = self.rows(challenge, 3)[0]
        report = g.parse_repeated_submessages(
            self.body('Battle_QueryReport', g.pb_int64_field(1, recorder[1])), 1)[0]
        self.assertEqual(self.report_rewards(report), [])
        self.assertEqual(draws, [])
        self.assertNotIn('PVZ.Cmd.Cmd_SyncCoin_SC', replies)
        self.assertNotIn('PVZ.Cmd.Cmd_Item_Update_SC', replies)
        fresh = store.get_or_create_player(self.player['uin'])[0]
        self.assertEqual((fresh['coin'], store.get_item_count(fresh['uin'], 2002)), (coin, beans))
        self.assertEqual(g._load_json_state(fresh, 'rank_arena_data', {})['challenges'], 1)

    def test_ladder_reward_failure_rolls_back_paid_cost_rank_and_both_reward_choices(self):
        self.real_reports()
        before_info = self.ladder_info()
        target = self.rows(before_info, 10)[0][1]
        state = g._load_json_state(self.player, 'rank_arena_data', {})
        state['challenges'] = 5
        g._save_json_state(self.player, 'rank_arena_data', state)
        store.save_player(self.player['uin'], present_ticket=30, ticket=40)
        self.player.update(present_ticket=30, ticket=40)
        ranks = {row[1]: row[8] for row in self.rows(self.body('RankArena_GetRank'), 1)}
        before = copy.deepcopy(self.player)
        enemy_before = store.get_or_create_player(target)[0]['rank_arena_data']
        beans = store.get_item_count(self.player['uin'], 2002)
        expected = [(1, 10000), (2002, 1)]
        draws = self.ladder_reward_draws(expected)
        grant = g.grant_fuben_reward

        def report_rows():
            with store._db() as con:
                exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='local_rank_arena_reports'").fetchone()
                return list(con.execute('SELECT * FROM local_rank_arena_reports').fetchall()) if exists else []

        reports_before = report_rows()
        for item in expected:
            with self.subTest(item=item):
                def fail_after_grant(player, items):
                    self.assertEqual(items, [item])
                    grant(player, items)
                    current = store.get_or_create_player(player['uin'])[0]
                    current_state = g._load_json_state(current, 'rank_arena_data', {})
                    self.assertEqual(current_state['challenges'], 6)
                    self.assertLess(current_state['last_rank'], ranks[player['uin']])
                    self.assertEqual((current['present_ticket'], current['ticket']), (0, 20))
                    self.assertEqual(current['coin'], before['coin'] + (10000 if item[0] == 1 else 0))
                    self.assertEqual(store.get_item_count(player['uin'], 2002), beans + (item[0] == 2002))
                    raise RuntimeError('QA failure after actual victory reward grant')

                with patch.dict(g.SERVER_CONFIG_OVERRIDES, {'sRankArenaPayChallenge': '50,50,50,50,50'}), \
                        patch.object(g, 'grant_fuben_reward', side_effect=fail_after_grant):
                    with self.assertRaisesRegex(RuntimeError, 'after actual victory reward grant'):
                        self.route('RankArena_Challenge', g.pb_int64_field(1, target))
                # A login read updates last_login. Inspect the durable row
                # without adding a write outside the transaction under test.
                with store._db() as con:
                    fresh = dict(con.execute('SELECT * FROM players WHERE uin=?',
                                             (self.player['uin'],)).fetchone())
                self.assertEqual(fresh, before)
                self.assertEqual(self.player, before)
                self.assertEqual(store.get_item_count(fresh['uin'], 2002), beans)
                self.assertEqual(store.get_or_create_player(target)[0]['rank_arena_data'], enemy_before)
                self.assertEqual(report_rows(), reports_before)
                self.assertEqual({row[1]: row[8] for row in self.rows(self.body('RankArena_GetRank'), 1)}, ranks)
        self.assertEqual(draws, expected)

    def test_cross_refresh_samples_four_other_players_for_the_five_card_client(self):
        draws = self.seeded_sample('cross_arena_refresh_repairs_20261011')
        groups = []
        for _ in range(10):
            calls = draws.call_count
            response = self.body('AllServerRankArena_GetOpponent')
            self.assertGreater(draws.call_count, calls)
            self_row = self.rows(response, 1)
            self.assertEqual(self.ids(self_row), (self.player['uin'],))
            rows = self.rows(response, 2)
            self.assert_valid(rows, 4, 9)
            groups.append(self.ids(rows))
            calls = draws.call_count
            info = self.cross_info()
            self.assertEqual(self.rows(info, 9), rows)
            podium = self.rows(info, 8)
            self.assert_valid(podium, 5, 9)
            self.assertEqual([row[9] for row in podium], [1, 2, 3, 4, 5])
            self.player = store.get_or_create_player(self.player['uin'])[0]
            self.assertEqual(self.rows(self.cross_info(), 9), rows)
            self.assertEqual(draws.call_count, calls)
        self.assertTrue(all(a != b for a, b in zip(groups, groups[1:])), groups)
        self.assertGreater(len(set(groups)), 1)
        # Detect the old invariant top-five response independently of order.
        self.assertTrue(any(role not in {b['uin'] for b in self.roster[:5]}
                            for group in groups for role in group), groups)

    def test_refreshing_one_arena_keeps_the_other_arena_batch(self):
        self.seeded_sample('ladder_rules_repairs_20261011', seed=812)
        ladder_before = self.rows(self.ladder_info(), 10)
        cross_before = self.rows(self.cross_info(), 9)
        ladder_next = self.rows(self.body('RankArena_GetOpponentInfo'), 1)
        self.assertNotEqual(self.ids(ladder_next), self.ids(ladder_before))
        self.assertEqual(self.rows(self.cross_info(), 9), cross_before)
        cross_next = self.rows(self.body('AllServerRankArena_GetOpponent'), 2)
        self.assertNotEqual(self.ids(cross_next), self.ids(cross_before))
        self.assertEqual(self.rows(self.ladder_info(), 10), ladder_next)

    def test_cross_challenge_sends_complete_batch_and_new_attempt_count_before_report(self):
        draws = self.seeded_sample('cross_arena_refresh_repairs_20261011')
        before = self.rows(self.cross_info(), 9)
        self.assert_valid(before, 4, 9)
        for used in (1, 2):
            target = before[0][1]
            replies = self.route('AllServerRankArena_Challenge', g.pb_int64_field(1, target)
                                 + g.pb_varint_field(3, used))
            names = [name for name, _ in replies]
            info_name = 'PVZ.Cmd.Cmd_AllServerRankArena_Info_SC'
            challenge_name = 'PVZ.Cmd.Cmd_AllServerRankArena_Challenge_SC'
            self.assertLess(names.index(info_name), names.index(challenge_name))
            info, challenge = dict(replies)[info_name], dict(replies)[challenge_name]
            self.assertEqual(g.parse_varint_fields(info)[2], used)
            self.assertEqual(g.parse_varint_fields(challenge)[4], used)
            info_rows, result_rows = self.rows(info, 9), self.rows(challenge, 6)
            self.assert_valid(info_rows, 4, 9)
            self.assert_valid(result_rows, 4, 9)
            self.assertEqual(result_rows, info_rows)
            self.assertEqual(g.parse_repeated_submessages(challenge, 7), [b'arena-refresh-report'])
            calls = draws.call_count
            reread = self.cross_info()
            self.assertEqual(self.rows(reread, 9), info_rows)
            self.assertEqual(g.parse_varint_fields(reread)[2], used)
            self.assertEqual(draws.call_count, calls)
            before = info_rows

    def test_cross_win_swaps_higher_rank_and_every_route_keeps_it_after_reload(self):
        self.seeded_sample('cross_arena_refresh_repairs_20261011')
        before_info = self.cross_info()
        expected = self.cross_board()
        old_rank = expected[self.player['uin']]
        target = self.rows(before_info, 9)[-1]
        target_rank = expected[target[1]]
        self.assertLess(target_rank, old_rank)
        result = g.parse_varint_fields(self.body('AllServerRankArena_Challenge',
            g.pb_int64_field(1, target[1]) + g.pb_varint_field(3, 1)))
        expected[self.player['uin']], expected[target[1]] = target_rank, old_rank
        self.assertEqual((result[1], result[2], result[4], result[5]), (1, target_rank, 1, 2))
        info = self.assert_cross_ranks(expected)
        preview = g.parse_varint_fields(self.body('AllServerRankArena_GetOpponentFormation',
                                                 g.pb_int64_field(1, target[1])))
        self.assertEqual((preview[1], preview[2]), (target[1], old_rank))
        saved_batch = self.rows(info, 9)
        store.close_thread_db()
        g.bots_local.initialize(db_path=store.DB_PATH)
        g._RANK_BOT_CACHE = (0, [])
        g._NPC_ENCOUNTERS.clear()
        self.player = store.get_or_create_player(self.player['uin'])[0]
        reread = self.assert_cross_ranks(expected)
        self.assertEqual(self.rows(reread, 9), saved_batch)
        self.assertEqual(g.parse_varint_fields(reread)[5], 2)

    def test_cross_loss_and_lower_rank_win_change_daily_points_without_moving_rank(self):
        self.real_reports()
        self.seeded_sample('cross_arena_refresh_repairs_20261011')
        self.cross_info()
        expected = self.cross_board()
        target = self.roster[24]['uin']
        old_rank, target_rank = expected[self.player['uin']], expected[target]
        first = self.body('AllServerRankArena_Challenge', g.pb_int64_field(1, target)
                          + g.pb_varint_field(3, 1))
        first_report = g.parse_repeated_submessages(first, 7)[0]
        self.assertEqual(self.rows(first_report, 2)[0][11], 2)
        expected[self.player['uin']], expected[target] = target_rank, old_rank
        self.assert_cross_ranks(expected)
        higher = self.roster[9]['uin']
        self.assertLess(expected[higher], expected[self.player['uin']])
        with patch.object(g, 'run_battle', return_value=(False, 10, 20, [])):
            lost_body = self.body('AllServerRankArena_Challenge',
                g.pb_int64_field(1, higher) + g.pb_varint_field(3, 2))
        lost = g.parse_varint_fields(lost_body)
        lost_report = g.parse_repeated_submessages(lost_body, 7)[0]
        self.assertEqual(self.rows(lost_report, 2)[0][11], 1)
        self.assertEqual((lost[1], lost[2], lost[5]), (0, target_rank, 3))
        self.assert_cross_ranks(expected)
        self.assertGreater(expected[target], expected[self.player['uin']])
        lower_body = self.body('AllServerRankArena_Challenge',
            g.pb_int64_field(1, target) + g.pb_varint_field(3, 3))
        lower_win = g.parse_varint_fields(lower_body)
        lower_report = g.parse_repeated_submessages(lower_body, 7)[0]
        self.assertEqual(self.rows(lower_report, 2)[0][11], 2)
        self.assertEqual((lower_win[1], lower_win[2], lower_win[4], lower_win[5]),
                         (1, target_rank, 3, 5))
        self.assert_cross_ranks(expected)
        fresh = store.get_or_create_player(self.player['uin'])[0]
        saved = g._load_json_state(fresh, 'allserver_rank_arena_data', {})
        self.assertEqual((saved['wins'], saved['losses'], saved['daily_score']), (2, 1, 5))

    def test_cross_human_accounts_share_unique_persistent_rank_slots(self):
        self.seeded_sample('cross_arena_refresh_repairs_20261011')
        self.cross_info()
        original = self.player
        other, _ = store.get_or_create_player(original['uin'] + 1)
        store.save_player(other['uin'], name='CrossPeerQA', role_created=1, level=100)
        self.player = store.get_or_create_player(other['uin'])[0]
        self.cross_info()
        expected = self.cross_board()
        self.assertIn(original['uin'], expected)
        self.assertNotEqual(expected[original['uin']], expected[other['uin']])
        target = self.roster[9]['uin']
        previous_rank = expected[other['uin']]
        self.body('AllServerRankArena_Challenge', g.pb_int64_field(1, target)
                  + g.pb_varint_field(3, 1))
        expected[other['uin']], expected[target] = expected[target], previous_rank
        self.assert_cross_ranks(expected)
        self.player = original
        self.assert_cross_ranks(expected)
        target = self.roster[10]['uin']
        previous_rank = expected[original['uin']]
        self.body('AllServerRankArena_Challenge', g.pb_int64_field(1, target)
                  + g.pb_varint_field(3, 1))
        expected[original['uin']], expected[target] = expected[target], previous_rank
        self.assert_cross_ranks(expected)
        self.player = store.get_or_create_player(other['uin'])[0]
        self.assert_cross_ranks(expected)

    def test_cross_earned_rank_survives_unrelated_bot_and_player_score_changes(self):
        self.seeded_sample('cross_arena_refresh_repairs_20261011')
        self.cross_info()
        expected = self.cross_board()
        target = self.roster[24]['uin']
        previous_rank = expected[self.player['uin']]
        self.body('AllServerRankArena_Challenge', g.pb_int64_field(1, target)
                  + g.pb_varint_field(3, 1))
        expected[self.player['uin']], expected[target] = expected[target], previous_rank
        self.assert_cross_ranks(expected)
        with store._db() as con:
            con.execute('UPDATE bots_state SET score=-score')
        state = g._asra_state(self.player)
        state['score'] = 1000000
        g._save_json_state(self.player, 'allserver_rank_arena_data', state)
        g._RANK_BOT_CACHE = (0, [])
        ordinary = g.parse_varint_fields(self.body('Arena_GetMyInfo'))
        self.assertEqual(ordinary[1], 1)
        self.assert_cross_ranks(expected)

    def test_cross_gap_compacts_earned_order_and_preserves_yesterday_reward_rank(self):
        self.seeded_sample('cross_arena_refresh_repairs_20261011')
        self.cross_info()
        expected = self.cross_board()
        target = self.roster[24]['uin']
        old_rank, earned_rank = expected[self.player['uin']], expected[target]
        self.body('AllServerRankArena_Challenge', g.pb_int64_field(1, target)
                  + g.pb_varint_field(3, 1))
        expected[self.player['uin']], expected[target] = earned_rank, old_rank
        self.assert_cross_ranks(expected)
        removed = self.roster[4]['uin']
        removed_rank = expected.pop(removed)
        self.roster[:] = [bot for bot in self.roster if bot['uin'] != removed]
        expected = {role: rank - (rank > removed_rank) for role, rank in expected.items()}
        with patch.object(g.time, 'strftime', return_value='20261012'):
            info = self.assert_cross_ranks(expected)
            fields = g.parse_varint_fields(info)
            self.assertEqual((fields[2], fields[5], fields[7]), (0, 0, earned_rank))
            self.player = store.get_or_create_player(self.player['uin'])[0]
            info = self.assert_cross_ranks(expected)
            self.assertEqual(g.parse_varint_fields(info)[7], earned_rank)
            saved = g._load_json_state(self.player, 'allserver_rank_arena_data', {})
            self.assertEqual((saved['last_rank'], saved['daily_reward_rank']),
                             (earned_rank - 1, earned_rank))

    def test_cross_rank_one_still_refreshes_random_lower_opponents_and_keeps_podium_stable(self):
        draws = self.seeded_sample('cross_arena_refresh_repairs_20261011')
        self.cross_info()
        leader = self.roster[0]['uin']
        challenge = g.parse_varint_fields(self.body('AllServerRankArena_Challenge',
            g.pb_int64_field(1, leader) + g.pb_varint_field(3, 1)))
        self.assertEqual(challenge[2], 1)
        info = self.cross_info()
        podium = self.rows(info, 8)
        self.assertEqual([row[9] for row in podium], [1, 2, 3, 4, 5])
        self.assertEqual(podium[0][1], self.player['uin'])
        previous = self.ids(self.rows(info, 9))
        ranks_seen = []
        for _ in range(10):
            calls = draws.call_count
            refreshed = self.body('AllServerRankArena_GetOpponent')
            self.assertGreater(draws.call_count, calls)
            rows = self.rows(refreshed, 2)
            self.assert_valid(rows, 4, 9)
            self.assertNotEqual(set(self.ids(rows)), set(previous))
            self.assertTrue(all(row[9] > 1 for row in rows))
            ranks_seen.append({row[9] for row in rows})
            calls = draws.call_count
            stable = self.cross_info()
            self.assertEqual(self.rows(stable, 9), rows)
            self.assertEqual(self.rows(stable, 8), podium)
            self.assertEqual(g.parse_varint_fields(stable)[2], 1)
            self.player = store.get_or_create_player(self.player['uin'])[0]
            self.assertEqual(self.rows(self.cross_info(), 9), rows)
            self.assertEqual(draws.call_count, calls)
            previous = self.ids(rows)
        self.assertTrue(any(ranks != {2, 3, 4, 5} for ranks in ranks_seen), ranks_seen)

    def test_cross_rank_five_refresh_mixes_three_higher_and_one_random_lower_rank(self):
        draws = self.seeded_sample('cross_arena_refresh_repairs_20261011')
        self.cross_info()
        self.body('AllServerRankArena_Challenge', g.pb_int64_field(1, self.roster[4]['uin'])
                  + g.pb_varint_field(3, 1))
        info = self.cross_info()
        self.assertEqual(self.rows(info, 1)[0][9], 5)
        podium = self.rows(info, 8)
        self.assertEqual([row[9] for row in podium], [1, 2, 3, 4, 5])
        self.assertEqual(podium[-1][1], self.player['uin'])
        previous = self.rows(info, 9)
        for _ in range(10):
            rows = self.rows(self.body('AllServerRankArena_GetOpponent'), 2)
            self.assert_valid(rows, 4, 9)
            self.assertNotEqual(set(self.ids(rows)), set(self.ids(previous)))
            self.assertEqual(sum(row[9] < 5 for row in rows), 3)
            self.assertEqual(sum(row[9] > 5 for row in rows), 1)
            calls = draws.call_count
            stable = self.cross_info()
            self.assertEqual(self.rows(stable, 9), rows)
            self.assertEqual(self.rows(stable, 8), podium)
            self.assertEqual(g.parse_varint_fields(stable)[2], 1)
            self.assertEqual(draws.call_count, calls)
            previous = rows

    def test_cross_stale_opponent_rank_rejects_before_paid_attempt_or_battle(self):
        draws = self.seeded_sample('cross_arena_refresh_repairs_20261011')
        self.cross_info()
        state = g._asra_state(self.player)
        state['challenges'] = 5
        g._save_json_state(self.player, 'allserver_rank_arena_data', state)
        store.save_player(self.player['uin'], present_ticket=30, ticket=40)
        self.player.update(present_ticket=30, ticket=40)
        before_info = self.cross_info()
        target = self.rows(before_info, 9)[0]
        expected_ranks = self.cross_board()
        fresh = store.get_or_create_player(self.player['uin'])[0]
        expected_state = g._load_json_state(fresh, 'allserver_rank_arena_data', {})
        expected_bot = g.bots_local.bot_state(target[1])
        calls = draws.call_count
        with patch.object(g, 'run_battle', side_effect=AssertionError('stale rank started combat')) as fight:
            result = self.route('AllServerRankArena_Challenge', g.pb_int64_field(1, target[1])
                                + g.pb_varint_field(2, target[9] + 1) + g.pb_varint_field(3, 6))
        self.assertEqual([name for name, _ in result], ['PVZ.Cmd.Cmd_LogicError_SC'])
        self.assertEqual(g.parse_varint_fields(result[0][1])[1], 5006)
        fight.assert_not_called()
        self.assertEqual(draws.call_count, calls)
        self.assertEqual(g.bots_local.bot_state(target[1]), expected_bot)
        fresh = store.get_or_create_player(self.player['uin'])[0]
        self.assertEqual((fresh['present_ticket'], fresh['ticket']), (30, 40))
        self.assertEqual(g._load_json_state(fresh, 'allserver_rank_arena_data', {}), expected_state)
        self.assert_cross_ranks(expected_ranks)
        self.assertEqual(self.rows(self.cross_info(), 9), self.rows(before_info, 9))

    def test_cross_report_reloads_for_attacker_and_defender_after_caches_are_cleared(self):
        self.real_reports()
        self.seeded_sample('cross_arena_refresh_repairs_20261011')
        target = self.rows(self.cross_info(), 9)[0]
        challenge = self.body('AllServerRankArena_Challenge', g.pb_int64_field(1, target[1])
                              + g.pb_varint_field(2, target[9]) + g.pb_varint_field(3, 1))
        report = g.parse_repeated_submessages(challenge, 7)[0]
        self.assertEqual(self.rows(report, 2)[0][11], 2)
        report_uid = self.rows(self.body('AllServerRankArena_GetRecorder'), 1)[0][1]
        self.assertGreater(len(report), 100)
        for owner in (self.player['uin'], target[1]):
            with self.subTest(owner=owner):
                g._BATTLE_REPORTS.clear()
                store.close_thread_db()
                self.player = store.get_or_create_player(owner)[0]
                record = self.rows(self.body('AllServerRankArena_GetRecorder'), 1)[0]
                self.assertEqual(record[1], report_uid)
                queried = self.body('Battle_QueryRankReport', g.pb_int64_field(1, report_uid))
                self.assertEqual(g.parse_repeated_submessages(queried, 1), [report])

    def test_failed_cross_challenge_keeps_batch_and_does_not_consume_or_send_fake_success(self):
        draws = self.seeded_sample('cross_arena_refresh_repairs_20261011')
        before = self.cross_info()
        calls = draws.call_count
        replies = self.route('AllServerRankArena_Challenge', g.pb_int64_field(1, 900999)
                             + g.pb_varint_field(3, 1))
        self.assertEqual([name for name, _ in replies], ['PVZ.Cmd.Cmd_LogicError_SC'])
        self.assertEqual(g.parse_varint_fields(replies[0][1])[1], 5006)
        after = self.cross_info()
        self.assertEqual(self.rows(after, 9), self.rows(before, 9))
        self.assertEqual(g.parse_varint_fields(after)[2], 0)
        self.assertEqual(draws.call_count, calls)

    def test_post_battle_batch_failure_rolls_back_paid_settlement_and_previous_batch(self):
        self.seeded_sample('cross_arena_refresh_repairs_20261011')
        self.cross_info()
        state = g._asra_state(self.player)
        state['challenges'] = 5
        g._save_json_state(self.player, 'allserver_rank_arena_data', state)
        store.save_player(self.player['uin'], present_ticket=30, ticket=40)
        self.player.update(present_ticket=30, ticket=40)
        before = self.cross_info()
        rank_before = self.cross_board()
        target = self.rows(before, 9)[0][1]
        bot_before = g.bots_local.bot_state(target)
        module = importlib.import_module('cross_arena_refresh_repairs_20261011')
        with patch.object(module.random, 'sample', side_effect=RuntimeError('QA batch failure')):
            with self.assertRaises(RuntimeError):
                self.route('AllServerRankArena_Challenge', g.pb_int64_field(1, target)
                           + g.pb_varint_field(3, 6))
        self.assertEqual((self.player['present_ticket'], self.player['ticket']), (30, 40))
        self.assertEqual(g.bots_local.bot_state(target), bot_before)
        fresh = store.get_or_create_player(self.player['uin'])[0]
        saved = g._load_json_state(fresh, 'allserver_rank_arena_data', {})
        self.assertEqual((saved['challenges'], saved['records']), (5, []))
        self.assertEqual(self.rows(self.cross_info(), 9), self.rows(before, 9))
        self.assert_cross_ranks(rank_before)

    def test_cross_opponent_refresh_preserves_reward_claim_cursors(self):
        self.seeded_sample('cross_arena_refresh_repairs_20261011')
        state = g._asra_state(self.player)
        state.update(claimed_max_rank=10, claimed_score=25, daily_reward_rank=9,
                     max_rank=20, taken_daily=False)
        g._save_json_state(self.player, 'allserver_rank_arena_data', state)
        for _ in range(3):
            self.body('AllServerRankArena_GetOpponent')
            fields = g.parse_varint_fields(self.cross_info())
            self.assertEqual((fields[3], fields[4], fields[6], fields[7]), (20, 10, 25, 9))

    def test_stale_cross_reader_cannot_restore_pre_battle_counter_score_or_batch(self):
        self.seeded_sample('cross_arena_refresh_repairs_20261011')
        before = self.cross_info()
        stale = copy.deepcopy(self.player)
        target = self.rows(before, 9)[0][1]
        self.body('AllServerRankArena_Challenge', g.pb_int64_field(1, target)
                  + g.pb_varint_field(3, 1))
        fresh = store.get_or_create_player(self.player['uin'])[0]
        expected = g._load_json_state(fresh, 'allserver_rank_arena_data', {})
        for request, body in (
                ('AllServerRankArena_GetRecorder', b''),
                ('AllServerRankArena_GetOpponentFormation', g.pb_int64_field(1, target)),
                ('GetRankList', g.pb_varint_field(1, 105) + g.pb_varint_field(2, 1))):
            with self.subTest(request=request):
                reader = copy.deepcopy(stale)
                replies = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + request + '_CS'](body, reader)
                self.assertTrue(replies)
                fresh = store.get_or_create_player(self.player['uin'])[0]
                actual = g._load_json_state(fresh, 'allserver_rank_arena_data', {})
                for key in ('challenges', 'score', 'daily_score', 'opponent_batch', 'records'):
                    self.assertEqual(actual[key], expected[key], (request, key))


if __name__ == '__main__':
    unittest.main()
