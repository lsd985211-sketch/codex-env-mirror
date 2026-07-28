#!/usr/bin/env python3
"""Pure contract for one durable, authorized direct-file transfer.

Ownership: stable transfer identity, input validation, resume validators, and
the allowed state transition vocabulary.  Non-goals: source discovery, broker
routing, scheduler time ownership, permission decisions, and process control.
State behavior: pure; the transfer owner persists this contract in the existing
resource event store.  Caller context: resource_transfer_owner and its worker.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCHEMA = "resource_transfer_contract.v1"
PLANNED_STATES = {
    "planned",
    "queued",
    "leased",
    "running",
    "paused",
    "blocked",
    "cancelling",
}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
ALL_STATES = PLANNED_STATES | TERMINAL_STATES
TRANSITIONS = {
    "planned": {"queued", "blocked", "cancelled"},
    "queued": {"leased", "paused", "blocked", "cancelled"},
    "leased": {"running", "queued", "blocked", "cancelled"},
    "running": {"completed", "failed", "paused", "cancelling", "blocked"},
    "paused": {"queued", "cancelled", "blocked"},
    "cancelling": {"cancelled", "failed"},
    "blocked": {"queued", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed.geturl()


def execution_signature(
    *,
    request_id: str,
    operation_id: str,
    source_url: str,
    target_path: str,
    backend: str,
    authorization_generation: int = 0,
    route_signature: str = "",
) -> str:
    return _digest(
        {
            "request_id": request_id,
            "operation_id": operation_id,
            "source_url": _safe_url(source_url),
            "target_path": str(Path(target_path).expanduser()),
            "backend": backend,
            "authorization_generation": int(authorization_generation),
            "route_signature": route_signature,
        }
    )


def transfer_id(
    *, request_id: str, operation_id: str, execution_signature_value: str
) -> str:
    return (
        "tr_"
        + _digest(
            {
                "request_id": request_id,
                "operation_id": operation_id,
                "execution_signature": execution_signature_value,
            }
        )[:32]
    )


def build_plan(
    *,
    request_id: str,
    operation_id: str,
    source_url: str,
    target_path: str,
    authorization: dict[str, Any],
    backend: str = "python_http",
    expected_sha256: str = "",
    expected_length: int = 0,
    route_signature: str = "",
    allowlist: bool = False,
) -> dict[str, Any]:
    url = _safe_url(source_url)
    target = Path(target_path).expanduser()
    if not request_id or not operation_id:
        return {
            "schema": SCHEMA,
            "ok": False,
            "reason": "request_id_and_operation_id_required",
        }
    if not url:
        return {"schema": SCHEMA, "ok": False, "reason": "direct_http_url_required"}
    if not target.is_absolute() or ".." in target.parts:
        return {
            "schema": SCHEMA,
            "ok": False,
            "reason": "absolute_safe_target_path_required",
        }
    loopback = urlparse(url).hostname in {"127.0.0.1", "localhost", "::1"}
    fixture_authorized = bool(authorization.get("fixture_only")) and loopback
    if not bool(authorization.get("active")):
        return {
            "schema": SCHEMA,
            "ok": False,
            "reason": "active_scoped_authorization_required",
        }
    if not str(authorization.get("permit_ref") or "") and not fixture_authorized:
        return {
            "schema": SCHEMA,
            "ok": False,
            "reason": "scoped_authorization_permit_required",
        }
    generation = int(authorization.get("generation") or 0)
    signature = execution_signature(
        request_id=request_id,
        operation_id=operation_id,
        source_url=url,
        target_path=str(target),
        backend=backend,
        authorization_generation=generation,
        route_signature=route_signature,
    )
    item = {
        "schema": SCHEMA,
        "ok": True,
        "request_id": request_id,
        "operation_id": operation_id,
        "source_url": url,
        "target_path": str(target),
        "partial_path": str(target.with_suffix(target.suffix + ".part")),
        "partial_metadata_path": str(
            target.with_suffix(target.suffix + ".part.meta.json")
        ),
        "backend": backend,
        "expected_sha256": expected_sha256.lower(),
        "expected_length": max(0, int(expected_length or 0)),
        "route_signature": route_signature,
        "authorization": {
            "active": True,
            "generation": generation,
            "permit_ref": str(authorization.get("permit_ref") or ""),
            "fixture_only": fixture_authorized,
        },
        "allowlist": bool(allowlist),
        "execution_signature": signature,
    }
    item["transfer_id"] = transfer_id(
        request_id=request_id,
        operation_id=operation_id,
        execution_signature_value=signature,
    )
    return item


def resume_decision(
    *,
    partial_size: int,
    stored_etag: str,
    stored_last_modified: str,
    response_status: int,
    response_etag: str,
    response_last_modified: str,
    content_range: str,
    expected_total: int = 0,
) -> dict[str, Any]:
    if partial_size <= 0:
        return {"ok": True, "mode": "fresh", "reason": "no_partial"}
    same_validator = bool(
        stored_etag and response_etag and stored_etag == response_etag
    )
    weak_match = not stored_etag and bool(
        stored_last_modified
        and response_last_modified
        and stored_last_modified == response_last_modified
    )
    expected_prefix = f"bytes {partial_size}-"
    range_ok = response_status == 206 and content_range.startswith(expected_prefix)
    if range_ok and (same_validator or weak_match):
        return {
            "ok": True,
            "mode": "append",
            "validator": "etag" if same_validator else "last_modified",
            "reason": "validated_range",
        }
    return {
        "ok": False,
        "mode": "restart_required",
        "reason": "range_or_validator_mismatch",
    }


def disk_preflight(
    *,
    target_path: str,
    expected_length: int,
    partial_size: int = 0,
    reserve_bytes: int = 8 * 1024 * 1024,
) -> dict[str, Any]:
    target = Path(target_path)
    try:
        free = os.statvfs(target.parent).f_bavail * os.statvfs(target.parent).f_frsize
    except OSError:
        return {"ok": False, "reason": "target_parent_unavailable"}
    needed = max(
        0,
        int(expected_length or 0)
        - max(0, int(partial_size or 0))
        + max(0, int(reserve_bytes)),
    )
    return {
        "ok": free >= needed,
        "free_bytes": free,
        "required_bytes": needed,
        "reason": "" if free >= needed else "insufficient_disk_space",
    }


def validate_transition(current: str, target: str) -> bool:
    return current in ALL_STATES and target in TRANSITIONS.get(current, set())
