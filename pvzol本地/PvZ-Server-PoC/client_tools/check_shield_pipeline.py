# -*- coding: utf-8 -*-
"""验证护盾管线（2026 护盾三症状修复后）:
1. 伤害先扣盾: 全吸收刀 dmg=0/change_shield<0, 累计吸盾 = 初始盾量
2. 护盾傀儡(skill 901 等)给生命百分比最少的前 3 个队友叠新盾(容量动态扩容)
3. 无池单位 DTO maxShield=0(客户端不画空盾条) —— 旧的 hp//2 兜底是"植物只有蓝盾条
   没有盾量 + 队长技能发动后蓝盾消失"两个症状的根源
4. 盾系队长技能(10301 向日葵系)给无池植物发盾: change_shield>0 且 u["max_shield"]=峰值
5. 带盾僵尸(法老木乃伊 191): wiki 关卡敌人按卡面比例拆分 EHP, 满盾开局(蓝盾条)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pvz_poc_server as srv

srv.load_battle_cards()


def unit(pos, card_id, hp, shield=0, atk=100, is_leader=False):
    return {"pos": pos, "card_id": card_id, "hp": hp, "maxhp": hp,
            "shield": shield, "atk": atk, "rage": 0, "is_leader": is_leader,
            "skill1": 0, "skill2": 0, "name": "u%d" % pos, "power": 100,
            "type": 0 if pos % 2 == 1 else 1}


def main():
    ok = True

    # ---- 1. 伤害先扣盾 ----
    left = [unit(3, 2066, 5000, shield=2000), unit(7, 2101, 4000)]
    right = [unit(4, 191, 3000)]
    rep = srv.simulate_battle(left, right, won=True)
    hit3 = []
    for _round, (actions, _buffs) in enumerate(rep):
        for a in actions:
            for d in a.get("damages", []):
                if d.get("target") == 3:
                    hit3.append(d)
    full = [d for d in hit3 if d.get("dmg") == 0 and d.get("change_shield", 0) < 0]
    absorbed_total = -sum(d.get("change_shield", 0) for d in hit3)
    if full and absorbed_total == 2000:
        print("PASS: 伤害先扣盾 -> 全吸收刀 %s 条, 累计吸盾 %s (=初始盾量 2000)"
              % (len(full), absorbed_total))
    else:
        print("FAIL: 盾吸收异常: 全吸收刀=%s 累计吸盾=%s (期望 2000)" % (len(full), absorbed_total))
        ok = False

    # ---- 2. 护盾傀儡回盾 ----
    shield_skills = srv.load_dummy_config().get("shield_skills") or {}
    print("INFO: 解析到护盾傀儡技能:", sorted(shield_skills.keys()))
    if not shield_skills:
        print("FAIL: dummyZombieSkillInfo.txt 未解析出护盾技能")
        ok = False
    else:
        skill_id = sorted(shield_skills.keys())[0]
        base_bp, grow_bp = shield_skills[skill_id]
        # pos3 hp% 最低(500/4000), pos7 满血但有小盾池(100) -> 两者都应拿到回盾,
        # 且不受自身盾池(100)截断, 证明"叠加新盾"
        left = [unit(3, 2066, 500, shield=0), unit(7, 2101, 4000, shield=100)]
        right = [unit(4, 191, 3000)]
        puppets = {1: {"actor": 101, "skill": skill_id, "skill_lv": 1}}
        rep2 = srv.simulate_battle(left, right, won=True, puppets=puppets)
        by_target = {}
        for _round, (actions, _buffs) in enumerate(rep2):
            for a in actions:
                if a.get("puppet") is not None:
                    for d in a.get("damages", []):
                        cs = d.get("change_shield", 0)
                        if cs > 0:
                            by_target.setdefault(d.get("target"), 0)
                            by_target[d["target"]] += cs
        exp3 = 500 * base_bp // 10000       # 首轮傀儡给 pos3 的期望值
        got3 = by_target.get(3, 0)
        got7 = by_target.get(7, 0)
        if got3 >= exp3:
            print("PASS: 傀儡回盾优先给 hp%% 最低的 pos3 (%s >= 期望 %s)" % (got3, exp3))
        else:
            print("FAIL: pos3 (hp%% 最低) 未拿到足额回盾: got=%s exp>=%s, all=%s" % (got3, exp3, by_target))
            ok = False
        if got7 > 100:
            print("PASS: pos7 超过自身小盾池(100)获盾 %s -> 容量动态扩容, 新盾不截断" % got7)
        else:
            print("FAIL: pos7 回盾 %s 未超过自身盾池 100 (仍被截断)" % got7)
            ok = False
        # 峰值 = 任意时刻同时持有的最大盾量，可小于多次发放的累计和（叠盾间隔会被
        # 攻击消耗），但必须 >= 单次发放额且 >= 自身池，否则 DTO 盾条比例会失真。
        if left[0].get("max_shield", 0) >= exp3 and left[1].get("max_shield", 0) >= 100:
            print("PASS: 傀儡盾峰值回写 u['max_shield'] (pos3=%s >= 单次 %s, pos7=%s >= 池 100)"
                  % (left[0].get("max_shield"), exp3, left[1].get("max_shield")))
        else:
            print("FAIL: 峰值回写缺失 pos3=%s pos7=%s"
                  % (left[0].get("max_shield"), left[1].get("max_shield")))
            ok = False

    # ---- 3. 无池单位 DTO maxShield=0 (不画空盾条) ----
    f = srv.parse_varint_fields(srv.build_fight_card(3, 2066, hp=5000, shield=0))
    if f.get(8) == 0:
        print("PASS: 无池单位 DTO maxShield=0 (客户端不画空盾条, 原 hp//2 兜底已移除)")
    else:
        print("FAIL: 无池单位 DTO maxShield=%s (期望 0)" % f.get(8))
        ok = False

    # ---- 4. 盾系队长技能给无池植物发盾 ----
    st = srv._BATTLE_CARDS.get(1101, {})
    sid = st.get("leader_skill")
    eff = srv._leader_effect(sid or 0)
    if not eff.get("shield_ratio_bp"):
        print("FAIL: 向日葵队长技能 %s 未映射到盾系 (eff=%s)" % (sid, eff))
        ok = False
    else:
        left = [unit(3, 1101, 4000, shield=0, is_leader=True),
                unit(7, 1205, 5000, shield=0)]
        right = [unit(4, 191, 500, shield=0)]
        rep3 = srv.simulate_battle(left, right, won=True)
        r0_actions, _ = rep3[0]
        got = {}
        if r0_actions:
            for d in r0_actions[0].get("damages", []):
                if d.get("change_shield", 0) > 0:
                    got[d["target"]] = d["change_shield"]
        exp3 = 4000 * eff["shield_ratio_bp"] // 10000
        exp7 = 5000 * eff["shield_ratio_bp"] // 10000
        if got.get(3) == exp3 and got.get(7) == exp7:
            print("PASS: 盾系队长 %s 全体套盾 (pos3=%s, pos7=%s, 无池单位也拿盾)"
                  % (sid, got.get(3), got.get(7)))
        else:
            print("FAIL: 队长盾量异常 got=%s exp={3:%s,7:%s}" % (got, exp3, exp7))
            ok = False
        peak_ok = (left[0].get("max_shield") == exp3 and left[1].get("max_shield") == exp7)
        if peak_ok:
            print("PASS: 队长盾峰值回写 max_shield (pos3=%s, pos7=%s) -> 盾条可见"
                  % (left[0]["max_shield"], left[1]["max_shield"]))
        else:
            print("FAIL: 峰值回写错误 pos3=%s pos7=%s"
                  % (left[0].get("max_shield"), left[1].get("max_shield")))
            ok = False

    # ---- 5. 带盾僵尸(法老木乃伊 191)满盾开局 ----
    enemies = srv._build_wiki_enemies(1, 1) or []
    if not enemies:
        print("FAIL: ch1-1 wiki 敌人为空")
        ok = False
    else:
        u = enemies[0]
        zst = srv._BATTLE_CARDS.get(u["card_id"], {})
        if u["card_id"] != 191:
            print("FAIL: ch1-1 敌人卡 = %s (期望 191 带盾法老木乃伊)" % u["card_id"])
            ok = False
        elif u["shield"] <= 0 or u["hp"] + u["shield"] != 2970 // srv._ZOMBIE_HP_TEST_DIVISOR:
            print("FAIL: 191 血盾拆分错误 hp=%s shield=%s" % (u["hp"], u["shield"]))
            ok = False
        else:
            print("PASS: 带盾僵尸 %s(%s) hp=%s shield=%s EHP=%s (比例拆分, EHP 守恒)"
                  % (u["card_id"], zst.get("name"), u["hp"], u["shield"],
                     u["hp"] + u["shield"]))
        # 僵尸 DTO: 有池 -> maxShield=盾量, field5=盾量 (满盾开局)
        f = srv.parse_varint_fields(srv.build_fight_card(4, 191, hp=u["hp"], shield=u["shield"]))
        if f.get(8) == u["shield"] and f.get(5) == u["shield"]:
            print("PASS: 带盾僵尸 DTO field5=shield=%s field8=maxShield=%s -> 满蓝盾条开局"
                  % (u["shield"], f.get(8)))
        else:
            print("FAIL: 带盾僵尸 DTO field5=%s field8=%s (期望均为 %s)"
                  % (f.get(5), f.get(8), u["shield"]))
            ok = False

    print("====", "ALL PASS" if ok else "HAS FAILURES", "====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
