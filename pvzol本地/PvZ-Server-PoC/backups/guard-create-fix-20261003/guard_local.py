"""密藏守护 / Guard（MODE_HOLE_DEF = 19）阵型适配器。

客户端对密藏守护只监听 ``Cmd_Guard_SyncGuardFormation_SC``：

    Cmd_Guard_GetGuardFormation_CS                          -> 空包
    Cmd_Guard_SetGuardFormation_CS{index(1), formation(2)}
    SysGuard_SC{index(1), formation(2)}  <- 唯一的监听

服务端此前两个消息都没有实现：Get 落到通用兜底，回包名被写成
``Cmd_Guard_GetGuardFormation_SC``（客户端不监听）；Set 同样没有落库。
于是 Guard 的 formation VO 永远为空：左侧卡片点不动、队长也设不上。

本模块把 Guard 阵型存进 formation 表里独立的槽位命名空间
（team_id = 1000 + index），不再和家园队伍（team 1）互相覆盖。
GET 时若该槽还没有存档，就复制玩家的家园阵容播种一次，
保证客户端拿到非空 formation，卡片列表能正常进入编辑状态。
"""

_GUARD_TEAM_BASE = 1000
_GUARD_ATTACK_TEAM = 2000
_GUARD_SLOT_MAX = 31
_FALLBACK_POS = (3, 7, 9, 11, 13, 15, 17)


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

    handlers = {
        "PVZ.Cmd.Cmd_Guard_GetGuardFormation_CS": get_guard_formation,
        "PVZ.Cmd.Cmd_Guard_SetGuardFormation_CS": set_guard_formation,
        "PVZ.Cmd.Cmd_Guard_GetFormation_CS": get_formation,
        "PVZ.Cmd.Cmd_Guard_SetFormation_CS": set_formation,
    }
    server.REQUEST_HANDLERS.update(handlers)
    return handlers