#!/usr/bin/env python3
"""Runtime caches for resource-layer request execution.

Ownership: provide bounded in-process de-duplication for repeated resource
network plans and read-only owner-tool results.
Non-goals: persistent evidence storage, permission changes, package installs,
global proxy/DNS mutation, or replacing resource receipts.
State behavior: process-local TTL caches only; no filesystem, network, or
remote side effects.
Caller context: `resource_broker.py` and `resource_owner_executor.py` use this
module to avoid repeated slow probes/calls inside one validation or batch run.
"""

from __future__ import annotations

import copy
import json
import threading
import time
import urllib.parse
from typing import Any, Callable


_NETWORK_PLAN_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_NETWORK_PLAN_IN_FLIGHT: set[str] = set()
_NETWORK_CONDITION = threading.Condition()

_OWNER_RESULT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_OWNER_RESULT_LOCK = threading.Lock()

OWNER_RESULT_CACHEABLE_TOOLS = {
    "github",
    "microsoftdocs",
    "context7",
    "markitdown",
    "package_manager",
}


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalized_network_target(target_kind: str, target: str) -> str:
    """Return the route-decision target, not the whole request URL.

    Network route quality is mostly host/category based. Keeping the full path
    in the key makes a single batch probe the same host repeatedly. Package,
    GitHub, and docs routes can safely share host-level route evidence because
    this cache stores route advice only, not fetched resource content.
    """

    value = str(target or "").strip()
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        host = parsed.netloc.lower()
        if target_kind in {"package", "github", "docs"}:
            return f"{parsed.scheme}://{host}/"
        return f"{parsed.scheme}://{host}{parsed.path or '/'}"
    return value


def network_plan_cache_key(
    *,
    profile: str,
    target_kind: str,
    target: str,
    runtime: str,
    owner_tool: str = "",
    probe_timeout: int,
) -> str:
    _ = probe_timeout
    return _stable_json(
        {
            "profile": str(profile or ""),
            "target_kind": str(target_kind or ""),
            "target": normalized_network_target(target_kind, target),
            "owner_tool": str(owner_tool or "generic").strip().lower().replace(" ", "_") or "generic",
            "runtime": str(runtime or ""),
        }
    )


def get_or_compute_network_plan(cache_key: str, ttl_seconds: int, compute: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Return a cached network plan, coalescing concurrent identical probes."""

    ttl = max(0, int(ttl_seconds))
    now = time.time()
    with _NETWORK_CONDITION:
        cached = _NETWORK_PLAN_CACHE.get(cache_key)
        if cached and ttl and now - cached[0] <= ttl:
            payload = copy.deepcopy(cached[1])
            payload["cache_hit"] = True
            payload["cache_status"] = "hit"
            payload["cache_ttl_seconds"] = ttl
            return payload
        while cache_key in _NETWORK_PLAN_IN_FLIGHT:
            _NETWORK_CONDITION.wait(timeout=ttl or 30)
            cached = _NETWORK_PLAN_CACHE.get(cache_key)
            if cached and ttl and time.time() - cached[0] <= ttl:
                payload = copy.deepcopy(cached[1])
                payload["cache_hit"] = True
                payload["cache_status"] = "coalesced_hit"
                payload["cache_ttl_seconds"] = ttl
                return payload
            if ttl == 0:
                break
        _NETWORK_PLAN_IN_FLIGHT.add(cache_key)
    try:
        payload = compute()
        if isinstance(payload, dict):
            payload = copy.deepcopy(payload)
        else:
            payload = {"ok": False, "reason": "network_plan_compute_returned_non_dict", "result": payload}
        payload.setdefault("cache_hit", False)
        payload.setdefault("cache_status", "miss")
        payload.setdefault("cache_key", cache_key)
        if ttl:
            with _NETWORK_CONDITION:
                _NETWORK_PLAN_CACHE[cache_key] = (time.time(), copy.deepcopy(payload))
        return payload
    finally:
        with _NETWORK_CONDITION:
            _NETWORK_PLAN_IN_FLIGHT.discard(cache_key)
            _NETWORK_CONDITION.notify_all()


def owner_result_cache_key(tool: str, request: dict[str, Any]) -> str:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    relevant_metadata = {
        key: metadata.get(key)
        for key in (
            "validation_profile",
            "resource_validation_profile",
            "library_id",
            "library_name",
            "package_ecosystem",
            "ecosystem",
        )
        if key in metadata
    }
    return _stable_json(
        {
            "tool": str(tool or ""),
            "target": str(request.get("target") or ""),
            "url": str(request.get("url") or ""),
            "path": str(request.get("path") or ""),
            "task": str(request.get("task") or ""),
            "name": str(request.get("name") or ""),
            "metadata": relevant_metadata,
        }
    )


def owner_result_cache_allowed(tool: str, request: dict[str, Any]) -> bool:
    if tool not in OWNER_RESULT_CACHEABLE_TOOLS:
        return False
    if bool(request.get("need_materialization")):
        return False
    return True


def read_owner_result_cache(tool: str, request: dict[str, Any], ttl_seconds: int) -> dict[str, Any] | None:
    ttl = max(0, int(ttl_seconds))
    if not ttl or not owner_result_cache_allowed(tool, request):
        return None
    key = owner_result_cache_key(tool, request)
    with _OWNER_RESULT_LOCK:
        cached = _OWNER_RESULT_CACHE.get(key)
        if not cached or time.time() - cached[0] > ttl:
            return None
        payload = copy.deepcopy(cached[1])
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    payload["metadata"] = {
        **metadata,
        "owner_result_cache_hit": True,
        "owner_result_cache_key": key,
        "owner_result_cache_ttl_seconds": ttl,
    }
    return payload


def write_owner_result_cache(tool: str, request: dict[str, Any], result: dict[str, Any], ttl_seconds: int) -> None:
    ttl = max(0, int(ttl_seconds))
    if not ttl or not owner_result_cache_allowed(tool, request):
        return
    if not (result.get("ok") and str(result.get("status") or "") == "completed"):
        return
    if result.get("writes_files") or result.get("writes_remote_state"):
        return
    key = owner_result_cache_key(tool, request)
    payload = copy.deepcopy(result)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    payload["metadata"] = {
        **metadata,
        "owner_result_cache_hit": False,
        "owner_result_cache_key": key,
        "owner_result_cache_ttl_seconds": ttl,
    }
    with _OWNER_RESULT_LOCK:
        _OWNER_RESULT_CACHE[key] = (time.time(), payload)


def validate() -> dict[str, Any]:
    key = network_plan_cache_key(profile="smoke", target_kind="github", target="https://github.com/openai/codex", owner_tool="github", runtime="generic", probe_timeout=5)
    same_host = network_plan_cache_key(profile="smoke", target_kind="github", target="https://github.com/microsoft/playwright", owner_tool="github", runtime="generic", probe_timeout=5)
    different_owner = network_plan_cache_key(profile="smoke", target_kind="github", target="https://github.com/openai/codex", owner_tool="browser", runtime="generic", probe_timeout=5)
    return {
        "schema": "resource_request_runtime_cache.validate.v1",
        "ok": key == same_host and key != different_owner and "github" in OWNER_RESULT_CACHEABLE_TOOLS and "chrome-devtools" not in OWNER_RESULT_CACHEABLE_TOOLS,
        "network_cache_owner_tool_dimension_ok": key != different_owner,
        "network_cache_state": "process_local",
        "owner_result_cache_state": "process_local",
        "writes_files": False,
        "writes_remote_state": False,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
