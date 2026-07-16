#!/usr/bin/env python3
"""Regression tests for closeout-time membership and rule reconciliation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import workflow_finalization as finalization


class WorkflowMembershipFinalizationTests(unittest.TestCase):
    def test_major_change_requires_changed_files(self) -> None:
        result = finalization.finalize(task_kind="workflow", outcome="ok", major_change=True)

        self.assertFalse(result["ok"])
        self.assertIn("changed_files_required_for_major_change", result["blocked_reason"])
        self.assertFalse(result["project_checkpoint"]["applied"])

    def test_architecture_change_requires_membership_receipt(self) -> None:
        with patch.object(
            finalization,
            "membership_impact",
            return_value={
                "ok": True,
                "contract_upgrade_required": True,
                "affected_systems": ["workflow"],
                "affected_surfaces": ["workflow_route"],
                "required_next_commands": ["python _bridge\\system_membership.py validate"],
            },
        ), patch.object(finalization, "membership_validate", return_value={"ok": True}):
            result = finalization.finalize(
                task_kind="workflow",
                outcome="ok",
                changed_files=["_bridge/workflow_orchestrator.py"],
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["membership_reconciliation"]["required"])
        self.assertFalse(result["membership_reconciliation"]["receipt_ok"])

    def test_membership_receipt_completes_reconciliation(self) -> None:
        with patch.object(
            finalization,
            "membership_impact",
            return_value={
                "ok": True,
                "contract_upgrade_required": True,
                "affected_systems": ["workflow"],
                "affected_surfaces": ["workflow_route"],
                "required_next_commands": [],
            },
        ), patch.object(finalization, "membership_validate", return_value={"ok": True}):
            result = finalization.finalize(
                task_kind="workflow",
                outcome="ok",
                changed_files=["_bridge/workflow_orchestrator.py"],
                validation_receipts=["system_membership=ok", "rule_governance=ok"],
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["membership_reconciliation"]["complete"])

    def test_rule_change_requires_rule_governance_receipt(self) -> None:
        with patch.object(
            finalization,
            "membership_impact",
            return_value={"ok": True, "contract_upgrade_required": False},
        ), patch.object(
            finalization,
            "rule_impact",
            return_value={
                "ok": True,
                "rule_change_required": True,
                "affected": [{"rule_id": "platform.precedence"}],
                "unmatched": [],
            },
        ), patch.object(finalization, "rule_validate", return_value={"ok": True}):
            result = finalization.finalize(
                task_kind="workflow",
                outcome="ok",
                changed_files=["C:/Users/45543/.codex/AGENTS.md"],
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["rule_reconciliation"]["required"])
        self.assertFalse(result["rule_reconciliation"]["receipt_ok"])

    def test_rule_receipt_completes_reconciliation(self) -> None:
        with patch.object(
            finalization,
            "membership_impact",
            return_value={"ok": True, "contract_upgrade_required": False},
        ), patch.object(
            finalization,
            "rule_impact",
            return_value={"ok": True, "rule_change_required": True, "affected": [], "unmatched": []},
        ), patch.object(finalization, "rule_validate", return_value={"ok": True}):
            result = finalization.finalize(
                task_kind="workflow",
                outcome="ok",
                changed_files=["C:/Users/45543/.codex/AGENTS.md"],
                validation_receipts=["rule_governance=ok"],
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["rule_reconciliation"]["complete"])

    def test_non_architecture_change_is_not_blocked(self) -> None:
        with patch.object(
            finalization,
            "membership_impact",
            return_value={
                "ok": True,
                "contract_upgrade_required": False,
                "affected_systems": [],
                "affected_surfaces": [],
                "required_next_commands": [],
            },
        ):
            result = finalization.finalize(
                task_kind="docs",
                outcome="ok",
                changed_files=["README.md"],
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["membership_reconciliation"]["required"])

    def test_non_rule_change_does_not_report_rule_evidence_incomplete(self) -> None:
        with patch.object(
            finalization,
            "membership_impact",
            return_value={"ok": True, "contract_upgrade_required": False},
        ), patch.object(
            finalization,
            "rule_impact",
            return_value={
                "ok": False,
                "rule_change_required": False,
                "affected": [],
                "unmatched": ["_bridge/codex_workflow_entry.py"],
            },
        ):
            result = finalization.finalize(
                task_kind="workflow",
                outcome="ok",
                changed_files=["_bridge/codex_workflow_entry.py"],
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["rule_reconciliation"]["required"])
        self.assertTrue(result["rule_reconciliation"]["complete"])
        self.assertEqual(result["rule_reconciliation"]["reason"], "complete")


if __name__ == "__main__":
    unittest.main()
