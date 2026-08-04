#!/usr/bin/env python3
"""Defer or reject one skill in the soft-admission registry."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager")
REGISTRY_DIR = WORKSPACE_ROOT / "_bridge" / "shared" / "skill-system"
REGISTRY_PATH = REGISTRY_DIR / "registry.json"
DECISION_STATES = {"deferred", "rejected"}
ALLOWED_FROM_STATES = {"audit-pending", "audited", "approval-pending", "deferred"}


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


def apply_decision(entry: dict[str, Any], decision: str) -> dict[str, Any]:
    if decision not in DECISION_STATES:
        raise SystemExit(f"Unsupported decision: {decision}")
    current_state = entry.get("state")
    if current_state not in ALLOWED_FROM_STATES:
        raise SystemExit(f"Skill cannot move to {decision} from state {current_state}: {entry['skill_id']}")
    entry["state"] = decision
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Defer or reject one skill from the admission registry.")
    parser.add_argument("decision", choices=sorted(DECISION_STATES), help="Decision to apply")
    parser.add_argument("--skill-id", help="Skill id from registry.json")
    parser.add_argument("--path", help="Absolute path to the skill directory")
    args = parser.parse_args()

    registry = load_registry()
    entry = resolve_registry_entry(registry, args.skill_id, args.path)
    apply_decision(entry, args.decision)
    save_registry(registry)

    payload = {
        "skill_id": entry["skill_id"],
        "name": entry["name"],
        "state": entry["state"],
        "last_report_path": entry.get("last_report_path"),
        "last_audited_at": entry.get("last_audited_at"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
