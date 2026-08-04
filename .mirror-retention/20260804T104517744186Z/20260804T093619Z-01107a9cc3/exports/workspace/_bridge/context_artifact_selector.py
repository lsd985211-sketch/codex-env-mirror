#!/usr/bin/env python3
"""Read bounded selections from an existing durable artifact.

Ownership: validate local artifact identity and select registered bounded views.
Non-goals: network access, source-owner execution, artifact creation, mutation,
permission expansion, TTL cache retrieval, or long-term memory.
State behavior: read-only. Caller context: context projection L2 expansion.
"""

from __future__ import annotations

import base64
from collections import deque
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


SELECTOR_KINDS = {"json_field", "object_id", "text_lines", "byte_range", "query_hit", "receipt_section"}
MAX_RANGE_BYTES = 64 * 1024
MAX_LINES = 500
MAX_HITS = 50
HASH_CHUNK_BYTES = 1024 * 1024


def _artifact_path(value: str) -> Path:
    raw = str(value or "").strip()
    if raw.startswith("artifact:"):
        raw = raw.removeprefix("artifact:")
    return Path(raw).expanduser().resolve()


def _within_roots(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(Path(root).expanduser().resolve())
            return True
        except ValueError:
            continue
    return False


def _walk_field(value: Any, parts: list[str]) -> tuple[bool, Any]:
    current = value
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return False, None
    return True, current


def _find_object(value: Any, identifier: str) -> Any:
    if isinstance(value, dict):
        if any(str(value.get(key) or "") == identifier for key in ("id", "item_id", "request_id", "name", "code")):
            return value
        for child in value.values():
            found = _find_object(child, identifier)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_object(child, identifier)
            if found is not None:
                return found
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _text_lines(path: Path, start: int, end: int) -> str:
    selected: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line_number < start:
                continue
            if line_number > end:
                break
            selected.append(line.rstrip("\r\n"))
    return "\n".join(selected)


def _query_hits(path: Path, query: str, *, context: int, limit: int) -> list[dict[str, Any]]:
    before: deque[tuple[int, str]] = deque(maxlen=context)
    pending: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.rstrip("\r\n")
            for hit in list(pending):
                if line_number <= int(hit["target_end_line"]):
                    hit["rows"].append((line_number, line))
                if line_number >= int(hit["target_end_line"]):
                    completed.append(hit)
                    pending.remove(hit)
            if query.casefold() in line.casefold() and len(completed) + len(pending) < limit:
                rows = [*before, (line_number, line)]
                hit = {
                    "line": line_number,
                    "start_line": rows[0][0],
                    "target_end_line": line_number + context,
                    "rows": rows,
                }
                if context:
                    pending.append(hit)
                else:
                    completed.append(hit)
            before.append((line_number, line))
    completed.extend(pending)
    return [
        {
            "line": int(hit["line"]),
            "start_line": int(hit["start_line"]),
            "end_line": int(hit["rows"][-1][0]),
            "text": "\n".join(str(row[1]) for row in hit["rows"]),
        }
        for hit in completed[:limit]
    ]


def select_artifact(
    artifact_ref: str,
    selector: dict[str, Any],
    *,
    expected_sha256: str = "",
    allowed_roots: Iterable[Path] = (),
) -> dict[str, Any]:
    path = _artifact_path(artifact_ref)
    if not allowed_roots or not _within_roots(path, allowed_roots):
        return {"ok": False, "reason": "artifact_outside_allowed_roots", "artifact_ref": artifact_ref}
    if not path.is_file():
        return {"ok": False, "reason": "artifact_not_readable", "artifact_ref": artifact_ref}
    source_bytes = path.stat().st_size
    digest = _file_sha256(path)
    if expected_sha256 and digest != str(expected_sha256).lower():
        return {"ok": False, "reason": "artifact_hash_mismatch", "source_sha256": digest}
    kind = str(selector.get("kind") or "")
    if kind not in SELECTOR_KINDS:
        return {"ok": False, "reason": "selector_kind_not_registered", "kind": kind}
    base = {"schema": "context_artifact_selector.result.v1", "ok": True, "kind": kind, "artifact_ref": artifact_ref, "source_sha256": digest, "source_bytes": source_bytes}

    if kind == "byte_range":
        start = max(0, int(selector.get("start") or 0))
        end = min(source_bytes, int(selector.get("end") or source_bytes), start + MAX_RANGE_BYTES)
        if end < start:
            return {**base, "ok": False, "reason": "selector_range_invalid"}
        with path.open("rb") as handle:
            handle.seek(start)
            content = handle.read(end - start)
        return {
            **base,
            "start": start,
            "end": end,
            "encoding": "base64",
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    if kind in {"json_field", "object_id", "receipt_section"}:
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {**base, "ok": False, "reason": "artifact_json_required_for_selector"}
        if kind == "object_id":
            identifier = str(selector.get("id") or "")
            value = _find_object(payload, identifier) if identifier else None
            return {**base, "ok": value is not None, "reason": "" if value is not None else "selector_object_not_found", "value": value}
        parts = selector.get("path") if kind == "json_field" else str(selector.get("section") or "").split(".")
        parts = [str(item) for item in parts] if isinstance(parts, list) else []
        ok, value = _walk_field(payload, parts)
        return {**base, "ok": ok, "reason": "" if ok else "selector_field_not_found", "value": value}

    if kind == "text_lines":
        start = max(1, int(selector.get("start") or 1))
        end = min(int(selector.get("end") or start), start + MAX_LINES - 1)
        if end < start:
            return {**base, "ok": False, "reason": "selector_range_invalid"}
        try:
            text = _text_lines(path, start, end)
        except UnicodeDecodeError:
            return {**base, "ok": False, "reason": "artifact_utf8_required_for_selector"}
        return {**base, "start_line": start, "end_line": end, "text": text}

    query = str(selector.get("query") or "")
    if not query:
        return {**base, "ok": False, "reason": "selector_query_required"}
    context = max(0, min(int(selector.get("context_lines") or 0), 20))
    limit = max(1, min(int(selector.get("max_hits") or 10), MAX_HITS))
    try:
        hits = _query_hits(path, query, context=context, limit=limit)
    except UnicodeDecodeError:
        return {**base, "ok": False, "reason": "artifact_utf8_required_for_selector"}
    return {**base, "ok": bool(hits), "reason": "" if hits else "selector_query_no_hits", "query": query, "hits": hits}
