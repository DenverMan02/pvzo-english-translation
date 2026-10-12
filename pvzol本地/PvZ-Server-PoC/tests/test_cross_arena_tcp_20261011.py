"""Real TWeb/XOR7 cross-arena refresh, battle/status and subsequent refresh."""
import importlib
import random
import unittest
from unittest.mock import patch
import test_worldmap_tcp as tcp
from worldmap_test_support import many, one


class CrossArenaTCPTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = tcp.WorldMapTCPTests.asyncSetUp
    asyncTearDown = tcp.WorldMapTCPTests.asyncTearDown
    connect = tcp.WorldMapTCPTests.connect
    send = tcp.WorldMapTCPTests.send
    until = tcp.WorldMapTCPTests.until
    login = tcp.WorldMapTCPTests.login

    def opponent_ids(self, body, field):
        return tuple(one(row, 1) for row in many(body, field))

    def check_opponents(self, body, field, player):
        rows = many(body, field)
        roles = self.opponent_ids(body, field)
        # The original model adds the self tile; these four wire opponents
        # therefore form a complete five-card client panel.
        self.assertEqual(len(roles), 4)
        self.assertEqual(len(set(roles)), 4)
        self.assertNotIn(player['uin'], roles)
        self.assertTrue(all(self.g.bots_local.is_bot(role) for role in roles))
        self.assertTrue(all(one(row, 9) > 0 and one(row, 10) > 0 for row in rows))
        self.assertTrue(all(one(row, 5) == self.bot_levels[one(row, 1)] for row in rows))
        return roles

    async def test_real_client_refresh_battle_count_and_refresh_again(self):
        player = self.qa.player(99101151, role_created=1, level=200, ticket=1000)
        self.g._seed_bots()
        self.bot_levels = {bot['uin']: bot['level'] for bot in self.g.bots_local.bots()}
        self.g.ensure_player_cards(player)
        self.qa.train_owned_cards(player, level=200)
        conn = await self.login(player['uin'])
        module = importlib.import_module('cross_arena_refresh_repairs_20261011')
        rng = random.Random(20261011)
        with patch.object(module.random, 'sample', side_effect=rng.sample) as draw:
            await self.send(conn, 'AllServerRankArena_Info')
            first = await self.until(conn, 'AllServerRankArena_Info')
            self.assertEqual(one(first, 2), 0)
            self.assertEqual([one(row, 9) for row in many(first, 8)], [1, 2, 3, 4, 5])
            previous = self.check_opponents(first, 9, player)

            for _ in range(3):
                await self.send(conn, 'AllServerRankArena_GetOpponent')
                refreshed = await self.until(conn, 'AllServerRankArena_GetOpponent')
                offered = self.check_opponents(refreshed, 2, player)
                if len(self.bot_levels) > 4:
                    self.assertNotEqual(set(offered), set(previous))
                self.assertEqual(one(one(refreshed, 1), 1), player['uin'])
                calls = draw.call_count
                await self.send(conn, 'AllServerRankArena_Info')
                info = await self.until(conn, 'AllServerRankArena_Info')
                self.assertEqual(many(info, 9), many(refreshed, 2))
                self.assertEqual(draw.call_count, calls)
                previous = offered

            # Retain an independent pre-battle session, reproducing auxiliary
            # read handlers which once wrote stale JSON back into SQLite.
            peer = await self.login(player['uin'])

            expected_daily = 0
            for used in (1, 2):
                candidate = next(row for row in many(info, 9) if one(row, 1) == previous[0])
                own_before = one(one(info, 1), 9)
                enemy_before = one(candidate, 9)
                await self.send(conn, 'AllServerRankArena_GetOpponentFormation',
                                self.g.pb_int64_field(1, previous[0]))
                preview = await self.until(conn, 'AllServerRankArena_GetOpponentFormation')
                self.assertEqual((one(preview, 1), one(preview, 2)),
                                 (previous[0], one(candidate, 9)))
                self.assertEqual(one(preview, 4), one(candidate, 10))
                self.assertTrue(many(preview, 3))
                self.assertTrue(all(one(card, 2) > 0 for card in many(preview, 3)))
                await self.send(conn, 'AllServerRankArena_Challenge',
                    self.g.pb_int64_field(1, previous[0]) + self.g.pb_varint_field(3, used))
                # Status must arrive before the result opens its actual replay.
                status = await self.until(conn, 'AllServerRankArena_Info')
                result = await self.until(conn, 'AllServerRankArena_Challenge')
                self.assertEqual((one(status, 2), one(result, 4)), (used, used))
                after = self.check_opponents(status, 9, player)
                self.check_opponents(result, 6, player)
                self.assertEqual(many(status, 9), many(result, 6))
                self.assertEqual(one(one(status, 1), 9), one(result, 2))
                won = bool(one(result, 1))
                expected_rank = min(own_before, enemy_before) if won else own_before
                self.assertEqual(one(result, 2), expected_rank)
                expected_daily += 2 if won else 1
                self.assertEqual((one(status, 5), one(result, 5)),
                                 (expected_daily, expected_daily))
                self.assertGreater(len(one(result, 7)), 100)
                self.assertEqual(one(one(result, 7), 1), 0 if won else 1)
                self.assertEqual(one(one(one(result, 7), 2), 11), 2 if won else 1)
                await self.send(conn, 'AllServerRankArena_GetRecorder')
                recorder = await self.until(conn, 'AllServerRankArena_GetRecorder')
                report_id = one(many(recorder, 1)[0], 1)
                self.g._BATTLE_REPORTS.clear()
                self.qa.store.close_thread_db()
                await self.send(conn, 'Battle_QueryRankReport', self.g.pb_int64_field(1, report_id))
                replay = await self.until(conn, 'Battle_QueryRankReport')
                self.assertEqual(one(replay, 1), one(result, 7))
                calls = draw.call_count
                await self.send(conn, 'AllServerRankArena_Info')
                reread = await self.until(conn, 'AllServerRankArena_Info')
                self.assertEqual(one(reread, 2), used)
                self.assertEqual(many(reread, 9), many(status, 9))
                self.assertEqual(draw.call_count, calls)

                await self.send(peer, 'AllServerRankArena_GetRecorder')
                await self.until(peer, 'AllServerRankArena_GetRecorder')
                await self.send(peer, 'AllServerRankArena_GetOpponentFormation',
                                self.g.pb_int64_field(1, after[0]))
                await self.until(peer, 'AllServerRankArena_GetOpponentFormation')
                await self.send(peer, 'GetRankList', self.g.pb_varint_field(1, 105)
                                + self.g.pb_varint_field(2, 1))
                self.assertEqual(one(await self.until(peer, 'GetRankList'), 1), 105)
                await self.send(peer, 'AllServerRankArena_Info')
                peer_info = await self.until(peer, 'AllServerRankArena_Info')
                self.assertEqual(one(peer_info, 2), used)
                self.assertEqual(one(one(peer_info, 1), 9), expected_rank)
                self.assertEqual(many(peer_info, 9), many(status, 9))

                await self.send(conn, 'AllServerRankArena_GetOpponent')
                refreshed = await self.until(conn, 'AllServerRankArena_GetOpponent')
                previous = self.check_opponents(refreshed, 2, player)
                if len(self.bot_levels) > 4:
                    self.assertNotEqual(set(previous), set(after))
                await self.send(conn, 'AllServerRankArena_Info')
                reread = await self.until(conn, 'AllServerRankArena_Info')
                self.assertEqual(one(reread, 2), used)
                self.assertEqual(many(reread, 9), many(refreshed, 2))
                info = reread

            calls = draw.call_count
            again = await self.login(player['uin'])
            await self.send(again, 'AllServerRankArena_Info')
            reconnected = await self.until(again, 'AllServerRankArena_Info')
            self.assertEqual(one(reconnected, 2), 2)
            self.assertEqual(many(reconnected, 9), many(info, 9))
            self.assertEqual(draw.call_count, calls)

        fresh = self.qa.store.get_or_create_player(player['uin'])[0]
        state = self.g._load_json_state(fresh, 'allserver_rank_arena_data', {})
        self.assertEqual(state['challenges'], 2)
        self.assertFalse(any('crashed' in row or 'sending generic' in row
                             for row in self.records), self.records)


if __name__ == '__main__':
    unittest.main()
