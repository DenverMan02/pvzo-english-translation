"""Focused coverage for Nutrition SuperTrade's protocol failure paths and paid-in-full rule."""
import importlib.util
import itertools
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
GAME = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))


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
    server = load("nutrition_supertrade_test", HERE.parent / "pvz_poc_server.py")
    server._CLIENTDB_PATH = str(GAME / "webroot" / "clientDB.txt")
    uin = 899101
    player, _ = store.get_or_create_player(uin)

    request = server.pb_varint_field(1, 1) + server.pb_varint_field(2, 3)   # 200000 coins, dissolve 绿蓝

    # An unknown CostType/ChangeType pair is rejected outright.
    assert server.handle_nutrition_supertrade(
        server.pb_varint_field(1, 0) + server.pb_varint_field(2, 3),
        player) == [server.puppet_error(6316)]

    # A valid trade with too few coins reports the existing localized error and
    # does not mutate currency, reagent inventory, or bottle exp.
    store.save_player(uin, coin=0)
    player["coin"] = 0
    reply = server.handle_nutrition_supertrade(request, player)
    assert reply == [server.puppet_error(202)]
    assert store.get_or_create_player(uin)[0]["coin"] == 0
    assert store.get_reagents(uin) == []

    # The dialog promises "一次性将指定金币配成药剂": the whole chosen amount is spent, and a FULL
    # reagent bag must not truncate the trade (the client never reads NUTRITION_USE_BAG_MAX, so a
    # capped bag only ever cost the player part of the amount they picked).
    store.save_player(uin, coin=500_000)
    player["coin"] = 500_000
    for _ in range(server._NUTRITION_BAG_MAX):
        store.add_reagent(uin, store.next_reagent_uid(uin), 60101)
    bag_before = len(store.get_reagents(uin))
    rolls = itertools.cycle((60201, 60401))   # quality 3 dissolves; quality 5 is retained
    server._nutrition_roll = lambda _change_type: next(rolls)
    reply = server.handle_nutrition_supertrade(request, player)
    names = [name for name, _ in reply]
    assert "PVZ.Cmd.Cmd_Nutrition_SuperTrade_SC" in names
    assert "PVZ.Cmd.Cmd_LogicError_SC" not in names     # no bogus "bag full" toast any more
    trade = next(body for name, body in reply if name.endswith("Nutrition_SuperTrade_SC"))
    values = server.parse_varint_fields(trade)
    assert values[4] == 200_000                        # charged the full selected tier
    assert values[6] == 0                              # never reports a stop reason
    assert store.get_or_create_player(uin)[0]["coin"] == 300_000
    # 20 brews -> 10 dissolved + 10 retained, all 10 retained landed even though the bag was full.
    assert len(store.get_reagents(uin)) == bag_before + 10
    assert values[1] == 0 and values[2] == 10 * 40     # 60201 level-1 exp is 40
    assert values[3] == 2                              # 20 brews // 10 -> 2 黄金原液
    assert any(name.endswith("Nutrition_GetReagentAll_SC") for name, _ in reply)
    store.close_thread_db()

print("PASS: SuperTrade charges the full selected amount and never truncates on a full bag")
