# -*- coding: utf-8 -*-
"""验证人机系统:
1. ensure_bots 幂等创建 8 个 bot(玩家行 + 6 卡阵容 + 竞技场分数)
2. bot 战力不弱(总战力 >= 10 万)
3. 两个 bot 联盟已创建且成员在册
4. 竞技场榜单 = bots + 玩家, 按分数排序
5. 挑战 = 对手 bot, 胜负按战力, 分数联动
6. 对手阵容返回 bot 卡组
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pvz_poc_server as srv
import bots_local
import guild_local


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

    # 1. 种子 bot (跑两遍验证幂等)
    srv._seed_bots()
    n1 = bots_local.ensure_bots()
    roster = bots_local.bots()
    if len(roster) == 8 and n1 == 0:
        print("PASS: 8 个 bot 已就位, 二次种子幂等 (created=%d)" % n1)
    else:
        print("FAIL: roster=%d created=%d" % (len(roster), n1))
        ok = False

    # 2. 卡组与战力 (梯度分布: 最强 bot 保持高战力, 最弱 bot 开局也能挑战)
    weakest, strongest = None, None
    for b in roster:
        st = bots_local.bot_state(b["uin"])
        form = srv.store.get_formation(b["uin"], 1)
        assert st, b["uin"]
        if len(form) != 6:
            print("FAIL: bot %s 阵容槽位 = %d" % (b["name"], len(form)))
            ok = False
        if weakest is None or st["power"] < weakest[1]:
            weakest = (b["name"], st["power"])
        if strongest is None or st["power"] > strongest[1]:
            strongest = (b["name"], st["power"])
    if strongest and strongest[1] >= 100000 and weakest and weakest[1] >= 5000:
        print("PASS: bot 战力梯度 (最弱 %s = %d, 最强 %s = %d)"
              % (weakest[0], weakest[1], strongest[0], strongest[1]))
    else:
        print("FAIL: 最弱 bot = %s, 最强 bot = %s" % (weakest, strongest))
        ok = False

    # 3. 联盟
    import sqlite3
    con = sqlite3.connect(guild_local._DB_PATH)
    try:
        gnames = [r[0] for r in con.execute("SELECT name FROM local_guilds").fetchall()]
        n_bot_guilds = sum(1 for g in ("花园守护者", "月光花园") if g in gnames)
        rows = con.execute(
            "SELECT m.name, m.position, g.name FROM local_guild_members m "
            "JOIN local_guilds g ON m.guild_id=g.guild_id WHERE m.uin>=900000").fetchall()
    finally:
        con.close()
    if n_bot_guilds == 2 and len(rows) >= 7:
        bot_leaders = sorted({r[0] for r in rows if r[1] == 1 and r[2] in ("花园守护者", "月光花园")})
        print("PASS: bot 联盟 x2 已创建, %d 个 bot 在册, bot 盟主=%s" % (len(rows), bot_leaders))
    else:
        print("FAIL: bot 联盟=%d 成员=%d" % (n_bot_guilds, len(rows)))
        ok = False

    # 4. 榜单
    uin_row = None
    import sqlite3 as sq
    con = sq.connect(srv.store.DB_PATH)
    try:
        uin_row = con.execute("SELECT uin FROM players WHERE uin<900000 LIMIT 1").fetchone()
    finally:
        con.close()
    assert uin_row, "无真实玩家"
    player, _ = srv.store.get_or_create_player(uin_row[0])
    entries = srv._arena_leaderboard_entries(player, rank_arena=False)
    bot_entries = [e for e in entries if bots_local.is_bot(e["uin"])]
    scores = [e["score"] for e in entries]
    if len(bot_entries) == 8 and scores == sorted(scores, reverse=True) and \
            any(e["uin"] == player["uin"] for e in entries):
        print("PASS: 榜单含 8 bot + 玩家, 按分数降序")
    else:
        print("FAIL: bot=%d sorted=%s" % (len(bot_entries), scores == sorted(scores, reverse=True)))
        ok = False

    # 5. 挑战 (rank arena: 同时记录 last_opponent 供第 6 步阵容拉取)
    data = srv._arena_state(player, "rank_arena_data")
    pre = bots_local.pick_opponent(int(data.get("score") or 0))
    pre_state = bots_local.bot_state(pre["uin"]) if pre else None
    msgs = srv.handle_rankarena_challenge(b"", player)
    sc = dict(parse_fields([b for n, b in msgs if n == "PVZ.Cmd.Cmd_RankArena_Challenge_SC"][0]))
    data = srv._arena_state(player, "rank_arena_data")
    last = data["records"][-1]
    opp_uin = last["opponent"]
    before = bots_local.bot_state(opp_uin) if pre_state is None else pre_state
    if bots_local.is_bot(opp_uin) and last["name"] != "训练对手":
        print("PASS: 挑战对手 = %s (win=%s, score变化=%+d)" % (last["name"], last["win"], last["change"]))
    else:
        print("FAIL: 对手记录 =", last)
        ok = False
    msgs = srv.handle_arena_challenge(b"", player)
    data2 = srv._arena_state(player, "arena_data")
    last2 = data2["records"][-1]
    if bots_local.is_bot(last2["opponent"]):
        print("PASS: 普通竞技场对手 = %s (win=%s)" % (last2["name"], last2["win"]))
    else:
        print("FAIL: 普通竞技场对手 =", last2)
        ok = False
    # bot 分数联动 (before 取自挑战前快照)
    after = bots_local.bot_state(opp_uin)
    if after["wins"] + after["losses"] > before["wins"] + before["losses"] or \
            after["score"] != before["score"]:
        print("PASS: bot 战绩联动 (score %d -> %d, w/l=%d/%d)"
              % (before["score"], after["score"], after["wins"], after["losses"]))
    else:
        print("FAIL: bot 战绩未更新 (%s -> %s)" % (before, after))
        ok = False

    # 6. 对手阵容 (field-1 = 整个 Dto_FormationInfo 子消息; cardPos 是 Dto_FormationInfo 的 field 1)
    msgs = srv.handle_rankarena_getformation(b"", player, defense=False)
    body = [b for n, b in msgs if n == "PVZ.Cmd.Cmd_RankArena_GetBattleFormation_SC"][0]
    sub = [v for f, v in parse_fields(body) if f == 1]
    cards_in = len([1 for f, _v in parse_fields(sub[0]) if f == 1]) if sub else 0
    if sub and len(sub[0]) > 200 and cards_in == 6:
        print("PASS: 对手阵容返回 (%d 字节, %d 个 cardPos)" % (len(sub[0]), cards_in))
    else:
        print("FAIL: 对手阵容子消息 = %s, cardPos=%d" % ([len(s) for s in sub], cards_in))
        ok = False

    print("====", "ALL PASS" if ok else "HAS FAILURES", "====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
