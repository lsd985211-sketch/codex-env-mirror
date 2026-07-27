#!/usr/bin/env python3
"""Read-only knowledge extraction sidecar for the controlled iteration layer."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from typing import Any

from iteration_knowledge_models import (
    KnowledgeCandidate,
    KnowledgeCluster,
    KnowledgePromotionSuggestion,
)
from iteration_knowledge_score import merge_confidence, suggestion_priority


def _candidate_id(index: int) -> str:
    return f"kc-{index:03d}"


def _cluster_id(index: int) -> str:
    return f"kcl-{index:03d}"


def _promotion_target_for_route(route: str) -> str:
    normalized = str(route or "").strip().lower()
    if "skill" in normalized:
        return "skill_proposal"
    if "tool registry" in normalized or "cli" in normalized:
        return "policy_or_skill_proposal"
    if "project knowledge" in normalized:
        return "project_knowledge_proposal"
    return "observe_only"


def _validation_target(step_name: str) -> str:
    normalized = str(step_name or "").strip().lower()
    if "tool-registry" in normalized:
        return "policy_or_skill_proposal"
    if "resource-layer" in normalized:
        return "policy_or_skill_proposal"
    if "iteration-layer" in normalized:
        return "policy_or_skill_proposal"
    return "observe_only"


def _candidate_type(finding: dict[str, Any]) -> str:
    kind = str(finding.get("kind") or "").strip().lower()
    route = str(finding.get("recommended_route") or "").strip().lower()
    summary = str(finding.get("summary") or "").strip().lower()
    if "tool-state" in kind or "tool registry" in route:
        return "resource_strategy"
    if "cli" in route or "automation" in route:
        return "operation_skill"
    if "safety" in kind or "framework" in summary:
        return "repair_strategy"
    if "knowledge" in route:
        return "failure_pattern"
    return "operation_skill"


def _candidate_scope(finding: dict[str, Any]) -> str:
    route = str(finding.get("recommended_route") or "").strip().lower()
    summary = str(finding.get("summary") or "").strip().lower()
    if "tool registry" in route:
        return "bridge.tooling"
    if "cli" in route:
        return "bridge.iteration"
    if "skill" in route:
        return "framework.skill"
    if "project knowledge" in route:
        return "framework.knowledge"
    if "validation" in summary:
        return "framework.validation"
    return "framework.general"


def _candidate_preconditions(finding: dict[str, Any]) -> list[str]:
    summary = str(finding.get("summary") or "").strip()
    route = str(finding.get("recommended_route") or "").strip()
    preconditions = []
    if route:
        preconditions.append(f"route={route}")
    if summary:
        preconditions.append(f"summary={summary}")
    return preconditions


def _is_high_value_finding(finding: dict[str, Any]) -> bool:
    kind = str(finding.get("kind") or "").strip().lower()
    route = str(finding.get("recommended_route") or "").strip().lower()
    summary = str(finding.get("summary") or "").strip().lower()
    if kind in {"safety-gate", "phase-readiness", "skill-entrypoint"}:
        return True
    if "tool registry" in route:
        return True
    if "validation" in summary or "read-only review command" in summary:
        return True
    return False


def extract_knowledge_candidates(findings: list[dict[str, Any]]) -> list[KnowledgeCandidate]:
    candidates: list[KnowledgeCandidate] = []
    for index, finding in enumerate(findings, start=1):
        if not _is_high_value_finding(finding):
            continue
        route = str(finding.get("recommended_route") or "")
        target = _promotion_target_for_route(route)
        if target == "observe_only":
            continue
        confidence = "verified_file"
        if "mtime=" in str(finding.get("evidence") or ""):
            confidence = "verified_file"
        if "verified_text" in str(finding.get("confidence") or ""):
            confidence = "verified_file"
        candidate = KnowledgeCandidate(
            candidate_id=_candidate_id(index),
            type=_candidate_type(finding),
            summary=str(finding.get("summary") or "").strip(),
            evidence_refs=[str(finding.get("evidence") or "").strip()],
            scope=_candidate_scope(finding),
            preconditions=_candidate_preconditions(finding),
            recommended_action=str(finding.get("proposal") or "").strip(),
            confidence=confidence,
            promotion_target=target,
            source_kind=str(finding.get("kind") or "").strip(),
        )
        candidates.append(candidate)
    return candidates


def extract_validation_candidates(validation_plan: list[dict[str, Any]], start_index: int) -> list[KnowledgeCandidate]:
    candidates: list[KnowledgeCandidate] = []
    for offset, step in enumerate(validation_plan, start=0):
        step_name = str(step.get("name") or "").strip()
        target = _validation_target(step_name)
        if target == "observe_only":
            continue
        purpose = str(step.get("purpose") or "").strip()
        command = " ".join(str(item) for item in (step.get("command") or []))
        scope = "framework.validation"
        candidate_type = "operation_skill"
        if "tool-registry" in step_name.lower():
            scope = "bridge.tooling"
            candidate_type = "resource_strategy"
        elif "resource-layer" in step_name.lower():
            scope = "resource.layer"
            candidate_type = "resource_strategy"
        candidate = KnowledgeCandidate(
            candidate_id=_candidate_id(start_index + offset),
            type=candidate_type,
            summary=f"Use validation step `{step_name}` to verify: {purpose}",
            evidence_refs=[command],
            scope=scope,
            preconditions=[f"validation_step={step_name}"],
            recommended_action=f"Keep `{step_name}` in the bounded validation path and run it before promoting related operational knowledge.",
            confidence="verified_file",
            promotion_target=target,
            source_kind="validation-step",
        )
        candidates.append(candidate)
    return candidates


def cluster_knowledge_candidates(candidates: list[KnowledgeCandidate]) -> list[KnowledgeCluster]:
    grouped: dict[tuple[str, str, str], list[KnowledgeCandidate]] = defaultdict(list)
    for candidate in candidates:
        key = (candidate.type, candidate.scope, candidate.promotion_target)
        grouped[key].append(candidate)

    clusters: list[KnowledgeCluster] = []
    for index, ((candidate_type, scope, promotion_target), items) in enumerate(grouped.items(), start=1):
        confidence = merge_confidence(item.confidence for item in items)
        summary = items[0].summary if len(items) == 1 else f"{items[0].summary} (+{len(items) - 1} related)"
        clusters.append(
            KnowledgeCluster(
                cluster_id=_cluster_id(index),
                type=candidate_type,
                summary=summary,
                candidate_ids=[item.candidate_id for item in items],
                scope=scope,
                promotion_target=promotion_target,
                confidence=confidence,
            )
        )
    return clusters


def build_knowledge_promotion_suggestions(clusters: list[KnowledgeCluster]) -> list[KnowledgePromotionSuggestion]:
    suggestions: list[KnowledgePromotionSuggestion] = []
    for cluster in clusters:
        priority = suggestion_priority(cluster.scope, len(cluster.candidate_ids), cluster.confidence)
        suggestions.append(
            KnowledgePromotionSuggestion(
                cluster_id=cluster.cluster_id,
                priority=priority,
                target=cluster.promotion_target,
                reason=cluster.summary,
                validation="proposal_only: review evidence, confirm scope/preconditions, then promote through existing proposal flow",
            )
        )
    return suggestions


def extract_iteration_knowledge(findings: list[dict[str, Any]], validation_plan: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    candidates = extract_knowledge_candidates(findings)
    if validation_plan:
        candidates.extend(extract_validation_candidates(validation_plan, len(candidates) + 1))
    clusters = cluster_knowledge_candidates(candidates)
    suggestions = build_knowledge_promotion_suggestions(clusters)
    return {
        "knowledge_candidates": [asdict(item) for item in candidates],
        "knowledge_clusters": [asdict(item) for item in clusters],
        "knowledge_promotion_suggestions": [asdict(item) for item in suggestions],
    }
