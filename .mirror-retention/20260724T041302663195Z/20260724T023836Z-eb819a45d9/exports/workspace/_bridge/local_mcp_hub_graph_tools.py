#!/usr/bin/env python3
"""Fresh-stdio, read-only MCP adapters for managed local graph tools.

Ownership: invoke the isolated GitNexus and Graphify MCP servers for local,
read-only graph queries. Non-goals: indexing, graph extraction, hooks,
watchers, editor setup, global configuration, or network/model access.
State behavior: each call starts one short-lived stdio process and leaves no
owned process behind. Caller context: local_mcp_hub exposes these adapters as
Hub-first graph-tool routes.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


WSL_WORK_GIT = "/home/codexlab/work/codex-workspace"
GRAPHIFY_STATE_ROOT = "/home/codexlab/.local/share/codex-graphs"
GITNEXUS_BIN = "/home/codexlab/.local/share/codex-resource-dependencies/node/gitnexus/1.6.9/node_modules/.bin/gitnexus"
GRAPHIFY_BIN = "/home/codexlab/.local/share/codex-resource-dependencies/node/graphify/0.17.1/node_modules/.bin/graphify"
DEFAULT_GRAPH_PATH = f"{GRAPHIFY_STATE_ROOT}/bridge-code/.graphify/graph.json"
MAX_TIMEOUT_SECONDS = 120
GRAPHIFY_READ_ONLY_TOOLS = {
    "first_hop_summary",
    "review_delta",
    "review_analysis",
    "recommend_commits",
    "query_graph",
    "get_node",
    "get_neighbors",
    "get_community",
    "god_nodes",
    "graph_stats",
    "shortest_path",
}


def _timeout(arguments: dict[str, Any], default: int = 45) -> int:
    try:
        value = int(arguments.get("timeout_seconds") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_TIMEOUT_SECONDS))


def _wsl_path_within(value: Any, root: str) -> str:
    path = str(value or "").strip().replace("\\", "/")
    if not path:
        return ""
    normalized = Path(path).as_posix() if path.startswith("/") else path
    allowed = root.rstrip("/")
    if normalized == allowed or normalized.startswith(f"{allowed}/"):
        return normalized
    return ""


def _working_directory(arguments: dict[str, Any]) -> tuple[str, str]:
    raw = str(arguments.get("working_directory") or WSL_WORK_GIT).strip()
    path = _wsl_path_within(raw, WSL_WORK_GIT)
    if not path:
        return "", "working_directory_must_be_within_wsl_work_git"
    if os.name != "nt" and not Path(path).is_dir():
        return "", "working_directory_not_found"
    return path, ""


def _graph_path(arguments: dict[str, Any]) -> tuple[str, str]:
    raw = str(arguments.get("graph_path") or DEFAULT_GRAPH_PATH).strip()
    path = _wsl_path_within(raw, GRAPHIFY_STATE_ROOT)
    if not path:
        return "", "graph_path_must_be_within_managed_graphify_state_root"
    if not path.endswith("/graph.json"):
        return "", "graph_path_must_reference_graph_json"
    if os.name != "nt" and not Path(path).is_file():
        return "", "graph_path_not_found"
    return path, ""


def _command(command: list[str], working_directory: str) -> list[str]:
    if os.name != "nt":
        return command
    distro = os.environ.get("CODEX_WSL_DISTRO", "Codex-Wsl-Lab")
    return ["wsl.exe", "-d", distro, "--cd", working_directory, "--", *command]


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


def _fresh_stdio_call(
    *,
    command: list[str],
    working_directory: str,
    tool: str = "",
    arguments: dict[str, Any] | None = None,
    timeout_seconds: int,
    read_only_fallback_tools: set[str] | None = None,
) -> dict[str, Any]:
    env = dict(os.environ)
    env.update({"NO_COLOR": "1", "NPM_CONFIG_UPDATE_NOTIFIER": "false"})
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    proc = subprocess.Popen(
        _command(command, working_directory),
        cwd=None if os.name == "nt" else working_directory,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
    )
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
                    "clientInfo": {"name": "local-mcp-hub-graph-tools", "version": "1"},
                },
            },
        )
        initialized = _response_for(stdout_lines, 1, timeout_seconds, ignored)
        if "error" in initialized:
            return {"ok": False, "reason": "mcp_initialize_error", "error": initialized["error"], "stderr_tail": "".join(stderr_lines)[-1000:]}
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = _response_for(stdout_lines, 2, timeout_seconds, ignored)
        tools = (listed.get("result") or {}).get("tools") if isinstance(listed.get("result"), dict) else None
        if not isinstance(tools, list):
            return {"ok": False, "reason": "mcp_tools_list_invalid", "stderr_tail": "".join(stderr_lines)[-1000:]}
        if not tool:
            return {"ok": True, "tools": tools, "ignored_stdout": ignored[-5:], "stderr_tail": "".join(stderr_lines)[-1000:]}
        selected = next((item for item in tools if isinstance(item, dict) and item.get("name") == tool), None)
        annotations = selected.get("annotations") if isinstance(selected, dict) and isinstance(selected.get("annotations"), dict) else {}
        if not selected:
            return {"ok": False, "reason": "mcp_tool_not_advertised", "available_tools": [item.get("name") for item in tools if isinstance(item, dict)]}
        is_read_only = annotations.get("readOnlyHint") is True or tool in (read_only_fallback_tools or set())
        if not is_read_only:
            return {"ok": False, "reason": "mcp_tool_is_not_read_only", "tool": tool}
        _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": arguments or {}}})
        called = _response_for(stdout_lines, 3, timeout_seconds, ignored)
        if "error" in called:
            return {"ok": False, "reason": "mcp_tool_call_error", "tool": tool, "error": called["error"], "stderr_tail": "".join(stderr_lines)[-1000:]}
        return {"ok": True, "tool": tool, "result": called.get("result") or {}, "ignored_stdout": ignored[-5:], "stderr_tail": "".join(stderr_lines)[-1000:]}
    except (OSError, RuntimeError, TimeoutError) as exc:
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


def gitnexus_list_tools(arguments: dict[str, Any]) -> dict[str, Any]:
    working_directory, reason = _working_directory(arguments)
    if reason:
        return {"ok": False, "reason": reason, "tool": "gitnexus.list_tools"}
    payload = _fresh_stdio_call(command=[GITNEXUS_BIN, "mcp"], working_directory=working_directory, timeout_seconds=_timeout(arguments))
    payload.setdefault("tool", "gitnexus.list_tools")
    payload["working_directory"] = working_directory
    return payload


def gitnexus_call(arguments: dict[str, Any]) -> dict[str, Any]:
    working_directory, reason = _working_directory(arguments)
    tool = str(arguments.get("tool") or "").strip()
    tool_arguments = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
    if reason:
        return {"ok": False, "reason": reason, "tool": "gitnexus.call"}
    if not tool:
        return {"ok": False, "reason": "tool_required", "tool": "gitnexus.call"}
    payload = _fresh_stdio_call(command=[GITNEXUS_BIN, "mcp"], working_directory=working_directory, tool=tool, arguments=tool_arguments, timeout_seconds=_timeout(arguments))
    payload.setdefault("alias", "gitnexus.call")
    payload["working_directory"] = working_directory
    return payload


def graphify_list_tools(arguments: dict[str, Any]) -> dict[str, Any]:
    graph_path, reason = _graph_path(arguments)
    if reason:
        return {"ok": False, "reason": reason, "tool": "graphify.list_tools"}
    payload = _fresh_stdio_call(
        command=[GRAPHIFY_BIN, "serve", graph_path],
        working_directory=WSL_WORK_GIT,
        timeout_seconds=_timeout(arguments),
        read_only_fallback_tools=GRAPHIFY_READ_ONLY_TOOLS,
    )
    payload.setdefault("tool", "graphify.list_tools")
    payload["graph_path"] = graph_path
    return payload


def graphify_call(arguments: dict[str, Any]) -> dict[str, Any]:
    graph_path, reason = _graph_path(arguments)
    tool = str(arguments.get("tool") or "").strip()
    tool_arguments = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
    if reason:
        return {"ok": False, "reason": reason, "tool": "graphify.call"}
    if not tool:
        return {"ok": False, "reason": "tool_required", "tool": "graphify.call"}
    payload = _fresh_stdio_call(
        command=[GRAPHIFY_BIN, "serve", graph_path],
        working_directory=WSL_WORK_GIT,
        tool=tool,
        arguments=tool_arguments,
        timeout_seconds=_timeout(arguments),
        read_only_fallback_tools=GRAPHIFY_READ_ONLY_TOOLS,
    )
    payload.setdefault("alias", "graphify.call")
    payload["graph_path"] = graph_path
    return payload


def validate() -> dict[str, Any]:
    if os.name == "nt":
        gitnexus_available = bool(shutil.which("wsl.exe"))
        graphify_available = gitnexus_available
    else:
        gitnexus_available = Path(GITNEXUS_BIN).is_file()
        graphify_available = Path(GRAPHIFY_BIN).is_file()
    return {
        "schema": "local_mcp_hub_graph_tools.validate.v1",
        "ok": gitnexus_available and graphify_available,
        "gitnexus_available": gitnexus_available,
        "graphify_available": graphify_available,
        "default_graph_path": DEFAULT_GRAPH_PATH,
        "default_graph_present": Path(DEFAULT_GRAPH_PATH).is_file() if os.name != "nt" else None,
        "lifecycle": "fresh_stdio_per_call_exit",
        "write_policy": "only upstream read-only hints, or the fixed Graphify query-only allowlist, are forwarded",
    }
