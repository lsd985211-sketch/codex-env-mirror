"""Worker active-task recovery stages for the mobile bridge.

Owns: focused sub-stages of `recover_active_codex_tasks`, starting with
`queued_for_codex` task recovery and queued-only recovery result shaping.
Non-goals: sent/processing result polling, final reply delivery, permission
decisions, supplement ownership, app-server repair execution, or worker
dispatch selection.
State behavior: mutates queue rows and runtime markers only through injected
queue/callback APIs that were previously called directly by
`mobile_openclaw_cli.recover_active_codex_tasks`.
Normal callers: `mobile_openclaw_cli.recover_active_codex_tasks` facade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from cli_utils import parse_iso_datetime


def process_queued_active_recovery(
    queue: Any,
    config: dict[str, Any],
    *,
    queued: list[dict[str, Any]],
    sent: list[dict[str, Any]],
    now_dt: datetime,
    queued_recovery_after: int,
    delivery_mode_for_task: Callable[[dict[str, Any]], str],
    bridge_supplement_host_still_active_owner: Callable[[Any, dict[str, Any]], bool],
    task_route_identity: Callable[[dict[str, Any], str, str], tuple[str, str, str]],
    rehydrate_codex_turn_runtime_from_event: Callable[[Any, str], dict[str, Any]],
    provisional_codex_turn_runtime_from_unreadable_dispatch: Callable[[Any, dict[str, Any], str], dict[str, Any]],
    clear_delivery_retry: Callable[[Any, list[str]], None],
) -> dict[str, int]:
    """Recover stale queued tasks without touching sent-result polling."""

    active_owner_identities: set[tuple[str, str, str]] = set()
    for sent_task in sent:
        delivery_mode = delivery_mode_for_task(sent_task)
        thread_id = str(sent_task.get("codex_thread_id") or "")
        if bridge_supplement_host_still_active_owner(queue, sent_task):
            active_owner_identities.add(task_route_identity(sent_task, thread_id, delivery_mode))

    stale_queued: list[dict[str, Any]] = []
    for task in queued:
        queued_at = parse_iso_datetime(str(task.get("updated_at") or task.get("created_at") or ""))
        if queued_at and now_dt - queued_at < timedelta(seconds=queued_recovery_after):
            continue
        stale_queued.append(task)

    reverted = 0
    rehydrated_queued = 0
    for task in stale_queued:
        tid = str(task["id"])
        rehydrated = rehydrate_codex_turn_runtime_from_event(queue, tid)
        rehydrate_source = "codex_turn_started"
        if not rehydrated.get("ok"):
            task_delivery_mode = delivery_mode_for_task(task)
            task_thread_id = str(task.get("codex_thread_id") or "")
            if task_route_identity(task, task_thread_id, task_delivery_mode) in active_owner_identities:
                rehydrated = {
                    "ok": False,
                    "rehydrated": False,
                    "reason": "same route already has active final-reply owner",
                }
                queue.add_event(
                    "local",
                    "queued_materialized_rehydrate_deferred_same_route_active",
                    {
                        "thread_id": task_thread_id,
                        "delivery_mode": task_delivery_mode,
                        "policy": "do not promote unreadable-dispatch queued evidence while the same route already has an active final-reply owner",
                    },
                    tid,
                )
            else:
                rehydrated = provisional_codex_turn_runtime_from_unreadable_dispatch(queue, config, tid)
                rehydrate_source = "unreadable_dispatch"
        if rehydrated.get("ok"):
            with queue.session() as db:
                db.execute(
                    """
                    UPDATE mobile_tasks
                    SET status='sent_to_codex',
                        sent_to_codex_at=COALESCE(sent_to_codex_at, queued_for_codex_at, updated_at, ?),
                        updated_at=?
                    WHERE id=? AND status='queued_for_codex'
                    """,
                    (now_dt.isoformat(), now_dt.isoformat(), tid),
                )
            clear_delivery_retry(queue, [tid])
            event_type = (
                "recovery_queued_rehydrated_from_materialized_turn"
                if rehydrate_source == "unreadable_dispatch"
                else "recovery_queued_rehydrated_to_sent"
            )
            queue.add_event(
                "local",
                event_type,
                {
                    "rehydrated": rehydrated,
                    "policy": "preserve already-started Codex turn instead of redispatching queued task",
                    "source": rehydrate_source,
                },
                tid,
            )
            rehydrated_queued += 1
            continue
        with queue.session() as db:
            db.execute(
                "UPDATE mobile_tasks SET status='pending', updated_at=? WHERE id=? AND status='queued_for_codex'",
                (now_dt.isoformat(), tid),
            )
        queue.add_event("local", "recovery_queued_reverted_to_pending", {}, tid)
        reverted += 1

    return {
        "reverted": reverted,
        "queued_rehydrated": rehydrated_queued,
    }


def queued_only_recovery_result(
    queued: list[dict[str, Any]],
    *,
    reverted: int,
    queued_rehydrated: int,
    queued_recovery_after: int,
) -> dict[str, Any]:
    """Return the legacy queued-only recovery payload."""

    if reverted or queued_rehydrated:
        return {
            "ok": True,
            "action": "recovery_queued_processed",
            "recovered": 0,
            "reverted": reverted,
            "queued_rehydrated": queued_rehydrated,
        }
    return {
        "ok": True,
        "action": "queued_recent_waiting",
        "recovered": 0,
        "reverted": 0,
        "queued_rehydrated": 0,
        "queued_waiting": len(queued),
        "queued_recovery_after_seconds": queued_recovery_after,
    }


def task_has_durable_owned_ack_evidence(queue: Any, task_id: str) -> bool:
    """Return whether prior task-scoped recovery evidence saw an owned ack."""

    if not task_id:
        return False
    try:
        with queue.session() as db:
            row = db.execute(
                """
                SELECT 1
                FROM mobile_events
                WHERE task_id=?
                  AND event_type IN (
                    'active_poll_observation',
                    'active_waiting_followup_redelivery',
                    'recovery_waiting_followup_redelivery_skipped'
                  )
                  AND (
                    json_extract(payload_json, '$.ack_seen') = 1
                    OR json_extract(payload_json, '$.poll.ack_seen') = 1
                    OR json_extract(payload_json, '$.ownership.ack_seen') = 1
                  )
                ORDER BY id DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
    except Exception:
        row = None
    return row is not None


def waiting_followup_ack_only_terminal_result(
    queue: Any,
    config: dict[str, Any],
    task: dict[str, Any],
    poll: dict[str, Any],
    delivery_mode: str,
    expected_task_ids: list[str],
    deps: "ActiveRecoveryDependencies",
) -> dict[str, Any]:
    """Return a bounded recovery action for waiting-followup ack-only turns."""

    tid = str(task.get("id") or "")
    result_complete = bool(poll.get("result_complete"))
    ownership = poll.get("ownership") if isinstance(poll.get("ownership"), dict) else {}
    result_complete = result_complete or bool(ownership.get("result_complete"))
    poll_ack_seen = bool(deps.poll_has_mobile_ack(poll))
    durable_ack_seen = task_has_durable_owned_ack_evidence(queue, tid)
    owned_ack_seen = bool(poll_ack_seen or durable_ack_seen)
    waited_seconds = deps.waiting_followup_redelivery_age_seconds(queue, task, datetime.now(timezone.utc))
    stale_generation_after = max(
        900,
        int(config.get("trigger", {}).get("result_timeout_seconds") or 300) * 2,
        int(config.get("trigger", {}).get("visible_cdp_stale_generation_ack_after_seconds") or 0),
    )
    generation_active = bool(deps.poll_generation_is_active(poll))
    stale_generation_block = bool(
        durable_ack_seen
        and generation_active
        and waited_seconds >= stale_generation_after
        and not deps.poll_status_is_in_progress(poll)
        and not str(poll.get("newText") or "").strip()
    )
    no_observable_progress = bool(
        not result_complete
        and (not generation_active or stale_generation_block)
        and not deps.poll_status_is_in_progress(poll)
        and not str(poll.get("newText") or "").strip()
    )
    base_ack_only_terminal = bool(
        deps.poll_is_base_ack_only_terminal(poll)
        or (owned_ack_seen and no_observable_progress and bool(poll.get("terminal_without_text")))
    )
    ack_without_progress = bool(
        owned_ack_seen
        and no_observable_progress
    )
    if not tid or not (base_ack_only_terminal or ack_without_progress):
        return {"handled": False}
    if deps.task_event_exists(queue, tid, "pre_redelivery_base_ack_only_terminal"):
        failure = deps.fail_waiting_followup_redelivery_manual_required(
            queue,
            config,
            task,
            "base_ack_only_terminal_redelivery_already_attempted",
            {
                "poll": poll,
                "delivery_mode": delivery_mode,
                "expected_task_ids": expected_task_ids,
                "poll_ack_seen": poll_ack_seen,
                "durable_ack_seen": durable_ack_seen,
                "stale_generation_block": stale_generation_block,
                "waited_seconds": waited_seconds,
                "stale_generation_after_seconds": stale_generation_after,
                "policy": "fail closed after one controlled base ack/no-result redelivery attempt without owned mobile_result",
            },
        )
        return {"handled": True, "reverted_delta": 1, "failure": failure}
    queue.add_event(
        "local",
        "pre_redelivery_base_ack_only_terminal",
        {
            "poll": poll,
            "delivery_mode": delivery_mode,
            "expected_task_ids": expected_task_ids,
            "poll_ack_seen": poll_ack_seen,
            "durable_ack_seen": durable_ack_seen,
            "stale_generation_block": stale_generation_block,
            "waited_seconds": waited_seconds,
            "stale_generation_after_seconds": stale_generation_after,
            "policy": "owned ack without result and without observable progress means the model stopped after receipt; release once for controlled redelivery instead of waiting for another user message",
        },
        tid,
    )
    deps.clear_waiting_followup_redelivery_state(
        queue,
        tid,
        "base_ack_only_terminal_controlled_redelivery",
        {"poll": poll},
    )
    release = deps.release_active_task_to_pending(
        queue,
        config,
        task,
        "base_ack_only_terminal_without_result",
        {
            "poll": poll,
            "delivery_mode": delivery_mode,
            "expected_task_ids": expected_task_ids,
        },
    )
    queue.add_event(
        "local",
        "recovery_base_ack_only_terminal_requeued",
        {"poll": poll, "delivery_mode": delivery_mode, "expected_task_ids": expected_task_ids},
        tid,
    )
    return {"handled": True, "reverted_delta": 1, "release": release}


@dataclass(frozen=True)
class ActiveRecoveryDependencies:
    """External callbacks required by sent/processing active-task recovery."""

    _task_route_identity: Any
    active_route_lease_expired: Any
    active_slot_release_after_seconds: Any
    app_server_no_owned_result_manual_after_attempts: Any
    app_server_notfound_is_materializing: Any
    app_server_repair_continuation_after_seconds: Any
    bridge_supplement_host_still_active_owner: Any
    cancel_codex_generation: Any
    check_codex_health: Any
    clear_delivery_retry: Any
    clear_task_codex_runtime: Any
    clear_waiting_followup_redelivery_state: Any
    codex_turn_needs_retry: Any
    complete_delivery_group_member_from_finished_owner: Any
    complete_delivery_group_members: Any
    defer_app_server_inprogress_no_output_manual_review: Any
    delivery_group_member_ids: Any
    delivery_mode_for_task: Any
    fail_app_server_no_owned_result_manual_required: Any
    fail_waiting_followup_redelivery_manual_required: Any
    health_result_is_transient_probe_failure: Any
    mark_active_recovery_cooldown: Any
    mark_waiting_followup_redelivery: Any
    poll_codex_result: Any
    poll_generation_is_active: Any
    poll_has_mcp_transport_closed: Any
    poll_is_base_ack_only_terminal: Any
    poll_has_mobile_ack: Any
    poll_has_ownership_mismatch: Any
    poll_has_stalled_recoverable_tool: Any
    poll_in_progress_tools: Any
    poll_protocol_violation_reason: Any
    poll_status_is_in_progress: Any
    poll_turn_was_superseded: Any
    provisional_codex_turn_runtime_from_unreadable_dispatch: Any
    push_final_reply_async: Any
    record_active_poll_observation: Any
    record_unowned_intermediate_result: Any
    recover_owned_result_from_history_sources: Any
    rehydrate_codex_turn_runtime_from_event: Any
    release_active_task_to_pending: Any
    reserve_owned_result_consume: Any
    restart_codex_app_server_for_mcp: Any
    select_active_recovery_tasks: Any
    send_status_ack: Any
    start_app_server_repair_continuation: Any
    task_ack_code_runtime: Any
    task_batch_runtime: Any
    task_delivery_config: Any
    task_event_exists: Any
    task_event_recent: Any
    task_has_attachments: Any
    task_is_supplement_context: Any
    task_is_waiting_followup_redelivery: Any
    task_owns_final_reply: Any
    task_result_code_runtime: Any
    task_turn_key: Any
    task_waits_for_followup_redelivery: Any
    visible_cdp_no_owned_result_manual_after_seconds: Any
    waiting_followup_redelivery_age_seconds: Any


def recover_active_codex_tasks_impl(queue: Any, config: dict[str, Any], max_sent_checks: int | None, deps: ActiveRecoveryDependencies=None) -> dict[str, Any]:
    """Recover active Codex delivery tasks abandoned by a crashed/restarted worker.

    The bridge no longer closes tasks by wall-clock timeout. Recovery is based
    on current state: queued tasks that were never sent are returned to pending;
    sent/processing tasks are either recovered from a final Codex result or
    returned to pending when Codex/CDP is unhealthy.
    """
    recovery_cfg = config.get('trigger', {})
    if max_sent_checks is None:
        max_sent_checks = int(recovery_cfg.get('active_recovery_max_sent_checks_per_cycle') or 5)
    max_sent_checks = max(0, int(max_sent_checks))
    active = queue.list_active_codex_delivery_tasks(limit=100)
    if not active:
        return {'ok': True, 'action': 'no_active_tasks', 'recovered': 0, 'reverted': 0}
    queued = [task for task in active if str(task.get('status') or '') == 'queued_for_codex']
    sent = [task for task in active if str(task.get('status') or '') in {'sent_to_codex', 'processing'}]
    now_dt = datetime.now(timezone.utc)
    queued_recovery_after = max(30, int(config.get('trigger', {}).get('queued_recovery_after_seconds') or 120))
    active_release_after = deps.active_slot_release_after_seconds(config)
    queued_recovery = process_queued_active_recovery(queue, config, queued=queued, sent=sent, now_dt=now_dt, queued_recovery_after=queued_recovery_after, delivery_mode_for_task=lambda task: deps.delivery_mode_for_task(config, task), bridge_supplement_host_still_active_owner=deps.bridge_supplement_host_still_active_owner, task_route_identity=deps._task_route_identity, rehydrate_codex_turn_runtime_from_event=deps.rehydrate_codex_turn_runtime_from_event, provisional_codex_turn_runtime_from_unreadable_dispatch=deps.provisional_codex_turn_runtime_from_unreadable_dispatch, clear_delivery_retry=deps.clear_delivery_retry)
    reverted = int(queued_recovery.get('reverted') or 0)
    rehydrated_queued = int(queued_recovery.get('queued_rehydrated') or 0)
    if not sent:
        return queued_only_recovery_result(queued, reverted=reverted, queued_rehydrated=rehydrated_queued, queued_recovery_after=queued_recovery_after)
    recovered = 0
    checked_sent = 0
    lease_released = 0
    sent_to_check, recovery_deferred_by_cooldown = deps.select_active_recovery_tasks(queue, config, sent, max_sent_checks, now_dt)
    for task in sent_to_check:
        checked_sent += 1
        tid = str(task['id'])
        current_task = queue.get_task(tid)
        current_status = str((current_task or {}).get('status') or '')
        if not current_task or current_status not in {'sent_to_codex', 'processing'}:
            if not deps.task_event_recent(queue, tid, 'recovery_stale_active_snapshot_skipped', 60):
                queue.add_event('local', 'recovery_stale_active_snapshot_skipped', {'snapshot_status': str(task.get('status') or ''), 'current_status': current_status, 'reason': 'active recovery must use current task state, not the stale cycle snapshot'}, tid)
            continue
        task = current_task
        waiting_followup_redelivery = deps.task_is_waiting_followup_redelivery(queue, tid)
        delivery_mode = deps.delivery_mode_for_task(config, task)
        poll_config = deps.task_delivery_config(config, delivery_mode)
        if deps.task_is_supplement_context(queue, tid) and (not deps.task_owns_final_reply(queue, tid)):
            client_message_id, _expected_task_ids = deps.task_batch_runtime(queue, tid, [])
            completed_from_owner = deps.complete_delivery_group_member_from_finished_owner(queue, tid, str(task.get('codex_thread_id') or ''))
            if completed_from_owner:
                recovered += len(completed_from_owner)
                continue
            queue.add_event('local', 'delivery_group_member_result_poll_skipped', {'client_message_id': client_message_id, 'reason': 'delivery group member does not own final reply; skip recovery poll even if runtime can be rehydrated'}, tid)
            continue
        if deps.active_route_lease_expired(task, config, now_dt):
            if not deps.task_event_recent(queue, tid, 'recovery_active_route_lease_released', 60):
                queue.add_event('local', 'recovery_active_route_lease_released', {'sent_to_codex_at': str(task.get('sent_to_codex_at') or ''), 'lease_seconds': deps.active_slot_release_after_seconds(config), 'status': str(task.get('status') or ''), 'continues_result_poll': True}, tid)
            lease_released += 1
        health_result = deps.check_codex_health(poll_config)
        if not health_result.get('healthy'):
            if deps.health_result_is_transient_probe_failure(health_result):
                queue.add_event('local', 'recovery_transient_probe_failure_waiting', {'health': health_result}, tid)
                deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'transient_probe_failure')
                continue
            silence_key = 'silence:' + str(task.get('external_user') or '') + ':' + tid
            already_silenced = bool(queue.runtime_get(silence_key))
            deps.release_active_task_to_pending(queue, config, task, 'codex_health_unhealthy', {'health': health_result})
            queue.add_event('local', 'recovery_reverted_to_pending', {'health': health_result}, tid)
            if not already_silenced:
                queue.runtime_set(silence_key, '1')
                deps.send_status_ack(queue, task, '⚠️ 处理中断，已重新排队', config, 'status_ack_requeued')
            reverted += 1
            continue
        turn_id = str(queue.runtime_get(deps.task_turn_key(tid)) or '')
        client_message_id, expected_task_ids = deps.task_batch_runtime(queue, tid, [tid])
        if expected_task_ids == [] and deps.task_is_supplement_context(queue, tid):
            completed_from_owner = deps.complete_delivery_group_member_from_finished_owner(queue, tid, str(task.get('codex_thread_id') or ''))
            if completed_from_owner:
                recovered += len(completed_from_owner)
                continue
            queue.add_event('local', 'delivery_group_member_result_poll_skipped', {'client_message_id': client_message_id, 'reason': 'delivery group member does not own final reply; keep runtime until owner completes'}, tid)
            continue
        if not turn_id:
            rehydrated = deps.rehydrate_codex_turn_runtime_from_event(queue, tid)
            if rehydrated.get('ok'):
                turn_id = str(rehydrated.get('turn_id') or '')
                client_message_id, expected_task_ids = deps.task_batch_runtime(queue, tid, [tid])
                queue.add_event('local', 'recovery_missing_turn_runtime_rehydrated', {'client_message_id': client_message_id, 'expected_task_ids': expected_task_ids, 'rehydrated': rehydrated}, tid)
            else:
                deps.release_active_task_to_pending(queue, config, task, 'missing_codex_turn_runtime', {'client_message_id': client_message_id, 'expected_task_ids': expected_task_ids, 'rehydrate': rehydrated})
                queue.add_event('local', 'recovery_missing_turn_runtime_reverted', {'rehydrate': rehydrated}, tid)
                reverted += 1
                continue
        expected_result_codes = deps.task_result_code_runtime(queue, expected_task_ids)
        expected_ack_codes = deps.task_ack_code_runtime(queue, expected_task_ids)
        poll = deps.poll_codex_result(poll_config, str(task.get('codex_thread_id') or ''), turn_id, '', client_message_id, expected_task_ids, expected_result_codes, expected_ack_codes)
        sent_at_for_observation = parse_iso_datetime(str(task.get('sent_to_codex_at') or task.get('updated_at') or ''))
        waited_seconds_for_observation = int((now_dt - sent_at_for_observation).total_seconds()) if sent_at_for_observation else 0
        waiting_ack_after_for_observation = max(1, int(config.get('trigger', {}).get('waiting_ack_after_seconds') or 60))
        continuation_after_for_observation = deps.app_server_repair_continuation_after_seconds(config)
        active_poll_observation = deps.record_active_poll_observation(queue, tid, poll, waited_seconds_for_observation, delivery_mode=delivery_mode, waiting_ack_after_seconds=waiting_ack_after_for_observation, continuation_after_seconds=continuation_after_for_observation)
        if bool(poll.get('second_chance')) and (not deps.task_event_recent(queue, tid, 'app_server_result_poll_second_chance', 60)):
            queue.add_event('local', 'app_server_result_poll_second_chance', {'delivery_mode': delivery_mode, 'second_chance_timeout_seconds': poll.get('second_chance_timeout_seconds'), 'first_attempt': poll.get('first_attempt'), 'second_ok': bool(poll.get('ok')), 'result_complete': bool(poll.get('result_complete')), 'status': str(poll.get('status') or ''), 'active_poll_observation': active_poll_observation}, tid)
        original_poll = poll
        poll, new_text, owned_complete = deps.recover_owned_result_from_history_sources(queue, config, poll_config, tid, str(task.get('codex_thread_id') or ''), turn_id, client_message_id, expected_task_ids, expected_result_codes, expected_ack_codes, poll)
        if new_text and poll is not original_poll:
            event_type = 'session_store_owned_result_recovered' if bool(poll.get('session_store_recovery')) else ('thread_history_owned_result_recovered' if bool(poll.get('thread_history_fallback')) else 'historical_owned_result_recovered')
            queue.add_event('local', event_type, {'current_turn_id': turn_id, 'current_client_message_id': client_message_id, 'current_expected_task_ids': expected_task_ids, 'current_expected_result_codes': expected_result_codes, 'historical_attempt': poll.get('historical_attempt') or {}, 'original_poll_status': str(original_poll.get('status') or ''), 'thread_history_fallback': bool(poll.get('thread_history_fallback')), 'policy': 'same task may finish in durable Codex history even when the visible poll source has no owned text; complete once and ignore later duplicates'}, tid)
        if bool(poll.get('session_store_recovery_blocked')):
            if not deps.task_event_recent(queue, tid, 'session_store_owned_result_recovery_blocked', 900):
                queue.add_event('local', 'session_store_owned_result_recovery_blocked', {'recovery': poll.get('session_store_recovery') or {}, 'policy': 'conflicting exact owned results fail closed; do not redeliver or send until reviewed'}, tid)
            deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'session_store_owned_result_recovery_blocked')
            continue
        if waiting_followup_redelivery and (not new_text):
            ack_only_terminal = waiting_followup_ack_only_terminal_result(
                queue,
                config,
                task,
                poll,
                delivery_mode,
                expected_task_ids,
                deps,
            )
            if ack_only_terminal.get('handled'):
                reverted += int(ack_only_terminal.get('reverted_delta') or 0)
                continue
            if not deps.task_event_recent(queue, tid, 'recovery_waiting_followup_redelivery_skipped', 900):
                queue.add_event('local', 'recovery_waiting_followup_redelivery_skipped', {'status': current_status, 'poll_status': str(poll.get('status') or ''), 'ack_seen': bool(deps.poll_has_mobile_ack(poll)), 'result_complete': bool(owned_complete), 'policy': 'waiting-followup blocks redelivery and side effects only; owned-result recovery was checked first'}, tid)
            deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'waiting_followup_redelivery')
            continue
        if deps.poll_has_mcp_transport_closed(poll):
            restart_result: dict[str, Any] = {'skipped': True, 'reason': 'session_tool_surface_drift', 'health': health_result}
            if delivery_mode == 'codex-app-server' and (not bool(health_result.get('healthy'))):
                restart_result = deps.restart_codex_app_server_for_mcp(poll_config, 'mcp_transport_closed')
            deps.mark_waiting_followup_redelivery(queue, task, 'session_tool_surface_drift', {'poll': poll, 'restart_result': restart_result, 'policy': 'treat transport_closed as session/tool-surface drift; park the task until a fresh follow-up/new turn rebinds the tool surface instead of requeueing the same stale turn'})
            deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'session_tool_surface_drift')
            queue.add_event('local', 'recovery_mcp_transport_closed_waiting_followup', {'poll': poll, 'restart_result': restart_result, 'delivery_mode': delivery_mode, 'health': health_result}, tid)
            reverted += 1
            continue
        if deps.poll_turn_was_superseded(poll):
            deps.cancel_codex_generation(poll_config, str(task.get('codex_thread_id') or ''), str(queue.runtime_get(deps.task_turn_key(tid)) or ''))
            with queue.session() as db:
                db.execute("UPDATE mobile_tasks SET status='pending', updated_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), tid))
            deps.clear_task_codex_runtime(queue, tid)
            queue.add_event('local', 'recovery_superseded_turn_reverted', {'poll': poll}, tid)
            reverted += 1
            continue
        if new_text:
            current_task = queue.get_task(tid) or {}
            if str(current_task.get('status') or '') == 'done' and str(current_task.get('result') or '') == str(new_text or ''):
                queue.add_event('local', 'owned_result_duplicate_suppressed', {'reason': 'task_already_done_with_same_result', 'poll': poll}, tid)
                recovered += 1
                continue
            result_reservation = deps.reserve_owned_result_consume(queue, tid, poll)
            if not result_reservation.get('reserved'):
                queue.add_event('local', 'owned_result_duplicate_suppressed', {'reason': 'owned_result_already_being_consumed', 'lease': result_reservation, 'poll': poll}, tid)
                recovered += 1
                continue
            queue.complete(tid, new_text, status='done')
            silence_key = 'silence:' + str(task.get('external_user') or '') + ':' + tid
            queue.runtime_delete(silence_key)
            deps.clear_waiting_followup_redelivery_state(queue, tid, 'owned_result_recovered', {'poll': poll})
            completed_members = deps.complete_delivery_group_members(queue, tid, deps.delivery_group_member_ids(queue, tid), new_text, str(task.get('codex_thread_id') or ''))
            deps.clear_task_codex_runtime(queue, tid)
            reply = deps.push_final_reply_async(queue, task, new_text, config)
            queue.add_event('local', 'recovery_result_pushed', {'poll': poll, 'completed_group_members': completed_members}, tid)
            recovered += 1
        else:
            protocol_failure_reason = deps.poll_protocol_violation_reason(poll, expected_task_ids, expected_result_codes)
            terminal_failure_reason = protocol_failure_reason or ('terminal_without_owned_result' if deps.codex_turn_needs_retry(poll) or bool(poll.get('terminal_without_text')) else '')
            if terminal_failure_reason:
                if delivery_mode == 'codex-app-server':
                    materializing, materializing_detail = deps.app_server_notfound_is_materializing(queue, config, task, poll, now_dt)
                    if materializing:
                        queue.add_event('local', 'app_server_turn_materialization_waiting', {'poll': poll, **materializing_detail, 'turn_id': turn_id, 'client_message_id': client_message_id, 'expected_task_ids': expected_task_ids, 'policy': 'turn/start returned an id but app-server has not exposed it in turns/list yet; keep active briefly instead of redelivering'}, tid)
                        deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'app_server_turn_materialization_waiting')
                        continue
                if deps.task_has_attachments(task):
                    now = datetime.now(timezone.utc).isoformat()
                    error = 'Codex turn ended without an owned result for an attachment task; automatic redelivery is disabled to avoid duplicate attachment prompts. Use dashboard manual retry after inspection.'
                    with queue.session() as db:
                        db.execute("\n                            UPDATE mobile_tasks\n                            SET status='failed', error=?, updated_at=?, completed_at=?\n                            WHERE id=? AND status IN ('sent_to_codex', 'processing')\n                            ", (error, now, now, tid))
                    deps.clear_task_codex_runtime(queue, tid)
                    queue.add_event('local', 'attachment_terminal_without_result_manual_retry_required', {'poll': poll, 'policy': 'no_automatic_redelivery_for_attachment_tasks', 'reason': terminal_failure_reason}, tid)
                    reverted += 1
                    continue
                if terminal_failure_reason == 'protocol_violation_no_owned_result':
                    queue.add_event('local', 'recovery_protocol_violation_no_owned_result', {'poll': poll, 'delivery_mode': delivery_mode, 'expected_task_ids': expected_task_ids, 'policy': 'Codex turn reached a terminal state without the owned mobile_result boundary; recover through the existing bounded redelivery path, not as normal thinking'}, tid)
                    if delivery_mode == 'codex-app-server' and str(poll.get('status') or '').strip().lower() != 'notfound':
                        continuation = deps.start_app_server_repair_continuation(queue, config, task, terminal_failure_reason, poll, turn_id, client_message_id, expected_task_ids, expected_result_codes, expected_ack_codes)
                        if continuation.get('continued'):
                            continue
                        max_attempts = deps.app_server_no_owned_result_manual_after_attempts(config)
                        attempts = int(task.get('trigger_attempts') or 0)
                        if attempts >= max_attempts:
                            deps.fail_app_server_no_owned_result_manual_required(queue, config, task, terminal_failure_reason, {'trigger_attempts': attempts, 'manual_after_attempts': max_attempts, 'poll': poll, 'delivery_mode': delivery_mode, 'expected_task_ids': expected_task_ids})
                            reverted += 1
                            continue
                    if deps.task_waits_for_followup_redelivery(config, task):
                        ack_only_terminal = waiting_followup_ack_only_terminal_result(queue, config, task, poll, delivery_mode, expected_task_ids, deps)
                        if ack_only_terminal.get('handled'):
                            reverted += int(ack_only_terminal.get('reverted_delta') or 0)
                            continue
                deps.release_active_task_to_pending(queue, config, task, terminal_failure_reason, {'poll': poll}) if not deps.task_waits_for_followup_redelivery(config, task) else None
                if deps.task_waits_for_followup_redelivery(config, task):
                    if terminal_failure_reason == 'protocol_violation_no_owned_result':
                        waited_seconds = deps.waiting_followup_redelivery_age_seconds(queue, task, now_dt)
                        manual_after = deps.visible_cdp_no_owned_result_manual_after_seconds(config)
                        if waited_seconds >= manual_after:
                            deps.fail_waiting_followup_redelivery_manual_required(queue, config, task, terminal_failure_reason, {'waited_seconds': waited_seconds, 'manual_after_seconds': manual_after, 'poll': poll, 'delivery_mode': delivery_mode, 'expected_task_ids': expected_task_ids})
                            reverted += 1
                            continue
                    deps.mark_waiting_followup_redelivery(queue, task, terminal_failure_reason, {'waited_seconds': deps.waiting_followup_redelivery_age_seconds(queue, task, now_dt), 'manual_after_seconds': deps.visible_cdp_no_owned_result_manual_after_seconds(config), 'poll': poll, 'policy': 'primary visible CDP task waits for a new same-thread message before retrying delivery'})
                    deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'waiting_followup_redelivery')
                    continue
                queue.add_event('local', 'recovery_terminal_without_result_reverted', {'poll': poll, 'reason': terminal_failure_reason}, tid)
                reverted += 1
                continue
            waiting_ack_after = max(1, int(config.get('trigger', {}).get('waiting_ack_after_seconds') or 60))
            sent_at = parse_iso_datetime(str(task.get('sent_to_codex_at') or task.get('updated_at') or ''))
            waited_seconds = int((now_dt - sent_at).total_seconds()) if sent_at else 0
            stalled_recoverable, stalled_detail = deps.poll_has_stalled_recoverable_tool(poll, config, waited_seconds)
            if stalled_recoverable:
                cancel_result = deps.cancel_codex_generation(poll_config, str(task.get('codex_thread_id') or ''), turn_id)
                if bool(cancel_result.get('ok')) or bool(cancel_result.get('cancelled')):
                    deps.release_active_task_to_pending(queue, config, task, 'stalled_tool_call_without_owned_result', {'poll': poll, 'stalled_tool': stalled_detail, 'cancel_result': cancel_result, 'policy': 'cancel stale app-server turn before FIFO redelivery'})
                    queue.add_event('local', 'recovery_stalled_tool_requeued', {'poll': poll, 'stalled_tool': stalled_detail, 'cancel_result': cancel_result, 'delivery_mode': delivery_mode}, tid)
                    reverted += 1
                    continue
                queue.add_event('local', 'recovery_stalled_tool_cancel_failed', {'poll': poll, 'stalled_tool': stalled_detail, 'cancel_result': cancel_result, 'policy': 'keep observing to avoid duplicate active turns'}, tid)
                deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'stalled_tool_cancel_failed')
                continue
            if deps.poll_has_mobile_ack(poll) and deps.poll_status_is_in_progress(poll):
                empty_spin_after = deps.app_server_repair_continuation_after_seconds(config)
                if delivery_mode == 'codex-app-server' and waited_seconds >= empty_spin_after and (not str(poll.get('newText') or '').strip()) and (not bool(poll.get('result_complete'))) and (not deps.poll_in_progress_tools(poll)):
                    continuation = deps.start_app_server_repair_continuation(queue, config, task, 'ack_seen_inprogress_no_progress', poll, turn_id, client_message_id, expected_task_ids, expected_result_codes, expected_ack_codes)
                    if continuation.get('continued'):
                        continue
                    if str(continuation.get('reason') or '') in {'repair_continuation_already_attempted', 'continuation_dispatch_failed', 'attachment_task_requires_manual_recovery'}:
                        deps.defer_app_server_inprogress_no_output_manual_review(queue, config, task, 'ack_seen_inprogress_no_progress', {'waited_seconds': waited_seconds, 'repair_continuation_after_seconds': empty_spin_after, 'poll': poll, 'continuation': continuation, 'policy': 'turn is still inProgress; do not fail-close running work or retry side effects'})
                        reverted += 1
                        continue
                    queue.add_event('local', 'app_server_repair_continuation_deferred', {'reason': 'ack_seen_inprogress_no_progress', 'waited_seconds': waited_seconds, 'repair_continuation_after_seconds': empty_spin_after, 'continuation': continuation, 'policy': 'old turn could not be safely interrupted; keep observing to avoid duplicate turns'}, tid)
                    deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'repair_continuation_deferred')
                    continue
                if waited_seconds >= waiting_ack_after and (not deps.task_event_exists(queue, tid, 'status_ack_waiting')):
                    deps.send_status_ack(queue, task, f'🔄 已进入 Codex 处理，已等待 {waited_seconds} 秒…', config, 'status_ack_waiting')
                deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'ack_seen_waiting_for_owned_result')
                continue
            if deps.poll_has_ownership_mismatch(poll):
                deps.record_unowned_intermediate_result(queue, tid, poll)
                deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'ownership_mismatch')
                continue
            if waited_seconds >= active_release_after:
                if deps.poll_generation_is_active(poll):
                    followup_hold_after = max(active_release_after, int(config.get('trigger', {}).get('visible_cdp_followup_hold_after_seconds') or 180))
                    if deps.task_waits_for_followup_redelivery(config, task) and waited_seconds >= followup_hold_after and (not deps.poll_has_mobile_ack(poll)):
                        deps.mark_waiting_followup_redelivery(queue, task, 'generation_active_without_owned_result', {'waited_seconds': waited_seconds, 'active_release_after_seconds': active_release_after, 'followup_hold_after_seconds': followup_hold_after, 'poll': poll, 'policy': 'stop polling a long-running visible-CDP turn with no owned mobile ack; wait for same-thread follow-up before retrying delivery'})
                        deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'waiting_followup_redelivery')
                        continue
                    if not deps.task_event_recent(queue, tid, 'active_slot_release_deferred_generation_active', 60):
                        queue.add_event('local', 'active_slot_release_deferred_generation_active', {'waited_seconds': waited_seconds, 'active_release_after_seconds': active_release_after, 'poll': poll, 'policy': 'keep observing active Codex generation; do not redeliver the same task or invalidate supplements while the turn is still progressing'}, tid)
                    deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'generation_active_waiting_for_owned_result')
                    continue
                deps.release_active_task_to_pending(queue, config, task, 'active_lease_expired_without_owned_result', {'waited_seconds': waited_seconds, 'active_release_after_seconds': active_release_after, 'poll': poll, 'policy': 'requeue_expired_active_before_later_same_route_tasks'}) if not deps.task_waits_for_followup_redelivery(config, task) else None
                if deps.task_waits_for_followup_redelivery(config, task):
                    deps.mark_waiting_followup_redelivery(queue, task, 'active_lease_expired_without_owned_result', {'waited_seconds': waited_seconds, 'active_release_after_seconds': active_release_after, 'poll': poll, 'policy': 'primary visible CDP task waits for a new same-thread message before retrying delivery'})
                    deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'waiting_followup_redelivery')
                    continue
                queue.add_event('local', 'active_slot_requeued_for_ordered_redelivery', {'waited_seconds': waited_seconds, 'active_release_after_seconds': active_release_after, 'poll': poll, 'policy': 'preserve same-route FIFO by retrying expired active before newer pending messages'}, tid)
                reverted += 1
                continue
            if waited_seconds >= waiting_ack_after and (not deps.task_event_exists(queue, tid, 'status_ack_waiting')):
                deps.send_status_ack(queue, task, f'🔄 仍在处理，已等待 {waited_seconds} 秒…', config, 'status_ack_waiting')
            deps.mark_active_recovery_cooldown(queue, config, tid, now_dt, 'waiting_for_owned_result')
    return {'ok': True, 'action': 'recovery_complete', 'recovered': recovered, 'reverted': reverted, 'queued_rehydrated': rehydrated_queued, 'lease_released': lease_released, 'checked_sent': checked_sent, 'sent_active': len(sent), 'still_waiting': max(0, len(sent) - recovered - reverted), 'skipped_sent_checks': max(0, len(sent) - checked_sent), 'deferred_by_recovery_cooldown': recovery_deferred_by_cooldown, 'active_slot_release_after_seconds': active_release_after}

