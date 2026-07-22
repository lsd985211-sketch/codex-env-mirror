#!/usr/bin/env python3
"""Regression tests for platform-aware owner validators during WSL migration."""

# ruff: noqa: E402 - owner imports intentionally follow the local bridge path bootstrap.

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import cli_anything_governance as cli_anything
import codex_state_audit
import local_mcp_hub
import mcp_execution_priority
import platform_paths
from shared import codex_scheduler_runner


class WslPlatformOwnerValidatorTests(unittest.TestCase):
    def test_windows_startup_audit_paths_round_trip_through_host_projection(self) -> None:
        mapped = codex_state_audit.host_path(r"C:\Users\45543\.codex\config.toml")
        self.assertEqual(Path("/mnt/c/Users/45543/.codex/config.toml"), mapped)
        self.assertEqual(r"C:\Users\45543\.codex\config.toml", codex_state_audit.windows_path_text(mapped))

    def test_platform_paths_separate_work_git_from_windows_host_projection(self) -> None:
        exported = platform_paths.exported_environment()
        self.assertEqual(Path(__file__).resolve().parents[2], platform_paths.worktree_root())
        self.assertEqual(
            Path("/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager"),
            platform_paths.host_compatibility_root(),
        )
        self.assertNotEqual(exported["WORKTREE_ROOT"], exported["WINDOWS_HOST_COMPATIBILITY_ROOT"])

    def test_platform_paths_translate_wsl_worktree_for_windows_owner(self) -> None:
        source = "/home/codexlab/work/codex-workspace/workspace"
        translated = platform_paths.host_accessible_path(source, platform_name="nt")
        self.assertEqual(
            Path(r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace"),
            translated,
        )

    def test_platform_paths_keep_existing_wsl_unc_idempotent_for_windows_owner(self) -> None:
        source = Path(r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace")
        self.assertEqual(source, platform_paths.host_accessible_path(source, platform_name="nt"))
        self.assertEqual(
            "/home/codexlab/work/codex-workspace/workspace",
            platform_paths.wsl_linux_path_text(source),
        )

    def test_platform_paths_translate_windows_mount_for_windows_owner(self) -> None:
        translated = platform_paths.host_accessible_path("/mnt/c/Users/45543/file.txt", platform_name="nt")
        self.assertEqual(Path(r"C:\Users\45543\file.txt"), translated)

    def test_platform_paths_translate_windows_drive_for_wsl_owner(self) -> None:
        translated = platform_paths.host_accessible_path(r"C:\Users\45543\file.txt", platform_name="posix")
        self.assertEqual(Path("/mnt/c/Users/45543/file.txt"), translated)

    def test_platform_paths_compare_windows_and_wsl_spellings(self) -> None:
        windows_path = r"C:\Users\45543\Desktop\Codex资源库\memory\governance\memory_absorption_index.json"
        wsl_path = "/mnt/c/Users/45543/Desktop/Codex资源库/memory/governance/memory_absorption_index.json"
        self.assertTrue(platform_paths.same_host_path(windows_path, wsl_path))
        self.assertFalse(platform_paths.same_host_path(windows_path, wsl_path + ".other"))

    def test_platform_paths_resolve_windows_user_root_from_wsl(self) -> None:
        self.assertEqual(Path("/mnt/c/Users/45543"), platform_paths.windows_user_root())

    def test_platform_paths_resolve_windows_cc_switch_home_from_wsl(self) -> None:
        self.assertEqual(Path("/mnt/c/Users/45543/.cc-switch"), platform_paths.cc_switch_home())

    def test_resource_library_root_is_platform_native_on_wsl(self) -> None:
        self.assertEqual(
            Path("/mnt/c/Users/45543/Desktop/Codex资源库"),
            platform_paths.resource_library_root(),
        )

    def test_resource_library_consumers_use_platform_authority(self) -> None:
        consumers = (
            "shared/codex_scheduler_runner.py",
            "shared/record_store_maintenance.py",
            "shared/system_maintenance_cli.py",
            "resource_library_catalog.py",
            "defender_governance.py",
            "shared/codex_reporter.py",
            "shared/performance_maintenance_job.py",
            "backup_hygiene_doctor.py",
            "shared/email_scheduler.py",
            "shared/resource_event_store.py",
            "mobile_openclaw_bridge/mobile_openclaw_cli.py",
        )
        legacy_literal = r'r"C:\Users\45543\Desktop\Codex资源库'
        for relative in consumers:
            with self.subTest(relative=relative):
                source = (BRIDGE / relative).read_text(encoding="utf-8")
                self.assertIn("resource_library_root", source)
                self.assertNotIn(legacy_literal, source)

    def test_read_only_resource_catalog_does_not_create_windows_literal_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            proc = subprocess.run(
                [sys.executable, str(BRIDGE / "resource_library_catalog.py"), "snapshot"],
                cwd=cwd,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertFalse(any(path.name.startswith("C:") for path in cwd.iterdir()))

    def test_windows_scheduler_action_uses_host_projection_from_wsl(self) -> None:
        task = next(
            item
            for item in codex_scheduler_runner.desired_tasks()
            if item["id"] == "bridge_appserver_idle_restart_dry_run"
        )
        self.assertEqual(
            r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\shared\restart-bridge-appserver-if-idle.ps1",
            task["action"]["command"][0],
        )

    def test_hub_uses_wsl_memory_and_host_email_state(self) -> None:
        self.assertEqual(
            Path.home() / ".local" / "share" / "codex" / "memory" / "pmb" / "data",
            local_mcp_hub.PMB_HOME,
        )
        self.assertEqual(
            Path("/mnt/c/Users/45543/Desktop/Codex资源库/文档/系统维护/索引/record_store.sqlite"),
            local_mcp_hub.RECORD_STORE_INDEX_PATH,
        )
        self.assertEqual(
            Path("/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager/_bridge/shared/email_scheduler_state/email_state.sqlite"),
            local_mcp_hub.EMAIL_STATE_INDEX_PATH,
        )

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
