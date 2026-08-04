#!/usr/bin/env python3
"""R2 task-intent lifecycle binding for the governed Work Git owner.

Ownership: bind one PDP-verified current-task R2 intent and permit to one
declared, reversible Work Git change-set lifecycle and maintain its monotonic
operation checkpoints.
Non-goals: execute Git, classify arbitrary business risk, authorize R3/high
cost/R4 work, publish mirrors or remotes, replace mechanical confirmations,
or infer authority from approval prose.
State behavior: writes only through scoped_authorization intent, permit,
consumption, operation, and effect journals; Git receipts remain owned by
work_git_change_owner.
Caller context: work_git_change_owner immediately before and after start,
successor replay, commit, local-bare sync, or fast-forward integrate for one declared R2
change-set.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import scoped_authorization as authorization
import scoped_authorization_policy as policy


OWNER = "work_git_change_owner"
ACTION = "work_git.r2_declared_changeset_lifecycle"
PHASE = "work_git_r2_changeset"
STEPS = {"start": 0, "replay": 1, "commit": 2, "sync": 3, "integrate": 4}


def _failure(reason: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "reason": reason, **details}


def assessment_template(*, thread_id: str, task_id: str, repository_id: str, branch: str,
                        base_head: str, declared_paths: list[str], owner: str = OWNER,
                        recovery_ref: str = "") -> dict[str, Any]:
    scope = lifecycle_scope(
        thread_id=thread_id, repository_id=repository_id, task_id=task_id,
        branch=branch, base_head=base_head, declared_paths=declared_paths,
    )
    return {
        "schema": "work_git_change_owner.r2_assessment_template.v1",
        "scope": scope,
        "subject": {"thread_id": thread_id},
        "action": {"id": ACTION},
        "resource": {"target_fingerprint": scope["target_fingerprint"]},
        "environment": {"owner": owner, "phase": PHASE, "source_signature": base_head, "recovery_ref": recovery_ref or f"git:HEAD:{base_head}"},
        "risk": {"level": "R2", "facts": ["reversible", "verified_restore_point"]},
        "costs": {name: 0 for name in policy.load_policy().get("cost_dimensions", {})},
        "matched_forbids": [],
        "validator": "python _bridge/scoped_authorization_policy.py validate",
        "binding_rule": "copy subject/action/resource/environment identifiers exactly; do not substitute business labels",
    }


def _normalized_paths(values: list[str]) -> list[str]:
    return sorted({str(value or "").strip().replace("\\", "/") for value in values if str(value or "").strip()})


def _successor_binding(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    return {
        "predecessor_ref": str(value.get("predecessor_ref") or "").strip(),
        "predecessor_commit": str(value.get("predecessor_commit") or "").strip(),
        "old_base_head": str(value.get("old_base_head") or "").strip(),
        "current_main_head": str(value.get("current_main_head") or "").strip(),
        "successor_signature": str(value.get("successor_signature") or "").strip(),
        "changeset_digest": str(value.get("changeset_digest") or "").strip(),
        "validation_receipt_signature": str(value.get("validation_receipt_signature") or "").strip(),
    }


def lifecycle_target(
    *, repository_id: str, task_id: str, branch: str, base_head: str,
    declared_paths: list[str], successor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = {
        "repository_id": str(repository_id or "").strip(),
        "task_id": str(task_id or "").strip().lower(),
        "branch": str(branch or "").strip(),
        "base_head": str(base_head or "").strip(),
        "declared_paths": _normalized_paths(declared_paths),
    }
    binding = _successor_binding(successor)
    if binding:
        target["successor_replay"] = binding
    return target


def lifecycle_scope(
    *, thread_id: str, repository_id: str, task_id: str, branch: str,
    base_head: str, declared_paths: list[str], successor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = lifecycle_target(
        repository_id=repository_id, task_id=task_id, branch=branch,
        base_head=base_head, declared_paths=declared_paths, successor=successor,
    )
    scope = authorization.build_scope(
        thread_id=thread_id, action=ACTION, target=target, risk="R2",
        phase=PHASE, source_signature=base_head, requested_by_owner=OWNER,
    )
    return authorization.normalize_rich_scope({
        **scope,
        "actions": [ACTION],
        "locations": target["declared_paths"],
        "target_identifiers": [scope["target_fingerprint"]],
        "risk_ceiling": "R2",
        "allowed_executors": [OWNER],
        "prohibitions": [
            "external.push", "git.history_rewrite", "mirror.publish",
            "mirror.refresh", "release", "milestone",
        ],
    })


def prepare_r2_lifecycle(
    *, thread_id: str, repository_id: str, task_id: str, branch: str,
    base_head: str, declared_paths: list[str], assessment: Mapping[str, Any],
    rollout_path: Path | str, user_message_ref: str, operation_id: str,
    workflow_semantic_hash: str, successor: Mapping[str, Any] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Create or reuse the single non-bearer permit for one R2 lifecycle."""

    scope = lifecycle_scope(
        thread_id=thread_id, repository_id=repository_id, task_id=task_id,
        branch=branch, base_head=base_head, declared_paths=declared_paths, successor=successor,
    )
    recovery_ref = f"git:HEAD:{base_head}"
    decision = policy.decide_gate(assessment)
    intent = authorization.create_current_task_intent(
        scope, assessment=assessment, assessment_decision=decision,
        rollout_path=rollout_path, owner=OWNER, operation_id=operation_id,
        recovery_ref=recovery_ref, user_message_ref=user_message_ref,
        state_root=state_root,
    )
    if not intent.get("ok"):
        return intent
    # A retry after r2-start must not try to mint, annotate, or reserve the
    # already consumed permit again.  It may only reuse the private operation
    # journal when every capability-bearing binding still exactly matches this
    # prepare request.  This keeps retry observability cheap without making a
    # consumed permit transferable to another operation or scope.
    existing_operation = authorization.operation_snapshot(
        operation_id, executor=OWNER, state_root=state_root,
    )
    if existing_operation.get("ok"):
        existing_correlation = (
            existing_operation.get("correlation")
            if isinstance(existing_operation.get("correlation"), dict)
            else {}
        )
        existing_permit_ref = str(existing_operation.get("permit_ref") or "")
        existing_permit = authorization.permit_snapshot(
            existing_permit_ref, state_root=state_root,
        ) if existing_permit_ref else {"ok": False}
        exact_existing_operation = (
            existing_permit.get("ok")
            and str(existing_operation.get("intent_ref") or "") == str(intent.get("intent_ref") or "")
            and str(existing_operation.get("executor") or "") == OWNER
            and str(existing_operation.get("operation_id") or "") == operation_id
            and str(existing_operation.get("idempotency_key") or "") == operation_id
            and str(existing_correlation.get("task_id") or "") == str(task_id or "")
            and str(existing_correlation.get("target_fingerprint") or "") == scope["target_fingerprint"]
            and str(existing_correlation.get("scope_signature") or "") == str(existing_permit.get("scope_signature") or "")
            and str(existing_permit.get("operation_id") or "") == operation_id
            and str(existing_permit.get("executor") or "") == OWNER
            and str(existing_permit.get("workflow_semantic_hash") or "") == workflow_semantic_hash
            and authorization.normalize_rich_scope(existing_permit.get("scope") or {}) == scope
        )
        if exact_existing_operation:
            return {
                "schema": "work_git_change_owner.r2_lifecycle_authorization.v1",
                "ok": True,
                "task_id": str(task_id or ""),
                "intent_ref": intent["intent_ref"],
                "permit_ref": existing_permit_ref,
                "operation_id": operation_id,
                "scope_signature": existing_permit.get("scope_signature", ""),
                "lifecycle_signature": scope["target_fingerprint"],
                "target_fingerprint": scope["target_fingerprint"],
                "relevant_input_signature": str(existing_operation.get("relevant_input_signature") or intent.get("pdp_input_signature") or ""),
                "permit_expires_at": existing_permit.get("permit_expires_at", ""),
                "pdp_decision": decision.get("decision", ""),
                "reused": True,
            }
    permit = authorization.issue_current_task_permit(
        str(intent["intent_ref"]), scope, executor=OWNER,
        operation_id=operation_id, workflow_semantic_hash=workflow_semantic_hash,
        state_root=state_root,
    )
    if not permit.get("ok"):
        return permit
    correlation = authorization.annotate_permit_correlation(
        str(permit["permit_ref"]), executor=OWNER, operation_id=operation_id,
        correlation={
            "task_id": str(task_id or ""),
            "target_fingerprint": scope["target_fingerprint"],
            "scope_signature": str(permit.get("scope_signature") or ""),
        }, state_root=state_root,
    )
    if not correlation.get("ok"):
        return correlation
    prepared_operation = authorization.reserve_prepared_operation(
        str(permit["permit_ref"]), executor=OWNER, operation_id=operation_id, state_root=state_root,
    )
    if not prepared_operation.get("ok"):
        return prepared_operation
    return {
        "schema": "work_git_change_owner.r2_lifecycle_authorization.v1",
        "ok": True,
        "task_id": str(task_id or ""),
        "intent_ref": intent["intent_ref"],
        "permit_ref": permit["permit_ref"],
        "operation_id": operation_id,
        "scope_signature": permit.get("scope_signature", ""),
        "lifecycle_signature": scope["target_fingerprint"],
        "target_fingerprint": scope["target_fingerprint"],
        "relevant_input_signature": intent.get("pdp_input_signature", ""),
        "permit_expires_at": permit.get("permit_expires_at", ""),
        "pdp_decision": decision.get("decision", ""),
        "reused": bool(intent.get("reused") or permit.get("reused")),
    }


def authorize_step(
    *, permit_ref: str, operation_id: str, step: str, thread_id: str,
    repository_id: str, task_id: str, branch: str, base_head: str,
    declared_paths: list[str], workflow_semantic_hash: str,
    successor: Mapping[str, Any] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Consume once, then recover and validate one monotonic lifecycle step."""

    if step not in STEPS:
        return _failure("work_git_lifecycle_step_invalid", step=step)
    expected = lifecycle_scope(
        thread_id=thread_id, repository_id=repository_id, task_id=task_id,
        branch=branch, base_head=base_head, declared_paths=declared_paths, successor=successor,
    )
    snapshot = authorization.permit_snapshot(permit_ref, state_root=state_root)
    if not snapshot.get("ok"):
        return snapshot
    # Low-cost R2 work uses a task intent.  A challenge-granted R2 lifecycle
    # instead carries the same exact scope in a single-use ``one_time`` intent.
    # Both remain bound below to the owner, operation, source, and environment.
    if snapshot.get("intent_type") not in {"task_intent", "one_time"}:
        return _failure("work_git_lifecycle_exact_intent_required")
    mismatches = [
        field for field in ("thread_id", "action", "target_fingerprint", "phase", "source_signature", "requested_by_owner")
        if str((snapshot.get("scope") or {}).get(field) or "") != str(expected.get(field) or "")
    ]
    if mismatches:
        return _failure("work_git_lifecycle_scope_changed", mismatched_fields=mismatches)
    if str(snapshot.get("operation_id") or "") != str(operation_id or ""):
        return _failure("work_git_lifecycle_operation_changed")
    if str(snapshot.get("workflow_semantic_hash") or "") != str(workflow_semantic_hash or ""):
        return _failure("work_git_lifecycle_workflow_changed")
    consumed = authorization.consume_permit(
        permit_ref, executor=OWNER, operation_id=operation_id,
        idempotency_key=operation_id, state_root=state_root,
        current_environment_snapshot=snapshot.get("environment_snapshot"),
    )
    if not consumed.get("ok"):
        return consumed
    direct_main_commit = step == "commit" and branch == "main"
    started = authorization.record_effect(
        operation_id, executor=OWNER, status="effect_started",
        details={
            "lifecycle_signature": expected["target_fingerprint"],
            "implicit_start": "direct_main_commit" if direct_main_commit else "",
        },
        state_root=state_root,
    )
    if not started.get("ok"):
        operation = authorization.operation_snapshot(operation_id, executor=OWNER, state_root=state_root)
        if operation.get("status") not in {"effect_started", "effect_unknown", "effect_observed", "completed"}:
            return started
    operation = authorization.operation_snapshot(operation_id, executor=OWNER, state_root=state_root)
    if operation.get("status") == "effect_unknown":
        return _failure("work_git_lifecycle_effect_reconciliation_required")
    checkpoints = list((operation.get("details") or {}).get("lifecycle_checkpoints") or [])
    completed = [row for row in checkpoints if row.get("status") == "completed"]
    same = next((row for row in reversed(completed) if row.get("step") == step), None)
    if same:
        return {
            "schema": "work_git_change_owner.lifecycle_gate.v1", "ok": True,
            "reused": True, "skip_effect": True, "step": step,
            "effect_receipt_ref": same.get("effect_receipt_ref", ""),
            "operation_ref": operation.get("operation_ref", ""),
        }
    highest = max((STEPS.get(str(row.get("step")), -1) for row in completed), default=-1)
    required_previous = STEPS[step] - 1
    allowed_gap = False
    if step == "commit" and highest == STEPS["start"] and not _successor_binding(successor):
        required_previous = STEPS["start"]
        allowed_gap = True
    if direct_main_commit and highest == -1:
        # A clean main worktree has no task worktree to start.  Its first
        # source effect is the exact permit-bound commit, recorded above.
        required_previous = -1
        allowed_gap = True
    if step == "integrate" and highest == STEPS["commit"]:
        required_previous = STEPS["commit"]
        allowed_gap = True
    if (STEPS[step] > highest + 1 and not allowed_gap) or highest < required_previous:
        return _failure("work_git_lifecycle_step_out_of_order", step=step, highest_completed=highest)
    if STEPS[step] <= highest:
        return _failure("work_git_lifecycle_step_regression", step=step, highest_completed=highest)
    return {
        "schema": "work_git_change_owner.lifecycle_gate.v1", "ok": True,
        "reused": bool(consumed.get("reused")), "skip_effect": False,
        "step": step, "operation_ref": operation.get("operation_ref", ""),
    }


def record_step(
    *, operation_id: str, step: str, effect_receipt_ref: str, success: bool,
    result_signature: str, terminal: bool = False,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Append one result checkpoint and close the operation after integrate."""

    checkpoint = authorization.record_operation_checkpoint(
        operation_id, executor=OWNER, checkpoint={
            "step": step, "status": "completed" if success else "failed",
            "effect_receipt_ref": str(effect_receipt_ref or ""),
            "result_signature": str(result_signature or ""),
        },
        state_root=state_root,
    )
    if not checkpoint.get("ok") or not success or not terminal:
        return checkpoint
    observed = authorization.record_effect(
        operation_id, executor=OWNER, status="effect_observed",
        effect_receipt_ref=effect_receipt_ref, state_root=state_root,
    )
    if not observed.get("ok"):
        return observed
    return authorization.record_effect(
        operation_id, executor=OWNER, status="completed",
        effect_receipt_ref=effect_receipt_ref, state_root=state_root,
    )
