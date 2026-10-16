"""Quick regression checks for battle stat and tooltip string translations."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER / "client_tools"))

from translate_battle_ui import _module_targets, _translate_display_string


class BattleUITranslationTests(unittest.TestCase):
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



if __name__ == "__main__":
    unittest.main()
