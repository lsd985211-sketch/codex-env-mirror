#!/usr/bin/env python3
"""Read-only proposal generator for the controlled Codex iteration layer.

The command scans bounded, non-sensitive project artifacts and emits
classification proposals. It does not modify files.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from iteration_knowledge_extract import extract_iteration_knowledge


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECENT_LIMIT = 12
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 30
EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "attachments",
    "backup",
    "backups",
    "build",
    "dist",
    "lib",
    "logs",
    "login-runs",
    "node_modules",
    "npm-cache",
    "openclaw-extract",
    "pnpm-store",
    "runtime",
    "site-packages",
    "temp",
    "tmp",
    "venv",
    "venvs",
}
SENSITIVE_NAMES = {
    "config.local.json",
    "mobile_openclaw_bridge.db",
    "mobile_openclaw_bridge.db-shm",
    "mobile_openclaw_bridge.db-wal",
}


@dataclass(frozen=True)
class Finding:
    kind: str
    confidence: str
    summary: str
    evidence: str
    recommended_route: str
    proposal: str
    risk: str = "L1"


@dataclass(frozen=True)
class ProposalPackage:
    target: str
    change: str
    reason: str
    evidence: str
    risk: str
    backup: str
    validation: str
    rollback: str
    needs_user_confirmation: bool = True


@dataclass(frozen=True)
class ValidationStep:
    name: str
    command: list[str]
    purpose: str
    safe_default: bool = True


@dataclass(frozen=True)
class ValidationResult:
    name: str
    command: list[str]
    returncode: int
    passed: bool
    timed_out: bool
    stdout_tail: str
    stderr_tail: str


@dataclass(frozen=True)
class ProposalGroup:
    name: str
    priority: str
    description: str
    proposal_indexes: list[int]


@dataclass(frozen=True)
class RecommendedAction:
    priority: str
    action: str
    reason: str
    validation: str


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("/", "\\")
    except ValueError:
        return str(path)


def iter_safe_files(root: Path, limit: int) -> Iterable[Path]:
    candidates: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname.lower() not in EXCLUDED_DIRS
        ]
        current = Path(current_root)
        parts = {part.lower() for part in current.relative_to(root).parts}
        if parts & EXCLUDED_DIRS:
            continue
        for filename in filenames:
            path = current / filename
            if path.name in SENSITIVE_NAMES:
                continue
            if path.suffix.lower() not in {".md", ".py", ".ps1", ".json"}:
                continue
            candidates.append(path)
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    yield from candidates[:limit]


def add_recent_artifact_findings(limit: int) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_safe_files(ROOT / "_bridge", limit):
        route = "tool registry" if path.name == "TOOL_REGISTRY.md" else "project knowledge"
        if path.name.endswith(".py"):
            route = "CLI automation proposal"
        findings.append(
            Finding(
                kind="tool-state observation",
                confidence="verified_file_metadata",
                summary=f"Recent safe artifact may contain reusable operational knowledge: {rel(path)}",
                evidence=f"mtime={datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()} size={path.stat().st_size}",
                recommended_route=route,
                proposal="Review the artifact for reusable workflow, validation, or routing rules before promoting anything to skills or memory.",
            )
        )
    return findings


def add_design_findings() -> list[Finding]:
    design = ROOT / "docs" / "superpowers" / "specs" / "2026-06-23-codex-controlled-iteration-layer-design.md"
    if not design.exists():
        return [
            Finding(
                kind="missing-framework-doc",
                confidence="verified_absence",
                summary="Controlled iteration layer design document is missing.",
                evidence=rel(design),
                recommended_route="project knowledge",
                proposal="Create or restore the design document before implementing runtime review commands.",
            )
        ]
    text = design.read_text(encoding="utf-8")
    findings: list[Finding] = []
    if "No local file modification without asking the user first." in text:
        findings.append(
            Finding(
                kind="safety-gate",
                confidence="verified_text",
                summary="Iteration layer design includes the mandatory ask-before-edit safety gate.",
                evidence=rel(design),
                recommended_route="skill",
                proposal="Keep this rule as a hard gate for future iteration-layer implementation.",
            )
        )
    if "Phase 2: Read-only review command" in text:
        findings.append(
            Finding(
                kind="phase-readiness",
                confidence="verified_text",
                summary="Design explicitly allows a phase-2 read-only review command.",
                evidence=rel(design),
                recommended_route="CLI automation proposal",
                proposal="Implement phase 2 as dry-run/read-only by default and keep persistent writes behind user confirmation.",
            )
        )
    return findings


def add_skill_findings() -> list[Finding]:
    skill = ROOT / ".codex" / "skills" / "codex-cli" / "SKILL.md"
    if not skill.exists():
        return []
    text = skill.read_text(encoding="utf-8")
    if "iteration_layer_review.py" in text:
        return [
            Finding(
                kind="skill-entrypoint",
                confidence="verified_text",
                summary="codex-cli skill documents the iteration review command.",
                evidence=rel(skill),
                recommended_route="skill",
                proposal="Use the documented command for read-only iteration-layer proposal generation.",
            )
        ]
    return [
        Finding(
            kind="skill-entrypoint-gap",
            confidence="verified_text_absence",
            summary="codex-cli skill does not yet document the iteration review command.",
            evidence=rel(skill),
            recommended_route="skill",
            proposal="After user approval, add a short command entry so future Codex turns can discover the read-only review command.",
        )
    ]


def route_to_target(route: str) -> str:
    normalized = route.lower()
    if "skill" in normalized:
        return "project or global skill"
    if "memory" in normalized:
        return "memory update note"
    if "tool registry" in normalized:
        return "_bridge/mobile_openclaw_bridge/TOOL_REGISTRY.md"
    if "cli" in normalized:
        return "_bridge CLI automation"
    if "project knowledge" in normalized:
        return "project knowledge or docs"
    return "ignore/archive"


def validation_for_route(route: str) -> str:
    normalized = route.lower()
    if "skill" in normalized:
        return "Re-read the changed SKILL.md and run a task-specific dry-run if applicable."
    if "tool registry" in normalized:
        return "Run python _bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py tool-registry-health."
    if "cli" in normalized:
        return "Run the relevant command with --dry-run or --help and confirm no unintended writes."
    if "project knowledge" in normalized:
        return "Re-read the updated document and check for stale paths, unsupported claims, or missing rollback notes."
    if "memory" in normalized:
        return "Confirm the memory note is concise, durable, sourced, and not a raw transcript."
    return "No validation required unless the item is promoted later."


def proposal_from_finding(finding: Finding) -> ProposalPackage:
    target = route_to_target(finding.recommended_route)
    return ProposalPackage(
        target=target,
        change=f"Review and, only after approval, promote this finding to {target}.",
        reason=finding.summary,
        evidence=finding.evidence,
        risk=finding.risk,
        backup="Create a timestamped marker under _bridge/backups before any persistent update.",
        validation=validation_for_route(finding.recommended_route),
        rollback="Restore the backup copy or delete the newly added proposal/update file.",
        needs_user_confirmation=True,
    )


def build_proposals(findings: list[Finding]) -> list[ProposalPackage]:
    return [proposal_from_finding(item) for item in findings]


def classify_proposal_group(finding: Finding) -> tuple[str, str, str]:
    if finding.kind in {"safety-gate", "phase-readiness", "skill-entrypoint"}:
        return (
            "established-framework-rules",
            "P0",
            "Rules already documented or verified; preserve them and use them as guardrails.",
        )
    route = finding.recommended_route.lower()
    if "tool registry" in route:
        return (
            "tool-registry-review",
            "P1",
            "Tool availability and invocation facts that may belong in the project tool registry.",
        )
    if "cli" in route:
        return (
            "cli-automation-review",
            "P1",
            "Recent automation code that may contain reusable checks, validation commands, or routing rules.",
        )
    if "project knowledge" in route:
        return (
            "project-knowledge-review",
            "P2",
            "Project-local scripts or checkpoints that may deserve concise documentation after review.",
        )
    return (
        "observe-only",
        "P3",
        "Items that should remain observations unless a concrete reusable rule is found.",
    )


def priority_rank(value: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(str(value or "").strip().upper(), 99)


def _knowledge_review_group(
    suggestions: list[dict[str, Any]],
    proposal_count: int,
) -> ProposalGroup | None:
    if not suggestions:
        return None

    sorted_suggestions = sorted(
        suggestions,
        key=lambda item: (priority_rank(item.get("priority", "P3")), str(item.get("target", ""))),
    )
    top = sorted_suggestions[:2]
    priority = top[0].get("priority", "P2")
    unique_targets: list[str] = []
    for item in top:
        target = str(item.get("target", "")).strip()
        if target and target not in unique_targets:
            unique_targets.append(target)
    unique_reasons: list[str] = []
    for item in top:
        reason = str(item.get("reason", "")).strip()
        if reason and reason not in unique_reasons:
            unique_reasons.append(reason)
    description = (
        "Knowledge extraction found promotion candidates worth explicit review before any persistent update."
    )
    if unique_targets:
        description += f" Focus targets: {', '.join(unique_targets)}."
    if unique_reasons:
        description += f" Leading rationale: {'; '.join(unique_reasons)}"
    return ProposalGroup(
        name="knowledge-promotion-review",
        priority=str(priority),
        description=description,
        proposal_indexes=list(range(1, proposal_count + 1)),
    )


def _knowledge_target_guidance(target: str) -> tuple[str, str, str]:
    normalized = str(target or "").strip().lower()
    if normalized == "skill_proposal":
        return (
            "project or global skill",
            "Promote only stable operator guidance or reusable workflow rules; do not encode one-off incidents or mutable runtime state.",
            "Re-read the target SKILL.md, keep the entry concise, and validate with a task-specific dry-run where available.",
        )
    if normalized == "project_knowledge_proposal":
        return (
            "project knowledge or docs",
            "Promote only durable project-local facts, paths, or runbook guidance; keep raw incident detail out.",
            "Re-read the updated document and confirm paths, scope, and rollback notes still match the current workspace.",
        )
    return (
        "_bridge CLI automation or skill proposal",
        "Keep promotion proposal-only until a human approves the destination; prefer policy/skill guidance over broad framework rewrites.",
        "Run the referenced validation step or a bounded dry-run before promoting any operational rule.",
    )


def build_knowledge_promotion_map(
    suggestions: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    cluster_index = {
        str(cluster.get("cluster_id", "")).strip(): cluster
        for cluster in clusters
    }
    mapped: list[dict[str, Any]] = []
    for suggestion in suggestions:
        cluster_id = str(suggestion.get("cluster_id", "")).strip()
        target = str(suggestion.get("target", "")).strip()
        cluster = cluster_index.get(cluster_id, {})
        recommended_destination, promotion_boundary, recommended_validation = _knowledge_target_guidance(target)
        mapped.append(
            {
                "cluster_id": cluster_id,
                "priority": str(suggestion.get("priority", "P3")).strip(),
                "target": target,
                "scope": str(cluster.get("scope", "")).strip(),
                "candidate_count": len(cluster.get("candidate_ids", []) or []),
                "recommended_destination": recommended_destination,
                "promotion_boundary": promotion_boundary,
                "recommended_validation": recommended_validation,
                "reason": str(suggestion.get("reason", "")).strip(),
                "apply_mode": str(suggestion.get("apply_mode", "proposal_only")).strip(),
            }
        )
    return mapped


def build_knowledge_promotion_batches(promotion_map: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in promotion_map:
        destination = str(item.get("recommended_destination", "")).strip() or "unclassified"
        grouped.setdefault(destination, []).append(item)

    batches: list[dict[str, Any]] = []
    for index, (destination, items) in enumerate(
        sorted(
            grouped.items(),
            key=lambda entry: (
                min(priority_rank(item.get("priority", "P3")) for item in entry[1]),
                entry[0],
            ),
        ),
        start=1,
    ):
        sorted_items = sorted(
            items,
            key=lambda item: (priority_rank(item.get("priority", "P3")), str(item.get("cluster_id", ""))),
        )
        unique_scopes: list[str] = []
        unique_validations: list[str] = []
        unique_boundaries: list[str] = []
        for item in sorted_items:
            scope = str(item.get("scope", "")).strip()
            validation = str(item.get("recommended_validation", "")).strip()
            boundary = str(item.get("promotion_boundary", "")).strip()
            if scope and scope not in unique_scopes:
                unique_scopes.append(scope)
            if validation and validation not in unique_validations:
                unique_validations.append(validation)
            if boundary and boundary not in unique_boundaries:
                unique_boundaries.append(boundary)
        batches.append(
            {
                "batch_id": f"kpb-{index:03d}",
                "priority": str(sorted_items[0].get("priority", "P3")).strip(),
                "recommended_destination": destination,
                "cluster_ids": [str(item.get("cluster_id", "")).strip() for item in sorted_items],
                "item_count": len(sorted_items),
                "scopes": unique_scopes,
                "approval_focus": "; ".join(
                    str(item.get("reason", "")).strip()
                    for item in sorted_items[:2]
                    if str(item.get("reason", "")).strip()
                ),
                "batch_validation": " Then ".join(unique_validations[:2]),
                "risk_boundary": " ".join(unique_boundaries[:2]),
            }
        )
    return batches


def build_promotion_readiness_summary(promotion_map: list[dict[str, Any]]) -> dict[str, Any]:
    ready_for_manual_review: list[str] = []
    candidate_only: list[str] = []
    needs_additional_validation: list[str] = []
    needs_boundary_confirmation: list[str] = []
    destination_counts: dict[str, int] = {}

    for item in promotion_map:
        cluster_id = str(item.get("cluster_id", "")).strip()
        priority = str(item.get("priority", "P3")).strip()
        scope = str(item.get("scope", "")).strip().lower()
        destination = str(item.get("recommended_destination", "")).strip() or "unclassified"
        destination_counts[destination] = destination_counts.get(destination, 0) + 1

        if priority == "P1":
            ready_for_manual_review.append(cluster_id)
        else:
            candidate_only.append(cluster_id)

        if scope in {"framework.validation", "bridge.tooling", "resource.layer"}:
            needs_additional_validation.append(cluster_id)
        if str(item.get("target", "")).strip() == "policy_or_skill_proposal":
            needs_boundary_confirmation.append(cluster_id)

    return {
        "total_clusters": len(promotion_map),
        "ready_for_manual_review": ready_for_manual_review,
        "candidate_only": candidate_only,
        "needs_additional_validation": needs_additional_validation,
        "needs_boundary_confirmation": needs_boundary_confirmation,
        "destination_counts": destination_counts,
    }


def build_proposal_groups(findings: list[Finding], *, knowledge_suggestions: list[dict[str, Any]] | None = None) -> list[ProposalGroup]:
    grouped: dict[str, dict[str, Any]] = {}
    for index, finding in enumerate(findings, start=1):
        name, priority, description = classify_proposal_group(finding)
        if name not in grouped:
            grouped[name] = {
                "name": name,
                "priority": priority,
                "description": description,
                "proposal_indexes": [],
            }
        grouped[name]["proposal_indexes"].append(index)

    groups = [
        ProposalGroup(**item)
        for item in sorted(
            grouped.values(),
            key=lambda item: (priority_rank(item["priority"]), item["name"]),
        )
    ]

    knowledge_review = _knowledge_review_group(knowledge_suggestions or [], len(findings))
    if knowledge_review is not None:
        groups.append(knowledge_review)

    return sorted(groups, key=lambda item: (priority_rank(item.priority), item.name))


def build_aligned_proposal_groups(
    finding_groups: list[ProposalGroup],
    *,
    knowledge_promotion_batches: list[dict[str, Any]] | None = None,
    promotion_readiness_summary: dict[str, Any] | None = None,
) -> list[ProposalGroup]:
    groups: list[ProposalGroup] = []
    batches = knowledge_promotion_batches or []
    summary = promotion_readiness_summary or {}

    for batch in batches:
        groups.append(
            ProposalGroup(
                name=f"batch-{str(batch.get('batch_id', '')).strip()}",
                priority=str(batch.get("priority", "P2")).strip(),
                description=(
                    f"Batch {batch.get('batch_id', '')} groups {batch.get('item_count', 0)} candidates for "
                    f"{batch.get('recommended_destination', 'manual review')}. "
                    f"Focus: {batch.get('approval_focus', '')}"
                ),
                proposal_indexes=list(range(1, len(finding_groups) + 1)),
            )
        )

    ready = summary.get("ready_for_manual_review", []) or []
    pending = summary.get("needs_additional_validation", []) or []
    if ready or pending:
        groups.append(
            ProposalGroup(
                name="knowledge-readiness-review",
                priority="P1" if ready else "P2",
                description=(
                    f"Ready clusters: {', '.join(ready[:3]) or 'none'}. "
                    f"Validation-first clusters: {', '.join(pending[:3]) or 'none'}."
                ),
                proposal_indexes=list(range(1, len(finding_groups) + 1)),
            )
        )

    for group in finding_groups:
        if group.name in {"knowledge-promotion-review", "cli-automation-review"}:
            groups.append(
                ProposalGroup(
                    name=group.name,
                    priority="P2",
                    description=group.description,
                    proposal_indexes=group.proposal_indexes,
                )
            )
        else:
            groups.append(group)

    return sorted(groups, key=lambda item: (priority_rank(item.priority), item.name))


def build_decision_summary(
    knowledge_promotion_batches: list[dict[str, Any]] | None = None,
    promotion_readiness_summary: dict[str, Any] | None = None,
    knowledge_promotion_map: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    batches = sorted(
        knowledge_promotion_batches or [],
        key=lambda item: (priority_rank(item.get("priority", "P3")), str(item.get("batch_id", ""))),
    )
    promotion_map = sorted(
        knowledge_promotion_map or [],
        key=lambda item: (priority_rank(item.get("priority", "P3")), str(item.get("cluster_id", ""))),
    )
    readiness = promotion_readiness_summary or {}

    primary_batch = batches[0] if batches else {}
    primary_boundary = promotion_map[0] if promotion_map else {}
    ready = readiness.get("ready_for_manual_review", []) or []
    validation_first = readiness.get("needs_additional_validation", []) or []
    destination_counts = readiness.get("destination_counts", {}) or {}
    top_destination = ""
    if destination_counts:
        top_destination = max(
            destination_counts.items(),
            key=lambda item: (item[1], item[0]),
        )[0]

    return {
        "primary_batch_id": str(primary_batch.get("batch_id", "")).strip(),
        "primary_destination": str(primary_batch.get("recommended_destination", "")).strip() or top_destination,
        "primary_focus": str(primary_batch.get("approval_focus", "")).strip(),
        "primary_validation": str(primary_batch.get("batch_validation", "")).strip(),
        "primary_boundary_cluster": str(primary_boundary.get("cluster_id", "")).strip(),
        "primary_boundary": str(primary_boundary.get("promotion_boundary", "")).strip(),
        "ready_for_manual_review": ready[:5],
        "validation_first": validation_first[:5],
        "top_destination": top_destination,
        "summary_text": (
            f"Start with {str(primary_batch.get('batch_id', 'manual review')).strip()} "
            f"toward {str(primary_batch.get('recommended_destination', top_destination or 'manual promotion')).strip()}; "
            f"review ready clusters {', '.join(ready[:3]) or 'none'} before validation-first clusters "
            f"{', '.join(validation_first[:3]) or 'none'}."
        ),
    }


def build_recommended_actions(
    groups: list[ProposalGroup],
    *,
    validation_passed: bool | None,
    knowledge_suggestions: list[dict[str, Any]] | None = None,
    knowledge_clusters: list[dict[str, Any]] | None = None,
    knowledge_promotion_map: list[dict[str, Any]] | None = None,
    knowledge_promotion_batches: list[dict[str, Any]] | None = None,
    promotion_readiness_summary: dict[str, Any] | None = None,
) -> list[RecommendedAction]:
    actions: list[RecommendedAction] = []
    if validation_passed is False:
        actions.append(
            RecommendedAction(
                priority="P0",
                action="Fix failing validation before promoting any proposal.",
                reason="A failed read-only validation means the iteration layer cannot safely judge later changes.",
                validation="Re-run python _bridge/iteration_layer_review.py --json --run-validation.",
            )
        )
        return actions

    actions.append(
        RecommendedAction(
            priority="P0",
            action="Keep the ask-before-edit and proposal-only gates unchanged.",
            reason="These are the core safety boundaries that prevent the iteration layer from becoming uncontrolled self-modification.",
            validation="Confirm safety.writes_files=false and requires_user_confirmation_for_updates=true in the report.",
        )
    )

    group_names = {group.name for group in groups}
    if "cli-automation-review" in group_names:
        actions.append(
            RecommendedAction(
                priority="P1",
                action="Review recent bridge automation files for reusable checks before editing skills.",
                reason="Recent worker, maintenance, health-check, MCP, and dashboard changes are likely to contain stable operational rules.",
                validation="Run the relevant command with --dry-run or --help before promoting any rule.",
            )
        )
    if "tool-registry-review" in group_names:
        actions.append(
            RecommendedAction(
                priority="P1",
                action="Compare tool-registry facts with current health output before changing the registry.",
                reason="Tool availability can drift; stale registry facts would make future diagnosis worse.",
                validation="Run python _bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py tool-registry-health.",
            )
        )
    if "project-knowledge-review" in group_names:
        actions.append(
            RecommendedAction(
                priority="P2",
                action="Promote only concise, durable project facts from scripts and checkpoints.",
                reason="Project knowledge should preserve stable operating rules, not raw incident detail.",
                validation="Re-read the changed document and check for stale paths or unsupported claims.",
            )
        )

    promotion_map = sorted(
        knowledge_promotion_map or [],
        key=lambda item: (priority_rank(item.get("priority", "P3")), str(item.get("cluster_id", ""))),
    )
    batches = sorted(
        knowledge_promotion_batches or [],
        key=lambda item: (priority_rank(item.get("priority", "P3")), str(item.get("batch_id", ""))),
    )
    readiness = promotion_readiness_summary or {}

    if batches:
        top_batch = batches[0]
        actions.append(
            RecommendedAction(
                priority=str(top_batch.get("priority", "P2")),
                action=f"Start with batch {top_batch.get('batch_id', 'kpb-001')} for {top_batch.get('recommended_destination', 'manual review')}.",
                reason=str(top_batch.get("approval_focus", "")).strip()
                or "This batch groups the strongest related promotion candidates into one manual review pass.",
                validation=str(top_batch.get("batch_validation", "")).strip()
                or "Keep promotion manual and verify supporting evidence first.",
            )
        )
    elif knowledge_suggestions:
        suggestions = sorted(
            knowledge_suggestions,
            key=lambda item: (priority_rank(item.get("priority", "P3")), str(item.get("target", ""))),
        )
        top = suggestions[0]
        actions.append(
            RecommendedAction(
                priority=str(top.get("priority", "P2")),
                action=f"Review {top.get('target', 'knowledge promotion candidates')} in proposal-only mode before changing skills or docs.",
                reason=str(top.get("reason", "")).strip()
                or "Knowledge extraction surfaced a high-value promotion path that should inform the next manual review pass.",
                validation=str(top.get("validation", "")).strip()
                or "Keep promotion manual and verify supporting evidence first.",
            )
        )

    if promotion_map:
        top_item = promotion_map[0]
        actions.append(
            RecommendedAction(
                priority=str(top_item.get("priority", "P2")),
                action=f"Keep cluster {top_item.get('cluster_id', '')} inside its promotion boundary while reviewing {top_item.get('recommended_destination', 'the destination')}.",
                reason=str(top_item.get("promotion_boundary", "")).strip()
                or "Promotion should stay inside an explicit boundary to avoid turning a local lesson into a global rule too early.",
                validation=str(top_item.get("recommended_validation", "")).strip()
                or "Run the relevant validation step before approval.",
            )
        )

    ready_clusters = readiness.get("ready_for_manual_review", []) or []
    pending_validation = readiness.get("needs_additional_validation", []) or []
    if ready_clusters or pending_validation:
        reason_parts: list[str] = []
        if ready_clusters:
            reason_parts.append(f"Ready for manual review: {', '.join(ready_clusters[:3])}")
        if pending_validation:
            reason_parts.append(f"Needs validation first: {', '.join(pending_validation[:3])}")
        actions.append(
            RecommendedAction(
                priority="P2",
                action="Use readiness summary to separate immediately reviewable clusters from validation-first clusters.",
                reason="; ".join(reason_parts),
                validation="Prefer reviewing ready_for_manual_review items first, then run validation_plan steps for validation-bound clusters.",
            )
        )
    return actions


def build_approval_block(
    groups: list[ProposalGroup],
    recommended_actions: list[RecommendedAction],
    decision_summary: dict[str, Any],
    *,
    proposal_count: int,
    validation_passed: bool | None,
) -> dict[str, Any]:
    top_actions = sorted(
        recommended_actions,
        key=lambda item: (priority_rank(item.priority), item.action),
    )[:5]
    top_groups = sorted(
        groups,
        key=lambda item: (priority_rank(item.priority), item.name),
    )[:5]
    primary_request = str(decision_summary.get("summary_text", "")).strip()
    if not primary_request:
        primary_request = "Review the generated proposals and approve a bounded destination before any persistent update."

    return {
        "status": "awaiting_user_approval",
        "apply_mode": "proposal_only",
        "user_decision_required": True,
        "approved_by_default": False,
        "primary_request": primary_request,
        "approval_prompt": (
            "Approve the specific target/action to promote, or ask for revision. "
            "Without approval, do not write to skills, memory, project knowledge, CLI files, or bridge state."
        ),
        "proposal_count": proposal_count,
        "recommended_scope": [
            {
                "priority": item.priority,
                "action": item.action,
                "validation": item.validation,
            }
            for item in top_actions
        ],
        "proposal_group_scope": [
            {
                "priority": item.priority,
                "name": item.name,
                "proposal_indexes": item.proposal_indexes,
            }
            for item in top_groups
        ],
        "pre_apply_requirements": [
            "Create a marked timestamped backup before any persistent file update.",
            "Re-read the exact target file or destination before editing.",
            "Run the relevant validation or dry-run command after the approved change.",
        ],
        "blocked_without_approval": [
            "skill updates",
            "memory writes",
            "project knowledge writes",
            "_bridge CLI or maintenance-script edits",
            "bridge queue, delivery, or reply-state changes",
        ],
        "validation_state": "passed" if validation_passed else "failed" if validation_passed is False else "not_run",
    }


def validation_plan(profile: str = "quick") -> list[ValidationStep]:
    quick_steps = [
        ValidationStep(
            name="iteration-layer-self-check",
            command=[sys.executable, str(ROOT / "_bridge" / "iteration_layer_review.py"), "--dry-run", "--recent-limit", "3"],
            purpose="Confirm the iteration review command still generates read-only findings and proposals.",
        ),
        ValidationStep(
            name="tool-registry-health",
            command=[sys.executable, str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py"), "tool-registry-health"],
            purpose="Check the local tool registry and Codex tool availability summary.",
        ),
        ValidationStep(
            name="resource-layer-smoke-check",
            command=[sys.executable, str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py"), "resource-layer-smoke-check"],
            purpose="Run the temp-only resource acquisition smoke check.",
        ),
        ValidationStep(
            name="memory-governance-validate",
            command=[sys.executable, str(ROOT / "_bridge" / "memory_governance.py"), "validate"],
            purpose="Confirm long-lived system work has an active memory governance loop, PMB metrics, and candidate-review visibility.",
        ),
    ]
    full_only_steps = [
        ValidationStep(
            name="resource-layer-smoke-check",
            command=[sys.executable, str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py"), "resource-layer-smoke-check"],
            purpose="Run the temp-only resource acquisition smoke check.",
        ),
        ValidationStep(
            name="event-noise-coalescing-check",
            command=[sys.executable, str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py"), "event-noise-coalescing-check"],
            purpose="Confirm high-frequency diagnostic events are coalesced and do not create avoidable SQLite write pressure.",
        ),
        ValidationStep(
            name="codex-log-sqlite-health",
            command=[sys.executable, str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py"), "codex-log-sqlite-health"],
            purpose="Check Codex log SQLite growth and write-pressure risk without scanning raw traces.",
        ),
        ValidationStep(
            name="reply-dedupe-policy-check",
            command=[sys.executable, str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py"), "reply-dedupe-policy-check"],
            purpose="Confirm reply retry and accepted-but-unproven visibility semantics do not resend the same result repeatedly.",
        ),
        ValidationStep(
            name="cdp-route-quick-check",
            command=[sys.executable, str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py"), "cdp-route-quick-check"],
            purpose="Run a fast read-only TCP and /json/version probe for the primary visible-window CDP route.",
        ),
        ValidationStep(
            name="route-fallback-dispatch-check",
            command=[sys.executable, str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py"), "route-fallback-dispatch-check"],
            purpose="Confirm primary CDP problems do not block backup app-server dispatch candidates.",
        ),
    ]
    if profile == "full":
        # resource-layer-smoke-check is already part of the quick path.
        return quick_steps + full_only_steps[1:]
    return quick_steps


def tail_text(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def run_validation_steps(steps: list[ValidationStep], *, timeout_seconds: int) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    for step in steps:
        try:
            completed = subprocess.run(
                step.command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            results.append(
                ValidationResult(
                    name=step.name,
                    command=step.command,
                    returncode=completed.returncode,
                    passed=completed.returncode == 0,
                    timed_out=False,
                    stdout_tail=tail_text(completed.stdout.strip()),
                    stderr_tail=tail_text(completed.stderr.strip()),
                )
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            results.append(
                ValidationResult(
                    name=step.name,
                    command=step.command,
                    returncode=124,
                    passed=False,
                    timed_out=True,
                    stdout_tail=tail_text(str(stdout).strip()),
                    stderr_tail=tail_text(str(stderr).strip() or f"Timed out after {timeout_seconds}s."),
                )
            )
    return results


def build_report(limit: int, *, run_validation: bool, validation_profile: str, validation_timeout: int) -> dict[str, object]:
    findings = add_design_findings() + add_skill_findings() + add_recent_artifact_findings(limit)
    proposals = build_proposals(findings)
    steps = validation_plan(validation_profile)
    knowledge = extract_iteration_knowledge([asdict(item) for item in findings], [asdict(item) for item in steps])
    knowledge_promotion_map = build_knowledge_promotion_map(
        knowledge["knowledge_promotion_suggestions"],
        knowledge["knowledge_clusters"],
    )
    knowledge_promotion_batches = build_knowledge_promotion_batches(knowledge_promotion_map)
    promotion_readiness_summary = build_promotion_readiness_summary(knowledge_promotion_map)
    decision_summary = build_decision_summary(
        knowledge_promotion_batches=knowledge_promotion_batches,
        promotion_readiness_summary=promotion_readiness_summary,
        knowledge_promotion_map=knowledge_promotion_map,
    )
    groups = build_aligned_proposal_groups(
        build_proposal_groups(findings, knowledge_suggestions=knowledge["knowledge_promotion_suggestions"]),
        knowledge_promotion_batches=knowledge_promotion_batches,
        promotion_readiness_summary=promotion_readiness_summary,
    )
    validation_results = run_validation_steps(steps, timeout_seconds=validation_timeout) if run_validation else []
    validation_passed = all(item.passed for item in validation_results) if run_validation else None
    recommended_actions = build_recommended_actions(
        groups,
        validation_passed=validation_passed,
        knowledge_suggestions=knowledge["knowledge_promotion_suggestions"],
        knowledge_clusters=knowledge["knowledge_clusters"],
        knowledge_promotion_map=knowledge_promotion_map,
        knowledge_promotion_batches=knowledge_promotion_batches,
        promotion_readiness_summary=promotion_readiness_summary,
    )
    approval_block = build_approval_block(
        groups,
        recommended_actions,
        decision_summary,
        proposal_count=len(proposals),
        validation_passed=validation_passed,
    )
    return {
        "ok": True,
        "mode": "read_only",
        "workspace": str(ROOT),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "finding_count": len(findings),
        "findings": [asdict(item) for item in findings],
        "knowledge_candidate_count": len(knowledge["knowledge_candidates"]),
        "knowledge_cluster_count": len(knowledge["knowledge_clusters"]),
        "knowledge_candidates": knowledge["knowledge_candidates"],
        "knowledge_clusters": knowledge["knowledge_clusters"],
        "knowledge_promotion_suggestions": knowledge["knowledge_promotion_suggestions"],
        "knowledge_promotion_map": knowledge_promotion_map,
        "knowledge_promotion_batches": knowledge_promotion_batches,
        "promotion_readiness_summary": promotion_readiness_summary,
        "decision_summary": decision_summary,
        "proposal_count": len(proposals),
        "proposal_packages": [asdict(item) for item in proposals],
        "proposal_groups": [asdict(item) for item in groups],
        "recommended_next_actions": [asdict(item) for item in recommended_actions],
        "approval_block": approval_block,
        "validation_plan": [asdict(item) for item in steps],
        "validation_executed": run_validation,
        "validation_profile": validation_profile,
        "validation_timeout_seconds": validation_timeout,
        "validation_results": [asdict(item) for item in validation_results],
        "validation_passed": validation_passed,
        "safety": {
            "writes_files": False,
            "reads_sensitive_local_config_by_default": False,
            "requires_user_confirmation_for_updates": True,
        },
    }


def print_human(report: dict[str, object]) -> None:
    print(f"ok={report['ok']} mode={report['mode']} findings={report['finding_count']} proposals={report['proposal_count']}")
    print(f"workspace={report['workspace']}")
    print("safety=writes_files:false reads_sensitive_local_config_by_default:false")
    for index, finding in enumerate(report["findings"], start=1):  # type: ignore[index]
        print(f"\n[{index}] {finding['kind']} route={finding['recommended_route']} risk={finding['risk']}")
        print(f"summary: {finding['summary']}")
        print(f"evidence: {finding['evidence']}")
        print(f"proposal: {finding['proposal']}")
    print("\nknowledge candidates:")
    for candidate in report["knowledge_candidates"]:  # type: ignore[index]
        print(f"- {candidate['candidate_id']} {candidate['type']} target={candidate['promotion_target']} confidence={candidate['confidence']}")
        print(f"  summary: {candidate['summary']}")
    print("\nknowledge clusters:")
    for cluster in report["knowledge_clusters"]:  # type: ignore[index]
        print(f"- {cluster['cluster_id']} {cluster['type']} target={cluster['promotion_target']} confidence={cluster['confidence']}")
        print(f"  members: {', '.join(cluster['candidate_ids'])}")
    print("\nknowledge promotion suggestions:")
    for suggestion in report["knowledge_promotion_suggestions"]:  # type: ignore[index]
        print(f"- {suggestion['priority']} {suggestion['target']} from {suggestion['cluster_id']}")
        print(f"  reason: {suggestion['reason']}")
    print("\nknowledge promotion map:")
    for item in report["knowledge_promotion_map"]:  # type: ignore[index]
        print(f"- {item['priority']} {item['cluster_id']} -> {item['recommended_destination']}")
        print(f"  boundary: {item['promotion_boundary']}")
        print(f"  validation: {item['recommended_validation']}")
    print("\nknowledge promotion batches:")
    for batch in report["knowledge_promotion_batches"]:  # type: ignore[index]
        print(f"- {batch['batch_id']} {batch['priority']} -> {batch['recommended_destination']}")
        print(f"  focus: {batch['approval_focus']}")
        print(f"  validation: {batch['batch_validation']}")
    summary = report["promotion_readiness_summary"]  # type: ignore[index]
    print("\npromotion readiness summary:")
    print(f"- total_clusters: {summary['total_clusters']}")
    print(f"- ready_for_manual_review: {', '.join(summary['ready_for_manual_review'])}")
    print(f"- candidate_only: {', '.join(summary['candidate_only'])}")
    print(f"- needs_additional_validation: {', '.join(summary['needs_additional_validation'])}")
    decision = report["decision_summary"]  # type: ignore[index]
    print("\ndecision summary:")
    print(f"- primary_batch_id: {decision['primary_batch_id']}")
    print(f"- primary_destination: {decision['primary_destination']}")
    print(f"- summary: {decision['summary_text']}")
    print("\nproposal packages:")
    for index, proposal in enumerate(report["proposal_packages"], start=1):  # type: ignore[index]
        print(f"\n[{index}] target: {proposal['target']}")
        print(f"change: {proposal['change']}")
        print(f"reason: {proposal['reason']}")
        print(f"evidence: {proposal['evidence']}")
        print(f"risk: {proposal['risk']}")
        print(f"backup: {proposal['backup']}")
        print(f"validation: {proposal['validation']}")
        print(f"rollback: {proposal['rollback']}")
        print(f"needs_user_confirmation: {proposal['needs_user_confirmation']}")
    print("\nproposal groups:")
    for group in report["proposal_groups"]:  # type: ignore[index]
        indexes = ", ".join(str(item) for item in group["proposal_indexes"])
        print(f"\n{group['priority']} {group['name']}: proposals {indexes}")
        print(f"description: {group['description']}")
    print("\nrecommended next actions:")
    for action in report["recommended_next_actions"]:  # type: ignore[index]
        print(f"\n{action['priority']} {action['action']}")
        print(f"reason: {action['reason']}")
        print(f"validation: {action['validation']}")
    approval = report["approval_block"]  # type: ignore[index]
    print("\napproval block:")
    print(f"- status: {approval['status']}")
    print(f"- apply_mode: {approval['apply_mode']}")
    print(f"- user_decision_required: {approval['user_decision_required']}")
    print(f"- approved_by_default: {approval['approved_by_default']}")
    print(f"- primary_request: {approval['primary_request']}")
    print(f"- approval_prompt: {approval['approval_prompt']}")
    print(f"- validation_state: {approval['validation_state']}")
    print("- recommended_scope:")
    for item in approval["recommended_scope"]:  # type: ignore[index]
        print(f"  - {item['priority']} {item['action']}")
        print(f"    validation: {item['validation']}")
    print("- blocked_without_approval:")
    for item in approval["blocked_without_approval"]:  # type: ignore[index]
        print(f"  - {item}")
    print("\nvalidation plan:")
    for index, step in enumerate(report["validation_plan"], start=1):  # type: ignore[index]
        print(f"\n[{index}] {step['name']}")
        print(f"purpose: {step['purpose']}")
        print(f"command: {' '.join(step['command'])}")
        print(f"safe_default: {step['safe_default']}")
    if report["validation_executed"]:
        print(f"\nvalidation_profile={report['validation_profile']} timeout_seconds={report['validation_timeout_seconds']}")
        print("\nvalidation results:")
        for index, result in enumerate(report["validation_results"], start=1):  # type: ignore[index]
            print(f"\n[{index}] {result['name']} passed={result['passed']} returncode={result['returncode']} timed_out={result['timed_out']}")
            if result["stdout_tail"]:
                print(f"stdout_tail: {result['stdout_tail']}")
            if result["stderr_tail"]:
                print(f"stderr_tail: {result['stderr_tail']}")
        print(f"\nvalidation_passed={report['validation_passed']}")
    else:
        print("\nvalidation_executed=False")


def print_approval_only(report: dict[str, object], *, context: str = "new-proposal") -> None:
    approval = report["approval_block"]  # type: ignore[index]
    if context == "execution-result":
        print("**迭代执行结果**")
        print("状态：已按上一条已确认提案执行本轮落地/记录/验证")
        print("模式：不重复展示完整提案")
        print("说明：本轮没有新的可审批内容；如后续出现新的技能、记忆、CLI、项目知识或桥接状态候选，再生成新的“迭代提案”。")
        return

    status_labels = {
        "awaiting_user_approval": "等待用户确认",
    }
    mode_labels = {
        "proposal_only": "仅生成提案，不自动落地",
    }
    validation_labels = {
        "passed": "已通过",
        "failed": "未通过",
        "not_run": "未运行",
    }
    blocked_labels = {
        "skill updates": "写入技能",
        "memory writes": "写入长期记忆",
        "project knowledge writes": "写入项目知识",
        "_bridge CLI or maintenance-script edits": "修改 _bridge CLI 或维护脚本",
        "bridge queue, delivery, or reply-state changes": "改动桥接队列、投递状态或回复状态",
    }
    action_labels = {
        "Keep the ask-before-edit and proposal-only gates unchanged.": "保留“修改前询问”和“仅提案不自动落地”的安全边界。",
        "Review recent bridge automation files for reusable checks before editing skills.": "先审查近期桥接自动化文件里的可复用检查，再决定是否写入技能。",
        "Start with batch kpb-001 for _bridge CLI automation or skill proposal.": "优先审查 kpb-001，方向是 _bridge CLI 自动化或技能规则提案。",
        "Promote only concise, durable project facts from scripts and checkpoints.": "只推广简短、稳定、可复用的项目事实，脚本和检查点中的一次性细节不进入长期层。",
    }
    print("**迭代提案**")
    print(f"状态：{status_labels.get(str(approval['status']), approval['status'])}")
    print(f"模式：{mode_labels.get(str(approval['apply_mode']), approval['apply_mode'])}")
    print(f"默认批准：{'是' if approval['approved_by_default'] else '否'}")
    print(f"验证状态：{validation_labels.get(str(approval['validation_state']), approval['validation_state'])}")
    print("")
    print("建议处理范围：")
    for item in approval["recommended_scope"]:  # type: ignore[index]
        action = str(item["action"])
        if action.startswith("Keep cluster "):
            action = "将知识簇保持在既定推广边界内审查，避免把局部经验过早升级成全局规则。"
        print(f"- {item['priority']}：{action_labels.get(action, action)}")
    print("")
    print("未获确认前禁止：")
    for item in approval["blocked_without_approval"]:  # type: ignore[index]
        print(f"- {blocked_labels.get(str(item), item)}")
    print("")
    print("等待确认：请批准某个具体目标/动作，或要求修改提案。未获确认前，不写入技能、记忆、项目知识、CLI 文件或桥接状态。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Codex iteration-layer proposal review.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--approval-only", action="store_true", help="Emit only the final-reply approval block.")
    parser.add_argument(
        "--approval-context",
        choices=["new-proposal", "execution-result"],
        default="new-proposal",
        help="Choose whether the final-reply approval block represents a new proposal or the result of an already-approved proposal.",
    )
    parser.add_argument("--recent-limit", type=int, default=DEFAULT_RECENT_LIMIT, help="Maximum recent safe artifacts to inspect.")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Compatibility flag; this command is always read-only.")
    parser.add_argument("--run-validation", action="store_true", help="Run known safe validation checks and include their results.")
    parser.add_argument("--validation-profile", choices=["quick", "full"], default="quick", help="Validation depth. quick is bounded for routine turn-end checks; full runs the complete safe validation plan.")
    parser.add_argument("--validation-timeout", type=int, default=DEFAULT_VALIDATION_TIMEOUT_SECONDS, help="Per-validation-step timeout in seconds.")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    timeout = max(1, args.validation_timeout)
    report = build_report(
        max(0, args.recent_limit),
        run_validation=args.run_validation,
        validation_profile=args.validation_profile,
        validation_timeout=timeout,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.approval_only:
        print_approval_only(report, context=args.approval_context)
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
