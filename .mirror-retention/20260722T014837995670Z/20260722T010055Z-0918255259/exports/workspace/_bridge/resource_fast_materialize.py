#!/usr/bin/env python3
"""Lightweight explicit-URL materialization for the resource layer.

Ownership: fast resource-layer path for already-resolved user-approved URLs.
Non-goals: source discovery, owner MCP calls, package installs, or global
network/proxy mutation.
State behavior: writes the downloaded artifact, resource log, receipt log, and
resource-store manifest through existing resource-layer primitives.
Caller context: resource_cli materialize-url and Codex resource delegation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from resource_fetcher import (
    ResourceIntent,
    ResourceRequest,
    ResourceStage,
    acquire_resource_with_policy,
    append_resource_log,
)
from resource_router import route_resource
from resource_store import append_jsonl, persist_manifest


RESOURCE_LAYER_OWNER = "resource_layer"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_fast_request_id(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "res_fast_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _compact_attempt(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": 1,
        "tool": "resource_cli",
        "stage": ResourceStage.MATERIALIZE,
        "status": "completed" if result.get("ok") else (result.get("decision") or "failed"),
        "error_class": result.get("policy_reason") or result.get("error") or "",
        "reason": result.get("policy_reason") or result.get("error") or "",
        "next_action": result.get("next_action") or ("consume_resource" if result.get("ok") else "surface_resource_failure"),
        "result_kind": "artifact" if result.get("stored_path") else "",
        "source": result.get("source", ""),
    }


def _ownership(status: str) -> dict[str, Any]:
    completed = status == "completed"
    terminal = status in {"completed", "failed"}
    return {
        "acquisition_owner": RESOURCE_LAYER_OWNER,
        "owned_need_scope": "same_resolved_url_download_need",
        "owned_until_terminal_receipt": not terminal,
        "terminal_status": terminal,
        "resource_layer_terminal": terminal,
        "end_to_end_terminal": terminal,
        "resource_need_satisfied": completed,
        "same_need_fetch_allowed": terminal and not completed,
        "same_need_fetch_policy": "resource_satisfied" if completed else "resource_layer_released_after_terminal_failure",
        "duplicate_fetch_policy": {
            "same_need": "do_not_start_direct_fetch_while_resource_layer_owns_request",
            "allowed_actions": ["consume_resource", "surface_resource_layer_blocker"],
            "release_on": ["completed", "failed"],
        },
    }


def materialize_url_fast(
    *,
    url: str,
    task: str = "",
    name: str = "",
    target_dir: Path,
    store_root: Path,
    receipt_log: Path,
    resource_log: Path | None,
    expected_sha256: str = "",
    max_bytes: int | None = None,
    timeout_seconds: int = 30,
    retries: int = 1,
    retry_delay_seconds: float = 1.0,
    download_backend: str = "",
    resume_download: bool = False,
    validation_profile: str = "",
) -> dict[str, Any]:
    """Download one explicit URL and return a compact resource-layer receipt."""

    request_payload = {
        "url": url,
        "task": task,
        "name": name,
        "intent": ResourceIntent.EXPLICIT_USER_URL,
        "need_materialization": True,
        "allow_network": True,
        "allow_filesystem_write": True,
        "target_dir": str(target_dir),
        "max_bytes": max_bytes,
        "expected_sha256": expected_sha256,
        "timeout_seconds": timeout_seconds,
        "retry_budget": retries,
        "metadata": {
            "schema": "resource_fast_materialize.request.v1",
            "cli_command": "materialize-url",
            "task": task,
            "validation_profile": validation_profile,
            **({"download_backend": download_backend} if download_backend else {}),
            **({"resume_download": True} if resume_download else {}),
        },
    }
    request_id = stable_fast_request_id(request_payload)
    started = now_iso()
    target_dir.mkdir(parents=True, exist_ok=True)
    route = route_resource(
        url=url,
        intent=ResourceIntent.EXPLICIT_USER_URL,
        need_materialization=True,
        task=task,
        name=name,
    )
    result = acquire_resource_with_policy(
        ResourceRequest(
            source="resource_fast_materialize",
            target_dir=target_dir,
            name=name or Path(url.split("?", 1)[0]).name or "download",
            url=url,
            expected_sha256=expected_sha256,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            retries=max(0, int(retries)),
            retry_delay_seconds=retry_delay_seconds,
            metadata=request_payload["metadata"],
        ),
        intent=ResourceIntent.EXPLICIT_USER_URL,
        stage=ResourceStage.MATERIALIZE,
    )
    if resource_log is not None:
        append_resource_log(resource_log, result)

    result_payload = result.to_dict()
    status = "completed" if result.ok else "failed"
    event = {
        "schema": "resource_fast_materialize.event.v1",
        "request_id": request_id,
        "time": started,
        "stage": "materialize",
        "status": status,
        "message": "explicit URL fast materialization completed" if result.ok else "explicit URL fast materialization failed",
    }
    receipt = {
        "ok": bool(result.ok),
        "request_id": request_id,
        "status": status,
        "result_kind": "artifact" if result.ok else "none",
        "route": route.to_dict(),
        "attempts": [
            {
                "index": 1,
                "tool": "resource_cli",
                "stage": ResourceStage.MATERIALIZE,
                "status": status,
                "executable": True,
                "started_at": started,
                "finished_at": now_iso(),
                "result": result_payload,
                "error_class": "" if result.ok else (result.policy_reason or result.error or "resource_failed"),
                "reason": result.policy_reason or result.error or "",
                "next_action": result.next_action or ("consume_resource" if result.ok else "surface_resource_failure"),
            }
        ],
        "progress_events": [event],
        "artifact_path": result.stored_path or result.local_path,
        "content_ref": result.stored_path or result.local_path or request_id,
        "sha256": result.sha256,
        "cache_hit": bool(result.cache_hit),
        "error_class": "" if result.ok else (result.policy_reason or result.error or "resource_failed"),
        "next_action": result.next_action or ("consume_resource" if result.ok else "surface_resource_failure"),
        "confidence": 0.95 if result.ok else 0.25,
        "strategy_plan": [
            {
                "index": 1,
                "tool": "resource_cli",
                "stage": ResourceStage.MATERIALIZE,
                "executable_by_broker": True,
                "expected_status": "attempt",
                "reason": "fast_path_for_already_resolved_explicit_url",
            }
        ],
        "strategy_summary": {
            "mode": "fast_path",
            "reason": "already_resolved_explicit_url",
            "avoided": ["owner_discovery", "multi_tool_broker_planning"],
        },
        "network_gateway_plan": {},
        "network_summary": {},
        "owner_execution": {},
    }
    persisted_receipt = persist_manifest(
        store_root=store_root,
        request_id=request_id,
        request=request_payload,
        receipt=receipt,
        events=[event],
        strategy_plan=receipt["strategy_plan"],
    )
    append_jsonl(receipt_log, persisted_receipt)
    return {
        "schema": "resource_fast_materialize.run.v1",
        "ok": bool(persisted_receipt.get("ok")),
        "mode": "lightweight",
        "request_id": request_id,
        "status": persisted_receipt.get("status", ""),
        "acquisition_owner": RESOURCE_LAYER_OWNER,
        "ownership": _ownership(str(persisted_receipt.get("status") or "")),
        "resource_layer_terminal": str(persisted_receipt.get("status") or "") in {"completed", "failed"},
        "end_to_end_terminal": str(persisted_receipt.get("status") or "") in {"completed", "failed"},
        "resource_need_satisfied": persisted_receipt.get("status") == "completed",
        "same_need_fetch_allowed": persisted_receipt.get("status") != "completed",
        "codex_next_action": "consume_resource" if persisted_receipt.get("status") == "completed" else "surface_resource_layer_blocker",
        "progress": {"percent": 100 if persisted_receipt.get("status") in {"completed", "failed"} else 0},
        "status_summary": {"state": persisted_receipt.get("status", "")},
        "receipt_detail": "compact",
        "receipt": {
            "request_id": request_id,
            "ok": bool(persisted_receipt.get("ok")),
            "status": persisted_receipt.get("status", ""),
            "result_kind": persisted_receipt.get("result_kind", ""),
            "error_class": persisted_receipt.get("error_class", ""),
            "next_action": persisted_receipt.get("next_action", ""),
            "content_ref": persisted_receipt.get("content_ref", ""),
            "artifact_path": persisted_receipt.get("artifact_path", ""),
            "manifest_path": persisted_receipt.get("manifest_path", ""),
            "metadata_path": persisted_receipt.get("metadata_path", ""),
            "route": {
                "primary_tool": route.primary_tool,
                "intent": route.intent,
                "recommended_stage": route.recommended_stage,
                "risk_flags": list(route.risk_flags),
            },
            "attempts": [_compact_attempt(result_payload)],
        },
    }


def validate() -> dict[str, Any]:
    root = Path(__file__).resolve().parent / "tmp" / "resource-fast-materialize-validate"
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "resource_fast_materialize.validate.v1",
        "ok": True,
        "checks": [
            {
                "name": "stable_id_prefix",
                "ok": stable_fast_request_id({"url": "https://example.com/a"}).startswith("res_fast_"),
            }
        ],
    }
    payload["ok"] = all(bool(item["ok"]) for item in payload["checks"])
    return payload


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
