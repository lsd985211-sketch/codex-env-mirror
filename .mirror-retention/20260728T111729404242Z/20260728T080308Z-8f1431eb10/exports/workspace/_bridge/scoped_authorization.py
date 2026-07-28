#!/usr/bin/env python3
"""Own intent, decision, permit, consumption, and effect evidence.

Ownership: exact user-evidence challenges, authorization intents, embedded
policy decisions, attenuated per-turn/per-run permits, atomic consumption,
revocation fencing, and minimal operation/effect journals.
Non-goals: deciding business actions, inferring approval from prose, replacing
owner confirmation tokens, state-writer leases, queues, or durable executors.
State behavior: writes only challenge/grant/consumption receipts below the
configured external runtime root; source repositories and business state are
never modified.
Caller context: workflow challenge producers and high-impact owners immediately
before their first side effect. Callers pass stable references, never copied
authorization claims.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from scoped_authorization_environment import (
    classify_environment_change,
    environment_signature,
    normalize_environment_snapshot,
)
import authorization_environment_provider


DEFAULT_STATE_ROOT = Path.home() / ".codex-app" / "runtime" / "scoped-authorizations"
DEFAULT_TTL_SECONDS = 900
INTENT_TYPES = {"one_time", "long_lived", "permanent_default", "system_default"}
ACTIVE_INTENT_STATES = {"active"}
ACTIVE_PERMIT_STATES = {"issued"}
TERMINAL_OPERATION_STATES = {"completed", "cancelled", "compensation_required"}
SCOPE_FIELDS = (
    "thread_id",
    "action",
    "target_fingerprint",
    "risk",
    "phase",
    "source_signature",
    "requested_by_owner",
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def target_fingerprint(target: Any) -> str:
    return digest(target)


def authorization_turn_id(message_ref: str, message_hash: str) -> str:
    """Return a stable identity for one user-message authorization turn."""

    return f"turn:{digest({'message_ref': str(message_ref), 'message_hash': str(message_hash)})[:32]}"


def normalize_rich_scope(scope: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy and rich scopes into one deterministic policy input."""

    normalized: dict[str, Any] = {
        field: str(scope.get(field) or "").strip() for field in SCOPE_FIELDS
    }
    normalized.update({
        "type": str(scope.get("type") or normalized.get("action") or "operation").strip(),
        "actions": sorted({str(item).strip() for item in (scope.get("actions") or [normalized.get("action")]) if str(item).strip()}),
        "locations": sorted({str(item).strip() for item in (scope.get("locations") or []) if str(item).strip()}),
        "target_identifiers": sorted({str(item).strip() for item in (scope.get("target_identifiers") or [normalized.get("target_fingerprint")]) if str(item).strip()}),
        "data_classes": sorted({str(item).strip() for item in (scope.get("data_classes") or []) if str(item).strip()}),
        "privileges": sorted({str(item).strip() for item in (scope.get("privileges") or []) if str(item).strip()}),
        "risk_ceiling": str(scope.get("risk_ceiling") or normalized.get("risk") or "").strip(),
        "allowed_executors": sorted({str(item).strip() for item in (scope.get("allowed_executors") or [normalized.get("requested_by_owner")]) if str(item).strip()}),
        "prohibitions": sorted({str(item).strip() for item in (scope.get("prohibitions") or []) if str(item).strip()}),
    })
    return normalized


def rich_scope_signature(scope: dict[str, Any]) -> str:
    return digest(normalize_rich_scope(scope))


def build_scope(
    *,
    thread_id: str,
    action: str,
    target: Any,
    risk: str,
    phase: str,
    source_signature: str,
    requested_by_owner: str,
) -> dict[str, str]:
    return {
        "thread_id": str(thread_id or "").strip(),
        "action": str(action or "").strip(),
        "target_fingerprint": target_fingerprint(target),
        "risk": str(risk or "").strip(),
        "phase": str(phase or "").strip(),
        "source_signature": str(source_signature or "").strip(),
        "requested_by_owner": str(requested_by_owner or "").strip(),
    }


def scope_signature(scope: dict[str, Any]) -> str:
    return digest({field: str(scope.get(field) or "") for field in SCOPE_FIELDS})


def _state_root(value: Path | str | None) -> Path:
    return Path(value or os.environ.get("CODEX_SCOPED_AUTHORIZATION_ROOT") or DEFAULT_STATE_ROOT).expanduser().resolve()


def _safe_ref(ref: str | Path, root: Path, kind: str) -> Path:
    raw = str(ref or "").strip()
    if raw.startswith("scoped-authorization:"):
        _, ref_kind, identifier = raw.split(":", 2)
        if ref_kind != kind or not identifier.replace("-", "").isalnum():
            raise ValueError("authorization_ref_invalid")
        path = root / f"{kind}s" / f"{identifier}.json"
    else:
        path = Path(raw).expanduser().resolve()
    if root not in path.parents or path.parent.name != f"{kind}s" or path.suffix != ".json":
        raise ValueError("authorization_ref_outside_state_root")
    return path


def _ref(kind: str, identifier: str) -> str:
    return f"scoped-authorization:{kind}:{identifier}"


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "authorization_receipt_unreadable", "error": type(exc).__name__}
    return payload if isinstance(payload, dict) else {"ok": False, "reason": "authorization_receipt_not_object"}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _event(root: Path, *, event_type: str, intent_id: str = "", generation: int = 0, details: dict[str, Any] | None = None) -> dict[str, Any]:
    moment = now_utc()
    event_id = digest({
        "event_type": event_type,
        "intent_id": intent_id,
        "generation": generation,
        "details": details or {},
        "at": iso(moment),
        "nonce": secrets.token_hex(8),
    })[:32]
    payload = {
        "schema": "scoped_authorization.event.v2",
        "ok": True,
        "event_id": event_id,
        "event_ref": _ref("event", event_id),
        "event_type": event_type,
        "intent_id": intent_id,
        "generation": generation,
        "details": details or {},
        "recorded_at": iso(moment),
    }
    _write(root / "events" / f"{event_id}.json", payload)
    return payload


def _create_intent_unlocked(
    root: Path,
    scope: dict[str, Any],
    *,
    intent_type: str,
    authorizer: str,
    subject: str,
    evidence_ref: str,
    turn_id: str = "",
    authority_ref: str = "",
    allowed_actor_classes: list[str] | None = None,
    delegation_allowed: bool = False,
    max_delegation_depth: int = 0,
    termination: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if intent_type not in INTENT_TYPES:
        return {"ok": False, "reason": "authorization_intent_type_invalid"}
    normalized = normalize_rich_scope(scope)
    if any(not normalized.get(field) for field in SCOPE_FIELDS):
        return {"ok": False, "reason": "authorization_scope_incomplete"}
    if intent_type == "system_default" and not str(authority_ref or "").strip():
        return {"ok": False, "reason": "system_default_authority_ref_required"}
    intent_id = digest({
        "scope": normalized, "intent_type": intent_type, "authorizer": authorizer,
        "subject": subject, "evidence_ref": evidence_ref, "turn_id": turn_id,
        "authority_ref": authority_ref, "nonce": secrets.token_hex(16),
    })[:32]
    payload = {
        "schema": "scoped_authorization.intent.v2",
        "ok": True,
        "intent_id": intent_id,
        "intent_ref": _ref("intent", intent_id),
        "intent_type": intent_type,
        "authorizer": str(authorizer or "").strip(),
        "subject": str(subject or authorizer or "").strip(),
        "evidence_ref": str(evidence_ref or "").strip(),
        "authorization_turn_id": str(turn_id or "").strip(),
        "authority_ref": str(authority_ref or "").strip(),
        "scope": normalized,
        "scope_signature": rich_scope_signature(normalized),
        "allowed_actor_classes": sorted({str(x).strip() for x in (allowed_actor_classes or ["codex"]) if str(x).strip()}),
        "delegation_allowed": bool(delegation_allowed),
        "max_delegation_depth": max(0, int(max_delegation_depth)),
        "termination": termination or {},
        "generation": 1,
        "status": "active",
        "created_at": iso(now_utc()),
    }
    _write(root / "intents" / f"{intent_id}.json", payload)
    _event(root, event_type="intent_created", intent_id=intent_id, generation=1, details={"intent_type": intent_type})
    return payload


def create_intent(
    scope: dict[str, Any],
    *,
    intent_type: str,
    authorizer: str,
    subject: str = "",
    evidence_ref: str,
    turn_id: str = "",
    authority_ref: str = "",
    allowed_actor_classes: list[str] | None = None,
    delegation_allowed: bool = False,
    max_delegation_depth: int = 0,
    termination: dict[str, Any] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    root = _state_root(state_root)
    with _locked(root):
        return _create_intent_unlocked(
            root, scope, intent_type=intent_type, authorizer=authorizer, subject=subject,
            evidence_ref=evidence_ref, turn_id=turn_id, authority_ref=authority_ref,
            allowed_actor_classes=allowed_actor_classes, delegation_allowed=delegation_allowed,
            max_delegation_depth=max_delegation_depth, termination=termination,
        )


def ensure_system_intent(
    scope: dict[str, Any], *, authority_ref: str, subject: str,
    allowed_actor_classes: list[str] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Resolve one registry-backed system intent without automatic expansion.

    The first exact authority/scope pair may materialize its intent. Once an
    authority has an intent, changed authorization semantics require explicit
    reconciliation instead of silently creating a broader replacement.
    """

    authority = str(authority_ref or "").strip()
    if not authority:
        return {"ok": False, "reason": "system_default_authority_ref_required"}
    normalized = normalize_rich_scope(scope)
    signature = rich_scope_signature(normalized)
    root = _state_root(state_root)
    with _locked(root):
        matches: list[dict[str, Any]] = []
        for path in sorted((root / "intents").glob("*.json")):
            intent = _read(path)
            if intent.get("intent_type") == "system_default" and intent.get("authority_ref") == authority:
                matches.append(intent)
        exact = next((item for item in matches if item.get("scope_signature") == signature), None)
        if exact:
            return {**exact, "reused": True}
        if matches:
            return {
                "schema": "scoped_authorization.system_intent_reconciliation.v2",
                "ok": False,
                "reason": "authorization_reconciliation_required",
                "authority_ref": authority,
                "requested_scope_signature": signature,
                "existing_intent_refs": [item.get("intent_ref", "") for item in matches],
                "automatic_expansion_allowed": False,
            }
        return _create_intent_unlocked(
            root, normalized, intent_type="system_default", authorizer="system_authority",
            subject=subject, evidence_ref=authority, authority_ref=authority,
            allowed_actor_classes=allowed_actor_classes or ["scheduler"],
            delegation_allowed=False, max_delegation_depth=0,
        )


def _scope_attenuation_issues(parent: dict[str, Any], requested: dict[str, Any]) -> list[str]:
    p = normalize_rich_scope(parent)
    r = normalize_rich_scope(requested)
    issues: list[str] = []
    for field in SCOPE_FIELDS:
        if str(r.get(field) or "") != str(p.get(field) or ""):
            issues.append(field)
    for field in ("actions", "locations", "target_identifiers", "data_classes", "privileges", "allowed_executors"):
        if not set(r.get(field) or []).issubset(set(p.get(field) or [])):
            issues.append(field)
    if not set(p.get("prohibitions") or []).issubset(set(r.get("prohibitions") or [])):
        issues.append("prohibitions")
    if str(r.get("risk_ceiling") or "") != str(p.get("risk_ceiling") or ""):
        issues.append("risk_ceiling")
    return sorted(set(issues))


def _reconcile_intent_environment_unlocked(
    root: Path, intent: dict[str, Any], environment: dict[str, Any]
) -> dict[str, Any]:
    """Bind or incrementally reconcile one intent's authoritative environment."""

    current_signature = environment_signature(environment)
    baseline = intent.get("environment_snapshot")
    if not isinstance(baseline, dict) or not baseline:
        intent["environment_snapshot"] = environment
        intent["environment_signature"] = current_signature
        intent["updated_at"] = iso(now_utc())
        _write(root / "intents" / f"{intent['intent_id']}.json", intent)
        return {
            "schema": "scoped_authorization.environment_reconciliation.v1",
            "ok": True,
            "classification": "equivalent",
            "automatic_action": "bind_baseline",
            "current_signature": current_signature,
            "generation": intent["generation"],
        }
    change = classify_environment_change(baseline, environment)
    classification = str(change.get("classification") or "unavailable")
    if classification == "equivalent":
        intent["environment_snapshot"] = environment
        intent["environment_signature"] = current_signature
        intent.pop("pending_environment_signature", None)
        intent.pop("pending_environment_classification", None)
        intent["updated_at"] = iso(now_utc())
        _write(root / "intents" / f"{intent['intent_id']}.json", intent)
        return {**change, "ok": True, "generation": intent["generation"]}
    if classification == "expansion_required":
        if intent.get("pending_environment_signature") != current_signature:
            intent["generation"] = int(intent.get("generation") or 0) + 1
            intent["pending_environment_signature"] = current_signature
            intent["pending_environment_classification"] = classification
            intent["updated_at"] = iso(now_utc())
            _write(root / "intents" / f"{intent['intent_id']}.json", intent)
            _event(
                root, event_type="environment_reconciliation_required",
                intent_id=intent["intent_id"], generation=intent["generation"],
                details={"environment_signature": current_signature, "changed_sources": change.get("changed_sources", [])},
            )
        return {**change, "ok": False, "generation": intent["generation"]}
    if intent.get("environment_signature") != current_signature:
        intent["generation"] = int(intent.get("generation") or 0) + 1
        intent["environment_snapshot"] = environment
        intent["environment_signature"] = current_signature
        intent["environment_gate"] = classification
        intent.pop("pending_environment_signature", None)
        intent.pop("pending_environment_classification", None)
        intent["updated_at"] = iso(now_utc())
        _write(root / "intents" / f"{intent['intent_id']}.json", intent)
        _event(
            root, event_type="environment_fenced",
            intent_id=intent["intent_id"], generation=intent["generation"],
            details={"classification": classification, "environment_signature": current_signature},
        )
    return {**change, "ok": False, "generation": intent["generation"]}


def _decision_unlocked(
    root: Path, intent: dict[str, Any], requested_scope: dict[str, Any], *,
    actor_chain: list[dict[str, str]], executor: str, audience: str,
    workflow_semantic_hash: str, runtime_context: dict[str, Any] | None = None,
    environment_snapshot: dict[str, Any] | None = None,
    explicit_denies: list[str] | None = None,
) -> dict[str, Any]:
    denies = [str(x) for x in (explicit_denies or []) if str(x)]
    if intent.get("status") not in ACTIVE_INTENT_STATES:
        denies.append(f"intent_{intent.get('status') or 'inactive'}")
    issues = _scope_attenuation_issues(intent.get("scope") or {}, requested_scope)
    if issues:
        denies.append("scope_not_attenuated")
    if executor not in set((intent.get("scope") or {}).get("allowed_executors") or []):
        denies.append("executor_not_allowed")
    if audience != executor:
        denies.append("audience_executor_mismatch")
    actor_classes = [str(item.get("class") or "") for item in actor_chain if isinstance(item, dict)]
    if not actor_classes or any(x not in set(intent.get("allowed_actor_classes") or []) for x in actor_classes):
        denies.append("actor_class_not_allowed")
    if len(actor_chain) > 1 and (not intent.get("delegation_allowed") or len(actor_chain) - 1 > int(intent.get("max_delegation_depth") or 0)):
        denies.append("delegation_not_allowed")
    if not workflow_semantic_hash:
        denies.append("workflow_semantic_hash_required")
    environment = normalize_environment_snapshot(environment_snapshot or {
        "workflow_semantic_hash": workflow_semantic_hash,
        "authorization_semantic_signature": workflow_semantic_hash,
        "sources": {
            "workflow": {"signature": workflow_semantic_hash, "authority_ref": "workflow_semantic_hash"},
            "runtime_context": {"signature": digest(runtime_context or {}), "authority_ref": "caller_runtime_context"},
        },
    })
    environment_reconciliation = _reconcile_intent_environment_unlocked(root, intent, environment)
    if environment_reconciliation.get("classification") == "expansion_required":
        denies.append("authorization_reconciliation_required")
    elif not environment_reconciliation.get("ok"):
        denies.append("authorization_environment_fenced")
    if environment["workflow_semantic_hash"] != workflow_semantic_hash:
        denies.append("environment_workflow_semantic_hash_mismatch")
    unhealthy = [
        name for name, item in environment["sources"].items()
        if item.get("authorization_effect") in {"tighten", "incompatible", "unavailable"}
        or item.get("status") in {"incompatible", "unavailable"}
    ]
    expanding = [name for name, item in environment["sources"].items() if item.get("authorization_effect") == "expand"]
    if unhealthy:
        denies.append("authorization_environment_not_safe")
    if expanding:
        denies.append("authorization_reconciliation_required")
    decision_id = digest({
        "intent_id": intent.get("intent_id"), "generation": intent.get("generation"),
        "requested_scope": normalize_rich_scope(requested_scope), "actor_chain": actor_chain,
        "executor": executor, "audience": audience, "workflow_semantic_hash": workflow_semantic_hash,
        "runtime_context": runtime_context or {}, "environment_signature": environment_signature(environment),
        "denies": sorted(set(denies)),
    })[:32]
    decision = {
        "schema": "authorization_decision.v1",
        "ok": not denies,
        "decision": "allow" if not denies else "deny",
        "decision_id": decision_id,
        "decision_ref": _ref("decision", decision_id),
        "intent_ref": intent.get("intent_ref", ""),
        "generation": int(intent.get("generation") or 0),
        "requested_scope": normalize_rich_scope(requested_scope),
        "requested_scope_signature": rich_scope_signature(requested_scope),
        "actor_chain": actor_chain,
        "executor": executor,
        "audience": audience,
        "workflow_semantic_hash": workflow_semantic_hash,
        "runtime_context": runtime_context or {},
        "environment_snapshot": environment,
        "environment_signature": environment_signature(environment),
        "environment_unsafe_sources": unhealthy,
        "environment_expansion_sources": expanding,
        "environment_reconciliation": environment_reconciliation,
        "determining_denies": sorted(set(denies)),
        "scope_mismatch_fields": issues,
        "missing_authorization_delta": issues,
        "evaluated_at": iso(now_utc()),
    }
    _write(root / "decisions" / f"{decision_id}.json", decision)
    return decision


def decide(
    intent_ref: str, requested_scope: dict[str, Any], *, actor_chain: list[dict[str, str]],
    executor: str, audience: str, workflow_semantic_hash: str,
    runtime_context: dict[str, Any] | None = None, environment_snapshot: dict[str, Any] | None = None,
    explicit_denies: list[str] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    root = _state_root(state_root)
    try:
        path = _safe_ref(intent_ref, root, "intent")
    except ValueError as exc:
        return {"ok": False, "decision": "deny", "reason": str(exc)}
    with _locked(root):
        intent = _read(path)
        if not intent.get("ok"):
            return intent
        return _decision_unlocked(root, intent, requested_scope, actor_chain=actor_chain, executor=executor, audience=audience, workflow_semantic_hash=workflow_semantic_hash, runtime_context=runtime_context, environment_snapshot=environment_snapshot, explicit_denies=explicit_denies)


def _issue_permit_unlocked(
    root: Path, intent: dict[str, Any], requested_scope: dict[str, Any], *,
    actor_chain: list[dict[str, str]], executor: str, audience: str, operation_id: str,
    workflow_semantic_hash: str, authorization_turn: str = "", automation_run_id: str = "",
    runtime_context: dict[str, Any] | None = None, environment_snapshot: dict[str, Any] | None = None,
    explicit_denies: list[str] | None = None,
) -> dict[str, Any]:
    if bool(authorization_turn) == bool(automation_run_id):
        return {"ok": False, "reason": "exactly_one_turn_or_automation_run_required"}
    decision = _decision_unlocked(root, intent, requested_scope, actor_chain=actor_chain, executor=executor, audience=audience, workflow_semantic_hash=workflow_semantic_hash, runtime_context=runtime_context, environment_snapshot=environment_snapshot, explicit_denies=explicit_denies)
    if not decision.get("ok"):
        return {"ok": False, "reason": "authorization_decision_denied", "decision": decision}
    permit_id = digest({
        "intent_id": intent["intent_id"], "generation": intent["generation"],
        "operation_id": operation_id, "authorization_turn": authorization_turn,
        "automation_run_id": automation_run_id, "scope": decision["requested_scope_signature"],
        "executor": executor, "audience": audience, "workflow_semantic_hash": workflow_semantic_hash,
    })[:32]
    path = root / "permits" / f"{permit_id}.json"
    existing = _read(path) if path.exists() else {}
    if existing.get("ok"):
        return existing
    permit = {
        "schema": "scoped_authorization.permit.v2", "ok": True,
        "permit_id": permit_id, "permit_ref": _ref("permit", permit_id),
        "intent_ref": intent["intent_ref"], "decision_ref": decision["decision_ref"],
        "generation": intent["generation"], "scope": decision["requested_scope"],
        "scope_signature": decision["requested_scope_signature"], "subject": intent["subject"],
        "actor_chain": actor_chain, "executor": executor, "audience": audience,
        "operation_id": operation_id, "authorization_turn_id": authorization_turn,
        "automation_run_id": automation_run_id, "workflow_semantic_hash": workflow_semantic_hash,
        "environment_snapshot": decision["environment_snapshot"],
        "environment_signature": decision["environment_signature"],
        "status": "issued", "issued_at": iso(now_utc()),
    }
    _write(path, permit)
    _event(root, event_type="permit_issued", intent_id=intent["intent_id"], generation=intent["generation"], details={"permit_ref": permit["permit_ref"], "operation_id": operation_id})
    return permit


def issue_permit(
    intent_ref: str, requested_scope: dict[str, Any], *, actor_chain: list[dict[str, str]],
    executor: str, audience: str, operation_id: str, workflow_semantic_hash: str,
    authorization_turn: str = "", automation_run_id: str = "",
    runtime_context: dict[str, Any] | None = None, environment_snapshot: dict[str, Any] | None = None,
    explicit_denies: list[str] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    root = _state_root(state_root)
    try:
        path = _safe_ref(intent_ref, root, "intent")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    with _locked(root):
        intent = _read(path)
        if not intent.get("ok"):
            return intent
        return _issue_permit_unlocked(root, intent, requested_scope, actor_chain=actor_chain, executor=executor, audience=audience, operation_id=operation_id, workflow_semantic_hash=workflow_semantic_hash, authorization_turn=authorization_turn, automation_run_id=automation_run_id, runtime_context=runtime_context, environment_snapshot=environment_snapshot, explicit_denies=explicit_denies)


def _introspect_permit_unlocked(
    root: Path, permit: dict[str, Any], current_environment_snapshot: dict[str, Any] | None = None
) -> dict[str, Any]:
    try:
        intent_path = _safe_ref(str(permit.get("intent_ref") or ""), root, "intent")
    except ValueError as exc:
        return {"ok": False, "active": False, "reason": str(exc)}
    intent = _read(intent_path)
    if not intent.get("ok"):
        return {"ok": False, "active": False, "reason": "authorization_intent_unreadable"}
    reasons: list[str] = []
    if permit.get("status") not in ACTIVE_PERMIT_STATES:
        reasons.append(f"permit_{permit.get('status') or 'inactive'}")
    if intent.get("status") not in ACTIVE_INTENT_STATES:
        reasons.append(f"intent_{intent.get('status') or 'inactive'}")
    if int(permit.get("generation") or 0) != int(intent.get("generation") or 0):
        reasons.append("authorization_generation_fenced")
    environment_change = classify_environment_change(
        permit.get("environment_snapshot") or {},
        current_environment_snapshot or permit.get("environment_snapshot") or {},
    )
    if environment_change["classification"] != "equivalent":
        reasons.append(f"authorization_environment_{environment_change['classification']}")
    return {
        "schema": "scoped_authorization.introspection.v2",
        "ok": not reasons,
        "active": not reasons,
        "permit_ref": permit.get("permit_ref", ""),
        "intent_ref": permit.get("intent_ref", ""),
        "permit_status": permit.get("status", ""),
        "intent_status": intent.get("status", ""),
        "permit_generation": permit.get("generation", 0),
        "current_generation": intent.get("generation", 0),
        "reasons": reasons,
        "environment_change": environment_change,
        "authorization_reconciliation_required": environment_change["authorization_reconciliation_required"],
    }


def introspect(
    permit_ref: str, *, current_environment_snapshot: dict[str, Any] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    root = _state_root(state_root)
    try:
        path = _safe_ref(permit_ref, root, "permit")
    except ValueError as exc:
        return {"ok": False, "active": False, "reason": str(exc)}
    with _locked(root):
        permit = _read(path)
        if not permit.get("ok"):
            return {**permit, "active": False}
        return _introspect_permit_unlocked(root, permit, current_environment_snapshot)


def _operation_path(root: Path, operation_id: str, executor: str) -> Path:
    identifier = digest({"operation_id": operation_id, "executor": executor})[:32]
    return root / "operations" / f"{identifier}.json"


def _consume_permit_unlocked(
    root: Path, permit_path: Path, permit: dict[str, Any], *, executor: str,
    operation_id: str, idempotency_key: str, consumed_at: datetime | None = None,
    current_environment_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if str(permit.get("executor") or "") != executor or str(permit.get("audience") or "") != executor:
        return {"ok": False, "reason": "authorization_executor_or_audience_mismatch"}
    if str(permit.get("operation_id") or "") != operation_id:
        return {"ok": False, "reason": "authorization_operation_mismatch"}
    op_path = _operation_path(root, operation_id, executor)
    existing_op = _read(op_path) if op_path.exists() else {}
    if existing_op.get("ok"):
        if existing_op.get("permit_ref") != permit.get("permit_ref") or existing_op.get("idempotency_key") != idempotency_key:
            return {"ok": False, "reason": "authorization_operation_binding_conflict"}
        recovered = False
        consumption_ref = str(existing_op.get("consumption_ref") or "")
        if consumption_ref and not Path(consumption_ref).exists():
            recovered_receipt = {
                "schema": "scoped_authorization.consume.v2", "ok": True,
                "consumption_ref": consumption_ref, "permit_ref": permit.get("permit_ref", ""),
                "intent_ref": permit.get("intent_ref", ""), "decision_ref": permit.get("decision_ref", ""),
                "scope_signature": permit.get("scope_signature", ""), "consumer_owner": executor,
                "executor": executor, "audience": permit.get("audience", ""),
                "operation_id": operation_id, "idempotency_key": idempotency_key,
                "generation": permit.get("generation", 0),
                "consumed_at": permit.get("consumed_at", existing_op.get("created_at", "")),
                "authorization_turn_id": permit.get("authorization_turn_id", ""),
                "automation_run_id": permit.get("automation_run_id", ""),
                "reused": True, "recovered_from_operation": True,
            }
            _write(Path(consumption_ref), recovered_receipt)
            recovered = True
        return {
            "schema": "scoped_authorization.consume.v2", "ok": True, "reused": True,
            "permit_ref": permit.get("permit_ref", ""), "intent_ref": permit.get("intent_ref", ""),
            "operation_ref": str(op_path), "operation_id": operation_id,
            "consumption_ref": consumption_ref,
            "user_message_ref": existing_op.get("user_message_ref", ""),
            "authorization_turn_id": permit.get("authorization_turn_id", ""),
            "status": existing_op.get("status", "permit_reserved"),
            "recovered_from_operation": recovered,
        }
    active = _introspect_permit_unlocked(root, permit, current_environment_snapshot)
    if not active.get("active"):
        return {"ok": False, "reason": "authorization_permit_inactive", "introspection": active}
    moment = consumed_at or now_utc()
    consumption_id = digest({
        "permit_ref": permit.get("permit_ref"), "operation_id": operation_id,
        "executor": executor, "idempotency_key": idempotency_key,
    })[:32]
    consumption_path = root / "consumptions" / f"{consumption_id}.json"
    receipt = {
        "schema": "scoped_authorization.consume.v2", "ok": True,
        "consumption_ref": str(consumption_path), "permit_ref": permit.get("permit_ref", ""),
        "intent_ref": permit.get("intent_ref", ""), "decision_ref": permit.get("decision_ref", ""),
        "scope_signature": permit.get("scope_signature", ""), "consumer_owner": executor,
        "executor": executor, "audience": permit.get("audience", ""),
        "operation_id": operation_id, "idempotency_key": idempotency_key,
        "generation": permit.get("generation", 0), "consumed_at": iso(moment),
        "authorization_turn_id": permit.get("authorization_turn_id", ""),
        "automation_run_id": permit.get("automation_run_id", ""), "reused": False,
    }
    operation = {
        "schema": "scoped_authorization.operation.v2", "ok": True,
        "operation_id": operation_id, "operation_ref": str(op_path),
        "permit_ref": permit.get("permit_ref", ""), "intent_ref": permit.get("intent_ref", ""),
        "consumption_ref": str(consumption_path), "executor": executor,
        "idempotency_key": idempotency_key, "generation": permit.get("generation", 0),
        "status": "permit_reserved", "created_at": iso(moment), "updated_at": iso(moment),
        "user_message_ref": permit.get("user_message_ref", ""),
    }
    permit["status"] = "consumed"
    permit["consumed_at"] = iso(moment)
    permit["consumption_ref"] = str(consumption_path)
    _write(permit_path, permit)
    _write(consumption_path, receipt)
    _write(op_path, operation)
    _event(root, event_type="permit_consumed", intent_id=str(permit.get("intent_ref") or "").rsplit(":", 1)[-1], generation=int(permit.get("generation") or 0), details={"permit_ref": permit.get("permit_ref"), "operation_id": operation_id})
    return {**receipt, "operation_ref": str(op_path), "status": "permit_reserved"}


def consume_permit(
    permit_ref: str, *, executor: str, operation_id: str, idempotency_key: str = "",
    state_root: Path | str | None = None, consumed_at: datetime | None = None,
    current_environment_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not executor or not operation_id:
        return {"ok": False, "reason": "authorization_executor_and_operation_required"}
    root = _state_root(state_root)
    try:
        path = _safe_ref(permit_ref, root, "permit")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    with _locked(root):
        permit = _read(path)
        if not permit.get("ok"):
            return permit
        return _consume_permit_unlocked(root, path, permit, executor=executor, operation_id=operation_id, idempotency_key=idempotency_key or operation_id, consumed_at=consumed_at, current_environment_snapshot=current_environment_snapshot)


def record_effect(
    operation_id: str, *, executor: str, status: str, effect_receipt_ref: str = "",
    compensation_ref: str = "", details: dict[str, Any] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    allowed = {"effect_started", "effect_unknown", "effect_observed", "completed", "cancelled", "compensation_required"}
    if status not in allowed:
        return {"ok": False, "reason": "authorization_effect_status_invalid"}
    root = _state_root(state_root)
    path = _operation_path(root, operation_id, executor)
    with _locked(root):
        operation = _read(path)
        if not operation.get("ok"):
            return {"ok": False, "reason": "authorization_operation_not_found"}
        current = str(operation.get("status") or "")
        transitions = {
            "permit_reserved": {"effect_started", "cancelled"},
            "effect_started": {"effect_unknown", "effect_observed", "compensation_required"},
            "effect_unknown": {"effect_observed", "compensation_required"},
            "effect_observed": {"completed", "compensation_required"},
        }
        if status == current:
            return {**operation, "reused": True}
        if current == "completed" and status in {"effect_observed", "completed"}:
            return {**operation, "reused": True, "recovered_terminal_effect": True}
        if status not in transitions.get(current, set()):
            return {"ok": False, "reason": "authorization_effect_transition_invalid", "from": current, "to": status}
        if status in {"effect_observed", "completed"} and not (effect_receipt_ref or operation.get("effect_receipt_ref")):
            return {"ok": False, "reason": "authorization_effect_receipt_required"}
        operation["status"] = status
        operation["effect_receipt_ref"] = effect_receipt_ref or operation.get("effect_receipt_ref", "")
        operation["compensation_ref"] = compensation_ref or operation.get("compensation_ref", "")
        operation["details"] = details or operation.get("details", {})
        operation["updated_at"] = iso(now_utc())
        _write(path, operation)
        return operation


def transition_intent(
    intent_ref: str, *, action: str, reason: str, evidence_ref: str = "",
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    targets = {"revoke": "revoked", "suspend": "suspended", "resume": "active", "supersede": "superseded"}
    if action not in targets:
        return {"ok": False, "reason": "authorization_intent_transition_invalid"}
    if action == "resume" and not str(evidence_ref or "").strip():
        return {"ok": False, "reason": "authorization_meta_permission_evidence_required"}
    root = _state_root(state_root)
    try:
        path = _safe_ref(intent_ref, root, "intent")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    with _locked(root):
        intent = _read(path)
        if not intent.get("ok"):
            return intent
        current = str(intent.get("status") or "")
        if action == "resume" and current != "suspended":
            return {"ok": False, "reason": "only_suspended_intent_can_resume"}
        if action != "resume" and current not in ACTIVE_INTENT_STATES | {"suspended"}:
            return {"ok": False, "reason": "authorization_intent_already_terminal"}
        intent["generation"] = int(intent.get("generation") or 0) + 1
        intent["status"] = targets[action]
        intent["transition_reason"] = str(reason or "unspecified")[:500]
        if evidence_ref:
            intent["transition_evidence_ref"] = str(evidence_ref)[:1000]
        intent["updated_at"] = iso(now_utc())
        _write(path, intent)
        event = _event(root, event_type=f"intent_{action}", intent_id=intent["intent_id"], generation=intent["generation"], details={"reason": intent["transition_reason"]})
        return {"ok": True, "intent_ref": intent_ref, "status": intent["status"], "generation": intent["generation"], "event_ref": event["event_ref"]}


def inspect_context(
    scope: dict[str, Any], *, actor_chain: list[dict[str, str]], executor: str,
    audience: str, workflow_semantic_hash: str, state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Return one bounded authorization entry for Codex and workflow routing."""

    root = _state_root(state_root)
    candidates: list[dict[str, Any]] = []
    with _locked(root):
        for path in sorted((root / "intents").glob("*.json")):
            intent = _read(path)
            if not intent.get("ok"):
                continue
            decision = _decision_unlocked(
                root, intent, scope, actor_chain=actor_chain, executor=executor, audience=audience,
                workflow_semantic_hash=workflow_semantic_hash,
            )
            candidates.append({
                "intent_ref": intent.get("intent_ref", ""),
                "intent_type": intent.get("intent_type", ""),
                "status": intent.get("status", ""),
                "generation": intent.get("generation", 0),
                "decision": decision.get("decision", "deny"),
                "determining_denies": decision.get("determining_denies", []),
                "missing_authorization_delta": decision.get("missing_authorization_delta", []),
                "decision_ref": decision.get("decision_ref", ""),
            })
    allowed = [item for item in candidates if item["decision"] == "allow"]
    return {
        "schema": "scoped_authorization.inspect.v2",
        "ok": True,
        "effective_decision": "allow" if allowed else "deny",
        "scope_signature": rich_scope_signature(scope),
        "actor_chain": actor_chain,
        "executor": executor,
        "audience": audience,
        "workflow_semantic_hash": workflow_semantic_hash,
        "matching_intent_refs": [item["intent_ref"] for item in allowed],
        "candidate_count": len(candidates),
        "candidates": candidates[:20],
        "required_next_action": "issue_permit" if allowed else "request_exact_authorization",
    }


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    with (root / "authorization.lock").open("a+", encoding="utf-8") as handle:
        os.chmod(root / "authorization.lock", 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def create_challenge(
    scope: dict[str, Any],
    *,
    target_summary: str,
    state_root: Path | str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_uses: int = 1,
    issued_at: datetime | None = None,
) -> dict[str, Any]:
    normalized = {field: str(scope.get(field) or "").strip() for field in SCOPE_FIELDS}
    missing = [field for field, value in normalized.items() if not value]
    if missing:
        return {"ok": False, "reason": "authorization_scope_incomplete", "missing": missing}
    if max_uses != 1:
        return {"ok": False, "reason": "authorization_max_uses_must_be_one"}
    root = _state_root(state_root)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    created = issued_at or now_utc()
    challenge_id = secrets.token_hex(16)
    response_token = f"AUTHORIZE-{secrets.token_urlsafe(24)}"
    payload = {
        "schema": "scoped_authorization.challenge.v1",
        "ok": True,
        "challenge_id": challenge_id,
        "challenge_ref": _ref("challenge", challenge_id),
        "scope": normalized,
        "scope_signature": scope_signature(normalized),
        "target_summary": str(target_summary or "")[:500],
        "issued_at": iso(created),
        "expires_at": iso(created + timedelta(seconds=max(60, min(int(ttl_seconds), 3600)))),
        "max_uses": 1,
        "response_token": response_token,
        "response_instruction": f"请在后续用户消息中原样发送：{response_token}",
        "status": "pending",
    }
    _write(root / "challenges" / f"{challenge_id}.json", payload)
    return payload


def _user_messages(rollout_path: Path) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    with rollout_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if event.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
                continue
            parts = payload.get("content") if isinstance(payload.get("content"), list) else []
            text = "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
            messages.append({
                "timestamp": str(event.get("timestamp") or ""),
                "message_ref": f"{rollout_path.name}:line:{line_number}",
                "message_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
            })
    return messages


def _rollout_thread_id(rollout_path: Path) -> str:
    try:
        with rollout_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if event.get("type") == "session_meta":
                    return str(payload.get("id") or payload.get("session_id") or "").strip()
    except OSError:
        return ""
    return ""


def grant_from_thread(
    challenge_ref: str,
    *,
    rollout_path: Path,
    state_root: Path | str | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    root = _state_root(state_root)
    rollout_path = Path(rollout_path).resolve()
    try:
        challenge_path = _safe_ref(challenge_ref, root, "challenge")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    with _locked(root):
        challenge = _read(challenge_path)
        if not challenge.get("ok"):
            return challenge
        now = checked_at or now_utc()
        if parse_time(challenge["expires_at"]) <= now:
            return {"ok": False, "reason": "authorization_challenge_expired"}
        if _rollout_thread_id(rollout_path) != str(challenge["scope"].get("thread_id") or ""):
            return {"ok": False, "reason": "authorization_rollout_thread_mismatch"}
        issued = parse_time(challenge["issued_at"])
        matches = [
            message for message in _user_messages(rollout_path)
            if message["timestamp"] and parse_time(message["timestamp"]) > issued
            and message["text"].strip() == challenge["response_token"]
        ]
        if not matches:
            return {"ok": False, "reason": "post_challenge_user_response_not_found"}
        evidence = matches[-1]
        turn_id = authorization_turn_id(evidence["message_ref"], evidence["message_hash"])
        grant_id = digest({"challenge_id": challenge["challenge_id"], "message_hash": evidence["message_hash"]})[:32]
        grant_path = root / "grants" / f"{grant_id}.json"
        existing = _read(grant_path) if grant_path.exists() else {}
        if existing.get("ok"):
            return existing
        intent = _create_intent_unlocked(
            root, challenge["scope"], intent_type="one_time", authorizer="user",
            subject="user", evidence_ref=evidence["message_ref"], turn_id=turn_id,
            allowed_actor_classes=["codex", "durable_executor"],
        )
        if not intent.get("ok"):
            return intent
        payload = {
            "schema": "scoped_authorization.grant.v1",
            "ok": True,
            "grant_id": grant_id,
            "grant_ref": _ref("grant", grant_id),
            "challenge_ref": challenge["challenge_ref"],
            "scope": challenge["scope"],
            "scope_signature": challenge["scope_signature"],
            "issued_at": challenge["issued_at"],
            "expires_at": challenge["expires_at"],
            "challenge_safety_expires_at": challenge["expires_at"],
            "max_uses": 1,
            "use_count": 0,
            "status": "active",
            "user_message_ref": evidence["message_ref"],
            "user_message_timestamp": evidence["timestamp"],
            "user_message_hash": evidence["message_hash"],
            "authorization_turn_id": turn_id,
            "intent_ref": intent["intent_ref"],
            "generation": intent["generation"],
            "verified_role": "user",
            "response_token_matched": True,
        }
        _write(grant_path, payload)
        challenge["status"] = "granted"
        challenge["grant_ref"] = payload["grant_ref"]
        _write(challenge_path, challenge)
        return payload


def _scope_mismatch(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    return [field for field in SCOPE_FIELDS if str(actual.get(field) or "") != str(expected.get(field) or "")]


def validate_grant(
    grant_ref: str,
    expected_scope: dict[str, Any],
    *,
    state_root: Path | str | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    root = _state_root(state_root)
    try:
        path = _safe_ref(grant_ref, root, "grant")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    grant = _read(path)
    if not grant.get("ok"):
        return grant
    mismatched = _scope_mismatch(grant.get("scope") or {}, expected_scope)
    if mismatched:
        return {"ok": False, "reason": "authorization_scope_mismatch", "mismatched_fields": mismatched}
    if grant.get("scope_signature") != scope_signature(expected_scope):
        return {"ok": False, "reason": "authorization_scope_signature_mismatch"}
    if grant.get("status") != "active":
        return {"ok": False, "reason": f"authorization_grant_{grant.get('status') or 'inactive'}"}
    intent_ref = str(grant.get("intent_ref") or "")
    if intent_ref:
        try:
            intent = _read(_safe_ref(intent_ref, root, "intent"))
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        if intent.get("status") not in ACTIVE_INTENT_STATES:
            return {"ok": False, "reason": f"authorization_intent_{intent.get('status') or 'inactive'}"}
        if int(grant.get("generation") or 0) != int(intent.get("generation") or 0):
            return {"ok": False, "reason": "authorization_generation_fenced"}
    if int(grant.get("use_count") or 0) >= int(grant.get("max_uses") or 0):
        return {"ok": False, "reason": "authorization_grant_exhausted"}
    return {"ok": True, "grant_ref": grant_ref, "scope_signature": grant["scope_signature"], "user_message_ref": grant["user_message_ref"]}


def consume_grant(
    grant_ref: str,
    expected_scope: dict[str, Any],
    *,
    consumer_owner: str,
    operation_id: str,
    state_root: Path | str | None = None,
    consumed_at: datetime | None = None,
) -> dict[str, Any]:
    if not str(operation_id or "").strip() or not str(consumer_owner or "").strip():
        return {"ok": False, "reason": "authorization_consumer_and_operation_required"}
    root = _state_root(state_root)
    try:
        grant_path = _safe_ref(grant_ref, root, "grant")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    operation_digest = digest({"operation_id": operation_id, "consumer_owner": consumer_owner, "scope_signature": scope_signature(expected_scope)})
    receipt_path = root / "consumptions" / f"{operation_digest}.json"
    workflow_hash = scope_signature(expected_scope)
    environment = authorization_environment_provider.snapshot(
        workflow_semantic_hash=workflow_hash,
        owner=consumer_owner,
        owner_capability_signature=digest({
            "owner": consumer_owner,
            "action": str((expected_scope or {}).get("action") or ""),
            "phase": str((expected_scope or {}).get("phase") or ""),
        }),
    )
    with _locked(root):
        grant = _read(grant_path)
        if grant.get("intent_ref"):
            if grant.get("status") == "consumed" and grant.get("permit_ref"):
                if str(grant.get("consumed_operation_id") or "") != operation_id:
                    return {"ok": False, "reason": "authorization_grant_consumed"}
                permit_path = _safe_ref(str(grant["permit_ref"]), root, "permit")
                permit = _read(permit_path)
                result = _consume_permit_unlocked(
                    root, permit_path, permit, executor=consumer_owner, operation_id=operation_id,
                    idempotency_key=operation_id, consumed_at=consumed_at,
                    current_environment_snapshot=environment,
                )
                return {**result, "grant_ref": grant_ref, "user_message_ref": grant.get("user_message_ref", "")}
            checked = validate_grant(grant_ref, expected_scope, state_root=root, checked_at=consumed_at)
            if not checked.get("ok"):
                return checked
            intent = _read(_safe_ref(str(grant["intent_ref"]), root, "intent"))
            permit = _issue_permit_unlocked(
                root, intent, expected_scope, actor_chain=[{"class": "codex", "id": str((expected_scope or {}).get("thread_id") or "codex")}],
                executor=consumer_owner, audience=consumer_owner, operation_id=operation_id,
                workflow_semantic_hash=workflow_hash,
                authorization_turn=str(grant.get("authorization_turn_id") or ""),
                environment_snapshot=environment,
            )
            if not permit.get("ok"):
                return permit
            permit_path = _safe_ref(str(permit["permit_ref"]), root, "permit")
            permit["user_message_ref"] = grant.get("user_message_ref", "")
            _write(permit_path, permit)
            result = _consume_permit_unlocked(
                root, permit_path, permit, executor=consumer_owner, operation_id=operation_id,
                idempotency_key=operation_id, consumed_at=consumed_at,
                current_environment_snapshot=environment,
            )
            if not result.get("ok"):
                if result.get("reason") == "authorization_operation_binding_conflict":
                    return {"ok": False, "reason": "authorization_operation_already_bound_to_another_grant"}
                return result
            grant["use_count"] = 1
            grant["status"] = "consumed"
            grant["permit_ref"] = permit["permit_ref"]
            grant["consumption_ref"] = result.get("consumption_ref", "")
            grant["consumed_at"] = result.get("consumed_at", iso(consumed_at or now_utc()))
            grant["consumed_operation_id"] = operation_id
            grant["consumed_by_owner"] = consumer_owner
            _write(grant_path, grant)
            return {**result, "grant_ref": grant_ref, "user_message_ref": grant.get("user_message_ref", "")}
        existing = _read(receipt_path) if receipt_path.exists() else {}
        if existing.get("ok"):
            if existing.get("grant_ref") != grant_ref:
                return {"ok": False, "reason": "authorization_operation_already_bound_to_another_grant"}
            return {**existing, "reused": True}
        grant = _read(grant_path)
        if grant.get("status") == "consumed":
            if grant.get("consumption_operation_digest") != operation_digest:
                return {"ok": False, "reason": "authorization_grant_consumed"}
            recovered = {
                "schema": "scoped_authorization.consume.v1",
                "ok": True,
                "consumption_ref": str(receipt_path),
                "grant_ref": grant_ref,
                "scope_signature": grant["scope_signature"],
                "consumer_owner": str(grant.get("consumed_by_owner") or consumer_owner),
                "operation_id": str(grant.get("consumed_operation_id") or operation_id),
                "consumed_at": grant["consumed_at"],
                "user_message_ref": grant["user_message_ref"],
                "reused": True,
                "recovered_from_grant": True,
            }
            _write(receipt_path, recovered)
            return recovered
        checked = validate_grant(grant_ref, expected_scope, state_root=root, checked_at=consumed_at)
        if not checked.get("ok"):
            return checked
        moment = consumed_at or now_utc()
        receipt = {
            "schema": "scoped_authorization.consume.v1",
            "ok": True,
            "consumption_ref": str(receipt_path),
            "grant_ref": grant_ref,
            "scope_signature": grant["scope_signature"],
            "consumer_owner": consumer_owner,
            "operation_id": operation_id,
            "consumed_at": iso(moment),
            "user_message_ref": grant["user_message_ref"],
            "reused": False,
        }
        grant["use_count"] = 1
        grant["status"] = "consumed"
        grant["consumed_at"] = receipt["consumed_at"]
        grant["consumption_ref"] = receipt["consumption_ref"]
        grant["consumption_operation_digest"] = operation_digest
        grant["consumed_operation_id"] = operation_id
        grant["consumed_by_owner"] = consumer_owner
        _write(grant_path, grant)
        _write(receipt_path, receipt)
        return receipt


def revoke(grant_ref: str, *, reason: str, state_root: Path | str | None = None) -> dict[str, Any]:
    root = _state_root(state_root)
    try:
        path = _safe_ref(grant_ref, root, "grant")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    with _locked(root):
        grant = _read(path)
        if not grant.get("ok"):
            return grant
        if grant.get("status") == "consumed":
            return {"ok": False, "reason": "consumed_authorization_cannot_be_revoked"}
        grant["status"] = "revoked"
        grant["revocation_reason"] = str(reason or "unspecified")[:500]
        grant["revoked_at"] = iso(now_utc())
        _write(path, grant)
        return {"ok": True, "grant_ref": grant_ref, "status": "revoked"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scope-bound user authorization evidence owner")
    sub = parser.add_subparsers(dest="action", required=True)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--state-root", default="")
    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("--scope-json", required=True)
    inspect_parser.add_argument("--actor-class", default="codex")
    inspect_parser.add_argument("--actor-id", default="codex")
    inspect_parser.add_argument("--executor", required=True)
    inspect_parser.add_argument("--audience", required=True)
    inspect_parser.add_argument("--workflow-semantic-hash", required=True)
    inspect_parser.add_argument("--state-root", default="")
    introspect_parser = sub.add_parser("introspect")
    introspect_parser.add_argument("--permit-ref", required=True)
    introspect_parser.add_argument("--environment-json", default="")
    introspect_parser.add_argument("--state-root", default="")
    transition_parser = sub.add_parser("transition")
    transition_parser.add_argument("--intent-ref", required=True)
    transition_parser.add_argument("--action", dest="transition_action", required=True, choices=["revoke", "suspend", "resume", "supersede"])
    transition_parser.add_argument("--reason", required=True)
    transition_parser.add_argument("--evidence-ref", default="")
    transition_parser.add_argument("--state-root", default="")
    args = parser.parse_args(argv)
    root = _state_root(args.state_root)
    if args.action == "inspect":
        payload = inspect_context(
            json.loads(args.scope_json),
            actor_chain=[{"class": args.actor_class, "id": args.actor_id}],
            executor=args.executor,
            audience=args.audience,
            workflow_semantic_hash=args.workflow_semantic_hash,
            state_root=root,
        )
    elif args.action == "introspect":
        environment = json.loads(args.environment_json) if args.environment_json else None
        payload = introspect(args.permit_ref, current_environment_snapshot=environment, state_root=root)
    elif args.action == "transition":
        payload = transition_intent(
            args.intent_ref, action=args.transition_action, reason=args.reason,
            evidence_ref=args.evidence_ref, state_root=root,
        )
    else:
        payload = {
            "schema": "scoped_authorization.snapshot.v2",
            "ok": True,
            "state_root": str(root),
            "challenge_count": len(list((root / "challenges").glob("*.json"))),
            "intent_count": len(list((root / "intents").glob("*.json"))),
            "decision_count": len(list((root / "decisions").glob("*.json"))),
            "permit_count": len(list((root / "permits").glob("*.json"))),
            "grant_count": len(list((root / "grants").glob("*.json"))),
            "consumption_count": len(list((root / "consumptions").glob("*.json"))),
            "operation_count": len(list((root / "operations").glob("*.json"))),
            "event_count": len(list((root / "events").glob("*.json"))),
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
