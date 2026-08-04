#!/usr/bin/env python3
"""Derive one bounded Codex work budget from existing workflow facts.

Ownership: pure task-level phase and validation-budget projection.
Non-goals: task classification, owner selection, permission decisions, command
execution, receipt persistence, or business-state mutation.
State behavior: pure and read-only.
Caller context: workflow_orchestrator after task facts and machine phases exist.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


SCHEMA = "codex_task_execution_budget.v2"
HIGH_RISK_VALUES = {"l3", "r3", "r4", "high", "dangerous"}
DEEP_FACTS = {"destructive_or_high_risk"}
FULL_FACTS = {
    "local_write",
    "config_change",
    "external_write",
    "resource_materialization",
    "package_install",
    "database_write",
    "gui_or_browser_state",
    "secret_or_permission_use",
    "reload_or_restart_required",
    "system_member_change",
    "durable_closeout_required",
    "explicit_mobile_envelope",
}
SOFT_PHASES = {
    "phase_2_recall": "no_history_or_external_knowledge_dependency",
    "phase_3_skill_selection": "no_selected_skill_assets",
    "phase_5_tool_route": "no_tool_or_capability_route",
    "phase_7_execution": "no_local_or_external_execution",
}
FEEDBACK_SCHEMA = "adaptive_efficiency_budget_feedback.v1"
FEEDBACK_EXPERIMENT_SCHEMA = "adaptive_efficiency_experiment.v1"
FEEDBACK_STABILITY_SCHEMA = "adaptive_efficiency_stability.v1"
FEEDBACK_SOFT_PHASES = frozenset(SOFT_PHASES)
FEEDBACK_REQUIRED_GUARDRAILS = frozenset(
    {"semantic_equivalence", "authority_freshness", "accepted_and_consumed_required_for_promote"}
)


def _enabled_facts(task_facts: Mapping[str, Any]) -> set[str]:
    return {str(key) for key, value in task_facts.items() if value is True}


def execution_budget_input_signature(
    *,
    profile: str,
    task_facts: Mapping[str, Any],
    machine_phases: Sequence[Mapping[str, Any]],
    risk: str = "unknown",
    selected_asset_count: int = 0,
    route_term_count: int = 0,
    maintenance_count: int = 0,
    validation_count: int = 0,
    simple_fast_path: bool = False,
) -> str:
    """Return a stable, low-cardinality signature for one budget decision."""

    projection = {
        "profile": str(profile or "general"),
        "risk": str(risk or "unknown"),
        "task_facts": sorted(str(key) for key, value in task_facts.items() if value is True),
        "machine_phases": sorted(
            [
                {
                    "id": str(phase.get("id") or ""),
                    "enabled": phase.get("enabled") is True,
                    "skip_reason": str(phase.get("skip_reason") or ""),
                }
                for phase in machine_phases
                if phase.get("id")
            ],
            key=lambda item: (item["id"], item["enabled"], item["skip_reason"]),
        ),
        "selected_asset_count": max(0, int(selected_asset_count or 0)),
        "route_term_count": max(0, int(route_term_count or 0)),
        "maintenance_count": max(0, int(maintenance_count or 0)),
        "validation_count": max(0, int(validation_count or 0)),
        "simple_fast_path": bool(simple_fast_path),
    }
    encoded = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()[:48]}"


def evaluate_feedback_projection(
    feedback_projection: Mapping[str, Any] | None,
    *,
    current_input_signature: str,
    task_facts: Mapping[str, Any],
    validation_tier: str,
    has_material_effect: bool,
    has_route: bool,
    machine_phase_ids: set[str],
) -> dict[str, Any]:
    """Validate an existing observation recommendation before budget use.

    The observer remains the authority for evidence and disposition. This
    function only consumes a bounded projection and never persists or
    reinterprets authorization, owner, or business outcomes.
    """

    base = {
        "schema": FEEDBACK_SCHEMA,
        "status": "ignored",
        "reason": "feedback_not_provided",
        "applied_phase_ids": [],
        "input_signature_ref": current_input_signature,
        "candidate_ref": "",
        "read_only": True,
        "writes_business_state": False,
        "writes_review_queue": False,
    }
    if not isinstance(feedback_projection, Mapping) or not feedback_projection:
        return base
    experiment = feedback_projection.get("experiment_shadow")
    experiment = experiment if isinstance(experiment, Mapping) else feedback_projection
    stability = feedback_projection.get("stability")
    stability = stability if isinstance(stability, Mapping) else {}
    if str(experiment.get("schema") or "") != FEEDBACK_EXPERIMENT_SCHEMA:
        return {**base, "reason": "experiment_schema_invalid"}
    if str(feedback_projection.get("budget_input_signature_ref") or "") != current_input_signature:
        return {**base, "reason": "budget_input_signature_changed", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    if experiment.get("eligibility") != "eligible" or experiment.get("would_disposition") != "would_promote":
        return {**base, "reason": "experiment_not_promotable", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    if not str(experiment.get("candidate_ref") or "").strip() or not str(experiment.get("owner") or "").strip():
        return {**base, "reason": "experiment_binding_incomplete"}
    if not str(experiment.get("input_signature_ref") or "").strip():
        return {**base, "reason": "experiment_input_signature_missing"}
    guardrails = {str(item) for item in experiment.get("guardrails") or []}
    if not FEEDBACK_REQUIRED_GUARDRAILS.issubset(guardrails):
        return {**base, "reason": "experiment_guardrails_incomplete"}
    baseline = experiment.get("baseline_window") if isinstance(experiment.get("baseline_window"), Mapping) else {}
    sample_policy = experiment.get("sample_policy") if isinstance(experiment.get("sample_policy"), Mapping) else {}
    if int(baseline.get("independent_task_count") or 0) < int(sample_policy.get("minimum_tasks") or 2):
        return {**base, "reason": "experiment_task_sample_insufficient"}
    if int(baseline.get("occurrence_count") or 0) < int(sample_policy.get("minimum_occurrences") or 3):
        return {**base, "reason": "experiment_occurrence_sample_insufficient"}
    if str(stability.get("schema") or "") != FEEDBACK_STABILITY_SCHEMA:
        return {**base, "reason": "stability_projection_missing", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    if stability.get("disposition") != "would_promote":
        return {**base, "reason": "stability_not_promotable", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    if stability.get("input_signature_matches") is not True:
        return {**base, "reason": "stability_input_signature_changed", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    if stability.get("guardrail_ok") is not True:
        return {**base, "reason": "stability_guardrail_failed", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    if int(stability.get("cooldown_remaining") or 0) > 0:
        return {**base, "reason": "stability_cooldown_active", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    policy = stability.get("policy") if isinstance(stability.get("policy"), Mapping) else {}
    if int(stability.get("disposition_changes") or 0) >= int(policy.get("max_disposition_changes") or 1):
        return {**base, "reason": "stability_change_budget_exhausted", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    if validation_tier != "quick" or has_material_effect:
        return {**base, "reason": "budget_upgrade_required", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    facts = _enabled_facts(task_facts)
    if facts & {"external_knowledge_candidate", "external_network_read", "gui_or_browser_state", "explicit_mobile_envelope"}:
        return {**base, "reason": "feedback_dependency_not_bounded", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    recommended = feedback_projection.get("recommended_phase_ids")
    if not isinstance(recommended, list) or not recommended:
        return {**base, "reason": "recommended_phase_set_missing", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    requested = {str(item) for item in recommended if str(item) in FEEDBACK_SOFT_PHASES}
    unknown = {str(item) for item in recommended} - FEEDBACK_SOFT_PHASES
    if unknown:
        return {**base, "reason": "recommended_phase_not_soft", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    if "phase_2_recall" in requested and facts & {"external_knowledge_candidate", "external_network_read"}:
        requested.remove("phase_2_recall")
    if "phase_3_skill_selection" in requested and int(feedback_projection.get("selected_asset_count") or 0) > 0 and feedback_projection.get("asset_reuse_verified") is not True:
        requested.remove("phase_3_skill_selection")
    if "phase_5_tool_route" in requested and has_route:
        requested.remove("phase_5_tool_route")
    if not requested or not requested.issubset(machine_phase_ids):
        return {**base, "reason": "recommended_phase_set_not_applicable", "candidate_ref": str(experiment.get("candidate_ref") or "")}
    return {
        **base,
        "status": "applied",
        "reason": "adaptive_feedback_promote_consumed",
        "applied_phase_ids": sorted(requested),
        "candidate_ref": str(experiment.get("candidate_ref") or ""),
        "owner": str(experiment.get("owner") or ""),
        "experiment_input_signature_ref": str(experiment.get("input_signature_ref") or ""),
    }


def validation_budget(
    *, profile: str, task_facts: Mapping[str, Any], risk: str = "unknown"
) -> dict[str, Any]:
    """Select validation depth from side effects and risk, not a broad label."""

    facts = _enabled_facts(task_facts)
    risk_value = str(risk or "unknown").strip().casefold()
    if risk_value in HIGH_RISK_VALUES or facts & DEEP_FACTS:
        tier = "deep"
        reason = "high_risk_or_destructive_effect"
    elif facts & FULL_FACTS:
        tier = "full"
        reason = "state_or_external_side_effect"
    else:
        tier = "quick"
        reason = "read_only_or_no_material_side_effect"
    return {
        "tier": tier,
        "reason": reason,
        "trigger_facts": sorted((facts & (FULL_FACTS | DEEP_FACTS))),
        "profile_is_not_sufficient_to_escalate": profile == "maintenance_governance",
    }


def build_execution_budget(
    *,
    profile: str,
    task_facts: Mapping[str, Any],
    machine_phases: Sequence[Mapping[str, Any]],
    risk: str = "unknown",
    selected_asset_count: int = 0,
    route_term_count: int = 0,
    maintenance_count: int = 0,
    validation_count: int = 0,
    simple_fast_path: bool = False,
    feedback_projection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the bounded budget and a guarded Phase 2 phase-selection decision."""

    validation = validation_budget(profile=profile, task_facts=task_facts, risk=risk)
    active = [
        str(phase.get("id") or "")
        for phase in machine_phases
        if phase.get("enabled") is True and phase.get("id")
    ]
    skipped = {
        str(phase.get("id") or ""): str(phase.get("skip_reason") or "not_required")
        for phase in machine_phases
        if phase.get("enabled") is not True and phase.get("id")
    }
    owner_calls = sum(
        len(phase.get("commands") or [])
        for phase in machine_phases
        if phase.get("enabled") is True and isinstance(phase.get("commands"), list)
    )
    mode = {"quick": "bounded", "full": "full", "deep": "deep"}[validation["tier"]]
    facts = _enabled_facts(task_facts)
    has_material_effect = bool(facts & (FULL_FACTS | DEEP_FACTS))
    has_route = bool(route_term_count or maintenance_count or validation_count)
    current_input_signature = execution_budget_input_signature(
        profile=profile, task_facts=task_facts, machine_phases=machine_phases,
        risk=risk, selected_asset_count=selected_asset_count,
        route_term_count=route_term_count, maintenance_count=maintenance_count,
        validation_count=validation_count, simple_fast_path=simple_fast_path,
    )
    phase_selection: dict[str, Any] = {
        "schema": "codex_task_execution_budget.phase_selection.v1",
        "candidate_phase_ids": [],
        "skip_phase_ids": [],
        "skip_reasons": {},
        "activation_guard": "bounded_read_only_without_material_effect_or_route_dependency",
        "hard_phase_ids": ["phase_1_preflight", "phase_8_validation", "phase_9_closeout"],
        "enforced": False,
    }
    if validation["tier"] == "quick" and not has_material_effect:
        candidates = []
        if (simple_fast_path or (
            not facts.intersection({"external_knowledge_candidate", "external_network_read"}) and not has_route
        )):
            candidates.append("phase_2_recall")
        if simple_fast_path or not selected_asset_count:
            candidates.append("phase_3_skill_selection")
        if (simple_fast_path or not has_route) and not facts.intersection({"gui_or_browser_state", "explicit_mobile_envelope"}):
            candidates.append("phase_5_tool_route")
        if not facts.intersection({"local_write", "external_write", "resource_materialization", "package_install", "database_write", "gui_or_browser_state", "explicit_mobile_envelope"}):
            candidates.append("phase_7_execution")
        phase_selection["candidate_phase_ids"] = candidates
        phase_selection["skip_phase_ids"] = candidates
        phase_selection["skip_reasons"] = {phase_id: SOFT_PHASES[phase_id] for phase_id in candidates}
        phase_selection["enforced"] = bool(candidates)
    feedback = evaluate_feedback_projection(
        feedback_projection,
        current_input_signature=current_input_signature,
        task_facts=task_facts,
        validation_tier=validation["tier"],
        has_material_effect=has_material_effect,
        has_route=has_route,
        machine_phase_ids=set(active),
    )
    if feedback["status"] == "applied":
        selected = set(phase_selection["skip_phase_ids"]) | set(feedback["applied_phase_ids"])
        phase_selection["candidate_phase_ids"] = sorted(set(phase_selection["candidate_phase_ids"]) | selected)
        phase_selection["skip_phase_ids"] = sorted(selected)
        phase_selection["skip_reasons"].update({phase_id: "adaptive_feedback_promoted_reuse" for phase_id in feedback["applied_phase_ids"]})
        phase_selection["activation_guard"] = "adaptive_feedback_signature_guardrails"
        phase_selection["enforced"] = True
    unique_assets = max(0, int(selected_asset_count or 0))
    asset_projection = {
        "schema": "codex_task_execution_budget.asset_projection.v1",
        "selected_count": unique_assets,
        "duplicate_count": 0,
        "reuse_rule": "selected assets are already unique_limited by the route owner; no second asset catalog is consulted",
        "enforced": True,
    }
    skipped_commands = sum(
        len(phase.get("commands") or [])
        for phase in machine_phases
        if str(phase.get("id") or "") in set(phase_selection["skip_phase_ids"])
        and isinstance(phase.get("commands"), list)
    )
    effective_phase_count = len(active) - len(phase_selection["skip_phase_ids"])
    effective_owner_calls = max(0, owner_calls - skipped_commands)
    estimated_work_units = effective_phase_count + effective_owner_calls + max(0, int(selected_asset_count or 0))
    return {
        "schema": SCHEMA,
        "mode": mode,
        "decision_owner": "workflow_orchestrator",
        "activation_phase": "phase_2_phase_and_asset_dedup",
        "required_phase_ids": active,
        "skipped_phase_reasons": skipped,
        "owner_call_budget": effective_owner_calls,
        "planned_owner_call_budget": owner_calls,
        "skipped_owner_call_count": skipped_commands,
        "selected_asset_count": max(0, int(selected_asset_count or 0)),
        "validation_tier": validation["tier"],
        "validation_reason": validation["reason"],
        "validation_trigger_facts": validation["trigger_facts"],
        "phase_selection": phase_selection,
        "adaptive_feedback": feedback,
        "adaptive_feedback_input_signature_ref": current_input_signature,
        "asset_projection": asset_projection,
        "estimated_codex_work_units": estimated_work_units,
        "estimated_saved_work_units": len(phase_selection["skip_phase_ids"]),
        "phase_selection_enforced": phase_selection["enforced"],
        "validation_tier_enforced": True,
        "read_only_projection": True,
        "writes_business_state": False,
        "acceptance_required": True,
        "upgrade_triggers": [
            "state_or_external_side_effect_discovered",
            "high_risk_or_destructive_effect_discovered",
            "source_or_owner_scope_drift",
        ],
    }
