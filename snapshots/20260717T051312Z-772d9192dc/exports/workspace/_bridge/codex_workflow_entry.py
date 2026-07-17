#!/usr/bin/env python3
"""Machine-first workflow lifecycle facade over existing owner systems.

Planning and closeout remain local governance operations. Run/status/wait/cancel
delegate through workflow_owner_facade and preserve owner permission and state
boundaries rather than becoming a central executor.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import memory_work_notes

from bounded_output import bounded_payload, bounded_value
from maintenance_capability_registry import build_index as build_maintenance_index
from maintenance_capability_registry import doctor as maintenance_registry_doctor
from maintenance_capability_registry import metrics as maintenance_registry_metrics
from maintenance_capability_registry import query_registry as query_maintenance_registry
from codex_environment_mirror import execute as execute_mirror_command
from workflow_orchestrator import build_plan
from workflow_owner_facade import (
    action_from_run_ref,
    attach_owner_result,
    build_action,
    execute_lifecycle,
    execute_action,
    lifecycle_cancel,
    lifecycle_consume,
    lifecycle_status,
    parse_argument_items,
    save_planned_action,
)
from workflow_closeout_package import build_closeout_package, build_review_summary
from workflow_iteration_capture import capture_iteration_candidates
from workflow_review_queue import sync_review_groups
from workflow_closeout_signals import apply_post_closeout_mirror, optional_closeout_sections
from online_access_gate import check as check_online_access
from shared.cli_contract import enum_arg, normalize_enum_value
from shared.json_cli import (
    append_jsonl,
    compact_items,
    configure_utf8_stdio,
    now_iso,
    parse_key_value_items,
    print_json,
    repeatable_items,
)


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
WORK_NOTES = BRIDGE / "tmp" / "work_notes" / "current.jsonl"
CLOSEOUT_DIR = BRIDGE / "runtime" / "workflow_closeouts"
CLOSEOUT_JSONL = CLOSEOUT_DIR / "closeouts.jsonl"
CHECKPOINT_DIR = BRIDGE / "runtime" / "workflow_checkpoints"
CHECKPOINT_JSONL = CHECKPOINT_DIR / "checkpoints.jsonl"
WORKFLOW_OUTCOMES = {"ok", "partial", "failed", "blocked", "unknown"}

configure_utf8_stdio()


def read_work_notes() -> list[dict[str, Any]]:
    payload = memory_work_notes.work_note_read(WORK_NOTES, limit=10_000)
    return [item for item in payload.get("entries", []) if isinstance(item, dict)]


def record_items(csv_value: str | list[str], repeated_values: list[str] | None = None) -> list[str]:
    return repeatable_items(csv_value, repeated_values)


SELF_UPDATE_TRIGGER_TERMS = (
    "governance",
    "workflow",
    "resource",
    "memory",
    "skill",
    "system",
    "config",
    "mcp",
    "startup",
)

def select_self_update_owners(
    *,
    changed_surfaces: list[str] | None,
    task_kind: str,
    outcome: str,
    major_change: bool,
    changed_files: list[str] | None = None,
    config_changed: bool = False,
    validation_receipts: list[str] | None = None,
) -> list[str]:
    from self_update_governance import OWNER_NAMES, select_owners_for_change, validation_receipt_index

    explicit = record_items(changed_surfaces or [])
    normalized_surfaces = [item.lower().replace("_", "-") for item in explicit]
    if any(item in {"all", "deep", "full"} for item in normalized_surfaces):
        return list(OWNER_NAMES)
    return select_owners_for_change(
        changed_files=changed_files or [],
        changed_surfaces=explicit,
        task_kind=task_kind,
        outcome=outcome,
        config_changed=config_changed,
        major_change=major_change,
        validated_owners=validation_receipt_index(validation_receipts or []),
    )


def should_run_self_update_governance(
    *,
    task_kind: str,
    outcome: str,
    config_changed: bool,
    major_change: bool,
    changed_surfaces: list[str] | None = None,
    changed_files: list[str] | None = None,
) -> bool:
    if config_changed or major_change or changed_surfaces or changed_files or outcome in {"failed", "blocked", "partial"}:
        return True
    normalized = task_kind.lower().replace("_", "-")
    return any(term in normalized for term in SELF_UPDATE_TRIGGER_TERMS)


def self_update_closeout_signal(
    *,
    task_kind: str,
    outcome: str,
    config_changed: bool,
    major_change: bool,
    changed_surfaces: list[str] | None = None,
    changed_files: list[str] | None = None,
    validation_receipts: list[str] | None = None,
) -> dict[str, Any]:
    if not should_run_self_update_governance(
        task_kind=task_kind,
        outcome=outcome,
        config_changed=config_changed,
        major_change=major_change,
        changed_surfaces=changed_surfaces,
        changed_files=changed_files,
    ):
        return {
            "schema": "self_update_governance.closeout_signal.v1",
            "checked": False,
            "reason": "not_triggered_for_simple_successful_closeout",
            "signals": [],
        }
    selected_owners = select_self_update_owners(
        changed_surfaces=changed_surfaces,
        task_kind=task_kind,
        outcome=outcome,
        major_change=major_change,
        changed_files=changed_files,
        config_changed=config_changed,
        validation_receipts=validation_receipts,
    )
    if selected_owners == []:
        return {
            "schema": "self_update_governance.closeout_signal.v1",
            "checked": False,
            "reason": "no_applicable_self_update_owner_for_changed_surface",
            "selected_owners": [],
            "signals": [],
        }
    try:
        from self_update_governance import doctor as self_update_doctor

        payload = self_update_doctor(
            selected_owners=selected_owners,
            validation_receipts=validation_receipts or [],
            changed_files=changed_files or [],
            changed_surfaces=changed_surfaces or [],
            task_kind=task_kind,
            outcome=outcome,
            config_changed=config_changed,
            major_change=major_change,
        )
    except Exception as exc:
        return {
            "schema": "self_update_governance.closeout_signal.v1",
            "checked": True,
            "ok": False,
            "status": "risk",
            "signals": [
                {
                    "surface": "self_update_governance",
                    "code": "doctor_failed",
                    "severity": "risk",
                    "detail": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "next_action": "Run python _bridge\\self_update_governance.py doctor and fix the owner invocation path.",
                }
            ],
        }
    return {
        "schema": "self_update_governance.closeout_signal.v1",
        "checked": True,
        "ok": bool(payload.get("ok")),
        "status": payload.get("status", "unknown"),
        "summary": payload.get("summary", {}),
        "change_set": payload.get("change_set", {}),
        "selected_owners": payload.get("change_set", {}).get("selected_owners", selected_owners),
        "authoritative_owners": payload.get("authoritative_owners", []),
        "signals": payload.get("signals", []),
    }


def proposal_items(values: list[str]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        parts = [part.strip() for part in text.split("|", 3)]
        proposals.append(
            {
                "type": parts[0] if len(parts) > 0 and parts[0] else "general",
                "title": parts[1] if len(parts) > 1 and parts[1] else text,
                "detail": parts[2] if len(parts) > 2 else "",
                "artifact_ref": parts[3] if len(parts) > 3 else "",
                "requires_approval": True,
                "status": "pending_user_review",
            }
        )
    return proposals


def should_compact_closeout(package: dict[str, Any]) -> bool:
    status = package.get("status", {}) if isinstance(package.get("status"), dict) else {}
    tool_evidence = package.get("tool_evidence", {}) if isinstance(package.get("tool_evidence"), dict) else {}
    work_notes = package.get("work_notes", {}) if isinstance(package.get("work_notes"), dict) else {}
    proposals = package.get("proposals", []) if isinstance(package.get("proposals"), list) else []
    profile_candidates = package.get("user_profile_candidates", {}) if isinstance(package.get("user_profile_candidates"), dict) else {}
    external_candidates = package.get("external_knowledge_candidates", {}) if isinstance(package.get("external_knowledge_candidates"), dict) else {}
    self_update = package.get("self_update_governance", {}) if isinstance(package.get("self_update_governance"), dict) else {}
    finalization = package.get("finalization", {}) if isinstance(package.get("finalization"), dict) else {}
    finalization_signals = finalization.get("signals", {}) if isinstance(finalization.get("signals"), dict) else {}
    return (
        status.get("outcome") in {"ok", "complete"}
        and int(work_notes.get("active_count") or 0) == 0
        and not proposals
        and int(profile_candidates.get("candidate_count") or 0) == 0
        and int(external_candidates.get("selected_count") or 0) == 0
        and not self_update.get("signals")
        and not any(bool(finalization_signals.get(key)) for key in ("config_changed", "major_change"))
        and not tool_evidence.get("fallback_used")
        and not tool_evidence.get("negative_observations")
        and not tool_evidence.get("unverified")
    )


def compact_closeout(package: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "schema": "codex_workflow_entry.closeout.compact.v1",
        "ok": bool(package.get("ok")),
        "generated_at": package.get("generated_at"),
        "machine_first": True,
        "record_path": package.get("record_path"),
        "task_kind": package.get("task_kind"),
        "status": package.get("status"),
        "timings": package.get("timings"),
        "used": package.get("used"),
        "tool_evidence": package.get("tool_evidence"),
        "validation": package.get("validation"),
        "persistence_decisions": {
            "memory_absorb_needed": "not_needed",
            "user_profile_update_needed": "not_needed",
            "skill_revision_needed": "not_needed",
            "slash_template_needed": "not_needed",
            "tool_matrix_update_needed": "not_needed",
            "baseline_update_needed": "not_needed",
            "project_checkpoint_needed": "not_needed",
            "external_knowledge_capture_needed": "not_needed",
            "external_knowledge_absorb_needed": "not_needed",
        },
        "finalization": package.get("finalization", {}),
        "work_notes": {"active_count": 0},
        "proposals": [],
        "pending_disposition": {
            "schema": "codex_workflow_entry.pending_disposition.v1",
            "ok": True,
            "pending_count": 0,
            "items": [],
            "rule": "Compact closeout preserves the canonical queue shape even when no disposition is pending.",
        },
        "closeout_policy": "compact because no notes, proposals, fallback, negative observations, or unverified items were present",
    }
    compact["final_reply_must_show"] = build_review_summary(compact, limit=20)
    return compact


def reconciliation_summary(reconciliation: dict[str, Any]) -> dict[str, Any]:
    """Return compact, actionable reconciliation evidence for failed closeout gates."""
    if not isinstance(reconciliation, dict):
        return {}
    summary = {
        key: reconciliation.get(key)
        for key in (
            "required",
            "complete",
            "reason",
            "changed_files",
            "affected_systems",
            "affected_surfaces",
            "affected",
            "unmatched",
            "required_receipt",
            "receipt_ok",
            "required_next_commands",
        )
        if reconciliation.get(key) not in (None, "", [], {})
    }
    impact = reconciliation.get("impact") if isinstance(reconciliation.get("impact"), dict) else {}
    impact_evidence = {
        key: impact.get(key)
        for key in (
            "ok",
            "coverage_complete",
            "unmapped_system_changed",
            "rule_change_required",
            "contract_upgrade_required",
            "blockers",
            "risks",
            "advisories",
            "unmatched",
        )
        if impact.get(key) not in (None, "", [], {})
    }
    if impact_evidence:
        summary["impact_evidence"] = impact_evidence
    owner_validation = (
        reconciliation.get("owner_validation") if isinstance(reconciliation.get("owner_validation"), dict) else {}
    )
    owner_evidence = {
        key: owner_validation.get(key)
        for key in ("ok", "status", "issues", "blockers", "risks", "failures")
        if owner_validation.get(key) not in (None, "", [], {})
    }
    if owner_evidence:
        summary["owner_validation_evidence"] = owner_evidence
    return summary


def owner_result_summary(result: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """Return a bounded owner-result view that keeps failure causes actionable."""
    if not isinstance(result, dict):
        return {}
    keys = (
        "ok",
        "schema",
        "phase",
        "status",
        "reason",
        "error",
        "next_action",
        "snapshot_id",
        "capture_mode",
        "remote",
        "branch",
        "source_freshness",
        "readiness",
        "issues",
        "blockers",
        "failures",
        "actionable_failures",
        "remote_verification",
    )
    summary = {key: result.get(key) for key in keys if result.get(key) not in (None, "", [], {})}
    refresh = result.get("refresh") if isinstance(result.get("refresh"), dict) else {}
    if refresh:
        summary["refresh"] = owner_result_summary(refresh, full=full)
    push = result.get("push") if isinstance(result.get("push"), dict) else {}
    if push:
        push_keys = ("ok", "remote", "branch", "head", "remote_url", "remote_verification")
        summary["push"] = {key: push.get(key) for key in push_keys if push.get(key) not in (None, "", [], {})}
        if full and push.get("network_route") not in (None, "", [], {}):
            summary["push"]["network_route"] = bounded_value(push.get("network_route"), max_depth=3, max_items=8, max_string=360)
    if full:
        for key in ("attempts", "commit", "metadata_commit", "push", "push_receipt", "advisories"):
            if key == "push":
                continue
            if result.get(key) not in (None, "", [], {}):
                summary[key] = bounded_value(result.get(key), max_depth=4, max_items=12, max_string=700)
    elif result.get("ok") is not True:
        for key in ("attempts", "advisories"):
            if result.get(key) not in (None, "", [], {}):
                summary[key] = bounded_value(result.get(key), max_depth=3, max_items=5, max_string=360)
    return summary


def finalization_section_summary(section: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """Summarize closeout finalization sections without embedding full receipts."""
    if not isinstance(section, dict):
        return {}
    keys = ("ok", "needed", "applied", "reason", "blocked_reason", "next_step", "rule")
    summary = {key: section.get(key) for key in keys if section.get(key) not in (None, "", [], {})}
    result = section.get("result") if isinstance(section.get("result"), dict) else {}
    if result:
        summary["result"] = owner_result_summary(result, full=full)
        checkpoint = result.get("checkpoint") if isinstance(result.get("checkpoint"), dict) else {}
        if checkpoint:
            summary["checkpoint"] = {
                key: checkpoint.get(key)
                for key in (
                    "checkpoint_id",
                    "project_id",
                    "title",
                    "logical_ref",
                    "workspace_relative_path",
                    "workspace_path",
                )
                if checkpoint.get(key) not in (None, "", [], {})
            }
    if full:
        for key in ("check", "adopt"):
            if section.get(key) not in (None, "", [], {}):
                summary[key] = bounded_value(section.get(key), max_depth=4, max_items=10, max_string=500)
    return summary


def post_closeout_mirror_summary(post_closeout: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    if not isinstance(post_closeout, dict):
        return {}
    keys = ("schema", "required", "applied", "ok", "reason", "ordering", "reused", "next_action")
    summary = {key: post_closeout.get(key) for key in keys if post_closeout.get(key) not in (None, "", [], {})}
    result = post_closeout.get("result") if isinstance(post_closeout.get("result"), dict) else {}
    if result:
        summary["result"] = owner_result_summary(result, full=full)
    return summary


def closeout_cli_projection(payload: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    finalization = payload.get("finalization") if isinstance(payload.get("finalization"), dict) else {}
    membership = (
        finalization.get("membership_reconciliation")
        if isinstance(finalization.get("membership_reconciliation"), dict)
        else {}
    )
    rule = finalization.get("rule_reconciliation") if isinstance(finalization.get("rule_reconciliation"), dict) else {}
    tool_evidence = payload.get("tool_evidence") if isinstance(payload.get("tool_evidence"), dict) else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    external_research = tool_evidence.get("external_research") if isinstance(tool_evidence.get("external_research"), dict) else {}
    decision_evidence: dict[str, Any] = {}

    tool_failures = {
        key: tool_evidence.get(key)
        for key in ("fallback_used", "negative_observations", "unverified")
        if tool_evidence.get(key)
    }
    if tool_failures:
        decision_evidence["tool_failures"] = tool_failures
    online_gate = external_research.get("online_access_gate") if isinstance(external_research.get("online_access_gate"), dict) else {}
    if external_research.get("web_search_used") or (online_gate and not online_gate.get("ok")):
        decision_evidence["external_research"] = {
            key: external_research.get(key)
            for key in (
                "web_search_used",
                "owner_mcp_used",
                "owner_mcp_fallback_reason",
                "resource_request_id",
                "resource_status",
                "direct_web_fallback_reason",
                "user_direct_web",
                "platform_web_required",
                "online_access_gate",
            )
            if external_research.get(key) not in (None, "", [], {})
        }
    if validation.get("items") or validation.get("owner_receipts"):
        decision_evidence["validation"] = validation

    summary = {
        "schema": payload.get("schema"),
        "ok": payload.get("ok"),
        "output_mode": "closeout_full_bounded" if full else "closeout_default_bounded",
        "generated_at": payload.get("generated_at"),
        "record_path": payload.get("record_path"),
        "task_kind": payload.get("task_kind"),
        "status": payload.get("status"),
        "timings": payload.get("timings"),
        "used": payload.get("used"),
        "finalization": {
            "ok": finalization.get("ok"),
            "requested": finalization.get("requested"),
            "blocked_reason": finalization.get("blocked_reason"),
            "startup_baseline": finalization_section_summary(
                finalization.get("startup_baseline") if isinstance(finalization.get("startup_baseline"), dict) else {},
                full=full,
            ),
            "project_checkpoint": finalization_section_summary(
                finalization.get("project_checkpoint") if isinstance(finalization.get("project_checkpoint"), dict) else {},
                full=full,
            ),
            "membership_reconciliation": reconciliation_summary(membership),
            "rule_reconciliation": reconciliation_summary(rule),
            "post_closeout_mirror": post_closeout_mirror_summary(
                finalization.get("post_closeout_mirror") if isinstance(finalization.get("post_closeout_mirror"), dict) else {},
                full=full,
            ),
        },
        "pending_disposition": payload.get("pending_disposition"),
        "final_reply_must_show": payload.get("final_reply_must_show"),
        "detail_rule": (
            "--full-output shows a richer bounded diagnostic projection; use record_path/raw_result_ref for the complete stored package"
            if full
            else "default output shows actionable status only; use --full-output for a richer bounded diagnostic projection"
        ),
    }
    if decision_evidence:
        summary["decision_evidence"] = decision_evidence
    if full:
        summary["section_index"] = {
            "full_package_ref": str(payload.get("record_path") or ""),
            "raw_package_access": "read the JSONL record referenced by record_path; CLI output remains bounded",
            "included_sections": [
                "status",
                "used",
                "validation",
                "finalization",
                "pending_disposition",
                "final_reply_must_show",
            ],
        }
    return bounded_payload(
        summary,
        max_bytes=32 * 1024 if full else 12 * 1024,
        max_depth=7,
        max_items=50 if full else 24,
        max_string=1200 if full else 700,
        preserve_keys=(
            "schema",
            "ok",
            "output_mode",
            "generated_at",
            "record_path",
            "task_kind",
            "status",
            "decision_evidence",
            "finalization",
            "pending_disposition",
            "final_reply_must_show",
            "section_index",
        ),
        artifact_ref=str(payload.get("record_path") or ""),
    )


def save_closeout(payload: dict[str, Any]) -> None:
    append_jsonl(CLOSEOUT_JSONL, payload, sort_keys=True)


def save_checkpoint(payload: dict[str, Any]) -> None:
    append_jsonl(CHECKPOINT_JSONL, payload, sort_keys=True)


def owner_facade_route(action: dict[str, Any], message: str) -> dict[str, Any]:
    owner = str(action.get("owner") or "")
    auto = action.get("auto_lifecycle") if isinstance(action.get("auto_lifecycle"), dict) else {}
    needs_input = action.get("needs_input") if isinstance(action.get("needs_input"), dict) else {}
    if not owner:
        state, next_action = "owner_unresolved", "provide_needs_input"
    elif needs_input.get("required"):
        state, next_action = "needs_input", "provide_needs_input"
    elif auto.get("eligible"):
        state, next_action = "ready", "run_auto_lifecycle"
    else:
        state, next_action = "ready", "run_single_step"
    argv = ["python", "_bridge\\codex_workflow_entry.py", "run", "--message", message]
    if owner:
        argv.extend(["--owner", owner])
    if action.get("operation"):
        argv.extend(["--operation", str(action.get("operation"))])
    for key, value in (action.get("arguments") or {}).items():
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")) if isinstance(value, (dict, list, bool)) else str(value)
        argv.extend(["--arg", f"{key}={encoded}"])
    if auto.get("eligible"):
        argv.append("--auto-lifecycle")
    return {
        "schema": "workflow.owner_facade_route.v1",
        "default_for_owner_backed_task": bool(owner),
        "state": state,
        "next_action": next_action,
        "run_argv": argv if owner and not needs_input.get("required") else [],
        "needs_input": needs_input,
        "session_handoff_preserved": str(action.get("session_binding") or "none") != "none" or action.get("operation") in {"tool_call", "session_tool_call"},
        "rule": "use the facade route for owner-backed work; owner-native state and permissions remain authoritative",
    }


def compact_plan(
    message: str,
    risk: str,
    detail: str = "micro",
    *,
    owner: str = "",
    operation: str = "",
    arguments: dict[str, Any] | None = None,
    approved: bool = False,
    deadline_seconds: int = 300,
) -> dict[str, Any]:
    plan = build_plan(message, risk=risk, detail=detail)
    action = build_action(
        plan,
        message=message,
        owner=owner,
        operation=operation,
        arguments=arguments,
        approved=approved,
        deadline_seconds=deadline_seconds,
    )
    facade_route = owner_facade_route(action, message)
    payload = {
        "schema": "codex_workflow_entry.plan.v2",
        "source_plan_schema": plan.get("schema"),
        "ok": bool(plan.get("ok")),
        "generated_at": now_iso(),
        "machine_first": True,
        "human_readability_goal": False,
        "detail_level": plan.get("detail_level", detail),
        "context_budget_policy": plan.get("context_budget_policy", {}),
        "domains": plan.get("domains", []),
        "skills": plan.get("skills", {}),
        "skill_orchestration": plan.get("skill_orchestration", {}),
        "slash_templates": plan.get("slash_templates", {}),
        "execution_route_pack": plan.get("execution_route_pack", {}),
        "memory_route": plan.get("memory", {}),
        "machine_phases": plan.get("machine_phases", []),
        "execution_plan": plan.get("execution_plan", {}),
        "action_contract": action,
        "owner_facade": facade_route,
        "expand": plan.get("expand", {"standard": "--detail standard", "full": "--detail full"}),
        "stop_if": [
            "approval_required_for_write_without_user_approval",
            "permission_boundary_unclear",
            "required_template_missing",
            "native_tool_failed_and_no_same_boundary_fallback",
        ],
    }
    if isinstance(payload.get("execution_route_pack"), dict):
        payload["execution_route_pack"] = dict(payload["execution_route_pack"])
        payload["execution_route_pack"]["action_contract"] = action
        payload["execution_route_pack"]["owner_facade"] = facade_route
    return payload


def preflight(message: str, risk: str, detail: str = "standard") -> dict[str, Any]:
    payload = compact_plan(message, risk, detail=detail)
    phases = payload.get("machine_phases", [])
    return {
        "schema": "codex_workflow_entry.preflight.v2",
        "ok": bool(payload.get("ok")) and bool(phases),
        "generated_at": now_iso(),
        "machine_first": True,
        "phase_count": len(phases),
        "next_phase": phases[0].get("id") if phases else "",
        "plan": payload,
    }


def checkpoint(
    *,
    phase: str,
    trigger: str,
    summary: str = "",
    tool: str = "",
    evidence: list[str] | None = None,
    pending: list[str] | None = None,
    requires_approval: bool = False,
    save: bool = False,
) -> dict[str, Any]:
    evidence_items = parse_key_value_items(evidence or [])
    pending_items = compact_items(pending or [])
    payload = {
        "schema": "codex_workflow_entry.checkpoint.v1",
        "ok": True,
        "generated_at": now_iso(),
        "machine_first": True,
        "record_path": str(CHECKPOINT_JSONL) if save else "",
        "phase": str(phase or "unknown"),
        "trigger": str(trigger or "manual"),
        "summary": summary,
        "tool": tool,
        "evidence": evidence_items,
        "pending": pending_items,
        "requires_approval": bool(requires_approval),
        "authorization": {
            "inherits_main_task_write_approval": False,
            "rule": "checkpoint records state only; any derived write, repair, memory, skill, baseline, permission, or external action requires its own approval unless already explicitly authorized",
        },
        "recommended_next": [
            "continue_current_phase_if_not_blocked",
            "add_work_note_for_non_blocking_side_issue",
            "switch_same_permission_fallback_after_current_turn_negative_evidence",
            "surface_approval_request_before_derived_write",
        ],
    }
    if save:
        save_checkpoint(payload)
    return payload


def closeout(
    *,
    task_kind: str = "general",
    selected: str = "",
    used: str = "",
    outcome: str = "unknown",
    memory: str | list[str] = "",
    slash: str | list[str] = "",
    mcp: str | list[str] = "",
    local_tool: str | list[str] = "",
    current_turn_callable: list[str] | None = None,
    protocol_ok_only: list[str] | None = None,
    fallback_used: list[str] | None = None,
    negative_observation: list[str] | None = None,
    unverified: list[str] | None = None,
    web_search_used: bool = False,
    owner_mcp_used: list[str] | None = None,
    owner_mcp_fallback_reason: str = "",
    resource_request_id: str = "",
    resource_status: str = "",
    direct_web_fallback_reason: str = "",
    user_direct_web: bool = False,
    platform_web_required: bool = False,
    validation: list[str] | None = None,
    memory_route: list[str] | None = None,
    memory_layer: list[str] | None = None,
    proposal: list[str] | None = None,
    profile_signal: list[str] | None = None,
    check_profile_candidates: bool = False,
    check_external_knowledge: bool = False,
    config_changed: bool = False,
    major_change: bool = False,
    changed_surface: list[str] | None = None,
    validation_receipt: list[str] | None = None,
    auto_finalize: bool = False,
    finalization_project_id: str = "",
    finalization_title: str = "",
    finalization_summary: str = "",
    finalization_changed_file: list[str] | None = None,
    finalization_evidence: list[str] | None = None,
    finalization_backup: list[str] | None = None,
    finalization_stable_conclusion: list[str] | None = None,
    correction: list[str] | None = None,
    verified_root_cause: list[str] | None = None,
    regression_test: list[str] | None = None,
    prevention_guard: list[str] | None = None,
    repeated_manual_step: list[str] | None = None,
    save: bool = False,
) -> dict[str, Any]:
    closeout_started = time.perf_counter()
    timings: dict[str, float] = {}
    outcome = normalize_enum_value(
        outcome,
        allowed=WORKFLOW_OUTCOMES,
        field_name="closeout --outcome",
        prose_destination="--finalization-summary, --validation, or --notes",
    )
    notes = read_work_notes()
    selected_skills = record_items(selected)
    used_skills = record_items(used)
    used_memory = record_items(memory)
    used_slash = record_items(slash)
    used_mcp = record_items(mcp)
    used_local_tools = record_items(local_tool)
    callable_tools = compact_items(current_turn_callable or [])
    protocol_tools = compact_items(protocol_ok_only or [])
    fallback_tools = compact_items(fallback_used or [])
    negative_items = parse_key_value_items(negative_observation or [])
    owner_mcp_tools = compact_items(owner_mcp_used or [])
    unverified_input = list(unverified or [])
    online_gate = check_online_access(
        web_used=web_search_used,
        resource_request_id=resource_request_id,
        resource_status=resource_status,
        fallback_reason=direct_web_fallback_reason or owner_mcp_fallback_reason,
        user_direct_web=user_direct_web,
        platform_web_required=platform_web_required,
        evidence=owner_mcp_fallback_reason,
    )
    for blocker in online_gate.get("blockers", []):
        if isinstance(blocker, dict):
            unverified_input.append(f"{blocker.get('code', 'online_access_gate_blocker')}={blocker.get('message', '')}")
    if web_search_used and not online_gate.get("ok") and not owner_mcp_tools and not owner_mcp_fallback_reason.strip():
        unverified_input.append("external_research_owner_mcp_missing=generic web search used without owner MCP/resource-layer exception evidence")
    unverified_items = parse_key_value_items(unverified_input)
    validation_items = parse_key_value_items(validation or [])
    memory_route_items = parse_key_value_items(memory_route or [])
    memory_layer_items = parse_key_value_items(memory_layer or [])
    proposals = proposal_items(proposal or [])
    phase_started = time.perf_counter()
    optional_sections = optional_closeout_sections(
        outcome=outcome,
        proposals=proposals,
        profile_signal=profile_signal or [],
        check_profile_candidates=check_profile_candidates,
        check_external_knowledge=check_external_knowledge,
        web_search_used=web_search_used,
        owner_mcp_tools=owner_mcp_tools,
        config_changed=config_changed,
        major_change=major_change,
        auto_finalize=auto_finalize,
        finalization_project_id=finalization_project_id,
        finalization_title=finalization_title,
        finalization_summary=finalization_summary,
        finalization_changed_file=finalization_changed_file or [],
        finalization_evidence=finalization_evidence or [],
        finalization_backup=finalization_backup or [],
        finalization_stable_conclusion=finalization_stable_conclusion or [],
        validation_items=validation_items,
        validation_receipts=validation_receipt or [],
        task_kind=task_kind,
        defer_post_mirror=True,
    )
    timings["finalization_ms"] = round((time.perf_counter() - phase_started) * 1000, 1)
    profile_candidates = optional_sections["profile_candidates"]
    external_candidates = optional_sections["external_candidates"]
    finalization = optional_sections["finalization"]
    checkpoint_result = finalization.get("project_checkpoint", {}).get("result", {})
    iteration_capture = capture_iteration_candidates(
        outcome=outcome,
        config_changed=config_changed,
        major_change=major_change,
        corrections=correction or [],
        verified_root_causes=verified_root_cause or [],
        regression_tests=regression_test or [],
        prevention_guards=prevention_guard or [],
        repeated_manual_steps=repeated_manual_step or [],
        checkpoint=checkpoint_result,
        affected_system=finalization_project_id or task_kind,
    )
    phase_started = time.perf_counter()
    self_update_governance = self_update_closeout_signal(
        task_kind=task_kind,
        outcome=outcome,
        config_changed=config_changed,
        major_change=major_change,
        changed_surfaces=changed_surface or [],
        changed_files=finalization_changed_file or [],
        validation_receipts=validation_receipt or [],
    )
    timings["self_update_ms"] = round((time.perf_counter() - phase_started) * 1000, 1)
    phase_started = time.perf_counter()
    finalization = apply_post_closeout_mirror(
        finalization,
        changed_files=finalization_changed_file or [],
        apply=auto_finalize,
        outcome=outcome,
        owner_checks_ok=bool(self_update_governance.get("checked")) and bool(self_update_governance.get("ok")),
    )
    optional_sections["finalization"] = finalization
    timings["mirror_publish_ms"] = round((time.perf_counter() - phase_started) * 1000, 1)
    phase_started = time.perf_counter()
    package = build_closeout_package({
        "record_path": str(CLOSEOUT_JSONL) if save else "",
        "task_kind": task_kind,
        "outcome": outcome,
        "notes": notes,
        "proposals": proposals,
        "profile_candidates": profile_candidates,
        "external_candidates": external_candidates,
        "self_update_governance": self_update_governance,
        "iteration_capture": iteration_capture,
        "fallback_tools": fallback_tools,
        "negative_items": negative_items,
        "unverified_items": unverified_items,
        "used": {
            "memory": used_memory,
            "skills": used_skills,
            "slash_templates": used_slash,
            "mcp": used_mcp,
            "local_tools": used_local_tools,
        },
        "skill_usage": {
            "selected": selected_skills,
            "used": used_skills,
            "outcome": outcome,
            "record_command": "python _bridge\\skill_orchestrator.py record-usage --task-kind <kind> --selected <skill,...> --used <skill,...> --outcome <ok|partial|failed> --notes <short>",
        },
        "tool_evidence": {
            "current_turn_callable": callable_tools,
            "protocol_ok_only": protocol_tools,
            "fallback_used": fallback_tools,
            "negative_observations": negative_items,
            "unverified": unverified_items,
            "external_research": {
                "web_search_used": web_search_used,
                "owner_mcp_used": owner_mcp_tools,
                "owner_mcp_fallback_reason": owner_mcp_fallback_reason.strip(),
                "online_access_gate": online_gate,
                "resource_request_id": resource_request_id,
                "resource_status": resource_status,
                "direct_web_fallback_reason": direct_web_fallback_reason.strip(),
                "user_direct_web": user_direct_web,
                "platform_web_required": platform_web_required,
                "rule": "explicit web/external research must prefer resource layer first; generic web search needs resource-layer unavailable/route exhaustion evidence, explicit user request, or an explicit higher-precedence platform requirement",
            },
            "rule": "protocol_ok_only must not be treated as current_turn_callable; MCP fallback order is enforced by route policy and mcp_session_doctor, not by closeout scanning",
        },
        "work_notes": {
            "path": str(WORK_NOTES),
            "active_count": len(notes),
            "entries": notes,
            "write_authorization_inherited": False,
            "required_disposition": ["handled_read_only", "proposal", "deferred", "discarded"],
            "disposition_command": "python _bridge\\memory_governance.py work-note-dispose --ids <source_item_id> --disposition <handled_read_only|proposal|deferred|discarded>",
        },
        "memory_routing": {
            "route_decisions": memory_route_items,
            "layers_used": memory_layer_items,
            "rule": "memory layers are routed by task fit; PMB is useful for durable lessons/root causes, while live state must be verified through owning tools",
            "write_authorization_inherited": False,
        },
        "validation": {
            "items": validation_items,
            "owner_receipts": compact_items(validation_receipt or []),
            "required_before_final_reply": True,
            "fallback": "targeted readback or owning maintenance validate when a command is unavailable",
        },
        "finalization": finalization,
    })
    package["pending_disposition"]["items"] = sync_review_groups(
        package.get("pending_disposition", {}).get("items", []),
        authoritative_scopes=[
            {
                "kind": "self_update_governance",
                "source_item_prefix": f"self_update:{owner}:",
            }
            for owner in package.get("self_update_governance", {}).get("authoritative_owners", [])
        ],
    )
    package["pending_disposition"]["pending_count"] = len(package["pending_disposition"]["items"])
    package["pending_disposition"]["persistence"] = {
        "owner": "workflow_review_queue",
        "transition_command": "python _bridge\\workflow_review_queue.py transition --review-id <id> --status <approved|revised|rejected|applied|validated|resolved>",
        "iteration_owner_command": "python _bridge\\workflow_iteration_owner.py <plan|apply|validate|resolve> --review-id <iteration:id> [--confirm-apply]",
        "rule": "only status=pending is rendered; iteration writes require approved -> owner apply -> validated -> resolved",
    }
    package["final_reply_must_show"] = build_review_summary(package, limit=20)
    timings["package_ms"] = round((time.perf_counter() - phase_started) * 1000, 1)
    timings["total_ms"] = round((time.perf_counter() - closeout_started) * 1000, 1)
    package["timings"] = timings
    if save:
        save_closeout(package)
    if should_compact_closeout(package):
        return compact_closeout(package)
    return package


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if raw_argv[:1] == ["resource"]:
        from resource_cli import main as resource_main

        return resource_main(raw_argv[1:])
    parser = argparse.ArgumentParser(description="Machine-first Codex workflow entry facade")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "preflight"):
        p = sub.add_parser(name)
        p.add_argument("--message", required=True)
        p.add_argument("--risk", default="unknown")
        p.add_argument("--detail", choices=["micro", "standard", "full", "auto"], default="micro" if name == "plan" else "standard")
        if name == "plan":
            p.add_argument("--owner", choices=["resource", "email", "maintenance", "mcp", "mobile", "network", "office"], default="")
            p.add_argument("--operation", default="")
            p.add_argument("--arg", action="append", default=[], help="Structured owner argument as key=value; repeat when needed.")
            p.add_argument("--approve", action="store_true")
            p.add_argument("--deadline-seconds", type=int, default=300)
            p.add_argument("--save-action", action="store_true", help="Persist the planned action reference for a later run --workflow-run-id.")
    run = sub.add_parser("run")
    run_source = run.add_mutually_exclusive_group(required=True)
    run_source.add_argument("--message")
    run_source.add_argument("--workflow-run-id")
    run.add_argument("--risk", default="unknown")
    run.add_argument("--owner", choices=["resource", "email", "maintenance", "mcp", "mobile", "network", "office"], default="")
    run.add_argument("--operation", default="")
    run.add_argument("--arg", action="append", default=[], help="Structured owner argument as key=value; repeat when needed.")
    run.add_argument("--approve", action="store_true")
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument("--auto-lifecycle", action="store_true", help="Run the bounded low-risk run/wait/consume/closeout sequence when eligible.")
    run.add_argument("--interval", type=float, default=1.0)
    status = sub.add_parser("status")
    status.add_argument("--workflow-run-id", required=True)
    wait = sub.add_parser("wait")
    wait.add_argument("--workflow-run-id", required=True)
    wait.add_argument("--timeout", type=int, default=300)
    wait.add_argument("--interval", type=float, default=1.0)
    consume = sub.add_parser("consume")
    consume.add_argument("--workflow-run-id", required=True)
    cancel = sub.add_parser("cancel")
    cancel.add_argument("--workflow-run-id", required=True)
    cancel.add_argument("--approve", action="store_true")
    attach = sub.add_parser("attach-result")
    attach.add_argument("--workflow-run-id", required=True)
    attach_source = attach.add_mutually_exclusive_group(required=True)
    attach_source.add_argument("--owner-result-file")
    attach_source.add_argument("--owner-result-json")
    k = sub.add_parser("checkpoint")
    k.add_argument("--phase", required=True)
    k.add_argument("--trigger", required=True)
    k.add_argument("--summary", default="")
    k.add_argument("--tool", default="")
    k.add_argument("--evidence", action="append", default=[])
    k.add_argument("--pending", action="append", default=[])
    k.add_argument("--requires-approval", action="store_true")
    k.add_argument("--save", action="store_true")
    c = sub.add_parser("closeout")
    c.add_argument("--task-kind", default="general")
    c.add_argument("--selected", default="")
    c.add_argument("--used", default="")
    c.add_argument(
        "--outcome",
        default="unknown",
        type=enum_arg(
            "closeout --outcome",
            WORKFLOW_OUTCOMES,
            prose_destination="--finalization-summary, --validation, or --notes",
        ),
        help="Machine status only: ok|partial|failed|blocked|unknown. Put prose in --finalization-summary, --validation, or --notes.",
    )
    c.add_argument("--memory", action="append", default=[])
    c.add_argument("--slash", action="append", default=[])
    c.add_argument("--mcp", action="append", default=[])
    c.add_argument("--local-tool", action="append", default=[])
    c.add_argument("--current-turn-callable", action="append", default=[])
    c.add_argument("--protocol-ok-only", action="append", default=[])
    c.add_argument("--fallback-used", action="append", default=[])
    c.add_argument("--negative-observation", action="append", default=[])
    c.add_argument("--unverified", action="append", default=[])
    c.add_argument("--web-search-used", action="store_true")
    c.add_argument("--owner-mcp-used", action="append", default=[])
    c.add_argument("--owner-mcp-fallback-reason", default="")
    c.add_argument("--resource-request-id", default="")
    c.add_argument("--resource-status", default="")
    c.add_argument("--direct-web-fallback-reason", default="")
    c.add_argument("--user-direct-web", action="store_true")
    c.add_argument("--platform-web-required", action="store_true", help="Record an explicit higher-precedence platform requirement to use generic web; never inferred from task text.")
    c.add_argument("--validation", action="append", default=[])
    c.add_argument("--memory-route", action="append", default=[])
    c.add_argument("--memory-layer", action="append", default=[])
    c.add_argument("--proposal", action="append", default=[])
    c.add_argument("--profile-signal", action="append", default=[], help="Explicit current-turn user-profile candidate signal; dry-run candidate only.")
    c.add_argument("--check-profile-candidates", action="store_true", help="Run user-profile candidate scan; skipped by default.")
    c.add_argument("--check-external-knowledge", action="store_true", help="Run external-knowledge candidate materialization/check; skipped unless external research evidence exists.")
    c.add_argument("--config-changed", action="store_true", help="This turn intentionally changed Codex working-environment configuration.")
    c.add_argument("--major-change", action="store_true", help="This turn completed a verified major project change that needs a checkpoint.")
    c.add_argument("--changed-surface", action="append", default=[], help="Changed governance surface used to select closeout owners; repeat as needed. Use all/deep for the full owner set.")
    c.add_argument("--validation-receipt", action="append", default=[], help="Current-turn owner validation receipt as owner=path, owner=ok, or inline JSON.")
    c.add_argument("--auto-finalize", action="store_true", help="Apply bounded baseline/checkpoint finalization for explicit closeout signals.")
    c.add_argument("--finalization-project-id", default="")
    c.add_argument("--finalization-title", default="")
    c.add_argument("--finalization-summary", default="")
    c.add_argument("--finalization-changed-file", action="append", default=[])
    c.add_argument("--finalization-evidence", action="append", default=[])
    c.add_argument("--finalization-backup", action="append", default=[])
    c.add_argument("--finalization-stable-conclusion", action="append", default=[])
    c.add_argument("--correction", action="append", default=[], help="Verified user correction to capture as a review candidate; no target write.")
    c.add_argument("--verified-root-cause", action="append", default=[], help="Verified root cause to capture for review; no target write.")
    c.add_argument("--regression-test", action="append", default=[], help="New regression test conclusion to capture for review.")
    c.add_argument("--prevention-guard", action="append", default=[], help="Verified prevention guard to capture for review.")
    c.add_argument("--repeated-manual-step", action="append", default=[], help="Repeated manual step worth owner-reviewed automation.")
    c.add_argument("--notes", action="append", default=[], help="Compatibility alias; stored as validation notes.")
    c.add_argument("--save", action="store_true")
    c.add_argument(
        "--full-output",
        action="store_true",
        help="Print a richer bounded closeout diagnostic projection; the complete package remains available through record_path.",
    )
    r = sub.add_parser("review-summary")
    r.add_argument("--limit", type=int, default=20)
    resource = sub.add_parser(
        "resource",
        add_help=False,
        help="Run the resource owner CLI through the unified workflow entrypoint.",
    )
    resource.add_argument("resource_args", nargs=argparse.REMAINDER)
    maintenance = sub.add_parser("maintenance")
    maintenance_sub = maintenance.add_subparsers(dest="maintenance_command", required=True)
    catalog = maintenance_sub.add_parser("catalog")
    catalog.add_argument("--system", default="")
    catalog.add_argument("--term", default="")
    catalog.add_argument("--action", default="")
    catalog.add_argument("--limit", type=int, default=20)
    maintenance_build = maintenance_sub.add_parser("build-index")
    maintenance_build.add_argument("--apply", action="store_true")
    for name in ("doctor", "validate", "metrics"):
        maintenance_sub.add_parser(name)
    maintenance_run = maintenance_sub.add_parser("run")
    maintenance_run.add_argument("--capability-id", required=True)
    maintenance_run.add_argument("--action", required=True)
    maintenance_run.add_argument("--cli-arg", action="append", default=[])
    maintenance_run.add_argument("--timeout", type=int, default=300)
    mirror = sub.add_parser("mirror")
    mirror_sub = mirror.add_subparsers(dest="mirror_action", required=True)
    for name in ("status", "plan", "doctor", "validate", "release-plan", "contract-review-plan"):
        mirror_sub.add_parser(name)
    mirror_affected = mirror_sub.add_parser("affected-source-plan")
    mirror_affected.add_argument("--changed", action="append", default=[])
    mirror_compare = mirror_sub.add_parser("compare-snapshots")
    mirror_compare.add_argument("--left", required=True)
    mirror_compare.add_argument("--right", required=True)
    mirror_refresh = mirror_sub.add_parser("refresh")
    mirror_refresh.add_argument("--confirm", default="")
    mirror_refresh.add_argument("--changed", action="append", default=[])
    mirror_publish = mirror_sub.add_parser("publish")
    mirror_publish.add_argument("--confirm", default="")
    mirror_publish.add_argument("--changed", action="append", default=[])
    mirror_publish.add_argument("--remote", default="")
    mirror_publish.add_argument("--branch", default="")
    mirror_release = mirror_sub.add_parser("release")
    mirror_release.add_argument("--confirm", default="")
    mirror_release.add_argument("--tag", required=True)
    mirror_release.add_argument("--title", default="")
    mirror_release.add_argument("--remote", default="")
    mirror_release.add_argument("--branch", default="")
    mirror_review = mirror_sub.add_parser("contract-review")
    mirror_review.add_argument("--confirm", default="")
    mirror_review.add_argument("--decision", action="append", default=[])
    mirror_review.add_argument("--summary", default="")
    mirror_review.add_argument("--release-impact", choices=("patch", "minor", "major"), default="")
    mirror_review.add_argument("--remote", default="")
    mirror_review.add_argument("--branch", default="")
    mirror_restore = mirror_sub.add_parser("restore-plan")
    mirror_restore.add_argument("--target-root", required=True)
    mirror_stage = mirror_sub.add_parser("stage")
    mirror_stage.add_argument("--target-root", required=True)
    mirror_stage.add_argument("--confirm", default="")
    args = parser.parse_args(raw_argv)
    if args.command == "plan":
        payload = compact_plan(
            args.message,
            args.risk,
            detail=args.detail,
            owner=args.owner,
            operation=args.operation,
            arguments=parse_argument_items(args.arg),
            approved=args.approve,
            deadline_seconds=args.deadline_seconds,
        )
        if args.save_action:
            payload["planned_receipt"] = save_planned_action(payload.get("action_contract", {}))
    elif args.command == "preflight":
        payload = preflight(args.message, args.risk, detail=args.detail)
    elif args.command == "run":
        if args.workflow_run_id:
            action = action_from_run_ref(args.workflow_run_id)
            if args.approve:
                action = dict(action)
                action["approved"] = True
        else:
            plan_payload = compact_plan(
                args.message,
                args.risk,
                detail="micro",
                owner=args.owner,
                operation=args.operation,
                arguments=parse_argument_items(args.arg),
                approved=args.approve,
                deadline_seconds=args.timeout,
            )
            action = plan_payload.get("action_contract", {})
        payload = execute_lifecycle(action, timeout_seconds=args.timeout, interval=args.interval) if args.auto_lifecycle else execute_action(action, timeout_seconds=args.timeout)
    elif args.command == "status":
        payload = lifecycle_status(args.workflow_run_id)
    elif args.command == "wait":
        payload = lifecycle_status(args.workflow_run_id, wait=True, timeout=args.timeout, interval=args.interval)
    elif args.command == "consume":
        payload = lifecycle_consume(args.workflow_run_id)
    elif args.command == "cancel":
        payload = lifecycle_cancel(args.workflow_run_id, approved=args.approve)
    elif args.command == "attach-result":
        try:
            raw = Path(args.owner_result_file).read_text(encoding="utf-8") if args.owner_result_file else args.owner_result_json
            owner_result = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            payload = {
                "schema": "workflow.receipt.v1",
                "ok": False,
                "status": "blocked",
                "workflow_run_id": args.workflow_run_id,
                "error": {"class": "owner_result_unreadable", "reason": f"{type(exc).__name__}: {exc}"},
            }
        else:
            payload = attach_owner_result(args.workflow_run_id, owner_result)
    elif args.command == "checkpoint":
        payload = checkpoint(
            phase=args.phase,
            trigger=args.trigger,
            summary=args.summary,
            tool=args.tool,
            evidence=args.evidence,
            pending=args.pending,
            requires_approval=args.requires_approval,
            save=args.save,
        )
    elif args.command == "closeout":
        payload = closeout(
            task_kind=args.task_kind,
            selected=args.selected,
            used=args.used,
            outcome=args.outcome,
            memory=args.memory,
            slash=args.slash,
            mcp=args.mcp,
            local_tool=args.local_tool,
            current_turn_callable=args.current_turn_callable,
            protocol_ok_only=args.protocol_ok_only,
            fallback_used=args.fallback_used,
            negative_observation=args.negative_observation,
            unverified=args.unverified,
            web_search_used=args.web_search_used,
            owner_mcp_used=args.owner_mcp_used,
            owner_mcp_fallback_reason=args.owner_mcp_fallback_reason,
            resource_request_id=args.resource_request_id,
            resource_status=args.resource_status,
            direct_web_fallback_reason=args.direct_web_fallback_reason,
            user_direct_web=args.user_direct_web,
            platform_web_required=args.platform_web_required,
            validation=args.validation + [f"notes={item}" for item in args.notes],
            memory_route=args.memory_route,
            memory_layer=args.memory_layer,
            proposal=args.proposal,
            profile_signal=args.profile_signal,
            check_profile_candidates=args.check_profile_candidates,
            check_external_knowledge=args.check_external_knowledge,
            config_changed=args.config_changed,
            major_change=args.major_change,
            changed_surface=args.changed_surface,
            validation_receipt=args.validation_receipt,
            auto_finalize=args.auto_finalize,
            finalization_project_id=args.finalization_project_id,
            finalization_title=args.finalization_title,
            finalization_summary=args.finalization_summary,
            finalization_changed_file=args.finalization_changed_file,
            finalization_evidence=args.finalization_evidence,
            finalization_backup=args.finalization_backup,
            finalization_stable_conclusion=args.finalization_stable_conclusion,
            correction=args.correction,
            verified_root_cause=args.verified_root_cause,
            regression_test=args.regression_test,
            prevention_guard=args.prevention_guard,
            repeated_manual_step=args.repeated_manual_step,
            save=args.save,
        )
    elif args.command == "mirror":
        payload = execute_mirror_command(
            args.mirror_action,
            target_root=getattr(args, "target_root", ""),
            confirm=getattr(args, "confirm", ""),
            changed_paths=getattr(args, "decision", getattr(args, "changed", [])),
            left_snapshot=getattr(args, "left", ""),
            right_snapshot=getattr(args, "right", ""),
            remote=getattr(args, "remote", ""),
            branch=getattr(args, "branch", ""),
            tag=getattr(args, "tag", ""),
            title=getattr(args, "summary", getattr(args, "title", "")),
            release_impact=getattr(args, "release_impact", ""),
        )
    elif args.command == "maintenance":
        if args.maintenance_command == "catalog":
            payload = query_maintenance_registry(system=args.system, term=args.term, action=args.action, limit=args.limit)
        elif args.maintenance_command == "build-index":
            payload = build_maintenance_index(apply=args.apply)
        elif args.maintenance_command == "metrics":
            payload = maintenance_registry_metrics()
        elif args.maintenance_command in {"doctor", "validate"}:
            payload = maintenance_registry_doctor()
        else:
            message = f"run maintenance capability {args.capability_id} action {args.action}"
            plan = build_plan(message, risk="low")
            action = build_action(
                plan,
                message=message,
                owner="maintenance",
                operation="owner_command",
                arguments={"capability_id": args.capability_id, "subcommand": args.action, "cli_arg": args.cli_arg},
                approved=False,
                deadline_seconds=args.timeout,
            )
            payload = execute_action(action, timeout_seconds=args.timeout)
    else:
        payload = build_review_summary(closeout(task_kind="review_summary", outcome="ok"), limit=args.limit)
    output = closeout_cli_projection(payload, full=args.full_output) if args.command == "closeout" else payload
    print_json(output)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
