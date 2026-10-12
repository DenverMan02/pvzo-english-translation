import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import admin_server as admin


class CatalogSearchLabelsTests(unittest.TestCase):
    def matches(self, needle):
        return {item["id"] for item in admin.ITEM_CATALOG
                if needle.casefold() in item["search"]}

    def test_bag_label_finds_every_stack_catalog_entry(self):
        stacks = {item["id"] for item in admin.ITEM_CATALOG if item["kind"] == "stack"}
        self.assertTrue(stacks)
        self.assertLessEqual(stacks, self.matches("背包道具"))

    def test_plant_card_label_includes_all_real_card_entries(self):
        cards = {item["id"] for item in admin.ITEM_CATALOG if item["kind"] == "card"}
        self.assertTrue(cards)
        # Descriptions may legitimately match the same phrase for other types;
        # require all real cards without treating those extra matches as bugs.
        self.assertLessEqual(cards, self.matches("植物卡"))

    def test_invalid_historical_puzzle_does_not_become_grantable(self):
        self.assertNotIn(67813, admin.ITEM_CATALOG_BY_ID)
        self.assertNotIn(67813, self.matches("67813"))
        self.assertIn(67801, self.matches("红色拼图八"))


if __name__ == "__main__":
    unittest.main()
