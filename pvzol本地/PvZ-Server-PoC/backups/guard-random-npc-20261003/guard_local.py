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
  * NPC：没有足够真人时，用玩家当前出征阵容按 80% / 100% / 120% 缩放生成人机。

三个 member 都带完整 cardInfo（每条恰好一个 isLeader=true、不写 avatarId，避免
QBMovieUtil.updateAvatar 拿到 null 再 #1009），GetFightInfo(type=18) 再把选中
member 的卡组当作敌方阵容返回，客户端就能正常预览 + 出战。
"""

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
_ROB_SCALE_FACTORS = (0.8, 1.0, 1.2)   # 三个 NPC 目标 = 玩家出征战力的 80% / 100% / 120%
_ROB_NPC_ROLE_BASE = 800000000   # 合成 roleId 前缀，避开 bots_local 的 900000..999999 段
_ROB_ZONE_ID = 1

# 进程内缓存的掠夺目标：uin -> {"guard": {...}, "members": [...], "deadline": int, ...}。
# 掠夺是“刷新 -> 预览 -> 打”的短流程，同一个进程里保存最近一次刷新的目标即可；
# 服务端重启后玩家重新刷新一次就重建，不落库。
_ROB_TARGETS = {}


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

def _preview_placements(server, player):
    """出征阵容（team 2000）；缺省时用家园阵容播种，保证和 FightRobTarget 用的是同一套。"""
    store = server.store
    saved = store.get_formation(player["uin"], _GUARD_ATTACK_TEAM) or {}
    owned = {int(uid) for uid, _cid, _lvl, _exp in store.get_cards(player["uin"])}
    placements = {}
    entries = saved.items() if isinstance(saved, dict) else saved
    for pos, uid in entries:
        try:
            pos, uid = int(pos), int(uid)
        except (TypeError, ValueError):
            continue
        if pos > 0 and uid in owned:
            placements[pos] = uid
    if not placements:
        placements = server.default_formation_placements(player) or {}
        if placements:
            store.set_formation(player["uin"], _GUARD_ATTACK_TEAM, placements)
            leader = store.get_formation_leader(player["uin"], 1)
            if leader in placements.values():
                store.set_formation_leader(player["uin"], _GUARD_ATTACK_TEAM, leader)
    return placements


def rob_fight_preview(server, player, index):
    """BattleTargetType_Guard(18) 的 GetFightInfo 数据。

    返回 (formation_bytes, [Dto_EnemyCardInfo bytes], enemy_total_power)。敌方是 RefreshRobTarget
    缓存里第 index 个目标 member 的卡组（优先真人、否则按战力缩放出来的人机）；formation 是玩家
    自己的出征阵容。没有缓存（客户端没刷新就开打）时仍返回合法的空敌方列表，不抛异常。
    """
    entry = _ROB_TARGETS.get(int(player["uin"]))
    members = (entry or {}).get("members") or []
    member = None
    if members:
        index = int(index or 0)
        member = members[index] if 0 <= index < len(members) else members[0]
    placements = _preview_placements(server, player)
    formation = server.build_formation_info(player, _GUARD_ATTACK_TEAM, placements)
    cards = []
    total_power = 0
    for card in (member or {}).get("cards") or []:
        unit = card.get("unit") or {}
        cid = int(unit.get("card_id") or 0)
        if cid <= 0 or cid not in server._BATTLE_CARDS:
            continue
        total_power += max(0, int(unit.get("power") or 0))
        cards.append(server._enemy_card_dto(int(card.get("pos") or 0),
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
        """Keep at most ``max_cards`` owned cards on distinct positive positions.

        The Guard grid lives in its own slot namespace, so positions are kept
        exactly as the client sent them (only non-positive / duplicate slots are
        dropped) instead of being forced onto the home team's _FORMATION_POS.
        """
        owned = _owned(player)
        placements = {}
        entries = raw.items() if isinstance(raw, dict) else raw
        for pos, uid in entries:
            try:
                pos, uid = int(pos), int(uid)
            except (TypeError, ValueError):
                continue
            if pos <= 0 or uid not in owned or pos in placements:
                continue
            if len(placements) >= max_cards:
                break
            placements[pos] = uid
        return placements

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

    def _member_bytes(member):
        """Dto_Guard_Member：roleId/name/level/faceId/qqFaceURL/joinTime/inviteTime/
        power/speed + cardInfo(10)=[Dto_EnemyCardInfo]（avatarId 留空）。"""
        role_id = int(member.get("role_id") or 0) or 1
        body = (i64(1, role_id)
                + sf(2, str(member.get("name") or "Player"))
                + vf(3, max(1, int(member.get("level") or 1)))
                + vf(4, max(0, int(member.get("face_id") or 0)))
                + sf(5, str(member.get("face_url") or ""))
                + vf(6, max(0, int(member.get("join_time") or 0)))
                + vf(7, max(0, int(member.get("invite_time") or 0)))
                + vf(8, max(0, int(member.get("power") or 0)))
                + vf(9, max(0, int(member.get("speed") or 0))))
        for card in (member.get("cards") or []):
            unit = card.get("unit") or {}
            card_id = int(unit.get("card_id") or 0)
            if card_id <= 0 or card_id not in server._BATTLE_CARDS:
                continue
            body += bf(10, enemy_card_dto(int(card.get("pos") or 0),
                                          int(unit.get("uid") or 0), card_id,
                                          max(1, int(unit.get("level") or 1)),
                                          unit, bool(unit.get("is_leader"))))
        return body

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
                + boolean(5, bool(int(task.get("finish", 0)))))

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
                 + vf(6, 0) + vf(7, 0))
        log("    guard INFO -> %d guard(s)", len(tasks))
        return [("PVZ.Cmd.Cmd_Guard_Info_SC", body)]

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
        return build_player_units(player, placements)

    def _to_card(unit, pos):
        out = dict(unit)
        out["pos"] = int(pos)
        return {"pos": int(pos), "unit": out}

    def _scale_unit(unit, factor):
        out = dict(unit)
        for key in ("hp", "atk", "def_", "ability", "hit", "dodge", "critical",
                    "tough", "shield"):
            value = int(out.get(key) or 0)
            if value:
                out[key] = max(0, int(round(value * factor)))
        power = int(unit.get("power") or 0)
        out["power"] = max(1, int(round(power * factor))) if power else 1
        out["hp"] = max(1, int(out.get("hp") or 1))
        return out

    def _cards_from_units(units):
        cards = []
        for idx, unit in enumerate(units):
            if not int(unit.get("card_id") or 0):
                continue
            pos = enemy_positions[idx] if idx < len(enemy_positions) else int(unit.get("pos") or 0)
            cards.append(_to_card(unit, pos))
        if cards:
            leaders = [c for c in cards if c["unit"].get("is_leader")]
            if not leaders:
                cards[0]["unit"]["is_leader"] = True
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
            "power": max(0, sum(int(u.get("power") or 0) for u in units)),
            "speed": max(0, sum(int(u.get("speed") or 0) for u in units)),
            "cards": cards,
            "is_npc": False,
            "is_beat": False,
        }

    def _npc_member(player, units, index, factor, now):
        """人机对手：复制玩家出征阵容，按 factor 缩放血量/攻防/战力。"""
        scaled = [_scale_unit(u, factor) for u in units]
        cards = _cards_from_units(scaled)
        power = max(1, sum(int(u.get("power") or 0) for u in scaled))
        return {
            "role_id": _ROB_NPC_ROLE_BASE + (int(player["uin"]) % 100000) * 10 + int(index) + 1,
            "name": "密藏掠夺者%d" % (int(index) + 1),
            "level": max(1, int(player.get("level") or 1)),
            "face_id": 0,
            "face_url": "",
            "join_time": int(now),
            "invite_time": 0,
            "power": power,
            "speed": max(0, sum(int(u.get("speed") or 0) for u in scaled)),
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
        units = _attack_units(player)
        members = []
        for uin in _real_candidates(player, _ROB_TARGET_COUNT - len(members)):
            cand, _new = store.get_or_create_player(uin)
            member = _member_from_player(cand, now)
            if member:
                members.append(member)
        index = 0
        while len(members) < _ROB_TARGET_COUNT:
            factor = _ROB_SCALE_FACTORS[min(index, len(_ROB_SCALE_FACTORS) - 1)]
            if units:
                members.append(_npc_member(player, units, index, factor, now))
            else:
                # 玩家一张卡都没有时的兜底：仍然给出结构完整的 member，避免空 member 列表。
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

    def _entry_for(player, now=None, rebuild=True):
        uin = int(player["uin"])
        entry = _ROB_TARGETS.get(uin)
        if entry is None and rebuild:
            entry = _make_entry(player, int(now or time.time()))
            _ROB_TARGETS[uin] = entry
        return entry

    def refresh_rob_target(data, player):
        now = int(time.time())
        count = _refresh_count(player, bump=True)
        entry = _make_entry(player, now)
        entry["refresh"] = count
        _ROB_TARGETS[int(player["uin"])] = entry
        guard = entry["guard"]
        body = (vf(1, int(entry["zone_id"]))
                + bf(2, _guard_bytes(guard))
                + vf(3, count))
        log("    guard REFRESH-ROB uin=%s type=%d members=%s refresh=%d",
            player["uin"], guard["guard_type"],
            [(m["name"], m["power"], m["is_npc"]) for m in entry["members"]], count)
        return [("PVZ.Cmd.Cmd_Guard_RefreshRobTarget_SC", body)]

    def info_by_id(data, player):
        guard_id = int(parse(data).get(1, 0) or 0)
        guard = None
        entry = _ROB_TARGETS.get(int(player["uin"]))
        if entry and int(entry["guard"].get("guard_id") or 0) == guard_id:
            guard = entry["guard"]
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
        _ROB_TARGETS.pop(int(player["uin"]), None)
        log("    guard CANCEL-ROB uin=%s", player["uin"])
        return [("PVZ.Cmd.Cmd_Guard_CancelRob_SC", b"")]

    def fight_rob_target(data, player):
        now = int(time.time())
        index = int(parse(data).get(1, 0) or 0)
        entry = _entry_for(player, now)
        members = entry["members"]
        if index < 0:
            index = 0
        if members:
            member = members[index] if index < len(members) else members[0]
        else:
            member = None

        left = _attack_units(player)
        cards = (member or {}).get("cards") or []
        right = [dict(c["unit"]) for c in cards if c.get("unit")]
        for i, unit in enumerate(right):
            if i < len(enemy_positions):
                unit["pos"] = enemy_positions[i]

        if left and right:
            win, left_power, right_power = server.resolve_battle(left, right)
            rounds = server.simulate_battle(left, right, win)
        else:
            win, left_power, right_power, rounds = bool(left and not right), 0, 0, []
        report = server.build_fight_report(player, 0, 0, 0, win, None, False, left, right,
                                           server.encode_fight_replay(rounds),
                                           fight_type=18)
        if member is not None and win:
            member["is_beat"] = True
        deadline = int(entry.get("deadline") or (now + _ROB_TIME_SECONDS))
        finish = bool(members) and all(bool(m.get("is_beat")) for m in members)
        if now >= deadline:
            finish = True
        if member is None:
            finish = True

        body = bf(1, report) + boolean(2, win) + boolean(3, finish) + vf(5, deadline)
        for unit in left:
            uid = int(unit.get("uid") or 0)
            if uid:
                body += bf(4, i64(1, uid) + i64(2, max(0, int(unit.get("hp") or 0))))
        log("    guard FIGHT-ROB uin=%s index=%d vs=%s win=%s power=%d/%d finish=%s",
            player["uin"], index, (member or {}).get("name"), win,
            left_power, right_power, finish)
        return [("PVZ.Cmd.Cmd_Guard_FightRobTarget_SC", body)]

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
    }
    server.REQUEST_HANDLERS.update(handlers)
    return handlers