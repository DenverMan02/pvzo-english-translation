"""密藏守护 / Guard（MODE_HOLE_DEF = 19）适配器。

客户端对密藏守护监听两组协议：

    Cmd_Guard_GetGuardFormation_CS                       -> Cmd_Guard_SyncGuardFormation_SC
    Cmd_Guard_SetGuardFormation_CS{index, formation}     -> Cmd_Guard_SyncGuardFormation_SC
    Cmd_Guard_GetFormation_CS                            -> Cmd_Guard_SyncFormation_SC
    Cmd_Guard_SetFormation_CS{formation}                 -> Cmd_Guard_SyncFormation_SC
    Cmd_Guard_Info_CS                                    -> Cmd_Guard_Info_SC
    Cmd_Guard_Create_CS{guardType, formation, index}     -> Cmd_Guard_Create_SC
    Cmd_Guard_RefreshRobTarget_CS                        -> Cmd_Guard_RefreshRobTarget_SC
    Cmd_Guard_Info_By_ID_CS{guardId}                     -> Cmd_Guard_Info_By_ID_SC
    Cmd_Guard_CancelRob_CS                               -> Cmd_Guard_CancelRob_SC
    Cmd_Guard_FightRobTarget_CS{index}                   -> Cmd_Guard_FightRobTarget_SC

前 4 条只负责“出征前编辑阵容”，把 Guard 阵型存进 formation 表里独立的槽位
命名空间（team_id = 1000 + index），不再和家园队伍（team 1）互相覆盖。GET 时若
该槽还没有存档，就复制玩家的家园阵容播种一次，保证客户端拿到非空 formation。

之后 2 条对应“点出征 -> 建立密藏守护”。原始服务端没有实现，Create_CS 落到通用
兜底，回包里 Dto_Guard.guardType = 0，客户端 HoleTemplateFactory.getHoleType(0)
返回 null，HoleInfoCellMediator.reset() 在 #1009 崩掉。这里补上真实实现：
guardType 原样使用客户端传来的 HoleUseTypeTemplate.id(1..16)；guardId /
member.roleId / formation.leaderUid 全部显式编码成非空 Int64；创建出来的守护
任务落进 local_guard 表，Info_CS 再按序读回，客户端 4 个格子即可正常刷新。

最后 4 条是“密藏掠夺”（HoleConst.DETAIL_TYPE_ROB）。RefreshRobTarget_CS 原本也落
通用兜底，回包里没有 Dto_Guard，HoleFightWindowMediator.updateView() 在
L325 `_loc9_.useItemID` 直接 #1009（_loc9_ = getHoleType(type) == null）。这里按
“先真人、后 NPC”的顺序凑 3 个掠夺目标：

  * 真人：players.db 里其它非 bot、且 team1 阵容有效的玩家，用他们真实阵容做对手；
  * NPC：没有足够真人时，从 battleCard 植物池随机抽取 1-6 张、随机等级组成阵容，
    再按玩家当前出征总战力的 50% ~ 170% 随机缩放目标战力（不再复制玩家当前阵容）。

三个 member 都带完整 cardInfo（每条恰好一个 isLeader=true、不写 avatarId，避免
QBMovieUtil.updateAvatar 拿到 null 再 #1009），GetFightInfo(type=18) 再把选中
member 的卡组当作敌方阵容返回，客户端就能正常预览 + 出战。
"""

import copy
import random
import npc_lineups
import threading
import time

_GUARD_TEAM_BASE = 1000
_GUARD_ATTACK_TEAM = 2000
_GUARD_SLOT_MAX = 31
_FALLBACK_POS = (3, 7, 9, 11, 13, 15, 17)

# holeUseType.txt：id 1..16，每 4 个一组共享 costTime（分钟）。
_HOLE_TYPE_MAX = 16
_HOLE_COST_MINUTES = (180, 600, 1320, 1380)

# ---- 密藏掠夺（HoleConst.DETAIL_TYPE_ROB）参数 -------------------------------------
_ROB_TARGET_COUNT = 3            # HoleFightWindowMediator 固定 3 个格子
_ROB_TIME_SECONDS = 200          # holeUseType.txt robTime：一次掠夺可继续的时间（秒）
_ROB_POWER_MIN = 0.5             # 人机目标战力下限 = 玩家出征战力 * 50%
_ROB_POWER_MAX = 1.7             # 人机目标战力上限 = 玩家出征战力 * 170%
_ROB_NPC_ROLE_BASE = 800000000   # 合成 roleId 前缀，避开 bots_local 的 900000..999999 段
_ROB_ZONE_ID = 1

# 密藏掠夺的"灯火"消耗。客户端 ServerConfigTemplateFactory 从服务端配置里读
# iGuardRobCostLight / iDailyBuyLightTimes / iGuardMaxRenewLight。register() 会把这几
# 个键（连同 iGuardRefreshFactor / iGuardRefreshMaxCost）写进 SERVER_CONFIG_OVERRIDES，
# 随 Init_SC 下发给客户端，保证服务端和客户端用同一套数值：
#   * iGuardRefreshFactor / iGuardRefreshMaxCost = 1 -> 每次刷新目标扣 1 金币。
#     原版客户端打开掠夺页时 initByParam() 会主动请求一次刷新，因此打开窗口
#     也会按原逻辑扣 1 金币；手动点击"刷新目标"同样会再刷新一批。
#   * iGuardRobCostLight = 1 -> 每次开始战斗消耗 1 盏便携光源。
#   * iDailyBuyLightTimes = 5 -> 每天最多买 5 次灯（holeLight.txt 只有 5 条模板）。
#   * iGuardMaxRenewLight = 1 -> 灯火持有上限。
# 客户端 HoleProxyCmd.fightTarget() 只有 holeLight >= iGuardRobCostLight 时才真正下发
# Cmd_Guard_FightRobTarget_CS，否则走 Cmd_Guard_BuyLight_CS 买灯；服务端 buy_light()
# 补灯并回 SyncLight 后，客户端会继续把待战目标打完。
_ROB_COST_LIGHT = 1              # = 客户端 iGuardRobCostLight：每次掠夺消耗 1 盏灯
_ROB_REFRESH_COST = 1            # = 客户端 iGuardRefreshFactor / iGuardRefreshMaxCost：每次刷新 1 金币
_ROB_DAILY_BUY_LIGHT = 5         # = 客户端 iDailyBuyLightTimes：holeLight.txt 只有 5 条模板
_ROB_MAX_RENEW_LIGHT = 1         # = 客户端 iGuardMaxRenewLight：灯火持有上限（买满即停）

# 进程内缓存的掠夺目标：uin -> {"guard": {...}, "members": [...], "deadline": int, ...}。
# 掠夺是“刷新 -> 预览 -> 打”的短流程，同一个进程里保存最近一次刷新的目标即可；
# 服务端重启后玩家重新刷新一次就重建，不落库。
_ROB_TARGETS = {}
_ROB_LOCK = threading.RLock()


def _cost_minutes(guard_type):
    """成长时间（分钟）按 holeUseType.txt 的 1-4 / 5-8 / 9-12 / 13-16 分档。"""
    idx = (int(guard_type) - 1) // 4
    if 0 <= idx < len(_HOLE_COST_MINUTES):
        return _HOLE_COST_MINUTES[idx]
    return _HOLE_COST_MINUTES[0]


def _rob_guard_type(player):
    """把玩家等级映射到 holeUseType.txt 的有效 id（holeCondition 的 25/45/70/100）。"""
    level = max(1, int((player or {}).get("level") or 1))
    if level < 25:
        return 1
    if level < 45:
        return 5
    if level < 70:
        return 9
    return 13


def _slot(index):
    """Clamp a client slot index into the local namespace (0..31)."""
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 0
    if index < 0:
        index = 0
    if index > _GUARD_SLOT_MAX:
        index = _GUARD_SLOT_MAX
    return index


# ---- GetFightInfo(type=18)：密藏掠夺的开战前预览 ------------------------------------
# 客户端在出征窗口里点某个掠夺目标时会发 Cmd_Battle_GetFightInfo_CS，Dto_BattleTarget.type=18
# (BattleTargetType_Guard)、guardMemberIndex(14)=目标下标。服务器必须返回“自己的出征阵容 +
# 该目标的卡组”，否则窗口里自己一侧是空的（cardNum=0，startFight 会直接拦住）或敌方卡组对不上。

def _sanitize_placements(raw, owned, max_cards):
    """Keep owned UIDs once, preserving Guard's distinct positive grid slots."""
    placements, seen = {}, set()
    entries = raw.items() if isinstance(raw, dict) else (raw or ())
    for pos, uid in entries:
        try:
            pos, uid = int(pos), int(uid)
        except (TypeError, ValueError):
            continue
        if pos <= 0 or uid not in owned or pos in placements or uid in seen:
            continue
        if len(placements) >= max_cards:
            break
        placements[pos] = uid
        seen.add(uid)
    return placements


def _unique_guard_cards(cards):
    """A positive plant UID identifies one unit; zero is an unspecified NPC UID."""
    unique, seen = [], set()
    for card in cards:
        uid = int((card.get("unit") or {}).get("uid") or 0)
        if uid > 0:
            if uid in seen:
                continue
            seen.add(uid)
        unique.append(card)
    return unique


def _preview_placements(server, player):
    """出征阵容（team 2000）；缺省时用家园阵容播种，保证和 FightRobTarget 用的是同一套。"""
    store = server.store
    saved = store.get_formation(player["uin"], _GUARD_ATTACK_TEAM) or {}
    owned = {int(uid) for uid, _cid, _lvl, _exp in store.get_cards(player["uin"])}
    max_cards = int(getattr(server, "_FORMATION_MAX", 6) or 6)
    placements = _sanitize_placements(saved, owned, max_cards)
    if not placements:
        placements = _sanitize_placements(server.default_formation_placements(player), owned, max_cards)
        if placements:
            store.set_formation(player["uin"], _GUARD_ATTACK_TEAM, placements)
            leader = store.get_formation_leader(player["uin"], 1)
            if leader in placements.values():
                store.set_formation_leader(player["uin"], _GUARD_ATTACK_TEAM, leader)
    return placements


def _guard_enemy_card_dto(server, pos, uid, cid, level, unit, is_leader=False):
    """Reuse the shared enemy DTO's single total-power/initiative element."""
    return server._enemy_card_dto(pos, uid, cid, level, unit, is_leader)


def rob_fight_preview(server, player, index):
    """BattleTargetType_Guard(18) 的 GetFightInfo 数据。

    返回 (formation_bytes, [Dto_EnemyCardInfo bytes], enemy_total_power)。敌方是 RefreshRobTarget
    缓存里第 index 个目标 member 的卡组（优先真人、否则按战力缩放出来的人机）；formation 是玩家
    自己的出征阵容。没有缓存（客户端没刷新就开打）时仍返回合法的空敌方列表，不抛异常。
    """
    # Copy under the cache lock, then release it before formation/store work.
    with _ROB_LOCK:
        entry = copy.deepcopy(_ROB_TARGETS.get(int(player["uin"])))
    members = (entry or {}).get("members") or []
    member = None
    if members:
        index = int(index or 0)
        member = members[index] if 0 <= index < len(members) else members[0]
    placements = _preview_placements(server, player)
    formation = server.build_formation_info(player, _GUARD_ATTACK_TEAM, placements)
    cards = []
    total_power = 0
    for card in _unique_guard_cards((member or {}).get("cards") or []):
        unit = card.get("unit") or {}
        cid = int(unit.get("card_id") or 0)
        if cid <= 0 or cid not in server._BATTLE_CARDS:
            continue
        total_power += max(0, int(unit.get("power") or 0))
        cards.append(_guard_enemy_card_dto(server, int(card.get("pos") or 0),
                                            int(unit.get("uid") or 0), cid,
                                            max(1, int(unit.get("level") or 1)),
                                            unit, bool(unit.get("is_leader"))))
    return formation, cards, total_power


def register(server):
    """Install Guard handlers on ``server.REQUEST_HANDLERS`` and return them."""
    store = server.store
    parse = server.parse_varint_fields
    repeated = server.parse_repeated_submessages
    vf = server.pb_varint_field
    bf = server.pb_bytes
    i64 = server.pb_int64_field
    sf = server.pb_string
    boolean = server.pb_bool
    log = server.log.info
    max_cards = int(getattr(server, "_FORMATION_MAX", 6) or 6)

    enemy_card_dto = server._enemy_card_dto
    build_player_units = server.build_player_units
    build_formation_info = server.build_formation_info
    default_formation_placements = server.default_formation_placements
    player_face_url = server.player_face_url
    enemy_positions = list(getattr(server, "_ENEMY_POS", (4, 8, 10, 12, 14, 16, 18)))
    bots_api = getattr(server, "bots_local", None)

    # ---- 下发密藏掠夺相关配置（必须早于任何 Init_SC 生成） ----------------------
    # 这些 key 已经在 pvz_poc_server.SERVER_CONFIG_IDS 里。刷新费显式钉成 factor=1 /
    # maxCost=1（每次刷新扣 1 金币，绝不免费）；剩余 key 覆盖灯火与每日购买次数。
    _cfg = getattr(server, "SERVER_CONFIG_OVERRIDES", None)
    if isinstance(_cfg, dict):
        _cfg.update({
            "iGuardRefreshFactor": str(_ROB_REFRESH_COST),
            "iGuardRefreshMaxCost": str(_ROB_REFRESH_COST),
            "iGuardRobCostLight": str(_ROB_COST_LIGHT),
            "iGuardMaxRenewLight": str(_ROB_MAX_RENEW_LIGHT),
            "iDailyBuyLightTimes": str(_ROB_DAILY_BUY_LIGHT),
        })

    def _is_bot(uin):
        if bots_api is None:
            return False
        try:
            return bool(bots_api.is_bot(uin))
        except Exception:
            return False

    def _owned(player):
        return {int(uid) for uid, _cid, _lvl, _exp in store.get_cards(player["uin"])}

    def _sanitize(player, raw):
        """Keep at most ``max_cards`` distinct owned cards on positive positions.

        The Guard grid lives in its own slot namespace, so positions are kept
        exactly as the client sent them (non-positive / duplicate slots or UIDs are
        dropped) instead of being forced onto the home team's _FORMATION_POS.
        """
        return _sanitize_placements(raw, _owned(player), max_cards)

    def _stored(player, team_id):
        return _sanitize(player, store.get_formation(player["uin"], team_id))

    def _seed(player, team_id):
        """Read a Guard slot; seed it from the home lineup when still empty."""
        placements = _stored(player, team_id)
        if placements:
            return placements
        placements = _sanitize(player, default_formation_placements(player))
        if placements:
            store.set_formation(player["uin"], team_id, placements)
            leader = store.get_formation_leader(player["uin"], 1)
            if leader in placements.values():
                store.set_formation_leader(player["uin"], team_id, leader)
            log("    guard: seeded team=%d from home lineup: %s", team_id, placements)
        return placements

    def _sync_defend(player, index):
        team_id = _GUARD_TEAM_BASE + index
        placements = _seed(player, team_id)
        formation = build_formation_info(player, team_id, placements)
        return [("PVZ.Cmd.Cmd_Guard_SyncGuardFormation_SC",
                 vf(1, index) + bf(2, formation))]

    def get_guard_formation(data, player):
        index = _slot(parse(data).get(1, 0))
        log("    guard GET guard-formation slot=%d", index)
        return _sync_defend(player, index)

    def set_guard_formation(data, player):
        index = _slot(parse(data).get(1, 0))
        team_id = _GUARD_TEAM_BASE + index
        forms = repeated(data, 2)
        if not forms:
            # Malformed / empty request body: keep the stored lineup instead of
            # wiping it, then push it back so the client refreshes.
            log("    guard SET guard-formation slot=%d without formation; kept", index)
            return _sync_defend(player, index)
        fields = parse(forms[0])
        leader = int(fields.get(3, 0) or 0)
        raw = {}
        for cp in repeated(forms[0], 1):                 # Dto_FormationInfo.cardPos
            card = parse(cp)
            pos, uid = card.get(1, 0), card.get(2, 0)
            if pos and uid:
                raw[int(pos)] = int(uid)
        placements = _sanitize(player, raw)
        store.set_formation(player["uin"], team_id, placements)
        if leader in placements.values():
            store.set_formation_leader(player["uin"], team_id, leader)
        else:
            store.set_formation_leader(player["uin"], team_id, 0)
        log("    guard SET guard-formation slot=%d leader=%d cards=%s",
            index, leader, placements)
        return _sync_defend(player, index)

    def _sync_attack(player):
        placements = _seed(player, _GUARD_ATTACK_TEAM)
        formation = build_formation_info(player, _GUARD_ATTACK_TEAM, placements)
        return [("PVZ.Cmd.Cmd_Guard_SyncFormation_SC", bf(1, formation))]

    def get_formation(data, player):
        log("    guard GET attack-formation")
        return _sync_attack(player)

    def set_formation(data, player):
        forms = repeated(data, 1)
        if not forms:
            return _sync_attack(player)
        fields = parse(forms[0])
        leader = int(fields.get(3, 0) or 0)
        raw = {}
        for cp in repeated(forms[0], 1):
            card = parse(cp)
            pos, uid = card.get(1, 0), card.get(2, 0)
            if pos and uid:
                raw[int(pos)] = int(uid)
        placements = _sanitize(player, raw)
        store.set_formation(player["uin"], _GUARD_ATTACK_TEAM, placements)
        if leader in placements.values():
            store.set_formation_leader(player["uin"], _GUARD_ATTACK_TEAM, leader)
        else:
            store.set_formation_leader(player["uin"], _GUARD_ATTACK_TEAM, 0)
        log("    guard SET attack-formation leader=%d cards=%s", leader, placements)
        return _sync_attack(player)

    # ---- 密藏守护任务（出征 / 信息） ------------------------------------
    # 一个玩家最多 4 条（HoleConst.CELL_BOUNDS = 4），按 slot 覆盖旧档。

    def _ensure_guard_table(con):
        con.execute(
            "CREATE TABLE IF NOT EXISTS local_guard ("
            "guard_id INTEGER PRIMARY KEY, uin INTEGER NOT NULL, guard_type INTEGER NOT NULL,"
            " slot INTEGER NOT NULL, finish_time INTEGER NOT NULL, finish INTEGER NOT NULL DEFAULT 0,"
            " team_id INTEGER NOT NULL, role_id INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '',"
            " level INTEGER NOT NULL DEFAULT 1, face_id INTEGER NOT NULL DEFAULT 0,"
            " face_url TEXT NOT NULL DEFAULT '', power INTEGER NOT NULL DEFAULT 0,"
            " join_time INTEGER NOT NULL DEFAULT 0)")

    def _guards(player):
        with store._db() as con:
            _ensure_guard_table(con)
            rows = con.execute(
                "SELECT * FROM local_guard WHERE uin=? ORDER BY slot, guard_id",
                (int(player["uin"]),)).fetchall()
            return [dict(r) for r in rows]

    def _save_guard(player, task):
        with store._db() as con:
            _ensure_guard_table(con)
            con.execute("DELETE FROM local_guard WHERE uin=? AND slot=?",
                        (int(player["uin"]), int(task["slot"])))
            con.execute(
                "INSERT INTO local_guard (guard_id, uin, guard_type, slot, finish_time, finish,"
                " team_id, role_id, name, level, face_id, face_url, power, join_time)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(task["guard_id"]), int(player["uin"]), int(task["guard_type"]),
                 int(task["slot"]), int(task["finish_time"]), int(task.get("finish", 0)),
                 int(task["team_id"]), int(task["role_id"]), str(task["name"]),
                 int(task["level"]), int(task["face_id"]), str(task["face_url"]),
                 int(task["power"]), int(task["join_time"])))

    def _ensure_refresh_table(con):
        con.execute(
            "CREATE TABLE IF NOT EXISTS local_guard_refresh ("
            "uin INTEGER PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)")

    def _refresh_count(player, bump=False):
        uin = int(player["uin"])
        with store._db() as con:
            _ensure_refresh_table(con)
            row = con.execute("SELECT count FROM local_guard_refresh WHERE uin=?",
                              (uin,)).fetchone()
            count = int(row["count"]) if row else 0
            if bump:
                count += 1
                con.execute("INSERT OR REPLACE INTO local_guard_refresh (uin, count)"
                            " VALUES (?,?)", (uin, count))
        return count

    def _ensure_buylight_table(con):
        con.execute(
            "CREATE TABLE IF NOT EXISTS local_guard_buylight ("
            "uin INTEGER PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0, "
            "day TEXT NOT NULL DEFAULT '')")
        columns = {row[1] for row in con.execute("PRAGMA table_info(local_guard_buylight)")}
        if "day" not in columns:
            # A lifetime count has no known date. Treat it as an old day once,
            # rather than permanently exhausting every future day's allowance.
            con.execute("ALTER TABLE local_guard_buylight ADD COLUMN day TEXT NOT NULL DEFAULT ''")

    def _buylight_count_in(con, player):
        uin = int(player["uin"])
        today = time.strftime("%Y%m%d", time.localtime())
        _ensure_buylight_table(con)
        con.execute("INSERT OR IGNORE INTO local_guard_buylight(uin,count,day) VALUES(?,0,?)",
                    (uin, today))
        con.execute("UPDATE local_guard_buylight SET count=0,day=? WHERE uin=? AND day<>?",
                    (today, uin, today))
        row = con.execute("SELECT count FROM local_guard_buylight WHERE uin=?", (uin,)).fetchone()
        return max(0, int(row["count"]))

    def _buylight_count(player):
        with store._db() as con:
            store.begin_immediate(con)
            return _buylight_count_in(con, player)

    def _ensure_rob_history_table(con):
        # 本地掠夺记录：Dto_Guard_RobHistory 的字段一一对应。
        con.execute(
            "CREATE TABLE IF NOT EXISTS local_guard_rob_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "uin INTEGER NOT NULL,"
            "name TEXT NOT NULL DEFAULT '',"
            "zone_id INTEGER NOT NULL DEFAULT 1,"
            "level INTEGER NOT NULL DEFAULT 1,"
            "face_id INTEGER NOT NULL DEFAULT 0,"
            "face_url TEXT NOT NULL DEFAULT '',"
            "time INTEGER NOT NULL DEFAULT 0,"
            "rob_win INTEGER NOT NULL DEFAULT 0)")

    def _append_rob_history(con, player, member, win, now):
        """每场掠夺结算后写一条记录（无论胜负）。"""
        member = member or {}
        _ensure_rob_history_table(con)
        con.execute(
            "INSERT INTO local_guard_rob_history"
            " (uin, name, zone_id, level, face_id, face_url, time, rob_win)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (int(player["uin"]), str(member.get("name") or ""),
             int(_ROB_ZONE_ID), max(1, int(member.get("level") or 1)),
             max(0, int(member.get("face_id") or 0)),
             str(member.get("face_url") or ""),
             int(now), 1 if win else 0))

    def _rob_history_bytes(player, limit=20):
        """把最近 limit 条掠夺记录编码成 Cmd_Guard_RobHistory_SC 的 repeated field 1。"""
        try:
            with store._db() as con:
                _ensure_rob_history_table(con)
                rows = con.execute(
                    "SELECT name, zone_id, level, face_id, face_url, time, rob_win"
                    " FROM local_guard_rob_history WHERE uin=?"
                    " ORDER BY time DESC, id DESC LIMIT ?",
                    (int(player["uin"]), int(limit))).fetchall()
        except Exception:
            rows = []
        body = b""
        for row in rows:
            body += bf(1, (sf(1, str(row["name"] or ""))
                           + vf(2, max(0, int(row["zone_id"] or 0)))
                           + vf(3, max(0, int(row["level"] or 0)))
                           + vf(4, max(0, int(row["face_id"] or 0)))
                           + sf(5, str(row["face_url"] or ""))
                           + vf(6, max(0, int(row["time"] or 0)))
                           + boolean(7, bool(row["rob_win"]))))
        return body

    def _member_bytes(member):
        """Dto_Guard_Member：roleId/name/level/faceId/qqFaceURL/joinTime/inviteTime/
        power/speed + cardInfo(10)=[Dto_EnemyCardInfo]（avatarId 留空）。"""
        role_id = int(member.get("role_id") or 0) or 1
        raw_cards = member.get("cards") or []
        cards = _unique_guard_cards(raw_cards)
        power, speed = member.get("power"), member.get("speed")
        if len(cards) != len(raw_cards):
            power = sum(int((card.get("unit") or {}).get("power") or 0) for card in cards)
            speed = sum(int((card.get("unit") or {}).get("speed") or 0) for card in cards)
        body = (i64(1, role_id)
                + sf(2, str(member.get("name") or "Player"))
                + vf(3, max(1, int(member.get("level") or 1)))
                + vf(4, max(0, int(member.get("face_id") or 0)))
                + sf(5, str(member.get("face_url") or ""))
                + vf(6, max(0, int(member.get("join_time") or 0)))
                + vf(7, max(0, int(member.get("invite_time") or 0)))
                + vf(8, max(0, int(power or 0)))
                + vf(9, max(0, int(speed or 0))))
        for card in cards:
            unit = card.get("unit") or {}
            card_id = int(unit.get("card_id") or 0)
            if card_id <= 0 or card_id not in server._BATTLE_CARDS:
                continue
            body += bf(10, _guard_enemy_card_dto(server, int(card.get("pos") or 0),
                                          int(unit.get("uid") or 0), card_id,
                                          max(1, int(unit.get("level") or 1)),
                                          unit, bool(unit.get("is_leader"))))
        return body

    # ---- 掠夺奖励：holeReward / holeUseType 查表 + 编码 -------------------------
    # 奖励公式（反编译客户端 HoleInfoCellMediator / HoleFightWindowMediator）：
    #   base = holeReward[战力档位].reward
    #   num  = max(1, base * rewardRate * costTime / 100000000)
    # 注意：costTime 直接使用 holeUseType.txt 原值，不再乘 60。奖励物品 id 也使用
    # useItemID（金币任务就是 1，客户端图标同样按这个 id 取图）。

    _reward_tiers = []
    _rate_by_type = {}
    _cost_by_type = {}
    _item_by_type = {}
    _robtime_by_type = {}

    _read_table = getattr(server, "_read_clientdb_table", None)
    try:
        rows = _read_table("holeReward.txt") if _read_table else []
    except Exception:
        rows = []
    for row in (rows[1:] if rows else []):
        if len(row) < 2:
            continue
        try:
            _reward_tiers.append((int(row[0].strip()), int(row[1].strip())))
        except (TypeError, ValueError):
            pass
    _reward_tiers.sort(key=lambda item: item[0])
    if not _reward_tiers:
        # clientDB 不存在时只做保守兜底；正常本地版一定会读到完整表。
        _reward_tiers = [(0, 2000), (5000, 2499), (10000, 2998)]

    try:
        rows = _read_table("holeUseType.txt") if _read_table else []
    except Exception:
        rows = []
    if rows:
        header = {str(name).strip(): idx for idx, name in enumerate(rows[0])}
        for row in rows[1:]:
            try:
                tid = int(row[header["id"]].strip())
                _rate_by_type[tid] = int(row[header["rewardRate"]].strip())
                _cost_by_type[tid] = int(row[header["costTime"]].strip())
                _item_by_type[tid] = int(row[header["useItemID"]].strip())
                _robtime_by_type[tid] = int(row[header["robTime"]].strip())
            except (KeyError, IndexError, TypeError, ValueError):
                pass

    def _rob_reward_base(power):
        """HoleTemplateFactory.getReward(power)：取 id <= power 的最大一档。"""
        value = int(power or 0)
        if not _reward_tiers:
            return 2000
        lo, hi, best = 0, len(_reward_tiers) - 1, _reward_tiers[0][1]
        while lo <= hi:
            mid = (lo + hi) // 2
            if _reward_tiers[mid][0] <= value:
                best = _reward_tiers[mid][1]
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def _rob_reward_amount(guard_type, power):
        idx = max(1, min(int(guard_type or 1), _HOLE_TYPE_MAX))
        rate = _rate_by_type.get(idx, 250000)
        cost = _cost_by_type.get(idx, _cost_minutes(idx))
        base = _rob_reward_base(power)
        return max(1, int(base * rate * cost // 100000000))

    def _guard_reward_bytes(task):
        """Dto_Guard.reward = repeated Dto_IdNum{id(1), num(2)}。"""
        idx = max(1, min(int(task.get("guard_type") or 1), _HOLE_TYPE_MAX))
        amount = _rob_reward_amount(idx, int(task.get("power") or 0))
        item_id = _item_by_type.get(idx, 1)
        return bf(6, vf(1, item_id) + vf(2, amount))

    def _guard_bytes(task):
        """Dto_Guard：guardType 必须落在 holeUseType.txt 的有效 id(1..16) 上，
        guardId / member.roleId 必须是真实 Int64，否则客户端 reset() 会 #1009。"""
        guard_type = int(task.get("guard_type") or 0)
        if guard_type < 1 or guard_type > _HOLE_TYPE_MAX:
            guard_type = 1
        members = task.get("members") or None
        if members:
            member = b"".join(bf(4, _member_bytes(m)) for m in members)
        else:
            member = bf(4, _member_bytes(task))
        return (i64(1, int(task.get("guard_id") or 0))
                + vf(2, guard_type)
                + vf(3, max(0, int(task.get("finish_time") or 0)))
                + member
                + boolean(5, bool(int(task.get("finish", 0))))
                + _guard_reward_bytes(task))

    def get_info(data, player):
        now = int(time.time())
        tasks = _guards(player)
        body = b""
        for task in tasks:
            team_id = int(task.get("team_id")
                          or (_GUARD_TEAM_BASE + int(task.get("slot") or 0)))
            placements = _stored(player, team_id) or _seed(player, team_id)
            formation = build_formation_info(player, team_id, placements)
            fp = parse(formation)
            task["power"] = max(0, int(fp.get(4, 0) or 0))
            if not task.get("finish") and int(task.get("finish_time") or 0) <= now:
                task["finish"] = 1
                _save_guard(player, task)
            body += bf(1, _guard_bytes(task)) + bf(2, formation)
        body += (boolean(3, False) + boolean(4, False) + boolean(5, False)
                 + vf(6, _refresh_count(player)) + vf(7, _buylight_count(player)))
        messages = [("PVZ.Cmd.Cmd_Guard_Info_SC", body)]
        # 兜底：即使客户端缓存了旧的无自动刷新 SWF，重进时也把当前缓存目标补发一次。
        # 原版由 HoleFightWindowMediator 在打开掠夺页时主动发刷新请求，
        # GetInfo 不注入缓存目标，避免重复刷新或把上一轮旗帜提前清掉。
        log("    guard INFO -> %d guard(s)", len(tasks))
        return messages

    def create_guard(data, player):
        fields = parse(data)
        guard_type = int(fields.get(1, 0) or 0)
        if guard_type < 1 or guard_type > _HOLE_TYPE_MAX:
            guard_type = 1
        slot = _slot(fields.get(3, 0))
        team_id = _GUARD_TEAM_BASE + slot

        # 优先用客户端在出征界面里选好的阵容；缺失/非法时退回该槽已有存档。
        placements, leader = None, 0
        forms = repeated(data, 2)
        if forms:
            req_form = parse(forms[0])
            leader = int(req_form.get(3, 0) or 0)
            raw = {}
            for cp in repeated(forms[0], 1):
                card = parse(cp)
                pos = int(card.get(1, 0) or 0)
                uid = int(card.get(2, 0) or 0)
                if pos and uid:
                    raw[pos] = uid
            placements = _sanitize(player, raw)
        if not placements:
            placements = _seed(player, team_id)
        store.set_formation(player["uin"], team_id, placements)
        store.set_formation_leader(player["uin"], team_id,
                                   leader if leader in placements.values() else 0)

        formation = build_formation_info(player, team_id, placements)
        fp = parse(formation)
        total_power = max(0, int(fp.get(4, 0) or 0))
        leader_uid = int(fp.get(3, 0) or 0)

        now = int(time.time())
        uin = int(player["uin"])
        task = {
            "guard_id": (int(now * 1000) << 20) | ((uin & 0xFFFFF) << 4) | (slot & 0xF),
            "guard_type": guard_type,
            "slot": slot,
            "finish_time": now + _cost_minutes(guard_type) * 60,
            "finish": 0,
            "team_id": team_id,
            "role_id": int(player.get("role_id") or uin or 0),
            "name": str(player.get("name") or ("Player%d" % (uin % 100000))),
            "level": max(1, int(player.get("level") or 1)),
            "face_id": max(0, int(player.get("face_id") or 0)),
            "face_url": str(player_face_url(player) or ""),
            "power": total_power,
            "join_time": now,
        }
        _save_guard(player, task)
        log("    guard CREATE type=%d slot=%d team=%d leader=%d power=%d cards=%s",
            guard_type, slot, team_id, leader_uid, total_power, placements)
        return [("PVZ.Cmd.Cmd_Guard_Create_SC",
                 bf(1, _guard_bytes(task)) + bf(2, formation))]

    # ---- 密藏掠夺：刷新目标 / 详情 / 取消 / 开打 --------------------------
    def _attack_units(player):
        """玩家“出征阵容”（Guard attack team 2000，缺省时用家园阵容播种）。"""
        placements = _seed(player, _GUARD_ATTACK_TEAM)
        return build_player_units(player, placements, leader_team_id=_GUARD_ATTACK_TEAM)

    def _to_card(unit, pos):
        out = dict(unit)
        out["pos"] = int(pos)
        return {"pos": int(pos), "unit": out}

    def _scale_unit(unit, factor):
        out = dict(unit)
        for key in ("hp", "atk", "def_", "ability", "hit", "dodge", "critical",
                    "tough", "shield", "speed"):
            value = int(out.get(key) or 0)
            if value:
                out[key] = max(0, int(round(value * factor)))
        out["hp"] = max(1, int(out.get("hp") or 1))
        out["power"] = max(1, int(server.card_power(out)))
        return out

    def _cards_from_units(units):
        cards = []
        for card in _unique_guard_cards({"unit": unit} for unit in units):
            unit = card["unit"]
            if not int(unit.get("card_id") or 0):
                continue
            pos = int(unit.get("pos") or 0)
            if pos not in enemy_positions or any(c["pos"] == pos for c in cards):
                pos = next(p for p in enemy_positions if all(c["pos"] != p for c in cards))
            cards.append(_to_card(unit, pos))
        if cards:
            leaders = [c for c in cards if c["unit"].get("is_leader")]
            if not leaders:
                npc_lineups.choose_captain(
                    [c["unit"] for c in cards], server._BATTLE_CARDS, random)
            elif len(leaders) > 1:
                for c in cards:
                    if c is not leaders[0]:
                        c["unit"]["is_leader"] = False
        return cards

    def _member_from_player(cand, now):
        """真人对手：用它在 players.db 里的真实 team1 阵容。"""
        try:
            server.ensure_player_cards(cand)
        except Exception:
            return None
        placements = store.get_formation(cand["uin"], 1) or default_formation_placements(cand)
        units = build_player_units(cand, placements)
        if not units:
            return None
        cards = _cards_from_units(units)
        return {
            "role_id": int(cand.get("role_id") or cand["uin"]),
            "name": str(cand.get("name") or ("Player%d" % (int(cand["uin"]) % 100000))),
            "level": max(1, int(cand.get("level") or 1)),
            "face_id": max(0, int(cand.get("face_id") or 0)),
            "face_url": str(player_face_url(cand) or ""),
            "join_time": int(now),
            "invite_time": 0,
            "power": max(0, sum(int(c["unit"].get("power") or 0) for c in cards)),
            "speed": max(0, sum(int(c["unit"].get("speed") or 0) for c in cards)),
            "cards": cards,
            "is_npc": False,
            "is_beat": False,
        }

    def _npc_member(player, target_power, index, now, avoid_card_ids, used_card_ids):
        """人机对手：随机植物阵容，整体战力按玩家当前出征战力随机缩放。"""
        units = npc_lineups.generate_units(
            server, target_power, positions=enemy_positions, count=max_cards,
            uid_base=_ROB_NPC_ROLE_BASE + (int(player["uin"]) % 100000) * 100 + index * 10,
            avoid_card_ids=avoid_card_ids, used_card_ids=used_card_ids)
        cards = _cards_from_units(units)
        power = max(1, sum(int(u.get("power") or 0) for u in units))
        return {
            "role_id": _ROB_NPC_ROLE_BASE + (int(player["uin"]) % 100000) * 10 + int(index) + 1,
            "name": "密藏掠夺者%d" % (int(index) + 1),
            # member.level 取队内最高植物等级，和卡组等级保持一致，否则客户端概览里的
            # 等级会和底下植物对不上。
            "level": max(1, max((int(u.get("level") or 1) for u in units),
                                default=int(player.get("level") or 1))),
            "face_id": 0,
            "face_url": "",
            "join_time": int(now),
            "invite_time": 0,
            "power": power,
            "speed": max(0, sum(int(u.get("speed") or 0) for u in units)),
            "cards": cards,
            "is_npc": True,
            "is_beat": False,
        }

    def _real_candidates(player, limit=_ROB_TARGET_COUNT):
        """players.db 里其它非 bot、且 team1 阵容有效的真人 uin。"""
        me = int(player["uin"])
        out = []
        try:
            with store._db() as con:
                rows = con.execute(
                    "SELECT uin FROM players WHERE uin<>? ORDER BY last_login DESC, uin",
                    (me,)).fetchall()
        except Exception:
            return out
        for row in rows:
            uin = int(row["uin"])
            if uin == me or _is_bot(uin):
                continue
            placements = store.get_formation(uin, 1)
            if not placements:
                continue
            out.append(uin)
            if len(out) >= limit:
                break
        return out

    def _build_members(player, now):
        attack_units = _attack_units(player)
        reference_power = max(1, sum(int(u.get("power") or 0) for u in attack_units))
        avoid_card_ids = {int(u.get("card_id") or 0) for u in attack_units}
        avoid_card_ids.discard(0)
        members = []
        used_card_ids = set()
        # 本地单机 PoC 没有"连上真正服务器"的概念：players.db 里的其它 uin 只是历史测试账号
        # （星辰 / 吵闹的莫尼卡 / 高瘦的艾美 ...），既不是真的在线玩家、阵容也固定不变，正是
        # 它们占满了 3 个格子才导致对手永远一样。这里固定走随机人机（随机植物 + 随机等级，
        # 战力按玩家出征战力的 50%~170% 浮动）；真接入真实服务器时再调用 _real_candidates() /
        # _member_from_player() 把对手换成真人。
        index = 0
        while len(members) < _ROB_TARGET_COUNT:
            factor = random.uniform(_ROB_POWER_MIN, _ROB_POWER_MAX)
            target_power = min(int(reference_power * _ROB_POWER_MAX),
                               max(1, int(round(reference_power * factor))))
            member = _npc_member(player, target_power, index, now,
                                 avoid_card_ids, used_card_ids)
            if member.get("cards"):
                used_card_ids.update(int(c.get("unit", {}).get("card_id") or 0)
                                     for c in member.get("cards") or [])
                members.append(member)
            else:
                # 极端兜底：连一张可用的 battleCard 植物都没有时，仍然给出结构完整的 member。
                members.append({
                    "role_id": _ROB_NPC_ROLE_BASE + (int(player["uin"]) % 100000) * 10 + index + 1,
                    "name": "密藏掠夺者%d" % (index + 1),
                    "level": max(1, int(player.get("level") or 1)),
                    "face_id": 0, "face_url": "",
                    "join_time": int(now), "invite_time": 0,
                    "power": 1, "speed": 0, "cards": [],
                    "is_npc": True, "is_beat": False,
                })
            index += 1
        return members

    def _rob_guard_id(uin, seed):
        return (int(seed) << 20) | ((int(uin) & 0xFFFFF) << 4) | 1

    def _placeholder_guard(player, guard_id):
        """Info_By_ID 兜底：任何情况下都返回结构完整、guardType 合法的 Dto_Guard。"""
        now = int(time.time())
        return {
            "guard_id": int(guard_id) or _rob_guard_id(player["uin"], now),
            "guard_type": _rob_guard_type(player),
            "finish_time": now,
            "finish": 1,
            "role_id": int(player.get("role_id") or player["uin"] or 0) or 1,
            "name": str(player.get("name") or ""),
            "level": max(1, int(player.get("level") or 1)),
            "face_id": max(0, int(player.get("face_id") or 0)),
            "face_url": str(player_face_url(player) or ""),
            "join_time": now,
            "power": max(1, int(player.get("power") or 1)),
        }

    def _make_entry(player, now):
        members = _build_members(player, now)
        guard_type = _rob_guard_type(player)
        guard = {
            "guard_id": _rob_guard_id(player["uin"], now),
            "guard_type": guard_type,
            "finish_time": now + _ROB_TIME_SECONDS,
            "finish": 0,
            "members": members,
        }
        return {
            "guard": guard,
            "members": members,
            "deadline": now + _ROB_TIME_SECONDS,
            "zone_id": _ROB_ZONE_ID,
            "created": now,
        }

    def _rob_target_resp(player, entry):
        """Cmd_Guard_RefreshRobTarget_SC：zoneId(1) / guard(2) / refreshCount(3)。"""
        return (vf(1, int(entry["zone_id"]))
                + bf(2, _guard_bytes(entry["guard"]))
                + vf(3, int(entry.get("refresh") or _refresh_count(player))))

    def _entry_for(player, now=None, rebuild=True):
        """Stage a private target snapshot; the caller publishes after commit."""
        uin = int(player["uin"])
        entry = _ROB_TARGETS.get(uin)
        if entry is None and rebuild:
            entry = _make_entry(player, int(now or time.time()))
            return entry
        return copy.deepcopy(entry)

    def refresh_rob_target(data, player):
        # Keep commit and target publication in request order: releasing the
        # store transaction must not let an older request replace newer targets.
        with _ROB_LOCK:
            return _refresh_rob_target(data, player)

    def _refresh_rob_target(data, player):
        """原版刷新目标：客户端每次请求都换一批，并按配置扣刷新金币。"""
        now = int(time.time())
        uin = int(player["uin"])
        # The shared store reloads the latest wallet and commits its cost with
        # the counter. Publish the generated targets only after that succeeds.
        try:
            with store.atomic_player(player) as con:
                coin = max(0, int(player.get("coin") or 0))
                if coin < _ROB_REFRESH_COST:
                    log("    guard REFRESH-ROB blocked uin=%s coin=%d < %d",
                        player["uin"], coin, _ROB_REFRESH_COST)
                    return [("PVZ.Cmd.Cmd_SyncCoin_SC", vf(1, coin))]

                count = _refresh_count(player, bump=True)
                entry = _make_entry(player, now)
                entry["refresh"] = count
                con.execute("UPDATE players SET coin=? WHERE uin=?", (coin - _ROB_REFRESH_COST, uin))
                player["coin"] = coin - _ROB_REFRESH_COST
                messages = [("PVZ.Cmd.Cmd_Guard_RefreshRobTarget_SC", _rob_target_resp(player, entry)),
                            ("PVZ.Cmd.Cmd_SyncCoin_SC", vf(1, player["coin"]))]
        except Exception as exc:
            log("    guard REFRESH-ROB rolled back uin=%s: %s", uin, exc)
            return [("PVZ.Cmd.Cmd_LogicError_SC", vf(1, 3001))]
        _ROB_TARGETS[uin] = entry
        guard = entry["guard"]
        log("    guard REFRESH-ROB uin=%s type=%d members=%s refresh=%d coin=%d",
            player["uin"], guard["guard_type"],
            [(m["name"], m["power"], m["is_npc"]) for m in entry["members"]],
            count, player["coin"])
        return messages

    def buy_light(data, player):
        """Cmd_Guard_BuyLight_CS（空包）-> 买灯火。

        Cmd_Guard_BuyLight_SC：field1 repeated Dto_IdNum item（本地免费，留空）；
        field2 buyLightCount。必须再补发 Cmd_SyncLight_SC{light}，否则客户端
        actorModel.holeLight 不更新，onCmd_Guard_BuyLight_SC 里 afterBuyNeedFightIndex
        又会重新 buyLight()，永远进不了 FightRobTarget（就是"点开始战斗没反应"）。
        """
        # The counter and light are one claim: retries while a light is still
        # held do not spend another daily purchase, and failures roll back both.
        with store.atomic_player(player) as con:
            count = _buylight_count_in(con, player)
            cur = max(0, int(player.get("light") or 0))
            new_light = cur
            if cur < _ROB_MAX_RENEW_LIGHT:
                if count >= _ROB_DAILY_BUY_LIGHT:
                    return [("PVZ.Cmd.Cmd_LogicError_SC", vf(1, 9901))]
                count += 1
                new_light = max(cur, int(_ROB_COST_LIGHT))
                con.execute("UPDATE local_guard_buylight SET count=? WHERE uin=?",
                            (count, int(player["uin"])))
                con.execute("UPDATE players SET light=? WHERE uin=?",
                            (new_light, int(player["uin"])))
                player["light"] = new_light
        log("    guard BUY-LIGHT uin=%s -> light=%d count=%d",
            player["uin"], new_light, count)
        # 必须先 SyncLight 把 actorModel.holeLight 顶上去，再回 BuyLight_SC：
        # HoleProxy.onCmd_Guard_BuyLight_SC 里会读 holeLight 决定 afterBuyNeedFightIndex
        # 是否继续 fightTarget()；顺序反了客户端会以为还是没灯，又去 buyLight()。
        return [("PVZ.Cmd.Cmd_SyncLight_SC", vf(1, new_light)),
                ("PVZ.Cmd.Cmd_Guard_BuyLight_SC", vf(2, count))]

    def info_by_id(data, player):
        guard_id = int(parse(data).get(1, 0) or 0)
        guard = None
        with _ROB_LOCK:
            entry = _ROB_TARGETS.get(int(player["uin"]))
            if entry and int(entry["guard"].get("guard_id") or 0) == guard_id:
                guard = copy.deepcopy(entry["guard"])
        if guard is None:
            for task in _guards(player):
                if int(task.get("guard_id") or 0) == guard_id:
                    guard = task
                    break
        if guard is None:
            guard = _placeholder_guard(player, guard_id)
        log("    guard INFO-BY-ID uin=%s guard=%s type=%s",
            player["uin"], guard_id, guard.get("guard_type"))
        return [("PVZ.Cmd.Cmd_Guard_Info_By_ID_SC", bf(1, _guard_bytes(guard)))]

    def cancel_rob(data, player):
        # 原版关闭掠夺窗口后不保留本轮目标；下次打开会重新请求刷新。
        with _ROB_LOCK:
            _ROB_TARGETS.pop(int(player["uin"]), None)
        log("    guard CANCEL-ROB uin=%s", player["uin"])
        return [("PVZ.Cmd.Cmd_Guard_CancelRob_SC", b"")]

    def fight_rob_target(data, player):
        # All target writers use cache -> store lock order. Retain the cache
        # lock through commit and publication so cancel/refresh cannot overtake.
        with _ROB_LOCK:
            return _fight_rob_target(data, player)

    def _fight_rob_target(data, player):
        latest_light = None
        try:
            now = int(time.time())
            index = max(0, int(parse(data).get(1, 0) or 0))
            with store.atomic_player(player) as con:
                latest_light = max(0, int(player.get("light") or 0))
                # A missing/old client wallet cannot start a free battle or seed
                # new targets. Keep the original insufficient-light response.
                if latest_light < _ROB_COST_LIGHT:
                    return [("PVZ.Cmd.Cmd_SyncLight_SC", vf(1, latest_light))]
                entry = _entry_for(player, now)
                members = entry["members"]
                member = (members[index] if index < len(members) else members[0]) if members else None
                if member is not None and member.get("is_beat"):
                    return [("PVZ.Cmd.Cmd_LogicError_SC", vf(1, 11703)),
                            ("PVZ.Cmd.Cmd_SyncLight_SC", vf(1, latest_light))]

                new_light = latest_light - _ROB_COST_LIGHT
                con.execute("UPDATE players SET light=? WHERE uin=?", (new_light, int(player["uin"])))
                player["light"] = new_light
                left = _attack_units(player)
                cards = _unique_guard_cards((member or {}).get("cards") or [])
                right = [dict(c["unit"]) for c in cards if c.get("unit")]
                # Retain the preview's enemy gaps/captain and real puppet battle.
                if left and right:
                    win, left_power, right_power, rounds = server.run_battle(
                        left, right, puppets=server._auto_puppets(player))
                else:
                    win, left_power, right_power, rounds = bool(left and not right), 0, 0, []
                report = server.build_fight_report(player, 0, 0, 0, win, None, False, left, right,
                                                   server.encode_fight_replay(rounds), fight_type=18)
                if member is not None and win:
                    member["is_beat"] = True
                _append_rob_history(con, player, member, win, now)
                deadline = int(entry.get("deadline") or (now + _ROB_TIME_SECONDS))
                finish = bool(members) and all(bool(m.get("is_beat")) for m in members)
                if now >= deadline or member is None:
                    finish = True
                # Successful flags remain until the client refreshes or cancels.
                body = bf(1, report) + boolean(2, win) + boolean(3, finish) + vf(5, deadline)
                for unit in left:
                    uid = int(unit.get("uid") or 0)
                    if uid:
                        body += bf(4, i64(1, uid) + i64(2, max(0, int(unit.get("final_hp", unit.get("hp")) or 0))))
                messages = [("PVZ.Cmd.Cmd_Guard_FightRobTarget_SC", body),
                            ("PVZ.Cmd.Cmd_SyncLight_SC", vf(1, new_light))]
        except Exception as exc:
            log("    guard FIGHT-ROB rolled back uin=%s: %s", player.get("uin"), exc)
            messages = [("PVZ.Cmd.Cmd_LogicError_SC", vf(1, 3001))]
            if latest_light is not None:
                messages.append(("PVZ.Cmd.Cmd_SyncLight_SC", vf(1, latest_light)))
            return messages
        # The cache has not been mutated by a provisional battle. Publish only
        # after all SQL, packet encoding and the outer commit have succeeded.
        _ROB_TARGETS[int(player["uin"])] = entry
        log("    guard FIGHT-ROB uin=%s index=%d vs=%s win=%s power=%d/%d finish=%s light=%d",
            player["uin"], index, (member or {}).get("name"), win,
            left_power, right_power, finish, new_light)
        return messages

    def _claim_error(player, guard_id, code):
        messages = [("PVZ.Cmd.Cmd_LogicError_SC", vf(1, code))]
        try:
            # The original claim has exited. Build a complete current snapshot
            # separately so Info's normal maturity/seed/day updates either commit
            # with its DTO or roll back if the snapshot cannot be encoded.
            with store.atomic_player(player):
                info = get_info(b"", player)
            messages.extend(info)
        except Exception as exc:
            log("    guard TAKE-REWARD resync failed uin=%s guard=%s: %s",
                player["uin"], guard_id, exc)
        return messages

    def take_reward(data, player):
        """Cmd_Guard_TakeReward_CS{guardId} -> 发奖 + Cmd_Guard_TakeReward_SC。"""
        guard_id = int(parse(data).get(1, 0) or 0)
        error_code = 0
        try:
            # All store helpers join this thread's transaction. Reload the wallet
            # before checking the claim, and commit reward and task removal once.
            with store.atomic_player(player) as con:
                task = next((item for item in _guards(player)
                             if int(item.get("guard_id") or 0) == guard_id), None)
                if task is None:
                    log("    guard TAKE-REWARD invalid uin=%s guard=%s", player["uin"], guard_id)
                    error_code = 11703
                else:
                    if (not int(task.get("finish") or 0)
                            and int(task.get("finish_time") or 0) <= int(time.time())):
                        task["finish"] = 1
                        _save_guard(player, task)
                    if not int(task.get("finish") or 0):
                        log("    guard TAKE-REWARD not finished uin=%s guard=%s", player["uin"], guard_id)
                        error_code = 11712
                    else:
                        idx = max(1, min(int(task.get("guard_type") or 1), _HOLE_TYPE_MAX))
                        power = max(0, int(task.get("power") or 0))
                        amount = _rob_reward_amount(idx, power)
                        item_id = _item_by_type.get(idx, 1)
                        applied = server.grant_fuben_reward(player, [(item_id, amount)])
                        # Encode every successful packet before commit too: a bad DTO
                        # must leave the reward available for a complete retry.
                        messages = [("PVZ.Cmd.Cmd_Guard_TakeReward_SC",
                                     i64(1, guard_id) + bf(2, vf(1, item_id) + vf(2, amount)))]
                        if applied:
                            messages.extend(applied)
                        con.execute("DELETE FROM local_guard WHERE uin=? AND guard_id=?",
                                    (int(player["uin"]), guard_id))
                        store.set_formation(player["uin"], _GUARD_TEAM_BASE + int(task.get("slot") or 0), {})
                        store.set_formation_leader(player["uin"], _GUARD_TEAM_BASE + int(task.get("slot") or 0), 0)
        except Exception as exc:
            log("    guard TAKE-REWARD rolled back uin=%s guard=%s: %s", player["uin"], guard_id, exc)
            return _claim_error(player, guard_id, 3001)
        if error_code:
            return _claim_error(player, guard_id, error_code)
        log("    guard TAKE-REWARD uin=%s guard=%s item=%d num=%d power=%d",
            player["uin"], guard_id, item_id, amount, power)
        return messages

    def rob_history(data, player):
        """Cmd_Guard_RobHistory_CS（空包）-> 本地掠夺记录（最近 20 条）。

        Dto_Guard_RobHistory：name(1)/zoneId(2)/level(3)/faceId(4)/qqFaceURL(5)/
        time(6)/robWin(7)，整体作为 Cmd_Guard_RobHistory_SC 的 repeated field 1。
        """
        body = _rob_history_bytes(player)
        log("    guard ROB-HISTORY uin=%s bytes=%d", player["uin"], len(body))
        return [("PVZ.Cmd.Cmd_Guard_RobHistory_SC", body)]

    def defence_history(data, player):
        """Cmd_Guard_DefenceHistory_CS（空包）-> 空 repeated 字段。

        本地 PoC 没有真实的"被别人掠夺"事件，返回空列表让客户端正常显示"暂无记录"；
        不伪造防守记录。
        """
        log("    guard DEFENCE-HISTORY uin=%s -> empty", player["uin"])
        return [("PVZ.Cmd.Cmd_Guard_DefenceHistory_SC", b"")]

    handlers = {
        "PVZ.Cmd.Cmd_Guard_GetGuardFormation_CS": get_guard_formation,
        "PVZ.Cmd.Cmd_Guard_SetGuardFormation_CS": set_guard_formation,
        "PVZ.Cmd.Cmd_Guard_GetFormation_CS": get_formation,
        "PVZ.Cmd.Cmd_Guard_SetFormation_CS": set_formation,
        "PVZ.Cmd.Cmd_Guard_Info_CS": get_info,
        "PVZ.Cmd.Cmd_Guard_Create_CS": create_guard,
        "PVZ.Cmd.Cmd_Guard_RefreshRobTarget_CS": refresh_rob_target,
        "PVZ.Cmd.Cmd_Guard_Info_By_ID_CS": info_by_id,
        "PVZ.Cmd.Cmd_Guard_CancelRob_CS": cancel_rob,
        "PVZ.Cmd.Cmd_Guard_FightRobTarget_CS": fight_rob_target,
        "PVZ.Cmd.Cmd_Guard_BuyLight_CS": buy_light,
        "PVZ.Cmd.Cmd_Guard_TakeReward_CS": take_reward,
        "PVZ.Cmd.Cmd_Guard_RobHistory_CS": rob_history,
        "PVZ.Cmd.Cmd_Guard_DefenceHistory_CS": defence_history,
    }
    server.REQUEST_HANDLERS.update(handlers)
    return handlers
