"""Regression checks for the 酒吧 draw, 批量拆解, 卡牌库 duplicates and 神奇兑换 (植物币) economy.

Four user-visible bugs:
  1. the 酒吧 single draw (Cmd_Card_Extract_CS groupId=1) must add a plant card and grant item 2003
     (经验豆) as a separate bonus;
  2. 批量拆解 (Cmd_Card_AutoRecycle_CS) did nothing when the client sent no explicit uid list;
  3. a duplicate pull paid coins, so the card the reveal showed never reached the 卡牌库;
  4. 神奇兑换 (ExchangeStore 5 = ExchangeStoreType_PlantTicket) was empty and its currency (植物币 210,
     earned by 碎片回收) was not implemented at all.
  5. (follow-up) that restored list was longer than the 道具兑换 window can hold, so opening it — and
     every 刷新 — threw `RangeError #1125: 索引 6 超出范围 6` in
     ExchangePanelMediator/onGetExchangeList (ModuleExchange.swf). 神奇兑换 now answers one page of six
     wares and 刷新 walks the pool.
"""
import os
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_cardbar_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700003151
FAILED = []


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def msg(msgs, name):
    return next((b for n, b in msgs if n == name), None)


def add_dtos(body):
    """The Dto_CardFullInfo list of a Cmd_Card_SyncFullInfo_SC.addCardList (field 2)."""
    return srv.parse_repeated_submessages(body or b"", 2)


def _base_fields(body):
    out = []
    for raw in add_dtos(body):
        base = srv.parse_repeated_submessages(raw, 1)              # base (field 1)
        out.append(srv.parse_varint_fields(base[0]) if base else {})
    return out


def card_uids(body):
    return [b.get(1) for b in _base_fields(body)]


def card_ids(body):
    return [b.get(2) for b in _base_fields(body)]


def extract_item_ids(body):
    """Dto_Card_Extract_SC.items (field 1) -> BAG-ITEM ids shown by the reveal."""
    return [srv.parse_varint_fields(raw).get(1)
            for raw in srv.parse_repeated_submessages(body or b"", 1)]


def shop_entries(store_id, player):
    body = srv.handle_exchangestore_itemlist(srv.pb_varint_field(1, store_id), player)
    return [srv.parse_varint_fields(raw) for raw in srv.parse_repeated_submessages(body, 1)]


def main():
    player, _new = store.get_or_create_player(UIN)
    player["plants"] = store.get_player_plants(UIN)
    store.save_player(UIN, level=60, coin=1000000)
    player["level"], player["coin"] = 60, 1000000
    srv.ensure_player_cards(player)
    srv.load_battle_cards()
    srv.load_fragments()
    print("starter cards: %d" % len(store.get_cards(UIN)))

    # --- 1. 酒吧单抽 (groupId 1) gives one plant card plus one 经验豆 bonus ----------------
    before = store.get_item_count(UIN, 2003)
    cards_before = len(store.get_cards(UIN))
    msgs = srv.handle_card_extract(srv.pb_varint_field(1, 1), player)
    names = [n for n, _b in msgs]
    check("single draw answers Cmd_Card_Extract_SC",
          "PVZ.Cmd.Cmd_Card_Extract_SC" in names, names)
    extract = msg(msgs, "PVZ.Cmd.Cmd_Card_Extract_SC")
    check("single draw reveal contains one plant item, not the bean bonus",
          len(extract_item_ids(extract)) == 1 and 2003 not in extract_item_ids(extract),
          extract_item_ids(extract))
    sync = msg(msgs, "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC")
    drawn_ids = card_ids(sync)
    check("single draw adds exactly one plant card", len(drawn_ids) == 1 and drawn_ids[0] in srv._BATTLE_CARDS,
          drawn_ids)
    check("single draw grows the card collection by one", len(store.get_cards(UIN)) == cards_before + 1,
          len(store.get_cards(UIN)))
    check("single draw grants exactly one 经验豆 2003", store.get_item_count(UIN, 2003) == before + 1,
          (before, store.get_item_count(UIN, 2003)))

    # --- 2. duplicate draws land in the collection as ANOTHER copy -------------------------
    owned_before = len(store.get_cards(UIN))
    store.add_item(UIN, srv._DRAW_TICKET_ITEM, 10)                            # pay for the 十连
    msgs = srv.handle_card_extract(srv.pb_varint_field(1, 2), player)          # 十连 (ticket cost)
    sync = msg(msgs, "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC")
    drawn, new_uids = card_ids(sync), card_uids(sync)
    check("ten draw adds ten cards", len(drawn) == 10, len(drawn))
    check("ten draw uids are unique", len(set(new_uids)) == len(new_uids), new_uids)
    check("every drawn uid is in the collection",
          all(u in [c[0] for c in store.get_cards(UIN)] for u in new_uids))
    check("collection grew by the whole draw",
          len(store.get_cards(UIN)) == owned_before + 10, len(store.get_cards(UIN)))
    dupes = [cid for cid in set(drawn) if drawn.count(cid) > 1]
    if dupes:
        cid = dupes[0]
        uids_of = [u for u, c in zip(new_uids, drawn) if c == cid]
        check("a duplicate plant is stored as separate copies (%d)" % len(uids_of),
              len(uids_of) == len(set(uids_of)) > 1, (cid, uids_of))
    else:
        check("draw pool had no duplicate in this run (nothing to pin)", True, drawn)

    # --- 3. 神秘卡包 is a MIXED five-card pack, not the brewery's plant-only draw ----------
    store.add_item(UIN, srv._MYSTERY_TICKET_ITEM, 5)
    player["mystery_luck"] = 0
    owned_before = len(store.get_cards(UIN))
    with patch.object(srv.random, "random", return_value=1.0):
        msgs = srv.handle_card_buymysterycard(b"", player)
    sync = msg(msgs, "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC")
    rewards = extract_item_ids(msg(msgs, "PVZ.Cmd.Cmd_Card_BuyMysteryCard_SC"))
    check("ordinary mystery pack reveals five mixed rewards, no invented plant",
          len(rewards) == 5 and rewards[1] == 2004
          and len(add_dtos(sync)) == 0 and len(store.get_cards(UIN)) == owned_before, rewards)

    # --- 4. 批量拆解 with no uid list recycles the DUPLICATES ------------------------------
    dup_uid = srv._CARD_UID_BASE + 1201
    store.add_card(UIN, dup_uid, 1201, 1)                      # a 2nd 单发豌豆
    store.add_card(UIN, dup_uid + 100000, 1201, 1)             # a 3rd one
    copies_before = [u for u, cid, _l, _e in store.get_cards(UIN) if cid == 1201]
    msgs = srv.handle_card_autorecycle(srv.pb_varint_field(1, 0), player)
    body = msg(msgs, "PVZ.Cmd.Cmd_Card_AutoRecycle_SC")
    check("empty 批量拆解 request still answers its own SC", body is not None, [n for n, _b in msgs])
    copies = [u for u, cid, _l, _e in store.get_cards(UIN) if cid == 1201]
    check("empty request auto-recycles the extra copies (one copy survives)",
          bool(srv.parse_repeated_submessages(body or b"", 1)) and len(copies) == 1
          and len(copies_before) > 1, (copies_before, copies))
    deleted = srv.parse_repeated_varint(msg(msgs, "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC") or b"", 3)
    fam = {}
    for u, cid, _l, _e in store.get_cards(UIN):
        td = (srv._BATTLE_CARDS.get(cid) or {}).get("tdId") or -u
        fam[td] = fam.get(td, 0) + 1
    check("the client is told which copies were recycled",
          dup_uid + 100000 in deleted and len(deleted) >= len(copies_before) - 1,
          (copies_before, deleted))
    check("no plant is left with a duplicate copy", all(v == 1 for v in fam.values()), fam)

    # --- 5. 碎片回收 pays 植物币 at the ItemInfo rate --------------------------------------
    frag, rate = 51200, srv.frag_coin_value(51200)
    check("fragment 51200 has a recycle rate from clientDB", rate > 0, rate)
    store.add_item(UIN, frag, 30)
    frag_have = store.get_item_count(UIN, frag)
    coins_before = store.get_item_count(UIN, srv._PLANT_COIN_ITEM)
    msgs = srv.handle_item_recycledebris(srv.pb_varint_field(1, frag) + srv.pb_varint_field(2, 10),
                                         player)
    check("debris recycle answers Cmd_Item_RecycleDebris_SC",
          msg(msgs, "PVZ.Cmd.Cmd_Item_RecycleDebris_SC") is not None, [n for n, _b in msgs])
    check("debris recycle consumes the fragments", store.get_item_count(UIN, frag) == frag_have - 10,
          (frag_have, store.get_item_count(UIN, frag)))
    check("debris recycle pays 植物币", store.get_item_count(UIN, srv._PLANT_COIN_ITEM)
          == coins_before + rate * 10, store.get_item_count(UIN, srv._PLANT_COIN_ITEM))
    srv.handle_item_recycledebris(srv.pb_varint_field(1, 2002) + srv.pb_varint_field(2, 5), player)
    check("recycling a non-fragment pays nothing",
          store.get_item_count(UIN, srv._PLANT_COIN_ITEM) == coins_before + rate * 10)
    srv.handle_item_recycledebris(srv.pb_varint_field(1, frag) + srv.pb_varint_field(2, 99), player)
    check("recycle never destroys more than the player holds", store.get_item_count(UIN, frag) == 0,
          store.get_item_count(UIN, frag))

    # --- 6. 神奇兑换 (ExchangeStore 5) lists wares and spends 植物币 ------------------------
    # The 道具兑换 window keeps its rows in a FIXED six-slot Vector (ModuleExchange.swf,
    # ExchangePanel.exchangeListItems), so a longer entrylist used to throw
    # `RangeError: Error #1125: 索引 6 超出范围 6` in ExchangePanelMediator/onGetExchangeList as soon as
    # the window opened — and again on every 刷新, which re-sends the same list. Every answer must hold
    # at most six entries, numbered pos 1..6.
    entries = shop_entries(5, player)
    check("神奇兑换 fills the panel's six slots", len(entries) == srv._EXCHANGE_PANEL_SLOTS,
          len(entries))
    check("神奇兑换 numbers the slots 1..6", [e.get(2) for e in entries] == [1, 2, 3, 4, 5, 6],
          [e.get(2) for e in entries])
    check("神奇兑换 lists wares", len(entries) > 0, len(entries))
    check("神奇兑换 spends 植物币/神奇植物币",
          all(e.get(5) in (srv._PLANT_COIN_ITEM, srv._MYSTERY_COIN_ITEM) for e in entries),
          [(e.get(3), e.get(5), e.get(6)) for e in entries][:4])
    check("神奇兑换 wares are 碎片",
          all(e.get(3) in srv.load_fragments() for e in entries),
          [(e.get(3), srv.load_fragments().get(e.get(3), {}).get("name")) for e in entries][:3])
    check("神奇兑换 wares initially available", all(e.get(7, 0) == 0 for e in entries))
    cheap = next(e for e in entries if e.get(5) == srv._PLANT_COIN_ITEM)
    store.add_item(UIN, srv._PLANT_COIN_ITEM, cheap[6] * 2 + 10)
    coins_before = store.get_item_count(UIN, srv._PLANT_COIN_ITEM)
    frag_before = store.get_item_count(UIN, cheap[3])
    msgs = srv.handle_exchangestore_exchange(srv.pb_varint_field(1, cheap[1]), player)
    check("exchange answers Cmd_ExchangeStore_Exchange_SC",
          msg(msgs, "PVZ.Cmd.Cmd_ExchangeStore_Exchange_SC") is not None, [n for n, _b in msgs])
    check("exchange charges the plant coins",
          store.get_item_count(UIN, srv._PLANT_COIN_ITEM) == coins_before - cheap[6],
          store.get_item_count(UIN, srv._PLANT_COIN_ITEM))
    check("exchange hands the fragments over",
          store.get_item_count(UIN, cheap[3]) == frag_before + cheap[4],
          store.get_item_count(UIN, cheap[3]))
    check("exchange re-sends the shop list",
          msg(msgs, "PVZ.Cmd.Cmd_ExchangeStore_ItemList_SC") is not None)
    srv.handle_exchangestore_exchange(srv.pb_varint_field(1, cheap[1]), player)
    check("神奇兑换 duplicate purchase grants no extra fragments",
          store.get_item_count(UIN, cheap[3]) == frag_before + cheap[4],
          store.get_item_count(UIN, cheap[3]))
    check("神奇兑换 duplicate purchase does not charge again",
          store.get_item_count(UIN, srv._PLANT_COIN_ITEM) == coins_before - cheap[6])
    bought_row = next(e for e in shop_entries(5, player) if e.get(1) == cheap[1])
    check("神奇兑换 purchased row is disabled", bought_row.get(7) == 1)
    store.add_item(UIN, srv._PLANT_COIN_ITEM, -(store.get_item_count(UIN, srv._PLANT_COIN_ITEM)))
    other = next(e for e in entries if e.get(5) == srv._PLANT_COIN_ITEM and e.get(1) != cheap[1])
    other_before = store.get_item_count(UIN, other[3])
    srv.handle_exchangestore_exchange(srv.pb_varint_field(1, other[1]), player)
    check("exchange without the currency changes nothing",
          store.get_item_count(UIN, other[3]) == other_before
          and store.get_item_count(UIN, cheap[3]) == frag_before + cheap[4])
    check("unknown entry is a clean no-op",
          srv.handle_exchangestore_exchange(srv.pb_varint_field(1, 999999), player)[0][0]
          == "PVZ.Cmd.Cmd_ExchangeStore_Exchange_SC")
    sc = srv.handle_exchangestore_itemlist(srv.pb_varint_field(1, 5), player)
    check("store 5 answers Cmd_ExchangeStore_ItemList_SC", bool(sc))
    check("store 5 advertises a refresh time", srv.parse_varint_fields(sc).get(2, 0) > 0)
    check("store 5 keeps 刷新 usable (roomy refreshMaxTimes)",
          srv.parse_varint_fields(sc).get(3, 0) > 1, srv.parse_varint_fields(sc).get(3))

    # --- 6b. 刷新 walks the 神奇兑换 pool a page at a time --------------------------------
    pool = srv.plant_coin_shop_entries()
    pages = srv.plant_coin_shop_page_count()
    check("the 神奇兑换 pool is wider than one window", len(pool) > srv._EXCHANGE_PANEL_SLOTS,
          len(pool))
    check("the pool makes up more than one page", pages > 1, pages)
    first_page = {e.get(1) for e in entries}
    seen, changed = set(first_page), False
    for page in range(2, pages + 1):
        msgs = srv.handle_exchangestore_refresh(srv.pb_varint_field(1, 5), player)
        check("刷新 answers Cmd_ExchangeStore_Refresh_SC",
              msg(msgs, "PVZ.Cmd.Cmd_ExchangeStore_Refresh_SC") is not None, [n for n, _b in msgs])
        listing = shop_entries(5, player)
        check("刷新 page %d still holds six wares" % page,
              len(listing) == srv._EXCHANGE_PANEL_SLOTS, len(listing))
        ids = {e.get(1) for e in listing}
        changed = changed or ids != first_page
        seen |= ids
    check("刷新 changes the wares", changed, sorted(first_page))
    check("every ware shows up within one cycle of 刷新", seen == {e[0] for e in pool},
          (len(seen), len(pool)))
    srv.handle_exchangestore_refresh(srv.pb_varint_field(1, 5), player)
    check("刷新 wraps back to the first page",
          {e.get(1) for e in shop_entries(5, player)} == first_page)
    check("刷新 preserves the purchased row's disabled state",
          next(e for e in shop_entries(5, player) if e.get(1) == cheap[1]).get(7) == 1)
    srv.handle_exchangestore_refresh(srv.pb_varint_field(1, 5), player)
    stored, _new = store.get_or_create_player(UIN)
    check("the 神奇兑换 page is persisted for the next visit",
          int(stored.get("plant_shop_page") or 0) == 1, stored.get("plant_shop_page"))

    # --- 7. the island shop still works (no regression) ------------------------------------
    island = shop_entries(2, player)
    check("island shop (store 2) still lists its six entries", len(island) == len(srv._ZI_SHOP),
          len(island))
    check("an unstocked store answers empty",
          srv.handle_exchangestore_itemlist(srv.pb_varint_field(1, 7), player) == b"")

    # --- 8. the one-time start stash + registrations --------------------------------------
    starter_before = (store.get_item_count(UIN, srv._PLANT_COIN_ITEM),
                      store.get_item_count(UIN, srv._MYSTERY_COIN_ITEM))
    store.save_player(UIN, plant_coin_granted=0)
    player["plant_coin_granted"] = 0
    srv.handle_card_getpackage(b"", player)
    starter_after = (store.get_item_count(UIN, srv._PLANT_COIN_ITEM),
                     store.get_item_count(UIN, srv._MYSTERY_COIN_ITEM))
    check("opening the card library grants the exact 神奇兑换 starter stash",
          starter_after == (starter_before[0] + 5000, starter_before[1] + 500),
          (starter_before, starter_after))
    srv.handle_card_getpackage(b"", player)
    check("the stash is only handed out once",
          (store.get_item_count(UIN, srv._PLANT_COIN_ITEM),
           store.get_item_count(UIN, srv._MYSTERY_COIN_ITEM)) == starter_after,
          starter_after)
    for name in ("PVZ.Cmd.Cmd_Item_RecycleDebris_CS", "PVZ.Cmd.Cmd_Card_Extract_CS",
                 "PVZ.Cmd.Cmd_Card_AutoRecycle_CS", "PVZ.Cmd.Cmd_ExchangeStore_ItemList_CS",
                 "PVZ.Cmd.Cmd_ExchangeStore_Exchange_CS"):
        check("%s registered" % name.rsplit(".", 1)[-1], callable(srv.REQUEST_HANDLERS.get(name)))

    print("card bar / pieces / 神奇兑换: %s" % ("FAIL %s" % FAILED if FAILED else "PASS"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
