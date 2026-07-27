#!/usr/bin/env python3
"""Persistent request manifests for the resource layer."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "resource_store.manifest.v1"


def safe_component(value: str, fallback: str = "resource") -> str:
    text = str(value or fallback).strip() or fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    text = text.strip("._-")
    return text[:96] or fallback


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def receipt_index_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return compact, machine-first fields for the request index."""
    route = receipt.get("route") if isinstance(receipt.get("route"), dict) else {}
    network_summary = receipt.get("network_summary") if isinstance(receipt.get("network_summary"), dict) else {}
    owner_execution = receipt.get("owner_execution") if isinstance(receipt.get("owner_execution"), dict) else {}
    consumption = receipt.get("consumption") if isinstance(receipt.get("consumption"), dict) else {}
    return {
        "intent": route.get("intent", ""),
        "primary_tool": route.get("primary_tool", ""),
        "source_kind": route.get("source_kind", ""),
        "network_target_kind": network_summary.get("target_kind", ""),
        "network_route_mode": network_summary.get("route_mode", ""),
        "network_preferred_route": network_summary.get("preferred_route", ""),
        "owner_tool": owner_execution.get("owner_tool", ""),
        "owner_next_action": owner_execution.get("next_action", ""),
        "consumed": bool(consumption.get("satisfied")),
        "consumed_at": consumption.get("consumed_at", ""),
        "consumer": consumption.get("consumer", ""),
    }


def latest_preview_text(attempts: list[dict[str, Any]]) -> str:
    for attempt in reversed(attempts):
        result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        preview = metadata.get("preview_text")
        if isinstance(preview, str) and preview:
            return preview
    return ""


def latest_owner_result(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the latest successful owner result with consumable content."""

    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
        if not result.get("ok"):
            continue
        content = str(result.get("content") or "").strip()
        source_tool = str(attempt.get("tool") or result.get("source") or "")
        if not content or not source_tool:
            continue
        return {
            "source_tool": source_tool,
            "result_kind": str(result.get("result_kind") or "owner_result"),
            "content": content,
            "metadata": result.get("metadata") if isinstance(result.get("metadata"), dict) else {},
        }
    return {}


def persist_manifest(
    *,
    store_root: Path,
    request_id: str,
    request: dict[str, Any],
    receipt: dict[str, Any],
    events: list[dict[str, Any]],
    strategy_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    request_dir = store_root / "_requests" / safe_component(request_id)
    request_dir.mkdir(parents=True, exist_ok=True)
    preview_text = latest_preview_text(receipt.get("attempts", []))
    preview_path = request_dir / "preview.txt" if preview_text else None
    if preview_path:
        preview_path.write_text(preview_text, encoding="utf-8")
    owner_payload = latest_owner_result(receipt.get("attempts", []))
    owner_result: dict[str, Any] = {}
    owner_result_path = None
    if owner_payload:
        result_kind = str(owner_payload.get("result_kind") or "owner_result")
        suffix = ".md" if result_kind in {"markdown", "docs", "text"} else ".txt"
        owner_result_path = request_dir / f"owner-result{suffix}"
        owner_result_path.write_text(str(owner_payload.get("content") or ""), encoding="utf-8")
        owner_result = {
            "source_tool": str(owner_payload.get("source_tool") or ""),
            "result_kind": result_kind,
            "content_path": str(owner_result_path),
            "artifact_path": "",
            "metadata": owner_payload.get("metadata") if isinstance(owner_payload.get("metadata"), dict) else {},
        }

    manifest_path = request_dir / "manifest.json"
    updated_receipt = {
        **receipt,
        "manifest_path": str(manifest_path),
        "metadata_path": str(manifest_path),
        "preview_path": str(preview_path) if preview_path else "",
        "strategy_plan": strategy_plan,
    }
    saved_paths = {
        "manifest": str(manifest_path),
        "metadata": str(manifest_path),
        "preview": str(preview_path) if preview_path else "",
        "artifact": str(receipt.get("artifact_path") or ""),
    }
    if owner_result_path:
        saved_paths["owner_result"] = str(owner_result_path)
    updated_receipt["saved_paths"] = saved_paths
    if owner_result:
        updated_receipt["owner_result"] = owner_result
        if updated_receipt.get("ok") and not updated_receipt.get("artifact_path"):
            updated_receipt["result_kind"] = owner_result.get("result_kind") or updated_receipt.get("result_kind", "owner_result")
            updated_receipt["content_ref"] = owner_result.get("content_path") or updated_receipt.get("content_ref", "")
    manifest = {
        "schema": SCHEMA,
        "request_id": request_id,
        "request": request,
        "receipt": updated_receipt,
        "events": events,
        "strategy_plan": strategy_plan,
        "saved_paths": saved_paths,
    }
    write_json(manifest_path, manifest)
    append_jsonl(
        store_root / "_requests" / "index.jsonl",
        {
            "schema": "resource_store.index.v1",
            "request_id": request_id,
            "ok": bool(receipt.get("ok")),
            "status": receipt.get("status", ""),
            "result_kind": receipt.get("result_kind", ""),
            "manifest_path": str(manifest_path),
            "artifact_path": str(receipt.get("artifact_path") or ""),
            "preview_path": str(preview_path) if preview_path else "",
            "next_action": receipt.get("next_action", ""),
            "error_class": receipt.get("error_class", ""),
            **receipt_index_summary(updated_receipt),
        },
    )
    return updated_receipt


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "path": str(path)}
    return payload if isinstance(payload, dict) else {"ok": False, "reason": "manifest_not_object", "path": str(path)}


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def _consumable_paths(manifest: dict[str, Any], manifest_path: Path) -> dict[str, str]:
    receipt = manifest.get("receipt") if isinstance(manifest.get("receipt"), dict) else {}
    saved_paths = manifest.get("saved_paths") if isinstance(manifest.get("saved_paths"), dict) else {}
    owner_result = manifest.get("owner_result") if isinstance(manifest.get("owner_result"), dict) else {}
    receipt_owner_result = receipt.get("owner_result") if isinstance(receipt.get("owner_result"), dict) else {}
    candidates = {
        "manifest": str(manifest_path),
        "metadata": str(receipt.get("metadata_path") or saved_paths.get("metadata") or ""),
        "preview": str(receipt.get("preview_path") or saved_paths.get("preview") or ""),
        "artifact": str(receipt.get("artifact_path") or saved_paths.get("artifact") or ""),
        "content_ref": str(receipt.get("content_ref") or ""),
        "owner_result": str(
            receipt_owner_result.get("content_path")
            or owner_result.get("content_path")
            or saved_paths.get("owner_result")
            or ""
        ),
        "owner_artifact": str(
            receipt_owner_result.get("artifact_path")
            or owner_result.get("artifact_path")
            or ""
        ),
    }
    return {kind: value for kind, value in candidates.items() if value}


def mark_consumed(
    *,
    manifest_path: Path,
    consumed_path: str = "",
    no_read_needed_reason: str = "",
    consumer: str = "codex",
) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    manifest = read_manifest(manifest_path)
    if not manifest.get("request_id"):
        return {"ok": False, "reason": "manifest_missing_request_id", "path": str(manifest_path)}
    receipt = manifest.get("receipt") if isinstance(manifest.get("receipt"), dict) else {}
    if str(receipt.get("status") or "") != "completed":
        return {"ok": False, "reason": "resource_not_completed", "request_id": manifest["request_id"]}
    consumed_path = str(consumed_path or "").strip()
    no_read_needed_reason = str(no_read_needed_reason or "").strip()
    consumer = str(consumer or "").strip()
    if bool(consumed_path) == bool(no_read_needed_reason):
        return {"ok": False, "reason": "provide_exactly_one_consumption_evidence", "request_id": manifest["request_id"]}
    if not consumer:
        return {"ok": False, "reason": "consumer_required", "request_id": manifest["request_id"]}

    resolved_consumed_path = ""
    mode = "no_read_needed"
    if consumed_path:
        candidate_path = Path(consumed_path).expanduser().resolve()
        if not candidate_path.exists():
            return {"ok": False, "reason": "consumed_path_missing", "consumed_path": str(candidate_path)}
        allowed = {_normalized_path(value): value for value in _consumable_paths(manifest, manifest_path).values()}
        normalized_candidate = _normalized_path(candidate_path)
        if normalized_candidate not in allowed:
            return {
                "ok": False,
                "reason": "consumed_path_not_owned_by_request",
                "consumed_path": str(candidate_path),
                "allowed_paths": sorted(allowed.values()),
            }
        resolved_consumed_path = str(candidate_path)
        mode = "path_consumed"

    consumption = {
        "schema": "resource_store.consumption.v1",
        "satisfied": True,
        "consumed_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "consumer": consumer,
        "mode": mode,
        "consumed_path": resolved_consumed_path,
        "no_read_needed_reason": no_read_needed_reason,
    }
    receipt = dict(receipt)
    receipt["consumption"] = consumption
    manifest["consumption"] = consumption
    manifest["receipt"] = receipt
    write_json(manifest_path, manifest)
    append_jsonl(
        manifest_path.parents[1] / "index.jsonl",
        {
            "schema": "resource_store.index.v1",
            "request_id": manifest["request_id"],
            "ok": bool(receipt.get("ok")),
            "status": receipt.get("status", ""),
            "result_kind": receipt.get("result_kind", ""),
            "manifest_path": str(manifest_path),
            "artifact_path": receipt.get("artifact_path", ""),
            "preview_path": receipt.get("preview_path", ""),
            "next_action": receipt.get("next_action", ""),
            "error_class": receipt.get("error_class", ""),
            **receipt_index_summary(receipt),
        },
    )
    return receipt


def attach_owner_result(
    *,
    manifest_path: Path,
    source_tool: str,
    result_kind: str,
    content: str = "",
    artifact_path: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = read_manifest(manifest_path)
    if not manifest.get("request_id"):
        return {"ok": False, "reason": "manifest_missing_request_id", "path": str(manifest_path)}
    request_dir = manifest_path.parent
    saved_paths = dict(manifest.get("saved_paths") or {})
    content_path = ""
    if content:
        suffix = ".md" if result_kind in {"markdown", "docs", "text"} else ".txt"
        content_path_obj = request_dir / f"owner-result{suffix}"
        content_path_obj.write_text(content, encoding="utf-8")
        content_path = str(content_path_obj)
        saved_paths["owner_result"] = content_path
    if artifact_path:
        saved_paths["artifact"] = artifact_path
    owner_result = {
        "source_tool": source_tool,
        "result_kind": result_kind,
        "content_path": content_path,
        "artifact_path": artifact_path,
        "metadata": metadata or {},
    }
    manifest["owner_result"] = owner_result
    receipt = dict(manifest.get("receipt") or {})
    receipt.update(
        {
            "ok": True,
            "status": "completed",
            "result_kind": result_kind or receipt.get("result_kind", "owner_result"),
            "content_ref": content_path or artifact_path or receipt.get("content_ref", ""),
            "artifact_path": artifact_path or receipt.get("artifact_path", ""),
            "error_class": "",
            "next_action": "consume_resource",
            "confidence": max(float(receipt.get("confidence") or 0.0), 0.9),
            "saved_paths": saved_paths,
            "owner_result": owner_result,
            "codex_guidance": {},
        }
    )
    manifest["receipt"] = receipt
    manifest["saved_paths"] = saved_paths
    write_json(manifest_path, manifest)
    append_jsonl(
        manifest_path.parents[1] / "index.jsonl",
        {
            "schema": "resource_store.index.v1",
            "request_id": manifest["request_id"],
            "ok": True,
            "status": "completed",
            "result_kind": receipt["result_kind"],
            "manifest_path": str(manifest_path),
            "artifact_path": receipt.get("artifact_path", ""),
            "preview_path": saved_paths.get("preview", ""),
            "owner_result_path": content_path,
            "next_action": "consume_resource",
            "error_class": "",
            **receipt_index_summary(receipt),
        },
    )
    return receipt
