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

import os
import shutil
from pathlib import Path
from typing import Any

from local_mcp_stdio_client import fresh_stdio_call as _fresh_stdio_call


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


def gitnexus_list_tools(arguments: dict[str, Any]) -> dict[str, Any]:
    working_directory, reason = _working_directory(arguments)
    if reason:
        return {"ok": False, "reason": reason, "tool": "gitnexus.list_tools"}
    payload = _fresh_stdio_call(
        command=_command([GITNEXUS_BIN, "mcp"], working_directory),
        working_directory=None if os.name == "nt" else working_directory,
        timeout_seconds=_timeout(arguments),
        client_name="local-mcp-hub-graph-tools",
    )
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
    payload = _fresh_stdio_call(
        command=_command([GITNEXUS_BIN, "mcp"], working_directory),
        working_directory=None if os.name == "nt" else working_directory,
        tool=tool,
        arguments=tool_arguments,
        timeout_seconds=_timeout(arguments),
        client_name="local-mcp-hub-graph-tools",
    )
    payload.setdefault("alias", "gitnexus.call")
    payload["working_directory"] = working_directory
    return payload


def graphify_list_tools(arguments: dict[str, Any]) -> dict[str, Any]:
    graph_path, reason = _graph_path(arguments)
    if reason:
        return {"ok": False, "reason": reason, "tool": "graphify.list_tools"}
    payload = _fresh_stdio_call(
        command=_command([GRAPHIFY_BIN, "serve", graph_path], WSL_WORK_GIT),
        working_directory=None if os.name == "nt" else WSL_WORK_GIT,
        timeout_seconds=_timeout(arguments),
        allowed_tools=GRAPHIFY_READ_ONLY_TOOLS,
        client_name="local-mcp-hub-graph-tools",
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
        command=_command([GRAPHIFY_BIN, "serve", graph_path], WSL_WORK_GIT),
        working_directory=None if os.name == "nt" else WSL_WORK_GIT,
        tool=tool,
        arguments=tool_arguments,
        timeout_seconds=_timeout(arguments),
        allowed_tools=GRAPHIFY_READ_ONLY_TOOLS,
        client_name="local-mcp-hub-graph-tools",
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
