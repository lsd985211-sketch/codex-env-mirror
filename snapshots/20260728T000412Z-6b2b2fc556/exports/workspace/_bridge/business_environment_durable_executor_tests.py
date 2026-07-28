#!/usr/bin/env python3
"""Focused regression tests for business-environment durable operations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import persistent_task_kernel
from business_environment_durable_executor_process import advance_operation, build_operation
from maintenance_convergence_runtime import persist_plan


def plan(command: list[str], *, signature: str = "sig", timeout_seconds: int = 20) -> dict[str, object]:
    return {
        "schema": "maintenance_convergence_plan.v1",
        "ok": True,
        "derived_runtime": True,
        "status": "ready",
        "plan_id": f"plan:{signature}",
        "next_action": {
            "node_id": "sample:validate",
            "owner": "sample_owner.py",
            "action": "validate",
            "input_signature": signature,
            "command_argv": command,
            "automation_level": "A0",
            "effect_class": "observe",
            "timeout_seconds": timeout_seconds,
            "freshness_ttl_seconds": 60,
            "max_attempts": 2,
            "retry_delay_seconds": 120,
        },
    }


class DurableExecutorTests(unittest.TestCase):
    def test_signature_stably_identifies_operation(self) -> None:
        first = build_operation(plan([sys.executable, "-c", "pass"]), cwd="/tmp")
        second = build_operation(plan([sys.executable, "-c", "pass"]), cwd="/tmp")
        changed = build_operation(plan([sys.executable, "-c", "pass"], signature="changed"), cwd="/tmp")

        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertNotEqual(first["operation_id"], changed["operation_id"])
        self.assertFalse(first["business_command_resubmit_allowed"])

    def test_successful_signature_executes_once_then_reuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            count = root / "count.txt"
            command = [
                sys.executable,
                "-c",
                f"from pathlib import Path; p=Path({str(count)!r}); p.write_text((p.read_text() if p.exists() else '')+'x')",
            ]
            kwargs = {
                "mode": "converge",
                "cwd": str(root),
                "db_path": root / "tasks.sqlite",
                "result_state_root": root / "results",
            }
            with patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": str(root / "receipts")}):
                first = advance_operation(plan(command), **kwargs)
                second = advance_operation(plan(command), **kwargs)

            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertTrue(second["reused"])
            self.assertEqual(count.read_text(), "x")

    def test_failed_attempt_enters_backoff_without_immediate_resubmit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            count = root / "count.txt"
            command = [
                sys.executable,
                "-c",
                f"from pathlib import Path; p=Path({str(count)!r}); p.write_text((p.read_text() if p.exists() else '')+'x'); raise SystemExit(3)",
            ]
            kwargs = {
                "mode": "converge",
                "cwd": str(root),
                "db_path": root / "tasks.sqlite",
                "result_state_root": root / "results",
            }
            with patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": str(root / "receipts")}):
                first = advance_operation(plan(command), **kwargs)
                second = advance_operation(plan(command), **kwargs)

            self.assertEqual(first["state"], "retry_wait")
            self.assertEqual(second["state"], "retry_wait")
            self.assertEqual(second["next_action"], "wait_until_retry_due")
            self.assertEqual(count.read_text(), "x")

    def test_submit_returns_event_wait_without_poll_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": str(root / "receipts")}):
                result = advance_operation(
                    plan([sys.executable, "-c", "pass"]),
                    mode="submit",
                    cwd=str(root),
                    db_path=root / "tasks.sqlite",
                    result_state_root=root / "results",
                )

            self.assertEqual(result["next_action"], "wait_for_or_consume_terminal_event")
            self.assertFalse(result["business_command_resubmit_allowed"])
            self.assertNotIn("poll_command", result)

    def test_interrupted_executing_attempt_requires_receipt_and_never_resubmits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            count = root / "count.txt"
            value = plan([
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(count)!r}).write_text('executed')",
            ])
            operation = build_operation(value, cwd=str(root))
            db_path = root / "tasks.sqlite"
            persistent_task_kernel.enqueue(
                task_id=operation["task_id"],
                idempotency_key=operation["idempotency_key"],
                task_type="business_environment_durable_operation",
                target_module=operation["owner"],
                action_type=operation["action"],
                payload={
                    "operation_id": operation["operation_id"],
                    "plan_id": operation["plan_id"],
                    "node_id": operation["node_id"],
                    "input_signature": operation["input_signature"],
                    "execution_signature": operation["execution_signature"],
                    "command_owner": operation["command_owner"],
                    "timeout_seconds": operation["timeout_seconds"],
                    "max_attempts": operation["max_attempts"],
                    "retry_delay_seconds": operation["retry_delay_seconds"],
                },
                db_path=db_path,
            )
            lease_owner = "test-owner"
            persistent_task_kernel.claim_task(operation["task_id"], lease_owner=lease_owner, db_path=db_path)
            persistent_task_kernel.acknowledge(operation["task_id"], lease_owner=lease_owner, db_path=db_path)
            persistent_task_kernel.begin(operation["task_id"], lease_owner=lease_owner, db_path=db_path)

            with patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": str(root / "receipts")}):
                result = advance_operation(
                    value,
                    mode="converge",
                    cwd=str(root),
                    db_path=db_path,
                    result_state_root=root / "results",
                )

            self.assertEqual(result["reason"], "interrupted_attempt_requires_terminal_evidence")
            self.assertFalse(count.exists())
            self.assertFalse(result["business_command_resubmit_allowed"])

    def test_nonautomatic_node_fails_closed(self) -> None:
        value = plan([sys.executable, "-c", "pass"])
        value["next_action"]["automation_level"] = "A4"  # type: ignore[index]

        result = build_operation(value, cwd="/tmp")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "automation_level_requires_owner_or_user_decision")

    def test_untrusted_or_malformed_plan_fails_closed(self) -> None:
        untrusted = plan([sys.executable, "-c", "pass"])
        untrusted.pop("derived_runtime")
        malformed = plan([sys.executable, "-c", "pass"])
        malformed["next_action"]["timeout_seconds"] = "forever"  # type: ignore[index]

        self.assertEqual(build_operation(untrusted, cwd="/tmp")["reason"], "convergence_plan_not_ready")
        self.assertEqual(build_operation(malformed, cwd="/tmp")["reason"], "next_action_numeric_contract_invalid")

    def test_workflow_facade_operation_plan_is_cwd_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = plan([sys.executable, "-c", "pass"])
            persist_plan(value, state_root=root / "maintenance-convergence")
            facade = Path(__file__).with_name("codex_workflow_entry.py")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(facade),
                    "business-environment",
                    "operation-plan",
                    "--plan-id",
                    str(value["plan_id"]),
                    "--cwd",
                    str(root),
                ],
                cwd="/tmp",
                env={
                    **{key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
                    "CODEX_SCHEDULER_STATE_ROOT": str(root),
                },
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
                check=False,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["single_writer"], "persistent_task_kernel")


if __name__ == "__main__":
    unittest.main()
