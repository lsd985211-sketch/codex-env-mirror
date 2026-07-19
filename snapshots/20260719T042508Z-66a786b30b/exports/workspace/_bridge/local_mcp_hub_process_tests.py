#!/usr/bin/env python3
"""Regression tests for the local MCP Hub process lifecycle boundary."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_mcp_hub_process as hub_process


class HubBytecodeCacheTests(unittest.TestCase):
    def _write_cache(self, source: Path) -> Path:
        source.write_text("VALUE = 1\n", encoding="utf-8")
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(b"test-pyc")
        return cache

    def test_candidates_only_include_hub_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hub_cache = self._write_cache(root / "local_mcp_hub.py")
            worker_cache = self._write_cache(root / "local_mcp_hub_worker.py")
            unrelated_cache = self._write_cache(root / "resource_search.py")

            candidates = hub_process.hub_bytecode_cache_candidates(root)

            self.assertIn(hub_cache.resolve(), candidates)
            self.assertIn(worker_cache.resolve(), candidates)
            self.assertNotIn(unrelated_cache.resolve(), candidates)

    def test_dry_run_reports_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = self._write_cache(root / "local_mcp_hub.py")

            result = hub_process.clear_hub_bytecode_cache(module_dir=root, dry_run=True)

            self.assertTrue(result["ok"])
            self.assertTrue(cache.exists())
            self.assertEqual(result["removed_bytecode_cache"], [])
            self.assertIn(str(cache.resolve()), result["candidate_bytecode_cache"])

    def test_confirmed_cleanup_preserves_unrelated_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hub_cache = self._write_cache(root / "local_mcp_hub.py")
            unrelated_cache = self._write_cache(root / "other_module.py")

            result = hub_process.clear_hub_bytecode_cache(module_dir=root, dry_run=False)

            self.assertTrue(result["ok"])
            self.assertFalse(hub_cache.exists())
            self.assertTrue(unrelated_cache.exists())
            self.assertEqual(result["removed_bytecode_cache"], [str(hub_cache.resolve())])

    def test_reload_dry_run_never_stops_or_starts(self) -> None:
        with patch.object(hub_process, "local_hub_processes", return_value=[]), patch.object(
            hub_process, "clear_hub_bytecode_cache", return_value={"ok": True, "dry_run": True}
        ) as clear_cache, patch.object(hub_process, "stop_process") as stop, patch.object(
            hub_process, "start_local_hub_task"
        ) as start:
            result = hub_process.reload_local_hub(confirm_reload=False)

        self.assertTrue(result["dry_run"])
        clear_cache.assert_called_once_with(dry_run=True)
        stop.assert_not_called()
        start.assert_not_called()

    def test_reload_aborts_before_stop_when_cache_cleanup_fails(self) -> None:
        with patch.object(
            hub_process,
            "local_hub_processes",
            return_value=[{"pid": 123, "parent_pid": 1, "command_line": "local_mcp_hub.py serve --port 18881"}],
        ), patch.object(
            hub_process,
            "clear_hub_bytecode_cache",
            return_value={"ok": False, "dry_run": False, "failed_bytecode_cache": [{"path": "x", "error": "denied"}]},
        ), patch.object(hub_process, "stop_process") as stop, patch.object(
            hub_process, "start_local_hub_task"
        ) as start:
            result = hub_process.reload_local_hub(confirm_reload=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "hub_bytecode_cache_cleanup_failed")
        stop.assert_not_called()
        start.assert_not_called()

    def test_reload_waits_until_health_becomes_ready(self) -> None:
        process = [{"pid": 456, "parent_pid": 1, "command_line": "local_mcp_hub.py serve --port 18881"}]
        with patch.object(hub_process, "local_hub_processes", side_effect=[process, process, process]), patch.object(
            hub_process, "clear_hub_bytecode_cache", return_value={"ok": True, "dry_run": False}
        ), patch.object(hub_process, "stop_process", return_value={"ok": True}), patch.object(
            hub_process, "start_local_hub_task", return_value={"ok": True}
        ), patch.object(
            hub_process, "http_get_json", side_effect=[OSError("not ready"), {"ok": True}]
        ), patch.object(hub_process.time, "sleep"):
            result = hub_process.reload_local_hub(confirm_reload=True, wait_seconds=2.0)

        self.assertTrue(result["ok"])
        self.assertEqual(result["health_attempts"], 2)
        self.assertTrue(result["health"]["ok"])

    def test_reload_timeout_retains_last_health_error(self) -> None:
        process = [{"pid": 456, "parent_pid": 1, "command_line": "local_mcp_hub.py serve --port 18881"}]
        with patch.object(hub_process, "local_hub_processes", side_effect=[process, process]), patch.object(
            hub_process, "clear_hub_bytecode_cache", return_value={"ok": True, "dry_run": False}
        ), patch.object(hub_process, "stop_process", return_value={"ok": True}), patch.object(
            hub_process, "start_local_hub_task", return_value={"ok": True}
        ), patch.object(hub_process, "http_get_json", side_effect=OSError("still starting")), patch.object(
            hub_process.time, "monotonic", side_effect=[0.0, 1.0]
        ):
            result = hub_process.reload_local_hub(confirm_reload=True, wait_seconds=0.5)

        self.assertFalse(result["ok"])
        self.assertEqual(result["health_attempts"], 1)
        self.assertIn("still starting", result["health"]["reason"])


if __name__ == "__main__":
    unittest.main()
