#!/usr/bin/env python3
"""Small shared helpers for machine-first JSON CLI tools.

Keep this module dependency-light. It is intended for _bridge scripts that
emit JSON, append JSONL evidence, or parse compact repeatable CLI arguments.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def configure_utf8_stdio() -> None:
    for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text(path: Path, limit: int | None = None) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if limit is not None and len(text) > limit:
        return text[:limit]
    return text


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def compact_items(values: list[str]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def repeatable_items(csv_value: str | list[str], repeated_values: list[str] | None = None) -> list[str]:
    values: list[str] = []
    if isinstance(csv_value, list):
        for item in csv_value:
            values.extend(split_csv(item))
    else:
        values.extend(split_csv(csv_value))
    values.extend(list(repeated_values or []))
    return compact_items(values)


def parse_key_value_items(values: list[str]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if "=" in text:
            key, raw = text.split("=", 1)
            items.append({"key": key.strip(), "value": raw.strip()})
        else:
            items.append({"key": "", "value": text})
    return items


def json_dumps(payload: Any, *, pretty: bool = True, sort_keys: bool = False) -> str:
    if pretty:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=sort_keys)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys)


def print_json(payload: Any, *, pretty: bool = True, sort_keys: bool = False) -> None:
    print(json_dumps(payload, pretty=pretty, sort_keys=sort_keys))


def append_jsonl(path: Path, payload: dict[str, Any], *, sort_keys: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json_dumps(payload, pretty=False, sort_keys=sort_keys) + "\n")
