#!/usr/bin/env python3
"""Approve one audited skill version in the soft-admission registry."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager")
REGISTRY_DIR = WORKSPACE_ROOT / "_bridge" / "shared" / "skill-system"
REGISTRY_PATH = REGISTRY_DIR / "registry.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_path_key(path: str | Path) -> str:
    return str(Path(path).resolve()).lower()


def load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def save_registry(registry: dict[str, Any]) -> None:
    registry["generated_at"] = now_iso()
    REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_registry_entry(registry: dict[str, Any], skill_id: str | None, skill_path: str | None) -> dict[str, Any]:
    skills = registry.get("skills", [])
    if skill_id:
        for row in skills:
            if row["skill_id"] == skill_id:
                return row
        raise SystemExit(f"Skill not found by skill_id: {skill_id}")

    if skill_path:
        wanted = normalize_path_key(skill_path)
        for row in skills:
            if normalize_path_key(row["path"]) == wanted:
                return row
        raise SystemExit(f"Skill not found by path: {skill_path}")

    raise SystemExit("Provide either --skill-id or --path")


def approve_entry(entry: dict[str, Any]) -> dict[str, Any]:
    if entry.get("state") != "approval-pending":
        raise SystemExit(f"Skill is not approval-pending: {entry['skill_id']} ({entry.get('state')})")
    report_path_raw = entry.get("last_report_path")
    if not report_path_raw:
        raise SystemExit(f"Skill has no audit report to approve: {entry['skill_id']}")
    report_path = Path(report_path_raw)
    if not report_path.exists():
        raise SystemExit(f"Audit report path does not exist: {report_path}")
    if not entry.get("last_audited_at"):
        raise SystemExit(f"Skill has no last_audited_at timestamp: {entry['skill_id']}")
    entry["state"] = "approved"
    entry["last_approved_at"] = now_iso()
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve one audited skill version from the admission registry.")
    parser.add_argument("--skill-id", help="Skill id from registry.json")
    parser.add_argument("--path", help="Absolute path to the skill directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args()

    registry = load_registry()
    entry = resolve_registry_entry(registry, args.skill_id, args.path)
    approve_entry(entry)
    save_registry(registry)

    payload = {
        "skill_id": entry["skill_id"],
        "name": entry["name"],
        "state": entry["state"],
        "last_approved_at": entry["last_approved_at"],
        "last_report_path": entry["last_report_path"],
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
