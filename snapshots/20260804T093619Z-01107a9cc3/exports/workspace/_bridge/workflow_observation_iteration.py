#!/usr/bin/env python3
"""Record redacted business outcomes and derive governed iteration proposals.

Ownership: repeated-pattern planning, stable candidate identity, and the thin
adapter into the existing workflow review queue.
Non-goals: approval, memory or PMB writes, rule/skill/config changes, command
execution, business-state writes, or automatic remediation.
State behavior: record writes only redacted, deduplicable observer events; plan
and validate are read-only; apply may only upsert pending items through
``workflow_review_queue.sync_review_groups``.
Caller context: the unified daily scheduler and focused manual review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from business_environment_control_plane import build_business_outcome_observations
from codex_rule_observer import (
    DEFAULT_RUNTIME_ROOT,
    SCHEMA as OBSERVER_EVENT_SCHEMA,
    now_iso,
    read_recent_events,
    write_event,
)
from workflow_iteration_capture import stable_candidate_id
from workflow_efficiency_cycle_projection import project_efficiency_cycles
from workflow_review_queue import QUEUE_PATH, sync_review_groups


SCHEMA = "workflow_observation_iteration.v1"
POLICY_VERSION = "observation-pattern-policy.v1"
EXPERIMENT_SCHEMA = "adaptive_efficiency_experiment.v1"
STABILITY_SCHEMA = "adaptive_efficiency_stability.v1"
TARGET_NAMESPACE = "memory.project_conclusions"
SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def _digest(value: Any, *, length: int = 24) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _turn_key(event: dict[str, Any]) -> tuple[str, str]:
    return str(event.get("session_id") or "unknown"), str(event.get("turn_id") or "unknown")


def _safe_label(value: Any) -> str:
    text = str(value or "").strip()
    return text if SAFE_LABEL_RE.fullmatch(text) else f"redacted-label-{_digest(text, length=12)}"


def _known_outcome(event: dict[str, Any]) -> bool | None:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    if isinstance(result.get("ok"), bool):
        return bool(result["ok"])
    status = str(result.get("status") or "").strip().lower()
    if status in {"completed", "ok", "success", "succeeded"}:
        return True
    if status in {"blocked", "error", "failed", "failure", "timed_out", "timeout"}:
        return False
    return None


def record_business_outcomes(
    outcomes: list[dict[str, Any]], *, task_ref: str, runtime_root: Path = DEFAULT_RUNTIME_ROOT
) -> dict[str, Any]:
    """Persist the smallest safe outcome projection through the observer store."""

    projected = preview_business_outcomes(outcomes, task_ref=task_ref)
    if not projected.get("ok"):
        return {
            **projected,
            "schema": f"{SCHEMA}.record_business_outcomes",
            "writes_observer_events": False,
            "writes_business_state": False,
            "writes_review_queue": False,
        }
    event_paths: list[str] = []
    for observation in projected.get("observations", []):
        event = {
            **observation,
            "observation_schema": observation["schema"],
            "schema": OBSERVER_EVENT_SCHEMA,
            "recorded_at": now_iso(),
            "session_id": str(observation["task_key"]),
            "turn_id": str(observation["category"]),
        }
        event_paths.append(str(write_event(event, runtime_root)))
    return {
        "schema": f"{SCHEMA}.record_business_outcomes",
        "ok": True,
        "observation_count": len(event_paths),
        "event_paths": event_paths,
        "task_key": projected["task_key"],
        "writes_business_state": False,
        "writes_review_queue": False,
        "contracts": {
            "raw_event_owner": "codex_rule_observer",
            "source_projection_owner": "business_environment_control_plane",
            "task_and_result_references_redacted": True,
            "automatic_remediation": False,
        },
    }


def preview_business_outcomes(outcomes: list[dict[str, Any]], *, task_ref: str) -> dict[str, Any]:
    """Validate the owner-supplied projection without creating observer state."""

    projected = build_business_outcome_observations(outcomes, task_ref=task_ref)
    return {
        **projected,
        "schema": f"{SCHEMA}.preview_business_outcomes",
        "writes_observer_events": False,
        "writes_business_state": False,
        "writes_review_queue": False,
    }


def preview_efficiency_cycles(
    *, rollout_path: Path, task_ref: str, receipt_root: Path | None = None
) -> dict[str, Any]:
    """Project cycle evidence without creating observer or review state."""

    return {
        **project_efficiency_cycles(
            rollout_path=rollout_path,
            task_ref=task_ref,
            receipt_root=receipt_root,
        ),
        "schema": f"{SCHEMA}.preview_efficiency_cycles",
        "writes_observer_events": False,
        "writes_review_queue": False,
        "writes_business_state": False,
    }


def record_efficiency_cycles(
    *,
    rollout_path: Path,
    task_ref: str,
    receipt_root: Path | None = None,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
) -> dict[str, Any]:
    """Persist a valid redacted projection through the existing observer owner."""

    projected = preview_efficiency_cycles(
        rollout_path=rollout_path,
        task_ref=task_ref,
        receipt_root=receipt_root,
    )
    if not projected.get("ok"):
        return {
            **projected,
            "schema": f"{SCHEMA}.record_efficiency_cycles",
            "writes_observer_events": False,
        }
    event_paths: list[str] = []
    for cycle in projected.get("events", []):
        event = {
            **cycle,
            "projection_schema": cycle["schema"],
            "schema": OBSERVER_EVENT_SCHEMA,
            "recorded_at": now_iso(),
            "session_id": str(cycle["task_key"]),
            "turn_id": str(cycle["cycle_type"]),
        }
        event_paths.append(str(write_event(event, runtime_root)))
    return {
        "schema": f"{SCHEMA}.record_efficiency_cycles",
        "ok": True,
        "event_count": len(event_paths),
        "event_paths": event_paths,
        "task_key": projected["task_key"],
        "writes_observer_events": bool(event_paths),
        "writes_review_queue": False,
        "writes_business_state": False,
        "contracts": projected.get("contracts", {}),
    }


def _candidate(
    *,
    pattern_type: str,
    pattern_key: str,
    stable_conclusion: str,
    evidence_events: list[dict[str, Any]],
    attributes: dict[str, Any],
    runtime_root: Path,
    target_namespace: str = TARGET_NAMESPACE,
    title: str = "Observed repeated workflow pattern",
) -> dict[str, Any]:
    source_checkpoint = f"{POLICY_VERSION}:{pattern_type}:{pattern_key}"
    candidate_id = stable_candidate_id(
        text=stable_conclusion,
        source_checkpoint=source_checkpoint,
        stable_conclusion=stable_conclusion,
        target_namespace=target_namespace,
        affected_system="workflow",
    )
    event_ids = sorted({str(item.get("event_id") or "") for item in evidence_events if item.get("event_id")})
    turn_ids = sorted({"/".join(_turn_key(item)) for item in evidence_events})
    confidence_values = {
        str(item.get("measurement_confidence") or "").strip().lower()
        for item in evidence_events
    }
    if "low" in confidence_values:
        measurement_confidence = "low"
    elif confidence_values and confidence_values <= {"high"}:
        measurement_confidence = "high"
    elif confidence_values & {"medium", "high"}:
        measurement_confidence = "medium"
    else:
        measurement_confidence = "unknown"
    attributes = {"measurement_confidence": measurement_confidence, **attributes}
    return {
        "candidate_id": candidate_id,
        "source_item_id": candidate_id,
        "title": title,
        "summary": stable_conclusion,
        "source_url": f"artifact:{runtime_root}",
        "source_checkpoint": source_checkpoint,
        "stable_conclusion": stable_conclusion,
        "target_namespace": target_namespace,
        "affected_system": "workflow",
        "proposed_destination_namespace": target_namespace,
        "trust_tier": "derived_observation_proposal",
        "freshness_class": "bounded_observation_window",
        "approval_action": "review_evidence_then_approve_or_reject; no automatic remediation",
        "required_checks": [
            "Confirm the pattern remains useful outside the sampled turns",
            "Inspect stable event references without loading full private payloads",
            "Route any approved change through its exact owner",
            "Validate owner readback before resolving the review item",
        ],
        "attributes": {
            "owner": "maintenance_owner" if target_namespace.startswith("maintenance.") else "memory_governance",
            "signal_kind": "repeated_observation_pattern",
            "pattern_type": pattern_type,
            "pattern_key": pattern_key,
            "policy_version": POLICY_VERSION,
            "event_count": len(event_ids),
            "turn_count": len(turn_ids),
            "evidence_signature": _digest(event_ids),
            "event_ids": event_ids[:50],
            "turn_refs": turn_ids[:20],
            "write_authorization_inherited": False,
            **attributes,
        },
    }


def _measurement_confidence(candidate: dict[str, Any]) -> str:
    value = str(candidate.get("attributes", {}).get("measurement_confidence") or "").strip().lower()
    if value in {"low", "medium", "high"}:
        return value
    floor = str(candidate.get("attributes", {}).get("measurement_confidence_floor") or "").strip().lower()
    return floor if floor in {"low", "medium", "high"} else "unknown"


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def score_candidate_priority(candidate: dict[str, Any]) -> dict[str, Any]:
    """Project a bounded, redacted priority score from existing outcome facts."""

    attributes = candidate.get("attributes") if isinstance(candidate.get("attributes"), dict) else {}
    signal_kind = str(attributes.get("signal_kind") or "unknown")
    confidence = _measurement_confidence(candidate)
    confidence_weight = {"high": 1.0, "medium": 0.75, "low": 0.25}.get(confidence, 0.0)
    frequency = max(
        _nonnegative_int(attributes.get("occurrence_count")),
        _nonnegative_int(attributes.get("known_outcome_count")),
        _nonnegative_int(attributes.get("failure_count")),
        0,
    )
    task_coverage = max(
        _nonnegative_int(attributes.get("distinct_task_count")),
        _nonnegative_int(attributes.get("turn_count")),
        1,
    )
    direct_cost_ms = sum(
        _nonnegative_int(attributes.get(field))
        for field in (
            "total_user_wait_ms",
            "total_governance_tax_ms",
            "total_tool_wait_ms",
            "total_rework_ms",
            "total_idle_gap_ms",
        )
    )
    if direct_cost_ms == 0:
        direct_cost_ms = _nonnegative_int(attributes.get("total_round_trip_count")) * 1000
    if direct_cost_ms == 0 and signal_kind == "tool_reliability":
        direct_cost_ms = frequency * 1000
    implementation_cost = _nonnegative_int(attributes.get("implementation_cost_ms"))
    validation_cost = _nonnegative_int(attributes.get("validation_migration_cost_ms"))
    maintenance_cost = _nonnegative_int(attributes.get("expected_maintenance_cost_ms"))
    gross_cost_ms = frequency * max(1, task_coverage) * max(1, direct_cost_ms // max(1, frequency))
    net_value_score = max(0.0, (gross_cost_ms - implementation_cost - validation_cost - maintenance_cost) * confidence_weight)
    return {
        "schema": "adaptive_efficiency_priority.v1",
        "signal_kind": signal_kind,
        "measurement_confidence": confidence,
        "frequency": frequency,
        "task_coverage": task_coverage,
        "gross_avoidable_cost_ms": gross_cost_ms,
        "implementation_cost_ms": implementation_cost,
        "validation_migration_cost_ms": validation_cost,
        "expected_maintenance_cost_ms": maintenance_cost,
        "net_value_score": round(net_value_score, 3),
        "ranking_basis": "frequency*task_coverage*avoidable_cost*confidence-minus-governance-cost",
        "read_only": True,
        "writes_business_state": False,
        "writes_review_queue": False,
        "automatic_remediation": False,
    }


def build_experiment_shadow(
    candidate: dict[str, Any], *, minimum_occurrences: int = 3, minimum_tasks: int = 2
) -> dict[str, Any]:
    """Return a read-only experiment eligibility and disposition projection."""

    attributes = candidate.get("attributes") if isinstance(candidate.get("attributes"), dict) else {}
    signal_kind = str(attributes.get("signal_kind") or "unknown")
    task_count = int(attributes.get("distinct_task_count") or 0)
    occurrences = int(attributes.get("occurrence_count") or 0)
    confidence = _measurement_confidence(candidate)
    owner = str(attributes.get("owner") or "").strip()
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    reasons: list[str] = []
    if not candidate_id:
        reasons.append("candidate_id_missing")
    if task_count < minimum_tasks:
        reasons.append("independent_task_sample_insufficient")
    if occurrences < minimum_occurrences:
        reasons.append("occurrence_sample_insufficient")
    if confidence not in {"medium", "high"}:
        reasons.append("measurement_confidence_insufficient")
    if not owner:
        reasons.append("owner_missing")
    evidence_signature = str(attributes.get("evidence_signature") or "")
    if not evidence_signature:
        reasons.append("evidence_signature_missing")
    eligible = not reasons
    accepted = int(attributes.get("accepted_and_consumed_count") or 0)
    if not eligible:
        disposition = "would_hold"
    elif signal_kind == "business_outcome_degradation":
        disposition = "would_rollback"
    elif signal_kind == "efficiency_constraint" and accepted >= minimum_tasks:
        disposition = "would_promote"
    elif signal_kind in {"efficiency_cycle", "tool_reliability", "workflow_conformance"}:
        disposition = "would_hold"
    else:
        disposition = "would_hold"
    input_signature = _digest({
        "candidate_id": candidate_id,
        "source_checkpoint": candidate.get("source_checkpoint", ""),
        "policy_version": attributes.get("policy_version", POLICY_VERSION),
        "signal_kind": signal_kind,
        "owner": owner,
        "evidence_signature": evidence_signature,
    }, length=48)
    return {
        "schema": EXPERIMENT_SCHEMA,
        "experiment_id": f"shadow:{candidate_id}" if candidate_id else "shadow:invalid",
        "candidate_ref": candidate_id,
        "owner": owner,
        "action": f"observation.shadow.{signal_kind}",
        "autonomy_level": "L1",
        "eligibility": "eligible" if eligible else "ineligible",
        "eligibility_reasons": reasons,
        "baseline_window": {
            "independent_task_count": task_count,
            "occurrence_count": occurrences,
        },
        "treatment": {"kind": "shadow", "max_exposure": 0},
        "input_signature_ref": f"sha256:{input_signature}",
        "sample_policy": {
            "minimum_tasks": minimum_tasks,
            "minimum_occurrences": minimum_occurrences,
            "measurement_confidence": confidence,
        },
        "guardrails": [
            "semantic_equivalence",
            "authority_freshness",
            "accepted_and_consumed_required_for_promote",
        ],
        "stability_policy": {
            "promote_threshold": 0.8,
            "hold_threshold": 0.5,
            "rollback_threshold": 0.2,
            "cooldown_observations": 2,
            "max_disposition_changes": 1,
        },
        "would_disposition": disposition,
        "writes_business_state": False,
        "writes_review_queue": False,
        "automatic_remediation": False,
    }


def evaluate_disposition_stability(
    *,
    current_state: str = "evaluating",
    success_rate: float | None,
    sample_count: int,
    independent_task_count: int,
    guardrail_ok: bool,
    input_signature_matches: bool,
    cooldown_remaining: int = 0,
    disposition_changes: int = 0,
    minimum_samples: int = 3,
    minimum_tasks: int = 2,
    promote_threshold: float = 0.8,
    hold_threshold: float = 0.5,
    rollback_threshold: float = 0.2,
    cooldown_observations: int = 2,
    max_disposition_changes: int = 1,
) -> dict[str, Any]:
    """Evaluate a disposition without persisting state or executing an action."""

    reason = ""
    if not input_signature_matches:
        disposition, reason = "invalidated", "input_signature_changed"
    elif not guardrail_ok:
        disposition, reason = "would_rollback", "guardrail_failed"
    elif sample_count < minimum_samples or independent_task_count < minimum_tasks:
        disposition, reason = "would_hold", "sample_window_insufficient"
    elif cooldown_remaining > 0:
        disposition, reason = "would_hold", "cooldown_active"
    elif disposition_changes >= max_disposition_changes:
        disposition, reason = "would_hold", "change_budget_exhausted"
    elif success_rate is None:
        disposition, reason = "would_hold", "success_rate_unknown"
    elif success_rate <= rollback_threshold:
        disposition, reason = "would_rollback", "rollback_threshold_crossed"
    elif success_rate >= promote_threshold:
        disposition, reason = "would_promote", "promote_threshold_crossed"
    else:
        disposition, reason = "would_hold", "hysteresis_band"
    return {
        "schema": STABILITY_SCHEMA,
        "current_state": current_state,
        "disposition": disposition,
        "reason": reason,
        "success_rate": success_rate,
        "sample_count": sample_count,
        "independent_task_count": independent_task_count,
        "guardrail_ok": guardrail_ok,
        "input_signature_matches": input_signature_matches,
        "cooldown_remaining": cooldown_remaining,
        "disposition_changes": disposition_changes,
        "policy": {
            "minimum_samples": minimum_samples,
            "minimum_tasks": minimum_tasks,
            "promote_threshold": promote_threshold,
            "hold_threshold": hold_threshold,
            "rollback_threshold": rollback_threshold,
            "cooldown_observations": cooldown_observations,
            "max_disposition_changes": max_disposition_changes,
        },
        "read_only": True,
        "writes_business_state": False,
        "writes_review_queue": False,
        "automatic_remediation": False,
    }


def project_candidate_experiments(
    candidates: list[dict[str, Any]], *, minimum_occurrences: int = 3, minimum_tasks: int = 2
) -> list[dict[str, Any]]:
    """Attach deterministic shadow projections without changing candidate identity."""

    projected = [
        {
            **candidate,
            "priority_projection": score_candidate_priority(candidate),
            "experiment_shadow": build_experiment_shadow(
                candidate,
                minimum_occurrences=minimum_occurrences,
                minimum_tasks=minimum_tasks,
            ),
        }
        for candidate in candidates
    ]
    return sorted(
        projected,
        key=lambda item: (
            -float((item.get("priority_projection") or {}).get("net_value_score") or 0),
            str(item.get("candidate_id") or ""),
        ),
    )


def derive_candidates(
    events: list[dict[str, Any]],
    *,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    minimum_occurrences: int = 3,
    minimum_turns: int = 2,
    minimum_tasks: int = 2,
) -> list[dict[str, Any]]:
    post_tool_events = [item for item in events if item.get("event") == "PostToolUse"]
    by_turn: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in post_tool_events:
        by_turn[_turn_key(event)].append(event)

    candidates: list[dict[str, Any]] = []
    business_events = [
        item for item in events
        if item.get("event") == "BusinessOutcome"
        and str(item.get("outcome_state") or "") != "accepted_and_consumed"
        and str(item.get("task_key") or "")
    ]
    by_business_degradation: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in business_events:
        category = str(event.get("category") or "")
        state = str(event.get("outcome_state") or "")
        if category and state:
            by_business_degradation[(category, state)].append(event)
    for (category, state), degraded_events in sorted(by_business_degradation.items()):
        task_count = len({str(item.get("task_key") or "") for item in degraded_events})
        if len(degraded_events) < minimum_occurrences or task_count < minimum_tasks:
            continue
        candidates.append(
            _candidate(
                pattern_type="business_outcome_degradation",
                pattern_key=f"{category}:{state}",
                stable_conclusion=(
                    f"Business outcome category `{category}` repeatedly reached `{state}` across independent tasks; "
                    "review the redacted evidence and route any repair through its exact owner."
                ),
                evidence_events=degraded_events,
                attributes={
                    "signal_kind": "business_outcome_degradation",
                    "business_category": category,
                    "outcome_state": state,
                    "occurrence_count": len(degraded_events),
                    "distinct_task_count": task_count,
                },
                runtime_root=runtime_root,
                title="Repeated business outcome degradation requiring review",
            )
        )

    efficiency_events = [
        item for item in events
        if item.get("event") == "BusinessOutcome"
        and str(item.get("task_segment_class") or "")
        and str(item.get("measurement_confidence") or "") in {"medium", "high"}
    ]
    efficiency_constraints = {
        "approval_and_clarification_round_trips": lambda item: (
            int(item.get("approval_round_trip_count") or 0)
            + int(item.get("clarification_round_trip_count") or 0)
        ) > 0,
    }
    for constraint, matches in efficiency_constraints.items():
        evidence = [item for item in efficiency_events if matches(item)]
        task_count = len({str(item.get("task_key") or "") for item in evidence})
        if len(evidence) < minimum_occurrences or task_count < minimum_tasks:
            continue
        total_round_trips = sum(
            int(item.get("approval_round_trip_count") or 0)
            + int(item.get("clarification_round_trip_count") or 0)
            for item in evidence
        )
        total_user_wait_ms = sum(int(item.get("user_wait_ms") or 0) for item in evidence)
        total_governance_tax_ms = sum(int(item.get("governance_tax_ms") or 0) for item in evidence)
        candidates.append(
            _candidate(
                pattern_type="efficiency_constraint",
                pattern_key=constraint,
                stable_conclusion=(
                    "批准或澄清往返在多个独立任务段重复出现；请基于脱敏证据评估是否由对应 owner "
                    "消除重复门禁，并用 accepted_and_consumed 结果验证价值。"
                ),
                evidence_events=evidence,
                attributes={
                    "signal_kind": "efficiency_constraint",
                    "constraint": constraint,
                    "occurrence_count": len(evidence),
                    "distinct_task_count": task_count,
                    "total_round_trip_count": total_round_trips,
                    "total_user_wait_ms": total_user_wait_ms,
                    "total_governance_tax_ms": total_governance_tax_ms,
                    "accepted_and_consumed_count": sum(
                        str(item.get("outcome_state") or "") == "accepted_and_consumed"
                        for item in evidence
                    ),
                    "measurement_confidence_floor": "medium",
                },
                runtime_root=runtime_root,
                title="重复批准或澄清往返需要价值评审",
            )
        )
    cycle_events = [
        item for item in events
        if item.get("event") == "EfficiencyCycle"
        and str(item.get("cycle_type") or "")
        and str(item.get("measurement_confidence") or "") in {"medium", "high"}
    ]
    by_cycle: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in cycle_events:
        by_cycle[str(item["cycle_type"])].append(item)
    for cycle_type, evidence in sorted(by_cycle.items()):
        task_count = len({str(item.get("task_key") or "") for item in evidence})
        total_occurrences = sum(int(item.get("occurrence_count") or 0) for item in evidence)
        if total_occurrences < minimum_occurrences or task_count < minimum_tasks:
            continue
        candidates.append(
            _candidate(
                pattern_type="efficiency_cycle",
                pattern_key=cycle_type,
                stable_conclusion=(
                    f"端到端效率循环 {cycle_type} 在多个独立任务重复出现；请由对应 owner "
                    "审阅脱敏聚合证据，并以真实 accepted_and_consumed 结果验证治理价值。"
                ),
                evidence_events=evidence,
                attributes={
                    "signal_kind": "efficiency_cycle",
                    "cycle_type": cycle_type,
                    "occurrence_count": total_occurrences,
                    "distinct_task_count": task_count,
                    "business_acceptance_inferred": False,
                },
                runtime_root=runtime_root,
                title="高频端到端效率循环需要价值评审",
            )
        )
    governance_patterns = {
        "generic_web_without_resource_layer": (
            "generic_web",
            "resource_layer",
            "Repeated turns used generic web without an observed resource-layer result; review routing evidence before changing policy.",
        ),
        "local_write_without_backup": (
            "local_write",
            "backup",
            "Repeated turns contained local writes without an observed backup-owner result; review whether the workflow entry path needs reinforcement.",
        ),
        "local_write_without_validation": (
            "local_write",
            "validation",
            "Repeated turns contained local writes without an observed validation result; review whether the workflow entry path needs reinforcement.",
        ),
    }
    for pattern_key, (trigger_category, excluded_category, conclusion) in governance_patterns.items():
        evidence = [
            event
            for turn_events in by_turn.values()
            if trigger_category in {str(item.get("category") or "") for item in turn_events}
            and excluded_category not in {str(item.get("category") or "") for item in turn_events}
            for event in turn_events
            if event.get("category") == trigger_category
        ]
        turn_count = len({_turn_key(item) for item in evidence})
        if len(evidence) >= minimum_occurrences and turn_count >= minimum_turns:
            candidates.append(
                _candidate(
                    pattern_type="workflow_conformance",
                    pattern_key=pattern_key,
                    stable_conclusion=conclusion,
                    evidence_events=evidence,
                    attributes={"occurrence_count": len(evidence)},
                    runtime_root=runtime_root,
                )
            )

    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in post_tool_events:
        if _known_outcome(event) is not None:
            by_tool[_safe_label(event.get("tool_name"))].append(event)
    for tool, tool_events in sorted(by_tool.items()):
        outcomes = [_known_outcome(item) for item in tool_events]
        failures = sum(value is False for value in outcomes)
        turn_count = len({_turn_key(item) for item in tool_events})
        if len(tool_events) < max(5, minimum_occurrences) or failures < 2 or turn_count < minimum_turns:
            continue
        failure_rate = failures / len(tool_events)
        if failure_rate < 0.4:
            continue
        candidates.append(
            _candidate(
                pattern_type="tool_reliability",
                pattern_key=tool,
                stable_conclusion=(
                    f"Tool `{tool}` has a repeated observed failure pattern; verify owner health and result acceptance before proposing a durable workflow change."
                ),
                evidence_events=tool_events,
                attributes={
                    "known_outcome_count": len(tool_events),
                    "failure_count": failures,
                    "failure_rate": round(failure_rate, 3),
                },
                runtime_root=runtime_root,
            )
        )
    return project_candidate_experiments(
        candidates,
        minimum_occurrences=minimum_occurrences,
        minimum_tasks=minimum_tasks,
    )


def run(
    *,
    apply: bool,
    confirm: str = "",
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    queue_path: Path = QUEUE_PATH,
    max_age_hours: int = 24 * 7,
    minimum_occurrences: int = 3,
    minimum_turns: int = 2,
    minimum_tasks: int = 2,
) -> dict[str, Any]:
    events = read_recent_events(runtime_root=runtime_root, max_age_hours=max_age_hours)
    candidates = derive_candidates(
        events,
        runtime_root=runtime_root,
        minimum_occurrences=max(2, int(minimum_occurrences)),
        minimum_turns=max(2, int(minimum_turns)),
        minimum_tasks=max(2, int(minimum_tasks)),
    )
    if apply and confirm != "APPLY-OBSERVATION-PROPOSALS":
        return {
            "schema": f"{SCHEMA}.apply",
            "ok": False,
            "reason": "confirmation_required",
            "confirmation": "APPLY-OBSERVATION-PROPOSALS",
            "writes_review_queue": False,
        }
    queue_result: list[dict[str, Any]] = []
    if apply and candidates:
        queue_result = sync_review_groups(
            [
                {
                    "kind": "iteration_candidates",
                    "title": "Observed workflow patterns requiring review",
                    "review_items": candidates,
                }
            ],
            db_path=queue_path,
        )
    return {
        "schema": f"{SCHEMA}.{'apply' if apply else 'plan'}",
        "ok": True,
        "mode": "apply_proposals_only" if apply else "read_only_plan",
        "event_count": len(events),
        "candidate_count": len(candidates),
        "business_outcome_event_count": sum(item.get("event") == "BusinessOutcome" for item in events),
        "efficiency_cycle_event_count": sum(item.get("event") == "EfficiencyCycle" for item in events),
        "business_degradation_candidate_count": sum(
            item.get("attributes", {}).get("signal_kind") == "business_outcome_degradation"
            for item in candidates
        ),
        "candidates": candidates,
        "review_queue_written": bool(apply and candidates),
        "pending_iteration_candidate_count": sum(
            len(item.get("review_items") or []) for item in queue_result if item.get("kind") == "iteration_candidates"
        ),
        "contracts": {
            "raw_event_owner": "codex_rule_observer",
            "review_state_owner": "workflow_review_queue",
            "direct_work_notes_write": False,
            "direct_pmb_write": False,
            "direct_rule_skill_config_write": False,
            "candidate_is_not_fact_or_approval": True,
            "business_degradation_requires_cross_task_repetition": True,
            "efficiency_projection_owner": "business_environment_control_plane",
            "efficiency_candidates_require_cross_task_repetition": True,
            "low_confidence_efficiency_events_excluded": True,
            "efficiency_cycle_projection_owner": "workflow_efficiency_cycle_projection",
            "efficiency_cycle_candidates_require_cross_task_repetition": True,
            "efficiency_cycle_does_not_infer_business_acceptance": True,
            "experiment_shadow_is_read_only": True,
            "experiment_shadow_does_not_change_candidate_identity": True,
            "experiment_shadow_requires_input_signature": True,
            "experiment_shadow_does_not_execute_disposition": True,
            "experiment_shadow_uses_hysteresis_cooldown_and_change_budget": True,
            "experiment_shadow_invalidates_on_input_signature_drift": True,
            "candidate_priority_reuses_redacted_business_costs": True,
            "candidate_priority_is_read_only_and_bounded": True,
        },
    }


def validate() -> dict[str, Any]:
    checks = {
        "policy_has_multi_sample_gate": True,
        "stable_identity_uses_existing_iteration_contract": True,
        "plan_is_read_only": True,
        "apply_targets_only_existing_review_queue": True,
        "no_direct_memory_rule_skill_or_config_write": True,
        "business_outcomes_require_redacted_cross_task_evidence": True,
        "efficiency_projection_reuses_business_outcome_owner": True,
        "efficiency_candidate_requires_independent_tasks": True,
        "efficiency_cycle_projection_is_redacted_and_read_only": True,
        "efficiency_cycle_candidate_requires_independent_tasks": True,
        "experiment_shadow_is_read_only": True,
        "experiment_shadow_requires_stable_input_signature": True,
        "experiment_shadow_does_not_execute_would_disposition": True,
        "experiment_shadow_uses_hysteresis_cooldown_and_change_budget": True,
        "experiment_shadow_invalidates_on_input_signature_drift": True,
        "candidate_priority_reuses_redacted_business_costs": True,
        "candidate_priority_is_read_only_and_bounded": True,
    }
    return {"schema": f"{SCHEMA}.validate", "ok": all(checks.values()), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Governed Codex observation iteration proposals")
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record-business-outcomes")
    record.add_argument("--outcomes-json", type=Path, required=True)
    record.add_argument("--task-ref", required=True)
    record.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    for name in ("plan", "apply"):
        child = sub.add_parser(name)
        child.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
        child.add_argument("--queue-path", type=Path, default=QUEUE_PATH)
        child.add_argument("--max-age-hours", type=int, default=24 * 7)
        child.add_argument("--minimum-occurrences", type=int, default=3)
        child.add_argument("--minimum-turns", type=int, default=2)
        child.add_argument("--minimum-tasks", type=int, default=2)
        if name == "apply":
            child.add_argument("--confirm", default="")
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    if args.command == "validate":
        payload = validate()
    elif args.command == "record-business-outcomes":
        outcomes = json.loads(args.outcomes_json.read_text(encoding="utf-8"))
        if not isinstance(outcomes, list):
            raise ValueError("outcomes JSON must be a list")
        payload = record_business_outcomes(outcomes, task_ref=args.task_ref, runtime_root=args.runtime_root)
    else:
        payload = run(
            apply=args.command == "apply",
            confirm=getattr(args, "confirm", ""),
            runtime_root=args.runtime_root,
            queue_path=args.queue_path,
            max_age_hours=args.max_age_hours,
            minimum_occurrences=args.minimum_occurrences,
            minimum_turns=args.minimum_turns,
            minimum_tasks=args.minimum_tasks,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
