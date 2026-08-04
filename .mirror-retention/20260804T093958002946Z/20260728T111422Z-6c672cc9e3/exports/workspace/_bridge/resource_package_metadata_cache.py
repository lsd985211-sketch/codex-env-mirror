#!/usr/bin/env python3
"""Small cache for resource-layer package metadata validation.

Ownership: cache bounded package metadata lookups and provide deterministic
fixtures for quick/smoke validation profiles.
Non-goals: installing packages, resolving dependency graphs, replacing pip/npm,
or hiding live package-index failures in full/live profiles.
State behavior: writes compact JSON cache files under `_bridge/runtime`.
Caller context: `resource_owner_executor.py` uses this before running slow
package-index commands.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


BRIDGE_ROOT = Path(__file__).resolve().parent
CACHE_ROOT = BRIDGE_ROOT / "runtime" / "resource_package_metadata_cache"
SCHEMA = "resource_package_metadata_cache.entry.v1"

FIXTURES: dict[tuple[str, str], dict[str, Any]] = {
    ("python", "ruff"): {
        "latest": "0.15.19",
        "content": "ruff (0.15.19)\nAvailable versions: 0.15.19",
        "source": "fixture",
    },
    ("python", "requests"): {
        "latest": "2.32.4",
        "content": "requests (2.32.4)\nAvailable versions: 2.32.4",
        "source": "fixture",
    },
}


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def cache_key(ecosystem: str, package_name: str, index_url: str = "") -> str:
    raw = json.dumps(
        {
            "ecosystem": ecosystem.strip().lower(),
            "package": package_name.strip().lower(),
            "index_url": index_url.strip().lower(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def cache_path(ecosystem: str, package_name: str, index_url: str = "") -> Path:
    return CACHE_ROOT / f"{cache_key(ecosystem, package_name, index_url)}.json"


def fixture_result(ecosystem: str, package_name: str) -> dict[str, Any] | None:
    payload = FIXTURES.get((ecosystem.strip().lower(), package_name.strip().lower()))
    if not payload:
        return None
    generated_at = now().isoformat()
    return {
        "schema": SCHEMA,
        "ok": True,
        "cache_kind": "fixture",
        "generated_at": generated_at,
        "expires_at": "",
        "ecosystem": ecosystem,
        "package": package_name,
        "latest": payload.get("latest", ""),
        "content": payload.get("content", ""),
        "source": payload.get("source", "fixture"),
    }


def read_cache(ecosystem: str, package_name: str, index_url: str = "", *, ttl_seconds: int = 0) -> dict[str, Any] | None:
    path = cache_path(ecosystem, package_name, index_url)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return None
    generated_text = str(payload.get("generated_at") or "")
    effective_ttl = ttl_seconds
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if not payload.get("ok") and metadata.get("negative_ttl_seconds"):
        try:
            negative_ttl = int(metadata.get("negative_ttl_seconds") or 0)
            if negative_ttl > 0:
                effective_ttl = min(ttl_seconds, negative_ttl) if ttl_seconds > 0 else negative_ttl
        except (TypeError, ValueError):
            pass
    if effective_ttl > 0 and generated_text:
        try:
            generated = dt.datetime.fromisoformat(generated_text)
            if generated.tzinfo is None:
                generated = generated.replace(tzinfo=dt.UTC)
            if (now() - generated).total_seconds() > effective_ttl:
                return None
        except ValueError:
            return None
    payload["cache_kind"] = str(payload.get("cache_kind") or "disk")
    return payload


def write_cache(ecosystem: str, package_name: str, payload: dict[str, Any], index_url: str = "") -> Path:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    generated_at = now().isoformat()
    data = {
        "schema": SCHEMA,
        "ok": bool(payload.get("ok")),
        "cache_kind": "disk",
        "generated_at": generated_at,
        "expires_at": "",
        "ecosystem": ecosystem,
        "package": package_name,
        "latest": str(payload.get("latest") or ""),
        "content": str(payload.get("content") or ""),
        "source": str(payload.get("source") or "live_package_index"),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    }
    path = cache_path(ecosystem, package_name, index_url)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def validate() -> dict[str, Any]:
    fixture = fixture_result("python", "ruff")
    return {
        "schema": "resource_package_metadata_cache.validate.v1",
        "ok": bool(fixture and fixture.get("latest")),
        "cache_root": str(CACHE_ROOT),
        "fixture_count": len(FIXTURES),
        "writes_remote_state": False,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
