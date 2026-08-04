#!/usr/bin/env python3
from __future__ import annotations

import unittest

from workflow_plan_detail import apply_detail_level, compact_asset_guidance


class WorkflowPlanDetailTests(unittest.TestCase):
    def test_capability_bundle_stays_bounded_and_consumable(self) -> None:
        guidance = {
            "schema": "workflow_asset_guidance.v1",
            "active": True,
            "reason": "derived",
            "primary_owner": {"name": "resource_broker", "authority_ref": "route.primary_owner"},
            "capability_bundle": {
                "schema": "business_environment.capability_bundle.v1",
                "simple_fast_path": False,
                "status": "ready",
                "primary_owner": {"name": "resource_broker"},
                "assets": [{"name": f"asset-{index}"} for index in range(10)],
                "result_contract": {"consume_ref": "codex_workflow_entry.consume"},
                "evidence_signature": "signature",
                "expand": [f"ref-{index}" for index in range(8)],
            },
            "domain_projection": {
                "schema": "workflow_asset_domain_projection.v1",
                "scenario": "external_research",
                "assets": [{"name": f"domain-{index}"} for index in range(5)],
                "acceptance": {"predicate": "consume same request"},
                "expand": [f"domain-ref-{index}" for index in range(6)],
            },
            "sequence": [],
            "rules": [],
            "skills": [],
            "owners": [],
            "tools": [],
            "fallback": "forward fallback",
        }

        micro = compact_asset_guidance(guidance, "micro")
        standard = compact_asset_guidance(guidance, "standard")

        self.assertEqual(micro["primary_owner"]["name"], "resource_broker")
        self.assertEqual(micro["capability_bundle"]["asset_count"], 10)
        self.assertEqual(micro["capability_bundle"]["expand"], [])
        self.assertEqual(standard["capability_bundle"]["expand"], ["ref-0", "ref-1", "ref-2", "ref-3"])
        self.assertEqual(standard["capability_bundle"]["result_contract"]["consume_ref"], "codex_workflow_entry.consume")
        self.assertEqual(micro["domain_projection"]["scenario"], "external_research")
        self.assertEqual(micro["domain_projection"]["asset_count"], 5)
        self.assertEqual(micro["domain_projection"]["expand"], [])
        self.assertEqual(standard["domain_projection"]["expand"], ["domain-ref-0", "domain-ref-1", "domain-ref-2", "domain-ref-3"])

    def test_decision_frame_survives_bounded_asset_projection(self) -> None:
        guidance = {
            "schema": "workflow_asset_guidance.v1",
            "active": True,
            "decision_mode": "judgment_required",
            "environment_decision_frame": {
                "schema": "workflow_environment_decision_frame.v1",
                "decision_mode": "judgment_required",
                "post_gate_mode": "judgment_required",
                "hard_gates": [{"fact": "local_write", "authority_ref": "task.local_write"}],
                "candidates": [
                    {
                        "id": "workflow-owner",
                        "system": "workflow",
                        "authority_ref": "maintenance:workflow",
                        "applicability": "Owns the route lifecycle",
                        "availability": "ready",
                        "freshness": {"source_signature": "workflow-sig"},
                        "cost": {"discovery": "already_paid_read_view"},
                        "disambiguation": {"probe_ref": "workflow validate"},
                        "expand_ref": "workflow validate",
                        "confidence": "limited",
                    },
                    {
                        "id": "resource-owner",
                        "system": "resource",
                        "authority_ref": "maintenance:resource",
                        "applicability": "Owns structured research",
                        "availability": "ready",
                        "freshness": {"source_signature": "resource-sig"},
                        "cost": {"discovery": "already_paid_read_view"},
                        "disambiguation": {"probe_ref": "resource inspect"},
                        "expand_ref": "resource inspect",
                        "confidence": "high",
                    },
                ],
                "judgment_rule": "Codex selects or probes.",
            },
            "capability_bundle": {"schema": "business_environment.capability_bundle.v1", "status": "judgment_required"},
        }

        micro = compact_asset_guidance(guidance, "micro")
        standard = compact_asset_guidance(guidance, "standard")

        self.assertEqual(micro["decision_mode"], "judgment_required")
        self.assertEqual(micro["environment_decision_frame"]["candidate_refs"], ["maintenance:workflow", "maintenance:resource"])
        self.assertEqual(micro["environment_decision_frame"]["probe_refs"], ["workflow validate", "resource inspect"])
        self.assertEqual(standard["environment_decision_frame"]["candidates"][0]["availability"], "ready")
        self.assertEqual(standard["environment_decision_frame"]["candidates"][0]["freshness"]["source_signature"], "workflow-sig")
        self.assertEqual(standard["environment_decision_frame"]["candidates"][1]["expand_ref"], "resource inspect")
        self.assertLessEqual(len(standard["environment_decision_frame"]["candidates"]), 3)

    def test_real_micro_plan_keeps_system_change_gates_and_asset_projection(self) -> None:
        from workflow_orchestrator import build_plan

        full = build_plan("新增一个 MCP server 并纳入工作环境", detail="full")
        micro = apply_detail_level(full, "micro")

        route_pack = micro["execution_route_pack"]
        decision = route_pack["route_decision"]
        self.assertTrue(decision["required_gates"])
        self.assertIn("system_member_change", decision["task_facts"])
        self.assertTrue(decision["policy_decisions"])
        self.assertTrue(decision["stop_if"])
        self.assertTrue(route_pack["asset_guidance"]["active"])
        self.assertTrue(route_pack["asset_guidance"]["domain_projection"]["scenario"])
        environment = route_pack["environment_context"]
        self.assertIn("workflow", {item["system"] for item in environment["relevant_systems"]})
        self.assertTrue(environment["architecture_chain"])
        self.assertTrue(environment["tool_entrypoints"])
        self.assertTrue(environment["source_refs"])
        self.assertFalse(micro.get("compression_blocked", False))

    def test_semantic_capability_survives_micro_projection(self) -> None:
        from workflow_orchestrator import build_plan

        micro = build_plan("用 BGE-M3 做语义检索与概念归一", detail="micro")
        context = micro["execution_route_pack"]["environment_context"]
        model = context["semantic_capabilities"][0]
        self.assertEqual(model["capability_id"], "model:bge-m3")
        self.assertEqual(model["owner"], "semantic_capability_owner")
        self.assertIn("business_results", model)
        self.assertIn("concept_normalization", model["business_results"]["semantic_modes"])
        self.assertEqual(model["business_results"]["consumer_adapters"][0]["id"], "textbook_knowledge")


if __name__ == "__main__":
    unittest.main()
