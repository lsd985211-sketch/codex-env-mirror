#!/usr/bin/env python3
"""MetaMCP-specific helpers for the isolated gateway lab.

Ownership: MetaMCP lab status, lab-only bootstrap, and protocol smoke helpers.
Non-goals: no Codex config edits, no MCP registration, no system proxy/DNS
mutation, no startup integration, and no production database writes.
State behavior: reads Docker/HTTP state by default; bootstrap writes only the
isolated MetaMCP lab database inside Docker when explicitly confirmed.
Caller context: imported by `_bridge/gateway_lab.py` as a thin CLI facade.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from shared.json_cli import now_iso


APP_CONTAINER = "metamcp"
POSTGRES_CONTAINER = "metamcp-pg"
FRONTEND_URL = "http://127.0.0.1:12008"
PUBLIC_ENDPOINT_URL = f"{FRONTEND_URL}/metamcp"
LAB_NAMESPACE = "Lab"
LAB_ENDPOINT = "lab-public"
LAB_SERVER = "lab_echo"


def docker_path() -> str:
    path = shutil.which("docker")
    if path:
        return path
    user_docker = Path.home() / "AppData" / "Local" / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"
    return str(user_docker) if user_docker.exists() else "docker"


def run_command(command: list[str], *, timeout: int = 30) -> dict[str, Any]:
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "elapsed_ms": int((time.time() - started) * 1000),
            "stdout": completed.stdout[-6000:],
            "stderr": completed.stderr[-6000:],
            "command": command,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "elapsed_ms": int((time.time() - started) * 1000),
            "stdout": (exc.stdout or "")[-6000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-6000:] if isinstance(exc.stderr, str) else "",
            "error": "timeout",
            "command": command,
        }


def http_request(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 8,
    max_text_body: int = 1200,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = Request(url, data=data, method=method, headers=request_headers)
    started = time.time()
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost-only lab request.
            raw = response.read().decode("utf-8", errors="replace")
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            try:
                parsed: Any = json.loads(raw)
            except json.JSONDecodeError:
                content_type = str(response_headers.get("content-type") or "")
                if "text/event-stream" in content_type:
                    parsed = raw if max_text_body <= 0 else raw[:max_text_body]
                else:
                    parsed = raw[:max_text_body]
            return {
                "ok": 200 <= response.status < 400,
                "status": response.status,
                "elapsed_ms": int((time.time() - started) * 1000),
                "headers": response_headers,
                "body": parsed,
            }
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": raw[:1200] or str(exc),
        }
    except URLError as exc:
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": str(exc),
        }
    except TimeoutError as exc:
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": f"timeout: {exc}",
        }
    except socket.timeout as exc:
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": f"socket_timeout: {exc}",
        }


def inspect_containers() -> dict[str, Any]:
    containers = {}
    errors = []
    template = "{{json .State}}\t{{json .NetworkSettings.Ports}}\t{{.Config.Image}}\t{{.Id}}"
    for name in (APP_CONTAINER, POSTGRES_CONTAINER):
        result = run_command([docker_path(), "inspect", "--format", template, name], timeout=20)
        if not result.get("ok"):
            errors.append({"container": name, "result": result})
            continue
        parts = str(result.get("stdout") or "").strip().split("\t")
        if len(parts) != 4:
            errors.append({"container": name, "error": "unexpected_inspect_format", "stdout": result.get("stdout")})
            continue
        try:
            state = json.loads(parts[0])
            ports = json.loads(parts[1])
        except json.JSONDecodeError as exc:
            errors.append({"container": name, "error": f"inspect_json_error: {exc}"})
            continue
        containers[name] = {
            "id": parts[3][:12],
            "image": parts[2],
            "running": bool(state.get("Running")),
            "status": state.get("Status"),
            "health": (state.get("Health") or {}).get("Status", ""),
            "ports": ports or {},
        }
    return {"ok": not errors, "containers": containers, "errors": errors}


def port_bindings(containers: dict[str, Any]) -> list[dict[str, Any]]:
    checks = [
        {"container": APP_CONTAINER, "container_port": "12008/tcp", "expected_host_port": "12008"},
        {"container": POSTGRES_CONTAINER, "container_port": "5432/tcp", "expected_host_port": "9433"},
    ]
    bindings = []
    for check in checks:
        container = containers.get(check["container"]) or {}
        rows = (container.get("ports") or {}).get(check["container_port"]) or []
        host_ips = sorted({str(row.get("HostIp") or "") for row in rows})
        host_ports = sorted({str(row.get("HostPort") or "") for row in rows})
        localhost_only = bool(rows) and all(ip in {"127.0.0.1", "::1"} for ip in host_ips)
        bindings.append(
            {
                **check,
                "bound": bool(rows),
                "host_ips": host_ips,
                "host_ports": host_ports,
                "localhost_only": localhost_only,
                "ok": bool(rows) and localhost_only and check["expected_host_port"] in host_ports,
            }
        )
    return bindings


def db_counts() -> dict[str, Any]:
    sql = (
        "select 'users', count(*) from users "
        "union all select 'namespaces', count(*) from namespaces "
        "union all select 'endpoints', count(*) from endpoints "
        "union all select 'mcp_servers', count(*) from mcp_servers "
        "union all select 'namespace_server_mappings', count(*) from namespace_server_mappings;"
    )
    result = run_command(
        [docker_path(), "exec", POSTGRES_CONTAINER, "psql", "-U", "metamcp_lab_user", "-d", "metamcp_lab_db", "-At", "-F", "\t", "-c", sql],
        timeout=20,
    )
    counts: dict[str, int] = {}
    if result.get("ok"):
        for line in str(result.get("stdout") or "").splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[1].isdigit():
                counts[parts[0]] = int(parts[1])
    return {"ok": bool(result.get("ok")), "counts": counts, "result": result if not result.get("ok") else {"elapsed_ms": result.get("elapsed_ms")}}


def lab_rows() -> dict[str, Any]:
    sql = (
        "select 'namespace', name, uuid::text from namespaces where name = 'Lab' and user_id is null "
        "union all select 'endpoint', name, uuid::text from endpoints where name = 'lab-public' "
        "union all "
        "select 'server', s.name, s.uuid::text "
        "from mcp_servers s "
        "join namespace_server_mappings m on m.mcp_server_uuid = s.uuid "
        "join namespaces n on n.uuid = m.namespace_uuid "
        "where n.name = 'Lab' and n.user_id is null "
        "order by 1, 2;"
    )
    result = run_command(
        [docker_path(), "exec", POSTGRES_CONTAINER, "psql", "-U", "metamcp_lab_user", "-d", "metamcp_lab_db", "-At", "-F", "\t", "-c", sql],
        timeout=20,
    )
    rows = []
    if result.get("ok"):
        for line in str(result.get("stdout") or "").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                rows.append({"kind": parts[0], "name": parts[1], "uuid": parts[2]})
    return {"ok": bool(result.get("ok")), "rows": rows}


def recent_log_signals() -> dict[str, Any]:
    result = run_command([docker_path(), "logs", "--tail", "180", APP_CONTAINER], timeout=20)
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    signals = {
        "backend_started": "Backend running on port 12009" in text or "Server is running on port 12009" in text,
        "frontend_ready": "Ready in" in text or "Frontend running on port 12008" in text,
        "bootstrap_seen": "Initializing environment-based configuration" in text,
        "no_namespaces_seen": "No namespaces found in database" in text,
        "fatal_seen": "Fatal startup error" in text,
    }
    return {"ok": bool(result.get("ok")), "signals": signals}


def status() -> dict[str, Any]:
    inspected = inspect_containers()
    containers = inspected.get("containers") or {}
    bindings = port_bindings(containers)
    frontend = http_request(FRONTEND_URL)
    public_list = http_request(PUBLIC_ENDPOINT_URL)
    public_health = http_request(f"{PUBLIC_ENDPOINT_URL}/health")
    counts = db_counts()
    logs = recent_log_signals()
    issues: list[dict[str, Any]] = []
    for name in (APP_CONTAINER, POSTGRES_CONTAINER):
        container = containers.get(name) or {}
        if not container.get("running"):
            issues.append({"severity": "blocker", "code": f"{name}_not_running"})
        elif container.get("health") not in {"", "healthy"}:
            issues.append({"severity": "risk", "code": f"{name}_not_healthy", "health": container.get("health")})
    for binding in bindings:
        if not binding["ok"]:
            issues.append({"severity": "blocker", "code": "unsafe_or_missing_port_binding", "binding": binding})
    if not frontend.get("ok"):
        issues.append({"severity": "blocker", "code": "frontend_http_unavailable", "http": frontend})
    endpoint_count = 0
    if isinstance(public_list.get("body"), dict):
        endpoint_count = len(public_list["body"].get("endpoints") or [])
    if endpoint_count == 0:
        issues.append({"severity": "risk", "code": "no_public_endpoints", "message": "MetaMCP is running, but no lab endpoint is available yet."})
    status_value = "blocked" if any(item["severity"] == "blocker" for item in issues) else ("needs_bootstrap" if issues else "usable")
    return {
        "schema": "gateway_lab.metamcp_status.v1",
        "ok": status_value != "blocked",
        "generated_at": now_iso(),
        "status": status_value,
        "containers": containers,
        "port_bindings": bindings,
        "http": {
            "frontend": {"ok": frontend.get("ok"), "status": frontend.get("status"), "elapsed_ms": frontend.get("elapsed_ms")},
            "public_endpoints": public_list,
            "public_health": public_health,
        },
        "db": counts,
        "lab_rows": lab_rows(),
        "logs": logs,
        "issues": issues,
        "boundary": "read-only MetaMCP lab status; no production integration",
    }


def lab_bootstrap_sql() -> str:
    return f"""
WITH ensured_namespace AS (
  INSERT INTO namespaces (name, description, user_id)
  SELECT '{LAB_NAMESPACE}', 'Isolated Codex gateway lab', NULL
  WHERE NOT EXISTS (
    SELECT 1 FROM namespaces WHERE name = '{LAB_NAMESPACE}' AND user_id IS NULL
  )
  RETURNING uuid
),
selected_namespace AS (
  SELECT uuid FROM ensured_namespace
  UNION ALL
  SELECT uuid FROM namespaces WHERE name = '{LAB_NAMESPACE}' AND user_id IS NULL
  LIMIT 1
)
INSERT INTO endpoints (
  name, description, namespace_uuid, enable_api_key_auth, enable_oauth,
  use_query_param_auth, user_id
)
SELECT
  '{LAB_ENDPOINT}', 'Lab endpoint', uuid, false, false, false, NULL
FROM selected_namespace
WHERE NOT EXISTS (SELECT 1 FROM endpoints WHERE name = '{LAB_ENDPOINT}');
""".strip()


def echo_server_js() -> str:
    return r"""
const readline=require('readline');
const rl=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
function send(id,result,error){const payload={jsonrpc:'2.0',id}; if(error){payload.error=error}else{payload.result=result||{}}; process.stdout.write(JSON.stringify(payload)+'\n')}
rl.on('line',line=>{let m; try{m=JSON.parse(line)}catch{return} const id=m.id; if(id===undefined||id===null)return; const method=m.method||''; if(method==='initialize'){send(id,{protocolVersion:'2024-11-05',capabilities:{tools:{}},serverInfo:{name:'gateway-lab-echo',version:'0.1.0'}}); return} if(method==='tools/list'){send(id,{tools:[{name:'echo',description:'Return the provided text. Lab-only MetaMCP smoke tool.',inputSchema:{type:'object',properties:{text:{type:'string'}},required:['text']}}]}); return} if(method==='tools/call'){const args=(m.params&&m.params.arguments)||{}; send(id,{content:[{type:'text',text:String(args.text||'')}]}); return} send(id,null,{code:-32601,message:'unknown method: '+method})});
""".strip()


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def install_echo_sql() -> str:
    js = echo_server_js()
    return f"""
WITH selected_namespace AS (
  SELECT uuid FROM namespaces WHERE name = {sql_literal(LAB_NAMESPACE)} AND user_id IS NULL LIMIT 1
),
updated_server AS (
  UPDATE mcp_servers
  SET
    description = 'Lab-only echo MCP server for MetaMCP protocol smoke',
    type = 'STDIO',
    command = 'node',
    args = ARRAY['-e', {sql_literal(js)}],
    env = '{{}}'::jsonb,
    headers = '{{}}'::jsonb,
    error_status = 'NONE'
  WHERE name = {sql_literal(LAB_SERVER)} AND user_id IS NULL
  RETURNING uuid
),
ensured_server AS (
  INSERT INTO mcp_servers (name, description, type, command, args, env, headers, user_id)
  SELECT
    {sql_literal(LAB_SERVER)},
    'Lab-only echo MCP server for MetaMCP protocol smoke',
    'STDIO',
    'node',
    ARRAY['-e', {sql_literal(js)}],
    '{{}}'::jsonb,
    '{{}}'::jsonb,
    NULL
  WHERE NOT EXISTS (
    SELECT 1 FROM mcp_servers WHERE name = {sql_literal(LAB_SERVER)} AND user_id IS NULL
  )
  RETURNING uuid
),
selected_server AS (
  SELECT uuid FROM updated_server
  UNION ALL
  SELECT uuid FROM ensured_server
  UNION ALL
  SELECT uuid FROM mcp_servers WHERE name = {sql_literal(LAB_SERVER)} AND user_id IS NULL
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


def bootstrap_lab(*, confirm: bool = False) -> dict[str, Any]:
    before = status()
    plan = {
        "schema": "gateway_lab.metamcp_bootstrap_lab.v1",
        "ok": False,
        "generated_at": now_iso(),
        "confirm_required": not confirm,
        "before": {
            "status": before.get("status"),
            "db": before.get("db"),
            "issues": before.get("issues"),
        },
        "would_write": {
            "database": POSTGRES_CONTAINER,
            "namespace": LAB_NAMESPACE,
            "endpoint": LAB_ENDPOINT,
            "scope": "isolated MetaMCP lab database only",
        },
        "boundary": "lab-only database bootstrap; no Codex config, no startup, no system proxy/DNS, no production database",
    }
    if not confirm:
        return plan
    result = run_command(
        [docker_path(), "exec", POSTGRES_CONTAINER, "psql", "-U", "metamcp_lab_user", "-d", "metamcp_lab_db", "-v", "ON_ERROR_STOP=1", "-c", lab_bootstrap_sql()],
        timeout=30,
    )
    after = status()
    plan.update(
        {
            "ok": bool(result.get("ok")) and after.get("status") in {"usable", "needs_bootstrap"},
            "confirm_required": False,
            "apply_result": result if not result.get("ok") else {"ok": True, "elapsed_ms": result.get("elapsed_ms"), "stdout": result.get("stdout")},
            "after": {
                "status": after.get("status"),
                "db": after.get("db"),
                "endpoint_count": len(((after.get("http") or {}).get("public_endpoints") or {}).get("body", {}).get("endpoints", []) if isinstance(((after.get("http") or {}).get("public_endpoints") or {}).get("body"), dict) else []),
                "issues": after.get("issues"),
            },
        }
    )
    return plan


def install_echo_server(*, confirm: bool = False) -> dict[str, Any]:
    before = status()
    plan = {
        "schema": "gateway_lab.metamcp_install_echo.v1",
        "ok": False,
        "generated_at": now_iso(),
        "confirm_required": not confirm,
        "before": {
            "status": before.get("status"),
            "db": before.get("db"),
            "lab_rows": before.get("lab_rows"),
        },
        "would_write": {
            "database": POSTGRES_CONTAINER,
            "server": LAB_SERVER,
            "namespace": LAB_NAMESPACE,
            "mapping": f"{LAB_NAMESPACE}->{LAB_SERVER}",
            "scope": "isolated MetaMCP lab database only",
        },
        "boundary": "lab-only MCP server registration; no Codex config, no host command execution, no production database",
    }
    if not confirm:
        return plan
    if before.get("status") == "needs_bootstrap":
        bootstrap_lab(confirm=True)
    result = run_command(
        [docker_path(), "exec", POSTGRES_CONTAINER, "psql", "-U", "metamcp_lab_user", "-d", "metamcp_lab_db", "-v", "ON_ERROR_STOP=1", "-c", install_echo_sql()],
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


def compact_http_response(response: dict[str, Any], *, body_preview_chars: int = 1200) -> dict[str, Any]:
    compact = dict(response)
    body = compact.get("body")
    if isinstance(body, str) and len(body) > body_preview_chars:
        compact["body_preview"] = body[:body_preview_chars]
        compact["body_truncated"] = True
        compact["body_length"] = len(body)
        compact["body"] = compact["body_preview"]
    return compact


def sse_message_body(response_body: Any) -> dict[str, Any]:
    if isinstance(response_body, dict):
        return response_body
    text = str(response_body or "")
    events: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                events.append(current)
                current = []
            continue
        if line.startswith("data:"):
            current.append(line.split(":", 1)[1].lstrip())
    if current:
        events.append(current)
    for event_lines in events:
        data = "\n".join(event_lines).strip()
        if not data:
            continue
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def protocol_smoke() -> dict[str, Any]:
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "gateway-lab", "version": "0.1.0"},
        },
    }
    common_headers = {"Accept": "application/json, text/event-stream"}
    initialize = http_request(f"{PUBLIC_ENDPOINT_URL}/{LAB_ENDPOINT}/mcp", method="POST", body=init_body, headers=common_headers)
    session_id = ""
    if initialize.get("ok"):
        session_id = str((initialize.get("headers") or {}).get("mcp-session-id") or "")
    tools_list: dict[str, Any] = {"ok": False, "error": "no_session_id"}
    if session_id:
        tools_list = http_request(
            f"{PUBLIC_ENDPOINT_URL}/{LAB_ENDPOINT}/mcp",
            method="POST",
            body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={**common_headers, "mcp-session-id": session_id},
            timeout=120,
            max_text_body=0,
        )
        parsed_tools = sse_message_body(tools_list.get("body"))
        tool_names = []
        result = parsed_tools.get("result") if isinstance(parsed_tools, dict) else {}
        if isinstance(result, dict):
            tool_names = [str(tool.get("name") or "") for tool in result.get("tools") or [] if isinstance(tool, dict)]
        selected_tool = next((name for name in tool_names if name.endswith("__echo") or name == "echo"), "")
        tool_call: dict[str, Any] = {"ok": False, "error": "no_echo_tool", "tool_names": tool_names}
        if selected_tool:
            tool_call = http_request(
                f"{PUBLIC_ENDPOINT_URL}/{LAB_ENDPOINT}/mcp",
                method="POST",
                body={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": selected_tool, "arguments": {"text": "gateway-lab-ok"}},
                },
                headers={**common_headers, "mcp-session-id": session_id},
                timeout=30,
                max_text_body=0,
            )
        http_request(f"{PUBLIC_ENDPOINT_URL}/{LAB_ENDPOINT}/mcp", method="DELETE", headers={**common_headers, "mcp-session-id": session_id})
    else:
        parsed_tools = {}
        tool_names = []
        tool_call = {"ok": False, "error": "no_session_id"}
    tool_count = len(tool_names)
    parsed_call = sse_message_body(tool_call.get("body"))
    call_text = json.dumps(parsed_call, ensure_ascii=False)
    return {
        "schema": "gateway_lab.metamcp_protocol_smoke.v1",
        "ok": bool(initialize.get("ok") and tools_list.get("ok") and (tool_count == 0 or "gateway-lab-ok" in call_text)),
        "generated_at": now_iso(),
        "endpoint": f"{PUBLIC_ENDPOINT_URL}/{LAB_ENDPOINT}/mcp",
        "initialize": initialize,
        "tools_list": compact_http_response(tools_list),
        "parsed_tools": parsed_tools,
        "tool_call": compact_http_response(tool_call),
        "parsed_call": parsed_call,
        "tool_count": tool_count,
        "interpretation": "protocol_ok_empty_namespace" if tool_count == 0 else "protocol_ok_with_tools",
        "boundary": "localhost MetaMCP lab protocol smoke only",
    }


def smoke() -> dict[str, Any]:
    current = status()
    bootstrap = {"skipped": current.get("status") != "needs_bootstrap"}
    if current.get("status") == "needs_bootstrap":
        bootstrap = bootstrap_lab(confirm=True)
        current = status()
    protocol = protocol_smoke() if current.get("status") in {"usable", "needs_bootstrap"} else {"ok": False, "error": "status_blocked"}
    ok = bool(current.get("ok") and protocol.get("ok"))
    next_steps = [
        "Use this lab route to evaluate real candidate tools before production integration.",
        "Keep production Codex MCP config unchanged until a specific gateway route has repeated smoke evidence.",
    ]
    if ((protocol.get("protocol") or protocol) if isinstance(protocol, dict) else {}).get("tool_count") == 0:
        next_steps = [
            "Run `python _bridge\\gateway_lab.py metamcp-install-echo --confirm` or register another container-native lab MCP server.",
            "Keep production Codex MCP config unchanged until the lab has tool-list and tool-call evidence.",
        ]
    return {
        "schema": "gateway_lab.metamcp_smoke.v1",
        "ok": ok,
        "generated_at": now_iso(),
        "status": current,
        "bootstrap": bootstrap,
        "protocol": protocol,
        "next": next_steps,
    }
