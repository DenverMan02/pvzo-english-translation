"""Ordinary arena real TWeb battles with immediate counts and a stale peer."""
import unittest
import test_worldmap_tcp as tcp
from worldmap_test_support import many, one


class OrdinaryArenaTCPTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = tcp.WorldMapTCPTests.asyncSetUp
    asyncTearDown = tcp.WorldMapTCPTests.asyncTearDown
    connect = tcp.WorldMapTCPTests.connect
    send = tcp.WorldMapTCPTests.send
    until = tcp.WorldMapTCPTests.until
    login = tcp.WorldMapTCPTests.login

    async def test_real_battles_sync_count_without_waiting_for_the_client_timer(self):
        player = self.qa.player(99101152, role_created=1, level=200, ticket=1000)
        self.g._seed_bots()
        self.g.ensure_player_cards(player)
        self.qa.train_owned_cards(player, level=200)
        first = await self.login(player['uin'])
        await self.send(first, 'Arena_GetMyInfo')
        initial = await self.until(first, 'Arena_GetMyInfo')
        self.assertEqual(one(initial, 5), 10)
        expected_score = one(initial, 2)
        initial_hearts = int(self.qa.store.get_or_create_player(player['uin'])[0]['friend_point'])
        peer = await self.login(player['uin'])
        await self.send(peer, 'Arena_GetMyInfo')
        self.assertEqual(one(await self.until(peer, 'Arena_GetMyInfo'), 5), 10)

        reports = []
        expected_streak = 0
        for conn, remaining in ((first, 9), (peer, 8)):
            await self.send(conn, 'Arena_Challenge')
            # ArenaService adds the result recorder first. The pushed MyInfo
            # immediately replaces its list and updates the remaining count.
            result = await self.until(conn, 'Arena_Challenge')
            status = await self.until(conn, 'Arena_GetMyInfo')
            self.assertEqual(one(status, 5), remaining)
            self.assertEqual((one(status, 1), one(status, 2)),
                             (one(result, 1), one(result, 2)))
            recorder = one(result, 3)
            self.assertIsInstance(recorder, bytes)
            report_id = one(recorder, 1)
            self.assertGreater(report_id, 0)
            reports.append(report_id)
            self.g._BATTLE_REPORTS.clear()
            self.qa.store.close_thread_db()
            await self.send(conn, 'Battle_QueryReport', self.g.pb_int64_field(1, report_id))
            replay = one(await self.until(conn, 'Battle_QueryReport'), 1)
            self.assertIsInstance(replay, bytes)
            self.assertGreater(len(replay), 100)
            side = one(replay, 2)
            fights = 10 - remaining
            won = one(replay, 1) == 0
            rating = one(replay, 6)
            self.assertIn(rating, (1, 2, 3) if won else (0,))
            base = {3: 5, 2: 4, 1: 3, 0: 1}[rating]
            expected_streak = expected_streak + 1 if won else 0
            streak_bonus = 1 if expected_streak >= 2 else 0
            self.assertEqual((one(side, 11), one(side, 12), one(side, 14)),
                             (base, streak_bonus, expected_streak))
            change = base + streak_bonus
            self.assertEqual(one(recorder, 6), change)
            expected_score += change
            self.assertEqual((one(result, 2), one(status, 2)), (expected_score, expected_score))
            rewards = [(one(item, 2), one(item, 3))
                       for block in many(side, 3) for item in many(block, 4)]
            self.assertEqual(rewards, [(6, 10)])
            saved = self.qa.store.get_or_create_player(player['uin'])[0]
            self.assertEqual(saved['friend_point'], initial_hearts + 10 * fights)
            # Reading the report and status must not consume another attempt.
            await self.send(conn, 'Arena_GetMyInfo')
            reread = await self.until(conn, 'Arena_GetMyInfo')
            self.assertEqual(one(reread, 5), remaining)
            self.assertEqual(len(many(reread, 8)), 10 - remaining)

        self.assertEqual(len(set(reports)), 2)
        fresh = self.qa.store.get_or_create_player(player['uin'])[0]
        state = self.g._load_json_state(fresh, 'arena_data', {})
        self.assertEqual((state['challenges'], len(state['records'])), (2, 2))
        await self.send(first, 'Arena_GetMyInfo')
        self.assertEqual(one(await self.until(first, 'Arena_GetMyInfo'), 5), 8)
        again = await self.login(player['uin'])
        await self.send(again, 'Arena_GetMyInfo')
        reconnected = await self.until(again, 'Arena_GetMyInfo')
        self.assertEqual((one(reconnected, 5), len(many(reconnected, 8))), (8, 2))
        self.assertFalse(any('crashed' in row or 'sending generic' in row
                             for row in self.records), self.records)


if __name__ == '__main__':
    unittest.main()
