"""Enhance/star-up should sync one card and start animations without a full-package rebuild."""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_star_animation_"), "test.db")
store.init_db()
import pvz_poc_server as server  # noqa: E402


def main():
    player, _ = store.get_or_create_player(700004206)
    player["level"] = 50
    player["coin"] = 10000000
    store.save_player(player["uin"], level=50, coin=10000000)
    uid = 10001101
    store.add_card(player["uin"], uid, 1101, 1)
    for index in range(1, 105):
        store.add_card(player["uin"], uid + index, 1101, 1)
    store.set_card_forge_level(player["uin"], uid, 4)
    store.add_item(player["uin"], 51100, 60)

    start = time.perf_counter()
    forge_messages = server.handle_card_forge(server.pb_int64_field(1, uid), player)
    forge_elapsed = time.perf_counter() - start
    forge_names = [name for name, _ in forge_messages]
    forge_success = "PVZ.Cmd.Cmd_Card_Forge_SC"
    assert forge_names[-1] == forge_success, forge_names
    assert "PVZ.Cmd.Cmd_Card_GetPackage_SC" not in forge_names, forge_names
    for preceding in ("PVZ.Cmd.Cmd_Card_SyncFullInfo_SC",
                      "PVZ.Cmd.Cmd_SyncCoin_SC"):
        assert forge_names.index(preceding) < forge_names.index(forge_success), forge_names
    assert store.get_card_forge_level(player["uin"], uid) == 5

    start = time.perf_counter()
    messages = server.handle_card_usecardupgrade(server.pb_int64_field(1, uid), player)
    star_elapsed = time.perf_counter() - start
    names = [name for name, _ in messages]
    success = "PVZ.Cmd.Cmd_Card_UseCardUpgrade_SC"
    assert names.count(success) == 1, names
    assert names[-1] == success, names
    assert "PVZ.Cmd.Cmd_Card_GetPackage_SC" not in names, names
    for preceding in ("PVZ.Cmd.Cmd_Card_SyncFullInfo_SC",
                      "PVZ.Cmd.Cmd_Item_Update_SC",
                      "PVZ.Cmd.Cmd_SyncCoin_SC"):
        assert names.index(preceding) < names.index(success), names
    assert store.get_item_count(player["uin"], 51100) == 0
    assert store.get_card_forge_level(player["uin"], uid) == 0
    assert any(card[0] == uid and card[1] == 1102
               for card in store.get_cards(player["uin"]))
    print(f"105-card animation responses: forge={forge_elapsed:.3f}s star={star_elapsed:.3f}s PASS")


if __name__ == "__main__":
    main()
