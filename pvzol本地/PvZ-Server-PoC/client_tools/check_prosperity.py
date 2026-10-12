# -*- coding: utf-8 -*-
"""验证 #10 小镇繁荣度(item 2)入包修复.
1. 主线任务领奖 (claim_and_grant): 任务 1020 奖励 2x100 -> items 表 item2=100, 无 unmodeled
2. grant_fuben_reward([(2,100)]) -> items 表 + Cmd_Item_Update_SC
3. _lab_afford 计背包繁荣度; _lab_charge 扣减
4. handle_dailyquest_takereward 若有繁荣度任务 -> 同步 (静态检查: items 分支已放行 item 2)
"""
import sys, os, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pvz_poc_server as srv

tmp = os.path.join(tempfile.gettempdir(), "probe_prosperity.db")
if os.path.exists(tmp):
    os.remove(tmp)
srv.store.set_db_path(tmp)
srv.store.init_db()

UIN = 990002
player, _ = srv.store.get_or_create_player(UIN)
player["level"] = 50
player["coin"] = 1000

ok = True

# ---- 1. 主线任务领奖 ----
qid = 1020
with srv.store._db() as con:
    con.execute("CREATE TABLE IF NOT EXISTS main_quest_progress ("
                "uin INTEGER NOT NULL, quest_id INTEGER NOT NULL, "
                "curr_step INTEGER NOT NULL DEFAULT 0, "
                "claimed INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (uin, quest_id))")
    con.execute("INSERT OR REPLACE INTO main_quest_progress(uin, quest_id, curr_step, claimed) "
                "VALUES(?,?,?,0)", (UIN, qid, 1))  # target=1
rows = srv._main_quest_rows()
with sqlite3.connect(tmp) as con2:      # claim_and_grant: caller owns the commit
    con2.row_factory = sqlite3.Row
    result = srv.main_quest_local.claim_and_grant(con2, UIN, rows, qid)
item2_after = srv.store.get_item_count(UIN, 2)
counts = dict(result.get("itemCounts", []))
unmodeled = result.get("unmodeledRewards")
if (result and counts.get(2) == 100 and item2_after == 100
        and (not unmodeled or dict(unmodeled).get(2) is None)):
    print("PASS: 主线 1020 领奖 -> item2 入包 100 (itemCounts=%s, unmodeled=%s)"
          % (counts, unmodeled))
else:
    print("FAIL: 主线领奖:", "result=", result and counts, "db=", item2_after, "unmodeled=", unmodeled)
    ok = False

# ---- 2. grant_fuben_reward ----
msgs = srv.grant_fuben_reward(player, [(2, 100)])
item2_after = srv.store.get_item_count(UIN, 2)
names = [m[0] for m in msgs]
if item2_after == 200 and "PVZ.Cmd.Cmd_Item_Update_SC" in names:
    print("PASS: 副本奖励 item2x100 -> 入包 200 + Cmd_Item_Update_SC")
else:
    print("FAIL: fuben item2:", item2_after, names)
    ok = False

# ---- 3. _lab_afford / _lab_charge ----
if srv._lab_afford(player, [(2, 150)]):
    srv._lab_charge(player, [(2, 150)])
    item2_after = srv.store.get_item_count(UIN, 2)
    if item2_after == 50:
        print("PASS: 实验室按背包繁荣度判价并扣减 (200-150=50)")
    else:
        print("FAIL: _lab_charge 后 item2 =", item2_after)
        ok = False
else:
    print("FAIL: _lab_afford 未计入背包繁荣度")
    ok = False
if not srv._lab_afford(player, [(2, 51)]):
    print("PASS: 余额 50 买不起 51 -> False")
else:
    print("FAIL: _lab_afford 高估繁荣度")
    ok = False

# ---- 4. 每日任务分支静态检查 ----
import inspect
src = inspect.getsource(srv.handle_dailyquest_takereward)
if "elif item_id not in _UNMODELED_CURRENCIES" in src:
    print("INFO: 每日任务领奖分支与 _UNMODELED_CURRENCIES={5} 共用放行路径 (item2 现走背包)")
print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
