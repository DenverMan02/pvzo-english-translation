"""VIP3 must be able to bulk-sweep an adventure stage 10 times.

Player report: "我明明是vip3却无法扫荡十次，现在怎么还成扫荡x次了" — the lineup window showed a
greyed-out button labelled '扫荡X次'.

'扫荡X次' is the COMPILED-IN PLACEHOLDER LABEL. ModuleBattleLineup.swf only relabels it to
'扫荡10次' inside updateSweep(), and only when battleModel.fight_multSweep is true — which
BattleLineupCommand.preFight_PVE copies from the stage DTO's canMultiSweep. So the grey
'扫荡X次' means the client received canMultiSweep=false.

Two independent things have to line up, and this file pins both:

1. THE STAGE DTO. Cmd_Fuben_GetStageInfo_CS -> Dto_Fuben_StageInfo must carry
   canSingleSweep(7)=1 / canMultiSweep(8)=1 / multiSweeptTimes(9)=10.
2. THE PRIVILEGE. The client also gates the button on the VIP3 entitlement, delivered two
   ways: Cmd_PVZPrivilege_SC must list privilege 15 (VipPrivilegeInfo: 冒险连续扫荡), and
   Cmd_VipInfo_SC must set bHasVip3Privilege(7)=1. VipInfo.txt's privilegeString never lists
   15 on ANY tier in this build, so vip_privileges() synthesises it for 金壶VIP3.

Regression guard for the `privileges.setdefault` in both builders: dropping privilege 15
silently degrades the button to the placeholder label with no error anywhere.
"""
import os
import sys
import tempfile
import unittest

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_vip_sweep_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700005001
SWEEP_PRIV = 14          # 冒险副本/傀儡战境扫荡
MULTI_PRIV = 15          # 冒险连续扫荡
MULTI_TIMES = 10


def vip_player(level=3):
    store.get_or_create_player(UIN)
    store.save_player(UIN, level=200, vip_level=level, vip_day="20261004")
    player, _ = store.get_or_create_player(UIN)
    return player


def stage_fields(player, stars):
    return srv.parse_varint_fields(srv._fuben_stage_info_dto(player, 7, 1, 3, stars))


def privilege_list(body):
    out = {}
    for dto in srv.parse_repeated_submessages(body, 1):
        f = srv.parse_varint_fields(dto)
        out[f.get(1, 0)] = f.get(2, 0)
    return out


def coin_after(uin):
    with store._db() as con:
        return con.execute("SELECT coin FROM players WHERE uin=?", (uin,)).fetchone()["coin"]


class VipBulkSweepTest(unittest.TestCase):
    def test_vip3_gets_the_bulk_sweep_privilege(self):
        privs = srv.vip_privileges(vip_player(3))
        self.assertIn(SWEEP_PRIV, privs, "VIP3 lost 冒险副本扫荡")
        self.assertIn(MULTI_PRIV, privs,
                      "VIP3 lost 冒险连续扫荡 (privilege 15) — this is what greys the button")
        self.assertEqual(privs[MULTI_PRIV], MULTI_TIMES)

    def test_privilege_15_reaches_the_clients_privilege_list(self):
        privs = privilege_list(srv.build_pvzprivilege_sc(vip_player(3)))
        self.assertIn(MULTI_PRIV, privs,
                      "Cmd_PVZPrivilege_SC is missing privilege 15, so hasPrivilege(15) is false")
        self.assertEqual(privs[MULTI_PRIV], MULTI_TIMES)

    def test_vipinfo_flags_the_vip3_entitlement(self):
        body = srv.build_vipinfo_sc(vip_player(3))
        inner = srv.parse_repeated_submessages(body, 1)
        self.assertEqual(len(inner), 1, "Cmd_VipInfo_SC must carry one Dto_VipInfo")
        self.assertEqual(srv.parse_varint_fields(inner[0]).get(7, 0), 1,
                         "bHasVip3Privilege(7) must be 1 for VIP3")

    def test_cleared_stage_grants_both_sweep_buttons(self):
        for stars in (1, 2, 3):
            f = stage_fields(vip_player(3), stars)
            self.assertEqual(f.get(7, 0), 1, "canSingleSweep with %d stars" % stars)
            self.assertEqual(f.get(8, 0), 1,
                             "canMultiSweep must be 1 with %d stars, else the button keeps the "
                             "'扫荡X次' placeholder" % stars)
            self.assertEqual(f.get(9, 0), MULTI_TIMES,
                             "multiSweeptTimes must be %d" % MULTI_TIMES)

    def test_uncleared_stage_grants_neither(self):
        f = stage_fields(vip_player(3), 0)
        self.assertEqual(f.get(7, 0), 0, "an uncleared stage must not be sweepable")
        self.assertEqual(f.get(8, 0), 0)
        self.assertEqual(f.get(9, 0), 0)

    def test_non_vip_needs_three_stars_and_has_no_bulk_sweep(self):
        player = vip_player(0)
        self.assertEqual(srv.vip_privileges(player), {})
        self.assertEqual(stage_fields(player, 2).get(7, 0), 0,
                         "without privilege 14 the bar is 3 stars")
        self.assertEqual(stage_fields(player, 3).get(7, 0), 1)
        self.assertEqual(stage_fields(player, 3).get(8, 0), 0,
                         "non-VIP must never get the 10x button")

    def test_bulk_sweep_request_is_accepted_for_vip3(self):
        """End to end: the Cmd_Fuben_Sweep_CS the button actually sends must not be rejected."""
        player = vip_player(3)
        store.pass_fuben_stage(UIN, 7, 1, 3, 3)
        request = (srv.pb_varint_field(1, 7) + srv.pb_varint_field(2, 1)
                   + srv.pb_varint_field(3, 3) + srv.pb_varint_field(4, MULTI_TIMES)
                   + srv.pb_varint_field(5, 1))
        player, _ = store.get_or_create_player(UIN)
        store.save_player(UIN, energy=9999, coin=1000)
        player["energy"] = 9999
        before = coin_after(UIN)
        msgs = srv.handle_fuben_sweep(request, player)
        self.assertTrue(msgs, "VIP3 10x sweep was rejected")
        # Coins are the one reward every adventure stage is guaranteed to pay, so they are
        # the reliable signal that the sweep actually resolved ten times.
        self.assertGreater(coin_after(UIN), before,
                           "the 10x sweep paid out nothing")

    def test_sweep_above_the_allowed_count_is_rejected(self):
        player = vip_player(3)
        store.pass_fuben_stage(UIN, 7, 1, 3, 3)
        request = (srv.pb_varint_field(1, 7) + srv.pb_varint_field(2, 1)
                   + srv.pb_varint_field(3, 3) + srv.pb_varint_field(4, MULTI_TIMES + 1)
                   + srv.pb_varint_field(5, 1))
        player, _ = store.get_or_create_player(UIN)
        player["energy"] = 9999
        self.assertEqual(srv.handle_fuben_sweep(request, player), [],
                         "more than %d sweeps must be refused" % MULTI_TIMES)


if __name__ == "__main__":
    unittest.main()
