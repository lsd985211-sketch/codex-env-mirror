#!/usr/bin/env python3
"""Owner implementation for approved iteration candidates targeting memory.

Ownership: bounded dry-run/apply/readback for verified iteration conclusions in
the memory absorption index.
Non-goals: approving queue items, choosing non-memory owners, editing skills or
rules, or accepting arbitrary destination paths from candidate content.
State behavior: read-only by default; apply requires explicit confirmation and
uses the shared backup router before modifying an existing index.
Caller context: thin ``memory_governance`` facade and workflow iteration owner.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_note_analysis import highest_severity, sensitive_hits
from shared.backup_router import create_backup
from workflow_iteration_capture import verify_candidate_identity


DEFAULT_MEMORY_INDEX = Path.home() / "Desktop" / "Codex资源库" / "memory" / "governance" / "memory_absorption_index.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_id(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or candidate.get("source_item_id") or "").strip().lower()


def _privacy(candidate: dict[str, Any]) -> dict[str, Any]:
    hits = sensitive_hits(str(candidate.get("summary") or ""))
    return {
        "hits": hits,
        "severity": highest_severity(hits),
        "blocked": highest_severity(hits) in {"high", "medium"},
    }


def plan_iteration_candidate(
    candidate: dict[str, Any],
    *,
    index_path: Path = DEFAULT_MEMORY_INDEX,
) -> dict[str, Any]:
    candidate_id = _candidate_id(candidate)
    namespace = str(candidate.get("target_namespace") or candidate.get("proposed_destination_namespace") or "")
    privacy = _privacy(candidate)
    identity_ok = verify_candidate_identity(candidate)
    namespace_ok = namespace.startswith("memory.")
    ok = bool(candidate_id and identity_ok and namespace_ok and not privacy["blocked"])
    return {
        "schema": "memory_iteration_owner.plan.v1",
        "ok": ok,
        "dry_run": True,
        "candidate_id": candidate_id,
        "owner": "memory_governance",
        "index_path": str(index_path),
        "target_namespace": namespace,
        "identity_ok": identity_ok,
        "namespace_ok": namespace_ok,
        "privacy": privacy,
        "requires_explicit_confirmation": True,
        "writes_files": False,
        "reason": "ready_for_explicit_apply" if ok else "candidate_failed_owner_preconditions",
    }


def _read_index(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {"schema": "memory_absorption_index.v1", "iteration_candidates": []}, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return {}, "memory_index_not_object"
    return payload, ""


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    temp_path.replace(path)


def apply_iteration_candidate(
    candidate: dict[str, Any],
    *,
    confirm: bool = False,
    index_path: Path = DEFAULT_MEMORY_INDEX,
    backup: bool = True,
) -> dict[str, Any]:
    plan = plan_iteration_candidate(candidate, index_path=index_path)
    if not confirm:
        return plan
    if not backup and index_path.resolve() == DEFAULT_MEMORY_INDEX.resolve():
        return {
            **plan,
            "dry_run": False,
            "ok": False,
            "reason": "production_memory_backup_cannot_be_disabled",
        }
    if not plan["ok"]:
        return {**plan, "dry_run": False, "ok": False, "reason": "owner_preconditions_failed"}
    payload, error = _read_index(index_path)
    if error:
        return {**plan, "dry_run": False, "ok": False, "reason": "memory_index_unreadable", "error": error}
    entries = payload.setdefault("iteration_candidates", [])
    if not isinstance(entries, list):
        return {**plan, "dry_run": False, "ok": False, "reason": "iteration_candidates_not_list"}
    candidate_id = str(plan["candidate_id"])
    existing = next((item for item in entries if isinstance(item, dict) and item.get("candidate_id") == candidate_id), None)
    if existing is not None:
        return {
            **plan,
            "dry_run": False,
            "ok": True,
            "already_applied": True,
            "applied_item": existing,
            "backup": {"ok": True, "skipped": "idempotent_existing_candidate"},
        }
    backup_result: dict[str, Any] = {"ok": True, "skipped": "new_index_or_test_override"}
    if backup and index_path.exists():
        backup_result = create_backup(
            [str(index_path)],
            remark=f"iteration-candidate-{candidate_id.split(':', 1)[-1]}",
            purpose="approved iteration candidate memory apply",
            category="memory",
            trigger="memory_iteration_owner",
        )
        if not backup_result.get("ok"):
            return {**plan, "dry_run": False, "ok": False, "reason": "backup_failed", "backup": backup_result}
    applied_item = {
        "candidate_id": candidate_id,
        "text": str(candidate.get("summary") or ""),
        "source_checkpoint": str(candidate.get("source_checkpoint") or ""),
        "stable_conclusion": str(candidate.get("stable_conclusion") or candidate.get("summary") or ""),
        "target_namespace": str(candidate.get("target_namespace") or candidate.get("proposed_destination_namespace") or ""),
        "affected_system": str(candidate.get("affected_system") or ""),
        "status": "approved_applied",
        "applied_at": now_iso(),
        "source_owner": "workflow_iteration_owner",
    }
    entries.append(applied_item)
    payload["generated_at"] = now_iso()
    _write_json_atomic(index_path, payload)
    return {
        **plan,
        "dry_run": False,
        "ok": True,
        "already_applied": False,
        "applied_item": applied_item,
        "backup": backup_result,
        "write_receipt": {"path": str(index_path), "candidate_id": candidate_id},
    }


def apply_iteration_candidates(
    candidates: list[dict[str, Any]],
    *,
    confirm: bool = False,
    index_path: Path = DEFAULT_MEMORY_INDEX,
    backup: bool = True,
) -> dict[str, Any]:
    """Apply an approved batch with one backup and one atomic index write."""

    rows = [candidate for candidate in candidates if isinstance(candidate, dict)]
    plans = [plan_iteration_candidate(candidate, index_path=index_path) for candidate in rows]
    candidate_ids = [str(plan.get("candidate_id") or "") for plan in plans]
    unique_ids = list(dict.fromkeys(candidate_ids))
    issues = [
        {"candidate_id": plan.get("candidate_id"), "reason": plan.get("reason"), "privacy": plan.get("privacy")}
        for plan in plans
        if not plan.get("ok")
    ]
    if not rows or len(unique_ids) != len(rows):
        issues.append({"reason": "empty_or_duplicate_candidate_ids"})
    dry_run = {
        "schema": "memory_iteration_owner.batch_plan.v1",
        "ok": not issues,
        "dry_run": True,
        "owner": "memory_governance",
        "index_path": str(index_path),
        "candidate_count": len(rows),
        "candidate_ids": unique_ids,
        "issues": issues,
        "requires_explicit_confirmation": True,
        "writes_files": False,
    }
    if not confirm:
        return dry_run
    if not backup and index_path.resolve() == DEFAULT_MEMORY_INDEX.resolve():
        return {
            **dry_run,
            "dry_run": False,
            "ok": False,
            "reason": "production_memory_backup_cannot_be_disabled",
        }
    if issues:
        return {**dry_run, "dry_run": False, "ok": False, "reason": "owner_preconditions_failed"}

    payload, error = _read_index(index_path)
    if error:
        return {**dry_run, "dry_run": False, "ok": False, "reason": "memory_index_unreadable", "error": error}
    entries = payload.setdefault("iteration_candidates", [])
    if not isinstance(entries, list):
        return {**dry_run, "dry_run": False, "ok": False, "reason": "iteration_candidates_not_list"}
    existing_ids = {
        str(item.get("candidate_id") or "")
        for item in entries
        if isinstance(item, dict) and item.get("candidate_id")
    }
    new_rows = [candidate for candidate in rows if _candidate_id(candidate) not in existing_ids]
    backup_result: dict[str, Any] = {"ok": True, "skipped": "idempotent_or_new_index"}
    if new_rows and backup and index_path.exists():
        backup_result = create_backup(
            [str(index_path)],
            remark=f"iteration-candidate-batch-{len(new_rows)}",
            purpose="approved iteration candidate batch memory apply",
            category="memory",
            trigger="memory_iteration_owner",
        )
        if not backup_result.get("ok"):
            return {**dry_run, "dry_run": False, "ok": False, "reason": "backup_failed", "backup": backup_result}
    applied_at = now_iso()
    applied_items = [
        {
            "candidate_id": _candidate_id(candidate),
            "text": str(candidate.get("summary") or ""),
            "source_checkpoint": str(candidate.get("source_checkpoint") or ""),
            "stable_conclusion": str(candidate.get("stable_conclusion") or candidate.get("summary") or ""),
            "target_namespace": str(candidate.get("target_namespace") or candidate.get("proposed_destination_namespace") or ""),
            "affected_system": str(candidate.get("affected_system") or ""),
            "status": "approved_applied",
            "applied_at": applied_at,
            "source_owner": "workflow_iteration_owner",
        }
        for candidate in new_rows
    ]
    if applied_items:
        entries.extend(applied_items)
        payload["generated_at"] = applied_at
        _write_json_atomic(index_path, payload)
    return {
        **dry_run,
        "schema": "memory_iteration_owner.batch_apply.v1",
        "dry_run": False,
        "ok": True,
        "writes_files": bool(applied_items),
        "applied_count": len(applied_items),
        "already_applied_count": len(rows) - len(applied_items),
        "applied_ids": [item["candidate_id"] for item in applied_items],
        "backup": backup_result,
        "write_receipt": {"path": str(index_path), "candidate_count": len(rows)},
    }


def recall_iteration_candidate(
    candidate_id: str,
    *,
    index_path: Path = DEFAULT_MEMORY_INDEX,
) -> dict[str, Any]:
    payload, error = _read_index(index_path)
    if error:
        return {"ok": False, "reason": "memory_index_unreadable", "error": error, "path": str(index_path)}
    entries = payload.get("iteration_candidates") if isinstance(payload.get("iteration_candidates"), list) else []
    item = next((entry for entry in entries if isinstance(entry, dict) and entry.get("candidate_id") == candidate_id), None)
    return {
        "schema": "memory_iteration_owner.recall.v1",
        "ok": item is not None,
        "candidate_id": candidate_id,
        "path": str(index_path),
        "item": item or {},
        "reason": "found" if item is not None else "candidate_not_found",
    }


def validate_iteration_candidate(
    candidate: dict[str, Any],
    *,
    index_path: Path = DEFAULT_MEMORY_INDEX,
) -> dict[str, Any]:
    candidate_id = _candidate_id(candidate)
    recalled = recall_iteration_candidate(candidate_id, index_path=index_path)
    item = recalled.get("item") if isinstance(recalled.get("item"), dict) else {}
    identity_ok = verify_candidate_identity(candidate)
    content_ok = bool(item) and item.get("text") == str(candidate.get("summary") or "")
    ok = bool(recalled.get("ok") and identity_ok and content_ok)
    return {
        "schema": "memory_iteration_owner.validate.v1",
        "ok": ok,
        "candidate_id": candidate_id,
        "identity_ok": identity_ok,
        "content_ok": content_ok,
        "recall": recalled,
        "validation_receipt": {
            "owner": "memory_governance",
            "candidate_id": candidate_id,
            "readback_ok": ok,
            "path": str(index_path),
        },
    }
