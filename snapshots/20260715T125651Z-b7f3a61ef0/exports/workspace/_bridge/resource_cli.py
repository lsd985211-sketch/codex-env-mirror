#!/usr/bin/env python3
"""Command-line wrapper for workspace resource acquisition."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.parse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from resource_fetcher import (
    ResourceIntent,
    ResourceRequest,
    ResourceResult,
    ResourceStage,
    acquire_resource_with_policy,
    append_resource_log,
    preview_url_resource,
    probe_url_resource,
    sha256_file,
)
from resource_strategy_review import (
    build_resource_strategy_review,
    format_resource_strategy_review,
    inferred_resource_fields,
    read_resource_log,
)
from resource_router import route_resource
from resource_broker import (
    DEFAULT_EVENT_LOG,
    DEFAULT_RECEIPT_LOG,
    ResourceBrokerRequest,
    attach_result_to_request,
    handle_request,
    mark_request_consumed,
    read_receipt,
    request_from_payload,
    stable_request_id,
)
from resource_scheduler import ResourceBatchConfig, batch_config_from_payload, batch_status_from_manifest, execute_batch, requests_from_payload
from resource_scenario_smoke import run_scenario_smoke, scenario_payload, validate as validate_scenario_smoke
from resource_validation_profile import VALIDATION_PROFILES
from resource_progress_view import progress_for_batch, progress_view, request_progress_from_receipt
from resource_cli_parser import ResourceCliParserConfig, build_resource_parser
from resource_cli_resource import command_get
from resource_collection_acquirer import collect_resources
from codex_resource_delegation import build_delegation, build_delegation_from_envelope
from resource_legacy_audit import audit as resource_legacy_audit, validate as validate_resource_legacy_audit
from resource_fast_materialize import materialize_url_fast
from resource_library_paths import default_artifact_dir
from structured_task_envelope import load_resource_envelope
from shared.resource_event_store import strategy_entries


BRIDGE_ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = BRIDGE_ROOT / "resources"
DEFAULT_LOG = BRIDGE_ROOT / "logs" / "resource-fetcher.jsonl"
DEFAULT_JOB_ROOT = BRIDGE_ROOT / "resources" / "_jobs"
END_TO_END_TERMINAL_STATUSES = {"failed", "blocked", "deferred"}
RESOURCE_LAYER_RECEIPT_STATUSES = END_TO_END_TERMINAL_STATUSES | {"completed", "handoff_required"}
RESOURCE_JOB_ACQUISITION_OWNER = "resource_layer"
RESOURCE_JOB_DUPLICATE_FETCH_POLICY = {
    "same_need": "do_not_start_direct_fetch_while_resource_layer_owns_request",
    "allowed_actions": [
        "wait_for_resource_receipt",
        "poll_resource_progress",
        "perform_explicit_handoff_action_for_same_request_id",
        "refine_resource_delegation_after_deferred_or_insufficient_result",
        "use_configured_owner_hub_online_route_chain_after_failed_or_blocked",
        "surface_resource_layer_blocker",
    ],
    "refine_on": ["deferred"],
    "route_chain_on": ["failed", "blocked"],
    "direct_generic_web_release_on": ["resource_layer_unavailable", "predefined_online_route_exhausted", "explicit_user_direct_web", "higher_precedence_platform_web_required"],
    "handoff_status": "handoff_required keeps same request_id ownership; continue through resource layer and attach owner/tool result instead of starting an independent replacement fetch",
}
RESOURCE_INTENT_CHOICES = (
    ResourceIntent.EXPLICIT_ATTACHMENT,
    ResourceIntent.EXPLICIT_LOCAL_FILE,
    ResourceIntent.EXPLICIT_USER_URL,
    ResourceIntent.INLINE_URL_CANDIDATE,
    ResourceIntent.EXTERNAL_DEPENDENCY,
    ResourceIntent.PACKAGE_DEPENDENCY,
    ResourceIntent.DOCUMENTATION_LOOKUP,
    ResourceIntent.GENERATED_OUTPUT,
    ResourceIntent.TOOL_OUTPUT,
    ResourceIntent.UNKNOWN,
)
RESOURCE_STAGE_CHOICES = (
    ResourceStage.DISCOVER,
    ResourceStage.PROBE,
    ResourceStage.PREVIEW,
    ResourceStage.MATERIALIZE,
    ResourceStage.AUDIT,
)


def parse_max_bytes(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    text = value.strip().lower()
    units = {
        "b": 1,
        "kb": 1024,
        "k": 1024,
        "mb": 1024 * 1024,
        "m": 1024 * 1024,
        "gb": 1024 * 1024 * 1024,
        "g": 1024 * 1024 * 1024,
    }
    for suffix, multiplier in sorted(units.items(), key=lambda item: len(item[0]), reverse=True):
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip()
            return int(float(number) * multiplier)
    return int(text)


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def hidden_creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def result_exit(result: ResourceResult, *, json_output: bool, log_path: Path | None) -> int:
    if log_path is not None:
        append_resource_log(log_path, result)
    payload = result.to_dict()
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif result.ok:
        print(f"ok stored_path={result.stored_path} sha256={result.sha256} size={result.size} cache_hit={result.cache_hit}")
    else:
        print(f"failed error={result.error} sha256={result.sha256} size={result.size}")
    return 0 if result.ok else 1


def policy_result_exit(result: ResourceResult, *, json_output: bool, log_path: Path | None, strict: bool) -> int:
    if log_path is not None:
        append_resource_log(log_path, result)
    payload = result.to_dict()
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif result.ok:
        print(
            "ok decision={decision} policy={policy} stored_path={stored_path} sha256={sha256} size={size} cache_hit={cache_hit}".format(
                decision=result.decision or "allowed",
                policy=result.policy_name or "-",
                stored_path=result.stored_path,
                sha256=result.sha256,
                size=result.size,
                cache_hit=result.cache_hit,
            )
        )
    else:
        print(
            "decision={decision} policy={policy} reason={reason} next_action={next_action} error={error}".format(
                decision=result.decision or "failed",
                policy=result.policy_name or "-",
                reason=result.policy_reason or "-",
                next_action=result.next_action or "-",
                error=result.error or "-",
            )
        )
    if result.ok:
        return 0
    if not strict and result.decision == "deferred":
        return 0
    return 1


def target_dir(args: argparse.Namespace) -> Path:
    explicit = str(getattr(args, "target_dir", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    user_delivery = str(getattr(args, "command", "") or "") in {"fetch-url", "materialize-url"}
    user_delivery = user_delivery or (
        str(getattr(args, "command", "") or "") == "acquire"
        and str(getattr(args, "intent", "") or "") == ResourceIntent.EXPLICIT_USER_URL
        and str(getattr(args, "stage", "") or "") == ResourceStage.MATERIALIZE
    )
    if user_delivery:
        return default_artifact_dir(
            name=str(getattr(args, "name", "") or ""),
            url=str(getattr(args, "url", "") or ""),
            path=str(getattr(args, "path", "") or ""),
            task=str(getattr(args, "task", "") or ""),
        ).expanduser().resolve()
    return DEFAULT_CACHE_DIR.expanduser().resolve()


def default_request_target_dir(*, name: str = "", url: str = "", path: str = "", task: str = "") -> str:
    return str(default_artifact_dir(name=name, url=url, path=path, task=task).expanduser().resolve())


def request_target_dir_arg(args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "target_dir", "") or "").strip()
    if explicit:
        return explicit
    user_delivery = (
        str(getattr(args, "intent", "") or "") == ResourceIntent.EXPLICIT_USER_URL
        and bool(getattr(args, "need_materialization", False))
        and bool(getattr(args, "allow_filesystem_write", False))
    )
    user_delivery = user_delivery or (
        not bool(getattr(args, "path", ""))
        and bool(getattr(args, "need_materialization", False))
        and bool(getattr(args, "allow_filesystem_write", False))
    )
    if user_delivery:
        return default_request_target_dir(
            name=str(getattr(args, "name", "") or ""),
            url=str(getattr(args, "url", "") or ""),
            path=str(getattr(args, "path", "") or ""),
            task=str(getattr(args, "task", "") or ""),
        )
    return str(DEFAULT_CACHE_DIR)


def legacy_policy_metadata(command: str, *, intent: str, resource_kind: str, stage: str, purpose: str = "") -> dict[str, Any]:
    return {
        "cli_command": command,
        "legacy_command": True,
        "declared_intent": intent,
        "resource_kind": resource_kind,
        "stage": stage,
        "purpose": purpose,
        "policy_hint": "prefer_acquire_intent_stage_for_new_workflows",
    }


def download_backend_metadata(args: argparse.Namespace) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    backend = str(getattr(args, "download_backend", "") or "").strip().lower()
    if backend:
        metadata["download_backend"] = backend
    if bool(getattr(args, "resume_download", False)):
        metadata["resume_download"] = True
    return metadata


def package_manager_metadata(args: argparse.Namespace) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    fields = {
        "package_ecosystem": "package_ecosystem",
        "package_action": "package_action",
        "windows_package_manager": "windows_package_manager",
        "package_id": "package_id",
        "winget_id": "winget_id",
        "verify_binary": "verify_binary",
    }
    for attr, key in fields.items():
        value = str(getattr(args, attr, "") or "").strip()
        if value:
            metadata[key] = value
    if bool(getattr(args, "install_approved", False)):
        metadata["install_approved"] = True
    if bool(getattr(args, "accept_winget_agreements", False)):
        metadata["accept_winget_agreements"] = True
    return metadata


def command_fetch_file(args: argparse.Namespace) -> int:
    source_path = Path(args.path)
    request = ResourceRequest(
        source=args.source,
        target_dir=target_dir(args),
        name=args.name or source_path.name,
        local_path=source_path,
        expected_sha256=args.sha256 or "",
        max_bytes=parse_max_bytes(args.max_bytes),
        metadata=legacy_policy_metadata(
            "fetch-file",
            intent=args.intent,
            resource_kind="local_file",
            stage=ResourceStage.MATERIALIZE,
            purpose=args.purpose or "",
        ),
    )
    result = acquire_resource_with_policy(request, intent=args.intent, stage=ResourceStage.MATERIALIZE)
    return policy_result_exit(result, json_output=args.json, log_path=args.log_path, strict=args.strict)


def command_fetch_url(args: argparse.Namespace) -> int:
    request = ResourceRequest(
        source=args.source,
        target_dir=target_dir(args),
        name=args.name or Path(args.url.split("?", 1)[0]).name or "download",
        url=args.url,
        expected_sha256=args.sha256 or "",
        max_bytes=parse_max_bytes(args.max_bytes),
        timeout_seconds=args.timeout,
        retries=args.retries,
        retry_delay_seconds=args.retry_delay,
        metadata={
            **legacy_policy_metadata(
            "fetch-url",
            intent=args.intent,
            resource_kind="url",
            stage=ResourceStage.MATERIALIZE,
            purpose=args.purpose or "",
            ),
            **download_backend_metadata(args),
        },
    )
    result = acquire_resource_with_policy(request, intent=args.intent, stage=ResourceStage.MATERIALIZE)
    return policy_result_exit(result, json_output=args.json, log_path=args.log_path, strict=args.strict)


def command_materialize_url(args: argparse.Namespace) -> int:
    payload = materialize_url_fast(
        url=args.url,
        task=args.task or args.purpose or "explicit URL materialization",
        name=args.name or "",
        target_dir=target_dir(args),
        store_root=Path(args.store_root).expanduser().resolve(),
        receipt_log=Path(args.receipt_log).expanduser().resolve(),
        resource_log=None if args.no_resource_log else Path(args.resource_log).expanduser().resolve(),
        expected_sha256=args.sha256 or "",
        max_bytes=parse_max_bytes(args.max_bytes),
        timeout_seconds=args.timeout,
        retries=args.retries,
        retry_delay_seconds=args.retry_delay,
        download_backend=args.download_backend or "",
        resume_download=bool(args.resume_download),
        validation_profile=args.validation_profile or "",
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        receipt = payload.get("receipt", {})
        print(f"request_id={payload.get('request_id', '')} status={payload.get('status', '')} mode=lightweight")
        print(f"artifact_path={receipt.get('artifact_path', '')}")
        print(f"next_action={payload.get('codex_next_action', '')}")
    return 0 if payload.get("ok") else 1


def command_probe_url(args: argparse.Namespace) -> int:
    request = ResourceRequest(
        source=args.source,
        target_dir=target_dir(args),
        name=args.name or Path(args.url.split("?", 1)[0]).name or "probe",
        url=args.url,
        max_bytes=parse_max_bytes(args.max_bytes),
        timeout_seconds=args.timeout,
        metadata={"cli_command": "probe-url", "purpose": args.purpose or ""},
    )
    return policy_result_exit(probe_url_resource(request), json_output=args.json, log_path=args.log_path, strict=args.strict)


def command_preview_url(args: argparse.Namespace) -> int:
    request = ResourceRequest(
        source=args.source,
        target_dir=target_dir(args),
        name=args.name or Path(args.url.split("?", 1)[0]).name or "preview",
        url=args.url,
        max_bytes=parse_max_bytes(args.max_bytes),
        timeout_seconds=args.timeout,
        metadata={"cli_command": "preview-url", "purpose": args.purpose or ""},
    )
    return policy_result_exit(
        preview_url_resource(request, preview_bytes=args.preview_bytes),
        json_output=args.json,
        log_path=args.log_path,
        strict=args.strict,
    )


def command_acquire(args: argparse.Namespace) -> int:
    if args.path and args.url:
        result = ResourceResult(
            ok=False,
            source=args.source,
            name=args.name or "resource",
            error="choose either --path or --url, not both",
            decision="blocked",
            policy_reason="ambiguous_resource_reference",
            intent=args.intent,
            resource_kind="unknown",
            risk_flags=("ambiguous_reference",),
        )
        return policy_result_exit(result, json_output=args.json, log_path=args.log_path, strict=args.strict)
    local_path = Path(args.path) if args.path else None
    url = args.url or ""
    if args.name:
        name = args.name
    elif local_path:
        name = local_path.name
    elif url:
        name = Path(url.split("?", 1)[0]).name or args.intent
    else:
        name = args.intent
    metadata = {
        "cli_command": "acquire",
        "declared_intent": args.intent,
        "purpose": args.purpose or "",
        **download_backend_metadata(args),
    }
    request = ResourceRequest(
        source=args.source,
        target_dir=target_dir(args),
        name=name,
        local_path=local_path,
        url=url,
        expected_sha256=args.sha256 or "",
        max_bytes=parse_max_bytes(args.max_bytes),
        timeout_seconds=args.timeout,
        retries=args.retries,
        retry_delay_seconds=args.retry_delay,
        metadata=metadata,
    )
    result = acquire_resource_with_policy(request, intent=args.intent, stage=args.stage)
    return policy_result_exit(result, json_output=args.json, log_path=args.log_path, strict=args.strict)


def command_verify(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        result = ResourceResult(ok=False, source=args.source, original_local_path=str(path), error="local file does not exist")
        return result_exit(result, json_output=args.json, log_path=args.log_path)
    size = path.stat().st_size
    max_bytes = parse_max_bytes(args.max_bytes)
    digest = sha256_file(path)
    error = ""
    if max_bytes is not None and size > max_bytes:
        error = f"resource larger than {max_bytes} bytes"
    elif args.sha256 and digest.lower() != args.sha256.lower():
        error = "sha256 mismatch"
    result = ResourceResult(
        ok=not error,
        source=args.source,
        local_path=str(path),
        stored_path=str(path),
        original_local_path=str(path),
        name=path.name,
        sha256=digest,
        size=size,
        error=error,
        metadata={"cli_command": "verify"},
    )
    return result_exit(result, json_output=args.json, log_path=args.log_path)


def command_strategy_review(args: argparse.Namespace) -> int:
    entries = strategy_entries(limit=args.limit)
    observation_source = "record_store.resource_requests"
    if not entries:
        entries = read_resource_log(Path(args.resource_log).expanduser().resolve(), limit=args.limit)
        observation_source = "legacy_resource_log_fallback"
    if args.hide_legacy:
        entries = [
            entry for entry in entries
            if not inferred_resource_fields(entry).get("intent", "").startswith("legacy_cli_")
        ]
    report = build_resource_strategy_review(entries)
    report["filters"] = {"hide_legacy": bool(args.hide_legacy)}
    report["observation_source"] = observation_source
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(format_resource_strategy_review(report))
    return 0


def classify_url_semantics(url: str, *, context: str = "unknown") -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query.lower()
    text = " ".join([host, path, query])
    extension = Path(path).suffix.lower()

    doc_hosts = (
        "docs.",
        "developer.",
        "devdocs.",
        "readthedocs.io",
        "learn.microsoft.com",
        "developer.mozilla.org",
        "docs.python.org",
    )
    package_hosts = (
        "pypi.org",
        "npmjs.com",
        "crates.io",
        "mvnrepository.com",
        "repo.maven.apache.org",
        "modrinth.com",
        "curseforge.com",
    )
    artifact_extensions = {
        ".zip",
        ".jar",
        ".tar",
        ".gz",
        ".tgz",
        ".7z",
        ".exe",
        ".msi",
        ".whl",
        ".gem",
        ".nupkg",
    }
    reasons: list[str] = []
    if context == "inline_text":
        intent = ResourceIntent.INLINE_URL_CANDIDATE
        stage = ResourceStage.PREVIEW
        reasons.append("url_came_from_inline_text")
    elif context == "documentation":
        intent = ResourceIntent.DOCUMENTATION_LOOKUP
        stage = ResourceStage.PROBE
        reasons.append("caller_declared_documentation_context")
    elif context == "dependency":
        intent = ResourceIntent.EXTERNAL_DEPENDENCY
        stage = ResourceStage.PROBE
        reasons.append("caller_declared_dependency_context")
    elif host.startswith(doc_hosts) or any(marker in text for marker in ("/docs/", "/documentation/", "/reference/", "/api/")):
        intent = ResourceIntent.DOCUMENTATION_LOOKUP
        stage = ResourceStage.PROBE
        reasons.append("documentation_url_pattern")
    elif any(package_host in host for package_host in package_hosts):
        intent = ResourceIntent.PACKAGE_DEPENDENCY
        stage = ResourceStage.PROBE
        reasons.append("package_registry_url_pattern")
    elif "github.com" in host and ("/releases/" in path or "/archive/" in path):
        intent = ResourceIntent.EXTERNAL_DEPENDENCY
        stage = ResourceStage.PROBE
        reasons.append("release_or_archive_url_pattern")
    elif extension in artifact_extensions:
        intent = ResourceIntent.EXPLICIT_USER_URL if context == "explicit_user" else ResourceIntent.EXTERNAL_DEPENDENCY
        stage = ResourceStage.PROBE
        reasons.append(f"artifact_extension:{extension}")
    elif context == "explicit_user":
        intent = ResourceIntent.EXPLICIT_USER_URL
        stage = ResourceStage.PROBE
        reasons.append("caller_declared_explicit_user_url")
    else:
        intent = ResourceIntent.INLINE_URL_CANDIDATE
        stage = ResourceStage.PREVIEW
        reasons.append("generic_url_defaults_to_non_materializing_preview")

    materialize_allowed_by_default = intent == ResourceIntent.EXPLICIT_USER_URL and context == "explicit_user"
    return {
        "ok": True,
        "read_only": True,
        "url": url,
        "context": context,
        "host": host,
        "path": parsed.path,
        "extension": extension,
        "recommended_intent": intent,
        "recommended_stage": stage,
        "materialize_allowed_by_default": materialize_allowed_by_default,
        "reasons": reasons,
        "suggested_commands": {
            "probe": f"python _bridge\\resource_cli.py acquire --intent {intent} --stage probe --url \"{url}\" --json",
            "preview": f"python _bridge\\resource_cli.py acquire --intent {intent} --stage preview --url \"{url}\" --json",
            "materialize": (
                f"python _bridge\\resource_cli.py acquire --intent explicit_user_url --stage materialize --url \"{url}\" --target-dir _bridge\\resources --json"
                if materialize_allowed_by_default
                else ""
            ),
        },
        "policy_note": "This command only classifies URL semantics; it never fetches, installs, clones, or writes files.",
    }


def command_classify_url(args: argparse.Namespace) -> int:
    payload = classify_url_semantics(args.url, context=args.context)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"url={payload['url']}")
        print(f"recommended_intent={payload['recommended_intent']}")
        print(f"recommended_stage={payload['recommended_stage']}")
        print(f"materialize_allowed_by_default={payload['materialize_allowed_by_default']}")
        print(f"reasons={', '.join(payload['reasons'])}")
    return 0


def command_route(args: argparse.Namespace) -> int:
    payload = route_resource(
        url=args.url or "",
        path=args.path or "",
        target=args.target or "",
        intent=args.intent,
        need_materialization=bool(args.need_materialization),
        task=args.task or "",
        name=args.name or "",
    ).to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"primary_tool={payload['primary_tool']}")
        print(f"recommended_stage={payload['recommended_stage']}")
        print(f"intent={payload['intent']}")
        print(f"need_materialization={payload['need_materialization']}")
        if payload.get("secondary_tools"):
            print(f"secondary_tools={', '.join(payload['secondary_tools'])}")
        if payload.get("resource_cli_command"):
            print(f"resource_cli_command={payload['resource_cli_command']}")
        if payload.get("reasons"):
            print(f"reasons={'; '.join(payload['reasons'])}")
    return 0 if payload.get("ok") else 1


def command_request(args: argparse.Namespace) -> int:
    if args.json_payload:
        request = request_from_payload(json.loads(args.json_payload))
    elif args.payload_file:
        request = request_from_payload(json.loads(Path(args.payload_file).read_text(encoding="utf-8")))
    else:
        request = ResourceBrokerRequest(
            target=args.target or "",
            url=args.url or "",
            path=args.path or "",
            task=args.task or "",
            name=args.name or "",
            intent=args.intent,
            need_materialization=bool(args.need_materialization),
            allow_network=bool(args.allow_network),
            allow_filesystem_write=bool(args.allow_filesystem_write),
            max_bytes=parse_max_bytes(args.max_bytes),
            expected_sha256=args.sha256 or "",
            timeout_seconds=args.timeout,
            retry_budget=args.retries,
            target_dir=request_target_dir_arg(args),
            auto_owner=bool(args.auto_owner),
            owner_execution_mode=args.owner_execution_mode,
            metadata={
                "cli_command": "request",
                "purpose": args.purpose or "",
                **({"validation_profile": args.validation_profile} if args.validation_profile else {}),
                **download_backend_metadata(args),
                **package_manager_metadata(args),
                **({"package_target_dir_explicit": True} if str(args.target_dir or "").strip() else {}),
            },
        )
    receipt = handle_request(
        request,
        event_log=Path(args.event_log).expanduser().resolve(),
        receipt_log=Path(args.receipt_log).expanduser().resolve(),
        resource_log=None if args.no_resource_log else Path(args.resource_log).expanduser().resolve(),
        store_root=Path(args.store_root).expanduser().resolve(),
    )
    payload = receipt.__dict__
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"request_id={receipt.request_id} status={receipt.status} ok={receipt.ok}")
        print(f"result_kind={receipt.result_kind} artifact_path={receipt.artifact_path}")
        print(f"next_action={receipt.next_action} error_class={receipt.error_class}")
        network_summary = getattr(receipt, "network_summary", {}) or {}
        if network_summary:
            print(
                "network="
                f"target_kind={network_summary.get('target_kind', '')} "
                f"route_mode={network_summary.get('route_mode', '')} "
                f"preferred={network_summary.get('preferred_route', '')}"
            )
        owner_execution = getattr(receipt, "owner_execution", {}) or {}
        if owner_execution:
            print(
                "owner_execution="
                f"tool={owner_execution.get('owner_tool', '')} "
                f"next_action={owner_execution.get('next_action', '')}"
            )
    return 0 if receipt.ok or receipt.status == "handoff_required" else 1


def command_delegate(args: argparse.Namespace) -> int:
    payload = build_job_delegation(args)
    if bool(getattr(args, "submit", False)):
        request = request_from_payload(payload.get("request", {}))
        receipt = handle_request(
            request,
            event_log=Path(args.event_log).expanduser().resolve(),
            receipt_log=Path(args.receipt_log).expanduser().resolve(),
            resource_log=None if args.no_resource_log else Path(args.resource_log).expanduser().resolve(),
            store_root=Path(args.store_root).expanduser().resolve(),
        )
        payload = {
            **payload,
            "submitted": True,
            "receipt": receipt.__dict__,
            "request_id": receipt.request_id,
            "status": receipt.status,
            "progress_command": f"python _bridge\\resource_cli.py progress --request-id {receipt.request_id} --json",
            "status_command": f"python _bridge\\resource_cli.py status --request-id {receipt.request_id} --json",
            "completion_contract": {
                "codex_waits_for_receipt": True,
                "completed": "consume receipt artifact/content/metadata",
                "handoff_required": "call owner tool then attach_result to the same request_id",
                "failed_or_blocked": "surface resource-layer blocker; do not pretend resource was acquired",
            },
        }
        payload["safety_boundaries"] = [
            "submitted_resource_task",
            "resource_layer_executes_until_receipt",
            "no_install_without_request_policy",
            "no_remote_write",
            "filesystem_write_only_when_request_allow_filesystem_write_true",
        ]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        request = payload.get("request", {})
        route = payload.get("route", {})
        print(f"intent={request.get('intent')}")
        print(f"primary_tool={route.get('primary_tool')}")
        print(f"owner_hint={request.get('metadata', {}).get('owner_route_hint', {}).get('source_owner_mcp', '')}")
        if payload.get("submitted"):
            print(f"request_id={payload.get('request_id', '')} status={payload.get('status', '')}")
            print(f"progress_command={payload.get('progress_command', '')}")
        else:
            print(f"submit_command={payload.get('submit_command', '')}")
    return 0 if payload.get("ok") else 1


def build_job_delegation(args: argparse.Namespace) -> dict[str, Any]:
    request_json = str(getattr(args, "request_json", "") or "").strip()
    request_file = str(getattr(args, "request_file", "") or "").strip()
    if request_json or request_file:
        try:
            return build_delegation_from_envelope(
                load_resource_envelope(request_json=request_json, request_file=request_file)
            )
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "schema": "codex_resource_delegation.v1",
                "ok": False,
                "error_class": "invalid_structured_request_input",
                "errors": [str(exc)],
            }
    if not str(getattr(args, "task", "") or "").strip():
        return {
            "schema": "codex_resource_delegation.v1",
            "ok": False,
            "error_class": "missing_legacy_task",
            "errors": ["task_is_required_without_request_json_or_request_file"],
        }
    resource_kind = str(getattr(args, "resource_kind", "") or "").strip()
    if resource_kind == "auto":
        resource_kind = ""
    return build_delegation(
        task=args.task,
        target=args.target or "",
        url=args.url or "",
        path=args.path or "",
        name=args.name or "",
        intent=args.intent,
        need_materialization=bool(args.need_materialization),
        allow_network=bool(args.allow_network),
        allow_filesystem_write=bool(args.allow_filesystem_write),
        max_bytes=args.max_bytes,
        expected_sha256=args.sha256 or "",
        timeout_seconds=args.timeout,
        retry_budget=args.retries,
        target_dir=request_target_dir_arg(args),
        target_dir_explicit=bool(str(args.target_dir or "").strip()),
        auto_owner=bool(args.auto_owner),
        owner_execution_mode=args.owner_execution_mode,
        purpose=args.purpose or "",
        validation_profile=args.validation_profile or "",
        runtime=args.runtime or "generic",
        download_backend=args.download_backend or "",
        resume_download=bool(args.resume_download),
        package_ecosystem=args.package_ecosystem or "",
        package_action=args.package_action or "",
        windows_package_manager=args.windows_package_manager or "",
        package_id=args.package_id or "",
        winget_id=args.winget_id or "",
        verify_binary=args.verify_binary or "",
        install_approved=bool(args.install_approved),
        accept_winget_agreements=bool(args.accept_winget_agreements),
        resource_kind=resource_kind,
        preferred_owner_tools=list(getattr(args, "owner_tool", []) or []),
        blocked_owner_tools=list(getattr(args, "avoid_owner_tool", []) or []),
        source_kind=getattr(args, "source_kind", "") or "",
        site_or_domain=getattr(args, "site_or_domain", "") or "",
        language=getattr(args, "language", "") or "",
        freshness=getattr(args, "freshness", "") or "",
        authority=getattr(args, "authority", "") or "",
        file_format=getattr(args, "file_format", "") or "",
        license_filter=getattr(args, "license_filter", "") or "",
        relevance_threshold=getattr(args, "relevance_threshold", None),
        required_source_count=getattr(args, "required_source_count", None),
        constraints=list(getattr(args, "constraint", []) or []),
        exclude=list(getattr(args, "exclude", []) or []),
        refine_from=getattr(args, "refine_from", "") or "",
        refine_reason=getattr(args, "refine_reason", "") or "",
        candidate_review=bool(getattr(args, "candidate_review", False)),
        quantity=getattr(args, "quantity", None),
        minimum_quantity=getattr(args, "minimum_quantity", None),
        maximum_quantity=getattr(args, "maximum_quantity", None),
        uniqueness_required=bool(getattr(args, "unique", False)),
        uniqueness_dimensions=list(getattr(args, "uniqueness_dimension", []) or []),
        deduplication_keys=list(getattr(args, "dedup_key", []) or []),
        source_mode=getattr(args, "source_mode", "") or "",
        source_domains=list(getattr(args, "source_domain", []) or []),
        freshness_mode=getattr(args, "freshness_mode", "") or "",
        max_age_days=getattr(args, "max_age_days", None),
        destination_policy=getattr(args, "destination_policy", "") or "",
    )


def job_command_contract(request_id: str) -> dict[str, str]:
    return {
        "status": f"python _bridge\\resource_cli.py job status --request-id {request_id} --json",
        "progress": f"python _bridge\\resource_cli.py job progress --request-id {request_id} --json",
        "wait": f"python _bridge\\resource_cli.py job wait --request-id {request_id} --json",
        "receipt": f"python _bridge\\resource_cli.py job receipt --request-id {request_id} --json",
        "attach": f"python _bridge\\resource_cli.py job attach --request-id {request_id} --source-tool <tool> --content-file <path> --json",
        "consume": f"python _bridge\\resource_cli.py job consume --request-id {request_id} --consumed-path <path> --json",
    }


def compact_receipt_payload(receipt: Any) -> dict[str, Any]:
    data = receipt if isinstance(receipt, dict) else getattr(receipt, "__dict__", {})
    attempts: list[dict[str, Any]] = []
    for attempt in data.get("attempts") or []:
        result = attempt.get("result") if isinstance(attempt, dict) and isinstance(attempt.get("result"), dict) else {}
        attempts.append(
            {
                "index": attempt.get("index"),
                "tool": attempt.get("tool"),
                "stage": attempt.get("stage"),
                "status": attempt.get("status"),
                "error_class": attempt.get("error_class") or result.get("error_class", ""),
                "reason": attempt.get("reason") or result.get("reason", ""),
                "next_action": attempt.get("next_action") or result.get("next_action", ""),
                "result_kind": result.get("result_kind", ""),
                "source": result.get("source", ""),
            }
        )
    route = data.get("route") if isinstance(data.get("route"), dict) else {}
    return {
        "request_id": data.get("request_id", ""),
        "ok": bool(data.get("ok")),
        "status": data.get("status", ""),
        "result_kind": data.get("result_kind", ""),
        "error_class": data.get("error_class", ""),
        "next_action": data.get("next_action", ""),
        "content_ref": data.get("content_ref", ""),
        "artifact_path": data.get("artifact_path", ""),
        "manifest_path": data.get("manifest_path", ""),
        "metadata_path": data.get("metadata_path", ""),
        "network_summary": data.get("network_summary", {}),
        "route": {
            "primary_tool": route.get("primary_tool", ""),
            "intent": route.get("intent", ""),
            "recommended_stage": route.get("recommended_stage", ""),
            "risk_flags": route.get("risk_flags", []),
        },
        "attempts": attempts,
    }


def receipt_payload_for_detail(receipt: Any, detail: str) -> dict[str, Any]:
    if str(detail or "full").lower() == "compact":
        return compact_receipt_payload(receipt)
    return receipt if isinstance(receipt, dict) else getattr(receipt, "__dict__", {})


def resource_job_ownership_contract(
    *,
    resource_layer_terminal: bool,
    end_to_end_terminal: bool,
    status: str,
    satisfaction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    satisfaction = satisfaction if isinstance(satisfaction, dict) else {}
    resource_need_satisfied = bool(satisfaction.get("satisfied")) if satisfaction else status == "completed"
    refine_required = status == "deferred"
    route_chain_allowed = status in {"failed", "blocked"}
    direct_generic_web_allowed = False
    same_need_fetch_allowed = False
    if status == "handoff_required":
        same_need_fetch_policy = "continue_resource_layer_handoff_or_attach_result"
    elif resource_need_satisfied:
        same_need_fetch_policy = "resource_satisfied"
    elif refine_required:
        same_need_fetch_policy = "refine_resource_delegation_and_retry"
    elif route_chain_allowed:
        same_need_fetch_policy = "use_configured_owner_hub_online_route_chain_before_any_direct_web"
    elif not end_to_end_terminal:
        same_need_fetch_policy = "wait_for_resource_layer_receipt"
    else:
        same_need_fetch_policy = "resource_layer_not_satisfied"
    return {
        "acquisition_owner": RESOURCE_JOB_ACQUISITION_OWNER,
        "owned_need_scope": "same_resource_or_external_lookup_need",
        "owned_until_terminal_receipt": not end_to_end_terminal,
        "terminal_status": resource_layer_terminal,
        "resource_layer_terminal": resource_layer_terminal,
        "end_to_end_terminal": end_to_end_terminal,
        "resource_need_satisfied": resource_need_satisfied,
        "same_need_fetch_allowed": same_need_fetch_allowed,
        "same_need_independent_direct_fetch_allowed": False,
        "direct_generic_web_allowed": direct_generic_web_allowed,
        "refine_resource_delegation_required": refine_required,
        "configured_online_route_chain_allowed": route_chain_allowed,
        "same_need_fetch_policy": same_need_fetch_policy,
        "status": status,
        "duplicate_fetch_policy": RESOURCE_JOB_DUPLICATE_FETCH_POLICY,
        "satisfaction": satisfaction,
    }


def job_status_payload(request_id: str, receipt_log: Path) -> dict[str, Any]:
    receipt = read_receipt(receipt_log.expanduser().resolve(), request_id)
    status = str(receipt.get("status") or "")
    exists = bool(receipt.get("request_id") == request_id and status)
    satisfaction = receipt.get("satisfaction") if isinstance(receipt.get("satisfaction"), dict) else {}
    resource_need_satisfied = bool(satisfaction.get("satisfied")) if satisfaction else status == "completed"
    effective_status = status or "submitted"
    progress = request_progress_from_receipt(receipt) if exists else {}
    resource_layer_terminal = bool(progress.get("resource_layer_terminal")) if exists else False
    end_to_end_terminal = bool(progress.get("end_to_end_terminal")) if exists else False
    ownership = resource_job_ownership_contract(
        resource_layer_terminal=resource_layer_terminal,
        end_to_end_terminal=end_to_end_terminal,
        status=effective_status,
        satisfaction=satisfaction,
    )
    return {
        "schema": "resource_job.status.v1",
        "ok": exists,
        "request_id": request_id,
        "status": effective_status,
        "acquisition_owner": RESOURCE_JOB_ACQUISITION_OWNER,
        "ownership": ownership,
        "duplicate_fetch_policy": RESOURCE_JOB_DUPLICATE_FETCH_POLICY,
        "receipt_found": exists,
        "resource_layer_terminal": resource_layer_terminal,
        "end_to_end_terminal": end_to_end_terminal,
        "resource_need_satisfied": resource_need_satisfied,
        "same_need_fetch_allowed": bool(ownership.get("same_need_fetch_allowed")),
        "same_need_independent_direct_fetch_allowed": bool(ownership.get("same_need_independent_direct_fetch_allowed")),
        "direct_generic_web_allowed": bool(ownership.get("direct_generic_web_allowed")),
        "refine_resource_delegation_required": bool(ownership.get("refine_resource_delegation_required")),
        "configured_online_route_chain_allowed": bool(ownership.get("configured_online_route_chain_allowed")),
        "next_action": receipt.get("next_action") or ("wait_for_receipt" if not exists else ""),
        "codex_next_action": progress.get("codex_next_action") or ("wait_for_receipt" if not exists else ""),
        "consume_required": bool(progress.get("consume_required")),
        "required_consume_paths": progress.get("required_consume_paths") or [],
        "consume_contract": progress.get("consume_contract") or {},
        "status_summary": progress.get("status_summary") or {},
        "progress": progress.get("progress") or {},
        "exception": progress.get("exception") or {},
        "receipt": receipt if exists else {},
        "commands": job_command_contract(request_id),
    }


def command_job_submit(args: argparse.Namespace) -> int:
    payload = build_job_delegation(args)
    if not payload.get("ok"):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    request = request_from_payload(payload.get("request", {}))
    request_id = stable_request_id(request)
    if args.job_command == "run" or args.foreground:
        receipt = handle_request(
            request,
            event_log=Path(args.event_log).expanduser().resolve(),
            receipt_log=Path(args.receipt_log).expanduser().resolve(),
            resource_log=None if args.no_resource_log else Path(args.resource_log).expanduser().resolve(),
            store_root=Path(args.store_root).expanduser().resolve(),
        )
        progress = request_progress_from_receipt(receipt.__dict__)
        resource_layer_terminal = bool(progress.get("resource_layer_terminal"))
        end_to_end_terminal = bool(progress.get("end_to_end_terminal"))
        ownership = resource_job_ownership_contract(
            resource_layer_terminal=resource_layer_terminal,
            end_to_end_terminal=end_to_end_terminal,
            status=receipt.status,
            satisfaction=receipt.satisfaction,
        )
        result = {
            "schema": "resource_job.run.v1" if args.job_command == "run" else "resource_job.submit.v1",
            "ok": receipt.ok or receipt.status == "handoff_required",
            "mode": "blocking" if args.job_command == "run" else "foreground",
            "request_id": receipt.request_id,
            "status": receipt.status,
            "acquisition_owner": RESOURCE_JOB_ACQUISITION_OWNER,
            "ownership": ownership,
            "duplicate_fetch_policy": RESOURCE_JOB_DUPLICATE_FETCH_POLICY,
            "resource_layer_terminal": resource_layer_terminal,
            "end_to_end_terminal": end_to_end_terminal,
            "resource_need_satisfied": bool(receipt.satisfaction.get("satisfied")) if receipt.satisfaction else receipt.status == "completed",
            "same_need_fetch_allowed": bool(ownership.get("same_need_fetch_allowed")),
            "same_need_independent_direct_fetch_allowed": bool(ownership.get("same_need_independent_direct_fetch_allowed")),
            "direct_generic_web_allowed": bool(ownership.get("direct_generic_web_allowed")),
            "refine_resource_delegation_required": bool(ownership.get("refine_resource_delegation_required")),
            "configured_online_route_chain_allowed": bool(ownership.get("configured_online_route_chain_allowed")),
            "next_action": receipt.next_action,
            "codex_next_action": progress.get("codex_next_action", receipt.next_action),
            "consume_required": bool(progress.get("consume_required")),
            "required_consume_paths": progress.get("required_consume_paths") or [],
            "consume_contract": progress.get("consume_contract") or {},
            "status_summary": progress.get("status_summary", {}),
            "progress": progress.get("progress", {}),
            "exception": progress.get("exception", {}),
            "receipt_detail": str(getattr(args, "receipt_detail", "full") or "full"),
            "receipt": receipt_payload_for_detail(receipt, str(getattr(args, "receipt_detail", "full") or "full")),
            "commands": job_command_contract(receipt.request_id),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1

    job_dir = DEFAULT_JOB_ROOT / request_id
    job_dir.mkdir(parents=True, exist_ok=True)
    payload_path = job_dir / "request.json"
    stdout_path = job_dir / "stdout.json"
    stderr_path = job_dir / "stderr.log"
    state_path = job_dir / "job.json"
    payload_path.write_text(json.dumps(payload.get("request", {}), ensure_ascii=False, sort_keys=True), encoding="utf-8")
    cmd = [
        os.sys.executable,
        str(Path(__file__).resolve()),
        "request",
        "--payload-file",
        str(payload_path),
        "--event-log",
        str(Path(args.event_log).expanduser().resolve()),
        "--receipt-log",
        str(Path(args.receipt_log).expanduser().resolve()),
        "--store-root",
        str(Path(args.store_root).expanduser().resolve()),
        "--json",
    ]
    if args.no_resource_log:
        cmd.append("--no-resource-log")
    else:
        cmd.extend(["--resource-log", str(Path(args.resource_log).expanduser().resolve())])
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            cmd,
            cwd=str(BRIDGE_ROOT.parent),
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            creationflags=hidden_creationflags(),
        )
    state = {
        "schema": "resource_job.state.v1",
        "request_id": request_id,
        "submitted_at": now_stamp(),
        "acquisition_owner": RESOURCE_JOB_ACQUISITION_OWNER,
        "ownership": resource_job_ownership_contract(
            resource_layer_terminal=False,
            end_to_end_terminal=False,
            status="submitted",
        ),
        "pid": process.pid,
        "payload_path": str(payload_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "receipt_log": str(Path(args.receipt_log).expanduser().resolve()),
        "event_log": str(Path(args.event_log).expanduser().resolve()),
        "commands": job_command_contract(request_id),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    result = {
        "schema": "resource_job.submit.v1",
        "ok": True,
        "mode": "background",
        "request_id": request_id,
        "status": "submitted",
        "pid": process.pid,
        "acquisition_owner": RESOURCE_JOB_ACQUISITION_OWNER,
        "ownership": resource_job_ownership_contract(
            resource_layer_terminal=False,
            end_to_end_terminal=False,
            status="submitted",
        ),
        "duplicate_fetch_policy": RESOURCE_JOB_DUPLICATE_FETCH_POLICY,
        "resource_layer_terminal": False,
        "job_state_path": str(state_path),
        "commands": job_command_contract(request_id),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_job_wait(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + max(0.0, float(args.timeout))
    interval = max(0.1, float(args.interval))
    payload = job_status_payload(args.request_id, Path(args.receipt_log))
    while not payload.get("resource_layer_terminal") and time.monotonic() < deadline:
        time.sleep(interval)
        payload = job_status_payload(args.request_id, Path(args.receipt_log))
    payload = {
        **payload,
        "schema": "resource_job.wait.v1",
        "wait_timeout_seconds": float(args.timeout),
        "timed_out": not bool(payload.get("resource_layer_terminal")),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("resource_layer_terminal") else 1


def command_job_consume(args: argparse.Namespace) -> int:
    updated = mark_request_consumed(
        request_id=args.request_id,
        consumed_path=args.consumed_path or "",
        no_read_needed_reason=args.no_read_needed_reason or "",
        consumer=args.consumer or "codex",
        receipt_log=Path(args.receipt_log).expanduser().resolve(),
    )
    if not updated.get("consumption"):
        payload = {"schema": "resource_job.consume.v1", **updated}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    payload = {
        "schema": "resource_job.consume.v1",
        **job_status_payload(args.request_id, Path(args.receipt_log)),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("end_to_end_terminal") and not payload.get("consume_required") else 1


def command_job(args: argparse.Namespace) -> int:
    if args.job_command in {"submit", "run"}:
        return command_job_submit(args)
    if args.job_command == "wait":
        return command_job_wait(args)
    if args.job_command in {"status", "receipt"}:
        payload = job_status_payload(args.request_id, Path(args.receipt_log))
        if args.job_command == "receipt":
            payload = {"schema": "resource_job.receipt.v1", **payload}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("ok") else 1
    if args.job_command == "progress":
        payload = progress_view(request_id=args.request_id, receipt_log=Path(args.receipt_log).expanduser().resolve())
        payload = {"job_schema": "resource_job.progress.v1", **payload}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("ok") else 1
    if args.job_command == "consume":
        return command_job_consume(args)
    if args.job_command == "attach":
        return command_attach_result(args)
    return 1


def command_custom(args: argparse.Namespace) -> int:
    payload = build_job_delegation(args)
    if not payload.get("ok"):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    request = payload.get("request", {})
    metadata = request.get("metadata", {}) if isinstance(request, dict) else {}
    envelope = metadata.get("task_envelope", {}) if isinstance(metadata, dict) else {}
    resource = envelope.get("resource", {}) if isinstance(envelope, dict) else {}
    quantity = resource.get("quantity", {}) if isinstance(resource, dict) else {}
    materialization = resource.get("materialization", {}) if isinstance(resource, dict) else {}
    requested_count = int(quantity.get("requested") or 0) if isinstance(quantity, dict) else 0
    collection_action = str(envelope.get("action") or "") in {"discover_and_download", "download", "materialize"}
    if str(getattr(args, "mode", "run") or "run") == "run" and requested_count > 1 and bool(materialization.get("required")) and collection_action:
        uniqueness = resource.get("uniqueness", {}) if isinstance(resource, dict) else {}
        source_policy = resource.get("source_policy", {}) if isinstance(resource, dict) else {}
        freshness = resource.get("freshness", {}) if isinstance(resource, dict) else {}
        result = collect_resources(
            task=str(envelope.get("summary") or request.get("task") or "resource collection"),
            target=str(envelope.get("target") or request.get("target") or request.get("task") or "resource"),
            count=requested_count,
            resource_kind=str(resource.get("kind") or "auto"),
            source_page=str(envelope.get("url") or ""),
            target_dir=str(request.get("target_dir") or materialization.get("target_dir") or ""),
            candidate_limit=max(24, requested_count * 3),
            batch_size=min(6, requested_count),
            timeout=int(request.get("timeout_seconds") or 30),
            retries=int(request.get("retry_budget") or 1),
            max_bytes=request.get("max_bytes"),
            download_backend=str(metadata.get("download_backend") or ""),
            resume_download=bool(metadata.get("resume_download")),
            uniqueness_required=bool(uniqueness.get("required")),
            deduplication_keys=list(uniqueness.get("deduplication_keys") or []),
            source_mode=str(source_policy.get("mode") or ""),
            source_domains=list(source_policy.get("domains") or []),
            authority=str(source_policy.get("authority") or ""),
            freshness_mode=str(freshness.get("mode") or ""),
            max_age_days=freshness.get("max_age_days"),
            event_log=Path(args.event_log).expanduser().resolve(),
            receipt_log=Path(args.receipt_log).expanduser().resolve(),
            resource_log=None if args.no_resource_log else Path(args.resource_log).expanduser().resolve(),
            store_root=Path(args.store_root).expanduser().resolve(),
        )
        output = {
            "schema": "resource_custom.collection.v1",
            "ok": bool(result.get("ok")),
            "mode": "blocking_collection",
            "task_envelope": envelope,
            "structured_fields_applied": result.get("structured_execution", {}),
            "collection": result,
            "next_action": result.get("next_action", ""),
        }
        print(json.dumps(output, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if output["ok"] else 1
    if str(getattr(args, "mode", "run") or "run") == "submit":
        setattr(args, "job_command", "submit")
        setattr(args, "foreground", False)
    else:
        setattr(args, "job_command", "run")
        setattr(args, "foreground", True)
    return command_job_submit(args)


def command_request_batch(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    requests = requests_from_payload(payload)
    if args.validation_profile:
        requests = [
            replace(request, metadata={**(request.metadata or {}), "validation_profile": args.validation_profile})
            for request in requests
        ]
    payload_config = batch_config_from_payload(payload, plan_only=bool(args.plan_only))
    execution = payload.get("execution") if isinstance(payload, dict) and isinstance(payload.get("execution"), dict) else {}
    batch = execute_batch(
        requests,
        config=ResourceBatchConfig(
            max_active=int(execution.get("max_active") or args.max_active or payload_config.max_active),
            per_host_limit=int(execution.get("per_host_limit") or args.per_host_limit or payload_config.per_host_limit),
            plan_only=payload_config.plan_only,
            fail_fast=payload_config.fail_fast,
            total_budget_seconds=float(
                execution.get("total_budget_seconds")
                or execution.get("total_timeout_seconds")
                or args.total_timeout_seconds
                or payload_config.total_budget_seconds
            ),
        ),
        event_log=Path(args.event_log).expanduser().resolve(),
        receipt_log=Path(args.receipt_log).expanduser().resolve(),
        resource_log=None if args.no_resource_log else Path(args.resource_log).expanduser().resolve(),
        store_root=Path(args.store_root).expanduser().resolve(),
    )
    detail = str(getattr(args, "detail", "compact") or "compact").lower()
    output = batch
    if detail == "compact":
        output = progress_for_batch(Path(str(batch.get("manifest_path") or "")), include_items=True, limit=50)
        output = {
            **output,
            "receipt_detail": "compact",
            "full_manifest_path": str(batch.get("manifest_path") or ""),
            "batch_ok": bool(batch.get("ok")),
            "network_batch": batch.get("network_batch", {}),
        }
    if args.json:
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if batch.get("ok") else 1


def command_collect(args: argparse.Namespace) -> int:
    payload = collect_resources(
        task=args.task,
        target=args.target or args.task,
        count=args.count,
        resource_kind=args.resource_kind,
        source_page=args.source_page or "",
        target_dir=args.target_dir or "",
        candidate_limit=args.candidate_limit,
        batch_size=args.batch_size,
        max_active=args.max_active,
        per_host_limit=args.per_host_limit,
        timeout=args.timeout,
        retries=args.retries,
        max_bytes=args.max_bytes,
        download_backend=args.download_backend or "",
        resume_download=bool(args.resume_download),
        event_log=Path(args.event_log).expanduser().resolve(),
        receipt_log=Path(args.receipt_log).expanduser().resolve(),
        resource_log=None if args.no_resource_log else Path(args.resource_log).expanduser().resolve(),
        store_root=Path(args.store_root).expanduser().resolve(),
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"status={payload.get('status', '')} completed={payload.get('completed_count', 0)}/{payload.get('requested_count', 0)}")
        print(f"target_dir={payload.get('target_dir', '')}")
        for item in payload.get("artifacts") or []:
            print(f"artifact={item.get('artifact_path', '')}")
        if payload.get("failed_candidates"):
            print(f"failed_candidates={len(payload.get('failed_candidates') or [])}")
    return 0 if payload.get("ok") else 1


def command_batch_status(args: argparse.Namespace) -> int:
    payload = batch_status_from_manifest(Path(args.manifest_path).expanduser().resolve())
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


def command_progress(args: argparse.Namespace) -> int:
    payload = progress_view(
        request_id=args.request_id or "",
        manifest_path=args.manifest_path or "",
        batch_manifest_path=args.batch_manifest_path or "",
        include_items=bool(args.include_items),
        limit=int(args.limit),
        receipt_log=Path(args.receipt_log).expanduser().resolve(),
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


def command_scenario_smoke(args: argparse.Namespace) -> int:
    if args.print_payload:
        payload = scenario_payload(args.mode)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.validate_only:
        payload = validate_scenario_smoke()
    else:
        payload = run_scenario_smoke(
            mode=args.mode,
            max_active=args.max_active,
            per_host_limit=args.per_host_limit,
            tmp_root=Path(args.tmp_root).expanduser().resolve(),
        )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


def command_status(args: argparse.Namespace) -> int:
    payload = read_receipt(Path(args.receipt_log).expanduser().resolve(), args.request_id)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("request_id") == args.request_id and payload.get("status") else 1


def command_attach_result(args: argparse.Namespace) -> int:
    content = args.content or ""
    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8")
    metadata = json.loads(args.metadata_json) if args.metadata_json else {}
    payload = attach_result_to_request(
        request_id=args.request_id,
        source_tool=args.source_tool,
        result_kind=args.result_kind,
        content=content,
        artifact_path=args.artifact_path or "",
        metadata=metadata,
        receipt_log=Path(args.receipt_log).expanduser().resolve(),
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


def file_entry(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "relative_path": str(path.relative_to(root)),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "mtime_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime)),
        "sha256": sha256_file(path) if path.is_file() else "",
    }


def command_inspect_cache(args: argparse.Namespace) -> int:
    root = target_dir(args)
    files = []
    total_size = 0
    if root.exists():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            entry = file_entry(path, root)
            files.append(entry)
            total_size += entry["size"]
    limit = max(1, int(args.limit or 50))
    returned = files[:limit]
    payload = {
        "ok": True,
        "cache_dir": str(root),
        "count": len(files),
        "total_size": total_size,
        "returned_count": len(returned),
        "truncated": len(files) > len(returned),
        "files": returned,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"cache_dir={root} count={len(files)} total_size={total_size}")
        for entry in files[: args.limit]:
            print(f"{entry['size']:>10} {entry['sha256'][:16]} {entry['relative_path']}")
        if len(files) > args.limit:
            print(f"... {len(files) - args.limit} more")
    return 0


def command_clean_cache(args: argparse.Namespace) -> int:
    root = target_dir(args)
    cutoff = time.time() - (args.older_than_days * 86400)
    candidates = []
    if root.exists():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.stat().st_mtime >= cutoff:
                continue
            if bool(getattr(args, "transient_only", False)) and path.suffix.lower() not in {".part", ".partial", ".tmp", ".crdownload"}:
                continue
            candidates.append(file_entry(path, root))
    removed = []
    if not args.dry_run:
        for entry in candidates:
            path = Path(entry["path"])
            try:
                path.unlink()
                removed.append(entry)
            except OSError as exc:
                entry["error"] = str(exc)
    limit = max(1, int(getattr(args, "limit", 100) or 100))
    payload = {
        "ok": True,
        "cache_dir": str(root),
        "dry_run": args.dry_run,
        "transient_only": bool(getattr(args, "transient_only", False)),
        "candidate_count": len(candidates),
        "removed_count": len(removed),
        "candidate_bytes": sum(int(item.get("size") or 0) for item in candidates),
        "removed_bytes": sum(int(item.get("size") or 0) for item in removed),
        "returned_candidate_count": min(len(candidates), limit),
        "returned_removed_count": min(len(removed), limit),
        "truncated": len(candidates) > limit or len(removed) > limit,
        "candidates": candidates[:limit],
        "removed": removed[:limit],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        action = "would_remove" if args.dry_run else "removed"
        print(f"cache_dir={root} {action}={len(candidates) if args.dry_run else len(removed)}")
    return 0


def command_legacy_audit(args: argparse.Namespace) -> int:
    payload = validate_resource_legacy_audit() if args.validate_only else resource_legacy_audit()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    return build_resource_parser(
        ResourceCliParserConfig(
            bridge_root=BRIDGE_ROOT,
            default_cache_dir=DEFAULT_CACHE_DIR,
            default_log=DEFAULT_LOG,
            default_event_log=DEFAULT_EVENT_LOG,
            default_receipt_log=DEFAULT_RECEIPT_LOG,
            resource_intent_choices=RESOURCE_INTENT_CHOICES,
            resource_stage_choices=RESOURCE_STAGE_CHOICES,
            validation_profiles=VALIDATION_PROFILES,
            intent_unknown=ResourceIntent.UNKNOWN,
            intent_explicit_local_file=ResourceIntent.EXPLICIT_LOCAL_FILE,
            intent_explicit_user_url=ResourceIntent.EXPLICIT_USER_URL,
            stage_materialize=ResourceStage.MATERIALIZE,
            command_fetch_file=command_fetch_file,
            command_fetch_url=command_fetch_url,
            command_materialize_url=command_materialize_url,
            command_probe_url=command_probe_url,
            command_preview_url=command_preview_url,
            command_acquire=command_acquire,
            command_verify=command_verify,
            command_strategy_review=command_strategy_review,
            command_classify_url=command_classify_url,
            command_route=command_route,
            command_request=command_request,
            command_delegate=command_delegate,
            command_get=command_get,
            command_custom=command_custom,
            command_collect=command_collect,
            command_job=command_job,
            command_request_batch=command_request_batch,
            command_batch_status=command_batch_status,
            command_progress=command_progress,
            command_scenario_smoke=command_scenario_smoke,
            command_status=command_status,
            command_attach_result=command_attach_result,
            command_inspect_cache=command_inspect_cache,
            command_clean_cache=command_clean_cache,
            command_legacy_audit=command_legacy_audit,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.no_log:
        args.log_path = None
    elif args.log_path is not None:
        args.log_path = Path(args.log_path).expanduser().resolve()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
