#!/usr/bin/env python3
"""Bounded reporting projections for resource-process lifecycle actions.

Ownership: compact success receipts for resource-process cleanup operations.
Non-goals: process discovery, cleanup eligibility, termination, or validation.
State behavior: pure/read-only projection of an owner result.
Caller context: ``resource_process_doctor.py`` CLI serialization.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def _stop_state(row: dict[str, Any]) -> str:
    result = row.get("stop_result") if isinstance(row.get("stop_result"), dict) else {}
    if result.get("dry_run"):
        return "dry_run"
    if result.get("ok") is True:
        return "stopped"
    if result:
        return "failed"
    return "not_attempted"


def cleanup_success_projection(payload: dict[str, Any], *, preview_limit: int = 12) -> dict[str, Any]:
    """Keep cleanup decisions actionable without returning complete process rows."""
    selected = [item for item in payload.get("selected", []) if isinstance(item, dict)]
    skipped = [item for item in payload.get("skipped", []) if isinstance(item, dict)]
    results = [item for item in payload.get("results", []) if isinstance(item, dict)]
    result_states = Counter(_stop_state(item) for item in results)
    skipped_reasons = Counter(str(item.get("reason") or "unspecified") for item in skipped)
    selected_by_group = Counter(str(item.get("group") or "unknown") for item in selected)
    preview = [
        {
            "group": str(item.get("group") or ""),
            "pid": int(item.get("pid") or 0),
            "age_minutes": round(float(item.get("age_minutes") or 0.0), 1),
            "selection_mode": str(item.get("selection_mode") or ""),
            "protected": bool(item.get("protected")),
            "result": _stop_state(item),
        }
        for item in selected[: max(1, preview_limit)]
    ]
    return {
        "schema": str(payload.get("schema") or "resource_process.cleanup.v1"),
        "ok": bool(payload.get("ok")),
        "generated_at": payload.get("generated_at"),
        "apply_requested": bool(payload.get("apply_requested")),
        "applied": bool(payload.get("applied")),
        "safe_apply": bool(payload.get("safe_apply")),
        "include_protected": bool(payload.get("include_protected")),
        "min_age_minutes": payload.get("min_age_minutes"),
        "selected_groups": list(payload.get("selected_groups") or []),
        "selected_count": int(payload.get("selected_count") or 0),
        "skipped_count": int(payload.get("skipped_count") or 0),
        "cleanup_ok": bool(payload.get("cleanup_ok")),
        "post_validation_ok": payload.get("post_validation_ok"),
        "selected_by_group": dict(sorted(selected_by_group.items())),
        "result_counts": dict(sorted(result_states.items())),
        "skipped_reason_counts": dict(skipped_reasons.most_common(8)),
        "selected_preview": preview,
        "selected_preview_truncated": len(selected) > len(preview),
        "pre_plan_summary": payload.get("pre_plan_summary") or {},
        "current_turn_observation_state": payload.get("current_turn_observation_state"),
        "note": payload.get("note"),
    }


def doctor_projection(payload: dict[str, Any], *, issue_limit: int = 12, group_limit: int = 20) -> dict[str, Any]:
    """Preserve diagnostic causes and actions while excluding raw process rows."""
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    groups = snapshot.get("groups") if isinstance(snapshot.get("groups"), list) else []
    issues = [item for item in payload.get("issues", []) if isinstance(item, dict)]
    issue_rows = [
        {
            key: item.get(key)
            for key in (
                "severity",
                "code",
                "group",
                "message",
                "root_instance_count",
                "working_set_mb",
                "pressure_kind",
                "manual_action",
            )
            if item.get(key) not in (None, "", [], {})
        }
        for item in issues[: max(1, issue_limit)]
    ]
    group_rows = [
        {
            "group": item.get("group"),
            "count": item.get("count"),
            "root_instance_count": item.get("root_instance_count"),
            "effective_expected_max": item.get("effective_expected_max"),
            "working_set_mb": item.get("working_set_mb"),
            "protected": item.get("protected"),
        }
        for item in groups[: max(1, group_limit)]
        if isinstance(item, dict)
    ]
    return {
        "schema": str(payload.get("schema") or "resource_process.doctor.v1"),
        "ok": bool(payload.get("ok")),
        "generated_at": payload.get("generated_at"),
        "summary": payload.get("summary") or {},
        "issues": issue_rows,
        "issue_count": len(issues),
        "issues_truncated": len(issues) > len(issue_rows),
        "groups": group_rows,
        "group_count": len(groups),
        "groups_truncated": len(groups) > len(group_rows),
        "current_turn_anchor": payload.get("current_turn_anchor") or {},
        "reporting_safety_contract": payload.get("reporting_safety_contract") or {},
        "detail_ref": "command:python _bridge/resource_process_doctor.py doctor --full",
    }
