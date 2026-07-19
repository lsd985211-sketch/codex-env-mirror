#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest.mock import patch

import record_store_maintenance


class RecordStoreRepairPlanTests(unittest.TestCase):
    def test_healthy_index_is_kept(self) -> None:
        snap = {"roots": []}
        with patch.object(record_store_maintenance, "archive_plan", return_value={"groups": []}), patch.object(
            record_store_maintenance,
            "inspect_index",
            return_value={"exists": True, "ok": True, "record_count": 12, "fts_exists": True},
        ):
            result = record_store_maintenance.repair_plan(snap)

        action_ids = [item["id"] for item in result["actions"]]
        self.assertIn("keep_record_store_index", action_ids)
        self.assertNotIn("create_record_store_index", action_ids)

    def test_unreadable_index_is_rebuilt_not_recreated_blindly(self) -> None:
        snap = {"roots": []}
        with patch.object(record_store_maintenance, "archive_plan", return_value={"groups": []}), patch.object(
            record_store_maintenance,
            "inspect_index",
            return_value={"exists": True, "ok": False, "error": "malformed"},
        ):
            result = record_store_maintenance.repair_plan(snap)

        action_ids = [item["id"] for item in result["actions"]]
        self.assertIn("repair_unreadable_record_store_index", action_ids)
        self.assertNotIn("create_record_store_index", action_ids)


if __name__ == "__main__":
    unittest.main()
