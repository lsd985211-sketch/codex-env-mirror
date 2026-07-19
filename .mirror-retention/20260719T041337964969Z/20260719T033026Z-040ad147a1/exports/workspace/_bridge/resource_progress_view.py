#!/usr/bin/env python3
"""Conversation-oriented resource progress views.

Ownership: compact read-only status views for resource requests and batches.
Non-goals: executing requests, mutating manifests/receipts, background queues,
owner MCP calls, or network/proxy changes.
State behavior: reads broker receipts and manifests only.
Caller context: Codex conversation, `resource_cli.py progress`, and Hub
`resource.progress`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from resource_broker import DEFAULT_RECEIPT_LOG, read_receipt
from resource_codex_guidance import build_codex_guidance
from resource_scheduler import batch_status_from_manifest
from resource_store import read_manifest


END_TO_END_TERMINAL_STATUSES = {"failed", "blocked", "deferred"}
RESOURCE_LAYER_RECEIPT_STATUSES = END_TO_END_TERMINAL_STATUSES | {"completed", "handoff_required"}
NONTERMINAL_STATUSES = {"planned", "running", "submitted"}
FAILED_NEXT_ACTION_ALLOWLIST = {
    "adjust_request_constraints",
    "retry_with_corrected_resource_reference",
    "surface_owner_tool_failure",
    "use_package_manager_policy_before_install",
    "inspect_error_and_retry_or_escalate",
}
RESOURCE_PHASES = ("submitted", "classified", "network_gateway", "planned", "attempting", "reported")
PHASE_PROGRESS = {
    "submitted": 10,
    "classified": 25,
    "network_gateway": 40,
    "planned": 55,
    "attempting": 75,
    "reported": 100,
}


def ownership_signal_for_status(status: str) -> dict[str, Any]:
    resource_need_satisfied = status == "completed"
    refine_required = status == "deferred"
    route_chain_allowed = status in {"failed", "blocked"}
    if status == "handoff_required":
        policy = "continue_resource_layer_handoff_or_attach_result"
    elif resource_need_satisfied:
        policy = "resource_satisfied"
    elif refine_required:
        policy = "refine_resource_delegation_and_retry"
    elif route_chain_allowed:
        policy = "use_configured_owner_hub_online_route_chain_before_any_direct_web"
    elif status in NONTERMINAL_STATUSES:
        policy = "wait_for_resource_layer_receipt"
    else:
        policy = "resource_layer_not_satisfied"
    return {
        "resource_need_satisfied": resource_need_satisfied,
        "same_need_fetch_allowed": False,
        "same_need_independent_direct_fetch_allowed": False,
        "direct_generic_web_allowed": False,
        "refine_resource_delegation_required": refine_required,
        "configured_online_route_chain_allowed": route_chain_allowed,
        "same_need_fetch_policy": policy,
    }


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _short_path(value: Any) -> str:
    return str(value or "")


def consume_paths_for(status: str, paths: dict[str, str]) -> list[dict[str, str]]:
    if status != "completed":
        return []
    candidates = (
        ("owner_result", "read_owner_result"),
        ("preview", "read_preview"),
        ("artifact", "inspect_or_open_artifact"),
        ("manifest", "read_manifest"),
    )
    required: list[dict[str, str]] = []
    seen: set[str] = set()
    for kind, action in candidates:
        path = _short_path(paths.get(kind))
        if not path or path in seen:
            continue
        required.append({"kind": kind, "path": path, "codex_action": action})
        seen.add(path)
    return required


def _owner_calls(owner: dict[str, Any]) -> list[dict[str, Any]]:
    calls = owner.get("calls")
    if isinstance(calls, list):
        return [item for item in calls if isinstance(item, dict)][:3]
    call = owner.get("call")
    if isinstance(call, dict):
        return [call]
    return []


def status_is_terminal(status: str) -> bool:
    return resource_layer_status_is_terminal(status)


def resource_layer_status_is_terminal(status: str) -> bool:
    return status in RESOURCE_LAYER_RECEIPT_STATUSES


def next_action_for(receipt: dict[str, Any]) -> str:
    status = str(receipt.get("status") or "")
    if status == "completed" and str(receipt.get("result_kind") or "") == "source_selection":
        return "review_candidates_and_resubmit_selected_source"
    consumption = _as_dict(receipt.get("consumption"))
    if status == "completed" and consumption.get("satisfied"):
        return "resource_consumed"
    if status == "completed":
        return "consume_resource"
    if status == "handoff_required":
        return "call_owner_tool_and_attach_result"
    if status in {"failed", "blocked"}:
        action = str(receipt.get("next_action") or "")
        if action in FAILED_NEXT_ACTION_ALLOWLIST:
            return action
        return "inspect_error_and_retry_or_escalate"
    guidance = _as_dict(receipt.get("codex_guidance"))
    if guidance.get("codex_next_action"):
        return str(guidance.get("codex_next_action"))
    return str(receipt.get("next_action") or "refresh_status")


def summary_for(receipt: dict[str, Any], owner: dict[str, Any], attempts: list[Any] | None = None) -> str:
    status = str(receipt.get("status") or "unknown")
    result_kind = str(receipt.get("result_kind") or "none")
    route = _as_dict(receipt.get("route"))
    primary_tool = str(route.get("primary_tool") or "unknown")
    if status == "completed":
        successful_attempt = next(
            (
                item
                for item in reversed(attempts or [])
                if isinstance(item, dict) and (item.get("result") or {}).get("ok")
            ),
            {},
        )
        completed_tool = str(successful_attempt.get("tool") or primary_tool)
        return f"completed {result_kind} via {completed_tool}"
    if status == "handoff_required":
        owner_tool = str(owner.get("owner_tool") or primary_tool)
        return f"waiting for owner tool {owner_tool}; attach result to complete"
    error_class = str(receipt.get("error_class") or "")
    if error_class:
        return f"{status} via {primary_tool}: {error_class}"
    return f"{status} via {primary_tool}"


def current_phase_from_events(events: list[Any], status: str) -> str:
    for item in reversed(events):
        if not isinstance(item, dict):
            continue
        phase = str(item.get("stage") or "")
        if phase:
            return phase
    if status in RESOURCE_LAYER_RECEIPT_STATUSES:
        return "reported"
    return "submitted"


def progress_for_events(events: list[Any], status: str) -> dict[str, Any]:
    phase = current_phase_from_events(events, status)
    percent = PHASE_PROGRESS.get(phase, 0)
    if status in RESOURCE_LAYER_RECEIPT_STATUSES:
        percent = 100
    return {
        "phase": phase,
        "percent": percent,
        "event_count": len(events),
        "phase_order": list(RESOURCE_PHASES),
        "message": _progress_message(phase, status),
    }


def _progress_message(phase: str, status: str) -> str:
    if status == "completed":
        return "resource acquisition completed"
    if status == "handoff_required":
        return "resource layer reached owner-tool handoff"
    if status in {"failed", "blocked", "deferred"}:
        return f"resource acquisition {status}"
    return {
        "submitted": "resource request accepted",
        "classified": "resource route classified",
        "network_gateway": "network route evidence attached",
        "planned": "resource strategy planned",
        "attempting": "resource attempt running",
        "reported": "resource receipt produced",
    }.get(phase, "resource request in progress")


def exception_for(receipt: dict[str, Any], attempts: list[Any]) -> dict[str, Any]:
    status = str(receipt.get("status") or "")
    error_class = str(receipt.get("error_class") or "")
    failing_attempt = next(
        (
            item
            for item in reversed(attempts)
            if isinstance(item, dict)
            and (item.get("error_class") or item.get("status") in {"failed", "blocked", "deferred"})
        ),
        {},
    )
    reason = str(receipt.get("reason") or failing_attempt.get("reason") or "")
    return {
        "has_exception": bool(error_class or status in {"failed", "blocked", "deferred"}),
        "status": status,
        "error_class": error_class,
        "reason": reason,
        "attempt_index": failing_attempt.get("index"),
        "attempt_tool": str(failing_attempt.get("tool") or ""),
        "attempt_stage": str(failing_attempt.get("stage") or ""),
        "recoverable": status in {"handoff_required", "deferred"} or error_class in {
            "handoff_required_for_owner_tool",
            "policy_deferred",
        },
    }


def status_summary_for(
    receipt: dict[str, Any],
    owner: dict[str, Any],
    attempts: list[Any],
    events: list[Any],
    *,
    consume_required: bool | None = None,
) -> dict[str, Any]:
    status = str(receipt.get("status") or "unknown")
    consumption = _as_dict(receipt.get("consumption"))
    completed_consumed = bool(consumption.get("satisfied")) if consume_required is None else not consume_required
    end_to_end_terminal = status in END_TO_END_TERMINAL_STATUSES or (status == "completed" and completed_consumed)
    ownership = ownership_signal_for_status(status)
    return {
        "state": status,
        "summary": summary_for(receipt, owner, attempts),
        "is_terminal": status_is_terminal(status),
        "resource_layer_terminal": resource_layer_status_is_terminal(status),
        "end_to_end_terminal": end_to_end_terminal,
        **ownership,
        "codex_next_action": next_action_for(receipt),
        "confidence": float(receipt.get("confidence") or 0.0),
        "current_phase": current_phase_from_events(events, status),
        "attempt_count": len(attempts),
    }


def request_progress_from_receipt(receipt: dict[str, Any], *, manifest_path: str = "") -> dict[str, Any]:
    request = _as_dict(receipt.get("_request"))
    codex_guidance = _as_dict(receipt.get("codex_guidance"))
    if not codex_guidance and request:
        codex_guidance = build_codex_guidance(request, receipt)
    if codex_guidance:
        receipt = {**receipt, "codex_guidance": codex_guidance}
    route = _as_dict(receipt.get("route"))
    network = _as_dict(receipt.get("network_summary"))
    owner = _as_dict(receipt.get("owner_execution"))
    saved_paths = _as_dict(receipt.get("saved_paths"))
    status = str(receipt.get("status") or "unknown")
    attempts = _as_list(receipt.get("attempts"))
    events = _as_list(receipt.get("progress_events"))
    progress = progress_for_events(events, status)
    exception = exception_for(receipt, attempts)
    manifest = manifest_path or str(receipt.get("manifest_path") or saved_paths.get("manifest") or "")
    owner_result = _as_dict(receipt.get("owner_result"))
    ownership = ownership_signal_for_status(status)
    paths = {
        "manifest": _short_path(manifest),
        "preview": _short_path(receipt.get("preview_path") or saved_paths.get("preview")),
        "artifact": _short_path(receipt.get("artifact_path") or saved_paths.get("artifact")),
        "owner_result": _short_path(owner_result.get("content_path") or saved_paths.get("owner_result")),
    }
    required_consume_paths = consume_paths_for(status, paths)
    consumption = _as_dict(receipt.get("consumption"))
    consumed = bool(consumption.get("satisfied"))
    consume_required = bool(required_consume_paths) and not consumed
    end_to_end_terminal = status in END_TO_END_TERMINAL_STATUSES or (status == "completed" and not consume_required)
    status_summary = status_summary_for(receipt, owner, attempts, events, consume_required=consume_required)
    consume_contract = {
        "required": consume_required,
        "required_paths": required_consume_paths,
        "satisfied": consumed,
        "satisfied_by": "codex_reads_or_evaluates_one_required_path_or_records_no_read_needed_reason",
        "not_complete_until": "resource_completed_receipt_is_consumed_or_evaluated",
    }
    return {
        "schema": "resource_progress.request.v1",
        "ok": bool(receipt.get("request_id")) and status != "unknown",
        "kind": "request",
        "request_id": str(receipt.get("request_id") or ""),
        "status": status,
        "result_kind": str(receipt.get("result_kind") or "none"),
        "is_terminal": status_is_terminal(status),
        "resource_layer_terminal": resource_layer_status_is_terminal(status),
        "end_to_end_terminal": end_to_end_terminal,
        **ownership,
        "next_action": next_action_for(receipt),
        "codex_next_action": status_summary["codex_next_action"],
        "consume_required": consume_required,
        "required_consume_paths": required_consume_paths,
        "consume_contract": consume_contract,
        "consumption": consumption,
        "error_class": str(receipt.get("error_class") or ""),
        "status_summary": status_summary,
        "progress": progress,
        "exception": exception,
        "confidence": float(receipt.get("confidence") or 0.0),
        "route": {
            "primary_tool": str(route.get("primary_tool") or ""),
            "intent": str(route.get("intent") or ""),
            "source_kind": str(route.get("source_kind") or ""),
            "recommended_stage": str(route.get("recommended_stage") or ""),
        },
        "network": {
            "target_kind": str(network.get("target_kind") or ""),
            "route_mode": str(network.get("route_mode") or ""),
            "preferred_route": str(network.get("preferred_route") or ""),
            "direct_ok": network.get("direct_ok", None),
            "proxy_ok": network.get("proxy_ok", None),
        },
        "owner": {
            "owner_tool": str(owner.get("owner_tool") or ""),
            "next_action": str(owner.get("next_action") or ""),
            "requires_codex_action": status == "handoff_required",
            "same_request_attach_required": status == "handoff_required",
            "calls": _owner_calls(owner),
        },
        "paths": paths,
        "attempt_count": len(attempts),
        "event_count": len(events),
        "summary": status_summary["summary"],
        "codex_guidance": codex_guidance,
    }


def progress_for_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = read_manifest(manifest_path.expanduser().resolve())
    if not manifest.get("request_id"):
        return {
            "schema": "resource_progress.request.v1",
            "ok": False,
            "kind": "request",
            "reason": manifest.get("reason") or "manifest_missing_request_id",
            "manifest_path": str(manifest_path),
        }
    receipt = _as_dict(manifest.get("receipt"))
    receipt["_request"] = _as_dict(manifest.get("request"))
    receipt.setdefault("request_id", manifest.get("request_id"))
    if "owner_result" in manifest and "owner_result" not in receipt:
        receipt["owner_result"] = manifest.get("owner_result")
    if "saved_paths" in manifest and "saved_paths" not in receipt:
        receipt["saved_paths"] = manifest.get("saved_paths")
    return request_progress_from_receipt(receipt, manifest_path=str(manifest_path.expanduser().resolve()))


def progress_for_request(request_id: str, *, receipt_log: Path = DEFAULT_RECEIPT_LOG) -> dict[str, Any]:
    request_id = str(request_id or "").strip()
    if not request_id:
        return {"schema": "resource_progress.request.v1", "ok": False, "kind": "request", "reason": "request_id_required"}
    receipt = read_receipt(receipt_log.expanduser().resolve(), request_id)
    manifest_path = str(receipt.get("manifest_path") or "")
    if manifest_path and Path(manifest_path).exists():
        manifest_view = progress_for_manifest(Path(manifest_path))
        if manifest_view.get("ok"):
            manifest_view["receipt_log"] = str(receipt_log.expanduser().resolve())
            return manifest_view
    if receipt.get("request_id"):
        payload = request_progress_from_receipt(receipt, manifest_path=manifest_path)
        payload["receipt_log"] = str(receipt_log.expanduser().resolve())
        return payload
    return {
        "schema": "resource_progress.request.v1",
        "ok": False,
        "kind": "request",
        "request_id": request_id,
        "reason": receipt.get("reason") or "receipt_not_found",
        "receipt_log": str(receipt_log.expanduser().resolve()),
    }


def _batch_item_summary(item: dict[str, Any]) -> dict[str, Any]:
    network = _as_dict(item.get("network_summary"))
    acceptance = _as_dict(item.get("acceptance"))
    request = _as_dict(item.get("request"))
    metadata = _as_dict(request.get("metadata"))
    contract = _as_dict(metadata.get("batch_item_contract"))
    contract_acceptance = _as_dict(contract.get("acceptance"))
    planned_item = not bool(item.get("request_id")) and bool(request)
    return {
        "index": item.get("index"),
        "item_id": str(item.get("item_id") or contract.get("item_id") or ""),
        "required": bool(item.get("required", contract.get("required", True))),
        "request_id": str(item.get("request_id") or ""),
        "status": str(item.get("status") or ("planned" if planned_item else "unknown")),
        "result_kind": str(item.get("result_kind") or "none"),
        "queue_class": str(item.get("queue_class") or ""),
        "host_key": str(item.get("host_key") or ""),
        "next_action": str(item.get("next_action") or ""),
        "error_class": str(item.get("error_class") or ""),
        "manifest_path": str(item.get("manifest_path") or ""),
        "network_target_kind": str(network.get("target_kind") or ""),
        "accepted": bool(acceptance.get("accepted")),
        "acceptance_reason": str(acceptance.get("reason") or ""),
        "candidate_count": int(acceptance.get("candidate_count") or 0),
        "minimum_candidates": int(
            acceptance.get("minimum_candidates")
            or contract_acceptance.get("minimum_candidates")
            or contract_acceptance.get("minimum_quantity")
            or 0
        ),
    }


def progress_for_batch(manifest_path: Path, *, include_items: bool = False, limit: int = 20) -> dict[str, Any]:
    path = manifest_path.expanduser().resolve()
    status = batch_status_from_manifest(path)
    if not status.get("ok"):
        return {
            "schema": "resource_progress.batch.v1",
            "ok": False,
            "kind": "batch",
            "reason": status.get("reason") or "batch_status_failed",
            "manifest_path": str(path),
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = [item for item in _as_list(payload.get("results")) if isinstance(item, dict)]
    planned = [item for item in _as_list(payload.get("planned")) if isinstance(item, dict)]
    next_action = "consume_completed_items"
    if status.get("unmet_required_count"):
        next_action = "refine_failed_items_and_retry_only_those_items"
    elif status.get("failed_count"):
        next_action = "inspect_failed_items"
    elif status.get("deferred_count"):
        next_action = "complete_handoffs_or_approval_steps"
    elif status.get("status") == "planned":
        next_action = "execute_batch_when_approved"
    view = {
        "schema": "resource_progress.batch.v1",
        "ok": True,
        "kind": "batch",
        "batch_id": str(status.get("batch_id") or ""),
        "status": str(status.get("status") or ""),
        "is_terminal": str(status.get("status") or "") != "planned",
        "next_action": next_action,
        "counts": {
            "request": int(status.get("request_count") or 0),
            "completed": int(status.get("completed_count") or 0),
            "accepted": int(status.get("accepted_count") or 0),
            "required": int(status.get("required_count") or 0),
            "unmet_required": int(status.get("unmet_required_count") or 0),
            "deferred": int(status.get("deferred_count") or 0),
            "failed": int(status.get("failed_count") or 0),
            "planned": int(status.get("planned_count") or 0),
            "result": int(status.get("result_count") or 0),
        },
        "by_status": _as_dict(status.get("by_status")),
        "by_class": _as_dict(status.get("by_class")),
        "host_keys": _as_dict(status.get("host_keys")),
        "accepted_item_ids": _as_list(status.get("accepted_item_ids")),
        "failed_item_ids": _as_list(status.get("failed_item_ids")),
        "deferred_item_ids": _as_list(status.get("deferred_item_ids")),
        "paths": {"manifest": str(path)},
        "summary": (
            f"batch {status.get('status')} "
            f"completed={status.get('completed_count', 0)} "
            f"unmet_required={status.get('unmet_required_count', 0)} "
            f"deferred={status.get('deferred_count', 0)} "
            f"failed={status.get('failed_count', 0)}"
        ),
    }
    if include_items:
        selected = results if results else planned
        view["items"] = [_batch_item_summary(item) for item in selected[: max(0, int(limit))]]
        view["item_limit"] = int(limit)
        view["item_total"] = len(selected)
    return view


def progress_view(
    *,
    request_id: str = "",
    manifest_path: str = "",
    batch_manifest_path: str = "",
    include_items: bool = False,
    limit: int = 20,
    receipt_log: Path = DEFAULT_RECEIPT_LOG,
) -> dict[str, Any]:
    targets = [bool(request_id), bool(manifest_path), bool(batch_manifest_path)]
    if sum(1 for item in targets if item) != 1:
        return {
            "schema": "resource_progress.dispatch.v1",
            "ok": False,
            "reason": "choose_exactly_one_of_request_id_manifest_path_batch_manifest_path",
        }
    if request_id:
        return progress_for_request(request_id, receipt_log=receipt_log)
    if manifest_path:
        return progress_for_manifest(Path(manifest_path))
    return progress_for_batch(Path(batch_manifest_path), include_items=include_items, limit=limit)


def validate() -> dict[str, Any]:
    request_view = request_progress_from_receipt(
        {
            "request_id": "res_validate",
            "status": "handoff_required",
            "result_kind": "metadata",
            "route": {"primary_tool": "context7", "intent": "documentation_lookup", "source_kind": "url"},
            "network_summary": {"target_kind": "docs", "route_mode": "proxy", "preferred_route": "proxy"},
            "owner_execution": {"owner_tool": "context7", "next_action": "call_owner_tool"},
            "confidence": 0.7,
        }
    )
    return {
        "schema": "resource_progress.validate.v1",
        "ok": request_view.get("owner", {}).get("requires_codex_action") is True
        and request_view.get("next_action") == "call_owner_tool_and_attach_result"
        and request_view.get("resource_layer_terminal") is True
        and request_view.get("end_to_end_terminal") is False,
        "request_view": request_view,
        "read_only": True,
        "writes_state": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read compact resource progress views.")
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request")
    request.add_argument("--request-id", required=True)
    request.add_argument("--receipt-log", default=str(DEFAULT_RECEIPT_LOG))
    request.add_argument("--json", action="store_true")
    manifest = sub.add_parser("manifest")
    manifest.add_argument("--manifest-path", required=True)
    manifest.add_argument("--json", action="store_true")
    batch = sub.add_parser("batch")
    batch.add_argument("--manifest-path", required=True)
    batch.add_argument("--include-items", action="store_true")
    batch.add_argument("--limit", type=int, default=20)
    batch.add_argument("--json", action="store_true")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "request":
        payload = progress_for_request(args.request_id, receipt_log=Path(args.receipt_log))
    elif args.command == "manifest":
        payload = progress_for_manifest(Path(args.manifest_path))
    elif args.command == "batch":
        payload = progress_for_batch(Path(args.manifest_path), include_items=bool(args.include_items), limit=int(args.limit))
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=None if getattr(args, "json", False) else 2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
