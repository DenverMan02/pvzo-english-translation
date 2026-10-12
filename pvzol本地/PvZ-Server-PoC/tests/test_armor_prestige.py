"""Functional regression checks for 装甲 and 威望, using a throwaway database."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_armor_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700000998
FAILED = []


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def cmd(uid, armor_type=0, third=0, fourth=0):
    data = srv.pb_int64_field(1, uid)
    if armor_type:
        data += srv.pb_varint_field(2, armor_type)
    if third:
        data += srv.pb_varint_field(3, third)
    if fourth:
        data += srv.pb_varint_field(4, fourth)
    return data


def main():
    player, _ = store.get_or_create_player(UIN)
    player["level"] = 70
    store.save_player(UIN, level=70)
    player["plants"] = store.get_player_plants(UIN)
    package = srv.handle_card_getpackage(b"", player)
    uid, cid = store.get_cards(UIN)[0][:2]
    check("card package remains valid", isinstance(package, bytes) and len(package) > 20)
    check("one-time test materials granted", store.get_item_count(UIN, 43) == 5000
          and store.get_item_count(UIN, 98901) == 100)

    body = srv.handle_armor_get_card(cmd(uid), player)
    check("GetCard returns three armour records", isinstance(body, bytes) and len(body) > 20)

    before = store.get_item_count(UIN, 98901)
    msgs = srv.handle_armor_upgrade_star(cmd(uid, 1), player)
    state = store.get_card_armors(UIN, uid)[1]
    check("attack armour reaches star 1", state["star"] == 1 and state["substar"] == 0, state)
    check("star-up consumes its shard recipe", store.get_item_count(UIN, 98901) == before - 10)
    check("star-up returns protocol + item + card sync", len(msgs) == 3,
          [name for name, _body in msgs])

    before = store.get_item_count(UIN, 98904)
    msgs = srv.handle_armor_upgrade_level(cmd(uid, 1, 98904, 5), player)
    state = store.get_card_armors(UIN, uid)[1]
    check("five alloys provide 2500 XP and reach level 2", state["level"] == 2 and state["exp"] == 0,
          state)
    check("alloys were consumed", store.get_item_count(UIN, 98904) == before - 5)

    before = store.get_item_count(UIN, 98001)
    srv.handle_armor_set_gem(cmd(uid, 1, 98001), player)
    check("level-1 attack crystal sockets in hole 1",
          store.get_card_armors(UIN, uid)[1]["gems"].get(1) == 98001)
    check("socketing consumes one crystal", store.get_item_count(UIN, 98001) == before - 1)
    srv.handle_armor_combine_set(cmd(uid, 1, 1), player)
    check("socketed 3-for-1 fusion upgrades the crystal",
          store.get_card_armors(UIN, uid)[1]["gems"].get(1) == 98002)
    srv.handle_armor_unset_gem(cmd(uid, 1, 1), player)
    check("unset returns upgraded crystal to bag",
          1 not in store.get_card_armors(UIN, uid)[1]["gems"]
          and store.get_item_count(UIN, 98002) == 1)

    prestige_before = store.get_item_count(UIN, 43)
    msgs = srv.handle_card_evolve(cmd(uid), player)
    check("prestige evolution reaches level 1", store.get_card_evolve_level(UIN, uid) == 1)
    check("prestige consumes level-1 cost (32)", store.get_item_count(UIN, 43) == prestige_before - 32)
    check("evolution returns card sync, event, bag and prestige sync", len(msgs) == 4,
          [name for name, _body in msgs])
    st = srv.effective_card_stats(player, cid, 1, uid=uid)
    base = srv.card_stats_at_level(cid, 1)
    check("restored systems affect combat stats", st["atk"] > base["atk"] or st["hp"] > base["hp"],
          (base["atk"], st["atk"], base["hp"], st["hp"]))

    print("\n%s (%d failing check(s))" %
          ("ALL CHECKS PASSED" if not FAILED else "FAILURES: %s" % FAILED, len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
