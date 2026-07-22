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

    def test_validate_reuses_bounded_metadata_without_deep_scan(self) -> None:
        scheduler = {"actions": [{"action": "keep"}]}
        with patch.object(record_store_maintenance, "snapshot", side_effect=AssertionError("deep snapshot used")), patch.object(
            record_store_maintenance,
            "cold_archive_candidates",
            side_effect=AssertionError("archive scan used"),
        ), patch.object(
            record_store_maintenance,
            "ensure_scheduler_tasks",
            return_value=scheduler,
        ), patch.object(
            record_store_maintenance,
            "inspect_index",
            return_value={
                "exists": True,
                "ok": True,
                "record_count": 1,
                "fts_exists": True,
                "records_query_ok": True,
                "resource_query_ok": True,
            },
        ):
            result = record_store_maintenance.validate()

        self.assertTrue(result["ok"], result["checks"])
        self.assertEqual("contract_only", result["metrics"]["archive_scan_mode"])
        self.assertEqual(0, result["metrics"]["archive_candidate_count"])


if __name__ == "__main__":
    unittest.main()
