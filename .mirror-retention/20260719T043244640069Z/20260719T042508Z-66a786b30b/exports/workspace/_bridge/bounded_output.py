#!/usr/bin/env python3
"""Bounded machine-output projections for Codex maintenance surfaces.

Ownership: shared diagnostic serialization contract.
Non-goals: business-state storage, log retention, pagination backends, or
owner-specific semantic summaries.
State behavior: pure/read-only; callers persist full artifacts separately.
Caller context: workflow, maintenance, scheduler, resource, and MCP facades.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


DEFAULT_MAX_BYTES = 8 * 1024
DEFAULT_MAX_DEPTH = 6
DEFAULT_MAX_ITEMS = 20
DEFAULT_MAX_STRING = 1200
OUTPUT_EVIDENCE_POLICY = {
    "success": "bounded_traceable_summary",
    "failure": "decision_complete_inline_evidence",
    "failure_reference_required": True,
    "full": "richer_bounded_projection_with_artifact_reference",
    "aggregation_rule": "aggregates_supplement_not_replace_actionable_rows",
    "full_detail_access": "explicit_id_or_artifact_reference",
}


DEFAULT_DECISION_KEYS = (
    "schema",
    "ok",
    "status",
    "error",
    "reason",
    "next_action",
    "owner_status",
    "retryable",
    "total",
    "returned",
    "has_more",
    "cursor",
    "run_ref",
    "raw_result_ref",
    "artifacts",
)

ACTIONABLE_ROW_KEYS = (
    "name",
    "code",
    "severity",
    "scope",
    "message",
    "summary",
    "reason",
    "error",
    "detail",
    "details",
    "affected_objects",
    "next_action",
    "safe_next_step",
    "manual_action",
    "validation_command",
    "command",
    "owner",
    "owner_status",
    "result_ref",
    "raw_result_ref",
    "elapsed_ms",
)

# Keep failure evidence visible when a nested result must be bounded. This is
# deliberately shared so every owner projection follows the same contract.
DECISION_FIRST_KEYS = (
    "ok",
    "status",
    "output_mode",
    "record_path",
    "task_kind",
    "severity",
    "code",
    "error",
    "reason",
    "message",
    "summary",
    "decision_evidence",
    "finalization",
    "post_closeout_mirror",
    "section_index",
    "detail",
    "details",
    "blockers",
    "failures",
    "errors",
    "issues",
    "actionable_failures",
    "actionable_issues",
    "next_action",
    "safe_next_step",
    "manual_action",
    "validation_command",
    "result_ref",
    "raw_result_ref",
    "direct_web_allowed",
    "matched_reason",
    "matched_reason_description",
    "platform_web_required",
    "resource_request_id",
    "resource_status",
    "fallback_reason",
    "evidence",
    "route_chain_evidence",
)


def output_evidence_policy() -> dict[str, Any]:
    return dict(OUTPUT_EVIDENCE_POLICY)


def governed_cli_payload(
    payload: Mapping[str, Any],
    *,
    full: bool = False,
    full_result_ref: str = "",
    max_success_bytes: int = DEFAULT_MAX_BYTES,
    max_full_bytes: int = 32 * 1024,
) -> dict[str, Any]:
    """Project CLI output through the shared evidence contract.

    Default success output is short. Full output is a richer bounded diagnostic
    view, not an unbounded raw dump. Failures keep decision fields and
    actionable rows inline while pointing to the full artifact or command.
    """
    result = dict(payload)
    if full:
        projected = bounded_payload(
            result,
            max_bytes=max_full_bytes,
            max_depth=8,
            max_items=60,
            max_string=2400,
            preserve_keys=(
                "severity",
                "summary",
                "issues",
                "blockers",
                "failures",
                "errors",
                "checks",
                "actionable_failures",
                "actionable_issues",
                "next_action",
            ),
            artifact_ref=full_result_ref,
        )
        projected["output_mode"] = "full_bounded"
        projected["output_evidence_policy"] = output_evidence_policy()
        if full_result_ref:
            projected["raw_result_ref"] = full_result_ref
        return projected
    if result.get("ok") is not True:
        projected = bounded_payload(
            result,
            max_bytes=max(16 * 1024, max_success_bytes),
            max_depth=7,
            max_items=50,
            max_string=2000,
            preserve_keys=(
                "severity",
                "summary",
                "issues",
                "blockers",
                "failures",
                "errors",
                "actionable_failures",
                "actionable_issues",
                "next_action",
            ),
            artifact_ref=full_result_ref,
        )
        projected["output_mode"] = "failure_bounded"
        projected["output_evidence_policy"] = output_evidence_policy()
        if full_result_ref:
            projected["raw_result_ref"] = full_result_ref
        return projected
    projected = bounded_payload(
        result,
        max_bytes=max_success_bytes,
        preserve_keys=("severity", "summary", "issues", "blockers", "checks"),
        artifact_ref=full_result_ref,
    )
    projected["output_mode"] = "default_bounded"
    projected["output_evidence_policy"] = output_evidence_policy()
    if full_result_ref:
        projected["raw_result_ref"] = full_result_ref
    return projected


def _actionable_row(value: Any, *, source: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        row = {key: value[key] for key in ACTIONABLE_ROW_KEYS if key in value}
        if not row:
            row = {"detail": bounded_value(value, max_depth=4, max_items=12, max_string=1200)}
    else:
        row = {"detail": bounded_value(value, max_depth=3, max_items=12, max_string=1200)}
    return {"source": source, **row}


def aggregate_validator_cli_payload(
    payload: Mapping[str, Any],
    *,
    full: bool = False,
    full_result_ref: str = "",
    max_inline_bytes: int = 12 * 1024,
    max_actionable_rows: int = 20,
) -> dict[str, Any]:
    """Project aggregate validators without hiding the failing child rows."""
    result = dict(payload)
    if full:
        return governed_cli_payload(result, full=True, full_result_ref=full_result_ref)

    checks = result.get("checks") if isinstance(result.get("checks"), list) else []
    failed_checks = [item for item in checks if isinstance(item, Mapping) and item.get("ok") is False]
    issue_rows = result.get("issues") if isinstance(result.get("issues"), list) else []
    blocker_rows = result.get("blockers") if isinstance(result.get("blockers"), list) else []

    candidates: list[tuple[str, Any]] = [("checks", item) for item in failed_checks]
    for field in ("failed_checks", "failed", "failures", "errors"):
        rows = result.get(field)
        if isinstance(rows, list):
            candidates.extend((field, item) for item in rows)
        elif rows not in (None, "", False, 0):
            candidates.append((field, rows))
    candidates.extend(("blockers", item) for item in blocker_rows)
    if result.get("ok") is not True:
        candidates.extend(("issues", item) for item in issue_rows)

    seen: set[str] = set()
    actionable_failures: list[dict[str, Any]] = []
    for source, item in candidates:
        identity = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if identity in seen:
            continue
        seen.add(identity)
        actionable_failures.append(_actionable_row(item, source=source))

    if result.get("ok") is not True and not actionable_failures:
        actionable_failures.append(
            {
                "source": "aggregate_contract",
                "code": "aggregate_failed_without_actionable_rows",
                "severity": "risk",
                "reason": str(result.get("reason") or result.get("error") or "aggregate validator returned ok=false without child failure evidence"),
                "next_action": str(result.get("next_action") or "rerun the validator with --full and repair the aggregate result contract"),
            }
        )

    actionable_issues = [_actionable_row(item, source="issues") for item in issue_rows]
    failure_count = len(actionable_failures)
    summary = {
        "schema": result.get("schema"),
        "ok": result.get("ok"),
        "status": result.get("status"),
        "generated_at": result.get("generated_at"),
        "check_count": len(checks),
        "passed_count": len(checks) - len(failed_checks),
        "failed_check_count": len(failed_checks),
        "failure_count": failure_count,
        "issue_count": len(issue_rows),
        "blocker_count": len(blocker_rows),
        "actionable_failures": actionable_failures[:max_actionable_rows],
        "actionable_issues": actionable_issues[:max_actionable_rows] if result.get("ok") is True else [],
        "next_action": result.get("next_action"),
        "detail_rule": "aggregate counts supplement actionable rows; use raw_result_ref for complete child results",
    }
    if failure_count > max_actionable_rows:
        summary["omitted_failure_count"] = failure_count - max_actionable_rows
    if len(actionable_issues) > max_actionable_rows:
        summary["omitted_issue_count"] = len(actionable_issues) - max_actionable_rows
    projected = bounded_payload(
        summary,
        max_bytes=max_inline_bytes,
        max_items=max_actionable_rows,
        max_string=1200,
        preserve_keys=(
            "schema",
            "ok",
            "status",
            "generated_at",
            "check_count",
            "passed_count",
            "failed_check_count",
            "failure_count",
            "issue_count",
            "blocker_count",
            "actionable_failures",
            "actionable_issues",
        ),
        artifact_ref=full_result_ref,
    )
    projected["output_evidence_policy"] = output_evidence_policy()
    if full_result_ref:
        projected["raw_result_ref"] = full_result_ref
    return projected


def json_size_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"{value[:limit]}...<truncated:{omitted} chars>"


def bounded_value(
    value: Any,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_string: int = DEFAULT_MAX_STRING,
    _depth: int = 0,
) -> Any:
    """Recursively bound strings, collections, and nesting without changing sources."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_text(value, max_string)
    if _depth >= max_depth:
        if isinstance(value, Mapping):
            decision = {
                str(key): bounded_value(
                    item,
                    max_depth=max_depth,
                    max_items=min(max_items, 8),
                    max_string=max_string,
                    _depth=_depth + 1,
                )
                for key, item in value.items()
                if str(key) in DECISION_FIRST_KEYS
            }
            if decision:
                decision["_truncated_fields"] = max(0, len(value) - len(decision))
                return decision
            return {"_truncated": "max_depth", "field_count": len(value)}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            rows = []
            for item in list(value)[:max_items]:
                if isinstance(item, Mapping):
                    row = {
                        str(key): bounded_value(
                            child,
                            max_depth=max_depth,
                            max_items=min(max_items, 8),
                            max_string=max_string,
                            _depth=_depth + 1,
                        )
                        for key, child in item.items()
                        if str(key) in DECISION_FIRST_KEYS
                    }
                    rows.append(row or {"_truncated": "max_depth", "field_count": len(item)})
                else:
                    rows.append(_truncate_text(repr(item), max_string))
            if len(value) > max_items:
                rows.append({"_truncated_items": len(value) - max_items})
            return rows
        return _truncate_text(repr(value), max_string)
    if isinstance(value, Mapping):
        entries = list(value.items())
        priority = {key: index for index, key in enumerate(DECISION_FIRST_KEYS)}
        items = sorted(
            enumerate(entries),
            key=lambda indexed: (priority.get(str(indexed[1][0]), len(priority)), indexed[0]),
        )
        output = {
            str(key): bounded_value(
                item,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
                _depth=_depth + 1,
            )
            for _, (key, item) in items[:max_items]
        }
        if len(items) > max_items:
            output["_truncated_fields"] = len(items) - max_items
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if _depth >= max_depth:
            rows = []
            for item in list(value)[:max_items]:
                if isinstance(item, Mapping):
                    row = {
                        str(key): bounded_value(
                            child,
                            max_depth=max_depth,
                            max_items=min(max_items, 8),
                            max_string=max_string,
                            _depth=_depth + 1,
                        )
                        for key, child in item.items()
                        if str(key) in DECISION_FIRST_KEYS
                    }
                    rows.append(row or {"_truncated": "max_depth", "field_count": len(item)})
                else:
                    rows.append(_truncate_text(repr(item), max_string))
            if len(value) > max_items:
                rows.append({"_truncated_items": len(value) - max_items})
            return rows
        output = [
            bounded_value(
                item,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
                _depth=_depth + 1,
            )
            for item in list(value)[:max_items]
        ]
        if len(value) > max_items:
            output.append({"_truncated_items": len(value) - max_items})
        return output
    return _truncate_text(repr(value), max_string)


def bounded_payload(
    payload: Mapping[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_string: int = DEFAULT_MAX_STRING,
    preserve_keys: Sequence[str] = (),
    artifact_ref: str = "",
) -> dict[str, Any]:
    """Return a deterministic inline projection with an explicit budget receipt."""
    original_bytes = json_size_bytes(payload)
    effective_preserve_keys = tuple(dict.fromkeys((*DEFAULT_DECISION_KEYS, *preserve_keys)))
    ordered_payload = {
        **{key: payload[key] for key in effective_preserve_keys if key in payload},
        **{key: value for key, value in payload.items() if key not in effective_preserve_keys},
    }
    attempts = (
        (max_items, max_string, max_depth),
        (min(max_items, 12), min(max_string, 700), min(max_depth, 5)),
        (min(max_items, 8), min(max_string, 360), min(max_depth, 4)),
        (min(max_items, 5), min(max_string, 180), min(max_depth, 3)),
    )
    projected: dict[str, Any] = {}
    for item_limit, string_limit, depth_limit in attempts:
        candidate = bounded_value(
            ordered_payload,
            max_depth=depth_limit,
            max_items=item_limit,
            max_string=string_limit,
        )
        projected = candidate if isinstance(candidate, dict) else {"result": candidate}
        if json_size_bytes(projected) <= max_bytes:
            break
    else:
        essential = {
            key: bounded_value(payload.get(key), max_depth=3, max_items=5, max_string=180)
            for key in effective_preserve_keys
            if key in payload
        }
        projected = {
            **essential,
            "summary": "inline result exceeded output budget; inspect the referenced artifact for details",
        }

    budget = {
        "max_inline_bytes": max_bytes,
        "original_bytes": original_bytes,
        "returned_bytes": json_size_bytes(projected),
        "truncated": original_bytes > json_size_bytes(projected),
        "artifact_ref": artifact_ref,
        "detail_rule": "default output is bounded; fetch detail explicitly by reference",
        "functional_summary_rule": "aggregation supplements representative decision rows; it does not replace actionable results",
    }
    projected["output_budget"] = budget
    budget["returned_bytes"] = json_size_bytes(projected)
    return projected
