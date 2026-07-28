#!/usr/bin/env python3
"""Scoped-authorization PEP adapter for the unified scheduler.

The scheduler remains the run owner and scoped_authorization remains the sole
PDP/state owner.  This adapter derives stable authorization inputs from one
registered task contract; it cannot authorize arbitrary command text.
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any


BRIDGE = Path(__file__).resolve().parents[1]
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import authorization_environment_provider  # noqa: E402
import scoped_authorization  # noqa: E402


EXECUTOR = "codex_scheduler_runner"


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def task_authorization_contract(task: dict[str, Any]) -> dict[str, Any]:
    policy = task.get("policy") if isinstance(task.get("policy"), dict) else {}
    action = task.get("action") if isinstance(task.get("action"), dict) else {}
    return {
        "automation_id": str(task.get("id") or ""),
        "action_type": str(action.get("type") or ""),
        "command": [str(item) for item in action.get("command", [])],
        "mode": str(policy.get("mode") or ""),
        "risk": str(policy.get("risk") or "unknown"),
        "allowed_effect": str(policy.get("allowed_effect") or ""),
        "executor": EXECUTOR,
        "audience": EXECUTOR,
        "delegation_allowed": False,
    }


def workflow_semantic_hash(task: dict[str, Any]) -> str:
    return _digest(task_authorization_contract(task))


def environment_snapshot(workflow_hash: str = "scheduler.registered_task_contract.v1") -> dict[str, Any]:
    return authorization_environment_provider.snapshot(
        workflow_semantic_hash=workflow_hash,
        owner=EXECUTOR,
        owner_capability_signature=_digest({"executor": EXECUTOR, "permit_model": "per_run"}),
    )


def authorization_plan(task: dict[str, Any], *, due_reason: str) -> dict[str, Any]:
    contract = task_authorization_contract(task)
    semantic_hash = workflow_semantic_hash(task)
    run_id = f"scheduler-run:{uuid.uuid4().hex}"
    operation_id = f"scheduler:{contract['automation_id']}:{run_id.rsplit(':', 1)[-1]}"
    scope = scoped_authorization.build_scope(
        thread_id=f"automation:{contract['automation_id']}",
        action=f"scheduler.run:{contract['automation_id']}",
        target=contract,
        risk=contract["risk"],
        phase="scheduled_run",
        source_signature=semantic_hash,
        requested_by_owner=EXECUTOR,
    )
    environment = {
        **environment_snapshot(semantic_hash),
        "workflow_semantic_hash": semantic_hash,
        "authorization_semantic_signature": semantic_hash,
    }
    return {
        "schema": "codex_scheduler.authorization_plan.v1",
        "ok": True,
        "automation_run_id": run_id,
        "operation_id": operation_id,
        "due_reason": due_reason,
        "scope": scope,
        "workflow_semantic_hash": semantic_hash,
        "environment_snapshot": environment,
        "authority_ref": f"system-membership:scheduler:{contract['automation_id']}",
        "automatic_expansion_allowed": False,
    }


def authorize(task: dict[str, Any], *, due_reason: str, state_root: Path | str | None = None) -> dict[str, Any]:
    plan = authorization_plan(task, due_reason=due_reason)
    intent = scoped_authorization.ensure_system_intent(
        plan["scope"], authority_ref=plan["authority_ref"],
        subject=f"scheduler:{task.get('id')}", allowed_actor_classes=["scheduler"],
        state_root=state_root,
    )
    if not intent.get("ok"):
        return {**plan, "ok": False, "reason": intent.get("reason"), "intent": intent}
    permit = scoped_authorization.issue_permit(
        intent["intent_ref"], plan["scope"], actor_chain=[{"class": "scheduler", "id": str(task.get("id") or "")}],
        executor=EXECUTOR, audience=EXECUTOR, operation_id=plan["operation_id"],
        workflow_semantic_hash=plan["workflow_semantic_hash"], automation_run_id=plan["automation_run_id"],
        environment_snapshot=plan["environment_snapshot"], state_root=state_root,
    )
    if not permit.get("ok"):
        return {**plan, "ok": False, "reason": permit.get("reason"), "permit": permit}
    consumed = scoped_authorization.consume_permit(
        permit["permit_ref"], executor=EXECUTOR, operation_id=plan["operation_id"],
        current_environment_snapshot=plan["environment_snapshot"], state_root=state_root,
    )
    if not consumed.get("ok"):
        return {**plan, "ok": False, "reason": consumed.get("reason"), "consumption": consumed}
    started = scoped_authorization.record_effect(
        plan["operation_id"], executor=EXECUTOR, status="effect_started", state_root=state_root,
    )
    return {**plan, "ok": bool(started.get("ok")), "intent_ref": intent["intent_ref"], "permit_ref": permit["permit_ref"], "consumption_ref": consumed.get("consumption_ref", "")}


def finish(authorization: dict[str, Any], *, ok: bool, receipt_ref: str, state_root: Path | str | None = None) -> dict[str, Any]:
    operation_id = str(authorization.get("operation_id") or "")
    if not operation_id:
        return {"ok": False, "reason": "scheduler_authorization_operation_missing"}
    observed = scoped_authorization.record_effect(
        operation_id, executor=EXECUTOR, status="effect_observed",
        effect_receipt_ref=receipt_ref, details={"owner_ok": bool(ok)}, state_root=state_root,
    )
    if not observed.get("ok"):
        return observed
    return scoped_authorization.record_effect(
        operation_id, executor=EXECUTOR, status="completed" if ok else "compensation_required",
        effect_receipt_ref=receipt_ref, details={"owner_ok": bool(ok)}, state_root=state_root,
    )
