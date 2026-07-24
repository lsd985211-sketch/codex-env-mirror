#!/usr/bin/env python3
"""Focused regressions for the WSL maintenance-scheduler service owner."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import maintenance_scheduler_service as owner
from shared import codex_scheduler_runner as runner


class MaintenanceSchedulerServiceTests(unittest.TestCase):
    def test_unit_reuses_existing_runner_as_one_wsl_loop(self) -> None:
        with patch.object(owner, "PRIMARY_ROOT", Path("/home/codexlab/work space")):
            content = owner.unit_content(python=Path("/usr/bin/python3"), interval_seconds=300)
        self.assertIn("codex_scheduler_runner.py", content)
        self.assertIn("loop --interval-seconds 300", content)
        self.assertIn(r"WorkingDirectory=/home/codexlab/work\x20space/workspace", content)
        self.assertNotIn('WorkingDirectory="', content)
        self.assertIn("CODEX_SCHEDULER_SERVICE_MODE=wsl-user-systemd", content)
        self.assertIn("Restart=on-failure", content)
        self.assertNotIn("powershell", content.casefold())

    def test_windows_wake_rejects_legacy_resident_loop(self) -> None:
        with patch.object(
            owner,
            "_windows_task_row",
            return_value={
                "execute": r"C:\Windows\System32\wscript.exe",
                "arguments": "run-codex-scheduler.ps1 -IntervalSeconds 60",
                "run_level": "Highest",
            },
        ):
            payload = owner.windows_wake_status()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["resident_windows_loop"])

    def test_windows_wake_accepts_limited_one_shot_wsl_action(self) -> None:
        with patch.object(
            owner,
            "_windows_task_row",
            return_value={
                "execute": r"C:\Windows\System32\wsl.exe",
                "arguments": "-d Codex-Wsl-Lab -- systemctl --user start codex-maintenance-scheduler.service",
                "run_level": "Limited",
            },
        ):
            payload = owner.windows_wake_status()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["resident_windows_loop"])
        self.assertEqual("login_wake_only", payload["role"])

    def test_windows_conversion_is_blocked_until_service_is_ready(self) -> None:
        with patch.object(owner, "windows_wake_plan", return_value={"ok": False, "service_ready": False}), patch.object(
            owner.subprocess, "run"
        ) as runner:
            payload = owner.install_windows_wake(owner.WINDOWS_WAKE_CONFIRM)
        self.assertFalse(payload["ok"])
        self.assertEqual("wsl_service_or_projection_not_ready", payload["reason"])
        runner.assert_not_called()

    def test_windows_conversion_accepts_active_systemd_handoff_waiter(self) -> None:
        current = {
            "ok": False,
            "unit_exists": True,
            "enabled": True,
            "active": True,
            "systemd": {"LoadState": "loaded"},
            "identity": {"matches": False},
            "windows_wake": {"resident_windows_loop": True},
        }
        planned = {"unit_sha256": "same", "installed_unit_sha256": "same", "blockers": []}
        with patch.object(owner, "status", return_value=current), patch.object(owner, "plan", return_value=planned), patch.object(
            owner, "WINDOWS_WAKE_INSTALLER", Path(__file__)
        ):
            payload = owner.windows_wake_plan()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["handoff_waiting"])

    def test_windows_conversion_requires_post_handoff_service_identity(self) -> None:
        completed = owner.subprocess.CompletedProcess([], 0, "converted", "")
        with patch.object(owner, "windows_wake_plan", return_value={"ok": True}), patch.object(
            owner, "_backup_windows_task", return_value=({"ok": True, "manifest_paths": ["backup.json"]}, "<Task />")
        ), patch.object(owner.windows_execution_agent, "powershell_path", return_value=Path("powershell.exe")), patch.object(
            owner, "host_accessible_path", return_value=Path("installer.ps1")
        ), patch.object(owner.subprocess, "run", return_value=completed), patch.object(
            owner, "windows_wake_status", return_value={"ok": True}
        ), patch.object(owner, "wait_ready", return_value={"ok": True, "status": {"identity": {"matches": True}}}), patch.object(
            owner, "_restore_windows_task"
        ) as restore:
            payload = owner.install_windows_wake(owner.WINDOWS_WAKE_CONFIRM)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["rollback"]["applied"])
        restore.assert_not_called()

    def test_windows_conversion_rolls_back_when_service_never_takes_lock(self) -> None:
        completed = owner.subprocess.CompletedProcess([], 0, "converted", "")
        with patch.object(owner, "windows_wake_plan", return_value={"ok": True}), patch.object(
            owner, "_backup_windows_task", return_value=({"ok": True}, "<Task />")
        ), patch.object(owner.windows_execution_agent, "powershell_path", return_value=Path("powershell.exe")), patch.object(
            owner, "host_accessible_path", return_value=Path("installer.ps1")
        ), patch.object(owner.subprocess, "run", return_value=completed), patch.object(
            owner, "windows_wake_status", return_value={"ok": True}
        ), patch.object(owner, "wait_ready", return_value={"ok": False}), patch.object(
            owner, "_restore_windows_task", return_value={"ok": True}
        ) as restore:
            payload = owner.install_windows_wake(owner.WINDOWS_WAKE_CONFIRM)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["rollback"]["applied"])
        restore.assert_called_once_with("<Task />")

    def test_systemd_loop_waits_for_lock_then_becomes_authority(self) -> None:
        class FakeLock:
            acquire_count = 0
            released = False

            def __init__(self, _path: Path) -> None:
                pass

            def acquire(self) -> bool:
                self.acquire_count += 1
                return self.acquire_count > 1

            def release(self) -> None:
                self.released = True

        with patch.dict(owner.os.environ, {"CODEX_SCHEDULER_SERVICE_MODE": "wsl-user-systemd"}), patch.object(
            runner, "SingleInstanceLock", FakeLock
        ), patch.object(runner.time, "sleep"), patch.object(runner, "append_log"), patch.object(
            runner, "write_heartbeat"
        ), patch.object(runner, "run_due", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                runner.loop(300, dry_run=False)

    def test_lock_contention_tolerates_drvfs_flush_and_close_errors(self) -> None:
        handle = MagicMock()
        handle.fileno.return_value = 7
        handle.flush.side_effect = OSError(5, "Input/output error")
        handle.close.side_effect = OSError(5, "Input/output error")
        lock = runner.SingleInstanceLock(Path("scheduler.lock"))
        with patch("builtins.open", return_value=handle), patch("fcntl.flock"):
            self.assertFalse(lock.acquire())
        self.assertIsNone(lock.handle)

    def test_windows_wake_installer_cannot_reintroduce_resident_loop(self) -> None:
        installer = (Path(__file__).resolve().parent / "shared" / "install-codex-scheduler-task.ps1").read_text(encoding="utf-8")
        retired = Path(__file__).resolve().parent / "shared" / "run-codex-scheduler.ps1"
        self.assertFalse(retired.exists())
        self.assertIn("wsl.exe", installer)
        self.assertIn("codex-maintenance-scheduler.service", installer)
        self.assertIn("RunLevel Limited", installer)
        action_line = next(line for line in installer.splitlines() if line.startswith("$argument ="))
        self.assertNotIn("run-codex-scheduler.ps1", action_line)
        self.assertIn("Get-CimInstance Win32_Process", installer)
        self.assertIn("run-codex-scheduler.ps1", installer)
        self.assertIn("codex_scheduler_runner.py loop", installer)
        self.assertIn("pythonw.exe", installer)

    def test_validate_requires_one_pid_and_converted_windows_wake(self) -> None:
        planned = {"blockers": [], "unit_sha256": "same", "installed_unit_sha256": "same"}
        current = {
            "unit_exists": True,
            "enabled": True,
            "active": True,
            "identity": {"matches": True, "root_or_system": False},
            "windows_wake": {"ok": True},
        }
        with patch.object(owner, "plan", return_value=planned), patch.object(owner, "status", return_value=current):
            payload = owner.validate()
        self.assertTrue(payload["ok"], payload["issues"])


if __name__ == "__main__":
    unittest.main()
