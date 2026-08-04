from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import mobile_openclaw_cli as cli


RESULT_CODE = "result-code"
ACK_CODE = "ack-code"
CANDIDATE = "exact owned correction"
CANDIDATE_HASH = cli.sha256_text(CANDIDATE)


class OwnedResultCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        config = {"security": {"allowed_users": ["admin@im.wechat"]}}
        self.queue = cli.MobileQueue(Path(self.temp.name) / "queue.db", config=config)
        created = self.queue.enqueue(
            "status",
            source="test",
            external_user="admin@im.wechat",
            external_conversation="admin@im.wechat",
            metadata={"receiver_account_id": "primary"},
        )
        self.task_id = str(created["id"])
        with self.queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='pushed_to_wecom', push_status='pushed_to_wecom',
                    result='...', receiver_account_id='primary'
                WHERE id=?
                """,
                (self.task_id,),
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def attempts(self) -> list[dict]:
        return [{"turn_id": "cdp-visible-turn", "expected_result_codes": {self.task_id: RESULT_CODE}, "expected_ack_codes": {self.task_id: ACK_CODE}}]

    def candidate(self) -> dict:
        return {
            "ok": True,
            "newText": CANDIDATE,
            "result_complete": True,
            "source": {"sha256": CANDIDATE_HASH, "source_file": "test.jsonl", "source_line": 2},
        }

    def apply(self, **kwargs: object) -> dict:
        return cli.recover_owned_result(
            self.queue,
            {},
            self.task_id,
            apply=True,
            confirm="CORRECT-OWNED-RESULT",
            expected_sha256=CANDIDATE_HASH,
            **kwargs,
        )

    def test_concurrent_apply_sends_once(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        sends: list[str] = []

        def fake_send(_queue, _task, text, _config, **_kwargs):
            sends.append(text)
            entered.set()
            release.wait(timeout=3)
            return {"ok": True, "delivery_accepted": True}

        with patch.object(cli, "recent_codex_turn_protocol_attempts", return_value=self.attempts()), patch.object(
            cli, "find_codex_session_owned_result", side_effect=lambda *_args, **_kwargs: self.candidate()
        ), patch.object(cli, "push_final_reply", side_effect=fake_send), patch.object(
            cli, "final_reply_delivery_accepted", return_value=True
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.apply)
                self.assertTrue(entered.wait(timeout=3))
                second = pool.submit(self.apply)
                release.set()
                results = [first.result(timeout=5), second.result(timeout=5)]
        self.assertEqual(sends, [CANDIDATE])
        self.assertEqual(sum(1 for item in results if item.get("delivery_accepted")), 1)
        self.assertEqual(
            sum(
                1
                for item in results
                if item.get("reason") in {"correction_send_in_progress", "correction_send_outcome_unknown_manual_review_required"}
            ),
            1,
        )

    def test_pending_intent_fails_closed_without_resend(self) -> None:
        operation_id = cli.owned_result_correction_operation_id(self.task_id, CANDIDATE_HASH)
        self.queue.add_event(
            "local",
            "owned_result_correction_intent",
            {"operation_id": operation_id, "candidate_sha256": CANDIDATE_HASH},
            self.task_id,
        )
        with patch.object(cli, "recent_codex_turn_protocol_attempts", return_value=self.attempts()), patch.object(
            cli, "find_codex_session_owned_result", side_effect=lambda *_args, **_kwargs: self.candidate()
        ), patch.object(cli, "push_final_reply") as send:
            result = self.apply()
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "correction_send_outcome_unknown_manual_review_required")
        send.assert_not_called()

    def test_sender_receipt_reconciles_without_resend(self) -> None:
        operation_id = cli.owned_result_correction_operation_id(self.task_id, CANDIDATE_HASH)
        self.queue.add_event(
            "local",
            "owned_result_correction_intent",
            {"operation_id": operation_id, "candidate_sha256": CANDIDATE_HASH},
            self.task_id,
        )
        self.queue.add_event(
            "wecom",
            "final_reply_weixin_accepted",
            {"delivery_accepted": True, "operation": {"operation_id": operation_id, "candidate_sha256": CANDIDATE_HASH}},
            self.task_id,
        )
        with patch.object(cli, "recent_codex_turn_protocol_attempts", return_value=self.attempts()), patch.object(
            cli, "find_codex_session_owned_result", side_effect=lambda *_args, **_kwargs: self.candidate()
        ), patch.object(cli, "push_final_reply") as send:
            result = self.apply()
        self.assertTrue(result["ok"])
        self.assertTrue(result["reconciled_sender_receipt"])
        send.assert_not_called()
        task = self.queue.get_task(self.task_id) or {}
        self.assertEqual(task.get("result"), CANDIDATE)

    def test_hash_confirmation_mismatch_cannot_send(self) -> None:
        with patch.object(cli, "recent_codex_turn_protocol_attempts", return_value=self.attempts()), patch.object(
            cli, "find_codex_session_owned_result", side_effect=lambda *_args, **_kwargs: self.candidate()
        ), patch.object(cli, "push_final_reply") as send:
            result = cli.recover_owned_result(
                self.queue,
                {},
                self.task_id,
                apply=True,
                confirm="CORRECT-OWNED-RESULT",
                expected_sha256="wrong",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "candidate_hash_confirmation_mismatch")
        send.assert_not_called()

    def test_missing_session_result_is_negative_cached(self) -> None:
        calls = 0

        def missing(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return {"ok": True, "newText": None, "result_complete": False, "reason": "owned_result_not_found"}

        poll = {"ok": True, "newText": None, "result_complete": True, "ownership": {"valid": True, "result_complete": True}}
        with patch.object(cli, "poll_historical_owned_codex_result", return_value={}), patch.object(
            cli, "poll_codex_thread_history_owned_result", return_value={"ok": False, "reason": "no rollout found for stale"}
        ), patch.object(cli, "find_codex_session_owned_result", side_effect=missing):
            for _ in range(2):
                cli.recover_owned_result_from_history_sources(
                    self.queue,
                    {"trigger": {"session_owned_result_negative_cache_seconds": 30}},
                    {},
                    self.task_id,
                    "stale-thread",
                    "cdp-visible-turn",
                    "client",
                    [self.task_id],
                    {self.task_id: RESULT_CODE},
                    {self.task_id: ACK_CODE},
                    poll,
                )
        self.assertEqual(calls, 1)

    def test_ambiguous_session_result_sets_bounded_manual_review_marker(self) -> None:
        conflicting_hash = "b" * 64
        session_result = {
            "ok": False,
            "reason": "ambiguous_owned_results",
            "candidate_hashes": [CANDIDATE_HASH, conflicting_hash, "not-a-hash"],
            "candidate_count": 99,
            "search_mode": "bounded",
            "newText": None,
            "result_complete": False,
        }
        with patch.object(cli, "poll_historical_owned_codex_result", return_value={}), patch.object(
            cli, "poll_codex_thread_history_owned_result", return_value={"reason": "thread_unreadable"}
        ), patch.object(cli, "find_codex_session_owned_result", return_value=session_result):
            poll, text, complete = cli.recover_owned_result_from_history_sources(
                self.queue,
                {},
                {},
                self.task_id,
                "visible-thread",
                "cdp-visible-turn",
                "client-message",
                [self.task_id],
                {self.task_id: RESULT_CODE},
                {self.task_id: ACK_CODE},
                {"result_complete": True},
            )
        self.assertTrue(poll["session_store_recovery_blocked"])
        self.assertEqual(text, "")
        self.assertTrue(complete)
        raw = self.queue.runtime_get(cli.session_owned_result_manual_review_key(self.task_id))
        marker = cli.json.loads(str(raw))
        self.assertEqual(marker["reason"], "ambiguous_owned_results")
        self.assertEqual(marker["candidate_hashes"], sorted([CANDIDATE_HASH, conflicting_hash]))
        self.assertEqual(marker["candidate_count"], 16)
        with self.queue.session() as db:
            events = db.execute(
                "SELECT event_type FROM mobile_events WHERE task_id=? ORDER BY id DESC LIMIT 20",
                (self.task_id,),
            ).fetchall()
        self.assertTrue(any(str(event["event_type"]) == "session_store_owned_result_manual_review_required" for event in events))

    def test_direct_owned_result_clears_manual_review_marker(self) -> None:
        self.queue.runtime_set(
            cli.session_owned_result_manual_review_key(self.task_id),
            cli.json.dumps({"reason": "ambiguous_owned_results"}),
        )
        poll, text, complete = cli.recover_owned_result_from_history_sources(
            self.queue,
            {},
            {},
            self.task_id,
            "visible-thread",
            "cdp-visible-turn",
            "client-message",
            [self.task_id],
            {self.task_id: RESULT_CODE},
            {self.task_id: ACK_CODE},
            {"newText": CANDIDATE, "result_complete": True},
        )
        self.assertEqual(poll["newText"], CANDIDATE)
        self.assertEqual(text, CANDIDATE)
        self.assertTrue(complete)
        self.assertEqual(self.queue.runtime_get(cli.session_owned_result_manual_review_key(self.task_id)), "")


if __name__ == "__main__":
    unittest.main()
