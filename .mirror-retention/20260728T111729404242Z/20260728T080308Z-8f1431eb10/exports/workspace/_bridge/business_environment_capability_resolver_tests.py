#!/usr/bin/env python3
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from business_environment_capability_resolver import build_information_package


SCENARIOS = {
    "software_engineering": "审查当前工作树中的代码变更并找出回归风险",
    "system_maintenance": "诊断 Codex 工作环境的维护异常并生成修复计划",
    "external_research": "联网搜索官方资料并形成有引用的研究结论",
    "content_production": "制作并验证一份业务演示文稿",
    "cross_system_automation": "跨 Windows 和 WSL 自动化处理文件并验证业务结果",
}
ACTIVATION_CASES = [
    {
        "id": "activation:open-code-review:workspace-code-review",
        "capability_id": "code_review.delegation_mode",
        "message": SCENARIOS["software_engineering"],
        "expect_domain": "code_review_refactor",
        "expect_skill": "open-code-review-delegate",
        "usage_skill": "open-code-review-delegate",
        "minimum_applied": 1,
        "runtime_evidence": {"owner": "developer_toolchain_owner", "validation": "ocr delegate preview"},
        "result_consumption": "Codex consumes OCR-selected paths and rules",
    }
]


def authorities() -> dict[str, object]:
    member = {
        "member_id": "workflow.business-environment-control-plane",
        "system": "workflow",
        "kind": "facade",
        "lifecycle": "active",
        "owner": "business_environment_control_plane",
    }
    return {
        "workflow_plan": {"schema": "workflow.plan", "ok": True, "domains": [{"key": "workflow_governance"}]},
        "skill_plan": {"schema": "skill.plan", "ok": True, "selected_skills": [{"name": "open-code-review-delegate"}]},
        "membership": {
            "schema": "membership.snapshot",
            "ok": True,
            "contracts": {"workflow": {"health_commands": [{"owner": "workflow", "args": ["validate"]}]}},
            "mirror_source_projection": {"members": [member]},
        },
        "maintenance": {
            "schema": "maintenance.coverage",
            "ok": True,
            "member_dispositions": [{"member_id": member["member_id"], "disposition": "direct_capability"}],
        },
        "mcp": {
            "schema": "mcp.lookup",
            "ok": True,
            "matches": [{"capability": "review_support", "owner_profile": "ocr", "permission_boundary": "read_only"}],
        },
        "quality": {
            "schema": "skill.quality",
            "ok": True,
            "last_recorded_at": "2026-07-27T00:00:00Z",
            "skills": {"open-code-review-delegate": {"applied": 1, "completed": 1}},
        },
        "scheduler": {"schema": "scheduler.drift", "ok": True},
        "rules": {"schema": "rules.snapshot", "ok": True, "activation": {"coverage_complete": True}},
    }


class BusinessEnvironmentCapabilityResolverTests(unittest.TestCase):
    def build(self, message: str) -> dict[str, object]:
        with patch("business_environment_capability_resolver.tool_utilization_audit.declared_activation_cases", return_value=ACTIVATION_CASES):
            return build_information_package(message, **authorities())

    def test_five_business_categories_get_minimal_combinations(self) -> None:
        for expected, message in SCENARIOS.items():
            with self.subTest(expected=expected):
                result = self.build(message)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["business_category"], expected)
                self.assertTrue(result["minimal_capability_combination"]["business_owner"])
                self.assertTrue(all(row["role"] == "supporting_tool_not_business_owner" for row in result["minimal_capability_combination"]["supporting_tools"]))
                self.assertTrue(result["permission_boundaries"])

    def test_content_owner_resolves_to_selected_domain_skill(self) -> None:
        values = authorities()
        values["skill_plan"] = {"schema": "skill.plan", "ok": True, "selected_skills": [{"name": "baoyu-slide-deck"}]}
        with patch("business_environment_capability_resolver.tool_utilization_audit.declared_activation_cases", return_value=ACTIVATION_CASES):
            result = build_information_package(SCENARIOS["content_production"], **values)

        self.assertEqual(result["minimal_capability_combination"]["business_owner"], "skill:baoyu-slide-deck")

    def test_asset_fields_are_complete_and_default_is_bounded(self) -> None:
        result = self.build(SCENARIOS["software_engineering"])
        self.assertEqual(result["asset_summary"]["field_coverage_percent"], 100.0)
        self.assertTrue(result["asset_summary"]["installed_only_blocked_with_deadline"])
        self.assertTrue(result["asset_summary"]["utilization_gaps_actionable"])
        self.assertLessEqual(len(result["assets"]), 8)
        self.assertTrue(result["asset_detail_ref"].startswith("command:"))

    def test_consumed_review_evidence_is_not_installed_only(self) -> None:
        result = self.build(SCENARIOS["software_engineering"])
        review = next(row for row in result["_all_assets"] if row["asset_id"] == "code_review.delegation_mode")
        self.assertEqual(review["utilization_acceptance"]["state"], "observed_consumed")
        self.assertEqual(result["asset_summary"]["installed_only_count"], 0)

    def test_signature_invalidates_when_decision_evidence_changes(self) -> None:
        baseline = authorities()
        baseline["quality"]["skills"]["diagnose"] = {"selected": 2, "applied": 1, "completed": 1}
        changed_usage = copy.deepcopy(baseline)
        changed_usage["quality"]["skills"]["diagnose"]["applied"] = 2
        changed_boundary = copy.deepcopy(baseline)
        changed_boundary["mcp"]["matches"][0]["permission_boundary"] = "approval_required"

        with patch("business_environment_capability_resolver.tool_utilization_audit.declared_activation_cases", return_value=ACTIVATION_CASES):
            first = build_information_package(SCENARIOS["software_engineering"], **baseline)
            second = build_information_package(SCENARIOS["software_engineering"], **changed_usage)
            third = build_information_package(SCENARIOS["software_engineering"], **changed_boundary)

        self.assertEqual(first["asset_summary"]["selected_but_unused"], ["diagnose"])
        self.assertNotEqual(first["evidence_signature"], second["evidence_signature"])
        self.assertNotEqual(first["evidence_signature"], third["evidence_signature"])


if __name__ == "__main__":
    unittest.main()
