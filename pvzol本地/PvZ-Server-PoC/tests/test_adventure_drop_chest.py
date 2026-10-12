"""End-to-end: an adventure (TD) clear must put the Lab reagent AND 星魄 into BOTH the
player's bag AND the reward chest the client pops.

This is the regression test for the player report "冒险打了一章仍没看见实验室材料（普通骨）"
and "冒险模式中也没从战利品宝箱中看到星魄".

Two things have to hold, and they fail independently:

1. THE BAG. handle_td_report_result calls grant_items(), so the count really lands in
   `items`. If a drop id is not a real ItemInfo template the client's bag icon resolves to
   nothing and the material reads as "抽到空气" even though the row exists.

2. THE CHEST. Field 8 `itemReward` of Cmd_TD_ReportLevelResult_SC is what
   TDProxy.onCmd_TD_ReportLevelResult_SC hands to CommonCmd.itemFly() — the "战利品宝箱"
   panel. Each element must be its OWN length-delimited Dto_IdNum submessage
   (field 1 = item id, field 2 = amount); concatenating several Dto_IdNum payloads into one
   blob and wrapping that once makes the client read a repeated field number twice and throw
   IOError "Bad data format: ... cannot be set twice".

The roll is probabilistic (35% common / 12% rare Lab reagent, up to 20% 星魄), so the test
forces the RNG instead of sampling thousands of clears.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_adventure_drop_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700000204
LEVEL = (1, 1, 1)                 # stage 1 = 秦始皇陵 -> the 骨 family (40007/40008)
COMMON_BONE = 40007
RARE_BONE = 40008
STAR_SOUL = 2121
DUST = 51200


def cs_report(stage, level, sub, passed=1):
    """Cmd_TD_ReportLevelInfo CS bytes: stageId(1) levelId(2) subLevelId(3) passResult(4)."""
    return (srv.pb_varint_field(1, stage) + srv.pb_varint_field(2, level)
            + srv.pb_varint_field(3, sub) + srv.pb_varint_field(4, passed))


def chest_items(messages):
    """itemId -> amount from field 8 (itemReward) of Cmd_TD_ReportLevelResult_SC."""
    for name, body in messages:
        if name != "PVZ.Cmd.Cmd_TD_ReportLevelResult_SC":
            continue
        out = {}
        for dto in srv.parse_repeated_submessages(body, 8):
            f = srv.parse_varint_fields(dto)
            out[f.get(1, 0)] = f.get(2, 0)
        return out
    return None


def bag_counts(uin):
    return {r["item_id"]: r["count"] for r in store.get_items(uin)}


def run_clear(stage, level, sub, uin=UIN):
    store.get_or_create_player(uin)
    store.save_player(uin, level=200, coin=500000, exp=0, td_stage=stage, td_level=level)
    player, _created = store.get_or_create_player(uin)
    player["plants"] = [1200]
    # handle_td_report_result also drives quests and the main-line changelog; neither is
    # under test here and both write to the same temp DB.
    with patch.object(srv, "_quest_sync_messages", return_value=[]), \
         patch.object(srv, "_quest_record_condition", return_value=[]), \
         patch.object(srv.main_quest_local, "record_td_clear", return_value=[]):
        return srv.handle_td_report_result(cs_report(stage, level, sub), player)


def forced(roll_impl):
    """Patch the random roll so EVERY reagent draw yields `roll_impl`'s tier table."""
    def fake_roll(dietician_id):
        tier = roll_impl()
        return 60000 + tier * 100 + 1
    return fake_roll


class AdventureDropChestTest(unittest.TestCase):
    """Each case uses its own uin so bag state cannot leak between tests."""

    def test_forced_common_bone_lands_in_bag_and_chest(self):
        uin = 700002201
        with patch.object(srv, "td_lab_material_reward",
                          return_value=[(COMMON_BONE, 1)]), \
             patch.object(srv, "td_star_soul_reward", return_value=[]):
            messages = run_clear(*LEVEL, uin=uin)

        self.assertEqual(store.get_item_count(uin, COMMON_BONE), 1,
                         "普通骨 must be in the bag after the clear")
        chest = chest_items(messages)
        self.assertIsNotNone(chest, "no Cmd_TD_ReportLevelResult_SC in the reply")
        self.assertEqual(chest.get(COMMON_BONE), 1,
                         "普通骨 must appear in the 战利品宝箱 (field 8 itemReward)")

    def test_forced_star_soul_lands_in_bag_and_chest(self):
        uin = 700002202
        with patch.object(srv, "td_lab_material_reward", return_value=[]), \
             patch.object(srv, "td_star_soul_reward", return_value=[(STAR_SOUL, 2)]):
            messages = run_clear(*LEVEL, uin=uin)

        self.assertEqual(store.get_item_count(uin, STAR_SOUL), 2)
        chest = chest_items(messages)
        self.assertIsNotNone(chest)
        self.assertEqual(chest.get(STAR_SOUL), 2,
                         "星魄 must appear in the 战利品宝箱")

    def test_chest_carries_several_items_as_separate_submessages(self):
        """The 'cannot be set twice' trap: multiple rewards must each get their own
        length-delimited wrapper, or the client's parser sees a duplicate field number."""
        uin = 700002203
        with patch.object(srv, "td_lab_material_reward",
                          return_value=[(COMMON_BONE, 1), (RARE_BONE, 1)]), \
             patch.object(srv, "td_star_soul_reward", return_value=[(STAR_SOUL, 2)]), \
             patch.object(srv, "parse_bonus_items", return_value=[]):
            messages = run_clear(*LEVEL, uin=uin)

        body = dict(messages)["PVZ.Cmd.Cmd_TD_ReportLevelResult_SC"]
        raw = srv.parse_repeated_submessages(body, 8)
        self.assertEqual(len(raw), 3, "each reward needs its own field-8 submessage")
        # Re-parsing each submessage independently is exactly what the client does; if two
        # Dto_IdNum had been concatenated the second read would collide.
        seen = [srv.parse_varint_fields(dto) for dto in raw]
        self.assertEqual({f.get(1) for f in seen}, {COMMON_BONE, RARE_BONE, STAR_SOUL})
        self.assertEqual(store.get_item_count(uin, RARE_BONE), 1)
        self.assertEqual(store.get_item_count(uin, STAR_SOUL), 2)

    def test_every_droppable_lab_material_is_a_real_item_template(self):
        """A material id that ItemInfo does not define renders as a blank icon in the bag
        and an empty row in the chest — the "抽到空气" symptom."""
        rows = srv._read_clientdb_table("ItemInfo.txt")
        self.assertTrue(rows, "ItemInfo.txt unreadable — the check below would pass vacuously")
        col = {name: i for i, name in enumerate(rows[0])}
        self.assertIn("id", col, "ItemInfo.txt has no 'id' column: %r" % (rows[0][:8],))
        known = set()
        for r in rows[1:]:
            if len(r) > col["id"] and r[col["id"]].strip().isdigit():
                known.add(int(r[col["id"]]))
        self.assertGreater(len(known), 1000, "ItemInfo id column parsed wrong")
        for item_id in sorted(srv.td_lab_material_ids()) + [STAR_SOUL, DUST]:
            self.assertIn(item_id, known,
                          "item %d is granted but absent from ItemInfo.txt" % item_id)

    def test_a_cleared_authored_level_actually_rolls(self):
        """Sanity check on the probabilistic path itself: real authored levels must be able
        to drop both, otherwise the forced tests above prove nothing about live play."""
        stage, level, sub = LEVEL
        self.assertIn((stage, level, sub), srv.load_level_rewards())
        common = rare = soul = 0
        for _ in range(4000):
            common += sum(1 for i, _ in srv.td_lab_material_reward(stage, level, sub)
                          if i == COMMON_BONE)
            rare += sum(1 for i, _ in srv.td_lab_material_reward(stage, level, sub)
                        if i == RARE_BONE)
            soul += sum(1 for i, _ in srv.td_star_soul_reward(stage, level, sub)
                        if i == STAR_SOUL)
        self.assertGreater(common, 0, "普通骨 is unreachable")
        self.assertGreater(rare, 0, "高级骨 is unreachable")
        self.assertGreater(soul, 0, "星魄 is unreachable")


if __name__ == "__main__":
    unittest.main()
