#!/usr/bin/env python3
"""One-shot work-note storage helpers for memory governance."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from _bridge.shared.json_cli import now_iso
except ModuleNotFoundError:
    from shared.json_cli import now_iso


SensitiveHitFn = Callable[[str], list[dict[str, str]]]
DISPOSITIONS = {"handled_read_only", "proposal", "deferred", "discarded"}
PERSISTENT_DRAFT_SCOPES = {"draft", "drafts", "proposal", "proposals", "草案"}
PERSISTENT_DRAFT_SUFFIXES = ("-draft", "_draft", ".draft", "-proposal", "_proposal", ".proposal", "草案")


def json_safe(value: Any) -> Any:
    if isinstance(value, str):
        return "".join(ch if ch in "\n\r\t" or ord(ch) >= 32 else " " for ch in value)
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return value


def redact_work_note_text(
    text: str,
    *,
    sensitive_hits: SensitiveHitFn,
) -> tuple[str, list[dict[str, str]]]:
    """Redact obvious credential values before storing one-shot work notes."""
    redacted = str(text or "")
    hits = sensitive_hits(redacted)
    redacted = re.sub(r"gh[pousr]_[A-Za-z0-9_]{20,}", "[redacted-github-token]", redacted)
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[redacted-openai-key]", redacted)
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|private[_-]?key|password|passwd|cookie|authorization)\b\s*[:=]\s*['\"]?[^'\"\s]{8,}",
        lambda match: f"{match.group(1)}=[redacted-secret]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)authorization\s*:\s*bearer\s+[^'\"\s]{12,}",
        "authorization: bearer [redacted-secret]",
        redacted,
    )
    return redacted, hits


def _read_entries(note_file: Path) -> list[dict[str, Any]]:
    if not note_file.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line_no, line in enumerate(note_file.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            item = {"line_no": line_no, "parse_error": str(exc), "raw": line[:1000]}
        if isinstance(item, dict):
            item.setdefault("line_no", line_no)
            entries.append(item)
    return entries


def work_note_read(note_file: Path, *, limit: int = 100) -> dict[str, Any]:
    all_entries = _read_entries(note_file)
    entries = all_entries[-max(1, int(limit)) :]
    return {
        "schema": "memory_governance.ephemeral_work_notes.v1",
        "ok": True,
        "generated_at": now_iso(),
        "ephemeral": True,
        "path": str(note_file),
        "active_count": len(all_entries),
        "total_count": len(all_entries),
        "entries": entries,
        "codex_contract": {
            "script_role": "append_read_dispose_clear",
            "codex_must_read_raw_entries_before_non_simple_closeout": True,
            "script_must_not_summarize_classify_or_promote": True,
            "remove_from_pending_after_user_decision": True,
            "archive_disposition_before_removal": True,
            "promotion_requires_existing_memory_or_skill_or_baseline_approval_flow": True,
        },
    }


def work_note_add(
    note_file: Path,
    text: str,
    *,
    sensitive_hits: SensitiveHitFn,
    source: str = "codex",
    scope: str = "general",
    reason: str = "",
) -> dict[str, Any]:
    clean_text, sensitive = redact_work_note_text(text, sensitive_hits=sensitive_hits)
    if not clean_text.strip():
        return {"schema": "memory_governance.ephemeral_work_note_add.v1", "ok": False, "reason": "empty_text"}
    normalized_scope = str(scope or "general").strip().lower()
    if normalized_scope in PERSISTENT_DRAFT_SCOPES or normalized_scope.endswith(PERSISTENT_DRAFT_SUFFIXES):
        return {
            "schema": "memory_governance.ephemeral_work_note_add.v1",
            "ok": False,
            "reason": "persistent_draft_requires_drafts_store",
            "scope": normalized_scope,
            "recommended_owner": "draft_area",
            "recommended_path": "_bridge/shared/drafts/",
            "contract": "work notes are one-shot closeout items; retained drafts belong in the draft area and must not enter closeout review",
        }
    note_file.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "schema": "memory_governance.ephemeral_work_note.item.v1",
        "id": f"wn_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "created_at": now_iso(),
        "source": str(source or "codex")[:80],
        "scope": normalized_scope[:80],
        "reason": str(reason or "")[:240],
        "text": clean_text.strip(),
        "sensitive_hits": sensitive,
        "status": "active_until_closeout",
    }
    with note_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(json_safe(item), ensure_ascii=False, separators=(",", ":")) + "\n")
    return {
        "schema": "memory_governance.ephemeral_work_note_add.v1",
        "ok": True,
        "generated_at": now_iso(),
        "path": str(note_file),
        "id": item["id"],
        "sensitive_hit_count": len(sensitive),
        "contract": "one_shot_note; Codex reads raw entries at closeout, then clears them.",
    }


def work_note_clear(note_file: Path) -> dict[str, Any]:
    before = work_note_read(note_file, limit=10_000)
    existed = note_file.exists()
    if existed:
        note_file.unlink()
    return {
        "schema": "memory_governance.ephemeral_work_note_clear.v1",
        "ok": True,
        "generated_at": now_iso(),
        "path": str(note_file),
        "deleted_file": existed,
        "cleared_count": int(before.get("active_count") or 0),
        "archive_created": False,
    }


def work_note_dispose(note_file: Path, *, ids: list[str], disposition: str) -> dict[str, Any]:
    normalized = str(disposition or "").strip().lower()
    if normalized not in DISPOSITIONS:
        return {"schema": "memory_governance.ephemeral_work_note_dispose.v1", "ok": False, "reason": "invalid_disposition", "allowed": sorted(DISPOSITIONS)}
    requested = {str(item).strip() for item in ids if str(item).strip()}
    if not requested:
        return {"schema": "memory_governance.ephemeral_work_note_dispose.v1", "ok": False, "reason": "missing_ids"}
    entries = _read_entries(note_file)
    use_all = "all" in requested
    resolved: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for item in entries:
        note_id = str(item.get("id") or "")
        item.pop("line_no", None)
        if note_id and (use_all or note_id in requested):
            item["status"] = "resolved"
            item["disposition"] = normalized
            item["disposed_at"] = now_iso()
            resolved.append(item)
        else:
            remaining.append(item)
    if not resolved:
        return {"schema": "memory_governance.ephemeral_work_note_dispose.v1", "ok": False, "reason": "ids_not_found", "ids": sorted(requested)}
    note_file.parent.mkdir(parents=True, exist_ok=True)
    archive_file = note_file.with_name("resolved.jsonl")
    with archive_file.open("a", encoding="utf-8", newline="\n") as handle:
        for item in resolved:
            handle.write(json.dumps(json_safe(item), ensure_ascii=False, separators=(",", ":")) + "\n")
    if remaining:
        temporary = note_file.with_suffix(note_file.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for item in remaining:
                handle.write(json.dumps(json_safe(item), ensure_ascii=False, separators=(",", ":")) + "\n")
        temporary.replace(note_file)
    elif note_file.exists():
        note_file.unlink()
    return {
        "schema": "memory_governance.ephemeral_work_note_dispose.v1",
        "ok": True,
        "generated_at": now_iso(),
        "path": str(note_file),
        "disposition": normalized,
        "resolved_count": len(resolved),
        "resolved_ids": [str(item.get("id") or "") for item in resolved],
        "removed_from_pending": True,
        "remaining_pending_count": len(remaining),
        "archive_path": str(archive_file),
        "content_preserved": True,
        "will_reenter_review": False,
    }
