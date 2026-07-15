"""Source-only scan helpers for the mobile OpenClaw bridge.

Owns: classifying bridge files as source/config/docs/runtime/data and producing
bounded source-file lists for governance tools.
Non-goals: reading message contents, inspecting queue state, deleting files, or
rewriting runtime layout.
State behavior: read-only filesystem metadata.
Normal callers: mobile_openclaw_cli source-scan and maintenance/code review flows.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SOURCE_SUFFIXES = {".py", ".js", ".cjs", ".mjs", ".ps1", ".json", ".md"}
RUNTIME_DIRS = {"runtime", "attachments", "logs", "backups", ".backups", "__pycache__"}
DATA_SUFFIXES = {".db", ".db-shm", ".db-wal", ".sqlite", ".sqlite-shm", ".sqlite-wal"}
BACKUP_MARKERS = (".bak", ".bak-")


def is_backup_file(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in BACKUP_MARKERS)


def classify_path(path: Path, root: Path = ROOT) -> str:
    rel = path.relative_to(root)
    parts = {part.lower() for part in rel.parts[:-1]}
    if parts & RUNTIME_DIRS:
        return "runtime_data"
    if is_backup_file(path):
        return "backup"
    if path.suffix.lower() in DATA_SUFFIXES:
        return "runtime_data"
    if path.suffix.lower() in SOURCE_SUFFIXES:
        return "source"
    return "other"


def iter_bridge_files(root: Path = ROOT) -> list[Path]:
    """Return files without descending into runtime/data directories."""
    result: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name.lower() not in RUNTIME_DIRS and not name.lower().endswith(".tmp")
        ]
        base = Path(dirpath)
        result.extend(base / filename for filename in filenames)
    return sorted(result)


def source_files(root: Path = ROOT) -> list[Path]:
    result: list[Path] = []
    for path in iter_bridge_files(root):
        if classify_path(path, root) == "source":
            result.append(path)
    return sorted(result)


def snapshot(root: Path = ROOT) -> dict[str, Any]:
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for path in iter_bridge_files(root):
        category = classify_path(path, root)
        counts[category] = counts.get(category, 0) + 1
        examples.setdefault(category, [])
        if len(examples[category]) < 8:
            examples[category].append(str(path.relative_to(root)).replace("\\", "/"))
    files = [str(path.relative_to(root)).replace("\\", "/") for path in source_files(root)]
    return {
        "schema": "mobile_openclaw_bridge.source_scan.v1",
        "ok": True,
        "root": str(root),
        "counts": counts,
        "source_file_count": len(files),
        "source_files": files,
        "examples": examples,
        "excluded_directories": sorted(RUNTIME_DIRS),
        "excluded_suffixes": sorted(DATA_SUFFIXES),
        "rule": "Use source_files for code governance; do not broad-scan runtime, attachment, log, backup, or browser profile trees.",
    }


def validate(root: Path = ROOT) -> dict[str, Any]:
    snap = snapshot(root)
    source_items = [Path(item) for item in snap["source_files"]]
    leaked = [
        str(item).replace("\\", "/")
        for item in source_items
        if any(part.lower() in RUNTIME_DIRS for part in item.parts)
        or item.suffix.lower() in DATA_SUFFIXES
        or is_backup_file(item)
    ]
    return {
        "schema": "mobile_openclaw_bridge.source_scan.validate.v1",
        "ok": not leaked,
        "source_file_count": snap["source_file_count"],
        "leaked_excluded_paths": leaked,
        "excluded_directories": snap["excluded_directories"],
        "excluded_suffixes": snap["excluded_suffixes"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List source-only mobile bridge files for governance scans.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--json", action="store_true", help="Emit JSON snapshot")
    parser.add_argument("--paths-only", action="store_true", help="Emit newline-separated source file paths")
    parser.add_argument("--validate", action="store_true", help="Validate source scan excludes runtime/data paths")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    if args.validate:
        payload = validate(root)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1
    if args.paths_only:
        for path in source_files(root):
            print(path)
        return 0
    payload = snapshot(root)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"source files: {payload['source_file_count']}")
        for item in payload["source_files"]:
            print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
