#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _bridge import windows_kernel_pool_remediation as remediation


class KernelPoolRemediationTests(unittest.TestCase):
    def test_plan_id_is_order_independent(self) -> None:
        left = [
            {"name": "b", "program": "C:/missing-b.exe"},
            {"name": "a", "program": "C:/missing-a.exe"},
        ]
        right = list(reversed(left))
        self.assertEqual(remediation._plan_id(left), remediation._plan_id(right))

    @mock.patch.object(
        remediation,
        "wfp_inventory",
        return_value={
            "stale_candidates": [
                {"name": "rule", "program": "C:/missing.exe"}
            ],
            "eligible_candidates": [
                {"name": "rule", "program": "C:/missing.exe"}
            ],
        },
    )
    def test_apply_requires_confirmation_and_current_plan_id(self, _inventory: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = remediation.wfp_apply(
                Path(temp),
                Path("diagnostics.py"),
                confirm="wrong",
                plan_id="stale",
            )
        self.assertFalse(result["applied"])
        self.assertEqual(
            result["reason"], "exact_confirmation_and_current_plan_id_required"
        )
        self.assertEqual(result["eligible_count"], 1)


if __name__ == "__main__":
    unittest.main()
