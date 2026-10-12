"""Card-local power, real deployed power, and safe formation consumers."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as game

store = game.store


class CardPowerConsumersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = store.DB_PATH
        store.set_db_path(str(Path(self.tmp.name) / 'test.db'))
        store.init_db()
        self.player, _ = store.get_or_create_player(991991)
        self.player.update(level=70, plants=[], armor_prestige_granted=1, plant_coin_granted=1)
        store.save_player(self.player['uin'], level=70, armor_prestige_granted=1, plant_coin_granted=1)
        game.load_battle_cards()
        game._FRIEND_CARD_CACHE.clear()
        game._FRIEND_STAT_CACHE.clear()
        self.cid = 6504
        self.uid = 10006504
        store.add_card(self.player['uin'], self.uid, self.cid, 1)
        p = patch.object(game, 'ensure_player_cards')
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        store.set_db_path(self.old_db)
        self.tmp.cleanup()

    def request(self, rows, leader=0):
        cards = b''.join(game.pb_bytes(1, game.pb_varint_field(1, pos)
            + game.pb_int64_field(2, uid)
            + (game.pb_int64_field(3, owner) if owner else b'')) for pos, uid, owner in rows)
        return game.pb_bytes(1, cards + game.pb_varint_field(5, 1) + game.pb_int64_field(3, leader))

    def test_historical_duplicate_and_stale_entries_preserve_legal_position(self):
        store.set_formation(self.player['uin'], 1, {3: self.uid, 7: self.uid, 9: 12345, 99: self.uid})
        self.assertEqual(game.default_formation_placements(self.player), {3: self.uid})
        self.assertEqual(store.get_formation(self.player['uin']), {3: self.uid})
        units = game.build_player_units(self.player)
        self.assertEqual(len(units), 1)
        formation = game.parse_varint_fields(game.build_formation_info(self.player, 1, {3: self.uid}))
        self.assertEqual(formation[4], units[0]['power'])

    def test_seven_historical_owned_cards_are_capped_at_six(self):
        uids = [self.uid]
        for i in range(1, 7):
            uid = self.uid+i
            store.add_card(self.player['uin'], uid, self.cid, 1)
            uids.append(uid)
        original = dict(zip(game._FORMATION_POS, uids))
        store.set_formation(self.player['uin'], 1, original)
        self.assertEqual(game.default_formation_placements(self.player), dict(list(original.items())[:6]))
        self.assertEqual(len(game.build_player_units(self.player)), 6)

    def test_invalid_submissions_keep_previous_selection(self):
        previous = {9: self.uid}
        malformed = [
            [(3, self.uid, 0), (7, self.uid, 0)],
            [(3, self.uid, 0), (3, self.uid, 0)],
            [(99, self.uid, 0)],
            [(3, 12345, 0)],
            [(3, self.uid, 123456)],
        ]
        for rows in malformed:
            with self.subTest(rows=rows):
                store.set_formation(self.player['uin'], 1, previous)
                game.handle_battle_setformation(self.request(rows), self.player)
                self.assertEqual(store.get_formation(self.player['uin']), previous)

    def test_legal_duplicate_species_are_separate_cards(self):
        uid2 = self.uid+1
        store.add_card(self.player['uin'], uid2, self.cid, 1)
        game.handle_battle_setformation(self.request([(9, self.uid, 0), (17, uid2, 0)]), self.player)
        self.assertEqual(game.default_formation_placements(self.player), {9: self.uid, 17: uid2})
        self.assertEqual(len(game.build_player_units(self.player)), 2)

    def test_submitting_seven_owned_cards_keeps_previous_team(self):
        uids = [self.uid]
        for i in range(1, 7):
            uid = self.uid+i
            store.add_card(self.player['uin'], uid, self.cid, 1)
            uids.append(uid)
        previous = {9: self.uid}
        store.set_formation(self.player['uin'], 1, previous)
        game.handle_battle_setformation(self.request(
            [(pos, uid, 0) for pos, uid in zip(game._FORMATION_POS, uids)]), self.player)
        self.assertEqual(store.get_formation(self.player['uin']), previous)

    def test_home_cleanup_and_save_preserve_adventure_friend_owner(self):
        friend_uid, owner = 4006101, 4
        with patch.object(game, 'borrowed_friend', return_value={'friend_role_id': owner}):
            store.set_formation(self.player['uin'], 1, {3: self.uid, 7: self.uid, 17: friend_uid}, {17: owner})
            self.assertEqual(game.default_formation_placements(self.player), {3: self.uid})
            self.assertEqual(store.get_formation_with_owner(self.player['uin']),
                             {3: (self.uid, 0), 17: (friend_uid, owner)})
            game.handle_battle_setformation(self.request([(9, self.uid, 0)]), self.player)
            self.assertEqual(store.get_formation_with_owner(self.player['uin']),
                             {9: (self.uid, 0), 17: (friend_uid, owner)})

    def test_collection_power_includes_uid_progression_and_matches_package(self):
        before = game._player_fight_power(self.player)
        store.set_card_baptize(self.player['uin'], self.uid,
                               {'attack': 10000, 'hp': 10000, 'ability': 10000})
        after = game._player_fight_power(self.player)
        self.assertGreater(after, before)
        package = game.handle_card_getpackage(b'', self.player)
        cards = game.parse_repeated_submessages(package, 1)
        self.assertEqual(after, sum(game.parse_varint_fields(card)[2] for card in cards))
        with patch.object(game, 'deployed_stat_bonus', return_value={'atk': 99999, 'def_': 0, 'hp': 0}):
            self.assertEqual(game._player_fight_power(self.player), after)

    def test_real_deployment_bonuses_do_not_inflate_own_or_other_collection_rank(self):
        lab_id, lab_level = next((lid, lv) for lid, bottle in game.load_lab().items()
            for lv, row in bottle['levels'].items() if row.get('stat') and row['stat'][1] > 0)
        other, _ = store.get_or_create_player(991993)
        other.update(level=70, armor_prestige_granted=1, plant_coin_granted=1)
        store.add_card(other['uin'], self.uid, self.cid, 1)
        for owner in (self.player, other):
            with self.subTest(owner=owner['uin']):
                store.add_card(owner['uin'], self.uid+1, self.cid, 1)
                store.set_card_baptize(owner['uin'], self.uid, {'attack': 10000, 'ability': 10000})
                owner.update(wt_level=20, vip_level=3, vip_card_id=0)
                store.save_player(owner['uin'], level=70, wt_level=20, vip_level=3, vip_card_id=0,
                                  armor_prestige_granted=1, plant_coin_granted=1)
                game._lab_save_bottle(owner, lab_id, lab_level, game._LAB_STATUS_HAVEPRODUCE)
                for source in (game.lab_stat_bonus, game.wt_stat_bonus, game.vip_stat_bonus):
                    self.assertGreater(sum(source(owner).values()), 0)
                package = game.handle_card_getpackage(b'', owner)
                collection = sum(game.parse_varint_fields(card)[2]
                    for card in game.parse_repeated_submessages(package, 1))
                self.assertEqual(game._player_fight_power(owner), collection)
                self.assertEqual(game._rank_player_power(owner), collection)
                row = game._rank_row_for_cards(owner['uin'], owner['role_id'], owner['name'], 70, {})
                self.assertEqual(row['power'], collection)
                store.set_formation(owner['uin'], 1, {9: self.uid})
                local = game.effective_card_stats(owner, self.cid, 1, game._ZERO_STAT_BONUS, uid=self.uid)
                deployed = game.effective_card_stats(owner, self.cid, 1, uid=self.uid, position=9)
                bonus = game.deployed_stat_bonus(owner)
                for field in ('atk', 'def_', 'hp', 'ability'):
                    self.assertEqual(deployed[field] - local[field], bonus[field])
                self.assertGreater(game._player_formation_power(owner), game.card_power(local))

    def test_formation_power_matches_position_nutrition_preview_and_report(self):
        store.set_formation(self.player['uin'], 1, {9: self.uid})
        baseline = game._player_formation_power(self.player)
        store.add_reagent(self.player['uin'], 9001, 60101, position=9, slot_id=1)
        units = game.build_player_units(self.player)
        expected = sum(u['power'] for u in units)
        self.assertGreater(expected, baseline)
        self.assertEqual(game._player_formation_power(self.player), expected)
        formation = game.parse_varint_fields(game.build_formation_info(self.player, 1, {9: self.uid}))
        self.assertEqual(formation[4], expected)
        report = game.build_fight_report(self.player, 1, 1, 1, left_units=units, right_units=[])
        side = game.parse_repeated_submessages(report, 2)[0]
        self.assertEqual(game.parse_varint_fields(side)[7], expected)
        store.set_reagent_pos(self.player['uin'], 9001, 3, 1)
        self.assertEqual(game._player_formation_power(self.player), baseline)

    def test_sync_armor_amplification_uses_actual_role_level(self):
        self.player['level'] = 1
        store.save_player(self.player['uin'], level=1)
        armor = game.load_plant_armor()
        row = max((r for r in armor['increase'] if r['role'] > 1), key=lambda r: r['percent'])
        item_id = next(iid for iid, info in armor['crystals'].items()
                       if info['level'] == row['gem_level'] and info['props'])
        store.add_item(self.player['uin'], item_id, row['need'])
        store.set_card_armor(self.player['uin'], self.uid, 1, 0, 0, 1, 0)
        store.set_card_armor_gem(self.player['uin'], self.uid, 1, 1, item_id)
        self.assertNotEqual(game._armor_bonus(self.player['uin'], self.uid, self.cid, 1),
                            game._armor_bonus(self.player['uin'], self.uid, self.cid, 999))
        sync = game.build_card_sync_sc(self.player['uin'], self.uid, self.cid, 0)[1]
        full = game.parse_repeated_submessages(sync, 1)[0]
        expected = game.effective_card_stats(self.player, self.cid, 1, game._ZERO_STAT_BONUS, uid=self.uid)
        self.assertEqual(game.parse_varint_fields(full)[2], game.card_power(expected))
        total = next(game.parse_varint_fields(r)[2]
                     for r in game.parse_repeated_submessages(full, 13)
                     if game.parse_varint_fields(r)[1] == 0)
        self.assertEqual(total, game.parse_varint_fields(full)[2])

    def test_friend_uses_selected_copy_and_same_species_captain_switch_is_immediate(self):
        owner, _ = store.get_or_create_player(991992)
        first, second = 10007001, 10007002
        for uid in (first, second):
            store.add_card(owner['uin'], uid, self.cid, 1)
        store.set_card_baptize(owner['uin'], second, {'attack': 10000, 'hp': 10000})
        store.set_formation(owner['uin'], 1, {9: first, 17: second})
        store.set_formation_leader(owner['uin'], 1, first)
        friend = {'friend_role_id': owner['role_id']}
        borrowed_uid, cid, level, first_stats = game._friend_help_card(friend)
        expected_first = game.effective_card_stats(owner, self.cid, 1, game._ZERO_STAT_BONUS, uid=first)
        self.assertEqual(first_stats, expected_first)
        store.set_formation_leader(owner['uin'], 1, second)
        switched_uid, cid, level, second_stats = game._friend_help_card(friend)
        expected_second = game.effective_card_stats(owner, self.cid, 1, game._ZERO_STAT_BONUS, uid=second)
        self.assertEqual(second_stats, expected_second)
        self.assertEqual(switched_uid, borrowed_uid)
        self.assertGreater(game.card_power(second_stats), game.card_power(first_stats))
        cached_source_uids = {key[1] for key in game._FRIEND_STAT_CACHE if key[0] == owner['uin']}
        self.assertEqual(cached_source_uids, {first, second})
        # Preview and battle must carry that same selected card after a cache hit.
        placements = {9: self.uid, 17: borrowed_uid}
        owners = {17: owner['role_id']}
        with patch.object(game, 'borrowed_friend', return_value=friend):
            units = game.build_player_units(self.player, placements, owners)
            formation = game.build_formation_info_fuben(self.player, 1, placements, owners)
        self.assertEqual(game.parse_varint_fields(formation)[4], sum(u['power'] for u in units))
        borrowed = next(u for u in units if u['is_help_card'])
        bonus = game.deployed_stat_bonus(self.player)
        deployed = dict(expected_second)
        for field, value in bonus.items():
            deployed[field] = deployed.get(field, 0) + value
        self.assertEqual(borrowed['power'], game.card_power(deployed))

    def test_friend_no_captain_fallback_uses_highest_level_copy_progression(self):
        owner, _ = store.get_or_create_player(991992)
        first, selected = 10007001, 10007002
        store.add_card(owner['uin'], first, self.cid, 1)
        store.add_card(owner['uin'], selected, self.cid, 2)
        store.set_card_baptize(owner['uin'], first, {'attack': 999999})
        store.set_card_baptize(owner['uin'], selected, {'attack': 10000})
        borrowed_uid, cid, level, actual = game._friend_help_card({'friend_role_id': owner['role_id']})
        self.assertEqual((cid, level), (self.cid, 2))
        expected = game.effective_card_stats(owner, self.cid, 2, game._ZERO_STAT_BONUS, uid=selected)
        self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
