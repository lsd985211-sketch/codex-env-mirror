#!/usr/bin/env python3
from __future__ import annotations

import unittest

from workflow_iteration_capture import capture_iteration_candidates, parse_checkpoint, stable_candidate_id


class WorkflowIterationCaptureTests(unittest.TestCase):
    def test_structured_checkpoint_extracts_semantic_sections_and_stable_conclusions(self) -> None:
        checkpoint = {
            "checkpoint": {
                "checkpoint_id": "checkpoint-abc123",
                "project_id": "mcsmanager",
                "summary": "Closed the iteration governance gap.",
                "changed_files": ["_bridge/workflow_iteration_capture.py"],
                "evidence": ["Focused unit tests passed."],
                "verification": ["python -m unittest workflow_iteration_capture_tests"],
                "stable_conclusions": [
                    "Iteration capture must use structured closeout facts instead of task keywords.",
                    "Approved candidates must be applied only through the target owner.",
                ],
                "followups": ["Run the synthetic end-to-end test."],
                "path": "checkpoints/mcsmanager/iteration.md",
            }
        }
        parsed = parse_checkpoint(checkpoint)
        self.assertEqual(parsed["checkpoint_id"], "checkpoint-abc123")
        self.assertEqual(len(parsed["stable_conclusions"]), 2)

        first = capture_iteration_candidates(
            outcome="ok",
            major_change=True,
            checkpoint=checkpoint,
            affected_system="workflow",
        )
        second = capture_iteration_candidates(
            outcome="ok",
            major_change=True,
            checkpoint=checkpoint,
            affected_system="workflow",
        )
        self.assertEqual(first["candidate_count"], 2)
        self.assertEqual(
            [item["candidate_id"] for item in first["candidates"]],
            [item["candidate_id"] for item in second["candidates"]],
        )
        self.assertTrue(all(item["candidate_id"].startswith("iteration:") for item in first["candidates"]))

    def test_markdown_fallback_parses_exact_headings_only(self) -> None:
        markdown = """# Iteration checkpoint

## Summary
Verified the bounded closeout flow.

## Evidence
- focused test passed

## Verification
- recall succeeded

## Stable Conclusions
- A verified conclusion is captured directly.

## Followups
- keep owner boundaries
"""
        parsed = parse_checkpoint(markdown)
        self.assertEqual(parsed["summary"], "Verified the bounded closeout flow.")
        self.assertEqual(parsed["stable_conclusions"], ["A verified conclusion is captured directly."])

    def test_candidate_identity_covers_target_and_affected_system(self) -> None:
        base = stable_candidate_id(
            text="Use guarded transitions.",
            source_checkpoint="checkpoint-1",
            stable_conclusion="Use guarded transitions.",
            target_namespace="memory.project_conclusions",
            affected_system="workflow",
        )
        changed_target = stable_candidate_id(
            text="Use guarded transitions.",
            source_checkpoint="checkpoint-1",
            stable_conclusion="Use guarded transitions.",
            target_namespace="rules.workflow",
            affected_system="workflow",
        )
        self.assertNotEqual(base, changed_target)

    def test_sensitive_verified_conclusion_is_blocked_before_queue_persistence(self) -> None:
        result = capture_iteration_candidates(
            outcome="ok",
            verified_root_causes=["password=supersecretvalue"],
            affected_system="workflow",
        )
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["blocked_count"], 1)


if __name__ == "__main__":
    unittest.main()
