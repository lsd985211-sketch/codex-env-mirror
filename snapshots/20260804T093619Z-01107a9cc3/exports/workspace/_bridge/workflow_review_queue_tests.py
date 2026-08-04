#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from scoped_authorization import cancel_challenge, grant_from_thread, parse_time
from workflow_iteration_capture import capture_iteration_candidates

from workflow_review_queue import (
    authoritative_scopes_from_validation_receipts,
    connect,
    consume_preissued_authorization,
    decision_apply,
    decision_plan,
    decision_readback,
    dispose_exact,
    dispose,
    get_review_item,
    reconcile_implemented_reviews,
    mark_delivery,
    prepare_delivery_envelope,
    prepare_delivery_packages,
    preview_review_groups,
    snapshot,
    sync_review_groups,
    transition,
    verify_delivery,
)


class WorkflowReviewQueueTests(unittest.TestCase):
    def test_preview_review_groups_marks_changed_same_id_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            sync_review_groups(
                [{"kind": "tool_evidence", "review_items": [{
                    "source_item_id": "tool-negative:same-id",
                    "title": "旧标题",
                    "summary": "已持久化内容。",
                }]}],
                db_path=db_path,
            )
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()

            preview = preview_review_groups(
                [{"kind": "tool_evidence", "review_items": [{
                    "source_item_id": "tool-negative:same-id",
                    "title": "新标题",
                    "summary": "仅存在于本次只读计算中的内容。",
                }]}],
                db_path=db_path,
            )

            self.assertEqual(before, hashlib.sha256(db_path.read_bytes()).hexdigest())
            item = preview[0]["review_items"][0]
            self.assertEqual(item["title"], "新标题")
            self.assertEqual(item["review_queue_revision"], 0)
            self.assertEqual(item["review_queue_status"], "preview_changed")
            self.assertEqual(item["persisted_revision"], 1)
            persisted = get_review_item(item["review_queue_id"], db_path=db_path)
            self.assertEqual(persisted["item"]["title"], "旧标题")
            self.assertEqual(persisted["revision"], 1)

    def test_dispose_exact_is_atomic_guarded_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            groups = [{"kind": "tool_evidence", "review_items": [
                {"source_item_id": "tool-negative:target", "title": "目标卡", "summary": "待精确清理。"},
                {"source_item_id": "tool-negative:other", "title": "其它卡", "summary": "不得受影响。"},
            ]}]
            synced = sync_review_groups(groups, db_path=db_path)
            refs = {item["source_item_id"]: item for item in synced[0]["review_items"]}
            target = refs["tool-negative:target"]
            other = refs["tool-negative:other"]
            note = "test_fixture_cleanup: read-only closeout regression"

            mismatch = dispose_exact(
                target["review_queue_id"], expected_revision=2, expected_status="pending",
                disposition="discarded", note=note, db_path=db_path,
            )
            self.assertFalse(mismatch["ok"])
            self.assertEqual(mismatch["reason"], "exact_disposition_revision_mismatch")
            self.assertEqual(get_review_item(target["review_queue_id"], db_path=db_path)["status"], "pending")

            applied = dispose_exact(
                target["review_queue_id"], expected_revision=1, expected_status="pending",
                disposition="discarded", note=note, db_path=db_path,
            )
            self.assertTrue(applied["ok"])
            self.assertFalse(applied["reused"])
            reused = dispose_exact(
                target["review_queue_id"], expected_revision=1, expected_status="pending",
                disposition="discarded", note=note, db_path=db_path,
            )
            self.assertTrue(reused["ok"])
            self.assertTrue(reused["reused"])
            conflict = dispose_exact(
                target["review_queue_id"], expected_revision=1, expected_status="pending",
                disposition="discarded", note="different note", db_path=db_path,
            )
            self.assertFalse(conflict["ok"])
            self.assertEqual(conflict["reason"], "exact_disposition_status_conflict")
            self.assertEqual(get_review_item(other["review_queue_id"], db_path=db_path)["status"], "pending")

    def test_preview_review_groups_merges_transient_and_persisted_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            sync_review_groups(
                [{"kind": "proposals", "review_items": [{
                    "source_item_id": "proposal:persisted",
                    "title": "既有事项",
                    "summary": "既有待审批事项。",
                }]}],
                db_path=db_path,
            )
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()
            preview = preview_review_groups(
                [{"kind": "iteration_candidates", "review_items": [{
                    "candidate_id": "iteration:abcdef0123456789abcdef09",
                    "source_item_id": "iteration:abcdef0123456789abcdef09",
                    "title": "当前候选",
                    "summary": "当前只读候选。",
                }]}],
                db_path=db_path,
            )
            after = hashlib.sha256(db_path.read_bytes()).hexdigest()

            self.assertEqual(before, after)
            self.assertEqual({group["kind"] for group in preview}, {"proposals", "iteration_candidates"})
            items = [item for group in preview for item in group["review_items"]]
            transient = next(item for item in items if item.get("candidate_id"))
            persisted = next(item for item in items if item.get("source_item_id") == "proposal:persisted")
            self.assertEqual(transient["review_queue_revision"], 0)
            self.assertEqual(transient["review_queue_status"], "preview_only")
            self.assertEqual(persisted["review_queue_revision"], 1)
            self.assertEqual(persisted["review_queue_status"], "pending")
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
                        "summary": f"第 {index} 项审批决定摘要",
                        "approval_action": "approve|reject|revise|defer",
                        "attributes": {"owner": "memory_governance", "signal_kind": "prevention_guard"},
                    }
                    for index in range(1, count + 1)
                ],
            }],
            db_path=db_path,
        )

    @staticmethod
    def write_rollout(
        path: Path,
        *,
        thread_id: str,
        text: str,
        complete: bool = True,
        turn_id: str = "turn-final",
        timestamp: str = "2099-01-01T00:00:00+00:00",
    ) -> None:
        events = [
            {"type": "session_meta", "payload": {"id": thread_id}},
            {
                "timestamp": timestamp,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": "message-final",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
                },
            },
        ]
        if complete:
            events.append({
                "timestamp": timestamp,
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": turn_id, "last_agent_message": text},
            })
        path.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n", encoding="utf-8")

    @staticmethod
    def delivery_text(package: dict, approval: dict | None = None) -> str:
        text = "\n".join(
            f"Review Card\nID: {card['review_id']}\nTitle: {card['title']}\nSummary: {card['summary']}"
            for card in package["cards"]
        )
        return text + (f"\nAuthorization: {approval['response_token']}" if approval else "")

    def test_delivery_package_and_envelope_track_revisions_without_claiming_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            self.seed_delivery_items(db_path)
            packages = prepare_delivery_packages(db_path=db_path)
            self.assertTrue(packages["ok"], packages)
            self.assertEqual(packages["package_count"], 1)
            package = packages["packages"][0]
            envelope = prepare_delivery_envelope(
                package["package_id"], target_ref="thread:thread-test", db_path=db_path,
                authorization_state_root=Path(temp_dir) / "authorization",
            )
            self.assertTrue(envelope["ok"], envelope)
            self.assertEqual(envelope["status"], "prepared")
            self.assertEqual(len(envelope["item_refs"]), 6)
            self.assertEqual(package["cards"][0]["title"], "审批事项：Candidate 1")
            self.assertIn("### 审批卡片 1", envelope["markdown"])
            self.assertIn("- 标识：", envelope["markdown"])
            self.assertIn("- 摘要 SHA-256：", envelope["markdown"])
            self.assertNotIn("### Review Card", envelope["markdown"])
            self.assertIn(package["cards"][0]["review_id"], envelope["markdown"])
            self.assertIn(package["cards"][0]["title"], envelope["markdown"])
            self.assertIn(package["cards"][0]["summary"], envelope["markdown"])
            self.assertEqual("preissued_package_approve", envelope["approval"]["mode"])
            self.assertIn(envelope["approval"]["response_token"], envelope["markdown"])
            missing = mark_delivery(envelope["envelope_id"], response_ref="", db_path=db_path)
            self.assertFalse(missing["ok"])
            legacy = mark_delivery(envelope["envelope_id"], response_ref="thread:turn:response", db_path=db_path)
            self.assertFalse(legacy["ok"])
            missing_token = Path(temp_dir) / "missing-token.jsonl"
            self.write_rollout(missing_token, thread_id="thread-test", text=self.delivery_text(package))
            absent = verify_delivery(
                envelope["envelope_id"], rollout_path=missing_token, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertEqual("final_response_missing_preissued_authorization_evidence", absent["reason"])
            rollout = Path(temp_dir) / "rollout.jsonl"
            self.write_rollout(rollout, thread_id="thread-test", text=self.delivery_text(package, envelope["approval"]))
            delivered = verify_delivery(
                envelope["envelope_id"], rollout_path=rollout, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertTrue(delivered["ok"], delivered)
            self.assertEqual(delivered["status"], "delivered")
            replay = verify_delivery(
                envelope["envelope_id"], rollout_path=rollout, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertTrue(replay["ok"], replay)
            self.assertTrue(replay["reused"])
            same_envelope = prepare_delivery_envelope(
                package["package_id"], target_ref="thread:thread-test", db_path=db_path,
                authorization_state_root=Path(temp_dir) / "authorization",
            )
            self.assertEqual(same_envelope["status"], "delivered")
            self.assertEqual(same_envelope["approval"]["response_token"], envelope["approval"]["response_token"])
            self.assertEqual(same_envelope["approval"]["decision_id"], envelope["approval"]["decision_id"])
            from workflow_review_queue import connect

            conn = connect(db_path)
            try:
                conn.execute(
                    "UPDATE review_deliveries SET status='acknowledged' WHERE envelope_id=?",
                    (envelope["envelope_id"],),
                )
                conn.commit()
            finally:
                conn.close()
            acknowledged = prepare_delivery_envelope(
                package["package_id"], target_ref="thread:thread-test", db_path=db_path,
                authorization_state_root=Path(temp_dir) / "authorization",
            )
            self.assertEqual(acknowledged["status"], "acknowledged")
            self.assertEqual(acknowledged["response_digest"], delivered["response_digest"])

    def test_preissued_token_consumes_one_matching_decision_after_delivery_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            authorization_root = root / "authorization"
            self.seed_delivery_items(db_path, count=2)
            package = prepare_delivery_packages(db_path=db_path)["packages"][0]
            envelope = prepare_delivery_envelope(
                package["package_id"], target_ref="thread:thread-test", db_path=db_path,
                authorization_state_root=authorization_root,
            )
            issued = parse_time(envelope["approval"]["issued_at"])
            rollout = root / "rollout-thread-test.jsonl"
            events = [
                {"type": "session_meta", "payload": {"id": "thread-test"}},
                {"timestamp": (issued + timedelta(seconds=1)).isoformat(), "type": "response_item", "payload": {"type": "message", "id": "assistant-card", "role": "assistant", "content": [{"type": "output_text", "text": envelope["markdown"]}], "internal_chat_message_metadata_passthrough": {"turn_id": "delivery"}}},
                {"timestamp": (issued + timedelta(seconds=1)).isoformat(), "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "delivery", "last_agent_message": envelope["markdown"]}},
                {"timestamp": (issued + timedelta(seconds=2)).isoformat(), "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": envelope["approval"]["response_token"]}]}},
            ]
            rollout.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in events) + "\n", encoding="utf-8")
            consumed = consume_preissued_authorization(
                thread_id="thread-test", rollout_path=rollout, db_path=db_path, authorization_state_root=authorization_root,
                iteration_memory_index_path=root / "memory.json", iteration_backup=False,
            )
            self.assertTrue(consumed["ok"], consumed)
            self.assertEqual(1, consumed["applied_count"])
            self.assertTrue(consumed["result"]["lifecycle_handoff"]["required"])
            readback = decision_readback(envelope["approval"]["decision_id"], db_path=db_path)
            self.assertEqual("applied", readback["status"])

    def test_partial_preissued_plan_fences_the_full_package_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            authorization_root = root / "authorization"
            self.seed_delivery_items(db_path, count=2)
            package = prepare_delivery_packages(db_path=db_path)["packages"][0]
            envelope = prepare_delivery_envelope(
                package["package_id"], target_ref="thread:thread-test", db_path=db_path,
                authorization_state_root=authorization_root,
            )
            subset = package["item_refs"][0]["review_id"]
            partial = decision_plan(
                package["package_id"], "approve", include=[subset], envelope_id=envelope["envelope_id"],
                db_path=db_path, thread_id="thread-test", authorization_state_root=authorization_root, preissued=True,
            )
            self.assertTrue(partial["ok"], partial)
            issued = parse_time(envelope["approval"]["issued_at"])
            rollout = root / "rollout-thread-test.jsonl"
            event = {"timestamp": (issued + timedelta(seconds=1)).isoformat(), "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": envelope["approval"]["response_token"]}]}}
            rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-test"}}) + "\n" + json.dumps(event) + "\n", encoding="utf-8")
            fenced = grant_from_thread(envelope["approval"]["challenge_ref"], rollout_path=rollout, state_root=authorization_root, checked_at=issued + timedelta(seconds=2))
            self.assertFalse(fenced["ok"])
            self.assertEqual("authorization_challenge_cancelled", fenced["reason"])

    def test_delivered_expired_or_cancelled_code_reissues_a_new_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            authorization_root = root / "authorization"
            self.seed_delivery_items(db_path, count=1)
            package = prepare_delivery_packages(db_path=db_path)["packages"][0]
            first = prepare_delivery_envelope(
                package["package_id"], target_ref="thread:thread-test", db_path=db_path,
                authorization_state_root=authorization_root,
            )
            rollout = root / "delivery.jsonl"
            self.write_rollout(rollout, thread_id="thread-test", text=first["markdown"])
            delivered = verify_delivery(first["envelope_id"], rollout_path=rollout, expected_thread_id="thread-test", db_path=db_path)
            self.assertTrue(delivered["ok"], delivered)
            cancelled = cancel_challenge(first["approval"]["challenge_ref"], reason="test stale delivery", state_root=authorization_root)
            self.assertTrue(cancelled["ok"], cancelled)
            refreshed = prepare_delivery_envelope(
                package["package_id"], target_ref="thread:thread-test", db_path=db_path,
                authorization_state_root=authorization_root,
            )
            self.assertNotEqual(first["envelope_id"], refreshed["envelope_id"])
            self.assertNotEqual(first["approval"]["response_token"], refreshed["approval"]["response_token"])

    def test_historical_english_iteration_title_is_projected_without_mutating_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            sync_review_groups(
                [{
                    "kind": "iteration_candidates",
                    "review_items": [{
                        "candidate_id": "iteration:000000000000000000000001",
                        "source_item_id": "iteration:000000000000000000000001",
                        "title": "Verified iteration candidate: verified root cause",
                        "summary": "resource facade previously forwarded raw owner payloads",
                        "approval_action": "approve_then_dispatch_to_exact_owner",
                        "required_checks": ["Verify the conclusion is durable and current"],
                        "attributes": {"owner": "memory_governance", "signal_kind": "verified_root_cause"},
                    }],
                }],
                db_path=db_path,
            )

            package = prepare_delivery_packages(db_path=db_path, package_size=5)["packages"][0]
            card = package["cards"][0]
            self.assertEqual(card["review_id"], "iteration:000000000000000000000001")
            self.assertEqual(
                card["summary"],
                "中文摘要待生产者补充；原始证据：resource facade previously forwarded raw owner payloads",
            )
            self.assertFalse(card["presentation_complete"])
            self.assertEqual(card["title"], "已验证的迭代候选：根因")
            self.assertEqual(card["required_checks"], ["确认结论持久且仍然有效"])

    def test_existing_chinese_title_keeps_its_specific_business_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.sqlite"
            sync_review_groups(
                [{
                    "kind": "iteration_candidates",
                    "review_items": [{
                        "candidate_id": "iteration:000000000000000000000002",
                        "source_item_id": "iteration:000000000000000000000002",
                        "title": "资源层影片实体发现根因复核",
                        "summary": "preserve the exact evidence summary",
                        "attributes": {"owner": "memory_governance", "signal_kind": "verified_root_cause"},
                    }],
                }],
                db_path=db_path,
            )

            card = prepare_delivery_packages(db_path=db_path, package_size=5)["packages"][0]["cards"][0]
            self.assertEqual(card["title"], "资源层影片实体发现根因复核")
            self.assertEqual(
                card["summary"],
                "中文摘要待生产者补充；原始证据：preserve the exact evidence summary",
            )
            self.assertFalse(card["presentation_complete"])

    def test_incomplete_chinese_projection_cannot_issue_approval_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            sync_review_groups(
                [{
                    "kind": "proposals",
                    "review_items": [{
                        "source_item_id": "proposal:unmapped",
                        "title": "Unmapped proposal",
                        "summary": "Unmapped proposal detail",
                        "approval_action": "approve|revise|reject",
                    }],
                }],
                db_path=db_path,
            )

            package = prepare_delivery_packages(db_path=db_path)["packages"][0]
            result = prepare_delivery_envelope(
                package["package_id"],
                target_ref="thread:thread-test",
                db_path=db_path,
                authorization_state_root=root / "authorization",
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "approval_presentation_incomplete")
            self.assertEqual(result["incomplete_cards"][0]["review_id"], "proposals:source_item_id:proposal:unmapped")
            self.assertFalse((root / "authorization" / "challenges").exists())

    def test_delivery_rejects_missing_card_wrong_thread_and_nonfinal_echo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            self.seed_delivery_items(db_path, count=2)
            package = prepare_delivery_packages(db_path=db_path)["packages"][0]
            envelope = prepare_delivery_envelope(
                package["package_id"], target_ref="thread:thread-test", db_path=db_path,
                authorization_state_root=root / "authorization",
            )

            missing_card = root / "missing.jsonl"
            partial = dict(package)
            partial["cards"] = package["cards"][:1]
            self.write_rollout(missing_card, thread_id="thread-test", text=self.delivery_text(partial))
            result = verify_delivery(
                envelope["envelope_id"], rollout_path=missing_card, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertEqual(result["reason"], "final_response_missing_review_card_evidence")

            bullets = root / "bullets.jsonl"
            self.write_rollout(bullets, thread_id="thread-test", text="Two review cards were shown and approved.")
            result = verify_delivery(
                envelope["envelope_id"], rollout_path=bullets, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertEqual(result["reason"], "final_response_missing_review_card_evidence")

            wrong = root / "wrong.jsonl"
            self.write_rollout(wrong, thread_id="other-thread", text=self.delivery_text(package, envelope["approval"]))
            result = verify_delivery(
                envelope["envelope_id"], rollout_path=wrong, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertEqual(result["reason"], "delivery_rollout_thread_mismatch")

            unbound = prepare_delivery_envelope(package["package_id"], db_path=db_path)
            result = verify_delivery(
                unbound["envelope_id"], rollout_path=missing_card, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertEqual(result["reason"], "delivery_envelope_thread_unbound")

            echo = root / "echo.jsonl"
            self.write_rollout(echo, thread_id="thread-test", text=self.delivery_text(package, envelope["approval"]), complete=False)
            result = verify_delivery(
                envelope["envelope_id"], rollout_path=echo, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertEqual(result["reason"], "final_response_missing_review_card_evidence")

            early = root / "early.jsonl"
            self.write_rollout(
                early,
                thread_id="thread-test",
                text=self.delivery_text(package, envelope["approval"]),
                timestamp="2000-01-01T00:00:00+00:00",
            )
            result = verify_delivery(
                envelope["envelope_id"], rollout_path=early, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertEqual(result["reason"], "final_response_missing_review_card_evidence")

    def test_delivered_envelope_rejects_a_different_response_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            self.seed_delivery_items(db_path, count=1)
            package = prepare_delivery_packages(db_path=db_path)["packages"][0]
            envelope = prepare_delivery_envelope(
                package["package_id"], target_ref="thread:thread-test", db_path=db_path,
                authorization_state_root=root / "authorization",
            )
            first = root / "first.jsonl"
            self.write_rollout(first, thread_id="thread-test", text=self.delivery_text(package, envelope["approval"]))
            delivered = verify_delivery(
                envelope["envelope_id"], rollout_path=first, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertTrue(delivered["ok"], delivered)

            second = root / "second.jsonl"
            self.write_rollout(
                second,
                thread_id="thread-test",
                text=self.delivery_text(package, envelope["approval"]) + "\nDifferent persisted final response.",
                turn_id="turn-other",
            )
            conflict = verify_delivery(
                envelope["envelope_id"], rollout_path=second, expected_thread_id="thread-test", db_path=db_path
            )
            self.assertEqual(conflict["reason"], "delivered_response_binding_conflict")

    def test_batch_decision_is_revision_guarded_atomic_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            self.seed_delivery_items(db_path)
            package = prepare_delivery_packages(db_path=db_path)["packages"][0]
            envelope = prepare_delivery_envelope(package["package_id"], db_path=db_path)
            planned = decision_plan(package["package_id"], "approve", envelope_id=envelope["envelope_id"], db_path=db_path, thread_id="thread-test", authorization_state_root=root / "authorization")
            self.assertTrue(planned["ok"], planned)
            grant_ref = self.authorization_grant(planned, root)
            applied = decision_apply(
                planned["confirm_token"], authorization_grant_ref=grant_ref, db_path=db_path,
                authorization_state_root=root / "authorization", iteration_memory_index_path=root / "memory.json",
                iteration_backup=False,
            )
            self.assertTrue(applied["ok"], applied)
            self.assertEqual(len(applied["results"]), 6)
            self.assertTrue(all(item["status"] == "approved" for item in applied["results"]))
            self.assertTrue(applied["lifecycle_handoff"]["required"])
            self.assertFalse(applied["lifecycle_handoff"]["automatic_execution"]["completed"])
            self.assertFalse(applied["lifecycle_complete"])
            readback = decision_readback(planned["decision_id"], db_path=db_path)
            self.assertEqual(readback["status"], "applied")
            self.assertEqual(readback["result"]["decision_id"], planned["decision_id"])
            self.assertFalse(readback["lifecycle_complete"])
            self.assertIn("consume-approved", readback["required_next_action"])
            conn = connect(db_path)
            try:
                delivery = conn.execute(
                    "SELECT status,response_digest FROM review_deliveries WHERE envelope_id=?",
                    (envelope["envelope_id"],),
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(delivery["status"], "prepared")
            self.assertEqual(delivery["response_digest"], "")

    def test_approved_verified_iteration_candidate_is_consumed_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            candidate = capture_iteration_candidates(
                outcome="ok",
                prevention_guards=["批准后必须由既有 owner 完成低风险、可验证的生命周期。"],
                affected_system="workflow",
            )["candidates"][0]
            sync_review_groups([{"kind": "iteration_candidates", "review_items": [candidate]}], db_path=db_path)
            package = prepare_delivery_packages(db_path=db_path, package_size=5)["packages"][0]
            planned = decision_plan(
                package["package_id"], "approve", db_path=db_path, thread_id="thread-test",
                authorization_state_root=root / "authorization",
            )
            grant_ref = self.authorization_grant(planned, root)

            applied = decision_apply(
                planned["confirm_token"], authorization_grant_ref=grant_ref, db_path=db_path,
                authorization_state_root=root / "authorization", iteration_memory_index_path=root / "memory.json",
                iteration_backup=False,
            )

            self.assertTrue(applied["ok"], applied)
            self.assertTrue(applied["lifecycle_complete"])
            self.assertTrue(applied["lifecycle_handoff"]["automatic_execution"]["completed"])
            self.assertEqual("resolved", get_review_item(candidate["candidate_id"], db_path=db_path)["status"])

            recovered = decision_apply(
                planned["confirm_token"], authorization_grant_ref="already-consumed-grant", db_path=db_path,
                authorization_state_root=root / "authorization", iteration_memory_index_path=root / "memory.json",
                iteration_backup=False,
            )

            self.assertTrue(recovered["ok"], recovered)
            self.assertTrue(recovered["lifecycle_complete"])
            self.assertEqual(1, len(json.loads((root / "memory.json").read_text(encoding="utf-8"))["iteration_candidates"]))

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

    def test_implementation_reconciliation_requires_exact_receipt_head_and_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            review_id = "iteration:abcdef0123456789abcdef02"
            sync_review_groups(
                [{"kind": "iteration_candidates", "review_items": [{
                    "candidate_id": review_id,
                    "source_item_id": review_id,
                    "title": "已验证候选",
                    "summary": "已由实现提交覆盖的候选。",
                }]}],
                db_path=db_path,
            )
            commit = "a" * 40
            source_head = "b" * 40
            receipt = root / "integrate.json"
            receipt.write_text(json.dumps({
                "schema": "work_git_change_owner.integrate.v1",
                "ok": True,
                "status": "completed",
                "after": {"head": commit},
                "sync": {"ok": True, "remote_commit": commit},
            }), encoding="utf-8")
            receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()

            wrong_revision = reconcile_implemented_reviews(
                [{"review_id": review_id, "revision": 2}],
                implementation_receipt=receipt,
                implementation_receipt_sha256=receipt_sha,
                implementation_commit=commit,
                source_head=source_head,
                db_path=db_path,
                current_head_reader=lambda: source_head,
                ancestor_check=lambda _commit, _head: True,
            )
            self.assertFalse(wrong_revision["ok"])
            self.assertEqual(wrong_revision["reason"], "implementation_review_revision_mismatch")
            self.assertEqual(get_review_item(review_id, db_path=db_path)["status"], "pending")

            reconciled = reconcile_implemented_reviews(
                [{"review_id": review_id, "revision": 1}],
                implementation_receipt=receipt,
                implementation_receipt_sha256=receipt_sha,
                implementation_commit=commit,
                source_head=source_head,
                db_path=db_path,
                current_head_reader=lambda: source_head,
                ancestor_check=lambda _commit, _head: True,
            )
            self.assertTrue(reconciled["ok"], reconciled)
            self.assertEqual(reconciled["resolved_count"], 1)
            self.assertEqual(get_review_item(review_id, db_path=db_path)["status"], "resolved")

            repeated = reconcile_implemented_reviews(
                [{"review_id": review_id, "revision": 1}],
                implementation_receipt=receipt,
                implementation_receipt_sha256=receipt_sha,
                implementation_commit=commit,
                source_head=source_head,
                db_path=db_path,
                current_head_reader=lambda: source_head,
                ancestor_check=lambda _commit, _head: True,
            )
            self.assertTrue(repeated["ok"], repeated)
            self.assertTrue(repeated["reused"])

    def test_implementation_reconciliation_is_atomic_for_mixed_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "review.sqlite"
            ids = ["iteration:abcdef0123456789abcdef03", "iteration:abcdef0123456789abcdef04"]
            sync_review_groups(
                [{"kind": "iteration_candidates", "review_items": [
                    {"candidate_id": review_id, "source_item_id": review_id, "title": "已验证候选", "summary": "已实现事项。"}
                    for review_id in ids
                ]}],
                db_path=db_path,
            )
            commit = "c" * 40
            source_head = "d" * 40
            receipt = root / "integrate.json"
            receipt.write_text(json.dumps({
                "schema": "work_git_change_owner.integrate.v1", "ok": True, "status": "completed",
                "after": {"head": commit}, "sync": {"ok": True, "remote_commit": commit},
            }), encoding="utf-8")
            result = reconcile_implemented_reviews(
                [{"review_id": ids[0], "revision": 1}, {"review_id": ids[1], "revision": 2}],
                implementation_receipt=receipt,
                implementation_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
                implementation_commit=commit,
                source_head=source_head,
                db_path=db_path,
                current_head_reader=lambda: source_head,
                ancestor_check=lambda _commit, _head: True,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(all(get_review_item(review_id, db_path=db_path)["status"] == "pending" for review_id in ids))

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
