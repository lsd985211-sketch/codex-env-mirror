#!/usr/bin/env python3
"""Focused regressions for safe local Hub workflow-route caching."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import local_mcp_hub_routes as routes


def _plan(*, required_next_action: str = "follow_mcp_call_priority_chain") -> dict:
    return {
        "ok": True,
        "detail_level": "micro",
        "execution_route_pack": {
            "ok": True,
            "route_decision": {
                "task_facts": {},
                "required_gates": [],
                "required_next_action": required_next_action,
            },
            "authorization": {"required": False},
            "decision_authority": {"hard_gates": []},
        },
    }


class LocalMcpHubRouteFreshnessTests(unittest.TestCase):
    def test_rebuilds_when_authority_decision_changes(self) -> None:
        gated_plan = _plan(required_next_action="request_exact_authorization")
        gated_plan["execution_route_pack"]["route_decision"]["required_gates"] = ["current_authorization"]
        gated_plan["execution_route_pack"]["authorization"]["required"] = True
        gated_plan["execution_route_pack"]["decision_authority"]["hard_gates"] = ["current_authorization"]
        with patch.object(routes, "build_plan", side_effect=[_plan(), gated_plan]) as build:
            first = routes.workflow_route_pack({"message": "inspect workflow health"})
            second = routes.workflow_route_pack({"message": "inspect workflow health"})

        self.assertFalse(first["cache"]["hit"])
        self.assertFalse(second["cache"]["hit"])
        self.assertEqual(second["cache"]["reason"], "disabled_for_authority_freshness")
        self.assertTrue(second["execution_route_pack"]["authorization"]["required"])
        self.assertEqual(build.call_count, 2)

    def test_each_result_keeps_its_authoritative_generation_time(self) -> None:
        with patch.object(routes, "build_plan", return_value=_plan()) as build, patch.object(
            routes, "now_iso", side_effect=["authority-time-a", "authority-time-b"]
        ):
            first = routes.workflow_route_pack({"message": "inspect workflow health"})
            second = routes.workflow_route_pack({"message": "inspect workflow health"})

        self.assertEqual(first["generated_at"], "authority-time-a")
        self.assertEqual(second["generated_at"], "authority-time-b")
        self.assertEqual(build.call_count, 2)

    def test_resource_or_review_routes_are_never_cached(self) -> None:
        with patch.object(routes, "build_plan", return_value=_plan(required_next_action="submit_resource_request")) as build:
            first = routes.workflow_route_pack({"message": "research current documentation"})
            second = routes.workflow_route_pack({"message": "research current documentation"})

        self.assertFalse(first["cache"]["eligible"])
        self.assertFalse(second["cache"]["eligible"])
        self.assertEqual(build.call_count, 2)


if __name__ == "__main__":
    unittest.main()
