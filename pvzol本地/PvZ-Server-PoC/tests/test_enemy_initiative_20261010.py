"""Enemy initiative wire and combat regressions using a disposable database.

Run with Python -B. PVZ_TEST_SERVER_ROOT may override the local source directory.
Pins original level-table values and checks preview, report and live combat.
"""
import copy
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch


SERVER = Path(os.environ.get(
    'PVZ_TEST_SERVER_ROOT',
    str(Path(__file__).resolve().parents[1]),
))
sys.path.insert(0, str(SERVER))

# Redirect BEFORE importing the server: its guild adapters initialize tables.
import player_store as store

_scratch = tempfile.TemporaryDirectory(prefix='pvz-enemy-initiative-test-')
store.set_db_path(str(Path(_scratch.name) / 'test.db'))
store.init_db()

import pvz_poc_server as game
import guard_local
import npc_lineups


# Frozen independent oracle from original speedPlantLevel.txt (levels 1..300).
# Expected values do not use the helper or the table under test.
ORIGINAL_LEVEL_SPEEDS = (
    50, 100, 150, 200, 250, 300, 350, 400, 450, 500,
    550, 600, 650, 700, 750, 800, 850, 900, 950, 1000,
    1050, 1100, 1150, 1200, 1250, 1300, 1350, 1400, 1450, 1500,
    1550, 1600, 1650, 1700, 1750, 1800, 1850, 1900, 1950, 2000,
    2050, 2100, 2150, 2200, 2250, 2300, 2350, 2400, 2450, 2500,
    2550, 2600, 2650, 2700, 2750, 2800, 2850, 2900, 2950, 3000,
    3050, 3100, 3150, 3200, 3250, 3300, 3350, 3400, 3450, 3500,
    3550, 3600, 3650, 3700, 3750, 3800, 3850, 3900, 3950, 4000,
    4050, 4100, 4150, 4200, 4250, 4300, 4350, 4400, 4450, 4500,
    4550, 4600, 4650, 4700, 4750, 4800, 4850, 4900, 4950, 5000,
    5050, 5100, 5150, 5200, 5250, 5300, 5350, 5400, 5450, 5500,
    5550, 5600, 5650, 5700, 5750, 5800, 5850, 5900, 5950, 6000,
    6050, 6100, 6150, 6200, 6250, 6300, 6350, 6400, 6450, 6500,
    6550, 6600, 6650, 6700, 6750, 6800, 6850, 6900, 6950, 7000,
    7050, 7100, 7150, 7200, 7250, 7300, 7350, 7400, 7450, 7500,
    7550, 7600, 7650, 7700, 7750, 7800, 7850, 7900, 7950, 8000,
    8050, 8100, 8150, 8200, 8250, 8300, 8350, 8400, 8450, 8500,
    8550, 8600, 8650, 8700, 8750, 8800, 8850, 8900, 8950, 9000,
    9050, 9100, 9150, 9200, 9250, 9300, 9350, 9400, 9450, 9500,
    9550, 9600, 9650, 9700, 9750, 9800, 9850, 9900, 9950, 10000,
    10050, 10100, 10150, 10200, 10250, 10300, 10350, 10400, 10450, 10500,
    10550, 10600, 10650, 10700, 10750, 10800, 10850, 10900, 10950, 11000,
    11050, 11100, 11150, 11200, 11250, 11300, 11350, 11400, 11450, 11500,
    11550, 11600, 11650, 11700, 11750, 11800, 11850, 11900, 11950, 12000,
    12050, 12100, 12150, 12200, 12250, 12300, 12350, 12400, 12450, 12500,
    12550, 12600, 12650, 12700, 12750, 12800, 12850, 12900, 12950, 13000,
    13050, 13100, 13150, 13200, 13250, 13300, 13350, 13400, 13450, 13500,
    13550, 13600, 13650, 13700, 13750, 13800, 13850, 13900, 13950, 14000,
    14050, 14100, 14150, 14200, 14250, 14300, 14350, 14400, 14450, 14500,
    14550, 14600, 14650, 14700, 14750, 14800, 14850, 14900, 14950, 15000,
)


def original_level_speed(level):
    return ORIGINAL_LEVEL_SPEEDS[level - 1] if 1 <= level <= 300 else 0


def unit(pos, speed):
    return dict(pos=pos, uid=10000 + pos, card_id=101, level=1, exp=0,
                hp=10000, atk=100, def_=0, ability=0, shield=0,
                skill1=51, skill2=0, is_leader=False, power=321,
                speed=speed)


def total_elements(body, field):
    return [values for raw in game.parse_repeated_submessages(body, field)
            for values in [game.parse_varint_fields(raw)]
            if values.get(1) == 0]


def preview_card(u):
    return game.build_enemy_card(u['pos'], u['card_id'], u['hp'],
                                 u['level'], u['is_leader'],
                                 power=u['power'], speed=u['speed'])


class EnemyInitiativeTests(unittest.TestCase):
    seq = 0

    @classmethod
    def setUpClass(cls):
        game._CLIENTDB_PATH = str(SERVER.parent / 'webroot' / 'clientDB.txt')
        game.load_battle_cards()
        game.load_skill_info()

    def setUp(self):
        type(self).seq += 1
        self.player, _ = store.get_or_create_player(790070000 + type(self).seq)
        self.player.update(level=100, puppet_granted=1,
                           armor_prestige_granted=1, plant_coin_granted=1)
        store.save_player(self.player['uin'], level=100, puppet_granted=1,
                          armor_prestige_granted=1, plant_coin_granted=1)
        self.uid = 99000000 + type(self).seq
        store.add_card(self.player['uin'], self.uid, 1201, level=30)
        self.placements = {3: self.uid}
        store.set_formation(self.player['uin'], 1, self.placements)

    def test_all_three_hundred_authored_levels_match_fixed_original_oracle(self):
        table = game._read_clientdb_table('speedPlantLevel.txt')
        header = {name: index for index, name in enumerate(table[0])}
        authored = {int(row[header['level']]): int(row[header['speed']])
                    for row in table[1:]}
        oracle = dict(enumerate(ORIGINAL_LEVEL_SPEEDS, 1))
        self.assertEqual(authored, oracle)
        for level, expected in oracle.items():
            with self.subTest(level=level):
                self.assertEqual(game.card_level_speed(level), expected)
        for level in (-1, 0, 301, 1000):
            with self.subTest(missing=level):
                self.assertEqual(game.card_level_speed(level), 0)

    def test_card_total_speed_adds_extra_initiative_once(self):
        self.assertEqual(game.card_total_speed({}, 30), 1500)
        self.assertEqual(game.card_total_speed({'refine_speed': 7}, 30), 1507)
        self.assertEqual(game.card_total_speed({'refine_speed': 7, 'speed': 9000}, 30), 1507)
        self.assertEqual(game.card_total_speed({'refine_speed': 0}, 301), 0)

    def test_level_thirty_base_refine_prestige_and_puppet_are_not_double_counted(self):
        initial = game.build_player_units(self.player, self.placements)[0]
        self.assertEqual(initial['speed'], 1500)
        refine = dict(attack=200000, defense=100000, hp=600000, ability=100000)
        store.set_card_baptize(self.player['uin'], self.uid, refine)
        self.assertEqual(game.baptize_speed(refine), 4)
        store.set_card_evolve_level(self.player['uin'], self.uid, 10)
        prestige = int(game.load_plant_evolution()['levels'][10]['speed'])
        self.assertGreater(prestige, 0)

        cfg = game.load_dummy_config()
        info = cfg['zombies'][1000001]
        puppet = dict(exp=0, level=10, star=1, quality=100, spars='',
                      active_skill=info['active'], active_level=3,
                      passive_skill=info['passive'], passive_level=4)
        store.save_puppet(self.player['uin'], 1000001, **puppet)
        game.save_puppet_lineup(self.player, [1000001])
        prop = cfg['props'][(1, 1, 100)]
        puppet_speed = int((prop['speed'] + 10 * prop['g_speed']) * cfg['stars'][1]['buff'] / 10000)
        for kind in ('active', 'passive'):
            skill = cfg['active_skill'][cfg['skills'][puppet[kind + '_skill']]['info']]
            puppet_speed = int(puppet_speed + skill['base_value'] + skill['grow_value'] * puppet[kind + '_level'])
        self.assertGreater(puppet_speed, 0)
        self.assertEqual(game.puppet_deployed_speed(self.player), puppet_speed)

        extra = 4 + prestige + puppet_speed
        expected = 1500 + extra
        stats = game.effective_card_stats(self.player, 1201, 30, uid=self.uid, position=3)
        self.assertEqual(stats['refine_speed'], extra)
        self.assertEqual(game.card_total_speed(stats, 30), expected)
        rows = game._card_power_source_elements(
            self.player, self.uid, 1201, 30, include_deployed=True, position=3)
        elements = {element: (power, speed) for element, power, speed in rows}
        self.assertEqual(elements[0][1], expected)
        self.assertEqual(elements[1][1], 1500)
        self.assertEqual(elements[14][1], 4)
        self.assertEqual(elements[22][1], prestige)
        self.assertEqual(elements[20][1], puppet_speed)
        self.assertEqual(sum(speed or 0 for element, _power, speed in rows if element != 0), expected)
        self.assertEqual(sum(power for element, power, _speed in rows if element != 0), elements[0][0])
        actual = game.build_player_units(self.player, self.placements)[0]
        self.assertEqual(actual['speed'], expected)
        self.assertEqual(actual['power'], elements[0][0])

        formation = game.build_formation_info(self.player, 1, self.placements)
        cardpos = game.parse_repeated_submessages(formation, 1)
        self.assertEqual(len(cardpos), 1)
        self.assert_total(cardpos[0], 6, actual['power'], expected)
        report = game.build_fight_report(
            self.player, 1, 1, 1, won=False, left_units=[actual], right_units=[], puppet_bytes=b'')
        left = game.parse_repeated_submessages(report, 2)[0]
        self.assertEqual(game.parse_varint_fields(left)[15], expected)
        self.assert_total(game.parse_repeated_submessages(left, 2)[0], 18, actual['power'], expected)

    def test_speed_metadata_does_not_change_battle_power(self):
        stats = dict(atk=300, def_=80, hp=600, ability=70)
        self.assertEqual(game.card_power(stats), 400)
        for speed in (0, 50, 1500, 15000):
            altered = dict(stats, speed=speed, refine_speed=37)
            self.assertEqual(game.card_power(altered), 400)
            dto = game.build_enemy_card(4, 101, 600, 30, power=400, speed=speed)
            self.assertEqual(game.parse_varint_fields(dto)[5], 400)
            self.assert_total(dto, 9, 400, speed)

    def assert_base_units(self, units):
        self.assertTrue(units, 'fixture must build a real enemy squad')
        for u in units:
            # Qin summons begin dead/hidden and are absent from the opening preview.
            # Their configured level must not inflate initial side initiative.
            initially_alive = u.get('is_live', True) and u.get('initial_hp', u['hp']) > 0
            expected = original_level_speed(u['level']) if initially_alive else 0
            self.assertEqual(u['speed'], expected, (u['card_id'], u['level']))
            dto = preview_card(u)
            self.assert_total(dto, 9, u['power'], expected)
        report = game.build_fight_report(
            self.player, 1, 1, 1, won=False, left_units=[], right_units=units, puppet_bytes=b'')
        side = game.parse_repeated_submessages(report, 3)[0]
        self.assertEqual(game.parse_varint_fields(side)[15], sum(u['speed'] for u in units))

    def test_all_major_pve_enemy_generators_carry_authored_level_initiative(self):
        builders = [
            ('wiki-adventure', lambda: game._build_wiki_enemies(1, 1)),
            ('fallback-adventure', lambda: game._build_fallback_enemies(3, 4)),
            ('trial-road', lambda: game._tr_enemy_squad(10000, 45)),
            ('island-normal', lambda: game._zi_enemy_units(game._ZI_NORMAL, 3)),
            ('island-advanced', lambda: game._zi_enemy_units(game._ZI_ADV, 3)),
            ('activity-thief', lambda: game._activity_enemy_units(6)),
            ('activity-cart', lambda: game._activity_enemy_units(12)),
            ('activity-sea', lambda: game._activity_enemy_units(17)),
            ('activity-future', lambda: game._activity_enemy_units(22)),
            ('gene-trial', lambda: game._gene_test_enemy_units(self.player, 1, 0, 1)),
            ('endless-wiki', lambda: game._noend_fuben_squad(1)),
            ('endless-fallback', lambda: game._noend_fuben_squad(39)),
            ('plant-test', lambda: game._plant_test_enemies(self.player, 1, 1)),
        ]
        waves = sorted(game.load_dummy_config()['waves'])
        self.assertTrue(waves)
        mode, chapter, stage, wave = waves[0]
        builders.append(('puppet-fuben', lambda: game._puppet_fuben_units(
            self.player, mode, chapter, stage, wave)[1]))
        for boss_id in game._WORLD_BOSS_IDS:
            builders.append(('world-boss-%s' % boss_id,
                             lambda boss_id=boss_id: [game._worldboss_enemy(boss_id, {'hp': 1000000})]))
        builders.append(('world-boss-summons', lambda: game._worldboss_summons(998)))
        for label, build in builders:
            with self.subTest(builder=label):
                self.assert_base_units(build())

    def test_hidden_qin_summons_do_not_inflate_opening_initiative(self):
        summons = game._worldboss_summons(998)
        self.assertEqual(len(summons), 4)
        for u in summons:
            self.assertEqual(u['initial_hp'], 0)
            self.assertFalse(u['is_live'])
            self.assertEqual(u['speed'], 0)
        boss = game._worldboss_enemy(998, {'hp': 1000000})
        self.assertEqual(boss['speed'], 50)
        boss_preview = total_elements(preview_card(boss), 9)[0][3]
        report = game.build_fight_report(
            self.player, 0, 0, 0, won=False, left_units=[],
            right_units=[boss] + summons, puppet_bytes=b'')
        side = game.parse_repeated_submessages(report, 3)[0]
        self.assertEqual(game.parse_varint_fields(side)[15], boss_preview)

    def test_generated_and_saved_arena_npcs_receive_the_same_level_base(self):
        generated = npc_lineups.generate_units(game, 30000, rng=random.Random(42))
        self.assert_base_units(generated)
        bot, _ = store.get_or_create_player(900001)
        bot.update(level=100, puppet_granted=1, armor_prestige_granted=1)
        store.save_player(900001, level=100, puppet_granted=1, armor_prestige_granted=1)
        store.add_card(900001, 99110001, 1201, level=30)
        store.set_formation(900001, 1, {3: 99110001})
        stored = game._arena_opponent_units(900001)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['speed'], 1500)
        self.assert_base_units(stored)

    def test_getfightinfo_propagates_the_units_explicit_speed_instead_of_rederiving_it(self):
        right = [unit(4, 37), unit(10, 0)]
        target = b''.join(game.pb_varint_field(field, value)
                          for field, value in ((1, 1), (2, 1), (3, 1), (4, 1)))
        with patch.object(game, 'preview_enemy_units', return_value=right):
            body = game.handle_battle_getfightinfo(game.pb_bytes(1, target), self.player)
        cards = game.parse_repeated_submessages(body, 4)
        self.assertEqual(len(cards), 2)
        self.assert_total(cards[0], 9, 321, 37)
        self.assert_total(cards[1], 9, 321, 0)
        self.assertEqual(game.parse_varint_fields(body)[5], 642)

    def assert_total(self, dto, field, power, speed):
        rows = total_elements(dto, field)
        self.assertEqual(len(rows), 1, 'each card must contain exactly one total element')
        self.assertEqual(rows[0], {1: 0, 2: power, 3: speed})

    def test_pve_preview_carries_explicit_initiative_and_power(self):
        u = unit(4, 37)
        dto = preview_card(u)
        self.assert_total(dto, 9, 321, 37)
        fields = game.parse_varint_fields(dto)
        self.assertEqual(fields[5], 321)
        self.assertEqual(fields[7], u['hp'])
        self.assertEqual(fields[8], u['hp'])

    def test_explicit_zero_initiative_remains_present_and_zero(self):
        u = unit(4, 0)
        self.assert_total(preview_card(u), 9, 321, 0)
        ordinary = game._enemy_card_dto(4, u['uid'], u['card_id'], 1, u, False)
        self.assert_total(ordinary, 9, 321, 0)
        guarded = guard_local._guard_enemy_card_dto(
            game, 4, u['uid'], u['card_id'], 1, u, False)
        self.assert_total(guarded, 9, 321, 0)

    def test_npc_and_guard_preview_emit_one_total_element(self):
        u = unit(4, 37)
        ordinary = game._enemy_card_dto(4, u['uid'], u['card_id'], 1, u, False)
        guarded = guard_local._guard_enemy_card_dto(
            game, 4, u['uid'], u['card_id'], 1, u, False)
        self.assert_total(ordinary, 9, 321, 37)
        self.assert_total(guarded, 9, 321, 37)

    def test_preview_report_and_live_opening_share_the_same_team_initiative(self):
        # A lower card speed can still win initiative through the team sum.
        for own_speed, enemy_speeds, expected_actor in (
                (100, (60, 60), 4),
                (100, (37, 0), 3),
                (100, (60, 40), 3),
                (0, (0, 0), 3)):
            with self.subTest(own_speed=own_speed, enemy_speeds=enemy_speeds):
                left = [unit(3, own_speed)]
                right = [unit(4, enemy_speeds[0]), unit(10, enemy_speeds[1])]
                preview_rows = [total_elements(preview_card(u), 9) for u in right]
                self.assertTrue(all(len(rows) == 1 for rows in preview_rows))
                preview_speed = sum(rows[0][3] for rows in preview_rows)
                self.assertEqual(preview_speed, sum(enemy_speeds))

                _won, _lp, _rp, rounds = game.run_battle(
                    copy.deepcopy(left), copy.deepcopy(right), max_rounds=1)
                actions = [action for aa, _bb in rounds for action in aa
                           if not action.get('update') and not action.get('leader')
                           and not action.get('puppet')]
                self.assertTrue(actions)
                self.assertEqual(actions[0]['actor'], expected_actor)

                report = game.build_fight_report(
                    {'uin': 799999991, 'level': 1, 'name': 'Initiative regression'},
                    1, 1, 1, won=False, left_units=left, right_units=right,
                    replay_bytes=game.encode_fight_replay(rounds), puppet_bytes=b'')
                right_side = game.parse_repeated_submessages(report, 3)[0]
                report_speed = game.parse_varint_fields(right_side)[15]
                self.assertEqual(report_speed, preview_speed)
                report_rows = [total_elements(raw, 18)
                               for raw in game.parse_repeated_submessages(right_side, 2)]
                self.assertTrue(all(len(rows) == 1 for rows in report_rows))
                self.assertEqual([rows[0][3] for rows in report_rows], list(enemy_speeds))
                self.assertEqual(sum(rows[0][3] for rows in report_rows), report_speed)

                # The encoded normal-turn actor must match actual combat.
                wire_actor = None
                for raw_round in game.parse_repeated_submessages(report, 4):
                    for raw_action in game.parse_repeated_submessages(raw_round, 2):
                        if game.parse_varint_fields(raw_action).get(5, 0) == 1:
                            continue
                        actor = (game.parse_repeated_submessages(raw_action, 1)
                                 or game.parse_repeated_submessages(raw_action, 6))
                        if actor:
                            wire_actor = game.parse_varint_fields(actor[0])[1]
                            break
                    if wire_actor is not None:
                        break
                self.assertEqual(wire_actor, expected_actor)


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        store.close_thread_db()
        _scratch.cleanup()
