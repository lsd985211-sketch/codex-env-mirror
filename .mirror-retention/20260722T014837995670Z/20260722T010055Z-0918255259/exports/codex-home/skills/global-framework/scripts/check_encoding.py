#!/usr/bin/env python3
"""Read-only UTF-8 and metadata-risk audit for Codex skill files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def has_non_ascii(value: str) -> bool:
    return any(ord(ch) > 127 for ch in value)


def audit_file(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    item: dict[str, object] = {
        "path": str(path),
        "bytes": len(raw),
        "utf8_ok": True,
        "replacement_char": False,
        "frontmatter_non_ascii": [],
        "error": "",
    }
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        item["utf8_ok"] = False
        item["error"] = str(exc)
        return item

    item["replacement_char"] = "\ufffd" in text
    fm = frontmatter(text)
    risky = [
        key
        for key in ("name", "description")
        if key in fm and has_non_ascii(fm[key])
    ]
    item["frontmatter_non_ascii"] = risky
    return item


def iter_skill_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.exists():
            files.extend(root.rglob("SKILL.md"))
    return sorted(set(files))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit SKILL.md files for UTF-8 decode errors and trigger metadata encoding risk."
    )
    parser.add_argument("roots", nargs="+", help="Skill roots or SKILL.md files to audit")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    results = [audit_file(path) for path in iter_skill_files([Path(p) for p in args.roots])]
    failures = [
        r
        for r in results
        if not r["utf8_ok"] or r["replacement_char"] or r["frontmatter_non_ascii"]
    ]

    if args.json:
        print(json.dumps({"checked": len(results), "issues": failures}, ensure_ascii=False, indent=2))
    else:
        print(f"Checked={len(results)} Issues={len(failures)}")
        for item in failures:
            flags: list[str] = []
            if not item["utf8_ok"]:
                flags.append(f"utf8_error={item['error']}")
            if item["replacement_char"]:
                flags.append("contains_replacement_char")
            risky = item["frontmatter_non_ascii"]
            if risky:
                flags.append("frontmatter_non_ascii=" + ",".join(risky))  # type: ignore[arg-type]
            print(f"- {item['path']}: {'; '.join(flags)}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
