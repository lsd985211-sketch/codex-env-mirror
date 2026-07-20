"""Read-only evidence helpers for scoped mobile repair modes.

Owns: compact active/reply/supplement evidence snapshots used by repair
subcommands.
Non-goals: repair execution, queue mutation, acknowledgements, or sending.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from mobile_queue import MobileQueue


def snapshot_active_task_ids(snapshot: dict[str, Any]) -> list[str]:
    active = snapshot.get("active") if isinstance(snapshot.get("active"), list) else []
    return [str(item.get("id") or "") for item in active if isinstance(item, dict) and item.get("id")]


def snapshot_reply_task_ids(snapshot: dict[str, Any]) -> list[str]:
    replies = snapshot.get("reply_problems") if isinstance(snapshot.get("reply_problems"), list) else []
    return [str(item.get("id") or "") for item in replies if isinstance(item, dict) and item.get("id")]


def quick_active_repair_evidence(queue: MobileQueue, limit: int = 20) -> dict[str, Any]:
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, status, receiver_account_id, external_user, codex_thread_id,
                   sent_to_codex_at, updated_at, SUBSTR(COALESCE(text,''), 1, 120) AS text_preview
            FROM mobile_tasks
            WHERE status IN ('queued_for_codex','sent_to_codex','processing','pending')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    tasks = [dict(row) for row in rows]
    return {
        "task_ids": [str(item.get("id") or "") for item in tasks],
        "active_task_ids": [
            str(item.get("id") or "")
            for item in tasks
            if str(item.get("status") or "") in {"queued_for_codex", "sent_to_codex", "processing"}
        ],
        "pending_task_ids": [str(item.get("id") or "") for item in tasks if str(item.get("status") or "") == "pending"],
        "status_counts": {
            status: sum(1 for item in tasks if str(item.get("status") or "") == status)
            for status in sorted({str(item.get("status") or "") for item in tasks})
        },
        "tasks": tasks,
    }


def quick_reply_backlog_evidence(
    queue: MobileQueue,
    visibility_unconfirmed_candidates: Callable[[MobileQueue], list[dict[str, Any]]],
    limit: int = 20,
) -> dict[str, Any]:
    with queue.session() as db:
        rows = db.execute(
            """
            SELECT id, status, push_status, receiver_account_id, external_user,
                   updated_at, SUBSTR(COALESCE(result,''), 1, 160) AS result_preview
            FROM mobile_tasks
            WHERE push_status IN ('reply_pending','reply_retrying','reply_sending','push_failed')
               OR status IN ('push_failed')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    tasks = [dict(row) for row in rows]
    visibility_candidates = visibility_unconfirmed_candidates(queue)
    return {
        "reply_task_ids": [str(item.get("id") or "") for item in tasks],
        "push_status_counts": {
            status: sum(1 for item in tasks if str(item.get("push_status") or "") == status)
            for status in sorted({str(item.get("push_status") or "") for item in tasks})
        },
        "visibility_unconfirmed_task_ids": [str(item.get("id") or "") for item in visibility_candidates[:20]],
        "tasks": tasks,
    }


def quick_supplement_repair_evidence(queue: MobileQueue, limit: int = 30) -> dict[str, Any]:
    with queue.session() as db:
        runtime_rows = db.execute(
            """
            SELECT key, value, updated_at
            FROM mobile_runtime
            WHERE key LIKE 'bridge_supplement:%'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        pending_rows = db.execute(
            """
            SELECT id, status, receiver_account_id, external_user, codex_thread_id,
                   updated_at, SUBSTR(COALESCE(text,''), 1, 160) AS text_preview
            FROM mobile_tasks
            WHERE status='pending'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    runtime_items: list[dict[str, Any]] = []
    supplement_task_ids: list[str] = []
    for row in runtime_rows:
        item = dict(row)
        payload: dict[str, Any] = {}
        try:
            loaded = json.loads(str(item.get("value") or "{}"))
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            payload = {}
        item["payload"] = payload
        ids = payload.get("pending_task_ids")
        if isinstance(ids, list):
            supplement_task_ids.extend(str(value) for value in ids if str(value).strip())
        elif payload.get("task_id"):
            supplement_task_ids.append(str(payload.get("task_id")))
        runtime_items.append(item)
    return {
        "runtime_keys": [str(item.get("key") or "") for item in runtime_items],
        "supplement_task_ids": sorted(set(supplement_task_ids)),
        "pending_task_ids": [str(row["id"]) for row in pending_rows],
        "runtime_items": runtime_items,
        "pending_sample": [dict(row) for row in pending_rows],
    }
