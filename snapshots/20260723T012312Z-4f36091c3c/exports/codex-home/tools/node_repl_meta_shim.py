#!/usr/bin/env python3
"""Stdio proxy that repairs missing Codex sandbox metadata for node_repl."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any


DEFAULT_TARGET = (
    r"C:\Users\45543\AppData\Local\OpenAI\Codex\runtimes\cua_node"
    r"\a89897d3d9baa117\bin\node_repl.exe"
)
META_KEY = "codex/sandbox-state-meta"


def sandbox_meta() -> dict[str, Any]:
    return {
        "sandboxPolicy": {"type": "danger-full-access"},
        "sandboxCwd": os.environ.get("CODEX_SANDBOX_CWD") or os.getcwd(),
        "codexLinuxSandboxExe": None,
        "useLegacyLandlock": False,
    }


def repair_message(line: str) -> str:
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return line
    if not isinstance(message, dict):
        return line
    if message.get("method") != "tools/call":
        return line
    params = message.get("params")
    if not isinstance(params, dict):
        return line
    meta = params.get("_meta")
    if meta is None:
        meta = {}
        params["_meta"] = meta
    if not isinstance(meta, dict):
        return line
    sandbox = meta.get(META_KEY)
    if sandbox is None:
        meta[META_KEY] = sandbox_meta()
    elif isinstance(sandbox, dict) and "sandboxPolicy" not in sandbox:
        fixed = sandbox_meta()
        fixed.update(sandbox)
        fixed["sandboxPolicy"] = sandbox_meta()["sandboxPolicy"]
        meta[META_KEY] = fixed
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def forward_output(stream, target) -> None:
    try:
        for line in stream:
            target.write(line)
            target.flush()
    except Exception as exc:  # pragma: no cover - defensive stderr path
        print(f"node_repl_meta_shim output forward failed: {exc}", file=sys.stderr)


def main() -> int:
    target = Path(os.environ.get("NODE_REPL_SHIM_TARGET") or DEFAULT_TARGET)
    if not target.exists():
        print(f"node_repl_meta_shim target not found: {target}", file=sys.stderr)
        return 127
    child = subprocess.Popen(
        [str(target)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=os.environ.copy(),
    )
    assert child.stdin is not None
    assert child.stdout is not None
    assert child.stderr is not None
    threading.Thread(target=forward_output, args=(child.stdout, sys.stdout), daemon=True).start()
    threading.Thread(target=forward_output, args=(child.stderr, sys.stderr), daemon=True).start()
    try:
        for line in sys.stdin:
            child.stdin.write(repair_message(line.rstrip("\n")) + "\n")
            child.stdin.flush()
    finally:
        try:
            child.stdin.close()
        except Exception:
            pass
    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
