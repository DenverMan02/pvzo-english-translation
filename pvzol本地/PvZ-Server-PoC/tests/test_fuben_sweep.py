"""Adventure sweep eligibility, protocol flags, and reward routing."""

import os
import sys
import unittest
from contextlib import ExitStack, nullcontext
from unittest.mock import patch

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)
import pvz_poc_server as srv  # noqa: E402


def stage_row(body):
    rows = srv.parse_repeated_submessages(body, 1)
    assert len(rows) == 1
    return srv.parse_varint_fields(rows[0])


class AdventureSweepTest(unittest.TestCase):
    def setUp(self):
        # These eligibility tests use small player stubs. Settlement/database
        # effects have separate coverage with a real disposable account.
        effects = ExitStack()
        self.addCleanup(effects.close)
        effects.enter_context(patch.object(srv.store, "atomic_player",
                                           side_effect=lambda _player: nullcontext()))
        for name in ("grant_role_exp", "grant_battle_card_exp", "fuben_lab_material_reward",
                     "_quest_record_condition", "_quest_observe_card_progress"):
            effects.enter_context(patch.object(srv, name, return_value=[]))

    def _get_stage_info(self, player, star):
        with (patch.object(srv, "load_fuben_stages", return_value={(1, 1): [1, 2]}),
              patch.object(srv.store, "get_fuben_passed", return_value={1: star}),
              patch.object(srv, "_fuben_box_taken_flag", return_value=0),
              patch.object(srv, "note_battle_scene")):
            request = srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
            return stage_row(srv.handle_fuben_getstageinfo(request, player))

    def test_stage_dto_exposes_single_sweep_at_the_documented_star_threshold(self):
        non_vip = {"uin": 10, "vip_level": 0}
        self.assertEqual(self._get_stage_info(non_vip, 2).get(7), 0)
        cleared = self._get_stage_info(non_vip, 3)
        self.assertEqual(cleared.get(7), 1)
        self.assertEqual(cleared.get(8), 0)
        self.assertEqual(cleared.get(9), 0)

    def test_vip_one_star_and_privilege_15_ten_sweep_flags(self):
        vip = {"uin": 11, "vip_level": 1}
        with patch.object(srv, "vip_privileges", return_value={14: 0}):
            one_star = self._get_stage_info(vip, 1)
            self.assertEqual(one_star.get(7), 1)
            self.assertEqual(one_star.get(8), 0)
        with patch.object(srv, "vip_privileges", return_value={14: 0, 15: 0}):
            ten_sweep = self._get_stage_info(vip, 1)
            self.assertEqual(ten_sweep.get(7), 1)
            self.assertEqual(ten_sweep.get(8), 1)
            self.assertEqual(ten_sweep.get(9), 10)

    @staticmethod
    def _request(times, multi):
        return (srv.pb_varint_field(1, 1) + srv.pb_varint_field(2, 1)
                + srv.pb_varint_field(3, 1) + srv.pb_varint_field(4, times)
                + srv.pb_varint_field(5, int(multi)))

    def _run_sweep(self, player, times, multi, privileges):
        with (patch.object(srv, "vip_privileges", return_value=privileges),
              patch.object(srv.store, "get_fuben_passed", return_value={1: 3}),
              patch.object(srv, "fuben_energy_cost", return_value=5),
              patch.object(srv, "consume_energy", return_value=True) as consume,
              patch.object(srv, "fuben_stage_reward", return_value=[(9001, 2)]),
              patch.object(srv, "build_item_dto", return_value=b""),
              patch.object(srv, "grant_fuben_reward", return_value=[("test.reward", b"")]) as grant,
              patch.object(srv, "_daily_quest_touch"),
              patch.object(srv, "energy_sync_msg", return_value=("test.energy", b""))):
            response = srv.handle_fuben_sweep(self._request(times, multi), player)
            return response, consume, grant

    def test_single_sweep_grants_reward_only_after_full_clear_for_non_vip(self):
        player = {"uin": 12, "vip_level": 0}
        response, consume, grant = self._run_sweep(player, 1, False, {})
        self.assertEqual(response[0][0], "PVZ.Cmd.Cmd_Fuben_Sweep_SC")
        consume.assert_called_once_with(player, 5)
        granted = dict(grant.call_args.args[1])
        self.assertEqual(granted[9001], 2)
        self.assertEqual(granted[srv.hotspot_events.SHELL], 1)

    def test_ten_sweep_requires_the_multi_sweep_privilege_and_flag(self):
        player = {"uin": 13, "vip_level": 1}
        denied, consume, _grant = self._run_sweep(player, 10, True, {14: 0})
        self.assertEqual(denied, [])
        consume.assert_not_called()

        response, consume, grant = self._run_sweep(player, 10, True, {14: 0, 15: 0})
        self.assertEqual(response[0][0], "PVZ.Cmd.Cmd_Fuben_Sweep_SC")
        consume.assert_called_once_with(player, 50)
        granted = dict(grant.call_args.args[1])
        self.assertEqual(granted[9001], 20)
        self.assertEqual(granted[srv.hotspot_events.SHELL], 10)

    # --- 金壶VIP3 = 冒险连续扫荡 (privilege 15) -----------------------------------------------
    # VipInfo.txt's privilegeString lists 14 on every tier but never 15, yet the client's 布阵 window
    # sells the "扫荡N次" button as the VIP3 perk (BattleLineupWindowMediator.init tooltip =
    # FontNormal.VIP_SWEEP_ALL + VipInfoFactory.getVipInfoById(3).name) and leaves it disabled with the
    # raw default label '扫荡X次' while canMultiSweep is 0. vip_privileges() therefore synthesizes 15.

    VIP3_TIER = {"level": 3, "name": "金壶VIP3", "privileges": {14: 0}}

    def test_vip3_privileges_gain_continuous_sweep(self):
        with patch.object(srv, "vip_tier", return_value=self.VIP3_TIER):
            privs = srv.vip_privileges({"uin": 14, "vip_level": 3})
        self.assertEqual(privs.get(srv._FUBEN_MULTI_SWEEP_PRIVILEGE),
                         srv._FUBEN_MULTI_SWEEP_PRIV_PARAM)
        self.assertIn(srv._FUBEN_SWEEP_PRIVILEGE, privs)
        # ...and lower tiers keep the upsell-only behaviour.
        for level in (1, 2, 0):
            tier = {"level": level, "privileges": {14: 0}} if level else None
            with patch.object(srv, "vip_tier", return_value=tier):
                lower = srv.vip_privileges({"uin": 14, "vip_level": level})
            self.assertNotIn(srv._FUBEN_MULTI_SWEEP_PRIVILEGE, lower)

    def test_vip3_privilege_message_advertises_continuous_sweep(self):
        with patch.object(srv, "vip_tier", return_value=self.VIP3_TIER):
            body = srv.build_pvzprivilege_sc({"uin": 15, "vip_level": 3})
        sent = {}
        for part in srv.parse_repeated_submessages(body, 1):
            f = srv.parse_varint_fields(part)
            sent[f.get(1, 0)] = f.get(2, 0)
        self.assertEqual(sent.get(srv._FUBEN_MULTI_SWEEP_PRIVILEGE),
                         srv._FUBEN_MULTI_SWEEP_TIMES)

    def test_vip3_stage_dto_enables_the_ten_sweep_button(self):
        with patch.object(srv, "vip_tier", return_value=self.VIP3_TIER):
            one_star = self._get_stage_info({"uin": 16, "vip_level": 3}, 1)
        self.assertEqual((one_star.get(7), one_star.get(8), one_star.get(9)),
                         (1, 1, srv._FUBEN_MULTI_SWEEP_TIMES))

    def test_vip3_ten_sweep_is_accepted(self):
        player = {"uin": 17, "vip_level": 3}
        with (patch.object(srv, "vip_tier", return_value=self.VIP3_TIER),
              patch.object(srv.store, "get_fuben_passed", return_value={1: 1}),
              patch.object(srv, "fuben_energy_cost", return_value=5),
              patch.object(srv, "consume_energy", return_value=True) as consume,
              patch.object(srv, "fuben_stage_reward", return_value=[(9001, 2)]),
              patch.object(srv, "build_item_dto", return_value=b""),
              patch.object(srv, "grant_fuben_reward",
                           return_value=[("test.reward", b"")]) as grant,
              patch.object(srv, "_daily_quest_touch"),
              patch.object(srv, "energy_sync_msg", return_value=("test.energy", b""))):
            response = srv.handle_fuben_sweep(self._request(10, True), player)
        self.assertEqual(response[0][0], "PVZ.Cmd.Cmd_Fuben_Sweep_SC")
        consume.assert_called_once_with(player, 50)
        self.assertEqual(dict(grant.call_args.args[1])[9001], 20)

    def test_partial_sweep_counts_between_one_and_ten_are_honoured(self):
        player = {"uin": 18, "vip_level": 3}
        with (patch.object(srv, "vip_tier", return_value=self.VIP3_TIER),
              patch.object(srv.store, "get_fuben_passed", return_value={1: 1}),
              patch.object(srv, "fuben_energy_cost", return_value=5),
              patch.object(srv, "consume_energy", return_value=True) as consume,
              patch.object(srv, "fuben_stage_reward", return_value=[(9001, 2)]),
              patch.object(srv, "build_item_dto", return_value=b""),
              patch.object(srv, "grant_fuben_reward",
                           return_value=[("test.reward", b"")]) as grant,
              patch.object(srv, "_daily_quest_touch"),
              patch.object(srv, "energy_sync_msg", return_value=("test.energy", b""))):
            response = srv.handle_fuben_sweep(self._request(5, True), player)
        self.assertEqual(response[0][0], "PVZ.Cmd.Cmd_Fuben_Sweep_SC")
        consume.assert_called_once_with(player, 25)
        self.assertEqual(dict(grant.call_args.args[1])[9001], 10)
        # More than the supported maximum is still refused.
        with (patch.object(srv, "vip_tier", return_value=self.VIP3_TIER),
              patch.object(srv.store, "get_fuben_passed", return_value={1: 1}),
              patch.object(srv, "consume_energy", return_value=True) as consume2):
            self.assertEqual(srv.handle_fuben_sweep(self._request(11, True), player), [])
        consume2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
