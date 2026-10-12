"""Daily quest claim regression with disposable SQLite and two client sessions."""
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import player_store as store

store.DB_PATH = str(Path(tempfile.mkdtemp(prefix="pvz_daily_claim_")) / "test.db")
store.init_db()
import pvz_poc_server as server


def quest_status(player):
    info = server.handle_dailyquest_getinfo(b"", player)[0][1]
    return {server.parse_varint_fields(dto)[1]: server.parse_varint_fields(dto)
            for dto in server.parse_repeated_submessages(info, 1)}


def claim(player, qid):
    return server.handle_dailyquest_takereward(server.pb_varint_field(1, qid), player)


def test_claims():
    player, _ = store.get_or_create_player(77223311)
    store.save_player(player["uin"], level=60)
    player["level"] = 60
    states = quest_status(player)
    assert set(states) == {1, 50, 43}
    assert states[1][3] == 3
    assert states[50][3] == 2
    result = claim(player, 1)
    assert any(name.endswith("TakeReward_SC") for name, _ in result)
    sync = next(body for name, body in result if name.endswith("DailyQuest_Sync_SC"))
    dto = server.parse_repeated_submessages(sync, 1)[0]
    assert server.parse_varint_fields(dto)[3] == 4
    assert store.get_item_count(player["uin"], 2012) == 2
    assert store.get_item_count(player["uin"], 2006) == 1
    assert quest_status(player)[1][3] == 4
    # A second connection has a stale player object, but the DB owns the flag.
    stale, _ = store.get_or_create_player(player["uin"])
    duplicate = claim(stale, 1)
    assert not any(name.endswith("TakeReward_SC") for name, _ in duplicate)
    assert store.get_item_count(player["uin"], 2012) == 2
    server._daily_quest_touch(player, "fuben", 3)
    assert quest_status(player)[50][3] == 3
    claim(player, 50)
    assert quest_status(player)[50][3] == 4
    server._daily_quest_touch(player, "question", 1)
    assert quest_status(player)[43][3] == 3
    claim(player, 43)
    assert quest_status(player)[43][3] == 4
    # Unsupported old placeholder ID must never grant a second reward.
    assert not any(name.endswith("TakeReward_SC") for name, _ in claim(player, 4))
    # Migrate previously claimed old-ID records without paying them again.
    old = {"day": time.strftime("%Y%m%d"), "quests": {
        "1": {"count": 1, "taken": True},
        "2": {"count": 1, "taken": True},
        "3": {"count": 5, "taken": False}}}
    store.save_player(player["uin"], daily_quest_data=json.dumps(old))
    assert quest_status(player)[50][3] == 4
    assert quest_status(player)[43][3] == 3
    assert not any(name.endswith("TakeReward_SC") for name, _ in claim(player, 50))
    # Next local day resets eligibility and progress, without changing inventory.
    old["day"] = "19990101"
    store.save_player(player["uin"], daily_quest_data=json.dumps(old))
    states = quest_status(player)
    assert states[1][3] == 3 and states[50][3] == 2
    assert store.get_item_count(player["uin"], 2012) == 9


def test_concurrent_claim():
    first, _ = store.get_or_create_player(77223312)
    store.save_player(first["uin"], level=60)
    first["level"] = 60
    quest_status(first)
    second, _ = store.get_or_create_player(first["uin"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda player: claim(player, 1), (first, second)))
    assert sum(any(name.endswith("TakeReward_SC") for name, _ in reply) for reply in outcomes) == 1
    assert store.get_item_count(first["uin"], 2012) == 2


if __name__ == "__main__":
    test_claims()
    test_concurrent_claim()
    print("PASS: configured IDs, UI sync, one payout, stale/concurrent session, migration, day rollover")
