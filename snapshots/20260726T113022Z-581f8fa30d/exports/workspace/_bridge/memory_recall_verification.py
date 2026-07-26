#!/usr/bin/env python3
"""Read-only recall checks for approved memory absorption items.

Owns: building recall-check records, listing pending recall checks, and
verifying checks against the local absorption index plus PMB event evidence.
Non-goals: approving absorption, writing verification status, editing PMB
state, or applying note/profile changes.
State behavior: reads the absorption index and PMB events; never writes state.
Normal callers: `memory_governance.py` apply/check/validate facades.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from _bridge.memory_note_analysis import normalize_memory_text
from _bridge.memory_surface_snapshot import read_json
from _bridge.shared.json_cli import now_iso


JsonDict = dict[str, Any]
PmbEventReader = Callable[[int], tuple[list[JsonDict], str]]


def recall_queries_for_theme(theme: JsonDict, *, include_points: bool = True) -> list[str]:
    theme_id = str(theme.get("theme_id") or "").replace("_", " ").strip()
    destination = str(theme.get("destination") or "").replace(".", " ").strip()
    points = [str(item).strip() for item in (theme.get("stable_points") or []) if str(item).strip()]
    queries: list[str] = []
    if theme_id:
        queries.append(theme_id)
    if destination:
        queries.append(destination)
    if include_points:
        for point in points[:2]:
            compact = " ".join(point.split())
            if compact:
                queries.append(compact[:120])
    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        key = normalize_memory_text(query)
        if key and key not in seen:
            seen.add(key)
            deduped.append(query)
    return deduped[:4]


def build_post_apply_recall_checks(item: JsonDict, theme_id: str, *, batch_id: str) -> list[JsonDict]:
    destination = str(item.get("destination") or "")
    stable_points = [str(point).strip() for point in (item.get("keep") or []) if str(point).strip()]
    probe_theme = {
        "theme_id": theme_id,
        "destination": destination,
        "stable_points": stable_points,
    }
    checks: list[JsonDict] = []
    for index, query in enumerate(recall_queries_for_theme(probe_theme), start=1):
        checks.append(
            {
                "id": f"recall_{theme_id}_{index}",
                "status": "pending_manual_or_tool_verification",
                "created_at": now_iso(),
                "approved_batch_id": batch_id,
                "query": query,
                "expected_destination": destination,
                "expected_theme_id": theme_id,
                "method": "Use PMB recall/prepare or local memory index search when available; read-only verification only.",
            }
        )
    if not checks:
        checks.append(
            {
                "id": f"recall_{theme_id}_manual",
                "status": "pending_manual_or_tool_verification",
                "created_at": now_iso(),
                "approved_batch_id": batch_id,
                "query": theme_id,
                "expected_destination": destination,
                "expected_theme_id": theme_id,
                "method": "Manual read-back required because no stable query could be generated.",
            }
        )
    return checks


def recall_checks(absorption_index_path: Path, limit: int = 50, *, include_verified: bool = False) -> JsonDict:
    index_payload, index_error = read_json(absorption_index_path) if absorption_index_path.exists() else ({}, "missing")
    checks: list[JsonDict] = []
    if not index_error:
        for theme in index_payload.get("merged_themes") or []:
            if not isinstance(theme, dict) or theme.get("status") != "approved_absorbed":
                continue
            theme_id = str(theme.get("theme_id") or "")
            destination = str(theme.get("destination") or "")
            existing = theme.get("post_apply_recall_checks")
            theme_checks = existing if isinstance(existing, list) and existing else [
                {
                    "id": f"recall_{theme_id}_retrofit_{index}",
                    "status": "pending_manual_or_tool_verification",
                    "created_at": theme.get("approved_at") or "",
                    "approved_batch_id": theme.get("approved_batch_id") or "",
                    "query": query,
                    "expected_destination": destination,
                    "expected_theme_id": theme_id,
                    "method": "Retrofit read-only check for an older approved absorption item.",
                }
                for index, query in enumerate(recall_queries_for_theme(theme, include_points=False), start=1)
            ]
            if not theme_checks:
                theme_checks = [
                    {
                        "id": f"recall_{theme_id}_manual",
                        "status": "pending_manual_or_tool_verification",
                        "created_at": theme.get("approved_at") or "",
                        "approved_batch_id": theme.get("approved_batch_id") or "",
                        "query": theme_id,
                        "expected_destination": destination,
                        "expected_theme_id": theme_id,
                        "method": "Manual read-back required because no stable query could be generated.",
                    }
                ]
            for check in theme_checks:
                if not isinstance(check, dict):
                    continue
                status = str(check.get("status") or "pending_manual_or_tool_verification")
                if include_verified or not status.startswith("verified"):
                    checks.append({**check, "theme_id": theme_id, "destination": destination})
    limited = checks[: max(1, int(limit))]
    return {
        "schema": "memory_governance.recall_checks.v1",
        "ok": not bool(index_error),
        "generated_at": now_iso(),
        "dry_run": True,
        "index_path": str(absorption_index_path),
        "read_error": index_error,
        "pending_count": len(checks),
        "returned_count": len(limited),
        "checks": limited,
        "contract": {
            "read_only": True,
            "does_not_call_pmb_or_write_memory": True,
            "purpose": "Prove approved absorbed memory can be retrieved later, without promoting or deleting anything.",
            "acceptable_methods": [
                "PMB recall or prepare with the query",
                "MEMORY.md or absorption index read-back for fallback",
                "task preflight selects the relevant memory guidance when applicable",
            ],
        },
    }


def pmb_contains_query(events: list[JsonDict], query: str) -> JsonDict:
    words = [word for word in re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", str(query or "").lower()) if len(word) >= 3]
    if not words:
        return {"matched": False, "reason": "query_too_short"}
    for row in events:
        content = normalize_memory_text(str(row.get("content") or ""))
        if all(word in content for word in words[:5]):
            return {
                "matched": True,
                "ulid": row.get("ulid"),
                "event_type": row.get("event_type"),
                "preview": " ".join(str(row.get("content") or "").split())[:240],
            }
    return {"matched": False, "reason": "no_event_content_match"}


def recall_verify(absorption_index_path: Path, pmb_event_reader: PmbEventReader, limit: int = 50) -> JsonDict:
    checks_payload = recall_checks(absorption_index_path, limit=limit)
    index_payload, index_error = read_json(absorption_index_path) if absorption_index_path.exists() else ({}, "missing")
    themes = {
        str(item.get("theme_id")): item
        for item in (index_payload.get("merged_themes") or [])
        if isinstance(item, dict) and item.get("theme_id")
    }
    events, pmb_error = pmb_event_reader(2000)
    results: list[JsonDict] = []
    for check in checks_payload.get("checks") or []:
        if not isinstance(check, dict):
            continue
        theme_id = str(check.get("expected_theme_id") or check.get("theme_id") or "")
        expected_destination = str(check.get("expected_destination") or check.get("destination") or "")
        theme = themes.get(theme_id)
        index_match = bool(
            theme
            and theme.get("status") == "approved_absorbed"
            and (not expected_destination or str(theme.get("destination") or "") == expected_destination)
        )
        pmb_match = pmb_contains_query(events, str(check.get("query") or "")) if not pmb_error else {"matched": False, "reason": pmb_error}
        status = "not_verified"
        if index_match and pmb_match.get("matched"):
            status = "verified_pmb_and_local_index"
        elif index_match:
            status = "verified_local_index"
        elif pmb_match.get("matched"):
            status = "verified_pmb_only"
        results.append(
            {
                "id": check.get("id"),
                "query": check.get("query"),
                "theme_id": theme_id,
                "expected_destination": expected_destination,
                "status": status,
                "local_index_match": index_match,
                "pmb_match": pmb_match,
            }
        )
    status_counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schema": "memory_governance.recall_verify.v1",
        "ok": not bool(index_error),
        "generated_at": now_iso(),
        "dry_run": True,
        "writes_memory": False,
        "index_path": str(absorption_index_path),
        "index_error": index_error,
        "pmb_error": pmb_error,
        "checked_count": len(results),
        "status_counts": status_counts,
        "results": results,
        "contract": {
            "read_only": True,
            "local_index_match_is_sufficient_for_absorption_index_recall": True,
            "pmb_match_is_additional_evidence_not_required": True,
            "does_not_mark_checks_verified": True,
        },
    }
