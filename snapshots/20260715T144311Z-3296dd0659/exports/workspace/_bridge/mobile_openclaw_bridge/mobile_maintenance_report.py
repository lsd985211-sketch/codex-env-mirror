#!/usr/bin/env python3
"""Text report rendering for OpenClaw bridge maintenance.

Ownership: render a human-readable maintenance summary from an already
collected snapshot and diagnosis result.
Non-goals: collecting live evidence, mutating queue state, deciding repair
actions, or invoking external probes.
State behavior: read-only pure string rendering.
Caller context: mobile_maintenance.summary_report after inspect/diagnose.
"""

from __future__ import annotations

from typing import Any, Callable


def severity_rank(value: Any) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(value or ""), 9)


def format_age(seconds: Any) -> str:
    if seconds is None:
        return "unknown"
    try:
        value = int(seconds)
    except Exception:
        return "unknown"
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m{value % 60:02d}s"
    return f"{value // 3600}h{(value % 3600) // 60:02d}m"


def format_task_ids(values: Any, limit: int = 4) -> str:
    ids = [str(item) for item in (values or []) if item]
    if not ids:
        return "-"
    shown = ids[:limit]
    suffix = f" +{len(ids) - limit}" if len(ids) > limit else ""
    return ", ".join(shown) + suffix


def render_summary_report(
    snapshot: dict[str, Any],
    diagnosis: dict[str, Any],
    *,
    active_statuses: tuple[str, ...],
    default_policy: dict[str, Any],
    layer_status_fn: Callable[[dict[str, Any]], dict[str, str]],
    probe_evidence_state_fn: Callable[..., dict[str, Any]],
) -> str:
    issues = diagnosis.get("issues") if isinstance(diagnosis.get("issues"), list) else []
    issues = sorted(issues, key=lambda item: severity_rank(item.get("severity")))
    counts = snapshot.get("counts", {}).get("by_status", {})
    layers = layer_status_fn(snapshot)
    pending_items = snapshot.get("pending") if isinstance(snapshot.get("pending"), list) else []
    cdp_visible_unconfirmed_observing = (
        snapshot.get("cdp_visible_unconfirmed_observing")
        if isinstance(snapshot.get("cdp_visible_unconfirmed_observing"), list)
        else []
    )
    normal_pending_count = sum(1 for item in pending_items if item.get("pending_kind") != "supplement_waiting_mcp_ack")
    supplement_waiting_count = sum(1 for item in pending_items if item.get("pending_kind") == "supplement_waiting_mcp_ack")

    high_count = sum(1 for item in issues if item.get("severity") in {"critical", "high"})
    overall = "healthy" if not issues else ("degraded" if high_count == 0 else "unhealthy")
    lines = [
        "Weixin bridge maintenance summary",
        f"Overall: {overall}",
        f"Probe mode: {'deep' if snapshot.get('deep_probes') else 'quick'}",
        "",
        "Layers:",
    ]
    for key in [
        "gateway",
        "control",
        "gateway_task",
        "worker",
        "worker_task",
        "codex_app_server",
        "app_server_mcp",
        "codex_cdp",
        "mobile_mcp",
        "codex_plugins",
        "gui_automation",
        "resource_processes",
        "database",
        "dashboard_live",
    ]:
        lines.append(f"- {key}: {layers.get(key, 'unknown')}")
    probe_timings = snapshot.get("probe_timings") if isinstance(snapshot.get("probe_timings"), list) else []
    if probe_timings:
        lines.extend(["", "Probe Timings:"])
        for item in sorted(probe_timings, key=lambda entry: int(entry.get("elapsed_ms") or 0), reverse=True):
            lines.append(
                f"- {item.get('name')}: {int(item.get('elapsed_ms') or 0)}ms "
                f"status={item.get('status') or ('ok' if item.get('ok') else 'non_ok')}"
            )
    if not snapshot.get("deep_probes"):
        skipped_layers = [
            key for key, value in layers.items()
            if value == "skipped"
        ]
        bad_layers = [
            key for key, value in layers.items()
            if value not in {"ok", "skipped", "unknown"}
        ]
        lines.extend(
            [
                "",
                "Probe Evidence Boundary:",
                "- mode: quick",
                f"- skipped_deep_layers: {format_task_ids(skipped_layers, limit=12)}",
                f"- real_non_ok_layers_seen: {format_task_ids(bad_layers, limit=12)}",
                "- note: skipped means not checked in quick mode, not a failure; use maintenance summary --deep, inspect, or doctor when route/MCP/GUI evidence matters",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Probe Evidence Boundary:",
                "- mode: deep",
                "- skipped_deep_layers: -",
                "- note: deep mode includes CDP/MCP/GUI/scheduled-task probes where available",
            ]
        )

    iteration_decision = snapshot.get("iteration_decision_summary") if isinstance(snapshot.get("iteration_decision_summary"), dict) else {}
    if iteration_decision:
        ready_for_review = iteration_decision.get("ready_for_manual_review")
        validation_first = iteration_decision.get("validation_first")
        review_count = len(ready_for_review) if isinstance(ready_for_review, list) else 0
        validation_count = len(validation_first) if isinstance(validation_first, list) else 0
        lines.extend(
            [
                "",
                "Iteration Decision:",
                f"- status: {'ok' if iteration_decision.get('ok') else 'unavailable'}",
                f"- summary: {iteration_decision.get('summary_text') or iteration_decision.get('reason') or '-'}",
            ]
        )
        primary_batch_id = str(iteration_decision.get("primary_batch_id") or "").strip()
        primary_destination = str(iteration_decision.get("primary_destination") or "").strip()
        primary_boundary_cluster = str(iteration_decision.get("primary_boundary_cluster") or "").strip()
        primary_boundary = str(iteration_decision.get("primary_boundary") or "").strip()
        if primary_batch_id or primary_destination:
            lines.append(
                f"- focus: {primary_batch_id or '-'} -> {primary_destination or '-'}"
            )
        if primary_boundary_cluster or primary_boundary:
            lines.append(
                f"- boundary: {primary_boundary_cluster or '-'} | {primary_boundary or '-'}"
            )
        lines.append(
            f"- review_ready: {review_count} | validation_first: {validation_count}"
        )
        lines.extend(
            [
                "- finalization_gate: for bridge/maintenance/resource/GUI/config/automation/agent system-level changes, run `maintenance iteration` before final reporting",
                "- proposal_policy: iteration output is proposal-only; it is not permission to modify skills, memory, project knowledge, CLI files, or bridge state",
            ]
        )

    lines.extend(
        [
            "",
            "Queue:",
            f"- pending: {int(counts.get('pending') or 0)}",
            f"- pending_dispatchable: {normal_pending_count}",
            f"- supplement_waiting_mcp_ack: {supplement_waiting_count}",
            f"- active: {sum(int(counts.get(status) or 0) for status in active_statuses)}",
            f"- pushed_to_wecom: {int(counts.get('pushed_to_wecom') or 0)}",
            f"- push_failed: {int(counts.get('push_failed') or 0)}",
        ]
    )

    thread_routes_ui_health = snapshot.get("thread_routes_ui_health") if isinstance(snapshot.get("thread_routes_ui_health"), dict) else {}
    thread_route_state_counts = snapshot.get("thread_route_state_counts") if isinstance(snapshot.get("thread_route_state_counts"), dict) else {}
    if thread_routes_ui_health:
        lines.extend(
            [
                "",
                "Thread Routes:",
                f"- layer: {thread_routes_ui_health.get('layer') or 'unknown'}",
                f"- ok: {bool(thread_routes_ui_health.get('ok'))}",
                f"- scope: {thread_routes_ui_health.get('summary_scope') or ('skipped' if thread_routes_ui_health.get('skipped') else 'full')} checked={int(thread_routes_ui_health.get('checked') or 0)} total={int(thread_routes_ui_health.get('total_configured_threads') or 0)}",
                f"- visible_threads: {int(thread_routes_ui_health.get('visible_thread_count') or 0)}",
                f"- supplement_waiting: {int(thread_routes_ui_health.get('supplement_waiting_count') or 0)}",
                f"- state_counts: {thread_route_state_counts or {}}",
            ]
        )

    event_noise = snapshot.get("event_noise") if isinstance(snapshot.get("event_noise"), dict) else {}
    event_archive = (
        snapshot.get("event_archive_dry_run") if isinstance(snapshot.get("event_archive_dry_run"), dict) else {}
    )
    if event_noise:
        top_noisy = event_noise.get("top_noisy_event_types") if isinstance(event_noise.get("top_noisy_event_types"), list) else []
        top_noisy_text = "-"
        if top_noisy:
            top_noisy_text = ", ".join(
                f"{item.get('event_type')}={int(item.get('count') or 0)}"
                for item in top_noisy[:3]
            )
        lines.extend(
            [
                "",
                "Event Noise:",
                f"- total_events: {int(event_noise.get('total_events') or 0)}",
                f"- guard_seconds: {int(event_noise.get('guard_seconds') or 0)}",
                f"- guard_index: {'ok' if event_noise.get('guard_index_exists') else 'missing'}",
                f"- recent_suppressed: {int(event_noise.get('suppressed_recent_total') or 0)} across {int(event_noise.get('suppressed_marker_count') or 0)} guard markers",
                f"- top_noisy: {top_noisy_text}",
            ]
        )
    if event_archive:
        lines.extend(
            [
                f"- archive_dry_run_candidates: {int(event_archive.get('candidate_count') or 0)} "
                f"older_than={int(event_archive.get('retention_hours') or 0)}h",
                "- archive_policy: dry-run only; no events are deleted by maintenance repair",
            ]
        )

    control_reply_receipts = snapshot.get("control_reply_receipts") if isinstance(snapshot.get("control_reply_receipts"), dict) else {}
    if control_reply_receipts:
        lines.extend(
            [
                "",
                "Control Reply Receipts:",
                f"- contract: {'ok' if control_reply_receipts.get('ok') else 'broken'}",
                f"- sampled_events: {int(control_reply_receipts.get('sampled_event_count') or 0)}",
                f"- outbox: {int(control_reply_receipts.get('outbox_count') or 0)} terminal={int(control_reply_receipts.get('terminal_count') or 0)}",
                f"- missing_terminal: {int(control_reply_receipts.get('missing_terminal_count') or 0)}",
                f"- action_without_outbox: {int(control_reply_receipts.get('action_without_outbox_count') or 0)}",
                f"- missing_receipt_action: {int(control_reply_receipts.get('missing_receipt_action_count') or 0)}",
                f"- legacy_missing_receipt_action: {int(control_reply_receipts.get('legacy_missing_receipt_action_count') or 0)}",
                "- note: control commands such as repair/status/stop/resume are not complete until a control_reply_sent or control_reply_failed receipt exists.",
            ]
        )
        missing_terminal = control_reply_receipts.get("missing_terminal") if isinstance(control_reply_receipts.get("missing_terminal"), list) else []
        missing_receipt = control_reply_receipts.get("missing_receipt_actions") if isinstance(control_reply_receipts.get("missing_receipt_actions"), list) else []
        if missing_terminal:
            sample = missing_terminal[0]
            lines.append(
                f"  sample_missing_terminal: receipt={sample.get('receipt_id') or '-'} event={sample.get('event_type') or '-'} id={sample.get('event_id') or '-'}"
            )
        if missing_receipt:
            sample = missing_receipt[0]
            lines.append(
                f"  sample_missing_receipt: event={sample.get('event_type') or '-'} id={sample.get('event_id') or '-'}"
            )

    cdp_route = snapshot.get("cdp_route") if isinstance(snapshot.get("cdp_route"), dict) else {}
    if cdp_route:
        if cdp_route.get("skipped"):
            lines.extend(
                [
                    "",
                    "CDP Route:",
                    f"- layer: {cdp_route.get('layer') or 'unknown'}",
                    f"- endpoint: {cdp_route.get('host')}:{cdp_route.get('port')}",
                    f"- reason: {cdp_route.get('reason')}",
                    f"- recent_unconfirmed_observing: {len(cdp_visible_unconfirmed_observing)}",
                ]
            )
        else:
            start_scripts = cdp_route.get("start_scripts") if isinstance(cdp_route.get("start_scripts"), list) else []
            available_scripts = [str(item.get("path") or "") for item in start_scripts if item.get("exists")]
            missing_scripts = [str(item.get("path") or "") for item in start_scripts if not item.get("exists")]
            lines.extend(
                [
                    "",
                    "CDP Route:",
                    f"- layer: {cdp_route.get('layer') or 'unknown'}",
                    f"- endpoint: {cdp_route.get('host')}:{cdp_route.get('port')}",
                    "- os_listeners: live={live} stale={stale}".format(
                        live=int(cdp_route.get("os_port_state", {}).get("live_count") or 0),
                        stale=int(cdp_route.get("os_port_state", {}).get("stale_count") or 0),
                    ),
                    f"- send_script: {'ok' if cdp_route.get('send_script', {}).get('exists') else 'missing'}",
                    f"- start_script: {'ok' if cdp_route.get('start_script_available') else 'missing'}",
                    f"- startup_contract: {cdp_route.get('startup_contract') or 'use configured Codex Desktop startup script'}",
                    f"- primary_pending: {int(cdp_route.get('primary_pending_count') or 0)} ids={format_task_ids(cdp_route.get('primary_pending_task_ids'))}",
                    f"- recent_probe_failures: {int(cdp_route.get('recent_visible_probe_failures') or 0)}",
                    f"- recent_unconfirmed_observing: {len(cdp_visible_unconfirmed_observing)}",
                ]
            )
            if not available_scripts and missing_scripts:
                lines.append(f"  missing_start_script: {missing_scripts[0]}")

    mobile_mcp = snapshot.get("mobile_mcp") if isinstance(snapshot.get("mobile_mcp"), dict) else {}
    if mobile_mcp:
        if mobile_mcp.get("skipped"):
            lines.extend(
                [
                    "",
                    "Mobile MCP:",
                    "- direct_stdio: skipped",
                    f"- reason: {mobile_mcp.get('reason')}",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "Mobile MCP:",
                    f"- direct_stdio: {'ok' if mobile_mcp.get('ok') else 'failed'}",
                    f"- script: {'ok' if mobile_mcp.get('script_exists') else 'missing'}",
                    f"- tools: {format_task_ids(mobile_mcp.get('tool_names'), limit=6)}",
                    "- note: direct smoke proves the server can start; current Codex MCP transport may still require restart if a live tool reports Transport closed.",
                ]
            )
            if mobile_mcp.get("reason"):
                lines.append(f"- reason: {mobile_mcp.get('reason')}")

    mobile_mcp_fallback = snapshot.get("mobile_mcp_fallback") if isinstance(snapshot.get("mobile_mcp_fallback"), dict) else {}
    if mobile_mcp_fallback:
        if mobile_mcp_fallback.get("skipped"):
            lines.extend(
                [
                    "",
                    "Mobile MCP Fallback:",
                    "- local_stdio: skipped",
                    f"- reason: {mobile_mcp_fallback.get('reason')}",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "Mobile MCP Fallback:",
                    f"- local_stdio: {'ok' if mobile_mcp_fallback.get('ok') else 'failed'}",
                    f"- script: {'ok' if mobile_mcp_fallback.get('script_exists') else 'missing'}",
                    f"- tool: {mobile_mcp_fallback.get('tool') or 'bridge.health'}",
                    "- note: used only when the current Codex session MCP transport is closed; supplement ack still uses the same MCP server implementation.",
                ]
            )
            if mobile_mcp_fallback.get("reason"):
                lines.append(f"- reason: {mobile_mcp_fallback.get('reason')}")

    desktop_session_mcp = snapshot.get("desktop_session_mcp") if isinstance(snapshot.get("desktop_session_mcp"), dict) else {}
    if desktop_session_mcp:
        if desktop_session_mcp.get("skipped"):
            lines.extend(
                [
                    "",
                    "Desktop Session MCP:",
                    "- layer: skipped",
                    f"- reason: {desktop_session_mcp.get('reason')}",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "Desktop Session MCP:",
                    f"- layer: {desktop_session_mcp.get('layer') or 'unknown'}",
                    f"- desktop_version: {desktop_session_mcp.get('desktop_version') or '-'}",
                    f"- desktop_app_server_versions: {format_task_ids(desktop_session_mcp.get('desktop_app_server_versions'), limit=4)}",
                    f"- bridge_app_server_versions: {format_task_ids(desktop_session_mcp.get('bridge_app_server_versions'), limit=4)}",
                    f"- desktop_app_servers: {int(desktop_session_mcp.get('desktop_app_server_count') or 0)} bridge_app_servers={int(desktop_session_mcp.get('bridge_app_server_count') or 0)}",
                    f"- mcp_children: mobile={int(desktop_session_mcp.get('mobile_mcp_child_count') or 0)} node_repl={int(desktop_session_mcp.get('node_repl_child_count') or 0)}",
                    f"- transport_risk: {'yes' if desktop_session_mcp.get('current_session_transport_risk') else 'no'} version_split={'yes' if desktop_session_mcp.get('version_split') else 'no'}",
                    "- note: this checks the current visible Codex Desktop session; direct stdio and 18791 app-server MCP health do not prove these live tools are connected.",
                ]
            )

    codex_mcp_config = snapshot.get("codex_mcp_config") if isinstance(snapshot.get("codex_mcp_config"), dict) else {}
    if codex_mcp_config:
        if codex_mcp_config.get("skipped"):
            missing_names = []
            drifted_names = []
            repairable_names = []
            repairable_drifted_names = []
        else:
            missing_names = [str(item.get("name") or "") for item in codex_mcp_config.get("missing", [])]
            drifted_names = [str(item.get("name") or "") for item in codex_mcp_config.get("drifted", [])]
            repairable_names = [str(item.get("name") or "") for item in codex_mcp_config.get("repairable_missing", [])]
            repairable_drifted_names = [
                str(item.get("name") or "") for item in codex_mcp_config.get("repairable_drifted", [])
            ]
        lines.extend(
            [
                "",
                "Codex MCP Config:",
                f"- config: {codex_mcp_config.get('path')}",
                f"- parse: {'skipped' if codex_mcp_config.get('skipped') else ('ok' if codex_mcp_config.get('parse_ok') else 'failed')}",
                f"- registered: {format_task_ids(codex_mcp_config.get('registered_servers'), limit=12)}",
                f"- missing: {format_task_ids(missing_names, limit=12)}",
                f"- drifted: {format_task_ids(drifted_names, limit=12)}",
                f"- repairable_missing: {format_task_ids(repairable_names, limit=12)}",
                f"- repairable_drifted: {format_task_ids(repairable_drifted_names, limit=12)}",
                "- note: config repairs require Codex Desktop restart before tools appear in the current session.",
            ]
        )

    codex_plugins = snapshot.get("codex_plugins") if isinstance(snapshot.get("codex_plugins"), dict) else {}
    if codex_plugins:
        plugin_evidence = probe_evidence_state_fn(
            codex_plugins,
            profile="deep" if snapshot.get("deep_probes") else "quick",
        )
        if codex_plugins.get("skipped"):
            plugin_missing = []
            cache_missing = []
            manifest_missing = []
        else:
            plugin_missing = [str(item) for item in (codex_plugins.get("missing_enabled_plugins") or []) if item]
            cache_missing = [str(item) for item in (codex_plugins.get("missing_cache_plugins") or []) if item]
            manifest_missing = [str(item) for item in (codex_plugins.get("missing_manifest_plugins") or []) if item]
        lines.extend(
            [
                "",
                "Codex Plugins:",
                f"- config: {codex_plugins.get('config_path')}",
                f"- parse: {'skipped' if codex_plugins.get('skipped') else ('ok' if codex_plugins.get('config_parse_ok') else 'failed')}",
                f"- missing_enabled: {format_task_ids(plugin_missing, limit=12)}",
                f"- missing_cache: {format_task_ids(cache_missing, limit=12)}",
                f"- missing_manifest: {format_task_ids(manifest_missing, limit=12)}",
                f"- evidence_state: {plugin_evidence.get('state')} current_failure={'yes' if plugin_evidence.get('current_failure') else 'no'}",
                "- note: plugin repair is additive-only; it restores missing enabled=true entries and keeps extra plugin config untouched.",
            ]
        )
        if plugin_evidence.get("reason"):
            lines.append(f"- evidence_note: {plugin_evidence.get('reason')}")

    gui = snapshot.get("gui_automation") if isinstance(snapshot.get("gui_automation"), dict) else {}
    if gui:
        if gui.get("skipped"):
            lines.extend(
                [
                    "",
                    "GUI Automation:",
                    "- runtime_modules: skipped",
                    f"- reason: {gui.get('reason')}",
                ]
            )
        else:
            runtime = gui.get("runtime") if isinstance(gui.get("runtime"), dict) else {}
            ocr = gui.get("ocr") if isinstance(gui.get("ocr"), dict) else {}
            fallback_ocr = ocr.get("fallback") if isinstance(ocr.get("fallback"), dict) else {}
            fallback_label = "same"
            if fallback_ocr.get("skipped") is False:
                fallback_label = "ready" if fallback_ocr.get("ready") else "not-ready"
            lines.extend(
                [
                    "",
                    "GUI Automation:",
                    f"- registered: {'yes' if gui.get('registered') else 'no'}",
                    f"- command: {gui.get('command') or '-'}",
                    f"- runtime_modules: {'ok' if runtime.get('ok') else 'missing=' + format_task_ids(runtime.get('missing'), limit=8)}",
                    f"- ocr: {'ready' if ocr.get('ready') else 'not-ready'} device={ocr.get('requested_device') or 'default'} cuda={'yes' if ocr.get('compiled_cuda') else 'no'} fallback={fallback_label}",
                    f"- config_drifted: {'yes' if gui.get('config_drifted') else 'no'}",
                ]
            )
            if ocr.get("error"):
                lines.append(f"- ocr_error: {ocr.get('error')}")
            if ocr.get("gpu_default_block_reason"):
                lines.append(f"- gpu_default_block_reason: {ocr.get('gpu_default_block_reason')}")
            if fallback_ocr.get("error"):
                lines.append(f"- fallback_ocr_error: {fallback_ocr.get('error')}")

    app_server_mcp = snapshot.get("app_server_mcp") if isinstance(snapshot.get("app_server_mcp"), dict) else {}
    if app_server_mcp:
        listener = app_server_mcp.get("listener") if isinstance(app_server_mcp.get("listener"), dict) else {}
        actionability = snapshot.get("app_server_mcp_actionability") if isinstance(snapshot.get("app_server_mcp_actionability"), dict) else {}
        lines.extend(
            [
                "",
                "App-Server MCP Baseline:",
                f"- layer: {app_server_mcp.get('layer') or 'unknown'}",
                f"- listener_count: {int(listener.get('count') or 0)}",
                f"- mobile_mcp_children: {int(app_server_mcp.get('mobile_mcp_child_count') or 0)}",
                f"- recent_transport_closed_events: {int(app_server_mcp.get('recent_transport_closed_events') or 0)}",
                f"- actionable: {'yes' if actionability.get('actionable') else 'no'} reason={actionability.get('reason') or '-'}",
                "- note: this checks the bridge-owned 18791 app-server, which is the route used by backup accounts.",
            ]
        )

    resource_processes = snapshot.get("resource_processes") if isinstance(snapshot.get("resource_processes"), dict) else {}
    if resource_processes:
        metrics = resource_processes.get("metrics") if isinstance(resource_processes.get("metrics"), dict) else {}
        resource_issues = resource_processes.get("issues") if isinstance(resource_processes.get("issues"), list) else []
        fanout_groups = [
            str(item.get("group") or "")
            for item in resource_issues
            if item.get("group")
        ]
        repeated_batches = (
            resource_processes.get("repeated_launch_batches")
            if isinstance(resource_processes.get("repeated_launch_batches"), list)
            else []
        )
        repeated_batch_groups = [
            f"{item.get('group')}:{int(item.get('launch_batch_count') or 0)}"
            for item in repeated_batches
            if item.get("group")
        ]
        lines.extend(
            [
                "",
                "Resource Processes:",
                f"- layer: {resource_processes.get('layer') or ('skipped' if resource_processes.get('skipped') else 'unknown')}",
                f"- matched_processes: {int(metrics.get('matched_process_count') or 0)} root_instances={int(metrics.get('root_instance_count') or 0)} groups={int(metrics.get('matched_group_count') or 0)} working_set_mb={metrics.get('matched_working_set_mb') if metrics else '-'}",
                f"- app_server_owner: healthy={metrics.get('codex_app_server_owner_healthy')} issue={metrics.get('codex_app_server_owner_issue') or '-'} owners={int(metrics.get('codex_app_server_owner_count') or 0)}",
                f"- fanout_groups: {format_task_ids(fanout_groups, limit=12)}",
                f"- repeated_launch_batches: {format_task_ids(repeated_batch_groups, limit=12)}",
                f"- repair_plan: dry-run apply_supported={'yes' if (resource_processes.get('repair_plan_preview') or {}).get('apply_supported') else 'no'} governance_state={(resource_processes.get('repair_plan_preview') or {}).get('governance_state') or '-'} actions={int((resource_processes.get('repair_plan_preview') or {}).get('action_count') or 0)} orphan_actions={int((resource_processes.get('repair_plan_preview') or {}).get('orphan_candidate_action_count') or 0)} orphan_roots={int((resource_processes.get('repair_plan_preview') or {}).get('orphan_candidate_root_count') or 0)} non_protected_orphan_roots={int((resource_processes.get('repair_plan_preview') or {}).get('non_protected_orphan_candidate_root_count') or 0)} protected_orphan_actions={int((resource_processes.get('repair_plan_preview') or {}).get('protected_orphan_candidate_action_count') or 0)}",
                f"- latest_batch_policy: {(resource_processes.get('repair_plan_preview') or {}).get('latest_batch_policy') or '-'}",
                "- note: maintenance summary does not kill/start processes; use cleanup dry-run before any apply, and protected groups remain skipped unless explicitly reviewed.",
            ]
        )
        if resource_processes.get("reason"):
            lines.append(f"- reason: {resource_processes.get('reason')}")

    backup_hygiene = snapshot.get("backup_hygiene") if isinstance(snapshot.get("backup_hygiene"), dict) else {}
    if backup_hygiene:
        bmetrics = backup_hygiene.get("metrics") if isinstance(backup_hygiene.get("metrics"), dict) else {}
        lines.extend(
            [
                "",
                "Backup Hygiene:",
                f"- layer: {backup_hygiene.get('layer') or ('skipped' if backup_hygiene.get('skipped') else 'unknown')}",
                f"- backup_count: {int(bmetrics.get('backup_count') or 0)} total_size_mb={bmetrics.get('total_size_mb') if bmetrics else '-'} archive_candidates={int(bmetrics.get('archive_candidate_count') or 0)}",
                f"- same_directory_count: {int(bmetrics.get('same_directory_count') or 0)} apply_supported={'yes' if (backup_hygiene.get('repair_plan_preview') or {}).get('apply_supported') else 'no'}",
                "- note: dry-run only; no files are moved, compressed, or deleted by maintenance summary.",
            ]
        )

    lines.append("")
    lines.append("Top issues:")
    if issues:
        for item in issues[:6]:
            lines.append(f"- [{item.get('severity')}] {item.get('code')}: {item.get('summary')}")
    else:
        lines.append("- none")

    accounts = snapshot.get("top_accounts") if isinstance(snapshot.get("top_accounts"), list) else []
    lines.append("")
    lines.append("Accounts:")
    if accounts:
        for item in accounts[:8]:
            pending = int(item.get("pending_count") or 0)
            supplement_waiting = int(item.get("supplement_waiting_count") or 0)
            active = int(item.get("active_count") or 0)
            replies = int(item.get("reply_backlog_count") or 0)
            if pending == 0 and supplement_waiting == 0 and active == 0 and replies == 0:
                continue
            lines.append(
                "- {account} ({mode}): pending={pending} supplement_waiting={supplement_waiting} active={active} reply_backlog={replies} "
                "oldest_pending={oldest_pending} oldest_active={oldest_active}".format(
                    account=item.get("account"),
                    mode=item.get("delivery_mode"),
                    pending=pending,
                    supplement_waiting=supplement_waiting,
                    active=active,
                    replies=replies,
                    oldest_pending=format_age(item.get("oldest_pending_age_seconds")),
                    oldest_active=format_age(item.get("oldest_active_age_seconds")),
                )
            )
            if item.get("thread_resolution_state") or item.get("effective_thread_id"):
                lines.append(
                    "  thread_resolution: raw={raw} effective={effective} state={state}".format(
                        raw=item.get("thread_id") or "",
                        effective=item.get("effective_thread_id") or item.get("thread_id") or "",
                        state=item.get("thread_resolution_state") or "",
                    )
                )
            if item.get("pending_task_ids"):
                lines.append(f"  pending_ids: {format_task_ids(item.get('pending_task_ids'))}")
            if item.get("supplement_waiting_task_ids"):
                lines.append(f"  supplement_waiting_ids: {format_task_ids(item.get('supplement_waiting_task_ids'))}")
            if item.get("active_task_ids"):
                lines.append(f"  active_ids: {format_task_ids(item.get('active_task_ids'))}")
            if item.get("reply_task_ids"):
                lines.append(f"  reply_ids: {format_task_ids(item.get('reply_task_ids'))}")
    else:
        lines.append("- none")

    pending_routes = snapshot.get("top_pending_routes") if isinstance(snapshot.get("top_pending_routes"), list) else []
    supplement_routes = (
        snapshot.get("top_supplement_waiting_routes")
        if isinstance(snapshot.get("top_supplement_waiting_routes"), list)
        else []
    )
    active_routes = snapshot.get("top_active_routes") if isinstance(snapshot.get("top_active_routes"), list) else []
    lines.append("")
    lines.append("Routes with pending work:")
    if pending_routes:
        for item in pending_routes[:6]:
            lines.append(
                "- {route}: pending={pending} oldest={age} ids={ids} effective_thread={effective} state={state}".format(
                    route=item.get("route_key"),
                    pending=int(item.get("pending_count") or 0),
                    age=format_age(item.get("oldest_pending_age_seconds")),
                    ids=format_task_ids(item.get("pending_task_ids")),
                    effective=item.get("effective_thread_id") or item.get("thread_id") or "",
                    state=item.get("thread_resolution_state") or "",
                )
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Routes with supplement waiting for MCP ack:")
    if supplement_routes:
        for item in supplement_routes[:6]:
            lines.append(
                "- {route}: supplement_waiting={pending} oldest={age} ids={ids} effective_thread={effective} state={state}".format(
                    route=item.get("route_key"),
                    pending=int(item.get("supplement_waiting_count") or 0),
                    age=format_age(item.get("oldest_supplement_waiting_age_seconds")),
                    ids=format_task_ids(item.get("supplement_waiting_task_ids")),
                    effective=item.get("effective_thread_id") or item.get("thread_id") or "",
                    state=item.get("thread_resolution_state") or "",
                )
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Routes with active work:")
    if active_routes:
        for item in active_routes[:6]:
            lines.append(
                "- {route}: active={active} oldest={age} ids={ids}".format(
                    route=item.get("route_key"),
                    active=int(item.get("active_count") or 0),
                    age=format_age(item.get("oldest_active_age_seconds")),
                    ids=format_task_ids(item.get("active_task_ids")),
                )
            )
    else:
        lines.append("- none")

    active_observation = snapshot.get("active_observation") if isinstance(snapshot.get("active_observation"), dict) else {}
    progress_counts = active_observation.get("progress_stage_counts") if isinstance(active_observation.get("progress_stage_counts"), dict) else {}
    observing_items = active_observation.get("observing") if isinstance(active_observation.get("observing"), list) else []
    if progress_counts or observing_items:
        lines.append("")
        lines.append("Active progress observations:")
        if progress_counts:
            stage_text = ", ".join(f"{key}={value}" for key, value in sorted(progress_counts.items()))
            lines.append(f"- stages: {stage_text}")
        for item in observing_items[:6]:
            progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
            stage = str(progress.get("stage") or "no_poll_observation")
            status = str(progress.get("status") or item.get("status") or "")
            tools = int(progress.get("in_progress_tool_count") or 0)
            observed_at = str(progress.get("observed_at") or "")
            lines.append(
                "- {id}: stage={stage} status={status} age={age} tools={tools} observed_at={observed_at}".format(
                    id=item.get("id"),
                    stage=stage,
                    status=status,
                    age=format_age(item.get("age_seconds")),
                    tools=tools,
                    observed_at=observed_at or "-",
                )
            )

    policy = snapshot.get("policy") if isinstance(snapshot.get("policy"), dict) else default_policy
    auto_actions = [issue.get("safe_auto_fix") for issue in issues if issue.get("safe_auto_fix")]
    lines.extend(["", "Safe repair available:"])
    if auto_actions:
        for action in sorted({str(item) for item in auto_actions if item}):
            lines.append(f"- {action}")
        lines.append("- run: python .\\mobile_openclaw_cli.py maintenance repair --apply")
    else:
        lines.append("- none from current findings")
    lines.append("- reply sending/retry still requires: --include-reply-send")

    lines.append("")
    lines.append("Manual-only boundaries:")
    for item in policy.get("manual_only", [])[:8]:
        lines.append(f"- {item}")
    return "\n".join(lines)


