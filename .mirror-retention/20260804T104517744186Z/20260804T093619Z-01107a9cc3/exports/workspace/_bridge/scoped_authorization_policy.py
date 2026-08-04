#!/usr/bin/env python3
"""Pure risk-and-cost policy decision point for scoped authorization.

Ownership: deterministic risk/cost assessment and gate recommendation.
Non-goals: challenges, grants, permits, user evidence, persistence, business
side effects, owner-specific discovery, or enforcement.
State behavior: pure/read-only; loads one checked-in declarative policy.
Caller context: shadow evaluation and explicitly selected enforcement PEPs.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


POLICY_PATH = Path(__file__).resolve().parent / "policies" / "scoped_authorization_risk_cost_policy.json"
DECISIONS = {
    "allow_without_challenge",
    "bounded_preflight",
    "challenge_required",
    "deny_non_overrideable",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def validate_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    if payload.get("schema") != "scoped_authorization_risk_cost_policy.v1":
        issues.append("schema_invalid")
    if not str(payload.get("policy_version") or "").strip():
        issues.append("policy_version_missing")
    mode = payload.get("mode")
    if mode not in {"shadow_only", "enforce_selected_consumers"}:
        issues.append("policy_mode_invalid")
    selected_consumers = payload.get("selected_consumers")
    if mode == "enforce_selected_consumers" and (
        not isinstance(selected_consumers, list)
        or not selected_consumers
        or any(not str(item).strip() for item in selected_consumers)
    ):
        issues.append("selected_consumers_invalid")
    risk = payload.get("risk") if isinstance(payload.get("risk"), Mapping) else {}
    levels = risk.get("levels") if isinstance(risk.get("levels"), list) else []
    if levels != ["R0", "R1", "R2", "R3", "R4"]:
        issues.append("risk_levels_invalid")
    if risk.get("challenge_at") not in levels:
        issues.append("risk_challenge_level_invalid")
    if risk.get("deny_at") not in levels:
        issues.append("risk_deny_level_invalid")
    matrix = risk.get("severity_likelihood_matrix") if isinstance(risk.get("severity_likelihood_matrix"), Mapping) else {}
    if (
        set(matrix) != {f"S{index}" for index in range(5)}
        or any(not isinstance(row, Mapping) for row in matrix.values())
        or any(
            set(row) != {f"L{index}" for index in range(5)}
            or any(level not in levels for level in row.values())
            for row in matrix.values()
            if isinstance(row, Mapping)
        )
    ):
        issues.append("risk_matrix_invalid")
    hard_triggers = risk.get("hard_trigger_facts") if isinstance(risk.get("hard_trigger_facts"), Mapping) else {}
    if not hard_triggers or any(level not in levels for level in hard_triggers.values()):
        issues.append("risk_hard_triggers_invalid")
    override = payload.get("owner_override") if isinstance(payload.get("owner_override"), Mapping) else {}
    if override.get("rule") != "tighten_only" or override.get("unknown_dimension") != "reject" or override.get("threshold_increase") != "reject":
        issues.append("owner_override_policy_invalid")
    unknown = payload.get("unknown") if isinstance(payload.get("unknown"), Mapping) else {}
    if unknown.get("decision") != "bounded_preflight" or unknown.get("preflight_must_be_read_only") is not True or unknown.get("preflight_must_have_no_business_side_effect") is not True:
        issues.append("unknown_policy_invalid")
    if not isinstance(payload.get("central_forbids"), list) or not all(str(item).strip() for item in payload.get("central_forbids") or []):
        issues.append("central_forbids_invalid")
    dimensions = payload.get("cost_dimensions") if isinstance(payload.get("cost_dimensions"), Mapping) else {}
    if not dimensions:
        issues.append("cost_dimensions_missing")
    for name, contract in dimensions.items():
        if not str(name).strip() or not isinstance(contract, Mapping):
            issues.append(f"cost_dimension_invalid:{name}")
            continue
        threshold = contract.get("high_threshold")
        if not _finite_nonnegative(threshold) or float(threshold) <= 0:
            issues.append(f"cost_threshold_invalid:{name}")
        if not str(contract.get("unit") or "").strip():
            issues.append(f"cost_unit_missing:{name}")
    preflight = payload.get("bounded_preflight") if isinstance(payload.get("bounded_preflight"), Mapping) else {}
    for field in ("max_elapsed_seconds", "max_network_mib", "max_files", "max_records", "max_external_calls", "max_depth"):
        if not _finite_nonnegative(preflight.get(field)) or float(preflight.get(field) or 0) <= 0:
            issues.append(f"preflight_limit_invalid:{field}")
    if preflight.get("business_side_effects") is not False:
        issues.append("preflight_business_side_effects_must_be_false")
    if preflight.get("recursive_unbounded_scan") is not False:
        issues.append("preflight_unbounded_scan_must_be_false")
    ratio = payload.get("near_limit_ratio")
    if not _finite_nonnegative(ratio) or not 0 < float(ratio) < 1:
        issues.append("near_limit_ratio_invalid")
    return {"ok": not issues, "issues": sorted(set(issues)), "policy_signature": _digest(dict(payload))}


def _finite_nonnegative(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and float(value) >= 0


def normalize_assessment(assessment: Mapping[str, Any], *, policy_payload: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    normalized: dict[str, Any] = {}
    for field in ("subject", "action", "resource", "environment"):
        value = assessment.get(field)
        if not isinstance(value, Mapping) or not value:
            issues.append(f"{field}_missing")
        normalized[field] = dict(value) if isinstance(value, Mapping) else {}
    risk = assessment.get("risk") if isinstance(assessment.get("risk"), Mapping) else {}
    risk_level = str(risk.get("level") or "unknown")
    allowed_levels = set((policy_payload.get("risk") or {}).get("levels") or []) | {"unknown"}
    if risk_level not in allowed_levels:
        issues.append("risk_level_invalid")
    severity = str(risk.get("severity") or "")
    likelihood = str(risk.get("likelihood") or "")
    matrix = (policy_payload.get("risk") or {}).get("severity_likelihood_matrix") or {}
    if bool(severity) != bool(likelihood) or severity and (severity not in matrix or likelihood not in matrix.get(severity, {})):
        issues.append("risk_matrix_input_invalid")
    normalized["risk"] = {
        "level": risk_level,
        "severity": severity,
        "likelihood": likelihood,
        "facts": sorted({str(item).strip() for item in risk.get("facts") or [] if str(item).strip()}),
    }
    forbids = assessment.get("matched_forbids")
    if forbids is None:
        forbids = []
    if not isinstance(forbids, list):
        issues.append("matched_forbids_invalid")
        forbids = []
    normalized["matched_forbids"] = sorted({str(item).strip() for item in forbids if str(item).strip()})
    unknown_forbids = set(normalized["matched_forbids"]) - set(policy_payload.get("central_forbids") or [])
    if unknown_forbids:
        issues.extend(f"matched_forbid_unknown:{item}" for item in sorted(unknown_forbids))
    costs = assessment.get("costs") if isinstance(assessment.get("costs"), Mapping) else {}
    if not isinstance(assessment.get("costs"), Mapping):
        issues.append("costs_missing")
    normalized_costs: dict[str, Any] = {}
    for name in (policy_payload.get("cost_dimensions") or {}):
        if name not in costs or costs.get(name) is None:
            normalized_costs[name] = {"known": False, "actual": None, "estimate": None, "upper_bound": None}
            continue
        value = costs.get(name)
        if isinstance(value, Mapping):
            row = {key: value.get(key) for key in ("actual", "estimate", "upper_bound")}
            supplied = [item for item in row.values() if item is not None]
        else:
            row = {"actual": value, "estimate": None, "upper_bound": None}
            supplied = [value]
        if not supplied:
            normalized_costs[name] = {"known": False, **row}
            continue
        if any(not _finite_nonnegative(item) for item in supplied):
            issues.append(f"cost_value_invalid:{name}")
            normalized_costs[name] = {"known": False, **row}
            continue
        normalized_costs[name] = {"known": True, **row}
    unknown_dimensions = sorted(set(str(name) for name in costs) - set((policy_payload.get("cost_dimensions") or {})))
    if unknown_dimensions:
        issues.extend(f"cost_dimension_unknown:{name}" for name in unknown_dimensions)
    normalized["costs"] = normalized_costs
    return {"ok": not issues, "issues": sorted(set(issues)), "assessment": normalized}


def classify_risk(normalized: Mapping[str, Any], *, policy_payload: Mapping[str, Any]) -> dict[str, Any]:
    risk_input = normalized.get("risk") or {}
    level = str(risk_input.get("level") or "unknown")
    levels = list((policy_payload.get("risk") or {}).get("levels") or [])
    candidates = [level] if level in levels else []
    severity = str(risk_input.get("severity") or "")
    likelihood = str(risk_input.get("likelihood") or "")
    matrix = (policy_payload.get("risk") or {}).get("severity_likelihood_matrix") or {}
    if severity in matrix and likelihood in matrix[severity]:
        candidates.append(str(matrix[severity][likelihood]))
    hard_triggers = (policy_payload.get("risk") or {}).get("hard_trigger_facts") or {}
    candidates.extend(str(hard_triggers[fact]) for fact in risk_input.get("facts") or [] if fact in hard_triggers)
    if candidates:
        level = max(candidates, key=levels.index)
    return {
        "level": level,
        "unknown": not candidates,
        "severity": severity,
        "likelihood": likelihood,
        "hard_trigger_facts": sorted(set(risk_input.get("facts") or []) & set(hard_triggers)),
        "challenge_required": level in levels and levels.index(level) >= levels.index(policy_payload["risk"]["challenge_at"]),
        "deny_non_overrideable": level in levels and levels.index(level) >= levels.index(policy_payload["risk"]["deny_at"]),
    }


def validate_owner_override(override: Mapping[str, Any], *, policy_payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    selected = dict(policy_payload if policy_payload is not None else load_policy())
    dimensions = selected.get("cost_dimensions") if isinstance(selected.get("cost_dimensions"), Mapping) else {}
    invalid: list[str] = []
    effective: dict[str, float] = {}
    for name, value in override.items():
        if name not in dimensions or not _finite_nonnegative(value) or float(value) > float(dimensions.get(name, {}).get("high_threshold") or -1):
            invalid.append(str(name))
        else:
            effective[str(name)] = float(value)
    return {"ok": not invalid, "invalid_dimensions": sorted(invalid), "effective_thresholds": effective}


def evaluate_cost(
    normalized: Mapping[str, Any], *, policy_payload: Mapping[str, Any], owner_override: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    override_result = validate_owner_override(owner_override or {}, policy_payload=policy_payload)
    if not override_result["ok"]:
        return {"ok": False, "reason": "owner_override_invalid", **override_result}
    rows: dict[str, Any] = {}
    unknown: list[str] = []
    high: list[str] = []
    near: list[str] = []
    ratio = float(policy_payload.get("near_limit_ratio") or 0.9)
    for name, contract in policy_payload["cost_dimensions"].items():
        source = (normalized.get("costs") or {}).get(name) or {}
        threshold = float(override_result["effective_thresholds"].get(name, contract["high_threshold"]))
        if not source.get("known"):
            unknown.append(name)
            rows[name] = {"known": False, "effective_value": None, "threshold": threshold, "unit": contract["unit"]}
            continue
        values = [float(source[key]) for key in ("actual", "estimate", "upper_bound") if source.get(key) is not None]
        effective = max(values)
        is_high = effective >= threshold
        is_near = not is_high and effective >= threshold * ratio
        if is_high:
            high.append(name)
        if is_near:
            near.append(name)
        rows[name] = {
            "known": True, "effective_value": effective, "threshold": threshold,
            "unit": contract["unit"], "high": is_high, "near_limit": is_near,
        }
    return {"ok": True, "dimensions": rows, "unknown_dimensions": unknown, "high_dimensions": high, "near_limit_dimensions": near}


def build_preflight_budget(*, policy_payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    selected = dict(policy_payload if policy_payload is not None else load_policy())
    budget = dict(selected.get("bounded_preflight") or {})
    budget["read_only"] = True
    return budget


def _decision(
    name: str, *, normalized: Mapping[str, Any], policy_payload: Mapping[str, Any],
    determining_rules: list[str], missing_facts: list[str], risk: Mapping[str, Any],
    cost: Mapping[str, Any], reason: str = "", ok: bool = True,
) -> dict[str, Any]:
    if name not in DECISIONS:
        raise ValueError("authorization_gate_decision_invalid")
    return {
        "schema": "scoped_authorization_policy.decision.v1",
        "ok": ok,
        "mode": str(policy_payload.get("mode") or "shadow_only"),
        "shadow_only": policy_payload.get("mode") == "shadow_only",
        "selected_consumers": sorted({str(item) for item in policy_payload.get("selected_consumers") or [] if str(item).strip()}),
        "decision": name,
        "reason": reason,
        "determining_rules": sorted(set(determining_rules)),
        "missing_facts": sorted(set(missing_facts)),
        "risk_evaluation": dict(risk),
        "cost_evaluation": dict(cost.get("dimensions") or {}),
        "near_limit_dimensions": list(cost.get("near_limit_dimensions") or []),
        "preflight_budget": build_preflight_budget(policy_payload=policy_payload) if name == "bounded_preflight" else {},
        "next_action": {
            "allow_without_challenge": "continue_with_owner_controls",
            "bounded_preflight": "run_one_bounded_read_only_preflight_then_reevaluate",
            "challenge_required": "request_exact_scoped_authorization",
            "deny_non_overrideable": "stop_non_overrideable",
        }[name],
        "policy_version": str(policy_payload.get("policy_version") or ""),
        "policy_signature": _digest(dict(policy_payload)),
        "input_signature": _digest(dict(normalized)),
    }


def decide_gate(
    assessment: Mapping[str, Any], *, policy_payload: Mapping[str, Any] | None = None,
    owner_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    selected = dict(policy_payload if policy_payload is not None else load_policy())
    policy_check = validate_policy(selected)
    if not policy_check["ok"]:
        return _decision(
            "deny_non_overrideable", normalized=dict(assessment), policy_payload=selected,
            determining_rules=["default_deny:policy_invalid"], missing_facts=policy_check["issues"],
            risk={}, cost={}, reason="policy_invalid", ok=False,
        )
    normalized_result = normalize_assessment(assessment, policy_payload=selected)
    normalized = normalized_result["assessment"]
    if not normalized_result["ok"]:
        return _decision(
            "deny_non_overrideable", normalized=normalized, policy_payload=selected,
            determining_rules=["default_deny:assessment_invalid"], missing_facts=normalized_result["issues"],
            risk={}, cost={}, reason="assessment_invalid", ok=False,
        )
    risk = classify_risk(normalized, policy_payload=selected)
    cost = evaluate_cost(normalized, policy_payload=selected, owner_override=owner_override)
    if not cost.get("ok"):
        return _decision(
            "deny_non_overrideable", normalized=normalized, policy_payload=selected,
            determining_rules=["default_deny:owner_override_invalid"], missing_facts=cost.get("invalid_dimensions", []),
            risk=risk, cost={}, reason="owner_override_invalid", ok=False,
        )
    central_forbids = set(str(item) for item in selected.get("central_forbids") or [])
    matched_forbids = sorted(set(normalized.get("matched_forbids") or []) & central_forbids)
    if matched_forbids or risk["deny_non_overrideable"]:
        rules = [f"forbid:{item}" for item in matched_forbids]
        if risk["deny_non_overrideable"]:
            rules.append(f"risk:{risk['level']}")
        return _decision("deny_non_overrideable", normalized=normalized, policy_payload=selected, determining_rules=rules, missing_facts=[], risk=risk, cost=cost)
    high_costs = list(cost.get("high_dimensions") or [])
    if risk["challenge_required"] or high_costs:
        rules = [f"cost:{name}" for name in high_costs]
        if risk["challenge_required"]:
            rules.append(f"risk:{risk['level']}")
        return _decision("challenge_required", normalized=normalized, policy_payload=selected, determining_rules=rules, missing_facts=[], risk=risk, cost=cost)
    unknown = [*(cost.get("unknown_dimensions") or [])]
    if risk["unknown"]:
        unknown.append("risk.level")
    if unknown:
        return _decision("bounded_preflight", normalized=normalized, policy_payload=selected, determining_rules=["unknown:bounded_preflight"], missing_facts=unknown, risk=risk, cost=cost)
    return _decision("allow_without_challenge", normalized=normalized, policy_payload=selected, determining_rules=["risk_below_R3_and_all_costs_below_threshold"], missing_facts=[], risk=risk, cost=cost)


def degraded_decision(action: str, *, risk_level: str, policy_payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    selected = dict(policy_payload if policy_payload is not None else load_policy())
    allowed = str(action) in set(selected.get("degraded_allowlist") or []) and risk_level in {"R0", "R1"}
    return {
        "schema": "scoped_authorization_policy.degraded_decision.v1",
        "ok": allowed,
        "decision": "allow_without_challenge" if allowed else "deny_non_overrideable",
        "reason": "degraded_read_only_allowlist" if allowed else "degraded_default_deny",
        "shadow_only": selected.get("mode") == "shadow_only",
    }


def consumer_enforcement(decision: Mapping[str, Any], *, consumer: str) -> dict[str, Any]:
    """Select enforcement without allowing callers to reinterpret the PDP result."""

    mode = str(decision.get("mode") or "shadow_only")
    selected = {str(item) for item in decision.get("selected_consumers") or []}
    enforced = bool(decision.get("ok") and mode == "enforce_selected_consumers" and consumer in selected)
    return {
        "schema": "scoped_authorization_policy.consumer_enforcement.v1",
        "ok": bool(decision.get("ok")),
        "consumer": consumer,
        "mode": mode,
        "enforced": enforced,
        "selected_enforcement": str(decision.get("decision") or "deny_non_overrideable") if enforced else "legacy_fail_closed",
        "decision_ref": (
            f"scoped-authorization-policy:{decision.get('policy_signature')}:{decision.get('input_signature')}"
            if decision.get("policy_signature") and decision.get("input_signature")
            else ""
        ),
        "legacy_gate_retained": not enforced,
    }
