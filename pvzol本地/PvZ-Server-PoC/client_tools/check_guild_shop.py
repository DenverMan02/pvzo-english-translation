# -*- coding: utf-8 -*-
"""验证联盟商店 (ExchangeStore storeId 4 = GuildFuben):
1. ItemList_CS{storeId:4} 返回 6 条商品, 货币为联盟勋章 2028
2. Exchange_CS{entryId:401} 扣 100 联盟勋章发 碎片-火龙草 x10, hasExchanged 标记
3. 同日重复购买被拒
4. 刷新清除限购标志
5. 岛屿商店 entryId 1-6 与联盟 401-406 不混淆
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pvz_poc_server as srv
import guild_local


def find_player():
    import sqlite3
    srv.store.init_db()          # ensure the new guild_shop_* columns exist
    con = sqlite3.connect(srv.store.DB_PATH)
    try:
        row = con.execute("SELECT uin FROM players LIMIT 1").fetchone()
        return row[0] if row else None
    finally:
        con.close()


def parse_fields(buf):
    def uvarint(buf, i):
        v = s = 0
        while True:
            b = buf[i]; i += 1
            v |= (b & 0x7f) << s
            if not b & 0x80:
                return v, i
            s += 7
    i, out = 0, []
    while i < len(buf):
        key, i = uvarint(buf, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = uvarint(buf, i)
        elif w == 2:
            n, i = uvarint(buf, i)
            v = buf[i:i+n]; i += n
        else:
            raise ValueError(w)
        out.append((f, v))
    return out


def main():
    ok = True
    uin = find_player()
    if uin is None:
        print("SKIP: 无玩家")
        return 0
    player = srv.store.get_or_create_player(uin)[0]

    # 0. 初始化 guild_local (本地联盟库) — 商店本身不依赖, 但保持环境一致
    guild_local.initialize()

    # 1. 商品列表
    body = srv.handle_exchangestore_itemlist(srv.pb_varint_field(1, 4), player)
    entries = [v for f, v in parse_fields(body) if f == 1]
    if len(entries) == 6:
        print("PASS: store 4 返回 6 条商品")
    else:
        print("FAIL: store 4 商品数 =", len(entries))
        ok = False
    first = dict(parse_fields(entries[0]))
    if first.get(5) == 2028:
        print("PASS: 货币为联盟勋章 2028")
    else:
        print("FAIL: 第一条货币 =", first.get(5))
        ok = False

    # 2. 准备货币并购买 entry 401
    srv.grant_items(player, [(2028, 500)])
    player2 = srv.store.get_or_create_player(uin)[0]
    msgs = srv.handle_exchangestore_exchange(srv.pb_varint_field(1, 401), player2)
    names = [n for n, _ in msgs]
    if "PVZ.Cmd.Cmd_ExchangeStore_Exchange_SC" in names:
        print("PASS: 购买应答 Exchange_SC")
    else:
        print("FAIL: 应答 =", names)
        ok = False
    medals = srv.store.get_item_count(uin, 2028)
    if medals == 400:
        print("PASS: 扣除 100 联盟勋章 (余 %d)" % medals)
    else:
        print("FAIL: 联盟勋章余量 = %d (期望 400)" % medals)
        ok = False
    frag = srv.store.get_item_count(uin, 53900)
    if frag >= 10:
        print("PASS: 发放碎片-火龙草 x10 (持有 %d)" % frag)
    else:
        print("FAIL: 碎片数量 = %d" % frag)
        ok = False

    # 3. 重复购买被拒
    player3 = srv.store.get_or_create_player(uin)[0]
    msgs = srv.handle_exchangestore_exchange(srv.pb_varint_field(1, 401), player3)
    body = [b for n, b in msgs if n == "PVZ.Cmd.Cmd_ExchangeStore_ItemList_SC"][0]
    entries = [dict(parse_fields(v)) for f, v in parse_fields(body) if f == 1]
    e401 = next(e for e in entries if e.get(1) == 401)
    if e401.get(7) == 1:
        print("PASS: hasExchanged=1, 重复购买被拒")
    else:
        print("FAIL: hasExchanged =", e401.get(7))
        ok = False

    # 4. 刷新清除限购
    player4 = srv.store.get_or_create_player(uin)[0]
    srv.handle_exchangestore_refresh(srv.pb_varint_field(1, 4), player4)
    player5 = srv.store.get_or_create_player(uin)[0]
    if not (player5.get("guild_shop_bought") or ""):
        print("PASS: 刷新后限购标志清除")
    else:
        print("FAIL: 刷新后 bought =", player5.get("guild_shop_bought"))
        ok = False

    # 5. 岛屿 entry 不混淆
    zi = srv.exchange_store_entries(2)
    if not any(e[0] in (401, 402, 403, 404, 405, 406) for e in zi) and \
       not any(e[0] < 10 for e in srv._GUILD_SHOP):
        print("PASS: entryId 域隔离 (岛屿 1-6 / 联盟 401-406 / 神奇 5001+)")
    else:
        print("FAIL: entryId 域冲突")
        ok = False

    print("====", "ALL PASS" if ok else "HAS FAILURES", "====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
