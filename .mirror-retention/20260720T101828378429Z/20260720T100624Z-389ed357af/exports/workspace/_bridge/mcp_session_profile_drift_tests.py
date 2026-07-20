from __future__ import annotations

import json
import unittest

from mcp_session_profile_drift import profile_registration_issues


class McpSessionProfileDriftTests(unittest.TestCase):
    def test_hub_managed_absent_without_process_is_expected(self) -> None:
        issues = profile_registration_issues(
            profile_name="filesystem-admin",
            configured=False,
            process_present=False,
            retired=False,
        )
        self.assertEqual(issues, [])

    def test_hub_managed_process_is_reported_as_stale_drift(self) -> None:
        issues = profile_registration_issues(
            profile_name="filesystem-admin",
            configured=False,
            process_present=True,
            retired=False,
        )
        self.assertEqual([item["code"] for item in issues], ["hub_managed_native_process_drift"])
        self.assertIn("do not automatically terminate", issues[0]["message"])

    def test_hub_managed_desktop_registration_is_reported(self) -> None:
        issues = profile_registration_issues(
            profile_name="filesystem",
            configured=True,
            process_present=False,
            retired=False,
            platform_scope="windows",
        )
        self.assertEqual([item["code"] for item in issues], ["hub_managed_desktop_registration_drift"])

    def test_desktop_native_missing_still_reports_config_missing(self) -> None:
        issues = profile_registration_issues(
            profile_name="node_repl",
            configured=False,
            process_present=False,
            retired=False,
            platform_scope="windows",
        )
        self.assertEqual([item["code"] for item in issues], ["mcp_config_missing"])

    def test_wsl_desktop_native_missing_is_deferred(self) -> None:
        issues = profile_registration_issues(
            profile_name="mobile-openclaw-bridge",
            configured=False,
            process_present=False,
            retired=False,
            platform_scope="wsl",
        )
        self.assertEqual([item["code"] for item in issues], ["platform_deferred"])

    def test_wsl_hub_registration_is_allowed_for_forward_fallback(self) -> None:
        issues = profile_registration_issues(
            profile_name="filesystem",
            configured=True,
            process_present=False,
            retired=False,
            platform_scope="wsl",
        )
        self.assertEqual(issues, [])

    def test_diagnostics_never_emit_repair_or_kill_actions(self) -> None:
        issues = profile_registration_issues(
            profile_name="filesystem-admin",
            configured=True,
            process_present=True,
            retired=False,
        )
        serialized = json.dumps(issues, ensure_ascii=False).lower()
        self.assertNotIn('"action"', serialized)
        self.assertNotIn("taskkill", serialized)
        self.assertNotIn("stop-process", serialized)


if __name__ == "__main__":
    unittest.main()
