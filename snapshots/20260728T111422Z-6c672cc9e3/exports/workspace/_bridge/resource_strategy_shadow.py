#!/usr/bin/env python3
"""Pure shadow planner for adaptive resource acquisition.

The output is advisory evidence only.  It never executes a lane, creates a
request, mutates live ordering, or owns retry/scheduling state.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from typing import Any


def _identity(value: dict[str, Any]) -> str:
    for key in ("direct_url", "url", "landing_url"):
        raw = str(value.get(key) or "").strip()
        if raw:
            parsed = urllib.parse.urlparse(raw)
            return f"{parsed.netloc.lower()}{parsed.path.rstrip('/').lower()}"
    return (
        str(value.get("source_id") or value.get("owner_tool") or value.get("id") or "")
        .strip()
        .lower()
    )


def _lane(candidate: dict[str, Any], index: int) -> dict[str, Any]:
    owner = str(
        candidate.get("owner_tool") or candidate.get("tool") or "generic_search"
    ).strip()
    identity = _identity(candidate) or f"lane-{index}"
    authority = float(
        candidate.get("authority_score")
        or candidate.get("source_trust")
        or (0.9 if "official" in str(candidate).lower() else 0.6)
    )
    return {
        "id": f"lane-{index}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:8]}",
        "owner_tool": owner,
        "source_identity": identity,
        "marginal_contribution": round(max(0.0, min(1.0, authority)), 3),
        "depends_on": [],
        "budget_class": "bounded_read_only",
    }


def build_shadow_plan(
    *,
    request: dict[str, Any],
    route: dict[str, Any],
    live_plan: list[dict[str, Any]],
    source_plan: dict[str, Any],
    acceptance: dict[str, Any],
) -> dict[str, Any]:
    candidates = (
        source_plan.get("candidates")
        if isinstance(source_plan.get("candidates"), list)
        else []
    )
    lanes: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicate_count = 0
    for index, raw in enumerate(candidates, start=1):
        if not isinstance(raw, dict):
            continue
        lane = _lane(raw, index)
        if lane["source_identity"] in seen:
            duplicate_count += 1
            continue
        seen.add(lane["source_identity"])
        lanes.append(lane)
    if not lanes:
        for index, row in enumerate(live_plan, start=1):
            if not isinstance(row, dict):
                continue
            lane = _lane(row, index)
            if lane["source_identity"] in seen:
                duplicate_count += 1
                continue
            seen.add(lane["source_identity"])
            lanes.append(lane)
    text = " ".join(
        str(request.get(key) or "") for key in ("task", "target", "name")
    ).lower()
    entity_discovery = (
        any(
            term in text
            for term in (
                "电影",
                "影片",
                "观看",
                "租赁",
                "购买",
                "movie",
                "film",
                "watch",
            )
        )
        and not str(request.get("url") or "").strip()
    )
    if entity_discovery:
        lanes.sort(
            key=lambda row: (
                row["owner_tool"] in {"context7", "microsoftdocs", "openai-docs"},
                -float(row["marginal_contribution"]),
                row["id"],
            )
        )
    live_tools = [
        str(row.get("tool") or "") for row in live_plan if isinstance(row, dict)
    ]
    shadow_tools = [str(row.get("owner_tool") or "") for row in lanes]
    payload = {
        "schema": "resource_strategy_shadow.v1",
        "mode": "shadow_only",
        "acceptance_signature": str(acceptance.get("signature") or ""),
        "lanes": lanes,
        "live_tools": live_tools,
        "shadow_tools": shadow_tools,
        "order_differs": shadow_tools != live_tools[: len(shadow_tools)],
        "duplicate_candidate_count": duplicate_count,
        "lane_count": len(lanes),
        "stop_when": "acceptance_contract_satisfied",
        "writes_network_state": False,
        "writes_business_state": False,
        "creates_request": False,
    }
    payload["signature"] = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return payload


def controlled_execution_plan(
    *, request: dict[str, Any], shadow_plan: dict[str, Any], live_tools: list[str]
) -> dict[str, Any]:
    """Activate shadow ordering only for an explicit fixed allowlist.

    It may reorder already admitted tools and group independent read-only lanes;
    it cannot introduce a new owner, permission, request, retry loop, or write.
    """
    metadata = (
        request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    )
    task_class = str(metadata.get("adaptive_strategy_task_class") or "")
    allowed = bool(metadata.get("adaptive_strategy_allowlist")) and task_class in {
        "entity_discovery",
        "documentation_lookup",
        "direct_file_fixture",
    }
    admitted = [str(tool) for tool in live_tools if str(tool)]
    if not allowed:
        return {
            "schema": "resource_strategy_execution.v1",
            "ok": True,
            "enabled": False,
            "tools": admitted,
            "batches": [[tool] for tool in admitted],
            "reason": "not_allowlisted",
        }
    proposed = [
        str(lane.get("owner_tool") or "")
        for lane in shadow_plan.get("lanes", [])
        if isinstance(lane, dict)
    ]
    ordered = [tool for tool in proposed if tool in admitted]
    ordered.extend(tool for tool in admitted if tool not in ordered)
    read_only = {
        "generic_search",
        "context7",
        "microsoftdocs",
        "openai-docs",
        "github",
        "resource_source_strategy",
    }
    first_batch = [tool for tool in ordered if tool in read_only][:2]
    batches: list[list[str]] = [first_batch] if len(first_batch) > 1 else []
    batches.extend([[tool] for tool in ordered if tool not in first_batch])
    if not batches and ordered:
        batches = [[ordered[0]]]
    return {
        "schema": "resource_strategy_execution.v1",
        "ok": True,
        "enabled": True,
        "tools": ordered,
        "batches": batches,
        "max_parallel": 2,
        "fallback": "forward_only",
        "stop_when": "acceptance_contract_satisfied",
        "introduces_owner": False,
    }
