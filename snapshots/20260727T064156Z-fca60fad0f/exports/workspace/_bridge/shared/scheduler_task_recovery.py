#!/usr/bin/env python3
"""Guarded single-task recovery for the unified scheduler.

Ownership: select exactly one registered task and coordinate a manual retry.
Non-goals: accept command text, bypass a task's policy, or create a scheduler.
State behavior: a real attempt reuses the runner's receipt and state transition.
Caller context: the scheduler CLI provides the task table and existing owner hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


CONFIRM_PREFIX = "RECOVER-SCHEDULER-TASK:"


def confirmation_for(task_id: str) -> str:
    return f"{CONFIRM_PREFIX}{task_id}"


@dataclass(frozen=True)
class RecoveryHooks:
    """Existing scheduler authority injected by the runner facade."""

    run_task: Callable[[dict[str, Any], str, bool], Any]
    update_task_state: Callable[[dict[str, Any], dict[str, Any], Any], None]
    save_state: Callable[[dict[str, Any]], None]
    write_heartbeat: Callable[[dict[str, Any]], dict[str, Any]]
    append_log: Callable[[str], None]
    compact_run_result: Callable[[dict[str, Any]], dict[str, Any]]
    lock_factory: Callable[[], Any]


def recover_task(
    *,
    task_id: str,
    confirm: str,
    dry_run: bool,
    tasks: list[dict[str, Any]],
    state: dict[str, Any],
    hooks: RecoveryHooks,
) -> dict[str, Any]:
    """Run one enabled registered task while excluding the long-lived loop."""

    normalized_id = str(task_id or "").strip()
    task = next((item for item in tasks if str(item.get("id") or "") == normalized_id), None)
    if task is None:
        return {
            "schema": "codex_scheduler.recover_task.v1",
            "ok": False,
            "reason": "task_not_registered",
            "task_id": normalized_id,
        }
    if not task.get("enabled", False):
        return {
            "schema": "codex_scheduler.recover_task.v1",
            "ok": False,
            "reason": "task_disabled",
            "task_id": normalized_id,
        }
    required_confirm = confirmation_for(normalized_id)
    if not dry_run and confirm != required_confirm:
        return {
            "schema": "codex_scheduler.recover_task.v1",
            "ok": False,
            "reason": "confirmation_required",
            "task_id": normalized_id,
            "required_confirm": required_confirm,
        }

    lock = hooks.lock_factory()
    if not lock.acquire():
        return {
            "schema": "codex_scheduler.recover_task.v1",
            "ok": False,
            "reason": "scheduler_active",
            "task_id": normalized_id,
            "rule": "stop the scheduler service before a direct recovery; never race its state writer",
        }
    try:
        run = hooks.run_task(task, "manual_recovery", dry_run)
        result = dict(getattr(run, "__dict__", {}))
        if not dry_run:
            hooks.update_task_state(task, state, run)
            hooks.save_state(state)
        compact = hooks.compact_run_result(result)
        heartbeat = hooks.write_heartbeat(
            {
                "last_run_due_count": 1,
                "last_run_summary": [compact],
                "manual_recovery": {"task_id": normalized_id, "dry_run": dry_run, "ok": bool(result.get("ok"))},
            }
        )
        hooks.append_log(
            f"manual_recovery task={normalized_id} ok={bool(result.get('ok'))} dry_run={dry_run} record={result.get('record_path', '')}"
        )
        return {
            "schema": "codex_scheduler.recover_task.v1",
            "ok": bool(result.get("ok")),
            "task_id": normalized_id,
            "dry_run": dry_run,
            "run": result,
            "heartbeat": heartbeat,
            "state_updated": not dry_run,
            "failure_preserved": not bool(result.get("ok")),
        }
    finally:
        lock.release()
