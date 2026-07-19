#!/usr/bin/env python3
"""Normalize resource owner-tool results into a stable receipt shape.

Ownership: provide a compact, machine-readable summary for read-only owner
results returned by `resource_owner_executor.py`.
Non-goals: calling owner tools, deciding routes, writing receipts, or changing
permission boundaries.
State behavior: pure transformation; no filesystem, network, or process side
effects.
Caller context: resource owner executors call this before returning so progress,
receipts, and downstream Codex consumption can rely on one result envelope.
"""

from __future__ import annotations

import re
from typing import Any


SOURCE_URL_RE = re.compile(r"^Source:\s*(https?://\S+)", re.IGNORECASE | re.MULTILINE)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((https?://[^)]+)\)")


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _title_from_content(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.lstrip("#").strip()[:160]
    return ""


def _citations_from(content: str, metadata: dict[str, Any]) -> list[str]:
    citations: list[str] = []
    for key in ("url", "html_url", "top_url", "uri"):
        value = str(metadata.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            citations.append(value)
    citations.extend(SOURCE_URL_RE.findall(content))
    citations.extend(MARKDOWN_LINK_RE.findall(content))
    return list(dict.fromkeys(citations))[:8]


def _summary_from(content: str, metadata: dict[str, Any]) -> str:
    description = str(metadata.get("description") or "").strip()
    if description:
        return description[:500]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content.strip()) if part.strip()]
    if not paragraphs:
        return ""
    summary = re.sub(r"\s+", " ", paragraphs[0])
    return summary[:500]


def normalize_owner_result(result: dict[str, Any]) -> dict[str, Any]:
    """Attach a stable owner_result envelope while preserving legacy fields."""

    payload = dict(result)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata = dict(metadata)
    content = str(payload.get("content") or "")
    source_tool = _first_non_empty(payload.get("source"), payload.get("owner_tool"))
    result_kind = _first_non_empty(payload.get("result_kind"), metadata.get("result_kind"), "owner_result")
    title = _first_non_empty(metadata.get("title"), metadata.get("full_name"), _title_from_content(content), source_tool)
    url = _first_non_empty(metadata.get("url"), metadata.get("html_url"), metadata.get("top_url"), metadata.get("uri"))
    citations = _citations_from(content, metadata)
    owner_result = {
        "schema": "resource_owner.normalized_result.v1",
        "ok": bool(payload.get("ok")),
        "status": str(payload.get("status") or ""),
        "source_tool": source_tool,
        "result_kind": result_kind,
        "title": title,
        "url": url,
        "summary": _summary_from(content, metadata),
        "content_chars": len(content),
        "citations": citations,
        "owner_execution_route": str(metadata.get("owner_execution_route") or ""),
        "permission_boundary": str(payload.get("permission_boundary") or metadata.get("permission_boundary") or ""),
        "next_action": str(payload.get("next_action") or ""),
        "confidence": 0.9 if payload.get("ok") else 0.0,
    }
    metadata["owner_result_summary"] = {
        "source_tool": owner_result["source_tool"],
        "result_kind": owner_result["result_kind"],
        "title": owner_result["title"],
        "url": owner_result["url"],
        "citation_count": len(citations),
        "content_chars": len(content),
    }
    payload["metadata"] = metadata
    payload["owner_result"] = owner_result
    return payload


def validate() -> dict[str, Any]:
    sample = normalize_owner_result(
        {
            "ok": True,
            "status": "completed",
            "source": "context7",
            "result_kind": "docs",
            "content": "### json.dumps\n\nSource: https://github.com/python/cpython/blob/main/Doc/library/json.rst\n\nSerialize obj.",
            "metadata": {"owner_execution_route": "hub_mcp_gateway_call"},
            "next_action": "consume_resource",
        }
    )
    return {
        "schema": "resource_owner_result_normalizer.validate.v1",
        "ok": bool(sample.get("owner_result", {}).get("citations")),
        "writes_files": False,
        "writes_remote_state": False,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
