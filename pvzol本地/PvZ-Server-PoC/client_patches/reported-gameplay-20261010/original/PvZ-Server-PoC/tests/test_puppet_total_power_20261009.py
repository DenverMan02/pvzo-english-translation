"""Disposable-save regression for original puppet -> deployed-plant bonuses."""
from pathlib import Path
import copy
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
LOCAL_PARENT = Path(__file__).resolve().parents[1]
SERVER = (LOCAL_PARENT if (LOCAL_PARENT / "pvz_poc_server.py").exists()
          else Path(r"D:\pvzol本地最新最新ol\pvzol本地\PvZ-Server-PoC"))
WORK = Path(__file__).resolve().parent
DEFAULT_SOURCE = (SERVER / "pvz_poc_server.py" if WORK.name == "tests" else
                  WORK / "puppet_total_power_staged" / "pvz_poc_server.py")
SOURCE = Path(os.environ.get("PVZ_PUPPET_TOTAL_SOURCE", str(DEFAULT_SOURCE)))
sys.path.insert(0, str(SERVER))
import player_store as store

DB = tempfile.TemporaryDirectory(prefix="pvz-puppet-total-")
store.set_db_path(str(Path(DB.name) / "test.db"))
store.init_db()
g = types.ModuleType("pvz_puppet_total_under_test")
g.__file__ = str(SERVER / "pvz_poc_server.py")
sys.modules[g.__name__] = g
exec(compile(SOURCE.read_text(encoding="utf-8"), str(SOURCE), "exec"), g.__dict__)
g._CLIENTDB_PATH = str(SERVER.parent / "webroot" / "clientDB.txt")

FIELDS = ("atk", "def_", "hp", "ability", "hit", "dodge", "critical", "tough")
RAW_FIELDS = ("atk", "def", "hp", "ab", "hit", "dodge", "critical", "tough")
VECTORS = (
    (1000001, dict(level=1, star=1, quality=100, spars=""),
     (145764, 0, 1311840, 0, 276, 456, 276, 456),
     (242, 0, 2186, 0, 46, 76, 46, 76)),
    (1000001, dict(level=1, star=1, quality=100,
                  spars="1:1:1;2:1:1;3:1:1;4:1:1;5:1:1;6:1:1"),
     (249864, 52050, 4955340, 52050, 2364, 456, 276, 3930),
     (416, 86, 8258, 86, 394, 76, 46, 655)),
    (1000001, dict(level=10, star=2, quality=100,
                  spars="1:4:2;2:4:2;3:4:2;4:4:2;5:4:2;6:4:2"),
     (483144, 68550, 7912662, 68550, 3426, 1110, 654, 5634),
     (805, 114, 13187, 114, 571, 185, 109, 939)),
    (2000001, dict(level=10, star=3, quality=300,
                  spars="1:4:2;2:4:2;3:4:2;4:4:2;5:4:2;6:4:2"),
     (1100226, 68550, 13466952, 68550, 4596, 3036, 1824, 7560),
     (1833, 114, 22444, 114, 766, 506, 304, 1260)),
)
FORMATION = "PVZ.Cmd.Cmd_Battle_GetFormation_SC"


class PuppetTotalPowerTests(unittest.TestCase):
    seq = 0

    def setUp(self):
        type(self).seq += 1
        self.player, _ = store.get_or_create_player(91003000 + type(self).seq)
        self.uin = self.player["uin"]
        store.save_player(self.uin, level=100, coin=1000000000,
                          puppet_skill_val=200, puppet_skill_val_ts=int(time.time()), puppet_granted=1)
        self.player.update(level=100, coin=1000000000, puppet_skill_val=200,
                           puppet_skill_val_ts=int(time.time()), puppet_granted=1)
        g.load_battle_cards()
        self.uid = 92000000 + type(self).seq * 10
        self.placements = {}
        for n, pos in enumerate(g._FORMATION_POS[:6]):
            store.add_card(self.uin, self.uid + n, 101, level=10)
            self.placements[pos] = self.uid + n
        store.set_formation(self.uin, 1, self.placements)

    def puppet(self, base_id=1000001, **changes):
        info = g.load_dummy_config()["zombies"][base_id]
        row = dict(exp=0, level=1, star=1, quality=100, spars="",
                   active_skill=info["active"], active_level=1,
                   passive_skill=info["passive"], passive_level=1)
        row.update(changes)
        store.save_puppet(self.uin, base_id, **row)
        return store.get_puppet(self.uin, base_id)

    def deploy(self, ids=(1000001,)):
        g.save_puppet_lineup(self.player, ids)

    def call(self, action, fields):
        body = b"".join(g.pb_varint_field(k, v) for k, v in fields.items())
        return g.REQUEST_HANDLERS["PVZ.Cmd.Cmd_Puppet_" + action + "_CS"](body, self.player)

    def give_cost(self, cost):
        for iid, count in cost:
            if iid not in (1, g._DUMMY_ITEM_SKILL):
                store.add_item(self.uin, iid, count)

    def assert_push(self, messages):
        body = [b for n, b in messages if n == FORMATION]
        self.assertEqual(len(body), 1, [n for n, _b in messages])
        self.assertEqual(g.parse_varint_fields(body[0]).get(4), 1)
        team = g.parse_repeated_submessages(body[0], 2)
        self.assertEqual(len(team), 1)
        return team[0]

    def test_original_raw_and_integer_protocol_vectors(self):
        for base_id, row, raw, normalized in VECTORS:
            with self.subTest(base_id=base_id, row=row):
                self.puppet(base_id, **row)
                self.deploy((base_id,))
                actual = g._puppet_power_raw_attributes(base_id, store.get_puppet(self.uin, base_id))
                self.assertEqual(tuple(actual[k] for k in RAW_FIELDS), raw)
                self.assertEqual(tuple(g.puppet_deployed_stat_bonus(self.player)[k] for k in FIELDS), normalized)

    def test_authored_speed_columns_have_no_fractional_values(self):
        from decimal import Decimal
        rows = g._read_clientdb_table("dummyPropInfo.txt")
        cols = {name: n for n, name in enumerate(rows[0])}
        self.assertEqual(len(rows) - 1, 840)
        for field, maximum in (("baseSpeed", 528), ("growSpeed", 50)):
            values = [Decimal(r[cols[field]]) for r in rows[1:]]
            self.assertTrue(all(value == int(value) for value in values))
            self.assertEqual((min(values), max(values)), (0, maximum))

    def test_raw_is_aggregated_before_integer_unit_conversion(self):
        self.puppet(1000001)
        self.puppet(1000002)
        self.deploy((1000001, 1000002))
        raw = [g._puppet_power_raw_attributes(i, store.get_puppet(self.uin, i)) for i in (1000001, 1000002)]
        expected = tuple(sum(x[k] for x in raw) // (600 if n < 4 else 6)
                         for n, k in enumerate(RAW_FIELDS))
        self.assertEqual(tuple(g.puppet_deployed_stat_bonus(self.player)[k] for k in FIELDS), expected)

    def test_sparse_duplicate_unowned_and_sixth_slots_add_nothing(self):
        self.puppet(1000001)
        self.puppet(1000002)
        self.deploy((0, 1000001, 1000001, 9999999, 0, 1000002))
        self.assertEqual(g.puppet_lineup_slots(self.player), [0, 1000001, 0, 0, 0])
        self.assertEqual(tuple(g.puppet_deployed_stat_bonus(self.player)[k] for k in FIELDS), VECTORS[0][3])

    def test_fixed_stats_do_not_depend_on_plant_level_or_team_size(self):
        self.puppet()
        base = g.effective_card_stats(self.player, 101, 10, uid=self.uid)
        self.deploy()
        actual = g.effective_card_stats(self.player, 101, 10, uid=self.uid)
        delta = tuple(actual[k] - base[k] for k in FIELDS)
        self.assertEqual(delta, VECTORS[0][3])
        for count in (1, 3, 6):
            with self.subTest(count=count):
                units = g.build_player_units(self.player, dict(list(self.placements.items())[:count]))
                self.assertEqual(len(units), count)
                self.assertEqual({tuple(u[k] - base[k] for k in FIELDS) for u in units}, {delta})
                power_delta = g.card_power(actual) - g.card_power(base)
                self.assertEqual(sum(u["power"] - g.card_power(base) for u in units), count * power_delta)
                # The source panel divides a six-plant raw aggregate; integer
                # plant fields can introduce small final rounding differences.
                self.assertLessEqual(abs(power_delta - g.puppet_power(1000001, store.get_puppet(self.uin, 1000001)) // 6), 5)

    def test_formation_client_total_and_source_twenty_match_battle_units(self):
        self.puppet()
        self.deploy()
        body = g.build_formation_info(self.player, 1, self.placements)
        units = {u["uid"]: u for u in g.build_player_units(self.player, self.placements)}
        total = 0
        for cp in g.parse_repeated_submessages(body, 1):
            f = g.parse_varint_fields(cp)
            elements = {g.parse_varint_fields(e)[1]: g.parse_varint_fields(e)
                        for e in g.parse_repeated_submessages(cp, 6)}
            self.assertIn(20, elements)
            self.assertEqual(elements[0][2], units[f[2]]["power"])
            self.assertEqual(elements[0][2], sum(e[2] for k, e in elements.items() if k != 0))
            self.assertEqual(elements[0][3], units[f[2]]["speed"])
            total += elements[0][2]
        self.assertEqual(g.parse_varint_fields(body)[4], total)
        self.assertEqual(total, g._player_formation_power(self.player))

    def test_skill_speed_uses_authored_base_growth_and_int_additions(self):
        row = self.puppet(level=10, active_level=3, passive_level=4)
        self.deploy()
        cfg = g.load_dummy_config()
        p = cfg["props"][(1, 1, 100)]
        speed = int((p["speed"] + 10 * p["g_speed"]) * cfg["stars"][1]["buff"] / 10000)
        for kind in ("active", "passive"):
            info = cfg["active_skill"][cfg["skills"][row[kind + "_skill"]]["info"]]
            speed = int(speed + info["base_value"] + info["grow_value"] * row[kind + "_level"])
        self.assertGreater(speed, 0)
        self.assertEqual(g.puppet_deployed_speed(self.player), speed)
        st = g.effective_card_stats(self.player, 101, 10, uid=self.uid)
        self.assertEqual(st["refine_speed"], speed)
        elements = dict((e, (v, s)) for e, v, s in g._card_power_source_elements(
            self.player, self.uid, 101, 10, include_deployed=True))
        self.assertEqual(elements[20][1], speed)
        self.assertEqual(elements[0][1], speed)

    def test_feeding_deployed_puppet_refreshes_actual_cached_formation(self):
        self.puppet()
        self.deploy()
        store.add_item(self.uin, 95536, 1)
        before = g._player_formation_power(self.player)
        team = self.assert_push(self.call("UpgradeLevel", {1: 1000001, 2: 95536, 3: 1}))
        self.assertGreater(store.get_puppet(self.uin, 1000001)["level"], 1)
        self.assertGreater(g.parse_varint_fields(team)[4], before)
        self.assertEqual(g.parse_varint_fields(team)[4], g._player_formation_power(self.player))
        self.assertEqual(store.get_item_count(self.uin, 95536), 0)

    def test_star_equipment_and_skill_upgrade_refreshes(self):
        self.puppet(level=10, active_skill=102)
        self.deploy()
        cfg = g.load_dummy_config()
        store.add_item(self.uin, cfg["zombies"][1000001]["piece"], cfg["stars"][2]["pieces"])
        self.assert_push(self.call("UpgradeStar", {1: 1000001}))
        for iid, num in cfg["equip"][(1, 1)]["need"]:
            store.add_item(self.uin, iid, num)
        self.assert_push(self.call("UpgradeSparLevel", {1: 1000001, 2: 1, 3: 1}))
        for iid, num in cfg["skill_up"][1]:
            if iid not in (1, g._DUMMY_ITEM_SKILL):
                store.add_item(self.uin, iid, num)
        before_speed = g.puppet_deployed_speed(self.player)
        self.assert_push(self.call("UpgradeSkillLevel", {1: 1000001, 2: 0}))
        self.assertGreater(g.puppet_deployed_speed(self.player), before_speed)

    def test_null_skill_dto_falls_back_to_template_at_level_one(self):
        self.puppet()
        self.deploy()
        expected = g.puppet_deployed_speed(self.player)
        self.puppet(active_skill=0, active_level=77, passive_skill=0, passive_level=88)
        self.assertEqual(g.puppet_skills_bytes(store.get_puppet(self.uin, 1000001)), b"")
        self.assertEqual(g.puppet_deployed_speed(self.player), expected)

    def test_unknown_positive_skill_ids_do_not_gain_template_skill_speed(self):
        self.puppet(active_skill=9999999, passive_skill=9999998)
        self.deploy()
        prop = g.load_dummy_config()["props"][(1, 1, 100)]
        self.assertEqual(g.puppet_deployed_speed(self.player), prop["speed"] + prop["g_speed"])

    def test_unknown_and_zero_quality_add_no_attributes_or_speed(self):
        for quality in (0, 999999):
            with self.subTest(quality=quality):
                self.puppet(quality=quality)
                self.deploy()
                self.assertEqual(g.puppet_deployed_speed(self.player), 0)
                self.assertEqual(tuple(g.puppet_deployed_stat_bonus(self.player)[k] for k in FIELDS), (0,) * 8)

    def test_basic_skill_with_zero_speed_growth_has_no_power_refresh(self):
        self.puppet(level=10)
        self.deploy()
        before = g._puppet_deployment_signature(self.player)
        messages = self.call("UpgradeSkillLevel", {1: 1000001, 2: 0})
        self.assertEqual(store.get_puppet(self.uin, 1000001)["active_level"], 2)
        self.assertEqual(g._puppet_deployment_signature(self.player), before)
        self.assertNotIn(FORMATION, [n for n, _b in messages])

    def test_feeding_without_level_gain_and_same_lineup_do_not_refresh(self):
        self.puppet(level=100)
        self.deploy()
        store.add_item(self.uin, 95533, 1)
        before = g._puppet_deployment_signature(self.player)
        messages = self.call("UpgradeLevel", {1: 1000001, 2: 95533, 3: 1})
        self.assertEqual(store.get_puppet(self.uin, 1000001)["level"], 100)
        self.assertGreater(store.get_puppet(self.uin, 1000001)["exp"], 0)
        self.assertEqual(g._puppet_deployment_signature(self.player), before)
        self.assertNotIn(FORMATION, [n for n, _b in messages])
        formation = g.pb_bytes(1, g.pb_varint_field(1, 1000001))
        messages = g.handle_puppet_setformation(g.pb_bytes(1, formation), self.player)
        self.assertNotIn(FORMATION, [n for n, _b in messages])

    def test_puppet_quality_and_equipment_quality_refresh_real_plant_power(self):
        self.puppet(level=10)
        self.deploy()
        cfg = g.load_dummy_config()
        self.give_cost(cfg["quality"][100]["lossless"])
        before = g._player_formation_power(self.player)
        self.assert_push(self.call("UpgradeQuality", {1: 1000001, 2: 1}))
        self.assertGreater(g._player_formation_power(self.player), before)
        store.save_puppet(self.uin, 1000001, spars=f"1:{cfg['equip_quality'][1]['max_lv']}:1")
        self.give_cost(cfg["equip"][(1, 2)]["need"] + cfg["equip_quality"][2]["frags"]
                       + cfg["equip_quality"][2]["coins"])
        before = g._player_formation_power(self.player)
        self.assert_push(self.call("UpgradeSparQuality", {1: 1000001, 2: 1}))
        self.assertGreater(g._player_formation_power(self.player), before)

    def test_skill_quality_and_swap_refresh_deployed_plant_team(self):
        self.puppet(level=100, star=8, quality=300, active_level=100)
        self.puppet(1000002, level=10, quality=300)
        self.deploy()
        cfg = g.load_dummy_config()
        skill_info = cfg["active_skill"][cfg["skills"][101]["info"]]
        self.give_cost(skill_info["need"])
        speed_before = g.puppet_deployed_speed(self.player)
        self.assert_push(self.call("UpgradeSkillQuality", {1: 1000001, 2: 0}))
        self.assertGreater(g.puppet_deployed_speed(self.player), speed_before)
        self.give_cost(cfg["quality"][300]["swap_cost"])
        before = g._player_formation_power(self.player)
        self.assert_push(self.call("Swap", {1: 1000001, 2: 1000002}))
        self.assertLess(g._player_formation_power(self.player), before)

    def test_undeployed_upgrade_and_refused_request_do_not_emit_formation(self):
        self.puppet()
        store.add_item(self.uin, 95536, 1)
        messages = self.call("UpgradeLevel", {1: 1000001, 2: 95536, 3: 1})
        self.assertNotIn(FORMATION, [n for n, _b in messages])
        self.deploy()
        before = store.get_puppet(self.uin, 1000001)
        messages = self.call("UpgradeLevel", {1: 1000001, 2: 95536, 3: 1})
        self.assertNotIn(FORMATION, [n for n, _b in messages])
        self.assertEqual(store.get_puppet(self.uin, 1000001), before)
        store.save_player(self.uin, coin=0)
        self.player["coin"] = 0
        messages = self.call("UpgradeSkillLevel", {1: 1000001, 2: 0})
        self.assertNotIn(FORMATION, [n for n, _b in messages])
        self.assertEqual(g.parse_varint_fields(dict(messages)["PVZ.Cmd.Cmd_LogicError_SC"])[1], 202)
        self.assertEqual(store.get_puppet(self.uin, 1000001), before)
        self.assertEqual(store.get_player_by_role_id(self.player["role_id"])["coin"], 0)

    def test_set_and_remove_puppet_updates_plant_total_immediately(self):
        self.puppet()
        before = g._player_formation_power(self.player)
        info = g.pb_varint_field(1, 1000001)
        formation = g.pb_bytes(1, info)
        messages = g.handle_puppet_setformation(g.pb_bytes(1, formation), self.player)
        self.assert_push(messages)
        self.assertGreater(g._player_formation_power(self.player), before)
        self.assert_push(g.handle_puppet_setformation(g.pb_bytes(1, b""), self.player))
        self.assertEqual(g._player_formation_power(self.player), before)

    def test_failed_refresh_rolls_back_puppet_item_and_in_memory_player(self):
        self.puppet()
        self.deploy()
        store.add_item(self.uin, 95536, 1)
        before = store.get_puppet(self.uin, 1000001)
        player_before = copy.deepcopy(self.player)
        with patch.object(g, "handle_battle_getformation", side_effect=RuntimeError("injected formation failure")):
            with self.assertRaisesRegex(RuntimeError, "injected formation failure"):
                self.call("UpgradeLevel", {1: 1000001, 2: 95536, 3: 1})
        self.assertEqual(store.get_puppet(self.uin, 1000001), before)
        self.assertEqual(store.get_item_count(self.uin, 95536), 1)
        self.assertEqual(self.player, player_before)

    def test_puppet_fuben_has_same_fixed_bonus_without_percentage_layer(self):
        self.puppet(level=10, star=2)
        self.deploy()
        with patch.object(g, "_puppet_fuben_placements", return_value=self.placements), \
                patch.object(g, "_puppet_fuben_bonus", side_effect=AssertionError("obsolete percentage called")):
            left, _right = g._puppet_fuben_units(self.player, 1, 1, 1, 1)
        expected = g.build_player_units(self.player, self.placements)
        self.assertEqual(left, expected)

    def test_collection_and_rank_metrics_do_not_change_when_deploying_puppet(self):
        self.puppet()
        before = (g._player_fight_power(self.player), g._rank_player_power(self.player))
        base = g.effective_card_stats(self.player, 101, 10, g._ZERO_STAT_BONUS, uid=self.uid)
        self.deploy()
        self.assertEqual((g._player_fight_power(self.player), g._rank_player_power(self.player)), before)
        self.assertEqual(g.effective_card_stats(self.player, 101, 10, g._ZERO_STAT_BONUS, uid=self.uid), base)

    def test_router_uses_the_wrapped_actions(self):
        for action, name in (("SetFormation", "handle_puppet_setformation"),
                             ("UpgradeLevel", "handle_puppet_upgradelevel"),
                             ("UpgradeStar", "handle_puppet_upgradestar"),
                             ("UpgradeQuality", "handle_puppet_upgradequality"),
                             ("UpgradeSkillLevel", "handle_puppet_upgradeskilllevel"),
                             ("UpgradeSkillQuality", "handle_puppet_upgradeskillquality"),
                             ("UpgradeSparLevel", "handle_puppet_upgradesparlevel"),
                             ("UpgradeSparQuality", "handle_puppet_upgradesparquality"),
                             ("Swap", "handle_puppet_swap")):
            self.assertIs(g.REQUEST_HANDLERS["PVZ.Cmd.Cmd_Puppet_" + action + "_CS"], getattr(g, name))
            self.assertTrue(hasattr(getattr(g, name), "__wrapped__"))


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        store.close_thread_db()
        DB.cleanup()
