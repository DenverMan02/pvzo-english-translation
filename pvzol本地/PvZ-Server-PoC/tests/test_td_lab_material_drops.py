"""Lab reagents (实验室材料) drop from the chapter ItemInfo.gainWay names, not a rotation.

ItemInfo.gainWay is the authority:
    40007 普通骨  -> "皇陵主题章节随机掉落"     40009 1级骨 -> "皇陵之侧精英第9关掉落"
    40001 普通砂  -> "沙漠主题章节随机掉落"     40027 1级耀星 -> "海底探险精英第14关掉落"
The five families line up with FubenChapterTemplate's chapter groups (1-4/5-8/9-12/13-16/17-21)
and with the story worlds, so 秦始皇陵 (world 1) must hand out 骨 — the old positional rotation
handed out 砂 there and never touched the 普通/高级 members at all.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_td_lab_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

# (普通, 高级, 1级, 2级, 3级, 4级) per themed chapter group, straight from ItemInfo.gainWay.
SAND = (40001, 40002, 40003, 40004, 40005, 40006)
BONE = (40007, 40008, 40009, 40010, 40011, 40012)
BANDANA = (40013, 40014, 40015, 40016, 40017, 40018)
JEANS = (40019, 40020, 40021, 40022, 40023, 40024)
STAR = (40025, 40026, 40027, 40028, 40029, 40030)


def item_updates(messages):
    result = {}
    for name, body in messages:
        if name != "PVZ.Cmd.Cmd_Item_Update_SC":
            continue
        for dto in srv.parse_repeated_submessages(body, 1):
            fields = srv.parse_varint_fields(dto)
            result[fields.get(2, 0)] = fields.get(3, 0)
    return result


def report_items(messages):
    """{itemId: num} from the TD report SC's itemReward (field 8, repeated Dto_IdNum)."""
    body = next(b for n, b in messages if n == "PVZ.Cmd.Cmd_TD_ReportLevelResult_SC")
    out = {}
    for part in srv.parse_repeated_submessages(body, 8):
        fields = srv.parse_varint_fields(part)
        out[fields.get(1, 0)] = fields.get(2, 0)
    return out


class LabMaterialThemeTest(unittest.TestCase):
    def test_material_pool_comes_from_real_reagent_items_and_lab_costs(self):
        materials = srv.td_lab_material_ids()
        self.assertTrue(materials)
        self.assertEqual(materials, tuple(sorted(set(materials))))

        item_rows = srv._read_clientdb_table("ItemInfo.txt")
        item_headers = {name: i for i, name in enumerate(item_rows[0])}
        reagent_ids = {
            int(row[item_headers["id"]])
            for row in item_rows[1:]
            if row[item_headers["type"]] == "8"
        }
        lab_cost_ids = {
            item_id
            for bottle in srv.load_lab().values()
            for level in bottle["levels"].values()
            for key in ("unlockCost", "upgradeCost")
            for item_id, _amount in level[key]
        }
        self.assertEqual(set(materials), reagent_ids & lab_cost_ids)

    def test_chapters_map_to_the_five_gainway_themes(self):
        for chapter, family in ((1, SAND), (3, SAND), (5, BONE), (8, BONE),
                                (9, BANDANA), (12, BANDANA), (14, JEANS), (16, JEANS),
                                (17, STAR), (21, STAR)):
            self.assertEqual(srv.lab_theme_family(chapter), family, chapter)
        # Chapters past the 21 authored ones reuse the cycle instead of drying up.
        self.assertEqual(srv.lab_theme_family(22), SAND)
        self.assertIsNone(srv.lab_theme_family(0))

    def test_story_worlds_drop_their_own_theme_material(self):
        self.assertEqual(srv._TD_WORLD_LAB_FAMILY[1], (BONE[0], BONE[1]))    # 秦始皇陵 = 皇陵
        self.assertEqual(srv._TD_WORLD_LAB_FAMILY[2], (SAND[0], SAND[1]))    # 埃及沙漠 = 沙漠
        self.assertEqual(srv._TD_WORLD_LAB_FAMILY[3], (BANDANA[0], BANDANA[1]))
        self.assertEqual(srv._TD_WORLD_LAB_FAMILY[4], (JEANS[0], JEANS[1]))
        self.assertEqual(srv._TD_WORLD_LAB_FAMILY[5], (STAR[0], STAR[1]))
        # Tutorial / unknown world -> nothing.
        self.assertEqual(srv.td_lab_material_reward(0, 1, 1), [])
        self.assertEqual(srv.td_lab_material_reward(999, 1, 1), [])

    def test_world_one_hands_out_bone_not_salt(self):
        with patch.object(srv.random, "randint", return_value=1):   # force both rolls to hit
            granted = srv.td_lab_material_reward(1, 1, 1)
        self.assertEqual(sorted(granted), sorted([(BONE[0], 1), (BONE[1], 1)]))
        with patch.object(srv.random, "randint", return_value=10_000):   # force both to miss
            self.assertEqual(srv.td_lab_material_reward(1, 1, 1), [])

    def test_fuben_clear_rolls_the_theme_and_shows_it_in_the_chest(self):
        # A normal-mode clear of a themed chapter rolls the 普通/高级 pair.
        with patch.object(srv.random, "randint", return_value=1):
            self.assertEqual(sorted(srv.fuben_lab_material_reward(5, 1, 1)),
                             sorted([(BONE[0], 1), (BONE[1], 1)]))
        with patch.object(srv.random, "randint", return_value=10_000):
            self.assertEqual(srv.fuben_lab_material_reward(5, 1, 1), [])
        self.assertEqual(srv.fuben_lab_material_reward(0, 1, 1), [])

    def test_elite_stage_9_and_14_drop_the_graded_material(self):
        # gainWay: chapters 1-8 drop at 精英第9关, chapters 9+ at 精英第14关. The first clear is
        # already covered by fuben.txt bonusFirst, so only repeats roll here.
        for chapter, stage, item in ((1, 9, 40003), (5, 9, 40009),
                                     (9, 14, 40015), (14, 14, 40021), (21, 14, 40030)):
            with patch.object(srv.random, "randint", return_value=1):
                dropped = srv.lab_grade_reagent(chapter, 2, stage, is_first=False)
            self.assertEqual(dropped, [(item, 1)], (chapter, stage))
            self.assertEqual(srv.lab_grade_reagent(chapter, 2, stage, is_first=True), [])
        # Wrong stage or normal mode drops nothing.
        self.assertEqual(srv.lab_grade_reagent(1, 2, 8, False), [])
        self.assertEqual(srv.lab_grade_reagent(1, 1, 9, False), [])

    def test_win_grants_themed_material_and_lists_it_in_the_report(self):
        uin = 700001337
        player, _created = store.get_or_create_player(uin)
        player["level"], player["exp"] = 100, 0
        key = (1, 1, 1)
        self.assertIn(key, srv.load_level_rewards())
        authored = dict(srv.parse_bonus_items(srv.level_reward(*key).get("bonusItems", "")))
        authored_before = {item_id: store.get_item_count(uin, item_id) for item_id in authored}
        before = {item_id: store.get_item_count(uin, item_id) for item_id in BONE}

        request = (srv.pb_varint_field(1, key[0]) + srv.pb_varint_field(2, key[1])
                   + srv.pb_varint_field(3, key[2]) + srv.pb_varint_field(4, 1))
        with (patch.object(srv.random, "randint", return_value=1),
              patch.object(srv, "_quest_sync_messages", return_value=[]),
              patch.object(srv, "_quest_record_condition", return_value=[]),
              patch.object(srv.main_quest_local, "record_td_clear", return_value=[])):
            messages = srv.handle_td_report_result(request, player)
            srv.handle_td_report_result(request, player)     # replay: same payout

        # 秦始皇陵 pays out its 骨 pair — never the 砂 the old positional rotation produced. The
        # graded members are 精英副本 stage-9/14 drops, which the story worlds don't have.
        self.assertEqual(store.get_item_count(uin, BONE[0]), before[BONE[0]] + 2)
        self.assertEqual(store.get_item_count(uin, BONE[1]), before[BONE[1]] + 2)
        self.assertEqual(store.get_item_count(uin, BONE[2]), before[BONE[2]])
        self.assertEqual(store.get_item_count(uin, SAND[0]), 0)
        for item_id, amount in authored.items():
            self.assertEqual(store.get_item_count(uin, item_id),
                             authored_before[item_id] + 2 * amount)

        # The 通关奖励 chest reads itemReward (field 8) off the report SC, so every granted item
        # must be listed there or the player never sees it.
        granted = item_updates(messages)
        listed = report_items(messages)
        for item_id, total in granted.items():
            self.assertEqual(listed.get(item_id), total, item_id)
        self.assertEqual(listed[BONE[0]], before[BONE[0]] + 1)
        self.assertEqual(listed[BONE[1]], before[BONE[1]] + 1)
        self.assertIn(51200, listed)                      # authored fragment still listed too

    def test_loss_and_tutorial_success_do_not_grant_lab_material(self):
        materials = srv.td_lab_material_ids()
        for uin, key, passed in ((700001338, (1, 1, 1), 0),
                                 (700001339, (0, 1, 1), 1)):
            player, _created = store.get_or_create_player(uin)
            before = {item_id: store.get_item_count(uin, item_id) for item_id in materials}
            request = (srv.pb_varint_field(1, key[0]) + srv.pb_varint_field(2, key[1])
                       + srv.pb_varint_field(3, key[2]) + srv.pb_varint_field(4, passed))
            with patch.object(srv.random, "randint", return_value=1):
                messages = srv.handle_td_report_result(request, player)
            self.assertFalse(any(name == "PVZ.Cmd.Cmd_Item_Update_SC"
                                 for name, _body in messages))
            self.assertEqual({item_id: store.get_item_count(uin, item_id)
                              for item_id in materials}, before)


if __name__ == "__main__":
    unittest.main()
