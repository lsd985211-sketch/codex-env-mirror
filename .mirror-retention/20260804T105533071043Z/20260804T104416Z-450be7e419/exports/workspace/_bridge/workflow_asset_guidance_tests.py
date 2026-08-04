#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest.mock import patch

from workflow_asset_guidance import build_asset_guidance


class WorkflowAssetGuidanceTests(unittest.TestCase):
    def test_simple_task_has_no_composed_asset_route(self) -> None:
        result = build_asset_guidance(
            {"message": "把 hello 翻译成中文", "domains": [{"key": "general"}], "structured_route": {"task_contract": {}}},
            route_decision={"ambiguity": {"is_ambiguous": True}}, tool_policies=[], environment_context={}, resource_gate={},
        )
        self.assertFalse(result["active"])
        self.assertTrue(result["capability_bundle"]["simple_fast_path"])

    def test_current_date_task_also_stays_on_the_simple_fast_path(self) -> None:
        result = build_asset_guidance(
            {
                "message": "今天日期是什么",
                "domains": [{"key": "general"}],
                "skill_orchestration": {"ok": True, "selected_skills": [{"name": "irrelevant-helper"}]},
                "structured_route": {"task_contract": {}},
            },
            route_decision={"ambiguity": {"is_ambiguous": True}},
            tool_policies=[{"key": "irrelevant-support"}], environment_context={}, resource_gate={},
        )
        self.assertFalse(result["active"])

    def test_current_date_ignores_soft_external_knowledge_candidate(self) -> None:
        result = build_asset_guidance(
            {
                "message": "今天日期是什么",
                "domains": [{"key": "general"}],
                "structured_route": {
                    "task_contract": {
                        "task_facts": {
                            "external_knowledge_candidate": True,
                            "local_write": False,
                            "external_network_read": False,
                            "gui_or_browser_state": False,
                        }
                    }
                },
            },
            route_decision={"ambiguity": {"is_ambiguous": True}},
            tool_policies=[{"key": "irrelevant-support"}],
            environment_context={
                "environment_decision_frame": {
                    "schema": "workflow_environment_decision_frame.v1",
                    "decision_mode": "judgment_required",
                }
            },
            resource_gate={},
        )

        self.assertFalse(result["active"])
        self.assertTrue(result["capability_bundle"]["simple_fast_path"])

    def test_simple_term_with_substantive_fact_still_routes(self) -> None:
        result = build_asset_guidance(
            {
                "message": "把当前日期写入配置文件",
                "domains": [{"key": "general"}],
                "structured_route": {
                    "task_contract": {"task_facts": {"local_write": True}}
                },
            },
            route_decision={
                "ambiguity": {"is_ambiguous": False},
                "required_gates": [{"schema": "local_write_approval"}],
            },
            tool_policies=[],
            environment_context={
                "environment_decision_frame": {
                    "schema": "workflow_environment_decision_frame.v1",
                    "decision_mode": "hard_gate",
                    "hard_gates": [{"schema": "local_write_approval"}],
                }
            },
            resource_gate={},
        )

        self.assertTrue(result["active"])
        self.assertFalse(result["capability_bundle"]["simple_fast_path"])

    def test_content_skill_becomes_primary_owner_without_replacing_support_assets(self) -> None:
        result = build_asset_guidance(
            {
                "message": "把文章制作成带配图的演示文稿",
                "domains": [{"key": "content_production", "drives_execution": True, "systems": []}],
                "skill_orchestration": {"ok": True, "selected_skills": [{"name": "baoyu-slide-deck", "reasons": ["domain"]}]},
                "structured_route": {"task_contract": {}},
            },
            route_decision={"primary_domain": "general", "required_gates": []},
            tool_policies=[], environment_context={}, resource_gate={},
        )
        self.assertTrue(result["active"])
        self.assertEqual(result["primary_owner"]["name"], "skill:baoyu-slide-deck")
        self.assertEqual(result["capability_bundle"]["status"], "ready")
        self.assertLessEqual(len(result["capability_bundle"]["assets"]), 8)
        self.assertEqual(result["domain_projection"]["scenario"], "content_production")
        self.assertIn("domain_validation", {row["role"] for row in result["domain_projection"]["assets"]})

    def test_windows_desktop_filters_docs_and_keeps_runtime_owner(self) -> None:
        result = build_asset_guidance(
            {
                "message": "读取 Windows 微信桌面界面并验证",
                "domains": [{"key": "bridge", "drives_execution": True, "systems": ["bridge"]}],
                "skill_orchestration": {"ok": True, "selected_skills": [{"name": "gui-app-weixin"}]},
                "structured_route": {"task_contract": {"task_facts": {"gui_or_browser_state": True}}},
            },
            route_decision={"primary_owner": "windows_execution_agent", "primary_domain": "bridge", "required_gates": [], "owner_route": {"mcp_profile": "microsoftdocs"}},
            tool_policies=[], environment_context={}, resource_gate={},
        )
        self.assertEqual(result["primary_owner"]["name"], "windows_execution_agent")
        self.assertEqual(result["domain_projection"]["excluded_support"][0]["name"], "microsoftdocs")
        self.assertNotIn("microsoftdocs", {row["name"] for row in result["tools"]})

    def test_ambiguous_general_task_keeps_decision_frame_active(self) -> None:
        frame = {
            "schema": "workflow_environment_decision_frame.v1",
            "decision_mode": "judgment_required",
            "post_gate_mode": "judgment_required",
            "candidates": [{"authority_ref": "maintenance:workflow"}],
            "judgment_rule": "Codex selects or probes",
        }
        result = build_asset_guidance(
            {"message": "分析一个未知的本地问题", "domains": [{"key": "general"}], "structured_route": {"task_contract": {}}},
            route_decision={
                "primary_domain": "general",
                "required_gates": [],
                "ambiguity": {"is_ambiguous": True},
                "required_next_action": "clarify_or_run_read_only_route_probe",
            },
            tool_policies=[],
            environment_context={"environment_decision_frame": frame},
            resource_gate={},
        )

        self.assertTrue(result["active"])
        self.assertEqual(result["capability_bundle"]["status"], "judgment_required")
        self.assertEqual(result["environment_decision_frame"]["decision_mode"], "judgment_required")

    def test_capability_view_surfaces_ocr_contract_without_overriding_owner(self) -> None:
        result = build_asset_guidance(
            {
                "message": "识别本机 OCR 能力并处理扫描 PDF",
                "domains": [{"key": "gui_browser", "drives_execution": True, "systems": ["gui"]}],
                "skill_orchestration": {"ok": True, "selected_skills": [{"name": "pdf", "path": "/skills/pdf/SKILL.md"}]},
                "structured_route": {"task_contract": {}},
            },
            route_decision={"primary_owner": "pdf_owner", "primary_domain": "gui_browser", "required_gates": []},
            tool_policies=[],
            environment_context={},
            resource_gate={},
        )
        self.assertEqual(result["primary_owner"]["name"], "pdf_owner")
        self.assertEqual(result["capability_view"]["primary_owner"], "pdf_owner")
        self.assertTrue(any(row["capability_id"] == "skill:ppocrv5" for row in result["capability_view"]["candidates"]))
        self.assertIn(result["capability_view_decision_mode"], {"codex_select", "auto_fast_path", "auto_fallback"})
        self.assertEqual(result["primary_owner"]["capability_view"]["schema"], "environment_capability_view.v1")

    @patch("workflow_asset_guidance.semantic_capability_owner.select")
    @patch("workflow_asset_guidance.semantic_capability_owner.status")
    def test_bge_semantic_route_keeps_textbook_owner_and_fts_graph_fallback(self, status, select) -> None:
        status.return_value = {
            "callable": True,
            "healthy": True,
            "vector_reason": "probe_ok",
            "fallback": "structured_fts_graph",
            "capability_id": "model:bge-m3",
            "model": "bge-m3",
        }
        select.return_value = {
            "ok": True,
            "vector_used": True,
            "vector_reason": "dense_rrf",
            "selected": None,
            "ambiguous": True,
            "selection_reason": "codex_judgment_required",
            "results": [],
        }
        result = build_asset_guidance(
            {
                "message": "用 BGE-M3 做语义检索与概念归一",
                "domains": [{"key": "general"}],
                "structured_route": {"task_contract": {}},
            },
            route_decision={"primary_domain": "general", "required_gates": []},
            tool_policies=[],
            environment_context={
                "semantic_capabilities": [{
                    "capability_id": "model:bge-m3",
                    "name": "BGE-M3",
                    "source_authority": "semantic_capability_owner",
                    "entry_point": "python _bridge/semantic_capability_owner.py status --probe; embed|rerank|hybrid-rerank|select",
                    "fallback_route": "source-bound FTS + graph",
                    "business_scope": "通用语义能力优先；教材知识包 consumer 继续负责业务验收",
                    "business_results": {
                        "semantic_modes": ["chapter_entry_semantic_retrieval", "concept_normalization"],
                    },
                }],
                "environment_decision_frame": {},
            },
            resource_gate={},
        )
        tool = next(item for item in result["tools"] if item["name"] == "bge-m3-semantic-owner")
        self.assertEqual(tool["authority"], "semantic_capability_owner")
        self.assertIn("FTS + graph", tool["fallback"])
        self.assertIn("教材知识包", tool["business_scope"])
        self.assertIn("concept_normalization", tool["semantic_modes"])
        self.assertIn("vector_used", tool["result_contract"])
        self.assertIn("source_ref", tool["result_contract"])
        self.assertIn("hybrid-rerank", tool["action"])
        status.assert_called_once_with(probe=True)
        self.assertEqual(result["semantic_probe"]["vector_reason"], "probe_ok")


if __name__ == "__main__":
    unittest.main()
