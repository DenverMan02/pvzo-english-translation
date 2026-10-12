"""Match the original ModuleCardDNA percentage display to combat additions.

CardDNAModel.reset (method 110) decodes primary base values in hundredths,
secondary base values in basis points, and every percentage in basis points.
Factors are disabled here to isolate the persisted gene-choice contribution.
"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
SOURCE = os.environ.get("PVZ_COMBAT_SOURCE", str(SERVER / "pvz_poc_server.py"))
spec = importlib.util.spec_from_file_location("gene_units_under_test", SOURCE)
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
s._CLIENTDB_PATH = str(SERVER.parent / "webroot" / "clientDB.txt")


class GenePercentageUnitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.load_battle_cards()
        cls.dna = s.load_card_dna()
        # Use real authored paths, including all four secondary attributes.
        cls.paths = {}
        for (path_id, level), info in cls.dna["choose"].items():
            attr = info["type_name"]
            if level == 1 and info["total"] > 0 and attr in s._DNA_ATTR_STATS:
                cls.paths.setdefault(attr, path_id)

    def percentage_payload(self, rarity):
        card_id = next(cid for cid, card in s._BATTLE_CARDS.items()
                       if card["rarity"] == rarity and card["fruitType"] != 0)
        uid, uin, level = 80801, 80802, 300
        card = (uid, card_id, level, 0)
        stats = s.card_stats_at_level(card_id, level)
        # Authored cards often have only one nonzero secondary stat. Make all
        # four nonzero here so no conversion assertion passes trivially at 0.
        stats.update(hit=113, dodge=137, critical=173, tough=197)
        genes = {stage: {"factor_level": 0, "factor_exp": 0,
                         "attribute": path_id, "level": 1}
                 for stage, path_id in enumerate(self.paths.values(), 1)}
        with patch.object(s.store, "get_card_genes", return_value=genes), \
                patch.object(s, "card_stats_at_level", return_value=stats):
            raw = s._dna_gene_info_body(uid, card, genes)
            combat = s._card_gene_stat_bonus(uin, uid, card_id, level)
        attribute = s.parse_repeated_submessages(raw, 3)[0]
        base = s.parse_varint_fields(s.parse_repeated_submessages(attribute, 1)[0])
        percent = s.parse_varint_fields(s.parse_repeated_submessages(attribute, 2)[0])
        return card_id, stats, base, percent, combat

    def test_percentage_wire_keeps_authored_basis_points_for_all_rarities(self):
        self.assertEqual(set(self.paths), set(s._DNA_ATTR_STATS))
        for rarity in range(1, 6):
            card_id, _, _, percent, _ = self.percentage_payload(rarity)
            scale = s._dna_scale(s._BATTLE_CARDS[card_id])
            for attr, path_id in self.paths.items():
                with self.subTest(rarity=rarity, attr=attr):
                    authored = self.dna["choose"][(path_id, 1)]["total"]
                    self.assertEqual(percent[attr], int(authored * scale))

    def test_primary_display_percentage_matches_combat(self):
        for rarity in (1, 5):
            _, stats, base, percent, combat = self.percentage_payload(rarity)
            for attr in (1, 2, 3, 5):
                with self.subTest(rarity=rarity, attr=attr):
                    key = s._DNA_ATTR_STATS[attr]
                    self.assertEqual(base[attr], stats[key] * 100)
                    # Original AS3: int(base * 0.01 * percent * 0.0001).
                    displayed = int(base[attr] * 0.01 * percent[attr] * 0.0001)
                    self.assertEqual(displayed, combat[key])

    def test_secondary_display_percentage_matches_combat(self):
        for rarity in (1, 5):
            _, stats, base, percent, combat = self.percentage_payload(rarity)
            for attr in (6, 7, 8, 9):
                with self.subTest(rarity=rarity, attr=attr):
                    key = s._DNA_ATTR_STATS[attr]
                    self.assertEqual(base[attr], stats[key])
                    # Original AS3: int(base * percent * 0.0001).
                    displayed = int(base[attr] * percent[attr] * 0.0001)
                    self.assertEqual(displayed, combat[key])

    def test_no_chosen_paths_has_two_terms_and_zero_percentages(self):
        raw = s._dna_gene_info_body(80801, (80801, 1215, 300, 0), {})
        attribute = s.parse_repeated_submessages(raw, 3)[0]
        self.assertEqual(len(s.parse_repeated_submessages(attribute, 1)), 1)
        percent = s.parse_varint_fields(s.parse_repeated_submessages(attribute, 2)[0])
        self.assertEqual(percent, {attr: 0 for attr in s._DNA_ATTR_STATS})


if __name__ == "__main__":
    unittest.main()
