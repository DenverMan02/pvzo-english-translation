"""Regression checks for the online-reward, daily-activity and endless-TD fixes."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_online_noend_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402


def main():
    player, _ = store.get_or_create_player(700001237)

    now = int(time.time())
    player["online_reward_data"] = json.dumps({
        "day": time.strftime("%Y%m%d"), "last_touch": now,
        "daily_time": 1800, "weekly_time": 1800,
        "last_reward_time": 0, "claimed_ids": [],
    })
    info = srv.handle_online_reward_getinfo(b"", player)[0][1]
    assert len(srv.parse_repeated_submessages(info, 8)) == 5
    assert srv.parse_varint_fields(info).get(5) == 1
    for expected in range(1, 6):
        messages = srv.handle_online_reward_takereward(b"", player)
        reply = next(body for name, body in messages
                     if name == "PVZ.Cmd.Cmd_OnlineReward_TakeReward_SC")
        assert srv.parse_varint_fields(reply).get(1) == expected
    # The client uses Total_Daily_Reward + 1 as the all-claimed cursor.
    assert srv.parse_varint_fields(srv.handle_online_reward_getinfo(b"", player)[0][1]).get(5) == 6

    daily = srv.handle_dailyactivity_getinfo(b"", player)[0][1]
    assert [srv.parse_varint_fields(x).get(1) for x in srv.parse_repeated_submessages(daily, 1)] == list(range(1, 9))

    # Endless TD must expose the whole normal-TD roster.  Ten plants make the
    # old eight-entry smoke-test cap observable while remaining independent of
    # the account's database defaults.
    player["plants"] = sorted(srv.valid_td_plants())[:10]

    # Migrate the short-lived schema-3 starter bug (rain=2) exactly once.
    player["noend_td_data"] = json.dumps({
        "mode": 1, "level": 0, "wave": 0, "score": 0, "rain": 2,
        "strength_point": 0, "active_level": 0, "taken": False,
        "schema_version": 3,
    })
    store.study_talent(player["uin"], 1104)  # sunflower large-sun talent
    mode = srv.parse_repeated_submessages(srv.handle_noendtd_getinfo(b"", player)[0][1], 1)[0]
    assert srv.parse_varint_fields(mode).get(8) == 0
    assert srv.parse_varint_fields(mode).get(7) == 200
    assert json.loads(player["noend_td_data"])["schema_version"] == 5
    talents = srv.parse_repeated_varint(srv.handle_noendtd_getinfo(b"", player)[0][1], 2)
    assert 1104 in talents
    plant_ids = [srv.parse_varint_fields(x).get(1)
                 for x in srv.parse_repeated_submessages(
                     srv.handle_noendtd_getinfo(b"", player)[0][1], 3)]
    assert plant_ids == player["plants"]
    maps = srv.parse_repeated_submessages(mode, 9)
    assert [(srv.parse_varint_fields(x).get(1), srv.parse_varint_fields(x).get(2))
            for x in maps] == [(level, 1) for level in range(1, 13)]
    assert srv.parse_varint_fields(mode).get(2) == 0
    srv.handle_noendtd_starttdgame(srv.pb_varint_field(1, 1) + srv.pb_varint_field(4, 1), player)
    mode = srv.parse_repeated_submessages(srv.handle_noendtd_getinfo(b"", player)[0][1], 1)[0]
    assert srv.parse_varint_fields(mode).get(8) == 1
    assert srv.parse_varint_fields(mode).get(7) == 200
    srv.handle_noendtd_starttdgame(srv.pb_varint_field(1, 1) + srv.pb_varint_field(4, 1), player)
    mode = srv.parse_repeated_submessages(srv.handle_noendtd_getinfo(b"", player)[0][1], 1)[0]
    assert srv.parse_varint_fields(mode).get(8) == 1  # Retries do not spend another attempt.
    assert srv.handle_noendtd_starttdgame(srv.pb_varint_field(1, 1) + srv.pb_varint_field(4, 1), player)[0][1] == b""

    # Records written by the buggy v4 build may already say "today" while
    # retaining yesterday's level.  The v5 migration must clear that stale
    # progress without granting another attempt or refilling resources.
    player["noend_td_data"] = json.dumps({
        "mode": 1, "level": 1, "wave": 4, "score": 999, "rain": 177,
        "strength_point": 8, "active_level": 2, "taken": False,
        "schema_version": 4, "challenge_date": time.strftime("%Y%m%d"),
        "challenge_times": 1,
    })
    mode = srv.parse_repeated_submessages(srv.handle_noendtd_getinfo(b"", player)[0][1], 1)[0]
    mode_fields = srv.parse_varint_fields(mode)
    assert mode_fields.get(8) == 1
    assert mode_fields.get(2) == 0
    assert json.loads(player["noend_td_data"])["schema_version"] == 5

    # A new calendar day resets the run itself as well as the attempt cursor.
    # Persistent resources/upgrades are intentionally left untouched.
    state = json.loads(player["noend_td_data"])
    state.update({"challenge_date": time.strftime("%Y%m%d", time.localtime(now - 86400)),
                  "challenge_times": 2, "level": 1, "wave": 4,
                  "active_level": 2, "score": 999, "rain": 177,
                  "strength_point": 8})
    player["noend_td_data"] = json.dumps(state)
    mode = srv.parse_repeated_submessages(srv.handle_noendtd_getinfo(b"", player)[0][1], 1)[0]
    mode_fields = srv.parse_varint_fields(mode)
    assert mode_fields.get(8) == 0
    assert mode_fields.get(2) == 0
    assert mode_fields.get(3) == 0
    assert mode_fields.get(5) == 0
    migrated = json.loads(player["noend_td_data"])
    assert migrated["active_level"] == 0
    assert migrated["rain"] == 177
    assert migrated["strength_point"] == 8
    print("online/noend/activity: PASS")


if __name__ == "__main__":
    main()
