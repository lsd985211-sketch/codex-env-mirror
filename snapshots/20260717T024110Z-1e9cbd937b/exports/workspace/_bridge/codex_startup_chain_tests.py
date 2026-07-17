from __future__ import annotations

from pathlib import Path
import sys
import unittest

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

try:
    import codex_config_guard
except ModuleNotFoundError:
    from _bridge import codex_config_guard


ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCRIPTS = Path.home() / ".codex" / "scripts"


class CodexStartupChainTests(unittest.TestCase):
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
