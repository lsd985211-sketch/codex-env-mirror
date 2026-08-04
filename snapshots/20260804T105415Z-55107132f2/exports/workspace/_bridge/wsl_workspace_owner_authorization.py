#!/usr/bin/env python3
"""Scoped authorization for the one-way Windows host projection.

Ownership: bind one current-task R2 permit to the exact projection plan.
Non-goals: copy files, create backups, select members, reverse-sync, or treat
the mechanical confirmation string as authorization.
State behavior: writes only through the shared scoped-authorization journal.
Caller context: wsl_workspace_owner immediately before backup and after hash
readback.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import scoped_authorization as authorization
import scoped_authorization_policy as policy

OWNER = "wsl_workspace_owner"
ACTION = "wsl_workspace.host_compatibility_projection"
PHASE = "windows_host_compatibility_projection"


def _failure(reason: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "reason": reason, **details}


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def projection_target(plan: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for item in plan.get("files") or []:
        if not isinstance(item, Mapping) or not item.get("selected"):
            continue
        rows.append({
            "projection_scope": str(item.get("projection_scope") or ""),
            "relative_path": str(item.get("relative_path") or ""),
            "source_sha256": str(item.get("source_sha256") or ""),
            "target_sha256": str(item.get("target_sha256") or ""),
            "target_exists": bool(item.get("target_exists")),
        })
    manifest = Path(str(plan.get("manifest_path") or ""))
    return {
        "source_root": str(plan.get("source_root") or ""),
        "target_root": str(plan.get("target_root") or ""),
        "direction": "wsl_work_git_to_windows_host_projection_only",
        "selection_mode": str(plan.get("selection_mode") or ""),
        "selected_files": sorted(rows, key=lambda row: (row["projection_scope"], row["relative_path"])),
        "manifest_path": str(manifest),
        "manifest_sha256": _sha256(manifest),
        "manifest_current": bool(plan.get("manifest_current")),
    }


def source_signature(plan: Mapping[str, Any]) -> str:
    target = projection_target(plan)
    source = {
        "source_root": target["source_root"],
        "selected_files": [
            {
                "projection_scope": row["projection_scope"],
                "relative_path": row["relative_path"],
                "source_sha256": row["source_sha256"],
            }
            for row in target["selected_files"]
        ],
    }
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def projection_scope(plan: Mapping[str, Any], *, thread_id: str) -> dict[str, Any]:
    target = projection_target(plan)
    scope = authorization.build_scope(
        thread_id=thread_id, action=ACTION, target=target, risk="R2",
        phase=PHASE, source_signature=source_signature(plan),
        requested_by_owner=OWNER,
    )
    locations = [
        f"{row['projection_scope']}:{row['relative_path']}"
        for row in target["selected_files"]
    ]
    locations.append(f"manifest:{target['manifest_path']}")
    return authorization.normalize_rich_scope({
        **scope, "actions": [ACTION], "locations": locations,
        "target_identifiers": [scope["target_fingerprint"]],
        "risk_ceiling": "R2", "allowed_executors": [OWNER],
        "prohibitions": [
            "reverse_sync", "arbitrary_host_path_write", "external.push",
            "mirror.publish", "mirror.refresh", "release", "milestone",
        ],
    })


def prepare_projection(
    plan: Mapping[str, Any], *, thread_id: str, assessment: Mapping[str, Any],
    rollout_path: Path | str, user_message_ref: str, operation_id: str,
    workflow_semantic_hash: str, state_root: Path | str | None = None,
) -> dict[str, Any]:
    if not plan.get("eligible") or not plan.get("would_change"):
        return _failure("host_projection_change_required")
    scope = projection_scope(plan, thread_id=thread_id)
    decision = policy.decide_gate(assessment)
    intent = authorization.create_current_task_intent(
        scope, assessment=assessment, assessment_decision=decision,
        rollout_path=rollout_path, owner=OWNER, operation_id=operation_id,
        recovery_ref=f"backup-router:{scope['target_fingerprint']}",
        user_message_ref=user_message_ref, state_root=state_root,
    )
    if not intent.get("ok"):
        return intent
    permit = authorization.issue_current_task_permit(
        str(intent["intent_ref"]), scope, executor=OWNER,
        operation_id=operation_id, workflow_semantic_hash=workflow_semantic_hash,
        state_root=state_root,
    )
    if not permit.get("ok"):
        return permit
    return {
        "schema": "wsl_workspace_owner.host_projection_authorization.v1",
        "ok": True, "intent_ref": intent["intent_ref"],
        "permit_ref": permit["permit_ref"], "operation_id": operation_id,
        "scope_signature": permit.get("scope_signature", ""),
        "target_fingerprint": scope["target_fingerprint"],
        "pdp_decision": decision.get("decision", ""),
    }


def authorize_projection(
    permit_ref: str, plan: Mapping[str, Any], *, operation_id: str,
    workflow_semantic_hash: str, thread_id: str,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    if not permit_ref or not operation_id or not workflow_semantic_hash or not thread_id:
        return _failure("host_projection_scoped_authorization_required")
    expected = projection_scope(plan, thread_id=thread_id)
    snapshot = authorization.permit_snapshot(permit_ref, state_root=state_root)
    if not snapshot.get("ok"):
        return snapshot
    if snapshot.get("intent_type") != "task_intent":
        return _failure("host_projection_task_intent_required")
    if snapshot.get("executor") != OWNER or snapshot.get("audience") != OWNER:
        return _failure("host_projection_owner_binding_changed")
    mismatches = [
        field for field in ("thread_id", "action", "target_fingerprint", "phase", "source_signature", "requested_by_owner")
        if str((snapshot.get("scope") or {}).get(field) or "") != str(expected.get(field) or "")
    ]
    if mismatches:
        return _failure("host_projection_scope_changed", mismatched_fields=mismatches)
    if str(snapshot.get("operation_id") or "") != operation_id:
        return _failure("host_projection_operation_changed")
    if str(snapshot.get("workflow_semantic_hash") or "") != workflow_semantic_hash:
        return _failure("host_projection_workflow_changed")
    consumed = authorization.consume_permit(
        permit_ref, executor=OWNER, operation_id=operation_id,
        idempotency_key=operation_id, state_root=state_root,
        current_environment_snapshot=snapshot.get("environment_snapshot"),
    )
    if not consumed.get("ok"):
        return consumed
    started = authorization.record_effect(
        operation_id, executor=OWNER, status="effect_started",
        details={"target_fingerprint": expected["target_fingerprint"]},
        state_root=state_root,
    )
    if not started.get("ok"):
        return started
    return {
        "schema": "wsl_workspace_owner.host_projection_gate.v1",
        "ok": True, "operation_id": operation_id,
        "target_fingerprint": expected["target_fingerprint"],
    }


def finish_projection(
    operation_id: str, result: Mapping[str, Any], *,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    projected = sorted(str(value) for value in result.get("projected_files") or [])
    receipt = f"host-projection:{result.get('selection_mode', '')}:{len(projected)}:{bool(result.get('full_projection_current'))}"
    if not result.get("ok"):
        return authorization.record_effect(
            operation_id, executor=OWNER, status="effect_unknown",
            effect_receipt_ref=receipt,
            details={"reason": result.get("reason", "projection_acceptance_failed")},
            state_root=state_root,
        )
    observed = authorization.record_effect(
        operation_id, executor=OWNER, status="effect_observed",
        effect_receipt_ref=receipt,
        details={
            "projected_files": projected,
            "full_projection_current": bool(result.get("full_projection_current")),
            "remaining_drift": list(result.get("remaining_drift") or []),
        }, state_root=state_root,
    )
    if not observed.get("ok"):
        return observed
    return authorization.record_effect(
        operation_id, executor=OWNER, status="completed",
        effect_receipt_ref=receipt, state_root=state_root,
    )
