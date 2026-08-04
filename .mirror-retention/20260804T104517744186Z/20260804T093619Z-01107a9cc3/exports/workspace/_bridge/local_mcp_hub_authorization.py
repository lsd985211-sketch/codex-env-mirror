#!/usr/bin/env python3
"""Single-operation scoped authorization adapter for local Hub reloads.

Ownership: bind one current-task R2 permit to the exact Hub lifecycle
authority, source signature, listener port, and pre-reload process identity.
Non-goals: decide risk, restart services, delete caches, accept mechanical
confirmation as authorization, or persist a second authorization model.
State behavior: writes only through ``scoped_authorization`` journals.
Caller context: ``local_mcp_hub_process`` immediately before and after its
bounded reload side effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import scoped_authorization as authorization
import scoped_authorization_policy as policy


OWNER = "local_mcp_hub_process"
ACTION = "local_mcp_hub.reload"
PHASE = "local_mcp_hub_runtime_reload"
SERVICE = "codex-local-mcp-hub.service"
FALLBACK_TASK = "CodexLocalMcpHub"


def _failure(reason: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "reason": reason, **details}


def reload_target(
    *, authority: str, port: int, matched_processes: list[Mapping[str, Any]],
) -> dict[str, Any]:
    processes = sorted(
        (
            {
                "pid": int(item.get("pid") or 0),
                "authority": str(item.get("authority") or authority),
            }
            for item in matched_processes
        ),
        key=lambda item: (item["authority"], item["pid"]),
    )
    return {
        "authority": str(authority or ""),
        "service": SERVICE if authority == "wsl_user_systemd" else "",
        "fallback_task": FALLBACK_TASK if authority != "wsl_user_systemd" else "",
        "bind": {"host": "127.0.0.1", "port": int(port)},
        "matched_processes": processes,
    }


def reload_scope(
    *, thread_id: str, source_signature: str, authority: str, port: int,
    matched_processes: list[Mapping[str, Any]],
) -> dict[str, Any]:
    target = reload_target(
        authority=authority, port=port, matched_processes=matched_processes,
    )
    scope = authorization.build_scope(
        thread_id=thread_id, action=ACTION, target=target, risk="R2",
        phase=PHASE, source_signature=source_signature, requested_by_owner=OWNER,
    )
    return authorization.normalize_rich_scope({
        **scope,
        "actions": [ACTION],
        "locations": [
            f"tcp:127.0.0.1:{int(port)}",
            f"systemd:user:{SERVICE}" if authority == "wsl_user_systemd"
            else f"windows-task:{FALLBACK_TASK}",
        ],
        "target_identifiers": [scope["target_fingerprint"]],
        "risk_ceiling": "R2",
        "allowed_executors": [OWNER],
        "prohibitions": [
            "arbitrary_process_stop", "arbitrary_service_restart",
            "external_write", "mirror.refresh", "publish", "release",
        ],
    })


def prepare_reload(
    *, thread_id: str, source_signature: str, authority: str, port: int,
    matched_processes: list[Mapping[str, Any]], assessment: Mapping[str, Any],
    rollout_path: Path | str, user_message_ref: str, operation_id: str,
    workflow_semantic_hash: str, state_root: Path | str | None = None,
) -> dict[str, Any]:
    scope = reload_scope(
        thread_id=thread_id, source_signature=source_signature,
        authority=authority, port=port, matched_processes=matched_processes,
    )
    recovery_ref = (
        f"systemd:user:{SERVICE}:restart" if authority == "wsl_user_systemd"
        else f"windows-task:{FALLBACK_TASK}:restart"
    )
    decision = policy.decide_gate(assessment)
    intent = authorization.create_current_task_intent(
        scope, assessment=assessment, assessment_decision=decision,
        rollout_path=rollout_path, owner=OWNER, operation_id=operation_id,
        recovery_ref=recovery_ref, user_message_ref=user_message_ref,
        state_root=state_root,
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
        "schema": "local_mcp_hub.reload_authorization.v1",
        "ok": True,
        "intent_ref": intent["intent_ref"],
        "permit_ref": permit["permit_ref"],
        "operation_id": operation_id,
        "scope_signature": permit.get("scope_signature", ""),
        "target_fingerprint": scope["target_fingerprint"],
        "permit_expires_at": permit.get("permit_expires_at", ""),
        "pdp_decision": decision.get("decision", ""),
        "reused": bool(intent.get("reused") or permit.get("reused")),
    }


def authorize_reload(
    permit_ref: str, *, operation_id: str, workflow_semantic_hash: str,
    thread_id: str, source_signature: str, authority: str, port: int,
    matched_processes: list[Mapping[str, Any]],
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    if not permit_ref or not operation_id or not workflow_semantic_hash or not thread_id:
        return _failure("local_mcp_hub_reload_authorization_required")
    expected = reload_scope(
        thread_id=thread_id, source_signature=source_signature,
        authority=authority, port=port, matched_processes=matched_processes,
    )
    snapshot = authorization.permit_snapshot(permit_ref, state_root=state_root)
    if not snapshot.get("ok"):
        return snapshot
    if snapshot.get("intent_type") != "task_intent":
        return _failure("local_mcp_hub_reload_task_intent_required")
    if snapshot.get("executor") != OWNER or snapshot.get("audience") != OWNER:
        return _failure("local_mcp_hub_reload_owner_binding_changed")
    mismatches = [
        field for field in (
            "thread_id", "action", "target_fingerprint", "phase",
            "source_signature", "requested_by_owner",
        )
        if str((snapshot.get("scope") or {}).get(field) or "")
        != str(expected.get(field) or "")
    ]
    if mismatches:
        return _failure(
            "local_mcp_hub_reload_scope_changed", mismatched_fields=mismatches,
        )
    if str(snapshot.get("operation_id") or "") != operation_id:
        return _failure("local_mcp_hub_reload_operation_changed")
    if str(snapshot.get("workflow_semantic_hash") or "") != workflow_semantic_hash:
        return _failure("local_mcp_hub_reload_workflow_changed")
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
        operation = authorization.operation_snapshot(
            operation_id, executor=OWNER, state_root=state_root,
        )
        if operation.get("status") not in {
            "effect_started", "effect_unknown", "effect_observed", "completed",
        }:
            return started
    return {
        "schema": "local_mcp_hub.reload_gate.v1", "ok": True,
        "operation_id": operation_id,
        "target_fingerprint": expected["target_fingerprint"],
        "reused": bool(consumed.get("reused")),
    }


def finish_reload(
    operation_id: str, result: Mapping[str, Any], *,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    before = [str(item.get("pid") or "") for item in result.get("matched_processes") or []]
    after = [str(item.get("pid") or "") for item in result.get("after_processes") or []]
    receipt = (
        f"local-mcp-hub-reload:{result.get('authority', '')}:"
        f"{','.join(before)}->{','.join(after)}:{result.get('port', '')}"
    )
    if not result.get("ok"):
        return authorization.record_effect(
            operation_id, executor=OWNER, status="effect_unknown",
            effect_receipt_ref=receipt,
            details={"reason": result.get("reason", "reload_acceptance_failed")},
            state_root=state_root,
        )
    observed = authorization.record_effect(
        operation_id, executor=OWNER, status="effect_observed",
        effect_receipt_ref=receipt,
        details={"before_pids": before, "after_pids": after, "health_ok": True},
        state_root=state_root,
    )
    if not observed.get("ok"):
        return observed
    return authorization.record_effect(
        operation_id, executor=OWNER, status="completed",
        effect_receipt_ref=receipt, state_root=state_root,
    )
