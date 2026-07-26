#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workflow_review_queue import dispose, get_review_item, snapshot, sync_review_groups, transition


class WorkflowReviewQueueTests(unittest.TestCase):
    def test_iteration_candidate_uses_stable_candidate_id_as_queue_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            candidate_id = "iteration:0123456789abcdef01234567"
            pending = sync_review_groups(
                [{
                    "kind": "iteration_candidates",
                    "review_items": [{
                        "candidate_id": candidate_id,
                        "source_item_id": candidate_id,
                        "title": "Verified conclusion",
                        "summary": "Use owner-routed application.",
                    }],
                }],
                db_path=db_path,
            )
            self.assertEqual(pending[0]["review_items"][0]["review_queue_id"], candidate_id)

    def test_guarded_iteration_lifecycle_rejects_status_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            candidate_id = "iteration:abcdef0123456789abcdef01"
            sync_review_groups(
                [{
                    "kind": "iteration_candidates",
                    "review_items": [{
                        "candidate_id": candidate_id,
                        "source_item_id": candidate_id,
                        "title": "Verified conclusion",
                    }],
                }],
                db_path=db_path,
            )

            skipped = transition(candidate_id, "applied", db_path=db_path)
            self.assertFalse(skipped["ok"])
            self.assertEqual(skipped["reason"], "invalid_status_transition")

            for status in ("approved", "applied", "validated", "resolved"):
                result = transition(candidate_id, status, db_path=db_path)
                self.assertTrue(result["ok"], result)
            self.assertEqual(get_review_item(candidate_id, db_path=db_path)["status"], "resolved")

    def test_disposed_item_does_not_repeat_until_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            group = {
                "kind": "proposals",
                "action": "review",
                "review_items": [
                    {
                        "source_item_id": "proposal:test:stable",
                        "title": "Stable proposal",
                        "summary": "First revision",
                        "approval_action": "approve|revise|reject",
                    }
                ],
            }
            pending = sync_review_groups([group], db_path=db_path)
            review_id = pending[0]["review_items"][0]["review_queue_id"]
            self.assertTrue(dispose(review_id, "deferred", note="keep as draft", db_path=db_path)["ok"])
            self.assertEqual(sync_review_groups([group], db_path=db_path), [])

            changed = dict(group)
            changed["review_items"] = [dict(group["review_items"][0], summary="Second revision")]
            reopened = sync_review_groups([changed], db_path=db_path)
            self.assertEqual(len(reopened), 1)
            snap = snapshot(db_path=db_path)
            self.assertEqual(snap["pending"][0]["revision"], 2)

    def test_empty_closeout_still_surfaces_existing_pending_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            sync_review_groups(
                [{"kind": "memory", "review_items": [{"source_item_id": "memory:1", "title": "Memory candidate"}]}],
                db_path=db_path,
            )
            pending = sync_review_groups([], db_path=db_path)
            self.assertEqual(pending[0]["kind"], "memory")
            self.assertEqual(pending[0]["count"], 1)

    def test_fresh_authoritative_owner_resolves_missing_items_only_in_its_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            sync_review_groups(
                [
                    {
                        "kind": "self_update_governance",
                        "review_items": [
                            {
                                "source_item_id": "self_update:resource_process:fanout",
                                "title": "Process fanout",
                            },
                            {
                                "source_item_id": "self_update:memory:stale",
                                "title": "Memory stale",
                            },
                        ],
                    }
                ],
                db_path=db_path,
            )

            pending = sync_review_groups(
                [],
                db_path=db_path,
                authoritative_scopes=[
                    {
                        "kind": "self_update_governance",
                        "source_item_prefix": "self_update:resource_process:",
                    }
                ],
            )

            self.assertEqual(len(pending), 1)
            self.assertEqual(
                pending[0]["review_items"][0]["source_item_id"],
                "self_update:memory:stale",
            )
            self.assertEqual(snapshot(db_path=db_path)["counts"]["resolved"], 1)

    def test_auto_resolved_owner_issue_reopens_when_it_reappears(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            group = {
                "kind": "self_update_governance",
                "review_items": [
                    {
                        "source_item_id": "self_update:resource_process:fanout",
                        "title": "Process fanout",
                    }
                ],
            }
            sync_review_groups([group], db_path=db_path)
            sync_review_groups(
                [],
                db_path=db_path,
                authoritative_scopes=[
                    {
                        "kind": "self_update_governance",
                        "source_item_prefix": "self_update:resource_process:",
                    }
                ],
            )

            reopened = sync_review_groups([group], db_path=db_path)
            self.assertEqual(len(reopened), 1)
            self.assertEqual(snapshot(db_path=db_path)["pending"][0]["revision"], 2)


if __name__ == "__main__":
    unittest.main()
