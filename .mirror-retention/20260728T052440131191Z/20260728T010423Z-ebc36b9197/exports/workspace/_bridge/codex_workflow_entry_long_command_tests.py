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
    def test_internal_mirror_facade_routes_terminal_publish_without_second_orchestrator(self) -> None:
        output = io.StringIO()
        result = {
            "schema": "codex_environment_mirror.finalize_and_publish.v1",
            "ok": True,
            "within_deadline": True,
        }
        with patch.object(entry, "execute_mirror_command", return_value=result) as owner, redirect_stdout(output):
            with patch.object(entry, "durable_mirror_child_context_valid", return_value=True):
                exit_code = entry.main([
                "mirror",
                "finalize-and-publish",
                "--confirm",
                "PUBLISH-CODEX-MIRROR",
                "--changed",
                "workspace/_bridge/codex_environment_mirror.py",
                "--internal-long-command",
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

    def test_direct_release_is_automatically_routed_through_durable_intent(self) -> None:
        terminal = {
            "ok": True,
            "terminal": True,
            "status": "completed",
            "exit_code": 0,
            "raw_result_ref": "artifact:/receipt",
        }
        output = io.StringIO()
        with (
            patch.object(entry, "converge_or_reuse_long_command", return_value=terminal) as converge,
            patch.object(entry, "execute_mirror_command", side_effect=AssertionError("must not execute directly")),
            redirect_stdout(output),
        ):
            exit_code = entry.main([
                "mirror", "release", "--tag", "seed-v3.3.2",
                "--confirm", "RELEASE-CODEX-MIRROR",
            ])

        self.assertEqual(0, exit_code)
        intent_id = converge.call_args.args[0]
        command = converge.call_args.args[1]
        self.assertEqual("mirror:release:seed-v3.3.2", intent_id)
        self.assertIn("--internal-long-command", command)
        self.assertTrue(json.loads(output.getvalue())["terminal_consumed_in_call"])

    def test_hidden_internal_flag_without_owner_context_is_blocked(self) -> None:
        output = io.StringIO()
        with (
            patch.object(entry, "execute_mirror_command", side_effect=AssertionError("must not execute")),
            patch.dict("os.environ", {}, clear=True),
            redirect_stdout(output),
        ):
            exit_code = entry.main([
                "mirror", "release", "--tag", "seed-v3.3.2",
                "--confirm", "RELEASE-CODEX-MIRROR", "--internal-long-command",
            ])

        payload = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertEqual("durable_owner_execution_context_missing_or_mismatched", payload["reason"])

    def test_task_id_durable_child_context_is_accepted_without_intent(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CODEX_LONG_COMMAND_TASK_ID": "manual-task",
                "CODEX_LONG_COMMAND_INTENT_ID": "",
                "CODEX_LONG_COMMAND_EXECUTION_SIGNATURE": "a" * 64,
            },
            clear=True,
        ):
            self.assertTrue(entry.durable_mirror_child_context_valid("publish"))

    def test_all_direct_mirror_writes_use_durable_intent_and_internal_child(self) -> None:
        cases = (
            (["mirror", "refresh", "--confirm", "REFRESH-CODEX-MIRROR"], "refresh"),
            (["mirror", "publish", "--confirm", "PUBLISH-CODEX-MIRROR"], "publish"),
            (["mirror", "finalize-and-publish", "--confirm", "PUBLISH-CODEX-MIRROR"], "finalize-and-publish"),
        )
        for argv, operation in cases:
            with self.subTest(operation=operation):
                terminal = {"ok": True, "terminal": True, "status": "completed", "exit_code": 0}
                output = io.StringIO()
                with (
                    patch.object(entry, "current_work_git_head", return_value="a" * 40),
                    patch.object(entry, "converge_or_reuse_long_command", return_value=terminal) as converge,
                    patch.object(entry, "execute_mirror_command", side_effect=AssertionError("must not execute directly")),
                    redirect_stdout(output),
                ):
                    exit_code = entry.main(argv)

                self.assertEqual(0, exit_code)
                intent_id = converge.call_args.args[0]
                command = converge.call_args.args[1]
                self.assertTrue(intent_id.startswith(f"mirror:{operation}:"))
                self.assertIn("--internal-long-command", command)
                self.assertTrue(json.loads(output.getvalue())["automatic_durable_entry"])

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

    def test_mirror_submit_recover_only_consumes_receipt_without_converging_again(self) -> None:
        terminal = {
            "ok": True,
            "terminal": True,
            "status": "completed",
            "exit_code": 0,
            "recovered_from_durable_receipt": True,
        }
        output = io.StringIO()
        with (
            patch.object(entry, "consume_terminal_long_command", return_value=terminal) as consume,
            patch.object(entry, "converge_or_reuse_long_command", side_effect=AssertionError("must not converge")),
            redirect_stdout(output),
        ):
            exit_code = entry.main([
                "mirror", "submit", "--operation", "release",
                "--intent-id", "mirror:release:seed-v3.3.1",
                "--tag", "seed-v3.3.1", "--recover-only",
            ])

        self.assertEqual(0, exit_code)
        consume.assert_called_once()
        self.assertTrue(json.loads(output.getvalue())["terminal_consumed_in_call"])


if __name__ == "__main__":
    unittest.main()
