from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_session_owned_result import find_owned_result


TASK = "task_123"
RESULT = "result_456"
ACK = "ack_789"
BEGIN = f"[[mobile_result_begin:{TASK}:{RESULT}]]"
TASK_MARKER = f"[[mobile_task_id:{TASK}]]"
END = f"[[mobile_result_end:{TASK}:{RESULT}]]"


def record(record_type: str, payload: dict, timestamp: str = "2026-07-15T00:00:00Z") -> str:
    return json.dumps({"timestamp": timestamp, "type": record_type, "payload": payload}, ensure_ascii=False)


class CodexSessionOwnedResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rollout = self.root / "rollout-2026-07-15T00-00-00-019f1c72-03c3-7032-aa56-dff625d7c720.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, *lines: str) -> None:
        self.rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def find(self) -> dict:
        return find_owned_result(TASK, RESULT, ACK, session_roots=[self.root])

    def test_finds_exact_assistant_result_and_thread_id(self) -> None:
        text = f"{BEGIN}\n{TASK_MARKER}\n完成结果\n{END}"
        self.write(record("response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}))
        result = self.find()
        self.assertTrue(result["ok"])
        self.assertEqual(result["newText"], "完成结果")
        self.assertEqual(result["source"]["thread_id"], "019f1c72-03c3-7032-aa56-dff625d7c720")

    def test_deduplicates_agent_and_response_copies(self) -> None:
        text = f"{BEGIN}\n{TASK_MARKER}\n相同结果\n{END}"
        self.write(
            record("event_msg", {"type": "agent_message", "message": text}),
            record("response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}),
        )
        result = self.find()
        self.assertEqual(result["newText"], "相同结果")
        self.assertEqual(result["duplicate_copies"], 2)

    def test_ignores_user_prompt_and_compacted_record(self) -> None:
        text = f"{BEGIN}\n{TASK_MARKER}\n不能采用\n{END}"
        self.write(
            record("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}),
            record("compacted", {"message": text}),
        )
        self.assertIsNone(self.find()["newText"])

    def test_ignores_embedded_protocol_quote(self) -> None:
        quoted = f"handoff copied an older result:\n{BEGIN}\n{TASK_MARKER}\n不能采用\n{END}\nmore text"
        self.write(record("event_msg", {"type": "agent_message", "message": quoted}))
        self.assertIsNone(self.find()["newText"])

    def test_rejects_wrong_code_and_empty_body(self) -> None:
        wrong = f"[[mobile_result_begin:{TASK}:wrong]]\n{TASK_MARKER}\n错误\n[[mobile_result_end:{TASK}:wrong]]"
        empty = f"{BEGIN}\n{TASK_MARKER}\n{END}"
        self.write(
            record("event_msg", {"type": "agent_message", "message": wrong}),
            record("event_msg", {"type": "agent_message", "message": empty}),
        )
        self.assertIsNone(self.find()["newText"])

    def test_rejects_ui_ellipsis_placeholder(self) -> None:
        placeholder = f"{BEGIN}\n{TASK_MARKER}\n...\n{END}"
        self.write(record("event_msg", {"type": "agent_message", "message": placeholder}))
        self.assertIsNone(self.find()["newText"])

    def test_conflicting_results_fail_closed(self) -> None:
        first = f"{BEGIN}\n{TASK_MARKER}\n结果一\n{END}"
        second = f"{BEGIN}\n{TASK_MARKER}\n结果二\n{END}"
        self.write(
            record("event_msg", {"type": "agent_message", "message": first}, "2026-07-15T00:00:01Z"),
            record("event_msg", {"type": "agent_message", "message": second}, "2026-07-15T00:00:02Z"),
        )
        result = self.find()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "ambiguous_owned_results")

    def test_invalid_identifiers_fail_before_search(self) -> None:
        result = find_owned_result("../task", RESULT, session_roots=[self.root])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_protocol_identifier")


if __name__ == "__main__":
    unittest.main()
