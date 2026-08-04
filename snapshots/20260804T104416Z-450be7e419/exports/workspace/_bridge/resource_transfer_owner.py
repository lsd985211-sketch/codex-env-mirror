#!/usr/bin/env python3
"""Resource transfer lifecycle owner, backed by the existing event store.

Ownership: transfer identity, duplicate reuse, state transitions, scoped grant
readback, and consumption of a long-command terminal receipt. Non-goals:
source discovery, scheduler due decisions, recurring retries, or network policy.
State behavior: persists transfer rows/events in shared.resource_event_store.
Caller context: a broker facade or one-shot maintenance convergence invocation.
"""

from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path
from typing import Any

from resource_transfer_contract import build_plan
from shared import long_command_receipt
from shared.resource_event_store import (
    claim_transfer,
    convergence_candidates,
    get_transfer,
    submit_transfer,
    transition_transfer,
)


OWNER = "resource_transfer_owner"


def _authorization_active(
    plan: dict[str, Any], state_root: Path | str | None = None
) -> dict[str, Any]:
    auth = (
        plan.get("authorization") if isinstance(plan.get("authorization"), dict) else {}
    )
    if not auth.get("active"):
        return {"ok": False, "reason": "authorization_inactive"}
    permit_ref = str(auth.get("permit_ref") or "")
    if not permit_ref:
        if auth.get("fixture_only"):
            return {"ok": True, "mode": "loopback_fixture"}
        return {"ok": False, "reason": "scoped_authorization_permit_required"}
    import scoped_authorization

    checked = scoped_authorization.introspect(permit_ref, state_root=state_root)
    reasons = set(str(item) for item in checked.get("reasons", []))
    continuation = (
        checked.get("permit_status") == "consumed"
        and checked.get("intent_status") == "active"
        and int(checked.get("permit_generation") or 0)
        == int(checked.get("current_generation") or 0)
        and reasons <= {"permit_consumed"}
    )
    active = bool(checked.get("active")) or continuation
    return {
        "ok": active,
        "mode": "scoped_authorization",
        "continuation": continuation,
        "detail": checked,
        "reason": "authorization_generation_fenced" if not active else "",
    }


def plan(**kwargs: Any) -> dict[str, Any]:
    return build_plan(**kwargs)


def submit(
    plan_value: dict[str, Any], *, db_path: Path, state_root: Path | str | None = None
) -> dict[str, Any]:
    checked = _authorization_active(plan_value, state_root=state_root)
    if not checked.get("ok"):
        return {
            "ok": False,
            "reason": checked.get("reason") or "authorization_inactive",
            "authorization": checked,
        }
    stored = submit_transfer(plan_value, db_path=db_path)
    return {**stored, "authorization": checked}


def _command(plan_value: dict[str, Any]) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).with_name("resource_transfer_worker.py")),
        "--plan-json",
        json.dumps(plan_value, ensure_ascii=False, sort_keys=True),
    ]


def _receipt_intent(transfer: dict[str, Any], generation: int | None = None) -> str:
    execution_generation = int(
        transfer.get("generation") if generation is None else generation
    )
    return (
        f"resource-transfer:{transfer['transfer_id']}:"
        f"{transfer['execution_signature']}:generation-{execution_generation}"
    )


def converge(
    transfer_id: str,
    *,
    db_path: Path,
    state_root: Path | str | None = None,
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    transfer = get_transfer(transfer_id, db_path=db_path)
    if not transfer:
        return {"ok": False, "reason": "transfer_not_found"}
    if transfer["state"] in {"completed", "failed", "cancelled"}:
        return {
            "ok": transfer["state"] == "completed",
            "reused": True,
            "transfer": transfer,
        }
    checked = _authorization_active(transfer["plan"], state_root=state_root)
    if not checked.get("ok"):
        blocked = transition_transfer(
            transfer_id,
            expected={"queued", "leased", "running", "paused"},
            state="blocked",
            detail={"reason": checked.get("reason")},
            error_class="authorization_fenced",
            db_path=db_path,
        )
        return {
            **blocked,
            "ok": False,
            "reason": str(checked.get("reason") or "authorization_fenced"),
        }
    if transfer["state"] == "queued":
        claimed = claim_transfer(transfer_id, lease_owner=OWNER, db_path=db_path)
        if not claimed.get("ok"):
            return claimed
        transfer = claimed["transfer"]
    if transfer["state"] == "leased":
        moved = transition_transfer(
            transfer_id,
            expected={"leased"},
            state="running",
            detail={"authorization": checked.get("mode")},
            expected_generation=int(transfer["generation"]),
            db_path=db_path,
        )
        if not moved.get("ok"):
            return moved
        transfer = moved["transfer"]
    if transfer["state"] != "running":
        return {"ok": False, "reason": "transfer_not_runnable", "transfer": transfer}
    authorization_runtime: dict[str, Any] = {}
    permit_ref = str(
        (transfer["plan"].get("authorization") or {}).get("permit_ref") or ""
    )
    if permit_ref:
        import scoped_authorization

        before = scoped_authorization.introspect(permit_ref, state_root=state_root)
        consumed = scoped_authorization.consume_permit(
            permit_ref,
            executor=OWNER,
            operation_id=str(transfer["operation_id"]),
            idempotency_key=str(transfer["execution_signature"]),
            state_root=state_root,
        )
        if not consumed.get("ok"):
            blocked = transition_transfer(
                transfer_id,
                expected={"running"},
                state="blocked",
                detail={"reason": consumed.get("reason")},
                error_class="authorization_consume_failed",
                expected_generation=int(transfer["generation"]),
                db_path=db_path,
            )
            return {
                **blocked,
                "ok": False,
                "reason": str(consumed.get("reason") or "authorization_consume_failed"),
            }
        authorization_runtime = {
            "permit_ref": permit_ref,
            "intent_ref": str(before.get("intent_ref") or ""),
            "generation": int(
                before.get("current_generation") or before.get("permit_generation") or 0
            ),
            "state_root": str(Path(state_root).resolve()) if state_root else "",
        }
    plan_value = {
        **transfer["plan"],
        "runtime": {
            "db_path": str(db_path),
            "generation": int(transfer["generation"]),
            "authorization": authorization_runtime,
        },
    }
    receipt = long_command_receipt.converge_or_reuse(
        _receipt_intent(transfer),
        _command(plan_value),
        timeout_seconds=timeout_seconds,
        cwd=str(Path(__file__).resolve().parent),
        detail="full",
    )
    raw = str(receipt.get("stdout") or "").strip()
    try:
        worker = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        worker = {}
    if receipt.get("ok") and worker.get("ok"):
        settled = transition_transfer(
            transfer_id,
            expected={"running"},
            state="completed",
            receipt={"long_command": receipt, "worker": worker},
            detail={"bytes": worker.get("bytes", 0)},
            expected_generation=int(transfer["generation"]),
            db_path=db_path,
        )
        if settled.get("reason") == "transfer_generation_fenced":
            return {
                "ok": False,
                "reason": "worker_terminal_fenced",
                "transfer": get_transfer(transfer_id, db_path=db_path),
                "receipt": receipt,
            }
        return settled
    reason = str(worker.get("reason") or receipt.get("reason") or "transfer_failed")
    current = get_transfer(transfer_id, db_path=db_path) or {}
    if current.get("state") == "paused":
        return {
            "ok": True,
            "paused": True,
            "reason": reason,
            "transfer": current,
            "receipt": receipt,
        }
    if current.get("state") == "cancelling":
        return transition_transfer(
            transfer_id,
            expected={"cancelling"},
            state="cancelled",
            receipt={"long_command": receipt, "worker": worker},
            detail={"reason": reason},
            db_path=db_path,
        )
    target = (
        "blocked"
        if reason in {"insufficient_disk_space", "authorization_generation_fenced"}
        else "failed"
    )
    settled = transition_transfer(
        transfer_id,
        expected={"running"},
        state=target,
        receipt={"long_command": receipt, "worker": worker},
        detail={"reason": reason},
        error_class=reason,
        expected_generation=int(transfer["generation"]),
        db_path=db_path,
    )
    return {**settled, "ok": False, "reason": reason}


def pause(transfer_id: str, *, db_path: Path) -> dict[str, Any]:
    return transition_transfer(
        transfer_id,
        expected={"queued", "leased", "running"},
        state="paused",
        detail={"reason": "user_pause"},
        db_path=db_path,
    )


def resume(transfer_id: str, *, db_path: Path) -> dict[str, Any]:
    return transition_transfer(
        transfer_id,
        expected={"paused", "blocked"},
        state="queued",
        detail={"reason": "user_resume"},
        db_path=db_path,
    )


def cancel(transfer_id: str, *, db_path: Path) -> dict[str, Any]:
    current = get_transfer(transfer_id, db_path=db_path)
    if current and current.get("state") == "running":
        return transition_transfer(
            transfer_id,
            expected={"running"},
            state="cancelling",
            detail={"reason": "user_cancel"},
            db_path=db_path,
        )
    return transition_transfer(
        transfer_id,
        expected={"queued", "leased", "paused", "blocked"},
        state="cancelled",
        detail={"reason": "user_cancel"},
        db_path=db_path,
    )


def status(transfer_id: str, *, db_path: Path) -> dict[str, Any]:
    transfer = get_transfer(transfer_id, db_path=db_path)
    return {
        "schema": "resource_transfer_owner.status.v1",
        "ok": bool(transfer),
        "transfer": transfer,
        "reason": "" if transfer else "transfer_not_found",
    }


def reconcile(
    *, db_path: Path, state_root: Path | str | None = None, limit: int = 20
) -> dict[str, Any]:
    """Coordinate expired leases and existing receipts; never schedule retries."""
    actions: list[dict[str, Any]] = []
    for transfer in convergence_candidates(db_path=db_path, limit=limit):
        transfer_id = str(transfer.get("transfer_id") or "")
        if transfer.get("state") == "leased":
            result = transition_transfer(
                transfer_id,
                expected={"leased"},
                state="queued",
                detail={"reason": "lease_expired_before_execution"},
                expected_generation=int(transfer.get("generation") or 0),
                db_path=db_path,
            )
            actions.append(
                {
                    "transfer_id": transfer_id,
                    "action": "requeue_unstarted",
                    "ok": bool(result.get("ok")),
                }
            )
            continue
        receipt_generation = int(transfer.get("generation") or 0)
        if transfer.get("state") == "cancelling":
            receipt_generation = max(0, receipt_generation - 1)
        intent = _receipt_intent(transfer, receipt_generation)
        receipt = long_command_receipt.status(
            long_command_receipt.task_id_for_intent(intent)
        )
        if receipt.get("terminal") and isinstance(receipt.get("exit_code"), int):
            if transfer.get("state") == "cancelling":
                result = transition_transfer(
                    transfer_id,
                    expected={"cancelling"},
                    state="cancelled",
                    receipt={"long_command": receipt},
                    detail={"reason": "cancelled_worker_terminal_consumed"},
                    expected_generation=int(transfer.get("generation") or 0),
                    db_path=db_path,
                )
            else:
                result = converge(
                    transfer_id,
                    db_path=db_path,
                    state_root=state_root,
                    timeout_seconds=max(1, int(receipt.get("timeout_seconds") or 90)),
                )
            actions.append(
                {
                    "transfer_id": transfer_id,
                    "action": "consume_terminal_receipt",
                    "ok": bool(result.get("ok")),
                    "state": (result.get("transfer") or {}).get("state", ""),
                }
            )
        else:
            actions.append(
                {
                    "transfer_id": transfer_id,
                    "action": "preserve_existing_execution",
                    "ok": True,
                    "receipt_status": receipt.get("status", "missing"),
                }
            )
    return {
        "schema": "resource_transfer_owner.reconcile.v1",
        "ok": all(item["ok"] for item in actions),
        "count": len(actions),
        "actions": actions,
        "scheduler_role": "wake_only",
        "starts_new_transfer": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Durable resource transfer lifecycle owner"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    from shared.resource_event_store import RECORD_INDEX_PATH

    reconcile_parser = sub.add_parser("reconcile")
    reconcile_parser.add_argument("--db-path", default=str(RECORD_INDEX_PATH))
    reconcile_parser.add_argument("--state-root", default="")
    reconcile_parser.add_argument("--limit", type=int, default=20)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--transfer-id", required=True)
    status_parser.add_argument("--db-path", required=True)
    args = parser.parse_args()
    payload = (
        reconcile(
            db_path=Path(args.db_path),
            state_root=args.state_root or None,
            limit=args.limit,
        )
        if args.command == "reconcile"
        else status(args.transfer_id, db_path=Path(args.db_path))
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
