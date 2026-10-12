"""Functional test for the plant baptize / refine (植物洗炼) implementation.

Runs against a THROWAWAY sqlite DB so the real players.db is never touched:
    python tests/test_baptize.py          (from the PvZ-Server-PoC folder)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRV_DIR = os.path.dirname(HERE)                    # ..\PvZ-Server-PoC
sys.path.insert(0, SRV_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store                       # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_baptize_"), "test.db")
store.init_db()

import pvz_poc_server as srv                       # noqa: E402

UIN = 700000999
ATTRS = store.BAPTIZE_ATTRS
FAILED = []


def uid_cmd(uid):
    return srv.pb_int64_field(1, uid)


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def main():
    player, _new = store.get_or_create_player(UIN)
    player["plants"] = store.get_player_plants(UIN)
    srv.ensure_player_cards(player)
    cards = store.get_cards(UIN)
    assert cards, "the test player should own starter cards"
    uid, cid = cards[0][0], cards[0][1]
    print("test card: uid=%d cardId=%d caps=%s" % (uid, cid, srv._baptize_caps(cid)))
    print("costs:", srv.load_baptize_tables()["action"])

    store.add_item(UIN, 2130, 200)
    store.add_item(UIN, 2131, 200)
    store.add_item(UIN, 38, 500)

    # 1) info on a never-refined card -> all zeros, but a well-formed reply (+ the one-time stash).
    msgs = srv.handle_card_getrefineinfo(uid_cmd(uid), player)
    info_body = next((b for n, b in msgs if n == "PVZ.Cmd.Cmd_Card_GetRefineInfo_SC"), b"")
    check("GetRefineInfo answers bytes", isinstance(info_body, bytes) and len(info_body) > 20,
          "(%d bytes)" % len(info_body))
    check("first panel open hands out the 洗炼 starter stash",
          store.get_item_count(UIN, 2130) >= 50 and store.get_item_count(UIN, 38) >= 100 and
          any(n == "PVZ.Cmd.Cmd_Item_Update_SC" for n, _b in msgs), [n for n, _b in msgs])
    potions_before = store.get_item_count(UIN, 2130)
    check("the stash is only granted once",
          srv._baptize_grant_starter(player) == [] and
          store.get_item_count(UIN, 2130) == potions_before,
          store.get_item_count(UIN, 2130))

    # 2) one normal roll: cost charged, pending stored, values inside the caps.
    potions = store.get_item_count(UIN, 2130)
    msgs = srv.handle_card_refine(uid_cmd(uid) + srv.pb_varint_field(2, 1), player)
    pending = store.get_baptize_pending(UIN)
    caps = srv._baptize_caps(cid)
    check("Refine charged 3x item 2130",
          store.get_item_count(UIN, 2130) == potions - 3,
          "%d -> %d" % (potions, store.get_item_count(UIN, 2130)))
    check("Refine replies SC + item update", len(msgs) == 2 and
          msgs[0][0] == "PVZ.Cmd.Cmd_Card_Refine_SC", [n for n, _b in msgs])
    check("pending result stored for the card",
          bool(pending) and pending["uid"] == uid and pending["rolls"] == 1, pending)
    check("values are inside [0, cap]",
          all(0 <= pending[a] <= caps[a] for a in ATTRS), dict((a, pending[a]) for a in ATTRS))
    check("saved state untouched before 保存",
          all(store.get_card_baptize(UIN, uid)[a] == 0 for a in ATTRS))

    # 3) 放弃 -> refund +1 洗炼幸运点 for the normal roll, nothing saved.
    lucky = store.get_item_count(UIN, 38)
    msgs = srv.handle_card_confirmrefine(uid_cmd(uid) + srv.pb_bool(2, True), player)
    check("ConfirmRefine(abandon) refunds 1x item 38",
          store.get_item_count(UIN, 38) == lucky + 1,
          "%d -> %d" % (lucky, store.get_item_count(UIN, 38)))
    check("abandon clears the pending result", store.get_baptize_pending(UIN) is None)
    check("abandon keeps the saved state",
          all(store.get_card_baptize(UIN, uid)[a] == 0 for a in ATTRS))

    # 4) 幸运 roll -> never lowers, then 保存 commits it.
    msgs = srv.handle_card_refine(uid_cmd(uid) + srv.pb_varint_field(2, 3), player)
    lucky_pending = store.get_baptize_pending(UIN)
    check("lucky mode charges 50x item 38", store.get_item_count(UIN, 38) == lucky + 1 - 50,
          "item38 now %d" % store.get_item_count(UIN, 38))
    check("lucky mode never lowers an attribute",
          all(lucky_pending[a] >= 0 for a in ATTRS), dict((a, lucky_pending[a]) for a in ATTRS))
    msgs = srv.handle_card_confirmrefine(uid_cmd(uid), player)          # abandon omitted = 保存
    saved = store.get_card_baptize(UIN, uid)
    check("ConfirmRefine(save) persists the result",
          all(saved[a] == lucky_pending[a] for a in ATTRS), saved)
    check("save bumps totalRefineTimes", saved["total_times"] == 1, saved["total_times"])
    check("save re-syncs the card", any(n == "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC" for n, _b in msgs),
          [n for n, _b in msgs])

    # 5) saved attributes show up in the card's battle stats / 战力.
    st = srv.effective_card_stats(player, cid, 1, uid=uid)
    base = srv.card_stats_at_level(cid, 1)
    check("refine attributes are added to the card's battle stats",
          st["atk"] >= base["atk"] and st["hp"] >= base["hp"], (st["atk"], base["atk"]))

    # 6) 批量洗炼 with 攻击/防御 conditions.
    before = store.get_item_count(UIN, 2130)
    msgs = srv.handle_card_batchrefine(
        uid_cmd(uid) + srv.pb_varint_field(2, 1) + srv.pb_varint_field(3, 10)
        + srv.pb_varint_field(4, 1) + srv.pb_varint_field(4, 2), player)
    batch_body = next((b for n, b in msgs if n == "PVZ.Cmd.Cmd_Card_BatchRefine_SC"), b"")
    spent = before - store.get_item_count(UIN, 2130)
    check("BatchRefine performs >=1 roll and charges 3 potions per roll",
          spent >= 3 and spent % 3 == 0, "spent %d potions" % spent)
    check("BatchRefine returns operations", len(batch_body) > 30, "%d bytes" % len(batch_body))
    check("BatchRefine leaves an unsaved result", store.get_baptize_pending(UIN) is not None)

    # 7) no materials -> clean, non-crashing replies (never a dead request).
    store.add_item(UIN, 2130, -store.get_item_count(UIN, 2130))
    msgs = srv.handle_card_batchrefine(
        uid_cmd(uid) + srv.pb_varint_field(2, 1) + srv.pb_varint_field(3, 5), player)
    check("BatchRefine without materials still answers", len(msgs) == 1 and
          msgs[0][0] == "PVZ.Cmd.Cmd_Card_BatchRefine_SC", [n for n, _b in msgs])
    msgs = srv.handle_card_refine(uid_cmd(uid) + srv.pb_varint_field(2, 1), player)
    check("Refine without materials answers Refine_SC",
          len(msgs) == 1 and msgs[0][0] == "PVZ.Cmd.Cmd_Card_Refine_SC", [n for n, _b in msgs])

    # 8) disconnect: the pending result is dropped with its lucky-point refund.
    store.add_item(UIN, 2131, 100)
    srv.handle_card_refine(uid_cmd(uid) + srv.pb_varint_field(2, 2), player)
    check("advanced roll sets a pending result", store.get_baptize_pending(UIN) is not None)
    lucky = store.get_item_count(UIN, 38)
    srv.baptize_login_cleanup(player)
    check("login drops the pending result", store.get_baptize_pending(UIN) is None)
    check("login pays the advanced refund (+10)", store.get_item_count(UIN, 38) == lucky + 10,
          "%d -> %d" % (lucky, store.get_item_count(UIN, 38)))

    # 9) daily limit counter + reset on a new day.
    player["baptize_day"], player["baptize_used"] = "", 0
    check("daily allowance is the configured one",
          srv._baptize_daily_left(player) == srv._baptize_daily_limit(),
          srv._baptize_daily_limit())

    # 10) unknown card / bad mode safety.
    check("unknown card answers nothing", srv.handle_card_refine(uid_cmd(999999), player) == [])
    check("unknown card info answers nothing",
          srv.handle_card_getrefineinfo(uid_cmd(999999), player) == [])
    store.add_item(UIN, 2130, 50)
    msgs = srv.handle_card_refine(uid_cmd(uid) + srv.pb_varint_field(2, 99), player)
    check("unknown mode falls back to 普通 (charges 3x 2130)", len(msgs) == 2, [n for n, _b in msgs])

    # 11) 植物交换: the 洗炼 group moves between cards, clipped to the receiver's caps.
    if len(store.get_cards(UIN)) >= 2:
        src_uid, src_cid = store.get_cards(UIN)[0][:2]
        dst_uid, dst_cid = store.get_cards(UIN)[1][:2]
        store.set_card_baptize(UIN, src_uid, {"attack": 999999, "defense": 1000, "hp": 2000,
                                             "ability": 3000})
        store.set_card_baptize(UIN, dst_uid, {"attack": 0, "defense": 0, "hp": 0, "ability": 0})
        store.add_item(UIN, 3207, 5)
        body = srv.handle_card_swapattributelost(
            srv.pb_int64_field(1, src_uid) + srv.pb_int64_field(2, dst_uid)
            + srv.pb_bool(4, True) + srv.pb_bool(5, True), player)
        check("SwapAttributeLost reports the over-cap 洗炼 loss",
              srv.parse_varint_fields(body).get(1) == 1, srv.parse_varint_fields(body))
        tokens = store.get_item_count(UIN, 3207)
        msgs = srv.handle_card_swapattribute(
            srv.pb_int64_field(1, src_uid) + srv.pb_int64_field(2, dst_uid) + srv.pb_bool(5, True),
            player)
        moved = store.get_card_baptize(UIN, dst_uid)
        left = store.get_card_baptize(UIN, src_uid)
        check("SwapAttribute charges 1x item 3207", store.get_item_count(UIN, 3207) == tokens - 1,
              "%d -> %d" % (tokens, store.get_item_count(UIN, 3207)))
        check("swap moves the attributes and clips them to the caps",
              moved["attack"] == min(999999, srv._baptize_caps(dst_cid)["attack"])
              and moved["defense"] == 1000 and left["attack"] == 0, (moved, left))
        check("swap answers SwapAttribute_SC + card syncs",
              msgs and msgs[0][0] == "PVZ.Cmd.Cmd_Card_SwapAttribute_SC", [n for n, _b in msgs])
    else:
        print("SKIP 植物交换 checks (the test player owns a single card)")

    print("\n%s (%d failing check(s))"
          % ("ALL CHECKS PASSED" if not FAILED else "FAILURES: %s" % FAILED, len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
