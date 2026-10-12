"""The town shop must list clientDB goods and complete their purchases."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import player_store as store

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_shop_"), "test.db")
store.init_db()
import pvz_poc_server as srv


def main():
    player, _ = store.get_or_create_player(700004001)
    catalog = srv.mall_catalog()
    assert len(catalog) >= 70
    assert catalog[201][:3] == (2018, 10, False)
    assert catalog[202][:3] == (2070, 50, True)
    assert catalog[210][:3] == (1009, 20, False)
    listed = srv.handle_shop_getlist(b"", player)
    assert listed[0][0] == "PVZ.Cmd.Cmd_Shop_GetList_SC"
    assert len(srv.parse_repeated_varint(listed[0][1], 1)) == len(catalog)
    assert srv.REQUEST_HANDLERS["PVZ.Cmd.Cmd_Shop_GetList_CS"] is srv.handle_shop_getlist

    store.save_player(player["uin"], present_ticket=25, ticket=100)
    player.update(present_ticket=25, ticket=100)
    # Ordinary diamonds spend free balance before paid balance and update the bag.
    request = srv.pb_varint_field(1, 201) + srv.pb_varint_field(2, 3)
    response = srv.handle_shop_buyitem(request, player)
    assert response[0][0] == "PVZ.Cmd.Cmd_Shop_BuyItem_SC"
    assert store.get_item_count(player["uin"], 2018) == 3
    assert (player["present_ticket"], player["ticket"]) == (0, 95)
    assert {x[0] for x in response} >= {
        "PVZ.Cmd.Cmd_Item_Update_SC", "PVZ.Cmd.Cmd_SyncPresentTicket_SC",
        "PVZ.Cmd.Cmd_SyncTicket_SC"}

    # Gold-only goods cannot be paid for with gift diamonds.
    store.save_player(player["uin"], present_ticket=200, ticket=0)
    player.update(present_ticket=200, ticket=0)
    gold_request = srv.pb_varint_field(1, 202) + srv.pb_varint_field(2, 1)
    assert srv.handle_shop_buyitem(gold_request, player) == []
    assert store.get_item_count(player["uin"], 2070) == 0
    store.save_player(player["uin"], ticket=50)
    player["ticket"] = 50
    response = srv.handle_shop_buyitem(gold_request, player)
    assert store.get_item_count(player["uin"], 2070) == 1
    assert (player["present_ticket"], player["ticket"]) == (200, 0)
    assert "PVZ.Cmd.Cmd_SyncTicket_SC" in [name for name, _ in response]

    # An unknown id, invalid quantity or shortage must neither grant nor charge.
    snapshot = (store.get_item_count(player["uin"], 2018),
                player["present_ticket"], player["ticket"])
    for bad in (srv.pb_varint_field(1, 999999),
                srv.pb_varint_field(1, 201) + srv.pb_varint_field(2, 1001),
                srv.pb_varint_field(1, 401) + srv.pb_varint_field(2, 1000)):
        assert srv.handle_shop_buyitem(bad, player) == []
    assert (store.get_item_count(player["uin"], 2018),
            player["present_ticket"], player["ticket"]) == snapshot
    print("PASS shop catalog and purchase flow")


if __name__ == "__main__":
    main()
