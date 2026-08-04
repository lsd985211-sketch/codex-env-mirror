#!/usr/bin/env python3
from __future__ import annotations

import copy
import unittest
from unittest.mock import Mock, patch

from business_environment_capability_resolver import _category, build_capability_bundle, build_information_package


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

    def test_business_categories_get_minimal_combinations(self) -> None:
        for expected, message in SCENARIOS.items():
            with self.subTest(expected=expected):
                result = self.build(message)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["business_category"], expected)
                self.assertTrue(result["minimal_capability_combination"]["business_owner"])
                self.assertTrue(all(row["role"] == "supporting_tool_not_business_owner" for row in result["minimal_capability_combination"]["supporting_tools"]))
                self.assertTrue(result["permission_boundaries"])

    def test_specialized_local_routes_precede_maintenance_fallback(self) -> None:
        cases = {
            "通用 BGE-M3 路由优化，保持教材业务 owner 不变": ("semantic_retrieval", "semantic_capability_owner"),
            "识别扫描 PDF 并使用本机 OCR，不安装任何工具": ("document_processing", "pdf_owner"),
            "检索医学教材概念并给出引用": ("textbook_knowledge", "skill:medical-textbook-study"),
            "联网研究 BGE-M3 官方资料并给出引用": ("external_research", "resource_broker"),
            "修复 Python 模块中的函数调用问题": ("software_engineering", "codex_code_review_judgment"),
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                category = _category(message)
                self.assertEqual(expected, (category["id"], category["owner"]))

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

    def test_capability_bundle_keeps_primary_owner_and_bounded_support(self) -> None:
        bundle = build_capability_bundle(
            "把文章制作成带配图的演示文稿",
            workflow_plan={"domains": [{"key": "content_production"}]},
            route_decision={"primary_domain": "general", "required_gates": []},
            rules=[{"name": "workflow.task_contract"}],
            skills=[{"name": "baoyu-slide-deck", "reason": "domain skill"}],
            owners=[{"name": "presentation_owner", "reason": "domain owner"}],
            tools=[{"name": "render", "reason": "validation"}],
        )

        self.assertEqual(bundle["primary_owner"]["name"], "skill:baoyu-slide-deck")
        self.assertEqual(bundle["status"], "ready")
        self.assertLessEqual(len(bundle["assets"]), 8)
        self.assertTrue(all(row["authority_ref"] for row in bundle["assets"]))
        self.assertIn("consume", bundle["result_contract"]["consume_ref"])

    def test_explicit_python_module_repair_prefers_code_owner_not_generic_maintenance(self) -> None:
        bundle = build_capability_bundle(
            "修复 workflow_asset_guidance.py 的能力路由并运行回归",
            workflow_plan={"domains": [{"key": "general"}]},
            route_decision={"primary_domain": "general", "required_gates": []},
            rules=[], skills=[], owners=[], tools=[],
        )
        self.assertEqual(bundle["primary_owner"]["name"], "codex_code_review_judgment")

    def test_capability_view_is_bounded_and_reference_only(self) -> None:
        bundle = build_capability_bundle(
            "识别本机 OCR 能力",
            workflow_plan={"domains": [{"key": "gui_browser"}]},
            route_decision={"primary_domain": "gui_browser", "primary_owner": "pdf_owner", "required_gates": []},
            rules=[],
            skills=[{"name": "ppocrv5", "path": "/skills/ppocrv5/SKILL.md", "admission_state": "unregistered"}],
            owners=[],
            tools=[],
            capability_view={
                "schema": "environment_capability_view.v1",
                "source_signature": "sig",
                "decision_mode": "codex_select",
                "primary_owner": "pdf_owner",
                "candidates": [{"capability_id": "skill:ppocrv5", "admitted": False}],
                "issues": [{"code": "observed_candidates_not_admitted"}],
                "next_action": "codex_select_or_run_declared_minimal_probe",
            },
        )
        self.assertEqual(bundle["capability_view"]["schema"], "environment_capability_view.v1")
        self.assertEqual(bundle["capability_view"]["decision_mode"], "codex_select")
        self.assertEqual(bundle["capability_view"]["candidates"][0]["admitted"], False)
        self.assertLessEqual(len(bundle["capability_view"]["candidates"]), 6)

    def test_structured_external_research_uses_the_resource_fast_path(self) -> None:
        bundle = build_capability_bundle(
            "查询 Python 官方文档",
            workflow_plan={"domains": [{"key": "external_docs_research"}]},
            route_decision={
                "primary_domain": "external_docs_research",
                "required_gates": [],
                "resource_delegation_required": True,
            },
            rules=[], skills=[], owners=[], tools=[{"name": "resource-layer"}],
        )
        self.assertEqual(bundle["primary_owner"]["name"], "resource_broker")
        self.assertEqual(bundle["primary_owner"]["selection_reason"], "structured external research fast path")

    def test_explicit_owner_remains_authoritative_over_external_research_fast_path(self) -> None:
        bundle = build_capability_bundle(
            "代码任务需要查询 Python 官方文档",
            workflow_plan={"domains": [{"key": "external_docs_research"}]},
            route_decision={
                "primary_owner": "codex_code_review_judgment",
                "primary_domain": "external_docs_research",
                "required_gates": [],
                "resource_delegation_required": True,
            },
            rules=[], skills=[], owners=[], tools=[{"name": "resource-layer"}],
        )
        self.assertEqual(bundle["primary_owner"]["name"], "codex_code_review_judgment")

    def test_ambiguous_frame_keeps_automatic_category_as_unselected_recommendation(self) -> None:
        frame = {
            "schema": "workflow_environment_decision_frame.v1",
            "decision_mode": "judgment_required",
            "post_gate_mode": "judgment_required",
            "candidates": [
                {"authority_ref": "maintenance:workflow"},
                {"authority_ref": "maintenance:mcp"},
            ],
            "judgment_rule": "Codex selects an owner-backed candidate",
        }
        bundle = build_capability_bundle(
            "检查 Windows 桌面状态",
            workflow_plan={"domains": [{"key": "general"}]},
            route_decision={"primary_domain": "general", "required_gates": []},
            rules=[], skills=[], owners=[], tools=[], environment_decision_frame=frame,
        )

        self.assertEqual(bundle["status"], "judgment_required")
        self.assertEqual(bundle["primary_owner"]["decision_role"], "recommendation_only")
        self.assertIn("bounded automatic fallback", bundle["primary_owner"]["selection_reason"])
        self.assertTrue(bundle["required_decision"])
        self.assertEqual(bundle["environment_decision_frame"]["candidate_refs"], ["maintenance:workflow", "maintenance:mcp"])

    def test_explicit_owner_stays_ready_when_frame_is_ambiguous(self) -> None:
        bundle = build_capability_bundle(
            "检查 Windows 桌面状态",
            workflow_plan={"domains": [{"key": "general"}]},
            route_decision={"primary_owner": "windows_execution_agent", "primary_domain": "general", "required_gates": []},
            rules=[], skills=[], owners=[], tools=[],
            environment_decision_frame={"decision_mode": "judgment_required", "post_gate_mode": "judgment_required", "candidates": []},
        )
        self.assertEqual(bundle["status"], "ready")
        self.assertEqual(bundle["primary_owner"]["decision_role"], "selected_owner")

    def test_static_category_with_one_high_confidence_candidate_is_only_a_fast_recommendation(self) -> None:
        bundle = build_capability_bundle(
            "修复 Python 模块中的函数调用问题",
            workflow_plan={"domains": [{"key": "general"}]},
            route_decision={"primary_domain": "general", "required_gates": []},
            rules=[], skills=[], owners=[], tools=[],
            environment_decision_frame={
                "schema": "workflow_environment_decision_frame.v1",
                "decision_mode": "fast_recommendation",
                "post_gate_mode": "fast_recommendation",
                "candidates": [{"authority_ref": "maintenance:code"}],
            },
        )
        self.assertEqual(bundle["status"], "fast_recommendation")
        self.assertEqual(bundle["primary_owner"]["decision_role"], "recommendation_only")
        self.assertEqual(bundle["primary_owner"]["supporting_evidence_refs"], ["maintenance:code"])

    def test_bundle_promotes_signed_semantic_selection_without_replacing_owner(self) -> None:
        semantic_result = {
            "ok": True,
            "owner": "semantic_capability_owner",
            "vector_used": True,
            "vector_reason": "dense_plus_owner_ranks_rrf",
            "retrievers": ["dense", "lexical"],
            "results": [
                {"id": "skill:ppocrv5", "rrf_score": 0.04},
                {"id": "tool:web-ocr", "rrf_score": 0.02},
            ],
            "selected": "skill:ppocrv5",
            "ambiguous": False,
            "selection_margin": 0.02,
            "selection_reason": "semantic_margin_satisfied",
            "fallback": "caller_owned_lexical_graph_order",
            "fallback_used": False,
        }
        selector = Mock(return_value=semantic_result)
        bundle = build_capability_bundle(
            "识别扫描 PDF 并优先使用本机 OCR",
            workflow_plan={"domains": [{"key": "document_processing"}]},
            route_decision={"primary_owner": "pdf_owner", "primary_domain": "document_processing", "required_gates": []},
            rules=[], skills=[], owners=[], tools=[],
            capability_view={
                "schema": "environment_capability_view.v1",
                "source_signature": "capability-source",
                "primary_owner": "pdf_owner",
                "candidates": [
                    {
                        "candidate_id": "skill:ppocrv5", "capability_class": "skill", "role": "support",
                        "authority_ref": "skill_orchestrator", "source_ref": "skill:ppocrv5",
                        "entry_ref": "skill:ppocrv5", "fallback_ref": "fallback:manual-ocr",
                        "availability": "ready", "permission_state": "ready", "platform_ok": True,
                        "decision_relevant": True, "reason_codes": [], "content_sha256": "sha-ocr", "lexical_rank": 1,
                    },
                    {
                        "candidate_id": "tool:web-ocr", "capability_class": "tool", "role": "support",
                        "authority_ref": "workflow_asset_guidance", "source_ref": "tool:web-ocr",
                        "entry_ref": "tool:web-ocr", "fallback_ref": "fallback:manual-ocr",
                        "availability": "ready", "permission_state": "ready", "platform_ok": True,
                        "decision_relevant": True, "reason_codes": [], "content_sha256": "sha-web", "lexical_rank": 2,
                    },
                ],
            },
            semantic_selector=selector,
        )
        self.assertEqual("pdf_owner", bundle["primary_owner"]["name"])
        self.assertEqual("selected", bundle["capability_decision_receipt"]["status"])
        self.assertEqual("skill:ppocrv5", bundle["assets"][0]["name"])
        self.assertTrue(bundle["capability_decision_receipt"]["receipt_signature"])

    def test_exposed_bge_uses_existing_owner_by_default_but_is_not_ranked_as_candidate(self) -> None:
        capability_view = {
            "schema": "environment_capability_view.v1",
            "source_signature": "semantic-source",
            "primary_owner": "pdf_owner",
            "candidates": [
                {
                    "candidate_id": "model:bge-m3", "capability_class": "semantic_model", "role": "support",
                    "authority_ref": "semantic_capability_owner", "source_ref": "owner:bge-m3",
                    "entry_ref": "semantic_capability_owner.select", "fallback_ref": "fallback:fts-graph",
                    "availability": "unknown", "permission_state": "ready", "platform_ok": True,
                    "decision_relevant": True, "reason_codes": [], "content_sha256": "sha-bge",
                },
                {
                    "candidate_id": "skill:ppocrv5", "capability_class": "skill", "role": "support",
                    "authority_ref": "skill_orchestrator", "source_ref": "skill:ppocrv5",
                    "entry_ref": "skill:ppocrv5", "fallback_ref": "fallback:manual-ocr",
                    "availability": "ready", "permission_state": "ready", "platform_ok": True,
                    "decision_relevant": True, "reason_codes": [], "content_sha256": "sha-ocr", "lexical_rank": 1,
                },
                {
                    "candidate_id": "tool:web-ocr", "capability_class": "tool", "role": "support",
                    "authority_ref": "workflow_asset_guidance", "source_ref": "tool:web-ocr",
                    "entry_ref": "tool:web-ocr", "fallback_ref": "fallback:manual-ocr",
                    "availability": "ready", "permission_state": "ready", "platform_ok": True,
                    "decision_relevant": True, "reason_codes": [], "content_sha256": "sha-web", "lexical_rank": 2,
                },
            ],
        }
        with patch("business_environment_capability_resolver.semantic_capability_owner.select") as select:
            select.return_value = {
                "ok": True, "vector_used": True, "vector_reason": "dense_plus_owner_ranks_rrf",
                "retrievers": ["dense", "lexical"],
                "results": [{"id": "skill:ppocrv5", "rrf_score": 0.04}, {"id": "tool:web-ocr", "rrf_score": 0.02}],
                "selected": "skill:ppocrv5", "ambiguous": False, "selection_margin": 0.02,
                "selection_reason": "semantic_margin_satisfied", "fallback": "caller_owned_lexical_graph_order",
                "fallback_used": False,
            }
            bundle = build_capability_bundle(
                "识别扫描 PDF 并使用本机 OCR",
                workflow_plan={"domains": [{"key": "semantic_retrieval"}]},
                route_decision={"primary_owner": "pdf_owner", "primary_domain": "document_processing", "required_gates": []},
                rules=[], skills=[], owners=[], tools=[], capability_view=capability_view,
            )
        submitted = select.call_args.args[1]
        self.assertEqual(["skill:ppocrv5", "tool:web-ocr"], [row["id"] for row in submitted])
        self.assertEqual(2.0, select.call_args.kwargs["timeout"])
        self.assertEqual("skill:ppocrv5", bundle["assets"][0]["name"])

    def test_textbook_owner_survives_unavailable_general_semantic_model(self) -> None:
        selector = Mock()
        bundle = build_capability_bundle(
            "检索医学教材概念",
            workflow_plan={"domains": [{"key": "semantic_retrieval"}]},
            route_decision={"primary_owner": "skill:medical-textbook-study", "primary_domain": "semantic_retrieval", "required_gates": []},
            rules=[], skills=[], owners=[], tools=[],
            capability_view={
                "schema": "environment_capability_view.v1",
                "source_signature": "textbook-source",
                "primary_owner": "skill:medical-textbook-study",
                "candidates": [
                    {
                        "candidate_id": "model:bge-m3", "capability_class": "semantic_model", "role": "support",
                        "authority_ref": "semantic_capability_owner", "source_ref": "owner:bge-m3",
                        "entry_ref": "semantic_capability_owner.select", "fallback_ref": "textbook:fts-graph",
                        "availability": "ready", "permission_state": "ready", "platform_ok": True,
                        "decision_relevant": True, "reason_codes": [], "content_sha256": "sha-bge", "lexical_rank": 1,
                    },
                    {
                        "candidate_id": "textbook:fts", "capability_class": "owner", "role": "support",
                        "authority_ref": "medical_textbook", "source_ref": "textbook:fts",
                        "entry_ref": "textbook.search", "fallback_ref": "textbook:fts-graph",
                        "availability": "ready", "permission_state": "ready", "platform_ok": True,
                        "decision_relevant": True, "reason_codes": [], "content_sha256": "sha-fts", "lexical_rank": 2,
                    },
                ],
            },
            semantic_probe_result={
                "callable": False, "healthy": False,
                "vector_reason": "probe_failed:TimeoutError", "fallback": "structured_fts_graph",
            },
            semantic_selector=selector,
        )
        selector.assert_not_called()
        self.assertEqual("skill:medical-textbook-study", bundle["primary_owner"]["name"])
        self.assertEqual("ready", bundle["status"])
        self.assertEqual("judgment_required", bundle["capability_decision_receipt"]["status"])
        self.assertEqual("probe_failed:TimeoutError", bundle["capability_decision_receipt"]["ranking"]["vector_reason"])
        self.assertNotIn("embeddings", str(bundle))


if __name__ == "__main__":
    unittest.main()
