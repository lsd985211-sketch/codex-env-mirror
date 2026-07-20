from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

try:
    import codex_config_guard
except ModuleNotFoundError:
    from _bridge import codex_config_guard


ROOT = Path(__file__).resolve().parents[1]
WSL_WINDOWS_PROFILE_SCRIPTS = Path("/mnt/c/Users/45543/.codex/scripts")
PROFILE_SCRIPTS = (
    WSL_WINDOWS_PROFILE_SCRIPTS
    if WSL_WINDOWS_PROFILE_SCRIPTS.is_dir()
    else Path.home() / ".codex" / "scripts"
)


class CodexStartupChainTests(unittest.TestCase):
    def launcher_sources(self) -> list[str]:
        paths = [PROFILE_SCRIPTS / "start-codex-desktop-elevated.ps1"]
        managed_source = ROOT.parent / "codex-home" / "scripts" / "start-codex-desktop-elevated.ps1"
        if managed_source.is_file() and managed_source not in paths:
            paths.append(managed_source)
        return [path.read_text(encoding="utf-8") for path in paths]

    def run_static_preflight(self, *, wsl_enabled: bool, projection_changed: bool) -> tuple[dict, mock.Mock, mock.Mock]:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config.toml"
            config.write_text(
                "[desktop]\nrunCodexInWindowsSubsystemForLinux = "
                + ("true" if wsl_enabled else "false")
                + "\n",
                encoding="utf-8",
            )
            projection = {
                "ok": True,
                "changed": projection_changed,
                "ready": True,
                "status": "applied" if projection_changed else "not_required",
            }
            resume_projection = {
                "ok": True,
                "changed": False,
                "ready": True,
                "status": "already_current" if wsl_enabled else "not_required",
            }
            with (
                mock.patch.object(codex_config_guard, "CODEX_CONFIG", config),
                mock.patch.object(
                    codex_config_guard.codex_state_repair,
                    "ensure_desktop_environment_selection",
                    return_value={
                        "ok": True,
                        "changed": False,
                        "ready": True,
                        "selected_value": wsl_enabled,
                        "status": "already_current",
                    },
                ),
                mock.patch.object(codex_config_guard.codex_config_projection, "apply_projection", return_value={"ok": True}),
                mock.patch.object(codex_config_guard, "delegated_session_store_maintenance", return_value={"ok": True}),
                mock.patch.object(codex_config_guard, "audit_checks", return_value=[]),
                mock.patch.object(codex_config_guard, "classify", return_value={"critical_ok": True}),
                mock.patch.object(codex_config_guard.codex_state_repair, "ensure_wsl_runtime_projection", return_value=projection) as ensure,
                mock.patch.object(codex_config_guard.codex_state_repair, "ensure_wsl_resume_context_projection", return_value=resume_projection) as ensure_resume,
                mock.patch.object(codex_config_guard, "append_log"),
            ):
                result = codex_config_guard.run_once(True, phase="pre-start-static")
        return result, ensure, ensure_resume

    def test_static_preflight_applies_wsl_projection_when_baseline_is_satisfied(self) -> None:
        result, ensure, ensure_resume = self.run_static_preflight(wsl_enabled=True, projection_changed=True)

        ensure.assert_called_once_with(enabled=True, dry_run=False)
        ensure_resume.assert_called_once_with(enabled=True, dry_run=False)
        self.assertTrue(result["ok"])
        self.assertTrue(result["applied"])
        self.assertTrue(result["runtime_applied"])
        self.assertTrue(result["needs_codex_restart"])
        self.assertTrue(result["wsl_runtime_ready"])
        self.assertEqual(result["wsl_runtime_projection"]["status"], "applied")
        self.assertEqual(result["wsl_resume_context_projection"]["status"], "already_current")

    def test_static_preflight_preserves_native_mode_when_wsl_is_disabled(self) -> None:
        result, ensure, ensure_resume = self.run_static_preflight(wsl_enabled=False, projection_changed=False)

        ensure.assert_called_once_with(enabled=False, dry_run=False)
        ensure_resume.assert_called_once_with(enabled=False, dry_run=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["applied"])
        self.assertFalse(result["needs_codex_restart"])
        self.assertTrue(result["wsl_runtime_ready"])

    def test_environment_change_forces_mode_specific_repair_with_healthy_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config.toml"
            config.write_text(
                "[desktop]\nrunCodexInWindowsSubsystemForLinux = true\n",
                encoding="utf-8",
            )
            repair_receipt = {
                "ok": True,
                "changed": ["global_mcp_add_node_repl"],
                "needs_codex_restart": True,
                "wsl_runtime_projection": {
                    "ok": True,
                    "enabled": False,
                    "changed": False,
                    "ready": True,
                    "status": "not_required",
                },
            }
            with (
                mock.patch.object(codex_config_guard, "CODEX_CONFIG", config),
                mock.patch.object(
                    codex_config_guard.codex_state_repair,
                    "ensure_desktop_environment_selection",
                    return_value={
                        "ok": True,
                        "changed": True,
                        "ready": True,
                        "selected_value": False,
                        "status": "applied",
                    },
                ),
                mock.patch.object(codex_config_guard.codex_state_repair, "repair", return_value=repair_receipt) as repair,
                mock.patch.object(codex_config_guard.codex_config_projection, "apply_projection", return_value={"ok": True}),
                mock.patch.object(codex_config_guard, "delegated_session_store_maintenance", return_value={"ok": True}),
                mock.patch.object(codex_config_guard, "audit_checks", return_value=[]),
                mock.patch.object(codex_config_guard, "classify", return_value={"critical_ok": True}),
                mock.patch.object(
                    codex_config_guard.codex_state_repair,
                    "ensure_wsl_resume_context_projection",
                    return_value={"ok": True, "changed": False, "ready": True, "status": "not_required"},
                ) as resume,
                mock.patch.object(codex_config_guard, "append_log"),
            ):
                result = codex_config_guard.run_once(True, phase="pre-start-static")

            repair.assert_called_once_with(dry_run=False, runtime_validation=False)
            resume.assert_called_once_with(enabled=False, dry_run=False)
            self.assertTrue(result["ok"])
            self.assertTrue(result["applied"])
            self.assertTrue(result["environment_selection_ready"])

    def test_wsl_projection_defers_writes_while_desktop_is_running(self) -> None:
        with (
            mock.patch.object(codex_config_guard.codex_state_repair, "codex_desktop_running", return_value=True),
            mock.patch.object(codex_config_guard.codex_state_repair.shutil, "which") as which,
        ):
            result = codex_config_guard.codex_state_repair.ensure_wsl_runtime_projection(
                enabled=True,
                dry_run=False,
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["ready"])
        self.assertTrue(result["deferred"])
        self.assertEqual(result["status"], "deferred_desktop_running")
        which.assert_not_called()

    def test_config_guard_delegates_session_writes(self) -> None:
        receipt = codex_config_guard.delegated_session_store_maintenance("pre-start-static")
        self.assertTrue(receipt["ok"])
        self.assertTrue(receipt["skipped"])
        self.assertEqual(receipt["reason"], "owned_by_codex_prelaunch_maintenance")

    def test_config_guard_task_has_no_logon_trigger(self) -> None:
        installer = (ROOT / "_bridge" / "install-codex-config-guard-task.ps1").read_text(encoding="utf-8")
        self.assertNotIn("New-ScheduledTaskTrigger -AtLogOn", installer)
        self.assertIn("-Trigger $repeatTrigger", installer)

    def test_provider_watcher_task_recovers_after_restart_exhaustion(self) -> None:
        installer = (ROOT / "_bridge" / "install-codex-model-provider-watcher-task.ps1").read_text(encoding="utf-8")
        self.assertIn("$triggers = @($logonTrigger, $recoveryTrigger)", installer)
        self.assertIn("-RepetitionInterval (New-TimeSpan -Minutes $RecoveryIntervalMinutes)", installer)
        self.assertIn("-MultipleInstances IgnoreNew", installer)
        self.assertIn('supervise --poll-seconds 2', installer)

    def test_profile_bootstrap_supports_async_and_wait_modes(self) -> None:
        asynchronous = (PROFILE_SCRIPTS / "run-hidden.vbs").read_text(encoding="utf-8")
        waiting = (PROFILE_SCRIPTS / "run-hidden-wait.vbs").read_text(encoding="utf-8")
        self.assertIn("shell.Run command, 0, False", asynchronous)
        self.assertIn("shell.Run(command, 0, True)", waiting)
        self.assertIn("WScript.Quit exitCode", waiting)

    def test_shortcuts_use_profile_bootstrap(self) -> None:
        repair = (PROFILE_SCRIPTS / "repair-codex-admin-shortcuts.ps1").read_text(encoding="utf-8")
        self.assertIn('.codex\\scripts\\run-hidden.vbs', repair)
        self.assertIn('.codex\\scripts\\run-hidden-wait.vbs', repair)
        self.assertNotIn('_bridge\\shared\\run-hidden.vbs', repair)

    def test_launcher_has_free_port_fast_path_and_async_repair(self) -> None:
        launcher = (PROFILE_SCRIPTS / "start-codex-desktop-elevated.ps1").read_text(encoding="utf-8")
        self.assertIn('if ($beforePort -ne "free")', launcher)
        self.assertIn("Start-ShortcutSelfRepairAsync", launcher)
        self.assertIn("CODEX_STARTUP_PREFLIGHT_LOG_KEEP", launcher)
        self.assertIn('CODEX_STARTUP_PREFLIGHT_TIMEOUT_SECONDS" -Default 75', launcher)
        self.assertIn("WslRuntimeReady", launcher)
        self.assertIn("WslResumeContextStatus", launcher)
        self.assertIn("EnvironmentSelectionReady", launcher)
        self.assertIn("$environmentSelectionReady -and $wslRuntimeReady", launcher)
        self.assertIn("WSL runtime projection is not ready", launcher)
        self.assertIn("native compatibility launch to preserve Codex availability", launcher)
        self.assertIn('Get-ObjectPropertyValue -Object $result -Name "before" -Default $null', launcher)
        self.assertIn('Get-ObjectPropertyValue -Object $result -Name "after" -Default $null', launcher)
        self.assertNotIn("$result.before", launcher)
        self.assertNotIn("$result.after", launcher)

    def test_wsl_projection_failure_never_enables_strict_preflight_blocking(self) -> None:
        for launcher in self.launcher_sources():
            self.assertIn("function Test-CodexConfigPreflightShouldBlock", launcher)
            self.assertIn("WslEnabled = $wslEnabled", launcher)
            self.assertIn("WslRuntimeReady = $wslRuntimeReady", launcher)
            self.assertIn("$wslProjectionUnavailable = $wslEnabled -and -not $wslRuntimeReady", launcher)
            self.assertIn("return $strictMode -and -not $wslProjectionUnavailable", launcher)
            self.assertEqual(launcher.count("Test-CodexConfigPreflightShouldBlock -Result"), 2)

    def test_launcher_pins_native_codex_home_before_desktop_start(self) -> None:
        launcher = (PROFILE_SCRIPTS / "start-codex-desktop-elevated.ps1").read_text(encoding="utf-8")
        pin = '$env:CODEX_HOME = Join-Path $env:USERPROFILE ".codex"'
        start = "Start-Process -FilePath $codexExe"
        self.assertIn(pin, launcher)
        self.assertIn(start, launcher)
        self.assertLess(launcher.index(pin), launcher.index(start))

    def test_managed_launcher_validates_desktop_protocol_compatibility(self) -> None:
        launcher = (ROOT.parent / "codex-home" / "scripts" / "start-codex-desktop-elevated.ps1").read_text(encoding="utf-8")
        self.assertIn("function Invoke-CodexDesktopProtocolCompatibilityPreflight", launcher)
        self.assertIn("codex_desktop_protocol_compatibility.py", launcher)
        self.assertIn("CODEX_PROTOCOL_PREFLIGHT_TIMEOUT_SECONDS", launcher)
        self.assertIn("RedirectStandardOutput $protocolOut", launcher)
        self.assertIn("$null = $process.Handle", launcher)
        self.assertIn("$process.WaitForExit", launcher)
        self.assertIn("NativeNoticeSuppressionDeclared", launcher)
        self.assertIn('CODEX_STARTUP_PROTOCOL_FAIL_CLOSED -eq "1"', launcher)
        self.assertIn('exit 7', launcher)
        self.assertLess(
            launcher.index("Invoke-CodexDesktopProtocolCompatibilityPreflight"),
            launcher.index("Start-Process -FilePath $codexExe"),
        )

    def test_controlled_desktop_refresh_preserves_the_running_process(self) -> None:
        refresh = (ROOT.parent / "codex-home" / "scripts" / "restart-codex-desktop-cdp.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("codex_desktop_model_runtime.py", refresh)
        self.assertIn('"page-reload"', refresh)
        self.assertIn("Process-preserving Codex Desktop refresh completed", refresh)
        self.assertNotIn("Stop-Process", refresh)
        self.assertNotIn("CloseMainWindow", refresh)
        self.assertNotIn("start-codex-desktop-elevated.ps1", refresh)

    def test_launcher_fails_closed_on_unreliable_safety_state(self) -> None:
        launcher = (PROFILE_SCRIPTS / "start-codex-desktop-elevated.ps1").read_text(encoding="utf-8")
        helper = (PROFILE_SCRIPTS / "codex-desktop-launch-safety.ps1").read_text(encoding="utf-8")
        self.assertIn(". $launchSafetyPath", launcher)
        self.assertIn('exit 8', launcher)
        self.assertIn("Test-CodexProcessScanReliable", launcher)
        self.assertIn('CODEX_ALLOW_STALE_CODEX_CLEANUP -eq "1"', launcher)
        self.assertIn('"supervise"', launcher)
        self.assertIn("exclusive file-lock fallback", helper)
        self.assertIn("refusing an unprotected launch", helper)
        self.assertNotIn("continuing without singleton protection", helper)


if __name__ == "__main__":
    unittest.main()
