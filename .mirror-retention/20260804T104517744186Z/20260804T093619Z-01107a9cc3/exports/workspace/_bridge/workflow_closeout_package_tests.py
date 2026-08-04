#!/usr/bin/env python3
"""Regression tests for concrete closeout review-card evidence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

from workflow_closeout_package import build_closeout_package, build_pending_disposition, build_review_summary  # noqa: E402
from codex_workflow_entry import (  # noqa: E402
    closeout,
    closeout_cli_projection,
    compact_closeout,
    finalization_section_summary,
    proposal_items,
    should_compact_closeout,
)


class WorkflowCloseoutPackageTests(unittest.TestCase):
    @staticmethod
    def business_outcome() -> dict:
        return {
            "category": "software_engineering",
            "accepted": True,
            "consumed": True,
            "result_ref": "owner-result:private",
            "evidence_refs": ["owner-evidence:private"],
            "task_segment": {
                "task_segment_class": "authorized_repository_change",
                "active_execution_ms": 100,
                "tool_wait_ms": 20,
                "user_wait_ms": 30,
                "idle_gap_ms": 0,
                "rework_ms": 0,
                "governance_tax_ms": 10,
                "approval_round_trip_count": 0,
                "clarification_round_trip_count": 0,
                "first_pass": True,
                "measurement_confidence": "high",
                "timeline_evidence_ref": "owner-timeline:private",
            },
        }

    def test_unsaved_closeout_previews_business_outcome_without_recording(self) -> None:
        with patch("codex_workflow_entry.record_business_outcomes", side_effect=AssertionError("unsaved closeout recorded observer event")):
            package = closeout(
                task_kind="workflow",
                outcome="ok",
                business_outcomes=[self.business_outcome()],
                business_task_ref="private-task",
                save=False,
            )
        observation = package["business_outcome_observation"]
        self.assertTrue(observation["ok"], observation)
        self.assertEqual("preview", observation["mode"])
        self.assertFalse(observation["writes_observer_events"])
        self.assertNotIn("private-task", str(observation))
        self.assertNotIn("owner-timeline:private", str(observation))

    def test_saved_closeout_records_explicit_business_outcome_once(self) -> None:
        recorded = {
            "schema": "workflow_observation_iteration.record_business_outcomes",
            "ok": True,
            "observation_count": 1,
            "event_paths": ["/runtime/redacted-event.json"],
            "writes_observer_events": True,
            "writes_business_state": False,
            "writes_review_queue": False,
        }
        with (
            patch("codex_workflow_entry.record_business_outcomes", return_value=recorded) as record,
            patch("codex_workflow_entry.sync_review_groups", return_value=[]),
            patch("codex_workflow_entry.save_closeout") as save_closeout,
            patch("skill_orchestrator.record_usage", return_value={"ok": True}),
        ):
            package = closeout(
                task_kind="workflow",
                outcome="ok",
                business_outcomes=[self.business_outcome()],
                business_task_ref="stable-task-ref",
                save=True,
            )
        record.assert_called_once_with([self.business_outcome()], task_ref="stable-task-ref")
        save_closeout.assert_called_once()
        observation = package["business_outcome_observation"]
        self.assertEqual("recorded", observation["mode"])
        self.assertEqual(1, observation["observation_count"])

    def test_saved_closeout_rejects_invalid_business_outcome_without_recording(self) -> None:
        with (
            patch("codex_workflow_entry.record_business_outcomes", side_effect=AssertionError("invalid outcome recorded observer event")),
            patch("codex_workflow_entry.sync_review_groups", return_value=[]),
            patch("codex_workflow_entry.save_closeout") as save_closeout,
            patch("skill_orchestrator.record_usage", return_value={"ok": True}),
        ):
            package = closeout(
                task_kind="workflow",
                outcome="ok",
                business_outcomes=[{"category": "unknown"}],
                business_task_ref="stable-task-ref",
                save=True,
            )
        save_closeout.assert_called_once()
        observation = package["business_outcome_observation"]
        self.assertFalse(observation["ok"], observation)
        self.assertEqual("preview_rejected", observation["mode"])
        self.assertFalse(observation["writes_observer_events"])

    def test_unsaved_closeout_only_previews_efficiency_cycles(self) -> None:
        preview = {
            "schema": "workflow_observation_iteration.preview_efficiency_cycles",
            "ok": True,
            "event_count": 1,
            "events": [{"cycle_type": "validation_replay", "occurrence_count": 2}],
            "writes_observer_events": False,
            "writes_review_queue": False,
        }
        with (
            patch("codex_workflow_entry.resolve_rollout_path", return_value={"ok": True, "rollout_path": "/private/rollout.jsonl"}),
            patch("codex_workflow_entry.preview_efficiency_cycles", return_value=preview) as project,
            patch("codex_workflow_entry.record_efficiency_cycles", side_effect=AssertionError("preview wrote observer state")),
        ):
            package = closeout(
                task_kind="workflow",
                outcome="ok",
                delivery_thread_id="thread-private",
                finalization_project_id="task-private",
                save=False,
            )
        project.assert_called_once()
        observation = package["efficiency_cycle_observation"]
        self.assertEqual("preview", observation["mode"])
        self.assertFalse(observation["writes_observer_events"])

    def test_saved_closeout_records_efficiency_cycles_once(self) -> None:
        preview = {
            "schema": "workflow_observation_iteration.preview_efficiency_cycles",
            "ok": True,
            "event_count": 1,
            "events": [{"cycle_type": "validation_replay", "occurrence_count": 2}],
            "writes_observer_events": False,
            "writes_review_queue": False,
        }
        recorded = {
            "schema": "workflow_observation_iteration.record_efficiency_cycles",
            "ok": True,
            "event_count": 1,
            "event_paths": ["/runtime/redacted-cycle.json"],
            "writes_observer_events": True,
            "writes_review_queue": False,
        }
        with (
            patch("codex_workflow_entry.resolve_rollout_path", return_value={"ok": True, "rollout_path": "/private/rollout.jsonl"}),
            patch("codex_workflow_entry.reconcile_prepared_deliveries", return_value={"ok": True, "prepared_count": 0, "delivered_count": 0, "results": []}),
            patch("codex_workflow_entry.preview_efficiency_cycles", return_value=preview),
            patch("codex_workflow_entry.record_efficiency_cycles", return_value=recorded) as record,
            patch("codex_workflow_entry.sync_review_groups", return_value=[]),
            patch("codex_workflow_entry.save_closeout"),
            patch("skill_orchestrator.record_usage", return_value={"ok": True}),
        ):
            package = closeout(
                task_kind="workflow",
                outcome="ok",
                delivery_thread_id="thread-private",
                finalization_project_id="task-private",
                save=True,
            )
        record.assert_called_once()
        self.assertEqual("recorded", package["efficiency_cycle_observation"]["mode"])

    def test_closeout_package_stores_bounded_terminal_projection(self) -> None:
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
            "finalization": {"ok": True, "signals": {}},
            "terminal_convergence": {
                "convergence_id": "c1",
                "intent_id": "i1",
                "terminal_source_signature": "sig",
                "current_phase": "read_only_acceptance",
                "next_action": {"action_id": "mirror.verify", "decision": "verify"},
                "completed_action_ids": ["mirror.publish"],
                "receipt_refs": ["receipt:mirror"],
                "owner_raw_receipts": [{"secret": "must-not-copy"}],
            },
        })
        stored = package["terminal_convergence"]
        self.assertEqual(stored["terminal_source_signature"], "sig")
        self.assertEqual(stored["receipt_refs"], ["receipt:mirror"])
        self.assertNotIn("owner_raw_receipts", stored)
        self.assertFalse(stored["automatic_loop_allowed"])
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

    def test_existing_pending_queue_prevents_success_compaction(self) -> None:
        package = {
            "status": {"outcome": "ok"},
            "tool_evidence": {},
            "work_notes": {"active_count": 0},
            "proposals": [],
            "user_profile_candidates": {"candidate_count": 0},
            "external_knowledge_candidates": {"selected_count": 0},
            "self_update_governance": {"signals": []},
            "finalization": {"signals": {}},
            "pending_disposition": {
                "pending_count": 1,
                "items": [{"kind": "iteration_candidates", "review_items": [{"title": "Pending"}]}],
            },
        }

        self.assertFalse(should_compact_closeout(package))

    def test_review_summary_forbids_count_only_delivery(self) -> None:
        package = {
            "pending_disposition": {
                "items": [{
                    "kind": "iteration_candidates",
                    "review_items": [
                        {"source_item_id": f"item-{index}", "title": f"Decision {index}", "summary": "evidence"}
                        for index in range(12)
                    ],
                }]
            }
        }
        summary = build_review_summary(package, limit=10)
        self.assertEqual(summary["shown_count"], 10)
        self.assertEqual(summary["hidden_count"], 2)
        self.assertTrue(summary["display_contract"]["generic_count_only_forbidden"])
        self.assertEqual(summary["display_contract"]["maximum_cards_per_reply"], 10)

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

    def test_checkpoint_summary_preserves_logical_and_workspace_paths(self) -> None:
        summary = finalization_section_summary({
            "ok": True,
            "result": {
                "ok": True,
                "checkpoint": {
                    "checkpoint_id": "checkpoint-test",
                    "project_id": "audio-hardware-system",
                    "title": "Test",
                    "logical_ref": "checkpoints/audio-hardware-system/test.md",
                    "workspace_relative_path": "_bridge/shared/checkpoints/audio-hardware-system/test.md",
                    "workspace_path": "C:\\workspace\\_bridge\\shared\\checkpoints\\audio-hardware-system\\test.md",
                },
            },
        })

        checkpoint = summary["checkpoint"]
        self.assertNotIn("path", checkpoint)
        self.assertIn("_bridge/shared/checkpoints", checkpoint["workspace_relative_path"])
        self.assertTrue(checkpoint["workspace_path"].startswith("C:\\workspace"))

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
