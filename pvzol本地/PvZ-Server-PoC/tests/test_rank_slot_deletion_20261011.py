"""Deleting a real account must close its ladder slot without rewriting earned order or rewards."""
import unittest
from unittest.mock import patch
import test_ladder_progression_20261011 as ladder
import account_management


class RankSlotDeletionTests(unittest.TestCase):
    def setUp(self):
        # Reuse disposable storage/real final-route setup without inheriting and
        # rediscovering the entire parent regression suite.
        self.qa = ladder.LadderProgressionTests('test_winning_swaps_places_and_every_rank_route_agrees')
        self.qa.setUp()
        self.addCleanup(self.qa.doCleanups)
        self.g, self.store = ladder.g, ladder.store
        self.qa.seed_rank_101()
        self.qa.at(11, 21, 10)
        self.assertEqual(self.qa.my_rank(), 101)
        self.assertEqual(self.qa.saved()['reward_rank'], 101)
        self.other, _ = self.store.get_or_create_player(88011151)
        self.store.save_player(self.other['uin'], name='DeleteLadderChampion',
                               role_created=1, level=100)
        self.other = self.store.get_or_create_player(self.other['uin'])[0]
        self.g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_RankArena_GetMyInfo_CS'](b'', self.other)
        match = self.g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_RankArena_Challenge_CS'](
            self.g.pb_int64_field(1, 900001), self.other)
        self.assertEqual(self.g.parse_varint_fields(
            dict(match)['PVZ.Cmd.Cmd_RankArena_Challenge_SC'])[1], 1)
        # Establish a second earned move; compression must preserve this order,
        # even though the original bot score ordering puts this user at 101.
        fourth = next(uin for uin, rank in self.qa.ranks().items() if rank == 4)
        self.qa.challenge(fourth)
        self.assertEqual(self.qa.my_rank(), 4)
        self.before = self.qa.ranks()
        self.snapshot = self.qa.saved()
        deleted = account_management.delete_account(self.other['uin'])
        self.assertEqual(deleted['uin'], self.other['uin'])
        self.assertIsNone(account_management.get_account(self.other['uin']))

    def raw_slots(self):
        with self.store._db() as con:
            return [(int(row['uin']), int(row['rank'])) for row in con.execute(
                'SELECT uin,rank FROM local_rank_arena_slots ORDER BY rank').fetchall()]

    def expected_order(self):
        return [uin for uin, rank in sorted(self.before.items(), key=lambda item: item[1])
                if uin != self.other['uin']]

    def test_actual_account_deletion_closes_gap_preserves_earned_order_and_settlement_snapshot(self):
        after = self.qa.ranks()
        self.assertEqual(list(after), self.expected_order())
        self.assertEqual(list(after.values()), list(range(1, len(after) + 1)))
        self.assertEqual(after[self.qa.player['uin']], 3)
        self.assertNotIn(self.other['uin'], after)
        state = self.qa.saved()
        self.assertEqual(state['last_rank'], 3)
        for field in ('reward_period', 'reward_rank', 'reward_taken', 'reward_settled_at'):
            self.assertEqual(state[field], self.snapshot[field], field)
        self.assertEqual(state['reward_rank'], 101)
        info = self.qa.info()
        self.assertEqual(self.qa.rewards(info), ladder.VIDEO_RANK_101_REWARD)
        self.assertEqual([row[8] for row in self.qa.rows(info, 11)], [1, 2, 3])
        # Public ranking callers must see exactly the same compacted positions.
        generic = self.qa.rows(self.qa.body('GetSelfRank', self.g.pb_varint_field(1, 5)), 6)[0]
        self.assertEqual((generic[1], generic[2]), (3, self.qa.player['uin']))
        self.assertTrue(all(rank > 0 for _, rank in self.raw_slots()))

    def test_failed_normalization_does_not_commit_temporary_negative_ranks_or_new_snapshot(self):
        # Deletion has committed; only the subsequent board normalization may
        # roll back. Inject failure after its UNIQUE-safe temporary rank writes.
        original_save = self.g._save_json_state
        def fail_new_rank(player, field, state):
            if field == 'rank_arena_data' and player['uin'] == self.qa.player['uin'] and state.get('last_rank') == 3:
                raise RuntimeError('QA normalization persistence failure')
            return original_save(player, field, state)
        before_raw = self.raw_slots()
        with patch.object(self.g, '_save_json_state', side_effect=fail_new_rank):
            with self.assertRaises(RuntimeError):
                # This generic board route has no enclosing route transaction;
                # board() itself must protect normalization and both saves.
                self.qa.body('GetRankList', self.g.pb_varint_field(1, 5))
        self.assertEqual(self.raw_slots(), before_raw)
        self.assertTrue(all(rank > 0 for _, rank in self.raw_slots()))
        state = self.qa.saved()
        self.assertEqual((state['last_rank'], state['reward_rank']), (4, 101))
        self.assertEqual(state['reward_taken'], self.snapshot['reward_taken'])
        # Retrying performs the full normalization successfully.
        after = self.qa.ranks()
        self.assertEqual(list(after), self.expected_order())
        self.assertEqual(list(after.values()), list(range(1, len(after) + 1)))
        self.assertEqual((self.qa.saved()['last_rank'], self.qa.saved()['reward_rank']), (3, 101))


if __name__ == '__main__':
    unittest.main()
