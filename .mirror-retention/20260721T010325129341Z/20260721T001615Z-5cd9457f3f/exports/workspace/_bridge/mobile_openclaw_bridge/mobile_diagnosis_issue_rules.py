#!/usr/bin/env python3
"""Issue-rule groups for OpenClaw bridge maintenance diagnosis.

Ownership: pure issue generation from an already collected maintenance
snapshot. This module groups diagnostic rules by business concern so the
maintenance entrypoint can stay focused on orchestration.
Non-goals: collecting live evidence, repairing state, starting or stopping
processes, and mutating bridge queues.
State behavior: read-only; callers pass snapshot data and receive issue dicts.
Caller context: mobile_maintenance.diagnose_system and future focused doctor
tests that need the same issue semantics.
"""

from __future__ import annotations

from typing import Any

import capability_tokens


def make_issue(
    code: str,
    severity: str,
    summary: str,
    evidence: dict[str, Any] | None = None,
    safe_auto_fix: str = "",
    manual_action: str = "",
    *,
    owner_health_impact: bool = True,
    scope: str = "bridge",
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "summary": summary,
        "evidence": evidence or {},
        "safe_auto_fix": safe_auto_fix,
        "manual_action": manual_action,
        "owner_health_impact": owner_health_impact,
        "scope": scope,
    }


def governance_storage_issues(snapshot: dict[str, Any], *, db_size_warn_bytes: int) -> list[dict[str, Any]]:
    """Return permission, token, DB, event-retention, and account-drift issues."""
    issues: list[dict[str, Any]] = []
    database = snapshot.get("database") if isinstance(snapshot.get("database"), dict) else {}
    permissions = snapshot.get("permission_policy") if isinstance(snapshot.get("permission_policy"), dict) else {}
    capability_tokens_snapshot = snapshot.get("capability_tokens") if isinstance(snapshot.get("capability_tokens"), dict) else {}
    event_noise = snapshot.get("event_noise") if isinstance(snapshot.get("event_noise"), dict) else {}
    event_archive = snapshot.get("event_archive_dry_run") if isinstance(snapshot.get("event_archive_dry_run"), dict) else {}
    control_reply_receipts = (
        snapshot.get("control_reply_receipts")
        if isinstance(snapshot.get("control_reply_receipts"), dict)
        else {}
    )
    account_thread_drift = (
        snapshot.get("openclaw_account_thread_drift")
        if isinstance(snapshot.get("openclaw_account_thread_drift"), dict)
        else {}
    )

    for item in permissions.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append(
            make_issue(
                str(item.get("code") or "permission_policy_issue"),
                str(item.get("severity") or "medium"),
                str(item.get("summary") or "Bridge permission policy has an issue."),
                {
                    "primary_admin_user": permissions.get("primary_admin_user", ""),
                    "allowed_user_count": permissions.get("allowed_user_count", 0),
                    "policy": permissions.get("policy", {}),
                },
                manual_action="Review config.local.json security.allowed_users and OpenClaw primary account binding before changing permissions.",
            )
        )

    capability_doctor = capability_tokens.doctor(capability_tokens_snapshot) if capability_tokens_snapshot else {"issues": []}
    for item in capability_doctor.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append(
            make_issue(
                str(item.get("code") or "capability_token_issue"),
                str(item.get("severity") or "medium"),
                "Temporary capability token governance has an issue.",
                {
                    "grant_id": item.get("grant_id", ""),
                    "capabilities": item.get("capabilities", []),
                    "active_count": capability_tokens_snapshot.get("active_count", 0),
                    "policy": "tokens must stay narrow, expiring, admin-granted, and non-sensitive",
                },
                manual_action="Review capability-token doctor/repair-plan before granting or renewing temporary bridge capabilities.",
            )
        )

    if database and (
        not database.get("exists")
        or str(database.get("integrity_check") or "").lower() not in {"", "ok"}
        or database.get("read_error")
    ):
        issues.append(
            make_issue(
                "database_unhealthy",
                "critical",
                "Bridge SQLite database is missing, unreadable, or failed integrity checks.",
                database,
                manual_action="Stop worker, back up the DB, then repair or restore SQLite before processing queue tasks.",
            )
        )
    elif database and not database.get("under_limit"):
        issues.append(
            make_issue(
                "database_size_high",
                "low",
                "Bridge SQLite database is larger than the maintenance warning threshold, although integrity appears OK.",
                {
                    "path": database.get("path"),
                    "bytes": database.get("bytes"),
                    "threshold_bytes": db_size_warn_bytes,
                    "integrity_check": database.get("integrity_check"),
                    "journal_mode": database.get("journal_mode"),
                },
                manual_action="Review old events/tasks, create a DB backup, then run a deliberate cleanup or VACUUM if needed.",
            )
        )

    if event_noise and not event_noise.get("guard_index_exists"):
        issues.append(
            make_issue(
                "event_noise_guard_index_missing",
                "low",
                "Event noise guard index is missing; repeated diagnostic events may increase DB write pressure.",
                event_noise,
                manual_action="Restart or initialize the queue schema after backing up files so idx_mobile_events_noise_guard is created.",
            )
        )
    if event_archive and int(event_archive.get("candidate_count") or 0) > 50000:
        issues.append(
            make_issue(
                "historical_event_noise_archive_available",
                "low",
                "Historical diagnostic event noise can be archived later to shrink the bridge DB.",
                {
                    "candidate_count": event_archive.get("candidate_count"),
                    "retention_hours": event_archive.get("retention_hours"),
                    "top_event_type": (event_archive.get("by_event_type") or [{}])[0],
                },
                manual_action="Use a backup-first archive/VACUUM maintenance action in a quiet window; current repair does not delete events.",
            )
        )

    if control_reply_receipts and not control_reply_receipts.get("ok"):
        missing_terminal_count = int(control_reply_receipts.get("missing_terminal_count") or 0)
        action_without_outbox_count = int(control_reply_receipts.get("action_without_outbox_count") or 0)
        missing_receipt_action_count = int(control_reply_receipts.get("missing_receipt_action_count") or 0)
        parse_error_count = int(control_reply_receipts.get("parse_error_count") or 0)
        severity = "high" if missing_terminal_count else "medium"
        issues.append(
            make_issue(
                "control_reply_receipt_contract_broken",
                severity,
                "Recent mobile control commands do not have complete durable reply receipt evidence.",
                {
                    "missing_terminal_count": missing_terminal_count,
                    "action_without_outbox_count": action_without_outbox_count,
                    "missing_receipt_action_count": missing_receipt_action_count,
                    "parse_error_count": parse_error_count,
                    "missing_terminal": control_reply_receipts.get("missing_terminal", [])[:5],
                    "action_without_outbox": control_reply_receipts.get("action_without_outbox", [])[:5],
                    "missing_receipt_actions": control_reply_receipts.get("missing_receipt_actions", [])[:5],
                },
                manual_action=(
                    "Do not infer command success from the action event alone. "
                    "Use the control receipt trail to confirm control_reply_sent or control_reply_failed, "
                    "then retry or repair the specific control path if the user did not receive a reply."
                ),
            )
        )

    if account_thread_drift and int(account_thread_drift.get("missing_count") or 0) > 0:
        issues.append(
            make_issue(
                "openclaw_account_thread_drift",
                "medium",
                "Some persisted OpenClaw Weixin accounts do not have dedicated Codex thread routes yet.",
                {
                    "missing_count": account_thread_drift.get("missing_count"),
                    "missing_routes": account_thread_drift.get("missing_routes", [])[:8],
                    "skipped": account_thread_drift.get("skipped", [])[:8],
                },
                safe_auto_fix="sync_openclaw_account_onboarding",
                manual_action="Review maintenance repair dry-run, then apply account onboarding sync to create missing thread routes.",
            )
        )
    return issues


def bridge_runtime_route_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return runtime, route, worker, and direct mobile MCP health issues."""
    issues: list[dict[str, Any]] = []
    ports = snapshot.get("ports", {})
    processes = snapshot.get("processes", {})
    scheduled = snapshot.get("scheduled_tasks", {})
    control = snapshot.get("control") if isinstance(snapshot.get("control"), dict) else {}
    pending = snapshot.get("pending") if isinstance(snapshot.get("pending"), list) else []
    cdp_route = snapshot.get("cdp_route") if isinstance(snapshot.get("cdp_route"), dict) else {}
    mobile_mcp = snapshot.get("mobile_mcp") if isinstance(snapshot.get("mobile_mcp"), dict) else {}
    mobile_mcp_fallback = snapshot.get("mobile_mcp_fallback") if isinstance(snapshot.get("mobile_mcp_fallback"), dict) else {}
    desktop_session_mcp = snapshot.get("desktop_session_mcp") if isinstance(snapshot.get("desktop_session_mcp"), dict) else {}

    if not ports.get("openclaw_gateway", {}).get("ok"):
        issues.append(
            make_issue(
                "gateway_port_down",
                "critical",
                "OpenClaw Gateway port is not listening; phone messages cannot enter the queue.",
                ports.get("openclaw_gateway", {}),
                safe_auto_fix="start_openclaw_gateway_task",
            )
        )
    if control.get("paused") or control.get("stop_request_exists"):
        issues.append(
            make_issue(
                "delivery_globally_paused",
                "critical",
                "Global PAUSE or STOP_REQUEST is active; worker will not dispatch mobile tasks.",
                control,
                manual_action="If this was intentional, leave it. Otherwise run mode resume/control resume after confirming no turn should remain stopped.",
            )
        )

    worker_state = str(scheduled.get("worker", {}).get("state") or "").strip().lower()
    worker_start_safe = not control.get("paused") and not control.get("stop_request_exists") and worker_state != "disabled"
    if not processes.get("worker", {}).get("ok") or int(processes.get("worker", {}).get("count") or 0) == 0:
        issues.append(
            make_issue(
                "worker_not_running",
                "critical",
                "Mobile worker is not running; queued messages will not be processed.",
                processes.get("worker", {}),
                safe_auto_fix="start_worker_task" if worker_start_safe else "",
                manual_action=""
                if worker_start_safe
                else "Worker is stopped by PAUSE/STOP_REQUEST or disabled task state; resume/enable it deliberately before starting.",
            )
        )
    if scheduled.get("worker", {}).get("skipped"):
        pass
    elif scheduled.get("worker", {}).get("ok") and worker_state == "disabled":
        issues.append(
            make_issue(
                "worker_task_disabled",
                "high",
                "Worker scheduled task exists but is disabled; automatic restart/start will not be durable.",
                scheduled.get("worker", {}),
                manual_action="Enable MobileOpenClawBridgeWorker after confirming the bridge should resume.",
            )
        )
    if scheduled.get("worker", {}).get("skipped"):
        pass
    elif not scheduled.get("worker", {}).get("ok"):
        issues.append(
            make_issue(
                "worker_task_missing_or_broken",
                "high",
                "Worker scheduled task is missing or unhealthy.",
                scheduled.get("worker", {}),
                manual_action="Run install-worker-task.ps1 after reviewing task action.",
            )
        )
    if scheduled.get("gateway", {}).get("skipped"):
        pass
    elif not scheduled.get("gateway", {}).get("ok"):
        issues.append(
            make_issue(
                "gateway_task_missing_or_broken",
                "high",
                "OpenClaw Gateway scheduled task is missing or unhealthy.",
                scheduled.get("gateway", {}),
                manual_action="Run install-openclaw-gateway-task.ps1 after reviewing task action.",
            )
        )

    if not ports.get("codex_app_server", {}).get("ok"):
        issues.append(
            make_issue(
                "codex_app_server_down",
                "high",
                "Codex app-server is unavailable; background thread delivery cannot work.",
                ports.get("codex_app_server", {}),
                manual_action="Start or repair Codex app-server before app-server routes can dispatch.",
            )
        )
    cdp_route_skipped = bool(cdp_route.get("skipped"))
    cdp_layer = str(cdp_route.get("layer") or "").strip().lower()
    cdp_route_not_ready = bool(not cdp_route_skipped and cdp_layer and cdp_layer != "ready")
    if not ports.get("codex_cdp", {}).get("ok") or cdp_route_not_ready:
        primary_pending = [item for item in pending if item.get("account") == "primary"]
        issues.append(
            make_issue(
                "codex_cdp_unavailable",
                "high" if primary_pending else "medium",
                "Codex CDP route is unavailable; primary/current-window route cannot dispatch until the visible route is restored.",
                cdp_route or {"port": ports.get("codex_cdp", {}), "primary_pending_count": len(primary_pending)},
                manual_action=(
                    "Restore Codex Desktop visible CDP through start-codex-desktop-elevated.ps1 "
                    "with CODEX_CDP_PORT. Do not use a plain non-admin Codex launch. "
                    "Switching primary to app-server is manual-only and requires explicit approval."
                ),
            )
        )

    if mobile_mcp and mobile_mcp.get("skipped"):
        pass
    elif mobile_mcp and not mobile_mcp.get("ok"):
        issues.append(
            make_issue(
                "mobile_mcp_direct_smoke_failed",
                "medium",
                "Mobile bridge MCP server did not pass a direct stdio JSON-RPC smoke check.",
                mobile_mcp,
                manual_action="Fix MCP server startup/stdout discipline before relying on in-turn supplement polling.",
            )
        )
    elif mobile_mcp.get("ok"):
        tool_names = set(str(item) for item in (mobile_mcp.get("tool_names") or []))
        expected = {"bridge.health", "bridge.poll_updates", "bridge.ack_message", "bridge.get_pending_batch"}
        if not expected.issubset(tool_names):
            issues.append(
                make_issue(
                    "mobile_mcp_toolset_incomplete",
                    "medium",
                    "Mobile bridge MCP direct smoke succeeded, but required tools are missing.",
                    mobile_mcp,
                    manual_action="Restart Codex/MCP after verifying mobile_bridge_mcp_server.py exposes the expected tools.",
                )
            )
    if mobile_mcp_fallback and mobile_mcp_fallback.get("skipped"):
        pass
    elif mobile_mcp_fallback and not mobile_mcp_fallback.get("ok"):
        issues.append(
            make_issue(
                "mobile_mcp_local_fallback_unavailable",
                "high",
                "Mobile MCP current-session recovery fallback is unavailable; if Codex MCP transport closes, supplements cannot be read/acked through the local fallback.",
                mobile_mcp_fallback,
                manual_action="Fix local MCP stdio fallback before relying on supplement recovery after Transport closed.",
            )
        )
    if desktop_session_mcp and desktop_session_mcp.get("skipped"):
        pass
    elif desktop_session_mcp and not desktop_session_mcp.get("ok"):
        layer = str(desktop_session_mcp.get("layer") or "")
        severity = "high" if layer in {"desktop_mcp_children_missing", "desktop_bridge_codex_version_split"} else "medium"
        issues.append(
            make_issue(
                "codex_desktop_session_mcp_stale",
                severity,
                "Current Codex Desktop session has stale or incomplete MCP host evidence; tool names may remain visible while calls fail with Transport closed.",
                {
                    "layer": layer,
                    "desktop_version": desktop_session_mcp.get("desktop_version"),
                    "desktop_app_server_versions": desktop_session_mcp.get("desktop_app_server_versions") or [],
                    "bridge_app_server_versions": desktop_session_mcp.get("bridge_app_server_versions") or [],
                    "desktop_app_server_count": desktop_session_mcp.get("desktop_app_server_count"),
                    "mobile_mcp_child_count": desktop_session_mcp.get("mobile_mcp_child_count"),
                    "retired_member_process_count": desktop_session_mcp.get("retired_member_process_count"),
                    "retired_members": desktop_session_mcp.get("retired_members") or [],
                    "node_repl_child_count": desktop_session_mcp.get("node_repl_child_count"),
                    "version_split": desktop_session_mcp.get("version_split"),
                },
                manual_action=(
                    "Use the local stdio fallback for urgent supplement get/ack. For root recovery, first use a Codex MCP session reload if one is available; "
                    "if no reload route is exposed, perform a controlled Codex Desktop restart through the configured elevated startup path and validate live tool calls afterward."
                ),
            )
        )
    return issues


def codex_tooling_issues(
    snapshot: dict[str, Any],
    *,
    probe_evidence_state_fn: Any,
) -> list[dict[str, Any]]:
    """Return config, plugin, guard, and GUI automation issues for Codex tooling."""
    issues: list[dict[str, Any]] = []
    codex_mcp_config = snapshot.get("codex_mcp_config") if isinstance(snapshot.get("codex_mcp_config"), dict) else {}
    codex_plugins = snapshot.get("codex_plugins") if isinstance(snapshot.get("codex_plugins"), dict) else {}
    codex_config_guard = snapshot.get("codex_config_guard") if isinstance(snapshot.get("codex_config_guard"), dict) else {}
    gui_automation = snapshot.get("gui_automation") if isinstance(snapshot.get("gui_automation"), dict) else {}

    if codex_mcp_config and codex_mcp_config.get("skipped"):
        pass
    elif codex_mcp_config and not codex_mcp_config.get("ok"):
        repairable_missing = codex_mcp_config.get("repairable_missing") or []
        repairable_drifted = codex_mcp_config.get("repairable_drifted") or []
        safe_auto_fix = "repair_codex_mcp_config" if repairable_missing or repairable_drifted else ""
        issues.append(
            make_issue(
                "codex_mcp_config_incomplete",
                "high"
                if any(str(item.get("name") or "") == "mobile-openclaw-bridge" for item in codex_mcp_config.get("missing", []))
                else "medium",
                "Codex global config is missing or drifting expected MCP server registrations; current sessions may not expose required tools.",
                {
                    "path": codex_mcp_config.get("path"),
                    "registered_servers": codex_mcp_config.get("registered_servers"),
                    "missing": [item.get("name") for item in codex_mcp_config.get("missing", [])],
                    "drifted": [
                        {"name": item.get("name"), "issues": item.get("issues")}
                        for item in codex_mcp_config.get("drifted", [])
                    ],
                    "repairable_missing": [item.get("name") for item in repairable_missing],
                    "repairable_drifted": [item.get("name") for item in repairable_drifted],
                },
                safe_auto_fix=safe_auto_fix,
                manual_action=(
                    "Run maintenance repair --apply to repair known catalog MCP entries with a backup, "
                    "then restart Codex Desktop so the current session receives the tools. Existing drifted entries need manual review."
                ),
            )
        )

    plugin_evidence = probe_evidence_state_fn(
        codex_plugins,
        profile="deep" if snapshot.get("deep_probes") else "quick",
    )
    if codex_plugins and plugin_evidence.get("state") in {"quick_skipped", "stale_observation", "unknown"}:
        pass
    elif codex_plugins and plugin_evidence.get("current_failure"):
        if codex_plugins.get("plugin_table_missing"):
            issues.append(
                make_issue(
                    "codex_plugin_table_missing",
                    "high",
                    "Codex global config is missing the entire [plugins] table; this looks like a wholesale config reset rather than a few missing entries.",
                    {
                        "config_path": codex_plugins.get("config_path"),
                        "plugin_table_present": codex_plugins.get("plugin_table_present"),
                        "plugin_table_population": codex_plugins.get("plugin_table_population"),
                        "missing_enabled_plugins": codex_plugins.get("missing_enabled_plugins") or [],
                        "evidence_state": plugin_evidence,
                    },
                    manual_action=(
                        "Restore the [plugins] table from a marked backup before any sync or repair task can run, then restart Codex Desktop."
                    ),
                )
            )
        missing_enabled = [str(item) for item in (codex_plugins.get("missing_enabled_plugins") or []) if str(item).strip()]
        if missing_enabled:
            issues.append(
                make_issue(
                    "codex_plugin_enablement_incomplete",
                    "medium",
                    "Codex global config is missing expected plugin enablement entries; capability parity can drift even when plugin cache is present.",
                    {
                        "config_path": codex_plugins.get("config_path"),
                        "missing_enabled_plugins": missing_enabled,
                        "missing_cache_plugins": codex_plugins.get("missing_cache_plugins") or [],
                        "missing_manifest_plugins": codex_plugins.get("missing_manifest_plugins") or [],
                        "evidence_state": plugin_evidence,
                    },
                    safe_auto_fix="repair_codex_plugin_enablement",
                    manual_action=(
                        "Run maintenance repair --apply to restore only missing plugin enablement entries with a backup, "
                        "then restart Codex Desktop so the current session reloads the plugin-backed capabilities."
                    ),
                )
            )

    if codex_config_guard and codex_config_guard.get("skipped"):
        pass
    elif codex_config_guard and not codex_config_guard.get("ok"):
        guard_issues = codex_config_guard.get("issues") if isinstance(codex_config_guard.get("issues"), list) else []
        high_issues = [item for item in guard_issues if str(item.get("severity") or "") == "high"]
        if high_issues:
            issues.append(
                make_issue(
                    "codex_config_guard_drift",
                    "high",
                    "Codex config guard reported an external Codex startup/config issue. The bridge surfaces it for visibility but does not own or duplicate that health decision.",
                    {
                        "issues": high_issues,
                        "policy": "merge-only repair from codex_startup_baseline.json",
                    },
                    safe_auto_fix="repair_codex_config_guard",
                    manual_action=(
                        "Run maintenance repair --apply or codex-config-guard run-once --apply, "
                        "then restart Codex Desktop so current-session plugins and MCP tools reload."
                    ),
                    owner_health_impact=False,
                    scope="external_dependency",
                )
            )

    if gui_automation and gui_automation.get("skipped"):
        pass
    elif gui_automation and not gui_automation.get("ok"):
        runtime = gui_automation.get("runtime") if isinstance(gui_automation.get("runtime"), dict) else {}
        ocr = gui_automation.get("ocr") if isinstance(gui_automation.get("ocr"), dict) else {}
        issues.append(
            make_issue(
                "gui_automation_unhealthy",
                "medium",
                "GUI automation runtime or OCR backend is not healthy.",
                {
                    "registered": gui_automation.get("registered"),
                    "command": gui_automation.get("command"),
                    "missing_runtime_modules": runtime.get("missing"),
                    "ocr_ready": ocr.get("ready"),
                    "ocr_error": ocr.get("error"),
                    "config_drifted": gui_automation.get("config_drifted"),
                },
                safe_auto_fix="repair_codex_mcp_config" if gui_automation.get("config_drifted") else "",
                manual_action=(
                    "If config drift was repaired, restart Codex Desktop. If runtime modules or OCR files are still missing, "
                    "install dependencies in the configured GUI/OCR Python environments after backup."
                ),
            )
        )
    return issues


def app_server_mcp_issues(
    snapshot: dict[str, Any],
    *,
    app_server_mcp_issue_is_actionable_fn: Any,
) -> list[dict[str, Any]]:
    """Return bridge-owned app-server MCP baseline issues."""
    issues: list[dict[str, Any]] = []
    counts = snapshot.get("counts", {}).get("by_status", {})
    app_server_mcp = snapshot.get("app_server_mcp") if isinstance(snapshot.get("app_server_mcp"), dict) else {}
    if (
        app_server_mcp
        and not app_server_mcp.get("skipped")
        and not app_server_mcp.get("ok")
        and app_server_mcp_issue_is_actionable_fn(snapshot, app_server_mcp)
    ):
        severity = "medium"
        if int(counts.get("sent_to_codex") or 0) or int(counts.get("queued_for_codex") or 0):
            severity = "high"
        issues.append(
            make_issue(
                "codex_app_server_mcp_baseline_unhealthy",
                severity,
                "The bridge-owned Codex app-server listener does not have a clean mobile MCP baseline; backup app-server turns may fail MCP calls.",
                app_server_mcp,
                manual_action="Restart only the bridge-owned app-server listener on the configured port, then redeliver affected app-server tasks.",
            )
        )
    return issues


def resource_memory_hygiene_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return resource-process, backup-hygiene, and memory-governance issues."""
    issues: list[dict[str, Any]] = []
    resource_processes = snapshot.get("resource_processes") if isinstance(snapshot.get("resource_processes"), dict) else {}
    backup_hygiene = snapshot.get("backup_hygiene") if isinstance(snapshot.get("backup_hygiene"), dict) else {}
    memory_governance = snapshot.get("memory_governance") if isinstance(snapshot.get("memory_governance"), dict) else {}

    if resource_processes and resource_processes.get("skipped"):
        pass
    elif resource_processes:
        resource_issues = resource_processes.get("issues") if isinstance(resource_processes.get("issues"), list) else []
        risk_issues = [item for item in resource_issues if item.get("severity") in {"blocker", "risk"}]
        advisory_issues = [item for item in resource_issues if item.get("severity") == "advisory"]
        owner_issues = [item for item in resource_issues if item.get("code") == "codex_app_server_owner_unhealthy"]
        repair_preview = resource_processes.get("repair_plan_preview") if isinstance(resource_processes.get("repair_plan_preview"), dict) else {}
        resource_details = {
            "risk_groups": [item.get("group") for item in risk_issues[:12]],
            "advisory_groups": [item.get("group") for item in advisory_issues[:12]],
            "matched_process_count": (resource_processes.get("metrics") or {}).get("matched_process_count"),
            "root_instance_count": (resource_processes.get("metrics") or {}).get("root_instance_count"),
            "matched_working_set_mb": (resource_processes.get("metrics") or {}).get("matched_working_set_mb"),
            "repair_plan_preview": repair_preview,
        }
        if owner_issues:
            issues.append(
                make_issue(
                    "codex_app_server_owner_unhealthy",
                    "medium",
                    "Bridge app-server 18791 is not owned by exactly one current Codex app-server; backup app-server routing can drift or duplicate MCP hosts.",
                    {
                        "owner": owner_issues[0].get("owner"),
                        "resource_metrics": {
                            "codex_app_server_owner_healthy": (resource_processes.get("metrics") or {}).get("codex_app_server_owner_healthy"),
                            "codex_app_server_owner_issue": (resource_processes.get("metrics") or {}).get("codex_app_server_owner_issue"),
                            "codex_app_server_owner_count": (resource_processes.get("metrics") or {}).get("codex_app_server_owner_count"),
                        },
                    },
                    manual_action="Use the governed dashboard stack launcher to replace only the stale bridge app-server listener; do not manually kill unrelated Codex or MCP processes.",
                )
            )
            risk_issues = [item for item in risk_issues if item.get("code") != "codex_app_server_owner_unhealthy"]
        if risk_issues:
            orphan_roots = int(repair_preview.get("orphan_candidate_root_count") or 0)
            non_protected_orphan_roots = int(repair_preview.get("non_protected_orphan_candidate_root_count") or 0)
            if bool(repair_preview.get("apply_supported")) and non_protected_orphan_roots > 0:
                issue_code = "resource_process_fanout_cleanup_candidate"
                manual_action = "Run `resource-process cleanup --min-age-minutes 15` first; apply only after dry-run shows revalidated non-protected orphan roots."
            elif orphan_roots > 0:
                issue_code = "resource_process_fanout_protected_review"
                manual_action = "Review protected/stale candidates manually; do not auto-stop bridge, Reasonix, Codex, or active MCP roots."
            else:
                issue_code = "resource_process_fanout_transient_or_unclassified"
                manual_action = "Recheck after the startup/tool-probe window; if it persists, inspect startup-sources before cleanup because no safe orphan root is currently selected."
            issues.append(
                make_issue(
                    issue_code,
                    "medium",
                    "Resource/MCP process fanout is above the risk threshold; this can add memory load and duplicate tool hosts.",
                    resource_details,
                    manual_action=manual_action,
                )
            )
        elif advisory_issues:
            issues.append(
                make_issue(
                    "resource_process_fanout_advisory",
                    "low",
                    "Some resource/MCP process groups have duplicate instances, but current evidence is advisory only.",
                    {
                        "advisory_groups": [item.get("group") for item in advisory_issues[:12]],
                        "matched_process_count": (resource_processes.get("metrics") or {}).get("matched_process_count"),
                        "root_instance_count": (resource_processes.get("metrics") or {}).get("root_instance_count"),
                        "matched_working_set_mb": (resource_processes.get("metrics") or {}).get("matched_working_set_mb"),
                        "repair_plan_preview": resource_processes.get("repair_plan_preview"),
                    },
                    manual_action="Prefer singleton startup-source governance over manual cleanup; use resource-process repair-plan for dry-run evidence.",
                )
            )
        elif not resource_processes.get("ok"):
            issues.append(
                make_issue(
                    "resource_process_observer_failed",
                    "low",
                    "Resource/MCP process doctor could not complete its read-only process scan.",
                    {
                        "layer": resource_processes.get("layer"),
                        "reason": resource_processes.get("reason"),
                        "error_type": resource_processes.get("error_type"),
                    },
                    manual_action="Run `resource-process doctor` directly and check PowerShell/CIM availability before relying on fanout metrics.",
                )
            )

    if backup_hygiene and not backup_hygiene.get("skipped"):
        backup_issues = backup_hygiene.get("issues") if isinstance(backup_hygiene.get("issues"), list) else []
        if backup_issues:
            issues.append(
                make_issue(
                    "backup_hygiene_review",
                    "low" if all(item.get("severity") == "advisory" for item in backup_issues) else "medium",
                    "Source-tree backup files are accumulating and should be reviewed for archiving out of the hot search path.",
                    {
                        "backup_count": (backup_hygiene.get("metrics") or {}).get("backup_count"),
                        "archive_candidate_count": (backup_hygiene.get("metrics") or {}).get("archive_candidate_count"),
                        "total_size_mb": (backup_hygiene.get("metrics") or {}).get("total_size_mb"),
                        "repair_plan_preview": backup_hygiene.get("repair_plan_preview"),
                    },
                    manual_action="Review the backup hygiene dry-run first; keep the current no-delete boundary unless you explicitly approve an archive apply mode.",
                )
            )

    if memory_governance and not memory_governance.get("skipped"):
        memory_issues = memory_governance.get("issues") if isinstance(memory_governance.get("issues"), list) else []
        if memory_issues:
            risk_issues = [item for item in memory_issues if item.get("severity") == "risk"]
            issues.append(
                make_issue(
                    "memory_governance_review",
                    "medium" if risk_issues else "low",
                    "Memory governance reports pending review or memory-loop health issues.",
                    {
                        "status": memory_governance.get("status"),
                        "summary": memory_governance.get("summary"),
                        "issue_codes": [str(item.get("code") or "") for item in memory_issues[:12]],
                    },
                    manual_action="Run `python _bridge\\memory_governance.py doctor` and review candidate notes before promoting durable memory/skill/baseline changes.",
                )
            )
    return issues


def queue_delivery_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return queue, active-task observation, reply delivery, and live-state issues."""
    issues: list[dict[str, Any]] = []
    pending = snapshot.get("pending") if isinstance(snapshot.get("pending"), list) else []
    reply_problems = snapshot.get("reply_problems") if isinstance(snapshot.get("reply_problems"), list) else []
    session_timeout_misclassified = (
        snapshot.get("session_timeout_misclassified")
        if isinstance(snapshot.get("session_timeout_misclassified"), list)
        else []
    )
    routes = snapshot.get("routes") if isinstance(snapshot.get("routes"), dict) else {}
    events = snapshot.get("recent_events") if isinstance(snapshot.get("recent_events"), dict) else {}
    cdp_route = snapshot.get("cdp_route") if isinstance(snapshot.get("cdp_route"), dict) else {}
    active_observation = (
        snapshot.get("active_observation") if isinstance(snapshot.get("active_observation"), dict) else {}
    )
    app_server_materialization_lag = (
        snapshot.get("app_server_materialization_lag")
        if isinstance(snapshot.get("app_server_materialization_lag"), list)
        else []
    )
    cdp_visible_unconfirmed_observing = (
        snapshot.get("cdp_visible_unconfirmed_observing")
        if isinstance(snapshot.get("cdp_visible_unconfirmed_observing"), list)
        else []
    )
    top_pending = snapshot.get("top_pending_routes") if isinstance(snapshot.get("top_pending_routes"), list) else []
    top_active = snapshot.get("top_active_routes") if isinstance(snapshot.get("top_active_routes"), list) else []
    top_accounts_data = snapshot.get("top_accounts") if isinstance(snapshot.get("top_accounts"), list) else []

    normal_pending = [item for item in pending if item.get("pending_kind") != "supplement_waiting_mcp_ack"]
    supplement_waiting = [item for item in pending if item.get("pending_kind") == "supplement_waiting_mcp_ack"]
    if normal_pending:
        issues.append(
            make_issue(
                "pending_backlog",
                "medium",
                "There are pending tasks waiting for delivery.",
                {
                    "pending_count": len(normal_pending),
                    "sample_ids": [item.get("id") for item in normal_pending[:8]],
                    "top_pending_routes": top_pending[:5],
                    "top_accounts": top_accounts_data[:5],
                },
                manual_action="Use doctor route details to find whether backlog is CDP, route busy, thread unavailable, or retry cooldown.",
            )
        )
    if supplement_waiting:
        issues.append(
            make_issue(
                "supplement_waiting_mcp_ack",
                "low",
                "Some pending rows are already published as same-thread supplements and are waiting for the active Codex turn to read/ack them through MCP.",
                {
                    "count": len(supplement_waiting),
                    "sample_ids": [item.get("id") for item in supplement_waiting[:8]],
                    "base_task_ids": [
                        (item.get("supplement") or {}).get("base_task_id")
                        for item in supplement_waiting[:8]
                    ],
                },
                manual_action="Inspect the active owner turn and MCP tool status; do not dispatch these rows as separate primary tasks while their owner is healthy.",
            )
        )
    if app_server_materialization_lag:
        recovered_items = [
            item for item in app_server_materialization_lag
            if str(item.get("event_type") or "") == "recovery_queued_rehydrated_from_materialized_turn"
        ]
        failed_items = [
            item for item in app_server_materialization_lag
            if str(item.get("event_type") or "") == "delivery_failed_reverted_to_pending"
        ]
        unresolved = [
            item for item in failed_items
            if str(item.get("status") or "") in {"pending", "queued_for_codex"}
        ]
        issues.append(
            make_issue(
                "app_server_turn_materialization_lag",
                "low" if recovered_items and not unresolved else "medium",
                "Recent app-server dispatches returned turn ids before turns/list exposed them; require app-server readback-confirmed materialized-turn or owned-marker evidence before considering rehydrate, and avoid redelivery while evidence is ambiguous.",
                {
                    "recent_count": len(app_server_materialization_lag),
                    "recovered_count": len(recovered_items),
                    "unresolved_count": len(unresolved),
                    "sample_ids": [item.get("id") for item in app_server_materialization_lag[:8]],
                    "samples": app_server_materialization_lag[:8],
                },
                manual_action=(
                    "Let active recovery run first. If unresolved rows remain pending after the retry window, inspect exact task events and app-server readback; "
                    "do not manually resend unless no matching materialized turn, owned marker, or owned result exists."
                ),
            )
        )
    if cdp_visible_unconfirmed_observing:
        active_items = [
            item for item in cdp_visible_unconfirmed_observing
            if str(item.get("status") or "") in {"sent_to_codex", "processing"}
        ]
        issues.append(
            make_issue(
                "cdp_visible_submission_unconfirmed_observing",
                "low",
                "Recent visible-CDP submissions were accepted by transport but lacked visible prompt confirmation; this is diagnostic evidence, not a delivery failure by itself.",
                {
                    "recent_count": len(cdp_visible_unconfirmed_observing),
                    "active_count": len(active_items),
                    "sample_ids": [item.get("id") for item in cdp_visible_unconfirmed_observing[:8]],
                    "samples": cdp_visible_unconfirmed_observing[:8],
                },
                manual_action=(
                    "Let owned-result polling run and inspect exact ack/result markers before retrying; "
                    "use CDP route diagnostics if this repeats, but do not manually resend solely from missing visible evidence."
                ),
            )
        )

    observing_active = active_observation.get("observing") if isinstance(active_observation.get("observing"), list) else []
    waiting_followup_active = active_observation.get("waiting_followup") if isinstance(active_observation.get("waiting_followup"), list) else []
    blocked_active = active_observation.get("blocked") if isinstance(active_observation.get("blocked"), list) else []
    unknown_active = active_observation.get("unknown") if isinstance(active_observation.get("unknown"), list) else []
    if waiting_followup_active:
        issues.append(
            make_issue(
                "active_tasks_waiting_followup_redelivery",
                "low",
                "Some aged primary visible-CDP tasks are intentionally parked, waiting for a new same-thread follow-up before retrying delivery.",
                {
                    "task_ids": [item.get("id") for item in waiting_followup_active],
                    "count": len(waiting_followup_active),
                    "threshold_seconds": active_observation.get("threshold_seconds"),
                    "tasks": waiting_followup_active[:10],
                },
                manual_action="Do not redispatch these rows manually unless you want to override FIFO continuation behavior; the next same-thread user message will release them for retry.",
            )
        )
    if observing_active:
        issues.append(
            make_issue(
                "active_tasks_observing",
                "low",
                "Some sent_to_codex/processing tasks are older than the observation threshold, but their delivery channel is reachable; use progress_stage_counts to distinguish no-output thinking, tool work, and terminal/retry signals before intervening.",
                {
                    "task_ids": [item.get("id") for item in observing_active],
                    "count": len(observing_active),
                    "threshold_seconds": active_observation.get("threshold_seconds"),
                    "progress_stage_counts": active_observation.get("progress_stage_counts") or {},
                    "top_active_routes": top_active[:5],
                    "tasks": observing_active[:10],
                },
                manual_action="Observe the corresponding Codex turn/thread and wait for owned result polling; do not fail or reset solely because of elapsed time.",
            )
        )
    if blocked_active:
        issues.append(
            make_issue(
                "active_task_observation_blocked",
                "medium",
                "Some aged active tasks cannot be observed because their delivery route is unavailable.",
                {
                    "task_ids": [item.get("id") for item in blocked_active],
                    "count": len(blocked_active),
                    "threshold_seconds": active_observation.get("threshold_seconds"),
                    "tasks": blocked_active[:10],
                },
                manual_action="Restore the route health first, then inspect the owned Codex result before retrying, failing, or resetting these tasks.",
            )
        )
    if unknown_active:
        issues.append(
            make_issue(
                "active_task_observation_unknown",
                "low",
                "Some aged active tasks use an unknown delivery mode; elapsed time alone is not enough to mark them failed.",
                {
                    "task_ids": [item.get("id") for item in unknown_active],
                    "count": len(unknown_active),
                    "tasks": unknown_active[:10],
                },
                manual_action="Inspect the task runtime and delivery mode before deciding whether recovery is needed.",
            )
        )
    for key, route in routes.items():
        if int(route.get("active_count") or 0) > 0 and int(route.get("pending_count") or 0) > 0:
            oldest_pending = int(route.get("oldest_pending_age_seconds") or 0)
            issues.append(
                make_issue(
                    "route_has_active_and_pending",
                    "medium" if oldest_pending >= 600 else "low",
                    "A route has active work plus queued follow-up messages; this can be normal while Codex is still thinking.",
                    {
                        "route_key": key,
                        "account": route.get("account"),
                        "delivery_mode": route.get("delivery_mode"),
                        "thread_id": route.get("thread_id"),
                        "active_count": route.get("active_count"),
                        "pending_count": route.get("pending_count"),
                        "active_task_ids": route.get("active_task_ids", [])[:5],
                        "pending_task_ids": route.get("pending_task_ids", [])[:5],
                    },
                    manual_action="Confirm result polling and supplement/batch handling are progressing before treating this as stuck.",
                )
            )

    if reply_problems:
        issues.append(
            make_issue(
                "reply_delivery_backlog",
                "medium",
                "Some completed or outbound replies are waiting/retrying/failed on the Weixin side.",
                {"count": len(reply_problems), "sample_ids": [item.get("id") for item in reply_problems[:8]]},
                safe_auto_fix="recover_stale_reply_sending_and_retry_due_reply_pending",
            )
        )
        token_present_rejected = [
            item
            for item in reply_problems
            if item.get("diagnostic_category") == "token_present_but_send_rejected"
        ]
        if token_present_rejected:
            issues.append(
                make_issue(
                    "reply_send_token_present_but_rejected",
                    "medium",
                    "Some replies had a context token, but Weixin/OpenClaw still rejected the send.",
                    {
                        "count": len(token_present_rejected),
                        "sample_ids": [item.get("id") for item in token_present_rejected[:8]],
                        "accounts": sorted(
                            {
                                str(item.get("account") or "")
                                for item in token_present_rejected
                                if item.get("account")
                            }
                        ),
                    },
                    manual_action="Do not assume the token is missing. Compare latest inbound context, plain-text send, small-file send, and media send before retrying or changing routes.",
                )
            )
    if session_timeout_misclassified:
        issues.append(
            make_issue(
                "openclaw_weixin_session_expired_delivery_misclassified",
                "medium",
                "Some replies were recorded as pushed even though OpenClaw returned errcode=-14 session timeout.",
                {
                    "count": len(session_timeout_misclassified),
                    "sample_ids": [item.get("id") for item in session_timeout_misclassified[:8]],
                    "accounts": sorted({str(item.get("account") or "") for item in session_timeout_misclassified if item.get("account")}),
                },
                manual_action="Do not bulk resend. Confirm the affected task/user, then recover through reply_pending or a scoped explicit resend only after fresh Weixin context is available.",
            )
        )

    live = snapshot.get("dashboard_live_state", {})
    if not live.get("ok"):
        issues.append(
            make_issue(
                "dashboard_live_state_stale",
                "low",
                "Dashboard live state is stale or disconnected.",
                live,
                safe_auto_fix="cleanup_dashboard_live_tmp",
            )
        )
    elif live.get("tmp_files"):
        issues.append(
            make_issue(
                "dashboard_live_tmp_files",
                "low",
                "Dashboard live-state temp files are present and can be cleaned.",
                {"count": len(live.get("tmp_files") or []), "sample": (live.get("tmp_files") or [])[:5]},
                safe_auto_fix="cleanup_dashboard_live_tmp",
            )
        )
    if events.get("thread_delivery_visible_cdp_probe_failed", 0) >= 5:
        issues.append(
            make_issue(
                "repeated_cdp_probe_failures",
                "medium",
                "Recent events show repeated visible CDP probe failures.",
                cdp_route or {"recent_count": events.get("thread_delivery_visible_cdp_probe_failed")},
                manual_action="Do not treat this as real busy; repair CDP startup or isolate primary route.",
            )
        )
    return issues
