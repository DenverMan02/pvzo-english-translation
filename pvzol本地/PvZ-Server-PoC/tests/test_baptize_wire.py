"""Wire-format check for the 洗炼 (baptize) replies: parse the produced bytes with an INDEPENDENT
decoder (no reliance on the server's own writer) and verify every field number the client expects.

    python tests/test_baptize_wire.py        (from the PvZ-Server-PoC folder)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRV_DIR = os.path.dirname(HERE)                    # ..\PvZ-Server-PoC
sys.path.insert(0, SRV_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store                       # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_wire_"), "test.db")
store.init_db()

import pvz_poc_server as srv                       # noqa: E402

FAILED = []


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def read_varint(buf, pos):
    shift, result = 0, 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def parse(data):
    """[(field, wire, value)] — wire 2 keeps the raw chunk, varints come back as ints."""
    pos, out = 0, []
    while pos < len(data):
        key, pos = read_varint(data, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = read_varint(data, pos)
        elif wire == 2:
            ln, pos = read_varint(data, pos)
            value, pos = data[pos:pos + ln], pos + ln
        elif wire == 5:
            value, pos = data[pos:pos + 4], pos + 4
        elif wire == 1:
            value, pos = data[pos:pos + 8], pos + 8
        else:
            raise ValueError("unsupported wire type %d" % wire)
        out.append((field, wire, value))
    return out


def field_map(msg):
    out = {}
    for field, wire, value in msg:
        out.setdefault(field, []).append(value)
    return out


def attr_types(entries):
    """[(type, value)] for a repeated Dto_RefineAttribute list."""
    out = []
    for chunk in entries:
        fm = field_map(parse(chunk))
        out.append((fm.get(1, [None])[0], fm.get(2, [0])[0]))
    return out


def main():
    uin = 700000998
    player, _ = store.get_or_create_player(uin)
    player["plants"] = store.get_player_plants(uin)
    srv.ensure_player_cards(player)
    uid, cid = store.get_cards(uin)[0][:2]
    store.add_item(uin, 2130, 100)
    store.add_item(uin, 2131, 100)
    store.add_item(uin, 38, 500)

    # --- GetRefineInfo_SC -----------------------------------------------------
    msgs = srv.handle_card_getrefineinfo(srv.pb_int64_field(1, uid), player)
    body = next(b for n, b in msgs if n == "PVZ.Cmd.Cmd_Card_GetRefineInfo_SC")
    fm = field_map(parse(body))
    check("GetRefineInfo_SC.cardUId is a varint = the card uid", fm.get(1) == [uid], fm.get(1))
    check("GetRefineInfo_SC carries exactly 4 attributes", len(fm.get(2, [])) == 4)
    check("GetRefineInfo_SC attribute types are 1,2,3,5 (攻击/防御/生命/技力)",
          [t for t, _v in attr_types(fm.get(2, []))] == [1, 2, 3, 5],
          attr_types(fm.get(2, [])))
    check("GetRefineInfo_SC has speed/totalTimes/leftTimes",
          len(fm.get(3, [])) == 1 and len(fm.get(4, [])) == 1
          and fm.get(5) == [srv._baptize_daily_limit()],
          (fm.get(3), fm.get(4), fm.get(5)))

    # --- Refine_SC ------------------------------------------------------------
    msgs = srv.handle_card_refine(srv.pb_int64_field(1, uid) + srv.pb_varint_field(2, 1), player)
    refine = next(b for n, b in msgs if n == "PVZ.Cmd.Cmd_Card_Refine_SC")
    fm = field_map(parse(refine))
    check("Refine_SC.cardUId + mode", fm.get(1) == [uid] and fm.get(2) == [1], (fm.get(1), fm.get(2)))
    check("Refine_SC.attribute is field 3 x4 with types 1,2,3,5",
          [t for t, _v in attr_types(fm.get(3, []))] == [1, 2, 3, 5])
    check("Refine_SC.speed is field 4", len(fm.get(4, [])) == 1, fm.get(4))

    # --- ConfirmRefine_SC -----------------------------------------------------
    msgs = srv.handle_card_confirmrefine(srv.pb_int64_field(1, uid) + srv.pb_bool(2, True), player)
    confirm = next(b for n, b in msgs if n == "PVZ.Cmd.Cmd_Card_ConfirmRefine_SC")
    fm = field_map(parse(confirm))
    check("ConfirmRefine_SC{cardUId, abandon=1}", fm.get(1) == [uid] and fm.get(2) == [1], fm)

    # --- BatchRefine_SC ------------------------------------------------------
    msgs = srv.handle_card_batchrefine(
        srv.pb_int64_field(1, uid) + srv.pb_varint_field(2, 1) + srv.pb_varint_field(3, 4)
        + srv.pb_varint_field(4, 1), player)
    batch = next(b for n, b in msgs if n == "PVZ.Cmd.Cmd_Card_BatchRefine_SC")
    fm = field_map(parse(batch))
    ops = fm.get(3, [])
    check("BatchRefine_SC{cardUId, mode, operation[]}", fm.get(1) == [uid] and fm.get(2) == [1]
          and len(ops) >= 1, (fm.get(1), fm.get(2), len(ops)))
    ok_shape = True
    for op in ops:
        ofm = field_map(parse(op))
        if (ofm.get(1, [None])[0] not in (1, 2)          # result = Accepted/Refused
                or [t for t, _v in attr_types(ofm.get(2, []))] != [1, 2, 3, 5]
                or len(ofm.get(3, [])) != 1):            # speed
            ok_shape = False
    check("each Dto_RefineOperation = {result, attribute x4, speed}", ok_shape)

    print("\n%s (%d failing check(s))"
          % ("WIRE FORMAT OK" if not FAILED else "FAILURES: %s" % FAILED, len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
