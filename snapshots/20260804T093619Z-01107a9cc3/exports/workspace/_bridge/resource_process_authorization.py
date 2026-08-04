"""Scoped-authorization PEP for resource-process cleanup.

Ownership: bind a user challenge/grant to one fresh, non-protected cleanup
selection and record the owner readback in the shared effect journal.
Non-goals: discover processes, classify risk, stop protected processes, or
create a second authorization store.
State behavior: only ``scoped_authorization`` writes challenge, consumption,
and effect records; the doctor owns process observations and termination.
Caller context: ``resource_process_doctor`` immediately before safe cleanup.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import scoped_authorization


OWNER = "resource_process_doctor"
ACTION = "resource_process.safe_orphan_cleanup"
PHASE = "resource_process_cleanup"


def _target(payload: Mapping[str, Any]) -> dict[str, Any]:
    selected = payload.get("selected") if isinstance(payload.get("selected"), list) else []
    candidates = [
        {
            "group": str(item.get("group") or ""),
            "pid": int(item.get("pid") or 0),
            "start_time": str(item.get("start_time") or ""),
            "selection_mode": str(item.get("selection_mode") or ""),
        }
        for item in selected
        if isinstance(item, Mapping)
    ]
    return {
        "safe_apply": bool(payload.get("safe_apply")),
        "include_protected": bool(payload.get("include_protected")),
        "min_age_minutes": float(payload.get("min_age_minutes") or 0),
        "groups": list(payload.get("selected_groups") or []),
        "candidates": sorted(candidates, key=lambda item: (item["group"], item["pid"])),
    }


def cleanup_scope(payload: Mapping[str, Any], *, thread_id: str) -> dict[str, Any]:
    target = _target(payload)
    source_signature = hashlib.sha256(
        json.dumps(target, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return scoped_authorization.build_scope(
        thread_id=thread_id,
        action=ACTION,
        target=target,
        risk="R3",
        phase=PHASE,
        source_signature=source_signature,
        requested_by_owner=OWNER,
    )


def authorization_plan(payload: Mapping[str, Any], *, thread_id: str, state_root: Path | str | None = None) -> dict[str, Any]:
    if not payload.get("safe_apply") or payload.get("include_protected"):
        return {"schema": "resource_process.cleanup_authorization_plan.v1", "ok": False, "reason": "safe_non_protected_cleanup_required"}
    target = _target(payload)
    scope = cleanup_scope(payload, thread_id=thread_id)
    challenge = scoped_authorization.create_challenge(
        scope,
        target_summary=f"safe cleanup of {len(target.get('candidates') or [])} revalidated non-protected orphan roots",
        state_root=state_root,
    )
    return {
        "schema": "resource_process.cleanup_authorization_plan.v1",
        "ok": bool(challenge.get("ok")),
        "scope": scope,
        "authorization_challenge": challenge,
    }


def consume(
    grant_ref: str,
    payload: Mapping[str, Any],
    *,
    thread_id: str,
    operation_id: str,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    return scoped_authorization.consume_grant(
        grant_ref,
        cleanup_scope(payload, thread_id=thread_id),
        consumer_owner=OWNER,
        operation_id=operation_id,
        state_root=state_root,
    )


def effect_started(operation_id: str, *, state_root: Path | str | None = None) -> dict[str, Any]:
    return scoped_authorization.record_effect(operation_id, executor=OWNER, status="effect_started", state_root=state_root)


def effect_finished(
    operation_id: str,
    result: Mapping[str, Any],
    *,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    selected = result.get("selected") if isinstance(result.get("selected"), list) else []
    receipt = f"resource-process-cleanup:{len(selected)}:{bool(result.get('post_validation_ok'))}"
    if not result.get("cleanup_ok") or not result.get("post_validation_ok"):
        return scoped_authorization.record_effect(
            operation_id, executor=OWNER, status="effect_unknown", effect_receipt_ref=receipt,
            details={"cleanup_ok": bool(result.get("cleanup_ok")), "post_validation_ok": result.get("post_validation_ok")},
            state_root=state_root,
        )
    observed = scoped_authorization.record_effect(
        operation_id, executor=OWNER, status="effect_observed", effect_receipt_ref=receipt,
        details={"selected_count": len(selected), "post_validation_ok": True}, state_root=state_root,
    )
    if not observed.get("ok"):
        return observed
    return scoped_authorization.record_effect(
        operation_id, executor=OWNER, status="completed", effect_receipt_ref=receipt, state_root=state_root,
    )
