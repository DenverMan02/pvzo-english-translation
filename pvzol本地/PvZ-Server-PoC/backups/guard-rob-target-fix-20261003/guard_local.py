"""密藏守护 / Guard（MODE_HOLE_DEF = 19）适配器。

客户端对密藏守护监听两组协议：

    Cmd_Guard_GetGuardFormation_CS                       -> Cmd_Guard_SyncGuardFormation_SC
    Cmd_Guard_SetGuardFormation_CS{index, formation}     -> Cmd_Guard_SyncGuardFormation_SC
    Cmd_Guard_GetFormation_CS                            -> Cmd_Guard_SyncFormation_SC
    Cmd_Guard_SetFormation_CS{formation}                 -> Cmd_Guard_SyncFormation_SC
    Cmd_Guard_Info_CS                                    -> Cmd_Guard_Info_SC
    Cmd_Guard_Create_CS{guardType, formation, index}     -> Cmd_Guard_Create_SC

前 4 条只负责“出征前编辑阵容”，把 Guard 阵型存进 formation 表里独立的槽位
命名空间（team_id = 1000 + index），不再和家园队伍（team 1）互相覆盖。GET 时若
该槽还没有存档，就复制玩家的家园阵容播种一次，保证客户端拿到非空 formation。

后 2 条对应“点出征 -> 建立密藏守护”。原始服务端没有实现，Create_CS 落到通用
兜底，回包里 Dto_Guard.guardType = 0，客户端 HoleTemplateFactory.getHoleType(0)
返回 null，HoleInfoCellMediator.reset() 在 #1009 崩掉。这里补上真实实现：
guardType 原样使用客户端传来的 HoleUseTypeTemplate.id(1..16)；guardId /
member.roleId / formation.leaderUid 全部显式编码成非空 Int64；创建出来的守护
任务落进 local_guard 表，Info_CS 再按序读回，客户端 4 个格子即可正常刷新。
"""

import time

_GUARD_TEAM_BASE = 1000
_GUARD_ATTACK_TEAM = 2000
_GUARD_SLOT_MAX = 31
_FALLBACK_POS = (3, 7, 9, 11, 13, 15, 17)

# holeUseType.txt：id 1..16，每 4 个一组共享 costTime（分钟）。
_HOLE_TYPE_MAX = 16
_HOLE_COST_MINUTES = (180, 600, 1320, 1380)


def _cost_minutes(guard_type):
    """成长时间（分钟）按 holeUseType.txt 的 1-4 / 5-8 / 9-12 / 13-16 分档。"""
    idx = (int(guard_type) - 1) // 4
    if 0 <= idx < len(_HOLE_COST_MINUTES):
        return _HOLE_COST_MINUTES[idx]
    return _HOLE_COST_MINUTES[0]


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
        placements = _sanitize(player, server.default_formation_placements(player))
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
        formation = server.build_formation_info(player, team_id, placements)
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
        formation = server.build_formation_info(player, _GUARD_ATTACK_TEAM, placements)
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

    def _guard_bytes(task):
        """Dto_Guard：guardType 必须落在 holeUseType.txt 的有效 id(1..16) 上，
        guardId / member.roleId 必须是真实 Int64，否则客户端 reset() 会 #1009。"""
        guard_type = int(task.get("guard_type") or 0)
        if guard_type < 1 or guard_type > _HOLE_TYPE_MAX:
            guard_type = 1
        member = (i64(1, int(task.get("role_id") or 0))
                  + sf(2, str(task.get("name") or "Player"))
                  + vf(3, max(1, int(task.get("level") or 1)))
                  + vf(4, max(0, int(task.get("face_id") or 0)))
                  + sf(5, str(task.get("face_url") or ""))
                  + vf(6, max(0, int(task.get("join_time") or 0)))
                  + vf(8, max(0, int(task.get("power") or 0)))
                  + vf(9, 0))
        return (i64(1, int(task.get("guard_id") or 0))
                + vf(2, guard_type)
                + vf(3, max(0, int(task.get("finish_time") or 0)))
                + bf(4, member)
                + boolean(5, bool(int(task.get("finish", 0)))))

    def get_info(data, player):
        now = int(time.time())
        tasks = _guards(player)
        body = b""
        for task in tasks:
            team_id = int(task.get("team_id")
                          or (_GUARD_TEAM_BASE + int(task.get("slot") or 0)))
            placements = _stored(player, team_id) or _seed(player, team_id)
            formation = server.build_formation_info(player, team_id, placements)
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

        formation = server.build_formation_info(player, team_id, placements)
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
            "face_url": str(server.player_face_url(player) or ""),
            "power": total_power,
            "join_time": now,
        }
        _save_guard(player, task)
        log("    guard CREATE type=%d slot=%d team=%d leader=%d power=%d cards=%s",
            guard_type, slot, team_id, leader_uid, total_power, placements)
        return [("PVZ.Cmd.Cmd_Guard_Create_SC",
                 bf(1, _guard_bytes(task)) + bf(2, formation))]

    handlers = {
        "PVZ.Cmd.Cmd_Guard_GetGuardFormation_CS": get_guard_formation,
        "PVZ.Cmd.Cmd_Guard_SetGuardFormation_CS": set_guard_formation,
        "PVZ.Cmd.Cmd_Guard_GetFormation_CS": get_formation,
        "PVZ.Cmd.Cmd_Guard_SetFormation_CS": set_formation,
        "PVZ.Cmd.Cmd_Guard_Info_CS": get_info,
        "PVZ.Cmd.Cmd_Guard_Create_CS": create_guard,
    }
    server.REQUEST_HANDLERS.update(handlers)
    return handlers