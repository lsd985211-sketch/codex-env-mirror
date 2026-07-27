#!/usr/bin/env python3
from __future__ import annotations

import unittest

import codex_runtime_cache_governance as governance


class RuntimeCacheGovernanceTests(unittest.TestCase):
    def test_retention_keeps_current_and_newest_previous(self) -> None:
        rows = [
            {"name": governance.CURRENT_NAME, "kind": "current", "modified_at": "2026-07-10T00:00:00+00:00", "age_hours": 100, "size_bytes": 100, "complete_enough_for_rollback": False},
            {"name": governance.PREVIOUS_PREFIX + "new", "kind": "previous", "modified_at": "2026-07-09T00:00:00+00:00", "age_hours": 100, "size_bytes": 50, "complete_enough_for_rollback": True},
            {"name": governance.PREVIOUS_PREFIX + "old", "kind": "previous", "modified_at": "2026-07-01T00:00:00+00:00", "age_hours": 200, "size_bytes": 40, "complete_enough_for_rollback": True},
            {"name": governance.INSTALL_PREFIX + "stale", "kind": "install_residue", "modified_at": "2026-06-01T00:00:00+00:00", "age_hours": 300, "size_bytes": 30, "complete_enough_for_rollback": False},
        ]

        result = governance.classify_retention(rows)

        self.assertEqual(result["keep_previous"], governance.PREVIOUS_PREFIX + "new")
        self.assertEqual({item["name"] for item in result["quarantine_candidates"]}, {governance.PREVIOUS_PREFIX + "old", governance.INSTALL_PREFIX + "stale"})


if __name__ == "__main__":
    unittest.main()
