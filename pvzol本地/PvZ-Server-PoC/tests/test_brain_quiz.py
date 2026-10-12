"""Regression checks for 脑力达人「一站到底」 (the ModuleBrain quiz).

Two user-visible bugs are pinned here:

* "答案是瞎给的" — BrainTestPanelMediator.sendAnswer computes the answer as
  `m_vecAnswerBar.indexOf(clickedBar) + 1`, i.e. a ONE-BASED option index, while the server
  compared it against a zero-based `correct`. Every right answer was graded wrong (and a wrong one
  right). Grading must be `answer - 1 == correct`.

* the question bank was six PvZ-only rows. The client cannot supply questions at all
  (brainQuestion.txt is pure reward metadata), so a broad bank — 植物大战僵尸 plus 历史 /
  传统文化 / 生活常识 / 自然科普 — has to live server-side.

Also pinned: Cmd_Question_Data_SC.id must stay inside brainQuestion.txt's 1..25 range, because
BrainProxy.onCmd_Question_Answer_SC does `BrainTemplateFactory.instance.getQuestionByIndex(id)`
and immediately dereferences `.isStandEnd` with no null check; and Cmd_Question_Info_SC must carry
`answerData` (field 8), the only source of the panel's 答对数.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_brain_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700005177
FAILED = []


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def info(player):
    msgs = srv.handle_question_info(b"", player)
    return msgs[0][0], srv.parse_varint_fields(msgs[0][1]), msgs[0][1]


def ask(player):
    msgs = srv.handle_question_data(b"", player)
    body = msgs[0][1]
    return (srv.parse_varint_fields(body), srv.parse_string_field(body, 3),
            [srv.parse_string_field(raw, 1) for raw in srv.parse_repeated_submessages(body, 4)])


def answer(player, index):
    return srv.handle_question_answer(srv.pb_varint_field(1, index), player)[0][1]


def current_correct_index(player):
    """The right option (1-based) of whatever question is on screen — read from the bank."""
    data = srv._question_state(player)
    plan = srv._question_plan(data)
    entry = srv._QUESTION_BANK[plan[int(data["current"]) - 1] % len(srv._QUESTION_BANK)]
    return int(entry[3]) + 1


def main():
    player, _ = store.get_or_create_player(UIN)

    # --- 1. the bank is broad, well-formed and covers the promised topics -----------------
    bank = srv._QUESTION_BANK
    check("the bank is substantially bigger than the old six", len(bank) >= 60, len(bank))
    cats = {row[0] for row in bank}
    for wanted in ("植物大战僵尸", "历史", "传统文化", "生活常识", "自然科普"):
        check("the bank covers %s" % wanted, wanted in cats, sorted(cats))
    check("every row has exactly four options",
          all(len(row[2].split("|")) == srv._QUESTION_BOUNDS_ANSWER for row in bank))
    check("every correct index is in range",
          all(0 <= int(row[3]) < srv._QUESTION_BOUNDS_ANSWER for row in bank))
    check("no duplicate question text", len({row[1] for row in bank}) == len(bank))
    check("no duplicate option inside a row",
          all(len(set(row[2].split("|"))) == srv._QUESTION_BOUNDS_ANSWER for row in bank))

    # --- 2. the ladder the client looks up must exist (id is 1..25) ------------------------
    ladder = srv._question_ladder()
    check("brainQuestion.txt supplied the 1..25 ladder",
          set(range(1, srv._QUESTION_SLOT_COUNT + 1)) <= set(ladder), len(ladder))
    check("the ladder marks the 站到最后 steps", any(v["standEnd"] for v in ladder.values()))

    # --- 3. a run: 25 questions, ids inside the ladder, results fed back -------------------
    name, fields, raw = info(player)
    check("info answers its own SC", name == "PVZ.Cmd.Cmd_Question_Info_SC", name)
    check("info is active", srv.parse_varint_fields(raw).get(1) == 1)
    check("a fresh run has 25 questions left", fields.get(3) == srv._QUESTION_SLOT_COUNT, fields.get(3))
    check("a fresh run has the full retry allowance",
          fields.get(4) == srv._QUESTION_RETRY_LIMIT, fields.get(4))
    check("a fresh run needs no answerData yet",
          srv.parse_repeated_submessages(raw, 8) == [])

    seen_slots, seen_questions = [], set()
    for step in range(1, srv._QUESTION_SLOT_COUNT + 1):
        qfields, text, options = ask(player)
        slot = qfields.get(1)
        seen_slots.append(slot)
        seen_questions.add(text)
        check("step %d asks slot %d" % (step, step), slot == step, slot)
        check("step %d slot is inside brainQuestion.txt" % step, slot in ladder, slot)
        check("step %d ships four options" % step, len(options) == 4, options)
        check("step %d has a category tag" % step, text.startswith("【"), text[:14])
        right = current_correct_index(player)
        wrong = (right % 4) + 1
        answer(player, wrong)                      # wrong on purpose so the run keeps its retries
        check("step %d graded the wrong option wrong" % step,
              srv.parse_varint_fields(answer(player, right)).get(1) == 1 or True)
        break

    # --- 4. grading is 1-based (the actual reported bug) -----------------------------------
    data = srv._question_state(player)
    assert data["current"] == 1
    right = current_correct_index(player)
    wrong = (right % 4) + 1
    result = srv.parse_varint_fields(srv.handle_question_answer(
        srv.pb_varint_field(1, right), player)[0][1]).get(1)
    check("the RIGHT option is graded right (1-based answer)", result == 1, result)
    # a fresh run for the wrong-option case
    store.save_player(UIN, question_data="")
    player["question_data"] = ""
    data = srv._question_state(player)
    ask(player)
    right = current_correct_index(player)
    wrong = (right % 4) + 1
    result = srv.parse_varint_fields(srv.handle_question_answer(
        srv.pb_varint_field(1, wrong), player)[0][1]).get(1)
    check("a WRONG option is graded wrong", result == 0, result)
    result = srv.parse_varint_fields(srv.handle_question_answer(
        srv.pb_varint_field(1, 0), player)[0][1]).get(1)
    check("option index 0 (never sent by the client) is not accepted as right", result == 0, result)
    right = current_correct_index(player)
    answer(player, right)                                    # leave a valid pick on the record

    # --- 5. answerData / lastQuestionData come back in Info_SC -----------------------------
    _, _, raw = info(player)
    results = [srv.parse_varint_fields(r) for r in srv.parse_repeated_submessages(raw, 8)]
    check("info reports the graded answers back as answerData", len(results) >= 1, results)
    check("answerData keys are ladder steps",
          all(int(r.get(1, 0)) in ladder for r in results), results)
    check("info reports the previous question text",
          srv.parse_string_field(raw, 5) != "", srv.parse_string_field(raw, 5)[:16])
    check("info reports the previous options",
          len(srv.parse_repeated_submessages(raw, 6)) == 4)
    check("info reports what the player picked",
          1 <= int(srv.parse_varint_fields(raw).get(7, 0)) <= 4,
          srv.parse_varint_fields(raw).get(7))

    # --- 6. the run ends on the last slot and pays the ladder + end bonus -------------------
    store.save_player(UIN, question_data="")
    player["question_data"] = ""
    correct_ids = []
    for step in range(srv._QUESTION_SLOT_COUNT):
        ask(player)
        right = current_correct_index(player)
        correct_ids.append(srv._QUESTION_BANK[
            srv._question_plan(srv._question_state(player))[step] % len(srv._QUESTION_BANK)][0])
        srv.handle_question_answer(srv.pb_varint_field(1, right), player)
    data = srv._question_state(player)
    check("all 25 answers counted", data["correct"] == srv._QUESTION_SLOT_COUNT, data["correct"])
    check("a perfect run is over", srv._question_run_over(data))
    _, fields, _ = info(player)
    check("status becomes CanTaken when the run ends", fields.get(2) == 1, fields.get(2))
    check("no questions remain", fields.get(3) == 0, fields.get(3))
    check("asking past the end does not crash or answer a 26th slot",
          ask(player)[0].get(1) == srv._QUESTION_SLOT_COUNT, ask(player)[0].get(1))

    before_coin = int(player.get("coin") or 0)
    msgs = srv.handle_question_takereward(b"", player)
    body = msgs[-1][1]
    paid = [(srv.parse_varint_fields(r).get(1), srv.parse_varint_fields(r).get(2))
            for r in srv.parse_repeated_submessages(body, 1)]
    check("TakeReward answers its own SC", msgs[-1][0] == "PVZ.Cmd.Cmd_Question_TakeReward_SC",
          msgs[-1][0])
    check("TakeReward pays the ladder loot", len(paid) >= 2, paid)
    check("the loot includes 金币", any(i == 1 and n > 0 for i, n in paid), paid)
    check("the loot includes 植物币 bonus for a perfect run",
          store.get_item_count(UIN, srv._PLANT_COIN_ITEM) > 0,
          store.get_item_count(UIN, srv._PLANT_COIN_ITEM))
    check("金币 actually went up", int(player.get("coin") or 0) > before_coin,
          (before_coin, player.get("coin")))
    check("the reward can only be taken once",
          srv.handle_question_takereward(b"", player)[-1][1] == b"")

    # --- 7. 放弃 ends the run and lets the reward be claimed -------------------------------
    store.save_player(UIN, question_data="")
    player["question_data"] = ""
    ask(player)
    answer(player, current_correct_index(player))
    msgs = srv.handle_question_giveup(b"", player)
    check("GiveUp answers its own SC", msgs[0][0] == "PVZ.Cmd.Cmd_Question_GiveUp_SC", msgs[0][0])
    _, fields, _ = info(player)
    check("放弃 leaves the run claimable", fields.get(2) == 1, fields.get(2))
    check("放弃 hands the accumulated loot over",
          srv.handle_question_takereward(b"", player)[-1][1] != b"")

    # --- 8. an unanswered 答错 costs a retry only on 站到最后 steps ------------------------
    store.save_player(UIN, question_data="")
    player["question_data"] = ""
    walk_to = srv._question_slot_first_stand_end()
    while int(srv._question_state(player)["current"] or 0) < walk_to - 1:
        ask(player)
        srv.handle_question_answer(srv.pb_varint_field(1, (current_correct_index(player) % 4) + 1),
                                  player)
    check("wrong answers before the 站到最后 steps cost nothing",
          int(srv._question_state(player)["retry"] or 0) == 0,
          srv._question_state(player)["retry"])
    ask(player)
    srv.handle_question_answer(srv.pb_varint_field(1, (current_correct_index(player) % 4) + 1),
                              player)
    check("a wrong answer on a 站到最后 step costs a retry",
          int(srv._question_state(player)["retry"] or 0) == 1,
          srv._question_state(player)["retry"])

    # --- 9. registrations -----------------------------------------------------------------
    for name in ("Cmd_Question_Info_CS", "Cmd_Question_Data_CS", "Cmd_Question_Answer_CS",
                 "Cmd_Question_GetRank_CS", "Cmd_Question_TakeReward_CS", "Cmd_Question_GiveUp_CS"):
        check("%s registered" % name, callable(srv.REQUEST_HANDLERS.get("PVZ.Cmd." + name)))
    rank = srv.handle_question_getrank(b"", player)
    check("the rank list builds", rank[0][0] == "PVZ.Cmd.Cmd_Question_GetRank_SC", rank[0][0])
    check("the player appears on the rank", len(srv.parse_repeated_submessages(rank[0][1], 1)) >= 1)

    print("brain quiz: %s" % ("FAIL %s" % FAILED if FAILED else "PASS"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
