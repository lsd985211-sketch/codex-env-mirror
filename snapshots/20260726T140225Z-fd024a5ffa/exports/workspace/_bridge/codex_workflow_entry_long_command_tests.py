#!/usr/bin/env python3
"""Facade regressions for submit-once durable mirror commands."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import codex_workflow_entry as entry


class CodexWorkflowEntryLongCommandTests(unittest.TestCase):
    def test_mirror_submit_uses_intent_once_and_follows_one_handle(self) -> None:
        submitted = {
            "ok": True,
            "submit_ok": True,
            "task_id": "intent-abc",
            "reused": False,
            "reuse_state": "new",
        }
        terminal = {
            "ok": True,
            "terminal": True,
            "task_id": "intent-abc",
            "status": "completed",
            "exit_code": 0,
            "raw_result_ref": "artifact:/receipt",
        }
        output = io.StringIO()
        with (
            patch.object(entry, "submit_or_reuse_long_command", return_value=submitted) as submit,
            patch.object(entry, "follow_long_command", return_value=terminal) as follow,
            redirect_stdout(output),
        ):
            exit_code = entry.main(
                [
                    "mirror",
                    "submit",
                    "--operation",
                    "publish",
                    "--intent-id",
                    "mirror:publish:stable-head",
                    "--follow-seconds",
                    "45",
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        submit.assert_called_once()
        follow.assert_called_once_with("intent-abc", wait_seconds=45.0)
        self.assertIn(" follow --task-id intent-abc ", payload["follow_command"])
        self.assertNotIn("status_command", payload)

    def test_reused_terminal_failure_is_returned_without_new_business_submit(self) -> None:
        submitted = {
            "ok": False,
            "submit_ok": True,
            "task_id": "intent-failed",
            "reused": True,
            "reuse_state": "terminal",
        }
        terminal = {
            "ok": False,
            "terminal": True,
            "task_id": "intent-failed",
            "status": "failed",
            "exit_code": 7,
            "raw_result_ref": "artifact:/failed-receipt",
        }
        output = io.StringIO()
        with (
            patch.object(entry, "submit_or_reuse_long_command", return_value=submitted) as submit,
            patch.object(entry, "follow_long_command", return_value=terminal) as follow,
            redirect_stdout(output),
        ):
            exit_code = entry.main(
                [
                    "mirror",
                    "submit",
                    "--operation",
                    "publish",
                    "--intent-id",
                    "mirror:publish:failed-head",
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        submit.assert_called_once()
        follow.assert_called_once()
        self.assertEqual("failed", payload["status"])
        self.assertTrue(payload["submission"]["reused"])


if __name__ == "__main__":
    unittest.main()
