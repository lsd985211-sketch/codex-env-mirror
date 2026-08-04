#!/usr/bin/env python3
"""Own intent, decision, permit, consumption, and effect evidence.

Ownership: exact user-evidence challenges, authorization intents, embedded
policy decisions, attenuated per-turn/per-run permits, atomic consumption,
revocation fencing, and minimal operation/effect journals.
Non-goals: deciding business actions, inferring unconstrained approval prose, replacing
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
import hmac
import json
import os
import re
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
import scoped_authorization_evidence
from scoped_authorization_maintenance import effect_transition_plan


DEFAULT_STATE_ROOT = Path.home() / ".codex-app" / "runtime" / "scoped-authorizations"
# Exact user authorization remains valid until consumed, revoked, superseded,
# or fenced by its bound scope.  Owners may opt into an explicit deadline.
DEFAULT_TTL_SECONDS = 0
DEFAULT_GRANT_CONSUME_TTL_SECONDS = 0
DEFAULT_PERMIT_TTL_SECONDS = 0
INTENT_TYPES = {"one_time", "long_lived", "permanent_default", "system_default", "task_intent"}
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
CODEX_APP_THREAD_ID_RE = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", re.IGNORECASE)


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
        "fallback_actions": sorted({str(item).strip() for item in (scope.get("fallback_actions") or []) if str(item).strip()}),
    })
    return normalized


def rich_scope_signature(scope: dict[str, Any]) -> str:
    return digest(normalize_rich_scope(scope))


def scope_signature_projection(
    scope: dict[str, Any],
    *,
    challenge_signature: str = "",
    permit_signature: str = "",
    operation_signature: str = "",
) -> dict[str, Any]:
    """Make the core and rich scope-signature projections explicit in receipts."""

    core = scope_signature(scope)
    rich = rich_scope_signature(scope)
    challenge = str(challenge_signature or core)
    permit = str(permit_signature or rich)
    operation = str(operation_signature or permit)
    return {
        "challenge_scope_signature": challenge,
        "permit_scope_signature": permit,
        "operation_scope_signature": operation,
        "scope_signature_relationship": {
            "challenge_projection": "core_scope_v1",
            "permit_projection": "rich_scope_v1",
            "operation_projection": "rich_scope_v1",
            "same_normalized_scope": challenge == core and permit == rich and operation == rich,
            "note": "core and rich signatures may differ because rich scope includes exact locations, executors, prohibitions, and related constraints",
        },
    }


def build_scope(
    *,
    thread_id: str,
    action: str,
    target: Any,
    risk: str,
    phase: str,
    source_signature: str,
    requested_by_owner: str,
    target_fingerprint_value: str = "",
    fallback_actions: list[str] | None = None,
) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "thread_id": str(thread_id or "").strip(),
        "action": str(action or "").strip(),
        "target_fingerprint": str(target_fingerprint_value or "").strip() or target_fingerprint(target),
        "risk": str(risk or "").strip(),
        "phase": str(phase or "").strip(),
        "source_signature": str(source_signature or "").strip(),
        "requested_by_owner": str(requested_by_owner or "").strip(),
    }
    if fallback_actions:
        scope["fallback_actions"] = sorted({str(item).strip() for item in fallback_actions if str(item).strip()})
    return scope


def validate_same_scope_fallback(scope: dict[str, Any], fallback_action: str) -> dict[str, Any]:
    """Prove a runtime fallback is pre-declared inside the exact authorization scope."""

    normalized = normalize_rich_scope(scope)
    requested = str(fallback_action or "").strip()
    allowed = set(normalized.get("fallback_actions") or [])
    allowed.add(str(normalized.get("action") or "").strip())
    if not requested:
        return {"ok": False, "reason": "authorization_fallback_action_required"}
    if requested not in allowed:
        return {
            "ok": False,
            "reason": "authorization_fallback_scope_expansion",
            "fallback_action": requested,
            "allowed_actions": sorted(item for item in allowed if item),
            "scope_signature": rich_scope_signature(normalized),
        }
    return {
        "ok": True,
        "fallback_action": requested,
        "authorization_binding": "same_scope_fallback",
        "scope_signature": rich_scope_signature(normalized),
    }


def scope_signature(scope: dict[str, Any]) -> str:
    return digest({field: str(scope.get(field) or "") for field in SCOPE_FIELDS})


def relevant_input_signature(scope: dict[str, Any], policy_signature: str = "") -> str:
    """Bind the established scope contract and optional policy revision, not repository noise."""

    return digest({"scope_signature": scope_signature(scope), "policy_signature": str(policy_signature or "")})


def _bounded_ttl(value: int | None, *, default: int) -> int:
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return default
    return 0 if seconds <= 0 else max(60, min(seconds, 3600))


def _expiry(created: datetime, seconds: int) -> str:
    return iso(created + timedelta(seconds=seconds)) if seconds > 0 else ""


def _expired(value: Any, moment: datetime) -> bool:
    text = str(value or "").strip()
    return bool(text) and parse_time(text) <= moment


def _challenge_deadline(challenge: dict[str, Any]) -> str:
    return str(challenge.get("challenge_expires_at") or "") if str(challenge.get("schema") or "").endswith(".v2") else ""


def _grant_deadline(grant: dict[str, Any]) -> str:
    return str(grant.get("grant_consume_by") or "") if str(grant.get("schema") or "").endswith(".v2") else ""


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
    permit_ttl_seconds: int | None = DEFAULT_PERMIT_TTL_SECONDS,
    issued_at: datetime | None = None,
) -> dict[str, Any]:
    if bool(authorization_turn) == bool(automation_run_id):
        return {"ok": False, "reason": "exactly_one_turn_or_automation_run_required"}
    if intent.get("intent_type") == "task_intent":
        if automation_run_id or authorization_turn != str(intent.get("authorization_turn_id") or ""):
            return {"ok": False, "reason": "authorization_task_intent_turn_not_bound"}
        if operation_id != str(intent.get("task_operation_id") or ""):
            return {"ok": False, "reason": "authorization_task_intent_operation_not_bound"}
        if executor != str(intent.get("task_owner") or ""):
            return {"ok": False, "reason": "authorization_task_intent_executor_not_bound"}
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
    issued_at = issued_at or now_utc()
    permit_ttl = _bounded_ttl(permit_ttl_seconds, default=DEFAULT_PERMIT_TTL_SECONDS)
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
        "status": "issued", "issued_at": iso(issued_at),
        "permit_expires_at": _expiry(issued_at, permit_ttl),
        "attenuation": {},
        "user_message_ref": str(intent.get("user_message_ref") or intent.get("current_message_ref") or ""),
        "user_message_hash": str(intent.get("user_message_hash") or intent.get("current_message_hash") or ""),
        "relevant_input_signature": str(intent.get("pdp_input_signature") or intent.get("relevant_input_signature") or ""),
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
    permit_ttl_seconds: int | None = DEFAULT_PERMIT_TTL_SECONDS,
    issued_at: datetime | None = None,
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
        return _issue_permit_unlocked(root, intent, requested_scope, actor_chain=actor_chain, executor=executor, audience=audience, operation_id=operation_id, workflow_semantic_hash=workflow_semantic_hash, authorization_turn=authorization_turn, automation_run_id=automation_run_id, runtime_context=runtime_context, environment_snapshot=environment_snapshot, explicit_denies=explicit_denies, permit_ttl_seconds=permit_ttl_seconds, issued_at=issued_at)


def _introspect_permit_unlocked(
    root: Path, permit: dict[str, Any], current_environment_snapshot: dict[str, Any] | None = None,
    checked_at: datetime | None = None,
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
    if _expired(permit.get("permit_expires_at"), checked_at or now_utc()):
        reasons.append("authorization_permit_expired")
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
    state_root: Path | str | None = None, checked_at: datetime | None = None,
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
        return _introspect_permit_unlocked(root, permit, current_environment_snapshot, checked_at)


def permit_snapshot(
    permit_ref: str, *, state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Return the bounded binding needed by a registered consumer."""

    root = _state_root(state_root)
    try:
        path = _safe_ref(permit_ref, root, "permit")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    with _locked(root):
        permit = _read(path)
        if not permit.get("ok"):
            return permit
        try:
            intent = _read(_safe_ref(str(permit.get("intent_ref") or ""), root, "intent"))
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        if not intent.get("ok"):
            return intent
        return {
            "schema": "scoped_authorization.permit_snapshot.v1",
            "ok": True,
            "permit_ref": permit.get("permit_ref", ""),
            "intent_ref": permit.get("intent_ref", ""),
            "intent_type": intent.get("intent_type", ""),
            "operation_id": permit.get("operation_id", ""),
            "workflow_semantic_hash": permit.get("workflow_semantic_hash", ""),
            "scope": permit.get("scope") if isinstance(permit.get("scope"), dict) else {},
            "scope_signature": permit.get("scope_signature", ""),
            "executor": permit.get("executor", ""),
            "audience": permit.get("audience", ""),
            "status": permit.get("status", ""),
            "generation": permit.get("generation", 0),
            "environment_snapshot": permit.get("environment_snapshot") or {},
            "permit_expires_at": permit.get("permit_expires_at", ""),
        }


def _operation_path(root: Path, operation_id: str, executor: str) -> Path:
    identifier = digest({"operation_id": operation_id, "executor": executor})[:32]
    return root / "operations" / f"{identifier}.json"


def operation_snapshot(
    operation_id: str, *, executor: str, state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Read one operation journal without changing it."""

    root = _state_root(state_root)
    with _locked(root):
        operation = _read(_operation_path(root, operation_id, executor))
        return operation if operation.get("ok") else {"ok": False, "reason": "authorization_operation_not_found"}


def operation_projection(
    operation_id: str, *, executor: str, state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Return one non-capability-bearing operation correlation projection."""

    operation = operation_snapshot(operation_id, executor=executor, state_root=state_root)
    if not operation.get("ok"):
        return operation
    correlation = operation.get("correlation") if isinstance(operation.get("correlation"), dict) else {}
    return {
        "schema": "scoped_authorization.operation_projection.v1", "ok": True,
        "operation_id": str(operation.get("operation_id") or operation_id),
        "executor": str(operation.get("executor") or executor),
        "status": str(operation.get("status") or ""),
        "intent_ref": str(operation.get("intent_ref") or ""),
        "task_id": str(correlation.get("task_id") or ""),
        "target_fingerprint": str(correlation.get("target_fingerprint") or ""),
        "scope_signature": str(correlation.get("scope_signature") or ""),
        "relevant_input_signature": str(operation.get("relevant_input_signature") or ""),
        "user_message_ref": str(operation.get("user_message_ref") or ""),
        "operation_ref": str(operation.get("operation_ref") or ""),
    }


def annotate_permit_correlation(
    permit_ref: str, *, executor: str, operation_id: str, correlation: dict[str, Any],
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Attach bounded owner correlation to one unconsumed exact permit."""

    root = _state_root(state_root)
    try:
        path = _safe_ref(permit_ref, root, "permit")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    allowed = {"task_id", "target_fingerprint", "scope_signature"}
    normalized = {key: str(correlation.get(key) or "") for key in allowed}
    with _locked(root):
        permit = _read(path)
        if not permit.get("ok"):
            return permit
        if permit.get("status") != "issued":
            return {"ok": False, "reason": "authorization_permit_not_annotatable", "status": permit.get("status", "")}
        if str(permit.get("executor") or "") != str(executor or "") or str(permit.get("operation_id") or "") != str(operation_id or ""):
            return {"ok": False, "reason": "authorization_permit_correlation_binding_mismatch"}
        if normalized["scope_signature"] != str(permit.get("scope_signature") or ""):
            return {"ok": False, "reason": "authorization_permit_correlation_scope_mismatch"}
        existing = permit.get("correlation") if isinstance(permit.get("correlation"), dict) else {}
        if existing and existing != normalized:
            return {"ok": False, "reason": "authorization_permit_correlation_conflict"}
        permit["correlation"] = normalized
        _write(path, permit)
        return {"ok": True, "permit_ref": permit_ref, "correlation": normalized, "reused": bool(existing)}


def reserve_prepared_operation(
    permit_ref: str, *, executor: str, operation_id: str, state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Persist a private exact lookup binding without consuming the permit."""

    root = _state_root(state_root)
    try:
        permit_path = _safe_ref(permit_ref, root, "permit")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    operation_path = _operation_path(root, operation_id, executor)
    with _locked(root):
        permit = _read(permit_path)
        if not permit.get("ok"):
            return permit
        if permit.get("status") != "issued":
            return {"ok": False, "reason": "authorization_permit_not_preparable", "status": permit.get("status", "")}
        if str(permit.get("executor") or "") != str(executor or "") or str(permit.get("operation_id") or "") != str(operation_id or ""):
            return {"ok": False, "reason": "authorization_prepared_operation_binding_mismatch"}
        existing = _read(operation_path) if operation_path.exists() else {}
        if existing.get("ok"):
            if existing.get("permit_ref") != permit_ref or existing.get("status") != "prepared":
                return {"ok": False, "reason": "authorization_prepared_operation_conflict"}
            return {**existing, "reused": True}
        operation = {
            "schema": "scoped_authorization.operation.v2", "ok": True,
            "operation_id": operation_id, "operation_ref": str(operation_path),
            "permit_ref": permit_ref, "intent_ref": permit.get("intent_ref", ""),
            "executor": executor, "idempotency_key": operation_id,
            "generation": permit.get("generation", 0), "status": "prepared",
            "created_at": iso(now_utc()), "updated_at": iso(now_utc()),
            "user_message_ref": permit.get("user_message_ref", ""),
            "user_message_hash": permit.get("user_message_hash", ""),
            "relevant_input_signature": permit.get("relevant_input_signature", ""),
            "correlation": permit.get("correlation") if isinstance(permit.get("correlation"), dict) else {},
        }
        _write(operation_path, operation)
        return operation


def record_operation_checkpoint(
    operation_id: str, *, executor: str, checkpoint: dict[str, Any],
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Append one idempotent consumer checkpoint to an active operation."""

    step = str(checkpoint.get("step") or "").strip()
    if not step:
        return {"ok": False, "reason": "authorization_checkpoint_step_required"}
    root = _state_root(state_root)
    path = _operation_path(root, operation_id, executor)
    with _locked(root):
        operation = _read(path)
        if not operation.get("ok"):
            return {"ok": False, "reason": "authorization_operation_not_found"}
        if operation.get("status") not in {"effect_started", "effect_unknown", "effect_observed"}:
            return {"ok": False, "reason": "authorization_checkpoint_operation_inactive", "status": operation.get("status", "")}
        details = dict(operation.get("details") or {})
        rows = list(details.get("lifecycle_checkpoints") or [])
        exact = next((row for row in rows if row == checkpoint), None)
        if exact is not None:
            return {**operation, "reused": True, "checkpoint": exact}
        existing = next((row for row in rows if row.get("step") == step and row.get("status") == "completed"), None)
        if existing is not None:
            return {"ok": False, "reason": "authorization_checkpoint_binding_conflict", "step": step}
        rows.append(dict(checkpoint))
        details["lifecycle_checkpoints"] = rows
        operation["details"] = details
        operation["updated_at"] = iso(now_utc())
        _write(path, operation)
        return {**operation, "checkpoint": dict(checkpoint)}


def _consume_permit_unlocked(
    root: Path, permit_path: Path, permit: dict[str, Any], *, executor: str,
    operation_id: str, idempotency_key: str, consumed_at: datetime | None = None,
    current_environment_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if str(permit.get("executor") or "") != executor or str(permit.get("audience") or "") != executor:
        return _scope_binding_failure(
            "authorization_executor_or_audience_mismatch",
            actual_scope=permit.get("scope") if isinstance(permit.get("scope"), dict) else {},
            operation_id=operation_id,
            expected_owner=executor,
            actual_owner=str(permit.get("executor") or ""),
            expected_audience=executor,
            actual_audience=str(permit.get("audience") or ""),
            extra={"permit_ref": str(permit.get("permit_ref") or "")},
        )
    if str(permit.get("operation_id") or "") != operation_id:
        return _scope_binding_failure(
            "authorization_operation_mismatch",
            actual_scope=permit.get("scope") if isinstance(permit.get("scope"), dict) else {},
            operation_id=operation_id,
            extra={"permit_operation_id": str(permit.get("operation_id") or ""), "permit_ref": str(permit.get("permit_ref") or "")},
        )
    op_path = _operation_path(root, operation_id, executor)
    existing_op = _read(op_path) if op_path.exists() else {}
    if existing_op.get("status") == "prepared":
        # A prepared operation is only a private lookup binding. The first
        # side-effect gate must still atomically consume this exact permit.
        existing_op = {}
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
                "attenuation": permit.get("attenuation") or {},
                "authorization_thread": permit.get("authorization_thread") or {},
                "relevant_input_signature": permit.get("relevant_input_signature", ""),
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
                "user_message_hash": existing_op.get("user_message_hash", ""),
            "authorization_thread": existing_op.get("authorization_thread") or permit.get("authorization_thread") or {},
            "authorization_turn_id": permit.get("authorization_turn_id", ""),
            "attenuation": existing_op.get("attenuation") or permit.get("attenuation") or {},
            "relevant_input_signature": existing_op.get("relevant_input_signature") or permit.get("relevant_input_signature", ""),
            "status": existing_op.get("status", "permit_reserved"),
            "recovered_from_operation": recovered,
        }
    active = _introspect_permit_unlocked(root, permit, current_environment_snapshot, consumed_at)
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
        "attenuation": permit.get("attenuation") or {},
        "authorization_thread": permit.get("authorization_thread") or {},
        "user_message_ref": permit.get("user_message_ref", ""),
        "user_message_hash": permit.get("user_message_hash", ""),
        "relevant_input_signature": permit.get("relevant_input_signature", ""),
    }
    receipt.update(scope_signature_projection(permit.get("scope") or {}, permit_signature=str(permit.get("scope_signature") or "")))
    operation = {
        "schema": "scoped_authorization.operation.v2", "ok": True,
        "operation_id": operation_id, "operation_ref": str(op_path),
        "permit_ref": permit.get("permit_ref", ""), "intent_ref": permit.get("intent_ref", ""),
        "consumption_ref": str(consumption_path), "executor": executor,
        "idempotency_key": idempotency_key, "generation": permit.get("generation", 0),
        "status": "permit_reserved", "created_at": iso(moment), "updated_at": iso(moment),
        "user_message_ref": permit.get("user_message_ref", ""),
        "user_message_hash": permit.get("user_message_hash", ""),
        "attenuation": permit.get("attenuation") or {},
        "authorization_thread": permit.get("authorization_thread") or {},
        "relevant_input_signature": permit.get("relevant_input_signature", ""),
        "correlation": permit.get("correlation") if isinstance(permit.get("correlation"), dict) else {},
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
        transition = effect_transition_plan(current, status)
        if transition.get("reused"):
            return {
                **operation,
                "reused": True,
                **({"recovered_terminal_effect": True} if transition.get("recovered_terminal_effect") else {}),
            }
        if not transition.get("ok"):
            return transition
        if status in {"effect_observed", "completed"} and not (effect_receipt_ref or operation.get("effect_receipt_ref")):
            return {"ok": False, "reason": "authorization_effect_receipt_required"}
        operation_details = dict(operation.get("details") or {})
        if transition.get("implicit_effect_started"):
            operation_details["implicit_effect_started"] = {
                "from": transition["implicit_from"],
                "to": transition["implicit_to"],
                "recorded_at": iso(now_utc()),
                "owner": "scoped_authorization",
            }
        if details:
            operation_details.update(details)
        operation["status"] = status
        operation["effect_receipt_ref"] = effect_receipt_ref or operation.get("effect_receipt_ref", "")
        operation["compensation_ref"] = compensation_ref or operation.get("compensation_ref", "")
        operation["details"] = operation_details
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


def create_current_task_intent(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Thin facade for the current-task low-risk intent process owner."""

    from scoped_authorization_process import create_current_task_intent as create

    return create(*args, **kwargs)


def issue_current_task_permit(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Thin facade for the current-task low-risk permit process owner."""

    from scoped_authorization_process import issue_current_task_permit as issue

    return issue(*args, **kwargs)


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
    task_directive_ref: str = "",
    task_directive_hash: str = "",
    rollout_path: Path | str | None = None,
    sessions_root: Path | str | None = None,
    state_root: Path | str | None = None,
    ttl_seconds: int | None = DEFAULT_TTL_SECONDS,
    grant_ttl_seconds: int = DEFAULT_GRANT_CONSUME_TTL_SECONDS,
    permit_ttl_seconds: int = DEFAULT_PERMIT_TTL_SECONDS,
    policy_signature: str = "",
    max_uses: int = 1,
    issued_at: datetime | None = None,
) -> dict[str, Any]:
    normalized = normalize_rich_scope(scope)
    missing = [field for field in SCOPE_FIELDS if not normalized.get(field)]
    if missing:
        return {"ok": False, "reason": "authorization_scope_incomplete", "missing": missing}
    if max_uses != 1:
        return {"ok": False, "reason": "authorization_max_uses_must_be_one"}
    if bool(task_directive_ref) != bool(task_directive_hash):
        return {"ok": False, "reason": "authorization_task_directive_binding_incomplete"}
    authority = _authority_thread_binding(
        normalized, rollout_path=rollout_path, sessions_root=sessions_root,
    )
    if not authority.get("ok"):
        return authority
    normalized = authority["scope"]
    binding = _pre_challenge_direct_approval_binding(
        normalized, issued_at=issued_at, rollout_path=authority.get("rollout_path"), sessions_root=sessions_root,
        task_directive_ref=task_directive_ref, task_directive_hash=task_directive_hash,
    )
    if not binding.get("ok"):
        return binding
    task_directive_ref = str(binding.get("task_directive_ref") or task_directive_ref)
    task_directive_hash = str(binding.get("task_directive_hash") or task_directive_hash)
    root = _state_root(state_root)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    created = issued_at or now_utc()
    challenge_id = secrets.token_hex(16)
    preapproved = bool(binding.get("pre_challenge_approval_ref"))
    response_token, token_salt, response_token_hash = ("", "", "") if preapproved else scoped_authorization_evidence.token_material()
    challenge_ttl = _bounded_ttl(ttl_seconds, default=DEFAULT_TTL_SECONDS)
    payload = {
        "schema": "scoped_authorization.challenge.v2",
        "ok": True,
        "challenge_id": challenge_id,
        "challenge_ref": _ref("challenge", challenge_id),
        "scope": normalized,
        "scope_signature": scope_signature(normalized),
        "rich_scope_signature": rich_scope_signature(normalized),
        "relevant_input_signature": relevant_input_signature(normalized, policy_signature),
        "policy_signature": str(policy_signature or ""),
        "target_summary": str(target_summary or "")[:500],
        "issued_at": iso(created),
        "expires_at": _expiry(created, challenge_ttl),
        "challenge_expires_at": _expiry(created, challenge_ttl),
        "grant_ttl_seconds": _bounded_ttl(grant_ttl_seconds, default=DEFAULT_GRANT_CONSUME_TTL_SECONDS),
        "permit_ttl_seconds": _bounded_ttl(permit_ttl_seconds, default=DEFAULT_PERMIT_TTL_SECONDS),
        "max_uses": 1,
        "task_directive_ref": str(task_directive_ref or ""),
        "task_directive_hash": str(task_directive_hash or ""),
        "pre_challenge_approval_ref": str(binding.get("pre_challenge_approval_ref") or ""),
        "pre_challenge_approval_hash": str(binding.get("pre_challenge_approval_hash") or ""),
        "authorization_thread": authority["authorization_thread"],
        "response_token_salt": token_salt,
        "response_token_hash": response_token_hash,
        "status": "pending",
    }
    _write(root / "challenges" / f"{challenge_id}.json", payload)
    return {
        **payload,
        "response_token": response_token,
        "response_instruction": (
            "已绑定本任务中的直接批准；无需再次发送授权令牌。"
            if preapproved else f"请在后续用户消息中原样发送：{response_token}"
        ),
    }


def challenge_snapshot(
    challenge_ref: str,
    *,
    state_root: Path | str | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    """Read one challenge for its owning delivery projection without changing it."""

    root = _state_root(state_root)
    try:
        path = _safe_ref(challenge_ref, root, "challenge")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    challenge = _read(path)
    if not challenge.get("ok"):
        return challenge
    now = checked_at or now_utc()
    status = str(challenge.get("status") or "")
    if status == "pending" and _expired(_challenge_deadline(challenge), now):
        status = "expired"
    return {
        "schema": "scoped_authorization.challenge_snapshot.v1",
        "ok": True,
        "challenge_ref": str(challenge.get("challenge_ref") or challenge_ref),
        "scope": challenge.get("scope") if isinstance(challenge.get("scope"), dict) else {},
        "scope_signature": str(challenge.get("scope_signature") or ""),
        "authorization_thread": challenge.get("authorization_thread") or {},
        "target_summary": str(challenge.get("target_summary") or ""),
        "issued_at": str(challenge.get("issued_at") or ""),
        "expires_at": str(challenge.get("expires_at") or ""),
        "challenge_expires_at": _challenge_deadline(challenge),
        "relevant_input_signature": str(challenge.get("relevant_input_signature") or ""),
        "status": status,
        "grant_ref": str(challenge.get("grant_ref") or ""),
    }


def challenge_presentation(
    challenge_ref: str,
    *,
    response_token: str = "",
    state_root: Path | str | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    """Project one owner-issued challenge without minting or reconstructing a token."""

    if not str(challenge_ref or "").strip():
        return {"ok": False, "reason": "authorization_challenge_ref_required_for_presentation"}
    root = _state_root(state_root)
    try:
        path = _safe_ref(challenge_ref, root, "challenge")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    challenge = _read(path)
    if not challenge.get("ok"):
        return challenge
    now = checked_at or now_utc()
    status = str(challenge.get("status") or "")
    if status == "pending" and _expired(_challenge_deadline(challenge), now):
        status = "expired"
    stored_hash = str(challenge.get("response_token_hash") or "")
    stored_salt = str(challenge.get("response_token_salt") or "")
    token = str(response_token or "")
    if token and (not stored_hash or not hmac.compare_digest(scoped_authorization_evidence.token_hash(token, stored_salt), stored_hash)):
        return {"ok": False, "reason": "authorization_response_token_not_owner_issued", "challenge_ref": challenge_ref}
    if token:
        instruction = f"请在后续用户消息中原样发送：{token}"
        mode = "challenge_token"
    elif stored_hash:
        instruction = "challenge 已存在，但 owner 未提供可验证的原始令牌；不得自行生成 AUTHORIZE 文本。"
        mode = "challenge_token_unavailable"
    else:
        instruction = "已绑定本任务中的直接批准；无需发送授权令牌。"
        mode = "direct_approval"
    return {
        "schema": "scoped_authorization.challenge_presentation.v1",
        "ok": True,
        "challenge_ref": str(challenge.get("challenge_ref") or challenge_ref),
        "scope_signature": str(challenge.get("scope_signature") or ""),
        "status": status,
        "issued_at": str(challenge.get("issued_at") or ""),
        "expires_at": str(challenge.get("expires_at") or ""),
        "response_token": token,
        "response_instruction": instruction,
        "presentation_mode": mode,
    }


def cancel_challenge(
    challenge_ref: str,
    *,
    reason: str,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Fence an unused challenge when its owner replaces the exact decision scope."""

    root = _state_root(state_root)
    try:
        path = _safe_ref(challenge_ref, root, "challenge")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    with _locked(root):
        challenge = _read(path)
        if not challenge.get("ok"):
            return challenge
        status = str(challenge.get("status") or "")
        if status == "cancelled":
            return {"ok": True, "challenge_ref": challenge_ref, "status": status, "reused": True}
        if status != "pending":
            return {"ok": False, "reason": "authorization_challenge_not_cancellable", "status": status}
        challenge["status"] = "cancelled"
        challenge["cancellation_reason"] = str(reason or "scope_replaced")[:500]
        challenge["cancelled_at"] = iso(now_utc())
        _write(path, challenge)
        return {"ok": True, "challenge_ref": challenge_ref, "status": "cancelled"}


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


def _rollout_session_meta(rollout_path: Path) -> dict[str, Any]:
    try:
        with rollout_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if event.get("type") == "session_meta":
                    return payload
    except OSError:
        return {}
    return {}


def _rollout_thread_id(rollout_path: Path) -> str:
    payload = _rollout_session_meta(rollout_path)
    return str(payload.get("id") or payload.get("session_id") or "").strip()


def _rollout_parent_thread_id(rollout_path: Path) -> str:
    """Return only the Desktop-authored parent task identity from session metadata."""

    payload = _rollout_session_meta(rollout_path)
    parent = str(payload.get("parent_thread_id") or "").strip()
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    subagent = source.get("subagent") if isinstance(source.get("subagent"), dict) else {}
    spawned = subagent.get("thread_spawn") if isinstance(subagent.get("thread_spawn"), dict) else {}
    declared = str(spawned.get("parent_thread_id") or "").strip()
    if parent and declared and parent != declared:
        return ""
    return parent or declared


def discover_rollout_path(thread_id: str, *, sessions_root: Path | str | None = None) -> dict[str, Any]:
    """Resolve one rollout only when its declared session id matches exactly."""

    root = Path(sessions_root or (DEFAULT_STATE_ROOT.parents[1] / "sessions")).expanduser()
    if not root.is_dir():
        return {"ok": False, "reason": "authorization_sessions_root_unavailable", "sessions_root": str(root)}
    matches = [path.resolve() for path in root.rglob("*.jsonl") if _rollout_thread_id(path) == str(thread_id or "")]
    if not matches:
        return {"ok": False, "reason": "authorization_rollout_not_found", "thread_id": str(thread_id or "")}
    if len(matches) != 1:
        return {
            "ok": False,
            "reason": "authorization_rollout_ambiguous",
            "thread_id": str(thread_id or ""),
            "candidate_paths": [str(path) for path in matches[:20]],
        }
    return {"ok": True, "rollout_path": str(matches[0]), "thread_id": str(thread_id or ""), "discovery": "exact_session_id"}


def _authority_rollout_path(thread_id: str, *, sessions_root: Path | str | None, executor_rollout: Path | None) -> Path | None:
    """Find the parent rollout without treating a delegation message as authority."""

    roots: list[Path | str | None] = [sessions_root]
    if sessions_root is None:
        roots.append(None)
    if executor_rollout is not None:
        roots.append(executor_rollout.parent)
    seen: set[str] = set()
    for root in roots:
        key = str(root or "<default>")
        if key in seen:
            continue
        seen.add(key)
        discovery = discover_rollout_path(thread_id, sessions_root=root)
        if discovery.get("ok"):
            return Path(str(discovery["rollout_path"]))
    return None


def _authority_thread_binding(
    scope: dict[str, Any], *, rollout_path: Path | str | None, sessions_root: Path | str | None,
) -> dict[str, Any]:
    """Bind low-risk subtask challenges to Desktop's parent-task session identity.

    The parent relation comes exclusively from the rollout's ``session_meta``.
    Delegation envelopes are user-message content and are deliberately ignored.
    """

    normalized = normalize_rich_scope(scope)
    thread_id = str(normalized.get("thread_id") or "")
    risk = str(normalized.get("risk") or "").upper()
    if risk not in {"R0", "R1", "R2"}:
        return {
            "ok": True,
            "scope": normalized,
            "rollout_path": rollout_path,
            "authorization_thread": {"thread_id": thread_id, "executor_thread_id": thread_id, "source": "scope_thread_id"},
        }

    executor_rollout = Path(rollout_path).expanduser().resolve() if rollout_path else None
    if executor_rollout is None and (sessions_root is not None or CODEX_APP_THREAD_ID_RE.fullmatch(thread_id)):
        discovery = discover_rollout_path(thread_id, sessions_root=sessions_root)
        if discovery.get("ok"):
            executor_rollout = Path(str(discovery["rollout_path"]))
    if executor_rollout is None:
        return {
            "ok": True,
            "scope": normalized,
            "rollout_path": None,
            "authorization_thread": {"thread_id": thread_id, "executor_thread_id": thread_id, "source": "scope_thread_id"},
        }

    executor_thread_id = _rollout_thread_id(executor_rollout)
    if executor_thread_id != thread_id:
        return {"ok": False, "reason": "authorization_rollout_thread_mismatch"}
    parent_thread_id = _rollout_parent_thread_id(executor_rollout)
    if not parent_thread_id or parent_thread_id == executor_thread_id:
        return {
            "ok": True,
            "scope": normalized,
            "rollout_path": executor_rollout,
            "authorization_thread": {"thread_id": thread_id, "executor_thread_id": executor_thread_id, "source": "scope_thread_id"},
        }
    normalized["thread_id"] = parent_thread_id
    authority_rollout = _authority_rollout_path(
        parent_thread_id, sessions_root=sessions_root, executor_rollout=executor_rollout,
    )
    return {
        "ok": True,
        "scope": normalized,
        "rollout_path": authority_rollout,
        "authorization_thread": {
            "thread_id": parent_thread_id,
            "executor_thread_id": executor_thread_id,
            "source": "codex_app_session_meta.parent_thread_id",
        },
    }


def _pre_challenge_direct_approval_binding(
    scope: dict[str, Any], *, issued_at: datetime | None, rollout_path: Path | str | None,
    sessions_root: Path | str | None, task_directive_ref: str, task_directive_hash: str,
) -> dict[str, Any]:
    """Bind one current low-risk task approval without minting a token."""

    if str(scope.get("risk") or "").upper() not in {"R0", "R1", "R2"}:
        return {"ok": True}
    if rollout_path:
        rollout = Path(rollout_path).expanduser().resolve()
        if _rollout_thread_id(rollout) != str(scope.get("thread_id") or ""):
            return {"ok": False, "reason": "authorization_rollout_thread_mismatch"}
    else:
        if sessions_root is None and not CODEX_APP_THREAD_ID_RE.fullmatch(str(scope.get("thread_id") or "")):
            return {"ok": True}
        discovery = discover_rollout_path(str(scope.get("thread_id") or ""), sessions_root=sessions_root)
        if not discovery.get("ok"):
            return {"ok": True, "rollout_discovery": discovery}
        rollout = Path(str(discovery["rollout_path"]))
    created = issued_at or now_utc()
    messages: list[dict[str, str]] = []
    for row in _user_messages(rollout):
        try:
            if parse_time(row["timestamp"]) <= created:
                messages.append(row)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "authorization_user_message_timestamp_invalid", "message_ref": row.get("message_ref", "")}
    trusted = [
        row for row in messages
        if not any(marker in row["text"].lower() for marker in scoped_authorization_evidence.UNTRUSTED_MARKERS)
        and not scoped_authorization_evidence.TOKEN_RE.fullmatch(row["text"].strip())
    ]
    if not trusted:
        return {"ok": True}
    approval = trusted[-1]
    if not scoped_authorization_evidence.extract_direct_approval_evidence(approval["text"]).get("ok"):
        return {"ok": True}
    directive = next((candidate for candidate in reversed(trusted[:-1]) if scoped_authorization_evidence.extract_direct_task_evidence(candidate["text"]).get("ok")), None)
    if directive is None:
        return {"ok": True}
    if task_directive_ref and task_directive_ref != directive["message_ref"]:
        return {"ok": False, "reason": "authorization_task_directive_binding_changed"}
    if task_directive_hash and task_directive_hash != directive["message_hash"]:
        return {"ok": False, "reason": "authorization_task_directive_unverified"}
    return {"ok": True, "task_directive_ref": directive["message_ref"], "task_directive_hash": directive["message_hash"], "pre_challenge_approval_ref": approval["message_ref"], "pre_challenge_approval_hash": approval["message_hash"]}


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
        actual_thread_id = _rollout_thread_id(rollout_path)
        expected_thread_id = str(challenge["scope"].get("thread_id") or "")
        if actual_thread_id != expected_thread_id:
            return _scope_binding_failure(
                "authorization_rollout_thread_mismatch",
                challenge_ref=challenge_ref,
                extra={
                    "expected_thread_id": expected_thread_id,
                    "actual_thread_id": actual_thread_id,
                },
            )
        status = str(challenge.get("status") or "")
        if status == "cancelled":
            return {"ok": False, "reason": "authorization_challenge_cancelled"}
        if status == "granted":
            grant_ref = str(challenge.get("grant_ref") or "")
            if not grant_ref:
                return {"ok": False, "reason": "authorization_challenge_grant_missing"}
            grant = _read(_safe_ref(grant_ref, root, "grant"))
            return {**grant, "reused": bool(grant.get("ok")), "first_evidence_wins": True}
        if status != "pending":
            return {"ok": False, "reason": f"authorization_challenge_{status or 'inactive'}"}
        now = checked_at or now_utc()
        if _expired(_challenge_deadline(challenge), now):
            return {"ok": False, "reason": "authorization_challenge_expired"}
        try:
            issued = parse_time(challenge["issued_at"])
        except (TypeError, ValueError):
            return {"ok": False, "reason": "authorization_challenge_timestamp_invalid"}
        evidence: dict[str, str] | None = None
        binding_mode = ""
        token_present = False
        attenuation: dict[str, Any] = {"ok": True, "note": "", "prohibitions": [], "budget_limits": {}, "context": {}, "recognized": False}
        directive_ref = str(challenge.get("task_directive_ref") or "")
        directive_hash = str(challenge.get("task_directive_hash") or "")
        approval_ref = str(challenge.get("pre_challenge_approval_ref") or "")
        approval_hash = str(challenge.get("pre_challenge_approval_hash") or "")
        if approval_ref:
            directive = next((row for row in _user_messages(rollout_path) if row["message_ref"] == directive_ref), None)
            approval = next((row for row in _user_messages(rollout_path) if row["message_ref"] == approval_ref), None)
            if directive is None or directive["message_hash"] != directive_hash or approval is None or approval["message_hash"] != approval_hash:
                return {"ok": False, "reason": "authorization_pre_challenge_approval_unverified"}
            try:
                directive_at, approval_at = parse_time(directive["timestamp"]), parse_time(approval["timestamp"])
            except (TypeError, ValueError):
                return {"ok": False, "reason": "authorization_user_message_timestamp_invalid", "message_ref": approval_ref}
            if directive_at > approval_at or approval_at > issued:
                return {"ok": False, "reason": "authorization_pre_challenge_approval_order_invalid"}
            if not scoped_authorization_evidence.extract_direct_task_evidence(directive["text"]).get("ok") or not scoped_authorization_evidence.extract_direct_approval_evidence(approval["text"]).get("ok"):
                return {"ok": False, "reason": "authorization_pre_challenge_approval_unverified"}
            evidence = approval
            attenuation = scoped_authorization_evidence.extract_direct_approval_evidence(approval["text"])["attenuation"]
            binding_mode = "pre_challenge_direct_approval"
        elif directive_ref and str(challenge["scope"].get("risk") or "").upper() in {"R0", "R1", "R2"}:
            directive = next((row for row in _user_messages(rollout_path) if row["message_ref"] == directive_ref), None)
            if directive is None or directive["message_hash"] != directive_hash:
                return {"ok": False, "reason": "authorization_task_directive_unverified"}
            try:
                directive_at = parse_time(directive["timestamp"])
            except (TypeError, ValueError):
                return {"ok": False, "reason": "authorization_user_message_timestamp_invalid", "message_ref": directive_ref}
            direct = scoped_authorization_evidence.extract_direct_task_evidence(directive["text"])
            if directive_at <= issued and direct.get("ok"):
                evidence = directive
                attenuation = direct["attenuation"]
                binding_mode = "pre_challenge_direct_task"
        for message in _user_messages(rollout_path):
            if evidence is not None:
                break
            if not message["timestamp"]:
                continue
            try:
                observed_at = parse_time(message["timestamp"])
            except (TypeError, ValueError):
                return {"ok": False, "reason": "authorization_user_message_timestamp_invalid", "message_ref": message["message_ref"]}
            if observed_at <= issued:
                continue
            token_present = bool(scoped_authorization_evidence.TOKEN_RE.search(message["text"]))
            if challenge.get("response_token_hash") and token_present:
                parsed = scoped_authorization_evidence.extract_direct_user_evidence(
                    message["text"], salt=str(challenge.get("response_token_salt") or ""),
                    expected_hash=str(challenge.get("response_token_hash") or ""),
                )
            elif challenge.get("response_token_hash"):
                parsed = scoped_authorization_evidence.extract_direct_approval_evidence(message["text"])
            else:
                parsed = scoped_authorization_evidence.extract_direct_approval_evidence(message["text"])
                if not parsed.get("ok") and token_present:
                    parsed = {
                        "ok": message["text"].strip() == str(challenge.get("response_token") or ""),
                        "reason": "authorization_response_token_not_for_challenge",
                        "attenuation": attenuation,
                    }
            if not parsed.get("ok") and str(parsed.get("reason") or "") in {
                "authorization_response_multiple_tokens",
                "authorization_response_token_not_for_challenge",
                "authorization_attenuation_expansion_or_ambiguous",
                "authorization_attenuation_needs_input",
            }:
                return parsed
            if parsed.get("ok"):
                evidence = message
                attenuation = parsed.get("attenuation") if isinstance(parsed.get("attenuation"), dict) else attenuation
                binding_mode = str(parsed.get("binding_mode") or "response_token")
                break
        if evidence is None:
            return {"ok": False, "reason": "post_challenge_user_response_not_found"}
        turn_id = authorization_turn_id(evidence["message_ref"], evidence["message_hash"])
        grant_id = digest({"challenge_id": challenge["challenge_id"]})[:32]
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
        grant_ttl = _bounded_ttl(challenge.get("grant_ttl_seconds"), default=DEFAULT_GRANT_CONSUME_TTL_SECONDS)
        permit_ttl = _bounded_ttl(challenge.get("permit_ttl_seconds"), default=DEFAULT_PERMIT_TTL_SECONDS)
        payload = {
            "schema": "scoped_authorization.grant.v2",
            "ok": True,
            "grant_id": grant_id,
            "grant_ref": _ref("grant", grant_id),
            "challenge_ref": challenge["challenge_ref"],
            "scope": challenge["scope"],
            "scope_signature": challenge["scope_signature"],
            "issued_at": iso(now),
            "expires_at": _expiry(now, grant_ttl),
            "grant_consume_by": _expiry(now, grant_ttl),
            "challenge_safety_expires_at": _challenge_deadline(challenge),
            "permit_ttl_seconds": permit_ttl,
            "relevant_input_signature": str(challenge.get("relevant_input_signature") or relevant_input_signature(challenge["scope"])),
            "policy_signature": str(challenge.get("policy_signature") or ""),
            "attenuation": attenuation,
            "authorization_thread": challenge.get("authorization_thread") or {
                "thread_id": str(challenge.get("scope", {}).get("thread_id") or ""),
                "executor_thread_id": str(challenge.get("scope", {}).get("thread_id") or ""),
                "source": "legacy_scope_thread_id",
            },
            "owner_context": {
                "requested_by_owner": str(challenge.get("scope", {}).get("requested_by_owner") or ""),
                "phase": str(challenge.get("scope", {}).get("phase") or ""),
                "allowed_executors": sorted({str(item) for item in challenge.get("scope", {}).get("allowed_executors") or [] if str(item).strip()}),
                "attachment_context": attenuation.get("context") if isinstance(attenuation.get("context"), dict) else {},
            },
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
            "response_token_matched": bool(token_present),
            "authorization_binding_mode": binding_mode or "response_token",
        }
        _write(grant_path, payload)
        challenge["status"] = "granted"
        challenge["grant_ref"] = payload["grant_ref"]
        challenge["first_evidence_ref"] = evidence["message_ref"]
        _write(challenge_path, challenge)
        return payload


def consume_challenge(
    challenge_ref: str,
    *,
    consumer_owner: str,
    operation_id: str,
    expected_scope: dict[str, Any] | None = None,
    audience: str = "",
    fallback_action: str = "",
    rollout_path: Path | str | None = None,
    sessions_root: Path | str | None = None,
    state_root: Path | str | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    """Discover, grant, and consume one eligible challenge without widening scope."""

    root = _state_root(state_root)
    try:
        challenge_path = _safe_ref(challenge_ref, root, "challenge")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    challenge = _read(challenge_path)
    if not challenge.get("ok"):
        return challenge
    scope = challenge.get("scope") if isinstance(challenge.get("scope"), dict) else {}
    if expected_scope is not None:
        normalized_expected = normalize_rich_scope(expected_scope)
        actual_scope = normalize_rich_scope(scope)
        mismatched = _scope_mismatch(actual_scope, normalized_expected)
        if actual_scope.get("fallback_actions") != normalized_expected.get("fallback_actions"):
            mismatched.append("fallback_actions")
        if mismatched:
            return _scope_binding_failure(
                "authorization_challenge_scope_mismatch",
                expected_scope=normalized_expected,
                actual_scope=actual_scope,
                mismatched_fields=mismatched,
                challenge_ref=challenge_ref,
                operation_id=operation_id,
            )
    if fallback_action:
        fallback = validate_same_scope_fallback(scope, fallback_action)
        if not fallback.get("ok"):
            return {
                **fallback,
                "challenge_ref": challenge_ref,
                "operation_id": operation_id,
            }
    if str(scope.get("risk") or "").upper() not in {"R0", "R1", "R2"}:
        return {"ok": False, "reason": "authorization_challenge_consume_risk_not_eligible"}
    allowed = {str(item).strip() for item in scope.get("allowed_executors") or [] if str(item).strip()}
    requested_owner = str(scope.get("requested_by_owner") or "").strip()
    requested_audience = str(audience or consumer_owner or "").strip()
    if str(consumer_owner or "").strip() != requested_owner or (allowed and consumer_owner not in allowed):
        return _scope_binding_failure(
            "authorization_challenge_consume_owner_not_bound",
            expected_scope=scope,
            actual_scope=expected_scope,
            challenge_ref=challenge_ref,
            operation_id=operation_id,
            expected_owner=requested_owner,
            actual_owner=consumer_owner,
            extra={"allowed_executors": sorted(allowed)},
        )
    if requested_audience != requested_owner:
        return _scope_binding_failure(
            "authorization_challenge_consume_audience_not_bound",
            expected_scope=scope,
            actual_scope=expected_scope,
            challenge_ref=challenge_ref,
            operation_id=operation_id,
            expected_audience=requested_owner,
            actual_audience=requested_audience,
        )
    if rollout_path:
        rollout = Path(rollout_path).expanduser().resolve()
        discovery = {"ok": True, "rollout_path": str(rollout), "discovery": "explicit"}
    else:
        discovery = discover_rollout_path(str(scope.get("thread_id") or ""), sessions_root=sessions_root)
        if not discovery.get("ok"):
            return {**discovery, "challenge_ref": challenge_ref, "operation_id": operation_id}
        rollout = Path(str(discovery["rollout_path"]))
    grant = grant_from_thread(challenge_ref, rollout_path=rollout, state_root=root, checked_at=checked_at)
    if not grant.get("ok"):
        return {**grant, "discovery": discovery}
    consumed = consume_grant(
        str(grant["grant_ref"]), scope, consumer_owner=consumer_owner,
        operation_id=operation_id, state_root=root, consumed_at=checked_at,
    )
    if not consumed.get("ok"):
        return {**consumed, "challenge_ref": challenge_ref, "grant_ref": grant["grant_ref"], "discovery": discovery}
    permit_signature = str(consumed.get("scope_signature") or "")
    return {
        **consumed,
        "schema": "scoped_authorization.challenge_consume.v1",
        "challenge_ref": challenge_ref,
        "grant_ref": grant["grant_ref"],
        "discovery": discovery,
        "authorization_path": ["challenge", "grant", "one_time_intent", "permit", "consume"],
        **({
            "fallback_action": fallback_action,
            "fallback_authorization": "same_scope_fallback",
        } if fallback_action else {}),
        **scope_signature_projection(
            scope,
            challenge_signature=str(challenge.get("scope_signature") or ""),
            permit_signature=permit_signature,
            operation_signature=permit_signature,
        ),
    }


def _scope_mismatch(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    return [field for field in SCOPE_FIELDS if str(actual.get(field) or "") != str(expected.get(field) or "")]


def _scope_binding_failure(
    reason: str,
    *,
    expected_scope: dict[str, Any] | None = None,
    actual_scope: dict[str, Any] | None = None,
    mismatched_fields: list[str] | None = None,
    challenge_ref: str = "",
    grant_ref: str = "",
    operation_id: str = "",
    expected_owner: str = "",
    actual_owner: str = "",
    expected_audience: str = "",
    actual_audience: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return bounded binding evidence without copying tokens or user content."""

    result: dict[str, Any] = {"ok": False, "reason": reason}
    if expected_scope is not None:
        result["expected_scope"] = {
            field: str(expected_scope.get(field) or "") for field in SCOPE_FIELDS
        }
    if actual_scope is not None:
        result["actual_scope"] = {
            field: str(actual_scope.get(field) or "") for field in SCOPE_FIELDS
        }
    if mismatched_fields:
        result["mismatched_fields"] = sorted(set(mismatched_fields))
    for key, value in (
        ("challenge_ref", challenge_ref), ("grant_ref", grant_ref), ("operation_id", operation_id),
        ("expected_owner", expected_owner), ("actual_owner", actual_owner),
        ("expected_audience", expected_audience), ("actual_audience", actual_audience),
    ):
        if str(value or "").strip():
            result[key] = str(value)
    if extra:
        result.update(extra)
    return result


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
        return _scope_binding_failure(
            "authorization_scope_mismatch",
            expected_scope=expected_scope,
            actual_scope=grant.get("scope") if isinstance(grant.get("scope"), dict) else {},
            mismatched_fields=mismatched,
            grant_ref=grant_ref,
        )
    if grant.get("scope_signature") != scope_signature(expected_scope):
        return _scope_binding_failure(
            "authorization_scope_signature_mismatch",
            expected_scope=expected_scope,
            actual_scope=grant.get("scope") if isinstance(grant.get("scope"), dict) else {},
            grant_ref=grant_ref,
        )
    actual_rich_signature = rich_scope_signature(grant.get("scope") if isinstance(grant.get("scope"), dict) else {})
    expected_rich_signature = rich_scope_signature(expected_scope)
    if actual_rich_signature != expected_rich_signature:
        return _scope_binding_failure(
            "authorization_rich_scope_signature_mismatch",
            expected_scope=expected_scope,
            actual_scope=grant.get("scope") if isinstance(grant.get("scope"), dict) else {},
            grant_ref=grant_ref,
            extra={
                "expected_rich_scope_signature": expected_rich_signature,
                "actual_rich_scope_signature": actual_rich_signature,
            },
        )
    expected_relevant = relevant_input_signature(expected_scope, str(grant.get("policy_signature") or ""))
    if grant.get("relevant_input_signature") and grant.get("relevant_input_signature") != expected_relevant:
        return {"ok": False, "reason": "authorization_relevant_input_fenced"}
    if grant.get("status") != "active":
        return {"ok": False, "reason": f"authorization_grant_{grant.get('status') or 'inactive'}"}
    now = checked_at or now_utc()
    if _expired(_grant_deadline(grant), now):
        return {"ok": False, "reason": "authorization_grant_expired"}
    attenuation_check = scoped_authorization_evidence.attenuation_allows_scope(
        grant.get("attenuation") if isinstance(grant.get("attenuation"), dict) else {}, expected_scope,
    )
    if not attenuation_check.get("ok"):
        return attenuation_check
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
    return {
        "ok": True, "grant_ref": grant_ref, "scope_signature": grant["scope_signature"],
        "user_message_ref": grant["user_message_ref"], "attenuation": grant.get("attenuation") or {},
        "owner_context": grant.get("owner_context") or {},
        "relevant_input_signature": grant.get("relevant_input_signature") or "",
    }


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
                    return _scope_binding_failure(
                        "authorization_grant_consumed",
                        grant_ref=grant_ref,
                        operation_id=operation_id,
                        extra={"existing_operation_id": str(grant.get("consumed_operation_id") or "")},
                    )
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
                permit_ttl_seconds=_bounded_ttl(grant.get("permit_ttl_seconds"), default=DEFAULT_PERMIT_TTL_SECONDS),
                issued_at=consumed_at,
            )
            if not permit.get("ok"):
                return permit
            permit_path = _safe_ref(str(permit["permit_ref"]), root, "permit")
            permit["user_message_ref"] = grant.get("user_message_ref", "")
            permit["attenuation"] = grant.get("attenuation") or {}
            permit["relevant_input_signature"] = grant.get("relevant_input_signature", "")
            permit["authorization_thread"] = grant.get("authorization_thread") or {}
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
            return {
                **result,
                "grant_ref": grant_ref,
                "user_message_ref": grant.get("user_message_ref", ""),
                **scope_signature_projection(
                    expected_scope,
                    challenge_signature=str(grant.get("scope_signature") or ""),
                    permit_signature=str(result.get("scope_signature") or ""),
                    operation_signature=str(result.get("scope_signature") or ""),
                ),
            }
        existing = _read(receipt_path) if receipt_path.exists() else {}
        if existing.get("ok"):
            if existing.get("grant_ref") != grant_ref:
                return _scope_binding_failure(
                    "authorization_operation_already_bound_to_another_grant",
                    grant_ref=grant_ref,
                    operation_id=operation_id,
                    extra={"existing_grant_ref": str(existing.get("grant_ref") or "")},
                )
            return {**existing, "reused": True}
        grant = _read(grant_path)
        if grant.get("status") == "consumed":
            if grant.get("consumption_operation_digest") != operation_digest:
                return _scope_binding_failure(
                    "authorization_grant_consumed",
                    grant_ref=grant_ref,
                    operation_id=operation_id,
                    extra={"existing_operation_id": str(grant.get("consumed_operation_id") or "")},
                )
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
    challenge_consume_parser = sub.add_parser("challenge-consume", help="discover, grant, and consume one challenged R0-R2 scope")
    challenge_consume_parser.add_argument("--challenge-ref", required=True)
    challenge_consume_parser.add_argument("--consumer-owner", required=True)
    challenge_consume_parser.add_argument("--operation-id", required=True)
    challenge_consume_parser.add_argument("--expected-scope-json", default="")
    challenge_consume_parser.add_argument("--audience", default="")
    challenge_consume_parser.add_argument("--fallback-action", default="")
    challenge_consume_parser.add_argument("--rollout-path", default="")
    challenge_consume_parser.add_argument("--sessions-root", default="")
    challenge_consume_parser.add_argument("--state-root", default="")
    operation_parser = sub.add_parser("operation", help="read one bounded non-secret operation projection")
    operation_parser.add_argument("--operation-id", required=True)
    operation_parser.add_argument("--executor", required=True)
    operation_parser.add_argument("--state-root", default="")
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
    elif args.action == "challenge-consume":
        expected_scope = {}
        if args.expected_scope_json:
            try:
                expected_scope = json.loads(Path(args.expected_scope_json).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {"ok": False, "reason": "authorization_expected_scope_json_invalid"}
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                return 1
        payload = consume_challenge(
            args.challenge_ref, consumer_owner=args.consumer_owner, operation_id=args.operation_id,
            expected_scope=expected_scope or None, audience=args.audience,
            fallback_action=args.fallback_action,
            rollout_path=args.rollout_path or None, sessions_root=args.sessions_root or None,
            state_root=root,
        )
    elif args.action == "operation":
        payload = operation_projection(args.operation_id, executor=args.executor, state_root=root)
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
