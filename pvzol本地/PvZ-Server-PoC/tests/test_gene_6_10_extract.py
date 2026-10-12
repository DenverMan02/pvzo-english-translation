import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as s


ROOT = Path(__file__).resolve().parents[2]
s._CLIENTDB_PATH = str(ROOT / "webroot" / "clientDB.txt")


class GeneFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        s.store.DB_PATH = str(Path(self.tmp.name) / "players.db")
        s.store.init_db()
        self.uin, self.uid = 99001, 90001
        self.player, _ = s.store.get_or_create_player(self.uin)
        self.player["level"] = 60
        self.player["coin"] = 500000
        s.store.save_player(self.uin, level=60, coin=500000)
        self.card_id = 1215  # real, five-star, fruit type 1, rarity 1
        s.store.add_card(self.uin, self.uid, self.card_id, 300)
        s.load_card_dna()
        s.load_battle_cards()
        self.card_info = s._BATTLE_CARDS[self.card_id]
        for iid in (*range(92100, 92120), self.card_info["recycleItemId"]):
            s.store.add_item(self.uin, iid, 100000)

    def tearDown(self):
        s.store.close_thread_db()   # release players.db so TemporaryDirectory can delete it
        self.tmp.cleanup()

    def request(self, handler, gene_id=None, attribute=None):
        body = s.pb_varint_field(1, self.uid)
        if gene_id is not None:
            body += s.pb_varint_field(2, gene_id)
        if attribute is not None:
            body += s.pb_varint_field(3, attribute)
        return handler(body, self.player)

    def test_stages_six_through_ten_support_factor_activate_upgrade_and_reset(self):
        dna = s.load_card_dna()
        for stage in range(6, 11):
            if stage > 1:
                s.store.set_card_gene(self.uin, self.uid, stage - 1, 10, 0,
                                      dna["info"][(1, stage - 1)][0], 1)
            s.store.set_card_gene(self.uin, self.uid, stage, 9, 0, 0, 0)
            with patch.object(s.random, "randint", return_value=1):
                replies = self.request(s.handle_card_upgrade_gene_factor, stage)
            self.assertTrue(replies, stage)
            self.assertEqual(s.store.get_card_genes(self.uin, self.uid)[stage]["factor_level"], 10)
            first, second = dna["info"][(1, stage)][:2]
            self.assertTrue(self.request(s.handle_card_activate_gene, stage, first), stage)
            self.assertTrue(self.request(s.handle_card_upgrade_gene, stage), stage)
            self.assertEqual(s.store.get_card_genes(self.uin, self.uid)[stage]["level"], 2)
            self.assertTrue(self.request(s.handle_card_reset_gene, stage, second), stage)
            self.assertEqual(s.store.get_card_genes(self.uin, self.uid)[stage]["attribute"], second)

    def test_recycle_requires_extract_material_and_is_once_only(self):
        dna = s.load_card_dna()
        stage, attr = 10, dna["info"][(1, 10)][0]
        s.store.set_card_gene(self.uin, self.uid, stage, 1, 0, attr, 1)
        genes = s.store.get_card_genes(self.uin, self.uid)
        expected_cost = s._card_gene_recycle_cost(self.card_info, genes)
        expected_refund = s._card_gene_refund(self.card_info, genes)
        self.assertIn(92107, expected_cost)
        self.assertNotIn(92107, expected_refund)
        s.store.add_item(self.uin, 92107, -100000)
        self.assertFalse(self.request(s.handle_card_recycle_gene))
        self.assertIn(stage, s.store.get_card_genes(self.uin, self.uid))
        s.store.add_item(self.uin, 92107, expected_cost[92107])
        before = {iid: s.store.get_item_count(self.uin, iid)
                  for iid in set(expected_cost) | set(expected_refund)}
        replies = self.request(s.handle_card_recycle_gene)
        self.assertEqual(replies[0][0], "PVZ.Cmd.Cmd_Card_RecycleGene_SC")
        self.assertFalse(s.store.get_card_genes(self.uin, self.uid))
        for iid in before:
            self.assertEqual(s.store.get_item_count(self.uin, iid),
                             before[iid] + expected_refund.get(iid, 0) - expected_cost.get(iid, 0))
        self.assertFalse(self.request(s.handle_card_recycle_gene))

    def test_recycle_button_receives_active_stage_in_package_and_sync(self):
        dna = s.load_card_dna()
        for stage in (1, 2):
            s.store.set_card_gene(self.uin, self.uid, stage, 10, 0,
                                  dna["info"][(1, stage)][0], 1)

        def stage_in_card(raw):
            base = s.parse_repeated_submessages(raw, 1)[0]
            return s.parse_varint_fields(base).get(13, 0)

        package = s.handle_card_getpackage(b"", self.player)
        cards = s.parse_repeated_submessages(package, 1)
        matching = [raw for raw in cards if s.parse_varint_fields(
            s.parse_repeated_submessages(raw, 1)[0]).get(1) == self.uid]
        self.assertEqual(len(matching), 1)
        self.assertEqual(stage_in_card(matching[0]), 2)
        sync = s.build_card_sync_sc(self.uin, self.uid, self.card_id, 275)
        self.assertEqual(stage_in_card(s.parse_repeated_submessages(sync[1], 1)[0]), 2)

        self.assertTrue(self.request(s.handle_card_recycle_gene))
        sync = s.build_card_sync_sc(self.uin, self.uid, self.card_id, 313)
        self.assertEqual(stage_in_card(s.parse_repeated_submessages(sync[1], 1)[0]), 0)


if __name__ == "__main__":
    unittest.main()
