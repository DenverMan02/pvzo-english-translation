"""NoEndFuben puppets: real combat, original wire fields, disposable saves."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import pvz_poc_server as g
import missing_features_local as mf


def unit(pos, uid=0, hp=1000000, atk=1):
    return dict(pos=pos, uid=uid, card_id=101 if pos % 2 else 111,
                hp=hp, atk=atk, def_=0, ability=0, skill1=0, skill2=0,
                is_leader=False, level=1, power=100, name="test")


class NoEndPuppetRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db = g.store.DB_PATH
        self.addCleanup(lambda: g.store.set_db_path(self.old_db))
        g.store.set_db_path(str(Path(self.tmp.name) / "test.db"))
        g.store.init_db()
        self.player, _ = g.store.get_or_create_player(88009101)
        self.uin = self.player["uin"]
        self.api = dict(mf._API, db_path=g.store.DB_PATH)
        self.routes = mf.register(self.api)
        self.addCleanup(g.store.close_thread_db)
        g._CLIENTDB_PATH = str(SERVER.parent / "webroot" / "clientDB.txt")
        for uid in (9001, 9002):
            g.store.add_card(self.uin, uid, 101, 100)
        self.call("SetPreCard", g.pb_int64_field(1, 9001) + g.pb_int64_field(1, 9002))

    def call(self, name, body=b""):
        return self.routes["PVZ.Cmd.Cmd_NoEndFuben_" + name + "_CS"](body, self.player)

    def equip_puppet(self, slot=1):
        info = g.load_dummy_config()["zombies"][1000001]
        g.store.save_puppet(self.uin, 1000001, level=100, star=1, quality=100,
                            active_skill=info["active"], active_level=1,
                            passive_skill=info["passive"], passive_level=1)
        g.save_puppet_lineup(self.player, [0] * (slot - 1) + [1000001])

    def fight(self):
        left = [unit(3, 9001), unit(7, 9002)]
        right = [unit(4, hp=10000)]
        with patch.object(g, "build_player_units", side_effect=lambda *a, **kw: copy.deepcopy(left)), \
                patch.object(g, "_noend_fuben_squad", side_effect=lambda *a: copy.deepcopy(right)):
            result = self.call("Fight", g.pb_varint_field(1, 1))
        return result, mf._wire2(result, 6)

    @staticmethod
    def puppet_actions(report):
        return [(mf._wire0(round_, 1), mf._wire2(action, 6), action)
                for round_ in mf._repeated_messages(report, 4)
                for action in mf._repeated_messages(round_, 2)
                if mf._wire2(action, 6)]

    def test_real_puppet_attack_changes_outcome_and_is_encoded_on_player_side(self):
        _, without = self.fight()
        self.assertEqual(mf._wire0(without, 1), 1)
        self.equip_puppet()
        result, report = self.fight()
        self.assertEqual(mf._wire0(report, 1), 0)
        self.assertEqual(mf._wire0(report, 5), 26)
        self.assertEqual((mf._wire0(result, 1), mf._wire0(result, 3)), (0, 1))
        left, right = mf._wire2(report, 2), mf._wire2(report, 3)
        self.assertEqual((mf._wire0(left, 1), mf._wire0(right, 1)), (43, 44))
        listed = mf._repeated_messages(left, 16)
        self.assertEqual([(mf._wire0(x, 1), mf._wire0(x, 2)) for x in listed], [(101, 1000001)])
        self.assertEqual(mf._repeated_messages(right, 16), [])
        acts = self.puppet_actions(report)
        self.assertEqual([(n, mf._wire0(a, 1), mf._wire0(a, 3)) for n, a, _ in acts], [(1, 101, 101)])
        damage = mf._repeated_messages(acts[0][2], 2)[0]
        self.assertEqual((mf._wire0(damage, 1), mf._wire0(damage, 7)), (4, 1))
        self.assertEqual(mf._wire0(mf._repeated_messages(report, 4)[0], 1), 0)

    def test_sparse_lineup_acts_on_its_original_third_round(self):
        self.equip_puppet(slot=3)
        _, report = self.fight()
        acts = self.puppet_actions(report)
        self.assertEqual([(n, mf._wire0(a, 1)) for n, a, _ in acts], [(3, 105)])
        self.assertEqual(mf._wire0(report, 1), 0)

    def test_unknown_client_puppet_plan_does_not_emit_unloadable_actor(self):
        self.equip_puppet()
        with patch.object(g, "_BATTLECARD_IDS", {"101", "111"}):
            _, report = self.fight()
        self.assertEqual(self.puppet_actions(report), [])
        self.assertEqual(mf._repeated_messages(mf._wire2(report, 2), 16), [])
        self.assertEqual(mf._wire0(report, 1), 1)

    def test_puppet_combat_preserves_persistent_health_ratio(self):
        self.equip_puppet()
        with mf._db() as con:
            con.execute("UPDATE local_noend_fuben SET hp_ratios=? WHERE uin=?",
                        (json.dumps({"9001": .25, "9002": 0}), self.uin))
        _, report = self.fight()
        before = mf._repeated_messages(mf._wire2(report, 2), 2)
        after = mf._repeated_messages(mf._wire2(report, 2), 4)
        self.assertEqual([(mf._wire0(x, 1), mf._wire0(x, 3)) for x in before], [(3, 250000), (7, 0)])
        self.assertEqual(mf._wire0(after[1], 3), 0)
        with mf._db() as con:
            ratios = json.loads(mf._noend_row(con, self.uin)["hp_ratios"])
        self.assertAlmostEqual(ratios["9001"], mf._wire0(after[0], 3) / mf._wire0(after[0], 6))
        self.assertEqual(ratios["9002"], 0)

    def test_preview_preloads_selected_noend_plants_and_omits_enemy_puppets(self):
        target = g.pb_varint_field(1, 26) + g.pb_varint_field(22, 1) + g.pb_varint_field(23, 1)
        with patch.object(g, "_noend_fuben_squad", return_value=[unit(4)]), \
                patch.object(g, "default_formation_placements", return_value={13: 9002}), \
                patch.object(g, "build_formation_info", return_value=b"") as formation:
            body = g.handle_battle_getfightinfo(g.pb_bytes(1, target), self.player)
        formation.assert_called_once_with(self.player, 1, {3: 9001, 7: 9002})
        self.assertEqual(mf._repeated_messages(body, 6), [])


if __name__ == "__main__":
    unittest.main()
