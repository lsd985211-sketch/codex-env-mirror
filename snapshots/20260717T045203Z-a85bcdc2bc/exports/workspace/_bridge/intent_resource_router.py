#!/usr/bin/env python3
"""Intent/resource router for owner-tool selection.

Ownership: workflow routing support.
Non-goals: execute tools, mutate remote/local state, or replace the MCP matrix.
State behavior: read-only and deterministic.
Caller context: workflow_orchestrator, resource layer, and route validators.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from intent_routing import IntentRule, matched_terms, rank_intents, term_matches
from shared.json_cli import configure_utf8_stdio, now_iso, print_json


configure_utf8_stdio()


@dataclass(frozen=True)
class ResourceRule:
    key: str
    labels: tuple[str, ...]
    owner_mcp: str
    read_tools: tuple[str, ...]
    blocked_write_tools: tuple[str, ...]
    fallback_reasons: tuple[str, ...]
    route_terms: tuple[str, ...]


INTENT_RULES: dict[str, tuple[str, ...]] = {
    "external_research": ("联网", "搜索", "查资料", "相关知识", "research", "web search", "look up"),
    "official_docs": ("官方文档", "文档", "documentation", "docs", "manual", "learn"),
    "github_remote": ("github", "repo", "issue", "pull request", "release", "actions"),
    "package_or_library_docs": ("第三方库", "依赖库", "库文档", "框架", "sdk", "api", "package", "library", "npm", "pip", "python", "node"),
    "package_install": ("安装", "install", "package install", "dependency install", "choco", "chocolatey", "winget", "aria2", "aria2c"),
    "runtime_page_evidence": ("页面", "浏览器", "截图", "dom", "cdp", "chrome", "playwright"),
    "local_state": (
        "状态",
        "队列",
        "数据库",
        "sqlite",
        "记录",
        "记录索引",
        "索引优先",
        "索引查询",
        "record index",
        "index-first",
        "receipt",
        "delivery",
    ),
}

MATERIALIZATION_TERMS = (
    "下载",
    "保存",
    "落盘",
    "物化",
    "归档",
    "照片",
    "图片",
    "文件",
    "download",
    "save",
    "materialize",
    "archive",
    "photo",
    "image",
    "file",
)

URL_TERMS = ("http://", "https://")


RESOURCE_RULES: tuple[ResourceRule, ...] = (
    ResourceRule(
        key="github",
        labels=("github", "repo", "issue", "pull request", "release", "actions"),
        owner_mcp="github",
        read_tools=("get_me", "search_issues", "search_pull_requests", "pull_request_read", "list_*"),
        blocked_write_tools=("pull_request_review_write", "add_issue_comment", "create_pull_request", "merge_pull_request", "update_pull_request"),
        fallback_reasons=("owner_mcp_unavailable", "owner_mcp_insufficient", "current_turn_transport_closed_after_hub_fallback"),
        route_terms=("github", "github_remote"),
    ),
    ResourceRule(
        key="microsoft_docs",
        labels=("microsoft", "windows", "azure", "defender", "powershell", "microsoft docs", "microsoft learn"),
        owner_mcp="microsoftdocs",
        read_tools=("microsoft_docs_search", "microsoft_docs_fetch", "microsoft_code_sample_search"),
        blocked_write_tools=(),
        fallback_reasons=("owner_mcp_unavailable", "owner_mcp_insufficient"),
        route_terms=("microsoftdocs", "external_docs_research"),
    ),
    ResourceRule(
        key="library_docs",
        labels=("langchain", "llamaindex", "react", "nextjs", "node.js", "python", "sdk", "api", "library", "framework", "package"),
        owner_mcp="context7",
        read_tools=("resolve_library_id", "query_docs"),
        blocked_write_tools=(),
        fallback_reasons=("owner_mcp_unavailable", "owner_mcp_insufficient", "no_matching_library_id"),
        route_terms=("context7", "external_docs_research"),
    ),
    ResourceRule(
        key="browser_runtime",
        labels=("browser", "chrome", "dom", "screenshot", "page", "页面", "浏览器", "截图"),
        owner_mcp="chrome-devtools|playwright",
        read_tools=("snapshot", "screenshot", "evaluate_read_only", "page_inspect"),
        blocked_write_tools=("click", "type", "navigate_write_like_action"),
        fallback_reasons=("owner_mcp_unavailable", "owner_mcp_insufficient", "no_runtime_page_needed"),
        route_terms=("chrome-devtools", "playwright", "browser_devtools"),
    ),
    ResourceRule(
        key="package_manager",
        labels=("安装", "install", "package install", "dependency", "pip", "npm", "pnpm", "uv", "uvx", "choco", "chocolatey", "winget", "aria2", "aria2c"),
        owner_mcp="resource_layer_package_manager",
        read_tools=("_bridge/codex_workflow_entry.py resource job run --intent package_dependency --auto-owner --json",),
        blocked_write_tools=("direct_choco_or_winget_or_pip_or_npm_install_without_resource_job_receipt",),
        fallback_reasons=("resource_layer_package_manager_unavailable", "install_not_approved", "package_manager_adapter_insufficient"),
        route_terms=("resource_acquisition", "package_manager", "windows_package_manager"),
    ),
)


def _github_read_tools_for(text: str) -> tuple[str, ...]:
    if matched_terms(text, ("搜索", "search", "仓库", "repo", "repository")):
        return ("search_repositories", "get_me", "search_issues", "search_pull_requests", "pull_request_read", "list_*")
    if matched_terms(text, ("issue", "issues")):
        return ("search_issues", "get_me", "issue_read", "list_issue_types", "list_issue_fields")
    if matched_terms(text, ("pull request", "pr", "合并请求")):
        return ("search_pull_requests", "get_me", "pull_request_read", "list_pull_requests")
    if matched_terms(text, ("release", "tag", "发布")):
        return ("get_latest_release", "list_releases", "get_release_by_tag", "list_tags")
    return ("get_me", "search_repositories", "search_issues", "search_pull_requests", "pull_request_read", "list_*")


def _contains(text: str, term: str) -> bool:
    return term_matches(text, term)


def _has_materialization_signal(text: str) -> bool:
    return any(_contains(text, term) for term in MATERIALIZATION_TERMS)


def _has_url_signal(text: str) -> bool:
    return any(_contains(text, term) for term in URL_TERMS)


def detect_intents(message: str) -> list[dict[str, Any]]:
    return rank_intents(message, tuple(IntentRule(key, terms) for key, terms in INTENT_RULES.items()))


def detect_resources(message: str) -> list[dict[str, Any]]:
    text = str(message or "").lower()
    ranked = {
        str(item["key"]): item
        for item in rank_intents(message, tuple(IntentRule(rule.key, rule.labels) for rule in RESOURCE_RULES))
    }
    resources: list[dict[str, Any]] = []
    for rule in RESOURCE_RULES:
        evidence = ranked.get(rule.key)
        if not evidence:
            continue
        resources.append(
            {
                "key": rule.key,
                "hits": list(evidence["hits"]),
                "score": int(evidence["score"]),
                "suppressed_negated_hits": list(evidence["suppressed_negated_hits"]),
                "owner_mcp": rule.owner_mcp,
                "read_tools": list(_github_read_tools_for(text) if rule.key == "github" else rule.read_tools),
                "blocked_write_tools": list(rule.blocked_write_tools),
                "fallback_reasons": list(rule.fallback_reasons),
                "route_terms": list(rule.route_terms),
            }
        )
    return resources


def build_route(message: str) -> dict[str, Any]:
    text = str(message or "").lower()
    intents = detect_intents(message)
    resources = detect_resources(message)
    explicit_external = any(item["key"] in {"external_research", "official_docs", "github_remote", "package_or_library_docs"} for item in intents)
    package_install = any(item["key"] == "package_install" for item in intents)
    materialization_requested = _has_materialization_signal(text)
    concrete_url_present = _has_url_signal(text)
    requires_source_selection_before_materialization = bool(
        materialization_requested and not concrete_url_present and not package_install
    )
    research_only = bool(explicit_external and not materialization_requested and not package_install)
    source_discovery_required = bool(explicit_external or requires_source_selection_before_materialization)
    resource_layer_first_required = bool(explicit_external or materialization_requested or package_install)
    if package_install:
        resources.sort(key=lambda item: 0 if item.get("key") == "package_manager" else 1)
    owner_routes = [
        {
            "resource": item["key"],
            "owner_mcp": item["owner_mcp"],
            "read_tools_first": item["read_tools"],
            "write_tools_blocked_by_default": item["blocked_write_tools"],
            "fallback_allowed_only_with": item["fallback_reasons"],
            "route_terms": item["route_terms"],
        }
        for item in resources
    ]
    needs_resource_layer_owner_selection = bool(explicit_external and not owner_routes)
    if needs_resource_layer_owner_selection:
        owner_routes.append(
            {
                "resource": "unresolved_external_source",
                "owner_mcp": "resource_layer_owner_selector",
                "read_tools_first": [
                    "_bridge/codex_workflow_entry.py resource delegate --json",
                    "_bridge/codex_workflow_entry.py resource route --json",
                    "_bridge/intent_resource_router.py route",
                ],
                "write_tools_blocked_by_default": [
                    "download_or_materialize_without_request_policy",
                    "install_or_clone_without_codex_approval",
                    "remote_write_or_message_send",
                ],
                "fallback_allowed_only_with": [
                    "resource_layer_no_owner_mcp_for_source",
                    "owner_mcp_unavailable",
                    "owner_mcp_insufficient",
                ],
                "route_terms": ["resource_acquisition", "external_docs_research", "web_search_fallback"],
            }
        )
    generic_web_gate = {
        "generic_web_allowed": not resource_layer_first_required,
        "requires_fallback_reason_if_used": resource_layer_first_required,
        "allowed_reasons": sorted(
            {reason for item in resources for reason in item["fallback_reasons"]}
            or {
                "resource_layer_no_owner_mcp_for_source",
                "owner_mcp_unavailable",
                "owner_mcp_insufficient",
            }
        ),
        "violation_if_generic_web_first": resource_layer_first_required,
    }
    return {
        "schema": "intent_resource_router.route.v1",
        "ok": True,
        "generated_at": now_iso(),
        "message": message,
        "intents": intents,
        "resources": resources,
        "owner_routes": owner_routes,
        "resource_layer_contract": {
            "required": bool(explicit_external or package_install or materialization_requested),
            "entrypoint": "_bridge/codex_workflow_entry.py resource delegate --json",
            "submit_entrypoint": "_bridge/codex_workflow_entry.py resource job run --json",
            "owner_selection_required": needs_resource_layer_owner_selection,
            "task_class": (
                "package_install"
                if package_install
                else (
                    "known_url_materialization"
                    if materialization_requested and concrete_url_present
                    else (
                        "materialization_needs_source_selection"
                        if requires_source_selection_before_materialization
                        else ("research_only" if research_only else "resource_or_external")
                    )
                )
            ),
            "codex_url_discovery_allowed": False,
            "resource_layer_source_selection_required": requires_source_selection_before_materialization,
            "resource_layer_source_discovery_required": source_discovery_required,
            "source_discovery_owner": "resource_layer" if source_discovery_required else "",
            "source_discovery_scope": [
                "documentation_lookup",
                "paper_lookup",
                "project_lookup",
                "image_lookup",
                "dataset_lookup",
                "package_metadata",
                "url_location",
            ],
            "candidate_review_before_materialization": requires_source_selection_before_materialization,
            "candidate_review_owner": "codex" if requires_source_selection_before_materialization else "",
            "candidate_review_policy": {
                "default_action": "return_candidates_before_download"
                if requires_source_selection_before_materialization
                else "materialize_when_source_is_explicit",
                "codex_decides_next": requires_source_selection_before_materialization,
                "materialization_after_review": "submit_refined_resource_request_with_selected_url",
            },
            "direct_resource_delegation_preferred": bool(
                research_only or package_install or materialization_requested or explicit_external
            ),
            "materialization_requires_resource_layer": materialization_requested,
            "install_requires_resource_layer": package_install,
            "codex_direct_acquisition_allowed_only_with": [
                "resource_layer_unavailable",
                "predefined_online_route_exhausted",
                "current_turn_exclusive_tool_required",
                "explicit_user_requires_codex_direct_fetch",
            ],
            "unsuitable_result_policy": {
                "default_action": "refine_resource_delegation_and_retry",
                "do_not_default_to_codex_direct_fetch": True,
                "refinement_fields": [
                    "narrow_keywords",
                    "source_kind",
                    "owner_tool",
                    "site_or_domain",
                    "language",
                    "freshness",
                    "authority",
                    "format",
                    "download_backend",
                    "network_route",
                    "relevance_threshold",
                ],
                "direct_fetch_escape_hatches": [
                    "resource_layer_unavailable",
                    "predefined_online_route_exhausted",
                    "explicit_user_requires_codex_direct_fetch",
                ],
                "resource_deferred_action": "refine_resource_delegation_and_retry",
                "resource_failed_or_blocked_action": "use_configured_owner_hub_online_route_chain_before_direct_generic_web",
            },
            "result_iteration_policy": {
                "codex_evaluates_receipt": True,
                "default_when_unsuitable": "refine_resource_request_and_resubmit",
                "resource_layer_keeps_first_priority": True,
                "direct_codex_lookup_after_unsuitable_result": "not_default",
                "candidate_review_before_materialization": requires_source_selection_before_materialization,
            },
            "rule": (
                "External resource acquisition defaults to resource-layer ownership. "
                "The resource layer owns source discovery and URL selection for research, documentation, projects, papers, images, datasets, packages, and user-facing materialization. "
                "If a user-facing download/save/materialization request lacks a concrete URL, the resource layer should return candidates before download; Codex evaluates the candidate receipt, refines constraints or chooses a selected URL, then submits a follow-up materialization request. "
                "If the returned result is unsuitable or deferred, Codex evaluates the receipt, refines the resource delegation parameters, and retries through the resource layer. "
                "If the resource layer fails or blocks, Codex uses the configured owner/Hub online route chain before any direct generic web fallback. "
                "Package installs and external tool acquisition must be represented as resource jobs with receipts before any direct package-manager command is used."
            ),
        },
        "generic_web_gate": generic_web_gate,
        "evidence_required": [
            "resource_layer_receipt_for_research_only_download_install_or_materialization",
            "resource_layer_source_selection_receipt_when_materialization_lacks_url",
            "resource_layer_source_discovery_receipt_for_external_lookup",
            "refined_resource_delegation_receipt_before_codex_direct_fetch_when_result_unsuitable",
            "owner_mcp_attempt_or_current_turn_negative_observation",
            "hub_or_same_boundary_fallback_when_native_fails",
            "fallback_reason_before_generic_web",
            "read_tool_used_for_read_only_probe",
        ],
    }


def validate() -> dict[str, Any]:
    samples = [
        ("联网搜索 GitHub issue 和 OpenAI 官方文档", {"github"}, True),
        ("GitHub 搜索 semantic-router 仓库", {"github"}, True),
        ("查询 LangChain tool routing 官方文档", {"library_docs"}, True),
        ("分析 Windows Defender 官方文档", {"microsoft_docs"}, True),
        ("打开浏览器检查页面 DOM", {"browser_runtime"}, False),
        ("联网搜索相关知识，完善资源层设计", {"unresolved_external_source"}, True),
        ("安装 aria2 Windows 工具", {"package_manager"}, True),
        ("下载一张苹果总部建筑照片", set(), True),
    ]
    cases: list[dict[str, Any]] = []
    ok = True
    for message, expected_resources, should_block_generic_first in samples:
        route = build_route(message)
        actual = {str(item.get("resource") or "") for item in route.get("owner_routes", [])}
        gate = route.get("generic_web_gate", {})
        resource_contract = route.get("resource_layer_contract", {})
        item_ok = expected_resources.issubset(actual) and bool(gate.get("violation_if_generic_web_first")) == should_block_generic_first
        if should_block_generic_first:
            item_ok = (
                item_ok
                and resource_contract.get("result_iteration_policy", {}).get("resource_layer_keeps_first_priority") is True
            )
            if "安装 aria2 Windows 工具" not in message:
                item_ok = (
                    item_ok
                    and resource_contract.get("resource_layer_source_discovery_required") is True
                    and resource_contract.get("source_discovery_owner") == "resource_layer"
                )
        if "安装 aria2 Windows 工具" in message:
            item_ok = item_ok and bool(resource_contract.get("required"))
        if "下载一张苹果总部建筑照片" in message:
            item_ok = (
                item_ok
                and resource_contract.get("task_class") == "materialization_needs_source_selection"
                and resource_contract.get("codex_url_discovery_allowed") is False
                and resource_contract.get("resource_layer_source_selection_required") is True
                and resource_contract.get("resource_layer_source_discovery_required") is True
                and resource_contract.get("source_discovery_owner") == "resource_layer"
                and resource_contract.get("materialization_requires_resource_layer") is True
                and resource_contract.get("direct_resource_delegation_preferred") is True
            )
        if "GitHub 搜索 semantic-router 仓库" in message:
            github_route = next((item for item in route.get("owner_routes", []) if item.get("resource") == "github"), {})
            item_ok = item_ok and (github_route.get("read_tools_first") or [""])[0] == "search_repositories"
        ok = ok and item_ok
        cases.append(
            {
                "message": message,
                "ok": item_ok,
                "expected_resources": sorted(expected_resources),
                "actual_resources": sorted(actual),
                "generic_web_gate": gate,
            }
        )
    return {"schema": "intent_resource_router.validate.v1", "ok": ok, "generated_at": now_iso(), "cases": cases}


def main() -> int:
    parser = argparse.ArgumentParser(description="Route user intent/resources to owner MCPs before generic fallback.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    route_p = sub.add_parser("route")
    route_p.add_argument("--message", required=True)
    sub.add_parser("validate")
    args = parser.parse_args()
    if args.cmd == "route":
        print_json(build_route(args.message))
        return 0
    if args.cmd == "validate":
        payload = validate()
        print_json(payload)
        return 0 if payload["ok"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
