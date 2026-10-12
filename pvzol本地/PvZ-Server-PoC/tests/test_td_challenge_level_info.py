"""Tower-defence challenge ("塔防挑战") must not #1009 on the way into a challenge level.

Cmd_TD_GetChallengeLevelInfo_CS was never registered, and its SC
(Cmd_TD_GetChallengeLevelInfo_SC) has exactly ONE field — `infos`, a
RepeatedFieldDescriptor$TYPE_MESSAGE — which the generic fallback in populate_message()
used to skip (its regex has a `(?<!Repeated)` lookbehind). An _SC with no recognised
fields therefore serialised to 0 bytes, the client's infos[] came back empty,
LevelSelectProxy.getChallengeLevelInfo() returned null, and every unguarded caller
crashed:

    TDProxyCommand.requestStartTDGame          coerce -> use
    TDGameCommand.challengeRestartGame         .remainFreeChallengeTimes
    LSLevelChallengeEnterWdMediator.updateInheritInfo   .isCanInherit

i.e. TypeError #1009 and the blank panel the player reported when tapping 困难.

Two invariants are pinned here: the command is registered and answers with the real
authored list, and the generic fallback no longer silently produces 0 bytes for an _SC
whose fields are all repeated.
"""
import os
import sys
import tempfile
import unittest

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_td_challenge_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700003001
CS = "PVZ.Cmd.Cmd_TD_GetChallengeLevelInfo_CS"
SC = "PVZ.Cmd.Cmd_TD_GetChallengeLevelInfo_SC"


def parse_infos(body):
    """[(stageId, levelId, subLevelId, challengeLevelId, remainTimes, isCanInherit,
    iLastWeekScore)] from the SC body, one tuple per infos element."""
    out = []
    for dto in srv.parse_repeated_submessages(body, 1):
        f = srv.parse_varint_fields(dto)
        out.append((f.get(1, 0), f.get(2, 0), f.get(3, 0), f.get(4, 0),
                    f.get(5, 0), f.get(6, 0), f.get(7, 0)))
    return out


class TdChallengeLevelInfoTest(unittest.TestCase):
    def setUp(self):
        self.player, _ = store.get_or_create_player(UIN)

    def test_command_is_registered(self):
        self.assertIn(CS, srv.REQUEST_HANDLERS,
                      "Cmd_TD_GetChallengeLevelInfo_CS has no handler, so the reply falls "
                      "through to the generic fallback and infos[] arrives empty")

    def test_reply_carries_every_authored_challenge_level(self):
        body = srv.handle_td_getchallengelevelinfo(b"", self.player)
        rows = parse_infos(body)
        authored = srv.load_td_challenge_levels()
        self.assertTrue(authored, "no challenge levels parsed from levelselectInfo.txt")
        self.assertGreaterEqual(len(rows), len(authored))
        seen = {(r[0], r[1], r[2], r[3]) for r in rows}
        for key in authored:
            self.assertIn(key, seen,
                          "challenge level %s missing — the client lookup returns null" % (key,))

    def test_every_entry_has_all_seven_fields_populated(self):
        """All of Dto_TD_ChallengeLevelInfo's fields are TYPE_UINT32, so a missing one is
        indistinguishable from a legitimate 0. remainTimes must be > 0 (the client hides the
        free-challenge entry otherwise) and isCanInherit must be set (updateInheritInfo
        reads it unconditionally)."""
        rows = parse_infos(srv.handle_td_getchallengelevelinfo(b"", self.player))
        self.assertTrue(rows)
        for row in rows:
            stage, level, sub, chl, remain, inherit, _last = row
            self.assertGreater(stage, 0, row)
            self.assertGreater(level, 0, row)
            self.assertGreater(sub, 0, row)
            self.assertGreater(chl, 0, row)
            self.assertGreater(remain, 0, "remainTimes=0 hides the free-challenge entry: %s"
                               % (row,))
            self.assertEqual(inherit, 1, "isCanInherit must be 1: %s" % (row,))

    def test_no_duplicate_keys(self):
        rows = parse_infos(srv.handle_td_getchallengelevelinfo(b"", self.player))
        keys = [(r[0], r[1], r[2], r[3]) for r in rows]
        self.assertEqual(len(keys), len(set(keys)), "duplicate challenge entries")

    def test_generic_fallback_no_longer_returns_zero_bytes(self):
        """The root cause: an _SC whose ONLY field is repeated used to serialise empty."""
        body = srv.populate_message("Cmd_TD_GetChallengeLevelInfo_SC")
        self.assertNotEqual(body, b"",
                            "populate_message still skips RepeatedFieldDescriptor$TYPE_MESSAGE")
        # One empty Dto_TD_ChallengeLevelInfo: present, so getChildAt-style iteration finds
        # an object instead of tripping over a null.
        self.assertEqual(len(srv.parse_repeated_submessages(body, 1)), 1)

    def test_repeated_fields_do_not_disturb_single_message_fields(self):
        """A message with both kinds must still emit its single submessage fields."""
        msgs, i64s, reps = srv._fields("Cmd_TD_ReportLevelResult_SC")
        self.assertTrue(reps, "expected at least one repeated field on ReportLevelResult")
        for num, dto in msgs:
            self.assertIn(num, {n for n, _ in reps},
                          "field %d counted as both single and repeated" % num)


class TdChallengeDataTest(unittest.TestCase):
    def test_challenge_rows_exclude_the_plain_levels(self):
        """load_level_rewards() skips challengeLevelId != 0; load_td_challenge_levels() must
        return exactly those skipped rows, or the two would disagree about a level."""
        rows = srv._read_clientdb_table("levelselectInfo.txt")
        col = {name: i for i, name in enumerate(rows[0])}
        authored_challenges = set()
        for r in rows[1:]:
            if len(r) > col["challengeLevelId"] and r[col["challengeLevelId"]].strip() not in ("0", ""):
                authored_challenges.add((int(r[col["stageId"]]), int(r[col["levelId"]]),
                                         int(r[col["subLevelId"]]),
                                         int(r[col["challengeLevelId"]])))
        self.assertEqual(set(srv.load_td_challenge_levels()), authored_challenges)
        for key in authored_challenges:
            self.assertNotIn(key, srv.load_level_rewards(),
                             "%s is a challenge level, not a main-line reward row" % (key,))


if __name__ == "__main__":
    unittest.main()
