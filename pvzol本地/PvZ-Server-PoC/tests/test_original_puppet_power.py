"""Independent original-client vectors for puppet battle power.

Source: Game.swf DummyZombieInfo methods 39160, 39165, 39166, 39168;
DummyEquipmentInfo methods 39105/39106; GameGloble method 861.
Expected values include the original raw-primary flag, eight attributes,
sixfold attribute factors and star application only to the puppet body.
"""
import importlib.util
import os
from pathlib import Path
import sys
import unittest


SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
SOURCE = os.environ.get("PVZ_COMBAT_SOURCE", str(SERVER / "pvz_poc_server.py"))
spec = importlib.util.spec_from_file_location("puppet_power_under_test", SOURCE)
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
s._CLIENTDB_PATH = str(SERVER.parent / "webroot" / "clientDB.txt")

FIELDS = ("atk", "def", "hp", "ab", "hit", "dodge", "critical", "tough")
VECTORS = (
    (1000001, {"level": 1, "star": 1, "quality": 100, "spars": ""},
     (145764, 0, 1311840, 0, 276, 456, 276, 456), 3647),
    (1000001, {"level": 1, "star": 1, "quality": 100,
               "spars": "1:1:1;2:1:1;3:1:1;4:1:1;5:1:1;6:1:1"},
     (249864, 52050, 4955340, 52050, 2364, 456, 276, 3930), 14062),
    (1000001, {"level": 10, "star": 2, "quality": 100,
               "spars": "1:4:2;2:4:2;3:4:2;4:4:2;5:4:2;6:4:2"},
     (483144, 68550, 7912662, 68550, 3426, 1110, 654, 5634), 22386),
    (2000001, {"level": 10, "star": 3, "quality": 300,
               "spars": "1:4:2;2:4:2;3:4:2;4:4:2;5:4:2;6:4:2"},
     (1100226, 68550, 13466952, 68550, 4596, 3036, 1824, 7560), 37825),
)


class OriginalPuppetPowerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = s.load_dummy_config()

    def test_real_original_power_vectors(self):
        for base_id, row, _, expected in VECTORS:
            with self.subTest(base_id=base_id, row=row):
                self.assertEqual(s.puppet_power(base_id, row), expected)

    def test_all_eight_raw_attributes_match_original_vectors(self):
        for base_id, row, expected, _ in VECTORS:
            with self.subTest(base_id=base_id, row=row):
                raw = s._puppet_power_raw_attributes(base_id, row)
                self.assertEqual(tuple(raw[field] for field in FIELDS), expected)

    def test_loader_keeps_body_ability_and_secondary_growth(self):
        self.assertEqual(len(self.cfg["props"]), 840)
        self.assertEqual(self.cfg["props"][(1, 1, 100)]["atk"], 22187)
        head = self.cfg["equip"][(1, 1)]
        self.assertEqual((head["hit"], head["g_hit"], head["tough"], head["g_tough"]),
                         (55, 3, 83, 4))
        self.assertEqual(self.cfg["equip"][(2, 1)]["ab"], 5533)

    def test_empty_slots_add_no_quality_one_equipment(self):
        _, row, raw, expected = VECTORS[0]
        empty = dict(row, spars="1:0:0;2:0:0;3:0:0;4:0:0;5:0:0;6:0:0")
        self.assertEqual(s.puppet_power(1000001, empty), expected)
        self.assertEqual(tuple(s._puppet_power_raw_attributes(1000001, empty)[f] for f in FIELDS), raw)

    def test_equipment_growth_caps_at_authored_quality_limit(self):
        row = {"level": 1, "star": 1, "quality": 100, "spars": "1:10:1"}
        maximum = s.puppet_power(1000001, row)
        self.assertEqual(s.puppet_power(1000001, dict(row, spars="1:999:1")), maximum)

    def test_star_does_not_amplify_equipment(self):
        _, row, _, _ = VECTORS[1]
        first = s._puppet_power_raw_attributes(1000001, row)
        starred = s._puppet_power_raw_attributes(1000001, dict(row, star=2))
        body = s._puppet_power_raw_attributes(1000001, dict(row, spars=""))
        starred_body = s._puppet_power_raw_attributes(1000001, dict(row, star=2, spars=""))
        self.assertEqual([starred[f] - first[f] for f in FIELDS],
                         [starred_body[f] - body[f] for f in FIELDS])

    def test_missing_body_template_returns_zero(self):
        _, row, _, _ = VECTORS[1]
        self.assertEqual(s.puppet_power(1000001, dict(row, quality=999999)), 0)
        self.assertEqual(s.puppet_power(9999999, row), 0)


if __name__ == "__main__":
    unittest.main()
