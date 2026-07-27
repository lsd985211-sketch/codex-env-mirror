#!/usr/bin/env python3
"""Normalize mirror validation drift into an owner-routed read-only plan.

Ownership: convert mirror validator issues and bounded digest evidence into stable
owner decision rows.
Non-goals: modify source, projections, the mirror, memory, skills, or approval state.
State behavior: pure and read-only; no database, receipt store, or long-lived drift
state is created.
Caller context: the Codex environment mirror owner exposes this module through its
drift-plan and affected-source facades.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


PRIVATE_SOURCE_IDS = frozenset({"codex-native-memory-files"})
SKILL_SOURCE_IDS = frozenset({"codex-skills"})
PROJECTION_SOURCE_IDS = frozenset({"codex-global-agents"})
RUNTIME_SOURCE_TOKENS = ("runtime", "cache", "session", "state-db")
ALLOWED_DISPOSITIONS = {
    "source_update": frozenset({"adopt_current_authority"}),
    "projection_stale": frozenset({"projection_verified"}),
    "private_state": frozenset({"owner_export_reviewed"}),
    "runtime_only": frozenset({"recapture_from_runtime_owner"}),
}
MIRROR_CONTROL_PLANE_ISSUES = frozenset({"control_plane_static_file_drift", "governance_drift"})


def _route(
    source_id: str,
    item_id: str,
    issue_codes: set[str] | None = None,
    owner_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if issue_codes and issue_codes <= MIRROR_CONTROL_PLANE_ISSUES:
        return {
            "owner": "codex_environment_mirror",
            "classification": "source_update",
            "decision": "review_mirror_control_plane_change",
            "decision_status": "pending_review",
            "required_receipt": "clean mirror Git HEAD plus successful mirror owner plan",
            "blocker": True,
            "next_action": "review the committed mirror control-plane change through the mirror owner before refresh",
        }
    if source_id in PRIVATE_SOURCE_IDS:
        return {
            "owner": "memory_governance",
            "classification": "private_state",
            "decision": "owner_private_restore_review",
            "decision_status": "blocked",
            "required_receipt": "memory_governance owner review and private restore disposition",
            "blocker": True,
            "next_action": "review through memory_governance; keep private memory outside public mirror publication",
        }
    if source_id in SKILL_SOURCE_IDS:
        return {
            "owner": "skill_lifecycle_governance",
            "classification": "source_update",
            "decision": "review_source_change",
            "decision_status": "pending_review",
            "required_receipt": "skill lifecycle audit and validated source disposition",
            "blocker": True,
            "next_action": "review the Work Git skill change through skill_lifecycle_governance before one-way projection",
        }
    if source_id in PROJECTION_SOURCE_IDS:
        return {
            "owner": "wsl_workspace_owner",
            "classification": "projection_stale",
            "decision": "reproject_from_work_git",
            "decision_status": "pending_approval",
            "required_receipt": "rule_governance validation plus hash-verified host projection receipt",
            "blocker": True,
            "next_action": "after source review, request bounded wsl_workspace_owner host projection and validate rule_governance",
        }
    hinted_owner = str((owner_hint or {}).get("owner") or "").strip()
    hinted_kind = str((owner_hint or {}).get("kind") or "").strip()
    if hinted_owner:
        runtime_only = hinted_owner == "runtime" or hinted_kind == "runtime_versions"
        return {
            "owner": hinted_owner,
            "classification": "runtime_only" if runtime_only else "source_update",
            "decision": (
                "exclude_or_restore_through_runtime_owner"
                if runtime_only
                else "review_source_change"
            ),
            "decision_status": "blocked" if runtime_only else "pending_review",
            "required_receipt": f"{hinted_owner} reviewed disposition and validation receipt",
            "blocker": True,
            "next_action": (
                f"review through {hinted_owner}; keep ambient runtime state outside declarative source"
                if runtime_only
                else f"review the declared source or generated-source drift through {hinted_owner} before refresh"
            ),
        }
    lowered = f"{source_id}:{item_id}".lower()
    if any(token in lowered for token in RUNTIME_SOURCE_TOKENS):
        return {
            "owner": "runtime_owner_required",
            "classification": "runtime_only",
            "decision": "exclude_or_restore_through_runtime_owner",
            "decision_status": "blocked",
            "required_receipt": "named runtime owner disposition",
            "blocker": True,
            "next_action": "identify the runtime owner and keep ambient runtime state outside declarative source",
        }
    return {
        "owner": "unresolved",
        "classification": "unknown",
        "decision": "block_pending_owner_attribution",
        "decision_status": "blocked",
        "required_receipt": "owner attribution and reviewed disposition",
        "blocker": True,
        "next_action": "attribute an existing authority owner before refresh; do not absorb ambient state",
    }


def _digest_projection(evidence: dict[str, Any]) -> dict[str, str]:
    return {
        "work_git": str(evidence.get("work_git_digest") or ""),
        "approved_derived": str(evidence.get("approved_derived_digest") or ""),
        "current_projection": str(evidence.get("current_projection_digest") or ""),
    }


def build_drift_plan(
    issues: list[dict[str, Any]],
    *,
    evidence: dict[str, dict[str, Any]] | None = None,
    owner_hints: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return stable owner rows; successful planning never implies refresh approval."""
    evidence_by_id = evidence or {}
    hints_by_id = owner_hints or {}
    rows_by_id: dict[str, dict[str, Any]] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "unknown_issue")
        source_id = str(issue.get("source_id") or issue.get("asset_id") or "unknown")
        samples = [str(item) for item in issue.get("sample", []) if str(item).strip()]
        if not samples:
            samples = [source_id]
        for item_id in samples:
            item_evidence = evidence_by_id.get(item_id) or evidence_by_id.get(source_id) or {}
            route = _route(source_id, item_id, {code}, hints_by_id.get(source_id))
            row = {
                "item_id": item_id,
                "path": str(item_evidence.get("path") or item_id.partition(":")[2] or item_id),
                "source_id": source_id,
                "issue_codes": [code],
                "reported_count": int(issue.get("count") or len(samples)),
                "digests": _digest_projection(item_evidence),
                "evidence_refs": sorted({str(ref) for ref in item_evidence.get("refs", []) if str(ref)}),
                **route,
            }
            existing = rows_by_id.get(item_id)
            if existing:
                existing["issue_codes"] = sorted(set(existing["issue_codes"] + [code]))
                existing["reported_count"] = max(existing["reported_count"], row["reported_count"])
                combined_codes = set(existing["issue_codes"])
                if combined_codes <= MIRROR_CONTROL_PLANE_ISSUES:
                    existing.update(_route(source_id, item_id, combined_codes, hints_by_id.get(source_id)))
            else:
                rows_by_id[item_id] = row
    rows = [rows_by_id[key] for key in sorted(rows_by_id)]
    digest_rows = json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    blockers = [
        {"item_id": row["item_id"], "classification": row["classification"], "owner": row["owner"]}
        for row in rows
        if row["blocker"]
    ]
    return {
        "schema": "codex_environment_mirror.drift_plan.v1",
        "ok": True,
        "read_only": True,
        "refresh_allowed": not blockers,
        "row_count": len(rows),
        "blocker_count": len(blockers),
        "plan_digest": hashlib.sha256(digest_rows.encode("utf-8")).hexdigest(),
        "rows": rows,
        "blockers": blockers,
        "next_action": (
            "consume every owner receipt and rerun a force-fresh drift plan"
            if blockers
            else "all reported rows are resolved; caller must still enforce source stability and fresh validation gates"
        ),
    }


def apply_reviewed_dispositions(
    plan: dict[str, Any],
    *,
    decisions: dict[str, str] | None = None,
    owner_receipts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Consume explicit review decisions without weakening fail-closed classes."""
    selected = decisions or {}
    receipts = owner_receipts or {}
    reviewed_rows: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    for original in plan.get("rows", []):
        if not isinstance(original, dict):
            continue
        row = dict(original)
        item_id = str(row.get("item_id") or "")
        source_id = str(row.get("source_id") or "")
        classification = str(row.get("classification") or "unknown")
        owner = str(row.get("owner") or "unresolved")
        disposition = str(
            selected.get(f"item:{item_id}")
            or selected.get(f"source:{source_id}")
            or selected.get(f"class:{classification}")
            or ""
        )
        owner_receipt = str(receipts.get(owner) or "")
        allowed = ALLOWED_DISPOSITIONS.get(classification, frozenset())
        resolved = bool(
            disposition in allowed
            and owner not in {"", "unresolved", "runtime_owner_required"}
            and owner_receipt
        )
        row.update({
            "review_disposition": disposition,
            "owner_receipt_ref": owner_receipt,
            "decision_status": "consumed" if resolved else str(row.get("decision_status") or "blocked"),
            "blocker": not resolved,
        })
        if not resolved:
            reason = (
                "classification_requires_owner_attribution"
                if classification == "unknown"
                else "owner_receipt_required"
                if disposition in allowed and not owner_receipt
                else "reviewed_disposition_required"
            )
            blockers.append({"item_id": item_id, "classification": classification, "owner": owner, "reason": reason})
        reviewed_rows.append(row)
    return {
        **plan,
        "rows": reviewed_rows,
        "blockers": blockers,
        "blocker_count": len(blockers),
        "refresh_allowed": bool(reviewed_rows) and not blockers,
        "review_consumed": not blockers,
        "next_action": (
            "all current drift rows have signature-bound owner dispositions"
            if not blockers
            else "supply an allowed disposition and current owner receipt for every blocker"
        ),
    }
