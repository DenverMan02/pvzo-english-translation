"""冒险 (Cmd_TD_ReportLevelResult_CS) must have a chance to drop 星魄 (2121).

clientDB ItemInfo 2121: "植物觉醒的必备材料。可通过冒险第12章及以后章节精英难度掉落", and
fuben.txt encodes that as `2121:2:2000` in bonusOptional — the 冒险副本 path has always rolled it.
The MAIN-LINE adventure levels carry no bonusItems at all, so 星魄 simply never appeared while
playing them; this pins the probabilistic drop added on top of the (already deterministic) Lab
reagent roll.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_star_soul_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

STAR_SOUL = 2121
KEY = (1, 1, 1)                       # an authored story level


def item_updates(messages):
    result = {}
    for name, body in messages:
        if name != "PVZ.Cmd.Cmd_Item_Update_SC":
            continue
        for dto in srv.parse_repeated_submessages(body, 1):
            fields = srv.parse_varint_fields(dto)
            result[fields.get(2, 0)] = fields.get(3, 0)
    return result


class AdventureStarSoulTest(unittest.TestCase):
    def test_tutorial_and_unknown_levels_never_drop_it(self):
        self.assertEqual(srv.td_star_soul_reward(0, 1, 1), [])
        self.assertEqual(srv.td_star_soul_reward(999, 1, 1), [])
        self.assertEqual(srv.td_star_soul_reward(1, 1, 0), [])

    def test_drop_matches_the_authored_fuben_rate(self):
        # fuben.txt: `2121:2:2000` -> 2 per drop out of 10000.
        self.assertEqual(srv._TD_STAR_SOUL_ITEM, STAR_SOUL)
        self.assertEqual(srv._TD_STAR_SOUL_AMOUNT, 2)
        self.assertEqual(srv._TD_STAR_SOUL_MAX_CHANCE, 2000)
        self.assertIn(KEY, srv.load_level_rewards())
        with patch.object(srv.random, "randint", return_value=1):
            self.assertEqual(srv.td_star_soul_reward(*KEY), [(STAR_SOUL, 2)])
        with patch.object(srv.random, "randint", return_value=10000):
            self.assertEqual(srv.td_star_soul_reward(*KEY), [])

    def test_chance_rises_with_story_progress_and_then_caps(self):
        authored = sorted({k[0] for k in srv.load_level_rewards() if k[0] > 0})
        self.assertTrue(authored)
        for stage in authored:
            key = next(k for k in srv.load_level_rewards() if k[0] == stage)
            expected = min(srv._TD_STAR_SOUL_MAX_CHANCE,
                           srv._TD_STAR_SOUL_CHANCE_PER_STAGE * stage)
            with patch.object(srv.random, "randint", return_value=expected):
                self.assertEqual(srv.td_star_soul_reward(*key), [(STAR_SOUL, 2)], key)
            with patch.object(srv.random, "randint", return_value=expected + 1):
                self.assertEqual(srv.td_star_soul_reward(*key), [], key)

    def test_win_grants_it_and_syncs_the_bag(self):
        uin = 700002121
        player, _created = store.get_or_create_player(uin)
        player["level"], player["exp"] = 100, 0
        before = store.get_item_count(uin, STAR_SOUL)
        request = (srv.pb_varint_field(1, KEY[0]) + srv.pb_varint_field(2, KEY[1])
                   + srv.pb_varint_field(3, KEY[2]) + srv.pb_varint_field(4, 1))
        with (patch.object(srv, "_quest_sync_messages", return_value=[]),
              patch.object(srv, "_quest_record_condition", return_value=[]),
              patch.object(srv.main_quest_local, "record_td_clear", return_value=[]),
              patch.object(srv.random, "randint", return_value=1)):
            messages = srv.handle_td_report_result(request, player)
        self.assertEqual(store.get_item_count(uin, STAR_SOUL), before + 2)
        self.assertEqual(item_updates(messages)[STAR_SOUL], before + 2)

    def test_a_lost_level_drops_nothing(self):
        uin = 700002122
        player, _created = store.get_or_create_player(uin)
        before = store.get_item_count(uin, STAR_SOUL)
        request = (srv.pb_varint_field(1, KEY[0]) + srv.pb_varint_field(2, KEY[1])
                   + srv.pb_varint_field(3, KEY[2]) + srv.pb_varint_field(4, 0))
        with patch.object(srv.random, "randint", return_value=1):
            messages = srv.handle_td_report_result(request, player)
        self.assertEqual(store.get_item_count(uin, STAR_SOUL), before)
        self.assertFalse(any(name == "PVZ.Cmd.Cmd_Item_Update_SC" for name, _b in messages))


if __name__ == "__main__":
    unittest.main()
