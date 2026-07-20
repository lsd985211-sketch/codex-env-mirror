#!/usr/bin/env python3
"""Hub route helpers for workflow, network, and MCP diagnostic tools.

Ownership: local MCP Hub adapters for compact workflow route contracts and
pure command-building helpers for Hub-owned diagnostic routes.
Non-goals: execute workflow phases, mutate state, replace workflow_orchestrator,
change tool schemas, or bypass permission boundaries held by local_mcp_hub.py.
State behavior: read-only; callers provide any command runner and own execution.
Caller context: local_mcp_hub.py exposes these helpers as stable low-token tool
routes so Codex and tool-side callers do not parse full plans or duplicate
network/MCP diagnostic command construction.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from shared.json_cli import now_iso
from workflow_orchestrator import build_plan


DETAIL_LEVELS = {"micro", "standard", "full", "auto"}
JsonCommandRunner = Callable[[list[str], int], dict[str, Any]]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def workflow_route_pack(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the compact execution route pack for one task message."""

    message = _clean_text(arguments.get("message"))
    if not message:
        return {"ok": False, "reason": "message_required", "tool": "workflow.route_pack"}

    risk = _clean_text(arguments.get("risk")) or "unknown"
    detail = _clean_text(arguments.get("detail")).lower() or "micro"
    if detail not in DETAIL_LEVELS:
        return {
            "ok": False,
            "reason": "invalid_detail",
            "tool": "workflow.route_pack",
            "allowed": sorted(DETAIL_LEVELS),
            "detail": detail,
        }

    plan = build_plan(message, risk=risk, detail=detail)
    route_pack = plan.get("execution_route_pack")
    if not isinstance(route_pack, dict):
        return {"ok": False, "reason": "route_pack_missing", "tool": "workflow.route_pack"}

    return {
        "schema": "local_mcp_hub.workflow_route_pack.v1",
        "ok": bool(plan.get("ok") and route_pack.get("ok", True)),
        "generated_at": now_iso(),
        "tool": "workflow.route_pack",
        "request": {
            "message_sha256_12": hashlib.sha256(message.encode("utf-8")).hexdigest()[:12],
            "risk": risk,
            "detail_requested": detail,
            "detail_returned": plan.get("detail_level"),
        },
        "execution_route_pack": route_pack,
    }


def network_doctor_call(name: str, arguments: dict[str, Any], runner: JsonCommandRunner) -> dict[str, Any] | None:
    """Build and execute lower-level network_doctor routes for Hub tools."""

    if name == "network.snapshot":
        return runner(["python", "_bridge\\network_doctor.py", "snapshot"], 10)
    if name == "network.recommend":
        target = _clean_text(arguments.get("target"))
        context = _clean_text(arguments.get("context"))
        if not target:
            return {"ok": False, "reason": "target_required", "tool": name}
        command = ["python", "_bridge\\network_doctor.py", "recommend", target]
        if context:
            command.extend(["--context", context])
        return runner(command, 10)
    if name == "network.env":
        target = _clean_text(arguments.get("target"))
        context = _clean_text(arguments.get("context"))
        runtime = _clean_text(arguments.get("runtime")) or "generic"
        if not target:
            return {"ok": False, "reason": "target_required", "tool": name}
        command = ["python", "_bridge\\network_doctor.py", "env", target, "--runtime", runtime]
        if context:
            command.extend(["--context", context])
        return runner(command, 10)
    if name == "network.plan":
        target = _clean_text(arguments.get("target"))
        context = _clean_text(arguments.get("context"))
        if not target:
            return {"ok": False, "reason": "target_required", "tool": name}
        command = ["python", "_bridge\\network_doctor.py", "plan", target]
        if context:
            command.extend(["--context", context])
        return runner(command, 10)
    if name == "network.probe":
        target = _clean_text(arguments.get("target"))
        context = _clean_text(arguments.get("context"))
        timeout = int(arguments.get("timeout") or 20)
        if not target:
            return {"ok": False, "reason": "target_required", "tool": name}
        command = ["python", "_bridge\\network_doctor.py", "probe", target, "--timeout", str(timeout)]
        if context:
            command.extend(["--context", context])
        return runner(command, timeout + 10)
    if name == "network.probe_suite":
        timeout = int(arguments.get("timeout") or 10)
        return runner(["python", "_bridge\\network_doctor.py", "probe-suite", "--timeout", str(timeout)], (timeout + 5) * 6)
    if name == "network.validate":
        return runner(["python", "_bridge\\network_doctor.py", "validate"], 10)
    return None


def mcp_session_doctor_call(name: str, arguments: dict[str, Any], runner: JsonCommandRunner) -> dict[str, Any] | None:
    """Build and execute MCP session doctor routes for Hub tools."""

    if name == "mcp_session.validate":
        return runner(["python", "_bridge\\mcp_session_doctor.py", "validate"], 30)
    if name == "mcp_session.metrics":
        return runner(["python", "_bridge\\mcp_session_doctor.py", "metrics"], 30)
    if name == "mcp_session.recover_plan":
        profile = _clean_text(arguments.get("profile"))
        status = _clean_text(arguments.get("status")) or "transport_closed"
        return runner(
            [
                "python",
                "_bridge\\mcp_session_doctor.py",
                "recover-plan",
                "--profile",
                profile,
                "--status",
                status,
            ],
            30,
        )
    return None
