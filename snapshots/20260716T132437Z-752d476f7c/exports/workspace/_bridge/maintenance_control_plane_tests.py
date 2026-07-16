#!/usr/bin/env python3
from __future__ import annotations

import copy
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
from shared import codex_scheduler_runner
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

    def test_success_failure_evidence_policy_is_machine_readable(self) -> None:
        policy = output_evidence_policy()
        self.assertEqual(policy["success"], "bounded_traceable_summary")
        self.assertEqual(policy["failure"], "decision_complete_inline_evidence")
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
        self.assertTrue(success["output_budget"]["truncated"])
        self.assertEqual(success["raw_result_ref"], "command:test --full")
        self.assertEqual(failure["issues"], failure_payload["issues"])

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

    def test_registry_limit_is_hard_capped(self) -> None:
        result = maintenance_capability_registry.query_registry(limit=10000)

        self.assertLessEqual(result["limit"], 100)
        self.assertLessEqual(result["returned"], 100)

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


if __name__ == "__main__":
    unittest.main()
