#!/usr/bin/env python3
"""Machine metrics rendering for OpenClaw bridge maintenance snapshots.

Ownership: convert an already collected maintenance snapshot plus diagnosis
into compact machine-readable observability metrics.
Non-goals: collecting live evidence, diagnosing issues, repairing state, and
mutating queues/processes/configuration.
State behavior: read-only; callers pass snapshot/diagnosis and receive a dict.
Caller context: mobile_maintenance.observability_metrics facade and metrics CLI.
"""

from __future__ import annotations

from typing import Any, Callable


def render_observability_metrics(
    snapshot: dict[str, Any],
    diagnosis: dict[str, Any],
    *,
    layer_status_fn: Callable[[dict[str, Any]], dict[str, str]],
) -> dict[str, Any]:
    """Return compact machine-readable bridge observability metrics."""
    counts = snapshot.get("counts", {}).get("by_status", {})
    pending_items = snapshot.get("pending") if isinstance(snapshot.get("pending"), list) else []
    active_items = snapshot.get("active") if isinstance(snapshot.get("active"), list) else []
    reply_problems = snapshot.get("reply_problems") if isinstance(snapshot.get("reply_problems"), list) else []
    control_reply_receipts = (
        snapshot.get("control_reply_receipts")
        if isinstance(snapshot.get("control_reply_receipts"), dict)
        else {}
    )
    cdp_route = snapshot.get("cdp_route") if isinstance(snapshot.get("cdp_route"), dict) else {}
    app_server_mcp = snapshot.get("app_server_mcp") if isinstance(snapshot.get("app_server_mcp"), dict) else {}
    desktop_session_mcp = snapshot.get("desktop_session_mcp") if isinstance(snapshot.get("desktop_session_mcp"), dict) else {}
    app_server_mcp_action = (
        snapshot.get("app_server_mcp_actionability")
        if isinstance(snapshot.get("app_server_mcp_actionability"), dict)
        else {}
    )
    thread_routes_ui_health = (
        snapshot.get("thread_routes_ui_health")
        if isinstance(snapshot.get("thread_routes_ui_health"), dict)
        else {}
    )
    thread_route_state_counts = (
        snapshot.get("thread_route_state_counts")
        if isinstance(snapshot.get("thread_route_state_counts"), dict)
        else {}
    )
    materialization_lag = (
        snapshot.get("app_server_materialization_lag")
        if isinstance(snapshot.get("app_server_materialization_lag"), list)
        else []
    )
    reply_delivery_categories: dict[str, int] = {}
    for item in reply_problems:
        category = str(item.get("diagnostic_category") or "uncategorized")
        reply_delivery_categories[category] = reply_delivery_categories.get(category, 0) + 1
    issues = diagnosis.get("issues") if isinstance(diagnosis.get("issues"), list) else []
    issue_codes = [str(item.get("code") or "") for item in issues if item.get("code")]
    cdp_skipped = bool(cdp_route.get("skipped"))
    app_server_mcp_skipped = bool(app_server_mcp.get("skipped"))
    resource_processes = snapshot.get("resource_processes") if isinstance(snapshot.get("resource_processes"), dict) else {}
    backup_hygiene = snapshot.get("backup_hygiene") if isinstance(snapshot.get("backup_hygiene"), dict) else {}
    codex_config_guard = snapshot.get("codex_config_guard") if isinstance(snapshot.get("codex_config_guard"), dict) else {}
    permissions = snapshot.get("permission_policy") if isinstance(snapshot.get("permission_policy"), dict) else {}
    memory_governance = snapshot.get("memory_governance") if isinstance(snapshot.get("memory_governance"), dict) else {}
    resource_process_metrics = (
        resource_processes.get("metrics")
        if isinstance(resource_processes.get("metrics"), dict)
        else {}
    )
    resource_process_issues = (
        resource_processes.get("issues")
        if isinstance(resource_processes.get("issues"), list)
        else []
    )

    supplement_waiting = [
        item for item in pending_items if item.get("pending_kind") == "supplement_waiting_mcp_ack"
    ]
    normal_pending = [
        item for item in pending_items if item.get("pending_kind") != "supplement_waiting_mcp_ack"
    ]
    ui_visible_values: dict[str, int] = {}
    for item in active_items:
        observation = item.get("observation") if isinstance(item.get("observation"), dict) else {}
        value = str(observation.get("ui_visible") or "unknown")
        ui_visible_values[value] = ui_visible_values.get(value, 0) + 1

    return {
        "schema": "mobile-weixin-bridge-observability-metrics/v1",
        "generated_at": snapshot.get("generated_at"),
        "deep_probes": bool(snapshot.get("deep_probes")),
        "ok": not any(item.get("severity") in {"critical", "high"} for item in issues),
        "issue_count": len(issues),
        "issue_codes": issue_codes,
        "layers": layer_status_fn(snapshot),
        "queue": {
            "pending": len(normal_pending),
            "supplement_waiting_mcp_ack": len(supplement_waiting),
            "active": len(active_items),
            "reply_backlog": len(reply_problems),
            "status_counts": {str(key): int(value or 0) for key, value in counts.items()},
        },
        "cdp": {
            "ok": None if cdp_skipped else bool(cdp_route.get("ok")),
            "layer": str(cdp_route.get("layer") or ""),
            "host": str(cdp_route.get("host") or ""),
            "port": int(cdp_route.get("port") or 0),
            "endpoint_source": str(cdp_route.get("endpoint_source") or ""),
            "live_listeners": int(cdp_route.get("os_port_state", {}).get("live_count") or 0),
            "stale_listeners": int(cdp_route.get("os_port_state", {}).get("stale_count") or 0),
            "primary_pending": int(cdp_route.get("primary_pending_count") or 0),
            "start_script_available": None if cdp_skipped else bool(cdp_route.get("start_script_available")),
            "admin_start_script_available": None if cdp_skipped else bool(cdp_route.get("admin_start_script_available")),
            "manual_recovery_required": "codex_cdp_unavailable" in issue_codes,
        },
        "reply_delivery": {
            "backlog_count": len(reply_problems),
            "task_ids": [str(item.get("id") or "") for item in reply_problems[:10]],
            "diagnostic_categories": reply_delivery_categories,
            "requires_include_reply_send": bool(reply_problems),
        },
        "control_replies": {
            "ok": bool(control_reply_receipts.get("ok")),
            "sampled_event_count": int(control_reply_receipts.get("sampled_event_count") or 0),
            "outbox_count": int(control_reply_receipts.get("outbox_count") or 0),
            "terminal_count": int(control_reply_receipts.get("terminal_count") or 0),
            "missing_terminal_count": int(control_reply_receipts.get("missing_terminal_count") or 0),
            "action_without_outbox_count": int(control_reply_receipts.get("action_without_outbox_count") or 0),
            "missing_receipt_action_count": int(control_reply_receipts.get("missing_receipt_action_count") or 0),
            "legacy_missing_receipt_action_count": int(control_reply_receipts.get("legacy_missing_receipt_action_count") or 0),
            "parse_error_count": int(control_reply_receipts.get("parse_error_count") or 0),
            "contract": "action_receipt_id_plus_outbox_plus_sent_or_failed",
        },
        "app_server": {
            "mcp_layer": str(app_server_mcp.get("layer") or ""),
            "mcp_ok": None if app_server_mcp_skipped else bool(app_server_mcp.get("ok")),
            "mcp_actionable": None if app_server_mcp_skipped else bool(app_server_mcp_action.get("actionable")),
            "mcp_action_reason": str(app_server_mcp_action.get("reason") or ""),
            "materialization_lag_recent": len(materialization_lag),
            "materialization_lag_unresolved": sum(
                1 for item in materialization_lag if str(item.get("status") or "") in {"pending", "queued_for_codex", "sent_to_codex", "processing"}
            ),
            "active_ui_visible": ui_visible_values,
            "ui_visibility_proven": bool(ui_visible_values) and all(key == "true" for key in ui_visible_values),
        },
        "desktop_session_mcp": {
            "ok": None if desktop_session_mcp.get("skipped") else bool(desktop_session_mcp.get("ok")),
            "layer": str(desktop_session_mcp.get("layer") or ""),
            "current_session_transport_risk": bool(desktop_session_mcp.get("current_session_transport_risk")),
            "desktop_version": str(desktop_session_mcp.get("desktop_version") or ""),
            "desktop_app_server_versions": list(desktop_session_mcp.get("desktop_app_server_versions") or []),
            "bridge_app_server_versions": list(desktop_session_mcp.get("bridge_app_server_versions") or []),
            "version_split": bool(desktop_session_mcp.get("version_split")),
            "desktop_app_server_count": int(desktop_session_mcp.get("desktop_app_server_count") or 0),
            "mobile_mcp_child_count": int(desktop_session_mcp.get("mobile_mcp_child_count") or 0),
            "node_repl_child_count": int(desktop_session_mcp.get("node_repl_child_count") or 0),
            "root_recovery": "mcp_session_reload_or_controlled_codex_desktop_restart",
        },
        "thread_routes": {
            "ok": None if thread_routes_ui_health.get("skipped") else bool(thread_routes_ui_health.get("ok")),
            "layer": str(thread_routes_ui_health.get("layer") or ""),
            "state_counts": {str(key): int(value or 0) for key, value in thread_route_state_counts.items()},
            "visible_threads": int(thread_routes_ui_health.get("visible_thread_count") or 0),
            "supplement_waiting": int(thread_routes_ui_health.get("supplement_waiting_count") or 0),
        },
        "permissions": {
            "ok": bool(permissions.get("ok")),
            "allowed_user_count": int(permissions.get("allowed_user_count") or 0),
            "primary_admin_configured": bool(permissions.get("primary_admin_user")),
            "actor_count": len(permissions.get("actors") or []),
            "issue_codes": [str(item.get("code") or "") for item in (permissions.get("issues") or []) if isinstance(item, dict)],
            "deny_by_default_for_unknown_actions": bool(permissions.get("deny_by_default_for_unknown_actions")),
            "ordinary_user_unknown_actions_denied": bool(permissions.get("deny_by_default_for_unknown_actions")),
            "admin_superuser_enabled": bool(permissions.get("admin_superuser_enabled")),
            "unknown_action_policy": dict(permissions.get("unknown_action_policy") or {}),
            "ask_guard_applies_to_roles": list(permissions.get("ask_guard_applies_to_roles") or []),
            "ask_is_whitelist_only": bool(permissions.get("ask_is_whitelist_only")),
            "ask_allowed_scopes": list(permissions.get("ask_allowed_scopes") or []),
            "ask_denied_scopes": list(permissions.get("ask_denied_scopes") or []),
        },
        "codex_config_guard": {
            "ok": None if codex_config_guard.get("skipped") else bool(codex_config_guard.get("ok")),
            "issue_count": int(len(codex_config_guard.get("issues") or [])) if isinstance(codex_config_guard.get("issues"), list) else 0,
            "critical_failure_count": int(
                (((codex_config_guard.get("snapshot") or {}).get("audit") or {}).get("critical_failure_count") or 0)
            )
            if isinstance(codex_config_guard.get("snapshot"), dict)
            else 0,
            "restart_required": bool(
                (((codex_config_guard.get("snapshot") or {}).get("audit") or {}).get("restart_required"))
            )
            if isinstance(codex_config_guard.get("snapshot"), dict)
            else False,
            "repair_command": "python _bridge\\codex_config_guard.py run-once --apply",
            "validation_matrix": {
                "snapshot": "read-only",
                "doctor": "read-only",
                "repair_plan": "dry-run-only",
                "validate": "read-only gate check",
                "run_once_apply": "backup-protected merge-only repair",
            },
        },
        "resource_processes": {
            "ok": None if resource_processes.get("skipped") else bool(resource_processes.get("ok")),
            "layer": str(resource_processes.get("layer") or ""),
            "matched_process_count": int(resource_process_metrics.get("matched_process_count") or 0),
            "root_instance_count": int(resource_process_metrics.get("root_instance_count") or 0),
            "matched_group_count": int(resource_process_metrics.get("matched_group_count") or 0),
            "matched_working_set_mb": float(resource_process_metrics.get("matched_working_set_mb") or 0),
            "fanout_group_count": int(resource_process_metrics.get("fanout_group_count") or 0),
            "codex_app_server_owner_healthy": resource_process_metrics.get("codex_app_server_owner_healthy"),
            "codex_app_server_owner_issue": str(resource_process_metrics.get("codex_app_server_owner_issue") or ""),
            "codex_app_server_owner_count": int(resource_process_metrics.get("codex_app_server_owner_count") or 0),
            "risk_group_count": sum(1 for item in resource_process_issues if item.get("severity") in {"blocker", "risk"}),
            "advisory_group_count": sum(1 for item in resource_process_issues if item.get("severity") == "advisory"),
            "apply_supported": bool((resource_processes.get("repair_plan_preview") or {}).get("apply_supported")),
            "governance_state": str((resource_processes.get("repair_plan_preview") or {}).get("governance_state") or ""),
            "orphan_candidate_root_count": int((resource_processes.get("repair_plan_preview") or {}).get("orphan_candidate_root_count") or 0),
            "non_protected_orphan_candidate_root_count": int((resource_processes.get("repair_plan_preview") or {}).get("non_protected_orphan_candidate_root_count") or 0),
            "dry_run_only": True,
        },
        "backup_hygiene": {
            "ok": None if backup_hygiene.get("skipped") else bool(backup_hygiene.get("ok")),
            "layer": str(backup_hygiene.get("layer") or ""),
            "backup_count": int((backup_hygiene.get("metrics") or {}).get("backup_count") or 0),
            "total_size_mb": float((backup_hygiene.get("metrics") or {}).get("total_size_mb") or 0),
            "archive_candidate_count": int((backup_hygiene.get("metrics") or {}).get("archive_candidate_count") or 0),
            "same_directory_count": int((backup_hygiene.get("metrics") or {}).get("same_directory_count") or 0),
            "apply_supported": bool((backup_hygiene.get("repair_plan_preview") or {}).get("apply_supported")),
            "dry_run_only": True,
            "validation_matrix": {
                "snapshot": "read-only",
                "doctor": "read-only",
                "repair_plan": "dry-run-only",
                "validate": "read-only gate check",
                "apply": "gated confirm-only",
            },
        },
        "memory_governance": {
            "ok": None if memory_governance.get("skipped") else bool(memory_governance.get("ok")),
            "status": str(memory_governance.get("status") or ""),
            "issue_count": int(len(memory_governance.get("issues") or [])) if isinstance(memory_governance.get("issues"), list) else 0,
            "candidate_note_count": int((memory_governance.get("summary") or {}).get("candidate_note_count") or 0)
            if isinstance(memory_governance.get("summary"), dict)
            else 0,
            "operational_candidate_note_count": int((memory_governance.get("summary") or {}).get("operational_candidate_note_count") or 0)
            if isinstance(memory_governance.get("summary"), dict)
            else 0,
            "pmb_daemon_running": bool((memory_governance.get("summary") or {}).get("pmb_daemon_running"))
            if isinstance(memory_governance.get("summary"), dict)
            else False,
            "validation_matrix": {
                "snapshot": "read-only",
                "doctor": "read-only",
                "repair_plan": "dry-run-only",
                "validate": "read-only gate check",
                "metrics": "read-only",
            },
        },
        "safety": {
            "supplement_owner_gate_preserved": True,
            "no_route_switch_in_metrics": True,
            "no_reply_send_in_metrics": True,
            "no_queue_mutation_in_metrics": True,
            "no_resource_process_mutation_in_metrics": True,
            "no_backup_mutation_in_metrics": True,
            "no_memory_mutation_in_metrics": True,
        },
        "validation_matrix": {
            "snapshot": "read-only",
            "doctor": "read-only",
            "repair_plan": "dry-run-only",
            "validate": "read-only gate check",
            "apply": "gated confirm-only where supported",
        },
    }
