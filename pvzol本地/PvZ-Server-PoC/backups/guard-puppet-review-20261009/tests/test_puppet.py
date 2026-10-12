"""Regression checks for the 傀儡 (puppet — the client's "Dummy" module) system.

Everything the live game kept server-side was missing on this PoC: opening the town's 傀儡 button (town
button Name_Dummy, role level 55) sent Cmd_Puppet_GetInfo_CS into the void, so the panel was empty and
none of its buttons worked. These checks cover the whole restored loop:

  * the config tables the feature is driven by (dummyZombieInfo / dummyLevelInfo / dummyZombieStarInfo /
    dummyZombieQuiltyInfo / dummyEquipment* / dummyZombieSkillInfo / dummySkillUpdateInfo /
    dummyActiveSkillInfo / dummyZombieCallInfo / dummyFuben*);
  * 捕捉 (GetExtractInfo + Extract, 10次必出 + 免费捕获), 合成 (Recruit), the five upgrade paths, 交换,
    图纸拆解 and the two market purchases;
  * 上阵傀儡 (Get/SetFormation) and the 傀儡战境 dungeon (chapter/stage info, fight, sweep) — whose report
    carries the puppets (Dto_FightSide.puppetList / fightType 22) so they fight beside the plants.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_puppet_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700004211
FAILED = []
WUSENG = 1000001           # 武僧僵尸 (1★, 白, needs 100 x 95601)
ZHADAN = 1000002           # 炸弹僵尸 (another composable puppet)
DACHUI = 3000001           # 大锤僵尸 (3★, needs 300 x 95605 — never in the starter stash)


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def msg(msgs, name):
    return next((b for n, b in msgs if n == name), None)


def fields(body):
    """The varint fields of a message body as {fieldNumber: value}."""
    return srv.parse_varint_fields(body or b"")


def puppet_entries(body):
    """The Dto_PuppetFullInfo list of Cmd_Puppet_GetInfo_SC (field 1)."""
    return [srv.parse_varint_fields(raw) for raw in srv.parse_repeated_submessages(body or b"", 1)]


def extract_groups(body):
    """The Dto_Puppet_ExtractGroupInfo list of Cmd_Puppet_GetExtractInfo_SC (field 2)."""
    return [srv.parse_varint_fields(raw) for raw in srv.parse_repeated_submessages(body or b"", 2)]


def stage_infos(body):
    """The Dto_PuppetFuben_StageInfo list of Cmd_PuppetFuben_GetStageInfo_SC (field 3)."""
    return [srv.parse_varint_fields(raw) for raw in srv.parse_repeated_submessages(body or b"", 3)]


def report_of(fight_body):
    """The Dto_FightReport inside Cmd_PuppetFuben_Fight_SC (field 5)."""
    subs = srv.parse_repeated_submessages(fight_body or b"", 5)
    return subs[0] if subs else b""


def side(body, index):
    """One Dto_FightSide of a report (field 2 = left, 3 = right)."""
    subs = srv.parse_repeated_submessages(body or b"", index)
    return subs[0] if subs else b""


def force_win(won=True):
    """Patch resolve_battle for one fight (the tests must not depend on the team's power)."""
    real = srv.resolve_battle
    srv.resolve_battle = lambda left, right: (won, 0, 0)
    return real


def main():
    player, _new = store.get_or_create_player(UIN)
    player["plants"] = store.get_player_plants(UIN)
    store.save_player(UIN, level=60, coin=1000000, ticket=1000)
    player["level"], player["coin"], player["ticket"] = 60, 1000000, 1000
    srv.ensure_player_cards(player)
    srv.load_battle_cards()
    cfg = srv.load_dummy_config()

    # --- 1. the config tables the whole feature reads --------------------------------------
    check("傀儡 config: composable puppets load",
          len([z for z in cfg["zombies"].values() if z["composite"]]) >= 15)
    check("傀儡 config: 2 modes x 10 chapters x 6 stages", len(cfg["stages"]) == 120,
          len(cfg["stages"]))
    check("傀儡 config: every stage has 5 waves",
          all(len(srv.dummy_stage_waves(*k)) == 5 for k in list(cfg["stages"])[:20]),
          srv.dummy_stage_waves(1, 1, 1))
    check("傀儡 config: the level table caps by role level",
          cfg["levels"][1]["exp"] > 0 and cfg["levels"][16]["role_lv"] == 2, cfg["levels"][16])
    check("傀儡 config: 9 stars of 碎片 costs",
          cfg["stars"][1]["pieces"] == 100 and cfg["stars"][2]["pieces"] == 200, cfg["stars"][2])

    # --- 2. the panel opens: empty list + the one-time starter stash ------------------------
    body = srv.handle_puppet_getinfo(b"", player)
    check("傀儡 GetInfo answers (empty list on a fresh account)", puppet_entries(body) == [],
          len(puppet_entries(body)))
    check("opening the panel grants the one-time 傀儡 stash",
          store.get_item_count(UIN, srv._DUMMY_CATCHERS[0]) == 5,
          store.get_item_count(UIN, srv._DUMMY_CATCHERS[0]))
    srv.handle_puppet_getinfo(b"", player)
    check("the stash is only handed out once",
          store.get_item_count(UIN, srv._DUMMY_CATCHERS[0]) == 5,
          store.get_item_count(UIN, srv._DUMMY_CATCHERS[0]))
    check("the stash also seeds 100 碎片 for the five 1★ puppets",
          [store.get_item_count(UIN, i) for i in (95601, 95602, 95606, 95608, 95612)] == [100] * 5,
          [store.get_item_count(UIN, i) for i in (95601, 95602, 95606, 95608, 95612)])
    check("能量 / 技能点 start full",
          (srv.puppet_energy(player), srv.puppet_skill_val(player))
          == (srv._DUMMY_ENERGY_MAX, srv._DUMMY_SKILL_MAX),
          (srv.puppet_energy(player), srv.puppet_skill_val(player)))

    # --- 3. 捕捉: two groups, a free capture, the 捕捉器 cost, the 10-capture pity ----------
    groups = extract_groups(srv.handle_puppet_getextractinfo(b"", player))
    check("傀儡捕捉 lists its two groups", [g.get(1) for g in groups] == [1, 2],
          [g.get(1) for g in groups])
    check("the first capture of the day is free", groups[0].get(2) == 1, groups[0])
    catchers = store.get_item_count(UIN, 95537)
    msgs = srv.handle_puppet_extract(srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
                                     + srv.pb_varint_field(3, 1), player)
    sc = msg(msgs, "PVZ.Cmd.Cmd_Puppet_Extract_SC")
    check("捕捉 answers Cmd_Puppet_Extract_SC with the drawn items",
          sc is not None and srv.parse_repeated_submessages(sc, 2) != [], [n for n, _b in msgs])
    check("a free capture spends no 捕捉器", store.get_item_count(UIN, 95537) == catchers,
          store.get_item_count(UIN, 95537))
    check("the group counts the capture",
          extract_groups(srv.handle_puppet_getextractinfo(b"", player))[0].get(4) == 1)
    store.add_item(UIN, 95537, 10)                 # enough 捕捉器 for a full pity cycle
    catchers = store.get_item_count(UIN, 95537)
    before = {iid: store.get_item_count(UIN, iid) for iid in (95605, 95610, 95613)}
    for _step in range(10):
        srv.handle_puppet_extract(srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
                                  + srv.pb_varint_field(3, 1), player)
    high_tier = [iid for iid in (95605, 95610, 95613)
                 if store.get_item_count(UIN, iid) > before[iid]]
    check("the 10th capture guarantees a 高级僵尸 碎片", bool(high_tier), high_tier)
    check("paid captures spend the 捕捉器", store.get_item_count(UIN, 95537) == catchers - 10,
          store.get_item_count(UIN, 95537))
    gold = int(player.get("ticket") or 0)
    player["ticket"] = 0
    store.save_player(UIN, ticket=0)
    srv.handle_puppet_extract(srv.pb_varint_field(1, 2) + srv.pb_varint_field(2, 1)
                              + srv.pb_varint_field(3, 0), player)
    check("the free capture works without any currency",
          extract_groups(srv.handle_puppet_getextractinfo(b"", player))[1].get(4) == 1)
    srv.handle_puppet_extract(srv.pb_varint_field(1, 2) + srv.pb_varint_field(2, 1)
                              + srv.pb_varint_field(3, 0), player)
    check("a capture without the currency changes nothing",
          extract_groups(srv.handle_puppet_getextractinfo(b"", player))[1].get(4) == 1)
    player["ticket"] = gold
    store.save_player(UIN, ticket=gold)

    # --- 4. 合成 (Recruit): 100 x 碎片 -> the puppet ---------------------------------------
    # WUSENG's 100 碎片 come from the starter stash, so use another puppet for the "not enough" case.
    srv.handle_puppet_recruit(srv.pb_varint_field(1, DACHUI), player)
    check("合成 without the 碎片 creates nothing", store.get_puppet(UIN, DACHUI) is None)
    wuseng_pieces = store.get_item_count(UIN, 95601)      # 捕捉 may have added a few on top of the stash
    check("the stash (plus captures) covers one 合成", wuseng_pieces >= 100, wuseng_pieces)
    msgs = srv.handle_puppet_recruit(srv.pb_varint_field(1, WUSENG), player)
    sc = msg(msgs, "PVZ.Cmd.Cmd_Puppet_Recruit_SC")
    check("合成 answers Cmd_Puppet_Recruit_SC", sc is not None, [n for n, _b in msgs])
    row = store.get_puppet(UIN, WUSENG) or {}
    check("the puppet arrives at its 1★ / 白 / level 1",
          (row.get("star"), row.get("quality"), row.get("level")) == (1, 100, 1), row)
    check("合成 consumes the 碎片",
          store.get_item_count(UIN, 95601) == wuseng_pieces - 100,
          (wuseng_pieces, store.get_item_count(UIN, 95601)))
    check("合成 also activates both skills",
          int(row.get("active_skill") or 0) > 0 and int(row.get("passive_skill") or 0) > 0, row)
    srv.handle_puppet_recruit(srv.pb_varint_field(1, WUSENG), player)
    check("a puppet can only be composited once", store.get_puppet(UIN, WUSENG) is not None)
    # A refusal must TELL the client why: Cmd_LogicError_SC{pos = clientDB ErrorInfo id} is what the
    # live game pops ("你已经获得过该种傀儡了" / "需求道具不足"); without it the panel just sits there.
    msgs = srv.handle_puppet_recruit(srv.pb_varint_field(1, WUSENG), player)
    check("合成 an owned puppet answers ErrorInfo 12202 (你已经获得过该种傀儡了)",
          msg(msgs, "PVZ.Cmd.Cmd_LogicError_SC") == srv.pb_varint_field(1, srv._PUPPET_ERR_OWNED),
          [n for n, _b in msgs])
    msgs = srv.handle_puppet_recruit(srv.pb_varint_field(1, DACHUI), player)
    check("合成 without 碎片 answers ErrorInfo 204 (需求道具不足)",
          msg(msgs, "PVZ.Cmd.Cmd_LogicError_SC") == srv.pb_varint_field(1, srv._PUPPET_ERR_NO_ITEM),
          [n for n, _b in msgs])
    msgs = srv.handle_puppet_upgradeskilllevel(srv.pb_varint_field(1, WUSENG)
                                               + srv.pb_varint_field(2, 0), player)
    check("every refused upgrade carries the same 提示",
          msg(msgs, "PVZ.Cmd.Cmd_LogicError_SC") is not None
          and msg(msgs, "PVZ.Cmd.Cmd_Puppet_UpgradeSkillLevel_SC") is not None,
          [n for n, _b in msgs])

    # --- 5. GetInfo carries the puppet with all six equipment slots -------------------------
    dtos = srv.parse_repeated_submessages(srv.handle_puppet_getinfo(b"", player), 1)
    check("GetInfo now lists the owned puppet", len(dtos) == 1 and fields(dtos[0]).get(1) == WUSENG,
          [fields(d).get(1) for d in dtos])
    spars = srv.parse_repeated_submessages(dtos[0], 8)
    check("the six equipment slots are always present", len(spars) == srv._DUMMY_EQUIP_PARTS,
          len(spars))
    check("GetInfo can be narrowed to one puppet",
          len(srv.parse_repeated_submessages(
              srv.handle_puppet_getinfo(srv.pb_varint_field(1, WUSENG), player), 1)) == 1)

    # --- 6. 上阵傀儡 (the lineup) ------------------------------------------------------------
    # The panel's five pods are handled by POSITION (`DummyModel.dummyZombieLineupMapByPos`,
    # `getLineupZombieInfoByPos`) while Dto_PuppetInfo carries no position at all -> both sides
    # exchange a DENSE five-entry formation (index = round-1, an unused round = empty Dto_PuppetInfo).
    # A sparse list, or none at all, leaves the pods without drop targets — the panel then only ever
    # sends an empty formation itself, which is why 上阵 "放不上去".
    info_dto = srv.pb_varint_field(1, WUSENG)
    msgs = srv.handle_puppet_setformation(srv.pb_bytes(1, srv.pb_bytes(1, info_dto)), player)
    check("上阵 answers SetFormation_SC + the GetFormation_SC echo (the client has no SetFormation_SC)",
          msg(msgs, "PVZ.Cmd.Cmd_Puppet_SetFormation_SC") is not None
          and msg(msgs, "PVZ.Cmd.Cmd_Puppet_GetFormation_SC") is not None, [n for n, _b in msgs])
    formation = srv.parse_repeated_submessages(srv.handle_puppet_getformation(b"", player), 1)[0]
    slots = [fields(d).get(1, 0) for d in srv.parse_repeated_submessages(formation, 1)]
    check("上阵傀儡 echoes a dense five-round lineup", slots == [WUSENG, 0, 0, 0, 0], slots)
    check("the lineup is capped at five rounds", srv._DUMMY_MAX_LINEUP == 5)

    empty = srv.pb_bytes(1, b"")                      # one empty Dto_PuppetInfo = an unused round
    srv.handle_puppet_setformation(
        srv.pb_bytes(1, empty + empty + srv.pb_bytes(1, info_dto)), player)
    formation = srv.parse_repeated_submessages(srv.handle_puppet_getformation(b"", player), 1)[0]
    slots = [fields(d).get(1, 0) for d in srv.parse_repeated_submessages(formation, 1)]
    check("a puppet dropped into round 3 stays in round 3", slots == [0, 0, WUSENG, 0, 0], slots)
    check("the combat lineup stays the compact owned list", srv.puppet_lineup(player) == [WUSENG],
          srv.puppet_lineup(player))
    srv.handle_puppet_setformation(srv.pb_bytes(1, srv.pb_bytes(1, info_dto)), player)   # back to 首轮

    # --- 7. the growth paths ---------------------------------------------------------------
    store.add_item(UIN, 95533, 5)                       # 初级培养液 = 10 exp each
    sc = msg(srv.handle_puppet_upgradelevel(srv.pb_varint_field(1, WUSENG)
                                            + srv.pb_varint_field(2, 95533)
                                            + srv.pb_varint_field(3, 5), player),
             "PVZ.Cmd.Cmd_Puppet_UpgradeLevel_SC")
    check("feeding 培养液 levels the puppet up", (fields(sc).get(4), fields(sc).get(5)) == (2, 25),
          (fields(sc).get(4), fields(sc).get(5)))
    store.add_item(UIN, 95601, 200)
    star_pieces = store.get_item_count(UIN, 95601)
    sc = msg(srv.handle_puppet_upgradestar(srv.pb_varint_field(1, WUSENG), player),
             "PVZ.Cmd.Cmd_Puppet_UpgradeStar_SC")
    check("升星 spends 200 碎片 for 2★", (fields(sc).get(2), store.get_item_count(UIN, 95601))
          == (2, star_pieces - 200), (fields(sc).get(2), store.get_item_count(UIN, 95601)))
    sc = msg(srv.handle_puppet_upgradequality(srv.pb_varint_field(1, WUSENG)
                                              + srv.pb_varint_field(2, 0), player),
             "PVZ.Cmd.Cmd_Puppet_UpgradeQuality_SC")
    check("晋品 is refused while the equipment lags", fields(sc).get(3) == 100, fields(sc).get(3))
    forge = store.get_item_count(UIN, srv._DUMMY_ITEM_FORGE)
    sc = msg(srv.handle_puppet_upgradequality(srv.pb_varint_field(1, WUSENG)
                                              + srv.pb_varint_field(2, 1), player),
             "PVZ.Cmd.Cmd_Puppet_UpgradeQuality_SC")
    check("晋品 works with 傀儡原石 and moves the puppet to the next quality",
          fields(sc).get(3) == 101, fields(sc).get(3))
    check("晋品 consumed the 傀儡原石",
          store.get_item_count(UIN, srv._DUMMY_ITEM_FORGE) == forge - 1,
          store.get_item_count(UIN, srv._DUMMY_ITEM_FORGE))
    skill_before = srv.puppet_skill_val(player)
    coin_before = int(player.get("coin") or 0)
    sc = msg(srv.handle_puppet_upgradeskilllevel(srv.pb_varint_field(1, WUSENG)
                                                 + srv.pb_varint_field(2, 0), player),
             "PVZ.Cmd.Cmd_Puppet_UpgradeSkillLevel_SC")
    check("主动技能 levels up against the 技能点 pool", fields(sc).get(3) == 2, fields(sc).get(3))
    check("the 技能点 pool paid for it",
          srv.puppet_skill_val(player) == skill_before - 1,
          (skill_before, srv.puppet_skill_val(player)))
    check("the skill also charged 金币", int(player.get("coin") or 0) < coin_before,
          (coin_before, player.get("coin")))

    # --- 8. 装备 (the six parts): build, level up, promote --------------------------------
    store.add_item(UIN, 95501, 1)
    store.add_item(UIN, 95531, 4000)
    sc = msg(srv.handle_puppet_upgradesparlevel(srv.pb_varint_field(1, WUSENG)
                                                + srv.pb_varint_field(2, 1)
                                                + srv.pb_varint_field(3, 1), player),
             "PVZ.Cmd.Cmd_Puppet_UpgradeSparLevel_SC")
    check("装备 is built for its 图纸 + 装备碎片", fields(sc).get(4) == 1, fields(sc).get(4))
    sc = msg(srv.handle_puppet_upgradesparlevel(srv.pb_varint_field(1, WUSENG)
                                                + srv.pb_varint_field(2, 1)
                                                + srv.pb_varint_field(3, 3), player),
             "PVZ.Cmd.Cmd_Puppet_UpgradeSparLevel_SC")
    check("一键升级 spends three more levels", fields(sc).get(4) == 4, fields(sc).get(4))
    store.save_puppet(UIN, WUSENG, spars="1:10:1")      # 白色 tops out at level 10
    store.add_item(UIN, 95502, 1)
    sc = msg(srv.handle_puppet_upgradesparquality(srv.pb_varint_field(1, WUSENG)
                                                  + srv.pb_varint_field(2, 1), player),
             "PVZ.Cmd.Cmd_Puppet_UpgradeSparQuality_SC")
    check("装备晋品 raises the part's quality and keeps its level",
          (fields(sc).get(3), fields(sc).get(4)) == (2, 10), (fields(sc).get(3), fields(sc).get(4)))
    store.add_item(UIN, 95501, 3)
    frags = store.get_item_count(UIN, 95531)
    sc = msg(srv.handle_puppet_recyclespardraw(srv.pb_varint_field(1, 95501)
                                               + srv.pb_varint_field(2, 3), player),
             "PVZ.Cmd.Cmd_Puppet_RecycleSparDraw_SC")
    check("拆解图纸 answers 装备碎片",
          store.get_item_count(UIN, 95531) == frags + 90, store.get_item_count(UIN, 95531))

    # --- 9. 傀儡交换 (swap level/quality/equipment of two blue puppets) --------------------
    store.add_item(UIN, 95602, 100)
    srv.handle_puppet_recruit(srv.pb_varint_field(1, ZHADAN), player)
    store.save_puppet(UIN, WUSENG, level=2, quality=300)
    store.save_puppet(UIN, ZHADAN, level=5, quality=300, spars="2:3:1")
    store.add_item(UIN, srv._DUMMY_ITEM_SWAP_CARD, 1)
    cards_before = store.get_item_count(UIN, srv._DUMMY_ITEM_SWAP_CARD)
    sc = msg(srv.handle_puppet_swap(srv.pb_varint_field(1, WUSENG) + srv.pb_varint_field(2, ZHADAN),
                                    player), "PVZ.Cmd.Cmd_Puppet_Swap_SC")
    check("交换 answers Cmd_Puppet_Swap_SC", sc is not None, sc)
    check("交换 moves the level and the equipment",
          (store.get_puppet(UIN, WUSENG).get("level"), store.get_puppet(UIN, WUSENG).get("spars"),
           store.get_puppet(UIN, ZHADAN).get("level"))
          == (5, "2:3:1", 2),
          (store.get_puppet(UIN, WUSENG).get("level"), store.get_puppet(UIN, WUSENG).get("spars")))
    check("交换 spends a 傀儡交换卡",
          store.get_item_count(UIN, srv._DUMMY_ITEM_SWAP_CARD) == cards_before - 1,
          store.get_item_count(UIN, srv._DUMMY_ITEM_SWAP_CARD))

    # --- 10. 傀儡战境: chapter / stage info -------------------------------------------------
    chapters = fields(srv.handle_puppetfuben_getchapterinfo(b"", player))
    check("傀儡战境 starts with no chapter passed", (chapters.get(1), chapters.get(2)) == (0, 0),
          chapters)
    stages = stage_infos(srv.handle_puppetfuben_getstageinfo(
        srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1), player))
    check("傀儡战境 (普通 1-1..1-6) lists six stages", [s.get(1) for s in stages] == [1, 2, 3, 4, 5, 6],
          [s.get(1) for s in stages])
    check("stage 1 starts uncleared and un-sweepable",
          (stages[0].get(2), stages[0].get(5)) == (0, 0), stages[0])
    uid = store.get_cards(UIN)[0][0]
    body = srv.pb_bytes(1, srv.pb_varint_field(1, 3) + srv.pb_varint_field(2, uid))
    srv.handle_puppetfuben_setformation(body, player)
    formation = srv.parse_repeated_submessages(srv.handle_puppetfuben_getformation(b"", player), 1)[0]
    check("傀儡战境 keeps its own plant team",
          [fields(d).get(1) for d in srv.parse_repeated_submessages(formation, 1)] == [3],
          [fields(d).get(1) for d in srv.parse_repeated_submessages(formation, 1)])

    # --- 11. one wave: energy, the report (with the puppets), the recorded clear -----------
    energy_before = srv.puppet_energy(player)
    real = force_win()
    try:
        msgs = srv.handle_puppetfuben_fight(srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
                                            + srv.pb_varint_field(3, 1)
                                            + srv.pb_varint_field(4, 1), player)
    finally:
        srv.resolve_battle = real
    sc = msg(msgs, "PVZ.Cmd.Cmd_PuppetFuben_Fight_SC")
    check("傀儡战境 answers Cmd_PuppetFuben_Fight_SC", sc is not None, [n for n, _b in msgs])
    report = report_of(sc)
    check("the report routes to the 傀儡战境 result window (fightType 22)",
          fields(report).get(5) == srv._DUMMY_FUBEN_TARGET, fields(report).get(5))
    left = side(report, 2)
    check("the player's side is E_FightSide_PuppetFuben_Role (38)", fields(left).get(1) == 38,
          fields(left).get(1))
    check("the 上阵傀儡 ride along in leftSide.puppetList (field 16)",
          len(srv.parse_repeated_submessages(left, 16)) == len(srv.puppet_lineup(player)),
          len(srv.parse_repeated_submessages(left, 16)))
    puppet_dtos = [fields(d) for d in srv.parse_repeated_submessages(left, 16)]
    check("puppetList carries the client dummy positions in posIndex",
          [d.get(1) for d in puppet_dtos] == [101 + i * 2 for i, _b in enumerate(puppet_dtos)]
          and [d.get(2) for d in puppet_dtos] == srv.puppet_lineup(player),
          [(d.get(1), d.get(2)) for d in puppet_dtos])

    # the 上阵傀儡 must also FIGHT: every round carries Dto_FightAction.puppetActor(6) for the puppet of
    # its lineup slot ("傀儡按照首轮至五轮顺序每回合依次轮流出手"), hitting a zombie beside the plants.
    rounds = srv.parse_repeated_submessages(report, 4)
    acts = []
    for raw in rounds:
        rnd = fields(raw).get(1, 0)
        for action_raw in srv.parse_repeated_submessages(raw, 2):
            puppet_actor = srv.parse_repeated_submessages(action_raw, 6)
            if not puppet_actor:
                continue
            pa = fields(puppet_actor[0])
            dmg = srv.parse_repeated_submessages(action_raw, 2)
            acts.append((rnd, pa.get(1), pa.get(2), pa.get(3),
                         fields(dmg[0]).get(1) if dmg else 0))
    check("the replay carries Dto_FightAction.puppetActor(6) — the puppet fights", bool(acts), acts)
    check("puppet actions address the visible round slot and strike a zombie",
          all(a[1] == 101 + (a[0] - 1) % 5 * 2 and a[2] % 2 == 0 for a in acts), acts)
    check("round zero reveals the first puppet before any attack",
          fields(rounds[0]).get(1) == 0 and not srv.parse_repeated_submessages(rounds[0], 2))
    check("puppet actions omit the normal actor field",
          all(not srv.parse_repeated_submessages(action_raw, 1)
              for rnd_raw in rounds for action_raw in srv.parse_repeated_submessages(rnd_raw, 2)
              if srv.parse_repeated_submessages(action_raw, 6)), acts)
    check("the puppet's hit is the damage the replay applies to that zombie",
          all(a[2] == a[4] for a in acts), acts)
    check("at most one puppet acts per round", len({a[0] for a in acts}) == len(acts), acts)
    slots = {i + 1: b for i, b in enumerate(srv.puppet_lineup_slots(player)) if b}
    check("the puppet acts with its own 主动技能",
          all(a[3] == int((store.get_puppet(UIN, slots.get((a[0] - 1) % 5 + 1, 0)) or {}).get("active_skill") or 0)
              for a in acts), acts)
    # the round <-> slot rule, straight off simulate_battle: slot N acts in round N, empty slots are
    # skipped ("没有傀儡时该回合傀儡轮空") — a 5-round fight with puppets in slot 2 and 4 only.
    saved_lineup = srv.puppet_lineup_slots(player)
    srv.save_puppet_lineup(player, [0, WUSENG, 0, ZHADAN, 0])
    sparse_lineup = srv._puppet_battle_lineup(player)
    srv.save_puppet_lineup(player, saved_lineup)
    check("sparse formation preserves the actual dummy positions",
          {r: p["actor"] for r, p in sparse_lineup.items()} == {2: 103, 4: 107})
    long_rounds = srv.simulate_battle(
        [{"pos": 3, "card_id": 4, "hp": 100000, "atk": 1, "skill1": 0, "skill2": 0, "level": 1}],
        [{"pos": 4, "card_id": 900, "hp": 1000000, "atk": 0, "skill1": 0, "skill2": 0, "level": 1}],
        True, max_rounds=12, puppets=sparse_lineup)
    slots_acted = [(i, a["puppet"]["index"]) for i, (actions, _b) in enumerate(long_rounds, 1)
                   for a in actions if a.get("puppet")]
    check("the puppet of slot N acts in round N, empty slots are skipped",
          slots_acted == [(2, 103), (4, 107), (7, 103), (9, 107), (12, 103)], slots_acted)
    check("the fight spent the stage's 能量", srv.puppet_energy(player) == energy_before - 5,
          (energy_before, srv.puppet_energy(player)))
    check("the clear is pushed to the client",
          msg(msgs, "PVZ.Cmd.Cmd_PuppetFuben_SyncStageInfo_SC") is not None)
    check("the wave is recorded",
          store.puppet_fuben_progress(UIN, 1, 1).get(1, (0, 0, ""))[0] == 1,
          store.puppet_fuben_progress(UIN, 1, 1))
    sweep = srv.handle_puppetfuben_sweep(srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
                                         + srv.pb_varint_field(3, 1)
                                         + srv.pb_varint_field(4, 1), player)
    check("扫荡 is refused until the stage is fully cleared",
          fields(msg(sweep, "PVZ.Cmd.Cmd_PuppetFuben_Sweep_SC")).get(4) == 0)
    for wave in (2, 3, 4, 5):
        real = force_win()
        try:
            srv.handle_puppetfuben_fight(srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
                                         + srv.pb_varint_field(3, 1)
                                         + srv.pb_varint_field(4, wave), player)
        finally:
            srv.resolve_battle = real
    stages = stage_infos(srv.handle_puppetfuben_getstageinfo(
        srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1), player))
    check("clearing all five waves unlocks 扫荡", (stages[0].get(2), stages[0].get(5)) == (5, 1),
          stages[0])
    energy_before = srv.puppet_energy(player)
    msgs = srv.handle_puppetfuben_sweep(srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
                                        + srv.pb_varint_field(3, 1) + srv.pb_varint_field(4, 2),
                                        player)
    sc = msg(msgs, "PVZ.Cmd.Cmd_PuppetFuben_Sweep_SC")
    check("扫荡 answers with the loot of both sweeps",
          fields(sc).get(4) == 2 and srv.parse_repeated_submessages(sc, 5) != [],
          (fields(sc).get(4), len(srv.parse_repeated_submessages(sc, 5))))
    check("扫荡 spends 能量 per sweep", srv.puppet_energy(player) == energy_before - 10,
          (energy_before, srv.puppet_energy(player)))
    elite = stage_infos(srv.handle_puppetfuben_getstageinfo(srv.pb_varint_field(1, 1)
                                                           + srv.pb_varint_field(2, 2), player))
    check("精英 stays locked until the 普通 chapter is passed",
          all(s.get(2) == 0 for s in elite), [s.get(2) for s in elite])

    # --- 12. the 傀儡市场 (钻石 -> 能量 / 技能点) ------------------------------------------
    energy_before = srv.puppet_energy(player)
    ticket_before = int(player.get("ticket") or 0)
    msgs = srv.handle_market_buypuppetenergy(b"", player)
    check("buying 能量 answers with buyNum",
          fields(msg(msgs, "PVZ.Cmd.Cmd_Market_BuyPuppetEnergy_SC")).get(1) == 1,
          msg(msgs, "PVZ.Cmd.Cmd_Market_BuyPuppetEnergy_SC"))
    check("buying 能量 pays the table's amount for diamonds",
          (srv.puppet_energy(player), int(player.get("ticket") or 0))
          == (min(srv._DUMMY_ENERGY_MAX, energy_before + 50), ticket_before - 10),
          (srv.puppet_energy(player), int(player.get("ticket") or 0)))
    check("the purchase pushes SyncPuppetEnergy_SC",
          msg(msgs, "PVZ.Cmd.Cmd_SyncPuppetEnergy_SC") is not None, [n for n, _b in msgs])

    # --- 13. the pre-fight window: the enemy squad, our plants and the 上阵傀儡 ---------------
    target = (srv.pb_varint_field(1, srv._DUMMY_FUBEN_TARGET) + srv.pb_varint_field(18, 1)
              + srv.pb_varint_field(19, 1) + srv.pb_varint_field(20, 2)
              + srv.pb_varint_field(21, 1))
    sc = srv.handle_battle_getfightinfo(srv.pb_bytes(1, target), player)
    check("傀儡战境 preview lists the enemy squad",
          srv.parse_repeated_submessages(sc, 4) != [], len(srv.parse_repeated_submessages(sc, 4)))
    check("傀儡战境 preview carries puppetformation (field 6)",
          len(srv.parse_repeated_submessages(srv.parse_repeated_submessages(sc, 6)[0], 1)) >= 1)
    check("傀儡战境 preview preloads our own plants",
          srv.parse_repeated_submessages(srv.parse_repeated_submessages(sc, 1)[0], 1) != [])

    # --- 14. 普通冒险只携带我方傀儡，僵尸侧不能出现傀儡 -------------------------------
    # clientDB 已补齐玩家傀儡的 battleCard/plan 行；普通冒险使用左侧 sideType=38 的傀儡上下文，
    # 右侧仍是普通僵尸 sideType=1，不发送敌方 puppetList。这样傀儡可以在回放中出手，同时不会
    # 被客户端误判到僵尸一侧。
    check("this repo's clientDB carries the 傀儡 卡行 (tools_puppet_battle_cards.py --apply)",
          srv.client_knows_puppet_cards(player) is True)
    real_ids = srv._BATTLECARD_IDS
    try:
        srv._BATTLECARD_IDS = set()                     # an UNPATCHED clientDB
        check("client_knows_puppet_cards(): False while battleCard has no 傀儡 row",
              srv.client_knows_puppet_cards(player) is False)
        bare = srv.build_fight_report(player, 1, 1, 1, True, None, False, [], [], b"")
        check("unpatched clientDB -> AUTO sends NO puppetList on either side",
              srv.parse_repeated_submessages(side(bare, 2), 16) == []
              and srv.parse_repeated_submessages(side(bare, 3), 16) == [],
              len(srv.parse_repeated_submessages(side(bare, 2), 16)))
        srv._BATTLECARD_IDS = {"999999"}                # patched, but not for THIS line-up
        check("a patch that misses this player's 傀儡 still counts as unknown",
              srv.client_knows_puppet_cards(player) is False)
    finally:
        srv._BATTLECARD_IDS = real_ids
    auto = srv.build_fight_report(player, 1, 1, 1, True, None, False, [], [], b"",
                                  puppet_bytes=srv._auto_puppet_list(player), side_types=(38, 1))
    check("patched clientDB -> a NORMAL report carries only the player's puppetList",
          len(srv.parse_repeated_submessages(side(auto, 2), 16)) == len(srv.puppet_lineup(player))
          and srv.parse_repeated_submessages(side(auto, 3), 16) == [],
          (len(srv.parse_repeated_submessages(side(auto, 2), 16)),
           len(srv.parse_repeated_submessages(side(auto, 3), 16))))
    check("an explicit b\"\" still means no puppetList at all",
          srv.parse_repeated_submessages(side(srv.build_fight_report(
              player, 1, 1, 1, True, None, False, [], [], b"", puppet_bytes=b""), 2), 16) == [])
    # End to end: adventure fights carry only the player's puppetList and puppetActor actions.
    fuben_target = (srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
                    + srv.pb_varint_field(3, 1) + srv.pb_varint_field(4, 1))
    real = force_win()
    try:
        msgs = srv.handle_battle_fight(srv.pb_bytes(1, fuben_target), player)
    finally:
        srv.resolve_battle = real
    adv_report = srv.parse_repeated_submessages(msg(msgs, "PVZ.Cmd.Cmd_Battle_Fight_SC"), 1)[0]
    adv_rounds = srv.parse_repeated_submessages(adv_report, 4)
    right_positions = {fields(d).get(1) for d in srv.parse_repeated_submessages(side(adv_report, 3), 2)}
    replay_actions = [a for rnd in adv_rounds for a in srv.parse_repeated_submessages(rnd, 2)]
    enemy_kill_actions = [i for i, action in enumerate(replay_actions)
                          if any(fields(d).get(1) in right_positions and fields(d).get(7) == 1
                                 for d in srv.parse_repeated_submessages(action, 2))]
    left_rewards = srv.parse_repeated_submessages(side(adv_report, 2), 3)
    check("adventure returns the decided win + reward and records stage progress",
          fields(adv_report).get(1) == 0 and bool(left_rewards)
          and 1 in store.get_fuben_passed(UIN, 1, 1),
          (fields(adv_report).get(1), len(left_rewards), store.get_fuben_passed(UIN, 1, 1)))
    check("adventure's final enemy death is the final replay action (no post-kill puppet/fade wait)",
          bool(enemy_kill_actions) and enemy_kill_actions[-1] == len(replay_actions) - 1
          and bool(adv_rounds) and bool(srv.parse_repeated_submessages(adv_rounds[-1], 2)),
          (enemy_kill_actions, len(replay_actions), len(adv_rounds)))
    acts = [a for rnd in srv.parse_repeated_submessages(adv_report, 4)
            for a in srv.parse_repeated_submessages(rnd, 2)
            if srv.parse_repeated_submessages(a, 6)]
    listed = srv.parse_repeated_submessages(side(adv_report, 2), 16)
    check("an adventure fight carries player puppets, not enemy puppets",
          len(listed) == len(srv.puppet_lineup(player)) and bool(acts)
          and not srv.parse_repeated_submessages(side(adv_report, 3), 16),
          (len(listed), len(acts), len(srv.parse_repeated_submessages(side(adv_report, 3), 16))))

    # --- 15. every command is wired up ------------------------------------------------------
    for name in ("Cmd_Puppet_GetInfo_CS", "Cmd_Puppet_GetFormation_CS", "Cmd_Puppet_SetFormation_CS",
                 "Cmd_Puppet_Recruit_CS", "Cmd_Puppet_UpgradeLevel_CS", "Cmd_Puppet_UpgradeStar_CS",
                 "Cmd_Puppet_UpgradeQuality_CS", "Cmd_Puppet_UpgradeSkillLevel_CS",
                 "Cmd_Puppet_UpgradeSkillQuality_CS", "Cmd_Puppet_UpgradeSparLevel_CS",
                 "Cmd_Puppet_UpgradeSparQuality_CS", "Cmd_Puppet_RecycleSparDraw_CS",
                 "Cmd_Puppet_Swap_CS", "Cmd_Puppet_GetExtractInfo_CS", "Cmd_Puppet_Extract_CS",
                 "Cmd_Market_BuyPuppetEnergy_CS", "Cmd_Market_BuyPuppetSkillVal_CS",
                 "Cmd_PuppetFuben_GetChapterInfo_CS", "Cmd_PuppetFuben_GetStageInfo_CS",
                 "Cmd_PuppetFuben_GetFormation_CS", "Cmd_PuppetFuben_SetFormation_CS",
                 "Cmd_PuppetFuben_Fight_CS", "Cmd_PuppetFuben_Sweep_CS"):
        check("%s registered" % name.rsplit("_CS", 1)[0],
              callable(srv.REQUEST_HANDLERS.get("PVZ.Cmd." + name)))

    print("傀儡 (puppets): %s" % ("FAIL %s" % FAILED if FAILED else "PASS"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
