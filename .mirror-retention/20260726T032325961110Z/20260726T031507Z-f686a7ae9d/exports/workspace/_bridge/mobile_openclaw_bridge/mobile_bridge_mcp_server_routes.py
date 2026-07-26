#!/usr/bin/env python3
"""MCP supplement route validation for the OpenClaw bridge.

Ownership: decide whether a same-thread supplement runtime payload is
currently safe and useful for MCP callers to consume.
Non-goals: JSON-RPC transport, HTTP handling, tool schema registration, and
acknowledgement writes beyond the existing queue/runtime events used by the
supplement validator.
State behavior: may update the provided queue runtime key and add queue
events exactly as the previous in-server validator did.
Caller context: mobile_bridge_mcp_server.BridgeMcpService.get_pending_batch.
"""

from __future__ import annotations

import json
from typing import Any

from mobile_openclaw_cli import (  # noqa: E402
    bridge_supplement_base_task_id,
    bridge_supplement_key,
    bridge_supplement_recently_completed_owner,
    bridge_supplement_task_ids,
    mcp_ack_payload,
    promote_orphaned_bridge_supplements,
    task_owns_final_reply,
    valid_active_supplement_host,
    valid_mcp_ack_base_owner,
)


def _json_object(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_valid_supplement(queue: Any, thread_id: str, config: dict[str, Any] | None = None) -> str:
    """Return only currently consumable supplement payloads for MCP callers."""
    promote_orphaned_bridge_supplements(queue, config, thread_id)
    key = bridge_supplement_key(thread_id)
    raw = str(queue.runtime_get(key) or "")
    payload = _json_object(raw)
    if not payload:
        return ""

    task_ids = bridge_supplement_task_ids(payload)
    if not task_ids:
        queue.runtime_delete(key)
        queue.add_event(
            "local",
            "mcp_stale_supplement_released",
            {"runtime_key": key, "reason": "empty_supplement_items"},
            None,
        )
        return ""

    with queue.session() as db:
        rows = db.execute(
            f"""
            SELECT id, status
            FROM mobile_tasks
            WHERE id IN ({",".join("?" for _ in task_ids)})
            """,
            task_ids,
        ).fetchall()
    status_by_id = {str(row["id"] or ""): str(row["status"] or "") for row in rows}
    acked_task_ids = [
        tid
        for tid in task_ids
        if (mcp_ack_payload(queue, tid) and valid_mcp_ack_base_owner(queue, tid, mcp_ack_payload(queue, tid))[0])
    ]
    owner_task_ids = [tid for tid in task_ids if task_owns_final_reply(queue, tid)]
    pending_task_ids = [
        tid
        for tid in task_ids
        if status_by_id.get(tid) == "pending" and tid not in set(acked_task_ids)
    ]
    pending_task_ids = [tid for tid in pending_task_ids if tid not in set(owner_task_ids)]

    base_task_id = bridge_supplement_base_task_id(payload)
    base_task = queue.get_task(base_task_id) if base_task_id else None
    base_status = str((base_task or {}).get("status") or "")
    host_valid = bool(base_task and valid_active_supplement_host(queue, base_task))
    recently_completed_host = bool(
        base_task
        and base_status in {"done", "pushed_to_wecom"}
        and bridge_supplement_recently_completed_owner(queue, base_task, payload, config)
    )
    host_consumable = bool(host_valid or recently_completed_host)

    if pending_task_ids and host_consumable:
        if len(pending_task_ids) == len(task_ids):
            return raw
        pending_set = set(pending_task_ids)
        payload["items"] = [
            item
            for item in payload.get("items", [])
            if isinstance(item, dict) and str(item.get("message_id") or "") in pending_set
        ]
        sanitized = json.dumps(payload, ensure_ascii=False)
        queue.runtime_set(key, sanitized)
        queue.add_event(
            "local",
            "mcp_stale_supplement_sanitized",
            {
                "runtime_key": key,
                "base_task_id": base_task_id,
                "thread_id": thread_id,
                "task_ids": task_ids,
                "pending_task_ids": pending_task_ids,
                "acked_task_ids": acked_task_ids,
                "owner_task_ids": owner_task_ids,
                "reason": "removed_non_pending_supplement_items",
            },
            pending_task_ids[0],
        )
        return sanitized

    if pending_task_ids:
        reason = "host_not_active"
        if base_status in {"pending", "queued_for_codex"}:
            reason = "owner_waiting_redelivery"
        elif (
            base_status in {"done", "pushed_to_wecom"}
            and base_task_id
            and bridge_supplement_recently_completed_owner(queue, base_task, payload, config)
        ):
            reason = "owner_completed_ack_grace"
        event_payload = {
            "runtime_key": key,
            "base_task_id": base_task_id,
            "base_status": base_status,
            "thread_id": thread_id,
            "task_ids": task_ids,
            "pending_task_ids": pending_task_ids,
            "acked_task_ids": acked_task_ids,
            "owner_task_ids": owner_task_ids,
            "reason": reason,
            "policy": "preserve supplement identity; do not delete runtime payload or expose it as ordinary pending work",
        }
        for target in pending_task_ids:
            queue.add_event("local", "mcp_supplement_not_ready_preserved", event_payload, target)
        return ""

    queue.runtime_delete(key)
    reason = "host_not_active" if not host_consumable else "no_pending_supplement_items"
    event_payload = {
        "runtime_key": key,
        "base_task_id": base_task_id,
        "base_status": base_status,
        "thread_id": thread_id,
        "task_ids": task_ids,
        "pending_task_ids": pending_task_ids,
        "acked_task_ids": acked_task_ids,
        "owner_task_ids": owner_task_ids,
        "reason": reason,
    }
    targets = task_ids or [None]
    for target in targets:
        queue.add_event("local", "mcp_stale_supplement_released", event_payload, target)
    return ""


