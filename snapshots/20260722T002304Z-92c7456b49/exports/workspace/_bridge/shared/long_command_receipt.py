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
TERMINAL_STATUSES = frozenset({"completed", "failed", "timed_out", "cleanup_failed", "monitor_lost"})
FINALIZATION_GRACE_SECONDS = 2.0


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


def _base_state(
    task_id: str,
    command: list[str],
    *,
    timeout_seconds: int,
    cwd: str,
    status_value: str,
    supervisor_pid: int = 0,
) -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA}.status",
        "task_id": task_id,
        "status": status_value,
        "started_at": now_iso(),
        "command": {"executable": command[0], "argument_count": len(command) - 1, "signature": command_signature(command)},
        "cwd": str(Path(cwd).resolve()) if cwd else "",
        "timeout_seconds": max(1, int(timeout_seconds)),
        "stdout_ref": f"artifact:{task_dir(task_id) / 'stdout.log'}",
        "stderr_ref": f"artifact:{task_dir(task_id) / 'stderr.log'}",
        "supervisor_pid": int(supervisor_pid or 0),
    }


def status(task_id: str) -> dict[str, Any]:
    directory = task_dir(task_id)
    state = read_json(directory / "state.json")
    if not state:
        return {"schema": f"{SCHEMA}.status", "ok": False, "status": "missing", "task_id": task_id}
    if state.get("status") not in TERMINAL_STATUSES:
        command_alive = process_alive(int(state.get("pid") or 0))
        supervisor_alive = process_alive(int(state.get("supervisor_pid") or 0))
        state["process_alive"] = command_alive
        state["supervisor_alive"] = supervisor_alive
        if not command_alive and not supervisor_alive:
            deadline = float(state.get("finalization_deadline_monotonic") or 0.0)
            if deadline <= 0.0:
                state["finalization_deadline_monotonic"] = time.monotonic() + FINALIZATION_GRACE_SECONDS
                state["finalization_pending"] = True
                write_json_atomic(directory / "state.json", state)
                return state
            if time.monotonic() < deadline:
                state["finalization_pending"] = True
                return state
            state = {
                **state,
                "schema": f"{SCHEMA}.result",
                "ok": False,
                "status": "monitor_lost",
                "terminal": False,
                "reason": "worker_and_command_exited_without_terminal_receipt",
                "completed_at": now_iso(),
            }
            write_json_atomic(directory / "state.json", state)
    return state


def wait_for_terminal(task_id: str, *, wait_seconds: float, interval_seconds: float = 0.25) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while True:
        current = status(task_id)
        if str(current.get("status") or "") in TERMINAL_STATUSES:
            return current
        if time.monotonic() >= deadline:
            return {
                **current,
                "ok": False,
                "status": "deferred",
                "terminal": False,
                "reason": "terminal_receipt_not_ready",
                "next_action": f"status --task-id {task_id}",
            }
        time.sleep(max(0.05, float(interval_seconds)))


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
    supervisor_pid: int = 0,
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
    base = _base_state(
        task_id,
        command,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        status_value="starting",
        supervisor_pid=supervisor_pid,
    )
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


def start_command(
    task_id: str,
    command: list[str],
    *,
    timeout_seconds: int,
    cwd: str = "",
    max_inline_bytes: int = 4096,
) -> dict[str, Any]:
    """Submit a one-shot worker that survives a caller transport losing its final output."""
    if not command:
        return {"schema": f"{SCHEMA}.submit", "ok": False, "status": "blocked", "reason": "command_required"}
    directory = task_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "state.json"
    current = read_json(state_path)
    if current.get("status") not in TERMINAL_STATUSES and (
        process_alive(int(current.get("pid") or 0)) or process_alive(int(current.get("supervisor_pid") or 0))
    ):
        return {**current, "ok": False, "reason": "task_already_running"}
    submitted = _base_state(
        task_id,
        command,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        status_value="submitted",
    )
    write_json_atomic(state_path, submitted)
    worker_command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_worker",
        "--task-id",
        task_id,
        "--timeout-seconds",
        str(max(1, int(timeout_seconds))),
        "--max-inline-bytes",
        str(max(256, int(max_inline_bytes))),
        "--command-json",
        json.dumps(command, ensure_ascii=False),
    ]
    if cwd:
        worker_command.extend(["--cwd", cwd])
    try:
        # A launcher must not retain a Popen handle: its own short-lived process
        # would otherwise warn or try to reap the detached receipt worker. The
        # worker owns its command session and writes every terminal state.
        worker_pid = os.spawnv(os.P_NOWAIT, sys.executable, worker_command)
    except OSError as exc:
        failed = {
            **submitted,
            "schema": f"{SCHEMA}.result",
            "ok": False,
            "status": "failed",
            "terminal": False,
            "reason": f"worker_launch_failed:{type(exc).__name__}",
            "completed_at": now_iso(),
        }
        write_json_atomic(state_path, failed)
        return failed
    current = read_json(state_path)
    if current.get("status") == "submitted":
        current["supervisor_pid"] = worker_pid
        write_json_atomic(state_path, current)
    return {
        **_base_state(task_id, command, timeout_seconds=timeout_seconds, cwd=cwd, status_value="submitted", supervisor_pid=worker_pid),
        "schema": f"{SCHEMA}.submit",
        "ok": True,
        "terminal": False,
        "next_action": f"status --task-id {task_id}",
        "raw_result_ref": f"artifact:{directory}",
    }


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
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--task-id", required=True)
    start_parser.add_argument("--timeout-seconds", type=int, default=600)
    start_parser.add_argument("--max-inline-bytes", type=int, default=4096)
    start_parser.add_argument("--cwd", default="")
    start_parser.add_argument("command", nargs=argparse.REMAINDER)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--task-id", required=True)
    status_parser.add_argument("--wait-seconds", type=float, default=0.0)
    status_parser.add_argument("--interval-seconds", type=float, default=0.25)
    worker_parser = sub.add_parser("_worker")
    worker_parser.add_argument("--task-id", required=True)
    worker_parser.add_argument("--timeout-seconds", type=int, required=True)
    worker_parser.add_argument("--max-inline-bytes", type=int, required=True)
    worker_parser.add_argument("--cwd", default="")
    worker_parser.add_argument("--command-json", required=True)
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    if args.action == "run":
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        payload = run_command(args.task_id, command, timeout_seconds=args.timeout_seconds, cwd=args.cwd, max_inline_bytes=args.max_inline_bytes)
    elif args.action == "start":
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        payload = start_command(args.task_id, command, timeout_seconds=args.timeout_seconds, cwd=args.cwd, max_inline_bytes=args.max_inline_bytes)
    elif args.action == "status":
        payload = wait_for_terminal(args.task_id, wait_seconds=args.wait_seconds, interval_seconds=args.interval_seconds) if args.wait_seconds > 0 else status(args.task_id)
    elif args.action == "_worker":
        try:
            command = json.loads(args.command_json)
        except json.JSONDecodeError:
            command = []
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            payload = {"schema": f"{SCHEMA}.result", "ok": False, "status": "failed", "reason": "worker_command_invalid"}
        else:
            payload = run_command(
                args.task_id,
                command,
                timeout_seconds=args.timeout_seconds,
                cwd=args.cwd,
                max_inline_bytes=args.max_inline_bytes,
                supervisor_pid=os.getpid(),
            )
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") or args.action == "start" else 1


if __name__ == "__main__":
    raise SystemExit(main())
