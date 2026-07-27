#!/usr/bin/env python3
"""Native MCP facade for the shared non-blocking CodeGraph query runtime."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from codegraph_query_runtime import ROOT, query_codegraph


MCP_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "codegraph-fresh-wrapper"
SERVER_VERSION = "0.2.0"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")


def text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def json_text_result(payload: dict[str, Any], *, is_error: bool | None = None) -> dict[str, Any]:
    return text_result(
        json.dumps(payload, ensure_ascii=False, indent=2),
        is_error=(not bool(payload.get("ok", True))) if is_error is None else bool(is_error),
    )


class CodeGraphFreshService:
    def instructions(self) -> str:
        return (
            "CodeGraph wrapper MCP. Use codegraph_explore for source structure, "
            "symbol lookup, call paths, and impact analysis. The wrapper performs "
            "a validated index immediately and coalesces freshness maintenance "
            "in the background."
        )

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": self.instructions(),
        }

    def tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {
            "tools": [
                {
                    "name": "codegraph_explore",
                    "description": "Explore code structure, symbols, call paths, and impact through the shared non-blocking query runtime.",
                    "annotations": {
                        "title": "CodeGraph Explore",
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": False,
                        "openWorldHint": False,
                    },
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "projectPath": {"type": "string"},
                            "maxFiles": {"type": "integer", "minimum": 1, "maximum": 12},
                            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                            "freshness_targets": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional explicit local files/directories to freshness-check before exploring.",
                            },
                            "exclude_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional paths that must not appear in accepted results.",
                            },
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                }
            ]
        }

    def tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if name != "codegraph_explore":
            return json_text_result({"ok": False, "reason": "unknown_tool", "tool": name}, is_error=True)
        return self.codegraph_explore(arguments)

    def codegraph_explore(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return json_text_result({"ok": False, "reason": "query_required"}, is_error=True)
        result = query_codegraph(
            query,
            project_path=str(arguments.get("projectPath") or ROOT),
            max_files=int(arguments.get("maxFiles") or 8),
            timeout_seconds=int(arguments.get("timeout_seconds") or 60),
            freshness_targets=arguments.get("freshness_targets"),
            exclude_paths=arguments.get("exclude_paths"),
        )
        if not result.get("ok"):
            return json_text_result(result, is_error=True)
        evidence = {
            "schema": "codegraph_fresh_wrapper.evidence.v2",
            "index": result.get("index"),
            "freshness": result.get("freshness"),
            "refresh": result.get("refresh"),
            "degraded": result.get("degraded"),
        }
        text = str(result.get("analysis") or "")
        if str(result.get("stderr") or "").strip():
            text = f"{text}\n\n**CodeGraph stderr**\n\n```text\n{result.get('stderr')}\n```"
        text = f"{text}\n\n**Freshness Evidence**\n\n```json\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n```"
        return text_result(text)


def serve(service: CodeGraphFreshService) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id: Any = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = str(request.get("method") or "")
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            if method == "initialize":
                result = service.initialize(params)
            elif method == "tools/list":
                result = service.tools_list(params)
            elif method == "tools/call":
                result = service.tools_call(params)
            elif method == "notifications/initialized":
                continue
            else:
                result = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
            if "error" in result:
                response = {"jsonrpc": "2.0", "id": request_id, **result}
            else:
                response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Target-aware CodeGraph MCP wrapper")
    parser.parse_args()
    return serve(CodeGraphFreshService())


if __name__ == "__main__":
    raise SystemExit(main())
