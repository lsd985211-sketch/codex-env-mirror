#!/usr/bin/env python3
"""Focused regression tests for resource request admission."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import resource_broker
import resource_owner_executor
import resource_source_executor
from resource_broker import ResourceBrokerRequest, handle_request
from resource_fetcher import ResourceIntent
from resource_request_admission import normalize_resource_input, url_batch_payload, validate


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    single = normalize_resource_input({"url": "https://example.com/docs"})
    require(single["kind"] == "url" and single["urls"] == ["https://example.com/docs"], "single URL classification failed")

    legacy = normalize_resource_input({"target": "https://example.com/a   https://example.com/b"})
    require(legacy["kind"] == "url_list" and len(legacy["urls"]) == 2, "legacy multi URL input was not promoted")

    structured = normalize_resource_input({"urls": ["https://example.com/a", "https://example.com/a", "https://example.com/b"]})
    require(structured["urls"] == ["https://example.com/a", "https://example.com/b"], "URL identity deduplication failed")
    batch = url_batch_payload({"urls": structured["urls"], "task": "read docs", "auto_owner": True}, structured)
    require(len(batch["items"]) == 2, "URL batch item count mismatch")
    require(len({item["item_id"] for item in batch["items"]}) == 2, "URL batch item ids must be stable and unique")
    require(all(" " not in item["request"]["url"] for item in batch["items"]), "whitespace URL reached a batch item")

    ambiguous = normalize_resource_input({"url": "https://example.com/a explain"})
    require(not ambiguous["ok"] and ambiguous["reason"] == "ambiguous_resource_input", "mixed URL/prose must fail admission")

    movie_query = {
        "target": "搜索电影《阳光璀璨的日子》的合法观看渠道",
        "task": "搜索电影《阳光璀璨的日子》的合法观看渠道",
        "intent": ResourceIntent.DOCUMENTATION_LOOKUP,
        "auto_owner": True,
    }
    movie_admission = normalize_resource_input(movie_query)
    require(movie_admission["kind"] == "discovery_query", "movie query must remain a discovery query")
    require(
        movie_admission.get("normalized_query_candidates") == ["搜索电影《阳光灿烂的日子》的合法观看渠道"],
        f"movie alias candidate missing: {movie_admission}",
    )
    movie_request = ResourceBrokerRequest(**movie_query)
    movie_route = resource_broker.route_for_request(movie_request)
    require(movie_route.primary_tool == "resource_router", f"movie discovery must not route to Context7: {movie_route}")
    require(movie_route.intent == ResourceIntent.EXTERNAL_DEPENDENCY, "incompatible documentation intent was not corrected")
    movie_plan = resource_broker.strategy_plan_for_request(movie_request, movie_route)
    require(
        [item["tool"] for item in movie_plan[:2]] == ["resource_source_strategy", "generic_search"],
        f"movie discovery must enter the managed search owner: {movie_plan}",
    )
    require(
        all(item["stage"] != "probe" for item in movie_plan),
        f"URL-only probe leaked into a URL-free discovery plan: {movie_plan}",
    )
    require(
        all(item["tool"] not in {"context7", "resource_cli"} for item in movie_plan),
        f"URL-free entity discovery retained an inapplicable owner: {movie_plan}",
    )
    platform_request = ResourceBrokerRequest(
        target="阳光灿烂的日子 1994 豆瓣 腾讯视频 爱奇艺 优酷 芒果TV",
        task="阳光灿烂的日子 1994 豆瓣 腾讯视频 爱奇艺 优酷 芒果TV",
        intent=ResourceIntent.DOCUMENTATION_LOOKUP,
        auto_owner=True,
    )
    platform_route = resource_broker.route_for_request(platform_request)
    require(platform_route.primary_tool == "resource_router", "platform-brand discovery must not enter Context7")
    require(
        [item["tool"] for item in resource_broker.strategy_plan_for_request(platform_request, platform_route)]
        == ["resource_source_strategy", "generic_search"],
        "platform-brand discovery must use the managed search chain only",
    )
    platform_source = resource_source_executor.execute_source_selection(
        platform_request.__dict__, platform_route.to_dict(), timeout=1
    )
    require(
        platform_source.get("error_class") == "discovery_owner_execution_required"
        and platform_source.get("available_owner_adapter") == "generic_search",
        f"platform discovery must declare managed owner continuation: {platform_source}",
    )

    with tempfile.TemporaryDirectory(prefix="resource-admission-tests-") as tmp:
        root = Path(tmp)
        gateway_calls = {"count": 0}
        original_gateway = resource_broker.network_gateway_plan_for_request
        try:
            def forbidden_gateway(*_args: object, **_kwargs: object) -> dict[str, object]:
                gateway_calls["count"] += 1
                raise AssertionError("network gateway must not run for multi URL admission")

            resource_broker.network_gateway_plan_for_request = forbidden_gateway
            receipt = handle_request(
                ResourceBrokerRequest(url="https://example.com/a https://example.com/b"),
                event_log=root / "events.jsonl",
                receipt_log=root / "receipts.jsonl",
                store_root=root / "store",
            )
        finally:
            resource_broker.network_gateway_plan_for_request = original_gateway
        require(receipt.status == "blocked" and receipt.next_action == "promote_to_resource_batch", "direct broker must block multi URL before routing")
        require(gateway_calls["count"] == 0, "multi URL admission touched the network gateway")

        owner_calls: list[str] = []
        original_owner = resource_broker.execute_owner_tool
        original_network = resource_broker.network_gateway_plan_for_request
        try:
            def fake_owner(*, tool: str, request: dict[str, object], gateway_plan: dict[str, object], timeout: int, mode: str) -> dict[str, object]:
                del request, gateway_plan, timeout, mode
                owner_calls.append(tool)
                if tool == "markitdown":
                    return {"ok": False, "status": "handoff_required", "error_class": "gateway_tool_call_failed", "reason": "markitdown unavailable", "next_action": "try_next_route"}
                if tool == "generic_search":
                    return {
                        "ok": True,
                        "status": "completed",
                        "source": "generic_search",
                        "result_kind": "generic_web_extract",
                        "content": "retry policy documentation evidence",
                        "candidates": [{"source_id": "https://docs.example.com/retry", "url": "https://docs.example.com/retry", "snippet": "retry policy documentation evidence"}],
                        "metadata": {"items": [{"source_id": "https://docs.example.com/retry", "url": "https://docs.example.com/retry"}]},
                        "next_action": "consume_resource",
                    }
                return {"ok": False, "status": "failed", "reason": "unexpected owner"}

            resource_broker.execute_owner_tool = fake_owner
            resource_broker.network_gateway_plan_for_request = lambda *_args, **_kwargs: {
                "ok": True,
                "plan": {"target": "https://docs.example.com/retry", "target_kind": "docs", "route_mode": "direct", "env": {}, "unset_env": []},
            }
            owner_receipt = handle_request(
                ResourceBrokerRequest(
                    url="https://docs.example.com/retry",
                    task="retry policy documentation",
                    intent=ResourceIntent.DOCUMENTATION_LOOKUP,
                    auto_owner=True,
                ),
                event_log=root / "owner-events.jsonl",
                receipt_log=root / "owner-receipts.jsonl",
                store_root=root / "owner-store",
            )
        finally:
            resource_broker.execute_owner_tool = original_owner
            resource_broker.network_gateway_plan_for_request = original_network
        require(owner_receipt.status == "completed" and owner_receipt.ok, "safe owner fallback did not complete the same request")
        require(owner_calls == ["markitdown", "generic_search"], "safe owner chain did not move forward exactly once")

        search_calls: list[dict[str, object]] = []
        original_search_hub = resource_owner_executor.call_hub_tool
        try:
            def empty_search(_tool: str, arguments: dict[str, object], *, timeout: int) -> dict[str, object]:
                del timeout
                search_calls.append(dict(arguments))
                return {
                    "ok": False,
                    "status": "no_results",
                    "error_class": "search_backends_exhausted",
                    "reason": "all configured search backends returned no usable results",
                    "results": [],
                    "attempted_backends": [
                        {"backend": "duckduckgo", "error_class": "no_results", "reason": "no results"},
                        {"backend": "brave", "error_class": "no_results", "reason": "no results"},
                    ],
                }

            resource_owner_executor.call_hub_tool = empty_search
            search_result = resource_owner_executor.execute_generic_search(
                {
                    **movie_query,
                    "metadata": {
                        "resource_kind_hint": "video",
                        "query_normalization": {
                            "mode": "candidate_only",
                            "original_query": movie_query["target"],
                            "candidates": ["搜索电影《阳光灿烂的日子》的合法观看渠道"],
                        },
                    },
                },
                {"ok": True, "plan": {"route_mode": "direct", "target_kind": "web", "env": {}, "unset_env": []}},
                timeout=10,
            )
        finally:
            resource_owner_executor.call_hub_tool = original_search_hub
        require(search_result["status"] == "no_results", f"search exhaustion did not preserve terminal no_results: {search_result}")
        require(
        [item["query"] for item in search_calls]
        == ["搜索电影《阳光灿烂的日子》的合法观看渠道", "搜索电影《阳光璀璨的日子》的合法观看渠道"],
            f"normalized and original query order mismatch: {search_calls}",
        )
        require(len(search_result["metadata"]["attempted_backends"]) == 4, "backend exhaustion evidence was not retained")

        original_source_selection = resource_broker.execute_source_selection
        original_owner = resource_broker.execute_owner_tool
        original_network = resource_broker.network_gateway_plan_for_request
        try:
            resource_broker.execute_source_selection = lambda *_args, **_kwargs: {
                "ok": False,
                "status": "degraded",
                "error_class": "curated_catalog_no_match",
                "reason": "continue to managed search",
                "next_action": "continue_resource_layer_with_registered_search_owner",
            }
            resource_broker.execute_owner_tool = lambda **_kwargs: {
                "ok": False,
                "status": "no_results",
                "source": "generic_search",
                "error_class": "owner_no_results",
                "reason": "managed route chain exhausted",
                "metadata": {
                    "attempted_queries": ["搜索电影《阳光灿烂的日子》的合法观看渠道", movie_query["target"]],
                    "attempted_backends": [{"backend": "duckduckgo", "error_class": "no_results"}],
                },
                "next_action": "surface_terminal_no_results_with_route_chain",
            }
            resource_broker.network_gateway_plan_for_request = lambda *_args, **_kwargs: {
                "ok": True,
                "plan": {"route_mode": "direct", "target_kind": "web", "env": {}, "unset_env": []},
            }
            no_results_receipt = handle_request(
                movie_request,
                event_log=root / "movie-events.jsonl",
                receipt_log=root / "movie-receipts.jsonl",
                store_root=root / "movie-store",
            )
        finally:
            resource_broker.execute_source_selection = original_source_selection
            resource_broker.execute_owner_tool = original_owner
            resource_broker.network_gateway_plan_for_request = original_network
        require(no_results_receipt.status == "no_results", f"broker did not expose terminal no_results: {no_results_receipt}")
        require(no_results_receipt.error_class == "route_chain_exhausted_no_results", "route exhaustion class missing")
        require(
            [item["tool"] for item in no_results_receipt.attempts] == ["resource_source_strategy", "generic_search"],
            f"terminal chain contains inapplicable owners: {no_results_receipt.attempts}",
        )
    require(validate()["ok"], "admission validator failed")
    print(json.dumps({"ok": True, "tests": 27}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
