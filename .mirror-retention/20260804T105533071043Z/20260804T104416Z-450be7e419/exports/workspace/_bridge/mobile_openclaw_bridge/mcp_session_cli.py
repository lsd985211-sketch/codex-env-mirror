"""MCP session maintenance command adapter for mobile_openclaw_cli.

Owns: CLI parser registration and dispatch for the MCP session doctor,
protocol smoke, current-turn observation recording, and Hub gateway routes.
Non-goals: MCP server implementation, bridge queue mutation, Weixin delivery,
or permission decisions.
State behavior: may write MCP observation records only through
mcp_session_doctor's explicit record actions; other actions are read-only.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main
when args.cmd == "mcp-session".
"""

from __future__ import annotations

import json
from typing import Any


def register_mcp_session_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("mcp-session", help="MCP session transport/fallback doctor")
    parser.add_argument(
        "action",
        choices=[
            "snapshot",
            "doctor",
            "repair-plan",
            "metrics",
            "validate",
            "record-observation",
            "record-observations",
            "batch-recording-contract-check",
            "route-completion-contract-check",
            "smoke",
            "tool-call",
            "gateway-route",
            "gateway-call",
            "complete-route",
            "gateway-warmup",
        ],
    )
    parser.add_argument("--observe", action="append", default=[], help="Observation in profile:status form, e.g. codegraph:transport_closed")
    parser.add_argument("--run-fallback", action="store_true", help="Run bounded fallback probes where available")
    parser.add_argument("--run-smoke", action="store_true", help="Run bounded protocol initialize/tools-list smoke probes")
    parser.add_argument("--smoke-profile", action="append", default=[], help="MCP profile name for protocol smoke; may repeat")
    parser.add_argument("--timeout-seconds", type=int, default=0, help="Timeout for direct smoke command")
    parser.add_argument("--profile", default="", help="MCP profile name for record-observation")
    parser.add_argument("--status", default="", help="Observation status for record-observation, e.g. transport_closed")
    parser.add_argument("--source", default="", help="Observation source for record-observation")
    parser.add_argument("--detail", default="", help="Observation detail for record-observation")
    parser.add_argument("--items-json", default="", help="JSON array for record-observations")
    parser.add_argument("--items-file", default="", help="UTF-8 JSON array file for record-observations")
    parser.add_argument("--dry-run", action="store_true", help="Validate record-observation(s) without writing")
    parser.add_argument("--tool", default="", help="MCP tool name for tool-call")
    parser.add_argument("--arguments-json", default="{}", help="JSON object arguments for tool-call")
    parser.add_argument("--gateway-profile", action="append", default=[], help="MCP profile for gateway-warmup; may repeat")
    parser.add_argument("--thread-id", default="", help="Optional Codex thread id for current-turn anchoring")


def _load_tool_arguments(arguments_json: str, schema: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        parsed = json.loads(str(arguments_json or "{}"))
    except json.JSONDecodeError as exc:
        return None, {"schema": schema, "ok": False, "error": f"arguments_json_invalid: {exc}"}
    if not isinstance(parsed, dict):
        return None, {"schema": schema, "ok": False, "error": "arguments_json_must_be_object"}
    return parsed, None


def run_mcp_session_command(args: Any) -> dict[str, Any]:
    from mcp_session_doctor import _load_observation_items
    from mcp_session_doctor import batch_recording_contract_check
    from mcp_session_doctor import complete_route_after_native_failure
    from mcp_session_doctor import doctor
    from mcp_session_doctor import gateway_call
    from mcp_session_doctor import gateway_route
    from mcp_session_doctor import gateway_warmup
    from mcp_session_doctor import metrics
    from mcp_session_doctor import protocol_smoke
    from mcp_session_doctor import protocol_tool_call
    from mcp_session_doctor import record_observation
    from mcp_session_doctor import record_observations
    from mcp_session_doctor import repair_plan
    from mcp_session_doctor import route_completion_contract_check
    from mcp_session_doctor import snapshot
    from mcp_session_doctor import validate

    if args.action == "record-observation":
        return record_observation(
            str(args.profile or ""),
            str(args.status or ""),
            source=str(args.source or ""),
            detail=str(args.detail or ""),
            dry_run=bool(args.dry_run),
        )

    if args.action == "record-observations":
        items, error = _load_observation_items(str(args.items_json or ""), str(args.items_file or ""))
        if error:
            return {"schema": "mcp_session.record_observations.v1", "ok": False, "error": error}
        return record_observations(
            items,
            default_source=str(args.source or ""),
            default_status=str(args.status or ""),
            default_detail=str(args.detail or ""),
            dry_run=bool(args.dry_run),
        )

    if args.action == "batch-recording-contract-check":
        return batch_recording_contract_check()

    if args.action == "route-completion-contract-check":
        return route_completion_contract_check()

    if args.action == "smoke":
        return protocol_smoke(str(args.profile or ""), timeout_seconds=int(args.timeout_seconds or 0) or None)

    if args.action == "tool-call":
        tool_args, error = _load_tool_arguments(str(args.arguments_json or "{}"), "mcp_session.tool_call.v1")
        if error:
            return error
        return protocol_tool_call(
            str(args.profile or ""),
            str(args.tool or ""),
            arguments=tool_args or {},
            timeout_seconds=int(args.timeout_seconds or 0) or None,
        )

    if args.action == "gateway-route":
        return gateway_route(str(args.profile or ""), str(args.tool or ""))

    if args.action == "gateway-call":
        tool_args, error = _load_tool_arguments(str(args.arguments_json or "{}"), "mcp_tool_gateway.call.v1")
        if error:
            return error
        return gateway_call(
            str(args.profile or ""),
            str(args.tool or ""),
            arguments=tool_args or {},
            timeout_seconds=int(args.timeout_seconds or 0) or None,
        )

    if args.action == "complete-route":
        tool_args, error = _load_tool_arguments(str(args.arguments_json or "{}"), "mcp_session.route_completion.v1")
        if error:
            return error
        return complete_route_after_native_failure(
            str(args.profile or ""),
            str(args.tool or ""),
            status=str(args.status or "transport_closed"),
            detail=str(args.detail or ""),
            arguments=tool_args or {},
            timeout_seconds=int(args.timeout_seconds or 0) or None,
            source=str(args.source or "") or "current-codex-turn",
            dry_run=bool(args.dry_run),
        )

    if args.action == "gateway-warmup":
        profiles = [str(item).strip() for item in (args.gateway_profile or []) if str(item).strip()]
        if not profiles and str(args.profile or "").strip():
            profiles = [str(args.profile).strip()]
        if not profiles:
            profiles = ["custom-slash-commands", "sqlite-scratch", "sqlite-bridge-ro"]
        return gateway_warmup(profiles, timeout_seconds=int(args.timeout_seconds or 0) or None)

    snap = snapshot(
        observations=list(args.observe or []),
        run_fallback=bool(args.run_fallback),
        run_smoke=bool(args.run_smoke),
        smoke_profiles=list(args.smoke_profile or []),
        thread_id=str(args.thread_id or "").strip() or None,
    )
    if args.action == "snapshot":
        return snap
    if args.action == "doctor":
        return doctor(snap)
    if args.action == "repair-plan":
        return repair_plan(snap)
    if args.action == "metrics":
        return metrics(snap)
    return validate(snap)
