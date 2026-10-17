"""Offline account deletion without creating save backups.

The launcher is responsible for stopping its game/server processes and asking
the user for confirmation. These helpers never initialise a missing database or
create a player while looking up the account to show in that confirmation.
"""

import json
import os
from pathlib import Path
import re
import sqlite3

import player_store as store


def _account_number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("账号必须是 1 至 4294967295 的纯数字。")
    text = str(value)
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError("账号必须是 1 至 4294967295 的纯数字。")
    normalized = text.lstrip("0")
    if not normalized or len(normalized) > 10:
        raise ValueError("账号必须是 1 至 4294967295 的纯数字。")
    number = int(normalized)
    if not 1 <= number <= 4294967295:
        raise ValueError("账号必须是 1 至 4294967295 的纯数字。")
    return number


def _open_database(path, mode):
    # URI mode=ro/rw prevents SQLite from silently creating a missing file.
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=" + mode,
                          uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def get_account(uin):
    """Return an existing player dict or None; do not change login/save state."""
    number = _account_number(uin)
    if not os.path.isfile(store.DB_PATH):
        return None
    con = _open_database(store.DB_PATH, "ro")
    try:
        row = con.execute("SELECT * FROM players WHERE uin=?", (number,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _schema(con):
    return {
        row[0]: {col[1] for col in con.execute("PRAGMA table_info(%s)" % _quote(row[0]))}
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                               "AND name NOT LIKE 'sqlite_%'")
    }


def _remove(con, counts, table, where, params):
    cursor = con.execute("DELETE FROM %s WHERE %s" % (_quote(table), where), params)
    counts[table] = counts.get(table, 0) + cursor.rowcount


def _related_rows(con, schema, counts, uin, role_id):
    for table, columns, values in (
        ("friends", ("friend_role_id",), (role_id,)),
        ("local_friend_blacklist", ("role_id",), (role_id,)),
        ("local_friend_hidden", ("role_id",), (role_id,)),
        ("local_noend_votes", ("target_role_id",), (role_id,)),
        ("local_friend_applications", ("sender", "receiver"), (uin, uin)),
        ("local_friend_energy", ("sender", "receiver"), (uin, uin)),
        ("local_guild_invites", ("inviter_uin", "invitee_uin"), (uin, uin)),
        ("local_guild_events", ("role_id",), (role_id,)),
        # Wars store their owner in uin, but the opponent in target_uin and
        # role_id. Include the role identity for legacy rows without target_uin.
        ("local_worldmap_wars", ("target_uin", "role_id"), (uin, role_id)),
    ):
        available = [(column, value) for column, value in zip(columns, values)
                     if column in schema.get(table, set())]
        if available:
            _remove(con, counts, table, " OR ".join(_quote(c) + "=?" for c, _ in available),
                    tuple(value for _, value in available))

    if "owner_role_id" in schema.get("formation", set()):
        borrowed = con.execute("SELECT uin,team_id,uid FROM formation "
                               "WHERE owner_role_id=? AND uin<>?", (role_id, uin)).fetchall()
        _remove(con, counts, "formation", "owner_role_id=? AND uin<>?", (role_id, uin))
        if {"uin", "team_id", "uid"} <= schema.get("formation_leader", set()):
            for slot in borrowed:
                _remove(con, counts, "formation_leader", "uin=? AND team_id=? AND uid=? "
                        "AND NOT EXISTS (SELECT 1 FROM formation WHERE formation.uin=? "
                        "AND formation.team_id=? AND formation.uid=?)", tuple(slot) + tuple(slot))

    # A received mail and its attachment belong to the recipient. Preserve them;
    # only remove the deleted sender's live identity (this column stores uin).
    columns = schema.get("local_mails", set())
    if "send_role_id" in columns:
        assignments = ["send_role_id=0"]
        if "send_name" in columns:
            assignments.append("send_name='已删除账号'")
        if "send_face_id" in columns:
            assignments.append("send_face_id=0")
        con.execute("UPDATE local_mails SET %s WHERE send_role_id=?" % ",".join(assignments), (uin,))


def _guild_states(con, schema, counts, role_id, empty_guilds):
    if not {"uin", "feature", "data"} <= schema.get("local_feature_state", set()):
        return
    for guild_id in empty_guilds:
        _remove(con, counts, "local_feature_state", "uin=?", (-guild_id,))
    rows = con.execute("SELECT uin,feature,data FROM local_feature_state WHERE uin<0 "
                       "AND (feature='guild_leader' OR feature LIKE 'guild_hp_%_damage')").fetchall()
    for row in rows:
        try:
            data = json.loads(row["data"])
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        if row["feature"] == "guild_leader":
            if str(data.get("leader")) == str(role_id) or str(data.get("target")) == str(role_id):
                _remove(con, counts, "local_feature_state", "uin=? AND feature=?",
                        (row["uin"], row["feature"]))
        elif str(role_id) in data:
            del data[str(role_id)]
            con.execute("UPDATE local_feature_state SET data=? WHERE uin=? AND feature=?",
                        (json.dumps(data, ensure_ascii=False), row["uin"], row["feature"]))


def _finish_guilds(con, schema, counts, memberships, role_id):
    empty_guilds = []
    for member in memberships:
        guild_id = member["guild_id"]
        remaining = con.execute("SELECT * FROM local_guild_members WHERE guild_id=? "
                                "ORDER BY position,joined_at,role_id,uin", (guild_id,)).fetchall()
        if remaining:
            if member["position"] == 1:
                # Match the existing guild leave policy with deterministic ties.
                con.execute("UPDATE local_guild_members SET position=1 WHERE guild_id=? AND uin=?",
                            (guild_id, remaining[0]["uin"]))
            continue
        empty_guilds.append(guild_id)
        # Shared guild progress is removed only when its last member is gone.
        for table, columns in schema.items():
            if table.startswith("local_guild_") and "guild_id" in columns:
                _remove(con, counts, table, "guild_id=?", (guild_id,))
        if "guild_id" in schema.get("local_guilds", set()):
            _remove(con, counts, "local_guilds", "guild_id=?", (guild_id,))
    _guild_states(con, schema, counts, role_id, empty_guilds)


def delete_account(uin):
    """Atomically delete an existing account's data without creating a backup.

    Returns {uin, name, deleted_counts}. Errors propagate to the launcher
    and leave every database change rolled back. No database or account is
    created when the number is invalid or missing. External avatar assets and
    legacy databases and previously existing backups are left alone.
    """
    number = _account_number(uin)
    path = store.DB_PATH
    if not os.path.isfile(path):
        raise ValueError("存档数据库不存在，无法删除账号。")
    with store._ATOMIC_LOCK:
        con = _open_database(path, "rw")
        try:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("BEGIN IMMEDIATE")
            con.execute("PRAGMA defer_foreign_keys=ON")
            row = con.execute("SELECT * FROM players WHERE uin=?", (number,)).fetchone()
            if row is None:
                raise ValueError("账号 %s 不存在，未删除任何存档。" % uin)
            account = dict(row)
            role_id = int(account.get("role_id") or number)
            schema = _schema(con)
            memberships = []
            if "uin" in schema.get("local_guild_members", set()):
                memberships = con.execute("SELECT * FROM local_guild_members WHERE uin=?",
                                          (number,)).fetchall()
            counts = {}
            _related_rows(con, schema, counts, number, role_id)
            # Include legacy/migrated per-player tables automatically. Identifiers
            # originate only in SQLite's schema and are still quoted safely.
            for table, columns in schema.items():
                if table != "players" and "uin" in columns:
                    _remove(con, counts, table, "uin=?", (number,))
            _finish_guilds(con, schema, counts, memberships, role_id)
            _remove(con, counts, "players", "uin=?", (number,))
            con.commit()
            return {"uin": number, "name": account.get("name", ""),
                    "deleted_counts": counts}
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()
