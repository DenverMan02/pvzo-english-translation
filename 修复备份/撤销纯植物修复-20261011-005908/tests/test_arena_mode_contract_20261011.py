"""Arena mode boundaries with deployed puppets and disposable SQLite saves."""
import copy
from contextlib import ExitStack
import unittest
from unittest.mock import patch
from worldmap_test_support import IsolatedServer


def unit(pos, **values):
    result = dict(pos=pos, uid=10001 + pos, card_id=1225, hp=1000000,
                  atk=1, def_=0, ability=0, skill1=51, skill2=0,
                  is_leader=False, level=1, power=100, speed=0)
    result.update(values)
    return result


class ArenaModeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = IsolatedServer()
        cls.g = cls.server.game
        cls.g.load_battle_cards()
        cls.g.load_skill_info()

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def setUp(self):
        g = self.g
        self.player = self.server.player(88011011, name='Arena owner', level=100)
        info = g.load_dummy_config()['zombies'][1000001]
        g.store.save_puppet(self.player['uin'], 1000001, level=100, star=1, quality=100,
                            active_skill=info['active'], active_level=1,
                            passive_skill=info['passive'], passive_level=1)
        g.save_puppet_lineup(self.player, [1000001])
        self.assertTrue(g._auto_puppets(self.player))
        self.assertTrue(g._auto_puppet_list(self.player))
        self.bot = dict(uin=900001, name='Arena rival', level=10, face_id=4, score=105,
                        qq_face_url='http://127.0.0.1:8080/image/head/4.png')
        self.left = [unit(3)]
        self.right = [unit(4, hp=5000)]
        with g.store._db() as con:
            g.bots_local._ensure_bots_state_table(con)
            con.execute('INSERT OR REPLACE INTO bots_state(uin,score,power) VALUES(900001,105,100)')
        for field in ('arena_data', 'rank_arena_data', 'allserver_rank_arena_data'):
            state = g._arena_state(self.player, field)
            state.update(challenges=0, score=100, records=[], wins=0, losses=0)
            g._save_json_state(self.player, field, state)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(g.bots_local, 'bots', return_value=[self.bot]))
        stack.enter_context(patch.object(g.bots_local, 'pick_opponent', return_value=self.bot))
        stack.enter_context(patch.object(g, '_refresh_arena_npcs'))
        stack.enter_context(patch.object(g, 'build_player_units',
                                        side_effect=lambda *a, **k: copy.deepcopy(self.left)))
        stack.enter_context(patch.object(g, '_npc_encounter',
                                        side_effect=lambda *a: dict(name=self.bot['name'],
                                                                   level=self.bot['level'],
                                                                   units=copy.deepcopy(self.right))))

    def report(self, suffix, response_field):
        g = self.g
        messages = self.server.invoke(suffix, g.pb_varint_field(1, self.bot['uin']), self.player)
        body = dict(messages)['PVZ.Cmd.Cmd_' + suffix + '_SC']
        if response_field == 7:
            return g.parse_repeated_submessages(body, 7)[0]
        recorder = g.parse_repeated_submessages(body, 3)[0]
        rid = g.parse_varint_fields(recorder)[1]
        reply = self.server.invoke('Battle_QueryReport', g.pb_varint_field(1, rid), self.player)
        return g.parse_repeated_submessages(reply[0][1], 1)[0]

    def test_final_arena_routes_exclude_puppet_simulation_report_and_preserve_ownership(self):
        g = self.g
        owned = g.store.get_puppets(self.player['uin'])
        with patch.object(g, '_auto_puppets', side_effect=AssertionError('arena loaded puppets')), \
                patch.object(g, '_auto_puppet_list', side_effect=AssertionError('arena encoded puppets')):
            for suffix, kind, sides, response_field in (
                    ('Arena_Challenge', 4, (10, 11), 3),
                    ('RankArena_Challenge', 12, (19, 20), 3),
                    ('AllServerRankArena_Challenge', 27, (45, 46), 7)):
                with self.subTest(mode=suffix):
                    report = self.report(suffix, response_field)
                    self.assertEqual(g.parse_varint_fields(report)[5], kind)
                    self.assertEqual((g.parse_varint_fields(report)[1],
                                      g.parse_varint_fields(report)[7]), (1, 2))
                    left, right = [g.parse_repeated_submessages(report, field)[0] for field in (2, 3)]
                    self.assertEqual((g.parse_varint_fields(left)[1], g.parse_varint_fields(right)[1]), sides)
                    for side in (left, right):
                        self.assertFalse(g.parse_repeated_submessages(side, 16))
                    self.assertEqual(g.parse_repeated_submessages(right, 6), [self.bot['name'].encode()])
                    self.assertEqual(g.parse_varint_fields(right)[8], self.bot['level'])
                    self.assertEqual(g.parse_repeated_submessages(right, 13), [self.bot['qq_face_url'].encode()])
                    rounds = g.parse_repeated_submessages(report, 4)
                    self.assertEqual(max(g.parse_varint_fields(rd)[1] for rd in rounds), 30)
                    actions = [action for rd in g.parse_repeated_submessages(report, 4)
                               for action in g.parse_repeated_submessages(rd, 2)]
                    self.assertTrue(actions)
                    self.assertFalse(any(g.parse_repeated_submessages(action, 6) for action in actions))
        self.assertEqual(g.store.get_puppets(self.player['uin']), owned)
        self.assertEqual(g.puppet_lineup(self.player), [1000001])

    def test_shared_encoder_cannot_automatically_or_explicitly_add_arena_puppets(self):
        g = self.g
        for kind, sides in ((4, (10, 11)), (12, (19, 20)), (27, (45, 46))):
            for supplied in (None, g._auto_puppet_list(self.player)):
                with self.subTest(kind=kind, supplied=bool(supplied)):
                    report = g.build_fight_report(self.player, 0, 0, 0,
                                                  left_units=self.left, right_units=self.right,
                                                  fight_type=kind, puppet_bytes=supplied,
                                                  side_types=(38, 39), right_role=self.bot)
                    for field, expected in zip((2, 3), sides):
                        side = g.parse_repeated_submessages(report, field)[0]
                        self.assertEqual(g.parse_varint_fields(side)[1], expected)
                        self.assertFalse(g.parse_repeated_submessages(side, 16))

    def test_generic_fight_cannot_run_adventure_for_arena_targets(self):
        g = self.g
        before = (self.player['energy'], self.player['coin'])
        with patch.object(g, 'build_battle_units', side_effect=AssertionError('arena ran adventure')), \
                patch.object(g, '_auto_puppets', side_effect=AssertionError('arena loaded puppets')):
            for kind in (4, 12, 27, 28):
                with self.subTest(kind=kind):
                    messages = self.server.invoke('Battle_Fight', g.pb_bytes(1, g.pb_varint_field(1, kind)), self.player)
                    self.assertEqual(messages[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
                    self.assertEqual(g.parse_varint_fields(messages[0][1])[1], 5006)
        self.assertEqual((self.player['energy'], self.player['coin']), before)

    def test_invalid_arena_preview_never_falls_back_to_zombie_lineup(self):
        g = self.g
        with patch.object(g, 'preview_enemy_units', side_effect=AssertionError('arena preview ran adventure')):
            for kind, role in ((4, 0), (27, 0), (28, 0), (12, 900999)):
                with self.subTest(kind=kind, role=role):
                    target = g.pb_varint_field(1, kind) + g.pb_varint_field(8, role)
                    messages = self.server.invoke('Battle_GetFightInfo', g.pb_bytes(1, target), self.player)
                    self.assertEqual(messages[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
            with patch.object(g, '_npc_encounter', return_value=dict(units=[])):
                target = g.pb_varint_field(1, 12) + g.pb_varint_field(8, self.bot['uin'])
                messages = self.server.invoke('Battle_GetFightInfo', g.pb_bytes(1, target), self.player)
                self.assertEqual(messages[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')

    def test_valid_ladder_preview_uses_opponent_plants_and_no_puppet_field(self):
        g = self.g
        target = g.pb_varint_field(1, 12) + g.pb_varint_field(8, self.bot['uin'])
        messages = self.server.invoke('Battle_GetFightInfo', g.pb_bytes(1, target), self.player)
        body = dict(messages)['PVZ.Cmd.Cmd_Battle_GetFightInfo_SC']
        enemy = g.parse_repeated_submessages(body, 4)
        self.assertEqual([(g.parse_varint_fields(card)[1], g.parse_varint_fields(card)[2]) for card in enemy], [(4, 1225)])
        self.assertFalse(g.parse_repeated_submessages(body, 6))

    def test_real_bot_without_avatar_metadata_matches_existing_arena_avatar(self):
        g = self.g
        bot = dict(uin=900001, name='Roster bot', level=75)
        role = g._arena_fight_role(bot, dict(name=bot['name'], level=bot['level']))
        dto = g._rank_arena_opponent_dto(bot['uin'], bot['name'], bot['level'], 100, 1)
        self.assertEqual(role['face_id'], g.parse_varint_fields(dto)[3])
        self.assertEqual(role['qq_face_url'].encode(), g.parse_repeated_submessages(dto, 5)[0])

    def test_shared_combat_keeps_nonarena_default_round_budget(self):
        rounds = self.g.run_battle(copy.deepcopy(self.left), copy.deepcopy(self.right))[3]
        self.assertEqual(len(rounds), 60)


if __name__ == '__main__':
    unittest.main()
