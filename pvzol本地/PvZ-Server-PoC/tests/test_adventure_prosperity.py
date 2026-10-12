"""Adventure prosperity progression and preservation of unrelated client/server fixes."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store
import pvz_poc_server as srv

spec = importlib.util.spec_from_file_location("prosperity_patch", SERVER / "client_tools" / "repair_adventure_prosperity.py")
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def archive_tables(raw):
    pos, tables = 0, {}
    while pos < len(raw):
        length = int.from_bytes(raw[pos:pos + 4], "big")
        pos += 4
        name = raw[pos:pos + length].decode()
        pos += length
        length = int.from_bytes(raw[pos:pos + 4], "big")
        pos += 4
        tables[name] = raw[pos:pos + length]
        pos += length
    return tables


class AdventureProsperityTests(unittest.TestCase):
    def test_all_ordinary_and_elite_stages_increase_to_2800(self):
        rewards = srv.load_fuben_rewards()
        for mode in (1, 2):
            keys = sorted(key for key in rewards if key[1] == mode)
            values = [dict(rewards[key][1])[2] for key in keys]
            self.assertEqual(len(keys), 440)
            self.assertEqual(values[0], 160)
            self.assertEqual(keys[-1], (32, mode, 15))
            self.assertEqual(values[-1], 2800)
            self.assertTrue(all(b > a for a, b in zip(values, values[1:])))
            self.assertEqual(set(b - a for a, b in zip(values, values[1:])), {6, 7})
        self.assertEqual(dict(srv.fuben_stage_reward(32, 1, 15, True))[2], 2800)
        self.assertEqual(dict(srv.fuben_stage_reward(32, 2, 15, True))[2], 2800)

    def test_replay_sweep_unknown_stage_and_union_do_not_gain_first_clear_reward(self):
        for mode in (1, 2):
            self.assertNotIn(2, dict(srv.fuben_stage_reward(32, mode, 15, False)))
        self.assertNotIn(2, dict(srv.fuben_stage_reward(32, 3, 15, True)))
        self.assertEqual(srv.fuben_stage_reward(999, 1, 1, True), [])

    def test_real_deposit_is_exact_bag_prosperity_not_role_xp(self):
        original = store.DB_PATH
        store.close_thread_db()
        try:
            with tempfile.TemporaryDirectory(prefix="pvz_prosperity_") as folder:
                store.DB_PATH = str(Path(folder) / "test.db")
                store.init_db()
                player, _ = store.get_or_create_player(700009030)
                before = store.get_item_count(player["uin"], 2)
                role_xp = player["exp"]
                msgs = srv.grant_fuben_reward(player, [(2, 2800)])
                self.assertEqual(store.get_item_count(player["uin"], 2), before + 2800)
                body = next(body for kind, body in msgs if kind == "PVZ.Cmd.Cmd_Item_Update_SC")
                fields = srv.parse_varint_fields(srv.parse_repeated_submessages(body, 1)[0])
                self.assertEqual((fields[2], fields[3]), (2, before + 2800))
                self.assertEqual(player["exp"], role_xp)
                store.close_thread_db()
        finally:
            store.close_thread_db()
            store.DB_PATH = original

    def test_only_first_clear_prosperity_changed_other_tables_byte_identical(self):
        original = SERVER / "backups" / "adventure-prosperity-20260929" / "clientDB.before.txt"
        before = archive_tables(original.read_bytes())
        after = archive_tables(Path(srv._CLIENTDB_PATH).read_bytes())
        self.assertEqual(before.keys(), after.keys())
        for name in before:
            if name != "fuben.txt":
                self.assertEqual(before[name], after[name], name)
        old = [line.split("\t") for line in before["fuben.txt"].decode().splitlines()]
        new = [line.split("\t") for line in after["fuben.txt"].decode().splitlines()]
        self.assertEqual(len(old), len(new))
        column = old[0].index("bonusFirst")
        changed = 0
        for a, b in zip(old, new):
            self.assertEqual(a[:column], b[:column])
            self.assertEqual(a[column + 1:], b[column + 1:])
            if a[column] != b[column]:
                changed += 1
                old_items = srv.parse_bonus_items(a[column])
                new_items = srv.parse_bonus_items(b[column])
                self.assertEqual([(iid, count) for iid, count in old_items if iid != 2],
                                 [(iid, count) for iid, count in new_items if iid != 2])
        self.assertEqual(changed, 878)

    def test_repair_is_idempotent(self):
        raw = Path(srv._CLIENTDB_PATH).read_bytes()
        patched, changes = repair.patch_archive(raw)
        self.assertEqual(patched, raw)
        self.assertEqual(changes, [])

    def test_non_flat_future_rewards_are_not_silently_overwritten(self):
        before = SERVER / "backups" / "adventure-prosperity-20260929" / "clientDB.before.txt"
        raw = before.read_bytes().replace(b"4001:1;2:160", b"4001:1;2:900", 1)
        with self.assertRaises(ValueError):
            repair.patch_archive(raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
