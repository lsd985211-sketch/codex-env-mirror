#!/usr/bin/env python3
"""Focused regression tests for resource request admission."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import resource_broker
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
    require(validate()["ok"], "admission validator failed")
    print(json.dumps({"ok": True, "tests": 11}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
