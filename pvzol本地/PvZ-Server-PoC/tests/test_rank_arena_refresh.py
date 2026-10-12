"""Regression checks for 天梯竞技场「换一批」 (Cmd_RankArena_GetOpponentInfo_CS).

Two independent defects made the button look dead:

* RankArenaGlobal.RANK_ARENA_OPPONENT_NUM is 3 (the SWF class initializer pushes 3) and
  RankArenaWdMediator.onChangeOpponent() walks
      for (i = 0; i < RANK_ARENA_OPPONENT_NUM; i++) view.opponentArr[i].setData(data.opponents[i])
  on a Vector. Fewer than three opponents throws `RangeError #1125` mid-loop, so the
  `btnChange.enabled = true` right after it is never reached and the button never comes back.

* The handler used to call _refresh_arena_npcs(..., every bot), which re-ran bot account
  generation AND built a full lineup for the whole ~50-bot roster on every single click. The
  runtime log shows 09:10:43 request -> 09:10:51 "bots: 50 slots, 50 opponents generated" ->
  09:10:54 reply: 11 seconds per press.

Both are pinned here: the reply must always carry exactly RANK_ARENA_OPPONENT_NUM rows, must rotate
between presses, and must not regenerate bots or build lineups.
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_toprank_"), "test.db")
store.init_db()
import bots_local  # noqa: E402
import pvz_poc_server as srv  # noqa: E402

UIN = 700006633
FAILED = []


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def opponents(body, field=1):
    return [srv.parse_varint_fields(raw) for raw in srv.parse_repeated_submessages(body, field)]


def refresh(player):
    msgs = srv.handle_rankarena_getopponentinfo(b"", player)
    assert msgs[0][0] == "PVZ.Cmd.Cmd_RankArena_GetOpponentInfo_SC"
    return opponents(msgs[0][1])


def main():
    player, _ = store.get_or_create_player(UIN)

    # --- 1. the constant the client hard-codes --------------------------------------------
    check("the panel's cell count matches the SWF constant", srv._RANK_ARENA_OPPONENT_NUM == 3,
          srv._RANK_ARENA_OPPONENT_NUM)

    # --- 2. a roster-less server still ships the full cell count ---------------------------
    # _make_roster() fills _BOTS at import, so the "no opponents at all" case is forced by
    # stubbing the roster away. The reply must STILL carry the panel's three cells (padded),
    # because that is exactly the RangeError #1125 situation.
    real_bots, real_pick = bots_local.bots, bots_local.pick_opponent
    bots_local.bots = lambda: []
    bots_local.pick_opponent = lambda score, exclude=(): None
    try:
        rows = refresh(player)
    finally:
        bots_local.bots, bots_local.pick_opponent = real_bots, real_pick
    check("换一批 never ships fewer than the cell count", len(rows) == 3, len(rows))
    # first(roleId) / fourth(level) / eighth(rank) are the varint fields of the row DTO;
    # the second is the UTF-8 name and fourth(5) the portrait URL, so they are not varints.
    check("the padding rows are still well-formed DTOs",
          all(r.get(1, 0) > 0 and r.get(4, 0) > 0 and r.get(8, 0) > 0 for r in rows), rows)

    # --- 3. with a roster: always exactly three, and the roster rotates -------------------
    roster = bots_local.bots()
    check("the roster seeded", len(roster) >= 3, len(roster))
    calls = []
    real_regenerate = bots_local.regenerate
    real_units = srv._arena_opponent_units

    def counting_regenerate(uins=None):
        calls.append("regenerate")
        return real_regenerate(uins)

    def counting_units(role_id):
        calls.append("units")
        return []

    bots_local.regenerate = counting_regenerate
    srv._arena_opponent_units = counting_units
    try:
        first = refresh(player)
        check("a live roster still ships exactly the cell count", len(first) == 3, len(first))
        check("换一批 no longer regenerates bot accounts", calls == [], calls)
        seen = [tuple(r.get(1) for r in first)]
        for _ in range(4):
            seen.append(tuple(r.get(1) for r in refresh(player)))
        check("换一批 actually changes who is offered", len(set(seen)) > 1, seen)
        check("no opponent equals the player",
              all(u != UIN for group in seen for u in group), UIN)
        check("every offered opponent is a real bot",
              all(bots_local.is_bot(u) for group in seen for u in group))
        check("no lineup is built just to draw the list", calls == [], calls)
    finally:
        bots_local.regenerate = real_regenerate
        srv._arena_opponent_units = real_units

    # --- 4. the reply is FAST (the 11-second freeze) --------------------------------------
    start = time.time()
    for _ in range(20):
        refresh(player)
    elapsed = time.time() - start
    check("20 consecutive 换一批 presses stay under a second", elapsed < 1.0, "%.3fs" % elapsed)

    # --- 5. GetMyInfo ships the same three cells + a three-slot podium --------------------
    msgs = srv.handle_rankarena_getmyinfo(b"", player)
    check("myinfo answers its own SC", msgs[0][0] == "PVZ.Cmd.Cmd_RankArena_GetMyInfo_SC", msgs[0][0])
    body = msgs[0][1]
    check("myinfo ships the challenge cells (field 10)",
          len(opponents(body, 10)) == srv._RANK_ARENA_OPPONENT_NUM, len(opponents(body, 10)))
    check("myinfo ships a three-slot podium (field 11)",
          len(opponents(body, 11)) == max(srv._RANK_ARENA_TOP_N, 3), len(opponents(body, 11)))

    # --- 6. a challenge still finds the opponent it was shown ------------------------------
    target = opponents(msgs[0][1], 10)[0].get(1)
    srv.run_battle = lambda left, right, **kw: (True, 0, 0, [])
    reply = srv.handle_rankarena_challenge(
        srv.pb_int64_field(1, target) + srv.pb_varint_field(2, 0), player)
    check("challenging a listed opponent answers",
          reply[0][0] == "PVZ.Cmd.Cmd_RankArena_Challenge_SC", reply[0][0])

    print("rank arena 换一批: %s" % ("FAIL %s" % FAILED if FAILED else "PASS"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
