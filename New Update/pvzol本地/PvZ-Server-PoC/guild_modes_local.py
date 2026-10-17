"""Offline guild dungeon and competition routes backed by the local guild save.

The original online services are absent.  Keep dungeon progress shared by real
guild members and expose a real, persistent competition registration list; do
not invent opposing guilds or award wins for matches that were never played.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import time

import guild_local as guild
import player_store as store


_API = {}
_CHAPTERS = None
_STAGES = None
_STAGE_HP = 1000000


def _records(table):
    rows = (_API.get("read_clientdb_table") or (lambda _name: []))(table)
    if not rows:
        return []
    return [dict(zip(rows[0], row)) for row in rows[1:] if len(row) >= len(rows[0])]


def _templates():
    global _CHAPTERS, _STAGES
    if _CHAPTERS is None:
        _CHAPTERS = {}
        for row in _records("FubenChapterForUnion.txt"):
            try:
                _CHAPTERS[int(row["id"])] = row
            except (KeyError, ValueError):
                continue
        _STAGES = {}
        for row in _records("fuben.txt"):
            try:
                if int(row["mode"]) == 3:
                    _STAGES.setdefault(int(row["chapterId"]), []).append(int(row["stageId"]))
            except (KeyError, ValueError):
                continue
        for stages in _STAGES.values():
            stages.sort()
    return _CHAPTERS, _STAGES


def _initialize():
    with guild._db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS local_guild_dungeon (
            guild_id INTEGER NOT NULL, chapter_id INTEGER NOT NULL,
            stage_id INTEGER NOT NULL, open_time INTEGER NOT NULL,
            reset_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id, chapter_id))""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_guild_dungeon_fights (
            guild_id INTEGER NOT NULL, uin INTEGER NOT NULL, day TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id, uin, day))""")
        # Per-chapter daily attempts. FubenChapterForUnion.txt declares `fightCount` attempts per
        # member per chapter per day and Dto_GuildFuben_ChapterInfo.times is rendered by
        # UnionFubenListItemMediator as "今日剩余次数: {times}/{fightCount}" — this table is what
        # `times` is derived from. Keying on the day resets the quota at 0:00 for free.
        con.execute("""CREATE TABLE IF NOT EXISTS local_guild_dungeon_chapter_fights (
            guild_id INTEGER NOT NULL, uin INTEGER NOT NULL, chapter_id INTEGER NOT NULL,
            day TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id, uin, chapter_id, day))""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_guild_dungeon_claims (
            guild_id INTEGER NOT NULL, uin INTEGER NOT NULL, day TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id, uin, day))""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_guild_competition (
            season INTEGER NOT NULL, guild_id INTEGER NOT NULL,
            joined_at INTEGER NOT NULL,
            PRIMARY KEY(season, guild_id))""")


def _member_guild(con, player):
    member = guild._member(con, player.get("uin", 0))
    return (member, guild._guild(con, member["guild_id"])) if member else (None, None)


def _ensure_first_chapter(con, guild_id, chapters, stages):
    if not chapters:
        return
    first = min(chapters)
    first_stage = (stages.get(first) or [1])[0]
    con.execute("INSERT OR IGNORE INTO local_guild_dungeon"
                "(guild_id,chapter_id,stage_id,open_time) VALUES(?,?,?,?)",
                (guild_id, first, first_stage, int(time.time())))


def _stage_dto(chapter, stage, passed=False, guild_id=0, reset=0):
    maximum=remaining=_STAGE_HP
    if guild_id:
        import guild_damage_repairs
        values=guild_damage_repairs.stage_hp(guild_id,chapter,stage,reset)
        if values:maximum,remaining=values
    return (guild._v(1, chapter) + guild._v(2, stage)
            + guild._v(6, maximum) + guild._v(7, 0 if passed else remaining))


def _fight_count(row):
    """FubenChapterForUnion.txt's fightCount — one member's daily attempts in this chapter."""
    try:
        return max(0, int(row.get("fightCount") or 0))
    except (TypeError, ValueError):
        return 0


def _fights_today(con, guild_id, uin, chapter, day):
    row = con.execute("SELECT count FROM local_guild_dungeon_chapter_fights "
                      "WHERE guild_id=? AND uin=? AND chapter_id=? AND day=?",
                      (guild_id, uin, chapter, day)).fetchone()
    return int(row[0]) if row else 0


def _consume_fight(con, guild_id, uin, chapter, day):
    con.execute("INSERT INTO local_guild_dungeon_chapter_fights"
                "(guild_id,uin,chapter_id,day,count) VALUES(?,?,?,?,1) "
                "ON CONFLICT(guild_id,uin,chapter_id,day) DO UPDATE SET count=count+1",
                (guild_id, uin, chapter, day))


def _remaining_fights(con, guild_id, uin, row, day):
    """Today's unused attempts in `row`'s chapter (never negative)."""
    return max(0, _fight_count(row) - _fights_today(con, guild_id, uin, int(row["id"]), day))


def _chapter_dto(row, state, stages, remaining=None):
    chapter = int(row["id"])
    stage_list = stages.get(chapter) or [1]
    stage = int(state["stage_id"]) if state else stage_list[0]
    passed = bool(state and stage > stage_list[-1])
    if passed:
        stage = stage_list[-1]
    # `times` is 今日剩余次数 (see UNION_FUBEN_LIST_TIMES). It used to be filled with the chapter's
    # reset_count, which starts at 0 — every chapter therefore read "今日剩余次数: 0/2" and the
    # panel refused the challenge with UNION_FUBEN_TIMES_OVER ("今日联盟副本次数已用尽").
    if remaining is None:
        remaining = _fight_count(row) if state else 0
    return (guild._v(1, chapter) + guild._v(2, int(remaining))
            + guild._v(3, int(state["open_time"]) if state else 0)
            + guild._v(4, 1 if state else 0)
            + guild._m(5, _stage_dto(chapter, stage, passed, state['guild_id'] if state else 0, state['reset_count'] if state else 0))
            + guild._v(6, int(passed)))


def _formation(player):
    _API["ensure_player_cards"](player)
    placements = _API["default_formation_placements"](player)
    return _API["build_formation_info"](player, 1, placements)


def _set_formation(data, player):
    formation = guild._submessage(data, 1)
    positions = {}
    for wire, card in guild._fields(formation).get(1, []):
        if wire == 2:
            pos, uid = guild._int(card, 1), guild._int(card, 2)
            if pos and uid:
                positions[pos] = uid
    if positions and _API.get("set_formation"):
        _API["set_formation"](player["uin"], 1, positions)
    return b""


def _fight_type(report, value):
    # Replace just the report type, retaining the actual replay, units and loot.
    result = b""
    for field, values in guild._fields(report).items():
        if field == 5:
            continue
        for wire, item in values:
            result += guild._v(field, item) if wire == 0 else guild._m(field, item)
    return result + guild._v(5, value)


def _current_season(now=None):
    now = datetime.fromtimestamp(now or time.time())
    year, week, _ = now.isocalendar()
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return year * 100 + week, int((monday + timedelta(days=7)).timestamp())


def _guild_fight_info(con, row, season):
    gid = int(row["guild_id"])
    members = con.execute("SELECT COUNT(*),COALESCE(SUM(fight_power),0) FROM local_guild_members WHERE guild_id=?",
                          (gid,)).fetchone()
    signed = con.execute("SELECT 1 FROM local_guild_competition WHERE season=? AND guild_id=?",
                         (season, gid)).fetchone()
    flag = b"".join(guild._v(i, row[key]) for i, key in enumerate(
        ("color_id", "color2_id", "surface_id", "sign_id"), 1))
    # field 7 is Dto_GuildFight_GuildInfo.credits — the guild's live 联盟竞价 offer
    # (Cmd_GuildBid_CS.guildExp), which is what the 植物大作战 list shows next to each guild.
    credits = int(guild._row_get(row, "bid_exp"))
    return (guild._v(1, gid) + guild._s(2, row["name"]) + guild._v(3, row["level"])
            + guild._v(4, members[0] if signed else 0) + guild._v(5, members[0])
            + guild._v(6, members[1]) + guild._v(7, credits)
            + guild._v(8, 2 if signed else 1) + guild._m(40, flag))


def register(api):
    global _API, _CHAPTERS, _STAGES
    _API = dict(api)
    _CHAPTERS = _STAGES = None
    _initialize()

    def chapters(_data, player):
        templates, stages = _templates()
        with guild._db() as con:
            member, own_guild = _member_guild(con, player)
            if not member:
                return guild._v(2, 0) + guild._v(3, 0)
            gid = own_guild["guild_id"]
            _ensure_first_chapter(con, gid, templates, stages)
            states = {row["chapter_id"]: row for row in con.execute(
                "SELECT * FROM local_guild_dungeon WHERE guild_id=?", (gid,))}
            today = datetime.now().strftime("%Y%m%d")
            # `times` is per-member, per-chapter and per-day, so build it from today's rows.
            result = b"".join(
                guild._m(1, _chapter_dto(template, states.get(ch), stages,
                                         _remaining_fights(con, gid, player["uin"], template, today)))
                for ch, template in sorted(templates.items()))
            donated = con.execute("SELECT COALESCE(SUM(day_contrib),0) FROM local_guild_members "
                                  "WHERE guild_id=? AND contrib_day=?", (gid, today)).fetchone()[0]
            return result + guild._v(2, own_guild["cohesion"]) + guild._v(3, donated)

    def stage_info(data, player):
        chapter = guild._int(data, 1)
        templates, stages = _templates()
        if chapter not in templates:
            return guild._logic_error(5006)
        with guild._db() as con:
            member, _ = _member_guild(con, player)
            if not member:
                return guild._logic_error(5007)
            _ensure_first_chapter(con, member["guild_id"], templates, stages)
            state = con.execute("SELECT * FROM local_guild_dungeon WHERE guild_id=? AND chapter_id=?",
                                (member["guild_id"], chapter)).fetchone()
            if not state:
                return guild._logic_error(5006)
            last = (stages.get(chapter) or [1])[-1]
            passed = state["stage_id"] > last
            stage = min(state["stage_id"], last)
            return guild._m(1, _stage_dto(chapter, stage, passed,member['guild_id'],state['reset_count']))

    def open_chapter(data, player):
        chapter = guild._int(data, 1)
        templates, stages = _templates()
        if chapter not in templates:
            return guild._logic_error(5006)
        with guild._db() as con:
            member, own_guild = _member_guild(con, player)
            if not member or member["position"] not in (1, 3):
                return guild._logic_error(5007)
            gid = own_guild["guild_id"]
            _ensure_first_chapter(con, gid, templates, stages)
            state = con.execute("SELECT * FROM local_guild_dungeon WHERE guild_id=? AND chapter_id=?",
                                (gid, chapter)).fetchone()
            if not state:
                previous = max((key for key in templates if key < chapter), default=0)
                prior = con.execute("SELECT stage_id FROM local_guild_dungeon WHERE guild_id=? AND chapter_id=?",
                                    (gid, previous)).fetchone() if previous else None
                if previous and (not prior or prior[0] <= (stages.get(previous) or [1])[-1]):
                    return guild._logic_error(5006)
            cost = int(templates[chapter]["resetCohesion"] if state else templates[chapter]["openCohesion"])
            store.begin_immediate(con)
            if con.execute("UPDATE local_guilds SET cohesion=cohesion-? WHERE guild_id=? AND cohesion>=?",
                           (cost, gid, cost)).rowcount != 1:
                return guild._logic_error(5006)
            first = (stages.get(chapter) or [1])[0]
            con.execute("INSERT INTO local_guild_dungeon(guild_id,chapter_id,stage_id,open_time,reset_count) "
                        "VALUES(?,?,?,?,0) ON CONFLICT(guild_id,chapter_id) DO UPDATE SET "
                        "stage_id=excluded.stage_id,open_time=excluded.open_time,reset_count=reset_count+1",
                        (gid, chapter, first, int(time.time())))
            state = con.execute("SELECT * FROM local_guild_dungeon WHERE guild_id=? AND chapter_id=?",
                                (gid, chapter)).fetchone()
            info = guild._guild_info(con, guild._guild(con, gid))
            remaining = _remaining_fights(con, gid, player["uin"], templates[chapter],
                                          datetime.now().strftime("%Y%m%d"))
            return [("PVZ.Cmd.Cmd_GuildFuben_SyncChapterInfo_SC",
                     guild._m(1, _chapter_dto(templates[chapter], state, stages, remaining))),
                    ("PVZ.Cmd.Cmd_GuildInfoRefresh_SC", guild._m(1, info))]

    def start_fight(data, player):
        chapter, stage = guild._int(data, 1), guild._int(data, 2)
        templates, stages = _templates()
        if chapter not in templates or stage not in stages.get(chapter, []):
            return guild._logic_error(5006)
        today = datetime.now().strftime("%Y%m%d")
        with guild._db() as con:
            member, _ = _member_guild(con, player)
            if not member:
                return guild._logic_error(5007)
            gid = member["guild_id"]
            state = con.execute("SELECT stage_id FROM local_guild_dungeon WHERE guild_id=? AND chapter_id=?",
                                (gid, chapter)).fetchone()
            if not state or state[0] != stage:
                return guild._logic_error(5006)
            if _remaining_fights(con, gid, player["uin"], templates[chapter], today) <= 0:
                # ErrorInfo 8515 "该章节的每日挑战次数已用完" — fightCount attempts per member per
                # chapter per day, the same quota the panel advertises as 今日剩余次数.
                return guild._logic_error(8515)
        target = guild._v(1, 14) + guild._v(2, chapter) + guild._v(3, 3) + guild._v(4, stage)
        result = _API["run_fight"](guild._m(1, target), player)
        if not isinstance(result, list) or not result:
            return guild._logic_error(5006)
        report = guild._submessage(result[0][1], 1)
        if not report:
            return guild._logic_error(5006)
        won = guild._int(report, 1) == 0
        with guild._db() as con:
            # One attempt is spent whether or not the boss died — only progress needs a win.
            _consume_fight(con, gid, player["uin"], chapter, today)
            if won:
                stage_list = stages[chapter]
                next_stage = (stage_list[stage_list.index(stage) + 1]
                              if stage != stage_list[-1] else stage + 1)
                con.execute("UPDATE local_guild_dungeon SET stage_id=? "
                            "WHERE guild_id=? AND chapter_id=? AND stage_id=?",
                            (next_stage, gid, chapter, stage))
                con.execute("INSERT INTO local_guild_dungeon_fights(guild_id,uin,day,count) "
                            "VALUES(?,?,?,1) ON CONFLICT(guild_id,uin,day) DO UPDATE SET count=count+1",
                            (gid, player["uin"], today))
            state = con.execute("SELECT * FROM local_guild_dungeon WHERE guild_id=? AND chapter_id=?",
                                (gid, chapter)).fetchone()
            remaining = _remaining_fights(con, gid, player["uin"], templates[chapter], today)
        # Push the new 今日剩余次数 straight back so the list cell updates without a re-open.
        return ([("PVZ.Cmd.Cmd_GuildFuben_StartFight_SC", guild._m(1, _fight_type(report, 14)))]
                + result[1:]
                + [("PVZ.Cmd.Cmd_GuildFuben_SyncChapterInfo_SC",
                    guild._m(1, _chapter_dto(templates[chapter], state, stages, remaining)))])

    def reward_info(_data, player):
        with guild._db() as con:
            member, _ = _member_guild(con, player)
            count = 0
            taken = 0
            if member:
                row = con.execute("SELECT count FROM local_guild_dungeon_fights WHERE guild_id=? AND uin=? AND day=?",
                                  (member["guild_id"], player["uin"], datetime.now().strftime("%Y%m%d"))).fetchone()
                count = row[0] if row else 0
                row = con.execute("SELECT count FROM local_guild_dungeon_claims WHERE guild_id=? AND uin=? AND day=?",
                                  (member["guild_id"], player["uin"], datetime.now().strftime("%Y%m%d"))).fetchone()
                taken = row[0] if row else 0
        return (guild._v(1, 2028) + guild._v(2, 0) + guild._v(3, taken)
                + guild._v(4, count) + guild._v(5, 100))

    def reward_get(_data, player):
        today = datetime.now().strftime("%Y%m%d")
        with guild._db() as con:
            member, _ = _member_guild(con, player)
            if not member:
                return guild._logic_error(5007)
            gid, uin = member["guild_id"], player["uin"]
            store.begin_immediate(con)
            fight_row = con.execute("SELECT count FROM local_guild_dungeon_fights WHERE guild_id=? AND uin=? AND day=?",
                                    (gid, uin, today)).fetchone()
            taken_row = con.execute("SELECT count FROM local_guild_dungeon_claims WHERE guild_id=? AND uin=? AND day=?",
                                    (gid, uin, today)).fetchone()
            available = max(0, min(100, fight_row[0] if fight_row else 0) - (taken_row[0] if taken_row else 0))
            if not available:
                return guild._logic_error(5006)
            con.execute("INSERT INTO local_guild_dungeon_claims(guild_id,uin,day,count) VALUES(?,?,?,?) "
                        "ON CONFLICT(guild_id,uin,day) DO UPDATE SET count=count+excluded.count",
                        (gid, uin, today, available))
        reward = guild._m(1, guild._v(1, 2028) + guild._v(2, available))
        try:
            syncs = _API["grant_reward"](player, [(2028, available)])
        except Exception:
            with guild._db() as con:
                con.execute("UPDATE local_guild_dungeon_claims SET count=count-? "
                            "WHERE guild_id=? AND uin=? AND day=?",
                            (available, gid, uin, today))
            raise
        return [("PVZ.Cmd.Cmd_GuildFuben_NewDayReward_Get_SC", reward)] + syncs

    def competition_stage(_data, _player):
        season, next_round = _current_season()
        return (guild._v(1, 1) + guild._v(2, season)
                + guild._m(3, guild._v(1, season) + guild._v(2, 2) + guild._v(3, next_round)))

    def finalist(_data, _player):
        season, _ = _current_season()
        guild.refresh_member_profiles()
        with guild._db() as con:
            rows = con.execute("SELECT * FROM local_guilds ORDER BY level DESC,honor DESC,guild_id LIMIT 100").fetchall()
            return b"".join(guild._m(1, _guild_fight_info(con, row, season)) for row in rows)

    def apply_finalist(_data, player):
        season, _ = _current_season()
        with guild._db() as con:
            member, _ = _member_guild(con, player)
            if not member:
                return guild._logic_error(5007)
            con.execute("INSERT OR IGNORE INTO local_guild_competition(season,guild_id,joined_at) VALUES(?,?,?)",
                        (season, member["guild_id"], int(time.time())))
        return [("PVZ.Cmd.Cmd_GuildFight_ApplyFinalist_SC", b""),
                ("PVZ.Cmd.Cmd_GuildFight_GetFinalist_SC", finalist(b"", player))]

    def set_competition_formation(data, player):
        _set_formation(data, player)
        return [("PVZ.Cmd.Cmd_GuildFight_GetFormation_SC", guild._m(1, _formation(player)))]

    routes = {
        "Cmd_GuildFuben_GetChapterInfo_CS": chapters,
        "Cmd_GuildFuben_GetStageInfo_CS": stage_info,
        "Cmd_GuildFuben_OpenChapter_CS": open_chapter,
        "Cmd_GuildFuben_GetFormation_CS": lambda _d, p: guild._m(1, _formation(p)),
        "Cmd_GuildFuben_SetFormation_CS": _set_formation,
        "Cmd_GuildFuben_StartFight_CS": start_fight,
        "Cmd_GuildFuben_NewDayReward_GetInfo_CS": reward_info,
        "Cmd_GuildFuben_NewDayReward_Get_CS": reward_get,
        "Cmd_GuildFuben_DayReward_GetInfo_CS": lambda _d, _p: guild._v(2, 2) + guild._v(3, 0) + guild._v(4, 0),
        "Cmd_GuildFuben_DayReward_Get_CS": lambda _d, _p: b"",
        "Cmd_GuildFight_GetCurrentStage_CS": competition_stage,
        "Cmd_GuildFight_GetFinalist_CS": finalist,
        "Cmd_GuildFight_ApplyFinalist_CS": apply_finalist,
        "Cmd_GuildFight_GetFormation_CS": lambda _d, p: guild._m(1, _formation(p)),
        "Cmd_GuildFight_SetFormation_CS": set_competition_formation,
        "Cmd_GuildFight_GetHistoryChampion_CS": lambda _d, _p: b"",
        "Cmd_GuildFight_GetHistoryRecord_CS": lambda _d, _p: b"",
    }
    return {"PVZ.Cmd." + name: handler for name, handler in routes.items()}
