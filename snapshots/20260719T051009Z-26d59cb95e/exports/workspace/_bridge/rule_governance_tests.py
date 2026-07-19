"""Regression tests for rule authority registry activation metadata.

Ownership: rule_governance registry and validator consistency checks.
Non-goals: execute rule owners, mutate registry state, or replace owner validators.
State behavior: read-only tests over the checked-in registry.
Caller context: workflow/rule-governance closeout and maintenance validation.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rule_governance
from rule_governance import SCOPE_LAYER_ROLES, doctor, load_registry


class RuleGovernanceRegistryTests(unittest.TestCase):
    def test_legacy_codex_home_source_maps_to_declared_platform_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            agents = codex_home / "AGENTS.md"
            agents.write_text("test", encoding="utf-8")
            with patch.object(rule_governance, "CODEX_HOME", codex_home):
                resolved = rule_governance.expand_source("C:/Users/45543/.codex/AGENTS.md")

        self.assertEqual(resolved, [agents])

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

    def test_work_git_workspace_prefix_maps_to_active_rule_surface(self) -> None:
        registry = {
            "surfaces": [
                {
                    "rule_id": "workflow.rule_governance",
                    "source": "_bridge/rule_governance.py",
                    "owner": "rule_governance",
                    "validator": "python _bridge/rule_governance.py validate",
                    "enforcement_point": "changed_file_impact",
                }
            ],
            "activation_contracts": [],
        }
        with patch.object(rule_governance, "load_registry", return_value=registry):
            payload = rule_governance.impact(["workspace/_bridge/rule_governance.py"])

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["rule_change_required"])
        self.assertEqual(payload["affected"][0]["rule_id"], "workflow.rule_governance")


if __name__ == "__main__":
    unittest.main()
