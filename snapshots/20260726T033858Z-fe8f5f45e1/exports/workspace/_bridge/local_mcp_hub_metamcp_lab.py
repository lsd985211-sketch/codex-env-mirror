#!/usr/bin/env python3
"""Hub-facing adapter for the isolated MetaMCP gateway lab.

Ownership: compact catalog/search/describe/read-only-call helpers for the
MetaMCP lab endpoint so Hub can test lazy MCP aggregation without exposing every
child tool schema to Codex.
Non-goals: no production Codex MCP registration, no MetaMCP startup control, no
secret handling, no write-capable gateway calls, and no system proxy/DNS changes.
State behavior: read-only HTTP calls to the localhost lab endpoint; child tool
calls are limited to tools advertised as read-only and require an explicit lab
acknowledgement.
Caller context: imported by `_bridge/local_mcp_hub.py` as a thin Hub route.
"""

from __future__ import annotations

import json
import time
from typing import Any

from gateway_lab_metamcp import LAB_ENDPOINT, PUBLIC_ENDPOINT_URL, compact_http_response, db_counts, docker_path
from gateway_lab_metamcp import http_request, run_command, sse_message_body, status
from shared.json_cli import now_iso


LAB_READONLY_ACK = "gateway-lab-readonly-and-production-native-first"
ENDPOINT = f"{PUBLIC_ENDPOINT_URL}/{LAB_ENDPOINT}/mcp"
COMMON_HEADERS = {"Accept": "application/json, text/event-stream"}


def _session_initialize(client_name: str = "local-hub-metamcp-lab") -> tuple[dict[str, Any], str]:
    initialize = http_request(
        ENDPOINT,
        method="POST",
        body={
            "jsonrpc": "2.0",
            "id": 101,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": "0.1.0"},
            },
        },
        headers=COMMON_HEADERS,
        timeout=30,
    )
    session_id = str((initialize.get("headers") or {}).get("mcp-session-id") or "") if initialize.get("ok") else ""
    return initialize, session_id


def _session_delete(session_id: str) -> None:
    if session_id:
        http_request(ENDPOINT, method="DELETE", headers={**COMMON_HEADERS, "mcp-session-id": session_id}, timeout=10)


def _tools_list() -> dict[str, Any]:
    initialize, session_id = _session_initialize()
    if not session_id:
        return {
            "ok": False,
            "reason": "missing_session_id",
            "initialize": compact_http_response(initialize),
        }
    try:
        response = http_request(
            ENDPOINT,
            method="POST",
            body={"jsonrpc": "2.0", "id": 102, "method": "tools/list", "params": {}},
            headers={**COMMON_HEADERS, "mcp-session-id": session_id},
            timeout=120,
            max_text_body=0,
        )
        parsed = sse_message_body(response.get("body"))
        result = parsed.get("result") if isinstance(parsed, dict) else {}
        tools = result.get("tools") if isinstance(result, dict) else []
        if not isinstance(tools, list):
            tools = []
        return {
            "ok": bool(response.get("ok")),
            "generated_at": now_iso(),
            "endpoint": ENDPOINT,
            "raw_response": compact_http_response(response),
            "tools": [tool for tool in tools if isinstance(tool, dict)],
            "initialize_elapsed_ms": initialize.get("elapsed_ms"),
            "tools_list_elapsed_ms": response.get("elapsed_ms"),
        }
    finally:
        _session_delete(session_id)


def _server_config_rows() -> list[dict[str, Any]]:
    sql = (
        "select s.name, s.command, array_to_json(s.args)::text, s.env::text, s.type "
        "from mcp_servers s "
        "join namespace_server_mappings m on m.mcp_server_uuid = s.uuid "
        "join namespaces n on n.uuid = m.namespace_uuid "
        "where n.name = 'Lab' and n.user_id is null "
        "order by s.name;"
    )
    result = run_command(
        [
            docker_path(),
            "exec",
            "metamcp-pg",
            "psql",
            "-U",
            "metamcp_lab_user",
            "-d",
            "metamcp_lab_db",
            "-At",
            "-F",
            "\t",
            "-c",
            sql,
        ],
        timeout=20,
    )
    rows: list[dict[str, Any]] = []
    if not result.get("ok"):
        return rows
    for line in str(result.get("stdout") or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        args: Any = []
        env: Any = {}
        try:
            args = json.loads(parts[2]) if parts[2] else []
        except json.JSONDecodeError:
            args = []
        try:
            env = json.loads(parts[3]) if parts[3] else {}
        except json.JSONDecodeError:
            env = {}
        rows.append(
            {
                "name": parts[0],
                "command": parts[1],
                "args": args if isinstance(args, list) else [],
                "env_keys": sorted(env) if isinstance(env, dict) else [],
                "network_profile": (env or {}).get("CODEX_NETWORK_PROFILE", "") if isinstance(env, dict) else "",
                "network_route": (env or {}).get("CODEX_NETWORK_ROUTE", "") if isinstance(env, dict) else "",
                "network_target": (env or {}).get("CODEX_NETWORK_TARGET", "") if isinstance(env, dict) else "",
                "type": parts[4],
            }
        )
    return rows


def _server_name_for_tool(tool_name: str) -> str:
    return str(tool_name or "").split("__", 1)[0] if "__" in str(tool_name or "") else ""


def _compact_tool(tool: dict[str, Any], server_configs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    name = str(tool.get("name") or "")
    server_name = _server_name_for_tool(name)
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    description = str(tool.get("description") or "")
    return {
        "name": name,
        "title": str(tool.get("title") or ""),
        "server": server_name,
        "read_only": bool(annotations.get("readOnlyHint")),
        "destructive": bool(annotations.get("destructiveHint")),
        "open_world": bool(annotations.get("openWorldHint")),
        "required": schema.get("required", []) if isinstance(schema.get("required"), list) else [],
        "argument_keys": sorted(str(key) for key in properties),
        "description_preview": description[:240],
        "network": {
            "profile": server_configs.get(server_name, {}).get("network_profile", ""),
            "route": server_configs.get(server_name, {}).get("network_route", ""),
            "target": server_configs.get(server_name, {}).get("network_target", ""),
        },
    }


def catalog(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    include_descriptions = bool(args.get("include_descriptions"))
    current_status = status()
    tools_payload = _tools_list()
    server_rows = _server_config_rows()
    server_configs = {row["name"]: row for row in server_rows}
    compact_tools = [_compact_tool(tool, server_configs) for tool in tools_payload.get("tools", [])]
    if not include_descriptions:
        for tool in compact_tools:
            tool.pop("description_preview", None)
    return {
        "schema": "local_mcp_hub.metamcp_lab_catalog.v1",
        "ok": bool(current_status.get("ok") and tools_payload.get("ok")),
        "generated_at": now_iso(),
        "lab_status": current_status.get("status"),
        "db": db_counts(),
        "endpoint": ENDPOINT,
        "tool_count": len(compact_tools),
        "servers": server_rows,
        "tools": compact_tools,
        "token_policy": "compact catalog only; use describe for one full schema",
        "network_policy": "per-child network env is recorded in MetaMCP server env and surfaced by server/tool",
        "boundary": "isolated lab only; native MCP remains first; no production Codex config mutation",
    }


def search(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip().lower()
    limit = max(1, min(int(arguments.get("limit") or 10), 30))
    payload = catalog({"include_descriptions": True})
    if not query:
        matches = payload.get("tools", [])[:limit]
    else:
        matches = []
        for tool in payload.get("tools", []):
            haystack = " ".join(str(tool.get(key) or "") for key in ("name", "title", "server", "description_preview")).lower()
            if query in haystack:
                matches.append(tool)
            if len(matches) >= limit:
                break
    return {
        "schema": "local_mcp_hub.metamcp_lab_search.v1",
        "ok": bool(payload.get("ok")),
        "generated_at": now_iso(),
        "query": query,
        "match_count": len(matches),
        "matches": matches,
        "boundary": payload.get("boundary"),
    }


def describe(arguments: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(arguments.get("tool") or "").strip()
    if not tool_name:
        return {"ok": False, "reason": "tool_required", "required": "tool"}
    tools_payload = _tools_list()
    server_rows = _server_config_rows()
    server_configs = {row["name"]: row for row in server_rows}
    tool = next((item for item in tools_payload.get("tools", []) if item.get("name") == tool_name), None)
    if not tool:
        return {
            "schema": "local_mcp_hub.metamcp_lab_describe.v1",
            "ok": False,
            "reason": "tool_not_found",
            "tool": tool_name,
            "available": [str(item.get("name") or "") for item in tools_payload.get("tools", [])],
        }
    compact = _compact_tool(tool, server_configs)
    return {
        "schema": "local_mcp_hub.metamcp_lab_describe.v1",
        "ok": True,
        "generated_at": now_iso(),
        "tool": compact,
        "inputSchema": tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {},
        "annotations": tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {},
        "description": str(tool.get("description") or ""),
        "boundary": "single-tool schema expansion only; lab-only",
    }


def call_readonly(arguments: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(arguments.get("tool") or "").strip()
    tool_arguments = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
    timeout_seconds = max(1, min(int(arguments.get("timeout_seconds") or 120), 180))
    ack = str(arguments.get("gateway_lab_ack") or "").strip()
    if ack != LAB_READONLY_ACK:
        return {
            "ok": False,
            "reason": "gateway_lab_ack_required",
            "required": LAB_READONLY_ACK,
            "tool": tool_name,
        }
    described = describe({"tool": tool_name})
    if not described.get("ok"):
        return described
    tool_meta = described.get("tool") if isinstance(described.get("tool"), dict) else {}
    if not bool(tool_meta.get("read_only")):
        return {
            "ok": False,
            "reason": "only_readonly_tools_allowed_in_lab_hub_adapter",
            "tool": tool_name,
            "tool_meta": tool_meta,
        }

    initialize, session_id = _session_initialize("local-hub-metamcp-lab-readonly-call")
    if not session_id:
        return {"ok": False, "reason": "missing_session_id", "initialize": compact_http_response(initialize)}
    started = time.time()
    try:
        response = http_request(
            ENDPOINT,
            method="POST",
            body={
                "jsonrpc": "2.0",
                "id": 103,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": tool_arguments},
            },
            headers={**COMMON_HEADERS, "mcp-session-id": session_id},
            timeout=timeout_seconds,
            max_text_body=0,
        )
        parsed = sse_message_body(response.get("body"))
        return {
            "schema": "local_mcp_hub.metamcp_lab_call_readonly.v1",
            "ok": bool(response.get("ok")) and not bool((parsed.get("result") or {}).get("isError") if isinstance(parsed.get("result"), dict) else False),
            "generated_at": now_iso(),
            "tool": tool_name,
            "tool_meta": tool_meta,
            "elapsed_ms": int((time.time() - started) * 1000),
            "response": compact_http_response(response),
            "parsed": parsed,
            "boundary": "isolated lab read-only call; native MCP remains preferred and production permissions are not expanded",
        }
    finally:
        _session_delete(session_id)


def validate() -> dict[str, Any]:
    cat = catalog({"include_descriptions": False})
    readonly_tool = next((tool for tool in cat.get("tools", []) if tool.get("read_only")), {})
    issues: list[dict[str, Any]] = []
    if not cat.get("ok"):
        issues.append({"severity": "risk", "code": "catalog_failed"})
    if not readonly_tool:
        issues.append({"severity": "risk", "code": "no_readonly_lab_tool"})
    if not any(row.get("network_profile") for row in cat.get("servers", [])):
        issues.append({"severity": "advisory", "code": "no_child_network_profile_recorded"})
    return {
        "schema": "local_mcp_hub.metamcp_lab_validate.v1",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "catalog": {
            "ok": cat.get("ok"),
            "tool_count": cat.get("tool_count"),
            "servers": cat.get("servers"),
            "sample_readonly_tool": readonly_tool.get("name", ""),
        },
        "boundary": "validates lab adapter only; no production integration",
    }


def tools_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    routes = {
        "metamcp_lab.catalog": catalog,
        "metamcp_lab.search": search,
        "metamcp_lab.describe": describe,
        "metamcp_lab.call_readonly": call_readonly,
        "metamcp_lab.validate": lambda _args: validate(),
    }
    handler = routes.get(str(name or ""))
    if not handler:
        return {"ok": False, "reason": "unknown_metamcp_lab_tool", "tool": name}
    return handler(arguments)
