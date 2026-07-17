#!/usr/bin/env python3
"""Read-only candidate note scanning and absorption planning.

Ownership: list ad hoc candidate notes and build draft absorption plans for
memory_governance.py.
Non-goals: approve memories, write absorption indexes, archive source notes,
write PMB/user-profile facts, or decide final long-term destinations alone.
State behavior: read-only; it reads note files and returns review payloads.
Caller context: memory_governance.py exposes these helpers through its CLI so
Codex can show candidate cards before any durable memory write.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from _bridge.memory_note_analysis import highest_severity, recommend_note_destination, sensitive_hits, stable_point_candidates
from _bridge.shared.json_cli import now_iso


FileInfoFn = Callable[[Path], dict[str, Any]]
ReadTextFn = Callable[[Path], str]


def candidate_notes(
    ad_hoc_notes: Path,
    *,
    keywords: tuple[str, ...],
    file_info_fn: FileInfoFn,
    read_text_fn: ReadTextFn,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return compact candidate note previews for review surfaces."""

    if not ad_hoc_notes.exists():
        return []
    notes = sorted(
        [path for path in ad_hoc_notes.glob("*.md") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    items: list[dict[str, Any]] = []
    for path in notes[:limit]:
        text = read_text_fn(path)
        lower = text.lower()
        matched = [keyword for keyword in keywords if keyword in lower or keyword in path.name.lower()]
        items.append(
            {
                **file_info_fn(path),
                "name": path.name,
                "preview": " ".join(text.split())[:500],
                "long_lived_system_keywords": matched,
                "likely_operational": bool(matched),
            }
        )
    return items


def absorb_plan(
    ad_hoc_notes: Path,
    *,
    keywords: tuple[str, ...],
    file_info_fn: FileInfoFn,
    read_preview_fn: ReadTextFn,
    read_full_fn: ReadTextFn,
    limit: int = 20,
) -> dict[str, Any]:
    """Return a dry-run absorption plan for candidate notes."""

    notes = []
    for item in candidate_notes(
        ad_hoc_notes,
        keywords=keywords,
        file_info_fn=file_info_fn,
        read_text_fn=read_preview_fn,
        limit=limit,
    ):
        path = Path(str(item.get("path") or ""))
        text = read_full_fn(path)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        title = lines[0].lstrip("# ").strip() if lines else path.stem
        stable_points = stable_point_candidates(text, limit=5)
        destination = recommend_note_destination(path, text)
        sensitive = sensitive_hits(text)
        sensitive_severity = highest_severity(sensitive)
        decision = "review_required" if sensitive_severity in {"high", "medium"} else "candidate_for_absorption"
        notes.append(
            {
                "note": item,
                "title": title,
                "recommended_destination": destination,
                "proposed_stable_points": stable_points or ["Review note and extract only stable, reusable conclusions."],
                "excluded_by_default": [
                    "raw logs",
                    "one-off incident detail",
                    "tokens/passwords/secrets/authorization material",
                    "unverified current-state claims",
                ],
                "sensitive_hits": sensitive,
                "sensitive_severity": sensitive_severity,
                "decision": decision,
                "requires_user_approval_before_apply": True,
            }
        )
    return {
        "schema": "memory_governance.absorb_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "dry_run": True,
        "source": str(ad_hoc_notes),
        "candidate_count": len(notes),
        "candidates": notes,
        "apply_policy": {
            "default_action": "no_write",
            "requires_detail_before_approval": True,
            "requires_backup_before_apply": True,
            "never_store_secrets": True,
            "verify_after_apply": ["memory_governance validate", "PMB recall query for promoted rule"],
        },
    }
