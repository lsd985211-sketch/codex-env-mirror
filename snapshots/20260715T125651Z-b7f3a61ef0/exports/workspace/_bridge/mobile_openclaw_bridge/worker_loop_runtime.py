"""Worker loop runtime implementation for the mobile bridge.

Owns: the implementation body of `mobile_openclaw_cli.worker_once` and its
runtime dispatch/recovery orchestration.
Non-goals: CLI argument registration, command parsing, permission-policy
definitions, or direct ownership of helper state machines that already live in
purpose-specific modules.
State behavior: mutates queue state only through the injected queue and
callbacks assembled by the `mobile_openclaw_cli.worker_once` facade.
Normal callers: `mobile_openclaw_cli.worker_once`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerLoopDependencies:
    """External callbacks and constants used by worker_once_impl."""

    DEFAULT_CONFIG: Any
    Path: Any
    STOP_REQUEST: Any
    active_route_lease_expired: Any
    add_coalesced_event: Any
    attachment_supplement_signature_key: Any
    attachment_task_ids: Any
    auto_create_thread_route_for_user: Any
    bridge_supplement_base_task_id: Any
    bridge_supplement_key: Any
    bridge_supplement_payload_for_task: Any
    cdp_delivery_lacks_submission_evidence: Any
    clear_delivery_retry: Any
    clear_pending_backlog_supplement_if_matches: Any
    clear_thread_recovery: Any
    clear_waiting_followup_redelivery_state: Any
    codex_thread_dispatch_state: Any
    codex_thread_is_busy: Any
    codex_thread_is_unavailable: Any
    codex_thread_needs_background_prewarm: Any
    current_mcp_session_gate_for_dispatch: Any
    datetime: Any
    default_thread_id: Any
    defer_continuation_for_busy_route: Any
    delivery_group_split: Any
    delivery_group_task_ids: Any
    delivery_mode_for_task: Any
    delivery_retry_reason_allows_batch: Any
    dispatch_to_codex: Any
    effective_task_thread_id: Any
    enforce_ask_scope_for_task: Any
    enforce_worker_dispatch_permission: Any
    find_thread_for_external_user: Any
    find_waiting_followup_redelivery_active: Any
    get_active_thread: Any
    get_cdp_start_probe_cooldown: Any
    get_continuation_context: Any
    get_delivery_retry: Any
    get_thread_prewarm: Any
    include_released_active_pending_tasks: Any
    inspect_codex_thread_for_dispatch: Any
    json: Any
    latest_followup_trigger_owner: Any
    latest_task_event_payload: Any
    mark_cdp_start_probe_cooldown: Any
    mark_delivery_retry: Any
    mark_thread_prewarm: Any
    mark_thread_recovery: Any
    maybe_repair_app_server_unreadable_thread: Any
    maybe_sync_openclaw_account_onboarding: Any
    mcp_ack_payload: Any
    next_dispatchable_route_task_id: Any
    onboarding_created_text: Any
    onboarding_hold_key: Any
    onboarding_needed_text: Any
    pending_route_batch_tasks: Any
    pending_task_has_unacked_bridge_supplement: Any
    pending_task_is_published_bridge_supplement: Any
    poll_codex_result_cdp: Any
    process_mcp_acked_pending_supplements: Any
    process_pending_reply_context_retries: Any
    promote_orphaned_bridge_supplements: Any
    publish_attachment_active_supplements: Any
    publish_attachment_supplement_for_active: Any
    publish_pending_backlog_supplement_for_owner: Any
    reconcile_completed_replies_waiting_push: Any
    recover_active_codex_tasks: Any
    recover_pending_visible_cdp_unconfirmed_results: Any
    recover_stale_reply_sending_tasks: Any
    reject_task_for_permission: Any
    release_active_task_to_pending: Any
    release_invalid_published_supplements: Any
    release_queued_tasks_for_active_owner_supplement: Any
    resolved_visible_cdp_thread_id: Any
    revert_tasks_to_pending: Any
    same_followup_owner_route: Any
    send_status_ack: Any
    set_active_thread: Any
    sort_pending_by_route_fairness: Any
    start_thread_prewarm_background: Any
    sync_openclaw_accounts_to_bridge_users: Any
    task_ack_code_key: Any
    task_batch_key: Any
    task_can_be_same_turn_supplement: Any
    task_delivery_config: Any
    task_event_exists: Any
    task_event_payload_exists: Any
    task_event_recent: Any
    task_expected_ids_key: Any
    task_is_supplement_context: Any
    task_prompt: Any
    task_result_code_key: Any
    task_route_key: Any
    task_turn_key: Any
    timezone: Any
    try_complete_owned_result_before_redelivery: Any
    valid_active_supplement_host: Any
    visible_cdp_unverified_submission_attention_after_attempts: Any
    worker_once: Any


def worker_once_impl(queue: Any, config: dict[str, Any], limit: int, deps: WorkerLoopDependencies, task_id: str='', fallback_depth: int=0) -> dict[str, Any]:
    if deps.STOP_REQUEST.exists():
        return {'ok': True, 'action': 'stop_requested', 'reason': 'STOP_REQUEST exists; worker will not dispatch mobile tasks', 'processed': 0, 'stop_request': str(deps.STOP_REQUEST)}
    health = queue.health()
    if health.get('paused'):
        return {'ok': True, 'action': 'paused', 'processed': 0}
    recovery = deps.recover_active_codex_tasks(queue, config)
    recovery_had_work = recovery.get('recovered', 0) > 0 or recovery.get('reverted', 0) > 0
    reply_sending_recovery = deps.recover_stale_reply_sending_tasks(queue)
    account_onboarding_sync_result = deps.maybe_sync_openclaw_account_onboarding(queue, config)
    account_sync = deps.sync_openclaw_accounts_to_bridge_users(queue, config, require_thread_route=True)
    reply_reconcile = deps.reconcile_completed_replies_waiting_push(queue, config)
    mcp_acknowledged_supplements = deps.process_mcp_acked_pending_supplements(queue)
    pending_reply_retries = deps.process_pending_reply_context_retries(queue, config)
    queued_supplement_release = deps.release_queued_tasks_for_active_owner_supplement(queue, config)
    orphaned_supplement_promotion = deps.promote_orphaned_bridge_supplements(queue, config)
    pending_reply_had_work = pending_reply_retries.get('scheduled', 0) > 0
    pending_scan_limit = max(limit, 20)
    pending = queue.list_pending(pending_scan_limit, preferred_task_id=task_id)
    if not task_id:
        pending = deps.include_released_active_pending_tasks(queue, pending, pending_scan_limit)
    pending = deps.sort_pending_by_route_fairness(queue, config, pending, task_id=task_id)
    invalid_supplement_release = deps.release_invalid_published_supplements(queue, pending, config)
    if invalid_supplement_release.get('released_count', 0) > 0:
        pending = queue.list_pending(pending_scan_limit, preferred_task_id=task_id)
        if not task_id:
            pending = deps.include_released_active_pending_tasks(queue, pending, pending_scan_limit)
        pending = deps.sort_pending_by_route_fairness(queue, config, pending, task_id=task_id)
    pending_visible_cdp_recovery = deps.recover_pending_visible_cdp_unconfirmed_results(queue, config, pending)
    if pending_visible_cdp_recovery.get('recovered_count', 0) > 0:
        pending = queue.list_pending(pending_scan_limit, preferred_task_id=task_id)
        if not task_id:
            pending = deps.include_released_active_pending_tasks(queue, pending, pending_scan_limit)
        pending = deps.sort_pending_by_route_fairness(queue, config, pending, task_id=task_id)
    if not pending:
        action = 'recovery_cycle' if recovery_had_work or pending_reply_had_work or reply_sending_recovery.get('recovered_count') else 'idle'
        if pending_visible_cdp_recovery.get('recovered_count', 0) > 0:
            action = 'pending_visible_cdp_result_recovered'
        return {'ok': True, 'action': action, 'processed': 0, 'recovery': recovery, 'reply_sending_recovery': reply_sending_recovery, 'account_sync': account_sync, 'account_onboarding_sync': account_onboarding_sync_result, 'reply_reconcile': reply_reconcile, 'mcp_acknowledged_supplements': mcp_acknowledged_supplements, 'pending_reply_retries': pending_reply_retries, 'queued_supplement_release': queued_supplement_release, 'orphaned_supplement_promotion': orphaned_supplement_promotion, 'invalid_supplement_release': invalid_supplement_release, 'pending_visible_cdp_recovery': pending_visible_cdp_recovery}
    attachment_supplements = deps.publish_attachment_active_supplements(queue, config, pending)
    if attachment_supplements.get('published'):
        pending = queue.list_pending(pending_scan_limit, preferred_task_id=task_id)
        if not task_id:
            pending = deps.include_released_active_pending_tasks(queue, pending, pending_scan_limit)
        pending = deps.sort_pending_by_route_fairness(queue, config, pending, task_id=task_id)
        if not pending:
            action = 'attachment_supplement_recovery' if recovery_had_work or pending_reply_had_work else 'attachment_supplement_idle'
            return {'ok': True, 'action': action, 'processed': 0, 'recovery': recovery, 'reply_sending_recovery': reply_sending_recovery, 'account_sync': account_sync, 'account_onboarding_sync': account_onboarding_sync_result, 'reply_reconcile': reply_reconcile, 'mcp_acknowledged_supplements': mcp_acknowledged_supplements, 'pending_reply_retries': pending_reply_retries, 'queued_supplement_release': queued_supplement_release, 'orphaned_supplement_promotion': orphaned_supplement_promotion, 'invalid_supplement_release': invalid_supplement_release, 'pending_visible_cdp_recovery': pending_visible_cdp_recovery, 'attachment_supplements': attachment_supplements}
    current_cdp_thread_id = str(config.get('trigger', {}).get('codex_thread_id') or '')
    dispatchable: list[tuple[dict[str, Any], dict[str, str], str]] = []
    skipped_retry_wait = 0
    skipped_unassigned = 0
    skipped_unavailable = 0
    skipped_published_supplement = 0
    skipped_permission_denied = 0
    active_route_keys: set[str] = set()
    active_route_hosts: dict[str, dict[str, Any]] = {}
    now_for_routes = deps.datetime.now(deps.timezone.utc)
    visible_cdp_state_cache: dict[str, Any] | None = None
    active_codex_tasks = queue.list_active_codex_delivery_tasks(limit=100)
    waiting_redelivery_gate_deferred = 0
    for active_task in active_codex_tasks:
        active_mode = deps.delivery_mode_for_task(config, active_task)
        active_thread_id = str(active_task.get('codex_thread_id') or '')
        if active_mode == 'codex-cdp' and active_thread_id:
            continue
        if deps.active_route_lease_expired(active_task, config, now_for_routes):
            continue
        active_key = deps.task_route_key(active_mode, active_thread_id)
        active_route_keys.add(active_key)
        active_route_hosts.setdefault(active_key, active_task)
    skipped_busy_route = 0
    for task in pending:
        active_thread = deps.get_active_thread(queue, config, str(task.get('external_user') or ''), use_default=False)
        task_id_value = str(task.get('id') or '')
        task_delivery_mode = deps.delivery_mode_for_task(config, task)
        permission_gate = deps.enforce_worker_dispatch_permission(queue, config, task, task_id=task_id_value, enforce_ask_scope_for_task=deps.enforce_ask_scope_for_task, reject_task_for_permission=deps.reject_task_for_permission, send_status_ack=deps.send_status_ack)
        if not permission_gate.get('allowed'):
            skipped_permission_denied += 1
            continue
        if not active_thread:
            config_thread = deps.find_thread_for_external_user(config, str(task.get('external_user') or ''))
            if config_thread:
                deps.set_active_thread(queue, str(task.get('external_user') or ''), str(config_thread.get('id') or ''))
                active_thread = config_thread
                queue.add_event('local', 'thread_route_runtime_rehydrated', {'external_user': str(task.get('external_user') or ''), 'thread_id': str(config_thread.get('id') or ''), 'thread_name': str(config_thread.get('name') or ''), 'thread_id_value': str(config_thread.get('thread_id') or ''), 'policy': 'restore runtime active-thread mapping from persisted config before onboarding fallback'}, task_id_value)
        waiting_followup_active = deps.find_waiting_followup_redelivery_active(queue, config, task, active_codex_tasks)
        if waiting_followup_active is not None:
            waiting_active_id = str(waiting_followup_active.get('id') or '')
            waiting_thread_id = str(waiting_followup_active.get('codex_thread_id') or '').strip() or str((active_thread or {}).get('thread_id') or '').strip() or current_cdp_thread_id
            task_can_publish_supplement = deps.task_can_be_same_turn_supplement(queue, task)
            supplement_publish = {'ok': False, 'published': False, 'reason': 'missing_thread_id' if not waiting_thread_id else 'task_not_eligible_same_turn_supplement'}
            if waiting_thread_id and task_can_publish_supplement:
                supplement_publish = deps.publish_attachment_supplement_for_active(queue, config, waiting_followup_active, waiting_thread_id, [task], task_delivery_mode)
            if not supplement_publish.get('ok'):
                if not deps.task_event_recent(queue, task_id_value, 'followup_redelivery_supplement_publish_failed', 600):
                    queue.add_event('local', 'followup_redelivery_supplement_publish_failed', {'active_task_id': waiting_active_id, 'trigger_task_id': task_id_value, 'thread_id': waiting_thread_id, 'delivery_mode': task_delivery_mode, 'publish': supplement_publish, 'reason': 'same-thread follow-up cannot safely trigger owner redelivery until it is published as MCP supplement'}, task_id_value)
                continue
            waiting_payload = deps.latest_task_event_payload(queue, waiting_active_id, 'active_waiting_followup_redelivery')
            release_reason = str(waiting_payload.get('reason') or 'terminal_without_owned_result')
            release_detail = waiting_payload.get('detail') if isinstance(waiting_payload.get('detail'), dict) else {}
            owned_result_gate = deps.try_complete_owned_result_before_redelivery(queue, config, waiting_followup_active, release_reason, {**release_detail, 'trigger_task_id': task_id_value, 'trigger_policy': 'new_same_thread_message_releases_waiting_primary_redelivery', 'policy': 'check durable owned result before releasing the waiting primary task for redelivery'}, trigger_task_id=task_id_value)
            if owned_result_gate.get('completed'):
                queue.add_event('local', 'followup_triggered_waiting_redelivery_owned_result_consumed', {'active_task_id': waiting_active_id, 'trigger_task_id': task_id_value, 'delivery_mode': task_delivery_mode, 'release_reason': release_reason, 'gate': owned_result_gate, 'policy': 'owned result was already complete; cancel redelivery and return the finished result instead'}, waiting_active_id)
                return {'ok': True, 'action': 'waiting_owned_result_completed', 'processed': 0, 'recovery': {'ok': True, 'action': 'owned_result_completed_before_redelivery'}}
            if owned_result_gate.get('manual_required'):
                queue.add_event('local', 'followup_triggered_waiting_redelivery_manual_required', {'active_task_id': waiting_active_id, 'trigger_task_id': task_id_value, 'delivery_mode': task_delivery_mode, 'release_reason': release_reason, 'gate': owned_result_gate, 'policy': 'base task failed closed before redelivery; do not release it back to pending'}, waiting_active_id)
                return {'ok': True, 'action': 'waiting_redelivery_manual_required', 'processed': 0, 'recovery': {'ok': True, 'action': 'base_failed_closed_before_redelivery'}}
            if owned_result_gate.get('defer_redelivery'):
                waiting_redelivery_gate_deferred += 1
                queue.add_event('local', 'followup_triggered_waiting_redelivery_deferred', {'active_task_id': waiting_active_id, 'trigger_task_id': task_id_value, 'delivery_mode': task_delivery_mode, 'release_reason': release_reason, 'gate': owned_result_gate, 'policy': 'owned result state is not stable enough to redeliver yet; keep observing instead of requeueing'}, waiting_active_id)
                if not deps.task_event_recent(queue, task_id_value, 'dispatch_scan_gate_deferred_continue', 60):
                    queue.add_event('local', 'dispatch_scan_gate_deferred_continue', {'active_task_id': waiting_active_id, 'thread_id': waiting_thread_id, 'delivery_mode': task_delivery_mode, 'gate': owned_result_gate, 'reason': 'waiting follow-up redelivery gate deferred; keep this task pending/supplemented but continue scanning other routes', 'policy': 'a deferred primary follow-up gate must not starve independent account/thread dispatch'}, task_id_value)
                continue
            deps.clear_waiting_followup_redelivery_state(queue, waiting_active_id, 'triggered_by_same_thread_followup', {'trigger_task_id': task_id_value, 'delivery_mode': task_delivery_mode})
            release_result = deps.release_active_task_to_pending(queue, config, waiting_followup_active, release_reason, {**release_detail, 'trigger_task_id': task_id_value, 'trigger_policy': 'new_same_thread_message_releases_waiting_primary_redelivery', 'policy': 'new same-thread pending message triggers FIFO redelivery of the older primary visible-CDP task; the new message stays behind it as follow-up context', 'supplement_publish': supplement_publish})
            if release_result.get('ok'):
                deps.clear_delivery_retry(queue, [waiting_active_id])
                queue.add_event('local', 'followup_redelivery_retry_cleared', {'trigger_task_id': task_id_value, 'reason': 'new same-thread follow-up explicitly permits immediate FIFO redelivery of the released owner'}, waiting_active_id)
            queue.add_event('local', 'active_waiting_followup_redelivery_triggered', {'active_task_id': waiting_active_id, 'trigger_task_id': task_id_value, 'delivery_mode': task_delivery_mode, 'release_reason': release_reason, 'release_result': release_result, 'supplement_publish': supplement_publish, 'policy': 'release older waiting primary task first, then let newer same-thread pending messages join behind it in FIFO order'}, waiting_active_id)
            queue.add_event('local', 'followup_triggered_waiting_redelivery', {'released_active_task_id': waiting_active_id, 'thread_id': waiting_thread_id, 'supplement_publish': supplement_publish, 'reason': 'new same-thread message triggered retry of an older primary visible-CDP task; this newer message stays published as MCP supplement behind that owner'}, task_id_value)
            return deps.worker_once(queue, config, limit, task_id=waiting_active_id, fallback_depth=fallback_depth)
        if str(task.get('status') or '') == 'pending' and deps.pending_task_has_unacked_bridge_supplement(queue, task_id_value):
            skipped_published_supplement += 1
            if not deps.task_event_recent(queue, task_id_value, 'published_supplement_primary_dispatch_suppressed', 60):
                _published_key, published_payload = deps.bridge_supplement_payload_for_task(queue, task_id_value)
                queue.add_event('local', 'published_supplement_primary_dispatch_suppressed', {'thread_id': str(published_payload.get('thread_id') or ''), 'delivery_mode': str(published_payload.get('delivery_mode') or task_delivery_mode), 'base_task_id': deps.bridge_supplement_base_task_id(published_payload), 'reason': 'task is already published as bridge_supplement; keep pending for MCP ack instead of dispatching as a primary task', 'mcp_ack_present': False}, task_id_value)
            continue
        retry = deps.get_delivery_retry(queue, task_id_value)
        if retry.get('active') and (not deps.delivery_retry_reason_allows_batch(str(retry.get('reason') or ''))):
            skipped_retry_wait += 1
            if not deps.task_event_exists(queue, task_id_value, 'status_ack_delivery_retry_waiting'):
                deps.send_status_ack(queue, task, '已收到，目标 Codex 对话暂时忙或不可用；这条消息已保留，稍后自动重试。', config, 'status_ack_delivery_retry_waiting')
            continue
        followup_owner_id = deps.latest_followup_trigger_owner(queue, task_id_value)
        if followup_owner_id:
            followup_owner = queue.get_task(followup_owner_id) or {}
            owner_status = str(followup_owner.get('status') or '')
            same_owner_route = deps.same_followup_owner_route(queue, config, followup_owner, task, active_thread)
            if same_owner_route and owner_status in {'pending', 'queued_for_codex', 'sent_to_codex', 'processing'}:
                if not deps.task_event_recent(queue, task_id_value, 'followup_primary_dispatch_deferred_to_released_owner', 600):
                    queue.add_event('local', 'followup_primary_dispatch_deferred_to_released_owner', {'owner_task_id': followup_owner_id, 'owner_status': owner_status, 'owner_thread_id': deps.effective_task_thread_id(queue, config, followup_owner), 'task_thread_id': deps.effective_task_thread_id(queue, config, task, active_thread), 'policy': 'same-thread trigger message stays behind the released final-reply owner and must not dispatch independently before the owner retry settles'}, task_id_value)
                continue
        if not active_thread:
            auto_route = deps.auto_create_thread_route_for_user(queue, config, str(task.get('external_user') or ''))
            if auto_route.get('ok'):
                active_thread = auto_route.get('thread')
                queue.add_event('local', 'thread_route_auto_onboarded', {'external_user': str(task.get('external_user') or ''), 'created': bool(auto_route.get('created')), 'thread': active_thread}, task_id_value)
                if auto_route.get('created') and (not deps.task_event_exists(queue, task_id_value, 'status_ack_thread_auto_created')):
                    deps.send_status_ack(queue, task, deps.onboarding_created_text(str(active_thread.get('name') or active_thread.get('id') or '独立线程')), config, 'status_ack_thread_auto_created')
            else:
                skipped_unassigned += 1
                if not queue.runtime_get(deps.onboarding_hold_key(task_id_value)):
                    with queue.session() as db:
                        db.execute("UPDATE mobile_tasks SET status='rejected', error=?, updated_at=?, completed_at=? WHERE id=? AND status='pending'", ('New OpenClaw user is allowed but automatic Codex thread creation failed', deps.datetime.now(deps.timezone.utc).isoformat(), deps.datetime.now(deps.timezone.utc).isoformat(), task_id_value))
                    queue.runtime_set(deps.onboarding_hold_key(task_id_value), '1')
                queue.add_event('local', 'thread_route_missing', {'external_user': str(task.get('external_user') or ''), 'reason': 'no user_active_thread mapping and automatic Codex thread creation failed', 'auto_route': auto_route}, task_id_value)
                if not deps.task_event_exists(queue, task_id_value, 'status_ack_thread_unassigned'):
                    deps.send_status_ack(queue, task, deps.onboarding_needed_text(str(task.get('external_user') or '')), config, 'status_ack_thread_unassigned')
                continue
        if not active_thread:
            skipped_unassigned += 1
            queue.add_event('local', 'thread_route_missing', {'external_user': str(task.get('external_user') or ''), 'reason': 'no user_active_thread mapping; new Weixin users must be assigned an independent Codex thread'}, task_id_value)
            if not deps.task_event_exists(queue, task_id_value, 'status_ack_thread_unassigned'):
                deps.send_status_ack(queue, task, deps.onboarding_needed_text(str(task.get('external_user') or '')), config, 'status_ack_thread_unassigned')
            continue
        active_thread_id = str(active_thread.get('thread_id') or '')
        visible_thread = deps.resolved_visible_cdp_thread_id(queue, config, task, active_thread)
        resolved_thread_id = str(visible_thread.get('resolved_thread_id') or active_thread_id or '').strip()
        task_thread_id = str(visible_thread.get('task_thread_id') or '').strip()
        if task_delivery_mode == 'codex-cdp' and task_thread_id and resolved_thread_id and (task_thread_id != resolved_thread_id):
            mismatch_detail = {'task_thread_id': task_thread_id, 'resolved_thread_id': resolved_thread_id, 'route_thread_id': str(visible_thread.get('route_thread_id') or ''), 'route_source': str(visible_thread.get('route_source') or ''), 'route_snapshot_version': str(visible_thread.get('route_snapshot_version') or ''), 'policy': 'use resolved visible thread for CDP dispatch; treat configured codex_thread_id as advisory only'}
            queue.add_event('local', 'thread_delivery_visible_cdp_thread_mismatch', mismatch_detail, task_id_value)
            if not deps.task_event_exists(queue, task_id_value, 'status_ack_visible_cdp_thread_mismatch'):
                deps.send_status_ack(queue, task, '已收到，但当前可见线程和配置线程不一致；系统会改用当前可见线程继续，避免错发。', config, 'status_ack_visible_cdp_thread_mismatch')
        if str(task.get('status') or '') == 'pending' and deps.task_is_supplement_context(queue, task_id_value) and deps.pending_task_is_published_bridge_supplement(queue, task_id_value, active_thread_id) and (not deps.mcp_ack_payload(queue, task_id_value)):
            skipped_published_supplement += 1
            with queue.session() as db:
                db.execute("\n                    UPDATE mobile_tasks\n                    SET queued_for_codex_at=NULL,\n                        sent_to_codex_at=NULL,\n                        updated_at=?\n                    WHERE id=? AND status='pending'\n                    ", (deps.datetime.now(deps.timezone.utc).isoformat(), task_id_value))
            already_suppressed = deps.task_event_payload_exists(queue, task_id_value, 'published_supplement_primary_dispatch_suppressed', lambda existing: str(existing.get('thread_id') or '') == active_thread_id and str(existing.get('delivery_mode') or '') == task_delivery_mode)
            if not already_suppressed:
                queue.add_event('local', 'published_supplement_primary_dispatch_suppressed', {'thread_id': active_thread_id, 'delivery_mode': task_delivery_mode, 'reason': 'task is already published as bridge_supplement; keep pending for MCP ack instead of dispatching as a primary task', 'mcp_ack_present': False}, task_id_value)
            continue
        if task_delivery_mode == 'codex-app-server':
            active_thread_name = str(active_thread.get('name') or '')
            visibility = deps.inspect_codex_thread_for_dispatch(config, active_thread_id, active_thread_name)
            dispatch_state = deps.codex_thread_dispatch_state(visibility)
            dispatch_state_type = str(dispatch_state.get('state') or '')
            if deps.codex_thread_needs_background_prewarm(visibility):
                prewarm_state = deps.get_thread_prewarm(queue, active_thread_id)
                prewarm = {'ok': False, 'active': bool(prewarm_state.get('active')), 'state': prewarm_state}
                if not prewarm_state.get('active'):
                    prewarm_marker = deps.mark_thread_prewarm(queue, config, active_thread_id, active_thread_name, 'thread_unavailable')
                    prewarm_start = deps.start_thread_prewarm_background(deps.Path(str(config.get('_config_path') or deps.DEFAULT_CONFIG)), active_thread_id, active_thread_name)
                    prewarm = {'ok': bool(prewarm_start.get('_powershell_returncode', 0) == 0), 'background': True, 'marker': prewarm_marker, 'start': prewarm_start}
                queue.add_event('local', 'thread_delivery_prewarm_deferred', {'thread_id': active_thread_id, 'thread_project': active_thread.get('id'), 'prewarm': prewarm, 'visibility': visibility, 'non_blocking': True, 'reason': 'thread listed but not loaded; dispatch will resume it if needed'}, task_id_value)
            if deps.codex_thread_is_unavailable(visibility):
                skipped_unavailable += 1
                recovery_marker = deps.mark_thread_recovery(queue, task_id_value, 'thread_unavailable', {'thread_id': active_thread_id, 'visibility': visibility})
                deps.mark_delivery_retry(queue, config, [task_id_value], 'thread_prewarming', {'thread_id': active_thread_id, 'visibility': visibility})
                queue.add_event('local', 'thread_delivery_visibility_unavailable', {'thread_id': active_thread_id, 'thread_project': active_thread.get('id'), 'visibility': visibility, 'recovery': recovery_marker, 'reason': 'Codex app-server target thread is unavailable; background prewarm scheduled or cooling down'}, task_id_value)
                if not deps.task_event_exists(queue, task_id_value, 'status_ack_thread_visibility_unavailable'):
                    deps.send_status_ack(queue, task, '已收到，但目标 Codex 线程正在恢复。系统已暂存这条消息，恢复后自动投递；其他对话不会被它拖住。', config, 'status_ack_thread_visibility_unavailable')
                continue
            if dispatch_state_type == 'probe_failed':
                skipped_unavailable += 1
                recovery_marker = deps.mark_thread_recovery(queue, task_id_value, 'thread_probe_failed', {'thread_id': active_thread_id, 'visibility': visibility, 'dispatch_state': dispatch_state})
                deps.mark_delivery_retry(queue, config, [task_id_value], 'probe_failed', {'thread_id': active_thread_id, 'visible_state': visibility, 'dispatch_state': dispatch_state})
                queue.add_event('local', 'thread_delivery_probe_failed', {'thread_id': active_thread_id, 'thread_project': active_thread.get('id'), 'visibility': visibility, 'dispatch_state': dispatch_state, 'recovery': recovery_marker, 'retry': deps.get_delivery_retry(queue, task_id_value), 'reason': 'thread probe failed transiently; observe first and keep a bounded retry window'}, task_id_value)
                if not deps.task_event_exists(queue, task_id_value, 'status_ack_visible_cdp_probe_failed'):
                    deps.send_status_ack(queue, task, '已收到，但电脑当前 Codex 状态暂时读不到；系统会先继续观测，确认后再决定是否恢复。', config, 'status_ack_visible_cdp_probe_failed')
                continue
            if deps.codex_thread_is_busy(visibility):
                skipped_unavailable += 1
                recovery_marker = deps.mark_thread_recovery(queue, task_id_value, 'thread_busy', {'thread_id': active_thread_id, 'visibility': visibility})
                deps.mark_delivery_retry(queue, config, [task_id_value], 'thread_busy', {'thread_id': active_thread_id, 'visibility': visibility})
                deps.add_coalesced_event(queue, 'local', 'thread_delivery_busy', {'thread_id': active_thread_id, 'thread_project': active_thread.get('id'), 'visibility': visibility, 'recovery': recovery_marker, 'reason': 'target Codex thread is busy; task remains pending'}, task_id_value, signature=f'{active_thread_id}:thread_busy')
                if not deps.task_event_exists(queue, task_id_value, 'status_ack_thread_busy'):
                    deps.send_status_ack(queue, task, '已收到，但你的 Codex 独立线程正在处理上一条消息；这条消息已排队，线程空闲后会继续投递。', config, 'status_ack_thread_busy')
                continue
        if task_delivery_mode == 'codex-cdp':
            if visible_cdp_state_cache is None:
                cdp_probe_config = deps.task_delivery_config(config, 'codex-cdp')
                cdp_start_cooldown = deps.get_cdp_start_probe_cooldown(queue)
                if cdp_start_cooldown.get('active'):
                    cdp_probe_config['trigger']['codex_cdp_no_start'] = True
                visible_cdp_state_cache = deps.poll_codex_result_cdp(cdp_probe_config, '')
                visible_startup_probe = visible_cdp_state_cache.get('startup') if isinstance(visible_cdp_state_cache.get('startup'), dict) else {}
                if not cdp_start_cooldown.get('active') and (not visible_startup_probe.get('ok')) and (not bool(visible_cdp_state_cache.get('generationActive'))):
                    deps.mark_cdp_start_probe_cooldown(queue, config, {'startup': visible_startup_probe})
            visible_state = visible_cdp_state_cache
            visible_generation_active = bool(visible_state.get('generationActive'))
            visible_startup = visible_state.get('startup') if isinstance(visible_state.get('startup'), dict) else {}
            visible_probe_ready = bool(visible_state.get('ok', False)) and bool(visible_state.get('healthy', False)) and bool(visible_startup.get('ok', False))
            visible_startup_reason = str(visible_startup.get('reason') or visible_state.get('reason') or '')
            visible_stale_listener = visible_startup_reason == 'codex_cdp_stale_os_listener'
            visible_probe_failed = not visible_probe_ready and (not visible_generation_active)
            if visible_generation_active:
                deps.add_coalesced_event(queue, 'local', 'thread_delivery_visible_cdp_busy_observed', {'thread_id': active_thread_id, 'thread_project': active_thread.get('id'), 'visible_state': visible_state, 'reason': 'visible CDP reported generation active, but pre-delivery busy signals are advisory only; submit and let the CDP delivery result decide'}, task_id_value, signature=f'{active_thread_id}:visible_cdp_busy_observed')
            if visible_stale_listener:
                skipped_unavailable += 1
                deps.mark_delivery_retry(queue, config, [task_id_value], 'codex_cdp_stale_os_listener', {'thread_id': active_thread_id, 'visible_state': visible_state, 'system_level': True})
                deps.add_coalesced_event(queue, 'local', 'thread_delivery_visible_cdp_stale_os_listener', {'thread_id': active_thread_id, 'thread_project': active_thread.get('id'), 'visible_state': visible_state, 'reason': 'Codex Desktop CDP port is held by stale Windows listener rows; avoid repeated launches until the OS releases the port.'}, task_id_value, signature=f'{active_thread_id}:codex_cdp_stale_os_listener')
                if not deps.task_event_exists(queue, task_id_value, 'status_ack_visible_cdp_stale_os_listener'):
                    deps.send_status_ack(queue, task, '已收到，但电脑 Codex 的 CDP 端口被系统残留监听占用；消息已暂存，释放端口或重启 Codex Desktop 后会继续投递。', config, 'status_ack_visible_cdp_stale_os_listener')
                continue
            if visible_probe_failed:
                skipped_unavailable += 1
                deps.mark_delivery_retry(queue, config, [task_id_value], 'visible_cdp_probe_failed', {'thread_id': active_thread_id, 'visible_state': visible_state, 'transient': True})
                deps.add_coalesced_event(queue, 'local', 'thread_delivery_visible_cdp_probe_failed', {'thread_id': active_thread_id, 'thread_project': active_thread.get('id'), 'visible_state': visible_state, 'reason': 'visible Codex Desktop probe failed; treat as transient read failure rather than busy'}, task_id_value, signature=f'{active_thread_id}:visible_cdp_probe_failed')
                if not deps.task_event_exists(queue, task_id_value, 'status_ack_visible_cdp_probe_failed'):
                    deps.send_status_ack(queue, task, '已收到，但电脑当前 Codex 状态暂时读不到；系统会稍后重试，不会把这次探测失败当成忙碌态。', config, 'status_ack_visible_cdp_probe_failed')
                continue
            queue.add_event('local', 'thread_delivery_visible_cdp_route', {'requested_thread_id': active_thread_id, 'requested_thread_project': active_thread.get('id'), 'visible_thread_id': current_cdp_thread_id, 'resolved_thread_id': current_cdp_thread_id or active_thread_id, 'route_snapshot_version': current_cdp_thread_id or active_thread_id, 'reason': 'primary account uses visible Codex Desktop CDP input route'}, task_id_value)
            active_thread = {'id': str(active_thread.get('id') or deps.default_thread_id(config)), 'name': str(active_thread.get('name') or ''), 'thread_id': current_cdp_thread_id or active_thread_id, 'visible_cdp_fallback': 'true', 'snapshot_version': current_cdp_thread_id or active_thread_id}
            active_thread_id = str(active_thread.get('thread_id') or active_thread_id)
        route_key = deps.task_route_key(task_delivery_mode, active_thread_id)
        if route_key in active_route_keys:
            active_host = active_route_hosts.get(route_key) or {}
            skipped_busy_route += 1
            can_defer_to_active_host = deps.valid_active_supplement_host(queue, active_host)
            deferred = False
            if can_defer_to_active_host:
                deferred = deps.defer_continuation_for_busy_route(queue, task, config, route_key, active_thread_id, task_delivery_mode)
            if deferred:
                queue.add_event('local', 'thread_delivery_route_busy', {'route_key': route_key, 'thread_id': active_thread_id, 'delivery_mode': task_delivery_mode, 'reason': 'another task is already active or selected for this route', 'deferred_as_continuation': True}, task_id_value)
            elif not can_defer_to_active_host:
                queue.add_event('local', 'thread_delivery_route_busy_invalid_supplement_host', {'route_key': route_key, 'thread_id': active_thread_id, 'delivery_mode': task_delivery_mode, 'active_task_id': str(active_host.get('id') or ''), 'active_status': str(active_host.get('status') or ''), 'active_is_supplement_context': deps.task_is_supplement_context(queue, str(active_host.get('id') or '')), 'reason': 'same route has active work, but the active task is not a final-reply owner; keep pending without declaring it a supplement', 'deferred_as_continuation': False}, task_id_value)
            elif deps.task_event_exists(queue, task_id_value, 'continuation_deferred') or deps.task_is_supplement_context(queue, task_id_value):
                queue.add_event('local', 'thread_delivery_route_busy_duplicate_suppressed', {'route_key': route_key, 'thread_id': active_thread_id, 'delivery_mode': task_delivery_mode, 'reason': 'same supplement task already deferred; avoid repeating route-busy diagnostics', 'deferred_as_continuation': True}, task_id_value)
            else:
                queue.add_event('local', 'thread_delivery_route_busy', {'route_key': route_key, 'thread_id': active_thread_id, 'delivery_mode': task_delivery_mode, 'reason': 'another task is already active or selected for this route', 'deferred_as_continuation': False}, task_id_value)
            continue
        dispatchable.append((task, active_thread, task_delivery_mode))
        if len(dispatchable) >= pending_scan_limit:
            break
    if not dispatchable:
        action = 'idle_waiting_owned_result_gate' if waiting_redelivery_gate_deferred else 'idle_no_dispatchable_thread'
        recovery_result = dict(recovery)
        if waiting_redelivery_gate_deferred:
            recovery_result['action'] = 'waiting_owned_result_gate_deferred'
        return {'ok': True, 'action': action, 'processed': 0, 'recovery': recovery_result, 'reply_sending_recovery': reply_sending_recovery, 'mcp_acknowledged_supplements': mcp_acknowledged_supplements, 'queued_supplement_release': queued_supplement_release, 'invalid_supplement_release': invalid_supplement_release, 'orphaned_supplement_promotion': orphaned_supplement_promotion, 'pending': len(pending), 'skipped_retry_wait': skipped_retry_wait, 'skipped_unassigned': skipped_unassigned, 'skipped_unavailable': skipped_unavailable, 'skipped_busy_route': skipped_busy_route, 'skipped_published_supplement': skipped_published_supplement, 'waiting_redelivery_gate_deferred': waiting_redelivery_gate_deferred, 'attachment_supplements': attachment_supplements}
    first_task = dispatchable[0][0]
    first_thread = dispatchable[0][1]
    delivery_mode = dispatchable[0][2]
    thread_resolution = deps.resolved_visible_cdp_thread_id(queue, config, first_task, first_thread)
    thread_id = str(thread_resolution.get('resolved_thread_id') or first_thread.get('thread_id') or current_cdp_thread_id)
    thread_project = str(first_thread.get('id') or deps.default_thread_id(config))
    if not thread_id:
        queue.add_event('local', 'thread_route_missing_thread_id', {'external_user': str(first_task.get('external_user') or ''), 'reason': 'active thread mapping exists but has no resolvable visible Codex thread_id'}, str(first_task.get('id') or ''))
        return {'ok': False, 'action': 'blocked', 'reason': 'active thread has no resolvable visible Codex thread_id', 'processed': 0, 'recovery': recovery, 'reply_sending_recovery': reply_sending_recovery, 'mcp_acknowledged_supplements': mcp_acknowledged_supplements, 'queued_supplement_release': queued_supplement_release, 'orphaned_supplement_promotion': orphaned_supplement_promotion, 'pending': len(pending)}
    filtered_pending = []
    skipped = 0
    for task, active, active_delivery_mode in dispatchable:
        task_thread_id = str(active.get('thread_id') or '')
        if task_thread_id == thread_id and active_delivery_mode == delivery_mode:
            filtered_pending.append(task)
        else:
            skipped += 1
    pending = filtered_pending
    if not pending:
        return {'ok': True, 'action': 'idle_thread_mismatch', 'processed': 0, 'recovery': recovery, 'reply_sending_recovery': reply_sending_recovery, 'mcp_acknowledged_supplements': mcp_acknowledged_supplements, 'queued_supplement_release': queued_supplement_release, 'orphaned_supplement_promotion': orphaned_supplement_promotion, 'skipped': skipped}
    merged_pending = deps.pending_route_batch_tasks(queue, config, pending[0], thread_id, delivery_mode, pending)
    if not merged_pending:
        merged_pending = [dict(task) for task in pending]
    owner_tasks, member_tasks = deps.delivery_group_split(merged_pending)
    owner_task_ids = deps.delivery_group_task_ids(owner_tasks)
    member_task_ids = deps.delivery_group_task_ids(member_tasks)
    dispatch_tasks = owner_tasks if owner_tasks else [dict(merged_pending[0])]
    dispatch_task_ids = deps.delivery_group_task_ids(dispatch_tasks)
    attachment_ids = deps.attachment_task_ids(merged_pending)
    if attachment_ids:
        queue.add_event('local', 'attachment_batch_preempted', {'thread_id': thread_id, 'delivery_mode': delivery_mode, 'attachment_task_ids': attachment_ids, 'batch_task_ids': [str(task.get('id') or '') for task in merged_pending], 'owner_task_ids': owner_task_ids, 'member_task_ids': member_task_ids, 'reason': 'attachment task detected; deliver the owner prompt and expose later pending rows as MCP supplements'}, owner_task_ids[0] if owner_task_ids else str(merged_pending[0].get('id') or ''))
    task_ids = dispatch_task_ids
    first_task = dispatch_tasks[0]
    first_task_id = str(first_task.get('id') or '')
    if waiting_redelivery_gate_deferred and first_task_id:
        queue.add_event('local', 'dispatch_fairness_after_gate_defer', {'thread_id': thread_id, 'delivery_mode': delivery_mode, 'deferred_gate_count': waiting_redelivery_gate_deferred, 'dispatch_task_ids': dispatch_task_ids, 'reason': 'dispatch proceeds for an independent route after a waiting redelivery gate deferred on another route'}, first_task_id)
    mcp_gate = deps.current_mcp_session_gate_for_dispatch(delivery_mode, config)
    if not mcp_gate.get('ok'):
        deps.mark_delivery_retry(queue, config, task_ids, str(mcp_gate.get('reason') or 'mcp_tool_surface_unavailable'), {'thread_id': thread_id, 'delivery_mode': delivery_mode, 'gate': mcp_gate, 'policy': 'pre-dispatch gate prevents creating another hidden Codex turn while the current turn has closed MCP transports'})
        queue.add_event('local', 'mcp_tool_surface_pre_dispatch_blocked', {'thread_id': thread_id, 'delivery_mode': delivery_mode, 'task_ids': task_ids, 'gate': mcp_gate, 'reason': 'current turn MCP transport is unavailable; wait for fresh turn/session evidence'}, first_task_id)
        return {'ok': True, 'action': 'mcp_tool_surface_waiting_fresh_turn', 'processed': 0, 'thread_id': thread_id, 'thread_project': thread_project, 'delivery_mode': delivery_mode, 'task_ids': task_ids, 'mcp_gate': mcp_gate, 'pending': len(pending), 'recovery': recovery, 'reply_sending_recovery': reply_sending_recovery, 'mcp_acknowledged_supplements': mcp_acknowledged_supplements, 'queued_supplement_release': queued_supplement_release, 'orphaned_supplement_promotion': orphaned_supplement_promotion}
    delivery_group: dict[str, Any] = {'ok': True, 'owner_task_ids': owner_task_ids, 'member_task_ids': member_task_ids, 'member_count': len(member_task_ids), 'marked': False}
    pending_backlog_supplement: dict[str, Any] = {'ok': True, 'published': False, 'member_task_ids': member_task_ids}
    if first_task_id and (not deps.task_event_exists(queue, first_task_id, 'status_ack_delivery_queue_entered')):
        deps.send_status_ack(queue, first_task, '已进入 Codex 投递队列，正在准备投递。', config, 'status_ack_delivery_queue_entered')
    continuation = deps.get_continuation_context(queue, config, str(pending[0].get('external_user') or ''), thread_project)
    if queue.shadow_mode():
        prompt = deps.task_prompt(dispatch_tasks, continuation, bridge_thread_id=thread_id, config=config)
        for task in dispatch_tasks:
            queue.complete(str(task['id']), f'[shadow] Would dispatch this mobile task to Codex thread {thread_id}. Combined prompt:\n{prompt}', status='done')
            queue.add_event('local', 'shadow_dispatch_skipped', {'thread_id': thread_id, 'thread_project': thread_project, 'task_count': len(pending)}, str(task['id']))
        return {'ok': True, 'action': 'shadow_dispatched', 'processed': len(dispatch_tasks), 'thread_id': thread_id, 'thread_project': thread_project, 'prompt': prompt, 'reply_sending_recovery': reply_sending_recovery}
    queue_lock_scope = 'thread'
    ok, message = queue.queue_for_codex(task_ids, thread_id, lock_scope=queue_lock_scope)
    if not ok:
        return {'ok': False, 'action': 'queue_for_codex_failed', 'reason': message, 'processed': 0, 'recovery': recovery, 'mcp_acknowledged_supplements': mcp_acknowledged_supplements, 'orphaned_supplement_promotion': orphaned_supplement_promotion}
    for task in dispatch_tasks:
        queue.add_event('local', 'thread_route_selected', {'thread_id': thread_id, 'thread_project': thread_project, 'delivery_mode': delivery_mode}, str(task['id']))
    if member_tasks:
        pending_backlog_supplement = deps.publish_pending_backlog_supplement_for_owner(queue, config, first_task, thread_id, member_tasks, delivery_mode)
    ext_user = str(first_task.get('external_user') or '')
    if first_task_id and (not deps.task_event_exists(queue, first_task_id, 'status_ack_dispatching')):
        dispatching_text = '正在输入到电脑当前 Codex 对话。' if delivery_mode == 'codex-cdp' else '正在投递到 Codex。'
        deps.send_status_ack(queue, first_task, dispatching_text, config, 'status_ack_dispatching')
    dispatch_config = dict(config)
    dispatch_config['trigger'] = dict(config.get('trigger', {}))
    dispatch_config['trigger']['delivery_mode'] = delivery_mode
    dispatch_config['trigger']['auto_reply'] = False
    dispatch_config['_delivery_group_result_owner_task_ids'] = owner_task_ids
    delivery = {}
    try:
        delivery = deps.dispatch_to_codex(dispatch_tasks, thread_id, dispatch_config, continuation)
    except Exception as exc:
        delivery = {'ok': False, 'reason': str(exc)}
    cdp_submission_unverified = deps.cdp_delivery_lacks_submission_evidence(delivery)
    if cdp_submission_unverified:
        delivery = dict(delivery)
        delivery['submission_unconfirmed'] = True
        delivery['reason'] = 'cdp_visible_submission_unverified_observed'
        delivery['diagnostic_only'] = True
        delivery['policy'] = 'CDP transport accepted the submission, but visible-side confirmation was not captured; treat this as diagnostic only and do not retroactively deny transport acceptance'
        queue.add_event('local', 'cdp_visible_submission_unverified_observed', {'thread_id': thread_id, 'delivery': delivery, 'policy': 'visible confirmation is diagnostic; receipt/ownership polling remains authoritative'}, first_task_id)
    thread_recovery_marker: dict[str, Any] = {}
    app_server_repair: dict[str, Any] = {}
    app_server_unreadable_observed = delivery_mode == 'codex-app-server' and str(delivery.get('reason') or '') == 'app_server_turn_not_readable_after_dispatch'
    if not delivery.get('ok'):
        if cdp_submission_unverified:
            thread_recovery_marker = deps.mark_thread_recovery(queue, first_task_id, 'cdp_visible_submission_unverified', {'thread_id': thread_id, 'delivery': delivery, 'policy': 'CDP transport accepted the submission, but visible-side confirmation was not captured; keep retry reasoning separate from transport acceptance'})
            attention_after = deps.visible_cdp_unverified_submission_attention_after_attempts(config)
            if int(thread_recovery_marker.get('attempts') or 0) >= attention_after:
                delivery['reason'] = 'cdp_visible_submission_needs_attention'
                delivery['attention_required'] = True
                delivery['attention_after_attempts'] = attention_after
                delivery['policy'] = 'CDP transport accepted the submission, but visible-side confirmation was not captured repeatedly; surface attention without downgrading delivery acceptance'
                queue.add_event('local', 'cdp_visible_submission_needs_attention', {'thread_id': thread_id, 'attempts': int(thread_recovery_marker.get('attempts') or 0), 'threshold': attention_after, 'delivery': delivery, 'policy': 'do not switch to app-server; inspect and restore the visible CDP route before retrying'}, first_task_id)
        failed_task_ids = deps.delivery_group_task_ids(merged_pending) if cdp_submission_unverified else task_ids
        if pending_backlog_supplement.get('published'):
            deps.clear_pending_backlog_supplement_if_matches(queue, thread_id, first_task_id, str(pending_backlog_supplement.get('signature') or ''))
        if cdp_submission_unverified:
            if not member_task_ids:
                queue.runtime_delete(deps.bridge_supplement_key(thread_id))
            remaining_runtime = deps.bridge_supplement_payload_for_task(queue, first_task_id, thread_id)
            if not remaining_runtime[0]:
                queue.runtime_delete(deps.attachment_supplement_signature_key(first_task_id))
            for member_id in member_task_ids:
                queue.add_event('local', 'delivery_group_member_released', {'owner_task_id': first_task_id, 'thread_id': thread_id, 'reason': 'cdp_visible_submission_unverified', 'policy': 'original owner submission was not visibly confirmed; release delivery-group supplement status so the next retry can rebuild the batch'}, member_id)
            if member_task_ids:
                queue.add_event('local', 'pending_visible_cdp_unconfirmed_supplement_runtime_preserved', {'owner_task_id': first_task_id, 'thread_id': thread_id, 'member_task_ids': member_task_ids, 'policy': 'keep supplement runtime until deferred members are promoted or consumed; do not drop the promotion host early'}, first_task_id)
        deps.revert_tasks_to_pending(queue, failed_task_ids, 'delivery_failed_reverted_to_pending', {'thread_id': thread_id, 'delivery': delivery})
        deps.mark_delivery_retry(queue, config, failed_task_ids, str(delivery.get('reason') or 'dispatch_failed'), {'thread_id': thread_id, 'delivery': delivery})
        if not deps.task_event_exists(queue, first_task_id, 'status_ack_delivery_deferred'):
            deps.send_status_ack(queue, pending[0], '已收到，但 Codex 投递暂时不稳定；这条消息已保留，稍后自动重试，不会丢失。', config, 'status_ack_delivery_deferred')
        fallback_result: dict[str, Any] = {}
        current_route_key = deps.task_route_key(delivery_mode, thread_id)
        fallback_task_id = deps.next_dispatchable_route_task_id(dispatchable, current_route_key)
        max_fallback_depth = max(0, int(config.get('trigger', {}).get('worker_dispatch_fallback_depth') or 1))
        if fallback_task_id and fallback_depth < max_fallback_depth:
            queue.add_event('local', 'route_dispatch_fallback_selected', {'failed_route_key': current_route_key, 'fallback_task_id': fallback_task_id, 'fallback_depth': fallback_depth + 1, 'reason': 'route-local delivery failure; trying another dispatchable route in the same worker cycle'}, first_task_id)
            fallback_result = deps.worker_once(queue, config, limit, task_id=fallback_task_id, fallback_depth=fallback_depth + 1)
        elif not fallback_task_id:
            queue.add_event('local', 'route_dispatch_fallback_unavailable', {'failed_route_key': current_route_key, 'fallback_depth': fallback_depth, 'reason': 'no other dispatchable route in this worker cycle'}, first_task_id)
        return {'ok': True, 'action': 'delivery_deferred', 'processed': 0, 'recovery': recovery, 'mcp_acknowledged_supplements': mcp_acknowledged_supplements, 'invalid_supplement_release': invalid_supplement_release, 'orphaned_supplement_promotion': orphaned_supplement_promotion, 'thread_id': thread_id, 'delivery': delivery, 'thread_recovery': thread_recovery_marker, 'app_server_repair': app_server_repair, 'fallback_depth': fallback_depth, 'fallback_task_id': fallback_task_id, 'fallback_result': fallback_result}
    if app_server_unreadable_observed:
        thread_recovery_marker = deps.mark_thread_recovery(queue, first_task_id, 'app_server_turn_not_readable_after_dispatch', {'thread_id': thread_id, 'delivery': delivery, 'policy': 'turn/start returned an id but post-dispatch turns/list did not expose it; keep it as diagnostic evidence and do not undo transport acceptance'})
        app_server_repair = deps.maybe_repair_app_server_unreadable_thread(queue, config, first_task_id, thread_id, thread_recovery_marker, delivery)
    queue.mark_sent_to_codex(task_ids)
    recovered_markers = deps.clear_thread_recovery(queue, task_ids)
    if recovered_markers and first_task_id and (not deps.task_event_exists(queue, first_task_id, 'status_ack_thread_recovered')):
        deps.send_status_ack(queue, first_task, '目标 Codex 线程已恢复，正在继续投递之前保留的消息。', config, 'status_ack_thread_recovered')
    deps.clear_delivery_retry(queue, task_ids)
    turn_id = str(delivery.get('turn_id') or '')
    client_message_id = str(delivery.get('client_user_message_id') or '')
    baseline_key = str(delivery.get('baseline_key') or '')
    expected_task_ids = [str(item) for item in delivery.get('expected_task_ids') or owner_task_ids or task_ids if str(item)]
    if owner_task_ids:
        owner_id_set = set(owner_task_ids)
        expected_task_ids = [task_id for task_id in expected_task_ids if task_id in owner_id_set]
        if not expected_task_ids:
            expected_task_ids = owner_task_ids
    mobile_protocol_map = delivery.get('mobile_protocols') if isinstance(delivery.get('mobile_protocols'), dict) else {}
    if not turn_id:
        deps.revert_tasks_to_pending(queue, task_ids, 'delivery_missing_turn_id_reverted_to_pending', {'delivery': delivery})
        return {'ok': True, 'action': 'delivery_missing_turn_id_reverted', 'processed': 0, 'recovery': recovery, 'mcp_acknowledged_supplements': mcp_acknowledged_supplements, 'invalid_supplement_release': invalid_supplement_release, 'orphaned_supplement_promotion': orphaned_supplement_promotion, 'thread_id': thread_id, 'delivery': delivery}
    if turn_id:
        for tid in task_ids:
            queue.runtime_set(deps.task_turn_key(tid), turn_id)
            if client_message_id:
                queue.runtime_set(deps.task_batch_key(tid), client_message_id)
            if tid in expected_task_ids:
                queue.runtime_set(deps.task_expected_ids_key(tid), deps.json.dumps(expected_task_ids, ensure_ascii=False))
            else:
                queue.runtime_set(deps.task_expected_ids_key(tid), deps.json.dumps([], ensure_ascii=False))
            protocol = mobile_protocol_map.get(tid) if tid in expected_task_ids and isinstance(mobile_protocol_map.get(tid), dict) else {}
            ack_code = str(protocol.get('ack_code') or '')
            result_code = str(protocol.get('result_code') or '')
            if ack_code:
                queue.runtime_set(deps.task_ack_code_key(tid), ack_code)
            if result_code:
                queue.runtime_set(deps.task_result_code_key(tid), result_code)
            queue.add_event('local', 'codex_turn_started', {'thread_id': thread_id, 'turn_id': turn_id, 'client_message_id': client_message_id, 'expected_task_ids': expected_task_ids, 'mobile_protocol': {'ack_code_saved': bool(ack_code), 'result_code_saved': bool(result_code)}, 'mobile_protocols': mobile_protocol_map, 'delivery_mode': delivery_mode, 'desktop_visible': delivery.get('desktop_visible') or {}, 'sync_after_dispatch': delivery.get('sync_after_dispatch') or {}, 'delivery_group': delivery_group, 'pending_backlog_supplement': pending_backlog_supplement}, tid)
    external_user = str(pending[0].get('external_user') or '')
    dispatched_text = '已输入到电脑当前 Codex 对话，正在等待可见回复。' if delivery_mode == 'codex-cdp' else '📤 已投递到 Codex，正在思考…'
    dispatched_events = ('status_ack_dispatched', 'status_ack_dispatched_spawned', 'status_ack_dispatched_suppressed')
    if first_task_id and (not any((deps.task_event_exists(queue, first_task_id, event) for event in dispatched_events))):
        queue.add_event('wecom', 'status_ack_dispatched_guarded', {'delivery_mode': delivery_mode, 'thread_id': thread_id, 'reason': 'avoid duplicate thinking ack on repeated dispatch recovery'}, first_task_id)
        deps.send_status_ack(queue, first_task, dispatched_text, config, 'status_ack_dispatched')
    return {'ok': True, 'action': 'dispatched_waiting_result', 'processed': len(dispatch_tasks), 'supplement_member_count': len(member_task_ids), 'thread_id': thread_id, 'thread_project': thread_project, 'delivery_mode': delivery_mode, 'recovery': recovery, 'mcp_acknowledged_supplements': mcp_acknowledged_supplements, 'invalid_supplement_release': invalid_supplement_release, 'orphaned_supplement_promotion': orphaned_supplement_promotion, 'attachment_supplements': attachment_supplements, 'pending_backlog_supplement': pending_backlog_supplement, 'continuation': deps.get_continuation_context(queue, config, external_user, thread_project), 'delivery': delivery}
