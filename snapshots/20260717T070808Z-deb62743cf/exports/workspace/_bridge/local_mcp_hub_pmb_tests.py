#!/usr/bin/env python3
"""Regression tests for PMB daemon recovery through the local MCP Hub."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import local_mcp_hub as hub
from local_mcp_hub_pmb_runtime import PmbRecoverySingleFlight


class PmbHubRecoveryTests(unittest.TestCase):
    def test_daemon_ensure_uses_console_python(self) -> None:
        with patch.object(hub, "console_python_executable", return_value="C:\\Python314\\python.exe"), patch.object(
            hub,
            "run_json_command",
            return_value={"ok": True},
        ) as run_command:
            result = hub.pmb_daemon_ensure()

        self.assertTrue(result["ok"])
        run_command.assert_called_once_with(
            ["C:\\Python314\\python.exe", str(hub.BRIDGE_ROOT / "local_pmb_memory.py"), "daemon-ensure"],
            timeout=45,
        )

    def test_transport_error_recovers_once_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "daemon.token"
            token_path.write_text("token", encoding="utf-8")
            with patch.object(hub, "PMB_TOKEN_PATH", token_path), patch.object(
                hub,
                "_pmb_tool_call_once",
                side_effect=[
                    {"ok": False, "reason": "connection refused", "transport_error": True},
                    {"ok": True, "workspace": "default"},
                ],
            ) as call_once, patch.object(
                hub,
                "pmb_daemon_ensure",
                return_value={"ok": True},
            ) as ensure:
                result = hub.pmb_tool_call("stats", {})

        self.assertTrue(result["ok"])
        self.assertNotIn("transport_error", result)
        self.assertTrue(result["daemon_recovery"]["attempted"])
        ensure.assert_called_once_with()
        self.assertEqual(call_once.call_count, 2)

    def test_application_error_does_not_restart_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "daemon.token"
            token_path.write_text("token", encoding="utf-8")
            with patch.object(hub, "PMB_TOKEN_PATH", token_path), patch.object(
                hub,
                "_pmb_tool_call_once",
                return_value={"ok": False, "reason": "project_not_found"},
            ) as call_once, patch.object(hub, "pmb_daemon_ensure") as ensure:
                result = hub.pmb_tool_call("project_overview", {"name": "missing"})

        self.assertFalse(result["ok"])
        self.assertNotIn("daemon_recovery", result)
        ensure.assert_not_called()
        call_once.assert_called_once_with("project_overview", {"name": "missing"})

    def test_recovery_never_retries_more_than_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "daemon.token"
            token_path.write_text("token", encoding="utf-8")
            with patch.object(hub, "PMB_TOKEN_PATH", token_path), patch.object(
                hub,
                "_pmb_tool_call_once",
                return_value={"ok": False, "reason": "connection refused", "transport_error": True},
            ) as call_once, patch.object(
                hub,
                "pmb_daemon_ensure",
                return_value={"ok": True},
            ) as ensure:
                result = hub.pmb_tool_call("stats", {})

        self.assertFalse(result["ok"])
        self.assertNotIn("transport_error", result)
        self.assertTrue(result["daemon_recovery"]["ok"])
        ensure.assert_called_once_with()
        self.assertEqual(call_once.call_count, 2)

    def test_missing_token_starts_daemon_before_first_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "missing.token"
            with patch.object(hub, "PMB_TOKEN_PATH", token_path), patch.object(
                hub,
                "_pmb_tool_call_once",
                return_value={"ok": True, "workspace": "default"},
            ) as call_once, patch.object(
                hub,
                "pmb_daemon_ensure",
                return_value={"ok": True},
            ) as ensure:
                result = hub.pmb_tool_call("workspace_info", {})

        self.assertTrue(result["ok"])
        self.assertTrue(result["daemon_recovery"]["ok"])
        ensure.assert_called_once_with()
        call_once.assert_called_once_with("workspace_info", {})

    def test_failed_recovery_does_not_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "daemon.token"
            token_path.write_text("token", encoding="utf-8")
            with patch.object(hub, "PMB_TOKEN_PATH", token_path), patch.object(
                hub,
                "_pmb_tool_call_once",
                return_value={"ok": False, "reason": "connection refused", "transport_error": True},
            ) as call_once, patch.object(
                hub,
                "pmb_daemon_ensure",
                return_value={"ok": False, "reason": "start_failed"},
            ) as ensure:
                result = hub.pmb_tool_call("stats", {})

        self.assertFalse(result["ok"])
        self.assertFalse(result["daemon_recovery"]["ok"])
        ensure.assert_called_once_with()
        call_once.assert_called_once_with("stats", {})

    def test_parallel_transport_failures_share_one_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "daemon.token"
            token_path.write_text("token", encoding="utf-8")
            local_state = threading.local()

            def call_once(tool: str, arguments: dict) -> dict:
                attempt = int(getattr(local_state, "attempt", 0))
                local_state.attempt = attempt + 1
                if attempt == 0:
                    return {"ok": False, "reason": "connection refused", "transport_error": True}
                return {"ok": True, "tool": tool}

            def ensure_once() -> dict:
                time.sleep(0.1)
                return {"ok": True}

            coordinator = PmbRecoverySingleFlight()
            with patch.object(hub, "PMB_TOKEN_PATH", token_path), patch.object(
                hub,
                "PMB_RECOVERY_SINGLEFLIGHT",
                coordinator,
            ), patch.object(
                hub,
                "_pmb_tool_call_once",
                side_effect=call_once,
            ), patch.object(
                hub,
                "pmb_daemon_ensure",
                side_effect=ensure_once,
            ) as ensure:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda _: hub.pmb_tool_call("stats", {}), range(8)))

        self.assertEqual(ensure.call_count, 1)
        self.assertTrue(all(item.get("ok") for item in results))
        self.assertTrue(any(item["daemon_recovery"]["coalesced"] for item in results))


if __name__ == "__main__":
    unittest.main()
