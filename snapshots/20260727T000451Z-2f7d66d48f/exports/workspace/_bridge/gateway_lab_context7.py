#!/usr/bin/env python3
"""Context7 child MCP route for the isolated MetaMCP gateway lab.

Ownership: lab-only registration and validation of a real read-only Context7
MCP child behind MetaMCP.
Non-goals: no production Codex MCP registration, no secret migration, no host
filesystem access, no global npm install, and no write-capable tool routing.
State behavior: writes only the isolated MetaMCP lab database when explicitly
confirmed; validation performs read-only Context7 documentation lookup.
Caller context: imported by `_bridge/gateway_lab.py` as a thin CLI facade.
"""

from __future__ import annotations

import json
from typing import Any

from gateway_lab_metamcp import LAB_ENDPOINT, LAB_NAMESPACE, POSTGRES_CONTAINER
from gateway_lab_metamcp import PUBLIC_ENDPOINT_URL, bootstrap_lab, docker_path
from gateway_lab_metamcp import compact_http_response, http_request, run_command, sql_literal, sse_message_body, status
from network_policy import env_for_runtime
from shared.json_cli import now_iso


SERVER_NAME = "context7_docs"
PACKAGE_NAME = "@upstash/context7-mcp"
PACKAGE_VERSION = "3.2.3"
PACKAGE_SPEC = f"{PACKAGE_NAME}@{PACKAGE_VERSION}"
NETWORK_TARGET = "https://registry.npmjs.org/@upstash/context7-mcp"


def docker_child_proxy_url(value: str) -> str:
    text = str(value or "")
    return text.replace("://127.0.0.1:", "://host.docker.internal:").replace("://localhost:", "://host.docker.internal:")


def child_network_env() -> dict[str, Any]:
    payload = env_for_runtime(NETWORK_TARGET, runtime="npx", context="metamcp_context7_child")
    env = {str(key): str(value) for key, value in (payload.get("env") or {}).items() if value}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        if key in env:
            env[key] = docker_child_proxy_url(env[key])
    recommendation = payload.get("recommendation") if isinstance(payload.get("recommendation"), dict) else {}
    env["CODEX_NETWORK_PROFILE"] = str(recommendation.get("profile") or "")
    env["CODEX_NETWORK_ROUTE"] = str(recommendation.get("route") or "")
    env["CODEX_NETWORK_TARGET"] = NETWORK_TARGET
    env["CODEX_NETWORK_CONTEXT"] = "metamcp_context7_child"
    env["CODEX_NETWORK_CONTAINER_PROXY_REWRITE"] = "host.docker.internal"
    return {
        "schema": "gateway_lab.context7_network_env.v1",
        "ok": True,
        "source": payload,
        "env": env,
        "container_note": "MetaMCP child runs inside Docker; host localhost proxy is rewritten to host.docker.internal.",
    }


def install_sql() -> str:
    network_env = child_network_env()
    env_json = json.dumps(network_env["env"], ensure_ascii=False, separators=(",", ":"))
    return f"""
WITH selected_namespace AS (
  SELECT uuid FROM namespaces WHERE name = {sql_literal(LAB_NAMESPACE)} AND user_id IS NULL LIMIT 1
),
updated_server AS (
  UPDATE mcp_servers
  SET
    description = 'Lab-only read-only Context7 documentation MCP server',
    type = 'STDIO',
    command = 'npx',
    args = ARRAY['-y', {sql_literal(PACKAGE_SPEC)}],
    env = {sql_literal(env_json)}::jsonb,
    headers = '{{}}'::jsonb,
    error_status = 'NONE'
  WHERE name = {sql_literal(SERVER_NAME)} AND user_id IS NULL
  RETURNING uuid
),
ensured_server AS (
  INSERT INTO mcp_servers (name, description, type, command, args, env, headers, user_id)
  SELECT
    {sql_literal(SERVER_NAME)},
    'Lab-only read-only Context7 documentation MCP server',
    'STDIO',
    'npx',
    ARRAY['-y', {sql_literal(PACKAGE_SPEC)}],
    {sql_literal(env_json)}::jsonb,
    '{{}}'::jsonb,
    NULL
  WHERE NOT EXISTS (
    SELECT 1 FROM mcp_servers WHERE name = {sql_literal(SERVER_NAME)} AND user_id IS NULL
  )
  RETURNING uuid
),
selected_server AS (
  SELECT uuid FROM updated_server
  UNION ALL
  SELECT uuid FROM ensured_server
  UNION ALL
  SELECT uuid FROM mcp_servers WHERE name = {sql_literal(SERVER_NAME)} AND user_id IS NULL
  LIMIT 1
)
INSERT INTO namespace_server_mappings (namespace_uuid, mcp_server_uuid, status)
SELECT selected_namespace.uuid, selected_server.uuid, 'ACTIVE'
FROM selected_namespace, selected_server
WHERE NOT EXISTS (
  SELECT 1 FROM namespace_server_mappings
  WHERE namespace_uuid = selected_namespace.uuid AND mcp_server_uuid = selected_server.uuid
);
""".strip()


def npm_package_probe() -> dict[str, Any]:
    return run_command(
        [
            docker_path(),
            "exec",
            "metamcp",
            "sh",
            "-lc",
            f"command -v npx && npx --version && npm view {PACKAGE_NAME} version --json",
        ],
        timeout=45,
    )


def install(*, confirm: bool = False) -> dict[str, Any]:
    before = status()
    package_probe = npm_package_probe()
    plan = {
        "schema": "gateway_lab.context7_install.v1",
        "ok": False,
        "generated_at": now_iso(),
        "confirm_required": not confirm,
        "before": {
            "status": before.get("status"),
            "db": before.get("db"),
            "lab_rows": before.get("lab_rows"),
        },
    "package": {
            "name": PACKAGE_NAME,
            "version": PACKAGE_VERSION,
            "spec": PACKAGE_SPEC,
            "probe_ok": bool(package_probe.get("ok")),
        },
        "network": child_network_env(),
        "would_write": {
            "database": POSTGRES_CONTAINER,
            "server": SERVER_NAME,
            "namespace": LAB_NAMESPACE,
            "mapping": f"{LAB_NAMESPACE}->{SERVER_NAME}",
            "command": "npx",
            "args": ["-y", PACKAGE_SPEC],
            "env_keys": sorted(child_network_env()["env"]),
            "scope": "isolated MetaMCP lab database only",
        },
        "boundary": "lab-only read-only Context7 registration; no production Codex MCP config",
    }
    if not package_probe.get("ok"):
        plan["error"] = "package_probe_failed"
        plan["package_probe"] = package_probe
        return plan
    if not confirm:
        return plan
    if before.get("status") == "needs_bootstrap":
        bootstrap_lab(confirm=True)
    result = run_command(
        [docker_path(), "exec", POSTGRES_CONTAINER, "psql", "-U", "metamcp_lab_user", "-d", "metamcp_lab_db", "-v", "ON_ERROR_STOP=1", "-c", install_sql()],
        timeout=30,
    )
    after = status()
    plan.update(
        {
            "ok": bool(result.get("ok")),
            "confirm_required": False,
            "apply_result": result if not result.get("ok") else {"ok": True, "elapsed_ms": result.get("elapsed_ms"), "stdout": result.get("stdout")},
            "after": {
                "status": after.get("status"),
                "db": after.get("db"),
                "lab_rows": after.get("lab_rows"),
                "issues": after.get("issues"),
            },
        }
    )
    return plan


def find_context7_tool(tool_names: list[str], suffix: str) -> str:
    expected = f"{SERVER_NAME}__{suffix}"
    if expected in tool_names:
        return expected
    return next((name for name in tool_names if name.endswith(suffix)), "")


def protocol_smoke() -> dict[str, Any]:
    common_headers = {"Accept": "application/json, text/event-stream"}
    endpoint = f"{PUBLIC_ENDPOINT_URL}/{LAB_ENDPOINT}/mcp"
    initialize = http_request(
        endpoint,
        method="POST",
        body={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "gateway-lab-context7", "version": "0.1.0"},
            },
        },
        headers=common_headers,
        timeout=30,
    )
    session_id = ""
    if initialize.get("ok"):
        session_id = str((initialize.get("headers") or {}).get("mcp-session-id") or "")
    if not session_id:
        return {
            "schema": "gateway_lab.context7_smoke.v1",
            "ok": False,
            "generated_at": now_iso(),
            "initialize": initialize,
            "error": "missing_session_id",
        }

    tools_list = http_request(
        endpoint,
        method="POST",
        body={"jsonrpc": "2.0", "id": 12, "method": "tools/list", "params": {}},
        headers={**common_headers, "mcp-session-id": session_id},
        timeout=120,
        max_text_body=0,
    )
    parsed_tools = sse_message_body(tools_list.get("body"))
    result = parsed_tools.get("result") if isinstance(parsed_tools, dict) else {}
    tool_names = [str(tool.get("name") or "") for tool in (result.get("tools") or []) if isinstance(tool, dict)] if isinstance(result, dict) else []
    resolve_tool = find_context7_tool(tool_names, "resolve-library-id")
    resolve_call: dict[str, Any] = {"ok": False, "error": "resolve_tool_not_found", "tool_names": tool_names}
    if resolve_tool:
        resolve_call = http_request(
            endpoint,
            method="POST",
            body={
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "name": resolve_tool,
                    "arguments": {
                        "libraryName": "React",
                        "query": "React hooks documentation smoke test",
                    },
                },
            },
            headers={**common_headers, "mcp-session-id": session_id},
            timeout=120,
            max_text_body=0,
        )
    http_request(endpoint, method="DELETE", headers={**common_headers, "mcp-session-id": session_id}, timeout=10)
    parsed_resolve = sse_message_body(resolve_call.get("body"))
    resolve_text = json.dumps(parsed_resolve, ensure_ascii=False)
    ok = bool(initialize.get("ok") and tools_list.get("ok") and resolve_call.get("ok") and "react" in resolve_text.lower())
    return {
        "schema": "gateway_lab.context7_smoke.v1",
        "ok": ok,
        "generated_at": now_iso(),
        "endpoint": endpoint,
        "package": {"name": PACKAGE_NAME, "version": PACKAGE_VERSION, "spec": PACKAGE_SPEC},
        "initialize": initialize,
        "tools_list": compact_http_response(tools_list),
        "parsed_tools": parsed_tools,
        "tool_names": tool_names,
        "selected_tool": resolve_tool,
        "resolve_call": compact_http_response(resolve_call),
        "parsed_resolve": parsed_resolve,
        "interpretation": "context7_readonly_call_ok" if ok else "context7_readonly_call_failed",
        "boundary": "read-only Context7 docs lookup through isolated MetaMCP lab endpoint",
    }


def smoke() -> dict[str, Any]:
    current = status()
    installed = any(row.get("name") == SERVER_NAME for row in ((current.get("lab_rows") or {}).get("rows") or []))
    install_result: dict[str, Any] = {"skipped": installed}
    if not installed:
        install_result = install(confirm=True)
    protocol = protocol_smoke()
    return {
        "schema": "gateway_lab.context7_full_smoke.v1",
        "ok": bool(protocol.get("ok")),
        "generated_at": now_iso(),
        "status": status(),
        "install": install_result,
        "protocol": protocol,
    }
