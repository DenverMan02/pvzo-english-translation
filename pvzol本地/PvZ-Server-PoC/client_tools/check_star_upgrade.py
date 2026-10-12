# -*- coding: utf-8 -*-
"""端到端验证升星链路(含 LogicError 反馈) + 叶绿素合成 (#9).
升星:
1. 临时库; 玩家 level=10, 卡 1101(豌豆一星), forge=5, 碎片 51100x100, 金币 10w
   -> 期望卡变 1102 / forge 归零 / 扣碎片+金币 / 成功消息
2. 强化不足(升星后 forge=0 再点) -> Cmd_LogicError_SC{1015 该卡牌没有强化满}
3. 顶星卡 1105 -> Cmd_LogicError_SC{1016 强化等级已满}
叶绿素 (Cmd_Item_MergeChlorophyll_CS{itemBaseId(1), isAll(2)}):
4. 12x2101 + isAll=1 -> 2x2102 (级联合并)
5. 12x2101 + isAll=0 -> 1x2102
6. 3x2101 -> 拒绝 (无响应)
7. 2105 顶界 -> 拒绝
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pvz_poc_server as srv

tmp = os.path.join(tempfile.gettempdir(), "probe_star_e2e.db")
if os.path.exists(tmp):
    os.remove(tmp)
srv.store.set_db_path(tmp)
srv.store.init_db()
srv.load_battle_cards()
srv.load_card_upgrade_recipes()

UIN = 990001
player, _ = srv.store.get_or_create_player(UIN)
player["level"] = 10
player["coin"] = 100000
srv.store.add_card(UIN, 1, 1101)
srv.store.set_card_forge_level(UIN, 1, 5)
srv.store.add_item(UIN, 51100, 100)

ok = True


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


def card_of(uid):
    return next((c for c in srv.store.get_cards(UIN) if c[0] == uid), None)


def logic_pos(msgs):
    for name, body in msgs:
        if name == "PVZ.Cmd.Cmd_LogicError_SC":
            return dict(parse_fields(body)).get(1)
    return None

# ---- 1. 条件全满足 -> 升星成功 ----
msgs = srv.handle_card_usecardupgrade(srv.pb_varint_field(1, 1), player)
names = [m[0] for m in msgs]
card = card_of(1)
forge = srv.store.get_card_forge_level(UIN, 1)
pieces = srv.store.get_item_count(UIN, 51100)
coin = int(player["coin"])
new_uid = int(player.get("_last_card_evolve_new_uid", 0) or 0)
if (card and card[1] == 1102
        and "PVZ.Cmd.Cmd_Card_UseCardUpgrade_SC" in names
        and "PVZ.Cmd.Cmd_Card_SyncFullInfo_SC" in names
        and forge == 0 and pieces == 40 and coin == 70000):
    print("PASS: 升星成功 1101->1102, forge 归零, 扣 60 碎片 + 3w 金币")
else:
    print("FAIL: 升星链路异常:", names, card, forge, pieces, coin)
    ok = False

# ---- 2. 强化不足 -> LogicError 1015 ----
msgs2 = srv.handle_card_usecardupgrade(srv.pb_varint_field(1, new_uid or 1), player)
if logic_pos(msgs2) == 1015:
    print("PASS: 强化不足 -> LogicError 1015 (该卡牌没有强化满)")
else:
    print("FAIL: 强化不足应回 1015, got:", [m[0] for m in msgs2], logic_pos(msgs2))
    ok = False

# ---- 3. 顶星卡 -> LogicError 1016 ----
srv.store.add_card(UIN, 2, 1105)
srv.store.set_card_forge_level(UIN, 2, 5)
srv.store.add_item(UIN, 51100, 1000)
player["coin"] = 1000000
msgs3 = srv.handle_card_usecardupgrade(srv.pb_varint_field(1, 2), player)
if logic_pos(msgs3) == 1016:
    print("PASS: 顶星卡(newCardId=0) -> LogicError 1016 (强化等级已满)")
else:
    print("FAIL: 顶星卡应回 1016, got:", [m[0] for m in msgs3], logic_pos(msgs3))
    ok = False

# ---- 4-7. 叶绿素合成 ----
def merge(item_id, is_all):
    body = srv.pb_varint_field(1, item_id) + srv.pb_varint_field(2, is_all)
    return srv.handle_item_mergechlorophyll(body, player)

def count(item_id):
    return srv.store.get_item_count(UIN, item_id)

srv.store.add_item(UIN, 2101, 12)
msgs4 = merge(2101, 1)
names4 = [m[0] for m in msgs4]
if (count(2101) == 2 and count(2102) == 2
        and "PVZ.Cmd.Cmd_Item_Update_SC" in names4
        and "PVZ.Cmd.Cmd_Item_MergeChlorophyll_SC" in names4):
    print("PASS: 12x2101 isAll=1 -> 2x2102 (级联合并)")
else:
    print("FAIL: isAll=1 合并:", names4, count(2101), count(2102))
    ok = False

srv.store.add_item(UIN, 2101, 12)
msgs5 = merge(2101, 0)
if count(2101) == 9 and count(2102) == 3:
    print("PASS: isAll=0 -> 单批 5->1 (2101:12->7? 实际 %d, 2102 +%d)" % (count(2101), count(2102) - 2))
else:
    print("FAIL: isAll=0 单批合并:", count(2101), count(2102))
    ok = False

srv.store.add_item(UIN, 2103, 3)
msgs6 = merge(2103, 0)
if msgs6 == []:
    print("PASS: 材料不足(3<5) -> 无响应")
else:
    print("FAIL: 材料不足应无响应:", [m[0] for m in msgs6])
    ok = False

srv.store.add_item(UIN, 2105, 10)
msgs7 = merge(2105, 1)
if msgs7 == [] and count(2105) == 10:
    print("PASS: 五星顶界 -> 不再合成")
else:
    print("FAIL: 顶界应拒绝:", [m[0] for m in msgs7], count(2105))
    ok = False

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
