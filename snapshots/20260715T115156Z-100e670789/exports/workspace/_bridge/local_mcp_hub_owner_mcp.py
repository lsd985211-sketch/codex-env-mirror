#!/usr/bin/env python3
"""Guarded Hub-first adapter for stateless read-only owner MCP calls.

Ownership: explicit profile/tool allowlist and same-boundary fresh-stdio calls.
Non-goals: write tools, session-bound browser/GUI/mobile calls, permission changes,
or dynamic route discovery.
State behavior: delegates to the existing MCP session gateway state only.
Caller context: local_mcp_hub tool registration and validation.
"""

from __future__ import annotations

from typing import Any, Callable


HUB_READONLY_ACK = "hub-readonly-owner-call-preserves-original-permissions"

FILESYSTEM_READ_TOOLS = {
    "directory_tree",
    "get_file_info",
    "list_allowed_directories",
    "list_directory",
    "list_directory_with_sizes",
    "read_file",
    "read_media_file",
    "read_multiple_files",
    "read_text_file",
    "search_files",
}

READONLY_OWNER_TOOLS: dict[str, set[str]] = {
    "context7": {"resolve_library_id", "query_docs", "resolve-library-id", "get-library-docs"},
    "microsoftdocs": {"microsoft_docs_search", "microsoft_docs_fetch", "microsoft_code_sample_search"},
    "filesystem": FILESYSTEM_READ_TOOLS,
    "filesystem-admin": FILESYSTEM_READ_TOOLS,
    "markitdown": {"convert_to_markdown"},
    "myskills": {"skills_inventory", "skills_read", "skills_history", "scenarios_list", "discover_search"},
}


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "owner_mcp.call_readonly",
            "description": (
                "Call an explicitly allowlisted stateless read-only owner MCP through Hub-first fresh stdio. "
                "No prior native failure is required and the target MCP permission boundary is preserved."
            ),
            "annotations": {
                "title": "Read-only Owner MCP Call",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "profile": {"type": "string", "enum": sorted(READONLY_OWNER_TOOLS)},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object", "additionalProperties": True},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                    "hub_ack": {"type": "string", "description": f"Required exact acknowledgement: {HUB_READONLY_ACK}"},
                },
                "required": ["profile", "tool", "hub_ack"],
                "additionalProperties": False,
            },
        }
    ]


def _compact_tool_result(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    content = value.get("content")
    structured = value.get("structuredContent")
    if (
        isinstance(structured, dict)
        and isinstance(content, list)
        and len(content) == 1
        and isinstance(content[0], dict)
        and content[0].get("type") == "text"
        and structured.get("content") == content[0].get("text")
    ):
        return {"structuredContent": structured}
    return value


def _compact_gateway_payload(payload: dict[str, Any], *, profile: str, tool: str) -> dict[str, Any]:
    session = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    error = session.get("error") if session else payload.get("error")
    tool_result = session.get("result") if session else payload.get("result")
    return {
        "schema": "local_mcp_hub.owner_mcp.call_readonly.v1",
        "ok": bool(payload.get("ok")) and not bool(session.get("tool_result_is_error")),
        "profile": profile,
        "tool": tool,
        "route": {
            "mode": route.get("route"),
            "reason": route.get("reason"),
            "transport_isolated_from_current_turn": payload.get("transport_isolated_from_current_turn"),
        },
        "result": _compact_tool_result(tool_result),
        "error": error,
        "gateway_status": payload.get("gateway_status"),
        "gateway_state_path": payload.get("gateway_state_path"),
    }


def call(arguments: dict[str, Any], gateway_call: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    profile = str(arguments.get("profile") or "").strip().lower()
    tool = str(arguments.get("tool") or "").strip()
    hub_ack = str(arguments.get("hub_ack") or "").strip()
    if hub_ack != HUB_READONLY_ACK:
        return {"ok": False, "reason": "hub_readonly_ack_required", "required": HUB_READONLY_ACK, "profile": profile, "tool": tool}
    allowed = READONLY_OWNER_TOOLS.get(profile)
    if not allowed:
        return {"ok": False, "reason": "profile_not_allowlisted", "profile": profile, "tool": tool}
    if tool not in allowed:
        return {
            "ok": False,
            "reason": "tool_not_allowlisted_readonly",
            "profile": profile,
            "tool": tool,
            "allowed_tools": sorted(allowed),
        }
    tool_arguments = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
    timeout_seconds = max(1, min(int(arguments.get("timeout_seconds") or 45), 120))
    raw_payload = gateway_call(profile, tool, arguments=tool_arguments, timeout_seconds=timeout_seconds)
    payload = _compact_gateway_payload(raw_payload, profile=profile, tool=tool)
    payload.setdefault("owner_mcp_policy", {})
    if isinstance(payload["owner_mcp_policy"], dict):
        payload["owner_mcp_policy"].update(
            {
                "execution_affinity": "hub_first",
                "read_only_allowlisted": True,
                "prior_native_failure_required": False,
                "permission_boundary": "same_as_target_mcp_profile",
            }
        )
    return payload


def validate() -> dict[str, Any]:
    forbidden_profiles = sorted(set(READONLY_OWNER_TOOLS) & {"chrome-devtools", "gui-automation", "playwright", "mobile-openclaw-bridge"})
    write_markers = ("write", "edit", "move", "delete", "create", "upload", "send", "install", "apply")
    suspicious = sorted(
        f"{profile}:{tool}"
        for profile, tools in READONLY_OWNER_TOOLS.items()
        for tool in tools
        if any(marker in tool.lower() for marker in write_markers)
    )
    return {
        "schema": "local_mcp_hub.owner_mcp.validate.v1",
        "ok": not forbidden_profiles and not suspicious,
        "forbidden_profiles": forbidden_profiles,
        "suspicious_tools": suspicious,
        "profiles": {profile: sorted(tools) for profile, tools in READONLY_OWNER_TOOLS.items()},
        "filesystem_admin_rule": "default file profile may be admin, but this Hub adapter remains read-only and preserves the target profile boundary",
    }
