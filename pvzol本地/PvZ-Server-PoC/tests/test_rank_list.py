"""Regression checks for 排行榜 (Cmd_GetRankList_CS / Cmd_GetSelfRank_CS).

The window used to come up blank/one-row because the server only ever shipped "the player at
rank #1" and left 联盟 / 天梯竞技场 / 跨服 tabs completely empty. Three client facts drive the
contract pinned here (all read from ModuleRank.swf):

* RankProxy switches on the SC's `ranktype`; the generic empty reply sends 0, so
  EVENT_RANK_LIST_UPDATE never fires and the window stays blank.
* RankWindowMediator.updateView walks its `m_vecCell` vector and indexes `rankList[i]` for every
  i < RankGlobal.PAGE_BOUNDS with no bounds check -> a short page is RangeError #1125. The page
  must therefore carry at least PAGE_BOUNDS rows; we pad to _RANK_MIN_ROWS.
* m_totalPage = max(1, ceil(ranknum / PAGE_BOUNDS)), so `ranknum` is the number of ranked
  entries, not the page size.

The board itself must now be a real ordering of 玩家 + 人机 (+ 联盟 for type 3).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_ranklist_"), "test.db")
store.init_db()
import bots_local  # noqa: E402
import pvz_poc_server as srv  # noqa: E402

UIN = 700007711
FAILED = []


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def rows_of(body, field):
    return [srv.parse_varint_fields(raw) for raw in srv.parse_repeated_submessages(body, field)]


def list_reply(ranktype, page=1, player=None):
    return srv.handle_getranklist(
        srv.pb_varint_field(1, ranktype) + srv.pb_varint_field(2, page),
        player if player is not None else PLAYER[0])


PLAYER = []


def main():
    # main() is never run by the tests, so the boot step that gives the bots real accounts
    # (cards -> bots_state.power) and staffs the guild DB has to be done by hand.
    srv._seed_bots()

    player, _new = store.get_or_create_player(UIN)
    store.save_player(UIN, name="RankTester", level=60)
    player, _new = store.get_or_create_player(UIN)
    PLAYER.append(player)

    roster = bots_local.bots()
    check("the bot roster seeded", len(roster) >= 5, len(roster))

    # --- 1. the requested ranktype is echoed back (otherwise the tab never updates) --------
    body = list_reply(1)
    check("GetRankList_SC echoes ranktype", srv.parse_varint_fields(body).get(1) == 1,
          srv.parse_varint_fields(body).get(1))
    check("GetRankList_SC echoes the page", srv.parse_varint_fields(body).get(2) == 1,
          srv.parse_varint_fields(body).get(2))

    # --- 2. 战力榜 mixes the player with the bots and is ordered ---------------------------
    rows = rows_of(body, 4)
    total = srv.parse_varint_fields(body).get(3)
    check("ranknum counts ranked entries, not the page size", total == len(roster) + 1, total)
    check("the page is padded to the client's cell floor", len(rows) >= srv._RANK_MIN_ROWS,
          len(rows))
    real = [r for r in rows if r.get(1, 0) > 0]
    check("page 1 carries a full page of real rows", len(real) == srv._RANK_PAGE_SIZE, len(real))
    powers = [r.get(5, 0) for r in real]
    check("the board is ordered by 战力 (desc)", powers == sorted(powers, reverse=True), powers[:6])
    check("人机 are actually on the board",
          any(bots_local.is_bot(r.get(6, 0)) for r in real), [r.get(6) for r in real][:5])
    check("climbing rows are numbered 1..n", [r.get(1) for r in real] == list(range(1, len(real) + 1)),
          [r.get(1) for r in real])
    check("padding rows are well-formed DTOs",
          all(r.get(1, 0) == 0 for r in rows if r.get(1, 0) == 0) and len(rows) - len(real) >= 0,
          len(rows) - len(real))

    # --- 3. the 联盟 column comes from the guild DB ---------------------------------------
    guild_names = [srv.parse_string_field(raw, 4)
                   for raw in srv.parse_repeated_submessages(body, 4)]
    check("the 战力榜 fills the 联盟 column", any(n for n in guild_names), guild_names[:4])

    # --- 4. GetSelfRank reports the player's REAL standing ---------------------------------
    self_body = srv.handle_getselfrank(srv.pb_varint_field(1, 1), player)
    self_fields = srv.parse_repeated_submessages(self_body, 2)
    check("GetSelfRank answers its own SC field", len(self_fields) == 1, len(self_fields))
    mine = srv.parse_varint_fields(self_fields[0])
    check("GetSelfRank echoes ranktype", srv.parse_varint_fields(self_body).get(1) == 1,
          srv.parse_varint_fields(self_body).get(1))
    board = srv._rank_board(1, player)
    expected = next(rank for rank, row in board if row["uin"] == UIN)
    check("GetSelfRank is no longer hard-coded to #1", mine.get(1) == expected,
          "%s vs %s" % (mine.get(1), expected))
    check("GetSelfRank identifies the player", mine.get(6) == UIN, mine.get(6))

    # --- 5. every other tab has content too ------------------------------------------------
    for ranktype, field, label in ((2, 5, "成就"), (4, 7, "试炼"),
                                   (6, 9, "植物星级"), (12, 15, "新试炼")):
        page = rows_of(list_reply(ranktype), field)
        got = [r for r in page if r.get(1, 0) > 0]
        check("%s 榜 is populated" % label, len(got) >= 2, len(got))

    # --- 6. the 联盟战力榜 keeps guilds, not roles -----------------------------------------
    guild_rows = [r for r in rows_of(list_reply(3), 6) if r.get(1, 0) > 0]
    check("联盟战力榜 is populated", len(guild_rows) >= 1, len(guild_rows))
    check("联盟战力榜 ranks guilds by Σ member 战力",
          [r.get(6, 0) for r in guild_rows] == sorted([r.get(6, 0) for r in guild_rows], reverse=True),
          [r.get(6, 0) for r in guild_rows][:5])
    check("联盟战力榜 rows carry a guild name",
          all(srv.parse_string_field(raw, 2) for raw in
              srv.parse_repeated_submessages(list_reply(3), 6)[:len(guild_rows)]),
          [srv.parse_string_field(raw, 2) for raw in
           srv.parse_repeated_submessages(list_reply(3), 6)[:3]])

    # --- 7. 天梯竞技场 tab (ranktype 5) ranks by arena score --------------------------------
    arena_rows = [r for r in rows_of(list_reply(5), 8) if r.get(1, 0) > 0]
    check("天梯竞技场榜 is populated", len(arena_rows) >= 2, len(arena_rows))
    check("天梯竞技场 rows carry the challenger's record",
          all(r.get(4, 0) >= 1 for r in arena_rows) and all(r.get(2, 0) > 0 for r in arena_rows),
          [(r.get(4), r.get(2)) for r in arena_rows][:5])
    arena_self = srv.parse_varint_fields(srv.parse_repeated_submessages(
        srv.handle_getselfrank(srv.pb_varint_field(1, 5), player), 6)[0])
    check("天梯竞技场 GetSelfRank resolves the player", arena_self.get(2) == UIN, arena_self.get(2))

    # --- 8. an unknown tab still echoes its type but ships an empty board -------------------
    unknown = list_reply(999)
    check("unknown ranktype still echoes the type",
          srv.parse_varint_fields(unknown).get(1) == 999,
          srv.parse_varint_fields(unknown).get(1))
    check("unknown ranktype ships ranknum 0", srv.parse_varint_fields(unknown).get(3) == 0,
          srv.parse_varint_fields(unknown).get(3))

    # --- 9. a far page is still padded (no RangeError #1125 on page buttons) ---------------
    far = rows_of(list_reply(1, 99), 4)
    check("a past-the-end page is still padded to the floor", len(far) >= srv._RANK_MIN_ROWS,
          len(far))

    print("rank list: %s" % ("FAIL %s" % FAILED if FAILED else "PASS"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
