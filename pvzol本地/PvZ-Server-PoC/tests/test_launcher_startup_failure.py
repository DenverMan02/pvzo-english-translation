"""Required service crashes must not leave startup stuck at 66 percent."""
import io
import unittest
from unittest.mock import patch
from test_launcher_desktop import IsolatedLauncherTests, FakeProcess
import launcher_service
import launcher as legacy


class StartupFailureTests(IsolatedLauncherTests):
    def install_server(self, text=''):
        server = FakeProcess(text)
        self.service.server = server
        self.service.running = self.service.busy = True
        return server

    def test_admin_migration_crash_cleans_owned_server_and_reports_exact_reason(self):
        server = self.install_server("[admin] crashed: OperationalError('duplicate column name: card_market_friend_day')\n")
        self.service._read_process(server, self.service.session, 'server')
        self.assertEqual(server.terminated, 1)
        self.assertFalse(self.service.busy or self.service.running)
        self.assertIn('card_market_friend_day', self.service.error)
        self.assertIn('本地服务启动失败', self.service.status)

    def test_old_service_crash_does_not_stop_current_session(self):
        server = self.install_server()
        old = FakeProcess('[web] crashed: fixture error\n')
        self.service._read_process(old, self.service.session - 1, 'server')
        self.assertIs(self.service.server, server)
        self.assertEqual(server.terminated, 0)
        self.assertTrue(self.service.busy)

    def test_partial_readiness_identifies_missing_service_instead_of_bare_66(self):
        server = self.install_server()
        with patch.object(legacy, 'START_TIMEOUT', .5), \
             patch.object(legacy, 'web_ready', return_value=True), \
             patch.object(legacy, 'admin_ready', return_value=False), \
             patch.object(legacy, 'policy_ready', return_value=True), \
             patch.object(legacy, 'port_open', return_value=True), \
             patch.object(launcher_service.time, 'monotonic', side_effect=(0, 0, 1)), \
             patch.object(self.service.cancel, 'wait'):
            self.service._wait_ready(server, self.service.session, 'loader_p.swf', self.service.cancel)
        self.assertEqual(server.terminated, 1)
        self.assertIn('GM 后台 (8081)', self.service.error)
        waiting = [row['message'] for row in self.service.get_logs()['entries'] if row['message'].startswith('正在等待')]
        self.assertEqual(waiting, ['正在等待：GM 后台 (8081)'])

    def test_stop_failure_retains_owned_handle_and_crash_reason(self):
        server = self.install_server()
        with patch.object(server, 'terminate', side_effect=OSError('fixture denied')):
            self.service._fail_startup(server, self.service.session, '[web] crashed: fixture error')
        self.assertIs(self.service.server, server)
        self.assertTrue(self.service.running)
        self.assertIn('fixture error', self.service.error)
        self.assertIn('部分服务未能停止', self.service.error)
        server.code = 0


if __name__ == '__main__':
    unittest.main()
