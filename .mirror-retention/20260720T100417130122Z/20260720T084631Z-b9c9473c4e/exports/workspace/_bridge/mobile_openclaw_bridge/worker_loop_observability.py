"""Worker loop observability helpers.

Owns: deciding when a worker cycle is worth logging and building compact cycle
summaries.
Non-goals: dispatch decisions, queue mutation, recovery actions, or sending.
"""

from __future__ import annotations

import json
from typing import Any


def worker_loop_should_log(result: dict[str, Any], previous_signature: str) -> tuple[bool, str]:
    action = str(result.get("action") or "")
    processed = int(result.get("processed") or 0)
    ok = bool(result.get("ok", True))
    queue_activity = (
        processed
        or int((result.get("recovery") or {}).get("recovered") or 0)
        or int((result.get("recovery") or {}).get("reverted") or 0)
        or int((result.get("reply_sending_recovery") or {}).get("recovered_count") or 0)
        or int((result.get("pending_reply_retries") or {}).get("scheduled") or 0)
        or int((result.get("queued_supplement_release") or {}).get("released_count") or 0)
        or int((result.get("orphaned_supplement_promotion") or {}).get("promoted_count") or 0)
        or int((result.get("invalid_supplement_release") or {}).get("released_count") or 0)
        or int((result.get("attachment_supplements") or {}).get("published_count") or 0)
    )
    skipped = {
        key: result.get(key)
        for key in (
            "skipped_retry_wait",
            "skipped_unassigned",
            "skipped_unavailable",
            "skipped_published_supplement",
            "skipped_busy_route",
        )
        if result.get(key)
    }
    signature = json.dumps(
        {
            "ok": ok,
            "action": action,
            "queue_activity": bool(queue_activity),
            "skipped": skipped,
            "pending_reply_waiting_context": (result.get("pending_reply_retries") or {}).get("waiting_context"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    should_log = (not ok) or bool(queue_activity) or action not in {"idle", "recovery_cycle"} or bool(skipped)
    if not should_log and signature != previous_signature:
        should_log = True
    return should_log, signature


def worker_loop_summary(cycle: int, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "cycle": cycle,
        "ok": result.get("ok"),
        "action": result.get("action"),
        "processed": result.get("processed"),
        "recovery": {
            "recovered": (result.get("recovery") or {}).get("recovered"),
            "reverted": (result.get("recovery") or {}).get("reverted"),
            "lease_released": (result.get("recovery") or {}).get("lease_released"),
            "sent_active": (result.get("recovery") or {}).get("sent_active"),
            "still_waiting": (result.get("recovery") or {}).get("still_waiting"),
        },
        "reply_sending_recovered": (result.get("reply_sending_recovery") or {}).get("recovered_count"),
        "pending_reply_retries": {
            "scheduled": (result.get("pending_reply_retries") or {}).get("scheduled"),
            "skipped": (result.get("pending_reply_retries") or {}).get("skipped"),
            "waiting_context": (result.get("pending_reply_retries") or {}).get("waiting_context"),
        },
        "queued_supplement_released": (result.get("queued_supplement_release") or {}).get("released_count"),
        "orphaned_supplement_promoted": (result.get("orphaned_supplement_promotion") or {}).get("promoted_count"),
        "invalid_supplement_released": (result.get("invalid_supplement_release") or {}).get("released_count"),
        "attachment_supplements_published": (result.get("attachment_supplements") or {}).get("published_count"),
        "skipped": {
            key: result.get(key)
            for key in (
                "skipped_retry_wait",
                "skipped_unassigned",
                "skipped_unavailable",
                "skipped_published_supplement",
                "skipped_busy_route",
            )
            if result.get(key)
        },
    }


def worker_loop_has_activity(result: dict[str, Any]) -> bool:
    if int(result.get("processed") or 0) > 0:
        return True
    recovery = result.get("recovery") if isinstance(result.get("recovery"), dict) else {}
    reply_recovery = result.get("reply_sending_recovery") if isinstance(result.get("reply_sending_recovery"), dict) else {}
    pending_retry = result.get("pending_reply_retries") if isinstance(result.get("pending_reply_retries"), dict) else {}
    queued_supplement_release = result.get("queued_supplement_release") if isinstance(result.get("queued_supplement_release"), dict) else {}
    orphaned_supplement_promotion = result.get("orphaned_supplement_promotion") if isinstance(result.get("orphaned_supplement_promotion"), dict) else {}
    invalid_supplement_release = result.get("invalid_supplement_release") if isinstance(result.get("invalid_supplement_release"), dict) else {}
    attachment_supplements = result.get("attachment_supplements") if isinstance(result.get("attachment_supplements"), dict) else {}
    activity_counts = [
        int(recovery.get("recovered") or 0),
        int(recovery.get("reverted") or 0),
        int(recovery.get("lease_released") or 0),
        int(reply_recovery.get("recovered_count") or 0),
        int(pending_retry.get("scheduled") or 0),
        int(queued_supplement_release.get("released_count") or 0),
        int(orphaned_supplement_promotion.get("promoted_count") or 0),
        int(invalid_supplement_release.get("released_count") or 0),
        int(attachment_supplements.get("published_count") or 0),
    ]
    if any(count > 0 for count in activity_counts):
        return True
    if result.get("action") not in {"idle", "recovery_cycle", "idle_waiting_owned_result_gate", "idle_no_dispatchable_thread", "idle_thread_mismatch"}:
        return True
    for key in ("skipped_retry_wait", "skipped_unassigned", "skipped_unavailable", "skipped_busy_route", "skipped_published_supplement"):
        if result.get(key):
            return True
    return False
