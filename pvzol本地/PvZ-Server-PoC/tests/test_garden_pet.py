"""Protocol/state smoke test for the restored 阳光房宠物 system."""

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
uin = 700009991
player, _ = store.get_or_create_player(uin)
old = {k: player.get(k) for k in
       ("garden_goodwill", "ticket", "present_ticket", "garden_pet_data")}
try:
    player.update(garden_goodwill=50000, ticket=5000, present_ticket=5000,
                  garden_pet_data="")
    store.save_player(uin, garden_goodwill=50000, ticket=5000,
                      present_ticket=5000, garden_pet_data="")

    buy_body = server.pb_varint_field(1, 2) + server.pb_varint_field(2, 1)
    buy = server.handle_garden_buypet(buy_body, player)
    check("PVZ.Cmd.Cmd_Garden_BuyPet_SC" in [name for name, _body in buy],
          "buy response missing")
    player, _ = store.get_or_create_player(uin)
    state = server._garden_pet_states(player)
    check(state[2]["owned"] and state[2]["forever"], "permanent purchase not saved")

    use = server.handle_garden_usepet(server.pb_varint_field(1, 2), player)
    check("PVZ.Cmd.Cmd_Garden_UsePet_SC" in [name for name, _body in use],
          "use response missing")
    player, _ = store.get_or_create_player(uin)
    check(server._garden_active_pet_id(player, int(time.time())) == 2,
          "selected pet not persisted")
    check(server._garden_pending_exp(server.load_garden_seeds()[1], 1000, 1020, 2) == 13,
          "pet multiplier was not applied")
    print("garden pet: PASS")
finally:
    store.save_player(uin, **old)
