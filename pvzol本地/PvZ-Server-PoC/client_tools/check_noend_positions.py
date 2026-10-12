"""Dump the NoEndFuben fight report's left/right posIndexes to prove the two sides
never collide and that plants land on the odd _FORMATION_POS slots."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import missing_features_local as mfl  # noqa: E402
import player_store  # noqa: E402
import pvz_poc_server  # noqa: E402


def fields(data):
    out, i = {}, 0
    while i < len(data):
        key, i = mfl._uvarint(data, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            v, i = mfl._uvarint(data, i); out.setdefault(num, []).append(("v", v))
        elif wire == 2:
            ln, i = mfl._uvarint(data, i); out.setdefault(num, []).append(("m", data[i:i + ln])); i += ln
        elif wire == 5:
            i += 4
        elif wire == 1:
            i += 8
        else:
            break
    return out


def uvarint(data, i):
    shift = result = 0
    while i < len(data):
        b = data[i]; result |= (b & 0x7F) << shift; i += 1
        if not (b & 0x80):
            return result, i
        shift += 7
    return result, i


mfl._uvarint = uvarint

tmp = tempfile.TemporaryDirectory()
db = Path(tmp.name) / "players.db"
player_store.DB_PATH = str(db)
pvz_poc_server.store.DB_PATH = str(db)
player_store.init_db()
pvz_poc_server.store.init_db()
pvz_poc_server.load_battle_cards()

player = pvz_poc_server.store.get_or_create_player(900001)[0]
uin = player["uin"]
cids = list(pvz_poc_server._BATTLE_CARDS.keys())
con = sqlite3.connect(str(db))
for k in range(6):
    con.execute("INSERT INTO cards(uin,uid,card_id,level,exp) VALUES (?,?,?,1,0)",
                (uin, 300 + k, cids[k % len(cids)]))
con.commit(); con.close()

mfl.register({
    "db_path": str(db),
    "log": lambda *a: None,
    "read_clientdb_table": lambda name: [],
    "grant_reward": lambda p, items: [],
    "build_formation_info": lambda p, t, pl: mfl._v(2, t),
    "default_formation_placements": lambda p: {1: 11},
    "ensure_player_cards": lambda p: None,
    "set_formation": lambda *a: None,
    "run_fight": pvz_poc_server.handle_battle_fight,
})

print("server _FORMATION_POS =", pvz_poc_server._FORMATION_POS)
print("server _ENEMY_POS     =", pvz_poc_server._ENEMY_POS)
print()

# Use the real handler to persist pre_card (creates local_noend_fuben).
handlers = mfl.register({
    "db_path": str(db), "log": lambda *a: None, "read_clientdb_table": lambda name: [],
    "grant_reward": lambda p, items: [], "build_formation_info": lambda p, t, pl: mfl._v(2, t),
    "default_formation_placements": lambda p: {1: 11}, "ensure_player_cards": lambda p: None,
    "set_formation": lambda *a: None, "run_fight": pvz_poc_server.handle_battle_fight,
})
for n, floor in ((6, 3), (6, 7), (6, 30), (2, 5)):
    uids = [300 + k for k in range(n)]
    handlers["PVZ.Cmd.Cmd_NoEndFuben_SetPreCard_CS"](
        b"".join(mfl._i64(1, u) for u in uids), player)
    sc = handlers["PVZ.Cmd.Cmd_NoEndFuben_Fight_CS"](mfl._v(1, floor), player)
    report = mfl._wire2(sc, 6)
    rf = fields(report)
    ls, rs = fields(rf[2][0][1]), fields(rf[3][0][1])

    def dump(side):
        rows = []
        for card in side.get(2, []):
            cf = fields(card[1])
            rows.append((cf[1][0][1], cf[2][0][1], cf[4][0][1]))   # pos, cardId, rage(level-ish)
        return rows

    lp, rp = dump(ls), dump(rs)
    left_pos = [r[0] for r in lp]
    right_pos = [r[0] for r in rp]
    overlap = sorted(set(left_pos) & set(right_pos))
    even_left = [p for p in left_pos if p % 2 == 0]
    print(f"floor={floor:>3} pre_card={len(uids)} fightType={rf[5][0][1]}")
    print(f"   left  pos/cardId = {lp}")
    print(f"   right pos/cardId = {rp}")
    print(f"   overlap={overlap}  even-left(会被画到敌方半场)={even_left}  "
          f"left_ok={all(p in pvz_poc_server._FORMATION_POS for p in left_pos)}  "
          f"right_ok={all(p in pvz_poc_server._ENEMY_POS for p in right_pos)}")
    print()
