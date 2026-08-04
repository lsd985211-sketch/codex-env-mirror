#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import subprocess

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
import work_git_change_owner
import work_git_change_owner_process
from bounded_output import aggregate_validator_cli_payload, bounded_payload, governed_cli_payload, json_size_bytes, output_evidence_policy
from shared import codex_scheduler_runner, system_maintenance_cli
from mobile_openclaw_bridge import mobile_maintenance
from mobile_openclaw_bridge import bridge_maintenance_cli


class BoundedOutputTests(unittest.TestCase):
    def test_unsaved_closeout_is_read_only_but_preserves_review_projection(self) -> None:
        preview_groups = [{
            "kind": "tool_evidence",
            "count": 1,
            "review_items": [{
                "source_item_id": "tool-negative:preview-only",
                "review_queue_id": "tool_evidence:source_item_id:tool-negative:preview-only",
                "review_queue_revision": 0,
                "title": "只读预览中的工具证据",
                "summary": "该事项只能显示，不能在未保存收口中写入队列。",
            }],
        }]
        with (
            patch.object(codex_workflow_entry, "preview_review_groups", return_value=preview_groups) as preview,
            patch.object(codex_workflow_entry, "sync_review_groups", side_effect=AssertionError("unsaved closeout wrote review queue")) as sync,
            patch.object(codex_workflow_entry, "reconcile_prepared_deliveries", side_effect=AssertionError("unsaved closeout reconciled delivery")) as reconcile,
            patch.object(codex_workflow_entry, "prepare_delivery_packages", side_effect=AssertionError("unsaved closeout wrote delivery package")) as packages,
            patch.object(codex_workflow_entry, "prepare_delivery_envelope", side_effect=AssertionError("unsaved closeout created authorization challenge")) as envelope,
        ):
            payload = codex_workflow_entry.closeout(
                task_kind="validate", outcome="ok",
                negative_observation=["preview-only=read-only evidence"],
                delivery_thread_id="thread-test", save=False,
            )

        preview.assert_called_once()
        sync.assert_not_called()
        reconcile.assert_not_called()
        packages.assert_not_called()
        envelope.assert_not_called()
        self.assertEqual(payload["review_delivery"]["mode"], "read_only_preview")
        self.assertEqual(payload["pending_disposition"]["pending_count"], 1)
        self.assertFalse(payload["final_reply_must_show"]["delivery_required"])

    def test_saved_closeout_retains_governed_review_persistence(self) -> None:
        with (
            patch.object(codex_workflow_entry, "resolve_rollout_path", return_value={"ok": False}),
            patch.object(codex_workflow_entry, "sync_review_groups", return_value=[]) as sync,
            patch.object(codex_workflow_entry, "prepare_delivery_packages", return_value={"ok": True, "pending_count": 0, "package_count": 0, "packages": []}) as packages,
            patch.object(codex_workflow_entry, "save_closeout"),
            patch.object(codex_workflow_entry, "should_compact_closeout", return_value=False),
            patch("skill_orchestrator.record_usage", return_value={"ok": True}),
        ):
            payload = codex_workflow_entry.closeout(task_kind="validate", outcome="ok", save=True)

        sync.assert_called_once()
        packages.assert_called_once()
        self.assertEqual(payload["review_delivery"]["mode"], "governed_persistent")

    def test_closeout_reconciles_prior_review_delivery_before_preparing_current_envelope(self) -> None:
        calls: list[str] = []
        package = {
            "package_id": "review-package:test",
            "package_signature": "package-signature",
            "topic": "test",
            "count": 1,
            "item_refs": [{"review_id": "review:test", "revision": 1}],
        }

        def reconcile(**_kwargs):
            calls.append("reconcile")
            return {
                "schema": "workflow_review_queue.delivery_reconciliation.v1",
                "ok": False,
                "prepared_count": 1,
                "delivered_count": 0,
                "results": [{"ok": False, "reason": "final_response_missing_review_card_evidence"}],
            }

        def prepare(_package_id, **_kwargs):
            calls.append("prepare")
            return {
                "ok": True,
                "envelope_id": "review-envelope:test",
                "envelope_digest": "envelope-digest",
                "package_id": "review-package:test",
                "package_signature": "package-signature",
                "item_refs": package["item_refs"],
                "markdown": "### Review Card 1: Review\n- ID: review:test\n- Summary: exact",
                "status": "prepared",
            }

        with (
            patch.dict("os.environ", {"CODEX_THREAD_ID": "thread-test"}),
            patch.object(codex_workflow_entry, "resolve_rollout_path", return_value={"ok": True, "rollout_path": "/tmp/rollout.jsonl"}),
            patch.object(codex_workflow_entry, "reconcile_prepared_deliveries", side_effect=reconcile),
            patch.object(codex_workflow_entry, "prepare_delivery_packages", return_value={"ok": True, "pending_count": 1, "package_count": 1, "packages": [package]}),
            patch.object(codex_workflow_entry, "prepare_delivery_envelope", side_effect=prepare),
            patch.object(codex_workflow_entry, "save_closeout"),
            patch("skill_orchestrator.record_usage", return_value={"ok": True}),
        ):
            payload = codex_workflow_entry.closeout(
                task_kind="validate",
                outcome="ok",
                negative_observation=["delivery_reconciliation=focused regression"],
                save=True,
            )

        self.assertEqual(calls, ["reconcile", "prepare"])
        self.assertFalse(payload["review_delivery"]["prior_reconciliation"]["ok"])
        self.assertTrue(payload["final_reply_must_show"]["delivery_required"])
        self.assertFalse(payload["final_reply_must_show"]["delivery_complete"])

    def test_closeout_reuses_response_backed_delivered_envelope(self) -> None:
        package = {
            "package_id": "review-package:test",
            "package_signature": "package-signature",
            "topic": "test",
            "count": 1,
            "item_refs": [{"review_id": "review:test", "revision": 1}],
        }
        envelope = {
            "ok": True,
            "envelope_id": "review-envelope:test",
            "envelope_digest": "envelope-digest",
            "package_id": "review-package:test",
            "package_signature": "package-signature",
            "item_refs": package["item_refs"],
            "markdown": "### Review Card 1: Review\n- ID: review:test\n- Summary: exact",
            "status": "delivered",
        }
        with (
            patch.dict("os.environ", {"CODEX_THREAD_ID": "thread-test"}),
            patch.object(codex_workflow_entry, "resolve_rollout_path", return_value={"ok": True, "rollout_path": "/tmp/rollout.jsonl"}),
            patch.object(codex_workflow_entry, "reconcile_prepared_deliveries", return_value={"ok": True, "prepared_count": 1, "delivered_count": 1, "results": []}),
            patch.object(codex_workflow_entry, "prepare_delivery_packages", return_value={"ok": True, "pending_count": 1, "package_count": 1, "packages": [package]}),
            patch.object(codex_workflow_entry, "prepare_delivery_envelope", return_value=envelope),
            patch.object(codex_workflow_entry, "save_closeout"),
            patch("skill_orchestrator.record_usage", return_value={"ok": True}),
        ):
            payload = codex_workflow_entry.closeout(
                task_kind="validate",
                outcome="ok",
                negative_observation=["delivery_reconciliation=focused regression"],
                save=True,
            )

        self.assertFalse(payload["final_reply_must_show"]["delivery_required"])
        self.assertTrue(payload["final_reply_must_show"]["delivery_complete"])
        self.assertIn("review:test", payload["final_reply_must_show"]["markdown"])
        self.assertIn("reuse", payload["final_reply_must_show"]["delivery_next_action"])

    def test_closeout_skips_incomplete_package_and_delivers_next_ready_package(self) -> None:
        blocked = {
            "package_id": "review-package:blocked",
            "package_signature": "blocked-signature",
            "topic": "iteration",
            "count": 1,
            "item_refs": [{"review_id": "iteration:blocked", "revision": 1}],
        }
        ready = {
            "package_id": "review-package:ready",
            "package_signature": "ready-signature",
            "topic": "proposal",
            "count": 1,
            "item_refs": [{"review_id": "proposal:ready", "revision": 1}],
        }
        calls: list[str] = []

        def prepare(package_id, **_kwargs):
            calls.append(package_id)
            if package_id == blocked["package_id"]:
                return {
                    "ok": False,
                    "reason": "approval_presentation_incomplete",
                    "package_id": package_id,
                    "required_next_action": "producer supplies Chinese-primary semantics",
                }
            return {
                "ok": True,
                "envelope_id": "review-envelope:ready",
                "envelope_digest": "ready-digest",
                "package_id": package_id,
                "package_signature": ready["package_signature"],
                "item_refs": ready["item_refs"],
                "markdown": "### 审批卡 1：可交付事项",
                "status": "prepared",
            }

        with (
            patch.object(codex_workflow_entry, "resolve_rollout_path", return_value={"ok": False}),
            patch.object(codex_workflow_entry, "reconcile_prepared_deliveries", return_value={"ok": True, "prepared_count": 0, "delivered_count": 0, "results": []}),
            patch.object(codex_workflow_entry, "prepare_delivery_packages", return_value={"ok": True, "pending_count": 2, "package_count": 2, "packages": [blocked, ready]}),
            patch.object(codex_workflow_entry, "prepare_delivery_envelope", side_effect=prepare),
            patch.object(codex_workflow_entry, "save_closeout"),
            patch("skill_orchestrator.record_usage", return_value={"ok": True}),
        ):
            payload = codex_workflow_entry.closeout(
                task_kind="validate",
                outcome="ok",
                delivery_thread_id="thread-test",
                save=True,
            )

        self.assertEqual(calls, [blocked["package_id"], ready["package_id"]])
        self.assertEqual(payload["review_delivery"]["current_envelope"]["package_id"], ready["package_id"])
        self.assertEqual(payload["review_delivery"]["skipped_packages"][0]["package_id"], blocked["package_id"])
        self.assertEqual(payload["review_delivery"]["skipped_packages"][0]["reason"], "approval_presentation_incomplete")

    def test_closeout_does_not_skip_authorization_or_owner_failure(self) -> None:
        first = {
            "package_id": "review-package:first",
            "package_signature": "first-signature",
            "topic": "proposal",
            "count": 1,
            "item_refs": [{"review_id": "proposal:first", "revision": 1}],
        }
        second = dict(first, package_id="review-package:second")
        calls: list[str] = []

        def prepare(package_id, **_kwargs):
            calls.append(package_id)
            return {"ok": False, "reason": "preissued_approval_plan_failed", "package_id": package_id}

        with (
            patch.object(codex_workflow_entry, "resolve_rollout_path", return_value={"ok": False}),
            patch.object(codex_workflow_entry, "reconcile_prepared_deliveries", return_value={"ok": True, "prepared_count": 0, "delivered_count": 0, "results": []}),
            patch.object(codex_workflow_entry, "prepare_delivery_packages", return_value={"ok": True, "pending_count": 2, "package_count": 2, "packages": [first, second]}),
            patch.object(codex_workflow_entry, "prepare_delivery_envelope", side_effect=prepare),
            patch.object(codex_workflow_entry, "save_closeout"),
            patch("skill_orchestrator.record_usage", return_value={"ok": True}),
        ):
            payload = codex_workflow_entry.closeout(
                task_kind="validate", outcome="ok", delivery_thread_id="thread-test", save=True
            )

        self.assertEqual(calls, [first["package_id"]])
        self.assertEqual(payload["review_delivery"]["current_envelope"]["reason"], "preissued_approval_plan_failed")
        self.assertEqual(payload["review_delivery"]["skipped_packages"], [])

    def test_failed_review_delivery_envelope_prevents_success_compaction(self) -> None:
        package = {
            "status": {"outcome": "ok"},
            "tool_evidence": {},
            "work_notes": {"active_count": 0},
            "proposals": [],
            "user_profile_candidates": {"candidate_count": 0},
            "external_knowledge_candidates": {"selected_count": 0},
            "self_update_governance": {"signals": []},
            "finalization": {"signals": {}},
            "pending_disposition": {"pending_count": 0, "items": []},
            "review_delivery": {
                "current_envelope": {
                    "ok": False,
                    "reason": "preissued_approval_plan_failed",
                }
            },
        }

        self.assertFalse(codex_workflow_entry.should_compact_closeout(package))

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
    def test_retry_exhausted_interval_tasks_use_bounded_cooldown(self) -> None:
        now = codex_scheduler_runner.now_bj()
        task = {
            "id": "fixture_interval_task",
            "enabled": True,
            "trigger": {"type": "interval", "every_seconds": 300},
            "action": {"type": "command", "command": ["python", "--version"]},
            "policy": {
                "mode": "fixture",
                "retry_interval_seconds": 60,
                "max_retry_count": 2,
                "latest_lag_seconds": 3600,
                "retry_exhausted_action": "record_and_continue",
            },
        }
        recent = {
            "last_status": "retry_exhausted",
            "retry_count": 3,
            "last_attempt_at": codex_scheduler_runner.iso(
                now - timedelta(seconds=codex_scheduler_runner.RETRY_EXHAUSTED_COOLDOWN_SECONDS - 1)
            ),
        }
        elapsed = {
            "last_status": "retry_exhausted",
            "retry_count": 3,
            "last_attempt_at": codex_scheduler_runner.iso(
                now - timedelta(seconds=codex_scheduler_runner.RETRY_EXHAUSTED_COOLDOWN_SECONDS + 1)
            ),
        }

        self.assertIsNone(codex_scheduler_runner.get_due_reason(task, recent, now))
        self.assertEqual("retry_exhausted_recovery", codex_scheduler_runner.get_due_reason(task, elapsed, now))

    def test_retry_exhausted_recovery_failure_restarts_cooldown_without_resetting_count(self) -> None:
        now = codex_scheduler_runner.now_bj()
        task = {
            "id": "fixture_interval_task",
            "trigger": {"type": "interval", "every_seconds": 300},
            "policy": {
                "retry_interval_seconds": 60,
                "max_retry_count": 2,
                "latest_lag_seconds": 3600,
                "retry_exhausted_action": "record_and_continue",
            },
        }
        state = {"tasks": {"fixture_interval_task": {"retry_count": 3}}}
        run = codex_scheduler_runner.TaskRun(
            task_id="fixture_interval_task",
            ok=False,
            mode="fixture",
            due_reason="retry_exhausted_recovery",
            started_at=codex_scheduler_runner.iso(now),
            finished_at=codex_scheduler_runner.iso(now),
        )

        codex_scheduler_runner.update_task_state(task, state, run)
        updated = state["tasks"]["fixture_interval_task"]
        self.assertEqual("retry_exhausted", updated["last_status"])
        self.assertEqual(4, updated["retry_count"])
        self.assertEqual(codex_scheduler_runner.iso(now), updated["retry_exhausted_at"])
        self.assertIsNone(codex_scheduler_runner.get_due_reason(task, updated, now))

    def test_metrics_only_reports_retry_storm_after_cooldown(self) -> None:
        now = codex_scheduler_runner.now_bj()
        task = {
            "id": "fixture_interval_task",
            "enabled": True,
            "trigger": {"type": "interval", "every_seconds": 300},
            "action": {"type": "command", "command": ["python", "--version"]},
            "policy": {
                "retry_interval_seconds": 60,
                "max_retry_count": 2,
                "latest_lag_seconds": 3600,
                "retry_exhausted_action": "record_and_continue",
            },
        }
        state = {
            "tasks": {
                "fixture_interval_task": {
                    "last_status": "retry_exhausted",
                    "retry_count": 3,
                    "last_attempt_at": codex_scheduler_runner.iso(
                        now - timedelta(seconds=codex_scheduler_runner.RETRY_EXHAUSTED_COOLDOWN_SECONDS - 1)
                    ),
                }
            }
        }
        with (
            patch.object(codex_scheduler_runner, "snapshot", return_value={"tasks": [], "task_count": 0, "configuration": {}}),
            patch.object(codex_scheduler_runner, "load_state", return_value=state),
            patch.object(codex_scheduler_runner, "load_tasks", return_value=[task]),
            patch.object(codex_scheduler_runner, "now_bj", return_value=now),
        ):
            recent = codex_scheduler_runner.metrics()

        self.assertEqual(0, recent["retry_storm_candidate_count"])

        state["tasks"]["fixture_interval_task"]["last_attempt_at"] = codex_scheduler_runner.iso(
            now - timedelta(seconds=codex_scheduler_runner.RETRY_EXHAUSTED_COOLDOWN_SECONDS + 1)
        )
        with (
            patch.object(codex_scheduler_runner, "snapshot", return_value={"tasks": [], "task_count": 0, "configuration": {}}),
            patch.object(codex_scheduler_runner, "load_state", return_value=state),
            patch.object(codex_scheduler_runner, "load_tasks", return_value=[task]),
            patch.object(codex_scheduler_runner, "now_bj", return_value=now),
        ):
            elapsed = codex_scheduler_runner.metrics()

        self.assertEqual(1, elapsed["retry_storm_candidate_count"])
        self.assertEqual("retry_exhausted", elapsed["retry_storm_candidates"][0]["last_status"])

    def test_scheduler_authorization_hash_covers_only_registered_permission_semantics(self) -> None:
        from shared import scheduler_authorization

        task = copy.deepcopy(codex_scheduler_runner.DEFAULT_TASKS[0])
        baseline = scheduler_authorization.workflow_semantic_hash(task)
        task["name"] = "presentation-only rename"
        self.assertEqual(baseline, scheduler_authorization.workflow_semantic_hash(task))
        task["policy"]["risk"] = "critical"
        self.assertNotEqual(baseline, scheduler_authorization.workflow_semantic_hash(task))

    def test_scheduler_dry_run_plans_but_does_not_consume_authorization(self) -> None:
        task = copy.deepcopy(codex_scheduler_runner.DEFAULT_TASKS[0])
        with patch.object(codex_scheduler_runner, "write_run_record", return_value=Path("dry-run.json")):
            run = codex_scheduler_runner.run_task(task, "test", True)
        self.assertTrue(run.ok)
        self.assertFalse(run.authorization["consumed"])

    def test_scheduler_environment_consumes_dependency_and_runtime_health_authorities(self) -> None:
        from shared import scheduler_authorization

        import authorization_environment_provider
        authorization_environment_provider.clear_cache()
        snapshot = scheduler_authorization.environment_snapshot("workflow-test")
        self.assertIn("dependency_intelligence", snapshot["sources"])
        self.assertIn("runtime_health", snapshot["sources"])
        self.assertTrue(snapshot["sources"]["dependency_intelligence"]["signature"])
        self.assertFalse(snapshot["sources"]["dependency_intelligence"].get("validated_baseline_advanced", False))

    def test_approval_polling_change_does_not_slow_persistent_task_recovery(self) -> None:
        task = next(
            item
            for item in codex_scheduler_runner.DEFAULT_TASKS
            if item["id"] == "persistent_task_kernel_recover_expired"
        )

        self.assertEqual({"type": "interval", "every_seconds": 300}, task["trigger"])
        self.assertEqual(300, task["policy"]["retry_interval_seconds"])

    def test_resource_transfer_convergence_reuses_scheduler_and_never_downloads(self) -> None:
        task = next(item for item in codex_scheduler_runner.DEFAULT_TASKS if item["id"] == "resource_transfer_convergence")
        self.assertEqual({"type": "interval", "every_seconds": 300}, task["trigger"])
        self.assertEqual("_bridge/resource_transfer_owner.py", task["action"]["command"][1])
        self.assertIn("reconcile", task["action"]["command"])
        self.assertIn("never start a new transfer", task["policy"]["allowed_effect"])
        self.assertIn("another timer or retry loop", task["policy"]["allowed_effect"])

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

    def test_resource_process_apply_receives_authorization_refs_without_leaking_to_peers(self) -> None:
        registry = {
            "resource_process": {"name": "resource", "auto_policy": "controlled", "commands": {"apply": ["resource"]}},
            "peer": {"name": "peer", "auto_policy": "controlled", "commands": {"apply": ["peer"]}},
        }
        commands: list[list[str]] = []
        with patch.object(system_maintenance_cli, "REGISTRY", registry), patch.object(
            system_maintenance_cli,
            "run_json",
            side_effect=lambda command: commands.append(command) or {"ok": True},
        ):
            result = system_maintenance_cli.run_action(
                "apply",
                ["resource_process", "peer"],
                False,
                authorization={
                    "grant_ref": "scoped-authorization:grant:test",
                    "thread_id": "thread-test",
                    "operation_id": "operation-test",
                    "state_root": "/tmp/authorization-test",
                },
            )
        self.assertTrue(result["ok"])
        self.assertEqual(
            commands[0],
            [
                "resource",
                "--authorization-grant-ref",
                "scoped-authorization:grant:test",
                "--authorization-thread-id",
                "thread-test",
                "--authorization-operation-id",
                "operation-test",
                "--authorization-state-root",
                "/tmp/authorization-test",
            ],
        )
        self.assertEqual(commands[1], ["peer"])

    def test_apply_cli_projects_authorization_refs_to_run_action(self) -> None:
        with patch.object(system_maintenance_cli, "run_action", return_value={"ok": True}) as run_action, patch(
            "builtins.print"
        ):
            exit_code = system_maintenance_cli.main(
                [
                    "apply",
                    "--system",
                    "resource_process",
                    "--authorization-grant-ref",
                    "scoped-authorization:grant:test",
                    "--authorization-thread-id",
                    "thread-test",
                    "--authorization-operation-id",
                    "operation-test",
                    "--authorization-state-root",
                    "/tmp/authorization-test",
                ]
            )
        self.assertEqual(exit_code, 0)
        run_action.assert_called_once_with(
            "apply",
            ["resource_process"],
            False,
            authorization={
                "grant_ref": "scoped-authorization:grant:test",
                "thread_id": "thread-test",
                "operation_id": "operation-test",
                "state_root": "/tmp/authorization-test",
            },
        )


class WorkGitR2LifecycleAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state_root = root / "authorization"
        self.bare = root / "store.git"
        self.main = root / "main"
        self.tasks = root / "tasks"
        self.receipts = root / "receipts"
        self.rollout = root / "rollout.jsonl"
        self.rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-r2"}}) + "\n", encoding="utf-8")
        self._git(root, "init", "-q", "--bare", str(self.bare))
        self._git(root, "init", "-q", str(self.main))
        self._git(self.main, "config", "user.email", "tests@example.invalid")
        self._git(self.main, "config", "user.name", "R2 Lifecycle Tests")
        (self.main / "owned.txt").write_text("base\n", encoding="utf-8")
        self._git(self.main, "add", "owned.txt")
        self._git(self.main, "commit", "-q", "-m", "baseline")
        self._git(self.main, "branch", "-M", "main")
        self._git(self.main, "remote", "add", "origin", str(self.bare))
        self._git(self.main, "push", "-q", "-u", "origin", "main")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()

    def _message(self, text: str) -> str:
        event = {"timestamp": "2026-07-30T00:00:01+00:00", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}
        with self.rollout.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        line_number = len(self.rollout.read_text(encoding="utf-8").splitlines())
        return f"{self.rollout.name}:line:{line_number}"

    def _assessment(self, *, level: str = "R2", high_cost: bool = False) -> dict:
        costs = {
            "money_single_cny": 0, "money_daily_cny": 0, "elapsed_minutes": 1,
            "cpu_core_hours": 0, "gpu_minutes": 0, "network_gib": 0,
            "read_gib": 0, "read_records": 0, "write_gib": 0,
            "write_records": 1, "files_touched": 1, "external_calls": 0,
        }
        if high_cost:
            costs["elapsed_minutes"] = 31
        return {
            "subject": {"thread_id": "thread-r2"},
            "action": {"id": work_git_change_owner_process.ACTION},
            "resource": {"target_fingerprint": "filled-by-prepare"},
            "environment": {"owner": "work_git_change_owner", "phase": work_git_change_owner_process.PHASE, "source_signature": "filled-by-prepare", "recovery_ref": "filled-by-prepare"},
            "risk": {"level": level, "facts": []}, "costs": costs,
        }

    def _prepared(self, *, level: str = "R2", high_cost: bool = False, token_only_current: bool = False) -> dict:
        self._message("继续执行当前已声明的可恢复本地 Work Git change-set")
        user_message_ref = (
            self._message("AUTHORIZE-work-git-control")
            if token_only_current else f"{self.rollout.name}:line:2"
        )
        plan = work_git_change_owner.start_plan("r2-lifecycle", root=self.main, task_root=self.tasks)
        target = work_git_change_owner_process.lifecycle_target(
            repository_id=work_git_change_owner._repository_id(self.main), task_id="r2-lifecycle",
            branch=plan["branch"], base_head=plan["base_commit"], declared_paths=["owned.txt"],
        )
        scope = work_git_change_owner_process.lifecycle_scope(
            thread_id="thread-r2", repository_id=target["repository_id"], task_id=target["task_id"],
            branch=target["branch"], base_head=target["base_head"], declared_paths=target["declared_paths"],
        )
        assessment = self._assessment(level=level, high_cost=high_cost)
        assessment["resource"]["target_fingerprint"] = scope["target_fingerprint"]
        assessment["environment"].update({"source_signature": scope["source_signature"], "recovery_ref": f"git:HEAD:{scope['source_signature']}"})
        return work_git_change_owner.prepare_r2_lifecycle(
            "r2-lifecycle", ["owned.txt"], thread_id="thread-r2", assessment=assessment,
            rollout_path=self.rollout, user_message_ref=user_message_ref, operation_id="r2-operation",
            workflow_semantic_hash="workflow-r2-v1", root=self.main, state_root=self.state_root,
        )

    def test_token_only_current_message_prepares_existing_work_git_r2_lifecycle(self) -> None:
        prepared = self._prepared(token_only_current=True)
        self.assertTrue(prepared["ok"], prepared)
        started = work_git_change_owner.start_r2_task(
            "r2-lifecycle", ["owned.txt"], permit_ref=prepared["permit_ref"],
            operation_id="r2-operation", workflow_semantic_hash="workflow-r2-v1",
            confirm=work_git_change_owner.START_CONFIRM, root=self.main,
            task_root=self.tasks, receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertTrue(started["ok"], started)
        operation = work_git_change_owner_process.authorization.operation_snapshot(
            "r2-operation", executor="work_git_change_owner", state_root=self.state_root,
        )
        self.assertEqual(["start"], [row["step"] for row in operation["details"]["lifecycle_checkpoints"]])

    def test_single_task_intent_permit_covers_recoverable_start_commit_sync_integrate(self) -> None:
        prepared = self._prepared()
        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual("", prepared["permit_expires_at"])
        workflow_drift = work_git_change_owner.start_r2_task(
            "r2-lifecycle", ["owned.txt"], permit_ref="", operation_id="r2-operation",
            workflow_semantic_hash="workflow-r2-drift", confirm=work_git_change_owner.START_CONFIRM, root=self.main, task_root=self.tasks,
            receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertFalse(workflow_drift["ok"], workflow_drift)
        self.assertEqual("work_git_lifecycle_workflow_changed", workflow_drift["reason"])
        missing_confirm = work_git_change_owner.start_r2_task(
            "r2-lifecycle", ["owned.txt"], permit_ref=prepared["permit_ref"], operation_id="r2-operation",
            workflow_semantic_hash="workflow-r2-v1", confirm="", root=self.main, task_root=self.tasks,
            receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertTrue(missing_confirm["ok"], missing_confirm)
        started = work_git_change_owner.start_r2_task(
            "r2-lifecycle", ["owned.txt"], permit_ref=prepared["permit_ref"], operation_id="r2-operation",
            workflow_semantic_hash="workflow-r2-v1", confirm=work_git_change_owner.START_CONFIRM, root=self.main, task_root=self.tasks,
            receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertTrue(started["ok"], started)
        task_root = self.tasks / "r2-lifecycle"
        (task_root / "owned.txt").write_text("updated\n", encoding="utf-8")
        committed = work_git_change_owner.commit_r2_change_set(
            "r2-lifecycle", ["owned.txt"], permit_ref=prepared["permit_ref"], operation_id="r2-operation",
            workflow_semantic_hash="workflow-r2-v1", message="R2 lifecycle update", confirm="",
            root=task_root, receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertTrue(committed["ok"], committed)
        synced = work_git_change_owner.sync_r2_branch(
            "r2-lifecycle", permit_ref="", operation_id="r2-operation",
            workflow_semantic_hash="workflow-r2-v1", confirm="", root=task_root,
            receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertTrue(synced["ok"], synced)
        integrated = work_git_change_owner.integrate_r2_task(
            "r2-lifecycle", "codex/task/r2-lifecycle", permit_ref="", operation_id="r2-operation",
            workflow_semantic_hash="workflow-r2-v1", confirm="", root=task_root,
            receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertTrue(integrated["ok"], integrated)
        self.assertEqual("updated", self._git(self.main, "show", "HEAD:owned.txt"))
        retried = work_git_change_owner.integrate_r2_task(
            "r2-lifecycle", "codex/task/r2-lifecycle", permit_ref="", operation_id="r2-operation",
            workflow_semantic_hash="workflow-r2-v1", confirm="", root=task_root,
            receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertTrue(retried["ok"] and retried["reused"], retried)
        operation = work_git_change_owner_process.authorization.operation_snapshot("r2-operation", executor="work_git_change_owner", state_root=self.state_root)
        self.assertEqual("completed", operation["status"])
        self.assertEqual(["start", "commit", "sync", "integrate"], [row["step"] for row in operation["details"]["lifecycle_checkpoints"]])

    def test_successor_permit_binds_and_completes_exact_replay_lifecycle(self) -> None:
        predecessor = work_git_change_owner.start_task(
            "predecessor-r2", confirm=work_git_change_owner.START_CONFIRM,
            root=self.main, task_root=self.tasks, receipt_root=self.receipts,
        )
        predecessor_root = Path(predecessor["plan"]["destination"])
        self._git(predecessor_root, "config", "user.email", "tests@example.invalid")
        self._git(predecessor_root, "config", "user.name", "R2 Lifecycle Tests")
        (predecessor_root / "owned.txt").write_text("replayed\n", encoding="utf-8")
        predecessor_commit = work_git_change_owner.commit_change_set(
            "predecessor-r2", ["owned.txt"], message="Validated predecessor",
            confirm=work_git_change_owner.COMMIT_CONFIRM, root=predecessor_root,
            receipt_root=self.receipts,
        )
        (self.main / "foreign.txt").write_text("concurrent\n", encoding="utf-8")
        self._git(self.main, "add", "foreign.txt")
        self._git(self.main, "commit", "-q", "-m", "Concurrent main")
        successor = work_git_change_owner.successor_plan(
            "codex/task/predecessor-r2", ["owned.txt"], root=self.main,
        )
        self.assertTrue(successor["eligible"], successor)

        self._message("继续执行已验证 changeset 的 current-HEAD 精确续接")
        plan = work_git_change_owner.start_plan(
            "successor-r2", root=self.main, task_root=self.tasks,
        )
        scope = work_git_change_owner_process.lifecycle_scope(
            thread_id="thread-r2", repository_id=work_git_change_owner._repository_id(self.main),
            task_id="successor-r2", branch=plan["branch"],
            base_head=plan["base_commit"], declared_paths=["owned.txt"],
            successor=successor,
        )
        assessment = self._assessment()
        assessment["resource"]["target_fingerprint"] = scope["target_fingerprint"]
        assessment["environment"].update({
            "source_signature": scope["source_signature"],
            "recovery_ref": f"git:HEAD:{scope['source_signature']}",
        })
        prepared = work_git_change_owner.prepare_r2_lifecycle(
            "successor-r2", ["owned.txt"], thread_id="thread-r2",
            assessment=assessment, rollout_path=self.rollout,
            user_message_ref=f"{self.rollout.name}:line:2",
            operation_id="successor-r2-operation", workflow_semantic_hash="workflow-successor-v1",
            successor=successor, root=self.main, state_root=self.state_root,
        )
        self.assertTrue(prepared["ok"], prepared)
        started = work_git_change_owner.start_r2_task(
            "successor-r2", ["owned.txt"], permit_ref=prepared["permit_ref"],
            operation_id="successor-r2-operation", workflow_semantic_hash="workflow-successor-v1",
            confirm=work_git_change_owner.START_CONFIRM, root=self.main, task_root=self.tasks,
            receipt_root=self.receipts, successor=successor, state_root=self.state_root,
        )
        self.assertTrue(started["ok"], started)
        successor_root = self.tasks / "successor-r2"
        replayed = work_git_change_owner.replay_r2_successor(
            "successor-r2", ["owned.txt"], successor=successor,
            permit_ref=prepared["permit_ref"], operation_id="successor-r2-operation",
            workflow_semantic_hash="workflow-successor-v1",
            confirm=work_git_change_owner.REPLAY_CONFIRM, root=successor_root,
            receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertTrue(replayed["ok"], replayed)
        committed = work_git_change_owner.commit_r2_change_set(
            "successor-r2", ["owned.txt"], permit_ref=prepared["permit_ref"],
            operation_id="successor-r2-operation", workflow_semantic_hash="workflow-successor-v1",
            message="Replay validated successor", confirm=work_git_change_owner.COMMIT_CONFIRM,
            root=successor_root, receipt_root=self.receipts, successor=successor, state_root=self.state_root,
        )
        self.assertTrue(committed["ok"], committed)
        synced = work_git_change_owner.sync_r2_branch(
            "successor-r2", permit_ref=prepared["permit_ref"],
            operation_id="successor-r2-operation", workflow_semantic_hash="workflow-successor-v1",
            confirm=work_git_change_owner.SYNC_CONFIRM, root=successor_root,
            receipt_root=self.receipts, successor=successor, state_root=self.state_root,
        )
        self.assertTrue(synced["ok"], synced)
        integrated = work_git_change_owner.integrate_r2_task(
            "successor-r2", "codex/task/successor-r2", permit_ref=prepared["permit_ref"],
            operation_id="successor-r2-operation", workflow_semantic_hash="workflow-successor-v1",
            confirm=work_git_change_owner.INTEGRATE_CONFIRM, root=successor_root,
            receipt_root=self.receipts, successor=successor, state_root=self.state_root,
        )
        self.assertTrue(integrated["ok"], integrated)
        self.assertEqual(predecessor_commit["commit"], successor["predecessor_commit"])
        self.assertEqual("replayed", self._git(self.main, "show", "HEAD:owned.txt"))
        self.assertEqual("concurrent", self._git(self.main, "show", "HEAD:foreign.txt"))
        operation = work_git_change_owner_process.authorization.operation_snapshot(
            "successor-r2-operation", executor="work_git_change_owner", state_root=self.state_root,
        )
        self.assertEqual("completed", operation["status"])
        self.assertEqual(
            ["start", "replay", "commit", "sync", "integrate"],
            [row["step"] for row in operation["details"]["lifecycle_checkpoints"]],
        )

    def test_r3_or_high_cost_cannot_prepare_r2_lifecycle(self) -> None:
        for level, high_cost in (("R3", False), ("R2", True)):
            with self.subTest(level=level, high_cost=high_cost):
                prepared = self._prepared(level=level, high_cost=high_cost)
                self.assertFalse(prepared["ok"], prepared)
                self.assertIn(prepared["reason"], {"authorization_task_intent_pdp_decision_not_allow_without_challenge", "authorization_task_intent_risk_not_low"})

    def test_r2_prepare_template_rejection_receipt_and_operation_provenance(self) -> None:
        plan = work_git_change_owner.start_plan("r2-lifecycle", root=self.main, task_root=self.tasks)
        template = work_git_change_owner_process.assessment_template(
            thread_id="thread-r2", repository_id=work_git_change_owner._repository_id(self.main),
            task_id="r2-lifecycle", branch=plan["branch"], base_head=plan["base_commit"], declared_paths=["owned.txt"],
        )
        self.assertEqual("thread-r2", template["subject"]["thread_id"])
        self.assertEqual(
            set(work_git_change_owner_process.policy.load_policy()["cost_dimensions"]),
            set(template["costs"]),
        )
        malformed = self._assessment()
        rejected = work_git_change_owner.prepare_r2_lifecycle(
            "r2-lifecycle", ["owned.txt"], thread_id="thread-r2", assessment=malformed,
            rollout_path=self.rollout, user_message_ref=f"{self.rollout.name}:line:2",
            operation_id="r2-rejected", workflow_semantic_hash="workflow-r2-v1",
            root=self.main, state_root=self.state_root, receipt_root=self.receipts,
        )
        self.assertFalse(rejected["ok"])
        self.assertTrue(Path(rejected["rejection_receipt"]).is_file())
        self.assertIn("expected", rejected)
        prepared = self._prepared()
        started = work_git_change_owner.start_r2_task(
            "r2-lifecycle", ["owned.txt"], permit_ref=prepared["permit_ref"], operation_id="r2-operation",
            workflow_semantic_hash="workflow-r2-v1", confirm=work_git_change_owner.START_CONFIRM,
            root=self.main, task_root=self.tasks, receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertTrue(started["ok"], started)
        operation = work_git_change_owner_process.authorization.operation_snapshot(
            "r2-operation", executor="work_git_change_owner", state_root=self.state_root,
        )
        self.assertEqual(prepared["permit_ref"], operation["permit_ref"])
        self.assertTrue(operation["intent_ref"])
        self.assertTrue(operation["user_message_ref"])
        self.assertTrue(operation["relevant_input_signature"])

    def test_r2_prepare_success_reuse_and_rejection_have_bounded_receipts_and_unique_lookup(self) -> None:
        self._message("批准实施精确 R2 lifecycle；不要在任何收据中泄露这个测试的用户正文。")
        plan = work_git_change_owner.start_plan("r2-observable", root=self.main, task_root=self.tasks)
        scope = work_git_change_owner_process.lifecycle_scope(
            thread_id="thread-r2", repository_id=work_git_change_owner._repository_id(self.main),
            task_id="r2-observable", branch=plan["branch"], base_head=plan["base_commit"], declared_paths=["owned.txt"],
        )
        assessment = self._assessment()
        assessment["resource"]["target_fingerprint"] = scope["target_fingerprint"]
        assessment["environment"].update({"source_signature": scope["source_signature"], "recovery_ref": f"git:HEAD:{scope['source_signature']}"})
        prepared = work_git_change_owner.prepare_r2_lifecycle(
            "r2-observable", ["owned.txt"], thread_id="thread-r2", assessment=assessment,
            rollout_path=self.rollout, user_message_ref=f"{self.rollout.name}:line:2", operation_id="r2-observable-op",
            workflow_semantic_hash="workflow-r2-v1", root=self.main, state_root=self.state_root, receipt_root=self.receipts,
        )
        self.assertTrue(prepared["ok"], prepared)
        receipt = json.loads(Path(prepared["prepare_receipt"]).read_text(encoding="utf-8"))
        self.assertEqual("prepared", receipt["status"])
        self.assertEqual("r2-observable-op", receipt["operation_id"])
        self.assertEqual(scope["target_fingerprint"], receipt["target_fingerprint"])
        self.assertTrue(receipt["intent_ref"] and receipt["relevant_input_signature"])
        self.assertNotIn("permit_ref", receipt)
        self.assertNotIn("不要在任何收据中泄露", json.dumps(receipt, ensure_ascii=False))
        reused = work_git_change_owner.prepare_r2_lifecycle(
            "r2-observable", ["owned.txt"], thread_id="thread-r2", assessment=assessment,
            rollout_path=self.rollout, user_message_ref=f"{self.rollout.name}:line:2", operation_id="r2-observable-op",
            workflow_semantic_hash="workflow-r2-v1", root=self.main, state_root=self.state_root, receipt_root=self.receipts,
        )
        self.assertTrue(reused["ok"] and reused["reused"], reused)
        resolved = work_git_change_owner.prepare_result("r2-observable-op", task_id="r2-observable", receipt_root=self.receipts)
        self.assertTrue(resolved["ok"], resolved)
        self.assertTrue(resolved["reused"])
        rejected = work_git_change_owner.prepare_r2_lifecycle(
            "r2-rejected-observable", ["owned.txt"], thread_id="thread-r2", assessment=self._assessment(),
            rollout_path=self.rollout, user_message_ref=f"{self.rollout.name}:line:2", operation_id="r2-rejected-observable-op",
            workflow_semantic_hash="workflow-r2-v1", root=self.main, state_root=self.state_root, receipt_root=self.receipts,
        )
        self.assertFalse(rejected["ok"])
        self.assertTrue(Path(rejected["prepare_receipt"]).is_file())

    def test_r2_prepare_cli_receipt_query_concurrency_and_private_operation_resolution(self) -> None:
        self._message("批准实施当前精确 R2 lifecycle")
        plan = work_git_change_owner.start_plan("r2-cli-observable", root=self.main, task_root=self.tasks)
        scope = work_git_change_owner_process.lifecycle_scope(
            thread_id="thread-r2", repository_id=work_git_change_owner._repository_id(self.main),
            task_id="r2-cli-observable", branch=plan["branch"], base_head=plan["base_commit"], declared_paths=["owned.txt"],
        )
        assessment = self._assessment()
        assessment["resource"]["target_fingerprint"] = scope["target_fingerprint"]
        assessment["environment"].update({"source_signature": scope["source_signature"], "recovery_ref": f"git:HEAD:{scope['source_signature']}"})
        self.state_root.mkdir(parents=True, exist_ok=True)
        assessment_path = self.state_root / "assessment.json"
        assessment_path.write_text(json.dumps(assessment), encoding="utf-8")
        command = [
            "python3", str(Path(work_git_change_owner.__file__).resolve()), "r2-prepare",
            "--task-id", "r2-cli-observable", "--declared", "owned.txt", "--thread-id", "thread-r2",
            "--assessment-json", str(assessment_path), "--rollout-path", str(self.rollout),
            "--user-message-ref", f"{self.rollout.name}:line:2", "--operation-id", "r2-cli-observable-op",
            "--workflow-semantic-hash", "workflow-r2-v1", "--authorization-state-root", str(self.state_root),
            "--receipt-root", str(self.receipts), "--root", str(self.main),
        ]
        first = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(0, first.returncode, first.stderr)
        stdout = json.loads(first.stdout)
        self.assertTrue(stdout["ok"], stdout)
        self.assertEqual("prepared", stdout["status"])
        self.assertNotIn("permit_ref", stdout)
        stored = json.loads(Path(stdout["receipt_ref"]).read_text(encoding="utf-8"))
        self.assertEqual(stdout, stored)
        query = subprocess.run([
            "python3", str(Path(work_git_change_owner.__file__).resolve()), "prepare-result",
            "--operation-id", "r2-cli-observable-op", "--task-id", "r2-cli-observable",
            "--receipt-root", str(self.receipts), "--root", str(self.main),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(0, query.returncode, query.stderr)
        self.assertEqual(stdout, json.loads(query.stdout))
        started = work_git_change_owner.start_r2_task(
            "r2-cli-observable", ["owned.txt"], permit_ref="", operation_id="r2-cli-observable-op",
            workflow_semantic_hash="workflow-r2-v1", confirm=work_git_change_owner.START_CONFIRM, root=self.main,
            task_root=self.tasks, receipt_root=self.receipts, state_root=self.state_root,
        )
        self.assertTrue(started["ok"], started)
        operation = work_git_change_owner_process.authorization.operation_projection(
            "r2-cli-observable-op", executor="work_git_change_owner", state_root=self.state_root,
        )
        self.assertTrue(operation["ok"], operation)
        self.assertEqual("r2-cli-observable", operation["task_id"])
        self.assertEqual(scope["target_fingerprint"], operation["target_fingerprint"])
        self.assertEqual(stdout["scope_signature"], operation["scope_signature"])
        self.assertNotIn("permit_ref", operation)
        retry = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(0, retry.returncode, retry.stderr)
        self.assertTrue(json.loads(retry.stdout)["reused"])
        reject = subprocess.run([
            "python3", str(Path(work_git_change_owner.__file__).resolve()), "r2-prepare",
            "--task-id", "r2-cli-rejected", "--declared", "owned.txt", "--thread-id", "thread-r2",
            "--assessment-json", str(self.state_root / "missing.json"), "--rollout-path", str(self.rollout),
            "--user-message-ref", f"{self.rollout.name}:line:2", "--operation-id", "r2-cli-rejected-op",
            "--workflow-semantic-hash", "workflow-r2-v1", "--receipt-root", str(self.receipts), "--root", str(self.main),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(1, reject.returncode)
        rejection = json.loads(reject.stdout)
        self.assertFalse(rejection["ok"])
        self.assertEqual("rejected", rejection["status"])
        self.assertNotIn("permit_ref", rejection)
        self.assertEqual(rejection, json.loads(Path(rejection["receipt_ref"]).read_text(encoding="utf-8")))


class MirrorWorkflowFacadeTests(unittest.TestCase):
    def test_workflow_mirror_status_uses_shared_bounded_projection(self) -> None:
        payload = {
            "schema": "codex_environment_mirror.status.v1",
            "ok": True,
            "next_action": "inspect the stable result reference when more detail is required",
            "validation": {"raw_owner_payload": "x" * 40000},
        }
        with patch.object(
            codex_workflow_entry,
            "execute_mirror_command",
            return_value=payload,
        ) as execute, patch.object(codex_workflow_entry, "print_json") as emit:
            exit_code = codex_workflow_entry.main(["mirror", "status"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(execute.call_args.args, ("status",))
        projected = emit.call_args.args[0]
        self.assertEqual(projected["output_mode"], "default_bounded")
        self.assertEqual(projected["next_action"], payload["next_action"])
        self.assertEqual(projected["raw_result_ref"], "command:python _bridge/codex_workflow_entry.py mirror status")

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
