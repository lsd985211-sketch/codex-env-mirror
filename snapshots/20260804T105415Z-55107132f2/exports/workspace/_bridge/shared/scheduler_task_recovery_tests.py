#!/usr/bin/env python3
from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
import sys
from unittest.mock import patch


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from shared.scheduler_task_recovery import RecoveryHooks, confirmation_for, recover_task
from shared import codex_scheduler_runner as runner


@dataclass
class Run:
    task_id: str
    ok: bool
    record_path: str = "receipt.jsonl"


class Lock:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.released = False

    def acquire(self) -> bool:
        return self.acquired

    def release(self) -> None:
        self.released = True


class SchedulerTaskRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[str] = []
        self.state = {"tasks": {"retry_task": {"last_status": "retry_exhausted", "retry_count": 3}}}
        self.tasks = [{"id": "retry_task", "enabled": True, "action": {"command": ["python", "owner.py"]}}]
        self.lock = Lock()

    def hooks(self, *, succeeds: bool = True) -> RecoveryHooks:
        def run(task: dict, _reason: str, dry_run: bool) -> Run:
            self.events.append(f"run:{dry_run}")
            return Run(task_id=task["id"], ok=succeeds)

        def update(_task: dict, state: dict, run: Run) -> None:
            self.events.append("update")
            task_state = state["tasks"][run.task_id]
            task_state["last_status"] = "success" if run.ok else "retry_exhausted"
            task_state["retry_count"] = 0 if run.ok else 4

        return RecoveryHooks(
            run_task=run,
            update_task_state=update,
            save_state=lambda _state: self.events.append("save"),
            write_heartbeat=lambda payload: {"ok": True, "summary": payload["last_run_summary"]},
            append_log=lambda _message: self.events.append("log"),
            compact_run_result=lambda result: {"task_id": result["task_id"], "ok": result["ok"]},
            lock_factory=lambda: self.lock,
        )

    def test_unknown_task_is_rejected_without_execution(self) -> None:
        result = recover_task(task_id="unknown", confirm="", dry_run=False, tasks=self.tasks, state=self.state, hooks=self.hooks())
        self.assertFalse(result["ok"])
        self.assertEqual("task_not_registered", result["reason"])
        self.assertEqual([], self.events)

    def test_disabled_task_is_rejected_without_execution(self) -> None:
        self.tasks[0]["enabled"] = False
        result = recover_task(task_id="retry_task", confirm="", dry_run=False, tasks=self.tasks, state=self.state, hooks=self.hooks())
        self.assertFalse(result["ok"])
        self.assertEqual("task_disabled", result["reason"])
        self.assertEqual([], self.events)

    def test_real_execution_requires_exact_per_task_confirmation(self) -> None:
        result = recover_task(task_id="retry_task", confirm="RECOVER-SCHEDULER-TASK:other", dry_run=False, tasks=self.tasks, state=self.state, hooks=self.hooks())
        self.assertFalse(result["ok"])
        self.assertEqual("confirmation_required", result["reason"])
        self.assertEqual(confirmation_for("retry_task"), result["required_confirm"])
        self.assertEqual([], self.events)

    def test_dry_run_uses_task_without_updating_state(self) -> None:
        result = recover_task(task_id="retry_task", confirm="", dry_run=True, tasks=self.tasks, state=self.state, hooks=self.hooks())
        self.assertTrue(result["ok"])
        self.assertFalse(result["state_updated"])
        self.assertEqual("retry_exhausted", self.state["tasks"]["retry_task"]["last_status"])
        self.assertEqual(["run:True", "log"], self.events)
        self.assertTrue(self.lock.released)

    def test_success_clears_retry_exhausted_state_via_owner_transition(self) -> None:
        result = recover_task(task_id="retry_task", confirm=confirmation_for("retry_task"), dry_run=False, tasks=self.tasks, state=self.state, hooks=self.hooks())
        self.assertTrue(result["ok"])
        self.assertTrue(result["state_updated"])
        self.assertEqual("success", self.state["tasks"]["retry_task"]["last_status"])
        self.assertEqual(0, self.state["tasks"]["retry_task"]["retry_count"])
        self.assertEqual(["run:False", "update", "save", "log"], self.events)

    def test_failure_is_recorded_and_not_rewritten_as_success(self) -> None:
        result = recover_task(task_id="retry_task", confirm=confirmation_for("retry_task"), dry_run=False, tasks=self.tasks, state=self.state, hooks=self.hooks(succeeds=False))
        self.assertFalse(result["ok"])
        self.assertTrue(result["failure_preserved"])
        self.assertEqual("retry_exhausted", self.state["tasks"]["retry_task"]["last_status"])
        self.assertEqual(4, self.state["tasks"]["retry_task"]["retry_count"])

    def test_active_scheduler_lock_blocks_direct_recovery(self) -> None:
        self.lock = Lock(acquired=False)
        result = recover_task(task_id="retry_task", confirm=confirmation_for("retry_task"), dry_run=False, tasks=self.tasks, state=self.state, hooks=self.hooks())
        self.assertFalse(result["ok"])
        self.assertEqual("scheduler_active", result["reason"])
        self.assertEqual([], self.events)

    def test_runner_cli_passes_only_task_id_confirmation_and_dry_run(self) -> None:
        with patch.object(runner, "recover_task", return_value={"ok": True}) as recover, patch.object(runner, "print_json") as printer:
            self.assertEqual(0, runner.main(["recover-task", "--task-id", "retry_task", "--confirm", confirmation_for("retry_task"), "--dry-run"]))
        recover.assert_called_once_with("retry_task", confirm=confirmation_for("retry_task"), dry_run=True)
        printer.assert_called_once_with({"ok": True})

    def test_runner_cli_returns_failure_for_failed_recovery_payload(self) -> None:
        with patch.object(runner, "recover_task", return_value={"ok": False}) as recover, patch.object(runner, "print_json") as printer:
            self.assertEqual(1, runner.main(["recover-task", "--task-id", "retry_task", "--confirm", confirmation_for("retry_task")]))
        recover.assert_called_once_with("retry_task", confirm=confirmation_for("retry_task"), dry_run=False)
        printer.assert_called_once_with({"ok": False})


if __name__ == "__main__":
    unittest.main()
