#!/usr/bin/env python3
"""Bootstrap a WSL workspace without importing Windows runtime state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import developer_toolchain_owner
from platform_paths import exported_environment, worktree_root


SCHEMA = "codex-wsl-workspace-bootstrap/v1"
RUNTIME_ROOT = worktree_root() / "workspace" / "_bridge" / "runtime" / "bootstrap"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_version(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return {"ok": False, "command": command, "error": repr(exc)}
    output = (result.stdout or result.stderr or "").strip().splitlines()
    return {
        "ok": result.returncode == 0,
        "command": command,
        "returncode": result.returncode,
        "version": output[0] if output else "",
    }


def required_files(root: Path) -> list[Path]:
    return [
        root / "WORKSPACE-MANIFEST.json",
        root / "codex-home" / "config.template.toml",
        root / "workspace" / "AGENTS.md",
        root / "workspace" / "_bridge" / "workflow_orchestrator.py",
        root / "workspace" / "_bridge" / "maintenance_capability_registry.py",
        root / "workspace" / "_bridge" / "mcp_capability_routes.py",
        root / "workspace" / "_bridge" / "developer_toolchain_owner.py",
        root / "workspace" / "_bridge" / "policies" / "developer_toolchain.lock.json",
    ]


def git_status(root: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "status", "--porcelain=v1", "--branch"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}
    lines = result.stdout.splitlines()
    return {
        "ok": result.returncode == 0,
        "branch": lines[0] if lines else "",
        "changes": lines[1:],
        "clean": len(lines) <= 1,
    }


def validate(root: Path) -> dict[str, Any]:
    files = []
    for path in required_files(root):
        row = {"path": str(path), "exists": path.exists()}
        if path.is_file():
            row["sha256"] = sha256_file(path)
            row["bytes"] = path.stat().st_size
        files.append(row)
    generated_dirs = [
        root / "workspace" / "_bridge" / "runtime",
        root / "workspace" / "_bridge" / "__pycache__",
        root / "workspace" / "_bridge" / "shared" / "__pycache__",
    ]
    toolchain = developer_toolchain_owner.snapshot()
    return {
        "schema": f"{SCHEMA}/validate",
        "ok": all(item["exists"] for item in files) and bool(toolchain.get("ok")),
        "root": str(root),
        "platform": {"system": platform.system(), "release": platform.release()},
        "environment": exported_environment(),
        "files": files,
        "generated_dirs": [
            {"path": str(path), "exists": path.exists(), "ignored_expected": True}
            for path in generated_dirs
        ],
        "git": git_status(root),
        "tools": [
            command_version(["git", "--version"]),
            command_version(["python3", "--version"]),
            command_version(["codex", "--version"]),
        ],
        "developer_toolchain": toolchain,
    }


def write_receipt(payload: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate isolated WSL workspace bootstrap")
    parser.add_argument("--root", type=Path, default=worktree_root())
    parser.add_argument("--receipt", type=Path, default=RUNTIME_ROOT / "bootstrap-receipt.json")
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = validate(args.root.expanduser().resolve())
    payload["generated_at"] = now_iso()
    payload["activation_performed"] = False
    payload["host_runtime_imported"] = False
    if args.write_receipt:
        write_receipt(payload, args.receipt.expanduser().resolve())
        payload["receipt"] = str(args.receipt.expanduser().resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
