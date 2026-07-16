#!/usr/bin/env python3
"""Read-only owner-tool execution adapters for resource requests.

Ownership: execute safe, bounded owner read operations after the resource
broker has classified a request and obtained a network execution package.
Non-goals: calling current-turn MCP namespaces, installing packages, cloning
repositories, logging in, mutating remote state, or changing global proxy/DNS.
State behavior: read-only network/process execution; no persistent writes.
Caller context: `resource_broker.py` uses this module when a request explicitly
enables automatic owner execution.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from resource_execution_budget import ResourceExecutionBudget
from resource_network_execution import execution_package_from_gateway_plan
from resource_owner_hub_adapter import call_hub_tool, call_mcp_gateway_tool, mcp_json_content, mcp_text_content
from resource_owner_result_disk_cache import read_disk_owner_result_cache, write_disk_owner_result_cache
from resource_owner_result_normalizer import normalize_owner_result
from resource_owner_tool_registry import READ_ONLY_EXECUTABLE_OWNER_TOOLS
from resource_package_owner import (
    execute_package_metadata as execute_package_metadata_adapter,
    validate as validate_package_owner,
)
from resource_request_runtime_cache import read_owner_result_cache, write_owner_result_cache
from resource_strategy_policy import resource_result_satisfaction
from resource_validation_profile import metadata_profile
from resource_youtube_feed_owner import execute_youtube_feed, validate as validate_youtube_feed_owner
from structured_task_envelope import resource_contract_from_metadata


SUPPORTED_OWNER_TOOLS = READ_ONLY_EXECUTABLE_OWNER_TOOLS


def _json_result(**payload: Any) -> dict[str, Any]:
    payload.setdefault("schema", "resource_owner_executor.result.v1")
    payload.setdefault("writes_files", False)
    payload.setdefault("writes_remote_state", False)
    payload.setdefault("permission_boundary", "owner_read_only")
    return payload


def _normalized_json_result(**payload: Any) -> dict[str, Any]:
    return normalize_owner_result(_json_result(**payload))


def supports_owner_execution(tool: str, mode: str = "read_only") -> bool:
    return mode == "read_only" and tool in SUPPORTED_OWNER_TOOLS


def _proxy_handler_from_package(package: dict[str, Any]) -> urllib.request.ProxyHandler:
    proxy_url = str(package.get("proxy_url") or "")
    route_mode = str(package.get("route_mode") or "")
    if route_mode in {"probe_selected_direct", "direct"}:
        return urllib.request.ProxyHandler({})
    if proxy_url:
        return urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    return urllib.request.ProxyHandler()


def _open_json_url(url: str, package: dict[str, Any], timeout: int) -> dict[str, Any]:
    opener = urllib.request.build_opener(_proxy_handler_from_package(package))
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "codex-resource-layer"})
    with opener.open(request, timeout=max(1, min(timeout, 30))) as response:
        body = response.read(512_000)
        return json.loads(body.decode("utf-8", errors="replace"))


def _direct_package_from(package: dict[str, Any]) -> dict[str, Any]:
    direct = dict(package)
    direct["route_mode"] = "probe_selected_direct"
    direct["proxy_url"] = ""
    direct["env"] = {
        key: value
        for key, value in (package.get("env") or {}).items()
        if str(key).upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    direct["unset_env"] = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
    return direct


def _github_packages_to_try(gateway_plan: dict[str, Any], package: dict[str, Any]) -> list[dict[str, Any]]:
    packages = [package]
    probe = gateway_plan.get("probe") if isinstance(gateway_plan.get("probe"), dict) else {}
    direct = probe.get("direct") if isinstance(probe.get("direct"), dict) else {}
    if package.get("route_mode") != "probe_selected_direct" and direct.get("ok"):
        packages.append(_direct_package_from(package))
    return packages


def _github_repo_from_url(url: str, target: str) -> tuple[str, str] | None:
    text = str(url or target or "").strip()
    if not text:
        return None
    if "://" not in text:
        if re.search(r"\s", text):
            return None
        if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$", text):
            return None
    parsed = urllib.parse.urlparse(text if "://" in text else f"https://github.com/{text}")
    if parsed.netloc.lower() not in {"github.com", "www.github.com", "api.github.com"}:
        return None
    if re.search(r"\s", parsed.path):
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.netloc.lower() == "api.github.com" and len(parts) >= 3 and parts[0] == "repos":
        return parts[1], parts[2]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


def _github_search_query_from_request(request: dict[str, Any]) -> str:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    explicit = str(metadata.get("github_search_query") or metadata.get("search_query") or "").strip()
    if explicit:
        return _compact_github_search_query(explicit)
    target = str(request.get("target") or "").strip()
    task = str(request.get("task") or "").strip()
    candidate = target or task
    if not candidate:
        return ""
    if _github_repo_from_url("", candidate):
        return ""
    lowered = f"{task} {target}".lower()
    search_tokens = ("search", "find", "repository", "repositories", "repo", "github", "项目", "仓库", "搜索", "查找", "候选")
    if any(token in lowered for token in search_tokens):
        return _compact_github_search_query(candidate)
    return ""


def _compact_github_search_query(query: str) -> str:
    """Turn a natural-language resource target into a bounded GitHub search query."""

    text = str(query or "").lower()
    tokens = re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", text)
    stopwords = {
        "and",
        "for",
        "the",
        "with",
        "from",
        "into",
        "about",
        "mature",
        "practices",
        "resource",
        "resources",
        "agent",
        "agents",
        "work",
        "environments",
        "search",
        "github",
        "repository",
        "repositories",
        "project",
        "projects",
    }
    selected: list[str] = []
    for token in tokens:
        token = token.strip("._-")
        if len(token) < 3 or token in stopwords or token.isdigit():
            continue
        if token not in selected:
            selected.append(token)
        if len(selected) >= 12:
            break
    compact = " ".join(selected).strip()
    if not compact:
        compact = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff ]+", " ", str(query or ""))).strip()
    return compact[:180].strip()


def _github_search_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    normalized: list[dict[str, Any]] = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "full_name": item.get("full_name") or "",
                "description": item.get("description") or "",
                "html_url": item.get("html_url") or "",
                "language": item.get("language") or "",
                "stars": item.get("stargazers_count", 0),
                "forks": item.get("forks_count", 0),
                "open_issues": item.get("open_issues_count", 0),
                "updated_at": item.get("updated_at") or "",
                "topics": item.get("topics") if isinstance(item.get("topics"), list) else [],
            }
        )
    return normalized


def _github_search_content(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        name = item.get("full_name") or "unknown"
        url = item.get("html_url") or ""
        stars = item.get("stars", 0)
        language = item.get("language") or ""
        description = item.get("description") or ""
        lines.append(f"{index}. {name} ({stars} stars, {language})")
        if url:
            lines.append(f"   {url}")
        if description:
            lines.append(f"   {description}")
    return "\n".join(lines)


def execute_github_repository_search(request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int) -> dict[str, Any]:
    query = _github_search_query_from_request(request)
    if not query:
        return _json_result(ok=False, status="handoff_required", reason="github_repo_not_identified")
    package = execution_package_from_gateway_plan(gateway_plan)
    if not package.get("ok"):
        return _json_result(ok=False, status="handoff_required", reason="network_package_unavailable")
    per_page = max(1, min(int((request.get("metadata") if isinstance(request.get("metadata"), dict) else {}).get("per_page") or 10), 25))
    hub_payload = call_hub_tool(
        "github.api",
        {
            "method": "GET",
            "path": "/search/repositories",
            "query": {"q": query, "sort": "stars", "order": "desc", "per_page": per_page},
            "timeout_seconds": max(1, min(timeout, 120)),
        },
        timeout=timeout,
    )
    errors: list[dict[str, str]] = []
    if hub_payload.get("ok") and isinstance(hub_payload.get("result"), dict):
        payload = hub_payload["result"]
        items = _github_search_items(payload)
        return _json_result(
            ok=True,
            status="completed",
            source="github",
            result_kind="github_repository_search",
            content=_github_search_content(items),
            metadata={
                "query": query,
                "total_count": payload.get("total_count", len(items)),
                "incomplete_results": bool(payload.get("incomplete_results")),
                "items": items,
                "owner_execution_route": "local_hub_github_api",
                "hub_tool": "github.api",
                "hub_transport": hub_payload.get("hub_transport", "local_http_mcp_hub"),
                "token_source": hub_payload.get("token_source", ""),
                "rate_limit_remaining": hub_payload.get("rate_limit_remaining", ""),
                "network_route_mode": package.get("route_mode", ""),
                "network_target_kind": package.get("target_kind", ""),
            },
            next_action="consume_resource",
        )
    errors.append(
        {
            "route_mode": "local_hub_github_api",
            "error_class": str(hub_payload.get("reason") or hub_payload.get("status") or "hub_github_api_failed"),
            "reason": str(hub_payload.get("reason") or hub_payload.get("body") or hub_payload.get("error") or "")[:500],
        }
    )
    api_url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": query, "sort": "stars", "order": "desc", "per_page": per_page}
    )
    payload: dict[str, Any] | None = None
    used_package = package
    for candidate in _github_packages_to_try(gateway_plan, package):
        used_package = candidate
        try:
            payload = _open_json_url(api_url, candidate, timeout)
            break
        except urllib.error.HTTPError as exc:
            errors.append({"route_mode": str(candidate.get("route_mode") or ""), "error_class": "http_status", "reason": f"http_status={exc.code}"})
            if exc.code == 422:
                return _json_result(
                    ok=False,
                    status="degraded",
                    error_class="insufficient_coverage",
                    reason="github_search_query_rejected",
                    attempted_routes=errors,
                    metadata={"query": query, "http_status": 422},
                    next_action="refine_resource_delegation_and_retry",
                )
        except Exception as exc:
            errors.append({"route_mode": str(candidate.get("route_mode") or ""), "error_class": type(exc).__name__, "reason": str(exc)})
    if payload is None:
        last = errors[-1] if errors else {"error_class": "unknown", "reason": "github_repository_search_failed"}
        return _json_result(
            ok=False,
            status="failed",
            error_class=last.get("error_class", "unknown"),
            reason=last.get("reason", "github_repository_search_failed"),
            attempted_routes=errors,
        )
    items = _github_search_items(payload)
    return _json_result(
        ok=True,
        status="completed",
        source="github",
        result_kind="github_repository_search",
        content=_github_search_content(items),
        metadata={
            "query": query,
            "total_count": payload.get("total_count", len(items)),
            "incomplete_results": bool(payload.get("incomplete_results")),
            "items": items,
            "owner_execution_route": "direct_github_api_readonly_fallback",
            "network_route_mode": used_package.get("route_mode", ""),
            "network_target_kind": package.get("target_kind", ""),
            "attempted_routes": errors + [{"route_mode": str(used_package.get("route_mode") or ""), "ok": "true"}],
        },
        next_action="consume_resource",
    )


def execute_github_metadata(request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int) -> dict[str, Any]:
    repo = _github_repo_from_url(str(request.get("url") or ""), str(request.get("target") or ""))
    if not repo:
        return execute_github_repository_search(request, gateway_plan, timeout)
    owner, name = repo
    package = execution_package_from_gateway_plan(gateway_plan)
    if not package.get("ok"):
        return _json_result(ok=False, status="handoff_required", reason="network_package_unavailable")
    hub_payload = call_hub_tool(
        "github.api",
        {"method": "GET", "path": f"/repos/{owner}/{name}", "timeout_seconds": max(1, min(timeout, 120))},
        timeout=timeout,
    )
    if hub_payload.get("ok") and isinstance(hub_payload.get("result"), dict):
        payload = hub_payload["result"]
        return _json_result(
            ok=True,
            status="completed",
            source="github",
            result_kind="github_repo_metadata",
            content="",
            metadata={
                "full_name": payload.get("full_name", f"{owner}/{name}"),
                "description": payload.get("description", ""),
                "html_url": payload.get("html_url", f"https://github.com/{owner}/{name}"),
                "default_branch": payload.get("default_branch", ""),
                "stars": payload.get("stargazers_count", 0),
                "forks": payload.get("forks_count", 0),
                "open_issues": payload.get("open_issues_count", 0),
                "license": (payload.get("license") or {}).get("spdx_id", ""),
                "owner_execution_route": "local_hub_github_api",
                "hub_tool": "github.api",
                "hub_transport": hub_payload.get("hub_transport", "local_http_mcp_hub"),
                "token_source": hub_payload.get("token_source", ""),
                "rate_limit_remaining": hub_payload.get("rate_limit_remaining", ""),
                "network_route_mode": package.get("route_mode", ""),
                "network_target_kind": package.get("target_kind", ""),
            },
            next_action="consume_resource",
        )
    api_url = f"https://api.github.com/repos/{owner}/{name}"
    errors: list[dict[str, str]] = [
        {
            "route_mode": "local_hub_github_api",
            "error_class": str(hub_payload.get("reason") or hub_payload.get("status") or "hub_github_api_failed"),
            "reason": str(hub_payload.get("reason") or hub_payload.get("body") or hub_payload.get("error") or "")[:500],
        }
    ]
    payload: dict[str, Any] | None = None
    used_package = package
    for candidate in _github_packages_to_try(gateway_plan, package):
        used_package = candidate
        try:
            payload = _open_json_url(api_url, candidate, timeout)
            break
        except urllib.error.HTTPError as exc:
            errors.append({"route_mode": str(candidate.get("route_mode") or ""), "error_class": "http_status", "reason": f"http_status={exc.code}"})
        except Exception as exc:
            errors.append({"route_mode": str(candidate.get("route_mode") or ""), "error_class": type(exc).__name__, "reason": str(exc)})
    if payload is None:
        last = errors[-1] if errors else {"error_class": "unknown", "reason": "github_metadata_failed"}
        return _json_result(
            ok=False,
            status="failed",
            error_class=last.get("error_class", "unknown"),
            reason=last.get("reason", "github_metadata_failed"),
            attempted_routes=errors,
        )
    return _json_result(
        ok=True,
        status="completed",
        source="github",
        result_kind="github_repo_metadata",
        content="",
        metadata={
            "full_name": payload.get("full_name", f"{owner}/{name}"),
            "description": payload.get("description", ""),
            "html_url": payload.get("html_url", f"https://github.com/{owner}/{name}"),
            "default_branch": payload.get("default_branch", ""),
            "stars": payload.get("stargazers_count", 0),
            "forks": payload.get("forks_count", 0),
            "open_issues": payload.get("open_issues_count", 0),
            "license": (payload.get("license") or {}).get("spdx_id", ""),
            "owner_execution_route": "direct_github_api_readonly_fallback",
            "network_route_mode": used_package.get("route_mode", ""),
            "network_target_kind": package.get("target_kind", ""),
            "attempted_routes": errors + [{"route_mode": str(used_package.get("route_mode") or ""), "ok": "true"}],
        },
        next_action="consume_resource",
    )


def execute_package_metadata(request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int) -> dict[str, Any]:
    return execute_package_metadata_adapter(request, gateway_plan, timeout, _json_result)


def execute_generic_search(request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int) -> dict[str, Any]:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    custom = metadata.get("custom_delegation") if isinstance(metadata.get("custom_delegation"), dict) else {}
    constraints = custom.get("constraints") if isinstance(custom.get("constraints"), dict) else {}
    query = _request_text(request)
    if not query:
        return _normalized_json_result(ok=False, status="deferred", reason="generic_search_query_missing", next_action="refine_resource_delegation_and_retry")
    resource_kind = str(metadata.get("resource_kind_hint") or "").strip().lower()
    envelope = resource_contract_from_metadata(metadata)
    resource = envelope.get("resource", {}) if envelope else {}
    source_policy = resource.get("source_policy", {}) if isinstance(resource, dict) else {}
    structured_domains = source_policy.get("domains") if isinstance(source_policy.get("domains"), list) else []
    compatibility_domains = metadata.get("source_domains") if isinstance(metadata.get("source_domains"), list) else []
    domain_candidates = [
        str(constraints.get("site_or_domain") or metadata.get("site_or_domain") or "").strip(),
        *(str(value or "").strip() for value in structured_domains),
        *(str(value or "").strip() for value in compatibility_domains),
    ]
    site_or_domain = next((value for value in domain_candidates if value), "")
    tool_kind = {
        "image": "images",
        "news": "news",
        "video": "videos",
        "book": "books",
    }.get(resource_kind, "text")
    package = execution_package_from_gateway_plan(gateway_plan)
    if not package.get("ok"):
        return _normalized_json_result(ok=False, status="handoff_required", reason="network_package_unavailable", next_action="refresh_network_route_and_retry")
    arguments = {
        "query": query,
        "region": str(constraints.get("region") or metadata.get("region") or "wt-wt"),
        "safesearch": str(constraints.get("safesearch") or metadata.get("safesearch") or "moderate"),
        "timelimit": str(constraints.get("timelimit") or metadata.get("timelimit") or ""),
        "max_results": max(1, min(int(metadata.get("max_results") or 10), 20)),
        "backend": str(constraints.get("search_backend") or metadata.get("search_backend") or "auto"),
        "site_or_domain": site_or_domain,
        "proxy_url": str(package.get("proxy_url") or ""),
        "route_mode": str(package.get("route_mode") or ""),
        "timeout_seconds": max(1, min(timeout, 30)),
    }
    payload = call_hub_tool(f"resource_search.{tool_kind}", arguments, timeout=max(5, min(timeout + 5, 40)))
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    if payload.get("ok") and results:
        candidates: list[dict[str, Any]] = []
        for item in results[:20]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("href") or item.get("url") or item.get("content") or item.get("image") or "").strip()
            candidates.append(
                {
                    "title": str(item.get("title") or item.get("name") or url),
                    "url": url,
                    "landing_url": url,
                    "summary": str(item.get("body") or item.get("description") or item.get("snippet") or "")[:1000],
                    "source": str(item.get("source") or item.get("publisher") or "ddgs"),
                    "source_id": url or str(item.get("title") or ""),
                    "resource_kind": resource_kind or "generic_web",
                }
            )
        return _normalized_json_result(
            ok=True,
            status="completed",
            source="generic_search",
            result_kind=f"generic_{tool_kind}_search",
            content=json.dumps({"query": query, "candidates": candidates}, ensure_ascii=False),
            candidates=candidates,
            metadata={
                "query": query,
                "result_count": len(candidates),
                "top_url": str((candidates[0] if candidates else {}).get("url") or ""),
                "items": candidates,
                "owner_execution_route": payload.get("hub_transport", "local_http_mcp_hub"),
                "backend": payload.get("backend", "ddgs"),
                "route_mode": package.get("route_mode", ""),
            },
            next_action="consume_resource",
        )
    status = "handoff_required" if payload.get("reason") in {"hub_unreachable", "hub_initialize_error", "hub_tool_error"} else "failed"
    return _normalized_json_result(
        ok=False,
        status=status,
        source="generic_search",
        error_class=str(payload.get("error_class") or payload.get("reason") or payload.get("status") or "generic_search_failed"),
        reason=str(payload.get("reason") or "generic search returned no usable results"),
        metadata={"hub_payload": payload, "query": query, "route_mode": package.get("route_mode", "")},
        next_action="refresh_network_route_and_retry" if status == "failed" else "continue_resource_layer_handoff_or_attach_result",
    )


def _text_request_target(request: dict[str, Any]) -> tuple[str, str, str]:
    url = str(request.get("url") or "").strip()
    path = str(request.get("path") or "").strip()
    target = str(request.get("target") or "").strip()
    if not url and target.startswith(("http://", "https://")):
        url = target
    if not path and target and not url and ("/" in target or "\\" in target or ":" in target):
        path = target
    text = _request_text(request)
    return url, path, text


def _completed_owner_text(
    *,
    source: str,
    result_kind: str,
    content: str,
    gateway_payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _normalized_json_result(
        ok=True,
        status="completed",
        source=source,
        result_kind=result_kind,
        content=content[:12000],
        metadata={
            **(metadata or {}),
            "owner_execution_route": gateway_payload.get("owner_execution_route", "hub_mcp_gateway_call"),
            "profile": gateway_payload.get("profile") or gateway_payload.get("owner_profile") or source,
            "gateway_status": gateway_payload.get("gateway_status", ""),
            "transport_isolated_from_current_turn": bool(gateway_payload.get("transport_isolated_from_current_turn", True)),
            "permission_boundary": gateway_payload.get("permission_boundary", "owner_read_only_fresh_stdio_gateway"),
        },
        next_action="consume_resource",
    )


def _owner_gateway_handoff(source: str, gateway_payload: dict[str, Any], *, reason: str = "") -> dict[str, Any]:
    return _normalized_json_result(
        ok=False,
        status="handoff_required",
        source=source,
        error_class=str(gateway_payload.get("reason") or gateway_payload.get("gateway_status") or "owner_gateway_unavailable"),
        reason=reason or str(gateway_payload.get("reason") or "owner gateway did not return usable content"),
        metadata={
            "owner_execution_route": gateway_payload.get("owner_execution_route", ""),
            "gateway_status": gateway_payload.get("gateway_status", ""),
            "hub_attempt": gateway_payload.get("hub_attempt", {}),
            "permission_boundary": gateway_payload.get("permission_boundary", "owner_read_only_fresh_stdio_gateway"),
        },
        next_action="use_codex_current_turn_owner_tool",
    )


def execute_microsoftdocs(request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int) -> dict[str, Any]:
    _ = gateway_plan
    budget = ResourceExecutionBudget.start(timeout)
    url, _path, text = _text_request_target(request)
    query = text or url or "Microsoft documentation lookup"
    if "learn.microsoft.com" in url:
        call_timeout = budget.timeout_seconds(cap=timeout)
        if call_timeout <= 0:
            return _normalized_json_result(ok=False, status="failed", source="microsoftdocs", error_class="total_budget_exhausted", reason="total request budget exhausted before Microsoft Docs fetch")
        payload = call_mcp_gateway_tool("microsoftdocs", "microsoft_docs_fetch", {"url": url}, timeout=call_timeout)
        content = mcp_text_content(payload)
        if payload.get("ok") and content:
            return _completed_owner_text(
                source="microsoftdocs",
                result_kind="microsoft_docs_fetch",
                content=content,
                gateway_payload=payload,
                metadata={"url": url},
            )
    call_timeout = budget.timeout_seconds(cap=timeout)
    if call_timeout <= 0:
        return _normalized_json_result(ok=False, status="failed", source="microsoftdocs", error_class="total_budget_exhausted", reason="total request budget exhausted before Microsoft Docs search")
    payload = call_mcp_gateway_tool("microsoftdocs", "microsoft_docs_search", {"query": query}, timeout=call_timeout)
    content_json = mcp_json_content(payload)
    content = mcp_text_content(payload)
    if payload.get("ok") and (content_json or content):
        results = content_json.get("results") if isinstance(content_json.get("results"), list) else []
        if isinstance(content_json.get("results"), list) and not results:
            return _normalized_json_result(
                ok=False,
                status="deferred",
                source="microsoftdocs",
                result_kind="microsoft_docs_search",
                error_class="empty_owner_result",
                reason="Microsoft Docs search returned zero usable results",
                metadata={"query": query, "result_count": 0},
                next_action="refine_resource_delegation_and_retry",
            )
        return _completed_owner_text(
            source="microsoftdocs",
            result_kind="microsoft_docs_search",
            content=content or json.dumps(content_json, ensure_ascii=False),
            gateway_payload=payload,
            metadata={
                "query": query,
                "result_count": len(results),
                "top_url": str((results[0] or {}).get("contentUrl") or "") if results else "",
            },
        )
    return _owner_gateway_handoff("microsoftdocs", payload)


def execute_context7(request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int) -> dict[str, Any]:
    _ = gateway_plan
    budget = ResourceExecutionBudget.start(timeout)
    _url, _path, text = _text_request_target(request)
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    library_id = str(metadata.get("library_id") or "").strip()
    query = text or str(request.get("name") or request.get("target") or "").strip() or "documentation lookup"
    if library_id.startswith("/"):
        call_timeout = budget.timeout_seconds(cap=timeout)
        if call_timeout <= 0:
            return _normalized_json_result(ok=False, status="failed", source="context7", error_class="total_budget_exhausted", reason="total request budget exhausted before Context7 docs query")
        payload = call_mcp_gateway_tool("context7", "query_docs", {"libraryId": library_id, "query": query}, timeout=call_timeout)
        content = mcp_text_content(payload)
        if payload.get("ok") and content:
            return _completed_owner_text(
                source="context7",
                result_kind="context7_docs",
                content=content,
                gateway_payload=payload,
                metadata={"library_id": library_id, "query": query},
            )
        return _owner_gateway_handoff("context7", payload)
    library_name = str(metadata.get("library_name") or request.get("name") or request.get("target") or "").strip() or query.split()[0]
    resolution_cap = max(1, min(timeout, int(max(1, timeout) * 0.4)))
    call_timeout = budget.timeout_seconds(cap=resolution_cap)
    if call_timeout <= 0:
        return _normalized_json_result(ok=False, status="failed", source="context7", error_class="total_budget_exhausted", reason="total request budget exhausted before Context7 library resolution")
    payload = call_mcp_gateway_tool("context7", "resolve_library_id", {"libraryName": library_name, "query": query}, timeout=call_timeout)
    resolve_content = mcp_text_content(payload)
    resolved = _first_context7_library_id(resolve_content)
    if payload.get("ok") and resolved:
        call_timeout = budget.timeout_seconds(cap=timeout)
        if call_timeout <= 0:
            return _normalized_json_result(
                ok=False,
                status="failed",
                source="context7",
                error_class="total_budget_exhausted",
                reason="total request budget exhausted after Context7 library resolution",
                metadata={"library_id": resolved, "query": query, "budget": budget.snapshot(phase="query_docs")},
                next_action="narrow_request_or_raise_total_budget",
            )
        docs_payload = call_mcp_gateway_tool("context7", "query_docs", {"libraryId": resolved, "query": query}, timeout=call_timeout)
        docs_content = mcp_text_content(docs_payload)
        if docs_payload.get("ok") and docs_content:
            return _completed_owner_text(
                source="context7",
                result_kind="context7_docs",
                content=docs_content,
                gateway_payload=docs_payload,
                metadata={"library_id": resolved, "query": query, "resolved_from": library_name},
            )
        return _owner_gateway_handoff("context7", docs_payload, reason="context7 resolved a library but docs query returned no usable content")
    if payload.get("ok") and resolve_content:
        return _normalized_json_result(
            ok=False,
            status="deferred",
            source="context7",
            result_kind="context7_library_resolution",
            content=resolve_content,
            error_class="context7_library_id_unresolved",
            reason="Context7 returned resolver output but no machine-usable library ID",
            metadata={
                "query": query,
                "owner_execution_route": payload.get("owner_execution_route", "hub_mcp_gateway_call"),
                "gateway_status": payload.get("gateway_status", ""),
            },
            next_action="refine_library_name_or_select_library_id_then_query_docs",
        )
    return _owner_gateway_handoff("context7", payload)


def execute_markitdown(request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int) -> dict[str, Any]:
    _ = gateway_plan
    url, path, text = _text_request_target(request)
    uri = url or path
    if not uri:
        return _json_result(ok=False, status="handoff_required", reason="markitdown_uri_missing", next_action="provide_url_or_path")
    payload = call_mcp_gateway_tool("markitdown", "convert_to_markdown", {"uri": uri}, timeout=timeout)
    content = mcp_text_content(payload)
    if payload.get("ok") and content:
        return _completed_owner_text(
            source="markitdown",
            result_kind="markdown",
            content=content,
            gateway_payload=payload,
            metadata={"uri": uri, "task": text},
        )
    return _owner_gateway_handoff("markitdown", payload)


def execute_playwright(request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int) -> dict[str, Any]:
    _ = gateway_plan
    url, _path, text = _text_request_target(request)
    if not url:
        return _json_result(ok=False, status="handoff_required", reason="playwright_url_missing", next_action="provide_url")
    payload = call_mcp_gateway_tool("playwright", "browser_navigate", {"url": url}, timeout=timeout)
    content = mcp_text_content(payload)
    if payload.get("ok") and content:
        return _completed_owner_text(
            source="playwright",
            result_kind="browser_evidence",
            content=content,
            gateway_payload=payload,
            metadata={"url": url, "task": text},
        )
    return _owner_gateway_handoff("playwright", payload)


def execute_chrome_devtools(request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int) -> dict[str, Any]:
    _ = gateway_plan
    url, _path, text = _text_request_target(request)
    tool = "new_page" if url else "list_pages"
    arguments = {"url": url} if url else {}
    payload = call_mcp_gateway_tool("chrome-devtools", tool, arguments, timeout=timeout)
    content = mcp_text_content(payload)
    if payload.get("ok") and content:
        return _completed_owner_text(
            source="chrome-devtools",
            result_kind="browser_devtools_evidence",
            content=content,
            gateway_payload=payload,
            metadata={"url": url, "task": text, "devtools_tool": tool},
        )
    return _owner_gateway_handoff("chrome-devtools", payload)


def execute_owner_tool(
    *,
    tool: str,
    request: dict[str, Any],
    gateway_plan: dict[str, Any],
    timeout: int = 20,
    mode: str = "read_only",
) -> dict[str, Any]:
    if not supports_owner_execution(tool, mode):
        return normalize_owner_result(_json_result(ok=False, status="handoff_required", reason="owner_tool_not_supported_for_local_read_only_execution"))
    profile = metadata_profile(request.get("metadata") if isinstance(request.get("metadata"), dict) else {})
    cached = read_owner_result_cache(tool, request, ttl_seconds=profile.owner_result_cache_ttl_seconds)
    if cached:
        return normalize_owner_result(cached)
    disk_cached = read_disk_owner_result_cache(tool, request, ttl_seconds=profile.owner_result_cache_ttl_seconds)
    if disk_cached:
        write_owner_result_cache(tool, request, disk_cached, ttl_seconds=profile.owner_result_cache_ttl_seconds)
        return normalize_owner_result(disk_cached)
    if tool == "github":
        result = normalize_owner_result(execute_github_metadata(request, gateway_plan, timeout))
    elif tool == "package_manager":
        result = normalize_owner_result(execute_package_metadata(request, gateway_plan, timeout))
    elif tool == "microsoftdocs":
        result = normalize_owner_result(execute_microsoftdocs(request, gateway_plan, timeout))
    elif tool == "context7":
        result = normalize_owner_result(execute_context7(request, gateway_plan, timeout))
    elif tool == "markitdown":
        result = normalize_owner_result(execute_markitdown(request, gateway_plan, timeout))
    elif tool == "playwright":
        result = normalize_owner_result(execute_playwright(request, gateway_plan, timeout))
    elif tool == "chrome-devtools":
        result = normalize_owner_result(execute_chrome_devtools(request, gateway_plan, timeout))
    elif tool == "generic_search":
        result = normalize_owner_result(execute_generic_search(request, gateway_plan, timeout))
    elif tool == "youtube-feed":
        result = normalize_owner_result(execute_youtube_feed(request, gateway_plan, timeout))
    else:
        result = normalize_owner_result(_json_result(ok=False, status="handoff_required", reason="owner_tool_not_supported"))
    satisfaction = resource_result_satisfaction(request=request, tool=tool, result=result)
    if result.get("ok") and not satisfaction.satisfied:
        owner_result = result.get("owner_result") if isinstance(result.get("owner_result"), dict) else {}
        result = {
            **result,
            "ok": False,
            "status": "degraded",
            "error_class": satisfaction.reason,
            "reason": satisfaction.reason,
            "next_action": satisfaction.next_action,
            "satisfaction": satisfaction.to_dict(),
            "owner_result": {**owner_result, "ok": False, "status": "degraded", "confidence": 0.0},
        }
    elif result.get("ok"):
        result = {**result, "satisfaction": satisfaction.to_dict()}
    write_owner_result_cache(tool, request, result, ttl_seconds=profile.owner_result_cache_ttl_seconds)
    write_disk_owner_result_cache(tool, request, result, ttl_seconds=profile.owner_result_cache_ttl_seconds)
    return result


def _first_context7_library_id(content: str) -> str:
    match = re.search(r"Context7-compatible library ID:\s*(/[^\s]+)", content)
    return match.group(1).strip() if match else ""


def _request_text(request: dict[str, Any]) -> str:
    return " ".join(str(request.get(key) or "") for key in ("task", "target", "url", "name")).strip()


def owner_tool_handoff_contract(tool: str, request: dict[str, Any], gateway_plan: dict[str, Any] | None) -> dict[str, Any]:
    """Return an executable handoff plan for owner tools the broker cannot call."""

    package = execution_package_from_gateway_plan(gateway_plan)
    text = _request_text(request)
    url = str(request.get("url") or request.get("target") or "").strip()
    calls: list[dict[str, Any]] = []
    if tool == "microsoftdocs":
        if "learn.microsoft.com" in url:
            calls.append({"mcp": "microsoftdocs", "tool": "microsoft_docs_fetch", "arguments": {"url": url}})
        calls.append({"mcp": "microsoftdocs", "tool": "microsoft_docs_search", "arguments": {"query": text or url}})
    elif tool == "context7":
        metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
        library_id = str(metadata.get("library_id") or "").strip()
        query = text or "resource documentation lookup"
        if library_id.startswith("/"):
            calls.append({"mcp": "context7", "tool": "query_docs", "arguments": {"libraryId": library_id, "query": query}})
        else:
            library_name = str(request.get("name") or request.get("target") or "").strip() or query
            calls.append({"mcp": "context7", "tool": "resolve_library_id", "arguments": {"libraryName": library_name, "query": query}})
            calls.append({"mcp": "context7", "tool": "query_docs", "arguments": {"libraryId": "<resolved_library_id>", "query": query}})
    elif tool == "playwright":
        calls.append(
            {
                "mcp": "playwright",
                "tool": "browser_or_page_inspection",
                "arguments": {"url": url, "task": text, "read_only": True},
            }
        )
    elif tool == "chrome-devtools":
        calls.append(
            {
                "hub_tool": "chrome_devtools.list_pages",
                "arguments": {
                    "fallback_ack": "native-mcp-unavailable-and-original-permissions-apply",
                    "timeout_seconds": 30,
                },
            }
        )
        if url:
            calls.append(
                {
                    "hub_tool": "chrome_devtools.navigate_page",
                    "arguments": {
                        "url": url,
                        "fallback_ack": "native-mcp-unavailable-and-original-permissions-apply",
                        "timeout_seconds": 45,
                    },
                }
            )
        calls.append(
            {
                "hub_tool": "chrome_devtools.take_snapshot",
                "arguments": {
                    "fallback_ack": "native-mcp-unavailable-and-original-permissions-apply",
                    "timeout_seconds": 45,
                },
            }
        )
    elif tool == "markitdown":
        calls.append({"mcp": "markitdown", "tool": "convert_to_markdown", "arguments": {"uri": url, "task": text}})
    elif tool == "package_manager":
        calls.append({"owner": "package_manager", "tool": "metadata_or_install_risk_review", "arguments": {"target": request.get("target") or request.get("name"), "task": text}})
    else:
        calls.append({"owner": tool, "tool": "owner_specific_read", "arguments": {"target": request.get("target") or url, "task": text}})
    return _json_result(
        ok=False,
        status="handoff_required",
        owner_tool=tool,
        result_kind="owner_tool_handoff_contract",
        permission_boundary="owner_tool_required",
        current_turn_or_hub_calls=calls,
        network_execution_package=package,
        attach_result={
            "entrypoint": "python _bridge\\resource_cli.py attach-result",
            "source_tool": tool,
            "result_kind": "owner_result",
            "rule": "after the owner tool returns, attach content/artifact/metadata to the same request_id",
        },
        next_action="execute_owner_call_then_attach_result",
        reason="owner_tool_requires_current_turn_mcp_or_known_hub_alias",
    )


def validate() -> dict[str, Any]:
    package_owner_validation = validate_package_owner()
    youtube_feed_validation = validate_youtube_feed_owner()
    npm_probe = execute_package_metadata(
        {"target": "left-pad", "metadata": {"package_ecosystem": "npm", "package_action": "install"}},
        {"ok": True, "plan": {"route_mode": "probe_selected_direct", "target_kind": "package", "env": {}, "unset_env": []}},
        1,
    )
    docs_contract = owner_tool_handoff_contract(
        "microsoftdocs",
        {"url": "https://learn.microsoft.com/en-us/windows/", "task": "lookup Windows docs"},
        {"ok": True, "plan": {"route_mode": "probe_selected_direct", "target_kind": "docs"}},
    )
    return {
        "schema": "resource_owner_executor.validate.v1",
        "ok": bool(
            supports_owner_execution("github")
            and supports_owner_execution("package_manager")
            and supports_owner_execution("microsoftdocs")
            and supports_owner_execution("context7")
            and supports_owner_execution("markitdown")
            and supports_owner_execution("playwright")
            and supports_owner_execution("chrome-devtools")
            and supports_owner_execution("generic_search")
            and supports_owner_execution("youtube-feed")
            and youtube_feed_validation.get("ok")
            and npm_probe.get("status") == "handoff_required"
            and npm_probe.get("error_class") == "install_requires_explicit_approval"
            and package_owner_validation.get("ok")
            and docs_contract.get("status") == "handoff_required"
            and docs_contract.get("current_turn_or_hub_calls")
            and _first_context7_library_id("Context7-compatible library ID: /python/cpython") == "/python/cpython"
        ),
        "supported_owner_tools": sorted(SUPPORTED_OWNER_TOOLS),
        "default_mode": "read_only",
        "npm_handoff_ok": npm_probe.get("error_class") == "install_requires_explicit_approval",
        "package_owner_validation_ok": package_owner_validation.get("ok"),
        "package_owner_validation": package_owner_validation,
        "youtube_feed_validation": youtube_feed_validation,
        "docs_handoff_contract_ok": docs_contract.get("status") == "handoff_required",
        "writes_files": False,
        "writes_remote_state": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only resource owner executor")
    parser.add_argument("command", choices=("validate", "execute"))
    parser.add_argument("--tool", default="")
    parser.add_argument("--request-json", default="{}")
    parser.add_argument("--gateway-plan-json", default="{}")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    if args.command == "validate":
        print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    payload = execute_owner_tool(
        tool=args.tool,
        request=json.loads(args.request_json),
        gateway_plan=json.loads(args.gateway_plan_json),
        timeout=args.timeout,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") or payload.get("status") == "handoff_required" else 1


if __name__ == "__main__":
    raise SystemExit(main())
