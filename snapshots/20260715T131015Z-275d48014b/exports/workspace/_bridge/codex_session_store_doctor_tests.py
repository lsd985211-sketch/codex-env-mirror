#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import codex_session_store_doctor as doctor


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

    def test_auto_maintain_never_rewrites_active_candidate(self) -> None:
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
            self.assertEqual(active.read_bytes(), active_before)
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


if __name__ == "__main__":
    unittest.main()
