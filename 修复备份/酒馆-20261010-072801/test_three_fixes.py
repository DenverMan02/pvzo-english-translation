"""Regression checks for card health, bar beans, and Nightmare Trial Road."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_three_fixes_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402


def main():
    uin = 700001236
    player, _ = store.get_or_create_player(uin)
    player["plants"] = store.get_player_plants(uin)

    # Dto_CardBaseInfo field 9 is avatarId.  Keeping it at zero means the
    # default skin; field 7 is the health percentage.
    full = srv.build_card_full_info(123, 1201, 1,
                                    {"atk": 10, "def_": 10, "hp": 100,
                                     "ability": 5, "quality": 1})
    base = srv.parse_repeated_submessages(full, 1)[0]
    fields = srv.parse_varint_fields(base)
    assert fields.get(7) == 100
    assert fields.get(9) == 0

    before = store.get_item_count(uin, 2003)
    messages = srv.handle_card_extract(srv.pb_varint_field(1, 1), player)
    assert store.get_item_count(uin, 2003) == before + 1
    extract = next(body for name, body in messages if name == "PVZ.Cmd.Cmd_Card_Extract_SC")
    reveal_items = srv.parse_repeated_submessages(extract, 1)
    assert len(reveal_items) == 1
    assert srv.parse_varint_fields(reveal_items[0]).get(1) != 2003
    assert any(name == "PVZ.Cmd.Cmd_Item_Update_SC" for name, _body in messages)

    info = srv.handle_newscrewup_getinfo(b"", player)[0][1]
    assert srv.parse_varint_fields(info).get(6) == srv._TR_HARD_DAILY_BATTLES
    for name in ("PVZ.Cmd.Cmd_NewScrewUp_Fight_CS",
                 "PVZ.Cmd.Cmd_NewScrewUp_Restart_CS"):
        assert name in srv.REQUEST_HANDLERS
    assert srv.handle_notice_getsyslist(b"", player)[0][0].endswith("GetSysList_SC")
    assert srv.handle_notice_getrolllist(b"", player)[0][0].endswith("GetRollList_SC")
    assert "PVZ.Cmd.Cmd_Fuben_Sweep_CS" in srv.REQUEST_HANDLERS
    assert "PVZ.Cmd.Cmd_Welfare_GetInfo_CS" in srv.REQUEST_HANDLERS
    player["level"] = 30
    before_welfare = store.get_item_count(uin, 2003)
    welfare = srv.handle_welfare_take(srv.pb_varint_field(1, 4) + srv.pb_varint_field(2, 30), player)
    assert welfare and store.get_item_count(uin, 2003) == before_welfare + 3
    print("three fixes: PASS")


if __name__ == "__main__":
    main()
