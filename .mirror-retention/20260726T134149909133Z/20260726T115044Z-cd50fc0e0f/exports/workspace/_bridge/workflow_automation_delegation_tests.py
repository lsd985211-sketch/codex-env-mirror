#!/usr/bin/env python3
"""Focused regression tests for machine-first workflow delegation."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from workflow_automation_delegation import (
    automation_delegation_decision,
    compact_automation_delegation_policy,
    input_signature,
    POLICY_SCHEMA,
    schema_authority_contract,
    single_authority_plan_check,
    terminal_invalidated_actions,
    terminal_receipt_decision,
    validate_consumer_schema_authority,
    validate_schema_authority,
)


class WorkflowAutomationDelegationTests(unittest.TestCase):
    def test_policy_schema_is_owned_once_and_consumers_import_it(self) -> None:
        policy = compact_automation_delegation_policy()
        authority = schema_authority_contract()
        self.assertEqual(policy["schema"], POLICY_SCHEMA)
        self.assertEqual(authority["schema"], POLICY_SCHEMA)
        self.assertIn("do not pin a version literal", authority["rule"])

    def test_schema_authority_validation_catches_current_or_stale_copies(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stale_schema = POLICY_SCHEMA.rsplit("v", 1)[0] + "v4"
            (root / "consumer.py").write_text(
                f"EXPECTED = '{stale_schema}'\n", encoding="utf-8"
            )
            stale = validate_schema_authority(bridge_root=root)
            self.assertFalse(stale["ok"])
            self.assertEqual(stale["copied_literals"][0]["schemas"], [stale_schema])

            (root / "consumer.py").write_text(
                f"EXPECTED = '{POLICY_SCHEMA}'\n", encoding="utf-8"
            )
            copied_current = validate_schema_authority(bridge_root=root)
            self.assertFalse(copied_current["ok"])
            self.assertEqual(copied_current["copied_literals"][0]["schemas"], [POLICY_SCHEMA])

            (root / "consumer.py").write_text(
                "from workflow_automation_delegation import POLICY_SCHEMA\n", encoding="utf-8"
            )
            self.assertTrue(validate_schema_authority(bridge_root=root)["ok"])

    def test_generic_schema_authority_guard_is_reusable_by_peer_producers(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            producer = root / "producer.py"
            consumer = root / "consumer.py"
            family = "peer_contract"
            schema = family + ".v7"
            producer.write_text("SCHEMA = FAMILY + '.v7'\n", encoding="utf-8")
            consumer.write_text(f"EXPECTED = '{schema}'\n", encoding="utf-8")
            rejected = validate_consumer_schema_authority(
                family=family, schema=schema, producer_path=producer, consumer_root=root
            )
            self.assertFalse(rejected["ok"])
            consumer.write_text("from producer import SCHEMA\n", encoding="utf-8")
            self.assertTrue(
                validate_consumer_schema_authority(
                    family=family, schema=schema, producer_path=producer, consumer_root=root
                )["ok"]
            )

    def test_policy_requires_single_authority_and_derived_projections(self) -> None:
        policy = compact_automation_delegation_policy()
        self.assertIn("Persist each contract", policy["single_authority_principle"])
        self.assertIn("use_refs_for_cross_layer_consumption_instead_of_copying_full_payloads", policy["redundancy_design_checks"])
        self.assertIn("automate_only_a_declared_owner_operation_with_complete_inputs_and_a_stable_input_signature", policy["machine_execution_invariants"])

    def test_policy_reuses_one_machine_readable_command_contract_before_help(self) -> None:
        policy = compact_automation_delegation_policy()
        contract = policy["command_contract_reuse"]
        self.assertEqual(contract["scope"], "one_owner_version_and_command_family_per_task")
        self.assertEqual(contract["authority_order"][0], "owner_plan_receipt")
        self.assertEqual(contract["authority_order"][-1], "targeted_help_last_resort")
        self.assertIn("argument_rejected", contract["expand_help_only_when"])
        self.assertIn("apply, commit, integrate", contract["adjacent_subcommand_rule"])
        self.assertIn("remains available", contract["capability_preservation"])

    def test_single_authority_check_rejects_cross_layer_contract_copies(self) -> None:
        bad = {
            "structured_route": {"task_contract": {"task_facts": {}}, "route_decision": {}},
            "execution_route_pack": {
                "route_decision": {"task_contract": {}, "task_facts": {}, "matched_signals": {}},
                "resource_gate": {},
                "asset_guidance": {},
                "environment_context": {},
                "automation_decision": {},
            },
            "asset_guidance": {},
            "environment_context": {},
            "automation_decision": {},
        }
        result = single_authority_plan_check(bad)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["task_contract_has_one_authority"])
        self.assertFalse(result["checks"]["route_decision_has_one_authority"])

    def test_signature_ignores_mapping_order_and_chat_narration(self) -> None:
        first = input_signature(declared_inputs={"route": {"tool": "resource"}, "targets": ["a", "b"]})
        second = input_signature(declared_inputs={"targets": ["a", "b"], "route": {"tool": "resource"}})
        self.assertEqual(first, second)

    def test_low_risk_known_route_is_machine_owned(self) -> None:
        payload = automation_delegation_decision(
            task_facts={},
            owner_route={"mcp_profile": "codegraph", "capability": "code_structure"},
            required_gates=[],
            machine_phases=[{"id": "phase_1", "enabled": True, "commands": [{"read_only": True, "approval_required": False}]}],
            declared_inputs={"target": "module"},
        )
        self.assertEqual(payload["decision_class"], "auto_execute")
        self.assertEqual(payload["machine_actions"], ["phase_1"])
        self.assertEqual(payload["machine_execution_contract"]["input_signature"], payload["input_signature"])
        self.assertIn("consumable_receipt", payload["machine_execution_contract"]["required_evidence"])
        self.assertFalse(payload["machine_execution_contract"]["automated_write_allowed"])
        self.assertFalse(payload["codex_escalation"]["required_now"])

    def test_resource_work_is_deferred_with_batch_and_receipt_reuse(self) -> None:
        payload = automation_delegation_decision(
            task_facts={"external_network_read": True},
            owner_route={"owner_profile": "resource_layer", "capability": "resource_acquisition"},
            required_gates=[],
            machine_phases=[],
            declared_inputs={"urls": ["a", "b"]},
            resource_required=True,
        )
        self.assertEqual(payload["decision_class"], "codex_deferred")
        self.assertTrue(payload["batch_policy"]["eligible"])
        self.assertIn("same_input_signature", payload["reuse_policy"]["reuse_receipt_when"])

    def test_ambiguity_or_write_boundary_stays_with_codex(self) -> None:
        payload = automation_delegation_decision(
            task_facts={"external_write": True},
            owner_route={"mcp_profile": "github", "capability": "github_remote"},
            required_gates=[],
            machine_phases=[],
            declared_inputs={"repository": "owner/repo"},
            ambiguous=True,
        )
        self.assertEqual(payload["decision_class"], "review_required")
        self.assertTrue(payload["codex_escalation"]["required_now"])

    def test_terminal_receipt_reuse_requires_same_intent_and_owner_version(self) -> None:
        rejected = terminal_receipt_decision(
            action_id="mirror.publish",
            input_signature="sig-a",
            owner_contract_version="v1",
            intent_id="intent-a",
            receipt={"reuse_key": "wrong", "accepted": True},
        )
        self.assertEqual(rejected["decision"], "execute")
        self.assertEqual(rejected["reason"], "receipt_identity_mismatch")

        accepted = terminal_receipt_decision(
            action_id="mirror.publish",
            input_signature="sig-a",
            owner_contract_version="v1",
            intent_id="intent-a",
            receipt=rejected["expected_receipt"],
        )
        self.assertEqual(accepted["decision"], "reuse")

    def test_changed_action_invalidates_only_reachable_dependents(self) -> None:
        actions = [
            {"action_id": "host_projection.apply", "depends_on": []},
            {"action_id": "work_git.commit", "depends_on": ["host_projection.apply"]},
            {"action_id": "work_git.sync_bare", "depends_on": ["work_git.commit"]},
            {"action_id": "mirror.publish", "depends_on": ["work_git.sync_bare"]},
            {"action_id": "mirror.verify", "depends_on": ["mirror.publish"]},
            {"action_id": "external.research", "depends_on": []},
        ]
        self.assertEqual(
            terminal_invalidated_actions(actions, ["host_projection.apply"]),
            ["work_git.commit", "work_git.sync_bare", "mirror.publish", "mirror.verify"],
        )


if __name__ == "__main__":
    unittest.main()
