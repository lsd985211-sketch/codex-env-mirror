#!/usr/bin/env python3
"""Exact successor changeset replay mechanics for the Work Git owner.

Ownership: apply one already-assessed predecessor changeset to one clean,
current-HEAD task worktree and verify exact declared-path index readback.
Non-goals: assess successor eligibility, authorize work, commit, sync,
integrate, rebase, reset, force-push, publish, release, or own receipts.
State behavior: the only mutation is one atomic ``git apply --index`` after
all source, branch, path, patch, and HEAD preconditions pass.
Caller context: work_git_change_owner invokes this peer only after its
successor_plan readback and R2 lifecycle replay gate both succeed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


SCHEMA = "work_git_change_owner.successor_replay.v1"


def _run(root: Path, *args: str, input_data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_data,
        capture_output=True,
        check=False,
    )


def _text(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stdout.decode("utf-8", errors="surrogateescape").strip()


def _failure(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "ok": False, "status": "blocked", "reason": reason, **details}


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _changed_paths(root: Path, start: str, end: str) -> list[str]:
    result = _run(root, "diff", "--name-only", "-z", f"{start}..{end}")
    if result.returncode != 0:
        return []
    return sorted(
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    )


def _tree_entries(root: Path, treeish: str, paths: list[str]) -> dict[str, dict[str, str]]:
    result = _run(root, "ls-tree", "-z", treeish, "--", *paths)
    entries: dict[str, dict[str, str]] = {}
    if result.returncode != 0:
        return entries
    for raw in result.stdout.split(b"\0"):
        if not raw or b"\t" not in raw:
            continue
        metadata, raw_path = raw.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8", errors="surrogateescape")
        entries[path] = {"mode": mode, "type": object_type, "object_id": object_id}
    return entries


def _index_entries(root: Path, paths: list[str]) -> dict[str, dict[str, str]]:
    result = _run(root, "ls-files", "-s", "-z", "--", *paths)
    entries: dict[str, dict[str, str]] = {}
    if result.returncode != 0:
        return entries
    for raw in result.stdout.split(b"\0"):
        if not raw or b"\t" not in raw:
            continue
        metadata, raw_path = raw.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        if stage != "0":
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        entries[path] = {"mode": mode, "type": "blob", "object_id": object_id}
    return entries


def replay_changeset(
    *,
    task_root: Path | str,
    repository_root: Path | str,
    expected_branch: str,
    expected_base_head: str,
    predecessor_commit: str,
    old_base_head: str,
    declared_paths: list[str],
    expected_changeset_digest: str,
    successor_signature: str,
) -> dict[str, Any]:
    """Apply and verify one exact predecessor changeset, failing closed on drift."""

    task = Path(task_root).expanduser().resolve()
    repository = Path(repository_root).expanduser().resolve()
    declared = sorted({str(path).strip().replace("\\", "/") for path in declared_paths if str(path).strip()})
    if not declared:
        return _failure("successor_replay_declared_paths_required")

    task_common = _text(_run(task, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    repository_common = _text(_run(repository, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if not task_common or task_common != repository_common:
        return _failure("successor_replay_repository_identity_changed")
    actual_branch = _text(_run(task, "branch", "--show-current"))
    if actual_branch != expected_branch:
        return _failure("successor_replay_branch_changed", expected=expected_branch, actual=actual_branch)
    actual_head = _text(_run(task, "rev-parse", "HEAD"))
    if actual_head != expected_base_head:
        return _failure("successor_replay_base_head_changed", expected=expected_base_head, actual=actual_head)
    status = _run(task, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status.returncode != 0 or status.stdout:
        return _failure("successor_replay_task_worktree_not_clean")

    actual_paths = _changed_paths(repository, old_base_head, predecessor_commit)
    if actual_paths != declared:
        return _failure(
            "successor_replay_declared_changeset_changed",
            declared_paths=declared,
            actual_paths=actual_paths,
        )
    patch_result = _run(repository, "diff", "--binary", f"{old_base_head}..{predecessor_commit}")
    if patch_result.returncode != 0 or not patch_result.stdout:
        return _failure("successor_replay_patch_unverifiable")
    changeset_digest = hashlib.sha256(patch_result.stdout).hexdigest()
    if changeset_digest != expected_changeset_digest:
        return _failure(
            "successor_replay_changeset_digest_changed",
            expected=expected_changeset_digest,
            actual=changeset_digest,
        )
    applicable = _run(task, "apply", "--check", "--index", "--whitespace=nowarn", input_data=patch_result.stdout)
    if applicable.returncode != 0:
        return _failure(
            "successor_replay_patch_not_applicable",
            stderr=applicable.stderr.decode("utf-8", errors="replace")[-1200:],
        )

    applied = _run(task, "apply", "--index", "--whitespace=nowarn", input_data=patch_result.stdout)
    if applied.returncode != 0:
        return {
            **_failure(
                "successor_replay_apply_failed",
                stderr=applied.stderr.decode("utf-8", errors="replace")[-1200:],
            ),
            "status": "effect_unknown",
        }

    staged = sorted(
        item.decode("utf-8", errors="surrogateescape")
        for item in _run(task, "diff", "--cached", "--name-only", "-z").stdout.split(b"\0")
        if item
    )
    expected_entries = _tree_entries(repository, predecessor_commit, declared)
    actual_entries = _index_entries(task, declared)
    expected_projection = {path: expected_entries.get(path) for path in declared}
    actual_projection = {path: actual_entries.get(path) for path in declared}
    if staged != declared or actual_projection != expected_projection:
        return {
            **_failure(
                "successor_replay_readback_mismatch",
                staged_paths=staged,
                declared_paths=declared,
                expected_path_digest=_canonical_digest(expected_projection),
                actual_path_digest=_canonical_digest(actual_projection),
            ),
            "status": "effect_unknown",
        }
    return {
        "schema": SCHEMA,
        "ok": True,
        "status": "completed",
        "task_root": str(task),
        "branch": actual_branch,
        "base_head": actual_head,
        "predecessor_commit": predecessor_commit,
        "old_base_head": old_base_head,
        "declared_paths": declared,
        "staged_paths": staged,
        "changeset_digest": changeset_digest,
        "declared_path_digest": _canonical_digest(actual_projection),
        "successor_signature": successor_signature,
        "readback_ok": True,
    }
