"""The owned player exiting must release services, never a newer session."""
import sys
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_launcher_desktop import IsolatedLauncherTests, FakeProcess
import launcher_service
import launcher as legacy
from launcher_guard import guard_blocks_writes


class OwnedGameExitTests(IsolatedLauncherTests):
    def install_processes(self):
        server, game, admin = FakeProcess(), FakeProcess(), FakeProcess()
        game.pid = 100
        self.service.server, self.service.game, self.service.admin = server, game, admin
        self.service.running = True
        self.service._publish_guard()
        return server, game, admin

    def test_normal_game_close_stops_all_owned_handles_and_releases_guard(self):
        server, game, admin = self.install_processes()
        unrelated = FakeProcess()
        game.code = 0
        self.service._watch_game(game, server, self.service.session, self.service.cancel)
        self.assertEqual([p.terminated for p in (server, game, admin, unrelated)], [1, 0, 1, 0])
        self.assertFalse(self.service.running)
        self.assertFalse(self.service.busy)
        self.assertFalse(self.service.closed)
        self.assertEqual(self.service.status, '游戏已关闭，服务已停止')
        self.assertFalse(guard_blocks_writes(self.service._environment()))

    def test_crashed_game_also_stops_services_and_retains_exit_reason(self):
        server, game, _ = self.install_processes()
        game.code = 13
        self.service._watch_game(game, server, self.service.session, self.service.cancel)
        self.assertFalse(self.service.running)
        self.assertIn('代码 13', self.service.error)

    def test_previous_game_watcher_cannot_stop_a_restarted_game(self):
        server, game, _ = self.install_processes()
        session, cancel = self.service.session, self.service.cancel
        self.service.stop()
        self.service.cancel = threading.Event()
        new_server, new_game, _ = self.install_processes()
        game.code = 0
        self.service._watch_game(game, server, session, cancel)
        self.assertIs(self.service.server, new_server)
        self.assertIs(self.service.game, new_game)
        self.assertTrue(self.service.running)
        self.assertEqual(new_server.terminated, 0)

    def test_replaced_game_handle_is_ignored_even_with_same_session(self):
        server, game, _ = self.install_processes()
        self.service.game = FakeProcess()
        game.code = 0
        self.service._watch_game(game, server, self.service.session, self.service.cancel)
        self.assertEqual(server.terminated, 0)

    def test_failed_service_stop_retains_handle_for_retry_and_reports_failure(self):
        server, game, _ = self.install_processes()
        server.terminate = Mock(side_effect=OSError('fixture denied'))
        game.code = 0
        self.service._watch_game(game, server, self.service.session, self.service.cancel)
        self.assertIs(self.service.server, server)
        self.assertTrue(self.service.running)
        self.assertIn('未停止', self.service.error)
        server.code = 0

    def test_running_game_monitor_is_cancelled_without_waiting_for_game_exit(self):
        server, game, _ = self.install_processes()
        worker = threading.Thread(target=self.service._watch_game,
                                  args=(game, server, self.service.session, self.service.cancel))
        worker.start()
        self.service.stop()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(server.terminated, 1)

    def test_player_spawn_failure_cleans_ready_server_and_returns_to_idle(self):
        server, _game, _admin = self.install_processes()
        self.service.game = None
        self.service.launch_player = True
        self.service.busy = True
        with patch.object(legacy, 'web_ready', return_value=True), \
             patch.object(legacy, 'admin_ready', return_value=True), \
             patch.object(legacy, 'port_open', return_value=True), \
             patch.object(legacy, 'policy_ready', return_value=True), \
             patch('launcher_game_window.read_swf_stage', return_value=(1440, 900)), \
             patch.object(launcher_service.subprocess, 'Popen', side_effect=OSError('missing player')):
            self.service._wait_ready(server, self.service.session, 'loader_p.swf', self.service.cancel)
        self.assertFalse(self.service.running)
        self.assertFalse(self.service.busy)
        self.assertEqual(server.terminated, 1)
        self.assertIn('missing player', self.service.error)

    def test_ready_player_spawn_starts_watcher_and_placement_for_exact_session(self):
        server, game, _ = self.install_processes()
        self.service.game = None
        self.service.launch_player = True
        with patch.object(legacy, 'web_ready', return_value=True), \
             patch.object(legacy, 'admin_ready', return_value=True), \
             patch.object(legacy, 'port_open', return_value=True), \
             patch.object(legacy, 'policy_ready', return_value=True), \
             patch('launcher_game_window.read_swf_stage', return_value=(1440, 900)), \
             patch.object(launcher_service.subprocess, 'Popen', return_value=game), \
             patch.object(launcher_service.threading, 'Thread') as threads:
            self.service._wait_ready(server, self.service.session, 'loader_p.swf', self.service.cancel)
        self.assertEqual(threads.call_count, 2)
        self.assertEqual([call.kwargs['args'] for call in threads.call_args_list],
                         [(game, server, self.service.session, self.service.cancel)] * 2)
        self.assertEqual(threads.call_args_list[1].kwargs['kwargs'],
                         {'initially_hidden': sys.platform == 'win32',
                          'stage_size': (1440, 900) if sys.platform == 'win32' else None})

    def test_unrevealed_hidden_player_timeout_releases_ports_and_reports_error(self):
        server, game, admin = self.install_processes()
        with patch('launcher_game_window.place_game_window', return_value=False):
            self.service._place_game_window(game, server, self.service.session, self.service.cancel,
                                            initially_hidden=True)
        self.assertEqual([p.terminated for p in (game, server, admin)], [1, 1, 1])
        self.assertFalse(self.service.running)
        self.assertFalse(guard_blocks_writes(self.service._environment()))
        self.assertEqual(self.service.status, '游戏窗口未能显示')
        self.assertIn('未找到', self.service.error)

    def test_hidden_position_failure_cannot_leave_an_invisible_game_running(self):
        server, game, _ = self.install_processes()
        with patch('launcher_game_window.place_game_window', side_effect=OSError('fixture position denied')):
            self.service._place_game_window(game, server, self.service.session, self.service.cancel,
                                            initially_hidden=True)
        self.assertFalse(self.service.running)
        self.assertEqual(game.terminated, 1)
        self.assertIn('fixture position denied', self.service.error)

    def test_successful_hidden_reveal_keeps_owned_game_and_services_running(self):
        server, game, _ = self.install_processes()
        with patch('launcher_game_window.place_game_window', return_value=True) as place:
            self.service._place_game_window(game, server, self.service.session, self.service.cancel,
                                            initially_hidden=True)
        self.assertTrue(place.call_args.kwargs['initially_hidden'])
        self.assertTrue(self.service.running)
        self.assertEqual(game.terminated, 0)

    def test_old_hidden_start_failure_cannot_stop_a_new_session(self):
        server, game, _ = self.install_processes()
        session, cancel = self.service.session, self.service.cancel
        def stale_failure(*args, **kwargs):
            self.service.stop()
            self.service.cancel = threading.Event()
            self.install_processes()
            return False
        with patch('launcher_game_window.place_game_window', side_effect=stale_failure):
            self.service._place_game_window(game, server, session, cancel, initially_hidden=True)
        self.assertTrue(self.service.running)
        self.assertEqual(self.service.game.terminated, 0)
        self.assertEqual(self.service.server.terminated, 0)


if __name__ == '__main__': unittest.main()
