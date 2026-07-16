#!/usr/bin/env python3
"""Resource request broker with events, attempts, and receipts.

Owns resource-request orchestration. It does not bypass MCP permissions or
pretend CLI subprocesses can call current-turn MCP tools. Local resource_cli
attempts may execute; MCP/browser/domain/package-manager attempts are recorded
as owner-tool requirements so Codex can call the owning tool and attach the
result back to the same request.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from resource_fetcher import (
    ResourceIntent,
    ResourceRequest,
    ResourceResult,
    ResourceStage,
    acquire_resource_with_policy,
    append_resource_log,
)
from resource_execution_budget import ResourceExecutionBudget
from resource_router import ResourceRoute, route_resource
from resource_network_execution import owner_execution_contract, owner_tool_handoff_metadata, route_summary_from_gateway_plan
from resource_owner_executor import execute_owner_tool, owner_tool_handoff_contract, supports_owner_execution
from resource_request_runtime_cache import get_or_compute_network_plan, network_plan_cache_key
from resource_source_executor import execute_source_selection
from resource_source_strategy import candidate_source_plan
from resource_codex_guidance import build_codex_guidance
from resource_store import attach_owner_result, mark_consumed, persist_manifest, read_manifest
from shared.resource_event_store import RECORD_INDEX_PATH, record_event, upsert_request
from resource_validation_profile import metadata_profile
from structured_task_envelope import resource_contract_from_metadata
from resource_strategy_policy import (
    owner_result_relevance,
    owner_result_sufficiency,
    recovery_decision_for_attempt,
    resource_result_satisfaction,
    should_continue_after_attempt,
    strategy_summary,
)


BRIDGE_ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = BRIDGE_ROOT / "resources"
DEFAULT_STORE_ROOT = BRIDGE_ROOT / "resources"
DEFAULT_EVENT_LOG = BRIDGE_ROOT / "logs" / "resource-broker-events.jsonl"
DEFAULT_RECEIPT_LOG = BRIDGE_ROOT / "logs" / "resource-broker-receipts.jsonl"
LOCAL_EXECUTABLE_TOOLS = {"resource_cli", "local_parser", "resource_source_strategy"}
TOOL_ALIASES = {
    "resource_router": "resource_source_strategy",
}
MCP_OR_EXTERNAL_TOOLS = {
    "github",
    "context7",
    "microsoftdocs",
    "markitdown",
    "playwright",
    "chrome-devtools",
    "package_manager",
    "generic_search",
}
NETWORK_OWNER_DEFAULT_TARGETS = {
    "github": "https://api.github.com/",
    "docs": "https://duckduckgo.com/",
    "browser": "https://example.com/",
    "paper": "https://api.openalex.org/works?search=artificial%20intelligence&per-page=1",
    "image": "https://commons.wikimedia.org/",
    "dataset": "https://huggingface.co/datasets",
    "web": "https://example.com/",
    "external": "https://www.gstatic.com/generate_204",
}
NETWORK_OWNER_PROBE_TARGETS = {
    "github": ("https://api.github.com/", "https://github.com/"),
    "docs": ("https://duckduckgo.com/",),
    "browser": ("https://example.com/",),
    "paper": ("https://api.openalex.org/works?search=artificial%20intelligence&per-page=1", "https://export.arxiv.org/"),
    "image": ("https://commons.wikimedia.org/", "https://api.openverse.org/v1/images/?q=test&page_size=1"),
    "dataset": ("https://huggingface.co/datasets", "https://zenodo.org/"),
    "web": ("https://example.com/",),
    "external": ("https://www.gstatic.com/generate_204",),
}
RESOURCE_KIND_TARGET_KINDS = {
    "academic_paper": "paper",
    "image": "image",
    "dataset": "dataset",
    "generic_web": "web",
    "generic_download": "web",
    "document": "web",
    "audio": "web",
    "video": "web",
    "model_artifact": "dataset",
}


@dataclass(frozen=True)
class ResourceBrokerRequest:
    target: str = ""
    url: str = ""
    path: str = ""
    task: str = ""
    name: str = ""
    intent: str = ResourceIntent.UNKNOWN
    need_materialization: bool = False
    allow_network: bool = True
    allow_filesystem_write: bool = False
    max_bytes: int | None = None
    expected_sha256: str = ""
    timeout_seconds: int = 30
    retry_budget: int = 1
    target_dir: str = ""
    auto_owner: bool = False
    owner_execution_mode: str = "read_only"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceAttempt:
    index: int
    tool: str
    stage: str
    status: str
    executable: bool
    started_at: str
    finished_at: str
    result: dict[str, Any] = field(default_factory=dict)
    error_class: str = ""
    reason: str = ""
    next_action: str = ""


@dataclass(frozen=True)
class ResourceReceipt:
    ok: bool
    request_id: str
    status: str
    result_kind: str
    route: dict[str, Any]
    attempts: list[dict[str, Any]]
    progress_events: list[dict[str, Any]]
    artifact_path: str = ""
    content_ref: str = ""
    sha256: str = ""
    cache_hit: bool = False
    error_class: str = ""
    next_action: str = ""
    confidence: float = 0.0
    manifest_path: str = ""
    metadata_path: str = ""
    preview_path: str = ""
    saved_paths: dict[str, str] = field(default_factory=dict)
    strategy_plan: list[dict[str, Any]] = field(default_factory=list)
    strategy_summary: dict[str, Any] = field(default_factory=dict)
    network_gateway_plan: dict[str, Any] = field(default_factory=dict)
    network_summary: dict[str, Any] = field(default_factory=dict)
    owner_execution: dict[str, Any] = field(default_factory=dict)
    owner_result: dict[str, Any] = field(default_factory=dict)
    codex_guidance: dict[str, Any] = field(default_factory=dict)
    satisfaction: dict[str, Any] = field(default_factory=dict)
    execution_budget: dict[str, Any] = field(default_factory=dict)


def now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.UTC).isoformat()


def stable_request_id(request: ResourceBrokerRequest) -> str:
    payload = json.dumps(asdict(request), ensure_ascii=False, sort_keys=True)
    return "res_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def is_resource_result_loggable(result: dict[str, Any]) -> bool:
    """Return true only for ResourceResult-shaped acquisition records."""

    if not isinstance(result, dict):
        return False
    allowed = set(ResourceResult.__dataclass_fields__)
    if any(key not in allowed for key in result):
        return False
    if not {"ok", "source"}.issubset(result):
        return False
    if not any(str(result.get(key) or "") for key in ("local_path", "stored_path", "original_local_path", "sha256")):
        return False
    return True


def maybe_append_resource_result_log(path: Path, result: dict[str, Any]) -> None:
    """Append only real ResourceResult-shaped acquisition records."""

    if not is_resource_result_loggable(result):
        return
    append_resource_log(path, ResourceResult(**result))


def event(request_id: str, stage: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema": "resource_broker.event.v1",
        "request_id": request_id,
        "time": now_iso(),
        "stage": stage,
        "status": status,
        "message": message,
        **extra,
    }


def target_dir_for(request: ResourceBrokerRequest) -> Path:
    return Path(request.target_dir or DEFAULT_CACHE_DIR).expanduser().resolve()


def looks_like_local_path(value: str) -> bool:
    """Return true only for concrete local path-shaped targets.

    Free-form resource targets often contain slashes for concepts such as
    "workflow/routing" or "CodeGraph/static analysis". Treating any slash as a
    file path makes source discovery fall into local-file policy by accident.
    """

    text = str(value or "").strip().strip("\"'")
    if not text or text.startswith(("http://", "https://")):
        return False
    expanded = Path(text).expanduser()
    if expanded.exists():
        return True
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return True
    if text.startswith(("\\\\", "./", "../", ".\\", "..\\")):
        return True
    if "\\" in text:
        return True
    if "/" in text:
        parts = [part for part in text.split("/") if part]
        if len(parts) <= 1:
            return False
        if any(" " in part or part.strip() != part for part in parts):
            return False
        if parts[0] in {".", "..", "~"}:
            return True
        if Path(parts[-1]).suffix and len(text.split()) == 1:
            return True
    return False


def source_fields(request: ResourceBrokerRequest) -> tuple[str, str]:
    url = request.url.strip()
    path = request.path.strip()
    target = request.target.strip()
    if not url and not path and target:
        if target.startswith(("http://", "https://")):
            url = target
        elif looks_like_local_path(target):
            path = target
    return url, path


def hidden_creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def run_json_command(command: list[str], timeout: int) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            command,
            cwd=str(BRIDGE_ROOT.parent),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=hidden_creationflags(),
        )
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__, "error": str(exc)[:500]}
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        payload = {"ok": False, "reason": f"json_decode_failed: {exc}", "stdout_tail": (proc.stdout or "")[-1000:]}
    if isinstance(payload, dict):
        payload.setdefault("ok", proc.returncode == 0)
        payload.setdefault("returncode", proc.returncode)
        return payload
    return {"ok": proc.returncode == 0, "result": payload, "returncode": proc.returncode}


def network_target_kind_for_request(request: ResourceBrokerRequest, route: ResourceRoute) -> str:
    if route.primary_tool == "package_manager" or route.intent == ResourceIntent.PACKAGE_DEPENDENCY:
        return "package"
    if route.primary_tool == "github":
        return "github"
    if route.primary_tool in {"context7", "microsoftdocs"} or route.intent == ResourceIntent.DOCUMENTATION_LOOKUP:
        return "docs"
    if route.primary_tool in {"playwright", "chrome-devtools"}:
        return "browser"
    source_kind = source_selection_target_kind_for_request(request, route)
    if source_kind:
        return source_kind
    return ""


def source_selection_target_kind_for_request(request: ResourceBrokerRequest, route: ResourceRoute) -> str:
    """Map source-selection requests to gateway target kinds.

    Source selection may not have a final URL yet. This function keeps the
    resource strategy in the resource layer while still giving the gateway a
    stable network class for route/cache decisions.
    """

    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    hint = str(metadata.get("resource_kind_hint") or "").strip()
    if hint in RESOURCE_KIND_TARGET_KINDS:
        return RESOURCE_KIND_TARGET_KINDS[hint]
    risk_flags = set(route.risk_flags or ())
    if "academic_source_selection" in risk_flags:
        return "paper"
    if "image_source_selection" in risk_flags:
        return "image"
    if "dataset_source_selection" in risk_flags:
        return "dataset"
    if "multi_source_research" in risk_flags:
        return "web"
    if "external_source_selection" in risk_flags:
        return "web"
    if route.primary_tool == "resource_router" or route.source_kind == "unknown":
        plan = candidate_source_plan(asdict(request), route.to_dict(), limit=1)
        return RESOURCE_KIND_TARGET_KINDS.get(str(plan.get("resource_kind") or ""), "")
    return ""


def network_owner_tool_for_request(request: ResourceBrokerRequest, route: ResourceRoute, target_kind: str) -> str:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    explicit = str(metadata.get("owner_tool") or metadata.get("source_owner_tool") or metadata.get("network_owner_tool") or "").strip()
    if explicit:
        return explicit
    if target_kind == "docs":
        source_plan = candidate_source_plan(asdict(request), route.to_dict())
        capability = source_plan.get("execution_capability") if isinstance(source_plan.get("execution_capability"), dict) else {}
        registered_owner = str(capability.get("registered_owner_adapter") or "").strip()
        if registered_owner:
            return registered_owner
    if route.primary_tool:
        return route.primary_tool
    return target_kind or "generic"


def package_probe_target_for_request(request: ResourceBrokerRequest) -> str:
    """Return the registry URL that matches the current package executor.

    Match gateway evidence to the selected package ecosystem so an approved
    WinGet, Chocolatey, npm, or Python action does not probe an unrelated
    registry before the owner command runs.
    """

    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    ecosystem = str(metadata.get("package_ecosystem") or metadata.get("ecosystem") or "").lower()
    manager = str(
        metadata.get("windows_package_manager") or metadata.get("package_manager") or ""
    ).lower()
    if manager == "winget" or ecosystem == "winget":
        return "https://cdn.winget.microsoft.com/cache"
    if manager in {"choco", "chocolatey"} or ecosystem in {
        "windows",
        "windows_tool",
        "win_tool",
        "choco",
        "chocolatey",
    }:
        return "https://community.chocolatey.org/api/v2/"
    task_text = " ".join([request.task, request.target, request.name]).lower()
    if ecosystem in {"node", "npm", "pnpm", "yarn"} or any(term in task_text for term in ("npm ", "pnpm ", "yarn ", "npx ")):
        return "https://registry.npmjs.org/"
    package_name = str(request.target or request.name or "").strip().split()[0] if str(request.target or request.name or "").strip() else "requests"
    return f"https://pypi.org/simple/{package_name}/"


def structured_source_domains(request: ResourceBrokerRequest) -> list[str]:
    """Read only explicit structured source-domain constraints.

    Free-form task text is deliberately excluded: filenames such as AGENTS.md
    and prose examples are not network authorities.
    """

    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    custom = metadata.get("custom_delegation") if isinstance(metadata.get("custom_delegation"), dict) else {}
    source_policy = custom.get("source_policy") if isinstance(custom.get("source_policy"), dict) else {}
    values: list[str] = []
    for candidate in (
        metadata.get("source_domains"),
        metadata.get("official_domains"),
        source_policy.get("domains"),
        source_policy.get("source_domains"),
    ):
        if isinstance(candidate, str):
            values.append(candidate)
        elif isinstance(candidate, (list, tuple)):
            values.extend(str(item) for item in candidate)
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        domain = str(value or "").strip().lower()
        domain = re.sub(r"^https?://", "", domain).split("/", 1)[0].strip(".")
        if not domain or "." not in domain or domain in seen:
            continue
        seen.add(domain)
        output.append(domain)
    return output


def structured_probe_targets(request: ResourceBrokerRequest, target_kind: str, primary: str) -> list[str]:
    domains = structured_source_domains(request)
    if domains:
        return [f"https://{domain}/" for domain in domains]
    defaults = list(NETWORK_OWNER_PROBE_TARGETS.get(target_kind, ()))
    if primary and primary not in defaults:
        defaults.insert(0, primary)
    return defaults or ([primary] if primary else [])


def owner_probe_target_for_request(request: ResourceBrokerRequest, route: ResourceRoute, target_kind: str) -> tuple[str, str]:
    """Return a representative network target for owner-tool requests.

    The resource layer owns source/tool strategy, while the gateway owns route
    selection. For target-only owner requests there may be no final URL yet, so
    the broker supplies a stable owner endpoint for route probing and cache keys.
    """

    if target_kind == "package":
        return package_probe_target_for_request(request), "package_registry_probe"
    if target_kind == "github":
        target = str(request.target or request.name or "").strip()
        if target.startswith(("http://", "https://")):
            return target, "github_concrete_target"
        if "/" in target and " " not in target:
            return f"https://api.github.com/repos/{target.strip('/')}", "github_repo_api_probe"
        return NETWORK_OWNER_DEFAULT_TARGETS["github"], "github_api_search_probe"
    if target_kind == "docs":
        domains = structured_source_domains(request)
        if domains:
            return f"https://{domains[0]}/", "structured_source_domain_probe"
        if route.primary_tool == "microsoftdocs":
            return "https://learn.microsoft.com/", "microsoftdocs_default_probe"
        if route.primary_tool in {"openai-docs", "openaiDeveloperDocs"}:
            return "https://developers.openai.com/", "openai_docs_default_probe"
        if route.primary_tool == "context7":
            return "https://context7.com/", "context7_owner_probe"
        if route.primary_tool == "generic_search":
            return "https://duckduckgo.com/", "generic_search_owner_probe"
        return NETWORK_OWNER_DEFAULT_TARGETS["docs"], "docs_default_probe"
    if target_kind == "browser":
        return NETWORK_OWNER_DEFAULT_TARGETS["browser"], "browser_default_probe"
    if target_kind in {"paper", "image", "dataset", "web"}:
        return NETWORK_OWNER_DEFAULT_TARGETS[target_kind], f"{target_kind}_default_probe"
    if target_kind:
        return NETWORK_OWNER_DEFAULT_TARGETS["external"], "external_default_probe"
    return "", ""


def network_gateway_plan_for_request(
    request: ResourceBrokerRequest,
    route: ResourceRoute,
    *,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    precomputed = request.metadata.get("network_gateway_plan") if isinstance(request.metadata, dict) else None
    if isinstance(precomputed, dict) and precomputed.get("ok"):
        payload = copy.deepcopy(precomputed)
        payload.setdefault("source", "resource_request_metadata")
        payload.setdefault("cache_status", "precomputed_batch_plan")
        return payload
    effective_request = replace(request, timeout_seconds=timeout_seconds) if timeout_seconds is not None else request
    spec = network_gateway_request_for_request(effective_request, route)
    if spec.get("skipped"):
        return spec
    if not spec.get("ok"):
        return spec
    target_kind = str(spec.get("target_kind") or "")
    target_value = str(spec.get("target") or "")
    owner_tool = str(spec.get("owner_tool") or target_kind or "generic")
    runtime = str(spec.get("runtime") or "generic")
    probe_timeout = int(spec.get("probe_timeout") or 12)
    profile_name = str(spec.get("validation_profile") or "")
    cache_key = network_plan_cache_key(
        profile=profile_name,
        target_kind=target_kind,
        target=target_value,
        owner_tool=owner_tool,
        runtime=runtime,
        probe_timeout=probe_timeout,
    )
    ttl = max(0, int(spec.get("network_plan_cache_ttl_seconds") or 0))

    def compute() -> dict[str, Any]:
        command_timeout = max(1, min(int(timeout_seconds or probe_timeout + 25), probe_timeout + 25))
        command = [
            sys.executable,
            str(BRIDGE_ROOT / "codex_network_gateway.py"),
            "plan",
            "--target-kind",
            target_kind,
            "--target",
            target_value,
            "--runtime",
            runtime,
            "--owner-tool",
            owner_tool,
            "--probe",
            "--probe-timeout",
            str(probe_timeout),
        ]
        payload = run_json_command(command, timeout=command_timeout)
        payload.setdefault("validation_profile", profile_name)
        payload.setdefault("cache_target", target_value)
        return payload

    payload = get_or_compute_network_plan(cache_key, ttl, compute)
    payload.setdefault("requested_target", target_value)
    return payload


def network_gateway_request_for_request(request: ResourceBrokerRequest, route: ResourceRoute) -> dict[str, Any]:
    profile = metadata_profile(request.metadata)
    url, _path = source_fields(request)
    target_kind = network_target_kind_for_request(request, route)
    owner_tool = network_owner_tool_for_request(request, route, target_kind)
    target_value = url
    probe_target_reason = "concrete_url" if target_value else ""
    if not target_value and target_kind:
        target_value, probe_target_reason = owner_probe_target_for_request(request, route, target_kind)
    probe_targets = structured_probe_targets(request, target_kind, target_value)
    if target_kind and not target_value:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_concrete_network_target_for_owner_route",
            "validation_profile": profile.name,
            "target_kind": target_kind,
            "owner_tool": owner_tool,
            "target": target_value,
            "probe_targets": probe_targets,
            "plan": {
                "route_mode": "owner_tool_managed_network",
                "target_kind": target_kind,
                "owner_tool": owner_tool,
                "target": "",
                "env": {},
                "unset_env": [],
            },
        }
    if not request.allow_network or (route.source_kind != "url" and not target_kind):
        return {"ok": True, "skipped": True, "reason": "not_network_url_request"}
    if not profile.live_network:
        return {
            "ok": True,
            "skipped": True,
            "reason": "validation_profile_no_live_network",
            "validation_profile": profile.name,
            "target_kind": target_kind,
            "owner_tool": owner_tool,
            "target": target_value,
            "target_reason": probe_target_reason,
            "probe_targets": probe_targets,
            "plan": {
                "route_mode": "validation_profile_skipped",
                "target_kind": target_kind,
                "owner_tool": owner_tool,
                "target": target_value,
                "env": {},
                "unset_env": [],
            },
        }
    runtime = str(request.metadata.get("runtime") or "generic") if isinstance(request.metadata, dict) else "generic"
    probe_timeout = max(1, min(profile.max_owner_timeout_seconds, 20, int(request.timeout_seconds or 12)))
    return {
        "ok": True,
        "schema": "resource_broker.network_gateway_request.v1",
        "validation_profile": profile.name,
        "network_plan_cache_ttl_seconds": profile.network_plan_cache_ttl_seconds,
        "target_kind": target_kind,
        "owner_tool": owner_tool,
        "target": target_value,
        "target_reason": probe_target_reason,
        "probe_targets": probe_targets,
        "runtime": runtime,
        "probe_timeout": probe_timeout,
        "probe": True,
        "source": "resource_broker",
    }


def route_for_request(request: ResourceBrokerRequest) -> ResourceRoute:
    url, path = source_fields(request)
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    custom_delegation = metadata.get("custom_delegation") if isinstance(metadata.get("custom_delegation"), dict) else {}
    constraints = custom_delegation.get("constraints") if isinstance(custom_delegation.get("constraints"), dict) else {}
    envelope = resource_contract_from_metadata(metadata)
    resource = envelope.get("resource", {}) if envelope else {}
    source_policy = resource.get("source_policy", {}) if isinstance(resource, dict) else {}
    structured_domains = source_policy.get("domains", []) if isinstance(source_policy, dict) else []
    return route_resource(
        url=url,
        path=path,
        target=request.target,
        intent=request.intent,
        need_materialization=request.need_materialization,
        task=request.task,
        name=request.name,
        resource_kind_hint=str(resource.get("kind") or metadata.get("resource_kind_hint") or ""),
        source_kind_hint=str(source_policy.get("source_kind") or constraints.get("source_kind") or ""),
        site_or_domain=str((structured_domains or [constraints.get("site_or_domain") or ""])[0]),
    )


def _metadata_list(metadata: dict[str, Any], key: str) -> list[str]:
    envelope = resource_contract_from_metadata(metadata)
    owner_tools = envelope.get("resource", {}).get("owner_tools", {}) if envelope else {}
    structured_key = "preferred" if key == "preferred_owner_tools" else ("blocked" if key == "blocked_owner_tools" else "")
    values = owner_tools.get(structured_key) if structured_key and owner_tools.get(structured_key) else metadata.get(key)
    if not values and isinstance(metadata.get("custom_delegation"), dict):
        values = metadata["custom_delegation"].get(key)
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


def candidate_tools(route: ResourceRoute, request: ResourceBrokerRequest | None = None) -> list[str]:
    if route.primary_tool == "resource_router":
        tools = ["resource_source_strategy", *route.secondary_tools]
    else:
        tools = [route.primary_tool, *route.secondary_tools]
    url, _path = source_fields(request) if request else ("", "")
    if request and request.need_materialization and url:
        tools = ["resource_cli", *tools]
    metadata = request.metadata if request and isinstance(request.metadata, dict) else {}
    preferred = _metadata_list(metadata, "preferred_owner_tools")
    blocked = set(_metadata_list(metadata, "blocked_owner_tools"))
    if preferred:
        tools = [*preferred, *tools]
    if "resource_cli" not in tools:
        tools.append("resource_cli")
    normalized = [TOOL_ALIASES.get(tool, tool) for tool in tools]
    normalized_blocked = {TOOL_ALIASES.get(tool, tool) for tool in blocked}
    return [tool for tool in dict.fromkeys(normalized) if tool and tool != "none" and tool not in normalized_blocked]


def strategy_plan_for_request(request: ResourceBrokerRequest, route: ResourceRoute) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    if not route.ok:
        return plan
    source_plan = candidate_source_plan(asdict(request), route.to_dict())
    tools = candidate_tools(route, request)
    capability = source_plan.get("execution_capability") if isinstance(source_plan.get("execution_capability"), dict) else {}
    registered_owner = str(capability.get("registered_owner_adapter") or "").strip()
    if (
        source_plan.get("source_missing")
        and registered_owner
        and supports_owner_execution(registered_owner, request.owner_execution_mode)
        and registered_owner not in tools
    ):
        insert_at = tools.index("resource_source_strategy") + 1 if "resource_source_strategy" in tools else 0
        tools.insert(insert_at, registered_owner)
    for index, tool in enumerate(tools, start=1):
        stage = stage_for_tool(route, request, tool)
        blocked_reason = blocked_by_constraints(request, route, tool, stage)
        item = {
            "index": index,
            "tool": tool,
            "stage": stage,
            "executable_by_broker": (tool in LOCAL_EXECUTABLE_TOOLS or owner_auto_executable(request, tool)) and not blocked_reason,
            "expected_status": "handoff_required" if blocked_reason == "handoff_required_for_owner_tool" else ("blocked" if blocked_reason else "attempt"),
            "reason": blocked_reason or "eligible",
        }
        if tool == "resource_source_strategy":
            item["source_strategy"] = source_plan
            item["first_candidate"] = (source_plan.get("candidates") or [{}])[0]
        plan.append(item)
    return plan


def stage_for_tool(route: ResourceRoute, request: ResourceBrokerRequest, tool: str) -> str:
    if tool == "resource_source_strategy":
        return ResourceStage.DISCOVER
    if tool == "resource_cli":
        if request.need_materialization and not request.allow_filesystem_write:
            return ResourceStage.MATERIALIZE
        if request.need_materialization and request.allow_filesystem_write:
            return ResourceStage.MATERIALIZE
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        batch_contract = metadata.get("batch_item_contract") if isinstance(metadata.get("batch_item_contract"), dict) else {}
        acceptance = batch_contract.get("acceptance") if isinstance(batch_contract.get("acceptance"), dict) else {}
        if bool(acceptance.get("consumable_required")) and route.source_kind == "url":
            return ResourceStage.PREVIEW
        return route.recommended_stage if route.recommended_stage != ResourceStage.MATERIALIZE else ResourceStage.PREVIEW
    return route.recommended_stage


def blocked_by_constraints(request: ResourceBrokerRequest, route: ResourceRoute, tool: str, stage: str) -> str:
    if not request.allow_network and route.source_kind == "url" and stage in {
        ResourceStage.PROBE,
        ResourceStage.PREVIEW,
        ResourceStage.MATERIALIZE,
    }:
        return "network_not_allowed"
    if stage == ResourceStage.MATERIALIZE and not request.allow_filesystem_write:
        return "filesystem_write_not_allowed"
    if tool in MCP_OR_EXTERNAL_TOOLS:
        if owner_auto_executable(request, tool):
            return ""
        return "handoff_required_for_owner_tool"
    return ""


def owner_auto_executable(request: ResourceBrokerRequest, tool: str) -> bool:
    return bool(request.auto_owner and supports_owner_execution(tool, request.owner_execution_mode))


def source_selection_only(request: ResourceBrokerRequest) -> bool:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    return bool(metadata.get("source_selection_only"))


def make_resource_request(request: ResourceBrokerRequest, stage: str, network_gateway_plan: dict[str, Any] | None = None) -> ResourceRequest:
    url, path = source_fields(request)
    local_path = Path(path) if path else None
    return ResourceRequest(
        source="resource_broker",
        target_dir=target_dir_for(request),
        name=request.name or (Path(url.split("?", 1)[0]).name if url else (Path(path).name if path else "resource")),
        local_path=local_path,
        url=url,
        expected_sha256=request.expected_sha256,
        max_bytes=request.max_bytes,
        timeout_seconds=request.timeout_seconds,
        retries=max(0, int(request.retry_budget)),
        retry_delay_seconds=1.0,
        metadata={
            "broker_stage": stage,
            "task": request.task,
            **request.metadata,
            "network_gateway_plan": network_gateway_plan or {},
        },
    )


def result_error_class(result: ResourceResult) -> str:
    if result.ok:
        return ""
    metadata = result.metadata or {}
    if metadata.get("error_type"):
        return str(metadata["error_type"])
    if result.decision == "deferred":
        return "policy_deferred"
    if result.policy_reason:
        return result.policy_reason
    return result.error or "resource_failed"


def execute_local_attempt(
    request: ResourceBrokerRequest,
    route: ResourceRoute,
    *,
    tool: str,
    stage: str,
    index: int,
    network_gateway_plan: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
) -> ResourceAttempt:
    started = now_iso()
    effective_timeout = max(1, int(timeout_seconds or request.timeout_seconds or 1))
    reason = blocked_by_constraints(request, route, tool, stage)
    if reason:
        result = {}
        if reason == "handoff_required_for_owner_tool":
            result = {
                "network_handoff": owner_tool_handoff_metadata(network_gateway_plan),
                "owner_tool_contract": owner_tool_handoff_contract(tool, asdict(request), network_gateway_plan),
            }
        return ResourceAttempt(
            index=index,
            tool=tool,
            stage=stage,
            status="blocked" if reason != "handoff_required_for_owner_tool" else "handoff_required",
            executable=False,
            started_at=started,
            finished_at=now_iso(),
            result=result,
            error_class=reason,
            reason=reason,
            next_action="use_codex_current_turn_owner_tool" if reason == "handoff_required_for_owner_tool" else "adjust_request_constraints",
        )
    if tool == "resource_source_strategy":
        result = execute_source_selection(
            asdict(request),
            route.to_dict(),
            timeout=effective_timeout,
        )
        if source_selection_only(request) and result.get("ok"):
            result = {
                **result,
                "next_action": "return_source_selection_receipt",
                "materialization_deferred": True,
            }
        return ResourceAttempt(
            index=index,
            tool=tool,
            stage=stage,
            status=str(result.get("status") or ("completed" if result.get("ok") else "failed")),
            executable=True,
            started_at=started,
            finished_at=now_iso(),
            result=result,
            error_class=str(result.get("error_class") or ""),
            reason=str(result.get("reason") or ""),
            next_action=str(result.get("next_action") or ("use_selected_source" if result.get("ok") else "try_next_route")),
        )
    if tool not in LOCAL_EXECUTABLE_TOOLS:
        if owner_auto_executable(request, tool):
            owner_result = execute_owner_tool(
                tool=tool,
                request=asdict(request),
                gateway_plan=network_gateway_plan or {},
                timeout=effective_timeout,
                mode=request.owner_execution_mode,
            )
            relevance = owner_result_relevance(request=asdict(request), tool=tool, result=owner_result)
            if owner_result.get("ok") and not relevance.ok:
                owner_result = {
                    **owner_result,
                    "ok": False,
                    "status": "degraded",
                    "error_class": "low_relevance",
                    "reason": relevance.reason,
                    "next_action": "try_next_route",
                    "relevance": relevance.to_dict(),
                }
            elif owner_result.get("ok"):
                sufficiency = owner_result_sufficiency(request=asdict(request), tool=tool, result=owner_result)
                if not sufficiency.ok:
                    owner_result = {
                        **owner_result,
                        "ok": False,
                        "status": "degraded",
                        "error_class": "insufficient_coverage",
                        "reason": sufficiency.reason,
                        "next_action": sufficiency.next_action,
                        "relevance": relevance.to_dict(),
                        "sufficiency": sufficiency.to_dict(),
                    }
                else:
                    owner_result = {
                        **owner_result,
                        "relevance": relevance.to_dict(),
                        "sufficiency": sufficiency.to_dict(),
                    }
            return ResourceAttempt(
                index=index,
                tool=tool,
                stage=stage,
                status=str(owner_result.get("status") or ("completed" if owner_result.get("ok") else "failed")),
                executable=True,
                started_at=started,
                finished_at=now_iso(),
                result=owner_result,
                error_class=str(owner_result.get("error_class") or ""),
                reason=str(owner_result.get("reason") or ""),
                next_action=str(owner_result.get("next_action") or ("return_resource" if owner_result.get("ok") else "try_next_route")),
            )
        return ResourceAttempt(
            index=index,
            tool=tool,
            stage=stage,
            status="skipped",
            executable=False,
            started_at=started,
            finished_at=now_iso(),
            error_class="unsupported_local_tool",
            reason="tool is not executable from local broker",
            next_action="add bounded adapter or expose broker through MCP/Hub",
        )
    result = acquire_resource_with_policy(
        make_resource_request(replace(request, timeout_seconds=effective_timeout), stage, network_gateway_plan=network_gateway_plan),
        intent=route.intent,
        stage=stage,
    )
    return ResourceAttempt(
        index=index,
        tool=tool,
        stage=stage,
        status="completed" if result.ok else (result.decision or "failed"),
        executable=True,
        started_at=started,
        finished_at=now_iso(),
        result=result.to_dict(),
        error_class=result_error_class(result),
        reason=result.policy_reason or result.error,
        next_action=result.next_action or ("return_resource" if result.ok else "try_next_route"),
    )


def attempt_with_recovery_decision(attempt: ResourceAttempt) -> ResourceAttempt:
    """Attach canonical recovery policy to the attempt result metadata."""

    payload = asdict(attempt)
    decision = recovery_decision_for_attempt(payload).to_dict()
    result = dict(attempt.result or {})
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    result["metadata"] = {**metadata, "recovery_decision": decision}
    return ResourceAttempt(**{**payload, "result": result})


def receipt_from_attempts(
    request_id: str,
    request: ResourceBrokerRequest,
    route: ResourceRoute,
    attempts: list[ResourceAttempt],
    events: list[dict[str, Any]],
    network_gateway_plan: dict[str, Any],
    strategy_plan: list[dict[str, Any]] | None = None,
) -> ResourceReceipt:
    handoff = next((attempt for attempt in attempts if attempt.status == "handoff_required"), None)
    owner_failure = next(
        (
            attempt
            for attempt in attempts
            if attempt.tool in MCP_OR_EXTERNAL_TOOLS
            and attempt.executable
            and attempt.status == "failed"
            and not attempt.result.get("ok")
        ),
        None,
    )
    successful: ResourceAttempt | None = None
    successful_satisfaction: dict[str, Any] = {}
    unmet_satisfaction: dict[str, Any] = {}
    for attempt in attempts:
        if attempt.tool == "resource_source_strategy":
            continue
        decision = resource_result_satisfaction(
            request=asdict(request), tool=attempt.tool, result=attempt.result
        ).to_dict()
        if attempt.result.get("ok") and not unmet_satisfaction:
            unmet_satisfaction = decision
        if decision.get("satisfied"):
            successful = attempt
            successful_satisfaction = decision
            break
    network_summary = route_summary_from_gateway_plan(network_gateway_plan)
    request_url, _request_path = source_fields(request)
    request_target = request_url or request.target
    if network_summary.get("ok"):
        network_summary = {
            **network_summary,
            "route_probe_target": network_summary.get("target", ""),
            "request_target": request_target,
            "target": request_target,
            "route_evidence_reused": bool(
                str(network_summary.get("target") or "")
                and request_target
                and str(network_summary.get("target") or "") != request_target
            ),
        }
    strategy = strategy_summary([asdict(attempt) for attempt in attempts], strategy_plan or [])
    owner_execution = {}
    if handoff:
        owner_execution = handoff.result.get("owner_tool_contract") if isinstance(handoff.result.get("owner_tool_contract"), dict) else {}
        if not owner_execution:
            owner_execution = owner_execution_contract(handoff.tool, network_gateway_plan)
    if owner_failure:
        return ResourceReceipt(
            ok=False,
            request_id=request_id,
            status="failed",
            result_kind="none",
            route=route.to_dict(),
            attempts=[asdict(attempt) for attempt in attempts],
            progress_events=events,
            strategy_summary=strategy,
            network_gateway_plan=network_gateway_plan,
            network_summary=network_summary,
            owner_execution=owner_execution,
            error_class=owner_failure.error_class,
            next_action=owner_failure.next_action or "surface_owner_tool_failure",
            confidence=0.35,
        )
    source_selection = next(
        (attempt for attempt in attempts if source_selection_only(request) and attempt.result.get("ok") and attempt.result.get("selected_url")),
        None,
    )
    if source_selection:
        return ResourceReceipt(
            ok=True,
            request_id=request_id,
            status="completed",
            result_kind="source_selection",
            route=route.to_dict(),
            attempts=[asdict(attempt) for attempt in attempts],
            progress_events=events,
            strategy_summary=strategy,
            network_gateway_plan=network_gateway_plan,
            network_summary=network_summary,
            owner_execution=owner_execution,
            next_action="review_candidates_and_resubmit_selected_source",
            confidence=0.9,
            satisfaction={
                "schema": "resource_satisfaction.v1",
                "satisfied": False,
                "stage_satisfied": True,
                "reason": "source_selection_ready_for_review",
                "next_action": "review_candidates_and_resubmit_selected_source",
            },
        )
    refinement_needed = next((attempt for attempt in attempts if attempt.error_class == "insufficient_coverage"), None)
    if successful:
        result = successful.result
        artifact = str(result.get("stored_path") or result.get("local_path") or "")
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        has_preview = "preview_text" in metadata
        if handoff and not artifact and not has_preview:
            return ResourceReceipt(
                ok=False,
                request_id=request_id,
                status="handoff_required",
                result_kind="metadata",
                route=route.to_dict(),
                attempts=[asdict(attempt) for attempt in attempts],
                progress_events=events,
                strategy_summary=strategy,
                network_gateway_plan=network_gateway_plan,
                network_summary=network_summary,
                owner_execution=owner_execution,
                error_class=handoff.error_class,
                next_action=handoff.next_action,
                confidence=0.7,
            )
        owner_result_kind = str(result.get("result_kind") or "")
        result_kind = "artifact" if artifact else ("preview" if has_preview else (owner_result_kind or "metadata"))
        return ResourceReceipt(
            ok=True,
            request_id=request_id,
            status="completed",
            result_kind=result_kind,
            route=route.to_dict(),
            attempts=[asdict(attempt) for attempt in attempts],
            progress_events=events,
            strategy_summary=strategy,
            network_gateway_plan=network_gateway_plan,
            network_summary=network_summary,
            owner_execution=owner_execution,
            artifact_path=artifact,
            content_ref=artifact or request_id,
            sha256=str(result.get("sha256") or ""),
            cache_hit=bool(result.get("cache_hit")),
            next_action=str(result.get("next_action") or "consume_resource"),
            confidence=0.95,
            satisfaction=successful_satisfaction,
        )
    if handoff:
        return ResourceReceipt(
            ok=False,
            request_id=request_id,
            status="handoff_required",
            result_kind="metadata",
            route=route.to_dict(),
            attempts=[asdict(attempt) for attempt in attempts],
            progress_events=events,
            strategy_summary=strategy,
            network_gateway_plan=network_gateway_plan,
            network_summary=network_summary,
            owner_execution=owner_execution,
            error_class=handoff.error_class,
            next_action=handoff.next_action,
            confidence=0.7,
            satisfaction=unmet_satisfaction,
        )
    if refinement_needed or unmet_satisfaction:
        return ResourceReceipt(
            ok=False,
            request_id=request_id,
            status="deferred",
            result_kind="metadata",
            route=route.to_dict(),
            attempts=[asdict(attempt) for attempt in attempts],
            progress_events=events,
            strategy_summary=strategy,
            network_gateway_plan=network_gateway_plan,
            network_summary=network_summary,
            owner_execution=owner_execution,
            error_class=str((unmet_satisfaction or {}).get("reason") or "insufficient_coverage"),
            next_action=str((unmet_satisfaction or {}).get("next_action") or "refine_resource_delegation_and_retry"),
            confidence=0.65,
            satisfaction=unmet_satisfaction or {"schema": "resource_satisfaction.v1", "satisfied": False, "reason": "insufficient_coverage", "next_action": "refine_resource_delegation_and_retry"},
        )
    last = attempts[-1] if attempts else None
    status = "handoff_required" if handoff else "failed"
    next_action = handoff.next_action if handoff else (last.next_action if last else "classify_resource")
    error_class = handoff.error_class if handoff else (last.error_class if last else "no_attempts")
    return ResourceReceipt(
        ok=False,
        request_id=request_id,
        status=status,
        result_kind="none",
        route=route.to_dict(),
        attempts=[asdict(attempt) for attempt in attempts],
        progress_events=events,
        strategy_summary=strategy,
        network_gateway_plan=network_gateway_plan,
        network_summary=network_summary,
        owner_execution=owner_execution,
        error_class=error_class,
        next_action=next_action,
        confidence=0.65 if handoff else 0.3,
        satisfaction={"schema": "resource_satisfaction.v1", "satisfied": False, "reason": error_class or "no_satisfying_attempt", "next_action": next_action},
    )


def handle_request(
    request: ResourceBrokerRequest,
    *,
    event_log: Path = DEFAULT_EVENT_LOG,
    receipt_log: Path = DEFAULT_RECEIPT_LOG,
    resource_log: Path | None = None,
    store_root: Path = DEFAULT_STORE_ROOT,
    execution_budget_seconds: float | int | None = None,
) -> ResourceReceipt:
    request_id = stable_request_id(request)
    execution_budget = ResourceExecutionBudget.start(
        request.timeout_seconds if execution_budget_seconds is None else execution_budget_seconds
    )
    events: list[dict[str, Any]] = []
    resolved_store_root = store_root.expanduser().resolve()
    event_db_path = (
        RECORD_INDEX_PATH
        if resolved_store_root == DEFAULT_STORE_ROOT.expanduser().resolve()
        else resolved_store_root / "_index" / "resource-observability.sqlite"
    )

    def emit(event_stage: str, status: str, message: str, **extra: Any) -> None:
        item = event(request_id, event_stage, status, message, **extra)
        events.append(item)
        append_jsonl(event_log, item)
        record_event(item, db_path=event_db_path)

    emit("submitted", "ok", "resource request accepted")
    route = route_for_request(request)
    emit("classified", "ok" if route.ok else "blocked", "resource route classified", route=route.to_dict())
    strategy_plan = strategy_plan_for_request(request, route)
    emit("planned", "ok" if strategy_plan or route.ok else "blocked", "resource strategy planned", strategy_plan=strategy_plan)
    deterministic_block = ""
    if request.need_materialization and not request.allow_filesystem_write:
        deterministic_block = "filesystem_write_not_allowed"
    elif not request.allow_network and route.source_kind == "url":
        deterministic_block = "network_not_allowed"
    gateway_cap = max(1, min(8, int(max(1, request.timeout_seconds) * 0.35)))
    gateway_timeout = execution_budget.timeout_seconds(cap=gateway_cap)
    if deterministic_block:
        network_gateway_plan = {
            "ok": False,
            "reason": "preflight_policy_block",
            "error_class": deterministic_block,
            "network_skipped": True,
            "execution_budget": execution_budget.snapshot(phase="network_gateway_skipped"),
        }
    elif gateway_timeout > 0:
        network_gateway_plan = network_gateway_plan_for_request(request, route, timeout_seconds=gateway_timeout)
    else:
        network_gateway_plan = {"ok": False, "reason": "total_budget_exhausted", "execution_budget": execution_budget.snapshot(phase="network_gateway")}
    emit(
        "network_gateway",
        "skipped" if deterministic_block else ("ok" if network_gateway_plan.get("ok") else "degraded"),
        "network gateway route evidence attached",
        network_gateway_plan=network_gateway_plan,
    )
    attempts: list[ResourceAttempt] = []
    if route.ok:
        effective_request = request
        effective_route = route
        effective_network_gateway_plan = network_gateway_plan
        planned_tools = [
            str(item.get("tool") or "")
            for item in strategy_plan
            if isinstance(item, dict) and str(item.get("tool") or "").strip()
        ]
        for tool in planned_tools or candidate_tools(route, effective_request):
            remaining_timeout = execution_budget.timeout_seconds(cap=effective_request.timeout_seconds)
            if remaining_timeout <= 0:
                exhausted = ResourceAttempt(
                    index=len(attempts) + 1,
                    tool=tool,
                    stage=stage_for_tool(effective_route, effective_request, tool),
                    status="failed",
                    executable=False,
                    started_at=now_iso(),
                    finished_at=now_iso(),
                    result={"ok": False, "execution_budget": execution_budget.snapshot(phase="before_attempt")},
                    error_class="total_budget_exhausted",
                    reason="total request budget exhausted before the next resource attempt",
                    next_action="narrow_request_or_raise_total_budget",
                )
                attempts.append(attempt_with_recovery_decision(exhausted))
                emit("attempting", "failed", "total request budget exhausted", tool=tool, error_class="total_budget_exhausted")
                break
            effective_url, _effective_path = source_fields(effective_request)
            if tool == "generic_search" and effective_url:
                emit(
                    "attempting",
                    "skipped",
                    "generic search skipped because a prior source-selection step resolved the URL",
                    tool=tool,
                    selected_url=effective_url,
                )
                continue
            stage = stage_for_tool(effective_route, effective_request, tool)
            emit("attempting", "started", f"attempting {tool}", tool=tool, attempt_stage=stage)
            attempt = execute_local_attempt(
                effective_request,
                effective_route,
                tool=tool,
                stage=stage,
                index=len(attempts) + 1,
                network_gateway_plan=effective_network_gateway_plan,
                timeout_seconds=remaining_timeout,
            )
            attempt = attempt_with_recovery_decision(attempt)
            attempts.append(attempt)
            emit(
                "attempting",
                attempt.status,
                f"{tool} {attempt.status}",
                tool=tool,
                attempt_stage=stage,
                error_class=attempt.error_class,
            )
            if tool == "resource_source_strategy" and attempt.result.get("ok") and attempt.result.get("selected_url"):
                selected_url = str(attempt.result.get("selected_url") or "").strip()
                selected_name = str(attempt.result.get("selected_name") or "").strip()
                if source_selection_only(effective_request):
                    emit(
                        "source_selected",
                        "deferred",
                        "resource source selected; materialization deferred by request metadata",
                        selected_url=selected_url,
                        selected_source_id=attempt.result.get("selected_source_id", ""),
                    )
                    break
                metadata = dict(request.metadata or {})
                metadata["source_selection_result"] = {
                    "selected_url": selected_url,
                    "selected_name": selected_name,
                    "selected_source_id": attempt.result.get("selected_source_id", ""),
                    "candidate_count": len(attempt.result.get("candidates") or []),
                }
                effective_request = replace(
                    request,
                    url=selected_url,
                    name=request.name or selected_name,
                    intent=ResourceIntent.EXPLICIT_USER_URL if request.need_materialization else ResourceIntent.INLINE_URL_CANDIDATE,
                    metadata=metadata,
                )
                effective_route = route_for_request(effective_request)
                effective_network_gateway_plan = network_gateway_plan_for_request(
                    effective_request,
                    effective_route,
                    timeout_seconds=execution_budget.timeout_seconds(cap=effective_request.timeout_seconds),
                )
                emit(
                    "source_selected",
                    "ok",
                    "resource source selected for follow-up acquisition",
                    selected_url=selected_url,
                    selected_source_id=attempt.result.get("selected_source_id", ""),
                    route=effective_route.to_dict(),
                )
                continue
            if attempt.result.get("ok"):
                satisfaction = resource_result_satisfaction(
                    request=asdict(effective_request),
                    tool=tool,
                    result=attempt.result,
                ).to_dict()
                if satisfaction.get("satisfied"):
                    break
                degraded_result = dict(attempt.result or {})
                degraded_metadata = degraded_result.get("metadata") if isinstance(degraded_result.get("metadata"), dict) else {}
                degraded_result["metadata"] = {
                    **degraded_metadata,
                    "acceptance_decision": satisfaction,
                    "execution_budget": execution_budget.snapshot(phase=f"after_{tool}"),
                }
                attempt = attempt_with_recovery_decision(
                    replace(
                        attempt,
                        status="degraded",
                        result=degraded_result,
                        error_class=str(satisfaction.get("reason") or "resource_not_accepted"),
                        reason=str(satisfaction.get("reason") or "resource_not_accepted"),
                        next_action=str(satisfaction.get("next_action") or "try_next_route"),
                    )
                )
                attempts[-1] = attempt
                emit(
                    "evaluated",
                    "degraded",
                    f"{tool} ran successfully but did not satisfy the resource need",
                    tool=tool,
                    satisfaction=satisfaction,
                )
                continue
            if should_continue_after_attempt(asdict(attempt), need_materialization=effective_request.need_materialization):
                continue
            break
    receipt = receipt_from_attempts(request_id, request, route, attempts, events, network_gateway_plan, strategy_plan)
    receipt = replace(receipt, execution_budget=execution_budget.snapshot(phase="reported"))
    guidance_receipt_payload = {
        **asdict(receipt),
        "progress_events": events,
        "strategy_plan": strategy_plan,
    }
    codex_guidance = build_codex_guidance(asdict(request), guidance_receipt_payload)
    receipt = replace(receipt, codex_guidance=codex_guidance)
    emit("reported", receipt.status, "resource receipt produced", ok=receipt.ok, next_action=receipt.next_action)
    receipt_payload = {
        **asdict(receipt),
        "progress_events": events,
        "strategy_plan": strategy_plan,
    }
    persisted = persist_manifest(
        store_root=resolved_store_root,
        request_id=request_id,
        request=asdict(request),
        receipt=receipt_payload,
        events=events,
        strategy_plan=strategy_plan,
    )
    receipt = ResourceReceipt(**persisted)
    append_jsonl(receipt_log, asdict(receipt))
    upsert_request(
        request_id=request_id,
        request=asdict(request),
        receipt=asdict(receipt),
        manifest_path=str(receipt.manifest_path or ""),
        db_path=event_db_path,
    )
    if resource_log:
        for attempt in attempts:
            if attempt.tool in LOCAL_EXECUTABLE_TOOLS and attempt.executable and attempt.result and "source" in attempt.result:
                maybe_append_resource_result_log(resource_log, attempt.result)
    return receipt


def request_from_payload(payload: dict[str, Any]) -> ResourceBrokerRequest:
    allowed = {field.name for field in ResourceBrokerRequest.__dataclass_fields__.values()}
    clean = {key: value for key, value in payload.items() if key in allowed}
    return ResourceBrokerRequest(**clean)


def read_receipt(receipt_log: Path, request_id: str) -> dict[str, Any]:
    if not receipt_log.exists():
        return {"ok": False, "reason": "receipt_log_missing", "request_id": request_id}
    found: dict[str, Any] | None = None
    with receipt_log.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("request_id") == request_id:
                found = item
    return found or {"ok": False, "reason": "receipt_not_found", "request_id": request_id}


def _event_db_path_for_manifest(manifest_path: Path) -> Path:
    store_root = manifest_path.expanduser().resolve().parents[2]
    return (
        RECORD_INDEX_PATH
        if store_root == DEFAULT_STORE_ROOT.expanduser().resolve()
        else store_root / "_index" / "resource-observability.sqlite"
    )


def _upsert_manifest_receipt(manifest_path: Path, receipt: dict[str, Any]) -> None:
    manifest = read_manifest(manifest_path)
    request = manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
    request_id = str(manifest.get("request_id") or receipt.get("request_id") or "")
    if request_id:
        upsert_request(
            request_id=request_id,
            request=request,
            receipt=receipt,
            manifest_path=str(manifest_path),
            db_path=_event_db_path_for_manifest(manifest_path),
        )


def attach_result_to_request(
    *,
    request_id: str,
    source_tool: str,
    result_kind: str,
    content: str = "",
    artifact_path: str = "",
    metadata: dict[str, Any] | None = None,
    receipt_log: Path = DEFAULT_RECEIPT_LOG,
) -> dict[str, Any]:
    receipt = read_receipt(receipt_log, request_id)
    manifest_path = str(receipt.get("manifest_path") or "")
    if not manifest_path:
        return {"ok": False, "reason": "manifest_path_missing", "request_id": request_id}
    updated = attach_owner_result(
        manifest_path=Path(manifest_path),
        source_tool=source_tool,
        result_kind=result_kind,
        content=content,
        artifact_path=artifact_path,
        metadata=metadata or {},
    )
    append_jsonl(receipt_log, updated)
    _upsert_manifest_receipt(Path(manifest_path), updated)
    return updated


def mark_request_consumed(
    *,
    request_id: str,
    consumed_path: str = "",
    no_read_needed_reason: str = "",
    consumer: str = "codex",
    receipt_log: Path = DEFAULT_RECEIPT_LOG,
) -> dict[str, Any]:
    receipt = read_receipt(receipt_log, request_id)
    manifest_path_text = str(receipt.get("manifest_path") or "")
    if not manifest_path_text:
        return {"ok": False, "reason": "manifest_path_missing", "request_id": request_id}
    manifest_path = Path(manifest_path_text).expanduser().resolve()
    updated = mark_consumed(
        manifest_path=manifest_path,
        consumed_path=consumed_path,
        no_read_needed_reason=no_read_needed_reason,
        consumer=consumer,
    )
    if not updated.get("request_id") or not updated.get("consumption"):
        return updated
    append_jsonl(receipt_log, updated)
    _upsert_manifest_receipt(manifest_path, updated)
    return updated


def validate() -> dict[str, Any]:
    source_selection_result = {
        "ok": True,
        "status": "completed",
        "source": "resource_source_executor",
        "selected": {"url": "https://example.com/"},
        "candidates": [{"url": "https://example.com/"}],
    }
    owner_metadata_result = {
        "status": "completed",
        "source": "github",
        "result_kind": "github_repository_search",
        "items": [],
    }
    acquisition_result = {
        "ok": True,
        "source": "local_file",
        "stored_path": "C:\\resources\\example.txt",
        "sha256": "0" * 64,
        "size": 1,
    }
    explicit_url_request = ResourceBrokerRequest(
        url="https://example.com/report.html",
        intent=ResourceIntent.EXPLICIT_USER_URL,
        need_materialization=True,
        allow_filesystem_write=True,
    )
    explicit_url_route = route_for_request(explicit_url_request)
    explicit_url_tools = candidate_tools(explicit_url_route, explicit_url_request)
    explicit_url_plan = strategy_plan_for_request(explicit_url_request, explicit_url_route)
    with tempfile.TemporaryDirectory(prefix="resource-broker-validate-") as tmp:
        log_path = Path(tmp) / "resource-fetcher.jsonl"
        maybe_append_resource_result_log(log_path, source_selection_result)
        maybe_append_resource_result_log(log_path, owner_metadata_result)
        maybe_append_resource_result_log(log_path, acquisition_result)
        lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    cases = {
        "source_selection_not_loggable": not is_resource_result_loggable(source_selection_result),
        "owner_metadata_not_loggable": not is_resource_result_loggable(owner_metadata_result),
        "acquisition_loggable": is_resource_result_loggable(acquisition_result),
        "only_acquisition_appended": len(lines) == 1,
        "explicit_url_materialization_uses_resource_cli_first": explicit_url_tools[:1] == ["resource_cli"],
        "explicit_url_materialization_first_stage_is_materialize": bool(explicit_url_plan)
        and explicit_url_plan[0].get("tool") == "resource_cli"
        and explicit_url_plan[0].get("stage") == ResourceStage.MATERIALIZE,
    }
    issues = [{"code": key} for key, ok in cases.items() if not ok]
    return {
        "schema": "resource_broker.validate.v1",
        "ok": not issues,
        "cases": cases,
        "issues": issues,
        "rule": "resource-fetcher log accepts only ResourceResult-shaped local acquisition records; explicit URL materialization routes to resource_cli before owner discovery tools",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resource broker request/status interface")
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request")
    request.add_argument("--json-payload", default="", help="Inline JSON request payload.")
    request.add_argument("--payload-file", default="", help="JSON request payload file.")
    request.add_argument("--target", default="")
    request.add_argument("--url", default="")
    request.add_argument("--path", default="")
    request.add_argument("--task", default="")
    request.add_argument("--name", default="")
    request.add_argument("--intent", default=ResourceIntent.UNKNOWN)
    request.add_argument("--need-materialization", action="store_true")
    request.add_argument("--allow-network", action=argparse.BooleanOptionalAction, default=True)
    request.add_argument("--allow-filesystem-write", action=argparse.BooleanOptionalAction, default=False)
    request.add_argument("--target-dir", default="")
    request.add_argument("--max-bytes", type=int, default=None)
    request.add_argument("--auto-owner", action="store_true", help="Run supported read-only owner executors before handoff.")
    request.add_argument("--owner-execution-mode", default="read_only", choices=("read_only",))
    request.add_argument("--event-log", default=str(DEFAULT_EVENT_LOG))
    request.add_argument("--receipt-log", default=str(DEFAULT_RECEIPT_LOG))
    request.add_argument("--store-root", default=str(DEFAULT_STORE_ROOT))
    request.add_argument("--resource-log", default="")
    request.add_argument("--json", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("--request-id", required=True)
    status.add_argument("--receipt-log", default=str(DEFAULT_RECEIPT_LOG))
    status.add_argument("--json", action="store_true")

    attach = sub.add_parser("attach-result")
    attach.add_argument("--request-id", required=True)
    attach.add_argument("--source-tool", required=True)
    attach.add_argument("--result-kind", default="owner_result")
    attach.add_argument("--content", default="")
    attach.add_argument("--content-file", default="")
    attach.add_argument("--artifact-path", default="")
    attach.add_argument("--metadata-json", default="")
    attach.add_argument("--receipt-log", default=str(DEFAULT_RECEIPT_LOG))
    attach.add_argument("--json", action="store_true")
    sub.add_parser("validate")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        payload = read_receipt(Path(args.receipt_log).expanduser().resolve(), args.request_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.json else None, sort_keys=True))
        return 0 if payload.get("ok") is not False or payload.get("status") else 1
    if args.command == "attach-result":
        content = args.content
        if args.content_file:
            content = Path(args.content_file).read_text(encoding="utf-8")
        metadata = json.loads(args.metadata_json) if args.metadata_json else {}
        payload = attach_result_to_request(
            request_id=args.request_id,
            source_tool=args.source_tool,
            result_kind=args.result_kind,
            content=content,
            artifact_path=args.artifact_path,
            metadata=metadata,
            receipt_log=Path(args.receipt_log).expanduser().resolve(),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.json else None, sort_keys=True))
        return 0 if payload.get("ok") else 1
    if args.command == "validate":
        payload = validate()
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("ok") else 1
    if args.json_payload:
        payload = json.loads(args.json_payload)
        request = request_from_payload(payload)
    elif args.payload_file:
        payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
        request = request_from_payload(payload)
    else:
        request = ResourceBrokerRequest(
            target=args.target,
            url=args.url,
            path=args.path,
            task=args.task,
            name=args.name,
            intent=args.intent,
            need_materialization=bool(args.need_materialization),
            allow_network=bool(args.allow_network),
            allow_filesystem_write=bool(args.allow_filesystem_write),
            max_bytes=args.max_bytes,
            target_dir=args.target_dir,
            auto_owner=bool(args.auto_owner),
            owner_execution_mode=args.owner_execution_mode,
        )
    receipt = handle_request(
        request,
        event_log=Path(args.event_log).expanduser().resolve(),
        receipt_log=Path(args.receipt_log).expanduser().resolve(),
        resource_log=Path(args.resource_log).expanduser().resolve() if args.resource_log else None,
        store_root=Path(args.store_root).expanduser().resolve(),
    )
    print(json.dumps(asdict(receipt), ensure_ascii=False, indent=2 if not args.json else None, sort_keys=True))
    return 0 if receipt.ok or receipt.status == "handoff_required" else 1


if __name__ == "__main__":
    raise SystemExit(main())
