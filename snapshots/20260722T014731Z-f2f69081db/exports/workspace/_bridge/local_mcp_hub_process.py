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
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

from shared.windows_powershell import encoded_command_arguments

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 18881
TASK_NAME = "CodexLocalMcpHub"
HUB_SOURCE_GLOBS = ("local_mcp_hub.py", "local_mcp_hub_*.py")
WSL_INTEROP_ENTRY = Path("/proc/sys/fs/binfmt_misc/WSLInterop")
WSL_INIT = Path("/init")
WINDOWS_SYSTEM_ROOT = Path("/mnt/c/Windows")


def hidden_creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def windows_console_encoding() -> str:
    return "mbcs" if os.name == "nt" else "utf-8"


def decode_windows_cli_output(value: bytes | str | None) -> str:
    if isinstance(value, str):
        return value
    raw = bytes(value or b"")
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def windows_cmd_path(value: str) -> str:
    match = re.match(r"^/mnt/([A-Za-z])/(.*)$", str(value).replace("\\", "/"))
    if not match:
        return str(value)
    suffix = match.group(2).replace("/", "\\")
    return f"{match.group(1).upper()}:\\{suffix}"


def windows_interop_command(
    executable: str,
    *arguments: str,
    interop_entry: Path = WSL_INTEROP_ENTRY,
    init_path: Path = WSL_INIT,
) -> list[str]:
    command = [executable, *arguments]
    if os.name == "nt" or interop_entry.exists() or not init_path.is_file():
        return command
    resolved = shutil.which(executable)
    if not resolved:
        return command
    if any(argument.startswith("/") for argument in arguments):
        cmd = shutil.which("cmd.exe")
        if cmd:
            command_line = subprocess.list2cmdline([windows_cmd_path(resolved), *arguments])
            return [str(init_path), cmd, "/d", "/s", "/c", command_line]
    return [str(init_path), resolved, *arguments]


def windows_interop_cwd(
    command: list[str],
    *,
    default: Path = ROOT,
    windows_system_root: Path = WINDOWS_SYSTEM_ROOT,
) -> Path:
    if (
        len(command) >= 2
        and Path(command[0]).name == "init"
        and Path(command[1]).name.casefold() == "cmd.exe"
        and windows_system_root.is_dir()
    ):
        return windows_system_root
    return default


def windows_powershell_command(script: str) -> list[str]:
    """Build a WSL-safe command for fixed owner-authored PowerShell source."""
    return windows_interop_command("powershell.exe", *encoded_command_arguments(script))


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
            windows_powershell_command(command),
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
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            pid = 0
        command_line = str(row.get("CommandLine") or "")
        if not pid:
            continue
        normalized_command = re.sub(r"\s+", " ", command_line.replace("/", "\\")).lower()
        if "local_mcp_hub.py serve" not in normalized_command or str(int(port)) not in normalized_command:
            continue
        processes.append({"pid": pid, "parent_pid": row.get("ParentProcessId"), "command_line": command_line})
    return processes


def start_local_hub_task(task_name: str = TASK_NAME) -> dict[str, Any]:
    command = windows_interop_command("schtasks.exe", "/Run", "/TN", task_name)
    try:
        proc = subprocess.run(
            command,
            cwd=str(windows_interop_cwd(command)),
            capture_output=True,
            timeout=10,
            creationflags=hidden_creationflags(),
        )
    except Exception as exc:
        return {"ok": False, "reason": repr(exc), "taskName": task_name}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": decode_windows_cli_output(proc.stdout)[:2000],
        "stderr": decode_windows_cli_output(proc.stderr)[:2000],
        "taskName": task_name,
    }


def stop_process(pid: int) -> dict[str, Any]:
    command = windows_interop_command("taskkill.exe", "/PID", str(int(pid)), "/F")
    try:
        proc = subprocess.run(
            command,
            cwd=str(windows_interop_cwd(command)),
            capture_output=True,
            timeout=10,
            creationflags=hidden_creationflags(),
        )
    except Exception as exc:
        return {"ok": False, "reason": repr(exc), "pid": pid}
    return {
        "ok": proc.returncode == 0,
        "pid": pid,
        "returncode": proc.returncode,
        "stdout": decode_windows_cli_output(proc.stdout)[:2000],
        "stderr": decode_windows_cli_output(proc.stderr)[:2000],
    }


def hub_runtime_state(
    port: int = DEFAULT_PORT,
    *,
    process_retry_seconds: float = 0.75,
) -> dict[str, Any]:
    processes = local_hub_processes(port)
    try:
        health = http_get_json(f"http://127.0.0.1:{int(port)}/health", timeout=1.0)
    except Exception as exc:
        health = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    process_probe_attempts = 1
    retry_window = max(0.0, min(float(process_retry_seconds), 3.0))
    deadline = time.monotonic() + retry_window
    while health.get("ok") and not processes and time.monotonic() < deadline:
        time.sleep(min(0.1, max(0.01, retry_window)))
        process_probe_attempts += 1
        processes = local_hub_processes(port)
    if not processes:
        reason = "listener_process_visibility_missing" if health.get("ok") else "listener_process_missing"
        return {
            "schema": "local_mcp_hub.runtime.v1",
            "ok": False,
            "port": int(port),
            "processes": [],
            "process_probe_attempts": process_probe_attempts,
            "health": health,
            "reason": reason,
        }
    return {
        "schema": "local_mcp_hub.runtime.v1",
        "ok": bool(health.get("ok")),
        "port": int(port),
        "processes": processes,
        "process_probe_attempts": process_probe_attempts,
        "health": health,
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
        "start_route": f"schtasks.exe /Run /TN {TASK_NAME}",
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
