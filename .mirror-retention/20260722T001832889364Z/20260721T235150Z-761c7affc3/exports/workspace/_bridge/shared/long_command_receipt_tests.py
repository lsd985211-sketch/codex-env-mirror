#!/usr/bin/env python3
"""Focused tests for durable long-command receipts."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from shared import long_command_receipt as owner


class LongCommandReceiptTests(unittest.TestCase):
    def run_in(self, root: str, task_id: str, source: str, *, timeout: int = 5, max_bytes: int = 512) -> dict[str, object]:
        with patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": root}):
            return owner.run_command(task_id, [sys.executable, "-c", source], timeout_seconds=timeout, max_inline_bytes=max_bytes)

    def test_success_is_terminal_and_status_reads_same_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_in(temp, "success", "print('done')")
            with patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
                status = owner.status("success")
        self.assertTrue(result["ok"])
        self.assertEqual(0, result["exit_code"])
        self.assertTrue(result["terminal"])
        self.assertEqual(result["completed_at"], status["completed_at"])

    def test_failure_retains_stderr_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_in(temp, "failure", "import sys; print('bad', file=sys.stderr); raise SystemExit(7)")
        self.assertFalse(result["ok"])
        self.assertEqual("failed", result["status"])
        self.assertEqual(7, result["exit_code"])
        self.assertIn("bad", result["stderr"])

    def test_long_output_is_bounded_but_full_artifact_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_in(temp, "bounded", "print('x' * 5000)", max_bytes=300)
            raw = Path(temp, "bounded", "stdout.log").read_text(encoding="utf-8")
        self.assertTrue(result["stdout_truncated"])
        self.assertLess(len(result["stdout"]), len(raw))
        self.assertTrue(str(result["raw_result_ref"]).startswith("artifact:"))

    def test_timeout_terminates_and_writes_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_in(temp, "timeout", "import time; time.sleep(5)", timeout=1)
        self.assertFalse(result["ok"])
        self.assertEqual("timed_out", result["status"])
        self.assertTrue(result["terminal"])
        self.assertIsInstance(result["exit_code"], int)

    def test_unreaped_timeout_is_not_reported_as_terminal(self) -> None:
        process = Mock(pid=1234)
        process.wait.side_effect = [subprocess.TimeoutExpired("test", 2), subprocess.TimeoutExpired("test", 2)]
        process.poll.return_value = None
        with patch.object(owner.os, "name", "nt"), self.assertRaisesRegex(RuntimeError, "process_not_reaped_after_kill"):
            owner.terminate_group(process)

    def test_permission_error_means_process_exists(self) -> None:
        with patch.object(owner.os, "kill", side_effect=PermissionError):
            self.assertTrue(owner.process_alive(1234))

    def test_invalid_task_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_task_id"):
            owner.task_dir("../escape")


if __name__ == "__main__":
    unittest.main()
