#!/usr/bin/env python3
"""Build machine-readable resource delegations from Codex task intent.

Ownership: Codex-side resource delegation payload construction.
Non-goals: fetch resources, call MCP tools, install packages, write remote state,
or decide final analysis quality.
State behavior: read-only; emits ResourceBrokerRequest-compatible JSON.
Caller context: Codex workflow, resource_cli delegate facade, and validators.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

from intent_routing import matched_terms
from intent_resource_router import build_route as build_intent_route
from resource_broker import ResourceBrokerRequest
from resource_fetcher import ResourceIntent
from resource_library_paths import default_artifact_dir
from resource_owner_tool_registry import SUPPORTED_DELEGATION_OWNER_TOOLS
from resource_router import route_resource
from shared.json_cli import configure_utf8_stdio, now_iso, print_json
from structured_task_envelope import build_legacy_resource_envelope, resource_task_facts


configure_utf8_stdio()


NETWORK_INTENTS = {
    "external_research",
    "official_docs",
    "github_remote",
    "package_or_library_docs",
    "runtime_page_evidence",
}

SUPPORTED_OWNER_TOOLS = SUPPORTED_DELEGATION_OWNER_TOOLS


ACADEMIC_PAPER_TERMS = (
    "arxiv",
    "doi",
    "学术",
    "开放获取",
    "期刊",
    "会议",
    "论文",
)
ACADEMIC_PAPER_WORD_TERMS = ("academic", "conference", "journal", "paper", "proceedings", "scholar")
ACADEMIC_PAPER_PHRASE_TERMS = ("open access",)
IMAGE_TERMS = ("图片", "照片", "图像", "配图", "截图", "壁纸", "海报", "photo", "image", "images", "picture", "wallpaper", "screenshot")
DATASET_TERMS = ("数据集", "训练数据", "样本数据", "模型数据", "dataset", "datasets", "data set", "training data", "csv", "parquet", "jsonl")


def _as_bool(value: Any) -> bool:
    return bool(value) and str(value).lower() not in {"0", "false", "no", "none"}


def _compact_list(values: Any) -> list[str]:
    if values is None:
        return []
    raw_items = values if isinstance(values, list | tuple | set) else [values]
    items: list[str] = []
    for raw in raw_items:
        for item in str(raw or "").split(","):
            text = item.strip()
            if text and text not in items:
                items.append(text)
    return items


def _compact_mapping(values: Any) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in _compact_list(values):
        if "=" not in item:
            mapping[item] = "true"
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key:
            mapping[key] = value.strip()
    return mapping


def _owner_list(values: Any) -> list[str]:
    return [item for item in _compact_list(values) if item in SUPPORTED_OWNER_TOOLS]


def has_academic_paper_signal(text: str) -> bool:
    return bool(matched_terms(text, ACADEMIC_PAPER_TERMS + ACADEMIC_PAPER_PHRASE_TERMS + ACADEMIC_PAPER_WORD_TERMS))


def has_image_signal(text: str) -> bool:
    return bool(matched_terms(text, IMAGE_TERMS))


def has_dataset_signal(text: str) -> bool:
    return bool(matched_terms(text, DATASET_TERMS))


def infer_intent(*, target: str, url: str, path: str, task: str, requested_intent: str) -> str:
    if requested_intent and requested_intent != ResourceIntent.UNKNOWN:
        return requested_intent
    text = " ".join([target, url, path, task]).lower()
    if path:
        return ResourceIntent.EXPLICIT_LOCAL_FILE
    if url:
        return ResourceIntent.EXPLICIT_USER_URL
    if has_academic_paper_signal(text):
        return ResourceIntent.EXTERNAL_DEPENDENCY
    if matched_terms(text, ("browser", "chrome", "playwright", "screenshot", "dom", "rendered", "浏览器", "页面", "截图")):
        return ResourceIntent.TOOL_OUTPUT
    if matched_terms(text, ("install", "package", "dependency", "pip", "npm", "pnpm", "uv", "uvx", "winget", "choco", "chocolatey", "依赖", "安装")):
        return ResourceIntent.PACKAGE_DEPENDENCY
    if matched_terms(text, ("docs", "documentation", "api", "sdk", "framework", "library", "文档", "接口", "库", "框架", "官方")):
        return ResourceIntent.DOCUMENTATION_LOOKUP
    if matched_terms(text, ("github", "repo", "repository", "issue", "release", "仓库")):
        return ResourceIntent.EXTERNAL_DEPENDENCY
    if matched_terms(text, ("联网", "搜索", "查资料", "research", "web search", "look up")):
        return ResourceIntent.DOCUMENTATION_LOOKUP
    return ResourceIntent.UNKNOWN


def owner_route_for_primary(owner_routes: list[Any], primary_tool: str) -> dict[str, Any]:
    for item in owner_routes:
        if not isinstance(item, dict):
            continue
        owner_mcp = str(item.get("owner_mcp") or "")
        if owner_mcp == primary_tool or primary_tool in owner_mcp.split("|"):
            return item
    for item in owner_routes:
        if isinstance(item, dict) and item.get("owner_mcp") == "resource_layer_owner_selector":
            return item
    return {}


def explicit_url_fast_materialize_allowed(
    *,
    url: str,
    path: str,
    resolved_intent: str,
    need_materialization: bool,
    allow_filesystem_write: bool,
) -> bool:
    return (
        bool(url)
        and not bool(path)
        and resolved_intent == ResourceIntent.EXPLICIT_USER_URL
        and bool(need_materialization)
        and bool(allow_filesystem_write)
    )


def build_delegation(
    *,
    task: str,
    target: str = "",
    url: str = "",
    path: str = "",
    name: str = "",
    intent: str = ResourceIntent.UNKNOWN,
    need_materialization: bool = False,
    allow_network: bool = True,
    allow_filesystem_write: bool = False,
    max_bytes: int | None = None,
    expected_sha256: str = "",
    timeout_seconds: int = 30,
    retry_budget: int = 1,
    target_dir: str = "",
    target_dir_explicit: bool = False,
    auto_owner: bool = True,
    owner_execution_mode: str = "read_only",
    purpose: str = "",
    validation_profile: str = "",
    runtime: str = "generic",
    download_backend: str = "",
    resume_download: bool = False,
    package_ecosystem: str = "",
    package_action: str = "",
    windows_package_manager: str = "",
    package_id: str = "",
    winget_id: str = "",
    verify_binary: str = "",
    install_approved: bool = False,
    accept_winget_agreements: bool = False,
    resource_kind: str = "",
    preferred_owner_tools: list[str] | None = None,
    blocked_owner_tools: list[str] | None = None,
    source_kind: str = "",
    site_or_domain: str = "",
    language: str = "",
    freshness: str = "",
    authority: str = "",
    file_format: str = "",
    license_filter: str = "",
    relevance_threshold: float | None = None,
    required_source_count: int | None = None,
    constraints: list[str] | None = None,
    exclude: list[str] | None = None,
    refine_from: str = "",
    refine_reason: str = "",
    candidate_review: bool = False,
    quantity: int | None = None,
    minimum_quantity: int | None = None,
    maximum_quantity: int | None = None,
    uniqueness_required: bool = False,
    uniqueness_dimensions: list[str] | None = None,
    deduplication_keys: list[str] | None = None,
    source_mode: str = "",
    source_domains: list[str] | None = None,
    freshness_mode: str = "",
    max_age_days: int | None = None,
    destination_policy: str = "",
    task_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a ResourceBrokerRequest payload plus routing evidence."""

    resolved_intent = infer_intent(target=target, url=url, path=path, task=task, requested_intent=intent)
    route = route_resource(
        url=url,
        path=path,
        target=target,
        intent=resolved_intent,
        need_materialization=need_materialization,
        task=task,
        name=name,
    )
    intent_route = build_intent_route(" ".join(part for part in (task, target, url, path, name) if part))
    detected_intents = [
        str(item.get("key") or "")
        for item in intent_route.get("intents", [])
        if isinstance(item, dict)
    ]
    network_needed = bool(allow_network and (url or target or any(item in NETWORK_INTENTS for item in detected_intents)))
    resource_kind_hint = str(resource_kind or "").strip()
    resource_text = " ".join(part for part in (task, target, url, path, name) if part)
    if not resource_kind_hint:
        if has_academic_paper_signal(resource_text):
            resource_kind_hint = "academic_paper"
        elif has_image_signal(resource_text):
            resource_kind_hint = "image"
        elif has_dataset_signal(resource_text):
            resource_kind_hint = "dataset"
    owner_routes = intent_route.get("owner_routes", []) if isinstance(intent_route.get("owner_routes"), list) else []
    owner_route = owner_route_for_primary(owner_routes, route.primary_tool)
    resource_layer_contract = intent_route.get("resource_layer_contract", {})
    if not isinstance(resource_layer_contract, dict):
        resource_layer_contract = {}
    candidate_review_before_materialization = bool(
        resource_layer_contract.get("candidate_review_before_materialization")
        and need_materialization
        and not url
        and not path
    )
    metadata = {
        "schema": "codex_resource_delegation.metadata.v1",
        "created_by": "codex",
        "created_at": now_iso(),
        "purpose": purpose or task,
        "validation_profile": validation_profile,
        "runtime": runtime,
        "codex_expectation": {
            "resource_layer_should_execute": True,
            "codex_provided_machine_readable_delegation": True,
            "resource_layer_should_not_guess_primary_intent": True,
            "task_class": resource_layer_contract.get("task_class"),
            "codex_url_discovery_allowed": bool(resource_layer_contract.get("codex_url_discovery_allowed")),
            "resource_layer_source_selection_required": bool(
                resource_layer_contract.get("resource_layer_source_selection_required")
            ),
            "resource_layer_source_discovery_required": bool(
                resource_layer_contract.get("resource_layer_source_discovery_required")
            ),
            "source_discovery_owner": resource_layer_contract.get("source_discovery_owner", ""),
            "source_discovery_scope": resource_layer_contract.get("source_discovery_scope", []),
            "candidate_review_before_materialization": bool(
                resource_layer_contract.get("candidate_review_before_materialization")
            ),
            "candidate_review_owner": resource_layer_contract.get("candidate_review_owner", ""),
            "candidate_review_policy": resource_layer_contract.get("candidate_review_policy", {}),
            "direct_resource_delegation_preferred": bool(resource_layer_contract.get("direct_resource_delegation_preferred")),
            "materialization_requires_resource_layer": bool(resource_layer_contract.get("materialization_requires_resource_layer")),
            "install_requires_resource_layer": bool(resource_layer_contract.get("install_requires_resource_layer")),
            "codex_direct_acquisition_allowed_only_with": resource_layer_contract.get(
                "codex_direct_acquisition_allowed_only_with", []
            ),
            "unsuitable_result_policy": resource_layer_contract.get("unsuitable_result_policy", {}),
            "result_iteration_policy": resource_layer_contract.get("result_iteration_policy", {}),
            "generic_web_requires_fallback_reason": bool(
                intent_route.get("generic_web_gate", {}).get("requires_fallback_reason_if_used")
            ),
        },
        "owner_route_hint": {
            "primary_tool": route.primary_tool,
            "secondary_tools": list(route.secondary_tools),
            "recommended_stage": route.recommended_stage,
            "source_owner_mcp": route.primary_tool,
            "fallback_allowed_only_with": owner_route.get("fallback_allowed_only_with", []),
        },
        "network_needed": network_needed,
        "resource_kind_hint": resource_kind_hint,
        "intent_resource_route": {
            "owner_routes": owner_routes,
            "generic_web_gate": intent_route.get("generic_web_gate", {}),
            "resource_layer_contract": resource_layer_contract,
            "evidence_required": intent_route.get("evidence_required", []),
        },
    }
    backend = str(download_backend or "").strip().lower()
    if backend:
        metadata["download_backend"] = backend
    if _as_bool(resume_download):
        metadata["resume_download"] = True
    if candidate_review and need_materialization and not url and not path:
        candidate_review_before_materialization = True
    if candidate_review_before_materialization:
        metadata["source_selection_only"] = True
        metadata["candidate_review_before_materialization"] = True
        metadata["candidate_review_next_action"] = "codex_selects_candidate_or_refines_request_then_resubmits"
    preferred = _owner_list(preferred_owner_tools)
    blocked = _owner_list(blocked_owner_tools)
    if destination_policy == "user_resource_library" and need_materialization and not target_dir:
        target_dir = str(default_artifact_dir(name=name, url=url, path=path, task=task).expanduser().resolve())
    custom_constraints = {
        "source_kind": source_kind.strip(),
        "site_or_domain": site_or_domain.strip(),
        "language": language.strip(),
        "freshness": freshness.strip(),
        "authority": authority.strip(),
        "file_format": file_format.strip(),
        "license": license_filter.strip(),
        "constraints": _compact_mapping(constraints),
        "exclude": _compact_list(exclude),
    }
    custom_constraints = {
        key: value
        for key, value in custom_constraints.items()
        if value not in ("", [], {})
    }
    if resource_kind_hint:
        metadata["resource_kind_hint"] = resource_kind_hint
    if preferred or blocked or custom_constraints or refine_from or refine_reason or relevance_threshold is not None or required_source_count is not None:
        metadata["custom_delegation"] = {
            "schema": "resource_custom_delegation.v1",
            "preferred_owner_tools": preferred,
            "blocked_owner_tools": blocked,
            "constraints": custom_constraints,
            "refine_from_request_id": refine_from.strip(),
            "refine_reason": refine_reason.strip(),
            "candidate_review_requested": bool(candidate_review),
        }
        if relevance_threshold is not None:
            metadata["custom_delegation"]["relevance_threshold"] = relevance_threshold
        if required_source_count is not None:
            metadata["custom_delegation"]["required_source_count"] = required_source_count
        metadata["preferred_owner_tools"] = preferred
        metadata["blocked_owner_tools"] = blocked
    package_fields = {
        "package_ecosystem": package_ecosystem,
        "package_action": package_action,
        "windows_package_manager": windows_package_manager,
        "package_id": package_id,
        "winget_id": winget_id,
        "verify_binary": verify_binary,
    }
    for key, raw_value in package_fields.items():
        value = str(raw_value or "").strip()
        if value:
            metadata[key] = value
    if _as_bool(install_approved):
        metadata["install_approved"] = True
    if _as_bool(target_dir_explicit):
        metadata["package_target_dir_explicit"] = True
    if _as_bool(accept_winget_agreements):
        metadata["accept_winget_agreements"] = True
    normalized_envelope = task_envelope if isinstance(task_envelope, dict) else build_legacy_resource_envelope(
        task=task,
        target=target,
        url=url,
        path=path,
        resource_kind=resource_kind_hint,
        package_action=package_action,
        need_materialization=need_materialization,
        allow_network=allow_network,
        allow_filesystem_write=allow_filesystem_write,
        install_approved=install_approved,
        candidate_review=candidate_review,
        quantity=quantity,
        minimum_quantity=minimum_quantity,
        maximum_quantity=maximum_quantity,
        uniqueness_required=uniqueness_required,
        uniqueness_dimensions=uniqueness_dimensions,
        deduplication_keys=deduplication_keys,
        source_mode=source_mode,
        source_domains=source_domains,
        source_kind=source_kind,
        authority=authority,
        freshness_mode=freshness_mode or freshness,
        max_age_days=max_age_days,
        target_dir=target_dir,
        destination_policy=destination_policy,
        language=language,
        file_format=file_format,
        license_filter=license_filter,
        exclude=exclude,
        preferred_owner_tools=preferred,
        blocked_owner_tools=blocked,
        relevance_threshold=relevance_threshold,
        required_source_count=required_source_count,
    )
    metadata["task_envelope"] = normalized_envelope
    metadata["task_facts"] = resource_task_facts(normalized_envelope)
    envelope_resource = normalized_envelope.get("resource", {}) if isinstance(normalized_envelope, dict) else {}
    envelope_quantity = envelope_resource.get("quantity", {}) if isinstance(envelope_resource, dict) else {}
    envelope_uniqueness = envelope_resource.get("uniqueness", {}) if isinstance(envelope_resource, dict) else {}
    envelope_source_policy = envelope_resource.get("source_policy", {}) if isinstance(envelope_resource, dict) else {}
    requested_count = int(envelope_quantity.get("requested") or 0) if isinstance(envelope_quantity, dict) else 0
    if requested_count:
        metadata["requested_count"] = requested_count
    if isinstance(envelope_uniqueness, dict) and envelope_uniqueness.get("required"):
        metadata["uniqueness_required"] = True
        metadata["deduplication_keys"] = envelope_uniqueness.get("deduplication_keys") or []
    if isinstance(envelope_source_policy, dict) and envelope_source_policy.get("mode") == "multi_source":
        metadata["multi_source_required"] = True
    request = ResourceBrokerRequest(
        target=target,
        url=url,
        path=path,
        task=task,
        name=name,
        intent=resolved_intent,
        need_materialization=need_materialization,
        allow_network=allow_network,
        allow_filesystem_write=allow_filesystem_write,
        max_bytes=max_bytes,
        expected_sha256=expected_sha256,
        timeout_seconds=timeout_seconds,
        retry_budget=retry_budget,
        target_dir=target_dir,
        auto_owner=auto_owner,
        owner_execution_mode=owner_execution_mode,
        metadata=metadata,
    )
    request_payload = asdict(request)
    fast_materialize_allowed = explicit_url_fast_materialize_allowed(
        url=url,
        path=path,
        resolved_intent=resolved_intent,
        need_materialization=need_materialization,
        allow_filesystem_write=allow_filesystem_write,
    )
    fast_materialize_command = ""
    if fast_materialize_allowed:
        fast_materialize_command = (
            "python _bridge\\codex_workflow_entry.py resource materialize-url "
            f"\"{url}\" "
            f"--task \"{task}\" "
            f"--name \"{name}\" "
            "--validation-profile quick --json"
        )
    return {
        "schema": "codex_resource_delegation.v1",
        "ok": True,
        "generated_at": now_iso(),
        "request": request_payload,
        "task_facts": metadata["task_facts"],
        "request_json": json.dumps(request_payload, ensure_ascii=False, sort_keys=True),
        "submit_command": (
            "python _bridge\\codex_workflow_entry.py resource request --json "
            f"--json-payload '{json.dumps(request_payload, ensure_ascii=False, sort_keys=True)}'"
        ),
        "route": route.to_dict(),
        "fast_materialize": {
            "allowed": fast_materialize_allowed,
            "reason": "already_resolved_explicit_url" if fast_materialize_allowed else "",
            "command": fast_materialize_command,
            "contract": (
                "resource-layer lightweight materialization with artifact, receipt log, resource log, and manifest"
                if fast_materialize_allowed
                else ""
            ),
        },
        "intent_resource_route": intent_route,
        "safety_boundaries": [
            "delegation_build_only",
            "no_fetch",
            "no_install",
            "package_install_requires_explicit_install_approved_metadata",
            "no_remote_write",
            "filesystem_write_only_when_request_allow_filesystem_write_true",
        ],
    }


def build_delegation_from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Translate a validated structured envelope into the existing broker request."""

    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return {
            "schema": "codex_resource_delegation.v1",
            "ok": False,
            "generated_at": now_iso(),
            "error_class": "invalid_structured_task_envelope",
            "errors": envelope.get("errors", []) if isinstance(envelope, dict) else ["structured_request_must_be_object"],
            "missing_fields": envelope.get("missing_fields", []) if isinstance(envelope, dict) else [],
            "task_envelope": envelope if isinstance(envelope, dict) else {},
        }
    resource = envelope.get("resource", {})
    quantity = resource.get("quantity", {})
    uniqueness = resource.get("uniqueness", {})
    source_policy = resource.get("source_policy", {})
    freshness = resource.get("freshness", {})
    materialization = resource.get("materialization", {})
    constraints = resource.get("constraints", {})
    owner_tools = resource.get("owner_tools", {})
    quality = resource.get("quality", {})
    transfer = resource.get("transfer", {})
    package = resource.get("package", {})
    safety = envelope.get("safety", {})
    action = str(envelope.get("action") or "")
    need_materialization = bool(materialization.get("required")) or action in {"discover_and_download", "download", "materialize"}
    extra_constraints = [f"{key}={value}" for key, value in (constraints.get("extra") or {}).items()]
    return build_delegation(
        task=str(envelope.get("summary") or envelope.get("target") or "resource request"),
        target=str(envelope.get("target") or ""),
        url=str(envelope.get("url") or ""),
        path=str(envelope.get("path") or ""),
        name=str(transfer.get("name") or ""),
        need_materialization=need_materialization,
        allow_network=bool(safety.get("allow_network", True)),
        allow_filesystem_write=bool(safety.get("allow_filesystem_write")),
        target_dir=str(materialization.get("target_dir") or ""),
        max_bytes=transfer.get("max_bytes"),
        expected_sha256=str(transfer.get("expected_sha256") or ""),
        timeout_seconds=int(transfer.get("timeout_seconds") or 30),
        retry_budget=int(transfer.get("retry_budget") or 1),
        download_backend=str(transfer.get("download_backend") or ""),
        resume_download=bool(transfer.get("resume_download")),
        package_ecosystem=str(package.get("ecosystem") or ""),
        package_action=action if action == "install" else "",
        windows_package_manager=str(package.get("manager") or ""),
        package_id=str(package.get("package_id") or ""),
        winget_id=str(package.get("winget_id") or ""),
        verify_binary=str(package.get("verify_binary") or ""),
        install_approved=bool(safety.get("install_approved")),
        accept_winget_agreements=bool(package.get("accept_agreements")),
        resource_kind=str(resource.get("kind") or ""),
        preferred_owner_tools=list(owner_tools.get("preferred") or []),
        blocked_owner_tools=list(owner_tools.get("blocked") or []),
        source_kind=str(source_policy.get("source_kind") or ""),
        site_or_domain=str((source_policy.get("domains") or [""])[0]),
        language=str(constraints.get("language") or ""),
        freshness=str(freshness.get("mode") or ""),
        authority=str(source_policy.get("authority") or ""),
        file_format=str(constraints.get("format") or ""),
        license_filter=str(constraints.get("license") or ""),
        relevance_threshold=quality.get("relevance_threshold"),
        required_source_count=quality.get("required_source_count"),
        constraints=extra_constraints,
        exclude=list(constraints.get("exclude") or []),
        candidate_review=action == "discover",
        quantity=quantity.get("requested"),
        minimum_quantity=quantity.get("minimum"),
        maximum_quantity=quantity.get("maximum"),
        uniqueness_required=bool(uniqueness.get("required")),
        uniqueness_dimensions=list(uniqueness.get("dimensions") or []),
        deduplication_keys=list(uniqueness.get("deduplication_keys") or []),
        source_mode=str(source_policy.get("mode") or ""),
        source_domains=list(source_policy.get("domains") or []),
        freshness_mode=str(freshness.get("mode") or ""),
        max_age_days=freshness.get("max_age_days"),
        destination_policy=str(materialization.get("destination_policy") or ""),
        task_envelope=envelope,
    )


def validate() -> dict[str, Any]:
    cases = [
        {
            "name": "windows_tool_install_gate",
            "kwargs": {
                "task": "安装 aria2 Windows 工具",
                "target": "aria2",
                "package_ecosystem": "windows_tool",
                "package_action": "install",
                "windows_package_manager": "choco",
            },
            "expected_tool": "package_manager",
            "expected_intent": ResourceIntent.PACKAGE_DEPENDENCY,
            "expected_metadata": {"package_action": "install", "install_approved": None},
        },
        {
            "name": "windows_docs",
            "kwargs": {
                "task": "联网搜索 Windows 代理成熟做法",
                "target": "Windows proxy documentation",
                "intent": ResourceIntent.DOCUMENTATION_LOOKUP,
            },
            "expected_tool": "microsoftdocs",
            "expected_intent": ResourceIntent.DOCUMENTATION_LOOKUP,
        },
        {
            "name": "github_project",
            "kwargs": {
                "task": "联网搜索 GitHub 上适合本机的网络网关项目",
                "target": "GitHub network gateway project",
            },
            "expected_tool": "github",
            "expected_intent": ResourceIntent.EXTERNAL_DEPENDENCY,
        },
        {
            "name": "browser_evidence",
            "kwargs": {
                "task": "网页运行态截图和 DOM 证据",
                "target": "rendered page screenshot evidence",
            },
            "expected_tool": "playwright",
            "expected_intent": ResourceIntent.TOOL_OUTPUT,
        },
    ]
    details: list[dict[str, Any]] = []
    ok = True
    for case in cases:
        payload = build_delegation(**case["kwargs"])
        request = payload.get("request", {})
        route = payload.get("route", {})
        expected_metadata = case.get("expected_metadata", {})
        metadata = request.get("metadata", {})
        metadata_ok = True
        for key, expected_value in expected_metadata.items():
            if expected_value is None:
                metadata_ok = metadata_ok and key not in metadata
            else:
                metadata_ok = metadata_ok and metadata.get(key) == expected_value
        item_ok = (
            request.get("intent") == case["expected_intent"]
            and route.get("primary_tool") == case["expected_tool"]
            and request.get("metadata", {}).get("owner_route_hint", {}).get("source_owner_mcp") == case["expected_tool"]
            and request.get("metadata", {}).get("codex_expectation", {}).get("codex_provided_machine_readable_delegation") is True
            and metadata_ok
        )
        ok = ok and item_ok
        details.append(
            {
                "name": case["name"],
                "ok": item_ok,
                "intent": request.get("intent"),
                "primary_tool": route.get("primary_tool"),
                "owner_route_hint": request.get("metadata", {}).get("owner_route_hint", {}),
                "metadata_ok": metadata_ok,
            }
        )
    return {"schema": "codex_resource_delegation.validate.v1", "ok": ok, "generated_at": now_iso(), "cases": details}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ResourceBrokerRequest JSON from Codex task intent.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build")
    build.add_argument("--task", required=True)
    build.add_argument("--target", default="")
    build.add_argument("--url", default="")
    build.add_argument("--path", default="")
    build.add_argument("--name", default="")
    build.add_argument("--intent", default=ResourceIntent.UNKNOWN)
    build.add_argument("--need-materialization", action="store_true")
    build.add_argument("--allow-network", action=argparse.BooleanOptionalAction, default=True)
    build.add_argument("--allow-filesystem-write", action=argparse.BooleanOptionalAction, default=False)
    build.add_argument("--max-bytes", type=int, default=None)
    build.add_argument("--sha256", default="")
    build.add_argument("--timeout", type=int, default=30)
    build.add_argument("--retries", type=int, default=1)
    build.add_argument("--target-dir", default="")
    build.add_argument("--auto-owner", action=argparse.BooleanOptionalAction, default=True)
    build.add_argument("--owner-execution-mode", default="read_only", choices=("read_only",))
    build.add_argument("--purpose", default="")
    build.add_argument("--validation-profile", default="")
    build.add_argument("--runtime", default="generic")
    build.add_argument("--download-backend", choices=("auto", "curl", "aria2"), default="")
    build.add_argument("--resume-download", action="store_true")
    build.add_argument("--package-ecosystem", default="")
    build.add_argument("--package-action", default="")
    build.add_argument("--windows-package-manager", default="")
    build.add_argument("--package-id", default="")
    build.add_argument("--winget-id", default="")
    build.add_argument("--verify-binary", default="")
    build.add_argument("--install-approved", action="store_true")
    build.add_argument("--accept-winget-agreements", action="store_true")
    build.add_argument("--resource-kind", default="")
    build.add_argument("--owner-tool", action="append", default=[])
    build.add_argument("--avoid-owner-tool", action="append", default=[])
    build.add_argument("--source-kind", default="")
    build.add_argument("--site-or-domain", default="")
    build.add_argument("--language", default="")
    build.add_argument("--freshness", default="")
    build.add_argument("--authority", default="")
    build.add_argument("--format", dest="file_format", default="")
    build.add_argument("--license", dest="license_filter", default="")
    build.add_argument("--relevance-threshold", type=float, default=None)
    build.add_argument("--required-source-count", type=int, default=None)
    build.add_argument("--constraint", action="append", default=[])
    build.add_argument("--exclude", action="append", default=[])
    build.add_argument("--refine-from", default="")
    build.add_argument("--refine-reason", default="")
    build.add_argument("--candidate-review", action="store_true")
    sub.add_parser("validate")
    args = parser.parse_args()
    if args.cmd == "build":
        print_json(
            build_delegation(
                task=args.task,
                target=args.target,
                url=args.url,
                path=args.path,
                name=args.name,
                intent=args.intent,
                need_materialization=_as_bool(args.need_materialization),
                allow_network=_as_bool(args.allow_network),
                allow_filesystem_write=_as_bool(args.allow_filesystem_write),
                max_bytes=args.max_bytes,
                expected_sha256=args.sha256,
                timeout_seconds=args.timeout,
                retry_budget=args.retries,
                target_dir=args.target_dir,
                auto_owner=_as_bool(args.auto_owner),
                owner_execution_mode=args.owner_execution_mode,
                purpose=args.purpose,
                validation_profile=args.validation_profile,
                runtime=args.runtime,
                download_backend=args.download_backend,
                resume_download=_as_bool(args.resume_download),
                package_ecosystem=args.package_ecosystem,
                package_action=args.package_action,
                windows_package_manager=args.windows_package_manager,
                package_id=args.package_id,
                winget_id=args.winget_id,
                verify_binary=args.verify_binary,
                install_approved=_as_bool(args.install_approved),
                accept_winget_agreements=_as_bool(args.accept_winget_agreements),
                resource_kind=args.resource_kind,
                preferred_owner_tools=args.owner_tool,
                blocked_owner_tools=args.avoid_owner_tool,
                source_kind=args.source_kind,
                site_or_domain=args.site_or_domain,
                language=args.language,
                freshness=args.freshness,
                authority=args.authority,
                file_format=args.file_format,
                license_filter=args.license_filter,
                relevance_threshold=args.relevance_threshold,
                required_source_count=args.required_source_count,
                constraints=args.constraint,
                exclude=args.exclude,
                refine_from=args.refine_from,
                refine_reason=args.refine_reason,
                candidate_review=_as_bool(args.candidate_review),
            )
        )
        return 0
    if args.cmd == "validate":
        payload = validate()
        print_json(payload)
        return 0 if payload.get("ok") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
