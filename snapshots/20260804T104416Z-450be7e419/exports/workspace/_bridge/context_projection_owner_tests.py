#!/usr/bin/env python3
from __future__ import annotations

import unittest

import context_projection_owner as owner


class ContextProjectionOwnerTests(unittest.TestCase):
    def test_small_decision_complete_payload_is_direct(self) -> None:
        result = owner.decide_projection(
            {"schema": "x.v1", "ok": True, "status": "completed", "next_action": "consume"},
            source_kind="default", source_signature="source-a", consumer_purpose="codex",
        )
        self.assertEqual(result["mode"], "direct")
        self.assertEqual(result["functional_recall"], 1.0)

    def test_large_redundant_payload_uses_shared_projection(self) -> None:
        result = owner.decide_projection(
            {"ok": True, "status": "completed", "next_action": "consume", "rows": ["duplicate" * 100] * 100},
            source_kind="default", source_signature="source-b", consumer_purpose="codex", inline_budget=1200,
        )
        self.assertEqual(result["mode"], "project")
        self.assertEqual(result["functional_recall"], 1.0)
        self.assertLess(result["projected_bytes"], result["input_bytes"] )

    def test_functional_loss_without_artifact_blocks(self) -> None:
        result = owner.decide_projection(
            {"required_gates": [{"owner": str(i), "completion": "x" * 500} for i in range(30)]},
            source_kind="workflow", source_signature="source-c", consumer_purpose="codex", inline_budget=500,
        )
        self.assertEqual(result["mode"], "block_for_reference")
        self.assertEqual(result["functional_integrity"], "blocked_no_reference")

    def test_functional_loss_with_artifact_returns_reference(self) -> None:
        result = owner.decide_projection(
            {"required_gates": [{"owner": str(i), "completion": "x" * 500} for i in range(30)]},
            source_kind="workflow", source_signature="source-d", consumer_purpose="codex", inline_budget=500,
            artifact_ref="artifact:/tmp/full.json",
        )
        self.assertEqual(result["mode"], "reference")
        self.assertEqual(result["functional_integrity"], "reference_required")

    def test_headroom_is_selected_only_with_durable_artifact_and_positive_savings(self) -> None:
        payload = {"ok": True, "status": "completed", "next_action": "consume", "content": "z" * 30000}
        selected = owner.decide_projection(
            payload, source_kind="log", source_signature="source-e", consumer_purpose="codex",
            artifact_ref="artifact:/tmp/log.txt", reversible_compression_available=True,
            estimated_compression_ratio=0.2,
        )
        no_artifact = owner.decide_projection(
            payload, source_kind="log", source_signature="source-e", consumer_purpose="codex",
            reversible_compression_available=True, estimated_compression_ratio=0.2,
        )
        self.assertEqual(selected["mode"], "reversible_compress")
        self.assertNotEqual(no_artifact["mode"], "reversible_compress")

    def test_existing_bounded_projection_is_not_compressed_twice(self) -> None:
        result = owner.decide_projection(
            {"ok": True, "output_budget": {"functional_compression": {"functional_integrity": "preserved"}}},
            source_kind="default", source_signature="source-f", consumer_purpose="codex",
            artifact_ref="artifact:/tmp/full.json", reversible_compression_available=True,
            estimated_compression_ratio=0.1, already_projected=True,
        )
        self.assertEqual(result["mode"], "direct")
        self.assertTrue(result["already_projected"] )

    def test_blocked_existing_projection_remains_blocked(self) -> None:
        result = owner.decide_projection(
            {
                "ok": False,
                "compression_blocked": True,
                "output_budget": {
                    "artifact_ref": "",
                    "functional_compression": {"functional_integrity": "blocked_no_reference"},
                },
            },
            source_kind="workflow", source_signature="source-blocked", consumer_purpose="codex",
            already_projected=True,
        )
        self.assertEqual(result["mode"], "block_for_reference")
        self.assertFalse(result["ok"] )

    def test_decision_signature_is_deterministic_and_risk_is_not_a_gate(self) -> None:
        kwargs = dict(source_kind="default", source_signature="source-g", consumer_purpose="codex")
        first = owner.decide_projection({"risk": "high", "recommended": False, "status": "completed"}, **kwargs)
        second = owner.decide_projection({"risk": "high", "recommended": False, "status": "completed"}, **kwargs)
        self.assertEqual(first["decision_signature"], second["decision_signature"] )
        self.assertNotEqual(first["mode"], "block_for_reference")

    def test_decision_signature_tracks_projection_inputs(self) -> None:
        payload = {"status": "completed", "next_action": "consume", "rows": ["x" * 200] * 100}
        base = dict(
            source_kind="resource",
            source_signature="source-h",
            consumer_purpose="codex",
            required_field_names=("next_action",),
        )
        compact = owner.decide_projection(payload, inline_budget=1000, **base)
        richer = owner.decide_projection(payload, inline_budget=4000, **base)
        referenced = owner.decide_projection(payload, inline_budget=1000, artifact_ref="artifact:/tmp/result.json", **base)
        reversible = owner.decide_projection(
            payload,
            inline_budget=1000,
            artifact_ref="artifact:/tmp/result.json",
            reversible_compression_available=True,
            estimated_compression_ratio=0.2,
            **base,
        )
        signatures = {
            compact["decision_signature"],
            richer["decision_signature"],
            referenced["decision_signature"],
            reversible["decision_signature"],
        }
        self.assertEqual(len(signatures), 4)
        self.assertIn("next_action", compact["required_fields"])

    def test_mcp_result_contract_preserves_route_and_permission_policy(self) -> None:
        contract = owner.contract_for("mcp_result")
        self.assertIn("error", contract["required_fields"])
        self.assertIn("route", contract["preserve_fields"])
        self.assertIn("owner_mcp_policy", contract["preserve_fields"])


if __name__ == "__main__":
    unittest.main()
