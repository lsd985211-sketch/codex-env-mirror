#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import codex_reporter
import codex_scheduler_runner
import performance_maintenance_job


class RecordWriteAmplificationTests(unittest.TestCase):
    def test_report_enqueue_deduplicates_same_semantic_issue_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request_root = root / "requests"
            raw_root = root / "raw"
            evidence_one = {
                "schema": "doctor.v1",
                "ok": False,
                "generated_at": "2026-07-12T01:00:00Z",
                "issues": [{"code": "same_issue", "pid": 100}],
            }
            evidence_two = {
                "schema": "doctor.v1",
                "ok": False,
                "generated_at": "2026-07-12T02:00:00Z",
                "issues": [{"code": "same_issue", "pid": 200}],
            }
            with (
                mock.patch.object(codex_reporter, "REQUEST_ROOT", request_root),
                mock.patch.object(codex_reporter, "RAW_ROOT", raw_root),
            ):
                first = codex_reporter.enqueue_report(
                    kind="performance",
                    title="Performance issue",
                    evidence=evidence_one,
                    policy="report_only",
                )
                second = codex_reporter.enqueue_report(
                    kind="performance",
                    title="Performance issue",
                    evidence=evidence_two,
                    policy="report_only",
                )

            self.assertTrue(first["queued"])
            self.assertTrue(second["deduplicated"])
            self.assertEqual(first["request_id"], second["request_id"])
            self.assertEqual(len(list(request_root.glob("*.json"))), 1)
            self.assertEqual(len(list(raw_root.rglob("sha256-*.json"))), 1)

    def test_empty_report_worker_poll_does_not_create_record_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                mock.patch.object(codex_reporter, "REQUEST_ROOT", root / "requests"),
                mock.patch.object(codex_reporter, "RECORD_ROOT", root / "records"),
                mock.patch.object(codex_reporter, "LOCK_PATH", root / "worker.lock"),
            ):
                result = codex_reporter.worker(max_jobs=1, timeout_seconds=1)
            self.assertTrue(result["record_suppressed"])
            self.assertFalse((root / "records").exists())

    def test_unchanged_success_is_sampled_but_failures_persist(self) -> None:
        now = datetime(2026, 7, 14, tzinfo=timezone.utc)
        recent = {"last_persisted_at": (now - timedelta(hours=1)).isoformat()}
        old = {"last_persisted_at": (now - timedelta(hours=7)).isoformat()}
        self.assertFalse(
            performance_maintenance_job.should_persist_maintenance_record(
                recent, state_changed=False, action_applied=False, ok=True, now=now
            )
        )
        self.assertTrue(
            performance_maintenance_job.should_persist_maintenance_record(
                old, state_changed=False, action_applied=False, ok=True, now=now
            )
        )
        self.assertTrue(
            performance_maintenance_job.should_persist_maintenance_record(
                recent, state_changed=False, action_applied=False, ok=False, now=now
            )
        )

    def test_scheduler_success_summary_keeps_decision_fields_not_snapshot(self) -> None:
        raw = {
            "schema": "validator.v1",
            "ok": True,
            "status": "completed",
            "issue_count": 0,
            "snapshot": {"rows": ["x" * 1000] * 100},
        }
        summary = codex_scheduler_runner.summarize_success_stdout(
            __import__("json").dumps(raw), limit=500
        )
        self.assertIn("validator.v1", summary)
        self.assertIn("original_chars", summary)
        self.assertNotIn("snapshot", summary)
        self.assertLessEqual(len(summary), 500)

    def test_performance_step_record_drops_large_payload_body(self) -> None:
        step = performance_maintenance_job.Step(
            "performance_doctor",
            ok=False,
            payload={
                "schema": "performance.v1",
                "generated_at": "2026-07-12T01:00:00Z",
                "issues": [{"code": "sustained_load", "detail": "x" * 100_000}],
            },
        )
        compact = performance_maintenance_job.compact_step_record(step)
        self.assertEqual(compact["issue_codes"], ["sustained_load"])
        self.assertNotIn("payload", compact)
        self.assertLess(len(str(compact)), 1000)


if __name__ == "__main__":
    unittest.main()
