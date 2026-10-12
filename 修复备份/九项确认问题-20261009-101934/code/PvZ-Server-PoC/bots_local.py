# -*- coding: utf-8 -*-
"""Persistent generated opponents for arena and guilds.

Fifty reserved NPC identities share the treasure-raider plant generator.
Legacy templates are removed. Refresh/matchmaking replaces generated opponents;
callers snapshot encounters so previews and fights remain consistent. All displayed power comes from stored cards.
"""
from __future__ import annotations

import os
from contextlib import closing
import random
import sqlite3
import time

_DB_PATH = None          # players.db (bots_state table lives here)
_GUILD_DB_PATH = None    # guild_local's sqlite (local_guilds / local_guild_members)
_API = {}

# Stable generated identities: the old fifty hard-coded card/name templates are gone.
# Reusing their reserved IDs keeps existing challenges and human guild memberships valid.
_GENERATOR_VERSION = 1


def _make_roster():
    rng = random.SystemRandom()
    first = ["晚风", "小满", "青柠", "南山", "拾光", "听雨", "星河", "云朵", "阿橘", "半夏"]
    last = ["不熬夜", "种花中", "慢慢来", "有点闲", "在摸鱼", "等晴天", "打个盹", "今天在线"]
    names = [a + b for a in first for b in last]
    rng.shuffle(names)
    guilds = ["花园守护者", "月光花园", "人机联盟", "阳光联盟", "寒霜要塞", "熔岩军团", "量子花园", "暗夜花苑"]
    sizes = [7, 7, 7, 7, 6, 6, 5, 5]
    profiles = [((i + 0.5) / 50) ** 2.2 * rng.uniform(0.72, 0.88) for i in range(50)]
    profiles[-3:] = [0.88, 0.95, 1.0]
    rng.shuffle(profiles)
    roster = []
    for guild, size in zip(guilds, sizes):
        for member in range(size):
            i = len(roster)
            f = profiles[i]
            roster.append(dict(uin=900001+i, name=names[i], guild=guild,
                               leader=member == 0, score=900+int(f**0.45*2400),
                               level=30+int(f**0.35*170), develop=f))
    return roster


_BOTS = _make_roster()

_GUILD_TEMPLATES = {
    "花园守护者": {"level": 7, "honor": 4200, "cohesion": 6800, "color_id": 2, "surface_id": 3,
                  "notice": "每天两次联盟副本，缺一不可！", "manifesto": "守护戴夫的花园，人人有责。"},
    "月光花园":   {"level": 6, "honor": 3100, "cohesion": 5200, "color_id": 5, "surface_id": 2,
                  "notice": "夜战僵尸岛，勋章管够。", "manifesto": "月下种花，白日斩僵尸。"},
    # 新增：人机联盟（由 AI 守护者组成，永远在线）
    "人机联盟":   {"level": 8, "honor": 5000, "cohesion": 8000, "color_id": 4, "surface_id": 1,
                  "notice": "人机联盟集结，AI 守护者与你并肩作战！", "manifesto": "由 AI 同伴组成的联盟，永远在线。"},
    # 50 人机分到 8 个联盟，联盟列表才不会只有两三条
    "阳光联盟":   {"level": 5, "honor": 2600, "cohesion": 4400, "color_id": 3, "surface_id": 4,
                  "notice": "每日阳光任务别漏，联盟经验靠大家。", "manifesto": "向阳而生，所向披靡。"},
    "寒霜要塞":   {"level": 6, "honor": 3400, "cohesion": 5600, "color_id": 6, "surface_id": 5,
                  "notice": "冰系阵容集结，冻结一切来犯之敌。", "manifesto": "霜冻千里，坚不可摧。"},
    "熔岩军团":   {"level": 7, "honor": 3900, "cohesion": 6200, "color_id": 1, "surface_id": 6,
                  "notice": "火系输出拉满，副本速推优先。", "manifesto": "烈焰焚天，寸草不留。"},
    "量子花园":   {"level": 4, "honor": 2100, "cohesion": 3600, "color_id": 7, "surface_id": 2,
                  "notice": "量子实验性阵容，欢迎交流配队。", "manifesto": "叠加态种花，坍缩即胜利。"},
    "暗夜花苑":   {"level": 9, "honor": 5500, "cohesion": 8600, "color_id": 8, "surface_id": 3,
                  "notice": "夜间活跃，联盟副本晚八点开组。", "manifesto": "暗夜花开，敌血成河。"},
}

def _log(message, *args):
    cb = _API.get("log")
    if cb:
        try:
            cb(message, *args)
        except Exception:
            pass


def initialize(db_path=None, guild_db_path=None, api=None):
    global _DB_PATH, _GUILD_DB_PATH, _API
    if db_path:
        _DB_PATH = os.path.abspath(db_path)
    if guild_db_path:
        _GUILD_DB_PATH = os.path.abspath(guild_db_path)
    if api:
        _API = api


def bots():
    """The bot roster: [{uin, name, guild, leader, score, level}].

    Names/levels come from the saved generated accounts after startup seeding.
    Power in bots_state is computed from their actual stored progression."""
    out = []
    for idx, b in enumerate(_BOTS):
        row = dict(b)
        out.append(row)
    return out


def is_bot(uin):
    return int(uin or 0) >= 900000 and int(uin or 0) < 1000000


def _connect(path):
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _ensure_bots_state_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS bots_state (
        uin INTEGER PRIMARY KEY,
        score INTEGER NOT NULL DEFAULT 1000,
        wins INTEGER NOT NULL DEFAULT 0,
        losses INTEGER NOT NULL DEFAULT 0,
        power INTEGER NOT NULL DEFAULT 0
    )""")


def bot_state(uin):
    """{score, wins, losses, power} for one bot, or None."""
    if not _DB_PATH:
        return None
    con = _connect(_DB_PATH)
    try:
        _ensure_bots_state_table(con)
        row = con.execute("SELECT score, wins, losses, power FROM bots_state WHERE uin=?",
                          (int(uin),)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def bot_states(uins=None):
    """{uin: {score, wins, losses, power}} for the whole roster in ONE query.

    `bot_state` opens its own connection per call, so asking it for every bot cost ~50
    connect/create-table/close round trips — seconds on the arena panels, which rebuild the board
    on every refresh. Use this bulk form wherever more than a couple of bots are needed."""
    if not _DB_PATH:
        return {}
    con = _connect(_DB_PATH)
    try:
        _ensure_bots_state_table(con)
        rows = con.execute("SELECT uin, score, wins, losses, power FROM bots_state").fetchall()
    finally:
        con.close()
    out = {int(r["uin"]): {"score": int(r["score"]), "wins": int(r["wins"]),
                           "losses": int(r["losses"]), "power": int(r["power"])} for r in rows}
    if uins is None:
        return out
    wanted = {int(u) for u in uins}
    return {uin: state for uin, state in out.items() if uin in wanted}


def apply_match_result(uin, player_won, delta_win=15, delta_loss=8):
    """After a player-vs-bot match: move both sides' scores apart and record the bot's tally."""
    if not _DB_PATH:
        return
    con = _connect(_DB_PATH)
    try:
        _ensure_bots_state_table(con)
        row = con.execute("SELECT score, wins, losses FROM bots_state WHERE uin=?",
                          (int(uin),)).fetchone()
        if not row:
            return
        score = int(row["score"]) + (-delta_win if player_won else delta_win)
        if player_won:
            con.execute("UPDATE bots_state SET score=?, losses=losses+1 WHERE uin=?",
                        (max(0, score), int(uin)))
        else:
            con.execute("UPDATE bots_state SET score=?, wins=wins+1 WHERE uin=?",
                        (max(0, score), int(uin)))
        con.commit()
    finally:
        con.close()


def pick_opponent(player_score, exclude=()):
    """The bot whose arena score is closest to the player's (never one of `exclude`)."""
    if not _DB_PATH:
        return None
    con = _connect(_DB_PATH)
    try:
        _ensure_bots_state_table(con)
        rows = con.execute("SELECT uin, score, power FROM bots_state").fetchall()
    finally:
        con.close()
    pool = [r for r in rows if int(r["uin"]) not in {int(x) for x in exclude}]
    if not pool:
        return None
    best = min(pool, key=lambda r: abs(int(r["score"]) - int(player_score or 0)))
    return {"uin": int(best["uin"]), "score": int(best["score"]), "power": int(best["power"])}


def _ensure_guild_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS local_guilds (
        guild_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL COLLATE NOCASE UNIQUE,
        level INTEGER NOT NULL DEFAULT 1,
        exp INTEGER NOT NULL DEFAULT 0,
        honor INTEGER NOT NULL DEFAULT 0,
        qq_group TEXT NOT NULL DEFAULT '',
        notice TEXT NOT NULL DEFAULT '',
        manifesto TEXT NOT NULL DEFAULT '',
        color_id INTEGER NOT NULL DEFAULT 1,
        color2_id INTEGER NOT NULL DEFAULT 1,
        surface_id INTEGER NOT NULL DEFAULT 1,
        sign_id INTEGER NOT NULL DEFAULT 1,
        cohesion INTEGER NOT NULL DEFAULT 0,
        allow_quick_join INTEGER NOT NULL DEFAULT 1,
        need_power INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS local_guild_members (
        guild_id INTEGER NOT NULL,
        uin INTEGER NOT NULL UNIQUE,
        role_id INTEGER NOT NULL UNIQUE,
        name TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 2,
        contrib INTEGER NOT NULL DEFAULT 0,
        day_contrib INTEGER NOT NULL DEFAULT 0,
        contrib_day TEXT NOT NULL DEFAULT '',
        level INTEGER NOT NULL DEFAULT 1,
        face_id INTEGER NOT NULL DEFAULT 0,
        fight_power INTEGER NOT NULL DEFAULT 0,
        joined_at INTEGER NOT NULL,
        PRIMARY KEY(guild_id, uin)
    )""")
    # Which guild each bot was moved into. The in-memory roster is rebuilt from _make_roster() on
    # every start, so the move has to live somewhere durable or the next boot would drag the bot
    # back into its template guild.
    con.execute("""CREATE TABLE IF NOT EXISTS bot_guild_assignment (
        uin INTEGER PRIMARY KEY,
        guild_id INTEGER NOT NULL
    )""")


def _bot_lookup():
    return {int(b["uin"]): b for b in _BOTS}


def _insert_bot_member(con, guild_id, bot, state, position=2, joined_at=None):
    """Write one bot's guild member row. `state` must be resolved BEFORE this connection opens:
    bot_state() opens its own sqlite connection, and doing that while this one holds a write
    transaction deadlocks ("database is locked") for the full sqlite timeout."""
    con.execute(
        "INSERT OR REPLACE INTO local_guild_members(guild_id, uin, role_id, name, position, "
        "contrib, level, face_id, fight_power, joined_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (int(guild_id), int(bot["uin"]), int(bot["uin"]), str(bot["name"]), int(position),
         300 + int(bot["score"]) % 200, int(bot["level"]), 0,
         int((state or {}).get("power") or 0),
         int(joined_at if joined_at is not None else time.time())))


# A guild a real player owns should not be an empty room: this many arena bots move in as
# ordinary members (position 2). They are the same identities that fight in 天梯竞技场.
_BOTS_PER_PLAYER_GUILD = 4
# Never strip a bot guild below this many members while lending one of its members away.
_BOT_GUILD_FLOOR = 4


def staff_player_guilds():
    """Move a few arena bots into every guild that has no bot member yet (a real player's guild).

    "把竞技场的人机放到联盟中，一些加入我的联盟，一些自己创建或加入别的人机联盟": the bot
    guilds from _GUILD_TEMPLATES stay as they are (bots lead them), and on top of that any guild
    created by a player receives _BOTS_PER_PLAYER_GUILD bots so the member list, 捐献排行 and
    联盟副本 actually have company. Idempotent — a guild that already holds a bot is skipped, so
    it is safe to call at startup and after every guild creation."""
    if not _GUILD_DB_PATH:
        return 0
    states = bot_states()                      # one connection, before the guild one opens
    bots = _bot_lookup()
    if not bots:
        return 0
    con = _connect(_GUILD_DB_PATH)
    try:
        _ensure_guild_tables(con)
        rows = con.execute("SELECT guild_id, uin FROM local_guild_members").fetchall()
        by_guild, member_guild = {}, {}
        for row in rows:
            gid, uin = int(row["guild_id"]), int(row["uin"])
            by_guild.setdefault(gid, set()).add(uin)
            member_guild[uin] = gid
        sizes = {gid: len(members) for gid, members in by_guild.items()}
        # Bots that may be lent out: not a guild's leader, and taken from the strongest first so
        # the player's guild looks like a real veteran roster.
        pool = sorted((u for u in bots if not bots[u]["leader"]),
                      key=lambda u: (-int(bots[u]["level"]), u))
        moved = 0
        for gid in sorted(by_guild):
            if by_guild[gid] & set(bots):
                continue                                  # already a bot guild
            picked = []
            for uin in list(pool):
                source = member_guild.get(uin)
                if source is None or sizes.get(source, 0) <= _BOT_GUILD_FLOOR:
                    continue
                pool.remove(uin)
                sizes[source] -= 1
                picked.append(uin)
                if len(picked) >= _BOTS_PER_PLAYER_GUILD:
                    break
            for uin in picked:
                con.execute("DELETE FROM local_guild_members WHERE uin=?", (uin,))
                con.execute("INSERT OR REPLACE INTO bot_guild_assignment(uin, guild_id) VALUES(?,?)",
                            (uin, gid))
                _insert_bot_member(con, gid, bots[uin], states.get(uin), position=2)
                moved += 1
        # Bots that were lent out in an earlier run must not be duplicated back into their
        # template guild when _seed_guilds() walks the roster again.
        for row in con.execute("SELECT uin, guild_id FROM bot_guild_assignment").fetchall():
            uin, gid = int(row["uin"]), int(row["guild_id"])
            if uin in bots and member_guild.get(uin) != gid:
                _insert_bot_member(con, gid, bots[uin], states.get(uin), position=2)
        con.commit()
        if moved:
            _log("bots: %d joined player guild(s)", moved)
        return moved
    finally:
        con.close()


def _seed_guilds():
    """Create the bot guilds + member rows (idempotent). Guilds WITHOUT a bot member roster are
    left alone — real player guilds are never touched."""
    if not _GUILD_DB_PATH:
        return
    states = bot_states()                      # one connection, before the guild one opens
    con = _connect(_GUILD_DB_PATH)
    try:
        _ensure_guild_tables(con)
        now = int(time.time())
        for guild_name, tpl in _GUILD_TEMPLATES.items():
            row = con.execute("SELECT guild_id FROM local_guilds WHERE name=?",
                              (guild_name,)).fetchone()
            if not row:
                con.execute(
                    "INSERT INTO local_guilds(name, level, exp, honor, notice, manifesto, "
                    "color_id, color2_id, surface_id, cohesion, allow_quick_join, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,1,?)",
                    (guild_name, tpl["level"], tpl["honor"] * 10, tpl["honor"], tpl["notice"],
                     tpl["manifesto"], tpl["color_id"], tpl["color_id"], tpl["surface_id"],
                     tpl["cohesion"], now))
                row = con.execute("SELECT guild_id FROM local_guilds WHERE name=?",
                                  (guild_name,)).fetchone()
            gid = int(row["guild_id"])
            for b in _BOTS:
                if b["guild"] != guild_name:
                    continue
                member = con.execute("SELECT guild_id FROM local_guild_members WHERE uin=?",
                                     (b["uin"],)).fetchone()
                if member:
                    # keep the stored power fresh (it feeds the guild member list); a bot that was
                    # lent to a player guild keeps its row there.
                    con.execute("UPDATE local_guild_members SET fight_power=?, level=?, name=? WHERE uin=?",
                                (int((states.get(b["uin"]) or {}).get("power") or 0),
                                 b["level"], b["name"], b["uin"]))
                    continue
                _insert_bot_member(con, gid, b, states.get(b["uin"]),
                                   position=1 if b["leader"] else 2, joined_at=now)
        con.commit()
    finally:
        con.close()
    staff_player_guilds()


def ensure_bots(refresh_uins=None):
    """Migrate legacy teams; explicitly refreshed slots receive newly generated opponents."""
    if not _DB_PATH or not _API.get("generate_units"):
        return 0
    with closing(_connect(_DB_PATH)) as con, con:
        _ensure_bots_state_table(con)
        con.execute("CREATE TABLE IF NOT EXISTS bot_generation (uin INTEGER PRIMARY KEY, version INTEGER NOT NULL)")
        versions = dict(con.execute("SELECT uin, version FROM bot_generation"))
    refresh_uins = set(refresh_uins or ())
    fresh = {b['uin']:b for b in _make_roster()} if refresh_uins else {}
    created = 0
    for idx, b in enumerate(_BOTS):
        uin = b["uin"]
        if refresh_uins and uin not in refresh_uins:
            continue
        player, _new = _API["get_or_create_player"](uin)
        migrating = versions.get(uin) != _GENERATOR_VERSION
        if migrating or uin in refresh_uins:
            if uin in fresh:
                b.update(fresh[uin])
            occupied_names = {str(row['name']) for row in _BOTS if row['uin'] != uin}
            while b['name'] in occupied_names:
                b['name'] += str(random.SystemRandom().randint(10,99))
            rng = random.Random(random.SystemRandom().getrandbits(64))
            target = 4000 + int(b["develop"] ** 0.6 * 480000)
            units = _API["generate_units"](target, rng, 20000000 + idx * 100)
            if not units:
                raise RuntimeError("No valid plant templates; legacy team retained")
            b["level"] = max(b["level"], _API["required_role_level"](max(u["level"] for u in units)))
            _API["clear_cards_and_formation"](uin)
            _API["save_player"](uin, name=b["name"], level=b["level"])
            placed = {}
            for unit in units:
                uid, cid, level = unit["uid"], unit["card_id"], unit["level"]
                _API["add_card"](uin, uid, cid, level, 0)
                fraction = min(0.95, b["develop"] * rng.uniform(0.55, 1.3))
                _API["develop_npc_card"](uin, uid, cid, level, fraction, rng)
                placed[unit["pos"]] = uid
            _API["set_formation"](uin, 1, placed)
            captain = next(u["uid"] for u in units if u["is_leader"])
            _API["set_formation_leader"](uin, 1, captain)
            with closing(_connect(_DB_PATH)) as con, con:
                con.execute("INSERT OR REPLACE INTO bot_generation VALUES (?,?)", (uin, _GENERATOR_VERSION))
                if migrating:
                    con.execute("INSERT OR REPLACE INTO bots_state(uin,score,wins,losses,power) VALUES(?,?,0,0,0)", (uin,b["score"]))
            created += 1
        player, _ = _API["get_or_create_player"](uin)
        b["level"] = int(player["level"])
        b["name"] = str(player["name"])
        # The arena/guild cache describes the deployed team. A first preview
        # can grant idle TD starter cards; counting the whole collection made
        # the next server boot increase power without changing any fighter.
        formation_power = _API.get("formation_power")
        if formation_power:
            power = int(formation_power(uin))
        else:
            formation = _API.get("get_formation")
            deployed = set(formation(uin, 1).values()) if formation else None
            power = sum(_API["card_power_full"](uin,cid,level,uid)
                        for uid,cid,level,_exp in _API["get_cards"](uin)
                        if deployed is None or uid in deployed)
        with closing(_connect(_DB_PATH)) as con, con:
            con.execute("UPDATE bots_state SET power=? WHERE uin=?", (power,uin))
    _seed_guilds()
    _log("bots: %d slots, %d opponents generated", len(_BOTS), created)
    return created


def regenerate(uins=None):
    """Explicit list refresh / matchmaking, never during preview or combat."""
    return ensure_bots({b['uin'] for b in _BOTS} if uins is None else set(uins))


def run_ai_tick():
    """One autonomous bot-activity tick: simulate bot-vs-bot arena skirmishes so the
    leaderboards / arena opponents stay alive — the 人机 (bots) 'run their own program'.

    Uses only the existing arena-state pipeline (pick/apply_match_result); never touches
    real players. Safe to call on a timer from the server's event loop."""
    import random
    if not _DB_PATH:
        return
    con = _connect(_DB_PATH)
    try:
        _ensure_bots_state_table(con)
        rows = con.execute("SELECT uin, score, power FROM bots_state").fetchall()
    finally:
        con.close()
    if len(rows) < 2:
        return
    a, b = random.sample(rows, 2)
    # Rankings use the same live combat as player challenges.
    fight = _API.get("fight_bots")
    if fight is None:
        return
    a_wins = fight(int(a["uin"]), int(b["uin"]))
    if a_wins:
        apply_match_result(int(b["uin"]), True)    # b lost to a
        apply_match_result(int(a["uin"]), False)   # a beat b
    else:
        apply_match_result(int(a["uin"]), True)    # a lost to b
        apply_match_result(int(b["uin"]), False)   # b beat a
    _log("bot AI tick: %s vs %s -> %s won", a["uin"], b["uin"], "a" if a_wins else "b")
