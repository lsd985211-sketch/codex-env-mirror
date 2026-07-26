from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import iteration_layer_review as review


class IterationLayerValidationRouteTests(unittest.TestCase):
    def test_toolchain_validation_uses_authoritative_owner(self) -> None:
        steps = {step.name: step for step in review.validation_plan("quick")}

        self.assertIn("developer-toolchain-validate", steps)
        command = steps["developer-toolchain-validate"].command
        self.assertEqual(Path(command[-2]).name, "developer_toolchain_owner.py")
        self.assertEqual(command[-1], "validate")
        self.assertNotIn("mobile_openclaw_cli.py", " ".join(command))

    def test_tool_registry_proposals_share_the_owner_validation_route(self) -> None:
        self.assertEqual(
            review.validation_for_route("tool registry"),
            "Run python _bridge/developer_toolchain_owner.py validate.",
        )

    def test_recent_artifacts_and_validation_steps_do_not_create_proposals(self) -> None:
        observation = review.Finding(
            kind="tool-state observation",
            confidence="verified_file_metadata",
            summary="Recent owner evidence exists.",
            evidence="mtime=1 size=1",
            recommended_route="CLI automation proposal",
            proposal="Observe only.",
        )
        with (
            patch.object(review, "add_design_findings", return_value=[]),
            patch.object(review, "add_skill_findings", return_value=[]),
            patch.object(review, "add_recent_artifact_findings", return_value=[observation]),
        ):
            report = review.build_report(1, run_validation=False, validation_profile="quick", validation_timeout=1)

        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(report["observation_count"], 1)
        self.assertEqual(report["actionable_finding_count"], 0)
        self.assertEqual(report["proposal_count"], 0)
        self.assertEqual(report["knowledge_candidate_count"], 0)
        self.assertEqual(report["approval_block"]["status"], "no_actionable_proposals")
        self.assertFalse(report["approval_block"]["user_decision_required"])

    def test_explicit_gap_remains_actionable(self) -> None:
        gap = review.Finding(
            kind="skill-entrypoint-gap",
            confidence="verified_text_absence",
            summary="A declared entrypoint is missing.",
            evidence="skill.md",
            recommended_route="skill",
            proposal="Add the missing entrypoint after approval.",
        )

        self.assertEqual(review.actionable_findings([gap]), [gap])


if __name__ == "__main__":
    unittest.main()
