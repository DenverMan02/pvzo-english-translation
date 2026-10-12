"""Account visibility and GET navigation regression tests with a disposable DB."""
import gc
import html
import http.client
import http.server
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import admin_server as admin
import player_store as store


class QuietHandler(admin.AdminHandler):
    def log_message(self, *_args):
        pass


class AdminAccountListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='account-list-')
        self.old_db = store.DB_PATH
        store.set_db_path(str(Path(self.tmp.name) / 'players.db'))
        store.init_db()
        self.patches = [patch.object(admin, '_port_open', return_value=False),
                        patch.object(admin, 'guard_blocks_writes', return_value=False),
                        patch.dict(os.environ, {'PVZ_LAUNCHER_ORIGIN': 'http://127.0.0.1:43817'})]
        for item in self.patches:
            item.start()
        for number, name in ((899999, '边界玩家甲'), (1000000, '边界玩家乙'),
                             (700000204, '非常长的中文角色名字仍然横向显示'),
                             (900000, '边界机器人甲'), (900001, '现役机器人'), (999999, '边界机器人乙')):
            store.create_new_player(number)
            store.save_player(number, name=name)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()
        store.close_thread_db()
        store.set_db_path(self.old_db)
        for item in reversed(self.patches):
            item.stop()
        gc.collect()
        self.tmp.cleanup()

    def request(self, path):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            client.request('GET', path)
            response = client.getresponse()
            body = response.read().decode('utf-8')
            self.assertEqual(response.status, 200, body)
            return body, dict(response.getheaders())
        finally:
            client.close()

    def account_ids(self, body):
        return [int(number) for number in re.findall(r'data-account-uin="([0-9]+)"', body)]

    def form_fields(self, form):
        return {name: html.unescape(value) for name, value in
                re.findall(r'<input\b[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form)}

    def test_default_filters_every_reserved_robot_but_keeps_both_boundaries(self):
        body, _ = self.request('/')
        self.assertEqual(set(self.account_ids(body)), {899999, 1000000, 700000204})
        self.assertIn('已隐藏 3 个机器人账号', body)
        self.assertIn('共 3 个账号', body)
        self.assertIn('aria-checked="false"', body)
        for value in ('0', 'true', 'yes', '2'):
            body, _ = self.request('/?include_robots=' + value)
            self.assertEqual(set(self.account_ids(body)), {899999, 1000000, 700000204})
        for search in ('900000', '现役机器人'):
            body, _ = self.request('/?' + urlencode({'q': search}))
            self.assertEqual(self.account_ids(body), [])
            self.assertIn('没有匹配的账号', body)

    def test_toggle_is_a_native_get_action_and_can_show_and_hide_robots(self):
        body, _ = self.request('/?' + urlencode({'q': '边界'}))
        switch_form = re.search(r'<form method="get" action="/">(.*?)</form>', body, re.S)[1]
        fields = self.form_fields(switch_form)
        self.assertEqual(fields['q'], '边界')
        self.assertIn('role="switch"', switch_form)
        toggle_value = re.search(r'name="include_robots" value="([^"]+)"', switch_form)[1]
        fields['include_robots'] = toggle_value
        shown, headers = self.request('/?' + urlencode(fields))
        self.assertEqual(set(self.account_ids(shown)), {899999, 1000000, 900000, 999999})
        self.assertIn('aria-checked="true"', shown)
        self.assertIn('机器人账号', shown)
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertNotIn('script-src', headers['Content-Security-Policy'])
        switch_form = re.search(r'<form method="get" action="/">(.*?)</form>', shown, re.S)[1]
        fields = self.form_fields(switch_form)
        fields['include_robots'] = re.search(r'name="include_robots" value="([^"]+)"', switch_form)[1]
        hidden, _ = self.request('/?' + urlencode(fields))
        self.assertEqual(set(self.account_ids(hidden)), {899999, 1000000})

    def test_pagination_search_clear_and_embedded_dark_context_survive(self):
        for number in range(101, 133):
            store.create_new_player(number)
            store.save_player(number, name='分页角色%s' % number)
        path = '/?' + urlencode({'q': '角色', 'include_robots': '1', 'embedded': '1', 'theme': 'dark'})
        body, _ = self.request(path)
        self.assertEqual(len(self.account_ids(body)), 30)
        search_form = re.search(r'<form\b[^>]*class="account-search"[^>]*>(.*?)</form>', body, re.S)[1]
        fields = self.form_fields(search_form)
        self.assertEqual(fields['include_robots'], '1')
        self.assertEqual(fields['embedded'], '1')
        self.assertEqual(fields['theme'], 'dark')
        submitted, _ = self.request('/?' + urlencode(fields))
        self.assertIn('embedded dark-theme', submitted)
        self.assertIn('aria-checked="true"', submitted)
        next_link = html.unescape(re.search(r'href="([^"]+)">下一页', body)[1])
        next_query = parse_qs(urlsplit(next_link).query)
        self.assertEqual(next_query, {'q': ['角色'], 'page': ['2'], 'include_robots': ['1'], 'embedded': ['1'], 'theme': ['dark']})
        second, _ = self.request(next_link)
        self.assertEqual(len(self.account_ids(second)), 3)
        self.assertFalse(set(self.account_ids(body)) & set(self.account_ids(second)))
        clear_link = html.unescape(re.search(r'href="([^"]+)">全部', body)[1])
        clear_query = parse_qs(urlsplit(clear_link).query)
        self.assertNotIn('q', clear_query)
        self.assertEqual(clear_query['include_robots'], ['1'])
        self.assertEqual(clear_query['theme'], ['dark'])
        self.assertEqual(clear_query['embedded'], ['1'])

    def test_name_search_literal_wildcards_backslashes_and_html_escape(self):
        names = ('带%百分号', '带_下划线', '路径\\中文', '<img src=x onerror=alert(1)>')
        for number, name in enumerate(names, 201):
            store.create_new_player(number)
            store.save_player(number, name=name)
        for term, number in (('%', 201), ('_', 202), ('\\', 203), ('<img', 204)):
            body, _ = self.request('/?' + urlencode({'q': term}))
            self.assertEqual(self.account_ids(body), [number])
        body, _ = self.request('/?q=204')
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', body)
        self.assertNotIn('<img src=x', body)

    def test_row_details_and_read_only_game_gate_are_preserved(self):
        with patch.object(admin, 'guard_blocks_writes', return_value=True):
            body, _ = self.request('/?embedded=1&theme=dark')
        self.assertIn('游戏正在运行，GM 管理当前为只读', body)
        self.assertIn('<fieldset disabled', body)
        self.assertIn('非常长的中文角色名字仍然横向显示', body)
        self.assertIn('class="account-tags"', body)
        self.assertIn('塔防 1-1-1', body)
        self.assertIn('最近登录', body)
        self.assertIn('金币 ', body)
        self.assertIn('蓝钻 ', body)
        self.assertIn('金钻 ', body)
        self.assertIn('aria-label="管理账号 ', body)
        self.assertNotIn('<table>', body)
        link = html.unescape(re.search(r'class="account-name" href="([^"]+)"', body)[1])
        self.assertEqual(parse_qs(urlsplit(link).query)['theme'], ['dark'])
        self.assertEqual(parse_qs(urlsplit(link).query)['embedded'], ['1'])


if __name__ == '__main__':
    unittest.main()
