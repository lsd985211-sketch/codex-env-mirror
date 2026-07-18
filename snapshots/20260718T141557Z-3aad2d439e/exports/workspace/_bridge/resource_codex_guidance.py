#!/usr/bin/env python3
"""Codex-facing guidance for resource-layer receipts.

Ownership: derive compact next-step guidance from resource requests, attempts,
and receipts so Codex can continue delegation without re-parsing raw attempts.
Non-goals: fetching resources, choosing on behalf of the user, calling MCP
tools, changing network state, or writing files.
State behavior: pure read-only transformation of in-memory payloads.
Caller context: resource broker receipts and progress views.
"""

from __future__ import annotations

from typing import Any


FAILED_NEXT_ACTION_ALLOWLIST = {
    "adjust_request_constraints",
    "retry_with_corrected_resource_reference",
    "surface_owner_tool_failure",
    "use_package_manager_policy_before_install",
    "inspect_error_and_retry_or_escalate",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _candidate_url(candidate: dict[str, Any]) -> str:
    return _text(candidate.get("url") or candidate.get("download_url") or candidate.get("source_url"))


def _candidate_title(candidate: dict[str, Any]) -> str:
    return _text(candidate.get("title") or candidate.get("name") or candidate.get("selected_name"))


def _source_selection_attempt(receipt: dict[str, Any]) -> dict[str, Any]:
    for attempt in _as_list(receipt.get("attempts")):
        if not isinstance(attempt, dict):
            continue
        if attempt.get("tool") == "resource_source_strategy":
            return attempt
    return {}


def source_selection_result(receipt: dict[str, Any]) -> dict[str, Any]:
    attempt = _source_selection_attempt(receipt)
    return _as_dict(attempt.get("result"))


def candidate_summary(receipt: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    result = source_selection_result(receipt)
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(result.get("candidates"))[: max(0, limit)], start=1):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "rank": index,
                "id": _text(item.get("id") or item.get("source_id") or item.get("selected_source_id")),
                "title": _candidate_title(item),
                "url": _candidate_url(item),
                "owner_tool": _text(item.get("owner_tool")),
                "source_type": _text(item.get("source_type")),
                "license": _text(item.get("license") or item.get("license_url")),
                "score": item.get("score", item.get("relevance_score", "")),
                "reason": _text(item.get("reason") or item.get("notes")),
            }
        )
    return rows


def _selected_candidate(result: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    selected_url = _text(result.get("selected_url"))
    selected_id = _text(result.get("selected_source_id"))
    for candidate in candidates:
        if selected_url and candidate.get("url") == selected_url:
            return candidate
        if selected_id and candidate.get("id") == selected_id:
            return candidate
    if selected_url:
        return {
            "rank": 1,
            "id": selected_id,
            "title": _text(result.get("selected_name")),
            "url": selected_url,
        }
    return candidates[0] if candidates else {}


def refined_request_seed(request: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Return a safe follow-up materialization seed for a selected candidate."""

    result = source_selection_result(receipt)
    candidates = candidate_summary(receipt, limit=5)
    selected = _selected_candidate(result, candidates)
    metadata = dict(_as_dict(request.get("metadata")))
    metadata.pop("source_selection_only", None)
    metadata["source_selection_result"] = {
        "selected_url": _text(selected.get("url") or result.get("selected_url")),
        "selected_name": _text(selected.get("title") or result.get("selected_name")),
        "selected_source_id": _text(selected.get("id") or result.get("selected_source_id")),
        "candidate_count": len(_as_list(result.get("candidates"))),
    }
    metadata["candidate_reviewed_by"] = "codex"
    seed = {
        "task": _text(request.get("task")),
        "target": _text(request.get("target")),
        "url": metadata["source_selection_result"]["selected_url"],
        "path": "",
        "name": _text(request.get("name") or metadata["source_selection_result"]["selected_name"]),
        "intent": "explicit_user_url",
        "need_materialization": True,
        "allow_network": bool(request.get("allow_network", True)),
        "allow_filesystem_write": bool(request.get("allow_filesystem_write")),
        "max_bytes": request.get("max_bytes"),
        "expected_sha256": _text(request.get("expected_sha256")),
        "timeout_seconds": int(request.get("timeout_seconds") or 30),
        "retry_budget": int(request.get("retry_budget") or 1),
        "target_dir": _text(request.get("target_dir")),
        "metadata": metadata,
    }
    return seed


def build_codex_guidance(request: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Build compact guidance for Codex's next resource-layer action."""

    status = _text(receipt.get("status"))
    result_kind = _text(receipt.get("result_kind"))
    metadata = _as_dict(request.get("metadata"))
    selection_only = bool(metadata.get("source_selection_only")) or result_kind == "source_selection"
    candidates = candidate_summary(receipt, limit=5)
    guidance: dict[str, Any] = {
        "schema": "resource_codex_guidance.v1",
        "status": status,
        "result_kind": result_kind,
        "codex_next_action": _text(receipt.get("next_action") or "inspect_resource_receipt"),
        "resource_need_satisfied": status == "completed" and not selection_only,
        "same_need_fetch_allowed": status in {"failed", "blocked", "deferred"} and not bool(receipt.get("ok")),
        "candidate_review_required": False,
        "candidate_count": len(candidates),
        "candidate_summary": candidates,
        "refined_request_seed": {},
        "refinement_options": [],
        "reason": "",
    }
    if status == "completed" and selection_only:
        guidance.update(
            {
                "codex_next_action": "review_candidates_and_resubmit_selected_source",
                "resource_need_satisfied": False,
                "same_need_fetch_allowed": False,
                "candidate_review_required": True,
                "refined_request_seed": refined_request_seed(request, receipt),
                "refinement_options": [
                    "select_candidate_url_and_resubmit_materialization",
                    "tighten_keywords_or_source_kind_and_retry_source_selection",
                    "ask_user_if_candidates_are_ambiguous_or_license_sensitive",
                ],
                "reason": "source_selection_completed_materialization_deferred",
            }
        )
    elif status == "completed":
        guidance.update({"codex_next_action": "consume_resource", "reason": "resource_completed"})
    elif status == "handoff_required":
        guidance.update(
            {
                "codex_next_action": "call_owner_tool_and_attach_result",
                "resource_need_satisfied": False,
                "same_need_fetch_allowed": False,
                "refinement_options": ["perform_owner_tool_call_for_same_request_id"],
                "reason": "owner_tool_handoff_required",
            }
        )
    elif status in {"failed", "blocked", "deferred"}:
        failed_action = _text(receipt.get("next_action"))
        error_class = _text(receipt.get("error_class") or status)
        if status in {"failed", "blocked"} and failed_action not in FAILED_NEXT_ACTION_ALLOWLIST:
            failed_action = "inspect_error_and_retry_or_escalate"
        elif not failed_action:
            failed_action = "refine_request_and_retry_resource_layer"
        fetch_allowed = True
        if error_class == "insufficient_coverage":
            failed_action = "refine_resource_delegation_and_retry"
            fetch_allowed = False
        guidance.update(
            {
                "codex_next_action": failed_action,
                "resource_need_satisfied": False,
                "same_need_fetch_allowed": fetch_allowed,
                "refinement_options": [
                    "tighten_keywords",
                    "change_source_kind_or_owner_tool",
                    "expand_source_set_or_required_source_count",
                    "increase_timeout_or_size_budget_when_safe",
                    "surface_blocker_if_policy_or_permission_limited",
                ],
                "reason": error_class,
            }
        )
    return guidance
