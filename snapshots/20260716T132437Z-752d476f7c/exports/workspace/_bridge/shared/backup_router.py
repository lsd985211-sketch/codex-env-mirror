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
RESOURCE_LIBRARY_ROOT = Path(r"C:\Users\45543\Desktop\Codex资源库")
RESOURCE_DOC_ROOT = RESOURCE_LIBRARY_ROOT / "文档"
CODEX_SKILLS_ROOT = Path.home() / ".codex" / "skills"
CODEX_SKILLS_BACKUP_ROOT = RESOURCE_LIBRARY_ROOT / "_backup" / "skills"

PREFERRED_BACKUP_DIR_NAMES = ("_backup", "备份", "backups")
RESOURCE_LIBRARY_BACKUP_DIR_NAMES = ("_backup",)
RESERVED_SEARCH_PARTS = {"runtime", "logs", "attachments", "__pycache__", "node_modules", "pnpm-store"}
DEFAULT_REMARK = "local-edit"


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
    resolved = path.resolve()
    if is_relative_to(resolved, CODEX_SKILLS_ROOT):
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
    if is_relative_to(resolved, RESOURCE_LIBRARY_ROOT):
        return RESOURCE_LIBRARY_ROOT / "_backup" / month / category
    if is_relative_to(resolved, CODEX_SKILLS_ROOT):
        return CODEX_SKILLS_BACKUP_ROOT / month / category
    return BRIDGE_ROOT / "backups" / "manual" / month / category


def routed_backup_dir(planned_dir: Path | None, path: Path, category: str) -> tuple[Path, str]:
    month = now_utc().strftime("%Y%m")
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
        backup_dir, route = routed_backup_dir(planned_dir, resolved, item_category)
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
    base = Path(root).expanduser() if root else BRIDGE_ROOT / "backups"
    if not base.is_absolute():
        base = (PROJECT_ROOT / base).resolve()
    manifests = sorted(base.rglob("manifest.json")) if base.exists() else []
    results = [validate_manifest(path) for path in manifests]
    checked = [item for item in results if not item.get("skipped")]
    failures = [item for item in checked if not item.get("ok")]
    return {
        "schema": "backup_router.validate.v1",
        "ok": not failures,
        "generated_at": now_iso(),
        "root": str(base),
        "manifest_count": len(checked),
        "skipped_manifest_count": len(results) - len(checked),
        "failure_count": len(failures),
        "failures": failures[:50],
    }


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
    else:
        payload = validate(args.root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
