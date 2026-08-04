#!/usr/bin/env python3
"""Pure capability selection and bounded decision receipt projection.

This module composes caller-owned facts only.  It does not discover assets,
run arbitrary probes, consume authorization, or persist receipts.  A caller
may inject the existing bounded semantic owner after its availability is
known; model failure remains evidence for the caller-owned fallback.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping


SCHEMA = "capability_decision_receipt.v1"
MAX_CANDIDATES = 16
MAX_PROBES = 3
MAX_REJECTED = 8
MAX_REASONS = 6
MAX_SEMANTIC_TEXT_CHARS = 640

_AVAILABILITY = {"ready", "unknown", "unavailable", "blocked"}
_BLOCKED_PERMISSIONS = {"blocked", "denied", "failed", "unavailable"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    """Return the deterministic digest used by session and receipt signatures."""

    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _reason_codes(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    else:
        values = _as_list(value)
    return list(dict.fromkeys(_text(item) for item in values if _text(item)))[:MAX_REASONS]


def _candidate_id(item: Mapping[str, Any]) -> str:
    return _text(item.get("candidate_id") or item.get("id") or item.get("capability_id"))


def _candidate_content(item: Mapping[str, Any], candidate_id: str) -> str:
    return _text(item.get("content") or item.get("text") or item.get("description") or candidate_id)


def _rank(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def normalize_candidates(candidates: list[Any] | None) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize candidates and report invalid rows without raising."""

    normalized: list[dict[str, Any]] = []
    issues: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(candidates or []):
        if not isinstance(raw, Mapping):
            issues.append(f"candidate_object_required:{index}")
            continue
        candidate_id = _candidate_id(raw)
        if not candidate_id:
            issues.append(f"candidate_identity_missing:{index}")
            continue
        if candidate_id in seen:
            issues.append(f"candidate_identity_duplicate:{candidate_id}")
            continue
        authority_ref = _text(raw.get("authority_ref") or raw.get("source_authority"))
        if not authority_ref:
            issues.append(f"candidate_authority_missing:{candidate_id}")
            continue
        seen.add(candidate_id)
        availability = _text(raw.get("availability") or "unknown").casefold()
        if availability not in _AVAILABILITY:
            availability = "unknown"
        content = _candidate_content(raw, candidate_id)
        normalized.append({
            "candidate_id": candidate_id,
            "kind": _text(raw.get("kind") or raw.get("capability_class") or "support"),
            "role": _text(raw.get("role") or "support"),
            "authority_ref": authority_ref,
            "source_ref": _text(raw.get("source_ref") or raw.get("path") or authority_ref),
            "entry_ref": _text(raw.get("entry_ref") or raw.get("entry_point") or raw.get("action")),
            "probe_ref": _text(raw.get("probe_ref") or ""),
            "fallback_ref": _text(raw.get("fallback_ref") or raw.get("fallback_route") or raw.get("fallback")),
            "availability": availability,
            "permission_state": _text(raw.get("permission_state") or "ready").casefold(),
            "platform_ok": raw.get("platform_ok", True),
            "decision_relevant": raw.get("decision_relevant", True) is not False,
            "reason_codes": _reason_codes(raw.get("reason_codes")),
            "lexical_rank": _rank(raw.get("lexical_rank")),
            "graph_rank": _rank(raw.get("graph_rank")),
            "content_sha256": _text(raw.get("content_sha256")) or stable_hash(content),
            "semantic_text": content[:MAX_SEMANTIC_TEXT_CHARS],
        })
    normalized.sort(key=lambda item: item["candidate_id"])
    return normalized, issues


def filter_candidates(candidates: list[Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply only deterministic safety/platform/availability filters."""

    normalized, issues = normalize_candidates(candidates)
    rejected: list[dict[str, Any]] = [
        {"candidate_id": issue.rsplit(":", 1)[-1], "reason_codes": [issue]}
        for issue in issues
        if issue.startswith("candidate_")
    ]
    accepted: list[dict[str, Any]] = []
    for item in normalized:
        reasons = list(item["reason_codes"])
        if item["availability"] in {"unavailable", "blocked"}:
            reasons.append(f"availability_{item['availability']}")
        if item["permission_state"] in _BLOCKED_PERMISSIONS:
            reasons.append("permission_blocked")
        if item["platform_ok"] is False:
            reasons.append("platform_mismatch")
        # Unknown candidates may carry discovery warnings (for example an
        # unregistered skill). Keep them visible for semantic recommendation,
        # but never let those warnings authorize execution; ready candidates
        # still require a clean contract.
        blocking_reasons = {
            "permission_blocked",
            "platform_mismatch",
            "availability_unavailable",
            "availability_blocked",
        }
        if reasons and (item["availability"] != "unknown" or blocking_reasons.intersection(reasons)):
            rejected.append({"candidate_id": item["candidate_id"], "reason_codes": list(dict.fromkeys(reasons))[:MAX_REASONS]})
        else:
            accepted.append(item)
    rejected.sort(key=lambda item: item["candidate_id"])
    return accepted, rejected[:MAX_REJECTED]


def _probe_sort_key(item: Mapping[str, Any]) -> tuple[int, int, str]:
    return (
        int(item.get("lexical_rank") or MAX_CANDIDATES + 1),
        int(item.get("graph_rank") or MAX_CANDIDATES + 1),
        _text(item.get("candidate_id")),
    )


def plan_probes(candidates: list[Any] | None, *, limit: int = MAX_PROBES) -> list[dict[str, str]]:
    """Plan bounded probes; execution belongs to an injected owner adapter."""

    normalized, _ = normalize_candidates(candidates)
    unknown = [
        item for item in normalized
        if item["availability"] == "unknown" and item["decision_relevant"] and item["probe_ref"]
    ]
    unknown.sort(key=_probe_sort_key)
    bounded = max(0, min(int(limit), MAX_PROBES))
    return [
        {"candidate_id": item["candidate_id"], "probe_ref": item["probe_ref"]}
        for item in unknown[:bounded]
    ]


def _gate_reasons(gates: list[Any] | None) -> list[str]:
    reasons: list[str] = []
    for gate in gates or []:
        if not isinstance(gate, Mapping):
            continue
        gate_id = _text(gate.get("id") or gate.get("name") or "gate")
        status = _text(gate.get("status")).casefold()
        if gate.get("satisfied") is False or status in {"blocked", "denied", "failed", "unresolved"}:
            reasons.append(f"required_gate_unsatisfied:{gate_id}")
    return sorted(set(reasons))


def _owner_projection(owner: Any) -> dict[str, str]:
    if isinstance(owner, str):
        return {"name": _text(owner), "authority_ref": "", "entry_ref": ""}
    value = _as_dict(owner)
    return {
        "name": _text(value.get("name") or value.get("owner")),
        "authority_ref": _text(value.get("authority_ref")),
        "entry_ref": _text(value.get("entry_ref") or value.get("entry_point")),
    }


def _acceptance_projection(value: Any) -> dict[str, str]:
    item = _as_dict(value)
    return {
        "predicate": _text(item.get("predicate") or item.get("acceptance_predicate")),
        "consume_ref": _text(item.get("consume_ref") or item.get("consumer")),
    }


def _candidate_projection(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in (
            "candidate_id", "kind", "role", "authority_ref", "source_ref", "entry_ref",
            "probe_ref", "fallback_ref", "availability", "reason_codes", "lexical_rank",
            "graph_rank", "content_sha256",
        )
    }


def _ranking_projection(value: Any) -> dict[str, Any]:
    item = _as_dict(value)
    results = _as_list(item.get("results"))
    margin = item.get("top_margin")
    if margin is None and len(results) >= 2 and all(isinstance(row, Mapping) for row in results[:2]):
        first = results[0].get("rrf_score", results[0].get("score"))
        second = results[1].get("rrf_score", results[1].get("score"))
        if isinstance(first, (int, float)) and isinstance(second, (int, float)):
            margin = round(float(first) - float(second), 10)
    return {
        "method": _text(item.get("method") or "structured_only"),
        "semantic_used": bool(item.get("semantic_used") or item.get("vector_used")),
        "vector_reason": _text(item.get("vector_reason")),
        "retrievers": [_text(value) for value in _as_list(item.get("retrievers")) if _text(value)][:3],
        "ambiguous": bool(item.get("ambiguous")),
        "selection_reason": _text(item.get("selection_reason")),
        "top_margin": item.get("selection_margin") if item.get("selection_margin") is not None else margin,
        "fallback": _text(item.get("fallback")),
        "fallback_used": bool(item.get("fallback_used")),
    }


def semantic_ranking(
    query: str,
    candidates: list[Any] | None,
    *,
    semantic_selector: Callable[[str, list[dict[str, Any]]], Mapping[str, Any]] | None,
    semantic_probe_result: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Call one injected semantic owner for a bounded ready candidate set.

    Candidate command bodies, probe refs, permission refs, and arbitrary
    metadata are never sent to the model owner.  An unavailable probe prevents
    the call entirely.  Transport exceptions are converted into the same
    fail-closed fallback evidence returned by the owner.
    """

    if semantic_selector is None or not _text(query):
        return None
    probe = _as_dict(semantic_probe_result)
    if probe and (probe.get("callable") is False or probe.get("healthy") is False):
        return {
            "method": "semantic_capability_owner.select",
            "ok": False,
            "vector_used": False,
            "vector_reason": _text(probe.get("vector_reason") or "semantic_probe_unavailable"),
            "selected": None,
            "ambiguous": True,
            "selection_reason": "semantic_owner_unavailable",
            "fallback": _text(probe.get("fallback") or "structured_fts_graph"),
            "fallback_used": True,
            "results": [],
        }
    normalized, _ = normalize_candidates(candidates)
    eligible = [
        item for item in normalized
        if item["availability"] in {"ready", "unknown"}
        and item["decision_relevant"]
        and item["kind"] != "semantic_model"
        and item["candidate_id"] != "model:bge-m3"
        and item["permission_state"] not in _BLOCKED_PERMISSIONS
        and item["platform_ok"] is not False
        and (not item["reason_codes"] or item["availability"] == "unknown")
    ]
    if len(eligible) < 2:
        return None
    payload = [
        {
            **{
                "id": item["candidate_id"],
                "text": item["semantic_text"],
                "source_ref": item["source_ref"],
                "content_sha256": item["content_sha256"],
            },
            **({"lexical_rank": item["lexical_rank"]} if item["lexical_rank"] is not None else {}),
            **({"graph_rank": item["graph_rank"]} if item["graph_rank"] is not None else {}),
        }
        for item in eligible
    ]
    try:
        result = dict(semantic_selector(_text(query), payload))
    except Exception as exc:  # owner transport failures must never break routing
        result = {
            "ok": False,
            "vector_used": False,
            "vector_reason": f"transport_failed:{type(exc).__name__}",
            "selected": None,
            "ambiguous": True,
            "selection_reason": "semantic_owner_unavailable",
            "fallback": "caller_owned_lexical_graph_order",
            "fallback_used": True,
            "results": [],
        }
    result["method"] = "semantic_capability_owner.select"
    return result


def _receipt_signature(payload: Mapping[str, Any]) -> str:
    return stable_hash({key: value for key, value in payload.items() if key != "receipt_signature"})


def decide_capabilities(
    *,
    task_contract_ref: str,
    primary_owner: Mapping[str, Any] | str,
    candidates: list[Any] | None,
    acceptance: Mapping[str, Any] | None,
    required_gates: list[Any] | None = None,
    ranking_result: Mapping[str, Any] | None = None,
    probe_results: list[Mapping[str, Any]] | None = None,
    permission_ref: str = "",
    source_signature: str = "",
    environment_signature: str = "",
    simple_fast_path: bool = False,
) -> dict[str, Any]:
    """Build one deterministic, bounded decision receipt from caller facts."""

    owner = _owner_projection(primary_owner)
    acceptance_projection = _acceptance_projection(acceptance)
    normalized, normalization_issues = normalize_candidates(candidates)
    accepted, rejected = filter_candidates(normalized)
    reasons = _gate_reasons(required_gates)
    if not owner["name"]:
        reasons.append("primary_owner_missing")
    if not acceptance_projection["predicate"] or not acceptance_projection["consume_ref"]:
        reasons.append("acceptance_contract_missing")

    planned_probes: list[dict[str, str]] = []
    supplied_probe_results = {
        _text(item.get("candidate_id")): item
        for item in probe_results or []
        if isinstance(item, Mapping) and _text(item.get("candidate_id"))
    }
    for item in accepted:
        probe = supplied_probe_results.get(item["candidate_id"])
        if probe:
            observed = _text(probe.get("result") or "unknown").casefold()
            if observed in _AVAILABILITY:
                item["availability"] = observed
    if not reasons and not simple_fast_path:
        planned_probes = plan_probes(accepted)
        if planned_probes:
            reasons.append("decision_relevant_probe_required")

    ranking = _ranking_projection(ranking_result)
    selected: dict[str, Any] | None = None
    recommended: list[dict[str, Any]] = []
    selected_id = _text(_as_dict(ranking_result).get("selected"))
    if selected_id and not _gate_reasons(required_gates):
        ranked_candidate = next(
            (item for item in accepted if item["candidate_id"] == selected_id),
            None,
        )
        if ranked_candidate and ranked_candidate["availability"] != "ready":
            recommended = [_candidate_projection(ranked_candidate)]
            reasons.append("semantic_recommendation_requires_owner_probe")
    if not reasons and not simple_fast_path:
        eligible = [
            item for item in accepted
            if item["availability"] == "ready" and not item["reason_codes"]
        ]
        if not selected_id and len(eligible) == 1:
            selected = eligible[0]
        elif ranking["ambiguous"]:
            reasons.append(f"ranking_ambiguous:{ranking['selection_reason'] or 'margin_too_small'}")
        elif selected_id:
            selected = next((item for item in eligible if item["candidate_id"] == selected_id), None)
            if selected is None:
                reasons.append("ranking_candidate_not_eligible")
        elif len(eligible) > 1:
            reasons.append("multiple_ready_candidates")
        else:
            reasons.append("no_ready_candidate")

    if selected is not None and (not selected["entry_ref"] or not selected["fallback_ref"]):
        reasons.append("selected_candidate_contract_incomplete")
        selected = None

    if normalization_issues:
        rejected.extend({"candidate_id": issue.rsplit(":", 1)[-1], "reason_codes": [issue]} for issue in normalization_issues)
    rejected.sort(key=lambda item: item["candidate_id"])
    status = "selected" if simple_fast_path or (selected is not None and not reasons) else "blocked" if _gate_reasons(required_gates) else "judgment_required"
    source = _text(source_signature) or stable_hash({"task_contract_ref": task_contract_ref, "owner": owner, "candidates": normalized})
    environment = _text(environment_signature) or stable_hash({"candidate_states": [(item["candidate_id"], item["availability"]) for item in normalized]})
    selected_support = [] if simple_fast_path or selected is None else [_candidate_projection(selected)]
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": status,
        "simple_fast_path": bool(simple_fast_path),
        "task_contract_ref": _text(task_contract_ref),
        "primary_owner": owner,
        "selected_supporting_assets": selected_support,
        "recommended_supporting_assets": recommended,
        "rejected_candidates": rejected[:MAX_REJECTED],
        "probe_requests": [] if simple_fast_path else planned_probes,
        "probe_receipt_refs": [
            _text(item.get("probe_receipt_ref")) for item in supplied_probe_results.values()
            if _text(item.get("probe_receipt_ref"))
        ][:MAX_PROBES],
        "ranking": ranking,
        "fallback_ref": _text((selected or {}).get("fallback_ref") or owner.get("fallback_ref")),
        "acceptance": acceptance_projection,
        "permission_ref": _text(permission_ref),
        "source_signature": source,
        "environment_signature": environment,
        "reason_codes": sorted(set(reasons))[:MAX_REASONS],
    }
    receipt["receipt_signature"] = _receipt_signature(receipt)
    return receipt


def decide_from_environment_view(
    *,
    task_contract_ref: str,
    primary_owner: Mapping[str, Any] | str,
    environment_view: Mapping[str, Any],
    acceptance: Mapping[str, Any] | None,
    required_gates: list[Any] | None = None,
    ranking_result: Mapping[str, Any] | None = None,
    probe_results: list[Mapping[str, Any]] | None = None,
    permission_ref: str = "",
    semantic_query: str = "",
    semantic_probe_result: Mapping[str, Any] | None = None,
    semantic_selector: Callable[[str, list[dict[str, Any]]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Adapt one existing environment view without discovering or executing anything."""

    view = _as_dict(environment_view)
    candidates = _as_list(view.get("candidates"))[:MAX_CANDIDATES]
    ranking = ranking_result
    if ranking is None:
        ranking = semantic_ranking(
            semantic_query,
            candidates,
            semantic_selector=semantic_selector,
            semantic_probe_result=semantic_probe_result,
        )
    return decide_capabilities(
        task_contract_ref=task_contract_ref,
        primary_owner=primary_owner,
        candidates=candidates,
        acceptance=acceptance,
        required_gates=required_gates,
        ranking_result=ranking,
        probe_results=probe_results,
        permission_ref=permission_ref,
        source_signature=_text(view.get("source_signature")),
        environment_signature=_text(view.get("environment_signature") or view.get("source_signature")),
        simple_fast_path=bool(view.get("simple_fast_path")),
    )
