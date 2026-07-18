#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _bridge import windows_kernel_pool_governance as governance


MIB = 1024 * 1024
GIB = 1024 * MIB


def summary(
    captured_at: str,
    *,
    nonpaged: int,
    nvidia: int,
    scheduler: int,
    firewall: int,
    last_boot_time: str = "",
) -> dict[str, object]:
    return {
        "schema": "windows_kernel_pool_diagnostics.sample.v1",
        "ok": True,
        "captured_at": captured_at,
        "label": "test",
        "evidence_mode": "summary_only",
        "system": {
            "uptime_hours": 10,
            "last_boot_time": last_boot_time,
            "nvidia_driver_version": "610.62",
            "pool_nonpaged_bytes": nonpaged,
            "available_memory_mb": 2048,
            "committed_percent": 70,
        },
        "evidence_quality": {"status": "valid"},
        "pool_totals": {"Nonp": nonpaged, "Paged": GIB},
        "category_bytes": {
            "nvidia": nvidia,
            "gpu_scheduler": scheduler,
            "firewall_filter": firewall,
        },
        "top_by_bytes": [
            {
                "tag": "NvLH",
                "type": "Nonp",
                "bytes": nvidia,
                "diff": 100,
                "mapped": "nvlddmkm.sys",
            }
        ],
    }


class KernelPoolGovernanceTests(unittest.TestCase):
    def test_index_summary_is_idempotent_and_has_no_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = summary(
                "2026-07-15T00:00:00+08:00",
                nonpaged=5 * GIB,
                nvidia=2 * GIB,
                scheduler=2 * GIB,
                firewall=700 * MIB,
            )
            source = root / "summary.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            governance.index_summary(payload, source, root)
            governance.index_summary(payload, source, root)
            metrics = governance.metrics(root)
            self.assertEqual(metrics["sample_count"], 1)
            self.assertEqual(metrics["category_rows"], 3)
            self.assertEqual(metrics["tag_rows"], 1)
            self.assertTrue(governance.validate(root)["ok"])

    def test_backfill_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, hour in enumerate((0, 1)):
                sample_dir = root / f"sample-{index}"
                sample_dir.mkdir()
                payload = summary(
                    f"2026-07-15T0{hour}:00:00+08:00",
                    nonpaged=(4 + index) * GIB,
                    nvidia=(1 + index) * GIB,
                    scheduler=2 * GIB,
                    firewall=(600 + index * 100) * MIB,
                )
                (sample_dir / "summary.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
            self.assertTrue(governance.backfill(root)["ok"])
            self.assertTrue(governance.backfill(root)["ok"])
            self.assertEqual(governance.metrics(root)["sample_count"], 2)

    def test_doctor_reports_graphics_and_firewall_growth(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            older = summary(
                "2026-07-15T00:00:00+08:00",
                nonpaged=5 * GIB,
                nvidia=2 * GIB,
                scheduler=2 * GIB,
                firewall=600 * MIB,
            )
            newer = summary(
                "2026-07-15T01:00:00+08:00",
                nonpaged=5 * GIB + 512 * MIB,
                nvidia=2 * GIB + 64 * MIB,
                scheduler=2 * GIB + 64 * MIB,
                firewall=800 * MIB,
            )
            governance.index_summary(older, root / "older.json", root)
            governance.index_summary(newer, root / "newer.json", root)
            result = governance.doctor(root)
            codes = {issue["code"] for issue in result["issues"]}
            self.assertFalse(result["ok"])
            self.assertIn("nonpaged_pool_high", codes)
            self.assertIn("nonpaged_pool_growing", codes)
            self.assertIn("graphics_kernel_pool_pressure", codes)
            self.assertIn("firewall_filter_pool_growth", codes)

    def test_schedule_apply_requires_exact_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = governance.schedule_apply(
                Path(__file__), root, confirm="install-kernel-pool-monitor"
            )
            self.assertFalse(result["applied"])
            self.assertEqual(result["reason"], "explicit_confirmation_required")
            self.assertEqual(result["confirmation"], governance.SCHEDULE_CONFIRMATION)
            self.assertIn("--summary-only", result["arguments"])

    def test_doctor_does_not_mix_samples_from_before_reboot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            older = summary(
                "2026-07-15T00:00:00+08:00",
                nonpaged=2 * GIB,
                nvidia=GIB,
                scheduler=GIB,
                firewall=100 * MIB,
            )
            newer = summary(
                "2026-07-15T02:00:00+08:00",
                nonpaged=5 * GIB,
                nvidia=2 * GIB,
                scheduler=2 * GIB,
                firewall=700 * MIB,
                last_boot_time="2026-07-15T01:00:00+08:00",
            )
            governance.index_summary(older, root / "older.json", root)
            governance.index_summary(newer, root / "newer.json", root)
            result = governance.doctor(root)
            codes = {issue["code"] for issue in result["issues"]}
            self.assertEqual(result["analyzed_sample_count"], 1)
            self.assertNotIn("nonpaged_pool_growing", codes)
            self.assertIsNone(result["rates_mb_per_hour"]["nonpaged"])


if __name__ == "__main__":
    unittest.main()
