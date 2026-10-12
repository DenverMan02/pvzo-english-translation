"""PvZOL local single-player treasure adapter.

This is a deliberate local adaptation, not a reconstruction of the official
two-player/raid and random-loot server. Rewards are only those stated in the
client's ItemInfo.txt treasure-map descriptions.
"""

import sqlite3
import time

MAP_REWARDS = {
    3500: ((1, 20000), (2101, 2)),
    3502: ((1, 30000), (2101, 4), (2130, 10)),
    3504: ((1, 40000), (2101, 6), (2130, 10)),
    3506: ((1, 50000), (2101, 8), (2130, 20)),
    3508: ((1, 60000), (2101, 10), (2130, 30)),
}
DAILY_MAPS = 3
SEARCH_SECONDS = 30
CHILDREN_ACTIVITY_ID = 39001
CHILDREN_VIEW_TYPE = 39
# The original event reward table is not in either resource pack. These are
# explicitly local daily tiers; only the user's best score unlocks them.
# 每档奖励从"只有金币"扩展为 金币 + 钻石 + 蓝/黄经验豆 + 高稀有植物碎片。
# item id: 1=金币, 4=钻石, 2002=蓝经验豆, 2003=黄经验豆, 5xxxx=植物碎片。
CHILDREN_REWARDS = (
    (10,  [(1, 2000), (4, 3), (2002, 3), (53900, 1)]),
    (20,  [(1, 4000), (4, 6), (2003, 3), (54900, 1)]),
    (30,  [(1, 6000), (4, 9), (2002, 5), (54400, 2)]),
    (40,  [(1, 8000), (4, 12), (2003, 5), (55800, 2)]),
    (50,  [(1, 10000), (4, 15), (2002, 8), (57000, 3)]),
)


def _grant_item_inline(con, uin, item_id, delta):
    """Add `delta` of a bag item (exp bean / plant fragment) to a player inside an open txn.
    Mirror of register()'s nested change_item, but module-level so the event handlers can use it."""
    row = con.execute("SELECT count FROM items WHERE uin=? AND item_id=?", (uin, item_id)).fetchone()
    count = (int(row[0]) if row else 0) + delta
    if count < 0:
        count = 0
    con.execute(
        "INSERT INTO items(uin,item_id,count) VALUES(?,?,?) "
        "ON CONFLICT(uin,item_id) DO UPDATE SET count=excluded.count",
        (uin, item_id, count))
    return count


def register(server):
    """Install handlers into pvz_poc_server.REQUEST_HANDLERS after its definition."""
    store = server.store
    vf = server.pb_varint_field
    sf = server.pb_string
    bf = server.pb_bytes
    parse = server.parse_varint_fields

    def ensure(con, player):
        con.execute(
            "CREATE TABLE IF NOT EXISTS local_treasure ("
            "uin INTEGER PRIMARY KEY, day TEXT NOT NULL, claimed INTEGER NOT NULL DEFAULT 0, "
            "used INTEGER NOT NULL DEFAULT 0, house_id INTEGER NOT NULL DEFAULT 0, "
            "map_id INTEGER NOT NULL DEFAULT 0, end_at INTEGER NOT NULL DEFAULT 0, "
            "last_map_id INTEGER NOT NULL DEFAULT 0, last_time INTEGER NOT NULL DEFAULT 0)"
        )
        day = time.strftime("%Y%m%d", time.localtime())
        con.execute("INSERT OR IGNORE INTO local_treasure(uin,day) VALUES(?,?)", (player["uin"], day))
        # A search may straddle midnight. Keep it active; reset the daily counters
        # only after it has settled, so a date change cannot erase owed rewards.
        con.execute(
            "UPDATE local_treasure SET day=?,claimed=0,used=0 WHERE uin=? AND day<>? "
            "AND map_id=0", (day, player["uin"], day)
        )
        return con.execute("SELECT * FROM local_treasure WHERE uin=?", (player["uin"],)).fetchone()

    def item_count(con, uin, item_id):
        row = con.execute("SELECT count FROM items WHERE uin=? AND item_id=?", (uin, item_id)).fetchone()
        return int(row[0]) if row else 0

    def change_item(con, uin, item_id, delta):
        count = item_count(con, uin, item_id) + delta
        if count < 0:
            raise ValueError("treasure item would go negative")
        con.execute(
            "INSERT INTO items(uin,item_id,count) VALUES(?,?,?) "
            "ON CONFLICT(uin,item_id) DO UPDATE SET count=excluded.count",
            (uin, item_id, count),
        )
        return count

    def item_sync(changes):
        if not changes:
            return []
        body = b"".join(bf(1, server.build_item_dto(item_id, count)) for item_id, count in changes)
        return [("PVZ.Cmd.Cmd_Item_Update_SC", body)]

    def treasure_report(player, map_id, ended_at):
        rewards = b"".join(bf(4, server.build_item_dto(item_id, count))
                           for item_id, count in MAP_REWARDS[map_id])
        left = vf(1, int(player.get("role_id") or player["uin"])) + sf(2, str(player.get("name") or "Player")) + rewards
        right = vf(1, 0) + sf(2, "独行寻宝")
        return vf(1, 5) + bf(2, b"") + bf(3, left) + bf(4, right) + vf(5, ended_at)

    def settle(player):
        """Award an expired search exactly once in a single SQLite transaction."""
        changes, coin, report = [], None, None
        with store._db() as con:
            row = ensure(con, player)
            if row["map_id"] and int(row["end_at"]) <= int(time.time()):
                uin, map_id = player["uin"], int(row["map_id"])
                for item_id, count in MAP_REWARDS[map_id]:
                    if item_id == 1:
                        con.execute("UPDATE players SET coin=coin+? WHERE uin=?", (count, uin))
                        coin = int(con.execute("SELECT coin FROM players WHERE uin=?", (uin,)).fetchone()[0])
                    else:
                        changes.append((item_id, change_item(con, uin, item_id, count)))
                con.execute(
                    "UPDATE local_treasure SET map_id=0,house_id=0,end_at=0,"
                    "last_map_id=?,last_time=? WHERE uin=?",
                    (map_id, int(time.time()), uin),
                )
                report = treasure_report(player, map_id, int(time.time()))
        msgs = item_sync(changes)
        if coin is not None:
            player["coin"] = coin
            msgs.insert(0, ("PVZ.Cmd.Cmd_SyncCoin_SC", vf(1, coin)))
        if report is not None:
            msgs.append(("PVZ.Cmd.Cmd_Treasure_SyncFightReport_SC", bf(1, report)))
        return msgs

    def state(player):
        with store._db() as con:
            return ensure(con, player)

    def sync(player):
        row = state(player)
        active = bool(row["map_id"])
        body = (
            vf(1, max(0, DAILY_MAPS - int(row["used"])))
            + vf(2, 0)  # local solo mode has no PvP seize/raid
            + vf(4, int(active))
            + vf(5, not bool(row["claimed"]))
            + vf(6, int(row["end_at"]) if active else 0)
            + vf(7, 0)
        )
        return ("PVZ.Cmd.Cmd_SyncTreasure_SC", body)

    def house(player, house_id):
        row = state(player)
        own = int(row["house_id"]) == house_id and bool(row["map_id"])
        side = b""
        if own:
            side = (
                vf(1, int(player.get("role_id") or player["uin"]))
                + sf(2, str(player.get("name") or "Player"))
                + vf(3, int(player.get("avatar_plant_id") or 1200))
                + vf(6, int(row["end_at"]) - SEARCH_SECONDS)
                + vf(7, int(row["map_id"]))
            )
        # Both sides must be present as empty protobuf messages: the client
        # directly dereferences .left.roleId and .right.roleId.
        return vf(1, house_id) + bf(4, side) + bf(5, b"") + vf(6, int(row["end_at"]) - SEARCH_SECONDS if own else 0)

    def pre(player):
        return settle(player)

    def is_open(data, player):
        return pre(player) + [("PVZ.Cmd.Cmd_TreasureIsOpen_SC", vf(1, 1))]

    def begin(data, player):
        return pre(player) + [sync(player), ("PVZ.Cmd.Cmd_SyncHallPlayerCount_SC", vf(1, 1))]

    def end(data, player):
        return pre(player)

    def take_map(data, player):
        msgs = pre(player)
        count = None
        with store._db() as con:
            row = ensure(con, player)
            if not row["claimed"]:
                count = change_item(con, player["uin"], 3500, DAILY_MAPS)
                con.execute("UPDATE local_treasure SET claimed=1 WHERE uin=?", (player["uin"],))
        if count is None:
            return msgs + [sync(player)]
        return msgs + [
            ("PVZ.Cmd.Cmd_Treasure_TakeMap_SC", vf(1, 3500)),
            *item_sync([(3500, count)]), sync(player),
        ]

    def page(data, player):
        msgs = pre(player)
        args = parse(data)
        start = max(1, min(999, int(args.get(1, 1))))
        size = max(1, min(9, int(args.get(2, 9))))
        body = b"".join(bf(1, house(player, i)) for i in range(start, start + size)) + vf(2, 1)
        return msgs + [("PVZ.Cmd.Cmd_Treasure_SearchHousePage_SC", body), sync(player)]

    def search_house(data, player):
        msgs = pre(player)
        house_id = max(1, int(parse(data).get(1, 1)))
        return msgs + [("PVZ.Cmd.Cmd_Treasure_SearchHouse_SC", bf(1, house(player, house_id)))]

    def enter(data, player):
        msgs = pre(player)
        args = parse(data)
        house_id = max(1, int(args.get(1, 1)))
        uin = player["uin"]
        map_id, count = 0, 0
        with store._db() as con:
            row = ensure(con, player)
            if not row["map_id"] and int(row["used"]) < DAILY_MAPS:
                map_id = next((i for i in MAP_REWARDS if item_count(con, uin, i) > 0), 0)
                if map_id:
                    count = change_item(con, uin, map_id, -1)
                    con.execute(
                        "UPDATE local_treasure SET used=used+1,house_id=?,map_id=?,end_at=? WHERE uin=?",
                        (house_id, map_id, int(time.time()) + SEARCH_SECONDS, uin),
                    )
        if not map_id:
            return msgs + [sync(player)]
        return msgs + [
            ("PVZ.Cmd.Cmd_Treasure_EnterHouse_SC", bf(1, house(player, house_id))),
            *item_sync([(map_id, count)]), sync(player),
        ]

    def leave(data, player):
        # Explicit exit abandons the search. Consumed maps are not refunded,
        # matching the local mode's displayed choice to leave the house.
        msgs = pre(player)
        with store._db() as con:
            ensure(con, player)
            con.execute("UPDATE local_treasure SET map_id=0,house_id=0,end_at=0 WHERE uin=?", (player["uin"],))
        return msgs + [("PVZ.Cmd.Cmd_Treasure_ExitHouse_SC", b""), sync(player)]

    def formation(data, player, seize=False):
        msgs = pre(player)
        placements = server.default_formation_placements(player)
        info = server.build_formation_info(player, 1, placements)
        suffix = "Seize" if seize else "Search"
        return msgs + [(f"PVZ.Cmd.Cmd_Treasure_Get{suffix}Formation_SC", bf(1, info))]

    def set_search_formation(data, player):
        # The local solo room uses the same one editable team the private server
        # already exposes in the normal battle lineup.
        sub = server.parse_repeated_submessages(data, 1)
        placements = {}
        if sub:
            owned = {int(uid) for uid, *_ in store.get_cards(player["uin"])}
            for cp in server.parse_repeated_submessages(sub[0], 1):
                fields = parse(cp)
                pos, uid = int(fields.get(1, 0)), int(fields.get(2, 0))
                if pos and uid in owned:
                    placements[pos] = uid
        if placements:
            store.set_formation(player["uin"], 1, placements)
        return [("PVZ.Cmd.Cmd_Treasure_SetSearchFormation_SC", b""), *formation(b"", player)]

    def report(data, player):
        msgs = pre(player)
        row = state(player)
        body = (bf(1, treasure_report(player, int(row["last_map_id"]), int(row["last_time"])))
                if row["last_map_id"] else b"")
        return msgs + [("PVZ.Cmd.Cmd_Treasure_GetFightReport_SC", body)]

    handlers = {
        "TreasureIsOpen": is_open,
        "Treasure_Begin": begin,
        "Treasure_End": end,
        "Treasure_TakeMap": take_map,
        "Treasure_SearchHousePage": page,
        "Treasure_SearchHouse": search_house,
        "Treasure_EnterHouse": enter,
        "Treasure_ExitHouse": leave,
        "Treasure_GetSearchFormation": formation,
        "Treasure_GetSeizeFormation": lambda d, p: formation(d, p, True),
        "Treasure_GetFightReport": report,
        "Treasure_SetSearchFormation": set_search_formation,
        "Treasure_SetSeizeFormation": lambda d, p: [("PVZ.Cmd.Cmd_Treasure_SetSeizeFormation_SC", b"")],
    }
    for cmd, handler in handlers.items():
        server.REQUEST_HANDLERS[f"PVZ.Cmd.Cmd_{cmd}_CS"] = handler
    return handlers


def register_children_day(server):
    """Persist the 30-second spaceship game's daily best (client viewType 39)."""
    store = server.store

    def read_best(player):
        return int(_children_state(server, player)["best"])

    def get_best(data, player):
        return server.pb_varint_field(1, read_best(player))

    def report(data, player):
        score = int(server.parse_varint_fields(data).get(1, 0))
        if 0 <= score <= 70:  # ChildrenDayConst.ZOMBIE_TOTAL_COUNT
            _children_state(server, player)
            with store._db() as con:
                con.execute("UPDATE local_children_day SET best=MAX(best,?) WHERE uin=?",
                            (score, player["uin"]))
        return b""  # Cmd_FlexibleActivity_ReportZombieNum_SC is an empty ACK.

    server.REQUEST_HANDLERS.update({
        "PVZ.Cmd.Cmd_FlexibleActivity_GetZombieNum_CS": get_best,
        "PVZ.Cmd.Cmd_FlexibleActivity_ReportZombieNum_CS": report,
    })
    return {"get": get_best, "report": report}


def _children_state(server, player):
    day = time.strftime("%Y%m%d", time.localtime())
    with server.store._db() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS local_children_day ("
            "uin INTEGER PRIMARY KEY, day TEXT NOT NULL, best INTEGER NOT NULL DEFAULT 0, "
            "taken INTEGER NOT NULL DEFAULT 0)"
        )
        con.execute("INSERT OR IGNORE INTO local_children_day(uin,day) VALUES(?,?)",
                    (player["uin"], day))
        con.execute("UPDATE local_children_day SET day=?,best=0,taken=0 WHERE uin=? AND day<>?",
                    (day, player["uin"], day))
        return con.execute("SELECT * FROM local_children_day WHERE uin=?",
                           (player["uin"],)).fetchone()


def children_common_entry(server, player):
    """Dto_FlexibleActivityCommon payload for appending to activity list field 1."""
    now = int(time.time())
    return (server.pb_varint_field(1, CHILDREN_ACTIVITY_ID)
            + server.pb_varint_field(2, 1001)
            + server.pb_varint_field(3, 10074)  # ActivityIcon.txt: 太空大战
            + server.pb_varint_field(4, CHILDREN_VIEW_TYPE)
            + server.pb_varint_field(5, now - 86400)
            + server.pb_varint_field(6, 2145916800))


def children_get_stat(server, player):
    """FlexibleActivity_1001 with score conditions and five daily rewards."""
    row = _children_state(server, player)
    best, taken = int(row["best"]), int(row["taken"])
    body = server.pb_varint_field(1, CHILDREN_ACTIVITY_ID)
    for index, (threshold, rewards) in enumerate(CHILDREN_REWARDS):
        # 一档奖励可能包含多个物品（金币/钻石/经验豆/碎片）。
        # Dto_FlexibleActivityReward.items(1) 是 **repeated Dto_IdNum{id(1), num(2)}** ——
        # 每个物品必须是独立的一个 field 1 子消息。旧写法先把多个 (id,num) 拼成一个 blob、
        # 又把这个 blob 整体套进单个 field 1，客户端于是在同一个 Dto_IdNum 里读到 4 个 id：
        #   Error: Bad data format: Dto_IdNum.id cannot be set twice.
        items = b"".join(
            server.pb_bytes(1, server.pb_varint_field(1, int(item_id))
                            + server.pb_varint_field(2, int(count)))
            for item_id, count in rewards)
        reward = (items
                  + server.pb_bool(2, best >= threshold and not (taken & (1 << index)))
                  + server.pb_varint_field(3, int(time.time()) if taken & (1 << index) else 0))
        tracker = server.pb_varint_field(6, threshold) + server.pb_varint_field(7, best)
        condition = (server.pb_bytes(15, tracker) + server.pb_varint_field(6, 1)
                     + server.pb_varint_field(7, int(bool(taken & (1 << index))))
                     + server.pb_bytes(100, reward))
        body += server.pb_bytes(2, condition)
    return [("PVZ.Cmd.FlexibleActivity_1001", body)]


def children_take_reward(server, cmd_data, player):
    """Atomically claim one achieved, unclaimed daily tier; replay gives no coins."""
    args = server.parse_varint_fields(cmd_data)
    if int(args.get(1, 0)) != CHILDREN_ACTIVITY_ID:
        return []
    denied = [("PVZ.Cmd.Cmd_FlexibleActivity_TakeReward_SC", b"")]
    index = int(args.get(2, -1))
    if not 0 <= index < len(CHILDREN_REWARDS):
        return denied
    _children_state(server, player)
    threshold, rewards = CHILDREN_REWARDS[index]
    bit = 1 << index
    coin_delta = 0
    diamond_delta = 0
    item_changes = {}          # item_id -> new count (背包物品：经验豆 / 植物碎片)
    with server.store._db() as con:
        con.execute("BEGIN IMMEDIATE")
        updated = con.execute(
            "UPDATE local_children_day SET taken=taken|? WHERE uin=? AND best>=? AND (taken&?)=0",
            (bit, player["uin"], threshold, bit),
        )
        if not updated.rowcount:
            return denied
        for item_id, count in rewards:
            if item_id == 1:                       # 金币
                coin_delta += count
            elif item_id == 4:                     # 钻石 (gift diamond)
                diamond_delta += count
            else:                                 # 背包物品：经验豆 / 植物碎片
                item_changes[item_id] = _grant_item_inline(con, player["uin"], item_id, count)
        if coin_delta:
            con.execute("UPDATE players SET coin=coin+? WHERE uin=?", (coin_delta, player["uin"]))
        if diamond_delta:
            con.execute("UPDATE players SET present_ticket=present_ticket+? WHERE uin=?",
                        (diamond_delta, player["uin"]))
        coin = int(con.execute("SELECT coin FROM players WHERE uin=?", (player["uin"],)).fetchone()[0])
        present_ticket = int(con.execute(
            "SELECT present_ticket FROM players WHERE uin=?", (player["uin"],)).fetchone()[0])
    player["coin"] = coin
    player["present_ticket"] = present_ticket
    # 发奖回包：items(1) 是 **repeated Dto_IdNum{id(1), num(2)}**，每个物品必须是独立的
    # 一个 field 1 子消息（同 children_get_stat 的修法）。旧写法把 4 个 (id,num) 拼成一个
    # blob 再整体套一层 field 1，客户端于是在同一个 Dto_IdNum 里读到多个 id：
    #   Error: Bad data format: Dto_IdNum.id cannot be set twice.
    items = b"".join(
        server.pb_bytes(1, server.pb_varint_field(1, int(iid))
                        + server.pb_varint_field(2, int(cnt)))
        for iid, cnt in rewards)
    msgs = [("PVZ.Cmd.Cmd_FlexibleActivity_TakeReward_SC", items),
            ("PVZ.Cmd.Cmd_SyncCoin_SC", server.pb_varint_field(1, coin))]
    if diamond_delta:
        msgs.append(("PVZ.Cmd.Cmd_SyncPresentTicket_SC", server.pb_varint_field(1, present_ticket)))
    if item_changes:
        body = b"".join(server.pb_bytes(1, server.build_item_dto(iid, cnt))
                         for iid, cnt in item_changes.items())
        msgs.append(("PVZ.Cmd.Cmd_Item_Update_SC", body))
    return msgs
