"""Regression coverage for the real Dto_IdNum one-click card upgrade request."""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_fast_level_"), "test.db")
store.init_db()
import pvz_poc_server as server  # noqa: E402


def main():
    player, _ = store.get_or_create_player(700001238)
    player["level"] = 50
    store.save_player(player["uin"], level=50)
    uid = 10001101
    store.add_card(player["uin"], uid, 1101, 1)
    store.add_item(player["uin"], 2002, 2)

    # Cmd_Card_FasterLevelUp_CS.costItemList is repeated Dto_IdNum:
    # {id=2002, num=2}.  The old server read fields 2/3 and therefore used 0.
    entry = server.pb_varint_field(1, 2002) + server.pb_varint_field(2, 2)
    request = (server.pb_int64_field(1, uid)
               + server.pb_tag(2, 2) + server.uvarint(len(entry)) + entry)
    messages = server.handle_card_fasterlevelup(request, player)
    names = [name for name, _ in messages]
    assert "PVZ.Cmd.Cmd_Card_FasterLevelUp_SC" in names
    assert store.get_item_count(player["uin"], 2002) == 0
    card = next(row for row in store.get_cards(player["uin"]) if row[0] == uid)
    assert card[3] == 20000, card
    print("card faster level-up: PASS")


if __name__ == "__main__":
    main()
