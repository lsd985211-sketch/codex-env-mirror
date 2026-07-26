#!/usr/bin/env python3
"""Local stdio fallback client for the mobile OpenClaw MCP server.

Owns: launching a fresh mobile MCP stdio process and calling supplement
get/ack tools when the active Codex session's native MCP transport is unusable.
Non-goals: implementing MCP tools, changing supplement semantics, queue
mutation outside the MCP server, or bypassing MCP permission boundaries.
State behavior: starts a short-lived child process and returns the tool result;
durable state changes, if any, are owned by the MCP server implementation.
Normal callers: mobile_openclaw_cli supplement fallback commands and tool
health checks.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]


def short_value(value: Any, limit: int = 160) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def mobile_mcp_stdio_tool_call(
    config: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    timeout_seconds: int = 8,
) -> dict[str, Any]:
    """Call the mobile MCP server through a fresh local stdio process."""
    command = str(config.get("mcp", {}).get("mobile_openclaw_command") or sys.executable)
    script = Path(
        config.get("mcp", {}).get("mobile_openclaw_script")
        or ROOT / "mobile_bridge_mcp_server.py"
    )
    config_path = Path(str(config.get("_config_path") or ROOT / "config.local.json"))
    result: dict[str, Any] = {
        "ok": False,
        "fallback": "local_stdio_mcp",
        "tool_name": tool_name,
        "command": command,
        "script": str(script),
        "script_exists": script.exists(),
        "config": str(config_path),
    }
    if not tool_name:
        result["reason"] = "tool_name_required"
        return result
    if not script.exists():
        result["reason"] = "mobile_mcp_script_missing"
        return result

    cmd = [command, str(script), "--config", str(config_path)]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        result["reason"] = f"spawn_failed: {exc}"
        return result

    responses: list[dict[str, Any]] = []

    def read_response(deadline: float) -> dict[str, Any]:
        assert proc.stdout is not None
        while True:
            if time.time() > deadline:
                raise TimeoutError("timed out waiting for local MCP fallback response")
            line = proc.stdout.readline()
            if line:
                try:
                    parsed = json.loads(line)
                except Exception:
                    return {"raw": short_value(line, 1000)}
                return parsed if isinstance(parsed, dict) else {"raw": parsed}
            if proc.poll() is not None:
                raise RuntimeError(f"local MCP fallback exited early rc={proc.returncode}")
            time.sleep(0.05)

    try:
        assert proc.stdin is not None
        deadline = time.time() + max(1, int(timeout_seconds))
        init_request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        call_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        }
        for request in (init_request, call_request):
            proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            responses.append(read_response(deadline))
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "exit", "params": {}}) + "\n")
        proc.stdin.flush()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    except Exception as exc:
        result["reason"] = str(exc)
        try:
            proc.kill()
        except Exception:
            pass
    stderr = ""
    try:
        if proc.stderr is not None:
            stderr = proc.stderr.read()
    except Exception:
        stderr = ""
    result["returncode"] = proc.returncode
    result["stderr"] = short_value(stderr, 1000)
    result["responses"] = responses
    if len(responses) < 2:
        result.setdefault("reason", "missing_tools_call_response")
        return result
    response = responses[1]
    if response.get("error"):
        result["reason"] = "mcp_tool_error"
        result["error"] = response.get("error")
        return result
    call_result = response.get("result")
    if not isinstance(call_result, dict):
        result["reason"] = "invalid_mcp_tool_result"
        result["raw_result"] = call_result
        return result
    payload: dict[str, Any] = {}
    content = call_result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            raw_text = str(first.get("text") or "")
            try:
                parsed_payload = json.loads(raw_text)
                payload = parsed_payload if isinstance(parsed_payload, dict) else {"raw": parsed_payload}
            except Exception:
                payload = {"raw_text": raw_text}
    result["is_error"] = bool(call_result.get("isError"))
    result["tool_result"] = payload
    result["ok"] = not bool(call_result.get("isError")) and bool(payload.get("ok", True))
    if not result["ok"]:
        result["reason"] = str(payload.get("reason") or "tool_result_not_ok")
    return result


def supplement_fallback_get_pending_batch(
    config: dict[str, Any],
    thread_id: str,
    timeout_seconds: int = 8,
) -> dict[str, Any]:
    thread_id = str(thread_id or "").strip()
    if not thread_id:
        return {"ok": False, "reason": "thread_id_required"}
    result = mobile_mcp_stdio_tool_call(
        config,
        "bridge.get_pending_batch",
        {"thread_id": thread_id},
        timeout_seconds=timeout_seconds,
    )
    result["thread_id"] = thread_id
    result["purpose"] = "supplement_get_pending_batch_fallback"
    return result


def supplement_fallback_ack_message(
    config: dict[str, Any],
    thread_id: str,
    message_id: str,
    timeout_seconds: int = 8,
) -> dict[str, Any]:
    thread_id = str(thread_id or "").strip()
    message_id = str(message_id or "").strip()
    if not thread_id:
        return {"ok": False, "reason": "thread_id_required"}
    if not message_id:
        return {"ok": False, "reason": "message_id_required"}
    result = mobile_mcp_stdio_tool_call(
        config,
        "bridge.ack_message",
        {"thread_id": thread_id, "message_id": message_id},
        timeout_seconds=timeout_seconds,
    )
    result["thread_id"] = thread_id
    result["message_id"] = message_id
    result["purpose"] = "supplement_ack_message_fallback"
    return result
