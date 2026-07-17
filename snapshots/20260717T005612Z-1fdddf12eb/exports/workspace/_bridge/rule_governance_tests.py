"""Regression tests for rule authority registry activation metadata.

Ownership: rule_governance registry and validator consistency checks.
Non-goals: execute rule owners, mutate registry state, or replace owner validators.
State behavior: read-only tests over the checked-in registry.
Caller context: workflow/rule-governance closeout and maintenance validation.
"""

from __future__ import annotations

import unittest

from rule_governance import SCOPE_LAYER_ROLES, doctor, load_registry


class RuleGovernanceRegistryTests(unittest.TestCase):
    def test_scope_layer_roles_are_registered(self) -> None:
        registry = load_registry()
        valid_layer_roles = set(registry.get("valid_layer_roles") or [])
        scoped_roles = {role for roles in SCOPE_LAYER_ROLES.values() for role in roles}

        self.assertFalse(scoped_roles - valid_layer_roles)

    def test_federated_evolution_is_workflow_reconciliation_gate(self) -> None:
        registry = load_registry()
        surface = next(item for item in registry["surfaces"] if item["rule_id"] == "workflow.federated_evolution")
        contract = next(
            item for item in registry["activation_contracts"] if item["rule_id"] == "workflow.federated_evolution"
        )

        self.assertEqual(surface["scope"], "workflow")
        self.assertEqual(contract["effect"], "mandatory")
        self.assertEqual(contract["layer_role"], "reconciliation_gate")
        self.assertIn("reconciliation_gate", SCOPE_LAYER_ROLES["workflow"])

        payload = doctor(full=True)
        blocker_codes = {
            item.get("code")
            for item in payload["issues"]
            if item.get("rule_id") == "workflow.federated_evolution" and item.get("severity") == "blocker"
        }
        self.assertNotIn("invalid_layer_role", blocker_codes)
        self.assertNotIn("layer_scope_mismatch", blocker_codes)


if __name__ == "__main__":
    unittest.main()
