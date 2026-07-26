#!/usr/bin/env python3
"""Low-noise script inventory for the mcsmanager workspace.

This tool is read-only. It classifies local scripts without walking dependency,
backup, log, or attachment trees by default.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SUFFIXES = {".ps1", ".py", ".js", ".mjs", ".bat", ".cmd"}
DEFAULT_EXCLUDED_DIRS = {
    "__pycache__",
    ".backups",
    ".cache",
    "attachments",
    "backup",
    "backups",
    "browser-profile",
    "browser-profiles",
    "chrome-profile",
    "chrome-profiles",
    "corepack-home",
    "direct-tarballs",
    "dist",
    "downloads",
    "extract",
    ".venv",
    "venv",
    "venvs",
    "login-runs",
    "logs",
    "node_modules",
    "npm-cache",
    "npm-cache-ci",
    "npm-cache-localtgz2",
    "npm-cache-test",
    "npm-cache-test2",
    "openclaw-extract",
    "playwright-profile",
    "playwright-profiles",
    "pnpm-store",
    "runtime",
    "site-packages",
    "tmp",
    "user-data-dir",
}
DEFAULT_ROOTS = [
    "_bridge",
    "_tools/codex-cdp-tools",
    "_tools/openclaw-codex",
]
MAX_HUMAN_ITEMS_PER_CATEGORY = 12


@dataclass(frozen=True)
class ScriptItem:
    path: str
    category: str
    role: str
    size_bytes: int
    mtime: str


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("/", "\\")


def relative_parts(path: Path) -> tuple[str, ...]:
    """Return lowercase workspace-relative path parts for filtering/classifying."""
    resolved_root = ROOT.resolve()
    resolved_path = path.resolve()
    try:
        rel = resolved_path.relative_to(resolved_root)
    except ValueError:
        rel = path
    return tuple(part.lower() for part in rel.parts)


def is_default_excluded_part(part: str) -> bool:
    part = part.lower()
    return (
        part in DEFAULT_EXCLUDED_DIRS
        or "extract" in part
        or part.startswith("npm-cache")
        or part.endswith(".dist-info")
        or part.endswith(".egg-info")
    )


def should_skip_dir(path: Path, include_history: bool) -> bool:
    if include_history:
        return False
    for part in relative_parts(path):
        if is_default_excluded_part(part):
            return True
    return False


def should_skip_path(path: Path, include_history: bool) -> bool:
    if include_history:
        return False
    for part in relative_parts(path):
        if is_default_excluded_part(part):
            return True
    return False


def iter_script_files(roots: Iterable[str], include_history: bool) -> Iterable[Path]:
    for root_name in roots:
        root = (ROOT / root_name).resolve()
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() in SCRIPT_SUFFIXES:
                yield root
            continue
        for current, dirnames, filenames in os.walk(root):
            current_path = Path(current)
            if should_skip_dir(current_path, include_history):
                dirnames[:] = []
                continue
            if not include_history:
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not is_default_excluded_part(name)
                    and not should_skip_dir(current_path / name, include_history)
                ]
            for filename in filenames:
                if Path(filename).suffix.lower() not in SCRIPT_SUFFIXES:
                    continue
                path = current_path / filename
                if not should_skip_path(path, include_history):
                    yield path


def classify(path: Path) -> tuple[str, str]:
    rel = relative(path)
    rel_lower = rel.lower()
    name = path.name.lower()
    parts = set(relative_parts(path))

    if "backups" in parts or "backup" in parts:
        return "backup", "Backup or rollback artifact"
    if {"node_modules", "dist", "extract"} & parts:
        return "dependency", "Third-party or unpacked dependency artifact"
    if rel_lower.startswith("_bridge\\mobile_wecom_bridge\\"):
        if name in {"mobile_queue.py"}:
            return "active", "Shared queue, safety policy, and task state"
        if name.startswith("test"):
            return "helper", "Dry-run or local validation script"
        return "legacy", "Legacy WeCom bridge route kept for reference"
    if rel_lower.startswith("_bridge\\file_toolkit\\"):
        if name == "install-deps.ps1":
            return "helper", "File analysis dependency installer"
        return "active", "Attachment analysis toolkit"
    if rel_lower.startswith("_bridge\\mobile_openclaw_bridge\\"):
        if name in {"mobile_openclaw_cli.py", "health_checks.py"}:
            return "active", "OpenClaw mobile bridge runtime or diagnostics"
        if name.endswith(".ps1"):
            return "helper", "Worker install/start/supervisor helper"
        return "helper", "OpenClaw mobile bridge helper"
    if rel_lower.startswith("_tools\\codex-cdp-tools\\"):
        if name == "codex_cdp_send.js":
            return "active", "Codex Desktop CDP delivery helper"
        return "dependency", "Codex CDP tool dependency"
    if rel_lower.startswith("_tools\\openclaw-codex\\"):
        if name == "weixin_send_reply.mjs":
            return "active", "OpenClaw Weixin reply helper"
        if "login-artifacts" in parts:
            if name.startswith("weixin-login-wait-"):
                return "legacy", "Generated one-off OpenClaw login wait artifact"
            return "helper", "OpenClaw login helper"
        return "dependency", "OpenClaw runtime, install, or unpacked artifact"
    if rel_lower.startswith("_bridge\\shared\\bridge-keeper"):
        return "legacy", "Legacy visible-window bridge keeper route"
    if rel_lower.startswith("_bridge\\"):
        if name in {"codex_state_repair.py", "codex_state_audit.py", "codex_baseline_update.py"}:
            return "active", "Codex startup baseline repair, audit, or adoption tool"
        if name in {"knowledge_finalizer.py", "new_agent_bootstrap.py", "project_checkpoint_finalize.py", "script_inventory.py"}:
            return "active", "Project governance, checkpoint, or handoff tool"
        return "helper", "Bridge utility script"
    return "helper", "Workspace script"


def build_inventory(roots: Iterable[str], include_history: bool) -> list[ScriptItem]:
    items: list[ScriptItem] = []
    seen: set[Path] = set()
    for path in iter_script_files(roots, include_history):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        category, role = classify(resolved)
        stat = resolved.stat()
        items.append(
            ScriptItem(
                path=relative(resolved),
                category=category,
                role=role,
                size_bytes=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            )
        )
    return sorted(items, key=lambda item: (item.category, item.path.lower()))


def render_human(items: list[ScriptItem], include_history: bool) -> str:
    counts = Counter(item.category for item in items)
    total_size = sum(item.size_bytes for item in items)
    lines = [
        "Script inventory",
        f"root: {ROOT}",
        f"include_history: {str(include_history).lower()}",
        f"total_scripts: {len(items)}",
        f"total_size_bytes: {total_size}",
        "",
        "Counts by category:",
    ]
    for category in ("active", "helper", "legacy", "dependency", "backup"):
        lines.append(f"- {category}: {counts.get(category, 0)}")
    grouped: dict[str, list[ScriptItem]] = defaultdict(list)
    for item in items:
        grouped[item.category].append(item)
    for category in ("active", "helper", "legacy", "dependency", "backup"):
        category_items = grouped.get(category, [])
        if not category_items:
            continue
        if category == "dependency" and not include_history:
            lines.extend(["", "dependency:", "- dependency details omitted by default; use --json for structured output"])
            continue
        lines.extend(["", f"{category}:"])
        for item in category_items[:MAX_HUMAN_ITEMS_PER_CATEGORY]:
            lines.append(f"- {item.path} ({item.size_bytes} bytes) - {item.role}")
        omitted = len(category_items) - MAX_HUMAN_ITEMS_PER_CATEGORY
        if omitted > 0:
            lines.append(f"- ... {omitted} more omitted; use --json for structured output")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Low-noise script inventory")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument(
        "--include-history",
        action="store_true",
        help="Include backup/dependency/history trees; noisy by design",
    )
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Additional or replacement scan root; repeatable",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    roots = args.roots or DEFAULT_ROOTS
    items = build_inventory(roots, args.include_history)
    if args.json:
        payload = {
            "ok": True,
            "workspace": str(ROOT),
            "include_history": bool(args.include_history),
            "excluded_dirs": sorted(DEFAULT_EXCLUDED_DIRS) if not args.include_history else [],
            "counts": dict(Counter(item.category for item in items)),
            "items": [asdict(item) for item in items],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_human(items, bool(args.include_history)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
