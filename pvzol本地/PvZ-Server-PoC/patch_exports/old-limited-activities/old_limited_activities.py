"""Compatibility adapter for old activity requests; unsupported entries stay hidden.

The historical icons still exist in clientDB, but their event state and reward tables
are missing. Publishing synthetic entries makes legacy panels crash, so none are
listed unless explicitly allowlisted after a complete server implementation exists.
"""

import time


# ActivityIcon id -> ActivityGlobal view type. These are the client routes from
# ActivityViewCommand; 34 is the safe Welfare Hall route for legacy promos whose
# original destination was a generic/third-party landing page.
HISTORIC_VIEW_TYPES = {
    10050: 16,  # 首充送好礼
    10051: 14,  # 超值团购 (implemented by hotspot_events)
    10052: 8,   # 七日礼包
    10053: 34,  # 免费植物
    10054: 18,  # 双11植物狂欢礼
    10055: 34,  # 福利活动
    10056: 24,  # 欢迎回来
    10057: 67,  # 植物送礼
    10058: 23,  # 限时精彩活动 (implemented by hotspot_events)
    10059: 18,  # 双12冬日暖心礼
    10060: 45,  # 开服七天乐
    10061: 25,  # 弹珠达人
    10062: 24,  # 参与有奖
    10063: 33,  # 元宵给力转
    10064: 30,  # 植物摇摇乐
    10065: 31,  # 刮刮乐
    10066: 361, # 植物争霸赛
    10067: 33,  # 暖春登陆礼
    10068: 34,  # 福利大厅
    10069: 23,  # 来挑战自己吧
    10070: 35,  # 植物探险
    10071: 24,  # 精彩活动
    10072: 33,  # 戴夫劳动节
    10073: 24,  # 豌豆图标活动；避免恢复原失效外链
    10074: 39,  # 太空大战 (implemented by treasure_local)
    10075: 40,  # 新手好礼双重奏
    10076: 42,  # 植物龙舟赛
    10077: 24,  # 惊喜不断
    10078: 43,  # 皇陵探宝
    10079: 55,  # 消费送好礼
    10080: 33,  # 夏日献礼
    10081: 46,  # 最强集字 (implemented by hotspot_events)
    10082: 49,  # 超级翻牌
    10083: 33,  # 清凉夏日
    10084: 50,  # 大风吹/许愿 (implemented by hotspot_events)
    10085: 20,  # 椰子炮之战
    10086: 51,  # 合服狂欢周
    10087: 33,  # 中秋佳节
    10088: 24,  # 最新活动
    10089: 33,  # 新春
    10090: 53,  # 一起砸南瓜
    10091: 361, # 最强阵容 穿越时空
    10092: 54,  # 全服秒杀
    10093: 22,  # 感恩节
    10094: 55,  # 戴夫银行
    10095: 8,   # 开服大礼
    10096: 58,  # 一起抢红包
    10098: 55,  # 消费送礼
    10099: 45,  # 登录有好礼
    10100: 66,  # 潘妮小店 (implemented by hotspot_events)
    10101: 67,  # 植物嘉年华
    10102: 68,  # 宝罐迷阵 (implemented by jar_local)
    10103: 43,  # 埃及宝藏
}

PROMO_ICON_RANGE = range(10050, 10104)
# Keep the separately exported REQUEST_HANDLERS package safe as well as the live server.
VISIBLE_ARCHIVED_ICON_IDS = frozenset()
PUBLISHED_BY_LOCAL_FEATURES = {10051, 10058, 10074, 10081, 10084, 10100, 10102}
ACTIVITY_ID_OFFSET = 870000
END_DATE = 2145916800  # 2038-01-01 UTC, within the client's uint32 timestamp.
_ICON_ROWS = None
_ARCHIVED_IDS = None


def _icon_rows(server):
    global _ICON_ROWS
    if _ICON_ROWS is None:
        _ICON_ROWS = server._read_clientdb_table("ActivityIcon.txt") or []
    return _ICON_ROWS


def activity_id_for_icon(icon_id):
    return ACTIVITY_ID_OFFSET + int(icon_id)


def archived_activity_ids(server):
    """IDs assigned to legacy promo icons present in the current clientDB."""
    global _ARCHIVED_IDS
    if _ARCHIVED_IDS is None:
        ids = set()
        for row in _icon_rows(server)[1:]:
            try:
                icon_id = int(row[0])
            except (IndexError, TypeError, ValueError):
                continue
            if icon_id in HISTORIC_VIEW_TYPES and icon_id not in PUBLISHED_BY_LOCAL_FEATURES:
                ids.add(activity_id_for_icon(icon_id))
        _ARCHIVED_IDS = ids
    return _ARCHIVED_IDS


def common_entries(server):
    """Return repeated field-1 Dto_FlexibleActivityCommon records."""
    rows = _icon_rows(server)
    if not rows:
        return b""
    now = int(time.time())
    out = bytearray()
    seen = set()
    for row in rows[1:]:
        try:
            icon_id = int(row[0])
        except (IndexError, TypeError, ValueError):
            continue
        if (icon_id not in HISTORIC_VIEW_TYPES
                or icon_id not in VISIBLE_ARCHIVED_ICON_IDS
                or icon_id in PUBLISHED_BY_LOCAL_FEATURES
                or icon_id in seen):
            continue
        seen.add(icon_id)
        activity_id = activity_id_for_icon(icon_id)
        dto = (server.pb_varint_field(1, activity_id)
               + server.pb_varint_field(2, 0)  # archived listing; no invented reward schema
               + server.pb_varint_field(3, icon_id)
               + server.pb_varint_field(4, HISTORIC_VIEW_TYPES[icon_id])
               + server.pb_varint_field(5, now - 86400)
               + server.pb_varint_field(6, END_DATE))
        out.extend(server.pb_tag(1, 2) + server.uvarint(len(dto)) + dto)
    return bytes(out)


def empty_stat(server, activity_id):
    """A safe empty FlexibleActivity_1 reply for archived entries that query generic stats."""
    activity_id = int(activity_id)
    if activity_id not in archived_activity_ids(server):
        return None
    # FlexibleActivity_1 { activityId = 1, limit = 2 }; a present empty limit yields
    # empty stage/reward state and cannot grant items.
    body = (server.pb_varint_field(1, activity_id)
            + server.pb_tag(2, 2) + server.uvarint(0))
    return [("PVZ.Cmd.FlexibleActivity_1", body)]
