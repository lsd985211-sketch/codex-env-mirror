#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    import codex_session_store_doctor as doctor
except ModuleNotFoundError:
    from _bridge import codex_session_store_doctor as doctor


def write_session(path: Path, marker: str) -> None:
    records = [
        {"type": "session_meta", "payload": {"id": marker}},
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "output": marker + ("x" * (12 * 1024 * 1024)),
            },
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in records),
        encoding="utf-8",
    )


def write_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in records),
        encoding="utf-8",
    )


def compacted_record(*, summary: dict | None = None) -> dict:
    payload = {
        "message": "preserved native compacted summary",
        "replacement_history": [{"window_id": "window-1"}],
    }
    if summary is not None:
        payload["recovery_summary"] = summary
    return {"type": "compacted", "payload": payload}


class SessionStoreAutoMaintainTests(unittest.TestCase):
    def test_fresh_unchanged_metadata_skips_recursive_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions = root / "sessions"
            inactive = sessions / "2026" / "07" / "11" / "inactive.jsonl"
            active = sessions / "2026" / "07" / "12" / "active.jsonl"
            write_session(inactive, "inactive")
            write_session(active, "active")
            now = time.time()
            os.utime(inactive, (now - 60, now - 60))
            os.utime(active, (now, now))
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "last_checked_at": doctor.utc_now(),
                        "sessions_root": str(sessions),
                        "total_bytes": inactive.stat().st_size,
                        "largest_path": str(inactive),
                        "largest_bytes": inactive.stat().st_size,
                        "largest_mb": round(inactive.stat().st_size / (1024 * 1024), 2),
                        "active_candidate_path": str(active),
                        "active_candidate_bytes": active.stat().st_size,
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(doctor, "AUTO_MAINTAIN_STATE_PATH", state_path),
                mock.patch.object(doctor, "AUTO_MAINTAIN_LOCK_PATH", root / "lock.json"),
                mock.patch.object(doctor, "codex_process_family_running", return_value=False),
                mock.patch.object(doctor, "snapshot", side_effect=AssertionError("full snapshot should be skipped")),
            ):
                result = doctor.auto_maintain(
                    sessions_root=sessions,
                    apply=True,
                    boundary="pre-launch",
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["result"]["reason"], "cooldown_metadata_unchanged")

    def test_auto_maintain_compacts_all_risk_candidates_at_stopped_process_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions = root / "sessions"
            inactive = sessions / "2026" / "07" / "11" / "inactive.jsonl"
            active = sessions / "2026" / "07" / "12" / "active.jsonl"
            write_session(inactive, "inactive")
            write_session(active, "active")
            now = time.time()
            os.utime(inactive, (now - 60, now - 60))
            os.utime(active, (now, now))
            active_before = active.read_bytes()
            inactive_before = inactive.read_bytes()
            thresholds = doctor.Thresholds(warn_bytes=100, risk_bytes=200, total_warn_bytes=100, top_limit=10)

            with (
                mock.patch.object(doctor, "AUTO_MAINTAIN_STATE_PATH", root / "state.json"),
                mock.patch.object(doctor, "AUTO_MAINTAIN_LOCK_PATH", root / "lock.json"),
                mock.patch.object(doctor, "COMPACTION_BACKUP_ROOT", root / "backups"),
                mock.patch.object(doctor, "codex_process_family_running", return_value=False),
            ):
                result = doctor.auto_maintain(
                    sessions_root=sessions,
                    thresholds=thresholds,
                    apply=True,
                    boundary="pre-launch",
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["boundary"], "pre-launch")
            self.assertNotEqual(active.read_bytes(), active_before)
            self.assertNotEqual(inactive.read_bytes(), inactive_before)
            self.assertFalse((root / "lock.json").exists())

    def test_busy_lock_is_non_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = root / "lock.json"
            lock_path.write_text("{}", encoding="utf-8")
            with mock.patch.object(doctor, "AUTO_MAINTAIN_LOCK_PATH", lock_path):
                result = doctor.auto_maintain(sessions_root=root / "sessions", apply=True, boundary="pre-launch")
            self.assertTrue(result["ok"])
            self.assertEqual(result["reason"], "auto_maintain_lock_busy")
            self.assertTrue(lock_path.exists())

    def test_stale_live_owner_lock_is_not_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = root / "lock.json"
            lock_path.write_text(json.dumps({"pid": 123}), encoding="utf-8")
            old = time.time() - doctor.AUTO_MAINTAIN_LOCK_STALE_SECONDS - 10
            os.utime(lock_path, (old, old))
            with (
                mock.patch.object(doctor, "AUTO_MAINTAIN_LOCK_PATH", lock_path),
                mock.patch.object(doctor, "process_is_alive", return_value=True),
            ):
                result = doctor.auto_maintain(sessions_root=root / "sessions", apply=True, boundary="pre-launch")
            self.assertEqual(result["reason"], "auto_maintain_lock_busy")
            self.assertTrue(result["lock"]["owner_alive"])
            self.assertTrue(lock_path.exists())

    def test_stale_dead_owner_lock_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = root / "lock.json"
            lock_path.write_text(json.dumps({"pid": 123}), encoding="utf-8")
            old = time.time() - doctor.AUTO_MAINTAIN_LOCK_STALE_SECONDS - 10
            os.utime(lock_path, (old, old))
            with (
                mock.patch.object(doctor, "AUTO_MAINTAIN_LOCK_PATH", lock_path),
                mock.patch.object(doctor, "AUTO_MAINTAIN_STATE_PATH", root / "state.json"),
                mock.patch.object(doctor, "process_is_alive", return_value=False),
                mock.patch.object(doctor, "codex_process_family_running", return_value=True),
            ):
                result = doctor.auto_maintain(sessions_root=root / "sessions", apply=True, boundary="pre-launch")
            self.assertEqual(result["result"]["reason"], "codex_process_family_running_fast_skip")
            self.assertFalse(lock_path.exists())


class RecoverySummaryTests(unittest.TestCase):
    def test_legacy_compacted_record_is_upgraded_without_changing_body(self) -> None:
        original = compacted_record()
        normalized, _saved, reason = doctor.compact_json_object(original)

        self.assertEqual(reason, "normalize_compacted_recovery_summary")
        self.assertEqual(normalized["payload"]["message"], original["payload"]["message"])
        self.assertEqual(normalized["payload"]["replacement_history"], original["payload"]["replacement_history"])
        summary = normalized["payload"]["recovery_summary"]
        self.assertEqual(summary["schema"], doctor.RECOVERY_SUMMARY_SCHEMA)
        self.assertTrue(summary["legacy"])
        self.assertTrue(doctor.validate_recovery_summary(summary)["ok"])
        for field in (*doctor.RECOVERY_SUMMARY_LIST_FIELDS, *doctor.RECOVERY_SUMMARY_STRING_FIELDS, "source", "legacy"):
            self.assertIn(field, summary)

    def test_existing_v2_summary_is_preserved(self) -> None:
        summary = doctor.empty_recovery_summary(legacy=False, source="workflow_closeout")
        summary["stable_conclusions"] = ["validated route"]
        summary["changed_files"] = [{"path": "_bridge/example.py"}]
        summary["evidence_refs"] = [{"type": "test", "ref": "test:recovery-summary", "summary": "passed"}]
        original = compacted_record(summary=summary)

        normalized, _saved, reason = doctor.compact_json_object(original)

        self.assertEqual(reason, "preserve_context_record")
        self.assertEqual(normalized["payload"]["recovery_summary"], summary)

    def test_invalid_evidence_ref_is_rejected(self) -> None:
        summary = doctor.empty_recovery_summary()
        summary["evidence_refs"] = [{"type": "tool", "ref": "call:1", "summary": "x" * 2049}]
        result = doctor.validate_recovery_summary(summary)

        self.assertFalse(result["ok"])
        self.assertIn("evidence_refs_invalid_item", result["issues"])

    def test_invalid_summary_blocks_apply_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "session.jsonl"
            summary = doctor.empty_recovery_summary()
            summary["changed_files"] = ["bad\npath"]
            write_records(path, [compacted_record(summary=summary)])
            before = path.read_bytes()

            with mock.patch.object(doctor, "codex_process_family_running", return_value=False):
                result = doctor.compact_file_apply(path, backup_dir=root / "backups", min_savings_bytes=0)

            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "recovery_summary_validation_failed")
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(path.with_suffix(path.suffix + ".compact.tmp").exists())

    def test_apply_preserves_compacted_body_and_writes_recovery_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "session.jsonl"
            original_compacted = compacted_record()
            write_records(
                path,
                [
                    original_compacted,
                    {"type": "response_item", "payload": {"type": "function_call_output", "output": "x" * 200}},
                ],
            )

            with mock.patch.object(doctor, "codex_process_family_running", return_value=False):
                result = doctor.compact_file_apply(
                    path,
                    backup_dir=root / "backups",
                    keep_chars=20,
                    min_savings_bytes=0,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["applied"])
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "committed")
            self.assertEqual(manifest["candidate_sha256"], result["after_sha256"])
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            stored = next(item for item in records if item["type"] == "compacted")
            self.assertEqual(stored["payload"]["message"], original_compacted["payload"]["message"])
            self.assertEqual(stored["payload"]["replacement_history"], original_compacted["payload"]["replacement_history"])
            self.assertTrue(doctor.validate_compacted_record(stored)["ok"])

    def test_temp_validation_failure_keeps_source_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "session.jsonl"
            write_records(
                path,
                [
                    compacted_record(),
                    {"type": "response_item", "payload": {"type": "function_call_output", "output": "x" * 200}},
                ],
            )
            before = path.read_bytes()
            invalid_integrity = {
                "ok": False,
                "record_count": 1,
                "summary_fingerprint": "different",
                "core_fingerprint": "different",
                "issues": [{"line": 1, "reason": "injected"}],
            }

            with (
                mock.patch.object(doctor, "codex_process_family_running", return_value=False),
                mock.patch.object(doctor, "recovery_summary_integrity", return_value=invalid_integrity),
            ):
                result = doctor.compact_file_apply(
                    path,
                    backup_dir=root / "backups",
                    keep_chars=20,
                    min_savings_bytes=0,
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "recovery_summary_validation_failed")
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(Path(result["backup_path"]).is_file())
            self.assertFalse(path.with_suffix(path.suffix + ".compact.tmp").exists())


if __name__ == "__main__":
    unittest.main()
