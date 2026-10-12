"""Home formations: authored fourth/fifth unlocks and safe dependent refreshes.

All saves are synthetic and loaded through IsolatedServer.  The first three
slots retain the existing local behavior; the original resource strings gate
the fourth and fifth slots at town levels 25 and 30 respectively.
"""
import copy
import importlib
import json
import time
import unittest
from unittest.mock import patch

from worldmap_test_support import IsolatedServer, many, message, one


class HomeFormationUnlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = IsolatedServer()
        cls.g = cls.qa.game
        cls.store = cls.qa.store
        cls.wm = importlib.import_module('worldmap_local')
        cls.a = cls.g._worldmap_adapter
        cls.r = cls.a.resources
        cls.serial = 0

    @classmethod
    def tearDownClass(cls):
        cls.qa.close()

    def setUp(self):
        type(self).serial += 1
        self.now = 1800000000
        clock = patch.object(self.wm, '_now', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.p = self.qa.player(99860000 + self.serial, level=100,
                                coin=1000000, role_created=1)
        self.g.rebuild_td_roster(self.p)
        self.g.ensure_player_cards(self.p)
        cards = self.store.get_cards(self.p['uin'])
        # Explicit extra owned-card fixtures avoid depending on roster size.
        for index in range(max(0, 5 - len(cards))):
            self.store.add_card(self.p['uin'], self.p['uin'] * 100 + index,
                                cards[0][1], 100, 0)
        self.uids = [row[0] for row in self.store.get_cards(self.p['uin'])]
        self.pos = self.g._FORMATION_POS
        self.assertGreaterEqual(len(self.uids), 5)
        self.a.get_formation(b'', self.p)  # Initialize home before snapshots.

    def v(self, number, value):
        return self.g.pb_varint_field(number, value)

    def m(self, number, value):
        return self.g.pb_bytes(number, value)

    def position(self, value):
        return self.v(1, value[0]) + self.v(2, value[1])

    def call(self, name, data=b'', player=None):
        return self.qa.invoke(name, data, player or self.p)

    def level(self, value):
        self.store.save_player(self.p['uin'], level=value)
        self.p['level'] = value

    def formation_reply(self, team=None, player=None):
        data = b'' if team is None else self.v(1, team)
        return message(self.call('Battle_GetFormation', data, player),
                       'Battle_GetFormation')

    def dto(self, team, player=None):
        return one(self.formation_reply(team, player), 1)

    def assign(self, team, placements, leader=0):
        dto = b''.join(self.m(1, self.v(1, pos) + self.v(2, uid))
                       for pos, uid in placements.items())
        return self.call('Battle_SetFormation',
                         self.m(1, dto + self.v(5, team) + self.v(3, leader)))

    def seed_locked(self, team):
        # Simulate a saved formation created before a town-level correction.
        self.store.set_formation(self.p['uin'], team,
                                 {self.pos[4]: self.uids[4]})
        self.store.set_formation_leader(self.p['uin'], team, self.uids[4])

    def state(self):
        with self.a.db(self.p) as con:
            return self.a.state(con, self.p)

    def city_dispatch(self, team, npc_index):
        npc = self.wm.NPCS[npc_index]
        ready = message(self.call('War_DeclareWar', self.v(1, npc['role_id'])),
                        'War_DeclareWar')
        self.now = max(self.now, one(ready, 2) + 2)
        cmd = self.v(1, 2) + self.m(2, self.position(npc['pos'])) + self.v(3, team)
        return self.call('War_SetOut', cmd)

    def assert_refused(self, replies, success):
        self.assertTrue(any(name.endswith('LogicError_SC')
                            for name, _ in replies), replies)
        self.assertFalse(any(name.endswith(success + '_SC')
                             for name, _ in replies), replies)

    def test_authored_unlock_boundaries_return_only_available_slots_and_five_max(self):
        self.assertEqual(self.wm.ATTACK_TEAMS, (1, 1001, 1002, 1003, 1004))
        for level, ids in ((24, [1, 1001, 1002]),
                           (25, [1, 1001, 1002, 1003]),
                           (29, [1, 1001, 1002, 1003]),
                           (30, [1, 1001, 1002, 1003, 1004])):
            with self.subTest(level=level):
                self.level(level)
                reply = self.formation_reply()
                self.assertEqual(one(reply, 3), 5)
                self.assertEqual([one(dto, 5) for dto in many(reply, 2)], ids)
                self.assertIn(one(one(reply, 1), 5), ids)

    def test_fourth_and_fifth_thresholds_match_the_original_clientdb_rows(self):
        rows = self.g._read_clientdb_table('levelupOpenInfo.txt')
        self.assertTrue(rows)
        header = {name: index for index, name in enumerate(rows[0])}
        authored = {int(row[header['id']]): int(row[header['openLV']])
                    for row in rows[1:] if row[header['id']].isdigit()}
        self.assertEqual((authored[81], authored[88]), (25, 30))
        self.assertEqual(self.wm.FORMATION_UNLOCK_LEVELS[3:],
                         (authored[81], authored[88]))

    def test_first_three_slots_remain_visible_and_editable_at_town_level_one(self):
        self.level(1)
        reply = self.formation_reply()
        self.assertEqual([one(dto, 5) for dto in many(reply, 2)], [1, 1001, 1002])
        self.assertEqual(one(reply, 3), 5)
        for index, team in enumerate((1, 1001, 1002)):
            with self.subTest(team=team):
                message(self.assign(team, {self.pos[index]: self.uids[index]}, self.uids[index]),
                        'Battle_GetFormation')
                self.assertEqual(one(self.dto(team), 3), self.uids[index])
                self.assertEqual(one(message(self.call('Battle_SetDefaultTeam', self.v(1, team)),
                                              'Battle_SetDefaultTeam'), 1), team)

    def test_requesting_locked_saved_slot_does_not_select_or_delete_it(self):
        for level, team in ((24, 1003), (29, 1004)):
            with self.subTest(level=level, team=team):
                self.seed_locked(team)
                before = self.store.get_formation_with_owner(self.p['uin'], team)
                self.level(level)
                self.assert_refused(self.call('Battle_GetFormation', self.v(1, team)),
                                    'Battle_GetFormation')
                reply = self.formation_reply()
                self.assertNotIn(team, [one(dto, 5) for dto in many(reply, 2)])
                self.assertEqual(self.store.get_formation_with_owner(self.p['uin'], team), before)

    def test_locked_slot_save_is_refused_and_preserves_existing_layout_and_captain(self):
        for level, team in ((24, 1003), (29, 1004)):
            with self.subTest(level=level, team=team):
                self.seed_locked(team)
                self.level(level)
                before = self.store.get_formation_with_owner(self.p['uin'], team)
                replies = self.assign(team, {self.pos[3]: self.uids[3]}, self.uids[3])
                self.assert_refused(replies, 'Battle_GetFormation')
                self.assertEqual(self.store.get_formation_with_owner(self.p['uin'], team), before)
                self.assertEqual(self.store.get_formation_leader(self.p['uin'], team), self.uids[4])

    def test_locked_slot_default_selection_is_refused(self):
        for level, team in ((24, 1003), (29, 1004)):
            with self.subTest(level=level, team=team):
                self.level(level)
                self.assert_refused(self.call('Battle_SetDefaultTeam', self.v(1, team)),
                                    'Battle_SetDefaultTeam')
                self.assertEqual(self.state()['default_team'], 1)

    def test_locked_saved_slot_cannot_become_town_defender(self):
        for level, team in ((24, 1003), (29, 1004)):
            with self.subTest(level=level, team=team):
                self.seed_locked(team)
                self.level(level)
                self.assert_refused(self.call('Battle_SetDefendTeam', self.v(1, team)),
                                    'Battle_GetFormation')
                self.assertEqual(self.state()['defend_team'], 0)

    def test_locked_saved_slot_cannot_start_a_declared_city_mission(self):
        npc = self.wm.NPCS[0]
        ready = message(self.call('War_DeclareWar', self.v(1, npc['role_id'])),
                        'War_DeclareWar')
        self.now = max(self.now, one(ready, 2) + 2)
        for level, team in ((24, 1003), (29, 1004)):
            with self.subTest(level=level, team=team):
                self.seed_locked(team)
                self.level(level)
                cmd = self.v(1, 2) + self.m(2, self.position(npc['pos'])) + self.v(3, team)
                self.assert_refused(self.call('War_SetOut', cmd), 'War_SetOut')
                with self.a.db(self.p) as con:
                    self.assertIsNone(self.a.mission(con, self.p, team))
                    self.assertEqual(self.a.state(con, self.p)['used'], 0)

    def test_locked_saved_slot_cannot_start_a_mine_mission(self):
        for level, team in ((24, 1003), (29, 1004)):
            with self.subTest(level=level, team=team):
                self.seed_locked(team)
                self.level(level)
                cmd = self.m(1, self.position((20, 20))) + self.v(2, team)
                self.assert_refused(self.call('Mine_SetOutTeam', cmd), 'Mine_SetOutTeam')
                with self.r.db(self.p) as con:
                    self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_mine_missions '
                                                 'WHERE uin=?', (self.p['uin'],)).fetchone()[0], 0)

    def test_locked_saved_slot_cannot_capture_a_temple(self):
        temple = self.r.templates()[0][0]
        for level, team in ((24, 1003), (29, 1004)):
            with self.subTest(level=level, team=team):
                self.seed_locked(team)
                self.level(level)
                cmd = self.m(1, self.position(temple['pos'])) + self.v(2, team) + self.v(3, temple['area'])
                self.assert_refused(self.call('Temple_SetOut', cmd), 'Temple_SetOut')
                with self.r.db(self.p) as con:
                    self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_temples '
                                                 'WHERE uin=? AND captured_at>0',
                                                 (self.p['uin'],)).fetchone()[0], 0)

    def test_fourth_and_fifth_layout_and_captain_survive_a_new_session(self):
        for index, team in ((3, 1003), (4, 1004)):
            message(self.assign(team, {self.pos[index]: self.uids[index]}, self.uids[index]),
                    'Battle_GetFormation')
        self.store.close_thread_db()
        relog = self.qa.player(self.p['uin'], name='', level=100)
        for index, team in ((3, 1003), (4, 1004)):
            with self.subTest(team=team):
                dto = self.dto(team, relog)
                self.assertEqual(one(dto, 5), team)
                self.assertEqual(one(dto, 3), self.uids[index])
                self.assertEqual([(one(cp, 1), one(cp, 2)) for cp in many(dto, 1)],
                                 [(self.pos[index], self.uids[index])])

    def test_fourth_and_fifth_captain_can_be_explicitly_cleared(self):
        for index, team in ((3, 1003), (4, 1004)):
            with self.subTest(team=team):
                layout = {self.pos[index]: self.uids[index]}
                message(self.assign(team, layout, self.uids[index]), 'Battle_GetFormation')
                message(self.assign(team, layout, 0), 'Battle_GetFormation')
                self.assertEqual(one(self.dto(team), 3), 0)
                self.assertEqual(self.store.get_formation_leader(self.p['uin'], team), 0)

    def test_transfer_from_legacy_map_slot_to_fifth_clears_old_captain_without_cloning(self):
        uid = self.uids[1]
        message(self.assign(1001, {self.pos[1]: uid}, uid), 'Battle_GetFormation')
        message(self.assign(1004, {self.pos[4]: uid}, uid), 'Battle_GetFormation')
        self.assertEqual(self.a.placements(self.p, 1001), {})
        self.assertEqual(one(self.dto(1001), 3), 0)
        self.assertEqual(one(self.dto(1004), 3), uid)
        deployed = [one(cp, 2) for dto in many(self.formation_reply(), 2) for cp in many(dto, 1)]
        self.assertEqual(len(deployed), len(set(deployed)))

    def test_new_default_and_town_defender_remain_authoritative(self):
        message(self.assign(1004, {self.pos[4]: self.uids[4]}, self.uids[4]),
                'Battle_GetFormation')
        message(self.call('Battle_SetDefaultTeam', self.v(1, 1004)), 'Battle_SetDefaultTeam')
        self.call('Battle_SetDefendTeam', self.v(1, 1004))
        self.assertEqual(one(self.formation_reply(), 4), 1004)
        self.assertEqual(one(self.dto(1004), 8), 1)
        self.assert_refused(self.assign(1004, {}), 'Battle_GetFormation')
        self.call('Battle_CancelDefendTeam', self.v(1, 1004))
        self.assertEqual(one(self.dto(1004), 8), 0)

    def test_fourth_and_fifth_city_missions_lock_only_their_plants_and_return_on_recall(self):
        self.qa.train_owned_cards(self.p, level=100)
        for index, team, npc_index in ((3, 1003, 0), (4, 1004, 1)):
            with self.subTest(team=team):
                uid = self.uids[index]
                layout = {self.pos[index]: uid}
                message(self.assign(team, layout, uid), 'Battle_GetFormation')
                message(self.city_dispatch(team, npc_index), 'War_SetOut')
                self.assertEqual(one(self.dto(team), 6), 1)
                with self.a.db(self.p) as con:
                    mission = dict(self.a.mission(con, self.p, team))
                    self.assertEqual(mission['state'], self.wm.MARCHING)
                    self.assertIn(uid, self.a.busy_cards(con, self.p))
                # Editing the dispatched team or moving its plant to another
                # free team must not alter the frozen formation.
                self.assert_refused(self.assign(team, {}), 'Battle_GetFormation')
                other = 1004 if team == 1003 else 1003
                self.assert_refused(self.assign(other, {self.pos[0]: uid}, uid),
                                    'Battle_GetFormation')
                self.assertEqual(self.a.placements(self.p, team), layout)
                message(self.assign(other, {self.pos[2]: self.uids[2]}, self.uids[2]),
                        'Battle_GetFormation')
                self.assertEqual(one(self.dto(other), 6), 0)
                self.assertEqual(one(self.dto(other), 3), self.uids[2])
                message(self.call('War_RecallTeam', self.v(1, team) + self.v(2, 1)),
                        'War_RecallTeam')
                with self.a.db(self.p) as con:
                    returning = dict(self.a.mission(con, self.p, team))
                    self.assertEqual(returning['state'], self.wm.RECALLING)
                    self.assertEqual(returning['cancelled'], 1)
                self.now = returning['home_at'] + 1
                self.a.advance(self.p)
                self.assertEqual(one(self.dto(team), 6), 0)
                with self.a.db(self.p) as con:
                    self.assertIsNone(self.a.mission(con, self.p, team))
                    self.assertNotIn(uid, self.a.busy_cards(con, self.p))
                message(self.assign(team, layout, 0), 'Battle_GetFormation')
                self.assertEqual(one(self.dto(team), 3), 0)

    def test_fourth_and_fifth_town_defenders_cannot_be_dispatched(self):
        for index, team, npc_index in ((3, 1003, 0), (4, 1004, 1)):
            with self.subTest(team=team):
                message(self.assign(team, {self.pos[index]: self.uids[index]}, self.uids[index]),
                        'Battle_GetFormation')
                self.call('Battle_SetDefendTeam', self.v(1, team))
                self.assertEqual(one(self.dto(team), 8), 1)
                replies = self.city_dispatch(team, npc_index)
                self.assert_refused(replies, 'War_SetOut')
                self.assertEqual(one(message(replies, 'LogicError'), 1), 4721)
                with self.a.db(self.p) as con:
                    self.assertIsNone(self.a.mission(con, self.p, team))
                self.call('Battle_CancelDefendTeam', self.v(1, team))
                self.assertEqual(one(self.dto(team), 8), 0)

    def test_direct_refresh_matches_adapter_for_every_unlock_boundary(self):
        for level in (24, 25, 29, 30):
            with self.subTest(level=level):
                self.level(level)
                direct = self.g.handle_battle_getformation(b'', self.p)
                expected = message(self.a.get_formation(b'', self.p), 'Battle_GetFormation')
                self.assertEqual(direct, expected)

    def test_playing_levelups_push_each_new_slot_live_and_skip_non_unlock_levels(self):
        self.level(24)
        self.p['exp'] = 0
        self.store.save_player(self.p['uin'], exp=0)
        first = self.g.grant_role_exp(self.p, self.g.need_exp(24))
        self.assertEqual(self.p['level'], 25)
        sync = message(first, 'Battle_SyncNewFormation')
        self.assertEqual((one(sync, 1), one(sync, 2), many(sync, 3)), (1, 5, [1003]))
        reply = message(first, 'Battle_GetFormation')
        self.assertEqual([one(dto, 5) for dto in many(reply, 2)], [1, 1001, 1002, 1003])
        middle = self.g.grant_role_exp(self.p, self.g.need_exp(25))
        self.assertEqual(self.p['level'], 26)
        self.assertFalse(any(name.endswith(('Battle_SyncNewFormation_SC',
                                            'Battle_GetFormation_SC')) for name, _ in middle))
        last = self.g.grant_role_exp(self.p, sum(self.g.need_exp(level) for level in range(26, 30)))
        self.assertEqual(self.p['level'], 30)
        sync = message(last, 'Battle_SyncNewFormation')
        self.assertEqual((one(sync, 1), one(sync, 2), many(sync, 3)), (1, 5, [1004]))
        reply = message(last, 'Battle_GetFormation')
        self.assertEqual([one(dto, 5) for dto in many(reply, 2)], [1, 1001, 1002, 1003, 1004])

    def test_jump_across_both_thresholds_pushes_two_new_ids_and_full_list_once(self):
        self.level(24)
        self.p['exp'] = 0
        self.store.save_player(self.p['uin'], exp=0)
        replies = self.g.grant_role_exp(self.p, sum(self.g.need_exp(level) for level in range(24, 30)))
        self.assertEqual(self.p['level'], 30)
        syncs = [body for name, body in replies if name.endswith('Battle_SyncNewFormation_SC')]
        self.assertEqual(len(syncs), 1)
        self.assertEqual((one(syncs[0], 1), one(syncs[0], 2), many(syncs[0], 3)),
                         (2, 5, [1003, 1004]))
        forms = [body for name, body in replies if name.endswith('Battle_GetFormation_SC')]
        self.assertEqual(len(forms), 1)
        self.assertEqual([one(dto, 5) for dto in many(forms[0], 2)], [1, 1001, 1002, 1003, 1004])
        self.assertEqual(self.qa.player(self.p['uin'], name='')['level'], 30)

    def test_direct_refresh_joins_atomic_connection_without_committing_it(self):
        before_coin = self.p['coin']
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'rollback after refresh'):
            with self.store.atomic_player(self.p) as outer:
                self.store.save_player(self.p['uin'], coin=before_coin - 123)
                reply = self.g.handle_battle_getformation(b'', self.p)
                self.assertEqual([one(dto, 5) for dto in many(reply, 2)],
                                 [1, 1001, 1002, 1003, 1004])
                with self.a.db(self.p) as formation_con, self.r.db(self.p) as resource_con:
                    self.assertIs(formation_con, outer)
                    self.assertIs(resource_con, outer)
                self.assertTrue(outer.in_transaction)
                raise RuntimeError('rollback after refresh')
        self.assertLess(time.monotonic() - started, 5,
                        'A nested refresh must not wait for another SQLite writer')
        self.assertEqual(self.qa.player(self.p['uin'], name='')['coin'], before_coin)
        self.assertEqual(self.p['coin'], before_coin)
        self.assertEqual(one(self.g.handle_battle_getformation(b'', self.p), 3), 5)

    def test_puppet_promotion_and_materials_roll_back_when_formation_refresh_fails(self):
        base = 1000001
        config = self.g.load_dummy_config()
        info = config['zombies'][base]
        gear = self.g.puppet_spars_text({part: (level, quality)
                                        for part, quality, level
                                        in config['quality'][100]['equip_need']})
        self.store.save_puppet(self.p['uin'], base, level=10, quality=100, exp=0, star=1,
                               active_skill=info['active'], active_level=1,
                               passive_skill=info['passive'], passive_level=1, spars=gear)
        self.store.add_item(self.p['uin'], 95539, 2)
        before = self.store.get_puppet(self.p['uin'], base)
        player_before = copy.deepcopy(self.p)
        with patch.object(self.g, '_puppet_deployment_signature', side_effect=[(0,), (1,)]), \
             patch.object(self.a, 'formation', side_effect=RuntimeError('injected refresh failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected refresh failure'):
                self.call('Puppet_UpgradeQuality', self.v(1, base) + self.v(2, 1))
        self.assertEqual(self.store.get_puppet(self.p['uin'], base), before)
        self.assertEqual(self.store.get_item_count(self.p['uin'], 95539), 2)
        self.assertEqual(self.p, player_before)

    def test_queries_and_saves_preserve_legacy_map_and_borrowed_adventure_owner(self):
        friend_pos = self.pos[-1]
        friend_uid, friend_owner = 8877665544, 77665544
        self.store.set_formation(self.p['uin'], 1,
                                 {self.pos[0]: self.uids[0], friend_pos: friend_uid},
                                 {friend_pos: friend_owner})
        legacy = {self.pos[1]: self.uids[1]}
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET formation=?,leader=?,home_initialized=1 '
                        'WHERE uin=?', (json.dumps(legacy), self.uids[1], self.p['uin']))
        home_before = self.store.get_formation_with_owner(self.p['uin'], 1)
        self.formation_reply()
        message(self.assign(1003, {self.pos[3]: self.uids[3]}, self.uids[3]), 'Battle_GetFormation')
        message(self.assign(1, {self.pos[0]: self.uids[0]}, self.uids[0]), 'Battle_GetFormation')
        self.assertEqual(self.store.get_formation_with_owner(self.p['uin'], 1), home_before)
        self.assertEqual(self.a.placements(self.p, 1001), legacy)
        self.assertEqual(one(self.dto(1001), 3), self.uids[1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
