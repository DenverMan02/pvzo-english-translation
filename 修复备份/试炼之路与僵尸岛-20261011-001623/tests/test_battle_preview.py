"""Regression checks for the pre-fight lineup == the fight itself (enemy zombie types).

The bug: BattleLineupWindow preloads/previews the enemy squad from Cmd_Battle_GetFightInfo_SC before a
fight, but the fight built its own squad — a randomised themed one for a target the preview request can't
identify (Trial Road / Nightmare / Gene Trial send 0/0/0 fuben ids) — so the zombies on the lawn did not
match the ones in the battle. These checks pin the contract: the preview shows the fight's real squad and
the fight follows the preview.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_preview_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700001337
FAILED = []


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def card_ids(units):
    return [(u["pos"], u["card_id"]) for u in units]


def preview_cards(sc_body):
    """The cardIds the pre-fight window would paint (Dto_EnemyCardInfo.cardId(2) of each enemyCard)."""
    out = []
    for raw in srv.parse_repeated_submessages(sc_body, 4):
        out.append(srv.parse_varint_fields(raw).get(2))
    return out


def zero_fuben_target():
    """A Trial Road / Nightmare / Gene Trial target: the ids we can't read, so all-zero fuben fields."""
    target = (srv.pb_varint_field(1, 11) + srv.pb_varint_field(2, 0)
              + srv.pb_varint_field(3, 0) + srv.pb_varint_field(4, 0))
    return srv.pb_bytes(1, target)


def fuben_target(chapter, mode, stage):
    target = (srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, chapter)
              + srv.pb_varint_field(3, mode) + srv.pb_varint_field(4, stage))
    return srv.pb_bytes(1, target)


def main():
    player, _new = store.get_or_create_player(UIN)
    player["plants"] = store.get_player_plants(UIN)
    srv.ensure_player_cards(player)
    store.save_player(UIN, level=60, tr_current=21, tr_hard_current=4)
    player["tr_current"], player["tr_hard_current"] = 21, 4

    # --- 1. an adventure preview is the stage's real squad -------------------------------
    sc = srv.handle_battle_getfightinfo(fuben_target(1, 1, 1), player)
    wiki = srv._build_wiki_enemies(1, 1) or srv._build_fallback_enemies(1, 1)
    check("adventure preview == stage squad", preview_cards(sc) == [u["card_id"] for u in wiki],
          preview_cards(sc))

    # --- 2. Trial Road preview == the squad handle_screwup_fight will field ---------------
    srv.note_battle_scene(player, "screwup")
    sc = srv.handle_battle_getfightinfo(zero_fuben_target(), player)
    stage = 22                                        # the client fights currentStage + 1
    expect = srv._tr_enemy_squad(srv.load_trial_road()[stage]["power"], stage)
    check("trial-road preview == trial-road fight squad",
          preview_cards(sc) == [u["card_id"] for u in expect], preview_cards(sc))
    check("trial-road preview power == fight power",
          srv.parse_varint_fields(sc).get(5) == sum(u["power"] for u in expect),
          srv.parse_varint_fields(sc).get(5))

    captured = {}

    real_run_battle = srv.run_battle

    def capture_run_battle(left, right, **kwargs):
        captured["right"] = [dict(u) for u in right]
        _won, lp, rp, rounds = real_run_battle(left, right, **kwargs)
        return False, lp, rp, rounds  # Preserve the reward-free preview fixture.

    srv.run_battle = capture_run_battle
    try:
        srv.handle_screwup_fight(srv.pb_varint_field(1, stage), player)
    finally:
        srv.run_battle = real_run_battle
    check("fight fields the previewed zombies",
          [u["card_id"] for u in captured.get("right", [])] == preview_cards(sc),
          [u["card_id"] for u in captured.get("right", [])])

    # --- 3. Nightmare follows its own climb (2x power) -----------------------------------
    srv.note_battle_scene(player, "newscrewup")
    sc = srv.handle_battle_getfightinfo(zero_fuben_target(), player)
    nm_stage = 5
    nm_expect = srv._tr_enemy_squad(max(1, srv.load_trial_road()[nm_stage]["power"] * 2), nm_stage)
    check("nightmare preview == nightmare fight squad",
          preview_cards(sc) == [u["card_id"] for u in nm_expect], preview_cards(sc))

    # --- 4. the fallback squad is deterministic per stage --------------------------------
    a = [(u["pos"], u["card_id"]) for u in srv._build_fallback_enemies(3, 4)]
    b = [(u["pos"], u["card_id"]) for u in srv._build_fallback_enemies(3, 4)]
    check("fallback squad is stable for one stage", a == b, a)

    # --- 5. the override swaps TYPES only (stats/power stay the fight's) ------------------
    srv._PENDING_PREVIEW.pop(UIN, None)
    srv.remember_battle_preview(player, [{"pos": 4, "card_id": 190}, {"pos": 8, "card_id": 161}],
                                "fuben", "9/1/1")
    fight_units = [{"pos": 4, "card_id": 111, "hp": 500, "atk": 50, "def_": 5, "skill1": 1,
                    "skill2": 2, "level": 1, "is_leader": True, "power": 700, "name": "木乃伊"},
                   {"pos": 8, "card_id": 121, "hp": 400, "atk": 40, "def_": 4, "skill1": 1,
                    "skill2": 2, "level": 1, "is_leader": False, "power": 600, "name": "路障"}]
    out = srv.apply_previewed_enemy_types(player, fight_units, "fuben", "9/1/1")
    check("override takes the previewed types", card_ids(out) == [(4, 190), (8, 161)], card_ids(out))
    check("override keeps the fight's stats",
          [(u["hp"], u["atk"], u["power"]) for u in out] == [(500, 50, 700), (400, 40, 600)])
    other = [{"pos": 4, "card_id": 111, "hp": 1, "atk": 1, "def_": 0, "skill1": 1, "skill2": 2,
              "level": 1, "is_leader": True, "power": 10, "name": "木乃伊"}]
    check("override refuses another stage",
          card_ids(srv.apply_previewed_enemy_types(player, other, "fuben", "1/1/1")) == [(4, 111)])
    check("override refuses another mode",
          card_ids(srv.apply_previewed_enemy_types(
              player, [dict(other[0])], "screwup", "9/1/1")) == [(4, 111)])

    # --- 6. gene trial preview == gene trial fight ---------------------------------------
    player["_gene_mode"], player["gene_test_last_stage"] = 0, 1
    srv.note_battle_scene(player, "gene")
    sc = srv.handle_battle_getfightinfo(zero_fuben_target(), player)
    gene = srv._gene_test_enemy_units(player, 1, 0, 1)
    check("gene-trial preview == gene-trial fight enemy",
          bool(gene) and preview_cards(sc) == [u["card_id"] for u in gene], preview_cards(sc))

    # --- 7. the activity preview and its fight read the SAME fuben id ---------------------
    sc = srv.handle_activity_getfightinfo(srv.pb_varint_field(1, 3) + srv.pb_varint_field(2, 14), player)
    sea = srv._activity_enemy_units(14)
    check("activity preview follows fubenId(2)",
          preview_cards(sc) == [u["card_id"] for u in sea], preview_cards(sc))

    # --- 8. the battle REPORT carries 先攻 and never has an action-less round -------------
    # Two client-side contract points discovered from a live 立即结束 crash:
    #   * the battle HUD prints ONE 先攻 number per side ("Zombies 先攻: ???" when Dto_FightSide
    #     .totalSpeed(15) is missing) fed by each Dto_FightCardInfo.powerEle(18) {value, speed};
    #   * 立即结束 fast-forwards to the END of fightReplay, and the round it lands on is walked
    #     action-by-action — an action-less round there throws #1009 and wedges the round state machine.
    srv.run_battle = capture_run_battle
    try:
        msgs = srv.handle_screwup_fight(srv.pb_varint_field(1, stage), player)
    finally:
        srv.run_battle = real_run_battle
    report = srv.parse_repeated_submessages(msgs[0][1], 3)[0]
    sides = srv.parse_repeated_submessages(report, 2) + srv.parse_repeated_submessages(report, 3)
    check("report carries both sides", len(sides) == 2, len(sides))
    side_speeds, card_speeds, cards_seen = [], [], 0
    for side in sides:
        fields = srv.parse_varint_fields(side)
        cards = srv.parse_repeated_submessages(side, 2)
        cards_seen += len(cards)
        side_speeds.append(fields.get(15))
        card_speeds.append([
            fields.get(3)
            for c in cards for pe in srv.parse_repeated_submessages(c, 18)
            for fields in [srv.parse_varint_fields(pe)] if fields.get(1) == 0])
    check("every side carries totalSpeed(15)", all(s is not None for s in side_speeds), side_speeds)
    check("totalSpeed == the cards' summed 先攻",
          side_speeds == [sum(s) for s in card_speeds], (side_speeds, card_speeds))
    check("every battle card carries a powerEle speed",
          cards_seen > 0 and all(len(s) == len(srv.parse_repeated_submessages(side, 2)) for side, s
                                 in zip(sides, card_speeds)) and
          all(v is not None for s in card_speeds for v in s), card_speeds)
    rounds = srv.parse_repeated_submessages(report, 4)
    check("report has a replay", bool(rounds), len(rounds))
    empty_rounds = [i for i, r in enumerate(rounds)
                    if not srv.parse_repeated_submessages(r, 2)]
    check("no action-less round in the replay (立即结束 safe)", not empty_rounds, empty_rounds)

    # The backyard/island LINEUP gets the same 先攻: BattleCardFormationVO derives its 总先攻 by summing
    # each cardPos' powerEle speed, so a missing/0 speed made the lineup panel print 总先攻 0.
    formation = srv.handle_battle_getformation(b"", player)
    cardpos = srv.parse_repeated_submessages(srv.parse_repeated_submessages(formation, 1)[0], 1)
    line_powers = [srv.parse_varint_fields(pe)
                   for c in cardpos for pe in srv.parse_repeated_submessages(c, 6)]
    check("formation cardPos carries powerEle value(2)+speed(3)",
          bool(line_powers) and all(p.get(2) is not None for p in line_powers) and
          all(p.get(3) is not None for p in line_powers if p.get(1) == 0), line_powers)

    for name in ("PVZ.Cmd.Cmd_Battle_GetFightInfo_CS", "PVZ.Cmd.Cmd_Battle_Fight_CS",
                 "PVZ.Cmd.Cmd_ScrewUp_Fight_CS", "PVZ.Cmd.Cmd_NewScrewUp_Fight_CS",
                 "PVZ.Cmd.Cmd_GeneTest_Fight_CS", "PVZ.Cmd.Cmd_Battle_GetActivityFubenFightInfo_CS"):
        check("%s registered" % name.rsplit(".", 1)[-1], callable(srv.REQUEST_HANDLERS.get(name)))

    print("battle preview: %s" % ("FAIL %s" % FAILED if FAILED else "PASS"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
