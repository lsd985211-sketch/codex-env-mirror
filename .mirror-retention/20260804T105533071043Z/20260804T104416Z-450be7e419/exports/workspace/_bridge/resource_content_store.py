#!/usr/bin/env python3
"""Persist and reversibly expand text resource content.

Ownership: atomic raw/decoded content artifacts, bounded previews, stable
content indexes, and offline section/line/byte/query expansion.
Non-goals: network acquisition, request routing, authorization, binary
materialization, or a second request/receipt database.
State behavior: writes only below the caller-provided request/content directory
and reads only referenced artifacts during expansion.
Caller context: resource_fetcher creates one content artifact set from one HTTP
response; resource_store adopts it into the canonical request directory.
"""

from __future__ import annotations

import hashlib
import html
import importlib
import json
import os
import re
import shutil
import sys
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import managed_python_dependency_runtime as managed_python_runtime


SCHEMA = "resource_content_store.v1"
DEFAULT_PREVIEW_BYTES = 8192
DEFAULT_EXPAND_CHARS = 12000
MANAGED_DEPENDENCY_ROOT = Path(__file__).resolve().parent / "runtime_dependencies"


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        elif tag.lower() in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1
        elif tag.lower() in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _managed_beautifulsoup_text(text: str) -> tuple[str, str] | None:
    """Parse HTML only from the ABI-scoped owner-managed dependency tree."""
    selection = managed_python_runtime.select_dependency_target(
        MANAGED_DEPENDENCY_ROOT,
        "beautifulsoup4",
        python_executable=sys.executable,
    )
    if not selection.get("ok"):
        return None
    target = Path(str(selection.get("path") or "")).resolve()
    existing = sys.modules.get("bs4")
    existing_origin = Path(str(getattr(existing, "__file__", "") or "")).resolve() if existing else None
    if existing_origin and target not in existing_origin.parents:
        return None
    original_path = list(sys.path)
    try:
        sys.path.insert(0, str(target))
        module = importlib.import_module("bs4")
        origin = Path(str(getattr(module, "__file__", "") or "")).resolve()
        if target not in origin.parents:
            return None
        soup = module.BeautifulSoup(text, "html.parser")
        for hidden in soup(["script", "style", "noscript", "svg"]):
            hidden.decompose()
        visible = soup.get_text("\n")
        visible = re.sub(r"[ \t\f\v]+", " ", visible)
        visible = re.sub(r"\n\s*\n\s*\n+", "\n\n", visible).strip()
        return visible, "managed_beautifulsoup_html_parser"
    except Exception:
        return None
    finally:
        sys.path[:] = original_path


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    temporary.write_bytes(data)
    temporary.replace(path)


def _charset(content_type: str) -> str:
    match = re.search(r"charset=([^;\s]+)", str(content_type or ""), flags=re.IGNORECASE)
    return match.group(1).strip("\"'") if match else "utf-8"


def decode_content(raw: bytes, content_type: str) -> tuple[str, str]:
    requested = _charset(content_type)
    for encoding in (requested, "utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_text(text: str, content_type: str) -> tuple[str, str]:
    kind = str(content_type or "").lower()
    declared_type = kind.split(";", 1)[0].strip()
    if declared_type and declared_type not in {"text/html", "application/xhtml+xml"}:
        return text, "declared_non_html_text"
    if "html" not in kind and not re.search(r"<(?:html|body|main|article|p|div)\b", text[:2000], re.IGNORECASE):
        return text, "plain_text"
    managed = _managed_beautifulsoup_text(text)
    if managed is not None:
        return managed
    parser = _VisibleTextParser()
    try:
        parser.feed(text)
        visible = html.unescape("".join(parser.parts))
        visible = re.sub(r"[ \t\f\v]+", " ", visible)
        visible = re.sub(r"\n\s*\n\s*\n+", "\n\n", visible).strip()
        return visible, "stdlib_html_parser"
    except Exception:
        return text, "html_parse_failed_raw_text_preserved"


def persist_content(
    *,
    content_dir: Path,
    raw: bytes,
    source_url: str,
    content_type: str,
    preview_bytes: int = DEFAULT_PREVIEW_BYTES,
) -> dict[str, Any]:
    content_dir = content_dir.expanduser().resolve()
    text, encoding = decode_content(raw, content_type)
    parsed, parser = parse_text(text, content_type)
    raw_hash = hashlib.sha256(raw).hexdigest()
    text_bytes = parsed.encode("utf-8")
    text_hash = hashlib.sha256(text_bytes).hexdigest()
    raw_path = content_dir / "content.raw"
    text_path = content_dir / "content.txt"
    preview_path = content_dir / "preview.txt"
    index_path = content_dir / "content-index.json"
    preview_limit = max(0, int(preview_bytes))
    preview = text_bytes[:preview_limit]
    _atomic_write(raw_path, raw)
    _atomic_write(text_path, text_bytes)
    _atomic_write(preview_path, preview)
    lines = parsed.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line.encode("utf-8"))
    index = {
        "schema": f"{SCHEMA}.index",
        "source_url": source_url,
        "content_type": content_type,
        "encoding": encoding,
        "parser": parser,
        "raw_sha256": raw_hash,
        "content_sha256": text_hash,
        "raw_bytes": len(raw),
        "content_bytes": len(text_bytes),
        "content_chars": len(parsed),
        "line_count": len(lines),
        "line_byte_offsets": offsets,
        "raw_ref": str(raw_path),
        "content_ref": str(text_path),
        "preview_ref": str(preview_path),
        "preview_limit": preview_limit,
        "preview_truncated": len(text_bytes) > preview_limit,
    }
    _atomic_write(index_path, (json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {**index, "ok": True, "content_index_ref": str(index_path)}


def adopt_content_artifacts(*, request_dir: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    """Copy a verified fetch artifact set into the canonical request directory."""

    index_ref = Path(str(metadata.get("content_index_ref") or ""))
    if not index_ref.is_file():
        return {}
    try:
        source_index = json.loads(index_ref.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    source_raw = Path(str(source_index.get("raw_ref") or ""))
    source_text = Path(str(source_index.get("content_ref") or ""))
    source_preview = Path(str(source_index.get("preview_ref") or ""))
    if not all(path.is_file() for path in (source_raw, source_text, source_preview)):
        return {}
    raw = source_raw.read_bytes()
    text_bytes = source_text.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source_index.get("raw_sha256") or hashlib.sha256(text_bytes).hexdigest() != source_index.get("content_sha256"):
        return {}
    target_dir = request_dir / "content"
    target_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "raw_ref": target_dir / "content.raw",
        "content_ref": target_dir / "content.txt",
        "preview_ref": target_dir / "preview.txt",
        "content_index_ref": target_dir / "content-index.json",
    }
    shutil.copyfile(source_raw, targets["raw_ref"])
    shutil.copyfile(source_text, targets["content_ref"])
    shutil.copyfile(source_preview, targets["preview_ref"])
    target_index = {
        **source_index,
        "raw_ref": str(targets["raw_ref"]),
        "content_ref": str(targets["content_ref"]),
        "preview_ref": str(targets["preview_ref"]),
    }
    _atomic_write(targets["content_index_ref"], (json.dumps(target_index, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {**target_index, "content_index_ref": str(targets["content_index_ref"]), "ok": True}


def expand_content(
    *,
    content_ref: Path,
    query: str = "",
    line_start: int = 0,
    line_count: int = 0,
    byte_start: int = 0,
    byte_count: int = 0,
    max_chars: int = DEFAULT_EXPAND_CHARS,
) -> dict[str, Any]:
    path = content_ref.expanduser().resolve()
    if not path.is_file():
        return {"schema": f"{SCHEMA}.expand", "ok": False, "reason": "content_ref_missing", "content_ref": str(path)}
    data = path.read_bytes()
    content_hash = hashlib.sha256(data).hexdigest()
    text = data.decode("utf-8", errors="replace")
    mode = "bounded_head"
    start = 0
    fragment = text
    if query:
        match = re.search(re.escape(query), text, re.IGNORECASE)
        if not match:
            return {"schema": f"{SCHEMA}.expand", "ok": False, "reason": "query_not_found", "query": query, "content_sha256": content_hash, "content_ref": str(path)}
        start = max(0, match.start() - max_chars // 4)
        fragment = text[start : start + max_chars]
        mode = "query"
    elif line_start or line_count:
        lines = text.splitlines()
        start_line = max(1, int(line_start or 1))
        count = max(1, int(line_count or 20))
        fragment = "\n".join(lines[start_line - 1 : start_line - 1 + count])
        start = start_line
        mode = "line"
    elif byte_start or byte_count:
        start_byte = max(0, int(byte_start))
        count = max(1, int(byte_count or max_chars))
        fragment = data[start_byte : start_byte + count].decode("utf-8", errors="replace")
        start = start_byte
        mode = "byte"
    else:
        fragment = text[:max_chars]
    return {
        "schema": f"{SCHEMA}.expand",
        "ok": True,
        "mode": mode,
        "start": start,
        "text": fragment[:max_chars],
        "returned_chars": min(len(fragment), max_chars),
        "content_chars": len(text),
        "truncated": len(fragment) > max_chars or (mode == "bounded_head" and len(text) > max_chars),
        "content_sha256": content_hash,
        "content_ref": str(path),
        "network_used": False,
    }
