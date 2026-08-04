#!/usr/bin/env python3
"""Current-task low-risk intent authority for scoped authorization.

Ownership: verify a current direct-user task message plus the risk/cost PDP's
allow-without-challenge result, then derive one non-bearer task intent and its
single-operation permit.
Non-goals: infer business risk, replace the PDP, accept approval prose,
challenge high-impact work, or make cross-task/default authorization.
State behavior: writes only through ``scoped_authorization``'s locked runtime
intent, decision, permit, consumption, and effect journals.
Caller context: a selected R0-R2 low-cost PEP immediately before a reversible
current-task operation; R3, high-cost, R4, and incomplete assessment callers
must use their existing challenge or deny paths instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import authorization_environment_provider
import scoped_authorization as authorization
import scoped_authorization_policy as policy


TASK_INTENT_TYPE = "task_intent"
LOW_RISK_LEVELS = {"R0", "R1", "R2"}
_UNTRUSTED_MARKERS = ("<codex_delegation",)
_CONTINUATION_MESSAGES = {
    "继续", "继续工作", "继续执行", "批准", "批准继续", "批准执行",
    "确认", "确认继续", "同意", "同意继续",
}
_TOKEN_RE = re.compile(r"AUTHORIZE-[A-Za-z0-9_-]+")


def _failure(reason: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "reason": reason, **details}


def _message_kind(text: str) -> str:
    projected = authorization.scoped_authorization_evidence.user_authored_authorization_text(text)
    if not projected.get("ok"):
        return "untrusted_envelope"
    stripped = str(projected.get("text") or "").strip()
    lowered = stripped.lower()
    if not stripped:
        return "blank"
    if any(marker in lowered for marker in _UNTRUSTED_MARKERS):
        return "untrusted_envelope"
    if _TOKEN_RE.fullmatch(stripped):
        return "token_only"
    if _TOKEN_RE.search(stripped):
        return "mixed_token"
    normalized = stripped.strip(" ，,。.!！?？;；:\n\t")
    if normalized in _CONTINUATION_MESSAGES:
        return "continuation"
    return "directive_candidate"


def _current_task_messages(
    rollout_path: Path, *, thread_id: str, user_message_ref: str,
    task_directive_ref: str = "",
) -> dict[str, Any]:
    if authorization._rollout_thread_id(rollout_path) != thread_id:
        return _failure("authorization_rollout_thread_mismatch")
    messages = authorization._user_messages(rollout_path)
    if not messages:
        return _failure("authorization_task_intent_user_message_missing")
    current_index = next((index for index, item in enumerate(messages) if item["message_ref"] == user_message_ref), None)
    if current_index is None:
        return _failure("authorization_task_intent_user_message_unverified")
    requested_current = messages[current_index]
    current = requested_current
    continuity_observation = ""
    explicit_ref = str(task_directive_ref or "").strip()
    if explicit_ref and not any(item["message_ref"] == explicit_ref for item in messages):
        return _failure("authorization_task_intent_directive_ref_unverified")
    current_kind = _message_kind(str(current.get("text") or ""))
    if current_kind == "untrusted_envelope":
        continuity_observation = "untrusted_envelope"
    trusted_messages = [item for item in messages if _message_kind(str(item.get("text") or "")) != "untrusted_envelope"]
    latest_trusted = trusted_messages[-1] if trusted_messages else current
    trailing = messages[current_index + 1 :]
    if current["message_ref"] != latest_trusted["message_ref"]:
        trailing_kinds = [_message_kind(str(item.get("text") or "")) for item in trailing]
        if not all(kind in {"untrusted_envelope", "continuation", "token_only", "blank"} for kind in trailing_kinds):
            return _failure(
                "authorization_task_intent_user_message_not_current",
                current_message_ref=latest_trusted.get("message_ref", ""),
                current_kind=_message_kind(str(latest_trusted.get("text") or "")),
                verified_directive_ref=current.get("message_ref", ""),
                retryable=True,
                retry_condition="supply directive_ref and latest trusted continuation ref",
            )
        current = latest_trusted
        current_index = next(index for index, item in enumerate(messages) if item["message_ref"] == current["message_ref"])
        current_kind = _message_kind(str(current.get("text") or ""))
    elif messages and _message_kind(str(messages[-1].get("text") or "")) == "untrusted_envelope":
        # Preserve the audit observation while binding authority to the latest
        # direct user message rather than treating the envelope as a barrier.
        continuity_observation = "untrusted_envelope"
    if current_kind == "mixed_token":
        return _failure("authorization_task_intent_mixed_token_message")
    if current_kind == "directive_candidate":
        if explicit_ref and explicit_ref != current["message_ref"]:
            return _failure("authorization_task_intent_directive_barrier")
        return {
            "ok": True, "current": current, "directive": current,
            "continuity_reason": continuity_observation or "current_directive",
        }

    directive = None
    for candidate in reversed(messages[:current_index]):
        kind = _message_kind(str(candidate.get("text") or ""))
        if kind in {"blank", "untrusted_envelope", "token_only", "continuation"}:
            continue
        if kind == "mixed_token":
            return _failure("authorization_task_intent_directive_barrier")
        directive = candidate
        break
    if directive is None:
        return _failure("authorization_task_intent_directive_missing")
    if explicit_ref and explicit_ref != directive["message_ref"]:
        return _failure("authorization_task_intent_directive_barrier")
    return {
        "ok": True, "current": current, "directive": directive,
        "continuity_reason": continuity_observation or current_kind,
    }


def _binding_issues(scope: Mapping[str, Any], assessment: Mapping[str, Any], owner: str, recovery_ref: str) -> list[str]:
    subject = assessment.get("subject") if isinstance(assessment.get("subject"), Mapping) else {}
    action = assessment.get("action") if isinstance(assessment.get("action"), Mapping) else {}
    resource = assessment.get("resource") if isinstance(assessment.get("resource"), Mapping) else {}
    environment = assessment.get("environment") if isinstance(assessment.get("environment"), Mapping) else {}
    expected = {
        "subject.thread_id": (subject.get("thread_id"), scope.get("thread_id")),
        "action.id": (action.get("id"), scope.get("action")),
        "resource.target_fingerprint": (resource.get("target_fingerprint"), scope.get("target_fingerprint")),
        "environment.owner": (environment.get("owner"), owner),
        "environment.phase": (environment.get("phase"), scope.get("phase")),
        "environment.source_signature": (environment.get("source_signature"), scope.get("source_signature")),
        "environment.recovery_ref": (environment.get("recovery_ref"), recovery_ref),
    }
    return sorted(name for name, (actual, required) in expected.items() if str(actual or "") != str(required or ""))


def assessment_projection(scope: Mapping[str, Any], *, owner: str, recovery_ref: str) -> dict[str, Any]:
    normalized = authorization.normalize_rich_scope(dict(scope))
    return {
        "schema": "scoped_authorization.assessment_projection.v1",
        "subject": {"thread_id": normalized.get("thread_id", "")},
        "action": {"id": normalized.get("action", "")},
        "resource": {"target_fingerprint": normalized.get("target_fingerprint", "")},
        "environment": {
            "owner": str(owner or ""), "phase": normalized.get("phase", ""),
            "source_signature": normalized.get("source_signature", ""), "recovery_ref": str(recovery_ref or ""),
        },
        "risk": {"level": normalized.get("risk", ""), "facts": []},
        "costs": {"money_single_cny": 0, "money_daily_cny": 0, "elapsed_minutes": 0},
        "matched_forbids": [],
    }


def _verified_pdp(scope: Mapping[str, Any], assessment: Mapping[str, Any], assessment_decision: Mapping[str, Any], *, owner: str, recovery_ref: str) -> dict[str, Any]:
    normalized = authorization.normalize_rich_scope(dict(scope))
    if normalized.get("risk") not in LOW_RISK_LEVELS or normalized.get("risk_ceiling") not in LOW_RISK_LEVELS:
        return _failure("authorization_task_intent_risk_not_low")
    if normalized.get("requested_by_owner") != owner or owner not in set(normalized.get("allowed_executors") or []):
        return _failure("authorization_task_intent_owner_not_bound")
    if not recovery_ref:
        return _failure("authorization_task_intent_recovery_ref_required")
    issues = _binding_issues(normalized, assessment, owner, recovery_ref)
    if issues:
        expected = assessment_projection(normalized, owner=owner, recovery_ref=recovery_ref)
        actual = {key: assessment.get(key, {}) for key in ("subject", "action", "resource", "environment")}
        return _failure(
            "authorization_task_intent_assessment_scope_mismatch", mismatched_fields=issues,
            expected=expected, actual=actual,
            assessment_template_ref="scoped-authorization:assessment-template:current-task-r2",
            retryable=True, retry_condition="replace assessment fields with the exact expected projection",
        )
    recomputed = policy.decide_gate(assessment)
    required_fields = ("decision", "policy_signature", "input_signature")
    if any(str(assessment_decision.get(field) or "") != str(recomputed.get(field) or "") for field in required_fields):
        return _failure("authorization_task_intent_pdp_decision_unverified")
    if recomputed.get("decision") != "allow_without_challenge" or not recomputed.get("ok"):
        return _failure("authorization_task_intent_pdp_decision_not_allow_without_challenge", pdp_decision=str(recomputed.get("decision") or ""))
    return {"ok": True, "scope": normalized, "decision": recomputed}


def create_current_task_intent(scope: Mapping[str, Any], *, assessment: Mapping[str, Any], assessment_decision: Mapping[str, Any], rollout_path: Path | str, owner: str, operation_id: str, recovery_ref: str, user_message_ref: str, task_directive_ref: str = "", state_root: Path | str | None = None) -> dict[str, Any]:
    """Create or reuse one verified current-task low-risk intent."""

    owner = str(owner or "").strip()
    operation_id = str(operation_id or "").strip()
    if not owner or not operation_id:
        return _failure("authorization_task_intent_owner_and_operation_required")
    verified = _verified_pdp(scope, assessment, assessment_decision, owner=owner, recovery_ref=str(recovery_ref or ""))
    if not verified.get("ok"):
        return verified
    normalized = verified["scope"]
    selected = _current_task_messages(
        Path(rollout_path), thread_id=normalized["thread_id"],
        user_message_ref=str(user_message_ref or ""),
        task_directive_ref=str(task_directive_ref or ""),
    )
    if not selected.get("ok"):
        return selected
    current = selected["current"]
    directive = selected["directive"]
    turn_id = authorization.authorization_turn_id(
        authorization.digest({
            "current_message_ref": current["message_ref"],
            "task_directive_ref": directive["message_ref"],
        }),
        authorization.digest({
            "current_message_hash": current["message_hash"],
            "task_directive_hash": directive["message_hash"],
        }),
    )
    intent_signature = authorization.digest({
        "scope": normalized, "owner": owner, "operation_id": operation_id, "recovery_ref": str(recovery_ref),
        "current_message_ref": current["message_ref"], "current_message_hash": current["message_hash"],
        "task_directive_ref": directive["message_ref"], "task_directive_hash": directive["message_hash"],
        "policy_signature": verified["decision"]["policy_signature"], "input_signature": verified["decision"]["input_signature"],
    })
    root = authorization._state_root(state_root)
    with authorization._locked(root):
        for path in sorted((root / "intents").glob("*.json")):
            existing = authorization._read(path)
            if existing.get("intent_type") != TASK_INTENT_TYPE:
                continue
            if existing.get("task_operation_id") == operation_id and existing.get("task_intent_signature") != intent_signature:
                return _failure("authorization_task_intent_operation_scope_changed")
            if existing.get("task_intent_signature") == intent_signature:
                if existing.get("status") not in authorization.ACTIVE_INTENT_STATES:
                    return _failure("authorization_task_intent_inactive", status=existing.get("status", ""))
                return {**existing, "reused": True}
        created = authorization._create_intent_unlocked(
            root, normalized, intent_type=TASK_INTENT_TYPE, authorizer="current_task_user",
            subject=normalized["thread_id"], evidence_ref=directive["message_ref"], turn_id=turn_id,
            authority_ref=f"scoped-authorization-policy:{verified['decision']['policy_signature']}:{verified['decision']['input_signature']}",
            allowed_actor_classes=["codex", "durable_executor"], delegation_allowed=False,
            max_delegation_depth=0, termination={"kind": "single_operation", "operation_id": operation_id},
        )
        if not created.get("ok"):
            return created
        path = authorization._safe_ref(created["intent_ref"], root, "intent")
        created.update({
            "task_intent_signature": intent_signature, "task_operation_id": operation_id, "task_owner": owner,
            "recovery_ref": str(recovery_ref),
            "user_message_ref": current["message_ref"], "user_message_hash": current["message_hash"],
            "current_message_ref": current["message_ref"], "current_message_hash": current["message_hash"],
            "task_directive_ref": directive["message_ref"], "task_directive_hash": directive["message_hash"],
            "continuity_reason": selected["continuity_reason"],
            "pdp_decision": "allow_without_challenge", "pdp_policy_signature": verified["decision"]["policy_signature"],
            "pdp_input_signature": verified["decision"]["input_signature"],
        })
        authorization._write(path, created)
        return created


def issue_current_task_permit(intent_ref: str, requested_scope: Mapping[str, Any], *, executor: str, operation_id: str, workflow_semantic_hash: str, state_root: Path | str | None = None) -> dict[str, Any]:
    """Derive the only allowed permit for a verified task intent."""

    root = authorization._state_root(state_root)
    try:
        path = authorization._safe_ref(intent_ref, root, "intent")
    except ValueError as exc:
        return _failure(str(exc))
    with authorization._locked(root):
        intent = authorization._read(path)
        if intent.get("intent_type") != TASK_INTENT_TYPE:
            return _failure("authorization_task_intent_required")
        if str(intent.get("task_owner") or "") != str(executor or ""):
            return _failure("authorization_task_intent_executor_not_bound")
        if str(intent.get("task_operation_id") or "") != str(operation_id or ""):
            return _failure("authorization_task_intent_operation_mismatch")
        environment = authorization_environment_provider.snapshot(
            workflow_semantic_hash=workflow_semantic_hash, owner=str(executor),
            owner_capability_signature=authorization.digest({"owner": executor, "action": intent["scope"]["action"], "phase": intent["scope"]["phase"]}),
        )
        return authorization._issue_permit_unlocked(
            root, intent, dict(requested_scope), actor_chain=[{"class": "codex", "id": intent["scope"]["thread_id"]}],
            executor=str(executor), audience=str(executor), operation_id=str(operation_id),
            workflow_semantic_hash=workflow_semantic_hash, authorization_turn=str(intent.get("authorization_turn_id") or ""),
            environment_snapshot=environment, permit_ttl_seconds=0,
        )
