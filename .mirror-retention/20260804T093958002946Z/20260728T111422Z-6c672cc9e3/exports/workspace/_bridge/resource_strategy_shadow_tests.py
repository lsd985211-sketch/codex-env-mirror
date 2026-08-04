#!/usr/bin/env python3
from __future__ import annotations

import unittest

from resource_strategy_shadow import build_shadow_plan, controlled_execution_plan


class ResourceStrategyShadowTests(unittest.TestCase):
    def test_entity_discovery_does_not_prefer_context7(self) -> None:
        result = build_shadow_plan(
            request={"target": "搜索电影阳光灿烂的日子合法观看渠道"},
            route={},
            live_plan=[{"tool": "context7"}, {"tool": "generic_search"}],
            source_plan={
                "candidates": [
                    {"owner_tool": "context7", "id": "docs"},
                    {"owner_tool": "generic_search", "id": "web"},
                ]
            },
            acceptance={"signature": "accept"},
        )
        self.assertEqual(result["lanes"][0]["owner_tool"], "generic_search")
        self.assertTrue(result["order_differs"])
        self.assertFalse(result["creates_request"])

    def test_duplicate_source_identity_is_counted_once(self) -> None:
        result = build_shadow_plan(
            request={},
            route={},
            live_plan=[],
            source_plan={
                "candidates": [
                    {"url": "https://example.test/a"},
                    {"url": "https://example.test/a"},
                ]
            },
            acceptance={"signature": "accept"},
        )
        self.assertEqual(result["lane_count"], 1)
        self.assertEqual(result["duplicate_candidate_count"], 1)

    def test_dynamic_execution_is_fixed_allowlist_and_never_introduces_owner(
        self,
    ) -> None:
        shadow = {
            "lanes": [
                {"owner_tool": "generic_search"},
                {"owner_tool": "context7"},
                {"owner_tool": "unknown"},
            ]
        }
        off = controlled_execution_plan(
            request={"metadata": {}},
            shadow_plan=shadow,
            live_tools=["context7", "generic_search"],
        )
        self.assertFalse(off["enabled"])
        on = controlled_execution_plan(
            request={
                "metadata": {
                    "adaptive_strategy_allowlist": True,
                    "adaptive_strategy_task_class": "entity_discovery",
                }
            },
            shadow_plan=shadow,
            live_tools=["context7", "generic_search"],
        )
        self.assertTrue(on["enabled"])
        self.assertEqual(on["tools"], ["generic_search", "context7"])
        self.assertNotIn("unknown", on["tools"])


if __name__ == "__main__":
    unittest.main()
