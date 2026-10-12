"""Regression coverage for garden fertilizer, watering and pet auto-harvest."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import player_store as store
import pvz_poc_server as server


def check(condition, message):
    if not condition:
        raise AssertionError(message)


store.init_db()
uin = 700009995
player, _ = store.get_or_create_player(uin)
player_fields = ("coin", "ticket", "present_ticket", "garden_exp", "garden_goodwill",
                 "garden_pet_data", "garden_kettle_day", "garden_kettlelimit",
                 "garden_autouse", "garden_autofertilizer", "level")
old_player = {key: player.get(key) for key in player_fields}
old_items = {item_id: store.get_item_count(uin, item_id) for item_id in (1009, 1025)}
try:
    now = int(time.time())
    player.update(coin=100000, ticket=40, present_ticket=0, garden_exp=0,
                  level=20,
                  garden_goodwill=0, garden_pet_data='{"2":{"owned":1,"forever":1,"used":1}}',
                  garden_kettle_day="", garden_kettlelimit=10)
    store.save_player(uin, **{key: player[key] for key in player_fields})
    store.unlock_garden_field(uin, 1)
    store.set_garden_pot(uin, 1, 1, now - 40)
    store.update_garden_pot_meta(uin, 1, 1025, now - 40)

    player, _ = store.get_or_create_player(uin)
    buy = server.handle_garden_buyfertilizer(
        server.pb_varint_field(1, 1025) + server.pb_varint_field(2, 1), player)
    check("PVZ.Cmd.Cmd_Garden_BuyFertilizer_SC" in [name for name, _ in buy],
          "fertilizer purchase was not acknowledged")
    check(store.get_item_count(uin, 1025) == 1 and player["coin"] == 99000,
          "ordinary fertilizer did not charge 1000 coins and add one item")

    player, _ = store.get_or_create_player(uin)
    use = server.handle_garden_fertilize(
        server.pb_varint_field(1, 1) + server.pb_varint_field(2, 1025), player)
    check("PVZ.Cmd.Cmd_Garden_Fertilize_SC" in [name for name, _ in use],
          "fertilize response missing")
    meta = store.get_garden_pot_details(uin)[1]
    check(meta["fertilizer_id"] == 1025 and meta["last_fertilizer_time"] >= now,
          "fertilizer metadata was not persisted")

    player, _ = store.get_or_create_player(uin)
    # The garden client sends the MallItemTemplate id (210), not the item id
    # (1009), when it auto-opens the golden-can purchase dialog.
    buy_kettle = server.handle_shop_buyitem(
        server.pb_varint_field(1, 210) + server.pb_varint_field(2, 1), player)
    check("PVZ.Cmd.Cmd_Shop_BuyItem_SC" in [name for name, _ in buy_kettle],
          "generic watering-can purchase was not acknowledged")
    check(store.get_item_count(uin, 1009) == 1 and player["ticket"] == 20,
          "watering-can purchase did not charge 20 diamonds and add one can")

    player, _ = store.get_or_create_player(uin)
    water = server.handle_garden_water(server.pb_varint_field(1, 1), player)
    check("PVZ.Cmd.Cmd_Garden_Water_SC" in [name for name, _ in water],
          "watering response missing")
    check(store.get_item_count(uin, 1009) == 0 and player["garden_kettlelimit"] == 9,
          "watering can or daily kettle counter was not consumed")

    # Selecting a fertilizer while auto-use is off must remain the selected
    # manual fertilizer (the old handler incorrectly reset it to zero).
    selected = server.handle_garden_autousefertilizer(
        server.pb_bool(1, False) + server.pb_varint_field(2, 1025), player)
    check(server.parse_varint_fields(selected[0][1]).get(2) == 1025
          and player["garden_autofertilizer"] == 1025,
          "fertilizer selection was lost when auto-use was disabled")

    # Level 20 is the first requirement for Lv2 and costs 50,000 coins.
    before_coin = int(player["coin"])
    upgraded = server.handle_garden_potuplevel(server.pb_varint_field(1, 1), player)
    check("PVZ.Cmd.Cmd_Garden_PotUpLevel_SC" in [name for name, _ in upgraded]
          and store.get_garden_pot_details(uin)[1]["level"] == 2
          and int(player["coin"]) == before_coin - 50000,
          "pot level upgrade did not persist or charge its configured cost")

    # Empty fieldid list is the exact request emitted by the pet's 20-second timer.
    player, _ = store.get_or_create_player(uin)
    auto = server.handle_garden_harvest(b"", player)
    harvest_body = next(body for name, body in auto
                        if name == "PVZ.Cmd.Cmd_Garden_Harvest_SC")
    check(len(server.parse_repeated_submessages(harvest_body, 1)) == 1,
          "pet auto-harvest did not return a repeated result")
    player, _ = store.get_or_create_player(uin)
    check(int(player["garden_exp"]) > 0, "pet auto-harvest did not bank experience")
    print("garden actions: PASS")
finally:
    store.set_garden_pot(uin, 1, 0, 0)
    store.update_garden_pot_level(uin, 1, 1)
    for item_id, old_count in old_items.items():
        current = store.get_item_count(uin, item_id)
        if current != old_count:
            store.add_item(uin, item_id, old_count - current)
    store.save_player(uin, **old_player)
