"""GM reset/rename regression tests against disposable SQLite saves only."""
import hashlib
import hmac
import http.client
import http.server
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import admin_server as admin
import player_store as store
import profile_editor as profile


class QuietHandler(admin.AdminHandler):
    def log_message(self, *_args):
        pass


class AdminRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.old_db = store.DB_PATH
        self.old_backups = admin.BACKUP_DIR
        store.set_db_path(str(self.base / 'players.db'))
        admin.BACKUP_DIR = str(self.base / 'backups')
        store.init_db()
        for uin, name, role_id in ((101, '会长甲', 9501), (202, '成员乙', 95202),
                                    (303, '副会丙', 95303), (404, '成员丁', 95404)):
            store.get_or_create_player(uin)
            store.save_player(uin, name=name, role_id=role_id, coin=123456 + uin)
        self.stop_guard = patch.object(admin, 'guard_blocks_writes', return_value=False)
        self.port_guard = patch.object(admin, '_port_open', return_value=False)
        self.stop_guard.start()
        self.port_guard.start()
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.thread.join(3)
            self.server.server_close()
        self.stop_guard.stop()
        self.port_guard.stop()
        admin.BACKUP_DIR = self.old_backups
        store.set_db_path(self.old_db)
        self.tmp.cleanup()

    def sql(self, statement, args=()):
        with store._db() as con:
            return [tuple(row) for row in con.execute(statement, args)]

    def snapshot(self, path=None):
        con = sqlite3.connect(path or store.DB_PATH)
        try:
            names = [row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: sorted(con.execute('SELECT * FROM "' + name.replace('"', '""') + '"'), key=repr)
                    for name in names}
        finally:
            con.close()

    def guild(self, members):
        self.sql('CREATE TABLE local_guilds(guild_id INTEGER PRIMARY KEY,name TEXT,exp INTEGER)')
        self.sql("INSERT INTO local_guilds VALUES(10,'本会',45678),(30,'其他会',87654)")
        self.sql('CREATE TABLE local_guild_members(guild_id INTEGER,uin INTEGER,role_id INTEGER,name TEXT,position INTEGER,joined_at INTEGER,PRIMARY KEY(guild_id,uin))')
        for uin, position, joined in members:
            player = admin._player(uin)
            self.sql('INSERT INTO local_guild_members VALUES(10,?,?,?,?,?)',
                     (uin, player['role_id'], player['name'], position, joined))
        self.sql('CREATE TABLE local_guild_applications(guild_id INTEGER,uin INTEGER,role_id INTEGER)')
        self.sql('INSERT INTO local_guild_applications VALUES(10,909,9909),(30,919,9919)')
        self.sql('CREATE TABLE local_guild_dungeon(guild_id INTEGER PRIMARY KEY,hp INTEGER)')
        self.sql('INSERT INTO local_guild_dungeon VALUES(10,12345),(30,54321)')
        self.sql('CREATE TABLE local_guild_invites(guild_id INTEGER,inviter_uin INTEGER,invitee_uin INTEGER)')
        self.sql('INSERT INTO local_guild_invites VALUES(10,202,909),(30,303,919)')
        self.sql('CREATE TABLE local_feature_state(uin INTEGER,feature TEXT,data TEXT,PRIMARY KEY(uin,feature))')
        for values in ((-10, 'guild_leader', '{"leader":9501,"target":95202}'),
                       (-10, 'guild_hp_1_1_0', '{"hp":12345}'),
                       (-10, 'guild_hp_1_1_0_damage', '{"9501":10,"95202":20}'),
                       (-30, 'guild_hp_1_1_0_damage', '{"95303":99}'),
                       (101, 'personal', '{"progress":8}'),
                       (202, 'personal', '{"progress":9}')):
            self.sql('INSERT INTO local_feature_state VALUES(?,?,?)', values)

    def test_reset_leader_elects_deterministic_successor_and_keeps_shared_progress(self):
        # Match the normal leave/account deletion policy; ordinary position 2
        # precedes deputy position 3, and oldest/tied role ID wins.
        self.guild(((101, 1, 5), (404, 2, 10), (303, 3, 7), (202, 2, 10)))
        before = self.snapshot()
        backup, affected = admin._reset_player(101)
        self.assertEqual(self.snapshot(backup), before)
        self.assertIn(('local_guild_members', 1), affected)
        self.assertEqual(self.sql('SELECT uin,position FROM local_guild_members ORDER BY uin'),
                         [(202, 1), (303, 3), (404, 2)])
        self.assertEqual(self.sql('SELECT guild_id,exp FROM local_guilds ORDER BY guild_id'),
                         [(10, 45678), (30, 87654)])
        self.assertEqual(self.sql("SELECT data FROM local_feature_state WHERE uin=-10 AND feature='guild_hp_1_1_0'"),
                         [('{"hp":12345}',)])
        self.assertEqual(self.sql("SELECT data FROM local_feature_state WHERE uin=-10 AND feature='guild_hp_1_1_0_damage'"),
                         [('{"95202": 20}',)])
        self.assertFalse(self.sql("SELECT 1 FROM local_feature_state WHERE feature='guild_leader'"))
        self.assertEqual(admin._player(101)['role_id'], 101)
        self.assertEqual(admin._player(101)['coin'], store.DEFAULTS['coin'])
        for uin in (202, 303, 404):
            self.assertEqual(admin._player(uin)['coin'], 123456 + uin)

    def test_reset_deputy_preserves_leader_and_other_members(self):
        self.guild(((101, 3, 5), (202, 1, 10), (303, 2, 8)))
        admin._reset_player(101)
        self.assertEqual(self.sql('SELECT uin,position FROM local_guild_members ORDER BY uin'),
                         [(202, 1), (303, 2)])
        self.assertEqual(self.sql('SELECT COUNT(*) FROM local_guilds WHERE guild_id=10'), [(1,)])

    def test_reset_ordinary_member_preserves_leader_and_deputy(self):
        self.guild(((101, 2, 5), (202, 1, 10), (303, 3, 8)))
        admin._reset_player(101)
        self.assertEqual(self.sql('SELECT uin,position FROM local_guild_members ORDER BY uin'),
                         [(202, 1), (303, 3)])

    def test_reset_last_member_removes_only_its_empty_guild_and_shared_states(self):
        self.guild(((101, 1, 5),))
        admin._reset_player(101)
        for table in ('local_guilds', 'local_guild_applications', 'local_guild_dungeon', 'local_guild_invites'):
            self.assertFalse(self.sql('SELECT * FROM ' + table + ' WHERE guild_id=10'))
            self.assertTrue(self.sql('SELECT * FROM ' + table + ' WHERE guild_id=30'))
        self.assertFalse(self.sql('SELECT * FROM local_feature_state WHERE uin=-10'))
        self.assertEqual(self.sql('SELECT data FROM local_feature_state WHERE uin=-30'), [('{"95303":99}',)])
        self.assertEqual(self.sql('SELECT data FROM local_feature_state WHERE uin=202'), [('{"progress":9}',)])

    def test_reset_without_guild_tables_creates_native_starter_and_clears_only_target(self):
        store.add_item(101, 999, 7)
        store.add_item(202, 999, 8)
        before_other = admin._player(202)
        admin._reset_player(101)
        self.assertEqual(store.get_item_count(101, 999), 0)
        self.assertEqual(store.get_item_count(202, 999), 8)
        self.assertEqual(admin._player(202), before_other)
        self.assertEqual(self.sql('SELECT plant_id FROM plants WHERE uin=101 ORDER BY plant_id'),
                         [(plant,) for plant in sorted(store.STARTER_PLANTS)])

    def test_native_starter_failure_rolls_back_deletion_election_and_shared_cleanup(self):
        for members in (((101, 1, 5), (202, 2, 10)), ((101, 1, 5),)):
            with self.subTest(members=members):
                if self.sql("SELECT name FROM sqlite_master WHERE name='local_guilds'"):
                    for table in ('local_guilds', 'local_guild_members', 'local_guild_applications',
                                  'local_guild_dungeon', 'local_guild_invites', 'local_feature_state'):
                        self.sql('DROP TABLE ' + table)
                self.guild(members)
                before = self.snapshot()
                original_create = store.get_or_create_player
                def fail_after_create(uin):
                    original_create(uin)
                    raise RuntimeError('injected starter failure')
                with patch.object(store, 'get_or_create_player', side_effect=fail_after_create):
                    with self.assertRaisesRegex(RuntimeError, 'starter failure'):
                        admin._reset_player(101)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(list((self.base / 'backups').glob('*.db'))), 2)

    def test_missing_player_reset_rejects_without_backup_or_new_account(self):
        before = self.snapshot()
        with self.assertRaisesRegex(admin.ValidationError, '不存在'):
            admin._reset_player(999)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.base / 'backups').exists())

    def test_running_game_reset_is_blocked_before_backup(self):
        before = self.snapshot()
        with patch.object(admin, 'guard_blocks_writes', return_value=True):
            with self.assertRaisesRegex(admin.ValidationError, '停止游戏'):
                admin._reset_player(101)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.base / 'backups').exists())

    def test_game_started_during_backup_rejects_before_mutation(self):
        before = self.snapshot()
        with patch.object(admin, 'guard_blocks_writes', side_effect=[False, True]):
            with self.assertRaisesRegex(admin.ValidationError, '停止游戏'):
                admin._reset_player(101)
        self.assertEqual(self.snapshot(), before)
        backups = list((self.base / 'backups').glob('*.db'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.snapshot(backups[0]), before)

    def rename_baseline(self):
        player = admin._player(101)
        keys = [field[0] for field in admin.EDIT_FIELDS] + ['vip_level', 'vip_card_id', 'vip_endtime',
                                                       'td_privilege', 'td_privilege_endtime']
        return {key: player[key] for key in keys} | {'uin': 101}

    def social_names(self):
        store.add_friend(202, 9501, '会长甲', 1200, 1, 0, 0, 0, 0)
        store.add_friend(303, 9501, '会长甲', 1200, 1, 0, 0, 0, 0)
        store.add_friend(202, 101, '其他角色', 1200, 1, 0, 0, 0, 0)
        self.sql('CREATE TABLE local_guild_members(uin INTEGER PRIMARY KEY,name TEXT)')
        self.sql("INSERT INTO local_guild_members VALUES(101,'会长甲'),(202,'成员乙')")

    def test_duplicate_and_unicode_casefold_renames_reject_all_changed_attributes(self):
        self.social_names()
        for existing, submitted in (('成员乙', '成员乙'), ('Alice', 'aLiCe'), ('Straße', 'STRASSE'), ('Σ甲', 'ς甲')):
            with self.subTest(existing=existing, submitted=submitted):
                store.save_player(202, name=existing)
                baseline = self.rename_baseline()
                before = self.snapshot()
                with self.assertRaisesRegex(admin.ValidationError, '名称已被'):
                    admin._save_incremental(101, {'name': submitted, 'coin': baseline['coin'] + 10}, baseline)
                self.assertEqual(self.snapshot(), before)

    def test_valid_rename_syncs_real_role_id_and_guild_uin_in_same_transaction(self):
        self.social_names()
        baseline = self.rename_baseline()
        store.save_player(101, sun=8765)
        admin._save_incremental(101, {'name': '新角色', 'coin': baseline['coin'] + 10}, baseline)
        self.assertEqual(admin._player(101)['name'], '新角色')
        self.assertEqual(admin._player(101)['coin'], baseline['coin'] + 10)
        self.assertEqual(admin._player(101)['sun'], 8765)
        self.assertEqual(self.sql('SELECT friend_name FROM friends WHERE friend_role_id=9501'),
                         [('新角色',), ('新角色',)])
        self.assertEqual(self.sql('SELECT friend_name FROM friends WHERE friend_role_id=101'), [('其他角色',)])
        self.assertEqual(self.sql('SELECT uin,name FROM local_guild_members ORDER BY uin'),
                         [(101, '新角色'), (202, '成员乙')])

    def test_unchanged_legacy_duplicate_names_allow_incremental_currency_edit(self):
        store.save_player(101, name='LegacyLongName')
        store.save_player(202, name='LegacyLongName')
        baseline = self.rename_baseline()
        admin._save_incremental(101, {'name': baseline['name'], 'coin': baseline['coin'] + 10}, baseline)
        self.assertEqual(admin._player(101)['name'], 'LegacyLongName')
        self.assertEqual(admin._player(202)['name'], 'LegacyLongName')
        self.assertEqual(admin._player(101)['coin'], baseline['coin'] + 10)

    def test_profile_and_gm_use_same_unicode_rename_rule(self):
        store.save_player(202, name='Straße')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '名称已被'):
            profile.save_profile(101, {'name': 'STRASSE', 'signature': '不应写入'})
        self.assertEqual(self.snapshot(), before)

    def request(self, path, fields):
        if not self.server:
            self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            client.request('POST', path, urlencode(dict(fields, csrf=admin.CSRF_TOKEN)),
                           {'Content-Type': 'application/x-www-form-urlencoded'})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode('utf-8')
        finally:
            client.close()

    def form(self, **changes):
        baseline = self.rename_baseline()
        raw = json.dumps(baseline, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        signature = hmac.new(admin.CSRF_TOKEN.encode(), raw.encode(), hashlib.sha256).hexdigest()
        return baseline | {'baseline': raw, 'baseline_sig': signature} | changes

    def test_http_duplicate_rename_is_validation_error_without_partial_write(self):
        fields = self.form(name='成员乙', coin=33333)
        before = self.snapshot()
        status, headers, _body = self.request('/player/update', fields)
        self.assertEqual(status, 303)
        query = parse_qs(urlsplit(headers['Location']).query)
        self.assertEqual(query['kind'], ['error'])
        self.assertIn('名称已被', query['msg'][0])
        self.assertEqual(self.snapshot(), before)

    def test_http_social_sync_failure_returns_500_and_rolls_back_all_fields(self):
        self.social_names()
        fields = self.form(name='新角色', coin=33333)
        before = self.snapshot()
        original_sync = profile.sync_name
        def fail_after_sync(con, player, name):
            original_sync(con, player, name)
            raise RuntimeError('injected social update failure')
        with patch.object(profile, 'sync_name', side_effect=fail_after_sync), \
             patch.object(admin.traceback, 'print_exc') as logging:
            status, _headers, body = self.request('/player/update', fields)
        self.assertEqual(status, 500)
        self.assertIn('未写入不完整数据', body)
        logging.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_http_reset_requires_confirmation_and_keeps_save_intact(self):
        before = self.snapshot()
        status, headers, _body = self.request('/player/reset', {'uin': 101, 'confirm_uin': 202, 'ack': 'yes'})
        self.assertEqual(status, 303)
        self.assertIn('kind=error', headers['Location'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.base / 'backups').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
