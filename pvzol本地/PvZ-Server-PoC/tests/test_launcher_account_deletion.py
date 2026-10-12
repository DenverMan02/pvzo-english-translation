"""Exercise account deletion using fake dialogs/manager and a temporary config.

No Tk window, game process, or production player database is opened by this file.
"""
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[2] / "launcher.py"
SPEC = importlib.util.spec_from_file_location("launcher_delete_under_test", SOURCE)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class Variable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def make_app(value="001"):
    app = launcher.Launcher.__new__(launcher.Launcher)
    app.root = Mock()
    app.root.winfo_children.return_value = []
    app.running = False
    app.uin_var = Variable(value)
    app.uin_box = Mock()
    app._log = Mock()
    return app


class LauncherDeletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name) / "launcher_config.json"
        self.apply_patch(patch.object(launcher, "CONFIG_PATH", str(self.config)))
        # These replacements make it impossible for tests to reach the real DB.
        self.manager = types.ModuleType("account_management")
        self.manager.get_account = Mock(return_value={"name": "测试角色"})
        self.manager.delete_account = Mock(
            side_effect=lambda value: {"uin": int(value)})
        self.apply_patch(patch.dict(sys.modules, {"account_management": self.manager}))
        self.port = self.apply_patch(patch.object(launcher, "port_open", return_value=False))
        self.ask = Mock(return_value=False)
        self.notice = Mock(return_value=False)
        self.dialog = self.apply_patch(patch.object(
            launcher, "themed_dialog", side_effect=self.respond_to_dialog, create=True))
        for name in ("askyesno", "showwarning", "showerror", "showinfo"):
            self.apply_patch(patch.object(
                launcher.messagebox, name,
                side_effect=AssertionError("删除账号流程应使用 themed_dialog")))

    def respond_to_dialog(self, *args, **kwargs):
        if kwargs.get("confirm", False):
            return self.ask(*args, **kwargs)
        return self.notice(*args, **kwargs)

    def dialog_text(self, dialog):
        args, kwargs = dialog.call_args
        return "\n".join(str(value) for value in args[1:]) + "\n" + str(kwargs.get("detail", ""))

    def apply_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def save_recent(self, selected="001", recent=None):
        data = {"last_uin": selected, "recent": ["001", "222"] if recent is None else recent,
                "ruffle_path": "keep-my-setting"}
        self.config.write_text(json.dumps(data), encoding="utf-8")

    def assert_no_delete_or_confirmation(self):
        self.ask.assert_not_called()
        self.manager.delete_account.assert_not_called()

    def test_cancel_is_read_only_and_preserves_config_and_selection(self):
        self.save_recent()
        before = self.config.read_bytes()
        app = make_app()
        app.delete_account()
        self.manager.get_account.assert_called_once_with("001")
        self.manager.delete_account.assert_not_called()
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(app.uin_var.get(), "001")
        app.uin_box.configure.assert_not_called()
        self.notice.assert_not_called()

    def test_confirmation_names_account_character_and_progress_without_backup(self):
        app = make_app("0222")
        app.delete_account()
        self.ask.assert_called_once()
        args, kwargs = self.ask.call_args
        prompt = self.dialog_text(self.ask)
        self.assertIn("0222", prompt)
        self.assertIn("测试角色", prompt)
        self.assertIn("全部游戏进度", prompt)
        self.assertNotIn("自动备份", prompt)
        self.assertIs(args[0], app.root)
        self.assertTrue(kwargs["confirm"])

    def test_yes_deletes_selected_account_without_reporting_backup(self):
        self.save_recent()
        self.ask.return_value = True
        app = make_app(" 001 ")
        app.delete_account()
        self.manager.delete_account.assert_called_once_with("001")
        self.notice.assert_called_once()
        self.assertIn("001", self.dialog_text(self.notice))
        self.assertIn("已删除", self.dialog_text(self.notice))
        self.assertNotIn("备份", self.dialog_text(self.notice))
        self.assertIs(self.notice.call_args.args[0], app.root)
        self.assertEqual(app.uin_var.get(), "222")
        self.assertNotIn("备份", "\n".join(call.args[0] for call in app._log.call_args_list))

    def test_running_server_blocks_read_confirmation_and_deletion(self):
        app = make_app()
        app.running = True
        app.delete_account()
        self.manager.get_account.assert_not_called()
        self.assert_no_delete_or_confirmation()
        self.notice.assert_called_once()

    def test_external_game_service_blocks_read_confirmation_and_deletion(self):
        self.port.return_value = True
        app = make_app()
        app.delete_account()
        self.port.assert_called_once_with(launcher.GAME_PORT)
        self.manager.get_account.assert_not_called()
        self.assert_no_delete_or_confirmation()
        self.notice.assert_called_once()

    def test_standalone_admin_service_blocks_read_confirmation_and_deletion(self):
        self.port.side_effect = lambda port: port == launcher.ADMIN_PORT
        app = make_app()
        app.delete_account()
        self.assertEqual(self.port.call_args_list[0].args, (launcher.GAME_PORT,))
        self.assertEqual(self.port.call_args_list[1].args, (launcher.ADMIN_PORT,))
        self.manager.get_account.assert_not_called()
        self.assert_no_delete_or_confirmation()
        self.notice.assert_called_once()
        self.assertIn("后台", self.dialog_text(self.notice))

    def test_invalid_values_never_read_confirm_or_delete(self):
        for value in ("", "0", "4294967296", "abc", "１２", "1&uin=2"):
            with self.subTest(value=value):
                app = make_app(value)
                app.delete_account()
        self.manager.get_account.assert_not_called()
        self.assert_no_delete_or_confirmation()
        self.assertEqual(self.notice.call_count, 6)

    def test_missing_account_never_confirms_or_deletes(self):
        self.manager.get_account.return_value = None
        app = make_app()
        app.delete_account()
        self.manager.get_account.assert_called_once_with("001")
        self.assert_no_delete_or_confirmation()
        self.notice.assert_called_once()
        self.assertIn("尚无存档", self.dialog_text(self.notice))

    def test_read_failure_reports_error_without_confirmation_or_deletion(self):
        self.manager.get_account.side_effect = OSError("cannot read")
        app = make_app()
        app.delete_account()
        self.assert_no_delete_or_confirmation()
        self.notice.assert_called_once()
        self.assertIn("cannot read", self.dialog_text(self.notice))

    def test_game_starting_during_confirmation_blocks_deletion(self):
        self.save_recent()
        before = self.config.read_bytes()
        app = make_app()
        def accept_after_start(*args, **kwargs):
            app.running = True
            return True
        self.ask.side_effect = accept_after_start
        app.delete_account()
        self.ask.assert_called_once()
        self.manager.delete_account.assert_not_called()
        self.notice.assert_called_once()
        self.assertIn("停止", self.dialog_text(self.notice))
        self.assertIn("后台", self.dialog_text(self.notice))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertNotIn("已删除", self.dialog_text(self.notice))

    def test_external_game_starting_during_confirmation_blocks_deletion(self):
        self.port.side_effect = [False, False, True]
        self.ask.return_value = True
        app = make_app()
        app.delete_account()
        self.ask.assert_called_once()
        self.manager.delete_account.assert_not_called()
        self.notice.assert_called_once()
        self.assertNotIn("已删除", self.dialog_text(self.notice))

    def test_delete_error_preserves_recent_selection_and_does_not_claim_success(self):
        self.save_recent()
        before = self.config.read_bytes()
        self.ask.return_value = True
        self.manager.delete_account.side_effect = OSError("database locked")
        app = make_app()
        app.delete_account()
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(app.uin_var.get(), "001")
        app.uin_box.configure.assert_not_called()
        self.notice.assert_called_once()
        self.assertIn("database locked", self.dialog_text(self.notice))
        self.assertNotIn("已删除", self.dialog_text(self.notice))

    def test_deletion_removes_all_zero_prefixed_aliases_and_preserves_other_settings(self):
        self.save_recent("0001", ["001", "0001", "1", "0222", "700000204"])
        app = make_app("0001")
        app._delete_account("0001")
        self.manager.delete_account.assert_called_once_with("0001")
        self.assertEqual(launcher.load_config(), {"last_uin": "0222", "recent": ["0222", "700000204"],
                                                  "ruffle_path": "keep-my-setting"})
        self.assertEqual(app.uin_var.get(), "0222")
        app.uin_box.configure.assert_called_once_with(values=["0222", "700000204"])

    def test_last_account_stays_empty_when_config_is_reloaded(self):
        self.save_recent("001", ["1", "001", "0001"])
        app = make_app()
        result = app._delete_account("001")
        self.assertTrue(result["config_saved"])
        self.assertEqual(launcher.load_config()["last_uin"], "")
        self.assertEqual(launcher.load_config()["recent"], [])
        self.assertEqual(app.uin_var.get(), "")
        app.uin_box.configure.assert_called_once_with(values=[])

    def test_deletion_closes_only_the_deleted_accounts_editors(self):
        app = make_app()
        first_editor = types.SimpleNamespace(_account_uin=1, destroy=Mock())
        second_editor = types.SimpleNamespace(_account_uin=1, destroy=Mock())
        other_account_editor = types.SimpleNamespace(_account_uin=222, destroy=Mock())
        ordinary_window = types.SimpleNamespace(destroy=Mock())
        app.root.winfo_children.return_value = [first_editor, other_account_editor,
                                               ordinary_window, second_editor]
        app._delete_account("001")
        first_editor.destroy.assert_called_once_with()
        second_editor.destroy.assert_called_once_with()
        other_account_editor.destroy.assert_not_called()
        ordinary_window.destroy.assert_not_called()

    def test_configuration_failure_still_reports_completed_deletion_without_backup(self):
        self.save_recent()
        self.ask.return_value = True
        app = make_app()
        with patch.object(launcher, "save_config", return_value=False):
            app.delete_account()
        self.manager.delete_account.assert_called_once_with("001")
        self.notice.assert_called_once()
        notice = self.dialog_text(self.notice)
        self.assertIn("已删除", notice)
        self.assertNotIn("备份", notice)
        self.assertIn("列表保存失败", notice)
        self.assertEqual(app.uin_var.get(), "222")
        app.uin_box.configure.assert_called_once_with(values=["222"])

    def test_private_deletion_rejects_running_or_invalid_account(self):
        app = make_app()
        app.running = True
        with self.assertRaisesRegex(ValueError, "停止"):
            app._delete_account("001")
        app.running = False
        with self.assertRaisesRegex(ValueError, "纯数字"):
            app._delete_account("bad")
        self.manager.delete_account.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
