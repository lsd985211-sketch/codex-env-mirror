#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from business_environment_control_plane import BUSINESS_ACCEPTANCE_SCENARIOS, build_final_acceptance, build_plan
from code_maintainability import placement_plan
from skill_orchestrator import build_plan as build_skill_plan


GLOBAL_GOVERNANCE_MESSAGE = (
    "彻底治理当前 Codex 工作环境，去除淘汰机制、充分利用先进资产、"
    "自动服务未来业务并持续演进"
)
MILESTONE_B_MESSAGE = "继续执行业务环境受控内核重建里程碑 B：业务入口与资产利用闭环"
EXTERNAL_RESEARCH_MESSAGE = "联网搜索官方资料并形成有引用的研究结论"
CONTENT_PRODUCTION_MESSAGE = "制作并验证一份业务演示文稿"


def skill_context() -> dict[str, object]:
    return {
        "lifecycle_refresh": {
            "records": [],
            "summary": {},
            "state_db": "test-only",
        },
        "inventory": {
            "global-framework": {
                "name": "global-framework",
                "description": "Global workflow and governance routing",
                "path": "/tmp/global-framework/SKILL.md",
                "source": "test",
                "scenarios": [],
                "needsAttention": False,
            }
        },
        "lifecycle_records_by_path": {},
        "quality_by_key": {},
        "discovered_skill_keys": {"global-framework"},
        "myskills_inventory_path": None,
    }


class BusinessEnvironmentControlPlaneTests(unittest.TestCase):
    def final_outcomes(self) -> list[dict[str, object]]:
        return [
            {
                "category": category,
                "accepted": True,
                "consumed": True,
                "result_ref": f"owner-result:{category}",
                "evidence_refs": [f"sha256:{index:064x}"],
            }
            for index, category in enumerate(BUSINESS_ACCEPTANCE_SCENARIOS, start=1)
        ]

    def test_final_acceptance_requires_all_five_consumed_owner_results(self) -> None:
        owners = {
            "recovery": {"ok": True, "capability_restore_ready": True},
            "membership": {"ok": True, "retirement_tombstone_count": 0, "retirement_tombstones": []},
            "review_queue": {"ok": True, "counts": {"resolved": 1}, "pending": []},
            "scheduler": {"ok": True, "drift_count": 0},
            "durable_executor": {"ok": True, "checks": [{"name": "no_business_resubmit_contract", "ok": True}]},
        }
        complete = build_final_acceptance(self.final_outcomes(), owner_results=owners)
        self.assertTrue(complete["ok"], complete)
        self.assertEqual(complete["retirement"]["disposition"], "no-removal")
        self.assertEqual(complete["business_outcome_summary"]["consumed_count"], 5)

        owners["scheduler"] = {
            "ok": True,
            "missing_task_ids": [],
            "runtime_only_task_ids": [],
            "changed_tasks": [],
        }
        self.assertTrue(build_final_acceptance(self.final_outcomes(), owner_results=owners)["ok"])

        missing = build_final_acceptance(self.final_outcomes()[:-1], owner_results=owners)
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["business_outcome_summary"]["missing_categories"], ["cross_system_automation"])

        unconsumed = self.final_outcomes()
        unconsumed[0]["consumed"] = False
        self.assertFalse(build_final_acceptance(unconsumed, owner_results=owners)["ok"])

    def test_final_acceptance_does_not_treat_route_success_as_outcome_consumption(self) -> None:
        outcomes = self.final_outcomes()
        outcomes[0] = {"category": "software_engineering", "route_ok": True, "accepted": True, "consumed": True}
        result = build_final_acceptance(outcomes, owner_results={})
        row = next(item for item in result["business_outcomes"] if item["category"] == "software_engineering")
        self.assertFalse(row["ok"])
        self.assertIn("owner_result_reference_missing", row["issues"])
        self.assertIn("acceptance_evidence_missing", row["issues"])

    def test_final_acceptance_fails_closed_for_duplicate_empty_evidence_and_owner_gaps(self) -> None:
        outcomes = self.final_outcomes()
        outcomes.append(dict(outcomes[0]))
        outcomes[1]["evidence_refs"] = [""]
        result = build_final_acceptance(outcomes, owner_results={})
        self.assertFalse(result["ok"])
        self.assertEqual(result["business_outcome_summary"]["duplicate_categories"], ["software_engineering"])
        self.assertIn("acceptance_evidence_missing", result["business_outcomes"][1]["issues"])
        self.assertEqual(
            result["failed_owner_gates"],
            ["durable_executor", "membership", "recovery", "review_queue", "scheduler"],
        )

    def test_final_acceptance_requires_membership_owner_to_close_retirements(self) -> None:
        owners = {
            "recovery": {"ok": True, "capability_restore_ready": True},
            "membership": {"ok": True, "retirement_tombstone_count": 1, "retirement_tombstones": [{"member_id": "old"}]},
            "review_queue": {"ok": True, "pending": []},
            "scheduler": {"ok": True, "drift_count": 0},
            "durable_executor": {"ok": True, "checks": {"no_business_resubmit_contract": True}},
        }
        blocked = build_final_acceptance(self.final_outcomes(), owner_results=owners)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["retirement"]["disposition"], "retirement-owner-acceptance-required")
        owners["membership"]["retirement_acceptance"] = {"ok": True, "accepted_tombstone_count": 1}
        self.assertTrue(build_final_acceptance(self.final_outcomes(), owner_results=owners)["ok"])

    def test_layer_local_domain_labels_do_not_create_false_authority_conflicts(self) -> None:
        common = {
            "placement": {"ok": True, "schema": "placement.v1", "owner_module": "_bridge/business_environment_control_plane.py"},
            "membership": {"ok": True, "schema": "membership.v1", "mirror_source_projection": {"members": []}},
            "maintenance": {"ok": True, "schema": "maintenance.v1", "member_dispositions": []},
        }
        cases = [
            (EXTERNAL_RESEARCH_MESSAGE, "external_docs_research", "web_research"),
            (CONTENT_PRODUCTION_MESSAGE, "general", "slide_deck"),
        ]
        for message, workflow_domain, skill_domain in cases:
            with self.subTest(message=message), patch(
                "business_environment_control_plane.business_environment_capability_resolver.build_information_package",
                return_value={
                    "ok": True,
                    "business_category": "external_research" if skill_domain == "web_research" else "content_production",
                    "evidence_signature": "evidence",
                    "_all_assets": [],
                },
            ):
                result = build_plan(
                    message,
                    workflow_plan={"ok": True, "schema": "workflow.v1", "domains": [{"key": workflow_domain}]},
                    skill_plan={"ok": True, "schema": "skills.v1", "domains": [{"key": skill_domain}], "selected_skills": [{"name": "domain-skill"}]},
                    **common,
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["authority_conflicts"], [])
    def test_global_governance_message_selects_workflow_skill(self) -> None:
        result = build_skill_plan(GLOBAL_GOVERNANCE_MESSAGE, routing_context=skill_context())

        self.assertIn("workflow_governance", [item["key"] for item in result["domains"]])
        self.assertIn("global-framework", [item["name"] for item in result["selected_skills"]])

    def test_milestone_b_message_selects_governance_without_unrelated_mcp_builder(self) -> None:
        result = build_skill_plan(MILESTONE_B_MESSAGE, routing_context=skill_context())
        selected = [item["name"] for item in result["selected_skills"]]

        self.assertIn("workflow_governance", [item["key"] for item in result["domains"]])
        self.assertIn("global-framework", selected)
        self.assertNotIn("mcp-builder", selected)

    def test_global_control_plane_placement_never_falls_into_local_maintenance(self) -> None:
        args = argparse.Namespace(
            root=None,
            all_bridge=False,
            include_excluded=False,
            term=["business", "environment", "control", "maintenance", "governance"],
            task_mode="code",
            message="建立面向整个 Codex 工作环境的全局业务环境控制面和自动维护编排入口",
            target="",
            limit=8,
            issue_limit=20,
            large_file_lines=1200,
            large_function_lines=160,
            large_function_decisions=30,
            json=False,
        )

        result = placement_plan(args)

        self.assertEqual(result["owner_module"], "_bridge/business_environment_control_plane.py")
        self.assertNotIn("mobile", result["owner_module"])
        self.assertNotIn("record_store_maintenance", result["owner_module"])

    def test_shadow_plan_is_stable_and_uses_authority_references(self) -> None:
        workflow = {
            "ok": True,
            "schema": "workflow_orchestrator.plan.v1",
            "domains": [{"key": "workflow_governance"}],
            "execution_route_pack": {"route_decision": {"required_gates": []}},
        }
        skills = {
            "ok": True,
            "schema": "skill_orchestrator.plan.v1",
            "domains": [{"key": "workflow_governance"}],
            "selected_skills": [{"name": "global-framework"}],
            "tools": {"suggested": []},
        }
        placement = {
            "ok": True,
            "schema": "code_maintainability.placement_plan.v1",
            "owner_module": "_bridge/business_environment_control_plane.py",
            "recommended_placement": "owner_module",
        }
        membership = {
            "ok": True,
            "schema": "system_membership.v2.snapshot",
            "systems": ["workflow"],
            "mirror_source_projection": {
                "members": [{"member_id": "workspace-bridge-source", "system": "workflow", "owner": "wsl_workspace_owner", "lifecycle": "active"}]
            },
        }
        maintenance = {
            "ok": True,
            "schema": "maintenance_capability_registry.coverage.v1",
            "coverage_percent": 100.0,
            "active_member_count": 1,
            "unmapped_members": [],
        }

        first = build_plan(
            GLOBAL_GOVERNANCE_MESSAGE,
            workflow_plan=workflow,
            skill_plan=skills,
            placement=placement,
            membership=membership,
            maintenance=maintenance,
        )
        second = build_plan(
            GLOBAL_GOVERNANCE_MESSAGE,
            workflow_plan=workflow,
            skill_plan=skills,
            placement=placement,
            membership=membership,
            maintenance=maintenance,
        )

        self.assertTrue(first["ok"], first)
        self.assertTrue(first["read_only"])
        self.assertTrue(first["shadow_only"])
        self.assertEqual(first["input_signature"], second["input_signature"])
        self.assertEqual(first["decision"], second["decision"])
        self.assertEqual(first["authority_conflicts"], [])
        self.assertTrue(all(item["classification"] for item in first["asset_lifecycle"]))
        self.assertFalse(first["writes_authoritative_state"])
        self.assertEqual(len(first["authority_bindings"]), 5)
        self.assertEqual(first["durable_operation"]["input_signature"], first["input_signature"])
        self.assertEqual(first["business_outcome"]["state"], "shadow_decision_ready")

    def test_real_shadow_plan_uses_global_control_plane_placement(self) -> None:
        result = build_plan(GLOBAL_GOVERNANCE_MESSAGE)

        owner_refs = [
            item["ref"]
            for item in result["decision"]["selected_capabilities"]
            if item["kind"] == "owner_module"
        ]
        self.assertEqual(owner_refs, ["_bridge/business_environment_control_plane.py"])

    def test_live_shadow_plan_does_not_refresh_skill_lifecycle(self) -> None:
        with patch("business_environment_control_plane.skill_orchestrator.prepare_routing_context") as refresh:
            result = build_plan(GLOBAL_GOVERNANCE_MESSAGE)

        refresh.assert_not_called()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["authority_conflicts"], [])
        self.assertTrue(result["business_information_package"]["read_only"])

    def test_business_context_facade_exposes_completion_and_asset_summary(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(__file__).replace("business_environment_control_plane_tests.py", "codex_workflow_entry.py"),
                "business-environment",
                "context",
                "--message",
                GLOBAL_GOVERNANCE_MESSAGE,
            ],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(payload["read_only"])
        self.assertTrue(payload["completion_predicate"])
        self.assertEqual(payload["asset_summary"]["field_coverage_percent"], 100.0)

    def test_workflow_facade_exposes_read_only_shadow_plan(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(__file__).replace("business_environment_control_plane_tests.py", "codex_workflow_entry.py"),
                "business-environment",
                "plan",
                "--message",
                GLOBAL_GOVERNANCE_MESSAGE,
            ],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(payload["read_only"])
        self.assertTrue(payload["shadow_only"])
        self.assertFalse(payload["writes_authoritative_state"])


if __name__ == "__main__":
    unittest.main()
