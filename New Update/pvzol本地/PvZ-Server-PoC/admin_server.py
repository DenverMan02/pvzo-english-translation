#!/usr/bin/env python3
"""植物大战僵尸 OL 本地 GM 管理后台。

仅监听 127.0.0.1，直接复用 player_store.py / gm.py 中的存档与关卡逻辑。
除 Python 标准库外不需要额外依赖。
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
    for row in gm._clientdb_dicts(table):
        try:
            key = int(row.get(id_field, ""))
        except (TypeError, ValueError):
            continue
        name = (row.get(name_field) or "").strip()
        if key > 0:
            result[key] = name or str(key)
    return result


ITEM_ROWS = gm._clientdb_dicts("ItemInfo.txt")
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
    row for row in gm._clientdb_dicts("levelselectInfo.txt")
    if (row.get("challengeLevelId", "0") or "0") in ("0", "")
]
LEVEL_KEYS = {
    (int(row.get("stageId") or 0), int(row.get("levelId") or 0),
     int(row.get("subLevelId") or 0))
    for row in LEVEL_ROWS
    if (row.get("stageId") or "0").isdigit()
    and (row.get("levelId") or "0").isdigit()
    and (row.get("subLevelId") or "0").isdigit()
}

# ItemInfo 混合了真正背包物品、账号货币、卡牌展示物、装备实例、
# 试剂实例以及一批只用于埋点的“购买原因”。后台必须按真实模型分流，
# 否则会把装备/卡牌伪造成客户端无法解释的普通堆栈。
CURRENCY_GRANTS = {
    1: ("coin", "金币"),
    3: ("sun", "阳光"),
    4: ("present_ticket", "钻石"),
    6: ("friend_point", "友情点"),
    7: ("fame", "声望"),
    9: ("energy", "体力"),
    37: ("ticket", "金钻"),
    39: ("contrib_val", "个人贡献"),
    40: ("light", "便携光源"),
    41: ("puppet_skill_val", "傀儡技能点"),
    42: ("puppet_energy", "傀儡能量"),
}
SPECIAL_STACK_IDS = {2, 38, 43}  # 繁荣度、洗炼幸运点、威望：服务器明确按 bag stack 消耗

ITEM_KIND_LABELS = {
    "stack": "背包道具",
    "currency": "账号货币",
    "card": "植物卡",
    "equipment": "装备",
    "reagent": "试剂",
}

ITEM_TYPE_LABELS = {
    1: "通用", 2: "材料/消耗品", 4: "植物卡", 5: "植物碎片",
    6: "经验豆", 7: "装备", 8: "实验室材料", 9: "叶绿素",
    10: "拼图", 11: "试剂", 12: "能量石", 13: "基因/傀儡材料",
    14: "培养液", 15: "傀儡展示项", 16: "远征资源",
}


def _row_int(row, key, default=0):
    try:
        return int(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def _item_grant_kind(row):
    item_id = _row_int(row, "id")
    item_type = _row_int(row, "type")
    if item_id in CURRENCY_GRANTS:
        return "currency"
    if item_id == 5:  # 植物经验在本服务器没有账号级数据模型
        return None
    if item_type == 4:
        card_spec = str(row.get("cardItemId") or "")
        return "card" if card_spec.split(":", 1)[0].isdigit() else None
    if item_type == 7:
        return "equipment"
    if item_type == 11:
        # 60001 试剂残渣是“药粉”转换物，不是可装备的试剂实例。
        return "reagent" if item_id != 60001 else None
    if item_type == 15:  # ItemInfo id != dummyZombieInfo.baseId，不能直接建傀儡
        return None
    if item_type == 1:
        # type=1 的低 ID 大部分是付费/操作埋点；只放行真正声明了堆叠上限的物品。
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
        return ("无效拼图编号（ID %s）" % item_id,
                "%s拼图 · 当前合法编号 %s · 保留原始数量" % (color, allowed))
    base_id = group * 100 + 1
    if 660 <= group <= 679 and 2 <= offset <= 25 and set(configured) == {base_id}:
        cells = PUZZLE_SHARED_MATERIALS.get(base_id)
        if cells:
            return ("无效拼图编号（ID %s）" % item_id,
                    "%s · 本阶 %s 格共用 ID %s · 保留原始数量" %
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
        return PLANT_NAMES[item_id], "塔防植物编号 · 旧背包记录"
    if item_id in CARD_BASE_NAMES:
        return CARD_BASE_NAMES[item_id], "植物卡原型编号 · 旧背包记录"
    puzzle_details = _invalid_puzzle_details(item_id)
    if puzzle_details:
        return puzzle_details
    return "未收录物品（ID %s）" % item_id, "当前客户端未定义 · 保留原始数量"


def _inventory_item_html(item_id):
    name, detail = _inventory_item_details(item_id)
    return esc(name) + ('<div class="sub">%s</div>' % esc(detail) if detail else "")


EDIT_FIELDS = (
    ("name", "角色名", "text", None, None),
    ("level", "账号等级", "number", 1, max(gm.player_level_values())),
    ("exp", "经验", "number", 0, 2_000_000_000),
    ("coin", "金币", "number", 0, 2_000_000_000),
    ("sun", "阳光", "number", 0, 2_000_000_000),
    ("energy", "体力", "number", 0, 2_000_000_000),
    ("present_ticket", "蓝钻", "number", 0, 2_000_000_000),
    ("ticket", "金钻", "number", 0, 2_000_000_000),
    ("fame", "声望", "number", 0, 2_000_000_000),
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
    """sqlite3.Connection 的 with 只提交事务，这里额外确保句柄被关闭。"""
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
        raise ValidationError("账号 %s 不存在，请先在首页创建。" % uin)
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
        raise ValidationError("编辑页面已过期，请刷新后重新保存。")
    try:
        baseline = json.loads(raw)
    except (ValueError, TypeError):
        raise ValidationError("编辑快照无效，请刷新后重新保存。")
    if not isinstance(baseline, dict) or baseline.get("uin") != uin:
        raise ValidationError("编辑快照与账号不匹配，请刷新后重试。")
    return baseline


def _save_incremental(uin, values, baseline):
    """Check and update only fields changed by the user, in one write transaction."""
    if any(key not in baseline for key in values):
        raise ValidationError("编辑快照缺少字段，请刷新后重试。")
    changed = {key: value for key, value in values.items() if value != baseline[key]}
    player = _require_player(uin)
    with store.atomic_player(player) as con:
        for key in changed:
            if player.get(key) != baseline[key]:
                raise ValidationError("%s 已被其他操作修改，请刷新页面后重新核对。" % key)
        for key, dependencies in (("vip_level", ("vip_card_id", "vip_endtime")),
                                  ("td_privilege", ("td_privilege_endtime",))):
            if key in changed:
                for dependency in dependencies:
                    if dependency not in baseline or player.get(dependency) != baseline[dependency]:
                        raise ValidationError("会员有效期已被其他操作修改，请刷新页面后重新核对。")
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
        raise ValidationError("%s 必须是整数。" % title)
    if minimum is not None and value < minimum:
        raise ValidationError("%s 不能小于 %s。" % (title, minimum))
    if maximum is not None and value > maximum:
        raise ValidationError("%s 不能大于 %s。" % (title, maximum))
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
                       "reason": "旧逻辑按格子序号生成了客户端未定义的拼图编号",
                       "items": rows}, stream, ensure_ascii=False, indent=2)
        _require_game_stopped()
        removed = con.executemany("DELETE FROM items WHERE uin=? AND item_id=? AND count=?",
                                 [(uin, row["item_id"], row["count"]) for row in rows])
        if removed.rowcount != len(rows):
            raise ValidationError("背包记录已变化，请刷新后重新整理。")
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
        raise ValidationError("游戏正在运行。请先在启动器停止游戏，再修改存档；当前操作尚未写入。")


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

# ---------------------------------------------------------------------------
# Shared formatting, URLs and pagination
# ---------------------------------------------------------------------------
DEFAULT_MAX_FIELDS = 80
SELECTION_MAX_FIELDS = 800          # catalog page (≤100 rows) + cart (≤60 rows) + controls
CURRENCY_LIMIT = 2_000_000_000
INSTANCE_GRANT_LIMIT = 50           # cards / equipment / reagents created per line
MAX_CART_ITEMS = 60
MAX_PRESETS = 30
MAX_RESULTS_PER_PLAYER = 5
CATALOG_PAGE_SIZES = (20, 50, 100)
INVENTORY_PAGE_SIZE = 30
ACCOUNT_PAGE_SIZE = 30
CARD_PAGE_SIZE = 40
FRIEND_PAGE_SIZE = 30
GRANT_STATE_PATH = os.path.join(HERE, "gm_grant_state.json")
GRANT_STATE_LOCK = threading.RLock()
_INTEGER_TEXT = re.compile(r"[0-9]{1,12}")


class Redirect:
    """A handler result. do_POST sends it only after the write transaction ends."""

    __slots__ = ("path", "message", "kind")

    def __init__(self, path, message="", kind="ok"):
        self.path, self.message, self.kind = path, message, kind


def _fmt(value):
    try:
        return format(int(value), ",")
    except (TypeError, ValueError):
        return esc(value)


def _short_num(value):
    """Compact Chinese magnitude for hints: 2000000000 -> 20亿, 50000 -> 5万."""
    value = int(value)
    if value >= 100_000_000 and value % 100_000_000 == 0:
        return "%d亿" % (value // 100_000_000)
    if value >= 10_000 and value % 10_000 == 0:
        return "%d万" % (value // 10_000)
    return format(value, ",")


def _num(value):
    return '<span class="num">%s</span>' % _fmt(value)


def _first(values, key, default=""):
    raw = values.get(key, [default]) if isinstance(values, dict) else [default]
    if isinstance(raw, list):
        raw = raw[0] if raw else default
    return default if raw is None else str(raw)


def _url(path, **params):
    clean = [(key, value) for key, value in params.items() if value not in (None, "")]
    return path + ("?" + urlencode(clean) if clean else "")


def _clamp_page(raw, total_pages):
    """Return (page, note). Only ASCII positive integers are page numbers."""
    text = str(raw or "").strip()
    if not text:
        return 1, ""
    if not _INTEGER_TEXT.fullmatch(text):
        return 1, "页码“%s”无效，已显示第 1 页。" % text[:12]
    page = int(text)
    if page < 1:
        return 1, "页码从 1 开始，已显示第 1 页。"
    if page > total_pages:
        return total_pages, "第 %s 页超出范围（共 %s 页），已显示最后一页。" % (page, total_pages)
    return page, ""


def _page_slice(total, raw_page, per_page):
    pages = max(1, (int(total) + per_page - 1) // per_page)
    page, note = _clamp_page(raw_page, pages)
    return page, pages, (page - 1) * per_page, note


def _page_window(page, pages):
    numbers = sorted({1, pages, *range(max(1, page - 2), min(pages, page + 2) + 1)})
    result, previous = [], 0
    for number in numbers:
        if number - previous > 1:
            result.append(None)
        result.append(number)
        previous = number
    return result


def _pager(total, page, pages, link, *, unit="条", jump_action="", jump_params=(), jump_name="page"):
    """Uniform pager: 首页/上一页/页码/下一页/末页, jump field and totals.

    ``link(n)`` returns the GET URL of page n. The jump form repeats the
    current filters as hidden fields because GET submission replaces a query.
    An empty list gets no pager: its empty state already says everything.
    """
    if not total:
        return ""
    def step(label, number, enabled, rel=""):
        if not enabled:
            return '<span class="pg-btn" aria-disabled="true">%s</span>' % label
        return '<a class="pg-btn" href="%s"%s>%s</a>' % (esc(link(number)), rel, label)

    parts = [step("首页", 1, page > 1), step("上一页", page - 1, page > 1, ' rel="prev"')]
    for number in _page_window(page, pages):
        if number is None:
            parts.append('<span class="pg-gap" aria-hidden="true">…</span>')
        elif number == page:
            parts.append('<span class="pg-btn current" aria-current="page">%s</span>' % number)
        else:
            parts.append('<a class="pg-btn" href="%s" aria-label="第 %s 页">%s</a>' % (esc(link(number)), number, number))
    parts += [step("下一页", page + 1, page < pages, ' rel="next"'), step("末页", pages, page < pages)]
    jump = ""
    if pages > 1:
        hidden = "".join('<input type="hidden" name="%s" value="%s">' % (esc(key), esc(value))
                         for key, value in jump_params if value not in (None, ""))
        jump = ('<form method="get" action="%s" class="pg-jump">%s<label>跳至 '
                '<input name="%s" type="number" min="1" max="%s" step="1" inputmode="numeric" required '
                'aria-label="页码（1–%s）"> 页</label><button type="submit" class="btn sm">跳转</button></form>') % (
                    esc(jump_action), hidden, esc(jump_name), pages, pages)
    return ('<nav class="pager" aria-label="分页"><span class="pg-info">共 <b class="num">%s</b> %s · 第 <b class="num">%s</b> / '
            '<span class="num">%s</span> 页</span><div class="pg-steps">%s</div>%s</nav>') % (
                _fmt(total), unit, page, pages, "".join(parts), jump)


def _pager_buttons(total, page, pages, *, unit="项"):
    """Pager for the catalog's single POST form: every step saves the page's selection first."""
    if not total:
        return ""
    def step(label, number, enabled):
        if not enabled:
            return '<span class="pg-btn" aria-disabled="true">%s</span>' % label
        return ('<button type="submit" class="pg-btn" name="nav" value="page:%s" formnovalidate>%s</button>'
                % (number, label))

    parts = [step("首页", 1, page > 1), step("上一页", page - 1, page > 1)]
    for number in _page_window(page, pages):
        if number is None:
            parts.append('<span class="pg-gap" aria-hidden="true">…</span>')
        elif number == page:
            parts.append('<span class="pg-btn current" aria-current="page">%s</span>' % number)
        else:
            parts.append(step(str(number), number, True))
    parts += [step("下一页", page + 1, page < pages), step("末页", pages, page < pages)]
    jump = ""
    if pages > 1:
        jump = ('<span class="pg-jump"><label>跳至 <input name="jump" type="number" min="1" max="%s" step="1" '
                'inputmode="numeric" aria-label="页码（1–%s）"> 页</label>'
                '<button type="submit" class="btn sm" name="nav" value="jump" formnovalidate>跳转</button></span>') % (pages, pages)
    return ('<nav class="pager" aria-label="分页"><span class="pg-info">共 <b class="num">%s</b> %s · 第 <b class="num">%s</b> / '
            '<span class="num">%s</span> 页</span><div class="pg-steps">%s</div>%s</nav>') % (
                _fmt(total), unit, page, pages, "".join(parts), jump)


def _search_terms(text):
    """Case-insensitive AND terms; full-width spaces separate Chinese keywords too."""
    return [term for term in str(text or "").replace("\u3000", " ").casefold().split() if term]


# ---------------------------------------------------------------------------
# Item catalog helpers
# ---------------------------------------------------------------------------
def _catalog_matches(search, item_kind):
    terms = _search_terms(search)
    matches = [item for item in ITEM_CATALOG
               if (not item_kind or item["kind"] == item_kind)
               and all(term in item["search"] for term in terms)]
    query = str(search or "").strip()
    if query.isdigit():
        exact = int(query)
        matches.sort(key=lambda item: (exact not in item["ids"], item["id"]))
    return matches


def _grant_limit(item):
    if item["kind"] in ("card", "equipment", "reagent"):
        return INSTANCE_GRANT_LIMIT
    if item["kind"] == "stack" and item["stack_num"]:
        return min(item["stack_num"], CURRENCY_LIMIT)
    return CURRENCY_LIMIT


def _card_id(item):
    head = str(item.get("card_spec") or "").split(":", 1)[0]
    return int(head) if head.isdigit() else None


def _catalog_entry(item_id):
    """Display entry for an exact grantable ID (merged gift IDs keep their own ID)."""
    item = ITEM_CATALOG_BY_ID.get(item_id)
    if item is None:
        return None
    return item


def _parse_quantity(raw, item):
    """Strict quantity parsing: returns (value, error)."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None, "数量不能为空"
    if text.startswith("-") and _INTEGER_TEXT.fullmatch(text[1:].strip()):
        return None, "数量不能为负数"
    if not _INTEGER_TEXT.fullmatch(text):
        return None, "数量必须是整数（不能含小数点、字母或符号）"
    value = int(text)
    if value == 0:
        return None, "数量必须大于 0"
    limit = _grant_limit(item)
    if value > limit:
        return None, "超过单次上限 %s" % _fmt(limit)
    return value, ""


def _owned_counter(uin):
    """Snapshot of what the account owns, per grant model."""
    player = _require_player(uin)
    stack = dict(store.get_items(uin))
    equipment, cards, reagents = {}, {}, {}
    for row in store.get_equipments(uin):
        equipment[int(row["item_id"])] = equipment.get(int(row["item_id"]), 0) + 1
    for _uid, card_id, _level, _exp in store.get_cards(uin):
        cards[int(card_id)] = cards.get(int(card_id), 0) + 1
    for row in store.get_reagents(uin):
        reagents[int(row["base_id"])] = reagents.get(int(row["base_id"]), 0) + 1

    def count(item, exact_id=None):
        kind = item["kind"]
        if kind == "currency":
            return int(player.get(CURRENCY_GRANTS[item["id"]][0], 0) or 0)
        if kind == "equipment":
            return equipment.get(exact_id or item["id"], 0)
        if kind == "reagent":
            return reagents.get(exact_id or item["id"], 0)
        if kind == "card":
            card_id = _card_id(item)
            return cards.get(card_id, 0) if card_id is not None else 0
        if exact_id is not None:
            return stack.get(exact_id, 0)
        return sum(stack.get(iid, 0) for iid in item["ids"])
    return count


def _count_now(uin, item_id, con):
    """Current amount inside an open transaction (sees this batch's own writes)."""
    item = ITEM_CATALOG_BY_ID[item_id]
    kind = item["kind"]
    if kind == "currency":
        field = CURRENCY_GRANTS[item_id][0]
        row = con.execute("SELECT %s FROM players WHERE uin=?" % field, (uin,)).fetchone()
        return int((row[0] if row else 0) or 0)
    if kind == "stack":
        return store.get_item_count(uin, item_id)
    if kind == "equipment":
        return sum(1 for row in store.get_equipments(uin) if int(row["item_id"]) == item_id)
    if kind == "reagent":
        return sum(1 for row in store.get_reagents(uin) if int(row["base_id"]) == item_id)
    card_id = _card_id(item)
    return sum(1 for _uid, cid, _level, _exp in store.get_cards(uin) if int(cid) == card_id)


def _apply_grant(uin, item_id, quantity, con):
    """Write one grant line inside the caller's open transaction; return (before, after)."""
    item = ITEM_CATALOG_BY_ID.get(item_id)
    if item is None:
        raise ValidationError("道具 ID %s 不存在，或属于无法安全发放的客户端展示项。" % item_id)
    kind = item["kind"]
    before = _count_now(uin, item_id, con)
    if kind == "currency":
        field, label = CURRENCY_GRANTS[item_id]
        if before + quantity > CURRENCY_LIMIT:
            raise ValidationError("%s发放后将超过上限 2,000,000,000（当前 %s）。" % (label, _fmt(before)))
        updates = {field: before + quantity}
        if field in ("puppet_skill_val", "puppet_energy"):
            updates[field + "_ts"] = int(time.time())
        store.save_player(uin, **updates)
    elif kind == "stack":
        try:
            gm.validate_stack_delta(item_id, quantity, before)
        except ValueError as exc:
            raise ValidationError(str(exc))
        after = store.add_item(uin, item_id, quantity)
        if item_id == 43:  # 威望同时缓存在玩家列中
            store.save_player(uin, prestige=after)
    elif kind == "equipment":
        for _ in range(quantity):
            store.add_equipment(uin, store.next_equipment_uid(uin), item_id)
    elif kind == "card":
        card_id = _card_id(item)
        if card_id is None:
            raise ValidationError("该卡牌道具没有有效的 battleCard 映射。")
        if card_id not in CARD_NAMES:
            raise ValidationError("卡牌 ID %s 不在客户端 battleCard 表中。" % card_id)
        taken = {int(uid) for uid, _cid, _level, _exp in store.get_cards(uin)}
        for _ in range(quantity):
            uid = 10_000_000 + card_id
            while uid in taken:
                uid += 1
            taken.add(uid)
            store.add_card(uin, uid, card_id, 1)
    elif kind == "reagent":
        for _ in range(quantity):
            store.add_reagent(uin, store.next_reagent_uid(uin), item_id, level=1, position=0, slot_id=0)
    else:
        raise ValidationError("该道具类型尚不支持发放。")
    after = _count_now(uin, item_id, con)
    if after - before != quantity:
        # Never report a grant the database did not record exactly.
        raise ValidationError("%s 写入数量与请求不一致（请求 %s，实际 %s），整批已回滚。" % (
            item["name"], _fmt(quantity), _fmt(after - before)))
    return before, after


def _line_for(item_id, quantity):
    item = ITEM_CATALOG_BY_ID.get(item_id)
    return {"id": item_id, "qty": quantity,
            "name": item["name"] if item else "未知道具（ID %s）" % item_id,
            "kind": ITEM_KIND_LABELS.get(item["kind"], "—") if item else "—",
            "status": "pending", "reason": "", "before": None, "after": None}


def _validate_grant_lines(uin, entries):
    """Validate every line against the current save before any write."""
    count = _owned_counter(uin)
    lines, seen = [], set()
    for item_id, quantity in entries:
        line = _line_for(item_id, quantity)
        item = ITEM_CATALOG_BY_ID.get(item_id)
        reason = ""
        if item_id in seen:
            reason = "重复：同一批次中该道具只能出现一次"
        elif item is None:
            reason = "道具不存在，或属于无法安全发放的展示项"
        elif type(quantity) is not int or quantity <= 0:
            reason = "数量必须是正整数"
        elif quantity > _grant_limit(item):
            reason = "超过单次上限 %s" % _fmt(_grant_limit(item))
        else:
            current = count(item, item_id)
            line["before"] = current
            if item["kind"] == "currency" and current + quantity > CURRENCY_LIMIT:
                reason = "发放后将超过上限 2,000,000,000（当前 %s）" % _fmt(current)
            elif item["kind"] == "stack":
                try:
                    gm.validate_stack_delta(item_id, quantity, current)
                except ValueError as exc:
                    reason = str(exc)
            elif item["kind"] == "card":
                card_id = _card_id(item)
                if card_id is None:
                    reason = "没有有效的 battleCard 映射"
                elif card_id not in CARD_NAMES:
                    reason = "卡牌 ID %s 不在客户端 battleCard 表中" % card_id
        seen.add(item_id)
        if reason:
            line.update(status="invalid", reason=reason)
        lines.append(line)
    return lines


def _execute_grants(uin, entries):
    """All-or-nothing batch grant.

    1. Validate every line against the committed save; any invalid line
       cancels the whole batch before a single write.
    2. Write all lines inside one ``store.atomic_player`` transaction
       (BEGIN IMMEDIATE + SAVEPOINT). Any failure rolls back every line.
    3. Only after COMMIT, re-read the save to report the real amounts.
    """
    if not entries:
        raise ValidationError("已选清单为空，请先勾选道具。")
    _require_game_stopped()
    player = _require_player(uin)
    lines = _validate_grant_lines(uin, entries)
    invalid = [line for line in lines if line["status"] == "invalid"]
    if invalid:
        for line in lines:
            if line["status"] == "pending":
                line.update(status="skipped", reason="未执行：同批次有 %d 项未通过校验，整批未写入" % len(invalid))
        return {"ok": False, "lines": lines,
                "message": "整批未写入：%d 项未通过校验，请修改后重新发放。" % len(invalid)}
    failed = None
    try:
        with store.atomic_player(player) as con:
            # Re-check inside the writer reservation: the game may have started.
            _require_game_stopped()
            for index, line in enumerate(lines):
                failed = index
                line["before"], line["after"] = _apply_grant(uin, line["id"], line["qty"], con)
            failed = None
    except (ValidationError, sqlite3.Error, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "数据库错误：%s" % exc
        count = _owned_counter(uin)
        for index, line in enumerate(lines):
            item = ITEM_CATALOG_BY_ID.get(line["id"])
            line["after"] = count(item, line["id"]) if item else None
            if failed is None:
                line.update(status="skipped", reason="未执行：" + reason)
            elif index == failed:
                line.update(status="failed", reason=reason)
            elif index < failed:
                line.update(status="rolled_back", reason="已回滚：第 %d 项失败，整批撤销" % (failed + 1))
            else:
                line.update(status="skipped", reason="未执行：第 %d 项失败，整批撤销" % (failed + 1))
        where = "第 %d 项（%s）" % (failed + 1, lines[failed]["name"]) if failed is not None else "提交前检查"
        return {"ok": False, "lines": lines, "message": "整批未写入：%s失败——%s" % (where, reason)}
    count = _owned_counter(uin)
    for line in lines:
        line["after"] = count(ITEM_CATALOG_BY_ID[line["id"]], line["id"])
        line.update(status="ok", reason="")
    return {"ok": True, "lines": lines,
            "message": "已发放 %d 项给 %s（UIN %s），逐项数量见下表。重新进入游戏后可见。" % (
                len(lines), player.get("name") or "未命名角色", uin)}


# ---------------------------------------------------------------------------
# Selection (已选清单), presets and batch results — a small JSON file next to
# players.db. It never stores save data; losing it only loses the selection.
# ---------------------------------------------------------------------------
def _load_grant_state():
    try:
        with open(GRANT_STATE_PATH, encoding="utf-8") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        state = {}
    except (OSError, ValueError):
        try:
            os.replace(GRANT_STATE_PATH, GRANT_STATE_PATH + ".broken")
        except OSError:
            pass
        state = {}
    if not isinstance(state, dict):
        state = {}
    for key in ("carts", "results"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    if not isinstance(state.get("presets"), list):
        state["presets"] = []
    return state


def _save_grant_state(state):
    temporary = "%s.%d.%d.tmp" % (GRANT_STATE_PATH, os.getpid(), threading.get_ident())
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, GRANT_STATE_PATH)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise ValidationError("已选清单未能保存：%s" % exc)


def _clean_entries(raw):
    entries, seen = [], set()
    for entry in raw if isinstance(raw, list) else []:
        try:
            item_id, quantity = int(entry[0]), int(entry[1])
        except (TypeError, ValueError, IndexError):
            continue
        if item_id in seen or item_id not in ITEM_CATALOG_BY_ID or quantity <= 0:
            continue
        seen.add(item_id)
        entries.append([item_id, quantity])
    return entries[:MAX_CART_ITEMS]


def _cart(state, uin):
    key = str(int(uin))
    cart = state["carts"].get(key)
    if not isinstance(cart, dict):
        cart = {}
    cart["items"] = _clean_entries(cart.get("items"))
    cart["undo"] = _clean_entries(cart.get("undo")) or None
    try:
        cart["rev"] = int(cart.get("rev") or 0)
    except (TypeError, ValueError):
        cart["rev"] = 0
    if not isinstance(cart.get("token"), str) or len(cart["token"]) < 16:
        cart["token"] = secrets.token_urlsafe(18)
    state["carts"][key] = cart
    return cart


def _cart_summary(uin):
    with GRANT_STATE_LOCK:
        cart = _cart(_load_grant_state(), uin)
    return len(cart["items"]), sum(quantity for _iid, quantity in cart["items"])


def _store_result(state, uin, result):
    key = str(int(uin))
    batch = "%s-%s" % (time.strftime("%Y%m%d%H%M%S"), secrets.token_hex(3))
    record = dict(result, id=batch, time=int(time.time()), uin=int(uin))
    history = [entry for entry in state["results"].get(key, []) if isinstance(entry, dict)]
    state["results"][key] = ([record] + history)[:MAX_RESULTS_PER_PLAYER]
    return batch


def _find_result(uin, batch):
    if not batch:
        return None
    with GRANT_STATE_LOCK:
        state = _load_grant_state()
    for entry in state["results"].get(str(int(uin)), []):
        if isinstance(entry, dict) and entry.get("id") == batch:
            return entry
    return None
# ---------------------------------------------------------------------------
# Visual language shared with the desktop launcher (offline, no JavaScript)
# ---------------------------------------------------------------------------
BASE_CSS = r"""
:root{color-scheme:light;--bg:#f5f7f6;--surface:#fff;--surface-2:#f0f4f2;--surface-3:#e6ece9;--line:#dfe6e2;--line-strong:#c9d3ce;
--text:#15211b;--muted:#5b6a62;--faint:#7d8a83;--primary:#1d7f46;--primary-hover:#176c3b;--primary-ink:#fff;--primary-soft:#e3f3e8;
--danger:#b93838;--danger-soft:#fbeaea;--warn:#8a5a00;--warn-soft:#fff4dc;--info:#0b62b8;--info-soft:#e7f0fb;--ok:#1d7f46;--ok-soft:#e3f3e8;
--focus:#2a78d6;--radius:8px;--radius-lg:12px;--shadow:0 1px 2px #0f1f160d,0 2px 8px #0f1f160a}
body.dark-theme{color-scheme:dark;--bg:#111714;--surface:#18201c;--surface-2:#1e2823;--surface-3:#26322c;--line:#2c3832;--line-strong:#3a4841;
--text:#e4ece7;--muted:#a3b2aa;--faint:#86958d;--primary:#41b36f;--primary-hover:#55c381;--primary-ink:#06210f;--primary-soft:#173623;
--danger:#f07575;--danger-soft:#3a1d1d;--warn:#f0c062;--warn-soft:#352a12;--info:#7ab7f5;--info-soft:#14283d;--ok:#5fcf8c;--ok-soft:#173623;--focus:#7ab7f5;--shadow:none}
@media (prefers-color-scheme:dark){body:not(.light-theme):not(.dark-theme){color-scheme:dark;--bg:#111714;--surface:#18201c;--surface-2:#1e2823;--surface-3:#26322c;--line:#2c3832;--line-strong:#3a4841;
--text:#e4ece7;--muted:#a3b2aa;--faint:#86958d;--primary:#41b36f;--primary-hover:#55c381;--primary-ink:#06210f;--primary-soft:#173623;
--danger:#f07575;--danger-soft:#3a1d1d;--warn:#f0c062;--warn-soft:#352a12;--info:#7ab7f5;--info-soft:#14283d;--ok:#5fcf8c;--ok-soft:#173623;--focus:#7ab7f5;--shadow:none}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 "Microsoft YaHei UI","Microsoft YaHei","Segoe UI",system-ui,sans-serif}
a{color:var(--primary);text-decoration:none}a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
h1{font-size:18px;line-height:1.4;margin:0}h2{font-size:15px;line-height:1.4;margin:0}h3{font-size:14px;margin:0}
p{margin:0}.sub,.hint{color:var(--muted);font-size:13px}.hint{margin-top:6px}
.num,.mono,table{font-variant-numeric:tabular-nums}.mono{font-family:"Cascadia Mono",Consolas,ui-monospace,monospace;font-size:13px}
.nowrap{white-space:nowrap}.right{text-align:right}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.wrap{max-width:1240px;margin:0 auto;padding:0 24px}
.topbar{position:sticky;top:0;z-index:5;background:var(--surface);border-bottom:1px solid var(--line)}
.head{display:flex;align-items:center;gap:16px;min-height:52px}.brand{font-weight:700;color:var(--text)}
.head nav{margin-left:auto;display:flex;gap:16px}.main{padding:20px 24px 40px}
footer{color:var(--faint);font-size:12px;padding:16px 24px;border-top:1px solid var(--line)}
.embedded .topbar,.embedded footer{display:none}.embedded .wrap{max-width:none;padding:0}.embedded .main{padding:16px 20px 32px}
.stack{display:grid;gap:16px}.row{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);padding:16px;box-shadow:var(--shadow);min-width:0}
.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.panel-head p{margin-top:2px}.panel+.panel{margin-top:16px}
.field{display:grid;gap:4px;min-width:0;flex:1 1 160px}.field.sm{flex:0 1 128px}.field.xs{flex:0 1 96px}
.field>span,.field label,.label{font-size:12px;color:var(--muted);font-weight:600}
input,select{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--line-strong);border-radius:var(--radius);padding:7px 10px;min-height:34px;width:100%;min-width:0}
input[type=checkbox]{width:16px;height:16px;min-height:0;padding:0;accent-color:var(--primary);vertical-align:middle}
input:focus,select:focus{border-color:var(--focus);outline:none;box-shadow:0 0 0 3px color-mix(in srgb,var(--focus) 22%,transparent)}
input[type=number]{font-variant-numeric:tabular-nums}
input[placeholder]:invalid:not(:placeholder-shown):not(:focus){border-color:var(--danger)}input:user-invalid:not(:focus){border-color:var(--danger)}
.btn,button{display:inline-flex;align-items:center;justify-content:center;gap:6px;font:inherit;font-weight:600;line-height:1.2;min-height:34px;padding:7px 14px;border-radius:var(--radius);border:1px solid var(--line-strong);background:var(--surface);color:var(--text);cursor:pointer;white-space:nowrap;text-decoration:none}
.btn:hover,button:hover{background:var(--surface-2);text-decoration:none}
.btn.primary,button.primary{background:var(--primary);border-color:var(--primary);color:var(--primary-ink)}
.btn.primary:hover,button.primary:hover{background:var(--primary-hover)}
.btn.danger,button.danger{background:var(--danger-soft);border-color:color-mix(in srgb,var(--danger) 40%,transparent);color:var(--danger)}
.btn.ghost,button.ghost{background:transparent;border-color:transparent;color:var(--primary)}
.btn.ghost:hover,button.ghost:hover{background:var(--primary-soft)}
.btn.sm,button.sm{min-height:28px;padding:4px 10px;font-size:13px}.btn.block,button.block{width:100%}
button:disabled,.btn[aria-disabled=true]{opacity:.5;cursor:not-allowed}
fieldset{border:0;margin:0;padding:0;min-width:0}
.notice{display:flex;gap:8px;align-items:flex-start;padding:10px 12px;border-radius:var(--radius);border:1px solid transparent;font-size:13px;margin:0 0 16px}
.notice b{font-weight:700}.notice.ok{background:var(--ok-soft);color:var(--ok);border-color:color-mix(in srgb,var(--ok) 30%,transparent)}
.notice.error{background:var(--danger-soft);color:var(--danger);border-color:color-mix(in srgb,var(--danger) 30%,transparent)}
.notice.warn{background:var(--warn-soft);color:var(--warn);border-color:color-mix(in srgb,var(--warn) 30%,transparent)}
.notice.info{background:var(--info-soft);color:var(--info);border-color:color-mix(in srgb,var(--info) 30%,transparent)}
.chip{display:inline-flex;align-items:center;gap:4px;padding:1px 8px;border-radius:999px;background:var(--surface-2);color:var(--muted);font-size:12px;line-height:20px;white-space:nowrap}
.chip.ok{background:var(--ok-soft);color:var(--ok)}.chip.warn{background:var(--warn-soft);color:var(--warn)}.chip.error{background:var(--danger-soft);color:var(--danger)}.chip.info{background:var(--info-soft);color:var(--info)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--faint);display:inline-block}.dot.on{background:var(--ok)}
table{width:100%;border-collapse:collapse}th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
th{font-size:12px;font-weight:600;color:var(--muted);background:var(--surface-2);white-space:nowrap}
tbody tr:hover{background:color-mix(in srgb,var(--surface-2) 60%,transparent)}td.num,th.num{text-align:right}
.table-wrap{border:1px solid var(--line);border-radius:var(--radius);overflow-x:auto}
.table-wrap table tr:last-child td{border-bottom:0}.empty{padding:28px 12px;text-align:center;color:var(--muted)}
.empty b{display:block;color:var(--text);margin-bottom:4px}
.item-name{font-weight:600}.item-desc{color:var(--muted);font-size:12px;margin-top:2px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.pager{display:flex;align-items:center;gap:8px 16px;flex-wrap:wrap;margin-top:12px;font-size:13px;color:var(--muted)}
.pg-steps{display:flex;gap:4px;flex-wrap:wrap}.pg-btn{display:inline-flex;align-items:center;justify-content:center;min-width:32px;min-height:30px;padding:3px 9px;border-radius:6px;border:1px solid var(--line);background:var(--surface);color:var(--text);font:inherit;font-size:13px;font-weight:500;cursor:pointer}
a.pg-btn:hover,button.pg-btn:hover{background:var(--surface-2);text-decoration:none}.pg-btn.current{background:var(--primary);border-color:var(--primary);color:var(--primary-ink);font-weight:700}
.pg-btn[aria-disabled=true]{opacity:.45;cursor:default}.pg-gap{padding:0 2px;color:var(--faint)}
.pg-jump{display:inline-flex;align-items:center;gap:6px;margin-left:auto}.pg-jump label{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}.pg-jump input{width:72px;min-height:30px;padding:3px 8px}
.acct-bar{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:12px}
.acct-id{display:flex;align-items:center;gap:12px;min-width:0}.avatar{flex:none;display:grid;place-items:center;width:40px;height:40px;border-radius:10px;background:var(--primary-soft);color:var(--primary);font-weight:700;font-size:17px}
.acct-meta{color:var(--muted);font-size:13px}.acct-stats{display:flex;flex-wrap:wrap;gap:6px 18px;margin:0 0 0 auto}
.acct-stats div{display:grid}.acct-stats dt{font-size:12px;color:var(--muted)}.acct-stats dd{margin:0;font-weight:700;font-variant-numeric:tabular-nums}
.tabs{display:flex;flex-wrap:wrap;gap:2px;border-bottom:1px solid var(--line);margin:0 0 16px}
.tabs a{padding:8px 12px;color:var(--muted);font-weight:600;border-bottom:2px solid transparent;margin-bottom:-1px;white-space:nowrap}
.tabs a:hover{color:var(--text);text-decoration:none;background:var(--surface-2);border-radius:6px 6px 0 0}
.tabs a[aria-current=page]{color:var(--primary);border-bottom-color:var(--primary)}
.tabs .badge{display:inline-block;min-width:18px;margin-left:4px;padding:0 5px;border-radius:9px;background:var(--primary);color:var(--primary-ink);font-size:11px;line-height:18px;text-align:center}
.form-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}
.form-section+.form-section{margin-top:16px;padding-top:16px;border-top:1px solid var(--line)}
.form-section h3{font-size:13px;color:var(--muted);margin-bottom:10px}
.form-actions{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap;margin-top:16px}
.toolbar{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px}.toolbar .field{flex:1 1 200px}
.toolbar .field.sm{flex:0 1 130px}.toolbar .field.xs{flex:0 1 96px}.toolbar .field.grow{flex:3 1 220px}
.adjust{display:flex;gap:4px;align-items:center;flex-wrap:nowrap}.adjust input{width:92px;min-height:28px;padding:3px 8px}
.pill-list{display:flex;flex-wrap:wrap;gap:6px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:13px}.kv dt{color:var(--muted)}.kv dd{margin:0;font-variant-numeric:tabular-nums}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:16px}
.metric{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);padding:12px 14px}
.metric b{display:block;font-size:20px;font-variant-numeric:tabular-nums}.metric span{font-size:12px;color:var(--muted)}
.danger-zone{border-color:color-mix(in srgb,var(--danger) 35%,var(--line))}
.head-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.inline-form{display:inline-flex;gap:8px;align-items:center;margin:0}
.popover{position:relative}.popover>summary{list-style:none}.popover>summary::-webkit-details-marker{display:none}
.popover-body{position:absolute;right:0;top:calc(100% + 6px);z-index:10;width:300px;max-width:calc(100vw - 32px);background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);padding:14px;box-shadow:0 10px 28px #0f1f1630}
.popover-body .form-actions{margin-top:10px}.toolbar .toggle{margin-left:auto}
.status-line{display:flex;flex-wrap:wrap;gap:4px 18px;margin-top:12px;font-size:12px;color:var(--muted)}.embedded .standalone-only{display:none}
tr.exact,tr.exact:hover{background:var(--primary-soft)}
/* 道具发放：目录 + 已选清单（无脚本：CSS 即时反馈，提交后服务端保存） */
.grant-layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px;align-items:start}
.catalog{counter-reset:picked}.catalog input.pick:checked{counter-increment:picked}.picked-live::after{content:counter(picked)}
.catalog tbody tr:has(input.pick:checked){background:var(--primary-soft)}
.catalog-table{table-layout:fixed}.catalog-table th:nth-child(3){width:96px}.catalog-table th:nth-child(4){width:124px}.catalog-table th:nth-child(5){width:72px}
.pick-cell{width:40px;text-align:center}.item-line{display:flex;align-items:center;gap:8px;min-width:0}
.item-line .item-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.item-meta{color:var(--muted);font-size:12px;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chip.kind-currency{background:var(--warn-soft);color:var(--warn)}.chip.kind-card{background:var(--info-soft);color:var(--info)}
.chip.kind-equipment,.chip.kind-reagent{background:var(--primary-soft);color:var(--primary)}
.qty{width:100%;min-height:30px;padding:4px 8px}.id-choice{margin-top:4px;min-height:28px;padding:2px 6px}.sel-qty{font-weight:700;color:var(--primary)}
.catalog-foot{position:sticky;bottom:0;z-index:2;display:flex;justify-content:space-between;align-items:center;gap:8px 12px;flex-wrap:wrap;
margin:12px -16px -16px;padding:10px 16px;background:var(--surface);border-top:1px solid var(--line);border-radius:0 0 var(--radius-lg) var(--radius-lg)}
.cart{position:sticky;top:12px;display:flex;flex-direction:column;gap:10px;max-height:calc(100vh - 24px)}
.cart-head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.cart-target{margin:0;padding:8px 10px;border-radius:var(--radius);background:var(--primary-soft);font-size:13px;overflow-wrap:anywhere}
.cart-list{list-style:none;margin:0;padding:0;overflow:auto;min-height:44px;flex:0 1 auto;overscroll-behavior:contain;border-block:1px solid var(--line)}
.cart-row{display:grid;grid-template-columns:minmax(0,1fr) 84px 30px;gap:6px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)}
.cart-row:last-child{border-bottom:0}.cart-name{min-width:0}.cart-row .item-name{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cart-row input{min-height:28px;padding:3px 8px}button.icon{width:30px;min-width:30px;padding:0;font-size:18px;line-height:1}
.cart-actions{display:grid;gap:8px}.row.tight{gap:8px;align-items:flex-end}.empty.compact{padding:14px 6px}
.result-panel .notice{margin:0 0 8px}.result-panel details.more>summary{font-weight:500}
.result-table td,.result-table th{font-size:13px}.status-ok{color:var(--ok);font-weight:700}.status-bad{color:var(--danger);font-weight:700}.status-muted{color:var(--muted)}
details.more>summary{cursor:pointer;color:var(--primary);font-weight:600;font-size:13px;list-style:none}details.more>summary::-webkit-details-marker{display:none}
details.more[open]>summary{margin-bottom:10px}
.account-list td:first-child{min-width:180px}.account-name{font-weight:600;color:var(--text)}
@media (max-width:880px){.grant-layout{grid-template-columns:minmax(0,1fr)}.cart{position:static;max-height:none}.cart-list{overflow:visible}}
@media (max-width:760px){.main,.embedded .main{padding:12px}.acct-stats{margin-left:0}.hide-sm{display:none}.pg-jump{margin-left:0}
.panel{padding:12px}th,td{padding:7px 8px}.adjust input{width:72px}}
@media (max-width:560px){.hide-xs{display:none}.adjust{flex-wrap:wrap}}
"""


def _page(title, content, flash_message="", flash_kind="ok"):
    flash = ""
    if flash_message:
        kind = flash_kind if flash_kind in ("ok", "error", "warn", "info") else "ok"
        flash = '<div class="notice %s" role="%s">%s</div>' % (
            kind, "alert" if kind == "error" else "status", esc(flash_message))
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s · PvZ OL GM 后台</title>
<style>%s</style></head><body><header class="topbar"><div class="wrap head"><a class="brand" href="/">PvZ OL GM 后台</a>
<span class="chip">仅限本机</span><nav><a href="/">账号列表</a><a href="http://127.0.0.1:8080/" target="_blank" rel="noreferrer">游戏网页</a></nav></div></header>
<main class="wrap main" id="main">%s%s</main><footer>本地 GM 工具 · 所有修改直接写入 players.db · 重置等高风险操作会先自动备份</footer></body></html>""" % (
        esc(title), BASE_CSS, flash, content)


def _csrf_input():
    return '<input type="hidden" name="csrf" value="%s">' % esc(CSRF_TOKEN)


def _redirect_url(path, message="", kind="ok"):
    if not message:
        return path
    sep = "&" if "?" in path else "?"
    return path + sep + urlencode({"msg": message, "kind": kind})


def _catalog_url(uin, values=None, **extra):
    """Catalog URL that keeps search, type, page and page size."""
    values = values or {}

    def first(*keys):
        for key in keys:
            raw = _first(values, key)
            if raw not in (None, ""):
                return raw
        return ""

    params = {"uin": str(int(uin))}
    query = first("catalog_q", "q").strip()[:60]
    item_kind = first("catalog_kind", "item_kind").strip()
    page = first("catalog_page", "page").strip()
    per = first("catalog_per", "per").strip()
    if query:
        params["q"] = query
    if item_kind in ITEM_KIND_LABELS:
        params["item_kind"] = item_kind
    if page.isdigit() and int(page) > 1:
        params["page"] = str(int(page))
    if per.isdigit() and int(per) in CATALOG_PAGE_SIZES and int(per) != 50:
        params["per"] = per
    params.update({key: value for key, value in extra.items() if value not in (None, "")})
    return "/items?" + urlencode(params)


PLAYER_TABS = (("attributes", "基础属性"), ("inventory", "背包道具"), ("items", "道具发放"),
               ("plants", "塔防植物"), ("cards", "植物卡"), ("progress", "关卡进度"),
               ("vip", "钻石特权"), ("friends", "好友管理"), ("reset", "重置账号"))


def _player_url(uin, section="attributes", **params):
    if section == "items":
        return _url("/items", uin=uin, **params)
    return _url("/player", uin=uin, section=section, **params)


def _tabs(uin, active, cart_count=0):
    links = []
    for key, label in PLAYER_TABS:
        badge = ('<span class="badge" aria-label="已选 %s 项">%s</span>' % (cart_count, cart_count)
                 if key == "items" and cart_count else "")
        links.append('<a href="%s"%s>%s%s</a>' % (
            esc(_player_url(uin, key)), ' aria-current="page"' if key == active else "", label, badge))
    return '<nav class="tabs" aria-label="账号管理分区">%s</nav>' % "".join(links)


def _account_bar(player, uin):
    name = str(player.get("name") or "未命名角色")
    stats = (("金币", _fmt(player.get("coin", 0))), ("阳光", _fmt(player.get("sun", 0))),
             ("蓝钻", _fmt(player.get("present_ticket", 0))), ("金钻", _fmt(player.get("ticket", 0))),
             ("塔防", "%s-%s-%s" % (player.get("td_stage", 0), player.get("td_level", 0), player.get("td_sub", 0))))
    return ('<div class="acct-bar"><div class="acct-id"><span class="avatar" aria-hidden="true">%s</span><div>'
            '<h1>%s</h1><div class="acct-meta"><span class="mono">UIN %s</span> · Lv.%s · %s</div></div></div>'
            '<dl class="acct-stats">%s</dl><a class="btn sm" href="/">切换账号</a></div>') % (
                esc(name[:1]), esc(name), uin, esc(player.get("level", 0)),
                "VIP%s" % esc(player.get("vip_level", 0)) if int(player.get("vip_level", 0) or 0) else "未开通 VIP",
                "".join('<div><dt>%s</dt><dd>%s</dd></div>' % (label, value) for label, value in stats))


def _hidden(**fields):
    return "".join('<input type="hidden" name="%s" value="%s">' % (esc(key), esc(value))
                   for key, value in fields.items() if value not in (None, ""))
# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------
CATALOG_DISPLAY_BY_ID = {item["id"]: item for item in ITEM_CATALOG}
CURRENCY_FIELD_LABELS = {key: label for key, label, _kind, _min, _max in EDIT_FIELDS}
GRANT_STATUS_LABELS = {"ok": ("status-ok", "✓ 已发放"), "invalid": ("status-bad", "✗ 未通过校验"),
                       "failed": ("status-bad", "✗ 写入失败"), "rolled_back": ("status-muted", "↺ 已回滚"),
                       "skipped": ("status-muted", "– 未执行"), "pending": ("status-muted", "–")}


def _game_running():
    return guard_blocks_writes() or _port_open(9001)


def _render_dashboard(query):
    search = (_first(query, "q") or "").strip()[:100]
    include_robots = _first(query, "include_robots") == "1"
    terms = _search_terms(search)[:5]
    # A pure-number query may be an exact UIN: show it even when robots are hidden.
    exact_uin = int(search) if search.isdigit() and len(search) <= 18 else None
    with _connection() as con:
        robot_total = con.execute("SELECT COUNT(*) FROM players WHERE uin >= 900000 AND uin < 1000000").fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM players WHERE uin < 900000 OR uin >= 1000000").fetchone()[0]
        sql = ("SELECT uin,name,level,coin,sun,ticket,present_ticket,vip_level,td_stage,td_level,td_sub,last_login "
               "FROM players")
        clauses, args = [], []
        if not include_robots:
            if exact_uin is not None:
                clauses.append("((uin < 900000 OR uin >= 1000000) OR uin = ?)")
                args.append(exact_uin)
            else:
                clauses.append("(uin < 900000 OR uin >= 1000000)")
        for term in terms:
            clauses.append(r"(CAST(uin AS TEXT) LIKE ? ESCAPE '\' OR name LIKE ? ESCAPE '\')")
            like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            args.extend([like, like])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        matching = con.execute("SELECT COUNT(*) FROM (" + sql + ")", args).fetchone()[0]
        page, pages, offset, page_note = _page_slice(matching, _first(query, "page"), ACCOUNT_PAGE_SIZE)
        order = " ORDER BY "
        order_args = []
        if exact_uin is not None:
            order += "CASE WHEN uin = ? THEN 0 ELSE 1 END, "
            order_args.append(exact_uin)
        order += ("CASE WHEN uin >= 900000 AND uin < 1000000 THEN 1 ELSE 0 END, last_login DESC, uin DESC"
                  " LIMIT ? OFFSET ?")
        players = [dict(row) for row in con.execute(sql + order, args + order_args + [ACCOUNT_PAGE_SIZE, offset])]
        exact_exists = exact_uin is not None and con.execute(
            "SELECT 1 FROM players WHERE uin=?", (exact_uin,)).fetchone() is not None

    def list_url(number=1, *, robots=include_robots, keep_search=True):
        return _url("/", q=search if keep_search else "", include_robots="1" if robots else "",
                    page=number if number > 1 else "")

    rows = []
    for player in players:
        uin = int(player["uin"])
        robot = 900000 <= uin < 1000000
        name = str(player["name"] or "未命名角色")
        rows.append(
            '<tr%s><td><a class="account-name" href="%s">%s</a>%s<div class="sub mono">UIN %s</div></td>'
            '<td class="num nowrap">Lv.%s</td><td class="nowrap hide-sm">%s</td><td class="num nowrap">%s</td>'
            '<td class="num nowrap hide-sm">%s / %s</td><td class="num nowrap hide-xs">%s-%s-%s</td>'
            '<td class="nowrap hide-sm">%s</td><td class="nowrap right"><a class="btn sm" href="%s">管理</a> '
            '<a class="btn sm ghost" href="%s">发放道具</a></td></tr>' % (
                ' class="exact"' if uin == exact_uin else "",
                esc(_player_url(uin)), esc(name), ' <span class="chip">机器人</span>' if robot else "", uin,
                esc(player["level"]), "VIP%s" % esc(player["vip_level"]) if player["vip_level"] else "—",
                _fmt(player["coin"]), _fmt(player["present_ticket"]), _fmt(player["ticket"]),
                esc(player["td_stage"]), esc(player["td_level"]), esc(player["td_sub"]),
                esc(_format_time(player["last_login"])), esc(_player_url(uin)), esc(_player_url(uin, "items"))))
    create_exact = ""
    if exact_uin is not None and not exact_exists and exact_uin > 0:
        create_exact = ('<form method="post" action="/player/new" class="inline-form">%s%s'
                        '<button type="submit" class="primary sm">新建账号 %s</button></form>') % (
                            _csrf_input(), _hidden(uin=exact_uin), exact_uin)
    if not rows:
        if exact_uin is not None and not exact_exists:
            empty = '<b>UIN %s 还没有存档</b>可以直接用这个编号新建一个独立的新手存档。%s' % (exact_uin, create_exact)
        elif search:
            empty = '<b>没有匹配“%s”的账号</b>换个角色名或 UIN 再试%s。' % (
                esc(search), "，或点“显示机器人账号”" if not include_robots and robot_total else "")
        else:
            empty = '<b>还没有玩家账号</b>点右上角“新建账号”，输入一个未使用的 UIN 即可创建。'
        rows.append('<tr><td colspan="8" class="empty">%s</td></tr>' % empty)
    elif create_exact:
        # Partial matches exist, but the exact UIN itself is still free.
        rows.insert(0, '<tr class="exact"><td colspan="8"><div class="row" style="align-items:center">'
                       '<span class="sub">UIN <b class="mono">%s</b> 还没有存档。</span>%s</div></td></tr>' % (exact_uin, create_exact))
    robots_label = "隐藏机器人账号" if include_robots else "显示机器人账号（%s）" % _fmt(robot_total)
    pager = _pager(matching, page, pages, list_url, unit="个账号", jump_action="/",
                   jump_params=(("q", search), ("include_robots", "1" if include_robots else "")))
    note = '<div class="notice info">%s</div>' % esc(page_note) if page_note else ""
    web_on, game_on = _port_open(8080), _port_open(9001)
    summary = "玩家账号 <b class=\"num\">%s</b> 个%s" % (
        _fmt(total), " · 含机器人 %s 个" % _fmt(robot_total) if include_robots else "")
    if search:
        summary += " · 匹配 <b class=\"num\">%s</b> 个" % _fmt(matching)
    return """<section class="panel" aria-labelledby="accounts-title">
<div class="panel-head"><div><h1 id="accounts-title">账号列表</h1><p class="sub">%s</p></div>
<div class="head-actions"><details class="popover"><summary class="btn primary">新建账号</summary>
<form method="post" action="/player/new" class="popover-body">%s<div class="field"><label for="new-uin">新账号 UIN（纯数字，未被占用）</label>
<input id="new-uin" class="mono" name="uin" inputmode="numeric" pattern="[0-9]+" maxlength="18" required placeholder="例如 700000205" autocomplete="off"></div>
<p class="hint">创建独立的新手存档；已有存档不会被覆盖。</p><div class="form-actions"><button type="submit" class="primary">创建</button></div></form></details>
<form method="post" action="/backup" class="inline-form">%s<button type="submit" title="复制一份 players.db 到 backups/admin">立即备份</button></form></div></div>
<form method="get" action="/" class="toolbar" role="search">%s<div class="field"><label for="account-q">搜索角色名或 UIN（输入完整 UIN 可直接打开或新建）</label>
<input id="account-q" name="q" value="%s" placeholder="例如：豌豆 或 700000200" autocomplete="off" type="search"></div>
<button type="submit" class="primary">搜索</button>%s<a class="btn ghost toggle" href="%s" role="switch" aria-checked="%s">%s</a></form>%s
<div class="table-wrap"><table class="account-list"><thead><tr><th>账号</th><th class="num">等级</th><th class="hide-sm">VIP</th><th class="num">金币</th>
<th class="num hide-sm">蓝钻 / 金钻</th><th class="num hide-xs">塔防</th><th class="hide-sm">最近登录</th><th class="right">操作</th></tr></thead><tbody>%s</tbody></table></div>%s
<p class="status-line standalone-only"><span>数据库 <span class="mono">%s</span></span><span><span class="dot%s"></span> 网页资源 8080 %s</span>
<span><span class="dot%s"></span> 游戏服务 9001 %s</span></p></section>""" % (
        summary, _csrf_input(), _csrf_input(),
        _hidden(include_robots="1" if include_robots else ""), esc(search),
        '<a class="btn ghost" href="%s">清除</a>' % esc(list_url(keep_search=False)) if search else "",
        esc(list_url(robots=not include_robots)), "true" if include_robots else "false", esc(robots_label),
        note, "".join(rows), pager, esc(os.path.basename(store.DB_PATH)),
        " on" if web_on else "", "运行中" if web_on else "未运行", " on" if game_on else "",
        "运行中（GM 只读）" if game_on else "未运行")


def _render_attributes(player, uin):
    groups = (("角色", ("name", "level", "exp")),
              ("货币", ("coin", "sun", "energy", "present_ticket", "ticket", "fame", "friend_point")))
    fields = {key: (label, kind, minimum, maximum) for key, label, kind, minimum, maximum in EDIT_FIELDS}
    sections = []
    for title, keys in groups:
        inputs = []
        for key in keys:
            label, kind, minimum, maximum = fields[key]
            attrs = (' type="number" min="%s" max="%s" step="1" inputmode="numeric"' % (minimum, maximum)
                     if kind == "number" else ' type="text" maxlength="24"')
            inputs.append('<div class="field"><label for="f-%s">%s</label><input id="f-%s" name="%s" value="%s"%s required></div>' % (
                key, esc(label), key, key, esc(player.get(key, "")), attrs))
        sections.append('<div class="form-section"><h3>%s</h3><div class="form-grid">%s</div></div>' % (title, "".join(inputs)))
    vip = int(player.get("vip_level", 0) or 0)
    privilege = int(player.get("td_privilege", 0) or 0)
    sections.append(
        '<div class="form-section"><h3>特权</h3><div class="form-grid"><div class="field"><label for="f-vip">VIP 等级</label>'
        '<select id="f-vip" name="vip_level">%s</select></div><div class="field"><label for="f-td">塔防特权</label>'
        '<select id="f-td" name="td_privilege"><option value="0"%s>关闭</option><option value="1"%s>开启（10 年）</option></select></div></div>'
        '<p class="hint">修改 VIP 等级会清除原有会员卡有效期；塔防特权开启后有效期为 10 年。</p></div>' % (
            "".join('<option value="%s"%s>%s</option>' % (v, " selected" if vip == v else "", "关闭" if v == 0 else "VIP%d" % v)
                    for v in range(4)),
            "" if privilege else " selected", " selected" if privilege else ""))
    snapshot = _snapshot_input(player, [field[0] for field in EDIT_FIELDS] +
                               ["vip_level", "vip_card_id", "vip_endtime", "td_privilege", "td_privilege_endtime"])
    return ('<section class="panel"><div class="panel-head"><div><h2>基础属性</h2><p class="sub">只保存你改动过的字段；'
            '若其他页面同时修改了同一字段，会提示刷新而不会覆盖。重新进入游戏后生效。</p></div></div>'
            '<form method="post" action="/player/update">%s%s%s<div class="form-actions">'
            '<a class="btn ghost" href="%s">放弃修改</a><button type="submit" class="primary">保存基础属性</button></div></form>'
            '<p class="hint">等级会限制植物卡等级，并决定城镇建筑和后续章节是否开放。</p></section>') % (
                _csrf_input() + snapshot, _hidden(uin=uin), "".join(sections), esc(_player_url(uin, "attributes")))


def _inventory_rows(uin):
    rows = []
    for item_id, count in store.get_items(uin):
        name, detail = _inventory_item_details(item_id)
        known = item_id in ITEM_NAMES
        rows.append({"kind": "stack" if known else "legacy", "id": int(item_id), "name": name,
                     "detail": detail, "count": int(count), "uid": None,
                     "search": ("%s %s %s" % (item_id, name, detail)).casefold()})
    for equipment in store.get_equipments(uin):
        iid = int(equipment["item_id"])
        name = ITEM_NAMES.get(iid, "未知装备")
        rows.append({"kind": "equipment", "id": iid, "name": name, "detail": "装备实例 · UID %s" % equipment["uid"],
                     "count": 1, "uid": int(equipment["uid"]),
                     "search": ("%s %s 装备 %s" % (iid, name, equipment["uid"])).casefold()})
    return rows


INVENTORY_KINDS = (("", "全部"), ("stack", "普通道具"), ("equipment", "装备实例"), ("legacy", "未收录 / 旧记录"))
INVENTORY_SORTS = (("id", "按 ID"), ("count_desc", "数量从多到少"), ("count_asc", "数量从少到多"))


def _inventory_context(values):
    kind = _first(values, "ikind")
    sort = _first(values, "isort")
    return {"iq": _first(values, "iq").strip()[:60],
            "ikind": kind if kind in dict(INVENTORY_KINDS) else "",
            "isort": sort if sort in dict(INVENTORY_SORTS) and sort != "id" else "",
            "ipage": _first(values, "ipage")}


def _render_inventory(player, uin, query):
    context = _inventory_context(query)
    all_rows = _inventory_rows(uin)
    terms = _search_terms(context["iq"])
    rows = [row for row in all_rows if (not context["ikind"] or row["kind"] == context["ikind"])
            and all(term in row["search"] for term in terms)]
    if context["isort"] == "count_desc":
        rows.sort(key=lambda row: (-row["count"], row["id"]))
    elif context["isort"] == "count_asc":
        rows.sort(key=lambda row: (row["count"], row["id"]))
    else:
        rows.sort(key=lambda row: (row["id"], row["uid"] or 0))
    page, pages, offset, page_note = _page_slice(len(rows), context["ipage"], INVENTORY_PAGE_SIZE)
    visible = rows[offset:offset + INVENTORY_PAGE_SIZE]
    keep = dict(iq=context["iq"], ikind=context["ikind"], isort=context["isort"], ipage=page if page > 1 else "")
    body = []
    for row in visible:
        if row["kind"] == "equipment":
            action = '<span class="sub">独立实例，不能按数量调整</span>'
        else:
            action = ('<form method="post" action="/player/item-adjust" class="adjust">%s%s'
                      '<input name="amount" type="number" min="0" max="2000000000" step="1" value="1" inputmode="numeric" required aria-label="%s 的调整数量">'
                      '<button type="submit" class="sm" name="op" value="add" title="在当前数量上增加">＋增加</button>'
                      '<button type="submit" class="sm" name="op" value="sub" title="在当前数量上减少">－减少</button>'
                      '<button type="submit" class="sm" name="op" value="set" title="直接设为输入的数量，0 表示移除">设为</button></form>') % (
                          _csrf_input(), _hidden(uin=uin, item_id=row["id"], expected=row["count"], **keep), esc(row["name"]))
        body.append('<tr><td class="mono">%s</td><td><div class="item-name">%s</div>%s</td><td class="num"><b>%s</b></td><td>%s</td></tr>' % (
            row["id"], esc(row["name"]), '<div class="item-desc">%s</div>' % esc(row["detail"]) if row["detail"] else "",
            _fmt(row["count"]), action))
    if not body:
        if all_rows:
            body.append('<tr><td colspan="4" class="empty"><b>没有符合条件的道具</b>调整搜索词或类型后再试。</td></tr>')
        else:
            body.append('<tr><td colspan="4" class="empty"><b>背包为空</b>可在“道具发放”中批量添加道具。</td></tr>')
    stack_kinds = sum(1 for row in all_rows if row["kind"] != "equipment")
    equipment_total = sum(1 for row in all_rows if row["kind"] == "equipment")
    invalid_puzzles = sum(_invalid_puzzle_details(row["id"]) is not None for row in all_rows if row["kind"] != "equipment")
    repair = ""
    if invalid_puzzles:
        repair = ('<div class="notice warn"><div>检测到 <b>%s</b> 种无效旧拼图编号（客户端未定义）。整理前会自动备份，仅移除这些无效记录。'
                  '<form method="post" action="/player/repair-puzzles" style="margin-top:8px">%s%s<button type="submit" class="sm">备份并整理无效拼图</button></form></div></div>') % (
                      invalid_puzzles, _csrf_input(), _hidden(uin=uin))
    link = lambda number: _player_url(uin, "inventory", iq=context["iq"], ikind=context["ikind"],
                                      isort=context["isort"], ipage=number if number > 1 else "")
    pager = _pager(len(rows), page, pages, link, unit="条", jump_action="/player", jump_name="ipage",
                   jump_params=(("uin", uin), ("section", "inventory"), ("iq", context["iq"]),
                                ("ikind", context["ikind"]), ("isort", context["isort"])))
    note = '<div class="notice info">%s</div>' % esc(page_note) if page_note else ""
    kind_options = "".join('<option value="%s"%s>%s</option>' % (key, " selected" if context["ikind"] == key else "", label)
                           for key, label in INVENTORY_KINDS)
    sort_options = "".join('<option value="%s"%s>%s</option>' % (key, " selected" if (context["isort"] or "id") == key else "", label)
                           for key, label in INVENTORY_SORTS)
    clear = ('<a class="btn ghost" href="%s">清除</a>' % esc(_player_url(uin, "inventory"))
             if context["iq"] or context["ikind"] or context["isort"] else "")
    return """<section class="panel"><div class="panel-head"><div><h2>背包道具</h2><p class="sub">普通堆叠 <b class="num">%s</b> 种 · 装备实例 <b class="num">%s</b> 件。
“增加/减少”在现有数量上加减；“设为”直接改成目标数量（0 = 移除）。</p></div><a class="btn primary" href="%s">批量发放道具</a></div>%s
<form method="get" action="/player" class="toolbar" role="search">%s<div class="field"><label for="inv-q">搜索名称或 ID</label><input id="inv-q" name="iq" value="%s" placeholder="例如：钥匙 或 4001" autocomplete="off"></div>
<div class="field sm"><label for="inv-kind">类型</label><select id="inv-kind" name="ikind">%s</select></div><div class="field sm"><label for="inv-sort">排序</label><select id="inv-sort" name="isort">%s</select></div>
<button type="submit">筛选</button>%s</form>%s
<div class="table-wrap"><table><thead><tr><th>ID</th><th>名称</th><th class="num">数量</th><th>调整数量</th></tr></thead><tbody>%s</tbody></table></div>%s</section>
<section class="panel"><div class="panel-head"><div><h2>按 ID 调整</h2><p class="sub">用于列表中没有的道具 ID。新增普通道具会校验堆叠上限；减少不会低于 0。</p></div></div>
<form method="post" action="/player/item-adjust" class="row">%s%s<div class="field sm"><label for="adj-id">道具 ID</label><input id="adj-id" class="mono" name="item_id" type="number" min="1" max="99999999" step="1" inputmode="numeric" required></div>
<div class="field sm"><label for="adj-op">操作</label><select id="adj-op" name="op"><option value="add">增加</option><option value="sub">减少</option><option value="set">设为</option></select></div>
<div class="field sm"><label for="adj-amount">数量</label><input id="adj-amount" name="amount" type="number" min="0" max="2000000000" step="1" value="1" inputmode="numeric" required></div>
<button type="submit" class="primary">应用</button></form></section>""" % (
        _fmt(stack_kinds), _fmt(equipment_total), esc(_player_url(uin, "items")), repair,
        _hidden(uin=uin, section="inventory"), esc(context["iq"]), kind_options, sort_options, clear, note,
        "".join(body), pager, _csrf_input(), _hidden(uin=uin, **keep))


def _render_plants(player, uin):
    plants = store.get_player_plants(uin)
    pills = "".join('<span class="chip"><span class="mono">%s</span> %s</span>' % (pid, esc(PLANT_NAMES.get(pid, "未知植物")))
                    for pid in plants) or '<p class="empty"><b>暂无塔防植物</b>在上方输入植物 ID 解锁。</p>'
    options = "".join('<option value="%s">%s %s</option>' % (pid, pid, esc(name)) for pid, name in sorted(PLANT_NAMES.items()))
    return ('<section class="panel"><div class="panel-head"><div><h2>塔防植物</h2><p class="sub">已解锁 <b class="num">%s</b> 种。'
            '输入植物 ID（可从下拉建议中按名称选择）。</p></div></div>'
            '<form method="post" action="/player/plant" class="toolbar">%s%s<div class="field"><label for="plant-id">植物 ID</label>'
            '<input id="plant-id" class="mono" name="plant_id" type="number" min="1" step="1" list="plant-ids" required><datalist id="plant-ids">%s</datalist></div>'
            '<div class="field sm"><label for="plant-op">操作</label><select id="plant-op" name="operation"><option value="add">解锁</option><option value="remove">移除</option></select></div>'
            '<button type="submit" class="primary">应用</button></form><div class="pill-list">%s</div></section>') % (
                len(plants), _csrf_input(), _hidden(uin=uin), options, pills)


def _render_cards(player, uin, query):
    cards = store.get_cards(uin)
    page, pages, offset, page_note = _page_slice(len(cards), _first(query, "cpage"), CARD_PAGE_SIZE)
    rows = "".join('<tr><td class="mono">%s</td><td class="mono">%s</td><td>%s</td><td class="num">Lv.%s</td><td class="num">%s</td></tr>' % (
        uid, cid, esc(CARD_NAMES.get(cid, "未知卡牌")), level, _fmt(exp)) for uid, cid, level, exp in cards[offset:offset + CARD_PAGE_SIZE])
    rows = rows or '<tr><td colspan="5" class="empty"><b>还没有植物卡</b>可在“道具发放”中按“植物卡”类型发放。</td></tr>'
    pager = _pager(len(cards), page, pages, lambda n: _player_url(uin, "cards", cpage=n if n > 1 else ""), unit="张",
                   jump_action="/player", jump_name="cpage", jump_params=(("uin", uin), ("section", "cards")))
    note = '<div class="notice info">%s</div>' % esc(page_note) if page_note else ""
    return ('<section class="panel"><div class="panel-head"><div><h2>植物卡</h2><p class="sub">共 <b class="num">%s</b> 张。等级按服务器经验曲线写入，并受账号等级上限限制。</p></div></div>'
            '<form method="post" action="/player/cards" class="toolbar">%s%s<div class="field sm"><label for="card-uid">卡牌 UID（留空 = 全部）</label>'
            '<input id="card-uid" class="mono" name="uid" type="number" min="1" step="1"></div><div class="field xs"><label for="card-level">目标等级</label>'
            '<input id="card-level" name="level" type="number" min="1" max="999" step="1" required></div><button type="submit" class="primary">设置等级</button></form>%s'
            '<div class="table-wrap"><table><thead><tr><th>UID</th><th>卡牌 ID</th><th>名称</th><th class="num">等级</th><th class="num">经验</th></tr></thead><tbody>%s</tbody></table></div>%s</section>') % (
                _fmt(len(cards)), _csrf_input(), _hidden(uin=uin), note, rows, pager)


def _render_progress(player, uin):
    with _connection() as con:
        td_count = con.execute("SELECT COUNT(*) FROM td_passed WHERE uin=?", (uin,)).fetchone()[0]
        td_stars = con.execute("SELECT COALESCE(SUM(star),0) FROM td_passed WHERE uin=?", (uin,)).fetchone()[0]
    return ('<section class="panel"><div class="panel-head"><div><h2>关卡进度（快速通关）</h2><p class="sub">按客户端关卡表补齐此前的关卡、星级、门和奖励植物。</p></div>'
            '<dl class="kv"><dt>当前记录</dt><dd><b>%s-%s-%s</b></dd><dt>已通关</dt><dd>%s 关 · %s 星</dd></dl></div>'
            '<form method="post" action="/player/progress" class="toolbar">%s%s<div class="field xs"><label for="pg-stage">章节</label><input id="pg-stage" name="stage" type="number" min="1" step="1" value="%s" required></div>'
            '<div class="field xs"><label for="pg-level">关卡</label><input id="pg-level" name="level" type="number" min="1" step="1" value="%s" required></div>'
            '<div class="field sm"><label for="pg-sub">小关（留空 = 整关）</label><input id="pg-sub" name="sub" type="number" min="1" step="1"></div>'
            '<button type="submit" class="primary">通关至此</button></form></section>') % (
                esc(player["td_stage"]), esc(player["td_level"]), esc(player["td_sub"]), _fmt(td_count), _fmt(td_stars),
                _csrf_input(), _hidden(uin=uin), esc(player["td_stage"]), esc(player["td_level"]))


def _render_vip(player, uin):
    flag = int(player.get("diamond_flag", 0) or 0)
    return ('<section class="panel"><div class="panel-head"><div><h2>钻石特权（钻石卡）</h2><p class="sub">登录时通过会员信息同步给客户端；0 表示未开通该类型。VIP 等级与塔防特权在“基础属性”中修改。</p></div></div>'
            '<form method="post" action="/player/diamond">%s%s<div class="form-grid"><div class="field"><label for="dm-flag">显示钻石卡</label><select id="dm-flag" name="flag"><option value="0"%s>关闭</option><option value="1"%s>开启</option></select></div>'
            '<div class="field"><label for="dm-blue">蓝钻等级</label><input id="dm-blue" name="blue" type="number" min="0" max="99" step="1" value="%s" required></div>'
            '<div class="field"><label for="dm-yellow">黄钻等级</label><input id="dm-yellow" name="yellow" type="number" min="0" max="99" step="1" value="%s" required></div>'
            '<div class="field"><label for="dm-xinyue">心悦等级</label><input id="dm-xinyue" name="xinyue" type="number" min="0" max="99" step="1" value="%s" required></div></div>'
            '<div class="form-actions"><button type="submit" class="primary">保存钻石卡</button></div></form></section>') % (
                _csrf_input() + _snapshot_input(player, ["diamond_flag", "diamond_blue_lvl", "diamond_yellow_lvl", "diamond_xinyue_lvl"]),
                _hidden(uin=uin), "" if flag else " selected", " selected" if flag else "",
                int(player.get("diamond_blue_lvl", 0) or 0), int(player.get("diamond_yellow_lvl", 0) or 0),
                int(player.get("diamond_xinyue_lvl", 0) or 0))


def _render_friends(player, uin, query):
    friends = store.get_friends(uin)
    page, pages, offset, page_note = _page_slice(len(friends), _first(query, "fpage"), FRIEND_PAGE_SIZE)
    rows = "".join(
        '<tr><td class="mono">%s</td><td>%s</td><td>%s %s · Lv.%s</td><td class="num">%s / %s / %s</td>'
        '<td class="right"><form method="post" action="/player/friend">%s%s<button class="danger sm" type="submit">移除</button></form></td></tr>' % (
            f["friend_role_id"], esc(f["friend_name"]), f["help_plant_id"], esc(PLANT_NAMES.get(f["help_plant_id"], "未知植物")),
            f["help_plant_level"], f["diamond_blue_lvl"], f["diamond_yellow_lvl"], f["diamond_xinyue_lvl"],
            _csrf_input(), _hidden(uin=uin, operation="remove", friend_role_id=f["friend_role_id"], fpage=page if page > 1 else ""))
        for f in friends[offset:offset + FRIEND_PAGE_SIZE])
    rows = rows or '<tr><td colspan="5" class="empty"><b>暂无好友</b>使用上方表单添加。</td></tr>'
    options = "".join('<option value="%s">%s %s</option>' % (pid, pid, esc(name)) for pid, name in sorted(PLANT_NAMES.items()))
    pager = _pager(len(friends), page, pages, lambda n: _player_url(uin, "friends", fpage=n if n > 1 else ""), unit="位好友",
                   jump_action="/player", jump_name="fpage", jump_params=(("uin", uin), ("section", "friends")))
    note = '<div class="notice info">%s</div>' % esc(page_note) if page_note else ""
    return ('<section class="panel"><div class="panel-head"><div><h2>添加 / 更新好友</h2><p class="sub">角色 ID 已存在时会更新其名称与好友卡。</p></div></div>'
            '<form method="post" action="/player/friend">%s%s<div class="form-section"><div class="form-grid">'
            '<div class="field"><label for="fr-id">好友角色 ID</label><input id="fr-id" class="mono" name="friend_role_id" type="number" min="1" step="1" required></div>'
            '<div class="field"><label for="fr-name">好友名称</label><input id="fr-name" name="friend_name" maxlength="24" required></div>'
            '<div class="field"><label for="fr-plant">好友卡植物 ID</label><input id="fr-plant" class="mono" name="plant_id" type="number" min="1" step="1" list="friend-plant-ids" required><datalist id="friend-plant-ids">%s</datalist></div>'
            '<div class="field"><label for="fr-level">植物等级</label><input id="fr-level" name="plant_level" type="number" min="1" max="999" step="1" value="1" required></div></div></div>'
            '<div class="form-section"><h3>好友钻石卡</h3><div class="form-grid"><div class="field"><label for="fr-flag">显示</label><select id="fr-flag" name="diamond_flag"><option value="1">开启</option><option value="0">关闭</option></select></div>'
            '<div class="field"><label for="fr-blue">蓝钻</label><input id="fr-blue" name="blue" type="number" min="0" max="99" step="1" value="0" required></div>'
            '<div class="field"><label for="fr-yellow">黄钻</label><input id="fr-yellow" name="yellow" type="number" min="0" max="99" step="1" value="0" required></div>'
            '<div class="field"><label for="fr-xinyue">心悦</label><input id="fr-xinyue" name="xinyue" type="number" min="0" max="99" step="1" value="0" required></div></div></div>'
            '<div class="form-actions"><button type="submit" class="primary">添加 / 更新</button></div></form></section>'
            '<section class="panel"><div class="panel-head"><h2>好友列表</h2><span class="chip">%s 位</span></div>%s'
            '<div class="table-wrap"><table><thead><tr><th>角色 ID</th><th>名称</th><th>好友卡</th><th class="num">蓝 / 黄 / 心悦</th><th></th></tr></thead><tbody>%s</tbody></table></div>%s</section>') % (
                _csrf_input(), _hidden(uin=uin, operation="add"), options, _fmt(len(friends)), note, rows, pager)


def _render_reset(player, uin):
    return ('<section class="panel danger-zone"><div class="panel-head"><div><h2>重置账号</h2><p class="sub">会自动备份整个数据库，然后删除该 UIN 在所有玩法表中的记录，并重建一份新手存档。会长身份与空公会按账号生命周期规则处理。</p></div></div>'
            '<form method="post" action="/player/reset" class="stack" style="max-width:420px">%s%s<div class="field"><label for="reset-uin">输入 <b class="mono">%s</b> 确认</label>'
            '<input id="reset-uin" name="confirm_uin" class="mono" autocomplete="off" required inputmode="numeric"></div>'
            '<label class="sub"><input name="ack" type="checkbox" value="yes" required> 我知道该账号会回到初始状态（备份可在存档目录找回）</label>'
            '<div><button class="danger" type="submit">备份并重置</button></div></form></section>') % (_csrf_input(), _hidden(uin=uin), uin)


def _render_result(result, message="", kind=""):
    """Per-line batch outcome. It also carries the redirect message so the
    page shows a single status block instead of a flash plus a summary."""
    ok = bool(result.get("ok"))
    rows = []
    for line in result.get("lines", []):
        css, label = GRANT_STATUS_LABELS.get(line.get("status"), ("status-muted", "–"))
        rows.append('<tr><td><div class="item-name">%s</div><div class="sub mono">ID %s · %s</div></td><td class="num">%s</td>'
                    '<td class="num">%s</td><td class="num">%s</td><td><span class="%s">%s</span>%s</td></tr>' % (
                        esc(line.get("name")), esc(line.get("id")), esc(line.get("kind")), _fmt(line.get("qty", 0)),
                        "—" if line.get("before") is None else _fmt(line["before"]),
                        "—" if line.get("after") is None else _fmt(line["after"]), css, label,
                        '<div class="sub">%s</div>' % esc(line["reason"]) if line.get("reason") else ""))
    stamp = _dt.datetime.fromtimestamp(int(result.get("time", 0) or 0)).strftime("%H:%M:%S") if result.get("time") else ""
    kind = kind if kind in ("ok", "error", "warn", "info") else ("ok" if ok else "error")
    headline = message or result.get("message") or ("全部发放成功。" if ok else "整批未写入。")
    return ('<section class="panel result-panel %s" aria-labelledby="result-title"><div class="notice %s" role="%s" id="result-title">%s</div>'
            '<details class="more"%s><summary>逐项结果（%s 项）· 批次 <span class="mono">%s</span> · %s</summary>'
            '<p class="sub" style="margin-bottom:8px">“当前库存”是写入后重新读取的实际数量。</p>'
            '<div class="table-wrap"><table class="result-table"><thead><tr><th>道具</th><th class="num">数量</th><th class="num">发放前</th>'
            '<th class="num">当前库存</th><th>结果</th></tr></thead><tbody>%s</tbody></table></div>'
            '<div class="form-actions"><button type="submit" class="sm" name="nav" value="reload:%s" formnovalidate>把本批次重新加入清单</button></div>'
            '</details></section>') % (
                "ok" if ok else "bad", kind, "alert" if kind == "error" else "status", esc(headline),
                "" if ok and len(rows) > 8 else " open", len(rows), esc(result.get("id", "")), esc(stamp),
                "".join(rows), esc(result.get("id", "")))


def _grant_state_for_render(uin):
    """Load the cart; persist a freshly minted grant token so the first submit matches."""
    with GRANT_STATE_LOCK:
        state = _load_grant_state()
        raw = state["carts"].get(str(int(uin)))
        had_token = isinstance(raw, dict) and isinstance(raw.get("token"), str) and len(raw["token"]) >= 16
        cart = _cart(state, uin)
        if not had_token:
            try:
                _save_grant_state(state)
            except ValidationError:
                pass  # the next POST reports the storage problem
        presets = [preset for preset in state["presets"] if isinstance(preset, dict)]
    return cart, presets


def _render_items(uin, player, query, flash=("", "")):
    search = (_first(query, "q") or "").strip()[:60]
    item_kind = _first(query, "item_kind").strip()
    item_kind = item_kind if item_kind in ITEM_KIND_LABELS else ""
    per = int(_first(query, "per")) if _first(query, "per").isdigit() and int(_first(query, "per")) in CATALOG_PAGE_SIZES else 50
    matches = _catalog_matches(search, item_kind)
    page, pages, offset, page_note = _page_slice(len(matches), _first(query, "page"), per)
    visible = matches[offset:offset + per]
    running = _game_running()
    cart, presets = _grant_state_for_render(uin)
    cart_map = {iid: quantity for iid, quantity in cart["items"]}
    count = _owned_counter(uin)
    rows, shown, was = [], [], []
    for item in visible:
        primary = item["id"]
        shown.append(str(primary))
        selected = next((iid for iid in item["ids"] if iid in cart_map), None)
        if selected is not None:
            was.append(str(primary))
        limit = _grant_limit(item)
        meta = ["ID %s" % "/".join(str(iid) for iid in item["ids"]), "单次≤%s" % _short_num(limit)]
        if item["kind"] in ("card", "equipment", "reagent"):
            meta.append("每件独立实例")
        elif item["kind"] == "currency":
            meta.append("写入账号“%s”" % CURRENCY_FIELD_LABELS.get(CURRENCY_GRANTS[primary][0], CURRENCY_GRANTS[primary][1]))
        description = " · ".join(part for part in (item["desc"], "来源：" + item["gain_way"] if item["gain_way"] else "") if part)
        owned = _fmt(count(item))
        id_choice = ""
        if len(item["ids"]) > 1:
            owned = "<br>".join("%s：%s" % (iid, _fmt(count(item, iid))) for iid in item["ids"])
            if selected is None:
                id_choice = '<select name="id_%s" aria-label="选择具体 ID" class="id-choice">%s</select>' % (
                    primary, "".join('<option value="%s"%s>ID %s</option>' % (iid, " selected" if search == str(iid) else "", iid)
                                     for iid in item["ids"]))
        if selected is not None:
            quantity_cell = '<span class="sel-qty" title="在右侧清单中修改数量">已选 ×%s</span>%s' % (
                _fmt(cart_map[selected]), '<div class="sub mono">ID %s</div>' % selected if len(item["ids"]) > 1 else "")
        else:
            quantity_cell = ('<input class="qty" name="q_%s" type="number" min="1" max="%s" step="1" value="1" inputmode="numeric" '
                             'title="单次上限 %s" aria-label="%s 的数量（1–%s）">%s') % (
                                 primary, limit, _fmt(limit), esc(item["name"]), _fmt(limit), id_choice)
        rows.append('<tr><td class="pick-cell"><input type="checkbox" class="pick" name="pick" value="%s"%s aria-label="选择 %s"></td>'
                    '<td><div class="item-line"><span class="item-name">%s</span><span class="chip kind-%s">%s</span></div>'
                    '<div class="item-meta" title="%s"><span class="mono">%s</span>%s</div></td>'
                    '<td class="num">%s</td><td>%s</td><td class="right"><button type="submit" class="sm" name="nav" value="grant:%s"%s '
                    'title="不经过清单，立即单独发放这一项" aria-label="立即发放 %s">发放</button></td></tr>' % (
                        primary, " checked" if selected is not None else "", esc(item["name"]),
                        esc(item["name"]), esc(item["kind"]), esc(ITEM_KIND_LABELS[item["kind"]]),
                        esc(description or item["name"]), esc(" · ".join(meta)),
                        (" · " + esc(description)) if description else "", owned,
                        quantity_cell, primary, " disabled" if running else "", esc(item["name"])))
    if not rows:
        rows.append('<tr><td colspan="5" class="empty"><b>没有找到可安全发放的道具</b>%s</td></tr>' % (
            "换个关键词，或把类型改为“全部类型”。" if search or item_kind else "客户端道具表为空。"))
    kind_options = '<option value="">全部类型</option>' + "".join(
        '<option value="%s"%s>%s</option>' % (key, " selected" if item_kind == key else "", esc(label))
        for key, label in ITEM_KIND_LABELS.items())
    per_options = "".join('<option value="%s"%s>%s 条</option>' % (size, " selected" if size == per else "", size)
                          for size in CATALOG_PAGE_SIZES)
    cart_rows = []
    for iid, quantity in cart["items"]:
        item = ITEM_CATALOG_BY_ID[iid]
        cart_rows.append('<li class="cart-row"><div class="cart-name"><div class="item-name" title="%s">%s</div><div class="sub mono">ID %s · 已有 %s</div></div>'
                         '<input name="c_%s" type="number" min="1" max="%s" step="1" value="%s" inputmode="numeric" title="单次上限 %s" aria-label="%s 的发放数量">'
                         '<button type="submit" class="sm ghost icon" name="nav" value="remove:%s" formnovalidate title="移除" aria-label="移除 %s">×</button></li>' % (
                             esc(item["name"]), esc(item["name"]), iid, _fmt(count(item, iid)),
                             iid, _grant_limit(item), quantity, _fmt(_grant_limit(item)), esc(item["name"]), iid, esc(item["name"])))
    name = str(player.get("name") or "未命名角色")
    target = ('<p class="cart-target">发放给 <b>%s</b> <span class="mono">UIN %s</span></p>' % (esc(name), uin))
    if cart_rows:
        cart_body = ('%s<ol class="cart-list">%s</ol><div class="cart-actions">'
                     '<button type="submit" class="primary block" name="nav" value="grant_all"%s>发放全部 %s 项</button>'
                     '<div class="row tight"><button type="submit" class="sm" name="nav" value="save">保存数量</button>'
                     '<button type="submit" class="sm ghost" name="nav" value="clear" formnovalidate>清空清单</button></div>'
                     '<p class="hint">整批一次事务：任一项失败会全部回滚，不会只发出一部分。</p></div>') % (
                         target, "".join(cart_rows), " disabled" if running else "", len(cart["items"]))
    else:
        cart_body = target + '<div class="empty compact"><b>清单为空</b>勾选左侧道具或改数量即可加入；翻页、搜索时会自动保留。</div>'
        if cart.get("undo"):
            cart_body += '<button type="submit" class="sm block" name="nav" value="undo" formnovalidate>撤销清空（恢复 %s 项）</button>' % len(cart["undo"])
    preset_options = "".join('<option value="%s">%s（%s 项）</option>' % (esc(preset.get("id")), esc(preset.get("name")), len(preset.get("items") or []))
                             for preset in presets)
    presets_html = ('<details class="more presets"%s><summary>常用组合（%s）</summary><div class="stack" style="gap:8px">%s'
                    '<div class="row tight"><div class="field"><label for="preset-name">把当前清单保存为组合</label><input id="preset-name" name="preset_name" maxlength="20" placeholder="例如：新号礼包"%s></div>'
                    '<button type="submit" class="sm" name="nav" value="preset_save" formnovalidate%s>保存</button></div></div></details>') % (
                        " open" if presets and not cart_rows else "", len(presets),
                        ('<div class="row tight"><div class="field"><label for="preset-id">已保存的组合（可用于任意账号）</label><select id="preset-id" name="preset_id">%s</select></div>'
                         '<button type="submit" class="sm" name="nav" value="preset_load" formnovalidate>载入</button>'
                         '<button type="submit" class="sm ghost" name="nav" value="preset_delete" formnovalidate>删除</button></div>' % preset_options)
                        if presets else '<p class="sub">常发的一组道具可以保存下来，下次一键载入。</p>',
                        "" if cart_rows else " disabled", "" if cart_rows else " disabled")
    result = _find_result(uin, _first(query, "batch"))
    running_note = ('<div class="notice warn">游戏正在运行：可以继续挑选道具到清单，停止游戏后再发放（服务端也会拒绝运行中的写入）。</div>'
                    if running else "")
    note = '<div class="notice info">%s</div>' % esc(page_note) if page_note else ""
    result_html = _render_result(result, *flash) if result else ""
    return """%s<form method="post" action="/items/selection" data-while-running="1">%s%s
<button type="submit" name="nav" value="search" class="sr-only" tabindex="-1" aria-hidden="true" formnovalidate>搜索</button>%s
<div class="grant-layout"><section class="panel catalog" aria-labelledby="catalog-title"><div class="panel-head"><div><h2 id="catalog-title">道具库</h2>
<p class="sub">勾选道具（或直接改数量）即加入右侧清单；翻页、搜索、筛选前会自动保存本页的选择。</p></div></div>
<div class="toolbar" role="search"><div class="field grow"><label for="catalog-q">搜索名称 / ID / 描述（空格分隔多个词）</label>
<input id="catalog-q" name="q" value="%s" placeholder="例如：塔防钥匙、4001、经验豆" autocomplete="off" type="search"></div>
<div class="field sm"><label for="catalog-kind">发放类型</label><select id="catalog-kind" name="item_kind">%s</select></div>
<div class="field xs"><label for="catalog-per">每页</label><select id="catalog-per" name="per">%s</select></div>
<button type="submit" name="nav" value="search" formnovalidate>搜索</button>%s</div>
<div class="table-wrap"><table class="catalog-table"><thead><tr><th class="pick-cell"><span class="sr-only">选择</span></th><th>道具</th><th class="num">已有</th><th>数量</th><th><span class="sr-only">单项发放</span></th></tr></thead>
<tbody>%s</tbody></table></div>%s
<div class="catalog-foot"><span class="sub">本页已勾选 <b class="picked-live num"></b> 项 · 清单 <b class="num">%s</b> 项</span>
<button type="submit" name="nav" value="add">保存选择到清单</button></div></section>
<aside class="panel cart" aria-labelledby="cart-title"><div class="cart-head"><h2 id="cart-title">已选清单</h2><span class="chip%s">%s 项</span></div>
%s%s</aside></div></form>""" % (
        running_note, _csrf_input(),
        _hidden(uin=uin, cur_q=search, cur_kind=item_kind, cur_per=per, cur_page=page, shown=",".join(shown),
                was=",".join(was), cart_rev=cart["rev"], grant_token=cart["token"]),
        result_html + note, esc(search), kind_options, per_options,
        '<button type="submit" class="ghost" name="nav" value="reset_filters" formnovalidate>清除条件</button>' if search or item_kind else "",
        "".join(rows), _pager_buttons(len(matches), page, pages, unit="项"), len(cart["items"]),
        " ok" if cart["items"] else "", len(cart["items"]), cart_body, presets_html)
# ---------------------------------------------------------------------------
# Selection form processing (道具发放)
# ---------------------------------------------------------------------------
def _csv_ints(raw):
    result = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            result.append(int(part))
    return result


def _apply_selection_changes(cart, form, skip=None):
    """Merge one catalog page and the cart's quantity fields into the selection.

    Rows rendered as selected (``was``) are removed when unticked. Unselected
    rows are added when ticked, or when their quantity was changed from the
    default 1 (counted as ``auto``). Invalid quantities are reported and leave
    the previous selection unchanged.
    """
    entries = {iid: quantity for iid, quantity in cart["items"]}
    picks = set(form.get("pick", []))
    was = set(_csv_ints(_first(form, "was")))
    stats = {"added": 0, "removed": 0, "auto": 0, "updated": 0, "errors": []}
    for primary in _csv_ints(_first(form, "shown"))[:max(CATALOG_PAGE_SIZES)]:
        display = CATALOG_DISPLAY_BY_ID.get(primary)
        if display is None:
            continue
        checked = str(primary) in picks
        if primary == skip:
            continue  # the row being granted on its own keeps its own quantity
        if primary in was:
            if not checked:
                for iid in display["ids"]:
                    if entries.pop(iid, None) is not None:
                        stats["removed"] += 1
            continue
        raw = _first(form, "q_%s" % primary).strip()
        modified = raw not in ("", "1")
        if not checked and not modified:
            continue
        exact = primary
        if len(display["ids"]) > 1:
            choice = _first(form, "id_%s" % primary)
            if choice.isdigit() and int(choice) in display["ids"]:
                exact = int(choice)
        item = ITEM_CATALOG_BY_ID[exact]
        quantity, error = _parse_quantity(raw or "1", item)
        if error:
            stats["errors"].append("%s（ID %s）：%s" % (item["name"], exact, error))
            continue
        if exact not in entries and len(entries) >= MAX_CART_ITEMS:
            stats["errors"].append("%s 未加入：已选清单最多 %s 项" % (item["name"], MAX_CART_ITEMS))
            continue
        if exact in entries:
            stats["updated"] += entries[exact] != quantity
        else:
            stats["added"] += 1
            stats["auto"] += not checked
        entries[exact] = quantity
    for iid in list(entries):
        key = "c_%s" % iid
        if key not in form:
            continue
        quantity, error = _parse_quantity(_first(form, key), ITEM_CATALOG_BY_ID[iid])
        if error:
            stats["errors"].append("%s（ID %s）：%s，保留原数量 %s" % (
                ITEM_CATALOG_BY_ID[iid]["name"], iid, error, _fmt(entries[iid])))
            continue
        if entries[iid] != quantity:
            entries[iid] = quantity
            stats["updated"] += 1
    cart["items"] = [[iid, quantity] for iid, quantity in entries.items()]
    return stats


def _selection_message(stats):
    parts = []
    if stats["added"]:
        parts.append("已加入 %s 项" % stats["added"] + ("（其中 %s 项因修改了数量自动勾选）" % stats["auto"] if stats["auto"] else ""))
    if stats["removed"]:
        parts.append("已移除 %s 项" % stats["removed"])
    if stats["updated"]:
        parts.append("已更新 %s 项数量" % stats["updated"])
    if stats["errors"]:
        parts.append("以下输入无效未保存：" + "；".join(stats["errors"][:6]) + ("等" if len(stats["errors"]) > 6 else ""))
    return "；".join(parts)


class AdminHandler(http.server.BaseHTTPRequestHandler):
    server_version = "PvZLocalAdmin/2.0"

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
        nonce, self._script_nonce = getattr(self, "_script_nonce", None), None
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; " +
            ("script-src 'nonce-%s'; " % nonce if nonce and embedded else "") +
            "form-action 'self'; base-uri 'none'; frame-ancestors " +
            (_launcher_origin() if embedded else "'none'"),
        )

    def _send(self, status, body, content_type="text/html; charset=utf-8"):
        self._script_nonce = None
        if isinstance(body, str) and content_type.startswith("text/html"):
            query = parse_qs(urlsplit(self.path).query)
            classes = []
            if self._is_embedded():
                classes.append("embedded")
            theme = query.get("theme", [""])[0]
            if theme in ("dark", "light"):
                classes.append(theme + "-theme")
            body = body.replace("<body>", '<body class="%s">' % " ".join(classes), 1)
            if self._is_embedded():
                body = re.sub(r'(href|action)="(/[^\"]*)"',
                              lambda match: '%s="%s"' % (match[1], esc(self._embedded_destination(html.unescape(match[2])))), body)

                def embedded_form(match):
                    tag = match[1]
                    action = re.search(r'\baction="([^\"]*)"', tag)
                    context = parse_qs(urlsplit(html.unescape(action[1])).query) if action else query
                    fields = [("embedded", "1")]
                    form_theme = context.get("theme", [""])[0]
                    section = context.get("section", [""])[0]
                    if form_theme in ("light", "dark"):
                        fields.append(("theme", form_theme))
                    if section in EMBED_SECTIONS:
                        fields.append(("section", section))
                    # GET submission replaces the action's query, so the display
                    # context must also be successful controls.
                    return tag + "".join('<input type="hidden" name="%s" value="%s">' %
                                         (name, esc(value)) for name, value in fields)
                body = re.sub(r"(<form\b[^>]*>)", embedded_form, body)
                # Tell the launcher which page the frame shows so its toolbar
                # (account, reload, open in browser) follows in-frame navigation.
                # Only this nonce'd one-liner may run; the origin is the validated
                # loopback launcher origin, so the location goes nowhere else.
                self._script_nonce = secrets.token_urlsafe(16)
                script = ('<script nonce="%s">try{parent.postMessage({type:"pvz-gm-location",'
                          'href:location.pathname+location.search},%s)}catch(e){}</script>'
                          % (self._script_nonce, json.dumps(_launcher_origin())))
                body = body.replace("</body>", script + "</body>", 1) if "</body>" in body else body + script
            if _game_running():
                # Rendering matches the server-side write gate: write forms stay
                # readable but cannot be submitted. Selection forms that only
                # touch gm_grant_state.json stay usable; their grant buttons are
                # rendered disabled and rejected by the server while running.
                def lock(match):
                    if "data-while-running" in match[1]:
                        return match[0]
                    return match[1] + '<fieldset disabled>' + match[2] + "</fieldset></form>"
                body = re.sub(r'(<form\b[^>]*method="post"[^>]*>)(.*?)</form>', lock, body, flags=re.S)
                body = body.replace('<main class="wrap main" id="main">',
                                    '<main class="wrap main" id="main"><div class="notice warn" role="status"><b>游戏正在运行，GM 修改已锁定。</b>'
                                    '停止游戏后刷新即可编辑；当前显示的是只读数据。</div>', 1)
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
        self.send_header("Content-Length", "0")
        self._security_headers()
        self.end_headers()

    def _request_allowed(self):
        host = self.headers.get("Host", "")
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        if (hostname or "").lower() not in ("127.0.0.1", "localhost", "::1"):
            self._send(421, "非法 Host", "text/plain; charset=utf-8")
            return False
        return True

    def _post_form(self, max_fields=DEFAULT_MAX_FIELDS):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValidationError("无效请求长度。")
        if length <= 0 or length > MAX_POST_BYTES:
            raise ValidationError("请求内容过大或为空。")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            raise ValidationError("仅支持标准表单提交。")
        # Origin is only a browser hint here (WebView may send ``null``); the
        # boundary is loopback binding + Host allow-list + the random CSRF token.
        raw = self.rfile.read(length).decode("utf-8", "strict")
        try:
            form = parse_qs(raw, keep_blank_values=True, max_num_fields=max_fields)
        except ValueError:
            raise ValidationError("表单字段过多，请减少本页显示条数后重试。")
        token = (form.get("csrf", [""])[0] or "")
        if not secrets.compare_digest(token, CSRF_TOKEN):
            raise ValidationError("页面已过期，请刷新后重试。")
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
                self._send(404, _page("未找到", '<section class="panel"><h1>页面不存在</h1><p class="sub" style="margin:8px 0 12px">链接可能已过期。</p><a class="btn" href="/">返回账号列表</a></section>'))
        except ValidationError as exc:
            self._send(400, _page("输入错误", '<section class="panel"><h1>无法打开该页面</h1><p class="sub" style="margin:8px 0 12px">%s</p><a class="btn" href="/">返回账号列表</a></section>' % esc(exc)))
        except Exception:
            traceback.print_exc()
            self._send(500, _page("后台错误", '<section class="panel"><h1>后台出错</h1><p class="sub" style="margin-top:8px">请查看启动器“服务与日志”。</p></section>'))
        finally:
            store.close_thread_db()

    # Routes whose handler runs inside one per-account write transaction.
    TX_ROUTES = frozenset(("/player/update", "/player/item", "/player/item-adjust", "/player/plant",
                           "/player/progress", "/player/cards", "/player/diamond", "/player/friend"))
    # Routes that do not write players.db (or gate their own writes).
    UNGATED_ROUTES = frozenset(("/player/create", "/backup", "/items/selection"))
    SECTION_ROUTES = {"/player/update": "attributes", "/player/plant": "plants", "/player/progress": "progress",
                      "/player/cards": "cards", "/player/diamond": "vip", "/player/friend": "friends",
                      "/player/reset": "reset"}

    def _handlers(self):
        return {
            "/player/create": self._create_player,
            "/player/new": self._new_player,
            "/player/update": self._update_player,
            "/player/item": self._edit_item,
            "/player/item-adjust": self._adjust_item,
            "/player/repair-puzzles": self._repair_puzzles,
            "/player/grant-item": self._grant_item,
            "/items/selection": self._items_selection,
            "/player/plant": self._edit_plant,
            "/player/progress": self._edit_progress,
            "/player/cards": self._edit_cards,
            "/player/diamond": self._edit_diamond,
            "/player/friend": self._edit_friend,
            "/player/reset": self._reset_account,
            "/backup": self._manual_backup,
        }

    def _error_target(self, path, form):
        try:
            uin = int(_first(form, "uin"))
        except (TypeError, ValueError):
            return "/"
        if path in ("/player/create", "/player/new", "/backup") or uin <= 0:
            return "/"
        if path == "/player/grant-item":
            return _catalog_url(uin, form)
        if path == "/items/selection":
            return _url("/items", uin=uin, q=_first(form, "cur_q"), item_kind=_first(form, "cur_kind"),
                        per=_first(form, "cur_per") if _first(form, "cur_per") != "50" else "",
                        page=_first(form, "cur_page") if _first(form, "cur_page") not in ("", "1") else "")
        if path in ("/player/item", "/player/item-adjust", "/player/repair-puzzles"):
            return _player_url(uin, "inventory", **_inventory_context(form))
        return _player_url(uin, self.SECTION_ROUTES.get(path, "attributes"),
                           fpage=_first(form, "fpage") if path == "/player/friend" else "")

    def do_POST(self):
        if not self._request_allowed():
            return
        path = urlsplit(self.path).path
        handler = self._handlers().get(path)
        if not handler:
            self._send(404, "未找到", "text/plain; charset=utf-8")
            return
        form = {}
        try:
            form = self._post_form(SELECTION_MAX_FIELDS if path == "/items/selection" else DEFAULT_MAX_FIELDS)
            with WRITE_LOCK:
                if path not in self.UNGATED_ROUTES:
                    _require_game_stopped()
                if path in self.TX_ROUTES:
                    player = _require_player(_integer(form, "uin", 1, None, "UIN"))
                    with store.atomic_player(player):
                        result = handler(form)
                else:
                    result = handler(form)
        except ValidationError as exc:
            self._redirect(self._error_target(path, form), str(exc), "error")
            return
        except Exception:
            traceback.print_exc()
            self._send(500, _page("后台错误", '<section class="panel"><h1>操作失败</h1><p class="sub" style="margin-top:8px">事务已回滚，未写入不完整数据。请查看启动器“服务与日志”。</p></section>'))
            return
        finally:
            store.close_thread_db()
        # Respond only now: the write transaction above has committed.
        self._redirect(result.path, result.message, result.kind)

    # -- pages ---------------------------------------------------------------
    def _dashboard(self, query):
        message, kind = self._message(query)
        self._send(200, _page("账号列表", _render_dashboard(query), message, kind))

    def _player_page(self, uin, query):
        player = _require_player(uin)
        section = _first(query, "section") or "attributes"
        if section == "items":
            return self._items_page(uin, query)
        if section not in dict(PLAYER_TABS):
            section = "attributes"
        renderers = {
            "attributes": lambda: _render_attributes(player, uin),
            "inventory": lambda: _render_inventory(player, uin, query),
            "plants": lambda: _render_plants(player, uin),
            "cards": lambda: _render_cards(player, uin, query),
            "progress": lambda: _render_progress(player, uin),
            "vip": lambda: _render_vip(player, uin),
            "friends": lambda: _render_friends(player, uin, query),
            "reset": lambda: _render_reset(player, uin),
        }
        cart_items, _total = _cart_summary(uin)
        content = _account_bar(player, uin) + _tabs(uin, section, cart_items) + renderers[section]()
        message, kind = self._message(query)
        self._send(200, _page("%s · %s" % (player.get("name") or uin, dict(PLAYER_TABS)[section]), content, message, kind))

    def _items_page(self, uin, query):
        player = _require_player(uin)
        message, kind = self._message(query)
        has_result = _find_result(uin, _first(query, "batch")) is not None
        body = _render_items(uin, player, query, (message, kind) if has_result else ("", ""))
        cart_items, _total = _cart_summary(uin)
        content = _account_bar(player, uin) + _tabs(uin, "items", cart_items) + body
        if has_result:
            message = ""  # shown inside the result panel instead
        self._send(200, _page("道具发放 · %s" % (player.get("name") or uin), content, message, kind))

    # -- write handlers (return Redirect; do_POST responds after commit) --------
    def _create_player(self, form):
        uin = _integer(form, "uin", 1, 9_223_372_036_854_775_807, "UIN")
        _require_player(uin)
        return Redirect(_player_url(uin), "已打开现有存档。")

    def _new_player(self, form):
        uin = _integer(form, "uin", 1, 9_223_372_036_854_775_807, "UIN")
        try:
            store.create_new_player(uin)
        except ValueError as exc:
            raise ValidationError(str(exc))
        return Redirect(_player_url(uin), "已创建新账号 %s 和独立新手存档。启动器输入该编号即可进入游戏。" % uin)

    def _update_player(self, form):
        uin = _integer(form, "uin", 1, 9_223_372_036_854_775_807, "UIN")
        _require_player(uin)
        baseline = _baseline(form, uin)
        values = {}
        name = (form.get("name", [""])[0] or "").strip()
        if not name or len(name) > 24 or any(ord(ch) < 32 for ch in name):
            raise ValidationError("角色名需为 1–24 个可见字符。")
        if name != baseline.get("name"):
            from profile_editor import _text, NAME_MIN, NAME_MAX
            try:
                _text(name, "角色名", NAME_MAX, NAME_MIN)
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
        vip = _integer(form, "vip_level", 0, 3, "VIP 等级")
        privilege = _integer(form, "td_privilege", 0, 1, "塔防特权")
        values.update(vip_level=vip, td_privilege=privilege)
        count = _save_incremental(uin, values, baseline)
        return Redirect(_player_url(uin, "attributes"),
                        "已保存 %s 项基础属性，重新进入游戏后生效。" % count if count else "没有修改的属性，存档保持原样。",
                        "ok" if count else "info")

    def _edit_item(self, form):
        """Legacy endpoint: signed delta for an item ID (kept for old forms/bookmarks)."""
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        item_id = _integer(form, "item_id", 1, 99_999_999, "物品 ID")
        delta = _integer(form, "delta", -2_000_000_000, 2_000_000_000, "增减数量")
        current = store.get_item_count(uin, item_id)
        if delta < 0 and -delta > current:
            raise ValidationError("当前只有 %s 个，不能减少 %s 个；本次未修改。" % (_fmt(current), _fmt(-delta)))
        try:
            gm.validate_stack_delta(item_id, delta, current)
        except ValueError as exc:
            raise ValidationError(str(exc))
        new_count = store.add_item(uin, item_id, delta)
        if item_id == 43:  # 威望同时在玩家列中缓存一份，保持两者一致。
            store.save_player(uin, prestige=new_count)
        name, _detail = _inventory_item_details(item_id)
        return Redirect(_player_url(uin, "inventory"), "%s（ID %s）当前数量：%s。" % (name, item_id, _fmt(new_count)))

    def _adjust_item(self, form):
        """背包数量调整：增加 / 减少（相对）与 设为（目标数量）分开处理。"""
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        item_id = _integer(form, "item_id", 1, 99_999_999, "道具 ID")
        op = _first(form, "op")
        if op not in ("add", "sub", "set"):
            raise ValidationError("请选择“增加”“减少”或“设为”。")
        amount = _integer(form, "amount", 0, CURRENCY_LIMIT, "数量")
        current = store.get_item_count(uin, item_id)
        expected = _first(form, "expected").strip()
        if op in ("sub", "set") and _INTEGER_TEXT.fullmatch(expected) and int(expected) != current:
            raise ValidationError("数量已变化：页面显示 %s，当前为 %s。请核对后重试，本次未修改。" % (
                _fmt(expected), _fmt(current)))
        name, _detail = _inventory_item_details(item_id)
        back = _player_url(uin, "inventory", **_inventory_context(form))
        if op == "add":
            if amount <= 0:
                raise ValidationError("增加的数量必须大于 0。")
            delta = amount
        elif op == "sub":
            if amount <= 0:
                raise ValidationError("减少的数量必须大于 0。")
            if amount > current:
                raise ValidationError("%s 当前只有 %s 个，不能减少 %s 个；如需清空请用“设为 0”。" % (
                    name, _fmt(current), _fmt(amount)))
            delta = -amount
        else:
            delta = amount - current
            if delta == 0:
                return Redirect(back, "%s 已是 %s 个，未修改。" % (name, _fmt(current)), "info")
        try:
            gm.validate_stack_delta(item_id, delta, current)
        except ValueError as exc:
            raise ValidationError(str(exc))
        new_count = store.add_item(uin, item_id, delta)
        if new_count != current + delta:
            raise ValidationError("写入结果与预期不一致（预期 %s，实际 %s），已回滚。" % (_fmt(current + delta), _fmt(new_count)))
        if item_id == 43:
            store.save_player(uin, prestige=new_count)
        verb = {"add": "增加", "sub": "减少", "set": "设为"}[op]
        return Redirect(back, "%s（ID %s）%s：%s → %s%s。" % (
            name, item_id, verb, _fmt(current), _fmt(new_count), "（已从背包移除）" if new_count == 0 else ""))

    def _repair_puzzles(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        result = _repair_invalid_puzzles(uin)
        message = ("已整理 %s 种无效旧拼图；合法碎片数量保持原样。备份：%s。" %
                   (result["count"], os.path.basename(result["backup"]))) if result["count"] else "没有需要整理的无效旧拼图。"
        return Redirect(_player_url(uin, "inventory"), message)

    def _grant_item(self, form):
        """Legacy single-item endpoint; uses the same validated grant path as batches."""
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        item_id = _integer(form, "item_id", 1, 99_999_999, "道具 ID")
        item = ITEM_CATALOG_BY_ID.get(item_id)
        if not item:
            raise ValidationError("道具 ID %s 不存在，或属于无法安全发放的客户端展示项。" % item_id)
        quantity = _integer(form, "quantity", 1, _grant_limit(item), "发放数量")
        result = _execute_grants(uin, [(item_id, quantity)])
        with GRANT_STATE_LOCK:
            state = _load_grant_state()
            batch = _store_result(state, uin, result)
            _save_grant_state(state)
        return Redirect(_catalog_url(uin, form, batch=batch), result["message"], "ok" if result["ok"] else "error")

    def _items_selection(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        player = _require_player(uin)
        nav = _first(form, "nav") or "add"
        per_raw = _first(form, "per")
        current = {"q": _first(form, "cur_q").strip()[:60], "item_kind": _first(form, "cur_kind"),
                   "per": _first(form, "cur_per") or "50"}
        typed = {"q": _first(form, "q").strip()[:60],
                 "item_kind": _first(form, "item_kind") if _first(form, "item_kind") in ITEM_KIND_LABELS else "",
                 "per": per_raw if per_raw.isdigit() and int(per_raw) in CATALOG_PAGE_SIZES else current["per"]}
        if current["item_kind"] not in ITEM_KIND_LABELS:
            current["item_kind"] = ""
        filters_changed = typed != current
        page = _first(form, "cur_page")
        filters = current
        if nav == "search" and not filters_changed:
            # Enter in a quantity, jump or preset field submits the form's first
            # (hidden) search button: keep the page and honour that field.
            if _first(form, "jump").strip() and _first(form, "jump").strip() != page:
                nav = "jump"
            elif _first(form, "preset_name").strip():
                nav = "preset_save"
            else:
                nav = "save"
        if nav == "search" or (filters_changed and (nav.startswith("page:") or nav == "jump")):
            filters, page = typed, ""
        elif nav == "reset_filters":
            filters, page = dict(current, q="", item_kind=""), ""
        elif nav.startswith("page:"):
            page = nav.split(":", 1)[1]
        elif nav == "jump":
            page = _first(form, "jump").strip() or page

        def back(batch=""):
            return _url("/items", uin=uin, q=filters["q"], item_kind=filters["item_kind"],
                        per=filters["per"] if filters["per"] != "50" else "",
                        page=page if page not in ("", "1") else "", batch=batch)

        with GRANT_STATE_LOCK:
            state = _load_grant_state()
            cart = _cart(state, uin)
            granting = nav == "grant_all" or nav.startswith("grant:")
            if granting:
                token = _first(form, "grant_token")
                if not token or not secrets.compare_digest(token, cart["token"]):
                    history = state["results"].get(str(uin)) or []
                    last = history[0].get("id", "") if history and isinstance(history[0], dict) else ""
                    return Redirect(back(last), "这次发放请求已处理过或页面已过期：重复提交已忽略，没有再次写入。", "warn")
                if nav == "grant_all" and _first(form, "cart_rev") != str(cart["rev"]):
                    return Redirect(back(), "已选清单已在其他页面修改，请核对后再发放；本次未写入。", "error")
            before = [list(entry) for entry in cart["items"]]
            single = nav.split(":", 1)[1] if nav.startswith("grant:") else ""
            stats = _apply_selection_changes(cart, form, skip=int(single) if single.isdigit() else None)
            membership_changed = {iid for iid, _q in before} != {iid for iid, _q in cart["items"]}
            message, kind = _selection_message(stats), "error" if stats["errors"] else "ok"
            batch = ""
            if nav.startswith("remove:"):
                target = nav.split(":", 1)[1]
                remaining = [entry for entry in cart["items"] if str(entry[0]) != target]
                if len(remaining) != len(cart["items"]):
                    removed = ITEM_CATALOG_BY_ID.get(int(target), {}).get("name", target) if target.isdigit() else target
                    cart["items"] = remaining
                    message, kind = "已从清单移除：%s。" % removed, "ok"
            elif nav == "clear":
                if cart["items"]:
                    cart["undo"] = [list(entry) for entry in cart["items"]]
                    message, kind = "已清空 %s 项。可点击“撤销清空”恢复。" % len(cart["items"]), "ok"
                    cart["items"] = []
            elif nav == "undo" and cart.get("undo"):
                existing = {iid for iid, _q in cart["items"]}
                cart["items"] = (cart["items"] + [entry for entry in cart["undo"] if entry[0] not in existing])[:MAX_CART_ITEMS]
                cart["undo"] = None
                message, kind = "已恢复清空前的清单。", "ok"
            elif nav == "preset_save":
                name = _first(form, "preset_name").strip()[:20]
                if not cart["items"]:
                    message, kind = "清单为空，无法保存为组合。", "error"
                elif not name or any(ord(ch) < 32 for ch in name):
                    message, kind = "请填写 1–20 个字的组合名称。", "error"
                else:
                    presets = [preset for preset in state["presets"] if isinstance(preset, dict) and preset.get("name") != name]
                    if len(presets) >= MAX_PRESETS:
                        message, kind = "最多保存 %s 个组合，请先删除不用的组合。" % MAX_PRESETS, "error"
                    else:
                        presets.insert(0, {"id": secrets.token_hex(4), "name": name, "created": int(time.time()),
                                           "items": [list(entry) for entry in cart["items"]]})
                        state["presets"] = presets
                        message, kind = "已保存组合“%s”（%s 项），可在任意账号载入。" % (name, len(cart["items"])), "ok"
            elif nav in ("preset_load", "preset_delete"):
                preset_id = _first(form, "preset_id")
                preset = next((p for p in state["presets"] if isinstance(p, dict) and p.get("id") == preset_id), None)
                if preset is None:
                    message, kind = "组合不存在，可能已被删除。", "error"
                elif nav == "preset_delete":
                    state["presets"] = [p for p in state["presets"] if p is not preset]
                    message, kind = "已删除组合“%s”。" % preset.get("name"), "ok"
                else:
                    entries = {iid: q for iid, q in cart["items"]}
                    loaded = 0
                    for iid, quantity in _clean_entries(preset.get("items")):
                        if iid in entries or len(entries) < MAX_CART_ITEMS:
                            entries[iid] = min(quantity, _grant_limit(ITEM_CATALOG_BY_ID[iid]))
                            loaded += 1
                    cart["items"] = [[iid, q] for iid, q in entries.items()]
                    message, kind = "已载入组合“%s”的 %s 项（同名道具以组合数量为准），请核对后发放。" % (preset.get("name"), loaded), "ok"
            elif nav.startswith("reload:"):
                record = _find_result(uin, nav.split(":", 1)[1])
                if record is None:
                    message, kind = "该批次记录已过期。", "error"
                else:
                    entries = {iid: q for iid, q in cart["items"]}
                    for line in record.get("lines", []):
                        iid = int(line.get("id", 0) or 0)
                        if iid in ITEM_CATALOG_BY_ID and (iid in entries or len(entries) < MAX_CART_ITEMS):
                            entries[iid] = int(line.get("qty", 1) or 1)
                    cart["items"] = [[iid, q] for iid, q in entries.items()]
                    message, kind = "已把批次 %s 的道具重新加入清单，请核对后发放。" % record.get("id"), "ok"
            elif nav == "grant_all":
                if membership_changed:
                    message, kind = ("清单有变化（%s），请核对右侧清单后再点击发放；本次未写入。" %
                                     (_selection_message(stats) or "已更新")), "warn"
                elif stats["errors"]:
                    message, kind = "发放已取消：" + _selection_message(stats), "error"
                else:
                    cart["token"] = secrets.token_urlsafe(18)
                    _save_grant_state(state)  # consume the token before writing players.db
                    result = _execute_grants(uin, [tuple(entry) for entry in cart["items"]])
                    batch = _store_result(state, uin, result)
                    if result["ok"]:
                        cart["undo"] = None
                        cart["items"] = []
                    message, kind = result["message"], "ok" if result["ok"] else "error"
            elif nav.startswith("grant:"):
                target = nav.split(":", 1)[1]
                display = CATALOG_DISPLAY_BY_ID.get(int(target)) if target.isdigit() else None
                if display is None:
                    message, kind = "道具不存在或不可发放。", "error"
                else:
                    in_cart = next(((iid, q) for iid, q in cart["items"] if iid in display["ids"]), None)
                    if in_cart:
                        exact, quantity, error = in_cart[0], in_cart[1], ""
                    else:
                        exact = display["id"]
                        choice = _first(form, "id_%s" % display["id"])
                        if len(display["ids"]) > 1 and choice.isdigit() and int(choice) in display["ids"]:
                            exact = int(choice)
                        quantity, error = _parse_quantity(_first(form, "q_%s" % display["id"]) or "1", ITEM_CATALOG_BY_ID[exact])
                    if error:
                        message, kind = "%s：%s，未发放。" % (display["name"], error), "error"
                    else:
                        cart["token"] = secrets.token_urlsafe(18)
                        _save_grant_state(state)
                        result = _execute_grants(uin, [(exact, quantity)])
                        batch = _store_result(state, uin, result)
                        if result["ok"] and in_cart:
                            cart["items"] = [entry for entry in cart["items"] if entry[0] != exact]
                            result["message"] += "已从清单移除该项。"
                        message, kind = result["message"], "ok" if result["ok"] else "error"
            elif not message:
                message, kind = ("清单已保存（%s 项）。" % len(cart["items"]) if nav in ("add", "save") else ""), "ok"
            if cart["items"] != before:
                cart["rev"] += 1
            _save_grant_state(state)
        return Redirect(back(batch), message, kind)

    def _edit_plant(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        plant_id = _integer(form, "plant_id", 1, 99_999_999, "植物 ID")
        operation = (form.get("operation", [""])[0] or "").strip()
        if operation == "add":
            if plant_id not in PLANT_NAMES:
                raise ValidationError("植物 ID %s 不在客户端塔防植物表中。" % plant_id)
            store.unlock_plant(uin, plant_id)
            message = "已解锁 %s（%s）。" % (PLANT_NAMES[plant_id], plant_id)
        elif operation == "remove":
            if plant_id not in store.get_player_plants(uin):
                raise ValidationError("该账号没有塔防植物 %s，未修改。" % plant_id)
            store.remove_plant(uin, plant_id)
            message = "已移除塔防植物 %s（%s）。" % (PLANT_NAMES.get(plant_id, "未知植物"), plant_id)
        else:
            raise ValidationError("无效的植物操作。")
        return Redirect(_player_url(uin, "plants"), message)

    def _edit_progress(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        stage = _integer(form, "stage", 1, 999, "章节")
        level = _integer(form, "level", 1, 999, "关卡")
        raw_sub = (form.get("sub", [""])[0] or "").strip()
        candidates = sorted(key[2] for key in LEVEL_KEYS if key[:2] == (stage, level))
        if not candidates:
            raise ValidationError("客户端中不存在章节 %s 的第 %s 关。" % (stage, level))
        sub = max(candidates) if not raw_sub else _integer(form, "sub", 1, 999, "小关")
        if (stage, level, sub) not in LEVEL_KEYS:
            raise ValidationError("客户端中不存在关卡 %s-%s-%s。" % (stage, level, sub))
        gm.passto(uin, stage, level, sub)
        return Redirect(_player_url(uin, "progress"),
                        "已按游戏逻辑通关至 %s-%s-%s，并补齐门和奖励植物。" % (stage, level, sub))

    def _edit_cards(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        player = _require_player(uin)
        target = _integer(form, "level", 1, 999, "卡牌等级")
        raw_uid = (form.get("uid", [""])[0] or "").strip()
        uid = _integer(form, "uid", 1, None, "卡牌 UID") if raw_uid else None
        cards = [card for card in store.get_cards(uin) if uid is None or card[0] == uid]
        if not cards:
            raise ValidationError("没有找到可修改的植物卡。")
        # 复用服务器的经验曲线和账号等级上限，避免写出客户端无法解释的等级。
        import pvz_poc_server as game
        game.load_card_level_curve()
        exact = next((total for level, total, _next in game._CARD_LEVEL_CURVE if level == target), None)
        if exact is None:
            raise ValidationError("经验表中不存在卡牌等级 %s。" % target)
        changed = []
        for card_uid, card_id, _old_level, _old_exp in cards:
            new_level, kept_exp = game.card_level_from_exp(exact, card_id, int(player["level"]))
            store.set_card_level(uin, card_uid, new_level, kept_exp)
            changed.append(new_level)
        actual = min(changed) if changed else target
        suffix = "（受账号等级上限限制，实际为 %s 级）" % actual if actual != target else ""
        return Redirect(_player_url(uin, "cards"), "已修改 %s 张植物卡的等级%s。" % (len(changed), suffix))

    def _edit_diamond(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        baseline = _baseline(form, uin)
        flag = _integer(form, "flag", 0, 1, "钻石卡开关")
        blue = _integer(form, "blue", 0, 99, "蓝钻等级")
        yellow = _integer(form, "yellow", 0, 99, "黄钻等级")
        xinyue = _integer(form, "xinyue", 0, 99, "心悦等级")
        count = _save_incremental(uin, dict(diamond_flag=flag, diamond_blue_lvl=blue,
                                            diamond_yellow_lvl=yellow, diamond_xinyue_lvl=xinyue), baseline)
        if not count:
            return Redirect(_player_url(uin, "vip"), "钻石卡没有变化，存档保持原样。", "info")
        return Redirect(_player_url(uin, "vip"), "钻石卡已保存：显示=%s，蓝钻=%s，黄钻=%s，心悦=%s。重新进入游戏后生效。" %
                        ("开启" if flag else "关闭", blue, yellow, xinyue))

    def _edit_friend(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        operation = (form.get("operation", [""])[0] or "").strip()
        role_id = _integer(form, "friend_role_id", 1, 9_223_372_036_854_775_807, "好友角色 ID")
        back = _player_url(uin, "friends", fpage=_first(form, "fpage"))
        if operation == "remove":
            store.remove_friend(uin, role_id)
            return Redirect(back, "已移除好友 %s。" % role_id)
        if operation != "add":
            raise ValidationError("无效的好友操作。")
        if role_id == uin:
            raise ValidationError("不能把自己添加为好友。")
        name = (form.get("friend_name", [""])[0] or "").strip()
        if not name or len(name) > 24 or any(ord(ch) < 32 for ch in name):
            raise ValidationError("好友名称需为 1–24 个可见字符。")
        plant_id = _integer(form, "plant_id", 1, 99_999_999, "好友卡植物 ID")
        if plant_id not in PLANT_NAMES:
            raise ValidationError("植物 ID %s 不在客户端塔防植物表中。" % plant_id)
        plant_level = _integer(form, "plant_level", 1, 999, "好友卡等级")
        diamond_flag = _integer(form, "diamond_flag", 0, 1, "好友钻石卡开关")
        blue = _integer(form, "blue", 0, 99, "好友蓝钻")
        yellow = _integer(form, "yellow", 0, 99, "好友黄钻")
        xinyue = _integer(form, "xinyue", 0, 99, "好友心悦")
        store.add_friend(uin, role_id, name, plant_id, plant_level, diamond_flag, blue, yellow, xinyue)
        return Redirect(_player_url(uin, "friends"), "已添加 / 更新好友 %s（%s），重新进入游戏后生效。" % (name, role_id))

    def _reset_account(self, form):
        uin = _integer(form, "uin", 1, None, "UIN")
        _require_player(uin)
        confirm = (form.get("confirm_uin", [""])[0] or "").strip()
        acknowledged = (form.get("ack", [""])[0] or "") == "yes"
        if confirm != str(uin) or not acknowledged:
            raise ValidationError("确认 UIN 不匹配或未勾选确认，重置已取消。")
        backup, affected = _reset_player(uin)
        count = sum(rows for _table, rows in affected)
        return Redirect(_player_url(uin, "attributes"),
                        "账号已重置为新手档，共清理 %s 条记录。备份：%s" % (count, os.path.basename(backup)))

    def _manual_backup(self, form):
        destination = _backup_database("manual")
        return Redirect("/", "数据库已备份：%s" % os.path.basename(destination))

    def log_message(self, fmt, *args):
        # The launcher probes /healthz every few seconds while its service view
        # is open; logging each successful probe buried real requests.
        if (str(getattr(self, "path", "")).split("?", 1)[0] == "/healthz"
                and len(args) >= 2 and str(args[1]) == "200"):
            return
        sys.stdout.write("[admin] %s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()


class AdminServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # macOS 在刚关闭浏览器连接后会短暂保留 TIME_WAIT，允许立即重启。
    # Windows 的 SO_REUSEADDR 语义不同，保持严格模式以避免两个后台同时监听。
    allow_reuse_address = sys.platform != "win32"


def serve(port=ADMIN_PORT, db_path=None):
    if db_path:
        store.set_db_path(os.path.abspath(db_path))
    store.init_db()
    httpd = AdminServer((ADMIN_HOST, int(port)), AdminHandler)
    print("[admin] 本地 GM 后台: http://%s:%d" % (ADMIN_HOST, int(port)))
    print("[admin] 数据库: %s" % store.DB_PATH)
    sys.stdout.flush()
    with httpd:
        httpd.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description="PvZ OL 本地 GM 管理后台")
    parser.add_argument("--port", type=int, default=ADMIN_PORT, help="本机监听端口")
    parser.add_argument("--db", help="players.db 路径（主要用于测试）")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be in 1..65535")
    try:
        # Standalone (offline) GM started by the launcher reports request activity.
        from runtime_status import RuntimeStatus
        status = RuntimeStatus.from_env("admin")
        status.track_http(AdminHandler, "admin", ignore_paths=("/healthz",))
        status.start()
    except Exception as exc:  # diagnostic only; never block the GM
        print("[admin] activity tracking unavailable: %r" % exc)
    try:
        serve(args.port, args.db)
    except KeyboardInterrupt:
        print("\n[admin] 已停止")
    except OSError as exc:
        print("[admin] 无法监听 127.0.0.1:%s: %s" % (args.port, exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
