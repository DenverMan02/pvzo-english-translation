"""Regression checks for the six ExchangeStore windows and their top-left currency readout.

Two user-visible bugs are pinned here:

* every shop showed the "本店结算货币" icon with a count of ZERO (and a blank icon). The window
  paints that readout purely from `ExchangeModel.curStoreItem`, whose ONLY writer is the setter
  of `refreshItem` — i.e. field 4 of Cmd_ExchangeStore_ItemList_SC. The server sent a hard-coded 0
  there, so every storefront displayed "x0". It must now carry that store's coin (derived from the
  wares themselves; 神奇兑换 is pinned to 210 植物币 because the client derives its own second
  readout, curStoreItem2 = 211 神奇植物币, from the store type).

* the 竞技场 / 天梯竞技场 / 融合 / 果实 兑换 buttons all open ExchangeStoreType_Arena (= 3), and the
  战场商店 (= 6) is opened from 远征 / 巅峰之战. Neither store was stocked, so
  handle_exchangestore_itemlist answered a 0-byte body, ExchangePanelMediator.onGetExchangeList got
  an empty entrylist and the window rendered nothing at all — the reported "商店没有界面".
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_exshop_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700003912
FAILED = []


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def list_body(player, store_id):
    return srv.handle_exchangestore_itemlist(srv.pb_varint_field(1, store_id), player)


def entries_of(body):
    return [srv.parse_varint_fields(raw) for raw in srv.parse_repeated_submessages(body, 1)]


# (storeId, expected coin, a label) — the currency each ItemInfo row names in its own description.
# 神奇兑换 is special: its wares alternate between 210/211 and the client adds curStoreItem2 itself.
STORES = [
    (srv._WORLD_BOSS_STORE, srv._WORLD_BOSS_GEAR_ITEM, "世界BOSS商店"),
    (2, srv._ZI_MEDAL_ITEM, "僵尸岛商店"),
    (srv._ARENA_STORE, srv._ARENA_MEDAL_ITEM, "竞技商店"),
    (srv._GUILD_SHOP_STORE, srv._GUILD_MEDAL_ITEM, "联盟商店"),
    (srv._PLANT_TICKET_STORE, srv._PLANT_COIN_ITEM, "神奇兑换"),
    (srv._BATTLE_STORE, srv._BATTLE_MEDAL_ITEM, "战场商店"),
]


def main():
    player, _ = store.get_or_create_player(UIN)

    # --- 1. every ExchangeStoreType is stocked and nothing else answers ---------------------
    check("the store list matches ExchangeStoreType (0..6, 0 unused)",
          sorted([s for s, _, _ in STORES]) == [1, 2, 3, 4, 5, 6])
    for store_id, coin, label in STORES:
        body = list_body(player, store_id)
        check("%s (storeId %d) has a list" % (label, store_id), len(body) > 0, len(body))
        entries = entries_of(body)
        check("%s lists six wares" % label, len(entries) == srv._EXCHANGE_PANEL_SLOTS, len(entries))
        check("%s prices everything in %d" % (label, coin),
              all(e.get(5) == coin for e in entries), sorted({e.get(5) for e in entries}))
        # the bug: field 4 is the coin the window paints at its top-left
        check("%s advertises coin %d in refreshItem(4)" % (label, coin),
              srv.parse_varint_fields(body).get(4) == coin,
              srv.parse_varint_fields(body).get(4))
    check("an unknown store still answers an empty body",
          list_body(player, 0) == b"" and list_body(player, 7) == b"")

    # --- 2. entryIds stay in their own range so a purchase resolves to the right store -------
    ranges = {1: (701, 706), 2: (1, 6), 3: (601, 606), 4: (401, 406), 6: (801, 806)}
    seen = set()
    for store_id, (lo, hi) in ranges.items():
        ids = [e.get(1) for e in entries_of(list_body(player, store_id))]
        check("store %d uses entryIds %d..%d" % (store_id, lo, hi),
              sorted(ids) == list(range(lo, hi + 1)), ids)
        seen |= set(ids)
    check("no entryId is shared between stores", len(seen) == 30, len(seen))
    for store_id in ranges:
        for entry_id in [e.get(1) for e in entries_of(list_body(player, store_id))]:
            if srv.exchange_store_of_entry(entry_id) != store_id:
                check("entry %d resolves to store %d" % (entry_id, store_id), False,
                      srv.exchange_store_of_entry(entry_id))
                break
    check("every stocked entryId resolves back to its store", not any(
        srv.exchange_store_of_entry(e.get(1)) != s for s in ranges
        for e in entries_of(list_body(player, s))))

    # --- 3. the arena shop is actually usable: medals buy things ----------------------------
    store.add_item(UIN, srv._ARENA_MEDAL_ITEM, 500)
    ward = srv._ARENA_SHOP[3]                                    # 一星叶绿素 x1 for 10 勋章
    before = store.get_item_count(UIN, ward[2])
    medals_before = store.get_item_count(UIN, srv._ARENA_MEDAL_ITEM)
    msgs = srv.handle_exchangestore_exchange(srv.pb_varint_field(1, ward[0]), player)
    check("the arena exchange answers its own SC",
          msgs[0][0] == "PVZ.Cmd.Cmd_ExchangeStore_Exchange_SC", msgs[0][0])
    check("the arena exchange hands the item over",
          store.get_item_count(UIN, ward[2]) == before + ward[3], store.get_item_count(UIN, ward[2]))
    check("the arena exchange charges 竞技勋章",
          store.get_item_count(UIN, srv._ARENA_MEDAL_ITEM) == medals_before - ward[5],
          store.get_item_count(UIN, srv._ARENA_MEDAL_ITEM))
    check("the arena exchange re-sends the arena list",
          msgs[-1][0] == "PVZ.Cmd.Cmd_ExchangeStore_ItemList_SC"
          and srv.parse_varint_fields(msgs[-1][1]).get(4) == srv._ARENA_MEDAL_ITEM)
    srv.handle_exchangestore_exchange(srv.pb_varint_field(1, ward[0]), player)
    check("the arena shop is one purchase per entry per day",
          store.get_item_count(UIN, srv._ARENA_MEDAL_ITEM) == medals_before - ward[5],
          store.get_item_count(UIN, srv._ARENA_MEDAL_ITEM))
    marked = [e.get(7) for e in entries_of(list_body(player, srv._ARENA_STORE))]
    check("the bought arena ware is flagged hasExchanged",
          marked.count(1) == 1, marked)

    # --- 4. 刷新 clears that store's flags only --------------------------------------------
    msgs = srv.handle_exchangestore_refresh(srv.pb_varint_field(1, srv._ARENA_STORE), player)
    check("the arena 刷新 answers its own SC",
          msgs[0][0] == "PVZ.Cmd.Cmd_ExchangeStore_Refresh_SC", msgs[0][0])
    msgs = srv.handle_exchangestore_refresh(srv.pb_varint_field(1, srv._ARENA_STORE), player)
    check("the arena 刷新 re-opens the wares",
          not any(e.get(7) for e in entries_of(msgs[1][1])),
          [e.get(7) for e in entries_of(msgs[1][1])])
    store.add_item(UIN, srv._ZI_MEDAL_ITEM, 500)
    zi_ward = srv._ZI_SHOP[3]
    srv.handle_exchangestore_exchange(srv.pb_varint_field(1, zi_ward[0]), player)
    check("a different store keeps its own flags",
          any(e.get(7) for e in entries_of(list_body(player, 2))),
          [e.get(7) for e in entries_of(list_body(player, 2))])

    # --- 5. the arena hands out the medals the shop spends ---------------------------------
    real_run = srv.run_battle
    srv.run_battle = lambda left, right, **kw: (True, 0, 0, [])
    try:
        medals_before = store.get_item_count(UIN, srv._ARENA_MEDAL_ITEM)
        srv.handle_arena_challenge(b"", player)
        check("an arena win pays 竞技勋章",
              store.get_item_count(UIN, srv._ARENA_MEDAL_ITEM) == medals_before + srv._ARENA_MEDAL_WIN,
              store.get_item_count(UIN, srv._ARENA_MEDAL_ITEM))
    finally:
        srv.run_battle = real_run

    print("exchange stores: %s" % ("FAIL %s" % FAILED if FAILED else "PASS"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
