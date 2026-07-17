#!/usr/bin/env python3
"""Batch scheduler for resource-layer requests.

Ownership: plan and execute a bounded batch of resource broker requests with
per-class and per-host concurrency limits, then write a batch manifest.
Non-goals: background daemon queues, persistent workers, package installation,
git clone orchestration, remote writes, or global proxy/DNS mutation.
State behavior: writes only batch manifests, broker receipts, and broker logs
under caller-provided resource store/log paths.
Caller context: `resource_cli.py request-batch` and maintenance tests.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import subprocess
import sys
import threading
import tempfile
import urllib.parse
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import codex_network_gateway
from resource_execution_budget import ResourceExecutionBudget
from resource_broker import (
    DEFAULT_EVENT_LOG,
    DEFAULT_RECEIPT_LOG,
    DEFAULT_STORE_ROOT,
    ResourceBrokerRequest,
    handle_request,
    network_gateway_request_for_request,
    request_from_payload,
    route_for_request,
)
from resource_fetcher import ResourceIntent


DEFAULT_CLASS_LIMITS = {
    "local_light": 4,
    "url_probe": 6,
    "url_preview": 3,
    "download": 2,
    "github_metadata": 2,
    "package_metadata": 2,
    "owner_mcp": 2,
    "source_selection": 2,
    "install_or_clone": 0,
    "unknown": 1,
}

PACKAGE_REGISTRY_HOST_KEYS = {
    "package_manager": "package:pypi",
}

OWNER_TOOL_HOST_KEYS = {
    "context7": "docs:context7",
    "microsoftdocs": "docs:microsoft",
    "markitdown": "document:markitdown",
    "playwright": "browser:playwright",
    "chrome-devtools": "browser:chrome-devtools",
}


@dataclass(frozen=True)
class ScheduledResource:
    index: int
    request: ResourceBrokerRequest
    route: dict[str, Any]
    queue_class: str
    host_key: str
    priority: int = 100


@dataclass(frozen=True)
class ResourceBatchConfig:
    max_active: int = 6
    per_host_limit: int = 2
    class_limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_CLASS_LIMITS))
    plan_only: bool = False
    fail_fast: bool = False
    total_budget_seconds: float = 0.0


BATCH_ITEM_METADATA_KEY = "batch_item_contract"


def console_python_executable(executable: str | Path | None = None) -> str:
    """Return a Python CLI executable even when the current owner uses pythonw."""

    current = Path(executable or sys.executable)
    if current.name.casefold() == "pythonw.exe":
        sibling = current.with_name("python.exe")
        if sibling.exists():
            return str(sibling)
    return str(current)


def batch_item_contract(request: ResourceBrokerRequest, index: int) -> dict[str, Any]:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    raw = metadata.get(BATCH_ITEM_METADATA_KEY) if isinstance(metadata.get(BATCH_ITEM_METADATA_KEY), dict) else {}
    acceptance = raw.get("acceptance") if isinstance(raw.get("acceptance"), dict) else {}
    return {
        "item_id": str(raw.get("item_id") or f"item-{index:03d}"),
        "required": bool(raw.get("required", True)),
        "acceptance": acceptance,
        "quantity": raw.get("quantity") if isinstance(raw.get("quantity"), dict) else {},
        "source": raw.get("source") if isinstance(raw.get("source"), dict) else {},
        "freshness": raw.get("freshness") if isinstance(raw.get("freshness"), dict) else {},
        "diversity": raw.get("diversity") if isinstance(raw.get("diversity"), dict) else {},
    }


def _attempt_result_candidate_count(attempts: list[dict[str, Any]]) -> int:
    count = 0
    for attempt in attempts:
        result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
        items = metadata.get("items") if isinstance(metadata.get("items"), list) else []
        explicit = max(
            int(result.get("candidate_count") or 0),
            int(metadata.get("candidate_count") or 0),
            len(candidates),
            len(items),
        )
        if explicit == 0 and any(metadata.get(key) for key in ("full_name", "html_url", "top_url", "url", "library_id")):
            explicit = 1
        count = max(count, explicit)
    return count


def _attempt_result_provenance_count(attempts: list[dict[str, Any]]) -> int:
    identities: set[str] = set()
    for attempt in attempts:
        result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        for value in (result.get("source"), result.get("source_url"), result.get("url"), metadata.get("html_url"), metadata.get("top_url"), metadata.get("url"), metadata.get("library_id")):
            text = str(value or "").strip()
            if text:
                identities.add(text)
        for key in ("candidates", "citations"):
            values = result.get(key) if isinstance(result.get(key), list) else []
            for value in values:
                if isinstance(value, dict):
                    text = str(value.get("source_id") or value.get("url") or value.get("source") or "").strip()
                else:
                    text = str(value or "").strip()
                if text:
                    identities.add(text)
        for value in metadata.get("items") if isinstance(metadata.get("items"), list) else []:
            if isinstance(value, dict):
                text = str(value.get("html_url") or value.get("url") or value.get("full_name") or "").strip()
                if text:
                    identities.add(text)
    return len(identities)


def evaluate_item_acceptance(request: ResourceBrokerRequest, receipt: Any, index: int) -> dict[str, Any]:
    contract = batch_item_contract(request, index)
    acceptance = contract["acceptance"]
    candidate_count = _attempt_result_candidate_count(list(receipt.attempts or []))
    provenance_count = _attempt_result_provenance_count(list(receipt.attempts or []))
    result_kind = str(receipt.result_kind or "")
    discovery_result = any(term in result_kind for term in ("search", "discovery", "source_selection", "candidates"))
    minimum_candidates = int(
        acceptance.get("minimum_candidates")
        or acceptance.get("minimum_quantity")
        or (1 if discovery_result else 0)
    )
    provenance_required = bool(acceptance.get("provenance_required"))
    consumable_required = bool(acceptance.get("consumable_required", True))
    receipt_satisfied = bool((receipt.satisfaction or {}).get("satisfied"))
    reasons: list[str] = []
    if not receipt.ok or receipt.status != "completed":
        reasons.append(str(receipt.error_class or receipt.status or "request_not_completed"))
    if consumable_required and not receipt_satisfied:
        reasons.append(str((receipt.satisfaction or {}).get("reason") or "resource_not_consumable"))
    if candidate_count < minimum_candidates:
        reasons.append("minimum_candidates_not_met")
    if provenance_required and provenance_count < 1:
        reasons.append("provenance_required")
    accepted = not reasons
    return {
        "schema": "resource_scheduler.item_acceptance.v1",
        "item_id": contract["item_id"],
        "required": contract["required"],
        "accepted": accepted,
        "reason": "accepted" if accepted else reasons[0],
        "reasons": reasons,
        "candidate_count": candidate_count,
        "minimum_candidates": minimum_candidates,
        "provenance_count": provenance_count,
        "provenance_required": provenance_required,
        "consumable_required": consumable_required,
    }


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def stable_batch_id(requests: list[ResourceBrokerRequest]) -> str:
    payload = json.dumps([asdict(item) for item in requests], ensure_ascii=False, sort_keys=True)
    return "batch_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def host_key_for_request(request: ResourceBrokerRequest) -> str:
    url = request.url or (request.target if str(request.target).startswith(("http://", "https://")) else "")
    if not url:
        return "local"
    parsed = urllib.parse.urlparse(url)
    return parsed.netloc.lower() or "unknown"


def semantic_host_key_for_request(request: ResourceBrokerRequest, route: dict[str, Any]) -> str:
    """Return a concurrency key that reflects the real shared bottleneck.

    URL-backed resources use their hostname. Non-URL owner routes use semantic
    keys so package/docs requests do not collapse into the generic `local`
    bucket, which would hide registry or MCP pressure from the scheduler.
    """

    host_key = host_key_for_request(request)
    if host_key != "local":
        return host_key
    primary_tool = str(route.get("primary_tool") or "")
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    ecosystem = str(metadata.get("package_ecosystem") or metadata.get("ecosystem") or "").strip().lower()
    task_text = " ".join([request.task, request.target, request.name]).lower()
    if primary_tool == "package_manager" or str(route.get("intent") or request.intent or "") == ResourceIntent.PACKAGE_DEPENDENCY:
        if ecosystem in {"node", "npm", "pnpm", "yarn"} or any(term in task_text for term in ("npm ", "pnpm ", "yarn ", "npx ")):
            return "package:npm"
        return "package:pypi"
    if primary_tool in PACKAGE_REGISTRY_HOST_KEYS:
        return PACKAGE_REGISTRY_HOST_KEYS[primary_tool]
    if primary_tool in OWNER_TOOL_HOST_KEYS:
        return OWNER_TOOL_HOST_KEYS[primary_tool]
    if route.get("source_kind") == "unknown" or primary_tool == "resource_router":
        return "source_selection"
    intent = str(route.get("intent") or request.intent or "")
    if intent == ResourceIntent.DOCUMENTATION_LOOKUP:
        return "docs:unknown"
    return host_key


def classify_queue(request: ResourceBrokerRequest, route: dict[str, Any]) -> str:
    if route.get("source_kind") == "local_file":
        return "local_light"
    if request.need_materialization and route.get("source_kind") == "url":
        return "download"
    primary_tool = str(route.get("primary_tool") or "")
    intent = str(route.get("intent") or request.intent or "")
    if primary_tool == "github":
        return "github_metadata"
    if primary_tool == "package_manager" or intent == ResourceIntent.PACKAGE_DEPENDENCY:
        return "package_metadata" if request.auto_owner else "install_or_clone"
    if route.get("source_kind") == "unknown" or primary_tool == "resource_router":
        return "source_selection"
    if primary_tool in {"context7", "microsoftdocs", "markitdown", "playwright", "chrome-devtools"}:
        return "owner_mcp"
    stage = str(route.get("recommended_stage") or "")
    if stage == "probe":
        return "url_probe"
    if route.get("source_kind") == "url":
        return "url_preview"
    return "unknown"


def request_priority(queue_class: str, index: int) -> int:
    order = {
        "local_light": 10,
        "url_probe": 20,
        "github_metadata": 30,
        "package_metadata": 35,
        "url_preview": 40,
        "owner_mcp": 50,
        "source_selection": 60,
        "download": 70,
        "install_or_clone": 90,
        "unknown": 100,
    }
    return order.get(queue_class, 100) * 1000 + index


def schedule_requests(requests: list[ResourceBrokerRequest]) -> list[ScheduledResource]:
    scheduled: list[ScheduledResource] = []
    for index, request in enumerate(requests, start=1):
        route = route_for_request(request).to_dict()
        queue_class = classify_queue(request, route)
        scheduled.append(
            ScheduledResource(
                index=index,
                request=request,
                route=route,
                queue_class=queue_class,
                host_key=semantic_host_key_for_request(request, route),
                priority=request_priority(queue_class, index),
            )
        )
    return sorted(scheduled, key=lambda item: item.priority)


def run_json_command(command: list[str], *, timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "reason": "command_failed",
            "error": str(exc),
            "command": command,
        }
    if completed.returncode != 0:
        return {
            "ok": False,
            "reason": "command_returned_nonzero",
            "returncode": completed.returncode,
            "stderr": completed.stderr,
            "command": command,
        }
    if not (completed.stdout or "").strip():
        return {
            "ok": False,
            "reason": "command_returned_empty_stdout",
            "stderr": completed.stderr,
            "command": command,
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "reason": "command_returned_invalid_json",
            "error": str(exc),
            "stdout_preview": (completed.stdout or "")[:500],
            "command": command,
        }
    return payload if isinstance(payload, dict) else {"ok": False, "reason": "command_returned_non_object_json", "payload": payload}


def network_gateway_batch_plan(
    gateway_requests: list[dict[str, Any]],
    *,
    timeout_seconds: int = 90,
) -> tuple[dict[str, Any], str]:
    """Use the gateway owner API first; retain the CLI as a bounded fallback."""

    try:
        payload = codex_network_gateway.batch_plan(gateway_requests, total_budget_seconds=timeout_seconds)
        if isinstance(payload, dict):
            return payload, "in_process_owner_api"
        owner_error = {"ok": False, "reason": "owner_api_returned_non_object"}
    except Exception as exc:
        owner_error = {
            "ok": False,
            "reason": "owner_api_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as fh:
        json.dump(gateway_requests, fh, ensure_ascii=False, sort_keys=True)
        request_path = Path(fh.name)
    try:
        payload = run_json_command(
            [
                console_python_executable(),
                str(Path(__file__).resolve().parent / "codex_network_gateway.py"),
                "batch-plan",
                "--requests-file",
                str(request_path),
                "--total-timeout-seconds",
                str(max(1, timeout_seconds)),
            ],
            timeout=max(1, timeout_seconds),
        )
    finally:
        try:
            request_path.unlink()
        except OSError:
            pass
    if not payload.get("ok"):
        payload = dict(payload)
        payload["owner_api_error"] = owner_error
    return payload, "console_python_cli_fallback"


def precompute_network_plans(
    scheduled: list[ScheduledResource],
    *,
    timeout_seconds: int = 90,
) -> tuple[list[ScheduledResource], dict[str, Any]]:
    """Ask the network gateway for batch route decisions, then attach them.

    The scheduler does not interpret route health. It only batches repeated
    network-layer questions and passes returned plans to the broker.
    """

    gateway_requests: list[dict[str, Any]] = []
    index_by_gateway_item: list[int] = []
    for item in scheduled:
        spec = network_gateway_request_for_request(item.request, route_for_request(item.request))
        if spec.get("ok") and not spec.get("skipped"):
            gateway_requests.append(
                {
                    "target_kind": spec.get("target_kind", ""),
                    "target": spec.get("target", ""),
                    "owner_tool": spec.get("owner_tool", ""),
                    "runtime": spec.get("runtime", "generic"),
                    "probe": bool(spec.get("probe", True)),
                    "probe_timeout": max(1, min(int(spec.get("probe_timeout") or 12), timeout_seconds)),
                }
            )
            index_by_gateway_item.append(item.index)
    if not gateway_requests:
        return scheduled, {
            "schema": "resource_scheduler.network_batch.v1",
            "ok": True,
            "skipped": True,
            "reason": "no_live_network_requests",
            "request_count": 0,
        }
    payload, gateway_mode = network_gateway_batch_plan(gateway_requests, timeout_seconds=timeout_seconds)
    if not payload.get("ok"):
        return scheduled, {
            "schema": "resource_scheduler.network_batch.v1",
            "ok": False,
            "reason": "network_gateway_batch_plan_failed",
            "gateway_result": payload,
            "gateway_python": console_python_executable(),
            "gateway_mode": gateway_mode,
            "request_count": len(gateway_requests),
        }
    by_original_index: dict[int, dict[str, Any]] = {}
    for result in payload.get("results") or []:
        gateway_index = int(result.get("index") or 0)
        if 1 <= gateway_index <= len(index_by_gateway_item):
            by_original_index[index_by_gateway_item[gateway_index - 1]] = result.get("plan") if isinstance(result.get("plan"), dict) else result
    enriched: list[ScheduledResource] = []
    for item in scheduled:
        plan = by_original_index.get(item.index)
        if not plan:
            enriched.append(item)
            continue
        metadata = dict(item.request.metadata or {})
        metadata["network_gateway_plan"] = plan
        metadata["network_gateway_plan_source"] = "resource_scheduler_batch"
        enriched.append(replace(item, request=replace(item.request, metadata=metadata)))
    summary = {
        "schema": "resource_scheduler.network_batch.v1",
        "ok": True,
        "request_count": len(gateway_requests),
        "attached_count": len(by_original_index),
        "cache_hit_count": payload.get("cache_hit_count", 0),
        "stale_hit_count": payload.get("stale_hit_count", 0),
        "gateway_mode": gateway_mode,
        "rule": "scheduler batches network-layer route questions only; resource policy and execution remain in resource layer",
    }
    return enriched, summary


def _semaphore_for(mapping: dict[str, threading.Semaphore], key: str, limit: int) -> threading.Semaphore:
    if key not in mapping:
        mapping[key] = threading.Semaphore(max(1, limit))
    return mapping[key]


def execute_one(
    item: ScheduledResource,
    *,
    config: ResourceBatchConfig,
    class_semaphores: dict[str, threading.Semaphore],
    host_semaphores: dict[str, threading.Semaphore],
    event_log: Path,
    receipt_log: Path,
    resource_log: Path | None,
    store_root: Path,
    batch_budget: ResourceExecutionBudget,
) -> dict[str, Any]:
    contract = batch_item_contract(item.request, item.index)
    class_limit = int(config.class_limits.get(item.queue_class, 1))
    if class_limit <= 0:
        return {
            "index": item.index,
            "item_id": contract["item_id"],
            "required": contract["required"],
            "ok": False,
            "status": "deferred",
            "queue_class": item.queue_class,
            "host_key": item.host_key,
            "reason": "queue_class_not_auto_executable",
            "route": item.route,
            "request": asdict(item.request),
            "acceptance": {
                "schema": "resource_scheduler.item_acceptance.v1",
                "item_id": contract["item_id"],
                "required": contract["required"],
                "accepted": False,
                "reason": "queue_class_not_auto_executable",
                "reasons": ["queue_class_not_auto_executable"],
            },
        }
    class_sem = _semaphore_for(class_semaphores, item.queue_class, class_limit)
    host_sem = _semaphore_for(host_semaphores, item.host_key, config.per_host_limit)
    started_at = now_iso()
    with class_sem:
        with host_sem:
            remaining = batch_budget.remaining_seconds()
            if batch_budget.exhausted():
                return {
                    "index": item.index,
                    "item_id": contract["item_id"],
                    "required": contract["required"],
                    "ok": False,
                    "status": "failed",
                    "queue_class": item.queue_class,
                    "host_key": item.host_key,
                    "reason": "total_budget_exhausted",
                    "error_class": "total_budget_exhausted",
                    "route": item.route,
                    "request": asdict(item.request),
                    "execution_budget": batch_budget.snapshot(phase="before_item"),
                    "acceptance": {
                        "schema": "resource_scheduler.item_acceptance.v1",
                        "item_id": contract["item_id"],
                        "required": contract["required"],
                        "accepted": False,
                        "reason": "total_budget_exhausted",
                        "reasons": ["total_budget_exhausted"],
                    },
                }
            receipt = handle_request(
                item.request,
                event_log=event_log,
                receipt_log=receipt_log,
                resource_log=resource_log,
                store_root=store_root,
                execution_budget_seconds=(
                    min(float(item.request.timeout_seconds), remaining)
                    if batch_budget.bounded
                    else item.request.timeout_seconds
                ),
            )
    acceptance = evaluate_item_acceptance(item.request, receipt, item.index)
    return {
        "index": item.index,
        "item_id": contract["item_id"],
        "required": contract["required"],
        "ok": bool(acceptance["accepted"]),
        "status": receipt.status,
        "queue_class": item.queue_class,
        "host_key": item.host_key,
        "request_id": receipt.request_id,
        "result_kind": receipt.result_kind,
        "manifest_path": receipt.manifest_path,
        "artifact_path": receipt.artifact_path,
        "next_action": receipt.next_action,
        "error_class": receipt.error_class,
        "network_summary": receipt.network_summary,
        "satisfaction": receipt.satisfaction,
        "acceptance": acceptance,
        "started_at": started_at,
        "finished_at": now_iso(),
        "execution_budget": getattr(receipt, "execution_budget", {}),
    }


def cancelled_item_result(item: ScheduledResource, *, reason: str) -> dict[str, Any]:
    contract = batch_item_contract(item.request, item.index)
    return {
        "index": item.index,
        "item_id": contract["item_id"],
        "required": contract["required"],
        "ok": False,
        "status": "deferred",
        "queue_class": item.queue_class,
        "host_key": item.host_key,
        "request_id": "",
        "result_kind": "none",
        "manifest_path": "",
        "artifact_path": "",
        "next_action": "inspect_failed_required_item_before_resuming_batch",
        "error_class": reason,
        "network_summary": {},
        "satisfaction": {"schema": "resource_satisfaction.v1", "satisfied": False, "reason": reason},
        "acceptance": {
            "schema": "resource_scheduler.item_acceptance.v1",
            "item_id": contract["item_id"],
            "required": contract["required"],
            "accepted": False,
            "reason": reason,
            "reasons": [reason],
        },
        "started_at": "",
        "finished_at": now_iso(),
        "execution_budget": {},
    }


def write_batch_manifest(store_root: Path, batch_id: str, payload: dict[str, Any]) -> Path:
    batch_dir = store_root / "_batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    path = batch_dir / "batch.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    index_path = store_root / "_batches" / "index.jsonl"
    with index_path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "schema": "resource_scheduler.batch_index.v1",
                    "batch_id": batch_id,
                    "ok": payload.get("ok"),
                    "status": payload.get("status"),
                    "request_count": payload.get("request_count"),
                    "completed_count": payload.get("completed_count"),
                    "accepted_count": payload.get("accepted_count"),
                    "required_count": payload.get("required_count"),
                    "unmet_required_count": payload.get("unmet_required_count"),
                    "deferred_count": payload.get("deferred_count"),
                    "failed_count": payload.get("failed_count"),
                    "failed_item_ids": payload.get("failed_item_ids", []),
                    "manifest_path": str(path),
                    "generated_at": payload.get("generated_at"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
    return path


def batch_status_from_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": "resource_scheduler.batch_status.v1",
            "ok": False,
            "reason": "batch_manifest_missing",
            "manifest_path": str(path),
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    planned = payload.get("planned") if isinstance(payload.get("planned"), list) else []
    by_status: dict[str, int] = {}
    by_class: dict[str, int] = {}
    host_keys: dict[str, int] = {}
    for item in results:
        status = str(item.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        queue_class = str(item.get("queue_class") or "unknown")
        by_class[queue_class] = by_class.get(queue_class, 0) + 1
        host_key = str(item.get("host_key") or "unknown")
        host_keys[host_key] = host_keys.get(host_key, 0) + 1
    return {
        "schema": "resource_scheduler.batch_status.v1",
        "ok": True,
        "read_ok": True,
        "batch_ok": bool(payload.get("ok")),
        "batch_id": payload.get("batch_id", ""),
        "status": payload.get("status", ""),
        "manifest_path": str(path),
        "request_count": payload.get("request_count", len(planned)),
        "completed_count": payload.get("completed_count", 0),
        "accepted_count": payload.get("accepted_count", payload.get("completed_count", 0)),
        "required_count": payload.get("required_count", 0),
        "unmet_required_count": payload.get("unmet_required_count", 0),
        "deferred_count": payload.get("deferred_count", 0),
        "failed_count": payload.get("failed_count", 0),
        "accepted_item_ids": payload.get("accepted_item_ids", []),
        "failed_item_ids": payload.get("failed_item_ids", []),
        "deferred_item_ids": payload.get("deferred_item_ids", []),
        "planned_count": len(planned),
        "result_count": len(results),
        "by_status": by_status,
        "by_class": by_class,
        "host_keys": host_keys,
        "generated_at": payload.get("generated_at", ""),
    }


def execute_batch(
    requests: list[ResourceBrokerRequest],
    *,
    config: ResourceBatchConfig | None = None,
    event_log: Path = DEFAULT_EVENT_LOG,
    receipt_log: Path = DEFAULT_RECEIPT_LOG,
    resource_log: Path | None = None,
    store_root: Path = DEFAULT_STORE_ROOT,
) -> dict[str, Any]:
    config = config or ResourceBatchConfig()
    batch_budget = ResourceExecutionBudget.start(config.total_budget_seconds)
    batch_id = stable_batch_id(requests)
    scheduled = schedule_requests(requests)
    network_budget_cap = (
        max(1, min(15, int(max(1.0, config.total_budget_seconds) * 0.25)))
        if batch_budget.bounded
        else 90
    )
    network_timeout = batch_budget.timeout_seconds(cap=network_budget_cap) if batch_budget.bounded else 90
    if batch_budget.bounded and network_timeout <= 0:
        network_batch = {
            "schema": "resource_scheduler.network_batch.v1",
            "ok": False,
            "reason": "total_budget_exhausted",
            "request_count": 0,
            "execution_budget": batch_budget.snapshot(phase="network_batch_skipped"),
        }
    else:
        scheduled, network_batch = precompute_network_plans(scheduled, timeout_seconds=network_timeout)
    planned = [asdict(item) for item in scheduled]
    results: list[dict[str, Any]] = []
    if not config.plan_only:
        class_semaphores: dict[str, threading.Semaphore] = {}
        host_semaphores: dict[str, threading.Semaphore] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(config.max_active))) as pool:
            def submit_item(item: ScheduledResource) -> concurrent.futures.Future[dict[str, Any]]:
                return pool.submit(
                    execute_one,
                    item,
                    config=config,
                    class_semaphores=class_semaphores,
                    host_semaphores=host_semaphores,
                    event_log=event_log,
                    receipt_log=receipt_log,
                    resource_log=resource_log,
                    store_root=store_root,
                    batch_budget=batch_budget,
                )

            if config.fail_fast:
                remaining_items = iter(scheduled)
                future_items: dict[concurrent.futures.Future[dict[str, Any]], ScheduledResource] = {}
                for _slot in range(max(1, int(config.max_active))):
                    item = next(remaining_items, None)
                    if item is None:
                        break
                    future_items[submit_item(item)] = item
                stopped = False
                while future_items:
                    done, _pending = concurrent.futures.wait(
                        future_items,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for future in done:
                        future_items.pop(future, None)
                        if future.cancelled():
                            continue
                        result = future.result()
                        results.append(result)
                        if bool(result.get("required", True)) and not bool((result.get("acceptance") or {}).get("accepted")):
                            stopped = True
                    if stopped:
                        for pending, pending_item in list(future_items.items()):
                            if pending.cancel():
                                future_items.pop(pending, None)
                                results.append(cancelled_item_result(pending_item, reason="fail_fast_cancelled"))
                        for pending_item in remaining_items:
                            results.append(cancelled_item_result(pending_item, reason="fail_fast_cancelled"))
                    else:
                        while len(future_items) < max(1, int(config.max_active)):
                            item = next(remaining_items, None)
                            if item is None:
                                break
                            future_items[submit_item(item)] = item
            else:
                future_items = {submit_item(item): item for item in scheduled}
                for future in concurrent.futures.as_completed(future_items):
                    results.append(future.result())
        results.sort(key=lambda item: int(item.get("index") or 0))
    completed_count = sum(1 for item in results if (item.get("acceptance") or {}).get("accepted"))
    deferred_count = sum(1 for item in results if item.get("status") in {"deferred", "handoff_required"})
    failed_count = sum(
        1
        for item in results
        if not (item.get("acceptance") or {}).get("accepted")
        and item.get("status") not in {"deferred", "handoff_required"}
    )
    required_count = sum(
        1
        for index, request in enumerate(requests, start=1)
        if bool(batch_item_contract(request, index).get("required", True))
    )
    unmet_required = [
        item
        for item in results
        if bool(item.get("required", True))
        and not bool((item.get("acceptance") or {}).get("accepted"))
    ]
    failed_item_ids = [
        str(item.get("item_id") or "")
        for item in results
        if not bool((item.get("acceptance") or {}).get("accepted"))
    ]
    deferred_item_ids = [
        str(item.get("item_id") or "")
        for item in results
        if item.get("status") in {"deferred", "handoff_required"}
    ]
    accepted_item_ids = [
        str(item.get("item_id") or "")
        for item in results
        if bool((item.get("acceptance") or {}).get("accepted"))
    ]
    if config.plan_only:
        aggregate_status = "planned"
    elif not unmet_required:
        aggregate_status = "completed"
    elif completed_count:
        aggregate_status = "partial"
    elif results and all(item.get("status") in {"deferred", "handoff_required"} for item in results):
        aggregate_status = "deferred"
    elif results and all(item.get("status") == "blocked" for item in results):
        aggregate_status = "blocked"
    else:
        aggregate_status = "failed"
    payload = {
        "schema": "resource_scheduler.batch_receipt.v1",
        "ok": aggregate_status in {"planned", "completed"},
        "status": aggregate_status,
        "batch_id": batch_id,
        "generated_at": now_iso(),
        "request_count": len(requests),
        "completed_count": completed_count,
        "accepted_count": completed_count,
        "required_count": required_count,
        "unmet_required_count": len(unmet_required),
        "deferred_count": deferred_count,
        "failed_count": failed_count,
        "accepted_item_ids": accepted_item_ids,
        "failed_item_ids": failed_item_ids,
        "deferred_item_ids": deferred_item_ids,
        "config": {
            "max_active": config.max_active,
            "per_host_limit": config.per_host_limit,
            "class_limits": config.class_limits,
            "plan_only": config.plan_only,
            "fail_fast": config.fail_fast,
            "total_budget_seconds": config.total_budget_seconds,
        },
        "execution_budget": batch_budget.snapshot(phase="batch_complete"),
        "network_batch": network_batch,
        "planned": planned,
        "results": results,
    }
    manifest_path = write_batch_manifest(store_root.expanduser().resolve(), batch_id, payload)
    payload["manifest_path"] = str(manifest_path)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def requests_from_payload(payload: Any) -> list[ResourceBrokerRequest]:
    raw_items = None
    batch_name = ""
    if isinstance(payload, dict):
        raw_items = payload.get("items") if isinstance(payload.get("items"), list) else payload.get("requests")
        batch_name = str(payload.get("batch_name") or "").strip()
    else:
        raw_items = payload
    if not isinstance(raw_items, list):
        raise ValueError("batch payload must be a list or an object with items/requests")
    requests: list[ResourceBrokerRequest] = []
    seen_item_ids: set[str] = set()
    contract_keys = {"item_id", "required", "acceptance", "quantity", "source", "freshness", "diversity", "request"}
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"batch item {index} must be an object")
        request_payload = (
            item.get("request")
            if isinstance(item.get("request"), dict)
            else {key: value for key, value in item.items() if key not in contract_keys}
        )
        request = request_from_payload(request_payload)
        item_id = str(item.get("item_id") or f"item-{index:03d}").strip()
        if not item_id:
            raise ValueError(f"batch item {index} item_id is empty")
        if item_id in seen_item_ids:
            raise ValueError(f"duplicate batch item_id: {item_id}")
        seen_item_ids.add(item_id)
        metadata = dict(request.metadata or {})
        metadata[BATCH_ITEM_METADATA_KEY] = {
            "item_id": item_id,
            "required": bool(item.get("required", True)),
            "acceptance": item.get("acceptance") if isinstance(item.get("acceptance"), dict) else {},
            "quantity": item.get("quantity") if isinstance(item.get("quantity"), dict) else {},
            "source": item.get("source") if isinstance(item.get("source"), dict) else {},
            "freshness": item.get("freshness") if isinstance(item.get("freshness"), dict) else {},
            "diversity": item.get("diversity") if isinstance(item.get("diversity"), dict) else {},
            "batch_name": batch_name,
        }
        requests.append(replace(request, metadata=metadata))
    if not requests:
        raise ValueError("batch payload contains no resource items")
    return requests


def batch_config_from_payload(payload: Any, *, plan_only: bool = False) -> ResourceBatchConfig:
    execution = payload.get("execution") if isinstance(payload, dict) and isinstance(payload.get("execution"), dict) else {}
    return ResourceBatchConfig(
        max_active=max(1, min(int(execution.get("max_active") or 6), 32)),
        per_host_limit=max(1, min(int(execution.get("per_host_limit") or 2), 16)),
        plan_only=bool(plan_only or execution.get("plan_only")),
        fail_fast=bool(execution.get("fail_fast", False)),
        total_budget_seconds=max(
            0.0,
            float(execution.get("total_budget_seconds") or execution.get("total_timeout_seconds") or 0.0),
        ),
    )


def validate() -> dict[str, Any]:
    requests = [
        ResourceBrokerRequest(path="sample.txt", intent=ResourceIntent.EXPLICIT_LOCAL_FILE, metadata={"validation_profile": "quick"}),
        ResourceBrokerRequest(url="https://github.com/microsoft/playwright", intent=ResourceIntent.EXTERNAL_DEPENDENCY, auto_owner=True, metadata={"validation_profile": "quick"}),
        ResourceBrokerRequest(target="ruff", intent=ResourceIntent.PACKAGE_DEPENDENCY, auto_owner=True, metadata={"validation_profile": "quick"}),
    ]
    scheduled = schedule_requests(requests)
    enriched, network_batch = precompute_network_plans(scheduled)
    classes = [item.queue_class for item in scheduled]
    host_keys = [item.host_key for item in scheduled]
    return {
        "schema": "resource_scheduler.validate.v1",
        "ok": classes == ["local_light", "github_metadata", "package_metadata"] and host_keys[-1] == "package:pypi" and network_batch.get("ok") and len(enriched) == len(scheduled),
        "classes": classes,
        "host_keys": host_keys,
        "network_batch": network_batch,
        "default_class_limits": DEFAULT_CLASS_LIMITS,
        "writes_global_network_state": False,
        "background_daemon": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch resource scheduler")
    sub = parser.add_subparsers(dest="command", required=True)
    execute = sub.add_parser("execute")
    execute.add_argument("--payload-file", required=True)
    execute.add_argument("--plan-only", action="store_true")
    execute.add_argument("--max-active", type=int, default=6)
    execute.add_argument("--per-host-limit", type=int, default=2)
    execute.add_argument("--total-timeout-seconds", type=float, default=0.0)
    execute.add_argument("--event-log", default=str(DEFAULT_EVENT_LOG))
    execute.add_argument("--receipt-log", default=str(DEFAULT_RECEIPT_LOG))
    execute.add_argument("--resource-log", default="")
    execute.add_argument("--store-root", default=str(DEFAULT_STORE_ROOT))
    execute.add_argument("--json", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("--manifest-path", required=True)
    status.add_argument("--json", action="store_true")
    sub.add_parser("validate")
    args = parser.parse_args()
    if args.command == "validate":
        print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        payload = batch_status_from_manifest(Path(args.manifest_path).expanduser().resolve())
        print(json.dumps(payload, ensure_ascii=False, indent=None if args.json else 2, sort_keys=True))
        return 0 if payload.get("ok") else 1
    payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    batch = execute_batch(
        requests_from_payload(payload),
        config=ResourceBatchConfig(
            max_active=args.max_active,
            per_host_limit=args.per_host_limit,
            plan_only=args.plan_only,
            total_budget_seconds=max(0.0, args.total_timeout_seconds),
        ),
        event_log=Path(args.event_log).expanduser().resolve(),
        receipt_log=Path(args.receipt_log).expanduser().resolve(),
        resource_log=Path(args.resource_log).expanduser().resolve() if args.resource_log else None,
        store_root=Path(args.store_root).expanduser().resolve(),
    )
    print(json.dumps(batch, ensure_ascii=False, indent=2 if not args.json else None, sort_keys=True))
    return 0 if batch.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
