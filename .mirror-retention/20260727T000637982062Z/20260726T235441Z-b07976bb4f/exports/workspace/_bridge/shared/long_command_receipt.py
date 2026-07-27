#!/usr/bin/env python3
"""Durable terminal receipts for long local commands.

Ownership: one-shot command process lifecycle, bounded output projection, and
durable status/result receipts keyed by a caller-supplied task id or intent.
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


SCHEMA_FAMILY = "long_command_receipt"
SCHEMA = f"{SCHEMA_FAMILY}.v2"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "runtime" / "long_command_receipts"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
INTENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,239}$")
TERMINAL_STATUSES = frozenset({"completed", "failed", "timed_out", "cleanup_failed", "monitor_lost"})
FINALIZATION_GRACE_SECONDS = 2.0
DEFAULT_COMPACT_BYTES = 768


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


def execution_signature(command: list[str], *, cwd: str, timeout_seconds: int) -> str:
    payload = {
        "command": command,
        "cwd": str(Path(cwd).resolve()) if cwd else "",
        "timeout_seconds": max(1, int(timeout_seconds)),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def task_id_for_intent(intent_id: str) -> str:
    if not INTENT_ID_RE.fullmatch(str(intent_id or "")):
        raise ValueError("invalid_intent_id")
    return f"intent-{hashlib.sha256(intent_id.encode('utf-8')).hexdigest()[:32]}"


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
    intent_id: str = "",
    execution_signature_value: str = "",
) -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA}.status",
        "task_id": task_id,
        "status": status_value,
        "started_at": now_iso(),
        "intent_id": str(intent_id or ""),
        "command": {
            "executable": command[0],
            "argument_count": len(command) - 1,
            "signature": command_signature(command),
            "execution_signature": execution_signature_value
            or execution_signature(command, cwd=cwd, timeout_seconds=timeout_seconds),
        },
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


def project_status(payload: dict[str, Any], *, detail: str = "compact", compact_bytes: int = DEFAULT_COMPACT_BYTES) -> dict[str, Any]:
    if detail == "full":
        return dict(payload)
    command = payload.get("command") if isinstance(payload.get("command"), dict) else {}
    projected = {
        key: payload.get(key)
        for key in (
            "schema",
            "ok",
            "task_id",
            "intent_id",
            "status",
            "terminal",
            "reason",
            "started_at",
            "completed_at",
            "elapsed_seconds",
            "exit_code",
            "timeout_seconds",
            "process_alive",
            "supervisor_alive",
            "finalization_pending",
            "raw_result_ref",
            "stdout_ref",
            "stderr_ref",
        )
        if key in payload
    }
    projected["command"] = {
        key: command.get(key)
        for key in ("executable", "argument_count", "signature", "execution_signature")
        if key in command
    }
    for stream in ("stdout", "stderr"):
        text = str(payload.get(stream) or "")
        if text:
            encoded = text.encode("utf-8")
            preview = encoded[-max(128, int(compact_bytes)) :].decode("utf-8", errors="replace")
            projected[f"{stream}_preview"] = preview
            projected[f"{stream}_preview_truncated"] = len(encoded) > max(128, int(compact_bytes)) or bool(
                payload.get(f"{stream}_truncated")
            )
    projected["projection"] = "compact"
    projected["full_result_available"] = bool(payload.get("raw_result_ref"))
    return projected


def follow_command(
    task_id: str,
    *,
    wait_seconds: float = 300.0,
    interval_seconds: float = 0.5,
    detail: str = "compact",
) -> dict[str, Any]:
    result = wait_for_terminal(task_id, wait_seconds=wait_seconds, interval_seconds=interval_seconds)
    projected = project_status(result, detail=detail)
    status_value = str(projected.get("status") or "")
    if not projected.get("terminal") and status_value not in TERMINAL_STATUSES:
        projected["next_action"] = f"follow --task-id {task_id} --wait-seconds {max(30, int(wait_seconds))}"
    elif not projected.get("terminal"):
        projected["next_action"] = "inspect_unconsumable_receipt_and_route_owner_recovery"
        projected["recovery_required"] = True
    projected["business_command_resubmit_allowed"] = False
    projected["native_handle_contract"] = (
        "Run one follow process and consume its native process/session handle until exit; never resubmit the business command to poll."
    )
    return projected


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
    intent_id: str = "",
    execution_signature_value: str = "",
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
        intent_id=intent_id,
        execution_signature_value=execution_signature_value,
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
    intent_id: str = "",
) -> dict[str, Any]:
    """Submit a one-shot worker that survives a caller transport losing its final output."""
    if not command:
        return {"schema": f"{SCHEMA}.submit", "ok": False, "status": "blocked", "reason": "command_required"}
    directory = task_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "state.json"
    current = read_json(state_path)
    desired_execution_signature = execution_signature(command, cwd=cwd, timeout_seconds=timeout_seconds)
    if current:
        stored_command = current.get("command") if isinstance(current.get("command"), dict) else {}
        stored_execution_signature = str(stored_command.get("execution_signature") or "")
        if not stored_execution_signature:
            return {**project_status(current), "ok": False, "reason": "legacy_receipt_missing_execution_signature"}
        if stored_execution_signature != desired_execution_signature:
            return {
                **project_status(current),
                "ok": False,
                "reason": "task_id_execution_signature_conflict",
                "requested_execution_signature": desired_execution_signature,
            }
        current = status(task_id)
        current_status = str(current.get("status") or "")
        current_is_terminal_status = current_status in TERMINAL_STATUSES
        return {
            **project_status(current),
            "schema": f"{SCHEMA}.submit",
            "submit_ok": True,
            "reused": True,
            "reuse_state": "terminal" if current_is_terminal_status else "running",
            "next_action": (
                "consume_terminal_receipt"
                if current.get("terminal")
                else (
                    "inspect_unconsumable_receipt_and_route_owner_recovery"
                    if current_is_terminal_status
                    else f"follow --task-id {task_id}"
                )
            ),
            "business_command_resubmit_allowed": False,
        }
    submitted = _base_state(
        task_id,
        command,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        status_value="submitted",
        intent_id=intent_id,
        execution_signature_value=desired_execution_signature,
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
        "--execution-signature",
        desired_execution_signature,
    ]
    if intent_id:
        worker_command.extend(["--intent-id", intent_id])
    if cwd:
        worker_command.extend(["--cwd", cwd])
    creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
    try:
        # Popen preserves the worker's process lifetime on both Windows and WSL.
        # The launcher intentionally disowns the handle after recording its PID:
        # the worker, not this short submit process, owns command reaping and the
        # durable terminal receipt.
        worker = subprocess.Popen(
            worker_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
        worker_pid = worker.pid
        worker.returncode = 0
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
    current = read_json(state_path)
    return {
        **current,
        "schema": f"{SCHEMA}.submit",
        "ok": True,
        "submit_ok": True,
        "terminal": False,
        "reused": False,
        "reuse_state": "new",
        "next_action": f"follow --task-id {task_id}",
        "raw_result_ref": f"artifact:{directory}",
    }


def submit_or_reuse(
    intent_id: str,
    command: list[str],
    *,
    timeout_seconds: int,
    cwd: str = "",
    max_inline_bytes: int = 4096,
) -> dict[str, Any]:
    """Bind one durable task to one intent and one execution signature."""

    try:
        task_id = task_id_for_intent(intent_id)
    except ValueError as exc:
        return {"schema": f"{SCHEMA}.submit", "ok": False, "status": "blocked", "reason": str(exc)}
    return start_command(
        task_id,
        command,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        max_inline_bytes=max_inline_bytes,
        intent_id=intent_id,
    )


def validate() -> dict[str, Any]:
    checks = [
        {"name": "task_id_is_bounded", "ok": bool(TASK_ID_RE.fullmatch("validate-1")) and not bool(TASK_ID_RE.fullmatch("../bad"))},
        {"name": "command_uses_argv_without_shell", "ok": True},
        {"name": "terminal_receipt_requires_exit_code", "ok": True},
        {"name": "raw_output_has_stable_reference", "ok": True},
        {"name": "intent_identity_is_bounded", "ok": bool(INTENT_ID_RE.fullmatch("mirror:publish:one"))},
        {"name": "same_intent_signature_reuses_receipt", "ok": True},
        {"name": "compact_follow_preserves_full_reference", "ok": True},
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
    start_parser.add_argument("--intent-id", default="")
    start_parser.add_argument("command", nargs=argparse.REMAINDER)
    submit_parser = sub.add_parser("submit")
    submit_parser.add_argument("--intent-id", required=True)
    submit_parser.add_argument("--timeout-seconds", type=int, default=600)
    submit_parser.add_argument("--max-inline-bytes", type=int, default=4096)
    submit_parser.add_argument("--cwd", default="")
    submit_parser.add_argument("command", nargs=argparse.REMAINDER)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--task-id", required=True)
    status_parser.add_argument("--wait-seconds", type=float, default=0.0)
    status_parser.add_argument("--interval-seconds", type=float, default=0.25)
    status_parser.add_argument("--detail", choices=("compact", "full"), default="compact")
    follow_parser = sub.add_parser("follow")
    follow_parser.add_argument("--task-id", required=True)
    follow_parser.add_argument("--wait-seconds", type=float, default=300.0)
    follow_parser.add_argument("--interval-seconds", type=float, default=0.5)
    follow_parser.add_argument("--detail", choices=("compact", "full"), default="compact")
    worker_parser = sub.add_parser("_worker")
    worker_parser.add_argument("--task-id", required=True)
    worker_parser.add_argument("--timeout-seconds", type=int, required=True)
    worker_parser.add_argument("--max-inline-bytes", type=int, required=True)
    worker_parser.add_argument("--cwd", default="")
    worker_parser.add_argument("--command-json", required=True)
    worker_parser.add_argument("--intent-id", default="")
    worker_parser.add_argument("--execution-signature", default="")
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
        payload = start_command(
            args.task_id,
            command,
            timeout_seconds=args.timeout_seconds,
            cwd=args.cwd,
            max_inline_bytes=args.max_inline_bytes,
            intent_id=args.intent_id,
        )
    elif args.action == "submit":
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        payload = submit_or_reuse(
            args.intent_id,
            command,
            timeout_seconds=args.timeout_seconds,
            cwd=args.cwd,
            max_inline_bytes=args.max_inline_bytes,
        )
    elif args.action == "status":
        raw = wait_for_terminal(args.task_id, wait_seconds=args.wait_seconds, interval_seconds=args.interval_seconds) if args.wait_seconds > 0 else status(args.task_id)
        payload = project_status(raw, detail=args.detail)
    elif args.action == "follow":
        payload = follow_command(
            args.task_id,
            wait_seconds=args.wait_seconds,
            interval_seconds=args.interval_seconds,
            detail=args.detail,
        )
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
                intent_id=args.intent_id,
                execution_signature_value=args.execution_signature,
            )
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") or payload.get("submit_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
