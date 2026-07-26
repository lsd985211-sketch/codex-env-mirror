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

    def run_static_preflight(
        self,
        *,
        wsl_enabled: bool,
        projection_changed: bool,
        selection_result: dict | None = None,
    ) -> tuple[dict, mock.Mock, mock.Mock, mock.Mock]:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config.toml"
            config.write_text(
                "[desktop]\nrunCodexInWindowsSubsystemForLinux = "
                + ("true" if wsl_enabled else "false")
                + "\n",
                encoding="utf-8",
            )
            selected = selection_result or {
                "ok": True,
                "changed": False,
                "ready": True,
                "selected_value": wsl_enabled,
                "effective_value": wsl_enabled,
                "status": "already_current",
            }
            effective_enabled = bool(selected.get("effective_value", selected.get("selected_value", wsl_enabled)))
            projection = {
                "ok": True,
                "changed": projection_changed,
                "ready": True,
                "enabled": effective_enabled,
                "status": "applied" if projection_changed else "not_required",
            }
            resume_projection = {
                "ok": True,
                "changed": False,
                "ready": True,
                "status": "already_current" if effective_enabled else "not_required",
            }
            with (
                mock.patch.object(codex_config_guard, "CODEX_CONFIG", config),
                mock.patch.object(
                    codex_config_guard.codex_state_repair,
                    "ensure_desktop_environment_selection",
                    return_value=selected,
                ),
                mock.patch.object(codex_config_guard.codex_config_projection, "apply_projection", return_value={"ok": True}),
                mock.patch.object(codex_config_guard, "delegated_session_store_maintenance", return_value={"ok": True}),
                mock.patch.object(codex_config_guard, "audit_checks", return_value=[]),
                mock.patch.object(codex_config_guard, "classify", return_value={"critical_ok": True}),
                mock.patch.object(codex_config_guard.codex_state_repair, "ensure_wsl_runtime_projection", return_value=projection) as ensure,
                mock.patch.object(codex_config_guard.codex_state_repair, "ensure_wsl_resume_context_projection", return_value=resume_projection) as ensure_resume,
                mock.patch.object(
                    codex_config_guard.codex_state_repair,
                    "ensure_windows_resume_cwd_projection",
                return_value={"ok": True, "changed": False, "ready": True, "status": "not_required"},
                ) as ensure_windows_resume,
                mock.patch.object(codex_config_guard, "append_log"),
            ):
                result = codex_config_guard.run_once(True, phase="pre-start-static")
        return result, ensure, ensure_resume, ensure_windows_resume

    def test_static_preflight_applies_wsl_projection_when_baseline_is_satisfied(self) -> None:
        result, ensure, ensure_resume, ensure_windows_resume = self.run_static_preflight(wsl_enabled=True, projection_changed=True)

        ensure.assert_called_once_with(enabled=True, dry_run=False)
        ensure_resume.assert_called_once_with(enabled=True, dry_run=False)
        ensure_windows_resume.assert_called_once_with(enabled=False, dry_run=False)
        self.assertTrue(result["ok"])
        self.assertTrue(result["applied"])
        self.assertTrue(result["runtime_applied"])
        self.assertTrue(result["needs_codex_restart"])
        self.assertTrue(result["wsl_runtime_ready"])
        self.assertEqual(result["wsl_runtime_projection"]["status"], "applied")
        self.assertEqual(result["wsl_resume_context_projection"]["status"], "already_current")

    def test_static_preflight_preserves_native_mode_when_wsl_is_disabled(self) -> None:
        result, ensure, ensure_resume, ensure_windows_resume = self.run_static_preflight(wsl_enabled=False, projection_changed=False)

        ensure.assert_called_once_with(enabled=False, dry_run=False)
        ensure_resume.assert_called_once_with(enabled=False, dry_run=False)
        ensure_windows_resume.assert_called_once_with(enabled=True, dry_run=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["applied"])
        self.assertFalse(result["needs_codex_restart"])
        self.assertTrue(result["wsl_runtime_ready"])

    def test_wsl_failure_uses_windows_effective_mode_without_erasing_desired_mode(self) -> None:
        result, ensure, ensure_resume, ensure_windows_resume = self.run_static_preflight(
            wsl_enabled=True,
            projection_changed=False,
            selection_result={
                "ok": False,
                "changed": False,
                "ready": False,
                "selected_value": True,
                "desired_value": True,
                "effective_value": False,
                "fallback_preserved": True,
                "fallback_pending": True,
                "status": "owner_failed",
            },
        )

        ensure.assert_called_once_with(enabled=False, dry_run=False)
        ensure_resume.assert_called_once_with(enabled=False, dry_run=False)
        ensure_windows_resume.assert_called_once_with(enabled=True, dry_run=False)
        self.assertFalse(result["environment_selection_ready"])
        self.assertTrue(result["desktop_environment_selection"]["desired_value"])
        self.assertFalse(result["wsl_runtime_projection"]["enabled"])

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
                mock.patch.object(
                    codex_config_guard.codex_state_repair,
                    "ensure_windows_resume_cwd_projection",
                    return_value={"ok": True, "changed": False, "ready": True, "status": "already_current"},
                ) as windows_resume,
                mock.patch.object(codex_config_guard, "append_log"),
            ):
                result = codex_config_guard.run_once(True, phase="pre-start-static")

            repair.assert_called_once_with(dry_run=False, runtime_validation=False)
            resume.assert_called_once_with(enabled=False, dry_run=False)
            windows_resume.assert_called_once_with(enabled=True, dry_run=False)
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

    def test_launcher_consumes_a_typed_full_restart_request_without_force_kill(self) -> None:
        launcher = (ROOT.parent / "codex-home" / "scripts" / "start-codex-desktop-elevated.ps1").read_text(encoding="utf-8")
        self.assertIn("desktop_restart_request.v1", launcher)
        self.assertIn("codex-desktop-restart-request.json", launcher)
        self.assertIn("codex-desktop-restart-receipt.json", launcher)
        self.assertIn("CloseMainWindow()", launcher)
        self.assertIn("desktop_restart_request.py", launcher)
        self.assertIn('"signal-exit"', launcher)
        self.assertIn("electron_quit_ipc", launcher)
        self.assertIn("force_kill_used = $false", launcher)
        self.assertIn("graceful_exit_method", launcher)
        self.assertIn("input_signature", launcher)

    def test_launcher_dispatches_version_fingerprint_hidden_and_without_waiting(self) -> None:
        launcher = (ROOT.parent / "codex-home" / "scripts" / "start-codex-desktop-elevated.ps1").read_text(encoding="utf-8-sig")
        start = launcher.index("function Start-CodexVersionFingerprintAsync")
        end = launcher.index("\nStart-CodexVersionFingerprintAsync", start)
        function_body = launcher[start:end]
        self.assertIn("codex-version-fingerprint.ps1", function_body)
        self.assertIn("Start-Process", function_body)
        self.assertIn("-WindowStyle Hidden", function_body)
        self.assertNotIn("-Wait", function_body)
        self.assertNotIn("wsl.exe", function_body)
        self.assertNotIn("Invoke-WebRequest", function_body)

    def test_version_fingerprint_helper_is_local_bounded_and_fail_open(self) -> None:
        helper_path = ROOT.parent / "codex-home" / "scripts" / "codex-version-fingerprint.ps1"
        raw = helper_path.read_bytes()
        helper = raw.decode("utf-8")
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn("Local\\OpenAI-Codex-Version-Fingerprint", helper)
        self.assertIn("TotalTimeoutSeconds", helper)
        self.assertIn("CommandTimeoutSeconds", helper)
        self.assertIn("Write-JsonAtomic", helper)
        self.assertIn("validated_baseline_advanced = $false", helper)
        self.assertIn("network_used = $false", helper)
        self.assertTrue(helper.rstrip().endswith("exit 0"))
        for forbidden in ("Invoke-WebRequest", "Invoke-RestMethod", "curl.exe", "https://", "http://"):
            self.assertNotIn(forbidden, helper)

    def test_wsl_projection_failure_never_enables_strict_preflight_blocking(self) -> None:
        for launcher in self.launcher_sources():
            self.assertIn("function Test-CodexConfigPreflightShouldBlock", launcher)
            self.assertIn("WslEnabled = $wslEnabled", launcher)
            self.assertIn("WslRuntimeReady = $wslRuntimeReady", launcher)
            self.assertIn("$wslProjectionUnavailable = $wslEnabled -and -not $wslRuntimeReady", launcher)
            self.assertIn("return $strictMode -and -not $wslProjectionUnavailable", launcher)
            self.assertEqual(launcher.count("Test-CodexConfigPreflightShouldBlock -Result"), 2)

    def test_launcher_keeps_windows_codex_home_for_native_local_host(self) -> None:
        launcher = (ROOT.parent / "codex-home" / "scripts" / "start-codex-desktop-elevated.ps1").read_text(encoding="utf-8")
        pin = '$env:CODEX_HOME = Join-Path $env:USERPROFILE ".codex"'
        start = "Start-Process -FilePath $codexExe"
        self.assertIn(pin, launcher)
        self.assertIn(start, launcher)
        self.assertLess(launcher.index(pin), launcher.index(start))
        self.assertNotIn("Set-CodexDesktopChildEnvironment", launcher)
        self.assertNotIn("Remove-Item Env:CODEX_HOME", launcher)

    def test_launcher_retires_legacy_model_runtime_before_compatibility_fallback(self) -> None:
        launcher = (ROOT.parent / "codex-home" / "scripts" / "start-codex-desktop-elevated.ps1").read_text(encoding="utf-8")
        reconcile_start = launcher.index("function Invoke-CodexDesktopModelRuntimeReconcile")
        reconcile_end = launcher.index("function Start-CodexModelProviderWatcherAsync", reconcile_start)
        reconcile = launcher[reconcile_start:reconcile_end]
        native_probe = "$nativeRuntime = Invoke-CodexDesktopNativeRuntimeRetirement"
        compatibility_fallback = "$modelListBridgeShim = Invoke-CodexDesktopModelListBridgeShim"
        self.assertIn("function Invoke-CodexDesktopNativeRuntimeRetirement", launcher)
        self.assertIn('"legacy-runtime-shims-retire"', launcher)
        self.assertIn(native_probe, reconcile)
        self.assertIn("if ([bool]$nativeRuntime.NativeAuthoritative)", reconcile)
        self.assertIn("return", reconcile)
        self.assertLess(reconcile.index(native_probe), reconcile.index(compatibility_fallback))

    def test_launcher_rebuilds_effective_path_before_desktop_start(self) -> None:
        launcher = (ROOT.parent / "codex-home" / "scripts" / "start-codex-desktop-elevated.ps1").read_text(encoding="utf-8")
        initialize = "$desktopLaunchEnvironment = Initialize-CodexDesktopLaunchEnvironment"
        start = "Start-Process -FilePath $codexExe"
        self.assertIn('[Environment]::GetEnvironmentVariable("Path", "Machine")', launcher)
        self.assertIn('[Environment]::GetEnvironmentVariable("Path", "User")', launcher)
        self.assertIn('$preferredGitDirectory = Join-Path $env:ProgramFiles "Git\\cmd"', launcher)
        self.assertIn("$entries.Add($preferredGitDirectory)", launcher)
        self.assertIn("$entry -match '^[A-Za-z]$'", launcher)
        self.assertIn("$entry -match '^\\\\(?!\\\\)'", launcher)
        self.assertIn('$env:Path = [string]::Join(";", $entries)', launcher)
        self.assertIn("codex-desktop-launch-environment.json", launcher)
        self.assertIn("path_input_signature", launcher)
        self.assertIn("rejected_ambiguous_entry_count", launcher)
        self.assertIn("[System.IO.File]::WriteAllText($temporaryPath, $json, $utf8NoBom)", launcher)
        self.assertNotIn("Set-Content -LiteralPath $temporaryPath -Encoding UTF8", launcher)
        self.assertNotIn("full_path =", launcher)
        self.assertLess(launcher.index(initialize), launcher.index(start))

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

    def test_manual_launcher_authorization_can_replace_non_elevated_cdp_owner(self) -> None:
        repair = (PROFILE_SCRIPTS / "repair-codex-admin-shortcuts.ps1").read_text(encoding="utf-8")
        for launcher in self.launcher_sources():
            self.assertIn("function Stop-CodexDesktopProcessFamilyForRelaunch", launcher)
            self.assertIn("$allowAuthorizedTakeover", launcher)
            self.assertIn('CODEX_ALLOW_STALE_CODEX_CLEANUP -eq "1"', launcher)
            self.assertIn('CODEX_DISABLE_STALE_CODEX_CLEANUP -ne "1"', launcher)
            self.assertIn('Reason "authorized_non_elevated_cdp_takeover"', launcher)
            self.assertIn('SuccessAction "non_elevated_processes_stopped"', launcher)
            self.assertIn("Authorized non-elevated Codex takeover completed", launcher)
            self.assertIn("Close the current Codex window, then start Codex Current Admin again", launcher)
        self.assertIn("CODEX_ALLOW_STALE_CODEX_CLEANUP = '1'", repair)
        self.assertIn('Ensure-CodexDesktopScheduledTaskHiddenWrapper -StartScript $startScript', repair)

    def test_failed_final_config_preflight_does_not_abort_under_strict_mode(self) -> None:
        for launcher in self.launcher_sources():
            self.assertIn(
                'Get-ObjectPropertyValue -Object $finalConfigPreflight -Name "WslEnabled" -Default $false',
                launcher,
            )
            self.assertIn(
                'Get-ObjectPropertyValue -Object $finalConfigPreflight -Name "WslRuntimeReady"',
                launcher,
            )
            self.assertIn(
                'Get-ObjectPropertyValue -Object $finalConfigPreflight -Name "WslProjectionStatus"',
                launcher,
            )
            self.assertNotIn("$finalConfigPreflight.WslEnabled", launcher)
            self.assertNotIn("$finalConfigPreflight.WslRuntimeReady", launcher)


if __name__ == "__main__":
    unittest.main()
