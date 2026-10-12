"""Adventure formation regressions: partial layouts and borrowed-card moves."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
spec = importlib.util.spec_from_file_location('formation_under_test', os.environ.get('PVZ_FORMATION_SOURCE', str(SERVER / 'pvz_poc_server.py')))
game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(game)
store = game.store

class AdventureFormationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(store.close_thread_db)
        p = patch.object(store, 'DB_PATH', str(Path(self.tmp.name) / 'test.db'))
        p.start()
        self.addCleanup(p.stop)
        store.init_db()
        self.player = {'uin': 700000204, 'role_id': 700000204}
        self.defaults = {3: 10001706, 7: 10006503, 9: 10006904, 11: 10001243, 13: 10008103, 15: 10001223}
        self.friend_uid = 4006101
        mocks = [
            patch.object(game, 'ensure_player_cards'),
            patch.object(store, 'get_cards', return_value=[(uid, 1001, 1, 0) for uid in self.defaults.values()]),
            patch.object(store, 'get_friend_by_role_id', side_effect=lambda uin, owner: {'friend_role_id': owner} if owner == 4 else None),
            patch.object(game, '_preferred_formation_placements', return_value=self.defaults.copy()),
            patch.object(game, 'default_formation_placements', return_value=self.defaults.copy()),
            patch.object(game, '_legacy_formation_placements', return_value={}),
            patch.object(game, 'note_battle_scene'),
            patch.object(game, 'load_battle_cards'),
            patch.object(game, '_BATTLE_CARDS', {}),
            patch.object(game, 'deployed_stat_bonus', return_value={}),
            patch.object(game, '_friend_help_card', return_value=(self.friend_uid, 1001, 1, {})),
            patch.object(game, 'handle_friend_getlist', return_value=b'')]
        for m in mocks:
            m.start()
            self.addCleanup(m.stop)

    def request(self, rows):
        cps = b''.join(game.pb_bytes(1, game.pb_varint_field(1, pos) + game.pb_int64_field(2, uid) + (game.pb_int64_field(3, owner) if owner else b'')) for pos, uid, owner in rows)
        return game.pb_bytes(1, cps + game.pb_varint_field(5, 1))

    def send(self, placements, owners=None):
        owners = owners or {}
        messages = game.handle_fuben_setformation(self.request([(p,u,owners.get(p,0)) for p,u in placements.items()]), self.player)
        return self.decode(dict(messages)['PVZ.Cmd.Cmd_Fuben_GetFormation_SC'])

    def decode(self, body):
        formation = game.parse_repeated_submessages(body, 1)[0]
        rows = [game.parse_varint_fields(cp) for cp in game.parse_repeated_submessages(formation, 1)]
        return {r[1]: (r[2], r.get(3,0)) for r in rows}

    def test_logged_remove_then_move_friend_does_not_restore_removed_card(self):
        own = {p:u for p,u in self.defaults.items() if p != 3}
        for pos in (3, 17, 3, 17):
            layout = dict(own)
            layout[pos] = self.friend_uid
            expected = {p:(u,4 if p == pos else 0) for p,u in layout.items()}
            self.assertEqual(self.send(layout, {pos:4}), expected)
            self.assertEqual(self.decode(game.handle_fuben_getformation(b'',self.player)), expected)
            self.assertEqual(store.get_formation_with_owner(self.player['uin'],1), expected)

    def test_moved_owned_card_never_gets_second_copy(self):
        layout = {7:10001706,9:10006904,11:10001243,13:10008103,17:self.friend_uid}
        actual = self.send(layout, {17:4})
        self.assertEqual(actual, {p:(u,4 if p==17 else 0) for p,u in layout.items()})

    def test_existing_duplicates_are_cleaned_and_persisted(self):
        store.set_formation(self.player['uin'],1,{3:10001706,7:10001706,17:self.friend_uid},{17:4})
        self.assertEqual(game._fuben_formation_entries(self.player), ({3:10001706,17:self.friend_uid},{17:4}))
        self.assertEqual(store.get_formation_with_owner(self.player['uin']), {3:(10001706,0),17:(self.friend_uid,4)})

    def test_partial_without_friend_is_preserved(self):
        self.assertEqual(self.send({9:10001706}), {9:(10001706,0)})

    def test_friend_only_is_not_auto_filled(self):
        self.assertEqual(self.send({17:self.friend_uid},{17:4}), {17:(self.friend_uid,4)})

    def test_full_six_plus_friend_is_preserved(self):
        layout = dict(self.defaults)
        layout[17] = self.friend_uid
        self.assertEqual(self.send(layout,{17:4}), {p:(u,4 if p==17 else 0) for p,u in layout.items()})

    def test_new_team_still_initializes(self):
        self.assertEqual(game._fuben_formation_entries(self.player), (self.defaults,{}))

    def test_invalid_or_duplicate_submission_keeps_previous_lineup(self):
        for rows in ([(3,10001706,0),(7,10001706,0)], [(3,10001706,0),(3,10006503,0)], [(99,10001706,0)]):
            store.set_formation(self.player['uin'],1,{9:10006904})
            game.handle_fuben_setformation(self.request(rows),self.player)
            self.assertEqual(store.get_formation_with_owner(self.player['uin']), {9:(10006904,0)})

    def leader_request(self, layout, leader):
        wire = self.request([(p,u,0) for p,u in layout.items()])
        form = game.parse_repeated_submessages(wire,1)[0]
        return game.pb_bytes(1, form + game.pb_int64_field(3,leader))

    def test_captain_selection_is_saved_and_echoed(self):
        layout = {3:10001706,9:10006904}
        for leader in (10006904,10001706,10006904):
            messages = game.handle_fuben_setformation(self.leader_request(layout,leader), self.player)
            form = game.parse_repeated_submessages(dict(messages)['PVZ.Cmd.Cmd_Fuben_GetFormation_SC'],1)[0]
            self.assertEqual(game.parse_varint_fields(form)[3],leader)
            self.assertEqual(store.get_formation_leader(self.player['uin']),leader)
            body = game.handle_fuben_getformation(b'',self.player)
            self.assertEqual(game.parse_varint_fields(game.parse_repeated_submessages(body,1)[0])[3],leader)

    def test_captain_removal_falls_back_to_remaining_owned_card(self):
        store.set_formation_leader(self.player['uin'],1,10006904)
        self.send({3:10001706,17:self.friend_uid},{17:4})
        self.assertEqual(store.get_formation_leader(self.player['uin']),10001706)

    def test_invalid_captain_does_not_replace_valid_captain(self):
        store.set_formation_leader(self.player['uin'],1,10006904)
        game.handle_fuben_setformation(self.leader_request({3:10001706,9:10006904},999),self.player)
        self.assertEqual(store.get_formation_leader(self.player['uin']),10006904)

if __name__ == '__main__':
    unittest.main()


