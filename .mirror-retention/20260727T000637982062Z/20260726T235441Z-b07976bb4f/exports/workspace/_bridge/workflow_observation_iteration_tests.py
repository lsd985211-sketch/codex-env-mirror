from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_rule_observer import build_tool_event, write_event
from workflow_observation_iteration import derive_candidates, run
from workflow_review_queue import snapshot


def event(session: str, turn: str, call: str, tool: str, category_command: str, ok: bool) -> dict:
    return build_tool_event(
        {
            "session_id": session,
            "turn_id": turn,
            "tool_use_id": call,
            "tool_name": tool,
            "tool_input": {"command": category_command},
            "tool_response": {"ok": ok},
        }
    )


class WorkflowObservationIterationTests(unittest.TestCase):
    def test_insufficient_samples_do_not_produce_candidate(self) -> None:
        events = [event("s", "t", "1", "Bash", "apply_patch", True)]
        self.assertEqual(derive_candidates(events), [])

    def test_repeated_tool_failure_has_stable_identity(self) -> None:
        events = [
            event("s", f"t-{index % 3}", str(index), "mcp__owner__call", "", index >= 3)
            for index in range(5)
        ]
        first = derive_candidates(events)
        replay = derive_candidates([*events, event("s", "t-4", "6", "mcp__owner__call", "", False)])
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["candidate_id"], replay[0]["candidate_id"])

    def test_apply_replays_one_pending_review_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "events"
            queue = Path(tmp) / "review.sqlite"
            for index in range(5):
                write_event(
                    event("s", f"t-{index % 3}", str(index), "mcp__owner__call", "", index >= 3),
                    root,
                )
            first = run(apply=True, confirm="APPLY-OBSERVATION-PROPOSALS", runtime_root=root, queue_path=queue)
            replay = run(apply=True, confirm="APPLY-OBSERVATION-PROPOSALS", runtime_root=root, queue_path=queue)
            snap = snapshot(db_path=queue)
        self.assertTrue(first["ok"] and replay["ok"])
        self.assertEqual(len(snap["pending"]), 1)
        self.assertFalse(first["contracts"]["direct_pmb_write"])

    def test_untrusted_tool_label_is_not_copied_to_candidate(self) -> None:
        events = [event("s", f"t-{index % 3}", str(index), "password=not-a-tool", "", False) for index in range(5)]
        candidate = derive_candidates(events)[0]
        self.assertNotIn("password=not-a-tool", candidate["summary"])

    def test_governance_occurrence_count_uses_only_trigger_events(self) -> None:
        events = [
            event("s", "t-1", "w-1", "apply_patch", "apply_patch", True),
            event("s", "t-1", "o-1", "Bash", "echo unrelated", True),
            event("s", "t-2", "w-2", "apply_patch", "apply_patch", True),
            event("s", "t-2", "o-2", "Bash", "echo unrelated", True),
        ]
        self.assertEqual(derive_candidates(events, minimum_occurrences=3), [])


if __name__ == "__main__":
    unittest.main()
