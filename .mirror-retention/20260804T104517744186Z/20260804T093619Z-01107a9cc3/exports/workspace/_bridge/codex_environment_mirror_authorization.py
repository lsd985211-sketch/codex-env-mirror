"""Scoped-authorization PEP for local mirror refresh.

Ownership: bind a challenge/grant to one Work Git and changed-path snapshot,
then journal the refresh readback through the shared authorization owner.
Non-goals: publish, release, stage restores, choose drift dispositions, or
replace mirror preflight and state-write gates.
State behavior: authorization state is owned exclusively by
``scoped_authorization``; mirror state remains owned by its existing owner.
Caller context: ``codex_environment_mirror`` before it acquires the refresh
operation lock and starts its first refresh side effect.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import scoped_authorization


OWNER = "codex_environment_mirror"
ACTION = "codex_environment_mirror.refresh"
PHASE = "mirror_refresh"


def refresh_scope(
    changed_paths: list[str],
    source_authority: Mapping[str, Any],
    *,
    thread_id: str,
) -> dict[str, Any]:
    work_git = source_authority.get("work_git") if isinstance(source_authority.get("work_git"), Mapping) else {}
    target = {
        "changed_paths": sorted({str(path).replace("\\\\", "/") for path in (changed_paths or []) if str(path)}),
        "work_git_head": str(work_git.get("worktree_head") or ""),
        "bare_head": str(work_git.get("bare_head") or ""),
    }
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


def authorization_plan(
    changed_paths: list[str], source_authority: Mapping[str, Any], *, thread_id: str,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    if not source_authority.get("ok"):
        return {"schema": "codex_environment_mirror.refresh_authorization_plan.v1", "ok": False, "reason": "work_git_release_not_ready", "source_authority": dict(source_authority)}
    work_git = source_authority.get("work_git") if isinstance(source_authority.get("work_git"), Mapping) else {}
    scope = refresh_scope(changed_paths, source_authority, thread_id=thread_id)
    challenge = scoped_authorization.create_challenge(
        scope,
        target_summary=f"local mirror refresh at Work Git {work_git.get('worktree_head') or 'unknown'}; publishing remains prohibited",
        state_root=state_root,
    )
    return {
        "schema": "codex_environment_mirror.refresh_authorization_plan.v1",
        "ok": bool(challenge.get("ok")), "scope": scope, "authorization_challenge": challenge,
    }


def consume(
    grant_ref: str, changed_paths: list[str], source_authority: Mapping[str, Any], *,
    thread_id: str, operation_id: str, state_root: Path | str | None = None,
) -> dict[str, Any]:
    return scoped_authorization.consume_grant(
        grant_ref, refresh_scope(changed_paths, source_authority, thread_id=thread_id),
        consumer_owner=OWNER, operation_id=operation_id, state_root=state_root,
    )


def effect_started(operation_id: str, *, state_root: Path | str | None = None) -> dict[str, Any]:
    return scoped_authorization.record_effect(operation_id, executor=OWNER, status="effect_started", state_root=state_root)


def effect_finished(
    operation_id: str, result: Mapping[str, Any], *, state_root: Path | str | None = None,
) -> dict[str, Any]:
    receipt = f"mirror-refresh:{result.get('snapshot_id', '')}:{result.get('reason', '')}"
    if not result.get("ok"):
        return scoped_authorization.record_effect(
            operation_id, executor=OWNER, status="effect_unknown", effect_receipt_ref=receipt,
            details={"phase": result.get("phase", ""), "reason": result.get("reason", "")}, state_root=state_root,
        )
    observed = scoped_authorization.record_effect(
        operation_id, executor=OWNER, status="effect_observed", effect_receipt_ref=receipt,
        details={"snapshot_id": result.get("snapshot_id", ""), "reused": bool(result.get("reused"))}, state_root=state_root,
    )
    if not observed.get("ok"):
        return observed
    return scoped_authorization.record_effect(
        operation_id, executor=OWNER, status="completed", effect_receipt_ref=receipt, state_root=state_root,
    )
