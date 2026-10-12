"""Review issues 01/02/08 through the installed routes and disposable saves."""
from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import player_store as store

# Server import installs tables; point it at a disposable DB before importing.
_bootstrap = tempfile.TemporaryDirectory(prefix="pvzol-world-import-")
_before_import_db = store.DB_PATH
try:
    store.set_db_path(str(Path(_bootstrap.name) / "import.db"))
    store.init_db()
    import pvz_poc_server as g
    import guild_local as guild
    import guard_local as guard
    import jar_local as jar
finally:
    store.close_thread_db()
    store.set_db_path(_before_import_db)
    _bootstrap.cleanup()


class WorldRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pvzol-world-repair-")
        self.addCleanup(self.tmp.cleanup)
        old_db, old_guild_db = store.DB_PATH, guild._DB_PATH
        self.addCleanup(lambda: store.set_db_path(old_db))
        self.addCleanup(lambda: setattr(guild, "_DB_PATH", old_guild_db))
        self.addCleanup(store.close_thread_db)
        store.set_db_path(str(Path(self.tmp.name) / "test.db"))
        store.init_db()
        guild.initialize(store.DB_PATH)
        self.player, _ = store.get_or_create_player(88009120)
        self.uin = self.player["uin"]
        store.save_player(self.uin, name="Review Player", level=20, coin=500,
                          present_ticket=200, light=0)
        self.player.update(name="Review Player", level=20, coin=500,
                           present_ticket=200, light=0)

    def route(self, name, data=b"", player=None):
        return g.REQUEST_HANDLERS["PVZ.Cmd." + name + "_CS"](
            data, self.player if player is None else player)

    def wallet(self):
        with store._db() as con:
            return tuple(con.execute("SELECT coin,present_ticket,light FROM players WHERE uin=?",
                                     (self.uin,)).fetchone())

    def error(self, reply):
        return g.parse_varint_fields(dict(reply)["PVZ.Cmd.Cmd_LogicError_SC"])[1]

    def jar_request(self, index):
        return g.pb_varint_field(1, jar.ACTIVITY_ID) + g.pb_varint_field(2, index)

    def select_jars(self):
        chosen = [(group, item) for group in range(3) for item in (5, 4, 0, 1)]
        data = g.pb_varint_field(1, jar.ACTIVITY_ID)
        data += b"".join(g.pb_bytes(2, g.pb_varint_field(1, group)
                                  + g.pb_varint_field(2, item)) for group, item in chosen)
        reply = self.route("Cmd_FlexibleActivity26_Select", data)
        self.assertEqual(reply[0][0], "PVZ.Cmd.Cmd_FlexibleActivity26_Select_SC")
        return chosen

    def test_all_jar_diamond_groups_update_wallet_hud_and_not_bag(self):
        self.select_jars()
        store.add_item(self.uin, jar.HAMMER_ID, 10)
        # Existing accidental bag diamonds are left alone; no guessed compensation.
        store.add_item(self.uin, 4, 13)
        total = 200
        for jar_id, selected_index, amount in ((1, 0, 5), (2, 4, 10), (3, 8, 20)):
            with self.subTest(group=selected_index // 4), patch.object(jar.random, "choice", return_value=selected_index):
                reply = self.route("Cmd_FlexibleActivity26_BreakJar", self.jar_request(jar_id))
            total += amount
            self.assertEqual(self.wallet()[1], total)
            self.assertEqual(self.player["present_ticket"], total)
            self.assertEqual(g.parse_varint_fields(dict(reply)["PVZ.Cmd.Cmd_SyncPresentTicket_SC"]), {1: total})
            self.assertEqual(store.get_item_count(self.uin, 4), 13)
        self.assertEqual(store.get_item_count(self.uin, jar.HAMMER_ID), 4)

    def test_repeated_break_does_not_repeat_currency_cost_or_score(self):
        self.select_jars()
        store.add_item(self.uin, jar.HAMMER_ID, 2)
        with patch.object(jar.random, "choice", return_value=0):
            first = self.route("Cmd_FlexibleActivity26_BreakJar", self.jar_request(1))
            repeat = self.route("Cmd_FlexibleActivity26_BreakJar", self.jar_request(1))
        self.assertIn("PVZ.Cmd.Cmd_SyncPresentTicket_SC", dict(first))
        self.assertEqual(repeat, [])
        self.assertEqual(self.wallet()[1], 205)
        self.assertEqual(store.get_item_count(self.uin, jar.HAMMER_ID), 1)
        self.assertEqual(jar._state(g, self.player)["score"], 1)

    def test_60_hit_milestone_grants_both_wallets_once(self):
        jar._state(g, self.player)
        with store._db() as con:
            con.execute("UPDATE local_hitjar SET score=60 WHERE uin=?", (self.uin,))
        reply = self.route("Cmd_FlexibleActivity26_JoinReward", self.jar_request(3))
        self.assertEqual(self.wallet()[:2], (60500, 230))
        self.assertEqual(g.parse_varint_fields(dict(reply)["PVZ.Cmd.Cmd_SyncCoin_SC"]), {1: 60500})
        self.assertEqual(g.parse_varint_fields(dict(reply)["PVZ.Cmd.Cmd_SyncPresentTicket_SC"]), {1: 230})
        self.assertEqual(store.get_item_count(self.uin, 4), 0)
        self.assertEqual(self.route("Cmd_FlexibleActivity26_JoinReward", self.jar_request(3)), [])
        self.assertEqual(self.wallet()[:2], (60500, 230))

    def test_wallet_failure_rolls_back_milestone_and_other_currency(self):
        jar._state(g, self.player)
        with store._db() as con:
            con.execute("UPDATE local_hitjar SET score=60 WHERE uin=?", (self.uin,))
            con.execute("CREATE TRIGGER fail_diamond BEFORE UPDATE OF present_ticket ON players "
                        "WHEN NEW.present_ticket>OLD.present_ticket BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.route("Cmd_FlexibleActivity26_JoinReward", self.jar_request(3))
        self.assertEqual(self.wallet()[:2], (500, 200))
        self.assertEqual((self.player["coin"], self.player["present_ticket"]), (500, 200))
        self.assertEqual(jar._state(g, self.player)["milestones_taken"], 0)
        with store._db() as con:
            con.execute("DROP TRIGGER fail_diamond")
        self.route("Cmd_FlexibleActivity26_JoinReward", self.jar_request(3))
        self.assertEqual(self.wallet()[:2], (60500, 230))

    def light_info(self):
        body = dict(self.route("Cmd_Guard_Info"))["PVZ.Cmd.Cmd_Guard_Info_SC"]
        return g.parse_varint_fields(body)[7]

    def consume_light(self):
        store.save_player(self.uin, light=0)
        self.player["light"] = 0

    def buy_lights(self, count):
        for expected in range(1, count + 1):
            self.consume_light()
            reply = self.route("Cmd_Guard_BuyLight")
            self.assertEqual([name for name, _ in reply],
                             ["PVZ.Cmd.Cmd_SyncLight_SC", "PVZ.Cmd.Cmd_Guard_BuyLight_SC"])
            self.assertEqual(g.parse_varint_fields(reply[0][1]), {1: 1})
            self.assertEqual(g.parse_varint_fields(reply[1][1]), {2: expected})

    def test_guard_has_five_daily_purchases_and_server_rejects_sixth(self):
        with patch.object(guard.time, "strftime", return_value="20261009"):
            self.buy_lights(5)
            self.consume_light()
            before = self.wallet()
            self.assertEqual(self.error(self.route("Cmd_Guard_BuyLight")), 9901)
            self.assertEqual(self.light_info(), 5)
            self.assertEqual(self.wallet(), before)
            self.assertEqual(self.wallet(), (500, 200, 0))  # Free local policy remains.

    def test_guard_retries_and_stale_player_do_not_spend_daily_quota(self):
        with patch.object(guard.time, "strftime", return_value="20261009"):
            self.buy_lights(1)
            self.player["light"] = 0  # A stale in-memory request; DB still holds the light.
            reply = self.route("Cmd_Guard_BuyLight")
            self.assertEqual(g.parse_varint_fields(reply[1][1]), {2: 1})
            self.assertEqual(self.wallet(), (500, 200, 1))
            self.assertEqual(self.player["light"], 1)
            self.assertEqual(self.light_info(), 1)

    def test_guard_cross_day_resets_info_and_allows_purchase_again(self):
        with patch.object(guard.time, "strftime", return_value="20261009"):
            self.buy_lights(5)
            self.consume_light()
        with patch.object(guard.time, "strftime", return_value="20261010"):
            self.assertEqual(self.light_info(), 0)
            self.buy_lights(1)
            self.assertEqual(self.light_info(), 1)

    def test_guard_old_lifetime_count_migrates_once_without_future_lockout(self):
        with store._db() as con:
            con.execute("CREATE TABLE local_guard_buylight(uin INTEGER PRIMARY KEY,count INTEGER NOT NULL DEFAULT 0)")
            con.execute("INSERT INTO local_guard_buylight VALUES(?,99)", (self.uin,))
        with patch.object(guard.time, "strftime", return_value="20261009"):
            self.assertEqual(self.light_info(), 0)
            self.buy_lights(2)
            self.assertEqual(self.light_info(), 2)
            with store._db() as con:
                self.assertEqual(tuple(con.execute("SELECT count,day FROM local_guard_buylight WHERE uin=?",
                                                   (self.uin,)).fetchone()), (2, "20261009"))
        with patch.object(guard.time, "strftime", return_value="20261010"):
            self.assertEqual(self.light_info(), 0)

    def test_guard_light_write_failure_rolls_back_quota_and_memory(self):
        with patch.object(guard.time, "strftime", return_value="20261009"):
            self.assertEqual(self.light_info(), 0)
            with store._db() as con:
                con.execute("CREATE TRIGGER fail_light BEFORE UPDATE OF light ON players "
                            "WHEN NEW.light>OLD.light BEGIN SELECT RAISE(ABORT,'test failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                self.route("Cmd_Guard_BuyLight")
            self.assertEqual(self.light_info(), 0)
            self.assertEqual(self.wallet(), (500, 200, 0))
            self.assertEqual(self.player["light"], 0)
            with store._db() as con:
                con.execute("DROP TRIGGER fail_light")
            self.buy_lights(1)

    def test_guard_simultaneous_last_purchase_does_not_exceed_daily_limit(self):
        with patch.object(guard.time, "strftime", return_value="20261009"):
            self.buy_lights(4)
            self.consume_light()
            gate = threading.Barrier(2)

            def buy():
                request_player = copy.deepcopy(self.player)
                try:
                    gate.wait(timeout=5)
                    return self.route("Cmd_Guard_BuyLight", player=request_player)
                finally:
                    store.close_thread_db()

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(buy), executor.submit(buy)]
                replies = [future.result(timeout=10) for future in futures]
            self.assertEqual(self.light_info(), 5)
            self.assertEqual(self.wallet(), (500, 200, 1))
            self.assertEqual([g.parse_varint_fields(reply[1][1])[2] for reply in replies], [5, 5])
            self.consume_light()
            self.assertEqual(self.error(self.route("Cmd_Guard_BuyLight")), 9901)

    def make_guild(self, name, need_power, level=1, allow=True):
        with guild._db() as con:
            cur = con.execute("INSERT INTO local_guilds(name,created_at,allow_quick_join,need_power,level) VALUES(?,0,?,?,?)",
                              (name, int(allow), need_power, level))
            return cur.lastrowid

    def member(self):
        with guild._db() as con:
            row = guild._member(con, self.uin)
            return dict(row) if row else None

    def actual_power(self):
        # Test the final wiring too: a correct helper unused by the live route is insufficient.
        self.assertIs(guild._API.get("player_fight_power"), g._player_fight_power)
        store.add_card(self.uin, 912001, 1201, level=10)
        return g._player_fight_power(self.player)

    def test_quick_join_rejects_low_real_power_even_with_forged_hints(self):
        power = self.actual_power()
        self.make_guild("Too Strong", power + 1)
        self.player["fight_power"] = 2**63
        self.player["power"] = 2**63
        reply = self.route("Cmd_Guild_QuickJoin", g.pb_varint_field(1, 2**63))
        self.assertEqual(self.error(reply), 5066)
        self.assertIsNone(self.member())

    def test_quick_join_allows_exact_threshold_and_repeat_has_one_membership(self):
        power = self.actual_power()
        gid = self.make_guild("Exact Power", power)
        self.assertEqual(self.route("Cmd_Guild_QuickJoin"), b"")
        self.assertEqual(self.member()["guild_id"], gid)
        self.route("Cmd_Guild_QuickJoin")
        with guild._db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM local_guild_members WHERE uin=?", (self.uin,)).fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM local_guild_events WHERE role_id=?", (self.uin,)).fetchone()[0], 1)

    def test_quick_join_skips_high_gate_and_uses_next_eligible_guild(self):
        power = self.actual_power()
        self.make_guild("Too Strong First", power + 1, level=2)
        gid = self.make_guild("Eligible Second", power)
        self.route("Cmd_Guild_QuickJoin")
        self.assertEqual(self.member()["guild_id"], gid)

    def test_quick_join_uses_current_cards_after_power_increase(self):
        power = self.actual_power()
        gid = self.make_guild("Upgrade First", power + 1)
        self.assertEqual(self.error(self.route("Cmd_Guild_QuickJoin")), 5066)
        store.add_card(self.uin, 912002, 1201, level=10)
        self.route("Cmd_Guild_QuickJoin")
        self.assertEqual(self.member()["guild_id"], gid)

    def test_quick_join_missing_power_callback_rejects_gate_and_keeps_zero_compatible(self):
        self.make_guild("Needs Power", 1)
        with patch.dict(guild._API, {"player_fight_power": None}):
            self.assertEqual(self.error(self.route("Cmd_Guild_QuickJoin")), 5066)
            self.assertIsNone(self.member())
            gid = self.make_guild("No Gate", 0)
            self.route("Cmd_Guild_QuickJoin")
            self.assertEqual(self.member()["guild_id"], gid)

    def test_quick_join_no_open_guild_returns_real_error(self):
        self.make_guild("Closed", 0, allow=False)
        self.assertEqual(self.error(self.route("Cmd_Guild_QuickJoin")), 5064)
        self.assertIsNone(self.member())

    def test_quick_join_failed_power_calculation_cannot_bypass_gate(self):
        self.make_guild("Needs Working Power", 1)

        def fail(_player):
            raise RuntimeError("controlled power calculation failure")

        with patch.dict(guild._API, {"player_fight_power": fail}):
            self.assertEqual(self.error(self.route("Cmd_Guild_QuickJoin")), 5066)
        self.assertIsNone(self.member())


if __name__ == "__main__":
    unittest.main(verbosity=2)
