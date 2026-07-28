#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from scoped_authorization import grant_from_thread, parse_time

from workflow_review_queue import (
    authoritative_scopes_from_validation_receipts,
    decision_apply,
    decision_plan,
    decision_readback,
    dispose,
    get_review_item,
    mark_delivery,
    prepare_delivery_envelope,
    prepare_delivery_packages,
    snapshot,
    sync_review_groups,
    transition,
)


class WorkflowReviewQueueTests(unittest.TestCase):
    @staticmethod
    def authorization_grant(planned: dict, root: Path) -> str:
        challenge = planned["authorization_challenge"]
        issued = parse_time(challenge["issued_at"])
        rollout = root / "rollout-thread-test.jsonl"
        event = {
            "timestamp": (issued + timedelta(seconds=1)).isoformat(),
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": challenge["response_token"]}],
            },
        }
        rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-test"}}) + "\n" + json.dumps(event) + "\n", encoding="utf-8")
        grant = grant_from_thread(
            challenge["challenge_ref"],
            rollout_path=rollout,
            state_root=root / "authorization",
            checked_at=issued + timedelta(seconds=2),
        )
        if not grant.get("ok"):
            raise AssertionError(grant)
        return str(grant["grant_ref"])

    @staticmethod
    def seed_delivery_items(db_path: Path, count: int = 6) -> None:
        sync_review_groups(
            [{
                "kind": "iteration_candidates",
                "review_items": [
                    {
                        "candidate_id": f"iteration:{index:024x}",
                        "source_item_id": f"iteration:{index:024x}",
                        "title": f"Candidate {index}",
                        "summary": f"Decision {index}",
                        "approval_action": "approve|reject|revise|defer",
                        "attributes": {"owner": "memory_governance", "signal_kind": "prevention_guard"},
                    }
                    for index in range(1, count + 1)
                ],
            }],
            db_path=db_path,
        )

    def test_delivery_package_and_envelope_track_revisions_without_claiming_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            self.seed_delivery_items(db_path)
            packages = prepare_delivery_packages(db_path=db_path)
            self.assertTrue(packages["ok"], packages)
            self.assertEqual(packages["package_count"], 1)
            package = packages["packages"][0]
            envelope = prepare_delivery_envelope(package["package_id"], db_path=db_path)
            self.assertTrue(envelope["ok"], envelope)
            self.assertEqual(envelope["status"], "prepared")
            self.assertEqual(len(envelope["item_refs"]), 6)
            missing = mark_delivery(envelope["envelope_id"], response_ref="", db_path=db_path)
            self.assertFalse(missing["ok"])
            delivered = mark_delivery(envelope["envelope_id"], response_ref="thread:turn:response", db_path=db_path)
            self.assertTrue(delivered["ok"], delivered)
            self.assertEqual(delivered["status"], "delivered")

    def test_batch_decision_is_revision_guarded_atomic_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            self.seed_delivery_items(db_path)
            package = prepare_delivery_packages(db_path=db_path)["packages"][0]
            envelope = prepare_delivery_envelope(package["package_id"], db_path=db_path)
            planned = decision_plan(package["package_id"], "approve", envelope_id=envelope["envelope_id"], db_path=db_path, thread_id="thread-test", authorization_state_root=Path(temp_dir) / "authorization")
            self.assertTrue(planned["ok"], planned)
            grant_ref = self.authorization_grant(planned, Path(temp_dir))
            applied = decision_apply(planned["confirm_token"], authorization_grant_ref=grant_ref, db_path=db_path, authorization_state_root=Path(temp_dir) / "authorization")
            self.assertTrue(applied["ok"], applied)
            self.assertEqual(len(applied["results"]), 6)
            self.assertTrue(all(item["status"] == "approved" for item in applied["results"]))
            self.assertTrue(applied["lifecycle_handoff"]["required"])
            self.assertIn("consume-approved", applied["lifecycle_handoff"]["command"])
            self.assertFalse(applied["lifecycle_complete"])
            readback = decision_readback(planned["decision_id"], db_path=db_path)
            self.assertEqual(readback["status"], "applied")
            self.assertEqual(readback["result"]["decision_id"], planned["decision_id"])
            self.assertFalse(readback["lifecycle_complete"])
            self.assertIn("consume-approved", readback["required_next_action"])

    def test_stale_package_cannot_apply_to_new_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            self.seed_delivery_items(db_path, count=1)
            package = prepare_delivery_packages(db_path=db_path, package_size=5)["packages"][0]
            planned = decision_plan(package["package_id"], "approve", db_path=db_path, thread_id="thread-test", authorization_state_root=Path(temp_dir) / "authorization")
            review_id = package["item_refs"][0]["review_id"]
            changed_group = [{"kind": "iteration_candidates", "review_items": [{
                "candidate_id": review_id,
                "source_item_id": review_id,
                "title": "Changed candidate",
                "summary": "New decision material",
            }]}]
            transition(review_id, "rejected", db_path=db_path)
            sync_review_groups(changed_group, db_path=db_path)
            grant_ref = self.authorization_grant(planned, Path(temp_dir))
            applied = decision_apply(planned["confirm_token"], authorization_grant_ref=grant_ref, db_path=db_path, authorization_state_root=Path(temp_dir) / "authorization")
            self.assertFalse(applied["ok"])
            self.assertEqual(applied["reason"], "decision_atomic_revision_conflict")

    def test_nonempty_legacy_user_evidence_ref_cannot_authorize_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            self.seed_delivery_items(db_path, count=1)
            package = prepare_delivery_packages(db_path=db_path, package_size=5)["packages"][0]
            planned = decision_plan(package["package_id"], "approve", db_path=db_path, thread_id="thread-test", authorization_state_root=root / "authorization")
            applied = decision_apply(planned["confirm_token"], user_evidence_ref="user-message:any-string", db_path=db_path, authorization_state_root=root / "authorization")
            self.assertFalse(applied["ok"])
            self.assertEqual("scoped_authorization_grant_required", applied["reason"])
            self.assertFalse(applied["legacy_user_evidence_ref_accepted"])

    def test_packages_do_not_mix_memory_candidates_with_technical_dispositions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            self.seed_delivery_items(db_path, count=6)
            sync_review_groups(
                [{"kind": "tool_evidence", "review_items": [
                    {"source_item_id": "tool:one", "title": "Technical evidence"}
                ]}],
                db_path=db_path,
            )
            packages = prepare_delivery_packages(db_path=db_path)
            self.assertEqual(packages["package_count"], 2)
            kinds = [{card["kind"] for card in package["cards"]} for package in packages["packages"]]
            self.assertIn({"iteration_candidates"}, kinds)
            self.assertIn({"tool_evidence"}, kinds)
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

    def test_successful_owner_receipt_resolves_absent_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            sync_review_groups(
                [{
                    "kind": "self_update_governance",
                    "review_items": [{
                        "source_item_id": "self_update:wsl_workspace_owner:host_projection_stale",
                        "title": "Host projection stale",
                    }],
                }],
                db_path=db_path,
            )

            pending = sync_review_groups(
                [],
                db_path=db_path,
                authoritative_scopes=authoritative_scopes_from_validation_receipts(
                    ["wsl_workspace_owner=ok", "workflow=/non-authoritative/path"]
                ),
            )

            self.assertEqual(pending, [])
            self.assertEqual(snapshot(db_path=db_path)["counts"]["resolved"], 1)


if __name__ == "__main__":
    unittest.main()
