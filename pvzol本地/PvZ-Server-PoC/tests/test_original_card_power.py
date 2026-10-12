"""Oracle values reconstructed from the original Game/Baptize/fruit ABC code."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g

class OriginalCardPowerTests(unittest.TestCase):
    def test_primary_weights_and_ability_are_original_units(self):
        self.assertEqual(g.card_power({'atk': 300, 'def_': 80, 'hp': 600, 'ability': 70}), 400)
        self.assertEqual(g.card_power({'ability': 1}), 1)
        self.assertEqual(g.card_power({'hp': 60}), 10)
        self.assertEqual(g.card_power({'atk': 60}), 30)

    def test_round_each_rate_and_primary_term_separately(self):
        self.assertEqual(g.card_power(dict(atk=7, def_=11, hp=25, ability=17,
                            hit=3, dodge=5, critical=7, tough=9)), 45)
        for field in ('hit', 'dodge', 'critical', 'tough'):
            self.assertEqual(g.card_power({field: 120}), 60)
            self.assertEqual(g.card_power({field: 1}), 0)

    def test_shield_and_initiative_do_not_add_power(self):
        self.assertEqual(g.card_power({'shield': 9000, 'speed': 1000, 'refine_speed': 300}), 0)
        self.assertEqual(g.card_power({}), 0)
        self.assertEqual(g.card_power({'atk': -2}), 0)

    def test_integer_hp_keeps_precision(self):
        self.assertEqual(g.card_power({'hp': 6*(2**53+1)+5}), 2**53+1)

    def test_primary_configuration_uses_original_hundredths(self):
        self.assertEqual(g._ADV_STAT_SCALE, 100)
        g.load_battle_cards()
        card = g._BATTLE_CARDS[1201]
        at_one = g.card_stats_at_level(1201, 1)
        at_ten = g.card_stats_at_level(1201, 10)
        for field, initial, growth in [('atk','init_atk','grow_atk'), ('def_','init_def','grow_def'),
                                        ('hp','init_hp','grow_hp'), ('ability','init_abi','grow_abi')]:
            self.assertEqual(at_one[field], card[initial]//100)
            self.assertEqual(at_ten[field], (card[initial]+9*card[growth])//100)
        self.assertEqual(at_ten['shield'], (card['init_shield']+9*card['grow_shield'])//80)
        for field in ('hit','dodge','critical','tough'):
            self.assertEqual(at_ten[field], card['init_'+field]+9*card['grow_'+field])

    def test_refine_matches_client_conversion_and_floor_boundaries(self):
        self.assertEqual(g.baptize_power(dict(attack=100, defense=100, hp=100, ability=100)), 2)
        self.assertEqual(g.baptize_power(dict(attack=199, defense=199, hp=599, ability=199)), 2)
        self.assertEqual(g.baptize_power(dict(attack=200, defense=200, hp=600, ability=200)), 6)
        self.assertEqual(g.baptize_power(dict(attack=200000, defense=100000, hp=600000, ability=100000)), 4000)
        self.assertEqual(g.baptize_speed(dict(attack=200000, defense=100000, hp=600000, ability=100000)), 4)

    def test_refine_source_preserves_base_and_additive_totals(self):
        old_db = g.store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            try:
                g.store.set_db_path(str(Path(tmp)/'test.db'))
                g.store.init_db()
                p, _ = g.store.get_or_create_player(88008108)
                uid, cid = 10001201, 1201
                g.store.add_card(p['uin'], uid, cid, 1)
                before = {e:v for e,v,_ in g._card_power_source_elements(p,uid,cid,1)}
                for raw in (100,199,10000,300000):
                    values = dict(attack=raw,defense=raw,hp=raw,ability=raw)
                    g.store.set_card_baptize(p['uin'],uid,values)
                    elements = {e:v for e,v,_ in g._card_power_source_elements(p,uid,cid,1)}
                    stats = g.effective_card_stats(p,cid,1,g._ZERO_STAT_BONUS,uid=uid)
                    self.assertEqual(elements[0],g.card_power(stats))
                    self.assertEqual(elements[14],g.baptize_power(values))
                    self.assertEqual(sum(v for e,v in elements.items() if e),elements[0])
                    self.assertGreaterEqual(elements[1],before[1])
                    self.assertLessEqual(elements[1],before[1]+2)
            finally:
                g.store.set_db_path(old_db)

if __name__ == '__main__':
    unittest.main()
