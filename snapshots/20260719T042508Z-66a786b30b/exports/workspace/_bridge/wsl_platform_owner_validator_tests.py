#!/usr/bin/env python3
"""Regression tests for platform-aware owner validators during WSL migration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import cli_anything_governance as cli_anything
import local_mcp_hub
import mcp_execution_priority


class WslPlatformOwnerValidatorTests(unittest.TestCase):
    def test_wsl_mcp_priority_allows_projected_hub_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config.toml"
            config.write_text(
                """
[mcp_servers.node_repl]
command = "/home/codexlab/.local/bin/codex-node-repl"
required = true

[mcp_servers.custom-slash-commands]
command = "python3"

[mcp_servers.sqlite-scratch]
command = "python3"
""".strip()
                + "\n",
                encoding="utf-8",
            )
            with patch.object(mcp_execution_priority, "runtime_platform", return_value="wsl"), patch.dict(
                mcp_execution_priority.os.environ,
                {"CODEX_CONFIG": str(config)},
            ):
                payload = mcp_execution_priority.validate()
        self.assertTrue(payload["ok"], payload["issues"])
        self.assertEqual(payload["registration_validation"], "wsl_projected_config_contract")

    def test_wsl_office_harness_missing_is_deferred_by_default(self) -> None:
        snap = {
            "ok": True,
            "cli_hub": {
                "command": None,
                "list_ok": False,
                "matrix_list_ok": False,
                "analytics_disabled_for_wrapper": True,
            },
            "skill": {"installed": True, "missing": []},
            "local_harnesses": {"items": []},
            "catalog": {},
        }
        surface = {"ok": False, "surfaces": []}
        with patch.object(cli_anything, "runtime_platform", return_value="wsl"), patch.object(
            cli_anything,
            "windows_office_runtime_available",
            return_value=False,
        ), patch.object(cli_anything, "command_surface", return_value=surface):
            default_payload = cli_anything.validate(snap)
            required_payload = cli_anything.validate(snap, require_office=True)
        self.assertTrue(default_payload["ok"], default_payload["failures"])
        self.assertTrue(default_payload["office_harness"]["deferred"])
        self.assertFalse(required_payload["ok"])
        self.assertIn("office_harness_command_surface_invalid", {item["code"] for item in required_payload["failures"]})

    def test_wsl_hub_keeps_desktop_weixin_platform_deferred(self) -> None:
        service = local_mcp_hub.LocalMcpHub()
        names = {str(tool.get("name") or "") for tool in service.all_tool_specs()}
        self.assertIn("desktop_weixin.capabilities", names)
        self.assertIn("desktop_weixin.status", names)
        result = service.tools_call({"name": "desktop_weixin.status", "arguments": {}})
        text = result["content"][0]["text"]
        self.assertIn("desktop_weixin_platform_deferred", text)
        self.assertTrue(result["isError"])

    def test_wsl_hub_sqlite_paths_resolve_to_existing_sources(self) -> None:
        self.assertTrue(local_mcp_hub.SCRATCH_DB_PATH.exists(), local_mcp_hub.SCRATCH_DB_PATH)
        self.assertTrue(local_mcp_hub.BRIDGE_DB_PATH.exists(), local_mcp_hub.BRIDGE_DB_PATH)


if __name__ == "__main__":
    unittest.main()
