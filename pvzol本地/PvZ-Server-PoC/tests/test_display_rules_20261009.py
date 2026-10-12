import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import pvz_poc_server as g
import display_rules_repairs_20261009 as repairs


class DisplayRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = g.store.DB_PATH
        g.store.set_db_path(str(Path(self.tmp.name) / 'test.db'))
        g.store.init_db()
        self.player, _ = g.store.get_or_create_player(981029901)
        self.player.update(level=70, armor_prestige_granted=1, plant_coin_granted=1)
        g.store.save_player(self.player['uin'], level=70, armor_prestige_granted=1, plant_coin_granted=1)
        self.uid = 99001001
        g.store.add_card(self.player['uin'], self.uid, 1201, 30)
        g.load_battle_cards()

    def tearDown(self):
        g.store.set_db_path(self.old)
        self.tmp.cleanup()

    def test_prestige_initiative_reaches_stats_breakdown_and_fight(self):
        g.store.set_formation(self.player['uin'], 1, {3: self.uid})
        initial = g.build_player_units(self.player)[0]['speed']
        g.store.set_card_evolve_level(self.player['uin'], self.uid, 10)
        speed = g.load_plant_evolution()['levels'][10]['speed']
        stats = g.effective_card_stats(self.player, 1201, 30, uid=self.uid)
        self.assertEqual(stats['prestige_speed'], speed)
        rows = g._card_power_source_elements(self.player, self.uid, 1201, 30)
        self.assertEqual(next(s for e, p, s in rows if e == 22), speed)
        self.assertEqual(next(s for e, p, s in rows if e == 0), g.card_level_speed(30) + speed)
        self.assertEqual(g.build_player_units(self.player)[0]['speed'], initial + speed)
        self.assertEqual(sum(p for e, p, s in rows if e != 0), rows[0][1])

    def test_zero_prestige_does_not_add_initiative(self):
        self.assertEqual(repairs.prestige_speed(g, self.player['uin'], self.uid), 0)
        rows = g._card_power_source_elements(self.player, self.uid, 1201, 30)
        self.assertEqual(rows[0][2], g.card_level_speed(30))

    def test_friend_card_fallback_separates_prestige_from_refine(self):
        g.store.set_card_evolve_level(self.player['uin'], self.uid, 10)
        stats = g.effective_card_stats(self.player, 1201, 30, uid=self.uid)
        stats['refine_speed'] += 42
        full = g.build_card_full_info(self.uid, 1201, 30, stats)
        rows = {g.parse_varint_fields(dto)[1]: g.parse_varint_fields(dto)
                for dto in g.parse_repeated_submessages(full, 13)}
        self.assertEqual(rows[22][3], stats['prestige_speed'])
        self.assertEqual(rows[14][3], 42)
        self.assertEqual(rows[22][2], stats['prestige_power'])

    def test_fertilizer_gives_blue_ball_without_changing_experience(self):
        now = int(time.time())
        seedid, seed = next((sid, seed) for sid, seed in g.load_garden_seeds().items()
                            if seed['type'] == g._GARDEN_SEED_TYPE_COLOR)
        for fid in g.load_garden_fertilizers():
            dto = g._encode_garden_pot(seedid, now-100, now, 0, fid, now-100, 1)
            fields = g.parse_varint_fields(dto)
            self.assertEqual(fields[11], 1)
            expected = g._garden_pending_exp(dict(seed, id=seedid), now-100, now, 0, fid, now-100, 1)
            self.assertEqual(fields[9], expected)
        self.assertNotIn(11, g.parse_varint_fields(g._encode_garden_pot(0, 0, now)))

    def test_sunflower_last_talent_uses_130_boundary(self):
        talent = g.load_plant_talents()['talent'][1105]
        self.assertEqual(talent['cond'], '18::130')
        g.store.unlock_plant(self.player['uin'], 1100)
        for tid in (1101, 1102, 1103, 1104):
            g.store.study_talent(self.player['uin'], tid)
        for score, expected in ((30, False), (129, False), (130, True)):
            self.player['arena_data'] = json.dumps({'day': time.strftime('%Y%m%d'), 'score': score,
                                                   'daily_score': max(0, score - 100)})
            g.store.save_player(self.player['uin'], arena_data=self.player['arena_data'])
            with patch.object(g, 'plant_card_power', return_value=999999), patch.object(g, '_quest_observe_talent_progress', return_value=[]):
                g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Talent_Study_CS'](g.pb_varint_field(1, 1105), self.player)
            self.assertEqual(1105 in g.store.get_talents(self.player['uin']), expected)


if __name__ == '__main__':
    unittest.main()
