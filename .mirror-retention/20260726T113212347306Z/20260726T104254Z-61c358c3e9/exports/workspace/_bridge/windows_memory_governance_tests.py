from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import windows_memory_governance
from windows_memory_governance import build_pressure_layers, classify_process, index_summary, metrics, process_family, summarize, trends


class WindowsMemoryGovernanceTests(unittest.TestCase):
    def test_live_collection_uses_shared_resolved_encoded_command(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")
        with patch.object(
            windows_memory_governance,
            "powershell_encoded_command",
            return_value=["/host/powershell.exe", "-EncodedCommand", "abc"],
        ) as build, patch.object(windows_memory_governance.subprocess, "run", return_value=completed) as run:
            windows_memory_governance._run_powershell("Write-Output '{}'", timeout=5)

        build.assert_called_once_with("Write-Output '{}'", no_logo=True)
        self.assertEqual("/host/powershell.exe", run.call_args.args[0][0])

    def test_classifies_codex_webview_before_generic_browser(self) -> None:
        row = {"name": "msedgewebview2.exe", "command_line": "--user-data-dir=C:\\Users\\x\\Codex\\web"}

        self.assertEqual(classify_process(row), "codex_desktop")

    def test_classifies_webview_by_owning_application(self) -> None:
        clash = {"name": "msedgewebview2.exe", "command_line": "--webview-exe-name=clash-verge.exe --type=renderer"}
        search = {"name": "msedgewebview2.exe", "command_line": "--webview-exe-name=SearchHost.exe --type=renderer"}
        app = {"name": "msedgewebview2.exe", "command_line": "--webview-exe-name=cc-switch.exe --type=renderer"}

        self.assertEqual(classify_process(clash), "network_remote")
        self.assertEqual(classify_process(search), "windows_core")
        self.assertEqual(classify_process(app), "user_applications")
        self.assertEqual(process_family(clash), "webview:clash-verge.exe:renderer")

    def test_windows_hosts_do_not_pollute_user_or_runtime_categories(self) -> None:
        self.assertEqual(classify_process({"name": "Secure System", "command_line": ""}), "windows_core")
        self.assertEqual(classify_process({"name": "conhost.exe", "command_line": ""}), "windows_core")
        self.assertEqual(classify_process({"name": "Widgets.exe", "command_line": ""}), "windows_core")

    def test_classifies_desktop_weixin_as_mcp_session(self) -> None:
        row = {"name": "python.exe", "command_line": "python _bridge\\desktop_weixin_mcp_server.py"}

        self.assertEqual(classify_process(row), "mcp_sessions")

    def test_family_uses_script_name(self) -> None:
        row = {"name": "python.exe", "command_line": "python C:\\work\\worker.py --serve"}

        self.assertEqual(process_family(row), "worker.py")

    def test_family_uses_python_module_and_svchost_service_identity(self) -> None:
        pmb = {"name": "python.exe", "command_line": "python -m pmb.cli daemon run"}
        service_host = {"name": "svchost.exe", "command_line": "svchost -k netsvcs", "services": ["Dnscache", "NlaSvc"]}

        self.assertEqual(process_family(pmb), "python-module:pmb.cli")
        self.assertEqual(process_family(service_host), "svchost:dnscache+nlasvc")

    def test_summary_covers_every_process(self) -> None:
        snapshot = {
            "captured_at": "2026-07-15T00:00:00+00:00",
            "system": {"total_memory_mb": 16000, "available_memory_mb": 1600},
            "processes": [
                {"pid": 1, "name": "ChatGPT.exe", "command_line": "", "working_set_bytes": 100 * 1024 * 1024, "private_bytes": 120 * 1024 * 1024, "handles": 10},
                {"pid": 2, "name": "MsMpEng.exe", "command_line": "", "working_set_bytes": 50 * 1024 * 1024, "private_bytes": 60 * 1024 * 1024, "handles": 20},
            ],
        }

        result = summarize(snapshot)

        self.assertEqual(result["process_count"], 2)
        self.assertEqual(sum(item["process_count"] for item in result["categories"]), 2)
        self.assertEqual(result["system"]["used_percent"], 90.0)
        self.assertEqual(result["process_totals"]["working_set_mb"], 150.0)
        self.assertEqual(result["process_totals"]["private_mb"], 180.0)
        self.assertEqual(result["process_totals"]["private_residency_gap_mb"], 30.0)
        self.assertEqual(result["process_totals"]["handles"], 30)

    def test_sqlite_metrics_preserve_actionable_category_and_family_rows(self) -> None:
        summary = {
            "captured_at": "2026-07-15T00:00:00+00:00",
            "system": {"total_memory_mb": 16000, "available_memory_mb": 1600, "used_percent": 90, "committed_percent": 80, "pool_nonpaged_mb": 100, "pool_paged_mb": 50, "pagefile_count": 1, "pagefile_allocated_mb": 8000, "pagefile_current_usage_mb": 2000, "pagefile_peak_usage_mb": 3000, "pagefile_usage_percent": 25, "pagefile_peak_usage_percent": 37.5, "compression_metric_state": "unavailable"},
            "process_count": 1,
            "categories": [{"category": "codex_desktop", "process_count": 1, "working_set_mb": 500, "private_mb": 700, "handles": 20}],
            "top_families": [{"category": "codex_desktop", "family": "chatgpt.exe", "process_count": 1, "working_set_mb": 500, "private_mb": 700, "handles": 20, "top_pid": 10, "top_pid_working_set_mb": 500}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            index_summary(summary, root)

            result = metrics(root)

        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["categories"][0]["category"], "codex_desktop")
        self.assertEqual(result["top_families"][0]["family"], "chatgpt.exe")
        self.assertEqual(result["top_private_families"][0]["family"], "chatgpt.exe")
        self.assertEqual(result["top_handle_families"][0]["family"], "chatgpt.exe")
        self.assertEqual(result["latest"]["pagefile_current_usage_mb"], 2000.0)

    def test_family_rankings_cover_working_set_private_bytes_and_handles(self) -> None:
        snapshot = {
            "captured_at": "2026-07-15T00:00:00+00:00",
            "system": {"total_memory_mb": 16000, "available_memory_mb": 8000},
            "processes": [
                {"pid": 1, "name": "ChatGPT.exe", "command_line": "", "working_set_bytes": 500 * 1024 * 1024, "private_bytes": 600 * 1024 * 1024, "handles": 100},
                {"pid": 2, "name": "python.exe", "command_line": "python worker.py", "working_set_bytes": 20 * 1024 * 1024, "private_bytes": 900 * 1024 * 1024, "handles": 50},
                {"pid": 3, "name": "service.exe", "command_line": "", "working_set_bytes": 10 * 1024 * 1024, "private_bytes": 50 * 1024 * 1024, "handles": 2000},
            ],
        }

        result = summarize(snapshot)

        self.assertEqual(result["top_families"][0]["family"], "chatgpt.exe")
        self.assertEqual(result["top_private_families"][0]["family"], "worker.py")
        self.assertEqual(result["top_handle_families"][0]["family"], "service.exe")
        self.assertEqual(result["commit_heavy_families"][0]["family"], "worker.py")
        self.assertEqual(result["commit_heavy_families"][0]["top_private_pid"], 2)
        self.assertEqual(result["commit_heavy_families"][0]["private_residency_gap_mb"], 880.0)

    def test_trends_return_category_delta_not_only_counts(self) -> None:
        base = {
            "system": {"total_memory_mb": 16000, "available_memory_mb": 1600, "used_percent": 90, "committed_percent": 80, "pool_nonpaged_mb": 100, "pool_paged_mb": 50, "pagefile_current_usage_mb": 1000},
            "process_count": 1,
            "top_families": [{"category": "automation_services", "family": "python-module:pmb.cli", "process_count": 1, "working_set_mb": 10, "private_mb": 700, "handles": 30, "top_pid": 4, "top_pid_working_set_mb": 10, "top_private_pid": 4, "top_pid_private_mb": 700, "private_residency_gap_mb": 690, "private_to_working_set_ratio": 70}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = {**base, "captured_at": "2026-07-15T00:00:00+00:00", "categories": [{"category": "codex_desktop", "process_count": 1, "working_set_mb": 500, "private_mb": 700, "handles": 20}]}
            second = {**base, "captured_at": "2026-07-15T00:10:00+00:00", "system": {**base["system"], "pagefile_current_usage_mb": 1250}, "categories": [{"category": "codex_desktop", "process_count": 1, "working_set_mb": 650, "private_mb": 800, "handles": 22}]}
            index_summary(first, root)
            index_summary(second, root)

            result = trends(root, hours=100000, limit=10, categories=["codex_desktop"])

        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["category_deltas_mb"]["codex_desktop"], 150.0)
        self.assertEqual(result["category_private_deltas_mb"]["codex_desktop"], 100.0)
        self.assertEqual(result["category_handle_deltas"]["codex_desktop"], 2)
        self.assertEqual(result["samples"][-1]["category_working_set_mb"]["codex_desktop"], 650.0)
        self.assertEqual(result["system_deltas"]["pagefile_current_usage_mb"], 250.0)

    def test_trends_do_not_compare_across_boot_or_classifier_versions(self) -> None:
        base = {
            "system": {"total_memory_mb": 16000, "available_memory_mb": 4000, "used_percent": 75, "committed_percent": 60, "pool_nonpaged_mb": 100, "pool_paged_mb": 50, "last_boot_time": "boot-a"},
            "process_count": 1,
            "top_families": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = {**base, "classifier_version": "legacy", "captured_at": "2026-07-15T00:00:00+00:00", "categories": [{"category": "windows_core", "process_count": 1, "working_set_mb": 100, "private_mb": 120, "handles": 10}]}
            current_1 = {**base, "classifier_version": "current", "captured_at": "2026-07-15T00:10:00+00:00", "categories": [{"category": "windows_core", "process_count": 1, "working_set_mb": 500, "private_mb": 520, "handles": 20}]}
            current_2 = {**base, "classifier_version": "current", "captured_at": "2026-07-15T00:20:00+00:00", "categories": [{"category": "windows_core", "process_count": 1, "working_set_mb": 550, "private_mb": 570, "handles": 25}]}
            index_summary(old, root)
            index_summary(current_1, root)
            index_summary(current_2, root)

            result = trends(root, hours=100000, limit=10)

        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["excluded_by_classifier_count"], 1)
        self.assertEqual(result["category_deltas_mb"]["windows_core"], 50.0)

    def test_existing_sqlite_schema_is_migrated_in_place(self) -> None:
        summary = {
            "captured_at": "2026-07-15T00:00:00+00:00",
            "system": {"total_memory_mb": 16000, "available_memory_mb": 1600, "used_percent": 90, "committed_percent": 80, "pool_nonpaged_mb": 100, "pool_paged_mb": 50, "committed_mb": 12000, "commit_limit_mb": 24000, "cache_mb": 200, "standby_cache_mb": 1000, "process_private_mb": 700, "process_handle_count": 20, "pagefile_count": 1, "pagefile_allocated_mb": 16000, "pagefile_current_usage_mb": 4000, "pagefile_peak_usage_mb": 6000, "pagefile_usage_percent": 25, "pagefile_peak_usage_percent": 37.5, "compression_metric_state": "unavailable"},
            "process_count": 1,
            "categories": [],
            "top_families": [{"category": "automation_services", "family": "python-module:pmb.cli", "process_count": 1, "working_set_mb": 10, "private_mb": 700, "handles": 30, "top_pid": 4, "top_pid_working_set_mb": 10, "top_private_pid": 4, "top_pid_private_mb": 700, "private_residency_gap_mb": 690, "private_to_working_set_ratio": 70}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.mkdir(parents=True, exist_ok=True)
            database = root / "windows_memory_trends.sqlite"
            conn = sqlite3.connect(database)
            conn.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL UNIQUE, total_memory_mb REAL NOT NULL, available_memory_mb REAL NOT NULL, used_percent REAL NOT NULL, committed_percent REAL NOT NULL, pool_nonpaged_mb REAL NOT NULL, pool_paged_mb REAL NOT NULL, process_count INTEGER NOT NULL, indexed_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE categories (sample_id INTEGER NOT NULL, category TEXT NOT NULL, process_count INTEGER NOT NULL, working_set_mb REAL NOT NULL, private_mb REAL NOT NULL, handles INTEGER NOT NULL, PRIMARY KEY(sample_id, category))")
            conn.execute("CREATE TABLE families (sample_id INTEGER NOT NULL, category TEXT NOT NULL, family TEXT NOT NULL, process_count INTEGER NOT NULL, working_set_mb REAL NOT NULL, private_mb REAL NOT NULL, handles INTEGER NOT NULL, top_pid INTEGER NOT NULL, top_pid_working_set_mb REAL NOT NULL, PRIMARY KEY(sample_id, category, family))")
            conn.commit()
            conn.close()

            index_summary(summary, root)
            result = metrics(root)

        self.assertEqual(result["latest"]["committed_mb"], 12000.0)
        self.assertEqual(result["latest"]["standby_cache_mb"], 1000.0)
        self.assertEqual(result["latest"]["pagefile_current_usage_mb"], 4000.0)
        self.assertEqual(result["top_families"][0]["private_residency_gap_mb"], 690.0)

    def test_pressure_layers_prefer_specialized_kernel_owner(self) -> None:
        summary = {
            "system": {"used_percent": 88, "available_memory_mb": 1800, "committed_percent": 73, "pool_nonpaged_mb": 6800, "pool_paged_mb": 2900},
            "process_count": 10,
            "process_totals": {"working_set_mb": 2000, "private_mb": 3000, "handles": 1000},
            "categories": [{"category": "codex_desktop", "working_set_mb": 900}],
        }
        kernel = {"ok": False, "issues": [{"severity": "risk", "code": "graphics_kernel_pool_pressure"}]}
        resource = {"ok": True, "mcp_instance_budget_state": "ok"}

        result = build_pressure_layers(summary, kernel, resource)

        self.assertEqual(result["dominant_layer"], "kernel_pool")
        self.assertEqual(result["next_owner"], "windows_kernel_pool_diagnostics.py")


if __name__ == "__main__":
    unittest.main()
