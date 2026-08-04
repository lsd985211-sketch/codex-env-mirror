#!/usr/bin/env python3
"""Build a bounded, non-authoritative view of existing environment capabilities.

The view composes facts supplied by existing owners.  It does not discover,
execute, authorize, or persist capability state; callers remain responsible
for invoking the owning contract and consuming its result.  Semantic models
are normalized like any other owner-backed capability and never become an
implicit execution path.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA = "environment_capability_view.v1"
_TRUE_STATES = {"admitted", "accepted", "healthy", "ok", "ready", "closed", "callable"}
_FALSE_STATES = {"blocked", "denied", "failed", "missing", "stale", "unavailable", "unregistered"}
_ADMITTED_STATES = {"admitted", "accepted", "managed", "trusted"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_ref(item: dict[str, Any], authority: str, name: str) -> str:
    return _text(
        item.get("authority_ref")
        or item.get("source_ref")
        or item.get("path")
        or item.get("source")
        or f"{authority}:{name}"
    )


def _state_bool(value: Any, *, true_values: set[str] | None = None, false_values: set[str] | None = None) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = _text(value).casefold()
    if normalized in (true_values or _TRUE_STATES):
        return True
    if normalized in (false_values or _FALSE_STATES):
        return False
    return None


def _health_value(item: dict[str, Any]) -> bool | None:
    health = item.get("healthy")
    if health is not None:
        return _state_bool(health)
    health_payload = _as_dict(item.get("health"))
    return _state_bool(health_payload.get("state") or health_payload.get("status"))


def _callable_value(item: dict[str, Any], capability_class: str, admitted: bool | None) -> bool | None:
    for key in ("callable", "current_turn_callable", "current_turn_call_completed"):
        if key in item:
            return _state_bool(item.get(key))
    if capability_class == "skill":
        # A skill with no admission is a discovered contract, not an executable
        # capability.  Keep it visible while failing closed for auto execution.
        return bool(admitted is True and _text(item.get("path")))
    return None


def _admitted_value(item: dict[str, Any], capability_class: str) -> bool | None:
    if "admitted" in item:
        return _state_bool(item.get("admitted"))
    admission_state = _text(item.get("admission_state")).casefold()
    if admission_state:
        return admission_state in _ADMITTED_STATES
    if capability_class == "skill":
        return False
    return None


def _platform_scope(item: dict[str, Any]) -> list[str]:
    values = item.get("platform_scope") or item.get("platforms") or item.get("systems") or []
    if isinstance(values, dict):
        values = list(values)
    if not isinstance(values, list):
        values = [values]
    return list(dict.fromkeys(_text(value) for value in values if _text(value)))[:6]


def _evidence_refs(item: dict[str, Any], authority: str, name: str) -> list[str]:
    values = item.get("evidence_refs") or item.get("evidence") or []
    if not isinstance(values, list):
        values = [values]
    refs = [_text(value) for value in values if _text(value)]
    if not refs:
        refs = [_source_ref(item, authority, name)]
    return list(dict.fromkeys(refs))[:4]


def _normalize_candidate(item: dict[str, Any], *, capability_class: str, authority: str, primary_owner: str) -> dict[str, Any] | None:
    item = {
        **_as_dict(item.get("candidate")),
        **_as_dict(item.get("contract_projection")),
        **item,
    }
    name = _text(
        item.get("name")
        or item.get("member")
        or item.get("capability")
        or item.get("module")
        or item.get("module_path")
        or item.get("system")
    )
    if not name:
        return None
    source_ref = _source_ref(item, authority, name)
    admitted = _admitted_value(item, capability_class)
    observed = bool(
        item.get("observed") is True
        or _text(item.get("path") or item.get("entry_point") or item.get("action") or item.get("source"))
    )
    callable_value = _callable_value(item, capability_class, admitted)
    observed = observed or callable_value is not None
    healthy = _health_value(item)
    fallback = _text(item.get("fallback_route") or item.get("fallback") or item.get("local_fallback"))
    risk_hints = [_text(value) for value in _as_list(item.get("risk_hints")) if _text(value)]
    admission_state = _text(item.get("admission_state"))
    if admission_state == "unregistered":
        risk_hints.append("unregistered")
    if capability_class == "skill" and _text(item.get("metadata")):
        risk_hints.append("metadata_only_contract")
    freshness = _as_dict(item.get("freshness")) or {"state": "unknown"}
    stale = _text(item.get("index_status")).casefold() in {"index_stale", "stale"} or _text(
        freshness.get("state") or freshness.get("status")
    ).casefold() == "stale"
    if stale:
        risk_hints.append("stale_source_evidence")
        callable_value = None
        healthy = None
    capability_id = _text(item.get("capability_id")) or f"{capability_class}:{name}"
    state = "ready"
    if admitted is False:
        state = "observed_candidate" if observed else "unadmitted"
    elif callable_value is False or healthy is False:
        state = "unavailable"
    elif stale or callable_value is None or healthy is None:
        state = "unknown"
    owner_default = (
        f"skill:{name}"
        if capability_class == "skill"
        else "workflow_asset_guidance"
        if capability_class == "tool"
        else authority
    )
    return {
        "capability_id": capability_id,
        "capability_class": capability_class,
        "candidate_id": capability_id,
        "source_authority": authority,
        "authority_ref": _text(item.get("authority_ref") or authority),
        "source_ref": source_ref,
        "discovery_root": _text(item.get("discovery_root")),
        "role": "primary" if _text(item.get("owner") or item.get("owner_profile")) == primary_owner and primary_owner else "support",
        "owner": _text(item.get("owner") or item.get("owner_profile") or owner_default),
        "owner_ref": _text(item.get("owner_ref") or item.get("owner") or item.get("owner_profile") or owner_default),
        "entry_point": _text(item.get("entry_point") or item.get("action") or item.get("path") or name),
        "entry_ref": _text(item.get("entry_ref") or item.get("entry_point") or item.get("action") or item.get("path") or name),
        "contract_ref": _text(item.get("contract_ref")),
        "contract_signature": _text(item.get("contract_signature") or item.get("contract_fingerprint")),
        "candidate_signature": _text(item.get("candidate_signature")),
        "source_signature": _text(item.get("source_signature")),
        "probe_ref": _text(item.get("probe_ref") or ""),
        "fallback_ref": fallback,
        "declared": bool(_text(item.get("description") or item.get("contract") or item.get("path") or item.get("owner") or source_ref)),
        "observed": observed,
        "callable": callable_value,
        "healthy": healthy,
        "admitted": admitted,
        "admission_state": admission_state or ("admitted" if admitted is True else "unadmitted" if admitted is False else "unknown"),
        "state": state,
        "platform_scope": _platform_scope(item),
        "freshness": freshness,
        "evidence_refs": _evidence_refs(item, authority, name),
        "fallback_route": fallback,
        "availability": "ready" if state == "ready" else "unavailable" if state == "unavailable" else "unknown",
        "permission_state": _text(item.get("permission_state") or "unknown"),
        "platform_ok": item.get("platform_ok"),
        "decision_relevant": item.get("decision_relevant", True) is not False,
        "reason_codes": list(dict.fromkeys(risk_hints))[:6],
        "content_sha256": _text(item.get("content_sha256") or item.get("candidate_signature")) or _stable_hash({"id": capability_id, "name": name, "entry": _text(item.get("entry_point") or item.get("action") or item.get("path") or name), "source": source_ref}),
        "lexical_rank": item.get("lexical_rank"),
        "graph_rank": item.get("graph_rank"),
        "risk_hints": list(dict.fromkeys(risk_hints))[:6],
        "risk_class": _text(item.get("risk_class") or "unknown"),
        "missing_requirements": list(
            dict.fromkeys(_text(value) for value in _as_list(item.get("missing_requirements")) if _text(value))
        )[:8],
        "validation_ref": _text(item.get("validation_ref") or item.get("validation") or item.get("validator")),
        "relevance_score": int(item.get("score") or item.get("relevance_score") or 0),
    }


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _source_signature(candidates: list[dict[str, Any]], supplied: str = "") -> str:
    return supplied or _stable_hash(
        [
            {
                "id": item["capability_id"],
                "source": item["source_authority"],
                "evidence": item["evidence_refs"],
                "state": {key: item[key] for key in ("declared", "observed", "callable", "healthy", "admitted")},
            }
            for item in candidates
        ]
    )


def _decision_mode(candidates: list[dict[str, Any]], primary_owner: str, *, ambiguous: bool) -> str:
    ready = [item for item in candidates if item.get("admitted") is True and item.get("callable") is True and item.get("healthy") is not False]
    owner_ready = [item for item in ready if not primary_owner or item.get("owner") == primary_owner]
    if primary_owner and len(owner_ready) == 1:
        return "auto_fast_path"
    if ambiguous or len(ready) != 1:
        return "codex_select"
    return "auto_fallback"


def build_capability_view(
    message: str,
    *,
    skills: list[dict[str, Any]] | None = None,
    owners: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    environment_context: dict[str, Any] | None = None,
    primary_owner: str = "",
    source_signature: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    """Compose capability facts without querying or mutating their authorities."""
    context = _as_dict(environment_context)
    candidates: list[dict[str, Any]] = []
    skill_items = _items(skills)
    # The existing skill owner exposes a bounded description-match discovery
    # side-channel.  Use it only to surface omitted capabilities such as OCR;
    # execution selection and admission remain owned by the normal route.
    try:
        from skill_orchestrator import capability_matches

        skill_items.extend(capability_matches(message, limit=6))
    except Exception:
        pass
    for item in skill_items:
        row = _normalize_candidate(item, capability_class="skill", authority="skill_orchestrator", primary_owner=primary_owner)
        if row:
            candidates.append(row)
    for item in _items(owners):
        owner_item = dict(item)
        owner_item.setdefault("index_status", context.get("index_status"))
        row = _normalize_candidate(owner_item, capability_class="owner", authority="maintenance_capability_registry", primary_owner=primary_owner)
        if row:
            candidates.append(row)
    for item in _items(tools):
        row = _normalize_candidate(item, capability_class="tool", authority="workflow_asset_guidance", primary_owner=primary_owner)
        if row:
            candidates.append(row)
    for item in _items(context.get("mcp_routes")):
        row = _normalize_candidate(item, capability_class="mcp", authority="mcp_capability_routes", primary_owner=primary_owner)
        if row:
            candidates.append(row)
    for item in _items(context.get("semantic_capabilities")):
        row = _normalize_candidate(item, capability_class="semantic_model", authority="semantic_capability_owner", primary_owner=primary_owner)
        if row:
            row["business_scope"] = _text(item.get("business_scope"))
            row["usage_policy"] = _text(item.get("usage_policy"))
            row["business_results"] = _as_dict(item.get("business_results"))
            candidates.append(row)

    deduped: dict[str, dict[str, Any]] = {}
    for item in candidates:
        existing = deduped.get(item["capability_id"])
        if existing is None:
            deduped[item["capability_id"]] = item
            continue
        signatures = {value for value in (existing.get("contract_signature"), item.get("contract_signature")) if value}
        owner_refs = {value for value in (existing.get("owner_ref"), item.get("owner_ref")) if value}
        if len(signatures) > 1 or len(owner_refs) > 1:
            conflicted = dict(existing)
            conflict_codes = []
            if len(signatures) > 1:
                conflict_codes.append("contract_signature_conflict")
            if len(owner_refs) > 1:
                conflict_codes.append("duplicate_owner_conflict")
            conflicted["admitted"] = False
            conflicted["admission_state"] = "conflicted"
            conflicted["callable"] = False
            conflicted["healthy"] = None
            conflicted["state"] = "unavailable"
            conflicted["availability"] = "unavailable"
            conflicted["risk_hints"] = list(dict.fromkeys([*conflicted.get("risk_hints", []), *conflict_codes]))[:6]
            conflicted["reason_codes"] = list(conflicted["risk_hints"])
            conflicted["missing_requirements"] = list(
                dict.fromkeys([*conflicted.get("missing_requirements", []), "conflict_resolution"])
            )[:8]
            deduped[item["capability_id"]] = conflicted
        elif item.get("callable") is True and existing.get("callable") is not True:
            deduped[item["capability_id"]] = item
    candidates = sorted(
        deduped.values(),
        key=lambda item: (-int(item.get("relevance_score") or 0), item.get("owner") != primary_owner, item["state"], item["capability_id"]),
    )[: max(1, min(limit, 24))]
    ambiguous = bool(_as_dict(context.get("environment_decision_frame")).get("decision_mode") in {"judgment_required", "insufficient_discovery"})
    mode = _decision_mode(candidates, primary_owner, ambiguous=ambiguous)
    issues = []
    if not candidates:
        issues.append({"code": "no_owner_backed_capabilities", "reason": "bounded authority inputs produced no candidate"})
    if any(item.get("state") == "unknown" for item in candidates):
        issues.append({"code": "callability_or_health_unknown", "reason": "unknown status cannot authorize automatic execution"})
    if any(item.get("admitted") is False for item in candidates):
        issues.append({"code": "observed_candidates_not_admitted", "reason": "observed candidates remain visible but cannot auto-execute"})
    return {
        "schema": SCHEMA,
        "generated_at": _text(context.get("generated_at")) or "",
        "source_signature": _source_signature(candidates, source_signature),
        "authority_status": _text(context.get("authority_status")) or "referenced_owners",
        "index_status": _text(context.get("index_status")) or "unknown",
        "current_turn_evidence": _as_dict(context.get("current_turn_evidence")),
        "decision_mode": mode,
        "primary_owner": primary_owner,
        "candidates": candidates,
        "issues": issues[:6],
        "next_action": "codex_select_or_run_declared_minimal_probe" if mode == "codex_select" else "use_existing_owner_contract",
        "projection_rule": "derived_references_only; authority_and_permissions_remain_with_existing_owners",
        "limits": {"candidate_count": len(candidates), "max_candidates": max(1, min(limit, 24))},
    }


def validate() -> dict[str, Any]:
    view = build_capability_view(
        "识别 OCR 能力",
        skills=[{"name": "ppocrv5", "description": "Route OCR tasks", "path": "/skills/ppocrv5/SKILL.md", "admission_state": "unregistered"}],
        owners=[{"name": "pdf_owner", "owner": "pdf_owner", "validator": "pdf.validate"}],
        tools=[{"name": "gui-ocr", "owner": "gui_owner", "current_turn_callable": True, "healthy": True}],
        primary_owner="pdf_owner",
    )
    checks = [
        {"name": "schema", "ok": view.get("schema") == SCHEMA},
        {"name": "observed_skill_stays_unadmitted", "ok": next(row for row in view["candidates"] if row["capability_id"] == "skill:ppocrv5")["admitted"] is False},
        {"name": "callable_tool_preserved", "ok": next(row for row in view["candidates"] if row["capability_id"] == "tool:gui-ocr")["callable"] is True},
        {"name": "primary_owner_is_explicit", "ok": view["primary_owner"] == "pdf_owner"},
    ]
    return {"schema": f"{SCHEMA}.validate", "ok": all(item["ok"] for item in checks), "checks": checks, "view": view}


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(validate(), ensure_ascii=False, indent=2))
