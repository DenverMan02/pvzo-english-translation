import importlib.util
import os
import sys
import tempfile
from pathlib import Path

GAME = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(GAME / "PvZ-Server-PoC"))
import player_store as store

with tempfile.TemporaryDirectory(prefix="pvz_wardrobe_") as tmp:
    store.DB_PATH = str(Path(tmp) / "test.db")
    store.init_db()
    spec = importlib.util.spec_from_file_location("wardrobe_candidate", HERE.parent / "pvz_poc_server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    srv._CLIENTDB_PATH = str(GAME / "webroot" / "clientDB.txt")
    uin = 700009876
    player, _ = store.get_or_create_player(uin)
    player["present_ticket"] = 1000
    store.save_player(uin, present_ticket=1000)
    uid = 10005001
    store.add_card(uin, uid, 501705, 300)
    card = next(c for c in store.get_cards(uin) if c[0] == uid)
    assert srv._card_avatar_plant_id(card) == 1700
    catalog = srv._working_card_avatars(1700)
    assert srv._avatar_default_status(player, uid, card, 1705, catalog)[0] == 2
    assert srv._avatar_default_status(player, uid, card, 1707, catalog)[0] == 5
    shop = srv.handle_shop_buyitem(srv.pb_varint_field(1, 201) + srv.pb_varint_field(2, 100), player)
    assert any(name == "PVZ.Cmd.Cmd_Shop_BuyItem_SC" for name, _ in shop)
    assert store.get_item_count(uin, 2018) == 100
    assert player["present_ticket"] == 0
    rejected = srv.handle_shop_buyitem(srv.pb_varint_field(1, 201) + srv.pb_varint_field(2, 1), player)
    assert rejected == [] and store.get_item_count(uin, 2018) == 100
    bought = srv.handle_card_buyavatar(srv.pb_int64_field(1, uid) + srv.pb_varint_field(2, 1705), player)
    assert any(name == "PVZ.Cmd.Cmd_Card_BuyAvatar_SC" for name, _ in bought)
    assert store.get_item_count(uin, 2018) == 0
    assert store.get_card_avatars(uin, uid)[1705]["status"] == 3
    used = srv.handle_card_useavatar(srv.pb_int64_field(1, uid) + srv.pb_varint_field(2, 1705), player)
    assert any(name == "PVZ.Cmd.Cmd_Card_UseAvatar_SC" for name, _ in used)
    assert store.get_card_avatars(uin, uid)[1705]["status"] == 4
    player, _ = store.get_or_create_player(uin)
    assert srv._card_avatar_selected(player, uid, card) == 1705
    player["ticket"] = 100
    store.save_player(uin, ticket=100)
    paid = srv.handle_shop_buyitem(srv.pb_varint_field(1, 202) + srv.pb_varint_field(2, 2), player)
    assert any(name == "PVZ.Cmd.Cmd_Shop_BuyItem_SC" for name, _ in paid)
    assert store.get_item_count(uin, 2070) == 2 and player["ticket"] == 0
    print("PASS wardrobe shop/buy/use/relogin flow")
    store.close_thread_db()
