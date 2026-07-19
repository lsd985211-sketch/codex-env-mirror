#!/usr/bin/env python3
"""Tiny stdio MCP server for gateway lab smoke tests.

Ownership: disposable local test child for `_bridge/gateway_lab.py` experiments.
Non-goals: no production state, no filesystem access, no external network calls.
State behavior: stateless stdio JSON-RPC loop.
Caller context: launched only by isolated gateway candidate tests.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def respond(request_id: Any, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result or {}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(message: dict[str, Any]) -> None:
    method = str(message.get("method") or "")
    request_id = message.get("id")
    if not request_id:
        return
    if method == "initialize":
        respond(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "gateway-lab-echo", "version": "0.1.0"},
            },
        )
    elif method == "tools/list":
        respond(
            request_id,
            {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Return the provided text. Lab-only smoke test tool.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }
                ]
            },
        )
    elif method == "tools/call":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        respond(request_id, {"content": [{"type": "text", "text": str(args.get("text") or "")}]})
    else:
        respond(request_id, error={"code": -32601, "message": f"unknown method: {method}"})


def main() -> int:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            handle(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
