#!/usr/bin/env python3
"""Durable execution adapter for business-environment convergence nodes.

Ownership: stable operation identity, exact-task lifecycle coordination, one
durable command submission, terminal receipt consumption, and result handoff.
Non-goals: planning maintenance DAGs, owning business commands, scheduling,
permission decisions, approval inference, or creating another runtime ledger.
State behavior: writes only through persistent_task_kernel,
shared.long_command_receipt, and maintenance_convergence_runtime owners.
Caller context: business-environment milestone C and its thin workflow facade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import persistent_task_kernel
from maintenance_convergence_runtime import STATE_ROOT, load_plan as load_convergence_plan, route_result
from shared import long_command_receipt
from shared.json_cli import configure_utf8_stdio, print_json


DEFAULT_DB = persistent_task_kernel.DEFAULT_DB
AUTOMATIC_LEVELS = {"A0", "A1", "A2"}
TERMINAL_TASK_STATES = {"succeeded", "dead_letter", "rejected"}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int | None:
    try:
        parsed = int(default if value in (None, "") else value)
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def load_plan(plan_id: str, *, state_root: Path = STATE_ROOT) -> dict[str, Any]:
    """Load a persisted maintenance-owner artifact, never an arbitrary command file."""

    return load_convergence_plan(str(plan_id or ""), state_root=state_root)


def build_operation(
    plan: dict[str, Any],
    *,
    cwd: str,
    timeout_seconds: int = 0,
) -> dict[str, Any]:
    if (
        not isinstance(plan, dict)
        or not plan.get("ok")
        or plan.get("schema") != "maintenance_convergence_plan.v1"
        or plan.get("derived_runtime") is not True
    ):
        return {"schema": "business_environment.durable_operation.v1", "ok": False, "reason": "convergence_plan_not_ready"}
    action = plan.get("next_action")
    if action is None and str(plan.get("status") or "") == "complete":
        return {
            "schema": "business_environment.durable_operation.v1",
            "ok": True,
            "complete": True,
            "decision": "reuse",
            "reason": "convergence_plan_complete",
        }
    if not isinstance(action, dict):
        return {"schema": "business_environment.durable_operation.v1", "ok": False, "reason": "next_action_missing"}
    command = action.get("command_argv")
    signature = str(action.get("input_signature") or "")
    node_id = str(action.get("node_id") or "")
    level = str(action.get("automation_level") or "A4")
    if (
        not str(plan.get("plan_id") or "")
        or not node_id
        or not signature
        or not isinstance(command, list)
        or not command
        or not all(str(item) for item in command)
    ):
        return {"schema": "business_environment.durable_operation.v1", "ok": False, "reason": "next_action_contract_incomplete"}
    if level not in AUTOMATIC_LEVELS:
        return {
            "schema": "business_environment.durable_operation.v1",
            "ok": False,
            "reason": "automation_level_requires_owner_or_user_decision",
            "node_id": node_id,
            "automation_level": level,
        }
    effective_timeout = _bounded_int(
        timeout_seconds or action.get("timeout_seconds"), default=30, minimum=1, maximum=3600
    )
    max_attempts = _bounded_int(action.get("max_attempts"), default=3, minimum=1, maximum=5)
    retry_delay_seconds = _bounded_int(action.get("retry_delay_seconds"), default=60, minimum=1, maximum=3600)
    freshness_ttl_seconds = _bounded_int(action.get("freshness_ttl_seconds"), default=0, minimum=0, maximum=86400)
    if None in {effective_timeout, max_attempts, retry_delay_seconds, freshness_ttl_seconds}:
        return {"schema": "business_environment.durable_operation.v1", "ok": False, "reason": "next_action_numeric_contract_invalid"}
    identity = _digest({"node_id": node_id, "input_signature": signature})
    operation_id = f"business-operation:{identity[:32]}"
    return {
        "schema": "business_environment.durable_operation.v1",
        "ok": True,
        "complete": False,
        "operation_id": operation_id,
        "task_id": f"bizop-{identity[:32]}",
        "idempotency_key": operation_id,
        "plan_id": str(plan.get("plan_id") or ""),
        "node_id": node_id,
        "input_signature": signature,
        "owner": str(action.get("owner") or ""),
        "action": str(action.get("action") or ""),
        "command": [str(item) for item in command],
        "cwd": str(Path(cwd).resolve()),
        "timeout_seconds": effective_timeout,
        "max_attempts": max_attempts,
        "retry_delay_seconds": retry_delay_seconds,
        "automation_level": level,
        "effect_class": str(action.get("effect_class") or "unknown"),
        "freshness_ttl_seconds": freshness_ttl_seconds,
        "execution_signature": long_command_receipt.execution_signature(
            [str(item) for item in command], cwd=str(Path(cwd).resolve()), timeout_seconds=effective_timeout
        ),
        "single_writer": "persistent_task_kernel",
        "command_owner": "shared.long_command_receipt",
        "business_command_resubmit_allowed": False,
    }


def _attempt_intent(operation: dict[str, Any], attempt: int) -> str:
    return f"{operation['operation_id']}:attempt:{max(1, int(attempt))}"


def _task_projection(operation: dict[str, Any], task: dict[str, Any], **extra: Any) -> dict[str, Any]:
    state = str(task.get("state") or "")
    next_action = "reuse_terminal_result" if state == "succeeded" else ""
    if state in {"leased", "acked", "executing", "recovery_required"}:
        next_action = "wait_for_or_consume_terminal_event"
    elif state == "retry_wait":
        next_action = "wait_until_retry_due"
    elif state == "dead_letter":
        next_action = "review_single_governance_item"
    return {
        "schema": "business_environment.durable_operation_status.v1",
        "ok": state == "succeeded",
        "operation_id": operation["operation_id"],
        "task_id": operation["task_id"],
        "state": state,
        "attempt_count": int(task.get("attempt_count") or 0),
        "next_attempt_at": str(task.get("next_attempt_at") or ""),
        "terminal": state in TERMINAL_TASK_STATES,
        "reused": state == "succeeded",
        "next_action": next_action,
        "governance_key": f"durable-operation:{operation['operation_id']}",
        "business_command_resubmit_allowed": False,
        **extra,
    }


def _ensure_task(operation: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    return persistent_task_kernel.enqueue(
        task_id=operation["task_id"],
        idempotency_key=operation["idempotency_key"],
        task_type="business_environment_durable_operation",
        target_module=operation["owner"] or operation["node_id"],
        action_type=operation["action"] or "converge",
        payload={
            "operation_id": operation["operation_id"],
            "plan_id": operation["plan_id"],
            "node_id": operation["node_id"],
            "input_signature": operation["input_signature"],
            "execution_signature": operation["execution_signature"],
            "command_owner": operation["command_owner"],
            "timeout_seconds": operation["timeout_seconds"],
            "max_attempts": operation["max_attempts"],
            "retry_delay_seconds": operation["retry_delay_seconds"],
        },
        acceptance={"terminal_receipt": True, "exit_code": 0, "result_consumed": True},
        max_attempts=operation["max_attempts"],
        retry_delay_seconds=operation["retry_delay_seconds"],
        db_path=db_path,
    )


def _owner_result(receipt: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    stdout = str(receipt.get("stdout") or "").strip()
    parsed: dict[str, Any] = {}
    if stdout:
        try:
            candidate = json.loads(stdout)
            if isinstance(candidate, dict):
                parsed = candidate
        except json.JSONDecodeError:
            pass
    exit_code = receipt.get("exit_code")
    ok = bool(receipt.get("terminal")) and isinstance(exit_code, int) and exit_code == 0
    return {
        **parsed,
        "ok": bool(parsed.get("ok", ok)) and ok,
        "owner": operation["owner"],
        "reason": str(parsed.get("reason") or ("" if ok else receipt.get("reason") or f"exit_code_{exit_code}")),
        "artifact_ref": str(receipt.get("raw_result_ref") or receipt.get("stdout_ref") or ""),
        "operation_id": operation["operation_id"],
        "input_signature": operation["input_signature"],
        "execution_signature": operation["execution_signature"],
        "terminal": bool(receipt.get("terminal")),
        "exit_code": exit_code,
    }


def _settle(
    operation: dict[str, Any],
    receipt: dict[str, Any],
    *,
    db_path: Path,
    result_state_root: Path,
) -> dict[str, Any]:
    owner_result = _owner_result(receipt, operation)
    if not owner_result["terminal"] or not isinstance(owner_result["exit_code"], int):
        task = persistent_task_kernel.get(operation["task_id"], db_path=db_path) or {}
        return _task_projection(operation, task, long_command=receipt, reason="terminal_receipt_not_ready")
    settled = persistent_task_kernel.settle_durable_terminal(
        operation["task_id"],
        success=bool(owner_result["ok"]),
        result=owner_result,
        reason=str(owner_result.get("reason") or "durable_command_failed"),
        db_path=db_path,
    )
    task = settled.get("task") if isinstance(settled.get("task"), dict) else {}
    route: dict[str, Any] = {}
    if task.get("state") in {"succeeded", "dead_letter"}:
        route = route_result(
            owner_result,
            plan_id=operation["plan_id"],
            node_id=operation["node_id"],
            input_signature=operation["input_signature"],
            state_root=result_state_root,
        )
    return _task_projection(
        operation,
        task,
        ok=bool(settled.get("ok")) and task.get("state") == "succeeded",
        receipt_reused=bool(receipt.get("reused") or receipt.get("submission_reused")),
        long_command=receipt,
        result_route=route,
        reason=str(owner_result.get("reason") or ""),
    )


def advance_operation(
    plan: dict[str, Any],
    *,
    mode: str,
    cwd: str,
    timeout_seconds: int = 0,
    db_path: Path = DEFAULT_DB,
    result_state_root: Path = STATE_ROOT,
) -> dict[str, Any]:
    operation = build_operation(plan, cwd=cwd, timeout_seconds=timeout_seconds)
    if not operation.get("ok") or operation.get("complete"):
        return operation
    ensured = _ensure_task(operation, db_path=db_path)
    if not ensured.get("ok"):
        return {**operation, "ok": False, "reason": ensured.get("reason"), "task": ensured.get("task", {})}
    task = ensured.get("task") if isinstance(ensured.get("task"), dict) else {}
    if task.get("state") in TERMINAL_TASK_STATES:
        return _task_projection(operation, task)
    attempt = max(1, int(task.get("attempt_count") or 0))
    if task.get("state") == "queued" and int(task.get("attempt_count") or 0) > 0:
        previous = long_command_receipt.consume_terminal_by_intent(
            _attempt_intent(operation, attempt),
            operation["command"],
            timeout_seconds=operation["timeout_seconds"],
            cwd=operation["cwd"],
        )
        if previous.get("terminal"):
            return _settle(operation, previous, db_path=db_path, result_state_root=result_state_root)
        return _task_projection(
            operation,
            task,
            long_command=previous,
            reason="previous_attempt_terminal_evidence_required_before_retry",
        )
    if mode in {"follow", "consume"}:
        receipt = long_command_receipt.consume_terminal_by_intent(
            _attempt_intent(operation, attempt),
            operation["command"],
            timeout_seconds=operation["timeout_seconds"],
            cwd=operation["cwd"],
        )
        if receipt.get("terminal"):
            return _settle(operation, receipt, db_path=db_path, result_state_root=result_state_root)
        return _task_projection(
            operation,
            task,
            long_command=receipt,
            reason=str(receipt.get("reason") or "terminal_event_pending"),
        )
    lease_owner = f"durable-{_digest(operation['operation_id'])[:24]}"
    if task.get("state") in {"executing", "recovery_required"}:
        receipt = long_command_receipt.consume_terminal_by_intent(
            _attempt_intent(operation, attempt),
            operation["command"],
            timeout_seconds=operation["timeout_seconds"],
            cwd=operation["cwd"],
        )
        if receipt.get("terminal"):
            return _settle(operation, receipt, db_path=db_path, result_state_root=result_state_root)
        return _task_projection(
            operation,
            task,
            long_command=receipt,
            reason="interrupted_attempt_requires_terminal_evidence",
        )
    if task.get("state") in {"queued", "retry_wait"}:
        leased = persistent_task_kernel.claim_task(
            operation["task_id"],
            lease_owner=lease_owner,
            lease_seconds=operation["timeout_seconds"] + 30,
            db_path=db_path,
        )
        if not leased.get("ok"):
            current = leased.get("task") if isinstance(leased.get("task"), dict) else task
            return _task_projection(operation, current, reason=str(leased.get("reason") or "task_not_ready"))
        task = leased["task"]
        acknowledged = persistent_task_kernel.acknowledge(
            operation["task_id"], lease_owner=lease_owner, db_path=db_path
        )
        if not acknowledged.get("ok"):
            return _task_projection(operation, acknowledged.get("task") or task, reason=acknowledged.get("reason"))
        task = acknowledged["task"]
        attempt = int(task.get("attempt_count") or 1)
    elif task.get("state") == "leased":
        if str(task.get("lease_owner") or "") != lease_owner:
            return _task_projection(operation, task, reason="operation_already_leased")
        acknowledged = persistent_task_kernel.acknowledge(
            operation["task_id"], lease_owner=lease_owner, db_path=db_path
        )
        if not acknowledged.get("ok"):
            return _task_projection(operation, acknowledged.get("task") or task, reason=acknowledged.get("reason"))
        task = acknowledged["task"]
        attempt = int(task.get("attempt_count") or 1)
    elif task.get("state") != "acked":
        return _task_projection(operation, task, reason="task_state_not_executable")
    intent = _attempt_intent(operation, attempt)
    if mode == "submit":
        receipt = long_command_receipt.submit_or_reuse(
            intent,
            operation["command"],
            timeout_seconds=operation["timeout_seconds"],
            cwd=operation["cwd"],
        )
        if receipt.get("terminal"):
            return _settle(operation, receipt, db_path=db_path, result_state_root=result_state_root)
        return _task_projection(operation, task, long_command=receipt, reason="terminal_event_pending")
    if mode != "converge":
        return {**operation, "ok": False, "reason": "unsupported_operation_mode", "mode": mode}
    receipt = long_command_receipt.converge_or_reuse(
        intent,
        operation["command"],
        timeout_seconds=operation["timeout_seconds"],
        cwd=operation["cwd"],
    )
    return _settle(operation, receipt, db_path=db_path, result_state_root=result_state_root)


def validate() -> dict[str, Any]:
    plan = {
        "ok": True,
        "status": "ready",
        "plan_id": "plan:validate",
        "next_action": {
            "node_id": "owner:validate",
            "input_signature": "sig",
            "command_argv": ["python", "owner.py", "validate"],
            "automation_level": "A0",
            "timeout_seconds": 30,
        },
    }
    first = build_operation(plan, cwd=".")
    second = build_operation(plan, cwd=".")
    checks = [
        {"name": "operation_identity_is_signature_stable", "ok": first.get("operation_id") == second.get("operation_id")},
        {"name": "timeout_is_part_of_execution_signature", "ok": bool(first.get("execution_signature"))},
        {"name": "automatic_levels_are_bounded", "ok": AUTOMATIC_LEVELS == {"A0", "A1", "A2"}},
        {"name": "no_business_resubmit_contract", "ok": first.get("business_command_resubmit_allowed") is False},
    ]
    return {"schema": "business_environment.durable_executor.validate.v1", "ok": all(item["ok"] for item in checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Business environment durable operation coordinator")
    parser.add_argument("--db-path", default=str(DEFAULT_DB))
    parser.add_argument("--result-state-root", default=str(STATE_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    for name in ("plan", "submit", "follow", "consume", "converge"):
        child = sub.add_parser(name)
        child.add_argument("--plan-id", required=True)
        child.add_argument("--cwd", default=str(Path.cwd()))
        child.add_argument("--timeout-seconds", type=int, default=0)
    args = parser.parse_args(argv)
    if args.command == "validate":
        payload = validate()
    else:
        plan = load_plan(args.plan_id)
        if args.command == "plan":
            payload = build_operation(plan, cwd=args.cwd, timeout_seconds=args.timeout_seconds)
        else:
            payload = advance_operation(
                plan,
                mode=args.command,
                cwd=args.cwd,
                timeout_seconds=args.timeout_seconds,
                db_path=Path(args.db_path),
                result_state_root=Path(args.result_state_root),
            )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
