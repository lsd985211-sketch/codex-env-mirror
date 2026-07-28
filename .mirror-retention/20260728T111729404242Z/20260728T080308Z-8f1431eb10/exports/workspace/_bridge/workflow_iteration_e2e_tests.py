#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workflow_iteration_capture import capture_iteration_candidates
from workflow_iteration_owner import process_candidate, recall_candidate
from workflow_closeout_package import build_pending_disposition
from workflow_review_queue import sync_review_groups, transition


class WorkflowIterationEndToEndTests(unittest.TestCase):
    def test_capture_queue_approval_owner_apply_recall_and_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue_path = root / "review.sqlite"
            memory_index = root / "memory_absorption_index.json"
            checkpoint = {
                "checkpoint": {
                    "checkpoint_id": "checkpoint-e2e",
                    "project_id": "mcsmanager",
                    "summary": "Verified iteration capture flow.",
                    "stable_conclusions": [
                        "Structured checkpoint conclusions enter review before owner-routed persistence."
                    ],
                    "path": "checkpoints/mcsmanager/e2e.md",
                }
            }
            captured = capture_iteration_candidates(
                outcome="ok",
                major_change=True,
                checkpoint=checkpoint,
                affected_system="workflow",
            )
            candidate = captured["candidates"][0]
            groups = build_pending_disposition(
                notes=[],
                proposals=[],
                profile_candidate_count=0,
                external_candidate_count=0,
                fallback_tools=[],
                negative_items=[],
                unverified_items=[],
                iteration_candidates=captured["candidates"],
            )["items"]
            first = sync_review_groups(groups, db_path=queue_path)
            replay = sync_review_groups(groups, db_path=queue_path)
            self.assertEqual(first[0]["review_items"][0]["review_queue_id"], candidate["candidate_id"])
            self.assertEqual(replay[0]["count"], 1)

            self.assertTrue(transition(candidate["candidate_id"], "approved", db_path=queue_path)["ok"])
            dry_run = process_candidate(
                candidate["candidate_id"],
                action="apply",
                confirm=False,
                db_path=queue_path,
                memory_index_path=memory_index,
                backup=False,
            )
            self.assertTrue(dry_run["ok"])
            self.assertTrue(dry_run["dry_run"])
            self.assertFalse(memory_index.exists())

            applied = process_candidate(
                candidate["candidate_id"],
                action="apply",
                confirm=True,
                db_path=queue_path,
                memory_index_path=memory_index,
                backup=False,
            )
            self.assertTrue(applied["ok"], applied)
            validated = process_candidate(
                candidate["candidate_id"],
                action="validate",
                db_path=queue_path,
                memory_index_path=memory_index,
            )
            self.assertTrue(validated["ok"], validated)
            recalled = recall_candidate(candidate["candidate_id"], memory_index_path=memory_index)
            self.assertTrue(recalled["ok"])
            resolved = process_candidate(candidate["candidate_id"], action="resolve", db_path=queue_path)
            self.assertTrue(resolved["ok"], resolved)


if __name__ == "__main__":
    unittest.main()
