"""Regression check that repeated experience-bean requests remain usable."""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_exp_bean_repeat_"), "test.db")
store.init_db()
import pvz_poc_server as server  # noqa: E402


def main():
    player, _ = store.get_or_create_player(700004201)
    player["level"] = 50
    store.save_player(player["uin"], level=50)
    uid = 10001101
    store.add_card(player["uin"], uid, 1101, 1)
    store.add_item(player["uin"], 2002, 3)

    request = server.pb_int64_field(1, uid) + server.pb_varint_field(2, 2002)
    for expected_count in (2, 1):
        messages = server.handle_card_levelup(request, player)
        names = [name for name, _ in messages]
        assert "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC" in names, names
        assert "PVZ.Cmd.Cmd_Item_Update_SC" in names, names
        assert names.index("PVZ.Cmd.Cmd_Item_Update_SC") < names.index("PVZ.Cmd.Cmd_Card_SyncFullInfo_SC"), names
        assert store.get_item_count(player["uin"], 2002) == expected_count

    card = next(row for row in store.get_cards(player["uin"]) if row[0] == uid)
    assert card[3] == 20000, card
    print("experience bean repeated-use handler: PASS")


if __name__ == "__main__":
    main()
