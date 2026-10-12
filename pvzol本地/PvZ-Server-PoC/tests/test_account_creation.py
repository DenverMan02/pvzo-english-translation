"""New-account creation and login use only disposable SQLite databases."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import player_store as store
import pvz_poc_server as game


class MemoryReader:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    async def read(self, _size):
        return next(self.chunks, b'')


class MemoryWriter:
    def __init__(self):
        self.output = bytearray()
        self.closed = False

    def get_extra_info(self, name):
        return ('127.0.0.1', 9001 if name == 'sockname' else 12345)

    def write(self, data):
        self.output.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


def client_packet(uin, cmd_name, cmd_data=b''):
    body = game.xor7(game.build_cmdcommon(cmd_name, cmd_data))
    skey = b'test\0'
    user = uin.to_bytes(4, 'big') + len(skey).to_bytes(4, 'big') + skey
    return (bytes([game.TWEB_MAGIC])
            + (12 + len(user) + len(body)).to_bytes(4, 'big')
            + len(body).to_bytes(4, 'big')
            + bytes([1, game.CMD_UPLOAD, 1]) + user + body)


class AccountCreationTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = store.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / 'account-test.db')
        store.set_db_path(self.db)
        store.init_db()

    def tearDown(self):
        store.close_thread_db()
        store.set_db_path(self.previous_db)
        self.tmp.cleanup()

    def saved_row(self, uin):
        with closing(sqlite3.connect(self.db)) as con:
            con.row_factory = sqlite3.Row
            row = con.execute('SELECT * FROM players WHERE uin=?', (uin,)).fetchone()
            return dict(row) if row else None

    def login(self, uin, requests):
        writer = MemoryWriter()
        # These systems are unrelated to role creation; avoid giving test accounts
        # login gifts. The real handshake, protobuf encoding, and Init_SC remain.
        values = {
            'rebuild_td_roster': [], 'ensure_player_cards': None,
            'sanitize_td_progress': None, 'baptize_login_cleanup': None,
            'card_swap_login_grant': None, 'normalize_level': False,
            'vip_login_messages': [], 'active_vip_level': 0,
            'td_privilege_login_messages': [], 'td_privilege_active': False,
            'diamond_login_messages': [], 'make_server_config_entries': b'',
            'puppet_skill_val': 0, 'puppet_energy': 0,
        }
        with ExitStack() as stack:
            for name, value in values.items():
                stack.enter_context(patch.object(game, name, return_value=value))
            stack.enter_context(patch.object(game, 'log'))
            asyncio.run(game.handle_client(
                MemoryReader([client_packet(uin, name, data) for name, data in requests]),
                writer))
        self.assertTrue(writer.closed)
        replies = []
        for packet in game.frame_packets(writer.output):
            length = int.from_bytes(packet[5:9], 'big')
            replies.append(game.parse_cmdcommon(packet[-length:]))
        return replies

    def assert_tutorial_init(self, reply):
        self.assertEqual(reply['cmdName'], 'PVZ.Cmd.Cmd_Init_SC')
        latest = game.parse_repeated_submessages(reply['cmdData'], 7)
        self.assertEqual(len(latest), 1)
        self.assertEqual(game.parse_varint_fields(latest[0]), {1: 0, 2: 1, 3: 1})

    def test_fresh_account_has_starter_state_and_can_reload(self):
        player = store.create_new_player('700009111')
        self.assertEqual(player['uin'], 700009111)
        self.assertEqual(player['role_id'], 700009111)
        self.assertEqual(player['level'], 1)
        self.assertEqual(player['coin'], 500)
        self.assertEqual(player['role_created'], 0)
        self.assertEqual(player['tutorial_initialized'], 1)
        self.assertEqual(player['tutorial_active'], 1)
        self.assertEqual(store.get_player_plants(player['uin']), store.STARTER_PLANTS)
        again, created = store.get_or_create_player(player['uin'])
        self.assertFalse(created)
        self.assertEqual(again['role_created'], 0)
        self.assertEqual(again['tutorial_active'], 1)

    def test_duplicate_creation_preserves_existing_save(self):
        player = store.create_new_player('001')
        store.save_player(player['uin'], name='已有角色', level=87, coin=23456,
                          last_login=123, role_created=1, tutorial_active=0)
        store.unlock_plant(player['uin'], 1400)
        before = self.saved_row(1)
        plants_before = store.get_player_plants(1)
        with self.assertRaises(ValueError):
            store.create_new_player('1')
        self.assertEqual(self.saved_row(1), before)
        self.assertEqual(store.get_player_plants(1), plants_before)

    def test_numeric_boundaries_and_leading_zeroes(self):
        self.assertEqual(store.create_new_player('00001')['uin'], 1)
        self.assertEqual(store.create_new_player(4294967295)['uin'], 4294967295)
        self.assertEqual(self.saved_row(4294967295)['role_id'], 4294967295)

    def test_invalid_account_values_do_not_create_rows(self):
        for value in ('', '0', 0, -1, '4294967296', '１２３', '١٢٣',
                      ' 1', '1 ', '+1', '1.0', 1.0, True, None, '1&uin=2',
                      '9' * 5000):
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValueError):
                store.create_new_player(value)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM players').fetchone()[0], 0)

    def test_concurrent_creation_accepts_only_one_and_never_replaces_it(self):
        barrier = threading.Barrier(2)

        def create():
            try:
                barrier.wait(timeout=5)
                return ('created', store.create_new_player(700009112)['uin'])
            except ValueError:
                return ('duplicate', None)
            finally:
                store.close_thread_db()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create), pool.submit(create)]
            outcomes = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(outcome[0] for outcome in outcomes), ['created', 'duplicate'])
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM players').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM plants').fetchone()[0],
                             len(store.STARTER_PLANTS))

    def test_new_role_named_in_init_enters_tutorial_and_relogin_is_old_user(self):
        uin = 700009113
        store.create_new_player(uin)
        replies = self.login(uin, [
            ('PVZ.Cmd.Cmd_CheckOldUser_CS', b''),
            ('PVZ.Cmd.Cmd_Init_CS', game.pb_string(10, '新建测试角色')),
        ])
        self.assertEqual([r['cmdName'] for r in replies],
                         ['PVZ.Cmd.Cmd_CheckOldUser_SC', 'PVZ.Cmd.Cmd_Init_SC'])
        self.assertEqual(game.parse_varint_fields(replies[0]['cmdData'])[1], 0)
        self.assert_tutorial_init(replies[1])
        self.assertEqual(self.saved_row(uin)['name'], '新建测试角色')
        self.assertEqual(self.saved_row(uin)['role_created'], 1)
        again = self.login(uin, [
            ('PVZ.Cmd.Cmd_CheckOldUser_CS', b''), ('PVZ.Cmd.Cmd_Init_CS', b''),
        ])
        self.assertEqual(game.parse_varint_fields(again[0]['cmdData'])[1], 1)
        self.assert_tutorial_init(again[1])

    def test_separate_role_name_request_is_acknowledged_before_init(self):
        uin = 700009114
        store.create_new_player(uin)
        replies = self.login(uin, [
            ('PVZ.Cmd.Cmd_CheckOldUser_CS', b''),
            ('PVZ.Cmd.Cmd_SetRoleName_CS', game.pb_string(1, '独立命名角色')
             + game.pb_varint_field(2, 1)),
            ('PVZ.Cmd.Cmd_Init_CS', b''),
        ])
        self.assertEqual([r['cmdName'] for r in replies], [
            'PVZ.Cmd.Cmd_CheckOldUser_SC', 'PVZ.Cmd.Cmd_SetRoleName_SC',
            'PVZ.Cmd.Cmd_Init_SC',
        ])
        self.assertEqual(game.parse_string_field(replies[1]['cmdData'], 1), '独立命名角色')
        self.assertEqual(game.parse_varint_fields(replies[1]['cmdData'])[2], 1)
        self.assertEqual(self.saved_row(uin)['role_created'], 1)
        self.assert_tutorial_init(replies[2])


    def test_tutorial_failure_resumes_and_success_persists_normal_progress(self):
        uin = 700009115
        player = store.create_new_player(uin)
        store.save_player(uin, role_created=1)
        player['role_created'] = 1
        request = (game.pb_varint_field(1, 0) + game.pb_varint_field(2, 1)
                   + game.pb_varint_field(3, 1))
        with patch.object(game, 'log'):
            game.handle_td_report_result(request + game.pb_varint_field(4, 0), player)
        self.assertEqual(self.saved_row(uin)['tutorial_active'], 1)
        with patch.object(game, 'log'):
            game.handle_td_report_result(request + game.pb_varint_field(4, 1), player)
        self.assertEqual(self.saved_row(uin)['tutorial_active'], 0)
        replies = self.login(uin, [
            ('PVZ.Cmd.Cmd_CheckOldUser_CS', b''), ('PVZ.Cmd.Cmd_Init_CS', b''),
        ])
        latest = game.parse_repeated_submessages(replies[1]['cmdData'], 7)
        self.assertEqual(game.parse_varint_fields(latest[0]), {1: 1, 2: 1, 3: 1})
        self.assertEqual(game.parse_varint_fields(replies[0]['cmdData'])[1], 1)

if __name__ == '__main__':
    unittest.main(verbosity=2)


