"""Supplement runtime state transitions for the mobile bridge.

Owns: supplement payload release and validation routines that mutate bridge
supplement runtime state.
Non-goals: CLI parsing, MCP transport, final reply sending, or permission
policy decisions.
State behavior: mutates queue/runtime only through the injected queue object and
facade-provided helper callbacks.
Normal callers: `mobile_openclaw_cli.release_invalid_published_supplements` and
worker loop runtime through that facade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SupplementRuntimeDependencies:
    """External supplement helper callbacks used by runtime transitions."""

    bridge_supplement_ack_wait_expired: Any
    bridge_supplement_base_task_id: Any
    bridge_supplement_host_still_active_owner: Any
    bridge_supplement_payload_for_task: Any
    bridge_supplement_recently_completed_owner: Any
    mcp_ack_payload: Any
    task_event_payload_exists: Any
    task_is_promoted_supplement_owner: Any
    task_is_released_final_reply_owner: Any
    valid_active_supplement_host: Any


def release_invalid_published_supplements_impl(
    queue: Any,
    pending: list[dict[str, Any]],
    config: dict[str, Any] | None,
    deps: SupplementRuntimeDependencies,
) -> dict[str, Any]:
    """Preserve supplement identity while reporting payloads not ready for MCP."""
    released: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []
    for task in pending:
        task_id = str(task.get('id') or '')
        if not task_id:
            continue
        thread_id = str(task.get('codex_thread_id') or '')
        key, payload = deps.bridge_supplement_payload_for_task(queue, task_id, thread_id)
        if not key or not payload:
            continue
        if deps.mcp_ack_payload(queue, task_id):
            continue
        base_task_id = deps.bridge_supplement_base_task_id(payload)
        base_task = queue.get_task(base_task_id) if base_task_id else None
        base_status = str((base_task or {}).get('status') or '')
        base_is_released_owner = bool(base_task_id and base_task and (base_status in {'pending', 'queued_for_codex'}) and deps.task_is_released_final_reply_owner(queue, base_task_id))
        if base_is_released_owner:
            supplement_signature = str(payload.get('supplement_signature') or '')
            already_recorded = deps.task_event_payload_exists(queue, task_id, 'published_supplement_released_owner_preserved', lambda existing: str(existing.get('base_task_id') or '') == base_task_id and str(existing.get('thread_id') or '') == str(payload.get('thread_id') or '') and (str(existing.get('supplement_signature') or '') == supplement_signature))
            if not already_recorded:
                queue.add_event('local', 'published_supplement_released_owner_preserved', {'runtime_key': key, 'base_task_id': base_task_id, 'base_status': base_status, 'thread_id': str(payload.get('thread_id') or ''), 'published_at': str(payload.get('published_at') or ''), 'supplement_signature': supplement_signature, 'reason': 'base owner is temporarily pending for ordered redelivery; keep supplement available for MCP pickup after owner dispatch'}, task_id)
            continue
        if base_task_id and base_task and (base_status in {'pending', 'queued_for_codex'}) and deps.task_is_promoted_supplement_owner(queue, base_task_id):
            supplement_signature = str(payload.get('supplement_signature') or '')
            already_recorded = deps.task_event_payload_exists(queue, task_id, 'published_supplement_promoted_owner_preserved', lambda existing: str(existing.get('base_task_id') or '') == base_task_id and str(existing.get('thread_id') or '') == str(payload.get('thread_id') or '') and (str(existing.get('supplement_signature') or '') == supplement_signature))
            if not already_recorded:
                queue.add_event('local', 'published_supplement_promoted_owner_preserved', {'runtime_key': key, 'base_task_id': base_task_id, 'base_status': base_status, 'thread_id': str(payload.get('thread_id') or ''), 'published_at': str(payload.get('published_at') or ''), 'supplement_signature': supplement_signature, 'reason': 'base supplement was promoted to final-reply owner and is waiting for dispatch; keep later supplements attached behind it'}, task_id)
            continue
        if deps.bridge_supplement_ack_wait_expired(payload, config):
            if deps.bridge_supplement_host_still_active_owner(queue, base_task):
                supplement_signature = str(payload.get('supplement_signature') or '')
                already_recorded = deps.task_event_payload_exists(queue, task_id, 'published_supplement_ack_wait_extended', lambda existing: str(existing.get('base_task_id') or '') == base_task_id and str(existing.get('thread_id') or '') == str(payload.get('thread_id') or '') and (str(existing.get('supplement_signature') or '') == supplement_signature))
                if not already_recorded:
                    queue.add_event('local', 'published_supplement_ack_wait_extended', {'runtime_key': key, 'base_task_id': base_task_id, 'base_status': base_status, 'thread_id': str(payload.get('thread_id') or ''), 'published_at': str(payload.get('published_at') or ''), 'supplement_signature': supplement_signature, 'reason': 'base owner is still active; keep supplement available for Codex MCP pickup'}, task_id)
                continue
            supplement_signature = str(payload.get('supplement_signature') or '')
            already_recorded = deps.task_event_payload_exists(queue, task_id, 'published_supplement_ack_wait_orphaned', lambda existing: str(existing.get('base_task_id') or '') == base_task_id and str(existing.get('thread_id') or '') == str(payload.get('thread_id') or '') and (str(existing.get('supplement_signature') or '') == supplement_signature))
            record = {'task_id': task_id, 'runtime_key': key, 'base_task_id': base_task_id, 'base_status': base_status, 'thread_id': str(payload.get('thread_id') or ''), 'published_at': str(payload.get('published_at') or ''), 'supplement_signature': supplement_signature, 'reason': 'MCP ack grace expired but supplement identity is preserved; do not re-enter normal dispatch', 'policy': 'pending row remains supplement-context until acked, owner recovers, or maintenance explicitly resolves it'}
            if not already_recorded:
                queue.add_event('local', 'published_supplement_ack_wait_orphaned', record, task_id)
            preserved.append(record)
            continue
        if base_task_id and (not base_task):
            record = {'task_id': task_id, 'runtime_key': key, 'base_task_id': base_task_id, 'reason': 'base task is not in this queue view; preserve MCP recovery payload', 'policy': 'do not downgrade supplement context to ordinary pending dispatch'}
            already_recorded = deps.task_event_payload_exists(queue, task_id, 'published_supplement_missing_host_preserved', lambda existing: str(existing.get('base_task_id') or '') == base_task_id and str(existing.get('runtime_key') or '') == key)
            if not already_recorded:
                queue.add_event('local', 'published_supplement_missing_host_preserved', record, task_id)
            preserved.append(record)
            continue
        if deps.bridge_supplement_recently_completed_owner(queue, base_task, payload, config):
            supplement_signature = str(payload.get('supplement_signature') or '')
            already_recorded = deps.task_event_payload_exists(queue, task_id, 'published_supplement_completed_owner_preserved', lambda existing: str(existing.get('base_task_id') or '') == base_task_id and str(existing.get('thread_id') or '') == str(payload.get('thread_id') or '') and (str(existing.get('supplement_signature') or '') == supplement_signature))
            record = {'task_id': task_id, 'runtime_key': key, 'base_task_id': base_task_id, 'base_status': base_status, 'thread_id': str(payload.get('thread_id') or ''), 'published_at': str(payload.get('published_at') or ''), 'supplement_signature': supplement_signature, 'reason': 'base owner completed successfully but supplement ack grace window is still open; keep supplement available for late MCP pickup instead of releasing it back to normal dispatch'}
            if not already_recorded:
                queue.add_event('local', 'published_supplement_completed_owner_preserved', record, task_id)
            preserved.append(record)
            continue
        valid_host = bool(base_task and deps.valid_active_supplement_host(queue, base_task))
        if valid_host:
            continue
        supplement_signature = str(payload.get('supplement_signature') or '')
        already_recorded = deps.task_event_payload_exists(queue, task_id, 'published_supplement_invalid_host_preserved', lambda existing: str(existing.get('base_task_id') or '') == base_task_id and str(existing.get('thread_id') or '') == str(payload.get('thread_id') or '') and (str(existing.get('supplement_signature') or '') == supplement_signature))
        record = {'task_id': task_id, 'runtime_key': key, 'base_task_id': base_task_id, 'base_status': base_status, 'thread_id': str(payload.get('thread_id') or ''), 'published_at': str(payload.get('published_at') or ''), 'supplement_signature': supplement_signature, 'reason': 'published supplement host is not an active final-reply owner', 'policy': 'preserve supplement identity; maintenance must resolve orphaned context explicitly instead of normal redispatch'}
        if not already_recorded:
            queue.add_event('local', 'published_supplement_invalid_host_preserved', record, task_id)
        preserved.append(record)
    return {'ok': True, 'released': released, 'released_count': len(released), 'preserved': preserved, 'preserved_count': len(preserved)}
