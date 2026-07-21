#!/usr/bin/env python3
"""Maintenance inspection and safe repair helpers for the OpenClaw bridge."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
WECOM_BRIDGE = PROJECT_ROOT / "_bridge" / "mobile_wecom_bridge"
BRIDGE_ROOT = PROJECT_ROOT / "_bridge"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WECOM_BRIDGE) not in sys.path:
    sys.path.insert(0, str(WECOM_BRIDGE))
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from mobile_queue import (  # noqa: E402
    NOISY_EVENT_TYPES,
    MobileQueue,
    event_noise_guard_seconds,
)
import permission_policy  # noqa: E402
import capability_tokens  # noqa: E402
from mobile_diagnosis_issue_rules import (  # noqa: E402
    app_server_mcp_issues,
    bridge_runtime_route_issues,
    codex_tooling_issues,
    governance_storage_issues,
    queue_delivery_issues,
    resource_memory_hygiene_issues,
)
from mobile_observability_metrics import render_observability_metrics  # noqa: E402
from mobile_maintenance_report import render_summary_report  # noqa: E402
from mobile_maintenance_probe_policy import (  # noqa: E402
    DEEP_PROBE_SKIPPED,
    QUICK_PROBE_SKIPPED,
    DeepProbePolicy,
    parse_deep_probe_allowlist,
    timed_probe,
)
from shared.backup_router import create_backup as create_routed_backup  # noqa: E402

from health_checks import (  # noqa: E402
    http_json,
    inspect_openclaw_gateway_processes,
    inspect_scheduled_task,
    inspect_worker_processes,
    latest_worker_stderr,
    sqlite_health,
    tcp_check,
)
from codex_plugin_config_health import codex_plugin_config_health  # noqa: E402
import codex_config_guard as codex_config_guard_module  # noqa: E402
from mcp_execution_priority import HUB_MANAGED_MCP_NAMES  # noqa: E402
from system_membership import load_decommissioned_mcp  # noqa: E402
from thread_route_state import thread_items  # noqa: E402

ACTIVE_STATUSES = ("queued_for_codex", "sent_to_codex", "processing")
WORK_STATUSES = ACTIVE_STATUSES + ("pending",)
REPLY_PROBLEM_STATUSES = ("push_failed",)
DEFAULT_WORKER_TASK = "MobileOpenClawBridgeWorker"
DEFAULT_GATEWAY_TASK = "OpenClawGatewayWorker"
DEFAULT_DASHBOARD_LIVE_STATE = ROOT / "runtime" / "dashboard_live_state.json"
DEFAULT_DB_SIZE_WARN_BYTES = 64 * 1024 * 1024
CDP_ENDPOINT_STATE = ROOT / "runtime" / "codex_cdp_endpoint.json"
STOP_REQUEST = ROOT / "STOP_REQUEST"
CODEX_CONFIG_PATH = Path(os.environ.get("CODEX_CONFIG_PATH") or Path.home() / ".codex" / "config.toml")
GUI_REQUIRED_MODULES = ("win32con", "win32gui", "win32process", "PIL", "pywinauto", "uiautomation")
GUI_MODULE_STATUS_TIMEOUT_SECONDS = 8
GUI_OCR_STATUS_TIMEOUT_SECONDS = 8
SESSION_DYNAMIC_MCP_ENV = {
    "node_repl": {
        "BROWSER_USE_CODEX_APP_VERSION",
        "CODEX_CLI_PATH",
        "NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
        "SKY_CUA_NATIVE_PIPE_DIRECTORY",
    }
}
THREAD_ROUTES_UI_SUMMARY_LIMIT = 3
ITERATION_LAYER_REVIEW = PROJECT_ROOT / "_bridge" / "iteration_layer_review.py"
BACKUP_HYGIENE_DOCTOR = PROJECT_ROOT / "_bridge" / "backup_hygiene_doctor.py"
CODEX_STARTUP_BASELINE = PROJECT_ROOT / "_bridge" / "codex_startup_baseline.json"
MEMORY_GOVERNANCE = PROJECT_ROOT / "_bridge" / "memory_governance.py"


def maintenance_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def kill_process_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill.exe", "/PID", str(int(pid)), "/T", "/F"],
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


def run_capture_tree_timeout(
    args: list[str],
    *,
    timeout: int,
    cwd: Path | str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return {
            "ok": False,
            "spawn_failed": True,
            "reason": str(exc),
            "exception_type": type(exc).__name__,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    try:
        stdout, stderr = proc.communicate(timeout=max(1, int(timeout)))
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except subprocess.TimeoutExpired:
        kill_process_tree(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except Exception:
            stdout, stderr = "", ""
        return {
            "ok": False,
            "timed_out": True,
            "timeout_seconds": int(timeout),
            "returncode": proc.returncode,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }


def resource_process_health(deep_probes: bool) -> dict[str, Any]:
    """Return compact read-only resource/MCP process fanout health.

    The underlying scan walks local processes and can be several seconds on a
    loaded Windows desktop, so quick maintenance summaries intentionally skip it.
    """
    if not deep_probes:
        return {
            **DEEP_PROBE_SKIPPED,
            "layer": "skipped",
            "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process metrics",
        }
    try:
        from resource_process_doctor import doctor as resource_process_doctor
        from resource_process_doctor import metrics as resource_process_metrics
        from resource_process_doctor import process_snapshot as resource_process_snapshot
        from resource_process_doctor import repair_plan as resource_process_repair_plan
        from resource_process_doctor import startup_sources as resource_process_startup_sources

        snap = resource_process_snapshot()
        met = resource_process_metrics(snap)
        doc = resource_process_doctor(snap)
        plan = resource_process_repair_plan(snap)
        sources = resource_process_startup_sources(snap)
        issues = doc.get("issues") if isinstance(doc.get("issues"), list) else []
        risk_count = sum(1 for item in issues if item.get("severity") in {"blocker", "risk"})
        advisory_count = sum(1 for item in issues if item.get("severity") == "advisory")
        if not snap.get("ok"):
            layer = "observer_failed"
        elif risk_count:
            layer = "risk"
        elif advisory_count:
            layer = "advisory"
        else:
            layer = "ok"
        return {
            "ok": bool(snap.get("ok")) and not risk_count,
            "layer": layer,
            "metrics": {
                "schema": met.get("schema"),
                "ok": met.get("ok"),
                "matched_group_count": met.get("matched_group_count"),
                "matched_process_count": met.get("matched_process_count"),
                "root_instance_count": met.get("root_instance_count"),
                "matched_working_set_mb": met.get("matched_working_set_mb"),
                "fanout_group_count": met.get("fanout_group_count"),
                "codex_app_server_owner_healthy": met.get("codex_app_server_owner_healthy"),
                "codex_app_server_owner_issue": met.get("codex_app_server_owner_issue"),
                "codex_app_server_owner_count": met.get("codex_app_server_owner_count"),
                "groups": met.get("groups", [])[:20],
            },
            "issues": issues,
            "doctor_summary": doc.get("summary") if isinstance(doc.get("summary"), dict) else {},
            "startup_sources_summary": sources.get("summary") if isinstance(sources.get("summary"), dict) else {},
            "repeated_launch_batches": [
                {
                    "group": group.get("group"),
                    "parent_pid": parent.get("parent_pid"),
                    "parent_name": parent.get("parent_name"),
                    "launch_batch_count": parent.get("launch_batch_count"),
                    "launch_batches": parent.get("launch_batches"),
                }
                for group in (sources.get("groups") if isinstance(sources.get("groups"), list) else [])
                for parent in (group.get("top_parent_sources") if isinstance(group.get("top_parent_sources"), list) else [])
                if int(parent.get("launch_batch_count") or 0) > 1
            ][:12],
            "repair_plan_preview": {
                "apply_supported": bool(plan.get("apply_supported")),
                "action_count": len(plan.get("actions") or []),
                "orphan_candidate_action_count": int(plan.get("orphan_candidate_action_count") or 0),
                "non_protected_orphan_candidate_action_count": int(plan.get("non_protected_orphan_candidate_action_count") or 0),
                "protected_orphan_candidate_action_count": int(plan.get("protected_orphan_candidate_action_count") or 0),
                "orphan_candidate_root_count": int(plan.get("orphan_candidate_root_count") or 0),
                "non_protected_orphan_candidate_root_count": int(plan.get("non_protected_orphan_candidate_root_count") or 0),
                "orphan_candidate_process_count": int(plan.get("orphan_candidate_process_count") or 0),
                "latest_batch_policy": plan.get("latest_batch_policy"),
                "governance_state": plan.get("governance_state"),
                "cleanup_commands": plan.get("cleanup_commands") if isinstance(plan.get("cleanup_commands"), dict) else {},
                "apply_contract": plan.get("apply_contract") if isinstance(plan.get("apply_contract"), dict) else {},
                "dry_run_contract": plan.get("dry_run_contract") if isinstance(plan.get("dry_run_contract"), dict) else {},
            },
            "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process doctor",
        }
    except Exception as exc:
        return {
            "ok": False,
            "layer": "error",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process doctor",
        }


def backup_hygiene_health(deep_probes: bool) -> dict[str, Any]:
    """Return compact read-only backup hygiene health."""
    if not deep_probes:
        return {
            **DEEP_PROBE_SKIPPED,
            "layer": "skipped",
            "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py backup-hygiene metrics",
        }
    try:
        from backup_hygiene_doctor import backup_snapshot, doctor as backup_doctor, metrics as backup_metrics, repair_plan as backup_repair_plan

        snap = backup_snapshot()
        met = backup_metrics(snap)
        doc = backup_doctor(snap)
        plan = backup_repair_plan(snap)
        issues = doc.get("issues") if isinstance(doc.get("issues"), list) else []
        risk_count = sum(1 for item in issues if item.get("severity") in {"blocker", "risk"})
        advisory_count = sum(1 for item in issues if item.get("severity") == "advisory")
        if not snap.get("ok"):
            layer = "observer_failed"
        elif risk_count:
            layer = "risk"
        elif advisory_count:
            layer = "advisory"
        else:
            layer = "ok"
        return {
            "ok": bool(snap.get("ok")) and not risk_count,
            "layer": layer,
            "metrics": met,
            "issues": issues,
            "doctor_summary": doc.get("summary") if isinstance(doc.get("summary"), dict) else {},
            "repair_plan_preview": {
                "apply_supported": bool(plan.get("apply_supported")),
                "action_count": len(plan.get("actions") or []),
                "dry_run_contract": plan.get("dry_run_contract") if isinstance(plan.get("dry_run_contract"), dict) else {},
            },
            "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py backup-hygiene doctor",
        }
    except Exception as exc:
        return {
            "ok": False,
            "layer": "error",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py backup-hygiene doctor",
        }


def probe_evidence_state(probe: dict[str, Any], *, profile: str = "") -> dict[str, Any]:
    """Classify probe evidence so unknown or stale data is not reported as live failure."""
    if not isinstance(probe, dict) or not probe:
        return {
            "state": "unknown",
            "current_failure": False,
            "actionable": False,
            "reason": "no probe evidence",
            "profile": profile or "",
        }
    if probe.get("skipped"):
        return {
            "state": "quick_skipped",
            "current_failure": False,
            "actionable": False,
            "reason": str(probe.get("reason") or "probe skipped"),
            "profile": profile or "quick",
        }
    if probe.get("historical") or probe.get("stale") or probe.get("stale_observation"):
        return {
            "state": "stale_observation",
            "current_failure": False,
            "actionable": False,
            "reason": str(probe.get("reason") or "historical or stale observation"),
            "profile": profile or "",
        }
    ok = probe.get("ok")
    if ok is True:
        return {
            "state": "current_ok",
            "current_failure": False,
            "actionable": False,
            "reason": "",
            "profile": profile or "",
        }
    if ok is False:
        return {
            "state": "current_failure",
            "current_failure": True,
            "actionable": True,
            "reason": str(probe.get("reason") or "current probe reported failure"),
            "profile": profile or "",
        }
    return {
        "state": "unknown",
        "current_failure": False,
        "actionable": False,
        "reason": str(probe.get("reason") or "probe did not report current ok/failure"),
        "profile": profile or "",
    }


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_seconds(value: Any, now: datetime | None = None) -> int | None:
    parsed = parse_iso(value)
    if not parsed:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - parsed).total_seconds()))


def short(value: Any, limit: int = 160) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def account_of(row: sqlite3.Row | dict[str, Any]) -> str:
    value = row["receiver_account_id"] if isinstance(row, sqlite3.Row) else row.get("receiver_account_id")
    text = str(value or "").strip()
    return text or "(none)"


def delivery_mode_for_account(config: dict[str, Any] | None, account: str) -> str:
    account_id = str(account or "").strip()
    if account_id == "primary":
        return "codex-cdp"
    trigger = config.get("trigger", {}) if isinstance(config, dict) else {}
    if not isinstance(trigger, dict):
        trigger = {}
    return str(trigger.get("delivery_mode") or "stub").strip().lower() or "stub"


def bridge_supplement_index(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Map pending task id to the bridge_supplement payload exposing it to MCP."""
    index: dict[str, dict[str, Any]] = {}
    rows = db.execute(
        """
        SELECT key, value, updated_at
        FROM mobile_runtime
        WHERE key LIKE 'bridge_supplement:%'
        """
    ).fetchall()
    for row in rows:
        key = str(row["key"] or "")
        try:
            payload = json.loads(str(row["value"] or "{}"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            continue
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("message_id") or "").strip()
            if not task_id:
                continue
            index[task_id] = {
                "runtime_key": key,
                "thread_id": str(payload.get("thread_id") or key.replace("bridge_supplement:", "", 1)),
                "base_task_id": str(payload.get("active_task_id") or payload.get("base_message_id") or ""),
                "published_at": str(payload.get("published_at") or row["updated_at"] or ""),
                "supplement_signature": str(payload.get("supplement_signature") or ""),
            }
    return index


def route_key(row: sqlite3.Row | dict[str, Any], config: dict[str, Any] | None = None) -> str:
    thread_id = row["codex_thread_id"] if isinstance(row, sqlite3.Row) else row.get("codex_thread_id")
    account = account_of(row)
    mode = delivery_mode_for_account(config, account)
    return f"{mode}:{account}:{str(thread_id or '').strip() or '(no-thread)'}"


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def table_counts(db: sqlite3.Connection) -> dict[str, Any]:
    by_status = {
        str(row["status"]): int(row["n"])
        for row in db.execute("SELECT status, COUNT(*) AS n FROM mobile_tasks GROUP BY status").fetchall()
    }
    by_account_status: dict[str, dict[str, int]] = {}
    for row in db.execute(
        """
        SELECT COALESCE(NULLIF(receiver_account_id,''),'(none)') AS account, status, COUNT(*) AS n
        FROM mobile_tasks
        GROUP BY account, status
        ORDER BY account, status
        """
    ).fetchall():
        account = str(row["account"])
        by_account_status.setdefault(account, {})[str(row["status"])] = int(row["n"])
    return {"by_status": by_status, "by_account_status": by_account_status}


def active_rows(db: sqlite3.Connection, config: dict[str, Any], limit: int = 50) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT id, source, external_user, receiver_account_id, status, codex_thread_id,
               queued_for_codex_at, sent_to_codex_at, created_at, updated_at,
               push_status, SUBSTR(COALESCE(text,''), 1, 180) AS text_preview
        FROM mobile_tasks
        WHERE status IN ('queued_for_codex','sent_to_codex','processing')
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    now = datetime.now(timezone.utc)
    result = []
    for row in rows:
        item = dict(row)
        item["account"] = account_of(row)
        item["delivery_mode"] = delivery_mode_for_account(config, item["account"])
        item["route_key"] = route_key(row, config)
        item["age_seconds"] = age_seconds(item.get("sent_to_codex_at") or item.get("updated_at"), now)
        result.append(item)
    return result


def app_server_materialization_lag_rows(db: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT t.id, t.status, t.receiver_account_id, t.codex_thread_id,
               t.updated_at, t.queued_for_codex_at, t.sent_to_codex_at,
               e.event_type, e.payload_json, e.created_at AS event_created_at
        FROM mobile_events e
        JOIN mobile_tasks t ON t.id=e.task_id
        WHERE e.event_type IN (
            'delivery_failed_reverted_to_pending',
            'codex_turn_runtime_rehydrated_from_unreadable_dispatch',
            'recovery_queued_rehydrated_from_materialized_turn'
        )
          AND e.created_at >= datetime('now', '-6 hours')
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    now = datetime.now(timezone.utc)
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        delivery = payload.get("delivery") if isinstance(payload.get("delivery"), dict) else {}
        event_type = str(row["event_type"] or "")
        if event_type == "delivery_failed_reverted_to_pending":
            if str(delivery.get("mode") or "") != "codex-app-server":
                continue
            if str(delivery.get("reason") or "") != "app_server_turn_not_readable_after_dispatch":
                continue
        result.append(
            {
                "id": str(row["id"] or ""),
                "status": str(row["status"] or ""),
                "account": str(row["receiver_account_id"] or "") or "(none)",
                "thread_id": str(row["codex_thread_id"] or delivery.get("thread_id") or ""),
                "event_type": event_type,
                "event_age_seconds": age_seconds(row["event_created_at"], now),
                "event_created_at": str(row["event_created_at"] or ""),
                "turn_id": str(payload.get("turn_id") or delivery.get("turn_id") or ""),
                "client_message_id": str(payload.get("client_message_id") or delivery.get("client_user_message_id") or ""),
                "expected_task_ids": payload.get("expected_task_ids") or delivery.get("expected_task_ids") or [],
                "reason": str(payload.get("reason") or delivery.get("reason") or "app_server_turn_materialization_lag"),
            }
        )
    return result


def cdp_visible_unconfirmed_observing_rows(db: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT t.id, t.status, t.receiver_account_id, t.codex_thread_id,
               t.updated_at, t.queued_for_codex_at, t.sent_to_codex_at,
               e.payload_json, e.created_at AS event_created_at
        FROM mobile_events e
        JOIN mobile_tasks t ON t.id=e.task_id
        WHERE e.event_type='cdp_visible_submission_unverified_observed'
          AND e.created_at >= datetime('now', '-6 hours')
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    now = datetime.now(timezone.utc)
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        delivery = payload.get("delivery") if isinstance(payload.get("delivery"), dict) else {}
        result.append(
            {
                "id": str(row["id"] or ""),
                "status": str(row["status"] or ""),
                "account": str(row["receiver_account_id"] or "") or "(none)",
                "thread_id": str(row["codex_thread_id"] or delivery.get("thread_id") or ""),
                "event_age_seconds": age_seconds(row["event_created_at"], now),
                "event_created_at": str(row["event_created_at"] or ""),
                "turn_id": str(delivery.get("turn_id") or ""),
                "client_message_id": str(delivery.get("client_user_message_id") or ""),
                "expected_task_ids": delivery.get("expected_task_ids") or [],
                "reason": str(delivery.get("reason") or "cdp_visible_submission_unverified_observed"),
                "diagnostic_only": bool(delivery.get("diagnostic_only")),
            }
        )
    return result


def pending_rows(
    db: sqlite3.Connection,
    config: dict[str, Any],
    limit: int = 80,
    supplement_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT id, source, external_user, receiver_account_id, status, codex_thread_id,
               created_at, updated_at, push_status, SUBSTR(COALESCE(text,''), 1, 180) AS text_preview
        FROM mobile_tasks
        WHERE status='pending'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    now = datetime.now(timezone.utc)
    result = []
    supplement_index = supplement_index or {}
    for row in rows:
        item = dict(row)
        item["account"] = account_of(row)
        item["delivery_mode"] = delivery_mode_for_account(config, item["account"])
        raw_thread_id = str(item.get("codex_thread_id") or "").strip()
        supplement = supplement_index.get(str(item.get("id") or "")) or {}
        item["effective_thread_id"] = str(supplement.get("thread_id") or raw_thread_id)
        item["raw_route_key"] = route_key(row, config)
        item["route_key"] = f"{item['delivery_mode']}:{item['account']}:{item['effective_thread_id'] or '(thread-pending)'}"
        item["thread_resolution_state"] = (
            "supplement_thread_resolved"
            if supplement and raw_thread_id
            else "supplement_thread_pending"
            if supplement
            else "raw_thread_present"
            if raw_thread_id
            else "thread_missing"
        )
        item["age_seconds"] = age_seconds(item.get("created_at") or item.get("updated_at"), now)
        item["pending_kind"] = "supplement_waiting_mcp_ack" if supplement else "normal"
        item["supplement"] = supplement
        result.append(item)
    return result


def _nested_payload_flag(payload: Any, key: str) -> bool:
    if isinstance(payload, dict):
        if bool(payload.get(key)):
            return True
        return any(_nested_payload_flag(value, key) for value in payload.values())
    if isinstance(payload, list):
        return any(_nested_payload_flag(value, key) for value in payload)
    return False


def _reply_problem_diagnostic(result_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(result_json or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return {"diagnostic_category": "", "source_reason": "", "context_token_present": False}
    source_reason = str(payload.get("source_reason") or "")
    category = str(payload.get("diagnostic_category") or "")
    token_present = bool(payload.get("context_token_present")) or _nested_payload_flag(payload, "contextTokenPresent")
    if not category and source_reason in {"sendmessage_ret_-2", "media_sendmessage_ret_-2"}:
        category = "token_present_but_send_rejected" if token_present else "send_rejected_without_context_token"
    return {
        "diagnostic_category": category,
        "source_reason": source_reason,
        "context_token_present": token_present,
        "fresh_inbound_required": bool(payload.get("fresh_inbound_required"))
        or source_reason in {"sendmessage_ret_-2", "media_sendmessage_ret_-2", "weixin_send_circuit_open"},
    }


def reply_problem_rows(db: sqlite3.Connection, limit: int = 80) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT id, source, external_user, receiver_account_id, status, push_status,
               created_at, updated_at, pushed_at, SUBSTR(COALESCE(error,''), 1, 240) AS error_preview,
               SUBSTR(COALESCE(result,''), 1, 180) AS result_preview,
               COALESCE(result,'') AS result_json
        FROM mobile_tasks
        WHERE status IN ('push_failed') OR push_status IN ('reply_pending','reply_retrying','reply_sending')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    now = datetime.now(timezone.utc)
    result = []
    for row in rows:
        item = dict(row)
        item["account"] = account_of(row)
        item["age_seconds"] = age_seconds(item.get("updated_at"), now)
        item.update(_reply_problem_diagnostic(str(item.pop("result_json", "") or "")))
        result.append(item)
    return result


CONTROL_ACTION_EVENT_TYPES = {
    "control_rejected",
    "repair_control_completed",
    "system_maintenance_control_started",
    "thread_switched",
    "user_status_replied",
}
CONTROL_REPLY_TERMINAL_EVENT_TYPES = {"control_reply_sent", "control_reply_failed"}


def control_reply_receipt_health(db: sqlite3.Connection, limit: int = 240) -> dict[str, Any]:
    rows = db.execute(
        """
        SELECT id, source, event_type, payload_json, created_at
        FROM mobile_events
        WHERE event_type IN (
            'control_reply_outbox_created',
            'control_reply_sent',
            'control_reply_failed',
            'control_rejected',
            'repair_control_completed',
            'system_maintenance_control_started',
            'thread_switched',
            'user_status_replied'
        )
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    outbox: dict[str, dict[str, Any]] = {}
    terminal: dict[str, dict[str, Any]] = {}
    actions: dict[str, dict[str, Any]] = {}
    raw_missing_receipt_actions: list[dict[str, Any]] = []
    first_contract_event_id = 0
    parse_errors = 0
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except Exception:
            payload = {}
            parse_errors += 1
        if not isinstance(payload, dict):
            payload = {}
        event_type = str(row["event_type"] or "")
        receipt_id = str(payload.get("receipt_id") or "").strip()
        entry = {
            "event_id": int(row["id"] or 0),
            "event_type": event_type,
            "created_at": str(row["created_at"] or ""),
            "source": str(row["source"] or ""),
            "receipt_id": receipt_id,
            "command": str(payload.get("command") or ""),
            "ok": payload.get("ok"),
        }
        if event_type == "control_reply_outbox_created" and receipt_id:
            outbox.setdefault(receipt_id, entry)
            event_id = int(entry.get("event_id") or 0)
            if event_id and (not first_contract_event_id or event_id < first_contract_event_id):
                first_contract_event_id = event_id
        elif event_type in CONTROL_REPLY_TERMINAL_EVENT_TYPES and receipt_id:
            terminal.setdefault(receipt_id, entry)
            event_id = int(entry.get("event_id") or 0)
            if event_id and (not first_contract_event_id or event_id < first_contract_event_id):
                first_contract_event_id = event_id
        elif event_type in CONTROL_ACTION_EVENT_TYPES:
            if receipt_id:
                actions.setdefault(receipt_id, entry)
            else:
                raw_missing_receipt_actions.append(entry)
    missing_terminal = [
        {
            **entry,
            "action_event_type": actions.get(receipt_id, {}).get("event_type", ""),
            "action_event_id": actions.get(receipt_id, {}).get("event_id", 0),
        }
        for receipt_id, entry in outbox.items()
        if receipt_id not in terminal
    ][:12]
    action_without_outbox = [
        entry
        for receipt_id, entry in actions.items()
        if receipt_id not in outbox and receipt_id not in terminal
    ][:12]
    current_missing_receipt_actions = [
        entry
        for entry in raw_missing_receipt_actions
        if first_contract_event_id and int(entry.get("event_id") or 0) > first_contract_event_id
    ]
    legacy_missing_receipt_actions = [
        entry
        for entry in raw_missing_receipt_actions
        if not first_contract_event_id or int(entry.get("event_id") or 0) <= first_contract_event_id
    ]
    issue_count = len(missing_terminal) + len(action_without_outbox) + len(current_missing_receipt_actions)
    return {
        "ok": issue_count == 0 and parse_errors == 0,
        "schema": "mobile-control-reply-receipt-health/v1",
        "sample_limit": max(1, int(limit)),
        "sampled_event_count": len(rows),
        "contract_event_id_floor": first_contract_event_id,
        "outbox_count": len(outbox),
        "terminal_count": len(terminal),
        "action_count": len(actions),
        "missing_terminal_count": len(missing_terminal),
        "action_without_outbox_count": len(action_without_outbox),
        "missing_receipt_action_count": len(current_missing_receipt_actions),
        "legacy_missing_receipt_action_count": len(legacy_missing_receipt_actions),
        "parse_error_count": parse_errors,
        "missing_terminal": missing_terminal,
        "action_without_outbox": action_without_outbox,
        "missing_receipt_actions": current_missing_receipt_actions[:12],
        "legacy_missing_receipt_actions": legacy_missing_receipt_actions[:12],
        "policy": (
            "Every mobile control command action must carry a receipt_id, create a "
            "control_reply_outbox_created event, and end in control_reply_sent or control_reply_failed."
        ),
    }


def session_timeout_misclassified_rows(db: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT
            t.id, t.source, t.external_user, t.receiver_account_id, t.status,
            t.push_status, t.created_at, t.updated_at, t.pushed_at,
            SUBSTR(COALESCE(t.result,''), 1, 180) AS result_preview,
            e.created_at AS event_created_at,
            SUBSTR(COALESCE(e.payload_json,''), 1, 1600) AS event_payload_preview
        FROM mobile_tasks t
        JOIN mobile_events e ON e.task_id = t.id
        WHERE e.event_type IN ('final_reply_weixin_accepted','push_result')
          AND e.payload_json LIKE '%"errcode": -14%'
          AND (t.status='pushed_to_wecom' OR t.push_status='pushed_to_wecom')
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    now = datetime.now(timezone.utc)
    result = []
    seen: set[str] = set()
    for row in rows:
        item = dict(row)
        task_id = str(item.get("id") or "")
        if task_id in seen:
            continue
        seen.add(task_id)
        item["account"] = account_of(row)
        item["age_seconds"] = age_seconds(item.get("event_created_at") or item.get("updated_at"), now)
        result.append(item)
    return result


def recent_event_summary(db: sqlite3.Connection, limit: int = 120) -> dict[str, int]:
    events: dict[str, int] = {}
    for row in db.execute(
        """
        SELECT event_type, COUNT(*) AS n
        FROM (
          SELECT event_type FROM mobile_events ORDER BY id DESC LIMIT ?
        )
        GROUP BY event_type
        ORDER BY n DESC
        """,
        (max(1, int(limit)),),
    ).fetchall():
        events[str(row["event_type"])] = int(row["n"])
    return events


def event_noise_health(db: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    """Summarize event-table write pressure without reading task payloads."""
    noisy_types = sorted(str(item) for item in NOISY_EVENT_TYPES)
    total_events = int(db.execute("SELECT COUNT(*) FROM mobile_events").fetchone()[0])
    index_exists = bool(
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_mobile_events_noise_guard'"
        ).fetchone()
    )
    top_event_types = [
        dict(row)
        for row in db.execute(
            """
            SELECT event_type, COUNT(*) AS count
            FROM mobile_events
            GROUP BY event_type
            ORDER BY count DESC
            LIMIT 15
            """
        ).fetchall()
    ]
    top_noisy_event_types = [
        dict(row)
        for row in db.execute(
            """
            SELECT event_type, COUNT(*) AS count
            FROM mobile_events
            WHERE event_type IN ({})
            GROUP BY event_type
            ORDER BY count DESC
            LIMIT 15
            """.format(",".join("?" for _ in noisy_types)),
            noisy_types,
        ).fetchall()
    ] if noisy_types else []
    suppressed_markers = [
        dict(row)
        for row in db.execute(
            """
            SELECT key, value, updated_at
            FROM mobile_runtime
            WHERE key LIKE 'event_noise_guard:%'
            ORDER BY updated_at DESC
            LIMIT 20
            """
        ).fetchall()
    ]
    suppressed_total = 0
    parsed_markers: list[dict[str, Any]] = []
    for row in suppressed_markers:
        try:
            marker = json.loads(str(row.get("value") or "{}"))
        except Exception:
            marker = {}
        count = int(marker.get("suppressed_count") or 0) if isinstance(marker, dict) else 0
        suppressed_total += count
        parsed_markers.append(
            {
                "key": row.get("key"),
                "event_type": marker.get("event_type") if isinstance(marker, dict) else "",
                "task_id": marker.get("task_id") if isinstance(marker, dict) else "",
                "suppressed_count": count,
                "updated_at": row.get("updated_at"),
            }
        )
    noisy_total = sum(int(item.get("count") or 0) for item in top_noisy_event_types)
    return {
        "ok": index_exists,
        "total_events": total_events,
        "guard_seconds": event_noise_guard_seconds(config),
        "guard_index_exists": index_exists,
        "noisy_event_types_count": len(noisy_types),
        "top_event_types": top_event_types,
        "top_noisy_event_types": top_noisy_event_types,
        "top_noisy_event_rows": noisy_total,
        "suppressed_marker_count": len(suppressed_markers),
        "suppressed_recent_total": suppressed_total,
        "suppressed_recent": parsed_markers,
        "policy": {
            "semantic_events_guarded": False,
            "archive_cleanup": "dry_run_only_in_current_maintenance",
        },
    }


def event_archive_dry_run(db: sqlite3.Connection, retention_hours: int = 24) -> dict[str, Any]:
    """Estimate safely archiveable diagnostic noise; does not mutate the DB."""
    noisy_types = sorted(str(item) for item in NOISY_EVENT_TYPES)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(retention_hours)))).isoformat()
    if not noisy_types:
        return {
            "ok": True,
            "dry_run": True,
            "retention_hours": retention_hours,
            "candidate_count": 0,
            "by_event_type": [],
            "policy": "no noisy event types configured",
        }
    rows = [
        dict(row)
        for row in db.execute(
            """
            SELECT e.event_type, COUNT(*) AS count, MIN(e.created_at) AS oldest, MAX(e.created_at) AS newest
            FROM mobile_events e
            LEFT JOIN mobile_tasks t ON t.id=e.task_id
            WHERE e.event_type IN ({})
              AND e.created_at < ?
              AND (t.id IS NULL OR (
                t.status NOT IN ('pending','queued_for_codex','sent_to_codex','processing')
                AND COALESCE(t.push_status, '') NOT IN ('reply_pending','reply_retrying','reply_sending')
              ))
            GROUP BY e.event_type
            ORDER BY count DESC
            """.format(",".join("?" for _ in noisy_types)),
            [*noisy_types, cutoff],
        ).fetchall()
    ]
    count = sum(int(row.get("count") or 0) for row in rows)
    return {
        "ok": True,
        "dry_run": True,
        "retention_hours": retention_hours,
        "cutoff": cutoff,
        "candidate_count": count,
        "by_event_type": rows,
        "policy": (
            "diagnostic noisy events only; active/pending/reply-backlog tasks are excluded; "
            "no deletion or VACUUM is performed by this dry-run"
        ),
    }


def _event_archive_candidate_ids(db: sqlite3.Connection, retention_hours: int) -> list[int]:
    noisy_types = sorted(str(item) for item in NOISY_EVENT_TYPES)
    if not noisy_types:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(retention_hours)))).isoformat()
    rows = db.execute(
        """
        SELECT e.id
        FROM mobile_events e
        LEFT JOIN mobile_tasks t ON t.id=e.task_id
        WHERE e.event_type IN ({})
          AND e.created_at < ?
          AND (t.id IS NULL OR (
            t.status NOT IN ('pending','queued_for_codex','sent_to_codex','processing')
            AND COALESCE(t.push_status, '') NOT IN ('reply_pending','reply_retrying','reply_sending')
          ))
        ORDER BY e.id
        """.format(",".join("?" for _ in noisy_types)),
        [*noisy_types, cutoff],
    ).fetchall()
    return [int(row["id"] if isinstance(row, sqlite3.Row) else row[0]) for row in rows]


def bridge_db_event_archive(
    db_path: Path,
    *,
    retention_hours: int = 24,
    apply: bool = False,
    vacuum: bool = False,
) -> dict[str, Any]:
    """Archive old diagnostic event noise; never mutates tasks or sends replies."""
    db_path = Path(db_path)
    before = sqlite_health(db_path)
    if not db_path.exists():
        return {
            "schema": "bridge_db.event_archive.v1",
            "ok": False,
            "generated_at": maintenance_now_iso(),
            "dry_run": not apply,
            "reason": "database_not_found",
            "db_path": str(db_path),
        }
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        dry = event_archive_dry_run(db, retention_hours=retention_hours)
        candidate_ids = _event_archive_candidate_ids(db, retention_hours)
        if not apply:
            return {
                "schema": "bridge_db.event_archive.v1",
                "ok": bool(before.get("exists")) and str(before.get("integrity_check") or "").lower() in {"", "ok"},
                "generated_at": maintenance_now_iso(),
                "dry_run": True,
                "db_path": str(db_path),
                "before": before,
                "event_archive_dry_run": dry,
                "candidate_id_count": len(candidate_ids),
                "would_vacuum": bool(vacuum),
                "policy": "Dry-run only. Apply archives noisy diagnostic events and excludes active/pending/reply-backlog tasks.",
            }

        archived_at = maintenance_now_iso()
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_events_archive (
                id INTEGER PRIMARY KEY,
                task_id TEXT,
                source TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                archived_at TEXT NOT NULL,
                archive_reason TEXT NOT NULL
            )
            """
        )
        archived_count = 0
        deleted_count = 0
        for offset in range(0, len(candidate_ids), 500):
            chunk = candidate_ids[offset : offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            archive_result = db.execute(
                f"""
                INSERT OR IGNORE INTO mobile_events_archive
                (id, task_id, source, event_type, payload_json, created_at, archived_at, archive_reason)
                SELECT id, task_id, source, event_type, payload_json, created_at, ?, ?
                FROM mobile_events
                WHERE id IN ({placeholders})
                """,
                [archived_at, "approved_noisy_event_archive", *chunk],
            )
            archived_count += int(archive_result.rowcount if archive_result.rowcount is not None else 0)
            delete_result = db.execute(f"DELETE FROM mobile_events WHERE id IN ({placeholders})", chunk)
            deleted_count += int(delete_result.rowcount if delete_result.rowcount is not None else 0)
        db.commit()
        vacuum_result = {"requested": bool(vacuum), "ran": False}
        if vacuum:
            db.execute("VACUUM")
            vacuum_result["ran"] = True
    after = sqlite_health(db_path)
    return {
        "schema": "bridge_db.event_archive.v1",
        "ok": str(after.get("integrity_check") or "").lower() in {"", "ok"},
        "generated_at": maintenance_now_iso(),
        "dry_run": False,
        "db_path": str(db_path),
        "retention_hours": retention_hours,
        "candidate_id_count": len(candidate_ids),
        "archived_count": archived_count,
        "deleted_count": deleted_count,
        "vacuum": vacuum_result,
        "before": before,
        "after": after,
        "policy": "Archived noisy diagnostic events only; active/pending/reply-backlog task events are excluded; task rows are untouched.",
    }


def bridge_db_archive_offload(
    db_path: Path,
    *,
    archive_path: Path | None = None,
    apply: bool = False,
    vacuum: bool = False,
) -> dict[str, Any]:
    """Move in-DB event archive rows to a separate archive SQLite file."""
    db_path = Path(db_path)
    archive_path = archive_path or (ROOT / "archives" / f"mobile_events_archive_{datetime.now(timezone.utc).strftime('%Y%m')}.sqlite")
    before = sqlite_health(db_path)
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        row = db.execute(
            "SELECT COUNT(*) AS rows, COALESCE(SUM(LENGTH(payload_json)), 0) AS payload_bytes FROM mobile_events_archive"
        ).fetchone()
        archive_rows = int(row["rows"] or 0)
        payload_bytes = int(row["payload_bytes"] or 0)
        if not apply:
            return {
                "schema": "bridge_db.archive_offload.v1",
                "ok": str(before.get("integrity_check") or "").lower() in {"", "ok"},
                "generated_at": maintenance_now_iso(),
                "dry_run": True,
                "db_path": str(db_path),
                "archive_path": str(archive_path),
                "archive_rows": archive_rows,
                "payload_bytes": payload_bytes,
                "would_vacuum": bool(vacuum),
                "policy": "Dry-run only. Apply copies mobile_events_archive to an external SQLite file before deleting copied rows from the main DB.",
            }
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [dict(item) for item in db.execute("SELECT id, task_id, source, event_type, payload_json, created_at, archived_at, archive_reason FROM mobile_events_archive ORDER BY id")]
        with sqlite3.connect(archive_path) as archive_db:
            archive_db.execute(
                """
                CREATE TABLE IF NOT EXISTS mobile_events_archive (
                    id INTEGER PRIMARY KEY,
                    task_id TEXT,
                    source TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    archive_reason TEXT NOT NULL,
                    offloaded_at TEXT NOT NULL
                )
                """
            )
            offloaded_at = maintenance_now_iso()
            archive_db.executemany(
                """
                INSERT OR IGNORE INTO mobile_events_archive
                (id, task_id, source, event_type, payload_json, created_at, archived_at, archive_reason, offloaded_at)
                VALUES (:id, :task_id, :source, :event_type, :payload_json, :created_at, :archived_at, :archive_reason, :offloaded_at)
                """,
                [{**item, "offloaded_at": offloaded_at} for item in rows],
            )
            archive_db.commit()
        deleted_count = 0
        for offset in range(0, len(rows), 500):
            chunk = [int(item["id"]) for item in rows[offset : offset + 500]]
            placeholders = ",".join("?" for _ in chunk)
            result = db.execute(f"DELETE FROM mobile_events_archive WHERE id IN ({placeholders})", chunk)
            deleted_count += int(result.rowcount if result.rowcount is not None else 0)
        db.commit()
        vacuum_result = {"requested": bool(vacuum), "ran": False}
        if vacuum:
            db.execute("VACUUM")
            vacuum_result["ran"] = True
    after = sqlite_health(db_path)
    return {
        "schema": "bridge_db.archive_offload.v1",
        "ok": str(after.get("integrity_check") or "").lower() in {"", "ok"} and archive_path.exists(),
        "generated_at": maintenance_now_iso(),
        "dry_run": False,
        "db_path": str(db_path),
        "archive_path": str(archive_path),
        "copied_count": len(rows),
        "deleted_count": deleted_count,
        "payload_bytes": payload_bytes,
        "vacuum": vacuum_result,
        "before": before,
        "after": after,
        "policy": "External archive preserves event evidence; main DB keeps live events and task state only.",
    }


def route_summary(active: list[dict[str, Any]], pending: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    routes: dict[str, dict[str, Any]] = {}
    for item in active:
        key = str(item.get("route_key") or "")
        route = routes.setdefault(
            key,
            {
                "route_key": key,
                "account": item.get("account"),
                "delivery_mode": item.get("delivery_mode") or "",
                "thread_id": item.get("codex_thread_id") or "",
                "effective_thread_id": item.get("effective_thread_id") or item.get("codex_thread_id") or "",
                "thread_resolution_state": item.get("thread_resolution_state") or "",
                "active_count": 0,
                "pending_count": 0,
                "supplement_waiting_count": 0,
                "oldest_active_age_seconds": None,
                "oldest_pending_age_seconds": None,
                "oldest_supplement_waiting_age_seconds": None,
                "active_task_ids": [],
                "pending_task_ids": [],
                "supplement_waiting_task_ids": [],
            },
        )
        route["active_count"] += 1
        route["active_task_ids"].append(item.get("id"))
        age = item.get("age_seconds")
        if age is not None:
            current = route.get("oldest_active_age_seconds")
            route["oldest_active_age_seconds"] = age if current is None else max(int(current), int(age))
    for item in pending:
        key = str(item.get("route_key") or "")
        route = routes.setdefault(
            key,
            {
                "route_key": key,
                "account": item.get("account"),
                "delivery_mode": item.get("delivery_mode") or "",
                "thread_id": item.get("codex_thread_id") or "",
                "effective_thread_id": item.get("effective_thread_id") or item.get("codex_thread_id") or "",
                "thread_resolution_state": item.get("thread_resolution_state") or "",
                "active_count": 0,
                "pending_count": 0,
                "supplement_waiting_count": 0,
                "oldest_active_age_seconds": None,
                "oldest_pending_age_seconds": None,
                "oldest_supplement_waiting_age_seconds": None,
                "active_task_ids": [],
                "pending_task_ids": [],
                "supplement_waiting_task_ids": [],
            },
        )
        if item.get("pending_kind") == "supplement_waiting_mcp_ack":
            route["supplement_waiting_count"] += 1
            route["supplement_waiting_task_ids"].append(item.get("id"))
            age = item.get("age_seconds")
            if age is not None:
                current = route.get("oldest_supplement_waiting_age_seconds")
                route["oldest_supplement_waiting_age_seconds"] = age if current is None else max(int(current), int(age))
            continue
        route["pending_count"] += 1
        route["pending_task_ids"].append(item.get("id"))
        age = item.get("age_seconds")
        if age is not None:
            current = route.get("oldest_pending_age_seconds")
            route["oldest_pending_age_seconds"] = age if current is None else max(int(current), int(age))
    return routes


def account_summary(
    counts: dict[str, Any],
    active: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    reply_problems: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    accounts: dict[str, dict[str, Any]] = {}

    def ensure(account: Any) -> dict[str, Any]:
        account_id = str(account or "").strip() or "(none)"
        return accounts.setdefault(
            account_id,
            {
                "account": account_id,
                "delivery_mode": delivery_mode_for_account(config, account_id),
                "status_counts": {},
                "pending_count": 0,
                "supplement_waiting_count": 0,
                "active_count": 0,
                "reply_backlog_count": 0,
                "oldest_pending_age_seconds": None,
                "oldest_supplement_waiting_age_seconds": None,
                "oldest_active_age_seconds": None,
                "pending_task_ids": [],
                "supplement_waiting_task_ids": [],
                "active_task_ids": [],
                "reply_task_ids": [],
            },
        )

    by_account_status = counts.get("by_account_status", {}) if isinstance(counts, dict) else {}
    if isinstance(by_account_status, dict):
        for account, status_counts in by_account_status.items():
            entry = ensure(account)
            if isinstance(status_counts, dict):
                entry["status_counts"] = {str(key): int(value or 0) for key, value in status_counts.items()}

    for item in pending:
        entry = ensure(item.get("account"))
        if item.get("pending_kind") == "supplement_waiting_mcp_ack":
            entry["supplement_waiting_count"] += 1
            entry["supplement_waiting_task_ids"].append(item.get("id"))
            age = item.get("age_seconds")
            if age is not None:
                current = entry.get("oldest_supplement_waiting_age_seconds")
                entry["oldest_supplement_waiting_age_seconds"] = age if current is None else max(int(current), int(age))
            continue
        entry["pending_count"] += 1
        entry["pending_task_ids"].append(item.get("id"))
        age = item.get("age_seconds")
        if age is not None:
            current = entry.get("oldest_pending_age_seconds")
            entry["oldest_pending_age_seconds"] = age if current is None else max(int(current), int(age))

    for item in active:
        entry = ensure(item.get("account"))
        entry["active_count"] += 1
        entry["active_task_ids"].append(item.get("id"))
        age = item.get("age_seconds")
        if age is not None:
            current = entry.get("oldest_active_age_seconds")
            entry["oldest_active_age_seconds"] = age if current is None else max(int(current), int(age))

    for item in reply_problems:
        entry = ensure(item.get("account"))
        entry["reply_backlog_count"] += 1
        entry["reply_task_ids"].append(item.get("id"))

    for entry in accounts.values():
        entry["pending_task_ids"] = entry["pending_task_ids"][:8]
        entry["supplement_waiting_task_ids"] = entry["supplement_waiting_task_ids"][:8]
        entry["active_task_ids"] = entry["active_task_ids"][:8]
        entry["reply_task_ids"] = entry["reply_task_ids"][:8]
    return accounts


def top_routes(routes: dict[str, dict[str, Any]], key_name: str, limit: int = 6) -> list[dict[str, Any]]:
    filtered = [route for route in routes.values() if int(route.get(key_name) or 0) > 0]
    filtered.sort(
        key=lambda route: (
            -int(route.get(key_name) or 0),
            -int(
                route.get("oldest_pending_age_seconds")
                or route.get("oldest_supplement_waiting_age_seconds")
                or route.get("oldest_active_age_seconds")
                or 0
            ),
            str(route.get("route_key") or ""),
        )
    )
    return [
        {
            "route_key": route.get("route_key"),
            "account": route.get("account"),
            "delivery_mode": route.get("delivery_mode"),
            "thread_id": route.get("thread_id"),
            key_name: route.get(key_name),
            "oldest_pending_age_seconds": route.get("oldest_pending_age_seconds"),
            "oldest_supplement_waiting_age_seconds": route.get("oldest_supplement_waiting_age_seconds"),
            "oldest_active_age_seconds": route.get("oldest_active_age_seconds"),
            "pending_task_ids": (route.get("pending_task_ids") or [])[:5],
            "supplement_waiting_task_ids": (route.get("supplement_waiting_task_ids") or [])[:5],
            "active_task_ids": (route.get("active_task_ids") or [])[:5],
        }
        for route in filtered[:limit]
    ]


def top_accounts(accounts: dict[str, dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    rows = list(accounts.values())
    rows.sort(
        key=lambda item: (
            -int(item.get("pending_count") or 0),
            -int(item.get("active_count") or 0),
            -int(item.get("reply_backlog_count") or 0),
            str(item.get("account") or ""),
        )
    )
    return [
        {
            "account": item.get("account"),
            "delivery_mode": item.get("delivery_mode"),
            "pending_count": item.get("pending_count"),
            "active_count": item.get("active_count"),
            "reply_backlog_count": item.get("reply_backlog_count"),
            "oldest_pending_age_seconds": item.get("oldest_pending_age_seconds"),
            "oldest_active_age_seconds": item.get("oldest_active_age_seconds"),
            "pending_task_ids": item.get("pending_task_ids"),
            "active_task_ids": item.get("active_task_ids"),
            "reply_task_ids": item.get("reply_task_ids"),
        }
        for item in rows[:limit]
    ]


def live_state_health(path: Path = DEFAULT_DASHBOARD_LIVE_STATE) -> dict[str, Any]:
    data = load_json(path, {})
    result = {"path": str(path), "exists": path.exists(), "ok": False}
    if not isinstance(data, dict) or not data:
        result["reason"] = "missing_or_invalid"
        return result
    generated_at = str(data.get("generated_at") or "")
    age = age_seconds(generated_at)
    result.update(
        {
            "ok": bool(data.get("ok")) and age is not None and age <= 15,
            "connected": bool(data.get("connected")),
            "generated_at": generated_at,
            "age_seconds": age,
            "last_error": str(data.get("last_error") or ""),
            "tmp_files": [str(item) for item in sorted(path.parent.glob(path.name + ".*.tmp"))[:20]],
        }
    )
    if not result["ok"]:
        result["reason"] = "stale_or_disconnected"
    return result


def cdp_route_diagnostics(
    config: dict[str, Any],
    ports: dict[str, Any],
    pending: list[dict[str, Any]],
    recent_events: dict[str, int],
    os_port_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    cdp_endpoint = resolve_codex_cdp_endpoint(config)
    host = str(cdp_endpoint.get("host") or trigger.get("codex_cdp_host") or "localhost").strip() or "localhost"
    port = int(cdp_endpoint.get("port") or trigger.get("codex_cdp_port") or 9229)
    script = Path(
        trigger.get("codex_cdp_script")
        or PROJECT_ROOT / "_tools" / "codex-cdp-tools" / "codex_cdp_send.js"
    )
    configured_start_script = str(trigger.get("codex_cdp_start_script") or "").strip()
    start_scripts: list[Path] = []
    if configured_start_script:
        start_scripts.append(Path(configured_start_script).expanduser())
    else:
        start_scripts.append(Path.home() / ".codex" / "scripts" / "start-codex-desktop-elevated.ps1")

    primary_pending = [item for item in pending if item.get("account") == "primary"]
    cdp_port = ports.get("codex_cdp", {}) if isinstance(ports.get("codex_cdp"), dict) else {}
    os_port_state = os_port_state or {}
    recent_probe_failures = int(recent_events.get("thread_delivery_visible_cdp_probe_failed") or 0)
    script_exists = script.exists()
    start_script_entries = [
        {
            "path": str(path),
            "exists": path.exists(),
            "admin_startup": path.name.lower() == "start-codex-desktop-elevated.ps1",
        }
        for path in start_scripts
    ]
    start_script_available = any(bool(item.get("exists")) for item in start_script_entries)
    admin_start_script_available = any(
        bool(item.get("exists")) and bool(item.get("admin_startup")) for item in start_script_entries
    )
    layer = "ready"
    if not cdp_port.get("ok"):
        if int(os_port_state.get("stale_count") or 0) > 0 and int(os_port_state.get("live_count") or 0) == 0:
            layer = "stale_os_listener"
        elif int(os_port_state.get("listener_count") or 0) > 0:
            layer = "listener_unresponsive"
        else:
            layer = "transport_down"
    elif not script_exists:
        layer = "send_script_missing"
    elif not start_script_available:
        layer = "startup_script_missing"
    elif recent_probe_failures >= 5:
        layer = "probe_unstable"
    return {
        "ok": bool(cdp_port.get("ok")) and script_exists,
        "layer": layer,
        "host": host,
        "port": port,
        "endpoint_source": cdp_endpoint.get("source"),
        "endpoint_state": cdp_endpoint.get("state") or {},
        "endpoint_probes": cdp_endpoint.get("probes") or [],
        "process_ports": cdp_endpoint.get("process_ports") or [],
        "port_state": cdp_port,
        "os_port_state": os_port_state,
        "send_script": {"path": str(script), "exists": script_exists},
        "start_scripts": start_script_entries,
        "start_script_available": start_script_available,
        "admin_start_script_available": admin_start_script_available,
        "startup_contract": (
            "CDP recovery must launch Codex Desktop through "
            "start-codex-desktop-elevated.ps1 with CODEX_CDP_PORT; "
            "do not use a plain non-admin Codex launch."
        ),
        "primary_pending_count": len(primary_pending),
        "primary_pending_task_ids": [str(item.get("id") or "") for item in primary_pending[:10]],
        "recent_visible_probe_failures": recent_probe_failures,
        "safe_boundary": "diagnostic only: do not clear pending, send Weixin replies, change bindings, or switch primary route automatically",
        "recommendation": (
            "Restore Codex Desktop visible CDP through the configured elevated startup script; "
            "keep backup accounts on app-server routes."
        ),
    }


def load_codex_cdp_endpoint_state() -> dict[str, Any]:
    data = load_json(CDP_ENDPOINT_STATE, {})
    return data if isinstance(data, dict) else {}


def codex_cdp_endpoint_ready(host: str, port: int, timeout: float = 0.35) -> dict[str, Any]:
    tcp = tcp_check(int(port), host=host, timeout=timeout)
    version: dict[str, Any] = {"ok": False, "reason": "transport_not_ready"}
    if tcp.get("ok"):
        version = http_json("/json/version", int(port), host=host, timeout=timeout)
    return {
        "ok": bool(tcp.get("ok")) and bool(version.get("ok")),
        "host": host,
        "port": int(port),
        "tcp": tcp,
        "version": version,
    }


def cdp_discovery_cooldown_active(
    state: dict[str, Any],
    preferred_host: str,
    preferred_port: int,
    seconds: int = 30,
) -> bool:
    if str(state.get("source") or "") != "discovery-failed":
        return False
    if str(state.get("preferred_host") or "") != str(preferred_host or ""):
        return False
    try:
        state_preferred_port = int(state.get("preferred_port") or 0)
    except Exception:
        return False
    if state_preferred_port != int(preferred_port):
        return False
    parsed = parse_iso(str(state.get("verified_at") or ""))
    if not parsed:
        return False
    return (datetime.now(timezone.utc) - parsed).total_seconds() < seconds


def cdp_candidate_ports(preferred_port: int, state_port: int = 0) -> list[int]:
    raw_ports: list[Any] = [
        preferred_port,
        state_port,
        os.environ.get("CODEX_CDP_PORT"),
        *codex_desktop_cdp_process_ports(),
        9230,
        9229,
        9222,
        9223,
    ]
    ports: list[int] = []
    for item in raw_ports:
        try:
            port = int(item or 0)
        except Exception:
            continue
        if 0 < port < 65536 and port not in ports:
            ports.append(port)
    return ports


def codex_desktop_cdp_process_ports() -> list[int]:
    """Discover remote-debugging ports from currently running Codex Desktop processes."""
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -match '^Codex(\.exe)?$' -and
    $_.CommandLine -match '--remote-debugging-port=(\d+)'
  } |
  ForEach-Object {
    if ($_.CommandLine -match '--remote-debugging-port=(\d+)') {
      [pscustomobject]@{ pid = [int]$_.ProcessId; port = [int]$Matches[1]; command_line = $_.CommandLine }
    }
  } |
  ConvertTo-Json -Depth 4
"""
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return []
        parsed = json.loads(raw)
    except Exception:
        return []
    items = parsed if isinstance(parsed, list) else [parsed]
    ports: list[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            port = int(item.get("port") or 0)
        except Exception:
            continue
        if 0 < port < 65536 and port not in ports:
            ports.append(port)
    return ports


def resolve_codex_cdp_endpoint(config: dict[str, Any]) -> dict[str, Any]:
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    preferred_host = str(trigger.get("codex_cdp_host") or "localhost").strip() or "localhost"
    preferred_port = int(trigger.get("codex_cdp_port") or 9229)
    process_discovery_enabled = bool(trigger.get("codex_cdp_process_discovery", True))
    runtime_state_enabled = bool(trigger.get("codex_cdp_runtime_state", True))
    state = load_codex_cdp_endpoint_state() if runtime_state_enabled else {}
    try:
        state_port = int(state.get("port") or 0)
    except Exception:
        state_port = 0
    state_host = str(state.get("host") or "").strip()
    if state_host and state_port > 0:
        ready = codex_cdp_endpoint_ready(state_host, state_port)
        if ready.get("ok"):
            return {"host": state_host, "port": state_port, "source": "runtime-state", "state": state, "ready": ready}
    preferred_ready = codex_cdp_endpoint_ready(preferred_host, preferred_port)
    if preferred_ready.get("ok"):
        return {
            "host": preferred_host,
            "port": preferred_port,
            "source": "preferred-config",
            "state": state,
            "ready": preferred_ready,
        }
    if not process_discovery_enabled:
        return {
            "host": preferred_host,
            "port": preferred_port,
            "source": "preferred-config",
            "state": state,
            "ready": preferred_ready,
            "process_ports": [],
            "process_discovery_skipped": "disabled_by_config",
        }
    process_ports = codex_desktop_cdp_process_ports()
    if cdp_discovery_cooldown_active(state, preferred_host, preferred_port) and not process_ports:
        return {
            "host": preferred_host,
            "port": preferred_port,
            "source": "preferred-config",
            "state": state,
            "ready": preferred_ready,
            "discovery_skipped": "cooldown",
        }
    probes: list[dict[str, Any]] = []
    for port in cdp_candidate_ports(preferred_port, state_port):
        for host in dict.fromkeys([preferred_host, "localhost", "127.0.0.1"]):
            ready = codex_cdp_endpoint_ready(str(host or "localhost"), port)
            probes.append(
                {
                    "host": ready.get("host"),
                    "port": ready.get("port"),
                    "ok": bool(ready.get("ok")),
                    "tcp_ok": bool((ready.get("tcp") or {}).get("ok")),
                    "version_ok": bool((ready.get("version") or {}).get("ok")),
                    "version_reason": str((ready.get("version") or {}).get("reason") or ""),
                }
            )
            if ready.get("ok"):
                return {
                    "host": str(ready["host"]),
                    "port": int(ready["port"]),
                    "source": "codex-process-discovery" if int(ready["port"]) in process_ports else "discovered",
                    "state": state,
                    "ready": ready,
                    "probes": probes,
                    "process_ports": process_ports,
                }
    return {
        "host": preferred_host,
        "port": int(process_ports[0] if process_ports else preferred_port),
        "source": "codex-process-discovery-unready" if process_ports else "preferred-config",
        "state": state,
        "ready": preferred_ready,
        "probes": probes,
        "process_ports": process_ports,
    }


def mobile_mcp_direct_smoke(config: dict[str, Any], timeout_seconds: int = 8) -> dict[str, Any]:
    """Check whether the mobile bridge MCP server starts and answers JSON-RPC."""
    command = str(config.get("mcp", {}).get("mobile_openclaw_command") or sys.executable)
    script = Path(
        config.get("mcp", {}).get("mobile_openclaw_script")
        or ROOT / "mobile_bridge_mcp_server.py"
    )
    config_path = Path(str(config.get("_config_path") or ROOT / "config.local.json"))
    result: dict[str, Any] = {
        "ok": False,
        "command": command,
        "script": str(script),
        "script_exists": script.exists(),
        "config": str(config_path),
        "transport": "stdio",
    }
    if not script.exists():
        result["reason"] = "script_missing"
        return result
    cmd = [command, str(script), "--config", str(config_path)]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        result["reason"] = f"spawn_failed: {exc}"
        return result

    responses: list[dict[str, Any]] = []
    try:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "bridge.health", "arguments": {}}},
        ]
        assert proc.stdin is not None
        assert proc.stdout is not None
        deadline = time.time() + max(1, int(timeout_seconds))
        for request in requests:
            proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            while True:
                if time.time() > deadline:
                    raise TimeoutError("timed out waiting for MCP response")
                line = proc.stdout.readline()
                if line:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(f"MCP process exited early rc={proc.returncode}")
                time.sleep(0.05)
            try:
                parsed = json.loads(line)
            except Exception:
                parsed = {"raw": short(line, 500)}
            responses.append(parsed if isinstance(parsed, dict) else {"raw": parsed})
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 4, "method": "exit", "params": {}}) + "\n")
        proc.stdin.flush()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    except Exception as exc:
        result["reason"] = str(exc)
        try:
            proc.kill()
        except Exception:
            pass
    stderr = ""
    try:
        if proc.stderr is not None:
            stderr = proc.stderr.read()
    except Exception:
        stderr = ""
    tool_names: list[str] = []
    if len(responses) >= 2:
        tools = responses[1].get("result", {}).get("tools", []) if isinstance(responses[1].get("result"), dict) else []
        if isinstance(tools, list):
            tool_names = [str(item.get("name") or "") for item in tools if isinstance(item, dict)]
    health_ok = False
    if len(responses) >= 3:
        third = responses[2]
        health_ok = bool(third.get("result")) and not bool(third.get("error"))
    result.update(
        {
            "ok": len(responses) == 3 and bool(tool_names) and health_ok,
            "returncode": proc.returncode,
            "responses": len(responses),
            "tool_names": tool_names,
            "stderr": short(stderr, 1000),
        }
    )
    if not result["ok"] and "reason" not in result:
        result["reason"] = "direct_jsonrpc_failed"
    return result


def mobile_mcp_local_fallback_health(config: dict[str, Any], timeout_seconds: int = 8) -> dict[str, Any]:
    """Read-only probe for the local CLI fallback used after session MCP transport loss."""
    try:
        from mobile_openclaw_cli import mobile_mcp_stdio_tool_call

        result = mobile_mcp_stdio_tool_call(
            config,
            "bridge.health",
            {},
            timeout_seconds=timeout_seconds,
        )
        return {
            "ok": bool(result.get("ok")),
            "fallback": "local_stdio_mcp",
            "tool": "bridge.health",
            "script": result.get("script"),
            "script_exists": result.get("script_exists"),
            "reason": result.get("reason") or "",
            "stderr": result.get("stderr") or "",
            "returncode": result.get("returncode"),
            "read_only": True,
            "note": "fallback launches a fresh mobile MCP stdio process; get/ack fallback uses the same MCP server tool implementation",
        }
    except Exception as exc:
        return {
            "ok": False,
            "fallback": "local_stdio_mcp",
            "reason": str(exc),
            "read_only": True,
        }


def toml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def codex_config_path(config: dict[str, Any] | None = None) -> Path:
    mcp_config = (config or {}).get("mcp", {}) if isinstance((config or {}).get("mcp"), dict) else {}
    configured = str(mcp_config.get("codex_config_path") or "").strip()
    return Path(configured) if configured else CODEX_CONFIG_PATH


def read_codex_config(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "path": str(path),
        "exists": path.exists(),
        "parse_ok": False,
        "data": {},
        "servers": {},
        "server_names": [],
    }
    if not path.exists():
        result["reason"] = "config_missing"
        return result
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8-sig"))
    except Exception as exc:
        result["reason"] = f"parse_failed: {exc}"
        return result
    servers = data.get("mcp_servers") if isinstance(data.get("mcp_servers"), dict) else {}
    result.update(
        {
            "ok": True,
            "parse_ok": True,
            "data": data,
            "servers": servers,
            "server_names": sorted(str(name) for name in servers.keys()),
        }
    )
    return result


def infer_codex_mcp_python_command(parsed_config: dict[str, Any]) -> str:
    servers = parsed_config.get("servers") if isinstance(parsed_config.get("servers"), dict) else {}
    for name in ("agent-bridge", "mobile-openclaw-bridge", "gui-automation", "filesystem-admin"):
        server = servers.get(name) if isinstance(servers.get(name), dict) else {}
        command = str(server.get("command") or "").strip()
        if command:
            return command
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe"
    return str(bundled if bundled.exists() else Path(sys.executable))


def python_module_health(command: str, modules: tuple[str, ...], timeout: int = 20) -> dict[str, Any]:
    command = str(command or "").strip()
    if not command:
        return {"ok": False, "command": "", "reason": "missing_python_command"}
    if not Path(command).exists():
        return {"ok": False, "command": command, "reason": "python_not_found"}
    probe = """
import importlib.util, json, sys
mods = %r
status = {m: bool(importlib.util.find_spec(m)) for m in mods}
print(json.dumps({"executable": sys.executable, "modules": status}, ensure_ascii=False))
""" % (list(modules),)
    proc = run_capture_tree_timeout([command, "-c", probe], timeout=max(1, int(timeout)))
    if proc.get("spawn_failed") or proc.get("timed_out"):
        return {
            "ok": False,
            "command": command,
            "reason": "module_probe_timeout" if proc.get("timed_out") else str(proc.get("reason") or "spawn_failed"),
            "exception_type": str(proc.get("exception_type") or ""),
            "timed_out": bool(proc.get("timed_out")),
            "elapsed_ms": int(proc.get("elapsed_ms") or 0),
        }
    try:
        parsed = json.loads(str(proc.get("stdout") or "{}"))
    except Exception:
        parsed = {"stdout_tail": str(proc.get("stdout") or "")[-500:]}
    modules_status = parsed.get("modules") if isinstance(parsed.get("modules"), dict) else {}
    missing = [name for name in modules if not modules_status.get(name)]
    return {
        "ok": int(proc.get("returncode") or 0) == 0 and not missing,
        "command": command,
        "returncode": proc.get("returncode"),
        "executable": parsed.get("executable") or "",
        "modules": modules_status,
        "missing": missing,
        "stderr_tail": str(proc.get("stderr") or "")[-800:],
        "elapsed_ms": int(proc.get("elapsed_ms") or 0),
    }


def infer_gui_automation_python_command(parsed_config: dict[str, Any]) -> str:
    servers = parsed_config.get("servers") if isinstance(parsed_config.get("servers"), dict) else {}
    gui_server = servers.get("gui-automation") if isinstance(servers.get("gui-automation"), dict) else {}
    candidates = [
        os.environ.get("GUI_AUTOMATION_PYTHON"),
        str(gui_server.get("command") or ""),
        r"C:\Python314\python.exe",
        infer_codex_mcp_python_command(parsed_config),
        sys.executable,
    ]
    seen: set[str] = set()
    for candidate in candidates:
        command = str(candidate or "").strip()
        key = command.casefold()
        if not command or key in seen or not Path(command).exists():
            continue
        seen.add(key)
        if python_module_health(command, GUI_REQUIRED_MODULES, timeout=12).get("ok"):
            return command
    return infer_codex_mcp_python_command(parsed_config)


def expected_codex_mcp_specs(parsed_config: dict[str, Any]) -> list[dict[str, Any]]:
    retired_names = frozenset(load_decommissioned_mcp())
    hub_managed_names = frozenset(HUB_MANAGED_MCP_NAMES)
    if CODEX_STARTUP_BASELINE.exists():
        try:
            baseline = json.loads(CODEX_STARTUP_BASELINE.read_text(encoding="utf-8"))
            specs: list[dict[str, Any]] = []
            for name, raw_spec in (baseline.get("expected_mcp") or {}).items():
                if name in retired_names or name in hub_managed_names or not isinstance(raw_spec, dict):
                    continue
                spec = dict(raw_spec)
                spec["name"] = str(name)
                spec["description"] = str(spec.get("description") or f"Baseline Codex MCP: {name}")
                spec["repairable"] = True
                command = str(spec.get("command") or "").strip()
                spec["script_exists"] = bool(
                    not command
                    or command.startswith("http://")
                    or command.startswith("https://")
                    or Path(command).exists()
                )
                specs.append(spec)
            if specs:
                return specs
        except Exception:
            pass
    python_command = infer_codex_mcp_python_command(parsed_config)
    gui_python_command = infer_gui_automation_python_command(parsed_config)
    shared_env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    gui_ocr_python = str(PROJECT_ROOT / "_bridge" / "venvs" / "ocr-gpu-py312" / "Scripts" / "python.exe")
    gui_ocr_fallback_python = str(PROJECT_ROOT / "_bridge" / "venvs" / "ocr-py312" / "Scripts" / "python.exe")
    servers = parsed_config.get("servers") if isinstance(parsed_config.get("servers"), dict) else {}
    dynamic_specs: list[dict[str, Any]] = []
    for name, server in servers.items():
        if not isinstance(server, dict):
            continue
        if name == "node_repl" or name in retired_names or name in hub_managed_names:
            continue
        command = str(server.get("command") or "").strip()
        args = [str(item) for item in (server.get("args") or []) if str(item)]
        if not command and not args:
            continue
        env = server.get("env") if isinstance(server.get("env"), dict) else {}
        spec: dict[str, Any] = {
            "name": str(name),
            "description": f"Codex MCP server registered in config: {name}",
            "required": True,
            "repairable": True,
            "command": command or python_command,
            "args": args,
            "startup_timeout_sec": int(server.get("startup_timeout_sec") or 60),
            "env": {str(k): str(v) for k, v in env.items()},
            "config_only": True,
        }
        if name == "github":
            spec["description"] = "GitHub MCP server for repo, issue, PR, and authenticated GitHub operations"
            spec["reserved_marketplace"] = True
            spec["cli_visibility_optional"] = True
        dynamic_specs.append(spec)
    return [
        {
            "name": "mobile-openclaw-bridge",
            "description": "Weixin bridge MCP tools used by in-turn supplement polling and ack",
            "required": True,
            "repairable": True,
            "command": python_command,
            "args": [str(ROOT / "mobile_bridge_mcp_server.py")],
            "startup_timeout_sec": 60,
            "env": {
                **shared_env,
                "MOBILE_OPENCLAW_BRIDGE_CONFIG": str(ROOT / "config.local.json"),
            },
        },
        {
            "name": "gui-automation",
            "description": "Windows GUI automation MCP tools",
            "required": True,
            "repairable": (PROJECT_ROOT / "_bridge" / "gui_automation_mcp.py").exists(),
            "command": gui_python_command,
            "args": [str(PROJECT_ROOT / "_bridge" / "gui_automation_mcp.py")],
            "startup_timeout_sec": 60,
            "env": {
                **shared_env,
                "GUI_OCR_PYTHON": gui_ocr_python,
                "GUI_OCR_FALLBACK_PYTHON": gui_ocr_fallback_python,
                "GUI_OCR_DEVICE": "gpu",
            },
        },
        *dynamic_specs,
    ]


def codex_mcp_spec_status(servers: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    name = str(spec.get("name") or "")
    server = servers.get(name) if isinstance(servers.get(name), dict) else None
    script_path = Path(str((spec.get("args") or [""])[0] or ""))
    status: dict[str, Any] = {
        "name": name,
        "description": spec.get("description"),
        "required": bool(spec.get("required")),
        "registered": bool(server),
        "repairable": bool(spec.get("repairable")),
        "script": str(script_path),
        "script_exists": script_path.exists(),
        "issues": [],
    }
    if not server:
        if spec.get("config_only"):
            status["registered"] = False
            if spec.get("reserved_marketplace"):
                status["issues"].append("reserved_marketplace_config_only")
            else:
                status["issues"].append("missing_server")
            return status
        status["issues"].append("missing_server")
        return status
    expected_args = [str(item) for item in (spec.get("args") or [])]
    actual_args = [str(item) for item in (server.get("args") or [])] if isinstance(server.get("args"), list) else []
    if actual_args != expected_args:
        status["issues"].append("args_drift")
        status["actual_args"] = actual_args
        status["expected_args"] = expected_args
    expected_command = str(spec.get("command") or "")
    actual_command = str(server.get("command") or "")
    if expected_command and actual_command and actual_command.lower() != expected_command.lower():
        status["issues"].append("command_drift")
        status["actual_command"] = actual_command
        status["expected_command"] = expected_command
    env = server.get("env") if isinstance(server.get("env"), dict) else {}
    dynamic_env = SESSION_DYNAMIC_MCP_ENV.get(name, set())
    missing_env = [
        key
        for key, value in (spec.get("env") or {}).items()
        if key not in dynamic_env and str(env.get(key) or "") != str(value)
    ]
    dynamic_env_drift = [
        key
        for key, value in (spec.get("env") or {}).items()
        if key in dynamic_env and str(env.get(key) or "") != str(value)
    ]
    if missing_env:
        status["issues"].append("env_missing_or_drift")
        status["missing_or_drifted_env"] = missing_env
    if dynamic_env_drift:
        status["dynamic_env_drift_ignored"] = dynamic_env_drift
    return status


def codex_mcp_config_health(config: dict[str, Any]) -> dict[str, Any]:
    path = codex_config_path(config)
    parsed = read_codex_config(path)
    result: dict[str, Any] = {
        "ok": False,
        "path": str(path),
        "exists": parsed.get("exists"),
        "parse_ok": parsed.get("parse_ok"),
        "registered_servers": parsed.get("server_names") or [],
        "expected": [],
        "missing": [],
        "drifted": [],
        "repairable_missing": [],
        "repairable_drifted": [],
        "restart_required_after_repair": False,
    }
    if not parsed.get("ok"):
        result["reason"] = parsed.get("reason")
        return result
    servers = parsed.get("servers") if isinstance(parsed.get("servers"), dict) else {}
    expected = [codex_mcp_spec_status(servers, spec) for spec in expected_codex_mcp_specs(parsed)]
    missing = [
        item
        for item in expected
        if "missing_server" in item.get("issues", [])
        or "reserved_marketplace_config_only" in item.get("issues", [])
    ]
    drifted = [item for item in expected if item.get("registered") and item.get("issues")]
    repairable_missing = [
        item
        for item in missing
        if item.get("repairable") and (item.get("script_exists") or item.get("config_only"))
    ]
    repairable_drifted = [
        item
        for item in drifted
        if item.get("repairable")
        and (item.get("script_exists") or item.get("config_only"))
        and set(str(issue) for issue in (item.get("issues") or [])).issubset(
            {"args_drift", "command_drift", "env_missing_or_drift", "reserved_marketplace_config_only"}
        )
    ]
    result.update(
        {
            "ok": not missing and not drifted,
            "expected": expected,
            "missing": missing,
            "drifted": drifted,
            "repairable_missing": repairable_missing,
            "repairable_drifted": repairable_drifted,
            "restart_required_after_repair": bool(repairable_missing or repairable_drifted),
        }
    )
    return result


def codex_mcp_toml_block(spec: dict[str, Any]) -> str:
    name = str(spec.get("name") or "")
    lines = [
        "",
        f"[mcp_servers.{toml_string(name)}]",
        f"args = [{', '.join(toml_string(item) for item in (spec.get('args') or []))}]",
        f"command = {toml_string(spec.get('command') or sys.executable)}",
        f"startup_timeout_sec = {int(spec.get('startup_timeout_sec') or 60)}",
    ]
    env = spec.get("env") if isinstance(spec.get("env"), dict) else {}
    if env:
        lines.extend(["", f"[mcp_servers.{toml_string(name)}.env]"])
        for key, value in env.items():
            lines.append(f"{key} = {toml_string(value)}")
    return "\n".join(lines) + "\n"


def replace_codex_mcp_server_block(text: str, spec: dict[str, Any]) -> tuple[str, bool]:
    name = str(spec.get("name") or "")
    section = f"[mcp_servers.{toml_string(name)}]"
    start = text.find(section)
    if start < 0:
        return text, False
    end = len(text)
    same_prefix = f"[mcp_servers.{toml_string(name)}."
    for match in re.finditer(r"(?m)^\[", text[start + len(section) :]):
        index = start + len(section) + match.start()
        line_end = text.find("\n", index)
        line = text[index:] if line_end < 0 else text[index:line_end]
        if line.startswith(same_prefix):
            continue
        end = index
        break
    replacement = codex_mcp_toml_block(spec).lstrip("\n")
    prefix = text[:start]
    suffix = text[end:].lstrip("\n")
    return prefix.rstrip() + "\n\n" + replacement + ("\n" + suffix if suffix else ""), True


def repair_codex_mcp_config(config: dict[str, Any], health: dict[str, Any], apply: bool) -> dict[str, Any]:
    path = Path(str(health.get("path") or codex_config_path(config)))
    missing_names = {str(item.get("name") or "") for item in (health.get("repairable_missing") or [])}
    drifted_names = {str(item.get("name") or "") for item in (health.get("repairable_drifted") or [])}
    if not missing_names and not drifted_names:
        return {
            "ok": True,
            "applied": False,
            "skipped": True,
            "reason": "no repairable missing or drifted MCP server config entries",
        }
    parsed = read_codex_config(path)
    if not parsed.get("ok"):
        return {"ok": False, "applied": False, "reason": parsed.get("reason") or "codex config parse failed"}
    specs = [
        spec
        for spec in expected_codex_mcp_specs(parsed)
        if str(spec.get("name") or "") in missing_names or str(spec.get("name") or "") in drifted_names
    ]
    if not apply:
        return {
            "ok": True,
            "applied": False,
            "would_add": [str(spec.get("name") or "") for spec in specs if str(spec.get("name") or "") in missing_names],
            "would_rewrite": [str(spec.get("name") or "") for spec in specs if str(spec.get("name") or "") in drifted_names],
            "restart_required": True,
            "reason": "dry-run; pass --apply to repair known catalog MCP server entries",
        }
    backup = ""
    try:
        backup_result = create_routed_backup(
            [str(path)],
            category="bridge",
            purpose="maintenance-mcp-config",
            trigger="mobile-maintenance-repair",
            remark="maintenance-mcp-config",
        )
        if not backup_result.get("ok"):
            return {
                "ok": False,
                "applied": False,
                "reason": f"backup_failed: {backup_result.get('reason') or 'unknown'}",
                "backup": "",
            }
        backup_items = backup_result.get("items") or []
        backup = str(backup_items[0].get("backup_path") or "") if backup_items else ""
        text = path.read_text(encoding="utf-8-sig")
        rewritten: list[str] = []
        for spec in specs:
            name = str(spec.get("name") or "")
            if name not in drifted_names:
                continue
            text, changed = replace_codex_mcp_server_block(text, spec)
            if changed:
                rewritten.append(name)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.rstrip() + "\n")
        if missing_names:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("\n# Added by OpenClaw bridge maintenance repair: missing Codex MCP registrations.\n")
                for spec in specs:
                    if str(spec.get("name") or "") not in missing_names:
                        continue
                    handle.write(codex_mcp_toml_block(spec))
    except Exception as exc:
        return {"ok": False, "applied": False, "reason": f"write_failed: {exc}", "backup": str(backup)}
    return {
        "ok": True,
        "applied": True,
        "added": [str(spec.get("name") or "") for spec in specs if str(spec.get("name") or "") in missing_names],
        "rewritten": rewritten,
        "backup": str(backup),
        "restart_required": True,
        "note": "Restart Codex Desktop before expecting these MCP tools in the current session.",
    }


def set_toml_table_key(text: str, table: str, key: str, value: Any) -> tuple[str, bool]:
    lines = text.splitlines()
    if isinstance(value, bool):
        rendered_value = "true" if value else "false"
    else:
        rendered_value = toml_string(value)
    assignment = f"{key} = {rendered_value}"
    start = None
    for index, line in enumerate(lines):
        if line.strip() == f"[{table}]":
            start = index
            break
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{table}]")
        lines.append(assignment)
        return "\n".join(lines) + "\n", True
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    for index in range(start + 1, end):
        stripped = lines[index].strip()
        if stripped.startswith(f"{key} ") or stripped.startswith(f"{key}="):
            if stripped == assignment:
                return text, False
            lines[index] = assignment
            return "\n".join(lines) + "\n", True
    lines.insert(end, assignment)
    return "\n".join(lines) + "\n", True


def repair_codex_plugin_enablement(config: dict[str, Any], health: dict[str, Any], apply: bool) -> dict[str, Any]:
    path = Path(str(health.get("config_path") or codex_config_path(config)))
    missing_plugins = [str(item) for item in (health.get("missing_enabled_plugins") or []) if str(item).strip()]
    if not missing_plugins:
        return {
            "ok": True,
            "applied": False,
            "skipped": True,
            "reason": "no missing plugin enablement entries",
        }
    if not bool(health.get("config_parse_ok")):
        return {
            "ok": False,
            "applied": False,
            "reason": "codex config parse failed; plugin enablement repair is unsafe until config is parseable",
        }
    if not apply:
        return {
            "ok": True,
            "applied": False,
            "would_enable": missing_plugins,
            "restart_required": True,
            "policy": "additive_only",
            "reason": "dry-run; pass --apply to restore missing plugin enablement entries only",
        }
    backup = ""
    try:
        backup_result = create_routed_backup(
            [str(path)],
            category="bridge",
            purpose="maintenance-plugin-enable",
            trigger="mobile-maintenance-repair",
            remark="maintenance-plugin-enable",
        )
        if not backup_result.get("ok"):
            return {
                "ok": False,
                "applied": False,
                "reason": f"backup_failed: {backup_result.get('reason') or 'unknown'}",
                "backup": "",
            }
        backup_items = backup_result.get("items") or []
        backup = str(backup_items[0].get("backup_path") or "") if backup_items else ""
        text = path.read_text(encoding="utf-8-sig")
        changed_plugins: list[str] = []
        for plugin in missing_plugins:
            text, changed = set_toml_table_key(text, f'plugins."{plugin}"', "enabled", True)
            if changed:
                changed_plugins.append(plugin)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.rstrip() + "\n")
    except Exception as exc:
        return {"ok": False, "applied": False, "reason": f"write_failed: {exc}", "backup": str(backup)}
    return {
        "ok": True,
        "applied": True,
        "enabled": changed_plugins,
        "backup": str(backup),
        "restart_required": True,
        "policy": "additive_only",
        "note": "Only missing plugin enablement entries were restored; existing extra plugin config was preserved.",
    }


def repair_codex_config_guard(apply: bool) -> dict[str, Any]:
    if not apply:
        plan = codex_config_guard_module.repair_plan()
        return {
            "ok": bool(plan.get("ok")),
            "applied": False,
            "would_apply": bool(plan.get("would_apply")),
            "restart_required": bool((plan.get("repair") or {}).get("needs_codex_restart")),
            "policy": "merge_only_baseline_guard",
            "reason": "dry-run; pass --apply to run Codex config guard repair",
            "plan": plan,
        }
    return codex_config_guard_module.run_once(apply=True)


def codex_app_server_listener_processes(config: dict[str, Any]) -> dict[str, Any]:
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    host = str(trigger.get("codex_app_server_host") or "127.0.0.1")
    port = int(trigger.get("codex_app_server_port") or 18791)
    listen = f"ws://{host}:{port}"
    ps_listen = listen.replace("'", "''")
    script = f"""
$listen = '{ps_listen}'
$all = Get-CimInstance Win32_Process
$targets = @($all | Where-Object {{
  $_.Name -ieq 'codex.exe' -and
  $_.CommandLine -match 'app-server' -and
  $_.CommandLine -like "*$listen*"
}})
$items = foreach ($target in $targets) {{
  $children = @($all | Where-Object {{ $_.ParentProcessId -eq $target.ProcessId }})
  [pscustomobject]@{{
    process_id = [int]$target.ProcessId
    parent_process_id = [int]$target.ParentProcessId
    name = [string]$target.Name
    creation_date = [string]$target.CreationDate
    command_line_preview = ([string]$target.CommandLine).Substring(0, [Math]::Min(220, ([string]$target.CommandLine).Length))
    child_count = $children.Count
    mobile_mcp_child_count = @($children | Where-Object {{ $_.CommandLine -match 'mobile_bridge_mcp_server\\.py' }}).Count
    child_summary = @($children | Select-Object -First 12 ProcessId,Name)
  }}
}}
[pscustomobject]@{{
  ok = $true
  listen = $listen
  count = @($items).Count
  items = @($items)
}} | ConvertTo-Json -Depth 7
"""
    return powershell_json(script, timeout=15)


def app_server_mcp_baseline(config: dict[str, Any], recent_events: dict[str, int]) -> dict[str, Any]:
    processes = codex_app_server_listener_processes(config)
    raw_items = processes.get("items")
    if isinstance(raw_items, dict):
        items = [raw_items]
    elif isinstance(raw_items, list):
        items = raw_items
    else:
        items = []
    mobile_children = sum(int(item.get("mobile_mcp_child_count") or 0) for item in items if isinstance(item, dict))
    transport_closed_events = sum(
        int(value or 0)
        for key, value in (recent_events or {}).items()
        if "mcp_transport_closed" in str(key)
    )
    count = int(processes.get("count") or 0)
    ok = bool(processes.get("ok")) and count == 1 and mobile_children > 0 and transport_closed_events == 0
    if not processes.get("ok"):
        layer = "process_probe_failed"
    elif count == 0:
        layer = "app_server_listener_missing"
    elif count > 1:
        layer = "multiple_app_server_listeners"
    elif mobile_children <= 0:
        layer = "mobile_mcp_child_missing"
    elif transport_closed_events > 0:
        layer = "recent_transport_closed"
    else:
        layer = "ok"
    return {
        "ok": ok,
        "layer": layer,
        "listener": processes,
        "mobile_mcp_child_count": mobile_children,
        "recent_transport_closed_events": transport_closed_events,
        "safe_boundary": "diagnostic only; restart only the bridge-owned app-server listener after concrete MCP transport failure",
    }


def extract_codex_package_version(command_line: str) -> str:
    match = re.search(r"OpenAI\.Codex_([^\\\s]+)_x64__", str(command_line or ""))
    return match.group(1) if match else ""


def codex_desktop_session_mcp_baseline(config: dict[str, Any]) -> dict[str, Any]:
    """Inspect the visible Codex Desktop session's MCP host process tree.

    Direct MCP smoke checks prove a server script can start. They do not prove
    the current Desktop session still has a live stdio transport. This probe
    checks the Desktop-owned app-server under the CDP-visible Codex process.
    """
    retired_names = frozenset(load_decommissioned_mcp())
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    resolved = resolve_codex_cdp_endpoint(config)
    port = int(resolved.get("port") or trigger.get("codex_cdp_port") or 9229)
    ps_port = str(port).replace("'", "''")
    retired_pattern = "|".join(
        re.escape(name).replace(r"\-", "[-_]")
        for name in sorted(retired_names)
        if str(name).strip()
    ) or r"(?!)"
    ps_retired_pattern = retired_pattern.replace("'", "''")
    script = f"""
$all = Get-CimInstance Win32_Process
$listenerPids = @()
try {{
  $listenerPids = @(Get-NetTCPConnection -LocalPort {ps_port} -State Listen -ErrorAction Stop | ForEach-Object {{ [int]$_.OwningProcess }})
}} catch {{
  $listenerPids = @()
}}
$desktops = @($all | Where-Object {{
  $_.Name -in @('ChatGPT.exe','Codex.exe') -and
  $_.CommandLine -notmatch '--type=' -and
  (
    $listenerPids -contains [int]$_.ProcessId -or
    $_.CommandLine -match '--remote-debugging-port={ps_port}'
  )
}} | Sort-Object CreationDate -Descending)
$desktop = $desktops | Select-Object -First 1
$bridgeServers = @($all | Where-Object {{
  $_.Name -ieq 'codex.exe' -and
  $_.CommandLine -match 'app-server' -and
  $_.CommandLine -match '--listen ws://127\\.0\\.0\\.1:18791'
}})
$items = @()
if ($desktop) {{
  $appServers = @($all | Where-Object {{
    $_.ParentProcessId -eq $desktop.ProcessId -and
    $_.Name -ieq 'codex.exe' -and
    $_.CommandLine -match 'app-server'
  }})
  foreach ($app in $appServers) {{
    $children = @($all | Where-Object {{ $_.ParentProcessId -eq $app.ProcessId }})
    $items += [pscustomobject]@{{
      process_id = [int]$app.ProcessId
      parent_process_id = [int]$app.ParentProcessId
      name = [string]$app.Name
      creation_date = [string]$app.CreationDate
      command_line = [string]$app.CommandLine
      child_count = [int]$children.Count
      mobile_mcp_child_count = @($children | Where-Object {{ $_.CommandLine -match 'mobile_bridge_mcp_server\\.py' }}).Count
      retired_member_process_count = @($children | Where-Object {{ $_.CommandLine -match '{ps_retired_pattern}' }}).Count
      node_repl_child_count = @($children | Where-Object {{ $_.Name -ieq 'node_repl.exe' }}).Count
      child_summary = @($children | Select-Object -First 20 ProcessId,Name,CommandLine)
    }}
  }}
}}
[pscustomobject]@{{
  ok = $true
  cdp_port = {ps_port}
  listener_pids = @($listenerPids)
  desktop_count = [int]$desktops.Count
  desktop = if ($desktop) {{ [pscustomobject]@{{
    process_id = [int]$desktop.ProcessId
    parent_process_id = [int]$desktop.ParentProcessId
    name = [string]$desktop.Name
    creation_date = [string]$desktop.CreationDate
    command_line = [string]$desktop.CommandLine
  }} }} else {{ $null }}
  desktop_app_server_count = [int]$items.Count
  desktop_app_servers = @($items)
  bridge_app_server_count = [int]$bridgeServers.Count
  bridge_app_servers = @($bridgeServers | Select-Object -First 4 ProcessId,ParentProcessId,Name,CreationDate,CommandLine)
}} | ConvertTo-Json -Depth 8
"""
    processes = powershell_json(script, timeout=15)
    raw_items = processes.get("desktop_app_servers")
    if isinstance(raw_items, dict):
        items = [raw_items]
    elif isinstance(raw_items, list):
        items = raw_items
    else:
        items = []
    desktop = processes.get("desktop") if isinstance(processes.get("desktop"), dict) else {}
    bridge_raw = processes.get("bridge_app_servers")
    if isinstance(bridge_raw, dict):
        bridge_items = [bridge_raw]
    elif isinstance(bridge_raw, list):
        bridge_items = bridge_raw
    else:
        bridge_items = []

    mobile_children = sum(int(item.get("mobile_mcp_child_count") or 0) for item in items if isinstance(item, dict))
    retired_member_process_count = sum(int(item.get("retired_member_process_count") or 0) for item in items if isinstance(item, dict))
    node_repl_children = sum(int(item.get("node_repl_child_count") or 0) for item in items if isinstance(item, dict))
    desktop_version = extract_codex_package_version(str(desktop.get("command_line") or ""))
    app_versions = sorted(
        {
            extract_codex_package_version(str(item.get("command_line") or ""))
            for item in items
            if isinstance(item, dict) and extract_codex_package_version(str(item.get("command_line") or ""))
        }
    )
    bridge_versions = sorted(
        {
            extract_codex_package_version(str(item.get("CommandLine") or item.get("command_line") or ""))
            for item in bridge_items
            if isinstance(item, dict)
            and extract_codex_package_version(str(item.get("CommandLine") or item.get("command_line") or ""))
        }
    )
    desktop_count = int(processes.get("desktop_count") or 0)
    app_count = int(processes.get("desktop_app_server_count") or 0)
    bridge_count = int(processes.get("bridge_app_server_count") or 0)
    has_any_non_node_mcp = bool(mobile_children)
    version_split = bool(desktop_version and bridge_versions and desktop_version not in bridge_versions)
    ok = bool(processes.get("ok")) and desktop_count == 1 and app_count >= 1 and has_any_non_node_mcp and not version_split and retired_member_process_count == 0
    if not processes.get("ok"):
        layer = "process_probe_failed"
    elif desktop_count == 0:
        layer = "desktop_cdp_process_missing"
    elif desktop_count > 1:
        layer = "multiple_desktop_cdp_processes"
    elif app_count == 0:
        layer = "desktop_app_server_missing"
    elif not has_any_non_node_mcp:
        layer = "desktop_mcp_children_missing"
    elif retired_member_process_count:
        layer = "retired_member_process_present"
    elif version_split:
        layer = "desktop_bridge_codex_version_split"
    else:
        layer = "ok"
    return {
        "ok": ok,
        "layer": layer,
        "processes": processes,
        "desktop_version": desktop_version,
        "desktop_app_server_versions": app_versions,
        "bridge_app_server_versions": bridge_versions,
        "version_split": version_split,
        "desktop_app_server_count": app_count,
        "bridge_app_server_count": bridge_count,
        "mobile_mcp_child_count": mobile_children,
        "retired_member_process_count": retired_member_process_count,
        "retired_members": sorted(retired_names),
        "node_repl_child_count": node_repl_children,
        "current_session_transport_risk": layer in {"desktop_mcp_children_missing", "desktop_bridge_codex_version_split", "retired_member_process_present"},
        "safe_boundary": "diagnostic only; current-session MCP transport is owned by Codex Desktop and requires MCP session reload or controlled Desktop restart",
    }


def gui_automation_health(config: dict[str, Any], codex_mcp_config: dict[str, Any]) -> dict[str, Any]:
    parsed = read_codex_config(codex_config_path(config))
    servers = parsed.get("servers") if isinstance(parsed.get("servers"), dict) else {}
    server = servers.get("gui-automation") if isinstance(servers.get("gui-automation"), dict) else {}
    command = str(server.get("command") or "").strip()
    args = server.get("args") if isinstance(server.get("args"), list) else []
    env = server.get("env") if isinstance(server.get("env"), dict) else {}
    runtime_command = infer_gui_automation_python_command(parsed)
    runtime = python_module_health(runtime_command, GUI_REQUIRED_MODULES, timeout=GUI_MODULE_STATUS_TIMEOUT_SECONDS)
    script = Path(str(args[0] if args else PROJECT_ROOT / "_bridge" / "gui_automation_mcp.py"))
    ocr_python = Path(
        str(
            env.get("GUI_OCR_PYTHON")
            or PROJECT_ROOT / "_bridge" / "venvs" / "ocr-py312" / "Scripts" / "python.exe"
        )
    )
    ocr_fallback_python = Path(
        str(
            env.get("GUI_OCR_FALLBACK_PYTHON")
            or PROJECT_ROOT / "_bridge" / "venvs" / "ocr-py312" / "Scripts" / "python.exe"
        )
    )
    ocr_device = str(env.get("GUI_OCR_DEVICE") or "").strip()
    ocr_runner = PROJECT_ROOT / "_bridge" / "gui_ocr_paddle_runner.py"
    ocr: dict[str, Any] = {
        "ready": False,
        "python": str(ocr_python),
        "fallback_python": str(ocr_fallback_python),
        "runner": str(ocr_runner),
        "requested_device": ocr_device or "default",
        "fallback_enabled": ocr_fallback_python != ocr_python,
    }
    if not ocr_python.exists():
        ocr["error"] = "ocr_python_missing"
    elif not ocr_runner.exists():
        ocr["error"] = "ocr_runner_missing"
    else:
        try:
            proc = run_capture_tree_timeout(
                [str(ocr_python), str(ocr_runner), "--status"],
                cwd=PROJECT_ROOT,
                timeout=GUI_OCR_STATUS_TIMEOUT_SECONDS,
            )
            try:
                payload = json.loads(str(proc.get("stdout") or "").strip().splitlines()[0])
            except Exception:
                payload = {"ready": False, "error": "non_json_ocr_status", "stdout_tail": str(proc.get("stdout") or "")[-500:]}
            if isinstance(payload, dict):
                ocr.update(payload)
            ocr["returncode"] = proc.get("returncode")
            ocr["elapsed_ms"] = int(proc.get("elapsed_ms") or 0)
            if proc.get("timed_out"):
                ocr["error"] = f"ocr_status_timeout_{GUI_OCR_STATUS_TIMEOUT_SECONDS}s"
                ocr["timed_out"] = True
            if proc.get("stderr") and ocr.get("ready") is not True:
                ocr["stderr_tail"] = str(proc.get("stderr") or "")[-800:]
        except Exception as exc:
            ocr["error"] = str(exc)
            ocr["exception_type"] = type(exc).__name__
    fallback_ocr: dict[str, Any] = {"ready": False, "python": str(ocr_fallback_python), "skipped": True}
    if ocr_fallback_python != ocr_python:
        fallback_ocr["skipped"] = False
        if not ocr_fallback_python.exists():
            fallback_ocr["error"] = "fallback_ocr_python_missing"
        elif not ocr_runner.exists():
            fallback_ocr["error"] = "ocr_runner_missing"
        else:
            try:
                proc = run_capture_tree_timeout(
                    [str(ocr_fallback_python), str(ocr_runner), "--status"],
                    cwd=PROJECT_ROOT,
                    timeout=GUI_OCR_STATUS_TIMEOUT_SECONDS,
                )
                try:
                    payload = json.loads(str(proc.get("stdout") or "").strip().splitlines()[0])
                except Exception:
                    payload = {"ready": False, "error": "non_json_ocr_status", "stdout_tail": str(proc.get("stdout") or "")[-500:]}
                if isinstance(payload, dict):
                    fallback_ocr.update(payload)
                fallback_ocr["returncode"] = proc.get("returncode")
                fallback_ocr["elapsed_ms"] = int(proc.get("elapsed_ms") or 0)
                if proc.get("timed_out"):
                    fallback_ocr["error"] = f"ocr_status_timeout_{GUI_OCR_STATUS_TIMEOUT_SECONDS}s"
                    fallback_ocr["timed_out"] = True
                if proc.get("stderr") and fallback_ocr.get("ready") is not True:
                    fallback_ocr["stderr_tail"] = str(proc.get("stderr") or "")[-800:]
            except Exception as exc:
                fallback_ocr["error"] = str(exc)
                fallback_ocr["exception_type"] = type(exc).__name__
    ocr["fallback"] = fallback_ocr
    if ocr_device.lower().startswith("gpu") and ocr.get("compiled_cuda") is not True:
        ocr["gpu_default_blocked"] = True
        ocr["gpu_default_block_reason"] = "primary OCR backend is not CUDA-enabled"
    fallback_required = ocr_fallback_python != ocr_python
    fallback_ok = bool(fallback_ocr.get("ready")) if fallback_required else True
    gpu_device_ok = not ocr_device.lower().startswith("gpu") or bool(ocr.get("compiled_cuda"))
    drifted_names = {str(item.get("name") or "") for item in (codex_mcp_config.get("drifted") or [])}
    return {
        "ok": bool(runtime.get("ok")) and bool(ocr.get("ready")) and fallback_ok and gpu_device_ok,
        "registered": "gui-automation" in set(str(name) for name in (codex_mcp_config.get("registered_servers") or [])),
        "script": {"path": str(script), "exists": script.exists()},
        "command": command,
        "runtime_command": runtime_command,
        "runtime": runtime,
        "ocr": ocr,
        "config_drifted": "gui-automation" in drifted_names,
        "restart_required_after_config_repair": bool(codex_mcp_config.get("restart_required_after_repair")),
    }


def app_server_mcp_actionability(snapshot: dict[str, Any], app_server_mcp: dict[str, Any]) -> dict[str, Any]:
    """Explain whether an app-server MCP baseline issue is a current fault."""
    if isinstance(app_server_mcp, dict) and app_server_mcp.get("skipped"):
        return {"actionable": False, "reason": "deep_probe_skipped"}
    if not isinstance(app_server_mcp, dict) or app_server_mcp.get("ok"):
        return {"actionable": False, "reason": "baseline_ok_or_missing"}
    layer = str(app_server_mcp.get("layer") or "")
    if layer != "mobile_mcp_child_missing":
        return {"actionable": True, "reason": layer or "non_idle_baseline_issue"}
    if int(app_server_mcp.get("recent_transport_closed_events") or 0) > 0:
        return {"actionable": True, "reason": "recent_transport_closed"}
    pending = snapshot.get("pending") if isinstance(snapshot.get("pending"), list) else []
    active = snapshot.get("active") if isinstance(snapshot.get("active"), list) else []
    supplement_waiting = [
        item
        for item in pending
        if (
            isinstance(item, dict)
            and item.get("pending_kind") == "supplement_waiting_mcp_ack"
            and str(item.get("delivery_mode") or "").lower() == "codex-app-server"
        )
    ]
    app_server_active = [
        item
        for item in active
        if isinstance(item, dict) and str(item.get("delivery_mode") or "").lower() == "codex-app-server"
    ]
    if supplement_waiting:
        return {
            "actionable": True,
            "reason": "app_server_supplement_waiting_for_mcp_ack",
            "task_ids": [item.get("id") for item in supplement_waiting[:8]],
        }
    if app_server_active:
        return {
            "actionable": True,
            "reason": "app_server_active_task_waiting",
            "task_ids": [item.get("id") for item in app_server_active[:8]],
        }
    return {"actionable": False, "reason": "idle_no_app_server_mcp_work"}


def app_server_mcp_issue_is_actionable(snapshot: dict[str, Any], app_server_mcp: dict[str, Any]) -> bool:
    """Avoid treating an idle app-server with no child MCP process as a live fault."""
    return bool(app_server_mcp_actionability(snapshot, app_server_mcp).get("actionable"))


def control_state(queue: MobileQueue) -> dict[str, Any]:
    pause_file = queue.pause_file()
    return {
        "paused": queue.is_paused(),
        "pause_file": str(pause_file),
        "pause_file_exists": pause_file.exists(),
        "stop_request": str(STOP_REQUEST),
        "stop_request_exists": STOP_REQUEST.exists(),
        "stop_request_age_seconds": age_seconds(
            datetime.fromtimestamp(STOP_REQUEST.stat().st_mtime, timezone.utc).isoformat()
        )
        if STOP_REQUEST.exists()
        else None,
    }


def reply_sending_runtime_key(task_id: str) -> str:
    return f"reply_sending:{task_id}"


def parse_reply_sending_lease(raw: Any) -> dict[str, str]:
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


def reply_sending_lease_expired(raw: Any, now: datetime | None = None) -> bool:
    entry = parse_reply_sending_lease(raw)
    if not entry:
        return True
    now = now or datetime.now(timezone.utc)
    expires_at = parse_iso(entry.get("expires_at"))
    if expires_at:
        return now >= expires_at
    started_at = parse_iso(entry.get("started_at"))
    if started_at:
        return now >= started_at + timedelta(seconds=300)
    return True


def make_issue(
    code: str,
    severity: str,
    summary: str,
    evidence: dict[str, Any] | None = None,
    safe_auto_fix: str = "",
    manual_action: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "summary": summary,
        "evidence": evidence or {},
        "safe_auto_fix": safe_auto_fix,
        "manual_action": manual_action,
    }


def iteration_advisories(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    decision = snapshot.get("iteration_decision_summary") if isinstance(snapshot.get("iteration_decision_summary"), dict) else {}
    if not decision:
        return []
    if not decision.get("ok"):
        return [
            {
                "code": "iteration_decision_unavailable",
                "severity": "info",
                "summary": "Iteration-layer decision summary is unavailable for this doctor run.",
                "details": {
                    "reason": decision.get("reason"),
                },
                "recommended_action": "Keep maintenance diagnosis focused on current bridge health; inspect iteration_layer_review separately if proposal guidance is needed.",
            }
        ]

    ready_for_review = decision.get("ready_for_manual_review") if isinstance(decision.get("ready_for_manual_review"), list) else []
    validation_first = decision.get("validation_first") if isinstance(decision.get("validation_first"), list) else []
    advisory = {
        "code": "iteration_decision_focus",
        "severity": "info",
        "summary": str(decision.get("summary_text") or "").strip() or "Iteration layer produced a review-ready decision summary.",
        "details": {
            "primary_batch_id": decision.get("primary_batch_id"),
            "primary_destination": decision.get("primary_destination"),
            "primary_boundary_cluster": decision.get("primary_boundary_cluster"),
            "primary_boundary": decision.get("primary_boundary"),
            "ready_for_manual_review": ready_for_review,
            "validation_first": validation_first,
        },
        "recommended_action": (
            "Use this as a read-only next-step guide only; review ready_for_manual_review clusters first, "
            "keep promotion proposal-only, and run validation before promoting validation-first clusters."
        ),
    }
    return [advisory]


def active_observation_buckets(
    active: list[dict[str, Any]],
    ports: dict[str, Any],
    db_path: Path | None = None,
    threshold_seconds: int = 300,
) -> dict[str, Any]:
    """Classify aged active tasks as observable work before calling them stuck."""
    observing: list[dict[str, Any]] = []
    waiting_followup: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []

    def latest_active_poll_observation(task_id: str) -> dict[str, Any]:
        if not db_path or not task_id:
            return {}
        try:
            with sqlite3.connect(db_path, timeout=5) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    """
                    SELECT payload_json, created_at
                    FROM mobile_events
                    WHERE task_id=? AND event_type='active_poll_observation'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
        except Exception:
            return {}
        if not row:
            return {}
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return {
            "observed_at": str(row["created_at"] or ""),
            "stage": str(payload.get("stage") or ""),
            "status": str(payload.get("status") or ""),
            "waited_seconds": payload.get("waited_seconds"),
            "ack_seen": bool(payload.get("ack_seen")),
            "has_text": bool(payload.get("has_text")),
            "result_complete": bool(payload.get("result_complete")),
            "terminal_without_text": bool(payload.get("terminal_without_text")),
            "retryable_failure": bool(payload.get("retryable_failure")),
            "in_progress_tool_count": int(payload.get("in_progress_tool_count") or 0),
            "in_progress_tools": payload.get("in_progress_tools") if isinstance(payload.get("in_progress_tools"), list) else [],
        }

    def task_waiting_followup_redelivery(task_id: str) -> bool:
        if not db_path or not task_id:
            return False
        try:
            with sqlite3.connect(db_path, timeout=5) as db:
                wait_row = db.execute(
                    """
                    SELECT MAX(id)
                    FROM mobile_events
                    WHERE task_id=? AND event_type='active_waiting_followup_redelivery'
                    """,
                    (task_id,),
                ).fetchone()
                clear_row = db.execute(
                    """
                    SELECT MAX(id)
                    FROM mobile_events
                    WHERE task_id=? AND event_type IN (
                        'active_waiting_followup_redelivery_triggered',
                        'active_waiting_followup_redelivery_cleared',
                        'active_slot_released_to_pending',
                        'queued_for_codex',
                        'sent_to_codex',
                        'recovery_result_pushed',
                        'push_result'
                    )
                    """,
                    (task_id,),
                ).fetchone()
        except Exception:
            return False
        wait_id = int(wait_row[0] or 0) if wait_row else 0
        clear_id = int(clear_row[0] or 0) if clear_row else 0
        return wait_id > clear_id

    for item in active:
        status = str(item.get("status") or "")
        if status not in {"sent_to_codex", "processing"}:
            continue
        age = int(item.get("age_seconds") or 0)
        if age < threshold_seconds:
            continue
        mode = str(item.get("delivery_mode") or "")
        if mode == "codex-app-server":
            channel = ports.get("codex_app_server") if isinstance(ports.get("codex_app_server"), dict) else {}
        elif mode == "codex-cdp":
            channel = ports.get("codex_cdp") if isinstance(ports.get("codex_cdp"), dict) else {}
        else:
            channel = {}
        entry = {
            "id": item.get("id"),
            "status": status,
            "account": item.get("account"),
            "delivery_mode": mode,
            "thread_id": item.get("codex_thread_id"),
            "age_seconds": age,
            "route_key": item.get("route_key"),
            "channel_ok": bool(channel.get("ok")),
            "channel_reason": channel.get("reason") or "",
        }
        progress = latest_active_poll_observation(str(item.get("id") or ""))
        if progress:
            entry["progress"] = progress
        if task_waiting_followup_redelivery(str(item.get("id") or "")):
            entry["classification"] = "waiting_same_thread_followup_redelivery"
            waiting_followup.append(entry)
            continue
        if channel.get("ok"):
            entry["classification"] = "observing_active_codex_work"
            observing.append(entry)
        elif mode in {"codex-app-server", "codex-cdp"}:
            entry["classification"] = "observation_blocked_by_route_health"
            blocked.append(entry)
        else:
            entry["classification"] = "observation_unknown_route"
            unknown.append(entry)
    return {
        "threshold_seconds": threshold_seconds,
        "observing": observing,
        "waiting_followup": waiting_followup,
        "blocked": blocked,
        "unknown": unknown,
        "progress_stage_counts": progress_stage_counts(observing + waiting_followup + blocked + unknown),
    }


def progress_stage_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
        stage = str(progress.get("stage") or "no_poll_observation")
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def default_maintenance_policy() -> dict[str, Any]:
    return {
        "auto_fixes_without_extra_flags": [
            "start worker scheduled task when worker is absent",
            "start OpenClaw Gateway scheduled task when gateway port is down",
            "recover expired reply_sending leases back to reply_pending",
            "remove stale dashboard live-state temp files",
            "restore missing or drifted expected Codex MCP config entries with a marked backup",
            "restore missing plugin enablement entries with a marked backup and no rollback of newer plugin config",
            "sync persisted OpenClaw accounts into missing dedicated Codex thread routes",
        ],
        "requires_explicit_flag": [
            "sending or retrying reply_pending messages to Weixin",
        ],
        "manual_only": [
            "delete tasks",
            "mark active tasks failed or cancelled",
            "move sent_to_codex tasks back to pending",
            "change account-slot bindings",
            "change Codex model/provider or non-catalog MCP baseline",
            "switch primary delivery route between CDP and app-server",
        ],
    }


def iteration_decision_summary() -> dict[str, Any]:
    if not ITERATION_LAYER_REVIEW.exists():
        return {"ok": False, "reason": "iteration_layer_review_missing"}
    try:
        proc = subprocess.run(
            [sys.executable, str(ITERATION_LAYER_REVIEW), "--json", "--recent-limit", "3"],
            cwd=str(PROJECT_ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
    except Exception as exc:
        return {"ok": False, "reason": f"iteration_layer_review_exec_failed: {exc}"}
    if proc.returncode != 0:
        return {
            "ok": False,
            "reason": "iteration_layer_review_nonzero",
            "returncode": proc.returncode,
            "stderr": short(proc.stderr, 400),
        }
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return {"ok": False, "reason": f"iteration_layer_review_json_invalid: {exc}"}
    summary = payload.get("decision_summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        return {"ok": False, "reason": "decision_summary_missing"}
    return {"ok": True, **summary}


def controlled_iteration_gate_report(
    *,
    recent_limit: int = 12,
    run_validation: bool = True,
    validation_profile: str = "quick",
) -> dict[str, Any]:
    """Run the controlled iteration review as a read-only finalization gate."""
    command = [
        sys.executable,
        str(ITERATION_LAYER_REVIEW),
        "--json",
        "--recent-limit",
        str(recent_limit),
    ]
    if run_validation:
        command.extend(["--run-validation", "--validation-profile", validation_profile])
    contract = {
        "purpose": "show controlled iteration proposals before finalizing broad system-level work",
        "system_level_scopes": [
            "bridge",
            "maintenance",
            "resource",
            "GUI",
            "configuration",
            "automation",
            "agent-interaction",
        ],
        "must_display_to_user_after_system_level_change": True,
        "proposal_only": True,
        "not_permission_to_modify": True,
        "must_backup_before_any_approved_persistent_update": True,
    }
    if not ITERATION_LAYER_REVIEW.exists():
        return {
            "schema": "mobile-weixin-bridge-controlled-iteration-gate/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "reason": "iteration_layer_review_missing",
            "command": command,
            "contract": contract,
        }
    try:
        proc = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
        )
    except Exception as exc:
        return {
            "schema": "mobile-weixin-bridge-controlled-iteration-gate/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "reason": f"iteration_layer_review_exec_failed: {exc}",
            "command": command,
            "contract": contract,
        }
    if proc.returncode != 0:
        return {
            "schema": "mobile-weixin-bridge-controlled-iteration-gate/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "reason": "iteration_layer_review_nonzero",
            "returncode": proc.returncode,
            "stderr": short(proc.stderr, 1200),
            "command": command,
            "contract": contract,
        }
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return {
            "schema": "mobile-weixin-bridge-controlled-iteration-gate/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "reason": f"iteration_layer_review_json_invalid: {exc}",
            "stdout": short(proc.stdout, 1200),
            "command": command,
            "contract": contract,
        }
    if not isinstance(payload, dict):
        return {
            "schema": "mobile-weixin-bridge-controlled-iteration-gate/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "reason": "iteration_layer_review_payload_not_object",
            "command": command,
            "contract": contract,
        }

    safety = payload.get("safety") if isinstance(payload.get("safety"), dict) else {}
    approval = payload.get("approval_block") if isinstance(payload.get("approval_block"), dict) else {}
    violations: list[str] = []
    if safety.get("writes_files") is not False:
        violations.append("iteration_review_must_be_read_only")
    if safety.get("requires_user_confirmation_for_updates") is not True:
        violations.append("iteration_review_must_require_user_confirmation")
    if approval.get("approved_by_default") is not False:
        violations.append("iteration_review_must_not_auto_approve")
    if run_validation and payload.get("validation_executed") is not True:
        violations.append("iteration_review_validation_not_executed")
    if run_validation and payload.get("validation_passed") is not True:
        violations.append("iteration_review_validation_not_passed")

    proposal_groups = payload.get("proposal_groups") if isinstance(payload.get("proposal_groups"), list) else []
    recommended_next_actions = (
        payload.get("recommended_next_actions")
        if isinstance(payload.get("recommended_next_actions"), list)
        else []
    )
    return {
        "schema": "mobile-weixin-bridge-controlled-iteration-gate/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": not violations,
        "reason": "ok" if not violations else "contract_violation",
        "violations": violations,
        "command": command,
        "contract": contract,
        "decision_summary": payload.get("decision_summary") if isinstance(payload.get("decision_summary"), dict) else {},
        "proposal_count": len(proposal_groups),
        "proposal_groups": proposal_groups,
        "recommended_next_actions": recommended_next_actions,
        "approval_block": approval,
        "safety": safety,
        "validation_executed": payload.get("validation_executed"),
        "validation_passed": payload.get("validation_passed"),
        "validation_profile": payload.get("validation_profile"),
        "blocked_without_approval": approval.get("blocked_without_approval", []),
        "closeout_display": iteration_closeout_display(
            proposal_groups=proposal_groups,
            recommended_next_actions=recommended_next_actions,
            approval_block=approval,
            validation_passed=payload.get("validation_passed"),
        ),
    }


def iteration_closeout_display(
    *,
    proposal_groups: list[Any],
    recommended_next_actions: list[Any],
    approval_block: dict[str, Any],
    validation_passed: Any,
) -> dict[str, Any]:
    """Build the exact human-facing closeout items that must be shown."""
    groups: list[str] = []
    for group in proposal_groups[:6]:
        if not isinstance(group, dict):
            continue
        priority = str(group.get("priority") or "P?").strip()
        name = str(group.get("name") or "").strip()
        description = str(group.get("description") or "").strip()
        if name or description:
            groups.append(f"{priority}: {name} - {description}".strip(" -"))

    actions: list[str] = []
    for action in recommended_next_actions[:6]:
        if not isinstance(action, dict):
            continue
        priority = str(action.get("priority") or "P?").strip()
        text = str(action.get("action") or "").strip()
        validation = str(action.get("validation") or "").strip()
        if text:
            suffix = f" 验证: {validation}" if validation else ""
            actions.append(f"{priority}: {text}{suffix}")

    blocked = [
        str(item).strip()
        for item in (approval_block.get("blocked_without_approval") or [])
        if str(item).strip()
    ]
    status = str(approval_block.get("status") or "").strip()
    user_decision_required = bool(approval_block.get("user_decision_required"))
    return {
        "schema": "iteration-closeout-display/v1",
        "must_display_to_user": bool(user_decision_required or groups or actions or blocked),
        "validation_passed": bool(validation_passed),
        "status": status,
        "proposal_groups": groups,
        "recommended_next_actions": actions,
        "blocked_without_approval": blocked,
        "final_reply_rule": "If must_display_to_user is true, include these items in the final user reply; do not collapse them into 'iteration passed'.",
    }


def memory_governance_summary(command_name: str = "doctor") -> dict[str, Any]:
    command = [sys.executable, str(MEMORY_GOVERNANCE), command_name]
    if not MEMORY_GOVERNANCE.exists():
        return {
            "schema": "mobile-weixin-bridge-memory-governance-summary/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "reason": "memory_governance_missing",
            "command": command,
        }
    try:
        proc = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=60,
        )
    except Exception as exc:
        return {
            "schema": "mobile-weixin-bridge-memory-governance-summary/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "reason": f"memory_governance_exec_failed: {exc}",
            "command": command,
        }
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return {
            "schema": "mobile-weixin-bridge-memory-governance-summary/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "reason": f"memory_governance_json_invalid: {exc}",
            "returncode": proc.returncode,
            "stdout": short(proc.stdout, 1200),
            "stderr": short(proc.stderr, 1200),
            "command": command,
        }
    if isinstance(payload, dict):
        payload.setdefault("command", command)
        return payload
    return {
        "schema": "mobile-weixin-bridge-memory-governance-summary/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": False,
        "reason": "memory_governance_unexpected_payload",
        "command": command,
    }


def inspect_system(queue: MobileQueue, config: dict[str, Any], deep_probes: bool = True) -> dict[str, Any]:
    db_path = Path(config.get("queue", {}).get("db_path") or ROOT / "mobile_openclaw_bridge.db")
    worker_task = str(config.get("control", {}).get("scheduled_task_name") or DEFAULT_WORKER_TASK)
    gateway_task = str(config.get("openclaw", {}).get("gateway_scheduled_task_name") or DEFAULT_GATEWAY_TASK)
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    openclaw = config.get("openclaw", {}) if isinstance(config.get("openclaw"), dict) else {}
    database = sqlite_health(db_path)
    db_read_error = ""
    try:
        with connect_readonly(db_path) as db:
            counts = table_counts(db)
            active = active_rows(db, config)
            supplement_index = bridge_supplement_index(db)
            pending = pending_rows(db, config, supplement_index=supplement_index)
            reply_problems = reply_problem_rows(db)
            session_timeout_misclassified = session_timeout_misclassified_rows(db)
            control_reply_receipts = control_reply_receipt_health(db)
            recent_events = recent_event_summary(db)
            event_noise = event_noise_health(db, config)
            event_archive = event_archive_dry_run(db)
            app_server_materialization_lag = app_server_materialization_lag_rows(db)
            cdp_visible_unconfirmed_observing = cdp_visible_unconfirmed_observing_rows(db)
    except Exception as exc:
        counts = {"by_status": {}, "by_account_status": {}}
        active = []
        pending = []
        reply_problems = []
        session_timeout_misclassified = []
        control_reply_receipts = {"ok": False, "reason": str(exc)}
        recent_events = {}
        event_noise = {"ok": False, "reason": str(exc)}
        event_archive = {"ok": False, "dry_run": True, "reason": str(exc)}
        app_server_materialization_lag = []
        cdp_visible_unconfirmed_observing = []
        db_read_error = str(exc)
        database["ok"] = False
        database["read_error"] = db_read_error
    routes = route_summary(active, pending)
    accounts = account_summary(counts, active, pending, reply_problems, config)
    worker = inspect_worker_processes(PROJECT_ROOT)
    gateway = inspect_openclaw_gateway_processes(PROJECT_ROOT)
    deep_probe_allowlist = parse_deep_probe_allowlist(trigger.get("maintenance_deep_probe_allowlist"))
    probe_policy = DeepProbePolicy(deep_probes=deep_probes, allowlist=deep_probe_allowlist)

    if probe_policy.enabled("worker_task"):
        worker_task_state = inspect_scheduled_task(PROJECT_ROOT, worker_task)
    else:
        worker_task_state = dict(QUICK_PROBE_SKIPPED)
    if probe_policy.enabled("gateway_task"):
        gateway_task_state = inspect_scheduled_task(PROJECT_ROOT, gateway_task)
    else:
        gateway_task_state = dict(QUICK_PROBE_SKIPPED)
    cdp_endpoint = resolve_codex_cdp_endpoint(config)
    cdp_port = int(cdp_endpoint.get("port") or trigger.get("codex_cdp_port") or 9229)
    cdp_host = str(cdp_endpoint.get("host") or trigger.get("codex_cdp_host") or "localhost")
    app_port = int(trigger.get("codex_app_server_port") or 18791)
    gateway_port = int(openclaw.get("port") or 18789)
    tcp_timeout = 1.5 if deep_probes else 0.25
    ports = {
        "openclaw_gateway": tcp_check(gateway_port, timeout=tcp_timeout),
        "codex_app_server": tcp_check(app_port, host=str(trigger.get("codex_app_server_host") or "127.0.0.1"), timeout=tcp_timeout),
        "codex_cdp": tcp_check(cdp_port, host=cdp_host, timeout=tcp_timeout),
    }
    active_observation = active_observation_buckets(active, ports, db_path=db_path)
    probe_timings: list[dict[str, Any]] = []
    if deep_probes:
        if probe_policy.enabled("cdp_os_port"):
            cdp_os_port, timing = timed_probe("cdp_os_port", lambda: os_port_listener_state(cdp_port))
        else:
            cdp_os_port, timing = probe_policy.skipped("cdp_os_port")
        probe_timings.append(timing)
        if probe_policy.enabled("cdp_route"):
            cdp_route, timing = timed_probe("cdp_route", lambda: cdp_route_diagnostics(config, ports, pending, recent_events, cdp_os_port))
        else:
            cdp_route, timing = probe_policy.skipped("cdp_route", {"port": cdp_port, "host": cdp_host})
        probe_timings.append(timing)
        if probe_policy.enabled("mobile_mcp"):
            mobile_mcp, timing = timed_probe("mobile_mcp", lambda: mobile_mcp_direct_smoke(config))
        else:
            mobile_mcp, timing = probe_policy.skipped("mobile_mcp")
        probe_timings.append(timing)
        if probe_policy.enabled("mobile_mcp_fallback"):
            mobile_mcp_fallback, timing = timed_probe("mobile_mcp_fallback", lambda: mobile_mcp_local_fallback_health(config))
        else:
            mobile_mcp_fallback, timing = probe_policy.skipped("mobile_mcp_fallback")
        probe_timings.append(timing)
        if probe_policy.enabled("codex_mcp_config"):
            codex_mcp_config, timing = timed_probe("codex_mcp_config", lambda: codex_mcp_config_health(config))
        else:
            codex_mcp_config, timing = probe_policy.skipped("codex_mcp_config", {"path": str(codex_config_path(config))})
        probe_timings.append(timing)
        if probe_policy.enabled("codex_plugins"):
            codex_plugins, timing = timed_probe("codex_plugins", lambda: codex_plugin_config_health(run_cli=False))
        else:
            codex_plugins, timing = probe_policy.skipped("codex_plugins", {"config_path": str(codex_config_path(config))})
        probe_timings.append(timing)
        if probe_policy.enabled("codex_config_guard"):
            codex_config_guard, timing = timed_probe("codex_config_guard", lambda: codex_config_guard_module.doctor())
        else:
            codex_config_guard, timing = probe_policy.skipped("codex_config_guard")
        probe_timings.append(timing)
        if probe_policy.enabled("desktop_session_mcp"):
            desktop_session_mcp, timing = timed_probe("desktop_session_mcp", lambda: codex_desktop_session_mcp_baseline(config))
        else:
            desktop_session_mcp, timing = probe_policy.skipped("desktop_session_mcp")
        probe_timings.append(timing)
        if probe_policy.enabled("app_server_mcp"):
            app_server_mcp, timing = timed_probe("app_server_mcp", lambda: app_server_mcp_baseline(config, recent_events))
        else:
            app_server_mcp, timing = probe_policy.skipped("app_server_mcp")
        probe_timings.append(timing)
        if probe_policy.enabled("gui_automation"):
            gui_automation, timing = timed_probe("gui_automation", lambda: gui_automation_health(config, codex_mcp_config))
        else:
            gui_automation, timing = probe_policy.skipped("gui_automation")
        probe_timings.append(timing)
        if probe_policy.enabled("memory_governance"):
            memory_governance, timing = timed_probe("memory_governance", lambda: memory_governance_summary("doctor"))
        else:
            memory_governance, timing = probe_policy.skipped("memory_governance")
        probe_timings.append(timing)

        def load_thread_routes_ui() -> dict[str, Any]:
            from mobile_openclaw_cli import thread_routes_ui_health as thread_routes_ui_health_impl

            result = thread_routes_ui_health_impl(config, limit=THREAD_ROUTES_UI_SUMMARY_LIMIT)
            try:
                total = len(thread_items(config))
            except Exception:
                total = 0
            result["sampled"] = True
            result["sample_limit"] = THREAD_ROUTES_UI_SUMMARY_LIMIT
            result["total_configured_threads"] = total
            result["summary_scope"] = "sampled_for_maintenance_summary"
            return result

        if probe_policy.enabled("thread_routes_ui"):
            thread_routes_ui, timing = timed_probe("thread_routes_ui", load_thread_routes_ui)
        else:
            thread_routes_ui, timing = probe_policy.skipped("thread_routes_ui")
        probe_timings.append(timing)
    else:
        cdp_route = {**DEEP_PROBE_SKIPPED, "layer": "skipped", "port": cdp_port, "host": cdp_host}
        mobile_mcp = dict(DEEP_PROBE_SKIPPED)
        mobile_mcp_fallback = dict(DEEP_PROBE_SKIPPED)
        codex_mcp_config = {**DEEP_PROBE_SKIPPED, "path": str(codex_config_path(config))}
        codex_plugins = {**DEEP_PROBE_SKIPPED, "config_path": str(codex_config_path(config))}
        codex_config_guard = {**DEEP_PROBE_SKIPPED, "layer": "skipped"}
        desktop_session_mcp = {**DEEP_PROBE_SKIPPED, "layer": "skipped"}
        app_server_mcp = {**DEEP_PROBE_SKIPPED, "layer": "skipped"}
        gui_automation = dict(DEEP_PROBE_SKIPPED)
        memory_governance = {**DEEP_PROBE_SKIPPED, "layer": "skipped"}
        thread_routes_ui = {**DEEP_PROBE_SKIPPED, "layer": "skipped"}
    if probe_policy.enabled("resource_processes") or (not deep_probes and not deep_probe_allowlist):
        resource_processes, timing = timed_probe("resource_processes", lambda: resource_process_health(deep_probes))
    else:
        resource_processes, timing = probe_policy.skipped("resource_processes")
    probe_timings.append(timing)
    app_server_mcp_action = app_server_mcp_actionability(
        {
            "pending": pending,
            "active": active,
        },
        app_server_mcp,
    )
    account_thread_drift = openclaw_account_thread_drift(config, queue)
    permission_account_map = {
        str(item.get("account_id") or ""): {
            "user_id": str(item.get("external_user") or ""),
            "token_present": "yes" if str(item.get("external_user") or "") else "no",
        }
        for item in (account_thread_drift.get("accounts") or [])
        if isinstance(item, dict) and str(item.get("account_id") or "")
    }
    permission_snapshot = permission_policy.snapshot(config, permission_account_map)
    capability_token_snapshot = capability_tokens.snapshot()
    decision = iteration_decision_summary()
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": default_maintenance_policy(),
        "db_path": str(db_path),
        "database": database,
        "db_read_error": db_read_error,
        "counts": counts,
        "active": active,
        "pending": pending,
        "reply_problems": reply_problems,
        "session_timeout_misclassified": session_timeout_misclassified,
        "control_reply_receipts": control_reply_receipts,
        "routes": routes,
        "accounts": accounts,
        "top_pending_routes": top_routes(routes, "pending_count"),
        "top_supplement_waiting_routes": top_routes(routes, "supplement_waiting_count"),
        "top_active_routes": top_routes(routes, "active_count"),
        "top_accounts": top_accounts(accounts),
        "recent_events": recent_events,
        "event_noise": event_noise,
        "event_archive_dry_run": event_archive,
        "app_server_materialization_lag": app_server_materialization_lag,
        "cdp_visible_unconfirmed_observing": cdp_visible_unconfirmed_observing,
        "control": control_state(queue),
        "processes": {
            "worker": worker,
            "gateway": gateway,
        },
        "scheduled_tasks": {
            "worker": worker_task_state,
            "gateway": gateway_task_state,
        },
        "ports": ports,
        "active_observation": active_observation,
        "cdp_route": cdp_route,
        "mobile_mcp": mobile_mcp,
        "mobile_mcp_fallback": mobile_mcp_fallback,
        "codex_mcp_config": codex_mcp_config,
        "codex_plugins": codex_plugins,
        "codex_config_guard": codex_config_guard,
        "desktop_session_mcp": desktop_session_mcp,
        "app_server_mcp": app_server_mcp,
        "app_server_mcp_actionability": app_server_mcp_action,
        "thread_routes_ui_health": thread_routes_ui,
        "thread_route_state_counts": dict(thread_routes_ui.get("state_counts") or {}) if isinstance(thread_routes_ui, dict) else {},
        "gui_automation": gui_automation,
        "memory_governance": memory_governance,
        "resource_processes": resource_processes,
        "probe_timings": probe_timings,
        "openclaw_account_thread_drift": account_thread_drift,
        "permission_policy": permission_snapshot,
        "capability_tokens": capability_token_snapshot,
        "iteration_decision_summary": decision,
        "deep_probes": bool(deep_probes),
        "latest_worker_stderr": latest_worker_stderr(ROOT),
        "dashboard_live_state": live_state_health(),
    }


def diagnose_system(snapshot: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    issues.extend(governance_storage_issues(snapshot, db_size_warn_bytes=DEFAULT_DB_SIZE_WARN_BYTES))
    issues.extend(bridge_runtime_route_issues(snapshot))
    issues.extend(codex_tooling_issues(snapshot, probe_evidence_state_fn=probe_evidence_state))
    issues.extend(
        app_server_mcp_issues(
            snapshot,
            app_server_mcp_issue_is_actionable_fn=app_server_mcp_issue_is_actionable,
        )
    )
    issues.extend(resource_memory_hygiene_issues(snapshot))
    issues.extend(queue_delivery_issues(snapshot))
    blocking_issues = [
        issue
        for issue in issues
        if issue.get("severity") in {"critical", "high"} and issue.get("owner_health_impact") is not False
    ]
    ok = not blocking_issues
    return {
        "ok": ok,
        "issue_count": len(issues),
        "blocking_issue_count": len(blocking_issues),
        "external_dependency_issue_count": sum(1 for issue in issues if issue.get("scope") == "external_dependency"),
        "issues": issues,
        "summary": summarize_issues(issues),
    }


def summarize_issues(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return "No maintenance issues detected by the current rule set."
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    ordered = sorted(issues, key=lambda item: severity_order.get(str(item.get("severity")), 9))
    lines = ["Maintenance findings:"]
    for item in ordered:
        lines.append(f"- [{item.get('severity')}] {item.get('code')}: {item.get('summary')}")
        if item.get("safe_auto_fix"):
            lines.append(f"  safe_auto_fix: {item.get('safe_auto_fix')}")
        if item.get("manual_action"):
            lines.append(f"  manual_action: {item.get('manual_action')}")
    return "\n".join(lines)


def layer_status(snapshot: dict[str, Any]) -> dict[str, str]:
    database = snapshot.get("database") if isinstance(snapshot.get("database"), dict) else {}
    ports = snapshot.get("ports") if isinstance(snapshot.get("ports"), dict) else {}
    processes = snapshot.get("processes") if isinstance(snapshot.get("processes"), dict) else {}
    scheduled = snapshot.get("scheduled_tasks") if isinstance(snapshot.get("scheduled_tasks"), dict) else {}
    live = snapshot.get("dashboard_live_state") if isinstance(snapshot.get("dashboard_live_state"), dict) else {}
    control = snapshot.get("control") if isinstance(snapshot.get("control"), dict) else {}
    mobile_mcp = snapshot.get("mobile_mcp") if isinstance(snapshot.get("mobile_mcp"), dict) else {}
    mobile_mcp_fallback = snapshot.get("mobile_mcp_fallback") if isinstance(snapshot.get("mobile_mcp_fallback"), dict) else {}
    desktop_session_mcp = snapshot.get("desktop_session_mcp") if isinstance(snapshot.get("desktop_session_mcp"), dict) else {}
    gui = snapshot.get("gui_automation") if isinstance(snapshot.get("gui_automation"), dict) else {}
    codex_config_guard = snapshot.get("codex_config_guard") if isinstance(snapshot.get("codex_config_guard"), dict) else {}
    resource_processes = snapshot.get("resource_processes") if isinstance(snapshot.get("resource_processes"), dict) else {}
    backup_hygiene = snapshot.get("backup_hygiene") if isinstance(snapshot.get("backup_hygiene"), dict) else {}
    memory_governance = snapshot.get("memory_governance") if isinstance(snapshot.get("memory_governance"), dict) else {}
    db_ok = bool(database.get("exists")) and str(database.get("integrity_check") or "").lower() in {"", "ok"}
    db_state = "ok" if db_ok else "bad"
    if db_ok and not database.get("under_limit"):
        db_state = "ok-size-high"

    worker_ok = bool(processes.get("worker", {}).get("ok")) and int(processes.get("worker", {}).get("count") or 0) > 0
    gateway_task_skipped = bool(scheduled.get("gateway", {}).get("skipped"))
    worker_task_skipped = bool(scheduled.get("worker", {}).get("skipped"))
    gateway_task_ok = bool(scheduled.get("gateway", {}).get("ok"))
    worker_task_ok = bool(scheduled.get("worker", {}).get("ok"))
    worker_task_state = str(scheduled.get("worker", {}).get("state") or "").strip().lower()
    worker_task_status = "skipped" if worker_task_skipped else ("ok" if worker_task_ok else "bad")
    if worker_task_ok and worker_task_state == "disabled":
        worker_task_status = "disabled"
    return {
        "control": "stopped" if control.get("stop_request_exists") else ("paused" if control.get("paused") else "running"),
        "gateway": "ok" if ports.get("openclaw_gateway", {}).get("ok") else "down",
        "gateway_task": "skipped" if gateway_task_skipped else ("ok" if gateway_task_ok else "bad"),
        "worker": "ok" if worker_ok else "down",
        "worker_task": worker_task_status,
        "codex_app_server": "ok" if ports.get("codex_app_server", {}).get("ok") else "down",
        "codex_cdp": "ok" if ports.get("codex_cdp", {}).get("ok") else "down",
        "mobile_mcp": "skipped" if mobile_mcp.get("skipped") else ("ok" if mobile_mcp.get("ok") else ("unknown" if not mobile_mcp else "bad")),
        "mobile_mcp_fallback": "skipped" if mobile_mcp_fallback.get("skipped") else (
            "ok" if mobile_mcp_fallback.get("ok") else ("unknown" if not mobile_mcp_fallback else "bad")
        ),
        "desktop_session_mcp": "skipped" if desktop_session_mcp.get("skipped") else (
            "ok" if desktop_session_mcp.get("ok") else (
                str(desktop_session_mcp.get("layer") or "unknown") if desktop_session_mcp else "unknown"
            )
        ),
        "codex_plugins": "skipped" if snapshot.get("codex_plugins", {}).get("skipped") else (
            "ok" if snapshot.get("codex_plugins", {}).get("ok") else ("unknown" if not snapshot.get("codex_plugins") else "bad")
        ),
        "codex_config_guard": "skipped" if codex_config_guard.get("skipped") else (
            "ok" if codex_config_guard.get("ok") else ("unknown" if not codex_config_guard else "bad")
        ),
        "gui_automation": "skipped" if gui.get("skipped") else ("ok" if gui.get("ok") else ("unknown" if not gui else "bad")),
        "resource_processes": "skipped" if resource_processes.get("skipped") else (
            str(resource_processes.get("layer") or "unknown") if resource_processes else "unknown"
        ),
        "backup_hygiene": "skipped" if backup_hygiene.get("skipped") else (
            str(backup_hygiene.get("layer") or "unknown") if backup_hygiene else "unknown"
        ),
        "memory_governance": "skipped" if memory_governance.get("skipped") else (
            str(memory_governance.get("status") or ("ok" if memory_governance.get("ok") else "bad"))
            if memory_governance else "unknown"
        ),
        "app_server_mcp": "skipped" if snapshot.get("app_server_mcp", {}).get("skipped") else (
            "ok" if snapshot.get("app_server_mcp", {}).get("ok") else (
                str(snapshot.get("app_server_mcp", {}).get("layer") or "unknown") if snapshot.get("app_server_mcp") else "unknown"
            )
        ),
        "database": db_state,
        "dashboard_live": "ok" if live.get("ok") else "stale",
    }


def observability_metrics(snapshot: dict[str, Any], diagnosis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return compact machine-readable bridge observability metrics."""
    diagnosis = diagnosis or diagnose_system(snapshot)
    return render_observability_metrics(
        snapshot,
        diagnosis,
        layer_status_fn=layer_status,
    )

def summary_report(queue: MobileQueue, config: dict[str, Any], deep: bool = False) -> str:
    snapshot = inspect_system(queue, config, deep_probes=deep)
    diagnosis = diagnose_system(snapshot)
    return render_summary_report(
        snapshot,
        diagnosis,
        active_statuses=ACTIVE_STATUSES,
        default_policy=default_maintenance_policy(),
        layer_status_fn=layer_status,
        probe_evidence_state_fn=probe_evidence_state,
    )


@dataclass
class RepairAction:
    code: str
    description: str
    safe: bool = True


def run_powershell(command: str, timeout: int = 20) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=str(PROJECT_ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": short(proc.stdout, 1200),
        "stderr": short(proc.stderr, 1200),
    }


def powershell_json(command: str, timeout: int = 20) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=str(PROJECT_ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    stdout = str(proc.stdout or "").strip()
    stderr = short(proc.stderr, 1200)
    result = {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": stdout, "stderr": stderr}
    if not stdout:
        return result
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return result
    if isinstance(parsed, dict):
        parsed.setdefault("ok", bool(result.get("ok")))
        parsed["_powershell_returncode"] = result.get("returncode")
        if stderr:
            parsed["_powershell_stderr"] = stderr
        return parsed
    return {"ok": bool(result.get("ok")), "result": parsed, "_powershell_returncode": result.get("returncode")}


def os_port_listener_state(port: int) -> dict[str, Any]:
    script = f"""
$rows = Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue
$items = foreach ($row in $rows) {{
  $pidValue = [int]$row.OwningProcess
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
  [pscustomobject]@{{
    local_address = [string]$row.LocalAddress
    local_port = [int]$row.LocalPort
    pid = $pidValue
    process_exists = [bool]$proc
    name = if ($proc) {{ [string]$proc.Name }} else {{ "" }}
    command_line = if ($proc) {{ [string]$proc.CommandLine }} else {{ "" }}
  }}
}}
[pscustomobject]@{{
  ok = $true
  port = {int(port)}
  listener_count = @($items).Count
  live_count = @($items | Where-Object {{ $_.process_exists }}).Count
  stale_count = @($items | Where-Object {{ -not $_.process_exists }}).Count
  listeners = @($items)
}} | ConvertTo-Json -Depth 5
"""
    return powershell_json(script, timeout=10)


def start_scheduled_task(task_name: str, apply: bool) -> dict[str, Any]:
    if not apply:
        return {"ok": True, "dry_run": True, "action": "start_scheduled_task", "task_name": task_name}
    escaped = task_name.replace("'", "''")
    return run_powershell(f"Start-ScheduledTask -TaskName '{escaped}'", timeout=20)


def cleanup_dashboard_tmp_files(apply: bool) -> dict[str, Any]:
    runtime = ROOT / "runtime"
    files = sorted(runtime.glob("dashboard_live_state.json.*.tmp")) if runtime.exists() else []
    if not apply:
        return {"ok": True, "dry_run": True, "action": "cleanup_dashboard_tmp_files", "count": len(files)}
    removed = []
    failed = []
    for path in files:
        try:
            path.unlink()
            removed.append(str(path))
        except Exception as exc:
            failed.append({"path": str(path), "reason": str(exc)})
    return {"ok": not failed, "removed": removed, "failed": failed}


def openclaw_account_thread_drift(config: dict[str, Any], queue: MobileQueue | None = None) -> dict[str, Any]:
    try:
        from mobile_openclaw_cli import openclaw_account_thread_drift as drift_impl

        return drift_impl(config, queue)
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "missing_count": 0, "missing_routes": [], "skipped": []}


def sync_openclaw_account_onboarding(queue: MobileQueue, config: dict[str, Any], apply: bool) -> dict[str, Any]:
    if not apply:
        drift = openclaw_account_thread_drift(config, queue)
        return {
            "ok": True,
            "dry_run": True,
            "action": "sync_openclaw_account_onboarding",
            "candidate_count": int(drift.get("missing_count") or 0),
            "missing_routes": drift.get("missing_routes", []),
            "skipped": drift.get("skipped", []),
        }
    from mobile_openclaw_cli import account_onboarding_sync

    return account_onboarding_sync(queue, config, apply=True)


def recover_reply_sending_leases(queue: MobileQueue, apply: bool) -> dict[str, Any]:
    if not apply:
        with queue.session() as db:
            rows = db.execute(
                """
                SELECT t.id, r.value AS lease
                FROM mobile_tasks t
                LEFT JOIN mobile_runtime r ON r.key = ('reply_sending:' || t.id)
                WHERE t.push_status='reply_sending'
                ORDER BY t.updated_at ASC
                LIMIT 50
                """
            ).fetchall()
        expired = []
        active = []
        for row in rows:
            task_id = str(row["id"] if isinstance(row, sqlite3.Row) else row[0])
            lease = row["lease"] if isinstance(row, sqlite3.Row) else row[1]
            if reply_sending_lease_expired(lease):
                expired.append(task_id)
            else:
                active.append(task_id)
        return {
            "ok": True,
            "dry_run": True,
            "action": "recover_reply_sending_leases",
            "total_reply_sending": len(rows),
            "expired_count": len(expired),
            "expired_task_ids": expired[:10],
            "active_count": len(active),
            "active_task_ids": active[:10],
        }
    from mobile_openclaw_cli import recover_stale_reply_sending_tasks

    return recover_stale_reply_sending_tasks(queue)


def schedule_due_reply_pending(queue: MobileQueue, config: dict[str, Any], apply: bool) -> dict[str, Any]:
    if not apply:
        with queue.session() as db:
            count = db.execute(
                """
                SELECT COUNT(*) FROM mobile_tasks
                WHERE push_status IN ('reply_pending','reply_retrying')
                """
            ).fetchone()[0]
        return {"ok": True, "dry_run": True, "action": "schedule_due_reply_pending", "candidate_count": int(count)}
    from mobile_openclaw_cli import process_pending_reply_context_retries

    return process_pending_reply_context_retries(queue, config)


def _json_object_from_text(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _push_failed_source_reason(task: dict[str, Any]) -> str:
    error_payload = _json_object_from_text(str(task.get("error") or ""))
    reply_payload = error_payload.get("reply") if isinstance(error_payload.get("reply"), dict) else {}
    for payload in (reply_payload, error_payload):
        source_reason = str(payload.get("source_reason") or payload.get("reason") or "")
        if source_reason:
            return source_reason
    return "recovered_push_failed"


def _push_failed_reply_account(task: dict[str, Any]) -> str:
    account_id = str(task.get("receiver_account_id") or "").strip()
    if account_id:
        return account_id
    error_payload = _json_object_from_text(str(task.get("error") or ""))
    reply_payload = error_payload.get("reply") if isinstance(error_payload.get("reply"), dict) else {}
    return str(reply_payload.get("account_id") or "").strip()


def _push_failed_recoverable_text(task: dict[str, Any]) -> str:
    result_text = str(task.get("result") or "").strip()
    if result_text:
        return result_text
    metadata = _json_object_from_text(str(task.get("metadata_json") or ""))
    if bool(metadata.get("outbound_only")) and str(task.get("source") or "") == "dashboard-weixin":
        return str(metadata.get("chat_record_text") or task.get("text") or "").strip()
    return ""


def push_failed_reply_recovery_candidates(queue: MobileQueue, limit: int = 50) -> list[dict[str, Any]]:
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, source, external_user, receiver_account_id, status, push_status,
                   text, result, error, metadata_json, updated_at
            FROM mobile_tasks
            WHERE status='push_failed'
              AND (
                    COALESCE(result, '') <> ''
                    OR (
                        source='dashboard-weixin'
                        AND COALESCE(text, '') <> ''
                        AND COALESCE(metadata_json, '') LIKE '%"outbound_only": true%'
                    )
                  )
              AND COALESCE(push_status, '') NOT IN ('reply_pending','reply_retrying','reply_sending','pushed_to_wecom')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["source_reason"] = _push_failed_source_reason(item)
        item["recovered_text_chars"] = len(_push_failed_recoverable_text(item))
        item["recovered_account_id"] = _push_failed_reply_account(item)
        item["has_route"] = bool(str(item.get("external_user") or "").strip() and str(item.get("recovered_account_id") or "").strip())
        candidates.append(item)
    return candidates


def recover_push_failed_to_reply_pending(queue: MobileQueue, config: dict[str, Any], apply: bool) -> dict[str, Any]:
    candidates = push_failed_reply_recovery_candidates(queue)
    if not apply:
        return {
            "ok": True,
            "dry_run": True,
            "action": "recover_push_failed_to_reply_pending",
            "candidate_count": len(candidates),
            "task_ids": [str(item.get("id") or "") for item in candidates[:20]],
        }

    recovered: list[str] = []
    skipped: list[dict[str, str]] = []
    for task in candidates:
        task_id = str(task.get("id") or "")
        result_text = _push_failed_recoverable_text(task)
        account_id = _push_failed_reply_account(task)
        if not task_id:
            continue
        if not result_text:
            skipped.append({"task_id": task_id, "reason": "empty_result"})
            continue
        if result_text.startswith("[supplement]"):
            skipped.append({"task_id": task_id, "reason": "internal_supplement_result"})
            continue
        if not str(task.get("external_user") or "").strip() or not account_id:
            skipped.append({"task_id": task_id, "reason": "missing_reply_route"})
            continue
        source_reason = _push_failed_source_reason(task)
        detail = {
            "ok": False,
            "recoverable": True,
            "reason": "waiting_weixin_context",
            "source_reason": source_reason,
            "account_id": account_id,
            "external_user": str(task.get("external_user") or ""),
            "next_step": "retry through reply_pending context recovery",
            "maintenance_action": "recover_push_failed_to_reply_pending",
        }
        queue.mark_reply_pending(task_id, json.dumps(detail, ensure_ascii=False))
        now = maintenance_now_iso()
        with queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='done',
                    result=CASE WHEN COALESCE(result, '')='' THEN ? ELSE result END,
                    receiver_account_id=CASE WHEN COALESCE(receiver_account_id, '')='' THEN ? ELSE receiver_account_id END,
                    error='',
                    push_status='reply_pending',
                    updated_at=?
                WHERE id=? AND status='push_failed'
                """,
                (result_text, account_id, now, task_id),
            )
        queue.runtime_delete(f"reply_pending_batch_notice:{task_id}")
        queue.runtime_delete(f"pending_reply_context_retry:{task_id}")
        queue.runtime_delete(f"reply_sending:{task_id}")
        queue.add_event("wecom", "final_reply_waiting_weixin_context", detail, task_id)
        queue.add_event("wecom", "push_failed_recovered_to_reply_pending", detail, task_id)
        recovered.append(task_id)
    return {
        "ok": True,
        "action": "recover_push_failed_to_reply_pending",
        "recovered_count": len(recovered),
        "recovered_task_ids": recovered,
        "skipped": skipped,
    }


def visibility_unconfirmed_reply_pending_candidates(queue: MobileQueue) -> list[dict[str, Any]]:
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT t.id, t.receiver_account_id, t.external_user, t.status, t.push_status,
                   t.updated_at, SUBSTR(COALESCE(t.result,''), 1, 180) AS result_preview
            FROM mobile_tasks t
            WHERE t.push_status IN ('reply_pending','reply_retrying')
              AND EXISTS (
                SELECT 1
                FROM mobile_events e
                WHERE e.task_id=t.id
                  AND e.event_type='final_reply_waiting_weixin_context'
                  AND e.payload_json LIKE '%"source_reason": "phone_visible_not_confirmed"%'
              )
            ORDER BY t.updated_at ASC
            LIMIT 100
            """
        ).fetchall()
    return [dict(row) for row in rows]


def reconcile_visibility_unconfirmed_reply_pending(queue: MobileQueue, apply: bool) -> dict[str, Any]:
    candidates = visibility_unconfirmed_reply_pending_candidates(queue)
    if not apply:
        return {
            "ok": True,
            "dry_run": True,
            "action": "reconcile_visibility_unconfirmed_reply_pending",
            "candidate_count": len(candidates),
            "task_ids": [str(item.get("id") or "") for item in candidates[:20]],
        }

    reconciled: list[str] = []
    failed: list[dict[str, str]] = []
    now = datetime.now(timezone.utc).isoformat()
    for item in candidates:
        task_id = str(item.get("id") or "")
        if not task_id:
            continue
        detail = {
            "ok": True,
            "delivery_accepted": True,
            "phone_visible_confirmed": False,
            "reason": "delivery_accepted_without_visibility_confirmation",
            "reconciled_from": str(item.get("push_status") or ""),
            "account_id": str(item.get("receiver_account_id") or ""),
            "maintenance_action": "reconcile_visibility_unconfirmed_reply_pending",
        }
        try:
            with queue.session() as db:
                changed = db.execute(
                    """
                    UPDATE mobile_tasks
                    SET status='pushed_to_wecom',
                        push_status='pushed_to_wecom',
                        pushed_at=COALESCE(pushed_at, ?),
                        updated_at=?
                    WHERE id=?
                      AND push_status IN ('reply_pending','reply_retrying')
                    """,
                    (now, now, task_id),
                ).rowcount
            if not changed:
                continue
            queue.add_event("wecom", "final_reply_visibility_unconfirmed_reconciled", detail, task_id)
            queue.add_event("wecom", "push_result", {"ok": True, "push_status": "pushed_to_wecom", "detail": json.dumps(detail, ensure_ascii=False)}, task_id)
            queue.runtime_delete(f"reply_pending_batch_notice:{task_id}")
            queue.runtime_delete(f"pending_reply_context_last_token:{task_id}")
            queue.runtime_delete(f"pending_reply_context_retry:{task_id}")
            queue.runtime_delete(f"reply_sending:{task_id}")
            reconciled.append(task_id)
        except Exception as exc:
            failed.append({"task_id": task_id, "reason": str(exc)})
    return {
        "ok": not failed,
        "action": "reconcile_visibility_unconfirmed_reply_pending",
        "reconciled_count": len(reconciled),
        "reconciled_task_ids": reconciled,
        "failed": failed,
    }


def repair_system(
    queue: MobileQueue,
    config: dict[str, Any],
    snapshot: dict[str, Any],
    diagnosis: dict[str, Any],
    apply: bool,
    include_reply_send: bool = False,
) -> dict[str, Any]:
    issues = diagnosis.get("issues") if isinstance(diagnosis.get("issues"), list) else []
    worker_task = str(config.get("control", {}).get("scheduled_task_name") or DEFAULT_WORKER_TASK)
    gateway_task = str(config.get("openclaw", {}).get("gateway_scheduled_task_name") or DEFAULT_GATEWAY_TASK)
    actions: list[dict[str, Any]] = []
    safe_fixes = {
        str(issue.get("code") or ""): str(issue.get("safe_auto_fix") or "")
        for issue in issues
        if issue.get("safe_auto_fix")
    }
    repair_plan = [
        {
            "code": "start_worker_task",
            "precondition": "worker_not_running has safe_auto_fix=start_worker_task",
            "would_mutate": "start the configured worker scheduled task only",
            "backup": "not applicable; scheduled task definition is not modified",
            "rollback": "stop the worker task if it was started unintentionally",
            "validation": "maintenance summary and worker process count",
        },
        {
            "code": "start_openclaw_gateway_task",
            "precondition": "gateway_port_down has safe_auto_fix=start_openclaw_gateway_task",
            "would_mutate": "start the configured OpenClaw Gateway scheduled task only",
            "backup": "not applicable; scheduled task definition is not modified",
            "rollback": "stop the Gateway task if it was started unintentionally",
            "validation": "maintenance summary and OpenClaw Gateway port check",
        },
        {
            "code": "cleanup_dashboard_live_tmp",
            "precondition": "dashboard live-state temp files are stale",
            "would_mutate": "remove dashboard runtime temp files only",
            "backup": "not required for temp files",
            "rollback": "dashboard recreates live-state files on next update",
            "validation": "maintenance summary dashboard_live layer",
        },
        {
            "code": "repair_codex_config_guard",
            "precondition": "Codex config guard reports required baseline drift",
            "would_mutate": "run merge-only codex_state_repair against global/project Codex config and global state",
            "backup": "creates _bridge/backups/<timestamp>-codex-state-repair with copied config files before writing",
            "rollback": "restore the generated backup files, then restart Codex Desktop",
            "validation": "codex-config-guard validate and maintenance summary --deep",
        },
        {
            "code": "repair_codex_mcp_config",
            "precondition": "known catalog MCP entries are missing or drifted",
            "would_mutate": "rewrite or append only expected catalog MCP server blocks",
            "backup": "creates a routed backup via shared backup_router before writing",
            "rollback": "restore the generated config backup, then restart Codex Desktop",
            "validation": "tool-registry-health after Codex Desktop restart",
        },
        {
            "code": "use_mobile_mcp_local_stdio_fallback",
            "precondition": "current Codex session reports MCP Transport closed while the mobile MCP direct smoke/fallback health is OK",
            "would_mutate": "nothing for get-pending-batch; ack-message writes the same mcp_ack runtime and mcp_message_acked event as the normal MCP tool only after Codex has incorporated the supplement",
            "backup": "not applicable for read-only health; ack is ordinary supplement consumption evidence, not a repair rewrite",
            "rollback": "no automatic rollback for a valid consumed supplement ack; invalid ack attempts are ignored or quarantined by existing MCP ack guards",
            "validation": "supplement-cli-fallback-check and maintenance summary --deep",
        },
        {
            "code": "refresh_codex_desktop_mcp_session",
            "precondition": "doctor reports codex_desktop_session_mcp_stale: current Desktop session has stale MCP host evidence or Codex version split",
            "would_mutate": "no bridge data mutation; recovery reloads the current Codex MCP session if such a route is exposed, otherwise requires a controlled Codex Desktop restart",
            "backup": "not applicable for process/session refresh; save work before restarting Desktop",
            "rollback": "not applicable; restart Codex Desktop again through the configured elevated startup path if the first refresh fails",
            "validation": "after refresh/restart, live MCP calls should succeed and maintenance summary --deep should report Desktop Session MCP layer=ok",
        },
        {
            "code": "repair_codex_plugin_enablement",
            "precondition": "current deep plugin evidence reports missing expected plugin enablement entries",
            "would_mutate": "append or set only missing [plugins.\"name@marketplace\"] enabled = true entries",
            "backup": "creates a routed backup via shared backup_router before writing",
            "rollback": "restore the generated config backup, then restart Codex Desktop",
            "validation": "tool-registry-health after Codex Desktop restart",
        },
        {
            "code": "sync_openclaw_account_onboarding",
            "precondition": "persisted OpenClaw Weixin accounts are missing dedicated Codex thread routes",
            "would_mutate": "create missing Codex app-server threads, append only missing bridge thread route items, and set per-user active-thread keys",
            "backup": "creates config.local.json.bak-<timestamp>-account-onboarding-sync before writing bridge thread routes",
            "rollback": "restore config backup and clear created user_active_thread runtime keys if the sync was unintended",
            "validation": "account-onboarding-sync-check and maintenance doctor",
        },
        {
            "code": "reconcile_visibility_unconfirmed_reply_pending",
            "precondition": "reply backlog contains accepted-but-phone-visibility-unconfirmed historical replies",
            "would_mutate": "mark matching historical reply_pending rows as pushed without sending Weixin messages",
            "backup": "database backup is not created by this scoped repair",
            "rollback": "manual DB restore from an external backup if needed",
            "validation": "final-reply-visibility-unconfirmed-check",
        },
        {
            "code": "observe_app_server_turn_materialization_lag",
            "precondition": "doctor reports app_server_turn_materialization_lag",
            "would_mutate": "nothing; worker recovery only rehydrates unreadable-dispatch evidence after app-server readback confirms a materialized turn or owned marker, and maintenance only reports it",
            "backup": "not applicable for read-only observation",
            "rollback": "not applicable",
            "validation": "queued-turn-materialized-readback-rehydrate-check",
        },
        {
            "code": "observe_app_server_repair_continuation",
            "precondition": "active app-server owner is acked but empty-spinning, or terminates without owned mobile_result markers",
            "would_mutate": "maintenance repair mutates nothing; worker recovery may interrupt the old turn once, submit one continuation to the same thread with the original result markers, and then fail closed if continuation cannot start or complete",
            "backup": "not applicable for maintenance dry-run; worker records durable events before and after the continuation attempt",
            "rollback": "no automatic rollback; if the single continuation fails, manual recovery is required instead of alternate prompt retries",
            "validation": "app-server-repair-continuation-check",
        },
        {
            "code": "observe_control_reply_receipts",
            "precondition": "doctor reports control_reply_receipt_contract_broken",
            "would_mutate": "nothing; maintenance only reports missing control reply receipts so the command path can be fixed or retried deliberately",
            "backup": "not applicable for read-only observation",
            "rollback": "not applicable",
            "validation": "control-receipt-contract-check and maintenance doctor",
        },
        {
            "code": "review_resource_process_fanout",
            "precondition": "doctor reports resource_process_fanout, resource_process_fanout_advisory, or codex_app_server_owner_unhealthy",
            "would_mutate": "nothing; maintenance only surfaces resource/MCP process fanout, app-server owner drift, and links to the read-only resource-process repair plan",
            "backup": "not applicable for read-only observation",
            "rollback": "not applicable",
            "validation": "resource-process metrics, resource-process doctor, and resource-process repair-plan",
        },
        {
            "code": "recover_stale_reply_sending",
            "precondition": "reply_sending lease is expired",
            "would_mutate": "move expired reply_sending lease rows back to retryable reply state",
            "backup": "database backup is not created by this scoped repair",
            "rollback": "manual DB restore from an external backup if needed",
            "validation": "maintenance summary reply backlog",
        },
        {
            "code": "schedule_due_reply_pending",
            "precondition": "requires --include-reply-send because it can send messages to Weixin users",
            "would_mutate": "schedule due reply_pending messages for sending",
            "backup": "database backup is not created by this scoped repair",
            "rollback": "cannot unsend a Weixin message; use only after reviewing doctor output",
            "validation": "reply delivery status and phone-visible confirmation",
        },
        {
            "code": "recover_push_failed_to_reply_pending",
            "precondition": "reply backlog contains historical push_failed rows with durable result text",
            "would_mutate": "move eligible push_failed rows back into the governed reply_pending retry path",
            "backup": "database backup is not created by this scoped repair",
            "rollback": "manual DB restore from an external backup if needed",
            "validation": "maintenance summary reply backlog and reply delivery status",
        },
    ]

    if safe_fixes.get("worker_not_running") == "start_worker_task":
        actions.append({"code": "start_worker_task", "result": start_scheduled_task(worker_task, apply)})
    if safe_fixes.get("gateway_port_down") == "start_openclaw_gateway_task":
        actions.append({"code": "start_openclaw_gateway_task", "result": start_scheduled_task(gateway_task, apply)})
    if (
        safe_fixes.get("dashboard_live_state_stale") == "cleanup_dashboard_live_tmp"
        or safe_fixes.get("dashboard_live_tmp_files") == "cleanup_dashboard_live_tmp"
    ):
        actions.append({"code": "cleanup_dashboard_live_tmp", "result": cleanup_dashboard_tmp_files(apply)})
    if safe_fixes.get("codex_mcp_config_incomplete") == "repair_codex_mcp_config":
        codex_mcp_config = snapshot.get("codex_mcp_config") if isinstance(snapshot.get("codex_mcp_config"), dict) else {}
        actions.append(
            {
                "code": "repair_codex_mcp_config",
                "result": repair_codex_mcp_config(config, codex_mcp_config, apply),
            }
        )
    if safe_fixes.get("codex_config_guard_drift") == "repair_codex_config_guard":
        actions.append(
            {
                "code": "repair_codex_config_guard",
                "result": repair_codex_config_guard(apply),
            }
        )
    if safe_fixes.get("codex_plugin_enablement_incomplete") == "repair_codex_plugin_enablement":
        codex_plugins = snapshot.get("codex_plugins") if isinstance(snapshot.get("codex_plugins"), dict) else {}
        actions.append(
            {
                "code": "repair_codex_plugin_enablement",
                "result": repair_codex_plugin_enablement(config, codex_plugins, apply),
            }
        )
    if safe_fixes.get("openclaw_account_thread_drift") == "sync_openclaw_account_onboarding":
        actions.append(
            {
                "code": "sync_openclaw_account_onboarding",
                "result": sync_openclaw_account_onboarding(queue, config, apply),
            }
        )
    if safe_fixes.get("reply_delivery_backlog") == "recover_stale_reply_sending_and_retry_due_reply_pending":
        actions.append(
            {
                "code": "reconcile_visibility_unconfirmed_reply_pending",
                "result": reconcile_visibility_unconfirmed_reply_pending(queue, apply),
            }
        )
        actions.append({"code": "recover_stale_reply_sending", "result": recover_reply_sending_leases(queue, apply)})
        actions.append(
            {
                "code": "recover_push_failed_to_reply_pending",
                "result": recover_push_failed_to_reply_pending(queue, config, apply),
            }
        )
        if include_reply_send:
            actions.append({"code": "schedule_due_reply_pending", "result": schedule_due_reply_pending(queue, config, apply)})
        else:
            actions.append(
                {
                    "code": "schedule_due_reply_pending",
                    "result": {
                        "ok": True,
                        "skipped": True,
                        "reason": "requires --include-reply-send because this can send messages to Weixin users",
                    },
                }
            )

    skipped = []
    for issue in issues:
        if issue.get("safe_auto_fix"):
            continue
        skipped.append(
            {
                "code": issue.get("code"),
                "severity": issue.get("severity"),
                "reason": "no safe automatic repair defined",
                "manual_action": issue.get("manual_action"),
            }
        )
    return {
        "ok": all(bool(item.get("result", {}).get("ok")) for item in actions),
        "applied": bool(apply),
        "include_reply_send": bool(include_reply_send),
        "policy": default_maintenance_policy(),
        "plan": repair_plan,
        "dry_run_contract": {
            "writes_files": bool(apply),
            "sends_weixin_messages": bool(apply and include_reply_send),
            "never_auto": default_maintenance_policy().get("manual_only", []),
        },
        "actions": actions,
        "skipped": skipped,
    }


def inspect_report(queue: MobileQueue, config: dict[str, Any]) -> dict[str, Any]:
    return inspect_system(queue, config)


def doctor_report(queue: MobileQueue, config: dict[str, Any]) -> dict[str, Any]:
    snapshot = inspect_system(queue, config)
    diagnosis = diagnose_system(snapshot)
    return {
        "ok": diagnosis.get("ok"),
        "snapshot": snapshot,
        "diagnosis": diagnosis,
        "advisories": iteration_advisories(snapshot),
    }


def iteration_gate_report(queue: MobileQueue, config: dict[str, Any]) -> dict[str, Any]:
    return controlled_iteration_gate_report()


def metrics_report(queue: MobileQueue, config: dict[str, Any], deep_probes: bool = True) -> dict[str, Any]:
    snapshot = inspect_system(queue, config, deep_probes=deep_probes)
    diagnosis = diagnose_system(snapshot)
    return observability_metrics(snapshot, diagnosis)


def repair_report(
    queue: MobileQueue,
    config: dict[str, Any],
    apply: bool = False,
    include_reply_send: bool = False,
) -> dict[str, Any]:
    snapshot = inspect_system(queue, config)
    diagnosis = diagnose_system(snapshot)
    repair = repair_system(queue, config, snapshot, diagnosis, apply=apply, include_reply_send=include_reply_send)
    return {
        "ok": bool(repair.get("ok")),
        "apply": bool(apply),
        "diagnosis": diagnosis,
        "repair": repair,
        "advisories": iteration_advisories(snapshot),
    }
