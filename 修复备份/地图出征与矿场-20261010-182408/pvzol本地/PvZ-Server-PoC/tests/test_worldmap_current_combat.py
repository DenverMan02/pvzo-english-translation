"""Current map combat uses one simulation and its authoritative final health.

Run this suite in its own fresh Python process. No game module is imported until
IsolatedServer has copied the sources and selected its synthetic player database.
"""
import importlib
import json
import unittest
from unittest.mock import patch

from worldmap_test_support import IsolatedServer, many, message, one


class WorldMapCurrentCombatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = IsolatedServer()
        cls.g = cls.qa.game
        cls.wm = importlib.import_module("worldmap_local")
        cls.a = cls.g._worldmap_adapter
        cls.g.load_battle_cards()
        cls.serial = 0

    @classmethod
    def tearDownClass(cls):
        cls.qa.close()

    def setUp(self):
        type(self).serial += 1
        self.now = 1800000000
        self.p = self.qa.player(99509000 + self.serial, role_created=1,
                                level=100, coin=10000)
        self.g.rebuild_td_roster(self.p)
        self.g.ensure_player_cards(self.p)
        clock = patch.object(self.wm, "_now", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.placements = dict(zip(self.g._FORMATION_POS,
                                   [r[0] for r in self.qa.store.get_cards(self.p["uin"])]))
        dto = b"".join(self.m(1, self.v(1, pos) + self.g.pb_int64_field(2, uid))
                       for pos, uid in self.placements.items())
        dto += self.v(5, self.wm.TEAM_ID)
        message(self.call("Battle_SetFormation", self.m(1, dto)), "Battle_GetFormation")

    def v(self, number, value):
        return self.g.pb_varint_field(number, value)

    def m(self, number, value):
        return self.g.pb_bytes(number, value)

    def call(self, name, data=b""):
        return self.qa.invoke(name, data, self.p)

    def dispatch(self, npc):
        pos = self.v(1, npc["pos"][0]) + self.v(2, npc["pos"][1])
        reply = self.call("War_SetOut", self.v(1, 2) + self.m(2, pos)
                          + self.v(3, self.wm.TEAM_ID))
        self.assertEqual(one(message(reply, "War_SetOut"), 1), self.wm.TEAM_ID)
        return self.mission()

    def mission(self):
        with self.a.db(self.p) as con:
            row = con.execute("SELECT * FROM local_worldmap_missions WHERE uin=? "
                              "ORDER BY id DESC LIMIT 1", (self.p["uin"],)).fetchone()
        self.assertIsNotNone(row)
        return dict(row)

    @staticmethod
    def signed64(value):
        return value - (1 << 64) if value & (1 << 63) else value

    def test_real_win_and_loss_use_one_simulation_for_result_and_replay(self):
        original_run = self.g.run_battle
        original_simulate = self.g.simulate_battle
        for npc_index, level, expected_win in ((0, 100, True), (2, 1, False)):
            with self.subTest(npc=npc_index, expected_win=expected_win):
                self.qa.train_owned_cards(self.p, level=level)
                captured = {}

                def run_once(left, right, **options):
                    result = original_run(left, right, **options)
                    captured.update(result=result, left=left, right=right)
                    return result

                with patch.object(self.g, "run_battle", side_effect=run_once) as run_spy, \
                        patch.object(self.g, "simulate_battle", wraps=original_simulate) as simulate_spy:
                    row = self.dispatch(self.wm.NPCS[npc_index])
                self.assertEqual(run_spy.call_count, 1)
                self.assertEqual(simulate_spy.call_count, 1)
                self.assertEqual(run_spy.call_args.kwargs,
                                 {"leader_sides": ("left",), "stop_on_elimination": True})
                self.assertEqual(simulate_spy.call_args.kwargs["leader_sides"], ("left",))
                self.assertIs(simulate_spy.call_args.kwargs["stop_on_elimination"], True)
                won, _, _, rounds = captured["result"]
                self.assertEqual(bool(won), expected_win, "exercise both actual outcomes")
                self.assertEqual(bool(row["won"]), bool(won))
                report = bytes(row["report"])
                self.assertEqual((one(report, 1), one(report, 7)),
                                 (0, 1) if won else (1, 2))
                replay = many(report, 4)
                self.assertTrue(replay, "the saved report needs real battle rounds")
                self.assertEqual(replay, many(self.g.encode_fight_replay(rounds), 4),
                                 "saved replay must describe the simulation that settled the result")
                self.assertEqual(row["reward"], self.wm.NPCS[npc_index]["coin"] if won else 0)
                reported = {one(card, 1): card for card in many(one(report, 2), 4)}
                for unit in captured["left"]:
                    card = reported[unit["pos"]]
                    self.assertEqual(one(card, 3), unit["final_hp"])
                    self.assertEqual(one(card, 6), unit["final_max_hp"])
                self.now = row["home_at"] + 1
                self.a.advance(self.p)

    def test_heal_shield_and_max_hp_changes_persist_the_engine_final_ratio(self):
        """Cap increases grant HP outside changeHp, so summing damage is wrong."""
        captured = {}

        def controlled_simulation(left, right, _legacy=None, *, result_state=None, **options):
            self.assertEqual(options, {"leader_sides": ("left",),
                                       "stop_on_elimination": True})
            for unit in left:
                unit.update(hp=200, initial_hp=200, max_hp=200, shield=80,
                            max_shield=80, final_hp=90, final_max_hp=300,
                            final_shield=50, final_rage=0)
            for unit in right:
                unit.update(hp=100, initial_hp=100, max_hp=100, final_hp=0,
                            final_max_hp=100, final_shield=0, final_rage=0)
            ally, enemy = left[0]["pos"], right[0]["pos"]

            def action(actor, damages):
                return {"actor": actor, "skill": 1, "rage": 0, "damages": damages}

            # Per ally: 200 - 180 + 70 + 100(cap gain) - 100 = 90 HP;
            # the original cap is 200, final cap 300, and shield loses 30.
            rounds = [
                ([action(enemy, [{"target": u["pos"], "dmg": 180,
                                  "change_shield": -30, "dead": False} for u in left])], []),
                ([action(ally, [{"target": u["pos"], "dmg": -70,
                                 "dead": False} for u in left])], []),
                ([action(ally, [{"target": u["pos"], "dmg": -100,
                                 "change_max_hp": 100, "dead": False} for u in left])], []),
                ([action(enemy, [{"target": u["pos"], "dmg": 100,
                                  "dead": False} for u in left]),
                  action(ally, [{"target": u["pos"], "dmg": 100,
                                 "dead": True} for u in right])], []),
            ]
            result_state.update(won=True,
                                hp={u["pos"]: u["final_hp"] for u in left + right},
                                shield={u["pos"]: u["final_shield"] for u in left + right})
            captured.update(left=left, rounds=rounds)
            return rounds

        with patch.object(self.g, "simulate_battle", side_effect=controlled_simulation) as simulate_spy, \
                patch.object(self.g, "run_battle", wraps=self.g.run_battle) as run_spy:
            row = self.dispatch(self.wm.NPCS[0])
        self.assertEqual(run_spy.call_count, 1)
        self.assertEqual(simulate_spy.call_count, 1)
        expected_health = {str(uid): 3000 for uid in self.placements.values()}
        self.assertEqual(json.loads(row["health_after"]), expected_health)
        report = bytes(row["report"])
        self.assertEqual((one(report, 1), one(report, 7)), (0, 1))
        side = one(report, 2)
        before = {one(card, 1): card for card in many(side, 2)}
        after = {one(card, 1): card for card in many(side, 4)}
        for pos, uid in self.placements.items():
            self.assertEqual((one(before[pos], 3), one(before[pos], 6)), (200, 200))
            self.assertEqual((one(after[pos], 3), one(after[pos], 6), one(after[pos], 5)),
                             (90, 300, 50))
            self.assertEqual(json.loads(row["health_after"])[str(uid)],
                             one(after[pos], 3) * 10000 // one(after[pos], 6))

        damages = [damage for frame in many(report, 4)
                   for action in many(frame, 2) for damage in many(action, 2)]
        first_pos = next(iter(self.placements))
        own_damages = [d for d in damages if one(d, 1) == first_pos]
        self.assertTrue(any(self.signed64(one(d, 2)) == 70 for d in own_damages), "heal survives replay")
        self.assertTrue(any(self.signed64(one(d, 6, 0)) == -30 for d in own_damages), "shield delta survives replay")
        self.assertTrue(any(one(d, 11, 0) == 100 for d in own_damages), "max-HP delta survives replay")
        naive_hp = one(before[first_pos], 3) + sum(self.signed64(one(d, 2)) for d in own_damages)
        self.assertNotEqual(naive_hp, one(after[first_pos], 3),
                            "this fixture exposes summing changeHp without max-HP semantics")

        self.now = row["home_at"] + 1
        self.a.advance(self.p)
        with self.a.db(self.p) as con:
            saved_health = json.loads(self.a.state(con, self.p)["health"])
        self.assertEqual({uid: saved_health[uid] for uid in expected_health}, expected_health)
        settled = self.mission()
        self.assertEqual((settled["state"], settled["settled"]), (self.wm.HOME, 1))
        formation = self.a.formation(self.p, self.wm.TEAM_ID)
        for card in many(formation, 1):
            self.assertEqual(one(card, 4), one(card, 5) * 3000 // 10000,
                             "the returned formation must display the saved final health ratio")
        query = message(self.call("Battle_QueryReport", self.v(1, row["id"])), "Battle_QueryReport")
        self.assertEqual(one(query, 1), report, "history returns the original settled report")

    def test_dispatcher_keeps_endless_puppet_restrictions_after_map_registration(self):
        """Actual non-map delegation must still use the stored endless layer."""
        mf = importlib.import_module("missing_features_local")
        repair = importlib.import_module("endless_rules_repairs_20261009")
        deployed = "1000001"
        self.qa.store.save_puppet(self.p["uin"], 1000001, level=1)
        self.p["puppet_lineup"] = deployed
        self.qa.store.save_player(self.p["uin"], puppet_lineup=deployed)
        pos, uid = next(iter(self.placements.items()))
        with patch.object(repair.time, "time", side_effect=lambda: self.now):
            # The request deliberately asks for a different layer. Restrictions
            # must follow the synthetic account's actual saved mode instead.
            for saved_mode, requested_mode, allowed in ((1, 7, False), (2, 7, False), (3, 1, True)):
                with self.subTest(saved_mode=saved_mode, requested_mode=requested_mode):
                    self.assertEqual(repair.mode_rules(self.g, saved_mode)["puppets"], allowed)
                    with mf._db() as con:
                        mf._noend_row(con, self.p["uin"])
                        con.execute("UPDATE local_noend_fuben SET mode_id=?,pre_card=?,formation_json=?,"
                                    "formation_set=1,hp_ratios='{}',begin_time=?,end_time=?,"
                                    "stage_id=1,leader_uid=? WHERE uin=?",
                                    (saved_mode, str(uid), json.dumps({pos: uid}), self.now - 1,
                                     self.now + 86400, uid, self.p["uin"]))
                    target = self.v(1, 26) + self.v(22, requested_mode) + self.v(23, 2)
                    with patch.object(self.g, "preview_enemy_units", wraps=self.g.preview_enemy_units) as enemies, \
                            patch.object(self.g, "build_formation_info", wraps=self.g.build_formation_info) as formation:
                        reply = message(self.call("Battle_GetFightInfo", self.m(1, target)), "Battle_GetFightInfo")
                    self.assertEqual(enemies.call_count, 1, "the real dispatcher must reach the endless preview")
                    self.assertEqual(formation.call_count, 1)
                    expected_lineup = deployed if allowed else ""
                    self.assertEqual(enemies.call_args.args[0]["puppet_lineup"], expected_lineup)
                    self.assertEqual(formation.call_args.args[0]["puppet_lineup"], expected_lineup,
                                     "formation power/preload must use the restricted player too")
                    self.assertTrue(many(reply, 4), "delegate still returns the real enemy preview")
                    self.assertEqual([one(card, 2) for card in many(one(reply, 1), 1)], [uid],
                                     "delegate must preserve the actual endless selected formation")
                    self.assertEqual(self.p["puppet_lineup"], deployed, "restriction is local to this mode's preview")
                    self.assertEqual(self.qa.store.get_or_create_player(self.p["uin"])[0]["puppet_lineup"], deployed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
