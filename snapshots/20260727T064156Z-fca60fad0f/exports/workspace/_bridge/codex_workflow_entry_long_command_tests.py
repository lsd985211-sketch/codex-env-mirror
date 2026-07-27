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
    def test_mirror_facade_routes_terminal_publish_without_second_orchestrator(self) -> None:
        output = io.StringIO()
        result = {
            "schema": "codex_environment_mirror.finalize_and_publish.v1",
            "ok": True,
            "within_deadline": True,
        }
        with patch.object(entry, "execute_mirror_command", return_value=result) as owner, redirect_stdout(output):
            exit_code = entry.main([
                "mirror",
                "finalize-and-publish",
                "--confirm",
                "PUBLISH-CODEX-MIRROR",
                "--changed",
                "workspace/_bridge/codex_environment_mirror.py",
            ])

        self.assertEqual(0, exit_code)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        owner.assert_called_once_with(
            "finalize-and-publish",
            target_root="",
            confirm="PUBLISH-CODEX-MIRROR",
            changed_paths=["workspace/_bridge/codex_environment_mirror.py"],
            left_snapshot="",
            right_snapshot="",
            remote="",
            branch="",
            tag="",
            title="",
            release_impact="",
            force_fresh=False,
        )

    def test_mirror_submit_uses_one_terminal_convergence_call(self) -> None:
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
            patch.object(entry, "converge_or_reuse_long_command", return_value=terminal) as converge,
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
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        converge.assert_called_once()
        self.assertTrue(payload["terminal_consumed_in_call"])
        self.assertNotIn("follow_command", payload)
        self.assertNotIn("status_command", payload)

    def test_reused_terminal_failure_is_returned_without_new_business_submit(self) -> None:
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
            patch.object(entry, "converge_or_reuse_long_command", return_value=terminal) as converge,
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
        converge.assert_called_once()
        self.assertEqual("failed", payload["status"])
        self.assertTrue(payload["terminal_consumed_in_call"])


if __name__ == "__main__":
    unittest.main()
