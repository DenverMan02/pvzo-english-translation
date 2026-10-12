"""Actual TWeb/XOR7 login, three-cell refresh, combat and report query."""
import unittest
import test_worldmap_tcp as tcp
from worldmap_test_support import many, one


class LadderTCPTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = tcp.WorldMapTCPTests.asyncSetUp
    asyncTearDown = tcp.WorldMapTCPTests.asyncTearDown
    connect = tcp.WorldMapTCPTests.connect
    send = tcp.WorldMapTCPTests.send
    until = tcp.WorldMapTCPTests.until
    login = tcp.WorldMapTCPTests.login

    async def test_real_client_sequence_refresh_fight_and_report(self):
        player = self.qa.player(99101150, role_created=1, level=200, ticket=1000)
        self.g._seed_bots()
        self.g.ensure_player_cards(player)
        self.qa.train_owned_cards(player, level=200)
        conn = await self.login(player['uin'])
        await self.send(conn, 'RankArena_GetMyInfo')
        first = await self.until(conn, 'RankArena_GetMyInfo')
        before = one(first, 2)
        previous = [one(row, 1) for row in many(first, 10)]
        self.assertEqual(len(previous), 3)
        await self.send(conn, 'RankArena_GetOpponentInfo')
        refreshed = await self.until(conn, 'RankArena_GetOpponentInfo')
        fresh = many(refreshed, 1)
        offered = [one(row, 1) for row in fresh]
        self.assertTrue(all(a != b for a, b in zip(previous, offered)))
        # ModuleRankArena immediately issues this after the refresh response.
        await self.send(conn, 'RankArena_GetMyInfo')
        info = await self.until(conn, 'RankArena_GetMyInfo')
        self.assertEqual(offered, [one(row, 1) for row in many(info, 10)])
        await self.send(conn, 'RankArena_Challenge', self.g.pb_int64_field(1, offered[0]) + self.g.pb_varint_field(2, 0))
        changed = await self.until(conn, 'RankArena_GetMyInfo')
        result = await self.until(conn, 'RankArena_Challenge')
        rank, won = one(result, 1), bool(one(result, 2))
        self.assertEqual(one(changed, 2), rank)
        self.assertEqual(rank, min(before, one(fresh[0], 8)) if won else before)
        self.assertEqual(one(changed, 5), 4)
        self.assertEqual(one(changed, 7), 300)
        rec = one(result, 3)
        report_id = one(rec, 1)
        self.g._BATTLE_REPORTS.clear()
        await self.send(conn, 'Battle_QueryReport', self.g.pb_int64_field(1, report_id))
        report = one(await self.until(conn, 'Battle_QueryReport'), 1)
        self.assertIsInstance(report, bytes)
        self.assertGreater(len(report), 100)
        self.assertFalse(any('crashed' in row or 'sending generic' in row for row in self.records))


if __name__ == '__main__':
    unittest.main()
