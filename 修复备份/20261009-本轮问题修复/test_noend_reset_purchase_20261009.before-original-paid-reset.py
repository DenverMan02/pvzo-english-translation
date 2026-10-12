"""Daily free/paid NoEndFuben resets, exclusively using disposable databases."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store
import missing_features_local as mf


def moment(year, month, day, hour=12, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second,
                    tzinfo=timezone(timedelta(hours=8))).timestamp()


class NoEndResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_db, self.old_api, self.old_mf_db = store.DB_PATH, dict(mf._API), mf._DB_PATH
        self.addCleanup(self.restore)
        store.set_db_path(str(Path(self.temp.name) / 'players.db'))
        store.init_db()
        self.player, _ = store.get_or_create_player(8091009)
        self.player['ticket'] = 100
        store.save_player(self.player['uin'], ticket=100)
        self.routes = mf.register({'db_path': store.DB_PATH})
        self.clock = patch.object(mf.time, 'time', return_value=moment(2026, 10, 9))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.call('Info')

    def restore(self):
        store.close_thread_db()
        store.set_db_path(self.old_db)
        mf.register(self.old_api, db_path=self.old_mf_db)

    def call(self, name):
        return self.routes['PVZ.Cmd.Cmd_NoEndFuben_' + name + '_CS'](b'', self.player)

    def row(self):
        with mf._db() as con:
            return dict(mf._noend_row(con, self.player['uin']))

    def update(self, **values):
        with mf._db() as con:
            columns = ','.join(key + '=?' for key in values)
            con.execute('UPDATE local_noend_fuben SET ' + columns + ' WHERE uin=?',
                        (*values.values(), self.player['uin']))

    def prepare(self):
        self.update(stage_id=12, lose_times=2, pre_card='91,92', leader_uid=91,
                    formation_json='{"3":91,"7":92}', formation_set=1,
                    hp_ratios='{"91":0.25,"92":0.5}', history_max_stage_id=50,
                    best_stage_id=45, last_sweep_stage=30, report_id=101,
                    has_pass_next_stage=1)

    def ticket(self):
        with mf._db() as con:
            return con.execute('SELECT ticket FROM players WHERE uin=?',
                               (self.player['uin'],)).fetchone()[0]

    def test_legacy_lifetime_counter_is_restored_once_without_losing_plants(self):
        self.prepare()
        self.update(reset_times=99, reset_date='')
        old = self.row()
        info = self.call('Info')
        now = self.row()
        self.assertEqual(mf._wire0(info, 13), 0)
        self.assertEqual(now['reset_date'], '20261009')
        for key in ('stage_id', 'lose_times', 'pre_card', 'formation_json', 'leader_uid',
                    'formation_set', 'hp_ratios', 'history_max_stage_id',
                    'best_stage_id', 'last_sweep_stage', 'report_id'):
            self.assertEqual(now[key], old[key], key)
        self.call('Reset')
        self.call('Info')
        self.assertEqual(self.row()['reset_times'], 1)

    def test_three_free_resets_then_paid_reset_syncs_balance_and_keeps_history(self):
        for used in (1, 2, 3):
            self.prepare()
            body = self.call('Reset')
            self.assertIsInstance(body, bytes)
            self.assertEqual(mf._wire0(body, 1), used)
            self.assertEqual(self.ticket(), 100)
        self.prepare()
        reply = self.call('Reset')
        self.assertEqual(reply, [('PVZ.Cmd.Cmd_NoEndFuben_Reset_SC', mf._v(1, 4)),
                                 ('PVZ.Cmd.Cmd_SyncTicket_SC', mf._v(1, 80))])
        self.assertEqual((self.ticket(), self.player['ticket']), (80, 80))
        state = self.row()
        for key in ('stage_id', 'lose_times', 'leader_uid', 'formation_set',
                    'has_pass_next_stage', 'report_id'):
            self.assertEqual(state[key], 0, key)
        self.assertEqual(state['pre_card'], '')
        self.assertEqual(state['formation_json'], '{}')
        self.assertEqual(state['hp_ratios'], '{}')
        self.assertEqual((state['history_max_stage_id'], state['best_stage_id'],
                          state['last_sweep_stage']), (50, 45, 30))
        self.prepare()
        self.call('Reset')
        self.assertEqual((self.ticket(), self.row()['reset_times']), (60, 5))

    def test_next_day_restores_free_budget_without_clearing_current_run(self):
        self.prepare()
        self.update(reset_times=5)
        old = self.row()
        with patch.object(mf.time, 'time', return_value=moment(2026, 10, 10, 0)):
            info = self.call('Info')
        self.assertEqual(mf._wire0(info, 13), 0)
        state = self.row()
        self.assertEqual(state['reset_date'], '20261010')
        for key in ('stage_id', 'pre_card', 'formation_json', 'leader_uid', 'hp_ratios',
                    'best_stage_id', 'history_max_stage_id', 'last_sweep_stage'):
            self.assertEqual(state[key], old[key], key)

    def test_reset_crossing_midnight_refreshes_before_deciding_purchase_cost(self):
        self.prepare()
        self.update(reset_times=3)
        with patch.object(mf.time, 'time', return_value=moment(2026, 10, 10, 0)):
            self.assertEqual(mf._wire0(self.call('Reset'), 1), 1)
        self.assertEqual((self.ticket(), self.row()['reset_date']), (100, '20261010'))

    def test_daily_boundary_is_china_midnight_independent_of_host_timezone(self):
        self.update(reset_times=3)
        with patch.object(mf.time, 'time', return_value=moment(2026, 10, 9, 23, 59, 59)):
            self.call('Info')
            self.assertEqual(self.row()['reset_times'], 3)
        with patch.object(mf.time, 'time', return_value=moment(2026, 10, 10, 0)):
            self.call('Info')
            self.assertEqual(self.row()['reset_times'], 0)

    def test_insufficient_ticket_preserves_entire_run_and_counter(self):
        self.prepare()
        self.update(reset_times=3)
        store.save_player(self.player['uin'], ticket=19)
        self.player['ticket'] = 19
        before = self.row()
        reply = self.call('Reset')
        self.assertEqual(reply, [('PVZ.Cmd.Cmd_LogicError_SC', mf._v(1, 208))])
        self.assertEqual(self.row(), before)
        self.assertEqual((self.ticket(), self.player['ticket']), (19, 19))

    def test_progress_failure_rolls_back_deduction_and_in_memory_balance(self):
        self.prepare()
        self.update(reset_times=3)
        before = self.row()
        original = mf._noend_row
        calls = []
        def failure(con, uin):
            calls.append(uin)
            if len(calls) == 2:
                raise RuntimeError('test storage failure after debit/reset SQL')
            return original(con, uin)
        with patch.object(mf, '_noend_row', side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, 'storage failure'):
                self.call('Reset')
        self.assertEqual(self.row(), before)
        self.assertEqual((self.ticket(), self.player['ticket']), (100, 100))

    def test_custom_config_budget_and_price_match_server_advertised_values(self):
        values = {'iNoEndFubenResetTimes': '1', 'iNoEndFubenResetGoldTicket': '7'}
        mf._API['server_config_value'] = lambda key, player: values[key]
        self.prepare()
        self.call('Reset')
        self.assertEqual(self.ticket(), 100)
        self.prepare()
        reply = self.call('Reset')
        self.assertEqual(self.ticket(), 93)
        self.assertEqual(reply[-1], ('PVZ.Cmd.Cmd_SyncTicket_SC', mf._v(1, 93)))

    def test_stale_in_memory_balance_cannot_override_newer_saved_balance(self):
        self.prepare()
        self.update(reset_times=3)
        self.player['ticket'] = 999
        store.save_player(self.player['uin'], ticket=20)
        self.call('Reset')
        self.assertEqual((self.ticket(), self.player['ticket']), (0, 0))

    def test_duplicate_paid_reset_resends_balance_without_charging_or_counting_twice(self):
        self.prepare()
        self.update(reset_times=3)
        first = self.call('Reset')
        state = self.row()
        store.close_thread_db()
        self.assertEqual(self.call('Reset'), first)
        self.assertEqual(self.row(), state)
        self.assertEqual((self.ticket(), self.player['ticket']), (80, 80))

    def test_duplicate_free_reset_does_not_consume_another_free_attempt(self):
        self.prepare()
        first = self.call('Reset')
        self.assertEqual(first, self.call('Reset'))
        self.assertEqual((self.row()['reset_times'], self.ticket()), (1, 100))

    def test_empty_fresh_run_does_not_waste_reset_budget(self):
        self.assertEqual(mf._wire0(self.call('Reset'), 1), 0)
        self.assertEqual((self.row()['reset_times'], self.ticket()), (0, 100))

    def test_old_schema_adds_date_without_resetting_saved_progress(self):
        self.prepare()
        with mf._db() as con:
            con.execute('ALTER TABLE local_noend_fuben DROP COLUMN reset_date')
        self.call('Info')
        self.assertEqual(self.row()['reset_date'], '20261009')
        self.assertEqual((self.row()['stage_id'], self.row()['pre_card']), (12, '91,92'))

    def test_week_rollover_preserves_all_time_history_and_restores_budget(self):
        self.prepare()
        self.update(reset_times=5)
        with patch.object(mf.time, 'time', return_value=moment(2026, 10, 12, 0)):
            self.call('Info')
        state = self.row()
        self.assertEqual((state['reset_times'], state['stage_id'], state['pre_card']), (0, 0, ''))
        self.assertEqual(state['history_max_stage_id'], 50)


if __name__ == '__main__':
    unittest.main(verbosity=2)
