#!/usr/bin/env python3
"""Protocol smoke tests for the isolated WSL Codex runtime projection."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from pathlib import Path

from wsl_codex_runtime import windows_cwd_to_wsl


ROOT = Path(__file__).resolve().parents[2]
CODEX_HOME = Path(os.environ.get("WSL_CODEX_HOME", str(Path.home() / ".codex-app"))).expanduser().resolve()
TIMEOUT = 12.0


def read_json_line(stream, timeout: float = TIMEOUT) -> dict:
    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        raise TimeoutError("MCP response timeout")
    line = stream.readline()
    if not line:
        raise RuntimeError("MCP process closed before response")
    return json.loads(line)


def smoke(name: str, command: list[str]) -> dict[str, object]:
    proc = subprocess.Popen(
        command,
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "wsl-codex-runtime-smoke", "version": "1"},
            },
        }) + "\n")
        proc.stdin.flush()
        initialized = read_json_line(proc.stdout)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
        proc.stdin.flush()
        tools = read_json_line(proc.stdout)
        return {
            "name": name,
            "ok": initialized.get("id") == 1 and "result" in initialized and tools.get("id") == 2 and "result" in tools,
            "initialized": initialized.get("id") == 1 and "result" in initialized,
            "tools_listed": tools.get("id") == 2 and "result" in tools,
            "tool_count": len((tools.get("result") or {}).get("tools") or []),
        }
    finally:
        proc.kill()
        proc.wait(timeout=5)


def main() -> int:
    results = []
    mapped_c, mapped_c_strategy = windows_cwd_to_wsl(r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager")
    mapped_w, mapped_w_strategy = windows_cwd_to_wsl("W:\\")
    results.append({
        "name": "session-cwd-projection",
        "ok": mapped_c.startswith("/mnt/c/") and mapped_w == str(ROOT),
        "windows_cwd": mapped_c,
        "windows_strategy": mapped_c_strategy,
        "work_git_cwd": mapped_w,
        "work_git_strategy": mapped_w_strategy,
    })
    results.append(smoke("node_repl", [str(ROOT / "workspace" / "_bridge" / "codex_node_repl_wsl.sh")]))
    results.append(smoke("custom-slash-commands", [
        "python3",
        str(ROOT / "workspace" / "_bridge" / "custom_slash_commands_mcp.py"),
        "--registry",
        str(ROOT / "workspace" / "_bridge" / "slash_commands" / "commands.json"),
    ]))
    results.append(smoke("sqlite-scratch", [
        "python3",
        str(ROOT / "workspace" / "_bridge" / "sqlite_mcp_server.py"),
        "--db",
        str(CODEX_HOME / "sqlite" / "codex_scratch.sqlite"),
        "--permissions",
        "list,read,create,update,delete,ddl,transaction,utility",
    ]))
    payload = {"schema": "codex-wsl-runtime.smoke.v1", "ok": all(row["ok"] for row in results), "results": results}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
