#!/usr/bin/env python3
from __future__ import annotations

import unittest

import context_projection_shadow as shadow


class ContextProjectionShadowTests(unittest.TestCase):
    def test_shadow_does_not_replace_actual_output(self) -> None:
        payload = {"ok": True, "status": "completed", "next_action": "consume", "rows": ["x" * 200] * 100}
        result = shadow.evaluate_shadow(
            payload, source_kind="default", source_signature="shadow-a", consumer_purpose="codex", inline_budget=1000,
        )
        self.assertTrue(result["actual_output_unchanged"] )
        self.assertEqual(payload["rows"][0], "x" * 200)
        self.assertEqual(result["functional_recall"], 1.0)
        self.assertGreater(result["estimated_bytes_saved"], 0)

    def test_shadow_marks_existing_projection_without_second_compression(self) -> None:
        result = shadow.evaluate_shadow(
            {"ok": True, "output_budget": {"functional_compression": {"functional_integrity": "preserved"}}},
            source_kind="default", source_signature="shadow-b", consumer_purpose="codex", already_projected=True,
        )
        self.assertEqual(result["projection_mode"], "direct")
        self.assertTrue(result["already_projected"] )
        self.assertFalse(result["headroom_recommended"] )

    def test_aggregate_is_signature_deduplicated(self) -> None:
        row = shadow.evaluate_shadow(
            {"ok": True, "status": "completed"}, source_kind="default", source_signature="shadow-c", consumer_purpose="codex",
        )
        aggregate = shadow.aggregate_shadow([row, row])
        self.assertEqual(aggregate["observation_count"], 1)
        self.assertEqual(aggregate["duplicate_observation_count"], 1)


if __name__ == "__main__":
    unittest.main()
