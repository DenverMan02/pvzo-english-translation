"""Persistent local adapter for the playable core of the PvZOL guild system.

The shipped server used to answer RoleGuildInfo with guildId=0 and left guild
commands to the generic empty-response fallback.  This module implements guild
creation, discovery, applications, membership, donation, administration, and the
DTOs required to keep the client panels populated.  It deliberately keeps its
tables additive and does not migrate or rewrite existing player saves.

Install into pvz_poc_server.REQUEST_HANDLERS with ``register(api)``.  api accepts
``db_path``, ``config_value`` and ``log`` callbacks.
"""
from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import sys
import time
import player_store as store
from datetime import datetime


_BASE = (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
         else os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(_BASE, "players.db")
_DB_PATH = DEFAULT_DB_PATH
_API = {}
_SKILL_RULES = None
_DONATE_DAILY_UNITS = 10000  # 100 million coins / 10,000 coins per wire unit
_QUICK_POWER_UNSET = object()


def _varint(value: int) -> bytes:
    value = max(0, int(value))
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((int(field) << 3) | int(wire))


def _v(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


def _s(field: int, value: str) -> bytes:
    data = str(value or "").encode("utf-8")
    return _tag(field, 2) + _varint(len(data)) + data


def _m(field: int, value: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(value)) + value


def _logic_error(code):
    return [("PVZ.Cmd.Cmd_LogicError_SC", _v(1, code))]


def _read_varint(data: bytes, pos: int):
    value = shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7
        if shift > 70:
            break
    raise ValueError("malformed protobuf varint")


def _fields(data: bytes):
    """Return {field: [(wire, value), ...]} while safely skipping unknown fields."""
    result = {}
    pos = 0
    data = bytes(data or b"")
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field, wire = key >> 3, key & 7
        if field <= 0:
            raise ValueError("invalid protobuf field")
        if wire == 0:
            value, pos = _read_varint(data, pos)
        elif wire == 1:
            if pos + 8 > len(data):
                raise ValueError("truncated fixed64")
            value, pos = data[pos:pos + 8], pos + 8
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            if length > len(data) - pos:
                raise ValueError("truncated length-delimited field")
            value, pos = data[pos:pos + length], pos + length
        elif wire == 5:
            if pos + 4 > len(data):
                raise ValueError("truncated fixed32")
            value, pos = data[pos:pos + 4], pos + 4
        else:
            raise ValueError("unsupported protobuf wire type %d" % wire)
        result.setdefault(field, []).append((wire, value))
    return result


def _int(data: bytes, field: int, default=0) -> int:
    for wire, value in _fields(data).get(field, []):
        if wire == 0:
            return int(value)
    return int(default)


def _string(data: bytes, field: int, default="") -> str:
    for wire, value in _fields(data).get(field, []):
        if wire == 2:
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return default
    return default


def _submessage(data: bytes, field: int) -> bytes:
    for wire, value in _fields(data).get(field, []):
        if wire == 2:
            return value
    return b""


@contextlib.contextmanager
def _db():
    if os.path.abspath(_DB_PATH) == os.path.abspath(store.DB_PATH):
        with store._db() as con:
            yield con
        return
    con = sqlite3.connect(_DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _ensure_column(con, table, column, declaration):
    """Additive migration for one column. Existing saves keep working untouched."""
    existing = {row[1] for row in con.execute("PRAGMA table_info(%s)" % table)}
    if column not in existing:
        con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, declaration))


def initialize(db_path=None):
    """Create only guild-owned tables; never alters player or gameplay tables."""
    global _DB_PATH
    if db_path:
        _DB_PATH = os.path.abspath(db_path)
    with _db() as con:
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
        # 联盟竞价 (Cmd_GuildBid_CS): the guild's current 联盟经验 offer, refunded and replaced on
        # every new bid so the guild's exp is conserved. Existing databases get the column here.
        _ensure_column(con, "local_guilds", "bid_exp", "INTEGER NOT NULL DEFAULT 0")
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
        con.execute("""CREATE TABLE IF NOT EXISTS local_guild_applications (
            guild_id INTEGER NOT NULL,
            uin INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            level INTEGER NOT NULL DEFAULT 1,
            fight_power INTEGER NOT NULL DEFAULT 0,
            apply_time INTEGER NOT NULL,
            PRIMARY KEY(guild_id, uin)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_guild_invites (
            guild_id INTEGER NOT NULL,
            inviter_uin INTEGER NOT NULL,
            invitee_uin INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id, invitee_uin)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_guild_players (
            uin INTEGER PRIMARY KEY,
            last_quit_time INTEGER NOT NULL DEFAULT 0,
            invite_count INTEGER NOT NULL DEFAULT 0
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_guild_skills (
            uin INTEGER NOT NULL,
            skill_id INTEGER NOT NULL,
            level INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(uin, skill_id)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_guild_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            event_type INTEGER NOT NULL,
            role_id INTEGER NOT NULL DEFAULT 0,
            role_name TEXT NOT NULL DEFAULT '',
            event_time INTEGER NOT NULL,
            text TEXT NOT NULL DEFAULT ''
        )""")


def _cfg_int(name, fallback):
    getter = _API.get("config_value")
    if getter:
        try:
            return max(0, int(getter(name)))
        except (TypeError, ValueError):
            pass
    return int(fallback)


def _log(message, *args):
    cb = _API.get("log")
    if cb:
        try:
            cb(message, *args)
        except Exception:
            pass


def _notify_changed():
    """Tell the host the roster moved. The 战力排行榜 caches {uin: guild} for a few seconds, so a
    create/join/quit/kick has to drop that cache or the 联盟 column stays stale until the TTL."""
    cb = _API.get("guild_changed")
    if cb:
        try:
            cb()
        except Exception:
            pass


def _member(con, uin):
    return con.execute("SELECT * FROM local_guild_members WHERE uin=?", (int(uin),)).fetchone()


def _guild(con, guild_id):
    if not guild_id:
        return None
    return con.execute("SELECT * FROM local_guilds WHERE guild_id=?", (int(guild_id),)).fetchone()


def _guild_for_player(con, player):
    member = _member(con, player.get("uin", 0))
    return _guild(con, member["guild_id"]) if member else None


def _guild_base(con, guild):
    if not guild:
        return b""
    count = con.execute("SELECT COUNT(*) FROM local_guild_members WHERE guild_id=?", (guild["guild_id"],)).fetchone()[0]
    leader = con.execute("SELECT role_id,name FROM local_guild_members WHERE guild_id=? AND position=1 LIMIT 1", (guild["guild_id"],)).fetchone()
    flags = (_v(1, guild["color_id"]) + _v(2, guild["color2_id"])
             + _v(3, guild["surface_id"]) + _v(4, guild["sign_id"]))
    return (_v(1, guild["guild_id"]) + _s(2, guild["name"])
            + _v(3, guild["level"]) + _v(4, count) + _v(5, _member_limit(guild))
            + _v(6, leader["role_id"] if leader else 0)
            + _s(7, leader["name"] if leader else "")
            + _s(8, guild["manifesto"]) + _m(9, flags))


def _row_get(row, key, default=0):
    """sqlite3.Row lookup that tolerates a column added after the save was created."""
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def _guild_info(con, guild):
    if not guild:
        return b""
    base = _guild_base(con, guild)
    # Dto_Guild_Info: 5 = curBid — the live 联盟竞价 offer UnionBiddingWindowMediator.refreshBaseInfo
    # reads back into the 竞价 window, written by Cmd_GuildBid_CS.
    return (_m(1, base) + _v(2, guild["exp"]) + _v(3, guild["honor"])
            + _s(4, guild["qq_group"]) + _v(5, int(_row_get(guild, "bid_exp")))
            + _v(6, guild["created_at"]) + _s(7, guild["notice"])
            + _v(10, 0) + _v(11, 0) + _v(12, 0) + _v(13, 0)
            + _v(14, 0) + _v(15, 0) + _v(18, guild["cohesion"]))


def member_index():
    """{uin: {"guild_id", "guild_name", "guild_level"}} for every guild member.

    The 战力排行榜 needs each role's 联盟 column; doing it per row would be one query per entry,
    so the leaderboard pulls this whole map once instead."""
    with _db() as con:
        rows = con.execute(
            "SELECT m.uin AS uin, m.guild_id AS gid, g.name AS gname, g.level AS glevel "
            "FROM local_guild_members m JOIN local_guilds g ON g.guild_id = m.guild_id").fetchall()
    return {int(r["uin"]): {"guild_id": int(r["gid"]), "guild_name": str(r["gname"]),
                            "guild_level": int(r["glevel"])} for r in rows}


def power_board():
    """[{guild_id, name, level, member_num, member_limit, power}] — the 联盟战力榜, ordered by
    Σ member fight_power. Bot members keep their stored fight_power fresh on every boot
    (bots_local._seed_guilds); human members use the server's live collection power."""
    refresh_member_profiles()
    with _db() as con:
        rows = con.execute(
            "SELECT g.guild_id AS gid, g.name AS name, g.level AS level, "
            "COUNT(m.uin) AS n, COALESCE(SUM(m.fight_power), 0) AS power "
            "FROM local_guilds g LEFT JOIN local_guild_members m ON m.guild_id = g.guild_id "
            "GROUP BY g.guild_id").fetchall()
    out = [{"guild_id": int(r["gid"]), "name": str(r["name"]), "level": int(r["level"]),
            "member_num": int(r["n"]), "member_limit": 50, "power": int(r["power"])}
           for r in rows]
    out.sort(key=lambda g: (-g["power"], -g["member_num"], g["name"]))
    return out


def _role_guild_info(con, player):
    uin = int(player.get("uin", 0))
    member = _member(con, uin)
    state = con.execute("SELECT last_quit_time,invite_count FROM local_guild_players WHERE uin=?", (uin,)).fetchone()
    out = b""
    if member:
        guild = _guild(con, member["guild_id"])
        if guild:
            out += _v(1, guild["guild_id"]) + _s(2, guild["name"])
            out += _v(6, member["position"]) + _v(7, member["contrib"])
            out += _v(11, guild["level"]) + _v(12, guild["cohesion"])
            out += _v(13, member["day_contrib"] if member["contrib_day"] == datetime.now().strftime("%Y%m%d") else 0)
    else:
        # Keep empty membership fields explicitly present: older clients sometimes
        # read guildName directly instead of checking hasGuildName first.
        out = _v(1, 0) + _s(2, "")
    if state:
        out += _v(3, state["invite_count"]) + _v(4, state["last_quit_time"])
    applications = con.execute("SELECT guild_id FROM local_guild_applications WHERE uin=? ORDER BY apply_time", (uin,)).fetchall()
    for application in applications:
        out += _v(5, application["guild_id"])
    return out


def _member_dto(row):
    return (_v(1, row["role_id"]) + _s(2, row["name"]) + _v(3, row["position"])
            + _v(4, row["contrib"]) + _v(5, 0)
            + _v(6, row["day_contrib"] if row["contrib_day"] == datetime.now().strftime("%Y%m%d") else 0)
            + _v(7, row["level"]) + _v(8, row["face_id"]) + _v(9, row["fight_power"])
            + _s(10, "") + _v(11, 0))


def _member_limit(guild):
    rows=(_API.get('read_clientdb_table') or (lambda _n: []))('UnionLevelInfo.txt')
    if rows:
        for row in rows[1:]:
            entry=dict(zip(rows[0],row))
            if int(entry['id'])==int(guild['level']):return int(entry['maxMember'])
    return 50


def _quick_join_power(player):
    """Read live collection power through the server, never a request/player hint."""
    getter = _API.get("player_fight_power")
    if getter is None:
        return None
    try:
        return max(0, int(getter(player)))
    except Exception as exc:
        _log("guild collection power unavailable uin=%s: %s", player.get("uin"), exc)
        return None


def _live_member_profile(player):
    """Read saved role fields and the existing collection-power calculation.

    Run before a membership writer transaction: the power callback uses the
    shared player store and must not commit a half-written guild operation.
    """
    with _db() as con:
        has_players = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='players'").fetchone()
        row = con.execute("SELECT * FROM players WHERE uin=?", (int(player.get("uin", 0)),)).fetchone() if has_players else None
    live = dict(row) if row else dict(player)
    return (int(live.get("level") or 1), int(live.get("face_id") or 0),
            _quick_join_power(live), str(live.get("name") or "Player"))


def _refresh_role_profiles(guild_id=None, applications=False):
    """Refresh saved human member/application profiles in one guild, or all.

    Bots keep their generated stats, and an orphaned old membership keeps its
    cached values. Compute every power before writing so nested store reads do
    not commit some of the cache updates early.
    """
    table = "local_guild_applications" if applications else "local_guild_members"
    face_column = "0" if applications else "m.face_id"
    with _db() as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='players'").fetchone():
            return
        sql = ("SELECT p.*,m.guild_id AS profile_guild_id,m.name AS cached_name,m.level AS cached_level," + face_column + " AS cached_face_id,"
               "m.fight_power AS cached_power FROM " + table + " m "
               "JOIN players p ON p.uin=m.uin WHERE NOT (m.uin>=900000 AND m.uin<1000000)")
        rows = con.execute(sql + (" AND m.guild_id=?" if guild_id is not None else ""),
                           (int(guild_id),) if guild_id is not None else ()).fetchall()
    updates = []
    for row in rows:
        live = dict(row)
        level, face = int(live.get("level") or 1), int(live.get("face_id") or 0)
        name = str(live.get("name") or "Player")
        if applications:
            face = 0
        power = _quick_join_power(live)
        if power is None:
            power = int(row["cached_power"])
        if (name, level, face, power) != (row["cached_name"], row["cached_level"], row["cached_face_id"], row["cached_power"]):
            values = (name, level, power) if applications else (name, level, face, power)
            updates.append(values + (int(row["uin"]), int(row["profile_guild_id"])))
    if updates:
        with _db() as con:
            columns = "name=?,level=?,fight_power=?" if applications else "name=?,level=?,face_id=?,fight_power=?"
            con.executemany("UPDATE " + table + " SET " + columns + " WHERE uin=? AND guild_id=?", updates)


def refresh_member_profiles(guild_id=None):
    _refresh_role_profiles(guild_id)


def _joinable(con, player, guild, require_quick=False, quick_power=_QUICK_POWER_UNSET):
    if not guild or _member(con, player.get("uin", 0)):
        return False
    count = con.execute("SELECT COUNT(*) FROM local_guild_members WHERE guild_id=?", (guild["guild_id"],)).fetchone()[0]
    if count >= _member_limit(guild):
        return False
    if require_quick and not guild["allow_quick_join"]:
        return False
    if require_quick:
        needed = max(0, int(guild["need_power"] or 0))
        if needed:
            power = _quick_join_power(player) if quick_power is _QUICK_POWER_UNSET else quick_power
            if power is None or power < needed:
                return False
    return True


def _add_member(con, guild, player, position=2, profile=None):
    uin = int(player.get("uin", 0))
    level, face, power, name = profile if profile is not None else (
        int(player.get("level") or 1), int(player.get("face_id") or 0), 0,
        str(player.get("name") or "Player"))
    _notify_changed()
    con.execute("DELETE FROM local_guild_applications WHERE guild_id=? AND uin=?", (guild["guild_id"], uin))
    con.execute("""INSERT INTO local_guild_members
        (guild_id,uin,role_id,name,position,level,face_id,fight_power,joined_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (guild["guild_id"], uin, int(player.get("role_id") or uin),
         name, int(position), level,
         face, int(power or 0), int(time.time())))
    con.execute("INSERT OR IGNORE INTO local_guild_players(uin) VALUES(?)", (uin,))
    con.execute("INSERT INTO local_guild_events(guild_id,event_type,role_id,role_name,event_time) VALUES(?,?,?,?,?)",
                (guild["guild_id"], 1, int(player.get("role_id") or uin), name, int(time.time())))


def _skill_rules():
    global _SKILL_RULES
    if _SKILL_RULES is not None:
        return _SKILL_RULES
    reader = _API.get("read_clientdb_table")
    if not reader:
        return {}, {}, {}
    def records(name):
        rows = reader(name)
        if not rows:
            return []
        header = rows[0]
        return [dict(zip(header, row)) for row in rows[1:] if len(row) >= len(header)]
    unlock = {int(row["skillID"]): int(row["unlockLevel"])
              for row in records("unionSkillUnlock.txt")}
    cost = {(int(row["skillID"]), int(row["targetLevel"])): int(row["needPoint"])
            for row in records("unionSkillInfo.txt")}
    limit = {int(row["id"]): int(row["limit"])
             for row in records("unionSkillLevel.txt")}
    _SKILL_RULES = (unlock, cost, limit)
    return _SKILL_RULES


def register(api=None, db_path=None):
    """Return REQUEST_HANDLERS entries for core guild commands."""
    global _API, _SKILL_RULES
    _API = dict(api or {})
    _SKILL_RULES = None
    initialize(db_path or _API.get("db_path"))

    def handle_role_info(cmd_data, player):
        with _db() as con:
            dto = _role_guild_info(con, player)
        return _m(1, dto)

    def handle_guild_info(cmd_data, player):
        gid = _int(cmd_data, 1)
        with _db() as con:
            guild = _guild(con, gid) if gid else _guild_for_player(con, player)
            info = _guild_info(con, guild)
        return _m(1, info) if info else b""

    def handle_guild_list(cmd_data, player):
        refresh = _API.get("refresh_npcs")
        if refresh:
            refresh()
        begin, end = _int(cmd_data, 1), _int(cmd_data, 2, 19)
        begin = min(max(0, begin), 100000)
        end = min(max(begin, end), begin + 99)
        with _db() as con:
            total = con.execute("SELECT COUNT(*) FROM local_guilds").fetchone()[0]
            rows = con.execute("SELECT * FROM local_guilds ORDER BY level DESC, exp DESC, guild_id LIMIT ? OFFSET ?",
                               (end - begin + 1, begin)).fetchall()
            out = _v(1, total)
            for row in rows:
                out += _m(2, _guild_base(con, row))
        return out

    def handle_create(cmd_data, player):
        name = re.sub(r"\s+", " ", _string(cmd_data, 1)).strip()
        if len(name) < 2 or len(name) > 24 or any(ord(ch) < 32 for ch in name):
            return b""
        uin = int(player.get("uin", 0))
        role_id = int(player.get("role_id") or uin)
        # Despite the legacy config name, the client checks recruit tokens,
        # not coins. The offline config makes creation free until those tokens
        # have a real acquisition path.
        fee = _cfg_int("iCreateGuildNeedCoin", 0)
        min_level = _cfg_int("iCreateGuildNeedLevel", 1)
        if not uin or int(player.get("level") or 1) < min_level:
            return b""
        flag = _fields(_submessage(cmd_data, 2))
        colors = [int(flag.get(i, [(0, 1)])[0][1]) if flag.get(i) and flag[i][0][0] == 0 else 1 for i in range(1, 5)]
        now = int(time.time())
        profile = _live_member_profile(player)
        with _db() as con:
            if _member(con, uin):
                return b""
            store.begin_immediate(con)
            if con.execute("SELECT 1 FROM local_guilds WHERE name=? COLLATE NOCASE", (name,)).fetchone():
                return b""
            if fee:
                cur = con.execute("UPDATE items SET count=count-? WHERE uin=? AND item_id=2029 AND count>=?",
                                  (fee, uin, fee))
                if cur.rowcount != 1:
                    return b""
            cur = con.execute("""INSERT INTO local_guilds(name,manifesto,color_id,color2_id,surface_id,sign_id,created_at)
                VALUES(?,?,?,?,?,?,?)""", (name, name, *colors, now))
            guild = con.execute("SELECT * FROM local_guilds WHERE guild_id=?", (cur.lastrowid,)).fetchone()
            _add_member(con, guild, player, position=1, profile=profile)
        _log("local guild created: guildId=%d roleId=%d name=%s", guild["guild_id"], role_id, name)
        staff = _API.get("assign_bots")
        if staff:
            # The guild a player just created should not be an empty room: a few arena bots move
            # in as ordinary members right away.
            try:
                staff()
            except Exception as exc:
                _log("bot guild staffing failed: %s", exc)
        return b""

    def handle_join_req(cmd_data, player):
        gid = _int(cmd_data, 1)
        uin = int(player.get("uin", 0))
        profile = _live_member_profile(player)
        with _db() as con:
            guild = _guild(con, gid)
            if not _joinable(con, player, guild) or not uin:
                return b""
            con.execute("INSERT OR REPLACE INTO local_guild_applications(guild_id,uin,role_id,name,level,fight_power,apply_time) VALUES(?,?,?,?,?,?,?)",
                        (gid, uin, int(player.get("role_id") or uin), profile[3], profile[0], int(profile[2] or 0), int(time.time())))
            con.execute("INSERT OR IGNORE INTO local_guild_players(uin) VALUES(?)", (uin,))
        return b""

    def handle_quick_join(cmd_data, player):
        if not player.get("uin"):
            return b""
        # Compute before taking the membership writer lock: the power helper
        # reads player/card tables through the shared store connection.
        profile = _live_member_profile(player)
        power = profile[2]
        with _db() as con:
            store.begin_immediate(con)
            if _member(con, player["uin"]):
                return b""
            guilds = con.execute("SELECT * FROM local_guilds WHERE allow_quick_join=1 ORDER BY level DESC, exp DESC, guild_id").fetchall()
            chosen = next((g for g in guilds if _joinable(con, player, g, True, power)), None)
            if chosen:
                _add_member(con, chosen, player, profile=profile)
            else:
                # These errors are in the shipped ErrorInfo table. Do not send
                # an empty QuickJoin_SC that the client would treat as success.
                below_power = any(_joinable(con, player, g)
                                  and int(g["need_power"] or 0) > (power or 0)
                                  for g in guilds)
                return _logic_error(5066 if below_power else 5064)
        return b""

    def handle_members(cmd_data, player):
        gid = _int(cmd_data, 1)
        refresh = _API.get("refresh_npcs")
        if refresh:
            with _db() as con:
                actor = _member(con, player.get("uin",0))
                gid = gid or (actor["guild_id"] if actor else 0)
                npc_uins = [int(r[0]) for r in con.execute(
                    "SELECT uin FROM local_guild_members WHERE guild_id=? AND uin BETWEEN 900001 AND 900050", (gid,))]
            if npc_uins:
                refresh(npc_uins)
        with _db() as con:
            if not gid:
                member = _member(con, player.get("uin", 0))
                gid = member["guild_id"] if member else 0
        refresh_member_profiles(gid)
        with _db() as con:
            rows = con.execute("SELECT * FROM local_guild_members WHERE guild_id=? ORDER BY position,joined_at,role_id", (gid,)).fetchall() if gid else []
            out = b""
            for row in rows:
                out += _m(1, _member_dto(row))
            if gid:
                out += _v(2, gid)
        return out

    def handle_join_list(cmd_data, player):
        with _db() as con:
            guild = _guild_for_player(con, player)
        if guild:
            _refresh_role_profiles(guild["guild_id"], applications=True)
        with _db() as con:
            rows = con.execute("SELECT * FROM local_guild_applications WHERE guild_id=? ORDER BY apply_time", (guild["guild_id"],)).fetchall() if guild else []
            out = b""
            for row in rows:
                dto = _v(1, row["role_id"]) + _s(2, row["name"]) + _v(3, row["fight_power"]) + _v(4, row["level"])
                out += _m(1, dto)
        return out

    def handle_ack_join(cmd_data, player):
        role_id, agree = _int(cmd_data, 1), bool(_int(cmd_data, 2))
        with _db() as con:
            has_players = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='players'").fetchone()
            joined_row = con.execute("SELECT * FROM players WHERE role_id=?", (role_id,)).fetchone() if agree and has_players else None
        profile = _live_member_profile(dict(joined_row)) if joined_row else None
        with _db() as con:
            store.begin_immediate(con)
            owner = _member(con, player.get("uin", 0))
            if not owner or owner["position"] not in (1, 3):
                return b""
            application = con.execute("SELECT * FROM local_guild_applications WHERE guild_id=? AND role_id=?", (owner["guild_id"], role_id)).fetchone()
            if application:
                if agree:
                    joined_player = con.execute("SELECT uin,role_id,name,level,face_id FROM players WHERE role_id=?", (role_id,)).fetchone()
                    if joined_player:
                        guild = _guild(con, owner["guild_id"])
                        if not _joinable(con, dict(joined_player), guild):
                            return _logic_error(5008)
                        _add_member(con, guild, dict(joined_player), profile=profile)
                con.execute("DELETE FROM local_guild_applications WHERE guild_id=? AND role_id=?", (owner["guild_id"], role_id))
        return b""

    def handle_quit(cmd_data, player):
        uin = int(player.get("uin", 0))
        now = int(time.time())
        with _db() as con:
            member = _member(con, uin)
            if not member:
                return b""
            guild_id = member["guild_id"]
            remaining = con.execute("SELECT * FROM local_guild_members WHERE guild_id=? AND uin<>? ORDER BY position,joined_at", (guild_id, uin)).fetchall()
            if member["position"] == 1 and remaining:
                con.execute("UPDATE local_guild_members SET position=1 WHERE uin=?", (remaining[0]["uin"],))
            _notify_changed()
            con.execute("DELETE FROM local_guild_members WHERE uin=?", (uin,))
            con.execute("DELETE FROM local_guild_applications WHERE uin=?", (uin,))
            con.execute("INSERT INTO local_guild_players(uin,last_quit_time) VALUES(?,?) ON CONFLICT(uin) DO UPDATE SET last_quit_time=excluded.last_quit_time", (uin, now))
            if not remaining:
                con.execute("DELETE FROM local_guilds WHERE guild_id=?", (guild_id,))
                con.execute("DELETE FROM local_guild_applications WHERE guild_id=?", (guild_id,))
        return b""

    def handle_donate(cmd_data, player):
        # The wire amount is measured in 10,000-coin units (the donation UI
        # multiplies the typed number by UNION_DONATE_UNIT before checking it).
        amount = _int(cmd_data, 1)
        cost = amount * 10000
        uin = int(player.get("uin", 0))
        if amount <= 0:
            return _logic_error(5006)
        if amount > _DONATE_DAILY_UNITS:
            return _logic_error(5062)
        today = datetime.now().strftime("%Y%m%d")
        with _db() as con:
            member = _member(con, uin)
            if not member:
                return _logic_error(5007)
            store.begin_immediate(con)
            current = con.execute("SELECT * FROM local_guild_members WHERE uin=?", (uin,)).fetchone()
            prior = current["day_contrib"] if current["contrib_day"] == today else 0
            if prior + amount > _DONATE_DAILY_UNITS:
                return _logic_error(5062)
            cur = con.execute("UPDATE players SET coin=coin-? WHERE uin=? AND coin>=?", (cost, uin, cost))
            if cur.rowcount != 1:
                return _logic_error(202)
            con.execute("UPDATE players SET contrib_val=contrib_val+? WHERE uin=?", (amount, uin))
            con.execute("UPDATE local_guild_members SET contrib=contrib+?,day_contrib=?,contrib_day=? WHERE uin=?",
                        (amount, prior + amount, today, uin))
            con.execute("UPDATE local_guilds SET exp=exp+?,honor=honor+?,cohesion=cohesion+? WHERE guild_id=?",
                        (amount, amount, amount, member["guild_id"]))
        with _db() as con:
            row = con.execute("SELECT coin FROM players WHERE uin=?", (uin,)).fetchone()
        if row:
            player["coin"] = int(row["coin"])
        player["contrib_val"] = int(player.get("contrib_val") or 0) + amount
        with _db() as con:
            guild_info = _guild_info(con, _guild(con, member["guild_id"]))
            role_info = _role_guild_info(con, player)
        return [("PVZ.Cmd.Cmd_GuildDonate_SC", b""),
                ("PVZ.Cmd.Cmd_SyncCoin_SC", _v(1, player["coin"])),
                ("PVZ.Cmd.Cmd_SyncContribVal_SC", _v(1, player["contrib_val"])),
                ("PVZ.Cmd.Cmd_RoleGuildInfo_SC", _m(1, role_info)),
                ("PVZ.Cmd.Cmd_GuildInfoRefresh_SC", _m(1, guild_info))]

    def handle_skill_info(cmd_data, player):
        uin = int(player.get("uin", 0))
        with _db() as con:
            rows = con.execute("SELECT skill_id,level FROM local_guild_skills WHERE uin=?", (uin,)).fetchall()
        return b"".join(_m(1, _v(1, row["skill_id"]) + _v(2, row["level"])) for row in rows)

    def handle_skill_upgrade(cmd_data, player):
        uin, skill_id = int(player.get("uin", 0)), _int(cmd_data, 1)
        unlock, costs, limits = _skill_rules()
        with _db() as con:
            # Reserve the writer before reading the skill or eligibility. Two
            # sockets must each pay for their own next level, not both buy the
            # same level from a pre-transaction snapshot.
            store.begin_immediate(con)
            saved = con.execute("SELECT level,contrib_val FROM players WHERE uin=?",
                                (uin,)).fetchone()
            player_level = int(saved["level"] or 0) if saved else 0
            contribution = int(saved["contrib_val"] or 0) if saved else 0
            current = con.execute("SELECT level FROM local_guild_skills WHERE uin=? AND skill_id=?",
                                  (uin, skill_id)).fetchone()
            level = int(current["level"]) if current else 0
            next_level = level + 1
            cost = costs.get((skill_id, next_level))
            if (saved and _member(con, uin) and skill_id in unlock and cost is not None
                    and cost >= 0 and player_level >= unlock[skill_id]
                    and next_level <= limits.get(player_level, 0)):
                result = con.execute("UPDATE players SET contrib_val=contrib_val-? "
                                     "WHERE uin=? AND contrib_val>=?", (cost, uin, cost))
                if result.rowcount == 1:
                    con.execute("INSERT INTO local_guild_skills(uin,skill_id,level) VALUES(?,?,?) "
                                "ON CONFLICT(uin,skill_id) DO UPDATE SET level=excluded.level",
                                (uin, skill_id, next_level))
                    contribution -= cost
                    level = next_level
        # Even rejected requests synchronize the saved balance. A stale socket
        # must never subtract from its own cached contribution or report it.
        player["contrib_val"] = contribution
        if saved:
            player["level"] = player_level
        # The client dereferences SC.info.id unconditionally, including when a
        # request is rejected; always provide a complete Dto_SkillInfo.
        reply = _m(1, _v(1, skill_id) + _v(2, level))
        return [("PVZ.Cmd.Cmd_GuildSkill_Upgrade_SC", reply),
                ("PVZ.Cmd.Cmd_SyncContribVal_SC", _v(1, player.get("contrib_val") or 0))]

    def handle_change_notice(cmd_data, player):
        notice = _string(cmd_data, 1).strip()[:120]
        with _db() as con:
            member = _member(con, player.get("uin", 0))
            if not member:
                return _logic_error(5035)                       # 该角色未加入联盟
            if member["position"] not in (1, 3):
                return _logic_error(5007)                       # 权限不足，不能操作
            con.execute("UPDATE local_guilds SET notice=? WHERE guild_id=?",
                        (notice, member["guild_id"]))
            info = _guild_info(con, _guild(con, member["guild_id"]))
        # Cmd_GuildChangeNotice_SC carries no fields; the panel updates off the guild refresh.
        return [("PVZ.Cmd.Cmd_GuildChangeNotice_SC", b""),
                ("PVZ.Cmd.Cmd_GuildInfoRefresh_SC", _m(1, info))]

    def handle_guild_bid(cmd_data, player):
        """Cmd_GuildBid_CS{manifesto(1,string), guildExp(2)} -> Cmd_GuildBid_SC.

        The 联盟竞价 window (UnionBiddingWindowMediator.onClick -> UnionServiceCmd.unionBidding)
        is the only place a leader/deputy can type the guild's 联盟简介/宣言: its textarea starts
        from guild.manifesto and the 确定 button hands it to this command, waiting for
        Cmd_GuildBid_SC before it fires Union_Bidding_Success. With no route here the button
        looked dead — the reported "联盟左上角联盟简介无法填写".

        The bid replaces the guild's previous offer (refunded first) so the guild's 联盟经验 is
        conserved; the live offer is reported back as Dto_GuildFight_GuildInfo.credits.
        """
        manifesto = _string(cmd_data, 1).strip()[:120]
        bid = max(0, _int(cmd_data, 2))
        if not manifesto:
            return _logic_error(5006)                           # 联盟参数不正确 (空宣言)
        with _db() as con:
            member = _member(con, player.get("uin", 0))
            if not member:
                return _logic_error(5035)
            if member["position"] not in (1, 3):
                return _logic_error(5007)
            guild = _guild(con, member["guild_id"])
            if not guild:
                return _logic_error(5005)
            refund = max(0, int(guild["bid_exp"] or 0))
            if bid > int(guild["exp"] or 0) + refund:
                return _logic_error(5006)                       # 竞价经验超过联盟经验
            con.execute("UPDATE local_guilds SET manifesto=?, bid_exp=?, exp=exp+?-? WHERE guild_id=?",
                        (manifesto, bid, refund, bid, guild["guild_id"]))
            info = _guild_info(con, _guild(con, guild["guild_id"]))
        _log("guild %s bid %d exp, manifesto len=%d", guild["guild_id"], bid, len(manifesto))
        return [("PVZ.Cmd.Cmd_GuildBid_SC", b""),
                ("PVZ.Cmd.Cmd_GuildInfoRefresh_SC", _m(1, info))]

    def handle_change_qq(cmd_data, player):
        qq = _string(cmd_data, 1).strip()[:32]
        with _db() as con:
            member = _member(con, player.get("uin", 0))
            if member and member["position"] in (1, 3):
                con.execute("UPDATE local_guilds SET qq_group=? WHERE guild_id=?", (qq, member["guild_id"]))
        return b""

    def handle_change_name(cmd_data, player):
        name = re.sub(r"\s+", " ", _string(cmd_data, 1)).strip()
        if not 2 <= len(name) <= 24:
            return b""
        with _db() as con:
            member = _member(con, player.get("uin", 0))
            if member and member["position"] == 1:
                con.execute("UPDATE local_guilds SET name=? WHERE guild_id=?", (name, member["guild_id"]))
        return b""

    def handle_change_flag(cmd_data, player):
        flag = _fields(_submessage(cmd_data, 1))
        keys = []
        for field in range(1, 5):
            entry = flag.get(field, [])
            keys.append(int(entry[0][1]) if entry and entry[0][0] == 0 else 1)
        with _db() as con:
            member = _member(con, player.get("uin", 0))
            if member and member["position"] == 1:
                con.execute("UPDATE local_guilds SET color_id=?,color2_id=?,surface_id=?,sign_id=? WHERE guild_id=?", (*keys, member["guild_id"]))
        return b""

    def handle_kick(cmd_data, player):
        role_id = _int(cmd_data, 1)
        with _db() as con:
            actor = _member(con, player.get("uin", 0))
            target = con.execute("SELECT * FROM local_guild_members WHERE guild_id=? AND role_id=?", (actor["guild_id"], role_id)).fetchone() if actor else None
            if actor and target and actor["position"] in (1, 3) and target["position"] != 1 and actor["role_id"] != role_id:
                _notify_changed()
                con.execute("DELETE FROM local_guild_members WHERE guild_id=? AND role_id=?", (actor["guild_id"], role_id))
        return b""

    def handle_appoint(cmd_data, player):
        role_id, position = _int(cmd_data, 1), _int(cmd_data, 2)
        if position not in (2, 3, 4):
            return b""
        with _db() as con:
            actor = _member(con, player.get("uin", 0))
            target = con.execute("SELECT * FROM local_guild_members WHERE guild_id=? AND role_id=?", (actor["guild_id"], role_id)).fetchone() if actor else None
            if actor and actor["position"] == 1 and target and target["position"] != 1:
                con.execute("UPDATE local_guild_members SET position=? WHERE guild_id=? AND role_id=?", (position, actor["guild_id"], role_id))
        return b""

    def handle_set_quick_join(cmd_data, player):
        allow = bool(_int(cmd_data, 1))
        power = max(0, min(2**31 - 1, _int(cmd_data, 2)))
        with _db() as con:
            member = _member(con, player.get("uin", 0))
            if member and member["position"] == 1:
                con.execute("UPDATE local_guilds SET allow_quick_join=?,need_power=? WHERE guild_id=?", (int(allow), power, member["guild_id"]))
        return b""

    def handle_get_quick_join(cmd_data, player):
        with _db() as con:
            member = _member(con, player.get("uin", 0))
            guild = _guild(con, member["guild_id"]) if member else None
            return _v(1, guild["allow_quick_join"] if guild else 1) + _v(2, guild["need_power"] if guild else 0)

    def handle_search_summary(cmd_data, player):
        with _db() as con:
            base = _guild_base(con, _guild(con, _int(cmd_data, 1)))
        return _m(1, base) if base else b""

    def handle_observe(cmd_data, player):
        with _db() as con:
            base = _guild_base(con, _guild(con, _int(cmd_data, 1)))
        return _m(1, base) + _v(2, 0) + _v(3, 0) if base else b""

    def handle_honor_list(cmd_data, player):
        with _db() as con:
            guilds = con.execute("SELECT * FROM local_guilds ORDER BY honor DESC,guild_id LIMIT 100").fetchall()
            return b"".join(_m(1, _guild_info(con, guild)) for guild in guilds)

    def handle_join_invite_list(cmd_data, player):
        with _db() as con:
            rows = con.execute("""SELECT g.* FROM local_guild_invites i
                JOIN local_guilds g ON g.guild_id=i.guild_id WHERE i.invitee_uin=?
                ORDER BY i.created_at DESC""", (int(player.get("uin", 0)),)).fetchall()
            out = b""
            for row in rows:
                out += _m(1, _guild_base(con, row))
        return out

    def handle_invite_req(cmd_data, player):
        role_id = _int(cmd_data, 1)
        if not role_id:
            return b""
        with _db() as con:
            inviter = _member(con, player.get("uin", 0))
            target = con.execute("SELECT uin FROM players WHERE role_id=?", (role_id,)).fetchone()
            if not inviter or inviter["position"] not in (1, 3, 4) or not target:
                return b""
            if _member(con, target["uin"]):
                return b""
            con.execute("INSERT OR REPLACE INTO local_guild_invites(guild_id,inviter_uin,invitee_uin,created_at) VALUES(?,?,?,?)",
                        (inviter["guild_id"], inviter["uin"], target["uin"], int(time.time())))
            con.execute("INSERT OR IGNORE INTO local_guild_players(uin) VALUES(?)", (target["uin"],))
            con.execute("UPDATE local_guild_players SET invite_count=invite_count+1 WHERE uin=?", (target["uin"],))
        return b""

    def handle_invite_ack(cmd_data, player):
        guild_id, agree = _int(cmd_data, 1), bool(_int(cmd_data, 2))
        profile = _live_member_profile(player) if agree else None
        with _db() as con:
            invite = con.execute("SELECT * FROM local_guild_invites WHERE guild_id=? AND invitee_uin=?",
                                 (guild_id, int(player.get("uin", 0)))).fetchone()
            guild = _guild(con, guild_id)
            if invite:
                if agree and _joinable(con, player, guild):
                    _add_member(con, guild, player, profile=profile)
                con.execute("DELETE FROM local_guild_invites WHERE guild_id=? AND invitee_uin=?",
                            (guild_id, int(player.get("uin", 0))))
        return b""

    def handle_ignore_apply(cmd_data, player):
        gid = _int(cmd_data, 1)
        with _db() as con:
            actor = _member(con, player.get("uin", 0))
            guild_id = gid or (actor["guild_id"] if actor else 0)
            if actor and actor["guild_id"] == guild_id and actor["position"] in (1, 3):
                con.execute("DELETE FROM local_guild_applications WHERE guild_id=?", (guild_id,))
        return b""

    def handle_event_list(cmd_data, player):
        with _db() as con:
            guild = _guild_for_player(con, player)
            rows = con.execute("SELECT * FROM local_guild_events WHERE guild_id=? ORDER BY event_id DESC LIMIT 50", (guild["guild_id"],)).fetchall() if guild else []
            out = b""
            for row in rows:
                dto = _v(1, row["event_type"])
                if row["role_id"]:
                    user = _v(1, row["role_id"]) + _s(2, row["role_name"])
                    dto += _m(2, user)
                dto += _v(9, row["event_time"])
                out += _m(1, dto)
        return out

    handlers = {
        "PVZ.Cmd.Cmd_RoleGuildInfo_CS": handle_role_info,
        "PVZ.Cmd.Cmd_GuildInfo_CS": handle_guild_info,
        "PVZ.Cmd.Cmd_GuildList_CS": handle_guild_list,
        "PVZ.Cmd.Cmd_GuildCreate_CS": handle_create,
        "PVZ.Cmd.Cmd_GuildJoinReq_CS": handle_join_req,
        "PVZ.Cmd.Cmd_Guild_QuickJoin_CS": handle_quick_join,
        "PVZ.Cmd.Cmd_GuildMember_CS": handle_members,
        "PVZ.Cmd.Cmd_GuildJoinList_CS": handle_join_list,
        "PVZ.Cmd.Cmd_GuildAckJoin_CS": handle_ack_join,
        "PVZ.Cmd.Cmd_GuildQuit_CS": handle_quit,
        "PVZ.Cmd.Cmd_GuildDonate_CS": handle_donate,
        "PVZ.Cmd.Cmd_GuildChangeNotice_CS": handle_change_notice,
        "PVZ.Cmd.Cmd_GuildBid_CS": handle_guild_bid,
        "PVZ.Cmd.Cmd_GuildChangeQQ_CS": handle_change_qq,
        "PVZ.Cmd.Cmd_GuildChangeFlag_CS": handle_change_flag,
        "PVZ.Cmd.Cmd_Guild_ChangeName_CS": handle_change_name,
        "PVZ.Cmd.Cmd_GuildKick_CS": handle_kick,
        "PVZ.Cmd.Cmd_GuildAppoint_CS": handle_appoint,
        "PVZ.Cmd.Cmd_Guild_SetQuickJoin_CS": handle_set_quick_join,
        "PVZ.Cmd.Cmd_Guild_GetQuickJoin_CS": handle_get_quick_join,
        "PVZ.Cmd.Cmd_Guild_Search_SummaryInfo_CS": handle_search_summary,
        "PVZ.Cmd.Cmd_Guild_Observe_CS": handle_observe,
        "PVZ.Cmd.Cmd_GuildHonorList_CS": handle_honor_list,
        "PVZ.Cmd.Cmd_GuildInviteList_CS": handle_join_invite_list,
        "PVZ.Cmd.Cmd_GuildInviteReq_CS": handle_invite_req,
        "PVZ.Cmd.Cmd_GuildInviteAck_CS": handle_invite_ack,
        "PVZ.Cmd.Cmd_Guild_IgnoreApply_CS": handle_ignore_apply,
        "PVZ.Cmd.Cmd_GuildEventList_CS": handle_event_list,
        "PVZ.Cmd.Cmd_GuildSkill_Info_CS": handle_skill_info,
        "PVZ.Cmd.Cmd_GuildSkill_Upgrade_CS": handle_skill_upgrade,
    }
    _log("local guild adapter ready (%d core routes)", len(handlers))
    return handlers
