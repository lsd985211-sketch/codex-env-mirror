#!/usr/bin/env python3
from __future__ import annotations

import unittest

from resource_acceptance import build_acceptance_contract, evaluate_acceptance


class ResourceAcceptanceTests(unittest.TestCase):
    def test_transport_success_cannot_satisfy_required_artifact(self) -> None:
        request = {"need_materialization": True, "metadata": {"resource_kind_hint": "generic_download"}}
        contract = build_acceptance_contract(request)
        decision = evaluate_acceptance(contract, {"ok": True, "result_kind": "metadata", "metadata": {"url": "https://example.test/a"}})
        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["reason"], "required_deliverables_not_met")

    def test_search_candidates_satisfy_candidate_contract(self) -> None:
        request = {"metadata": {"task_envelope": {"schema": "structured_task_envelope.v1", "domain": "resource", "resource": {"execution": {"deliverables": ["candidates"], "acceptance": {"required_deliverables": ["candidates"]}}}}}}
        first = build_acceptance_contract(request)
        second = build_acceptance_contract(request)
        self.assertEqual(first["signature"], second["signature"])
        decision = evaluate_acceptance(first, {"ok": True, "result_kind": "search", "candidates": [{"url": "https://example.test"}]})
        self.assertTrue(decision["accepted"])

    def test_content_contract_rejects_metadata_only(self) -> None:
        request = {"metadata": {"task_envelope": {"schema": "structured_task_envelope.v1", "domain": "resource", "resource": {"execution": {"deliverables": ["content"]}}}}}
        contract = build_acceptance_contract(request)
        decision = evaluate_acceptance(contract, {"ok": True, "result_kind": "metadata", "metadata": {"title": "only metadata"}})
        self.assertFalse(decision["accepted"])


if __name__ == "__main__":
    unittest.main()
