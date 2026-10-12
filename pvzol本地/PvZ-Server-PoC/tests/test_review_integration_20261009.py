"""Independent check that new Endless TD refreshes preserve real saved lawns."""
import atexit
import json
import logging
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store

BOOT = tempfile.TemporaryDirectory(prefix='pvz_review_integration_boot_')
store.set_db_path(str(Path(BOOT.name) / 'boot.db'))
store.init_db()
atexit.register(BOOT.cleanup)
atexit.register(store.close_thread_db)
with patch.object(logging, 'FileHandler', return_value=logging.NullHandler()), patch.object(logging, 'basicConfig'):
    import pvz_poc_server as g


class SavedLawnRefreshIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pvz_saved_lawn_refresh_')
        self.addCleanup(self.tmp.cleanup)
        old_db = store.DB_PATH
        self.addCleanup(lambda: store.set_db_path(old_db))
        store.set_db_path(str(Path(self.tmp.name) / 'players.db'))
        store.init_db()
        self.addCleanup(store.close_thread_db)
        self.p, _ = store.get_or_create_player(99001009)
        self.p['plants'] = [1100, 1200, 1300, 1500]
        self.day_patch = patch.object(g.time, 'strftime', return_value='20261009')
        self.day_patch.start()
        self.addCleanup(self.day_patch.stop)
        state = g._noendtd_state(self.p)
        state.update(level=1, wave=3, score=1234, rain=188, strength_point=500)
        g._save_json_state(self.p, 'noend_td_data', state)
        self.route('GetInfo')  # Normal lobby entry initializes feature-state tables.
        # ByteArray save(): version, sun, bean, plant-count, tile-count.
        # An empty but valid lawn exercises the real SaveRecord validation.
        self.clear = struct.pack('>IIBBB', 1, 75, 2, 0, 0)
        self.packed = zlib.compress(self.clear)
        saved = self.route('SaveRecord', g.pb_bytes(1, self.packed))
        self.assertEqual(saved, [('PVZ.Cmd.Cmd_NoEndTD_SaveRecord_SC', b'')])
        self.original_record = self.record(self.route('GetInfo'))
        self.assertEqual(g.parse_varint_fields(self.original_record), {1: 1, 2: 188, 3: 1234})

    def route(self, name, body=b''):
        return g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_NoEndTD_' + name + '_CS'](body, self.p)

    def record(self, messages):
        bodies = [body for name, body in messages if name == 'PVZ.Cmd.Cmd_NoEndTD_GetInfo_SC']
        self.assertEqual(len(bodies), 1)
        records = g.parse_repeated_submessages(bodies[0], 10)
        self.assertEqual(len(records), 1, 'refresh would clear the client saved-lawn record')
        record = records[0]
        packed = g.parse_repeated_submessages(record, 4)
        self.assertEqual(packed, [self.packed])
        self.assertEqual(zlib.decompress(packed[0]), self.clear)
        return record

    def test_upgrade_refresh_keeps_lawn_for_live_route_and_direct_entry(self):
        request = g.pb_varint_field(1, 1200)
        self.assertEqual(self.record(self.route('PlantUpgrade', request)), self.original_record)
        self.assertEqual(self.record(g.handle_noendtd_upgrade(request, self.p)), self.original_record)
        self.assertEqual(json.loads(self.p['noend_td_data'])['plant_strength']['1200'], 3)
        resumed = self.route('ReadRecord')
        mode = g.parse_varint_fields(g.parse_repeated_submessages(resumed[0][1], 1)[0])
        self.assertEqual((mode[2], mode[5], mode[7]), (1, 1234, 188))

    def test_duplicate_week_reward_refresh_keeps_lawn_and_never_pays_twice(self):
        self.route('TakeWeekReward')
        balances = (self.p['coin'], self.p['present_ticket'])
        self.assertEqual(self.record(self.route('TakeWeekReward')), self.original_record)
        self.assertEqual(self.record(g.handle_noendtd_takeweekreward(b'', self.p)), self.original_record)
        self.assertEqual((self.p['coin'], self.p['present_ticket']), balances)

    def test_rejected_upgrade_refresh_keeps_saved_lawn(self):
        before = json.loads(self.p['noend_td_data'])
        denied = self.route('PlantUpgrade', g.pb_varint_field(1, 1300))
        self.assertFalse(any(name.endswith('PlantUpgrade_SC') for name, _ in denied))
        self.assertEqual(self.record(denied), self.original_record)
        self.assertEqual(json.loads(self.p['noend_td_data']), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
