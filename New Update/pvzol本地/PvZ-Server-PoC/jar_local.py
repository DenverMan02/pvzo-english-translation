"""Local PvZOL '宝罐迷阵' adapter (explicit local rewards, not official odds)."""

import json
import random
import time

ACTIVITY_ID = 68001
VIEW_TYPE = 68
ICON_ID = 10102
HAMMER_ID = 5117
JAR_COUNT = 12
FREE_RESETS = 1
# Local replacement for the missing original cost table. The first two entries
# match the observed UI progression: after the first hit, the next costs x2.
BREAK_COSTS = tuple(range(1, JAR_COUNT + 1))
# 宝罐迷阵奖品：不再只有 叶绿素/时装/植物/金币 —— 加入 钻石(4)、蓝/黄经验豆、四星叶绿素(2104)、
# 傀儡原石(95539)、技能光盘(95532)，以及**每天 0 点轮换**的高稀有植物碎片（见 _jar_groups）。
GROUP_SELECT = (4, 4, 4)
_JAR_GROUP_TEMPLATE = (
    # (叶绿素, 时装, 植物, 固定道具, 金币) —— 第 6 槽留给当天轮换的高稀有碎片
    ((2101, 2), (2130, 5), (1103, 1), (2018, 3), (1, 5000)),
    ((2101, 4), (2130, 10), (1103, 2), (2018, 6), (1, 7500)),
    ((2101, 8), (2130, 20), (1103, 3), (2018, 10), (1, 10000)),
)
_JAR_GROUP_EXTRAS = (((4, 5), (2002, 3)), ((4, 10), (2003, 4)), ((4, 20), (2104, 2)))
_JAR_FALLBACK_FRAGMENTS = (54900, 55800)


def _jar_groups(server):
    """Today's three prize groups; the last slot is a HIGH-RARITY fragment that rotates at 0点."""
    try:
        picks = server.daily_fragments(2, salt=11)
    except Exception:
        picks = _JAR_FALLBACK_FRAGMENTS
    groups = []
    for base, extras in zip(_JAR_GROUP_TEMPLATE, _JAR_GROUP_EXTRAS):
        group = list(base) + list(extras)
        group.append((picks[len(groups) % len(picks)], 2 + len(groups)))
        groups.append(tuple(group))
    return tuple(groups)


MILESTONES = ((3, ((1, 3000), (2002, 2))), (9, ((1, 9000), (2003, 3))),
              (30, ((1, 30000), (2104, 1))), (60, ((1, 60000), (4, 30))))

# Use the same wallet and HUD fields as the server's currency reward mapping.
# Keep the write in the claim transaction so a retry cannot grant it twice.
_CURRENCY_REWARDS = {
    1: ("coin", "PVZ.Cmd.Cmd_SyncCoin_SC"),
    4: ("present_ticket", "PVZ.Cmd.Cmd_SyncPresentTicket_SC"),
}


def _state(server, player):
    today = time.strftime("%Y%m%d", time.localtime())
    with server.store._db() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS local_hitjar ("
            "uin INTEGER PRIMARY KEY, day TEXT NOT NULL, daily_taken INTEGER NOT NULL DEFAULT 0, "
            "selection TEXT NOT NULL DEFAULT '[]', broken TEXT NOT NULL DEFAULT '{}', "
            "history TEXT NOT NULL DEFAULT '[]', "
            "reset_count INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0, "
            "milestones_taken INTEGER NOT NULL DEFAULT 0)"
        )
        con.execute("INSERT OR IGNORE INTO local_hitjar(uin,day) VALUES(?,?)", (player["uin"], today))
        con.execute(
            "UPDATE local_hitjar SET day=?,daily_taken=0,selection='[]',broken='{}',"
            "reset_count=0 WHERE uin=? AND day<>?",
            (today, player["uin"], today),
        )
        return con.execute("SELECT * FROM local_hitjar WHERE uin=?", (player["uin"],)).fetchone()


def _pair(server, item_id, count):
    return server.pb_varint_field(1, item_id) + server.pb_varint_field(2, count)


def _item_changes(server, values):
    if not values:
        return []
    return [("PVZ.Cmd.Cmd_Item_Update_SC", b"".join(
        server.pb_bytes(1, server.build_item_dto(item_id, count))
        for item_id, count in values))]


def _selection_dto(server, group_idx, item_idx, break_id=0):
    return (server.pb_varint_field(1, group_idx)
            + server.pb_varint_field(2, item_idx)
            + server.pb_varint_field(3, break_id))


def _selection_from(row):
    return [tuple(x) for x in json.loads(row["selection"] or "[]")]


def _broken_from(row):
    return {int(k): int(v) for k, v in json.loads(row["broken"] or "{}").items()}


def common_entry(server, player):
    """Dto_FlexibleActivityCommon body for appending as activity list field 1."""
    now = int(time.time())
    return (server.pb_varint_field(1, ACTIVITY_ID)
            + server.pb_varint_field(2, 26)
            + server.pb_varint_field(3, ICON_ID)
            + server.pb_varint_field(4, VIEW_TYPE)
            + server.pb_varint_field(5, now - 86400)
            + server.pb_varint_field(6, 2145916800))


def get_stat(server, player):
    """FlexibleActivity_26: catalog, selected rewards, costs, free claim, milestones."""
    row = _state(server, player)
    body = server.pb_varint_field(1, ACTIVITY_ID)
    for group_idx, offers in enumerate(_jar_groups(server)):
        group = server.pb_varint_field(1, GROUP_SELECT[group_idx]) + server.pb_bool(3, True)
        for item_id, count in offers:
            group += server.pb_bytes(2, _pair(server, item_id, count)
                                     + server.pb_varint_field(3, 1000))
        body += server.pb_bytes(2, group)
    for cost in BREAK_COSTS:
        body += server.pb_bytes(3, _pair(server, HAMMER_ID, cost))
    body += server.pb_varint_field(5, FREE_RESETS)
    for threshold, rewards in MILESTONES:
        reward = server.pb_varint_field(1, threshold)
        for item_id, count in rewards:
            reward += server.pb_bytes(2, _pair(server, item_id, count))
        reward += server.pb_bool(101, bool(int(row["milestones_taken"]) & (1 << MILESTONES.index((threshold, rewards)))))
        body += server.pb_bytes(6, reward)
    daily = server.pb_bytes(1, _pair(server, HAMMER_ID, 1)) + server.pb_bool(101, bool(row["daily_taken"]))
    body += server.pb_bytes(7, daily)
    body += server.pb_varint_field(101, int(time.time()))
    body += server.pb_varint_field(102, int(row["reset_count"]))
    body += server.pb_varint_field(103, int(row["score"]))
    broken = _broken_from(row)
    for idx, (group_idx, item_idx) in enumerate(_selection_from(row)):
        break_id = next((jar for jar, selected_idx in broken.items() if selected_idx == idx), 0)
        body += server.pb_bytes(104, _selection_dto(server, group_idx, item_idx, break_id))
    return [("PVZ.Cmd.FlexibleActivity_26", body)]


def _grant(con, player, rewards):
    """Apply wallet or stackable bag rewards inside the existing claim transaction."""
    changes, currencies = [], {}
    for item_id, count in rewards:
        if item_id in _CURRENCY_REWARDS:
            field, _sc = _CURRENCY_REWARDS[item_id]
            done = con.execute("UPDATE players SET %s=%s+? WHERE uin=? AND %s+?>=0"
                               % (field, field, field), (count, player["uin"], count))
            if done.rowcount != 1:
                raise ValueError("hitjar wallet reward could not be applied")
            currencies[item_id] = int(con.execute("SELECT %s FROM players WHERE uin=?"
                                                  % field, (player["uin"],)).fetchone()[0])
        else:
            old = con.execute("SELECT count FROM items WHERE uin=? AND item_id=?",
                              (player["uin"], item_id)).fetchone()
            new_count = (int(old[0]) if old else 0) + count
            if new_count < 0:
                raise ValueError("hitjar item would go negative")
            con.execute("INSERT INTO items(uin,item_id,count) VALUES(?,?,?) "
                        "ON CONFLICT(uin,item_id) DO UPDATE SET count=excluded.count",
                        (player["uin"], item_id, new_count))
            changes.append((item_id, new_count))
    return changes, currencies


def _sync_grant(server, player, changes, currencies):
    msgs = _item_changes(server, changes)
    for item_id, balance in currencies.items():
        field, sc = _CURRENCY_REWARDS[item_id]
        player[field] = balance
        msgs.append((sc, server.pb_varint_field(1, balance)))
    return msgs


def register(server):
    """Register activity-26 actions. Hook list/stat dispatch separately."""
    store = server.store
    parse = server.parse_varint_fields
    vf = server.pb_varint_field
    bf = server.pb_bytes

    def belongs(data):
        return int(parse(data).get(1, 0)) == ACTIVITY_ID

    def daily(data, player):
        if not belongs(data):
            return []
        _state(server, player)
        with store._db() as con:
            con.execute("BEGIN IMMEDIATE")
            done = con.execute("UPDATE local_hitjar SET daily_taken=1 WHERE uin=? AND daily_taken=0",
                               (player["uin"],))
            if not done.rowcount:
                return []
            changes, currencies = _grant(con, player, ((HAMMER_ID, 1),))
        body = vf(1, ACTIVITY_ID) + bf(2, _pair(server, HAMMER_ID, 1))
        return [("PVZ.Cmd.Cmd_FlexibleActivity26_DailyReward_SC", body),
                *_sync_grant(server, player, changes, currencies)]

    def select(data, player):
        if not belongs(data):
            return []
        raw = server.parse_repeated_submessages(data, 2)
        chosen = [tuple(int(parse(item).get(field, -1)) for field in (1, 2)) for item in raw]
        if len(chosen) != JAR_COUNT or len(set(chosen)) != JAR_COUNT:
            return []
        _groups = _jar_groups(server)
        if any(g < 0 or g >= len(_groups) or i < 0 or i >= len(_groups[g]) for g, i in chosen):
            return []
        if any(sum(group == g for group, _ in chosen) != GROUP_SELECT[g] for g in range(len(_groups))):
            return []
        _state(server, player)
        with store._db() as con:
            row = con.execute("SELECT broken FROM local_hitjar WHERE uin=?", (player["uin"],)).fetchone()
            if _broken_from(row):
                return []
            con.execute("UPDATE local_hitjar SET selection=? WHERE uin=?",
                        (json.dumps(chosen, separators=(',', ':')), player["uin"]))
        body = vf(1, ACTIVITY_ID) + b"".join(bf(2, _selection_dto(server, g, i)) for g, i in chosen)
        return [("PVZ.Cmd.Cmd_FlexibleActivity26_Select_SC", body)]

    def break_jar(data, player):
        if not belongs(data):
            return []
        jar = int(parse(data).get(2, 0))
        if not 1 <= jar <= JAR_COUNT:
            return []
        _state(server, player)
        with store._db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM local_hitjar WHERE uin=?", (player["uin"],)).fetchone()
            chosen = _selection_from(row)
            broken = _broken_from(row)
            if len(chosen) != JAR_COUNT or jar in broken:
                return []
            left = [i for i in range(JAR_COUNT) if i not in broken.values()]
            if not left:
                return []
            hammer = con.execute("SELECT count FROM items WHERE uin=? AND item_id=?",
                                 (player["uin"], HAMMER_ID)).fetchone()
            cost = BREAK_COSTS[len(broken)]
            if not hammer or int(hammer[0]) < cost:
                return []
            chosen_index = random.choice(left)
            group_idx, item_idx = chosen[chosen_index]
            reward = _jar_groups(server)[group_idx][item_idx]
            changes, currencies = _grant(con, player, ((HAMMER_ID, -cost), reward))
            broken[jar] = chosen_index
            history = json.loads(row["history"] or "[]")
            history.insert(0, [int(time.time()), reward[0], reward[1]])
            con.execute("UPDATE local_hitjar SET broken=?,history=?,score=score+1 WHERE uin=?",
                        (json.dumps(broken, separators=(',', ':')),
                         json.dumps(history[:20], separators=(',', ':')), player["uin"]))
        body = (vf(1, ACTIVITY_ID) + vf(2, jar) + vf(3, chosen_index)
                + bf(4, _pair(server, *reward)))
        return [("PVZ.Cmd.Cmd_FlexibleActivity26_BreakJar_SC", body),
                *_sync_grant(server, player, changes, currencies)]

    def reset(data, player):
        if not belongs(data):
            return []
        _state(server, player)
        with store._db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM local_hitjar WHERE uin=?", (player["uin"],)).fetchone()
            if int(row["reset_count"]) >= FREE_RESETS or not _broken_from(row):
                return []
            con.execute("UPDATE local_hitjar SET broken='{}',reset_count=reset_count+1 WHERE uin=?",
                        (player["uin"],))
            count = int(row["reset_count"]) + 1
            selection = _selection_from(row)
        body = vf(1, ACTIVITY_ID) + vf(2, count)
        for g, i in selection:
            body += bf(3, _selection_dto(server, g, i))
        return [("PVZ.Cmd.Cmd_FlexibleActivity26_Reset_SC", body)]

    def milestone(data, player):
        if not belongs(data):
            return []
        index = int(parse(data).get(2, -1))
        if not 0 <= index < len(MILESTONES):
            return []
        _state(server, player)
        threshold, rewards = MILESTONES[index]
        bit = 1 << index
        with store._db() as con:
            con.execute("BEGIN IMMEDIATE")
            done = con.execute("UPDATE local_hitjar SET milestones_taken=milestones_taken|? "
                               "WHERE uin=? AND score>=? AND (milestones_taken&?)=0",
                               (bit, player["uin"], threshold, bit))
            if not done.rowcount:
                return []
            changes, currencies = _grant(con, player, rewards)
        body = vf(1, ACTIVITY_ID) + vf(2, index)
        for item_id, count in rewards:
            body += bf(3, _pair(server, item_id, count))
        return [("PVZ.Cmd.Cmd_FlexibleActivity26_JoinReward_SC", body),
                *_sync_grant(server, player, changes, currencies)]

    def history(data, player):
        if not belongs(data):
            return []
        row = _state(server, player)
        body = vf(1, ACTIVITY_ID)
        for timestamp, item_id, count in json.loads(row["history"] or "[]"):
            entry = (vf(1, player["uin"])
                     + server.pb_string(2, player.get("name", "本地玩家"))
                     + vf(3, timestamp)
                     + bf(4, _pair(server, item_id, count)))
            body += bf(2, entry)
        return [("PVZ.Cmd.Cmd_FlexibleActivity26_History_SC", body)]

    handlers = {
        "BreakJar": break_jar,
        "Reset": reset,
        "Select": select,
        "History": history,
        "JoinReward": milestone,
        "DailyReward": daily,
    }
    for name, handler in handlers.items():
        server.REQUEST_HANDLERS[f"PVZ.Cmd.Cmd_FlexibleActivity26_{name}_CS"] = handler
    return handlers
