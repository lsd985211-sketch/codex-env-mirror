#!/usr/bin/env python3
"""Normalize resource request cardinality before routing or network work.

Ownership: classify one resource request as a single target, URL batch, or
ambiguous input and construct compatibility batch items.
Non-goals: owner selection, network execution, retries, persistence, or
permission decisions.
State behavior: pure functions; no file, network, or runtime writes.
Caller context: resource broker, CLI, Hub, scheduler, and focused tests.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Any


URL_FIELDS = ("url", "target")


def _absolute_http_url(value: str) -> bool:
    text = str(value or "").strip()
    if not text or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in text):
        return False
    parsed = urllib.parse.urlparse(text)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _tokens(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [item for item in re.split(r"\s+", text) if item]


def _deduplicate(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def normalize_resource_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic admission decision without executing work."""

    populated = [(field, payload.get(field)) for field in URL_FIELDS if payload.get(field)]
    explicit_urls = payload.get("urls")
    if explicit_urls:
        populated.insert(0, ("urls", explicit_urls))
    if not populated:
        kind = "local_path" if payload.get("path") else ("package" if str(payload.get("intent") or "") == "package_dependency" else "discovery_query")
        return {"schema": "resource_input.v1", "ok": True, "kind": kind, "urls": [], "reason": "classified_non_url_input"}

    all_tokens: list[str] = []
    source_fields: list[str] = []
    for field, value in populated:
        tokens = _tokens(value)
        if tokens:
            all_tokens.extend(tokens)
            source_fields.append(field)
    url_tokens = [item for item in all_tokens if _absolute_http_url(item)]
    non_url_tokens = [item for item in all_tokens if not _absolute_http_url(item)]
    urls = _deduplicate(url_tokens)
    if urls and non_url_tokens:
        return {
            "schema": "resource_input.v1",
            "ok": False,
            "kind": "ambiguous",
            "urls": urls,
            "reason": "ambiguous_resource_input",
            "non_url_tokens": non_url_tokens,
            "source_fields": source_fields,
            "next_action": "provide_one_url_or_a_structured_url_list_or_a_discovery_query",
        }
    if len(urls) > 1:
        return {
            "schema": "resource_input.v1",
            "ok": True,
            "kind": "url_list",
            "urls": urls,
            "reason": "multiple_urls_require_batch",
            "source_fields": source_fields,
        }
    if len(urls) == 1:
        return {
            "schema": "resource_input.v1",
            "ok": True,
            "kind": "url",
            "urls": urls,
            "reason": "single_url",
            "source_fields": source_fields,
        }
    return {
        "schema": "resource_input.v1",
        "ok": True,
        "kind": "discovery_query",
        "urls": [],
        "reason": "classified_discovery_query",
        "source_fields": source_fields,
    }


def url_batch_payload(payload: dict[str, Any], admission: dict[str, Any] | None = None) -> dict[str, Any]:
    decision = admission or normalize_resource_input(payload)
    if not decision.get("ok") or decision.get("kind") != "url_list":
        raise ValueError(str(decision.get("reason") or "url_list_required"))
    urls = [str(item) for item in decision.get("urls") or []]
    allowed = {
        "task", "name", "intent", "need_materialization", "allow_network",
        "allow_filesystem_write", "max_bytes", "expected_sha256",
        "timeout_seconds", "retry_budget", "target_dir", "auto_owner",
        "owner_execution_mode", "metadata",
    }
    common = {key: value for key, value in payload.items() if key in allowed}
    batch_items: list[dict[str, Any]] = []
    for index, url in enumerate(urls, start=1):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        request = {**common, "url": url, "target": url}
        metadata = dict(request.get("metadata") or {})
        metadata["admission"] = {"schema": "resource_input.v1", "kind": "url", "promoted_from_url_list": True}
        request["metadata"] = metadata
        batch_items.append({"item_id": f"url-{index:03d}-{digest}", "required": True, "request": request})
    return {
        "schema": "resource.batch_request.v1",
        "batch_name": str(payload.get("name") or "resource-url-list"),
        "items": batch_items,
        "execution": {"fail_fast": False},
        "admission": decision,
    }


def validate() -> dict[str, Any]:
    multi = normalize_resource_input({"url": "https://example.com/a https://example.com/b"})
    ambiguous = normalize_resource_input({"target": "read https://example.com/a"})
    batch = url_batch_payload({"url": ["https://example.com/a", "https://example.com/b"]})
    return {
        "schema": "resource_request_admission.validate.v1",
        "ok": multi.get("kind") == "url_list" and ambiguous.get("reason") == "ambiguous_resource_input" and len(batch["items"]) == 2,
        "multi": multi,
        "ambiguous": ambiguous,
        "batch_item_count": len(batch["items"]),
        "read_only": True,
    }
