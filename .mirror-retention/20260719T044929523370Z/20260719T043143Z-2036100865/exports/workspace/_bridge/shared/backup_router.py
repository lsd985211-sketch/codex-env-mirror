#!/usr/bin/env python3
"""Unified backup router for local edits.

The router plans and creates classified backups before file edits. It prefers
module-owned backup directories when they already exist; otherwise it falls back
to a project-wide or resource-library-wide backup root. Every created backup is
recorded in a manifest so later doctor/repair tools can audit and restore it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .json_cli import now_iso
except ImportError:
    from json_cli import now_iso


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_ROOT = PROJECT_ROOT / "_bridge"
WORK_GIT_ROOT = Path(os.environ.get("WORKTREE_GIT_ROOT") or str(PROJECT_ROOT.parent)).expanduser().resolve()
CODEX_APP_HOME = Path(
    os.environ.get("CODEX_HOME") or os.environ.get("WSL_CODEX_HOME") or (Path.home() / ".codex-app")
).expanduser().resolve()
WORK_GIT_BACKUP_ROOT = CODEX_APP_HOME / "backups" / "work-git"
RESOURCE_LIBRARY_ROOT = Path(
    os.environ.get(
        "CODEX_RESOURCE_LIBRARY_ROOT",
        str(Path.home() / "Desktop" / "Codex资源库") if os.name != "nt" else r"C:\Users\45543\Desktop\Codex资源库",
    )
).expanduser().resolve()
RESOURCE_DOC_ROOT = RESOURCE_LIBRARY_ROOT / "文档"
CODEX_SKILLS_ROOT = Path.home() / ".codex" / "skills"
CODEX_SKILLS_ROOTS = (CODEX_APP_HOME / "skills", CODEX_SKILLS_ROOT)
CODEX_SKILLS_BACKUP_ROOT = RESOURCE_LIBRARY_ROOT / "_backup" / "skills"

PREFERRED_BACKUP_DIR_NAMES = ("_backup", "备份", "backups")
RESOURCE_LIBRARY_BACKUP_DIR_NAMES = ("_backup",)
RESERVED_SEARCH_PARTS = {"runtime", "logs", "attachments", "__pycache__", "node_modules", "pnpm-store"}
DEFAULT_REMARK = "local-edit"
LEGACY_WORK_GIT_BACKUP_ROOT = BRIDGE_ROOT / "backups"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_slug(value: str, *, default: str = DEFAULT_REMARK, limit: int = 80) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9._\-\u4e00-\u9fff]+", "-", text)
    text = text.strip(".-_")
    if not text:
        text = default
    return text[:limit].strip(".-_") or default


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def lexical_is_relative_to(path: Path, root: Path) -> bool:
    """Check a path without resolving symlinks, preserving caller intent."""
    try:
        Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root)))
        return True
    except ValueError:
        return False


def is_codex_skill_path(path: Path) -> bool:
    return any(
        lexical_is_relative_to(path, root) or is_relative_to(path, root)
        for root in CODEX_SKILLS_ROOTS
    )


def is_work_git_path(path: Path) -> bool:
    """Return true for files in the active Work Git checkout."""
    root = WORK_GIT_ROOT.resolve()
    return (root / ".git").exists() and is_relative_to(path.resolve(), root)


def classify_path(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    text = str(resolved).lower()
    parts = [part.lower() for part in resolved.parts]
    if is_relative_to(resolved, RESOURCE_LIBRARY_ROOT):
        if "邮箱区" in resolved.parts:
            return {"domain": "resource-library", "category": "email"}
        if "定时模块" in resolved.parts:
            return {"domain": "resource-library", "category": "scheduler"}
        if "系统维护" in resolved.parts:
            return {"domain": "resource-library", "category": "maintenance"}
        return {"domain": "resource-library", "category": "documents"}
    if "_bridge" in parts:
        if "mobile_openclaw_bridge" in parts:
            return {"domain": "project", "category": "bridge"}
        if "shared" in parts:
            return {"domain": "project", "category": "shared"}
        if "slash_commands" in parts:
            return {"domain": "project", "category": "slash-commands"}
        if "tools" in parts:
            return {"domain": "project", "category": "tooling"}
        return {"domain": "project", "category": "bridge"}
    if ".codex" in parts or "\\.codex\\" in text:
        return {"domain": "codex-profile", "category": "codex-config"}
    return {"domain": "project", "category": "misc"}


def planned_backup_dir_near(path: Path) -> Path | None:
    resolved = Path(os.path.abspath(path)) if is_codex_skill_path(path) else path.resolve()
    if is_codex_skill_path(path):
        return None
    parent = resolved.parent
    candidates: list[Path] = []
    current = parent
    stop_roots = [PROJECT_ROOT.resolve(), RESOURCE_DOC_ROOT.resolve(), RESOURCE_LIBRARY_ROOT.resolve()]
    for _ in range(5):
        if current.name.lower() in RESERVED_SEARCH_PARTS:
            break
        names = RESOURCE_LIBRARY_BACKUP_DIR_NAMES if is_relative_to(current, RESOURCE_LIBRARY_ROOT) else PREFERRED_BACKUP_DIR_NAMES
        for name in names:
            candidate = current / name
            if candidate.exists() and candidate.is_dir():
                candidates.append(candidate)
        if any(current == root for root in stop_roots):
            break
        if current.parent == current:
            break
        current = current.parent
    if not candidates:
        return None
    candidates.sort(key=lambda item: len(item.parts), reverse=True)
    return candidates[0]


def fallback_backup_root(path: Path, category: str) -> Path:
    resolved = path.resolve()
    month = now_utc().strftime("%Y%m")
    if is_codex_skill_path(path):
        return CODEX_SKILLS_BACKUP_ROOT / month / category
    if is_work_git_path(resolved):
        return WORK_GIT_BACKUP_ROOT / month / category
    if is_relative_to(resolved, RESOURCE_LIBRARY_ROOT):
        return RESOURCE_LIBRARY_ROOT / "_backup" / month / category
    if any(is_relative_to(resolved, root) for root in CODEX_SKILLS_ROOTS):
        return CODEX_SKILLS_BACKUP_ROOT / month / category
    return BRIDGE_ROOT / "backups" / "manual" / month / category


def routed_backup_dir(planned_dir: Path | None, path: Path, category: str) -> tuple[Path, str]:
    month = now_utc().strftime("%Y%m")
    if is_codex_skill_path(path):
        return CODEX_SKILLS_BACKUP_ROOT / month / category, "codex_skill_external_backup_root"
    if is_work_git_path(path):
        return WORK_GIT_BACKUP_ROOT / month / category, "wsl_work_git_external_backup_root"
    if planned_dir is None:
        return fallback_backup_root(path, category), "fallback_unified_backup_root"
    if planned_dir.resolve() == (BRIDGE_ROOT / "backups").resolve():
        return planned_dir / "manual" / month / category, "project_planned_backup_root"
    if planned_dir.name.lower() in {"_backup", "backups"} or planned_dir.name == "备份":
        return planned_dir / month / category, "module_planned_backup_dir"
    return planned_dir, "module_planned_backup_dir"


def relative_original_path(path: Path) -> Path:
    resolved = path.resolve()
    for root in (PROJECT_ROOT.resolve(), RESOURCE_LIBRARY_ROOT.resolve(), Path.home().resolve()):
        try:
            return resolved.relative_to(root)
        except ValueError:
            continue
    drive = resolved.drive.replace(":", "") or "root"
    return Path(drive) / Path(*resolved.parts[1:])


def backup_set_dir_for(backup_dir: Path, remark: str) -> Path:
    stamp = now_utc().strftime("%Y%m%d-%H%M%S")
    safe_remark = sanitize_slug(remark)
    return backup_dir / f"{stamp}-{safe_remark}"


def backup_target_for(path: Path, backup_set_dir: Path) -> Path:
    rel = relative_original_path(path)
    return backup_set_dir / rel


def plan(paths: list[str], *, remark: str = DEFAULT_REMARK, purpose: str = "", category: str = "") -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for value in paths:
        src = Path(value).expanduser()
        if not src.is_absolute():
            src = (PROJECT_ROOT / src).resolve()
        resolved = src.resolve()
        classification = classify_path(resolved)
        item_category = sanitize_slug(category or classification["category"], default="misc")
        planned_dir = planned_backup_dir_near(resolved)
        backup_dir, route = routed_backup_dir(planned_dir, src, item_category)
        backup_set_dir = backup_set_dir_for(backup_dir, remark)
        target = backup_target_for(resolved, backup_set_dir)
        items.append(
            {
                "source_path": str(resolved),
                "exists": resolved.exists(),
                "is_file": resolved.is_file(),
                "domain": classification["domain"],
                "category": item_category,
                "route": route,
                "backup_dir": str(backup_dir),
                "backup_set_dir": str(backup_set_dir),
                "backup_path": str(target),
            }
        )
    return {
        "schema": "backup_router.plan.v1",
        "ok": all(item["exists"] and item["is_file"] for item in items),
        "generated_at": now_iso(),
        "remark": sanitize_slug(remark),
        "purpose": purpose,
        "items": items,
        "dry_run_contract": {
            "writes_files": False,
            "copies_files": False,
            "deletes_files": False,
        },
    }


def create_backup(
    paths: list[str],
    *,
    remark: str = DEFAULT_REMARK,
    purpose: str = "",
    category: str = "",
    trigger: str = "codex",
) -> dict[str, Any]:
    planned = plan(paths, remark=remark, purpose=purpose, category=category)
    if not planned.get("ok"):
        return {**planned, "schema": "backup_router.create.v1", "ok": False, "reason": "one_or_more_sources_missing_or_not_files"}
    created_items: list[dict[str, Any]] = []
    manifest_dirs: set[Path] = set()
    for item in planned.get("items", []):
        src = Path(str(item["source_path"]))
        dst = Path(str(item["backup_path"]))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        src_hash = sha256_file(src)
        dst_hash = sha256_file(dst)
        backup_item = {
            **item,
            "backup_path": str(dst),
            "source_sha256": src_hash,
            "backup_sha256": dst_hash,
            "hash_match": src_hash == dst_hash,
            "size_bytes": src.stat().st_size,
        }
        created_items.append(backup_item)
        manifest_dirs.add(Path(str(item["backup_set_dir"])))

    manifests: list[str] = []
    for manifest_dir in sorted(manifest_dirs, key=str):
        manifest_items = [item for item in created_items if str(item["backup_path"]).startswith(str(manifest_dir))]
        manifest = {
            "schema": "backup_router.manifest.v1",
            "created_at": now_iso(),
            "remark": sanitize_slug(remark),
            "purpose": purpose,
            "category": sanitize_slug(category or (manifest_items[0].get("category") if manifest_items else "misc")),
            "trigger": trigger,
            "restore": "Copy each backup_path back to source_path after reviewing sha256 and purpose.",
            "items": manifest_items,
        }
        manifest_path = manifest_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifests.append(str(manifest_path))
    return {
        "schema": "backup_router.create.v1",
        "ok": bool(created_items) and all(item.get("hash_match") for item in created_items),
        "generated_at": now_iso(),
        "created_count": len(created_items),
        "manifest_paths": manifests,
        "items": created_items,
    }


def validate_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"path": str(path), "ok": False, "reason": f"manifest_parse_failed: {exc}"}
    schema = str(payload.get("schema") or "")
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    looks_like_backup_manifest = schema.startswith("backup_router.") or any(
        isinstance(item, dict) and item.get("backup_path")
        for item in items
    )
    if not looks_like_backup_manifest:
        return {"path": str(path), "ok": True, "skipped": True, "reason": "non_backup_manifest"}
    issues: list[dict[str, Any]] = []
    for item in items:
        backup_path = Path(str(item.get("backup_path") or ""))
        expected_hash = str(item.get("backup_sha256") or item.get("source_sha256") or "")
        if not backup_path.exists():
            issues.append({"backup_path": str(backup_path), "reason": "backup_missing"})
            continue
        if expected_hash and sha256_file(backup_path) != expected_hash:
            issues.append({"backup_path": str(backup_path), "reason": "hash_mismatch"})
    return {
        "path": str(path),
        "ok": not issues and bool(items),
        "item_count": len(items),
        "issues": issues,
    }


def validate(root: str = "") -> dict[str, Any]:
    bases = [Path(root).expanduser()] if root else [WORK_GIT_BACKUP_ROOT, LEGACY_WORK_GIT_BACKUP_ROOT]
    normalized_bases: list[Path] = []
    for base in bases:
        if not base.is_absolute():
            base = (PROJECT_ROOT / base).resolve()
        normalized_bases.append(base.resolve())
    manifests = sorted({path for base in normalized_bases if base.exists() for path in base.rglob("manifest.json")})
    results = [validate_manifest(path) for path in manifests]
    checked = [item for item in results if not item.get("skipped")]
    failures = [item for item in checked if not item.get("ok")]
    return {
        "schema": "backup_router.validate.v1",
        "ok": not failures,
        "generated_at": now_iso(),
        "root": str(normalized_bases[0]) if len(normalized_bases) == 1 else [str(base) for base in normalized_bases],
        "manifest_count": len(checked),
        "skipped_manifest_count": len(results) - len(checked),
        "failure_count": len(failures),
        "failures": failures[:50],
    }


def migrate_work_git_backups(*, apply: bool = False) -> dict[str, Any]:
    """Move legacy Work Git backup sets outside the active repository."""
    source_root = LEGACY_WORK_GIT_BACKUP_ROOT.resolve()
    destination_root = (WORK_GIT_BACKUP_ROOT / "migrated" / "legacy-bridge-backups").resolve()
    manifests = sorted(source_root.rglob("manifest.json")) if source_root.exists() else []
    source_files = sorted(path for path in source_root.rglob("*") if path.is_file()) if source_root.exists() else []
    result: dict[str, Any] = {
        "schema": "backup_router.migrate_work_git.v1",
        "ok": True,
        "generated_at": now_iso(),
        "source_root": str(source_root),
        "destination_root": str(destination_root),
        "manifest_count": len(manifests),
        "source_file_count": len(source_files),
        "dry_run_contract": {"copies_files": apply, "rewrites_manifests": apply, "deletes_legacy_tree": apply},
    }
    if not source_root.exists():
        result["reason"] = "legacy_backup_root_missing"
        return result
    if source_files and not manifests:
        return {**result, "ok": False, "reason": "legacy_files_without_manifests"}
    if is_relative_to(destination_root, source_root) or is_relative_to(source_root, destination_root):
        return {**result, "ok": False, "reason": "migration_roots_overlap"}
    if not apply:
        result["planned_manifest_paths"] = [str(path) for path in manifests[:100]]
        result["planned_file_count"] = len(source_files)
        return result
    if destination_root.exists():
        return {**result, "ok": False, "reason": "destination_exists_refusing_merge"}

    destination_root.mkdir(parents=True, exist_ok=False)
    copy_failures: list[dict[str, str]] = []
    for source in source_files:
        target = destination_root / source.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if source.name != "manifest.json" and sha256_file(source) != sha256_file(target):
            copy_failures.append({"source": str(source), "target": str(target), "reason": "copy_hash_mismatch"})
    if copy_failures:
        return {**result, "ok": False, "reason": "copied_file_hash_validation_failed", "failures": copy_failures[:50]}

    rewritten = 0
    for source_manifest in manifests:
        target_manifest = destination_root / source_manifest.relative_to(source_root)
        payload = json.loads(target_manifest.read_text(encoding="utf-8"))
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            for field in ("backup_path", "backup_dir", "backup_set_dir"):
                old_value = Path(str(item.get(field) or ""))
                if old_value.is_absolute() and is_relative_to(old_value, source_root):
                    item[field] = str(destination_root / old_value.relative_to(source_root))
            item["route"] = "wsl_work_git_external_backup_root_migrated"
        payload["migrated_from"] = str(source_manifest)
        payload["migration_schema"] = "backup_router.work_git_backup_migration.v1"
        target_manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rewritten += 1

    validation = validate(str(destination_root))
    result["rewritten_manifest_count"] = rewritten
    result["copied_file_hash_match_count"] = len(source_files) - len(manifests)
    result["validation"] = validation
    if not validation.get("ok") or int(validation.get("manifest_count") or 0) != len(manifests):
        return {**result, "ok": False, "reason": "migrated_manifest_validation_failed"}
    shutil.rmtree(source_root)
    result["removed_source_root"] = True
    result["migrated_file_count"] = sum(1 for path in destination_root.rglob("*") if path.is_file())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan and create routed backups with manifests")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "create"):
        p = sub.add_parser(name)
        p.add_argument("paths", nargs="+")
        p.add_argument("--remark", default=DEFAULT_REMARK)
        p.add_argument("--purpose", default="")
        p.add_argument("--category", default="")
        p.add_argument("--trigger", default="codex")
    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--root", default="")
    p_migrate = sub.add_parser("migrate-work-git")
    p_migrate.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "plan":
        payload = plan(list(args.paths), remark=args.remark, purpose=args.purpose, category=args.category)
    elif args.command == "create":
        payload = create_backup(
            list(args.paths),
            remark=args.remark,
            purpose=args.purpose,
            category=args.category,
            trigger=args.trigger,
        )
    elif args.command == "validate":
        payload = validate(args.root)
    else:
        payload = migrate_work_git_backups(apply=bool(args.apply))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
