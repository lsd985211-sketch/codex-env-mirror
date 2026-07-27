"""Thread prewarm runtime marker helpers for the mobile bridge.

Owns: prewarm cooldown/budget config readers and runtime marker read/write
helpers for Codex app-server thread prewarming.
Non-goals: app-server probing, background process startup, dispatch decisions,
retry scheduling, or task queue lifecycle.
State behavior: reads trigger config; reads/writes only `thread_prewarm:*`
runtime keys and records the `thread_prewarm_scheduled` audit event.
Normal callers: mobile_openclaw_cli worker dispatch path and thread prewarm
regression checks.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from thread_route_state import thread_prewarm_key


def thread_prewarm_cooldown_seconds(config: dict[str, Any]) -> int:
    return max(5, int(config.get("trigger", {}).get("thread_prewarm_cooldown_seconds") or 30))


def thread_prewarm_budget_seconds(config: dict[str, Any]) -> int:
    return max(2, int(config.get("trigger", {}).get("thread_prewarm_timeout_seconds") or 5))


def get_thread_prewarm(queue: Any, thread_id: str) -> dict[str, Any]:
    raw = queue.runtime_get(thread_prewarm_key(thread_id))
    if not raw:
        return {"active": False}
    try:
        data = json.loads(raw)
        retry_after = datetime.fromisoformat(str(data.get("retry_after") or ""))
    except Exception:
        queue.runtime_delete(thread_prewarm_key(thread_id))
        return {"active": False, "reason": "invalid_prewarm_marker"}
    now = datetime.now(timezone.utc)
    if now >= retry_after:
        queue.runtime_delete(thread_prewarm_key(thread_id))
        data["active"] = False
        data["ready"] = True
        return data
    data["active"] = True
    data["remaining_seconds"] = max(0, int((retry_after - now).total_seconds()))
    return data


def mark_thread_prewarm(
    queue: Any,
    config: dict[str, Any],
    thread_id: str,
    thread_name: str = "",
    reason: str = "thread_unavailable",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    payload = {
        "thread_id": thread_id,
        "thread_name": thread_name,
        "reason": reason,
        "started_at": now.isoformat(),
        "retry_after": (now + timedelta(seconds=thread_prewarm_cooldown_seconds(config))).isoformat(),
    }
    queue.runtime_set(thread_prewarm_key(thread_id), json.dumps(payload, ensure_ascii=False))
    queue.add_event("local", "thread_prewarm_scheduled", payload)
    return payload


def clear_thread_prewarm(queue: Any, thread_id: str) -> None:
    queue.runtime_delete(thread_prewarm_key(thread_id))
