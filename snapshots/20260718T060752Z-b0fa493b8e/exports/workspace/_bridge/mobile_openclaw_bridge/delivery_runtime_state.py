"""Delivery retry and route recovery runtime state for the mobile bridge.

Owns: delivery retry cooldown policy, retry markers, thread recovery markers,
active route lease expiry, and short-window diagnostic event coalescing.
Non-goals: dispatch execution, final reply sending, permission decisions,
owned-result recovery, app-server repair execution, or task completion.
State behavior: reads trigger config; reads/writes only delivery retry and
thread recovery runtime keys; writes coalesced diagnostic events through the
queue event API.
Normal callers: mobile_openclaw_cli worker dispatch/recovery paths and bridge
regression checks.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from cli_utils import parse_iso_datetime
from thread_route_state import delivery_retry_key, thread_recovery_key


def delivery_retry_seconds(config: dict[str, Any]) -> int:
    return max(3, int(config.get("trigger", {}).get("delivery_retry_seconds") or 15))


def delivery_retry_seconds_for_reason(config: dict[str, Any], reason: str) -> int:
    """Cooldown before a pending task may be dispatched again."""

    trigger = config.get("trigger", {})
    reason_value = str(reason or "")
    if reason_value == "active_lease_expired_without_owned_result":
        return 0
    if reason_value == "app_server_turn_not_readable_after_dispatch":
        return max(60, int(trigger.get("app_server_unreadable_turn_retry_seconds") or 120))
    if reason_value == "protocol_violation_no_owned_result":
        return max(5, int(trigger.get("protocol_violation_retry_seconds") or trigger.get("delivery_retry_seconds") or 15))
    if reason_value in {
        "cdp_visible_submission_unverified_observed",
        "cdp_visible_submission_needs_attention",
    }:
        return max(1, int(trigger.get("delivery_retry_seconds") or 15))
    if reason_value in {
        "dispatch_failed",
        "visible_cdp_not_confirmed",
        "delivery_missing_turn_id",
        "visible_cdp_busy",
        "mcp_transport_closed",
        "app_server_mcp_transport_closed",
        "mcp_tool_surface_unavailable",
    }:
        return max(
            5,
            int(trigger.get("visible_cdp_busy_retry_seconds") or trigger.get("delivery_retry_seconds") or 5),
        )
    if reason_value in {
        "visible_cdp_probe_failed",
        "codex_cdp_stale_os_listener",
        "thread_probe_failed",
        "probe_failed",
    }:
        if reason_value == "codex_cdp_stale_os_listener":
            return max(30, int(trigger.get("codex_cdp_stale_listener_retry_seconds") or 30))
        return max(1, int(trigger.get("probe_retry_seconds") or 1))
    return delivery_retry_seconds(config)


def delivery_retry_reason_allows_batch(reason: str) -> bool:
    return str(reason or "") in {
        "mcp_transport_closed",
        "app_server_mcp_transport_closed",
        "thread_busy",
        "thread_unavailable",
        "thread_prewarming",
        "visible_cdp_busy",
        "attachment_preempted",
    }


def active_slot_release_after_seconds(config: dict[str, Any]) -> int:
    """How long a sent task may occupy a delivery route without owned output."""

    return max(30, int(config.get("trigger", {}).get("active_slot_release_after_seconds") or 90))


def active_route_lease_expired(task: dict[str, Any], config: dict[str, Any], now: datetime | None = None) -> bool:
    status = str(task.get("status") or "")
    if status not in {"sent_to_codex", "processing"}:
        return False
    now = now or datetime.now(timezone.utc)
    lease_seconds = active_slot_release_after_seconds(config)
    sent_at = parse_iso_datetime(str(task.get("sent_to_codex_at") or task.get("updated_at") or ""))
    if not sent_at:
        return True
    return now >= sent_at + timedelta(seconds=lease_seconds)


def get_delivery_retry(queue: Any, task_id: str) -> dict[str, Any]:
    raw = queue.runtime_get(delivery_retry_key(task_id))
    if not raw:
        return {"active": False}
    try:
        data = json.loads(raw)
        retry_after = datetime.fromisoformat(str(data.get("retry_after") or ""))
    except Exception:
        queue.runtime_delete(delivery_retry_key(task_id))
        return {"active": False, "reason": "invalid_retry_marker"}
    now = datetime.now(timezone.utc)
    if now >= retry_after:
        if str(data.get("reason") or "") != "active_lease_expired_without_owned_result":
            queue.runtime_delete(delivery_retry_key(task_id))
        data["active"] = False
        data["ready"] = True
        return data
    data["active"] = True
    data["remaining_seconds"] = max(0, int((retry_after - now).total_seconds()))
    return data


def event_coalesce_key(task_id: str | None, event_type: str, signature: str) -> str:
    raw = f"{task_id or '-'}|{event_type}|{signature}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"event_coalesce:{digest}"


def _stable_signature(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]


def add_coalesced_event(
    queue: Any,
    source: str,
    event_type: str,
    payload: dict[str, Any],
    task_id: str | None = None,
    signature: str = "",
    window_seconds: int = 60,
) -> bool:
    """Insert the first diagnostic event in a short window, then count repeats."""

    now_dt = datetime.now(timezone.utc)
    signature = signature or _stable_signature(payload)
    key = event_coalesce_key(task_id, event_type, signature)
    current = {
        "event_type": event_type,
        "task_id": task_id or "",
        "signature": signature,
        "first_seen_at": now_dt.isoformat(),
        "last_seen_at": now_dt.isoformat(),
        "suppressed_count": 0,
    }
    raw = queue.runtime_get(key)
    if raw:
        try:
            previous = json.loads(raw)
            first_seen = datetime.fromisoformat(str(previous.get("first_seen_at") or ""))
            if (now_dt - first_seen).total_seconds() < max(1, int(window_seconds)):
                previous["last_seen_at"] = now_dt.isoformat()
                previous["suppressed_count"] = int(previous.get("suppressed_count") or 0) + 1
                queue.runtime_set(key, json.dumps(previous, ensure_ascii=False))
                return False
            current["previous_suppressed_count"] = int(previous.get("suppressed_count") or 0)
            current["previous_first_seen_at"] = previous.get("first_seen_at")
            current["previous_last_seen_at"] = previous.get("last_seen_at")
        except Exception:
            current["previous_marker_invalid"] = True
    event_payload = dict(payload or {})
    if current.get("previous_suppressed_count"):
        event_payload["coalesced_previous_suppressed_count"] = current["previous_suppressed_count"]
        event_payload["coalesced_previous_first_seen_at"] = current.get("previous_first_seen_at")
        event_payload["coalesced_previous_last_seen_at"] = current.get("previous_last_seen_at")
    queue.runtime_set(key, json.dumps(current, ensure_ascii=False))
    queue.add_event(source, event_type, event_payload, task_id)
    return True


def mark_delivery_retry(
    queue: Any,
    config: dict[str, Any],
    task_ids: list[str],
    reason: str,
    detail: dict[str, Any] | None = None,
) -> None:
    seconds = delivery_retry_seconds_for_reason(config, reason)
    retry_after = (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat()
    payload = {
        "reason": reason,
        "retry_after": retry_after,
        "detail": detail or {},
    }
    for tid in task_ids:
        queue.runtime_set(delivery_retry_key(tid), json.dumps(payload, ensure_ascii=False))
        add_coalesced_event(
            queue,
            "local",
            "delivery_retry_scheduled",
            payload,
            tid,
            signature=str(reason or "unknown"),
        )


def clear_delivery_retry(queue: Any, task_ids: list[str]) -> None:
    for tid in task_ids:
        previous = get_delivery_retry(queue, tid)
        queue.runtime_delete(delivery_retry_key(tid))
        if previous:
            queue.add_event("local", "delivery_retry_cleared", previous, tid)


def get_thread_recovery(queue: Any, task_id: str) -> dict[str, Any]:
    raw = queue.runtime_get(thread_recovery_key(task_id))
    if not raw:
        return {"active": False, "attempts": 0}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        queue.runtime_delete(thread_recovery_key(task_id))
        return {"active": False, "attempts": 0, "reason": "invalid_recovery_marker"}
    data["active"] = True
    data["attempts"] = max(0, int(data.get("attempts") or 0))
    return data


def mark_thread_recovery(
    queue: Any,
    task_id: str,
    reason: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous = get_thread_recovery(queue, task_id)
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "active": True,
        "reason": reason,
        "attempts": int(previous.get("attempts") or 0) + 1,
        "first_seen_at": previous.get("first_seen_at") or now,
        "last_seen_at": now,
        "detail": detail or {},
    }
    queue.runtime_set(thread_recovery_key(task_id), json.dumps(payload, ensure_ascii=False))
    add_coalesced_event(
        queue,
        "local",
        "thread_recovery_marked",
        payload,
        task_id,
        signature=str(reason or "unknown"),
    )
    return payload


def clear_thread_recovery(queue: Any, task_ids: list[str]) -> list[dict[str, Any]]:
    cleared: list[dict[str, Any]] = []
    for tid in task_ids:
        previous = get_thread_recovery(queue, tid)
        if previous.get("active"):
            queue.runtime_delete(thread_recovery_key(tid))
            queue.add_event("local", "thread_recovery_cleared", previous, tid)
            cleared.append({"task_id": tid, "previous": previous})
    return cleared
