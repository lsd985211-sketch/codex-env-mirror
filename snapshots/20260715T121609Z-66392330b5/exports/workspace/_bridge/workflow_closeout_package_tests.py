#!/usr/bin/env python3
"""Regression tests for concrete closeout review-card evidence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

from workflow_closeout_package import build_closeout_package, build_pending_disposition, build_review_summary  # noqa: E402
from codex_workflow_entry import closeout_cli_projection, compact_closeout, proposal_items  # noqa: E402


class WorkflowCloseoutPackageTests(unittest.TestCase):
    def test_iteration_candidates_are_added_to_single_review_queue(self) -> None:
        pending = build_pending_disposition(
            notes=[],
            proposals=[],
            profile_candidate_count=0,
            external_candidate_count=0,
            fallback_tools=[],
            negative_items=[],
            unverified_items=[],
            iteration_candidates=[{
                "candidate_id": "iteration:0123456789abcdef01234567",
                "source_item_id": "iteration:0123456789abcdef01234567",
                "title": "Verified stable conclusion",
                "summary": "The iteration layer must remain read-only before approval.",
                "proposed_destination_namespace": "memory.project_conclusions",
            }],
        )
        self.assertEqual([item["kind"] for item in pending["items"]], ["iteration_candidates"])
        self.assertTrue(pending["items"][0]["approval_required_for_write"])
        review_item = pending["items"][0]["review_items"][0]
        self.assertEqual(review_item["candidate_id"], "iteration:0123456789abcdef01234567")
        self.assertEqual(review_item["target_namespace"], "memory.project_conclusions")

    def test_work_notes_and_proposals_include_concrete_items(self) -> None:
        pending = build_pending_disposition(
            notes=[{
                "id": "wn-1",
                "scope": "tool-routing",
                "text": "Concrete OfficeCLI evaluation details.",
                "created_at": "2026-07-11T00:00:00+08:00",
                "reason": "user requested draft",
            }],
            proposals=[{"type": "skill", "title": "Revise skill", "detail": "Change the trigger contract."}],
            profile_candidate_count=0,
            external_candidate_count=0,
            fallback_tools=[],
            negative_items=[],
            unverified_items=[],
        )
        items = {item["kind"]: item for item in pending["items"]}
        self.assertEqual(items["work_notes"]["review_items"][0]["source_item_id"], "wn-1")
        self.assertIn("Concrete OfficeCLI", items["work_notes"]["review_items"][0]["summary"])
        self.assertEqual(items["proposals"]["review_items"][0]["title"], "Revise skill")

    def test_draft_review_proposal_references_artifact_without_copying_it(self) -> None:
        proposals = proposal_items([
            "draft_review|Review OfficeCLI draft|Decide whether to start an isolated pilot.|_bridge/shared/drafts/officecli-evaluation-draft-20260711.md"
        ])
        pending = build_pending_disposition(
            notes=[],
            proposals=proposals,
            profile_candidate_count=0,
            external_candidate_count=0,
            fallback_tools=[],
            negative_items=[],
            unverified_items=[],
        )
        item = pending["items"][0]["review_items"][0]
        self.assertEqual(item["path"], "_bridge/shared/drafts/officecli-evaluation-draft-20260711.md")
        self.assertEqual(item["attributes"]["content_maturity"], "draft")
        self.assertEqual(item["attributes"]["workflow_status"], "pending_review")
        self.assertNotIn("Current recommendation", item["summary"])

    def test_successful_fallback_is_evidence_not_pending_approval(self) -> None:
        pending = build_pending_disposition(
            notes=[],
            proposals=[],
            profile_candidate_count=0,
            external_candidate_count=0,
            fallback_tools=["codegraph:native_to_hub"],
            negative_items=[{"profile": "codegraph", "status": "transport_closed"}],
            unverified_items=[],
        )
        self.assertNotIn("tool_evidence", [item["kind"] for item in pending["items"]])

    def test_unverified_tool_item_preserves_specific_detail(self) -> None:
        pending = build_pending_disposition(
            notes=[],
            proposals=[],
            profile_candidate_count=0,
            external_candidate_count=0,
            fallback_tools=[],
            negative_items=[],
            unverified_items=[{"key": "github-owner", "detail": "Repository owner route was not verified."}],
        )
        item = pending["items"][0]
        self.assertEqual(item["kind"], "tool_evidence")
        self.assertIn("Repository owner route", item["review_items"][0]["summary"])

    def test_self_update_item_covered_by_work_note_is_not_duplicated(self) -> None:
        pending = build_pending_disposition(
            notes=[{"id": "wn-1", "scope": "draft", "text": "Actual pending note."}],
            proposals=[],
            profile_candidate_count=0,
            external_candidate_count=0,
            fallback_tools=[],
            negative_items=[],
            unverified_items=[],
            self_update_signals=[{
                "surface": "memory",
                "code": "memory_governance_not_ok",
                "severity": "warn",
                "review_items": [{
                    "source_item_id": "memory:work-notes",
                    "title": "Pending work notes",
                    "summary": "One note exists.",
                    "covered_by": "work_notes",
                }],
            }],
        )
        self.assertEqual([item["kind"] for item in pending["items"]], ["work_notes"])

    def test_missing_review_items_is_explicitly_incomplete(self) -> None:
        package = {
            "pending_disposition": {
                "items": [{
                    "kind": "future_owner",
                    "count": 2,
                    "action": "review",
                    "approval_required_for_write": True,
                }]
            }
        }
        summary = build_review_summary(package)
        self.assertFalse(summary["detail_complete"])
        self.assertEqual(summary["incomplete_count"], 1)
        self.assertIn("supplied no concrete review_items", summary["cards"][0]["digest"])

    def test_compact_closeout_preserves_empty_queue_contract(self) -> None:
        compact = compact_closeout({
            "ok": True,
            "generated_at": "2026-07-12T00:00:00+08:00",
            "task_kind": "simple",
            "status": {"outcome": "ok"},
            "used": {},
            "tool_evidence": {},
            "validation": {},
            "finalization": {},
        })
        self.assertEqual(compact["pending_disposition"]["pending_count"], 0)
        self.assertTrue(compact["final_reply_must_show"]["detail_complete"])
        self.assertEqual(compact["final_reply_must_show"]["total_review_cards"], 0)

    def test_failed_finalization_prevents_completion_claim(self) -> None:
        package = build_closeout_package({
            "record_path": "",
            "task_kind": "workflow",
            "outcome": "ok",
            "used": {"slash_templates": []},
            "skill_usage": {},
            "tool_evidence": {},
            "work_notes": {},
            "memory_routing": {},
            "profile_candidates": {"ok": True, "candidate_count": 0, "candidates": []},
            "external_candidates": {"ok": True, "selected_count": 0, "would_write": []},
            "self_update_governance": {},
            "validation": {},
            "notes": [],
            "proposals": [],
            "fallback_tools": [],
            "negative_items": [],
            "unverified_items": [],
            "finalization": {"ok": False, "signals": {}, "blocked_reason": "membership_incomplete"},
        })

        self.assertFalse(package["ok"])
        self.assertFalse(package["status"]["main_task_complete"])

    def test_compact_projection_preserves_membership_blocker(self) -> None:
        compact = closeout_cli_projection({
            "schema": "codex_workflow_entry.closeout.v2",
            "ok": False,
            "status": {"outcome": "ok", "main_task_complete": False},
            "finalization": {
                "ok": False,
                "blocked_reason": "system_membership_reconciliation_incomplete",
                "membership_reconciliation": {
                    "required": True,
                    "complete": False,
                    "reason": "membership_evidence_incomplete",
                    "changed_files": ["_bridge/task_route_contract.py"],
                    "affected_systems": ["workflow"],
                    "required_receipt": "system_membership=ok",
                    "receipt_ok": True,
                    "required_next_commands": ["python _bridge\\system_membership.py validate"],
                    "impact": {
                        "ok": False,
                        "coverage_complete": False,
                        "unmapped_system_changed": ["_bridge/new_workflow_tests.py"],
                        "blockers": [
                            {
                                "code": "system_change_partially_unmapped",
                                "message": "One or more system-level changed files have no membership impact rule.",
                                "paths": ["_bridge/new_workflow_tests.py"],
                                "safe_next_step": "register each owning prefix in IMPACT_RULES, then rerun impact",
                            }
                        ],
                    },
                },
                "rule_reconciliation": {
                    "required": False,
                    "complete": False,
                    "reason": "rule_governance_evidence_incomplete",
                    "changed_files": ["_bridge/rule_governance.py"],
                    "unmatched": ["_bridge/rule_governance.py"],
                },
            },
        })

        self.assertEqual(
            compact["finalization"]["blocked_reason"],
            "system_membership_reconciliation_incomplete",
        )
        self.assertEqual(
            compact["finalization"]["membership_reconciliation"]["required_receipt"],
            "system_membership=ok",
        )
        evidence = compact["finalization"]["membership_reconciliation"]["impact_evidence"]
        self.assertEqual(evidence["unmapped_system_changed"], ["_bridge/new_workflow_tests.py"])
        self.assertEqual(
            evidence["blockers"][0]["safe_next_step"],
            "register each owning prefix in IMPACT_RULES, then rerun impact",
        )
        self.assertEqual(
            compact["finalization"]["rule_reconciliation"]["unmatched"],
            ["_bridge/rule_governance.py"],
        )


if __name__ == "__main__":
    unittest.main()
