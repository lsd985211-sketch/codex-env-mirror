#!/usr/bin/env python3

import unittest
from unittest.mock import patch

import workflow_environment_context as context


class WorkflowEnvironmentContextTests(unittest.TestCase):
    def test_decision_frame_exposes_bounded_owner_backed_candidates(self) -> None:
        result = context.build_environment_context(
            message="检查 workflow 和 resource owner",
            domain_keys=["workflow_governance", "resource_acquisition"],
            task_facts={},
            selected_skills=[],
            matrix_terms=["workflow", "resource"],
        )

        frame = result["environment_decision_frame"]
        self.assertLessEqual(len(frame["candidates"]), 3)
        self.assertTrue(all(row["authority_ref"] for row in frame["candidates"]))
        self.assertTrue(all(row["expand_ref"] for row in frame["candidates"]))
        self.assertIn(frame["decision_mode"], {"fast_recommendation", "judgment_required"})
        self.assertTrue(all(row["candidate_id"] and row["entry_ref"] for row in frame["candidates"]))
        self.assertTrue(all(row["availability"] in {"ready", "unknown", "unavailable"} for row in frame["candidates"]))

    def test_task_fact_gate_is_separate_from_post_gate_judgment(self) -> None:
        result = context.build_environment_context(
            message="联网查询官方资料",
            domain_keys=["external_docs_research"],
            task_facts={"external_network_read": True},
            selected_skills=["agent-reach"],
            matrix_terms=["documentation"],
        )

        frame = result["environment_decision_frame"]
        self.assertEqual(frame["decision_mode"], "hard_gate")
        self.assertEqual(frame["hard_gates"][0]["fact"], "external_network_read")
        self.assertIn(frame["post_gate_mode"], {"fast_recommendation", "judgment_required"})

    def test_environment_context_passes_registry_snapshot_to_batch_owner(self) -> None:
        import workflow_environment_context as module

        marker = object()
        with patch.object(module, "query_registry_batch", wraps=module.query_registry_batch) as query:
            module.build_environment_context(
                message="检查工作流", domain_keys=["workflow_governance"], task_facts={},
                selected_skills=[], matrix_terms=[], registry_snapshot=marker,
            )
        self.assertIs(marker, query.call_args.kwargs["snapshot"])

    def test_environment_context_uses_one_registry_batch(self) -> None:
        def fake_batch(queries, *, read_view=None, snapshot=None):
            del read_view, snapshot
            results = [
                {
                    "schema": "maintenance_capability_registry.query.v1",
                    "ok": True,
                    "filters": {
                        "system": str(query.get("system") or ""),
                        "term": str(query.get("term") or ""),
                        "action": str(query.get("action") or ""),
                    },
                    "items": [],
                    "returned": 0,
                    "total": 0,
                    "has_more": False,
                    "limit": int(query.get("limit") or 20),
                    "index_status": "fresh",
                    "authority_status": "derived_index_fresh",
                    "derived_refresh_recommended": False,
                    "query_tokens": [],
                }
                for query in queries
            ]
            return {
                "schema": "maintenance_capability_registry.query_batch.v1",
                "ok": True,
                "results": results,
                "query_count": len(queries),
                "unique_query_count": len(queries),
                "deduplicated_count": 0,
                "source_scan_count": 1,
                "read_view": {
                    "schema": "maintenance_registry_read_view.v1",
                    "source_signature": "source-v1",
                    "index_status": "fresh",
                    "authority_status": "derived_index_fresh",
                },
            }

        with patch.object(context, "query_registry_batch", side_effect=fake_batch) as batch_query:
            result = context.build_environment_context(
                message="检查 workflow 和 resource owner",
                domain_keys=["workflow_governance", "resource_acquisition"],
                task_facts={"resource_materialization": True},
                selected_skills=[],
                matrix_terms=["workflow", "resource"],
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(batch_query.call_count, 1)
        submitted = batch_query.call_args.args[0]
        self.assertGreater(len(submitted), len(result["relevant_systems"]))
        self.assertEqual(result["maintenance_query_metrics"]["source_scan_count"], 1)
        self.assertEqual(result["maintenance_read_view"]["source_signature"], "source-v1")

    def test_semantic_tasks_expose_bge_owner_reference_without_claiming_callability(self) -> None:
        semantic = context.build_environment_context(
            message="用 BGE-M3 做语义检索与概念归一",
            domain_keys=["general"],
            task_facts={},
            selected_skills=[],
            matrix_terms=[],
        )
        ordinary = context.build_environment_context(
            message="检查工作流状态",
            domain_keys=["general"],
            task_facts={},
            selected_skills=[],
            matrix_terms=[],
        )
        model = semantic["semantic_capabilities"][0]
        self.assertEqual(model["capability_id"], "model:bge-m3")
        self.assertEqual(model["source_authority"], "semantic_capability_owner")
        self.assertIn("semantic_capability_owner.py status --probe", model["entry_point"])
        self.assertIn("hybrid-rerank", model["entry_point"])
        self.assertIn("route_layer", model["platform_scope"])
        self.assertIsNone(model["callable"])
        self.assertIn("FTS/graph", model["fallback_route"])
        self.assertIn("concept_normalization", model["business_results"]["semantic_modes"])
        self.assertTrue(model["business_results"]["citation_required"])
        shared = model["business_results"]["shared_owner_contract"]
        self.assertEqual(model["business_results"]["vector_scope"], "caller_candidate_batch")
        self.assertIn("eligible_source_chunks", model["business_results"]["consumer_adapters"][0]["vector_scope"])
        self.assertEqual(shared["transport_modes"], ["dense"])
        self.assertIn("sparse", shared["unsupported_transport_modes"])
        self.assertEqual(shared["hybrid_fusion"], "reciprocal_rank_fusion")
        self.assertEqual(shared["selection_policy"], "margin_or_codex_judgment")
        self.assertEqual(model["business_results"]["consumer_adapters"][0]["id"], "textbook_knowledge")
        self.assertEqual(ordinary["semantic_capabilities"], [])


if __name__ == "__main__":
    unittest.main()
