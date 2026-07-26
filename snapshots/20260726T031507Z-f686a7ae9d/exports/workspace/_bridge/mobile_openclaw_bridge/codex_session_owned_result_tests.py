from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_session_owned_result import find_owned_result, is_usable_owned_result_text


TASK = "task_123"
RESULT = "result_456"
ACK = "ack_789"
BEGIN = f"[[mobile_result_begin:{TASK}:{RESULT}]]"
TASK_MARKER = f"[[mobile_task_id:{TASK}]]"
ACK_MARKER = f"[[mobile_ack:{TASK}:{ACK}]]"
END = f"[[mobile_result_end:{TASK}:{RESULT}]]"
CREATED_AT = "2026-07-15T00:00:00Z"


def record(record_type: str, payload: dict, timestamp: str = CREATED_AT) -> str:
    return json.dumps({"timestamp": timestamp, "type": record_type, "payload": payload}, ensure_ascii=False)


def delegation(timestamp: str = CREATED_AT) -> str:
    prompt = f"delegation\n{ACK_MARKER}\n{BEGIN}\n{TASK_MARKER}\n{END}"
    return record("event_msg", {"type": "user_message", "message": prompt}, timestamp)


def final_agent(text: str, timestamp: str = "2026-07-15T00:00:01Z", phase: str = "final_answer") -> str:
    return record("event_msg", {"type": "agent_message", "message": text, "phase": phase}, timestamp)


class CodexSessionOwnedResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rollout = self.root / "rollout-2026-07-15T00-00-00-019f1c72-03c3-7032-aa56-dff625d7c720.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, *lines: str) -> None:
        self.rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def find(self, **kwargs: object) -> dict:
        return find_owned_result(TASK, RESULT, ACK, created_at=CREATED_AT, session_roots=[self.root], **kwargs)

    def test_finds_bound_final_result_and_thread_id(self) -> None:
        text = f"{BEGIN}\n{TASK_MARKER}\n完成结果\n{END}"
        self.write(delegation(), final_agent(text))
        result = self.find()
        self.assertTrue(result["ok"])
        self.assertEqual(result["newText"], "完成结果")
        self.assertEqual(result["source"]["thread_id"], "019f1c72-03c3-7032-aa56-dff625d7c720")
        self.assertEqual(result["source"]["delegation_line"], 1)

    def test_deduplicates_agent_and_response_copies(self) -> None:
        text = f"{BEGIN}\n{TASK_MARKER}\n相同结果\n{END}"
        self.write(
            delegation(),
            final_agent(text),
            record("response_item", {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": text}]}),
        )
        result = self.find()
        self.assertEqual(result["newText"], "相同结果")
        self.assertEqual(result["duplicate_copies"], 2)

    def test_rejects_user_prompt_compacted_and_commentary(self) -> None:
        text = f"{BEGIN}\n{TASK_MARKER}\n不能采用\n{END}"
        self.write(
            delegation(),
            record("compacted", {"message": text}),
            final_agent(text, phase="commentary"),
        )
        self.assertIsNone(self.find()["newText"])

    def test_rejects_embedded_protocol_quote_and_old_unbound_result(self) -> None:
        quoted = f"handoff copied:\n{BEGIN}\n{TASK_MARKER}\n不能采用\n{END}\nmore text"
        old = f"{BEGIN}\n{TASK_MARKER}\n过期结果\n{END}"
        self.write(
            final_agent(quoted),
            final_agent(old, timestamp="2025-01-01T00:00:00Z"),
        )
        self.assertIsNone(self.find()["newText"])

    def test_rejects_wrong_code_empty_and_oversized_body(self) -> None:
        wrong = f"[[mobile_result_begin:{TASK}:wrong]]\n{TASK_MARKER}\n错误\n[[mobile_result_end:{TASK}:wrong]]"
        empty = f"{BEGIN}\n{TASK_MARKER}\n{END}"
        oversized = f"{BEGIN}\n{TASK_MARKER}\n{'A' * 61_000}\n{END}"
        self.write(delegation(), final_agent(wrong), final_agent(empty), final_agent(oversized))
        self.assertIsNone(self.find()["newText"])

    def test_rejects_ui_placeholder_variants(self) -> None:
        for placeholder in ["...", "…", "....…", "\u200b...", "[truncated]", "... (truncated)"]:
            self.assertFalse(is_usable_owned_result_text(placeholder), placeholder)

    def test_conflicting_bound_results_fail_closed(self) -> None:
        first = f"{BEGIN}\n{TASK_MARKER}\n结果一\n{END}"
        second = f"{BEGIN}\n{TASK_MARKER}\n结果二\n{END}"
        self.write(delegation(), final_agent(first), final_agent(second, "2026-07-15T00:00:02Z"))
        result = self.find()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "ambiguous_owned_results")

    def test_rg_no_match_does_not_trigger_second_full_scan(self) -> None:
        with patch("codex_session_owned_result._candidate_files_with_rg", return_value=("not_found", [])), patch(
            "codex_session_owned_result._candidate_files_bounded", side_effect=AssertionError("fallback must not run")
        ):
            result = self.find()
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "owned_result_not_found")

    def test_invalid_identifiers_fail_before_search(self) -> None:
        result = find_owned_result("../task", RESULT, session_roots=[self.root])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_protocol_identifier")


if __name__ == "__main__":
    unittest.main()
