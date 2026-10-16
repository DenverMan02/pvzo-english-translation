"""Regression checks for in-game UI, tooltip, and SWF translation safety."""
from __future__ import annotations

import struct
import sys
import unittest
import zlib
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER / "client_tools"))

from translate_battle_ui import SUBSTRING_TRANSLATIONS, TRANSLATIONS, _iter_edit_text_fields, _module_targets, _patch_abc_strings, _patch_swf, _read_constant_pools, _translate_display_string


class BattleUITranslationTests(unittest.TestCase):
    def test_substring_translation_keys_all_exist(self):
        self.assertEqual(set(SUBSTRING_TRANSLATIONS) - set(TRANSLATIONS), set())
    def test_bulk_label_translation_uses_one_pass_without_losing_neighbors(self):
        text = " | ".join(["生命+1500 技力+250", "阵容", "连续签到", "世界BOSS", "当前等级：120"] * 40)
        expected = " | ".join(["Health+1500 Skill Power+250", "Formation", "Consecutive Check-Ins", "World Boss", "Current Level: 120"] * 40)
        self.assertEqual(_translate_display_string(text), expected)
    def test_stat_breakdown_labels(self):
        text = "总和 战力 2784 先攻 1600 基础战力 1919"
        self.assertEqual(
            _translate_display_string(text),
            "Total Power 2784 Initiative 1600 Base Power 1919",
        )

    def test_health_and_skill_power_bonus(self):
        self.assertEqual(
            _translate_display_string("生命+1500 技力+250"),
            "Health+1500 Skill Power+250",
        )

    def test_battle_panel_labels(self):
        self.assertEqual(
            _translate_display_string("队伍总战力：21690"),
            "Total Team Power：21690",
        )
        self.assertEqual(
            _translate_display_string("队长技能：[Intermediate Skill]"),
            "Leader Skill：[Intermediate Skill]",
        )

    def test_known_endless_messages(self):
        self.assertEqual(
            _translate_display_string("挑战第3关成功！"),
            "Challenge stage 3 cleared!",
        )
        self.assertEqual(_translate_display_string("暂无奖励！"), "No rewards available!")

    def test_does_not_translate_stat_words_inside_chinese_prose(self):
        self.assertEqual(_translate_display_string("敌方攻击当前目标"), "敌方攻击当前目标")
        self.assertEqual(_translate_display_string("该技能增加生命恢复效果"), "该技能增加生命恢复效果")
        self.assertEqual(_translate_display_string("金币不足，请稍后重试"), "金币不足，请稍后重试")

    def test_standalone_buttons_and_reset_prompts(self):
        self.assertEqual(_translate_display_string("取消"), "Cancel")
        self.assertEqual(_translate_display_string("确定"), "Confirm")
        self.assertEqual(_translate_display_string("免费重置进度"), "Reset Progress for Free")
        self.assertEqual(_translate_display_string("购买重置进度"), "Purchase Progress Reset")

    def test_endless_reset_tooltip(self):
        self.assertEqual(
            _translate_display_string("本难度历史最高成绩为第{0}关，可以使用一键扫荡到第{1}关"),
            "Best result in this difficulty: stage {0}. Use Quick Sweep to clear up to stage {1}.",
        )

    def test_short_ui_words_do_not_rewrite_chinese_sentences(self):
        phrase = "玩家正在战斗，等待好友领取奖励"
        self.assertEqual(_translate_display_string(phrase), phrase)
    def test_rich_text_tooltip_labels(self):
        self.assertEqual(
            _translate_display_string("<font>(未领取)</font>"),
            "<font>(Unclaimed)</font>",
        )

    def test_endless_reset_rich_tooltip(self):
        tooltip = (
            "今日免费重置：<font>{0}</font>/{1}<br>"
            "每日0点恢复免费次数；用完后每次{2}钻购买重置"
        )
        self.assertEqual(
            _translate_display_string(tooltip),
            "Free resets today: <font>{0}</font>/{1}<br>"
            "Free resets replenish daily at midnight; after they run out, each reset costs {2} diamonds.",
        )

    def test_common_resource_and_service_labels(self):
        self.assertEqual(_translate_display_string("金币"), "Coins")
        self.assertEqual(_translate_display_string("服务器繁忙"), "Server busy")
        self.assertEqual(_translate_display_string("今日剩余次数： 3/3"), "Attempts Remaining Today: 3/3")
    def test_module_targets_exclude_data_and_font_resources(self):
        manifest = (
            b'<file name="ModuleCard.swf" size="12" ver="abc" />'
            b'<file name="ModuleCard_0123456789abcdef0123456789abcdef.swf" size="12" ver="abc" />'
            b'<file name="ModuleClientDBRes.swf" size="99" ver="def" />'
            b'<file name="ModuleFont.swf" size="99" ver="def" />'
        )
        targets = _module_targets(manifest)
        self.assertIn("ModuleCard.swf", targets)
        self.assertNotIn("ModuleCard_0123456789abcdef0123456789abcdef.swf", targets)
        self.assertNotIn("ModuleClientDBRes.swf", targets)
        self.assertNotIn("ModuleFont.swf", targets)
        self.assertEqual(targets.count("ModuleCard.swf"), 1)

    def test_common_pvz_terms(self):
        self.assertEqual(_translate_display_string("阵容"), "Formation")
        self.assertEqual(_translate_display_string("技能树"), "Skill Tree")
        self.assertEqual(_translate_display_string("火属性"), "Fire Element")
        self.assertEqual(_translate_display_string("护盾上限"), "Max Shield")
        self.assertEqual(_translate_display_string("无尽炼狱"), "Endless Purgatory")
        self.assertEqual(_translate_display_string("奖励预览"), "Reward Preview")

    def test_constant_pool_translation_does_not_need_method_body_parser(self):
        # Minimal ABC header and constant pools only; there is no method/body tail.
        abc = bytes([0, 0, 46, 0, 1, 1, 1, 2, 6]) + "生命".encode("utf-8") + bytes([1, 1, 1])
        patched, changed, hits, blocked = _patch_abc_strings(abc)
        self.assertEqual(changed, 1)
        self.assertEqual(hits, 1)
        self.assertEqual(blocked, 0)
        self.assertEqual(_read_constant_pools(patched)["strings"][1], "Health")

    def test_endless_failure_stage_suffix_is_translated_in_its_pool(self):
        prefix = "挑战失败！当前成绩为第".encode("utf-8")
        suffix = "关".encode("utf-8")
        abc = bytes([0, 0, 46, 0, 1, 1, 1, 3, len(prefix)]) + prefix + bytes([len(suffix)]) + suffix + bytes([1, 1, 1])
        patched, changed, hits, blocked = _patch_abc_strings(abc)
        strings = _read_constant_pools(patched)["strings"]
        self.assertEqual(strings[1], "Challenge failed! Current result: stage ")
        self.assertEqual(strings[2], "")
        self.assertEqual(changed, 2)
        self.assertGreaterEqual(hits, 2)
        self.assertEqual(blocked, 0)
    def test_constant_pool_translation_preserves_namespace_and_property_names(self):
        # The '生命' constant is referenced as both a package namespace and a QName.
        abc = (
            bytes([0, 0, 46, 0, 1, 1, 1, 2, 6])
            + '生命'.encode('utf-8')
            + bytes([2, 0x16, 1, 1, 2, 0x07, 1, 1])
        )
        patched, changed, hits, blocked = _patch_abc_strings(abc)
        self.assertEqual(changed, 0)
        self.assertEqual(hits, 0)
        self.assertGreater(blocked, 0)
        self.assertEqual(_read_constant_pools(patched)['strings'][1], '生命')

    def test_card_progression_and_item_requirements(self):
        self.assertEqual(_translate_display_string("基因槽"), "Gene Slot")
        self.assertEqual(_translate_display_string("确认购买"), "Confirm Purchase")
        self.assertEqual(_translate_display_string("道具已满"), "Item Limit Reached")
        self.assertEqual(_translate_display_string("已达到最高等级"), "Maximum Level Reached")
        self.assertEqual(_translate_display_string("当前不可用"), "Currently Unavailable")
        self.assertEqual(_translate_display_string("功能暂未开放"), "Feature Not Available Yet")

    def test_tooltip_and_common_gameplay_messages(self):
        self.assertEqual(_translate_display_string("增加经验："), "XP Gained: ")
        self.assertEqual(_translate_display_string("当前等级："), "Current Level: ")
        self.assertEqual(_translate_display_string("没有足够的经验"), "Not enough XP")
        self.assertEqual(_translate_display_string("金币不足！"), "Not enough coins!")
        self.assertEqual(_translate_display_string("请先选择植物！"), "Select a plant first!")
        self.assertEqual(_translate_display_string("此操作无法撤销"), "This action cannot be undone")
    def test_dynamic_endless_failure_toast(self):
        self.assertEqual(
            _translate_display_string("挑战失败！当前成绩为第3关"),
            "Challenge failed! Current result: stage 3",
        )
        self.assertEqual(_translate_display_string("挑战第3关成功！"), "Challenge stage 3 cleared!")
    def test_endless_purgatory_floating_notices(self):
        self.assertEqual(_translate_display_string("暂无新的战报！"), "No new battle reports!")
        self.assertEqual(_translate_display_string("暂无奖励！"), "No rewards available!")

    def test_split_free_reset_tooltip_fragments(self):
        self.assertEqual(_translate_display_string("今日免费重置："), "Free resets today: ")
        self.assertEqual(
            _translate_display_string("次\n每日0点恢复免费次数；用完后每次"),
            " reset(s) left.\nFree resets replenish daily at midnight; after they run out, each reset costs ",
        )
        self.assertEqual(_translate_display_string("钻购买重置"), " diamonds.")
    def test_pvz_online_plant_and_zombie_names(self):
        self.assertEqual(_translate_display_string("胡萝卜火箭"), "Carrot Rocket")
        self.assertEqual(_translate_display_string("竹小龙"), "Bamboo Dragon")
        self.assertEqual(_translate_display_string("冰冻生菜"), "Iceberg Lettuce")
        self.assertEqual(_translate_display_string("秦尸皇"), "Qin Shi Huang Zombie")
        self.assertEqual(_translate_display_string("尸身人面像"), "Mummy Sphinx")
    def test_daily_activity_and_multiplayer_labels(self):
        self.assertEqual(_translate_display_string("每日任务"), "Daily Quests")
        self.assertEqual(_translate_display_string("连续签到"), "Consecutive Check-Ins")
        self.assertEqual(_translate_display_string("兑换码"), "Redemption Code")
        self.assertEqual(_translate_display_string("匹配成功"), "Match Found")
        self.assertEqual(_translate_display_string("队伍已满"), "Team is Full")
        self.assertEqual(_translate_display_string("时光之旅"), "Time Journey")
        self.assertEqual(_translate_display_string("世界BOSS"), "World Boss")
    def test_town_chat_and_activity_labels_from_collage(self):
        self.assertEqual(_translate_display_string("VIP增加次数"), "VIP bonus attempts")
        self.assertEqual(_translate_display_string("世界"), "World")
        self.assertEqual(_translate_display_string("私聊"), "Private Chat")
        self.assertEqual(_translate_display_string("登录送礼"), "Login Gifts")
        self.assertEqual(_translate_display_string("热点活动"), "Featured Events")
        self.assertEqual(_translate_display_string("成就奖励"), "Achievement Rewards")
        self.assertEqual(_translate_display_string("系统公告"), "System Announcements")
        self.assertEqual(_translate_display_string("巅峰王者"), "Peak Champion")
        self.assertEqual(_translate_display_string("每日任务"), "Daily Quests")
        self.assertEqual(_translate_display_string("战力达人"), "Power Rankings")
        self.assertEqual(_translate_display_string("联盟活动"), "Alliance Events")
        self.assertEqual(_translate_display_string("任务提示"), "Quest Tips")
        self.assertEqual(_translate_display_string("套装"), "Sets")
        self.assertEqual(_translate_display_string("收起"), "Collapse")
        self.assertEqual(_translate_display_string("展开"), "Expand")
    def test_dynamic_sweep_counters_and_reward_copy(self):
        self.assertEqual(_translate_display_string("今日可挑战:5次"), "Challenges available today: 5 attempts")
        self.assertEqual(_translate_display_string("免费次数：9999次（VIP增加次数）"), "Free attempts: 9999 attempts（VIP bonus attempts）")
        self.assertEqual(_translate_display_string("首次通关可以获得500Coins"), "First clear rewards: 500 Coins")
        self.assertEqual(_translate_display_string("每天可以领取1000Coins"), "Available daily: 1000 Coins")
    def test_swf_edit_text_title_is_translated_outside_abc(self):
        rect = bytes([0x08, 0x00])  # RECT with Nbits=1
        frame_header = bytes([0x00, 0x0C, 0x01, 0x00])
        initial_text = "扫荡结算".encode("utf-8") + b"\x00"
        payload = (
            struct.pack("<H", 1)
            + rect
            + struct.pack("<H", 0x8000)  # HasText
            + b"label\x00"
            + initial_text
        )
        tag_header = struct.pack("<H", (37 << 6) | len(payload))  # DefineEditText
        end_tag = bytes([0x00, 0x00])
        body = rect + frame_header + tag_header + payload + end_tag
        swf = b"FWS" + bytes([10]) + struct.pack("<I", 8 + len(body)) + body

        patched, changed, hits, blocked, tag_count, skipped = _patch_swf(swf)
        self.assertGreater(changed, 0)
        self.assertEqual(tag_count, 0)  # This fixture uses editable text, not ABC.
        self.assertEqual(skipped, 0)
        self.assertEqual(patched[:3], b"CWS")
        unpacked = patched[:8] + zlib.decompress(patched[8:])
        fields = list(_iter_edit_text_fields(unpacked))
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0][-1], "Sweep Summary")
    def test_screenshot_sweep_and_stage_reward_labels(self):
        self.assertEqual(_translate_display_string("扫荡奖励"), "Sweep Rewards")
        self.assertEqual(_translate_display_string("再扫荡一次"), "Sweep Again")
        self.assertEqual(_translate_display_string("第1次扫荡"), "Sweep #1")
        self.assertEqual(_translate_display_string("小镇繁荣度"), "Town Prosperity")
        self.assertEqual(_translate_display_string("怪物等级"), "Enemy Level")
        self.assertEqual(_translate_display_string("首次通关奖励"), "First-Clear Rewards")
        self.assertEqual(_translate_display_string("三星奖励"), "Three-Star Rewards")
        self.assertEqual(_translate_display_string("今日剩余扫荡次数: 3/5"), "Sweeps Remaining Today: 3/5")
        self.assertEqual(_translate_display_string("金币不足，请稍后重试"), "金币不足，请稍后重试")
    def test_power_source_breakdown_vocabulary(self):
        self.assertEqual(_translate_display_string("基础/强化"), "Base / Enhancement")
        self.assertEqual(_translate_display_string("强化战力"), "Enhancement Power")
        self.assertEqual(_translate_display_string("装备战力"), "Equipment Power")
        self.assertEqual(_translate_display_string("战力来源"), "Power Sources")
        self.assertEqual(_translate_display_string("队伍战力"), "Team Power")

if __name__ == "__main__":
    unittest.main()
