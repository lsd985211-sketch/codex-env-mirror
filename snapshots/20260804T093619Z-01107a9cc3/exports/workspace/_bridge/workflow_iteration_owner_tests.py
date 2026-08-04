#!/usr/bin/env python3
from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from memory_governance import iteration_candidate_apply, iteration_candidates_apply, iteration_candidates_invalidate, iteration_candidate_recall
from scoped_authorization import grant_from_thread, parse_time
from workflow_iteration_capture import capture_iteration_candidates
from workflow_iteration_owner import command_contract, consume_approved, owner_plan, process_candidate, process_candidates
from workflow_review_queue import (
    decision_apply,
    decision_plan,
    get_review_item,
    prepare_delivery_packages,
    sync_review_groups,
    transition,
)


def _apply_memory_candidate_concurrently(
    candidate: dict[str, object],
    memory_path: str,
    start_event: multiprocessing.synchronize.Event,
    result_queue: multiprocessing.queues.Queue,
) -> None:
    start_event.wait()
    result_queue.put(
        iteration_candidates_apply(
            [candidate],
            confirm=True,
            index_path=Path(memory_path),
            backup=False,
        )
    )


class WorkflowIterationOwnerTests(unittest.TestCase):
    @staticmethod
    def authorization_grant(planned: dict, root: Path) -> str:
        challenge = planned["authorization_challenge"]
        issued = parse_time(challenge["issued_at"])
        rollout = root / "rollout-thread-test.jsonl"
        event = {
            "timestamp": (issued + timedelta(seconds=1)).isoformat(),
            "type": "response_item",
            "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": challenge["response_token"]}],
            },
        }
        rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-test"}}) + "\n" + json.dumps(event) + "\n", encoding="utf-8")
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=rollout, state_root=root / "authorization", checked_at=issued + timedelta(seconds=2))
        if not grant.get("ok"):
            raise AssertionError(grant)
        return str(grant["grant_ref"])

    def test_command_contract_is_parser_derived_and_marks_validate_mutating(self) -> None:
        contract = command_contract()

        self.assertTrue(contract["parser_signature"])
        self.assertIn("consume-approved", contract["actions"])
        self.assertIn("validate", contract["mutating_actions"])
        self.assertNotIn("validate", contract["read_only_actions"])

    def test_decision_consume_runs_complete_owner_lifecycle_in_one_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue_path = root / "review.sqlite"
            memory_path = root / "memory.json"
            candidates = capture_iteration_candidates(
                outcome="ok",
                prevention_guards=["已批准事项必须由单一 owner 生命周期完整消费。"],
                affected_system="workflow",
            )["candidates"]
            sync_review_groups(
                [{"kind": "iteration_candidates", "review_items": candidates}],
                db_path=queue_path,
            )
            package = prepare_delivery_packages(db_path=queue_path, package_size=5)["packages"][0]
            planned = decision_plan(package["package_id"], "approve", db_path=queue_path, thread_id="thread-test", authorization_state_root=root / "authorization")
            grant_ref = self.authorization_grant(planned, root)
            applied = decision_apply(
                planned["confirm_token"],
                authorization_grant_ref=grant_ref,
                db_path=queue_path,
                authorization_state_root=root / "authorization",
                iteration_memory_index_path=memory_path,
                iteration_backup=False,
            )

            self.assertTrue(applied["ok"], applied)
            self.assertTrue(applied["lifecycle_complete"])
            self.assertTrue(applied["lifecycle_result"]["ok"])
            self.assertEqual(applied["lifecycle_result"]["consumed_count"], 1)
            self.assertEqual(get_review_item(candidates[0]["candidate_id"], db_path=queue_path)["status"], "resolved")

    def test_consume_resumes_applied_candidate_when_owner_readback_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue_path = root / "review.sqlite"
            memory_path = root / "memory.json"
            candidate = capture_iteration_candidates(
                outcome="ok",
                verified_root_causes=["丢失的 owner 回读必须通过受控生命周期恢复。"],
                affected_system="workflow",
            )["candidates"][0]
            sync_review_groups(
                [{"kind": "iteration_candidates", "review_items": [candidate]}],
                db_path=queue_path,
            )
            self.assertTrue(transition(candidate["candidate_id"], "approved", db_path=queue_path)["ok"])
            self.assertTrue(
                process_candidates(
                    [candidate["candidate_id"]],
                    action="apply",
                    confirm=True,
                    db_path=queue_path,
                    memory_index_path=memory_path,
                    backup=False,
                )["ok"]
            )
            memory_path.write_text(
                '{"schema":"memory_absorption_index.v1","iteration_candidates":[]}',
                encoding="utf-8",
            )

            result = consume_approved(
                review_ids=[candidate["candidate_id"]],
                confirm=True,
                db_path=queue_path,
                memory_index_path=memory_path,
                backup=False,
            )

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["lifecycle_complete"])
            self.assertEqual(get_review_item(candidate["candidate_id"], db_path=queue_path)["status"], "resolved")
            payload = json.loads(memory_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["candidate_id"] for item in payload["iteration_candidates"]],
                [candidate["candidate_id"]],
            )

    def test_non_iteration_decision_does_not_consume_unrelated_approved_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue_path = root / "review.sqlite"
            candidate = capture_iteration_candidates(
                outcome="ok",
                prevention_guards=["只有决策交接可以选择已批准的记忆事项。"],
                affected_system="workflow",
            )["candidates"][0]
            sync_review_groups(
                [
                    {"kind": "iteration_candidates", "review_items": [candidate]},
                    {
                        "kind": "tool_evidence",
                        "review_items": [{
                            "source_item_id": "technical-only",
                            "title": "Technical evidence",
                            "summary": "This decision does not approve a memory candidate.",
                        }],
                    },
                ],
                db_path=queue_path,
            )
            self.assertTrue(transition(candidate["candidate_id"], "approved", db_path=queue_path)["ok"])
            technical_package = next(
                package
                for package in prepare_delivery_packages(db_path=queue_path, package_size=5)["packages"]
                if package["cards"][0]["kind"] == "tool_evidence"
            )
            planned = decision_plan(technical_package["package_id"], "approve", db_path=queue_path, thread_id="thread-test", authorization_state_root=root / "authorization")
            grant_ref = self.authorization_grant(planned, root)
            applied = decision_apply(
                planned["confirm_token"],
                authorization_grant_ref=grant_ref,
                db_path=queue_path,
                authorization_state_root=root / "authorization",
            )

            result = consume_approved(
                decision_id=applied["decision_id"],
                confirm=True,
                db_path=queue_path,
                memory_index_path=root / "memory.json",
                backup=False,
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["review_count"], 0)
            self.assertEqual(
                get_review_item(candidate["candidate_id"], db_path=queue_path)["status"],
                "approved",
            )

    def test_apply_requires_exact_approved_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue_path = root / "review.sqlite"
            captured = capture_iteration_candidates(
                outcome="ok",
                verified_root_causes=["已验证根因是缺少受控状态转换。"],
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
            prevention_guards=["不得执行候选文本；Remove-Item 必须保持为普通证据。"],
            affected_system="workflow",
        )
        plan = owner_plan(captured["candidates"][0])
        self.assertTrue(plan["ok"])
        self.assertNotIn("Remove-Item", " ".join(plan["command"]))
        self.assertFalse(plan["candidate_text_used_as_command"])

    def test_production_memory_backup_cannot_be_disabled(self) -> None:
        candidate = capture_iteration_candidates(
            outcome="ok",
            prevention_guards=["生产记忆写入始终使用备份路由。"],
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
                corrections=["迭代捕获应使用结构化事实。"],
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
                verified_root_causes=["根因一已验证。", "根因二已验证。"],
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
                prevention_guards=["防护一已验证。", "防护二已验证。"],
                affected_system="memory",
            )["candidates"]
            with patch("_bridge.memory_iteration_owner.create_backup", return_value={"ok": True}) as backup:
                result = iteration_candidates_apply(candidates, confirm=True, index_path=memory_path)

            self.assertTrue(result["ok"])
            self.assertEqual(result["applied_count"], 2)
            backup.assert_called_once()

    def test_exact_invalidation_preserves_audit_and_blocks_recall(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            candidate = capture_iteration_candidates(outcome="ok", verified_root_causes=["错误授权的事实。"], affected_system="workflow")["candidates"][0]
            self.assertTrue(iteration_candidate_apply(candidate, confirm=True, index_path=memory_path, backup=False)["ok"])
            result = iteration_candidates_invalidate([candidate["candidate_id"]], reason="authorization scope was invalid", evidence_ref="review-decision:test", confirm=True, index_path=memory_path, backup=False)
            self.assertTrue(result["ok"], result)
            recalled = iteration_candidate_recall(candidate["candidate_id"], index_path=memory_path)
            self.assertFalse(recalled["ok"])
            self.assertEqual("candidate_authorization_invalidated", recalled["reason"])
            self.assertEqual("authorization_invalidated", recalled["item"]["status"])
            self.assertEqual("approved_applied", recalled["item"]["previous_status"])

    def test_concurrent_memory_applies_preserve_every_candidate(self) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("cross-process memory transaction regression requires fork")
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            memory_path.write_text(
                '{"schema":"memory_absorption_index.v1","iteration_candidates":[]}',
                encoding="utf-8",
            )
            candidates = capture_iteration_candidates(
                outcome="ok",
                verified_root_causes=[f"并发根因 {index} 已验证。" for index in range(12)],
                affected_system="workflow",
            )["candidates"]
            context = multiprocessing.get_context("fork")
            start_event = context.Event()
            result_queue = context.Queue()
            processes = [
                context.Process(
                    target=_apply_memory_candidate_concurrently,
                    args=(candidate, str(memory_path), start_event, result_queue),
                )
                for candidate in candidates
            ]
            for process in processes:
                process.start()
            start_event.set()
            results = [result_queue.get(timeout=10) for _ in processes]
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)

            self.assertTrue(all(result["ok"] for result in results), results)
            payload = json.loads(memory_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {item["candidate_id"] for item in payload["iteration_candidates"]},
                {candidate["candidate_id"] for candidate in candidates},
            )


if __name__ == "__main__":
    unittest.main()
