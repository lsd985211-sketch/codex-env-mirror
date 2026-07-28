#!/usr/bin/env python3
"""Typed full-restart request lifecycle for Codex Desktop.

Ownership: one-time request creation, Task Scheduler handoff, and receipt
acceptance on behalf of the WSL workspace owner.
Non-goals: process termination, scheduled-task creation, Desktop launch logic,
or Git classification.
State behavior: plan/status are read-only; apply writes bounded Windows profile
state and invokes one existing typed Windows operation.
Caller context: wsl_workspace_owner exposes this module as a narrow facade while
the governed Windows launcher consumes the request and owns process behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import codex_desktop_model_runtime
import windows_execution_agent
from shared.backup_router import create_backup


SCHEMA = "desktop_restart_request.v1"
CONFIRM = "RESTART-CODEX-DESKTOP"
TOKEN = "FULL-DESKTOP-RESTART"
OPERATION = "desktop.start_elevated"
DEFAULT_TTL_SECONDS = 300
if os.name == "nt":
    _STATE_ROOT = Path(os.environ.get("USERPROFILE", r"C:\Users\45543")) / ".codex" / "state"
else:
    _STATE_ROOT = Path("/mnt/c/Users/45543/.codex/state")
DEFAULT_REQUEST_PATH = _STATE_ROOT / "codex-desktop-restart-request.json"
DEFAULT_CONSUMING_PATH = _STATE_ROOT / "codex-desktop-restart-request.consuming.json"
DEFAULT_RECEIPT_PATH = _STATE_ROOT / "codex-desktop-restart-receipt.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signature(payload: dict[str, Any]) -> str:
    fields = (
        str(payload.get("schema") or ""),
        str(payload.get("request_id") or ""),
        str(payload.get("requested_at") or ""),
        str(payload.get("expires_at") or ""),
        str(payload.get("reason") or ""),
        str(payload.get("token") or ""),
    )
    return hashlib.sha256("\n".join(fields).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _desktop_process_ids() -> list[int]:
    rows = codex_desktop_model_runtime.query_desktop_host_processes(main_only=True)
    return sorted(
        {
            int(row.get("ProcessId"))
            for row in rows
            if isinstance(row, dict) and str(row.get("ProcessId") or "").isdigit()
        }
    )


def _quit_expression() -> str:
    return r"""
(async () => {
  const bridge = window.electronBridge;
  if (!bridge || typeof bridge.sendMessageFromView !== 'function') {
    return {ok:false, reason:'electron_quit_bridge_unavailable'};
  }
  await bridge.sendMessageFromView({type:'quit-app'});
  return {ok:true, reason:'electron_quit_ipc_sent'};
})()
"""


def signal_graceful_exit(
    request_id: str,
    input_signature: str,
    *,
    consuming_path: Path = DEFAULT_CONSUMING_PATH,
    wait_seconds: float = 20.0,
) -> dict[str, Any]:
    """Ask Electron to exit through CDP and prove that its process family stopped."""

    request = _read_json(consuming_path)
    expected_signature = _signature(request) if request else ""
    try:
        expires_at = datetime.fromisoformat(str(request.get("expires_at") or ""))
    except ValueError:
        expires_at = datetime.min.replace(tzinfo=timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    valid = bool(
        request.get("schema") == SCHEMA
        and request.get("token") == TOKEN
        and str(request.get("request_id") or "") == str(request_id or "")
        and str(request.get("input_signature") or "") == str(input_signature or "")
        and str(input_signature or "") == expected_signature
        and expires_at > datetime.now(timezone.utc)
    )
    base = {
        "schema": f"{SCHEMA}.graceful_exit",
        "ok": False,
        "request_id": str(request_id or ""),
        "method": "electron_quit_ipc",
        "force_kill_used": False,
    }
    if not valid:
        return {**base, "reason": "restart_request_invalid_or_expired", "requested": False}

    before_pids = _desktop_process_ids()
    if not before_pids:
        return {
            **base,
            "ok": True,
            "reason": "desktop_already_exited",
            "requested": False,
            "before_pids": [],
            "after_pids": [],
        }

    port, ws_url, pages, reason = codex_desktop_model_runtime._find_codex_page()
    if not ws_url:
        return {
            **base,
            "reason": reason or "desktop_cdp_unavailable",
            "requested": False,
            "before_pids": before_pids,
            "after_pids": before_pids,
            "cdp_port": port,
            "page_count": len(pages),
        }

    client = None
    dispatch_error = ""
    dispatch_result: Any = None
    try:
        client = codex_desktop_model_runtime._CdpClient(ws_url)
        dispatch_result = client.evaluate(_quit_expression())
    except Exception as exc:
        # The native quit path may close the WebSocket before evaluation returns.
        dispatch_error = type(exc).__name__
    finally:
        if client is not None:
            client.close()

    deadline = time.monotonic() + max(1.0, float(wait_seconds))
    after_pids = _desktop_process_ids()
    while after_pids and time.monotonic() < deadline:
        time.sleep(0.25)
        after_pids = _desktop_process_ids()
    exited = not after_pids
    return {
        **base,
        "ok": exited,
        "reason": "desktop_exited_gracefully" if exited else "desktop_remained_after_native_quit",
        "requested": True,
        "before_pids": before_pids,
        "after_pids": after_pids,
        "cdp_port": port,
        "page_count": len(pages),
        "dispatch_result": dispatch_result,
        "dispatch_error": dispatch_error,
    }


def plan(
    *,
    request_path: Path = DEFAULT_REQUEST_PATH,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
) -> dict[str, Any]:
    invocation = windows_execution_agent.invoke_plan(OPERATION)
    blockers = [] if invocation.get("ok") else [{"code": "desktop_launcher_task_unavailable", "detail": invocation}]
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": not blockers,
        "generated_at": now_iso(),
        "request_path": str(request_path),
        "receipt_path": str(receipt_path),
        "ttl_seconds": DEFAULT_TTL_SECONDS,
        "confirmation": CONFIRM,
        "invocation": invocation,
        "blockers": blockers,
        "acceptance": "launcher receipt is completed and the live Desktop git worker recognizes the registered project",
        "force_kill_allowed": False,
    }


def apply(
    confirm: str,
    *,
    reason: str = "reload_desktop_main_process_state",
    request_path: Path = DEFAULT_REQUEST_PATH,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
) -> dict[str, Any]:
    planned = plan(request_path=request_path, receipt_path=receipt_path)
    if confirm != CONFIRM:
        return {**planned, "schema": f"{SCHEMA}.apply", "ok": False, "status": "blocked", "reason": "explicit_confirmation_required"}
    if not planned.get("ok"):
        return {**planned, "schema": f"{SCHEMA}.apply", "ok": False, "status": "blocked", "reason": "launcher_task_unavailable"}

    existing = [str(path) for path in (request_path, receipt_path) if path.exists()]
    backup = (
        create_backup(
            existing,
            category="codex-desktop-restart",
            purpose="before replacing Desktop restart request or receipt",
            trigger="desktop_restart_request.apply",
            remark="Before governed Desktop full restart",
        )
        if existing
        else {"ok": True, "manifest_paths": []}
    )
    if not backup.get("ok"):
        return {**planned, "schema": f"{SCHEMA}.apply", "ok": False, "status": "blocked", "reason": "restart_state_backup_failed", "backup": backup}

    if receipt_path.exists():
        receipt_path.unlink()
    requested_at = datetime.now(timezone.utc)
    request = {
        "schema": SCHEMA,
        "request_id": uuid.uuid4().hex,
        "requested_at": requested_at.isoformat(),
        "expires_at": (requested_at + timedelta(seconds=DEFAULT_TTL_SECONDS)).isoformat(),
        "reason": str(reason or "reload_desktop_main_process_state"),
        "token": TOKEN,
    }
    request["input_signature"] = _signature(request)
    _write_json_atomic(request_path, request)

    invocation_plan = planned["invocation"]
    invocation = windows_execution_agent.invoke(OPERATION, str(invocation_plan.get("confirmation") or ""))
    if not invocation.get("ok"):
        request_path.unlink(missing_ok=True)
        failure = {
            "schema": f"{SCHEMA}.receipt",
            "ok": False,
            "status": "failed",
            "reason": "launcher_task_handoff_failed",
            "request_id": request["request_id"],
            "completed_at": now_iso(),
            "force_kill_used": False,
        }
        _write_json_atomic(receipt_path, failure)
        return {
            **planned,
            "schema": f"{SCHEMA}.apply",
            "ok": False,
            "status": "failed",
            "reason": "launcher_task_handoff_failed",
            "request": request,
            "invocation": invocation,
            "backup": backup,
        }
    return {
        **planned,
        "schema": f"{SCHEMA}.apply",
        "ok": True,
        "status": "accepted",
        "reason": "launcher_task_accepted_restart_request",
        "request": request,
        "invocation": invocation,
        "backup": {"ok": True, "manifest_paths": backup.get("manifest_paths", [])},
        "business_result_consumed": False,
        "next_action": "wait for the launcher receipt, then consume the live Desktop Git worker result",
    }


def status(
    *,
    desktop_snapshot: Callable[[], dict[str, Any]],
    request_path: Path = DEFAULT_REQUEST_PATH,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
) -> dict[str, Any]:
    request = _read_json(request_path)
    receipt = _read_json(receipt_path)
    live = desktop_snapshot()
    receipt_complete = bool(receipt.get("ok") and receipt.get("status") == "completed")
    classifier_ready = bool(live.get("desktop_classifier_recognized"))
    ready = receipt_complete and classifier_ready
    return {
        "schema": f"{SCHEMA}.status",
        "ok": True,
        "generated_at": now_iso(),
        "ready": ready,
        "status": "completed" if ready else "pending" if request or receipt.get("status") == "relaunch_pending" else "failed" if receipt else "idle",
        "request": request,
        "receipt": receipt,
        "desktop": live,
        "acceptance": {
            "launcher_receipt_complete": receipt_complete,
            "desktop_classifier_recognized": classifier_ready,
            "force_kill_used": bool(receipt.get("force_kill_used", False)),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Governed Codex Desktop restart request operations")
    sub = parser.add_subparsers(dest="command", required=True)
    signal = sub.add_parser("signal-exit")
    signal.add_argument("--request-id", required=True)
    signal.add_argument("--input-signature", required=True)
    signal.add_argument("--wait-seconds", type=float, default=20.0)
    args = parser.parse_args(argv)
    payload = signal_graceful_exit(
        args.request_id,
        args.input_signature,
        wait_seconds=args.wait_seconds,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
