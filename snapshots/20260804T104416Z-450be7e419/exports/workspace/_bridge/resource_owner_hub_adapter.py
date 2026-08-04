#!/usr/bin/env python3
"""Hub-backed read-only owner-tool call helpers for the resource layer.

Ownership: call known Local MCP Hub tools for resource acquisition adapters and
normalize their results for `resource_owner_executor.py`.
Non-goals: current-turn native MCP calls, permission expansion, broad MCP
gateway routing, remote writes, or global network/proxy mutation.
State behavior: read-only HTTP calls to localhost Hub; no persistent writes.
Caller context: resource owner adapters that can use a known Hub tool such as
`github.api` or `chrome_devtools.*` without asking Codex to perform the call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_HUB_URL = "http://127.0.0.1:18881/mcp"
MCP_PROTOCOL_VERSION = "2025-11-25"
BRIDGE_ROOT = Path(__file__).resolve().parent
ROOT = BRIDGE_ROOT.parent
GATEWAY_FALLBACK_ACK = "native-mcp-unavailable-and-original-permissions-apply"
RECOVERABLE_GATEWAY_REASONS = {
    "native_mcp_preferred_no_active_negative_observation",
    "hub_unreachable",
    "hub_tool_error",
    "hub_tool_returned_no_text",
    "TimeoutError",
    "timeout",
    "read_timeout",
    "tool_call_response_missing",
    "gateway_tool_call_failed",
}
RECOVERABLE_GATEWAY_TOKENS = (
    "timeout",
    "timed out",
    "tool_call_response_missing",
    "response_missing",
    "connection reset",
    "connection aborted",
    "temporarily unavailable",
    "transport closed",
)


def _json_result(**payload: Any) -> dict[str, Any]:
    payload.setdefault("schema", "resource_owner_hub_adapter.result.v1")
    payload.setdefault("hub_url", DEFAULT_HUB_URL)
    payload.setdefault("writes_files", False)
    payload.setdefault("writes_remote_state", False)
    payload.setdefault("permission_boundary", "local_hub_original_tool_permissions")
    return payload


def gateway_failure_reason(payload: dict[str, Any]) -> str:
    """Return the most actionable Hub/gateway failure reason."""

    for key in ("reason", "gateway_status", "error_class", "status"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    for key in ("reason", "gateway_status", "error_class", "status"):
        value = str(result.get(key) or "").strip()
        if value:
            return value
    error = str(payload.get("error") or "").strip()
    return error or "hub_gateway_unknown_failure"


def gateway_failure_is_recoverable(payload: dict[str, Any]) -> bool:
    """Classify failures where same-boundary fresh-stdio fallback is valid."""

    reason = gateway_failure_reason(payload)
    if reason in RECOVERABLE_GATEWAY_REASONS:
        return True
    haystack_parts = [
        reason,
        str(payload.get("error") or ""),
        str(payload.get("gateway_status") or ""),
    ]
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    haystack_parts.extend(
        [
            str(result.get("reason") or ""),
            str(result.get("error") or ""),
            str(result.get("gateway_status") or ""),
        ]
    )
    haystack = " ".join(haystack_parts).lower()
    return any(token in haystack for token in RECOVERABLE_GATEWAY_TOKENS)


def _post_json_rpc(method: str, params: dict[str, Any], *, timeout: int, request_id: int) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }
    request = urllib.request.Request(DEFAULT_HUB_URL, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=max(1, min(timeout, 120))) as response:
        body = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(body or "{}")
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def call_hub_tool(tool: str, arguments: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
    """Call one known Local MCP Hub tool and return parsed tool JSON."""

    try:
        initialized = _post_json_rpc(
            "initialize",
            {"protocolVersion": MCP_PROTOCOL_VERSION},
            timeout=min(timeout, 10),
            request_id=1,
        )
        if initialized.get("error"):
            return _json_result(ok=False, reason="hub_initialize_error", hub_error=initialized.get("error"))
        response = _post_json_rpc(
            "tools/call",
            {"name": tool, "arguments": arguments},
            timeout=timeout,
            request_id=2,
        )
    except urllib.error.URLError as exc:
        return _json_result(ok=False, reason="hub_unreachable", error=str(exc))
    except Exception as exc:
        return _json_result(ok=False, reason=type(exc).__name__, error=str(exc))

    if response.get("error"):
        return _json_result(ok=False, reason="hub_tool_error", hub_error=response.get("error"), tool=tool)
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    content = result.get("content") if isinstance(result.get("content"), list) else []
    text = ""
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = str(item.get("text") or "")
            break
    if not text:
        return _json_result(ok=False, reason="hub_tool_returned_no_text", tool=tool, raw_result=result)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return _json_result(ok=False, reason=f"hub_tool_json_decode_failed: {exc}", tool=tool, text_tail=text[-1000:])
    if not isinstance(payload, dict):
        return _json_result(ok=False, reason="hub_tool_json_root_not_object", tool=tool)
    payload.setdefault("ok", bool(payload.get("ok")))
    payload.setdefault("hub_tool", tool)
    payload.setdefault("hub_transport", "local_http_mcp_hub")
    payload.setdefault("permission_boundary", "local_hub_original_tool_permissions")
    return payload


def hidden_creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _run_gateway_cli(profile: str, tool: str, arguments: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    command = [
        sys.executable,
        str(BRIDGE_ROOT / "mcp_session_doctor.py"),
        "gateway-call",
        "--profile",
        profile,
        "--tool",
        tool,
        "--arguments-json",
        json.dumps(arguments, ensure_ascii=False),
        "--timeout-seconds",
        str(max(1, min(timeout, 120))),
    ]
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, min(timeout + 15, 150)),
            creationflags=hidden_creationflags(),
        )
    except Exception as exc:
        return _json_result(ok=False, reason=type(exc).__name__, error=str(exc), profile=profile, tool=tool, route="local_gateway_cli")
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        payload = {"ok": False, "reason": f"gateway_cli_json_decode_failed: {exc}", "stdout_tail": (proc.stdout or "")[-1000:]}
    if isinstance(payload, dict):
        payload.setdefault("ok", proc.returncode == 0)
        payload.setdefault("returncode", proc.returncode)
        payload.setdefault("route", "local_gateway_cli")
        payload.setdefault("profile", profile)
        payload.setdefault("tool", tool)
        if proc.stderr:
            payload.setdefault("stderr", proc.stderr[-2000:])
        return payload
    return {"ok": proc.returncode == 0, "result": payload, "route": "local_gateway_cli", "profile": profile, "tool": tool}


def _mcp_text_items(payload: dict[str, Any]) -> list[str]:
    candidates: list[Any] = [
        payload.get("content"),
        (payload.get("result") or {}).get("content") if isinstance(payload.get("result"), dict) else None,
    ]
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    nested = result.get("result") if isinstance(result.get("result"), dict) else {}
    candidates.extend(
        [
            nested.get("content"),
            (nested.get("result") or {}).get("content") if isinstance(nested.get("result"), dict) else None,
        ]
    )
    texts: list[str] = []
    for content in candidates:
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
        if texts:
            return texts
    return texts


def mcp_text_content(payload: dict[str, Any], *, max_chars: int = 8000) -> str:
    text = "\n\n".join(item for item in _mcp_text_items(payload) if item)
    return text[:max_chars]


def mcp_json_content(payload: dict[str, Any]) -> dict[str, Any]:
    text = mcp_text_content(payload, max_chars=2_000_000).strip()
    if not text or text[:1] not in "{[":
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {"items": parsed}


def call_mcp_gateway_tool(
    profile: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    timeout: int = 30,
    allow_local_cli_fallback: bool = True,
) -> dict[str, Any]:
    """Call an owner MCP through Hub, with explicit same-boundary CLI fallback.

    Hub remains first. The local gateway CLI is used only when Hub is unreachable
    or returns a policy/transport refusal that prevents autonomous resource-layer
    execution. The fallback is still the governed fresh-stdio gateway and does
    not expand the target MCP's permissions.
    """

    hub_payload = call_hub_tool(
        "mcp_gateway.call",
        {
            "profile": profile,
            "tool": tool,
            "arguments": arguments,
            "timeout_seconds": max(1, min(timeout, 120)),
            "fallback_ack": GATEWAY_FALLBACK_ACK,
        },
        timeout=timeout,
    )
    hub_payload.setdefault("owner_profile", profile)
    hub_payload.setdefault("owner_tool", tool)
    if hub_payload.get("ok"):
        hub_payload.setdefault("owner_execution_route", "hub_mcp_gateway_call")
        return hub_payload
    reason = gateway_failure_reason(hub_payload)
    if not allow_local_cli_fallback or not gateway_failure_is_recoverable(hub_payload):
        hub_payload.setdefault("owner_execution_route", "hub_mcp_gateway_call_failed")
        hub_payload.setdefault("normalized_reason", reason)
        hub_payload.setdefault("recoverable_by_same_boundary_fallback", False)
        return hub_payload
    cli_payload = _run_gateway_cli(profile, tool, arguments, timeout=timeout)
    cli_payload["owner_execution_route"] = "local_gateway_cli_after_hub_attempt"
    cli_payload["hub_attempt"] = {
        "ok": bool(hub_payload.get("ok")),
        "reason": reason,
        "normalized_reason": reason,
        "recoverable_by_same_boundary_fallback": True,
        "route": hub_payload.get("route", {}),
        "policy": "Hub was attempted first; local CLI fallback preserves the same MCP permission boundary.",
    }
    cli_payload.setdefault("permission_boundary", "owner_read_only_fresh_stdio_gateway")
    return cli_payload


def validate() -> dict[str, Any]:
    catalog = call_hub_tool("hub.catalog", {}, timeout=10)
    gateway_shape = call_mcp_gateway_tool(
        "microsoftdocs",
        "microsoft_docs_search",
        {"query": "Windows proxy settings", "top": 1},
        timeout=20,
        allow_local_cli_fallback=False,
    )
    return {
        "schema": "resource_owner_hub_adapter.validate.v1",
        "ok": bool(catalog.get("ok")) and bool(gateway_shape.get("ok")),
        "hub_url": DEFAULT_HUB_URL,
        "catalog_count": ((catalog.get("counts") or {}).get("total") if isinstance(catalog.get("counts"), dict) else None),
        "gateway_call_ok": bool(gateway_shape.get("ok")),
        "gateway_call_route": gateway_shape.get("owner_execution_route", "hub_mcp_gateway_call"),
        "writes_files": False,
        "writes_remote_state": False,
    }
