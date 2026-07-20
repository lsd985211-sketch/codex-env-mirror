from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SHARED = Path(__file__).resolve().parents[1]
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

import email_state_index
import incident_index
import migration_ledger


class MigrationLedgerTests(unittest.TestCase):
    def test_operation_and_events_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "source.sqlite"
            destination_path = Path(tmp) / "destination.sqlite"
            operation = migration_ledger.create_operation(
                domain="test", owner="owner", source_path="a", target_path="b",
                reason="test migration", db_path=source_path,
            )
            self.assertTrue(operation["created"])
            event = migration_ledger.append_event(operation["migration_id"], "verified", db_path=source_path)
            self.assertTrue(event["ok"])
            destination = sqlite3.connect(destination_path)
            migration_ledger.ensure_schema(destination)
            counts = migration_ledger.copy_rows_from(source_path, destination)
            destination.commit()
            self.assertEqual(counts["migration_operations"], 1)
            self.assertEqual(counts["migration_events"], 1)
            self.assertEqual(destination.execute("SELECT status FROM migration_current_status").fetchone()[0], "verified")
            destination.close()


class EmailReconciliationTests(unittest.TestCase):
    @staticmethod
    def stage(stage: str, item_id: str, status: str, payload: dict[str, object]) -> dict[str, object]:
        return {"stage": stage, "item_id": item_id, "status": status,
                "schedule_run_id": payload.get("schedule_run_id", ""),
                "payload_json": json.dumps(payload), "indexed_at": "now"}

    def test_receipt_links_by_delivery_id_and_retained_draft_is_valid(self) -> None:
        run_id = "run-1"
        delivery_id = "delivery-1"
        rows = {
            "email_stage_items": [
                self.stage("schedule_run", run_id, "sent", {"schedule_run_id": run_id}),
                self.stage("draft_item", "draft-1", "draft", {"schedule_run_id": run_id}),
                self.stage("delivery_job", delivery_id, "sent", {"schedule_run_id": run_id, "delivery_job_id": delivery_id, "message_id": "<m1>"}),
            ],
            "email_smtp_receipts": [{"receipt_id": delivery_id, "schedule_run_id": "", "status": "sent", "payload_json": json.dumps({"receipt_id": delivery_id, "message_id": "<m1>"})}],
            "email_inbox_messages": [],
        }
        result = email_state_index.build_reconciliation(rows, "now")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["classification"], "valid_overlap")

    def test_sent_stage_without_receipt_is_reported(self) -> None:
        run_id = "run-2"
        rows = {"email_stage_items": [
            self.stage("schedule_run", run_id, "sent", {"schedule_run_id": run_id}),
            self.stage("delivery_job", "delivery-2", "sent", {"schedule_run_id": run_id}),
        ], "email_smtp_receipts": [], "email_inbox_messages": []}
        result = email_state_index.build_reconciliation(rows, "now")
        self.assertEqual(result[0]["classification"], "missing_receipt")


class IncidentIndexTests(unittest.TestCase):
    def test_reports_cluster_and_use_unique_run_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = sqlite3.connect(root / "records.sqlite")
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE records (kind TEXT, area TEXT, source_path TEXT, archive_path TEXT, created_at TEXT)")
            for index in (1, 2):
                run_id = f"run-{index}"
                request_path = root / f"request-{index}.json"
                run_path = root / f"run-{index}.json"
                request_path.write_text(json.dumps({
                    "request_id": f"request-{index}", "created_at": f"2026-01-0{index}T00:00:00+00:00",
                    "status": "done", "kind": "codex_main_process", "title": "Codex main process",
                    "policy": "report_only", "evidence": {"maintenance_run_id": run_id, "issues": [{"code": "main_process_pressure"}]},
                }), encoding="utf-8")
                run_path.write_text(json.dumps({
                    "schema": "performance-maintenance-record.v2", "ok": True,
                    "generated_at": f"2026-01-0{index}T00:00:00+00:00", "trigger": {"request_id": run_id},
                    "reports": [str(request_path)],
                }), encoding="utf-8")
                conn.execute("INSERT INTO records VALUES ('execution_record','system_maintenance',?,'',?)", (str(run_path), f"2026-01-0{index}"))
                conn.execute("INSERT INTO records VALUES ('report_request','system_maintenance',?,'',?)", (str(request_path), f"2026-01-0{index}"))
            conn.commit()
            rebuilt = incident_index.rebuild(conn, apply=True)
            self.assertEqual(rebuilt["family_count"], 1)
            self.assertEqual(rebuilt["occurrence_count"], 2)
            metrics = incident_index.metrics(conn, kind="codex_main_process")
            self.assertEqual(metrics["report_count"], 2)
            self.assertEqual(metrics["unique_incident_count"], 1)
            self.assertEqual(metrics["failure_rate"], 1.0)
            conn.close()


if __name__ == "__main__":
    unittest.main()
