#!/usr/bin/env python3
"""One-shot stdio client for locally managed MCP implementations.

Ownership: one fresh MCP initialize/list/call lifecycle, bounded subprocess
cleanup, UTF-8 transport, and read-only/allowlist forwarding checks.
Non-goals: selecting a business tool, granting permissions, persistent child
processes, package installation, or target-specific path policy.
State behavior: starts one child per call and always closes or terminates it.
Caller context: purpose-owned Hub adapters supply the command, cwd, environment,
and explicit tool allowlist for their own permission boundary.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


MAX_TIMEOUT_SECONDS = 120


def _pump_lines(stream: Any, destination: queue.Queue[str] | list[str]) -> None:
    try:
        for line in iter(stream.readline, ""):
            if isinstance(destination, queue.Queue):
                destination.put(line)
            else:
                destination.append(line)
                if len(destination) > 40:
                    del destination[:-40]
    finally:
        if isinstance(destination, queue.Queue):
            destination.put("")


def _send(proc: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise RuntimeError("mcp_stdin_unavailable")
    proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def _response_for(
    lines: queue.Queue[str],
    request_id: int,
    timeout_seconds: int,
    ignored: list[str],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("mcp_response_timeout")
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("mcp_response_timeout") from exc
        if not line:
            raise RuntimeError("mcp_process_closed_before_response")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            ignored.append(line.strip()[:300])
            continue
        if payload.get("id") == request_id:
            return payload if isinstance(payload, dict) else {}
        ignored.append(line.strip()[:300])


def fresh_stdio_call(
    *,
    command: list[str],
    working_directory: Path | str | None,
    tool: str = "",
    arguments: dict[str, Any] | None = None,
    timeout_seconds: int = 45,
    allowed_tools: set[str] | None = None,
    env_overrides: dict[str, str] | None = None,
    client_name: str = "local-mcp-hub",
) -> dict[str, Any]:
    """Run one MCP request and reject non-read-only tools unless allowlisted."""

    timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))
    env = dict(os.environ)
    env.update({"NO_COLOR": "1", "NPM_CONFIG_UPDATE_NOTIFIER": "false"})
    env.update({str(key): str(value) for key, value in (env_overrides or {}).items()})
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(working_directory) if working_directory else None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
    except OSError as exc:
        return {"ok": False, "reason": type(exc).__name__, "detail": str(exc), "stderr_tail": ""}

    stdout_lines: queue.Queue[str] = queue.Queue()
    stderr_lines: list[str] = []
    threading.Thread(target=_pump_lines, args=(proc.stdout, stdout_lines), daemon=True).start()
    threading.Thread(target=_pump_lines, args=(proc.stderr, stderr_lines), daemon=True).start()
    ignored: list[str] = []
    try:
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": client_name, "version": "1"},
                },
            },
        )
        initialized = _response_for(stdout_lines, 1, timeout, ignored)
        if "error" in initialized:
            return {"ok": False, "reason": "mcp_initialize_error", "error": initialized["error"], "stderr_tail": "".join(stderr_lines)[-1000:]}
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = _response_for(stdout_lines, 2, timeout, ignored)
        tools = (listed.get("result") or {}).get("tools") if isinstance(listed.get("result"), dict) else None
        if not isinstance(tools, list):
            return {"ok": False, "reason": "mcp_tools_list_invalid", "stderr_tail": "".join(stderr_lines)[-1000:]}
        if not tool:
            return {"ok": True, "tools": tools, "ignored_stdout": ignored[-5:], "stderr_tail": "".join(stderr_lines)[-1000:]}
        selected = next((item for item in tools if isinstance(item, dict) and item.get("name") == tool), None)
        if not selected:
            return {"ok": False, "reason": "mcp_tool_not_advertised", "available_tools": [item.get("name") for item in tools if isinstance(item, dict)]}
        annotations = selected.get("annotations") if isinstance(selected.get("annotations"), dict) else {}
        permitted = annotations.get("readOnlyHint") is True or tool in (allowed_tools or set())
        if not permitted:
            return {"ok": False, "reason": "mcp_tool_is_not_read_only_or_allowlisted", "tool": tool}
        _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": arguments or {}}})
        called = _response_for(stdout_lines, 3, timeout, ignored)
        if "error" in called:
            return {"ok": False, "reason": "mcp_tool_call_error", "tool": tool, "error": called["error"], "stderr_tail": "".join(stderr_lines)[-1000:]}
        return {"ok": True, "tool": tool, "result": called.get("result") or {}, "ignored_stdout": ignored[-5:], "stderr_tail": "".join(stderr_lines)[-1000:]}
    except (RuntimeError, TimeoutError) as exc:
        return {"ok": False, "reason": type(exc).__name__, "detail": str(exc), "stderr_tail": "".join(stderr_lines)[-1000:]}
    finally:
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
