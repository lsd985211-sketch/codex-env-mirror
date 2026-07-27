#!/usr/bin/env python3
"""Deterministic hygiene planner for the workflow review backlog.

Ownership: exact semantic duplicate detection, evidence-only candidate
classification, bounded backlog cohorts, and confirmed reconciliation plans.
Non-goals: approve durable lessons, judge proposal value, write memory, or
replace workflow_review_queue as the state authority.
State behavior: plan is read-only; reconcile delegates one atomic terminal
transition batch to workflow_review_queue after an exact confirmation token.
Caller context: workflow_review_queue backlog-plan/backlog-reconcile and
maintenance closeout diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from workflow_review_queue import QUEUE_PATH, bulk_discard_pending, connect, snapshot


CONFIRM = "RECONCILE-REVIEW-BACKLOG"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def semantic_key(item: dict[str, Any]) -> str:
    identity = {
        "text": _clean(item.get("summary")),
        "stable_conclusion": _clean(item.get("stable_conclusion") or item.get("summary")),
        "target_namespace": _clean(
            item.get("target_namespace") or item.get("proposed_destination_namespace")
        ),
        "affected_system": _clean(item.get("affected_system")),
    }
    if not identity["stable_conclusion"]:
        return ""
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _pending_rows(db_path: Path) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT review_id,kind,item_json,created_at,updated_at FROM review_items "
            "WHERE status='pending' ORDER BY created_at,review_id"
        ).fetchall()
    finally:
        conn.close()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            item = json.loads(str(row["item_json"]))
        except json.JSONDecodeError:
            item = {}
        result.append({
            "review_id": str(row["review_id"]),
            "kind": str(row["kind"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "item": item,
        })
    return result


def _signal_kind(row: dict[str, Any]) -> str:
    attributes = row["item"].get("attributes") if isinstance(row["item"].get("attributes"), dict) else {}
    return _clean(attributes.get("signal_kind"))


def _owner(row: dict[str, Any]) -> str:
    attributes = row["item"].get("attributes") if isinstance(row["item"].get("attributes"), dict) else {}
    return _clean(attributes.get("owner")) or "unmapped"


def classify(rows: list[dict[str, Any]], *, limit: int = 20) -> dict[str, Any]:
    duplicates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["kind"] != "iteration_candidates":
            continue
        key = semantic_key(row["item"])
        if key:
            duplicates[key].append(row)

    duplicate_groups: list[dict[str, Any]] = []
    duplicate_dispositions: dict[str, str] = {}
    for key, group in sorted(duplicates.items()):
        if len(group) < 2:
            continue
        canonical = group[0]
        duplicate_ids = [row["review_id"] for row in group[1:]]
        for review_id in duplicate_ids:
            duplicate_dispositions[review_id] = (
                f"auto_consolidated: exact semantic duplicate of {canonical['review_id']}"
            )
        duplicate_groups.append({
            "semantic_key": key,
            "canonical_review_id": canonical["review_id"],
            "duplicate_review_ids": duplicate_ids,
            "summary": _clean(canonical["item"].get("summary")),
        })

    evidence_only = [
        row for row in rows
        if row["kind"] == "iteration_candidates" and _signal_kind(row) == "regression_test"
    ]
    evidence_dispositions = {
        row["review_id"]: (
            "auto_discarded: regression result remains checkpoint verification evidence and is not a durable lesson"
        )
        for row in evidence_only
    }
    machine_dispositions = {**duplicate_dispositions, **evidence_dispositions}

    cohort_counts = Counter(
        (row["kind"], _owner(row), _signal_kind(row) or "unspecified")
        for row in rows
        if row["review_id"] not in machine_dispositions
    )
    cohorts = [
        {"kind": kind, "owner": owner, "signal_kind": signal, "count": count}
        for (kind, owner, signal), count in sorted(
            cohort_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    review_required = [row for row in rows if row["review_id"] not in machine_dispositions]
    bounded = max(1, int(limit))
    return {
        "schema": "workflow_review_backlog.plan.v1",
        "ok": True,
        "pending_count": len(rows),
        "machine_disposition_count": len(machine_dispositions),
        "approval_required_count": len(review_required),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_groups": duplicate_groups[:bounded],
        "evidence_only_count": len(evidence_only),
        "cohorts": cohorts[:bounded],
        "review_sample": [
            {
                "review_id": row["review_id"],
                "kind": row["kind"],
                "owner": _owner(row),
                "signal_kind": _signal_kind(row) or "unspecified",
                "title": _clean(row["item"].get("title")),
                "summary": _clean(row["item"].get("summary"))[:500],
                "approval_action": _clean(row["item"].get("approval_action")),
            }
            for row in review_required[:bounded]
        ],
        "machine_dispositions": machine_dispositions,
        "confirmation": CONFIRM,
        "contracts": {
            "state_owner": "workflow_review_queue",
            "approval_inferred": False,
            "durable_items_auto_approved": False,
            "exact_semantic_duplicates_only": True,
            "regression_results_remain_checkpoint_evidence": True,
        },
    }


def plan(*, db_path: Path = QUEUE_PATH, limit: int = 20) -> dict[str, Any]:
    return classify(_pending_rows(db_path), limit=limit)


def reconcile(
    *,
    confirm: str,
    db_path: Path = QUEUE_PATH,
    limit: int = 20,
) -> dict[str, Any]:
    before = plan(db_path=db_path, limit=limit)
    if confirm != CONFIRM:
        return {
            "schema": "workflow_review_backlog.reconcile.v1",
            "ok": False,
            "applied": False,
            "reason": "confirmation_required",
            "confirmation": CONFIRM,
            "plan": before,
        }
    operation = bulk_discard_pending(before["machine_dispositions"], db_path=db_path)
    after = plan(db_path=db_path, limit=limit)
    return {
        "schema": "workflow_review_backlog.reconcile.v1",
        "ok": bool(operation.get("ok")),
        "applied": bool(operation.get("discarded_count")),
        "operation": operation,
        "before": {key: before[key] for key in ("pending_count", "machine_disposition_count", "approval_required_count")},
        "after": {key: after[key] for key in ("pending_count", "machine_disposition_count", "approval_required_count")},
        "queue": snapshot(db_path=db_path).get("counts", {}),
    }


def validate() -> dict[str, Any]:
    first = semantic_key({
        "stable_conclusion": "Keep one authority.",
        "target_namespace": "memory.project_conclusions",
        "affected_system": "workflow",
        "source_checkpoint": "one",
    })
    second = semantic_key({
        "stable_conclusion": "Keep one authority.",
        "target_namespace": "memory.project_conclusions",
        "affected_system": "workflow",
        "source_checkpoint": "two",
    })
    return {
        "schema": "workflow_review_backlog.validate.v1",
        "ok": bool(first and first == second),
        "semantic_identity_ignores_evidence_location": first == second,
        "confirmation": CONFIRM,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Workflow review backlog hygiene owner")
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--limit", type=int, default=20)
    reconcile_parser = sub.add_parser("reconcile")
    reconcile_parser.add_argument("--confirm", default="")
    reconcile_parser.add_argument("--limit", type=int, default=20)
    sub.add_parser("validate")
    args = parser.parse_args()
    if args.command == "plan":
        payload = plan(limit=args.limit)
    elif args.command == "reconcile":
        payload = reconcile(confirm=args.confirm, limit=args.limit)
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
