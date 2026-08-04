#!/usr/bin/env python3
"""Coordination target admission for workflow closeout.

Ownership: validates caller identity and Codex App target facts before a
workflow may request cross-task coordination.
Non-goals: sending messages, mutating state, or selecting business ownership.
State behavior: pure read-only validation over caller-supplied App facts.
Caller context: workflow_closeout_signals consumes the result for handoff.
"""

from __future__ import annotations

import json
import os
from typing import Any


def current_thread_id(value: str = "") -> str:
    """Return the caller ID injected by the Codex environment."""

    return str(value or os.environ.get("CODEX_THREAD_ID") or "").strip()


def _thread_fact(value: Any) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Accept only a structured Codex App active-target observation."""

    if isinstance(value, dict):
        payload = value
    else:
        text = str(value or "").strip()
        if not text or not text.startswith("{"):
            return None, {"thread_id": text, "reason": "live_state_evidence_required"}
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return None, {"thread_id": "", "reason": "live_state_evidence_invalid"}
        payload = loaded if isinstance(loaded, dict) else None
    if not payload:
        return None, {"thread_id": "", "reason": "live_state_evidence_invalid"}
    thread_id = str(payload.get("threadId") or payload.get("thread_id") or "").strip()
    status_value = payload.get("status")
    status = str(status_value.get("type") or "") if isinstance(status_value, dict) else str(status_value or "")
    if not thread_id:
        return None, {"thread_id": "", "reason": "thread_id_required"}
    if status.strip() != "active":
        return None, {"thread_id": thread_id, "reason": "status_not_active"}
    if payload.get("sameRepository") is not True:
        return None, {"thread_id": thread_id, "reason": "same_repository_evidence_required"}
    if payload.get("currentTask") is not False:
        return None, {"thread_id": thread_id, "reason": "current_task_or_missing_exclusion"}
    if payload.get("archived") is True:
        return None, {"thread_id": thread_id, "reason": "archived_target"}
    return {"thread_id": thread_id, "status": "active"}, None


def _eligible_active_thread_ids(values: list[str] | None) -> tuple[list[str], list[dict[str, str]]]:
    result: list[str] = []
    ineligible: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values or []:
        fact, issue = _thread_fact(value)
        if issue:
            ineligible.append(issue)
            continue
        thread_id = str(fact["thread_id"])
        if thread_id not in seen:
            result.append(thread_id)
            seen.add(thread_id)
    return result, ineligible


def coordination_target_admission(
    *,
    current_thread_id_value: str = "",
    discovery_threads: list[str] | None,
    revalidated_threads: list[str] | None = None,
    requested_target_thread: str = "",
) -> dict[str, Any]:
    """Reject a known current thread and require fresh target evidence."""

    own_thread = current_thread_id(current_thread_id_value)
    active, ineligible = _eligible_active_thread_ids(discovery_threads)
    revalidation_supplied = revalidated_threads is not None
    send_time_active, send_time_ineligible = _eligible_active_thread_ids(
        revalidated_threads if revalidation_supplied else discovery_threads
    )
    if revalidation_supplied:
        ineligible.extend(send_time_ineligible)

    target = str(requested_target_thread or "").strip()
    blockers: list[dict[str, Any]] = []
    if own_thread:
        active = [thread_id for thread_id in active if thread_id != own_thread]
        send_time_active = [thread_id for thread_id in send_time_active if thread_id != own_thread]
        if target == own_thread:
            blockers.append({"code": "handoff_target_is_current_thread", "target_thread": target})
    if not target and len(active) == 1:
        target = active[0]

    if target and target not in active:
        blockers.append({"code": "handoff_target_not_active_in_workspace", "target_thread": target})
    elif target and not revalidation_supplied:
        blockers.append({"code": "handoff_live_revalidation_required", "target_thread": target})
    elif target and target not in send_time_active:
        blockers.append({"code": "handoff_target_not_active_at_send", "target_thread": target})

    return {
        "schema": "workflow_closeout.coordination_target_admission.v1",
        "ok": not blockers,
        "current_thread_id": own_thread,
        "active_workspace_threads": active,
        "send_time_active_workspace_threads": send_time_active,
        "send_time_revalidation_supplied": revalidation_supplied,
        "ineligible_thread_facts": ineligible,
        "target_thread": target,
        "blockers": blockers,
    }
