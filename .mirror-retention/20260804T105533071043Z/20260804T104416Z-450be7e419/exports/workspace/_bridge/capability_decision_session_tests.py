import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import capability_decision_session as session  # noqa: E402


PRIMARY_OWNER = {
    "name": "codex_code_review_judgment",
    "authority_ref": "workflow.route_decision.primary_owner",
    "entry_ref": "owner:code-review",
}
ACCEPTANCE = {
    "predicate": "focused validation passes",
    "consume_ref": "codex_workflow_entry.consume",
}


def candidate(candidate_id: str, **overrides):
    value = {
        "candidate_id": candidate_id,
        "kind": "skill",
        "role": "support",
        "authority_ref": f"skill:{candidate_id}",
        "source_ref": f"skill-source:{candidate_id}",
        "entry_ref": f"skill-entry:{candidate_id}",
        "probe_ref": f"skill-probe:{candidate_id}",
        "fallback_ref": "fallback:manual-review",
        "availability": "ready",
        "content": f"Capability {candidate_id}",
    }
    value.update(overrides)
    return value


class CapabilityDecisionSessionTests(unittest.TestCase):
    def test_semantic_selector_ranks_ready_support_without_replacing_primary_owner(self):
        selector = Mock(return_value={
            "ok": True,
            "owner": "semantic_capability_owner",
            "vector_used": True,
            "vector_reason": "dense_plus_owner_ranks_rrf",
            "retrievers": ["dense", "lexical"],
            "results": [
                {"id": "ocr-local", "rrf_score": 0.04},
                {"id": "ocr-web", "rrf_score": 0.02},
            ],
            "selected": "ocr-local",
            "ambiguous": False,
            "selection_margin": 0.02,
            "selection_reason": "semantic_margin_satisfied",
            "fallback": "caller_owned_lexical_graph_order",
            "fallback_used": False,
        })
        result = session.decide_from_environment_view(
            task_contract_ref="workflow:ocr",
            primary_owner=PRIMARY_OWNER,
            environment_view={
                "source_signature": "owner-source",
                "candidates": [
                    candidate("ocr-local", content="本机 OCR 能力", lexical_rank=1),
                    candidate("ocr-web", content="外部 OCR 资料", lexical_rank=2),
                ],
            },
            acceptance=ACCEPTANCE,
            semantic_query="识别扫描 PDF",
            semantic_selector=selector,
        )
        self.assertEqual("selected", result["status"])
        self.assertEqual("codex_code_review_judgment", result["primary_owner"]["name"])
        self.assertEqual("ocr-local", result["selected_supporting_assets"][0]["candidate_id"])
        self.assertTrue(result["ranking"]["semantic_used"])
        self.assertEqual("dense_plus_owner_ranks_rrf", result["ranking"]["vector_reason"])
        submitted = selector.call_args.args[1]
        self.assertEqual([row["id"] for row in submitted], ["ocr-local", "ocr-web"])
        self.assertNotIn("entry_ref", submitted[0])
        self.assertNotIn("本机 OCR 能力", str(result))

    def test_unavailable_semantic_probe_does_not_call_model_and_keeps_fallback_judgment(self):
        selector = Mock()
        result = session.decide_from_environment_view(
            task_contract_ref="workflow:ocr",
            primary_owner=PRIMARY_OWNER,
            environment_view={
                "candidates": [candidate("ocr-local"), candidate("ocr-web", lexical_rank=2)],
            },
            acceptance=ACCEPTANCE,
            semantic_query="识别扫描 PDF",
            semantic_probe_result={
                "callable": False,
                "healthy": False,
                "vector_reason": "probe_failed:TimeoutError",
                "fallback": "structured_fts_graph",
            },
            semantic_selector=selector,
        )
        selector.assert_not_called()
        self.assertEqual("judgment_required", result["status"])
        self.assertIn("ranking_ambiguous:semantic_owner_unavailable", result["reason_codes"])
        self.assertEqual("probe_failed:TimeoutError", result["ranking"]["vector_reason"])
        self.assertTrue(result["ranking"]["fallback_used"])

    def test_unknown_semantic_choice_is_recommendation_until_owner_probe(self):
        selector = Mock(return_value={
            "ok": True,
            "owner": "semantic_capability_owner",
            "vector_used": True,
            "vector_reason": "dense_rrf",
            "results": [
                {"id": "local-ocr", "rrf_score": 0.04},
                {"id": "unprobed-ocr", "rrf_score": 0.02},
            ],
            "selected": "unprobed-ocr",
            "ambiguous": False,
            "selection_reason": "semantic_margin_satisfied",
            "fallback": "caller_owned_lexical_graph_order",
            "fallback_used": False,
        })
        result = session.decide_from_environment_view(
            task_contract_ref="workflow:ocr",
            primary_owner=PRIMARY_OWNER,
            environment_view={
                "candidates": [
                    candidate("local-ocr"),
                    candidate("unprobed-ocr", availability="unknown"),
                ],
            },
            acceptance=ACCEPTANCE,
            semantic_query="识别扫描 PDF",
            semantic_probe_result={"callable": True, "healthy": True, "vector_reason": "probe_ok"},
            semantic_selector=selector,
        )
        self.assertEqual("judgment_required", result["status"])
        self.assertEqual(result["selected_supporting_assets"], [])
        self.assertEqual(
            result["recommended_supporting_assets"][0]["candidate_id"],
            "unprobed-ocr",
        )
        self.assertIn("semantic_recommendation_requires_owner_probe", result["reason_codes"])

    def test_semantic_near_tie_requires_codex_judgment(self):
        selector = Mock(return_value={
            "ok": True,
            "owner": "semantic_capability_owner",
            "vector_used": True,
            "vector_reason": "dense_rrf",
            "results": [
                {"id": "first", "rrf_score": 0.02},
                {"id": "second", "rrf_score": 0.0199},
            ],
            "selected": None,
            "ambiguous": True,
            "selection_margin": 0.0001,
            "selection_reason": "codex_judgment_required",
            "fallback": "caller_owned_lexical_graph_order",
            "fallback_used": False,
        })
        result = session.decide_from_environment_view(
            task_contract_ref="workflow:ambiguous",
            primary_owner=PRIMARY_OWNER,
            environment_view={"candidates": [candidate("first"), candidate("second")]},
            acceptance=ACCEPTANCE,
            semantic_query="ambiguous task",
            semantic_selector=selector,
        )
        self.assertEqual("judgment_required", result["status"])
        self.assertIn("ranking_ambiguous:codex_judgment_required", result["reason_codes"])
        self.assertEqual(0.0001, result["ranking"]["top_margin"])

    def test_environment_view_adapter_preserves_owner_and_signature(self):
        result = session.decide_from_environment_view(
            task_contract_ref="workflow:environment-view",
            primary_owner=PRIMARY_OWNER,
            environment_view={
                "source_signature": "owner-source",
                "environment_signature": "owner-environment",
                "candidates": [candidate("ocr", role="support")],
            },
            acceptance=ACCEPTANCE,
        )
        self.assertEqual("codex_code_review_judgment", result["primary_owner"]["name"])
        self.assertEqual("owner-source", result["source_signature"])
        self.assertEqual("owner-environment", result["environment_signature"])

    def test_normalize_candidate_requires_identity_and_authority(self):
        normalized, issues = session.normalize_candidates([
            candidate("ocr"),
            {"candidate_id": "missing-authority", "kind": "skill"},
            {"kind": "skill", "authority_ref": "skill:missing-id"},
        ])
        self.assertEqual([item["candidate_id"] for item in normalized], ["ocr"])
        self.assertEqual(issues, ["candidate_authority_missing:missing-authority", "candidate_identity_missing:2"])
        self.assertEqual(normalized[0]["content_sha256"], session.stable_hash("Capability ocr"))

    def test_normalize_candidate_rejects_duplicate_identity(self):
        normalized, issues = session.normalize_candidates([candidate("ocr"), candidate("ocr")])
        self.assertEqual([item["candidate_id"] for item in normalized], ["ocr"])
        self.assertEqual(issues, ["candidate_identity_duplicate:ocr"])

    def test_filter_rejects_platform_permission_and_unavailable_candidates(self):
        accepted, rejected = session.filter_candidates([
            candidate("unavailable", availability="unavailable"),
            candidate("blocked", permission_state="blocked"),
            candidate("wrong-platform", platform_ok=False),
            candidate("ready"),
        ])
        self.assertEqual([item["candidate_id"] for item in accepted], ["ready"])
        self.assertEqual(
            {item["candidate_id"]: item["reason_codes"] for item in rejected},
            {
                "unavailable": ["availability_unavailable"],
                "blocked": ["permission_blocked"],
                "wrong-platform": ["platform_mismatch"],
            },
        )

    def test_probe_plan_only_contains_decision_relevant_unknowns(self):
        planned = session.plan_probes([
            candidate("ready"),
            candidate("first", availability="unknown", lexical_rank=2),
            candidate("second", availability="unknown", lexical_rank=1),
            candidate("not-relevant", availability="unknown", decision_relevant=False),
            candidate("no-probe", availability="unknown", probe_ref=""),
        ], limit=1)
        self.assertEqual(planned, [{"candidate_id": "second", "probe_ref": "skill-probe:second"}])

    def test_primary_owner_is_preserved_when_support_is_selected(self):
        result = session.decide_capabilities(
            task_contract_ref="workflow:task-1",
            primary_owner=PRIMARY_OWNER,
            candidates=[candidate("codegraph")],
            acceptance=ACCEPTANCE,
        )
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["primary_owner"]["name"], "codex_code_review_judgment")
        self.assertEqual(result["selected_supporting_assets"][0]["candidate_id"], "codegraph")

    def test_hard_gate_blocks_selection_before_ranking(self):
        result = session.decide_capabilities(
            task_contract_ref="workflow:task-1",
            primary_owner=PRIMARY_OWNER,
            candidates=[candidate("codegraph")],
            acceptance=ACCEPTANCE,
            required_gates=[{"id": "authorization", "satisfied": False}],
            ranking_result={"selected": "codegraph", "ambiguous": False},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_codes"], ["required_gate_unsatisfied:authorization"])
        self.assertEqual(result["selected_supporting_assets"], [])
        self.assertEqual(result["probe_requests"], [])

    def test_unknown_decision_relevant_candidate_requires_probe(self):
        result = session.decide_capabilities(
            task_contract_ref="workflow:task-1",
            primary_owner=PRIMARY_OWNER,
            candidates=[candidate("ocr", availability="unknown")],
            acceptance=ACCEPTANCE,
        )
        self.assertEqual(result["status"], "judgment_required")
        self.assertEqual(result["reason_codes"], ["decision_relevant_probe_required"])
        self.assertEqual(result["probe_requests"], [{"candidate_id": "ocr", "probe_ref": "skill-probe:ocr"}])

    def test_ranking_ambiguity_requires_codex_judgment(self):
        result = session.decide_capabilities(
            task_contract_ref="workflow:task-1",
            primary_owner=PRIMARY_OWNER,
            candidates=[candidate("first"), candidate("second")],
            acceptance=ACCEPTANCE,
            ranking_result={"selected": None, "ambiguous": True, "selection_reason": "margin_too_small"},
        )
        self.assertEqual(result["status"], "judgment_required")
        self.assertEqual(result["reason_codes"], ["ranking_ambiguous:margin_too_small"])

    def test_incomplete_execution_contract_requires_judgment(self):
        result = session.decide_capabilities(
            task_contract_ref="workflow:task-1",
            primary_owner=PRIMARY_OWNER,
            candidates=[candidate("ocr", fallback_ref="")],
            acceptance=ACCEPTANCE,
        )
        self.assertEqual(result["status"], "judgment_required")
        self.assertEqual(result["reason_codes"], ["selected_candidate_contract_incomplete"])

    def test_receipt_signature_is_stable_and_changes_with_source(self):
        common = {
            "task_contract_ref": "workflow:task-1",
            "primary_owner": PRIMARY_OWNER,
            "candidates": [candidate("ocr")],
            "acceptance": ACCEPTANCE,
            "source_signature": "source-a",
            "environment_signature": "environment-a",
        }
        first = session.decide_capabilities(**common)
        second = session.decide_capabilities(**common)
        changed = session.decide_capabilities(**{**common, "source_signature": "source-b"})
        self.assertEqual(first["receipt_signature"], second["receipt_signature"])
        self.assertNotEqual(first["receipt_signature"], changed["receipt_signature"])
        self.assertNotIn("Capability ocr", str(first))

    def test_simple_fast_path_is_quiet_and_has_no_probe_requests(self):
        result = session.decide_capabilities(
            task_contract_ref="workflow:task-1",
            primary_owner=PRIMARY_OWNER,
            candidates=[candidate("ocr", availability="unknown")],
            acceptance=ACCEPTANCE,
            simple_fast_path=True,
        )
        self.assertEqual(result["status"], "selected")
        self.assertTrue(result["simple_fast_path"])
        self.assertEqual(result["probe_requests"], [])
        self.assertEqual(result["selected_supporting_assets"], [])


if __name__ == "__main__":
    unittest.main()
