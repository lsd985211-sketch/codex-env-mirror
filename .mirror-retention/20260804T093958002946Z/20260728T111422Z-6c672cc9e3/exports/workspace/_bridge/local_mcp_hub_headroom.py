#!/usr/bin/env python3
"""Hub adapter for the managed standalone Headroom compression MCP.

Ownership: expose compress, retrieve, and stats through one fresh stdio call
using the managed Headroom runtime and a fixed upstream tool allowlist.
Non-goals: proxy/provider wrapping, model configuration, long-term memory,
arbitrary Headroom CLI access, file reading, or package installation.
State behavior: compression writes only the owner-declared TTL cache; retrieve
and stats read that cache. Caller context: local_mcp_hub owns public schemas and
dispatches here without expanding Headroom's upstream permissions.
"""

from __future__ import annotations

from typing import Any

import headroom_runtime
from local_mcp_stdio_client import fresh_stdio_call


UPSTREAM_TOOLS = {
    "headroom_compress",
    "headroom_retrieve",
    "headroom_stats",
}
MAX_CONTENT_CHARS = 2_000_000


def _timeout(arguments: dict[str, Any]) -> int:
    try:
        value = int(arguments.get("timeout_seconds") or 60)
    except (TypeError, ValueError):
        value = 60
    return max(1, min(value, 120))


def call(upstream_tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if upstream_tool not in UPSTREAM_TOOLS:
        return {"ok": False, "reason": "headroom_tool_not_allowlisted", "tool": upstream_tool}
    forwarded = {key: value for key, value in arguments.items() if key != "timeout_seconds"}
    if upstream_tool == "headroom_compress":
        content = forwarded.get("content")
        if not isinstance(content, str) or not content:
            return {"ok": False, "reason": "content_required", "tool": upstream_tool}
        if len(content) > MAX_CONTENT_CHARS:
            return {"ok": False, "reason": "content_exceeds_bounded_limit", "max_content_chars": MAX_CONTENT_CHARS, "content_chars": len(content)}
    command = headroom_runtime.command_spec()
    if not command.get("ok"):
        return {"ok": False, "reason": "headroom_runtime_not_ready", "runtime": command.get("runtime", {})}
    result = fresh_stdio_call(
        command=list(command["command"]),
        working_directory=str(command["working_directory"]),
        tool=upstream_tool,
        arguments=forwarded,
        timeout_seconds=_timeout(arguments),
        allowed_tools=UPSTREAM_TOOLS,
        client_name="local-mcp-hub-headroom",
    )
    result.setdefault("tool", upstream_tool)
    result["runtime_version"] = headroom_runtime.EXPECTED_VERSION
    return result


def validate() -> dict[str, Any]:
    runtime = headroom_runtime.validate()
    return {
        "schema": "local_mcp_hub_headroom.validate.v1",
        "ok": bool(runtime.get("ok")),
        "runtime": runtime,
        "tools": sorted(UPSTREAM_TOOLS),
        "lifecycle": "fresh_stdio_per_call_exit",
        "memory_boundary": "TTL context cache only; PMB remains the long-term memory authority",
        "provider_config_modified": False,
    }
