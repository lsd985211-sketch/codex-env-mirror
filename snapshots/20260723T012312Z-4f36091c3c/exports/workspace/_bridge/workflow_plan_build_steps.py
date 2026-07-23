#!/usr/bin/env python3
"""Pure build-step helpers for workflow_orchestrator plans.

Ownership: small reusable transformations used while assembling a workflow
plan from already classified domains and already built machine phases.
Non-goals: classifying domains, reading skill bodies, executing tools, or
mutating workflow state.
State behavior: read-only; all functions operate on caller-supplied data.
Caller context: workflow_orchestrator.build_plan.
"""

from __future__ import annotations

from typing import Any, Callable


def collect_domain_routes(
    domain_matches: list[dict[str, Any]],
    *,
    domain_drives_execution_fn: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    """Collect selected domains and route candidates from classifier matches."""
    selected_domains: list[dict[str, Any]] = []
    skill_candidates: list[str] = []
    slash_candidates: list[str] = []
    matrix_terms: list[str] = []
    maintenance: list[str] = []
    validation: list[str] = []
    for item in domain_matches:
        domain = item["domain"]
        drives_execution = domain_drives_execution_fn(item)
        selected_domains.append(
            {
                "key": domain.key,
                "label": domain.label,
                "keyword_hits": item["hits"],
                "score": item.get("score", 0),
                "confidence": item.get("confidence", 0.0),
                "route_confidence": item.get("route_confidence", item.get("confidence", 0.0)),
                "candidate_ratio": item.get("candidate_ratio", 0.0),
                "match_quality": item.get("match_quality", ""),
                "systems": list(item.get("systems") or []),
                "drives_execution": drives_execution,
            }
        )
        if not drives_execution:
            continue
        skill_candidates.extend(domain.skills)
        slash_candidates.extend(domain.slash)
        matrix_terms.extend(domain.matrix_terms)
        maintenance.extend(domain.maintenance)
        validation.extend(domain.validation)
    return {
        "selected_domains": selected_domains,
        "skill_candidates": skill_candidates,
        "slash_candidates": slash_candidates,
        "matrix_terms": matrix_terms,
        "maintenance": maintenance,
        "validation": validation,
    }


def build_skill_orchestration(
    message: str,
    *,
    build_skill_orchestration_plan: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    """Call the optional skill orchestrator with a stable fallback payload."""
    fallback: dict[str, Any] = {
        "ok": False,
        "reason": "skill_orchestrator_unavailable",
        "rule": "fallback to static workflow_orchestrator skill candidates",
    }
    if build_skill_orchestration_plan is None:
        return fallback
    try:
        return build_skill_orchestration_plan(message)
    except Exception as exc:  # noqa: BLE001 - optional layer failure is plan evidence.
        return {
            "ok": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "rule": "fallback to static workflow_orchestrator skill candidates",
        }


def phase_execution_summary(machine_phases: list[dict[str, Any]]) -> dict[str, Any]:
    """Return active phase ids, dependency graph, and skipped phase evidence."""
    active_phase_ids = [str(phase.get("id") or "") for phase in machine_phases if phase.get("enabled")]
    active_phase_set = set(active_phase_ids)
    active_dependency_graph = {
        str(phase.get("id") or ""): [
            dep for dep in phase.get("depends_on", [])
            if dep in active_phase_set
        ]
        for phase in machine_phases
        if phase.get("enabled")
    }
    skipped_phases = [
        {"id": phase.get("id"), "reason": phase.get("skip_reason")}
        for phase in machine_phases
        if not phase.get("enabled")
    ]
    return {
        "active_phase_ids": active_phase_ids,
        "active_dependency_graph": active_dependency_graph,
        "skipped_phases": skipped_phases,
    }


def skill_orchestration_summary(skill_orchestration: dict[str, Any], *, limit: int = 4) -> dict[str, Any]:
    """Project skill orchestration output into the workflow plan schema."""
    return {
        "schema": skill_orchestration.get("schema", "skill_orchestrator.plan.v1"),
        "ok": bool(skill_orchestration.get("ok")),
        "inventory": skill_orchestration.get("inventory", {}),
        "selected_skills": [
            {
                "name": item.get("name"),
                "score": item.get("score"),
                "reasons": item.get("reasons", []),
                "path": item.get("path", ""),
            }
            for item in skill_orchestration.get("selected_skills", [])[:limit]
            if isinstance(item, dict)
        ],
        "gap_proposals": skill_orchestration.get("gap_proposals", [])[:6],
        "rule": "Use this as a dynamic skill preflight; still read the selected SKILL.md before relying on it.",
    }
