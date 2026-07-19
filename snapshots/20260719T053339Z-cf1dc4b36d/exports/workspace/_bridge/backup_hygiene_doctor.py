#!/usr/bin/env python3
"""Read-only backup hygiene doctor for project-local .bak-* files.

This module observes scattered edit backups and proposes dry-run retention
actions. It never deletes, moves, compresses, or rewrites files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = ROOT / "_bridge"
CODEX_APP_HOME = Path(
    os.environ.get("CODEX_HOME") or os.environ.get("WSL_CODEX_HOME") or (Path.home() / ".codex-app")
).expanduser().resolve()
WORK_GIT_BACKUP_ROOT = CODEX_APP_HOME / "backups" / "work-git"
LEGACY_BRIDGE_BACKUP_ROOT = BRIDGE_ROOT / "backups"
ARCHIVE_ROOT = WORK_GIT_BACKUP_ROOT / "archive"
RESOURCE_LIBRARY_ROOT = Path(r"C:\Users\45543\Desktop\Codex资源库")
RESOURCE_DOC_ROOT = RESOURCE_LIBRARY_ROOT / "文档"
RESOURCE_LIBRARY_OFFICIAL_BACKUP_ROOT = RESOURCE_LIBRARY_ROOT / "_backup"
RESOURCE_LIBRARY_LEGACY_TOP_BACKUP_ROOT = RESOURCE_LIBRARY_ROOT / "backups"
PLANNED_BACKUP_ROOTS = (
    ROOT / "_backup",
    LEGACY_BRIDGE_BACKUP_ROOT,
    WORK_GIT_BACKUP_ROOT,
    BRIDGE_ROOT / "mobile_openclaw_bridge" / "backups",
    RESOURCE_LIBRARY_OFFICIAL_BACKUP_ROOT,
)

BACKUP_RE = re.compile(r"^(?P<base>.+)\.bak-(?P<stamp>\d{8}(?:-\d{4,6})?)(?:-(?P<label>.+))?$", re.IGNORECASE)


@dataclass(frozen=True)
class BackupPolicy:
    keep_recent_hours: int = 48
    keep_days: int = 7
    keep_per_directory: int = 3
    warn_count: int = 80
    warn_bytes: int = 128 * 1024 * 1024
    large_file_bytes: int = 512 * 1024


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_backup_name(path: Path) -> dict[str, Any]:
    match = BACKUP_RE.match(path.name)
    base_name = path.name
    stamp = ""
    label = ""
    if match:
        base_name = str(match.group("base") or path.name)
        stamp = str(match.group("stamp") or "")
        label = str(match.group("label") or "")
    original = path.with_name(base_name)
    return {
        "base_name": base_name,
        "stamp": stamp,
        "label": label,
        "original_path": str(original),
        "original_exists": original.exists(),
    }


def iter_backup_files(root: Path = BRIDGE_ROOT) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        [path for path in root.rglob("*.bak-*") if path.is_file() and ARCHIVE_ROOT not in path.parents],
        key=lambda path: (path.stat().st_mtime, str(path)),
        reverse=True,
    )


def is_under_any(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def manifest_paths() -> list[Path]:
    paths: list[Path] = []
    for root in PLANNED_BACKUP_ROOTS:
        if root.exists():
            paths.extend(path for path in root.rglob("manifest.json") if path.is_file())
    return sorted(set(paths), key=str)


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
        if not backup_path.exists():
            issues.append({"backup_path": str(backup_path), "reason": "backup_missing"})
    return {
        "path": str(path),
        "ok": bool(items) and not issues,
        "item_count": len(items),
        "issues": issues,
    }


def classify_backup(path: Path, policy: BackupPolicy, now: datetime) -> dict[str, Any]:
    stat = path.stat()
    age_hours = max(0.0, (now.timestamp() - stat.st_mtime) / 3600)
    parsed = parse_backup_name(path)
    original_path = str(parsed["original_path"])
    archive_rel = path.relative_to(BRIDGE_ROOT) if path.is_relative_to(BRIDGE_ROOT) else path.name
    archive_target = ARCHIVE_ROOT / datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y%m") / archive_rel
    in_planned_backup_root = is_under_any(path, PLANNED_BACKUP_ROOTS)
    if in_planned_backup_root:
        action = "keep_planned_backup_root"
    elif age_hours <= policy.keep_recent_hours:
        action = "keep_recent"
    elif age_hours <= policy.keep_days * 24:
        action = "keep_retention_window"
    else:
        action = "archive_candidate"
    return {
        "path": str(path),
        "directory": str(path.parent),
        "name": path.name,
        "size_bytes": int(stat.st_size),
        "last_write_time": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "age_hours": round(age_hours, 1),
        "original_path": original_path,
        "original_exists": bool(parsed["original_exists"]),
        "base_name": parsed["base_name"],
        "stamp": parsed["stamp"],
        "label": parsed["label"],
        "large": stat.st_size >= policy.large_file_bytes,
        "in_planned_backup_root": in_planned_backup_root,
        "action": action,
        "archive_target": str(archive_target),
    }


def backup_snapshot(policy: BackupPolicy | None = None) -> dict[str, Any]:
    policy = policy or BackupPolicy()
    now = now_utc()
    files = [classify_backup(path, policy, now) for path in iter_backup_files()]
    by_directory_files: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        by_directory_files[str(item["directory"])].append(item)
    for directory_items in by_directory_files.values():
        for index, item in enumerate(directory_items):
            if item.get("in_planned_backup_root"):
                continue
            if item.get("action") != "archive_candidate":
                continue
            if index >= int(policy.keep_per_directory):
                item["archive_reason"] = "per_directory_retention"
    by_dir: dict[str, dict[str, Any]] = {}
    by_original: dict[str, dict[str, Any]] = {}
    for item in files:
        directory = str(item["directory"])
        original = str(item["original_path"])
        dir_bucket = by_dir.setdefault(directory, {"directory": directory, "count": 0, "size_bytes": 0})
        dir_bucket["count"] += 1
        dir_bucket["size_bytes"] += int(item["size_bytes"])
        original_bucket = by_original.setdefault(original, {"original_path": original, "count": 0, "size_bytes": 0, "latest": ""})
        original_bucket["count"] += 1
        original_bucket["size_bytes"] += int(item["size_bytes"])
        if not original_bucket["latest"] or str(item["last_write_time"]) > str(original_bucket["latest"]):
            original_bucket["latest"] = str(item["last_write_time"])
    total_size = sum(int(item["size_bytes"]) for item in files)
    all_manifests = [validate_manifest(path) for path in manifest_paths()]
    manifests = [item for item in all_manifests if not item.get("skipped")]
    planned_backup_files = [item for item in files if item.get("in_planned_backup_root")]
    scattered_backup_files = [item for item in files if not item.get("in_planned_backup_root")]
    return {
        "schema": "backup_hygiene.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "root": str(ROOT),
        "scan_root": str(BRIDGE_ROOT),
        "archive_root": str(ARCHIVE_ROOT),
        "resource_library_backup_policy": {
            "official_backup_root": str(RESOURCE_LIBRARY_OFFICIAL_BACKUP_ROOT),
            "legacy_top_backup_root": str(RESOURCE_LIBRARY_LEGACY_TOP_BACKUP_ROOT),
            "legacy_top_backup_exists": RESOURCE_LIBRARY_LEGACY_TOP_BACKUP_ROOT.exists(),
            "rule": "The desktop Codex resource library uses only top-level _backup as the active backup root; top-level backups is legacy and should be migrated under _backup/legacy.",
        },
        "policy": {
            "keep_recent_hours": policy.keep_recent_hours,
            "keep_days": policy.keep_days,
            "keep_per_directory": policy.keep_per_directory,
            "keep_per_original": policy.keep_per_directory,
            "warn_count": policy.warn_count,
            "warn_bytes": policy.warn_bytes,
            "large_file_bytes": policy.large_file_bytes,
        },
        "summary": {
            "backup_count": len(files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 1),
            "directory_count": len(by_dir),
            "original_count": len(by_original),
            "archive_candidate_count": sum(1 for item in files if item["action"] == "archive_candidate"),
            "per_directory_candidate_count": sum(1 for item in files if item.get("archive_reason") == "per_directory_retention"),
            "large_count": sum(1 for item in files if item["large"]),
            "same_directory_count": len(scattered_backup_files),
            "scattered_backup_count": len(scattered_backup_files),
            "planned_backup_file_count": len(planned_backup_files),
            "manifest_count": len(manifests),
            "non_backup_manifest_count": sum(1 for item in all_manifests if item.get("skipped")),
            "manifest_failure_count": sum(1 for item in manifests if not item.get("ok")),
            "unmanifested_planned_backup_count": max(0, len(planned_backup_files) - sum(int(item.get("item_count") or 0) for item in manifests if item.get("ok"))),
        },
        "by_directory": sorted(by_dir.values(), key=lambda item: (-int(item["count"]), str(item["directory"]))),
        "by_original": sorted(by_original.values(), key=lambda item: (-int(item["count"]), str(item["original_path"])))[:80],
        "files": files,
        "manifests": manifests,
        "non_backup_manifests": [item for item in all_manifests if item.get("skipped")][:50],
    }


def doctor(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snapshot or backup_snapshot()
    summary = snap.get("summary") if isinstance(snap.get("summary"), dict) else {}
    policy = snap.get("policy") if isinstance(snap.get("policy"), dict) else {}
    issues: list[dict[str, Any]] = []
    count = int(summary.get("backup_count") or 0)
    total_bytes = int(summary.get("total_size_bytes") or 0)
    scattered_count = int(summary.get("scattered_backup_count") or 0)
    archive_candidate_count = int(summary.get("archive_candidate_count") or 0)
    manifest_failure_count = int(summary.get("manifest_failure_count") or 0)
    unmanifested_planned_count = int(summary.get("unmanifested_planned_backup_count") or 0)
    resource_policy = snap.get("resource_library_backup_policy") if isinstance(snap.get("resource_library_backup_policy"), dict) else {}
    if resource_policy.get("legacy_top_backup_exists"):
        issues.append(
            {
                "severity": "advisory",
                "code": "resource_library_legacy_top_backup_root",
                "message": "Desktop Codex resource library has a top-level backups directory; active backups should use only _backup.",
                "path": resource_policy.get("legacy_top_backup_root"),
                "official_backup_root": resource_policy.get("official_backup_root"),
                "manual_action": "Move the legacy top-level backups directory under _backup/legacy with a migration manifest; do not create new active backups there.",
            }
        )
    if scattered_count >= int(policy.get("warn_count") or 80) and archive_candidate_count:
        issues.append(
            {
                "severity": "risk",
                "code": "backup_file_fanout",
                "message": f"{scattered_count} backup files are outside planned backup roots and {archive_candidate_count} are eligible for archive.",
                "count": scattered_count,
                "manual_action": "Review backup-hygiene repair-plan; route new backups through backup_router and archive legacy scattered backups.",
            }
        )
    elif scattered_count >= int(policy.get("warn_count") or 80):
        issues.append(
            {
                "severity": "advisory",
                "code": "backup_recent_fanout",
                "message": f"{scattered_count} backup files are outside planned backup roots, but none are currently eligible for archive under the retention policy.",
                "count": scattered_count,
                "manual_action": "Keep new backups routed through backup_router and re-run backup-hygiene after the retention window.",
            }
        )
    if total_bytes >= int(policy.get("warn_bytes") or 0):
        issues.append(
            {
                "severity": "risk",
                "code": "backup_storage_growth",
                "message": f"Backup files use {round(total_bytes / 1024 / 1024, 1)} MB under _bridge.",
                "size_bytes": total_bytes,
                "manual_action": "Archive old backups out of source/search paths, then consider compression after approval.",
            }
        )
    if int(summary.get("same_directory_count") or 0) >= 20:
        issues.append(
            {
                "severity": "advisory",
                "code": "backup_search_pollution",
                "message": "Many backups are stored beside active source files instead of planned backup roots.",
                "same_directory_count": summary.get("same_directory_count"),
                "manual_action": "Use backup_router for new backups; move legacy scattered backups only through a reviewed repair plan.",
            }
        )
    if manifest_failure_count:
        issues.append(
            {
                "severity": "risk",
                "code": "backup_manifest_invalid",
                "message": f"{manifest_failure_count} backup manifest(s) are invalid or reference missing files.",
                "manual_action": "Inspect manifest failures before trusting rollback points.",
            }
        )
    if unmanifested_planned_count >= 20:
        issues.append(
            {
                "severity": "advisory",
                "code": "backup_manifest_missing",
                "message": f"{unmanifested_planned_count} planned backup file(s) are not covered by valid manifests.",
                "manual_action": "Use backup_router create for future backups; migrate old planned backups with manifest generation only after review.",
            }
        )
    return {
        "schema": "backup_hygiene.doctor.v1",
        "ok": not any(item.get("severity") in {"blocker", "risk"} for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": summary,
        "snapshot": {
            "root": snap.get("root"),
            "scan_root": snap.get("scan_root"),
            "archive_root": snap.get("archive_root"),
            "policy": policy,
            "resource_library_backup_policy": resource_policy,
            "summary": summary,
            "by_directory": (snap.get("by_directory") or [])[:20],
            "by_original": (snap.get("by_original") or [])[:20],
            "manifests": (snap.get("manifests") or [])[:20],
            "non_backup_manifest_count": len(snap.get("non_backup_manifests") or []),
        },
    }


def repair_plan(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snapshot or backup_snapshot()
    files = snap.get("files") if isinstance(snap.get("files"), list) else []
    archive_candidates = [
        item for item in files
        if item.get("action") == "archive_candidate" and not item.get("in_planned_backup_root")
    ]
    actions: list[dict[str, Any]] = []
    if archive_candidates:
        actions.append(
            {
                "code": "archive_old_backups",
                "dry_run_only": True,
                "would_mutate": "move old *.bak-* files from source directories into the external Work Git backup archive preserving relative paths",
                "candidate_count": len(archive_candidates),
                "candidate_size_mb": round(sum(int(item.get("size_bytes") or 0) for item in archive_candidates) / 1024 / 1024, 1),
                "preview": [
                    {
                        "from": item.get("path"),
                        "to": item.get("archive_target"),
                        "age_hours": item.get("age_hours"),
                    }
                    for item in archive_candidates[:30]
                ],
                "guardrails": [
                    "do not delete in this tool",
                    "do not move backups younger than retention window",
                    "preserve relative path and filename",
                    "only move source files discovered under _bridge",
                ],
                "validation": "rerun backup-hygiene doctor, then run representative rg searches with *.bak-* excluded",
            }
        )
    apply_supported = bool(archive_candidates)
    return {
        "schema": "backup_hygiene.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "apply_supported": apply_supported,
        "dry_run_contract": {
            "moves_files": apply_supported,
            "deletes_files": False,
            "compresses_files": False,
            "rewrites_source": False,
        },
        "summary": snap.get("summary"),
        "actions": actions,
        "next_step": "Use backup_router for new backups. After reviewing this dry-run, apply only archive/move for legacy scattered backups, not deletion.",
    }


def apply(snapshot: dict[str, Any] | None = None, *, confirm: str = "") -> dict[str, Any]:
    snap = snapshot or backup_snapshot()
    plan = repair_plan(snap)
    if confirm != "archive-old-backups":
        return {
            "schema": "backup_hygiene.apply.v1",
            "ok": False,
            "generated_at": now_iso(),
            "reason": "confirmation required",
            "required_confirm": "archive-old-backups",
            "dry_run_only": True,
            "plan": plan,
        }
    files = snap.get("files") if isinstance(snap.get("files"), list) else []
    archive_candidates = [item for item in files if item.get("action") == "archive_candidate"]
    moved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in archive_candidates:
        src = Path(str(item.get("path") or ""))
        dst = Path(str(item.get("archive_target") or ""))
        if ARCHIVE_ROOT in src.parents:
            skipped.append({"path": str(src), "reason": "already archived"})
            continue
        if not src.exists():
            skipped.append({"path": str(src), "reason": "missing"})
            continue
        if dst.exists():
            skipped.append({"path": str(src), "reason": "archive target exists"})
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append({"from": str(src), "to": str(dst)})
    return {
        "schema": "backup_hygiene.apply.v1",
        "ok": True,
        "generated_at": now_iso(),
        "confirmed": confirm,
        "moved_count": len(moved),
        "skipped_count": len(skipped),
        "moved": moved[:100],
        "skipped": skipped[:100],
        "note": "apply only archives old backups; it does not delete or compress files.",
    }


def metrics(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snapshot or backup_snapshot()
    summary = snap.get("summary") if isinstance(snap.get("summary"), dict) else {}
    return {
        "schema": "backup_hygiene.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "backup_count": int(summary.get("backup_count") or 0),
        "total_size_bytes": int(summary.get("total_size_bytes") or 0),
        "total_size_mb": float(summary.get("total_size_mb") or 0),
        "directory_count": int(summary.get("directory_count") or 0),
        "original_count": int(summary.get("original_count") or 0),
        "archive_candidate_count": int(summary.get("archive_candidate_count") or 0),
        "per_directory_candidate_count": int(summary.get("per_directory_candidate_count") or 0),
        "large_count": int(summary.get("large_count") or 0),
        "same_directory_count": int(summary.get("same_directory_count") or 0),
        "scattered_backup_count": int(summary.get("scattered_backup_count") or 0),
        "planned_backup_file_count": int(summary.get("planned_backup_file_count") or 0),
        "manifest_count": int(summary.get("manifest_count") or 0),
        "non_backup_manifest_count": int(summary.get("non_backup_manifest_count") or 0),
        "manifest_failure_count": int(summary.get("manifest_failure_count") or 0),
        "unmanifested_planned_backup_count": int(summary.get("unmanifested_planned_backup_count") or 0),
    }


def validate(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snapshot or backup_snapshot()
    plan = repair_plan(snap)
    summary = snap.get("summary") if isinstance(snap.get("summary"), dict) else {}
    manifest_failure_count = int(summary.get("manifest_failure_count") or 0)
    return {
        "schema": "backup_hygiene.validate.v1",
        "ok": bool(snap.get("ok"))
        and bool(plan.get("dry_run_contract", {}).get("deletes_files") is False)
        and manifest_failure_count == 0,
        "generated_at": now_iso(),
        "backup_count": summary.get("backup_count"),
        "manifest_count": int(summary.get("manifest_count") or 0),
        "manifest_failure_count": manifest_failure_count,
        "dry_run_only": True,
        "note": "Validation requires readable backup manifests and a non-destructive repair plan.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup hygiene doctor")
    parser.add_argument("command", choices=["snapshot", "doctor", "repair-plan", "apply", "metrics", "validate"])
    parser.add_argument(
        "--confirm",
        default="",
        help="Required confirmation token for controlled repair commands.",
    )
    args = parser.parse_args()
    snap = backup_snapshot()
    if args.command == "snapshot":
        payload = snap
    elif args.command == "doctor":
        payload = doctor(snap)
    elif args.command == "repair-plan":
        payload = repair_plan(snap)
    elif args.command == "apply":
        payload = apply(snap, confirm=args.confirm)
    elif args.command == "metrics":
        payload = metrics(snap)
    else:
        payload = validate(snap)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
