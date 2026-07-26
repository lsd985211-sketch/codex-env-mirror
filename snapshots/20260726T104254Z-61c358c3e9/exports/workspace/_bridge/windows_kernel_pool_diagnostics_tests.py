#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from _bridge import windows_kernel_pool_diagnostics as diagnostics


def write_summary(sample: Path) -> None:
    sample.mkdir(parents=True)
    payload = {
        "captured_at": "2026-07-14T20:40:00+08:00",
        "label": sample.name,
        "system": {
            "pool_nonpaged_bytes": 1,
            "pool_paged_bytes": 1,
            "available_memory_mb": 1,
            "committed_percent": 1,
            "live_processes": 1,
        },
        "pool_totals": {"Nonp": 1, "Paged": 1},
        "category_bytes": {"nvidia": 1},
        "category_delta_from_baseline": {},
    }
    (sample / "summary.json").write_text(json.dumps(payload), encoding="utf-8")


class KernelPoolEvidenceGovernanceTests(unittest.TestCase):
    def test_governance_and_remediation_commands_are_wired(self) -> None:
        command_functions = {
            "index-rebuild": diagnostics.governance_backfill,
            "doctor": diagnostics.governance_doctor,
            "metrics": diagnostics.governance_metrics,
            "schedule-plan": diagnostics.governance_schedule_plan,
            "validate": diagnostics.governance_validate,
            "wfp-plan": diagnostics.wfp_repair_plan,
        }
        for command, expected in command_functions.items():
            with self.subTest(command=command):
                self.assertIs(diagnostics.parser().parse_args([command]).func, expected)

    def test_category_bytes_tracks_storage_and_security_objects(self) -> None:
        rows = [
            {"tag": "ismc", "mapped": "Unknown Driver", "bytes": 10},
            {"tag": "Toke", "mapped": "Token objects", "bytes": 20},
            {"tag": "SeAt", "mapped": "Security Attributes", "bytes": 30},
        ]
        categories = diagnostics.category_bytes(rows)
        self.assertEqual(categories["storage_rst"], 10)
        self.assertEqual(categories["security_objects"], 50)

    def test_baseline_delta_marks_missing_categories_unavailable(self) -> None:
        delta = diagnostics.baseline_delta({"known": 20, "new": 30}, {"known": 5})
        self.assertEqual(delta["known"], 15)
        self.assertIsNone(delta["new"])

    def test_wpn_decision_supports_high_handle_count(self) -> None:
        decision = diagnostics.wpn_decision(
            {"notifications": 1, "nvidia": 2 * 1024 * 1024 * 1024},
            {"wpn_handles": diagnostics.WPN_HANDLE_THRESHOLD},
        )
        self.assertTrue(decision["wpn_restart_supported"])
        self.assertTrue(decision["wpn_handle_leak_suspected"])
        self.assertFalse(decision["notification_pool_dominant"])

    def test_wpn_decision_rejects_low_evidence(self) -> None:
        decision = diagnostics.wpn_decision(
            {"notifications": 1, "nvidia": 2 * 1024 * 1024 * 1024},
            {"wpn_handles": 500},
        )
        self.assertFalse(decision["wpn_restart_supported"])

    def test_sample_dir_rejects_nested_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nested = root / "group" / "sample"
            write_summary(nested)
            with self.assertRaisesRegex(ValueError, "direct child"):
                diagnostics.sample_dir(nested, root)

    def test_quarantine_excludes_invalid_sample_from_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sample = root / "invalid-sample"
            write_summary(sample)
            with contextlib.redirect_stdout(io.StringIO()):
                diagnostics.quarantine(
                    argparse.Namespace(
                        sample_dir=sample,
                        output_root=root,
                        reason="known invalid totals",
                    )
                )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                diagnostics.status(argparse.Namespace(output_root=root, limit=10))
            result = json.loads(output.getvalue())
            self.assertEqual(result["sample_count"], 0)
            self.assertEqual(result["quarantine_count"], 1)
            quality = json.loads(
                (root / diagnostics.QUARANTINE_DIR / sample.name / diagnostics.QUALITY_FILE).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(quality["status"], "invalid")

    def test_status_reads_quality_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sample = root / "limited-sample"
            write_summary(sample)
            with contextlib.redirect_stdout(io.StringIO()):
                diagnostics.annotate(
                    argparse.Namespace(
                        sample_dir=sample,
                        output_root=root,
                        status="limited",
                        scope="system",
                        reason="system counters unavailable",
                    )
                )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                diagnostics.status(argparse.Namespace(output_root=root, limit=10))
            result = json.loads(output.getvalue())
            self.assertEqual(result["samples"][0]["evidence_quality"]["status"], "limited")
            self.assertEqual(result["samples"][0]["evidence_quality"]["scope"], "system")


if __name__ == "__main__":
    unittest.main()
