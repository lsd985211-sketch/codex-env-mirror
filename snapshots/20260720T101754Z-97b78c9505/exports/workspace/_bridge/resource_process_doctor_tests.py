from __future__ import annotations

import unittest
from unittest.mock import patch

from resource_process_doctor import (
    PROCESS_PATTERNS,
    classify_group,
    classify_orphaned_stdio_host_group,
    codex_app_server_owner_state,
    mcp_budget_state,
    matches_process_pattern,
    process_host_chain_orphaned,
    process_reporting_safety_contract,
    protected_cleanup_evidence,
    resource_process_issues,
    powershell_json_command,
    resolve_powershell_executable,
    run_hidden_powershell,
)
from resource_process_reporting import cleanup_success_projection, doctor_projection


class ProcessHostChainTests(unittest.TestCase):
    def test_wsl_resolves_windows_powershell_when_alias_is_missing(self) -> None:
        with patch("resource_process_doctor.shutil.which", return_value=None):
            executable = resolve_powershell_executable()
        self.assertTrue(executable.lower().endswith("powershell.exe"))

    def test_powershell_command_uses_resolved_executable(self) -> None:
        with patch("resource_process_doctor.resolve_powershell_executable", return_value="/tmp/powershell.exe"):
            command = powershell_json_command("Write-Output '{}'")
        self.assertEqual(command[0], "/tmp/powershell.exe")

    def test_missing_powershell_returns_bounded_failure(self) -> None:
        with patch("resource_process_doctor.subprocess.run", side_effect=FileNotFoundError("powershell")):
            result = run_hidden_powershell(["powershell", "-NoProfile"], 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "powershell_unavailable")

    def test_missing_grandparent_marks_non_host_chain_orphaned(self) -> None:
        processes = {
            10: {"pid": 10, "parent_pid": 20, "command_line": "python mcp.py"},
            20: {"pid": 20, "parent_pid": 30, "command_line": "codex.exe mcp-server"},
            30: {"pid": 30, "parent_pid": 40, "command_line": "node codex.js mcp-server"},
        }
        self.assertTrue(process_host_chain_orphaned(processes[10], processes))

    def test_desktop_host_chain_is_not_orphaned(self) -> None:
        processes = {
            10: {"pid": 10, "parent_pid": 20, "command_line": "python mcp.py"},
            20: {
                "pid": 20,
                "parent_pid": 30,
                "command_line": r"C:\Program Files\WindowsApps\OpenAI.Codex_1.2.3.4_x64\app\resources\codex.exe app-server",
            },
        }
        self.assertFalse(process_host_chain_orphaned(processes[10], processes))

    def test_protected_cleanup_accepts_broken_host_chain_evidence(self) -> None:
        action = {
            "group": "bridge_server_v2",
            "protected": True,
            "would_keep_root_instance_pids": [99],
            "would_review_stop_root_instance_pids": [10],
            "orphan_batch_candidates": [{"parent_pid": 20, "pids": [10]}],
            "latest_batches_kept": [{"parent_pid": 50, "pids": [99]}],
        }
        proc = {
            "group": "bridge_server_v2",
            "instance_root": True,
            "host_parent_chain_orphaned": True,
        }
        evidence = protected_cleanup_evidence(action, 10, proc)
        self.assertTrue(evidence["ok"])
        self.assertTrue(evidence["checks"]["orphaned_host_chain"])

    def test_memory_only_pressure_remains_advisory(self) -> None:
        self.assertEqual(mcp_budget_state(9, 770.0), "advisory")
        self.assertEqual(mcp_budget_state(12, 770.0), "risk")
        self.assertEqual(mcp_budget_state(24, 100.0), "risk")

    def test_transient_fanout_does_not_create_cleanup_issue(self) -> None:
        group = {
            "group": "desktop_weixin_mcp",
            "category": "mcp",
            "count": 18,
            "root_instance_count": 9,
            "expected_max": 1,
            "effective_expected_max": 1,
            "working_set_mb": 100.0,
            "persistent_working_set_mb": 0.0,
            "persistent_root_instance_count": 0,
            "fanout_age_evidence_complete": True,
            "protected": False,
        }
        self.assertIsNone(classify_group(group))
        self.assertTrue(group["transient_fanout"])
        issues = resource_process_issues([group], {}, {"healthy": True, "issue": ""})
        codes = {item.get("code") for item in issues}
        self.assertNotIn("resource_process_fanout", codes)
        self.assertNotIn("mcp_session_multiplication_pressure", codes)

    def test_persistent_fanout_uses_age_qualified_count(self) -> None:
        issue = classify_group(
            {
                "group": "desktop_weixin_mcp",
                "count": 8,
                "root_instance_count": 4,
                "expected_max": 1,
                "effective_expected_max": 1,
                "persistent_root_instance_count": 3,
                "fanout_age_evidence_complete": True,
                "working_set_mb": 50.0,
                "protected": False,
            }
        )
        self.assertIsNotNone(issue)
        self.assertEqual(issue["count"], 3)
        self.assertEqual(issue["observed_root_instance_count"], 4)

    def test_orphan_signal_only_applies_to_session_owned_stdio(self) -> None:
        stdio_issue = classify_orphaned_stdio_host_group(
            {
                "group": "local_pmb_proxy",
                "orphaned_host_root_pids": [10],
                "orphaned_host_root_details": [{"pid": 10, "age_minutes": 3.0}],
                "protected": False,
            }
        )
        service_issue = classify_orphaned_stdio_host_group(
            {"group": "openclaw_gateway", "orphaned_host_root_pids": [20], "protected": True}
        )
        self.assertEqual(stdio_issue["code"], "mcp_orphaned_stdio_host_chain")
        self.assertIsNone(service_issue)

    def test_transient_orphan_remains_advisory_until_age_gate(self) -> None:
        issue = classify_orphaned_stdio_host_group(
            {
                "group": "local_pmb_proxy",
                "orphaned_host_root_pids": [10],
                "orphaned_host_root_details": [{"pid": 10, "age_minutes": 0.25}],
                "protected": False,
            }
        )
        self.assertEqual(issue["severity"], "advisory")
        self.assertFalse(issue["persistent_after_age_gate"])

    def test_powershell_text_does_not_own_codex_app_server(self) -> None:
        state = codex_app_server_owner_state(
            [
                {
                    "pid": 10,
                    "name": "powershell.exe",
                    "command_line": "powershell -Command codex.exe app-server --listen ws://127.0.0.1:18791",
                }
            ]
        )
        self.assertEqual(state["owner_count"], 0)
        self.assertEqual(state["issue"], "missing")

    def test_actual_codex_process_owns_app_server(self) -> None:
        state = codex_app_server_owner_state(
            [
                {
                    "pid": 10,
                    "name": "codex.exe",
                    "command_line": r"C:\OpenAI\Codex\bin\abc\codex.exe app-server --listen ws://127.0.0.1:18791",
                }
            ]
        )
        self.assertEqual(state["owner_count"], 1)
        self.assertTrue(state["healthy"])

    def test_reporting_contract_forbids_fixed_pid_cleanup(self) -> None:
        contract = process_reporting_safety_contract()

        self.assertTrue(contract["fixed_pid_cleanup_forbidden"])
        self.assertTrue(contract["parent_missing_alone_is_insufficient"])
        self.assertEqual(contract["cleanup_entrypoint"], "fresh_owner_repair_plan_then_safe_apply")
        self.assertIn("launch_batch_membership", contract["required_candidate_evidence"])

    def test_desktop_weixin_is_covered_by_session_lifecycle(self) -> None:
        specs = {item.group: item for item in PROCESS_PATTERNS}

        self.assertIn("desktop_weixin_mcp", specs)
        self.assertEqual(specs["desktop_weixin_mcp"].expected_max, 1)
        self.assertFalse(specs["desktop_weixin_mcp"].protected)

    def test_pythonw_pmb_daemon_remains_process_governed(self) -> None:
        specs = {item.group: item for item in PROCESS_PATTERNS}
        command = r'C:\Python\pythonw.exe -m pmb.cli daemon run --port 8765 --host 127.0.0.1'

        self.assertTrue(matches_process_pattern(specs["local_pmb_daemon"], command.lower()))

    def test_in_process_lazy_launcher_remains_proxy_governed(self) -> None:
        specs = {item.group: item for item in PROCESS_PATTERNS}
        command = r'C:\Python\python.exe C:\workspace\_bridge\mcp_profile_launcher.py cdev'

        self.assertTrue(matches_process_pattern(specs["mcp_lazy_stdio_proxy"], command.lower()))

    def test_cleanup_success_projection_preserves_actionable_summary(self) -> None:
        payload = {
            "schema": "resource_process.cleanup.v1",
            "ok": True,
            "apply_requested": True,
            "applied": True,
            "safe_apply": True,
            "selected_count": 2,
            "skipped_count": 1,
            "cleanup_ok": True,
            "post_validation_ok": True,
            "selected": [
                {"group": "playwright", "pid": 10, "age_minutes": 20, "selection_mode": "old_batch", "stop_result": {"ok": True, "dry_run": False}},
                {"group": "playwright", "pid": 20, "age_minutes": 18, "selection_mode": "old_batch", "stop_result": {"ok": True, "dry_run": False}},
            ],
            "results": [
                {"stop_result": {"ok": True, "dry_run": False}},
                {"stop_result": {"ok": True, "dry_run": False}},
            ],
            "skipped": [{"reason": "latest_batch_kept"}],
        }

        result = cleanup_success_projection(payload)

        self.assertEqual(result["selected_by_group"], {"playwright": 2})
        self.assertEqual(result["result_counts"], {"stopped": 2})
        self.assertEqual(result["skipped_reason_counts"], {"latest_batch_kept": 1})
        self.assertEqual(len(result["selected_preview"]), 2)

    def test_doctor_projection_excludes_raw_process_rows(self) -> None:
        payload = {
            "schema": "resource_process.doctor.v1",
            "ok": False,
            "summary": {"root_instance_count": 20},
            "issues": [{"severity": "risk", "code": "fanout", "message": "too many", "manual_action": "use owner cleanup", "raw": "ignored"}],
            "snapshot": {"groups": [{"group": "playwright", "count": 8, "root_instance_count": 2, "working_set_mb": 50}], "processes": [{"command_line": "large"}]},
        }

        result = doctor_projection(payload)

        self.assertNotIn("snapshot", result)
        self.assertNotIn("processes", result)
        self.assertEqual(result["issues"][0]["manual_action"], "use owner cleanup")
        self.assertEqual(result["groups"][0]["group"], "playwright")


if __name__ == "__main__":
    unittest.main()
