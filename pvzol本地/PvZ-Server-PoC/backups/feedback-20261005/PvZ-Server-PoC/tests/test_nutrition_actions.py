"""Focused temp-DB checks for nutrition's previously inert client actions."""
import importlib.util
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
GAME = HERE.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


with tempfile.TemporaryDirectory() as td:
    store = load("player_store", HERE.parent / "player_store.py")
    store.DB_PATH = str(pathlib.Path(td) / "players.db")
    store.init_db()
    server = load("nutrition_server_candidate", HERE.parent / "pvz_poc_server.py")
    server._CLIENTDB_PATH = str(GAME / "webroot" / "clientDB.txt")
    uin = 899001
    player, _ = store.get_or_create_player(uin)
    store.save_player(uin, coin=2000000, present_ticket=300)
    player.update(coin=2000000, present_ticket=300)
    store.add_item(uin, 2026, 1)

    def pb(**fields):
        return b"".join(server.pb_varint_field(int(k), v) for k, v in fields.items())

    # Ticket first, then diamond; replay with no balance must give no free reagent.
    reply = server.handle_nutrition_ticketactivate(b"", player)
    assert any(name.endswith("TicketActivate_SC") for name, _ in reply)
    assert store.get_item_count(uin, 2026) == 0
    assert len(server._nutrition_temp_list(player)) == 1
    reply = server.handle_nutrition_ticketactivate(b"", player)
    assert reply and player["present_ticket"] == 100
    assert len(server._nutrition_temp_list(player)) == 2
    assert server.handle_nutrition_ticketactivate(b"", player) == []
    assert len(server._nutrition_temp_list(player)) == 2

    # Configured exchange price and actual reagent persist across a new player load.
    offers = server._nutrition_exchange_table()
    assert offers[1][1] == 30
    store.save_player(uin, nutrition_gold=30)
    player["nutrition_gold"] = 30
    reply = server.handle_nutrition_exchange(pb(**{"1": 1}), player)
    assert any(name.endswith("Exchange_SC") for name, _ in reply)
    assert store.get_reagents(uin)[0]["base_id"] == offers[1][0]
    assert store.get_or_create_player(uin)[0]["nutrition_gold"] == 0
    assert server.handle_nutrition_exchange(pb(**{"1": 1}), player) == []

    # A weaker source adds partial exp; replay cannot consume it twice.
    first = store.get_reagents(uin)[0]
    uid2 = store.next_reagent_uid(uin)
    store.add_reagent(uin, uid2, 60101)
    reply = server.handle_nutrition_merge(pb(**{"1": uid2, "2": first["uid"]}), player)
    assert any(name.endswith("SyncFullInfo_SC") for name, _ in reply)
    merged = store.get_reagents(uin)
    assert len(merged) == 1 and merged[0]["exp"] > 0
    assert server.handle_nutrition_merge(pb(**{"1": uid2, "2": first["uid"]}), player) == []

    # Bulk trade consumes exactly one coin tier and grows the two bank pools.
    old = store.get_or_create_player(uin)[0]
    server._nutrition_roll = lambda _change_type: 60201  # quality 3 is decomposed for changeType 3
    reply = server.handle_nutrition_supertrade(pb(**{"1": 1, "2": 3}), player)
    new = store.get_or_create_player(uin)[0]
    assert any(name.endswith("SuperTrade_SC") for name, _ in reply)
    assert old["coin"] - new["coin"] == 200000
    assert new["nutrition_bottle_exp"] > old["nutrition_bottle_exp"]
    assert new["nutrition_gold"] > old["nutrition_gold"]
    assert server.handle_nutrition_supertrade(pb(**{"1": 0, "2": 3}), player) == [server.puppet_error(6316)]
    print("PASS: ticket/diamond, configured exchange, merge persistence/replay, bulk trade")
    store.close_thread_db()
