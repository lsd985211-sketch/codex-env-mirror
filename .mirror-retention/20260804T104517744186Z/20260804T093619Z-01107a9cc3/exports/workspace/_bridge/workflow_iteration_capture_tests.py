#!/usr/bin/env python3
from __future__ import annotations

import unittest

from workflow_iteration_capture import capture_iteration_candidates, parse_checkpoint, stable_candidate_id
from workflow_review_presentation import is_chinese_primary_title, project_card


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
                    "迭代捕获必须使用结构化收口事实，而不是任务关键词。",
                    "获批候选只能通过目标 owner 应用。",
                ],
                "followups": ["Run the synthetic end-to-end test."],
                "logical_ref": "checkpoints/mcsmanager/iteration.md",
                "workspace_relative_path": "_bridge/shared/checkpoints/mcsmanager/iteration.md",
                "workspace_path": "C:/workspace/_bridge/shared/checkpoints/mcsmanager/iteration.md",
            }
        }
        parsed = parse_checkpoint(checkpoint)
        self.assertEqual(parsed["checkpoint_id"], "checkpoint-abc123")
        self.assertEqual(parsed["logical_ref"], "checkpoints/mcsmanager/iteration.md")
        self.assertEqual(parsed["source_ref"], "C:/workspace/_bridge/shared/checkpoints/mcsmanager/iteration.md")
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
        self.assertTrue(all(item["source_url"].startswith("C:/workspace/") for item in first["candidates"]))

    def test_historical_path_field_is_read_only_input_compatibility(self) -> None:
        parsed = parse_checkpoint({"checkpoint_id": "old", "path": "checkpoints/legacy/test.md"})

        self.assertEqual(parsed["logical_ref"], "checkpoints/legacy/test.md")
        self.assertEqual(parsed["source_ref"], "checkpoints/legacy/test.md")
        self.assertNotIn("path", parsed)

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

    def test_candidate_identity_does_not_duplicate_across_checkpoints(self) -> None:
        first = stable_candidate_id(
            text="Use guarded transitions.",
            source_checkpoint="checkpoint-1",
            stable_conclusion="Use guarded transitions.",
            target_namespace="memory.project_conclusions",
            affected_system="workflow",
        )
        second = stable_candidate_id(
            text="Use guarded transitions.",
            source_checkpoint="checkpoint-2",
            stable_conclusion="Use guarded transitions.",
            target_namespace="memory.project_conclusions",
            affected_system="workflow",
        )
        self.assertEqual(first, second)

    def test_regression_results_remain_checkpoint_evidence_not_memory_candidates(self) -> None:
        result = capture_iteration_candidates(
            outcome="ok",
            regression_tests=["workflow tests 12/12 passed"],
            affected_system="workflow",
        )
        self.assertTrue(result["triggered"])
        self.assertEqual(0, result["candidate_count"])
        self.assertEqual(1, result["evidence_only_signal_count"])

    def test_sensitive_verified_conclusion_is_blocked_before_queue_persistence(self) -> None:
        result = capture_iteration_candidates(
            outcome="ok",
            verified_root_causes=["password=supersecretvalue"],
            affected_system="workflow",
        )
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["blocked_count"], 1)

    def test_english_only_signals_remain_evidence_but_do_not_enter_approval_queue(self) -> None:
        result = capture_iteration_candidates(
            outcome="ok",
            verified_root_causes=["The resource facade forwarded raw owner payloads."],
            prevention_guards=["Every projection-changing input participates in the signature."],
            affected_system="workflow",
        )

        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["blocked_count"], 2)
        self.assertTrue(all(item["reason"] == "approval_presentation_incomplete" for item in result["blocked_candidates"]))
        self.assertEqual(
            result["blocked_candidates"][0]["original_evidence"],
            "The resource facade forwarded raw owner payloads.",
        )

    def test_chinese_primary_signal_enters_approval_queue(self) -> None:
        result = capture_iteration_candidates(
            outcome="ok",
            verified_root_causes=["资源 facade 曾直接转发 owner 原始载荷。"],
            affected_system="workflow",
        )

        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["blocked_count"], 0)
        self.assertTrue(is_chinese_primary_title(result["candidates"][0]["title"]))
        projected_once = project_card({**result["candidates"][0], "kind": "iteration_candidates"})
        projected_twice = project_card(projected_once)
        self.assertEqual(projected_once["title"], projected_twice["title"])
        self.assertEqual(projected_once["required_checks"], projected_twice["required_checks"])

    def test_known_closeout_proposal_uses_chinese_semantic_title_and_checks(self) -> None:
        projected = project_card({
            "kind": "proposals",
            "title": "Phase 1: reduce low-risk route overhead and reconcile resource attachment satisfaction; no safety-gate weakening.",
            "summary": "Phase 1: reduce low-risk route overhead and reconcile resource attachment satisfaction; no safety-gate weakening.",
            "required_checks": [
                "Apply only the exact approved proposal scope",
                "Use the supplied proposal detail",
            ],
        })

        self.assertEqual(projected["title"], "第一阶段：降低低风险路由开销并核对资源附件满足情况（不削弱安全门禁）")
        self.assertEqual(projected["summary"], "降低低风险路由的重复开销，并核对资源附件是否真正满足调用方需求；实施不得削弱任何安全门禁。")
        self.assertEqual(projected["required_checks"], ["仅应用本次精确批准的提案范围", "依据已提供的提案详情作出决定"])
        self.assertTrue(projected["presentation_complete"])

    def test_unknown_english_card_is_retained_as_evidence_but_not_approval_ready(self) -> None:
        projected = project_card({
            "kind": "proposals",
            "title": "Unmapped proposal",
            "summary": "Unmapped proposal detail",
            "required_checks": ["Verify exact scope"],
        })

        self.assertEqual(projected["title"], "审批事项：Unmapped proposal")
        self.assertEqual(projected["summary"], "中文摘要待生产者补充；原始证据：Unmapped proposal detail")
        self.assertFalse(projected["presentation_complete"])


if __name__ == "__main__":
    unittest.main()
