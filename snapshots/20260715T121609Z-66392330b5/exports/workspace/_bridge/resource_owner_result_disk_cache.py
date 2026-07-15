#!/usr/bin/env python3
"""Persistent cache for bounded read-only resource owner results.

Ownership: cache successful, read-only owner-tool text/metadata results across
short-lived resource CLI processes.
Non-goals: caching failures, browser state, local file conversions, installs,
remote writes, permission decisions, or network route decisions.
State behavior: writes compact JSON entries under `_bridge/runtime` with TTL
enforced by callers.
Caller context: `resource_owner_executor.py` checks this cache after its
process-local cache and before calling slow owner tools.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from resource_request_runtime_cache import owner_result_cache_allowed, owner_result_cache_key


BRIDGE_ROOT = Path(__file__).resolve().parent
CACHE_ROOT = BRIDGE_ROOT / "runtime" / "resource_owner_result_cache"
SCHEMA = "resource_owner_result_cache.entry.v1"
DISK_CACHEABLE_TOOLS = {"github", "microsoftdocs", "context7", "markitdown"}
MAX_CONTENT_CHARS = 80_000


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def cache_path(tool: str, request: dict[str, Any]) -> Path:
    key = owner_result_cache_key(tool, request)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return CACHE_ROOT / f"{digest}.json"


def disk_cache_allowed(tool: str, request: dict[str, Any]) -> bool:
    """Return whether a result is safe and useful to persist briefly."""

    if tool not in DISK_CACHEABLE_TOOLS:
        return False
    if not owner_result_cache_allowed(tool, request):
        return False
    if tool == "markitdown":
        url = str(request.get("url") or request.get("target") or "").strip()
        path = str(request.get("path") or "").strip()
        if path or not url.startswith(("http://", "https://")):
            return False
    return True


def read_disk_owner_result_cache(tool: str, request: dict[str, Any], ttl_seconds: int) -> dict[str, Any] | None:
    ttl = max(0, int(ttl_seconds))
    if not ttl or not disk_cache_allowed(tool, request):
        return None
    path = cache_path(tool, request)
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(entry, dict) or entry.get("schema") != SCHEMA:
        return None
    generated_text = str(entry.get("generated_at") or "")
    try:
        generated = dt.datetime.fromisoformat(generated_text)
    except ValueError:
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=dt.UTC)
    if (now() - generated).total_seconds() > ttl:
        return None
    result = entry.get("result")
    if not isinstance(result, dict):
        return None
    payload = copy.deepcopy(result)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    payload["metadata"] = {
        **metadata,
        "owner_result_disk_cache_hit": True,
        "owner_result_disk_cache_key": str(entry.get("cache_key") or ""),
        "owner_result_disk_cache_path": str(path),
        "owner_result_disk_cache_ttl_seconds": ttl,
    }
    return payload


def write_disk_owner_result_cache(tool: str, request: dict[str, Any], result: dict[str, Any], ttl_seconds: int) -> Path | None:
    ttl = max(0, int(ttl_seconds))
    if not ttl or not disk_cache_allowed(tool, request):
        return None
    if not (result.get("ok") and str(result.get("status") or "") == "completed"):
        return None
    if result.get("writes_files") or result.get("writes_remote_state"):
        return None
    if len(str(result.get("content") or "")) > MAX_CONTENT_CHARS:
        return None
    payload = copy.deepcopy(result)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    key = owner_result_cache_key(tool, request)
    payload["metadata"] = {
        **metadata,
        "owner_result_disk_cache_hit": False,
        "owner_result_disk_cache_key": key,
        "owner_result_disk_cache_ttl_seconds": ttl,
    }
    entry = {
        "schema": SCHEMA,
        "generated_at": now().isoformat(),
        "tool": tool,
        "cache_key": key,
        "result": payload,
    }
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    path = cache_path(tool, request)
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def validate() -> dict[str, Any]:
    request = {"url": "https://example.com/", "task": "convert page"}
    path = cache_path("markitdown", request)
    return {
        "schema": "resource_owner_result_disk_cache.validate.v1",
        "ok": disk_cache_allowed("markitdown", request)
        and not disk_cache_allowed("markitdown", {"path": "local.md"})
        and not disk_cache_allowed("playwright", {"url": "https://example.com/"})
        and path.name.endswith(".json"),
        "cache_root": str(CACHE_ROOT),
        "cacheable_tools": sorted(DISK_CACHEABLE_TOOLS),
        "writes_files": True,
        "writes_remote_state": False,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
