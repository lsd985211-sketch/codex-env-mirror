#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workflow_review_backlog import CONFIRM, plan, reconcile
from workflow_review_queue import snapshot, sync_review_groups


class WorkflowReviewBacklogTests(unittest.TestCase):
    def test_reconcile_discards_only_exact_duplicates_and_regression_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            sync_review_groups(
                [{
                    "kind": "iteration_candidates",
                    "review_items": [
                        {
                            "candidate_id": "iteration:000000000000000000000001",
                            "summary": "Keep one authority.",
                            "stable_conclusion": "Keep one authority.",
                            "target_namespace": "memory.project_conclusions",
                            "affected_system": "workflow",
                            "attributes": {"owner": "memory_governance", "signal_kind": "verified_root_cause"},
                        },
                        {
                            "candidate_id": "iteration:000000000000000000000002",
                            "summary": "Keep one authority.",
                            "stable_conclusion": "Keep one authority.",
                            "target_namespace": "memory.project_conclusions",
                            "affected_system": "workflow",
                            "attributes": {"owner": "memory_governance", "signal_kind": "prevention_guard"},
                        },
                        {
                            "candidate_id": "iteration:000000000000000000000003",
                            "summary": "workflow tests 12/12 passed",
                            "stable_conclusion": "workflow tests 12/12 passed",
                            "target_namespace": "memory.project_conclusions",
                            "affected_system": "workflow",
                            "attributes": {"owner": "memory_governance", "signal_kind": "regression_test"},
                        },
                        {
                            "candidate_id": "iteration:000000000000000000000004",
                            "summary": "Preserve user state.",
                            "stable_conclusion": "Preserve user state.",
                            "target_namespace": "memory.project_conclusions",
                            "affected_system": "workflow",
                            "attributes": {"owner": "memory_governance", "signal_kind": "prevention_guard"},
                        },
                    ],
                }],
                db_path=db_path,
            )

            planned = plan(db_path=db_path)
            self.assertEqual(4, planned["pending_count"])
            self.assertEqual(2, planned["machine_disposition_count"])
            self.assertEqual(2, planned["approval_required_count"])

            result = reconcile(confirm=CONFIRM, db_path=db_path)
            self.assertTrue(result["ok"], result)
            self.assertEqual(2, result["operation"]["discarded_count"])
            self.assertEqual({"pending": 2, "discarded": 2}, snapshot(db_path=db_path)["counts"])

    def test_reconcile_requires_exact_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = reconcile(confirm="", db_path=Path(temp_dir) / "review.sqlite")
            self.assertFalse(result["ok"])
            self.assertEqual(CONFIRM, result["confirmation"])

    def test_same_destination_with_different_text_remains_for_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            sync_review_groups(
                [{
                    "kind": "iteration_candidates",
                    "review_items": [
                        {
                            "candidate_id": "iteration:000000000000000000000011",
                            "summary": "Route plugin installation through its owner.",
                            "stable_conclusion": "Preserve one plugin authority.",
                            "target_namespace": "memory.project_conclusions",
                            "affected_system": "workflow",
                            "attributes": {"owner": "memory_governance", "signal_kind": "prevention_guard"},
                        },
                        {
                            "candidate_id": "iteration:000000000000000000000012",
                            "summary": "Require a backup before plugin installation.",
                            "stable_conclusion": "Preserve one plugin authority.",
                            "target_namespace": "memory.project_conclusions",
                            "affected_system": "workflow",
                            "attributes": {"owner": "memory_governance", "signal_kind": "prevention_guard"},
                        },
                    ],
                }],
                db_path=db_path,
            )
            planned = plan(db_path=db_path)
            self.assertEqual(0, planned["machine_disposition_count"])
            self.assertEqual(2, planned["approval_required_count"])

    def test_validation_placeholders_are_removed_and_every_row_is_accounted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            sync_review_groups(
                [{
                    "kind": "tool_evidence",
                    "review_items": [
                        {"source_item_id": "tool-unverified:unverified-1", "title": "placeholder"},
                        {"source_item_id": "tool-negative:negative-1:1", "title": "placeholder"},
                        {"source_item_id": "tool-negative:node_repl:1", "title": "real unresolved evidence"},
                    ],
                }],
                db_path=db_path,
            )
            planned = plan(db_path=db_path)
            self.assertEqual(planned["deterministic_noise_count"], 2)
            self.assertEqual(planned["approval_required_count"], 1)
            result = reconcile(confirm=CONFIRM, db_path=db_path)
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["accounting"]["complete"])
            self.assertEqual(result["packages"]["package_count"], 1)


if __name__ == "__main__":
    unittest.main()
