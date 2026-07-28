#!/usr/bin/env python3
"""Focused tests for the typed Windows execution-plane agent."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import windows_execution_agent as owner
from shared import codex_scheduler_runner


def task_row(task_name: str, **changes: object) -> dict[str, object]:
    policy = owner.TASK_POLICIES[task_name]
    row: dict[str, object] = {
        "task_name": task_name,
        "state": "Ready",
        "user_id": "user",
        "logon_type": "Interactive",
        "run_level": policy["run_level"],
        "execute": "C:\\Windows\\System32\\wscript.exe",
        "arguments": policy["action_marker"],
    }
    row.update(changes)
    return row


def full_inventory() -> dict[str, object]:
    return {
        "ok": True,
        "tasks": [task_row(name) for name, policy in owner.TASK_POLICIES.items() if policy.get("required")],
    }


class WindowsExecutionAgentTests(unittest.TestCase):
    def test_capabilities_expose_only_fixed_operation_ids(self) -> None:
        payload = owner.capabilities()
        operations = {item["operation"] for item in payload["operations"]}
        self.assertIn("local_mcp_hub.start", operations)
        self.assertIn("desktop.start_elevated", operations)
        self.assertIn("wsl_control_plane.wake", operations)
        self.assertNotIn("scheduler.start", operations)
        self.assertNotIn("mtp_media_archive.run", operations)
        self.assertFalse(payload["boundaries"]["arbitrary_command"])
        self.assertFalse(payload["boundaries"]["arbitrary_arguments"])
        self.assertEqual("codex_scheduler_runner", payload["boundaries"]["periodic_execution_owner"])
        self.assertFalse(payload["boundaries"]["second_scheduler_created"])

    def test_existing_scheduler_owns_periodic_agent_validation(self) -> None:
        task = next(item for item in codex_scheduler_runner.DEFAULT_TASKS if item["id"] == "windows_execution_plane_validate")
        self.assertEqual({"type": "interval", "every_seconds": 1800}, task["trigger"])
        self.assertEqual(
            ["python", "_bridge/windows_execution_agent.py", "validate"],
            task["action"]["command"],
        )
        self.assertEqual("read-only", task["policy"]["mode"])

    def test_windows_scheduler_task_is_only_a_limited_wsl_wake_lane(self) -> None:
        policy = owner.TASK_POLICIES["CodexSchedulerRunner"]
        self.assertEqual("maintenance_scheduler_service", policy["owner"])
        self.assertEqual("standard_user", policy["lane"])
        self.assertEqual("Limited", policy["run_level"])
        self.assertEqual(["wsl_control_plane.wake"], policy["operations"])
        self.assertEqual("codex-maintenance-scheduler.service", policy["action_marker"])

    def test_validate_accepts_declared_task_lanes(self) -> None:
        payload = owner.validate(inventory=full_inventory())
        self.assertTrue(payload["ok"])
        self.assertEqual([], payload["issues"])

    def test_inventory_script_receives_only_catalogued_task_literals(self) -> None:
        with patch.object(owner.Path, "is_file", return_value=True), patch.object(
            owner,
            "_run",
            return_value={"ok": True, "returncode": 0, "stdout": "[]", "stderr": ""},
        ) as runner:
            payload = owner._task_inventory()
        self.assertTrue(payload["ok"])
        script = runner.call_args.args[0][-1]
        self.assertNotIn("__TASK_NAMES__", script)
        self.assertIn("'CodexLocalMcpHub'", script)
        self.assertNotIn("CODEX_WINDOWS_AGENT_TASKS", script)

    def test_validate_rejects_system_principal_and_run_level_drift(self) -> None:
        inventory = full_inventory()
        inventory["tasks"][0]["user_id"] = r"NT AUTHORITY\SYSTEM"
        inventory["tasks"][1]["run_level"] = "Highest"
        payload = owner.validate(inventory=inventory)
        codes = {item["code"] for item in payload["issues"]}
        self.assertIn("windows_system_principal_forbidden", codes)
        self.assertIn("windows_task_run_level_drift", codes)

    def test_validate_rejects_action_drift_and_work_git_target(self) -> None:
        inventory = full_inventory()
        inventory["tasks"][0]["arguments"] = r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\unsafe.ps1"
        payload = owner.validate(inventory=inventory)
        codes = {item["code"] for item in payload["issues"]}
        self.assertIn("windows_task_action_drift", codes)
        self.assertIn("windows_task_targets_work_git", codes)

    def test_invoke_requires_exact_confirmation_without_running(self) -> None:
        with patch.object(owner, "_run") as runner:
            payload = owner.invoke("local_mcp_hub.start", "")
        self.assertFalse(payload["ok"])
        self.assertEqual("explicit_confirmation_required", payload["reason"])
        runner.assert_not_called()

    def test_invoke_uses_only_catalogued_task_name(self) -> None:
        with patch.object(owner.Path, "is_file", return_value=True), patch.object(
            owner,
            "_run",
            return_value={"ok": True, "returncode": 0, "stdout": "SUCCESS", "stderr": ""},
        ) as runner:
            payload = owner.invoke(
                "local_mcp_hub.start",
                "RUN-WINDOWS-EXECUTION:local_mcp_hub.start",
            )
        self.assertTrue(payload["ok"])
        command = runner.call_args.args[0]
        self.assertEqual(command[-4:], [str(owner.schtasks_path()), "/Run", "/TN", "CodexLocalMcpHub"])
        self.assertFalse(payload["business_result_consumed"])

    def test_unknown_operation_is_blocked(self) -> None:
        payload = owner.invoke_plan("shell.run")
        self.assertFalse(payload["ok"])
        self.assertEqual("operation_not_allowlisted", payload["reason"])

    def test_mtp_archive_operation_is_not_exposed_without_a_safe_backend(self) -> None:
        plan = owner.invoke_plan("mtp_media_archive.run")
        self.assertFalse(plan["ok"])
        self.assertEqual("operation_not_allowlisted", plan["reason"])


if __name__ == "__main__":
    unittest.main()
