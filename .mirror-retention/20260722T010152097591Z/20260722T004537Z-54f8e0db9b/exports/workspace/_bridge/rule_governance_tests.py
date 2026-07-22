"""Regression tests for rule authority registry activation metadata.

Ownership: rule_governance registry and validator consistency checks.
Non-goals: execute rule owners, mutate registry state, or replace owner validators.
State behavior: read-only tests over the checked-in registry.
Caller context: workflow/rule-governance closeout and maintenance validation.
"""

from __future__ import annotations

import tempfile
import unittest
import os
import copy
from pathlib import Path
from unittest.mock import patch

import rule_governance
from rule_governance import SCOPE_LAYER_ROLES, doctor, load_registry


class RuleGovernanceRegistryTests(unittest.TestCase):
    @staticmethod
    def _passing_runtime_probes() -> dict[str, dict[str, object]]:
        return {
            "all": {
                "name": "all",
                "ok": True,
                "rules": [
                    "platform.precedence",
                    "workspace.instructions",
                    "workspace.bridge_subtree.instructions",
                    "workflow.task_contract",
                    "workflow.route_plan",
                    "workflow.execution_decision",
                    "tool.mcp_priority",
                    "external.online_access",
                    "foreign.nested_agents",
                    "workflow.execution_economy",
                ],
            }
        }

    def test_passing_fixed_probes_remove_repeat_runtime_advisories(self) -> None:
        with patch.object(rule_governance, "runtime_enforcement_probes", return_value=self._passing_runtime_probes()):
            payload = doctor(full=True)

        self.assertFalse(any(item.get("code") == "rule_not_runtime_enforced" for item in payload["issues"]))
        self.assertFalse(any(item.get("code") == "runtime_enforcement_probe_failed" for item in payload["issues"]))
        self.assertEqual(payload["runtime_enforcement"]["failed_probe_count"], 0)

    def test_failed_fixed_probe_remains_actionable_risk(self) -> None:
        probes = self._passing_runtime_probes()
        probes["all"] = {**probes["all"], "ok": False, "detail": "validator failed"}
        with patch.object(rule_governance, "runtime_enforcement_probes", return_value=probes):
            payload = doctor(full=True)

        failed = [item for item in payload["issues"] if item.get("code") == "runtime_enforcement_probe_failed"]
        self.assertEqual(len(failed), 10)
        self.assertTrue(all(item.get("probe", {}).get("detail") == "validator failed" for item in failed))

    def test_foreign_scope_probe_requires_registered_foreign_contract(self) -> None:
        registry = copy.deepcopy(load_registry())
        registry["surfaces"] = [item for item in registry["surfaces"] if item.get("rule_id") != "foreign.nested_agents"]
        with patch.object(rule_governance, "_run_fixed_probe", return_value={"ok": True, "rules": []}):
            probes = rule_governance.runtime_enforcement_probes(registry, discovered=[])

        self.assertFalse(probes["foreign_scope"]["ok"])

    def test_runtime_probe_allowlist_does_not_derive_commands_from_registry(self) -> None:
        registry = load_registry()
        with patch.object(rule_governance, "_run_fixed_probe") as run_probe:
            rule_governance.runtime_enforcement_probes(registry, discovered=[])

        invoked = {call.args[1]["script"] for call in run_probe.call_args_list}
        self.assertEqual(invoked, {spec["script"] for spec in rule_governance.RUNTIME_ENFORCEMENT_PROBES.values()})

    def test_mcp_execution_priority_is_a_priority_rule_consumer(self) -> None:
        payload = rule_governance.impact(["_bridge/mcp_execution_priority.py"])

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["rule_change_required"])
        self.assertTrue(
            any(
                item.get("rule_id") == "tool.mcp_priority"
                and item.get("match_kind") == "enforcement_consumer"
                for item in payload["affected"]
            )
        )

    def test_windows_ignores_inherited_wsl_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            native_home = Path(temp_dir) / ".codex"
            with patch.dict(os.environ, {"CODEX_HOME": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\.codex-app"}), patch.object(
                rule_governance.sys, "platform", "win32"
            ), patch.object(Path, "home", return_value=Path(temp_dir)):
                resolved = rule_governance.resolve_codex_home()

        self.assertEqual(native_home, resolved)

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

    def test_rule_governance_sources_are_registered_for_real_closeout_impact(self) -> None:
        payload = rule_governance.impact(
            [
                "workspace/_bridge/rule_governance.py",
                "workspace/_bridge/rule_governance_tests.py",
                "workspace/_bridge/policies/rule_authority_registry.json",
            ]
        )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["rule_change_required"])
        self.assertFalse(payload["unmatched"])
        self.assertTrue(all(item.get("rule_id") == "workflow.rule_governance" for item in payload["affected"]))


if __name__ == "__main__":
    unittest.main()
