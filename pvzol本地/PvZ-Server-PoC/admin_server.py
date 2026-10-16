#!/usr/bin/env python3
"""Plants vs. Zombies Online Local Admin Panel。

Listens only on 127.0.0.1 and reuses save and level logic from player_store.py / gm.py.
No dependencies beyond the Python standard library.
"""

import argparse
import contextlib
import datetime as _dt
import html
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import traceback
from urllib.parse import parse_qs, urlencode, urlsplit

if getattr(sys, "frozen", False):
    # PyInstaller 打包后 __file__ 指向临时解包目录，备份/数据应落在 exe 同级目录。
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gm  # noqa: E402
import player_store as store  # noqa: E402
from launcher_guard import guard_blocks_writes  # noqa: E402


ADMIN_HOST = "127.0.0.1"
ADMIN_PORT = 8081
MAX_POST_BYTES = 64 * 1024
CSRF_TOKEN = secrets.token_urlsafe(32)
BACKUP_DIR = os.path.join(HERE, "backups", "admin")
WRITE_LOCK = threading.RLock()
EMBED_COOKIE = "pvz_admin_embedded"


def _launcher_origin():
    """Allow only the explicitly configured launcher loopback HTTP origin."""
    raw = os.environ.get("PVZ_LAUNCHER_ORIGIN", "")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or
            parsed.username is not None or parsed.password is not None or
            parsed.path not in ("", "/") or parsed.query or parsed.fragment or
            port is None or not 1 <= port <= 65535):
        return ""
    return "http://127.0.0.1:%s" % port


class ValidationError(ValueError):
    pass


def _client_name_map(table, id_field="id", name_field="name"):
    result = {}
    for row in (gm._clientdb_dicts(table) or []):
        if not row or not isinstance(row, dict):
            continue
        try:
            key = int(row.get(id_field) or 0)
        except (TypeError, ValueError):
            continue
        name = (row.get(name_field) or "").strip()
        if key > 0:
            result[key] = name or str(key)
    return result


ITEM_ROWS = [r for r in (gm._clientdb_dicts("ItemInfo.txt") or []) if r and isinstance(r, dict)]
ITEM_NAMES = {}
for _item_row in ITEM_ROWS:
    try:
        _item_id = int(_item_row.get("id") or 0)
    except (TypeError, ValueError):
        continue
    if _item_id > 0:
        ITEM_NAMES[_item_id] = (_item_row.get("name") or str(_item_id)).strip()
PLANT_NAMES = _client_name_map("tdPlantTypeInfo.txt")
CARD_NAMES = _client_name_map("battleCard.txt")
CARD_BASE_NAMES = _client_name_map("battleCard.txt", id_field="tdId")
LEVEL_ROWS = [
    row for row in (gm._clientdb_dicts("levelselectInfo.txt") or [])
    if row and isinstance(row, dict) and (row.get("challengeLevelId", "0") or "0") in ("0", "")
]
LEVEL_KEYS = {
    (int(row.get("stageId") or 0), int(row.get("levelId") or 0),
     int(row.get("subLevelId") or 0))
    for row in LEVEL_ROWS
    if str(row.get("stageId") or "0").isdigit()
    and str(row.get("levelId") or "0").isdigit()
    and str(row.get("subLevelId") or "0").isdigit()
}

# ItemInfo 混合了真正Inventory、Account Currency、卡牌展示物、装备实例、
# Reagents实例以及一批只用于埋点的“购买原因”。后台必须按真实模型分流，
# 否则会把装备/卡牌伪造成客户端无法解释的普通堆栈。
CURRENCY_GRANTS = {
    1: ("coin", "Coins"),
    3: ("sun", "阳光"),
    4: ("present_ticket", "钻石"),
    6: ("friend_point", "Friend Points"),
    7: ("fame", "Prestige"),
    9: ("energy", "Stamina"),
    37: ("ticket", "Gold Diamonds"),
    39: ("contrib_val", "Personal Contribution"),
    40: ("light", "Portable Light Source"),
    41: ("puppet_skill_val", "Puppet Skill Points"),
    42: ("puppet_energy", "Puppet Energy"),
}
SPECIAL_STACK_IDS = {2, 38, 43}  # 繁荣度、洗炼幸运点、威望：服务器明确按 bag stack 消耗

ITEM_KIND_LABELS = {
    "stack": "Inventory Items",
    "currency": "Account Currency",
    "card": "Plant Cards",
    "equipment": "装备",
    "reagent": "Reagents",
}

ITEM_TYPE_LABELS = {
    1: "General", 2: "Materials / Consumables", 4: "Plant Cards", 5: "Plant Fragments",
    6: "XP Bean", 7: "装备", 8: "Lab Materials", 9: "Chlorophyll",
    10: "Puzzle Pieces", 11: "Reagents", 12: "Energy Stone", 13: "Genes / Puppet Materials",
    14: "Cultivation Fluid", 15: "Puppet Display Entry", 16: "Expedition Resources",
}


def _row_int(row, key, default=0):
    if not row or not isinstance(row, dict):
        return default
    try:
        val = row.get(key)
        if val is None or val == "":
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def _item_grant_kind(row):
    item_id = _row_int(row, "id")
    item_type = _row_int(row, "type")
    if item_id in CURRENCY_GRANTS:
        return "currency"
    if item_id == 5:  # 植物XP在本服务器没有账号级数据模型
        return None
    if item_type == 4:
        card_spec = str(row.get("cardItemId") or "")
        return "card" if card_spec.split(":", 1)[0].isdigit() else None
    if item_type == 7:
        return "equipment"
    if item_type == 11:
        # 60001 Reagents残渣是“药粉”转换物，不是可装备的Reagents实例。
        return "reagent" if item_id != 60001 else None
    if item_type == 15:  # ItemInfo id != dummyZombieInfo.baseId，不能直接建傀儡
        return None
    if item_type == 1:
        # type=1 的低 ID 大部分是付费/Action埋点；只放行真正声明了堆叠上限的物品。
        return "stack" if gm.is_stack_item(row) else None
    if item_type in (2, 5, 6, 8, 9, 10, 12, 13, 14, 16):
        return "stack" if gm.is_stack_item(row) else None
    return None


ITEM_CATALOG = []
for _row in ITEM_ROWS:
    _kind = _item_grant_kind(_row)
    _iid = _row_int(_row, "id")
    if not _kind or _iid <= 0:
        continue
    _entry = {
        "id": _iid,
        "ids": (_iid,),
        "name": (_row.get("name") or str(_iid)).strip(),
        "desc": (_row.get("desc") or "").replace("<br>", " · ").strip(),
        "gain_way": (_row.get("gainWay") or "").strip(),
        "type": _row_int(_row, "type"),
        "sub_type": _row_int(_row, "subType"),
        "quality": max(0, _row_int(_row, "quality")),
        "min_level": max(0, _row_int(_row, "minLvRequired")),
        "stack_num": max(0, _row_int(_row, "stackNum")),
        "card_spec": str(_row.get("cardItemId") or "").strip(),
        "kind": _kind,
    }
    _entry["search"] = ("%s %s %s %s %s %s" % (
        _entry["id"], _entry["name"], _entry["desc"], _entry["gain_way"],
        ITEM_KIND_LABELS[_kind], ITEM_TYPE_LABELS.get(_entry["type"], ""))).casefold()
    ITEM_CATALOG.append(_entry)
ITEM_CATALOG.sort(key=lambda item: item["id"])
ITEM_CATALOG_BY_ID = {item["id"]: item for item in ITEM_CATALOG}

# These two authored IDs describe the same equipment-energy gift. Group only
# the catalog display; grants and existing inventory retain their exact IDs.
# Compare every client field so a future configuration change separates them.
_item_rows_by_id = {_row_int(row, "id"): row for row in ITEM_ROWS}
_same_gift_ids = (3726, 3734)
if all(iid in ITEM_CATALOG_BY_ID for iid in _same_gift_ids):
    _same_gift_rows = [{key: value for key, value in _item_rows_by_id[iid].items()
                        if key != "id"} for iid in _same_gift_ids]
    if _same_gift_rows[0] == _same_gift_rows[1]:
        _same_gift = dict(ITEM_CATALOG_BY_ID[3726], ids=_same_gift_ids)
        _same_gift["search"] += " 3734"
        ITEM_CATALOG = [_same_gift if item["id"] == 3726 else item
                        for item in ITEM_CATALOG if item["id"] != 3734]


PUZZLE_GROUP_ITEMS = {}
for _row in ITEM_ROWS:
    _iid = _row_int(_row, "id")
    if _row_int(_row, "type") == 10 and _row_int(_row, "subType") == 1001:
        PUZZLE_GROUP_ITEMS.setdefault(_iid // 100, {})[_iid] = _row

# Derive the shared material and board size from the actual puzzle costs.
PUZZLE_SHARED_MATERIALS = {}
PUZZLE_REFERENCED_ITEMS = {
    _row_int(row, "id") for row in gm._clientdb_dicts("puzzlePrice.txt")
}
for _row in gm._clientdb_dicts("cardPuzzle.txt"):
    _costs = [part.split(":", 1)[0] for part in
              str(_row.get("originalCostItem") or "").split(";") if part]
    PUZZLE_REFERENCED_ITEMS.update(int(cost) for cost in _costs if cost.isdigit())
    _cells = _row_int(_row, "puzzleWidth") * _row_int(_row, "puzzleHeight")
    if _cells > 1 and len(_costs) == _cells and len(set(_costs)) == 1 and _costs[0].isdigit():
        _iid = int(_costs[0])
        if _iid in PUZZLE_GROUP_ITEMS.get(_iid // 100, {}):
            PUZZLE_SHARED_MATERIALS[_iid] = _cells


def _invalid_puzzle_details(item_id):
    if item_id in ITEM_NAMES or item_id in PUZZLE_REFERENCED_ITEMS:
        return None
    group, offset = divmod(item_id, 100)
    configured = PUZZLE_GROUP_ITEMS.get(group, {})
    if not configured or item_id in configured:
        return None
    if group in (610, 620) and 1 <= offset <= 16:
        ids = sorted(configured)
        if ids == list(range(ids[0], ids[-1] + 1)):
            allowed = "%s–%s" % (ids[0], ids[-1]) if len(ids) > 1 else str(ids[0])
        else:
            allowed = "、".join(map(str, ids))
        color = "白色" if group == 610 else "绿色"
        return ("Invalid puzzle ID (ID %s)" % item_id,
                "%s puzzle · valid IDs for this tier: %s · original quantity preserved" % (color, allowed))
    base_id = group * 100 + 1
    if 660 <= group <= 679 and 2 <= offset <= 25 and set(configured) == {base_id}:
        cells = PUZZLE_SHARED_MATERIALS.get(base_id)
        if cells:
            return ("Invalid puzzle ID (ID %s)" % item_id,
                    "%s · %s slots in this tier share ID %s · original quantity preserved" %
                    (ITEM_NAMES[base_id], cells, base_id))
    return None


def _inventory_item_details(item_id):
    """Resolve historical bag entries without inventing grantable items.

    Old saves contain plant IDs and card prototype IDs in the stack table.
    These names are verifiable in their own client tables, but they are not
    ItemInfo entries and must not be added to the ordinary item catalog.
    """
    if item_id in ITEM_NAMES:
        return ITEM_NAMES[item_id], ""
    if item_id in PLANT_NAMES:
        return PLANT_NAMES[item_id], "Tower Defense plant ID · legacy inventory entry"
    if item_id in CARD_BASE_NAMES:
        return CARD_BASE_NAMES[item_id], "Plant card prototype ID · legacy inventory entry"
    puzzle_details = _invalid_puzzle_details(item_id)
    if puzzle_details:
        return puzzle_details
    return "Unknown item (ID %s)" % item_id, "Not defined by the current client · original quantity preserved"


def _inventory_item_html(item_id):
    name, detail = _inventory_item_details(item_id)
    return esc(name) + ('<div class="sub">%s</div>' % esc(detail) if detail else "")


EDIT_FIELDS = (
    ("name", "Character Name", "text", None, None),
    ("level", "Account Level", "number", 1, max(gm.player_level_values())),
    ("exp", "XP", "number", 0, 2_000_000_000),
    ("coin", "Coins", "number", 0, 2_000_000_000),
    ("sun", "阳光", "number", 0, 2_000_000_000),
    ("energy", "Stamina", "number", 0, 2_000_000_000),
    ("present_ticket", "Blue Diamonds", "number", 0, 2_000_000_000),
    ("ticket", "Gold Diamonds", "number", 0, 2_000_000_000),
    ("fame", "Prestige", "number", 0, 2_000_000_000),
    ("friend_point", "好友点", "number", 0, 2_000_000_000),
)


def esc(value):
    return html.escape(str(value), quote=True)


def _connect():
    con = sqlite3.connect(store.DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    return con


@contextlib.contextmanager
def _connection():
    """sqlite3.Connection 的 with 只提交事务，这里额外确保句柄被Off。"""
    con = _connect()
    try:
        yield con
    finally:
        con.close()


def _player(uin):
    with _connection() as con:
        row = con.execute("SELECT * FROM players WHERE uin=?", (int(uin),)).fetchone()
        return dict(row) if row else None


def _require_player(uin):
    player = _player(uin)
    if not player:
        raise ValidationError("Account %s does not exist. Create it on the home page first." % uin)
    return player


def _snapshot_input(player, keys):
    snapshot = {key: player.get(key) for key in keys}
    snapshot["uin"] = int(player["uin"])
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(CSRF_TOKEN.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return ('<input type="hidden" name="baseline" value="%s">'
            '<input type="hidden" name="baseline_sig" value="%s">') % (esc(raw), signature)


def _baseline(form, uin):
    raw = (form.get("baseline", [""])[0] or "")
    signature = (form.get("baseline_sig", [""])[0] or "")
    actual = hmac.new(CSRF_TOKEN.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not raw or not hmac.compare_digest(signature, actual):
        raise ValidationError("This edit page has expired. Refresh and save again.")
    try:
        baseline = json.loads(raw)
    except (ValueError, TypeError):
        raise ValidationError("Invalid edit snapshot. Refresh and save again.")
    if not isinstance(baseline, dict) or baseline.get("uin") != uin:
        raise ValidationError("The edit snapshot does not match this account. Refresh and try again.")
    return baseline


def _save_incremental(uin, values, baseline):
    """Check and update only fields changed by the user, in one write transaction."""
    if any(key not in baseline for key in values):
        raise ValidationError("The edit snapshot is missing fields. Refresh and try again.")
    changed = {key: value for key, value in values.items() if value != baseline[key]}
    player = _require_player(uin)
    with store.atomic_player(player) as con:
        for key in changed:
            if player.get(key) != baseline[key]:
                raise ValidationError("%s was changed by another operation. Refresh the page and review it again." % key)
        for key, dependencies in (("vip_level", ("vip_card_id", "vip_endtime")),
                                  ("td_privilege", ("td_privilege_endtime",))):
            if key in changed:
                for dependency in dependencies:
                    if dependency not in baseline or player.get(dependency) != baseline[dependency]:
                        raise ValidationError("The membership expiration was changed by another operation. Refresh the page and review it again.")
        if "vip_level" in changed:
            changed.update(vip_card_id=0, vip_endtime=0)
        if "td_privilege" in changed:
            changed["td_privilege_endtime"] = int(time.time()) + 86400 * 3650 if changed["td_privilege"] else 0
        if "name" in changed:
            from profile_editor import validate_name
            try:
                changed["name"] = validate_name(con, player, changed["name"])
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        if changed:
            store.save_player(uin, **changed)
            if "name" in changed:
                from profile_editor import sync_name
                sync_name(con, player, changed["name"])
    return len(changed)


def _integer(form, key, minimum=None, maximum=None, label=None):
    raw = (form.get(key, [""])[0] or "").strip()
    title = label or key
    if key == "uin":
        try:
            return gm.validate_uin(raw)
        except ValueError as exc:
            raise ValidationError(str(exc))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError("%s must be an integer." % title)
    if minimum is not None and value < minimum:
        raise ValidationError("%s must be at least %s." % (title, minimum))
    if maximum is not None and value > maximum:
        raise ValidationError("%s must be no greater than %s." % (title, maximum))
    return value


def _format_time(value):
    try:
        value = int(value or 0)
        if value <= 0:
            return "—"
        return _dt.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "—"


def _backup_database(reason):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "-" for c in reason)[:48]
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = os.path.join(BACKUP_DIR, "%s-%s.db" % (stamp, safe_reason or "backup"))
    source = sqlite3.connect(store.DB_PATH, timeout=30)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return destination


def _repair_invalid_puzzles(uin):
    """Archive only proven obsolete puzzle IDs, then remove their bag rows.

    Hold SQLite's writer reservation while making the backup so it contains
    the exact rows removed. Never convert cell indices into real materials:
    old saves already contain the configured shared stack separately.
    """
    _require_game_stopped()
    _require_player(uin)
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        rows = [dict(row) for row in con.execute(
            "SELECT item_id, count FROM items WHERE uin=? ORDER BY item_id", (uin,))
            if _invalid_puzzle_details(row["item_id"]) is not None]
        if not rows:
            con.rollback()
            return {"count": 0, "backup": None, "records": None}
        backup = _backup_database("before-puzzle-repair-%s" % uin)
        records = backup + ".json"
        with open(records, "x", encoding="utf-8") as stream:
            json.dump({"uin": uin, "backup": os.path.basename(backup),
                       "reason": "旧逻辑按格子序号生成了客户端未定义的Puzzle Pieces编号",
                       "items": rows}, stream, ensure_ascii=False, indent=2)
        _require_game_stopped()
        removed = con.executemany("DELETE FROM items WHERE uin=? AND item_id=? AND count=?",
                                 [(uin, row["item_id"], row["count"]) for row in rows])
        if removed.rowcount != len(rows):
            raise ValidationError("The inventory changed. Refresh before repairing it.")
        con.commit()
        return {"count": len(rows), "backup": backup, "records": records}
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _reset_player(uin):
    """备份并原子重建目标新手档，按账户生命周期规则处理会长与空会。"""
    from account_management import _schema, _remove, _finish_guilds

    _require_game_stopped()
    player = _require_player(uin)
    with store.atomic_player(player) as con:
        # Use the backup helper's independent read connection before mutations;
        # backing up this write transaction's own connection can wait forever.
        backup = _backup_database("before-reset-%s" % uin)
        _require_game_stopped()
        schema = _schema(con)
        memberships = []
        if "uin" in schema.get("local_guild_members", set()):
            memberships = con.execute("SELECT * FROM local_guild_members WHERE uin=?",
                                      (uin,)).fetchall()
        counts = {}
        for table, columns in schema.items():
            if table != "players" and "uin" in columns:
                _remove(con, counts, table, "uin=?", (uin,))
        _finish_guilds(con, schema, counts, memberships, int(player.get('role_id') or uin))
        _remove(con, counts, "players", "uin=?", (uin,))
        # Joining this transaction keeps deletion and native starter creation
        # together if either creation or shared-guild cleanup fails.
        store.get_or_create_player(uin)
    return backup, [(table, rows) for table, rows in counts.items() if rows]


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.08):
            return True
    except OSError:
        return False


def _require_game_stopped():
    if guard_blocks_writes() or _port_open(9001):
        raise ValidationError("The game is running. Stop it in the launcher before editing saves; no changes were written.")


EMBED_SECTIONS = frozenset(("attributes", "inventory", "plants", "cards", "progress", "vip", "friends", "reset"))


def _embedded_url(path, *, theme="", section=""):
    """Carry the iframe's display context through local links and form actions."""
    if not path.startswith("/") or path.startswith("//"):
        return path
    parsed = urlsplit(path)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["embedded"] = ["1"]
    if theme in ("light", "dark") and not query.get("theme", [""])[0]:
        query["theme"] = [theme]
    if (section in EMBED_SECTIONS and not query.get("section", [""])[0]
            and (parsed.path in ("/player", "/items") or parsed.path.startswith("/player/"))):
        query["section"] = [section]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


BASE_CSS = r"""
:root{color-scheme:dark;--bg:#11180d;--panel:#1c2815;--panel2:#24351a;--line:#38502a;
--text:#edf5e6;--muted:#9eaf91;--green:#8bd450;--green2:#b8ed8f;--red:#ef795e;--gold:#e6bc55}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#10170c,#182311 55%,#11180d);
color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
a{color:var(--green2);text-decoration:none}a:hover{text-decoration:underline}.wrap{max-width:1160px;margin:auto;padding:0 22px}
header{border-bottom:1px solid var(--line);background:#131d0fdd;position:sticky;top:0;z-index:2;backdrop-filter:blur(8px)}
.head{display:flex;gap:18px;align-items:center;padding:14px 0}.brand{font-weight:800;font-size:19px;color:#fff}.tag{color:var(--muted);font-size:12px}
nav{margin-left:auto;display:flex;gap:14px}.main{padding:28px 0 52px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}
.card{background:linear-gradient(145deg,var(--panel),#172110);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 12px 32px #0003}
.span12{grid-column:span 12}.span8{grid-column:span 8}.span7{grid-column:span 7}.span6{grid-column:span 6}.span5{grid-column:span 5}.span4{grid-column:span 4}
h1{font-size:26px;margin:0 0 18px}h2{font-size:18px;margin:0 0 14px}h3{font-size:14px;margin:18px 0 8px;color:var(--green2)}
.sub{color:var(--muted);font-size:13px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric{background:#10180c;border:1px solid #304522;border-radius:11px;padding:13px}
.metric b{display:block;font-size:22px;color:#fff}.metric span{color:var(--muted);font-size:12px}.status{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:99px;background:#0f180b;border:1px solid var(--line);font-size:12px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--red)}.dot.on{background:var(--green);box-shadow:0 0 8px var(--green)}
form{margin:0}.row{display:flex;gap:10px;align-items:end;flex-wrap:wrap}.field{min-width:120px;flex:1}.field.small{max-width:145px}.field label{display:block;color:var(--muted);font-size:12px;margin:0 0 4px}
input,select{width:100%;border:1px solid #3b522d;background:#0e160a;color:var(--text);border-radius:8px;padding:9px 10px;font:inherit;outline:none}
input:focus,select:focus{border-color:var(--green)}button,.btn{display:inline-block;border:0;border-radius:8px;padding:9px 14px;background:var(--green);color:#14200e;font-weight:750;cursor:pointer;font:inherit}
button:hover,.btn:hover{filter:brightness(1.08);text-decoration:none}.secondary{background:#304524;color:var(--text)}.danger{background:#a94331;color:white}.smallbtn{padding:5px 9px;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px 8px;border-bottom:1px solid #304324;vertical-align:middle}th{color:var(--muted);font-weight:600}tr:last-child td{border-bottom:0}.num{font-variant-numeric:tabular-nums}.scroll{overflow:auto}
.flash{border-radius:10px;padding:11px 14px;margin:0 0 18px;border:1px solid #53773a;background:#23381a}.flash.error{border-color:#8d493b;background:#3b201a;color:#ffd7ce}
.pills{display:flex;gap:7px;flex-wrap:wrap}.pill{border:1px solid #3b542c;background:#10190c;border-radius:99px;padding:4px 9px;font-size:12px}.empty{color:var(--muted);padding:20px 0;text-align:center}
.warning{border-left:3px solid var(--gold);padding:9px 12px;background:#2a2413;color:#f3d78d;font-size:13px;border-radius:5px}.dangerbox{border-color:#783c31;background:#241613}
footer{color:#78876d;font-size:12px;padding:20px 0;border-top:1px solid #293b20}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.right{text-align:right}.nowrap{white-space:nowrap}
@media(max-width:780px){.span8,.span7,.span6,.span5,.span4{grid-column:span 12}.metrics{grid-template-columns:repeat(2,1fr)}nav .optional{display:none}.wrap{padding:0 13px}.main{padding-top:18px}}
"""

# Shared visual language with the desktop launcher. Offline, no CDN dependency.
BASE_CSS += r"""
:root{color-scheme:light;--bg:#f4f6f3;--panel:#fff;--panel2:#f0f5ed;--line:#e2e8df;
--text:#24342b;--muted:#829087;--green:#4c875b;--green2:#487b54;--red:#c86a57;--gold:#d6a64b}
body{background:var(--bg);font:14px/1.65 'Microsoft YaHei','Segoe UI',sans-serif}
header{background:#ffffffed;border-color:var(--line)}.brand{color:var(--text);font-size:17px}.tag{background:#edf4e9;padding:3px 10px;border-radius:20px}
.wrap{max-width:1240px;padding:0 26px}.main{padding:24px 0 40px}.grid{gap:18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:23px;box-shadow:0 3px 15px #263a2910}
h1{font-size:24px;letter-spacing:-.6px;margin-bottom:20px}h2{font-size:17px;color:var(--text);font-weight:650}h3{color:var(--green2)}
.metric{background:#f5f8f3;border:0;border-radius:12px;padding:16px}.metric b{color:var(--text);font-size:21px}.metric span{color:var(--muted)}
.status{background:#fff;border-color:var(--line);color:var(--text)}.dot{background:#cbd4c7}.dot.on{background:#76a570;box-shadow:none}
input,select{background:#f9fbf8;color:var(--text);border-color:#dfe7db;border-radius:9px;padding:10px 12px;transition:border-color .15s,box-shadow .15s}
input:focus,select:focus{box-shadow:0 0 0 3px #80a87120}button,.btn{background:var(--green);color:#fff;border-radius:9px;padding:10px 15px;font-size:13px;font-weight:600}
.secondary{background:#edf3e9;color:#5c775b}.danger{background:#f6e9e4;color:#b25e4e}button:hover,.btn:hover{filter:brightness(.97)}
th,td{border-color:#edf1ea;padding:11px 10px}th{font-size:12px;background:#f7f9f5;font-weight:500}.scroll{border-radius:10px}
.flash{border-color:#dbe8d5;background:#eff6e9;color:#517143}.flash.error{background:#fbefea;border-color:#ebd1c6;color:#b3614e}
.pill{border-color:#e1e8dc;background:#f7f9f3;color:#64805c;border-radius:7px}.warning{background:#fff8e5;color:#a18140;border-color:#dfb256}
.dangerbox{background:#fffaf7;border-color:#efdfd6}.empty{color:#92a18e}footer{border-color:var(--line);color:#95a08e}
.embedded>header,.embedded>footer{display:none}.embedded .wrap{max-width:none;padding:0 4px}.embedded .main{padding:10px 5px 24px}
.gm-tabs{display:flex;gap:7px;flex-wrap:wrap;margin:0 0 20px;padding:6px;background:#eaf0e5;border-radius:12px;align-items:center}
.gm-tabs a{padding:9px 14px;border-radius:8px;color:#74846f;font-size:13px}.gm-tabs a:hover{text-decoration:none;background:#f7faf4}
.gm-tabs a.active{background:#fff;color:#4e7852;box-shadow:0 2px 7px #53664612;font-weight:650}
.gm-hidden{display:none!important}.gm-panel{grid-column:span 12}.gm-panel .row{gap:13px}.gm-panel .field{flex:1 1 160px}
.gm-panel.dangerbox{max-width:none}.pager{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:17px;font-size:12px;color:var(--muted)}
@media(prefers-color-scheme:dark){body.auto-theme{color-scheme:dark}}
body.dark-theme{color-scheme:dark;--bg:#1c241f;--panel:#252f28;--panel2:#2c392f;--line:#39483c;--text:#dce5d9;--muted:#91a28e;--green:#73a174;--green2:#a1c596}
body.dark-theme .card{background:var(--panel)}body.dark-theme .metric,body.dark-theme th,body.dark-theme input,body.dark-theme select{background:#202a22}
body.dark-theme .gm-tabs,body.dark-theme .secondary{background:#29382b}body.dark-theme .gm-tabs a.active{background:#3b4d3c;color:#c7dfbf}
body.dark-theme .status,body.dark-theme .pill{background:#2b382c;color:#bdd1b6}body.dark-theme header{background:#252f28}

/* Embedded workbench follows the launcher's white, slate and sky-blue theme. */
body.embedded{color-scheme:light;--bg:#f8fafc;--panel:#fff;--panel2:#f1f5f9;--line:#e2e8f0;
--text:#0f172a;--muted:#475569;--green:#0ea5e9;--green2:#0284c7;--red:#dc5b61;--gold:#eab308;
background:var(--bg);color:var(--text);font:14px/1.65 'Microsoft YaHei UI','Microsoft YaHei','Segoe UI',sans-serif}
.embedded .wrap{max-width:none;padding:0 24px}.embedded .main{padding:24px}.embedded .grid{gap:16px}
.embedded h1{font-size:18px;line-height:1.5;letter-spacing:0;margin:0 0 24px;font-weight:700}
.embedded h2{font-size:17px;line-height:1.5;margin:0 0 16px;font-weight:650;color:var(--text)}
.embedded h3{font-size:16px;line-height:1.5;margin:24px 0 16px;color:var(--text)}
.embedded .card{background:var(--panel);border-color:var(--line);border-radius:14px;padding:24px;box-shadow:0 3px 16px #0f172a05}
.embedded .metrics{gap:16px}.embedded .metric{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:16px}
.embedded .metric b{font-size:22px;line-height:1.4;color:var(--text)}
.embedded .metric span,.embedded .field label,.embedded .tag,.embedded .status,.embedded .smallbtn,.embedded .pill,.embedded .pager,.embedded .sub,.embedded .warning{font-size:13px;color:var(--muted)}
.embedded .field label{margin-bottom:8px}.embedded .row,.embedded .gm-panel .row{gap:16px}
.embedded input,.embedded select{font-size:14px;line-height:1.5;color:var(--text);background:var(--panel);border-color:var(--line);border-radius:8px;padding:10px 12px}
.embedded input:focus,.embedded select:focus{border-color:var(--green);box-shadow:0 0 0 3px #0ea5e91a}
.embedded button,.embedded .btn{font-size:14px;line-height:1.5;font-weight:600;padding:10px 16px;border-radius:8px;background:var(--green);color:#fff}
.embedded .smallbtn{font-size:13px;padding:6px 12px}.embedded button:hover,.embedded .btn:hover{filter:brightness(.97)}
.embedded .secondary{background:var(--panel2);color:var(--muted);border:1px solid var(--line)}
.embedded .danger{background:#fff1f2;color:#be123c;border:1px solid #fecdd3}
.embedded table{font-size:14px}.embedded th,.embedded td{padding:12px 16px;border-color:var(--line)}
.embedded th{font-size:13px;font-weight:600;background:var(--panel2);color:var(--muted)}
.embedded .gm-tabs{gap:8px;margin-bottom:24px;padding:8px;background:var(--panel2);border:1px solid var(--line);border-radius:10px}
.embedded .gm-tabs a{font-size:14px;line-height:1.5;padding:10px 16px;color:var(--muted);border-radius:7px}
.embedded .gm-tabs a:hover{background:#e0f2fe;color:var(--green2)}
.embedded .gm-tabs a.active{background:var(--panel);color:var(--green2);font-weight:650;box-shadow:0 2px 6px #0f172a08}
.embedded .status,.embedded .pill,.embedded .tag{background:var(--panel2);border-color:var(--line);color:var(--muted)}
.embedded .dot{background:#94a3b8}.embedded .dot.on{background:var(--green);box-shadow:none}
.embedded .flash{font-size:14px;padding:16px;margin-bottom:24px;border-color:#bae6fd;background:#f0f9ff;color:#0369a1}
.embedded .flash.error{border-color:#fecdd3;background:#fff1f2;color:#be123c}
.embedded .warning{background:#fffbeb;color:#92400e;border-color:#eab308;padding:12px 16px}
.embedded .dangerbox{background:var(--panel);border-color:#fecdd3}.embedded .empty{font-size:14px;color:var(--muted);padding:24px 0}
.embedded .pager{margin-top:16px;gap:16px}.embedded .pills{gap:8px}.embedded .pill{padding:6px 12px}
body.embedded.dark-theme{color-scheme:dark;--bg:#101722;--panel:#182230;--panel2:#1c2939;--line:#2c3b50;
--text:#e9eff7;--muted:#b8c6d8;--green:#38bdf8;--green2:#7dd3fc;--red:#fb7185;--gold:#facc15}
.embedded.dark-theme .card,.embedded.dark-theme input,.embedded.dark-theme select{background:var(--panel)}
.embedded.dark-theme .metric,.embedded.dark-theme th,.embedded.dark-theme .gm-tabs,.embedded.dark-theme .secondary,.embedded.dark-theme .status,.embedded.dark-theme .pill,.embedded.dark-theme .tag{background:var(--panel2);color:var(--muted)}
.embedded.dark-theme .gm-tabs a.active{background:var(--panel);color:var(--green2)}
.embedded.dark-theme .gm-tabs a:hover{background:#15374b;color:var(--green2)}
.embedded.dark-theme button,.embedded.dark-theme .btn{color:#082f49}
.embedded.dark-theme .secondary{color:var(--muted)}
.embedded.dark-theme .danger,.embedded.dark-theme .flash.error{background:#3c1f2c;color:#fda4af;border-color:#713042}
.embedded.dark-theme .flash{background:#12364b;color:#7dd3fc;border-color:#155e75}
.embedded.dark-theme .warning{background:#332b19;color:#fcd34d;border-color:#a16207}
.embedded.dark-theme .dangerbox{border-color:#713042}
@media(max-width:780px){.embedded .wrap,.embedded .main{padding:16px}.embedded .card{padding:16px}.embedded .gm-tabs{margin-bottom:16px}.embedded th,.embedded td{padding:12px}}
"""

BASE_CSS += r"""

/* PCL-style account groups keep names and metadata readable in narrow workbenches. */
.account-list-card{min-width:0}.account-list-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}
.account-list-heading h2{margin:0}.embedded .account-list-heading h2{margin:0}.account-count{font-size:12px;color:var(--muted);white-space:nowrap}
.account-search{display:flex;align-items:center;gap:10px;margin-bottom:12px}.account-search-field{position:relative;flex:1;min-width:0}
.account-search-field svg{position:absolute;left:14px;top:50%;transform:translateY(-50%);width:18px;height:18px;color:var(--muted);pointer-events:none}
.account-search-field input,.embedded .account-search-field input{padding-left:42px}.account-search .btn,.account-search button{flex:none;white-space:nowrap}
.account-filter-row{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:18px}
.account-filter-row .sub{margin:0}.robot-switch,.embedded .robot-switch{display:inline-flex;align-items:center;gap:10px;padding:5px 0;border:0;background:transparent;color:var(--text);font-size:13px;font-weight:400;line-height:1.5}
.robot-switch:hover,.embedded .robot-switch:hover{filter:none}.robot-switch-track{display:inline-flex;align-items:center;width:34px;height:20px;padding:3px;background:var(--line);border-radius:20px;flex:none;transition:background .15s}
.robot-switch-track:after{content:'';width:14px;height:14px;background:var(--panel);border-radius:50%;box-shadow:0 1px 3px #0002;transition:transform .15s}
.robot-switch[aria-checked="true"] .robot-switch-track{background:var(--green)}.robot-switch[aria-checked="true"] .robot-switch-track:after{transform:translateX(14px)}
.robot-switch:focus-visible,.account-row a:focus-visible,.account-group summary:focus-visible{outline:3px solid var(--green);outline-offset:4px;border-radius:6px}
.account-group{border:1px solid var(--line);border-radius:12px;background:var(--panel);overflow:hidden;margin-top:12px}
.account-group summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:15px 18px;background:var(--panel2);font-size:14px;font-weight:600;cursor:pointer;list-style:none}
.account-group summary::-webkit-details-marker{display:none}.account-group summary:after{content:'';width:7px;height:7px;border-right:2px solid var(--muted);border-bottom:2px solid var(--muted);transform:rotate(45deg);margin-right:2px}
.account-group[open] summary:after{transform:rotate(225deg)}.account-group-count{font-size:12px;font-weight:400;color:var(--muted);margin-left:8px}
.account-row{display:flex;align-items:center;gap:14px;padding:16px 18px;transition:background .15s}.account-row+.account-row{border-top:1px solid var(--line)}
.account-row:hover{background:var(--panel2)}.account-avatar{display:flex;align-items:center;justify-content:center;flex:0 0 42px;width:42px;height:42px;border-radius:11px;background:var(--panel2);color:var(--green2);font-size:19px;font-weight:600}
.account-avatar svg{width:23px;height:23px}.account-body{flex:1;min-width:0}.account-title{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;line-height:1.5}
.account-name{font-size:15px;font-weight:600;color:var(--text);overflow-wrap:anywhere}.account-uin{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.account-tags{display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-top:6px}.account-tag{padding:1px 7px;border-radius:4px;background:var(--panel2);color:var(--muted);font-size:12px;line-height:1.7;white-space:nowrap}
.account-login{font-size:12px;color:var(--muted);margin-top:6px;line-height:1.5}.account-open,.embedded .account-open{flex:none;white-space:nowrap;padding:7px 11px;font-size:12px}
.account-empty{border:1px dashed var(--line);border-radius:12px;padding:32px 18px;text-align:center;color:var(--muted);margin-top:12px}.account-empty p{margin:6px 0 0;font-size:12px}
.account-list-card .pager{flex-wrap:wrap}.account-pager-links{display:flex;gap:8px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.embedded.dark-theme .robot-switch{color:var(--text)}
@media(max-width:540px){.account-search{flex-wrap:wrap}.account-search-field{flex-basis:100%}.account-row{gap:10px;padding:14px 12px}.account-avatar{width:36px;height:36px;flex-basis:36px}.account-title{gap:3px 8px}.account-open,.embedded .account-open{padding:6px 8px}.account-group summary{padding:13px 12px}.account-filter-row{gap:8px}.account-count{font-size:11px}}

"""


def _page(title, content, flash_message="", flash_kind="ok"):
    flash = ""
    if flash_message:
        flash = '<div class="flash%s">%s</div>' % (
            " error" if flash_kind == "error" else "", esc(flash_message))
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s · PvZ OL Admin Panel</title>
<style>%s</style></head><body><header><div class="wrap head"><a class="brand" href="/">🌻 PvZ OL Admin Panel</a>
<span class="tag">Local Only</span><nav><a href="/">Accounts</a><a class="optional" href="http://127.0.0.1:8080/" target="_blank" rel="noreferrer">Game Website</a></nav></div></header>
<main class="wrap main">%s%s</main><footer><div class="wrap">Local admin tool · changes write directly to players.db · important operations are backed up first</div></footer></body></html>""" % (
        esc(title), BASE_CSS, flash, content)


def _csrf_input():
    return '<input type="hidden" name="csrf" value="%s">' % esc(CSRF_TOKEN)


def _redirect_url(path, message="", kind="ok"):
    if not message:
        return path
    sep = "&" if "?" in path else "?"
    return path + sep + urlencode({"msg": message, "kind": kind})


def _catalog_url(uin, values=None):
    values = values or {}

    def first(*keys):
        for key in keys:
            raw = values.get(key, [""])
            if isinstance(raw, list):
                raw = raw[0] if raw else ""
            if raw not in (None, ""):
                return str(raw)
        return ""

    params = {"uin": str(int(uin))}
    query = first("catalog_q", "q").strip()[:60]
    item_kind = first("catalog_kind", "item_kind").strip()
    page = first("catalog_page", "page").strip()
    if query:
        params["q"] = query
    if item_kind in ITEM_KIND_LABELS:
        params["item_kind"] = item_kind
    if page.isdigit() and int(page) > 1:
        params["page"] = str(int(page))
    return "/items?" + urlencode(params)


class AdminHandler(http.server.BaseHTTPRequestHandler):
    server_version = "PvZLocalAdmin/1.0"

    def _is_embedded(self):
        return bool(_launcher_origin()) and parse_qs(urlsplit(self.path).query).get("embedded", [""])[0] == "1"

    def _embedded_destination(self, path):
        query = parse_qs(urlsplit(self.path).query)
        return _embedded_url(path, theme=query.get("theme", [""])[0],
                             section=query.get("section", [""])[0])

    def _security_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        embedded = self._is_embedded()
        if not embedded:
            self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors " +
            (_launcher_origin() if embedded else "'none'"),
        )

    def _send(self, status, body, content_type="text/html; charset=utf-8"):
        if isinstance(body, str) and content_type.startswith("text/html"):
            query = parse_qs(urlsplit(self.path).query)
            classes = []
            if self._is_embedded():
                classes.append('embedded')
            if query.get('theme', [''])[0] == 'dark':
                classes.append('dark-theme')
            body = body.replace('<body>', '<body class="%s">' % ' '.join(classes), 1)
            if urlsplit(self.path).path == '/player':
                tabs = [('attributes', 'Basic Attributes'), ('inventory', 'Inventory Items'), ('plants', 'Tower Defense Plants'),
                        ('cards', 'Plant Cards'), ('progress', 'Level Progress'), ('vip', 'Diamond Membership'), ('friends', 'Friend Management'), ('reset', 'Reset Account')]
                selected = query.get('section', ['attributes'])[0]
                if selected not in dict(tabs):
                    selected = 'attributes'
                labels = {'Basic Attributes': 'attributes', 'Inventory': 'inventory', 'Tower Defense Plants': 'plants', 'Plant Cards': 'cards',
                          'Fast-Forward Progress': 'progress', 'Membership Card': 'vip', 'Friends & Friend Cards': 'friends', 'Reset This Account': 'reset'}
                def panel(match):
                    tag, attrs, title = match.groups()
                    section = labels.get(title)
                    if not section:
                        return match.group(0)
                    css_class = ' gm-panel' + (' gm-hidden' if section != selected else '')
                    attrs = re.sub(r'class="([^"]*)"', lambda css: 'class="%s%s"' % (css[1], css_class), attrs)
                    return '<%s%s data-section="%s"><h2>%s</h2>' % (tag, attrs, section, title)
                body = re.sub(r'<(section|aside)([^>]*class="card[^>]+)><h2>([^<]+)</h2>', panel, body)
                uin = query.get('uin', [''])[0]
                toolbar = '<div class="gm-tabs">' + ''.join('<a class="%s" href="%s">%s</a>' % (
                    'active' if key == selected else '', esc('/player?' + urlencode({'uin': uin, 'section': key, 'theme': query.get('theme', [''])[0]})), label) for key, label in tabs) + '</div>'
                body = body.replace('<div class="grid">', toolbar + '<div class="grid">', 1)
            if self._is_embedded():
                body = re.sub(r'(href|action)="(/[^\"]*)"',
                              lambda match: '%s="%s"' % (match[1], esc(self._embedded_destination(html.unescape(match[2])))), body)
                def embedded_form(match):
                    tag = match[1]
                    action = re.search(r'\baction="([^\"]*)"', tag)
                    context = parse_qs(urlsplit(html.unescape(action[1])).query) if action else query
                    fields = [('embedded', '1')]
                    theme = context.get('theme', [''])[0]
                    section = context.get('section', [''])[0]
                    if theme in ('light', 'dark'):
                        fields.append(('theme', theme))
                    if section in EMBED_SECTIONS:
                        fields.append(('section', section))
                    # Browsers replace an action's query when submitting GET
                    # forms, so the display context must also be successful controls.
                    return tag + ''.join('<input type="hidden" name="%s" value="%s">' %
                                         (name, esc(value)) for name, value in fields)
                body = re.sub(r'(<form\b[^>]*>)', embedded_form, body)
            if guard_blocks_writes() or _port_open(9001):
                # Rendering matches the server-side write gate. Forms remain
                # readable while the game is active, but cannot be submitted.
                body = re.sub(r'(<form\b[^>]*method="post"[^>]*>)(.*?)</form>',
                              r'\1<fieldset disabled style="border:0;padding:0;margin:0">\2</fieldset></form>', body, flags=re.S)
                body = body.replace('<main class="wrap main">', '<main class="wrap main"><div class="warning" style="margin-bottom:18px">The game is running, so the admin panel is read-only. Stop the game and refresh to edit.</div>', 1)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _redirect(self, path, message="", kind="ok"):
        self.send_response(303)
        destination = _redirect_url(path, message, kind)
        self.send_header("Location", self._embedded_destination(destination) if self._is_embedded() else destination)
        self._security_headers()
        self.end_headers()

    def _request_allowed(self):
        host = self.headers.get("Host", "")
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        if (hostname or "").lower() not in ("127.0.0.1", "localhost", "::1"):
            self._send(421, "Invalid Host", "text/plain; charset=utf-8")
            return False
        return True

    def _post_form(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValidationError("Invalid request length.")
        if length <= 0 or length > MAX_POST_BYTES:
            raise ValidationError("Request is too large or empty.")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            raise ValidationError("Only standard form submissions are supported.")
        # Safari、WebView 等 macOS 内嵌浏览器可能把本地页面的 Origin 发送为
        # ``null`` 或Apply自定义来源。Origin 只能作为浏览器提示，不能作为这里
        # 的安全边界；真正的写入保护由仅监听回环地址、Host 白名单和下面的
        # 随机 CSRF 令牌共同提供。
        raw = self.rfile.read(length).decode("utf-8", "strict")
        form = parse_qs(raw, keep_blank_values=True, max_num_fields=80)
        token = (form.get("csrf", [""])[0] or "")
        if not secrets.compare_digest(token, CSRF_TOKEN):
            raise ValidationError("This page has expired. Refresh and try again.")
        return form

    def _message(self, query):
        return (query.get("msg", [""])[0], query.get("kind", ["ok"])[0])

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._request_allowed():
            return
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/healthz":
                self._send(200, "ok\n", "text/plain; charset=utf-8")
            elif parsed.path == "/":
                self._dashboard(query)
            elif parsed.path == "/player":
                uin = _integer(query, "uin", 1, 9_223_372_036_854_775_807, "UIN")
                self._player_page(uin, query)
            elif parsed.path == "/items":
                uin = _integer(query, "uin", 1, 9_223_372_036_854_775_807, "UIN")
                self._items_page(uin, query)
            else:
                self._send(404, _page("Not Found", '<div class="card"><h1>Page not found</h1><a href="/">Return Home</a></div>'))
        except ValidationError as exc:
            self._send(400, _page("Invalid Input", '<div class="card"><h1>Invalid Input</h1><p>%s</p><a href="/">Return Home</a></div>' % esc(exc)))
        except Exception:
            traceback.print_exc()
            self._send(500, _page("Admin Error", '<div class="card"><h1>Admin Error</h1><p>Check the launcher logs.</p></div>'))
        finally:
            store.close_thread_db()

    def do_POST(self):
        if not self._request_allowed():
            return
        parsed = urlsplit(self.path)
        form = {}
        try:
            form = self._post_form()
            handlers = {
                "/player/create": self._create_player,
                "/player/new": self._new_player,
                "/player/update": self._update_player,
                "/player/item": self._edit_item,
                "/player/repair-puzzles": self._repair_puzzles,
                "/player/grant-item": self._grant_item,
                "/player/plant": self._edit_plant,
                "/player/progress": self._edit_progress,
                "/player/cards": self._edit_cards,
                "/player/diamond": self._edit_diamond,
                "/player/friend": self._edit_friend,
                "/player/reset": self._reset_account,
                "/backup": self._manual_backup,
            }
            handler = handlers.get(parsed.path)
            if not handler:
                self._send(404, "Not Found", "text/plain; charset=utf-8")
                return
            with WRITE_LOCK:
                if parsed.path not in ("/player/create", "/backup"):
                    _require_game_stopped()
                # Per-account transactions keep multi-instance grants and their
                # balances atomic. Reset owns its separate backup transaction.
                if parsed.path not in ("/player/create", "/player/new", "/player/reset", "/player/repair-puzzles", "/backup"):
                    player = _require_player(_integer(form, "uin", 1, None, "UIN"))
                    with store.atomic_player(player):
                        handler(form)
                else:
                    handler(form)
        except ValidationError as exc:
            uin = ""
            try:
                uin = str(int((form.get("uin", [""])[0] or "")))
            except Exception:
                pass
            if parsed.path in ("/player/create", "/player/new"):
                target = "/"
            elif parsed.path == "/player/grant-item" and uin:
                target = _catalog_url(int(uin), form)
            else:
                target = "/player?uin=" + uin if uin else "/"
            self._redirect(target, str(exc), "error")
        except Exception:
            traceback.print_exc()
            self._send(500, _page("Admin Error", '<div class="card"><h1>Operation Failed</h1><p>Incomplete data was not written. Check the launcher logs.</p></div>'))
        finally:
            store.close_thread_db()

    def _dashboard(self, query):
        search = (query.get("q", [""])[0] or "").strip()[:100]
        include_robots = query.get("include_robots", [""])[0] == "1"
        theme = query.get("theme", [""])[0]
        try:
            page_number = max(1, int(query.get("page", ["1"])[0]))
        except ValueError:
            page_number = 1
        per_page = 30

        def list_url(*, page=1, clear_search=False):
            params = {}
            if search and not clear_search:
                params["q"] = search
            if include_robots:
                params["include_robots"] = "1"
            if page > 1:
                params["page"] = page
            if theme in ("light", "dark"):
                params["theme"] = theme
            return "/" + ("?" + urlencode(params) if params else "")

        with _connection() as con:
            # Arena, ranked arena, friends and guilds all share bots_local's
            # entire reserved NPC range, including legacy/unseeded identities.
            robot_total = con.execute("SELECT COUNT(*) FROM players WHERE uin >= 900000 AND uin < 1000000").fetchone()[0]
            total = con.execute("SELECT COUNT(*) FROM players WHERE uin < 900000 OR uin >= 1000000").fetchone()[0]
            cards = con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
            items = con.execute("SELECT COALESCE(SUM(count),0) FROM items").fetchone()[0]
            sql = ("SELECT uin,name,level,coin,sun,ticket,present_ticket,vip_level,td_stage,td_level,td_sub,last_login "
                   "FROM players")
            clauses, args = [], []
            if not include_robots:
                clauses.append("(uin < 900000 OR uin >= 1000000)")
            if search:
                clauses.append(r"(CAST(uin AS TEXT) LIKE ? ESCAPE '\' OR name LIKE ? ESCAPE '\')")
                term = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                args.extend([term, term])
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            matching = con.execute("SELECT COUNT(*) FROM (" + sql + ")", args).fetchone()[0]
            matching_robots = con.execute("SELECT COUNT(*) FROM (" + sql + ") WHERE uin >= 900000 AND uin < 1000000", args).fetchone()[0]
            total_pages = max(1, (matching + per_page - 1) // per_page)
            page_number = min(page_number, total_pages)
            sql += " ORDER BY CASE WHEN uin >= 900000 AND uin < 1000000 THEN 1 ELSE 0 END, last_login DESC, uin DESC LIMIT ? OFFSET ?"
            players = [dict(row) for row in con.execute(sql, args + [per_page, (page_number - 1) * per_page])]

        robot_icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 3v3m-2-3h4M5 9h14v11H5zM2 12v5m20-5v5M8 16h8"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/></svg>'

        def account_row(player):
            is_robot = 900000 <= int(player["uin"]) < 1000000
            name = str(player["name"] or "Unnamed Character")
            destination = "/player?" + urlencode(dict(uin=player["uin"], **({"theme": theme} if theme in ("light", "dark") else {})))
            tags = ("Lv.%s" % player["level"], "VIP%s" % player["vip_level"],
                    "Coins %s" % player["coin"], "Blue Diamonds %s" % player["present_ticket"],
                    "Gold Diamonds %s" % player["ticket"], "塔防 %s-%s-%s" % (player["td_stage"], player["td_level"], player["td_sub"]))
            return ('<article class="account-row" data-account-uin="%s"><span class="account-avatar" aria-hidden="true">%s</span>'
                    '<div class="account-body"><div class="account-title"><a class="account-name" href="%s">%s</a>'
                    '<span class="account-uin">UIN %s</span></div><div class="account-tags">%s</div>'
                    '<div class="account-login">Last login %s</div></div>'
                    '<a class="btn secondary account-open" href="%s" aria-label="Manage account %s">Manage →</a></article>') % (
                        player["uin"], robot_icon if is_robot else esc(name[0]), esc(destination), esc(name), player["uin"],
                        "".join('<span class="account-tag">%s</span>' % esc(tag) for tag in tags),
                        esc(_format_time(player["last_login"])), esc(destination), esc(name))

        groups = []
        for robots, label, group_total in ((False, "Player Accounts", matching - matching_robots), (True, "Bot Accounts", matching_robots)):
            group_players = [p for p in players if (900000 <= int(p["uin"]) < 1000000) == robots]
            if group_players:
                groups.append('<details class="account-group" open><summary><span>%s<span class="account-group-count">%s</span></span></summary>%s</details>' % (
                    label, group_total, "".join(account_row(p) for p in group_players)))
        if not groups:
            groups.append('<div class="account-empty">No matching accounts<p>Try searching again with a different character name or UIN%s.</p></div>' % (
                " or enable bot accounts" if not include_robots and robot_total else ""))

        # These are successful GET controls: theme and robot visibility survive
        # search submissions even when the browser replaces the action query.
        search_fields = ('<input type="hidden" name="include_robots" value="1">' if include_robots else "")
        switch_fields = '<input type="hidden" name="q" value="%s">' % esc(search)
        if theme in ("light", "dark"):
            theme_field = '<input type="hidden" name="theme" value="%s">' % theme
            search_fields += theme_field
            switch_fields += theme_field
        visibility_hint = "Showing %s bot accounts" % robot_total if include_robots else "%s bot accounts hidden" % robot_total
        pager = '<div class="pager"><span>%s accounts · page %s of %s</span><div class="account-pager-links">' % (matching, page_number, total_pages)
        if page_number > 1:
            pager += '<a class="btn secondary smallbtn" href="%s">Previous</a>' % esc(list_url(page=page_number - 1))
        if page_number < total_pages:
            pager += '<a class="btn secondary smallbtn" href="%s">Next</a>' % esc(list_url(page=page_number + 1))
        pager += '</div></div>'
        web_on, game_on = _port_open(8080), _port_open(9001)
        message, kind = self._message(query)
        content = """
<h1>Account Management</h1>
<div class="grid"><section class="card span12"><div class="metrics">
<div class="metric"><b>%s</b><span>Player Accounts</span></div><div class="metric"><b>%s</b><span>Plant Cards</span></div>
<div class="metric"><b>%s</b><span>Total Inventory Items</span></div><div class="metric"><b><span class="status"><i class="dot%s"></i>Web :8080</span> <span class="status"><i class="dot%s"></i>Game :9001</span></b><span>Service Status</span></div>
</div></section>
<section class="card span8 account-list-card"><div class="account-list-heading"><h2>Player List</h2><span class="account-count">%s player accounts</span></div>
<form method="get" action="/" class="account-search">%s<div class="account-search-field"><label class="sr-only" for="account-search">Search UIN or character name</label><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/></svg><input id="account-search" name="q" value="%s" placeholder="Search character name or UIN"></div><button type="submit">Search</button><a class="btn secondary" href="%s">Clear</a></form>
<div class="account-filter-row"><p class="sub">%s</p><form method="get" action="/">%s<button type="submit" class="robot-switch" role="switch" aria-checked="%s" name="include_robots" value="%s"><span>Show bot accounts</span><span class="robot-switch-track" aria-hidden="true"></span></button></form></div>
%s%s</section>
<aside class="card span4"><h2>Open Existing Account</h2><p class="sub">Enter the account ID used in the launcher to open an existing save.</p>
<form method="post" action="/player/create">%s<div class="field"><label>Account ID (UIN)</label><input class="mono" name="uin" inputmode="numeric" pattern="[0-9]+" maxlength="19" required placeholder="700000200"></div><button class="secondary" style="margin-top:10px;width:100%%" type="submit">Open Account</button></form>
<h3>Create Account</h3><p class="sub">Enter an unused numeric ID to create a separate new-player save. Duplicate IDs will be rejected, and existing saves are preserved.</p>
<form method="post" action="/player/new">%s<div class="field"><label>New Account ID (UIN)</label><input class="mono" name="uin" inputmode="numeric" pattern="[0-9]+" maxlength="19" required placeholder="e.g. 700000205"></div><button style="margin-top:10px;width:100%%" type="submit">Create Account</button></form>
<h3>Database</h3><p class="sub mono">%s</p><form method="post" action="/backup">%s<button class="secondary" style="width:100%%" type="submit">Back Up players.db Now</button></form></aside></div>""" % (
            total, cards, items, " on" if web_on else "", " on" if game_on else "", total,
            search_fields, esc(search), esc(list_url(clear_search=True)), esc(visibility_hint), switch_fields,
            "true" if include_robots else "false", "0" if include_robots else "1", "".join(groups), pager,
            _csrf_input(), _csrf_input(), esc(os.path.basename(store.DB_PATH)), _csrf_input())
        self._send(200, _page("Account Management", content, message, kind))

    def _items_page(self, uin, query):
        player = _require_player(uin)
        search = (query.get("q", [""])[0] or "").strip()[:60]
        item_kind = (query.get("item_kind", [""])[0] or "").strip()
        if item_kind not in ITEM_KIND_LABELS:
            item_kind = ""
        try:
            page_number = max(1, int((query.get("page", ["1"])[0] or "1")))
        except ValueError:
            page_number = 1

        needle = search.casefold()
        matches = [item for item in ITEM_CATALOG
                   if (not item_kind or item["kind"] == item_kind)
                   and (not needle or needle in item["search"])]
        per_page = 50
        total_pages = max(1, (len(matches) + per_page - 1) // per_page)
        page_number = min(page_number, total_pages)
        visible = matches[(page_number - 1) * per_page:page_number * per_page]

        stack_counts = dict(store.get_items(uin))
        equipment_counts = {}
        for equipment in store.get_equipments(uin):
            iid = int(equipment["item_id"])
            equipment_counts[iid] = equipment_counts.get(iid, 0) + 1
        card_counts = {}
        for _uid, card_id, _level, _exp in store.get_cards(uin):
            card_counts[int(card_id)] = card_counts.get(int(card_id), 0) + 1
        reagent_counts = {}
        for reagent in store.get_reagents(uin):
            base_id = int(reagent["base_id"])
            reagent_counts[base_id] = reagent_counts.get(base_id, 0) + 1

        def owned_count(item):
            if not item or not isinstance(item, dict):
                return 0
            if item.get("kind") == "currency":
                c_info = CURRENCY_GRANTS.get(item.get("id"))
                if not c_info:
                    return 0
                return int(player.get(c_info[0]) or 0)
            if item.get("kind") == "equipment":
                return equipment_counts.get(item.get("id"), 0)
            if item.get("kind") == "reagent":
                return reagent_counts.get(item.get("id"), 0)
            if item.get("kind") == "card":
                try:
                    card_spec = str(item.get("card_spec") or "")
                    card_id = int(card_spec.split(":", 1)[0])
                except (TypeError, ValueError, IndexError, AttributeError):
                    return 0
                return card_counts.get(card_id, 0)
            return sum(stack_counts.get(iid, 0) for iid in item.get("ids", ()))

        rows = []
        for item in visible:
            kind_label = ITEM_KIND_LABELS[item["kind"]]
            type_label = ITEM_TYPE_LABELS.get(item["type"], "Type %s" % item["type"])
            details = []
            if item["desc"]:
                details.append(item["desc"])
            if item["gain_way"]:
                details.append("Source: " + item["gain_way"])
            if item["kind"] == "card":
                details.append("Creates a real plant card instance instead of a fake inventory stack")
            elif item["kind"] in ("equipment", "reagent"):
                details.append("Each quantity creates a separate instance")
            limit = 2_000_000_000 if item["kind"] == "currency" else (
                50 if item["kind"] in ("card", "equipment", "reagent") else
                (item["stack_num"] or 2_000_000_000))
            id_html = "<b>%s</b>" % item["id"]
            owned_html = "<b>%s</b>" % owned_count(item)
            grant_id_input = '<input type="hidden" name="item_id" value="%s">' % item["id"]
            if len(item["ids"]) > 1:
                id_html += '<div class="sub">Matching IDs: %s</div>' % "、".join(
                    str(iid) for iid in item["ids"][1:])
                owned_html += '<div class="sub">%s</div>' % " · ".join(
                    "%s：%s" % (iid, stack_counts.get(iid, 0)) for iid in item["ids"])
                selected_id = int(search) if search in tuple(map(str, item["ids"])) else item["id"]
                id_options = "".join('<option value="%s"%s>%s</option>' % (
                    iid, " selected" if iid == selected_id else "", iid) for iid in item["ids"])
                grant_id_input = ('<div class="field small"><label>Item ID</label>'
                                  '<select name="item_id">%s</select></div>') % id_options
            rows.append(f"""
<tr><td class="mono nowrap">{id_html}</td><td><b>{esc(item['name'])}</b><div class="sub">{esc(' · '.join(details))}</div></td>
<td><span class="pill">{esc(kind_label)}</span><div class="sub">{esc(type_label)} · Quality {item['quality']} · Min. Lv.{item['min_level']}</div></td>
<td class="num">{owned_html}</td><td><form method="post" action="/player/grant-item" class="row">{_csrf_input()}
<input type="hidden" name="uin" value="{uin}">{grant_id_input}
<input type="hidden" name="catalog_q" value="{esc(search)}"><input type="hidden" name="catalog_kind" value="{esc(item_kind)}"><input type="hidden" name="catalog_page" value="{page_number}">
<div class="field small"><label>Quantity</label><input name="quantity" type="number" min="1" max="{limit}" value="1" required></div><button type="submit">Grant</button></form></td></tr>""")
        rows_html = "".join(rows) or '<tr><td colspan="5" class="empty">No safely grantable items found</td></tr>'

        options = ['<option value=""%s>All Grantable Items</option>' %
                   (" selected" if not item_kind else "")]
        options.extend('<option value="%s"%s>%s</option>' %
                       (key, " selected" if item_kind == key else "", esc(label))
                       for key, label in ITEM_KIND_LABELS.items())

        page_links = []
        for number in range(max(1, page_number - 2), min(total_pages, page_number + 2) + 1):
            url = "/items?" + urlencode({
                "uin": uin, "q": search, "item_kind": item_kind, "page": number})
            if number == page_number:
                page_links.append('<span class="pill"><b>%s</b></span>' % number)
            else:
                page_links.append('<a class="pill" href="%s">%s</a>' % (esc(url), number))
        previous = ""
        following = ""
        if page_number > 1:
            previous = '<a class="btn secondary smallbtn" href="%s">← Previous</a>' % esc(
                "/items?" + urlencode({"uin": uin, "q": search, "item_kind": item_kind,
                                        "page": page_number - 1}))
        if page_number < total_pages:
            following = '<a class="btn secondary smallbtn" href="%s">Next →</a>' % esc(
                "/items?" + urlencode({"uin": uin, "q": search, "item_kind": item_kind,
                                        "page": page_number + 1}))
        pager = '<div class="row" style="justify-content:center;margin-top:16px">%s%s%s</div>' % (
            previous, "".join(page_links), following)

        message, flash_kind = self._message(query)
        content = f"""
<p class="sub"><a href="/player?uin={uin}">← Return to {esc(player['name'])}</a></p>
<h1>Grant Specific Items <span class="sub mono">UIN {uin}</span></h1>
<div class="warning" style="margin-bottom:16px">This page reads real names and descriptions from the client ItemInfo. Regular items, currencies, plant cards, equipment, and reagents are written to their correct data models; puppet display entries and plant XP that cannot be safely modeled are excluded.</div>
<section class="card"><form method="get" action="/items" class="row"><input type="hidden" name="uin" value="{uin}">
<div class="field"><label>Search by item name, ID, description, or source</label><input name="q" value="{esc(search)}" placeholder="e.g. Tower Defense Key, 4001, XP Bean, reagent"></div>
<div class="field small"><label>Grant Type</label><select name="item_kind">{''.join(options)}</select></div><button type="submit">Search</button><a class="btn secondary" href="/items?uin={uin}">Clear</a></form>
<p class="sub" style="margin-bottom:0">{len(matches)} items · page {page_number}/{total_pages}. After granting items, re-enter the game or refresh the relevant inventory view.</p></section>
<section class="card" style="margin-top:16px"><div class="scroll"><table><thead><tr><th>ID</th><th>Item</th><th>Type</th><th>Owned</th><th>Quantity to Grant</th></tr></thead><tbody>{rows_html}</tbody></table></div>{pager}</section>"""
        self._send(200, _page("Grant Specific Items", content, message, flash_kind))

    def _player_page(self, uin, query):
        player = _require_player(uin)
        plants = store.get_player_plants(uin)
        bag = store.get_items(uin)
        cards = store.get_cards(uin)
        friends = store.get_friends(uin)
        with _connection() as con:
            td_count = con.execute("SELECT COUNT(*) FROM td_passed WHERE uin=?", (uin,)).fetchone()[0]
            td_stars = con.execute("SELECT COALESCE(SUM(star),0) FROM td_passed WHERE uin=?", (uin,)).fetchone()[0]

        edit_inputs = []
        for key, label, kind, minimum, maximum in EDIT_FIELDS:
            attrs = ""
            if kind == "number":
                attrs = ' min="%s" max="%s" step="1"' % (minimum, maximum)
            edit_inputs.append(
                '<div class="field"><label>%s</label><input name="%s" type="%s" value="%s"%s required></div>' %
                (esc(label), esc(key), kind, esc(player.get(key, "")), attrs))
        edit_inputs.append(
            '<div class="field"><label>VIP Level</label><select name="vip_level">%s</select></div>' %
            "".join('<option value="%s"%s>%s</option>' %
                    (v, " selected" if int(player.get("vip_level") or 0) == v else "", "Off" if v == 0 else "VIP%d" % v)
                    for v in range(4)))
        edit_inputs.append(
            '<div class="field"><label>Tower Defense Perk</label><select name="td_privilege"><option value="0"%s>Off</option><option value="1"%s>On</option></select></div>' %
            (" selected" if not int(player.get("td_privilege") or 0) else "",
             " selected" if int(player.get("td_privilege") or 0) else ""))

        bag_rows = "".join(
            '<tr><td class="mono">%s</td><td>%s</td><td class="num"><b>%s</b></td></tr>' %
            (iid, _inventory_item_html(iid), count)
            for iid, count in bag
        )
        bag_rows += "".join(
            '<tr><td class="mono">%s</td><td>%s（Equipment UID %s）</td><td class="num"><b>1</b></td></tr>' %
            (equipment["item_id"], esc(ITEM_NAMES.get(equipment["item_id"], "Unknown Equipment")),
             equipment["uid"])
            for equipment in store.get_equipments(uin)
        )
        bag_rows = bag_rows or '<tr><td colspan="3" class="empty">Inventory is empty</td></tr>'
        invalid_puzzles = sum(_invalid_puzzle_details(iid) is not None for iid, _ in bag)
        repair_note = (
            '<form method="post" action="/player/repair-puzzles" style="margin-top:14px">%s'
            '<input type="hidden" name="uin" value="%s">'
            '<p class="sub">Found %s invalid legacy puzzle IDs. A backup will be made first; only invalid entries will be removed.</p>'
            '<button class="secondary" type="submit">Back Up and Repair Invalid Puzzles</button></form>' %
            (_csrf_input(), uin, invalid_puzzles)) if invalid_puzzles else ""
        plant_pills = "".join(
            '<span class="pill"><span class="mono">%s</span> %s</span>' %
            (pid, esc(PLANT_NAMES.get(pid, "Unknown Plant"))) for pid in plants
        ) or '<span class="empty">No Tower Defense plants unlocked</span>'
        card_rows = "".join(
            '<tr><td class="mono">%s</td><td class="mono">%s</td><td>%s</td><td>Lv.%s</td><td>%s</td></tr>' %
            (uid, cid, esc(CARD_NAMES.get(cid, "Unknown Card")), level, exp)
            for uid, cid, level, exp in cards
        ) or '<tr><td colspan="5" class="empty">This account has no plant cards yet</td></tr>'
        friend_rows = "".join(
            '<tr><td class="mono">%s</td><td>%s</td><td>%s %s · Lv.%s</td>'
            '<td>%s / %s / %s</td><td><form method="post" action="/player/friend">%s'
            '<input type="hidden" name="uin" value="%s"><input type="hidden" name="operation" value="remove">'
            '<input type="hidden" name="friend_role_id" value="%s"><button class="danger smallbtn" type="submit">Remove</button></form></td></tr>' %
            (f["friend_role_id"], esc(f["friend_name"]), f["help_plant_id"],
             esc(PLANT_NAMES.get(f["help_plant_id"], "Unknown Plant")), f["help_plant_level"],
             f["diamond_blue_lvl"], f["diamond_yellow_lvl"], f["diamond_xinyue_lvl"],
             _csrf_input(), uin, f["friend_role_id"])
            for f in friends
        ) or '<tr><td colspan="5" class="empty">No friends yet</td></tr>'
        message, kind = self._message(query)
        content = """
<p class="sub"><a href="/">← Back to Account List</a></p><h1>%s <span class="sub mono">UIN %s</span></h1>
<div class="grid"><section class="card span12"><div class="metrics">
<div class="metric"><b>Lv.%s</b><span>Account Level</span></div><div class="metric"><b>%s</b><span>Coins</span></div>
<div class="metric"><b>%s / %s</b><span>Blue / Gold Diamonds</span></div><div class="metric"><b>%s stages · %s stars</b><span>Tower Defense Progress</span></div></div></section>
<section class="card span7"><h2>Basic Attributes</h2><form method="post" action="/player/update">%s<input type="hidden" name="uin" value="%s"><div class="row">%s</div><button style="margin-top:14px" type="submit">Save Basic Attributes</button></form><p class="sub">Account level caps plant card levels and determines which town buildings and later worlds are unlocked. Changes take effect after re-entering the game.</p></section>
<aside class="card span5"><h2>Fast-Forward Progress</h2><p class="sub">Completes earlier stages, stars, gates, and reward plants according to the client level table.</p>
<form method="post" action="/player/progress">%s<input type="hidden" name="uin" value="%s"><div class="row"><div class="field small"><label>World</label><input name="stage" type="number" min="1" value="%s" required></div><div class="field small"><label>Stage</label><input name="level" type="number" min="1" value="%s" required></div><div class="field small"><label>Substage (leave blank for full stage)</label><input name="sub" type="number" min="1"></div></div><button style="margin-top:10px" type="submit">Clear Through Here</button></form>
<h3>Current Progress</h3><p><b>%s-%s-%s</b> · %s stages · %s stars</p></aside>
<section class="card span6"><h2>Inventory</h2><p><a class="btn" href="/items?uin=%s">🎁 Open Item Catalog</a></p><p class="sub">The item catalog supports searching by name or ID and distinguishes regular items, plant cards, equipment, and reagents. The control below only adjusts stackable items.</p><p class="sub">Legacy inventory names are checked against plant data; unknown IDs retain their original quantities. Manage plant unlocks and plant card instances in their respective sections.</p><form method="post" action="/player/item" class="row">%s<input type="hidden" name="uin" value="%s"><div class="field"><label>Item ID</label><input name="item_id" type="number" min="1" required></div><div class="field"><label>Quantity Change (negative allowed)</label><input name="delta" type="number" value="1" required></div><button type="submit">Apply</button></form>%s<div class="scroll" style="margin-top:14px;max-height:330px"><table><thead><tr><th>ID</th><th>Name</th><th>Quantity</th></tr></thead><tbody>%s</tbody></table></div></section>
<section class="card span6"><h2>Tower Defense Plants</h2><form method="post" action="/player/plant" class="row">%s<input type="hidden" name="uin" value="%s"><div class="field"><label>Plant ID</label><input name="plant_id" type="number" min="1" list="plant-ids" required><datalist id="plant-ids">%s</datalist></div><div class="field small"><label>Action</label><select name="operation"><option value="add">Unlock</option><option value="remove">Remove</option></select></div><button type="submit">Apply</button></form><div class="pills" style="margin-top:16px">%s</div></section>
<section class="card span8"><h2>Plant Cards</h2><form method="post" action="/player/cards" class="row">%s<input type="hidden" name="uin" value="%s"><div class="field"><label>Card UID (leave blank for all)</label><input name="uid" type="number" min="1"></div><div class="field small"><label>Target Level</label><input name="level" type="number" min="1" max="999" required></div><button type="submit">Set Level</button></form><div class="scroll" style="margin-top:14px;max-height:330px"><table><thead><tr><th>UID</th><th>Card ID</th><th>Name</th><th>Level</th><th>XP</th></tr></thead><tbody>%s</tbody></table></div></section>
<aside class="card span4 dangerbox"><h2>Reset This Account</h2><div class="warning">Stop the game before resetting. The admin panel backs up the entire database, removes this UIN's records from all gameplay tables, and creates a new-player save.</div>
<form method="post" action="/player/reset" style="margin-top:13px">%s<input type="hidden" name="uin" value="%s"><div class="field"><label>Enter %s to confirm</label><input name="confirm_uin" class="mono" autocomplete="off" required></div><label class="sub" style="display:block;margin:10px 0"><input name="ack" type="checkbox" value="yes" style="width:auto" required> I understand this account will be reset to its initial state</label><button class="danger" type="submit">Back Up and Reset</button></form></aside></div>""" % (
            esc(player["name"]), uin, player["level"], player["coin"], player["present_ticket"],
            player["ticket"], td_count, td_stars,
            _csrf_input() + _snapshot_input(player, [field[0] for field in EDIT_FIELDS] +
                ["vip_level", "vip_card_id", "vip_endtime", "td_privilege", "td_privilege_endtime"]), uin,
            "".join(edit_inputs), _csrf_input(), uin, player["td_stage"], player["td_level"],
            player["td_stage"], player["td_level"], player["td_sub"], td_count, td_stars,
            uin, _csrf_input(), uin, repair_note, bag_rows, _csrf_input(), uin,
            "".join('<option value="%s">%s %s</option>' % (pid, pid, esc(name))
                    for pid, name in sorted(PLANT_NAMES.items())), plant_pills,
            _csrf_input(), uin, card_rows, _csrf_input(), uin, uin)
        plant_options = "".join(
            '<option value="%s">%s %s</option>' % (pid, pid, esc(name))
            for pid, name in sorted(PLANT_NAMES.items()))
        content += """
<div class="grid" style="margin-top:16px"><section class="card span5"><h2>Membership Card</h2><p class="sub">Membership information is synchronized to the client at login. 0 means that membership type is inactive.</p>
<form method="post" action="/player/diamond">%s<input type="hidden" name="uin" value="%s"><div class="row"><div class="field small"><label>Show Card</label><select name="flag"><option value="0"%s>Off</option><option value="1"%s>On</option></select></div><div class="field small"><label>Blue Diamond Level</label><input name="blue" type="number" min="0" max="99" value="%s" required></div><div class="field small"><label>Gold Diamond Level</label><input name="yellow" type="number" min="0" max="99" value="%s" required></div><div class="field small"><label>Heartfelt Membership Level</label><input name="xinyue" type="number" min="0" max="99" value="%s" required></div></div><button style="margin-top:10px" type="submit">Save Membership</button></form></section>
<section class="card span7"><h2>Friends & Friend Cards</h2><form method="post" action="/player/friend">%s<input type="hidden" name="uin" value="%s"><input type="hidden" name="operation" value="add"><div class="row"><div class="field"><label>Friend Character ID</label><input name="friend_role_id" type="number" min="1" required></div><div class="field"><label>Friend Name</label><input name="friend_name" maxlength="24" required></div><div class="field"><label>Friend Card Plant ID</label><input name="plant_id" type="number" min="1" list="friend-plant-ids" required><datalist id="friend-plant-ids">%s</datalist></div><div class="field small"><label>Plant Level</label><input name="plant_level" type="number" min="1" max="999" value="1" required></div></div><div class="row" style="margin-top:8px"><div class="field small"><label>Show Membership</label><select name="diamond_flag"><option value="1">On</option><option value="0">Off</option></select></div><div class="field small"><label>Blue Diamonds</label><input name="blue" type="number" min="0" max="99" value="0" required></div><div class="field small"><label>Gold Diamonds</label><input name="yellow" type="number" min="0" max="99" value="0" required></div><div class="field small"><label>Heartfelt Membership</label><input name="xinyue" type="number" min="0" max="99" value="0" required></div><button type="submit">Add / Update</button></div></form>
<div class="scroll" style="margin-top:14px;max-height:300px"><table><thead><tr><th>Character ID</th><th>Name</th><th>Friend Card</th><th>Blue / Gold / Heartfelt</th><th></th></tr></thead><tbody>%s</tbody></table></div></section></div>""" % (
            _csrf_input() + _snapshot_input(player, ["diamond_flag", "diamond_blue_lvl", "diamond_yellow_lvl", "diamond_xinyue_lvl"]), uin,
            " selected" if not int(player.get("diamond_flag") or 0) else "",
            " selected" if int(player.get("diamond_flag") or 0) else "",
            int(player.get("diamond_blue_lvl") or 0),
            int(player.get("diamond_yellow_lvl") or 0),
            int(player.get("diamond_xinyue_lvl") or 0),
            _csrf_input(), uin, plant_options, friend_rows)
        self._send(200, _page("Player %s" % uin, content, message, kind))

    def _create_player(self, form):
        uin = _integer(form, "uin", 1, 9_223_372_036_854_775_807, "UIN")
        _require_player(uin)
        self._redirect("/player?uin=%s" % uin, "Opened existing save.")

    def _new_player(self, form):
        uin = _integer(form, "uin", 1, 9_223_372_036_854_775_807, "UIN")
        try:
            store.create_new_player(uin)
        except ValueError as exc:
            raise ValidationError(str(exc))
        self._redirect("/player?uin=%s" % uin,
                       "Created new account %s with a separate new-player save. Enter this ID in the launcher to play." % uin)

    def _update_player(self, form):
        uin = _integer(form, "uin", 1, 9_223_372_036_854_775_807, "UIN")
        _require_player(uin)
        baseline = _baseline(form, uin)
        values = {}
        name = (form.get("name", [""])[0] or "").strip()
        if not name or len(name) > 24 or any(ord(ch) < 32 for ch in name):
            raise ValidationError("Character name must be 1–24 visible characters.")
        if name != baseline.get('name'):
            from profile_editor import _text, NAME_MIN, NAME_MAX
            try:
                _text(name, 'Character Name', NAME_MAX, NAME_MIN)
            except ValueError as exc:
                raise ValidationError(str(exc))
        values["name"] = name
        for key, label, kind, minimum, maximum in EDIT_FIELDS:
            if kind == "number":
                values[key] = _integer(form, key, minimum, maximum, label)
        try:
            gm.validate_player_fields(values)
        except ValueError as exc:
            raise ValidationError(str(exc))
        vip = _integer(form, "vip_level", 0, 3, "VIP Level")
        privilege = _integer(form, "td_privilege", 0, 1, "Tower Defense Perk")
        values.update(vip_level=vip, td_privilege=privilege)
        count = _save_incremental(uin, values, baseline)
        self._redirect("/player?uin=%s" % uin,
                       "Basic attributes saved. Changes take effect after re-entering the game." if count else "No attributes changed; the save is unchanged.")

    def _edit_item(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        item_id = _integer(form, "item_id", 1, 99_999_999, "Item ID")
        delta = _integer(form, "delta", -2_000_000_000, 2_000_000_000, "Quantity Change")
        current = store.get_item_count(uin, item_id)
        try:
            gm.validate_stack_delta(item_id, delta, current)
        except ValueError as exc:
            raise ValidationError(str(exc))
        new_count = store.add_item(uin, item_id, delta)
        if item_id == 43:  # Prestige is also cached in the player record to keep both values synchronized.
            store.save_player(uin, prestige=new_count)
        name, _detail = _inventory_item_details(item_id)
        self._redirect("/player?uin=%s" % uin,
                       "%s (ID %s) current quantity: %s." % (name, item_id, new_count))

    def _repair_puzzles(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        result = _repair_invalid_puzzles(uin)
        message = ("Repaired %s invalid legacy puzzle IDs; valid fragment quantities were preserved. Backup: %s." %
                   (result["count"], os.path.basename(result["backup"]))) if result["count"] else "No invalid legacy puzzle IDs to repair."
        self._redirect("/player?uin=%s" % uin, message)

    def _grant_item(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        player = _require_player(uin)
        item_id = _integer(form, "item_id", 1, 99_999_999, "Item ID")
        item = ITEM_CATALOG_BY_ID.get(item_id)
        if not item:
            raise ValidationError("Item ID %s does not exist or is a client display entry that cannot be safely granted." % item_id)
        hard_limit = 50 if item["kind"] in ("card", "equipment", "reagent") else 2_000_000_000
        if item["kind"] == "stack" and item["stack_num"]:
            hard_limit = item["stack_num"]
        quantity = _integer(form, "quantity", 1, hard_limit, "Quantity to Grant")

        if item["kind"] == "currency":
            field, label = CURRENCY_GRANTS[item_id]
            current = int(player.get(field, 0) or 0)
            if current + quantity > 2_000_000_000:
                raise ValidationError("Granting %s would exceed the admin safety limit." % label)
            new_count = current + quantity
            updates = {field: new_count}
            if field in ("puppet_skill_val", "puppet_energy"):
                updates[field + "_ts"] = int(time.time())
            store.save_player(uin, **updates)
            summary = "%s +%s, current total: %s" % (label, quantity, new_count)
        elif item["kind"] == "stack":
            current = store.get_item_count(uin, item_id)
            try:
                gm.validate_stack_delta(item_id, quantity, current)
            except ValueError as exc:
                raise ValidationError(str(exc))
            new_count = store.add_item(uin, item_id, quantity)
            if item_id == 43:
                store.save_player(uin, prestige=new_count)
            summary = "%s +%s, current inventory total: %s" % (item["name"], quantity, new_count)
        elif item["kind"] == "equipment":
            for _ in range(quantity):
                uid = store.next_equipment_uid(uin)
                store.add_equipment(uin, uid, item_id)
            total = sum(1 for equipment in store.get_equipments(uin)
                        if int(equipment["item_id"]) == item_id)
            summary = "%s equipment instances +%s, current total: %s" % (item["name"], quantity, total)
        elif item["kind"] == "card":
            try:
                card_id = int(item["card_spec"].split(":", 1)[0])
            except (TypeError, ValueError):
                raise ValidationError("This plant card item has no valid battleCard mapping.")
            if card_id not in CARD_NAMES:
                raise ValidationError("Card ID %s is not in the client's battleCard table." % card_id)
            taken = {int(uid) for uid, _cid, _level, _exp in store.get_cards(uin)}
            for _ in range(quantity):
                uid = 10_000_000 + card_id
                while uid in taken:
                    uid += 1
                taken.add(uid)
                store.add_card(uin, uid, card_id, 1)
            total = sum(1 for _uid, cid, _level, _exp in store.get_cards(uin)
                        if int(cid) == card_id)
            summary = "%s plant card instances +%s, current total: %s" % (item["name"], quantity, total)
        elif item["kind"] == "reagent":
            for _ in range(quantity):
                uid = store.next_reagent_uid(uin)
                store.add_reagent(uin, uid, item_id, level=1, position=0, slot_id=0)
            total = sum(1 for reagent in store.get_reagents(uin)
                        if int(reagent["base_id"]) == item_id)
            summary = "%s reagent instances +%s, current total: %s" % (item["name"], quantity, total)
        else:
            raise ValidationError("Granting this item type is not supported yet.")

        self._redirect(_catalog_url(uin, form), summary + ". Re-enter the game to see the changes.")

    def _edit_plant(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        plant_id = _integer(form, "plant_id", 1, 99_999_999, "Plant ID")
        operation = (form.get("operation", [""])[0] or "").strip()
        if operation == "add":
            if plant_id not in PLANT_NAMES:
                raise ValidationError("Plant ID %s is not in the client's Tower Defense plant table." % plant_id)
            store.unlock_plant(uin, plant_id)
            message = "Unlocked %s (%s)." % (PLANT_NAMES[plant_id], plant_id)
        elif operation == "remove":
            store.remove_plant(uin, plant_id)
            message = "Removed Tower Defense plant %s." % plant_id
        else:
            raise ValidationError("Invalid plant action.")
        self._redirect("/player?uin=%s" % uin, message)

    def _edit_progress(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        stage = _integer(form, "stage", 1, 999, "World")
        level = _integer(form, "level", 1, 999, "Stage")
        raw_sub = (form.get("sub", [""])[0] or "").strip()
        candidates = sorted(key[2] for key in LEVEL_KEYS if key[:2] == (stage, level))
        if not candidates:
            raise ValidationError("World %s, stage %s does not exist in the client." % (stage, level))
        sub = max(candidates) if not raw_sub else _integer(form, "sub", 1, 999, "小关")
        if (stage, level, sub) not in LEVEL_KEYS:
            raise ValidationError("Stage %s-%s-%s does not exist in the client." % (stage, level, sub))
        gm.passto(uin, stage, level, sub)
        self._redirect("/player?uin=%s" % uin,
                       "Cleared through %s-%s-%s using game logic, including gates and reward plants." % (stage, level, sub))

    def _edit_cards(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        player = _require_player(uin)
        target = _integer(form, "level", 1, 999, "Card Level")
        raw_uid = (form.get("uid", [""])[0] or "").strip()
        uid = _integer(form, "uid", 1, None, "卡牌 UID") if raw_uid else None
        cards = [card for card in store.get_cards(uin) if uid is None or card[0] == uid]
        if not cards:
            raise ValidationError("No plant cards found to edit.")
        # 复用服务器的XP曲线和Account Level上限，避免写出客户端无法解释的Level。
        import pvz_poc_server as game
        game.load_card_level_curve()
        exact = next((total for level, total, _next in game._CARD_LEVEL_CURVE if level == target), None)
        if exact is None:
            raise ValidationError("Card level %s does not exist in the XP table." % target)
        changed = []
        for card_uid, card_id, _old_level, _old_exp in cards:
            new_level, kept_exp = game.card_level_from_exp(exact, card_id, int(player["level"]))
            store.set_card_level(uin, card_uid, new_level, kept_exp)
            changed.append(new_level)
        actual = min(changed) if changed else target
        suffix = "(capped by account level; actual level is %s)" % actual if actual != target else ""
        self._redirect("/player?uin=%s" % uin,
                       "Updated the levels of %s plant cards%s." % (len(changed), suffix))

    def _edit_diamond(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        baseline = _baseline(form, uin)
        flag = _integer(form, "flag", 0, 1, "Membership Card Toggle")
        blue = _integer(form, "blue", 0, 99, "Blue Diamond Level")
        yellow = _integer(form, "yellow", 0, 99, "Gold Diamond Level")
        xinyue = _integer(form, "xinyue", 0, 99, "Heartfelt Membership Level")
        _save_incremental(uin, dict(diamond_flag=flag, diamond_blue_lvl=blue,
            diamond_yellow_lvl=yellow, diamond_xinyue_lvl=xinyue), baseline)
        self._redirect(
            "/player?uin=%s" % uin,
            "Membership saved: display=%s, blue diamonds=%s, gold diamonds=%s, heartfelt=%s. Changes take effect after re-entering the game." %
            (flag, blue, yellow, xinyue))

    def _edit_friend(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        operation = (form.get("operation", [""])[0] or "").strip()
        role_id = _integer(form, "friend_role_id", 1, 9_223_372_036_854_775_807,
                           "Friend Character ID")
        if operation == "remove":
            store.remove_friend(uin, role_id)
            self._redirect("/player?uin=%s" % uin, "Removed friend %s." % role_id)
            return
        if operation != "add":
            raise ValidationError("Invalid friend action.")
        if role_id == uin:
            raise ValidationError("You cannot add yourself as a friend.")
        name = (form.get("friend_name", [""])[0] or "").strip()
        if not name or len(name) > 24 or any(ord(ch) < 32 for ch in name):
            raise ValidationError("Friend name must be 1–24 visible characters.")
        plant_id = _integer(form, "plant_id", 1, 99_999_999, "Friend Card Plant ID")
        if plant_id not in PLANT_NAMES:
            raise ValidationError("Plant ID %s is not in the client's Tower Defense plant table." % plant_id)
        plant_level = _integer(form, "plant_level", 1, 999, "Friend Card Level")
        diamond_flag = _integer(form, "diamond_flag", 0, 1, "Friend Membership Card Toggle")
        blue = _integer(form, "blue", 0, 99, "Friend Blue Diamonds")
        yellow = _integer(form, "yellow", 0, 99, "Friend Gold Diamonds")
        xinyue = _integer(form, "xinyue", 0, 99, "Friend Heartfelt Membership")
        store.add_friend(
            uin, role_id, name, plant_id, plant_level,
            diamond_flag, blue, yellow, xinyue)
        self._redirect(
            "/player?uin=%s" % uin,
            "Added / updated friend %s (%s). Changes take effect after re-entering the game." % (name, role_id))

    def _reset_account(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        confirm = (form.get("confirm_uin", [""])[0] or "").strip()
        acknowledged = (form.get("ack", [""])[0] or "") == "yes"
        if confirm != str(uin) or not acknowledged:
            raise ValidationError("UIN confirmation did not match; reset cancelled.")
        backup, affected = _reset_player(uin)
        count = sum(rows for _table, rows in affected)
        self._redirect("/player?uin=%s" % uin,
                       "Account reset to a new-player save. Removed %s records. Backup: %s" %
                       (count, os.path.basename(backup)))

    def _manual_backup(self, form):
        destination = _backup_database("manual")
        self._redirect("/", "Database backed up: %s" % os.path.basename(destination))

    def log_message(self, fmt, *args):
        sys.stdout.write("[admin] %s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()


class AdminServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # macOS 在刚Off浏览器连接后会短暂保留 TIME_WAIT，允许立即重启。
    # Windows 的 SO_REUSEADDR 语义不同，保持严格模式以避免两个后台同时监听。
    allow_reuse_address = sys.platform != "win32"


def serve(port=ADMIN_PORT, db_path=None):
    if db_path:
        store.set_db_path(os.path.abspath(db_path))
    store.init_db()
    httpd = AdminServer((ADMIN_HOST, int(port)), AdminHandler)
    print("[admin] Local admin panel: http://%s:%d" % (ADMIN_HOST, int(port)))
    print("[admin] Database: %s" % store.DB_PATH)
    sys.stdout.flush()
    with httpd:
        httpd.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description="PvZ OL Local Admin Panel")
    parser.add_argument("--port", type=int, default=ADMIN_PORT, help="Local listening port")
    parser.add_argument("--db", help="Path to players.db (mainly for testing)")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be in 1..65535")
    try:
        serve(args.port, args.db)
    except KeyboardInterrupt:
        print("\n[admin] Stopped")
    except OSError as exc:
        print("[admin] Could not listen on 127.0.0.1:%s: %s" % (args.port, exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
