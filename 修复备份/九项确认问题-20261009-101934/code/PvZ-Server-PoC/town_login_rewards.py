"""Standalone three-day login-gift activity for the local PvZOL server.

Integration: import this module from pvz_poc_server, append common_entry() to
Cmd_FlexibleActivity_GetActivityList_SC, delegate activity id 81001 in
handle_flexactivity_getstat, and route Cmd_FlexibleActivity_TakeReward_CS to
take_reward(). All helpers take the server module explicitly to avoid imports
that would open a player's real database during isolated tests.

The original time-limited reward configuration is absent from clientDB. The
three modest coin/sun rewards below are an explicit local-server policy, not
claimed to be original production rewards.
"""
import json
import time

ACTIVITY_ID = 81001
VIEW_TYPE = 8  # ActivityGlobal.VIEW_TYPE_LOGIN_REWARD
REWARDS = (((1, 1000), (3, 10)),
           ((1, 2000), (3, 20)),
           ((1, 3000), (3, 30)))
DAY = 86400


def _today(now=None):
    now = int(time.time() if now is None else now)
    local = time.localtime(now)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday,
                            0, 0, 0, 0, 0, -1)))


def _date_value(day_start):
    # LoginRewardBar -> ActivityUtils.getDateByConditionValue parses YYYYMMDD,
    # not Unix seconds (despite the protobuf field being uint32).
    return int(time.strftime("%Y%m%d", time.localtime(day_start)))


def _state(server, uin, now=None):
    """Persist each player's first event day and read claimed indexes."""
    today = _today(now)
    with server.store._db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS town_login_gifts ("
                    "uin INTEGER PRIMARY KEY, start_day INTEGER NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS town_login_gift_claims ("
                    "uin INTEGER NOT NULL, day_index INTEGER NOT NULL, "
                    "claim_time INTEGER NOT NULL, PRIMARY KEY(uin, day_index))")
        con.execute("INSERT OR IGNORE INTO town_login_gifts(uin,start_day) VALUES(?,?)",
                    (uin, today))
        start = int(con.execute("SELECT start_day FROM town_login_gifts WHERE uin=?",
                                (uin,)).fetchone()[0])
        claims = {int(row[0]): int(row[1]) for row in con.execute(
            "SELECT day_index,claim_time FROM town_login_gift_claims WHERE uin=?", (uin,))}
    return start, claims


def _submessage(server, field, payload):
    return server.pb_tag(field, 2) + server.uvarint(len(payload)) + payload


def common_entry(server, player, now=None):
    """Dto_FlexibleActivityCommon payload; caller wraps as list field 1."""
    start, _ = _state(server, player["uin"], now)
    return (server.pb_varint_field(1, ACTIVITY_ID)
            + server.pb_varint_field(2, 1001)
            + server.pb_varint_field(3, 10008)  # ActivityIcon.txt: 登录有礼
            + server.pb_varint_field(4, VIEW_TYPE)
            + server.pb_varint_field(5, start)
            + server.pb_varint_field(6, start + 3 * DAY)
            + server.pb_string(11, json.dumps({"title": "登录送礼"}, ensure_ascii=False)))


def get_stat(server, player, now=None):
    """FlexibleActivity_1001 with the three exact rows ModuleLoginReward indexes."""
    start, claims = _state(server, player["uin"], now)
    today = _today(now)
    body = server.pb_varint_field(1, ACTIVITY_ID)
    for index, rewards in enumerate(REWARDS):
        date = start + index * DAY
        item_body = b"".join(_submessage(
            server, 1, server.pb_varint_field(1, item_id)
            + server.pb_varint_field(2, amount)) for item_id, amount in rewards)
        reward = (item_body + server.pb_bool(2, date == today and index not in claims)
                  + server.pb_varint_field(3, claims.get(index, 0)))
        condition = (_submessage(server, 4, server.pb_varint_field(1, _date_value(date)))
                     + server.pb_varint_field(6, 1)
                     + server.pb_varint_field(7, int(index in claims))
                     + _submessage(server, 100, reward))
        body += _submessage(server, 2, condition)
    return [("PVZ.Cmd.FlexibleActivity_1001", body)]


def take_reward(server, cmd_data, player, now=None):
    """Grant only today's unclaimed gift; a repeated request cannot grant twice."""
    fields = server.parse_varint_fields(cmd_data)
    if int(fields.get(1, 0)) != ACTIVITY_ID:
        return []
    # ActivityProxy queues the activity id before sending the request. Even a
    # rejected click needs this acknowledgement to drain that queue and refresh.
    denied = [("PVZ.Cmd.Cmd_FlexibleActivity_TakeReward_SC", b"")]
    index = int(fields.get(2, -1))
    if not 0 <= index < len(REWARDS):
        return denied
    start, _ = _state(server, player["uin"], now)
    today = _today(now)
    if today != start + index * DAY:
        return denied
    uin = player["uin"]
    claim_time = int(time.time() if now is None else now)
    coins = sum(n for item_id, n in REWARDS[index] if item_id == 1)
    sun = sum(n for item_id, n in REWARDS[index] if item_id == 3)
    with server.store._db() as con:
        con.execute("BEGIN IMMEDIATE")
        inserted = con.execute(
            "INSERT OR IGNORE INTO town_login_gift_claims(uin,day_index,claim_time) "
            "VALUES(?,?,?)", (uin, index, claim_time))
        if not inserted.rowcount:
            return denied
        con.execute("UPDATE players SET coin=coin+?, sun=sun+? WHERE uin=?",
                    (coins, sun, uin))
        row = con.execute("SELECT coin,sun FROM players WHERE uin=?", (uin,)).fetchone()
    player["coin"], player["sun"] = int(row["coin"]), int(row["sun"])
    body = b"".join(_submessage(server, 1, server.pb_varint_field(1, item_id)
                                 + server.pb_varint_field(2, amount))
                    for item_id, amount in REWARDS[index])
    return [("PVZ.Cmd.Cmd_FlexibleActivity_TakeReward_SC", body),
            ("PVZ.Cmd.Cmd_SyncCoin_SC", server.pb_varint_field(1, player["coin"])),
            ("PVZ.Cmd.Cmd_SyncSun_SC", server.pb_varint_field(1, player["sun"]))]
