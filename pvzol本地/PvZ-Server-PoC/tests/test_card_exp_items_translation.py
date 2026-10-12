"""Plant-EXP beans must still resolve after ItemInfo names/descs are translated."""

import unittest

import pvz_poc_server as server


def _row(*values):
    return list(values)


HEADER = [
    "id", "name", "desc", "icon", "minLvRequired", "type", "subType",
    "quality", "stackNum", "isUse", "isMulUse", "isCanSell", "sellPrice",
    "isCanShow", "cardItemId", "gainWay", "useEffect", "mainType",
    "isAddict", "needPassword",
]


class CardExpItemsTranslationTests(unittest.TestCase):
    def setUp(self):
        self.previous = server._CARD_EXP_ITEMS
        server._CARD_EXP_ITEMS = None

    def tearDown(self):
        server._CARD_EXP_ITEMS = self.previous

    def test_use_effect_survives_english_iteminfo(self):
        rows = [
            HEADER,
            _row(2002, "Small Experience Bean", "Increases plant EXP by 10,000",
                 2002, 1, 6, 601, 2, 99999, 1, 0, 0, 0, 0, "", "", "1:10000", 3, 1, 0),
            _row(2003, "Experience Bean", "Increases Plant EXP by 50,000",
                 2003, 1, 6, 602, 3, 99999, 1, 0, 0, 0, 0, "", "", "1:50000", 3, 1, 0),
            _row(2004, "Luring Experience Bean", "Grants 200,000 Plant EXP",
                 2004, 1, 6, 603, 4, 99999, 1, 0, 0, 0, 0, "", "", "1:200000", 3, 1, 0),
            _row(3011, "Boxing Champ Cabbage", "costume",
                 3011, 1, 2, 205, 4, 1, 0, 0, 0, 0, 0, "", "", "1:1801", 0, 0, 0),
            _row(98904, "Alloy Element", "armor exp 500",
                 98904, 1, 13, 261, 2, 99999, 0, 0, 0, 0, 0, "", "", "1:500", 2, 0, 0),
        ]

        def fake_table(name):
            self.assertEqual(name, "ItemInfo.txt")
            return rows

        original = server._read_clientdb_table
        server._read_clientdb_table = fake_table
        try:
            beans = server.card_exp_items()
        finally:
            server._read_clientdb_table = original

        self.assertEqual(beans, {2002: 10000, 2003: 50000, 2004: 200000})

    def test_chinese_desc_still_works_without_use_effect(self):
        self.assertEqual(
            server._card_exp_from_item_fields("", "", "增加植物10000点经验"),
            10000,
        )

    def test_english_desc_fallback_only_for_type_6(self):
        self.assertEqual(
            server._card_exp_from_item_fields(6, "", "Increases plant EXP by 10,000"),
            10000,
        )
        self.assertEqual(
            server._card_exp_from_item_fields(6, "", "Grants 200,000 Plant EXP"),
            200000,
        )
        self.assertIsNone(
            server._card_exp_from_item_fields(
                2, "", "Grants 1 Plant Shard, 1 Chlorophyll, or 10,000-40,000 Plant EXP."
            )
        )


if __name__ == "__main__":
    unittest.main()
