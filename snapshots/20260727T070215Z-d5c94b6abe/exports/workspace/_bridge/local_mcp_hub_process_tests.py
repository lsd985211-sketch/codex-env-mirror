#!/usr/bin/env python3
"""Regression tests for the local MCP Hub process lifecycle boundary."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_mcp_hub_process as hub_process
import local_mcp_hub


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
        with patch.object(hub_process, "hub_service_status", return_value={"unit_exists": False}), patch.object(
            hub_process, "hub_runtime_state", side_effect=[{"processes": process}, {"ok": True, "processes": process}]
        ), patch.object(
            hub_process, "clear_hub_bytecode_cache", return_value={"ok": True, "dry_run": False}
        ), patch.object(hub_process, "stop_process", return_value={"ok": True}), patch.object(
            hub_process, "start_local_hub_task", return_value={"ok": True}
        ), patch.object(
            hub_process, "wait_hub_ready", return_value={"ok": True, "attempts": 2, "health": {"ok": True}}
        ):
            result = hub_process.reload_local_hub(confirm_reload=True, wait_seconds=2.0)

        self.assertTrue(result["ok"])
        self.assertEqual(result["health_attempts"], 2)
        self.assertTrue(result["health"]["ok"])

    def test_reload_timeout_retains_last_health_error(self) -> None:
        process = [{"pid": 456, "parent_pid": 1, "command_line": "local_mcp_hub.py serve --port 18881"}]
        with patch.object(hub_process, "hub_service_status", return_value={"unit_exists": False}), patch.object(
            hub_process, "hub_runtime_state", side_effect=[{"processes": process}, {"ok": False, "processes": process}]
        ), patch.object(
            hub_process, "clear_hub_bytecode_cache", return_value={"ok": True, "dry_run": False}
        ), patch.object(hub_process, "stop_process", return_value={"ok": True}), patch.object(
            hub_process, "start_local_hub_task", return_value={"ok": True}
        ), patch.object(
            hub_process,
            "wait_hub_ready",
            return_value={"ok": False, "attempts": 1, "health": {"ok": False, "reason": "still starting"}},
        ):
            result = hub_process.reload_local_hub(confirm_reload=True, wait_seconds=0.5)

        self.assertFalse(result["ok"])
        self.assertEqual(result["health_attempts"], 1)
        self.assertIn("still starting", result["health"]["reason"])

    def test_unit_is_loopback_only_and_restart_on_failure(self) -> None:
        content = hub_process.hub_unit_content(
            python=Path("/usr/bin/python3"),
            script=Path("/home/test/workspace/_bridge/local_mcp_hub.py"),
        )
        self.assertIn("serve --host 127.0.0.1 --port 18881", content)
        self.assertIn("Restart=on-failure", content)
        self.assertIn("NoNewPrivileges=yes", content)
        self.assertNotIn("0.0.0.0", content)
        spaced = hub_process.hub_unit_content(
            python=Path("/usr/bin/python3"),
            script=Path("/home/test/work space/_bridge/local_mcp_hub.py"),
        )
        self.assertIn(r"WorkingDirectory=/home/test/work\x20space/_bridge", spaced)

    def test_install_service_requires_confirmation_without_write(self) -> None:
        with patch.object(hub_process, "hub_service_plan", return_value={"ok": True, "blockers": []}), patch.object(
            hub_process, "install_user_unit"
        ) as install:
            result = hub_process.install_hub_service("")
        self.assertFalse(result["ok"])
        self.assertEqual(result["required_confirmation"], hub_process.INSTALL_CONFIRM)
        install.assert_not_called()

    def test_runtime_prefers_active_systemd_identity(self) -> None:
        service = {"active": True, "systemd": {"ExecMainPID": "321"}}
        with patch.object(hub_process, "hub_service_status", return_value=service), patch.object(
            hub_process, "local_hub_processes"
        ) as windows, patch.object(hub_process, "http_get_json", return_value={"ok": True}):
            result = hub_process.hub_runtime_state()
        self.assertTrue(result["ok"])
        self.assertEqual(result["authority"], "wsl_user_systemd")
        self.assertEqual(result["processes"][0]["pid"], 321)
        windows.assert_not_called()

    def test_reload_uses_systemd_when_unit_exists(self) -> None:
        before = {"processes": [{"pid": 321, "authority": "wsl_user_systemd"}]}
        after = {"ok": True, "processes": [{"pid": 654, "authority": "wsl_user_systemd"}]}
        with patch.object(hub_process, "hub_service_status", return_value={"unit_exists": True}), patch.object(
            hub_process, "hub_runtime_state", side_effect=[before, after]
        ), patch.object(hub_process, "clear_hub_bytecode_cache", return_value={"ok": True}), patch.object(
            hub_process, "systemctl", return_value={"ok": True}
        ) as systemctl, patch.object(
            hub_process, "wait_hub_ready", return_value={"ok": True, "attempts": 1, "health": {"ok": True}}
        ), patch.object(hub_process, "start_local_hub_task") as windows_start:
            result = hub_process.reload_local_hub(confirm_reload=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["authority"], "wsl_user_systemd")
        systemctl.assert_called_once_with("restart", hub_process.SERVICE_NAME, timeout=60)
        windows_start.assert_not_called()

    def test_windows_interop_commands_use_executable_names(self) -> None:
        completed = unittest.mock.Mock(returncode=0, stdout="", stderr="")
        with patch.object(hub_process.subprocess, "run", return_value=completed) as runner:
            hub_process.local_hub_processes()
            self.assertTrue(any(str(item).lower().endswith("powershell.exe") for item in runner.call_args.args[0]))
            hub_process.start_local_hub_task()
            self.assertTrue(any("schtasks.exe" in str(item).lower() for item in runner.call_args.args[0]))
            hub_process.stop_process(123)
            self.assertTrue(any("taskkill.exe" in str(item).lower() for item in runner.call_args.args[0]))

    def test_windows_powershell_command_uses_encoded_utf16le_source(self) -> None:
        command = hub_process.windows_powershell_command("$path = 'C:\\资源库\\图片'")
        self.assertIn("powershell.exe", str(command[0]).lower())
        self.assertIn("-EncodedCommand", command)
        encoded = command[command.index("-EncodedCommand") + 1]
        from shared.windows_powershell import decode_encoded_command

        self.assertEqual("$path = 'C:\\资源库\\图片'", decode_encoded_command(encoded))

    def test_process_probe_accepts_the_serving_hub_pid(self) -> None:
        completed = unittest.mock.Mock(
            returncode=0,
            stdout='{"ProcessId": 123, "ParentProcessId": 1, "CommandLine": "pythonw.exe local_mcp_hub.py serve --port 18881"}',
            stderr="",
        )
        with patch.object(hub_process.subprocess, "run", return_value=completed), patch.object(
            hub_process.os,
            "getpid",
            return_value=123,
        ):
            processes = hub_process.local_hub_processes()

        self.assertEqual([123], [item["pid"] for item in processes])

    def test_missing_wslinterop_uses_init_without_changing_normal_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            interop = root / "WSLInterop"
            init_path = root / "init"
            init_path.write_text("", encoding="utf-8")
            executable = r"/mnt/c/windows/system32/schtasks.exe"
            cmd = r"/mnt/c/windows/system32/cmd.exe"
            with patch.object(
                hub_process.shutil, "which", side_effect=lambda name: cmd if name == "cmd.exe" else executable
            ):
                fallback = hub_process.windows_interop_command(
                    "schtasks.exe", "/Run", interop_entry=interop, init_path=init_path
                )
                interop.write_text("enabled\n", encoding="utf-8")
                normal = hub_process.windows_interop_command(
                    "schtasks.exe", "/Run", interop_entry=interop, init_path=init_path
                )
            self.assertEqual(
                fallback,
                [str(init_path), cmd, "/d", "/s", "/c", r"C:\windows\system32\schtasks.exe /Run"],
            )
            self.assertEqual(normal, ["schtasks.exe", "/Run"])
            windows_root = root / "Windows"
            windows_root.mkdir()
            self.assertEqual(
                hub_process.windows_interop_cwd(fallback, default=root, windows_system_root=windows_root),
                windows_root,
            )
            self.assertEqual(
                hub_process.windows_interop_cwd(normal, default=root, windows_system_root=windows_root),
                root,
            )

    def test_windows_cli_output_decodes_utf8_then_gb18030(self) -> None:
        self.assertEqual(hub_process.decode_windows_cli_output("ready".encode("utf-8")), "ready")
        self.assertEqual(hub_process.decode_windows_cli_output("成功".encode("gb18030")), "成功")

    def test_scheduled_task_state_reuses_interop_command_owner(self) -> None:
        completed = unittest.mock.Mock(returncode=0, stdout='{"exists": true}', stderr="")
        command = ["/init", "/mnt/c/windows/system32/powershell.exe"]
        with patch.object(local_mcp_hub, "windows_interop_command", return_value=command) as interop, patch.object(
            local_mcp_hub.subprocess, "run", return_value=completed
        ) as runner:
            result = local_mcp_hub.scheduled_task_state()
        self.assertTrue(result["ok"])
        interop.assert_called_once()
        self.assertEqual(runner.call_args.args[0], command)

    def test_reload_plan_names_wsl_callable_task_command(self) -> None:
        with patch.object(hub_process, "hub_service_status", return_value={"unit_exists": False}), patch.object(
            hub_process, "hub_runtime_state", return_value={"processes": []}
        ):
            result = hub_process.reload_local_hub(confirm_reload=False)
        self.assertEqual(result["start_route"], "schtasks.exe /Run /TN CodexLocalMcpHub")

    def test_runtime_state_requires_listener_and_health(self) -> None:
        process = [{"pid": 456, "parent_pid": 1, "command_line": "local_mcp_hub.py serve --port 18881"}]
        inactive_service = {"active": False, "systemd": {"ExecMainPID": 0}}
        with patch.object(hub_process, "hub_service_status", return_value=inactive_service), patch.object(
            hub_process, "local_hub_processes", return_value=[]
        ), patch.object(
            hub_process,
            "http_get_json",
            side_effect=OSError("connection refused"),
        ):
            missing = hub_process.hub_runtime_state()
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["reason"], "listener_process_missing")
        self.assertIn("connection refused", missing["health"]["reason"])
        with patch.object(hub_process, "hub_service_status", return_value=inactive_service), patch.object(
            hub_process, "local_hub_processes", return_value=process
        ), patch.object(
            hub_process, "http_get_json", return_value={"ok": True}
        ):
            healthy = hub_process.hub_runtime_state()
        self.assertTrue(healthy["ok"])
        self.assertEqual(healthy["processes"], process)

    def test_runtime_state_retries_transient_process_visibility_after_health_passes(self) -> None:
        process = [{"pid": 456, "parent_pid": 1, "command_line": "local_mcp_hub.py serve --port 18881"}]
        inactive_service = {"active": False, "systemd": {"ExecMainPID": 0}}
        with patch.object(hub_process, "hub_service_status", return_value=inactive_service), patch.object(
            hub_process, "local_hub_processes", side_effect=[[], process]
        ), patch.object(
            hub_process,
            "http_get_json",
            return_value={"ok": True},
        ), patch.object(hub_process.time, "sleep") as sleeper:
            recovered = hub_process.hub_runtime_state(process_retry_seconds=0.5)

        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["process_probe_attempts"], 2)
        self.assertEqual(recovered["processes"], process)
        sleeper.assert_called_once()

    def test_runtime_state_does_not_accept_health_without_process_identity(self) -> None:
        inactive_service = {"active": False, "systemd": {"ExecMainPID": 0}}
        with patch.object(hub_process, "hub_service_status", return_value=inactive_service), patch.object(
            hub_process, "local_hub_processes", return_value=[]
        ), patch.object(
            hub_process,
            "http_get_json",
            return_value={"ok": True},
        ):
            result = hub_process.hub_runtime_state(process_retry_seconds=0.01)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "listener_process_visibility_missing")
        self.assertTrue(result["health"]["ok"])


if __name__ == "__main__":
    unittest.main()
