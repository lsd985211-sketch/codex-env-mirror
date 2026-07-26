#!/usr/bin/env python3
"""PMB organization planning for memory governance.

Owns PMB event reads, organization-plan generation, and review-marker payloads
for memory governance. It does not delete PMB memories or rewrite the PMB event
database. Apply operations write a separate governance marker file so the
original PMB facts remain auditable and rollback is file based.
Normal callers are `memory_governance.py` facades such as `pmb_organize_plan`,
`pmb_fact_repair_plan`, and recall verification flows.
"""

from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from typing import Any

from _bridge.memory_note_analysis import drift_hits, highest_severity, normalize_memory_text, sensitive_hits
from _bridge.shared.json_cli import now_iso


MARKS_SCHEMA = "memory_governance.pmb_fact_review_marks.v1"
REPAIR_PLAN_SCHEMA = "memory_governance.pmb_fact_repair_plan.v1"
APPLY_SCHEMA = "memory_governance.pmb_fact_apply_approved.v1"


def read_review_marks(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {
            "schema": MARKS_SCHEMA,
            "ok": True,
            "generated_at": now_iso(),
            "marks": {},
            "batches": [],
        }, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"schema": MARKS_SCHEMA, "ok": False, "marks": {}, "batches": []}, f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return {"schema": MARKS_SCHEMA, "ok": False, "marks": {}, "batches": []}, "marks_root_not_object"
    marks = payload.get("marks")
    if not isinstance(marks, dict):
        payload["marks"] = {}
    batches = payload.get("batches")
    if not isinstance(batches, list):
        payload["batches"] = []
    payload.setdefault("schema", MARKS_SCHEMA)
    payload.setdefault("ok", True)
    return payload, ""


def active_marked_ulids(path: Path) -> set[str]:
    payload, error = read_review_marks(path)
    if error:
        return set()
    marks = payload.get("marks") if isinstance(payload.get("marks"), dict) else {}
    return {
        str(ulid)
        for ulid, mark in marks.items()
        if isinstance(mark, dict)
        and mark.get("active") is not False
        and str(mark.get("disposition") or "") in {"drift_prone_current_state", "sensitive_reviewed", "duplicate_reviewed"}
    }


def read_pmb_events(pmb_workspace_db: Path, limit: int = 2000) -> tuple[list[dict[str, Any]], str]:
    if not pmb_workspace_db.exists():
        return [], f"missing: {pmb_workspace_db}"
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(f"file:{pmb_workspace_db}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        columns = [str(row["name"]) for row in db.execute("PRAGMA table_info(events)").fetchall()]
        select_cols = [col for col in ("ulid", "event_type", "content", "metadata", "timestamp", "created_at") if col in columns]
        if not select_cols:
            return [], "events_table_missing_expected_columns"
        rows = db.execute(
            f"SELECT {', '.join(select_cols)} FROM events ORDER BY rowid DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    return [{key: row[key] for key in row.keys()} for row in rows], ""


def organize_plan(pmb_workspace_db: Path, limit: int = 2000, review_marks_path: Path | None = None) -> dict[str, Any]:
    rows, error = read_pmb_events(pmb_workspace_db, limit=limit)
    marked_ulids = active_marked_ulids(review_marks_path) if review_marks_path else set()
    duplicate_groups: dict[str, list[dict[str, Any]]] = {}
    sensitive_rows: list[dict[str, Any]] = []
    sensitive_policy_mentions: list[dict[str, Any]] = []
    stale_candidates: list[dict[str, Any]] = []
    drift_policy_mentions: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("ulid") or "") in marked_ulids:
            continue
        content = str(row.get("content") or "")
        norm = normalize_memory_text(content)
        if len(norm) >= 80:
            duplicate_groups.setdefault(norm, []).append(row)
        hits = sensitive_hits(content)
        if hits:
            severity = highest_severity(hits)
            target = sensitive_rows if severity in {"high", "medium"} else sensitive_policy_mentions
            target.append({"ulid": row.get("ulid"), "severity": severity, "hits": hits, "preview": " ".join(content.split())[:240]})
        drift = drift_hits(content)
        if drift:
            severity = highest_severity(drift)
            target = stale_candidates if severity in {"high", "medium"} else drift_policy_mentions
            target.append({"ulid": row.get("ulid"), "severity": severity, "hits": drift, "preview": " ".join(content.split())[:240]})
    duplicates = [
        {
            "count": len(group),
            "ulids": [str(item.get("ulid") or "") for item in group[:10]],
            "preview": " ".join(str(group[0].get("content") or "").split())[:240],
            "recommended_action": "consolidate_after_review",
        }
        for group in duplicate_groups.values()
        if len(group) > 1
    ]
    duplicates.sort(key=lambda item: -int(item["count"]))
    actions: list[dict[str, Any]] = []
    if duplicates:
        actions.append({"id": "dedupe_exact_or_near_exact_pmb_facts", "mode": "manual_approval_required", "candidate_groups": len(duplicates)})
    if stale_candidates:
        actions.append({"id": "review_drift_prone_current_state_memories", "mode": "manual_approval_required", "candidate_count": len(stale_candidates)})
    if sensitive_rows:
        actions.append({"id": "review_sensitive_memory_candidates", "mode": "manual_approval_required", "candidate_count": len(sensitive_rows)})
    return {
        "schema": "memory_governance.pmb_organize_plan.v1",
        "ok": not bool(error),
        "generated_at": now_iso(),
        "dry_run": True,
        "pmb_db": str(pmb_workspace_db),
        "read_error": error,
        "sampled_count": len(rows),
        "review_marks_path": str(review_marks_path) if review_marks_path else "",
        "suppressed_by_review_mark_count": len(marked_ulids),
        "duplicate_groups": duplicates[:25],
        "stale_or_drift_prone_candidates": stale_candidates[:25],
        "drift_policy_mentions": drift_policy_mentions[:25],
        "sensitive_candidates": sensitive_rows[:25],
        "sensitive_policy_mentions": sensitive_policy_mentions[:25],
        "actions": actions,
        "apply_policy": {
            "default_action": "no_write",
            "do_not_delete_automatically": True,
            "requires_backup_before_apply": True,
            "requires_user_approval_with_details": True,
            "safe_automatic_actions": ["index_refresh_only_after_validation"],
        },
    }


def candidate_by_ulid(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for group_name in ("stale_or_drift_prone_candidates", "sensitive_candidates"):
        for item in plan.get(group_name, []) if isinstance(plan.get(group_name), list) else []:
            if not isinstance(item, dict):
                continue
            ulid = str(item.get("ulid") or "").strip()
            if not ulid:
                continue
            candidates[ulid] = {
                "ulid": ulid,
                "category": "drift_prone_current_state" if group_name == "stale_or_drift_prone_candidates" else "sensitive_reviewed",
                "severity": item.get("severity", ""),
                "hits": item.get("hits", []),
                "preview": item.get("preview", ""),
                "recommended_action": "mark_as_reviewed_and_suppress_from_future_organize_plan",
            }
    for group in plan.get("duplicate_groups", []) if isinstance(plan.get("duplicate_groups"), list) else []:
        if not isinstance(group, dict):
            continue
        for ulid in group.get("ulids", []) if isinstance(group.get("ulids"), list) else []:
            text = str(ulid or "").strip()
            if not text or text in candidates:
                continue
            candidates[text] = {
                "ulid": text,
                "category": "duplicate_reviewed",
                "severity": "low",
                "hits": [{"code": "duplicate_group", "severity": "low"}],
                "preview": group.get("preview", ""),
                "recommended_action": "mark_as_reviewed_and_suppress_from_future_organize_plan",
            }
    return candidates


def parse_ids(value: str) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    if raw.lower() in {"all", "*", "全部"}:
        return {"all"}
    return {item.strip() for item in raw.replace(";", ",").replace("\n", ",").split(",") if item.strip()}


def fact_repair_plan(
    pmb_workspace_db: Path,
    review_marks_path: Path,
    *,
    ids: str = "",
    limit: int = 2000,
) -> dict[str, Any]:
    plan = organize_plan(pmb_workspace_db, limit=limit, review_marks_path=review_marks_path)
    candidates = candidate_by_ulid(plan)
    requested = parse_ids(ids)
    selected = [
        item
        for ulid, item in sorted(candidates.items())
        if not requested or "all" in requested or ulid in requested
    ]
    return {
        "schema": REPAIR_PLAN_SCHEMA,
        "ok": bool(plan.get("ok")),
        "generated_at": now_iso(),
        "dry_run": True,
        "pmb_db": str(pmb_workspace_db),
        "review_marks_path": str(review_marks_path),
        "requested_ids": sorted(requested),
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "selected": selected,
        "writes_pmb_db": False,
        "writes_review_marks": True,
        "apply_policy": {
            "requires_confirm_apply": True,
            "does_not_delete_or_rewrite_pmb_events": True,
            "effect": "future pmb-organize-plan suppresses reviewed ulids; original PMB recall remains auditable",
        },
        "apply_command": "python _bridge\\memory_governance.py pmb-fact-apply-approved --ids <ulid|all> --confirm-apply",
    }


def apply_fact_review_marks(
    pmb_workspace_db: Path,
    review_marks_path: Path,
    *,
    ids: str,
    limit: int = 2000,
    confirm: bool = False,
) -> dict[str, Any]:
    repair = fact_repair_plan(pmb_workspace_db, review_marks_path, ids=ids, limit=limit)
    selected = repair.get("selected", []) if isinstance(repair.get("selected"), list) else []
    payload, read_error = read_review_marks(review_marks_path)
    dry_payload = {
        "schema": APPLY_SCHEMA,
        "ok": not bool(read_error),
        "generated_at": now_iso(),
        "dry_run": not confirm,
        "requested_ids": repair.get("requested_ids", []),
        "selected_count": len(selected),
        "selected_ids": [item.get("ulid") for item in selected if isinstance(item, dict)],
        "review_marks_path": str(review_marks_path),
        "writes_pmb_db": False,
        "writes_review_marks": bool(confirm and selected),
        "read_error": read_error,
        "requires_confirm_apply": True,
    }
    if read_error:
        return dry_payload
    if not confirm:
        return {
            **dry_payload,
            "required_next_command": "python _bridge\\memory_governance.py pmb-fact-apply-approved --ids <ulid|all> --confirm-apply",
        }
    if not selected:
        return {**dry_payload, "ok": False, "reason": "no_selected_pmb_fact_candidates"}
    marks = payload.setdefault("marks", {})
    batches = payload.setdefault("batches", [])
    batch_id = now_iso().replace(":", "").replace("+", "Z")
    applied: list[dict[str, Any]] = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        ulid = str(item.get("ulid") or "").strip()
        if not ulid:
            continue
        mark = {
            "ulid": ulid,
            "active": True,
            "disposition": item.get("category") or "reviewed",
            "severity": item.get("severity") or "",
            "reviewed_at": now_iso(),
            "review_batch_id": batch_id,
            "reason": "approved PMB fact-level organization; suppress from future organize-plan without deleting PMB event",
            "hits": item.get("hits", []),
            "preview": item.get("preview", ""),
        }
        marks[ulid] = mark
        applied.append({"ulid": ulid, "disposition": mark["disposition"], "severity": mark["severity"]})
    if isinstance(batches, list):
        batches.append({"batch_id": batch_id, "applied_at": now_iso(), "applied": applied})
    payload["ok"] = True
    payload["schema"] = MARKS_SCHEMA
    payload["generated_at"] = now_iso()
    review_marks_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = review_marks_path.with_name(f"{review_marks_path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(review_marks_path)
    return {
        **dry_payload,
        "dry_run": False,
        "batch_id": batch_id,
        "applied": applied,
        "mark_count": len(marks) if isinstance(marks, dict) else 0,
    }
