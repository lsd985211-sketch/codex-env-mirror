#!/usr/bin/env python3
"""Signature-bound durable receipt consumer for workflow validation.

Ownership: one replaceable derived receipt for a fully passing validation run.
The consumer never treats a partial or failed result as a pass and never
changes the authoritative validator payload.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from platform_paths import scheduler_state_root


SCHEMA = "workflow_validation_receipt_reuse.v1"
ACCEPTANCE_PREDICATE = "workflow_orchestrator.validate.accepted.v1"
DEFAULT_TTL_SECONDS = 900
STATE_ROOT = scheduler_state_root() / "maintenance-convergence" / "validation-receipts"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def context_signature(
    *, workflow_source_signature: str, skill_context_signature: str,
    registry_source_signature: str,
) -> str:
    """Build the complete invalidation key for one validation authority set."""

    return _digest({
        "schema": SCHEMA,
        "acceptance_predicate": ACCEPTANCE_PREDICATE,
        "workflow_source_signature": str(workflow_source_signature or ""),
        "skill_context_signature": str(skill_context_signature or ""),
        "registry_source_signature": str(registry_source_signature or ""),
    }) if all((workflow_source_signature, skill_context_signature, registry_source_signature)) else ""


def _current_path(state_root: Path) -> Path:
    return state_root / "current.json"


def load_receipt(
    signature: str, *, state_root: Path = STATE_ROOT, now: datetime | None = None,
) -> dict[str, Any]:
    if not signature:
        return {"ok": False, "reason": "validation_receipt_signature_missing"}
    path = _current_path(state_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"ok": False, "reason": "validation_receipt_missing"}
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "reason": "validation_receipt_unreadable", "receipt_ref": str(path)}
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return {"ok": False, "reason": "validation_receipt_schema_invalid", "receipt_ref": str(path)}
    if payload.get("status") != "executed_passed" or payload.get("readback_ok") is not True:
        return {"ok": False, "reason": "validation_receipt_not_executed_passed", "receipt_ref": str(path)}
    if str(payload.get("context_signature") or "") != signature:
        return {"ok": False, "reason": "validation_receipt_signature_mismatch", "receipt_ref": str(path)}
    expiry = _parse_time(str(payload.get("expires_at") or ""))
    if expiry is None or expiry <= (now or _now()):
        return {"ok": False, "reason": "validation_receipt_expired", "receipt_ref": str(path)}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if not result or result.get("ok") is not True:
        return {"ok": False, "reason": "validation_receipt_payload_invalid", "receipt_ref": str(path)}
    return {
        "ok": True,
        "reused": True,
        "status": "receipt_reused_passed",
        "receipt_ref": str(path),
        "context_signature": signature,
        "payload": deepcopy(result),
    }


def store_receipt(
    result: Mapping[str, Any], signature: str, *, state_root: Path = STATE_ROOT,
    ttl_seconds: int = DEFAULT_TTL_SECONDS, now: datetime | None = None,
) -> dict[str, Any]:
    checks = result.get("checks") if isinstance(result.get("checks"), list) else []
    if not signature:
        return {"ok": False, "stored": False, "reason": "validation_receipt_signature_missing"}
    if result.get("ok") is not True or not checks or any(
        not isinstance(item, dict) or item.get("ok") is not True for item in checks
    ):
        return {"ok": True, "stored": False, "reason": "validation_result_not_fully_passing"}
    created = now or _now()
    bounded_ttl = max(60, min(int(ttl_seconds), 86400))
    artifact = state_root / f"receipt-{signature}.json"
    payload = {
        "schema": SCHEMA,
        "status": "executed_passed",
        "readback_ok": True,
        "context_signature": signature,
        "acceptance_predicate": ACCEPTANCE_PREDICATE,
        "created_at": _iso(created),
        "expires_at": _iso(created + timedelta(seconds=bounded_ttl)),
        "artifact_ref": str(artifact),
        "result": deepcopy(dict(result)),
    }
    _atomic_write(artifact, payload)
    current = _current_path(state_root)
    _atomic_write(current, payload)
    return {"ok": True, "stored": True, "receipt_ref": str(current), "artifact_ref": str(artifact)}


def attach_reuse_projection(
    result: Mapping[str, Any], *, reused: bool, receipt_ref: str = "",
) -> dict[str, Any]:
    execution_status = "executed_passed" if result.get("ok") is True else "executed_failed"
    return {
        **deepcopy(dict(result)),
        "validation_receipt_reuse": bool(reused),
        "validation_receipt_reuse_status": "receipt_reused_passed" if reused else execution_status,
        "validation_receipt_ref": str(receipt_ref or ""),
    }
