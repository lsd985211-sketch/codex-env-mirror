#!/usr/bin/env python3
"""CLI for the OpenClaw Weixin mobile bridge shadow queue."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
CDP_ENDPOINT_STATE = ROOT / "runtime" / "codex_cdp_endpoint.json"
WECOM_BRIDGE = PROJECT_ROOT / "_bridge" / "mobile_wecom_bridge"
if str(WECOM_BRIDGE) not in sys.path:
    sys.path.insert(0, str(WECOM_BRIDGE))
FILE_TOOLKIT = PROJECT_ROOT / "_bridge" / "file_toolkit"
if str(FILE_TOOLKIT.parent) not in sys.path:
    sys.path.insert(0, str(FILE_TOOLKIT.parent))
BRIDGE_ROOT = PROJECT_ROOT / "_bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from mobile_queue import MobileQueue  # noqa: E402
import permission_policy  # noqa: E402
from file_toolkit import analyze_path, preview_path  # noqa: E402
from resource_fetcher import (  # noqa: E402
    ResourceIntent,
    ResourceRequest,
    acquire_local_resource,
    acquire_resource_with_policy,
    append_resource_log,
)
from shared.backup_router import create_backup as create_routed_backup  # noqa: E402
import attachment_resources  # noqa: E402
from attachment_resources import (  # noqa: E402
    ascii_safe_filename,
    describe_attachment,
    is_ascii_safe_filename,
    materialize_attachments,
    parse_attachments_json,
    safe_local_path,
    sha256_file,
    task_attachments,
    task_has_attachments,
)
from cli_utils import parse_iso_datetime, print_json, sha256_text, utc_now  # noqa: E402
from codegraph_fallback_cli import register_codegraph_fallback_parser, run_codegraph_fallback  # noqa: E402
from codex_session_owned_result import (  # noqa: E402
    find_owned_result as find_codex_session_owned_result,
    is_usable_owned_result_text,
)
from control_command_parser import exact_control_command, parse_repair_control_command  # noqa: E402
from control_message_runtime import run_control_message_handler  # noqa: E402
from regression_checks_capability import run_capability_passphrase_regression_check  # noqa: E402
from regression_checks_cdp_delivery import run_cdp_delivery_regression_check  # noqa: E402
from regression_checks_reply_pending import run_reply_pending_regression_check  # noqa: E402
from regression_checks_control_contracts import run_control_contract_regression_check  # noqa: E402
from regression_checks_route_thread import run_route_thread_regression_check  # noqa: E402
from control_message_handlers import (  # noqa: E402
    control_receipt_id,
    control_reply_task,
    send_control_reply as send_control_reply_with_receipt,
)
from control_reply_text import (  # noqa: E402
    compact_repair_reply_text,
    compact_system_maintenance_reply_text,
    issue_codes_from_diagnosis,
)
from delivery_runtime_state import (  # noqa: E402
    active_route_lease_expired,
    active_slot_release_after_seconds,
    add_coalesced_event,
    clear_delivery_retry,
    clear_thread_recovery,
    delivery_retry_reason_allows_batch,
    delivery_retry_seconds,
    delivery_retry_seconds_for_reason,
    event_coalesce_key,
    get_delivery_retry,
    get_thread_recovery,
    mark_delivery_retry,
    mark_thread_recovery,
)
from enqueue_command_cli import run_enqueue_command  # noqa: E402
from codex_cdp_route import (  # noqa: E402
    cdp_recovery_plan,
    cdp_route_quick_check,
    cdp_startup_contract_check,
    check_codex_health_cdp,
    codex_cdp_config,
    ensure_codex_cdp,
)
from codex_app_server_route import (  # noqa: E402
    app_server_config,
    codex_app_server_owner_report,
    create_codex_thread_app_server,
    desktop_sync_check_app_server,
    ensure_codex_app_server,
    inspect_codex_thread_app_server,
    restart_codex_app_server_for_mcp,
    run_codex_app_server_client,
    stop_codex_app_server_listener,
)
from repair_evidence import (  # noqa: E402
    quick_active_repair_evidence,
    quick_reply_backlog_evidence as read_quick_reply_backlog_evidence,
    quick_supplement_repair_evidence,
    snapshot_active_task_ids,
    snapshot_reply_task_ids,
)
from health_checks import (  # noqa: E402
    attachments_health,
    codex_logs_sqlite_health,
    config_health,
    http_health,
    http_json,
    inspect_openclaw_gateway_processes,
    inspect_scheduled_task,
    inspect_worker_processes,
    latest_worker_log_summary,
    latest_worker_stderr,
    path_health,
    powershell_json,
    scheduled_task_action_health,
    scheduled_task_script_health,
    sqlite_health,
    tcp_check,
)
from historical_recovery_cli import register_historical_recovery_parsers, run_historical_recovery_command  # noqa: E402
from mobile_maintenance import (  # noqa: E402
    codex_mcp_config_health,
    control_reply_receipt_health,
    diagnose_system,
    doctor_report,
    gui_automation_health,
    inspect_system,
    iteration_gate_report,
    os_port_listener_state,
    probe_evidence_state,
    recover_reply_sending_leases,
    repair_report,
    repair_codex_mcp_config,
    repair_codex_plugin_enablement,
    visibility_unconfirmed_reply_pending_candidates,
)
from codex_plugin_config_health import codex_plugin_config_health  # noqa: E402
import capability_tokens  # noqa: E402
import capability_passphrase_text  # noqa: E402
import final_reply_classification  # noqa: E402
import reply_status_text  # noqa: E402
from supplement_runtime import SupplementRuntimeDependencies, release_invalid_published_supplements_impl  # noqa: E402
from regression_checks_owned_result import run_owned_result_regression_check  # noqa: E402
from regression_checks_supplement import run_supplement_regression_check  # noqa: E402
from regression_checks_scheduling import run_scheduling_regression_check  # noqa: E402
from backup_command_cli import register_backup_command_parsers, run_backup_hygiene_command, run_backup_router_command  # noqa: E402
from bridge_control_cli import register_bridge_control_parsers, run_bridge_control_command  # noqa: E402
from bridge_maintenance_cli import register_bridge_maintenance_parser, run_bridge_maintenance_command  # noqa: E402
from capability_token_cli import register_capability_token_parser, run_capability_token_command  # noqa: E402
from maintenance_command_cli import (  # noqa: E402
    register_maintenance_command_parsers,
    run_bridge_db_command,
    run_codex_config_guard_command,
    run_defender_governance_command,
    run_email_scheduler_command,
    run_performance_command,
    run_resource_process_command,
    run_source_scan_command,
)
from mcp_dispatch_gate import current_mcp_session_gate_for_dispatch  # noqa: E402
from mobile_cli_command_specs import register_simple_check_commands  # noqa: E402
from mcp_session_cli import register_mcp_session_parser, run_mcp_session_command  # noqa: E402
from thread_prewarm_state import (  # noqa: E402
    clear_thread_prewarm,
    get_thread_prewarm,
    mark_thread_prewarm,
    thread_prewarm_budget_seconds,
    thread_prewarm_cooldown_seconds,
)
from mobile_mcp_fallback_client import (  # noqa: E402
    mobile_mcp_stdio_tool_call,
    supplement_fallback_ack_message,
    supplement_fallback_get_pending_batch,
)
from mobile_prompt_contract import (  # noqa: E402
    build_task_prompt,
    make_mobile_batch_id,
    mobile_ack_codes_arg,
    mobile_protocol,
    mobile_protocols,
    mobile_result_codes_arg,
    mobile_result_marker,
    strip_mobile_result_markers,
    validate_final_reply_prompt_contract,
)
from openclaw_accounts import (  # noqa: E402
    configured_openclaw_account_ids,
    enrich_allowed_users_from_openclaw_accounts,
    is_openclaw_bound_user,
    openclaw_account_user_id,
    openclaw_context_token_for_user,
    permission_account_map,
    read_openclaw_account,
    receiver_account_id,
)
from queue_command_cli import register_queue_command_parsers, run_queue_command  # noqa: E402
from reply_command_cli import register_reply_command_parsers, run_reply_command  # noqa: E402
from simple_check_handlers import build_simple_check_command_handlers  # noqa: E402
from thread_route_state import (  # noqa: E402
    active_thread_key,
    cdp_start_probe_key,
    clear_waiting_thread_selection,
    continuation_key,
    continuation_window_seconds,
    default_thread_id,
    delivery_retry_key,
    find_thread,
    find_thread_for_external_user,
    get_active_thread,
    is_waiting_thread_selection,
    mark_waiting_thread_selection,
    pending_thread_selection_key,
    set_active_thread,
    thread_items,
    thread_menu_text,
    thread_prewarm_key,
    thread_switch_trigger,
    weixin_send_circuit_key,
    weixin_status_ack_circuit_key,
)
from thread_route_cli import register_thread_route_parsers, run_thread_route_command  # noqa: E402
from tool_health_cli import register_tool_health_parsers, run_tool_health_command  # noqa: E402
from worker_loop_cli import register_worker_loop_parsers, run_worker_command  # noqa: E402
from worker_active_recovery import (  # noqa: E402
    ActiveRecoveryDependencies,
    recover_active_codex_tasks_impl,
)
from worker_dispatch_permission import enforce_worker_dispatch_permission  # noqa: E402
from worker_loop_observability import (  # noqa: E402
    worker_loop_has_activity,
    worker_loop_should_log,
    worker_loop_summary,
)
from worker_loop_runtime import WorkerLoopDependencies, worker_once_impl  # noqa: E402


DEFAULT_CONFIG = ROOT / "config.local.json"
DEFAULT_EXAMPLE_CONFIG = ROOT / "config.example.json"
DEFAULT_DB = ROOT / "mobile_openclaw_bridge.db"
ATTACHMENTS_DIR = ROOT / "attachments"
OUTBOUND_MEDIA_SPOOL_DIR = ATTACHMENTS_DIR / "outbound-media"
RESOURCE_LOG = ROOT / "logs" / "resource-fetcher.jsonl"
STOP_REQUEST = ROOT / "STOP_REQUEST"
DEFAULT_TASK_NAME = "MobileOpenClawBridgeWorker"
DEFAULT_GATEWAY_TASK_NAME = "OpenClawGatewayWorker"
MAX_ATTACHMENT_PREVIEW_CHARS = 1200
MAX_ATTACHMENT_COPY_BYTES = 100 * 1024 * 1024
DEFAULT_SESSION_OWNED_RESULT_NEGATIVE_CACHE_SECONDS = 30
INBOUND_MESSAGE_RE = re.compile(
    r"inbound message: from=(?P<from>\S+)\s+types=(?P<types>\S+)"
)
INBOUND_DETAIL_RE = re.compile(
    r"inbound: from=(?P<from>\S+)\s+to=(?P<to>\S+)\s+bodyLen=(?P<body_len>\d+)\s+hasMedia=(?P<has_media>\S+)"
)
LOG_STOP_RE = re.compile(
    r"(?:from=(?P<from>\S+).{0,200}(?:content|text|body)=['\"]?stop['\"]?|(?:content|text|body)=['\"]?stop['\"]?.{0,200}from=(?P<from2>\S+))",
    re.IGNORECASE,
)
OUTBOUND_MEDIA_RE = re.compile(
    r"\[\[(?:mobile_media_file|mobile_media|weixin_media):(?P<path>[^\]]+)\]\]"
)
OUTBOUND_MEDIA_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:MEDIA|WEIXIN_MEDIA)[ \t]*:[ \t]*(?P<path>.+?)[ \t]*$"
)
CAPABILITY_PASSPHRASE_WAIT_MINUTES = 10


class TemporaryStopRequestPath:
    """Point temp-only regression checks at an isolated STOP_REQUEST path."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.original = STOP_REQUEST

    def __enter__(self) -> "TemporaryStopRequestPath":
        globals()["STOP_REQUEST"] = self.path
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        globals()["STOP_REQUEST"] = self.original
REPLY_SENDING_LEASE_SECONDS = 300
STATUS_ACK_SENDING_LEASE_SECONDS = 300
SUPPLEMENT_ACK_GRACE_SECONDS = 90


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_config(path: Path) -> dict[str, Any]:
    if path.exists():
        config = load_json(path)
    else:
        config = load_json(DEFAULT_EXAMPLE_CONFIG)
    if not config:
        config = {}
    db_path = config.get("queue", {}).get("db_path")
    if db_path and not Path(db_path).is_absolute():
        config.setdefault("queue", {})["db_path"] = str(ROOT / db_path)
    enrich_allowed_users_from_openclaw_accounts(config)
    return config


def save_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def backup_file_for_action(path: Path, action: str) -> str:
    if not path.exists():
        return ""
    result = create_routed_backup(
        [str(path)],
        category="bridge",
        purpose=action,
        trigger="mobile-openclaw-cli",
        remark=f"mobile-openclaw-{action}",
    )
    if not result.get("ok"):
        raise RuntimeError(f"routed backup failed for {path}: {result.get('reason') or 'unknown'}")
    items = result.get("items") or []
    if not items:
        raise RuntimeError(f"routed backup returned no item for {path}")
    return str(items[0].get("backup_path") or "")


def sync_openclaw_accounts_to_bridge_users(
    queue: MobileQueue,
    config: dict[str, Any],
    require_thread_route: bool = False,
) -> dict[str, Any]:
    synced: list[dict[str, str]] = []
    skipped: list[str] = []
    for account_id in configured_openclaw_account_ids(config):
        account = read_openclaw_account(config, account_id)
        user_id = str(account.get("userId") or "").strip()
        token = str(account.get("token") or "").strip()
        if not user_id or not token:
            if account_id:
                skipped.append(account_id)
            continue
        if require_thread_route:
            runtime_value = queue.runtime_get(active_thread_key(user_id))
            runtime_route = find_thread(config, runtime_value) if runtime_value else None
            route = runtime_route or find_thread_for_external_user(config, user_id) or find_thread(
                config,
                onboarding_thread_placeholder_id(user_id),
            )
            if not route:
                skipped.append(f"{account_id}:missing_thread_route")
                continue
        queue.ensure_user("openclaw-weixin", user_id, allow_trigger=True)
        synced.append({"account_id": account_id, "external_user": user_id})
    signature = hashlib.sha256(json.dumps(synced, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    signature_key = "openclaw_accounts_sync_signature"
    previous_signature = str(queue.runtime_get(signature_key) or "")
    if synced and previous_signature != signature:
        queue.runtime_set(signature_key, signature)
        queue.add_event(
            "local",
            "openclaw_accounts_synced",
            {"synced_count": len(synced), "synced": synced, "skipped_empty_slots": skipped, "signature": signature},
        )
    return {
        "ok": True,
        "synced_count": len(synced),
        "synced": synced,
        "skipped_empty_slots": skipped,
        "require_thread_route": bool(require_thread_route),
        "changed": previous_signature != signature,
    }


def delivery_mode_for_task(config: dict[str, Any], task: dict[str, Any]) -> str:
    """Primary is a visible desktop remote-control path; backups stay background."""
    account_id = receiver_account_id(
        config,
        str(task.get("receiver_account_id") or ""),
        str(task.get("external_user") or ""),
    )
    if account_id == "primary":
        return "codex-cdp"
    return str(config.get("trigger", {}).get("delivery_mode") or "stub").lower()


def task_delivery_config(config: dict[str, Any], delivery_mode: str) -> dict[str, Any]:
    dispatch_config = dict(config)
    dispatch_config["trigger"] = dict(config.get("trigger", {}))
    dispatch_config["trigger"]["delivery_mode"] = delivery_mode
    dispatch_config["trigger"]["auto_reply"] = False
    return dispatch_config


def task_route_key(delivery_mode: str, thread_id: str) -> str:
    return f"{str(delivery_mode or '').lower()}:{str(thread_id or '')}"


def task_route_identity(task: dict[str, Any], delivery_mode: str, thread_id: str) -> tuple[str, str, str]:
    return (
        str(task.get("external_user") or ""),
        str(task.get("receiver_account_id") or ""),
        task_route_key(delivery_mode, thread_id),
    )


def runtime_acquire_lease(queue: MobileQueue, key: str, payload: dict[str, Any], lease_seconds: int) -> dict[str, Any]:
    if not key:
        return {"reserved": True}
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=max(1, int(lease_seconds or 1)))).isoformat()
    value = dict(payload)
    value["started_at"] = now.isoformat()
    value["expires_at"] = expires_at
    with queue.session() as db:
        row = db.execute(
            """
            INSERT INTO mobile_runtime(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            RETURNING value
            """,
            (key, json.dumps(value, ensure_ascii=False), now.isoformat()),
        ).fetchone()
        if row:
            return {"reserved": True, "key": key, "expires_at": expires_at}
        existing = db.execute("SELECT value FROM mobile_runtime WHERE key=?", (key,)).fetchone()
        existing_value = str(existing["value"] or "") if existing else ""
        try:
            existing_payload = json.loads(existing_value or "{}")
        except json.JSONDecodeError:
            existing_payload = {}
        existing_expires_at = parse_iso_datetime(str(existing_payload.get("expires_at") or ""))
        if existing_expires_at and existing_expires_at > now:
            return {
                "reserved": False,
                "duplicate": True,
                "key": key,
                "expires_at": str(existing_payload.get("expires_at") or ""),
            }
        db.execute(
            """
            UPDATE mobile_runtime
            SET value=?, updated_at=?
            WHERE key=?
            """,
            (json.dumps(value, ensure_ascii=False), now.isoformat(), key),
        )
    return {"reserved": True, "key": key, "expires_at": expires_at}


def status_ack_send_key(task_id: str, event_type: str, text: str = "") -> str:
    return f"status_ack_sending:{str(task_id or '').strip()}:{str(event_type or '').strip()}"


def reserve_status_ack_send(queue: MobileQueue, task_id: str, event_type: str, text: str) -> dict[str, Any]:
    if not task_id:
        return {"reserved": True}
    return runtime_acquire_lease(
        queue,
        status_ack_send_key(task_id, event_type, text),
        {
            "task_id": task_id,
            "event_type": event_type,
            "text_sha256": hashlib.sha256(str(text or "").encode("utf-8")).hexdigest(),
        },
        STATUS_ACK_SENDING_LEASE_SECONDS,
    )


def status_ack_already_sent(queue: MobileQueue, task_id: str, event_type: str) -> bool:
    if not task_id or not event_type:
        return False
    with queue.session() as db:
        row = db.execute(
            """
            SELECT payload_json
            FROM mobile_events
            WHERE task_id=? AND event_type=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id, event_type),
        ).fetchone()
    if not row:
        return False
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict) or not payload.get("ok"):
        return False
    reply = payload.get("reply")
    if isinstance(reply, dict):
        return bool(reply.get("ok") or reply.get("deliveryAccepted"))
    return True


def sort_pending_by_route_fairness(
    queue: MobileQueue,
    config: dict[str, Any],
    pending: list[dict[str, Any]],
    task_id: str = "",
) -> list[dict[str, Any]]:
    """Group pending tasks by route and rotate across routes fairly.

    The worker should not let one hot route monopolize the visible scan window.
    This keeps backup accounts from being starved when the primary route has a
    deep backlog or repeated retries.
    """
    route_groups: dict[str, list[dict[str, Any]]] = {}
    route_order: list[str] = []
    route_last_updated: dict[str, str] = {}
    for task in pending:
        delivery_mode = delivery_mode_for_task(config, task)
        active_thread = get_active_thread(queue, config, str(task.get("external_user") or ""), use_default=False)
        thread_id = effective_task_thread_id(queue, config, task, active_thread)
        route_key = task_route_key(delivery_mode, thread_id)
        if route_key not in route_groups:
            route_groups[route_key] = []
            route_order.append(route_key)
        route_groups[route_key].append(task)
        order_key = pending_task_order_key(queue, task)
        current_route_key = route_last_updated.get(route_key, "")
        if not current_route_key or order_key < current_route_key:
            route_last_updated[route_key] = order_key
    if len(route_order) <= 1:
        return sorted(pending, key=lambda item: pending_task_order_key(queue, item))
    for tasks in route_groups.values():
        tasks.sort(key=lambda item: pending_task_order_key(queue, item))
    preferred_route_key = ""
    if task_id:
        for route_key, tasks in route_groups.items():
            if any(str(item.get("id") or "") == task_id for item in tasks):
                preferred_route_key = route_key
                break
    if not preferred_route_key and route_order:
        preferred_route_key = min(
            route_order,
            key=lambda key: route_last_updated.get(key, ""),
        )
    ordered_routes = [preferred_route_key] if preferred_route_key in route_groups else []
    ordered_routes.extend(route_key for route_key in route_order if route_key != preferred_route_key)
    merged: list[dict[str, Any]] = []
    route_positions = {route_key: 0 for route_key in ordered_routes}
    while True:
        progressed = False
        for route_key in ordered_routes:
            tasks = route_groups.get(route_key) or []
            index = route_positions.get(route_key, 0)
            if index >= len(tasks):
                continue
            merged.append(tasks[index])
            route_positions[route_key] = index + 1
            progressed = True
        if not progressed:
            break
    return merged


def is_primary_admin_user(config: dict[str, Any], external_user: str) -> bool:
    return permission_policy.role_for_actor(config, external_user, permission_account_map(config)) == "admin"


def queue_from_config(config: dict[str, Any]) -> MobileQueue:
    db_path = config.get("queue", {}).get("db_path") or str(DEFAULT_DB)
    queue_config = dict(config)
    queue_config["_is_external_user_allowed"] = lambda external_user: is_openclaw_bound_user(config, external_user)
    return MobileQueue(db_path, config=queue_config)


def db_path_from_config(config: dict[str, Any]) -> str:
    return str(config.get("queue", {}).get("db_path") or str(DEFAULT_DB))


def active_system_maintenance_lock() -> dict[str, Any] | None:
    lock_path = Path(r"C:\Users\45543\Desktop\Codex资源库\文档\系统维护\运行态\performance-maintenance.lock")
    if not lock_path.exists():
        return None
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return {"lock_path": str(lock_path), "reason": "lock_file_unreadable"}
    pid_text = str(payload.get("pid") or "").strip()
    if not pid_text.isdigit():
        return {"lock_path": str(lock_path), "reason": "lock_file_without_pid", "payload": payload}
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"if (Get-Process -Id {int(pid_text)} -ErrorAction SilentlyContinue) {{ 'true' }} else {{ 'false' }}",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=5,
        )
    except Exception:
        return {"lock_path": str(lock_path), "reason": "lock_probe_failed", "payload": payload}
    if "true" not in (proc.stdout or "").lower():
        return None
    payload["lock_path"] = str(lock_path)
    return payload


def run_mobile_system_maintenance_control(
    apply_safe: bool = True,
    *,
    external_user: str = "",
    account_id: str = "",
) -> dict[str, Any]:
    request_id = f"mobile-repair-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    active_lock = active_system_maintenance_lock()
    if active_lock:
        return {
            "ok": True,
            "control": "repair",
            "mode": "system",
            "started": False,
            "skipped": True,
            "reason": "maintenance_already_running",
            "request_id": request_id,
            "active_lock": active_lock,
            "policy": "mobile repair dedupes while total computer maintenance lock is active",
        }
    runtime_dir = PROJECT_ROOT / "_bridge" / "mobile_openclaw_bridge" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_path = runtime_dir / f"{request_id}.log"
    command = [
        sys.executable,
        "_bridge/shared/performance_maintenance_job.py",
        "--trigger-source",
        "mobile-repair",
        "--trigger-user",
        external_user,
        "--trigger-account",
        account_id,
        "--trigger-mode",
        "manual",
        "--request-id",
        request_id,
    ]
    if apply_safe:
        command.append("--apply-safe")
    stdout = None
    try:
        stdout = open(log_path, "a", encoding="utf-8")
        stderr = subprocess.STDOUT
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        proc = subprocess.Popen(
            command,
            stdout=stdout,
            stderr=stderr,
            cwd=str(PROJECT_ROOT),
            creationflags=creationflags,
        )
    except Exception as exc:
        if stdout is not None:
            stdout.close()
        return {
            "ok": False,
            "control": "repair",
            "mode": "system",
            "started": False,
            "request_id": request_id,
            "command": command,
            "log_path": str(log_path),
            "error": f"{type(exc).__name__}: {exc}",
        }
    if stdout is not None:
        stdout.close()
    return {
        "ok": True,
        "control": "repair",
        "mode": "system",
        "started": True,
        "async": True,
        "pid": int(proc.pid),
        "request_id": request_id,
        "command": command,
        "log_path": str(log_path),
        "policy": "mobile repair starts total computer maintenance asynchronously; job lock handles cooldown/dedupe",
    }


def quick_reply_backlog_evidence(queue: MobileQueue, limit: int = 20) -> dict[str, Any]:
    return read_quick_reply_backlog_evidence(queue, visibility_unconfirmed_reply_pending_candidates, limit=limit)


def scoped_backlog_repair(queue: MobileQueue, apply_safe: bool) -> tuple[list[str], dict[str, Any]]:
    actions_taken: list[str] = []
    visibility = visibility_unconfirmed_reply_pending_candidates(queue)
    if visibility:
        actions_taken.append("visibility_unconfirmed_candidates_observed")
    if apply_safe:
        stale_result = recover_stale_reply_sending_tasks(queue)
    else:
        stale_result = recover_reply_sending_leases(queue, apply=False)
    recovered = int(stale_result.get("recovered_count") or stale_result.get("expired_count") or 0)
    if apply_safe and recovered:
        actions_taken.append("recover_stale_reply_sending")
    return actions_taken, {
        "stale_reply_sending": stale_result,
        "visibility_unconfirmed_task_ids": [str(item.get("id") or "") for item in visibility[:20]],
    }


def scoped_plugin_repair(config: dict[str, Any], apply_safe: bool) -> tuple[list[str], dict[str, Any]]:
    actions_taken: list[str] = []
    mcp_health = codex_mcp_config_health(config)
    plugin_health = codex_plugin_config_health(run_cli=False)
    mcp_repair = repair_codex_mcp_config(config, mcp_health, apply=bool(apply_safe))
    plugin_repair = repair_codex_plugin_enablement(config, plugin_health, apply=bool(apply_safe))
    if mcp_repair.get("applied"):
        actions_taken.append("repair_codex_mcp_config")
    if plugin_repair.get("applied"):
        actions_taken.append("repair_codex_plugin_enablement")
    return actions_taken, {
        "mcp_config": {
            "ok": bool(mcp_health.get("ok")),
            "repairable_missing": [item.get("name") for item in mcp_health.get("repairable_missing", []) if isinstance(item, dict)],
            "repairable_drifted": [item.get("name") for item in mcp_health.get("repairable_drifted", []) if isinstance(item, dict)],
            "repair": mcp_repair,
        },
        "plugins": {
            "ok": bool(plugin_health.get("ok")),
            "missing_enabled_plugins": plugin_health.get("missing_enabled_plugins") or [],
            "repair": plugin_repair,
        },
    }


def mobile_repair_specialized_report(
    queue: MobileQueue,
    config: dict[str, Any],
    mode: str,
    apply_safe: bool = True,
) -> dict[str, Any]:
    """Run a bounded scoped repair/check without broadening maintenance powers."""
    mode = mode or "safe"
    actions_taken: list[str] = []
    actions_blocked: list[str] = []
    next_step = ""
    report: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}
    diagnosis: dict[str, Any] = {}
    evidence: dict[str, Any] = {}

    if mode == "last":
        recent = queue.list_tasks(5)
        active_evidence = quick_active_repair_evidence(queue, limit=10)
        backlog_evidence = quick_reply_backlog_evidence(queue, limit=10)
        evidence = {
            "latest_task_ids": [str(item.get("id") or "") for item in recent if isinstance(item, dict)],
            "active_task_ids": active_evidence.get("active_task_ids", [])[:5],
            "reply_task_ids": backlog_evidence.get("reply_task_ids", [])[:5],
        }
        actions_blocked.append("no blind resend of the latest task")
        next_step = "已检查最近任务、active 和 reply backlog；如需处理具体任务，用更窄模式或 task_id。"
    elif mode == "active":
        active_evidence = quick_active_repair_evidence(queue, limit=30)
        report = {"active": active_evidence}
        evidence = {
            "active_task_ids": active_evidence.get("active_task_ids", []),
            "pending_task_ids": active_evidence.get("pending_task_ids", []),
            "status_counts": active_evidence.get("status_counts", {}),
        }
        actions_blocked.extend([
            "no active task forced failed/cancelled",
            "no sent_to_codex task moved back to pending",
            "no interrupt/continuation submitted from mobile scoped repair",
        ])
        next_step = "active 恢复继续由 worker 的 bounded recovery 执行；本命令只给出当前证据和计划。"
    elif mode == "cdp":
        quick = cdp_route_quick_check(config)
        plan = cdp_recovery_plan(config)
        evidence = {
            "cdp_quick_ok": bool(quick.get("ok")),
            "cdp_layer": quick.get("layer") or quick.get("reason") or "",
            "plan_ok": bool(plan.get("ok")),
        }
        report = {"quick": quick, "plan": plan}
        actions_blocked.extend(["no primary route switch", "no plain non-admin Codex launch"])
        next_step = "如需修复 CDP，只使用配置里的受控启动脚本和 CDP 合约检查。"
    elif mode == "backlog":
        backlog_evidence = quick_reply_backlog_evidence(queue, limit=50)
        scoped_actions, scoped_report = scoped_backlog_repair(queue, apply_safe=bool(apply_safe))
        actions_taken.extend(scoped_actions)
        report = {"backlog": backlog_evidence, "repair": scoped_report}
        evidence = {
            "reply_task_ids": backlog_evidence.get("reply_task_ids", []),
            "visibility_unconfirmed_task_ids": backlog_evidence.get("visibility_unconfirmed_task_ids", []),
            "push_status_counts": backlog_evidence.get("push_status_counts", {}),
        }
        actions_blocked.append("no Weixin reply send without explicit include-reply-send permission")
        next_step = "需要真正发送 reply backlog 时，必须另行显式授权发送。"
    elif mode == "supplement":
        supplement_evidence = quick_supplement_repair_evidence(queue, limit=50)
        report = {"supplement": supplement_evidence}
        evidence = {
            "supplement_task_ids": supplement_evidence.get("supplement_task_ids", []),
            "runtime_keys": supplement_evidence.get("runtime_keys", [])[:5],
            "pending_task_ids": supplement_evidence.get("pending_task_ids", [])[:10],
        }
        actions_blocked.append("no unprocessed supplement ack/drop")
        next_step = "补充信息只做保全和状态诊断；不会为清队列而 ack 未处理内容。"
    elif mode == "plugins":
        scoped_actions, scoped_report = scoped_plugin_repair(config, apply_safe=bool(apply_safe))
        actions_taken.extend(scoped_actions)
        report = {"plugins": scoped_report}
        evidence = {
            "mcp_missing": (scoped_report.get("mcp_config") or {}).get("repairable_missing", []),
            "mcp_drifted": (scoped_report.get("mcp_config") or {}).get("repairable_drifted", []),
            "missing_enabled_plugins": (scoped_report.get("plugins") or {}).get("missing_enabled_plugins", []),
            "plugin_related": bool((scoped_report.get("mcp_config") or {}).get("repairable_missing"))
            or bool((scoped_report.get("mcp_config") or {}).get("repairable_drifted"))
            or bool((scoped_report.get("plugins") or {}).get("missing_enabled_plugins")),
        }
        actions_blocked.append("additive-only; no deletion of new plugin/config entries")
        next_step = "若补齐了 MCP/plugin 配置，通常需要重启 Codex Desktop 才能在当前会话可见。"
    elif mode == "tools":
        tool_report = tool_registry_health(queue, config)
        evidence = {
            "tool_registry_ok": bool(tool_report.get("ok")),
            "recommendation_count": len(tool_report.get("recommendations") if isinstance(tool_report.get("recommendations"), list) else []),
        }
        report = {"tool_registry": tool_report}
        actions_blocked.extend(["no package install", "no PATH mutation", "no tool download"])
        next_step = "工具专项只做探测和建议；安装或补依赖仍需单独批准。"
    else:
        return {
            "ok": False,
            "control": "repair",
            "mode": mode,
            "applied": False,
            "unsupported_mode": True,
            "supported_modes": ["safe", "status", "deep", "last", "active", "cdp", "backlog", "supplement", "plugins", "tools"],
            "reason": "unknown repair mode",
        }

    return {
        "ok": True,
        "control": "repair",
        "mode": mode,
        "applied": bool(actions_taken),
        "specialized_mode": True,
        "summary": f"repair {mode} 专项执行完成。",
        "actions_taken": actions_taken,
        "actions_blocked": actions_blocked,
        "evidence": evidence,
        "next_step": next_step,
        "report": report,
    }


def run_mobile_repair_control(
    queue: MobileQueue,
    config: dict[str, Any],
    mode: str,
    apply_safe: bool = True,
) -> dict[str, Any]:
    mode = mode or "safe"
    specialized_modes = {"last", "active", "cdp", "backlog", "supplement", "plugins", "tools"}
    if mode in specialized_modes:
        return mobile_repair_specialized_report(queue, config, mode, apply_safe=apply_safe)
    if mode not in {"safe", "status", "deep"}:
        return {
            "ok": False,
            "control": "repair",
            "mode": mode,
            "applied": False,
            "unsupported_mode": True,
            "supported_modes": ["safe", "status", "deep", *sorted(specialized_modes)],
            "reason": "specialized repair mode is specified in the design spec but is not wired to a bounded executor yet",
        }
    if mode == "status":
        report = doctor_report(queue, config)
        return {
            "ok": bool(report.get("ok")),
            "control": "repair",
            "mode": mode,
            "applied": False,
            "diagnosis": report.get("diagnosis", report),
            "report": report,
        }
    if mode == "deep":
        report = repair_report(queue, config, apply=False, include_reply_send=False)
        return {
            "ok": bool(report.get("ok")),
            "control": "repair",
            "mode": mode,
            "applied": False,
            "diagnosis": report.get("diagnosis", {}),
            "repair": report.get("repair", {}),
            "advisories": report.get("advisories", []),
            "report": report,
        }
    report = repair_report(queue, config, apply=bool(apply_safe), include_reply_send=False)
    return {
        "ok": bool(report.get("ok")),
        "control": "repair",
        "mode": mode,
        "applied": bool((report.get("repair") or {}).get("applied")),
        "diagnosis": report.get("diagnosis", {}),
        "repair": report.get("repair", {}),
        "advisories": report.get("advisories", []),
        "report": report,
    }


def task_name_from_config(config: dict[str, Any]) -> str:
    return str(config.get("control", {}).get("scheduled_task_name") or DEFAULT_TASK_NAME)


def cdp_start_probe_cooldown_seconds(config: dict[str, Any]) -> int:
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    return max(5, int(trigger.get("codex_cdp_start_probe_cooldown_seconds") or 15))


def visible_cdp_no_owned_result_manual_after_seconds(config: dict[str, Any]) -> int:
    """Bound primary visible-CDP waits after a terminal owned-result protocol failure."""
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    return max(
        active_slot_release_after_seconds(config),
        int(trigger.get("visible_cdp_no_owned_result_manual_after_seconds") or 3600),
    )


def visible_cdp_unverified_submission_attention_after_attempts(config: dict[str, Any]) -> int:
    """Bound repeated visible-CDP submission failures without switching routes."""
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    return max(1, min(10, int(trigger.get("visible_cdp_unverified_submission_attention_after_attempts") or 3)))


def terminal_failed_status(status: str) -> bool:
    return str(status or "") in {"failed", "codex_timeout", "cancelled", "canceled"}


def app_server_no_owned_result_manual_after_attempts(config: dict[str, Any]) -> int:
    """Bound app-server protocol-violation redelivery loops before failing closed."""
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    return max(1, min(10, int(trigger.get("app_server_no_owned_result_manual_after_attempts") or 3)))


def app_server_repair_continuation_after_seconds(config: dict[str, Any]) -> int:
    """Bound how long an acked app-server turn may sit in-progress before repair continuation."""
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    return max(120, int(trigger.get("app_server_repair_continuation_after_seconds") or 600))


def app_server_turn_materialization_grace_seconds(config: dict[str, Any]) -> int:
    """Short wait for app-server turn/start ids to become readable in turns/list."""
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    return max(15, min(180, int(trigger.get("app_server_turn_materialization_grace_seconds") or 60)))


def app_server_unreadable_repair_threshold(config: dict[str, Any]) -> int:
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    return max(2, int(trigger.get("app_server_unreadable_repair_threshold") or 3))


def app_server_unreadable_repair_cooldown_seconds(config: dict[str, Any]) -> int:
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    return max(60, int(trigger.get("app_server_unreadable_repair_cooldown_seconds") or 300))


def app_server_unreadable_repair_key(thread_id: str) -> str:
    safe_thread_id = str(thread_id or "").strip() or "unknown"
    return f"app_server_unreadable_repair:{safe_thread_id}"


def get_app_server_unreadable_repair(queue: MobileQueue, thread_id: str, now: datetime | None = None) -> dict[str, Any]:
    raw = queue.runtime_get(app_server_unreadable_repair_key(thread_id))
    if not raw:
        return {"active": False}
    try:
        data = json.loads(raw)
        retry_after = datetime.fromisoformat(str(data.get("retry_after") or ""))
    except Exception:
        queue.runtime_delete(app_server_unreadable_repair_key(thread_id))
        return {"active": False, "reason": "invalid_repair_marker"}
    now = now or datetime.now(timezone.utc)
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=timezone.utc)
    if now >= retry_after:
        queue.runtime_delete(app_server_unreadable_repair_key(thread_id))
        data["active"] = False
        data["ready"] = True
        return data
    data["active"] = True
    data["remaining_seconds"] = max(0, int((retry_after - now).total_seconds()))
    return data


def active_recovery_retry_key(task_id: str) -> str:
    return f"active_recovery_retry:{task_id}"


def active_recovery_cooldown_seconds(config: dict[str, Any]) -> int:
    return max(1, int(config.get("trigger", {}).get("active_recovery_cooldown_seconds") or 5))


def active_recovery_due(queue: MobileQueue, task_id: str, now: datetime) -> bool:
    raw = str(queue.runtime_get(active_recovery_retry_key(task_id)) or "")
    if not raw:
        return True
    try:
        retry_at = datetime.fromisoformat(raw)
    except Exception:
        queue.runtime_delete(active_recovery_retry_key(task_id))
        return True
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    if now >= retry_at:
        queue.runtime_delete(active_recovery_retry_key(task_id))
        return True
    return False


def mark_active_recovery_cooldown(
    queue: MobileQueue,
    config: dict[str, Any],
    task_id: str,
    now: datetime,
    reason: str,
) -> str:
    retry_at = (now + timedelta(seconds=active_recovery_cooldown_seconds(config))).isoformat()
    queue.runtime_set(active_recovery_retry_key(task_id), retry_at)
    if not task_event_recent(queue, task_id, "active_recovery_retry_scheduled", 60):
        queue.add_event(
            "local",
            "active_recovery_retry_scheduled",
            {"reason": reason, "retry_after": retry_at},
            task_id,
        )
    return retry_at


def select_active_recovery_tasks(
    queue: MobileQueue,
    config: dict[str, Any],
    sent: list[dict[str, Any]],
    max_sent_checks: int,
    now: datetime,
) -> tuple[list[dict[str, Any]], int]:
    """Pick active sent tasks fairly across delivery routes.

    This prevents an old/stale task on one route from starving result recovery
    for newer tasks on other accounts or threads.
    """
    if max_sent_checks <= 0 or not sent:
        return [], len(sent)

    due_by_route: dict[str, list[dict[str, Any]]] = {}
    deferred = 0
    for task in sent:
        tid = str(task.get("id") or "")
        if not tid:
            deferred += 1
            continue
        if not active_recovery_due(queue, tid, now):
            deferred += 1
            continue
        mode = delivery_mode_for_task(config, task)
        route = task_route_key(mode, str(task.get("codex_thread_id") or ""))
        due_by_route.setdefault(route, []).append(task)

    for tasks in due_by_route.values():
        tasks.sort(key=lambda item: str(item.get("sent_to_codex_at") or item.get("updated_at") or item.get("created_at") or ""))

    selected: list[dict[str, Any]] = []
    route_keys = sorted(
        due_by_route,
        key=lambda key: str((due_by_route.get(key) or [{}])[0].get("sent_to_codex_at") or (due_by_route.get(key) or [{}])[0].get("updated_at") or ""),
    )
    positions = {key: 0 for key in route_keys}
    while len(selected) < max_sent_checks:
        progressed = False
        for key in route_keys:
            tasks = due_by_route.get(key) or []
            index = positions.get(key, 0)
            if index >= len(tasks):
                continue
            selected.append(tasks[index])
            positions[key] = index + 1
            progressed = True
            if len(selected) >= max_sent_checks:
                break
        if not progressed:
            break
    return selected, deferred


def get_cdp_start_probe_cooldown(queue: MobileQueue) -> dict[str, Any]:
    raw = queue.runtime_get(cdp_start_probe_key())
    if not raw:
        return {"active": False}
    try:
        data = json.loads(str(raw))
        retry_after = datetime.fromisoformat(str(data.get("retry_after") or ""))
    except Exception:
        queue.runtime_delete(cdp_start_probe_key())
        return {"active": False, "reason": "invalid_cdp_start_probe_marker"}
    now = datetime.now(timezone.utc)
    if now >= retry_after:
        queue.runtime_delete(cdp_start_probe_key())
        data["active"] = False
        data["ready"] = True
        return data
    data["active"] = True
    data["remaining_seconds"] = max(0, int((retry_after - now).total_seconds()))
    return data


def mark_cdp_start_probe_cooldown(queue: MobileQueue, config: dict[str, Any], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    retry_after = (
        datetime.now(timezone.utc) + timedelta(seconds=cdp_start_probe_cooldown_seconds(config))
    ).isoformat()
    payload = {
        "reason": "codex_cdp_start_probe_failed",
        "retry_after": retry_after,
        "detail": detail or {},
    }
    queue.runtime_set(cdp_start_probe_key(), json.dumps(payload, ensure_ascii=False))
    add_coalesced_event(
        queue,
        "local",
        "codex_cdp_start_probe_cooldown",
        payload,
        signature="codex_cdp_start_probe_failed",
    )
    return payload


def pending_task_order_key(queue: MobileQueue, task: dict[str, Any]) -> str:
    """Stable FIFO key for pending dispatch.

    Released active tasks are returned to pending, but their updated_at changes
    during release. Use the pre-release active timestamp from the retry detail
    so the old task remains ahead of later same-route messages.
    """
    retry = get_delivery_retry(queue, str(task.get("id") or ""))
    if str(retry.get("reason") or "") in {
        "active_lease_expired_without_owned_result",
        "terminal_without_owned_result",
        "protocol_violation_no_owned_result",
        "mcp_transport_closed",
        "app_server_mcp_transport_closed",
    }:
        detail = retry.get("detail") if isinstance(retry.get("detail"), dict) else {}
        inner_detail = detail.get("detail") if isinstance(detail.get("detail"), dict) else {}
        candidates = [
            str((inner_detail or {}).get("sent_to_codex_at") or ""),
            str(detail.get("sent_to_codex_at") or ""),
            str(detail.get("created_at") or ""),
            str(task.get("created_at") or ""),
        ]
        for candidate in candidates:
            if candidate:
                return candidate
    return str(task.get("created_at") or task.get("updated_at") or task.get("id") or "")


def include_released_active_pending_tasks(
    queue: MobileQueue,
    pending: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Ensure released active tasks are visible even when the pending queue is deep."""
    existing_ids = {str(task.get("id") or "") for task in pending}
    candidate_ids: list[str] = []
    with queue.session() as db:
        runtime_rows = db.execute(
            """
            SELECT key
            FROM mobile_runtime
            WHERE key LIKE 'delivery_retry:%'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (max(limit * 5, 100),),
        ).fetchall()
    for row in runtime_rows:
        key = str(row["key"] or "")
        task_id = key.split("delivery_retry:", 1)[1] if key.startswith("delivery_retry:") else ""
        if task_id and task_id not in existing_ids:
            candidate_ids.append(task_id)
    if not candidate_ids:
        return pending

    extra: list[dict[str, Any]] = []
    with queue.session() as db:
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = db.execute(
            f"""
            SELECT id, source, external_user, command, risk_level, status, text,
                   receiver_account_id, attachments_json, created_at, updated_at
            FROM mobile_tasks
            WHERE status='pending' AND id IN ({placeholders})
            """,
            candidate_ids,
        ).fetchall()
    for row in rows:
        task = dict(row)
        task_id = str(task.get("id") or "")
        if not task_id or task_id in existing_ids:
            continue
        retry = get_delivery_retry(queue, task_id)
        if str(retry.get("reason") or "") not in {
            "active_lease_expired_without_owned_result",
            "terminal_without_owned_result",
            "protocol_violation_no_owned_result",
            "mcp_transport_closed",
            "app_server_mcp_transport_closed",
        }:
            continue
        extra.append(task)
        existing_ids.add(task_id)
    if not extra:
        return pending
    return [*pending, *extra]


def task_is_released_final_reply_owner(queue: MobileQueue, task_id: str) -> bool:
    retry = get_delivery_retry(queue, task_id)
    if str(retry.get("reason") or "") not in {
        "active_lease_expired_without_owned_result",
        "terminal_without_owned_result",
        "protocol_violation_no_owned_result",
    }:
        return False
    if task_event_exists(queue, task_id, "delivery_group_member"):
        return False
    return bool(
        task_event_exists(queue, task_id, "codex_turn_started")
        or task_event_exists(queue, task_id, "delivery_group_owner")
    )


def task_created_datetime(task: dict[str, Any] | None) -> datetime | None:
    if not task:
        return None
    return parse_iso_datetime(str(task.get("created_at") or task.get("updated_at") or ""))


def followup_trigger_owner_is_valid(queue: MobileQueue, task_id: str, owner_task_id: str) -> bool:
    """A follow-up can only point behind an older owner, never in a cycle."""
    tid = str(task_id or "")
    owner_id = str(owner_task_id or "")
    if not tid or not owner_id or tid == owner_id:
        return False
    task = queue.get_task(tid) or {}
    owner = queue.get_task(owner_id) or {}
    if not task or not owner:
        return False
    task_created = task_created_datetime(task)
    owner_created = task_created_datetime(owner)
    if task_created and owner_created and task_created <= owner_created:
        return False
    return True


def latest_followup_trigger_owner(queue: MobileQueue, task_id: str) -> str:
    payload = latest_task_event_payload(queue, task_id, "followup_triggered_waiting_redelivery")
    owner_task_id = str(payload.get("released_active_task_id") or "")
    if not followup_trigger_owner_is_valid(queue, task_id, owner_task_id):
        return ""
    return owner_task_id


def effective_task_thread_id(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    active_thread: dict[str, Any] | None = None,
) -> str:
    explicit = str(task.get("codex_thread_id") or "").strip()
    if explicit:
        return explicit
    if active_thread:
        mapped = str(active_thread.get("thread_id") or "").strip()
        if mapped:
            return mapped
    mapped_thread = get_active_thread(queue, config, str(task.get("external_user") or ""), use_default=False)
    if mapped_thread:
        mapped = str(mapped_thread.get("thread_id") or "").strip()
        if mapped:
            return mapped
    account_id = receiver_account_id(
        config,
        str(task.get("receiver_account_id") or ""),
        str(task.get("external_user") or ""),
    )
    if delivery_mode_for_task(config, task) == "codex-cdp" and account_id == "primary":
        return str(config.get("trigger", {}).get("codex_thread_id") or "").strip()
    return ""


def resolved_visible_cdp_thread_id(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    active_thread: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_thread_id = str(task.get("codex_thread_id") or "").strip()
    active_thread_id = str((active_thread or {}).get("thread_id") or "").strip()
    route_thread = ""
    route_source = ""
    route = get_active_thread(queue, config, str(task.get("external_user") or ""), use_default=False)
    if route:
        route_thread = str(route.get("thread_id") or "").strip()
        route_source = str(route.get("id") or route.get("name") or "").strip()
    resolved_thread_id = active_thread_id or route_thread or task_thread_id or str(config.get("trigger", {}).get("codex_thread_id") or "").strip()
    mismatch = bool(task_thread_id and resolved_thread_id and task_thread_id != resolved_thread_id)
    return {
        "ok": bool(resolved_thread_id),
        "task_thread_id": task_thread_id,
        "active_thread_id": active_thread_id,
        "route_thread_id": route_thread,
        "route_source": route_source,
        "resolved_thread_id": resolved_thread_id,
        "mismatch": mismatch,
        "route_snapshot_version": str((active_thread or {}).get("snapshot_version") or route_source or ""),
    }


def thread_route_diagnostics(
    queue: MobileQueue,
    config: dict[str, Any],
    external_user: str,
    thread: dict[str, Any] | None = None,
) -> dict[str, Any]:
    thread_item = thread if isinstance(thread, dict) else {}
    configured_cdp_thread_id = str(config.get("trigger", {}).get("codex_thread_id") or "").strip()
    thread_task = {
        "external_user": str(external_user or ""),
        "codex_thread_id": str(thread_item.get("thread_id") or ""),
    }
    resolution = resolved_visible_cdp_thread_id(queue, config, thread_task, thread_item) if thread_item else {"ok": False}
    return {
        "configured_cdp_thread_id": configured_cdp_thread_id,
        "resolved_visible_thread_id": str(resolution.get("resolved_thread_id") or ""),
        "route_mismatch": bool(resolution.get("mismatch")),
        "route_snapshot_version": str(resolution.get("route_snapshot_version") or ""),
        "thread_resolution": resolution,
    }


def same_followup_owner_route(
    queue: MobileQueue,
    config: dict[str, Any],
    owner: dict[str, Any],
    task: dict[str, Any],
    task_active_thread: dict[str, Any] | None = None,
) -> bool:
    if not owner or not task:
        return False
    if str(owner.get("external_user") or "") != str(task.get("external_user") or ""):
        return False
    task_user = str(task.get("external_user") or "")
    owner_account = receiver_account_id(config, str(owner.get("receiver_account_id") or ""), task_user)
    task_account = receiver_account_id(config, str(task.get("receiver_account_id") or ""), task_user)
    if owner_account != task_account:
        return False
    owner_mode = delivery_mode_for_task(config, owner)
    task_mode = delivery_mode_for_task(config, task)
    if owner_mode != task_mode:
        return False
    if task_mode == "codex-cdp" and task_account == "primary":
        return True
    owner_thread_id = effective_task_thread_id(queue, config, owner)
    task_thread_id = effective_task_thread_id(queue, config, task, task_active_thread)
    return bool(owner_thread_id and task_thread_id and owner_thread_id == task_thread_id)


def weixin_send_circuit_seconds(config: dict[str, Any]) -> int:
    return max(60, int(config.get("openclaw", {}).get("sendmessage_circuit_seconds") or 300))


def get_weixin_circuit(queue: MobileQueue, key: str) -> dict[str, Any]:
    raw = queue.runtime_get(key)
    if not raw:
        return {"active": False}
    try:
        data = json.loads(raw)
        retry_after = datetime.fromisoformat(str(data.get("retry_after") or ""))
    except Exception:
        queue.runtime_delete(key)
        return {"active": False, "reason": "invalid_circuit_marker"}
    now = datetime.now(timezone.utc)
    if now >= retry_after:
        queue.runtime_delete(key)
        data["active"] = False
        data["ready"] = True
        return data
    data["active"] = True
    data["remaining_seconds"] = max(0, int((retry_after - now).total_seconds()))
    return data


def get_weixin_send_circuit(queue: MobileQueue, account_id: str) -> dict[str, Any]:
    return get_weixin_circuit(queue, weixin_send_circuit_key(account_id))


def get_weixin_status_ack_circuit(queue: MobileQueue, account_id: str) -> dict[str, Any]:
    return get_weixin_circuit(queue, weixin_status_ack_circuit_key(account_id))


def mark_weixin_circuit(
    queue: MobileQueue,
    config: dict[str, Any],
    account_id: str,
    key: str,
    event_type: str,
    reason: str,
    detail: dict[str, Any] | None = None,
    task_id: str = "",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    payload = {
        "account_id": str(account_id or ""),
        "reason": reason,
        "detail": detail or {},
        "started_at": now.isoformat(),
        "retry_after": (now + timedelta(seconds=weixin_send_circuit_seconds(config))).isoformat(),
    }
    queue.runtime_set(key, json.dumps(payload, ensure_ascii=False))
    queue.add_event("wecom", event_type, payload, task_id or None)
    return payload


def mark_weixin_send_circuit(
    queue: MobileQueue,
    config: dict[str, Any],
    account_id: str,
    reason: str,
    detail: dict[str, Any] | None = None,
    task_id: str = "",
) -> dict[str, Any]:
    return mark_weixin_circuit(
        queue,
        config,
        account_id,
        weixin_send_circuit_key(account_id),
        "weixin_send_circuit_opened",
        reason,
        detail,
        task_id,
    )


def mark_weixin_status_ack_circuit(
    queue: MobileQueue,
    config: dict[str, Any],
    account_id: str,
    reason: str,
    detail: dict[str, Any] | None = None,
    task_id: str = "",
) -> dict[str, Any]:
    return mark_weixin_circuit(
        queue,
        config,
        account_id,
        weixin_status_ack_circuit_key(account_id),
        "weixin_status_ack_circuit_opened",
        reason,
        detail,
        task_id,
    )


def maybe_repair_app_server_unreadable_thread(
    queue: MobileQueue,
    config: dict[str, Any],
    task_id: str,
    thread_id: str,
    recovery_marker: dict[str, Any],
    delivery: dict[str, Any],
) -> dict[str, Any]:
    """Repair the original app-server route after repeated unreadable turns.

    This intentionally does not switch threads. The only automatic repair is a
    bounded restart of the bridge-owned app-server listener, followed by normal
    retry of the same task on the same thread.
    """
    tid = str(task_id or "")
    target_thread_id = str(thread_id or "").strip()
    if not tid or not target_thread_id:
        return {"ok": False, "action": "skipped", "reason": "missing task_id or thread_id"}
    attempts = int((recovery_marker or {}).get("attempts") or 0)
    threshold = app_server_unreadable_repair_threshold(config)
    if attempts < threshold:
        return {"ok": True, "action": "below_threshold", "attempts": attempts, "threshold": threshold}

    now = datetime.now(timezone.utc)
    cooldown = get_app_server_unreadable_repair(queue, target_thread_id, now)
    if cooldown.get("active"):
        queue.add_event(
            "local",
            "app_server_unreadable_repair_cooldown",
            {
                "thread_id": target_thread_id,
                "attempts": attempts,
                "threshold": threshold,
                "cooldown": cooldown,
                "policy": "same original thread stays pending while repair cooldown is active",
            },
            tid,
        )
        return {"ok": True, "action": "cooldown", "cooldown": cooldown, "attempts": attempts, "threshold": threshold}

    active_app_server = [
        task for task in queue.list_active_codex_delivery_tasks(limit=100)
        if delivery_mode_for_task(config, task) == "codex-app-server"
        and str(task.get("id") or "") not in {tid}
    ]
    if active_app_server:
        payload = {
            "thread_id": target_thread_id,
            "attempts": attempts,
            "threshold": threshold,
            "active_task_ids": [str(task.get("id") or "") for task in active_app_server],
            "policy": "defer app-server listener restart while other app-server deliveries are active",
        }
        queue.add_event("local", "app_server_unreadable_repair_deferred_active_tasks", payload, tid)
        return {"ok": True, "action": "deferred_active_tasks", **payload}

    restart = restart_codex_app_server_for_mcp(config, "app_server_turn_not_readable_after_dispatch")
    retry_after = (now + timedelta(seconds=app_server_unreadable_repair_cooldown_seconds(config))).isoformat()
    payload = {
        "thread_id": target_thread_id,
        "attempts": attempts,
        "threshold": threshold,
        "retry_after": retry_after,
        "restart": restart,
        "delivery": delivery,
        "policy": "restart only the bridge-owned app-server listener; keep pending work on the original thread",
    }
    queue.runtime_set(app_server_unreadable_repair_key(target_thread_id), json.dumps(payload, ensure_ascii=False))
    queue.add_event("local", "app_server_unreadable_repair_attempted", payload, tid)
    return {"ok": bool(restart.get("ok")), "action": "restart_attempted", **payload}


def clear_completed_task_runtime(queue: MobileQueue, task_id: str) -> None:
    tid = str(task_id or "")
    if not tid:
        return
    clear_delivery_retry(queue, [tid])
    clear_thread_recovery(queue, [tid])
    queue.runtime_delete(pending_reply_context_last_token_key(tid))
    clear_task_codex_runtime(queue, tid)


def get_continuation_context(
    queue: MobileQueue,
    config: dict[str, Any],
    external_user: str,
    thread_project: str,
) -> dict[str, Any]:
    raw = queue.runtime_get(continuation_key(external_user, thread_project))
    if not raw:
        return {"active": False, "reason": "no_window"}
    try:
        data = json.loads(raw)
        expires_at = datetime.fromisoformat(str(data.get("expires_at") or ""))
    except Exception:
        queue.runtime_delete(continuation_key(external_user, thread_project))
        return {"active": False, "reason": "invalid_window"}
    now = datetime.now(timezone.utc)
    if now > expires_at:
        queue.runtime_delete(continuation_key(external_user, thread_project))
        return {"active": False, "reason": "expired_window"}
    return {
        "active": True,
        "expires_at": expires_at.isoformat(),
        "remaining_seconds": max(0, int((expires_at - now).total_seconds())),
        "window_seconds": continuation_window_seconds(config),
    }


def refresh_continuation_window(
    queue: MobileQueue,
    config: dict[str, Any],
    external_user: str,
    thread_project: str,
) -> None:
    seconds = max(1, continuation_window_seconds(config))
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    queue.runtime_set(
        continuation_key(external_user, thread_project),
        json.dumps({"expires_at": expires_at, "window_seconds": seconds}, ensure_ascii=False),
    )


def run_powershell(script: str, timeout: int = 20) -> dict[str, Any]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    try:
        proc = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-3000:],
        "stderr": (proc.stderr or "")[-3000:],
    }


def set_shadow_mode(config_path: Path, config: dict[str, Any], value: bool) -> None:
    config.setdefault("safety", {})["shadow_mode"] = value
    save_config(config_path, config)


def set_confirmation_secret_hash(config_path: Path, config: dict[str, Any], secret: str) -> str:
    digest = sha256_text(secret)
    config.setdefault("security", {})["confirmation_secret_hash"] = digest
    save_config(config_path, config)
    return digest


def stop_worker_processes() -> dict[str, Any]:
    script = r"""
$ErrorActionPreference = 'Continue'
$currentPid = $PID
$targets = Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $currentPid -and
  $_.Name -in @('powershell.exe','pwsh.exe','python.exe','pythonw.exe') -and
  (
    $_.CommandLine -match 'mobile_openclaw_cli\.py worker-loop' -or
    $_.CommandLine -match 'run-worker-loop\.ps1' -or
    $_.CommandLine -match 'start_worker_hidden\.py'
  )
} | Select-Object ProcessId,ParentProcessId,Name,CommandLine
$stopped = @()
foreach ($target in $targets) {
  try {
    Stop-Process -Id $target.ProcessId -Force -ErrorAction Stop
    $stopped += [pscustomobject]@{ process_id = $target.ProcessId; name = $target.Name; ok = $true }
  } catch {
    $stopped += [pscustomobject]@{ process_id = $target.ProcessId; name = $target.Name; ok = $false; error = $_.Exception.Message }
  }
}
[pscustomobject]@{ ok = $true; stopped = $stopped } | ConvertTo-Json -Depth 5
"""
    return run_powershell(script, timeout=20)


def stop_scheduled_task(task_name: str, disable: bool = True) -> dict[str, Any]:
    escaped = task_name.replace("'", "''")
    disable_line = f"Disable-ScheduledTask -TaskName '{escaped}' -ErrorAction SilentlyContinue | Out-Null" if disable else ""
    script = f"""
$ErrorActionPreference = 'Continue'
$exists = Get-ScheduledTask -TaskName '{escaped}' -ErrorAction SilentlyContinue
if ($exists) {{
  Stop-ScheduledTask -TaskName '{escaped}' -ErrorAction SilentlyContinue
  {disable_line}
  [pscustomobject]@{{ ok = $true; task_name = '{escaped}'; existed = $true; disabled = ${str(disable).lower()} }} | ConvertTo-Json -Depth 3
}} else {{
  [pscustomobject]@{{ ok = $true; task_name = '{escaped}'; existed = $false; disabled = $false }} | ConvertTo-Json -Depth 3
}}
"""
    return run_powershell(script, timeout=20)


def enable_scheduled_task(task_name: str, start: bool = True) -> dict[str, Any]:
    escaped = task_name.replace("'", "''")
    start_line = f"Start-ScheduledTask -TaskName '{escaped}' -ErrorAction SilentlyContinue" if start else ""
    script = f"""
$ErrorActionPreference = 'Continue'
$exists = Get-ScheduledTask -TaskName '{escaped}' -ErrorAction SilentlyContinue
if ($exists) {{
  Enable-ScheduledTask -TaskName '{escaped}' -ErrorAction SilentlyContinue | Out-Null
  {start_line}
  [pscustomobject]@{{ ok = $true; task_name = '{escaped}'; existed = $true; started = ${str(start).lower()} }} | ConvertTo-Json -Depth 3
}} else {{
  [pscustomobject]@{{ ok = $false; task_name = '{escaped}'; existed = $false; reason = 'scheduled task not found' }} | ConvertTo-Json -Depth 3
}}
"""
    return run_powershell(script, timeout=20)


def active_codex_tasks(queue: MobileQueue, include_all_active: bool = True) -> list[dict[str, Any]]:
    statuses = ("queued_for_codex", "sent_to_codex", "processing")
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, source, external_user, command, risk_level, status, codex_thread_id,
                   receiver_account_id, queued_for_codex_at, sent_to_codex_at, updated_at, created_at,
                   LENGTH(COALESCE(result, '')) AS result_length,
                   SUBSTR(COALESCE(error, ''), 1, 200) AS error_preview
            FROM mobile_tasks
            WHERE status IN ('queued_for_codex','sent_to_codex','processing')
            ORDER BY updated_at ASC
            """
        ).fetchall()
    tasks = [dict(row) for row in rows]
    if include_all_active:
        return tasks
    return [task for task in tasks if int(task.get("result_length") or 0) == 0]


def mark_stuck_tasks_failed(queue: MobileQueue, task_ids: list[str], reason: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    active_ids = {str(task["id"]) for task in active_codex_tasks(queue)}
    for task_id in task_ids:
        if task_id not in active_ids:
            results.append({"id": task_id, "ok": False, "reason": "task is not active"})
            continue
        queue.complete(task_id, reason, status="failed")
        queue.add_event("local", "stuck_task_marked_failed", {"reason": reason}, task_id)
        results.append({"id": task_id, "ok": True})
    return results


def stability_check(queue: MobileQueue, config: dict[str, Any], deep: bool = False) -> dict[str, Any]:
    status = bridge_status(queue, config)
    config_path = Path(config.get("_config_path") or DEFAULT_CONFIG)
    db_path = Path(db_path_from_config(config))
    trigger = config.get("trigger", {})
    cdp_settings = codex_cdp_config(config)
    cdp_host = str(cdp_settings.get("host") or trigger.get("codex_cdp_host") or "localhost")
    cdp_port = int(cdp_settings.get("port") or trigger.get("codex_cdp_port") or 9229)
    app_server_host = str(trigger.get("codex_app_server_host") or "127.0.0.1")
    app_server_port = int(trigger.get("codex_app_server_port") or 18791)
    delivery_health = check_codex_health(config)
    status["pending_count"] = len(queue.list_pending(50))
    status["active_codex_tasks"] = active_codex_tasks(queue)
    status["openclaw_gateway_processes"] = inspect_openclaw_gateway_processes(PROJECT_ROOT)
    status["latest_worker_stderr"] = latest_worker_stderr(ROOT)
    status["latest_worker_logs"] = latest_worker_log_summary(ROOT)
    status["file_health"] = {
        "config": config_health(config_path, config, len(thread_items(config))),
        "database": sqlite_health(db_path),
        "attachments": attachments_health(ATTACHMENTS_DIR),
        "shared_queue_module": path_health(WECOM_BRIDGE / "mobile_queue.py", "file", max_bytes=2 * 1024 * 1024),
        "file_toolkit": path_health(FILE_TOOLKIT / "__init__.py", "file", max_bytes=512 * 1024),
    }
    status["ports"] = {
        "codex_cdp": tcp_check(cdp_port, host=cdp_host),
        "codex_app_server": tcp_check(app_server_port, host=app_server_host),
        "openclaw_gateway": tcp_check(int(config.get("openclaw", {}).get("port") or 18789)),
    }
    status["codex_cdp_version"] = http_json(
        "/json/version",
        cdp_port,
        host=cdp_host,
    )
    status["codex_cdp_endpoint"] = {
        "host": cdp_host,
        "port": cdp_port,
        "source": cdp_settings.get("endpoint_source"),
        "preferred_host": cdp_settings.get("preferred_host"),
        "preferred_port": cdp_settings.get("preferred_port"),
        "state": cdp_settings.get("endpoint_state") or {},
    }
    status["codex_delivery_health"] = delivery_health
    if deep:
        status["thread_routes_ui_health"] = thread_routes_ui_health(config)
    else:
        route_count = len(thread_items(config))
        status["thread_routes_ui_health"] = {
            "ok": True,
            "healthy": True,
            "skipped": True,
            "mode": "fast",
            "route_count": route_count,
            "warning_count": 0,
            "recoverable_count": 0,
            "fatal_count": 0,
            "checked": 0,
            "state_counts": {"ready": 0, "prewarm": 0, "probe_failed": 0, "unavailable": 0, "busy": 0},
            "routes": [],
            "reason": "thread route UI probes are deep advisory checks; run stability-check --deep to inspect configured thread routes",
        }
    if isinstance(status.get("thread_routes_ui_health"), dict):
        state_counts = status["thread_routes_ui_health"].get("state_counts")
        if isinstance(state_counts, dict):
            status["thread_route_state_counts"] = dict(state_counts)
    status["scheduled_task_action"] = scheduled_task_action_health(PROJECT_ROOT, ROOT, task_name_from_config(config))
    status["openclaw_gateway_scheduled_task"] = inspect_scheduled_task(PROJECT_ROOT, DEFAULT_GATEWAY_TASK_NAME)
    status["openclaw_gateway_scheduled_task_action"] = scheduled_task_script_health(
        PROJECT_ROOT,
        DEFAULT_GATEWAY_TASK_NAME,
        [
            ROOT / "start-openclaw-gateway-hidden.ps1",
            ROOT / "run-openclaw-gateway-loop.ps1",
            ROOT / "start_openclaw_gateway_hidden.py",
        ],
    )
    status["shared_queue_module"] = str(WECOM_BRIDGE / "mobile_queue.py")
    core_ports = {
        key: value
        for key, value in status.get("ports", {}).items()
        if key in {"codex_cdp", "codex_app_server", "openclaw_gateway"}
    }
    thread_routes = status.get("thread_routes_ui_health", {}) if isinstance(status.get("thread_routes_ui_health"), dict) else {}
    current_queue_idle = int(status.get("pending_count") or 0) == 0 and len(status.get("active_codex_tasks") or []) == 0
    thread_routes_blocking = (not current_queue_idle) and not bool(thread_routes.get("ok"))
    status["stability_scope"] = {
        "schema": "mobile-openclaw-stability-scope/v1",
        "ok_rule": "core bridge runtime only; historical/advisory checks do not make the bridge unavailable",
        "mode": "deep" if deep else "fast",
        "deep_thread_route_probe": bool(deep),
        "current_queue_idle": current_queue_idle,
        "thread_routes_blocking": thread_routes_blocking,
        "core_ports": sorted(core_ports),
        "advisory_ports": [],
    }
    status["advisories"] = []
    if thread_routes and not bool(thread_routes.get("ok")):
        status["advisories"].append(
            {
                "code": "configured_thread_routes_need_review",
                "severity": "low" if current_queue_idle else "medium",
                "summary": "Some configured Codex thread routes are unavailable or only prewarmable.",
                "state_counts": thread_routes.get("state_counts", {}),
                "blocking_current_work": thread_routes_blocking,
            }
        )
    status["ok"] = all(
        [
            bool(status.get("integrity_check") == "ok"),
            not bool(status.get("paused")),
            not bool(status.get("stop_request")),
            not bool(status.get("pause_file")),
            current_queue_idle,
            bool(status.get("latest_worker_stderr", {}).get("ok")),
            all(bool(item.get("ok")) for item in core_ports.values()),
            all(bool(item.get("ok")) for item in status.get("file_health", {}).values()),
            bool(status.get("scheduled_task", {}).get("ok")),
            bool(status.get("scheduled_task_action", {}).get("ok")),
            bool(status.get("openclaw_gateway_processes", {}).get("ok")),
            bool(status.get("openclaw_gateway_scheduled_task", {}).get("ok")),
            bool(status.get("openclaw_gateway_scheduled_task_action", {}).get("ok")),
            bool(status.get("codex_cdp_version", {}).get("ok")),
            bool(status.get("codex_delivery_health", {}).get("healthy")),
            not thread_routes_blocking,
        ]
    )
    status["health_notes"] = [
        "This command is read-only.",
        "Default stability-check runs fast core probes; use --deep for thread route UI probes and historical route advisory detail.",
        "STOP_REQUEST pauses future delivery; stop also attempts a CDP click on the active Codex stop button.",
        "OpenClawGatewayWorker is project-managed; OpenClaw's built-in gateway status may not identify it as the native OpenClaw service.",
        "file_health writes and removes a tiny attachments/.write-probe file to verify writability.",
    ]
    return status


def p0_audit(queue: MobileQueue, config: dict[str, Any]) -> dict[str, Any]:
    """Read-only P0 audit for event pressure and reply backlog.

    This intentionally reports only facts and next-step gates. It must not
    delete events, send or retry replies, switch routes, or mutate task state.
    """
    snapshot = inspect_system(queue, config)
    database = snapshot.get("database") if isinstance(snapshot.get("database"), dict) else {}
    event_noise = snapshot.get("event_noise") if isinstance(snapshot.get("event_noise"), dict) else {}
    event_archive = (
        snapshot.get("event_archive_dry_run")
        if isinstance(snapshot.get("event_archive_dry_run"), dict)
        else {}
    )
    accounts = snapshot.get("top_accounts") if isinstance(snapshot.get("top_accounts"), list) else []
    reply_problems = snapshot.get("reply_problems") if isinstance(snapshot.get("reply_problems"), list) else []
    active = snapshot.get("active") if isinstance(snapshot.get("active"), list) else []
    pending = snapshot.get("pending") if isinstance(snapshot.get("pending"), list) else []

    top_noisy = []
    for item in (event_noise.get("top_noisy_event_types") or [])[:8]:
        if isinstance(item, dict):
            top_noisy.append(
                {
                    "event_type": item.get("event_type"),
                    "count": int(item.get("count") or 0),
                }
            )

    reply_by_account = []
    for item in accounts:
        if not isinstance(item, dict):
            continue
        count = int(item.get("reply_backlog_count") or 0)
        if count <= 0:
            continue
        reply_by_account.append(
            {
                "account": item.get("account"),
                "delivery_mode": item.get("delivery_mode"),
                "reply_backlog_count": count,
                "reply_task_ids": (item.get("reply_task_ids") or [])[:8],
            }
        )

    total_events = int(event_noise.get("total_events") or 0)
    archive_candidates = int(event_archive.get("candidate_count") or 0)
    database_size = int(database.get("bytes") or database.get("size_bytes") or 0)
    reply_count = len(reply_problems)
    severity = "ok"
    if total_events >= 100000 or archive_candidates >= 10000 or database_size >= 64 * 1024 * 1024:
        severity = "attention"
    if reply_count:
        severity = "attention"

    recommendations: list[dict[str, Any]] = []
    if archive_candidates:
        recommendations.append(
            {
                "code": "event_archive_plan",
                "priority": "P0",
                "action": (
                    "Prepare an explicit maintenance window for diagnostic-event archive or prune. "
                    "Require DB backup, event-noise checks, and user approval before any delete/VACUUM."
                ),
                "current_command": "maintenance summary shows dry-run candidates only",
            }
        )
    if reply_count:
        recommendations.append(
            {
                "code": "reply_backlog_audit",
                "priority": "P0",
                "action": (
                    "Classify each reply backlog item as visibility-unconfirmed accepted, retryable, "
                    "or stale failed. Do not resend without an explicit include-reply-send approval."
                ),
                "safe_dry_run": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py maintenance repair",
            }
        )
    if not recommendations:
        recommendations.append(
            {
                "code": "continue_observation",
                "priority": "P0",
                "action": "No immediate P0 cleanup candidate found; keep current monitoring checks.",
            }
        )

    return {
        "ok": True,
        "read_only": True,
        "audit": "p0_event_noise_and_reply_backlog",
        "generated_at": snapshot.get("generated_at"),
        "severity": severity,
        "guardrails": {
            "deletes_events": False,
            "sends_replies": False,
            "changes_routes": False,
            "mutates_tasks": False,
            "requires_user_approval_for_apply": True,
        },
        "database": {
            "ok": database.get("ok"),
            "size_bytes": database_size,
            "under_limit": database.get("under_limit"),
            "integrity_check": database.get("integrity_check"),
            "warning": database.get("warning") or database.get("reason") or "",
            "db_path": snapshot.get("db_path"),
        },
        "event_noise": {
            "ok": event_noise.get("ok"),
            "total_events": total_events,
            "guard_seconds": event_noise.get("guard_seconds"),
            "guard_index_exists": event_noise.get("guard_index_exists"),
            "suppressed_recent_total": int(event_noise.get("suppressed_recent_total") or 0),
            "suppressed_marker_count": int(event_noise.get("suppressed_marker_count") or 0),
            "top_noisy_event_types": top_noisy,
        },
        "event_archive_dry_run": {
            "ok": event_archive.get("ok"),
            "dry_run": True,
            "retention_hours": event_archive.get("retention_hours"),
            "cutoff": event_archive.get("cutoff"),
            "candidate_count": archive_candidates,
            "by_event_type": (event_archive.get("by_event_type") or [])[:8],
            "policy": event_archive.get("policy"),
        },
        "reply_backlog": {
            "count": reply_count,
            "by_account": reply_by_account,
            "sample": [
                {
                    "id": item.get("id"),
                    "account": item.get("account"),
                    "status": item.get("status"),
                    "push_status": item.get("push_status"),
                    "age_seconds": item.get("age_seconds"),
                }
                for item in reply_problems[:12]
                if isinstance(item, dict)
            ],
        },
        "current_work": {
            "pending_count": len(pending),
            "active_count": len(active),
            "active_task_ids": [item.get("id") for item in active[:8] if isinstance(item, dict)],
        },
        "recommendations": recommendations,
        "validation_commands": [
            "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py event-noise-coalescing-check",
            "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py reply-dedupe-policy-check",
            "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py maintenance summary",
            "python _bridge\\iteration_layer_review.py --json --recent-limit 12 --run-validation",
        ],
    }


def bridge_status(queue: MobileQueue, config: dict[str, Any]) -> dict[str, Any]:
    health = queue.health()
    task_name = task_name_from_config(config)
    health["stop_request"] = STOP_REQUEST.exists()
    health["pause_file"] = queue.pause_file().exists()
    health["worker_processes"] = inspect_worker_processes(PROJECT_ROOT)
    health["scheduled_task"] = inspect_scheduled_task(PROJECT_ROOT, task_name)
    return health


def status_reply_text(status: dict[str, Any]) -> str:
    return reply_status_text.bridge_status_reply_text(status)


def user_status_reply_text(queue: MobileQueue, config: dict[str, Any], external_user: str) -> str:
    allowed = queue.is_user_allowed(external_user)
    admin = is_primary_admin_user(config, external_user)
    active = get_active_thread(queue, config, external_user)
    counts: dict[str, int] = {}
    recent_status = "无记录"
    recent_task_id = ""
    recent_updated = ""
    with queue.session() as db:
        for row in db.execute(
            """
            SELECT status, COUNT(*) AS n
            FROM mobile_tasks
            WHERE external_user=?
            GROUP BY status
            """,
            (external_user,),
        ).fetchall():
            counts[str(row["status"])] = int(row["n"])
        latest = db.execute(
            """
            SELECT id, status, updated_at
            FROM mobile_tasks
            WHERE external_user=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (external_user,),
        ).fetchone()
        if latest:
            recent_status = str(latest["status"] or "")
            recent_task_id = str(latest["id"] or "")
            recent_updated = str(latest["updated_at"] or "")
    active_name = str(active.get("name") or active.get("id") or "未分配") if active else "未分配"
    return reply_status_text.user_status_reply_text(
        allowed=allowed,
        admin=admin,
        active_name=active_name,
        counts=counts,
        recent_status=recent_status,
        recent_task_id=recent_task_id,
        recent_updated=recent_updated,
    )


def onboarding_hold_key(task_id: str) -> str:
    return f"onboarding_hold:{task_id}"


def onboarding_thread_placeholder_id(external_user: str) -> str:
    user = str(external_user or "").split("@", 1)[0].strip().lower()
    readable = re.sub(r"[^a-z0-9_-]+", "", user)[:10]
    digest = hashlib.sha256(str(external_user or "").encode("utf-8")).hexdigest()[:6]
    if readable:
        return f"weixin-user-{readable}-{digest}"
    return f"weixin-user-{digest}"


def onboarding_thread_name(external_user: str) -> str:
    user = str(external_user or "").split("@", 1)[0].strip()
    label = re.sub(r"[^A-Za-z0-9_-]+", "", user)[:10] or hashlib.sha256(
        str(external_user or "").encode("utf-8")
    ).hexdigest()[:8]
    return f"微信用户 {label} 独立对话"


def onboarding_needed_text(external_user: str) -> str:
    return "\n".join(
        [
            "已收到你的消息。",
            "这个微信用户已经通过 OpenClaw 识别，但还没有绑定独立 Codex 对话线程。",
            f"用户标识：{external_user}",
            f"建议线程标识：{onboarding_thread_placeholder_id(external_user)}",
            "为避免串话，系统不会把你的消息投递到主线程；请稍后等待管理员完成线程绑定。",
        ]
    )


def onboarding_created_text(thread_name: str) -> str:
    return "\n".join(
        [
            "已为这个微信用户创建独立 Codex 对话线程。",
            f"当前线程：{thread_name}",
            "后续消息会继续进入这个用户自己的线程，不会投递到主线程。",
        ]
    )


def interrupt_codex_generation(config: dict[str, Any]) -> dict[str, Any]:
    trigger = config.get("trigger", {})
    node = str(trigger.get("node_path") or "node")
    script = Path(
        trigger.get("codex_cdp_stop_script")
        or PROJECT_ROOT / "_tools" / "codex-cdp-tools" / "codex_cdp_stop.js"
    )
    port = int(trigger.get("codex_cdp_port") or 9229)
    host = str(trigger.get("codex_cdp_host") or "localhost")
    timeout_seconds = int(trigger.get("hard_stop_timeout_seconds") or 8)
    if not script.exists():
        return {
            "ok": False,
            "reason": f"codex CDP stop script not found: {script}",
            "script": str(script),
        }
    command = [
        node,
        str(script),
        "--host",
        host,
        "--port",
        str(port),
        "--timeout-ms",
        str(max(timeout_seconds * 1000, 1000)),
    ]
    try:
        proc = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout_seconds + 2,
            cwd=str(PROJECT_ROOT / "_tools" / "codex-cdp-tools"),
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"codex CDP hard interrupt failed to start: {exc}",
            "command": command,
        }
    try:
        parsed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        parsed = {"ok": False, "raw_stdout": proc.stdout}
    return {
        "ok": proc.returncode == 0 and bool(parsed.get("ok")),
        "mode": "codex-cdp-stop",
        "returncode": proc.returncode,
        "stdout": parsed,
        "stderr": (proc.stderr or "")[-2000:],
    }


def emergency_stop(queue: MobileQueue, config_path: Path, config: dict[str, Any], actor: str = "") -> dict[str, Any]:
    queue.pause_file().write_text(f"paused by stop at {utc_now()} actor={actor}\n", encoding="utf-8")
    STOP_REQUEST.write_text(f"stop requested at {utc_now()} actor={actor}\n", encoding="utf-8")
    set_shadow_mode(config_path, config, True)
    codex_interrupt = interrupt_codex_generation(config)
    task_name = task_name_from_config(config)
    task_result = stop_scheduled_task(task_name, disable=True)
    process_result = stop_worker_processes()
    queue.add_event(
        "local",
        "emergency_stop",
        {
            "actor": actor,
            "pause_file": str(queue.pause_file()),
            "stop_request": str(STOP_REQUEST),
            "codex_interrupt": codex_interrupt,
            "scheduled_task": task_result,
            "processes": process_result,
        },
    )
    return {
        "ok": True,
        "action": "emergency_stop",
        "paused": True,
        "shadow_mode": True,
        "pause_file": str(queue.pause_file()),
        "stop_request": str(STOP_REQUEST),
        "codex_interrupt": codex_interrupt,
        "scheduled_task": task_result,
        "processes": process_result,
    }


def resume_bridge(queue: MobileQueue, config_path: Path, config: dict[str, Any], actor: str = "") -> dict[str, Any]:
    if queue.pause_file().exists():
        queue.pause_file().unlink()
    if STOP_REQUEST.exists():
        STOP_REQUEST.unlink()
    set_shadow_mode(config_path, config, False)
    task_name = task_name_from_config(config)
    task_result = enable_scheduled_task(task_name, start=True)
    queue.add_event(
        "local",
        "bridge_resumed",
        {"actor": actor, "scheduled_task": task_result},
    )
    return {
        "ok": True,
        "action": "resume_bridge",
        "paused": False,
        "shadow_mode": False,
        "pause_file": str(queue.pause_file()),
        "stop_request": str(STOP_REQUEST),
        "scheduled_task": task_result,
    }


def start_worker_once(config_path: Path, limit: int = 5, task_id: str = "") -> dict[str, Any]:
    bundled_python = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe"
    python = str(bundled_python if bundled_python.exists() else "python")
    args = [
        str(ROOT / "mobile_openclaw_cli.py"),
        "--config",
        str(config_path),
        "worker-once",
        "--limit",
        str(limit),
    ]
    if task_id:
        args.extend(["--task-id", task_id])
    # Use PowerShell Start-Process so confirmation replies do not block OpenClaw inbound handling.
    quoted_args = "@(" + ",".join("'" + arg.replace("'", "''") + "'" for arg in args) + ")"
    ps = (
        f"Start-Process -FilePath '{python.replace(chr(39), chr(39) * 2)}' "
        f"-ArgumentList {quoted_args} -WorkingDirectory '{str(ROOT).replace(chr(39), chr(39) * 2)}' "
        "-WindowStyle Hidden"
    )
    result = run_powershell(ps, timeout=10)
    result["command"] = [python, *args]
    return result


def send_control_reply(
    queue: MobileQueue,
    reply_task: dict[str, Any],
    text: str,
    config: dict[str, Any],
    command: str,
    *,
    receipt_id: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return send_control_reply_with_receipt(
        queue,
        reply_task,
        text,
        config,
        command,
        reply_func=lambda task, body, cfg: reply_to_weixin(task, body, cfg, send=True),
        delivery_accepted_func=final_reply_delivery_accepted,
        phone_visible_func=final_reply_phone_visible,
        receipt_id=receipt_id,
        extra=extra,
    )


def maybe_handle_control_message(
    queue: MobileQueue,
    config_path: Path,
    config: dict[str, Any],
    text: str,
    external_user: str,
    external_conversation: str,
    account_id: str = "",
) -> dict[str, Any] | None:
    """Facade for control-message handling; implementation lives in control_message_runtime."""
    return run_control_message_handler(
        globals(),
        queue,
        config_path,
        config,
        text,
        external_user,
        external_conversation,
        account_id,
    )


def reject_task_for_permission(queue: MobileQueue, task_id: str, reason: str, detail: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with queue.session() as db:
        db.execute(
            """
            UPDATE mobile_tasks
            SET status='rejected',
                error=?,
                updated_at=?,
                completed_at=?
            WHERE id=? AND status IN ('pending','queued_for_codex','sent_to_codex','processing')
            """,
            (reason, now, now, task_id),
        )
    queue.add_event("local", "permission_ask_scope_rejected", detail, task_id)


def mark_task_waiting_capability_passphrase(
    queue: MobileQueue,
    task_id: str,
    detail: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=CAPABILITY_PASSPHRASE_WAIT_MINUTES)).isoformat()
    with queue.session() as db:
        row = db.execute("SELECT metadata_json FROM mobile_tasks WHERE id=?", (task_id,)).fetchone()
        metadata = {}
        if row:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except Exception:
                metadata = {}
        metadata["capability_passphrase"] = {
            "status": "waiting",
            "requested_at": now,
            "expires_at": expires_at,
            "attempt_count": 0,
            "required_actions": detail.get("required_actions", []),
            "grant_ids": detail.get("grant_ids", []),
            "scope": detail.get("scope", ""),
            "policy": "active grant exists; user must provide the passphrase in a follow-up message before Codex dispatch",
        }
        db.execute(
            """
            UPDATE mobile_tasks
            SET status='waiting_capability_passphrase',
                error='capability passphrase required',
                metadata_json=?,
                updated_at=?
            WHERE id=? AND status IN ('pending','queued_for_codex','sent_to_codex','processing')
            """,
            (json.dumps(metadata, ensure_ascii=False), now, task_id),
        )
    queue.add_event("local", "permission_capability_passphrase_required", detail, task_id)


def capability_passphrase_challenge_from_task(task: dict[str, Any]) -> dict[str, Any]:
    try:
        metadata = json.loads(str(task.get("metadata_json") or "{}"))
    except Exception:
        metadata = {}
    challenge = metadata.get("capability_passphrase") if isinstance(metadata.get("capability_passphrase"), dict) else {}
    return challenge if isinstance(challenge, dict) else {}


def capability_passphrase_wait_expired(task: dict[str, Any], now: datetime | None = None) -> bool:
    challenge = capability_passphrase_challenge_from_task(task)
    expires_at = parse_iso_datetime(str(challenge.get("expires_at") or ""))
    if not expires_at:
        return False
    return (now or datetime.now(timezone.utc)) >= expires_at


def close_capability_passphrase_wait(
    queue: MobileQueue,
    task_id: str,
    *,
    status: str,
    error: str,
    event_type: str,
    detail: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with queue.session() as db:
        db.execute(
            """
            UPDATE mobile_tasks
            SET status=?,
                error=?,
                updated_at=?,
                completed_at=?
            WHERE id=? AND status='waiting_capability_passphrase'
            """,
            (status, error, now, now, task_id),
        )
    queue.add_event("local", event_type, detail or {"task_id": task_id}, task_id)


def extract_capability_passphrase(text: str) -> str:
    return capability_passphrase_text.extract_capability_passphrase(text)


def capability_passphrase_candidates(text: str) -> list[str]:
    return capability_passphrase_text.capability_passphrase_candidates(text)


def is_capability_passphrase_cancel(text: str) -> bool:
    return capability_passphrase_text.is_capability_passphrase_cancel(text)


def is_direct_capability_passphrase_reply(text: str) -> bool:
    return capability_passphrase_text.is_direct_capability_passphrase_reply(text)


def resolve_capability_passphrase(text: str, grants: list[dict[str, Any]]) -> str:
    return capability_passphrase_text.resolve_capability_passphrase(
        text,
        grants,
        passphrase_required=capability_tokens.passphrase_required,
        verify_passphrase=capability_tokens.verify_passphrase,
    )


def redact_capability_passphrase(text: str) -> str:
    return capability_passphrase_text.redact_capability_passphrase(text)


def redact_capability_passphrase_value(text: str, passphrase: str) -> str:
    return capability_passphrase_text.redact_capability_passphrase_value(text, passphrase)


def redact_task_capability_passphrase(queue: MobileQueue, task_id: str, text: str, passphrase: str = "") -> None:
    if not task_id:
        return
    redacted = redact_capability_passphrase_value(text, passphrase)
    if redacted == str(text or ""):
        return
    now = datetime.now(timezone.utc).isoformat()
    with queue.session() as db:
        db.execute(
            "UPDATE mobile_tasks SET text=?, updated_at=? WHERE id=?",
            (redacted, now, task_id),
        )
    queue.add_event("local", "permission_capability_passphrase_redacted", {"task_id": task_id}, task_id)


def find_waiting_capability_passphrase_task(
    queue: MobileQueue,
    *,
    actor: str,
    account_id: str,
    conversation: str = "",
    exclude_task_id: str = "",
) -> dict[str, Any] | None:
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT *
            FROM mobile_tasks
            WHERE status='waiting_capability_passphrase'
              AND external_user=?
              AND receiver_account_id=?
              AND (?='' OR external_conversation=?)
              AND (?='' OR id<>?)
            ORDER BY updated_at DESC, created_at DESC
            """,
            (actor, account_id, conversation, conversation, exclude_task_id, exclude_task_id),
        ).fetchall()
    now = datetime.now(timezone.utc)
    for row in rows:
        task = dict(row)
        if capability_passphrase_wait_expired(task, now):
            close_capability_passphrase_wait(
                queue,
                str(task.get("id") or ""),
                status="rejected",
                error="capability passphrase wait expired",
                event_type="permission_capability_passphrase_expired",
                detail={"task_id": str(task.get("id") or ""), "policy": "expired waiting task is closed before matching a new passphrase"},
            )
            continue
        return task
    return None


def maybe_complete_capability_passphrase_reply(
    queue: MobileQueue,
    *,
    text: str,
    actor: str,
    account_id: str,
    conversation: str = "",
) -> dict[str, Any] | None:
    waiting = find_waiting_capability_passphrase_task(
        queue,
        actor=actor,
        account_id=account_id,
        conversation=conversation,
    )
    if not waiting:
        return None
    raw_text = str(text or "").strip()
    if is_capability_passphrase_cancel(raw_text):
        close_capability_passphrase_wait(
            queue,
            str(waiting.get("id") or ""),
            status="cancelled",
            error="capability passphrase wait cancelled by user",
            event_type="permission_capability_passphrase_cancelled",
            detail={"task_id": str(waiting.get("id") or ""), "actor": actor, "account_id": account_id},
        )
        return {
            "ok": True,
            "handled": True,
            "task_id": waiting.get("id"),
            "status": "cancelled",
            "reply_text": "已取消这条受限请求。",
        }
    if capability_passphrase_wait_expired(waiting):
        close_capability_passphrase_wait(
            queue,
            str(waiting.get("id") or ""),
            status="rejected",
            error="capability passphrase wait expired",
            event_type="permission_capability_passphrase_expired",
            detail={"task_id": str(waiting.get("id") or ""), "actor": actor, "account_id": account_id},
        )
        return {
            "ok": False,
            "handled": True,
            "task_id": waiting.get("id"),
            "status": "rejected",
            "reason": "capability_passphrase_wait_expired",
            "reply_text": "这条受限请求的口令等待已过期，请重新发起请求。",
        }
    explicit = bool(extract_capability_passphrase(raw_text))
    if not explicit and not is_direct_capability_passphrase_reply(raw_text):
        return None
    metadata = {}
    try:
        metadata = json.loads(str(waiting.get("metadata_json") or "{}"))
    except Exception:
        metadata = {}
    challenge = metadata.get("capability_passphrase") if isinstance(metadata.get("capability_passphrase"), dict) else {}
    grant_ids = [str(item) for item in (challenge.get("grant_ids") or []) if str(item)]
    if not grant_ids:
        return {
            "ok": False,
            "handled": True,
            "task_id": waiting.get("id"),
            "reason": "waiting_task_missing_grants",
            "reply_text": "这条受限请求缺少令牌记录，已停止继续执行。请让管理员重新授权后再试。",
        }
    store = capability_tokens.read_store()
    grant_items = [
        item for item in store.get("grants", [])
        if str(item.get("grant_id") or "") in set(grant_ids)
    ]
    passphrase = resolve_capability_passphrase(raw_text, grant_items)
    if not passphrase and (explicit or is_direct_capability_passphrase_reply(raw_text)):
        passphrase = extract_capability_passphrase(raw_text) or raw_text
    if not passphrase:
        queue.add_event(
            "local",
            "permission_capability_passphrase_missing_or_invalid",
            {"task_id": waiting.get("id"), "grant_ids": grant_ids},
            str(waiting.get("id") or ""),
        )
        return {
            "ok": False,
            "handled": True,
            "task_id": waiting.get("id"),
            "reason": "passphrase_required",
            "reply_text": "需要提供管理员授予的口令后才能继续这条受限请求。",
        }
    used: list[dict[str, Any]] = []
    for grant_id in sorted(set(grant_ids)):
        result = capability_tokens.consume_grant(
            grant_id=grant_id,
            task_id=str(waiting.get("id") or ""),
            capability=",".join(str(item) for item in (challenge.get("required_actions") or []) if str(item)),
            reason=str(challenge.get("scope") or "capability_passphrase_reply"),
            passphrase=passphrase,
        )
        used.append(result)
        if not result.get("ok"):
            metadata["capability_passphrase"] = {
                **challenge,
                "status": "waiting",
                "last_failed_at": datetime.now(timezone.utc).isoformat(),
                "last_failure_reason": str(result.get("reason") or "passphrase_verification_failed"),
                "attempt_count": int(challenge.get("attempt_count") or 0) + 1,
            }
            with queue.session() as db:
                db.execute(
                    """
                    UPDATE mobile_tasks
                    SET metadata_json=?,
                        updated_at=?
                    WHERE id=? AND status='waiting_capability_passphrase'
                    """,
                    (json.dumps(metadata, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), str(waiting.get("id") or "")),
                )
            if str(result.get("reason") or "") in {"passphrase_locked", "grant_not_active", "grant_not_found"}:
                close_capability_passphrase_wait(
                    queue,
                    str(waiting.get("id") or ""),
                    status="rejected",
                    error=str(result.get("reason") or "passphrase_verification_failed"),
                    event_type="permission_capability_passphrase_closed",
                    detail={"task_id": str(waiting.get("id") or ""), "reason": str(result.get("reason") or "")},
                )
            return {
                "ok": False,
                "handled": True,
                "task_id": waiting.get("id"),
                "reason": str(result.get("reason") or "passphrase_verification_failed"),
                "reply_text": "口令验证失败或令牌已失效，请检查口令，或让管理员重新授权。",
                "capability_token": result,
            }
    now = datetime.now(timezone.utc).isoformat()
    metadata["capability_passphrase"] = {
        **challenge,
        "status": "verified",
        "verified_at": now,
        "used_grants": [item.get("grant", {}) for item in used if isinstance(item, dict)],
    }
    with queue.session() as db:
        db.execute(
            """
            UPDATE mobile_tasks
            SET status='pending',
                error='',
                metadata_json=?,
                updated_at=?
            WHERE id=? AND status='waiting_capability_passphrase'
            """,
            (json.dumps(metadata, ensure_ascii=False), now, str(waiting.get("id") or "")),
        )
    queue.add_event(
        "local",
        "permission_capability_passphrase_verified",
        {"grant_ids": sorted(set(grant_ids)), "required_actions": challenge.get("required_actions", [])},
        str(waiting.get("id") or ""),
    )
    return {
        "ok": True,
        "handled": True,
        "task_id": waiting.get("id"),
        "status": "pending",
        "reply_text": "口令验证通过，原请求已恢复执行。",
    }


def task_capability_passphrase_verified(task: dict[str, Any], required_actions: list[str], scope: str) -> bool:
    metadata = {}
    try:
        metadata = json.loads(str(task.get("metadata_json") or "{}"))
    except Exception:
        metadata = {}
    challenge = metadata.get("capability_passphrase") if isinstance(metadata.get("capability_passphrase"), dict) else {}
    if str(challenge.get("status") or "") != "verified":
        return False
    verified_actions = {str(item) for item in (challenge.get("required_actions") or []) if str(item)}
    required = {str(item) for item in required_actions if str(item)}
    if not required.issubset(verified_actions):
        return False
    verified_scope = str(challenge.get("scope") or "")
    return not scope or not verified_scope or verified_scope == str(scope or "")


def enforce_ask_scope_for_task(queue: MobileQueue, config: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    command = str(task.get("command") or "").strip().lower()
    if command not in {"", "/ask", "/report", "/analyze", "/memory"}:
        return {"ok": True, "allowed": True, "skipped": True, "reason": "not an ask-style command"}
    text = str(task.get("text") or "")
    actor = str(task.get("external_user") or "")
    account_id = str(task.get("receiver_account_id") or "")
    account_map = permission_account_map(config)
    ask_decision = permission_policy.decide(config, actor, "ask", account_id, account_map)
    scope_decision = permission_policy.classify_ask_scope(text)
    detail = {
        "actor": actor,
        "account_id": account_id,
        "command": command or "/ask",
        "ask_permission": ask_decision.to_dict(),
        "ask_scope": scope_decision.to_dict(),
        "policy": "ask obvious-denial guard applies to non-admin users; admin is superuser and is audited instead of rejected",
    }
    if not ask_decision.allowed:
        return {"ok": False, "allowed": False, "reason": ask_decision.reason, "detail": detail}
    if ask_decision.role == "admin":
        if not scope_decision.allowed or ask_decision.implicit_admin_allow:
            detail["audit_required"] = True
            detail["admin_superuser"] = True
            audit_task_id = str(task.get("id") or "")
            if audit_task_id and not queue.get_task(audit_task_id):
                audit_task_id = ""
            queue.add_event("local", "admin_superuser_ask_guard_bypassed", detail, audit_task_id or None)
        return {"ok": True, "allowed": True, "reason": "admin superuser allowed; ask guard audited", "detail": detail}
    if scope_decision.allowed:
        return {"ok": True, "allowed": True, "reason": scope_decision.reason, "detail": detail}
    # Temporary grants prove eligibility for a narrow capability challenge, not
    # authorization for this task. Use the role's base capabilities to decide
    # whether a restricted /ask scope still needs the token/passphrase gate.
    capabilities = set(permission_policy.capabilities_for_role(ask_decision.role))
    missing = [action for action in scope_decision.required_actions if action not in capabilities]
    if missing:
        grantable_missing = [action for action in missing if action in capability_tokens.GRANTABLE_CAPABILITIES]
        non_grantable_missing = [action for action in missing if action not in capability_tokens.GRANTABLE_CAPABILITIES]
        if grantable_missing and not non_grantable_missing:
            if task_capability_passphrase_verified(task, list(scope_decision.required_actions), scope_decision.scope):
                task_id = str(task.get("id") or "")
                if task_id and not task_event_exists(queue, task_id, "permission_capability_passphrase_gate_allowed"):
                    queue.add_event(
                        "local",
                        "permission_capability_passphrase_gate_allowed",
                        {
                            "actor": actor,
                            "account_id": account_id,
                            "scope": scope_decision.scope,
                            "required_actions": list(scope_decision.required_actions),
                            "policy": "follow-up passphrase already verified; original task may continue through the normal Codex pipeline",
                        },
                        task_id,
                    )
                detail["capability_token"] = {
                    "ok": True,
                    "passphrase_verified": True,
                    "policy": "temporary generated-artifact capability was verified by the follow-up passphrase before dispatch",
                }
                return {"ok": True, "allowed": True, "reason": "capability passphrase verified", "detail": detail}
            if not account_id:
                detail["missing_actions"] = missing
                detail["capability_token"] = {"ok": False, "reason": "receiver_account_id_required_for_generated_artifact_scope"}
                return {"ok": False, "allowed": False, "reason": scope_decision.reason, "detail": detail}
            task_id = str(task.get("id") or "")
            existing_wait = find_waiting_capability_passphrase_task(
                queue,
                actor=actor,
                account_id=account_id,
                conversation=str(task.get("external_conversation") or ""),
                exclude_task_id=task_id,
            )
            if existing_wait:
                detail["existing_waiting_capability_passphrase_task"] = {
                    "id": str(existing_wait.get("id") or ""),
                    "created_at": str(existing_wait.get("created_at") or ""),
                    "updated_at": str(existing_wait.get("updated_at") or ""),
                }
                return {
                    "ok": False,
                    "allowed": False,
                    "reason": "capability_passphrase_wait_already_active",
                    "wait_conflict": True,
                    "detail": detail,
                }
            grants = []
            for action in grantable_missing:
                grant_item = capability_tokens.find_grant(account_id=account_id, actor=actor, capability=action)
                if not grant_item:
                    detail["missing_actions"] = missing
                    detail["capability_token"] = {"ok": False, "reason": f"missing_active_grant_for_{action}"}
                    return {"ok": False, "allowed": False, "reason": scope_decision.reason, "detail": detail}
                grants.append(grant_item)
            if any(capability_tokens.passphrase_required(item) for item in grants):
                wait_detail = {
                    **detail,
                    "required_actions": list(scope_decision.required_actions),
                    "scope": scope_decision.scope,
                    "grant_ids": [str(item.get("grant_id") or "") for item in grants],
                    "capability_token": {
                        "ok": True,
                        "passphrase_required": True,
                        "generated_artifact_dir": str(capability_tokens.generated_artifact_dir(account_id)),
                        "policy": "active temporary capability token found; follow-up passphrase is required before consuming the grant",
                    },
                }
                mark_task_waiting_capability_passphrase(queue, str(task.get("id") or ""), wait_detail)
                return {"ok": False, "allowed": False, "reason": "capability_passphrase_required", "wait_for_passphrase": True, "detail": wait_detail}
            passphrase = ""
            if task_id and not task_event_exists(queue, task_id, "permission_capability_grant_used"):
                used: list[dict[str, Any]] = []
                unique_grants = {str(item.get("grant_id") or ""): item for item in grants if str(item.get("grant_id") or "")}
                for grant_item in unique_grants.values():
                    result = capability_tokens.consume_grant(
                        grant_id=str(grant_item.get("grant_id") or ""),
                        task_id=task_id,
                        capability=",".join(grantable_missing),
                        reason=scope_decision.scope,
                        passphrase=passphrase,
                    )
                    used.append(result)
                    if not result.get("ok"):
                        detail["capability_token"] = result
                        return {"ok": False, "allowed": False, "reason": str(result.get("reason") or scope_decision.reason), "detail": detail}
                queue.add_event(
                    "local",
                    "permission_capability_grant_used",
                    {
                        "actor": actor,
                        "account_id": account_id,
                        "scope": scope_decision.scope,
                        "required_actions": list(scope_decision.required_actions),
                        "generated_artifact_dir": str(capability_tokens.generated_artifact_dir(account_id)),
                        "grants": [item.get("grant", {}) for item in used if isinstance(item, dict)],
                    },
                    task_id,
                )
                redact_task_capability_passphrase(queue, task_id, text, passphrase)
                task["text"] = redact_capability_passphrase_value(text, passphrase)
            detail["capability_token"] = {
                "ok": True,
                "grant_ids": [str(item.get("grant_id") or "") for item in grants],
                "generated_artifact_dir": str(capability_tokens.generated_artifact_dir(account_id)),
                "passphrase_required": any(capability_tokens.passphrase_required(item) for item in grants),
                "policy": "temporary grant only permits generated artifact create/send inside the account attachment directory; passphrases are verified before use and redacted before Codex dispatch",
            }
            return {"ok": True, "allowed": True, "reason": "active temporary capability token allowed generated artifact scope", "detail": detail}
        detail["missing_actions"] = missing
        return {"ok": False, "allowed": False, "reason": scope_decision.reason, "detail": detail}
    return {"ok": True, "allowed": True, "reason": "required ask scope actions are explicitly granted", "detail": detail}


def capability_passphrase_state_machine_check() -> dict[str, Any]:
    return run_capability_passphrase_regression_check("capability_passphrase_state_machine_check", globals())


CONTROL_TASK_COMMANDS = {"status", "stop", "resume", "hardstop", "confirm", "cancel"}


def task_command_value(task: dict[str, Any]) -> str:
    return str(task.get("command") or "").strip().lower().lstrip("/")


def task_is_control_task(task: dict[str, Any]) -> bool:
    command = task_command_value(task)
    if command in CONTROL_TASK_COMMANDS:
        return True
    return bool(exact_control_command(str(task.get("text") or "")))


def task_can_join_supplement(task: dict[str, Any]) -> bool:
    status = str(task.get("status") or "").strip().lower()
    if status and status not in {"pending", "queued_for_codex", "sent_to_codex", "processing"}:
        return False
    risk_level = str(task.get("risk_level") or "").strip().upper()
    if risk_level in {"L2", "L3"}:
        return False
    return not task_is_control_task(task)


def task_can_be_same_turn_supplement(queue: MobileQueue, task: dict[str, Any]) -> bool:
    """Return False for released final-reply owners that must be retried FIFO."""
    task_id = str(task.get("id") or "")
    if task_id and task_is_released_final_reply_owner(queue, task_id):
        return False
    if task_id and latest_followup_trigger_owner(queue, task_id):
        return False
    return task_can_join_supplement(task)


def stable_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:24]


def extract_recent_log_messages(log_path: Path, max_lines: int) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    recent = lines[-max_lines:]
    messages: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for line in recent:
        first = INBOUND_MESSAGE_RE.search(line)
        if first:
            pending = {
                "from": first.group("from"),
                "types": first.group("types"),
                "raw_line": line,
            }
            continue
        detail = INBOUND_DETAIL_RE.search(line)
        if detail:
            item = dict(pending or {})
            item.update(
                {
                    "from": detail.group("from"),
                    "to": detail.group("to"),
                    "body_len": int(detail.group("body_len")),
                    "has_media": detail.group("has_media").lower() == "true",
                    "detail_line": line,
                }
            )
            messages.append(item)
            pending = None
    return messages


def scan_stop_log(queue: MobileQueue, config_path: Path, config: dict[str, Any], max_lines: int) -> dict[str, Any]:
    log_path = Path(config.get("openclaw", {}).get("log_path") or "")
    if not log_path.exists():
        return {"ok": False, "action": "scan_stop_log", "reason": f"log not found: {log_path}"}
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    for line in reversed(lines):
        match = LOG_STOP_RE.search(line)
        if not match:
            continue
        external_user = (match.group("from") or match.group("from2") or "").strip()
        decision = permission_policy.decide(config, external_user, "stop")
        if not external_user or not decision.allowed:
            if external_user:
                queue.add_event(
                    "openclaw-weixin",
                    "control_rejected",
                    {"command": "stop", "source": "scan_stop_log", "reason": decision.reason},
                )
            continue
        result = emergency_stop(queue, config_path, config, actor=f"log:{external_user}")
        result["source"] = "scan_stop_log"
        result["matched_user"] = external_user
        return result
    return {"ok": True, "action": "scan_stop_log", "matched": False, "log_path": str(log_path), "lines_checked": len(lines)}


FAILED_RESULT_ERROR_SUBSTRINGS = (
    "codex app-server client failed:",
    "codex delivery layer is not implemented",
    "timed out after",
    "command '['node'",
    "dispatch failed",
    "transport closed",
    "mcp transport closed",
    "connection refused",
    "connectex",
)


def result_looks_like_failed_transport_error(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in FAILED_RESULT_ERROR_SUBSTRINGS)


def short_value(value: Any, limit: int = 160) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def failed_result_has_recoverable_reply_text(task_row: dict[str, Any]) -> bool:
    raw_result = str(task_row.get("result") or "").strip()
    if not raw_result or raw_result.startswith("[supplement]"):
        return False
    cleaned = strip_mobile_result_markers(raw_result).strip()
    if not is_usable_owned_result_text(cleaned):
        return False
    if not cleaned:
        return False
    if not result_looks_like_failed_transport_error(cleaned):
        return True
    return bool(
        re.search(
            r"\[\[mobile_result_begin:[^\]]+\]\].+?\[\[mobile_result_end:[^\]]+\]\]",
            raw_result,
            re.S,
        )
    )


def task_batch_key(task_id: str) -> str:
    return f"codex_batch:{task_id}"


def task_expected_ids_key(task_id: str) -> str:
    return f"codex_expected_task_ids:{task_id}"


def task_ack_code_key(task_id: str) -> str:
    return f"mobile_ack_code:{task_id}"


def task_result_code_key(task_id: str) -> str:
    return f"mobile_result_code:{task_id}"


def delivery_group_members_key(owner_task_id: str) -> str:
    return f"delivery_group_members:{owner_task_id}"


def task_prompt(
    tasks: list[dict[str, Any]],
    continuation: dict[str, Any] | None = None,
    mobile_batch_id: str = "",
    bridge_thread_id: str = "",
    result_owner_task_ids: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    return build_task_prompt(
        tasks,
        continuation=continuation,
        mobile_batch_id=mobile_batch_id,
        bridge_thread_id=bridge_thread_id,
        result_owner_task_ids=result_owner_task_ids,
        config=config,
        task_can_join_supplement=task_can_join_supplement,
        task_capability_passphrase_verified=task_capability_passphrase_verified,
        permission_account_map=permission_account_map,
    )


def final_reply_prompt_contract_gate(prompt: str, expected_task_ids: list[str]) -> dict[str, Any]:
    """Block legacy base final-reply prompt contracts before Codex dispatch."""

    validation = validate_final_reply_prompt_contract(prompt, expected_task_ids)
    if validation.get("ok"):
        return {"ok": True, "validation": validation}
    return {
        "ok": False,
        "reason": "mobile_prompt_contract_invalid",
        "validation": validation,
    }


def dispatch_to_codex_stub(
    tasks: list[dict[str, Any]],
    thread_id: str,
    continuation: dict[str, Any] | None = None,
    result_owner_task_ids: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "reason": "codex delivery layer is not implemented for background use yet",
        "thread_id": thread_id,
        "prompt": task_prompt(
            tasks,
            continuation,
            bridge_thread_id=thread_id,
            result_owner_task_ids=result_owner_task_ids,
            config=config,
        ),
    }


def dispatch_to_codex_cdp(
    tasks: list[dict[str, Any]],
    thread_id: str,
    config: dict[str, Any],
    continuation: dict[str, Any] | None = None,
    result_owner_task_ids: list[str] | None = None,
) -> dict[str, Any]:
    if result_owner_task_ids is None:
        result_owner_task_ids = list(config.get("_delivery_group_result_owner_task_ids") or [])
    startup = ensure_codex_cdp(config)
    if not startup.get("ok"):
        return {
            "ok": False,
            "reason": "codex cdp is not ready",
            "startup": startup,
            "thread_id": thread_id,
            "prompt": task_prompt(
                tasks,
                continuation,
                bridge_thread_id=thread_id,
                result_owner_task_ids=result_owner_task_ids,
                config=config,
            ),
            "mode": "codex-cdp",
        }
    mobile_batch_id = make_mobile_batch_id(tasks)
    all_task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
    owner_id_set = {str(item) for item in (result_owner_task_ids or []) if str(item)}
    expected_task_ids = [task_id for task_id in all_task_ids if task_id in owner_id_set] if owner_id_set else all_task_ids
    protocol_tasks = [task for task in tasks if str(task.get("id") or "") in set(expected_task_ids)]
    protocols = mobile_protocols(protocol_tasks, mobile_batch_id)
    prompt = task_prompt(
        tasks,
        continuation,
        mobile_batch_id=mobile_batch_id,
        bridge_thread_id=thread_id,
        result_owner_task_ids=expected_task_ids,
        config=config,
    )
    contract_gate = final_reply_prompt_contract_gate(prompt, expected_task_ids)
    if not contract_gate.get("ok"):
        return {
            "ok": False,
            "reason": contract_gate.get("reason") or "mobile_prompt_contract_invalid",
            "thread_id": thread_id,
            "mode": "codex-cdp",
            "client_user_message_id": mobile_batch_id,
            "expected_task_ids": expected_task_ids,
            "mobile_protocols": protocols,
            "prompt_contract": contract_gate,
            "prompt": prompt,
            "startup": startup,
        }
    settings = codex_cdp_config(config)
    trigger = config.get("trigger", {})
    auto_reply = bool(trigger.get("auto_reply", False))
    result_timeout_seconds = int(trigger.get("result_timeout_seconds") or 300)
    timeout_seconds = max(int(trigger.get("delivery_timeout_seconds") or 20), settings["start_timeout"] + 10)
    if not settings["script"].exists():
        return {
            "ok": False,
            "reason": f"codex CDP delivery script not found: {settings['script']}",
            "thread_id": thread_id,
            "prompt": prompt,
            "startup": startup,
        }
    command = [
        settings["node"],
        str(settings["script"]),
        "--host",
        settings["host"],
        "--port",
        str(settings["port"]),
        "--expected-task-ids",
        ",".join(expected_task_ids),
    ]
    result_codes = mobile_result_codes_arg(protocols)
    if result_codes:
        command.extend(["--expected-result-codes", result_codes])
    if auto_reply:
        command.extend(
            [
                "--wait-result",
                "--wait-timeout-ms",
                str(result_timeout_seconds * 1000),
                "--settle-ms",
                str(int(trigger.get("result_settle_ms") or 5000)),
            ]
        )
        timeout_seconds = max(timeout_seconds, result_timeout_seconds + 30)
    try:
        proc = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout_seconds,
            cwd=str(PROJECT_ROOT / "_tools" / "codex-cdp-tools"),
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"codex CDP delivery failed to start: {exc}",
            "thread_id": thread_id,
            "prompt": prompt,
            "startup": startup,
        }
    parsed: dict[str, Any]
    try:
        parsed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        parsed = {"ok": False, "raw_stdout": proc.stdout}
    submission_confirmed = (
        parsed.get("submissionConfirmed")
        if "submissionConfirmed" in parsed
        else parsed.get("bodyHasPrompt")
    )
    desktop_visible = {
        "confirmed": bool(submission_confirmed),
        "body_has_exact_prompt": bool(parsed.get("bodyHasExactPrompt", parsed.get("bodyHasPrompt"))),
        "target": parsed.get("target") or {},
        "baseline_key": str(parsed.get("baselineKey") or parsed.get("baseline_key") or ""),
        "baseline_count": parsed.get("baselineCount"),
        "filled_chars": parsed.get("filledChars"),
        "composer_after": str(parsed.get("composerAfter") or "")[:200],
        "submission_check": parsed.get("submissionCheck") or {},
    }
    process_ok = proc.returncode == 0 and bool(parsed.get("ok"))
    submission_unconfirmed = bool(process_ok and not desktop_visible["confirmed"])
    ok = bool(process_ok)
    reason = ""
    if not process_ok:
        reason = str(parsed.get("reason") or "cdp_visible_input_failed")
    elif submission_unconfirmed:
        reason = "cdp_visible_input_unconfirmed_observing"
    return {
        "ok": ok,
        "delivery_accepted": ok,
        "submission_confirmed": bool(desktop_visible["confirmed"]),
        "submission_unconfirmed": submission_unconfirmed,
        "reason": reason,
        "diagnostic_only": bool(submission_unconfirmed),
        "thread_id": thread_id,
        "mode": "codex-cdp",
        "turn_id": str(parsed.get("turnId") or parsed.get("turn_id") or "cdp-visible-turn"),
        "baseline_key": str(parsed.get("baselineKey") or parsed.get("baseline_key") or ""),
        "client_user_message_id": mobile_batch_id,
        "expected_task_ids": expected_task_ids,
        "mobile_protocols": protocols,
        "prompt_contract": contract_gate,
        "desktop_visible": desktop_visible,
        "returncode": proc.returncode,
        "stdout": parsed,
        "stderr": (proc.stderr or "")[-2000:],
        "prompt": prompt,
        "startup": startup,
    }


def app_server_result_poll_second_chance_timeout_seconds(config: dict[str, Any]) -> int:
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    base = max(1, int(trigger.get("delivery_timeout_seconds") or 20))
    configured = int(trigger.get("app_server_result_poll_second_chance_timeout_seconds") or 30)
    return max(base + 5, configured)


def app_server_result_poll_was_timeout(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict) or bool(result.get("ok")):
        return False
    parts = [
        result.get("reason"),
        result.get("error"),
        result.get("stderr"),
        result.get("raw_stdout"),
    ]
    text = "\n".join(str(part or "") for part in parts)
    return bool(re.search(r"\btimeout\b|timed out|TimeoutExpired", text, re.I))


def inspect_codex_thread_for_dispatch(
    config: dict[str, Any],
    thread_id: str,
    thread_name: str = "",
) -> dict[str, Any]:
    """Dispatch probe wrapper kept in the CLI so regression monkeypatches work."""
    light_probe = inspect_codex_thread_app_server(config, thread_id, thread_name, light=True)
    status_type = codex_thread_status_type(light_probe.get("listed_status")).lower()
    if not bool(light_probe.get("ok")) or not bool(light_probe.get("listed")):
        full_probe = inspect_codex_thread_app_server(
            config,
            thread_id,
            thread_name,
            stabilize_name=bool(thread_name),
            light=False,
        )
        if bool(full_probe.get("ok")):
            return full_probe
    if bool(light_probe.get("ok")) and bool(light_probe.get("listed")) and status_type in {"notloaded", "loading", "unloaded"}:
        return light_probe
    return light_probe


def poll_codex_thread_history_owned_result(
    config: dict[str, Any],
    thread_id: str,
    turn_id: str,
    client_message_id: str,
    expected_task_ids: list[str],
    expected_result_codes: dict[str, str],
    expected_ack_codes: dict[str, str],
) -> dict[str, Any]:
    """Read durable Codex thread history for an exact owned mobile_result block.

    Visible-CDP polling can observe a stale/empty DOM node after a later Codex
    final answer has already been persisted to the thread. This fallback uses
    the app-server read API only, and still requires exact task/result markers.
    """
    if not thread_id or not turn_id or not expected_result_codes:
        return {}
    history_config = dict(config)
    trigger = dict(config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {})
    trigger["delivery_mode"] = "codex-app-server"
    history_config["trigger"] = trigger
    startup = ensure_codex_app_server(history_config)
    if not startup.get("ok"):
        result = dict(startup)
        result.update(
            {
                "ok": False,
                "healthy": False,
                "mode": "codex-thread-history",
                "thread_history_fallback": True,
                "reason": result.get("reason") or "codex_app_server_unavailable",
            }
        )
        return result
    args = ["--poll-result", "--thread-id", thread_id, "--turn-id", turn_id]
    if client_message_id:
        args.extend(["--client-message-id", client_message_id])
    if expected_task_ids:
        args.extend(["--expected-task-ids", ",".join([str(item) for item in expected_task_ids if str(item)] )])
    if expected_result_codes:
        result_codes = ",".join(
            f"{task_id}={code}"
            for task_id, code in expected_result_codes.items()
            if task_id and code
        )
        if result_codes:
            args.extend(["--expected-result-codes", result_codes])
    if expected_ack_codes:
        ack_codes = ",".join(
            f"{task_id}={code}"
            for task_id, code in expected_ack_codes.items()
            if task_id and code
        )
        if ack_codes:
            args.extend(["--expected-ack-codes", ack_codes])
    parsed = run_codex_app_server_client(history_config, args, timeout_extra_seconds=45)
    parsed["startup"] = startup
    parsed["mode"] = "codex-thread-history"
    parsed["thread_history_fallback"] = True
    return parsed


def thread_routes_ui_health(config: dict[str, Any], limit: int = 10) -> dict[str, Any]:
    routes = []
    warning_count = 0
    fatal_count = 0
    recoverable_count = 0
    state_counts = {"ready": 0, "prewarm": 0, "probe_failed": 0, "unavailable": 0, "busy": 0}
    for item in thread_items(config)[: max(1, limit)]:
        thread_id = str(item.get("thread_id") or "")
        probe = inspect_codex_thread_for_dispatch(config, thread_id, str(item.get("name") or ""))
        status_type = codex_thread_status_type(probe.get("listed_status")).lower()
        listed = bool(probe.get("listed"))
        dispatch_state = codex_thread_dispatch_state(probe)
        state = str(dispatch_state.get("state") or "")
        if state in state_counts:
            state_counts[state] += 1
        dispatch_ok = bool(dispatch_state.get("ok"))
        recoverable = dispatch_ok and (
            not listed or state == "prewarm" or status_type in {"notloaded", "loading", "unloaded"}
        )
        fatal = state in {"unavailable", "probe_failed"} or (not bool(probe.get("ok")))
        healthy = dispatch_ok and not fatal and not recoverable
        if state == "ready" and bool(probe.get("ok")):
            healthy = True
        if recoverable:
            recoverable_count += 1
            warning_count += 1
        if fatal:
            fatal_count += 1
            warning_count += 1
        routes.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "thread_id": thread_id,
                "ok": bool(probe.get("ok")),
                "healthy": healthy,
                "recoverable": recoverable,
                "fatal": fatal,
                "severity": "fatal" if fatal else ("recoverable" if recoverable else "ok"),
                "listed": listed,
                "listed_status": probe.get("listed_status"),
                "reason": (
                    "thread is not listed but resume/read checks passed; worker can still dispatch"
                    if recoverable and not listed
                    else "thread is listed but not loaded; worker can dispatch and schedules background prewarm"
                    if recoverable
                    else "thread is available to Codex app-server"
                    if healthy
                    else "thread is unavailable to Codex app-server"
                    if fatal
                    else probe.get("reason") or probe.get("error")
                ),
            }
        )
    return {
        "ok": bool(routes) and fatal_count == 0 and all(bool(item.get("ok")) for item in routes),
        "healthy": bool(routes) and fatal_count == 0,
        "warning_count": warning_count,
        "recoverable_count": recoverable_count,
        "fatal_count": fatal_count,
        "checked": len(routes),
        "state_counts": state_counts,
        "routes": routes,
    }


def codex_thread_status_type(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("type") or "").strip()
    return str(value or "").strip()


def codex_thread_has_busy_signals(probe: dict[str, Any]) -> bool:
    status = probe.get("listed_status")
    status_type = codex_thread_status_type(status).lower()
    flags: list[str] = []
    if isinstance(status, dict) and isinstance(status.get("activeFlags"), list):
        flags = [str(item).strip().lower() for item in status.get("activeFlags") if str(item).strip()]
    if flags:
        return any(
            any(token in flag for token in ("generat", "running", "inprogress", "busy", "thinking"))
            for flag in flags
        )
    return status_type in {"running", "inprogress", "busy", "generating"}


def codex_thread_dispatch_state(probe: dict[str, Any]) -> dict[str, Any]:
    status = probe.get("listed_status")
    status_type = codex_thread_status_type(status).lower()
    ok = bool(probe.get("ok"))
    listed = bool(probe.get("listed"))
    resume_ok = bool(probe.get("resume_ok"))
    turns_ok = bool(probe.get("turns_ok"))
    if not ok:
        transient = health_result_is_transient_probe_failure(probe)
        return {
            "state": "probe_failed" if transient else "unavailable",
            "ok": False,
            "listed": listed,
            "status_type": status_type,
            "reason": str(probe.get("reason") or probe.get("error") or ""),
            "transient": transient,
        }
    if status_type in {"unknown", "error", "missing", "deleted"}:
        return {
            "state": "unavailable",
            "ok": True,
            "listed": listed,
            "status_type": status_type,
            "reason": str(probe.get("reason") or probe.get("error") or ""),
            "transient": False,
        }
    if listed and status_type in {"notloaded", "loading", "unloaded"}:
        return {
            "state": "prewarm",
            "ok": True,
            "listed": listed,
            "status_type": status_type,
            "reason": "thread listed but not loaded",
            "transient": False,
        }
    if not listed and not (resume_ok and turns_ok):
        return {
            "state": "unavailable",
            "ok": True,
            "listed": listed,
            "status_type": status_type,
            "reason": str(probe.get("reason") or probe.get("error") or ""),
            "transient": False,
        }
    if codex_thread_has_busy_signals(probe):
        return {
            "state": "busy",
            "ok": True,
            "listed": listed,
            "status_type": status_type,
            "reason": "thread is busy",
            "transient": False,
        }
    return {
        "state": "ready",
        "ok": True,
        "listed": listed,
        "status_type": status_type,
        "reason": "",
        "transient": False,
    }


def codex_thread_is_unavailable(probe: dict[str, Any]) -> bool:
    state = codex_thread_dispatch_state(probe)
    return str(state.get("state") or "") == "unavailable"


def codex_thread_needs_background_prewarm(probe: dict[str, Any]) -> bool:
    state = codex_thread_dispatch_state(probe)
    return str(state.get("state") or "") == "prewarm"


def prewarm_codex_thread_app_server(
    config: dict[str, Any],
    thread_id: str,
    thread_name: str = "",
) -> dict[str, Any]:
    before = inspect_codex_thread_app_server(config, thread_id, thread_name, light=True)
    if not (codex_thread_is_unavailable(before) or codex_thread_needs_background_prewarm(before)):
        return {"ok": True, "prewarmed": False, "before": before, "after": before}
    prewarm_config = dict(config)
    prewarm_config["trigger"] = dict(config.get("trigger", {}))
    prewarm_config["trigger"]["delivery_timeout_seconds"] = max(
        2,
        int(config.get("trigger", {}).get("thread_prewarm_timeout_seconds") or 5),
    )
    warmed = inspect_codex_thread_app_server(
        prewarm_config,
        thread_id,
        thread_name,
        stabilize_name=bool(thread_name),
        light=False,
    )
    after = inspect_codex_thread_app_server(prewarm_config, thread_id, thread_name, light=True)
    ready = (
        bool(after.get("ok"))
        and bool(after.get("listed"))
        and not codex_thread_is_unavailable(after)
        and not codex_thread_needs_background_prewarm(after)
    )
    return {
        "ok": ready,
        "prewarmed": True,
        "before": before,
        "warm": warmed,
        "after": after,
    }


def start_thread_prewarm_background(
    config_path: Path,
    thread_id: str,
    thread_name: str = "",
) -> dict[str, Any]:
    bundled_python = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe"
    python = str(bundled_python if bundled_python.exists() else "python")
    args = [
        str(ROOT / "mobile_openclaw_cli.py"),
        "--config",
        str(config_path),
        "thread-prewarm",
        "--thread-id",
        thread_id,
    ]
    if thread_name:
        args.extend(["--thread-name", thread_name])
    quoted_args = "@(" + ",".join("'" + arg.replace("'", "''") + "'" for arg in args) + ")"
    ps = (
        f"Start-Process -FilePath '{python.replace(chr(39), chr(39) * 2)}' "
        f"-ArgumentList {quoted_args} -WorkingDirectory '{str(ROOT).replace(chr(39), chr(39) * 2)}' "
        "-WindowStyle Hidden"
    )
    result = run_powershell(ps, timeout=10)
    result["command"] = [python, *args]
    return result


def run_thread_prewarm(queue: MobileQueue, config: dict[str, Any], thread_id: str, thread_name: str = "") -> dict[str, Any]:
    config_path = Path(str(config.get("_config_path") or DEFAULT_CONFIG))
    started_at = datetime.now(timezone.utc)
    result = prewarm_codex_thread_app_server(config, thread_id, thread_name)
    payload = {
        "thread_id": thread_id,
        "thread_name": thread_name,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "ok": bool(result.get("ok")),
        "result": result,
    }
    queue.add_event("local", "thread_prewarm_finished", payload)
    if result.get("ok"):
        clear_thread_prewarm(queue, thread_id)
    else:
        mark_thread_prewarm(queue, config, thread_id, thread_name, "prewarm_failed")
    if config_path:
        payload["config_path"] = str(config_path)
    return payload


def codex_thread_is_busy(probe: dict[str, Any]) -> bool:
    state = codex_thread_dispatch_state(probe)
    return str(state.get("state") or "") == "busy"


def codex_turn_needs_retry(poll: dict[str, Any]) -> bool:
    status = str(poll.get("status") or "").strip().lower()
    if bool(poll.get("mcp_transport_closed")) or bool(poll.get("retryable_tool_failure")):
        return True
    return status in {"interrupted", "failed", "cancelled", "canceled", "notfound"}


def poll_has_mcp_transport_closed(poll: dict[str, Any]) -> bool:
    if not isinstance(poll, dict):
        return False
    if bool(poll.get("mcp_transport_closed")):
        return True
    failures = []
    for key in ("bridge_failures", "native_failures"):
        value = poll.get(key)
        if isinstance(value, list):
            failures.extend(value)
    for item in failures:
        try:
            text = json.dumps(item, ensure_ascii=False)
        except Exception:
            text = str(item)
        if re.search(r"mobile-openclaw-bridge|bridge\.get_pending_batch|bridge\.poll_updates|bridge\.ack_message|bridge\.publish_supplement", text, re.I) and re.search(r"transport closed|tool call failed", text, re.I):
            return True
    return False


def poll_has_mobile_ack(poll: dict[str, Any]) -> bool:
    if not isinstance(poll, dict):
        return False
    if bool(poll.get("ack_seen")):
        return True
    ownership = poll.get("ownership")
    if isinstance(ownership, dict) and bool(ownership.get("ack_seen")):
        return True
    return False


def poll_status_is_in_progress(poll: dict[str, Any]) -> bool:
    return str((poll or {}).get("status") or "").strip().lower() in {"inprogress", "running", "processing"}


def poll_status_is_terminal(poll: dict[str, Any]) -> bool:
    if not isinstance(poll, dict):
        return False
    status = str(poll.get("status") or "").strip().lower()
    return bool(poll.get("terminal_without_text")) or status in {
        "completed",
        "complete",
        "done",
        "stopped",
        "interrupted",
        "failed",
        "cancelled",
        "canceled",
        "notfound",
    }


def poll_is_base_ack_only_terminal(poll: dict[str, Any]) -> bool:
    """Return true when a mobile-boundary turn ended after ack but before result."""
    if not isinstance(poll, dict):
        return False
    return bool(
        poll_has_mobile_ack(poll)
        and not bool(poll.get("result_complete"))
        and not poll_generation_is_active(poll)
        and not poll_status_is_in_progress(poll)
        and poll_status_is_terminal(poll)
        and not str(poll.get("newText") or "").strip()
    )


def poll_generation_is_active(poll: dict[str, Any]) -> bool:
    if not isinstance(poll, dict):
        return False
    if bool(poll.get("generationActive")):
        return True
    status = str(poll.get("status") or "").strip().lower()
    return status in {"inprogress", "processing"}


STALLED_TOOL_RECOVERY_ALLOWLIST = {
    "load_workspace_dependencies",
}


def stalled_tool_recovery_after_seconds(config: dict[str, Any]) -> int:
    return max(60, int(config.get("trigger", {}).get("stalled_tool_recovery_after_seconds") or 300))


def poll_in_progress_tools(poll: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(poll, dict):
        return []
    value = poll.get("in_progress_tools")
    if not isinstance(value, list):
        return []
    tools: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            tools.append(item)
    return tools


def tool_identity(tool: dict[str, Any]) -> str:
    parts = [
        str(tool.get("title") or ""),
        str(tool.get("name") or ""),
        str(tool.get("tool") or ""),
        str(tool.get("namespace") or ""),
        str(tool.get("server") or ""),
        str(tool.get("type") or ""),
        str(tool.get("status") or ""),
        str(tool.get("phase") or ""),
        str(tool.get("text") or ""),
    ]
    return "\n".join(parts)


def poll_has_stalled_recoverable_tool(
    poll: dict[str, Any],
    config: dict[str, Any],
    waited_seconds: int,
) -> tuple[bool, dict[str, Any]]:
    """Return True only for narrow quick-tool hangs that can safely requeue.

    Normal long-running tools must stay observed. This covers host-side helper
    calls that should finish quickly and otherwise strand a mobile-owned turn
    after the mobile ack marker has already been emitted.
    """
    threshold = stalled_tool_recovery_after_seconds(config)
    if waited_seconds < threshold:
        return False, {"waited_seconds": waited_seconds, "threshold_seconds": threshold}
    if not (poll_has_mobile_ack(poll) and poll_status_is_in_progress(poll)):
        return False, {"waited_seconds": waited_seconds, "threshold_seconds": threshold}
    if str(poll.get("newText") or "").strip():
        return False, {"waited_seconds": waited_seconds, "threshold_seconds": threshold, "reason": "has_text"}
    if bool(poll.get("result_complete")):
        return False, {"waited_seconds": waited_seconds, "threshold_seconds": threshold, "reason": "result_complete"}

    configured = config.get("trigger", {}).get("stalled_tool_recovery_allowlist")
    allowlist = STALLED_TOOL_RECOVERY_ALLOWLIST
    if isinstance(configured, list) and configured:
        allowlist = {str(item).strip() for item in configured if str(item).strip()}
    matched: list[dict[str, Any]] = []
    for tool in poll_in_progress_tools(poll):
        identity = tool_identity(tool)
        if any(name and name in identity for name in allowlist):
            matched.append(tool)
    if not matched:
        return False, {
            "waited_seconds": waited_seconds,
            "threshold_seconds": threshold,
            "in_progress_tools": poll_in_progress_tools(poll),
            "reason": "no_allowlisted_tool",
        }
    return True, {
        "waited_seconds": waited_seconds,
        "threshold_seconds": threshold,
        "matched_tools": matched,
        "allowlist": sorted(allowlist),
    }


def classify_active_poll_observation(
    poll: dict[str, Any],
    waited_seconds: int,
    *,
    delivery_mode: str = "",
    waiting_ack_after_seconds: int = 60,
    continuation_after_seconds: int = 0,
) -> dict[str, Any]:
    """Summarize a non-terminal active-turn poll without changing recovery policy."""
    status = str((poll or {}).get("status") or "").strip()
    status_lower = status.lower()
    has_text = bool(str((poll or {}).get("newText") or "").strip())
    result_complete = bool((poll or {}).get("result_complete"))
    ack_seen = poll_has_mobile_ack(poll)
    tools = poll_in_progress_tools(poll)
    terminal_without_text = bool((poll or {}).get("terminal_without_text"))
    retryable_failure = bool((poll or {}).get("retryable_tool_failure")) or poll_has_mcp_transport_closed(poll)
    owned_boundary_empty = bool((poll or {}).get("owned_result_boundary_complete_but_text_empty")) or (
        result_complete and not has_text
    )
    if owned_boundary_empty:
        stage = "owned_result_boundary_complete_but_text_empty"
    elif result_complete:
        stage = "completed_result_available"
    elif terminal_without_text:
        stage = "terminal_without_text"
    elif retryable_failure:
        stage = "retryable_failure_observed"
    elif has_text:
        stage = "intermediate_text_observed"
    elif tools:
        stage = "tool_in_progress"
    elif ack_seen and status_lower in {"inprogress", "running", "processing"}:
        if delivery_mode == "codex-app-server" and continuation_after_seconds and waited_seconds >= continuation_after_seconds:
            stage = "inprogress_no_output_continuation_window"
        elif waited_seconds >= waiting_ack_after_seconds:
            stage = "inprogress_no_output_after_wait_ack"
        else:
            stage = "inprogress_no_output_initial"
    elif status_lower in {"inprogress", "running", "processing"}:
        stage = "generation_active_without_ack"
    elif status_lower:
        stage = "nonterminal_status_observed"
    else:
        stage = "poll_status_unknown"
    return {
        "stage": stage,
        "status": status,
        "waited_seconds": waited_seconds,
        "delivery_mode": delivery_mode,
        "ack_seen": ack_seen,
        "has_text": has_text,
        "result_complete": result_complete,
        "owned_result_boundary_complete_but_text_empty": owned_boundary_empty,
        "terminal_without_text": terminal_without_text,
        "retryable_failure": retryable_failure,
        "in_progress_tool_count": len(tools),
        "in_progress_tools": tools[:5],
        "waiting_ack_after_seconds": waiting_ack_after_seconds,
        "repair_continuation_after_seconds": continuation_after_seconds,
    }


def record_active_poll_observation(
    queue: MobileQueue,
    task_id: str,
    poll: dict[str, Any],
    waited_seconds: int,
    *,
    delivery_mode: str = "",
    waiting_ack_after_seconds: int = 60,
    continuation_after_seconds: int = 0,
) -> dict[str, Any]:
    observation = classify_active_poll_observation(
        poll,
        waited_seconds,
        delivery_mode=delivery_mode,
        waiting_ack_after_seconds=waiting_ack_after_seconds,
        continuation_after_seconds=continuation_after_seconds,
    )
    signature = "|".join(
        [
            str(observation.get("stage") or ""),
            str(observation.get("status") or ""),
            str(observation.get("in_progress_tool_count") or 0),
            str(waited_seconds // 60),
        ]
    )
    if not task_event_recent(queue, task_id, "active_poll_observation", 60):
        queue.add_event(
            "local",
            "active_poll_observation",
            {
                **observation,
                "signature": signature,
                "policy": "diagnostic-only progress observation; does not change dispatch, repair, or retry decisions",
            },
            task_id,
        )
    return observation


def poll_protocol_violation_reason(
    poll: dict[str, Any],
    expected_task_ids: list[str] | None = None,
    expected_result_codes: dict[str, str] | None = None,
) -> str:
    """Classify a finished mobile-boundary turn that did not return owned text."""
    if not isinstance(poll, dict):
        return ""
    if poll_has_mcp_transport_closed(poll) or bool(poll.get("retryable_tool_failure")):
        return ""
    if bool(poll.get("result_complete")):
        return ""
    if str(poll.get("newText") or "").strip():
        return ""
    status = str(poll.get("status") or "").strip().lower()
    terminal = bool(poll.get("terminal_without_text")) or status in {
        "completed",
        "complete",
        "done",
        "stopped",
        "interrupted",
        "failed",
        "cancelled",
        "canceled",
        "notfound",
    }
    if not terminal:
        return ""
    expected_ids = [str(item) for item in (expected_task_ids or []) if str(item)]
    expected_codes = {
        str(key): str(value)
        for key, value in (expected_result_codes or {}).items()
        if str(key) and str(value)
    }
    ownership = poll.get("ownership")
    ownership_protocol = ""
    ownership_required = False
    ownership_result_complete = False
    if isinstance(ownership, dict):
        ownership_protocol = str(ownership.get("protocol") or "")
        ownership_required = bool(ownership.get("required")) or bool(ownership.get("expected_task_ids"))
        ownership_result_complete = bool(ownership.get("result_complete"))
    boundary_protocol = (
        str(poll.get("protocol") or "") == "mobile_result_boundary_v2"
        or ownership_protocol == "mobile_result_boundary_v2"
    )
    expects_owned_result = bool(expected_ids or expected_codes or ownership_required or boundary_protocol)
    if not expects_owned_result or ownership_result_complete:
        return ""
    return "protocol_violation_no_owned_result" if boundary_protocol else "terminal_without_owned_result"


def ensure_config_thread_item(
    config_path: Path,
    config: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    persisted = load_config(config_path)
    persisted.pop("_config_path", None)
    threads = persisted.setdefault("threads", {})
    items = threads.setdefault("items", [])
    if not isinstance(items, list):
        items = []
        threads["items"] = items
    for index, existing in enumerate(items):
        if not isinstance(existing, dict):
            continue
        if str(existing.get("id") or "") == str(item.get("id") or ""):
            merged = dict(existing)
            merged.update(item)
            items[index] = merged
            save_config(config_path, persisted)
            config.clear()
            config.update(load_config(config_path))
            config["_config_path"] = str(config_path)
            return merged
    items.append(item)
    save_config(config_path, persisted)
    config.clear()
    config.update(load_config(config_path))
    config["_config_path"] = str(config_path)
    return item


def auto_create_thread_route_for_user(
    queue: MobileQueue,
    config: dict[str, Any],
    external_user: str,
) -> dict[str, Any]:
    external_user = str(external_user or "").strip()
    if not is_openclaw_bound_user(config, external_user):
        return {"ok": False, "reason": "external_user is not a bound OpenClaw Weixin user"}
    existing = get_active_thread(queue, config, external_user, use_default=False)
    if existing:
        return {"ok": True, "created": False, "thread": existing}
    config_path = Path(config.get("_config_path") or DEFAULT_CONFIG)
    existing_user_item = find_thread_for_external_user(config, external_user)
    if existing_user_item:
        set_active_thread(queue, external_user, existing_user_item["id"])
        queue.add_event(
            "local",
            "thread_route_reused_existing_config_item",
            {
                "external_user": external_user,
                "thread_id": existing_user_item["id"],
                "codex_thread_id": existing_user_item["thread_id"],
            },
        )
        return {"ok": True, "created": False, "reused": True, "thread": existing_user_item}
    stable_id_value = onboarding_thread_placeholder_id(external_user)
    existing_item = find_thread(config, stable_id_value)
    if existing_item:
        set_active_thread(queue, external_user, existing_item["id"])
        return {"ok": True, "created": False, "thread": existing_item}
    thread_name = onboarding_thread_name(external_user)
    created = create_codex_thread_app_server(config, thread_name)
    if not created.get("ok"):
        return {"ok": False, "reason": "codex thread creation failed", "create_result": created}
    codex_thread_id = str(created.get("thread_id") or created.get("thread", {}).get("id") or "").strip()
    if not codex_thread_id:
        return {"ok": False, "reason": "codex thread creation returned no thread_id", "create_result": created}
    visibility = inspect_codex_thread_app_server(config, codex_thread_id, thread_name, stabilize_name=True)
    if not visibility.get("ok"):
        queue.add_event(
            "local",
            "thread_route_visibility_failed",
            {
                "external_user": external_user,
                "codex_thread_id": codex_thread_id,
                "thread_name": thread_name,
                "visibility": visibility,
            },
        )
        return {
            "ok": False,
            "reason": "codex thread was created but failed visibility check",
            "create_result": created,
            "visibility": visibility,
        }
    alias = str(external_user).split("@", 1)[0][:16]
    item = {
        "id": stable_id_value,
        "name": thread_name,
        "description": f"微信用户 {external_user} 的独立对话线程",
        "aliases": [alias, stable_id_value],
        "thread_id": codex_thread_id,
    }
    saved = ensure_config_thread_item(config_path, config, item)
    set_active_thread(queue, external_user, str(saved["id"]))
    queue.add_event(
        "local",
        "thread_route_auto_created",
        {
            "external_user": external_user,
            "thread_id": saved["id"],
            "codex_thread_id": codex_thread_id,
            "thread_name": thread_name,
            "visibility": visibility,
        },
    )
    return {
        "ok": True,
        "created": True,
        "thread": find_thread(config, str(saved["id"])) or saved,
        "create_result": created,
        "visibility": visibility,
    }


def openclaw_account_thread_drift(config: dict[str, Any], queue: MobileQueue | None = None) -> dict[str, Any]:
    """Read-only account-to-thread drift detector for QR-login onboarding."""
    accounts: list[dict[str, Any]] = []
    missing_routes: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for account_id in configured_openclaw_account_ids(config):
        account = read_openclaw_account(config, account_id)
        external_user = str(account.get("userId") or "").strip()
        token = str(account.get("token") or "").strip()
        if not account_id:
            continue
        if not external_user or not token:
            skipped.append(
                {
                    "account_id": account_id,
                    "reason": "missing userId or token",
                    "has_user_id": str(bool(external_user)).lower(),
                    "has_token": str(bool(token)).lower(),
                }
            )
            continue
        config_route = find_thread_for_external_user(config, external_user) or find_thread(
            config,
            onboarding_thread_placeholder_id(external_user),
        )
        runtime_value = ""
        runtime_route = None
        if queue is not None:
            runtime_value = queue.runtime_get(active_thread_key(external_user))
            runtime_route = find_thread(config, runtime_value) if runtime_value else None
        route = runtime_route or config_route
        item = {
            "account_id": account_id,
            "external_user": external_user,
            "thread_id": str((route or {}).get("thread_id") or ""),
            "thread_key": str((route or {}).get("id") or runtime_value or ""),
            "route_configured": bool(route),
            "runtime_thread_key": runtime_value,
            "expected_thread_key": onboarding_thread_placeholder_id(external_user),
        }
        accounts.append(item)
        if not route:
            missing_routes.append(item)
    return {
        "ok": not missing_routes,
        "accounts": accounts,
        "missing_count": len(missing_routes),
        "missing_routes": missing_routes,
        "skipped": skipped,
    }


def account_onboarding_sync(queue: MobileQueue, config: dict[str, Any], apply: bool = False) -> dict[str, Any]:
    """Synchronize persisted OpenClaw accounts into bridge users and thread routes.

    Dry-run is read-only. Apply may create Codex threads and append thread items
    for accounts that are already durably logged in.
    """
    before = openclaw_account_thread_drift(config, queue)
    actions: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    config_backup = ""
    if apply and int(before.get("missing_count") or 0) > 0:
        config_backup = backup_file_for_action(
            Path(config.get("_config_path") or DEFAULT_CONFIG),
            "account-onboarding-sync",
        )
    synced_users = {
        "ok": True,
        "dry_run": not apply,
        "action": "sync_openclaw_accounts_to_bridge_users",
        "candidate_count": len(before.get("accounts") or []),
    }
    if apply:
        missing_users = {str(item.get("external_user") or "") for item in before.get("missing_routes") or []}
        synced: list[dict[str, str]] = []
        for item in before.get("accounts") or []:
            external_user = str(item.get("external_user") or "")
            if not external_user or external_user in missing_users:
                continue
            queue.ensure_user("openclaw-weixin", external_user, allow_trigger=True)
            synced.append({"account_id": str(item.get("account_id") or ""), "external_user": external_user})
        synced_users = {
            "ok": True,
            "action": "sync_openclaw_accounts_to_bridge_users",
            "synced_count": len(synced),
            "synced": synced,
        }
    for item in before.get("missing_routes") or []:
        external_user = str(item.get("external_user") or "")
        account_id = str(item.get("account_id") or "")
        if not apply:
            actions.append(
                {
                    "account_id": account_id,
                    "external_user": external_user,
                    "action": "would_create_thread_route",
                    "thread_key": onboarding_thread_placeholder_id(external_user),
                    "thread_name": onboarding_thread_name(external_user),
                }
            )
            continue
        result = auto_create_thread_route_for_user(queue, config, external_user)
        if result.get("ok"):
            queue.ensure_user("openclaw-weixin", external_user, allow_trigger=True)
        actions.append(
            {
                "account_id": account_id,
                "external_user": external_user,
                "action": "create_thread_route",
                "result": result,
            }
        )
        if not result.get("ok"):
            failed.append({"account_id": account_id, "external_user": external_user, "result": result})
    after = openclaw_account_thread_drift(config, queue) if apply else before
    if apply:
        queue.add_event(
            "local",
            "openclaw_account_onboarding_sync",
            {
                "missing_before": int(before.get("missing_count") or 0),
                "missing_after": int(after.get("missing_count") or 0),
                "action_count": len(actions),
                "failed_count": len(failed),
            },
        )
    return {
        "ok": not failed and (not apply or int(after.get("missing_count") or 0) == 0),
        "applied": bool(apply),
        "before": before,
        "after": after,
        "config_backup": config_backup,
        "user_sync": synced_users,
        "actions": actions,
        "failed": failed,
    }


def account_onboarding_drift_signature(drift: dict[str, Any]) -> str:
    missing = [
        {
            "account_id": str(item.get("account_id") or ""),
            "external_user": str(item.get("external_user") or ""),
            "expected_thread_key": str(item.get("expected_thread_key") or ""),
        }
        for item in drift.get("missing_routes") or []
        if isinstance(item, dict)
    ]
    return hashlib.sha256(json.dumps(missing, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def account_onboarding_worker_sync_cooldown_seconds(config: dict[str, Any]) -> int:
    try:
        value = int(config.get("openclaw", {}).get("account_onboarding_worker_sync_cooldown_seconds") or 300)
    except Exception:
        value = 300
    return max(60, min(value, 3600))


def maybe_sync_openclaw_account_onboarding(queue: MobileQueue, config: dict[str, Any]) -> dict[str, Any]:
    """Worker-side invariant: logged-in OpenClaw accounts should have Codex routes.

    The normal login server still performs immediate onboarding. This bounded
    worker fallback only repairs durable drift, keeps the existing idempotent
    route checks, and never sends Weixin replies.
    """
    if config.get("openclaw", {}).get("account_onboarding_worker_sync_enabled") is False:
        return {"ok": True, "action": "disabled_by_config"}
    drift = openclaw_account_thread_drift(config, queue)
    missing_count = int(drift.get("missing_count") or 0)
    if missing_count <= 0:
        queue.runtime_delete("openclaw_account_onboarding_worker_sync:last")
        return {"ok": True, "action": "no_drift", "drift": drift}

    signature = account_onboarding_drift_signature(drift)
    cooldown_seconds = account_onboarding_worker_sync_cooldown_seconds(config)
    now = datetime.now(timezone.utc)
    blocked: list[dict[str, Any]] = []
    for item in drift.get("missing_routes") or []:
        if not isinstance(item, dict):
            continue
        account_id = str(item.get("account_id") or "")
        external_user = str(item.get("external_user") or "")
        item_signature = hashlib.sha256(
            json.dumps(
                {
                    "account_id": account_id,
                    "external_user": external_user,
                    "expected_thread_key": str(item.get("expected_thread_key") or ""),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        raw_item = str(queue.runtime_get(f"openclaw_account_onboarding_worker_sync:failed:{item_signature}") or "")
        if not raw_item:
            continue
        try:
            parsed_item = json.loads(raw_item)
            previous_item = parsed_item if isinstance(parsed_item, dict) else {}
        except json.JSONDecodeError:
            previous_item = {}
        attempted_at = parse_iso_datetime(str(previous_item.get("attempted_at") or ""))
        if attempted_at and attempted_at + timedelta(seconds=cooldown_seconds) > now:
            blocked.append(
                {
                    "account_id": account_id,
                    "external_user": external_user,
                    "signature": item_signature,
                    "next_retry_at": (attempted_at + timedelta(seconds=cooldown_seconds)).isoformat(),
                }
            )
    if blocked and len(blocked) >= missing_count:
        result = {
            "ok": True,
            "action": "cooldown",
            "missing_count": missing_count,
            "signature": signature,
            "cooldown_seconds": cooldown_seconds,
            "blocked": blocked,
        }
        queue.add_event("local", "openclaw_account_onboarding_worker_sync_cooldown", result)
        return result

    key = "openclaw_account_onboarding_worker_sync:last"
    previous: dict[str, Any] = {}
    raw = str(queue.runtime_get(key) or "")
    if raw:
        try:
            parsed = json.loads(raw)
            previous = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            previous = {}
    previous_signature = str(previous.get("signature") or "")
    previous_attempted_at = parse_iso_datetime(str(previous.get("attempted_at") or ""))
    if (
        previous_signature == signature
        and previous_attempted_at
        and previous_attempted_at + timedelta(seconds=cooldown_seconds) > now
    ):
        result = {
            "ok": True,
            "action": "cooldown",
            "missing_count": missing_count,
            "signature": signature,
            "cooldown_seconds": cooldown_seconds,
            "next_retry_at": (previous_attempted_at + timedelta(seconds=cooldown_seconds)).isoformat(),
        }
        queue.add_event("local", "openclaw_account_onboarding_worker_sync_cooldown", result)
        return result

    result = account_onboarding_sync(queue, config, apply=True)
    for failure in result.get("failed") or []:
        if not isinstance(failure, dict):
            continue
        failure_signature = hashlib.sha256(
            json.dumps(
                {
                    "account_id": str(failure.get("account_id") or ""),
                    "external_user": str(failure.get("external_user") or ""),
                    "expected_thread_key": onboarding_thread_placeholder_id(str(failure.get("external_user") or "")),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        queue.runtime_set(
            f"openclaw_account_onboarding_worker_sync:failed:{failure_signature}",
            json.dumps(
                {
                    "attempted_at": now.isoformat(),
                    "account_id": str(failure.get("account_id") or ""),
                    "external_user": str(failure.get("external_user") or ""),
                    "reason": str((failure.get("result") or {}).get("reason") or ""),
                },
                ensure_ascii=False,
            ),
        )
    payload = {
        "signature": signature,
        "attempted_at": now.isoformat(),
        "ok": bool(result.get("ok")),
        "missing_before": missing_count,
        "missing_after": int((result.get("after") or {}).get("missing_count") or 0),
        "failed_count": len(result.get("failed") or []),
    }
    queue.runtime_set(key, json.dumps(payload, ensure_ascii=False))
    queue.add_event("local", "openclaw_account_onboarding_worker_sync", payload)
    return {"ok": bool(result.get("ok")), "action": "applied", "sync": result, **payload}


def auto_onboarding_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_control_contract_regression_check("auto_onboarding_check", globals(), *args, **kwargs)


def account_onboarding_sync_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_control_contract_regression_check("account_onboarding_sync_check", globals(), *args, **kwargs)


def account_onboarding_worker_lifecycle_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_control_contract_regression_check("account_onboarding_worker_lifecycle_check", globals(), *args, **kwargs)


def mobile_repair_command_entry_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_control_contract_regression_check("mobile_repair_command_entry_check", globals(), *args, **kwargs)


def control_receipt_contract_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_control_contract_regression_check("control_receipt_contract_check", globals(), *args, **kwargs)


def mobile_repair_specialized_modes_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_control_contract_regression_check("mobile_repair_specialized_modes_check", globals(), *args, **kwargs)


def mobile_execution_contract_prompt_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_control_contract_regression_check("mobile_execution_contract_prompt_check", globals(), *args, **kwargs)


def mobile_permission_prompt_compact_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_control_contract_regression_check("mobile_permission_prompt_compact_check", globals(), *args, **kwargs)


def result_ownership_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_control_contract_regression_check("result_ownership_check", globals(), *args, **kwargs)


def fair_scheduling_check() -> dict[str, Any]:
    """Facade for moved scheduling regression check."""
    return run_scheduling_regression_check("fair_scheduling_check", globals())


def waiting_redelivery_gate_route_fairness_check() -> dict[str, Any]:
    """Facade for moved scheduling regression check."""
    return run_scheduling_regression_check("waiting_redelivery_gate_route_fairness_check", globals())


def route_fallback_dispatch_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("route_fallback_dispatch_check", globals(), *args, **kwargs)


def route_rotation_fairness_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("route_rotation_fairness_check", globals(), *args, **kwargs)


def cdp_live_listener_probe_unstable_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("cdp_live_listener_probe_unstable_check", globals(), *args, **kwargs)


def cdp_localhost_host_preserved_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("cdp_localhost_host_preserved_check", globals(), *args, **kwargs)


def active_observation_diagnosis_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("active_observation_diagnosis_check", globals(), *args, **kwargs)


def primary_visible_cdp_probe_failure_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("primary_visible_cdp_probe_failure_check", globals(), *args, **kwargs)


def transient_health_recovery_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("transient_health_recovery_check", globals(), *args, **kwargs)


def global_transient_health_scope_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("global_transient_health_scope_check", globals(), *args, **kwargs)


def thread_busy_status_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("thread_busy_status_check", globals(), *args, **kwargs)


def thread_prewarm_budget_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("thread_prewarm_budget_check", globals(), *args, **kwargs)


def thread_unlisted_recoverable_dispatch_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("thread_unlisted_recoverable_dispatch_check", globals(), *args, **kwargs)


def thread_dispatch_probe_fallback_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("thread_dispatch_probe_fallback_check", globals(), *args, **kwargs)


def thread_prewarm_execution_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("thread_prewarm_execution_check", globals(), *args, **kwargs)


def thread_prewarm_probe_failed_no_prewarm_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("thread_prewarm_probe_failed_no_prewarm_check", globals(), *args, **kwargs)


def thread_probe_failed_worker_retreat_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_route_thread_regression_check("thread_probe_failed_worker_retreat_check", globals(), *args, **kwargs)


def cdp_visible_delivery_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("cdp_visible_delivery_check", globals(), *args, **kwargs)


def visible_cdp_unconfirmed_observation_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("visible_cdp_unconfirmed_observation_check", globals(), *args, **kwargs)


def pending_visible_cdp_result_recovery_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("pending_visible_cdp_result_recovery_check", globals(), *args, **kwargs)


def cdp_route_doctor_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("cdp_route_doctor_check", globals(), *args, **kwargs)


def final_reply_visibility_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("final_reply_visibility_check", globals(), *args, **kwargs)


def visible_cdp_unconfirmed_multi_supplement_followup_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("visible_cdp_unconfirmed_multi_supplement_followup_check", globals(), *args, **kwargs)


def pending_visible_cdp_multi_supplement_consumption_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("pending_visible_cdp_multi_supplement_consumption_check", globals(), *args, **kwargs)


def visible_cdp_repeated_unconfirmed_attention_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("visible_cdp_repeated_unconfirmed_attention_check", globals(), *args, **kwargs)


def final_reply_visibility_unconfirmed_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("final_reply_visibility_unconfirmed_check", globals(), *args, **kwargs)


def reply_send_idempotency_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("reply_send_idempotency_check", globals(), *args, **kwargs)


def final_reply_media_text_split_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("final_reply_media_text_split_check", globals(), *args, **kwargs)


def final_reply_media_ret2_governance_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("final_reply_media_ret2_governance_check", globals(), *args, **kwargs)


def final_reply_active_owner_guard_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("final_reply_active_owner_guard_check", globals(), *args, **kwargs)


def failed_result_visibility_unconfirmed_recovery_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("failed_result_visibility_unconfirmed_recovery_check", globals(), *args, **kwargs)


def push_failed_ret2_fresh_context_recovery_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("push_failed_ret2_fresh_context_recovery_check", globals(), *args, **kwargs)


def weixin_errcode_session_timeout_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_cdp_delivery_regression_check("weixin_errcode_session_timeout_check", globals(), *args, **kwargs)


def reply_dedupe_policy_check() -> dict[str, Any]:
    """Temp-only check that uncertain final replies and status acks do not duplicate sends."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-reply-dedupe-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "openclaw": {
                "account_id": "backup1",
                "reply_pending_context_retry_limit_per_cycle": 5,
                "phone_status_ack_events": ["status_ack_dispatching"],
            },
            "queue": {"db_path": str(temp / "queue.db")},
        }
        task_id = "visibleunknown1"
        now = datetime.now(timezone.utc).isoformat()
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    risk_level, status, result, push_status, receiver_account_id,
                    metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "probe",
                    "L1",
                    "done",
                    "same final reply",
                    "reply_pending",
                    "backup1",
                    "{}",
                    now,
                    now,
                ),
            )
        queue.add_event(
            "wecom",
            "final_reply_waiting_weixin_context",
            {"source_reason": "phone_visible_not_confirmed", "reason": "waiting_weixin_context"},
            task_id,
        )
        pending_result = process_pending_reply_context_retries(queue, config, limit=5)
        enqueue_retry_result = schedule_waiting_context_replies(
            queue,
            config,
            "user@im.wechat",
            "backup1",
            "fresh-context",
            "trigger1",
        )

        first = reserve_status_ack_send(queue, "ack1", "status_ack_dispatching", "正在投递到 Codex。")
        second = reserve_status_ack_send(queue, "ack1", "status_ack_dispatching", "正在投递到 Codex。")
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    message_fingerprint, risk_level, status, receiver_account_id, metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "ack2",
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "status ack",
                    "ack2-fingerprint",
                    "L1",
                    "sent_to_codex",
                    "backup1",
                    "{}",
                    now,
                    now,
                ),
            )
        queue.add_event(
            "wecom",
            "status_ack_dispatching",
            {"ok": True, "reply": {"ok": True, "deliveryAccepted": True}},
            "ack2",
        )
        already_sent = send_status_ack(
            queue,
            {"id": "ack2", "external_user": "user@im.wechat", "receiver_account_id": "backup1"},
            "正在投递到 Codex。",
            config,
            "status_ack_dispatching",
        )

        ok = bool(
            pending_result.get("scheduled") == 0
            and pending_result.get("visibility_unknown") == 1
            and enqueue_retry_result.get("scheduled") == 0
            and enqueue_retry_result.get("visibility_unknown") == 1
            and first.get("reserved")
            and second.get("duplicate")
            and already_sent.get("suppressed")
            and already_sent.get("reason") == "status_ack_already_sent"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "pending_result": pending_result,
            "enqueue_retry_result": enqueue_retry_result,
            "first_ack": first,
            "second_ack": second,
            "already_sent_ack": already_sent,
            "assertion": "uncertain final replies are not auto-resent and duplicate async status ack spawns are suppressed",
        }


def event_noise_coalescing_check() -> dict[str, Any]:
    """Temp-only check that repetitive diagnostics are coalesced without dropping distinct events."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-event-noise-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "noise1"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    risk_level, status, receiver_account_id, metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "probe",
                    "L1",
                    "pending",
                    "primary",
                    "{}",
                    now,
                    now,
                ),
            )

        inserted_first = add_coalesced_event(
            queue,
            "local",
            "delivery_retry_scheduled",
            {"reason": "visible_cdp_probe_failed"},
            task_id,
            signature="visible_cdp_probe_failed",
        )
        inserted_second = add_coalesced_event(
            queue,
            "local",
            "delivery_retry_scheduled",
            {"reason": "visible_cdp_probe_failed"},
            task_id,
            signature="visible_cdp_probe_failed",
        )
        inserted_different = add_coalesced_event(
            queue,
            "local",
            "delivery_retry_scheduled",
            {"reason": "visible_cdp_busy"},
            task_id,
            signature="visible_cdp_busy",
        )
        queue.add_event("local", "sent_to_codex", {"fixture": True}, task_id)
        queue.add_event("local", "sent_to_codex", {"fixture": True}, task_id)
        queue.add_event(
            "local",
            "status_ack_visible_cdp_probe_failed_suppressed",
            {"reason": "visible_cdp_probe_failed", "thread_id": "thread-a"},
            task_id,
        )
        queue.add_event(
            "local",
            "status_ack_visible_cdp_probe_failed_suppressed",
            {"reason": "visible_cdp_probe_failed", "thread_id": "thread-a"},
            task_id,
        )
        queue.add_event(
            "local",
            "status_ack_visible_cdp_probe_failed_suppressed",
            {"reason": "visible_cdp_probe_failed", "thread_id": "thread-b"},
            task_id,
        )

        marker = json.loads(
            queue.runtime_get(event_coalesce_key(task_id, "delivery_retry_scheduled", "visible_cdp_probe_failed"))
            or "{}"
        )
        with queue.session() as db:
            events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (task_id,),
                ).fetchall()
            }
            db_guard_markers = [
                json.loads(str(row["value"] or "{}"))
                for row in db.execute(
                    """
                    SELECT value FROM mobile_runtime
                    WHERE key LIKE 'event_noise_guard:%'
                    ORDER BY updated_at DESC
                    """
                ).fetchall()
            ]
        suppressed_marker_count = sum(
            int(item.get("suppressed_count") or 0)
            for item in db_guard_markers
            if item.get("event_type") == "status_ack_visible_cdp_probe_failed_suppressed"
        )
        ok = bool(
            inserted_first
            and not inserted_second
            and inserted_different
            and events.get("delivery_retry_scheduled") == 2
            and events.get("sent_to_codex") == 2
            and events.get("status_ack_visible_cdp_probe_failed_suppressed") == 2
            and int(marker.get("suppressed_count") or 0) == 1
            and suppressed_marker_count == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "inserted": {
                "first_same_signature": inserted_first,
                "second_same_signature": inserted_second,
                "different_signature": inserted_different,
            },
            "events": events,
            "coalesce_marker": marker,
            "db_guard_suppressed_marker_count": suppressed_marker_count,
            "assertion": "only selected repetitive diagnostic events are coalesced; semantic lifecycle events still insert normally",
        }


def supplement_final_owner_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("supplement_final_owner_check", globals())



def delivery_group_owner_check() -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("delivery_group_owner_check", globals())


def pending_backlog_supplement_batch_check() -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("pending_backlog_supplement_batch_check", globals())


def active_visible_cdp_supplement_publish_check() -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("active_visible_cdp_supplement_publish_check", globals())


def active_ack_inprogress_observation_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("active_ack_inprogress_observation_check", globals())



def waiting_followup_owned_result_recovery_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("waiting_followup_owned_result_recovery_check", globals())



def waiting_followup_owned_result_redelivery_gate_check() -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("waiting_followup_owned_result_redelivery_gate_check", globals())


def base_ack_only_terminal_redelivery_check() -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("base_ack_only_terminal_redelivery_check", globals())


def waiting_completed_reply_evidence_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("waiting_completed_reply_evidence_check", globals())



def failure_close_owned_result_recovery_check() -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("failure_close_owned_result_recovery_check", globals())


def historical_failed_result_filter_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("historical_failed_result_filter_check", globals())



def failed_result_audit_recovery_consistency_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("failed_result_audit_recovery_consistency_check", globals())



def protocol_violation_no_owned_result_check() -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("protocol_violation_no_owned_result_check", globals())


def active_stalled_tool_recovery_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("active_stalled_tool_recovery_check", globals())



def app_server_repair_continuation_check() -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("app_server_repair_continuation_check", globals())


def active_progress_observability_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("active_progress_observability_check", globals())



def delivery_group_owner_event_fallback_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("delivery_group_owner_event_fallback_check", globals())



def delivery_group_stale_active_snapshot_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("delivery_group_stale_active_snapshot_check", globals())



def orphaned_supplement_promotion_with_push_evidence_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("orphaned_supplement_promotion_with_push_evidence_check", globals())



def failed_base_supplement_owner_promotion_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("failed_base_supplement_owner_promotion_check", globals())



def completed_owner_supplement_ack_window_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("completed_owner_supplement_ack_window_check", globals())



def app_server_result_poll_second_chance_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("app_server_result_poll_second_chance_check", globals())



def app_server_turn_materialization_window_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("app_server_turn_materialization_window_check", globals())



def historical_owned_result_fallback_check() -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("historical_owned_result_fallback_check", globals())


def thread_history_owned_result_fallback_check() -> dict[str, Any]:
    """Facade for moved owned-result regression check."""
    return run_owned_result_regression_check("thread_history_owned_result_fallback_check", globals())


def supplement_ack_gating_check() -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("supplement_ack_gating_check", globals())


def supplement_owner_promotion_check() -> dict[str, Any]:
    """Temp-only check that old supplement markers do not suppress a promoted owner."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-supp-owner-promotion-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "promoted-owner"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                    queued_for_codex_at, sent_to_codex_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "promoted owner",
                    hashlib.sha256(b"promoted owner").hexdigest(),
                    "promoted-owner",
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    "thread-1",
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
        queue.add_event(
            "local",
            "attachment_supplement_pending_published",
            {"active_task_id": "old-owner", "thread_id": "thread-1", "signature": "old-sig"},
            task_id,
        )
        supplement_before_owner = task_is_supplement_context(queue, task_id)
        queue.runtime_set(task_turn_key(task_id), "turn-promoted")
        queue.runtime_set(task_batch_key(task_id), "batch-promoted")
        queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
        supplement_after_owner = task_is_supplement_context(queue, task_id)
        waiting_suppressed = should_suppress_supplement_status_ack(queue, task_id, "status_ack_waiting")
        return {
            "ok": bool(supplement_before_owner and not supplement_after_owner and not waiting_suppressed),
            "temp_only": True,
            "supplement_before_owner": supplement_before_owner,
            "supplement_after_owner": supplement_after_owner,
            "waiting_suppressed": waiting_suppressed,
            "assertion": "old supplement marker events remain supplement context until a current final-reply owner takes over",
        }


def orphaned_supplement_promotion_check() -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("orphaned_supplement_promotion_check", globals())


def supplement_mcp_disconnect_no_primary_fallback_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("supplement_mcp_disconnect_no_primary_fallback_check", globals())



def supplement_cli_fallback_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("supplement_cli_fallback_check", globals())



def supplement_unacked_timeout_release_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("supplement_unacked_timeout_release_check", globals())



def supplement_release_no_republish_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("supplement_release_no_republish_check", globals())



def followup_redelivery_mcp_supplement_check() -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("followup_redelivery_mcp_supplement_check", globals())


def followup_redelivery_fifo_supplement_check() -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("followup_redelivery_fifo_supplement_check", globals())


def active_runtime_rehydrate_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("active_runtime_rehydrate_check", globals())



def queued_turn_rehydrate_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("queued_turn_rehydrate_check", globals())



def queued_turn_materialized_readback_rehydrate_check() -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("queued_turn_materialized_readback_rehydrate_check", globals())


def supplement_non_owner_host_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("supplement_non_owner_host_check", globals())



def queued_same_route_supplement_recovery_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("queued_same_route_supplement_recovery_check", globals())



def supplement_invalid_published_release_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("supplement_invalid_published_release_check", globals())



def mcp_ack_does_not_complete_owner_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("mcp_ack_does_not_complete_owner_check", globals())



def mcp_ack_missing_base_owner_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("mcp_ack_missing_base_owner_check", globals())



def invalid_mcp_ack_not_published_supplement_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("invalid_mcp_ack_not_published_supplement_check", globals())



def followup_redelivery_stale_pending_guard_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("followup_redelivery_stale_pending_guard_check", globals())



def iteration_closeout_display_check() -> dict[str, Any]:
    queue = MobileQueue(":memory:")
    report = iteration_gate_report(queue, {})
    closeout = report.get("closeout_display") if isinstance(report.get("closeout_display"), dict) else {}
    groups = closeout.get("proposal_groups") if isinstance(closeout.get("proposal_groups"), list) else []
    actions = closeout.get("recommended_next_actions") if isinstance(closeout.get("recommended_next_actions"), list) else []
    blocked = closeout.get("blocked_without_approval") if isinstance(closeout.get("blocked_without_approval"), list) else []
    checks = {
        "closeout_display_present": {
            "ok": bool(closeout),
            "schema": closeout.get("schema"),
        },
        "approval_requires_user_visible_items": {
            "ok": bool(
                closeout.get("must_display_to_user") is True
                and (groups or actions or blocked)
            ),
            "must_display_to_user": closeout.get("must_display_to_user"),
            "proposal_groups": len(groups),
            "recommended_next_actions": len(actions),
            "blocked_without_approval": len(blocked),
        },
        "proposal_only_boundary_preserved": {
            "ok": bool((report.get("approval_block") or {}).get("approved_by_default") is False),
            "approved_by_default": (report.get("approval_block") or {}).get("approved_by_default"),
        },
    }
    failed = {name: item for name, item in checks.items() if not item.get("ok")}
    return {
        "schema": "iteration-closeout-display-check/v1",
        "ok": not failed,
        "checks": checks,
        "failed": failed,
        "closeout_display": closeout,
        "read_only": True,
    }


def app_server_sync_after_dispatch_check() -> dict[str, Any]:
    """Temp-only check that app-server dispatch sync evidence is persisted."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-app-sync-") as temp_root:
        temp = Path(temp_root)
        script = temp / "fake_app_server_client.js"
        script.write_text(
            """
const args = process.argv.slice(2);
const has = (name) => args.includes(name);
if (has('--check-health')) {
  process.stdout.write(JSON.stringify({ ok: true, healthy: true, mode: 'codex-app-server' }));
} else if (has('--inspect-thread')) {
  process.stdout.write(JSON.stringify({
    ok: true,
    healthy: true,
    listed: true,
    listed_status: { type: 'idle' },
    mode: 'codex-app-server'
  }));
} else if (has('--dispatch')) {
  const threadId = args[args.indexOf('--thread-id') + 1] || 'thread-app-sync';
  process.stdout.write(JSON.stringify({
    ok: true,
    mode: 'codex-app-server',
    thread_id: threadId,
    turn_id: 'turn-app-sync',
    client_user_message_id: args[args.indexOf('--client-message-id') + 1] || '',
    status: 'running',
    sync_after_dispatch: {
      ok: true,
      turn_created: true,
      turn_readable: true,
      desktop_thread_hydrated: true,
      ui_visible: 'unknown',
      resume_ok: true,
      turn_found: true,
      turn_id: 'turn-app-sync',
      error: null
    }
  }));
} else {
  process.stdout.write(JSON.stringify({ ok: true, healthy: true }));
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        user = "app-sync@im.wechat"
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "node_path": "node",
                "codex_app_server_script": str(script),
                "codex_app_server_host": "127.0.0.1",
                "codex_app_server_port": 18791,
                "delivery_timeout_seconds": 5,
                "cooldown_seconds": 0,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "app-sync-route",
                        "name": "App Sync Route",
                        "description": "app server sync route",
                        "aliases": [],
                        "thread_id": "thread-app-sync",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "app-sync-route")
        enqueued = queue.enqueue(
            "你好",
            source="openclaw-weixin",
            external_user=user,
            metadata={"msg_id": "app-sync", "receiver_account_id": "backup1"},
        )
        task_id_value = str(enqueued["id"])
        original_start_server = globals()["ensure_codex_app_server"]
        original_status_ack = globals()["send_status_ack"]

        def fake_ensure_codex_app_server(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "started": False, "host": "127.0.0.1", "port": 18791}

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        try:
            globals()["ensure_codex_app_server"] = fake_ensure_codex_app_server
            globals()["send_status_ack"] = fake_send_status_ack
            result = worker_once(queue, config, limit=5)
        finally:
            globals()["ensure_codex_app_server"] = original_start_server
            globals()["send_status_ack"] = original_status_ack

        events = []
        with queue.session() as db:
            rows = db.execute(
                "SELECT event_type,payload_json FROM mobile_events WHERE task_id=? ORDER BY id",
                (task_id_value,),
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            events.append({"event_type": row["event_type"], "payload": payload})
        turn_event = next((event for event in events if event["event_type"] == "codex_turn_started"), None)
        sync = (turn_event or {}).get("payload", {}).get("sync_after_dispatch") or {}
        ok = bool(
            result.get("action") == "dispatched_waiting_result"
            and result.get("delivery", {}).get("sync_after_dispatch", {}).get("turn_found") is True
            and result.get("delivery", {}).get("sync_after_dispatch", {}).get("turn_readable") is True
            and result.get("delivery", {}).get("sync_after_dispatch", {}).get("desktop_thread_hydrated") is True
            and sync.get("turn_found") is True
            and sync.get("turn_readable") is True
            and sync.get("desktop_thread_hydrated") is True
            and sync.get("ui_visible") == "unknown"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result": result,
            "sync_after_dispatch_event": sync,
            "assertion": "app-server dispatch sync evidence distinguishes turn readability from unknown desktop UI visibility",
        }


def app_server_unreadable_dispatch_guard_check() -> dict[str, Any]:
    """Temp-only check that unreadable app-server dispatch keeps transport acceptance."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-app-unreadable-dispatch-") as temp_root:
        temp = Path(temp_root)
        script = temp / "fake_app_server_unreadable_client.js"
        script.write_text(
            """
const args = process.argv.slice(2);
const has = (name) => args.includes(name);
if (has('--check-health')) {
  process.stdout.write(JSON.stringify({ ok: true, healthy: true, mode: 'codex-app-server' }));
} else if (has('--inspect-thread')) {
  process.stdout.write(JSON.stringify({
    ok: true,
    healthy: true,
    listed: true,
    listed_status: { type: 'idle' },
    mode: 'codex-app-server'
  }));
} else if (has('--dispatch')) {
  process.stdout.write(JSON.stringify({
    ok: true,
    mode: 'codex-app-server',
    thread_id: 'thread-app-unreadable',
    turn_id: 'turn-app-unreadable',
    client_user_message_id: args[args.indexOf('--client-message-id') + 1] || '',
    status: 'running',
    sync_after_dispatch: {
      ok: false,
      turn_created: true,
      turn_readable: false,
      desktop_thread_hydrated: true,
      ui_visible: 'unknown',
      resume_ok: true,
      turn_found: false,
      turn_id: 'turn-app-unreadable',
      error: null
    }
  }));
} else {
  process.stdout.write(JSON.stringify({ ok: true, healthy: true }));
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        user = "app-unreadable@im.wechat"
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "node_path": "node",
                "codex_app_server_script": str(script),
                "codex_app_server_host": "127.0.0.1",
                "codex_app_server_port": 18791,
                "delivery_timeout_seconds": 5,
                "cooldown_seconds": 0,
                "delivery_retry_reason_seconds": {"dispatch_failed": 0},
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "app-unreadable-route",
                        "name": "App Unreadable Route",
                        "description": "app server unreadable route",
                        "aliases": [],
                        "thread_id": "thread-app-unreadable",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "app-unreadable-route")
        enqueued = queue.enqueue(
            "你好",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "app-unreadable-1", "receiver_account_id": "backup1"},
        )
        task_id = str(enqueued["id"])
        original_start_server = globals()["ensure_codex_app_server"]
        original_status_ack = globals()["send_status_ack"]

        def fake_ensure_codex_app_server(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "started": False, "host": "127.0.0.1", "port": 18791}

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        try:
            globals()["ensure_codex_app_server"] = fake_ensure_codex_app_server
            globals()["send_status_ack"] = fake_send_status_ack
            result = worker_once(queue, config, limit=5)
        finally:
            globals()["ensure_codex_app_server"] = original_start_server
            globals()["send_status_ack"] = original_status_ack
        task_after = queue.get_task(task_id) or {}
        recovery = get_thread_recovery(queue, task_id)
        retry = get_delivery_retry(queue, task_id)
        with queue.session() as db:
            events = {
                row["event_type"]: int(row["n"] or 0)
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (task_id,),
                )
            }
        ok = bool(
            result.get("action") == "dispatched_waiting_result"
            and task_after.get("status") == "sent_to_codex"
            and task_after.get("sent_to_codex_at")
            and events.get("codex_turn_started", 0) == 1
            and events.get("delivery_failed_reverted_to_pending", 0) == 0
            and recovery.get("active") is False
            and retry.get("active") is False
            and bool(result.get("delivery", {}).get("sync_after_dispatch", {}).get("diagnostic_only"))
            and result.get("delivery", {}).get("reason") == "app_server_turn_not_readable_after_dispatch"
            and result.get("delivery", {}).get("submission_unconfirmed") is True
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result": result,
            "task_status": task_after.get("status"),
            "sent_to_codex_at": task_after.get("sent_to_codex_at"),
            "thread_recovery": recovery,
            "delivery_retry": retry,
            "event_counts": events,
            "assertion": "app-server dispatch can keep transport acceptance while retaining unreadable-turn diagnostics without forcing a pending rollback",
        }


def app_server_unreadable_original_thread_repair_check() -> dict[str, Any]:
    """Temp-only check for bounded repair of repeated unreadable app-server turns."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-app-unreadable-repair-") as temp_root:
        temp = Path(temp_root)
        user = "app-unreadable-repair@im.wechat"
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "app_server_unreadable_repair_threshold": 2,
                "app_server_unreadable_repair_cooldown_seconds": 60,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "repair-route",
                        "name": "Repair Route",
                        "description": "app server unreadable repair route",
                        "aliases": [],
                        "thread_id": "thread-app-unreadable-repair",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "repair-route")
        enqueued = queue.enqueue(
            "你好",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "app-unreadable-repair-1", "receiver_account_id": "backup1"},
        )
        task_id = str(enqueued["id"])
        delivery = {
            "ok": False,
            "reason": "app_server_turn_not_readable_after_dispatch",
            "mode": "codex-app-server",
            "thread_id": "thread-app-unreadable-repair",
            "turn_id": "turn-unreadable-repair",
        }
        restarts: list[dict[str, Any]] = []
        original_restart = globals()["restart_codex_app_server_for_mcp"]

        def fake_restart(_config: dict[str, Any], reason: str = "") -> dict[str, Any]:
            result = {"ok": True, "reason": reason, "fake": True}
            restarts.append(result)
            return result

        try:
            globals()["restart_codex_app_server_for_mcp"] = fake_restart
            first_marker = mark_thread_recovery(
                queue,
                task_id,
                "app_server_turn_not_readable_after_dispatch",
                {"thread_id": "thread-app-unreadable-repair"},
            )
            first = maybe_repair_app_server_unreadable_thread(
                queue,
                config,
                task_id,
                "thread-app-unreadable-repair",
                first_marker,
                delivery,
            )
            second_marker = mark_thread_recovery(
                queue,
                task_id,
                "app_server_turn_not_readable_after_dispatch",
                {"thread_id": "thread-app-unreadable-repair"},
            )
            second = maybe_repair_app_server_unreadable_thread(
                queue,
                config,
                task_id,
                "thread-app-unreadable-repair",
                second_marker,
                delivery,
            )
            third_marker = mark_thread_recovery(
                queue,
                task_id,
                "app_server_turn_not_readable_after_dispatch",
                {"thread_id": "thread-app-unreadable-repair"},
            )
            third = maybe_repair_app_server_unreadable_thread(
                queue,
                config,
                task_id,
                "thread-app-unreadable-repair",
                third_marker,
                delivery,
            )
        finally:
            globals()["restart_codex_app_server_for_mcp"] = original_restart
        task_after = queue.get_task(task_id) or {}
        with queue.session() as db:
            events = {
                row["event_type"]: int(row["n"] or 0)
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (task_id,),
                )
            }
        ok = bool(
            first.get("action") == "below_threshold"
            and second.get("action") == "restart_attempted"
            and third.get("action") == "cooldown"
            and len(restarts) == 1
            and task_after.get("status") == "pending"
            and events.get("app_server_unreadable_repair_attempted") == 1
            and events.get("app_server_unreadable_repair_cooldown") == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "first": first,
            "second": second,
            "third": third,
            "restart_count": len(restarts),
            "task_status": task_after.get("status"),
            "event_counts": events,
            "assertion": "repeated unreadable turns repair the original app-server listener once, keep the original thread route, and then cool down",
        }


def dispatch_to_codex_app_server(
    tasks: list[dict[str, Any]],
    thread_id: str,
    config: dict[str, Any],
    continuation: dict[str, Any] | None = None,
    result_owner_task_ids: list[str] | None = None,
) -> dict[str, Any]:
    if result_owner_task_ids is None:
        result_owner_task_ids = list(config.get("_delivery_group_result_owner_task_ids") or [])
    mobile_batch_id = make_mobile_batch_id(tasks)
    all_task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
    owner_id_set = {str(item) for item in (result_owner_task_ids or []) if str(item)}
    expected_task_ids = [task_id for task_id in all_task_ids if task_id in owner_id_set] if owner_id_set else all_task_ids
    protocol_tasks = [task for task in tasks if str(task.get("id") or "") in set(expected_task_ids)]
    protocols = mobile_protocols(protocol_tasks, mobile_batch_id)
    prompt = task_prompt(
        tasks,
        continuation,
        mobile_batch_id=mobile_batch_id,
        bridge_thread_id=thread_id,
        result_owner_task_ids=expected_task_ids,
        config=config,
    )
    contract_gate = final_reply_prompt_contract_gate(prompt, expected_task_ids)
    if not contract_gate.get("ok"):
        return {
            "ok": False,
            "reason": contract_gate.get("reason") or "mobile_prompt_contract_invalid",
            "mode": "codex-app-server",
            "thread_id": thread_id,
            "client_user_message_id": mobile_batch_id,
            "expected_task_ids": expected_task_ids,
            "mobile_protocols": protocols,
            "prompt_contract": contract_gate,
            "prompt": prompt,
        }
    startup = ensure_codex_app_server(config)
    if not startup.get("ok"):
        result = dict(startup)
        result.update({"ok": False, "mode": "codex-app-server", "thread_id": thread_id, "prompt": prompt})
        return result
    args = [
        "--dispatch",
        "--thread-id",
        thread_id,
        "--cwd",
        str(PROJECT_ROOT),
        "--client-message-id",
        mobile_batch_id,
        "--expected-task-ids",
        ",".join(expected_task_ids),
        "--materialization-wait-ms",
        str(app_server_turn_materialization_grace_seconds(config) * 1000),
    ]
    result_codes = mobile_result_codes_arg(protocols)
    if result_codes:
        args.extend(["--expected-result-codes", result_codes])
    ack_codes = mobile_ack_codes_arg(protocols)
    if ack_codes:
        args.extend(["--expected-ack-codes", ack_codes])
    parsed = run_codex_app_server_client(config, args, prompt=prompt, timeout_extra_seconds=5)
    parsed.setdefault("mode", "codex-app-server")
    parsed.setdefault("thread_id", thread_id)
    parsed.setdefault("client_user_message_id", mobile_batch_id)
    parsed["expected_task_ids"] = expected_task_ids
    parsed["mobile_protocols"] = protocols
    parsed["prompt_contract"] = contract_gate
    parsed["prompt"] = prompt
    parsed["startup"] = startup
    parsed["sync_after_dispatch"] = parsed.get("sync_after_dispatch") or {}
    sync_after_dispatch = parsed["sync_after_dispatch"] if isinstance(parsed["sync_after_dispatch"], dict) else {}
    if parsed.get("ok") and sync_after_dispatch and not bool(sync_after_dispatch.get("turn_readable")):
        parsed["sync_after_dispatch"]["turn_readable"] = False
        parsed["sync_after_dispatch"]["diagnostic_only"] = True
        parsed["submission_unconfirmed"] = True
        parsed.setdefault("reason", "app_server_turn_not_readable_after_dispatch")
    return parsed


def dispatch_to_codex(
    tasks: list[dict[str, Any]],
    thread_id: str,
    config: dict[str, Any],
    continuation: dict[str, Any] | None = None,
    result_owner_task_ids: list[str] | None = None,
) -> dict[str, Any]:
    if result_owner_task_ids is None:
        result_owner_task_ids = list(config.get("_delivery_group_result_owner_task_ids") or [])
    mode = str(config.get("trigger", {}).get("delivery_mode") or "stub").lower()
    if mode == "codex-app-server":
        return dispatch_to_codex_app_server(tasks, thread_id, config, continuation, result_owner_task_ids)
    if mode == "codex-cdp":
        return dispatch_to_codex_cdp(tasks, thread_id, config, continuation, result_owner_task_ids)
    return dispatch_to_codex_stub(tasks, thread_id, continuation, result_owner_task_ids)



def gui_automation_health_check(config: dict[str, Any], prewarm: bool = False, ocr_probe: bool = False) -> dict[str, Any]:
    """Read-only GUI automation runtime, OCR, and fallback health check."""
    mcp_config = codex_mcp_config_health(config)
    gui = gui_automation_health(config, mcp_config)
    command = str(gui.get("command") or "")
    script = str((gui.get("script") or {}).get("path") or PROJECT_ROOT / "_bridge" / "gui_automation_mcp.py")
    self_check: dict[str, Any] = {"ok": False, "skipped": True, "reason": "runtime_not_ready"}
    if gui.get("ok") and command and Path(command).exists() and Path(script).exists():
        try:
            proc = subprocess.run(
                [command, "--self-check"],
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                timeout=30,
            )
            try:
                parsed = json.loads(proc.stdout or "{}")
            except Exception:
                parsed = {"ok": False, "stdout_tail": (proc.stdout or "")[-500:]}
            self_check = parsed if isinstance(parsed, dict) else {"ok": False, "raw": parsed}
            self_check["returncode"] = proc.returncode
            if proc.stderr and not self_check.get("ok"):
                self_check["stderr_tail"] = proc.stderr[-800:]
        except Exception as exc:
            self_check = {"ok": False, "error": str(exc), "exception_type": type(exc).__name__}

    ocr = gui.get("ocr") if isinstance(gui.get("ocr"), dict) else {}
    prewarm_result: dict[str, Any] = {"ok": bool(ocr.get("ready")), "skipped": not prewarm, "source": "status"}
    if prewarm:
        ocr_python = str(ocr.get("python") or "")
        ocr_runner = str(ocr.get("runner") or PROJECT_ROOT / "_bridge" / "gui_ocr_paddle_runner.py")
        if ocr_python and Path(ocr_python).exists() and Path(ocr_runner).exists():
            try:
                proc = subprocess.run(
                    [ocr_python, ocr_runner, "--status"],
                    cwd=str(PROJECT_ROOT),
                    text=True,
                    capture_output=True,
                    timeout=60,
                )
                try:
                    parsed = json.loads((proc.stdout or "").strip().splitlines()[0])
                except Exception:
                    parsed = {"ready": False, "stdout_tail": (proc.stdout or "")[-500:]}
                prewarm_result = {
                    "ok": bool(isinstance(parsed, dict) and parsed.get("ready")),
                    "returncode": proc.returncode,
                    "status": parsed,
                }
                if proc.stderr and not prewarm_result.get("ok"):
                    prewarm_result["stderr_tail"] = proc.stderr[-800:]
            except Exception as exc:
                prewarm_result = {"ok": False, "error": str(exc), "exception_type": type(exc).__name__}
        else:
            prewarm_result = {"ok": False, "reason": "ocr_python_or_runner_missing"}

    probe_result: dict[str, Any] = {"ok": bool(ocr.get("ready")), "skipped": not ocr_probe, "source": "status"}
    if ocr_probe:
        probe_result = gui_ocr_gpu_probe(config)

    requested_device = str(ocr.get("requested_device") or "").lower()
    configured_for_gpu = requested_device.startswith("gpu")
    configured_new_process_ok = bool(ocr.get("ready")) and (not configured_for_gpu or bool(ocr.get("compiled_cuda")))
    live_mcp_reload = {
        "configured_for_gpu": configured_for_gpu,
        "configured_new_process_ok": configured_new_process_ok,
        "restart_required_for_live_mcp": False,
        "reason": "",
    }
    if configured_for_gpu and configured_new_process_ok:
        live_mcp_reload.update(
            {
                "restart_required_for_live_mcp": True,
                "reason": "Codex Desktop may keep an already-started gui-automation MCP process with the previous OCR env until restart.",
            }
        )

    fallback = {
        "available": True,
        "preferred_when": [
            "gui-automation MCP runtime is unhealthy",
            "OCR is not ready but Computer Use can inspect the target window",
            "the task needs broad app/window control rather than exact UIA selectors",
        ],
        "boundary": "fallback is advisory only; this command does not automate windows or send input",
    }
    return {
        "ok": bool(gui.get("ok")) and bool(self_check.get("ok")) and bool(prewarm_result.get("ok")) and bool(probe_result.get("ok")),
        "read_only": True,
        "gui_automation": gui,
        "mcp_config": {
            "ok": mcp_config.get("ok"),
            "missing": [item.get("name") for item in (mcp_config.get("missing") or [])],
            "drifted": [item.get("name") for item in (mcp_config.get("drifted") or [])],
            "repairable_missing": [item.get("name") for item in (mcp_config.get("repairable_missing") or [])],
            "repairable_drifted": [item.get("name") for item in (mcp_config.get("repairable_drifted") or [])],
        },
        "self_check": self_check,
        "ocr_prewarm": prewarm_result,
        "ocr_probe": probe_result,
        "live_mcp_reload": live_mcp_reload,
        "fallback": fallback,
    }


def gui_ocr_gpu_probe(config: dict[str, Any]) -> dict[str, Any]:
    """Read-only probe for the isolated OCR GPU candidate environment."""
    mcp_config = codex_mcp_config_health(config)
    gui = gui_automation_health(config, mcp_config)
    ocr = gui.get("ocr") if isinstance(gui.get("ocr"), dict) else {}
    gpu_python = PROJECT_ROOT / "_bridge" / "venvs" / "ocr-gpu-py312" / "Scripts" / "python.exe"
    cpu_python = Path(str(ocr.get("fallback_python") or ocr.get("python") or PROJECT_ROOT / "_bridge" / "venvs" / "ocr-py312" / "Scripts" / "python.exe"))
    runner = PROJECT_ROOT / "_bridge" / "gui_ocr_paddle_runner.py"
    image_path = PROJECT_ROOT / "_bridge" / "tmp" / "ocr_gpu_probe.png"

    def _run(args: list[str], timeout: int = 120) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                args,
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            try:
                parsed = json.loads((proc.stdout or "").strip().splitlines()[0])
            except Exception:
                parsed = {"ready": False, "stdout_tail": (proc.stdout or "")[-500:]}
            payload = parsed if isinstance(parsed, dict) else {"ready": False, "raw": parsed}
            payload["returncode"] = proc.returncode
            if proc.stderr and payload.get("ready") is not True:
                payload["stderr_tail"] = proc.stderr[-800:]
            return payload
        except Exception as exc:
            return {"ready": False, "error": str(exc), "exception_type": type(exc).__name__}

    if not image_path.exists():
        try:
            from PIL import Image, ImageDraw

            image_path.parent.mkdir(parents=True, exist_ok=True)
            img = Image.new("RGB", (360, 120), "white")
            draw = ImageDraw.Draw(img)
            draw.text((30, 40), "GUI OCR GPU TEST", fill="black")
            img.save(image_path)
        except Exception as exc:
            return {"ok": False, "read_only": False, "error": str(exc), "exception_type": type(exc).__name__}

    gpu_status = _run([str(gpu_python), str(runner), "--status"]) if gpu_python.exists() else {"ready": False, "error": "gpu_python_missing"}
    gpu_recognize = (
        _run([str(gpu_python), str(runner), "--image", str(image_path), "--device", "gpu", "--lang", "en", "--max-items", "10"], timeout=240)
        if gpu_python.exists() and runner.exists()
        else {"ready": False, "error": "gpu_python_or_runner_missing"}
    )
    cpu_fallback = (
        _run([str(cpu_python), str(runner), "--image", str(image_path), "--lang", "en", "--max-items", "10"], timeout=240)
        if cpu_python.exists() and runner.exists()
        else {"ready": False, "error": "cpu_python_or_runner_missing"}
    )
    blocked_reason = ""
    if gpu_status.get("compiled_cuda") is not True:
        blocked_reason = "gpu_python_not_cuda_enabled"
    elif gpu_recognize.get("ready") is not True:
        error = str(gpu_recognize.get("error") or "")
        blocked_reason = "gpu_recognition_failed"
        if "cudnn64_8.dll" in error:
            blocked_reason = "missing_cudnn64_8_dll"
    return {
        "ok": bool(gpu_status.get("compiled_cuda")) and bool(gpu_recognize.get("ready")) and bool(cpu_fallback.get("ready")),
        "read_only": True,
        "recommend_switch_default_to_gpu": bool(gpu_status.get("compiled_cuda")) and bool(gpu_recognize.get("ready")),
        "blocked_reason": blocked_reason,
        "gpu_python": str(gpu_python),
        "cpu_fallback_python": str(cpu_python),
        "runner": str(runner),
        "probe_image": str(image_path),
        "gpu_status": gpu_status,
        "gpu_recognize": gpu_recognize,
        "cpu_fallback": cpu_fallback,
    }


def gui_automation_health_check_regression() -> dict[str, Any]:
    config = load_config(DEFAULT_CONFIG)
    result = gui_automation_health_check(config, prewarm=False, ocr_probe=True)
    gui = result.get("gui_automation") if isinstance(result.get("gui_automation"), dict) else {}
    fallback = result.get("fallback") if isinstance(result.get("fallback"), dict) else {}
    ok = bool(
        result.get("read_only")
        and isinstance(result.get("mcp_config"), dict)
        and isinstance(result.get("self_check"), dict)
        and isinstance(result.get("ocr_prewarm"), dict)
        and isinstance(result.get("ocr_probe"), dict)
        and isinstance(result.get("live_mcp_reload"), dict)
        and fallback.get("boundary")
        and "runtime" in gui
        and "ocr" in gui
    )
    return {
        "ok": ok,
        "temp_only": True,
        "health_ok": bool(result.get("ok")),
        "gui_ok": bool(gui.get("ok")),
        "self_check_ok": bool((result.get("self_check") or {}).get("ok")),
        "ocr_ready": bool((gui.get("ocr") or {}).get("ready")),
        "ocr_probe_ok": bool((result.get("ocr_probe") or {}).get("ok")),
        "live_mcp_reload_reported": "restart_required_for_live_mcp" in (result.get("live_mcp_reload") or {}),
        "assertion": "GUI automation health check is read-only, reports runtime/OCR/config state, OCR probe state, live MCP reload guidance, and fallback guidance",
    }


def tool_registry_health(queue: MobileQueue, config: dict[str, Any]) -> dict[str, Any]:
    """Read-only health summary for the local Codex tool registry."""
    command_names = [
        "python",
        "py",
        "pip",
        "node",
        "npm",
        "npx",
        "pnpm",
        "git",
        "rg",
        "curl",
        "tar",
        "7z",
        "ffmpeg",
        "ffprobe",
        "java",
        "javac",
        "pwsh",
        "powershell",
        "sqlite3",
        "jq",
    ]
    commands = {
        name: {"ok": bool(path := shutil.which(name)), "path": path or ""}
        for name in command_names
    }
    optional_commands = {"pnpm", "7z", "sqlite3", "jq"}
    missing_optional = sorted(name for name in optional_commands if not commands.get(name, {}).get("ok"))

    registry_path = ROOT / "TOOL_REGISTRY.md"
    project_paths = {
        "tool_registry": registry_path,
        "mobile_cli": ROOT / "mobile_openclaw_cli.py",
        "mobile_mcp_server": ROOT / "mobile_bridge_mcp_server.py",
        "worker_launcher": ROOT / "start-worker-hidden.ps1",
        "worker_loop": ROOT / "run-worker-loop.ps1",
        "gateway_launcher": ROOT / "start-openclaw-gateway-hidden.ps1",
        "resource_cli": PROJECT_ROOT / "_bridge" / "resource_cli.py",
        "resource_fetcher": PROJECT_ROOT / "_bridge" / "resource_fetcher.py",
        "resource_process_doctor": PROJECT_ROOT / "_bridge" / "resource_process_doctor.py",
        "file_toolkit": PROJECT_ROOT / "_bridge" / "file_toolkit" / "__init__.py",
    }
    paths = {
        name: {"ok": path.exists(), "path": str(path)}
        for name, path in project_paths.items()
    }

    cdp_settings = codex_cdp_config(config)
    cdp_host = str(cdp_settings.get("host") or "127.0.0.1")
    cdp_port = int(cdp_settings.get("port") or 9229)
    cdp_tcp = tcp_check(cdp_port, host=cdp_host, timeout=0.35)
    cdp_version: dict[str, Any] = {"ok": False, "reason": "transport_not_ready"}
    if cdp_tcp.get("ok"):
        cdp_version = http_json("/json/version", cdp_port, host=cdp_host, timeout=0.35)
    cdp_os = os_port_listener_state(cdp_port)
    if bool(cdp_tcp.get("ok")) and bool(cdp_version.get("ok")):
        cdp_layer = "ready"
    elif int(cdp_os.get("stale_count") or 0) > 0 and int(cdp_os.get("live_count") or 0) == 0:
        cdp_layer = "stale_os_listener"
    else:
        cdp_layer = "transport_down"

    runtime = {
        "openclaw_gateway": tcp_check(18789, host="127.0.0.1", timeout=0.5),
        "codex_app_server": tcp_check(18791, host="127.0.0.1", timeout=0.5),
        "resource_layer": {
            "ok": bool(paths.get("resource_cli", {}).get("ok"))
            and bool(paths.get("resource_fetcher", {}).get("ok"))
            and bool(paths.get("file_toolkit", {}).get("ok")),
            "smoke_command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-layer-smoke-check",
        },
        "codex_cdp": {
            "ok": cdp_layer == "ready",
            "layer": cdp_layer,
            "host": cdp_host,
            "port": cdp_port,
            "tcp": cdp_tcp,
            "version": cdp_version,
            "os_port_state": cdp_os,
        },
        "queue": queue.health(),
        "codex_plugins": codex_plugin_config_health(run_cli=False),
    }

    critical_path_names = {
        "tool_registry",
        "mobile_cli",
        "mobile_mcp_server",
        "worker_launcher",
        "worker_loop",
        "gateway_launcher",
        "resource_cli",
        "resource_fetcher",
        "resource_process_doctor",
        "file_toolkit",
    }
    missing_critical_paths = sorted(
        name for name in critical_path_names if not paths.get(name, {}).get("ok")
    )
    critical_runtime_ok = bool(runtime["openclaw_gateway"].get("ok")) and bool(runtime["codex_app_server"].get("ok"))
    critical_ok = not missing_critical_paths and critical_runtime_ok
    degraded = cdp_layer != "ready" or bool(missing_optional)
    status = "ok" if critical_ok and not degraded else "degraded" if critical_ok else "unhealthy"

    return {
        "ok": critical_ok,
        "status": status,
        "generated_at": utc_now(),
        "registry_path": str(registry_path),
        "commands": commands,
        "missing_optional_commands": missing_optional,
        "paths": paths,
        "missing_critical_paths": missing_critical_paths,
        "runtime": runtime,
        "routing_policy": [
            "purpose-built MCP tool",
            "project CLI under _bridge",
            "structured parser or Python helper",
            "PowerShell command",
            "browser or desktop automation",
        ],
    }


def tool_registry_drift_check(queue: MobileQueue, config: dict[str, Any]) -> dict[str, Any]:
    """Read-only drift audit between TOOL_REGISTRY.md and live tool health."""
    health = tool_registry_health(queue, config)
    registry_path = ROOT / "TOOL_REGISTRY.md"
    try:
        registry_text = registry_path.read_text(encoding="utf-8", errors="replace")
        registry_ok = True
        registry_error = ""
    except Exception as exc:
        registry_text = ""
        registry_ok = False
        registry_error = str(exc)

    commands = health.get("commands") if isinstance(health.get("commands"), dict) else {}
    paths = health.get("paths") if isinstance(health.get("paths"), dict) else {}
    runtime = health.get("runtime") if isinstance(health.get("runtime"), dict) else {}

    command_drifts: list[dict[str, Any]] = []
    for name, item in sorted(commands.items()):
        if not isinstance(item, dict):
            continue
        mentioned = f"`{name}`" in registry_text or f"| `{name}` |" in registry_text or f"| {name} |" in registry_text
        if mentioned and not bool(item.get("ok")):
            command_drifts.append({"name": name, "registry": "mentioned", "live": "missing"})

    path_drifts: list[dict[str, Any]] = []
    for name, item in sorted(paths.items()):
        if not isinstance(item, dict):
            continue
        mentioned = str(item.get("path") or "") in registry_text or name in registry_text
        if mentioned and not bool(item.get("ok")):
            path_drifts.append({"name": name, "registry": "mentioned", "live": "missing", "path": item.get("path")})

    runtime_drifts: list[dict[str, Any]] = []
    expected_runtime = {
        "openclaw_gateway": "OpenClaw Gateway",
        "codex_app_server": "app-server",
        "resource_layer": "resource layer",
        "codex_cdp": "CDP",
    }
    for key, label in expected_runtime.items():
        item = runtime.get(key) if isinstance(runtime.get(key), dict) else {}
        if label.lower() in registry_text.lower() and not bool(item.get("ok")):
            runtime_drifts.append({"name": key, "registry": "documented", "live": item.get("layer") or "not_ok"})

    drift_count = len(command_drifts) + len(path_drifts) + len(runtime_drifts)
    return {
        "ok": registry_ok and drift_count == 0,
        "read_only": True,
        "generated_at": utc_now(),
        "registry_path": str(registry_path),
        "registry_read_ok": registry_ok,
        "registry_error": registry_error,
        "drift_count": drift_count,
        "command_drifts": command_drifts,
        "path_drifts": path_drifts,
        "runtime_drifts": runtime_drifts,
        "health_status": health.get("status"),
        "health_ok": health.get("ok"),
        "policy": "report_only_no_repair_no_state_change",
        "next_actions": [
            "If drift is real, update TOOL_REGISTRY.md only after user approval and backup.",
            "If live health is degraded, diagnose with maintenance summary/doctor before editing static notes.",
            "Do not treat static registry text as the source of truth over live health output.",
        ],
    }


def resource_layer_smoke_check() -> dict[str, Any]:
    """Temp-only check that resource acquisition is integrated into attachments."""
    import contextlib
    import http.server
    import socketserver
    import threading

    @contextlib.contextmanager
    def local_http_server(root: Path):
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

        class ReusableTCPServer(socketserver.TCPServer):
            allow_reuse_address = True

        previous = Path.cwd()
        try:
            os.chdir(root)
            with ReusableTCPServer(("127.0.0.1", 0), QuietHandler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    yield f"http://127.0.0.1:{server.server_address[1]}"
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
        finally:
            os.chdir(previous)

    with tempfile.TemporaryDirectory(prefix="resource-layer-smoke-") as temp_root:
        root = Path(temp_root)
        source = root / "source.txt"
        source.write_text("resource-layer-smoke-check\n", encoding="utf-8")
        expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        cache_dir = root / "cache"
        smoke_attachments = root / "attachments"
        smoke_resource_log = root / "resource-fetcher.jsonl"
        request = ResourceRequest(
            source="resource_layer_smoke_check",
            target_dir=cache_dir,
            name="source.txt",
            local_path=source,
            expected_sha256=expected_digest,
            metadata={"temp_only": True},
        )
        result = acquire_local_resource(request)
        stored = Path(result.stored_path) if result.stored_path else Path()
        stored_exists = bool(result.stored_path) and stored.exists()
        stored_digest = hashlib.sha256(stored.read_bytes()).hexdigest() if stored_exists else ""
        cache_files = [path for path in cache_dir.rglob("*") if path.is_file()] if cache_dir.exists() else []
        web = root / "web.txt"
        web.write_text("resource layer url attachment\n", encoding="utf-8")
        original_attachments = []
        original_attachments_dir = ATTACHMENTS_DIR
        original_resource_log = RESOURCE_LOG
        with local_http_server(root) as base_url:
            original_attachments = [
                {"name": "source.txt", "local_path": str(source), "expected_sha256": expected_digest},
                {"name": "web.txt", "url": f"{base_url}/web.txt"},
                {"name": "bad-url.txt", "url": "file:///not-allowed.txt"},
            ]
            try:
                globals()["ATTACHMENTS_DIR"] = smoke_attachments
                globals()["RESOURCE_LOG"] = smoke_resource_log
                attachment_resources.ATTACHMENTS_DIR = smoke_attachments
                attachment_resources.RESOURCE_LOG = smoke_resource_log
                materialized = materialize_attachments(original_attachments)
                materialized_again = materialize_attachments([materialized[0]])
            finally:
                globals()["ATTACHMENTS_DIR"] = original_attachments_dir
                globals()["RESOURCE_LOG"] = original_resource_log
                attachment_resources.ATTACHMENTS_DIR = original_attachments_dir
                attachment_resources.RESOURCE_LOG = original_resource_log
        local_att = materialized[0]
        url_att = materialized[1]
        failed_att = materialized[2]
        prompt_lines = describe_attachment(local_att, 1)
        ok = bool(
            result.ok
            and stored_exists
            and stored_digest == expected_digest
            and result.sha256 == expected_digest
            and len(cache_files) == 1
            and local_att.get("resource_status") == "stored"
            and local_att.get("resource_policy") == "explicit_attachment_v1"
            and local_att.get("resource_decision") == "allowed"
            and local_att.get("analysis_kind") == "text"
            and "resource-layer-smoke-check" in str(local_att.get("analysis_preview") or "")
            and url_att.get("resource_status") == "stored"
            and url_att.get("resource_policy") == "explicit_attachment_v1"
            and url_att.get("resource_decision") == "allowed"
            and url_att.get("analysis_kind") == "text"
            and failed_att.get("resource_status") == "failed"
            and failed_att.get("resource_error") == "unsupported_url_scheme"
            and failed_att.get("resource_policy") == "explicit_attachment_v1"
            and failed_att.get("resource_decision") == "blocked"
            and bool(materialized_again and materialized_again[0].get("resource_cache_hit"))
            and any("resource_status=stored" in line and "policy=explicit_attachment_v1" in line for line in prompt_lines)
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result.to_dict(),
            "materialized": materialized,
            "materialized_again": materialized_again,
            "prompt_lines": prompt_lines,
            "stored_exists": stored_exists,
            "stored_digest": stored_digest,
            "expected_digest": expected_digest,
            "cache_file_count": len(cache_files),
            "assertion": "resource layer materializes local and URL attachments, persists analysis, records failures, and exposes prompt-ready metadata",
        }


def _maintenance_boundary_snapshot(codex_plugins: dict[str, Any], *, deep: bool) -> dict[str, Any]:
    return {
        "counts": {"by_status": {}},
        "database": {"exists": True, "integrity_check": "ok", "under_limit": True},
        "ports": {
            "openclaw_gateway": {"ok": True},
            "codex_app_server": {"ok": True},
            "codex_cdp": {"ok": True},
        },
        "processes": {"worker": {"ok": True, "count": 1}},
        "scheduled_tasks": {"worker": {"ok": True, "state": "Ready"}, "gateway": {"ok": True}},
        "control": {"paused": False, "stop_request_exists": False},
        "active": [],
        "pending": [],
        "reply_problems": [],
        "routes": {},
        "recent_events": {},
        "event_noise": {"guard_index_exists": True},
        "event_archive_dry_run": {"candidate_count": 0},
        "cdp_route": {"ok": True, "layer": "ready"},
        "mobile_mcp": {
            "ok": True,
            "tool_names": [
                "bridge.health",
                "bridge.poll_updates",
                "bridge.ack_message",
                "bridge.get_pending_batch",
            ],
        },
        "codex_mcp_config": {"ok": True},
        "codex_plugins": codex_plugins,
        "app_server_mcp": {"ok": True, "layer": "ok"},
        "gui_automation": {"ok": True},
        "dashboard_live_state": {"ok": True},
        "deep_probes": bool(deep),
        "app_server_materialization_lag": [],
        "top_pending_routes": [],
        "top_active_routes": [],
        "top_accounts": [],
    }


def maintenance_misjudgment_boundary_check() -> dict[str, Any]:
    """Temp-only regression for maintenance evidence freshness and plugin misjudgment."""
    cases = [
        {
            "name": "quick_skipped_is_not_plugin_failure",
            "deep": False,
            "probe": {"ok": None, "skipped": True, "reason": "quick probe skipped"},
            "expect_issue": False,
            "expect_state": "quick_skipped",
        },
        {
            "name": "stale_plugin_observation_is_not_current_failure",
            "deep": True,
            "probe": {
                "ok": False,
                "stale_observation": True,
                "reason": "historical plugin log before current config repair",
                "missing_enabled_plugins": ["browser@openai-bundled"],
            },
            "expect_issue": False,
            "expect_state": "stale_observation",
        },
        {
            "name": "current_missing_plugin_still_reports_repairable_issue",
            "deep": True,
            "probe": {
                "ok": False,
                "config_parse_ok": True,
                "config_path": "C:/Users/45543/.codex/config.toml",
                "missing_enabled_plugins": ["browser@openai-bundled"],
                "missing_cache_plugins": [],
                "missing_manifest_plugins": [],
            },
            "expect_issue": True,
            "expect_state": "current_failure",
        },
        {
            "name": "current_ok_plugin_does_not_report_issue",
            "deep": True,
            "probe": {
                "ok": True,
                "config_parse_ok": True,
                "missing_enabled_plugins": [],
                "missing_cache_plugins": [],
                "missing_manifest_plugins": [],
            },
            "expect_issue": False,
            "expect_state": "current_ok",
        },
    ]
    results: list[dict[str, Any]] = []
    ok = True
    for case in cases:
        evidence = probe_evidence_state(case["probe"], profile="deep" if case["deep"] else "quick")
        snapshot = _maintenance_boundary_snapshot(case["probe"], deep=bool(case["deep"]))
        diagnosis = diagnose_system(snapshot)
        issues = diagnosis.get("issues") if isinstance(diagnosis.get("issues"), list) else []
        plugin_issues = [item for item in issues if str(item.get("code") or "").startswith("codex_plugin")]
        case_ok = (
            bool(plugin_issues) == bool(case["expect_issue"])
            and str(evidence.get("state") or "") == str(case["expect_state"])
        )
        ok = ok and case_ok
        results.append(
            {
                "name": case["name"],
                "ok": case_ok,
                "evidence_state": evidence.get("state"),
                "current_failure": bool(evidence.get("current_failure")),
                "plugin_issue_codes": [item.get("code") for item in plugin_issues],
                "expected_issue": bool(case["expect_issue"]),
            }
        )
    return {
        "ok": ok,
        "temp_only": True,
        "cases": results,
        "assertion": "maintenance only reports plugin repair for current failure evidence; quick skipped and stale observations remain non-actionable",
    }


def codex_plugin_cli_visibility_boundary_check() -> dict[str, Any]:
    """Read-only regression for bundled plugin CLI-list visibility semantics."""
    health = codex_plugin_config_health(run_cli=True)
    expected = health.get("expected_plugins") if isinstance(health.get("expected_plugins"), dict) else {}
    bundled_keys = ["browser@openai-bundled", "chrome@openai-bundled", "computer-use@openai-bundled"]
    bundled = {key: expected.get(key, {}) for key in bundled_keys}
    bundled_config_cache_ok = all(
        bool(item.get("enabled")) and bool(item.get("cache_ok")) and bool(item.get("manifest_ok"))
        for item in bundled.values()
        if isinstance(item, dict)
    )
    missing_cli = set(str(item) for item in (health.get("missing_cli_visible_plugins") or []) if str(item))
    bundled_missing_cli = sorted(missing_cli.intersection(bundled_keys))
    ok = bool(
        health.get("ok")
        and str(health.get("status") or "") == "ok"
        and bundled_config_cache_ok
        and not bundled_missing_cli
    )
    return {
        "ok": ok,
        "temp_only": True,
        "health_ok": health.get("ok"),
        "status": health.get("status"),
        "bundled_config_cache_ok": bundled_config_cache_ok,
        "bundled_missing_cli_visible": bundled_missing_cli,
        "missing_enabled_plugins": health.get("missing_enabled_plugins"),
        "missing_cache_plugins": health.get("missing_cache_plugins"),
        "missing_manifest_plugins": health.get("missing_manifest_plugins"),
        "assertion": "openai-bundled browser/chrome/computer-use are healthy by config/cache/manifest; absence from codex plugin list is not a current plugin failure",
    }


def check_codex_health_app_server(config: dict[str, Any]) -> dict[str, Any]:
    startup = ensure_codex_app_server(config)
    if not startup.get("ok"):
        result = dict(startup)
        result.update({"ok": False, "healthy": False, "mode": "codex-app-server"})
        return result
    parsed = run_codex_app_server_client(config, ["--check-health"])
    parsed["startup"] = startup
    parsed.setdefault("mode", "codex-app-server")
    return parsed


def check_codex_health(config: dict[str, Any]) -> dict[str, Any]:
    mode = str(config.get("trigger", {}).get("delivery_mode") or "stub").lower()
    if mode == "codex-app-server":
        return check_codex_health_app_server(config)
    if mode == "codex-cdp":
        return check_codex_health_cdp(config)
    return {"ok": False, "healthy": False, "mode": mode, "reason": "delivery mode has no health check"}


def health_result_is_transient_probe_failure(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    if bool(result.get("healthy")):
        return False
    if bool(result.get("transient")):
        return True
    reason = str(result.get("reason") or result.get("error") or "").lower()
    return any(
        token in reason
        for token in (
            "snapshot_failed",
            "no_page",
            "aggregateerror",
            "websocket",
            "timeout",
            "cdp_port_not_listening",
            "version_not_ready",
            "launch",
            "start",
        )
    )


def cancel_codex_generation_cdp(config: dict[str, Any]) -> dict[str, Any]:
    trigger = config.get("trigger", {})
    node = str(trigger.get("node_path") or "node")
    script = Path(
        trigger.get("codex_cdp_script")
        or PROJECT_ROOT / "_tools" / "codex-cdp-tools" / "codex_cdp_send.js"
    )
    port = int(trigger.get("codex_cdp_port") or 9229)
    host = str(trigger.get("codex_cdp_host") or "localhost")
    timeout = int(trigger.get("delivery_timeout_seconds") or 20)
    command = [node, str(script), "--host", host, "--port", str(port), "--cancel"]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=str(PROJECT_ROOT / "_tools" / "codex-cdp-tools"),
        )
    except Exception as exc:
        return {"ok": False, "cancelled": False, "reason": f"cancel failed: {exc}"}
    try:
        parsed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        parsed = {"ok": False, "cancelled": False, "raw_stdout": proc.stdout}
    parsed["returncode"] = proc.returncode
    return parsed


def cancel_codex_generation_app_server(config: dict[str, Any], thread_id: str = "", turn_id: str = "") -> dict[str, Any]:
    if not thread_id or not turn_id:
        return {"ok": False, "cancelled": False, "mode": "codex-app-server", "reason": "thread_id and turn_id are required"}
    startup = ensure_codex_app_server(config)
    if not startup.get("ok"):
        result = dict(startup)
        result.update({"ok": False, "cancelled": False, "mode": "codex-app-server"})
        return result
    parsed = run_codex_app_server_client(
        config,
        ["--cancel", "--thread-id", thread_id, "--turn-id", turn_id],
    )
    parsed["startup"] = startup
    parsed.setdefault("mode", "codex-app-server")
    return parsed


def cancel_codex_generation(config: dict[str, Any], thread_id: str = "", turn_id: str = "") -> dict[str, Any]:
    mode = str(config.get("trigger", {}).get("delivery_mode") or "stub").lower()
    if mode == "codex-app-server":
        return cancel_codex_generation_app_server(config, thread_id, turn_id)
    if mode == "codex-cdp":
        return cancel_codex_generation_cdp(config)
    return {"ok": False, "cancelled": False, "mode": mode, "reason": "delivery mode has no cancel handler"}


def poll_codex_result_cdp(
    config: dict[str, Any],
    baseline_key: str,
    expected_task_ids: list[str] | None = None,
    expected_result_codes: dict[str, str] | None = None,
    expected_ack_codes: dict[str, str] | None = None,
) -> dict[str, Any]:
    startup = ensure_codex_cdp(config)
    settings = codex_cdp_config(config)
    if not startup.get("ok"):
        startup_reason = str(startup.get("reason") or "")
        return {
            "ok": True,
            "healthy": False,
            "generationActive": False,
            "transient": startup_reason != "codex_cdp_stale_os_listener",
            "reason": startup_reason or "codex cdp is starting or unavailable",
            "startup": startup,
            "newText": None,
        }
    command = [settings["node"], str(settings["script"]), "--host", settings["host"], "--port", str(settings["port"]), "--poll-once"]
    if baseline_key:
        command.extend(["--baseline-key", baseline_key])
    if expected_task_ids:
        command.extend(["--expected-task-ids", ",".join([str(item) for item in expected_task_ids if str(item)] )])
    if expected_result_codes:
        result_codes = ",".join(
            f"{task_id}={code}"
            for task_id, code in expected_result_codes.items()
            if task_id and code
        )
        if result_codes:
            command.extend(["--expected-result-codes", result_codes])
    if expected_ack_codes:
        ack_codes = ",".join(
            f"{task_id}={code}"
            for task_id, code in expected_ack_codes.items()
            if task_id and code
        )
        if ack_codes:
            command.extend(["--expected-ack-codes", ack_codes])
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=settings["timeout"],
            cwd=str(PROJECT_ROOT / "_tools" / "codex-cdp-tools"),
        )
    except Exception as exc:
        return {"ok": False, "newText": None, "reason": f"poll-once failed: {exc}", "startup": startup}
    try:
        parsed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        parsed = {"ok": False, "newText": None, "raw_stdout": proc.stdout}
    parsed["startup"] = startup
    return parsed


def poll_codex_result_app_server(
    config: dict[str, Any],
    thread_id: str,
    turn_id: str,
    client_message_id: str = "",
    expected_task_ids: list[str] | None = None,
    expected_result_codes: dict[str, str] | None = None,
    expected_ack_codes: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not thread_id or not turn_id:
        return {"ok": False, "newText": None, "healthy": False, "reason": "thread_id and turn_id are required"}
    startup = ensure_codex_app_server(config)
    if not startup.get("ok"):
        result = dict(startup)
        result.update({"ok": False, "newText": None, "healthy": False, "mode": "codex-app-server"})
        return result
    args = ["--poll-result", "--thread-id", thread_id, "--turn-id", turn_id]
    if client_message_id:
        args.extend(["--client-message-id", client_message_id])
    if expected_task_ids:
        args.extend(["--expected-task-ids", ",".join(expected_task_ids)])
    if expected_result_codes:
        result_codes = ",".join(
            f"{task_id}={code}"
            for task_id, code in expected_result_codes.items()
            if task_id and code
        )
        if result_codes:
            args.extend(["--expected-result-codes", result_codes])
    if expected_ack_codes:
        ack_codes = ",".join(
            f"{task_id}={code}"
            for task_id, code in expected_ack_codes.items()
            if task_id and code
        )
        if ack_codes:
            args.extend(["--expected-ack-codes", ack_codes])
    parsed = run_codex_app_server_client(config, args)
    if app_server_result_poll_was_timeout(parsed):
        retry_config = dict(config)
        retry_trigger = dict(config.get("trigger", {}))
        retry_trigger["delivery_timeout_seconds"] = app_server_result_poll_second_chance_timeout_seconds(config)
        retry_config["trigger"] = retry_trigger
        retry = run_codex_app_server_client(retry_config, args, timeout_extra_seconds=5)
        retry["first_attempt"] = parsed
        retry["second_chance"] = True
        retry["second_chance_timeout_seconds"] = retry_trigger["delivery_timeout_seconds"]
        retry["startup"] = startup
        retry.setdefault("mode", "codex-app-server")
        return retry
    parsed["startup"] = startup
    parsed.setdefault("mode", "codex-app-server")
    return parsed


def poll_codex_result(
    config: dict[str, Any],
    thread_id: str,
    turn_id: str,
    baseline_key: str,
    client_message_id: str = "",
    expected_task_ids: list[str] | None = None,
    expected_result_codes: dict[str, str] | None = None,
    expected_ack_codes: dict[str, str] | None = None,
) -> dict[str, Any]:
    mode = str(config.get("trigger", {}).get("delivery_mode") or "stub").lower()
    if mode == "codex-app-server":
        return poll_codex_result_app_server(config, thread_id, turn_id, client_message_id, expected_task_ids, expected_result_codes, expected_ack_codes)
    if mode == "codex-cdp":
        return poll_codex_result_cdp(config, baseline_key, expected_task_ids, expected_result_codes, expected_ack_codes)
    return {"ok": False, "newText": None, "healthy": False, "mode": mode, "reason": "delivery mode has no result poll handler"}


def task_turn_key(task_id: str) -> str:
    return f"codex_turn:{task_id}"


def poll_has_ownership_mismatch(poll: dict[str, Any]) -> bool:
    if str(poll.get("protocol") or "") == "mobile_result_boundary_v2":
        if not bool(poll.get("result_complete")) and not bool(poll.get("terminal_without_text")):
            return False
    ownership = poll.get("ownership")
    if isinstance(ownership, dict) and ownership.get("valid") is False:
        return True
    return bool(poll.get("ownership_mismatch"))


def cdp_delivery_lacks_submission_evidence(delivery: dict[str, Any]) -> bool:
    """Return true when CDP reports ok but no prompt/marker was visibly submitted."""
    if str(delivery.get("mode") or "") != "codex-cdp":
        return False
    if not bool(delivery.get("submission_unconfirmed")):
        return False
    desktop_visible = delivery.get("desktop_visible")
    if not isinstance(desktop_visible, dict):
        return True
    if bool(desktop_visible.get("confirmed")):
        return False
    if bool(desktop_visible.get("body_has_exact_prompt")):
        return False
    return True


def record_unowned_intermediate_result(
    queue: MobileQueue,
    task_id: str,
    poll: dict[str, Any],
) -> None:
    """Record unowned Codex text without redispatching the same mobile task."""
    tid = str(task_id or "")
    if not tid:
        return
    queue.add_event("local", "unowned_intermediate_seen", {"poll": poll}, tid)


def poll_turn_was_superseded(poll: dict[str, Any]) -> bool:
    # Do not redispatch a mobile task merely because a newer turn exists in the
    # same thread. Desktop/manual activity can create newer turns while the
    # mobile turn is still valid, and treating that as supersession caused
    # duplicate Weixin deliveries.
    return False


def task_batch_runtime(queue: MobileQueue, task_id: str, fallback_task_ids: list[str] | None = None) -> tuple[str, list[str]]:
    client_message_id = str(queue.runtime_get(task_batch_key(task_id)) or "")
    expected_value = queue.runtime_get(task_expected_ids_key(task_id))
    expected_raw = str(expected_value or "")
    expected_task_ids: list[str] = []
    if expected_raw:
        try:
            parsed = json.loads(expected_raw)
            if isinstance(parsed, list):
                expected_task_ids = [str(item) for item in parsed if str(item)]
        except json.JSONDecodeError:
            expected_task_ids = [item.strip() for item in expected_raw.split(",") if item.strip()]
    expected_missing = expected_value is None or not str(expected_value or "").strip()
    if expected_missing and not expected_task_ids and fallback_task_ids:
        expected_task_ids = [str(item) for item in fallback_task_ids if str(item)]
    if expected_missing and not expected_task_ids:
        event = latest_task_event_payload(queue, task_id, "codex_turn_started")
        if event:
            event_expected = event.get("expected_task_ids")
            if isinstance(event_expected, list):
                expected_task_ids = [str(item) for item in event_expected if str(item)]
            if not client_message_id:
                client_message_id = str(event.get("client_message_id") or "")
    return client_message_id, expected_task_ids


def mobile_protocols_from_turn_event(event: dict[str, Any], expected_task_ids: list[str], client_message_id: str) -> dict[str, dict[str, str]]:
    protocols = event.get("mobile_protocols")
    if not isinstance(protocols, dict):
        protocols = event.get("protocols")
    if isinstance(protocols, dict):
        result: dict[str, dict[str, str]] = {}
        for task_id in expected_task_ids:
            value = protocols.get(task_id)
            if not isinstance(value, dict):
                continue
            ack_code = str(value.get("ack_code") or "").strip()
            result_code = str(value.get("result_code") or "").strip()
            if ack_code or result_code:
                result[task_id] = {
                    "task_id": task_id,
                    "ack_code": ack_code,
                    "result_code": result_code,
                }
        if result:
            return result
    if client_message_id:
        return mobile_protocols([{"id": item} for item in expected_task_ids], client_message_id)
    return {}


def rehydrate_codex_turn_runtime_from_event(queue: MobileQueue, task_id: str) -> dict[str, Any]:
    """Rebuild volatile Codex turn runtime from the durable codex_turn_started event."""
    tid = str(task_id or "")
    if not tid:
        return {"ok": False, "rehydrated": False, "reason": "task_id is required"}
    event = latest_task_event_payload(queue, tid, "codex_turn_started")
    if not event:
        return {"ok": False, "rehydrated": False, "reason": "codex_turn_started event not found"}
    turn_id = str(event.get("turn_id") or "")
    client_message_id = str(event.get("client_message_id") or "")
    expected_task_ids = [str(item) for item in (event.get("expected_task_ids") or []) if str(item)]
    if not turn_id:
        return {"ok": False, "rehydrated": False, "reason": "codex_turn_started event has no turn_id"}
    if not expected_task_ids:
        expected_task_ids = [tid]

    queue.runtime_set(task_turn_key(tid), turn_id)
    if client_message_id:
        queue.runtime_set(task_batch_key(tid), client_message_id)
    protocols = mobile_protocols_from_turn_event(event, expected_task_ids, client_message_id)
    queue.runtime_set(task_expected_ids_key(tid), json.dumps(expected_task_ids, ensure_ascii=False))
    for expected_id in expected_task_ids:
        protocol = protocols.get(expected_id) or {}
        ack_code = str(protocol.get("ack_code") or "")
        result_code = str(protocol.get("result_code") or "")
        if ack_code:
            queue.runtime_set(task_ack_code_key(expected_id), ack_code)
        if result_code:
            queue.runtime_set(task_result_code_key(expected_id), result_code)
    queue.add_event(
        "local",
        "codex_turn_runtime_rehydrated",
        {
            "turn_id": turn_id,
            "client_message_id": client_message_id,
            "expected_task_ids": expected_task_ids,
            "protocol_codes_rehydrated": sorted(protocols.keys()),
            "source_event_created_at": str(event.get("_event_created_at") or ""),
        },
        tid,
    )
    return {
        "ok": True,
        "rehydrated": True,
        "turn_id": turn_id,
        "client_message_id": client_message_id,
        "expected_task_ids": expected_task_ids,
        "protocol_codes_rehydrated": sorted(protocols.keys()),
    }


def _protocol_codes_from_delivery(
    delivery: dict[str, Any],
    expected_task_ids: list[str],
    client_message_id: str,
) -> dict[str, dict[str, str]]:
    protocols = delivery.get("mobile_protocols")
    if not isinstance(protocols, dict):
        protocols = delivery.get("protocols")
    if isinstance(protocols, dict):
        event_like = {"mobile_protocols": protocols}
        return mobile_protocols_from_turn_event(event_like, expected_task_ids, client_message_id)
    if client_message_id:
        return mobile_protocols([{"id": item} for item in expected_task_ids], client_message_id)
    return {}


def _delivery_has_protocol_evidence(delivery: dict[str, Any], expected_task_ids: list[str]) -> bool:
    prompt = str(delivery.get("prompt") or "")
    protocols = delivery.get("mobile_protocols") if isinstance(delivery.get("mobile_protocols"), dict) else {}
    for task_id in expected_task_ids:
        tid = str(task_id or "")
        if not tid:
            continue
        protocol = protocols.get(tid) if isinstance(protocols.get(tid), dict) else {}
        ack_code = str(protocol.get("ack_code") or "").strip()
        result_code = str(protocol.get("result_code") or "").strip()
        if tid in prompt and (ack_code and ack_code in prompt) and (result_code and result_code in prompt):
            return True
    return False


def materialized_turn_readback_evidence(
    config: dict[str, Any],
    thread_id: str,
    turn_id: str,
    client_message_id: str,
    expected_task_ids: list[str],
    protocols: dict[str, dict[str, str]],
) -> dict[str, Any]:
    if not thread_id or not turn_id:
        return {"ok": False, "materialized": False, "reason": "thread_id and turn_id are required"}
    expected_result_codes: dict[str, str] = {}
    expected_ack_codes: dict[str, str] = {}
    for task_id in expected_task_ids:
        protocol = protocols.get(task_id) if isinstance(protocols.get(task_id), dict) else {}
        result_code = str(protocol.get("result_code") or "")
        ack_code = str(protocol.get("ack_code") or "")
        if result_code:
            expected_result_codes[task_id] = result_code
        if ack_code:
            expected_ack_codes[task_id] = ack_code
    poll_config = task_delivery_config(config, "codex-app-server")
    poll = poll_codex_result_app_server(
        poll_config,
        thread_id,
        turn_id,
        client_message_id,
        expected_task_ids,
        expected_result_codes,
        expected_ack_codes,
    )
    ownership = poll.get("ownership") if isinstance(poll.get("ownership"), dict) else {}
    status = str(poll.get("status") or "")
    matched_turn_id = str(poll.get("matched_turn_id") or "")
    materialized = bool(poll.get("healthy")) and status != "notFound" and str(poll.get("turn_id") or turn_id) == turn_id
    marker_seen = bool(poll.get("ack_seen")) or bool(poll.get("result_complete")) or bool(
        ownership.get("ack_seen") or ownership.get("begin_seen") or ownership.get("end_seen")
    )
    result_complete = bool(poll.get("result_complete")) or bool(ownership.get("result_complete"))
    if result_complete:
        return {
            "ok": True,
            "materialized": True,
            "marker_seen": True,
            "result_complete": True,
            "poll": poll,
            "reason": "owned_result_visible",
        }
    if materialized and marker_seen:
        return {
            "ok": True,
            "materialized": True,
            "marker_seen": True,
            "result_complete": False,
            "poll": poll,
            "reason": "owned_marker_visible",
        }
    if materialized and matched_turn_id == turn_id and bool(ownership.get("client_message_id_matched")):
        return {
            "ok": True,
            "materialized": True,
            "marker_seen": False,
            "result_complete": False,
            "poll": poll,
            "reason": "client_message_visible",
        }
    return {
        "ok": False,
        "materialized": materialized,
        "marker_seen": marker_seen,
        "result_complete": result_complete,
        "poll": poll,
        "reason": "materialized_turn_readback_not_confirmed",
    }


def provisional_codex_turn_runtime_from_unreadable_dispatch(
    queue: MobileQueue,
    config: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    """Recover a provisional app-server turn from unreadable-dispatch evidence.

    This is narrower than codex_turn_started rehydration: the prior dispatch
    returned a turn id but the immediate turns/list verification missed it.
    We only use it when the durable failed-delivery event still contains the
    exact mobile protocol markers in the submitted prompt.
    """
    tid = str(task_id or "")
    if not tid:
        return {"ok": False, "rehydrated": False, "reason": "task_id is required"}
    for payload in recent_task_event_payloads(queue, tid, "delivery_failed_reverted_to_pending", limit=8):
        delivery = payload.get("delivery") if isinstance(payload.get("delivery"), dict) else {}
        if str(delivery.get("mode") or "") != "codex-app-server":
            continue
        if str(delivery.get("reason") or "") != "app_server_turn_not_readable_after_dispatch":
            continue
        turn_id = str(delivery.get("turn_id") or "")
        thread_id = str(delivery.get("thread_id") or "")
        client_message_id = str(delivery.get("client_user_message_id") or "")
        expected_task_ids = [str(item) for item in (delivery.get("expected_task_ids") or []) if str(item)]
        if not expected_task_ids:
            expected_task_ids = [tid]
        if tid not in expected_task_ids:
            continue
        if not turn_id:
            continue
        if not _delivery_has_protocol_evidence(delivery, expected_task_ids):
            continue
        protocols = _protocol_codes_from_delivery(delivery, expected_task_ids, client_message_id)
        readback = materialized_turn_readback_evidence(
            config,
            thread_id,
            turn_id,
            client_message_id,
            expected_task_ids,
            protocols,
        )
        if not readback.get("ok"):
            queue.add_event(
                "local",
                "codex_turn_materialization_readback_not_confirmed",
                {
                    "turn_id": turn_id,
                    "thread_id": thread_id,
                    "client_message_id": client_message_id,
                    "expected_task_ids": expected_task_ids,
                    "readback": readback,
                    "source_event_created_at": str(payload.get("_event_created_at") or ""),
                    "policy": "do not rehydrate queued task from local prompt markers unless app-server readback confirms the materialized turn or owned marker",
                },
                tid,
            )
            continue
        queue.runtime_set(task_turn_key(tid), turn_id)
        if client_message_id:
            queue.runtime_set(task_batch_key(tid), client_message_id)
        queue.runtime_set(task_expected_ids_key(tid), json.dumps(expected_task_ids, ensure_ascii=False))
        for expected_id in expected_task_ids:
            protocol = protocols.get(expected_id) or {}
            ack_code = str(protocol.get("ack_code") or "")
            result_code = str(protocol.get("result_code") or "")
            if ack_code:
                queue.runtime_set(task_ack_code_key(expected_id), ack_code)
            if result_code:
                queue.runtime_set(task_result_code_key(expected_id), result_code)
        queue.add_event(
            "local",
            "codex_turn_runtime_rehydrated_from_unreadable_dispatch",
            {
                "turn_id": turn_id,
                "thread_id": thread_id,
                "client_message_id": client_message_id,
                "expected_task_ids": expected_task_ids,
                "protocol_codes_rehydrated": sorted(protocols.keys()),
                "source_event_created_at": str(payload.get("_event_created_at") or ""),
                "readback": readback,
                "reason": "app_server_turn_materialization_lag",
                "policy": "rehydrate a queued task only from exact unreadable-dispatch marker evidence; do not redispatch the same materialized turn",
            },
            tid,
        )
        return {
            "ok": True,
            "rehydrated": True,
            "turn_id": turn_id,
            "thread_id": thread_id,
            "client_message_id": client_message_id,
            "expected_task_ids": expected_task_ids,
            "protocol_codes_rehydrated": sorted(protocols.keys()),
            "source": "delivery_failed_reverted_to_pending",
            "readback": readback,
            "reason": "app_server_turn_materialization_lag",
        }
    return {
        "ok": False,
        "rehydrated": False,
        "reason": "no exact unreadable-dispatch materialization evidence",
    }


def delivery_group_member_ids_from_events(
    queue: MobileQueue,
    owner_task_id: str,
    thread_id: str = "",
) -> list[str]:
    owner_id = str(owner_task_id or "")
    if not owner_id:
        return []
    candidates: list[str] = []
    like = f'%"{owner_id}"%'
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT task_id, payload_json
            FROM mobile_events
            WHERE event_type='delivery_group_member'
              AND payload_json LIKE ?
            ORDER BY id ASC
            """,
            (like,),
        ).fetchall()
    for row in rows:
        task_id = str(row["task_id"] or "")
        if not task_id:
            continue
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        payload_owner = str(payload.get("owner_task_id") or "")
        payload_owners = [str(item) for item in payload.get("owner_task_ids") or [] if str(item)]
        if payload_owner != owner_id and owner_id not in payload_owners:
            continue
        payload_thread_id = str(payload.get("thread_id") or "")
        if thread_id and payload_thread_id and payload_thread_id != thread_id:
            continue
        candidates.append(task_id)
    return candidates


def delivery_group_member_ids(queue: MobileQueue, owner_task_id: str, thread_id: str = "") -> list[str]:
    raw = str(queue.runtime_get(delivery_group_members_key(owner_task_id)) or "")
    members: list[str] = []
    if not raw:
        parsed = []
    else:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(parsed, list):
        members.extend([str(item) for item in parsed if str(item)])
    members.extend(delivery_group_member_ids_from_events(queue, owner_task_id, thread_id))
    unique: list[str] = []
    seen: set[str] = set()
    for member_id in members:
        if member_id and member_id not in seen:
            seen.add(member_id)
            unique.append(member_id)
    return unique


def delivery_group_owner_id_for_member(queue: MobileQueue, member_task_id: str) -> str:
    task_id = str(member_task_id or "")
    if not task_id:
        return ""
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT payload_json
            FROM mobile_events
            WHERE task_id=? AND event_type='delivery_group_member'
            ORDER BY id DESC
            LIMIT 5
            """,
            (task_id,),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        owner_id = str(payload.get("owner_task_id") or "")
        if owner_id:
            return owner_id
        owner_ids = [str(item) for item in payload.get("owner_task_ids") or [] if str(item)]
        if owner_ids:
            return owner_ids[0]
    return ""


def task_result_code_runtime(queue: MobileQueue, task_ids: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for task_id in task_ids:
        tid = str(task_id or "")
        code = str(queue.runtime_get(task_result_code_key(tid)) or "").strip()
        if tid and code:
            result[tid] = code
    return result


def task_ack_code_runtime(queue: MobileQueue, task_ids: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for task_id in task_ids:
        tid = str(task_id or "")
        code = str(queue.runtime_get(task_ack_code_key(tid)) or "").strip()
        if tid and code:
            result[tid] = code
    return result


def recent_codex_turn_protocol_attempts(
    queue: MobileQueue,
    task_id: str,
    current_expected_task_ids: list[str],
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return durable Codex turn protocol attempts for the active owner task."""
    tid = str(task_id or "")
    if not tid:
        return []
    current_expected = {str(item) for item in current_expected_task_ids if str(item)}
    if not current_expected:
        current_expected = {tid}
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, payload_json, created_at
            FROM mobile_events
            WHERE task_id=? AND event_type='codex_turn_started'
            ORDER BY id DESC
            LIMIT ?
            """,
            (tid, max(1, int(limit))),
        ).fetchall()
    attempts: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        turn_id = str(payload.get("turn_id") or "")
        client_message_id = str(payload.get("client_message_id") or "")
        raw_expected = payload.get("expected_task_ids")
        event_expected = [str(item) for item in raw_expected or [] if str(item)] if isinstance(raw_expected, list) else []
        if not event_expected:
            event_expected = [tid]
        expected_task_ids = [item for item in event_expected if item in current_expected]
        if not expected_task_ids and tid in event_expected:
            expected_task_ids = [tid]
        if not expected_task_ids:
            continue
        protocols = mobile_protocols_from_turn_event(payload, expected_task_ids, client_message_id)
        result_codes: dict[str, str] = {}
        ack_codes: dict[str, str] = {}
        for expected_id in expected_task_ids:
            protocol = protocols.get(expected_id) or {}
            result_code = str(protocol.get("result_code") or "").strip()
            ack_code = str(protocol.get("ack_code") or "").strip()
            if result_code:
                result_codes[expected_id] = result_code
            if ack_code:
                ack_codes[expected_id] = ack_code
        if not result_codes:
            continue
        key = (
            turn_id,
            client_message_id,
            tuple(expected_task_ids),
            tuple(sorted(result_codes.items())),
            tuple(sorted(ack_codes.items())),
        )
        if key in seen:
            continue
        seen.add(key)
        attempts.append(
            {
                "event_id": int(row["id"]),
                "event_created_at": str(row["created_at"] or ""),
                "turn_id": turn_id,
                "client_message_id": client_message_id,
                "expected_task_ids": expected_task_ids,
                "expected_result_codes": result_codes,
                "expected_ack_codes": ack_codes,
            }
        )
    return attempts


def poll_historical_owned_codex_result(
    queue: MobileQueue,
    poll_config: dict[str, Any],
    task_id: str,
    thread_id: str,
    current_turn_id: str,
    current_client_message_id: str,
    expected_task_ids: list[str],
    expected_result_codes: dict[str, str],
    expected_ack_codes: dict[str, str],
) -> dict[str, Any]:
    """Try prior mobile protocol codes for the same active task.

    A redelivered task can finish in a newer Codex turn while the model emits an
    older attempt's result code. The normal poll should stay strict; this helper
    only runs after that poll has no owned text and still requires the app-server
    or CDP poller to validate an owned mobile_result boundary.
    """
    tid = str(task_id or "")
    thread_value = str(thread_id or "")
    current_turn = str(current_turn_id or "")
    if not tid or not thread_value or not current_turn:
        return {}
    current_key = (
        current_turn,
        str(current_client_message_id or ""),
        tuple(str(item) for item in expected_task_ids if str(item)),
        tuple(sorted((expected_result_codes or {}).items())),
        tuple(sorted((expected_ack_codes or {}).items())),
    )
    seen_poll_keys: set[tuple[Any, ...]] = {current_key}
    attempts = recent_codex_turn_protocol_attempts(queue, tid, expected_task_ids, limit=8)
    for attempt in attempts:
        candidate_turns = [current_turn]
        attempt_turn = str(attempt.get("turn_id") or "")
        if attempt_turn and attempt_turn != current_turn:
            candidate_turns.append(attempt_turn)
        for candidate_turn in candidate_turns:
            attempt_expected = [str(item) for item in attempt.get("expected_task_ids") or [] if str(item)]
            attempt_result_codes = {
                str(key): str(value)
                for key, value in (attempt.get("expected_result_codes") or {}).items()
                if str(key) and str(value)
            }
            attempt_ack_codes = {
                str(key): str(value)
                for key, value in (attempt.get("expected_ack_codes") or {}).items()
                if str(key) and str(value)
            }
            poll_key = (
                candidate_turn,
                str(attempt.get("client_message_id") or ""),
                tuple(attempt_expected),
                tuple(sorted(attempt_result_codes.items())),
                tuple(sorted(attempt_ack_codes.items())),
            )
            if poll_key in seen_poll_keys:
                continue
            seen_poll_keys.add(poll_key)
            poll = poll_codex_result(
                poll_config,
                thread_value,
                candidate_turn,
                "",
                str(attempt.get("client_message_id") or ""),
                attempt_expected,
                attempt_result_codes,
                attempt_ack_codes,
            )
            new_text = strip_mobile_result_markers(str(poll.get("newText") or "").strip())
            if not is_usable_owned_result_text(new_text):
                new_text = ""
            ownership = poll.get("ownership") if isinstance(poll.get("ownership"), dict) else {}
            owned_complete = bool(poll.get("result_complete")) or bool(ownership.get("result_complete"))
            if not new_text or poll_has_ownership_mismatch(poll) or not owned_complete:
                continue
            recovered = dict(poll)
            recovered["historical_attempt_fallback"] = True
            recovered["historical_attempt"] = {
                "event_id": attempt.get("event_id"),
                "event_created_at": attempt.get("event_created_at"),
                "turn_id": attempt_turn,
                "polled_turn_id": candidate_turn,
                "client_message_id": attempt.get("client_message_id"),
                "expected_task_ids": attempt_expected,
                "expected_result_codes": attempt_result_codes,
                "expected_ack_codes": attempt_ack_codes,
            }
            return recovered
    return {}


def recover_owned_result_from_history_sources(
    queue: MobileQueue,
    config: dict[str, Any],
    poll_config: dict[str, Any],
    task_id: str,
    thread_id: str,
    turn_id: str,
    client_message_id: str,
    expected_task_ids: list[str],
    expected_result_codes: dict[str, str],
    expected_ack_codes: dict[str, str],
    current_poll: dict[str, Any],
) -> tuple[dict[str, Any], str, bool]:
    """Return the strongest exact owned result available from durable sources."""
    new_text = strip_mobile_result_markers(str(current_poll.get("newText") or "").strip())
    if not is_usable_owned_result_text(new_text):
        new_text = ""
    ownership = current_poll.get("ownership") if isinstance(current_poll.get("ownership"), dict) else {}
    owned_complete = bool(current_poll.get("result_complete")) or bool(ownership.get("result_complete"))
    if new_text and owned_complete:
        clear_session_owned_result_manual_review(queue, task_id)
        return current_poll, new_text, True
    if poll_has_mcp_transport_closed(current_poll):
        return current_poll, new_text, owned_complete

    candidates: list[dict[str, Any]] = []
    historical_poll = poll_historical_owned_codex_result(
        queue,
        poll_config,
        task_id,
        thread_id,
        turn_id,
        client_message_id,
        expected_task_ids,
        expected_result_codes,
        expected_ack_codes,
    )
    if historical_poll:
        candidates.append(historical_poll)
    thread_history_poll = poll_codex_thread_history_owned_result(
        config,
        thread_id,
        turn_id,
        client_message_id,
        expected_task_ids,
        expected_result_codes,
        expected_ack_codes,
    )
    if thread_history_poll:
        candidates.append(thread_history_poll)

    for candidate in candidates:
        candidate_text = strip_mobile_result_markers(str(candidate.get("newText") or "").strip())
        if not is_usable_owned_result_text(candidate_text):
            candidate_text = ""
        candidate_ownership = candidate.get("ownership") if isinstance(candidate.get("ownership"), dict) else {}
        candidate_complete = bool(candidate.get("result_complete")) or bool(candidate_ownership.get("result_complete"))
        if candidate_text and candidate_complete and not poll_has_ownership_mismatch(candidate):
            recovered = dict(candidate)
            recovered.setdefault("durable_history_recovery", True)
            clear_session_owned_result_manual_review(queue, task_id)
            return recovered, candidate_text, True

    history_failure = json.dumps(thread_history_poll, ensure_ascii=False).lower() if thread_history_poll else ""
    should_poll_session_store = bool(owned_complete and not new_text) or any(
        marker in history_failure
        for marker in ("no rollout found", "notfound", "thread_unreadable", "thread history unavailable")
    )
    result_code = str(expected_result_codes.get(str(task_id or "")) or "").strip()
    if result_code and should_poll_session_store:
        if session_owned_result_negative_cached(queue, str(task_id or ""), result_code):
            return current_poll, new_text, owned_complete
        task = queue.get_task(str(task_id or "")) or {}
        session_poll = find_codex_session_owned_result(
            str(task_id or ""),
            result_code,
            str(expected_ack_codes.get(str(task_id or "")) or ""),
            created_at=str(task.get("created_at") or ""),
            expected_turn_id=turn_id,
        )
        session_text = strip_mobile_result_markers(str(session_poll.get("newText") or "").strip())
        if not is_usable_owned_result_text(session_text):
            session_text = ""
        if session_text and bool(session_poll.get("result_complete")) and not poll_has_ownership_mismatch(session_poll):
            queue.runtime_delete(session_owned_result_negative_key(str(task_id or ""), result_code))
            recovered = dict(session_poll)
            recovered["ack_seen"] = bool(recovered.get("ack_seen")) or poll_has_mobile_ack(current_poll)
            clear_session_owned_result_manual_review(queue, task_id)
            return recovered, session_text, True
        if str(session_poll.get("reason") or "") == "owned_result_not_found":
            mark_session_owned_result_negative(queue, config, str(task_id or ""), result_code)
        elif str(session_poll.get("reason") or "") == "ambiguous_owned_results":
            mark_session_owned_result_manual_review(queue, str(task_id or ""), session_poll)
            blocked = dict(current_poll)
            blocked["session_store_recovery_blocked"] = True
            blocked["session_store_recovery"] = session_poll
            return blocked, new_text, owned_complete
        elif not bool(session_poll.get("ok")):
            blocked = dict(current_poll)
            blocked["session_store_recovery_blocked"] = True
            blocked["session_store_recovery"] = session_poll
            return blocked, new_text, owned_complete
    return current_poll, new_text, owned_complete


def try_complete_owned_result_before_redelivery(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    reason: str,
    detail: dict[str, Any] | None = None,
    trigger_task_id: str = "",
) -> dict[str, Any]:
    """Complete an active owner if a valid owned result already exists.

    This is a narrow pre-redelivery gate. It must not infer success from
    partial visible text; it only completes on an owned mobile_result boundary
    from the current or a durable historical attempt for the same task.
    """
    tid = str(task.get("id") or "")
    if not tid:
        return {"ok": False, "completed": False, "defer_redelivery": False, "reason": "task id is required"}
    current_task = queue.get_task(tid) or {}
    current_status = str(current_task.get("status") or "")
    if current_status not in {"sent_to_codex", "processing"}:
        return {
            "ok": True,
            "completed": False,
            "defer_redelivery": False,
            "reason": "task_not_active_owner",
            "status": current_status,
        }
    if task_is_supplement_context(queue, tid) and not task_owns_final_reply(queue, tid):
        return {
            "ok": True,
            "completed": False,
            "defer_redelivery": False,
            "reason": "supplement_member_not_final_reply_owner",
        }

    task = current_task
    delivery_mode = delivery_mode_for_task(config, task)
    poll_config = task_delivery_config(config, delivery_mode)
    health_result = check_codex_health(poll_config)
    if not health_result.get("healthy"):
        return {
            "ok": True,
            "completed": False,
            "defer_redelivery": True,
            "reason": "codex_health_unavailable",
            "health": health_result,
        }

    turn_id = str(queue.runtime_get(task_turn_key(tid)) or "")
    client_message_id, expected_task_ids = task_batch_runtime(queue, tid, [tid])
    if not turn_id:
        rehydrated = rehydrate_codex_turn_runtime_from_event(queue, tid)
        if rehydrated.get("ok"):
            turn_id = str(rehydrated.get("turn_id") or "")
            client_message_id, expected_task_ids = task_batch_runtime(queue, tid, [tid])
        else:
            return {
                "ok": True,
                "completed": False,
                "defer_redelivery": False,
                "reason": "missing_turn_runtime",
                "rehydrate": rehydrated,
            }
    expected_result_codes = task_result_code_runtime(queue, expected_task_ids)
    expected_ack_codes = task_ack_code_runtime(queue, expected_task_ids)
    poll = poll_codex_result(
        poll_config,
        str(task.get("codex_thread_id") or ""),
        turn_id,
        "",
        client_message_id,
        expected_task_ids,
        expected_result_codes,
        expected_ack_codes,
    )
    poll, new_text, owned_complete = recover_owned_result_from_history_sources(
        queue,
        config,
        poll_config,
        tid,
        str(task.get("codex_thread_id") or ""),
        turn_id,
        client_message_id,
        expected_task_ids,
        expected_result_codes,
        expected_ack_codes,
        poll,
    )

    if new_text and owned_complete and not poll_has_ownership_mismatch(poll):
        queue.complete(tid, new_text, status="done")
        silence_key = "silence:" + str(task.get("external_user") or "") + ":" + tid
        queue.runtime_delete(silence_key)
        clear_waiting_followup_redelivery_state(
            queue,
            tid,
            "owned_result_found_before_redelivery",
            {"poll": poll, "trigger_task_id": trigger_task_id, "reason": reason},
        )
        completed_members = complete_delivery_group_members(
            queue,
            tid,
            delivery_group_member_ids(queue, tid),
            new_text,
            str(task.get("codex_thread_id") or ""),
        )
        clear_task_codex_runtime(queue, tid)
        reply = push_final_reply_async(queue, task, new_text, config)
        result = {
            "ok": True,
            "completed": True,
            "defer_redelivery": False,
            "task_id": tid,
            "trigger_task_id": trigger_task_id,
            "reason": "owned_result_found_before_redelivery",
            "original_redelivery_reason": reason,
            "poll": poll,
            "reply": reply,
            "completed_group_members": completed_members,
        }
        queue.add_event("local", "pre_redelivery_owned_result_completed", result, tid)
        return result

    if task_has_completed_final_reply_evidence(queue, tid, task):
        result = {
            "ok": True,
            "completed": True,
            "defer_redelivery": False,
            "task_id": tid,
            "trigger_task_id": trigger_task_id,
            "reason": "completed_final_reply_evidence_found_before_redelivery",
            "original_redelivery_reason": reason,
            "poll": poll,
            "detail": detail or {},
        }
        clear_waiting_followup_redelivery_state(
            queue,
            tid,
            "completed_final_reply_evidence_found_before_redelivery",
            {"poll": poll, "trigger_task_id": trigger_task_id, "reason": reason},
        )
        queue.add_event("local", "pre_redelivery_completed_reply_evidence_consumed", result, tid)
        return result

    ack_without_progress = bool(
        poll_has_mobile_ack(poll)
        and not owned_complete
        and not poll_generation_is_active(poll)
        and not poll_status_is_in_progress(poll)
        and not str(poll.get("newText") or "").strip()
    )
    ack_only_terminal = bool(poll_is_base_ack_only_terminal(poll) or ack_without_progress)
    if ack_only_terminal and task_event_exists(queue, tid, "pre_redelivery_base_ack_only_terminal"):
        failed = fail_waiting_followup_redelivery_manual_required(
            queue,
            config,
            task,
            "base_ack_only_terminal_redelivery_already_attempted",
            {
                "original_redelivery_reason": reason,
                "trigger_task_id": trigger_task_id,
                "poll": poll,
                "detail": detail or {},
                "policy": "fail closed after one controlled base redelivery attempt without owned mobile_result",
            },
        )
        return {
            "ok": True,
            "completed": False,
            "defer_redelivery": True,
            "manual_required": True,
            "reason": "base_ack_only_terminal_redelivery_already_attempted",
            "original_redelivery_reason": reason,
            "trigger_task_id": trigger_task_id,
            "poll": poll,
            "failure": failed,
        }
    defer = bool(
        poll_generation_is_active(poll)
        or poll_status_is_in_progress(poll)
        or (poll_has_mobile_ack(poll) and not ack_only_terminal)
    )
    result = {
        "ok": True,
        "completed": False,
        "defer_redelivery": defer,
        "reason": "base_ack_only_terminal_without_result" if ack_only_terminal else "owned_result_not_complete",
        "original_redelivery_reason": reason,
        "trigger_task_id": trigger_task_id,
        "poll_status": str(poll.get("status") or ""),
        "generation_active": bool(poll_generation_is_active(poll)),
        "ack_seen": bool(poll_has_mobile_ack(poll)),
        "ack_only_terminal": ack_only_terminal,
        "result_complete": bool(owned_complete),
        "ownership_mismatch": bool(poll_has_ownership_mismatch(poll)),
        "poll": poll,
        "detail": detail or {},
    }
    if defer:
        queue.add_event("local", "pre_redelivery_owned_result_deferred", result, tid)
    elif ack_only_terminal:
        queue.add_event("local", "pre_redelivery_base_ack_only_terminal", result, tid)
    return result


def clear_task_codex_runtime(queue: MobileQueue, task_id: str) -> None:
    queue.runtime_delete(task_turn_key(task_id))
    queue.runtime_delete(task_batch_key(task_id))
    queue.runtime_delete(task_expected_ids_key(task_id))
    queue.runtime_delete(task_ack_code_key(task_id))
    queue.runtime_delete(task_result_code_key(task_id))
    queue.runtime_delete(delivery_group_members_key(task_id))


def repair_continuation_attempted(queue: MobileQueue, task_id: str) -> bool:
    return bool(
        task_id
        and (
            task_event_exists(queue, task_id, "app_server_repair_continuation_started")
            or task_event_exists(queue, task_id, "app_server_repair_continuation_failed")
            or task_event_exists(queue, task_id, "app_server_repair_continuation_manual_required")
        )
    )


def build_app_server_repair_continuation_prompt(
    task: dict[str, Any],
    expected_task_ids: list[str],
    expected_result_codes: dict[str, str],
    expected_ack_codes: dict[str, str],
    reason: str,
    poll: dict[str, Any],
) -> str:
    task_id = str(task.get("id") or "")
    expected_ids = [str(item) for item in expected_task_ids if str(item)]
    if not expected_ids and task_id:
        expected_ids = [task_id]
    lines = [
        "<codex_delegation>",
        "  <source>mobile-openclaw-bridge-repair</source>",
        "  <input>",
        "The previous mobile-owned Codex turn was interrupted by bridge repair because it did not produce an owned final result.",
        "Continue the original task to a final Weixin reply. First inspect the prior context and any completed local work in this thread.",
        "Do not repeat irreversible or externally visible side effects such as installs, downloads, Weixin sends, GUI sends, file mutations, or purchases unless the prior attempt clearly did not perform them.",
        "If the state cannot be verified safely, return a concise failure/blocking result instead of retrying the side effect.",
        "You must reuse the original mobile result boundary below; do not invent a new task id or marker.",
        "Do not output mobile_ack. This repair is only for the final Weixin result.",
        "Do not output any old mobile protocol marker from prior attempts.",
        "Return only the final Weixin reply text between the exact result_begin/result_end markers.",
        f"repair_reason={reason}",
        f"original_task_id={task_id}",
        f"expected_task_ids={','.join(expected_ids)}",
    ]
    for tid in expected_ids:
        result_code = str(expected_result_codes.get(tid) or "")
        if result_code:
            lines.append(f"result_begin_{tid}=[[mobile_result_begin:{tid}:{result_code}]]")
            lines.append(f"result_end_{tid}=[[mobile_result_end:{tid}:{result_code}]]")
    lines.append("Original mobile task text:")
    lines.append(str(task.get("text") or "").strip())
    status = str((poll or {}).get("status") or "")
    if status:
        lines.append(f"previous_turn_status={status}")
    lines.extend(["  </input>", "</codex_delegation>"])
    return "\n".join(lines)


def start_app_server_repair_continuation(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    reason: str,
    poll: dict[str, Any],
    turn_id: str,
    client_message_id: str,
    expected_task_ids: list[str],
    expected_result_codes: dict[str, str],
    expected_ack_codes: dict[str, str],
) -> dict[str, Any]:
    """Interrupt one stale app-server turn, then submit one continuation with the original result codes."""
    tid = str(task.get("id") or "")
    thread_id = str(task.get("codex_thread_id") or "")
    if not tid or not thread_id or not turn_id:
        return {"ok": False, "continued": False, "reason": "task_id_thread_id_and_turn_id_required"}
    if task_has_attachments(task):
        return {"ok": True, "continued": False, "skipped": True, "reason": "attachment_task_requires_manual_recovery"}
    if repair_continuation_attempted(queue, tid):
        return {"ok": True, "continued": False, "skipped": True, "reason": "repair_continuation_already_attempted"}
    expected_ids = [str(item) for item in expected_task_ids if str(item)]
    if not expected_ids:
        expected_ids = [tid]
    if tid not in expected_ids:
        return {"ok": False, "continued": False, "reason": "task_is_not_result_owner"}
    result_codes = {str(k): str(v) for k, v in (expected_result_codes or {}).items() if str(k) and str(v)}
    if not any(result_codes.get(item) for item in expected_ids):
        return {"ok": False, "continued": False, "reason": "missing_original_result_code"}

    old_turn_active = poll_generation_is_active(poll) or poll_status_is_in_progress(poll)
    if old_turn_active:
        cancel_result = cancel_codex_generation(
            task_delivery_config(config, "codex-app-server"),
            thread_id,
            turn_id,
        )
        if not (bool(cancel_result.get("ok")) or bool(cancel_result.get("cancelled"))):
            queue.add_event(
                "local",
                "app_server_repair_continuation_cancel_failed",
                {
                    "reason": reason,
                    "turn_id": turn_id,
                    "client_message_id": client_message_id,
                    "cancel_result": cancel_result,
                    "policy": "do not send continuation while old turn may still be running",
                },
                tid,
            )
            return {"ok": False, "continued": False, "reason": "cancel_failed", "cancel_result": cancel_result}
    else:
        cancel_result = {
            "ok": True,
            "cancelled": False,
            "skipped": True,
            "reason": "old_turn_not_active",
            "status": str((poll or {}).get("status") or ""),
            "terminal": poll_status_is_terminal(poll),
            "policy": "terminal/no-progress app-server turns do not require interrupt before a single repair continuation",
        }

    repair_batch_id = f"{client_message_id or 'mobile-openclaw'}-repair-{int(time.time())}"
    prompt = build_app_server_repair_continuation_prompt(
        task,
        expected_ids,
        result_codes,
        expected_ack_codes,
        reason,
        poll,
    )
    args = [
        "--dispatch",
        "--thread-id",
        thread_id,
        "--cwd",
        str(PROJECT_ROOT),
        "--client-message-id",
        repair_batch_id,
        "--expected-task-ids",
        ",".join(expected_ids),
        "--expected-result-codes",
        ",".join(f"{item}={result_codes[item]}" for item in expected_ids if result_codes.get(item)),
        "--materialization-wait-ms",
        str(app_server_turn_materialization_grace_seconds(config) * 1000),
    ]
    ack_codes = ",".join(f"{item}={expected_ack_codes[item]}" for item in expected_ids if expected_ack_codes.get(item))
    if ack_codes:
        args.extend(["--expected-ack-codes", ack_codes])
    delivery = run_codex_app_server_client(
        task_delivery_config(config, "codex-app-server"),
        args,
        prompt=prompt,
        timeout_extra_seconds=5,
    )
    if not delivery.get("ok") or not str(delivery.get("turn_id") or ""):
        queue.add_event(
            "local",
            "app_server_repair_continuation_failed",
            {
                "reason": reason,
                "cancel_result": cancel_result,
                "delivery": delivery,
                "policy": "single continuation attempt failed; do not try alternate prompts or queue-disordering retries",
            },
            tid,
        )
        return {"ok": False, "continued": False, "reason": "continuation_dispatch_failed", "cancel_result": cancel_result, "delivery": delivery}

    new_turn_id = str(delivery.get("turn_id") or "")
    queue.runtime_set(task_turn_key(tid), new_turn_id)
    queue.runtime_set(task_batch_key(tid), repair_batch_id)
    queue.runtime_set(task_expected_ids_key(tid), json.dumps(expected_ids, ensure_ascii=False))
    for expected_id in expected_ids:
        if expected_ack_codes.get(expected_id):
            queue.runtime_set(task_ack_code_key(expected_id), str(expected_ack_codes[expected_id]))
        if result_codes.get(expected_id):
            queue.runtime_set(task_result_code_key(expected_id), str(result_codes[expected_id]))
    with queue.session() as db:
        db.execute(
            "UPDATE mobile_tasks SET sent_to_codex_at=?, updated_at=? WHERE id=? AND status IN ('sent_to_codex','processing')",
            (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), tid),
        )
    queue.add_event(
        "local",
        "app_server_repair_continuation_started",
        {
            "reason": reason,
            "old_turn_id": turn_id,
            "new_turn_id": new_turn_id,
            "old_client_message_id": client_message_id,
            "client_message_id": repair_batch_id,
            "expected_task_ids": expected_ids,
            "cancel_result": cancel_result,
            "delivery": delivery,
            "policy": "single repair continuation after confirmed interrupt; preserve original result markers and avoid duplicate side effects",
        },
        tid,
    )
    mark_active_recovery_cooldown(queue, config, tid, datetime.now(timezone.utc), "repair_continuation_started")
    return {"ok": True, "continued": True, "turn_id": new_turn_id, "client_message_id": repair_batch_id, "cancel_result": cancel_result, "delivery": delivery}


def task_context_token_key(task_id: str) -> str:
    return f"weixin_context_token:{task_id}"


def pending_reply_context_retry_key(task_id: str) -> str:
    return f"pending_reply_context_retry:{task_id}"


def pending_reply_context_global_retry_key() -> str:
    return "pending_reply_context_retry:global"


def pending_reply_context_last_token_key(task_id: str) -> str:
    return f"pending_reply_context_last_token:{task_id}"


def reply_sending_runtime_key(task_id: str) -> str:
    return f"reply_sending:{task_id}"


def _parse_reply_sending_runtime_entry(raw: str) -> dict[str, str]:
    value = str(raw or "").strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return {
            "started_at": str(parsed.get("started_at") or "").strip(),
            "expires_at": str(parsed.get("expires_at") or "").strip(),
        }
    return {"started_at": value, "expires_at": ""}


def _reply_sending_entry_expired(entry: dict[str, str], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    expires_at = parse_iso_datetime(str(entry.get("expires_at") or ""))
    if expires_at:
        return now >= expires_at
    started_at = parse_iso_datetime(str(entry.get("started_at") or ""))
    if started_at:
        return now >= started_at + timedelta(seconds=REPLY_SENDING_LEASE_SECONDS)
    return True


def set_task_context_token(queue: MobileQueue, task_id: str, context_token: str) -> None:
    if task_id and context_token:
        queue.runtime_set(task_context_token_key(task_id), context_token)


def set_pending_reply_context_last_token(queue: MobileQueue, task_id: str, context_token: str) -> None:
    if task_id and context_token:
        queue.runtime_set(pending_reply_context_last_token_key(task_id), context_token)


def task_is_reply_sending(queue: MobileQueue, task_id: str) -> bool:
    raw = str(queue.runtime_get(reply_sending_runtime_key(task_id)) or "").strip()
    if not raw:
        return False
    entry = _parse_reply_sending_runtime_entry(raw)
    if not entry:
        queue.runtime_delete(reply_sending_runtime_key(task_id))
        return False
    if _reply_sending_entry_expired(entry):
        queue.runtime_delete(reply_sending_runtime_key(task_id))
        return False
    return True


def mark_task_reply_sending(queue: MobileQueue, task_id: str) -> None:
    if task_id:
        now = datetime.now(timezone.utc)
        queue.runtime_set(
            reply_sending_runtime_key(task_id),
            json.dumps(
                {
                    "started_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=REPLY_SENDING_LEASE_SECONDS)).isoformat(),
                },
                ensure_ascii=False,
            ),
        )


def reserve_task_reply_send(queue: MobileQueue, task_id: str, text: str = "", media: str = "") -> dict[str, Any]:
    if not task_id:
        return {"reserved": False, "reason": "task id is required"}
    return runtime_acquire_lease(
        queue,
        reply_sending_runtime_key(task_id),
        {
            "task_id": task_id,
            "text_sha256": hashlib.sha256(str(text or "").encode("utf-8")).hexdigest(),
            "media": str(media or ""),
        },
        REPLY_SENDING_LEASE_SECONDS,
    )


def owned_result_consume_key(task_id: str) -> str:
    return f"owned_result_consuming:{str(task_id or '').strip()}"


def session_owned_result_negative_key(task_id: str, result_code: str) -> str:
    return f"session_owned_result_negative:{str(task_id or '').strip()}:{str(result_code or '').strip()}"


def session_owned_result_manual_review_key(task_id: str) -> str:
    return f"session_owned_result_manual_review:{str(task_id or '').strip()}"


def session_owned_result_manual_review_payload(recovery: dict[str, Any]) -> dict[str, Any]:
    """Persist bounded conflict facts only; never retain recovered result text."""
    candidate_hashes = sorted(
        {
            value.lower()
            for value in recovery.get("candidate_hashes", [])
            if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value)
        }
    )[:16]
    candidate_count = recovery.get("candidate_count")
    try:
        candidate_count = max(len(candidate_hashes), min(int(candidate_count), 16))
    except (TypeError, ValueError):
        candidate_count = len(candidate_hashes)
    return {
        "reason": "ambiguous_owned_results",
        "candidate_hashes": candidate_hashes,
        "candidate_count": candidate_count,
        "search_mode": str(recovery.get("search_mode") or "")[:64],
    }


def mark_session_owned_result_manual_review(queue: MobileQueue, task_id: str, recovery: dict[str, Any]) -> bool:
    """Record one durable review marker for conflicting exact session results.

    The active task remains untouched: this marker prevents a conflict from
    being silently hidden while the normal polling path may still find one
    later, unambiguous owned result.
    """
    tid = str(task_id or "").strip()
    if not tid:
        return False
    key = session_owned_result_manual_review_key(tid)
    payload = session_owned_result_manual_review_payload(recovery)
    existing: dict[str, Any] = {}
    raw = str(queue.runtime_get(key) or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            existing = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    stable_fields = ("reason", "candidate_hashes", "candidate_count", "search_mode")
    if existing and all(existing.get(field) == payload.get(field) for field in stable_fields):
        return False
    payload["recorded_at"] = datetime.now(timezone.utc).isoformat()
    queue.runtime_set(key, json.dumps(payload, ensure_ascii=False, sort_keys=True))
    queue.add_event(
        "local",
        "session_store_owned_result_manual_review_required",
        {
            **payload,
            "policy": "conflicting exact session-owned results require review; do not redeliver or send from the conflict",
        },
        tid,
    )
    return True


def clear_session_owned_result_manual_review(queue: MobileQueue, task_id: str) -> None:
    tid = str(task_id or "").strip()
    if tid:
        queue.runtime_delete(session_owned_result_manual_review_key(tid))


def session_owned_result_negative_cache_seconds(config: dict[str, Any]) -> int:
    value = config.get("trigger", {}).get("session_owned_result_negative_cache_seconds")
    try:
        return max(5, min(int(value if value is not None else DEFAULT_SESSION_OWNED_RESULT_NEGATIVE_CACHE_SECONDS), 300))
    except (TypeError, ValueError):
        return DEFAULT_SESSION_OWNED_RESULT_NEGATIVE_CACHE_SECONDS


def session_owned_result_negative_cached(queue: MobileQueue, task_id: str, result_code: str) -> bool:
    key = session_owned_result_negative_key(task_id, result_code)
    raw = str(queue.runtime_get(key) or "").strip()
    if not raw:
        return False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        queue.runtime_delete(key)
        return False
    expires_at = parse_iso_datetime(str(payload.get("expires_at") or "")) if isinstance(payload, dict) else None
    if not expires_at or datetime.now(timezone.utc) >= expires_at:
        queue.runtime_delete(key)
        return False
    return True


def mark_session_owned_result_negative(queue: MobileQueue, config: dict[str, Any], task_id: str, result_code: str) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=session_owned_result_negative_cache_seconds(config))
    queue.runtime_set(
        session_owned_result_negative_key(task_id, result_code),
        json.dumps({"expires_at": expires_at.isoformat()}, ensure_ascii=False),
    )


def reserve_owned_result_consume(queue: MobileQueue, task_id: str, poll: dict[str, Any]) -> dict[str, Any]:
    if not task_id:
        return {"reserved": False, "reason": "task id is required"}
    ownership = poll.get("ownership") if isinstance(poll.get("ownership"), dict) else {}
    return runtime_acquire_lease(
        queue,
        owned_result_consume_key(task_id),
        {
            "task_id": task_id,
            "turn_id": str(poll.get("matched_turn_id") or poll.get("turn_id") or ""),
            "matched_task_id": str(ownership.get("matched_task_id") or ""),
            "matched_result_code": str(ownership.get("matched_result_code") or ""),
            "mode": str(poll.get("mode") or ""),
        },
        REPLY_SENDING_LEASE_SECONDS,
    )


def clear_task_reply_sending(queue: MobileQueue, task_id: str) -> None:
    if task_id:
        queue.runtime_delete(reply_sending_runtime_key(task_id))


def recover_stale_reply_sending_tasks(queue: MobileQueue, limit: int = 20) -> dict[str, Any]:
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, source, external_user, external_conversation, command,
                   risk_level, status, text, result, error, push_status,
                   receiver_account_id, metadata_json, created_at, updated_at
            FROM mobile_tasks
            WHERE push_status='reply_sending'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()

    recovered: list[str] = []
    skipped: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        task = dict(row)
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        if task_is_reply_sending(queue, task_id):
            skipped.append(task_id)
            continue
        if task_event_recent(queue, task_id, "final_reply_weixin_accepted", 300):
            queue.runtime_delete(reply_pending_batch_notice_key(task_id))
            continue
        detail = {
            "ok": False,
            "recoverable": True,
            "reason": "stale_reply_sending_recovered",
            "task_id": task_id,
            "next_step": "retry final reply after fresh context token",
        }
        with queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET push_status='reply_pending', updated_at=?
                WHERE id=? AND push_status='reply_sending'
                """,
                (now, task_id),
            )
        queue.mark_reply_pending(task_id, json.dumps(detail, ensure_ascii=False))
        queue.add_event("wecom", "stale_reply_sending_recovered", detail, task_id)
        recovered.append(task_id)
    return {"ok": True, "recovered": recovered, "recovered_count": len(recovered), "skipped": skipped}


def reply_pending_retry_cooldown_seconds(config: dict[str, Any]) -> int:
    return max(5, int(config.get("openclaw", {}).get("reply_pending_context_retry_seconds") or 5))


def reply_pending_retry_limit_per_cycle(config: dict[str, Any]) -> int:
    return max(0, int(config.get("openclaw", {}).get("reply_pending_context_retry_limit_per_cycle") or 1))


def pending_reply_retry_due(queue: MobileQueue, task_id: str, now: datetime) -> bool:
    raw = str(queue.runtime_get(pending_reply_context_retry_key(task_id)) or "")
    if not raw:
        return True
    retry_after = parse_iso_datetime(raw)
    return not retry_after or now >= retry_after


def pending_reply_global_retry_due(queue: MobileQueue, now: datetime) -> bool:
    raw = str(queue.runtime_get(pending_reply_context_global_retry_key()) or "")
    if not raw:
        return True
    retry_after = parse_iso_datetime(raw)
    return not retry_after or now >= retry_after


def mark_pending_reply_retry_cooldown(
    queue: MobileQueue,
    task_id: str,
    now: datetime,
    cooldown_seconds: int,
) -> str:
    retry_after = (now + timedelta(seconds=max(5, int(cooldown_seconds)))).isoformat()
    queue.runtime_set(pending_reply_context_retry_key(task_id), retry_after)
    return retry_after


def mark_pending_reply_global_retry_cooldown(
    queue: MobileQueue,
    now: datetime,
    cooldown_seconds: int,
) -> str:
    retry_after = (now + timedelta(seconds=max(5, int(cooldown_seconds)))).isoformat()
    queue.runtime_set(pending_reply_context_global_retry_key(), retry_after)
    return retry_after


def pending_reply_account_retry_due(queue: MobileQueue, account_id: str, now: datetime) -> bool:
    raw = str(queue.runtime_get(f"pending_reply_context_retry:account:{account_id}") or "")
    if not raw:
        return True
    retry_after = parse_iso_datetime(raw)
    return not retry_after or now >= retry_after


def mark_pending_reply_account_retry_cooldown(
    queue: MobileQueue,
    account_id: str,
    now: datetime,
    cooldown_seconds: int,
) -> str:
    retry_after = (now + timedelta(seconds=max(5, int(cooldown_seconds)))).isoformat()
    queue.runtime_set(f"pending_reply_context_retry:account:{account_id}", retry_after)
    return retry_after


def schedule_waiting_context_replies(
    queue: MobileQueue,
    config: dict[str, Any],
    external_user: str,
    account_id: str,
    context_token: str,
    triggering_task_id: str,
    limit: int = 3,
) -> dict[str, Any]:
    if not external_user or not account_id or not context_token:
        return {"ok": True, "scheduled": 0, "reason": "missing routing or context"}
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, source, external_user, external_conversation, command,
                   risk_level, status, text, result, error, push_status,
                   receiver_account_id, metadata_json, created_at, updated_at
            FROM mobile_tasks
            WHERE external_user=?
              AND receiver_account_id=?
              AND push_status IN ('reply_pending','push_failed')
              AND COALESCE(result, '') <> ''
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (external_user, account_id, max(1, int(limit))),
        ).fetchall()
    scheduled: list[str] = []
    visibility_unknown = 0
    for row in rows:
        task = dict(row)
        task_id = str(task.get("id") or "")
        if not task_id or not task_event_exists(queue, task_id, "final_reply_waiting_weixin_context"):
            continue
        if final_reply_waiting_source_reason(queue, task_id) == "phone_visible_not_confirmed":
            visibility_unknown += 1
        now = datetime.now(timezone.utc).isoformat()
        with queue.session() as db:
            changed = db.execute(
                """
                UPDATE mobile_tasks
                SET push_status='reply_retrying', updated_at=?
                WHERE id=? AND push_status IN ('reply_pending','push_failed')
                """,
                (now, task_id),
            ).rowcount
        if not changed:
            continue
        set_task_context_token(queue, task_id, context_token)
        queue.add_event(
            "wecom",
            "final_reply_context_retry_scheduled",
            {
                "triggering_task_id": triggering_task_id,
                "account_id": account_id,
                "external_user": external_user,
            },
            task_id,
        )
        push_final_reply_async(
            queue,
            task,
            str(task.get("result") or ""),
            config,
            media=waiting_context_media(queue, task_id),
        )
        scheduled.append(task_id)
    return {
        "ok": True,
        "scheduled": len(scheduled),
        "task_ids": scheduled,
        "visibility_unknown": visibility_unknown,
    }


def refresh_pending_reply_context_token(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
) -> str:
    account_id = receiver_account_id(
        config,
        str(task.get("receiver_account_id") or ""),
        str(task.get("external_user") or ""),
    )
    external_user = str(task.get("external_user") or "")
    token = openclaw_context_token_for_user(config, account_id, external_user)
    if token:
        set_task_context_token(queue, str(task.get("id") or ""), token)
        set_pending_reply_context_last_token(queue, str(task.get("id") or ""), token)
    return token


def process_pending_reply_context_retries(
    queue: MobileQueue,
    config: dict[str, Any],
    limit: int | None = None,
) -> dict[str, Any]:
    if limit is None:
        limit = reply_pending_retry_limit_per_cycle(config)
    limit = max(0, int(limit))
    if limit <= 0:
        return {"ok": True, "scheduled": 0, "skipped": 0, "reason": "disabled"}

    scan_limit = max(limit * 5, 10)
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, source, external_user, external_conversation, command,
                   risk_level, status, text, result, error, push_status,
                   receiver_account_id, metadata_json, created_at, updated_at
            FROM mobile_tasks
            WHERE push_status IN ('reply_pending', 'reply_retrying')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (scan_limit,),
        ).fetchall()

    now = datetime.now(timezone.utc)
    cooldown = reply_pending_retry_cooldown_seconds(config)
    scheduled: list[str] = []
    skipped = 0
    waiting_context = 0
    missing_context = 0
    visibility_unknown = 0
    for row in rows:
        if len(scheduled) >= limit:
            break
        task = dict(row)
        task_id = str(task.get("id") or "")
        if not task_id or not task_event_exists(queue, task_id, "final_reply_waiting_weixin_context"):
            skipped += 1
            continue
        source_reason = final_reply_waiting_source_reason(queue, task_id)
        if source_reason == "phone_visible_not_confirmed":
            visibility_unknown += 1
        if reply_pending_requires_fresh_inbound_context(source_reason):
            skipped += 1
            continue
        if str(task.get("push_status") or "") == "reply_retrying" or task_is_reply_sending(queue, task_id):
            skipped += 1
            continue
        media = waiting_context_media(queue, task_id)
        retry_text = str(task.get("result") or "").strip()
        if not retry_text and media:
            retry_text = f"附件：{Path(media).name}"
        if not retry_text and not media:
            skipped += 1
            continue
        waiting_context += 1
        if not pending_reply_retry_due(queue, task_id, now):
            skipped += 1
            continue
        account_id = receiver_account_id(
            config,
            str(task.get("receiver_account_id") or ""),
            str(task.get("external_user") or ""),
        )
        if not pending_reply_account_retry_due(queue, account_id, now):
            skipped += 1
            continue
        ack_circuit = get_weixin_status_ack_circuit(queue, account_id)
        if ack_circuit.get("active"):
            skipped += 1
            retry_after = mark_pending_reply_retry_cooldown(queue, task_id, now, cooldown)
            account_retry_after = mark_pending_reply_account_retry_cooldown(queue, account_id, now, cooldown)
            circuit_signature = hashlib.sha256(
                json.dumps(
                    {
                        "account_id": account_id,
                        "circuit_retry_after": ack_circuit.get("retry_after"),
                        "circuit_reason": ack_circuit.get("reason"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:16]
            event_key = f"reply_pending_status_ack_circuit_notice:{account_id}"
            if str(queue.runtime_get(event_key) or "") != circuit_signature:
                queue.runtime_set(event_key, circuit_signature)
                queue.add_event(
                    "wecom",
                    "final_reply_batch_notice_waiting_status_ack_circuit",
                    {
                        "account_id": account_id,
                        "retry_after": retry_after,
                        "account_retry_after": account_retry_after,
                        "cooldown_seconds": cooldown,
                        "circuit": ack_circuit,
                    },
                    task_id,
                )
            continue
        external_user = str(task.get("external_user") or "")
        context_token = refresh_pending_reply_context_token(queue, config, task)
        if not context_token:
            missing_context += 1
            retry_after = mark_pending_reply_retry_cooldown(queue, task_id, now, cooldown)
            account_retry_after = mark_pending_reply_account_retry_cooldown(queue, account_id, now, cooldown)
            queue.add_event(
                "wecom",
                "final_reply_context_retry_waiting_for_context",
                {
                    "account_id": account_id,
                    "external_user": external_user,
                    "retry_after": retry_after,
                    "account_retry_after": account_retry_after,
                    "cooldown_seconds": cooldown,
                },
                task_id,
            )
            continue
        if not reply_pending_batch_notice_sent(queue, task_id):
            batch_notice = send_reply_pending_batch_notice(queue, task, config)
            if not batch_notice.get("ok"):
                retry_after = mark_pending_reply_retry_cooldown(queue, task_id, now, cooldown)
                account_retry_after = mark_pending_reply_account_retry_cooldown(queue, account_id, now, cooldown)
                queue.add_event(
                    "wecom",
                    "final_reply_batch_notice_failed",
                    {
                        "account_id": account_id,
                        "external_user": external_user,
                        "retry_after": retry_after,
                        "account_retry_after": account_retry_after,
                        "cooldown_seconds": cooldown,
                        "batch_notice": batch_notice,
                    },
                    task_id,
                )
                continue
        retry_after = mark_pending_reply_retry_cooldown(queue, task_id, now, cooldown)
        account_retry_after = mark_pending_reply_account_retry_cooldown(queue, account_id, now, cooldown)
        set_task_context_token(queue, task_id, context_token)
        mark_task_reply_sending(queue, task_id)
        queue.add_event(
            "wecom",
            "final_reply_context_retry_scheduled",
            {
                "triggering_task_id": "",
                "account_id": account_id,
                "external_user": external_user,
                "retry_after": retry_after,
                "account_retry_after": account_retry_after,
                "cooldown_seconds": cooldown,
                "auto_retry": True,
            },
            task_id,
        )
        push_final_reply_async(
            queue,
            task,
            retry_text,
            config,
            media=media,
        )
        scheduled.append(task_id)
    return {
        "ok": True,
        "scheduled": len(scheduled),
        "task_ids": scheduled,
        "skipped": skipped,
        "waiting_context": waiting_context,
        "missing_context": missing_context,
        "visibility_unknown": visibility_unknown,
        "cooldown_seconds": cooldown,
    }


def reconcile_completed_replies_waiting_push(queue: MobileQueue, config: dict[str, Any], limit: int = 20) -> dict[str, Any]:
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, source, external_user, external_conversation, command,
                   risk_level, status, text, result, error, push_status,
                   receiver_account_id, metadata_json, created_at, updated_at
            FROM mobile_tasks
            WHERE status='done'
              AND COALESCE(result, '') <> ''
              AND pushed_at IS NULL
              AND COALESCE(push_status, '') = ''
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()

    reconciled: list[str] = []
    skipped: list[dict[str, str]] = []
    for row in rows:
        task = dict(row)
        task_id = str(task.get("id") or "")
        result_text = str(task.get("result") or "").strip()
        if not task_id:
            continue
        if result_text.startswith("[supplement]"):
            skipped.append({"task_id": task_id, "reason": "internal_supplement_result"})
            continue
        if not str(task.get("external_user") or "").strip() or not str(task.get("receiver_account_id") or "").strip():
            skipped.append({"task_id": task_id, "reason": "missing_reply_route"})
            continue
        detail = {
            "ok": False,
            "recoverable": True,
            "reason": "waiting_weixin_context",
            "source_reason": "reconciled_done_without_push",
            "account_id": receiver_account_id(
                config,
                str(task.get("receiver_account_id") or ""),
                str(task.get("external_user") or ""),
            ),
            "external_user": str(task.get("external_user") or ""),
            "next_step": "retry through reply_pending batch notice and context recovery",
        }
        queue.mark_reply_pending(task_id, json.dumps(detail, ensure_ascii=False))
        set_pending_reply_context_last_token(queue, task_id, str(task_context_token(task) or ""))
        queue.add_event("wecom", "final_reply_waiting_weixin_context", detail, task_id)
        queue.add_event("wecom", "completed_reply_reconciled_to_reply_pending", detail, task_id)
        reconciled.append(task_id)
    return {"ok": True, "reconciled": reconciled, "reconciled_count": len(reconciled), "skipped": skipped}


def waiting_context_media(queue: MobileQueue, task_id: str) -> str | None:
    if not task_id:
        return None
    with queue.session() as db:
        row = db.execute(
            """
            SELECT payload_json
            FROM mobile_events
            WHERE task_id=? AND event_type='final_reply_waiting_weixin_context'
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        return None
    media_info = payload.get("media_info") if isinstance(payload, dict) else {}
    media = str(media_info.get("media") or "") if isinstance(media_info, dict) else ""
    return media or None


def final_reply_waiting_source_reason(queue: MobileQueue, task_id: str) -> str:
    if not task_id:
        return ""
    with queue.session() as db:
        row = db.execute(
            """
            SELECT payload_json
            FROM mobile_events
            WHERE task_id=? AND event_type='final_reply_waiting_weixin_context'
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    if not row:
        return ""
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        return ""
    return str(payload.get("source_reason") or "") if isinstance(payload, dict) else ""


def reply_pending_requires_fresh_inbound_context(source_reason: str) -> bool:
    """Return True when retry must be triggered by a new inbound Weixin message.

    ret=-2 means the sender reached Weixin but the business layer rejected the
    reply in the current context. Reusing the same stored context token from the
    worker loop just repeats the rejection and can spam reply attempts. The
    enqueue path still calls schedule_waiting_context_replies() with the newest
    inbound context, so recovery remains automatic when the user sends a real
    follow-up.
    """
    return str(source_reason or "") in {"sendmessage_ret_-2", "weixin_send_circuit_open"}


def reply_pending_batch_notice_key(task_id: str) -> str:
    return f"reply_pending_batch_notice:{task_id}"


def reply_pending_batch_notice_sent(queue: MobileQueue, task_id: str) -> bool:
    return bool(queue.runtime_get(reply_pending_batch_notice_key(task_id)))


def mark_reply_pending_batch_notice_sent(queue: MobileQueue, task_id: str) -> None:
    if task_id:
        queue.runtime_set(reply_pending_batch_notice_key(task_id), datetime.now(timezone.utc).isoformat())


def build_reply_pending_batch_notice(tasks: list[dict[str, Any]]) -> str:
    return reply_status_text.reply_pending_batch_notice(tasks)


def pending_reply_batch_tasks(
    queue: MobileQueue,
    task: dict[str, Any],
) -> list[dict[str, Any]]:
    account_id = str(task.get("receiver_account_id") or "")
    external_user = str(task.get("external_user") or "")
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, source, external_user, command, risk_level, status, text,
                   receiver_account_id, attachments_json, result, error, push_status,
                   created_at, updated_at
            FROM mobile_tasks
            WHERE external_user=?
              AND receiver_account_id=?
              AND push_status='reply_pending'
            ORDER BY updated_at ASC, created_at ASC
            """,
            (external_user, account_id),
        ).fetchall()
    return [dict(row) for row in rows]


def _task_route_identity(task: dict[str, Any], thread_id: str, delivery_mode: str) -> tuple[str, str, str]:
    return (
        str(task.get("external_user") or ""),
        str(task.get("receiver_account_id") or ""),
        task_route_key(delivery_mode, thread_id),
    )


def pending_route_batch_tasks(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    thread_id: str,
    delivery_mode: str,
    pending: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return same-route pending tasks for owner dispatch plus MCP supplement backlog."""
    identity = _task_route_identity(task, thread_id, delivery_mode)
    seed_task_id = str(task.get("id") or "")
    merged: list[dict[str, Any]] = []
    for item in pending:
        item_thread_id = effective_task_thread_id(queue, config, item)
        if _task_route_identity(item, item_thread_id, delivery_mode) != identity:
            continue
        item_id = str(item.get("id") or "")
        if item_id == seed_task_id:
            if not task_can_join_supplement(item):
                continue
        else:
            if task_is_supplement_context(queue, item_id):
                continue
            if not task_can_be_same_turn_supplement(queue, item):
                continue
        if str(item.get("status") or "") != "pending":
            continue
        retry = get_delivery_retry(queue, str(item.get("id") or ""))
        if retry.get("active") and not delivery_retry_reason_allows_batch(str(retry.get("reason") or "")):
            continue
        merged.append(dict(item))
    merged.sort(key=lambda item: pending_task_order_key(queue, item))
    return merged


def pending_batch_notice_tasks(queue: MobileQueue, task_ids: list[str]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    with queue.session() as db:
        for task_id in task_ids:
            row = db.execute(
                """
                SELECT id, source, external_user, external_conversation, command,
                       risk_level, status, text, result, error, push_status,
                       receiver_account_id, metadata_json, created_at, updated_at
                FROM mobile_tasks
                WHERE id=?
                """,
                (task_id,),
            ).fetchone()
            if row:
                tasks.append(dict(row))
    tasks.sort(key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))
    return tasks


def dispatch_failure_scope(reason: str, delivery_mode: str) -> str:
    """Scope a failed delivery so one route does not stall all routes."""
    reason_value = str(reason or "").strip().lower()
    mode_value = str(delivery_mode or "").strip().lower()
    if mode_value == "codex-cdp":
        return "route"
    if reason_value in {"codex cdp is not ready", "codex_cdp_transport_not_ready"}:
        return "route"
    if "cdp" in reason_value:
        return "route"
    return "route"


def dispatch_attempt_budget(config: dict[str, Any]) -> int:
    trigger = config.get("trigger", {})
    return max(1, int(trigger.get("worker_dispatch_attempts_per_cycle") or 4))


def should_send_once(queue: MobileQueue, task_id: str, event_type: str) -> bool:
    return bool(task_id and not task_event_exists(queue, task_id, event_type))


SUPPLEMENT_TASK_EVENT_TYPES = {
    "continuation_deferred",
    "attachment_supplement_pending_published",
    "pending_backlog_supplement_pending_published",
    "delivery_group_member",
    "delivery_group_member_completed",
    "mcp_acked_supplement_completed",
}


SUPPLEMENT_ALLOWED_ACK_EVENTS = {
    "status_ack_received",
    "status_ack_continuation_deferred",
    "status_ack_attachment_supplement",
    "status_ack_pending_backlog_supplement",
    "status_ack_delivery_group_supplement",
}


SUPPLEMENT_RELEASE_EVENT_TYPES = {
    "delivery_group_member_released",
}


def task_is_promoted_supplement_owner(queue: MobileQueue, task_id: str) -> bool:
    """A formerly published supplement can become the next final-reply owner."""
    return bool(task_id and task_event_exists(queue, task_id, "supplement_promoted_to_owner"))


def task_is_supplement_context(queue: MobileQueue, task_id: str) -> bool:
    if not task_id:
        return False
    if task_is_promoted_supplement_owner(queue, task_id):
        return False
    if task_is_released_final_reply_owner(queue, task_id):
        return False
    _client_message_id, expected_task_ids = task_batch_runtime(queue, task_id)
    if task_id in expected_task_ids:
        return False
    if task_has_final_reply_owner_evidence(queue, task_id):
        return False
    member_event_id = latest_task_event_id(queue, task_id, "delivery_group_member")
    release_event_id = latest_task_event_id(queue, task_id, "delivery_group_member_released")
    if member_event_id and release_event_id and release_event_id > member_event_id:
        return False
    ack_payload = mcp_ack_payload(queue, task_id)
    if ack_payload and valid_mcp_ack_base_owner(queue, task_id, ack_payload)[0]:
        return True
    key, payload = bridge_supplement_payload_for_task(queue, task_id)
    if key and payload:
        return True
    latest_context_id = max(
        (latest_task_event_id(queue, task_id, event_type) for event_type in SUPPLEMENT_TASK_EVENT_TYPES),
        default=0,
    )
    if not latest_context_id:
        return False
    latest_release_id = max(
        (latest_task_event_id(queue, task_id, event_type) for event_type in SUPPLEMENT_RELEASE_EVENT_TYPES),
        default=0,
    )
    return latest_release_id <= latest_context_id


def task_has_final_reply_owner_evidence(queue: MobileQueue, task_id: str) -> bool:
    tid = str(task_id or "")
    if not tid:
        return False
    expected_raw = queue.runtime_get(task_expected_ids_key(tid))
    _client_message_id, expected_task_ids = task_batch_runtime(queue, tid)
    if tid in expected_task_ids:
        return True
    if expected_raw is not None and str(expected_raw or "").strip() and tid not in expected_task_ids:
        return False
    if task_event_exists(queue, tid, "delivery_group_member"):
        return False
    return bool(
        task_event_exists(queue, tid, "delivery_group_owner")
        or task_event_exists(queue, tid, "codex_turn_started")
    )


def task_has_completed_final_reply_evidence(
    queue: MobileQueue,
    task_id: str,
    task: dict[str, Any] | None = None,
) -> bool:
    """Return True only for durable evidence that this task already finished its final reply."""
    tid = str(task_id or "")
    if not tid:
        return False
    current_task = dict(task or queue.get_task(tid) or {})
    status = str(current_task.get("status") or "")
    push_status = str(current_task.get("push_status") or "")
    result_text = str(current_task.get("result") or "").strip()
    if status in {"done", "pushed_to_wecom"} and result_text:
        return True
    if status != "pushed_to_wecom" and push_status != "pushed_to_wecom":
        return False
    if task_event_exists(queue, tid, "final_reply_visibility_unconfirmed"):
        return task_event_exists(queue, tid, "push_result")
    return task_event_exists(queue, tid, "final_reply_weixin_accepted") or task_event_exists(queue, tid, "push_result")


def task_owns_final_reply(queue: MobileQueue, task_id: str) -> bool:
    """Return True only when this task owns an active/finished mobile result."""
    tid = str(task_id or "")
    if not tid:
        return False
    return task_has_final_reply_owner_evidence(queue, tid) and not task_is_supplement_context(queue, tid)


def valid_active_supplement_host(queue: MobileQueue, active_task: dict[str, Any]) -> bool:
    """A task can host MCP supplements only if it owns the final reply."""
    task_id = str(active_task.get("id") or "")
    status = str(active_task.get("status") or "")
    if status not in {"sent_to_codex", "processing"}:
        return False
    if not task_id:
        return False
    if task_is_supplement_context(queue, task_id):
        return False
    return task_owns_final_reply(queue, task_id)


def delivery_group_split(tasks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the owner task and later pending rows that must stay MCP supplements."""
    ordered = sorted(
        [dict(task) for task in tasks],
        key=lambda item: str(item.get("created_at") or item.get("updated_at") or item.get("id") or ""),
    )
    if len(ordered) <= 1:
        return ordered, []
    owner = ordered[0]
    return [owner], ordered[1:]


def delivery_group_task_ids(tasks: list[dict[str, Any]]) -> list[str]:
    return [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]


def delivery_group_signature(tasks: list[dict[str, Any]]) -> str:
    payload = [
        {
            "id": str(task.get("id") or ""),
            "text": str(task.get("text") or ""),
            "created_at": str(task.get("created_at") or ""),
            "attachments": task_attachments(task),
        }
        for task in tasks
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def mark_delivery_group_members(
    queue: MobileQueue,
    owner_tasks: list[dict[str, Any]],
    member_tasks: list[dict[str, Any]],
    thread_id: str,
    delivery_mode: str,
) -> dict[str, Any]:
    owner_ids = delivery_group_task_ids(owner_tasks)
    member_ids = delivery_group_task_ids(member_tasks)
    if not owner_ids or not member_ids:
        return {"ok": True, "member_count": 0, "owner_task_ids": owner_ids, "member_task_ids": member_ids}
    owner_id = owner_ids[0]
    signature = delivery_group_signature(owner_tasks + member_tasks)
    for task in member_tasks:
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        queue.add_event(
            "local",
            "delivery_group_member",
            {
                "owner_task_id": owner_id,
                "owner_task_ids": owner_ids,
                "thread_id": thread_id,
                "delivery_mode": delivery_mode,
                "delivery_group_signature": signature,
                "policy": "supplement_member_no_final_reply",
            },
            task_id,
        )
    queue.add_event(
        "local",
        "delivery_group_owner",
        {
            "owner_task_ids": owner_ids,
            "member_task_ids": member_ids,
            "thread_id": thread_id,
            "delivery_mode": delivery_mode,
            "delivery_group_signature": signature,
            "policy": "single_final_reply_owner",
        },
        owner_id,
    )
    return {
        "ok": True,
        "owner_task_ids": owner_ids,
        "member_task_ids": member_ids,
        "delivery_group_signature": signature,
        "member_count": len(member_ids),
    }


def complete_delivery_group_members(
    queue: MobileQueue,
    owner_task_id: str,
    member_task_ids: list[str],
    result_text: str,
    thread_id: str,
) -> list[str]:
    completed: list[str] = []
    if not owner_task_id or not member_task_ids:
        return completed
    now = datetime.now(timezone.utc).isoformat()
    result = (
        "[supplement] consumed by delivery group"
        f"; owner_task_id={owner_task_id}"
        f"; thread_id={thread_id or '<unknown>'}"
    )
    with queue.session() as db:
        for task_id in member_task_ids:
            tid = str(task_id or "")
            if not tid:
                continue
            row = db.execute(
                "SELECT status, push_status FROM mobile_tasks WHERE id=?",
                (tid,),
            ).fetchone()
            if not row:
                continue
            status = str(row["status"] or "")
            push_status = str(row["push_status"] or "")
            if status not in {"queued_for_codex", "sent_to_codex", "processing", "pending"}:
                continue
            if push_status:
                continue
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='done', result=?, updated_at=?, completed_at=?
                WHERE id=? AND push_status=''
                """,
                (result, now, now, tid),
            )
            completed.append(tid)
    for tid in completed:
        clear_task_codex_runtime(queue, tid)
        queue.add_event(
            "local",
            "delivery_group_member_completed",
            {
                "owner_task_id": owner_task_id,
                "thread_id": thread_id,
                "result_length": len(result_text or ""),
                "policy": "member_consumed_no_final_reply",
            },
            tid,
        )
    return completed


def complete_delivery_group_member_from_finished_owner(
    queue: MobileQueue,
    member_task_id: str,
    thread_id: str,
) -> list[str]:
    member_id = str(member_task_id or "")
    if not member_id:
        return []
    owner_id = delivery_group_owner_id_for_member(queue, member_id)
    if not owner_id:
        return []
    owner = queue.get_task(owner_id) or {}
    owner_status = str(owner.get("status") or "")
    owner_push_status = str(owner.get("push_status") or "")
    owner_result = str(owner.get("result") or "")
    if owner_status not in {"done", "pushed_to_wecom"} and owner_push_status != "pushed_to_wecom":
        return []
    if not owner_result:
        return []
    member_ids = delivery_group_member_ids(queue, owner_id, thread_id)
    if member_id not in member_ids:
        member_ids.append(member_id)
    completed = complete_delivery_group_members(
        queue,
        owner_id,
        member_ids,
        owner_result,
        thread_id or str(owner.get("codex_thread_id") or ""),
    )
    if completed:
        queue.add_event(
            "local",
            "delivery_group_member_completed_from_finished_owner",
            {
                "owner_task_id": owner_id,
                "completed_member_ids": completed,
                "thread_id": thread_id,
                "reason": "member had no final-reply ownership but its delivery group owner is already complete",
            },
            member_id,
        )
    return completed


def should_suppress_supplement_status_ack(queue: MobileQueue, task_id: str, event_type: str) -> bool:
    if not task_id or str(event_type or "") in SUPPLEMENT_ALLOWED_ACK_EVENTS:
        return False
    return task_is_supplement_context(queue, task_id)


def next_dispatchable_route_task_id(
    dispatchable: list[tuple[dict[str, Any], dict[str, str], str]],
    current_route_key: str,
) -> str:
    for task, active_thread, delivery_mode in dispatchable:
        thread_id = str(active_thread.get("thread_id") or "")
        route_key = task_route_key(delivery_mode, thread_id)
        if route_key and route_key != current_route_key:
            return str(task.get("id") or "")
    return ""


def attachment_task_ids(tasks: list[dict[str, Any]]) -> list[str]:
    return [str(task.get("id") or "") for task in tasks if task_has_attachments(task)]


def has_attachment_task(tasks: list[dict[str, Any]]) -> bool:
    return any(task_has_attachments(task) for task in tasks)


def build_message_supplement_notice(batch_tasks: list[dict[str, Any]]) -> str:
    return reply_status_text.message_supplement_notice(batch_tasks)


def mcp_ack_key(task_id: str) -> str:
    return f"mcp_ack:{task_id}"


def mcp_ack_payload(queue: MobileQueue, task_id: str) -> dict[str, Any]:
    raw = str(queue.runtime_get(mcp_ack_key(task_id)) or "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"raw": raw}


def invalid_mcp_ack_key(task_id: str) -> str:
    return f"mcp_ack_invalid:{task_id}"


def quarantine_invalid_mcp_ack(
    queue: MobileQueue,
    task_id: str,
    ack_payload: dict[str, Any],
    reason: str,
) -> None:
    """Retire an unusable MCP ack so it cannot loop forever."""
    tid = str(task_id or "")
    if not tid:
        return
    payload = {
        "ack_payload": ack_payload if isinstance(ack_payload, dict) else {},
        "reason": reason,
        "quarantined_at": utc_now(),
    }
    queue.runtime_delete(mcp_ack_key(tid))
    queue.runtime_set(invalid_mcp_ack_key(tid), json.dumps(payload, ensure_ascii=False))
    if not task_event_exists(queue, tid, "mcp_ack_invalid_quarantined"):
        queue.add_event("local", "mcp_ack_invalid_quarantined", payload, tid)


def mcp_ack_has_valid_base_owner(queue: MobileQueue, task_id: str) -> bool:
    ack_payload = mcp_ack_payload(queue, task_id)
    return bool(ack_payload and valid_mcp_ack_base_owner(queue, task_id, ack_payload)[0])


def valid_mcp_ack_base_owner(queue: MobileQueue, task_id: str, ack_payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return the active final-reply owner named by an MCP supplement ack."""
    if not isinstance(ack_payload, dict):
        return "", {}
    base_task_id = str(ack_payload.get("base_task_id") or "").strip()
    if not base_task_id:
        thread_id = str(ack_payload.get("thread_id") or "").strip()
        base_task_id = bridge_supplement_base_task_id_from_events(queue, task_id, thread_id)
    if not base_task_id or base_task_id == str(task_id or ""):
        return "", {}
    base_task = queue.get_task(base_task_id) or {}
    if not base_task:
        return "", {}
    if not task_owns_final_reply(queue, base_task_id):
        return "", {}
    return base_task_id, base_task


def bridge_supplement_key(thread_id: str) -> str:
    return f"bridge_supplement:{thread_id}"


def bridge_supplement_payload_for_task(
    queue: MobileQueue,
    task_id: str,
    thread_id: str = "",
) -> tuple[str, dict[str, Any]]:
    """Return the bridge_supplement runtime payload that contains task_id."""
    tid = str(task_id or "")
    if not tid:
        return "", {}
    candidate_keys: list[str] = []
    if thread_id:
        candidate_keys.append(bridge_supplement_key(thread_id))
    if not candidate_keys:
        with queue.session() as db:
            rows = db.execute(
                "SELECT key FROM mobile_runtime WHERE key LIKE 'bridge_supplement:%'"
            ).fetchall()
        candidate_keys = [str(row["key"] or "") for row in rows if str(row["key"] or "")]
    for key in candidate_keys:
        raw = str(queue.runtime_get(key) or "")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        items = payload.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("message_id") or "") == tid:
                return key, payload
    return "", {}


def bridge_supplement_base_task_id_from_events(
    queue: MobileQueue,
    task_id: str,
    thread_id: str = "",
) -> str:
    """Recover a supplement owner from durable events when runtime payload is gone."""
    context = bridge_supplement_context_from_events(queue, task_id, thread_id)
    return str(context.get("base_task_id") or "")


def bridge_supplement_context_from_events(
    queue: MobileQueue,
    task_id: str,
    thread_id: str = "",
) -> dict[str, str]:
    """Recover supplement ack context from durable publish events."""
    tid = str(task_id or "")
    if not tid:
        return {}
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT task_id, event_type, payload_json
            FROM mobile_events
            WHERE task_id=?
            ORDER BY id DESC
            LIMIT 200
            """,
            (tid,),
        ).fetchall()
    for row in rows:
        event_type = str(row["event_type"] or "")
        payload_raw = str(row["payload_json"] or "")
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            continue
        if thread_id:
            payload_thread_id = str(payload.get("thread_id") or "")
            if payload_thread_id and payload_thread_id != thread_id:
                continue
        if event_type == "delivery_group_member_released" and str(row["task_id"] or "") == tid:
            return {}
        if event_type in {"attachment_supplement_pending_published", "attachment_supplement_published"}:
            base_task_id = str(payload.get("active_task_id") or payload.get("base_message_id") or "")
            if not base_task_id:
                continue
            member_ids = [str(item) for item in payload.get("batch_task_ids") or [] if str(item)]
            if str(row["task_id"] or "") == tid or tid in member_ids:
                return {
                    "base_task_id": base_task_id,
                    "thread_id": str(payload.get("thread_id") or thread_id or ""),
                    "supplement_signature": str(payload.get("signature") or payload.get("supplement_signature") or ""),
                    "source": event_type,
                }
        if event_type == "pending_backlog_supplement_pending_published" and str(row["task_id"] or "") == tid:
            base_task_id = str(payload.get("owner_task_id") or payload.get("active_task_id") or payload.get("base_message_id") or "")
            if base_task_id:
                return {
                    "base_task_id": base_task_id,
                    "thread_id": str(payload.get("thread_id") or thread_id or ""),
                    "supplement_signature": str(payload.get("signature") or payload.get("supplement_signature") or ""),
                    "source": event_type,
                }
        if event_type == "delivery_group_member" and str(row["task_id"] or "") == tid:
            owner_id = str(payload.get("owner_task_id") or "")
            if owner_id:
                return {
                    "base_task_id": owner_id,
                    "thread_id": str(payload.get("thread_id") or thread_id or ""),
                    "supplement_signature": str(payload.get("signature") or payload.get("supplement_signature") or ""),
                    "source": event_type,
                }
        if event_type in {"continuation_deferred", "status_ack_continuation_deferred"} and str(row["task_id"] or "") == tid:
            base_task_id = str(payload.get("active_task_id") or payload.get("base_message_id") or "")
            if base_task_id:
                return {
                    "base_task_id": base_task_id,
                    "thread_id": str(payload.get("thread_id") or thread_id or ""),
                    "supplement_signature": str(payload.get("signature") or payload.get("supplement_signature") or ""),
                    "source": event_type,
                }
    return {}


def bridge_supplement_base_task_id(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("active_task_id") or payload.get("base_message_id") or "")


def bridge_supplement_task_ids(payload: dict[str, Any]) -> list[str]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    task_ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("message_id") or "")
        if tid:
            task_ids.append(tid)
    return task_ids


def bridge_supplement_published_at(payload: dict[str, Any]) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    return parse_iso_datetime(str(payload.get("published_at") or ""))


def bridge_supplement_ack_grace_seconds(config: dict[str, Any] | None = None) -> int:
    if not isinstance(config, dict):
        return SUPPLEMENT_ACK_GRACE_SECONDS
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    value = trigger.get("supplement_ack_grace_seconds")
    if value is None:
        return SUPPLEMENT_ACK_GRACE_SECONDS
    try:
        return max(10, int(value))
    except (TypeError, ValueError):
        return SUPPLEMENT_ACK_GRACE_SECONDS


def bridge_supplement_ack_wait_expired(
    payload: dict[str, Any],
    config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> bool:
    published_at = bridge_supplement_published_at(payload)
    if not published_at:
        return False
    now_dt = now_dt or datetime.now(timezone.utc)
    return now_dt - published_at >= timedelta(seconds=bridge_supplement_ack_grace_seconds(config))


def bridge_supplement_owner_completion_reference(base_task: dict[str, Any]) -> datetime | None:
    for field in ("pushed_at", "completed_at", "updated_at"):
        parsed = parse_iso_datetime(str((base_task or {}).get(field) or ""))
        if parsed:
            return parsed
    return None


def bridge_supplement_owner_completion_ack_wait_expired(
    base_task: dict[str, Any],
    config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> bool:
    completed_at = bridge_supplement_owner_completion_reference(base_task)
    if not completed_at:
        return False
    now_dt = now_dt or datetime.now(timezone.utc)
    return now_dt - completed_at >= timedelta(seconds=bridge_supplement_ack_grace_seconds(config))


def task_event_payload_exists(
    queue: MobileQueue,
    task_id: str,
    event_type: str,
    predicate: Callable[[dict[str, Any]], bool],
) -> bool:
    if not task_id or not event_type:
        return False
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT payload_json
            FROM mobile_events
            WHERE task_id=? AND event_type=?
            ORDER BY id DESC
            """,
            (task_id, event_type),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and predicate(payload):
            return True
    return False


def latest_task_event_payload(queue: MobileQueue, task_id: str, event_type: str) -> dict[str, Any]:
    if not task_id or not event_type:
        return {}
    with queue.session() as db:
        row = db.execute(
            """
            SELECT payload_json, created_at
            FROM mobile_events
            WHERE task_id=? AND event_type=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id, event_type),
        ).fetchone()
    if not row:
        return {}
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        return {}
    payload = dict(payload)
    payload["_event_created_at"] = str(row["created_at"] or "")
    return payload


def pending_task_is_published_bridge_supplement(queue: MobileQueue, task_id: str, thread_id: str = "") -> bool:
    """Return True when a pending task is already published for MCP supplement pickup."""
    _key, payload = bridge_supplement_payload_for_task(queue, task_id, thread_id)
    if payload:
        return True
    return mcp_ack_has_valid_base_owner(queue, task_id)


def pending_task_has_unacked_bridge_supplement(queue: MobileQueue, task_id: str, thread_id: str = "") -> bool:
    """Return True only for pending rows already published for MCP supplement pickup."""
    _key, payload = bridge_supplement_payload_for_task(queue, task_id, thread_id)
    return bool(payload and not mcp_ack_payload(queue, task_id))


def pending_task_has_unacked_pending_backlog_supplement(queue: MobileQueue, task_id: str, thread_id: str = "") -> bool:
    """Return True when a pending backlog supplement still needs MCP consumption or promotion."""
    tid = str(task_id or "")
    if not tid:
        return False
    task = queue.get_task(tid) or {}
    if str(task.get("status") or "") != "pending":
        return False
    if mcp_ack_payload(queue, tid):
        return False
    key, payload = bridge_supplement_payload_for_task(queue, tid, thread_id)
    if key and payload:
        source = str(payload.get("supplement_source") or "")
        sources = payload.get("supplement_sources")
        source_values = [str(item) for item in sources] if isinstance(sources, list) else []
        if source == "pending_backlog" or "pending_backlog" in source_values:
            return True
    return task_event_payload_exists(
        queue,
        tid,
        "pending_backlog_supplement_pending_published",
        lambda payload: not thread_id or str(payload.get("thread_id") or "") == thread_id,
    )


def attachment_supplement_signature_key(active_task_id: str) -> str:
    return f"attachment_supplement_signature:{active_task_id}"


def task_supplement_cursor(task: dict[str, Any]) -> str:
    return str(task.get("updated_at") or task.get("created_at") or task.get("id") or "")


def task_supplement_snapshot(task: dict[str, Any], thread_id: str) -> dict[str, Any]:
    attachments = task_attachments(task)
    return {
        "message_id": str(task.get("id") or ""),
        "kind": "attachment" if attachments else "text",
        "text": str(task.get("text") or ""),
        "attachments": attachments,
        "created_at": str(task.get("created_at") or ""),
        "updated_at": str(task.get("updated_at") or ""),
        "source_user": str(task.get("external_user") or ""),
        "thread_id": thread_id,
        "cursor": task_supplement_cursor(task),
    }


def normalize_bridge_supplement_item(item: dict[str, Any], thread_id: str) -> dict[str, Any]:
    attachments = item.get("attachments") if isinstance(item.get("attachments"), list) else []
    return {
        "message_id": str(item.get("message_id") or item.get("id") or ""),
        "kind": str(item.get("kind") or ("attachment" if attachments else "text")),
        "text": str(item.get("text") or ""),
        "attachments": attachments,
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "source_user": str(item.get("source_user") or item.get("external_user") or ""),
        "thread_id": str(item.get("thread_id") or thread_id),
        "cursor": str(item.get("cursor") or item.get("updated_at") or item.get("created_at") or item.get("message_id") or ""),
    }


def bridge_supplement_normalized_items(items: Any, thread_id: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized_item = normalize_bridge_supplement_item(item, thread_id)
        message_id = str(normalized_item.get("message_id") or "")
        if not message_id or message_id in seen:
            continue
        seen.add(message_id)
        normalized.append(normalized_item)
    return normalized


def bridge_supplement_sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decorated: list[tuple[str, int, dict[str, Any]]] = []
    for index, item in enumerate(items):
        order_key = str(item.get("created_at") or item.get("updated_at") or item.get("cursor") or item.get("message_id") or "")
        decorated.append((order_key, index, item))
    return [item for _order_key, _index, item in sorted(decorated, key=lambda entry: (entry[0], entry[1]))]


def bridge_supplement_runtime_signature(base_task_id: str, items: list[dict[str, Any]]) -> str:
    signature_payload = {
        "base_task_id": str(base_task_id or ""),
        "items": [
            {
                "message_id": str(item.get("message_id") or ""),
                "cursor": str(item.get("cursor") or ""),
                "text": str(item.get("text") or ""),
                "attachments": item.get("attachments") if isinstance(item.get("attachments"), list) else [],
            }
            for item in items
        ],
    }
    raw = json.dumps(signature_payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def bridge_supplement_item_signature_map(payload: dict[str, Any]) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    raw_map = payload.get("item_supplement_signatures")
    if isinstance(raw_map, dict):
        return {str(key): str(value) for key, value in raw_map.items() if str(key)}
    signature = str(payload.get("supplement_signature") or "")
    return {
        str(item.get("message_id") or ""): signature
        for item in payload.get("items", [])
        if isinstance(item, dict) and str(item.get("message_id") or "")
    }


def bridge_supplement_prune_consumed_items(
    payload: dict[str, Any],
    consumed_task_ids: set[str],
    *,
    new_base_task_id: str = "",
    new_active_task_id: str = "",
    new_thread_id: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """Remove consumed supplement items and rebuild a smaller runtime payload.

    Returns the updated payload and the remaining task ids that still need
    supplementation. If the returned payload has no items, the caller should
    delete the runtime slot instead of saving it.
    """
    if not isinstance(payload, dict):
        return {}, []
    consumed_task_ids = {str(item) for item in consumed_task_ids if str(item)}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    signature_map = bridge_supplement_item_signature_map(payload)
    remaining_items = [
        item
        for item in items
        if isinstance(item, dict) and str(item.get("message_id") or "") not in consumed_task_ids
    ]
    remaining_ids = [str(item.get("message_id") or "") for item in remaining_items if str(item.get("message_id") or "")]
    if not remaining_items:
        return {}, []
    base_task_id = str(new_base_task_id or payload.get("base_message_id") or payload.get("active_task_id") or "")
    active_task_id = str(new_active_task_id or payload.get("active_task_id") or base_task_id or "")
    thread_id = str(new_thread_id or payload.get("thread_id") or "")
    updated_payload = dict(payload)
    updated_payload["items"] = remaining_items
    if base_task_id:
        updated_payload["base_message_id"] = base_task_id
    if active_task_id:
        updated_payload["active_task_id"] = active_task_id
    if thread_id:
        updated_payload["thread_id"] = thread_id
    updated_payload["supplement_signature"] = bridge_supplement_runtime_signature(base_task_id or active_task_id, bridge_supplement_normalized_items(remaining_items, thread_id))
    updated_payload["item_supplement_signatures"] = {
        str(item.get("message_id") or ""): str(signature_map.get(str(item.get("message_id") or "")) or updated_payload.get("supplement_signature") or "")
        for item in remaining_items
        if isinstance(item, dict) and str(item.get("message_id") or "")
    }
    if "previous_base_message_id" not in updated_payload and str(payload.get("base_message_id") or ""):
        updated_payload["previous_base_message_id"] = str(payload.get("base_message_id") or "")
    return updated_payload, remaining_ids


def bridge_supplement_partition_consumed_items(
    payload: dict[str, Any],
    consumed_task_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Split supplement payload items into consumed and remaining sets.

    Returns consumed items, remaining items, and remaining task ids. This lets
    base recovery prune only already-consumed supplements before any promotion
    logic looks at the residual chain.
    """
    if not isinstance(payload, dict):
        return [], [], []
    consumed_task_ids = {str(item) for item in consumed_task_ids if str(item)}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    consumed_items = [
        item
        for item in items
        if isinstance(item, dict) and str(item.get("message_id") or "") in consumed_task_ids
    ]
    remaining_items = [
        item
        for item in items
        if isinstance(item, dict) and str(item.get("message_id") or "") not in consumed_task_ids
    ]
    remaining_ids = [str(item.get("message_id") or "") for item in remaining_items if str(item.get("message_id") or "")]
    return consumed_items, remaining_items, remaining_ids


def bridge_supplement_context_from_payload(payload: dict[str, Any], task_id: str) -> dict[str, str]:
    tid = str(task_id or "")
    if not isinstance(payload, dict) or not tid:
        return {}
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("message_id") or "") != tid:
            continue
        signature_map = bridge_supplement_item_signature_map(payload)
        return {
            "base_task_id": bridge_supplement_base_task_id(payload),
            "thread_id": str(payload.get("thread_id") or item.get("thread_id") or ""),
            "supplement_signature": str(signature_map.get(tid) or payload.get("supplement_signature") or ""),
            "runtime_signature": str(payload.get("supplement_signature") or ""),
            "source": "bridge_supplement_runtime",
        }
    return {}


def merge_bridge_supplement_payload(
    queue: MobileQueue,
    payload: dict[str, Any],
    source: str = "",
) -> dict[str, Any]:
    """Merge unacked supplement items into the compatible per-thread runtime slot."""
    if not isinstance(payload, dict):
        return {"ok": False, "published": False, "reason": "payload must be an object"}
    thread_id = str(payload.get("thread_id") or "").strip()
    base_task_id = bridge_supplement_base_task_id(payload)
    if not thread_id or not base_task_id:
        return {"ok": False, "published": False, "reason": "missing thread_id or base task"}
    key = bridge_supplement_key(thread_id)
    incoming_items = bridge_supplement_normalized_items(payload.get("items"), thread_id)
    incoming_items = [
        item
        for item in incoming_items
        if not mcp_ack_has_valid_base_owner(queue, str(item.get("message_id") or ""))
        and not task_owns_final_reply(queue, str(item.get("message_id") or ""))
    ]
    if not incoming_items:
        return {"ok": True, "published": False, "duplicate": True, "reason": "no unacked supplement items"}

    existing_raw = str(queue.runtime_get(key) or "")
    existing_payload: dict[str, Any] = {}
    if existing_raw:
        try:
            parsed = json.loads(existing_raw)
            existing_payload = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            existing_payload = {}

    incoming_signature = str(payload.get("supplement_signature") or "") or bridge_supplement_runtime_signature(base_task_id, incoming_items)
    existing_base_task_id = bridge_supplement_base_task_id(existing_payload)
    existing_items = bridge_supplement_normalized_items(existing_payload.get("items"), thread_id) if existing_payload else []
    existing_items = [
        item
        for item in existing_items
        if not mcp_ack_has_valid_base_owner(queue, str(item.get("message_id") or ""))
        and not task_owns_final_reply(queue, str(item.get("message_id") or ""))
    ]

    if existing_payload and existing_base_task_id and existing_base_task_id != base_task_id and existing_items:
        queue.add_event(
            "local",
            "bridge_supplement_merge_conflict_preserved",
            {
                "runtime_key": key,
                "existing_base_task_id": existing_base_task_id,
                "new_base_task_id": base_task_id,
                "existing_task_ids": [str(item.get("message_id") or "") for item in existing_items],
                "new_task_ids": [str(item.get("message_id") or "") for item in incoming_items],
                "source": source,
                "reason": "existing unacked supplement payload belongs to a different owner",
            },
            base_task_id,
        )
        return {
            "ok": False,
            "published": False,
            "reason": "existing unacked supplement payload belongs to a different owner",
            "runtime_key": key,
            "base_task_id": base_task_id,
            "existing_base_task_id": existing_base_task_id,
        }

    merged_by_id: dict[str, dict[str, Any]] = {}
    for item in existing_items + incoming_items:
        message_id = str(item.get("message_id") or "")
        if message_id and message_id not in merged_by_id:
            merged_by_id[message_id] = item
    merged_items = bridge_supplement_sort_items(list(merged_by_id.values()))
    existing_ids = {str(item.get("message_id") or "") for item in existing_items}
    incoming_ids = [str(item.get("message_id") or "") for item in incoming_items if str(item.get("message_id") or "")]
    new_ids = [message_id for message_id in incoming_ids if message_id not in existing_ids]
    if not new_ids and existing_payload:
        return {
            "ok": True,
            "published": False,
            "duplicate": True,
            "reason": "all supplement items already published for this owner",
            "runtime_key": key,
            "payload": existing_payload,
            "base_task_id": base_task_id,
            "task_ids": [str(item.get("message_id") or "") for item in merged_items],
            "signature": str(existing_payload.get("supplement_signature") or incoming_signature),
        }

    signature_by_id = bridge_supplement_item_signature_map(existing_payload)
    for message_id in incoming_ids:
        signature_by_id.setdefault(message_id, incoming_signature)
    if existing_payload and existing_items:
        runtime_signature = bridge_supplement_runtime_signature(base_task_id, merged_items)
    else:
        runtime_signature = incoming_signature

    sources = []
    existing_sources = existing_payload.get("supplement_sources") if isinstance(existing_payload.get("supplement_sources"), list) else []
    sources.extend(str(item) for item in existing_sources if str(item))
    existing_source = str(existing_payload.get("supplement_source") or "")
    if existing_source:
        sources.append(existing_source)
    incoming_source = str(payload.get("supplement_source") or source or "")
    if incoming_source:
        sources.append(incoming_source)
    sources = list(dict.fromkeys(sources))

    merged_payload = {
        **existing_payload,
        **payload,
        "base_message_id": base_task_id,
        "active_task_id": str(payload.get("active_task_id") or base_task_id),
        "thread_id": thread_id,
        "items": merged_items,
        "published_at": utc_now(),
        "first_published_at": str(existing_payload.get("first_published_at") or existing_payload.get("published_at") or payload.get("published_at") or utc_now()),
        "supplement_signature": runtime_signature,
        "item_supplement_signatures": {
            message_id: str(signature_by_id.get(message_id) or runtime_signature)
            for message_id in [str(item.get("message_id") or "") for item in merged_items]
            if message_id
        },
    }
    if sources:
        merged_payload["supplement_sources"] = sources
        merged_payload["supplement_source"] = sources[-1]

    queue.runtime_set(key, json.dumps(merged_payload, ensure_ascii=False))
    if existing_payload and new_ids:
        queue.add_event(
            "local",
            "bridge_supplement_payload_merged",
            {
                "runtime_key": key,
                "base_task_id": base_task_id,
                "thread_id": thread_id,
                "existing_task_ids": sorted(existing_ids),
                "new_task_ids": new_ids,
                "merged_task_ids": [str(item.get("message_id") or "") for item in merged_items],
                "previous_supplement_signature": str(existing_payload.get("supplement_signature") or ""),
                "supplement_signature": runtime_signature,
                "source": source,
            },
            base_task_id,
        )
    return {
        "ok": True,
        "published": bool(new_ids or not existing_payload),
        "duplicate": False,
        "runtime_key": key,
        "payload": merged_payload,
        "base_task_id": base_task_id,
        "task_ids": [str(item.get("message_id") or "") for item in merged_items],
        "new_task_ids": new_ids or incoming_ids,
        "signature": runtime_signature,
        "item_signature": incoming_signature,
    }


def attachment_supplement_signature(active_task: dict[str, Any], batch_tasks: list[dict[str, Any]]) -> str:
    items = [
        {
            "id": str(task.get("id") or ""),
            "text": str(task.get("text") or ""),
            "updated_at": str(task.get("updated_at") or ""),
            "attachments": task_attachments(task),
            "command": task_command_value(task),
        }
        for task in sorted(batch_tasks, key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))
    ]
    payload = {
        "active_task_id": str(active_task.get("id") or ""),
        "items": items,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def bridge_supplement_host_still_active_owner(
    queue: MobileQueue,
    base_task: dict[str, Any] | None,
) -> bool:
    if not base_task:
        return False
    status = str(base_task.get("status") or "")
    if status not in {"sent_to_codex", "processing"}:
        return False
    return valid_active_supplement_host(queue, base_task)


def bridge_supplement_recently_completed_owner(
    queue: MobileQueue,
    base_task: dict[str, Any] | None,
    payload: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> bool:
    """Keep a published supplement attached during the post-completion ack grace window.

    A Codex turn may finish and only then flush its MCP ack for supplements.
    Releasing the supplement payload immediately on owner completion is too
    aggressive and causes same-thread follow-up messages to fall back to normal
    dispatch before the active turn had a fair chance to consume them.
    """
    if not base_task:
        return False
    status = str(base_task.get("status") or "")
    if status not in {"done", "pushed_to_wecom"}:
        return False
    task_id = str(base_task.get("id") or "")
    if not task_id:
        return False
    if task_is_supplement_context(queue, task_id):
        return False
    if not task_has_final_reply_owner_evidence(queue, task_id):
        return False
    return not bridge_supplement_owner_completion_ack_wait_expired(base_task, config)


def _bridge_supplement_runtime_rows(queue: MobileQueue, thread_id: str = "") -> list[tuple[str, dict[str, Any]]]:
    if thread_id:
        keys = [bridge_supplement_key(thread_id)]
    else:
        with queue.session() as db:
            rows = db.execute(
                """
                SELECT key
                FROM mobile_runtime
                WHERE key LIKE 'bridge_supplement:%'
                ORDER BY updated_at ASC
                """
            ).fetchall()
        keys = [str(row["key"] or "") for row in rows]
    results: list[tuple[str, dict[str, Any]]] = []
    for key in keys:
        raw = str(queue.runtime_get(key) or "")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            results.append((key, payload))
    return results


def promote_orphaned_bridge_supplements(
    queue: MobileQueue,
    config: dict[str, Any] | None = None,
    thread_id: str = "",
    force_base_task_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Promote the first unconsumed supplement when its base owner already finished.

    Supplements should stay supplements while the base owner is active, and also
    during the short post-completion MCP ack grace window. After that, keeping
    the pending rows hidden makes the queue appear stuck. The FIFO recovery is
    to promote the oldest remaining supplement to a normal final-reply owner and
    rebase the rest of the supplement chain to that new owner.
    """
    promoted: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []
    resumed: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    force_base_task_ids = {str(item) for item in (force_base_task_ids or set()) if str(item)}
    for key, payload in _bridge_supplement_runtime_rows(queue, thread_id):
        task_ids = bridge_supplement_task_ids(payload)
        if not task_ids:
            continue
        base_task_id = bridge_supplement_base_task_id(payload)
        base_task = queue.get_task(base_task_id) if base_task_id else None
        base_status = str((base_task or {}).get("status") or "")
        if not base_task:
            continue
        base_result = str((base_task or {}).get("result") or "").strip()
        base_push_status = str((base_task or {}).get("push_status") or "").strip()
        base_pushed_at = str((base_task or {}).get("pushed_at") or "").strip()
        base_completed = base_status in {"done", "pushed_to_wecom"}
        base_terminal_failed = (
            terminal_failed_status(base_status)
            and not base_result
            and not base_push_status
            and not base_pushed_at
        )
        if not base_completed and not base_terminal_failed:
            continue
        if base_completed and not task_has_completed_final_reply_evidence(queue, base_task_id, base_task):
            continue
        if base_completed and base_task_id not in force_base_task_ids and not task_owns_final_reply(queue, base_task_id):
            continue
        if (
            base_completed
            and base_task_id not in force_base_task_ids
            and bridge_supplement_recently_completed_owner(queue, base_task, payload, config)
        ):
            preserved.append(
                {
                    "runtime_key": key,
                    "base_task_id": base_task_id,
                    "base_status": base_status,
                    "reason": "base completed but MCP ack grace is still open",
                }
            )
            continue

        with queue.session() as db:
            placeholders = ",".join("?" for _ in task_ids)
            rows = db.execute(
                f"""
                SELECT id, status, created_at, updated_at
                FROM mobile_tasks
                WHERE id IN ({placeholders})
                """,
                task_ids,
            ).fetchall()
        row_by_id = {str(row["id"] or ""): dict(row) for row in rows}
        pending_task_ids = [
            tid
            for tid in task_ids
            if str((row_by_id.get(tid) or {}).get("status") or "") == "pending"
            and not mcp_ack_payload(queue, tid)
            and not task_owns_final_reply(queue, tid)
        ]
        if not pending_task_ids:
            continue

        promoted_id = pending_task_ids[0]
        remaining_ids = pending_task_ids[1:]
        remaining_set = set(remaining_ids)
        original_items = [item for item in payload.get("items", []) if isinstance(item, dict)]
        remaining_items = [
            item for item in original_items if str(item.get("message_id") or "") in remaining_set
        ]
        thread_value = str(payload.get("thread_id") or thread_id or str(key).split("bridge_supplement:", 1)[-1])
        signature_payload = {
            "old_base_task_id": base_task_id,
            "promoted_task_id": promoted_id,
            "remaining_task_ids": remaining_ids,
            "source_signature": str(payload.get("supplement_signature") or ""),
        }
        new_signature = hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        with queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET codex_thread_id=COALESCE(NULLIF(codex_thread_id, ''), ?),
                    queued_for_codex_at=NULL,
                    sent_to_codex_at=NULL,
                    claimed_by='',
                    claimed_at=NULL,
                    updated_at=?
                WHERE id=? AND status='pending'
                """,
                (thread_value, now, promoted_id),
            )
            if remaining_ids:
                placeholders = ",".join("?" for _ in remaining_ids)
                db.execute(
                    f"""
                    UPDATE mobile_tasks
                    SET codex_thread_id=COALESCE(NULLIF(codex_thread_id, ''), ?),
                        queued_for_codex_at=NULL,
                        sent_to_codex_at=NULL,
                        updated_at=?
                    WHERE id IN ({placeholders}) AND status='pending'
                    """,
                    (thread_value, now, *remaining_ids),
                )

        promoted_record = {
            "runtime_key": key,
            "previous_base_task_id": base_task_id,
            "base_status": base_status,
            "base_terminal_failed": base_terminal_failed,
            "thread_id": thread_value,
            "promoted_task_id": promoted_id,
            "remaining_task_ids": remaining_ids,
            "previous_supplement_signature": str(payload.get("supplement_signature") or ""),
            "new_supplement_signature": new_signature,
            "policy": "base owner already produced a final result or terminally failed without recoverable result; first unconsumed supplement becomes the next final-reply owner and later supplements rebase behind it",
        }
        queue.add_event("local", "supplement_promoted_to_owner", promoted_record, promoted_id)
        clear_delivery_retry(queue, [promoted_id])
        if remaining_items:
            new_payload = {
                **payload,
                "base_message_id": promoted_id,
                "active_task_id": promoted_id,
                "thread_id": thread_value,
                "items": remaining_items,
                "published_at": now,
                "supplement_signature": new_signature,
                "previous_base_message_id": base_task_id,
            }
            queue.runtime_set(key, json.dumps(new_payload, ensure_ascii=False))
            for remaining_id in remaining_ids:
                queue.add_event(
                    "local",
                    "supplement_rebased_to_promoted_owner",
                    {
                        **promoted_record,
                        "new_base_task_id": promoted_id,
                        "task_id": remaining_id,
                    },
                    remaining_id,
                )
        else:
            queue.runtime_delete(key)
        resumed.append({
            "runtime_key": key,
            "promoted_task_id": promoted_id,
            "thread_id": thread_value,
            "reason": "promoted supplement should re-enter normal dispatch immediately",
        })
        promoted.append(promoted_record)
    if resumed:
        queue.add_event(
            "local",
            "supplement_owner_reschedule_requested",
            {
                "thread_id": thread_id,
                "resumed_task_ids": [str(item["promoted_task_id"]) for item in resumed],
                "policy": "promotion must restart a real owner dispatch turn so the earliest supplement can be consumed and later supplements can rebase behind it",
            },
            str(resumed[0]["promoted_task_id"]),
        )
    return {
        "ok": True,
        "promoted": promoted,
        "promoted_count": len(promoted),
        "resumed": resumed,
        "resumed_count": len(resumed),
        "preserved": preserved,
        "preserved_count": len(preserved),
    }


def publish_attachment_supplement_for_active(
    queue: MobileQueue,
    config: dict[str, Any],
    active_task: dict[str, Any],
    thread_id: str,
    batch_pending: list[dict[str, Any]],
    delivery_mode: str,
) -> dict[str, Any]:
    active_task_id = str(active_task.get("id") or "")
    if not active_task_id or not thread_id or not batch_pending:
        return {"ok": False, "published": False, "reason": "missing active task, thread, or supplement batch"}
    already_published = [
        str(task.get("id") or "")
        for task in batch_pending
        if pending_task_is_published_bridge_supplement(queue, str(task.get("id") or ""), thread_id)
    ]
    if already_published and len(already_published) == len(batch_pending):
        return {
            "ok": True,
            "published": False,
            "duplicate": True,
            "reason": "all supplement tasks already published for this thread",
            "task_ids": already_published,
        }
    signature = attachment_supplement_signature(active_task, batch_pending)
    signature_key = attachment_supplement_signature_key(active_task_id)
    if queue.runtime_get(signature_key) == signature:
        return {"ok": True, "published": False, "duplicate": True, "signature": signature}

    items = [task_supplement_snapshot(task, thread_id) for task in batch_pending]
    payload = {
        "base_message_id": active_task_id,
        "thread_id": thread_id,
        "active_task_id": active_task_id,
        "delivery_mode": delivery_mode,
        "items": items,
        "published_at": utc_now(),
        "supplement_signature": signature,
    }
    merge_result = merge_bridge_supplement_payload(queue, payload, "attachment_supplement")
    if not merge_result.get("ok"):
        return {**merge_result, "signature": signature, "items": len(items)}
    queue.runtime_set(signature_key, signature)
    notice_text = build_message_supplement_notice(batch_pending)
    ack_result: dict[str, Any] = {"ok": False, "skipped": True, "reason": "empty notice"}
    if notice_text:
        ack_result = send_status_ack(
            queue,
            active_task,
            notice_text,
            config,
            "status_ack_attachment_supplement",
        )
    queue.add_event(
        "local",
        "attachment_supplement_published",
        {
            "thread_id": thread_id,
            "delivery_mode": delivery_mode,
            "batch_task_ids": [str(item.get("id") or "") for item in batch_pending],
            "signature": signature,
            "runtime_signature": str(merge_result.get("signature") or ""),
            "ack": ack_result,
        },
        active_task_id,
    )
    for task in batch_pending:
        queue.add_event(
            "local",
            "attachment_supplement_pending_published",
            {"active_task_id": active_task_id, "thread_id": thread_id, "signature": signature},
            str(task.get("id") or ""),
        )
    return {
        "ok": True,
        "published": bool(merge_result.get("published")),
        "signature": signature,
        "runtime_signature": str(merge_result.get("signature") or ""),
        "items": len(items),
        "ack": ack_result,
    }


def publish_pending_backlog_supplement_for_owner(
    queue: MobileQueue,
    config: dict[str, Any],
    owner_task: dict[str, Any],
    thread_id: str,
    backlog_tasks: list[dict[str, Any]],
    delivery_mode: str,
) -> dict[str, Any]:
    """Publish later same-route pending rows as one MCP supplement batch.

    The owner is the only task delivered to Codex. Later pending rows stay
    pending and are made available through bridge.get_pending_batch.
    """
    owner_task_id = str(owner_task.get("id") or "")
    if not owner_task_id or not thread_id or not backlog_tasks:
        return {"ok": False, "published": False, "reason": "missing owner task, thread, or supplement backlog"}
    backlog_ids = [str(task.get("id") or "") for task in backlog_tasks if str(task.get("id") or "")]
    already_published = [
        task_id
        for task_id in backlog_ids
        if pending_task_is_published_bridge_supplement(queue, task_id, thread_id)
    ]
    if already_published and len(already_published) == len(backlog_ids):
        return {
            "ok": True,
            "published": False,
            "duplicate": True,
            "reason": "all backlog tasks already published for this thread",
            "task_ids": already_published,
        }
    signature = attachment_supplement_signature(owner_task, backlog_tasks)
    items = [task_supplement_snapshot(task, thread_id) for task in backlog_tasks]
    payload = {
        "base_message_id": owner_task_id,
        "thread_id": thread_id,
        "active_task_id": owner_task_id,
        "delivery_mode": delivery_mode,
        "items": items,
        "published_at": utc_now(),
        "supplement_signature": signature,
        "supplement_source": "pending_backlog",
    }
    merge_result = merge_bridge_supplement_payload(queue, payload, "pending_backlog")
    if not merge_result.get("ok"):
        return {**merge_result, "signature": signature, "items": len(items)}
    queue.runtime_set(attachment_supplement_signature_key(owner_task_id), signature)
    notice_text = build_message_supplement_notice(backlog_tasks)
    ack_result: dict[str, Any] = {"ok": False, "skipped": True, "reason": "empty notice"}
    if notice_text:
        ack_result = send_status_ack(
            queue,
            owner_task,
            notice_text,
            config,
            "status_ack_pending_backlog_supplement",
        )
    queue.add_event(
        "local",
        "pending_backlog_supplement_published",
        {
            "thread_id": thread_id,
            "delivery_mode": delivery_mode,
            "owner_task_id": owner_task_id,
            "batch_task_ids": backlog_ids,
            "signature": signature,
            "runtime_signature": str(merge_result.get("signature") or ""),
            "ack": ack_result,
            "policy": "owner delivered alone; later pending rows are exposed only through MCP supplement",
        },
        owner_task_id,
    )
    for task in backlog_tasks:
        queue.add_event(
            "local",
            "pending_backlog_supplement_pending_published",
            {"owner_task_id": owner_task_id, "thread_id": thread_id, "signature": signature},
            str(task.get("id") or ""),
        )
    return {
        "ok": True,
        "published": bool(merge_result.get("published")),
        "signature": signature,
        "runtime_signature": str(merge_result.get("signature") or ""),
        "items": len(items),
        "ack": ack_result,
    }


def clear_pending_backlog_supplement_if_matches(
    queue: MobileQueue,
    thread_id: str,
    owner_task_id: str,
    signature: str,
) -> None:
    raw = str(queue.runtime_get(bridge_supplement_key(thread_id)) or "")
    if not raw:
        return
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    if str(payload.get("base_message_id") or "") != str(owner_task_id or ""):
        return
    raw_sources = payload.get("supplement_sources")
    source_values = [str(item) for item in raw_sources] if isinstance(raw_sources, list) else []
    if str(payload.get("supplement_source") or "") != "pending_backlog" and "pending_backlog" not in source_values:
        return
    signature_map = bridge_supplement_item_signature_map(payload)
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    remaining_items = [
        item
        for item in items
        if isinstance(item, dict)
        and str(signature_map.get(str(item.get("message_id") or "")) or payload.get("supplement_signature") or "") != str(signature or "")
    ]
    if len(remaining_items) == len(items):
        return
    if not remaining_items:
        queue.runtime_delete(bridge_supplement_key(thread_id))
        return
    payload["items"] = remaining_items
    payload["supplement_signature"] = bridge_supplement_runtime_signature(str(owner_task_id or ""), bridge_supplement_normalized_items(remaining_items, thread_id))
    payload["item_supplement_signatures"] = {
        str(item.get("message_id") or ""): str(signature_map.get(str(item.get("message_id") or "")) or payload.get("supplement_signature") or "")
        for item in remaining_items
        if isinstance(item, dict) and str(item.get("message_id") or "")
    }
    queue.runtime_set(bridge_supplement_key(thread_id), json.dumps(payload, ensure_ascii=False))


def process_mcp_acked_pending_supplements(queue: MobileQueue, limit: int = 100) -> dict[str, Any]:
    completed: list[str] = []
    candidates: dict[str, dict[str, Any]] = {}
    for task in queue.list_pending(limit):
        task_id = str(task.get("id") or "")
        if task_id:
            candidates[task_id] = task
    for task in queue.list_active_codex_delivery_tasks(limit):
        task_id = str(task.get("id") or "")
        if task_id:
            candidates.setdefault(task_id, task)

    for task in candidates.values():
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        ack_payload = mcp_ack_payload(queue, task_id)
        if not ack_payload:
            continue
        base_task_id, base_task = valid_mcp_ack_base_owner(queue, task_id, ack_payload)
        if not base_task_id:
            quarantine_invalid_mcp_ack(
                queue,
                task_id,
                ack_payload,
                "MCP ack is missing a valid final-reply base_task_id",
            )
            queue.add_event(
                "local",
                "mcp_ack_ignored_missing_base_owner",
                {
                    "ack_payload": ack_payload,
                    "reason": "MCP ack is missing a valid final-reply base_task_id; ack runtime quarantined and task kept in its current delivery state",
                },
                task_id,
            )
            continue
        if not task_is_supplement_context(queue, task_id):
            queue.add_event(
                "local",
                "mcp_ack_ignored_for_result_owner",
                {
                    "ack_payload": ack_payload,
                    "reason": "task is not supplement context; keep it as final-reply owner",
                },
                task_id,
            )
            continue
        if str(task.get("push_status") or ""):
            continue
        thread_id = str(ack_payload.get("thread_id") or base_task.get("codex_thread_id") or task.get("codex_thread_id") or "")
        result = (
            "[supplement] consumed by active Codex turn"
            f"; base_task_id={base_task_id or '<unknown>'}"
            f"; thread_id={thread_id or '<unknown>'}"
        )
        queue.complete(task_id, result)
        queue.add_event(
            "local",
            "mcp_acked_supplement_completed",
            {
                "base_task_id": base_task_id,
                "thread_id": thread_id,
                "supplement_signature": str(ack_payload.get("supplement_signature") or ""),
                "ack_source": str(ack_payload.get("ack_source") or "mcp_ack"),
                "policy": "supplement_consumed_no_final_reply",
                "recoverable_with_base_task": bool(base_task_id),
            },
            task_id,
        )
        completed.append(task_id)
    return {"ok": True, "completed": completed, "completed_count": len(completed)}


def release_invalid_published_supplements(
    queue: MobileQueue,
    pending: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Facade for supplement release; implementation lives in supplement_runtime."""
    deps = SupplementRuntimeDependencies(
        bridge_supplement_ack_wait_expired=bridge_supplement_ack_wait_expired,
        bridge_supplement_base_task_id=bridge_supplement_base_task_id,
        bridge_supplement_host_still_active_owner=bridge_supplement_host_still_active_owner,
        bridge_supplement_payload_for_task=bridge_supplement_payload_for_task,
        bridge_supplement_recently_completed_owner=bridge_supplement_recently_completed_owner,
        mcp_ack_payload=mcp_ack_payload,
        task_event_payload_exists=task_event_payload_exists,
        task_is_promoted_supplement_owner=task_is_promoted_supplement_owner,
        task_is_released_final_reply_owner=task_is_released_final_reply_owner,
        valid_active_supplement_host=valid_active_supplement_host,
    )
    return release_invalid_published_supplements_impl(queue, pending, config, deps)


def publish_attachment_active_supplements(
    queue: MobileQueue,
    config: dict[str, Any],
    pending: list[dict[str, Any]],
) -> dict[str, Any]:
    active = queue.list_active_codex_delivery_tasks(limit=100)
    published: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for active_task in active:
        status = str(active_task.get("status") or "")
        if status not in {"sent_to_codex", "processing"}:
            continue
        if not valid_active_supplement_host(queue, active_task):
            active_task_id = str(active_task.get("id") or "")
            if not task_event_recent(queue, active_task_id, "attachment_supplement_host_rejected", 60):
                queue.add_event(
                    "local",
                    "attachment_supplement_host_rejected",
                    {
                        "reason": "active task does not own final reply",
                        "status": status,
                        "supplement_context": task_is_supplement_context(queue, active_task_id),
                    },
                    active_task_id,
                )
            continue
        if task_is_control_task(active_task):
            continue
        thread_id = str(active_task.get("codex_thread_id") or "")
        if not thread_id:
            continue
        delivery_mode = delivery_mode_for_task(config, active_task)
        batch_pending = pending_route_batch_tasks(queue, config, active_task, thread_id, delivery_mode, pending)
        batch_pending = [
            item for item in batch_pending
            if not task_is_released_final_reply_owner(queue, str(item.get("id") or ""))
        ]
        if not batch_pending:
            continue
        task_id = str(active_task.get("id") or "")
        result = publish_attachment_supplement_for_active(
            queue,
            config,
            active_task,
            thread_id,
            batch_pending,
            delivery_mode,
        )
        if not result.get("ok"):
            failed.append(
                {
                    "task_id": task_id,
                    "thread_id": thread_id,
                    "delivery_mode": delivery_mode,
                    "result": result,
                }
            )
            queue.add_event(
                "local",
                "attachment_supplement_publish_failed",
                {
                    "thread_id": thread_id,
                    "delivery_mode": delivery_mode,
                    "result": result,
                    "batch_task_ids": [str(item.get("id") or "") for item in batch_pending],
                },
                task_id,
            )
            continue
        if result.get("duplicate"):
            duplicates.append({"task_id": task_id, "thread_id": thread_id, "result": result})
        elif result.get("published"):
            published.append({"task_id": task_id, "thread_id": thread_id, "result": result})
    return {"ok": True, "published": published, "duplicates": duplicates, "failed": failed, "suppressed": suppressed}


def publish_visible_cdp_busy_supplement_for_pending(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    thread_id: str,
    active_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Expose a same-thread CDP follow-up to MCP while the visible turn is generating."""
    task_id = str(task.get("id") or "")
    if not task_id or not thread_id:
        return {"ok": False, "published": False, "reason": "missing task or thread id"}
    if pending_task_is_published_bridge_supplement(queue, task_id, thread_id):
        return {"ok": True, "published": False, "duplicate": True, "reason": "task already published as bridge supplement"}
    if not task_can_be_same_turn_supplement(queue, task):
        return {"ok": False, "published": False, "reason": "task is not eligible for same-turn supplement"}

    task_identity = (
        str(task.get("external_user") or ""),
        str(task.get("receiver_account_id") or ""),
    )
    candidates: list[dict[str, Any]] = []
    for active_task in active_tasks:
        if delivery_mode_for_task(config, active_task) != "codex-cdp":
            continue
        if str(active_task.get("codex_thread_id") or "") != thread_id:
            continue
        if (
            str(active_task.get("external_user") or ""),
            str(active_task.get("receiver_account_id") or ""),
        ) != task_identity:
            continue
        if task_is_control_task(active_task):
            continue
        if not valid_active_supplement_host(queue, active_task):
            continue
        candidates.append(active_task)
    if not candidates:
        return {"ok": False, "published": False, "reason": "no valid active visible CDP supplement host"}

    candidates.sort(
        key=lambda item: str(item.get("sent_to_codex_at") or item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    active_host = candidates[0]
    result = publish_attachment_supplement_for_active(
        queue,
        config,
        active_host,
        thread_id,
        [task],
        "codex-cdp",
    )
    if result.get("ok"):
        queue.add_event(
            "local",
            "visible_cdp_busy_supplement_published",
            {
                "active_task_id": str(active_host.get("id") or ""),
                "thread_id": thread_id,
                "publish": result,
                "policy": "visible CDP is generating; same-thread follow-up is exposed through MCP supplement instead of normal busy retry",
            },
            task_id,
        )
    return result


def release_queued_tasks_for_active_owner_supplement(
    queue: MobileQueue,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Recover half-queued same-thread tasks so they can be MCP supplements.

    A worker can die or be killed after queue_for_codex but before dispatch
    returns a turn id. If the same route already has a healthy final-reply owner,
    that queued task should not keep retrying as a new app-server turn; it should
    return to pending and be published as a supplement for the active owner.
    """
    active = queue.list_active_codex_delivery_tasks(limit=100)
    owner_by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
    queued: list[dict[str, Any]] = []
    for task in active:
        status = str(task.get("status") or "")
        delivery_mode = delivery_mode_for_task(config, task)
        thread_id = str(task.get("codex_thread_id") or "")
        identity = _task_route_identity(task, thread_id, delivery_mode)
        if status in {"sent_to_codex", "processing"} and bridge_supplement_host_still_active_owner(queue, task):
            owner_by_identity.setdefault(identity, task)
        elif status == "queued_for_codex":
            queued.append(task)

    released: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    for task in queued:
        tid = str(task.get("id") or "")
        if not tid or queue.runtime_get(task_turn_key(tid)):
            continue
        delivery_mode = delivery_mode_for_task(config, task)
        thread_id = str(task.get("codex_thread_id") or "")
        owner = owner_by_identity.get(_task_route_identity(task, thread_id, delivery_mode))
        if not owner:
            continue
        with queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='pending',
                    queued_for_codex_at=NULL,
                    sent_to_codex_at=NULL,
                    claimed_by='',
                    claimed_at=NULL,
                    updated_at=?
                WHERE id=? AND status='queued_for_codex'
                """,
                (now, tid),
            )
        clear_delivery_retry(queue, [tid])
        payload = {
            "active_task_id": str(owner.get("id") or ""),
            "thread_id": thread_id,
            "delivery_mode": delivery_mode,
            "reason": "queued task has no turn runtime and same route has active final-reply owner",
            "policy": "return to pending so supplement publisher can expose it to MCP instead of starting a duplicate turn",
        }
        queue.add_event("local", "queued_same_route_released_for_supplement", payload, tid)
        released.append({"task_id": tid, **payload})
    return {"ok": True, "released": released, "released_count": len(released)}


def send_reply_pending_batch_notice(
    queue: MobileQueue,
    task: dict[str, Any],
    config: dict[str, Any],
    event_type: str = "status_ack_reply_pending_batch",
) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    if not task_id:
        return {"ok": False, "reason": "task id is required"}
    if reply_pending_batch_notice_sent(queue, task_id):
        return {"ok": True, "skipped": True, "reason": "batch_notice_already_sent"}
    batch_tasks = pending_reply_batch_tasks(queue, task)
    if not batch_tasks:
        return {"ok": False, "reason": "no pending batch tasks"}
    notice_text = build_reply_pending_batch_notice(batch_tasks)
    if not notice_text:
        return {"ok": False, "reason": "empty batch notice"}
    result = send_status_ack(queue, task, notice_text, config, event_type)
    if result.get("ok"):
        for item in batch_tasks:
            mark_reply_pending_batch_notice_sent(queue, str(item.get("id") or ""))
    return result


def revert_tasks_to_pending(
    queue: MobileQueue,
    task_ids: list[str],
    event_type: str,
    payload: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for tid in task_ids:
        clear_task_codex_runtime(queue, tid)
        with queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='pending',
                    queued_for_codex_at=NULL,
                    sent_to_codex_at=NULL,
                    claimed_by='',
                    claimed_at=NULL,
                    updated_at=?
                WHERE id=?
                """,
                (now, tid),
            )
        queue.add_event("local", event_type, payload, tid)


def release_active_task_to_pending(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    reason: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Release an active Codex delivery slot without failing the mobile task."""
    tid = str(task.get("id") or "")
    if not tid:
        return {"ok": False, "reason": "task id is required"}
    payload = {
        "reason": reason,
        "detail": detail or {},
        "previous_status": str(task.get("status") or ""),
        "codex_thread_id": str(task.get("codex_thread_id") or ""),
        "created_at": str(task.get("created_at") or ""),
        "updated_at": str(task.get("updated_at") or ""),
        "queued_for_codex_at": str(task.get("queued_for_codex_at") or ""),
        "sent_to_codex_at": str(task.get("sent_to_codex_at") or ""),
    }
    clear_waiting_followup_redelivery_state(queue, tid)
    revert_tasks_to_pending(
        queue,
        [tid],
        "active_slot_released_to_pending",
        payload,
    )
    mark_delivery_retry(queue, config, [tid], reason, payload)
    return {"ok": True, "task_id": tid, **payload}


def task_waits_for_followup_redelivery(config: dict[str, Any], task: dict[str, Any]) -> bool:
    """Primary visible-CDP turns should not auto-redeliver on missing owned result.

    For the visible desktop route, retyping the same message into the current
    Codex conversation is more harmful than waiting. We only retry after a new
    same-thread message arrives, which acts as a fresh user continuation signal.
    """
    delivery_mode = delivery_mode_for_task(config, task)
    account_id = receiver_account_id(
        config,
        str(task.get("receiver_account_id") or ""),
        str(task.get("external_user") or ""),
    )
    return delivery_mode == "codex-cdp" and account_id == "primary"


def waiting_followup_redelivery_key(task_id: str) -> str:
    return f"waiting_followup_redelivery:{task_id}"


def latest_task_event_id(
    queue: MobileQueue,
    task_id: str,
    event_types: str | list[str] | tuple[str, ...],
) -> int:
    if not task_id:
        return 0
    values = [event_types] if isinstance(event_types, str) else [str(item or "") for item in event_types if str(item or "")]
    if not values:
        return 0
    placeholders = ",".join("?" for _ in values)
    with queue.session() as db:
        row = db.execute(
            f"""
            SELECT MAX(id) AS max_id
            FROM mobile_events
            WHERE task_id=? AND event_type IN ({placeholders})
            """,
            (task_id, *values),
        ).fetchone()
    try:
        return int(row["max_id"] or 0) if row else 0
    except Exception:
        return 0


def clear_waiting_followup_redelivery_state(
    queue: MobileQueue,
    task_id: str,
    reason: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    if not task_id:
        return
    queue.runtime_delete(waiting_followup_redelivery_key(task_id))
    if reason:
        queue.add_event(
            "local",
            "active_waiting_followup_redelivery_cleared",
            {
                "reason": reason,
                "detail": detail or {},
            },
            task_id,
        )


def mark_waiting_followup_redelivery(
    queue: MobileQueue,
    task: dict[str, Any],
    reason: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tid = str(task.get("id") or "")
    if not tid:
        return {"ok": False, "reason": "task id is required"}
    already_waiting = task_is_waiting_followup_redelivery(queue, tid)
    payload = {
        "reason": reason,
        "detail": detail or {},
        "previous_status": str(task.get("status") or ""),
        "codex_thread_id": str(task.get("codex_thread_id") or ""),
        "created_at": str(task.get("created_at") or ""),
        "updated_at": str(task.get("updated_at") or ""),
        "queued_for_codex_at": str(task.get("queued_for_codex_at") or ""),
        "sent_to_codex_at": str(task.get("sent_to_codex_at") or ""),
        "policy": "wait_for_same_thread_followup_before_redelivery",
    }
    queue.runtime_set(waiting_followup_redelivery_key(tid), json.dumps(payload, ensure_ascii=False))
    if not already_waiting:
        queue.add_event("local", "active_waiting_followup_redelivery", payload, tid)
    return {"ok": True, "task_id": tid, **payload}


def waiting_followup_redelivery_age_seconds(
    queue: MobileQueue,
    task: dict[str, Any],
    now: datetime | None = None,
) -> int:
    tid = str(task.get("id") or "")
    now = now or datetime.now(timezone.utc)
    raw = queue.runtime_get(waiting_followup_redelivery_key(tid)) if tid else ""
    payload: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(str(raw))
            payload = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            payload = {}
    candidates = [
        latest_task_event_created_at(queue, tid, "active_waiting_followup_redelivery"),
        parse_iso_datetime(str(payload.get("sent_to_codex_at") or "")),
        parse_iso_datetime(str(payload.get("updated_at") or "")),
        parse_iso_datetime(str(task.get("sent_to_codex_at") or "")),
        parse_iso_datetime(str(task.get("updated_at") or "")),
        parse_iso_datetime(str(task.get("created_at") or "")),
    ]
    valid_candidates = [candidate for candidate in candidates if candidate]
    started_at = min(valid_candidates) if valid_candidates else None
    if not started_at:
        return 0
    return max(0, int((now - started_at).total_seconds()))


def try_recover_owned_result_before_failure_close(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    close_reason: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote a task to done if a durable owned final result exists right before fail-close."""
    tid = str(task.get("id") or "")
    if not tid:
        return {"ok": False, "recovered": False, "reason": "task id is required"}
    current_task = queue.get_task(tid) or {}
    current_status = str(current_task.get("status") or "")
    if current_status not in {"sent_to_codex", "processing"}:
        return {
            "ok": True,
            "recovered": False,
            "reason": "task_not_active_owner",
            "status": current_status,
        }
    if task_is_supplement_context(queue, tid) and not task_owns_final_reply(queue, tid):
        return {
            "ok": True,
            "recovered": False,
            "reason": "supplement_member_not_final_reply_owner",
        }

    task = current_task
    delivery_mode = delivery_mode_for_task(config, task)
    poll_config = task_delivery_config(config, delivery_mode)
    health_result = check_codex_health(poll_config)
    if not health_result.get("healthy"):
        return {
            "ok": True,
            "recovered": False,
            "reason": "codex_health_unavailable",
            "health": health_result,
        }

    turn_id = str(queue.runtime_get(task_turn_key(tid)) or "")
    client_message_id, expected_task_ids = task_batch_runtime(queue, tid, [tid])
    if not turn_id:
        rehydrated = rehydrate_codex_turn_runtime_from_event(queue, tid)
        if rehydrated.get("ok"):
            turn_id = str(rehydrated.get("turn_id") or "")
            client_message_id, expected_task_ids = task_batch_runtime(queue, tid, [tid])
        else:
            return {
                "ok": True,
                "recovered": False,
                "reason": "missing_turn_runtime",
                "rehydrate": rehydrated,
            }

    expected_result_codes = task_result_code_runtime(queue, expected_task_ids)
    expected_ack_codes = task_ack_code_runtime(queue, expected_task_ids)
    poll = poll_codex_result(
        poll_config,
        str(task.get("codex_thread_id") or ""),
        turn_id,
        "",
        client_message_id,
        expected_task_ids,
        expected_result_codes,
        expected_ack_codes,
    )
    poll, new_text, owned_complete = recover_owned_result_from_history_sources(
        queue,
        config,
        poll_config,
        tid,
        str(task.get("codex_thread_id") or ""),
        turn_id,
        client_message_id,
        expected_task_ids,
        expected_result_codes,
        expected_ack_codes,
        poll,
    )

    if not new_text or not owned_complete or poll_has_ownership_mismatch(poll):
        return {
            "ok": True,
            "recovered": False,
            "reason": "no_durable_owned_result_before_failure_close",
            "poll": poll,
        }

    queue.complete(tid, new_text, status="done")
    silence_key = "silence:" + str(task.get("external_user") or "") + ":" + tid
    queue.runtime_delete(silence_key)
    clear_waiting_followup_redelivery_state(
        queue,
        tid,
        "owned_result_recovered_before_failure_close",
        {"poll": poll, "close_reason": close_reason, "detail": detail or {}},
    )
    completed_members = complete_delivery_group_members(
        queue,
        tid,
        delivery_group_member_ids(queue, tid),
        new_text,
        str(task.get("codex_thread_id") or ""),
    )
    clear_task_codex_runtime(queue, tid)
    reply = push_final_reply_async(queue, task, new_text, config)
    result = {
        "ok": True,
        "recovered": True,
        "task_id": tid,
        "status": "done",
        "reason": "owned_result_recovered_before_failure_close",
        "close_reason": close_reason,
        "detail": detail or {},
        "poll": poll,
        "reply": reply,
        "completed_group_members": completed_members,
    }
    queue.add_event("local", "failure_close_owned_result_recovered", result, tid)
    return result


def fail_waiting_followup_redelivery_manual_required(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    reason: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    tid = str(task.get("id") or "")
    if not tid:
        return {"ok": False, "reason": "task id is required"}
    recovered = try_recover_owned_result_before_failure_close(queue, config, task, reason, detail)
    if recovered.get("recovered"):
        queue.runtime_delete(active_recovery_retry_key(tid))
        return recovered
    now = datetime.now(timezone.utc).isoformat()
    error = (
        "Codex visible-CDP turn ended without owned mobile_result markers after bounded recovery; "
        "manual retry is required."
    )
    with queue.session() as db:
        db.execute(
            """
            UPDATE mobile_tasks
            SET status='failed', error=?, updated_at=?, completed_at=?
            WHERE id=? AND status IN ('sent_to_codex', 'processing')
            """,
            (error, now, now, tid),
        )
    clear_waiting_followup_redelivery_state(queue, tid, reason, detail)
    clear_task_codex_runtime(queue, tid)
    queue.runtime_delete(active_recovery_retry_key(tid))
    queue.add_event(
        "local",
        "protocol_violation_no_owned_result_manual_required",
        {
            "reason": reason,
            "detail": detail,
            "recovery_attempt": recovered,
            "policy": "do not send unowned text or redeliver visible-CDP indefinitely; fail closed for manual retry",
        },
        tid,
    )
    send_terminal_failure_notice(queue, task, config, reason, detail)
    return {"ok": True, "task_id": tid, "status": "failed", "error": error}


def fail_app_server_no_owned_result_manual_required(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    reason: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    tid = str(task.get("id") or "")
    if not tid:
        return {"ok": False, "reason": "task id is required"}
    recovered = try_recover_owned_result_before_failure_close(queue, config, task, reason, detail)
    if recovered.get("recovered"):
        clear_delivery_retry(queue, [tid])
        queue.runtime_delete(active_recovery_retry_key(tid))
        return recovered
    now = datetime.now(timezone.utc).isoformat()
    error = (
        "Codex app-server turn ended without owned mobile_result markers after bounded recovery; "
        "manual retry is required."
    )
    with queue.session() as db:
        db.execute(
            """
            UPDATE mobile_tasks
            SET status='failed', error=?, updated_at=?, completed_at=?
            WHERE id=? AND status IN ('sent_to_codex', 'processing')
            """,
            (error, now, now, tid),
        )
    clear_task_codex_runtime(queue, tid)
    clear_delivery_retry(queue, [tid])
    queue.runtime_delete(active_recovery_retry_key(tid))
    queue.add_event(
        "local",
        "app_server_protocol_violation_no_owned_result_manual_required",
        {
            "reason": reason,
            "detail": detail,
            "recovery_attempt": recovered,
            "policy": "do not redeliver app-server indefinitely after repeated owned-result protocol violations; fail closed for manual retry",
        },
        tid,
    )
    send_terminal_failure_notice(queue, task, config, reason, detail)
    return {"ok": True, "task_id": tid, "status": "failed", "error": error}


def defer_app_server_inprogress_no_output_manual_review(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    reason: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    """Record a long-running active turn without converting it to terminal failure."""
    tid = str(task.get("id") or "")
    if not tid:
        return {"ok": False, "reason": "task id is required"}
    recovered = try_recover_owned_result_before_failure_close(queue, config, task, reason, detail)
    if recovered.get("recovered"):
        clear_delivery_retry(queue, [tid])
        queue.runtime_delete(active_recovery_retry_key(tid))
        return recovered
    queue.add_event(
        "local",
        "app_server_inprogress_no_output_manual_review_required",
        {
            "reason": reason,
            "detail": detail,
            "recovery_attempt": recovered,
            "policy": "turn is still inProgress; keep task active/observable and do not fail-close running work",
        },
        tid,
    )
    return {"ok": True, "task_id": tid, "status": str(task.get("status") or ""), "deferred": True}


def latest_task_event_payload(
    queue: MobileQueue,
    task_id: str,
    event_type: str,
) -> dict[str, Any]:
    if not task_id or not event_type:
        return {}
    with queue.session() as db:
        row = db.execute(
            """
            SELECT payload_json
            FROM mobile_events
            WHERE task_id=? AND event_type=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id, event_type),
        ).fetchone()
    if not row:
        return {}
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def latest_task_event_created_at(queue: MobileQueue, task_id: str, event_type: str) -> datetime | None:
    if not task_id or not event_type:
        return None
    with queue.session() as db:
        row = db.execute(
            """
            SELECT created_at
            FROM mobile_events
            WHERE task_id=? AND event_type=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id, event_type),
        ).fetchone()
    return parse_iso_datetime(str(row["created_at"] or "")) if row else None


def task_active_attempt_started_at(queue: MobileQueue, task: dict[str, Any]) -> datetime | None:
    """Best-known start time for the current active Codex attempt."""
    tid = str(task.get("id") or "")
    candidates = [
        parse_iso_datetime(str(task.get("sent_to_codex_at") or "")),
        latest_task_event_created_at(queue, tid, "codex_turn_started"),
        parse_iso_datetime(str(task.get("queued_for_codex_at") or "")),
        parse_iso_datetime(str(task.get("updated_at") or "")),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return None


def app_server_notfound_is_materializing(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    poll: dict[str, Any],
    now: datetime,
) -> tuple[bool, dict[str, Any]]:
    """Classify a fresh app-server turn id that is not readable yet.

    `turn/start` can return a turn id before `thread/turns/list` exposes that
    id. During this bounded window, treating `notFound` as terminal creates a
    redelivery loop. Outside the window, keep the existing terminal handling.
    """
    poll_status = str((poll or {}).get("status") or "").strip().lower()
    if poll_status != "notfound":
        return False, {"reason": "poll_status_not_notfound", "poll_status": poll_status}
    turn_id = str((poll or {}).get("turn_id") or "")
    tid = str(task.get("id") or "")
    event = latest_task_event_payload(queue, tid, "codex_turn_started")
    event_turn_id = str(event.get("turn_id") or "")
    if event_turn_id and turn_id and event_turn_id != turn_id:
        return False, {
            "reason": "turn_id_mismatch",
            "poll_turn_id": turn_id,
            "event_turn_id": event_turn_id,
        }
    started_at = task_active_attempt_started_at(queue, task)
    grace_seconds = app_server_turn_materialization_grace_seconds(config)
    if not started_at:
        return False, {"reason": "missing_attempt_start_time", "grace_seconds": grace_seconds}
    waited_seconds = max(0, int((now - started_at).total_seconds()))
    detail = {
        "reason": "within_materialization_window" if waited_seconds < grace_seconds else "materialization_window_expired",
        "waited_seconds": waited_seconds,
        "grace_seconds": grace_seconds,
        "turn_id": turn_id,
        "event_turn_id": event_turn_id,
        "client_message_id": str(event.get("client_message_id") or ""),
        "started_at": started_at.isoformat(),
    }
    return waited_seconds < grace_seconds, detail


def pending_task_can_trigger_waiting_followup_redelivery(
    queue: MobileQueue,
    pending_task: dict[str, Any],
    active_task_id: str,
) -> bool:
    """Only a new same-thread message may trigger a parked primary redelivery."""
    pending_created = task_created_datetime(pending_task)
    active_task = queue.get_task(active_task_id) or {}
    active_started = (
        parse_iso_datetime(str(active_task.get("sent_to_codex_at") or ""))
        or parse_iso_datetime(str(active_task.get("queued_for_codex_at") or ""))
        or task_created_datetime(active_task)
    )
    waiting_started = latest_task_event_created_at(queue, active_task_id, "active_waiting_followup_redelivery")
    if not active_started and not waiting_started:
        waiting_raw = queue.runtime_get(waiting_followup_redelivery_key(active_task_id))
        try:
            waiting_payload = json.loads(str(waiting_raw or "{}"))
        except json.JSONDecodeError:
            waiting_payload = {}
        if isinstance(waiting_payload, dict):
            active_started = (
                parse_iso_datetime(str(waiting_payload.get("sent_to_codex_at") or ""))
                or parse_iso_datetime(str(waiting_payload.get("updated_at") or ""))
                or parse_iso_datetime(str(waiting_payload.get("created_at") or ""))
            )
    threshold = active_started or waiting_started
    if not pending_created or not threshold:
        return False
    return pending_created > threshold


def task_is_waiting_followup_redelivery(queue: MobileQueue, task_id: str) -> bool:
    if not task_id:
        return False
    if queue.runtime_get(waiting_followup_redelivery_key(task_id)):
        return True
    waiting_id = latest_task_event_id(queue, task_id, "active_waiting_followup_redelivery")
    if waiting_id <= 0:
        return False
    cleared_id = latest_task_event_id(
        queue,
        task_id,
        (
            "active_waiting_followup_redelivery_triggered",
            "active_waiting_followup_redelivery_cleared",
            "active_slot_released_to_pending",
            "queued_for_codex",
            "sent_to_codex",
            "recovery_result_pushed",
            "push_result",
        ),
    )
    return waiting_id > cleared_id


def find_waiting_followup_redelivery_active(
    queue: MobileQueue,
    config: dict[str, Any],
    pending_task: dict[str, Any],
    active_tasks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    pending_id = str(pending_task.get("id") or "")
    pending_user = str(pending_task.get("external_user") or "")
    pending_mode = delivery_mode_for_task(config, pending_task)
    pending_account = receiver_account_id(
        config,
        str(pending_task.get("receiver_account_id") or ""),
        pending_user,
    )
    if pending_mode != "codex-cdp" or pending_account != "primary":
        return None
    for active_task in active_tasks:
        active_id = str(active_task.get("id") or "")
        if not active_id or active_id == pending_id:
            continue
        if str(active_task.get("status") or "") not in {"sent_to_codex", "processing"}:
            continue
        if not task_is_waiting_followup_redelivery(queue, active_id):
            continue
        if not pending_task_can_trigger_waiting_followup_redelivery(queue, pending_task, active_id):
            continue
        if delivery_mode_for_task(config, active_task) != pending_mode:
            continue
        active_user = str(active_task.get("external_user") or "")
        active_account = receiver_account_id(
            config,
            str(active_task.get("receiver_account_id") or ""),
            active_user,
        )
        if active_user == pending_user and active_account == pending_account:
            return active_task
    return None


def reply_to_weixin(
    task: dict[str, Any],
    text: str,
    config: dict[str, Any],
    send: bool,
    media: str | None = None,
) -> dict[str, Any]:
    openclaw = config.get("openclaw", {})
    account_id = receiver_account_id(
        config,
        str(task.get("receiver_account_id") or ""),
        str(task.get("external_user") or ""),
    )
    if not account_id:
        return {"ok": False, "reason": "openclaw.account_id is not configured"}
    node = str(openclaw.get("node_path") or "node")
    state_dir = Path(
        openclaw.get("state_dir")
        or PROJECT_ROOT / "_tools" / "openclaw-codex" / "clean-install" / "state"
    )
    script = Path(
        openclaw.get("weixin_reply_script")
        or PROJECT_ROOT / "_tools" / "openclaw-codex" / "weixin_send_reply.mjs"
    )
    if not script.exists():
        return {"ok": False, "reason": f"weixin reply script not found: {script}"}
    command = [
        node,
        str(script),
        "--state-dir",
        str(state_dir),
        "--account-id",
        account_id,
        "--to",
        str(task.get("external_user") or ""),
        "--text",
        text,
    ]
    if media:
        command.extend(["--media", str(media), "--transport", "gateway"])
    run_id = task_run_id(task)
    if run_id:
        command.extend(["--run-id", run_id])
    context_token = task_context_token(task)
    if context_token:
        command.extend(["--context-token", context_token])
    command.append("--send" if send else "--dry-run")
    try:
        proc = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=max(60, int(openclaw.get("reply_timeout_seconds") or 20)) if media else int(openclaw.get("reply_timeout_seconds") or 20),
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return {"ok": False, "reason": f"weixin reply failed to start: {exc}"}
    try:
        parsed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        parsed = {"ok": False, "raw_stdout": proc.stdout[-1000:]}
    stdout_ok = bool(parsed.get("ok"))
    weixin_ret = parsed.get("weixinRet")
    response = parsed.get("response")
    response_errcode = response.get("errcode") if isinstance(response, dict) else None
    response_errmsg = str(response.get("errmsg") or "") if isinstance(response, dict) else ""
    delivery_accepted = bool(parsed.get("deliveryAccepted"))
    # ret=-2 and non-zero response.errcode are Weixin/OpenClaw business-layer
    # rejections. The transport may still return HTTP 200 and deliveryAccepted.
    accepted = (
        proc.returncode == 0
        and stdout_ok
        and delivery_accepted
        and weixin_ret != -2
        and response_errcode in (None, 0)
    )
    business_error = ""
    if weixin_ret == -2:
        business_error = "weixin_ret_-2"
    elif response_errcode not in (None, 0):
        business_error = f"weixin_errcode_{response_errcode}"
    return {
        "ok": accepted,
        "delivery_accepted": delivery_accepted,
        "phone_visible_confirmed": bool(parsed.get("phoneVisibleConfirmed")),
        "weixin_ret": weixin_ret,
        "weixin_errcode": response_errcode,
        "weixin_errmsg": response_errmsg,
        "business_error": business_error,
        "dry_run": not send,
        "media": str(media or ""),
        "returncode": proc.returncode,
        "stdout": parsed,
        "stderr": (proc.stderr or "")[-1000:],
    }


def weixin_business_ret(reply: dict[str, Any]) -> Any:
    return final_reply_classification.weixin_business_ret(reply)


def weixin_business_errcode(reply: dict[str, Any]) -> Any:
    return final_reply_classification.weixin_business_errcode(reply)


def nested_reply_flag(reply: dict[str, Any], key: str) -> bool:
    return final_reply_classification.nested_reply_flag(reply, key)


def final_reply_context_token_present(task: dict[str, Any], detail: dict[str, Any]) -> bool:
    """Return token presence without exposing token material in diagnostics."""
    if task_context_token(task):
        return True
    return nested_reply_flag(detail, "contextTokenPresent")


def classify_final_reply_waiting_context(
    task: dict[str, Any],
    reason: str,
    detail: dict[str, Any],
    media_info: dict[str, Any],
) -> dict[str, Any]:
    token_present = final_reply_context_token_present(task, detail)
    return final_reply_classification.classify_final_reply_waiting_context(
        token_present=token_present,
        reason=reason,
        media_info=media_info,
    )


def classify_media_send_failure(reply: dict[str, Any]) -> dict[str, Any]:
    return final_reply_classification.classify_media_send_failure(reply)


def final_reply_phone_visible(reply: dict[str, Any]) -> bool:
    return final_reply_classification.final_reply_phone_visible(reply)


def final_reply_delivery_accepted(reply: dict[str, Any]) -> bool:
    return final_reply_classification.final_reply_delivery_accepted(reply)


def task_metadata(task: dict[str, Any]) -> dict[str, Any]:
    metadata: Any = task.get("metadata_json")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata or "{}")
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    return metadata if isinstance(metadata, dict) else {}


def is_dashboard_proxy_task(task: dict[str, Any]) -> bool:
    metadata = task_metadata(task)
    return bool(metadata.get("dashboard_proxy_user"))


def task_run_id(task: dict[str, Any]) -> str:
    metadata = task_metadata(task)
    return str(
        task.get("run_id")
        or task.get("runId")
        or metadata.get("run_id")
        or metadata.get("runId")
        or ""
    )


def task_context_token(task: dict[str, Any]) -> str:
    task_id = str(task.get("id") or "")
    queue = task.get("_queue")
    if task_id and isinstance(queue, MobileQueue):
        value = queue.runtime_get(task_context_token_key(task_id))
        if value:
            return value
        value = queue.runtime_get(pending_reply_context_last_token_key(task_id))
        if value:
            return value
    metadata = task_metadata(task)
    return str(
        task.get("context_token")
        or task.get("contextToken")
        or metadata.get("context_token")
        or metadata.get("contextToken")
        or ""
    )


def normalize_weixin_text_fallback(text: str) -> str:
    """Make final replies friendlier to the OpenClaw/iLink business filter."""
    value = str(text or "")
    replacements = {
        "**": "",
        "`": "",
        "～": "到",
        "~": "到",
        "%": "百分比",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def split_weixin_text(text: str, max_chars: int = 260) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", value) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            sentences = re.split(r"(?<=[。！？.!?])\s*", paragraph)
            piece = ""
            for sentence in [item for item in sentences if item]:
                if piece and len(piece) + len(sentence) + 1 > max_chars:
                    chunks.append(piece)
                    piece = sentence
                else:
                    piece = sentence if not piece else piece + "\n" + sentence
            if piece:
                chunks.append(piece)
            continue
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def extract_outbound_media(text: str) -> tuple[str, list[str]]:
    media: list[str] = []

    def replace_block_marker(match: re.Match[str]) -> str:
        path = match.group("path").strip()
        if path:
            media.append(path)
        return ""

    def replace_line_marker(match: re.Match[str]) -> str:
        path = match.group("path").strip()
        if path:
            media.append(path)
        return ""

    clean = OUTBOUND_MEDIA_RE.sub(replace_block_marker, str(text or ""))
    clean = OUTBOUND_MEDIA_LINE_RE.sub(replace_line_marker, clean)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, media


def prepare_outbound_media(media: str) -> tuple[str | None, dict[str, Any]]:
    path = safe_local_path(media)
    if not path:
        return None, {"ok": False, "reason": "media file not found", "media": media}
    try:
        stat = path.stat()
    except OSError as exc:
        return None, {"ok": False, "reason": f"media stat failed: {exc}", "media": str(path)}
    suffix = path.suffix.lower()
    digest = sha256_file(path)
    info: dict[str, Any] = {
        "ok": True,
        "media": str(path),
        "original_media": str(path),
        "original_name": path.name,
        "size": stat.st_size,
        "sha256": digest,
        "suffix": suffix,
        "packaged": False,
        "ascii_spooled": False,
        "send_name": path.name,
    }
    if is_ascii_safe_filename(path.name):
        return str(path), info

    send_name = ascii_safe_filename(path.name, fallback=f"attachment-{digest[:12]}")
    if "." not in Path(send_name).name and suffix:
        send_name = f"{send_name}{suffix}"
    spool_dir = OUTBOUND_MEDIA_SPOOL_DIR / datetime.now().strftime("%Y%m")
    spool_dir.mkdir(parents=True, exist_ok=True)
    spool_path = spool_dir / f"{Path(send_name).stem}-{digest[:12]}{Path(send_name).suffix}"
    if not spool_path.exists() or spool_path.stat().st_size != stat.st_size:
        shutil.copy2(path, spool_path)
    info.update(
        {
            "media": str(spool_path),
            "send_name": spool_path.name,
            "ascii_spooled": True,
            "spool_path": str(spool_path),
            "policy": "copied to ASCII-safe outbound media spool for Weixin file metadata stability",
        }
    )
    return str(spool_path), info


def reply_to_weixin_with_fallbacks(
    task: dict[str, Any],
    text: str,
    config: dict[str, Any],
    send: bool,
    media: str | None = None,
) -> dict[str, Any]:
    if media:
        first = reply_to_weixin(task, text, config, send, media=media)
        return {"ok": bool(first.get("ok")), "attempts": [{"mode": "media", **first}], "final": first}

    first = reply_to_weixin(task, text, config, send)
    attempts: list[dict[str, Any]] = [{"mode": "original", **first}]
    if send and weixin_business_errcode(first) == -14:
        return {"ok": False, "attempts": attempts, "final": first}
    if first.get("ok") or not send or (weixin_business_ret(first) != -2 and weixin_business_errcode(first) != -14):
        return {"ok": bool(first.get("ok")), "attempts": attempts, "final": first}

    normalized = normalize_weixin_text_fallback(text)
    if normalized and normalized != str(text or "").strip():
        second = reply_to_weixin(task, normalized, config, send)
        attempts.append({"mode": "normalized", **second})
        if second.get("ok"):
            return {"ok": True, "attempts": attempts, "final": second}
        if weixin_business_ret(second) != -2 and weixin_business_errcode(second) != -14:
            return {"ok": False, "attempts": attempts, "final": second}

    chunks = split_weixin_text(normalized or text)
    if len(chunks) <= 1:
        return {"ok": False, "attempts": attempts, "final": attempts[-1]}

    chunk_results: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        prefix = f"({index}/{len(chunks)}) "
        reply = reply_to_weixin(task, prefix + chunk, config, send)
        chunk_result = {"mode": "chunked", "chunk_index": index, "chunk_count": len(chunks), **reply}
        attempts.append(chunk_result)
        chunk_results.append(chunk_result)
        if not reply.get("ok"):
            return {"ok": False, "attempts": attempts, "final": reply, "chunk_results": chunk_results}
    return {"ok": True, "attempts": attempts, "final": chunk_results[-1], "chunk_results": chunk_results}


def final_reply_waiting_reason_from_reply(reply: dict[str, Any]) -> str:
    final = reply.get("final", reply) if isinstance(reply, dict) else {}
    ret = weixin_business_ret(final)
    errcode = weixin_business_errcode(final)
    if ret == -2:
        return "sendmessage_ret_-2"
    if errcode == -14:
        return "sendmessage_errcode_-14"
    return ""


def push_split_text_and_media_final_reply(
    queue: MobileQueue,
    task: dict[str, Any],
    text: str,
    config: dict[str, Any],
    media: str,
    media_info: dict[str, Any],
    account_id: str,
    delays: list[Any],
) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    attempts: list[dict[str, Any]] = []
    text_value = str(text or "").strip()
    max_attempts = 1 + len(delays)
    text_already_accepted = task_event_exists(queue, task_id, "final_reply_text_accepted")
    media_already_accepted = task_event_exists(queue, task_id, "final_reply_media_accepted")

    text_result: dict[str, Any] = {
        "ok": True,
        "skipped": True,
        "delivery_accepted": True,
        "phone_visible_confirmed": False,
        "reason": "final_reply_text_already_accepted",
    }
    if not text_already_accepted:
        text_result = {"ok": False, "reason": "not_attempted"}
        for attempt in range(max_attempts):
            reply = reply_to_weixin_with_fallbacks(task, text_value, config, send=True)
            stage_attempts = reply.get("attempts") if isinstance(reply.get("attempts"), list) else [reply]
            for item in stage_attempts:
                if isinstance(item, dict):
                    attempts.append({"stage": "text", **item})
            if reply.get("ok"):
                text_result = {
                    "ok": True,
                    "delivery_accepted": final_reply_delivery_accepted(reply),
                    "phone_visible_confirmed": final_reply_phone_visible(reply),
                    "attempts": stage_attempts,
                    "final": reply.get("final", reply),
                }
                queue.add_event("wecom", "final_reply_text_accepted", text_result, task_id)
                break
            waiting_reason = final_reply_waiting_reason_from_reply(reply)
            if waiting_reason:
                if waiting_reason == "sendmessage_errcode_-14" or attempt >= len(delays):
                    result = mark_final_reply_waiting_weixin_context(
                        queue,
                        task,
                        account_id,
                        waiting_reason,
                        {"stage": "text", "reply": reply, "attempts": attempts},
                        media_info=media_info,
                    )
                    clear_task_reply_sending(queue, task_id)
                    if task_id:
                        queue.runtime_delete(reply_pending_batch_notice_key(task_id))
                    return result
            if attempt < len(delays):
                time.sleep(max(1, int(delays[attempt] or 1)))
                continue
            text_result = {
                "ok": False,
                "reason": "final_reply_text_failed",
                "stage": "text",
                "reply": reply,
                "attempts": attempts,
                "media_info": media_info,
            }
            queue.add_event("wecom", "final_reply_text_failed", text_result, task_id)
            return text_result

    media_result: dict[str, Any] = {
        "ok": True,
        "skipped": True,
        "delivery_accepted": True,
        "phone_visible_confirmed": False,
        "reason": "final_reply_media_already_accepted",
    }
    if not media_already_accepted:
        media_result = {"ok": False, "reason": "not_attempted"}
        for attempt in range(max_attempts):
            reply = reply_to_weixin(task, "", config, send=True, media=media)
            media_attempt = {"stage": "media", "mode": "media_only", **reply}
            attempts.append(media_attempt)
            if reply.get("ok"):
                media_result = {
                    "ok": True,
                    "delivery_accepted": final_reply_delivery_accepted(reply),
                    "phone_visible_confirmed": final_reply_phone_visible(reply),
                    "attempts": [media_attempt],
                    "final": reply,
                    "media_info": media_info,
                }
                queue.add_event("wecom", "final_reply_media_accepted", media_result, task_id)
                break
            waiting_reason = final_reply_waiting_reason_from_reply(reply)
            if waiting_reason:
                if waiting_reason == "sendmessage_errcode_-14" or attempt >= len(delays):
                    source_reason = f"media_{waiting_reason}" if waiting_reason == "sendmessage_ret_-2" else waiting_reason
                    classification = classify_media_send_failure(reply)
                    queue.add_event(
                        "wecom",
                        "final_reply_media_unconfirmed",
                        {
                            "source_reason": source_reason,
                            "classification": classification,
                            "media_info": media_info,
                            "attempt_count": len(attempts),
                        },
                        task_id,
                    )
                    result = mark_final_reply_waiting_weixin_context(
                        queue,
                        task,
                        account_id,
                        source_reason,
                        {"stage": "media", "reply": reply, "attempts": attempts, "classification": classification},
                        media_info=media_info,
                    )
                    clear_task_reply_sending(queue, task_id)
                    if task_id:
                        queue.runtime_delete(reply_pending_batch_notice_key(task_id))
                    return result
            if attempt < len(delays):
                time.sleep(max(1, int(delays[attempt] or 1)))
                continue
            media_result = {
                "ok": False,
                "reason": "final_reply_media_failed",
                "stage": "media",
                "reply": reply,
                "attempts": attempts,
                "media_info": media_info,
            }
            queue.add_event("wecom", "final_reply_media_failed", media_result, task_id)
            return media_result

    return {
        "ok": bool(text_result.get("ok")) and bool(media_result.get("ok")),
        "delivery_accepted": final_reply_delivery_accepted(text_result) and final_reply_delivery_accepted(media_result),
        "phone_visible_confirmed": final_reply_phone_visible(text_result) and final_reply_phone_visible(media_result),
        "split_delivery": True,
        "already_complete": bool(text_already_accepted and media_already_accepted),
        "text_result": text_result,
        "media_result": media_result,
        "attempts": attempts,
        "final": media_result.get("final", media_result),
        "media_info": media_info,
    }


def send_status_ack_sync(
    queue: MobileQueue,
    task: dict[str, Any],
    text: str,
    config: dict[str, Any],
    event_type: str,
) -> dict[str, Any]:
    openclaw = config.get("openclaw", {})
    account_id = receiver_account_id(
        config,
        str(task.get("receiver_account_id") or ""),
        str(task.get("external_user") or ""),
    )
    circuit = get_weixin_status_ack_circuit(queue, account_id)
    if circuit.get("active"):
        result = {
            "ok": False,
            "skipped": True,
            "reason": "weixin_status_ack_circuit_open",
            "circuit": circuit,
        }
        task_id = str(task.get("id") or "")
        if task_id:
            queue.add_event("wecom", f"{event_type}_skipped_circuit_open", result, task_id)
        return result
    delays = openclaw.get("status_ack_retry_delays_seconds")
    if delays is None:
        delays = []
    if not isinstance(delays, list):
        delays = []
    reply_task = control_reply_task(
        str(task.get("external_user") or ""),
        str(task.get("external_conversation") or ""),
        str(task.get("receiver_account_id") or ""),
        task_run_id(task),
    )
    reply_task["_queue"] = queue
    reply_task["id"] = str(task.get("id") or "")
    attempts: list[dict[str, Any]] = []
    for attempt in range(1 + len(delays)):
        reply = reply_to_weixin(reply_task, text, config, send=True)
        attempts.append(reply)
        if reply.get("ok"):
            break
        ret = weixin_business_ret(reply)
        if ret == -2:
            mark_weixin_status_ack_circuit(
                queue,
                config,
                account_id,
                "sendmessage_ret_-2",
                {"event_type": event_type, "reply": reply},
                str(task.get("id") or ""),
            )
        if ret != -2 or attempt >= len(delays):
            break
        time.sleep(max(1, int(delays[attempt] or 1)))
    reply = attempts[-1] if attempts else {"ok": False, "reason": "no status ack attempt"}
    task_id = str(task.get("id") or "")
    if task_id:
        queue.add_event(
            "wecom",
            event_type,
            {
                "ok": bool(reply.get("ok")),
                "reply": reply,
                "attempts": attempts,
            },
            task_id,
        )
    return reply


def send_status_ack(
    queue: MobileQueue,
    task: dict[str, Any],
    text: str,
    config: dict[str, Any],
    event_type: str,
) -> dict[str, Any]:
    """Fire-and-forget status acknowledgements so they never delay Codex work."""
    task_id = str(task.get("id") or "")
    if should_suppress_supplement_status_ack(queue, task_id, event_type):
        return record_status_ack_suppressed(
            queue,
            task,
            text,
            event_type,
            "supplement_context_status_ack_suppressed",
        )
    if not phone_status_ack_enabled(config, event_type):
        return record_status_ack_suppressed(
            queue,
            task,
            text,
            event_type,
            "phone_status_ack_event_not_enabled",
        )

    if not task_id:
        return send_status_ack_sync(queue, task, text, config, event_type)
    if status_ack_already_sent(queue, task_id, event_type):
        result = {
            "ok": True,
            "suppressed": True,
            "duplicate": True,
            "reason": "status_ack_already_sent",
            "event_type": event_type,
            "text_chars": len(str(text or "")),
        }
        queue.add_event("wecom", f"{event_type}_duplicate_suppressed", result, task_id)
        return result
    reservation = reserve_status_ack_send(queue, task_id, event_type, text)
    if not reservation.get("reserved"):
        result = {
            "ok": True,
            "suppressed": True,
            "duplicate": True,
            "reason": "status_ack_already_sending",
            "event_type": event_type,
            "text_chars": len(str(text or "")),
            "lease": reservation,
        }
        queue.add_event("wecom", f"{event_type}_duplicate_suppressed", result, task_id)
        return result

    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    stdout_path = log_dir / f"status-ack-{stamp}.stdout.log"
    stderr_path = log_dir / f"status-ack-{stamp}.stderr.log"
    config_path = str(config.get("_config_path") or DEFAULT_CONFIG)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--config",
        config_path,
        "status-ack",
        task_id,
        "--event-type",
        event_type,
        "--text",
        text,
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": stdout_path.open("ab"),
        "stderr": stderr_path.open("ab"),
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(command, **kwargs)
    except Exception as exc:
        queue.add_event(
            "wecom",
            f"{event_type}_spawn_failed",
            {"ok": False, "reason": str(exc), "text_chars": len(text)},
            task_id,
        )
        return {"ok": False, "async": True, "spawned": False, "reason": str(exc)}
    finally:
        try:
            kwargs["stdout"].close()
            kwargs["stderr"].close()
        except Exception:
            pass
    queue.add_event(
        "wecom",
        f"{event_type}_spawned",
        {
            "ok": True,
            "pid": proc.pid,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "text_chars": len(text),
        },
        task_id,
    )
    return {"ok": True, "async": True, "spawned": True, "pid": proc.pid}


def spawn_cli_background(
    queue: MobileQueue,
    task_id: str,
    config: dict[str, Any],
    args: list[str],
    log_prefix: str,
    event_type: str,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    stdout_path = log_dir / f"{log_prefix}-{stamp}.stdout.log"
    stderr_path = log_dir / f"{log_prefix}-{stamp}.stderr.log"
    config_path = str(config.get("_config_path") or DEFAULT_CONFIG)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--config",
        config_path,
        *args,
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": stdout_path.open("ab"),
        "stderr": stderr_path.open("ab"),
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(command, **kwargs)
    except Exception as exc:
        clear_task_reply_sending(queue, task_id)
        payload = {"ok": False, "reason": str(exc), **(extra_payload or {})}
        queue.add_event("wecom", f"{event_type}_spawn_failed", payload, task_id)
        return {"ok": False, "async": True, "spawned": False, "reason": str(exc)}
    finally:
        try:
            kwargs["stdout"].close()
            kwargs["stderr"].close()
        except Exception:
            pass
    payload = {
        "ok": True,
        "pid": proc.pid,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        **(extra_payload or {}),
    }
    queue.add_event("wecom", f"{event_type}_spawned", payload, task_id)
    return {"ok": True, "async": True, "spawned": True, "pid": proc.pid}


def send_terminal_failure_notice(
    queue: MobileQueue,
    task: dict[str, Any],
    config: dict[str, Any],
    reason: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a one-shot visible notice when a task finally fail-closes."""
    task_id = str(task.get("id") or "")
    if not task_id:
        return {"ok": False, "reason": "task id is required"}
    event_type = "status_ack_failure_closed"
    if status_ack_already_sent(queue, task_id, event_type):
        result = {
            "ok": True,
            "suppressed": True,
            "duplicate": True,
            "reason": "status_ack_already_sent",
            "event_type": event_type,
            "text_chars": 0,
        }
        queue.add_event("wecom", f"{event_type}_duplicate_suppressed", result, task_id)
        return result
    reservation = reserve_status_ack_send(queue, task_id, event_type, reason)
    if not reservation.get("reserved"):
        result = {
            "ok": True,
            "suppressed": True,
            "duplicate": True,
            "reason": "status_ack_already_sending",
            "event_type": event_type,
            "text_chars": 0,
            "lease": reservation,
        }
        queue.add_event("wecom", f"{event_type}_duplicate_suppressed", result, task_id)
        return result

    reason_text = str(reason or "").strip()
    detail_text = ""
    if detail:
        try:
            detail_text = json.dumps(detail, ensure_ascii=False, sort_keys=True)
        except Exception:
            detail_text = str(detail)
    text = "已收到，但这条消息在 Codex 侧已经终态失败，已停止继续重试。"
    if reason_text:
        text += f" 原因：{reason_text}。"
    if detail_text:
        text += " 详情：" + detail_text

    return send_status_ack_sync(queue, task, text, config, event_type)


def task_event_exists(queue: MobileQueue, task_id: str, event_type: str) -> bool:
    if not task_id or not event_type:
        return False
    with queue.session() as db:
        row = db.execute(
            """
            SELECT 1 FROM mobile_events
            WHERE task_id=? AND event_type=?
            LIMIT 1
            """,
            (task_id, event_type),
        ).fetchone()
    return row is not None


def task_event_recent(queue: MobileQueue, task_id: str, event_type: str, seconds: int) -> bool:
    if not task_id or not event_type:
        return False
    since = (datetime.now(timezone.utc) - timedelta(seconds=max(1, int(seconds)))).isoformat()
    with queue.session() as db:
        row = db.execute(
            """
            SELECT 1 FROM mobile_events
            WHERE task_id=? AND event_type=? AND created_at>=?
            LIMIT 1
            """,
            (task_id, event_type, since),
        ).fetchone()
    return row is not None


def defer_continuation_for_busy_route(
    queue: MobileQueue,
    task: dict[str, Any],
    config: dict[str, Any],
    route_key: str,
    thread_id: str,
    delivery_mode: str,
) -> bool:
    """Record one durable continuation defer event for a pending same-route task."""
    task_id = str(task.get("id") or "")
    if not task_id:
        return False
    if task_event_exists(queue, task_id, "continuation_deferred"):
        return False
    queue.add_event(
        "local",
        "continuation_deferred",
        {
            "route_key": route_key,
            "thread_id": thread_id,
            "delivery_mode": delivery_mode,
            "reason": "same route has an active Codex turn; pending task will be delivered after the active turn completes",
            "policy": "defer_once_then_auto_continue",
        },
        task_id,
    )
    send_status_ack(
        queue,
        task,
        "已收到，你的上一条消息还在处理中；这条补充已暂存，当前回复结束后会自动继续处理。",
        config,
        "status_ack_continuation_deferred",
    )
    return True


PHONE_STATUS_ACK_EVENTS = {
    "status_ack_received",
    "status_ack_confirmation_required",
    "status_ack_dispatching",
    "status_ack_dispatched",
    "status_ack_waiting",
    "status_ack_continuation_deferred",
    "status_ack_attachment_supplement",
    "status_ack_pending_backlog_supplement",
    "status_ack_delivery_group_supplement",
}


def phone_status_ack_enabled(config: dict[str, Any], event_type: str) -> bool:
    openclaw = config.get("openclaw", {})
    configured = openclaw.get("phone_status_ack_events")
    if configured is None:
        allowed = PHONE_STATUS_ACK_EVENTS
    elif isinstance(configured, list):
        allowed = {str(item) for item in configured}
    else:
        allowed = PHONE_STATUS_ACK_EVENTS
    return str(event_type or "") in allowed


def record_status_ack_suppressed(
    queue: MobileQueue,
    task: dict[str, Any],
    text: str,
    event_type: str,
    reason: str,
) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    result = {
        "ok": True,
        "suppressed": True,
        "reason": reason,
        "event_type": event_type,
        "text_chars": len(str(text or "")),
    }
    if task_id:
        add_coalesced_event(
            queue,
            "wecom",
            f"{event_type}_suppressed",
            result,
            task_id,
            signature=f"{event_type}:{reason}",
        )
    return result


def push_final_reply(
    queue: MobileQueue,
    task: dict[str, Any],
    text: str,
    config: dict[str, Any],
    media: str | None = None,
    operation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    openclaw = config.get("openclaw", {})
    account_id = receiver_account_id(
        config,
        str(task.get("receiver_account_id") or ""),
        str(task.get("external_user") or ""),
    )
    task = dict(task)
    task["_queue"] = queue
    extracted_text, media_markers = extract_outbound_media(text)
    if media_markers and not media:
        media = media_markers[0]
        text = extracted_text or "文件已发送，请在微信中查看。"
    media_info: dict[str, Any] = {}
    if media:
        prepared_media, media_info = prepare_outbound_media(media)
        if not prepared_media:
            result = {"ok": False, "reason": "invalid_outbound_media", "media_info": media_info}
            queue.add_event("wecom", "final_reply_media_invalid", result, str(task.get("id") or ""))
            return result
        media = prepared_media
        queue.add_event("wecom", "final_reply_media_prepared", media_info, str(task.get("id") or ""))
    circuit = get_weixin_send_circuit(queue, account_id)
    if circuit.get("active"):
        if circuit.get("reason") == "sendmessage_ret_-2":
            result = mark_final_reply_waiting_weixin_context(
                queue,
                task,
                account_id,
                "weixin_send_circuit_open",
                {"circuit": circuit},
                media_info=media_info,
            )
            return result
        result = {
            "ok": False,
            "skipped": True,
            "reason": "weixin_send_circuit_open",
            "circuit": circuit,
        }
        queue.add_event("wecom", "final_reply_skipped_circuit_open", result, str(task.get("id") or ""))
        return result
    delays = openclaw.get("final_reply_retry_delays_seconds")
    if delays is None:
        delays = [10, 30, 60]
    if not isinstance(delays, list):
        delays = [10, 30, 60]
    if media and str(text or "").strip():
        result = push_split_text_and_media_final_reply(
            queue,
            task,
            text,
            config,
            str(media),
            media_info,
            account_id,
            delays,
        )
        if result.get("ok"):
            if operation:
                result["operation"] = dict(operation)
            clear_completed_task_runtime(queue, str(task.get("id") or ""))
            clear_task_reply_sending(queue, str(task.get("id") or ""))
            if task_id:
                queue.runtime_delete(reply_pending_batch_notice_key(task_id))
            if not result.get("already_complete"):
                queue.add_event("wecom", "final_reply_weixin_accepted", result, str(task.get("id") or ""))
        return result
    attempts: list[dict[str, Any]] = []
    max_attempts = 1 + len(delays)
    for attempt in range(max_attempts):
        reply = reply_to_weixin_with_fallbacks(task, text, config, send=True, media=media)
        attempts.extend(reply.get("attempts") if isinstance(reply.get("attempts"), list) else [reply])
        if reply.get("ok"):
            result = {
                "ok": True,
                "delivery_accepted": final_reply_delivery_accepted(reply),
                "phone_visible_confirmed": final_reply_phone_visible(reply),
                "attempts": attempts,
                "final": reply.get("final", reply),
                "media_info": media_info,
            }
            if operation:
                result["operation"] = dict(operation)
            clear_completed_task_runtime(queue, str(task.get("id") or ""))
            clear_task_reply_sending(queue, str(task.get("id") or ""))
            if task_id:
                queue.runtime_delete(reply_pending_batch_notice_key(task_id))
            queue.add_event("wecom", "final_reply_weixin_accepted", result, str(task.get("id") or ""))
            return result
        ret = weixin_business_ret(reply.get("final", reply))
        errcode = weixin_business_errcode(reply.get("final", reply))
        waiting_reason = "sendmessage_ret_-2" if ret == -2 else ("sendmessage_errcode_-14" if errcode == -14 else "")
        if waiting_reason:
            if errcode == -14 or attempt >= len(delays):
                source_reason = f"media_{waiting_reason}" if media else waiting_reason
                if media:
                    queue.add_event(
                        "wecom",
                        "final_reply_media_unconfirmed",
                        {
                            "source_reason": source_reason,
                            "classification": classify_media_send_failure(reply),
                            "media_info": media_info,
                            "attempt_count": len(attempts),
                        },
                        str(task.get("id") or ""),
                    )
                result = mark_final_reply_waiting_weixin_context(
                    queue,
                    task,
                    account_id,
                    source_reason,
                    {"reply": reply, "attempts": attempts},
                    media_info=media_info,
                )
                clear_task_reply_sending(queue, str(task.get("id") or ""))
                if task_id:
                    queue.runtime_delete(reply_pending_batch_notice_key(task_id))
                return result
            time.sleep(max(1, int(delays[attempt] or 1)))
            continue
        if not waiting_reason or attempt >= len(delays):
            break
        time.sleep(max(1, int(delays[attempt] or 1)))
    result = {"ok": False, "attempts": attempts, "final": attempts[-1] if attempts else {}}
    queue.add_event("wecom", "final_reply_failed", result, str(task.get("id") or ""))
    clear_task_reply_sending(queue, str(task.get("id") or ""))
    if task_id:
        queue.runtime_delete(reply_pending_batch_notice_key(task_id))
    return result


def final_reply_owner_ready(task: dict[str, Any]) -> bool:
    status = str(task.get("status") or "")
    result = str(task.get("result") or "").strip()
    return status in {"done", "failed"} or bool(result)


def guard_final_reply_owner_ready(queue: MobileQueue, task: dict[str, Any]) -> dict[str, Any] | None:
    if final_reply_owner_ready(task):
        return None
    task_id = str(task.get("id") or "")
    result = {
        "ok": False,
        "reason": "final_reply_owner_not_complete",
        "task_id": task_id,
        "status": str(task.get("status") or ""),
        "result_present": bool(str(task.get("result") or "").strip()),
        "push_status": str(task.get("push_status") or ""),
        "policy": "final-reply may only push completed owner results; attachment/direct-send tests must not reuse active mobile task ownership",
    }
    if task_id:
        queue.add_event("local", "final_reply_active_owner_guarded", result, task_id)
    return result


def recover_failed_tasks_with_result_for_reply(
    queue: MobileQueue,
    config: dict[str, Any],
    apply: bool = False,
    task_id: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Recover historical failed tasks that already have a durable result by routing only the result for reply."""
    def durable_reply_evidence_reason(task_row: dict[str, Any]) -> str:
        if task_event_exists(queue, tid, "failure_close_owned_result_recovered"):
            return "failure_close_owned_result_recovered"
        if task_event_exists(queue, tid, "pre_redelivery_owned_result_completed"):
            return "pre_redelivery_owned_result_completed"
        if task_event_exists(queue, tid, "thread_history_owned_result_recovered"):
            return "thread_history_owned_result_recovered"
        return ""

    def recover_result_from_thread_history(task_row: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        row_tid = str(task_row.get("id") or "")
        thread_id = str(task_row.get("codex_thread_id") or "")
        if not row_tid or not thread_id:
            return "", "", {}
        attempts = recent_codex_turn_protocol_attempts(queue, row_tid, [row_tid], limit=8)
        for attempt in attempts:
            turn_id = str(attempt.get("turn_id") or "")
            client_message_id = str(attempt.get("client_message_id") or "")
            expected_task_ids = [str(item) for item in attempt.get("expected_task_ids") or [] if str(item)]
            expected_result_codes = {
                str(key): str(value)
                for key, value in (attempt.get("expected_result_codes") or {}).items()
                if str(key) and str(value)
            }
            expected_ack_codes = {
                str(key): str(value)
                for key, value in (attempt.get("expected_ack_codes") or {}).items()
                if str(key) and str(value)
            }
            if not turn_id or not expected_result_codes:
                continue
            poll = poll_codex_thread_history_owned_result(
                config,
                thread_id,
                turn_id,
                client_message_id,
                expected_task_ids or [row_tid],
                expected_result_codes,
                expected_ack_codes,
            )
            text = strip_mobile_result_markers(str(poll.get("newText") or "").strip())
            if not is_usable_owned_result_text(text):
                text = ""
            ownership = poll.get("ownership") if isinstance(poll.get("ownership"), dict) else {}
            complete = bool(poll.get("result_complete")) or bool(ownership.get("result_complete"))
            if text and complete and not poll_has_ownership_mismatch(poll):
                return text, "thread_history_owned_result", {
                    "poll": poll,
                    "attempt": attempt,
                    "thread_id": thread_id,
                }
        return "", "", {}

    params: list[Any] = []
    where = [
        "status='failed'",
        "pushed_at IS NULL",
        "COALESCE(push_status, '') = ''",
        "COALESCE(external_user, '') <> ''",
        "COALESCE(receiver_account_id, '') <> ''",
    ]
    if task_id:
        where.append("id=?")
        params.append(task_id)
    query = f"""
        SELECT id, source, external_user, external_conversation, command,
               risk_level, status, text, result, error, push_status, pushed_at,
               receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at, completed_at
        FROM mobile_tasks
        WHERE {' AND '.join(where)}
        ORDER BY updated_at ASC
        LIMIT ?
    """
    params.append(max(1, int(limit)))
    with queue.session() as db:
        rows = db.execute(query, tuple(params)).fetchall()

    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    recovered: list[dict[str, Any]] = []

    for row in rows:
        task = dict(row)
        tid = str(task.get("id") or "")
        result_text = str(task.get("result") or "").strip()
        if not tid:
            continue
        recovered_result_source = ""
        recovered_result_detail: dict[str, Any] = {}
        if not result_text:
            result_text, recovered_result_source, recovered_result_detail = recover_result_from_thread_history(task)
        if not result_text:
            skipped.append({"task_id": tid, "reason": "missing_result_text_and_thread_history_owned_result"})
            continue
        if result_text.startswith("[supplement]"):
            skipped.append({"task_id": tid, "reason": "internal_supplement_result"})
            continue
        if task_is_supplement_context(queue, tid) and not task_owns_final_reply(queue, tid):
            skipped.append({"task_id": tid, "reason": "supplement_member_not_final_reply_owner"})
            continue
        if task_has_completed_final_reply_evidence(queue, tid, task):
            skipped.append({"task_id": tid, "reason": "completed_reply_evidence_already_present"})
            continue
        if result_looks_like_failed_transport_error(result_text) and not failed_result_has_recoverable_reply_text(task):
            skipped.append({"task_id": tid, "reason": "error_like_result_text"})
            continue
        evidence_reason = recovered_result_source or durable_reply_evidence_reason(task)
        if not evidence_reason:
            skipped.append({"task_id": tid, "reason": "missing_durable_owned_result_evidence"})
            continue
        candidates.append({
            "task_id": tid,
            "external_user": str(task.get("external_user") or ""),
            "receiver_account_id": str(task.get("receiver_account_id") or ""),
            "result_chars": len(result_text),
            "updated_at": str(task.get("updated_at") or ""),
            "evidence_reason": evidence_reason,
        })
        if not apply:
            continue

        if recovered_result_source:
            with queue.session() as db:
                db.execute(
                    """
                    UPDATE mobile_tasks
                    SET result=?, updated_at=?
                    WHERE id=? AND status='failed' AND COALESCE(result, '') = ''
                    """,
                    (result_text, utc_now(), tid),
                )
            task = queue.get_task(tid) or task
            queue.add_event(
                "local",
                "thread_history_owned_result_recovered",
                {
                    "source_status": "failed",
                    "result_chars": len(result_text),
                    "detail": recovered_result_detail,
                    "policy": "recover failed task result only from exact owned mobile_result markers in durable Codex thread history",
                },
                tid,
            )

        detail = {
            "ok": False,
            "recoverable": True,
            "reason": "waiting_weixin_context",
            "source_reason": "recovered_failed_task_with_result",
            "account_id": receiver_account_id(
                config,
                str(task.get("receiver_account_id") or ""),
                str(task.get("external_user") or ""),
            ),
            "external_user": str(task.get("external_user") or ""),
            "next_step": "retry through reply_pending batch notice and context recovery",
        }
        queue.mark_reply_pending(tid, json.dumps(detail, ensure_ascii=False))
        set_pending_reply_context_last_token(queue, tid, str(task_context_token(task) or ""))
        now = utc_now()
        with queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='done',
                    error='',
                    push_status='reply_pending',
                    updated_at=?
                WHERE id=? AND status='failed' AND COALESCE(result, '') <> ''
                """,
                (now, tid),
            )
        queue.runtime_delete(reply_pending_batch_notice_key(tid))
        queue.add_event(
            "local",
            "historical_failed_result_recovered_for_reply",
            {
                "source_status": "failed",
                "target_status": "done",
                "reply_path": "reply_pending",
                "evidence_reason": evidence_reason,
                "detail": detail,
            },
            tid,
        )
        queue.add_event("wecom", "final_reply_waiting_weixin_context", detail, tid)
        queue.add_event("wecom", "completed_reply_reconciled_to_reply_pending", detail, tid)
        recovered.append({
            "task_id": tid,
            "status": "done",
            "push_status": "reply_pending",
            "result_chars": len(result_text),
        })

    return {
        "ok": True,
        "apply": bool(apply),
        "task_id": task_id or "",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "recovered_count": len(recovered),
        "recovered": recovered,
        "skipped": skipped,
    }


def audit_failed_tasks_with_result_for_reply(
    queue: MobileQueue,
    task_id: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Read-only audit for failed tasks with result text and their current recovery eligibility."""
    def durable_reply_evidence_reason(task_row: dict[str, Any], tid: str) -> str:
        if task_event_exists(queue, tid, "failure_close_owned_result_recovered"):
            return "failure_close_owned_result_recovered"
        if task_event_exists(queue, tid, "pre_redelivery_owned_result_completed"):
            return "pre_redelivery_owned_result_completed"
        if task_event_exists(queue, tid, "thread_history_owned_result_recovered"):
            return "thread_history_owned_result_recovered"
        return ""

    def audit_thread_history_result(task_row: dict[str, Any], tid: str) -> tuple[str, str]:
        if not task_id:
            return "", ""
        thread_id = str(task_row.get("codex_thread_id") or "")
        if not tid or not thread_id:
            return "", ""
        attempts = recent_codex_turn_protocol_attempts(queue, tid, [tid], limit=8)
        for attempt in attempts:
            turn_id = str(attempt.get("turn_id") or "")
            client_message_id = str(attempt.get("client_message_id") or "")
            expected_task_ids = [str(item) for item in attempt.get("expected_task_ids") or [] if str(item)]
            expected_result_codes = {
                str(key): str(value)
                for key, value in (attempt.get("expected_result_codes") or {}).items()
                if str(key) and str(value)
            }
            expected_ack_codes = {
                str(key): str(value)
                for key, value in (attempt.get("expected_ack_codes") or {}).items()
                if str(key) and str(value)
            }
            if not turn_id or not expected_result_codes:
                continue
            poll = poll_codex_thread_history_owned_result(
                load_config(DEFAULT_CONFIG),
                thread_id,
                turn_id,
                client_message_id,
                expected_task_ids or [tid],
                expected_result_codes,
                expected_ack_codes,
            )
            text = strip_mobile_result_markers(str(poll.get("newText") or "").strip())
            if not is_usable_owned_result_text(text):
                text = ""
            ownership = poll.get("ownership") if isinstance(poll.get("ownership"), dict) else {}
            complete = bool(poll.get("result_complete")) or bool(ownership.get("result_complete"))
            if text and complete and not poll_has_ownership_mismatch(poll):
                return text, "thread_history_owned_result"
        return "", ""

    params: list[Any] = []
    where = [
        "status='failed'",
        "COALESCE(external_user, '') <> ''",
        "COALESCE(receiver_account_id, '') <> ''",
    ]
    if not task_id:
        where.append("COALESCE(result, '') <> ''")
    if task_id:
        where.append("id=?")
        params.append(task_id)
    query = f"""
        SELECT id, source, external_user, external_conversation, command,
               risk_level, status, text, result, error, push_status, pushed_at,
               receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at, completed_at
        FROM mobile_tasks
        WHERE {' AND '.join(where)}
        ORDER BY updated_at ASC
        LIMIT ?
    """
    params.append(max(1, int(limit)))
    with queue.session() as db:
        rows = db.execute(query, tuple(params)).fetchall()

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in rows:
        task = dict(row)
        tid = str(task.get("id") or "")
        result_text = str(task.get("result") or "").strip()
        history_evidence = ""
        if tid and not result_text:
            result_text, history_evidence = audit_thread_history_result(task, tid)
        if not tid or not result_text:
            continue
        reason = ""
        evidence_reason = history_evidence or durable_reply_evidence_reason(task, tid)
        if result_text.startswith("[supplement]"):
            reason = "internal_supplement_result"
        elif task_is_supplement_context(queue, tid) and not task_owns_final_reply(queue, tid):
            reason = "supplement_member_not_final_reply_owner"
        elif task_has_completed_final_reply_evidence(queue, tid, task):
            reason = "completed_reply_evidence_already_present"
        elif str(task.get("pushed_at") or "").strip():
            reason = "already_pushed"
        elif str(task.get("push_status") or "").strip():
            reason = f"push_status:{str(task.get('push_status') or '').strip()}"
        elif result_looks_like_failed_transport_error(result_text) and not failed_result_has_recoverable_reply_text(task):
            reason = "error_like_result_text"
        elif not evidence_reason:
            reason = "missing_durable_owned_result_evidence"

        item = {
            "task_id": tid,
            "external_user": str(task.get("external_user") or ""),
            "receiver_account_id": str(task.get("receiver_account_id") or ""),
            "status": str(task.get("status") or ""),
            "push_status": str(task.get("push_status") or ""),
            "pushed_at": str(task.get("pushed_at") or ""),
            "result_chars": len(result_text),
            "result_preview": result_text[:160],
            "updated_at": str(task.get("updated_at") or ""),
            "evidence_reason": evidence_reason,
        }
        if reason:
            item["excluded_reason"] = reason
            excluded.append(item)
        else:
            eligible.append(item)

    return {
        "ok": True,
        "task_id": task_id or "",
        "limit": max(1, int(limit)),
        "eligible_count": len(eligible),
        "eligible": eligible,
        "excluded_count": len(excluded),
        "excluded": excluded,
    }


def owned_result_correction_operation_id(task_id: str, candidate_hash: str) -> str:
    return sha256_text(f"owned-result-correction/v1:{str(task_id or '').strip()}:{str(candidate_hash or '').strip()}")


def owned_result_correction_operation_state(queue: MobileQueue, task_id: str, operation_id: str) -> dict[str, Any]:
    """Read durable correction intent and receipt evidence for one immutable operation."""
    tid = str(task_id or "").strip()
    op_id = str(operation_id or "").strip()
    state = {"intent": False, "sender_accepted": False, "accepted": False, "event_ids": []}
    if not tid or not op_id:
        return state
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, event_type, payload_json
            FROM mobile_events
            WHERE task_id=?
              AND event_type IN (
                'owned_result_correction_intent',
                'owned_result_corrective_reply_accepted',
                'final_reply_weixin_accepted'
              )
            ORDER BY id ASC
            """,
            (tid,),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        operation = payload.get("operation") if isinstance(payload.get("operation"), dict) else payload
        if str(operation.get("operation_id") or "") != op_id:
            continue
        state["event_ids"].append(int(row["id"]))
        event_type = str(row["event_type"] or "")
        if event_type == "owned_result_correction_intent":
            state["intent"] = True
        elif event_type == "final_reply_weixin_accepted" and bool(payload.get("delivery_accepted")):
            state["sender_accepted"] = True
        elif event_type == "owned_result_corrective_reply_accepted":
            state["accepted"] = True
    return state


def finalize_owned_result_correction(
    queue: MobileQueue,
    task_id: str,
    candidate_text: str,
    candidate_hash: str,
    source: dict[str, Any],
    operation_id: str,
    *,
    reconciled_sender_receipt: bool = False,
    reply: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a correction known to have an accepted sender receipt; never sends."""
    tid = str(task_id or "").strip()
    if not tid or not candidate_text or not candidate_hash or not operation_id:
        return {"ok": False, "reason": "correction_finalize_input_invalid"}
    now = utc_now()
    with queue.session() as db:
        db.execute(
            """
            UPDATE mobile_tasks
            SET result=?, status='pushed_to_wecom', push_status='pushed_to_wecom',
                error='', completed_at=COALESCE(completed_at, ?), updated_at=?
            WHERE id=? AND status='pushed_to_wecom'
            """,
            (candidate_text, now, now, tid),
        )
        changed = int(db.execute("SELECT changes() AS n").fetchone()["n"] or 0)
    if changed != 1:
        return {"ok": False, "reason": "correction_task_state_changed_before_finalize", "task_id": tid}
    queue.runtime_delete(owned_result_consume_key(tid))
    state = owned_result_correction_operation_state(queue, tid, operation_id)
    if not state.get("accepted"):
        queue.add_event(
            "wecom",
            "owned_result_corrective_reply_accepted",
            {
                "operation_id": operation_id,
                "candidate_sha256": candidate_hash,
                "candidate_chars": len(candidate_text),
                "source": source,
                "delivery_accepted": True,
                "reconciled_sender_receipt": bool(reconciled_sender_receipt),
                "policy": "single immutable correction operation; sender receipt is authoritative",
            },
            tid,
        )
    return {
        "ok": True,
        "task_id": tid,
        "delivery_accepted": True,
        "result_chars": len(candidate_text),
        "result_sha256": candidate_hash,
        "operation_id": operation_id,
        "reconciled_sender_receipt": bool(reconciled_sender_receipt),
        "source": source,
        "reply": reply or {},
    }


def audit_owned_result_recovery(queue: MobileQueue, task_id: str) -> dict[str, Any]:
    """Audit one exact pushed-placeholder task against bound session-store evidence."""
    tid = str(task_id or "").strip()
    task = queue.get_task(tid) or {}
    if not tid or not task:
        return {"ok": False, "read_only": True, "task_id": tid, "reason": "task_not_found"}

    attempts = recent_codex_turn_protocol_attempts(queue, tid, [tid], limit=8)
    candidate: dict[str, Any] = {}
    protocol: dict[str, str] = {}
    for attempt in attempts:
        result_code = str((attempt.get("expected_result_codes") or {}).get(tid) or "").strip()
        ack_code = str((attempt.get("expected_ack_codes") or {}).get(tid) or "").strip()
        if not result_code:
            continue
        found = find_codex_session_owned_result(
            tid,
            result_code,
            ack_code,
            created_at=str(task.get("created_at") or ""),
            expected_turn_id=str(attempt.get("turn_id") or ""),
        )
        if not bool(found.get("ok")):
            return {
                "ok": False,
                "read_only": True,
                "task_id": tid,
                "reason": str(found.get("reason") or "session_store_recovery_failed"),
                "session_store": found,
            }
        if is_usable_owned_result_text(str(found.get("newText") or "")):
            candidate = found
            protocol = {"result_code": result_code, "ack_code": ack_code}
            break

    candidate_text = str(candidate.get("newText") or "").strip()
    candidate_source = candidate.get("source") if isinstance(candidate.get("source"), dict) else {}
    candidate_hash = str(candidate_source.get("sha256") or (sha256_text(candidate_text) if candidate_text else ""))
    current_text = strip_mobile_result_markers(str(task.get("result") or "").strip())
    current_usable = is_usable_owned_result_text(current_text)
    current_hash = sha256_text(current_text) if current_text else ""
    operation_id = owned_result_correction_operation_id(tid, candidate_hash) if candidate_hash else ""
    operation_state = owned_result_correction_operation_state(queue, tid, operation_id)
    status = str(task.get("status") or "")
    pushed = status == "pushed_to_wecom" or str(task.get("push_status") or "") == "pushed_to_wecom"
    mode = "evidence_only"
    reason = ""
    eligible = False
    if not candidate_text:
        reason = "exact_owned_result_not_found"
    elif current_usable and current_hash == candidate_hash:
        reason = "task_already_contains_exact_result"
    elif operation_state.get("accepted"):
        reason = "corrective_reply_already_accepted"
    elif operation_state.get("sender_accepted"):
        mode = "finalize_accepted_correction"
        eligible = True
        reason = "sender_receipt_needs_local_finalize"
    elif operation_state.get("intent"):
        reason = "correction_send_outcome_unknown_manual_review_required"
    elif not pushed:
        reason = f"apply_restricted_to_pushed_placeholder:{status}"
    elif current_usable:
        reason = "existing_pushed_result_is_not_a_known_placeholder"
    elif not str(task.get("external_user") or "") or not str(task.get("receiver_account_id") or ""):
        reason = "reply_route_identity_missing"
    else:
        mode = "corrective_reply"
        eligible = True
        reason = "exact_owned_result_correction_ready"

    return {
        "ok": True,
        "read_only": True,
        "task_id": tid,
        "eligible": eligible,
        "reason": reason,
        "mode": mode,
        "task_status": status,
        "push_status": str(task.get("push_status") or ""),
        "current_result_chars": len(current_text),
        "current_result_usable": current_usable,
        "current_result_sha256": current_hash,
        "candidate_result_chars": len(candidate_text),
        "candidate_result_sha256": candidate_hash,
        "candidate_preview": candidate_text[:160],
        "source": candidate_source,
        "protocol": protocol,
        "operation_id": operation_id,
        "operation_state": operation_state,
        "duplicate_copies": candidate.get("duplicate_copies"),
    }


def recover_owned_result(
    queue: MobileQueue,
    config: dict[str, Any],
    task_id: str,
    *,
    apply: bool = False,
    confirm: str = "",
    expected_sha256: str = "",
) -> dict[str, Any]:
    """Finalize one immutable correction safely; unknown prior send outcomes never resend."""
    audit = audit_owned_result_recovery(queue, task_id)
    if not bool(audit.get("ok")) or not bool(audit.get("eligible")):
        return {**audit, "applied": False}
    if not apply:
        return {**audit, "applied": False, "dry_run": True}
    if str(confirm or "") != "CORRECT-OWNED-RESULT":
        return {"ok": False, "applied": False, "task_id": str(task_id or ""), "reason": "confirmation_required"}
    if str(expected_sha256 or "") != str(audit.get("candidate_result_sha256") or ""):
        return {"ok": False, "applied": False, "task_id": str(task_id or ""), "reason": "candidate_hash_confirmation_mismatch"}

    tid = str(task_id or "").strip()
    protocol = audit.get("protocol") if isinstance(audit.get("protocol"), dict) else {}
    task = queue.get_task(tid) or {}
    candidate = find_codex_session_owned_result(
        tid,
        str(protocol.get("result_code") or ""),
        str(protocol.get("ack_code") or ""),
        created_at=str(task.get("created_at") or ""),
    )
    candidate_text = str(candidate.get("newText") or "").strip()
    source = candidate.get("source") if isinstance(candidate.get("source"), dict) else {}
    candidate_hash = str(source.get("sha256") or (sha256_text(candidate_text) if candidate_text else ""))
    if not bool(candidate.get("ok")) or not is_usable_owned_result_text(candidate_text):
        return {"ok": False, "applied": False, "task_id": tid, "reason": str(candidate.get("reason") or "exact_owned_result_changed_before_apply")}
    if candidate_hash != str(audit.get("candidate_result_sha256") or ""):
        return {"ok": False, "applied": False, "task_id": tid, "reason": "candidate_changed_after_audit"}

    operation_id = str(audit.get("operation_id") or "")
    if str(audit.get("mode") or "") == "finalize_accepted_correction":
        return finalize_owned_result_correction(
            queue,
            tid,
            candidate_text,
            candidate_hash,
            source,
            operation_id,
            reconciled_sender_receipt=True,
        )
    if str(audit.get("mode") or "") != "corrective_reply":
        return {**audit, "ok": False, "applied": False, "reason": "unsupported_recovery_mode"}

    reservation = reserve_task_reply_send(queue, tid, candidate_text)
    if not reservation.get("reserved"):
        return {
            "ok": True,
            "applied": False,
            "task_id": tid,
            "reason": "correction_send_in_progress",
            "duplicate": True,
            "lease": reservation,
        }
    try:
        fresh = audit_owned_result_recovery(queue, tid)
        if (
            not bool(fresh.get("eligible"))
            or str(fresh.get("mode") or "") != "corrective_reply"
            or str(fresh.get("candidate_result_sha256") or "") != candidate_hash
            or str(fresh.get("operation_id") or "") != operation_id
        ):
            return {"ok": False, "applied": False, "task_id": tid, "reason": "task_or_candidate_changed_before_send", "audit": fresh}
        queue.add_event(
            "local",
            "owned_result_correction_intent",
            {
                "operation_id": operation_id,
                "candidate_sha256": candidate_hash,
                "candidate_chars": len(candidate_text),
                "source": source,
                "policy": "intent is durable before send; an unreceipted intent fails closed and is never automatically resent",
            },
            tid,
        )
        current_task = queue.get_task(tid) or {}
        reply = push_final_reply(
            queue,
            current_task,
            candidate_text,
            config,
            operation={
                "operation_id": operation_id,
                "kind": "owned_result_correction",
                "candidate_sha256": candidate_hash,
            },
        )
        if not (bool(reply.get("ok")) and final_reply_delivery_accepted(reply)):
            queue.add_event(
                "wecom",
                "owned_result_corrective_reply_failed",
                {"operation_id": operation_id, "candidate_sha256": candidate_hash, "reply": reply},
                tid,
            )
            return {"ok": False, "applied": True, "task_id": tid, "reason": "weixin_sender_did_not_accept_owned_result", "reply": reply}
        return finalize_owned_result_correction(queue, tid, candidate_text, candidate_hash, source, operation_id, reply=reply)
    except Exception as exc:
        queue.add_event(
            "local",
            "owned_result_correction_exception_after_intent",
            {"operation_id": operation_id, "candidate_sha256": candidate_hash, "error": str(exc)},
            tid,
        )
        return {"ok": False, "applied": True, "task_id": tid, "reason": "correction_outcome_unknown_manual_review_required"}
    finally:
        clear_task_reply_sending(queue, tid)


def historical_failed_result_recovery_help() -> dict[str, Any]:
    return {
        "ok": True,
        "read_only": True,
        "topic": "historical_failed_result_recovery",
        "recommended_flow": [
            {
                "step": 1,
                "name": "audit_all",
                "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py audit-failed-result-replies",
                "purpose": "List failed tasks with result text and see why each task is eligible or excluded.",
            },
            {
                "step": 2,
                "name": "audit_one_task",
                "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py audit-failed-result-replies --task-id <task_id>",
                "purpose": "Inspect one task before any recovery action.",
            },
            {
                "step": 3,
                "name": "dry_run_one_task",
                "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py recover-failed-result-replies --task-id <task_id>",
                "purpose": "Confirm whether the selected task is currently recoverable under the strict filter.",
            },
            {
                "step": 4,
                "name": "apply_one_task",
                "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py recover-failed-result-replies --task-id <task_id> --apply",
                "purpose": "Apply recovery for a single explicit task id only.",
            },
            {
                "step": 5,
                "name": "verify_queue",
                "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py maintenance summary",
                "purpose": "Verify there is no unexpected pending or active work after recovery.",
            },
        ],
        "guardrails": [
            "Historical recovery apply is restricted to a single explicit task id.",
            "Eligibility requires durable positive evidence; result text alone is not enough.",
            "Known transport or dispatch error text is excluded from recovery.",
            "Audit is read-only and should be run before any apply.",
        ],
        "anti_patterns": [
            "Do not run bulk apply for historical failed-result recovery.",
            "Do not treat error text in result as a recoverable final reply.",
            "Do not bypass audit when the task history is unclear.",
        ],
    }


def mark_final_reply_waiting_weixin_context(
    queue: MobileQueue,
    task: dict[str, Any],
    account_id: str,
    reason: str,
    detail: dict[str, Any],
    media_info: dict[str, Any],
) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    classification = classify_final_reply_waiting_context(task, reason, detail, media_info)
    result = {
        "ok": False,
        "recoverable": True,
        "reason": "waiting_weixin_context",
        "source_reason": reason,
        "diagnostic_category": classification["diagnostic_category"],
        "context_token_present": classification["context_token_present"],
        "fresh_inbound_required": classification["fresh_inbound_required"],
        "delivery_stage": classification["delivery_stage"],
        "account_id": account_id,
        "external_user": str(task.get("external_user") or ""),
        "media_info": media_info,
        "detail": detail,
        "next_step": classification["next_step"],
    }
    if task_id:
        queue.mark_reply_pending(task_id, json.dumps(result, ensure_ascii=False))
        set_pending_reply_context_last_token(queue, task_id, str(task_context_token(task) or ""))
        with queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status=CASE WHEN status='push_failed' THEN 'done' ELSE status END,
                    error=CASE WHEN status='push_failed' THEN '' ELSE error END
                WHERE id=?
                """,
                (task_id,),
            )
        clear_task_reply_sending(queue, task_id)
        queue.add_event("wecom", "final_reply_waiting_weixin_context", result, task_id)
    return result


def mark_final_reply_visibility_unconfirmed(
    queue: MobileQueue,
    task: dict[str, Any],
    account_id: str,
    detail: dict[str, Any],
    media_info: dict[str, Any],
) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    result = {
        "ok": True,
        "delivery_accepted": True,
        "phone_visible_confirmed": False,
        "reason": "delivery_accepted_without_visibility_confirmation",
        "account_id": account_id,
        "external_user": str(task.get("external_user") or ""),
        "media_info": media_info,
        "detail": detail,
        "push_status_recorded": bool(task_id),
        "next_step": "recorded once; replay only on later explicit delivery failure",
    }
    if task_id:
        queue.mark_pushed(task_id, True, json.dumps(result, ensure_ascii=False))
        clear_completed_task_runtime(queue, task_id)
        clear_task_reply_sending(queue, task_id)
        queue.runtime_delete(reply_pending_batch_notice_key(task_id))
        queue.add_event("wecom", "final_reply_visibility_unconfirmed", result, task_id)
    return result


def push_final_reply_async(
    queue: MobileQueue,
    task: dict[str, Any],
    text: str,
    config: dict[str, Any],
    media: str | None = None,
) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    if not task_id:
        return {"ok": False, "async": True, "reason": "task id is required"}
    if task_event_exists(queue, task_id, "final_reply_weixin_accepted") or task_event_exists(queue, task_id, "push_result"):
        result = {"ok": True, "async": True, "suppressed": True, "duplicate": True, "reason": "final_reply_already_sent_or_accepted"}
        queue.add_event("wecom", "final_reply_duplicate_suppressed", result, task_id)
        return result
    reservation = reserve_task_reply_send(queue, task_id, text, str(media or ""))
    if not reservation.get("reserved"):
        result = {
            "ok": True,
            "async": True,
            "suppressed": True,
            "duplicate": True,
            "reason": "final_reply_already_sending",
            "lease": reservation,
        }
        queue.add_event("wecom", "final_reply_duplicate_suppressed", result, task_id)
        return result
    payload_dir = ROOT / "logs" / "final-reply-payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    payload_path = payload_dir / f"{task_id}-{stamp}.txt"
    payload_path.write_text(text, encoding="utf-8")
    command = [
        "final-reply",
        task_id,
        "--text-file",
        str(payload_path),
    ]
    if media:
        command.extend(["--media", str(media)])
    return spawn_cli_background(
        queue,
        task_id,
        config,
        command,
        "final-reply",
        "final_reply",
        {"text_chars": len(text), "payload": str(payload_path), "media": str(media or "")},
    )


def latency_report(queue: MobileQueue, limit: int = 30) -> dict[str, Any]:
    def seconds_between(start: str | None, end: str | None) -> float | None:
        start_dt = parse_iso_datetime(start)
        end_dt = parse_iso_datetime(end)
        if not start_dt or not end_dt:
            return None
        return round((end_dt - start_dt).total_seconds(), 3)

    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, external_user, status, push_status, created_at,
                   queued_for_codex_at, sent_to_codex_at, completed_at,
                   pushed_at, error
            FROM mobile_tasks
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    items = []
    buckets: dict[str, list[float]] = {
        "created_to_queued_s": [],
        "queued_to_sent_s": [],
        "sent_to_done_s": [],
        "done_to_pushed_s": [],
    }
    for row in rows:
        item = dict(row)
        item["created_to_queued_s"] = seconds_between(item.get("created_at"), item.get("queued_for_codex_at"))
        item["queued_to_sent_s"] = seconds_between(item.get("queued_for_codex_at"), item.get("sent_to_codex_at"))
        item["sent_to_done_s"] = seconds_between(item.get("sent_to_codex_at"), item.get("completed_at"))
        item["done_to_pushed_s"] = seconds_between(item.get("completed_at"), item.get("pushed_at"))
        item["likely_bottleneck"] = "none"
        for key in ("done_to_pushed_s", "queued_to_sent_s", "sent_to_done_s", "created_to_queued_s"):
            value = item.get(key)
            if isinstance(value, (int, float)) and value >= 20:
                item["likely_bottleneck"] = key
                break
        for key in buckets:
            value = item.get(key)
            if isinstance(value, (int, float)):
                buckets[key].append(float(value))
        if item.get("error"):
            item["error"] = str(item["error"])[:300]
        items.append(item)

    summary: dict[str, dict[str, float | int]] = {}
    for key, values in buckets.items():
        if not values:
            continue
        values = sorted(values)
        summary[key] = {
            "n": len(values),
            "min": round(values[0], 3),
            "median": round(values[len(values) // 2], 3),
            "max": round(values[-1], 3),
        }
    return {
        "ok": True,
        "read_only": True,
        "limit": max(1, int(limit)),
        "summary": summary,
        "items": items,
    }


def recent_task_event_payloads(
    queue: MobileQueue,
    task_id: str,
    event_type: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not task_id or not event_type:
        return []
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT payload_json, created_at
            FROM mobile_events
            WHERE task_id=? AND event_type=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (task_id, event_type, max(1, int(limit))),
        ).fetchall()
    payloads: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        payload = dict(payload)
        payload["_event_created_at"] = str(row["created_at"] or "")
        payloads.append(payload)
    return payloads


def pending_tasks_for_delivery_batch(queue: MobileQueue, client_message_id: str) -> list[dict[str, Any]]:
    batch_id = str(client_message_id or "")
    if not batch_id:
        return []
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT DISTINCT e.task_id
            FROM mobile_events e
            JOIN mobile_tasks t ON t.id=e.task_id
            WHERE e.event_type='delivery_failed_reverted_to_pending'
              AND e.payload_json LIKE ?
              AND t.status='pending'
            ORDER BY t.created_at ASC, t.id ASC
            """,
            (f"%{batch_id}%",),
        ).fetchall()
    tasks: list[dict[str, Any]] = []
    for row in rows:
        task = queue.get_task(str(row["task_id"] or ""))
        if task and str(task.get("status") or "") == "pending":
            tasks.append(task)
    return tasks


def recover_pending_visible_cdp_unconfirmed_results(
    queue: MobileQueue,
    config: dict[str, Any],
    pending: list[dict[str, Any]],
    max_checks: int = 5,
) -> dict[str, Any]:
    """Recover pending tasks left by old visible-CDP unconfirmed-submit rollbacks."""
    recoverable_reasons = {
        "cdp_visible_input_not_confirmed",
        "cdp_visible_input_unconfirmed",
        "cdp_visible_input_unconfirmed_observing",
        "cdp_visible_submission_needs_attention",
    }
    recovered: list[dict[str, Any]] = []
    checked = 0
    seen_batches: set[str] = set()
    pending_by_id = {str(task.get("id") or ""): task for task in pending}
    cdp_config = task_delivery_config(config, "codex-cdp")
    for task in pending:
        if checked >= max(1, int(max_checks)):
            break
        task_id = str(task.get("id") or "")
        if not task_id or task_id not in pending_by_id:
            continue
        if delivery_mode_for_task(config, task) != "codex-cdp":
            continue
        for payload in recent_task_event_payloads(queue, task_id, "delivery_failed_reverted_to_pending", limit=8):
            delivery = payload.get("delivery") if isinstance(payload.get("delivery"), dict) else {}
            if str(delivery.get("mode") or "") != "codex-cdp":
                continue
            reason = str(delivery.get("reason") or "")
            if reason not in recoverable_reasons:
                continue
            client_message_id = str(delivery.get("client_user_message_id") or "")
            if not client_message_id or client_message_id in seen_batches:
                continue
            expected_task_ids = [str(item) for item in (delivery.get("expected_task_ids") or []) if str(item)]
            if not expected_task_ids:
                continue
            protocols = delivery.get("mobile_protocols") if isinstance(delivery.get("mobile_protocols"), dict) else {}
            expected_result_codes: dict[str, str] = {}
            expected_ack_codes: dict[str, str] = {}
            for owner_id in expected_task_ids:
                protocol = protocols.get(owner_id) if isinstance(protocols.get(owner_id), dict) else {}
                result_code = str(protocol.get("result_code") or "")
                ack_code = str(protocol.get("ack_code") or "")
                if result_code:
                    expected_result_codes[owner_id] = result_code
                if ack_code:
                    expected_ack_codes[owner_id] = ack_code
            if not expected_result_codes:
                continue
            checked += 1
            seen_batches.add(client_message_id)
            poll = poll_codex_result(
                cdp_config,
                str(delivery.get("thread_id") or task.get("codex_thread_id") or ""),
                str(delivery.get("turn_id") or "cdp-visible-turn"),
                str(delivery.get("baseline_key") or ""),
                client_message_id,
                expected_task_ids,
                expected_result_codes,
                expected_ack_codes,
            )
            new_text = strip_mobile_result_markers(str(poll.get("newText") or "").strip())
            if not is_usable_owned_result_text(new_text):
                new_text = ""
            if not new_text:
                queue.add_event(
                    "local",
                    "pending_visible_cdp_unconfirmed_result_not_ready",
                    {
                        "client_message_id": client_message_id,
                        "expected_task_ids": expected_task_ids,
                        "poll": poll,
                    },
                    task_id,
                )
                continue
            owner_id = str(poll.get("ownership", {}).get("matched_task_id") or expected_task_ids[0])
            if owner_id not in expected_task_ids:
                owner_id = expected_task_ids[0]
            owner_task = queue.get_task(owner_id) or {}
            if str(owner_task.get("status") or "") != "pending":
                continue
            batch_tasks = pending_tasks_for_delivery_batch(queue, client_message_id)
            batch_ids = [str(item.get("id") or "") for item in batch_tasks if str(item.get("id") or "")]
            member_ids = [tid for tid in batch_ids if tid not in set(expected_task_ids)]
            deferred_member_ids = [
                tid
                for tid in member_ids
                if pending_task_has_unacked_pending_backlog_supplement(queue, tid, str(delivery.get("thread_id") or owner_task.get("codex_thread_id") or ""))
            ]
            completable_member_ids = [tid for tid in member_ids if tid not in set(deferred_member_ids)]
            owner_tasks = [queue.get_task(tid) or {} for tid in expected_task_ids]
            owner_tasks = [item for item in owner_tasks if item]
            member_tasks = [queue.get_task(tid) or {} for tid in completable_member_ids]
            member_tasks = [item for item in member_tasks if item]
            thread_id = str(delivery.get("thread_id") or owner_task.get("codex_thread_id") or "")
            if member_tasks and not task_event_exists(queue, owner_id, "delivery_group_owner"):
                mark_delivery_group_members(queue, owner_tasks, member_tasks, thread_id, "codex-cdp")
            queue.complete(owner_id, new_text, status="done")
            completed_members = complete_delivery_group_members(queue, owner_id, completable_member_ids, new_text, thread_id)
            clear_delivery_retry(queue, [owner_id] + completable_member_ids)
            if deferred_member_ids:
                queue.add_event(
                    "local",
                    "pending_visible_cdp_unconfirmed_member_deferred_for_promotion",
                    {
                        "owner_task_id": owner_id,
                        "member_task_ids": deferred_member_ids,
                        "client_message_id": client_message_id,
                        "thread_id": thread_id,
                        "policy": "unacked pending_backlog supplements are not completed by owner result recovery; keep pending for MCP ack or orphaned supplement promotion",
                    },
                    owner_id,
                )
            clear_task_codex_runtime(queue, owner_id)
            reply = push_final_reply_async(queue, owner_task, new_text, config)
            remaining_supplement_runtime: dict[str, Any] = {}
            runtime_key = bridge_supplement_key(thread_id)
            runtime_payload: dict[str, Any] = {}
            raw_runtime_payload = str(queue.runtime_get(runtime_key) or "")
            if raw_runtime_payload:
                try:
                    parsed_runtime_payload = json.loads(raw_runtime_payload)
                    runtime_payload = parsed_runtime_payload if isinstance(parsed_runtime_payload, dict) else {}
                except json.JSONDecodeError:
                    runtime_payload = {}
            if runtime_key and runtime_payload:
                consumed_ids = set(completed_members)
                consumed_ids.add(owner_id)
                consumed_ids.update(str(tid) for tid in member_ids if str(tid) not in set(deferred_member_ids))
                consumed_items, residual_items, residual_ids = bridge_supplement_partition_consumed_items(
                    runtime_payload,
                    consumed_ids,
                )
                remaining_supplement_runtime, remaining_member_ids = bridge_supplement_prune_consumed_items(
                    runtime_payload,
                    consumed_ids,
                    new_base_task_id=owner_id,
                    new_active_task_id=owner_id,
                    new_thread_id=thread_id,
                )
                if remaining_supplement_runtime:
                    queue.runtime_set(runtime_key, json.dumps(remaining_supplement_runtime, ensure_ascii=False))
                else:
                    queue.runtime_delete(runtime_key)
                queue.add_event(
                    "local",
                    "pending_visible_cdp_unconfirmed_runtime_pruned",
                    {
                        "owner_task_id": owner_id,
                        "thread_id": thread_id,
                        "runtime_key": runtime_key,
                        "consumed_task_ids": sorted(consumed_ids),
                        "consumed_item_ids": [str(item.get("message_id") or "") for item in consumed_items if str(item.get("message_id") or "")],
                        "remaining_task_ids": remaining_member_ids,
                        "residual_item_ids": residual_ids,
                        "policy": "prune consumed supplement items first, then keep or delete runtime based on remaining items",
                    },
                    owner_id,
                )
            promotion = promote_orphaned_bridge_supplements(
                queue,
                config,
                thread_id,
                force_base_task_ids={owner_id} if deferred_member_ids else None,
            )
            result = {
                "owner_task_id": owner_id,
                "member_task_ids": member_ids,
                "completed_members": completed_members,
                "deferred_member_ids": deferred_member_ids,
                "promotion": promotion,
                "runtime_prune": {
                    "ok": bool(runtime_key),
                    "runtime_key": runtime_key,
                    "remaining": list(remaining_supplement_runtime.get("items", [])) if remaining_supplement_runtime else [],
                },
                "client_message_id": client_message_id,
                "new_text_chars": len(new_text),
                "poll": poll,
                "reply": reply,
                "reason": "owned visible-CDP result was found after an older unconfirmed-submit rollback",
            }
            queue.add_event("local", "pending_visible_cdp_unconfirmed_result_recovered", result, owner_id)
            for member_id in completed_members:
                queue.add_event("local", "pending_visible_cdp_unconfirmed_member_consumed", result, member_id)
            recovered.append(result)
            for tid in [owner_id] + member_ids:
                pending_by_id.pop(tid, None)
            break
    return {"ok": True, "checked": checked, "recovered": recovered, "recovered_count": len(recovered)}


def recover_active_codex_tasks(
    queue: MobileQueue,
    config: dict[str, Any],
    max_sent_checks: int | None = None,
) -> dict[str, Any]:
    """Facade for active Codex recovery; implementation lives in worker_active_recovery."""
    deps = ActiveRecoveryDependencies(
        _task_route_identity=_task_route_identity,
        active_route_lease_expired=active_route_lease_expired,
        active_slot_release_after_seconds=active_slot_release_after_seconds,
        app_server_no_owned_result_manual_after_attempts=app_server_no_owned_result_manual_after_attempts,
        app_server_notfound_is_materializing=app_server_notfound_is_materializing,
        app_server_repair_continuation_after_seconds=app_server_repair_continuation_after_seconds,
        bridge_supplement_host_still_active_owner=bridge_supplement_host_still_active_owner,
        cancel_codex_generation=cancel_codex_generation,
        check_codex_health=check_codex_health,
        clear_delivery_retry=clear_delivery_retry,
        clear_task_codex_runtime=clear_task_codex_runtime,
        clear_waiting_followup_redelivery_state=clear_waiting_followup_redelivery_state,
        codex_turn_needs_retry=codex_turn_needs_retry,
        complete_delivery_group_member_from_finished_owner=complete_delivery_group_member_from_finished_owner,
        complete_delivery_group_members=complete_delivery_group_members,
        defer_app_server_inprogress_no_output_manual_review=defer_app_server_inprogress_no_output_manual_review,
        delivery_group_member_ids=delivery_group_member_ids,
        delivery_mode_for_task=delivery_mode_for_task,
        fail_app_server_no_owned_result_manual_required=fail_app_server_no_owned_result_manual_required,
        fail_waiting_followup_redelivery_manual_required=fail_waiting_followup_redelivery_manual_required,
        health_result_is_transient_probe_failure=health_result_is_transient_probe_failure,
        mark_active_recovery_cooldown=mark_active_recovery_cooldown,
        mark_waiting_followup_redelivery=mark_waiting_followup_redelivery,
        poll_codex_result=poll_codex_result,
        poll_generation_is_active=poll_generation_is_active,
        poll_has_mcp_transport_closed=poll_has_mcp_transport_closed,
        poll_is_base_ack_only_terminal=poll_is_base_ack_only_terminal,
        poll_has_mobile_ack=poll_has_mobile_ack,
        poll_has_ownership_mismatch=poll_has_ownership_mismatch,
        poll_has_stalled_recoverable_tool=poll_has_stalled_recoverable_tool,
        poll_in_progress_tools=poll_in_progress_tools,
        poll_protocol_violation_reason=poll_protocol_violation_reason,
        poll_status_is_in_progress=poll_status_is_in_progress,
        poll_turn_was_superseded=poll_turn_was_superseded,
        provisional_codex_turn_runtime_from_unreadable_dispatch=provisional_codex_turn_runtime_from_unreadable_dispatch,
        push_final_reply_async=push_final_reply_async,
        record_active_poll_observation=record_active_poll_observation,
        record_unowned_intermediate_result=record_unowned_intermediate_result,
        recover_owned_result_from_history_sources=recover_owned_result_from_history_sources,
        rehydrate_codex_turn_runtime_from_event=rehydrate_codex_turn_runtime_from_event,
        release_active_task_to_pending=release_active_task_to_pending,
        reserve_owned_result_consume=reserve_owned_result_consume,
        restart_codex_app_server_for_mcp=restart_codex_app_server_for_mcp,
        select_active_recovery_tasks=select_active_recovery_tasks,
        send_status_ack=send_status_ack,
        start_app_server_repair_continuation=start_app_server_repair_continuation,
        task_ack_code_runtime=task_ack_code_runtime,
        task_batch_runtime=task_batch_runtime,
        task_delivery_config=task_delivery_config,
        task_event_exists=task_event_exists,
        task_event_recent=task_event_recent,
        task_has_attachments=task_has_attachments,
        task_is_supplement_context=task_is_supplement_context,
        task_is_waiting_followup_redelivery=task_is_waiting_followup_redelivery,
        task_owns_final_reply=task_owns_final_reply,
        task_result_code_runtime=task_result_code_runtime,
        task_turn_key=task_turn_key,
        task_waits_for_followup_redelivery=task_waits_for_followup_redelivery,
        visible_cdp_no_owned_result_manual_after_seconds=visible_cdp_no_owned_result_manual_after_seconds,
        waiting_followup_redelivery_age_seconds=waiting_followup_redelivery_age_seconds,
    )
    return recover_active_codex_tasks_impl(queue, config, max_sent_checks, deps)

def worker_once(
    queue: MobileQueue,
    config: dict[str, Any],
    limit: int,
    task_id: str = "",
    fallback_depth: int = 0,
) -> dict[str, Any]:
    """Facade for one worker cycle; implementation lives in worker_loop_runtime."""
    deps = WorkerLoopDependencies(
        DEFAULT_CONFIG=DEFAULT_CONFIG,
        Path=Path,
        STOP_REQUEST=STOP_REQUEST,
        active_route_lease_expired=active_route_lease_expired,
        add_coalesced_event=add_coalesced_event,
        attachment_supplement_signature_key=attachment_supplement_signature_key,
        attachment_task_ids=attachment_task_ids,
        auto_create_thread_route_for_user=auto_create_thread_route_for_user,
        bridge_supplement_base_task_id=bridge_supplement_base_task_id,
        bridge_supplement_key=bridge_supplement_key,
        bridge_supplement_payload_for_task=bridge_supplement_payload_for_task,
        cdp_delivery_lacks_submission_evidence=cdp_delivery_lacks_submission_evidence,
        clear_delivery_retry=clear_delivery_retry,
        clear_pending_backlog_supplement_if_matches=clear_pending_backlog_supplement_if_matches,
        clear_thread_recovery=clear_thread_recovery,
        clear_waiting_followup_redelivery_state=clear_waiting_followup_redelivery_state,
        codex_thread_dispatch_state=codex_thread_dispatch_state,
        codex_thread_is_busy=codex_thread_is_busy,
        codex_thread_is_unavailable=codex_thread_is_unavailable,
        codex_thread_needs_background_prewarm=codex_thread_needs_background_prewarm,
        current_mcp_session_gate_for_dispatch=current_mcp_session_gate_for_dispatch,
        datetime=datetime,
        default_thread_id=default_thread_id,
        defer_continuation_for_busy_route=defer_continuation_for_busy_route,
        delivery_group_split=delivery_group_split,
        delivery_group_task_ids=delivery_group_task_ids,
        delivery_mode_for_task=delivery_mode_for_task,
        delivery_retry_reason_allows_batch=delivery_retry_reason_allows_batch,
        dispatch_to_codex=dispatch_to_codex,
        effective_task_thread_id=effective_task_thread_id,
        enforce_ask_scope_for_task=enforce_ask_scope_for_task,
        enforce_worker_dispatch_permission=enforce_worker_dispatch_permission,
        find_thread_for_external_user=find_thread_for_external_user,
        find_waiting_followup_redelivery_active=find_waiting_followup_redelivery_active,
        get_active_thread=get_active_thread,
        get_cdp_start_probe_cooldown=get_cdp_start_probe_cooldown,
        get_continuation_context=get_continuation_context,
        get_delivery_retry=get_delivery_retry,
        get_thread_prewarm=get_thread_prewarm,
        include_released_active_pending_tasks=include_released_active_pending_tasks,
        inspect_codex_thread_for_dispatch=inspect_codex_thread_for_dispatch,
        json=json,
        latest_followup_trigger_owner=latest_followup_trigger_owner,
        latest_task_event_payload=latest_task_event_payload,
        mark_cdp_start_probe_cooldown=mark_cdp_start_probe_cooldown,
        mark_delivery_retry=mark_delivery_retry,
        mark_thread_prewarm=mark_thread_prewarm,
        mark_thread_recovery=mark_thread_recovery,
        maybe_repair_app_server_unreadable_thread=maybe_repair_app_server_unreadable_thread,
        maybe_sync_openclaw_account_onboarding=maybe_sync_openclaw_account_onboarding,
        mcp_ack_payload=mcp_ack_payload,
        next_dispatchable_route_task_id=next_dispatchable_route_task_id,
        onboarding_created_text=onboarding_created_text,
        onboarding_hold_key=onboarding_hold_key,
        onboarding_needed_text=onboarding_needed_text,
        pending_route_batch_tasks=pending_route_batch_tasks,
        pending_task_has_unacked_bridge_supplement=pending_task_has_unacked_bridge_supplement,
        pending_task_is_published_bridge_supplement=pending_task_is_published_bridge_supplement,
        poll_codex_result_cdp=poll_codex_result_cdp,
        process_mcp_acked_pending_supplements=process_mcp_acked_pending_supplements,
        process_pending_reply_context_retries=process_pending_reply_context_retries,
        promote_orphaned_bridge_supplements=promote_orphaned_bridge_supplements,
        publish_attachment_active_supplements=publish_attachment_active_supplements,
        publish_attachment_supplement_for_active=publish_attachment_supplement_for_active,
        publish_pending_backlog_supplement_for_owner=publish_pending_backlog_supplement_for_owner,
        reconcile_completed_replies_waiting_push=reconcile_completed_replies_waiting_push,
        recover_active_codex_tasks=recover_active_codex_tasks,
        recover_pending_visible_cdp_unconfirmed_results=recover_pending_visible_cdp_unconfirmed_results,
        recover_stale_reply_sending_tasks=recover_stale_reply_sending_tasks,
        reject_task_for_permission=reject_task_for_permission,
        release_active_task_to_pending=release_active_task_to_pending,
        release_invalid_published_supplements=release_invalid_published_supplements,
        release_queued_tasks_for_active_owner_supplement=release_queued_tasks_for_active_owner_supplement,
        resolved_visible_cdp_thread_id=resolved_visible_cdp_thread_id,
        revert_tasks_to_pending=revert_tasks_to_pending,
        same_followup_owner_route=same_followup_owner_route,
        send_status_ack=send_status_ack,
        set_active_thread=set_active_thread,
        sort_pending_by_route_fairness=sort_pending_by_route_fairness,
        start_thread_prewarm_background=start_thread_prewarm_background,
        sync_openclaw_accounts_to_bridge_users=sync_openclaw_accounts_to_bridge_users,
        task_ack_code_key=task_ack_code_key,
        task_batch_key=task_batch_key,
        task_can_be_same_turn_supplement=task_can_be_same_turn_supplement,
        task_delivery_config=task_delivery_config,
        task_event_exists=task_event_exists,
        task_event_payload_exists=task_event_payload_exists,
        task_event_recent=task_event_recent,
        task_expected_ids_key=task_expected_ids_key,
        task_is_supplement_context=task_is_supplement_context,
        task_prompt=task_prompt,
        task_result_code_key=task_result_code_key,
        task_route_key=task_route_key,
        task_turn_key=task_turn_key,
        timezone=timezone,
        try_complete_owned_result_before_redelivery=try_complete_owned_result_before_redelivery,
        valid_active_supplement_host=valid_active_supplement_host,
        visible_cdp_unverified_submission_attention_after_attempts=visible_cdp_unverified_submission_attention_after_attempts,
        worker_once=worker_once,
    )
    return worker_once_impl(queue, config, limit, deps, task_id=task_id, fallback_depth=fallback_depth)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw Weixin bridge shadow CLI")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enqueue = sub.add_parser("enqueue", help="Enqueue one OpenClaw message with explicit text")
    p_enqueue.add_argument("text")
    p_enqueue.add_argument("--user", required=True)
    p_enqueue.add_argument("--conversation", default="")
    p_enqueue.add_argument("--msg-id", default="")
    p_enqueue.add_argument("--account-id", default="", help="OpenClaw receiver account/slot id; defaults to openclaw.account_id")
    p_enqueue.add_argument("--run-id", default="", help="OpenClaw/Weixin run id for matching outbound replies")
    p_enqueue.add_argument("--context-token", default="", help="OpenClaw/Weixin context token for matching outbound replies")
    p_enqueue.add_argument("--attachments-json", default="[]")

    p_ingest = sub.add_parser("ingest-log", help="Ingest recent OpenClaw inbound log metadata")
    p_ingest.add_argument("--max-lines", type=int, default=500)
    p_ingest.add_argument("--placeholder-prefix", default="[openclaw-log-shadow]")

    p_scan_stop = sub.add_parser("scan-stop-log", help="Emergency fallback: scan OpenClaw log for exact stop")
    p_scan_stop.add_argument("--max-lines", type=int, default=200)

    register_queue_command_parsers(sub)

    register_worker_loop_parsers(sub)

    register_bridge_control_parsers(sub)

    register_reply_command_parsers(sub)

    register_historical_recovery_parsers(sub)

    p_latency = sub.add_parser("latency-report", help="Read-only latency breakdown for recent mobile tasks")
    p_latency.add_argument("--limit", type=int, default=30)

    register_thread_route_parsers(sub)

    register_bridge_maintenance_parser(sub)
    register_backup_command_parsers(sub)
    register_capability_token_parser(sub)

    register_tool_health_parsers(sub)
    sub.add_parser("resource-layer-smoke-check", help="Run temp-only resource layer acquisition smoke check")
    register_mcp_session_parser(sub)
    register_codegraph_fallback_parser(sub, PROJECT_ROOT)
    register_maintenance_command_parsers(sub)
    register_simple_check_commands(sub)
    if "stability-check" in sub.choices:
        sub.choices["stability-check"].add_argument(
            "--deep",
            action="store_true",
            help="Include thread route UI probes and advisory route details; default is fast core bridge health",
        )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(Path(args.config))
    config["_config_path"] = str(Path(args.config))
    queue = queue_from_config(config)

    if args.cmd == "enqueue":
        return run_enqueue_command(
            args,
            queue,
            config,
            config_path=Path(args.config),
            receiver_account_id=receiver_account_id,
            maybe_handle_control_message=maybe_handle_control_message,
            maybe_complete_capability_passphrase_reply=maybe_complete_capability_passphrase_reply,
            send_status_ack=send_status_ack,
            materialize_attachments=materialize_attachments,
            parse_attachments_json=parse_attachments_json,
            openclaw_context_token_for_user=openclaw_context_token_for_user,
            set_task_context_token=set_task_context_token,
            schedule_waiting_context_replies=schedule_waiting_context_replies,
            enforce_ask_scope_for_task=enforce_ask_scope_for_task,
            reject_task_for_permission=reject_task_for_permission,
            print_json=print_json,
        )

    if args.cmd == "ingest-log":
        log_path = Path(config.get("openclaw", {}).get("log_path") or "")
        messages = extract_recent_log_messages(log_path, args.max_lines)
        results = []
        for msg in messages:
            text = (
                f"{args.placeholder_prefix} inbound message body not available in log; "
                f"body_len={msg.get('body_len')} has_media={msg.get('has_media')}"
            )
            metadata = {
                "msg_id": f"log:{stable_id(str(msg.get('detail_line', '')))}",
                "create_time": msg.get("detail_line", "")[:80],
                "types": msg.get("types", ""),
                "body_len": msg.get("body_len"),
                "has_media": msg.get("has_media"),
                "transport": "openclaw-weixin-log",
                "receiver_account_id": receiver_account_id(config, external_user=msg.get("from") or ""),
            }
            results.append(
                queue.enqueue(
                    text,
                    source="openclaw-weixin",
                    external_user=str(msg.get("from") or "unknown"),
                    external_conversation=str(msg.get("to") or ""),
                    metadata=metadata,
                )
            )
        print_json({"ingested": len(results), "results": results})
        return 0

    if args.cmd == "scan-stop-log":
        print_json(scan_stop_log(queue, Path(args.config), config, args.max_lines))
        return 0

    if args.cmd in {"health", "list", "pending", "stuck-tasks", "get"}:
        payload = run_queue_command(
            args,
            queue,
            active_tasks=active_codex_tasks,
            mark_failed=mark_stuck_tasks_failed,
        )
        print_json(payload)
        if args.cmd == "stuck-tasks" and isinstance(payload, dict) and not payload.get("ok"):
            return 1
    elif args.cmd in {"worker-once", "worker-loop"}:
        return run_worker_command(
            args,
            config,
            load_config=load_config,
            db_path_from_config=db_path_from_config,
            queue_from_config=queue_from_config,
            worker_once=worker_once,
            print_json=print_json,
            worker_loop_has_activity=worker_loop_has_activity,
            worker_loop_should_log=worker_loop_should_log,
            worker_loop_summary=worker_loop_summary,
        )
    elif args.cmd in {"control", "stop-status", "confirm-latest", "set-secret-hash", "mode", "status"}:
        payload = run_bridge_control_command(
            args,
            queue,
            config,
            config_path=Path(args.config),
            stop_request_path=STOP_REQUEST,
            emergency_stop=emergency_stop,
            resume_bridge=resume_bridge,
            save_config=save_config,
            set_confirmation_secret_hash=set_confirmation_secret_hash,
        )
        print_json(payload)
        if args.cmd == "confirm-latest":
            return 0 if payload.get("ok") else 1
    elif args.cmd in {"reply", "status-ack", "final-reply"}:
        payload, exit_code = run_reply_command(
            args,
            queue,
            config,
            reply_to_weixin=reply_to_weixin,
            send_status_ack_sync=send_status_ack_sync,
            task_event_exists=task_event_exists,
            guard_final_reply_owner_ready=guard_final_reply_owner_ready,
            push_final_reply=push_final_reply,
            clear_task_reply_sending=clear_task_reply_sending,
            utc_now=utc_now,
        )
        print_json(payload)
        return exit_code
    elif args.cmd in {
        "recover-failed-result-replies",
        "audit-failed-result-replies",
        "historical-failed-result-recovery-help",
        "audit-owned-result-recovery",
        "recover-owned-result",
    }:
        payload, exit_code = run_historical_recovery_command(
            args,
            queue,
            config,
            recover_failed_tasks_with_result_for_reply=recover_failed_tasks_with_result_for_reply,
            audit_failed_tasks_with_result_for_reply=audit_failed_tasks_with_result_for_reply,
            historical_failed_result_recovery_help=historical_failed_result_recovery_help,
            audit_owned_result_recovery=audit_owned_result_recovery,
            recover_owned_result=recover_owned_result,
        )
        print_json(payload)
        return exit_code
    elif args.cmd == "latency-report":
        print_json(latency_report(queue, args.limit))
    elif args.cmd in {"account-onboarding-sync", "thread-route", "thread-visibility-check", "desktop-sync-check", "thread-prewarm"}:
        payload, exit_code = run_thread_route_command(
            args,
            queue,
            config,
            account_onboarding_sync=account_onboarding_sync,
            active_thread_key=active_thread_key,
            desktop_sync_check_app_server=desktop_sync_check_app_server,
            find_thread=find_thread,
            inspect_codex_thread_app_server=inspect_codex_thread_app_server,
            run_thread_prewarm=run_thread_prewarm,
            set_active_thread=set_active_thread,
            thread_route_diagnostics=thread_route_diagnostics,
        )
        print_json(payload)
        return exit_code
    elif args.cmd == "maintenance":
        text_output, payload = run_bridge_maintenance_command(args, queue, config)
        if payload is None:
            print(text_output)
            return 0
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "backup-hygiene":
        payload = run_backup_hygiene_command(args)
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "backup-router":
        payload = run_backup_router_command(args)
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "capability-token":
        payload = run_capability_token_command(args, config)
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "health":
        print_json(queue.health())
    elif args.cmd in {
        "tool-registry-health",
        "tool-registry-drift-check",
        "codex-plugin-config-health",
        "codex-plugin-cli-visibility-boundary-check",
        "gui-automation-health",
        "gui-ocr-gpu-probe",
        "cdp-startup-contract-check",
        "cdp-recovery-plan",
        "codex-log-sqlite-health",
        "supplement-fallback",
    }:
        payload, exit_code = run_tool_health_command(
            args,
            queue,
            config,
            tool_registry_health=tool_registry_health,
            tool_registry_drift_check=tool_registry_drift_check,
            codex_plugin_config_health=codex_plugin_config_health,
            codex_plugin_cli_visibility_boundary_check=codex_plugin_cli_visibility_boundary_check,
            gui_automation_health_check=gui_automation_health_check,
            gui_ocr_gpu_probe=gui_ocr_gpu_probe,
            cdp_startup_contract_check=cdp_startup_contract_check,
            cdp_recovery_plan=cdp_recovery_plan,
            codex_logs_sqlite_health=codex_logs_sqlite_health,
            mobile_mcp_stdio_tool_call=mobile_mcp_stdio_tool_call,
            supplement_fallback_get_pending_batch=supplement_fallback_get_pending_batch,
            supplement_fallback_ack_message=supplement_fallback_ack_message,
        )
        print_json(payload)
        return exit_code
    elif args.cmd == "resource-layer-smoke-check":
        print_json(resource_layer_smoke_check())
    elif args.cmd == "resource-process":
        payload = run_resource_process_command(args)
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "defender-governance":
        payload = run_defender_governance_command(args)
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "mcp-session":
        payload = run_mcp_session_command(args)
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "codegraph-fallback":
        payload = run_codegraph_fallback(args, PROJECT_ROOT)
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "performance":
        payload = run_performance_command(args)
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "email-scheduler":
        payload = run_email_scheduler_command(args, PROJECT_ROOT)
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "codex-config-guard":
        payload = run_codex_config_guard_command(args)
        print_json(payload)
        return 0 if payload.get("ok") else 1
    elif args.cmd == "source-scan":
        payload, paths = run_source_scan_command(args, ROOT)
        if paths is not None:
            for path in paths:
                print(path)
        else:
            print_json(payload)
            return 0 if payload and payload.get("ok") else 1
    elif args.cmd == "bridge-db-maintenance":
        payload = run_bridge_db_command(args, Path(db_path_from_config(config)))
        print_json(payload)
        return 0 if payload.get("ok") else 1
    else:
        handler = build_simple_check_command_handlers(
            globals(),
            queue,
            config,
            stability_deep=bool(getattr(args, "deep", False)),
        ).get(str(args.cmd or ""))
        if handler is None:
            print_json({"ok": False, "reason": "unknown_command", "cmd": str(args.cmd or "")})
            return 1
        print_json(handler())
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

