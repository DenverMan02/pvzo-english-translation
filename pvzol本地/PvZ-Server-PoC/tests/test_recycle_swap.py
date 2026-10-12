"""Functional tests for 植物拆解 / 批量拆解 (card recycle) and 植物交换 (card swap).

Runs against a THROWAWAY sqlite DB so the real players.db is never touched:
    python tests/test_recycle_swap.py          (from the PvZ-Server-PoC folder)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRV_DIR = os.path.dirname(HERE)                    # ..\PvZ-Server-PoC
sys.path.insert(0, SRV_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store                       # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_recycle_"), "test.db")
store.init_db()

import pvz_poc_server as srv                       # noqa: E402

UIN = 700000997
CARD_UID_BASE = srv._CARD_UID_BASE
FAILED = []


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def recycle_cmd(uid):
    return srv.pb_int64_field(1, uid)


def autorecycle_cmd(uids):
    body = srv.pb_varint_field(1, 0)                            # pwdtag
    for uid in uids:
        body += srv.pb_int64_field(2, uid)                      # repeated cardUId
    return body


def id_num_pairs(body):
    """{itemId: num} from a repeated Dto_IdNum field (field 2 of Recycle_SC / field 1 of AutoRecycle)."""
    out = {}
    for sub in srv.parse_repeated_submessages(body, 2) + srv.parse_repeated_submessages(body, 1):
        f = srv.parse_varint_fields(sub)
        if f.get(1):
            out[f.get(1)] = out.get(f.get(1), 0) + f.get(2, 0)
    return out


def del_card_list(body):
    return srv.parse_repeated_varint(body, 3)


def first_varint(body, field):
    """The FIRST value of a varint field (parse_varint_fields keeps only the last one)."""
    pos = 0
    while pos < len(body):
        key, pos = srv.read_uvarint(body, pos)
        fnum, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = srv.read_uvarint(body, pos)
            if fnum == field:
                return value
        elif wire == 2:
            ln, pos = srv.read_uvarint(body, pos)
            pos += ln
        else:
            break
    return None


def msg(msgs, name):
    return next((b for n, b in msgs if n == name), None)


def main():
    player, _new = store.get_or_create_player(UIN)
    player["plants"] = store.get_player_plants(UIN)
    store.save_player(UIN, level=60, coin=1000)
    player["level"], player["coin"] = 60, 1000
    srv.ensure_player_cards(player)
    loaders = srv.load_card_swap_costs()
    print("swap cost tables:", loaders)
    print("starter cards:", [(uid, cid) for uid, cid, _l, _e in store.get_cards(UIN)])

    # ------------------------------------------------------------- 1) 拆解 (single card)
    uid, cid = CARD_UID_BASE + 1501, 1501                        # 土豆地雷 star-1 starter
    info = srv._BATTLE_CARDS[cid]
    store.set_card_level(UIN, uid, 12, 30000)                    # 3x 小经验豆 worth of exp
    store.set_card_forge_level(UIN, uid, 2)
    store.set_card_fill_quality(UIN, uid, 2)                     # board 1 finished (9 pieces)
    store.add_card_filled_index(UIN, uid, 0)                     # one placed cell on board 2
    forge_coin = sum(row["cost"] for row in srv.load_card_forge().get(cid, [])
                     if row["level"] <= 2)
    coins_before = int(player["coin"])
    msgs = srv.handle_card_recycle(recycle_cmd(uid), player)
    names = [n for n, _b in msgs]
    check("Recycle answers Cmd_Card_Recycle_SC first",
          bool(names) and names[0] == "PVZ.Cmd.Cmd_Card_Recycle_SC", names)
    body = msg(msgs, "PVZ.Cmd.Cmd_Card_Recycle_SC") or b""
    check("Recycle_SC{cardUId} echoes the card",
          first_varint(body, 1) == uid, first_varint(body, 1))
    reward = id_num_pairs(body)
    check("Recycle_SC.recycleResult carries the 碎片 the dialog promised",
          reward.get(info["recycleItemId"]) == info["recycleItemNum"],
          "%s vs %s" % (reward.get(info["recycleItemId"]), info["recycleItemNum"]))
    check("Recycle hands the 碎片 to the bag",
          store.get_item_count(UIN, info["recycleItemId"]) >= info["recycleItemNum"])
    check("Recycle refunds the exp beans (30000 exp -> 3x 小经验豆)",
          store.get_item_count(UIN, 2002) == 3, store.get_item_count(UIN, 2002))
    check("Recycle refunds the finished puzzle board (61001..61009)",
          all(store.get_item_count(UIN, 61000 + n) == 1 for n in range(1, 10)),
          [store.get_item_count(UIN, 61000 + n) for n in range(1, 10)])
    check("Recycle refunds the placed cell of the current board (62001)",
          store.get_item_count(UIN, 62001) == 1, store.get_item_count(UIN, 62001))
    check("Recycle refunds the forge coins (%d)" % forge_coin,
          int(player["coin"]) - coins_before == forge_coin, int(player["coin"]) - coins_before)
    check("Recycle syncs the coin", msg(msgs, "PVZ.Cmd.Cmd_SyncCoin_SC") is not None, names)
    check("Recycle tells the client to drop the card",
          del_card_list(msg(msgs, "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC") or b"") == [uid],
          del_card_list(msg(msgs, "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC") or b""))
    check("Recycle deletes the card from the collection",
          uid not in [c[0] for c in store.get_cards(UIN)])
    check("Recycle forgets the card's dependent rows",
          store.get_card_forge_level(UIN, uid) == 0
          and store.get_card_filled_indices(UIN, uid) == [])
    check("Recycle of an unknown card is a clean no-op",
          srv.handle_card_recycle(recycle_cmd(uid), player) == [])

    # -------------------------------------------- 2) 拆解 refunds the whole star-up chain
    star_uid = CARD_UID_BASE + 1103                              # 向日葵 3星 (two star-ups in)
    store.add_card(UIN, star_uid, 1103, 1)
    coins_before = int(player["coin"])
    frag_before = store.get_item_count(UIN, 51100)
    srv.handle_card_recycle(recycle_cmd(star_uid), player)
    chain = srv._card_star_refund(1103)
    want_frag = (frag_before + sum(item_num for _i, item_num, _c in chain)
                 + srv._BATTLE_CARDS[1103]["recycleItemNum"])
    want_coin = coins_before + sum(coin for _i, _n, coin in chain)
    check("Recycle walks the CardUpgradeTemplate chain (%d star-ups)" % len(chain), len(chain) == 2,
          chain)
    check("Recycle refunds the star-up 碎片 on top of the plant's own 碎片 (%d)" % want_frag,
          store.get_item_count(UIN, 51100) == want_frag, store.get_item_count(UIN, 51100))
    check("Recycle refunds the star-up coins (%d)" % want_coin,
          int(player["coin"]) == want_coin, int(player["coin"]))

    # ------------------------------------------------------------- 3) 批量拆解 (batch)
    solo_uid, dup_uid = CARD_UID_BASE + 1201, CARD_UID_BASE + 1202   # 单发豌豆 1星 / 2星
    store.add_card(UIN, dup_uid, 1202, 1)
    msgs = srv.handle_card_autorecycle(autorecycle_cmd([solo_uid, dup_uid]), player)
    body = msg(msgs, "PVZ.Cmd.Cmd_Card_AutoRecycle_SC")
    check("AutoRecycle answers Cmd_Card_AutoRecycle_SC", body is not None, [n for n, _b in msgs])
    check("AutoRecycle keeps the BEST copy of a plant and recycles the other",
          solo_uid not in [c[0] for c in store.get_cards(UIN)]
          and dup_uid in [c[0] for c in store.get_cards(UIN)],
          [uid for uid, _c, _l, _e in store.get_cards(UIN)])
    check("AutoRecycle reports the delCardList",
          del_card_list(msg(msgs, "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC") or b"") == [solo_uid],
          del_card_list(msg(msgs, "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC") or b""))
    check("AutoRecycle pays the recycled card's 碎片",
          id_num_pairs(body or b"").get(51200, 0) == srv._BATTLE_CARDS[1201]["recycleItemNum"],
          id_num_pairs(body or b""))
    msgs = srv.handle_card_autorecycle(autorecycle_cmd([dup_uid]), player)
    check("AutoRecycle protects the LAST copy of a plant",
          dup_uid in [c[0] for c in store.get_cards(UIN)]
          and not id_num_pairs(msg(msgs, "PVZ.Cmd.Cmd_Card_AutoRecycle_SC") or b""),
          [n for n, _b in msgs])
    check("AutoRecycle ignores unknown uids",
          srv.handle_card_autorecycle(autorecycle_cmd([123456]), player)[0][0]
          == "PVZ.Cmd.Cmd_Card_AutoRecycle_SC")

    # ------------------------------------------------------------- 4) 植物交换 (swap)
    src_uid, dst_uid = CARD_UID_BASE + 1101, CARD_UID_BASE + 1301   # 向日葵 / 矮坚果 (both 1星)
    store.add_card(UIN, dst_uid, 1301, 1)
    store.set_card_level(UIN, src_uid, 20, 400000)
    store.set_card_fill_quality(UIN, src_uid, 3)
    store.add_card_filled_index(UIN, src_uid, 7)
    store.set_card_baptize(UIN, src_uid, {"attack": 999999, "defense": 1000, "hp": 2000,
                                          "ability": 3000})
    store.set_card_fruit_level(UIN, src_uid, 0, 3)
    store.set_card_gene(UIN, src_uid, 1, 3, 0, 0, 2)
    store.add_item(UIN, 3207, 20)
    want_src = srv.card_level_from_exp(0, 1101, int(player["level"]))
    want_dst = srv.card_level_from_exp(400000, 1301, int(player["level"]))
    charge = {}
    for group in ("refine", "fruit", "awake", "gene"):
        cost = loaders[group] if group in ("refine", "fruit") else loaders[group][1]
        for iid, num in cost.items():
            charge[iid] = charge.get(iid, 0) + num
    tokens = store.get_item_count(UIN, 3207)
    swap_body = (srv.pb_int64_field(1, src_uid) + srv.pb_int64_field(2, dst_uid)
                 + srv.pb_bool(3, True) + srv.pb_bool(4, True) + srv.pb_bool(5, True)
                 + srv.pb_bool(6, True))
    msgs = srv.handle_card_swapattribute(swap_body, player)
    names = [n for n, _b in msgs]
    check("Swap answers Cmd_Card_SwapAttribute_SC first",
          bool(names) and names[0] == "PVZ.Cmd.Cmd_Card_SwapAttribute_SC", names)
    check("Swap syncs BOTH cards", names.count("PVZ.Cmd.Cmd_Card_SyncFullInfo_SC") == 2, names)
    cards = {c[0]: c for c in store.get_cards(UIN)}
    check("Swap exchanges 等级/经验 (each card staying inside its own cap)",
          (cards[src_uid][2], cards[src_uid][3]) == want_src[0:2]
          and (cards[dst_uid][2], cards[dst_uid][3]) == want_dst[0:2],
          ((cards[src_uid][2], cards[src_uid][3]), want_src,
           (cards[dst_uid][2], cards[dst_uid][3]), want_dst))
    check("Swap exchanges 拼图 (board stage + placed pieces)",
          store.get_card_fill_quality(UIN, dst_uid) == 3
          and store.get_card_fill_quality(UIN, src_uid) == 1
          and store.get_card_filled_indices(UIN, dst_uid) == [7]
          and store.get_card_filled_indices(UIN, src_uid) == [],
          (store.get_card_fill_quality(UIN, dst_uid), store.get_card_fill_quality(UIN, src_uid),
           store.get_card_filled_indices(UIN, dst_uid)))
    moved = store.get_card_baptize(UIN, dst_uid)
    left = store.get_card_baptize(UIN, src_uid)
    check("Swap exchanges 洗炼 (clipped to the receiving card's caps)",
          moved["attack"] == min(999999, srv._baptize_caps(1301)["attack"])
          and moved["defense"] == 1000 and left["attack"] == 0, (moved, left))

    check("Swap exchanges 果实", store.get_card_fruits(UIN, dst_uid) == {0: 3}
          and store.get_card_fruits(UIN, src_uid) == {},
          (store.get_card_fruits(UIN, dst_uid), store.get_card_fruits(UIN, src_uid)))
    check("Swap exchanges 基因", store.get_card_genes(UIN, dst_uid).get(1, {}).get("level") == 2
          and not store.get_card_genes(UIN, src_uid),
          (store.get_card_genes(UIN, dst_uid), store.get_card_genes(UIN, src_uid)))
    check("Swap charges the 交换券 price of every ticked group (%s)" % charge,
          store.get_item_count(UIN, 3207) == tokens - charge.get(3207, 0),
          "%d -> %d" % (tokens, store.get_item_count(UIN, 3207)))
    store.add_item(UIN, 3207, -store.get_item_count(UIN, 3207))
    before = store.get_cards(UIN)
    check("Swap without 交换券 changes nothing",
          srv.handle_card_swapattribute(swap_body, player) == [] and store.get_cards(UIN) == before)
    check("Swap rejects a card the player does not own",
          srv.handle_card_swapattribute(
              srv.pb_int64_field(1, src_uid) + srv.pb_int64_field(2, 987654), player) == [])
    check("Swap rejects src == dst",
          srv.handle_card_swapattribute(
              srv.pb_int64_field(1, src_uid) + srv.pb_int64_field(2, src_uid), player) == [])

    # ------------------------------------------------------------- 4b) 觉醒 exchange
    awake_src, awake_dst = CARD_UID_BASE + 1205, CARD_UID_BASE + 101215   # 单发豌豆觉醒0 / 双发豌豆觉醒1
    store.add_card(UIN, awake_src, 1205, 1)
    store.add_card(UIN, awake_dst, 101215, 1)
    store.add_item(UIN, 3207, 10)
    tokens = store.get_item_count(UIN, 3207)
    srv.handle_card_swapattribute(
        srv.pb_int64_field(1, awake_src) + srv.pb_int64_field(2, awake_dst) + srv.pb_bool(3, True),
        player)
    cards = {c[0]: c for c in store.get_cards(UIN)}
    check("Swap exchanges 觉醒 (each plant gets the other's 觉醒 level, keeping its own family)",
          cards[awake_src][1] == 101205 and cards[awake_dst][1] == 1215,
          (cards[awake_src], cards[awake_dst]))
    check("Swap charges the awakeSwap price (%s)" % loaders["awake"][1],
          store.get_item_count(UIN, 3207) == tokens - loaders["awake"][1].get(3207, 0),
          "%d -> %d" % (tokens, store.get_item_count(UIN, 3207)))
    store.add_item(UIN, 2111, -store.get_item_count(UIN, 2111))
    store.add_item(UIN, 2121, -store.get_item_count(UIN, 2121))
    srv.handle_card_recycle(recycle_cmd(awake_src), player)
    check("Recycle refunds an awakened card's 觉醒石/结晶",
          store.get_item_count(UIN, 2111) == 1000 and store.get_item_count(UIN, 2121) == 300,
          (store.get_item_count(UIN, 2111), store.get_item_count(UIN, 2121)))

    # ------------------------------------------------------------- 5) wiring
    for name in ("PVZ.Cmd.Cmd_Card_Recycle_CS", "PVZ.Cmd.Cmd_Card_AutoRecycle_CS",
                 "PVZ.Cmd.Cmd_Card_SwapAttribute_CS", "PVZ.Cmd.Cmd_Card_SwapAttributeLost_CS"):
        cls = name.rsplit(".", 1)[-1]
        check("%s is registered" % name, callable(srv.REQUEST_HANDLERS.get(name)))
        check("%s.as exists for %s" % (cls, name), os.path.isfile(os.path.join(
            os.path.dirname(SRV_DIR), "Assets", "src", "PVZ", "Cmd", cls + ".as")))

    # ------------------------------------------------------------- 6) login 交换券 stash
    store.save_player(UIN, swap_granted=0)
    player["swap_granted"] = 0
    store.add_item(UIN, 3207, -store.get_item_count(UIN, 3207))
    srv.card_swap_login_grant(player)
    check("login hands out the 交换券 stash exactly once",
          store.get_item_count(UIN, 3207) == srv._CARD_SWAP_STARTER_ITEMS[0][1]
          and srv.card_swap_login_grant(player) == [],
          store.get_item_count(UIN, 3207))

    print("\n%d check(s) failed" % len(FAILED) if FAILED else "\nall checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
