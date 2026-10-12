import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import player_store as store
store.DB_PATH = str(Path(tempfile.mkdtemp(prefix='endless_repair_')) / 'test.db')
store.init_db()
candidate = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'pvz_poc_server.py'
spec = importlib.util.spec_from_file_location('endless_candidate', candidate)
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
s._CLIENTDB_PATH = str(ROOT.parent / 'webroot' / 'clientDB.txt')
assert Path(s._CLIENTDB_PATH).is_file(), s._CLIENTDB_PATH
p, _ = store.get_or_create_player(799001234)
p['ticket'] = 1000
def state(): return json.loads(p['noend_td_data'])
def save(d): s._save_json_state(p, 'noend_td_data', d)
def start(level, rain=100000): return s.handle_noendtd_starttdgame(s.pb_varint_field(1, 1) + s.pb_varint_field(3, rain) + s.pb_varint_field(4, level), p)
def report(win=1, repeat=0):
    msgs = s.handle_noendtd_reportresult(s.pb_varint_field(1, 1) + s.pb_varint_field(2, win) + s.pb_varint_field(3, 50) + s.pb_varint_field(5, repeat), p)
    body = next(b for n, b in msgs if n.endswith('ReportResult_SC'))
    return s.parse_varint_fields(body)
s._noendtd_state(p)
assert state()['rain'] == 200
start(1, 100000)
assert state()['rain'] == 200
run = state()['active_run_id']
start(1)
assert state()['active_run_id'] == run and state()['challenge_times'] == 1
assert report()[4] == 300 and state()['rain'] == 500
assert report()[4] == 0 and state()['rain'] == 500
start(2)
assert state()['challenge_times'] == 1
assert report(repeat=1)[4] == 0 and state()['active_level'] == 2
assert report()[4] == 400 and state()['rain'] == 900
start(3)
assert state()['challenge_times'] == 1
assert report()[4] == 500 and state()['rain'] == 1400
start(9)
assert state()['active_level'] == 0
start(4)
assert report(win=0)[4] == 0
assert report()[4] == 0 and state()['rain'] == 1400
assert not state()['continuing']
start(4)
assert state()['challenge_times'] == 2 and p['ticket'] == 900
assert report()[4] == 600
start(5)
assert state()['active_level'] == 5 and state()['challenge_times'] == 2
d = state(); d['challenge_date'] = '20000101'; save(d)
assert report()[4] == 0
assert state()['active_run_id'] == 0 and state()['level'] == 0
assert state()['challenge_times'] == 0 and state()['rain'] == 2000
start(1)
assert state()['challenge_times'] == 1
assert report()[4] == 300
d = state(); d['rain'] = 7; save(d)
s._noendtd_state(p)
assert state()['rain'] == 7
saved, _ = store.get_or_create_player(p['uin'])
assert json.loads(saved['noend_td_data'])['rain'] == 7
start(2, rain=3)
assert state()['rain'] == 3  # Chooser's legitimate local spend is retained.
start(2, rain=0)
assert state()['rain'] == 3  # A retried entry cannot deduct again.
rows = s._read_clientdb_table('tdPlantTypeInfo.txt')
col = rows[0].index('costWater')
plant = next(r for r in rows[1:] if len(r) > col and int(r[col] or 0) > 1)
cost = int(plant[col])
d = state(); d['rain'] = cost + 5; save(d)
s.handle_noendtd_useskill(s.pb_varint_field(2, int(plant[0])), p)
assert state()['rain'] == 5
s.handle_noendtd_useskill(s.pb_varint_field(2, int(plant[0])), p)
assert state()['rain'] == 5  # Insufficient balance does not go negative.
s.handle_noendtd_buyenergybean(b'', p)
assert state()['rain'] == 5  # Bean purchase cannot create rain.
print('PASS: progressive gains, cumulative persistence, 3+ maps/attempt, duplicate and repeat reports, failed runs, paid retry, midnight invalidation, snapshot rejection')

d = state()
d.update(level=11, active_level=12, active_run_id=900, settled_run_id=899,
         challenge_times=1, rain=50, continuing=False)
save(d)
assert report()[4] == 1400
assert state()['level'] == 12 and state()['rain'] == 1450
old_times = state()['challenge_times']
old_strength = state()['strength_point']
s.handle_noendtd_getinfo(b'', p)
assert state()['level'] == 0 and state()['active_level'] == 0
assert not state()['continuing']
assert state()['challenge_times'] == old_times
assert state()['rain'] == 1450 and state()['strength_point'] == old_strength
start(1)
assert state()['active_level'] == 1 and state()['challenge_times'] == 2
print('PASS: final map settlement remains level 12; lobby offers paid map-1 restart, retaining rain/strength/attempts')
