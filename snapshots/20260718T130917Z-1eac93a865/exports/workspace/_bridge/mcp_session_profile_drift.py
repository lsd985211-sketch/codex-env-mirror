#!/usr/bin/env python3
"""Diagnostic classification for MCP registration and process drift.

Ownership: classify expected desktop registration and stale native-process
evidence from the execution-priority registry.
Non-goals: terminate processes, edit Codex configuration, repair registrations,
or expand any filesystem/tool permission boundary.
State behavior: pure and read-only; returns diagnostic issue records only.
Caller context: mcp_session_doctor profile snapshots and focused regression
tests.
"""

from __future__ import annotations

from mcp_execution_priority import resolve_execution_priority


def profile_registration_issues(
    *,
    profile_name: str,
    configured: bool,
    process_present: bool,
    retired: bool,
) -> list[dict[str, str]]:
    if retired:
        return []

    priority = resolve_execution_priority(profile_name)
    registration_mode = str(priority.get("registration_mode") or "unclassified")
    issues: list[dict[str, str]] = []
    if registration_mode == "hub_managed":
        if configured:
            issues.append(
                {
                    "severity": "risk",
                    "code": "hub_managed_desktop_registration_drift",
                    "message": "Hub-managed MCP is unexpectedly registered in Codex Desktop; preserve Hub-first execution and reconcile configuration through the owning governance path.",
                }
            )
        if process_present:
            issues.append(
                {
                    "severity": "risk",
                    "code": "hub_managed_native_process_drift",
                    "message": "A native process exists for a Hub-managed MCP with desktop_instance_budget=0, consistent with restored or stale Desktop state; do not automatically terminate it, and use bounded owner process diagnostics plus explicit authorization before cleanup.",
                }
            )
        return issues

    if not configured:
        issues.append(
            {
                "severity": "risk",
                "code": "mcp_config_missing",
                "message": "Desktop-native MCP server is missing from Codex config.",
            }
        )
    return issues
