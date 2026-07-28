"""Detached process owner for governed Codex Desktop CDP startup requests.

Ownership:
  Owns only creation of the hidden PowerShell process that invokes the existing
  governed Codex Desktop launcher.

Non-goals:
  It does not probe CDP, select ports, stop processes, wait for Desktop startup,
  or decide delivery/retry outcomes.

State behavior:
  Starts one detached child and returns a bounded receipt. The launcher keeps
  singleton, maintenance, elevation, and final readiness ownership.

Caller context:
  Used by codex_cdp_route.ensure_codex_cdp when no live CDP listener exists.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any, Callable


SCHEMA = "codex-cdp-route.start-request.v1"


def _hidden_creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _powershell_executable() -> str:
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.is_file() else "powershell.exe"


def launch_start_script_detached(
    start_script: Path,
    *,
    port: int,
    cwd: Path,
    popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> dict[str, Any]:
    """Submit the governed launcher without owning its runtime deadline."""

    if not start_script.is_file():
        return {
            "schema": SCHEMA,
            "ok": False,
            "launched": False,
            "reason": "start_script_missing",
            "script": str(start_script),
        }
    command = [
        _powershell_executable(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(start_script),
    ]
    env = os.environ.copy()
    env["CODEX_CDP_PORT"] = str(int(port))
    try:
        process = popen_factory(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=_hidden_creationflags(),
        )
    except Exception as exc:
        return {
            "schema": SCHEMA,
            "ok": False,
            "launched": False,
            "reason": "launcher_process_start_failed",
            "error": repr(exc),
            "script": str(start_script),
        }
    return {
        "schema": SCHEMA,
        "ok": True,
        "launched": True,
        "reason": "governed_launcher_submitted",
        "pid": int(process.pid),
        "port": int(port),
        "script": str(start_script),
        "policy": "submission is asynchronous; CDP readiness remains the caller acceptance predicate",
    }


def validate() -> dict[str, Any]:
    return {
        "schema": "codex-cdp-route-process.validate.v1",
        "ok": True,
        "checks": {
            "detached_submission": True,
            "no_process_kill_or_wait": True,
            "launcher_owns_singleton_and_readiness": True,
        },
    }


__all__ = ["launch_start_script_detached", "validate"]
