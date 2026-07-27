#!/usr/bin/env python3
"""Coordinate approved iteration candidates through explicit target owners.

Ownership: exact-candidate queue readback, owner allowlisting, guarded lifecycle
transitions, and acceptance-receipt consumption.
Non-goals: deriving approval, executing arbitrary candidate-provided commands,
or writing owner state directly.
State behavior: plan is read-only; apply/validate/resolve require the expected
queue status and use the selected owner before advancing lifecycle state.
Caller context: operator CLI, closeout follow-up, and synthetic end-to-end tests.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from memory_governance import (
    ITERATION_MEMORY_INDEX as DEFAULT_MEMORY_INDEX,
    iteration_candidate_apply,
    iteration_candidate_recall,
    iteration_candidate_validate,
    iteration_candidates_apply,
)
from workflow_iteration_capture import owner_for_namespace, verify_candidate_identity
from workflow_review_queue import QUEUE_PATH, get_review_item, transition


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
STATIC_OWNER_PLANS = {
    "project_checkpoint_finalize": [sys.executable, str(BRIDGE / "project_checkpoint_finalize.py"), "--help"],
    "skill_owner": [sys.executable, str(BRIDGE / "skill_orchestrator.py"), "validate"],
    "rule_governance": [sys.executable, str(BRIDGE / "rule_governance.py"), "validate"],
    "maintenance_owner": [sys.executable, str(BRIDGE / "codex_workflow_entry.py"), "maintenance", "catalog"],
    "system_membership": [sys.executable, str(BRIDGE / "system_membership.py"), "validate"],
}


def _load_candidate(review_id: str, *, db_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    record = get_review_item(review_id, db_path=db_path)
    if not record.get("ok"):
        return {}, record
    item = record.get("item") if isinstance(record.get("item"), dict) else {}
    if record.get("kind") != "iteration_candidates":
        return {}, {"ok": False, "reason": "review_item_kind_not_iteration_candidate", "record": record}
    if not verify_candidate_identity(item):
        return {}, {"ok": False, "reason": "candidate_identity_mismatch", "record": record}
    return item, record


def owner_plan(candidate: dict[str, Any]) -> dict[str, Any]:
    namespace = str(candidate.get("target_namespace") or candidate.get("proposed_destination_namespace") or "")
    owner = owner_for_namespace(namespace)
    if not owner:
        return {
            "ok": False,
            "reason": "unmapped_target_namespace",
            "target_namespace": namespace,
            "allowed_prefixes": ["memory.", "project_checkpoint.", "skills.", "rules.", "maintenance.", "system_membership."],
        }
    if owner == "memory_governance":
        command = [
            sys.executable,
            str(BRIDGE / "memory_governance.py"),
            "iteration-candidate-plan",
            "--ids",
            str(candidate.get("candidate_id") or ""),
        ]
    else:
        command = list(STATIC_OWNER_PLANS[owner])
    return {
        "ok": True,
        "owner": owner,
        "target_namespace": namespace,
        "command": command,
        "command_is_static_allowlisted": True,
        "candidate_text_used_as_command": False,
        "writes_targets": False,
    }


def process_candidate(
    review_id: str,
    *,
    action: str,
    confirm: bool = False,
    db_path: Path = QUEUE_PATH,
    memory_index_path: Path = DEFAULT_MEMORY_INDEX,
    backup: bool = True,
) -> dict[str, Any]:
    action = str(action or "").strip().lower()
    candidate, record = _load_candidate(review_id, db_path=db_path)
    if not candidate:
        return record
    status = str(record.get("status") or "")
    plan = owner_plan(candidate)
    if not plan.get("ok"):
        return {**plan, "review_id": review_id, "status": status}
    owner = str(plan["owner"])
    if action == "plan":
        if status != "approved":
            return {"ok": False, "reason": "candidate_not_approved", "status": status, "review_id": review_id}
        return {**plan, "review_id": review_id, "status": status, "dry_run": True}
    if action == "apply":
        if status != "approved":
            return {"ok": False, "reason": "candidate_not_approved", "status": status, "review_id": review_id}
        if owner != "memory_governance":
            return {
                **plan,
                "review_id": review_id,
                "status": status,
                "dry_run": True,
                "reason": "owner_apply_not_implemented_keep_approved",
            }
        owner_result = iteration_candidate_apply(
            candidate,
            confirm=confirm,
            index_path=memory_index_path,
            backup=backup,
        )
        if not confirm:
            return {**owner_result, "review_id": review_id, "queue_status": status}
        if not owner_result.get("ok"):
            return {**owner_result, "review_id": review_id, "queue_status": status}
        moved = transition(review_id, "applied", note="owner_apply_receipt_ok", db_path=db_path)
        return {
            "ok": bool(moved.get("ok")),
            "dry_run": False,
            "review_id": review_id,
            "owner_result": owner_result,
            "queue_transition": moved,
        }
    if action == "validate":
        if status != "applied":
            return {"ok": False, "reason": "candidate_not_applied", "status": status, "review_id": review_id}
        if owner != "memory_governance":
            return {**plan, "review_id": review_id, "status": status, "reason": "owner_validation_not_implemented"}
        owner_result = iteration_candidate_validate(candidate, index_path=memory_index_path)
        if not owner_result.get("ok"):
            return {"ok": False, "reason": "owner_validation_failed", "review_id": review_id, "owner_result": owner_result}
        moved = transition(review_id, "validated", note="owner_readback_and_recall_ok", db_path=db_path)
        return {"ok": bool(moved.get("ok")), "review_id": review_id, "owner_result": owner_result, "queue_transition": moved}
    if action == "resolve":
        if status != "validated":
            return {"ok": False, "reason": "candidate_not_validated", "status": status, "review_id": review_id}
        moved = transition(review_id, "resolved", note="validated_owner_receipt_consumed", db_path=db_path)
        return {"ok": bool(moved.get("ok")), "review_id": review_id, "queue_transition": moved}
    return {"ok": False, "reason": "invalid_action", "allowed": ["plan", "apply", "validate", "resolve"]}


def process_candidates(
    review_ids: list[str],
    *,
    action: str,
    confirm: bool = False,
    db_path: Path = QUEUE_PATH,
    memory_index_path: Path = DEFAULT_MEMORY_INDEX,
    backup: bool = True,
) -> dict[str, Any]:
    """Process a reviewed memory-candidate batch without weakening lifecycle gates."""

    ids = list(dict.fromkeys(str(value or "").strip().lower() for value in review_ids if str(value or "").strip()))
    if not ids:
        return {"ok": False, "reason": "no_review_ids"}
    loaded = [_load_candidate(review_id, db_path=db_path) for review_id in ids]
    failures = [record for candidate, record in loaded if not candidate]
    if failures:
        return {"ok": False, "reason": "candidate_load_failed", "failures": failures}
    candidates = [candidate for candidate, _ in loaded]
    records = [record for _, record in loaded]
    statuses = {str(record.get("status") or "") for record in records}
    action = str(action or "").strip().lower()

    allowed_statuses = {
        "plan": {"approved"},
        "apply": {"approved", "applied"},
        "validate": {"applied", "validated"},
        "resolve": {"validated", "resolved"},
    }
    if action not in allowed_statuses:
        return {"ok": False, "reason": "invalid_action", "allowed": sorted(allowed_statuses)}
    unexpected = sorted(statuses - allowed_statuses[action])
    if unexpected:
        return {
            "ok": False,
            "reason": "candidate_batch_status_mismatch",
            "action": action,
            "statuses": sorted(statuses),
            "allowed_statuses": sorted(allowed_statuses[action]),
        }
    plans = [owner_plan(candidate) for candidate in candidates]
    if not all(plan.get("ok") and plan.get("owner") == "memory_governance" for plan in plans):
        return {"ok": False, "reason": "batch_owner_not_uniform_memory_governance", "plans": plans}
    if action == "plan":
        return {
            "ok": True,
            "dry_run": True,
            "action": action,
            "review_count": len(ids),
            "review_ids": ids,
            "owner": "memory_governance",
        }
    if action == "apply":
        owner_result = iteration_candidates_apply(
            candidates,
            confirm=confirm,
            index_path=memory_index_path,
            backup=backup,
        )
        if not confirm or not owner_result.get("ok"):
            return {**owner_result, "review_count": len(ids), "queue_statuses": sorted(statuses)}
        transitions = [
            transition(review_id, "applied", note="batch_owner_apply_receipt_ok", db_path=db_path)
            for review_id, record in zip(ids, records, strict=True)
            if record.get("status") == "approved"
        ]
    elif action == "validate":
        validations = [iteration_candidate_validate(candidate, index_path=memory_index_path) for candidate in candidates]
        if not all(item.get("ok") for item in validations):
            return {"ok": False, "reason": "batch_owner_validation_failed", "validations": validations}
        owner_result = {"ok": True, "validation_count": len(validations)}
        transitions = [
            transition(review_id, "validated", note="batch_owner_readback_ok", db_path=db_path)
            for review_id, record in zip(ids, records, strict=True)
            if record.get("status") == "applied"
        ]
    else:
        owner_result = {"ok": True, "resolved_count": len(ids)}
        transitions = [
            transition(review_id, "resolved", note="batch_validated_receipt_consumed", db_path=db_path)
            for review_id, record in zip(ids, records, strict=True)
            if record.get("status") == "validated"
        ]
    transition_failures = [item for item in transitions if not item.get("ok")]
    return {
        "ok": not transition_failures,
        "action": action,
        "review_count": len(ids),
        "owner_result": owner_result,
        "transition_count": len(transitions),
        "transition_failures": transition_failures,
    }


def recall_candidate(
    candidate_id: str,
    *,
    memory_index_path: Path = DEFAULT_MEMORY_INDEX,
) -> dict[str, Any]:
    return iteration_candidate_recall(candidate_id, index_path=memory_index_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Approved iteration candidate owner coordinator")
    parser.add_argument("action", choices=["plan", "apply", "validate", "resolve", "recall"])
    parser.add_argument("--review-id", action="append", required=True)
    parser.add_argument("--confirm-apply", action="store_true")
    args = parser.parse_args()
    review_ids = [item for value in args.review_id for item in str(value).split(",") if item.strip()]
    if args.action == "recall":
        payload = recall_candidate(review_ids[0]) if len(review_ids) == 1 else {"ok": False, "reason": "recall_requires_one_review_id"}
    elif len(review_ids) > 1:
        payload = process_candidates(review_ids, action=args.action, confirm=bool(args.confirm_apply))
    else:
        payload = process_candidate(review_ids[0], action=args.action, confirm=bool(args.confirm_apply))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
