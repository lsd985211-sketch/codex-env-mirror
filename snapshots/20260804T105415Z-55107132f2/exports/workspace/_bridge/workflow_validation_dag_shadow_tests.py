#!/usr/bin/env python3

import unittest

from workflow_validation_dag_shadow import (
    attach_dag_shadow,
    build_input_signature,
    build_validation_dag_shadow,
    compact_dag_shadow,
)


def membership() -> dict:
    return {"ok": True, "coverage_complete": True, "schema": "system_membership.v2.impact"}


def rules() -> dict:
    return {"ok": True, "schema": "rule_governance.impact.v1", "affected": [], "unmatched": []}


def membership_snapshot() -> dict:
    return {
        "ok": True,
        "schema": "system_membership.v2.snapshot",
        "systems": ["workflow"],
        "contracts": {"workflow": "v1"},
        "impact_rule_count": 1,
    }


def rule_snapshot() -> dict:
    return {
        "ok": True,
        "schema": "rule_governance.snapshot.v1",
        "registry": {"version": 1},
        "activation": {"version": 1},
        "surfaces": [{"rule_id": "workflow.test"}],
        "surface_count": 1,
    }


def batch(*ids: str) -> dict:
    return {
        "ok": True,
        "read_view": {
            "source_signature": "registry-v1",
            "index_schema": "maintenance_capability_registry.v4",
            "authority_status": "source_shard_authoritative",
        },
        "results": [{"items": [{"capability_id": value} for value in ids]}],
    }


def row(capability_id: str, action: str, **maintenance: object) -> dict:
    return {
        "capability_id": capability_id,
        "system": "workflow",
        "module_path": "_bridge/workflow_validation_dag_shadow.py",
        "contract_fingerprint": f"contract-{capability_id}",
        "action_commands": {action: [action]},
        "usual_entry": "",
        "maintenance": {"automatic_actions": [action], **maintenance},
    }


class ValidationDagShadowTests(unittest.TestCase):
    def _build(self, changed: list[str], **kwargs: object) -> dict:
        kwargs.setdefault("membership_impact", membership())
        kwargs.setdefault("rule_impact", rules())
        kwargs.setdefault("membership_snapshot", membership_snapshot())
        kwargs.setdefault("rule_snapshot", rule_snapshot())
        return build_validation_dag_shadow(
            changed,
            **kwargs,
        )

    def test_rejects_empty_git_metadata_and_outside_changed_sets(self) -> None:
        self.assertEqual("changed_files_required", build_validation_dag_shadow([])["reason"])
        self.assertEqual("changed_file_git_metadata", build_validation_dag_shadow([".git/config"])["reason"])
        self.assertEqual("changed_file_outside_worktree", build_validation_dag_shadow(["/tmp/not-workspace.py"])["reason"])

    def test_fails_closed_for_unmapped_membership_or_rules(self) -> None:
        member_blocked = build_validation_dag_shadow(
            ["workspace/_bridge/workflow_orchestrator.py"],
            membership_impact={"ok": False, "coverage_complete": False},
            rule_impact=rules(),
        )
        self.assertEqual("unmapped_change", member_blocked["blockers"][0]["status"])
        rule_blocked = build_validation_dag_shadow(
            ["workspace/_bridge/workflow_orchestrator.py"],
            membership_impact=membership(),
            rule_impact={"ok": True, "unmatched": ["workspace/_bridge/unknown.py"]},
        )
        self.assertEqual("invalid_signature", rule_blocked["blockers"][0]["status"])

    def test_projects_conflict_and_platform_without_execution_or_reuse(self) -> None:
        rows = [
            row("first", "validate", conflict_group="shared"),
            row("second", "validate", conflict_group="shared"),
            row("windows", "validate", platform_scope="windows"),
        ]
        result = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"],
            registry_batch=batch("first", "second", "windows"),
            registry_rows=rows, platform_scopes={"linux"},
        )
        statuses = {node["node_id"]: node["shadow_status"] for node in result["nodes"]}
        self.assertEqual("would_execute", statuses["first:validate"])
        self.assertEqual("would_block_conflict", statuses["second:validate"])
        self.assertEqual("would_defer_platform_scope", statuses["windows:validate"])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["execution_enabled"])
        self.assertFalse(result["receipt_reuse_enabled"])
        self.assertFalse(result["cache_enabled"])
        self.assertTrue(all(node["signature_fields_complete"] for node in result["nodes"]))
        self.assertTrue(all(node["input_signature"] for node in result["nodes"]))
        self.assertNotIn("passed", statuses.values())
        self.assertNotIn("reused", statuses.values())

    def test_detects_cycles_and_does_not_claim_completion(self) -> None:
        rows = [
            row("first", "validate", dependencies=["capability:second:validate"]),
            row("second", "validate", dependencies=["capability:first:validate"]),
        ]
        result = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"],
            registry_batch=batch("first", "second"), registry_rows=rows,
        )
        self.assertFalse(result["ok"])
        self.assertEqual({"cycle_detected"}, {node["shadow_status"] for node in result["nodes"]})

    def test_missing_mandatory_validator_is_a_global_blocker_not_a_false_node_failure(self) -> None:
        result = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"],
            rule_impact={
                "ok": True,
                "schema": "rule_governance.impact.v1",
                "unmatched": [],
                "affected": [{"validator": "python _bridge/missing_validator.py validate"}],
            },
            registry_batch=batch("first"), registry_rows=[row("first", "validate")],
        )
        self.assertFalse(result["ok"])
        self.assertEqual("would_execute", result["nodes"][0]["shadow_status"])
        self.assertEqual("invalid_signature", result["blockers"][-1]["status"])

    def test_registry_usual_entry_covers_mandatory_validator_without_new_owner(self) -> None:
        covered = {
            **row("bounded", "validate"),
            "usual_entry": "validate through `maintenance_control_plane_tests.py`",
        }
        result = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"],
            rule_impact={
                "ok": True,
                "schema": "rule_governance.impact.v1",
                "unmatched": [],
                "affected": [{"validator": "python _bridge/maintenance_control_plane_tests.py"}],
            },
            registry_batch=batch("bounded"), registry_rows=[covered],
        )
        self.assertTrue(result["ok"])
        self.assertEqual([], result["blockers"])

    def test_existing_owner_nodes_cover_mixed_python_validator_commands(self) -> None:
        rows = [
            {
                **row("orchestrator", "validate"),
                "module_path": "_bridge/workflow_orchestrator.py",
                "usual_entry": "validate",
            },
            {
                **row("long-command", "validate"),
                "module_path": "_bridge/shared/long_command_receipt.py",
                "usual_entry": "validate",
            },
        ]
        result = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"],
            rule_impact={
                "ok": True,
                "schema": "rule_governance.impact.v1",
                "unmatched": [],
                "affected": [{
                    "validator": (
                        "python _bridge\\workflow_orchestrator.py validate && "
                        "python _bridge/shared/long_command_receipt.py validate"
                    ),
                }],
            },
            registry_batch=batch("orchestrator", "long-command"),
            registry_rows=rows,
        )
        self.assertTrue(result["ok"])
        self.assertEqual([], result["blockers"])

    def test_unknown_validator_remains_fail_closed(self) -> None:
        result = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"],
            rule_impact={
                "ok": True,
                "schema": "rule_governance.impact.v1",
                "unmatched": [],
                "affected": [{"validator": "python -m _bridge.unknown_validator validate"}],
            },
            registry_batch=batch("orchestrator"),
            registry_rows=[row("orchestrator", "validate")],
        )
        self.assertFalse(result["ok"])
        self.assertEqual("invalid_signature", result["blockers"][-1]["status"])
        self.assertEqual(
            "mandatory_validator_not_graph_covered",
            result["blockers"][-1]["reason"],
        )

    def test_signature_is_order_insensitive_but_sensitive_to_each_semantic_input(self) -> None:
        fields = {
            "changed_file_bytes": "changed", "owner_source": "owner", "command_contract": "contract",
            "validator_schema": "validator", "validation_arguments": "argv-a", "membership_authority": "membership",
            "rule_authority": "rules", "platform_environment": "platform", "acceptance_predicate": "predicate",
        }
        first = build_input_signature(fields)
        reordered = build_input_signature(dict(reversed(list(fields.items()))))
        self.assertTrue(first["signature_fields_complete"])
        self.assertEqual(first["input_signature"], reordered["input_signature"])
        for field, value in fields.items():
            with self.subTest(field=field):
                changed = build_input_signature({**fields, field: f"{value}-changed"})
                self.assertNotEqual(first["input_signature"], changed["input_signature"])

    def test_each_missing_signature_field_invalidates_its_node(self) -> None:
        fields = {
            "changed_file_bytes": "changed", "owner_source": "owner", "command_contract": "contract",
            "validator_schema": "validator", "validation_arguments": "argv", "membership_authority": "membership",
            "rule_authority": "rules", "platform_environment": "platform", "acceptance_predicate": "predicate",
        }
        for field in fields:
            with self.subTest(field=field):
                signature = build_input_signature({**fields, field: ""})
                self.assertFalse(signature["signature_fields_complete"])
                self.assertEqual([field], signature["signature_missing_fields"])

    def test_missing_owner_bytes_marks_node_invalid_signature(self) -> None:
        result = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"],
            registry_batch=batch("first"),
            registry_rows=[{**row("first", "validate"), "module_path": "_bridge/missing_validator.py"}],
        )
        self.assertEqual("invalid_signature", result["nodes"][0]["shadow_status"])
        self.assertIn("owner_source", result["nodes"][0]["signature_missing_fields"])

    def test_receipt_readback_never_reuses_without_exact_successful_readback(self) -> None:
        first = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"], registry_batch=batch("first"), registry_rows=[row("first", "validate")],
        )
        node = first["nodes"][0]
        mismatched = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"], registry_batch=batch("first"), registry_rows=[row("first", "validate")],
            terminal_receipts=[{"node_id": node["node_id"], "input_signature": "old", "status": "healthy", "artifact_ref": "receipt:old"}],
        )
        self.assertEqual("signature_mismatch", mismatched["nodes"][0]["receipt_readback"]["status"])
        self.assertFalse(mismatched["receipt_reuse_enabled"])
        unread = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"], registry_batch=batch("first"), registry_rows=[row("first", "validate")],
            terminal_receipts=[{"node_id": node["node_id"], "input_signature": node["input_signature"], "status": "healthy", "artifact_ref": "receipt:declared-only"}],
        )
        self.assertEqual("readback_incomplete", unread["nodes"][0]["receipt_readback"]["status"])
        eligible = self._build(
            ["workspace/_bridge/workflow_orchestrator.py"], registry_batch=batch("first"), registry_rows=[row("first", "validate")],
            terminal_receipts=[{"node_id": node["node_id"], "input_signature": node["input_signature"], "status": "healthy", "artifact_ref": "receipt:exact", "readback_ok": True}],
        )
        self.assertEqual("eligible", eligible["nodes"][0]["receipt_readback"]["status"])
        self.assertEqual(1, eligible["receipt_observation"]["eligible_count"])
        self.assertFalse(eligible["receipt_reuse_enabled"])

    def test_attachment_and_default_projection_preserve_authoritative_payload(self) -> None:
        source = {"ok": True, "checks": [{"name": "existing", "ok": True}]}
        shadow = build_validation_dag_shadow([], membership_impact=membership(), rule_impact=rules())
        attached = attach_dag_shadow(source, shadow)
        self.assertEqual({"ok": True, "checks": [{"name": "existing", "ok": True}]}, source)
        self.assertTrue(attached["validation_dag_shadow"]["ok"])
        self.assertFalse(attached["validation_dag_shadow"]["shadow_ok"])
        self.assertFalse(attached["validation_dag_shadow"]["activation_ready"])
        projected = compact_dag_shadow(attached["validation_dag_shadow"])
        self.assertNotIn("nodes", projected)
        self.assertIn("full_result_ref", projected)


if __name__ == "__main__":
    unittest.main()
