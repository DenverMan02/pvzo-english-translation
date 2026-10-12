"""Smoke test for the four features that had no server side at all.

Covers: 邮件 (Mail), 无尽副本 (NoEndFuben), 支线任务 (AuxQuest) and 七日登录
(SevenDayLogin).  The handlers must round-trip through the real protocol
encoders because the Flash client parses the bytes strictly.
"""

import os
import shutil
import sqlite3
import struct
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import missing_features_local as mfl


def _fields(data):
    """Minimal protobuf key/value scanner (varint + length-delimited)."""
    out, i = {}, 0
    while i < len(data):
        key, i = _uvarint(data, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _uvarint(data, i); out.setdefault(num, []).append(("v", v))
        elif wire == 2:
            ln, i = _uvarint(data, i); val = data[i:i + ln]; i += ln
            out.setdefault(num, []).append(("m", val))
        elif wire == 5:
            i += 4
        elif wire == 1:
            i += 8
        else:
            break
    return out


def _uvarint(data, i):
    shift = 0; result = 0
    while i < len(data):
        b = data[i]; result |= (b & 0x7F) << shift; i += 1
        if not (b & 0x80):
            return result, i
        shift += 7
    return result, i


_CLIENTDB = {
    "endlessReward.txt": [["id", "rankStart", "rankEnd", "rewardStr"],
                          ["1", "1", "1", "1:2000000"],
                          ["2", "2", "2", "1:1500000"]],
    "sevenDay.txt": [["id", "rewardItemStr"],
                     ["1", "25702:1;3200:3"],
                     ["2", "2016:2;55700:20"]],
}

# A minimal Dto_FightReport whose result(1)=0 means "attacker won".
_WIN_REPORT = mfl._v(1, 0) + mfl._v(5, 26)


class MissingFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db = Path(self.temp.name) / "players.db"
        with closing(sqlite3.connect(db)) as con, con:
            con.execute("CREATE TABLE players (uin INTEGER PRIMARY KEY, role_id INTEGER, "
                        "name TEXT, level INTEGER)")
            con.execute("INSERT INTO players VALUES (42,42,'Tester',20)")
        self.granted = []
        self.db = db
        self.player = {"uin": 42, "role_id": 42, "name": "Tester", "level": 20}
        self.handlers = mfl.register({
            "db_path": str(db),
            "log": lambda *args: None,
            "read_clientdb_table": lambda name: _CLIENTDB.get(name, []),
            "grant_reward": self._grant,
            "build_formation_info": lambda player, team, placements: mfl._v(2, team),
            "default_formation_placements": lambda player: {1: 11},
            "ensure_player_cards": lambda player: None,
            "set_formation": lambda *args: None,
            "run_fight": lambda data, player: [("PVZ.Cmd.Cmd_Battle_Fight_SC",
                                                mfl._m(1, _WIN_REPORT))],
        })
        # Every one of the 24 requests must be routed.
        self.assertEqual(24, len(self.handlers))

    def _grant(self, player, items):
        self.granted.extend(items)
        return [("PVZ.Cmd.Cmd_Item_Update_SC", b"")]

    def call(self, name, body=b""):
        """Invoke one of the new routes; ``name`` is the part after ``Cmd_``."""
        return self.handlers["PVZ.Cmd.Cmd_" + name](body, self.player)

    # ---- 邮件 -----------------------------------------------------------
    def test_mail_lifecycle(self):
        mfl._system_mail(42, "奖励", "这是一封带附件的邮件", "3200:3")
        outline = self.call("Mail_GetOutline_CS", mfl._v(2, 0) + mfl._v(3, 19))
        self.assertEqual(1, mfl._wire0(outline, 1))
        self.assertEqual(mfl._MAIL_MAIL_MAX, mfl._wire0(outline, 3))
        self.assertEqual(1, mfl._wire0(outline, 4))       # hasAttachmentNum
        mail_id = mfl._wire0(mfl._wire2(outline, 2), 1)

        content = self.call("Mail_GetContent_CS", mfl._i64(1, mail_id))
        self.assertEqual(mail_id, mfl._wire0(content, 1))
        self.assertEqual("这是一封带附件的邮件", mfl._text(content, 2))

        taken = self.call("Mail_ObtainAttachment_CS", mfl._i64(1, mail_id))
        self.assertIn(("PVZ.Cmd.Cmd_Mail_ObtainAttachment_SC", mfl._i64(1, mail_id)),
                      [(taken[0][0], taken[0][1])])
        self.assertEqual([(3200, 3)], self.granted)
        # second claim must not pay twice
        self.call("Mail_ObtainAttachment_CS", mfl._i64(1, mail_id))
        self.assertEqual([(3200, 3)], self.granted)

        self.call("Mail_Delete_CS", mfl._i64(1, mail_id))
        self.assertEqual(0, mfl._wire0(self.call("Mail_GetOutline_CS"), 1))

    def test_mail_others_empty_when_nothing_to_do(self):
        self.assertEqual(b"", self.call("Mail_Send_CS"))
        self.assertEqual(b"", self.call("Mail_GuildSend_CS"))
        self.assertEqual(b"", self.call("Mail_AllDelete_CS"))

    # ---- 无尽副本 -------------------------------------------------------
    def test_noend_fight_advances_and_pays_out(self):
        info = self.call("NoEndFuben_Info_CS")
        self.assertEqual(1, mfl._wire0(info, 5))               # stageId
        self.assertEqual(1, mfl._wire0(info, 6))               # historyMaxStageId

        self.call("NoEndFuben_Fight_CS", mfl._v(1, 1))
        info = self.call("NoEndFuben_Info_CS")
        self.assertEqual(2, mfl._wire0(info, 5))               # unlocked floor 2
        self.assertEqual(1, mfl._wire0(info, 14))              # bestStageId

        messages = self.call("NoEndFuben_Sweep_CS")
        self.assertEqual("PVZ.Cmd.Cmd_NoEndFuben_Sweep_SC", messages[0][0])
        self.assertEqual([(1, 2000000)], self.granted)

        messages = self.call("NoEndFuben_TakeReward_CS")
        self.assertEqual("PVZ.Cmd.Cmd_NoEndFuben_TakeReward_SC", messages[0][0])
        self.assertTrue(mfl._wire2(messages[0][1], 1))         # reward Dto_IdNum present
        # claimed once -> second attempt pays nothing
        self.assertEqual(b"", mfl._wire2(self.call("NoEndFuben_TakeReward_CS")[0][1], 1))

    def test_noend_info_echoes_card_hp_ratio_so_the_prepare_popup_closes(self):
        """回归 EndlessFuben: Info 必须把已选出战植物用 field 11 回下去。

        EndlessFubenModel.isNeedSetPreCard() 在这份列表为空时恒为真，proxy 会
        在每次 Info 回包后重新 openPrepareView —— 表现就是「确认出战」点了没反应。
        """
        info = self.call("NoEndFuben_Info_CS")
        self.assertEqual([], mfl._repeated_messages(info, 11))

        cards = b"".join(mfl._i64(1, 1010 + i) for i in range(8))
        self.call("NoEndFuben_SetPreCard_CS", cards)

        info = self.call("NoEndFuben_Info_CS")
        ratios = mfl._repeated_messages(info, 11)
        self.assertEqual(8, len(ratios))
        for index, dto in enumerate(ratios):
            self.assertEqual(1010 + index, mfl._wire0(dto, 1))
            self.assertEqual(1.0, mfl._fixed64(dto, 2))

        # 重置一轮后需要重新选卡
        self.call("NoEndFuben_Reset_CS")
        self.assertEqual([], mfl._repeated_messages(self.call("NoEndFuben_Info_CS"), 11))

    def test_noend_confirm_fight_returns_saved_plants(self):
        self.call("NoEndFuben_SetPreCard_CS", mfl._i64(1, 2024) + mfl._i64(1, 2025))
        body = self.call("NoEndFuben_ConfirmFight_CS", mfl._v(1, 1))
        self.assertEqual(1, mfl._wire0(body, 1))
        ratios = mfl._repeated_messages(body, 2)
        self.assertEqual([2024, 2025], [mfl._wire0(d, 1) for d in ratios])

    def test_noend_rank_and_formation(self):
        role = self.call("NoEndFuben_Role_CS")
        self.assertTrue(mfl._wire2(role, 3))                   # self entry present
        messages = self.call("NoEndFuben_SetFormation_CS",
                             mfl._m(1, mfl._m(1, mfl._v(1, 1) + mfl._v(2, 11))))
        self.assertEqual("PVZ.Cmd.Cmd_NoEndFuben_SyncFormation_SC", messages[0][0])
        self.assertEqual(b"\x08\x65\x08\x66",
                         self.call("NoEndFuben_SetPreCard_CS",
                                   mfl._i64(1, 101) + mfl._i64(1, 102)))
        self.assertEqual(1, mfl._wire0(self.call("NoEndFuben_Reset_CS"), 1))

    # ---- 无尽炼狱战斗报告路由（#1009 修复） -------------------------------
    def test_noend_fight_report_routes_to_noend_target(self):
        """服务端必须把无尽炼狱战斗报告的 fightType 写成 26（BattleTargetType_NoEndFuben）。

        客户端 BattleServiceCommand.getResourceList 只识别 Fuben(1)/Arena 分支；若 fightType=1，
        而 NoEndFuben 走 setData(report) 不传 battleChapterId（=0），getFubenChapterTemplateById(0)
        返回 null -> 访问 battleMapId 触发 #1009。fightType=26 让客户端走默认资源分支，正常加载。

        本测试用全新临时库跑真实战斗分支。注意：项目目录名带连字符（PvZ-Server-PoC），
        player_store 在测试与 pvz_poc_server 内可能被导入成两个不同的模块对象，必须显式把
        **服务端实际读取的那一份** pvz_poc_server.store.DB_PATH 指向临时库并初始化，否则战斗
        管线会读到别的用例留下、已被清理的临时库路径 -> sqlite3.OperationalError。"""
        import player_store
        import pvz_poc_server
        import sqlite3 as _sq
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # LIFO: these close calls run BEFORE tmp.cleanup releases the directory —
        # the cached per-thread connection keeps players.db open on Windows.
        import player_store as _ps
        import pvz_poc_server as _srvmod
        self.addCleanup(_ps.close_thread_db)
        self.addCleanup(_srvmod.store.close_thread_db)
        db = Path(tmp.name) / "players.db"
        # 关键：服务端战斗管线读的是 pvz_poc_server.store，而它可能与本测试 import 的
        # player_store 不是同一个模块对象，必须分别指向同一份临时库。
        player_store.DB_PATH = str(db)
        pvz_poc_server.store.DB_PATH = str(db)
        # 两份模块对象都初始化到同一个文件（init_db 用 IF NOT EXISTS，幂等安全）。
        player_store.init_db()
        pvz_poc_server.store.init_db()
        pvz_poc_server.load_battle_cards()
        # 给玩家两张真实卡片（卡片表由 init_db 创建），让左侧真实有植物进入战斗。
        player = pvz_poc_server.store.get_or_create_player(777001)[0]
        uin = player["uin"]
        cid = next(iter(pvz_poc_server._BATTLE_CARDS.keys()))
        con = _sq.connect(str(db))
        con.execute("INSERT INTO cards(uin,uid,card_id,level,exp) VALUES (?,201,?,1,0),(?,202,?,1,0)",
                    (uin, cid, uin, cid))
        con.commit(); con.close()   # 先关掉自己的连接，避免清理时锁文件
        handlers = mfl.register({
            "db_path": str(db),
            "log": lambda *a: None,
            "read_clientdb_table": lambda name: [],
            "grant_reward": lambda p, items: [],
            "build_formation_info": lambda p, t, pl: mfl._v(2, t),
            "default_formation_placements": lambda p: {1: 11},
            "ensure_player_cards": lambda p: None,
            "set_formation": lambda *a: None,
            "run_fight": pvz_poc_server.handle_battle_fight,
        })
        # 通过真实 handler 设置出战植物：会正确创建 local_noend_fuben 表并写入 pre_card。
        handlers["PVZ.Cmd.Cmd_NoEndFuben_SetPreCard_CS"](
            mfl._i64(1, 201) + mfl._i64(1, 202), player)
        sc = handlers["PVZ.Cmd.Cmd_NoEndFuben_Fight_CS"](mfl._v(1, 5), player)
        # Cmd_NoEndFuben_Fight_SC{stageId(1)...report(6)=Dto_FightReport}
        report = mfl._wire2(sc, 6)
        self.assertTrue(report, "战斗报告不应为空")
        rf = _fields(report)
        self.assertIn(5, rf, "报告缺少 fightType(5)")
        self.assertEqual(26, rf[5][0][1], "fightType 必须是 26（NoEndFuben），否则客户端走 Fuben 模板分支 #1009")
        self.assertIn(2, rf, "报告缺少 leftSide(2) -> getResourceList 默认分支空指针")
        self.assertIn(3, rf, "报告缺少 rightSide(3) -> getResourceList 默认分支空指针")
        ls = _fields(rf[2][0][1])
        rs = _fields(rf[3][0][1])
        left_cards = ls.get(2, [])
        right_cards = rs.get(2, [])
        self.assertEqual(2, len(left_cards), "玩家选中的两张植物应进入战斗左侧")

        def pos_index(card):
            return _fields(card[1]).get(1, [("v", 0)])[0][1]

        left_pos = [pos_index(c) for c in left_cards]
        right_pos = [pos_index(c) for c in right_cards]
        # posIndex 必须是奇数（BattleConst.isLeft = pos%2==1），且落在客户端真正建了槽位的
        # _FORMATION_POS 上。用 {i+1: uid} 会把植物放到偶数位 -> 画到敌方半场，并且低位数与
        # _ENEMY_POS 的僵尸位重叠 -> createUnitList 对同一 UnitBase 跑两次 UpdateUnitPlan
        # -> UnitBase.addControl 抛"相同类型控制器只能添加一次!"。
        self.assertEqual([3, 7], left_pos, "左侧植物必须落在 _FORMATION_POS 的前两个槽位")
        self.assertEqual(len(set(left_pos)), len(left_pos), "leftSide 的 posIndex 不能重复")
        self.assertEqual(len(set(right_pos)), len(right_pos), "rightSide 的 posIndex 不能重复")
        self.assertFalse(set(left_pos) & set(right_pos),
                         "左右两侧的 posIndex 不能重叠（重叠会让客户端对同一单位重复 addControl）")
        for pos in right_pos:
            self.assertIn(pos, pvz_poc_server._ENEMY_POS,
                          "右侧僵尸必须落在 _ENEMY_POS（偶数槽位）")

    # ---- 支线任务 -------------------------------------------------------
    def test_aux_quest_flow(self):
        def state(body):
            quest = mfl._wire2(body, 1)
            return mfl._wire0(quest, 2), mfl._wire0(quest, 3)

        self.assertEqual((mfl.AUX_NOT_EXE, 0),
                         state(self.call("AuxQuest_GetAuxQuest_CS", mfl._v(1, 101))))
        self.assertEqual((mfl.AUX_NOT_EXE, 2),
                         state(self.call("AuxQuest_Operate_CS",
                                         mfl._v(1, 101) + mfl._v(2, 2))))
        self.assertEqual((mfl.AUX_FINISH, 3),
                         state(self.call("AuxQuest_Operate_CS",
                                         mfl._v(1, 101) + mfl._v(2, 5))))
        messages = self.call("AuxQuest_TakeReward_CS", mfl._v(1, 101))
        self.assertEqual("PVZ.Cmd.Cmd_AuxQuest_TakeReward_SC", messages[0][0])
        self.assertEqual((mfl.AUX_OVER, 3),
                         state(self.call("AuxQuest_GetAuxQuest_CS", mfl._v(1, 101))))
        # claiming twice is rejected
        self.assertEqual("PVZ.Cmd.Cmd_LogicError_SC",
                         self.call("AuxQuest_TakeReward_CS", mfl._v(1, 101))[0][0])

    # ---- 七日登录 -------------------------------------------------------
    def test_seven_day_login(self):
        info = self.call("SevenDayLogin_GetInfo_CS")
        self.assertEqual(1, mfl._wire0(info, 1))               # loginDay
        self.assertTrue(info.endswith(mfl._b(4, True)))        # isOpen

        messages = self.call("SevenDayLogin_TakeReward_CS")
        self.assertEqual("PVZ.Cmd.Cmd_SevenDayLogin_TakeReward_SC", messages[0][0])
        self.assertEqual([(25702, 1), (3200, 3)], self.granted)
        self.assertEqual("PVZ.Cmd.Cmd_LogicError_SC",
                         self.call("SevenDayLogin_TakeReward_CS")[0][0])


if __name__ == "__main__":
    unittest.main()
