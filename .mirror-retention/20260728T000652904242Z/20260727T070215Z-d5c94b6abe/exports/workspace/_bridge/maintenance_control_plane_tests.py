#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import maintenance_capability_registry
import mcp_capability_routes
import local_mcp_hub_owner_mcp
import global_coherence_doctor
import codex_config_guard
import codex_state_audit
import codex_workflow_entry
import workflow_action_synthesis
import workflow_owner_facade
import workflow_plan_detail
import workflow_orchestrator
from bounded_output import aggregate_validator_cli_payload, bounded_payload, governed_cli_payload, json_size_bytes, output_evidence_policy
from shared import codex_scheduler_runner, system_maintenance_cli
from mobile_openclaw_bridge import mobile_maintenance
from mobile_openclaw_bridge import bridge_maintenance_cli


class BoundedOutputTests(unittest.TestCase):
    def test_startup_audit_receipt_groups_success_and_keeps_failure_detail(self) -> None:
        receipt = codex_state_audit.compact_check_receipt(
            [
                codex_state_audit.Check("baseline_parse", True, "baseline.json"),
                codex_state_audit.Check("expected_mcp_registered", True, "mcp=['local-mcp-hub']"),
                codex_state_audit.Check("project_config_parse", False, "invalid TOML at line 4"),
            ]
        )
        self.assertEqual(receipt["check_count"], 3)
        self.assertEqual(receipt["failed_count"], 1)
        self.assertEqual(receipt["failures"][0]["detail"], "invalid TOML at line 4")
        self.assertEqual(
            {surface["surface"] for surface in receipt["surfaces"]},
            {"baseline", "mcp_configuration", "project_configuration"},
        )

    def test_maintenance_registry_prefers_module_identity_over_incidental_text(self) -> None:
        self.assertEqual(
            maintenance_capability_registry.infer_system(
                "_bridge/codex_session_store_doctor.py",
                "checkpoint and archive recommendations",
            ),
            "startup",
        )

    def test_maintenance_registry_maps_usb_owner_to_hardware(self) -> None:
        self.assertEqual(
            maintenance_capability_registry.infer_system(
                "_bridge/usb_device_owner.py",
                "read-only USB inventory and Android status",
            ),
            "hardware",
        )

    def test_maintenance_registry_maps_windows_hardware_owner_to_hardware(self) -> None:
        self.assertEqual(
            maintenance_capability_registry.infer_system(
                "_bridge/windows_hardware_owner.py",
                "read-only all-device PnP inventory and diagnostics",
            ),
            "hardware",
        )

    def test_maintenance_registry_maps_music_library_owner_to_audio(self) -> None:
        self.assertEqual(
            maintenance_capability_registry.infer_system(
                "_bridge/music_library_owner.py",
                "transactional local music library organization",
            ),
            "audio",
        )

    def test_maintenance_registry_extracts_windows_hardware_actions(self) -> None:
        actions = maintenance_capability_registry.extract_actions(
            "snapshot device problems classes events diff doctor validate"
        )

        self.assertEqual(
            actions,
            ["snapshot", "device", "problems", "classes", "doctor", "validate", "events", "diff"],
        )

    def test_maintenance_registry_extracts_usb_read_only_actions(self) -> None:
        actions = maintenance_capability_registry.extract_actions(
            "snapshot doctor events watch diff android validate"
        )

        self.assertEqual(
            actions,
            ["snapshot", "doctor", "validate", "events", "watch", "diff", "android"],
        )

    def test_maintenance_registry_extracts_parameterized_transport_action(self) -> None:
        actions = maintenance_capability_registry.extract_actions(
            "transport [--busid <bus-port>] doctor validate"
        )

        self.assertEqual(actions, ["doctor", "validate", "transport"])

    def test_wsl_workspace_row_preserves_pipe_separated_commands(self) -> None:
        row = next(
            item
            for item in maintenance_capability_registry.parse_surface_map()
            if item["module_path"] == "_bridge/wsl_workspace_owner.py"
        )

        self.assertEqual(row["system"], "wsl_workspace")
        for action in ("status", "plan", "validate", "handoff", "cleanup-plan", "mirror-export", "work-git-release"):
            self.assertIn(action, row["actions"])
        self.assertIn("bootstrap", row["actions"])
        self.assertNotIn("bootstrap", row["read_only_actions"])

    def test_maintenance_registry_never_labels_apply_or_rollback_read_only(self) -> None:
        row = next(
            item
            for item in maintenance_capability_registry.parse_surface_map()
            if item["module_path"] == "_bridge/usb_device_control.py"
        )

        self.assertIn("apply", row["actions"])
        self.assertIn("rollback", row["actions"])
        self.assertNotIn("apply", row["read_only_actions"])
        self.assertNotIn("rollback", row["read_only_actions"])
        self.assertNotIn("device", row["actions"])

    def test_iteration_owner_apply_is_discoverable_but_not_read_only(self) -> None:
        from workflow_iteration_owner import command_contract

        row = next(
            item
            for item in maintenance_capability_registry.parse_surface_map()
            if item["module_path"] == "_bridge/workflow_iteration_owner.py"
        )

        self.assertEqual(row["system"], "workflow")
        contract = command_contract()
        self.assertEqual(contract["actions"], row["actions"])
        self.assertEqual(contract["read_only_actions"], row["read_only_actions"])
        self.assertEqual(row["command_contract_source"], "owner")
        for action in ("apply", "validate", "resolve", "consume-approved"):
            self.assertIn(action, row["actions"])
            self.assertNotIn(action, row["read_only_actions"])

    def test_maintenance_index_signature_changes_with_owner_command_contract(self) -> None:
        row = {
            "module_path": "_bridge/workflow_iteration_owner.py",
            "actions": ["plan", "command-contract"],
            "read_only_actions": ["plan", "command-contract"],
            "command_contract_source": "owner",
            "command_contract_error": "",
            "parser_signature": "parser-v1",
        }
        first = maintenance_capability_registry.source_signature([row])
        second = maintenance_capability_registry.source_signature([
            {**row, "read_only_actions": ["command-contract"], "parser_signature": "parser-v2"}
        ])

        self.assertNotEqual(first, second)

    def test_success_failure_evidence_policy_is_machine_readable(self) -> None:
        policy = output_evidence_policy()
        self.assertEqual(policy["success"], "bounded_traceable_summary")
        self.assertEqual(policy["failure"], "decision_complete_inline_evidence")
        self.assertEqual(policy["full"], "richer_bounded_projection_with_artifact_reference")
        self.assertTrue(policy["failure_reference_required"])

    def test_governed_cli_bounds_success_but_keeps_failure_evidence(self) -> None:
        success = governed_cli_payload(
            {"ok": True, "schema": "ok.v1", "snapshot": {"rows": ["x" * 1000] * 100}},
            full_result_ref="command:test --full",
            max_success_bytes=1200,
        )
        failure_payload = {
            "ok": False,
            "schema": "failed.v1",
            "issues": [{"code": "root_cause", "detail": "x" * 5000}],
        }
        failure = governed_cli_payload(failure_payload, full_result_ref="command:test --full")
        full = governed_cli_payload(
            {"ok": True, "schema": "ok.v1", "snapshot": {"rows": ["x" * 1000] * 100}},
            full=True,
            full_result_ref="command:test --full",
            max_success_bytes=1200,
            max_full_bytes=6000,
        )
        self.assertTrue(success["output_budget"]["truncated"])
        self.assertEqual(success["output_mode"], "default_bounded")
        self.assertEqual(success["raw_result_ref"], "command:test --full")
        self.assertEqual(failure["output_mode"], "failure_bounded")
        self.assertEqual(failure["issues"][0]["code"], "root_cause")
        self.assertEqual(failure["raw_result_ref"], "command:test --full")
        self.assertEqual(full["output_mode"], "full_bounded")
        self.assertGreater(full["output_budget"]["max_inline_bytes"], success["output_budget"]["max_inline_bytes"])

    def test_bounded_payload_keeps_nested_failure_rows_at_depth_boundary(self) -> None:
        result = bounded_payload(
            {
                "schema": "closeout.v1",
                "online_access_gate": {
                    "ok": False,
                    "blockers": [
                        {
                            "code": "direct_web_without_resource_exception",
                            "message": "resource route evidence is missing",
                            "next_action": "run the configured resource route chain",
                        }
                    ],
                    "large_context": {"rows": ["x"] * 100},
                },
            },
            max_bytes=900,
            preserve_keys=("online_access_gate",),
        )

        gate = result["online_access_gate"]
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["blockers"][0]["code"], "direct_web_without_resource_exception")
        self.assertIn("next_action", gate["blockers"][0])

    def test_required_route_fields_survive_compression(self) -> None:
        payload = {
            "schema": "workflow.route.v1",
            "required_gates": [{"owner": "workspace_editing", "stop_if": ["backup_missing"]}],
            "policy_decisions": [{"rule_id": "workflow.closeout", "decision": "required"}],
            "owner": "workflow_owner",
            "required_next_action": "run owner validation",
            "stop_if": ["validation_failed"],
            "repeated_context": ["x" * 1000] * 30,
        }

        result = bounded_payload(payload, max_bytes=1800, artifact_ref="artifact:route/full")

        self.assertEqual(result["required_gates"], payload["required_gates"])
        self.assertEqual(result["policy_decisions"], payload["policy_decisions"])
        self.assertEqual(result["owner"], "workflow_owner")
        self.assertEqual(result["required_next_action"], "run owner validation")
        self.assertEqual(result["stop_if"], ["validation_failed"])
        self.assertEqual(
            result["output_budget"]["functional_compression"]["functional_integrity"],
            "preserved",
        )
        self.assertNotIn("compression_blocked", result)

    def test_oversized_required_content_requires_reference_instead_of_silent_loss(self) -> None:
        payload = {
            "schema": "workflow.route.v1",
            "required_gates": [
                {
                    "owner": f"owner-{index}",
                    "completion": "verify " + "x" * 300,
                    "stop_if": [f"gate-{index}-failed"],
                }
                for index in range(20)
            ],
            "policy_decisions": [{"rule_id": f"rule-{index}", "decision": "required"} for index in range(20)],
            "required_next_action": "consume every gate before acting",
        }

        result = bounded_payload(payload, max_bytes=700, artifact_ref="artifact:route/full")
        compression = result["output_budget"]["functional_compression"]

        self.assertTrue(result["compression_blocked"])
        self.assertEqual(compression["functional_integrity"], "reference_required")
        self.assertIn("required_gates", compression["reference_required"])
        self.assertIn("policy_decisions", compression["reference_required"])
        self.assertEqual(compression["safe_next_step"], "fetch the complete result from artifact_ref before acting")
        self.assertNotIn("functional_compression", result)

    def test_oversized_required_content_without_reference_is_explicitly_blocked(self) -> None:
        result = bounded_payload(
            {
                "ok": False,
                "error": "failure detail " + "x" * 1000,
                "next_action": "repair action " + "y" * 1000,
            },
            max_bytes=300,
        )
        compression = result["output_budget"]["functional_compression"]

        self.assertTrue(result["compression_blocked"])
        self.assertEqual(compression["functional_integrity"], "blocked_no_reference")
        self.assertEqual(
            compression["safe_next_step"],
            "rerun with an explicit full-output/artifact destination before acting",
        )

    def test_caller_can_declare_task_specific_functional_field(self) -> None:
        result = bounded_payload(
            {
                "permission_boundary": "requires explicit approval " + "x" * 1000,
                "context": ["duplicate"] * 100,
            },
            max_bytes=300,
            required_keys=("permission_boundary",),
            artifact_ref="artifact:permission/full",
        )
        compression = result["output_budget"]["functional_compression"]

        self.assertTrue(result["compression_blocked"])
        self.assertIn("permission_boundary", compression["reference_required"])

    def test_aggregate_validator_keeps_actionable_failed_rows_and_reference(self) -> None:
        payload = {
            "schema": "aggregate.validate.v1",
            "ok": False,
            "checks": [
                {"name": "healthy", "ok": True},
                {
                    "name": "owner_route",
                    "ok": False,
                    "reason": "owner command timed out",
                    "next_action": "run owner validate",
                    "validation_command": "python owner.py validate --full",
                },
            ],
        }
        result = aggregate_validator_cli_payload(payload, full_result_ref="command:test validate --full")
        self.assertEqual(result["failed_check_count"], 1)
        self.assertEqual(result["actionable_failures"][0]["name"], "owner_route")
        self.assertEqual(result["actionable_failures"][0]["next_action"], "run owner validate")
        self.assertEqual(result["raw_result_ref"], "command:test validate --full")

    def test_aggregate_validator_surfaces_contract_failure_when_children_are_missing(self) -> None:
        result = aggregate_validator_cli_payload(
            {"schema": "aggregate.validate.v1", "ok": False},
            full_result_ref="command:test validate --full",
        )
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(result["actionable_failures"][0]["code"], "aggregate_failed_without_actionable_rows")

    def test_aggregate_validator_keeps_nonblocking_issues_on_success(self) -> None:
        result = aggregate_validator_cli_payload(
            {
                "schema": "aggregate.validate.v1",
                "ok": True,
                "checks": [{"name": "startup", "ok": True}],
                "issues": [{"code": "runtime_drift", "severity": "advisory", "next_action": "refresh runtime"}],
            },
            full_result_ref="command:test validate --full",
        )
        self.assertEqual(result["issue_count"], 1)
        self.assertEqual(result["actionable_issues"][0]["code"], "runtime_drift")

    def test_registered_aggregate_validators_use_actionable_failure_projection(self) -> None:
        payload = {
            "schema": "aggregate.validate.v1",
            "ok": False,
            "checks": [{"name": "child", "ok": False, "reason": "failed", "next_action": "repair child"}],
        }
        projections = (
            workflow_orchestrator.cli_projection(payload, "validate"),
            mcp_capability_routes.cli_projection(payload, "validate"),
            codex_config_guard.cli_projection(payload, "validate"),
        )
        for result in projections:
            self.assertEqual(result["actionable_failures"][0]["name"], "child")
            self.assertEqual(result["actionable_failures"][0]["next_action"], "repair child")
            self.assertIn("--full", result["raw_result_ref"])

    def test_owner_health_advisory_failure_is_actionable_but_not_blocking(self) -> None:
        owner_issue = {
            "system": "bridge",
            "name": "mobile_bridge",
            "severity": "advisory",
            "ok": False,
            "owner_schema": "mobile.doctor.v1",
            "owner_status": "advisory",
            "elapsed_ms": 25,
            "result_ref": "command:python owner.py doctor --full",
            "diagnostics": {
                "reason": "External config dependency is degraded.",
                "next_action": "Run config guard doctor.",
                "diagnostic_count": 1,
                "items": [{"severity": "high", "code": "external_config", "scope": "external_dependency"}],
            },
        }
        issues = global_coherence_doctor.check_owner_health([owner_issue])
        with patch.object(global_coherence_doctor, "doctor", return_value={"issues": issues, "status": "advisory"}):
            result = global_coherence_doctor.validate()
        self.assertFalse(result["blockers"])
        self.assertTrue(result["ok"])
        self.assertEqual(issues[0]["root_cause"], "External config dependency is degraded.")
        self.assertEqual(issues[0]["details"][0]["code"], "external_config")

    def test_owner_health_accepts_declared_terminal_convergence_state(self) -> None:
        command = {
            "name": "environment_mirror",
            "args": ["_bridge/codex_environment_mirror.py", "validate"],
            "compatibility_args": ["_bridge/codex_environment_mirror.py", "health"],
        }
        owner_result = {
            "schema": "codex_environment_mirror.health.v1",
            "ok": True,
            "status": "publication_pending",
            "convergence": {
                "next_action": "complete_workflow_finalization_then_publish_once",
                "issue_codes": ["source_assets_changed"],
            },
        }
        with patch.object(global_coherence_doctor, "run_json", return_value=owner_result):
            rows = global_coherence_doctor.owner_health_snapshot(
                {"contracts": {"backup": {"health_commands": [command]}}}
            )
        self.assertTrue(rows[0]["ok"])
        self.assertTrue(rows[0]["owner_ok"])
        self.assertEqual(rows[0]["owner_status"], "publication_pending")
        self.assertEqual(rows[0]["convergence"]["next_action"], "complete_workflow_finalization_then_publish_once")
        issues = global_coherence_doctor.check_owner_health(rows)
        self.assertEqual(issues[0]["code"], "owner_terminal_convergence_pending")
        self.assertEqual(issues[0]["severity"], "advisory")

    def test_owner_health_rejects_mixed_or_unknown_pending_failure(self) -> None:
        command = {
            "name": "environment_mirror",
            "args": ["_bridge/codex_environment_mirror.py", "validate"],
            "compatibility_args": ["_bridge/codex_environment_mirror.py", "health"],
        }
        owner_result = {
            "schema": "codex_environment_mirror.health.v1",
            "ok": False,
            "issues": [
                {"code": "source_assets_changed"},
                {"code": "unknown_runtime_drift"},
            ],
        }
        with patch.object(global_coherence_doctor, "run_json", return_value=owner_result):
            rows = global_coherence_doctor.owner_health_snapshot(
                {"contracts": {"backup": {"health_commands": [command]}}}
            )
        self.assertFalse(rows[0]["ok"])
        self.assertFalse(rows[0]["owner_ok"])
        self.assertNotIn("convergence", rows[0])

    def test_global_coherence_default_projection_is_bounded_and_actionable(self) -> None:
        issues = [
            {
                "severity": "risk" if index % 2 == 0 else "advisory",
                "code": f"issue_{index}",
                "message": "m" * 3000,
                "root_cause": "root" * 500,
                "next_action": f"repair_{index}",
                "details": [{"code": f"detail_{index}", "evidence": "x" * 4000}],
            }
            for index in range(60)
        ]
        payload = {
            "schema": "global_coherence_doctor.doctor.v1",
            "ok": False,
            "status": "risk",
            "summary": {"risk_count": 30, "advisory_count": 30},
            "issues": issues,
            "snapshot": {"surfaces": {"workflow": {"ok": False, "reason": "route failed", "next_action": "repair route"}}},
        }
        result = global_coherence_doctor.compact_cli_payload("doctor", payload, artifact_ref="C:/tmp/coherence.json")
        self.assertLessEqual(json_size_bytes(result), global_coherence_doctor.DEFAULT_INLINE_BYTES + 2048)
        self.assertEqual(result["issues"][0]["next_action"], "repair_0")
        self.assertTrue(result["output_budget"]["truncated"])
        self.assertIn("artifact:", result["raw_result_ref"])

    def test_global_coherence_closeout_probe_is_pure_and_structurally_complete(self) -> None:
        result = global_coherence_doctor.closeout_structure_probe()

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_kind"], "closeout-structure-probe")
        self.assertEqual(result["pending_disposition"]["pending_count"], 0)
        self.assertEqual(result["pending_disposition"]["items"], [])
        self.assertEqual(result["final_reply_must_show"]["total_review_cards"], 0)

    def test_mobile_external_config_issue_does_not_fail_bridge_owner_health(self) -> None:
        issue = {
            "code": "codex_config_guard_drift",
            "severity": "high",
            "summary": "External dependency failed.",
            "evidence": {},
            "safe_auto_fix": "",
            "manual_action": "",
            "owner_health_impact": False,
            "scope": "external_dependency",
        }
        with (
            patch.object(mobile_maintenance, "governance_storage_issues", return_value=[]),
            patch.object(mobile_maintenance, "bridge_runtime_route_issues", return_value=[]),
            patch.object(mobile_maintenance, "codex_tooling_issues", return_value=[issue]),
            patch.object(mobile_maintenance, "app_server_mcp_issues", return_value=[]),
            patch.object(mobile_maintenance, "resource_memory_hygiene_issues", return_value=[]),
            patch.object(mobile_maintenance, "queue_delivery_issues", return_value=[]),
        ):
            result = mobile_maintenance.diagnose_system({})
        self.assertTrue(result["ok"])
        self.assertEqual(result["blocking_issue_count"], 0)
        self.assertEqual(result["external_dependency_issue_count"], 1)

    def test_mobile_mcp_expectations_exclude_hub_managed_profiles(self) -> None:
        specs = mobile_maintenance.expected_codex_mcp_specs({})
        names = {str(item.get("name") or "") for item in specs}
        self.assertFalse(names & set(mobile_maintenance.HUB_MANAGED_MCP_NAMES))

    def test_mobile_doctor_receipt_keeps_actionable_evidence_without_full_snapshot(self) -> None:
        receipt = bridge_maintenance_cli._doctor_receipt(
            {
                "ok": False,
                "snapshot": {
                    "generated_at": "now",
                    "database": {"ok": True, "integrity_check": "ok", "journal_mode": "wal", "bytes": 10, "under_limit": True},
                    "counts": {"by_status": {"pending": 1}},
                    "pending": [{"id": "task-1"}],
                },
                "diagnosis": {
                    "ok": False,
                    "issue_count": 1,
                    "blocking_issue_count": 1,
                    "issues": [
                        {
                            "code": "pending_route_missing",
                            "severity": "high",
                            "summary": "A pending task has no route.",
                            "evidence": {"task_id": "task-1", "route_state": "thread_missing"},
                            "manual_action": "Repair the account thread route.",
                        }
                    ],
                },
            },
            full=False,
        )
        self.assertNotIn("snapshot", receipt)
        self.assertEqual(receipt["issues"][0]["evidence"]["task_id"], "task-1")
        self.assertEqual(receipt["commands"]["repair_plan"], "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py maintenance repair")

    def test_default_contract_preserves_decision_fields_without_caller_hints(self) -> None:
        result = bounded_payload(
            {
                "noise": [{"body": "x" * 5000} for _ in range(50)],
                "ok": False,
                "status": "blocked",
                "error": {"class": "policy", "reason": "approval_required"},
                "next_action": "request_approval",
                "run_ref": "run.json",
            },
            max_bytes=2048,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error"]["reason"], "approval_required")
        self.assertEqual(result["next_action"], "request_approval")
        self.assertEqual(result["run_ref"], "run.json")
        self.assertIn("aggregation supplements", result["output_budget"]["functional_summary_rule"])

    def test_preserved_fields_survive_large_payload(self) -> None:
        payload = {
            "schema": "test.v1",
            "ok": False,
            "status": "blocked",
            "error": {"class": "test", "reason": "required"},
            "records": [{"body": "x" * 4000} for _ in range(100)],
        }

        result = bounded_payload(
            payload,
            max_bytes=2048,
            preserve_keys=("schema", "ok", "status", "error"),
            artifact_ref="result.json",
        )

        self.assertEqual(result["schema"], "test.v1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error"]["reason"], "required")
        self.assertTrue(result["output_budget"]["truncated"])
        self.assertEqual(result["output_budget"]["artifact_ref"], "result.json")

    def test_micro_plan_keeps_route_fields_within_budget(self) -> None:
        plan = {
            "schema": "workflow.plan.v1",
            "ok": True,
            "generated_at": "now",
            "profile": {"profile": "maintenance"},
            "domains": [{"domain": "workflow_governance", "score": 10}],
            "structured_route": {"route_decision": {"primary_domain": "workflow_governance"}},
            "execution_route_pack": {"route_decision": {"primary_domain": "workflow_governance"}},
            "machine_phases": [{"id": f"phase-{index}", "enabled": True, "commands": ["x" * 5000]} for index in range(50)],
        }

        result = workflow_plan_detail.apply_detail_level(plan, "micro")

        self.assertEqual(result["detail_level"], "micro")
        self.assertIn("execution_route_pack", result)
        self.assertLessEqual(json_size_bytes(result), 6 * 1024)

    def test_micro_plan_keeps_exact_required_owner_commands(self) -> None:
        plan = {
            "schema": "workflow.plan.v1",
            "ok": True,
            "generated_at": "now",
            "profile": {"profile": "repair_or_code_change"},
            "domains": [],
            "structured_route": {},
            "execution_route_pack": {},
            "machine_phases": [
                {
                    "id": "phase_6_module_context",
                    "owner": "code_maintainability",
                    "enabled": True,
                    "commands": [
                        {
                            "cmd": "python _bridge\\code_maintainability.py module-context --limit 8",
                            "read_only": True,
                            "required": True,
                        },
                        {
                            "cmd": "python _bridge\\code_maintainability.py placement-plan --message task --limit 8",
                            "read_only": True,
                            "required": True,
                        },
                    ],
                }
            ],
        }

        result = workflow_plan_detail.apply_detail_level(plan, "micro")

        self.assertEqual(len(result["required_commands"]), 2)
        self.assertIn("module-context --limit 8", result["required_commands"][0]["cmd"])
        self.assertIn("placement-plan --message task", result["required_commands"][1]["cmd"])
        self.assertFalse(result["output_budget"]["truncated"])

    def test_micro_plan_preserves_active_rule_decisions(self) -> None:
        plan = {
            "schema": "workflow.plan.v1",
            "ok": True,
            "generated_at": "now",
            "profile": {"profile": "research"},
            "domains": [{"key": "records_resources", "drives_execution": True}],
            "structured_route": {},
            "execution_route_pack": {
                "schema": "execution_route_pack.v1",
                "ok": True,
                "route_decision": {
                    "task_mode": "research",
                    "task_facts": {"external_network_read": True},
                    "required_gates": [{"fact": "external_network_read", "required": True}],
                    "policy_decisions": [
                        {"rule_id": "external.online_access", "decision": "required", "enforcement_point": "execution_route_pack.required_gates", "trigger_fact": "external_network_read"}
                    ],
                    "stop_if": ["resource_or_network_owner_boundary_unclear"],
                },
            },
        }

        result = workflow_plan_detail.apply_detail_level(plan, "micro")
        decision = result["execution_route_pack"]["route_decision"]

        self.assertEqual(decision["policy_decisions"][0]["rule_id"], "external.online_access")
        self.assertEqual(decision["required_gates"][0]["fact"], "external_network_read")
        self.assertIn("resource_or_network_owner_boundary_unclear", decision["stop_if"])


class MaintenanceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capabilities = {"maintenance": {"operations": ["owner_command"]}}

    def test_capability_and_legacy_targets_are_both_valid(self) -> None:
        capability = workflow_action_synthesis.synthesize(
            {},
            message="scheduler metrics",
            owner="maintenance",
            operation="owner_command",
            arguments={"capability_id": "scheduler", "subcommand": "metrics"},
            owner_capabilities=self.capabilities,
        )
        legacy = workflow_action_synthesis.synthesize(
            {},
            message="scheduler metrics",
            owner="maintenance",
            operation="owner_command",
            arguments={"script": "scheduler.py", "subcommand": "metrics"},
            owner_capabilities=self.capabilities,
        )

        self.assertTrue(capability["complete"])
        self.assertTrue(legacy["complete"])

    def test_missing_maintenance_target_is_explicit(self) -> None:
        result = workflow_action_synthesis.synthesize(
            {},
            message="scheduler metrics",
            owner="maintenance",
            operation="owner_command",
            arguments={"subcommand": "metrics"},
            owner_capabilities=self.capabilities,
        )

        self.assertFalse(result["complete"])
        self.assertIn("missing_argument:capability_id_or_script", result["issues"])

    def test_maintenance_owner_consumes_registry_command_argv(self) -> None:
        with patch.object(
            workflow_owner_facade,
            "resolve_capability",
            return_value={
                "ok": True,
                "script": str(Path(workflow_owner_facade.__file__).resolve()),
                "command_argv": ["--json", "system", "status"],
            },
        ):
            command, issues = workflow_owner_facade._maintenance_command(
                {"capability_id": "office-status", "subcommand": "status", "cli_arg": []}
            )

        self.assertEqual(issues, [])
        self.assertEqual(command[-3:], ["--json", "system", "status"])

    def test_registry_limit_is_hard_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            maintenance_capability_registry,
            "INDEX_PATH",
            Path(tmp) / "maintenance_capabilities.sqlite",
        ):
            build = maintenance_capability_registry.build_index(apply=True)
            self.assertTrue(build["ok"])
            self.assertTrue(build["applied"])
            result = maintenance_capability_registry.query_registry(limit=10000)

        self.assertLessEqual(result["limit"], 100)
        self.assertLessEqual(result["returned"], 100)

    def test_registry_uses_source_authority_when_runtime_index_is_missing(self) -> None:
        maintenance_capability_registry._cached_source_rows.cache_clear()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            maintenance_capability_registry,
            "INDEX_PATH",
            Path(tmp) / "missing.sqlite",
        ):
            result = maintenance_capability_registry.query_registry(system="workflow", limit=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "maintenance_surface_shard_fallback")
        self.assertEqual(result["index_status"], "index_missing")
        self.assertEqual(result["loaded_shards"], ["workflow"])
        self.assertGreater(result["returned"], 0)
        self.assertTrue(all(item["system"] == "workflow" for item in result["items"]))

    def test_registry_fallback_loads_only_requested_shards_and_reuses_each_parse(self) -> None:
        maintenance_capability_registry._cached_source_rows.cache_clear()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            maintenance_capability_registry,
            "INDEX_PATH",
            Path(tmp) / "missing.sqlite",
        ), patch.object(
            maintenance_capability_registry,
            "parse_surface_map",
            wraps=maintenance_capability_registry.parse_surface_map,
        ) as parse:
            maintenance_capability_registry.query_registry(system="workflow", limit=5)
            maintenance_capability_registry.query_registry(system="workflow", limit=5)
            maintenance_capability_registry.query_registry(system="mcp", limit=5)

        self.assertEqual(parse.call_count, 2)
        maintenance_capability_registry._cached_source_rows.cache_clear()

    def test_registry_stale_index_loads_only_requested_source_shard(self) -> None:
        maintenance_capability_registry._cached_source_rows.cache_clear()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            maintenance_capability_registry,
            "INDEX_PATH",
            Path(tmp) / "maintenance_capabilities.sqlite",
        ):
            build = maintenance_capability_registry.build_index(apply=True)
            self.assertTrue(build["applied"])
            with patch.object(
                maintenance_capability_registry,
                "source_signature",
                return_value="forced-stale-signature",
            ), patch.object(
                maintenance_capability_registry,
                "parse_surface_map",
                wraps=maintenance_capability_registry.parse_surface_map,
            ) as parse:
                result = maintenance_capability_registry.query_registry(system="workflow", limit=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["index_status"], "index_stale")
        self.assertEqual(result["loaded_shards"], ["workflow"])
        self.assertEqual(parse.call_count, 1)
        self.assertTrue(all(item["system"] == "workflow" for item in result["items"]))
        maintenance_capability_registry._cached_source_rows.cache_clear()

    def test_registry_refuses_unscoped_full_parse_when_index_is_missing(self) -> None:
        maintenance_capability_registry._cached_source_rows.cache_clear()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            maintenance_capability_registry,
            "INDEX_PATH",
            Path(tmp) / "missing.sqlite",
        ), patch.object(
            maintenance_capability_registry,
            "parse_surface_map",
            wraps=maintenance_capability_registry.parse_surface_map,
        ) as parse:
            result = maintenance_capability_registry.query_registry(limit=5)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "scope_required_when_index_unavailable")
        self.assertEqual(parse.call_count, 0)

    def test_compact_map_and_shards_preserve_all_capabilities(self) -> None:
        metrics = maintenance_capability_registry.metrics()

        self.assertTrue(metrics["compact_map_within_budget"])
        self.assertEqual(metrics["capability_count"], sum(metrics["systems"].values()))
        self.assertGreaterEqual(metrics["capability_count"], 169)
        self.assertEqual(metrics["contract_count"], sum(metrics["authority_contract_counts"].values()))
        self.assertGreaterEqual(metrics["contract_count"], 125)
        self.assertEqual(metrics["contract_count"], metrics["declared_contract_count"])
        self.assertEqual(metrics["contract_projection_mismatches"], [])
        self.assertEqual(metrics["authority_contract_counts"], metrics["index_contract_counts"])
        self.assertEqual(metrics["authority_contract_counts"], metrics["map_contract_counts"])
        self.assertEqual(metrics["duplicate_contracts"], [])
        self.assertEqual(metrics["surface_shard_count"], metrics["declared_surface_count"])

    def test_contract_projection_drift_reports_the_specific_system(self) -> None:
        with patch.object(
            maintenance_capability_registry,
            "metrics",
            return_value={
                "capability_count": 1,
                "compact_map_within_budget": True,
                "surface_shard_count": 1,
                "declared_surface_count": 1,
                "contract_projection_mismatches": [
                    {"system": "workflow", "authority_count": 29, "index_count": 28, "map_count": 29}
                ],
                "duplicate_contracts": [],
                "index_exists": True,
                "index_fresh": True,
            },
        ):
            result = maintenance_capability_registry.doctor()

        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"], ["maintenance contract count projection drift: workflow"])

    def test_owner_system_outweighs_storage_implementation_terms(self) -> None:
        system = maintenance_capability_registry.infer_system(
            "_bridge/skill_lifecycle_state.py",
            "persistent SQLite lineage evidence",
        )

        self.assertEqual(system, "skills")

    def test_mcp_route_build_cli_uses_summary_not_full_routes(self) -> None:
        routes = [
            {
                "capability": f"capability_{index}",
                "profile": "test",
                "execution_affinity": "hub_first",
                "required_first_step": "hub_mcp_direct",
                "full_detail": "x" * 10000,
            }
            for index in range(30)
        ]
        result = mcp_capability_routes.cli_projection(
            {"schema": "mcp_capability_routes.v1", "ok": True, "route_count": len(routes), "routes": routes},
            "build",
        )

        self.assertLessEqual(json_size_bytes(result), 10 * 1024)
        self.assertNotIn("full_detail", json.dumps(result))
        self.assertTrue(result["output_budget"]["artifact_ref"].endswith("mcp_capability_routes.json"))

    def test_owner_mcp_adapter_removes_transport_duplicates(self) -> None:
        gateway_payload = {
            "ok": True,
            "route": {"route": "fresh_stdio", "reason": "stable"},
            "gateway_status": "gateway_tool_call_ok",
            "gateway_state_path": "state.json",
            "transport_isolated_from_current_turn": True,
            "result": {
                "result": {
                    "content": [{"type": "text", "text": "file body"}],
                    "structuredContent": {"content": "file body"},
                },
                "initialize": {"large": "x" * 10000},
                "stdout": "x" * 10000,
                "command": ["python", "server.py"],
                "tool_result_is_error": False,
                "error": None,
            },
        }

        result = local_mcp_hub_owner_mcp.call(
            {
                "profile": "filesystem-admin",
                "tool": "read_text_file",
                "arguments": {"path": "example.txt"},
                "hub_ack": local_mcp_hub_owner_mcp.HUB_READONLY_ACK,
            },
            lambda *_args, **_kwargs: gateway_payload,
        )

        serialized = json.dumps(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["structuredContent"]["content"], "file body")
        self.assertNotIn("initialize", serialized)
        self.assertNotIn("stdout", serialized)
        self.assertNotIn("command", serialized)

    def test_closeout_projection_keeps_review_cards_and_drops_large_sections(self) -> None:
        payload = {
            "schema": "codex_workflow_entry.closeout.v2",
            "ok": True,
            "record_path": "closeouts.jsonl",
            "status": {"outcome": "ok"},
            "tool_evidence": {"large": "x" * 50000},
            "pending_disposition": {"pending_count": 1, "items": [{"id": "review-1"}]},
            "final_reply_must_show": {"total_review_cards": 1, "cards": [{"title": "Review"}]},
            "finalization": {"ok": True, "startup_baseline": {"needed": False}, "project_checkpoint": {"needed": False}},
        }

        result = codex_workflow_entry.closeout_cli_projection(payload)

        self.assertEqual(result["pending_disposition"]["pending_count"], 1)
        self.assertEqual(result["final_reply_must_show"]["cards"][0]["title"], "Review")
        self.assertNotIn("tool_evidence", result)
        self.assertEqual(result["output_budget"]["artifact_ref"], "closeouts.jsonl")

    def test_closeout_full_projection_is_richer_but_bounded(self) -> None:
        payload = {
            "schema": "codex_workflow_entry.closeout.v2",
            "ok": False,
            "record_path": "closeouts.jsonl",
            "status": {"outcome": "ok", "main_task_complete": False},
            "used": {},
            "finalization": {
                "ok": False,
                "blocked_reason": "post_closeout_mirror_publish_failed",
                "startup_baseline": {
                    "needed": True,
                    "applied": False,
                    "check": {"ok": True, "diff": {"rows": ["x" * 1000] * 100}},
                },
                "project_checkpoint": {"needed": False, "applied": False},
                "post_closeout_mirror": {
                    "schema": "workflow_closeout.post_mirror_publish.v1",
                    "required": True,
                    "applied": True,
                    "ok": False,
                    "reason": "mirrored_source_changed",
                    "result": {
                        "schema": "codex_environment_mirror.publish.v1",
                        "ok": False,
                        "phase": "refresh",
                        "refresh": {
                            "ok": False,
                            "schema": "codex_environment_mirror.refresh.v1",
                            "reason": "mirror_operation_busy",
                            "next_action": "wait for reported PID, then run mirror status",
                            "attempts": [{"attempt": 1, "ok": False, "detail": "x" * 5000}],
                        },
                    },
                },
            },
            "final_reply_must_show": {"cards": [{"title": "review", "digest": "x" * 1000}] * 40},
        }

        default = codex_workflow_entry.closeout_cli_projection(payload)
        full = codex_workflow_entry.closeout_cli_projection(payload, full=True)

        self.assertEqual(
            default["finalization"]["post_closeout_mirror"]["result"]["refresh"]["reason"],
            "mirror_operation_busy",
        )
        self.assertNotIn("check", default["finalization"]["startup_baseline"])
        self.assertIn("check", full["finalization"]["startup_baseline"])
        self.assertIn("section_index", full)
        self.assertLess(full["output_budget"]["returned_bytes"], 32 * 1024)
        self.assertGreater(full["output_budget"]["max_inline_bytes"], default["output_budget"]["max_inline_bytes"])

    def test_closeout_projection_keeps_publish_remote_verification(self) -> None:
        payload = {
            "schema": "codex_workflow_entry.closeout.v2",
            "ok": True,
            "record_path": "closeouts.jsonl",
            "status": {"outcome": "ok"},
            "finalization": {
                "ok": True,
                "post_closeout_mirror": {
                    "schema": "workflow_closeout.post_mirror_publish.v1",
                    "required": True,
                    "applied": True,
                    "ok": True,
                    "result": {
                        "schema": "codex_environment_mirror.publish.v1",
                        "ok": True,
                        "snapshot_id": "snapshot-1",
                        "push": {
                            "ok": True,
                            "remote": "origin",
                            "branch": "main",
                            "head": "abc123",
                            "remote_verification": {"ok": True, "remote_head": "abc123"},
                        },
                    },
                },
            },
            "final_reply_must_show": {"cards": [{"title": "review", "digest": "x" * 1000}] * 60},
        }

        result = codex_workflow_entry.closeout_cli_projection(payload)
        post = result["finalization"]["post_closeout_mirror"]

        self.assertEqual(post["result"]["snapshot_id"], "snapshot-1")
        self.assertTrue(post["result"]["push"]["remote_verification"]["ok"])
        self.assertEqual(post["result"]["push"]["remote"], "origin")

    def test_closeout_projection_omits_idle_online_gate(self) -> None:
        payload = codex_workflow_entry.closeout(task_kind="validate", outcome="ok")

        result = codex_workflow_entry.closeout_cli_projection(payload)

        self.assertNotIn("decision_evidence", result)

    def test_closeout_projection_keeps_online_gate_blocker_details(self) -> None:
        payload = codex_workflow_entry.closeout(
            task_kind="validate",
            outcome="ok",
            web_search_used=True,
        )

        result = codex_workflow_entry.closeout_cli_projection(payload)
        gate = result["decision_evidence"]["external_research"]["online_access_gate"]

        self.assertFalse(gate["ok"])
        self.assertEqual(gate["blockers"][0]["code"], "direct_web_without_resource_exception")

    def test_closeout_projection_keeps_allowed_online_route_reason(self) -> None:
        payload = codex_workflow_entry.closeout(
            task_kind="validate",
            outcome="ok",
            web_search_used=True,
            resource_request_id="res_test",
            resource_status="failed",
            direct_web_fallback_reason="predefined_online_route_exhausted",
            owner_mcp_fallback_reason="native_owner_failed;hub_owner_failed;local_hub_not_applicable;owner_cli_not_applicable",
        )

        result = codex_workflow_entry.closeout_cli_projection(payload)
        gate = result["decision_evidence"]["external_research"]["online_access_gate"]

        self.assertTrue(gate["ok"])
        self.assertEqual(gate["matched_reason"], "predefined_online_route_exhausted")

    def test_closeout_projection_allows_explicit_platform_web_requirement(self) -> None:
        payload = codex_workflow_entry.closeout(
            task_kind="validate",
            outcome="ok",
            web_search_used=True,
            platform_web_required=True,
            resource_request_id="batch_test",
            resource_status="completed",
        )

        result = codex_workflow_entry.closeout_cli_projection(payload)
        gate = result["decision_evidence"]["external_research"]["online_access_gate"]

        self.assertTrue(gate["ok"])
        self.assertTrue(gate["platform_web_required"])
        self.assertEqual(gate["resource_status"], "completed")
        self.assertEqual(gate["matched_reason"], "higher_precedence_platform_web_required")

    def test_closeout_projection_rejects_platform_reason_without_flag(self) -> None:
        payload = codex_workflow_entry.closeout(
            task_kind="validate",
            outcome="ok",
            web_search_used=True,
            direct_web_fallback_reason="higher_precedence_platform_web_required",
        )

        result = codex_workflow_entry.closeout_cli_projection(payload)
        gate = result["decision_evidence"]["external_research"]["online_access_gate"]

        self.assertFalse(gate["ok"])
        self.assertFalse(gate["platform_web_required"])
        self.assertEqual(gate["blockers"][0]["code"], "direct_web_without_resource_exception")

    def test_compact_closeout_preserves_audit_record_path(self) -> None:
        compact = codex_workflow_entry.compact_closeout(
            {
                "ok": True,
                "generated_at": "now",
                "record_path": "closeouts.jsonl",
                "status": {"outcome": "ok"},
                "used": {},
                "tool_evidence": {},
                "validation": {},
                "finalization": {},
            }
        )

        self.assertEqual(compact["record_path"], "closeouts.jsonl")

    def test_failed_owner_receipt_inlines_concrete_diagnostics(self) -> None:
        receipt = workflow_owner_facade._receipt(
            {"workflow_run_id": "run-test", "owner": "maintenance", "operation": "owner_command"},
            status="failed",
            ok=False,
            raw_result={
                "schema": "owner.doctor.v1",
                "ok": False,
                "status": "risk",
                "issues": [
                    {
                        "severity": "risk",
                        "code": "orphan_process",
                        "message": "One orphan process remains.",
                        "group": "filesystem-admin",
                        "root_pids": [1234],
                        "manual_action": "Run the owner repair plan.",
                    }
                ],
            },
            error_class="owner_command_failed",
            error_reason="owner_returned_not_ok",
            next_action="inspect_owner_result",
        )

        self.assertEqual(receipt["error"]["reason"], "One orphan process remains.")
        self.assertEqual(receipt["diagnostics"]["items"][0]["code"], "orphan_process")
        self.assertEqual(receipt["diagnostics"]["items"][0]["root_pids"], [1234])
        self.assertEqual(receipt["diagnostics"]["next_action"], "Run the owner repair plan.")

    def test_failure_diagnostics_prioritize_risk_and_flatten_nested_items(self) -> None:
        receipt = workflow_owner_facade._receipt(
            {"workflow_run_id": "run-test", "owner": "maintenance", "operation": "owner_command"},
            status="failed",
            ok=False,
            raw_result={
                "issues": [
                    {"severity": "advisory", "code": "unproven", "message": "Not yet probed."},
                    {
                        "severity": "risk",
                        "code": "owner_failed",
                        "message": "Owner failed.",
                        "details": [
                            {
                                "severity": "risk",
                                "code": "orphan_process",
                                "message": "Orphan remains.",
                                "root_pids": [4321],
                                "safe_next_step": "Run repair-plan.",
                            }
                        ],
                    },
                ]
            },
            error_class="owner_command_failed",
            error_reason="owner_returned_not_ok",
            next_action="inspect_owner_result",
        )

        self.assertEqual(receipt["error"]["reason"], "Owner failed.")
        self.assertEqual(receipt["diagnostics"]["items"][1]["root_pids"], [4321])
        self.assertEqual(receipt["diagnostics"]["next_action"], "Run repair-plan.")


class SchedulerGovernanceTests(unittest.TestCase):
    def test_approval_polling_change_does_not_slow_persistent_task_recovery(self) -> None:
        task = next(
            item
            for item in codex_scheduler_runner.DEFAULT_TASKS
            if item["id"] == "persistent_task_kernel_recover_expired"
        )

        self.assertEqual({"type": "interval", "every_seconds": 300}, task["trigger"])
        self.assertEqual(300, task["policy"]["retry_interval_seconds"])

    def test_approved_review_consumer_reuses_unified_scheduler_and_never_infers_approval(self) -> None:
        task = next(
            item
            for item in codex_scheduler_runner.DEFAULT_TASKS
            if item["id"] == "workflow_review_approved_consumer"
        )

        self.assertTrue(task["enabled"])
        self.assertEqual({"type": "interval", "every_seconds": 900}, task["trigger"])
        self.assertEqual("_bridge/workflow_iteration_owner.py", task["action"]["command"][1])
        self.assertIn("consume-approved", task["action"]["command"])
        self.assertIn("already approved", task["policy"]["allowed_effect"])
        self.assertIn("never infer approval", task["policy"]["allowed_effect"])

    def test_maintenance_convergence_uses_one_shadow_dispatcher(self) -> None:
        tasks = [
            item
            for item in codex_scheduler_runner.DEFAULT_TASKS
            if "maintenance_convergence_runtime.py" in " ".join(item.get("action", {}).get("command", []))
        ]

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "maintenance_convergence_shadow_scan")
        self.assertEqual(tasks[0]["policy"]["mode"], "maintenance-convergence-shadow")
        self.assertNotIn("--apply", tasks[0]["action"]["command"])
        self.assertIn("never execute owner repair commands", tasks[0]["policy"]["allowed_effect"])

    def test_scheduler_has_no_static_convergence_plan_bypass(self) -> None:
        bypasses = [
            item["id"]
            for item in codex_scheduler_runner.DEFAULT_TASKS
            if "maintenance_upgrade_governance.py convergence-plan"
            in " ".join(item.get("action", {}).get("command", []))
        ]

        self.assertEqual(bypasses, [])

    def test_workflow_observation_task_only_upserts_review_proposals(self) -> None:
        task = next(
            item
            for item in codex_scheduler_runner.DEFAULT_TASKS
            if item["id"] == "workflow_observation_iteration_daily"
        )
        self.assertTrue(task["enabled"])
        self.assertEqual({"type": "daily", "at": "04:10"}, task["trigger"])
        self.assertEqual("derived-observation-proposal-only", task["policy"]["mode"])
        self.assertEqual("_bridge/workflow_observation_iteration.py", task["action"]["command"][1])
        self.assertIn("APPLY-OBSERVATION-PROPOSALS", task["action"]["command"])
        self.assertIn("never write work notes", task["policy"]["allowed_effect"])

    def test_codex_update_intelligence_task_is_daily_read_only_and_never_applies(self) -> None:
        task = next(
            item
            for item in codex_scheduler_runner.DEFAULT_TASKS
            if item["id"] == "codex_update_intelligence_periodic_scan"
        )

        self.assertTrue(task["enabled"])
        self.assertEqual({"type": "daily", "at": "03:40"}, task["trigger"])
        self.assertEqual("read-only-intelligence-and-approval-proposal", task["policy"]["mode"])
        command = task["action"]["command"]
        self.assertEqual("_bridge/dependency_change_intelligence.py", command[1])
        self.assertIn("periodic", command)
        self.assertNotIn("--apply", command)
        self.assertIn("never apply repairs", task["policy"]["allowed_effect"])

    def test_powershell_task_uses_shared_absolute_file_command(self) -> None:
        task = {
            "action": {"type": "powershell", "command": [r"C:\Codex\restart.ps1", "-Mode", "dry-run"]}
        }
        with patch.object(
            codex_scheduler_runner,
            "powershell_file_command",
            return_value=["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-File", r"C:\Codex\restart.ps1"],
        ) as build:
            command = codex_scheduler_runner.command_for_task(task)

        self.assertTrue(command[0].startswith("/mnt/c/Windows/"))
        build.assert_called_once_with(r"C:\Codex\restart.ps1", "-Mode", "dry-run", execution_policy_bypass=True)

    def test_legacy_heartbeat_drops_stdout_and_message_content(self) -> None:
        result = codex_scheduler_runner.compact_heartbeat(
            {
                "ok": True,
                "last_run_results": [
                    {
                        "task_id": "email",
                        "ok": True,
                        "stdout_preview": "private mail body",
                        "stderr_preview": "private error body",
                        "record_path": "record.json",
                    }
                ],
            }
        )

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("private mail body", serialized)
        self.assertNotIn("private error body", serialized)
        self.assertEqual(result["last_run_summary"][0]["record_path"], "record.json")

    def test_override_migration_preserves_runtime_and_eliminates_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_path = root / "maintenance_tasks.json"
            overrides_path = root / "maintenance_task_overrides.json"
            runtime_tasks = copy.deepcopy(codex_scheduler_runner.DEFAULT_TASKS)
            runtime_tasks[0]["name"] = "custom name"
            runtime_tasks.append(
                {
                    "id": "runtime-only",
                    "name": "runtime only",
                    "enabled": False,
                    "trigger": {"type": "interval", "every_seconds": 3600},
                    "action": {"type": "command", "command": ["python", "noop.py"]},
                    "policy": {"mode": "dry-run"},
                }
            )
            tasks_path.write_text(json.dumps({"tasks": runtime_tasks}, ensure_ascii=False), encoding="utf-8")

            with (
                patch.object(codex_scheduler_runner, "TASKS_PATH", tasks_path),
                patch.object(codex_scheduler_runner, "TASK_OVERRIDES_PATH", overrides_path),
                patch.object(codex_scheduler_runner, "create_routed_backup", return_value={"ok": True, "manifest_paths": []}),
            ):
                result = codex_scheduler_runner.migrate_task_overrides(
                    apply=True,
                    confirm="MIGRATE-SCHEDULER-OVERRIDES",
                )

                self.assertTrue(result["ok"])
                self.assertTrue(result["drift"]["ok"])
                self.assertEqual(result["override_count"], 2)
                self.assertEqual(codex_scheduler_runner.load_tasks()[0]["name"], "custom name")
                self.assertIn("runtime-only", {task["id"] for task in codex_scheduler_runner.load_tasks()})


class SchedulerWslStateMigrationTests(unittest.TestCase):
    def test_explicit_legacy_overrides_outrank_generated_runtime_action_diff(self) -> None:
        explicit = {"schema": "codex_scheduler.task_overrides.v1", "tasks": [{"id": "bridge_appserver_idle_restart_dry_run", "patch": {"trigger": {"type": "interval", "every_seconds": 3600}}}]}
        raw = json.dumps(explicit, ensure_ascii=False).encode("utf-8")
        runtime = copy.deepcopy(codex_scheduler_runner.DEFAULT_TASKS)
        bridge = next(row for row in runtime if row["id"] == "bridge_appserver_idle_restart_dry_run")
        bridge["action"]["command"][0] = r"C:\\old-workspace\\restart.ps1"
        plan = codex_scheduler_runner._legacy_override_plan(
            {"maintenance_task_overrides.json": (raw, hashlib.sha256(raw).hexdigest())},
            {"tasks": runtime},
        )
        self.assertTrue(plan["ok"])
        self.assertEqual("explicit_legacy_overrides", plan["source"])
        self.assertNotIn("action", plan["tasks"][0]["patch"])

    def test_legacy_state_plan_is_read_only_and_never_uses_windows_mount_as_target(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as raw:
            root = Path(raw)
            with (
                patch.object(codex_scheduler_runner, "RUNTIME_ROOT", root),
                patch.object(codex_scheduler_runner, "STATE_PATH", root / "scheduler-state.json"),
                patch.object(codex_scheduler_runner, "TASKS_PATH", root / "maintenance_tasks.json"),
                patch.object(codex_scheduler_runner, "TASK_OVERRIDES_PATH", root / "maintenance_task_overrides.json"),
                patch.object(codex_scheduler_runner, "HEARTBEAT_PATH", root / "scheduler-heartbeat.json"),
                patch.object(codex_scheduler_runner, "LEGACY_IMPORT_RECEIPT_PATH", root / "legacy-state-import.json"),
            ):
                plan = codex_scheduler_runner.legacy_state_plan()
        self.assertTrue(plan["ok"])
        self.assertEqual("windows_powershell_only", plan["source_access"])
        self.assertNotIn("/mnt/", plan["target_root"])

    def test_legacy_import_requires_explicit_confirmation(self) -> None:
        with patch.object(codex_scheduler_runner, "_read_legacy_state_via_windows") as export:
            result = codex_scheduler_runner.import_legacy_state(apply=True, confirm="no")
        self.assertFalse(result["ok"])
        self.assertEqual("confirmation_required", result["reason"])
        export.assert_not_called()

    def test_legacy_import_derives_overrides_without_copying_windows_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = {"schema_version": 1, "tasks": {"fixture": {"last_status": "retry_exhausted", "retry_count": 3}}}
            tasks = copy.deepcopy(codex_scheduler_runner.DEFAULT_TASKS)
            tasks[0]["name"] = "preserved legacy task name"
            encoded_state = json.dumps(state, ensure_ascii=False).encode("utf-8")
            encoded_tasks = json.dumps({"tasks": tasks}, ensure_ascii=False).encode("utf-8")
            export = {
                "ok": True,
                "source_root": r"C:\\Users\\45543\\Desktop\\Codex资源库\\文档\\定时模块\\运行态\\统一调度",
                "files": {
                    "scheduler-state.json": (encoded_state, hashlib.sha256(encoded_state).hexdigest()),
                    "maintenance_tasks.json": (encoded_tasks, hashlib.sha256(encoded_tasks).hexdigest()),
                },
            }
            with (
                patch.object(codex_scheduler_runner, "RUNTIME_ROOT", root),
                patch.object(codex_scheduler_runner, "RECORD_ROOT", root / "records"),
                patch.object(codex_scheduler_runner, "GOVERNANCE_ROOT", root / "governance"),
                patch.object(codex_scheduler_runner, "LOG_DIR", root / "logs"),
                patch.object(codex_scheduler_runner, "STATE_PATH", root / "scheduler-state.json"),
                patch.object(codex_scheduler_runner, "TASKS_PATH", root / "maintenance_tasks.json"),
                patch.object(codex_scheduler_runner, "TASK_OVERRIDES_PATH", root / "maintenance_task_overrides.json"),
                patch.object(codex_scheduler_runner, "HEARTBEAT_PATH", root / "scheduler-heartbeat.json"),
                patch.object(codex_scheduler_runner, "LEGACY_IMPORT_RECEIPT_PATH", root / "legacy-state-import.json"),
                patch.object(codex_scheduler_runner, "_read_legacy_state_via_windows", return_value=export),
            ):
                result = codex_scheduler_runner.import_legacy_state(apply=True, confirm="IMPORT-LEGACY-SCHEDULER-STATE")
                imported_state = codex_scheduler_runner.read_json(root / "scheduler-state.json", {})
                overrides = codex_scheduler_runner.read_json(root / "maintenance_task_overrides.json", {})
                receipt_exists = (root / "legacy-state-import.json").is_file()
        self.assertTrue(result["ok"], result)
        self.assertEqual("retry_exhausted", imported_state["tasks"]["fixture"]["last_status"])
        self.assertEqual("preserved legacy task name", overrides["tasks"][0]["patch"]["name"])
        self.assertTrue(receipt_exists)


class UnifiedMaintenanceExecutionTests(unittest.TestCase):
    def test_read_only_aggregate_runs_in_parallel_and_preserves_registry_order(self) -> None:
        registry = {
            "first": {"name": "first", "auto_policy": "read_only", "commands": {"validate": ["first"]}},
            "second": {"name": "second", "auto_policy": "read_only", "commands": {"validate": ["second"]}},
        }
        with patch.object(system_maintenance_cli, "REGISTRY", registry), patch.object(
            system_maintenance_cli,
            "run_json",
            side_effect=lambda command: {"ok": True, "command": command},
        ):
            result = system_maintenance_cli.run_action("validate", [], True)
        self.assertEqual(result["execution"]["mode"], "parallel_read_only")
        self.assertEqual(list(result["systems"]), ["first", "second"])

    def test_apply_aggregate_remains_serial(self) -> None:
        registry = {
            "first": {"name": "first", "auto_policy": "controlled", "commands": {"apply": ["first"]}},
            "second": {"name": "second", "auto_policy": "controlled", "commands": {"apply": ["second"]}},
        }
        with patch.object(system_maintenance_cli, "REGISTRY", registry), patch.object(
            system_maintenance_cli,
            "run_json",
            side_effect=lambda command: {"ok": True, "command": command},
        ):
            result = system_maintenance_cli.run_action("apply", [], True)
        self.assertEqual(result["execution"]["mode"], "serial")


class MirrorWorkflowFacadeTests(unittest.TestCase):
    def test_health_reaches_mirror_owner(self) -> None:
        with patch.object(
            codex_workflow_entry,
            "execute_mirror_command",
            return_value={"schema": "codex_environment_mirror.health.v1", "ok": True},
        ) as execute, patch.object(codex_workflow_entry, "print_json"):
            exit_code = codex_workflow_entry.main(["mirror", "health"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(execute.call_args.args, ("health",))

    def test_status_force_fresh_reaches_mirror_owner(self) -> None:
        with patch.object(
            codex_workflow_entry,
            "execute_mirror_command",
            return_value={"schema": "codex_environment_mirror.status.v1", "ok": True},
        ) as execute, patch.object(codex_workflow_entry, "print_json"):
            exit_code = codex_workflow_entry.main(["mirror", "status", "--force-fresh"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(execute.call_args.args, ("status",))
        self.assertTrue(execute.call_args.kwargs["force_fresh"])

    def test_non_status_mirror_action_rejects_force_fresh(self) -> None:
        with self.assertRaises(SystemExit):
            codex_workflow_entry.main(["mirror", "plan", "--force-fresh"])

    def test_drift_plan_reaches_mirror_owner(self) -> None:
        with patch.object(
            codex_workflow_entry,
            "execute_mirror_command",
            return_value={"schema": "codex_environment_mirror.drift_plan.v1", "ok": True},
        ) as execute, patch.object(codex_workflow_entry, "print_json"):
            exit_code = codex_workflow_entry.main(["mirror", "drift-plan"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(execute.call_args.args, ("drift-plan",))


if __name__ == "__main__":
    unittest.main()
