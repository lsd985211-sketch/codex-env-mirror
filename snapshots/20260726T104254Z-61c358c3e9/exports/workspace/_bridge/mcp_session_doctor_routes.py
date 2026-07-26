#!/usr/bin/env python3
"""CLI route dispatch helpers for MCP session diagnostics.

Ownership: mapping mcp_session_doctor CLI command names to injected handler
calls and preserving command payload argument shaping.
Non-goals: read or write observation logs, run protocol smoke, call gateways,
own route policy, or change permission/fallback semantics.
State behavior: read-only by itself; injected handlers own any state effects.
Caller context: mcp_session_doctor.py keeps business diagnostics and passes its
existing handlers here so the CLI facade stays stable while dispatch remains
easy to audit and extend.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shared.json_cli import now_iso


HandlerMap = dict[str, Callable[..., dict[str, Any]]]


def direct_command_payload(
    args: argparse.Namespace,
    handlers: HandlerMap,
    *,
    observation_log: Path,
    current_turn_probe_source: str,
) -> dict[str, Any] | None:
    """Return the payload for direct MCP session CLI commands."""

    if args.command == "record-observation":
        return handlers["record_observation"](args.profile, args.status, source=args.source, detail=args.detail, dry_run=bool(args.dry_run))
    if args.command == "record-observations":
        items, error = handlers["load_observation_items"](args.items_json, args.items_file)
        if error:
            return {
                "schema": "mcp_session.record_observations.v1",
                "ok": False,
                "error": error,
                "generated_at": now_iso(),
                "observation_log": str(observation_log),
            }
        return handlers["record_observations"](
            items,
            default_source=args.source,
            default_status=args.status,
            default_detail=args.detail,
            dry_run=bool(args.dry_run),
        )
    if args.command == "batch-recording-contract-check":
        return handlers["batch_recording_contract_check"]()
    if args.command == "route-completion-contract-check":
        return handlers["route_completion_contract_check"]()
    if args.command == "smoke":
        return handlers["protocol_smoke"](args.profile, timeout_seconds=int(args.timeout_seconds or 0) or None)
    if args.command == "tool-call":
        tool_args, error = handlers["load_cli_json_object"](args.arguments_json, "mcp_session.tool_call.v1")
        return error or handlers["protocol_tool_call"](
            args.profile,
            args.tool,
            arguments=tool_args,
            timeout_seconds=int(args.timeout_seconds or 0) or None,
        )
    if args.command == "gateway-route":
        return handlers["gateway_route"](args.profile, args.tool)
    if args.command == "gateway-call":
        tool_args, error = handlers["load_cli_json_object"](args.arguments_json, "mcp_tool_gateway.call.v1")
        return error or handlers["gateway_call"](
            args.profile,
            args.tool,
            arguments=tool_args,
            timeout_seconds=int(args.timeout_seconds or 0) or None,
        )
    if args.command == "complete-route":
        tool_args, error = handlers["load_cli_json_object"](args.arguments_json, "mcp_session.route_completion.v1")
        return error or handlers["complete_route_after_native_failure"](
            args.profile,
            args.tool,
            status=args.status or "transport_closed",
            detail=args.detail,
            arguments=tool_args,
            timeout_seconds=int(args.timeout_seconds or 0) or None,
            source=args.source or current_turn_probe_source,
            dry_run=bool(args.dry_run),
        )
    if args.command == "gateway-warmup":
        profiles = handlers["gateway_warmup_profiles"](args)
        return handlers["gateway_warmup"](profiles, timeout_seconds=int(args.timeout_seconds or 0) or None)
    if args.command == "recover-plan":
        return handlers["recover_plan"](args.profile, status=args.status or "transport_closed")
    return None
