"""CodeGraph fallback command handler for mobile_openclaw_cli.

Owns: local CodeGraph CLI fallback command construction, execution,
target-path match validation, and routing guidance.
Non-goals: native MCP invocation, CodeGraph indexing, bridge queue mutation,
permission decisions, or source-file edits.
State behavior: read-only subprocess wrapper; does not mutate bridge state.
Normal callers: mobile_openclaw_cli.main when args.cmd == "codegraph-fallback".
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def register_codegraph_fallback_parser(subparsers: Any, project_root: Path) -> None:
    parser = subparsers.add_parser("codegraph-fallback", help="Local CodeGraph CLI fallback when MCP binding is unavailable")
    parser.add_argument("action", choices=["status", "query", "explore"])
    parser.add_argument("query", nargs="*", help="Query text for query/explore")
    parser.add_argument("--project-path", default=str(project_root), help="Project path for CodeGraph")
    parser.add_argument("--max-files", type=int, default=4, help="Maximum files for explore")
    parser.add_argument("--limit", type=int, default=10, help="Maximum results for query")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON where CodeGraph supports it")


def _expected_path_markers(query_items: list[str]) -> tuple[list[str], list[str]]:
    expected_paths = [
        item.strip("\"'")
        for item in query_items
        if ("/" in item or "\\" in item) and "." in item
    ]
    expected_source_markers = [
        "**`" + expected.replace("\\", "/").casefold() + "`**"
        for expected in expected_paths
    ]
    return expected_paths, expected_source_markers


def _build_codegraph_command(args: Any, project_root: Path, cmd_path: Path) -> list[str]:
    project_path = str(args.project_path)
    if args.action == "status":
        command = [str(cmd_path), "status", project_path]
        if args.json:
            command.append("--json")
        return command
    if args.action == "query":
        command = [
            str(cmd_path),
            "query",
            " ".join(args.query),
            "--path",
            project_path,
            "--limit",
            str(int(args.limit)),
        ]
        if args.json:
            command.append("--json")
        return command
    return [
        str(cmd_path),
        "explore",
        "--path",
        project_path,
        "--max-files",
        str(int(args.max_files)),
        *list(args.query),
    ]


def run_codegraph_fallback(args: Any, project_root: Path) -> dict[str, Any]:
    cmd_path = project_root / "_bridge" / "tools" / "codegraph" / "node_modules" / ".bin" / "codegraph.cmd"
    if args.action in {"query", "explore"} and not args.query:
        return {
            "ok": False,
            "schema": "codegraph_fallback.result.v1",
            "action": args.action,
            "error": "query_required",
            "command": str(cmd_path),
        }

    command = _build_codegraph_command(args, project_root, cmd_path)
    try:
        proc = subprocess.run(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except Exception as exc:
        return {
            "ok": False,
            "schema": "codegraph_fallback.result.v1",
            "action": args.action,
            "command": command,
            "error": f"{type(exc).__name__}: {exc}",
        }

    query_items = list(args.query or [])
    query_text = " ".join(query_items)
    expected_paths, expected_source_markers = _expected_path_markers(query_items)
    stdout_norm = (proc.stdout or "").replace("\\", "/").casefold()
    target_path_matched = not expected_paths or any(marker in stdout_norm for marker in expected_source_markers)
    return {
        "ok": proc.returncode == 0 and target_path_matched,
        "schema": "codegraph_fallback.result.v1",
        "action": args.action,
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "fallback_reason": "local_cli_equivalent_when_mcp_binding_unavailable",
        "target_match": {
            "schema": "codegraph_fallback.target_match.v1",
            "query_text": query_text,
            "expected_paths": expected_paths,
            "expected_source_markers": expected_source_markers,
            "matched": target_path_matched,
            "reason": "" if target_path_matched else "target_path_miss",
        },
        "routing_guidance": {
            "schema": "codegraph_fallback.routing_guidance.v1",
            "native_first": True,
            "fallback_boundary": "Use this local fallback only when native CodeGraph is unavailable, unbound, transport-closed, or visibly misses the intended file/symbol.",
            "precision_rule": "Anchor bridge queries with exact file paths and function names, for example _bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py stability_check.",
            "miss_handling": "If output targets unrelated files, rerun with a narrower file path plus function name, or switch to targeted local reads for that file.",
        },
    }
