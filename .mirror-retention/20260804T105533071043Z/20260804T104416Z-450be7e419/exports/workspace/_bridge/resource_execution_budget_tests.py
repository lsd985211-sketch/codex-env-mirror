#!/usr/bin/env python3

import unittest
from unittest import mock

import resource_execution_budget as budget_module
from resource_execution_budget import ResourceExecutionBudget


class ResourceExecutionBudgetTests(unittest.TestCase):
    def test_first_lane_cannot_consume_reserved_budget(self) -> None:
        with mock.patch.object(budget_module.time, "monotonic", return_value=100.0):
            budget = ResourceExecutionBudget.start(30)
            plan = budget.lane_plan(owner_cap=30, remaining_lane_count=3, minimum_per_remaining=3)
        self.assertEqual(plan["minimum_reserved_for_remaining"], 9.0)
        self.assertEqual(plan["allocated_timeout_seconds"], 21)

    def test_all_lanes_share_original_deadline(self) -> None:
        with mock.patch.object(budget_module.time, "monotonic", side_effect=[100.0, 110.0]):
            budget = ResourceExecutionBudget.start(30)
            timeout = budget.lane_timeout_seconds(owner_cap=30, remaining_lane_count=2, minimum_per_remaining=3)
        self.assertEqual(timeout, 14)
        self.assertEqual(budget.deadline_monotonic, 130.0)


if __name__ == "__main__":
    unittest.main()
