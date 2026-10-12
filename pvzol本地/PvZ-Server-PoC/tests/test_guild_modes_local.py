"""Guild modes must supply the fields that the Flash panels dereference."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import guild_local as guild
import guild_modes_local as modes


class GuildModesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Path(temp.name) / "players.db"
        with closing(sqlite3.connect(db)) as con, con:
            con.execute("CREATE TABLE players(uin INTEGER PRIMARY KEY,role_id INTEGER,name TEXT,"
                        "level INTEGER,face_id INTEGER,coin INTEGER,contrib_val INTEGER)")
            con.execute("CREATE TABLE items(uin INTEGER,item_id INTEGER,count INTEGER,PRIMARY KEY(uin,item_id))")
            con.execute("INSERT INTO players VALUES(42,42,'Tester',30,0,500000,0)")
        self.player = {"uin": 42, "role_id": 42, "name": "Tester", "level": 30,
                       "face_id": 0, "coin": 500000, "contrib_val": 0}
        core = guild.register({"db_path": str(db), "config_value": lambda _key: 0,
                               "read_clientdb_table": lambda _name: []})
        core["PVZ.Cmd.Cmd_GuildCreate_CS"](guild._s(1, "Test Guild"), self.player)
        self.db = db
        self.fights = []
        tables = {
            "FubenChapterForUnion.txt": [
                ["id", "upChapterId", "openCohesion", "resetCohesion", "fightCount"],
                ["5", "0", "10000", "20000", "2"], ["6", "5", "12000", "24000", "2"]],
            "fuben.txt": [["chapterId", "stageId", "mode"],
                          ["5", "1", "3"], ["5", "2", "3"], ["6", "1", "3"]]}

        self.win = True

        def fight(data, _player):
            target = guild._submessage(data, 1)
            self.fights.append((guild._int(target, 1), guild._int(target, 2),
                                guild._int(target, 3), guild._int(target, 4)))
            report = (guild._v(1, 0 if self.win else 1) + guild._m(2, guild._v(1, 0))
                      + guild._v(5, 1))
            return [("PVZ.Cmd.Cmd_Battle_Fight_SC", guild._m(1, report))]

        self.handlers = modes.register({
            "read_clientdb_table": lambda name: tables.get(name, []),
            "ensure_player_cards": lambda _player: None,
            "default_formation_placements": lambda _player: {1: 100},
            "build_formation_info": lambda _p, _team, _places: guild._v(5, 1),
            "run_fight": fight,
            "grant_reward": lambda _p, _items: [],
            "set_formation": lambda *_args: None})

    def call(self, name, data=b""):
        return self.handlers["PVZ.Cmd." + name](data, self.player)

    def chapters(self):
        body = self.call("Cmd_GuildFuben_GetChapterInfo_CS")
        return [value for wire, value in guild._fields(body).get(1, []) if wire == 2]

    def test_daily_challenge_times_come_from_fight_count(self):
        """Dto_GuildFuben_ChapterInfo.times is 今日剩余次数 = fightCount - today's attempts.

        The panel renders it as "今日剩余次数: {times}/{fightCount}" and refuses the challenge
        with UNION_FUBEN_TIMES_OVER once it hits zero, so it must never be a constant 0.
        """
        self.assertEqual(guild._int(self.chapters()[0], 2), 2)
        self.win = False                      # a loss spends an attempt but does not advance
        spent = self.call("Cmd_GuildFuben_StartFight_CS", guild._v(1, 5) + guild._v(2, 1))
        self.assertEqual(guild._int(self.chapters()[0], 2), 1)
        self.assertEqual(spent[-1][0], "PVZ.Cmd.Cmd_GuildFuben_SyncChapterInfo_SC")
        self.assertEqual(guild._int(guild._submessage(spent[-1][1], 1), 2), 1)
        self.call("Cmd_GuildFuben_StartFight_CS", guild._v(1, 5) + guild._v(2, 1))
        self.assertEqual(guild._int(self.chapters()[0], 2), 0)
        denied = self.call("Cmd_GuildFuben_StartFight_CS", guild._v(1, 5) + guild._v(2, 1))
        self.assertEqual(denied[0][0], "PVZ.Cmd.Cmd_LogicError_SC")
        self.assertEqual(guild._int(denied[0][1], 1), 8515)
        # The quota is daily: yesterday's attempts are not counted against today.
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("UPDATE local_guild_dungeon_chapter_fights SET day='19700101'")
        self.win = True
        allowed = self.call("Cmd_GuildFuben_StartFight_CS", guild._v(1, 5) + guild._v(2, 1))
        self.assertEqual(allowed[0][0], "PVZ.Cmd.Cmd_GuildFuben_StartFight_SC")

    def test_dungeon_chapter_and_stage_progress_are_shared(self):
        body = self.call("Cmd_GuildFuben_GetChapterInfo_CS")
        chapters = [value for wire, value in guild._fields(body).get(1, []) if wire == 2]
        self.assertEqual(len(chapters), 2)
        self.assertEqual(guild._int(chapters[0], 4), 1)
        self.assertEqual(guild._int(chapters[1], 4), 0)
        stage = guild._submessage(self.call("Cmd_GuildFuben_GetStageInfo_CS", guild._v(1, 5)), 1)
        self.assertEqual(guild._int(stage, 2), 1)
        result = self.call("Cmd_GuildFuben_StartFight_CS", guild._v(1, 5) + guild._v(2, 1))
        self.assertEqual(self.fights, [(14, 5, 3, 1)])
        self.assertEqual(result[0][0], "PVZ.Cmd.Cmd_GuildFuben_StartFight_SC")
        self.assertEqual(guild._int(guild._submessage(result[0][1], 1), 5), 14)
        stage = guild._submessage(self.call("Cmd_GuildFuben_GetStageInfo_CS", guild._v(1, 5)), 1)
        self.assertEqual(guild._int(stage, 2), 2)
        denied = self.call("Cmd_GuildFuben_StartFight_CS", guild._v(1, 5) + guild._v(2, 1))
        self.assertEqual(denied[0][0], "PVZ.Cmd.Cmd_LogicError_SC")
        reward = self.call("Cmd_GuildFuben_NewDayReward_Get_CS")
        self.assertEqual(reward[0][0], "PVZ.Cmd.Cmd_GuildFuben_NewDayReward_Get_SC")
        self.assertEqual(guild._int(guild._submessage(reward[0][1], 1), 2), 1)
        self.assertEqual(guild._int(self.call("Cmd_GuildFuben_NewDayReward_GetInfo_CS"), 3), 1)
        self.assertEqual(self.call("Cmd_GuildFuben_NewDayReward_Get_CS")[0][0],
                         "PVZ.Cmd.Cmd_LogicError_SC")

    def test_competition_list_and_registration_are_persistent(self):
        stage = self.call("Cmd_GuildFight_GetCurrentStage_CS")
        self.assertEqual(guild._int(stage, 1), 1)
        self.assertGreater(guild._int(guild._submessage(stage, 3), 3), 0)
        listing = self.call("Cmd_GuildFight_GetFinalist_CS")
        entry = guild._submessage(listing, 1)
        self.assertEqual(guild._string(entry, 2), "Test Guild")
        self.assertEqual(guild._int(entry, 8), 1)
        result = self.call("Cmd_GuildFight_ApplyFinalist_CS")
        self.assertEqual(result[0][0], "PVZ.Cmd.Cmd_GuildFight_ApplyFinalist_SC")
        entry = guild._submessage(self.call("Cmd_GuildFight_GetFinalist_CS"), 1)
        self.assertEqual(guild._int(entry, 8), 2)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM local_guild_competition").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
