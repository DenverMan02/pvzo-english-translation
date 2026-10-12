"""Local implementation of the four client features that had no server side.

Command-coverage audit (see ``../Assets/src/PVZ/Cmd/*.as``) found 767 distinct
``Cmd_*_CS`` messages in the client build.  The 24 requests below had no handler
at all and were falling through to the generic empty-``_SC`` reply, leaving the
client's panels dead:

===========================  ==================================================
module                       commands
===========================  ==================================================
邮件 Mail                    GetOutline / GetContent / Send / GuildSend /
                             Delete / AllDelete / ObtainAttachment /
                             ObtainAllAttachment
无尽副本 NoEndFuben (+ TD)   Info / Role / GetFormation / SetFormation /
                             SetPreCard / Fight / ConfirmFight / Sweep / Reset /
                             TakeReward / NoEndTD_Vote
支线任务 AuxQuest            GetAuxQuest / Operate / TakeReward
七日登录 SevenDayLogin       GetInfo / TakeReward
===========================  ==================================================

Every field number below comes straight out of the shipped ``.as`` protocol
classes; nothing is invented.  Progress lives in additive SQLite tables (same
pattern as ``guild_local``/``guild_modes_local``) so old saves keep working.

Install with ``register(api)``; the returned dict merges straight into
``pvz_poc_server.REQUEST_HANDLERS``.
"""
from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import struct
import sys
import time
from datetime import datetime, timedelta

_BASE = (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
         else os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(_BASE, "players.db")

_DB_PATH = DEFAULT_DB_PATH
_API: dict = {}

# 支线任务 DTO states (AuxQuestState.as)
AUX_NOT_EXE, AUX_FINISH, AUX_OVER, AUX_EXPIRE = 1, 2, 3, 4

# BattleTargetType_NoEndFuben, and the Dto_BattleTarget fields carrying the stage.
_NOEND_TARGET_TYPE = 26
_NOEND_MODE_FIELD, _NOEND_STAGE_FIELD = 22, 23

# 无尽副本的活动窗口按自然周结算（Cmd_NoEndFuben_Info_SC.beginTime/endTime）.
_WEEK_RESET_WEEKDAY = 0  # Monday

_MAIL_MAIL_MAX = 50          # Cmd_Mail_GetOutline_SC.numMax
_MAIL_SOURCE_SYSTEM = 1
_MAIL_SOURCE_PRIVATE = 2
_MAIL_SOURCE_GUILD = 3
# Dto_MailOutline.status: 0=未读 1=已读 2=附件已领取
_MAIL_UNREAD, _MAIL_READ, _MAIL_TAKEN = 0, 1, 2


# --------------------------------------------------------------------------
# protobuf primitives
# --------------------------------------------------------------------------
def _varint(value: int) -> bytes:
    value = max(0, int(value))
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _int64(value: int) -> bytes:
    return _varint(int(value) & ((1 << 64) - 1))


def _tag(field: int, wire: int) -> bytes:
    return _varint((int(field) << 3) | int(wire))


def _v(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


def _i64(field: int, value: int) -> bytes:
    return _tag(field, 0) + _int64(value)


def _b(field: int, value: bool) -> bytes:
    return _tag(field, 0) + _varint(1 if value else 0)


def _s(field: int, value: str) -> bytes:
    data = str(value or "").encode("utf-8")
    return _tag(field, 2) + _varint(len(data)) + data


def _d(field: int, value: float) -> bytes:
    """Wire type 1 (64-bit) — used by Dto_CardHpRatio.hpRatio."""
    return _tag(field, 1) + struct.pack("<d", float(value))


def _m(field: int, value: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(value)) + value


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


def _fields(data: bytes) -> dict:
    """{field: [(wire, value), ...]} skipping anything malformed."""
    result: dict = {}
    pos = 0
    data = bytes(data or b"")
    while pos < len(data):
        try:
            key, pos = _read_varint(data, pos)
        except ValueError:
            break
        field_num, wire = key >> 3, key & 7
        try:
            if wire == 0:
                value, pos = _read_varint(data, pos)
            elif wire == 1:
                value, pos = data[pos:pos + 8], pos + 8
            elif wire == 2:
                length, pos = _read_varint(data, pos)
                value = data[pos:pos + length]
                pos += length
            elif wire == 5:
                value, pos = data[pos:pos + 4], pos + 4
            else:
                break
        except ValueError:
            break
        result.setdefault(field_num, []).append((wire, value))
    return result


def _wire0(data: bytes, field: int, default: int = 0) -> int:
    values = _fields(data).get(field) or []
    for wire, value in values:
        if wire == 0:
            return int(value)
    return default


def _wire2(data: bytes, field: int) -> bytes:
    values = _fields(data).get(field) or []
    for wire, value in values:
        if wire == 2:
            return bytes(value)
    return b""


def _fixed64(data: bytes, field: int, default: float = 0.0) -> float:
    values = _fields(data).get(field) or []
    for wire, value in values:
        if wire == 1 and len(value) == 8:
            return struct.unpack("<d", value)[0]
    return default


def _text(data: bytes, field: int) -> str:
    return _wire2(data, field).decode("utf-8", "replace")


def _repeated_int64(data: bytes, field: int) -> list:
    return [int(v) for wire, v in _fields(data).get(field, []) if wire == 0]


def _repeated_messages(data: bytes, field: int) -> list:
    return [bytes(v) for wire, v in _fields(data).get(field, []) if wire == 2]


def _uin(player: dict) -> int:
    try:
        return int(player.get("uin") or 0)
    except (TypeError, ValueError):
        return 0


def _log(message: str, *args) -> None:
    sink = _API.get("log")
    if sink:
        try:
            sink(message, *args)
        except Exception:
            pass


def _logic_error(code: int):
    return [("PVZ.Cmd.Cmd_LogicError_SC", _v(1, code))]


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------
@contextlib.contextmanager
def _db():
    con = sqlite3.connect(_DB_PATH, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        con.execute("""CREATE TABLE IF NOT EXISTS local_mails (
            mail_id INTEGER PRIMARY KEY AUTOINCREMENT,
            uin INTEGER NOT NULL,
            source_type INTEGER NOT NULL DEFAULT 1,
            mail_type INTEGER NOT NULL DEFAULT 1,
            send_role_id INTEGER NOT NULL DEFAULT 0,
            send_name TEXT NOT NULL DEFAULT '',
            send_face_id INTEGER NOT NULL DEFAULT 0,
            status INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            attachment TEXT NOT NULL DEFAULT '',
            time INTEGER NOT NULL DEFAULT 0)""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_noend_fuben (
            uin INTEGER PRIMARY KEY,
            begin_time INTEGER NOT NULL DEFAULT 0,
            end_time INTEGER NOT NULL DEFAULT 0,
            opened INTEGER NOT NULL DEFAULT 1,
            mode_id INTEGER NOT NULL DEFAULT 1,
            stage_id INTEGER NOT NULL DEFAULT 1,
            history_max_stage_id INTEGER NOT NULL DEFAULT 1,
            lose_times INTEGER NOT NULL DEFAULT 0,
            report_id INTEGER NOT NULL DEFAULT 0,
            has_take_reward INTEGER NOT NULL DEFAULT 0,
            has_pass_next_stage INTEGER NOT NULL DEFAULT 0,
            reset_times INTEGER NOT NULL DEFAULT 0,
            best_stage_id INTEGER NOT NULL DEFAULT 0,
            reward_stage_id INTEGER NOT NULL DEFAULT 0,
            pre_card TEXT NOT NULL DEFAULT '')""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_noend_votes (
            uin INTEGER NOT NULL, mode INTEGER NOT NULL DEFAULT 0,
            target_role_id INTEGER NOT NULL DEFAULT 0,
            board_type INTEGER NOT NULL DEFAULT 0,
            up_down INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(uin, mode, target_role_id, board_type))""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_aux_quest (
            uin INTEGER NOT NULL, aux_quest_id INTEGER NOT NULL,
            target INTEGER NOT NULL DEFAULT 3,
            finish_num INTEGER NOT NULL DEFAULT 0,
            claimed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(uin, aux_quest_id))""")
        con.execute("""CREATE TABLE IF NOT EXISTS local_seven_day (
            uin INTEGER PRIMARY KEY,
            login_day INTEGER NOT NULL DEFAULT 0,
            has_reward INTEGER NOT NULL DEFAULT 0,
            last_seen TEXT NOT NULL DEFAULT '')""")
        # Defensive: a players.db saved by an older build may predate columns
        # added later.  ALTER is a no-op (caught) when the column exists.
        for _tbl, _col, _ddl in (
            ("local_noend_fuben", "pre_card", "TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                con.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_ddl}")
            except sqlite3.OperationalError:
                pass
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _rows(table: str) -> list:
    raw = (_API.get("read_clientdb_table") or (lambda _name: []))(table)
    if not raw:
        return []
    header = raw[0]
    result = []
    for row in raw[1:]:
        if len(row) >= len(header):
            result.append(dict(zip(header, row)))
    return result


def _parse_items(spec: str) -> list:
    """'25702:1;3200:3' / '1,100;2,200' -> [(item_id, count), ...]."""
    out = []
    for part in re.split(r"[;,，]", str(spec or "")):
        part = part.strip()
        if not part:
            continue
        bits = re.split(r"[:：]", part)
        if len(bits) != 2:
            continue
        try:
            item_id, count = int(bits[0]), int(bits[1])
        except ValueError:
            continue
        if item_id and count:
            out.append((item_id, count))
    return out


def _grant(player: dict, items) -> list:
    """Send [(id,num)] through the authoritative reward pipeline."""
    items = [pair for pair in items if pair]
    if not items:
        return []
    sink = _API.get("grant_reward")
    if not sink:
        return []
    try:
        return list(sink(player, items) or [])
    except Exception:
        _log("missing_features: reward grant failed for %s", items)
        return []


# --------------------------------------------------------------------------
# 邮件 Mail
# --------------------------------------------------------------------------
def _mail_outline(row: sqlite3.Row) -> bytes:
    dto = (_i64(1, row["mail_id"]) + _v(2, row["time"]) + _v(3, row["source_type"])
           + _v(4, row["mail_type"]) + _i64(5, row["send_role_id"])
           + _s(6, row["send_name"]) + _v(7, row["send_face_id"])
           + _v(8, row["status"]) + _s(9, row["title"]) + _s(11, ""))
    for item_id, count in _parse_items(row["attachment"]):
        dto += _m(10, _v(1, item_id) + _v(2, count))  # attachment: Dto_IdNum
    return dto


def _system_mail(uin: int, title: str, content: str, attachment: str = "") -> int:
    with _db() as con:
        cur = con.execute(
            "INSERT INTO local_mails(uin,source_type,mail_type,send_name,status,title,content,"
            "attachment,time) VALUES(?,?,?,?,?,?,?,?,?)",
            (uin, _MAIL_SOURCE_SYSTEM, 1, "系统", _MAIL_UNREAD, title, content,
             attachment, int(time.time())))
        return int(cur.lastrowid or 0)


# --------------------------------------------------------------------------
# 无尽副本 NoEndFuben
# --------------------------------------------------------------------------
def _week_window(now: float | None = None):
    now_dt = datetime.fromtimestamp(now or time.time())
    monday = (now_dt - timedelta(days=now_dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    if _WEEK_RESET_WEEKDAY:  # pragma: no cover - keeps the knob meaningful
        monday += timedelta(days=1)
    return int(monday.timestamp()), int((monday + timedelta(days=7)).timestamp())


def _noend_row(con: sqlite3.Connection, uin: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM local_noend_fuben WHERE uin=?", (uin,)).fetchone()
    if row is None:
        begin, end = _week_window()
        con.execute(
            "INSERT INTO local_noend_fuben(uin,begin_time,end_time,opened,mode_id,stage_id,"
            "history_max_stage_id,best_stage_id) VALUES(?,?,?,?,?,?,?,?)",
            (uin, begin, end, 1, 1, 1, 1, 0))
        row = con.execute("SELECT * FROM local_noend_fuben WHERE uin=?", (uin,)).fetchone()
    return row


def _noend_refresh(con: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
    """Roll the activity window; at most one蹭-free re-arm per week."""
    begin, end = _week_window()
    if int(row["end_time"]) and int(row["end_time"]) <= int(time.time()):
        con.execute("UPDATE local_noend_fuben SET begin_time=?,end_time=?,stage_id=?,"
                    "lose_times=0,has_take_reward=0,has_pass_next_stage=0,reward_stage_id=0 "
                    "WHERE uin=?", (begin, end, 1, int(row["uin"])))
        row = con.execute("SELECT * FROM local_noend_fuben WHERE uin=?",
                          (int(row["uin"]),)).fetchone()
    return row


def _card_hp_dto(uid: int, hp_ratio: float = 1.0) -> bytes:
    """Dto_CardHpRatio{uid(1,int64), hpRatio(2,double)} — one deployable plant."""
    return _i64(1, uid) + _d(2, hp_ratio)


def _noend_info_sc(row: sqlite3.Row) -> bytes:
    """Cmd_NoEndFuben_Info_SC.

    Field 11 (cardHpRatio) is what the client uses to decide whether the
    "choose your plants" popup must be shown: EndlessFubenModel.isNeedSetPreCard()
    returns true while that list is empty, and EndlessFubenProxy re-opens the
    popup on every Info reply — which pins the panel in an endless refresh loop
    and makes the 确认出战 button look dead. Always echo the saved pre-card list.
    """
    body = (_v(1, row["begin_time"]) + _v(2, row["end_time"]) + _b(3, row["opened"])
            + _v(4, row["mode_id"]) + _v(5, row["stage_id"])
            + _v(6, row["history_max_stage_id"]) + _v(7, row["lose_times"])
            + _i64(8, row["report_id"]) + _b(9, row["has_take_reward"])
            + _b(12, row["has_pass_next_stage"]) + _v(13, row["reset_times"])
            + _v(14, row["best_stage_id"]) + _v(15, row["reward_stage_id"]))
    for uid in _pre_cards(row):                       # cardHpRatio (field 11)
        body += _m(11, _card_hp_dto(uid))
    return body


def _pre_cards(row) -> list:
    """The saved 出战植物 uids for the current round."""
    raw = str(row["pre_card"] or "") if row is not None else ""
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit() and int(part) > 0:
            out.append(int(part))
    return out


def _noend_rank_rows(con: sqlite3.Connection):
    """Best NoEnd depth first — joined with the account table for the display name."""
    try:
        return con.execute(
            "SELECT n.uin AS uin, n.history_max_stage_id AS history_max_stage_id, "
            "COALESCE(p.name,'') AS role_name "
            "FROM local_noend_fuben n LEFT JOIN players p ON p.uin=n.uin "
            "ORDER BY n.history_max_stage_id DESC, n.uin ASC LIMIT 100").fetchall()
    except sqlite3.Error:                # standalone DB without the account table
        return con.execute(
            "SELECT uin AS uin, history_max_stage_id AS history_max_stage_id, '' AS role_name "
            "FROM local_noend_fuben ORDER BY history_max_stage_id DESC, uin ASC LIMIT 100"
        ).fetchall()


def _noend_role_dto(rank: int, row) -> bytes:
    stage = int(row["history_max_stage_id"] if "history_max_stage_id" in row.keys()
                else row["stage_id"])
    return (_v(1, rank) + _v(2, int(row["uin"])) + _s(3, row["role_name"])
            + _s(4, "") + _v(5, stage))


def _noend_stage_reward(stage_id: int) -> list:
    """endlessReward.txt: rankStart<=stage<=rankEnd -> rewardStr."""
    for row in _rows("endlessReward.txt"):
        try:
            low, high = int(row["rankStart"]), int(row["rankEnd"])
        except (KeyError, ValueError):
            continue
        if low <= stage_id <= high:
            return _parse_items(row.get("rewardStr", ""))
    return []


def _noend_formation(player: dict) -> bytes:
    if _API.get("ensure_player_cards"):
        _API["ensure_player_cards"](player)
    placements = (_API.get("default_formation_placements") or (lambda _p: {}))(player)
    return (_API.get("build_formation_info") or (lambda _p, _t, _x: b""))(player, 1, placements)


def _noend_run_battle(player: dict, mode_id: int, stage_id: int):
    """Play a real TD battle. Returns the Dto_FightReport body or b""."""
    target = (_v(1, _NOEND_TARGET_TYPE) + _v(_NOEND_MODE_FIELD, mode_id)
              + _v(_NOEND_STAGE_FIELD, stage_id))
    if not _API.get("run_fight"):
        return b""
    try:
        result = _API["run_fight"](_m(1, target), player)
    except Exception:
        _log("missing_features: noend battle crashed at stage %s", stage_id)
        return b""
    if not isinstance(result, list) or not result:
        return b""
    return _wire2(result[0][1], 1)


def _battle_won(report: bytes) -> bool:
    """Dto_FightReport.result: 0 means the attacking side won."""
    if not report:
        return False
    return _wire0(report, 1, 1) == 0


# --------------------------------------------------------------------------
# 支线任务 AuxQuest
# --------------------------------------------------------------------------
def _aux_target(con: sqlite3.Connection, aux_quest_id: int) -> int:
    """Target count for a side quest.

    The client carries no AuxQuest template table, so the quota is the one
    recorded for this quest already (or the module default of 3 steps).
    """
    try:
        return max(1, int(_API.get("aux_quest_target") or 3))
    except (TypeError, ValueError):
        return 3


def _aux_row(con: sqlite3.Connection, uin: int, aux_quest_id: int, create: bool = True):
    row = con.execute("SELECT * FROM local_aux_quest WHERE uin=? AND aux_quest_id=?",
                      (uin, aux_quest_id)).fetchone()
    if row is None and create:
        con.execute("INSERT INTO local_aux_quest(uin,aux_quest_id,target,finish_num,claimed)"
                    " VALUES(?,?,?,0,0)", (uin, aux_quest_id, _aux_target(con, aux_quest_id)))
        row = con.execute("SELECT * FROM local_aux_quest WHERE uin=? AND aux_quest_id=?",
                          (uin, aux_quest_id)).fetchone()
    return row


def _aux_state(row) -> tuple:
    if row is None:
        return AUX_NOT_EXE, 0
    target = max(1, int(row["target"]))
    finish = min(int(row["finish_num"]), target)
    if int(row["claimed"]):
        return AUX_OVER, finish
    if finish >= target:
        return AUX_FINISH, finish
    return AUX_NOT_EXE, finish


def _aux_dto(row, aux_quest_id: int) -> bytes:
    state, finish = _aux_state(row)
    return _v(1, aux_quest_id) + _v(2, state) + _v(3, finish)


# --------------------------------------------------------------------------
# 七日登录 SevenDayLogin
# --------------------------------------------------------------------------
_SEVEN_DAY_ROWS = None


def _seven_day_rows() -> list:
    global _SEVEN_DAY_ROWS
    if _SEVEN_DAY_ROWS is None:
        _SEVEN_DAY_ROWS = _rows("sevenDay.txt")
    return _SEVEN_DAY_ROWS


def _seven_day_reward(day: int) -> list:
    for row in _seven_day_rows():
        if row.get("id") == str(day):
            return _parse_items(row.get("rewardItemStr", ""))
    return []


def _seven_day_state(con: sqlite3.Connection, uin: int):
    row = con.execute("SELECT * FROM local_seven_day WHERE uin=?", (uin,)).fetchone()
    today = datetime.now().strftime("%Y%m%d")
    if row is None:
        con.execute("INSERT INTO local_seven_day(uin,login_day,has_reward,last_seen)"
                    " VALUES(?,?,?,?)", (uin, 1, 0, today))
        row = con.execute("SELECT * FROM local_seven_day WHERE uin=?", (uin,)).fetchone()
    elif row["last_seen"] != today:
        con.execute("UPDATE local_seven_day SET login_day=login_day+1, last_seen=? WHERE uin=?",
                    (today, uin))
        row = con.execute("SELECT * FROM local_seven_day WHERE uin=?", (uin,)).fetchone()
    return row


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def register(api=None, db_path=None):
    """Return the 24 missing routes. ``api`` may carry server callbacks."""
    global _API, _DB_PATH, _SEVEN_DAY_ROWS
    _API = dict(api or {})
    _DB_PATH = db_path or _API.get("db_path") or DEFAULT_DB_PATH
    _SEVEN_DAY_ROWS = None

    # ---------------- 邮件 ----------------
    def mail_outline(cmd_data, player):
        source_type = _wire0(cmd_data, 1, 0)
        start = max(0, _wire0(cmd_data, 2, 0))
        end = max(start, _wire0(cmd_data, 3, 19))
        begin = min(start, end)
        limit = min(max(end - begin + 1, 1), 20)
        uin = _uin(player)
        with _db() as con:
            where, args = "", [uin]
            if source_type:
                where, args = " AND source_type=?", [uin, source_type]
            total = con.execute(
                "SELECT COUNT(*) FROM local_mails WHERE uin=?" + where, args).fetchone()[0]
            rows = con.execute(
                "SELECT * FROM local_mails WHERE uin=?" + where +
                " ORDER BY mail_id DESC LIMIT ? OFFSET ?", args + [limit, begin]).fetchall()
            attach_num = con.execute(
                "SELECT COUNT(*) FROM local_mails WHERE uin=? AND attachment<>'' "
                "AND status<>?", (uin, _MAIL_TAKEN)).fetchone()[0]
            body = b"".join(_m(2, _mail_outline(row)) for row in rows)
        return (_v(1, total) + body + _v(3, _MAIL_MAIL_MAX) + _v(4, attach_num))

    def mail_get_content(cmd_data, player):
        mail_id = _wire0(cmd_data, 1, 0)
        uin = _uin(player)
        with _db() as con:
            row = con.execute("SELECT * FROM local_mails WHERE mail_id=? AND uin=?",
                              (mail_id, uin)).fetchone()
            if row is None:
                return b""
            if int(row["status"]) == _MAIL_UNREAD:
                con.execute("UPDATE local_mails SET status=? WHERE mail_id=?",
                            (_MAIL_READ, mail_id))
        # addContent(3) carries a battle report for defense/arena mails only:
        # nothing to attach for the plain mails this module can originate.
        return _i64(1, mail_id) + _s(2, row["content"])

    def mail_send(cmd_data, player):
        role_id = _wire0(cmd_data, 1, 0)
        content = _text(cmd_data, 2)[:500]
        title = _text(cmd_data, 3)[:60] or "(无主题)"
        if not content:
            return b""
        resolver = _API.get("uin_for_role_id")
        target = int(resolver(role_id)) if resolver and role_id else 0
        if target:
            try:
                with _db() as con:
                    con.execute(
                        "INSERT INTO local_mails(uin,source_type,mail_type,send_role_id,"
                        "send_name,status,title,content,attachment,time) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (target, _MAIL_SOURCE_PRIVATE, 1, _uin(player),
                         str(player.get("name") or ""), _MAIL_UNREAD, title, content, "",
                         int(time.time())))
            except sqlite3.Error:
                _log("missing_features: could not deliver mail to role %s", role_id)
        return b""

    def mail_guild_send(cmd_data, player):
        content = _text(cmd_data, 1)[:500]
        title = _text(cmd_data, 2)[:60] or "(群发邮件)"
        if not content:
            return b""
        uin = _uin(player)
        with _db() as con:
            try:
                guild_row = con.execute(
                    "SELECT guild_id FROM local_guild_members WHERE uin=?", (uin,)).fetchone()
            except sqlite3.Error:
                guild_row = None
            targets = []
            if guild_row:
                targets = [int(r["uin"]) for r in con.execute(
                    "SELECT uin FROM local_guild_members WHERE guild_id=?",
                    (int(guild_row["guild_id"]),)).fetchall()]
            if not targets:
                targets = [uin]
            name = str(player.get("name") or "")
            for target in targets:
                con.execute(
                    "INSERT INTO local_mails(uin,source_type,mail_type,send_role_id,send_name,"
                    "status,title,content,attachment,time) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (target, _MAIL_SOURCE_GUILD, 1, uin, name, _MAIL_UNREAD, title, content,
                     "", int(time.time())))
        return b""

    def mail_delete(cmd_data, player):
        mail_id = _wire0(cmd_data, 1, 0)
        with _db() as con:
            con.execute("DELETE FROM local_mails WHERE mail_id=? AND uin=?",
                        (mail_id, _uin(player)))
        return _i64(1, mail_id)

    def mail_all_delete(cmd_data, player):
        source_type = _wire0(cmd_data, 1, 0)
        with _db() as con:
            args = [_uin(player)]
            clause = ""
            if source_type:
                clause, args = " AND source_type=?", [_uin(player), source_type]
            con.execute("DELETE FROM local_mails WHERE uin=?" + clause, args)
        return b""

    def mail_obtain_attachment(cmd_data, player):
        mail_id = _wire0(cmd_data, 1, 0)
        uin = _uin(player)
        with _db() as con:
            row = con.execute("SELECT * FROM local_mails WHERE mail_id=? AND uin=?",
                              (mail_id, uin)).fetchone()
            if row is None or not row["attachment"]:
                return _i64(1, mail_id)
            con.execute("UPDATE local_mails SET status=?, attachment='' WHERE mail_id=?",
                        (_MAIL_TAKEN, mail_id))
        items = _parse_items(row["attachment"])
        return [("PVZ.Cmd.Cmd_Mail_ObtainAttachment_SC", _i64(1, mail_id))] + \
            _grant(player, items)

    def mail_obtain_all_attachment(cmd_data, player):
        uin = _uin(player)
        changes = []
        with _db() as con:
            rows = con.execute(
                "SELECT * FROM local_mails WHERE uin=? AND attachment<>'' AND status<>?",
                (uin, _MAIL_TAKEN)).fetchall()
            for row in rows:
                changes += _parse_items(row["attachment"])
                con.execute("UPDATE local_mails SET status=?, attachment='' WHERE mail_id=?",
                            (_MAIL_TAKEN, int(row["mail_id"])))
        attach_body = b""
        for item_id, count in changes:
            attach_body += _m(1, _v(1, item_id) + _v(2, count))  # attachment(1)
        return [("PVZ.Cmd.Cmd_Mail_ObtainAllAttachment_SC",
                 attach_body + _v(2, len(rows)))] + _grant(player, changes)

    # ---------------- 无尽副本 ----------------
    def noend_info(cmd_data, player):
        uin = _uin(player)
        with _db() as con:
            row = _noend_refresh(con, _noend_row(con, uin))
        return _noend_info_sc(row)

    def noend_role(cmd_data, player):
        uin = _uin(player)
        with _db() as con:
            _noend_refresh(con, _noend_row(con, uin))   # make sure we rank ourselves
            ranked = _noend_rank_rows(con)
            self_rank = 0
            for index, row in enumerate(ranked, 1):
                if int(row["uin"]) == uin:
                    self_rank = index
                    break
            first = _noend_role_dto(1, ranked[0]) if ranked else b""
            prev = b""
            if self_rank > 1:
                prev = _noend_role_dto(self_rank - 1, ranked[self_rank - 2])
            self_dto = _noend_role_dto(self_rank, ranked[self_rank - 1]) if self_rank else b""
        return (_m(1, first) if first else b"") + (_m(2, prev) if prev else b"") + \
            (_m(3, self_dto) if self_dto else b"")

    def noend_get_formation(cmd_data, player):
        return [("PVZ.Cmd.Cmd_NoEndFuben_SyncFormation_SC", _m(1, _noend_formation(player)))]

    def noend_set_formation(cmd_data, player):
        formation = _wire2(cmd_data, 1)
        positions = {}
        for card in _repeated_messages(formation, 1):
            pos, uid = _wire0(card, 1, 0), _wire0(card, 2, 0)
            if pos and uid:
                positions[pos] = uid
        if positions and _API.get("set_formation"):
            _API["set_formation"](_uin(player), 1, positions)
        return [("PVZ.Cmd.Cmd_NoEndFuben_SyncFormation_SC", _m(1, _noend_formation(player)))]

    def noend_set_pre_card(cmd_data, player):
        """Persist the 出战植物 list chosen in the prepare popup.

        This is the whole point of the 确认出战 button: the client only sends the
        chosen uids, and until the server echoes them back inside
        ``Cmd_NoEndFuben_Info_SC.cardHpRatio`` the player never gets past the
        popup — it re-opens on every refresh and every click looks ignored.
        """
        cards = [c for c in _repeated_int64(cmd_data, 1) if c > 0]
        uin = _uin(player)
        with _db() as con:
            _noend_row(con, uin)
            con.execute("UPDATE local_noend_fuben SET pre_card=? WHERE uin=?",
                        (",".join(str(c) for c in cards), uin))
        return b"".join(_i64(1, c) for c in cards)

    def noend_fight(cmd_data, player):
        """Run stage N. A win advances one floor, a loss only raises loseTimes."""
        stage_id = max(1, _wire0(cmd_data, 1, 1))
        uin = _uin(player)
        with _db() as con:
            row = _noend_refresh(con, _noend_row(con, uin))
            mode_id = int(row["mode_id"])
        report = _noend_run_battle(player, mode_id, stage_id)
        won = _battle_won(report)
        now_ts = int(time.time())
        if won:
            # Clearing floor N unlocks N+1; the loot table is keyed by the floor
            # actually cleared, so best_stage_id tracks that separately.
            with _db() as con:
                con.execute(
                    "UPDATE local_noend_fuben SET stage_id=MAX(stage_id,?),"
                    "history_max_stage_id=MAX(history_max_stage_id,?),"
                    "best_stage_id=MAX(best_stage_id,?),has_pass_next_stage=1,report_id=? "
                    "WHERE uin=?", (stage_id + 1, stage_id + 1, stage_id, now_ts, uin))
        else:
            with _db() as con:
                con.execute(
                    "UPDATE local_noend_fuben SET lose_times=lose_times+1,"
                    "has_pass_next_stage=0,report_id=? WHERE uin=?", (now_ts, uin))
        with _db() as con:
            row = _noend_row(con, uin)
        return (_v(1, stage_id) + _v(2, int(row["history_max_stage_id"]))
                + _b(3, bool(int(row["has_pass_next_stage"])))
                + _v(4, int(row["lose_times"])) + _i64(5, int(row["report_id"]))
                + (_m(6, report) if report else b""))

    def noend_confirm_fight(cmd_data, player):
        """Keep the surviving plants after a cleared floor.

        The client updates its line-up from ``cardHpRatio`` (field 2) here and
        then re-renders the panel; without the list it silently drops the
        plants and the player has to re-pick every round.
        """
        stage_id = max(1, _wire0(cmd_data, 1, 1))
        uin = _uin(player)
        with _db() as con:
            row = _noend_row(con, uin)
        body = _v(1, stage_id)
        for uid in _pre_cards(row):            # cardHpRatio (field 2)
            body += _m(2, _card_hp_dto(uid))
        return body

    def noend_sweep(cmd_data, player):
        """Clear every floor already proven (<= best) and pay out its loot."""
        uin = _uin(player)
        with _db() as con:
            row = _noend_refresh(con, _noend_row(con, uin))
            top = max(1, int(row["best_stage_id"]) or int(row["history_max_stage_id"]) - 1)
        items = []
        for stage in range(1, top + 1):
            items += _noend_stage_reward(stage)
        changes = []
        for item_id, count in items:
            changes.append((item_id, count))
        messages = _grant(player, changes)
        reward = b"".join(_m(2, _v(1, iid) + _v(2, num)) for iid, num in changes)
        return [("PVZ.Cmd.Cmd_NoEndFuben_Sweep_SC", _v(1, top) + reward)] + messages

    def noend_reset(cmd_data, player):
        """Start a new round: the saved plants are cleared, so the client asks
        for a fresh selection on the next Info refresh."""
        uin = _uin(player)
        with _db() as con:
            row = _noend_row(con, uin)
            con.execute("UPDATE local_noend_fuben SET stage_id=1,lose_times=0,"
                        "reset_times=reset_times+1,has_pass_next_stage=0,pre_card='' "
                        "WHERE uin=?", (uin,))
            row = _noend_row(con, uin)
        return _v(1, int(row["reset_times"]))

    def noend_take_reward(cmd_data, player):
        """Weekly prize for the deepest floor cleared (endlessReward.txt)."""
        uin = _uin(player)
        with _db() as con:
            row = _noend_row(con, uin)
            if int(row["has_take_reward"]):
                items = []
            else:
                items = _noend_stage_reward(max(1, int(row["best_stage_id"])))
                con.execute("UPDATE local_noend_fuben SET has_take_reward=1 WHERE uin=?", (uin,))
        reward = b"".join(_m(1, _v(1, iid) + _v(2, num)) for iid, num in items)
        return [("PVZ.Cmd.Cmd_NoEndFuben_TakeReward_SC", reward)] + _grant(player, items)

    def noendtd_vote(cmd_data, player):
        mode = _wire0(cmd_data, 1, 0)
        target = _wire0(cmd_data, 2, 0)
        board = _wire0(cmd_data, 3, 0)
        up_down = 1 if _wire0(cmd_data, 4, 0) else 0
        uin = _uin(player)
        with _db() as con:
            con.execute(
                "INSERT INTO local_noend_votes(uin,mode,target_role_id,board_type,up_down)"
                " VALUES(?,?,?,?,?) ON CONFLICT(uin,mode,target_role_id,board_type) "
                "DO UPDATE SET up_down=excluded.up_down", (uin, mode, target, board, up_down))
        return b""

    # ---------------- 支线任务 ----------------
    def aux_get(cmd_data, player):
        aux_quest_id = _wire0(cmd_data, 1, 0)
        with _db() as con:
            row = _aux_row(con, _uin(player), aux_quest_id)
        return _m(1, _aux_dto(row, aux_quest_id))

    def aux_operate(cmd_data, player):
        aux_quest_id = _wire0(cmd_data, 1, 0)
        deal_num = max(1, _wire0(cmd_data, 2, 1))
        uin = _uin(player)
        with _db() as con:
            row = _aux_row(con, uin, aux_quest_id)
            row = _aux_row(con, uin, aux_quest_id)  # re-read after insert
            target = max(1, int(row["target"]))
            finish = min(target, int(row["finish_num"]) + deal_num)
            con.execute("UPDATE local_aux_quest SET finish_num=? WHERE uin=? AND aux_quest_id=?",
                        (finish, uin, aux_quest_id))
            row = _aux_row(con, uin, aux_quest_id)
        return _m(1, _aux_dto(row, aux_quest_id))

    def aux_take_reward(cmd_data, player):
        aux_quest_id = _wire0(cmd_data, 1, 0)
        uin = _uin(player)
        with _db() as con:
            row = _aux_row(con, uin, aux_quest_id)
            state, _finish = _aux_state(row)
            if state != AUX_FINISH:
                return _logic_error(5006)
            con.execute("UPDATE local_aux_quest SET claimed=1 WHERE uin=? AND aux_quest_id=?",
                        (uin, aux_quest_id))
        # The client ships no AuxQuest reward template, so the payout — if any —
        # is supplied by the server callback; default is "no loot, just credit".
        resolver = _API.get("aux_quest_reward")
        items = list(resolver(aux_quest_id)) if resolver else []
        return [("PVZ.Cmd.Cmd_AuxQuest_TakeReward_SC", _v(1, aux_quest_id))] + \
            _grant(player, items)

    # ---------------- 七日登录 ----------------
    def seven_day_info(cmd_data, player):
        uin = _uin(player)
        with _db() as con:
            row = _seven_day_state(con, uin)
        # progress(2) carries the per-day task counters; the client renders the
        # seven checkboxes from loginDay + progress, so keep it present-but-zero
        # rather than letting it default to an undefined value.
        return (_v(1, int(row["login_day"])) + _v(2, 0)
                + _v(3, int(row["has_reward"])) + _b(4, True))

    def seven_day_take(cmd_data, player):
        uin = _uin(player)
        with _db() as con:
            row = _seven_day_state(con, uin)
            day = int(row["login_day"])
            mask = int(row["has_reward"])
            if day < 1 or day > 7 or (mask >> (day - 1)) & 1:
                return _logic_error(5006)
            items = _seven_day_reward(day)
            con.execute("UPDATE local_seven_day SET has_reward=? WHERE uin=?",
                        (mask | (1 << (day - 1)), uin))
        body = b"".join(_m(1, _v(1, iid) + _v(2, num)) for iid, num in items)  # itemList
        return [("PVZ.Cmd.Cmd_SevenDayLogin_TakeReward_SC", body)] + _grant(player, items)

    return {
        # 邮件
        "PVZ.Cmd.Cmd_Mail_GetOutline_CS": mail_outline,
        "PVZ.Cmd.Cmd_Mail_GetContent_CS": mail_get_content,
        "PVZ.Cmd.Cmd_Mail_Send_CS": mail_send,
        "PVZ.Cmd.Cmd_Mail_GuildSend_CS": mail_guild_send,
        "PVZ.Cmd.Cmd_Mail_Delete_CS": mail_delete,
        "PVZ.Cmd.Cmd_Mail_AllDelete_CS": mail_all_delete,
        "PVZ.Cmd.Cmd_Mail_ObtainAttachment_CS": mail_obtain_attachment,
        "PVZ.Cmd.Cmd_Mail_ObtainAllAttachment_CS": mail_obtain_all_attachment,
        # 无尽副本
        "PVZ.Cmd.Cmd_NoEndFuben_Info_CS": noend_info,
        "PVZ.Cmd.Cmd_NoEndFuben_Role_CS": noend_role,
        "PVZ.Cmd.Cmd_NoEndFuben_GetFormation_CS": noend_get_formation,
        "PVZ.Cmd.Cmd_NoEndFuben_SetFormation_CS": noend_set_formation,
        "PVZ.Cmd.Cmd_NoEndFuben_SetPreCard_CS": noend_set_pre_card,
        "PVZ.Cmd.Cmd_NoEndFuben_Fight_CS": noend_fight,
        "PVZ.Cmd.Cmd_NoEndFuben_ConfirmFight_CS": noend_confirm_fight,
        "PVZ.Cmd.Cmd_NoEndFuben_Sweep_CS": noend_sweep,
        "PVZ.Cmd.Cmd_NoEndFuben_Reset_CS": noend_reset,
        "PVZ.Cmd.Cmd_NoEndFuben_TakeReward_CS": noend_take_reward,
        "PVZ.Cmd.Cmd_NoEndTD_Vote_CS": noendtd_vote,
        # 支线任务
        "PVZ.Cmd.Cmd_AuxQuest_GetAuxQuest_CS": aux_get,
        "PVZ.Cmd.Cmd_AuxQuest_Operate_CS": aux_operate,
        "PVZ.Cmd.Cmd_AuxQuest_TakeReward_CS": aux_take_reward,
        # 七日登录
        "PVZ.Cmd.Cmd_SevenDayLogin_GetInfo_CS": seven_day_info,
        "PVZ.Cmd.Cmd_SevenDayLogin_TakeReward_CS": seven_day_take,
    }
