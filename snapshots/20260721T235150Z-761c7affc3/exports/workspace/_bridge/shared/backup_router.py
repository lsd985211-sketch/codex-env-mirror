#!/usr/bin/env python3
"""Git-aware recovery router for local edits.

Clean tracked bytes are already stored by Git and receive a verified HEAD/blob
reference. Dirty, untracked, runtime, and non-Git bytes are copied to an
external backup root. Backup destinations inside any Git worktree are blocked.
Every operation writes an external manifest for later validation and restore.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
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
GIT_REPOSITORY_BACKUP_ROOT = CODEX_APP_HOME / "backups" / "git-repositories"
UNIFIED_BACKUP_ROOT = CODEX_APP_HOME / "backups" / "unified"
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


def _directory_digest_update(
    digest: "hashlib._Hash",
    *,
    kind: str,
    relative_path: str,
    size_bytes: int = 0,
    content_sha256: str = "",
) -> None:
    digest.update(
        json.dumps(
            [kind, relative_path, int(size_bytes), content_sha256],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def sha256_directory(path: Path) -> dict[str, Any]:
    """Return a deterministic content digest without following symlink targets."""

    digest = hashlib.sha256(b"backup_router.directory.v1\n")
    file_count = 0
    directory_count = 1
    symlink_count = 0
    total_bytes = 0
    for item in sorted(path.rglob("*"), key=lambda value: value.relative_to(path).as_posix()):
        relative = item.relative_to(path).as_posix()
        if item.is_symlink():
            target = os.readlink(item)
            symlink_count += 1
            _directory_digest_update(
                digest,
                kind="symlink",
                relative_path=relative,
                size_bytes=len(target.encode("utf-8")),
                content_sha256=hashlib.sha256(target.encode("utf-8")).hexdigest(),
            )
        elif item.is_dir():
            directory_count += 1
            _directory_digest_update(digest, kind="directory", relative_path=relative)
        elif item.is_file():
            size = item.stat().st_size
            file_count += 1
            total_bytes += size
            _directory_digest_update(
                digest,
                kind="file",
                relative_path=relative,
                size_bytes=size,
                content_sha256=sha256_file(item),
            )
        else:
            raise OSError(f"unsupported directory entry: {item}")
    return {
        "sha256": digest.hexdigest(),
        "file_count": file_count,
        "directory_count": directory_count,
        "symlink_count": symlink_count,
        "size_bytes": total_bytes,
    }


def copy_directory_verified(source: Path, destination: Path) -> dict[str, Any]:
    """Copy once from source, then independently hash the destination tree."""

    if destination.exists():
        raise FileExistsError(f"backup destination already exists: {destination}")
    destination.mkdir(parents=True, exist_ok=False)
    source_digest = hashlib.sha256(b"backup_router.directory.v1\n")
    file_count = 0
    directory_count = 1
    symlink_count = 0
    total_bytes = 0
    directories: list[tuple[Path, Path]] = [(source, destination)]
    for item in sorted(source.rglob("*"), key=lambda value: value.relative_to(source).as_posix()):
        relative_path = item.relative_to(source)
        relative = relative_path.as_posix()
        target = destination / relative_path
        if item.is_symlink():
            link_target = os.readlink(item)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(link_target, target_is_directory=item.is_dir())
            symlink_count += 1
            _directory_digest_update(
                source_digest,
                kind="symlink",
                relative_path=relative,
                size_bytes=len(link_target.encode("utf-8")),
                content_sha256=hashlib.sha256(link_target.encode("utf-8")).hexdigest(),
            )
        elif item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            directories.append((item, target))
            directory_count += 1
            _directory_digest_update(source_digest, kind="directory", relative_path=relative)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            file_digest = hashlib.sha256()
            size = 0
            with item.open("rb") as source_handle, target.open("xb") as target_handle:
                for chunk in iter(lambda: source_handle.read(4 * 1024 * 1024), b""):
                    target_handle.write(chunk)
                    file_digest.update(chunk)
                    size += len(chunk)
            shutil.copystat(item, target, follow_symlinks=False)
            file_count += 1
            total_bytes += size
            _directory_digest_update(
                source_digest,
                kind="file",
                relative_path=relative,
                size_bytes=size,
                content_sha256=file_digest.hexdigest(),
            )
        else:
            raise OSError(f"unsupported directory entry: {item}")
    for source_dir, target_dir in reversed(directories):
        shutil.copystat(source_dir, target_dir, follow_symlinks=False)
    backup = sha256_directory(destination)
    return {
        "source_sha256": source_digest.hexdigest(),
        "backup_sha256": backup["sha256"],
        "hash_match": source_digest.hexdigest() == backup["sha256"],
        "file_count": file_count,
        "directory_count": directory_count,
        "symlink_count": symlink_count,
        "size_bytes": total_bytes,
        "backup_inventory": backup,
    }


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


def find_git_root(path: Path) -> Path | None:
    """Return the enclosing Git worktree without relying on one configured root."""
    candidate = path.expanduser().resolve()
    if candidate.is_file() or not candidate.exists():
        candidate = candidate.parent
    for current in (candidate, *candidate.parents):
        marker = current / ".git"
        if marker.is_dir() or marker.is_file():
            return current
    return None


def run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(root), *args]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(
            command,
            124 if isinstance(exc, subprocess.TimeoutExpired) else 127,
            stdout="",
            stderr=str(exc),
        )


def git_file_reference(path: Path) -> dict[str, Any] | None:
    """Return an exact HEAD/blob reference when Git already stores current bytes."""
    root = find_git_root(path)
    if root is None or not path.is_file():
        return None
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    tracked = run_git(root, ["ls-files", "--error-unmatch", "--", relative])
    status = run_git(root, ["status", "--porcelain=v1", "--untracked-files=all", "--", relative])
    head = run_git(root, ["rev-parse", "--verify", "HEAD"])
    head_blob = run_git(root, ["rev-parse", f"HEAD:{relative}"])
    current_blob = run_git(root, ["hash-object", "--path", relative, str(path.resolve())])
    if any(result.returncode != 0 for result in (tracked, status, head, head_blob, current_blob)):
        return None
    if status.stdout.strip():
        return None
    head_value = head.stdout.strip()
    head_blob_value = head_blob.stdout.strip()
    current_blob_value = current_blob.stdout.strip()
    if not head_value or not head_blob_value or head_blob_value != current_blob_value:
        return None
    return {
        "repository_root": str(root.resolve()),
        "relative_path": relative,
        "commit": head_value,
        "blob": head_blob_value,
        "restore_command": f'git -C "{root.resolve()}" restore --source {head_value} -- "{relative}"',
    }


def git_repository_backup_root(root: Path) -> Path:
    identity = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:12]
    slug = sanitize_slug(root.name, default="repository", limit=48)
    return GIT_REPOSITORY_BACKUP_ROOT / f"{slug}-{identity}"


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
    return UNIFIED_BACKUP_ROOT / month / category


def routed_backup_dir(planned_dir: Path | None, path: Path, category: str) -> tuple[Path, str]:
    month = now_utc().strftime("%Y%m")
    git_root = find_git_root(path)
    if git_root is not None and git_root.resolve() == WORK_GIT_ROOT.resolve():
        return WORK_GIT_BACKUP_ROOT / month / category, "wsl_work_git_external_backup_root"
    if git_root is not None:
        return git_repository_backup_root(git_root) / month / category, "git_repository_external_backup_root"
    if is_codex_skill_path(path):
        return CODEX_SKILLS_BACKUP_ROOT / month / category, "codex_skill_external_backup_root"
    if planned_dir is None:
        return fallback_backup_root(path, category), "fallback_unified_backup_root"
    if find_git_root(planned_dir) is not None:
        return UNIFIED_BACKUP_ROOT / month / category, "git_worktree_destination_guard"
    if planned_dir.resolve() == (BRIDGE_ROOT / "backups").resolve():
        return UNIFIED_BACKUP_ROOT / month / category, "legacy_worktree_backup_redirect"
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


def backup_set_dir_for(backup_dir: Path, remark: str, operation_id: str = "") -> Path:
    stamp = operation_id or f"{now_utc().strftime('%Y%m%d-%H%M%S-%f')}-{uuid.uuid4().hex[:8]}"
    safe_remark = sanitize_slug(remark)
    return backup_dir / f"{stamp}-{safe_remark}"


def backup_target_for(path: Path, backup_set_dir: Path) -> Path:
    rel = relative_original_path(path)
    return backup_set_dir / rel


def plan(paths: list[str], *, remark: str = DEFAULT_REMARK, purpose: str = "", category: str = "") -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    operation_id = f"{now_utc().strftime('%Y%m%d-%H%M%S-%f')}-{uuid.uuid4().hex[:8]}"
    for value in paths:
        src = Path(value).expanduser()
        if not src.is_absolute():
            cwd_candidate = (Path.cwd() / src).resolve()
            project_candidate = (PROJECT_ROOT / src).resolve()
            src = cwd_candidate if cwd_candidate.exists() else project_candidate
        resolved = src.resolve()
        classification = classify_path(resolved)
        item_category = sanitize_slug(category or classification["category"], default="misc")
        planned_dir = planned_backup_dir_near(resolved)
        backup_dir, route = routed_backup_dir(planned_dir, src, item_category)
        backup_set_dir = backup_set_dir_for(backup_dir, remark, operation_id)
        target = backup_target_for(resolved, backup_set_dir)
        git_reference = git_file_reference(resolved)
        backup_mode = "git_head_reference" if git_reference else "external_copy"
        destination_git_root = find_git_root(backup_dir)
        destination_policy_ok = destination_git_root is None
        items.append(
            {
                "source_path": str(resolved),
                "exists": resolved.exists(),
                "is_file": resolved.is_file(),
                "is_dir": resolved.is_dir(),
                "source_kind": "file" if resolved.is_file() else "directory" if resolved.is_dir() else "other",
                "domain": classification["domain"],
                "category": item_category,
                "route": route,
                "backup_dir": str(backup_dir),
                "backup_set_dir": str(backup_set_dir),
                "backup_path": "" if git_reference else str(target),
                "backup_mode": backup_mode,
                "copy_required": git_reference is None,
                "git_reference": git_reference,
                "destination_policy_ok": destination_policy_ok,
                "destination_git_root": str(destination_git_root or ""),
            }
        )
    return {
        "schema": "backup_router.plan.v2",
        "ok": all(
            item["exists"]
            and (item["is_file"] or item["is_dir"])
            and item["destination_policy_ok"]
            for item in items
        ),
        "generated_at": now_iso(),
        "remark": sanitize_slug(remark),
        "purpose": purpose,
        "items": items,
        "dry_run_contract": {
            "writes_files": False,
            "copies_files": False,
            "deletes_files": False,
        },
        "policy": {
            "clean_tracked_files": "git_head_reference",
            "dirty_untracked_or_non_git_files": "external_copy",
            "backup_destination_inside_git_worktree": "blocked",
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
        destination_blocked = any(not item.get("destination_policy_ok", True) for item in planned.get("items", []))
        reason = "backup_destination_inside_git_worktree" if destination_blocked else "one_or_more_sources_missing_or_unsupported"
        return {**planned, "schema": "backup_router.create.v2", "ok": False, "reason": reason}
    created_items: list[dict[str, Any]] = []
    manifest_dirs: set[Path] = set()
    for item in planned.get("items", []):
        src = Path(str(item["source_path"]))
        if item.get("backup_mode") == "git_head_reference":
            src_hash = sha256_file(src)
            backup_item = {
                **item,
                "source_sha256": src_hash,
                "backup_sha256": "",
                "hash_match": True,
                "reference_valid": True,
                "size_bytes": src.stat().st_size,
            }
        else:
            dst = Path(str(item["backup_path"]))
            if src.is_dir():
                dst.parent.mkdir(parents=True, exist_ok=True)
                copied = copy_directory_verified(src, dst)
                backup_item = {**item, "backup_path": str(dst), **copied}
            else:
                src_hash = sha256_file(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
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
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_items = [item for item in created_items if Path(str(item["backup_set_dir"])) == manifest_dir]
        manifest = {
            "schema": "backup_router.manifest.v2",
            "created_at": now_iso(),
            "remark": sanitize_slug(remark),
            "purpose": purpose,
            "category": sanitize_slug(category or (manifest_items[0].get("category") if manifest_items else "misc")),
            "trigger": trigger,
            "restore": "Use git_reference.restore_command for Git-backed items; copy backup_path for external-copy items after reviewing hashes.",
            "items": manifest_items,
        }
        manifest_path = manifest_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifests.append(str(manifest_path))
    return {
        "schema": "backup_router.create.v2",
        "ok": bool(created_items) and all(item.get("hash_match") for item in created_items),
        "generated_at": now_iso(),
        "created_count": len(created_items),
        "copied_count": sum(1 for item in created_items if item.get("backup_mode") == "external_copy"),
        "git_reference_count": sum(1 for item in created_items if item.get("backup_mode") == "git_head_reference"),
        "manifest_paths": manifests,
        "items": created_items,
    }


def validate_git_reference(item: dict[str, Any]) -> dict[str, Any]:
    reference = item.get("git_reference") if isinstance(item.get("git_reference"), dict) else {}
    root = Path(str(reference.get("repository_root") or ""))
    relative = str(reference.get("relative_path") or "")
    commit = str(reference.get("commit") or "")
    expected_blob = str(reference.get("blob") or "")
    if not root.is_dir() or not relative or not commit or not expected_blob:
        return {"ok": False, "reason": "git_reference_fields_missing"}
    object_check = run_git(root, ["cat-file", "-e", f"{commit}:{relative}"])
    blob_check = run_git(root, ["rev-parse", f"{commit}:{relative}"])
    if object_check.returncode != 0 or blob_check.returncode != 0:
        return {"ok": False, "reason": "git_reference_unavailable"}
    actual_blob = blob_check.stdout.strip()
    if actual_blob != expected_blob:
        return {"ok": False, "reason": "git_blob_mismatch", "actual_blob": actual_blob}
    return {"ok": True, "commit": commit, "blob": actual_blob}


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
        if not isinstance(item, dict):
            issues.append({"reason": "manifest_item_invalid"})
            continue
        if item.get("backup_mode") == "git_head_reference":
            reference_validation = validate_git_reference(item)
            if not reference_validation.get("ok"):
                issues.append({
                    "source_path": str(item.get("source_path") or ""),
                    "reason": reference_validation.get("reason"),
                })
            continue
        backup_value = str(item.get("backup_path") or "")
        if not backup_value:
            issues.append({"reason": "backup_path_missing"})
            continue
        backup_path = Path(backup_value)
        expected_hash = str(item.get("backup_sha256") or item.get("source_sha256") or "")
        source_kind = str(item.get("source_kind") or "file")
        expected_type_ok = backup_path.is_dir() if source_kind == "directory" else backup_path.is_file()
        if not expected_type_ok:
            issues.append({"backup_path": str(backup_path), "reason": "backup_missing"})
            continue
        try:
            actual_hash = (
                str(sha256_directory(backup_path)["sha256"])
                if source_kind == "directory"
                else sha256_file(backup_path)
            )
            if expected_hash and actual_hash != expected_hash:
                issues.append({"backup_path": str(backup_path), "reason": "hash_mismatch"})
        except OSError as exc:
            issues.append({"backup_path": str(backup_path), "reason": f"backup_read_failed: {exc}"})
    return {
        "path": str(path),
        "ok": not issues and bool(items),
        "item_count": len(items),
        "issues": issues,
    }


def validate(root: str = "") -> dict[str, Any]:
    bases = [Path(root).expanduser()] if root else [
        WORK_GIT_BACKUP_ROOT,
        GIT_REPOSITORY_BACKUP_ROOT,
        UNIFIED_BACKUP_ROOT,
        LEGACY_WORK_GIT_BACKUP_ROOT,
    ]
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
        "schema": "backup_router.validate.v2",
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


def migrate_git_repository_backups(
    repository: str,
    *,
    source_dir_name: str = "_backup",
    apply: bool = False,
) -> dict[str, Any]:
    """Move a manifest-covered backup root out of an arbitrary Git worktree."""
    repository_root = Path(repository).expanduser().resolve()
    git_root = find_git_root(repository_root)
    allowed_source_names = {"_backup", "backups", "备份"}
    source_root = repository_root / source_dir_name
    destination_root = git_repository_backup_root(repository_root) / "migrated" / sanitize_slug(
        source_dir_name,
        default="backup",
    )
    result: dict[str, Any] = {
        "schema": "backup_router.migrate_git_repository.v1",
        "ok": True,
        "generated_at": now_iso(),
        "repository_root": str(repository_root),
        "source_root": str(source_root),
        "destination_root": str(destination_root),
        "source_dir_name": source_dir_name,
        "dry_run_contract": {"copies_files": apply, "rewrites_manifests": apply, "deletes_source_root": apply},
    }
    if git_root is None or git_root.resolve() != repository_root:
        return {**result, "ok": False, "reason": "repository_root_is_not_git_worktree"}
    if source_dir_name not in allowed_source_names:
        return {**result, "ok": False, "reason": "unsupported_source_directory"}
    if not source_root.is_dir():
        return {**result, "reason": "source_backup_root_missing"}
    source_files = sorted(path for path in source_root.rglob("*") if path.is_file())
    manifests = sorted(source_root.rglob("manifest.json"))
    result["source_file_count"] = len(source_files)
    result["manifest_count"] = len(manifests)
    if source_files and not manifests:
        return {**result, "ok": False, "reason": "git_local_backups_without_manifests"}

    manifest_validation = [validate_manifest(path) for path in manifests]
    invalid_manifests = [item for item in manifest_validation if not item.get("ok")]
    referenced_paths: set[Path] = set()
    for manifest_path in manifests:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in payload.get("items", []):
            if not isinstance(item, dict) or item.get("backup_mode") == "git_head_reference":
                continue
            backup_path = Path(str(item.get("backup_path") or ""))
            if backup_path.is_absolute() and is_relative_to(backup_path, source_root):
                referenced_paths.add(backup_path.resolve())
    payload_files = {path.resolve() for path in source_files if path.name != "manifest.json"}
    unreferenced = sorted(str(path) for path in payload_files - referenced_paths)
    result["unreferenced_file_count"] = len(unreferenced)
    result["unreferenced_files"] = unreferenced[:50]
    if invalid_manifests:
        return {**result, "ok": False, "reason": "source_manifest_validation_failed", "failures": invalid_manifests[:20]}
    if unreferenced:
        return {**result, "ok": False, "reason": "source_files_not_covered_by_manifests"}
    if find_git_root(destination_root) is not None:
        return {**result, "ok": False, "reason": "migration_destination_inside_git_worktree"}
    if destination_root.exists():
        return {**result, "ok": False, "reason": "destination_exists_refusing_merge"}
    if not apply:
        result["planned_file_count"] = len(source_files)
        return result

    destination_root.mkdir(parents=True, exist_ok=False)
    for source in source_files:
        target = destination_root / source.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256_file(source) != sha256_file(target):
            return {**result, "ok": False, "reason": "copy_hash_mismatch", "source": str(source)}

    for source_manifest in manifests:
        target_manifest = destination_root / source_manifest.relative_to(source_root)
        payload = json.loads(target_manifest.read_text(encoding="utf-8"))
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            for field in ("backup_path", "backup_dir", "backup_set_dir"):
                old_value = Path(str(item.get(field) or ""))
                if old_value.is_absolute() and is_relative_to(old_value, source_root):
                    item[field] = str(destination_root / old_value.relative_to(source_root))
            item["route"] = "git_repository_external_backup_root_migrated"
        payload["migrated_from"] = str(source_manifest)
        payload["migration_schema"] = "backup_router.git_repository_backup_migration.v1"
        target_manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    validation = validate(str(destination_root))
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
    p_migrate_git = sub.add_parser("migrate-git-repository")
    p_migrate_git.add_argument("--repo", required=True)
    p_migrate_git.add_argument("--source-dir", default="_backup", choices=("_backup", "backups", "备份"))
    p_migrate_git.add_argument("--apply", action="store_true")
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
    elif args.command == "migrate-work-git":
        payload = migrate_work_git_backups(apply=bool(args.apply))
    else:
        payload = migrate_git_repository_backups(
            args.repo,
            source_dir_name=args.source_dir,
            apply=bool(args.apply),
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
