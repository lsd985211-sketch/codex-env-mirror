#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow_automation_delegation import POLICY_SCHEMA
from tool_utilization_audit import (
    activation_acceptance_with_build_plan,
    declared_activation_cases,
    evaluate_activation_case,
)


class ToolUtilizationAuditTests(unittest.TestCase):
    def test_activation_cases_are_derived_from_dependency_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "example.json").write_text(
                json.dumps({
                    "profile_id": "reviewer",
                    "capability_contracts": [{
                        "capability_id": "review.scope",
                        "activation_acceptance": {
                            "representative_tasks": [{
                                "id": "workspace",
                                "message": "review changes",
                                "expected_workflow_domain": "code_review_refactor",
                                "expected_skill": "review-skill",
                            }],
                            "runtime_evidence": {"owner": "tool-owner"},
                            "usage_evidence": {"skill": "review-skill", "minimum_applied": 1},
                            "result_consumption": "selected paths are consumed",
                        },
                    }],
                }),
                encoding="utf-8",
            )

            cases = declared_activation_cases(root, system_contracts={})

            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["expect_skill"], "review-skill")

    def test_activation_requires_route_skill_and_consumed_use(self) -> None:
        case = {
            "id": "activation:reviewer:workspace",
            "expect_domain": "code_review_refactor",
            "expect_skill": "review-skill",
            "usage_skill": "review-skill",
            "minimum_applied": 1,
            "runtime_evidence": {"owner": "tool-owner"},
            "result_consumption": "selected paths are consumed",
        }
        workflow_plan = {"domains": [{"key": "code_review_refactor"}]}
        skill_plan = {"selected_skills": [{"name": "review-skill"}]}
        missing = evaluate_activation_case(workflow_plan, skill_plan, {"skills": {}}, case)
        accepted = evaluate_activation_case(
            workflow_plan,
            skill_plan,
            {"skills": {"review-skill": {"applied": 1}}},
            case,
        )

        self.assertFalse(missing["ok"])
        self.assertTrue(accepted["ok"])

    def test_internal_member_can_require_a_plan_surface_without_a_skill_counter(self) -> None:
        case = {
            "id": "activation:system-workflow:machine-first",
            "expect_domain": "workflow_governance",
            "expected_plan_path": "automation_delegation.schema",
            "expected_plan_prefix": "workflow_automation_delegation.",
            "runtime_evidence": {"owner": "workflow_automation_delegation.py"},
            "result_consumption": "receipt reuse is consumed",
        }
        result = evaluate_activation_case(
            {
                "domains": [{"key": "workflow_governance"}],
                "automation_delegation": {"schema": POLICY_SCHEMA},
            },
            {"selected_skills": []},
            {"skills": {}},
            case,
        )

        self.assertTrue(result["ok"], result)

    def test_precomputed_skill_plan_reuses_quality_evidence_without_legacy_migration(self) -> None:
        case = {
            "id": "activation:reviewer:workspace",
            "message": "review changes",
            "expect_domain": "code_review_refactor",
            "expect_skill": "review-skill",
            "usage_skill": "review-skill",
            "minimum_applied": 1,
            "runtime_evidence": {"owner": "tool-owner"},
            "result_consumption": "selected paths are consumed",
        }
        def build_plan(_message: str, detail: str) -> dict[str, object]:
            return {"domains": [{"key": "code_review_refactor"}], "detail": detail}

        def skill_plan(_message: str) -> dict[str, object]:
            return {"selected_skills": [{"name": "review-skill"}]}

        with patch(
            "skill_orchestrator.skill_lifecycle_state.quality_summary",
            return_value={"skills": {"review-skill": {"applied": 1}}},
        ), patch("skill_orchestrator.usage_summary", side_effect=AssertionError("legacy migration path used")), patch(
            "skill_orchestrator.prepare_routing_context",
            side_effect=AssertionError("unnecessary routing refresh"),
        ):
            result = activation_acceptance_with_build_plan(
                build_plan,
                skill_plan=skill_plan,
                activation_cases=[case],
            )

        self.assertTrue(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
