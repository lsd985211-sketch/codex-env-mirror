#!/usr/bin/env python3
"""Discover skills, classify source, and update the soft-admission registry."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager")
CODEX_HOME = Path(r"C:\Users\45543\.codex")
REGISTRY_DIR = WORKSPACE_ROOT / "_bridge" / "shared" / "skill-system"
REGISTRY_PATH = REGISTRY_DIR / "registry.json"
SNAPSHOT_DIR = REGISTRY_DIR / "snapshots"
REPORTS_DIR = REGISTRY_DIR / "reports"
CHECKPOINTS_DIR = REGISTRY_DIR / "checkpoints"

SKILL_ROOT_SPECS = [
    (CODEX_HOME / "skills", "user-managed"),
]

PLUGIN_CACHE_ROOT = CODEX_HOME / "plugins" / "cache"
PLUGIN_BUNDLED_ROOT = CODEX_HOME / ".tmp" / "bundled-marketplaces"

IGNORED_DIR_NAMES = {"__pycache__", ".git", ".hg", ".svn", "node_modules"}
IGNORED_PLUGIN_PATH_MARKERS = ("guard-staging", ".staging-", "plugin-backup-")
SOURCE_PRIORITY = {
    "user-managed": 0,
    "system-managed": 1,
    "plugin-managed": 2,
}


@dataclass(frozen=True)
class SkillRecord:
    name: str
    path: Path
    source: str


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ensure_layout() -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_PATH.exists():
        REGISTRY_PATH.write_text(
            json.dumps({"version": 1, "generated_at": None, "skills": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_registry() -> dict[str, Any]:
    ensure_layout()
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def save_registry(registry: dict[str, Any]) -> None:
    registry["generated_at"] = now_iso()
    REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return hash_bytes(path.read_bytes())


def hash_tree(path: Path) -> str | None:
    if not path.exists() or not path.is_dir():
        return None
    files = []
    for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = file_path.relative_to(path).as_posix()
        if any(part in IGNORED_DIR_NAMES for part in file_path.parts):
            continue
        files.append((rel, hash_bytes(file_path.read_bytes())))
    if not files:
        return None
    material = "\n".join(f"{rel}:{digest}" for rel, digest in files).encode("utf-8")
    return hash_bytes(material)


def has_ignored_dir_part(path: Path) -> bool:
    return any(part in IGNORED_DIR_NAMES for part in path.parts)


def is_user_system_subtree(root: Path, path: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return False
    return bool(rel_parts) and rel_parts[0] == ".system"


def is_noisy_plugin_path(path: Path) -> bool:
    lowered_parts = [part.lower() for part in path.parts]
    return any(marker in part for part in lowered_parts for marker in IGNORED_PLUGIN_PATH_MARKERS)


def extract_declared_primary_layer(skill_md: Path) -> str | None:
    if not skill_md.exists():
        return None
    text = skill_md.read_text(encoding="utf-8", errors="ignore")
    marker = "- Primary layer:"
    for line in text.splitlines():
        if line.strip().startswith(marker):
            return line.split(":", 1)[1].strip() or None
    return None


def classify_change_level(previous: dict[str, Any] | None, current: dict[str, str | None]) -> str:
    if previous is None:
        return "critical"
    old = previous.get("fingerprints", {})
    if old.get("skill_md") != current.get("skill_md"):
        return "critical"
    if (
        old.get("agents") != current.get("agents")
        or old.get("scripts") != current.get("scripts")
        or old.get("references") != current.get("references")
    ):
        return "behavioral"
    if old.get("assets") != current.get("assets"):
        return "informational"
    return "none"


def build_skill_id(source: str, path: Path) -> str:
    raw = f"{source}:{path.resolve().as_posix()}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def normalize_path_key(path: Path | str) -> str:
    return str(Path(path).resolve()).lower()


def choose_reclassified_state(previous: dict[str, Any], new_source: str) -> str:
    previous_source = previous.get("source")
    previous_state = previous.get("state", "discovered")
    if previous_source == new_source:
        return previous_state
    if previous_state == "audit-pending" and previous_source == "user-managed" and new_source != "user-managed":
        return "discovered"
    return previous_state


def should_reset_approval_fields(previous: dict[str, Any] | None, change_level: str, state: str) -> bool:
    if previous is None:
        return False
    if previous.get("state") != "approved":
        return False
    if state != "audit-pending":
        return False
    return change_level in {"critical", "behavioral"}


def discover_from_root(root: Path, source: str) -> list[SkillRecord]:
    if not root.exists():
        return []
    skills = []
    for skill_md in root.rglob("SKILL.md"):
        parent = skill_md.parent
        if has_ignored_dir_part(parent):
            continue
        if source == "user-managed" and is_user_system_subtree(root, parent):
            continue
        skills.append(SkillRecord(name=parent.name, path=parent, source=source))
    return skills


def discover_plugin_skills() -> list[SkillRecord]:
    skills: list[SkillRecord] = []
    for base in (PLUGIN_CACHE_ROOT, PLUGIN_BUNDLED_ROOT):
        if not base.exists():
            continue
        for skill_md in base.rglob("SKILL.md"):
            parent = skill_md.parent
            if has_ignored_dir_part(parent):
                continue
            if is_noisy_plugin_path(parent):
                continue
            skills.append(SkillRecord(name=parent.name, path=parent, source="plugin-managed"))
    return skills


def discover_system_skills() -> list[SkillRecord]:
    system_root = CODEX_HOME / "skills" / ".system"
    return discover_from_root(system_root, "system-managed")


def dedupe_skills(skills: list[SkillRecord]) -> list[SkillRecord]:
    best: dict[str, SkillRecord] = {}
    for skill in skills:
        key = normalize_path_key(skill.path)
        existing = best.get(key)
        if existing is None or SOURCE_PRIORITY[skill.source] < SOURCE_PRIORITY[existing.source]:
            best[key] = skill
    return sorted(best.values(), key=lambda item: (item.source, str(item.path).lower()))


def discover_all_skills() -> list[SkillRecord]:
    results: list[SkillRecord] = []
    for root, source in SKILL_ROOT_SPECS:
        results.extend(discover_from_root(root, source))
    results.extend(discover_system_skills())
    results.extend(discover_plugin_skills())
    return dedupe_skills(results)


def fingerprint_skill(skill_dir: Path) -> dict[str, str | None]:
    return {
        "skill_md": hash_file(skill_dir / "SKILL.md"),
        "agents": hash_file(skill_dir / "agents" / "openai.yaml"),
        "scripts": hash_tree(skill_dir / "scripts"),
        "references": hash_tree(skill_dir / "references"),
        "assets": hash_tree(skill_dir / "assets"),
    }


def update_registry(existing: dict[str, Any], discovered_skills: list[SkillRecord]) -> tuple[dict[str, Any], dict[str, int]]:
    by_id = {entry["skill_id"]: entry for entry in existing.get("skills", [])}
    by_path = {normalize_path_key(entry["path"]): entry for entry in existing.get("skills", [])}
    existing_rows = list(existing.get("skills", []))
    used_skill_ids: set[str] = set()
    timestamp = now_iso()
    stats = {"new": 0, "changed": 0, "audit_pending": 0, "unchanged": 0}
    updated_rows: list[dict[str, Any]] = []

    for skill in discovered_skills:
        generated_skill_id = build_skill_id(skill.source, skill.path)
        previous = by_id.get(generated_skill_id)
        previous_by_path = by_path.get(normalize_path_key(skill.path))
        previous_effective = previous or previous_by_path
        fingerprints = fingerprint_skill(skill.path)
        if previous_effective is None:
            relocation_matches = [
                entry
                for entry in existing_rows
                if str(entry.get("skill_id") or "") not in used_skill_ids
                and entry.get("source") == skill.source
                and entry.get("name") == skill.name
                and entry.get("fingerprints") == fingerprints
            ]
            if len(relocation_matches) == 1:
                previous_effective = relocation_matches[0]
        skill_id = str(previous_effective.get("skill_id")) if previous_effective else generated_skill_id
        used_skill_ids.add(skill_id)
        change_level = classify_change_level(previous_effective, fingerprints)
        declared_layer = extract_declared_primary_layer(skill.path / "SKILL.md")

        if previous_effective is None:
            state = "audit-pending" if skill.source == "user-managed" else "discovered"
            stats["new"] += 1
        else:
            state = choose_reclassified_state(previous_effective, skill.source)
            if change_level in {"critical", "behavioral"}:
                stats["changed"] += 1
                if state == "approved" or skill.source == "user-managed":
                    state = "audit-pending"
            elif change_level == "informational":
                stats["changed"] += 1
            else:
                stats["unchanged"] += 1

        if state == "audit-pending":
            stats["audit_pending"] += 1

        reset_approval_fields = should_reset_approval_fields(previous_effective, change_level, state)

        row = {
            "skill_id": skill_id,
            "name": skill.name,
            "path": str(skill.path.resolve()),
            "source": skill.source,
            "state": state,
            "declared_primary_layer": declared_layer,
            "last_detected_at": timestamp,
            "last_audited_at": previous_effective.get("last_audited_at") if previous_effective else None,
            "last_approved_at": None if reset_approval_fields else (previous_effective.get("last_approved_at") if previous_effective else None),
            "last_change_level": change_level,
            "last_report_path": None if reset_approval_fields else (previous_effective.get("last_report_path") if previous_effective else None),
            "fingerprints": fingerprints,
        }
        updated_rows.append(row)

        snapshot_path = SNAPSHOT_DIR / f"{skill_id}.json"
        snapshot_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")

    existing["skills"] = sorted(updated_rows, key=lambda item: (item["source"], item["name"], item["path"]))
    return existing, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover skills and update the soft-admission registry.")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    parser.add_argument("--roots-only", action="store_true", help="Only print discovered root categories and exit")
    args = parser.parse_args()

    if args.roots_only:
        payload = {
            "roots": [
                {"path": str(path), "source": source, "exists": path.exists()}
                for path, source in SKILL_ROOT_SPECS
            ]
            + [
                {"path": str(CODEX_HOME / "skills" / ".system"), "source": "system-managed", "exists": (CODEX_HOME / "skills" / ".system").exists()},
                {"path": str(PLUGIN_CACHE_ROOT), "source": "plugin-managed", "exists": PLUGIN_CACHE_ROOT.exists()},
                {"path": str(PLUGIN_BUNDLED_ROOT), "source": "plugin-managed", "exists": PLUGIN_BUNDLED_ROOT.exists()},
            ]
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    registry = load_registry()
    discovered_skills = discover_all_skills()
    registry, stats = update_registry(registry, discovered_skills)
    save_registry(registry)

    payload = {
        "checked": len(discovered_skills),
        "new": stats["new"],
        "changed": stats["changed"],
        "unchanged": stats["unchanged"],
        "audit_pending": stats["audit_pending"],
        "registry": str(REGISTRY_PATH),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Checked={payload['checked']} New={payload['new']} Changed={payload['changed']} Unchanged={payload['unchanged']}")
        print(f"AuditPending={payload['audit_pending']}")
        print(f"Registry={payload['registry']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
