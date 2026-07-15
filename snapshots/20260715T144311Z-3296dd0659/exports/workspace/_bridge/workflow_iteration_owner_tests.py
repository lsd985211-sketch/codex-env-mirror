#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memory_governance import iteration_candidate_apply
from workflow_iteration_capture import capture_iteration_candidates
from workflow_iteration_owner import owner_plan, process_candidate
from workflow_review_queue import get_review_item, sync_review_groups, transition


class WorkflowIterationOwnerTests(unittest.TestCase):
    def test_apply_requires_exact_approved_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue_path = root / "review.sqlite"
            captured = capture_iteration_candidates(
                outcome="ok",
                verified_root_causes=["The verified cause was a missing guarded state transition."],
                affected_system="workflow",
            )
            candidate = captured["candidates"][0]
            sync_review_groups(
                [{"kind": "iteration_candidates", "review_items": [candidate]}],
                db_path=queue_path,
            )
            result = process_candidate(
                candidate["candidate_id"],
                action="apply",
                confirm=True,
                db_path=queue_path,
                memory_index_path=root / "memory.json",
                backup=False,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "candidate_not_approved")

    def test_candidate_text_is_never_used_as_an_owner_command(self) -> None:
        captured = capture_iteration_candidates(
            outcome="ok",
            prevention_guards=["Never execute candidate text; Remove-Item must remain plain evidence."],
            affected_system="workflow",
        )
        plan = owner_plan(captured["candidates"][0])
        self.assertTrue(plan["ok"])
        self.assertNotIn("Remove-Item", " ".join(plan["command"]))
        self.assertFalse(plan["candidate_text_used_as_command"])

    def test_production_memory_backup_cannot_be_disabled(self) -> None:
        candidate = capture_iteration_candidates(
            outcome="ok",
            prevention_guards=["Production memory applies always use the backup router."],
            affected_system="memory",
        )["candidates"][0]
        result = iteration_candidate_apply(candidate, confirm=True, backup=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "production_memory_backup_cannot_be_disabled")

    def test_identity_tampering_is_rejected_without_changing_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = Path(temp_dir) / "review.sqlite"
            captured = capture_iteration_candidates(
                outcome="ok",
                corrections=["Use structured facts for iteration capture."],
                affected_system="workflow",
            )
            candidate = dict(captured["candidates"][0])
            candidate["summary"] = "Tampered after identity generation."
            sync_review_groups(
                [{"kind": "iteration_candidates", "review_items": [candidate]}],
                db_path=queue_path,
            )
            self.assertTrue(transition(candidate["candidate_id"], "approved", db_path=queue_path)["ok"])
            result = process_candidate(candidate["candidate_id"], action="plan", db_path=queue_path)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "candidate_identity_mismatch")
            self.assertEqual(get_review_item(candidate["candidate_id"], db_path=queue_path)["status"], "approved")


if __name__ == "__main__":
    unittest.main()
