#!/usr/bin/env python3
"""One-shot data-plane worker for a transfer plan owned by resource_transfer_owner.

Ownership: HTTP Range bytes, validator checks, disk preflight, hash validation,
and atomic final placement. Non-goals: scheduling, retries, source discovery,
or authorization policy. State behavior: returns one JSON receipt on stdout.
Caller context: launched once through shared.long_command_receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from resource_transfer_contract import disk_preflight, resume_decision


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _headers(response: Any) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in response.headers.items()}


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_metadata(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, path)


def execute(plan: dict[str, Any]) -> dict[str, Any]:
    if not plan.get("ok") or not plan.get("allowlist"):
        return {
            "schema": "resource_transfer_worker.result.v1",
            "ok": False,
            "reason": "controlled_allowlist_required",
        }
    target = Path(str(plan["target_path"]))
    partial = Path(str(plan["partial_path"]))
    metadata_path = Path(str(plan["partial_metadata_path"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    runtime = plan.get("runtime") if isinstance(plan.get("runtime"), dict) else {}

    def fence() -> dict[str, Any]:
        if not runtime:
            return {"ok": True}
        from shared.resource_event_store import transfer_fence

        transfer = transfer_fence(
            str(plan.get("transfer_id") or ""),
            generation=int(runtime.get("generation") or 0),
            db_path=Path(str(runtime.get("db_path") or "")),
        )
        if not transfer.get("ok"):
            return transfer
        authorization = (
            runtime.get("authorization")
            if isinstance(runtime.get("authorization"), dict)
            else {}
        )
        if authorization.get("permit_ref"):
            import scoped_authorization

            active = scoped_authorization.introspect(
                str(authorization["permit_ref"]),
                state_root=authorization.get("state_root") or None,
            )
            same_generation = int(active.get("current_generation") or 0) == int(
                authorization.get("generation") or 0
            )
            intent_active = str(active.get("intent_status") or "") == "active"
            permit_valid = active.get("active") or (
                active.get("permit_status") == "consumed"
                and set(active.get("reasons", [])) <= {"permit_consumed"}
            )
            if not same_generation or not intent_active or not permit_valid:
                return {
                    "ok": False,
                    "reason": "authorization_generation_fenced",
                    "authorization": active,
                }
        return transfer

    first_fence = fence()
    if not first_fence.get("ok"):
        return {
            "schema": "resource_transfer_worker.result.v1",
            "ok": False,
            "reason": first_fence.get("reason") or "transfer_generation_fenced",
        }
    partial_size = partial.stat().st_size if partial.exists() else 0
    stored_metadata = _read_metadata(metadata_path) if partial_size else {}
    if partial_size and not stored_metadata:
        partial.unlink(missing_ok=True)
        return {
            "schema": "resource_transfer_worker.result.v1",
            "ok": False,
            "reason": "partial_validator_missing",
            "restart_required": True,
        }
    first = urllib.request.Request(str(plan["source_url"]), method="HEAD")
    try:
        with urllib.request.urlopen(first, timeout=15) as head:
            headers = _headers(head)
            expected_length = int(
                headers.get("content-length") or plan.get("expected_length") or 0
            )
            etag = headers.get("etag", "")
            last_modified = headers.get("last-modified", "")
    except Exception:
        headers = {}
        expected_length = int(plan.get("expected_length") or 0)
        etag = ""
        last_modified = ""
    disk = disk_preflight(
        target_path=str(target),
        expected_length=expected_length,
        partial_size=partial_size,
    )
    if not disk.get("ok"):
        return {
            "schema": "resource_transfer_worker.result.v1",
            "ok": False,
            "reason": disk["reason"],
            "disk": disk,
        }
    request_headers: dict[str, str] = {}
    if partial_size:
        request_headers["Range"] = f"bytes={partial_size}-"
        if etag:
            request_headers["If-Range"] = etag
        elif last_modified:
            request_headers["If-Range"] = last_modified
    try:
        with urllib.request.urlopen(
            urllib.request.Request(str(plan["source_url"]), headers=request_headers),
            timeout=30,
        ) as response:
            response_headers = _headers(response)
            decision = resume_decision(
                partial_size=partial_size,
                stored_etag=str(stored_metadata.get("etag") or ""),
                stored_last_modified=str(stored_metadata.get("last_modified") or ""),
                response_status=int(getattr(response, "status", 200)),
                response_etag=response_headers.get("etag", etag),
                response_last_modified=response_headers.get(
                    "last-modified", last_modified
                ),
                content_range=response_headers.get("content-range", ""),
                expected_total=expected_length,
            )
            append = partial_size > 0 and decision.get("mode") == "append"
            if partial_size and not append:
                partial.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
                partial_size = 0
                return {
                    "schema": "resource_transfer_worker.result.v1",
                    "ok": False,
                    "reason": "resume_restart_required",
                    "restart_required": True,
                    "resume": decision,
                }
            if not append:
                _write_metadata(
                    metadata_path,
                    {
                        "etag": response_headers.get("etag", etag),
                        "last_modified": response_headers.get(
                            "last-modified", last_modified
                        ),
                        "expected_length": expected_length,
                    },
                )
            with partial.open("ab" if append else "wb") as output:
                while True:
                    active = fence()
                    if not active.get("ok"):
                        return {
                            "schema": "resource_transfer_worker.result.v1",
                            "ok": False,
                            "reason": active.get("reason")
                            or "transfer_generation_fenced",
                            "partial_bytes": output.tell(),
                        }
                    chunk = response.read(1024 * 128)
                    if not chunk:
                        break
                    output.write(chunk)
    except urllib.error.HTTPError as exc:
        return {
            "schema": "resource_transfer_worker.result.v1",
            "ok": False,
            "reason": f"http_{exc.code}",
        }
    except Exception as exc:
        return {
            "schema": "resource_transfer_worker.result.v1",
            "ok": False,
            "reason": f"transfer_error:{type(exc).__name__}",
        }
    size = partial.stat().st_size if partial.exists() else 0
    expected_hash = str(plan.get("expected_sha256") or "")
    actual_hash = _hash(partial) if partial.exists() else ""
    if expected_length and size != expected_length:
        return {
            "schema": "resource_transfer_worker.result.v1",
            "ok": False,
            "reason": "length_mismatch",
            "bytes": size,
            "expected_length": expected_length,
        }
    if expected_hash and actual_hash != expected_hash:
        return {
            "schema": "resource_transfer_worker.result.v1",
            "ok": False,
            "reason": "sha256_mismatch",
            "sha256": actual_hash,
        }
    os.replace(partial, target)
    metadata_path.unlink(missing_ok=True)
    return {
        "schema": "resource_transfer_worker.result.v1",
        "ok": True,
        "artifact_path": str(target),
        "bytes": size,
        "sha256": actual_hash,
        "etag": etag,
        "last_modified": last_modified,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-json", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            execute(json.loads(args.plan_json)), ensure_ascii=False, sort_keys=True
        )
    )
