"""MCP session pre-dispatch gate for mobile bridge app-server delivery.

Owns: read-only current-session MCP gate decisions before hidden app-server
dispatch creates another Codex turn.
Non-goals: MCP repair, process cleanup, task retry scheduling, queue mutation,
or delivery execution.
State behavior: reads MCP/session diagnostics only; returns a structured gate
result and leaves retry/event persistence to the caller.
Normal callers: mobile_openclaw_cli worker dispatch path and focused gate
regression checks.
"""

from __future__ import annotations

from typing import Any


def current_mcp_session_gate_for_dispatch(delivery_mode: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Block hidden app-server dispatch when this turn already has stale MCP transports.

    This is deliberately a current-turn gate, not a service-health gate. A fresh
    stdio smoke can pass while the active Codex turn still holds closed MCP
    handles; in that state dispatching more work just repeats the same failure.
    """

    trigger = (config or {}).get("trigger", {}) if isinstance((config or {}).get("trigger", {}), dict) else {}
    if trigger.get("mcp_session_gate_for_dispatch_enabled") is False:
        return {"ok": True, "skipped": True, "reason": "disabled_by_config"}
    if str(delivery_mode or "").strip().lower() != "codex-app-server":
        return {"ok": True, "skipped": True, "reason": "delivery_mode_not_app_server"}
    try:
        from mcp_session_doctor import snapshot as mcp_session_snapshot
        from mcp_session_doctor import validate as mcp_session_validate
    except Exception as exc:
        return {
            "ok": True,
            "skipped": True,
            "reason": "mcp_session_doctor_unavailable",
            "error": repr(exc),
            "policy": "do not block dispatch if the read-only gate itself cannot import",
        }
    try:
        snap = mcp_session_snapshot(run_smoke=False)
        validation = mcp_session_validate(snap)
    except Exception as exc:
        return {
            "ok": True,
            "skipped": True,
            "reason": "mcp_session_gate_failed_open",
            "error": repr(exc),
            "policy": "do not block dispatch if the read-only gate itself fails unexpectedly",
        }
    issues = [str(item) for item in validation.get("issues", []) if str(item)]
    current_turn_issues = [
        issue for issue in issues
        if issue.startswith("current turn cannot use ")
    ]
    if current_turn_issues:
        return {
            "ok": False,
            "reason": "mcp_tool_surface_unavailable",
            "issues": current_turn_issues,
            "issue_count": len(current_turn_issues),
            "validation": {
                "ok": bool(validation.get("ok")),
                "schema": validation.get("schema"),
                "generated_at": validation.get("generated_at"),
                "profile_count": validation.get("profile_count"),
            },
            "policy": (
                "Current Codex turn has closed/unavailable MCP transports. "
                "Do not dispatch hidden app-server work into the same stale tool surface; "
                "keep tasks pending until a fresh turn records positive MCP call evidence."
            ),
        }
    return {
        "ok": True,
        "reason": "current_mcp_session_gate_clear",
        "validation": {
            "ok": bool(validation.get("ok")),
            "schema": validation.get("schema"),
            "generated_at": validation.get("generated_at"),
            "profile_count": validation.get("profile_count"),
        },
    }
