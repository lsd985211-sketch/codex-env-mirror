from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workflow_efficiency_cycle_projection import project_efficiency_cycles


class WorkflowEfficiencyCycleProjectionTests(unittest.TestCase):
    def test_projection_is_redacted_stable_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "private-rollout.jsonl"
            rows = []
            for name in ("workflow_orchestrator", "workflow_orchestrator", "wait_threads", "wait_threads"):
                rows.append({"type": "response_item", "payload": {"type": "function_call", "name": name, "arguments": "secret command /private/path AUTHORIZE-secret"}})
            rollout.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
            before = rollout.read_bytes()
            first = project_efficiency_cycles(rollout_path=rollout, task_ref="private-task")
            replay = project_efficiency_cycles(rollout_path=rollout, task_ref="private-task")
            after = rollout.read_bytes()
        self.assertTrue(first["ok"])
        self.assertEqual(first, replay)
        self.assertEqual(before, after)
        self.assertEqual({"routing_rebuild", "coordination_wait"}, {item["cycle_type"] for item in first["events"]})
        serialized = json.dumps(first, ensure_ascii=False)
        for secret in ("private-task", "secret command", "/private/path", "AUTHORIZE-secret"):
            self.assertNotIn(secret, serialized)

    def test_receipt_replay_detects_source_drift_without_copying_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "rollout.jsonl"
            rollout.write_text("", encoding="utf-8")
            receipts = root / "receipts"
            receipts.mkdir()
            for index, (step, base, head) in enumerate((
                ("start", "a", "a"), ("commit", "a", "b"),
                ("start", "c", "c"), ("commit", "c", "d"),
            )):
                item = {
                    "schema": f"work_git_change_owner.{step}.v1",
                    "plan": {"task_id": "task-a", "base_commit": base, "private_path": "/secret"},
                    "after": {"head": head},
                }
                (receipts / f"{index}.json").write_text(json.dumps(item), encoding="utf-8")
            result = project_efficiency_cycles(rollout_path=rollout, task_ref="task-a", receipt_root=receipts)
        events = {item["cycle_type"]: item for item in result["events"]}
        self.assertIn("source_drift_rework", events)
        self.assertIn("closeout_replay", events)
        self.assertNotIn("/secret", json.dumps(result))

    def test_missing_rollout_fails_closed_without_creating_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = project_efficiency_cycles(rollout_path=root / "missing.jsonl", task_ref="x")
            self.assertFalse(result["ok"])
            self.assertEqual(list(root.iterdir()), [])

    def test_exec_input_is_never_read_as_cycle_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_text(json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": "secret command workflow_validation AUTHORIZE-secret /private/path",
                },
            }) + "\n", encoding="utf-8")
            result = project_efficiency_cycles(rollout_path=rollout, task_ref="private-task")
        self.assertTrue(result["ok"])
        self.assertEqual(result["events"], [])
        serialized = json.dumps(result, ensure_ascii=False)
        for secret in ("secret command", "AUTHORIZE-secret", "/private/path"):
            self.assertNotIn(secret, serialized)

    def test_same_task_cycle_keeps_one_stable_event_identity_as_count_grows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rows = [
                {"type": "response_item", "payload": {"type": "function_call", "name": "wait"}},
                {"type": "response_item", "payload": {"type": "function_call", "name": "wait"}},
            ]
            rollout.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
            first = project_efficiency_cycles(rollout_path=rollout, task_ref="task-a")
            rows.append({"type": "response_item", "payload": {"type": "function_call", "name": "wait"}})
            rollout.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
            later = project_efficiency_cycles(rollout_path=rollout, task_ref="task-a")
        self.assertEqual(first["events"][0]["event_id"], later["events"][0]["event_id"])
        self.assertNotEqual(first["events"][0]["evidence_signature"], later["events"][0]["evidence_signature"])


if __name__ == "__main__":
    unittest.main()
