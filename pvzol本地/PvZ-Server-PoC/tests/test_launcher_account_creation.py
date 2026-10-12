"""Launcher creation writes only a temporary database and temporary config."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store

spec = importlib.util.spec_from_file_location("launcher_creation", SERVER.parent / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherCreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = store.DB_PATH
        store.set_db_path(str(Path(self.tmp.name) / "players.db"))
        self.config = Path(self.tmp.name) / "launcher_config.json"
        self.config_patch = patch.object(launcher, "CONFIG_PATH", str(self.config))
        self.config_patch.start()
        self.app = launcher.Launcher.__new__(launcher.Launcher)
        self.app.uin_var = Mock()
        self.app.uin_box = Mock()
        self.app._log = Mock()
        self.app.running = False

    def tearDown(self):
        self.config_patch.stop()
        store.set_db_path(self.old_db)
        self.tmp.cleanup()

    def test_creates_and_selects_account_and_remembers_it(self):
        self.assertEqual(self.app._create_account(" 700000205 "), "700000205")
        player = store.get_player_by_role_id(700000205)
        self.assertEqual(player["role_created"], 0)
        self.assertEqual(player["tutorial_active"], 1)
        self.app.uin_var.set.assert_called_once_with("700000205")
        self.assertEqual(json.loads(self.config.read_text())["recent"], ["700000205"])

    def test_duplicate_does_not_select_or_change_existing_save(self):
        self.app._create_account("700000205")
        store.save_player(700000205, name="原有角色", coin=123456)
        before = self.config.read_bytes()
        self.app.uin_var.reset_mock()
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.app._create_account("0700000205")
        self.assertEqual(store.get_player_by_role_id(700000205)["coin"], 123456)
        self.assertEqual(store.get_player_by_role_id(700000205)["name"], "原有角色")
        self.assertEqual(self.config.read_bytes(), before)
        self.app.uin_var.set.assert_not_called()

    def test_invalid_input_does_not_initialize_database(self):
        with self.assertRaises(ValueError):
            self.app._create_account("4294967296")
        self.assertFalse(Path(store.DB_PATH).exists())
        self.assertFalse(self.config.exists())

    def test_config_failure_still_selects_created_account(self):
        with patch.object(launcher, "save_config", return_value=False):
            self.app._create_account("700000206")
        self.assertIsNotNone(store.get_player_by_role_id(700000206))
        self.app.uin_var.set.assert_called_once_with("700000206")

    def test_running_game_does_not_open_create_dialog(self):
        self.app.running = True
        with patch.object(launcher.messagebox, "showwarning"), patch.object(launcher.tk, "Toplevel") as window:
            self.app.create_account()
        window.assert_not_called()


if __name__ == "__main__":
    unittest.main()
