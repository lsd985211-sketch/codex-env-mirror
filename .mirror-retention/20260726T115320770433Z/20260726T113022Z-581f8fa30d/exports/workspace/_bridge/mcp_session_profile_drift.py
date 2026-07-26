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

from mcp_execution_priority import resolve_execution_priority, runtime_platform


SESSION_BOUND_TOPOLOGIES = frozenset({"local_session_bound_stdio_kernel"})


def current_turn_callability_disposition(
    *,
    profile_name: str,
    transport_topology: str,
    current_turn_state: str,
    fallback_available: bool,
    gateway_available: bool,
) -> dict[str, str]:
    """Classify current-turn negative evidence without changing runtime state."""
    name = str(profile_name or "unknown")
    state = str(current_turn_state or "session_surface_missing_or_stale")
    topology = str(transport_topology or "external_stdio")
    if fallback_available:
        return {
            "severity": "advisory",
            "code": "bounded_fallback_available",
            "message": f"native current turn cannot use {name} but bounded fallback/fresh stdio call is available",
        }
    if gateway_available:
        return {
            "severity": "advisory",
            "code": "gateway_call_available",
            "message": f"native current turn cannot use {name} but tool gateway fresh stdio call is available",
        }
    if topology in SESSION_BOUND_TOPOLOGIES:
        return {
            "severity": "advisory",
            "code": "session_bound_acceptance_pending",
            "message": (
                f"current task cannot reuse session-bound {name} handle: {state}; "
                "acceptance remains pending until a new Desktop task completes a real tool call"
            ),
            "acceptance": "new_desktop_task_real_tool_call",
        }
    return {
        "severity": "risk",
        "code": "current_turn_unavailable",
        "message": f"current turn cannot use {name}: {state}",
    }


def profile_registration_issues(
    *,
    profile_name: str,
    configured: bool,
    process_present: bool,
    retired: bool,
    platform_scope: str | None = None,
) -> list[dict[str, str]]:
    if retired:
        return []

    priority = resolve_execution_priority(profile_name)
    registration_mode = str(priority.get("registration_mode") or "unclassified")
    issues: list[dict[str, str]] = []
    if registration_mode == "hub_managed":
        if configured and (platform_scope or runtime_platform()) == "windows":
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

    if not configured and (platform_scope or runtime_platform()) == "wsl":
        issues.append(
            {
                "severity": "advisory",
                "code": "platform_deferred",
                "message": "Desktop-native MCP is deferred to the Windows host or its governed Hub/owner fallback on WSL.",
            }
        )
    elif not configured:
        issues.append(
            {
                "severity": "risk",
                "code": "mcp_config_missing",
                "message": "Desktop-native MCP server is missing from Codex config.",
            }
        )
    return issues
