#!/usr/bin/env python3
"""Focused regression tests for machine-first workflow delegation."""

from __future__ import annotations

import unittest

from workflow_automation_delegation import automation_delegation_decision, compact_automation_delegation_policy, input_signature, single_authority_plan_check


class WorkflowAutomationDelegationTests(unittest.TestCase):
    def test_policy_requires_single_authority_and_derived_projections(self) -> None:
        policy = compact_automation_delegation_policy()
        self.assertIn("Persist each contract", policy["single_authority_principle"])
        self.assertIn("use_refs_for_cross_layer_consumption_instead_of_copying_full_payloads", policy["redundancy_design_checks"])
        self.assertIn("automate_only_a_declared_owner_operation_with_complete_inputs_and_a_stable_input_signature", policy["machine_execution_invariants"])

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


if __name__ == "__main__":
    unittest.main()
