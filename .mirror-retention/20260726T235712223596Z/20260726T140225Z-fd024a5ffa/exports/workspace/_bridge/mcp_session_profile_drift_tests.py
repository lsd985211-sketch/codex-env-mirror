from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import mcp_session_doctor
from mcp_session_profile_drift import current_turn_callability_disposition, profile_registration_issues


class McpSessionProfileDriftTests(unittest.TestCase):
    def test_thread_anchor_selects_database_with_freshest_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = root / "state_5.sqlite"
            current = root / "sqlite" / "state_5.sqlite"
            current.parent.mkdir()
            for path, thread_id, updated_at_ms in (
                (stale, "stale-thread", 1000),
                (current, "current-thread", 2000),
            ):
                con = sqlite3.connect(path)
                try:
                    con.execute(
                        "create table threads (id text, created_at_ms integer, updated_at_ms integer, "
                        "recency_at_ms integer, source text, model_provider text, cli_version text, cwd text)"
                    )
                    con.execute(
                        "insert into threads values (?, ?, ?, ?, ?, ?, ?, ?)",
                        (thread_id, updated_at_ms - 10, updated_at_ms, updated_at_ms - 5, "app", "openai", "1", "/repo"),
                    )
                    con.commit()
                finally:
                    con.close()

            anchor = mcp_session_doctor.thread_freshness_anchor(
                state_db_candidates=[stale, current]
            )

        self.assertEqual(anchor["state"], "ok")
        self.assertEqual(anchor["path"], str(current))
        self.assertEqual(anchor["selected_thread_id"], "current-thread")

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

    def test_session_bound_negative_is_pending_advisory_not_issue(self) -> None:
        disposition = current_turn_callability_disposition(
            profile_name="node_repl",
            transport_topology="local_session_bound_stdio_kernel",
            current_turn_state="transport_closed",
            fallback_available=False,
            gateway_available=False,
        )
        self.assertEqual(disposition["severity"], "advisory")
        self.assertEqual(disposition["code"], "session_bound_acceptance_pending")
        self.assertEqual(disposition["acceptance"], "new_desktop_task_real_tool_call")

        issues: list[str] = []
        advisories: list[str] = []
        pending: list[dict[str, str]] = []
        mcp_session_doctor._validate_snapshot_profiles(
            {
                "profiles": [
                    {
                        "name": "node_repl",
                        "transport_topology": "local_session_bound_stdio_kernel",
                        "current_turn_callable": {"callable": False, "state": "transport_closed"},
                    }
                ]
            },
            {},
            issues,
            advisories,
            [],
            pending,
        )
        self.assertEqual(issues, [])
        self.assertEqual([item["profile"] for item in pending], ["node_repl"])
        self.assertIn("new Desktop task", advisories[0])

    def test_stateless_negative_remains_issue(self) -> None:
        disposition = current_turn_callability_disposition(
            profile_name="filesystem",
            transport_topology="local_stateless_stdio",
            current_turn_state="transport_closed",
            fallback_available=False,
            gateway_available=False,
        )
        self.assertEqual(disposition["severity"], "risk")
        self.assertEqual(disposition["code"], "current_turn_unavailable")

        issues: list[str] = []
        advisories: list[str] = []
        pending: list[dict[str, str]] = []
        mcp_session_doctor._validate_snapshot_profiles(
            {
                "profiles": [
                    {
                        "name": "filesystem",
                        "transport_topology": "local_stateless_stdio",
                        "current_turn_callable": {"callable": False, "state": "transport_closed"},
                    }
                ]
            },
            {},
            issues,
            advisories,
            [],
            pending,
        )
        self.assertEqual(advisories, [])
        self.assertEqual(pending, [])
        self.assertEqual(issues, ["current turn cannot use filesystem: transport_closed"])


if __name__ == "__main__":
    unittest.main()
