"""Regression tests for the Git sync tool's subprocess output handling."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gitslop


class GitSlopOutputTests(unittest.TestCase):
    @patch("gitslop.subprocess.run")
    def test_invalid_utf8_output_never_crashes(self, run_mock):
        run_mock.return_value = SimpleNamespace(
            returncode=0,
            stdout=b" staged filename \x87 with output ",
            stderr=None,
        )

        ok, stdout, stderr = gitslop.run_git_cmd(["add", "-A"])

        self.assertTrue(ok)
        self.assertEqual(stdout, "staged filename \ufffd with output")
        self.assertEqual(stderr, "")
        self.assertFalse(run_mock.call_args.kwargs.get("text", False))
        self.assertEqual(
            run_mock.call_args.kwargs["stdout"],
            subprocess.PIPE,
        )
        self.assertEqual(
            run_mock.call_args.kwargs["stderr"],
            subprocess.PIPE,
        )

    @patch("gitslop.subprocess.run")
    def test_failed_git_command_handles_missing_stderr(self, run_mock):
        run_mock.return_value = SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=None,
        )

        ok, stdout, stderr = gitslop.run_git_cmd(["status"])

        self.assertFalse(ok)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    @patch("gitslop.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_git_returns_readable_error(self, run_mock):
        ok, stdout, stderr = gitslop.run_git_cmd(["status"])

        self.assertFalse(ok)
        self.assertEqual(stdout, "")
        self.assertIn("Git is not installed", stderr)


if __name__ == "__main__":
    unittest.main()
