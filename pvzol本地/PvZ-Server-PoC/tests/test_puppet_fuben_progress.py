"""Regression tests for ordered puppet-fuben waves and live progress synchronization."""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_puppet_fuben_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402


UIN = 700004212
FIGHT_SC = "PVZ.Cmd.Cmd_PuppetFuben_Fight_SC"
STAGE_SYNC_SC = "PVZ.Cmd.Cmd_PuppetFuben_SyncStageInfo_SC"
STAGE_INFO_SC = "PVZ.Cmd.Cmd_PuppetFuben_GetStageInfo_SC"
failed = []


def check(name, ok, actual=None):
    if not ok:
        failed.append(name)
    print("%-4s %s%s" % ("PASS" if ok else "FAIL", name,
                         "" if actual is None else " (%r)" % (actual,)))


def fields(body):
    return srv.parse_varint_fields(body or b"")


def message(messages, name):
    return next((body for command, body in messages if command == name), None)


def fight(player, wave, won):
    real = srv.resolve_battle
    srv.resolve_battle = lambda left, right: (won, 0, 0)
    try:
        request = (srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
                   + srv.pb_varint_field(3, 1) + srv.pb_varint_field(4, wave))
        return srv.handle_puppetfuben_fight(request, player)
    finally:
        srv.resolve_battle = real


def stage_sync_fields(messages):
    body = message(messages, STAGE_SYNC_SC)
    if body is None:
        return None, None
    stage_dtos = srv.parse_repeated_submessages(body, 3)
    return fields(body), fields(stage_dtos[0]) if stage_dtos else None


def formation_request(entries):
    return b"".join(
        srv.pb_bytes(1, srv.pb_varint_field(1, pos) + srv.pb_varint_field(2, uid))
        for pos, uid in entries)


def check_legacy_migration():
    old_db = store.DB_PATH
    legacy_db = os.path.join(tempfile.mkdtemp(prefix="pvz_puppet_legacy_"), "legacy.db")
    legacy_uin = 700004214
    con = sqlite3.connect(legacy_db)
    con.execute("CREATE TABLE puppet_fuben (uin INTEGER, mode INTEGER, stage_id INTEGER, "
                "wave INTEGER DEFAULT 0, times INTEGER DEFAULT 0, day TEXT DEFAULT '', "
                "PRIMARY KEY(uin, mode, stage_id))")
    con.execute("INSERT INTO puppet_fuben (uin,mode,stage_id,wave,times,day) VALUES (?,?,?,?,?,?)",
                (legacy_uin, 1, 1, 2, 3, "20260926"))
    con.commit()
    con.close()
    store.DB_PATH = legacy_db
    store.init_db()
    migrated = store.puppet_fuben_progress(legacy_uin, 1, 1).get(1)
    with sqlite3.connect(legacy_db) as con:
        archived_rows = con.execute("SELECT wave,times,day FROM puppet_fuben_legacy_chapterless "
                                     "WHERE uin=? AND mode=1 AND stage_id=1", (legacy_uin,)).fetchone()
    store.init_db()  # Idempotent restart must neither recopy nor discard the preserved legacy table.
    store.save_puppet_stage(legacy_uin, 1, 2, 1, wave=4, times=7, day="20260926")
    chapter_one = store.puppet_fuben_progress(legacy_uin, 1, 1).get(1)
    chapter_two = store.puppet_fuben_progress(legacy_uin, 1, 2).get(1)
    store.DB_PATH = old_db
    check("old schema migrates legacy row without losing progress", migrated == (2, 3, "20260926"), migrated)
    check("legacy source table remains as a read-only copy after idempotent startup",
          archived_rows == (2, 3, "20260926"), archived_rows)
    check("same stage id stores independent chapter progress",
          chapter_one == (2, 3, "20260926") and chapter_two == (4, 7, "20260926"),
          (chapter_one, chapter_two))


def main():
    player, _created = store.get_or_create_player(UIN)
    store.save_player(UIN, level=60, coin=1_000_000, ticket=1_000)
    player.update(level=60, coin=1_000_000, ticket=1_000)
    player["plants"] = store.get_player_plants(UIN)
    srv.ensure_player_cards(player)
    srv.puppet_starter_grant(player)

    card_uid = store.get_cards(UIN)[0][0]
    srv.handle_puppetfuben_setformation(formation_request([(3, card_uid)]), player)
    placed = store.get_formation(UIN, srv._DUMMY_FUBEN_TEAM)
    srv.handle_puppetfuben_setformation(formation_request([(pos, 0) for pos in (3, 7, 9, 11, 13)]), player)
    cleared = store.get_formation(UIN, srv._DUMMY_FUBEN_TEAM)
    check("empty plant formation replaces the previously saved team",
          placed == {3: card_uid} and cleared == {}
          and srv._puppet_fuben_placements(player) == {}, (placed, cleared))

    energy_before = srv.puppet_energy(player)
    skip = fight(player, 2, True)
    progress = store.puppet_fuben_progress(UIN, 1, 1).get(1, (0, 0, ""))
    check("fresh stage rejects skipping its first wave", fields(message(skip, FIGHT_SC)) == {}
          and progress[0] == 0 and progress[1] == 0
          and srv.puppet_energy(player) == energy_before,
          (fields(message(skip, FIGHT_SC)), progress, srv.puppet_energy(player)))

    first_win = fight(player, 1, True)
    chapter_body, stage = stage_sync_fields(first_win)
    check("winning wave 1 syncs star and chapter attempt count", stage == {1: 1, 2: 1, 3: 0, 4: 1, 5: 0}
          and chapter_body.get(4) == 1, (chapter_body, stage))

    energy_before = srv.puppet_energy(player)
    duplicate = fight(player, 1, True)
    progress = store.puppet_fuben_progress(UIN, 1, 1).get(1, (0, 0, ""))
    check("cleared wave cannot be replayed or charged again", fields(message(duplicate, FIGHT_SC)) == {}
          and STAGE_SYNC_SC not in [name for name, _body in duplicate]
          and progress[0] == 1 and progress[1] == 1
          and srv.puppet_energy(player) == energy_before,
          (fields(message(duplicate, FIGHT_SC)), progress, srv.puppet_energy(player)))

    second_loss = fight(player, 2, False)
    chapter_body, stage = stage_sync_fields(second_loss)
    check("failed wave syncs updated attempts without advancing star",
          stage == {1: 1, 2: 1, 3: 0, 4: 2, 5: 0} and chapter_body.get(4) == 2,
          (chapter_body, stage))
    queried = srv.handle_puppetfuben_getstageinfo(
        srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1), player)
    check("live sync count matches chapter stage-info query", fields(queried).get(4) == 2,
          (chapter_body.get(4), fields(queried).get(4)))

    # Finish wave 2 after its loss, then clear the remaining waves.
    for wave in (2, 3, 4, 5):
        msgs = fight(player, wave, True)
    all_stages = srv.handle_puppetfuben_getstageinfo(
        srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1), player)
    check("five distinct wins unlock sweep after retries",
          fields(all_stages).get(4) == 6
          and fields(srv.parse_repeated_submessages(all_stages, 3)[0]).get(2) == 5
          and fields(srv.parse_repeated_submessages(all_stages, 3)[0]).get(5) == 1,
          (fields(all_stages).get(4), fields(srv.parse_repeated_submessages(all_stages, 3)[0])))

    sweep = srv.handle_puppetfuben_sweep(
        srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
        + srv.pb_varint_field(3, 1) + srv.pb_varint_field(4, 1), player)
    chapter_body, stage = stage_sync_fields(sweep)
    check("sweep syncs refreshed stage attempts and chapter total",
          fields(message(sweep, "PVZ.Cmd.Cmd_PuppetFuben_Sweep_SC")).get(4) == 1
          and chapter_body.get(4) == 7 and stage == {1: 1, 2: 5, 3: 0, 4: 7, 5: 1},
          (chapter_body, stage))
    energy_before = srv.puppet_energy(player)
    finished_replay = fight(player, 5, True)
    progress = store.puppet_fuben_progress(UIN, 1, 1).get(1, (0, 0, ""))
    check("fully cleared stage cannot be replayed after sweep",
          fields(message(finished_replay, FIGHT_SC)) == {} and progress[0] == 5
          and progress[1] == 7 and srv.puppet_energy(player) == energy_before,
          (progress, srv.puppet_energy(player)))

    # Stage 2 becomes available only after stage 1 is complete. Its sync count must remain chapter-wide.
    request = (srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
               + srv.pb_varint_field(3, 2) + srv.pb_varint_field(4, 1))
    real = srv.resolve_battle
    srv.resolve_battle = lambda left, right: (False, 0, 0)
    try:
        stage_two_loss = srv.handle_puppetfuben_fight(request, player)
    finally:
        srv.resolve_battle = real
    chapter_body, stage = stage_sync_fields(stage_two_loss)
    check("new stage failure reports cumulative chapter attempts", chapter_body.get(4) == 8
          and stage == {1: 2, 2: 0, 3: 0, 4: 1, 5: 0}, (chapter_body, stage))

    store.save_puppet_stage(UIN, 1, 2, 1, wave=3, times=4, day=srv._zi_today())
    ch1_progress = srv._dummy_fuben_progress(player, 1, 1).get(1)
    ch2_progress = srv._dummy_fuben_progress(player, 1, 2).get(1)
    check("chapter 2 stage 1 does not overwrite chapter 1 stage 1",
          ch1_progress == (5, 7) and ch2_progress == (3, 4), (ch1_progress, ch2_progress))

    check_legacy_migration()

    if failed:
        print("\nFAILURES: " + ", ".join(failed))
        return 1
    print("\nAll puppet fuben progress regressions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




