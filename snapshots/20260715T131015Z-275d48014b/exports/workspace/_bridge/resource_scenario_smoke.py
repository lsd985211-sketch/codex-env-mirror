#!/usr/bin/env python3
"""Resource-layer scenario smoke tests.

Ownership: provide reusable resource-layer and network-gateway cooperation
scenarios for maintenance commands and regression tests.
Non-goals: background queues, package installation, git clone execution,
remote writes, or global proxy/DNS mutation.
State behavior: writes only caller-scoped batch manifests, event logs, and
receipt logs under `_bridge/tmp` or caller-provided paths.
Caller context: `resource_cli.py scenario-smoke`, resource-layer tests, and
manual gateway lab checks.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from resource_broker import ResourceBrokerRequest, network_gateway_request_for_request, route_for_request
from resource_scheduler import ResourceBatchConfig, batch_status_from_manifest, execute_batch, requests_from_payload
from resource_validation_profile import VALIDATION_PROFILES, profile_from


BRIDGE_ROOT = Path(__file__).resolve().parent
DEFAULT_TMP_ROOT = BRIDGE_ROOT / "tmp"


LIVE_SCENARIO_REQUESTS: list[dict[str, Any]] = [
    {"path": "_bridge/resource_router.py", "task": "simulate existing local file verification", "intent": "explicit_local_file"},
    {"path": "_bridge/tmp/does-not-exist-resource.txt", "task": "simulate missing local file", "intent": "explicit_local_file"},
    {"url": "https://example.com/", "task": "simulate generic URL preview", "intent": "explicit_user_url"},
    {
        "url": "https://example.invalid/resource.txt",
        "task": "simulate unreachable URL",
        "intent": "explicit_user_url",
        "timeout_seconds": 5,
        "retry_budget": 0,
    },
    {
        "url": "https://example.com/",
        "task": "simulate URL blocked by no-network policy",
        "intent": "explicit_user_url",
        "allow_network": False,
    },
    {
        "url": "https://github.com/microsoft/playwright",
        "task": "simulate GitHub repo metadata owner execution",
        "intent": "external_dependency",
        "auto_owner": True,
    },
    {
        "url": "https://github.com/openai/this-repo-should-not-exist-codex-resource-sim",
        "task": "simulate missing GitHub repo metadata",
        "intent": "external_dependency",
        "auto_owner": True,
        "timeout_seconds": 10,
    },
    {"target": "ruff", "task": "simulate Python package metadata", "intent": "package_dependency", "auto_owner": True},
    {
        "target": "definitely-not-a-real-package-codex-sim-xyz",
        "task": "simulate missing Python package metadata",
        "intent": "package_dependency",
        "auto_owner": True,
        "timeout_seconds": 10,
    },
    {
        "target": "left-pad",
        "task": "simulate npm package metadata request",
        "intent": "package_dependency",
        "auto_owner": True,
        "metadata": {"package_ecosystem": "npm"},
        "timeout_seconds": 10,
    },
    {
        "url": "https://learn.microsoft.com/en-us/windows/",
        "task": "simulate Microsoft documentation lookup owner handoff",
        "intent": "documentation_lookup",
    },
    {
        "url": "https://example.com/",
        "task": "simulate materialization without filesystem write grant",
        "intent": "explicit_user_url",
        "need_materialization": True,
        "allow_filesystem_write": False,
    },
    {
        "target": "ruff",
        "task": "simulate install-like package request without auto owner",
        "intent": "package_dependency",
        "auto_owner": False,
    },
    {
        "target": "中国 人工智能 论文 PDF 开放获取",
        "task": "simulate academic paper source selection",
        "intent": "external_dependency",
        "need_materialization": True,
        "allow_filesystem_write": True,
        "metadata": {"resource_kind_hint": "academic_paper"},
    },
]

QUICK_SCENARIO_REQUESTS: list[dict[str, Any]] = [
    {"path": "_bridge/resource_router.py", "task": "simulate existing local file verification", "intent": "explicit_local_file"},
    {
        "url": "https://example.com/",
        "task": "simulate URL blocked by no-network policy",
        "intent": "explicit_user_url",
        "allow_network": False,
    },
    {
        "url": "https://example.com/",
        "task": "simulate materialization without filesystem write grant",
        "intent": "explicit_user_url",
        "need_materialization": True,
        "allow_filesystem_write": False,
    },
    {
        "target": "left-pad",
        "task": "simulate npm package metadata request",
        "intent": "package_dependency",
        "auto_owner": True,
        "metadata": {"package_ecosystem": "npm"},
        "timeout_seconds": 2,
    },
    {
        "target": "ruff",
        "task": "simulate install-like package request without auto owner",
        "intent": "package_dependency",
        "auto_owner": False,
    },
    {
        "target": "中国 人工智能 论文 PDF 开放获取",
        "task": "simulate academic paper source selection",
        "intent": "external_dependency",
        "need_materialization": True,
        "allow_filesystem_write": True,
        "metadata": {"resource_kind_hint": "academic_paper", "source_selection_only": True},
    },
]

SMOKE_SCENARIO_REQUESTS: list[dict[str, Any]] = [
    *QUICK_SCENARIO_REQUESTS,
    {
        "target": "python",
        "task": "simulate Context7 target-only docs owner execution",
        "intent": "documentation_lookup",
        "auto_owner": True,
        "timeout_seconds": 20,
    },
    {
        "url": "https://example.com/",
        "task": "simulate MarkItDown page to markdown owner execution",
        "intent": "documentation_lookup",
        "auto_owner": True,
        "timeout_seconds": 20,
    },
    {
        "url": "https://example.com/",
        "task": "simulate Chrome DevTools page inspection owner execution",
        "intent": "documentation_lookup",
        "auto_owner": True,
        "timeout_seconds": 20,
    },
]

FULL_SCENARIO_REQUESTS: list[dict[str, Any]] = [
    *LIVE_SCENARIO_REQUESTS,
    {
        "target": "python",
        "task": "simulate Context7 target-only docs owner execution",
        "intent": "documentation_lookup",
        "auto_owner": True,
        "timeout_seconds": 30,
    },
    {
        "url": "https://example.com/",
        "task": "simulate MarkItDown page to markdown owner execution",
        "intent": "documentation_lookup",
        "auto_owner": True,
        "timeout_seconds": 30,
    },
]


def with_validation_profile(requests: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    profile = profile_from(mode)
    items: list[dict[str, Any]] = []
    for request in requests:
        item = dict(request)
        metadata = dict(item.get("metadata") or {})
        metadata.setdefault("validation_profile", profile.name)
        item["metadata"] = metadata
        item.setdefault("retry_budget", profile.default_retry_budget)
        if item.get("auto_owner"):
            item["timeout_seconds"] = min(int(item.get("timeout_seconds") or profile.max_owner_timeout_seconds), profile.max_owner_timeout_seconds)
        items.append(item)
    return items


def scenario_payload(mode: str) -> dict[str, Any]:
    if mode == "full":
        return {"requests": with_validation_profile(FULL_SCENARIO_REQUESTS, mode)}
    if mode == "live":
        return {"requests": with_validation_profile(LIVE_SCENARIO_REQUESTS, mode)}
    if mode == "smoke":
        return {"requests": with_validation_profile(SMOKE_SCENARIO_REQUESTS, mode)}
    if mode == "quick":
        return {"requests": with_validation_profile(QUICK_SCENARIO_REQUESTS, mode)}
    raise ValueError(f"unsupported scenario mode: {mode}")


def scenario_paths(mode: str, *, tmp_root: Path = DEFAULT_TMP_ROOT) -> dict[str, Path]:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"resource-gateway-scenario-{mode}-{stamp}"
    return {
        "store_root": tmp_root / f"{prefix}-store",
        "event_log": tmp_root / f"{prefix}-events.jsonl",
        "receipt_log": tmp_root / f"{prefix}-receipts.jsonl",
    }


def result_by_task(batch: dict[str, Any], task: str) -> dict[str, Any]:
    planned_by_index = {
        int(item.get("index") or 0): item
        for item in batch.get("planned", [])
        if isinstance(item, dict)
    }
    for result in batch.get("results", []):
        if not isinstance(result, dict):
            continue
        planned = planned_by_index.get(int(result.get("index") or 0), {})
        request = planned.get("request") if isinstance(planned.get("request"), dict) else {}
        if request.get("task") == task:
            merged = dict(result)
            merged["planned"] = planned
            return merged
    return {}


def planned_by_task(batch: dict[str, Any], task: str) -> dict[str, Any]:
    for planned in batch.get("planned", []):
        if not isinstance(planned, dict):
            continue
        request = planned.get("request") if isinstance(planned.get("request"), dict) else {}
        if request.get("task") == task:
            return planned
    return {}


def receipt_from_result(result: dict[str, Any]) -> dict[str, Any]:
    manifest_path = str(result.get("manifest_path") or "").strip()
    if not manifest_path:
        return {}
    try:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    receipt = payload.get("receipt") if isinstance(payload, dict) and isinstance(payload.get("receipt"), dict) else {}
    return receipt


def check_equals(checks: list[dict[str, Any]], name: str, actual: Any, expected: Any) -> None:
    checks.append({"name": name, "ok": actual == expected, "actual": actual, "expected": expected})


def check_in(checks: list[dict[str, Any]], name: str, actual: Any, expected_values: set[Any]) -> None:
    checks.append({"name": name, "ok": actual in expected_values, "actual": actual, "expected": sorted(expected_values)})


def evaluate_scenario(mode: str, batch: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    check_equals(checks, "batch_status_read_ok", status.get("read_ok"), True)
    check_equals(checks, "batch_status_has_batch_ok", "batch_ok" in status, True)

    npm_plan = planned_by_task(batch, "simulate npm package metadata request")
    check_equals(checks, "npm_host_key", npm_plan.get("host_key"), "package:npm")
    npm_result = result_by_task(batch, "simulate npm package metadata request")
    check_equals(checks, "npm_owner_status", npm_result.get("status"), "completed")
    check_equals(checks, "npm_owner_result_kind", npm_result.get("result_kind"), "node_package_metadata")
    check_equals(checks, "npm_owner_accepted", (npm_result.get("acceptance") or {}).get("accepted"), True)

    no_network = result_by_task(batch, "simulate URL blocked by no-network policy")
    check_equals(checks, "no_network_status", no_network.get("status"), "failed")
    check_equals(checks, "no_network_error", no_network.get("error_class"), "network_not_allowed")
    check_equals(
        checks,
        "no_network_primary_tool",
        (no_network.get("planned") or {}).get("route", {}).get("primary_tool"),
        "resource_cli",
    )

    materialize = result_by_task(batch, "simulate materialization without filesystem write grant")
    check_equals(checks, "materialize_without_write_status", materialize.get("status"), "failed")
    check_equals(checks, "materialize_without_write_error", materialize.get("error_class"), "filesystem_write_not_allowed")

    install_like = planned_by_task(batch, "simulate install-like package request without auto owner")
    check_equals(checks, "install_like_queue_deferred", install_like.get("queue_class"), "install_or_clone")
    source_selection = planned_by_task(batch, "simulate academic paper source selection")
    check_equals(checks, "paper_source_selection_queue", source_selection.get("queue_class"), "source_selection")
    check_equals(checks, "paper_source_selection_host_key", source_selection.get("host_key"), "source_selection")

    if mode in {"full", "live"}:
        github_owner = result_by_task(batch, "simulate GitHub repo metadata owner execution")
        check_equals(checks, "github_owner_status", github_owner.get("status"), "completed")
        github_receipt = receipt_from_result(github_owner)
        github_attempts = github_receipt.get("attempts") if isinstance(github_receipt.get("attempts"), list) else []
        github_result = github_attempts[0].get("result") if github_attempts and isinstance(github_attempts[0], dict) else {}
        github_metadata = github_result.get("metadata") if isinstance(github_result, dict) and isinstance(github_result.get("metadata"), dict) else {}
        check_equals(checks, "github_owner_route", github_metadata.get("owner_execution_route"), "local_hub_github_api")
        github_missing = result_by_task(batch, "simulate missing GitHub repo metadata")
        check_equals(checks, "github_missing_status", github_missing.get("status"), "failed")
        check_in(checks, "github_missing_error", github_missing.get("error_class"), {"http_status", "URLError"})

    if mode in {"smoke", "full"}:
        context7_plan = planned_by_task(batch, "simulate Context7 target-only docs owner execution")
        context7_request = context7_plan.get("request") if isinstance(context7_plan.get("request"), dict) else {}
        context7_metadata = context7_request.get("metadata") if isinstance(context7_request.get("metadata"), dict) else {}
        context7_gateway = context7_metadata.get("network_gateway_plan") if isinstance(context7_metadata.get("network_gateway_plan"), dict) else {}
        context7_network_plan = context7_gateway.get("plan") if isinstance(context7_gateway.get("plan"), dict) else {}
        check_equals(checks, "context7_target_only_gateway_kind", context7_network_plan.get("target_kind"), "docs")
        check_equals(checks, "context7_target_only_gateway_target", context7_network_plan.get("target"), "https://context7.com/")

    failed = [item for item in checks if not item.get("ok")]
    return {
        "schema": "resource_scenario_smoke.evaluation.v1",
        "ok": not failed,
        "mode": mode,
        "checks": checks,
        "failed_checks": failed,
    }


def run_scenario_smoke(
    *,
    mode: str = "quick",
    max_active: int = 4,
    per_host_limit: int = 1,
    tmp_root: Path = DEFAULT_TMP_ROOT,
) -> dict[str, Any]:
    paths = scenario_paths(mode, tmp_root=tmp_root.expanduser().resolve())
    requests: list[ResourceBrokerRequest] = requests_from_payload(scenario_payload(mode))
    batch = execute_batch(
        requests,
        config=ResourceBatchConfig(max_active=max_active, per_host_limit=per_host_limit),
        event_log=paths["event_log"],
        receipt_log=paths["receipt_log"],
        resource_log=None,
        store_root=paths["store_root"],
    )
    status = batch_status_from_manifest(Path(str(batch.get("manifest_path", ""))))
    evaluation = evaluate_scenario(mode, batch, status)
    return {
        "schema": "resource_scenario_smoke.result.v1",
        "ok": bool(evaluation.get("ok")),
        "mode": mode,
        "paths": {key: str(value) for key, value in paths.items()},
        "manifest_path": batch.get("manifest_path", ""),
        "batch_status": status,
        "evaluation": evaluation,
        "request_count": batch.get("request_count", 0),
    }


def validate() -> dict[str, Any]:
    quick_payload = scenario_payload("quick")
    smoke_payload = scenario_payload("smoke")
    full_payload = scenario_payload("full")
    live_payload = scenario_payload("live")
    docs_request = ResourceBrokerRequest(
        target="python",
        task="simulate Context7 target-only docs owner execution",
        intent="documentation_lookup",
        auto_owner=True,
        metadata={"validation_profile": "quick"},
    )
    docs_route = route_for_request(docs_request)
    docs_gateway = network_gateway_request_for_request(docs_request, docs_route)
    browser_request = ResourceBrokerRequest(
        task="simulate browser screenshot without URL",
        intent="documentation_lookup",
        auto_owner=True,
        metadata={"validation_profile": "quick"},
    )
    browser_route = route_for_request(browser_request)
    browser_gateway = network_gateway_request_for_request(browser_request, browser_route)
    github_request = ResourceBrokerRequest(
        target="open source resource acquisition agent github repositories",
        task="simulate GitHub repository search without URL",
        intent="external_dependency",
        auto_owner=True,
        metadata={"validation_profile": "quick"},
    )
    github_route = route_for_request(github_request)
    github_gateway = network_gateway_request_for_request(github_request, github_route)
    paper_request = ResourceBrokerRequest(
        target="中国 人工智能 论文 PDF 开放获取",
        task="simulate academic paper source selection",
        intent="external_dependency",
        need_materialization=True,
        metadata={"validation_profile": "quick", "resource_kind_hint": "academic_paper"},
    )
    paper_route = route_for_request(paper_request)
    paper_gateway = network_gateway_request_for_request(paper_request, paper_route)
    image_request = ResourceBrokerRequest(
        target="华为总部 Huawei headquarters photos",
        task="simulate image source selection without URL",
        intent="external_dependency",
        need_materialization=True,
        metadata={"validation_profile": "quick", "resource_kind_hint": "image"},
    )
    image_route = route_for_request(image_request)
    image_gateway = network_gateway_request_for_request(image_request, image_route)
    dataset_request = ResourceBrokerRequest(
        target="AI training dataset csv open license",
        task="simulate dataset source selection without URL",
        intent="external_dependency",
        need_materialization=True,
        metadata={"validation_profile": "quick", "resource_kind_hint": "dataset"},
    )
    dataset_route = route_for_request(dataset_request)
    dataset_gateway = network_gateway_request_for_request(dataset_request, dataset_route)
    web_request = ResourceBrokerRequest(
        target="mature resource acquisition retry best practices",
        task="simulate generic web source selection without URL",
        intent="external_dependency",
        metadata={"validation_profile": "quick"},
    )
    web_route = route_for_request(web_request)
    web_gateway = network_gateway_request_for_request(web_request, web_route)
    return {
        "schema": "resource_scenario_smoke.validate.v1",
        "ok": len(quick_payload["requests"]) >= 5
        and len(smoke_payload["requests"]) >= len(quick_payload["requests"])
        and len(full_payload["requests"]) >= len(smoke_payload["requests"])
        and len(live_payload["requests"]) >= len(quick_payload["requests"])
        and docs_gateway.get("target_kind") == "docs"
        and docs_gateway.get("target") == "https://context7.com/"
        and docs_gateway.get("reason") != "no_concrete_network_target_for_owner_route"
        and browser_gateway.get("target_kind") == "browser"
        and browser_gateway.get("target") == "https://example.com/"
        and browser_gateway.get("reason") != "no_concrete_network_target_for_owner_route"
        and github_gateway.get("target_kind") == "github"
        and github_gateway.get("target") == "https://api.github.com/"
        and github_gateway.get("reason") != "no_concrete_network_target_for_owner_route"
        and paper_gateway.get("target_kind") == "paper"
        and bool(paper_gateway.get("target"))
        and paper_gateway.get("reason") != "no_concrete_network_target_for_owner_route"
        and image_gateway.get("target_kind") == "image"
        and bool(image_gateway.get("target"))
        and image_gateway.get("reason") != "no_concrete_network_target_for_owner_route"
        and dataset_gateway.get("target_kind") == "dataset"
        and bool(dataset_gateway.get("target"))
        and dataset_gateway.get("reason") != "no_concrete_network_target_for_owner_route"
        and web_gateway.get("target_kind") == "web"
        and bool(web_gateway.get("target"))
        and web_gateway.get("reason") != "no_concrete_network_target_for_owner_route",
        "modes": list(VALIDATION_PROFILES),
        "quick_count": len(quick_payload["requests"]),
        "smoke_count": len(smoke_payload["requests"]),
        "full_count": len(full_payload["requests"]),
        "live_count": len(live_payload["requests"]),
        "target_only_docs_gateway": docs_gateway,
        "target_only_browser_gateway": browser_gateway,
        "target_only_github_gateway": github_gateway,
        "target_only_paper_gateway": paper_gateway,
        "target_only_image_gateway": image_gateway,
        "target_only_dataset_gateway": dataset_gateway,
        "target_only_web_gateway": web_gateway,
        "writes_global_network_state": False,
        "installs_packages": False,
        "performs_remote_writes": False,
    }
