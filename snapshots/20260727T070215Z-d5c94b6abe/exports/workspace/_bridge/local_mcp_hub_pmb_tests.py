#!/usr/bin/env python3
"""Regression tests for the Hub-to-systemd PMB connection boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_mcp_hub as hub


class PmbHubConnectionTests(unittest.TestCase):
    def test_missing_token_reports_service_without_starting_a_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "missing.token"
            with patch.object(hub, "PMB_TOKEN_PATH", token_path):
                result = hub.pmb_tool_call("workspace_info", {})

        self.assertFalse(result["ok"])
        self.assertEqual("pmb_daemon_token_missing", result["reason"])
        self.assertEqual("codex-pmb-memory.service", result["daemon_service"]["managed_by"])
        self.assertFalse(result["daemon_service"]["hub_may_start_daemon"])
        self.assertNotIn("transport_error", result)

    def test_transport_failure_is_not_retried_or_recovered_by_hub(self) -> None:
        with patch.object(
            hub,
            "_pmb_tool_call_once",
            return_value={"ok": False, "reason": "connection refused", "transport_error": True},
        ) as call_once:
            result = hub.pmb_tool_call("stats", {})

        self.assertFalse(result["ok"])
        self.assertEqual(1, call_once.call_count)
        self.assertTrue(result["daemon_service"]["retryable"])
        self.assertFalse(result["daemon_service"]["hub_may_start_daemon"])
        self.assertNotIn("daemon_recovery", result)
        self.assertNotIn("transport_error", result)

    def test_application_error_is_returned_without_service_annotation(self) -> None:
        with patch.object(
            hub,
            "_pmb_tool_call_once",
            return_value={"ok": False, "reason": "project_not_found"},
        ) as call_once:
            result = hub.pmb_tool_call("project_overview", {"name": "missing"})

        self.assertFalse(result["ok"])
        self.assertNotIn("daemon_service", result)
        call_once.assert_called_once_with("project_overview", {"name": "missing"})

    def test_success_is_returned_without_lifecycle_noise(self) -> None:
        with patch.object(
            hub,
            "_pmb_tool_call_once",
            return_value={"ok": True, "workspace": "mcsmanager"},
        ) as call_once:
            result = hub.pmb_tool_call("workspace_info", {})

        self.assertTrue(result["ok"])
        self.assertEqual("mcsmanager", result["workspace"])
        self.assertNotIn("daemon_service", result)
        call_once.assert_called_once_with("workspace_info", {})


if __name__ == "__main__":
    unittest.main()
