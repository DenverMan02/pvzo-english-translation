"""Quick regression checks for battle stat and tooltip string translations."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER / "client_tools"))

from translate_battle_ui import _translate_display_string


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


if __name__ == "__main__":
    unittest.main()
