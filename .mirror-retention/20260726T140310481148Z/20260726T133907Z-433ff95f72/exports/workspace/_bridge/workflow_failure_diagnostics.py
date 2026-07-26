#!/usr/bin/env python3
"""Extract decision-complete diagnostics from heterogeneous owner failures.

Ownership: normalize a bounded set of concrete failure items for workflow
receipts. Non-goals: owner-specific repair decisions, full-log storage, or
replacing the complete owner result artifact. State behavior: pure/read-only.
Caller context: workflow_owner_facade failure normalization and tests.
"""

from __future__ import annotations

from typing import Any

from bounded_output import bounded_value


MAX_DIAGNOSTICS = 10
ITEM_FIELDS = (
    "severity",
    "code",
    "name",
    "category",
    "scope",
    "dependency",
    "owner_health_impact",
    "message",
    "reason",
    "root_cause",
    "system",
    "group",
    "owner_check",
    "stable_id",
    "affected_objects",
    "count",
    "root_pids",
    "status",
    "next_action",
    "manual_action",
    "safe_next_step",
    "repair_plan_command",
    "validation_command",
    "query_command",
    "artifact_ref",
    "evidence",
    "summary",
    "detail",
)

SEVERITY_ORDER = {"critical": 0, "error": 1, "risk": 2, "warning": 3, "warn": 3, "advisory": 4, "info": 5}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_item(value: Any, source: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        text = str(value or "").strip()
        return {"source": source, "message": text} if text else None
    if source == "checks" and value.get("ok") is True:
        return None
    item = {"source": source}
    for key in ITEM_FIELDS:
        if key not in value or value.get(key) in (None, "", [], {}):
            continue
        item[key] = bounded_value(value.get(key), max_depth=3, max_items=8, max_string=600)
    if len(item) == 1:
        item["detail"] = bounded_value(value, max_depth=3, max_items=8, max_string=600)
    return item


def _append_item(items: list[dict[str, Any]], raw: Any, source: str) -> int:
    normalized = _normalize_item(raw, source)
    if normalized is None:
        return 0
    items.append(normalized)
    nested_values: list[tuple[str, Any]] = []
    if isinstance(raw, dict):
        nested_values.extend(("details", child) for child in _as_list(raw.get("details")))
        detail = raw.get("detail")
        if isinstance(detail, list):
            nested_values.extend(("detail", child) for child in detail)
        elif isinstance(detail, dict) and any(key in detail for key in ("code", "severity", "message", "summary", "reason")):
            nested_values.append(("detail", detail))
    for nested_name, child in nested_values:
        child_item = _normalize_item(child, f"{source}.{nested_name}")
        if child_item is not None:
            items.append(child_item)
    return 1 + len(nested_values)


def extract_failure_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    """Return representative concrete failures plus counts and owner references."""
    all_items: list[dict[str, Any]] = []
    total = 0
    for source in ("issues", "checks", "signals", "blockers", "advisories", "failures", "failed"):
        for raw in _as_list(payload.get(source)):
            total += _append_item(all_items, raw, source)

    for container_name in ("validation", "doctor", "audit"):
        container = _as_dict(payload.get(container_name))
        for source in ("issues", "checks", "signals", "blockers", "failures", "failed", "critical"):
            for raw in _as_list(container.get(source)):
                total += _append_item(all_items, raw, f"{container_name}.{source}")

    all_items.sort(key=lambda item: SEVERITY_ORDER.get(str(item.get("severity") or "").lower(), 6))
    items = all_items[:MAX_DIAGNOSTICS]

    error = _as_dict(payload.get("error"))
    reason = str(payload.get("reason") or error.get("reason") or "").strip()
    if not reason and items:
        first = items[0]
        reason = str(first.get("message") or first.get("reason") or first.get("summary") or first.get("detail") or "").strip()
    next_action = str(payload.get("next_action") or "").strip()
    if not next_action:
        for item in items:
            next_action = str(item.get("next_action") or item.get("manual_action") or item.get("safe_next_step") or "").strip()
            if next_action:
                break

    result = {
        "owner_schema": payload.get("schema"),
        "owner_status": payload.get("status"),
        "reason": reason,
        "next_action": next_action,
        "diagnostic_count": total,
        "returned_count": len(items),
        "has_more": total > len(items),
        "items": items,
    }
    for key in ("artifact_ref", "result_ref", "raw_result_ref", "query_command", "repair_plan_command", "validation_command"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            result[key] = bounded_value(value, max_depth=2, max_items=6, max_string=600)
    summary = payload.get("summary")
    if isinstance(summary, dict):
        result["summary"] = bounded_value(summary, max_depth=3, max_items=10, max_string=500)
    return result
