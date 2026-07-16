from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import codex_baseline_update as baseline_update  # noqa: E402
import system_membership as membership  # noqa: E402
import workflow_orchestrator as workflow  # noqa: E402


def tombstone(member: str = "legacy-member", replacement: str = "current-member") -> dict:
    return {
        "id": f"mcp:{member}",
        "system": "mcp",
        "member": member,
        "kind": "mcp_server",
        "lifecycle": "decommissioned",
        "owner": "test owner",
        "replacement": replacement,
        "reason": "test retirement",
        "history_policy": "isolated evidence only",
        "prevention_evidence": ["test guard"],
    }


class SystemMembershipTests(unittest.TestCase):
    def test_mirror_source_projection_is_active_member_driven(self) -> None:
        result = membership.mirror_source_projection()
        self.assertTrue(result["ok"], result["issues"])
        self.assertGreaterEqual(len(result["members"]), 1)
        self.assertIn("workspace-bridge-source", result["source_ids"])
        self.assertIn("system-membership-snapshot", result["generated_source_ids"])
        self.assertIn("workspace:_bridge/", result["change_roots"])

    def test_active_member_plan_requires_exit_strategy(self) -> None:
        result = membership.plan("mcp", "new-member", "mcp_server")
        self.assertTrue(result["ok"])
        self.assertIn("exit_strategy", result["required_surface_keys"])

    def test_impact_rejects_partial_system_file_coverage(self) -> None:
        result = membership.impact(
            [
                "_bridge/docs/maintenance_surface_map.md",
                "_bridge/unregistered_owner.py",
            ]
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["unmapped_system_changed"], ["_bridge/unregistered_owner.py"])
        self.assertTrue(
            any(item.get("code") == "system_change_partially_unmapped" for item in result["blockers"])
        )

    def test_environment_mirror_and_maintenance_registry_are_fully_mapped(self) -> None:
        result = membership.impact(
            [
                "_bridge/codex_environment_mirror.py",
                "_bridge/codex_environment_mirror_tests.py",
                "C:/Users/example/codex-env-mirror/scripts/mirror_cli.py",
                "_bridge/maintenance_capability_registry.py",
                "_bridge/slash_commands/commands.json",
            ]
        )
        self.assertTrue(result["ok"], result["blockers"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["unmapped_system_changed"], [])
        self.assertTrue({"backup", "workflow"}.issubset(result["affected_systems"]))
        self.assertIn("recovery_mirror", membership.CONTRACTS["backup"]["member_kinds"])

    def test_windows_memory_governance_impact_is_fully_mapped(self) -> None:
        result = membership.impact(
            [
                "_bridge/windows_memory_governance.py",
                "_bridge/windows_memory_governance_tests.py",
            ]
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["unmapped_system_changed"], [])
        self.assertIn("memory", result["affected_systems"])

    def test_project_checkpoint_artifacts_are_fully_mapped(self) -> None:
        result = membership.impact(
            [
                "_bridge/project_checkpoint_finalize.py",
                "_bridge/project_checkpoint_finalize_tests.py",
                "_bridge/shared/checkpoints/MANIFEST.md",
                "_bridge/shared/checkpoints/example/20260715-verified-change.md",
            ]
        )
        self.assertTrue(result["ok"], result["blockers"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["unmapped_system_changed"], [])
        self.assertTrue({"workflow", "memory"}.issubset(result["affected_systems"]))

    def test_hub_first_filesystem_repair_files_are_fully_mapped(self) -> None:
        result = membership.impact(
            [
                "_bridge/mcp_capability_routes.py",
                "_bridge/mcp_capability_routes_tests.py",
                "_bridge/runtime/mcp_capability_routes.json",
                "_bridge/mcp_session_doctor.py",
                "_bridge/mcp_session_profile_drift.py",
                "_bridge/mcp_session_profile_drift_tests.py",
            ]
        )
        self.assertTrue(result["ok"], result["blockers"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["unmapped_system_changed"], [])
        self.assertEqual(result["affected_systems"], ["mcp"])
        self.assertTrue(
            {"registration", "diagnostics", "derived_route_index", "hub_adapter", "maintenance_regression"}.issubset(
                result["affected_surfaces"]
            )
        )

    def test_resource_process_module_family_is_fully_mapped(self) -> None:
        result = membership.impact(
            [
                "_bridge/resource_process_doctor.py",
                "_bridge/resource_process_doctor_tests.py",
                "_bridge/resource_process_reporting.py",
            ]
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["unmapped_system_changed"], [])
        self.assertTrue({"resource", "mcp", "startup"}.issubset(result["affected_systems"]))

    def test_self_update_governance_module_family_is_fully_mapped(self) -> None:
        result = membership.impact(
            [
                "_bridge/self_update_governance.py",
                "_bridge/self_update_governance_tests.py",
            ]
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["unmapped_system_changed"], [])
        self.assertTrue(
            {"workflow", "skills", "memory", "resource"}.issubset(result["affected_systems"])
        )

    def test_shared_output_and_resource_delegation_are_fully_mapped(self) -> None:
        result = membership.impact(
            [
                "_bridge/bounded_output.py",
                "_bridge/codex_resource_delegation.py",
            ]
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["unmapped_system_changed"], [])
        self.assertTrue({"workflow", "mcp", "resource", "startup"}.issubset(result["affected_systems"]))

    def test_active_member_plan_requires_end_to_end_integration_surfaces(self) -> None:
        result = membership.plan("mcp", "new-member", "mcp_server")
        required = set(result["required_surface_keys"])
        self.assertTrue(
            {
                "member_identity",
                "activation_contract",
                "dependency_contract",
                "execution_contract",
                "result_contract",
                "maintenance_regression",
            }.issubset(required)
        )
        self.assertIn("batch_identity_rule", result["integration_policy"])
        self.assertIn("consumption_rule", result["integration_policy"])
        self.assertIn("concurrency_rule", result["integration_policy"])
        self.assertIn("reload_or_restart_boundary_is_explicit_and_validated", result["completion_checks"])

    def test_resource_contract_supports_current_owner_member_kinds(self) -> None:
        for member, kind in (
            ("resource_scheduler", "batch_scheduler"),
            ("resource_node_package_owner", "package_owner"),
            ("cloakbrowser_owner", "browser_owner"),
        ):
            with self.subTest(kind=kind):
                result = membership.plan("resource", member, kind)
                self.assertTrue(result["ok"])
                self.assertEqual(result["kind"], kind)

    def test_integration_policy_rejects_partial_success_and_global_quiescence_assumptions(self) -> None:
        policy = membership.INTEGRATION_POLICY
        self.assertIn("acceptance predicate", policy["success_rule"])
        self.assertIn("records consumption", policy["consumption_rule"])
        self.assertIn("must not require legitimate production state to remain globally static", policy["concurrency_rule"])
        self.assertIn("optional or session-bound members remain nonblocking", policy["optional_member_rule"])

    def test_retirement_plan_covers_all_purge_surfaces(self) -> None:
        result = membership.retirement_plan(
            "mcp", "legacy-member", "mcp_server", "current-member", "obsolete"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["lifecycle"], "decommissioning")
        self.assertTrue(
            set(membership.RETIREMENT_PURGE_SURFACES).issubset(result["required_surface_keys"])
        )

    def test_retirement_requires_reason_and_replacement_decision(self) -> None:
        result = membership.plan(
            "mcp", "legacy-member", "mcp_server", "decommissioning", "", ""
        )
        codes = {item["code"] for item in result["blockers"]}
        self.assertIn("retirement_reason_missing", codes)
        self.assertIn("replacement_decision_missing", codes)

    def test_reintroduced_registration_is_detected(self) -> None:
        issues = membership.retirement_state_issues(
            [tombstone()], {"legacy-member"}, guidance_paths=[]
        )
        self.assertTrue(
            any(item.get("code") == "decommissioned_member_registered" for item in issues)
        )

    def test_current_guidance_reference_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "current.md"
            path.write_text("route through legacy-member", encoding="utf-8")
            issues = membership.retirement_state_issues(
                [tombstone()], set(), guidance_paths=[path]
            )
        self.assertTrue(
            any(item.get("code") == "decommissioned_member_in_current_guidance" for item in issues)
        )

    def test_isolated_historical_reference_does_not_reactivate_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backups" / "history.md"
            path.parent.mkdir()
            path.write_text("legacy-member", encoding="utf-8")
            issues = membership.retirement_state_issues(
                [tombstone()], set(), guidance_paths=[path]
            )
        self.assertEqual(issues, [])

    def test_active_retired_implementation_path_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active_path = root / "legacy" / "server.py"
            active_path.parent.mkdir()
            active_path.write_text("print('legacy')", encoding="utf-8")
            item = tombstone()
            item["active_trace_paths"] = [
                {
                    "path": "legacy/server.py",
                    "surface": "implementation_exit",
                    "kind": "implementation_entry",
                }
            ]
            issues = membership.retirement_state_issues(
                [item], set(), guidance_paths=[], active_root=root
            )
        issue = next(item for item in issues if item.get("code") == "decommissioned_member_active_path")
        self.assertEqual(issue["member"], "legacy-member")
        self.assertEqual(issue["surface"], "implementation_exit")

    def test_isolated_archive_path_is_not_an_active_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "_bridge" / "archive" / "legacy" / "server.py"
            archive_path.parent.mkdir(parents=True)
            archive_path.write_text("print('legacy')", encoding="utf-8")
            item = tombstone()
            item["active_trace_paths"] = [
                {"path": "_bridge/archive/legacy/server.py", "surface": "implementation_exit"}
            ]
            issues = membership.retirement_state_issues(
                [item], set(), guidance_paths=[], active_root=root
            )
        self.assertEqual(issues, [])

    def test_incomplete_tombstone_is_detected(self) -> None:
        broken = tombstone()
        broken["replacement"] = ""
        issues = membership.retirement_state_issues([broken], set(), guidance_paths=[])
        issue = next(item for item in issues if item.get("code") == "retirement_tombstone_incomplete")
        self.assertIn("replacement", issue["missing"])

    def test_retirement_signal_emits_negative_directives(self) -> None:
        result = membership.retirement_signal(
            message="remove legacy-member",
            tombstones=[tombstone()],
            configured_names=set(),
            guidance_paths=[],
        )
        self.assertEqual(result["status"], "guard_active")
        self.assertEqual(result["directive"], "enforce_negative_tombstone")
        self.assertIn("legacy-member", result["do_not_route"])
        self.assertIn("legacy-member", result["do_not_invoke"])
        self.assertIn("legacy-member", result["do_not_generate"])
        self.assertIn("legacy-member", result["do_not_recommend"])
        self.assertIn("legacy-member", result["do_not_repair_or_restore"])
        self.assertEqual(result["use_replacement"]["legacy-member"], "current-member")
        self.assertEqual(result["required_surfaces"], result["purge_surfaces"])
        self.assertEqual(set(result["proof_surfaces"]), set(membership.RETIREMENT_PROOF_SURFACES))
        self.assertTrue(result["codex_instructions"])

    def test_retirement_signal_requires_purge_for_active_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active_path = root / "legacy-member"
            active_path.mkdir()
            item = tombstone()
            item["active_trace_paths"] = [{"path": "legacy-member"}]
            result = membership.retirement_signal(
                message="retire legacy-member",
                tombstones=[item],
                configured_names=set(),
                guidance_paths=[],
                active_root=root,
            )
        self.assertEqual(result["status"], "purge_required")
        self.assertFalse(result["ok"])

    def test_unrelated_task_does_not_emit_retirement_noise(self) -> None:
        result = membership.retirement_signal(
            message="format the current report",
            tombstones=[tombstone()],
            configured_names=set(),
            guidance_paths=[],
        )
        self.assertFalse(result["triggered"])
        self.assertEqual(result["directive"], "none")

    def test_baseline_adoption_uses_tombstone_authority(self) -> None:
        existing = {
            "decommissioned_mcp": {"legacy-member": {"replaced_by": "current-member"}},
            "expected_mcp": {"legacy-member": {"command": "old"}},
        }
        config = {
            "mcp_servers": {
                "legacy-member": {"command": "old"},
                "current-member": {"command": "new"},
            }
        }
        adopted = baseline_update.mcp_specs(config, existing)
        self.assertNotIn("legacy-member", adopted)
        self.assertIn("current-member", adopted)

    def test_workflow_plan_receives_retirement_guard(self) -> None:
        signal = membership.retirement_signal(
            message="retire legacy-member",
            tombstones=[tombstone()],
            configured_names=set(),
            guidance_paths=[],
        )
        with patch.object(workflow, "build_retirement_signal", return_value=signal):
            result = workflow.build_plan("retire legacy-member", detail="micro")
        guard = result.get("retirement_guard", {})
        self.assertTrue(guard.get("triggered"))
        self.assertIn("legacy-member", guard.get("do_not_route", []))


if __name__ == "__main__":
    unittest.main()
