"""Archived roster integration against disposable accounts, never real saves."""
from contextlib import ExitStack
from pathlib import Path
import atexit, copy, json, unittest
from unittest.mock import patch
from worldmap_test_support import IsolatedServer, fields, many, one, message

qa = IsolatedServer()
atexit.register(qa.close)
game = qa.game
archive = game.adventure_archive_formations


class ArchivedRosterTests(unittest.TestCase):
    def setUp(self):
        self.document = {'schema_version': 1, 'formations': [
            {'chapter': 1, 'mode': 2, 'stage': 1, 'source_group_id': 'qa-source',
             'mapping_status': 'direct', 'units': [
                 {'pos': 18, 'card_id': 111, 'level': 6, 'stats': {'power': 505}},
                 {'pos': 4, 'card_id': 121, 'level': 2, 'stats': {'power': 385}},
             ]}]}

    def test_rejects_duplicate_stage_reserved_slot_and_normal_mode(self):
        for change in ('duplicate', 'slot', 'mode'):
            document = copy.deepcopy(self.document)
            if change == 'duplicate':
                document['formations'] *= 2
            elif change == 'slot':
                document['formations'][0]['units'][0]['pos'] = 2
            else:
                document['formations'][0]['mode'] = 1
            with self.subTest(change=change), self.assertRaises(ValueError):
                archive.validate_data(document)

    def test_screenshot_power_level_slots_and_species_are_preserved(self):
        with patch.object(archive, 'FORMATIONS', archive.validate_data(self.document)):
            units = archive.build_units(game, 1, 2, 1)
        self.assertEqual([(u['pos'],u['card_id'],u['level'],u['power']) for u in units],
                         [(18,111,6,505),(4,121,2,385)])
        self.assertEqual(units[0]['speed'],game.card_level_speed(6))
        self.assertTrue(all(u['hp'] > 0 and u['atk'] > 0 for u in units))

    def test_original_numeric_hp_and_shield_are_independent(self):
        self.document['formations'][0]['units'][0]['stats'].update(
            {'hp': 3500, 'shield': 200, 'atk': 180, 'def_': 75, 'speed': 27})
        with patch.object(archive, 'FORMATIONS', archive.validate_data(self.document)):
            unit = archive.build_units(game, 1, 2, 1)[0]
        self.assertEqual([unit[k] for k in ('hp','shield','atk','def_','speed')],
                         [3500,200,180,75,27])

    def test_uncovered_and_normal_stages_keep_existing_builder(self):
        self.assertIsNone(archive.get_formation(1,1,1))
        self.assertEqual(game._build_adventure_enemies(1,1,1),game._build_wiki_enemies(1,1,1))
        self.assertEqual(game._build_adventure_enemies(32,3,999),game._build_fallback_enemies(32,999,3))

    def test_every_deployed_roster_uses_configured_stage_and_native_zombie(self):
        self.assertTrue(archive.FORMATIONS, 'Release must contain authored formations')
        configured = game.load_fuben_stages()
        for (chapter,mode,stage), roster in archive.FORMATIONS.items():
            with self.subTest(chapter=chapter,mode=mode,stage=stage):
                self.assertIn(stage, configured[(chapter,mode)])
                units = game._build_adventure_enemies(chapter,mode,stage)
                self.assertEqual([(u['pos'],u['card_id']) for u in units],
                                 [(u['pos'],u['card_id']) for u in roster['units']])
                self.assertEqual(units,game._build_adventure_enemies(chapter,mode,stage))
                self.assertTrue(all(game._BATTLE_CARDS[u['card_id']]['type'] != 2 for u in units))

    def test_all_archived_formations_execute_real_engine_and_encode_replay(self):
        player=qa.player(8811001,level=200,role_created=1)
        game.rebuild_td_roster(player)
        game.ensure_player_cards(player)
        qa.train_owned_cards(player,100)
        left=game.build_player_units(player)
        self.assertTrue(left)
        for key in archive.FORMATIONS:
            with self.subTest(stage=key):
                right=game._build_adventure_enemies(*key)
                won,lp,rp,rounds=game.run_battle(copy.deepcopy(left),right,
                    **archive.combat_options(*key))
                self.assertIsInstance(won,bool)
                self.assertGreater(rp,0)
                self.assertTrue(game.encode_fight_replay(rounds))
                self.assertTrue(all('final_hp' in unit for unit in right))

    def test_only_observed_round_limit_and_captain_badge_change_combat_options(self):
        with patch.object(archive,'FORMATIONS',archive.validate_data(self.document)):
            self.assertEqual(archive.combat_options(1,2,1),
                             {'leader_sides':('left',),'stop_on_elimination':True})
        self.document['formations'][0].update(
            {'max_rounds':30,'enemy_captain_badge_observed':True})
        with patch.object(archive,'FORMATIONS',archive.validate_data(self.document)):
            self.assertEqual(archive.combat_options(1,2,1),
                {'leader_sides':('left','right'),'stop_on_elimination':True,'max_rounds':30})
            self.assertEqual(archive.combat_options(1,1,1),
                             {'leader_sides':('left',),'stop_on_elimination':True})

    def test_actual_preview_and_fight_keep_source_count_slots_and_attributes(self):
        player=qa.player(8811002,level=100,energy=100)
        for stage in game.load_fuben_stages()[(1,1)]:
            game.store.pass_fuben_stage(player['uin'],1,1,stage,3)
        target=game.pb_bytes(1, game.pb_varint_field(1,1)+game.pb_varint_field(2,1)
                +game.pb_varint_field(3,2)+game.pb_varint_field(4,1))
        captured=[]
        def execute(left,right,**kwargs):
            captured.extend(copy.deepcopy(right))
            return False,0,sum(u['power'] for u in right),[]
        with patch.object(archive,'FORMATIONS',archive.validate_data(self.document)):
            expected=game._build_adventure_enemies(1,2,1)
            preview=message(qa.invoke('Battle_GetFightInfo',target,player),'Battle_GetFightInfo')
            # A stale cached roster must never turn a restored source formation
            # into a different enemy species while retaining source attributes.
            game.remember_battle_preview(player,[{'pos':4,'card_id':191}], 'fuben','1/2/1')
            with ExitStack() as stack:
                for name,value in (('_fuben_formation_entries',({},{})),
                    ('build_formation_info_fuben',b''),('build_player_units',[]),
                    ('_auto_puppets',[]),('_auto_puppet_list',b''),
                    ('build_fight_report',b''),('encode_fight_replay',b'')):
                    stack.enter_context(patch.object(game,name,return_value=value))
                stack.enter_context(patch.object(game,'run_battle',side_effect=execute))
                fight=qa.invoke('Battle_Fight',target,player)
        decoded=[fields(raw) for raw in many(preview,4)]
        self.assertEqual([(d[1][0][1],d[2][0][1]) for d in decoded],
                         [(u['pos'],u['card_id']) for u in expected])
        self.assertEqual(captured,expected)
        self.assertIn('PVZ.Cmd.Cmd_Battle_Fight_SC',dict(fight))

    def test_alliance_real_dispatcher_preparation_and_fight_share_full_roster(self):
        player=qa.player(8811003,level=200,energy=100,role_created=1)
        for chapter in range(1,12):
            for stage in game.load_fuben_stages()[(chapter,1)]:
                game.store.pass_fuben_stage(player['uin'],chapter,1,stage,3)
        key=(12,3,1)
        expected=game._build_adventure_enemies(*key)
        self.assertIsNotNone(archive.get_formation(*key))
        target=game.pb_bytes(1,game.pb_varint_field(1,1)+game.pb_varint_field(2,key[0])
                             +game.pb_varint_field(3,key[1])+game.pb_varint_field(4,key[2]))
        preview=message(qa.invoke('Battle_GetFightInfo',target,player),'Battle_GetFightInfo')
        self.assertEqual([(one(raw,1),one(raw,2)) for raw in many(preview,4)],
                         [(u['pos'],u['card_id']) for u in expected])
        captured=[]
        def execute(left,right,**kwargs):
            captured.extend(copy.deepcopy(right))
            return False,0,sum(u['power'] for u in right),[]
        with patch.object(game,'run_battle',side_effect=execute),\
             patch.object(game,'build_fight_report',return_value=b''),\
             patch.object(game,'encode_fight_replay',return_value=b''):
            fight=qa.invoke('Battle_Fight',target,player)
        self.assertIn('PVZ.Cmd.Cmd_Battle_Fight_SC',dict(fight))
        self.assertEqual(captured,expected)


if __name__ == '__main__':
    unittest.main()
