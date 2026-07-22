#!/usr/bin/env python3
"""Focused isolated behavior regression for persistent_task_kernel.

Ownership: task lifecycle behavior checks.
Non-goals: mutate production scheduler, mail, bridge, or runtime state.
State behavior: temporary SQLite database only.
Caller context: targeted validation for the sidecar kernel.
"""

from __future__ import annotations

import json

from persistent_task_kernel import behavior_evaluation
from shared import codex_scheduler_runner


def scheduler_integration_checks() -> list[dict[str, object]]:
    tasks = [
        task
        for task in codex_scheduler_runner.DEFAULT_TASKS
        if task.get("id") == "persistent_task_kernel_recover_expired"
    ]
    task = tasks[0] if len(tasks) == 1 else {}
    command = task.get("action", {}).get("command", []) if isinstance(task, dict) else []
    policy = task.get("policy", {}) if isinstance(task, dict) else {}
    return [
        {"name": "single_scheduler_recovery_registration", "ok": len(tasks) == 1},
        {
            "name": "scheduler_calls_only_guarded_expired_lease_recovery",
            "ok": command
            == [
                "python",
                "_bridge/persistent_task_kernel.py",
                "recover-expired",
                "--apply",
                "--confirm",
                "RECOVER-EXPIRED-TASKS",
            ],
        },
        {
            "name": "scheduler_recovery_does_not_claim_or_execute",
            "ok": not ({"claim", "begin", "worker", "dispatch"} & {str(item).lower() for item in command}),
        },
        {
            "name": "scheduler_recovery_effect_is_bounded",
            "ok": task.get("enabled") is True
            and policy.get("mode") == "controlled-state-reconciliation"
            and "never claim or execute" in str(policy.get("allowed_effect") or ""),
        },
    ]


def main() -> int:
    result = behavior_evaluation()
    checks = scheduler_integration_checks()
    result.setdefault("checks", []).extend(checks)
    result["ok"] = bool(result.get("ok")) and all(bool(item["ok"]) for item in checks)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
