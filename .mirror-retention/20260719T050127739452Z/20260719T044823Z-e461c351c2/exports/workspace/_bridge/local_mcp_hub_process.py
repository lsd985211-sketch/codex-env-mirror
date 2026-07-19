#!/usr/bin/env python3
"""Process control helpers for the local MCP Hub.

Ownership: this module manages only the local Hub server process lifecycle.
Non-goals: it must not manage arbitrary MCP servers, rewrite scheduled tasks,
or bypass the generated affinity entry stage, forward-only fallback chain, or
target permission model.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 18881
TASK_NAME = "CodexLocalMcpHub"
HUB_SOURCE_GLOBS = ("local_mcp_hub.py", "local_mcp_hub_*.py")


def hidden_creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def windows_console_encoding() -> str:
    return "mbcs" if os.name == "nt" else "utf-8"


def http_get_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {"ok": False, "reason": "json_root_not_object"}


def local_hub_processes(port: int = DEFAULT_PORT) -> list[dict[str, Any]]:
    command = (
        "$items = Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*local_mcp_hub.py*' "
        " -and $_.CommandLine -match 'local_mcp_hub\\.py\\s+serve' "
        f" -and $_.CommandLine -like '*{int(port)}*' }} | "
        "Select-Object ProcessId,ParentProcessId,CommandLine; "
        "$items | ConvertTo-Json -Depth 4"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=hidden_creationflags(),
        )
    except Exception:
        return []
    text = (proc.stdout or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    processes: list[dict[str, Any]] = []
    current_pid = os.getpid()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            pid = 0
        command_line = str(row.get("CommandLine") or "")
        if not pid or pid == current_pid:
            continue
        normalized_command = re.sub(r"\s+", " ", command_line.replace("/", "\\")).lower()
        if "local_mcp_hub.py serve" not in normalized_command or str(int(port)) not in normalized_command:
            continue
        processes.append({"pid": pid, "parent_pid": row.get("ParentProcessId"), "command_line": command_line})
    return processes


def start_local_hub_task(task_name: str = TASK_NAME) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["schtasks", "/Run", "/TN", task_name],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding=windows_console_encoding(),
            errors="replace",
            timeout=10,
            creationflags=hidden_creationflags(),
        )
    except Exception as exc:
        return {"ok": False, "reason": repr(exc), "taskName": task_name}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[:2000],
        "stderr": (proc.stderr or "")[:2000],
        "taskName": task_name,
    }


def stop_process(pid: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/F"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding=windows_console_encoding(),
            errors="replace",
            timeout=10,
            creationflags=hidden_creationflags(),
        )
    except Exception as exc:
        return {"ok": False, "reason": repr(exc), "pid": pid}
    return {
        "ok": proc.returncode == 0,
        "pid": pid,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[:2000],
        "stderr": (proc.stderr or "")[:2000],
    }


def hub_bytecode_cache_candidates(module_dir: Path | None = None) -> list[Path]:
    """Return exact bytecode paths owned by the local Hub source modules."""
    directory = (module_dir or Path(__file__).resolve().parent).resolve()
    sources: set[Path] = set()
    for pattern in HUB_SOURCE_GLOBS:
        sources.update(path.resolve() for path in directory.glob(pattern) if path.is_file())

    candidates: set[Path] = set()
    for source in sources:
        for optimization in (None, "1", "2"):
            try:
                cache_path = importlib.util.cache_from_source(str(source), optimization=optimization)
            except (NotImplementedError, ValueError):
                continue
            candidates.add(Path(cache_path).resolve())
    return sorted(candidates, key=lambda path: str(path).lower())


def clear_hub_bytecode_cache(*, module_dir: Path | None = None, dry_run: bool) -> dict[str, Any]:
    """Remove only Hub-owned bytecode caches, leaving unrelated caches untouched."""
    candidates = hub_bytecode_cache_candidates(module_dir)
    existing = [path for path in candidates if path.is_file()]
    removed: list[str] = []
    failures: list[dict[str, str]] = []
    if not dry_run:
        for path in existing:
            try:
                path.unlink()
                removed.append(str(path))
            except OSError as exc:
                failures.append({"path": str(path), "error": repr(exc)})
    return {
        "ok": not failures,
        "dry_run": bool(dry_run),
        "candidate_bytecode_cache": [str(path) for path in existing],
        "removed_bytecode_cache": removed,
        "failed_bytecode_cache": failures,
        "safety_boundary": "only importlib cache paths derived from local_mcp_hub.py and local_mcp_hub_*.py are eligible",
    }


def reload_local_hub(*, confirm_reload: bool, port: int = DEFAULT_PORT, wait_seconds: float = 5.0) -> dict[str, Any]:
    before = local_hub_processes(port)
    bytecode_cache = clear_hub_bytecode_cache(dry_run=not confirm_reload)
    plan = {
        "schema": "local_mcp_hub.reload.v1",
        "ok": True,
        "dry_run": not confirm_reload,
        "port": int(port),
        "matched_processes": before,
        "safety_boundary": "only processes whose command line contains local_mcp_hub.py, serve, and the target port are stopped",
        "start_route": f"schtasks /Run /TN {TASK_NAME}",
        "bytecode_cache": bytecode_cache,
    }
    if not confirm_reload:
        plan["next_step"] = "rerun with --confirm-reload to stop matched Hub listener processes and restart the scheduled task"
        return plan
    if not bytecode_cache.get("ok"):
        plan.update(
            {
                "ok": False,
                "dry_run": False,
                "reason": "hub_bytecode_cache_cleanup_failed",
                "next_step": "resolve the reported cache deletion failure; the existing Hub listener was left running",
            }
        )
        return plan
    stop_results = [stop_process(int(item["pid"])) for item in before]
    start_result = start_local_hub_task()
    deadline = time.monotonic() + max(0.5, min(float(wait_seconds), 20.0))
    after: list[dict[str, Any]] = []
    health: dict[str, Any] = {"ok": False, "reason": "health_wait_not_started"}
    health_attempts = 0
    while True:
        health_attempts += 1
        after = local_hub_processes(port)
        if after:
            try:
                health = http_get_json(f"http://127.0.0.1:{int(port)}/health", timeout=1.0)
            except Exception as exc:
                health = {"ok": False, "reason": repr(exc)}
            if health.get("ok"):
                break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    plan.update(
        {
            "dry_run": False,
            "stop_results": stop_results,
            "start_result": start_result,
            "after_processes": after,
            "health": health,
            "health_attempts": health_attempts,
            "health_wait_seconds": max(0.5, min(float(wait_seconds), 20.0)),
            "ok": bool(after) and bool(health.get("ok")) and all(item.get("ok") for item in stop_results),
        }
    )
    return plan
