"""The local guild entry flow must match the Flash client's requirements."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

import guild_local


class GuildLocalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db = Path(self.temp.name) / "players.db"
        with closing(sqlite3.connect(db)) as con, con:
            con.execute("CREATE TABLE players (uin INTEGER PRIMARY KEY, role_id INTEGER, "
                        "name TEXT, level INTEGER, face_id INTEGER, coin INTEGER, "
                        "contrib_val INTEGER NOT NULL DEFAULT 0)")
            con.execute("CREATE TABLE items (uin INTEGER, item_id INTEGER, count INTEGER, "
                        "PRIMARY KEY(uin,item_id))")
            con.execute("INSERT INTO players VALUES (42,42,'Tester',20,0,50000,0)")
        self.db = db
        self.player = {"uin": 42, "role_id": 42, "name": "Tester", "level": 20,
                       "face_id": 0, "coin": 50000, "contrib_val": 0}
        tables = {"unionSkillUnlock.txt": [["id", "skillID", "skillName", "unlockLevel"],
                                            ["1", "3", "Health", "10"]],
                  "unionSkillInfo.txt": [["id", "skillID", "propertyName", "targetLevel", "needPoint"],
                                         ["1", "3", "Health", "1", "2"]],
                  "unionSkillLevel.txt": [["id", "limit"], ["20", "4"]]}
        self.handlers = guild_local.register({"db_path": str(db), "config_value":
            lambda key: {"iCreateGuildNeedCoin": "0", "iCreateGuildNeedLevel": "1"}.get(key, "1"),
            "read_clientdb_table": lambda name: tables.get(name, [])})

    def test_create_is_visible_and_does_not_charge_coins(self):
        create = guild_local._s(1, "Test Guild")
        self.handlers["PVZ.Cmd.Cmd_GuildCreate_CS"](create, self.player)
        role = self.handlers["PVZ.Cmd.Cmd_RoleGuildInfo_CS"](b"", self.player)
        details = guild_local._submessage(role, 1)
        self.assertEqual(guild_local._int(details, 1), 1)
        self.assertEqual(guild_local._string(details, 2), "Test Guild")
        listing = self.handlers["PVZ.Cmd.Cmd_GuildList_CS"](b"", self.player)
        self.assertEqual(guild_local._int(listing, 1), 1)
        self.assertEqual(guild_local._string(guild_local._submessage(listing, 2), 2), "Test Guild")
        observed = self.handlers["PVZ.Cmd.Cmd_Guild_Observe_CS"](guild_local._v(1, 1), self.player)
        self.assertEqual(guild_local._string(guild_local._submessage(observed, 1), 2), "Test Guild")
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT coin FROM players WHERE uin=42").fetchone()[0], 50000)

    def test_donation_uses_ten_thousand_coin_units(self):
        self.handlers["PVZ.Cmd.Cmd_GuildCreate_CS"](guild_local._s(1, "Test Guild"), self.player)
        messages = self.handlers["PVZ.Cmd.Cmd_GuildDonate_CS"](guild_local._v(1, 2), self.player)
        self.assertIn("PVZ.Cmd.Cmd_SyncContribVal_SC", [name for name, _ in messages])
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT coin FROM players WHERE uin=42").fetchone()[0], 30000)
            self.assertEqual(con.execute("SELECT day_contrib FROM local_guild_members WHERE uin=42").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT contrib_val FROM players WHERE uin=42").fetchone()[0], 2)

    def test_legacy_daily_count_does_not_block_another_donation(self):
        self.handlers["PVZ.Cmd.Cmd_GuildCreate_CS"](guild_local._s(1, "Test Guild"), self.player)
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("UPDATE local_guild_members SET day_contrib=100,contrib_day=? WHERE uin=42",
                        (datetime.now().strftime("%Y%m%d"),))
        reply = self.handlers["PVZ.Cmd.Cmd_GuildDonate_CS"](guild_local._v(1, 1), self.player)
        self.assertEqual(reply[0][0], "PVZ.Cmd.Cmd_GuildDonate_SC")
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT day_contrib FROM local_guild_members WHERE uin=42").fetchone()[0], 101)
            self.assertEqual(con.execute("SELECT cohesion FROM local_guilds WHERE guild_id=1").fetchone()[0], 1)

    def test_skill_upgrade_has_complete_reply_and_spends_contribution(self):
        self.handlers["PVZ.Cmd.Cmd_GuildCreate_CS"](guild_local._s(1, "Test Guild"), self.player)
        self.handlers["PVZ.Cmd.Cmd_GuildDonate_CS"](guild_local._v(1, 2), self.player)
        reply = self.handlers["PVZ.Cmd.Cmd_GuildSkill_Upgrade_CS"](guild_local._v(1, 3), self.player)[0][1]
        info = guild_local._submessage(reply, 1)
        self.assertEqual((guild_local._int(info, 1), guild_local._int(info, 2)), (3, 1))
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT contrib_val FROM players WHERE uin=42").fetchone()[0], 0)
        second = self.handlers["PVZ.Cmd.Cmd_GuildSkill_Upgrade_CS"](guild_local._v(1, 3), self.player)[0][1]
        self.assertEqual(guild_local._int(guild_local._submessage(second, 1), 2), 1)

    def test_guild_bid_saves_manifesto_and_conserves_guild_exp(self):
        """Cmd_GuildBid_CS is the only route that writes the guild's 联盟简介.

        UnionBiddingWindowMediator.onClick hands the textarea + exp to UnionServiceCmd.unionBidding
        and waits for Cmd_GuildBid_SC, so without a route the 确定 button did nothing.
        """
        self.handlers["PVZ.Cmd.Cmd_GuildCreate_CS"](guild_local._s(1, "Test Guild"), self.player)
        self.handlers["PVZ.Cmd.Cmd_GuildDonate_CS"](guild_local._v(1, 5), self.player)  # exp = 5
        reply = self.handlers["PVZ.Cmd.Cmd_GuildBid_CS"](
            guild_local._s(1, "欢迎加入") + guild_local._v(2, 3), self.player)
        self.assertEqual([name for name, _ in reply],
                         ["PVZ.Cmd.Cmd_GuildBid_SC", "PVZ.Cmd.Cmd_GuildInfoRefresh_SC"])
        sent = guild_local._submessage(reply[1][1], 1)                 # Dto_Guild_Info
        base = guild_local._submessage(sent, 1)                        # Dto_Guild_BaseInfo
        self.assertEqual(guild_local._string(base, 8), "欢迎加入")
        self.assertEqual(guild_local._int(sent, 5), 3)                 # Dto_Guild_Info.curBid
        observed = self.handlers["PVZ.Cmd.Cmd_Guild_Observe_CS"](guild_local._v(1, 1), self.player)
        self.assertEqual(guild_local._string(guild_local._submessage(observed, 1), 8), "欢迎加入")
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT exp, bid_exp FROM local_guilds").fetchone(), (2, 3))
        # A second bid replaces the first instead of stacking: 2 + refund 3 = 5 available.
        again = self.handlers["PVZ.Cmd.Cmd_GuildBid_CS"](
            guild_local._s(1, "第二次宣言") + guild_local._v(2, 4), self.player)
        self.assertEqual(again[0][0], "PVZ.Cmd.Cmd_GuildBid_SC")
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT exp, bid_exp FROM local_guilds").fetchone(), (1, 4))
        # Rejected bids leave everything alone: empty manifesto, then more exp than the guild has.
        self.assertEqual(self.handlers["PVZ.Cmd.Cmd_GuildBid_CS"](
            guild_local._v(2, 4), self.player)[0][0], "PVZ.Cmd.Cmd_LogicError_SC")
        self.assertEqual(self.handlers["PVZ.Cmd.Cmd_GuildBid_CS"](
            guild_local._s(1, "太多") + guild_local._v(2, 999), self.player)[0][0],
            "PVZ.Cmd.Cmd_LogicError_SC")
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT exp, bid_exp FROM local_guilds").fetchone(), (1, 4))

    def test_change_notice_replies_and_refreshes_the_panel(self):
        self.handlers["PVZ.Cmd.Cmd_GuildCreate_CS"](guild_local._s(1, "Test Guild"), self.player)
        reply = self.handlers["PVZ.Cmd.Cmd_GuildChangeNotice_CS"](guild_local._s(1, "今晚八点开战"),
                                                                  self.player)
        self.assertEqual([name for name, _ in reply],
                         ["PVZ.Cmd.Cmd_GuildChangeNotice_SC", "PVZ.Cmd.Cmd_GuildInfoRefresh_SC"])
        self.assertEqual(guild_local._string(guild_local._submessage(reply[1][1], 1), 7),
                         "今晚八点开战")

    def test_donation_rejects_unaffordable_and_over_limit_requests(self):
        self.handlers["PVZ.Cmd.Cmd_GuildCreate_CS"](guild_local._s(1, "Test Guild"), self.player)
        donate = self.handlers["PVZ.Cmd.Cmd_GuildDonate_CS"]
        donate(guild_local._v(1, 6), self.player)  # needs 60,000 coins
        over_limit = donate(guild_local._v(1, 10001), self.player)
        self.assertEqual(over_limit[0][0], "PVZ.Cmd.Cmd_LogicError_SC")
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT coin FROM players WHERE uin=42").fetchone()[0], 50000)
            self.assertEqual(con.execute("SELECT day_contrib FROM local_guild_members WHERE uin=42").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
