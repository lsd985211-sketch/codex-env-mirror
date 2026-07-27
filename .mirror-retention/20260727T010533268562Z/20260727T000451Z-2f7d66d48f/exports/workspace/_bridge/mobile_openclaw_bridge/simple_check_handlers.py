"""Simple bridge check command handler map.

Owns: mapping no-argument check command names to existing check functions for
mobile_openclaw_cli.
Non-goals: implementing checks, changing regression semantics, queue mutation,
or command registration metadata.
State behavior: this module is read-only routing; any state behavior belongs to
the existing check function it invokes.
Normal callers: mobile_openclaw_cli.main after mobile_cli_command_specs has
registered the command names.
"""

from __future__ import annotations

from typing import Any, Callable


NO_ARG_CHECK_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("maintenance-misjudgment-boundary-check", "maintenance_misjudgment_boundary_check"),
    ("control-receipt-contract-check", "control_receipt_contract_check"),
    ("mobile-repair-command-entry-check", "mobile_repair_command_entry_check"),
    ("mobile-repair-specialized-modes-check", "mobile_repair_specialized_modes_check"),
    ("mobile-execution-contract-check", "mobile_execution_contract_prompt_check"),
    ("mobile-permission-prompt-compact-check", "mobile_permission_prompt_compact_check"),
    ("gui-automation-health-check", "gui_automation_health_check_regression"),
    ("onboarding-check", "auto_onboarding_check"),
    ("account-onboarding-sync-check", "account_onboarding_sync_check"),
    ("account-onboarding-worker-lifecycle-check", "account_onboarding_worker_lifecycle_check"),
    ("result-ownership-check", "result_ownership_check"),
    ("fair-scheduling-check", "fair_scheduling_check"),
    ("waiting-redelivery-gate-route-fairness-check", "waiting_redelivery_gate_route_fairness_check"),
    ("route-fallback-dispatch-check", "route_fallback_dispatch_check"),
    ("route-rotation-fairness-check", "route_rotation_fairness_check"),
    ("active-slot-release-check", "active_slot_release_check"),
    ("same-route-expired-active-order-check", "same_route_expired_active_order_check"),
    ("active-generation-supplement-check", "active_generation_preserves_supplement_check"),
    ("cdp-live-listener-probe-unstable-check", "cdp_live_listener_probe_unstable_check"),
    ("cdp-localhost-host-preserved-check", "cdp_localhost_host_preserved_check"),
    ("active-recovery-route-fairness-check", "active_recovery_route_fairness_check"),
    ("active-ack-inprogress-observation-check", "active_ack_inprogress_observation_check"),
    ("waiting-followup-owned-result-recovery-check", "waiting_followup_owned_result_recovery_check"),
    ("waiting-followup-owned-result-redelivery-gate-check", "waiting_followup_owned_result_redelivery_gate_check"),
    ("base-ack-only-terminal-redelivery-check", "base_ack_only_terminal_redelivery_check"),
    ("failure-close-owned-result-recovery-check", "failure_close_owned_result_recovery_check"),
    ("historical-failed-result-filter-check", "historical_failed_result_filter_check"),
    ("failed-result-audit-recovery-consistency-check", "failed_result_audit_recovery_consistency_check"),
    ("protocol-violation-no-owned-result-check", "protocol_violation_no_owned_result_check"),
    ("active-stalled-tool-recovery-check", "active_stalled_tool_recovery_check"),
    ("app-server-repair-continuation-check", "app_server_repair_continuation_check"),
    ("active-progress-observability-check", "active_progress_observability_check"),
    ("active-observation-diagnosis-check", "active_observation_diagnosis_check"),
    ("app-server-result-poll-second-chance-check", "app_server_result_poll_second_chance_check"),
    ("app-server-turn-materialization-window-check", "app_server_turn_materialization_window_check"),
    ("historical-owned-result-fallback-check", "historical_owned_result_fallback_check"),
    ("thread-history-owned-result-fallback-check", "thread_history_owned_result_fallback_check"),
    ("thread-busy-status-check", "thread_busy_status_check"),
    ("primary-visible-cdp-probe-failure-check", "primary_visible_cdp_probe_failure_check"),
    ("transient-health-recovery-check", "transient_health_recovery_check"),
    ("global-transient-health-scope-check", "global_transient_health_scope_check"),
    ("reply-pending-account-scope-check", "reply_pending_account_scope_check"),
    ("reply-pending-fresh-context-only-check", "reply_pending_fresh_context_only_check"),
    ("thread-prewarm-budget-check", "thread_prewarm_budget_check"),
    ("thread-unlisted-recoverable-dispatch-check", "thread_unlisted_recoverable_dispatch_check"),
    ("thread-dispatch-probe-fallback-check", "thread_dispatch_probe_fallback_check"),
    ("thread-prewarm-execution-check", "thread_prewarm_execution_check"),
    ("thread-prewarm-probe-failed-check", "thread_prewarm_probe_failed_no_prewarm_check"),
    ("thread-probe-failed-worker-retreat-check", "thread_probe_failed_worker_retreat_check"),
    ("cdp-visible-delivery-check", "cdp_visible_delivery_check"),
    ("visible-cdp-unconfirmed-observation-check", "visible_cdp_unconfirmed_observation_check"),
    ("visible-cdp-unconfirmed-multi-supplement-followup-check", "visible_cdp_unconfirmed_multi_supplement_followup_check"),
    ("pending-visible-cdp-multi-supplement-consumption-check", "pending_visible_cdp_multi_supplement_consumption_check"),
    ("visible-cdp-repeated-unconfirmed-attention-check", "visible_cdp_repeated_unconfirmed_attention_check"),
    ("pending-visible-cdp-result-recovery-check", "pending_visible_cdp_result_recovery_check"),
    ("cdp-route-doctor-check", "cdp_route_doctor_check"),
    ("final-reply-visibility-check", "final_reply_visibility_check"),
    ("final-reply-visibility-unconfirmed-check", "final_reply_visibility_unconfirmed_check"),
    ("reply-send-idempotency-check", "reply_send_idempotency_check"),
    ("final-reply-media-text-split-check", "final_reply_media_text_split_check"),
    ("final-reply-media-ret2-governance-check", "final_reply_media_ret2_governance_check"),
    ("final-reply-ret2-token-present-diagnostic-check", "final_reply_ret2_token_present_diagnostic_check"),
    ("final-reply-active-owner-guard-check", "final_reply_active_owner_guard_check"),
    ("failed-result-visibility-unconfirmed-recovery-check", "failed_result_visibility_unconfirmed_recovery_check"),
    ("push-failed-ret2-fresh-context-recovery-check", "push_failed_ret2_fresh_context_recovery_check"),
    ("weixin-errcode-session-timeout-check", "weixin_errcode_session_timeout_check"),
    ("reply-dedupe-policy-check", "reply_dedupe_policy_check"),
    ("event-noise-coalescing-check", "event_noise_coalescing_check"),
    ("supplement-final-owner-check", "supplement_final_owner_check"),
    ("pending-backlog-supplement-batch-check", "pending_backlog_supplement_batch_check"),
    ("active-visible-cdp-supplement-publish-check", "active_visible_cdp_supplement_publish_check"),
    ("delivery-group-owner-event-fallback-check", "delivery_group_owner_event_fallback_check"),
    ("delivery-group-stale-active-snapshot-check", "delivery_group_stale_active_snapshot_check"),
    ("supplement-ack-gating-check", "supplement_ack_gating_check"),
    ("supplement-owner-promotion-check", "supplement_owner_promotion_check"),
    ("orphaned-supplement-promotion-check", "orphaned_supplement_promotion_check"),
    ("waiting-completed-reply-evidence-check", "waiting_completed_reply_evidence_check"),
    ("orphaned-supplement-promotion-with-push-evidence-check", "orphaned_supplement_promotion_with_push_evidence_check"),
    ("failed-base-supplement-owner-promotion-check", "failed_base_supplement_owner_promotion_check"),
    ("completed-owner-supplement-ack-window-check", "completed_owner_supplement_ack_window_check"),
    ("supplement-mcp-disconnect-no-primary-fallback-check", "supplement_mcp_disconnect_no_primary_fallback_check"),
    ("supplement-cli-fallback-check", "supplement_cli_fallback_check"),
    ("supplement-unacked-timeout-release-check", "supplement_unacked_timeout_release_check"),
    ("supplement-release-no-republish-check", "supplement_release_no_republish_check"),
    ("followup-redelivery-mcp-supplement-check", "followup_redelivery_mcp_supplement_check"),
    ("followup-redelivery-fifo-supplement-check", "followup_redelivery_fifo_supplement_check"),
    ("queued-same-route-supplement-recovery-check", "queued_same_route_supplement_recovery_check"),
    ("active-runtime-rehydrate-check", "active_runtime_rehydrate_check"),
    ("queued-turn-rehydrate-check", "queued_turn_rehydrate_check"),
    ("queued-turn-materialized-readback-rehydrate-check", "queued_turn_materialized_readback_rehydrate_check"),
    ("supplement-non-owner-host-check", "supplement_non_owner_host_check"),
    ("supplement-invalid-published-release-check", "supplement_invalid_published_release_check"),
    ("mcp-ack-does-not-complete-owner-check", "mcp_ack_does_not_complete_owner_check"),
    ("mcp-ack-missing-base-owner-check", "mcp_ack_missing_base_owner_check"),
    ("invalid-mcp-ack-not-published-supplement-check", "invalid_mcp_ack_not_published_supplement_check"),
    ("followup-redelivery-stale-pending-guard-check", "followup_redelivery_stale_pending_guard_check"),
    ("capability-passphrase-state-machine-check", "capability_passphrase_state_machine_check"),
    ("iteration-closeout-display-check", "iteration_closeout_display_check"),
    ("app-server-sync-check", "app_server_sync_after_dispatch_check"),
    ("app-server-unreadable-dispatch-guard-check", "app_server_unreadable_dispatch_guard_check"),
    ("app-server-unreadable-original-thread-repair-check", "app_server_unreadable_original_thread_repair_check"),
)


def build_simple_check_command_handlers(
    namespace: dict[str, Any],
    queue: Any,
    config: dict[str, Any],
    *,
    stability_deep: bool = False,
) -> dict[str, Callable[[], dict[str, Any]]]:
    """Return handlers for no-argument check commands registered in specs."""
    handlers: dict[str, Callable[[], dict[str, Any]]] = {
        command_name: _zero_arg_handler(namespace, function_name)
        for command_name, function_name in NO_ARG_CHECK_FUNCTIONS
    }
    handlers["stability-check"] = lambda: namespace["stability_check"](queue, config, deep=stability_deep)
    handlers["p0-audit"] = lambda: namespace["p0_audit"](queue, config)
    handlers["cdp-route-quick-check"] = lambda: namespace["cdp_route_quick_check"](config)

    def delivery_group_owner_compatibility_check() -> dict[str, Any]:
        result = namespace["pending_backlog_supplement_batch_check"]()
        result["compatibility_command"] = "delivery-group-owner-check"
        return result

    handlers["delivery-group-owner-check"] = delivery_group_owner_compatibility_check
    return handlers


def _zero_arg_handler(namespace: dict[str, Any], function_name: str) -> Callable[[], dict[str, Any]]:
    def run() -> dict[str, Any]:
        return namespace[function_name]()

    return run
