#!/usr/bin/env python3
"""Canonical classification for advisory, authorizable, and hard decisions.

Ownership: pure decision-class normalization, precedence, stable signatures,
and compact projections shared by workflow and business owners.
Non-goals: issue permits, inspect live authorization state, execute effects, or
infer authority from severity, risk, exit codes, or natural-language reasons.
State behavior: pure and deterministic; performs no I/O.
Caller context: owners supply an explicit class, authority reference, current
condition, user-goal evidence, and scoped-authorization evidence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


SCHEMA = "decision_authority.v1"
DECISION_CLASSES = {"recommendation", "overrideable_policy", "hard_gate"}


def _signature(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def classify_decision(
    *,
    decision_class: str,
    authority_ref: str,
    condition_satisfied: bool,
    user_goal_explicit: bool = False,
    authorization_valid: bool = False,
    required_evidence: Iterable[str] = (),
    default_action: str = "",
    requested_action: str = "",
    policy_signature: str = "",
    input_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one explicit owner decision without guessing its authority."""

    normalized_class = str(decision_class or "").strip().lower()
    authority = str(authority_ref or "").strip()
    evidence = list(dict.fromkeys(str(item).strip() for item in required_evidence if str(item).strip()))
    inputs = {
        "decision_class": normalized_class,
        "authority_ref": authority,
        "condition_satisfied": bool(condition_satisfied),
        "user_goal_explicit": bool(user_goal_explicit),
        "authorization_valid": bool(authorization_valid),
        "required_evidence": evidence,
        "default_action": str(default_action or ""),
        "requested_action": str(requested_action or ""),
        "context": input_context or {},
    }
    input_signature = _signature(inputs)
    effective_policy_signature = str(policy_signature or "").strip() or _signature(
        {"decision_class": normalized_class, "authority_ref": authority, "required_evidence": evidence}
    )

    if normalized_class not in DECISION_CLASSES or not authority:
        return {
            "schema": SCHEMA,
            "ok": False,
            "reason": "explicit_decision_class_and_authority_ref_required",
            "decision_class": normalized_class,
            "authority_ref": authority,
            "blocking": True,
            "effective_outcome": "deny",
            "override_authority": "none",
            "input_signature": input_signature,
            "policy_signature": effective_policy_signature,
            "next_action": "resolve_decision_authority_contract",
        }

    consumed = False
    if normalized_class == "hard_gate":
        enforcement = "non_overrideable"
        blocking = not condition_satisfied
        outcome = "allow" if condition_satisfied else "deny"
        override_authority = "none"
        next_action = "continue" if condition_satisfied else "satisfy_hard_gate"
    elif normalized_class == "overrideable_policy":
        enforcement = "requires_scoped_authorization"
        consumed = bool(not condition_satisfied and authorization_valid)
        blocking = bool(not condition_satisfied and not authorization_valid)
        outcome = "allow" if condition_satisfied or authorization_valid else "needs_authorization"
        override_authority = "scoped_authorization"
        next_action = "continue" if not blocking else "obtain_matching_scoped_authorization"
    else:
        enforcement = "non_blocking"
        consumed = bool(not condition_satisfied and user_goal_explicit)
        blocking = False
        outcome = "allow" if condition_satisfied or user_goal_explicit else "recommend"
        override_authority = "explicit_user_goal"
        next_action = (
            "continue_with_explicit_user_goal"
            if consumed
            else str(default_action or "continue_with_recommendation")
        )

    payload = {
        "schema": SCHEMA,
        "ok": True,
        "decision_class": normalized_class,
        "enforcement": enforcement,
        "blocking": blocking,
        "effective_outcome": outcome,
        "authority_ref": authority,
        "override_authority": override_authority,
        "required_evidence": evidence,
        "input_signature": input_signature,
        "policy_signature": effective_policy_signature,
        "default_action": str(default_action or ""),
        "requested_action": str(requested_action or ""),
        "override_consumed": consumed,
        "user_override_consumed": consumed and normalized_class == "recommendation",
        "authorization_consumed": consumed and normalized_class == "overrideable_policy",
        "next_action": next_action,
    }
    payload["decision_id"] = _signature(
        {key: payload[key] for key in ("decision_class", "authority_ref", "input_signature", "policy_signature")}
    )[:24]
    return payload


def project_decisions(decisions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Project canonical decisions into workflow-facing buckets."""

    rows = [dict(item) for item in decisions if isinstance(item, dict)]
    recommendations = [item for item in rows if item.get("decision_class") == "recommendation"]
    policies = [item for item in rows if item.get("decision_class") == "overrideable_policy"]
    hard_gates = [item for item in rows if item.get("decision_class") == "hard_gate"]
    consumed = [item for item in rows if item.get("override_consumed")]
    unresolved = [item for item in rows if item.get("blocking")]
    return {
        "schema": "decision_authority.projection.v1",
        "ok": all(item.get("ok") is True for item in rows),
        "recommendations": recommendations,
        "authorization_requirements": policies,
        "hard_gates": hard_gates,
        "consumed_overrides": consumed,
        "blocking": bool(unresolved),
        "unresolved": unresolved,
        "signature": _signature(rows),
        "rule": "only explicit canonical blocking=true decisions may block; recommendations never become required gates",
    }


def validate() -> dict[str, Any]:
    recommendation = classify_decision(
        decision_class="recommendation",
        authority_ref="validate.recommendation",
        condition_satisfied=False,
        user_goal_explicit=True,
    )
    hard_gate = classify_decision(
        decision_class="hard_gate",
        authority_ref="validate.hard_gate",
        condition_satisfied=False,
        user_goal_explicit=True,
        authorization_valid=True,
    )
    projection = project_decisions([recommendation, hard_gate])
    return {
        "schema": "decision_authority.validate.v1",
        "ok": bool(
            recommendation.get("user_override_consumed")
            and not recommendation.get("blocking")
            and hard_gate.get("blocking")
            and hard_gate.get("effective_outcome") == "deny"
            and projection.get("blocking")
        ),
        "projection": projection,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
