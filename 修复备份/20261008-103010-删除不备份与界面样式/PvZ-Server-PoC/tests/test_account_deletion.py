"""Exercise only disposable SQLite databases; never the user's game saves."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import account_management as accounts
import player_store as store


class AccountDeletionTests(unittest.TestCase):
    def setUp(self):
        self.original_path = store.DB_PATH
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.path = self.base / "server" / "players.db"
        self.path.parent.mkdir()
        store.set_db_path(str(self.path))
        store.init_db()
        store.get_or_create_player(101)
        store.get_or_create_player(202)
        store.get_or_create_player(303)
        store.close_thread_db()
        self.sql("UPDATE players SET role_id=9001,name='待删除',coin=7654 WHERE uin=101")
        self.sql("UPDATE players SET role_id=90202,name='保留甲',coin=4321 WHERE uin=202")
        self.sql("UPDATE players SET role_id=90303,name='保留乙',coin=1234 WHERE uin=303")
        self.sql("INSERT INTO cards(uin,uid,card_id,level) VALUES(101,77,1103,10)")
        self.sql("INSERT INTO cards(uin,uid,card_id,level) VALUES(202,77,1203,20)")
        self.sql("INSERT INTO items VALUES(101,123,7)")
        self.sql("INSERT INTO items VALUES(202,123,8)")
        self.sql('CREATE TABLE "legacy "" progression"(uin INTEGER,progress INTEGER)')
        self.sql('INSERT INTO "legacy "" progression" VALUES(101,95),(202,98)')

    def tearDown(self):
        store.set_db_path(self.original_path)
        self.temp.cleanup()

    def sql(self, statement, parameters=()):
        con = sqlite3.connect(self.path)
        try:
            rows = con.execute(statement, parameters).fetchall()
            con.commit()
            return rows
        finally:
            con.close()

    def snapshot(self, path=None):
        con = sqlite3.connect(path or self.path)
        try:
            tables = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            return {name: sorted(con.execute("SELECT * FROM " + accounts._quote(name)).fetchall(), key=repr)
                    for name, in tables}
        finally:
            con.close()

    def social_and_guild(self):
        statements = [
            "INSERT INTO friends(uin,friend_role_id,friend_name) VALUES(101,90202,'保留甲'),(202,9001,'待删除'),(202,90303,'保留乙')",
            "INSERT INTO formation VALUES(202,1,1,77,9001),(202,1,2,77,0),(202,1,3,88,9001)",
            "INSERT INTO formation_leader VALUES(202,1,77),(202,2,88)",
            "INSERT INTO formation VALUES(202,2,1,88,9001)",
            "CREATE TABLE local_friend_applications(sender INTEGER,receiver INTEGER,status INTEGER)",
            "INSERT INTO local_friend_applications VALUES(101,202,0),(202,101,0),(202,303,0)",
            "CREATE TABLE local_friend_energy(id INTEGER PRIMARY KEY,sender INTEGER,receiver INTEGER,claimed INTEGER)",
            "INSERT INTO local_friend_energy VALUES(1,101,202,0),(2,202,101,1),(3,202,303,1)",
            "CREATE TABLE local_friend_blacklist(uin INTEGER,role_id INTEGER)",
            "INSERT INTO local_friend_blacklist VALUES(101,90303),(202,9001),(202,90303)",
            "CREATE TABLE local_friend_hidden(uin INTEGER,role_id INTEGER)",
            "INSERT INTO local_friend_hidden VALUES(101,90303),(202,9001),(202,90303)",
            "CREATE TABLE local_noend_votes(uin INTEGER,mode INTEGER,target_role_id INTEGER,board_type INTEGER,up_down INTEGER)",
            "INSERT INTO local_noend_votes VALUES(101,1,90303,0,1),(202,1,9001,0,1),(202,1,90303,0,1)",
            "CREATE TABLE local_mails(uin INTEGER,id INTEGER PRIMARY KEY,send_role_id INTEGER,send_name TEXT,send_face_id INTEGER,attachment TEXT,content TEXT)",
            "INSERT INTO local_mails VALUES(101,1,303,'保留乙',7,'100:3','给待删除'),(202,2,101,'待删除',4,'100:5','保留邮件内容'),(202,3,303,'保留乙',7,'100:9','其他邮件')",
            "CREATE TABLE local_guilds(guild_id INTEGER PRIMARY KEY,name TEXT,exp INTEGER)",
            "INSERT INTO local_guilds VALUES(10,'保留公会',54321),(30,'其他公会',45678)",
            "CREATE TABLE local_guild_members(guild_id INTEGER,uin INTEGER,role_id INTEGER,position INTEGER,joined_at INTEGER,PRIMARY KEY(guild_id,uin))",
            "INSERT INTO local_guild_members VALUES(10,101,9001,1,5),(10,202,90202,2,10),(10,303,90303,3,8)",
            "CREATE TABLE local_guild_applications(guild_id INTEGER,uin INTEGER,role_id INTEGER)",
            "INSERT INTO local_guild_applications VALUES(30,101,9001),(30,202,90202)",
            "CREATE TABLE local_guild_invites(guild_id INTEGER,inviter_uin INTEGER,invitee_uin INTEGER)",
            "INSERT INTO local_guild_invites VALUES(10,101,999),(10,202,101),(10,202,888),(30,303,999)",
            "CREATE TABLE local_guild_events(event_id INTEGER PRIMARY KEY,guild_id INTEGER,role_id INTEGER,text TEXT)",
            "INSERT INTO local_guild_events VALUES(1,10,9001,'目标事件'),(2,10,90202,'其他事件'),(3,30,90303,'别会事件')",
            "CREATE TABLE local_guild_dungeon(guild_id INTEGER PRIMARY KEY,hp INTEGER)",
            "INSERT INTO local_guild_dungeon VALUES(10,123456),(30,654321)",
            "CREATE TABLE local_guild_dungeon_claims(guild_id INTEGER,uin INTEGER,count INTEGER)",
            "INSERT INTO local_guild_dungeon_claims VALUES(10,101,3),(10,202,7)",
            "CREATE TABLE local_feature_state(uin INTEGER,feature TEXT,data TEXT,PRIMARY KEY(uin,feature))",
        ]
        for statement in statements:
            self.sql(statement)
        self.sql("INSERT INTO local_feature_state VALUES(?,?,?)", (101, "personal", '{"value":8}'))
        self.sql("INSERT INTO local_feature_state VALUES(?,?,?)", (202, "personal", '{"value":9}'))
        self.sql("INSERT INTO local_feature_state VALUES(?,?,?)", (-10, "guild_leader", '{"leader":9001,"target":90202}'))
        self.sql("INSERT INTO local_feature_state VALUES(?,?,?)", (-10, "guild_hp_1_1_0", '{"hp":123456}'))
        self.sql("INSERT INTO local_feature_state VALUES(?,?,?)", (-10, "guild_hp_1_1_0_damage", '{"9001":99,"90202":88}'))
        self.sql("INSERT INTO local_feature_state VALUES(?,?,?)", (-30, "guild_hp_1_1_0_damage", '{"90303":77}'))

    def test_existing_lookup_is_readonly_and_does_not_change_login(self):
        before = self.snapshot()
        contents = self.path.read_bytes()
        self.assertEqual(accounts.get_account("00101")["name"], "待删除")
        self.assertEqual(accounts.get_account(101)["coin"], 7654)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), contents)
        self.assertFalse((self.path.parent / "backups").exists())

    def test_missing_lookup_and_deletion_do_not_create_account(self):
        before = self.snapshot()
        contents = self.path.read_bytes()
        self.assertIsNone(accounts.get_account("999"))
        with self.assertRaisesRegex(ValueError, "不存在"):
            accounts.delete_account("999")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), contents)
        self.assertFalse((self.path.parent / "backups").exists())

    def test_missing_database_is_never_created_or_migrated(self):
        absent = self.base / "absent" / "players.db"
        store.set_db_path(str(absent))
        self.assertIsNone(accounts.get_account(101))
        with self.assertRaises(ValueError):
            accounts.delete_account(101)
        self.assertFalse(absent.parent.exists())

    def test_invalid_account_numbers_have_no_side_effects(self):
        contents = self.path.read_bytes()
        for value in (True, False, None, 1.0, [], {}, "", "0", 0, -1, "+1", "-1", "1.0", "1&x=2",
                      " 101", "101 ", "１０１", "١٠١", "4294967296", 4294967296, "9" * 10000):
            with self.subTest(value=repr(value)[:50]):
                with self.assertRaises(ValueError):
                    accounts.get_account(value)
                with self.assertRaises(ValueError):
                    accounts.delete_account(value)
        self.assertEqual(self.path.read_bytes(), contents)
        self.assertFalse((self.path.parent / "backups").exists())

    def test_boundary_and_leading_zero_numbers_are_validated(self):
        self.assertEqual(accounts._account_number("0000000001"), 1)
        self.assertEqual(accounts._account_number("0" * 10000 + "101"), 101)
        self.assertEqual(accounts._account_number("4294967295"), 4294967295)

    def test_delete_all_per_account_tables_backup_can_restore_every_row(self):
        before = self.snapshot()
        result = accounts.delete_account("00101")
        self.assertEqual(result["uin"], 101)
        self.assertEqual(result["name"], "待删除")
        self.assertEqual(Path(result["backup"]).parent, self.path.parent / "backups" / "account-delete")
        self.assertEqual(self.snapshot(result["backup"]), before)
        self.assertEqual(result["deleted_counts"]["players"], 1)
        self.assertEqual(result["deleted_counts"]['legacy " progression'], 1)
        self.assertIsNone(accounts.get_account(101))
        con = sqlite3.connect(self.path)
        try:
            for table, columns in accounts._schema(con).items():
                if "uin" in columns:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM " + accounts._quote(table) + " WHERE uin=101").fetchone()[0], 0, table)
        finally:
            con.close()
        after = self.snapshot()
        for table, rows in before.items():
            if table != "sqlite_sequence":
                self.assertTrue(all(row in after[table] for row in rows if row[0] in (202, 303)), table)

    def test_only_active_database_changes_legacy_root_database_is_untouched(self):
        legacy = self.base / "players.db"
        legacy.write_bytes(b"historical-root-database-do-not-touch")
        accounts.delete_account(101, self.base / "custom-backups")
        self.assertEqual(legacy.read_bytes(), b"historical-root-database-do-not-touch")
        self.assertEqual(len(list((self.base / "custom-backups").glob("*.db"))), 1)

    def test_backup_failure_does_not_delete_or_anonymize_any_row(self):
        self.social_and_guild()
        before = self.snapshot()
        blocked = self.base / "backup-path-is-a-file"
        blocked.write_text("existing file", encoding="utf-8")
        with self.assertRaises(OSError):
            accounts.delete_account(101, blocked)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(blocked.read_text(encoding="utf-8"), "existing file")

    def test_sqlite_abort_after_related_changes_rolls_back_every_change(self):
        self.social_and_guild()
        self.sql("CREATE TRIGGER prevent_delete BEFORE DELETE ON players BEGIN SELECT RAISE(ABORT,'blocked'); END")
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            accounts.delete_account(101)
        self.assertEqual(self.snapshot(), before)
        backups = list((self.path.parent / "backups" / "account-delete").glob("*.db"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.snapshot(backups[0]), before)

    def test_foreign_key_commit_error_rolls_back_all_deletions(self):
        self.sql("CREATE TABLE unknown_reference(account_id INTEGER REFERENCES players(uin))")
        self.sql("INSERT INTO unknown_reference VALUES(101)")
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            accounts.delete_account(101)
        self.assertEqual(self.snapshot(), before)

    def test_wal_backup_includes_uncheckpointed_committed_save(self):
        keeper = sqlite3.connect(self.path)
        try:
            keeper.execute("PRAGMA journal_mode=WAL")
            keeper.execute("PRAGMA wal_autocheckpoint=0")
            keeper.execute("UPDATE players SET coin=998877 WHERE uin=202")
            keeper.commit()
            before = self.snapshot()
            self.assertTrue(Path(str(self.path) + "-wal").exists())
            result = accounts.delete_account(101)
            self.assertEqual(self.snapshot(result["backup"]), before)
            self.assertEqual(accounts.get_account(202)["coin"], 998877)
        finally:
            keeper.close()

    def test_writer_lock_protects_backup_and_delete_from_concurrent_writes(self):
        backup = accounts._backup_database
        attempted = []
        def guarded_backup(*args):
            other = sqlite3.connect(self.path, timeout=0.01)
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    other.execute("UPDATE players SET coin=0 WHERE uin=202")
                attempted.append(True)
            finally:
                other.close()
            return backup(*args)
        with patch.object(accounts, "_backup_database", side_effect=guarded_backup):
            accounts.delete_account(101)
        self.assertEqual(attempted, [True])
        self.assertEqual(accounts.get_account(202)["coin"], 4321)

    def test_social_references_removed_other_players_cards_and_received_mail_preserved(self):
        self.social_and_guild()
        original_cards = self.sql("SELECT * FROM cards WHERE uin=202")
        result = accounts.delete_account(101)
        self.assertEqual(self.sql("SELECT friend_role_id FROM friends WHERE uin=202"), [(90303,)])
        self.assertEqual(self.sql("SELECT * FROM local_friend_applications"), [(202, 303, 0)])
        self.assertEqual(self.sql("SELECT * FROM local_friend_energy"), [(3, 202, 303, 1)])
        for table in ("local_friend_blacklist", "local_friend_hidden"):
            self.assertEqual(self.sql("SELECT * FROM " + table), [(202, 90303)])
        self.assertEqual(self.sql("SELECT target_role_id FROM local_noend_votes"), [(90303,)])
        self.assertEqual(self.sql("SELECT * FROM cards WHERE uin=202"), original_cards)
        self.assertEqual(self.sql("SELECT * FROM formation"), [(202, 1, 2, 77, 0)])
        self.assertEqual(self.sql("SELECT * FROM formation_leader"), [(202, 1, 77)])
        self.assertEqual(self.sql("SELECT send_role_id,send_name,send_face_id,attachment,content FROM local_mails WHERE id=2"),
                         [(0, "已删除账号", 0, "100:5", "保留邮件内容")])
        self.assertEqual(self.sql("SELECT send_role_id,send_name,send_face_id,attachment,content FROM local_mails WHERE id=3"),
                         [(303, "保留乙", 7, "100:9", "其他邮件")])
        self.assertGreater(result["deleted_counts"]["formation"], 0)

    def test_guild_leader_transferred_shared_progress_and_other_damage_preserved(self):
        self.social_and_guild()
        accounts.delete_account(101)
        self.assertEqual(self.sql("SELECT uin,position FROM local_guild_members ORDER BY uin"), [(202, 1), (303, 3)])
        self.assertEqual(self.sql("SELECT * FROM local_guilds ORDER BY guild_id"), [(10, "保留公会", 54321), (30, "其他公会", 45678)])
        self.assertEqual(self.sql("SELECT * FROM local_guild_dungeon ORDER BY guild_id"), [(10, 123456), (30, 654321)])
        self.assertEqual(self.sql("SELECT * FROM local_guild_dungeon_claims"), [(10, 202, 7)])
        self.assertEqual(self.sql("SELECT * FROM local_guild_invites ORDER BY guild_id"), [(10, 202, 888), (30, 303, 999)])
        self.assertEqual(self.sql("SELECT event_id FROM local_guild_events ORDER BY event_id"), [(2,), (3,)])
        self.assertEqual(self.sql("SELECT data FROM local_feature_state WHERE uin=-10 AND feature='guild_leader'"), [])
        data = self.sql("SELECT data FROM local_feature_state WHERE uin=-10 AND feature LIKE '%_damage'")[0][0]
        self.assertEqual(json.loads(data), {"90202": 88})
        self.assertEqual(self.sql("SELECT data FROM local_feature_state WHERE uin=-10 AND feature='guild_hp_1_1_0'"), [('{"hp":123456}',)])
        self.assertEqual(self.sql("SELECT data FROM local_feature_state WHERE uin=-30"), [('{"90303":77}',)])

    def test_last_guild_member_deletion_removes_only_empty_guild_and_its_state(self):
        self.social_and_guild()
        self.sql("DELETE FROM local_guild_members WHERE uin<>101")
        accounts.delete_account(101)
        self.assertEqual(self.sql("SELECT guild_id FROM local_guilds"), [(30,)])
        self.assertEqual(self.sql("SELECT * FROM local_guild_dungeon"), [(30, 654321)])
        self.assertEqual(self.sql("SELECT * FROM local_guild_invites"), [(30, 303, 999)])
        self.assertEqual(self.sql("SELECT event_id FROM local_guild_events"), [(3,)])
        self.assertEqual(self.sql("SELECT data FROM local_feature_state WHERE uin=-10"), [])
        self.assertEqual(self.sql("SELECT data FROM local_feature_state WHERE uin=-30"), [('{"90303":77}',)])
        self.assertEqual(accounts.get_account(202)["coin"], 4321)

    def test_deleting_regular_member_does_not_replace_remaining_leader(self):
        self.social_and_guild()
        self.sql("UPDATE local_guild_members SET position=2 WHERE uin=101")
        self.sql("UPDATE local_guild_members SET position=1 WHERE uin=303")
        accounts.delete_account(101)
        self.assertEqual(self.sql("SELECT uin,position FROM local_guild_members ORDER BY uin"), [(202, 2), (303, 1)])


if __name__ == "__main__":
    unittest.main()
