#!/usr/bin/env python3
from __future__ import annotations

import unittest

from workflow_asset_domain_projection import build_domain_projection


class WorkflowAssetDomainProjectionTests(unittest.TestCase):
    def project(self, message: str, owner: str, *, skills=None, tools=None, facts=None):
        return build_domain_projection(
            message,
            primary_owner=owner,
            skills=skills or [],
            owners=[],
            tools=tools or [],
            task_facts=facts or {},
        )

    def test_code_projection_references_existing_discovery_and_validation(self) -> None:
        result = self.project("修复 Python 模块并运行回归", "codex_code_review_judgment")
        refs = {item["authority_ref"] for item in result["assets"]}
        self.assertEqual(result["scenario"], "code_maintenance")
        self.assertIn("code_maintainability.module-assets", refs)
        self.assertIn("mcp_capability_routes.lookup:code_structure", refs)
        self.assertIn("maintenance_capability_registry.query", refs)
        self.assertIn("code_maintainability.placement-plan", refs)

    def test_primary_code_owner_is_not_overridden_by_resource_support(self) -> None:
        result = self.project(
            "修复 Python 模块中的函数调用问题并验证影响范围",
            "codex_code_review_judgment",
            tools=[{"name": "resource-layer"}],
        )
        self.assertEqual(result["scenario"], "code_maintenance")

    def test_primary_maintenance_owners_are_not_overridden_by_resource_support(self) -> None:
        for owner in ("maintenance_upgrade_governance", "workflow_governance"):
            with self.subTest(owner=owner):
                result = self.project(
                    "修复工作环境维护流程并参考现有资料",
                    owner,
                    tools=[{"name": "resource-layer"}],
                )
                self.assertEqual(result["scenario"], "system_maintenance")

    def test_research_projection_keeps_one_request_result_lifecycle(self) -> None:
        result = self.project("联网研究并形成引用报告", "resource_broker", tools=[{"name": "resource-layer"}])
        refs = {item["authority_ref"] for item in result["assets"]}
        self.assertIn("execution_route_pack.resource_gate", refs)
        self.assertIn("resource_cli.job.consume", refs)
        self.assertTrue(result["acceptance"]["selection_is_not_completion"])

    def test_windows_projection_filters_documentation_support(self) -> None:
        result = self.project(
            "读取 Windows 微信桌面并验证界面",
            "windows_execution_agent",
            skills=[{"name": "gui-app-weixin"}],
            tools=[{"name": "microsoftdocs"}, {"name": "browser-or-gui-owner"}],
            facts={"gui_or_browser_state": True},
        )
        self.assertEqual(result["scenario"], "windows_desktop")
        self.assertEqual([item["name"] for item in result["filtered_tools"]], ["browser-or-gui-owner"] )
        self.assertEqual(result["excluded_support"][0]["name"], "microsoftdocs")

    def test_primary_windows_owner_is_not_overridden_by_resource_support(self) -> None:
        result = self.project(
            "执行 Windows 平台操作",
            "windows_execution_agent",
            tools=[{"name": "resource-layer"}],
        )
        self.assertEqual(result["scenario"], "windows_desktop")

    def test_content_projection_references_skill_inputs_and_target_validation(self) -> None:
        result = self.project(
            "把文章制作成带配图的演示文稿",
            "skill:baoyu-slide-deck",
            skills=[{"name": "baoyu-slide-deck"}],
            tools=[{"name": "resource-layer"}],
        )
        roles = {item["role"] for item in result["assets"]}
        self.assertEqual(result["scenario"], "content_production")
        self.assertIn("production_inputs", roles)
        self.assertIn("domain_validation", roles)

    def test_unknown_route_stays_quiet(self) -> None:
        result = self.project("普通任务", "general")
        self.assertFalse(result["active"])
        self.assertEqual(result["assets"], [])


if __name__ == "__main__":
    unittest.main()
