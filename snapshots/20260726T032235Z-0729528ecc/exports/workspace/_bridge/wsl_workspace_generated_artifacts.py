#!/usr/bin/env python3
"""Govern fixed, disposable artifacts created inside the Work Git root.

Ownership: classify, plan, and remove a narrow allowlist of editor/test caches.
Non-goals: broad cache discovery, user-file cleanup, or Git history changes.
State behavior: plans are read-only; apply requires an exact confirmation and
revalidates path type, symlink, root, and ignore-contract boundaries.
Caller context: exposed through wsl_workspace_owner cleanup-plan/cleanup-apply.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


SCHEMA = "wsl_workspace_generated_artifacts.v1"
CLEANUP_CONFIRM = "PRUNE-WORK-GIT-GENERATED-ARTIFACTS"
ARTIFACTS = (
    {
        "relative_path": ".vs",
        "reason": "visual_studio_workspace_cache",
        "ignore_pattern": "**/.vs/",
        "expected_type": "directory",
    },
    {
        "relative_path": ".ruff_cache",
        "reason": "ruff_analysis_cache",
        "ignore_pattern": "**/.ruff_cache/",
        "expected_type": "directory",
    },
    {
        "relative_path": ".pytest_cache",
        "reason": "pytest_execution_cache",
        "ignore_pattern": "**/.pytest_cache/",
        "expected_type": "directory",
    },
    {
        "relative_path": ".mypy_cache",
        "reason": "mypy_analysis_cache",
        "ignore_pattern": "**/.mypy_cache/",
        "expected_type": "directory",
    },
)


def _tree_size(path: Path) -> tuple[int, int]:
    if path.is_file():
        return path.stat().st_size, 1
    total = 0
    files = 0
    for child in path.rglob("*"):
        if child.is_file() and not child.is_symlink():
            total += child.stat().st_size
            files += 1
    return total, files


def _ignore_patterns(root: Path) -> set[str]:
    path = root / ".gitignore"
    if not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def cleanup_plan(root: Path) -> dict[str, Any]:
    root = root.resolve()
    blockers: list[dict[str, Any]] = []
    if not (root / ".git").exists():
        blockers.append({"code": "work_git_identity_missing", "path": str(root)})

    declared_ignores = _ignore_patterns(root)
    missing_ignores = sorted(
        str(spec["ignore_pattern"])
        for spec in ARTIFACTS
        if spec["ignore_pattern"] not in declared_ignores
    )
    if missing_ignores:
        blockers.append({
            "code": "generated_artifact_ignore_contract_missing",
            "patterns": missing_ignores,
        })

    candidates: list[dict[str, Any]] = []
    for spec in ARTIFACTS:
        path = root / str(spec["relative_path"])
        exists = path.exists() or path.is_symlink()
        is_symlink = path.is_symlink()
        expected_directory = spec["expected_type"] == "directory"
        type_ok = not exists or (path.is_dir() if expected_directory else path.is_file())
        if exists and not type_ok:
            blockers.append({
                "code": "generated_artifact_type_mismatch",
                "path": str(path),
                "expected_type": spec["expected_type"],
            })
        size_bytes, file_count = _tree_size(path) if exists and not is_symlink and type_ok else (0, 0)
        candidates.append({
            **spec,
            "path": str(path),
            "exists": exists,
            "symlink": is_symlink,
            "type_ok": type_ok,
            "eligible": bool(exists and type_ok and not is_symlink),
            "size_bytes": size_bytes,
            "file_count": file_count,
        })

    eligible = [row for row in candidates if row["eligible"]]
    return {
        "schema": f"{SCHEMA}.cleanup_plan",
        "ok": not blockers,
        "eligible": not blockers,
        "read_only": True,
        "root": str(root),
        "candidates": candidates,
        "candidate_count": len(eligible),
        "reclaimable_bytes": sum(int(row["size_bytes"]) for row in eligible),
        "blockers": blockers,
        "apply_contract": {
            "confirmation": CLEANUP_CONFIRM,
            "fixed_root_relative_allowlist_only": True,
            "symlinks_forbidden": True,
            "backup_policy": "no copy for explicitly disposable generated caches",
            "gitignore_required_before_cleanup": True,
        },
    }


def cleanup_apply(root: Path, confirm: str) -> dict[str, Any]:
    plan = cleanup_plan(root)
    if confirm != CLEANUP_CONFIRM:
        return {
            "schema": f"{SCHEMA}.cleanup_apply",
            "ok": False,
            "applied": False,
            "reason": f"pass --confirm {CLEANUP_CONFIRM}",
            "plan": plan,
        }
    if not plan.get("eligible"):
        return {
            "schema": f"{SCHEMA}.cleanup_apply",
            "ok": False,
            "applied": False,
            "reason": "cleanup_plan_blocked",
            "plan": plan,
        }

    root = Path(plan["root"])
    deleted: list[dict[str, Any]] = []
    for row in plan["candidates"]:
        if not row.get("eligible"):
            continue
        path = Path(row["path"])
        if path.parent != root or path.is_symlink():
            return {
                "schema": f"{SCHEMA}.cleanup_apply",
                "ok": False,
                "applied": bool(deleted),
                "reason": "cleanup_candidate_boundary_changed",
                "candidate": row,
                "deleted": deleted,
            }
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        deleted.append(row)

    after = cleanup_plan(root)
    return {
        "schema": f"{SCHEMA}.cleanup_apply",
        "ok": bool(after.get("ok") and after.get("candidate_count") == 0),
        "applied": bool(deleted),
        "deleted": deleted,
        "deleted_count": len(deleted),
        "reclaimed_bytes": sum(int(row["size_bytes"]) for row in deleted),
        "after": after,
    }
