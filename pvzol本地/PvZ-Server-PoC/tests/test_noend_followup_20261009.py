"""Original NoEnd protocol flow, running exclusively against disposable databases."""
import atexit
import copy
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
if not (SERVER / 'pvz_poc_server.py').exists():
    SERVER = Path(r'D:\pvzol本地最新最新ol\pvzol本地\PvZ-Server-PoC')
sys.path.insert(0, str(SERVER))
sys.dont_write_bytecode = True
import player_store as store

# Redirect before the server's first import, so no production save or log is touched.
BOOT = tempfile.TemporaryDirectory()
store.set_db_path(str(Path(BOOT.name) / 'boot.db'))
store.init_db()
atexit.register(BOOT.cleanup)
atexit.register(store.close_thread_db)
with patch.object(logging, 'FileHandler', return_value=logging.NullHandler()), \
        patch.object(logging, 'basicConfig'):
    import pvz_poc_server as g
    import missing_features_local as mf
g._CLIENTDB_PATH = str(SERVER.parent / 'webroot' / 'clientDB.txt')


def unit(pos, uid=0, hp=1000000, atk=1, leader=False, card_id=None):
    return dict(pos=pos, uid=uid, card_id=card_id or (1101 if pos % 2 else 111),
                hp=hp, atk=atk, def_=0, ability=0, skill1=0, skill2=0,
                is_leader=leader, level=1, power=100, name='test')


class NoEndFollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_db = store.DB_PATH
        self.addCleanup(lambda: store.set_db_path(old_db))
        store.set_db_path(str(Path(self.tmp.name) / 'player.db'))
        store.init_db()
        self.player, _ = store.get_or_create_player(88009107)
        self.uin = self.player['uin']
        self.player['ticket'] = 100
        store.save_player(self.uin,ticket=100)
        self.api = dict(mf._API, db_path=store.DB_PATH)
        self.routes = mf.register(self.api)
        self.addCleanup(store.close_thread_db)
        for uid, cid in ((9001, 1101), (9002, 1201), (9003, 1301)):
            store.add_card(self.uin, uid, cid, 100)
        self.call('SetPreCard', self.pre_cards(9001, 9002))
        with mf._db() as con:
            con.execute('UPDATE local_noend_fuben SET mode_id=3 WHERE uin=?', (self.uin,))
        # Apply lazy additive run-state migration before taking state snapshots.
        g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_NoEndFuben_Info_CS'](b'', self.player)
        g._BATTLE_REPORTS.clear()

    @staticmethod
    def pre_cards(*uids):
        return b''.join(g.pb_int64_field(1, uid) for uid in uids)

    def call(self, name, body=b''):
        return self.routes['PVZ.Cmd.Cmd_NoEndFuben_' + name + '_CS'](body, self.player)

    def row(self):
        with mf._db() as con:
            return dict(mf._noend_row(con, self.uin))

    def set_formation(self, positions, leader):
        cards = b''.join(g.pb_bytes(1, g.pb_varint_field(1, pos) + g.pb_int64_field(2, uid))
                         for pos, uid in positions.items())
        return self.call('SetFormation', g.pb_bytes(1, cards + g.pb_int64_field(3, leader)))

    def equip_puppet(self, slot=1):
        info = g.load_dummy_config()['zombies'][1000001]
        store.save_puppet(self.uin, 1000001, level=100, star=1, quality=100,
                          active_skill=info['active'], active_level=1,
                          passive_skill=info['passive'], passive_level=1)
        g.save_puppet_lineup(self.player, [0] * (slot - 1) + [1000001])

    def fight(self, stage=1, enemy_hp=10000, left=None, right=None):
        left = left or [unit(3, 9001), unit(7, 9002)]
        right = right or [unit(4, hp=enemy_hp)]
        with patch.object(g, 'build_player_units', side_effect=lambda *a, **kw: copy.deepcopy(left)), \
                patch.object(g, '_noend_fuben_squad', side_effect=lambda *a: copy.deepcopy(right)):
            body = self.call('Fight', g.pb_varint_field(1, stage))
        return body, mf._wire2(body, 6)

    @staticmethod
    def actions(report):
        return [(mf._wire0(round_, 1), action)
                for round_ in mf._repeated_messages(report, 4)
                for action in mf._repeated_messages(round_, 2)]

    def query_report(self, report_id):
        return g.handle_battle_queryreport(g.pb_varint_field(1, report_id), self.player)

    def test_fight_query_report_is_identical_and_does_not_resettle(self):
        self.equip_puppet()
        body, report = self.fight()
        report_id = mf._wire0(body, 5)
        state = self.row()
        messages = self.query_report(report_id)
        self.assertEqual(messages[0][0], 'PVZ.Cmd.Cmd_Battle_QueryReport_SC')
        self.assertEqual(mf._wire2(messages[0][1], 1), report)
        self.assertEqual(self.row(), state)
        self.assertEqual(self.row()['report_id'], report_id)
        self.assertEqual(mf._wire0(report, 5), 26)
        self.assertEqual((mf._wire0(mf._wire2(report, 2), 1),
                          mf._wire0(mf._wire2(report, 3), 1)), (43, 44))

    def test_two_fights_in_the_same_second_have_distinct_replay_ids(self):
        self.equip_puppet()
        with patch.object(g.time, 'time', return_value=1791478800):
            first, report1 = self.fight()
            second, report2 = self.fight()
        id1, id2 = mf._wire0(first, 5), mf._wire0(second, 5)
        self.assertNotEqual(id1, id2)
        self.assertEqual(mf._wire2(self.query_report(id1)[0][1], 1), report1)
        self.assertEqual(mf._wire2(self.query_report(id2)[0][1], 1), report2)

    def test_pve_captain_uses_adventure_rule_after_initial_puppet_setup(self):
        self.equip_puppet()
        left = [unit(3, 9001, leader=True), unit(7, 9002)]
        right = [unit(4, hp=10000, leader=True)]
        _, report = self.fight(left=left, right=right)
        rounds = mf._repeated_messages(report, 4)
        self.assertEqual(mf._wire0(rounds[0], 1), 0)
        self.assertEqual(mf._repeated_messages(rounds[0], 2), [])
        zero = [(n, mf._wire0(mf._wire2(a, 1), 1)) for n, a in self.actions(report) if n == 0]
        self.assertEqual(zero, [(0, 3)])
        puppets = [(n, mf._wire0(mf._wire2(a, 6), 1))
                   for n, a in self.actions(report) if mf._wire2(a, 6)]
        self.assertEqual(puppets, [(1, 101)])
        self.assertEqual(mf._wire0(report, 1), 0)

    def test_sparse_puppet_still_changes_defeat_into_victory(self):
        _, defeat = self.fight()
        self.assertEqual(mf._wire0(defeat, 1), 1)
        self.equip_puppet(slot=3)
        _, victory = self.fight()
        self.assertEqual(mf._wire0(victory, 1), 0)
        acts = [(n, mf._wire0(mf._wire2(a, 6), 1))
                for n, a in self.actions(victory) if mf._wire2(a, 6)]
        self.assertEqual(acts, [(3, 105)])

    def test_saved_captain_sync_preview_and_actual_units_are_consistent(self):
        store.set_formation_leader(self.uin, 1, 9001)
        response = self.set_formation({3: 9001, 13: 9002}, 9002)
        formation = mf._wire2(response[0][1], 1)
        self.assertEqual(mf._wire0(formation, 3), 9002)
        self.assertEqual(store.get_formation_leader(self.uin, 1), 9001)
        self.assertEqual(g._noend_fuben_placements(self.uin), {3: 9001, 13: 9002})
        target = g.pb_varint_field(1, 26) + g.pb_varint_field(22, 1) + g.pb_varint_field(23, 1)
        preview = g.handle_battle_getfightinfo(g.pb_bytes(1, target), self.player)
        self.assertEqual(mf._wire0(mf._wire2(preview, 1), 3), 9002)
        self.assertEqual(mf._repeated_messages(preview, 6), [])
        actual_builder = g.build_player_units
        captured = []

        def build(*args, **kwargs):
            units = actual_builder(*args, **kwargs)
            captured.extend((u['uid'], u['is_leader']) for u in units)
            return units

        with patch.object(g, 'build_player_units', side_effect=build), \
                patch.object(g, '_noend_fuben_squad', return_value=[unit(4, hp=100)]):
            self.call('Fight', g.pb_varint_field(1, 1))
        self.assertEqual(captured, [(9001, False), (9002, True)])

    def test_subset_formation_preserves_prepared_roster_and_undeployed_health(self):
        self.call('SetPreCard', self.pre_cards(9001, 9002, 9003))
        with mf._db() as con:
            con.execute('UPDATE local_noend_fuben SET hp_ratios=? WHERE uin=?',
                        (json.dumps({'9001': .25, '9002': 0, '9003': .5}), self.uin))
        self.set_formation({9: 9001, 17: 9002}, 9001)
        self.assertEqual(self.row()['pre_card'], '9001,9002,9003')
        self.assertEqual(g._noend_fuben_placements(self.uin), {9: 9001})
        self.equip_puppet()
        body, report = self.fight(left=[unit(9, 9001)])
        before = mf._repeated_messages(mf._wire2(report, 2), 2)
        self.assertEqual([(mf._wire0(x, 1), mf._wire0(x, 3)) for x in before],
                         [(9, 250000)])
        ratios = json.loads(self.row()['hp_ratios'])
        self.assertEqual(ratios['9002'], 0)
        self.assertEqual(ratios['9003'], .5)
        confirm = self.call('ConfirmFight', g.pb_varint_field(1, 1))
        self.assertEqual([mf._wire0(x, 1) for x in mf._repeated_messages(confirm, 2)],
                         [9001, 9002, 9003])
        self.assertEqual((mf._wire0(confirm, 1), self.row()['has_pass_next_stage']), (1, 0))
        self.set_formation({13: 9003}, 9003)
        _, restored = self.fight(stage=2, left=[unit(13, 9003)])
        before = mf._repeated_messages(mf._wire2(restored, 2), 2)
        self.assertEqual([(mf._wire0(x, 1), mf._wire0(x, 3)) for x in before], [(13, 500000)])

    def test_prepared_roster_matches_original_sixteen_card_limit(self):
        uids = list(range(9010, 9026))
        for uid in range(9010, 9027):
            store.add_card(self.uin, uid, 1101, 100)
        response = self.call('SetPreCard', self.pre_cards(*uids))
        self.assertEqual(mf._repeated_int64(response, 1), uids)
        self.assertEqual(len(mf._noend_cards(self.row())), 16)
        self.assertEqual(len(g._noend_fuben_placements(self.uin)), 6)
        self.set_formation({17: uids[-1]}, uids[-1])
        self.assertEqual(g._noend_fuben_placements(self.uin), {17: uids[-1]})
        self.assertEqual(len(mf._noend_cards(self.row())), 16)
        old = self.row()
        self.call('SetPreCard', self.pre_cards(*uids, 9026))
        self.assertEqual(self.row(), old)

    def test_explicit_empty_formation_is_synced_and_cannot_start_or_settle(self):
        old = self.row()
        response = self.set_formation({}, 0)
        formation = mf._wire2(response[0][1], 1)
        self.assertEqual(mf._repeated_messages(formation, 1), [])
        self.assertEqual(mf._wire0(formation, 3), 0)
        self.assertEqual(g._noend_fuben_placements(self.uin), {})
        self.assertEqual(self.row()['pre_card'], old['pre_card'])
        state = self.row()
        result = self.call('Fight', g.pb_varint_field(1, 1))
        self.assertEqual(result[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertEqual(self.row(), state)
        target = g.pb_varint_field(1, 26) + g.pb_varint_field(22, 1) + g.pb_varint_field(23, 1)
        generic = g.handle_battle_fight(g.pb_bytes(1, target), self.player)
        self.assertEqual(generic[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertEqual(self.row(), state)

    def test_unprepared_owned_card_and_duplicate_slot_do_not_change_the_lineup(self):
        old = self.row()
        self.set_formation({3: 9001, 7: 9003}, 9003)
        self.assertEqual(self.row(), old)
        duplicate = b''.join(g.pb_bytes(1, g.pb_varint_field(1, 3) + g.pb_int64_field(2, uid))
                             for uid in (9001, 9002))
        self.call('SetFormation', g.pb_bytes(1, duplicate + g.pb_int64_field(3, 9002)))
        self.assertEqual(self.row(), old)

    def test_win_confirm_and_loss_exit_keep_original_cursor_and_health(self):
        self.equip_puppet()
        body, report = self.fight()
        self.assertEqual((mf._wire0(body, 1), mf._wire0(body, 3)), (0, 1))
        confirm = self.call('ConfirmFight', g.pb_varint_field(1, 1))
        self.assertEqual(mf._wire0(confirm, 1), 1)
        state = self.row()
        self.query_report(mf._wire0(body, 5))
        self.assertEqual(self.row(), state)
        g.save_puppet_lineup(self.player, [])
        lost, report = self.fight(stage=2)
        self.assertEqual((mf._wire0(report, 1), mf._wire0(lost, 1), mf._wire0(lost, 3)), (1, 2, 0))
        self.assertEqual(mf._wire0(lost, 4), 1)
        state = self.row()
        self.query_report(mf._wire0(lost, 5))
        self.assertEqual(self.row(), state)
        infos = mf._repeated_messages(self.call('Info'), 11)
        self.assertEqual([mf._wire0(x, 1) for x in infos], [9001, 9002])

    def test_reset_clears_endless_captain_but_preserves_adventure_captain(self):
        store.set_formation_leader(self.uin, 1, 9001)
        self.set_formation({3: 9001, 13: 9002}, 9002)
        self.call('Reset')
        self.assertEqual(self.row()['leader_uid'], 0)
        self.assertEqual(self.row()['pre_card'], '')
        self.assertEqual(store.get_formation_leader(self.uin, 1), 9001)
        self.call('SetPreCard', self.pre_cards(9003, 9002))
        self.assertEqual(self.row()['leader_uid'], 9003)

    def test_old_database_gets_zero_default_captain_without_changing_roster(self):
        with mf._db() as con:
            original = dict(mf._noend_row(con, self.uin))
            if 'leader_uid' in original:
                con.execute('ALTER TABLE local_noend_fuben DROP COLUMN leader_uid')
        with mf._db() as con:
            migrated = dict(mf._noend_row(con, self.uin))
        self.assertEqual(migrated['leader_uid'], 0)
        for key in ('pre_card', 'formation_json', 'hp_ratios', 'stage_id'):
            self.assertEqual(migrated[key], original[key])


if __name__ == '__main__':
    unittest.main(verbosity=2)
