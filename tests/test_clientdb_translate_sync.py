from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import clientdb_translate_sync as sync


class ClientDBTranslateSyncTests(unittest.TestCase):
    def workspace(self, root: Path, name: str) -> Path:
        path = root / name
        (path / "translate").mkdir(parents=True)
        return path

    def write(self, workspace: Path, relative: str, content: str) -> Path:
        path = workspace / "translate" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        return path

    def test_copies_old_translations_by_id_and_keeps_new_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.workspace(root, "old-translated")
            updated = self.workspace(root, "updated")

            self.write(
                old,
                "0001_plantInfo.txt",
                "id\tname\tdesc\n"
                "1\tPeashooter\tShoots peas\n"
                "2\tSunflower\tProduces sun\n",
            )
            target = self.write(
                updated,
                "0099_plantInfo.txt",
                "id\tname\tdesc\tnewField\n"
                "2\t向日葵\t生产阳光\tkeep-2\n"
                "3\t新植物\t新描述\tkeep-3\n"
                "1\t豌豆射手\t发射豌豆\tkeep-1\n",
            )

            plan = sync.plan_sync(old, updated)
            report = plan["summary"]
            self.assertEqual(report["matched_rows"], 2)
            self.assertEqual(report["new_rows_needing_translation"], 1)
            self.assertEqual(report["changed_cells"], 4)
            self.assertEqual(report["updated_files"], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), (
                "id\tname\tdesc\tnewField\n"
                "2\tSunflower\tProduces sun\tkeep-2\n"
                "3\t新植物\t新描述\tkeep-3\n"
                "1\tPeashooter\tShoots peas\tkeep-1\n"
            ))
            self.assertEqual(plan["changed"][0]["content"].splitlines()[2],
                             "3\t新植物\t新描述\tkeep-3")

    def test_dry_run_does_not_write_and_apply_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.workspace(root, "old")
            updated = self.workspace(root, "updated")
            self.write(old, "0000_ActivityIcon.txt", "id\tdesc\n1\tEnglish\n")
            target = self.write(updated, "0000_ActivityIcon.txt", "id\tdesc\n1\t中文\n")
            original = target.read_text(encoding="utf-8")

            plan = sync.plan_sync(old, updated)
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            backup = sync.apply_plan(plan)

            self.assertIsNotNone(backup)
            self.assertEqual(target.read_text(encoding="utf-8"), "id\tdesc\n1\tEnglish\n")
            self.assertEqual(
                (backup / "0000_ActivityIcon.txt").read_text(encoding="utf-8"),
                original,
            )

    def test_new_target_file_is_preserved_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.workspace(root, "old")
            updated = self.workspace(root, "updated")
            target = self.write(
                updated, "0235_newTable.txt", "id\tname\n10\t新字符串\n11\t另一个字符串\n"
            )

            plan = sync.plan_sync(old, updated)
            self.assertEqual(plan["summary"]["new_target_files"], 1)
            self.assertEqual(plan["summary"]["new_rows_needing_translation"], 2)
            self.assertEqual(plan["changed"], [])
            self.assertEqual(target.read_text(encoding="utf-8"),
                             "id\tname\n10\t新字符串\n11\t另一个字符串\n")

    def test_repeated_ids_use_occurrence_order_and_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.workspace(root, "old")
            updated = self.workspace(root, "updated")
            self.write(old, "0000_duplicate.txt",
                       "id\tname\n7\tFirst old translation\n7\tSecond old translation\n")
            self.write(updated, "0000_duplicate.txt",
                       "id\tname\n7\t第一条\n7\t第二条\n7\t新增行\n")

            plan = sync.plan_sync(old, updated)
            item = plan["files"][0]
            self.assertEqual(item["matched_rows"], 2)
            self.assertEqual(item["new_rows"], 1)
            self.assertIn("Repeated IDs", item["warning"])
            self.assertEqual(item["content"].splitlines(), [
                "id\tname",
                "7\tFirst old translation",
                "7\tSecond old translation",
                "7\t新增行",
            ])

    def test_non_tsv_file_is_not_guessed_or_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.workspace(root, "old")
            updated = self.workspace(root, "updated")
            self.write(old, "0000_story.txt", "old translated story\nline two\n")
            target = self.write(updated, "0000_story.txt", "new source story\nline two\n")

            plan = sync.plan_sync(old, updated)
            self.assertEqual(plan["files"][0]["status"], "manual-review")
            self.assertEqual(plan["changed"], [])
            self.assertEqual(target.read_text(encoding="utf-8"),
                             "new source story\nline two\n")

    def test_path_arguments_can_be_translate_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.workspace(root, "old")
            updated = self.workspace(root, "updated")
            self.write(old, "0000_simple.txt", "id\tname\n1\tEnglish\n")
            self.write(updated, "0000_simple.txt", "id\tname\n1\t中文\n")

            plan = sync.plan_sync(old / "translate", updated / "translate")
            self.assertEqual(plan["summary"]["matched_rows"], 1)
            self.assertEqual(plan["summary"]["changed_cells"], 1)


if __name__ == "__main__":
    unittest.main()
