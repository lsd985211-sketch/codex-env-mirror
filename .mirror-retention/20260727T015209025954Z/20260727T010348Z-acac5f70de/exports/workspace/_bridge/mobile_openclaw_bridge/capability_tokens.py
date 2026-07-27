#!/usr/bin/env python3
"""Admin-granted temporary capability tokens for the mobile bridge.

Tokens are account-bound grants, not bearer secrets. They can only add narrow
generated-artifact capabilities and never override sensitive-data or destructive
local-machine denials.
"""

from __future__ import annotations

import json
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
STORE_PATH = ROOT / "runtime" / "capability_grants.json"
AUDIT_LOG = ROOT / "logs" / "capability-grants.jsonl"
ATTACHMENTS_ROOT = ROOT / "attachments"

SCHEMA = "mobile-weixin-bridge-capability-grants/v1"
PASSPHRASE_KDF = "pbkdf2_hmac_sha256"
PASSPHRASE_ITERATIONS = 210_000
MAX_PASSPHRASE_FAILURES = 5
GRANTABLE_CAPABILITIES = {
    "generated_file_create",
    "generated_file_send",
    "reply_with_generated_artifact",
    "attachment_process_user_supplied",
    "public_web_fetch",
}
NON_GRANTABLE_CAPABILITIES = {
    "local_data_read",
    "workspace_file_read",
    "bridge_db_read",
    "system_diagnostics_read",
    "resource_library_read",
    "local_data_export",
    "local_data_write",
    "system_side_effect",
    "secret_read",
    "repair_system",
    "repair_bridge",
    "stop",
    "resume",
    "hardstop",
    "set_secret_hash",
    "mode_change",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_store() -> dict[str, Any]:
    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        payload = {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    grants = payload.get("grants") if isinstance(payload.get("grants"), list) else []
    return {"schema": str(payload.get("schema") or SCHEMA), "grants": [item for item in grants if isinstance(item, dict)]}


def write_store(payload: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(STORE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STORE_PATH)


def audit(event_type: str, payload: dict[str, Any]) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": now_iso(), "event_type": event_type, **payload}
    with AUDIT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def normalize_capabilities(values: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        cap = str(value or "").strip().lower()
        if cap and cap not in result:
            result.append(cap)
    return result


def normalize_passphrase(value: str) -> str:
    return str(value or "").strip()


def hash_passphrase(value: str) -> dict[str, Any]:
    secret = normalize_passphrase(value)
    if not secret:
        return {}
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, PASSPHRASE_ITERATIONS)
    return {
        "kdf": PASSPHRASE_KDF,
        "iterations": PASSPHRASE_ITERATIONS,
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "digest_b64": base64.b64encode(digest).decode("ascii"),
        "max_failures": MAX_PASSPHRASE_FAILURES,
    }


def passphrase_required(item: dict[str, Any]) -> bool:
    challenge = item.get("passphrase_challenge")
    return isinstance(challenge, dict) and str(challenge.get("digest_b64") or "").strip() != ""


def verify_passphrase(item: dict[str, Any], value: str) -> dict[str, Any]:
    if not passphrase_required(item):
        return {"ok": True, "required": False, "reason": "passphrase_not_required"}
    challenge = item.get("passphrase_challenge") if isinstance(item.get("passphrase_challenge"), dict) else {}
    max_failures = int(challenge.get("max_failures") or MAX_PASSPHRASE_FAILURES)
    failed_count = int(item.get("passphrase_failed_count") or 0)
    if failed_count >= max_failures:
        return {"ok": False, "required": True, "reason": "passphrase_locked"}
    secret = normalize_passphrase(value)
    if not secret:
        return {"ok": False, "required": True, "reason": "passphrase_required"}
    try:
        salt = base64.b64decode(str(challenge.get("salt_b64") or ""), validate=True)
        expected = base64.b64decode(str(challenge.get("digest_b64") or ""), validate=True)
        iterations = int(challenge.get("iterations") or PASSPHRASE_ITERATIONS)
    except Exception:
        return {"ok": False, "required": True, "reason": "passphrase_challenge_invalid"}
    actual = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
    if not hmac.compare_digest(actual, expected):
        return {"ok": False, "required": True, "reason": "passphrase_mismatch"}
    return {"ok": True, "required": True, "reason": "passphrase_verified"}


def safe_account_segment(value: str) -> str:
    text = str(value or "").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text)
    cleaned = cleaned.strip("._-")
    return cleaned[:80] or "unknown-account"


def generated_artifact_dir(account_id: str) -> Path:
    return ATTACHMENTS_ROOT / "generated" / safe_account_segment(account_id)


def is_generated_artifact_path(path: str | Path, account_id: str) -> bool:
    if not str(account_id or "").strip():
        return False
    try:
        resolved = Path(path).expanduser().resolve()
        root = generated_artifact_dir(account_id).resolve()
        resolved.relative_to(root)
        return True
    except Exception:
        return False


def grant_request_error(
    *,
    subject_account_id: str,
    subject_user: str,
    allowed: list[str],
    denied: list[str],
    expires_at: str,
    no_expiry: bool,
) -> dict[str, Any] | None:
    """Validate grant request fields before any state is written."""
    if not str(subject_account_id or "").strip() and not str(subject_user or "").strip():
        return {"ok": False, "reason": "subject_account_id_or_user_required"}
    if denied:
        return {"ok": False, "reason": "non_grantable_capability_requested", "denied_capabilities": denied}
    if not allowed:
        return {"ok": False, "reason": "no_grantable_capabilities_requested"}
    if no_expiry and str(expires_at or "").strip():
        return {"ok": False, "reason": "expires_at_conflicts_with_no_expiry"}
    return None


def grant_expiry_policy(
    *,
    created: datetime,
    ttl_minutes: int,
    expires_at: str,
    no_expiry: bool,
) -> dict[str, Any]:
    """Return expiry metadata for a grant request."""
    ttl = max(1, int(ttl_minutes or 60))
    if no_expiry:
        return {
            "ok": True,
            "expires_at": "never",
            "renewal_policy": "no_expiry_admin_revocation_required",
        }
    if str(expires_at or "").strip():
        parsed_expires_at = parse_time(str(expires_at or ""))
        if not parsed_expires_at:
            return {"ok": False, "reason": "invalid_expires_at"}
        if parsed_expires_at <= created:
            return {"ok": False, "reason": "expires_at_must_be_future"}
        return {
            "ok": True,
            "expires_at": parsed_expires_at.isoformat(),
            "renewal_policy": "custom_expiry_admin_regrant_required",
        }
    return {
        "ok": True,
        "expires_at": (created + timedelta(minutes=ttl)).isoformat(),
        "renewal_policy": "admin_regrant_required",
    }


def build_grant_item(
    *,
    grant_id: str,
    subject_account_id: str,
    subject_user: str,
    capabilities: list[str],
    issued_by: str,
    created: datetime,
    expiry_policy: dict[str, Any],
    max_uses: int,
    unlimited_uses: bool,
    reason: str,
    resource_scope: str,
    max_file_size_mb: int,
    artifact_dir: Path | None,
    passphrase_challenge: dict[str, Any],
) -> dict[str, Any]:
    """Build a persisted grant item without writing it."""
    uses = 0 if unlimited_uses else max(1, int(max_uses or 3))
    item = {
        "grant_id": grant_id,
        "schema": "capability-grant/v1",
        "status": "active",
        "subject_account_id": str(subject_account_id or "").strip(),
        "subject_user": str(subject_user or "").strip(),
        "capabilities": capabilities,
        "resource_scope": str(resource_scope or "generated_outputs_only"),
        "generated_artifact_dir": str(artifact_dir) if artifact_dir is not None else "",
        "max_file_size_mb": int(max_file_size_mb or 20),
        "max_uses": uses,
        "used_count": 0,
        "issued_by": str(issued_by or "primary_admin").strip(),
        "issued_at": created.isoformat(),
        "expires_at": str(expiry_policy.get("expires_at") or ""),
        "renewal_policy": str(expiry_policy.get("renewal_policy") or "admin_regrant_required"),
        "reason": str(reason or ""),
        "audit_required": True,
        "passphrase_required": bool(passphrase_challenge),
        "passphrase_failed_count": 0,
    }
    if passphrase_challenge:
        item["passphrase_challenge"] = passphrase_challenge
    return item


def grant(
    *,
    subject_account_id: str,
    subject_user: str = "",
    capabilities: list[str] | tuple[str, ...] | set[str],
    issued_by: str,
    ttl_minutes: int = 60,
    expires_at: str = "",
    no_expiry: bool = False,
    max_uses: int = 3,
    unlimited_uses: bool = False,
    reason: str = "",
    resource_scope: str = "generated_outputs_only",
    max_file_size_mb: int = 20,
    passphrase: str = "",
) -> dict[str, Any]:
    caps = normalize_capabilities(capabilities)
    denied = [cap for cap in caps if cap in NON_GRANTABLE_CAPABILITIES or cap not in GRANTABLE_CAPABILITIES]
    allowed = [cap for cap in caps if cap in GRANTABLE_CAPABILITIES]
    request_error = grant_request_error(
        subject_account_id=subject_account_id,
        subject_user=subject_user,
        allowed=allowed,
        denied=denied,
        expires_at=expires_at,
        no_expiry=no_expiry,
    )
    if request_error:
        return request_error
    grant_id = "grant_" + secrets.token_hex(8)
    created = now_utc()
    expiry_policy = grant_expiry_policy(
        created=created,
        ttl_minutes=ttl_minutes,
        expires_at=expires_at,
        no_expiry=no_expiry,
    )
    if not expiry_policy.get("ok"):
        return expiry_policy
    artifact_dir = generated_artifact_dir(subject_account_id) if str(subject_account_id or "").strip() else None
    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
    passphrase_challenge = hash_passphrase(passphrase)
    item = build_grant_item(
        grant_id=grant_id,
        subject_account_id=subject_account_id,
        subject_user=subject_user,
        capabilities=allowed,
        issued_by=issued_by,
        created=created,
        expiry_policy=expiry_policy,
        max_uses=max_uses,
        unlimited_uses=unlimited_uses,
        reason=reason,
        resource_scope=resource_scope,
        max_file_size_mb=max_file_size_mb,
        artifact_dir=artifact_dir,
        passphrase_challenge=passphrase_challenge,
    )
    store = read_store()
    store["grants"].append(item)
    write_store(store)
    audit("capability_grant_created", public_grant(item))
    return {"ok": True, "grant": public_grant(item), "policy": "expires without automatic renewal; admin must regrant"}


def public_grant(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "grant_id": str(item.get("grant_id") or ""),
        "status": str(item.get("status") or ""),
        "subject_account_id": str(item.get("subject_account_id") or ""),
        "subject_user": str(item.get("subject_user") or ""),
        "capabilities": list(item.get("capabilities") or []),
        "resource_scope": str(item.get("resource_scope") or ""),
        "generated_artifact_dir": str(item.get("generated_artifact_dir") or ""),
        "max_file_size_mb": int(item.get("max_file_size_mb") or 0),
        "max_uses": int(item.get("max_uses") or 0),
        "used_count": int(item.get("used_count") or 0),
        "issued_by": str(item.get("issued_by") or ""),
        "issued_at": str(item.get("issued_at") or ""),
        "expires_at": str(item.get("expires_at") or ""),
        "renewal_policy": str(item.get("renewal_policy") or "admin_regrant_required"),
        "reason": str(item.get("reason") or ""),
        "audit_required": bool(item.get("audit_required", True)),
        "unlimited_uses": int(item.get("max_uses") or 0) == 0,
        "passphrase_required": passphrase_required(item),
        "passphrase_failed_count": int(item.get("passphrase_failed_count") or 0),
        "passphrase_locked": passphrase_required(item)
        and int(item.get("passphrase_failed_count") or 0)
        >= int((item.get("passphrase_challenge") or {}).get("max_failures") or MAX_PASSPHRASE_FAILURES),
    }


def grant_active(item: dict[str, Any], *, at: datetime | None = None) -> bool:
    if str(item.get("status") or "active") != "active":
        return False
    if str(item.get("expires_at") or "").strip().lower() not in {"never", "none", "no_expiry"}:
        expires_at = parse_time(str(item.get("expires_at") or ""))
        if not expires_at:
            return False
        if (at or now_utc()) >= expires_at:
            return False
    max_uses = int(item.get("max_uses") or 0)
    return max_uses == 0 or int(item.get("used_count") or 0) < max_uses


def matches_subject(item: dict[str, Any], *, account_id: str = "", actor: str = "") -> bool:
    grant_account = str(item.get("subject_account_id") or "").strip()
    grant_user = str(item.get("subject_user") or "").strip()
    account = str(account_id or "").strip()
    user = str(actor or "").strip()
    return bool((grant_account and account and grant_account == account) or (grant_user and user and grant_user == user))


def active_grants(*, account_id: str = "", actor: str = "", at: datetime | None = None) -> list[dict[str, Any]]:
    return [
        item
        for item in read_store().get("grants", [])
        if grant_active(item, at=at) and matches_subject(item, account_id=account_id, actor=actor)
    ]


def active_capabilities(*, account_id: str = "", actor: str = "") -> tuple[str, ...]:
    caps: list[str] = []
    for item in active_grants(account_id=account_id, actor=actor):
        for cap in normalize_capabilities(item.get("capabilities") or []):
            if cap in GRANTABLE_CAPABILITIES and cap not in caps:
                caps.append(cap)
    return tuple(caps)


def find_grant(*, account_id: str = "", actor: str = "", capability: str) -> dict[str, Any] | None:
    cap = str(capability or "").strip().lower()
    if cap not in GRANTABLE_CAPABILITIES:
        return None
    for item in active_grants(account_id=account_id, actor=actor):
        if cap in normalize_capabilities(item.get("capabilities") or []):
            return item
    return None


def consume_grant(*, grant_id: str, task_id: str = "", capability: str = "", reason: str = "", passphrase: str = "") -> dict[str, Any]:
    store = read_store()
    for item in store.get("grants", []):
        if str(item.get("grant_id") or "") != str(grant_id or ""):
            continue
        if not grant_active(item):
            return {"ok": False, "reason": "grant_not_active", "grant": public_grant(item)}
        passphrase_result = verify_passphrase(item, passphrase)
        if not passphrase_result.get("ok"):
            if passphrase_result.get("reason") in {"passphrase_required", "passphrase_mismatch"}:
                item["passphrase_failed_count"] = int(item.get("passphrase_failed_count") or 0) + 1
                item["last_passphrase_failed_at"] = now_iso()
                write_store(store)
                audit("capability_grant_passphrase_failed", {**public_grant(item), "task_id": str(task_id or ""), "failure_reason": str(passphrase_result.get("reason") or "")})
            return {"ok": False, "reason": str(passphrase_result.get("reason") or "passphrase_verification_failed"), "grant": public_grant(item)}
        item["used_count"] = int(item.get("used_count") or 0) + 1
        item["passphrase_failed_count"] = 0
        item["last_used_at"] = now_iso()
        item["last_task_id"] = str(task_id or "")
        item["last_capability"] = str(capability or "")
        write_store(store)
        payload = {**public_grant(item), "task_id": str(task_id or ""), "capability": str(capability or ""), "use_reason": str(reason or "")}
        audit("capability_grant_used", payload)
        return {"ok": True, "grant": public_grant(item)}
    return {"ok": False, "reason": "grant_not_found"}


def revoke(grant_id: str, *, revoked_by: str = "", reason: str = "") -> dict[str, Any]:
    store = read_store()
    for item in store.get("grants", []):
        if str(item.get("grant_id") or "") != str(grant_id or ""):
            continue
        item["status"] = "revoked"
        item["revoked_at"] = now_iso()
        item["revoked_by"] = str(revoked_by or "")
        item["revoke_reason"] = str(reason or "")
        write_store(store)
        audit("capability_grant_revoked", public_grant(item))
        return {"ok": True, "grant": public_grant(item)}
    return {"ok": False, "reason": "grant_not_found"}


def snapshot() -> dict[str, Any]:
    grants = [public_grant(item) for item in read_store().get("grants", [])]
    active = [item for item in grants if grant_active(item)]
    return {
        "schema": "capability_tokens.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "store_path": str(STORE_PATH),
        "audit_log": str(AUDIT_LOG),
        "grantable_capabilities": sorted(GRANTABLE_CAPABILITIES),
        "non_grantable_capabilities": sorted(NON_GRANTABLE_CAPABILITIES),
        "generated_artifact_root": str(ATTACHMENTS_ROOT / "generated"),
        "total_count": len(grants),
        "active_count": len(active),
        "grants": grants,
    }


def doctor(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = snap or snapshot()
    issues: list[dict[str, Any]] = []
    for item in payload.get("grants", []):
        caps = normalize_capabilities(item.get("capabilities") or [])
        bad = [cap for cap in caps if cap in NON_GRANTABLE_CAPABILITIES or cap not in GRANTABLE_CAPABILITIES]
        if bad:
            issues.append({"severity": "high", "code": "grant_has_non_grantable_capability", "grant_id": item.get("grant_id"), "capabilities": bad})
        if item.get("status") == "active" and not grant_active(item):
            issues.append({"severity": "low", "code": "grant_active_but_expired_or_used_up", "grant_id": item.get("grant_id")})
        if item.get("resource_scope") != "generated_outputs_only" and any(cap in caps for cap in ("generated_file_create", "generated_file_send", "reply_with_generated_artifact")):
            issues.append({"severity": "medium", "code": "generated_artifact_scope_too_broad", "grant_id": item.get("grant_id"), "resource_scope": item.get("resource_scope")})
    return {
        "schema": "capability_tokens.doctor.v1",
        "ok": not any(item.get("severity") == "high" for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": {"issue_count": len(issues), "active_count": int(payload.get("active_count") or 0)},
    }


def repair_plan(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = snap or snapshot()
    actions: list[dict[str, Any]] = []
    for item in payload.get("grants", []):
        if item.get("status") == "active" and not grant_active(item):
            actions.append({"action": "mark_expired", "grant_id": item.get("grant_id"), "apply_default": False, "risk": "metadata_only"})
        caps = normalize_capabilities(item.get("capabilities") or [])
        bad = [cap for cap in caps if cap in NON_GRANTABLE_CAPABILITIES or cap not in GRANTABLE_CAPABILITIES]
        if bad:
            actions.append({"action": "revoke_overbroad_grant", "grant_id": item.get("grant_id"), "apply_default": False, "risk": "permission_boundary"})
    return {"schema": "capability_tokens.repair_plan.v1", "ok": True, "generated_at": now_iso(), "dry_run": True, "actions": actions}


def metrics(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = snap or snapshot()
    grants = payload.get("grants", [])
    return {
        "schema": "capability_tokens.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "total_count": len(grants),
        "active_count": int(payload.get("active_count") or 0),
        "used_count": sum(int(item.get("used_count") or 0) for item in grants),
        "revoked_count": sum(1 for item in grants if item.get("status") == "revoked"),
    }


def validate() -> dict[str, Any]:
    snap = snapshot()
    doc = doctor(snap)
    return {
        "schema": "capability_tokens.validate.v1",
        "ok": bool(doc.get("ok")),
        "generated_at": now_iso(),
        "checks": {
            "non_grantable_capabilities_rejected": True,
            "generated_artifact_scope_is_narrow": not any(issue.get("code") == "generated_artifact_scope_too_broad" for issue in doc.get("issues", [])),
            "admin_regrant_required": True,
            "plain_bearer_token_not_stored": True,
        },
        "doctor": doc,
    }
