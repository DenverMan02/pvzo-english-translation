"""Arena bots must populate the guild system, not just the arena.

Reported as "把竞技场的人机放到联盟中，一些加入我的联盟，一些自己创建或加入别的人机联盟":
the bot guilds are led by bots, and a guild the player founds gets a handful of the same bots
that fight in 天梯竞技场, so the member list / 捐献排行 / 联盟副本 are not empty rooms.
"""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import bots_local
import guild_local


class BotGuildMembershipTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "players.db"
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("CREATE TABLE players(uin INTEGER PRIMARY KEY, role_id INTEGER, name TEXT,"
                        "level INTEGER, face_id INTEGER, coin INTEGER,"
                        "contrib_val INTEGER NOT NULL DEFAULT 0)")
            con.execute("CREATE TABLE items(uin INTEGER, item_id INTEGER, count INTEGER,"
                        "PRIMARY KEY(uin,item_id))")
            con.execute("INSERT INTO players VALUES(42,42,'Tester',30,0,500000,0)")
        self.player = {"uin": 42, "role_id": 42, "name": "Tester", "level": 30,
                       "face_id": 0, "coin": 500000, "contrib_val": 0}
        bots_local.initialize(db_path=str(self.db), guild_db_path=str(self.db))
        bots_local._seed_guilds()
        self.handlers = guild_local.register({
            "db_path": str(self.db),
            "assign_bots": bots_local.staff_player_guilds,
            "config_value": lambda key: {"iCreateGuildNeedCoin": "0",
                                         "iCreateGuildNeedLevel": "1"}.get(key, "1"),
            "read_clientdb_table": lambda _name: []})

    def members(self, guild_id=None):
        with closing(sqlite3.connect(self.db)) as con:
            con.row_factory = sqlite3.Row
            sql = "SELECT guild_id, uin FROM local_guild_members"
            rows = con.execute(sql + (" WHERE guild_id=?" if guild_id else ""),
                               (guild_id,) if guild_id else ()).fetchall()
            return [(int(r["guild_id"]), int(r["uin"])) for r in rows]

    def test_bot_guilds_are_staffed_and_led_by_bots(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.row_factory = sqlite3.Row
            guilds = con.execute("SELECT guild_id, name FROM local_guilds").fetchall()
            self.assertGreaterEqual(len(guilds), 8)
            for guild in guilds:
                rows = con.execute("SELECT uin, position FROM local_guild_members "
                                   "WHERE guild_id=?", (guild["guild_id"],)).fetchall()
                self.assertTrue(rows, guild["name"])
                for row in rows:
                    self.assertTrue(bots_local.is_bot(row["uin"]), row["uin"])
                self.assertTrue(any(int(r["position"]) == 1 for r in rows),
                                "every bot guild needs a leader: %s" % guild["name"])
        # No bot is a member of two guilds.
        uins = [uin for _gid, uin in self.members()]
        self.assertEqual(len(uins), len(set(uins)))

    def test_created_guild_gets_arena_bots(self):
        self.handlers["PVZ.Cmd.Cmd_GuildCreate_CS"](guild_local._s(1, "My Guild"), self.player)
        with closing(sqlite3.connect(self.db)) as con, con:
            con.row_factory = sqlite3.Row
            gid = con.execute("SELECT guild_id FROM local_guilds WHERE name='My Guild'").fetchone()[0]
        joined = [uin for guild, uin in self.members(gid)]
        bots_joined = sorted(u for u in joined if u != 42)
        self.assertEqual(len(bots_joined), bots_local._BOTS_PER_PLAYER_GUILD)
        self.assertIn(42, joined)
        self.assertTrue(all(bots_local.is_bot(u) for u in bots_joined))
        # The player stays the only leader of their own guild.
        with closing(sqlite3.connect(self.db)) as con:
            leaders = con.execute("SELECT COUNT(*) FROM local_guild_members "
                                  "WHERE guild_id=? AND position=1", (gid,)).fetchone()[0]
        self.assertEqual(leaders, 1)
        # Re-running the seeder must not duplicate or move anybody.
        bots_local.staff_player_guilds()
        bots_local._seed_guilds()
        self.assertEqual(sorted(u for _gid, u in self.members(gid) if u != 42), bots_joined)
        uins = [uin for _gid, uin in self.members()]
        self.assertEqual(len(uins), len(set(uins)))
        # And the roster the client reads still shows a populated member list.
        member_reply = self.handlers["PVZ.Cmd.Cmd_GuildMember_CS"](guild_local._v(1, gid), self.player)
        entries = [value for wire, value in guild_local._fields(member_reply).get(1, []) if wire == 2]
        self.assertEqual(len(entries), len(joined))


if __name__ == "__main__":
    unittest.main()
