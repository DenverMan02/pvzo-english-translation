# -*- coding: utf-8 -*-
"""人机 (bots) for the local PvZ server.

WHY: the private server is single-player, so 排行榜/竞技场/联盟 are empty shells. This module
seeds a FIXED roster of bot accounts (high-numbered uins, never real logins) that:
  * own a six-card lineup on a quality/level gradient (~10k..300k+ power, so early bots are
    beatable while the top ones stay a real challenge — "人机的战力也不能弱"),
  * CREATE and JOIN guilds (two bot-led guilds appear in the guild list),
  * fill the arena / ranked-arena leaderboards and act as challengeable opponents.

The bots are REAL rows in players.db (uin 900001+) so every existing pipeline — formation DTOs,
guild member lists, power lookups — works unmodified. `ensure_bots` is idempotent and runs at
server start; bot arena scores live in the bots_state table and are nudged when a player beats
(or loses to) a bot.
"""
from __future__ import annotations

import os
import sqlite3
import time

_DB_PATH = None          # players.db (bots_state table lives here)
_GUILD_DB_PATH = None    # guild_local's sqlite (local_guilds / local_guild_members)
_API = {}

# uin, name, guild (None = guildless), guild_leader, arena score.
# 50 人机：竞技场 + 联盟都要有人，战力铺满 2,000 .. 3,000,000（见 _bot_target_power）。
# 榜单顺序由 score 决定，所以 score 随名次递增，最强的人机排在最前。
_BOTS = [
    # --- 花园守护者 ---
    {"uin": 900001, "name": "疯狂戴夫",     "guild": "花园守护者", "leader": True,  "score": 1000},
    {"uin": 900002, "name": "向日葵侠",     "guild": "花园守护者", "leader": False, "score": 1040},
    {"uin": 900003, "name": "豌豆先生",     "guild": "花园守护者", "leader": False, "score": 1080},
    {"uin": 900004, "name": "灯笼草大王",   "guild": "花园守护者", "leader": False, "score": 1120},
    {"uin": 900005, "name": "双发豌豆连击", "guild": "花园守护者", "leader": False, "score": 1160},
    {"uin": 900006, "name": "三线豌豆射手", "guild": "花园守护者", "leader": False, "score": 1200},
    {"uin": 900007, "name": "西瓜投手王",   "guild": "花园守护者", "leader": False, "score": 1240},
    # --- 月光花园 ---
    {"uin": 900008, "name": "月光仙人掌",   "guild": "月光花园",   "leader": True,  "score": 1280},
    {"uin": 900009, "name": "小喷菇",       "guild": "月光花园",   "leader": False, "score": 1320},
    {"uin": 900010, "name": "坚果司令",     "guild": "月光花园",   "leader": False, "score": 1360},
    {"uin": 900011, "name": "忧郁蘑菇王",   "guild": "月光花园",   "leader": False, "score": 1400},
    {"uin": 900012, "name": "咖啡豆唤醒者", "guild": "月光花园",   "leader": False, "score": 1440},
    {"uin": 900013, "name": "星星果闪耀",   "guild": "月光花园",   "leader": False, "score": 1480},
    {"uin": 900014, "name": "磁力菇大师",   "guild": "月光花园",   "leader": False, "score": 1520},
    # --- 人机联盟（AI 自主运行，参见 run_ai_tick）---
    {"uin": 900015, "name": "机械向日葵",   "guild": "人机联盟",   "leader": True,  "score": 1560},
    {"uin": 900016, "name": "钛合金坚果",   "guild": "人机联盟",   "leader": False, "score": 1600},
    {"uin": 900017, "name": "蒸汽机豌豆",   "guild": "人机联盟",   "leader": False, "score": 1640},
    {"uin": 900018, "name": "量子仙人掌",   "guild": "人机联盟",   "leader": False, "score": 1680},
    {"uin": 900019, "name": "光子向日葵",   "guild": "人机联盟",   "leader": False, "score": 1720},
    {"uin": 900020, "name": "时空豌豆",     "guild": "人机联盟",   "leader": False, "score": 1760},
    {"uin": 900021, "name": "反物质蘑菇",   "guild": "人机联盟",   "leader": False, "score": 1800},
    # --- 阳光联盟 ---
    {"uin": 900022, "name": "光能双子葵",   "guild": "阳光联盟",   "leader": True,  "score": 1840},
    {"uin": 900023, "name": "黄金向日葵",   "guild": "阳光联盟",   "leader": False, "score": 1880},
    {"uin": 900024, "name": "阳光番茄",     "guild": "阳光联盟",   "leader": False, "score": 1920},
    {"uin": 900025, "name": "火炬树桩烈焰", "guild": "阳光联盟",   "leader": False, "score": 1960},
    {"uin": 900026, "name": "玉米加农炮手", "guild": "阳光联盟",   "leader": False, "score": 2000},
    {"uin": 900027, "name": "白菜投手冠军", "guild": "阳光联盟",   "leader": False, "score": 2040},
    {"uin": 900028, "name": "机枪豌豆风暴", "guild": "阳光联盟",   "leader": False, "score": 2080},
    # --- 寒霜要塞 ---
    {"uin": 900029, "name": "霜冻寒冰菇",   "guild": "寒霜要塞",   "leader": True,  "score": 2120},
    {"uin": 900030, "name": "寒冰射手之神", "guild": "寒霜要塞",   "leader": False, "score": 2160},
    {"uin": 900031, "name": "冰霜西瓜王",   "guild": "寒霜要塞",   "leader": False, "score": 2200},
    {"uin": 900032, "name": "雪人坚果",     "guild": "寒霜要塞",   "leader": False, "score": 2240},
    {"uin": 900033, "name": "冰冻生菜守卫", "guild": "寒霜要塞",   "leader": False, "score": 2280},
    {"uin": 900034, "name": "极地仙人掌",   "guild": "寒霜要塞",   "leader": False, "score": 2320},
    # --- 熔岩军团 ---
    {"uin": 900035, "name": "熔岩辣椒王",   "guild": "熔岩军团",   "leader": True,  "score": 2360},
    {"uin": 900036, "name": "火爆辣椒王",   "guild": "熔岩军团",   "leader": False, "score": 2400},
    {"uin": 900037, "name": "火焰豌豆侠",   "guild": "熔岩军团",   "leader": False, "score": 2440},
    {"uin": 900038, "name": "岩浆坚果",     "guild": "熔岩军团",   "leader": False, "score": 2480},
    {"uin": 900039, "name": "烈焰向日葵",   "guild": "熔岩军团",   "leader": False, "score": 2520},
    {"uin": 900040, "name": "火花杨桃",     "guild": "熔岩军团",   "leader": False, "score": 2560},
    # --- 量子花园 ---
    {"uin": 900041, "name": "维度仙人掌",   "guild": "量子花园",   "leader": True,  "score": 2600},
    {"uin": 900042, "name": "量子坚果",     "guild": "量子花园",   "leader": False, "score": 2640},
    {"uin": 900043, "name": "幽灵辣椒",     "guild": "量子花园",   "leader": False, "score": 2680},
    {"uin": 900044, "name": "午夜铃兰",     "guild": "量子花园",   "leader": False, "score": 2720},
    {"uin": 900045, "name": "影子豌豆",     "guild": "量子花园",   "leader": False, "score": 2760},
    # --- 暗夜花苑 ---
    {"uin": 900046, "name": "暗夜玫瑰",     "guild": "暗夜花苑",   "leader": True,  "score": 2800},
    {"uin": 900047, "name": "月光蘑菇",     "guild": "暗夜花苑",   "leader": False, "score": 2840},
    {"uin": 900048, "name": "僵尸克星",     "guild": "暗夜花苑",   "leader": False, "score": 2880},
    {"uin": 900049, "name": "龙息火龙草",   "guild": "暗夜花苑",   "leader": False, "score": 2920},
    {"uin": 900050, "name": "雷神香蕉",     "guild": "暗夜花苑",   "leader": False, "score": 2960},
]

# The bot lineups are built from 8 plant species across quality tiers (q1..q4 live in the 11xx/9xxx
# id range, q5 in the 50xxxx range — verified against battleCard + card_stats_at_level). Each bot
# gets six species at ITS tier, so the roster spans ~12k (beatable early) to ~410k power (a real
# end-game challenge) — "人机的战力也不能弱" without turning every match into a curb stomp.
_BOT_SPECIES = [1181, 1251, 1241, 9501, 1161, 1151, 1231, 1221]   # q1 cid per species (+quality-1)
_BOT_Q5_SPECIES = [501185, 501255, 501245, 509505, 501165, 501155, 501235, 501225]
# --- development ladder ---------------------------------------------------------------
# 战力来自真实养成，而不是等级堆出来的数字：植物等级上限 300，其余战力由 拼图/锻造/果实/
# 觉醒/护甲/洗炼 一起贡献（见 server.bot_develop_card）。每个 bot 按名次拿一个养成进度，
# 六张卡的所有系统一起推进，所以榜单上的战力就是它实际能打出的强度。
_BOT_DEV_MIN = 0.0               # the weakest bot is a fresh L1 q1 lineup (a pushover)
_BOT_DEV_MAX = 1.0               # the top whale has every track maxed (等级300 + 全系统)

# per-bot card quality: index in _BOTS order. Capped at q4 — the q5 plants (50xxxx) carry
# ~10-26x the per-card power of q1 and pushed the top bots to 42万 (420k), which read as absurd
# ("战力 3w-500w") in the arena board. Keeping the roster on q1..q4 lands every bot in a believable
# 1.6万..4.2万 band (still beatable early, still a real threat at the top — "人机的战力也不能弱").
_BOT_QUALITY = {900001: 4, 900002: 2, 900003: 3, 900004: 4, 900005: 1, 900006: 3,
                900007: 4, 900008: 2, 900009: 4, 900010: 3, 900011: 4, 900012: 2,
                900013: 3, 900014: 4}
_BOT_MIX = {900002: (3, 3, 2, 2, 2, 2), 900003: (4, 4, 4, 3, 3, 3),
            900004: (4, 4, 4, 4, 4, 4)}
# The player formation slots (pvz_poc_server._FORMATION_POS): pos 1 is the puppet, 5 skipped.
_FORMATION_SLOTS = [3, 7, 9, 11, 13, 15]
_UID_BASE = 10_000_000     # same real-card uid scheme as ensure_player_cards

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

_BOT_LEVEL_MIN, _BOT_LEVEL_MAX = 45, 59


def _bot_develop_fraction(index, total=None):
    """How developed a bot is, _BOT_DEV_MIN .. _BOT_DEV_MAX — drives EVERY progression track."""
    total = total or len(_BOTS)
    if total <= 1:
        return _BOT_DEV_MAX
    return _BOT_DEV_MIN + (_BOT_DEV_MAX - _BOT_DEV_MIN) * (float(index) / float(total - 1))


def _bot_cards(uin, bot_index, develop=None):
    """The bot's six (cardId, level) pairs.

    The card LEVEL is capped at 300 (植物等级上限) — everything above that comes from
    ``develop_card`` (拼图/锻造/果实/觉醒/护甲/洗炼). Quality rises with the same fraction, so a
    barely-developed bot fields q1 commons and the top whale fields a full q5 set."""
    f = _bot_develop_fraction(bot_index) if develop is None else float(develop)
    tier = max(1, min(5, 1 + int(f * 5)))
    level = max(1, int(round(1 + f * 299)))
    out = []
    for slot, _pos in enumerate(_FORMATION_SLOTS):
        species = (bot_index + slot) % len(_BOT_SPECIES)
        cid = _BOT_Q5_SPECIES[species] if tier >= 5 else _BOT_SPECIES[species] + tier - 1
        out.append((cid, level))
    return out


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

    `level` is the role level the ladder shows; 战力 itself lives in bots_state, written by
    ensure_bots from the bots' REAL cards (等级上限300 + 拼图/锻造/果实/觉醒/护甲/洗炼)."""
    out = []
    for idx, b in enumerate(_BOTS):
        row = dict(b)
        row.setdefault("level", _BOT_LEVEL_MIN + idx)
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


def _seed_guilds():
    """Create the bot guilds + member rows (idempotent). Guilds WITHOUT a bot member roster are
    left alone — real player guilds are never touched."""
    if not _GUILD_DB_PATH:
        return
    con = _connect(_GUILD_DB_PATH)
    try:
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
                state = bot_state(b["uin"]) or {"power": 0}
                member = con.execute("SELECT uin FROM local_guild_members WHERE uin=?",
                                     (b["uin"],)).fetchone()
                if member:
                    # keep the stored power fresh (it feeds the guild member list)
                    con.execute("UPDATE local_guild_members SET fight_power=?, level=? WHERE uin=?",
                                (state["power"], 70, b["uin"]))
                    continue
                con.execute(
                    "INSERT OR IGNORE INTO local_guild_members(guild_id, uin, role_id, name, "
                    "position, contrib, level, face_id, fight_power, joined_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (gid, b["uin"], b["uin"], b["name"],
                     1 if b["leader"] else 2, 300 + b["score"] % 200, 70, 0,
                     state["power"], now))
        con.commit()
    finally:
        con.close()


def ensure_bots():
    """Idempotently create every bot account: player row, six-card lineup, formation, arena
    score row, and the two bot guilds. Safe to call on every server start."""
    if not _DB_PATH:
        _log("bots: no db path configured, skipped")
        return 0
    get_or_create_player = _API.get("get_or_create_player")
    add_card = _API.get("add_card")
    set_formation = _API.get("set_formation")
    card_power_at = _API.get("card_power_at")
    if not (get_or_create_player and add_card and set_formation and card_power_at):
        _log("bots: api not wired, skipped")
        return 0
    con = _connect(_DB_PATH)
    try:
        _ensure_bots_state_table(con)
        existing = {int(r["uin"]) for r in con.execute("SELECT uin FROM bots_state").fetchall()}
    finally:
        con.close()
    created = 0
    for idx, b in enumerate(_BOTS):
        uin = b["uin"]
        con = _connect(_DB_PATH)
        try:
            _ensure_bots_state_table(con)
            if uin not in existing:
                con.execute("INSERT OR IGNORE INTO bots_state(uin, score, wins, losses, power) "
                            "VALUES(?,?,0,0,0)", (uin, b["score"]))
                con.commit()
        finally:
            con.close()
        player, _new = get_or_create_player(uin)
        # name / level snapshot (the client shows these in lists)
        if (player.get("name") or "") != b["name"]:
            _API["save_player"](uin, name=b["name"], level=70)
            player["name"] = b["name"]
            player["level"] = 70
        # lineup: the bot's quality tier (see _bot_cards). If the stored cards no longer match
        # the current gradient (old seed / roster rebalance), wipe and re-add cleanly.
        develop = _bot_develop_fraction(idx)
        pairs = _bot_cards(uin, idx, develop)
        expected = {}                        # uid -> (cid, level)
        for slot, (cid, level) in zip(_FORMATION_SLOTS, pairs):
            expected[_UID_BASE + cid + idx] = (cid, level)
        have = {uid: (cid, level) for uid, cid, level, _exp in _API["get_cards"](uin)}
        if have != expected:
            clear = _API.get("clear_cards_and_formation")
            if clear:
                clear(uin)                   # drops stale cards AND the saved formation
                have = {}
        placed = {}
        changed = False
        for slot, (cid, level) in zip(_FORMATION_SLOTS, pairs):
            uid = _UID_BASE + cid + idx          # per-bot distinct uid (same cid reuse is fine)
            if uid not in have:
                if add_card(uin, uid, cid, level, 0):
                    have[uid] = (cid, level)
                    changed = True
            placed[slot] = uid
        if changed or not _API.get("get_formation", lambda *_a: None)(uin, 1):
            set_formation(uin, 1, placed)
        if changed:
            created += 1
        # 战力 = 从真实卡牌算出来的强度：等级(≤300) + 拼图/锻造/果实/觉醒/护甲/洗炼 一起推进，
        # 所以榜单数字就是它实际能打出的强度（arena 现在按真实阵容开打）。只在卡组重建时
        # 重推一遍养成，避免每次启动都写几千行进度。
        develop_card = _API.get("develop_card")
        card_power_full = _API.get("card_power_full")
        if changed and develop_card:
            for slot, (cid, level) in zip(_FORMATION_SLOTS, pairs):
                develop_card(uin, _UID_BASE + cid + idx, cid, develop)
        power = 0
        for cid, level in pairs:
            uid = _UID_BASE + cid + idx
            power += (card_power_full(uin, cid, level, uid) if card_power_full
                      else card_power_at(cid, level))
        con = _connect(_DB_PATH)
        try:
            _ensure_bots_state_table(con)
            con.execute("UPDATE bots_state SET power=? WHERE uin=?", (power, uin))
            con.commit()
        finally:
            con.close()
    _seed_guilds()
    _log("bots: ensured %d bot accounts (%s)", len(_BOTS),
         ", ".join("%s=%d" % (b["name"], b["uin"]) for b in _BOTS[:3]) + " ...")
    return created


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
