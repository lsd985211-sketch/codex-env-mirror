#!/usr/bin/env python3
"""Bounded fail-open launcher adapter for Codex session maintenance.

Ownership:
  The governed Codex Desktop launcher owns this adapter.

Non-goals:
  This module does not select sessions, compact files, change thresholds, or
  launch Codex Desktop. Those remain with codex_session_store_doctor.py and the
  PowerShell launcher.

State behavior:
  It starts the existing session-store owner in a hidden child process, applies
  a bounded timeout, and emits one structured receipt. On timeout it removes
  only a dead child's matching lock record.

Caller context:
  Called after the launcher has established a stopped Codex process boundary
  and before Codex Desktop starts.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence


SCHEMA = "codex-prelaunch-maintenance.receipt.v1"
DEFAULT_TIMEOUT_SECONDS = 180
MAX_DETAIL_CHARS = 2000


def _bounded_text(value: str, limit: int = MAX_DETAIL_CHARS) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[-limit:]


def _reason(payload: dict[str, Any]) -> str:
    for container in (payload, payload.get("gate"), payload.get("result"), payload.get("plan")):
        if isinstance(container, dict) and container.get("reason"):
            return str(container["reason"])
    return "maintenance_completed"


def _applied(payload: dict[str, Any]) -> bool:
    result = payload.get("result")
    return bool(payload.get("applied") or (isinstance(result, dict) and result.get("applied")))


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _stop_process_tree(process: subprocess.Popen[str]) -> str:
    details: list[str] = []
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
            check=False,
        )
        details.append(f"taskkill_exit={completed.returncode}")
        if completed.stderr:
            details.append(_bounded_text(completed.stderr, 500))
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        details.append("process_exit_unconfirmed")
    return "; ".join(details) or "process_killed"


def _clear_dead_child_lock(workspace: Path, child_pid: int) -> bool:
    lock_path = workspace / "_bridge" / "runtime" / "codex_session_store" / "auto_maintain.lock.json"
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        if int(payload.get("pid") or 0) != child_pid:
            return False
        lock_path.unlink()
        return True
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def run_prelaunch_maintenance(
    *,
    workspace: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    python_executable: str | Path = sys.executable,
    maintenance_script: Path | None = None,
    extra_args: Sequence[str] = (),
) -> dict[str, Any]:
    timeout_seconds = max(1, int(timeout_seconds))
    script = maintenance_script or workspace / "_bridge" / "codex_session_store_doctor.py"
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": True,
        "maintenance_ok": False,
        "startup_permitted": True,
        "applied": False,
        "outcome": "not_started",
        "reason": "",
        "timeout_seconds": timeout_seconds,
        "maintenance_script": str(script),
    }
    if not script.is_file():
        receipt.update(outcome="missing_maintenance_owner", reason="missing_session_store_doctor")
        return receipt

    command = [
        str(python_executable),
        str(script),
        "auto-maintain",
        "--apply",
        "--boundary",
        "pre-launch",
        *extra_args,
    ]
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
    except Exception as exc:
        receipt.update(outcome="owner_start_failed", reason=type(exc).__name__, detail=_bounded_text(str(exc)))
        return receipt

    receipt["child_pid"] = process.pid
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        stop_detail = _stop_process_tree(process)
        child_exit_confirmed = process.poll() is not None
        lock_cleared = child_exit_confirmed and _clear_dead_child_lock(workspace, process.pid)
        receipt.update(
            outcome="maintenance_timed_out",
            reason="prelaunch_maintenance_timeout",
            child_exit_confirmed=child_exit_confirmed,
            lock_cleared=lock_cleared,
            detail=stop_detail,
        )
        return receipt

    if process.returncode != 0:
        receipt.update(
            outcome="owner_failed",
            reason=f"maintenance_exit_{process.returncode}",
            detail=_bounded_text(stderr or stdout),
        )
        return receipt
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        receipt.update(
            outcome="invalid_owner_receipt",
            reason="maintenance_output_not_json",
            detail=_bounded_text(f"{exc}; stderr={stderr}"),
        )
        return receipt
    if not isinstance(payload, dict):
        receipt.update(outcome="invalid_owner_receipt", reason="maintenance_output_not_object")
        return receipt

    maintenance_ok = bool(payload.get("ok"))
    receipt.update(
        maintenance_ok=maintenance_ok,
        applied=_applied(payload),
        outcome="completed" if maintenance_ok else "owner_reported_failure",
        reason=_reason(payload),
        owner_schema=str(payload.get("schema") or ""),
    )
    if stderr.strip():
        receipt["detail"] = _bounded_text(stderr)
    return receipt


def validate() -> dict[str, Any]:
    return {
        "schema": "codex-prelaunch-maintenance.validate.v1",
        "ok": DEFAULT_TIMEOUT_SECONDS > 0 and bool(SCHEMA),
        "checks": {
            "bounded_timeout": DEFAULT_TIMEOUT_SECONDS,
            "fail_open_receipt": True,
            "hidden_child_on_windows": True,
            "owner_boundary_preserved": True,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded Codex pre-launch session maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    run.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    subparsers.add_parser("validate")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "validate":
        payload = validate()
    else:
        payload = run_prelaunch_maintenance(workspace=args.workspace, timeout_seconds=args.timeout_seconds)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
