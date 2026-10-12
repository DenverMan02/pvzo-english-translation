"""Real loopback HTTP regression tests for the launcher's embedded GM pages."""
from __future__ import annotations

import gc
import html
import http.client
import http.server
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

SERVER_SOURCE = Path(__file__).resolve().parents[1]
if str(SERVER_SOURCE) not in sys.path:
    sys.path.insert(0, str(SERVER_SOURCE))

import admin_server as admin
import player_store as store


class QuietHandler(admin.AdminHandler):
    def log_message(self, *_args):
        pass


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ('href', 'action') and value and value.startswith('/') and not value.startswith('//'):
                self.urls.append(value)


class AdminLauncherEmbedTests(unittest.TestCase):
    def setUp(self):
        qa_root = SERVER_SOURCE.parent.parent / 'qa'
        qa_root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='gm-embed-test-', dir=qa_root)
        self.old_db = store.DB_PATH
        store.set_db_path(str(Path(self.tmp.name) / 'players.db'))
        store.init_db()
        store.create_new_player(101)
        self.origin = 'http://127.0.0.1:43817'
        self.origin_patch = patch.dict(os.environ, {'PVZ_LAUNCHER_ORIGIN': self.origin})
        self.origin_patch.start()
        self.port_patch = patch.object(admin, '_port_open', return_value=False)
        self.port_patch.start()
        self.guard_patch = patch.object(admin, 'guard_blocks_writes', return_value=False)
        self.guard_patch.start()
        self.backup_patch = patch.object(admin, 'BACKUP_DIR', str(Path(self.tmp.name) / 'backups'))
        self.backup_patch.start()
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()
        store.set_db_path(self.old_db)
        self.backup_patch.stop()
        self.port_patch.stop()
        self.guard_patch.stop()
        self.origin_patch.stop()
        gc.collect()
        self.tmp.cleanup()

    def request(self, path, fields=None, headers=None, csrf=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            request_headers = dict(headers or {})
            if fields is None:
                connection.request('GET', path, headers=request_headers)
            else:
                data = dict(fields)
                if csrf:
                    data.setdefault('csrf', admin.CSRF_TOKEN)
                request_headers['Content-Type'] = 'application/x-www-form-urlencoded'
                connection.request('POST', path, urlencode(data), request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode('utf-8')
        finally:
            connection.close()

    def edit_form(self, number=101, *, theme='', section='attributes'):
        status, _, body = self.request('/player?' + urlencode({'uin': number, 'embedded': '1', 'theme': theme, 'section': section}))
        self.assertEqual(status, 200)
        match = re.search(r'<form\b[^>]*action="(/player/update[^\"]*)"[^>]*>(.*?)</form>', body, re.S)
        self.assertIsNotNone(match)
        self.last_edit_action = html.unescape(match[1])
        inputs = {
            name: html.unescape(value)
            for name, value in re.findall(r'<input\b[^>]*name="([^\"]+)"[^>]*value="([^\"]*)"', match[2])
        }
        baseline = json.loads(inputs['baseline'])
        values = {name: baseline[name] for name, *_ in admin.EDIT_FIELDS}
        values.update(uin=number, vip_level=baseline['vip_level'], td_privilege=baseline['td_privilege'],
                      baseline=inputs['baseline'], baseline_sig=inputs['baseline_sig'])
        return values, baseline

    def assert_error_redirect(self, response):
        status, headers, _ = response
        self.assertEqual(status, 303)
        query = parse_qs(urlsplit(headers['Location']).query)
        self.assertEqual(query['kind'], ['error'])
        return query['msg'][0]

    def test_launcher_origin_accepts_only_explicit_loopback_http_origin(self):
        self.assertEqual(admin._launcher_origin(), self.origin)
        invalid = ('', 'https://127.0.0.1:43817', 'http://evil.example:43817',
                   'http://127.0.0.1.evil.example:43817', 'http://user@127.0.0.1:43817',
                   'http://127.0.0.1:43817/path', 'http://127.0.0.1:43817/?token=abc',
                   'http://127.0.0.1:43817/#anything', 'http://127.0.0.1:0',
                   'http://localhost:43817', 'http://127.0.0.1')
        for value in invalid:
            with self.subTest(value=value), patch.dict(os.environ, {'PVZ_LAUNCHER_ORIGIN': value}):
                self.assertEqual(admin._launcher_origin(), '')

    def test_embedded_frame_policy_allows_exact_configured_launcher(self):
        status, headers, body = self.request('/?embedded=1')
        self.assertEqual(status, 200)
        self.assertNotIn('X-Frame-Options', headers)
        self.assertIn('frame-ancestors ' + self.origin, headers['Content-Security-Policy'])
        self.assertIn('<body class="embedded">', body)
        self.assertNotIn('frame-ancestors *', headers['Content-Security-Policy'])

    def test_standalone_and_unconfigured_pages_cannot_be_iframed(self):
        for path, configured in (('/', self.origin), ('/?embedded=1', ''), ('/?embedded=1', 'http://evil.example:43817')):
            with self.subTest(path=path, configured=configured), patch.dict(os.environ, {'PVZ_LAUNCHER_ORIGIN': configured}):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers['X-Frame-Options'], 'DENY')
                self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_embedded_navigation_and_post_redirect_preserve_embedding(self):
        status, _, body = self.request('/player?uin=101&embedded=1&section=inventory&theme=dark')
        self.assertEqual(status, 200)
        self.assertIn('embedded dark-theme', body)
        parser = NavigationParser()
        parser.feed(body)
        self.assertTrue(parser.urls)
        for url in parser.urls:
            with self.subTest(url=url):
                self.assertEqual(parse_qs(urlsplit(url).query).get('embedded'), ['1'])
        self.assertIn('name="embedded" value="1"', body)
        values, _ = self.edit_form()
        response = self.request('/player/update?embedded=1', values)
        self.assertEqual(response[0], 303)
        self.assertEqual(parse_qs(urlsplit(response[1]['Location']).query)['embedded'], ['1'])

    def test_hmac_tampering_is_rejected_before_save(self):
        values, baseline = self.edit_form()
        baseline['coin'] += 99
        values['baseline'] = json.dumps(baseline)
        values['coin'] = baseline['coin'] + 1
        before = admin._player(101)
        message = self.assert_error_redirect(self.request('/player/update?embedded=1', values))
        self.assertIn('过期', message)
        self.assertEqual(admin._player(101), before)

    def test_signed_snapshot_for_different_account_is_rejected(self):
        store.create_new_player(102)
        values, _ = self.edit_form(102)
        values['uin'] = 101
        before = admin._player(101)
        message = self.assert_error_redirect(self.request('/player/update?embedded=1', values))
        self.assertIn('不匹配', message)
        self.assertEqual(admin._player(101), before)

    def test_noop_form_preserves_save_and_membership_expiry(self):
        store.save_player(101, vip_level=2, vip_card_id=503, vip_endtime=1900000000,
                          td_privilege=1, td_privilege_endtime=1900000010)
        values, _ = self.edit_form()
        before = admin._player(101)
        status, headers, _ = self.request('/player/update?embedded=1', values)
        self.assertEqual(status, 303)
        self.assertNotIn('kind=error', headers['Location'])
        self.assertEqual(admin._player(101), before)

    def test_incremental_save_does_not_overwrite_unchanged_live_field(self):
        values, baseline = self.edit_form()
        store.save_player(101, sun=54321)
        values['coin'] = baseline['coin'] + 10
        status, headers, _ = self.request('/player/update?embedded=1', values)
        self.assertEqual(status, 303)
        self.assertNotIn('kind=error', headers['Location'])
        latest = admin._player(101)
        self.assertEqual(latest['coin'], baseline['coin'] + 10)
        self.assertEqual(latest['sun'], 54321)

    def test_stale_changed_field_rejects_entire_multi_field_submission(self):
        values, baseline = self.edit_form()
        store.save_player(101, coin=baseline['coin'] + 20)
        values.update(coin=baseline['coin'] + 10, name='拒绝覆盖')
        before = admin._player(101)
        message = self.assert_error_redirect(self.request('/player/update?embedded=1', values))
        self.assertIn('其他操作修改', message)
        self.assertEqual(admin._player(101), before)

    def test_membership_renewal_conflict_cannot_be_overwritten(self):
        for membership, expiry in (('vip_level', 'vip_endtime'), ('td_privilege', 'td_privilege_endtime')):
            with self.subTest(membership=membership):
                values, baseline = self.edit_form()
                store.save_player(101, **{expiry: 1950000000})
                values[membership] = 1 if baseline[membership] == 0 else 0
                before = admin._player(101)
                message = self.assert_error_redirect(self.request('/player/update?embedded=1', values))
                self.assertIn('会员有效期', message)
                self.assertEqual(admin._player(101), before)

    def test_unmodified_membership_renewal_survives_currency_edit(self):
        values, baseline = self.edit_form()
        store.save_player(101, vip_card_id=501, vip_endtime=1950000000)
        values['coin'] = baseline['coin'] + 10
        status, headers, _ = self.request('/player/update?embedded=1', values)
        self.assertEqual(status, 303)
        self.assertNotIn('kind=error', headers['Location'])
        self.assertEqual(admin._player(101)['vip_endtime'], 1950000000)
        self.assertEqual(admin._player(101)['vip_card_id'], 501)

    def test_running_game_renders_read_only_and_rejects_update_new_and_grant(self):
        values, baseline = self.edit_form()
        values['coin'] = baseline['coin'] + 10
        before = admin._player(101)
        bag = store.get_item_count(101, 4001)
        with patch.object(admin, '_port_open', return_value=True):
            status, _, body = self.request('/player?uin=101&embedded=1')
            self.assertEqual(status, 200)
            self.assertIn('当前为只读', body)
            self.assertIn('<fieldset disabled', body)
            for path, fields in (
                ('/player/update?embedded=1', values),
                ('/player/new?embedded=1', {'uin': '102'}),
                ('/player/grant-item?embedded=1', {'uin': '101', 'item_id': '4001', 'quantity': '1'}),
            ):
                with self.subTest(path=path):
                    message = self.assert_error_redirect(self.request(path, fields))
                    self.assertIn('停止游戏', message)
        self.assertEqual(admin._player(101), before)
        self.assertIsNone(admin._player(102))
        self.assertEqual(store.get_item_count(101, 4001), bag)

    def test_host_whitelist_and_csrf_block_untrusted_requests(self):
        self.assertEqual(self.request('/?embedded=1', headers={'Host': 'evil.example'})[0], 421)
        for csrf in ('', 'wrong-token'):
            with self.subTest(csrf=csrf):
                message = self.assert_error_redirect(self.request('/player/new?embedded=1', {'uin': '102', 'csrf': csrf}, csrf=False))
                self.assertIn('过期', message)
        self.assertIsNone(admin._player(102))

    def test_dark_embedded_attribute_submit_keeps_theme_and_current_tab(self):
        values, baseline = self.edit_form(theme='dark')
        values['coin'] = baseline['coin'] + 10
        status, headers, _ = self.request(self.last_edit_action, values)
        self.assertEqual(status, 303)
        destination = headers['Location']
        query = parse_qs(urlsplit(destination).query)
        self.assertEqual(query['embedded'], ['1'])
        self.assertEqual(query['theme'], ['dark'])
        self.assertEqual(query['section'], ['attributes'])
        self.assertNotIn('kind=error', destination)
        self.assertEqual(admin._player(101)['coin'], baseline['coin'] + 10)
        status, _, body = self.request(destination)
        self.assertEqual(status, 200)
        self.assertIn('<body class="embedded dark-theme">', body)
        self.assertRegex(body, r'<a class="active" href="[^\"]*section=attributes[^\"]*">基础属性</a>')

    def test_dark_cards_validation_redirect_keeps_cards_tab(self):
        status, _, body = self.request('/player?uin=101&embedded=1&theme=dark&section=cards')
        self.assertEqual(status, 200)
        match = re.search(r'<form\b[^>]*action="(/player/cards[^\"]*)"[^>]*>', body)
        self.assertIsNotNone(match)
        action = html.unescape(match[1])
        response = self.request(action, {'uin': '101', 'uid': '', 'level': '1'})
        self.assertIn('没有找到', self.assert_error_redirect(response))
        destination = response[1]['Location']
        query = parse_qs(urlsplit(destination).query)
        self.assertEqual(query['theme'], ['dark'])
        self.assertEqual(query['section'], ['cards'])
        status, _, redirected_body = self.request(destination)
        self.assertEqual(status, 200)
        self.assertIn('<body class="embedded dark-theme">', redirected_body)
        self.assertRegex(redirected_body, r'<a class="active" href="[^\"]*section=cards[^\"]*">植物卡</a>')

    def test_dark_inventory_link_grant_and_return_keep_theme_and_inventory_tab(self):
        status, _, body = self.request('/player?uin=101&embedded=1&theme=dark&section=inventory')
        self.assertEqual(status, 200)
        match = re.search(r'href="(/items\?[^\"]*)"', body)
        self.assertIsNotNone(match)
        destination = html.unescape(match[1])
        status, _, catalog_body = self.request(destination)
        self.assertEqual(status, 200)
        self.assertIn('<body class="embedded dark-theme">', catalog_body)
        match = re.search(r'<form\b[^>]*action="(/player/grant-item[^\"]*)"[^>]*>', catalog_body)
        self.assertIsNotNone(match)
        grant_action = html.unescape(match[1])
        before = store.get_item_count(101, 4001)
        status, headers, _ = self.request(grant_action, {'uin': '101', 'item_id': '4001', 'quantity': '1'})
        self.assertEqual(status, 303)
        self.assertNotIn('kind=error', headers['Location'])
        self.assertEqual(store.get_item_count(101, 4001), before + 1)
        query = parse_qs(urlsplit(headers['Location']).query)
        self.assertEqual(query['theme'], ['dark'])
        self.assertEqual(query['section'], ['inventory'])
        status, _, refreshed_catalog = self.request(headers['Location'])
        self.assertEqual(status, 200)
        self.assertIn('<body class="embedded dark-theme">', refreshed_catalog)
        back = re.search(r'href="(/player\?[^\"]*)"', refreshed_catalog)
        self.assertIsNotNone(back)
        status, _, returned_player = self.request(html.unescape(back[1]))
        self.assertEqual(status, 200)
        self.assertIn('<body class="embedded dark-theme">', returned_player)
        self.assertRegex(returned_player, r'<a class="active" href="[^\"]*section=inventory[^\"]*">背包道具</a>')

    def test_embedded_navigation_can_switch_tab_without_context_overriding_it(self):
        status, _, body = self.request('/player?uin=101&embedded=1&theme=dark&section=cards')
        self.assertEqual(status, 200)
        parser = NavigationParser()
        parser.feed(body)
        inventory = next(url for url in parser.urls if urlsplit(url).path == '/player'
                         and parse_qs(urlsplit(url).query).get('section') == ['inventory'])
        status, _, body = self.request(inventory)
        self.assertEqual(status, 200)
        self.assertIn('<body class="embedded dark-theme">', body)
        self.assertRegex(body, r'<a class="active" href="[^\"]*section=inventory[^\"]*">背包道具</a>')

    def test_dark_get_search_forms_keep_context_when_browser_replaces_action_query(self):
        for source, search, expected_section in (
            ('/?embedded=1&theme=dark', '101', None),
            ('/items?uin=101&embedded=1&theme=dark&section=inventory', '4001', 'inventory'),
        ):
            with self.subTest(source=source):
                status, _, body = self.request(source)
                self.assertEqual(status, 200)
                form = re.search(r'<form\b[^>]*method="get"[^>]*action="([^\"]*)"[^>]*>(.*?)</form>', body, re.S)
                self.assertIsNotNone(form)
                controls = {name: html.unescape(value) for name, value in re.findall(
                    r'<input\b[^>]*name="([^\"]+)"[^>]*value="([^\"]*)"', form[2])}
                controls['q'] = search
                if expected_section:
                    self.assertIn('name="item_kind"', form[2])
                    controls['item_kind'] = ''
                # This models actual GET form submission: only form controls,
                # rather than the old action query, form the new query string.
                submitted = urlsplit(html.unescape(form[1]))._replace(query=urlencode(controls)).geturl()
                status, _, searched = self.request(submitted)
                self.assertEqual(status, 200)
                self.assertIn('<body class="embedded dark-theme">', searched)
                self.assertIn('value="%s"' % search, searched)
                if expected_section:
                    back = re.search(r'href="(/player\?[^\"]*)"', searched)
                    self.assertIsNotNone(back)
                    status, _, returned = self.request(html.unescape(back[1]))
                    self.assertEqual(status, 200)
                    self.assertRegex(returned, r'<a class="active" href="[^\"]*section=inventory[^\"]*">背包道具</a>')

    def test_start_stop_guard_locks_gm_before_game_port_is_open(self):
        values, baseline = self.edit_form(theme='dark')
        values['coin'] = baseline['coin'] + 10
        before = admin._player(101)
        bag = store.get_item_count(101, 4001)
        with patch.object(admin, 'guard_blocks_writes', return_value=True):
            status, _, body = self.request('/player?uin=101&embedded=1&theme=dark')
            self.assertEqual(status, 200)
            self.assertIn('当前为只读', body)
            self.assertIn('<fieldset disabled', body)
            for route, fields in (
                (self.last_edit_action, values),
                ('/player/new?embedded=1&theme=dark', {'uin': '102'}),
                ('/player/grant-item?embedded=1&theme=dark', {'uin': '101', 'item_id': '4001', 'quantity': '1'}),
            ):
                with self.subTest(route=route):
                    message = self.assert_error_redirect(self.request(route, fields))
                    self.assertIn('停止游戏', message)
        self.assertEqual(admin._player(101), before)
        self.assertIsNone(admin._player(102))
        self.assertEqual(store.get_item_count(101, 4001), bag)


if __name__ == '__main__':
    unittest.main(verbosity=2)
