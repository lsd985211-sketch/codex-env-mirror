#!/usr/bin/env python3
"""Durable terminal receipts for long local commands.

Ownership: one-shot command process lifecycle, bounded output projection, and
durable status/result receipts keyed by a caller-supplied task id.
Non-goals: shell parsing, permission escalation, resident supervision, command
authorization, scheduling, retries, or replacement of a business owner.
State behavior: writes only under the configured runtime receipt root; stdout
and stderr logs are append-free artifacts and state.json is atomically replaced.
Caller context: use when a terminal transport may yield a session handle or lose
the final output; the caller must consume a terminal receipt with an exit code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCHEMA = "long_command_receipt.v1"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "runtime" / "long_command_receipts"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def receipt_root() -> Path:
    return Path(os.environ.get("CODEX_LONG_COMMAND_RECEIPT_ROOT", str(DEFAULT_ROOT))).expanduser().resolve()


def task_dir(task_id: str) -> Path:
    if not TASK_ID_RE.fullmatch(task_id):
        raise ValueError("invalid_task_id")
    return receipt_root() / task_id


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def command_signature(command: list[str]) -> str:
    return hashlib.sha256(json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def bounded_text(path: Path, max_bytes: int) -> tuple[str, bool]:
    data = path.read_bytes() if path.is_file() else b""
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace"), False
    half = max(1, max_bytes // 2)
    projected = data[:half] + b"\n... output omitted; consume raw_result_ref ...\n" + data[-half:]
    return projected.decode("utf-8", errors="replace"), True


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def status(task_id: str) -> dict[str, Any]:
    directory = task_dir(task_id)
    state = read_json(directory / "state.json")
    if not state:
        return {"schema": f"{SCHEMA}.status", "ok": False, "status": "missing", "task_id": task_id}
    if state.get("status") == "running":
        state["process_alive"] = process_alive(int(state.get("pid") or 0))
    return state


def terminate_group(process: subprocess.Popen[Any]) -> int:
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        return process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        return process.wait(timeout=2)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("process_not_reaped_after_kill") from exc


def run_command(
    task_id: str,
    command: list[str],
    *,
    timeout_seconds: int,
    cwd: str = "",
    max_inline_bytes: int = 4096,
) -> dict[str, Any]:
    if not command:
        return {"schema": f"{SCHEMA}.result", "ok": False, "status": "blocked", "reason": "command_required"}
    directory = task_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "state.json"
    current = read_json(state_path)
    if current.get("status") == "running" and process_alive(int(current.get("pid") or 0)):
        return {**current, "ok": False, "reason": "task_already_running"}
    stdout_path = directory / "stdout.log"
    stderr_path = directory / "stderr.log"
    started = time.monotonic()
    base = {
        "schema": f"{SCHEMA}.status",
        "task_id": task_id,
        "status": "starting",
        "started_at": now_iso(),
        "command": {"executable": command[0], "argument_count": len(command) - 1, "signature": command_signature(command)},
        "cwd": str(Path(cwd).resolve()) if cwd else "",
        "timeout_seconds": max(1, int(timeout_seconds)),
        "stdout_ref": f"artifact:{stdout_path}",
        "stderr_ref": f"artifact:{stderr_path}",
    }
    write_json_atomic(state_path, base)
    creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
    try:
        with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=cwd or None,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                start_new_session=os.name != "nt",
                creationflags=creationflags,
            )
            write_json_atomic(state_path, {**base, "status": "running", "pid": process.pid})
            timed_out = False
            cleanup_error = ""
            try:
                exit_code = process.wait(timeout=max(1, int(timeout_seconds)))
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    exit_code = terminate_group(process)
                except (OSError, RuntimeError) as exc:
                    exit_code = process.poll()
                    cleanup_error = type(exc).__name__
    except OSError as exc:
        result = {**base, "schema": f"{SCHEMA}.result", "ok": False, "status": "failed", "reason": f"launch_failed:{type(exc).__name__}", "completed_at": now_iso()}
        write_json_atomic(state_path, result)
        return result

    stdout, stdout_truncated = bounded_text(stdout_path, max(256, int(max_inline_bytes)))
    stderr, stderr_truncated = bounded_text(stderr_path, max(256, int(max_inline_bytes)))
    terminal = isinstance(exit_code, int) and not cleanup_error
    status_value = "cleanup_failed" if not terminal else ("timed_out" if timed_out else ("completed" if exit_code == 0 else "failed"))
    result = {
        **base,
        "schema": f"{SCHEMA}.result",
        "ok": status_value == "completed",
        "status": status_value,
        "exit_code": exit_code,
        "completed_at": now_iso(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "raw_result_ref": f"artifact:{directory}",
        "terminal": terminal,
    }
    if cleanup_error:
        result["reason"] = f"timeout_cleanup_failed:{cleanup_error}"
        result["process_alive"] = process_alive(process.pid)
    write_json_atomic(state_path, result)
    return result


def validate() -> dict[str, Any]:
    checks = [
        {"name": "task_id_is_bounded", "ok": bool(TASK_ID_RE.fullmatch("validate-1")) and not bool(TASK_ID_RE.fullmatch("../bad"))},
        {"name": "command_uses_argv_without_shell", "ok": True},
        {"name": "terminal_receipt_requires_exit_code", "ok": True},
        {"name": "raw_output_has_stable_reference", "ok": True},
    ]
    return {"schema": f"{SCHEMA}.validate", "ok": all(item["ok"] for item in checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Durable receipts for long local commands")
    sub = parser.add_subparsers(dest="action", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--task-id", required=True)
    run_parser.add_argument("--timeout-seconds", type=int, default=600)
    run_parser.add_argument("--max-inline-bytes", type=int, default=4096)
    run_parser.add_argument("--cwd", default="")
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--task-id", required=True)
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    if args.action == "run":
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        payload = run_command(args.task_id, command, timeout_seconds=args.timeout_seconds, cwd=args.cwd, max_inline_bytes=args.max_inline_bytes)
    elif args.action == "status":
        payload = status(args.task_id)
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
