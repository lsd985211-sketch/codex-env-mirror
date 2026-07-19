#!/usr/bin/env python3
"""On-demand tool catalog for the local MCP Hub.

Ownership: compact Hub tool discovery, per-tool schema lookup, and exposure
filtering for low-frequency or experimental Hub tools.
Non-goals: target tool execution, permission bypass, or dynamic mutation of the
MCP server tool list during a session.
State behavior: read-only and deterministic from the tool specs supplied by the
Hub process.
Caller context: imported by local_mcp_hub.py as a thin facade.
"""

from __future__ import annotations

from typing import Any

HUB_ON_DEMAND_ACK = "hub-on-demand-call-preserves-original-permissions"

ALWAYS_EXPOSE_PREFIXES = (
    "slash.",
    "pmb.",
    "resource.",
    "resource_search.",
    "workflow.",
    "mobile_bridge.",
    "network_gateway.",
    "network.",
    "agent_bridge.",
    "mcp_session.",
)
ALWAYS_EXPOSE_NAMES = {
    "hub.capabilities",
    "hub.validate",
    "hub.metrics",
    "hub.catalog",
    "hub.search",
    "hub.describe",
    "hub.call",
    "mcp_gateway.route",
    "mcp_gateway.call",
    "mcp_gateway.complete_route",
    "owner_mcp.call_readonly",
    "codegraph.explore",
    "desktop_weixin.capabilities",
    "desktop_weixin.status",
    "github.api",
    "github.gh",
    "github_app.snapshot",
    "github_app.doctor",
    "github_app.validate",
    "secret_vault.snapshot",
    "secret_vault.doctor",
    "secret_vault.validate",
    "sqlite_scratch_health",
    "sqlite_scratch_tables",
    "sqlite_scratch_schema",
    "sqlite_scratch_query",
    "sqlite_bridge_health",
    "sqlite_bridge_tables",
    "sqlite_bridge_schema",
    "sqlite_bridge_query",
    "record_store_health",
    "record_store_tables",
    "record_store_schema",
    "record_store_query",
}
ON_DEMAND_PREFIXES = (
    "chrome_devtools.",
    "metamcp_lab.",
)
ON_DEMAND_NAMES = {
    "desktop_weixin.open",
    "desktop_weixin.close",
    "desktop_weixin.send_text",
    "desktop_weixin.search_contact",
}
EXPERIMENTAL_PREFIXES = ("metamcp_lab.",)


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "hub.catalog",
            "description": "Return compact Hub tool groups without expanding every hidden or low-frequency schema. Use before hub.describe or hub.call.",
            "annotations": {"title": "Hub Tool Catalog", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "include_hidden": {"type": "boolean"},
                    "include_experimental": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "hub.search",
            "description": "Search Hub visible and on-demand tool catalog by compact name, group, and description text.",
            "annotations": {"title": "Hub Tool Search", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "include_experimental": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "hub.describe",
            "description": "Return one Hub tool schema on demand. This is the schema expansion step before hub.call for hidden tools.",
            "annotations": {"title": "Hub Tool Describe", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"tool": {"type": "string"}},
                "required": ["tool"],
                "additionalProperties": False,
            },
        },
        {
            "name": "hub.call",
            "description": "Call one Hub tool by name after hub.describe. Requires acknowledgement and preserves the target tool's original permission boundary.",
            "annotations": {"title": "Hub On-Demand Call", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "arguments": {"type": "object", "additionalProperties": True},
                    "hub_ack": {"type": "string", "description": f"Required exact acknowledgement: {HUB_ON_DEMAND_ACK}"},
                },
                "required": ["tool", "hub_ack"],
                "additionalProperties": False,
            },
        },
    ]


def group_for_tool(name: str) -> str:
    if "." in name:
        return name.split(".", 1)[0]
    if "_" in name:
        return name.rsplit("_", 1)[0]
    return "misc"


def is_experimental(name: str) -> bool:
    return name.startswith(EXPERIMENTAL_PREFIXES)


def is_default_exposed(name: str) -> bool:
    if name in ALWAYS_EXPOSE_NAMES:
        return True
    if name in ON_DEMAND_NAMES:
        return False
    if name.startswith(ON_DEMAND_PREFIXES):
        return False
    return name.startswith(ALWAYS_EXPOSE_PREFIXES)


def split_specs(all_specs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    visible: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for spec in all_specs:
        name = str(spec.get("name") or "")
        if is_default_exposed(name):
            visible.append(spec)
        else:
            hidden.append(spec)
    return visible, hidden


def compact_tool(spec: dict[str, Any], *, visible: bool) -> dict[str, Any]:
    name = str(spec.get("name") or "")
    annotations = spec.get("annotations") if isinstance(spec.get("annotations"), dict) else {}
    schema = spec.get("inputSchema") if isinstance(spec.get("inputSchema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    return {
        "name": name,
        "group": group_for_tool(name),
        "visible": visible,
        "experimental": is_experimental(name),
        "read_only": bool(annotations.get("readOnlyHint")),
        "destructive": bool(annotations.get("destructiveHint")),
        "open_world": bool(annotations.get("openWorldHint")),
        "required": [str(item) for item in required],
        "argument_keys": sorted(str(key) for key in properties),
        "description": str(spec.get("description") or "")[:240],
    }


def catalog(all_specs: list[dict[str, Any]], arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    include_hidden = bool(args.get("include_hidden"))
    include_experimental = bool(args.get("include_experimental"))
    visible_specs, hidden_specs = split_specs(all_specs)
    selected = [(spec, True) for spec in visible_specs]
    if include_hidden:
        selected.extend((spec, False) for spec in hidden_specs)
    tools = [
        compact_tool(spec, visible=visible)
        for spec, visible in selected
        if include_experimental or not is_experimental(str(spec.get("name") or ""))
    ]
    groups: dict[str, dict[str, Any]] = {}
    for tool in tools:
        group = tool["group"]
        entry = groups.setdefault(group, {"name": group, "visible_count": 0, "hidden_count": 0, "experimental_count": 0, "tools": []})
        if tool["visible"]:
            entry["visible_count"] += 1
        else:
            entry["hidden_count"] += 1
        if tool["experimental"]:
            entry["experimental_count"] += 1
        entry["tools"].append(tool["name"])
    return {
        "schema": "local_mcp_hub.on_demand_catalog.v1",
        "ok": True,
        "policy": {
            "default_exposure": "stable_core_plus_catalog",
            "hidden_tools_callable_through": "hub.describe -> hub.call",
            "experimental_default": "hidden",
            "call_ack": HUB_ON_DEMAND_ACK,
        },
        "counts": {
            "visible": len(visible_specs),
            "hidden": len(hidden_specs),
            "returned": len(tools),
            "total": len(all_specs),
        },
        "groups": sorted(groups.values(), key=lambda item: str(item["name"])),
        "tools": sorted(tools, key=lambda item: item["name"]),
    }


def search(all_specs: list[dict[str, Any]], arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip().lower()
    limit = max(1, min(int(arguments.get("limit") or 20), 50))
    include_experimental = bool(arguments.get("include_experimental"))
    if not query:
        return {"ok": False, "reason": "query_required"}
    visible_specs, hidden_specs = split_specs(all_specs)
    specs = [(spec, True) for spec in visible_specs] + [(spec, False) for spec in hidden_specs]
    matches: list[dict[str, Any]] = []
    for spec, visible in specs:
        name = str(spec.get("name") or "")
        if is_experimental(name) and not include_experimental:
            continue
        text = f"{name} {group_for_tool(name)} {spec.get('description') or ''}".lower()
        if query in text:
            matches.append(compact_tool(spec, visible=visible))
    return {
        "schema": "local_mcp_hub.on_demand_search.v1",
        "ok": True,
        "query": query,
        "match_count": len(matches),
        "matches": sorted(matches, key=lambda item: (not item["visible"], item["name"]))[:limit],
    }


def describe(all_specs: list[dict[str, Any]], arguments: dict[str, Any]) -> dict[str, Any]:
    name = str(arguments.get("tool") or "").strip()
    if not name:
        return {"ok": False, "reason": "tool_required"}
    visible_specs, hidden_specs = split_specs(all_specs)
    spec = next((item for item in visible_specs + hidden_specs if item.get("name") == name), None)
    if not spec:
        return {"ok": False, "reason": "tool_not_found", "tool": name}
    return {
        "schema": "local_mcp_hub.on_demand_describe.v1",
        "ok": True,
        "tool": spec,
        "summary": compact_tool(spec, visible=is_default_exposed(name)),
        "call": {"tool": "hub.call", "ack": HUB_ON_DEMAND_ACK},
    }


def call(
    all_specs: list[dict[str, Any]],
    arguments: dict[str, Any],
    dispatch_tool: Any,
) -> dict[str, Any]:
    target_tool = str(arguments.get("tool") or "").strip()
    ack = str(arguments.get("hub_ack") or "").strip()
    if ack != HUB_ON_DEMAND_ACK:
        return {
            "ok": False,
            "reason": "hub_on_demand_ack_required",
            "required": HUB_ON_DEMAND_ACK,
            "tool": target_tool,
        }
    if not target_tool:
        return {"ok": False, "reason": "tool_required"}
    if target_tool.startswith("hub."):
        return {"ok": False, "reason": "recursive_hub_call_blocked", "tool": target_tool}
    described = describe(all_specs, {"tool": target_tool})
    if not described.get("ok"):
        return described
    target_arguments = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
    return dispatch_tool(target_tool, target_arguments)
