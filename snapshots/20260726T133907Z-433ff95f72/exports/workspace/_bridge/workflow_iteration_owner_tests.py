#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_governance import iteration_candidate_apply, iteration_candidates_apply
from workflow_iteration_capture import capture_iteration_candidates
from workflow_iteration_owner import owner_plan, process_candidate, process_candidates
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

    def test_batch_apply_validate_resolve_preserves_guarded_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue_path = root / "review.sqlite"
            memory_path = root / "memory.json"
            candidates = capture_iteration_candidates(
                outcome="ok",
                verified_root_causes=["Cause one is verified.", "Cause two is verified."],
                affected_system="workflow",
            )["candidates"]
            sync_review_groups(
                [{"kind": "iteration_candidates", "review_items": candidates}],
                db_path=queue_path,
            )
            ids = [candidate["candidate_id"] for candidate in candidates]
            for candidate_id in ids:
                self.assertTrue(transition(candidate_id, "approved", db_path=queue_path)["ok"])

            applied = process_candidates(
                ids,
                action="apply",
                confirm=True,
                db_path=queue_path,
                memory_index_path=memory_path,
                backup=False,
            )
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["owner_result"]["applied_count"], 2)
            self.assertTrue(process_candidates(ids, action="validate", db_path=queue_path, memory_index_path=memory_path)["ok"])
            self.assertTrue(process_candidates(ids, action="resolve", db_path=queue_path, memory_index_path=memory_path)["ok"])
            self.assertTrue(all(get_review_item(value, db_path=queue_path)["status"] == "resolved" for value in ids))
            payload = json.loads(memory_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["iteration_candidates"]), 2)

    def test_batch_apply_uses_one_backup_for_existing_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            memory_path.write_text('{"schema":"memory_absorption_index.v1","iteration_candidates":[]}', encoding="utf-8")
            candidates = capture_iteration_candidates(
                outcome="ok",
                prevention_guards=["Guard one is verified.", "Guard two is verified."],
                affected_system="memory",
            )["candidates"]
            with patch("_bridge.memory_iteration_owner.create_backup", return_value={"ok": True}) as backup:
                result = iteration_candidates_apply(candidates, confirm=True, index_path=memory_path)

            self.assertTrue(result["ok"])
            self.assertEqual(result["applied_count"], 2)
            backup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
