#!/usr/bin/env python3
"""Governed task worktrees and path-scoped commits for WSL Work Git.

Owns: task isolation, explicit change-set commits, local bare-Git synchronization,
fast-forward integration, safe repository configuration, and receipts.
Non-goals: source-file editing, validation policy, backups, mirror publication,
GitHub publication, conflict auto-resolution, destructive reset, or branch cleanup.
State: read-only except exact-confirmation start/commit/sync/integrate/config actions.
Callers: Codex tasks working in the long-lived WSL Work Git environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.backup_router import create_backup


BRIDGE_ROOT = Path(__file__).resolve().parent
DEFAULT_WORKTREE = BRIDGE_ROOT.parents[1]
DEFAULT_TASK_ROOT = Path(
    os.environ.get("CODEX_WORK_GIT_TASK_ROOT", "~/.codex-app/worktrees/codex-workspace")
).expanduser()
DEFAULT_RECEIPT_ROOT = Path(
    os.environ.get("CODEX_WORK_GIT_RECEIPT_ROOT", "~/.codex-app/runtime/work-git-change-owner")
).expanduser()
START_CONFIRM = "START-WORK-GIT-TASK"
COMMIT_CONFIRM = "COMMIT-WORK-GIT-CHANGESET"
SYNC_CONFIRM = "SYNC-WORK-GIT-BRANCH"
INTEGRATE_CONFIRM = "INTEGRATE-WORK-GIT-TASK"
REPLAY_CONFIRM = "REPLAY-WORK-GIT-SUCCESSOR"
CONFIG_CONFIRM = "APPLY-WORK-GIT-SAFE-CONFIG"
MAINTENANCE_CONFIRM = "RUN-WORK-GIT-MAINTENANCE"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
MAINTENANCE_TASKS = ("commit-graph", "loose-objects", "incremental-repack")

WORKTREE_CONFIG = {
    "fetch.prune": "true",
    "pull.ff": "only",
    "push.default": "simple",
    "merge.conflictStyle": "zdiff3",
    "rerere.enabled": "true",
    "rerere.autoupdate": "false",
    "core.untrackedCache": "true",
    "gc.writeCommitGraph": "true",
    "maintenance.commit-graph.enabled": "true",
    "maintenance.loose-objects.enabled": "true",
    "maintenance.incremental-repack.enabled": "true",
}
BARE_CONFIG = {
    "receive.denyNonFastForwards": "true",
    "receive.denyDeletes": "true",
    "core.logAllRefUpdates": "true",
    "gc.writeCommitGraph": "true",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 60,
    text: bool = True,
    input_data: str | bytes | None = None,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=text,
            input=input_data,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "" if text else b"",
            "stderr": f"{type(exc).__name__}: {exc}" if text else str(exc).encode(),
        }
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _git(root: Path, *args: str, timeout: int = 60, text: bool = True) -> dict[str, Any]:
    return _run(["git", "-C", str(root), *args], timeout=timeout, text=text)


def _stdout(result: dict[str, Any]) -> str:
    return str(result.get("stdout") or "").strip()


def repository_root(path: Path | str) -> Path | None:
    result = _git(Path(path).expanduser(), "rev-parse", "--show-toplevel")
    return Path(_stdout(result)).resolve() if result.get("ok") and _stdout(result) else None


def _status_entries(root: Path, *, exact_untracked: bool = True) -> list[dict[str, Any]]:
    untracked_mode = "all" if exact_untracked else "normal"
    result = _git(root, "status", "--porcelain=v1", "-z", f"--untracked-files={untracked_mode}", text=False)
    if not result.get("ok"):
        return []
    raw = bytes(result.get("stdout") or b"")
    records = raw.split(b"\0")
    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        decoded = record.decode("utf-8", errors="surrogateescape")
        if len(decoded) < 3:
            continue
        status = decoded[:2]
        path = decoded[3:]
        original_path = ""
        if status[0] in {"R", "C"} and index < len(records):
            original_path = records[index].decode("utf-8", errors="surrogateescape")
            index += 1
        rows.append(
            {
                "path": path,
                "original_path": original_path,
                "status": status,
                "tracked": status != "??",
                "staged": status[0] not in {" ", "?", "!"},
                "unstaged": status[1] not in {" ", "?", "!"} or status == "??",
            }
        )
    return rows


def _equivalent_dirty_overlap(
    main_root: Path, branch: str, rows: list[dict[str, Any]], overlap: list[str],
) -> tuple[list[str], list[str]]:
    """Split dirty overlaps by exact working-tree equivalence to the branch.

    This is deliberately narrower than a merge: only an unstaged, tracked
    ordinary-path modification whose bytes already equal the branch blob can be
    carried through a fast-forward.  Renames, copies, untracked paths, staged
    content, absent branch blobs, and every byte difference remain blockers.
    """

    by_path = {str(row.get("path") or ""): row for row in rows}
    equivalent: list[str] = []
    conflicting: list[str] = []
    for path in overlap:
        row = by_path.get(path, {})
        if (
            not row.get("tracked")
            or row.get("staged")
            or row.get("original_path")
            or not row.get("unstaged")
        ):
            conflicting.append(path)
            continue
        working_path = main_root / path
        expected = _git(main_root, "show", f"{branch}:{path}", text=False)
        try:
            actual_bytes = working_path.read_bytes()
        except OSError:
            conflicting.append(path)
            continue
        if expected.get("ok") and actual_bytes == bytes(expected.get("stdout") or b""):
            equivalent.append(path)
        else:
            conflicting.append(path)
    return sorted(equivalent), sorted(conflicting)


def _worktree_rows(root: Path) -> list[dict[str, str]]:
    result = _git(root, "worktree", "list", "--porcelain")
    if not result.get("ok"):
        return []
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in str(result.get("stdout") or "").splitlines() + [""]:
        if not line:
            if current:
                rows.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    return rows


def _config_value(root: Path, key: str) -> str:
    result = _git(root, "config", "--local", "--get", key)
    return _stdout(result) if result.get("ok") else ""


def _origin_path(root: Path) -> Path | None:
    value = _stdout(_git(root, "remote", "get-url", "origin"))
    if not value or "://" in value or value.startswith("git@"):
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def _repository_id(root: Path) -> str:
    common_dir = _stdout(_git(root, "rev-parse", "--git-common-dir"))
    if not common_dir:
        return ""
    path = Path(common_dir)
    if not path.is_absolute():
        path = root / path
    return str(path.resolve())


def _is_bare_repository(path: Path | None) -> bool:
    if path is None or not path.is_dir():
        return False
    result = _git(path, "rev-parse", "--is-bare-repository")
    return bool(result.get("ok") and _stdout(result) == "true")


def snapshot(root: Path | str = DEFAULT_WORKTREE) -> dict[str, Any]:
    repo = repository_root(root)
    if repo is None:
        return {
            "schema": "work_git_change_owner.snapshot.v1",
            "ok": False,
            "reason": "git_worktree_required",
            "root": str(Path(root).expanduser()),
        }
    changes = _status_entries(repo, exact_untracked=False)
    branch = _stdout(_git(repo, "branch", "--show-current"))
    head = _stdout(_git(repo, "rev-parse", "HEAD"))
    origin = _origin_path(repo)
    origin_head = _stdout(_git(repo, "rev-parse", "refs/remotes/origin/main"))
    staged = [row["path"] for row in changes if row["staged"]]
    return {
        "schema": "work_git_change_owner.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "root": str(repo),
        "branch": branch,
        "head": head,
        "origin": str(origin or ""),
        "origin_is_local_bare": _is_bare_repository(origin),
        "origin_main_head": origin_head,
        "head_matches_origin_main": bool(head and head == origin_head),
        "clean": not changes,
        "change_count": len(changes),
        "staged_count": len(staged),
        "staged_paths": staged[:50],
        "change_sample": changes[:50],
        "worktrees": _worktree_rows(repo),
        "task_isolation_recommended": bool(branch == "main" and changes),
        "scope_rule": "main is the integration worktree; dirty or parallel tasks should start in an isolated task worktree",
    }


def _task_identity(task_id: str) -> tuple[str, str] | None:
    value = str(task_id or "").strip()
    if not TASK_ID_RE.fullmatch(value):
        return None
    slug = value.lower()
    return slug, f"codex/task/{slug}"


def start_plan(
    task_id: str,
    *,
    root: Path | str = DEFAULT_WORKTREE,
    task_root: Path | str = DEFAULT_TASK_ROOT,
) -> dict[str, Any]:
    state = snapshot(root)
    identity = _task_identity(task_id)
    blockers: list[dict[str, Any]] = []
    if not state.get("ok"):
        blockers.append({"code": "git_worktree_required", "root": state.get("root")})
    if identity is None:
        blockers.append({"code": "task_id_invalid", "rule": TASK_ID_RE.pattern})
    slug, branch = identity or ("invalid", "")
    destination = Path(task_root).expanduser().resolve() / slug
    source = Path(str(state.get("root") or Path(root).expanduser())).resolve()
    try:
        destination.relative_to(source)
        blockers.append({"code": "task_worktree_inside_source_refused", "path": str(destination)})
    except ValueError:
        pass
    worktrees = state.get("worktrees") if isinstance(state.get("worktrees"), list) else []
    existing = next((row for row in worktrees if row.get("branch") == f"refs/heads/{branch}"), None)
    branch_exists = bool(_git(source, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").get("ok"))
    destination_exists = destination.exists()
    if destination_exists and not existing:
        blockers.append({"code": "foreign_task_destination_exists", "path": str(destination)})
    return {
        "schema": "work_git_change_owner.start_plan.v1",
        "ok": not blockers,
        "generated_at": now_iso(),
        "task_id": task_id,
        "slug": slug,
        "branch": branch,
        "source_root": str(source),
        "destination": str(destination),
        "base_commit": str(state.get("head") or ""),
        "main_change_count": int(state.get("change_count") or 0),
        "isolates_existing_main_changes": bool(state.get("change_count")),
        "branch_exists": branch_exists,
        "existing_worktree": existing or {},
        "already_started": bool(existing and Path(str(existing.get("worktree") or "")).resolve() == destination),
        "blockers": blockers,
        "confirmation": START_CONFIRM,
        "writes_source_files": False,
        "imports_runtime_state": False,
    }


def _write_receipt(receipt_root: Path | str, name: str, payload: dict[str, Any]) -> str:
    root = Path(receipt_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.json"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return str(path)


def _prepare_receipt_name(task_id: str, operation_id: str) -> str:
    safe_task = re.sub(r"[^A-Za-z0-9._-]+", "-", str(task_id or "").strip()).strip(".-") or "unknown"
    operation_digest = hashlib.sha256(str(operation_id or "").encode("utf-8")).hexdigest()[:16]
    return f"r2-prepare-{safe_task}-{operation_digest}"


def prepare_terminal_projection(result: dict[str, Any], *, task_id: str, operation_id: str) -> dict[str, Any]:
    """Return the bounded, non-secret result contract for one R2 prepare."""

    ok = bool(result.get("ok"))
    reason = str(result.get("reason") or "")
    return {
        "schema": "work_git_change_owner.r2_prepare_result.v1",
        "ok": ok,
        "status": "prepared" if ok else "rejected",
        "reason": reason,
        "task_id": str(task_id or ""),
        "operation_id": str(operation_id or result.get("operation_id") or ""),
        "intent_ref": str(result.get("intent_ref") or ""),
        "scope_signature": str(result.get("scope_signature") or ""),
        "target_fingerprint": str(result.get("target_fingerprint") or result.get("lifecycle_signature") or ""),
        "relevant_input_signature": str(result.get("relevant_input_signature") or ""),
        "reused": bool(result.get("reused")),
        "receipt_ref": str(result.get("prepare_receipt") or result.get("rejection_receipt") or ""),
        "next_action": (
            "run r2-start through the owner with the same operation_id"
            if ok else "inspect receipt_ref, correct only the reported binding, then rerun with the same operation_id"
        ),
    }


def _record_prepare_result(
    result: dict[str, Any], *, task_id: str, operation_id: str, receipt_root: Path | str,
) -> dict[str, Any]:
    name = _prepare_receipt_name(task_id, operation_id)
    path = str(Path(receipt_root).expanduser().resolve() / f"{name}.json")
    projection = prepare_terminal_projection(result, task_id=task_id, operation_id=operation_id)
    projection["receipt_ref"] = path
    _write_receipt(receipt_root, name, projection)
    recorded = {**result, "prepare_receipt": path}
    if not projection["ok"]:
        recorded["rejection_receipt"] = path
    return recorded


def prepare_result(
    operation_id: str, *, task_id: str = "", receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
) -> dict[str, Any]:
    """Resolve one persisted R2 prepare result without caller-side directory scans."""

    root = Path(receipt_root).expanduser().resolve()
    matches: list[dict[str, Any]] = []
    for path in sorted(root.glob("r2-prepare-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            payload.get("schema") == "work_git_change_owner.r2_prepare_result.v1"
            and str(payload.get("operation_id") or "") == str(operation_id or "")
            and (not task_id or str(payload.get("task_id") or "") == str(task_id))
        ):
            matches.append(payload)
    if not matches:
        return {"schema": "work_git_change_owner.r2_prepare_result.v1", "ok": False, "status": "not_found", "reason": "work_git_r2_prepare_result_not_found", "operation_id": str(operation_id or ""), "task_id": str(task_id or ""), "next_action": "run r2-prepare or verify the exact operation_id"}
    if len(matches) != 1:
        return {"schema": "work_git_change_owner.r2_prepare_result.v1", "ok": False, "status": "ambiguous", "reason": "work_git_r2_prepare_result_ambiguous", "operation_id": str(operation_id or ""), "task_id": str(task_id or ""), "next_action": "supply task_id with operation_id"}
    return matches[0]


def start_task(
    task_id: str,
    *,
    confirm: str,
    root: Path | str = DEFAULT_WORKTREE,
    task_root: Path | str = DEFAULT_TASK_ROOT,
    receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
) -> dict[str, Any]:
    plan = start_plan(task_id, root=root, task_root=task_root)
    if confirm != START_CONFIRM:
        return {"schema": "work_git_change_owner.start.v1", "ok": False, "status": "blocked", "reason": f"pass --confirm {START_CONFIRM}", "plan": plan}
    if not plan.get("ok"):
        return {"schema": "work_git_change_owner.start.v1", "ok": False, "status": "blocked", "reason": "start_plan_blocked", "plan": plan}
    if plan.get("already_started"):
        return {"schema": "work_git_change_owner.start.v1", "ok": True, "status": "already_started", "plan": plan}
    destination = Path(plan["destination"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = ["worktree", "add"]
    if not plan.get("branch_exists"):
        command.extend(["-b", str(plan["branch"])])
    command.extend([str(destination), str(plan["branch"] if plan.get("branch_exists") else plan["base_commit"])])
    operation = _git(Path(plan["source_root"]), *command, timeout=120)
    after = snapshot(destination) if operation.get("ok") else {}
    result = {
        "schema": "work_git_change_owner.start.v1",
        "ok": bool(operation.get("ok") and after.get("ok") and after.get("branch") == plan["branch"]),
        "status": "completed" if operation.get("ok") else "failed",
        "generated_at": now_iso(),
        "plan": plan,
        "operation": {"returncode": operation.get("returncode"), "stderr": str(operation.get("stderr") or "")[-1200:]},
        "after": after,
    }
    result["receipt"] = _write_receipt(receipt_root, f"start-{plan['slug']}", result)
    return result


def _normalize_declared_paths(root: Path, values: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    normalized: list[str] = []
    issues: list[dict[str, str]] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            relative = resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            issues.append({"code": "declared_path_outside_worktree", "path": text})
            continue
        if relative == ".git" or relative.startswith(".git/"):
            issues.append({"code": "git_metadata_path_refused", "path": text})
            continue
        if relative not in normalized:
            normalized.append(relative)
    return normalized, issues


def commit_plan(
    task_id: str,
    changed_files: list[str],
    *,
    root: Path | str = DEFAULT_WORKTREE,
    message: str = "",
) -> dict[str, Any]:
    repo = repository_root(root)
    blockers: list[dict[str, Any]] = []
    if repo is None:
        blockers.append({"code": "git_worktree_required", "root": str(root)})
        repo = Path(root).expanduser().resolve()
    if _task_identity(task_id) is None:
        blockers.append({"code": "task_id_invalid", "rule": TASK_ID_RE.pattern})
    declared, path_issues = _normalize_declared_paths(repo, changed_files)
    blockers.extend(path_issues)
    if not declared:
        blockers.append({"code": "declared_changed_files_required"})
    rows = _status_entries(repo) if repository_root(repo) else []
    changed = [row["path"] for row in rows]
    changed_set = set(changed)
    declared_changed = [path for path in declared if path in changed_set]
    unchanged_declared = [path for path in declared if path not in changed_set]
    foreign_changes = [path for path in changed if path not in set(declared)]
    foreign_staged = [row["path"] for row in rows if row["staged"] and row["path"] not in set(declared)]
    if not declared_changed and declared:
        blockers.append({"code": "declared_files_have_no_changes", "paths": declared})
    if foreign_staged:
        blockers.append({"code": "foreign_staged_changes", "paths": foreign_staged})
    if not str(message or "").strip():
        blockers.append({"code": "commit_message_required"})
    branch = _stdout(_git(repo, "branch", "--show-current")) if repository_root(repo) else ""
    return {
        "schema": "work_git_change_owner.commit_plan.v1",
        "ok": not blockers,
        "generated_at": now_iso(),
        "task_id": task_id,
        "root": str(repo),
        "branch": branch,
        "head": _stdout(_git(repo, "rev-parse", "HEAD")) if repository_root(repo) else "",
        "declared_paths": declared,
        "declared_changed_paths": declared_changed,
        "unchanged_declared_paths": unchanged_declared,
        "foreign_change_count": len(foreign_changes),
        "foreign_changes_preserved": foreign_changes[:50],
        "foreign_staged_paths": foreign_staged,
        "isolation_recommended": bool(branch == "main" and foreign_changes),
        "blockers": blockers,
        "confirmation": COMMIT_CONFIRM,
        "acceptance": "the staged set must equal the declared changed paths exactly before commit",
    }


def commit_change_set(
    task_id: str,
    changed_files: list[str],
    *,
    message: str,
    confirm: str,
    root: Path | str = DEFAULT_WORKTREE,
    receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
) -> dict[str, Any]:
    plan = commit_plan(task_id, changed_files, root=root, message=message)
    if confirm != COMMIT_CONFIRM:
        return {"schema": "work_git_change_owner.commit.v1", "ok": False, "status": "blocked", "reason": f"pass --confirm {COMMIT_CONFIRM}", "plan": plan}
    if not plan.get("ok"):
        return {"schema": "work_git_change_owner.commit.v1", "ok": False, "status": "blocked", "reason": "commit_plan_blocked", "plan": plan}
    repo = Path(plan["root"])
    declared = list(plan["declared_changed_paths"])
    staged = _git(repo, "add", "--", *declared)
    if not staged.get("ok"):
        return {"schema": "work_git_change_owner.commit.v1", "ok": False, "status": "failed", "reason": "git_add_failed", "stderr": str(staged.get("stderr") or "")[-1200:], "plan": plan}
    staged_result = _git(repo, "diff", "--cached", "--name-only", "-z", text=False)
    staged_paths = [item.decode("utf-8", errors="surrogateescape") for item in bytes(staged_result.get("stdout") or b"").split(b"\0") if item]
    if set(staged_paths) != set(declared):
        return {
            "schema": "work_git_change_owner.commit.v1",
            "ok": False,
            "status": "blocked",
            "reason": "staged_scope_mismatch",
            "expected": declared,
            "actual": staged_paths,
            "recovery": "review the index; no reset or restore was performed",
            "plan": plan,
        }
    committed = _git(repo, "commit", "-m", str(message).strip(), timeout=120)
    after = snapshot(repo)
    result = {
        "schema": "work_git_change_owner.commit.v1",
        "ok": bool(committed.get("ok")),
        "status": "completed" if committed.get("ok") else "failed",
        "generated_at": now_iso(),
        "task_id": task_id,
        "commit": _stdout(_git(repo, "rev-parse", "HEAD")) if committed.get("ok") else "",
        "committed_paths": declared,
        "foreign_changes_preserved": plan["foreign_changes_preserved"],
        "operation": {"returncode": committed.get("returncode"), "stderr": str(committed.get("stderr") or "")[-1200:]},
        "after": after,
    }
    result["receipt"] = _write_receipt(receipt_root, f"commit-{task_id.lower()}", result)
    return result


def sync_plan(root: Path | str = DEFAULT_WORKTREE) -> dict[str, Any]:
    state = snapshot(root)
    branch = str(state.get("branch") or "")
    blockers: list[dict[str, Any]] = []
    if not state.get("ok"):
        blockers.append({"code": "git_worktree_required"})
    if not branch:
        blockers.append({"code": "named_branch_required"})
    if not state.get("origin_is_local_bare"):
        blockers.append({"code": "local_bare_origin_required", "origin": state.get("origin")})
    return {
        "schema": "work_git_change_owner.sync_plan.v1",
        "ok": not blockers,
        "generated_at": now_iso(),
        "root": state.get("root"),
        "branch": branch,
        "head": state.get("head"),
        "origin": state.get("origin"),
        "dirty_changes_not_included": int(state.get("change_count") or 0),
        "blockers": blockers,
        "confirmation": SYNC_CONFIRM,
        "remote_scope": "Windows local bare Git only",
    }


def sync_branch(
    *,
    confirm: str,
    root: Path | str = DEFAULT_WORKTREE,
    receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
) -> dict[str, Any]:
    plan = sync_plan(root)
    if confirm != SYNC_CONFIRM:
        return {"schema": "work_git_change_owner.sync.v1", "ok": False, "status": "blocked", "reason": f"pass --confirm {SYNC_CONFIRM}", "plan": plan}
    if not plan.get("ok"):
        return {"schema": "work_git_change_owner.sync.v1", "ok": False, "status": "blocked", "reason": "sync_plan_blocked", "plan": plan}
    repo = Path(str(plan["root"]))
    branch = str(plan["branch"])
    pushed = _git(repo, "push", "origin", f"HEAD:refs/heads/{branch}", timeout=120)
    remote_head = _stdout(_git(repo, "ls-remote", "--heads", "origin", f"refs/heads/{branch}"))
    remote_commit = remote_head.split()[0] if remote_head else ""
    result = {
        "schema": "work_git_change_owner.sync.v1",
        "ok": bool(pushed.get("ok") and remote_commit == plan["head"]),
        "status": "completed" if pushed.get("ok") else "failed",
        "generated_at": now_iso(),
        "plan": plan,
        "remote_commit": remote_commit,
        "operation": {"returncode": pushed.get("returncode"), "stderr": str(pushed.get("stderr") or "")[-1200:]},
    }
    result["receipt"] = _write_receipt(receipt_root, f"sync-{branch.replace('/', '-')}", result)
    return result


def _main_worktree(root: Path) -> Path | None:
    for row in _worktree_rows(root):
        if row.get("branch") == "refs/heads/main" and row.get("worktree"):
            return Path(row["worktree"]).resolve()
    return None


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _paths_intersect(left: str, right: str) -> bool:
    left = str(left or "").strip().strip("/")
    right = str(right or "").strip().strip("/")
    return bool(left and right) and (left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/"))


def _validation_receipt_selection(
    receipt_refs: list[str | dict[str, Any]],
    *,
    predecessor_commit: str,
    current_main_changed_paths: list[str],
) -> dict[str, Any]:
    """Select only validation-owner receipts whose bounded readback remains current."""

    from workflow_terminal_convergence import invalidate_dependents

    normalized: list[dict[str, Any]] = []
    identifier_counts: dict[str, int] = {}
    for index, raw in enumerate(receipt_refs):
        ref = str(raw.get("ref") or "").strip() if isinstance(raw, dict) else str(raw or "").strip()
        item: dict[str, Any] = {}
        readback_ok = False
        if ref:
            receipt_path = Path(ref).expanduser()
            if receipt_path.is_absolute() and receipt_path.is_file():
                try:
                    loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    loaded = {}
                if isinstance(loaded, dict):
                    item = loaded
                    readback_ok = True
        validator_id = str(item.get("validator_id") or "").strip()
        if validator_id:
            identifier_counts[validator_id] = identifier_counts.get(validator_id, 0) + 1
        normalized.append(
            {
                "validator_id": validator_id or f"unknown:{index}",
                "owner_contract_version": str(item.get("owner_contract_version") or "").strip(),
                "input_signature": str(item.get("input_signature") or "").strip(),
                "accepted": item.get("accepted") is True,
                "schema": str(item.get("schema") or "").strip(),
                "readback_ok": readback_ok and item.get("readback_ok") is True,
                "current": item.get("current") is True,
                "validated_head": str(item.get("validated_head") or "").strip(),
                "ref": ref,
                "source_dependencies": sorted(
                    {str(value).strip().replace("\\", "/") for value in item.get("source_dependencies") or [] if str(value).strip()}
                )
                if isinstance(item.get("source_dependencies"), list)
                else [],
                "depends_on": sorted(
                    {str(value).strip() for value in item.get("depends_on") or [] if str(value).strip()}
                )
                if isinstance(item.get("depends_on"), list)
                else [],
            }
        )

    known_ids = {item["validator_id"] for item in normalized}
    direct_reasons: dict[str, str] = {}
    for item in normalized:
        validator_id = item["validator_id"]
        if not item["readback_ok"]:
            direct_reasons[validator_id] = "validation_receipt_readback_unverified"
        elif item["schema"] != "validation_owner.receipt.v1":
            direct_reasons[validator_id] = "validation_receipt_schema_unverified"
        elif validator_id.startswith("unknown:"):
            direct_reasons[validator_id] = "validation_validator_id_missing"
        elif identifier_counts.get(validator_id, 0) > 1:
            direct_reasons[validator_id] = "validation_validator_id_duplicate"
        elif not item["owner_contract_version"]:
            direct_reasons[validator_id] = "validation_owner_contract_version_missing"
        elif not item["input_signature"]:
            direct_reasons[validator_id] = "validation_input_signature_missing"
        elif not item["accepted"]:
            direct_reasons[validator_id] = "validation_owner_not_accepted"
        elif not item["current"]:
            direct_reasons[validator_id] = "validation_owner_readback_not_current"
        elif item["validated_head"] != predecessor_commit:
            direct_reasons[validator_id] = "validation_source_head_mismatch"
        elif not item["source_dependencies"]:
            direct_reasons[validator_id] = "validation_source_dependencies_missing"
        elif any(dependency not in known_ids for dependency in item["depends_on"]):
            direct_reasons[validator_id] = "validation_dependency_unknown"
        elif any(
            _paths_intersect(source, changed)
            for source in item["source_dependencies"]
            for changed in current_main_changed_paths
        ):
            direct_reasons[validator_id] = "validation_source_dependency_changed"

    actions = [
        {"action_id": item["validator_id"], "depends_on": item["depends_on"]}
        for item in normalized
        if identifier_counts.get(item["validator_id"], 0) <= 1
    ]
    downstream = invalidate_dependents(actions, sorted(direct_reasons))
    invalidated_ids = set(direct_reasons) | set(downstream)
    invalidated: list[dict[str, str]] = []
    reusable_refs: list[str] = []
    skipped: list[dict[str, str]] = []
    for item in sorted(normalized, key=lambda row: row["validator_id"]):
        validator_id = item["validator_id"]
        if validator_id in invalidated_ids:
            invalidated.append(
                {
                    "validator_id": validator_id,
                    "reason": direct_reasons.get(validator_id, "validation_upstream_dependency_invalidated"),
                    "ref": item["ref"],
                }
            )
            continue
        reusable_refs.append(item["ref"])
        skipped.append(
            {
                "validator_id": validator_id,
                "reason": "validation_owner_receipt_current_and_dependencies_unchanged",
                "ref": item["ref"],
            }
        )
    return {
        "invalidated_dependencies": invalidated,
        "required_validations": list(invalidated),
        "reusable_receipt_refs": sorted(set(reusable_refs)),
        "skipped_validations": skipped,
        "input_signature": _canonical_digest(
            [
                {
                    "validator_id": item["validator_id"],
                    "schema": item["schema"],
                    "owner_contract_version": item["owner_contract_version"],
                    "input_signature": item["input_signature"],
                    "accepted": item["accepted"],
                    "readback_ok": item["readback_ok"],
                    "current": item["current"],
                    "validated_head": item["validated_head"],
                    "ref": item["ref"],
                    "source_dependencies": item["source_dependencies"],
                    "depends_on": item["depends_on"],
                }
                for item in sorted(normalized, key=lambda row: row["validator_id"])
            ]
        ),
    }


def successor_plan(
    predecessor_ref: str,
    declared_paths: list[str],
    *,
    validation_receipts: list[str | dict[str, Any]] | None = None,
    root: Path | str = DEFAULT_WORKTREE,
) -> dict[str, Any]:
    """Assess a stale task change-set against current main without mutation."""

    repo = repository_root(root)
    blockers: list[dict[str, Any]] = []
    if repo is None:
        repo = Path(root).expanduser().resolve()
        blockers.append({"code": "git_worktree_required"})
    main_root = _main_worktree(repo) if repository_root(repo) else None
    if main_root is None:
        blockers.append({"code": "main_integration_worktree_missing"})
        main_root = repo
    predecessor = str(predecessor_ref or "").strip()
    predecessor_commit = _stdout(_git(repo, "rev-parse", "--verify", f"{predecessor}^{{commit}}")) if predecessor else ""
    if not predecessor_commit:
        blockers.append({"code": "predecessor_unverifiable", "ref": predecessor})
    current_main_head = _stdout(_git(main_root, "rev-parse", "HEAD")) if repository_root(main_root) else ""
    old_base_head = (
        _stdout(_git(repo, "merge-base", current_main_head, predecessor_commit))
        if current_main_head and predecessor_commit
        else ""
    )
    if not old_base_head:
        blockers.append({"code": "common_base_unverifiable"})
    if predecessor_commit and current_main_head:
        if _git(repo, "merge-base", "--is-ancestor", predecessor_commit, current_main_head).get("ok"):
            blockers.append({"code": "predecessor_already_integrated", "predecessor_commit": predecessor_commit})
        elif _git(repo, "merge-base", "--is-ancestor", current_main_head, predecessor_commit).get("ok"):
            blockers.append({"code": "predecessor_not_stale", "predecessor_commit": predecessor_commit})
    declared, path_issues = _normalize_declared_paths(main_root, declared_paths)
    blockers.extend(path_issues)
    if not declared:
        blockers.append({"code": "declared_paths_required"})

    main_dirty = [row["path"] for row in _status_entries(main_root)] if repository_root(main_root) else []
    predecessor_worktree = next(
        (row for row in _worktree_rows(repo) if row.get("branch") == f"refs/heads/{predecessor}"),
        {},
    )
    predecessor_root = Path(predecessor_worktree["worktree"]) if predecessor_worktree.get("worktree") else None
    predecessor_dirty = [row["path"] for row in _status_entries(predecessor_root)] if predecessor_root else []
    if main_dirty or predecessor_dirty:
        blockers.append(
            {
                "code": "dirty_state",
                "main_paths": main_dirty[:50],
                "predecessor_paths": predecessor_dirty[:50],
            }
        )

    def changed_paths(start: str, end: str) -> list[str]:
        if not start or not end:
            return []
        result = _git(repo, "diff", "--name-only", "-z", f"{start}..{end}", text=False)
        if not result.get("ok"):
            return []
        return sorted(
            item.decode("utf-8", errors="surrogateescape")
            for item in bytes(result.get("stdout") or b"").split(b"\0")
            if item
        )

    predecessor_changed = changed_paths(old_base_head, predecessor_commit)
    current_main_changed = changed_paths(old_base_head, current_main_head)
    if predecessor_commit and old_base_head and not predecessor_changed:
        blockers.append({"code": "predecessor_changeset_empty"})
    if predecessor_changed and set(predecessor_changed) != set(declared):
        blockers.append(
            {
                "code": "declared_changeset_mismatch",
                "declared_paths": declared,
                "actual_paths": predecessor_changed,
            }
        )
    overlap = sorted(set(predecessor_changed) & set(current_main_changed))
    if overlap:
        blockers.append({"code": "path_overlap", "paths": overlap})

    structural_conflicts: list[dict[str, str]] = []
    for path in predecessor_changed:
        predecessor_type = _stdout(_git(repo, "cat-file", "-t", f"{predecessor_commit}:{path}"))
        current_type = _stdout(_git(repo, "cat-file", "-t", f"{current_main_head}:{path}"))
        if predecessor_type and current_type and predecessor_type != current_type:
            structural_conflicts.append(
                {
                    "path": path,
                    "predecessor_type": predecessor_type,
                    "current_main_type": current_type,
                }
            )
        parts = Path(path).parts
        for depth in range(1, len(parts)):
            prefix = Path(*parts[:depth]).as_posix()
            prefix_type = _stdout(_git(repo, "cat-file", "-t", f"{current_main_head}:{prefix}"))
            if prefix_type and prefix_type != "tree":
                structural_conflicts.append(
                    {
                        "path": path,
                        "conflicting_prefix": prefix,
                        "current_main_type": prefix_type,
                    }
                )
                break
    if structural_conflicts:
        blockers.append({"code": "tree_shape_conflict", "conflicts": structural_conflicts})

    patch_result = (
        _git(repo, "diff", "--binary", f"{old_base_head}..{predecessor_commit}", text=False)
        if old_base_head and predecessor_commit
        else {}
    )
    patch_bytes = bytes(patch_result.get("stdout") or b"")
    if predecessor_commit and old_base_head and not patch_result.get("ok"):
        blockers.append({"code": "changeset_unverifiable"})
    applicability = (
        _run(
            ["git", "-C", str(main_root), "apply", "--check", "--index", "--whitespace=nowarn"],
            text=False,
            input_data=patch_bytes,
        )
        if patch_bytes and patch_result.get("ok")
        else None
    )
    if applicability is not None and not applicability.get("ok"):
        blockers.append(
            {
                "code": "changeset_not_applicable",
                "stderr": bytes(applicability.get("stderr") or b"").decode("utf-8", errors="replace")[-1200:],
            }
        )
    changeset_digest = hashlib.sha256(patch_bytes).hexdigest() if patch_bytes else ""

    priority = [
        "dirty_state",
        "declared_changeset_mismatch",
        "path_overlap",
        "tree_shape_conflict",
        "changeset_not_applicable",
        "predecessor_already_integrated",
        "predecessor_not_stale",
        "predecessor_unverifiable",
        "common_base_unverifiable",
        "changeset_unverifiable",
        "predecessor_changeset_empty",
    ]
    blocker_codes = [str(item.get("code") or "") for item in blockers]
    reason = next((code for code in priority if code in blocker_codes), blocker_codes[0] if blocker_codes else "eligible")
    receipts = list(validation_receipts or [])
    validation_selection = _validation_receipt_selection(
        receipts,
        predecessor_commit=predecessor_commit,
        current_main_changed_paths=current_main_changed,
    )
    signature_payload = {
        "repository_id": _repository_id(repo) if repository_root(repo) else "",
        "predecessor_ref": predecessor,
        "predecessor_commit": predecessor_commit,
        "old_base_head": old_base_head,
        "current_main_head": current_main_head,
        "declared_paths": declared,
        "changeset_digest": changeset_digest,
        "current_main_changed_paths": current_main_changed,
        "validation_receipt_signature": validation_selection["input_signature"],
    }
    eligible = not blockers
    required_validations = (
        [
            {
                "validator_id": "validation_owner_selection",
                "reason": "current_main_requires_validation_owner_reassessment",
                "predecessor_commit": predecessor_commit,
                "current_main_head": current_main_head,
            },
            *validation_selection["required_validations"],
        ]
        if eligible and not receipts
        else validation_selection["required_validations"]
        if eligible
        else []
    )
    return {
        "schema": "work_git_change_owner.source_drift_successor_plan.v1",
        "ok": eligible,
        "eligible": eligible,
        "reason": reason,
        "generated_at": now_iso(),
        **signature_payload,
        "predecessor_changed_paths": predecessor_changed,
        "overlap_paths": overlap,
        "successor_signature": _canonical_digest(signature_payload),
        "validation_receipt_signature": validation_selection["input_signature"],
        "invalidated_dependencies": validation_selection["invalidated_dependencies"],
        "required_validations": required_validations,
        "reusable_receipt_refs": validation_selection["reusable_receipt_refs"] if eligible else [],
        "skipped_validations": validation_selection["skipped_validations"] if eligible else [],
        "next_action": "request_current_head_r2_scope" if eligible else "blocked",
        "blockers": blockers,
        "read_only": True,
        "automatic_replay_allowed": False,
    }


def _successor_receipt_refs(plan: dict[str, Any]) -> list[str]:
    refs = {str(value).strip() for value in plan.get("reusable_receipt_refs") or [] if str(value).strip()}
    for row in plan.get("required_validations") or []:
        if isinstance(row, dict) and str(row.get("ref") or "").strip():
            refs.add(str(row["ref"]).strip())
    return sorted(refs)


def _verified_successor_plan(
    plan: dict[str, Any], declared_paths: list[str], *, root: Path | str,
) -> dict[str, Any]:
    """Rebuild the read-only authority projection and reject any drift."""

    if plan.get("schema") != "work_git_change_owner.source_drift_successor_plan.v1":
        return {"ok": False, "reason": "successor_plan_schema_invalid"}
    current = successor_plan(
        str(plan.get("predecessor_ref") or ""), declared_paths,
        validation_receipts=_successor_receipt_refs(plan), root=root,
    )
    comparable = (
        "eligible", "predecessor_commit", "old_base_head", "current_main_head",
        "declared_paths", "changeset_digest", "validation_receipt_signature",
        "successor_signature",
    )
    mismatches = [key for key in comparable if current.get(key) != plan.get(key)]
    if not current.get("eligible") or mismatches:
        return {
            "ok": False, "reason": "successor_plan_source_or_signature_changed",
            "mismatched_fields": mismatches, "current_plan": current,
        }
    return {"ok": True, "plan": current}


def integrate_plan(branch: str, *, root: Path | str = DEFAULT_WORKTREE) -> dict[str, Any]:
    repo = repository_root(root)
    blockers: list[dict[str, Any]] = []
    branch_name = str(branch or "").strip()
    if repo is None:
        blockers.append({"code": "git_worktree_required"})
        repo = Path(root).expanduser().resolve()
    if not branch_name.startswith("codex/task/"):
        blockers.append({"code": "task_branch_required", "branch": branch_name})
    branch_exists = bool(_git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}").get("ok"))
    if not branch_exists:
        blockers.append({"code": "task_branch_missing", "branch": branch_name})
    main_root = _main_worktree(repo) if repository_root(repo) else None
    if main_root is None:
        blockers.append({"code": "main_integration_worktree_missing"})
    fast_forward = bool(branch_exists and _git(repo, "merge-base", "--is-ancestor", "main", branch_name).get("ok"))
    if branch_exists and not fast_forward:
        blockers.append({"code": "task_branch_rebase_required", "next_action": f"rebase {branch_name} onto current main in its task worktree"})
    changed_result = _git(repo, "diff", "--name-only", "-z", f"main..{branch_name}", text=False) if branch_exists else {}
    task_paths = [item.decode("utf-8", errors="surrogateescape") for item in bytes(changed_result.get("stdout") or b"").split(b"\0") if item]
    main_rows = _status_entries(main_root) if main_root else []
    main_dirty = [row["path"] for row in main_rows]
    main_staged = [row["path"] for row in main_rows if row["staged"]]
    overlap = sorted(set(task_paths) & set(main_dirty))
    equivalent_overlap, conflicting_overlap = _equivalent_dirty_overlap(
        main_root, branch_name, main_rows, overlap,
    ) if main_root and branch_exists else ([], overlap)
    if main_staged:
        blockers.append({"code": "main_has_staged_changes", "paths": main_staged})
    if conflicting_overlap:
        blockers.append({"code": "task_changes_overlap_dirty_main", "paths": conflicting_overlap})
    task_worktree = next((row for row in _worktree_rows(repo) if row.get("branch") == f"refs/heads/{branch_name}"), {})
    task_root = Path(task_worktree["worktree"]) if task_worktree.get("worktree") else None
    task_dirty = [row["path"] for row in _status_entries(task_root)] if task_root else []
    if task_dirty:
        blockers.append({"code": "task_worktree_dirty", "paths": task_dirty[:50]})
    return {
        "schema": "work_git_change_owner.integrate_plan.v1",
        "ok": not blockers,
        "generated_at": now_iso(),
        "branch": branch_name,
        "main_root": str(main_root or ""),
        "task_root": str(task_root or ""),
        "task_path_count": len(task_paths),
        "task_paths": task_paths[:100],
        "main_dirty_path_count": len(main_dirty),
        "main_dirty_paths_preserved": main_dirty[:100],
        "overlap_count": len(overlap),
        "overlap": overlap[:100],
        "equivalent_dirty_overlap": equivalent_overlap[:100],
        "conflicting_dirty_overlap": conflicting_overlap[:100],
        "fast_forward": fast_forward,
        "blockers": blockers,
        "confirmation": INTEGRATE_CONFIRM,
        "cleanup_performed": False,
    }


def integrate_task(
    branch: str,
    *,
    confirm: str,
    root: Path | str = DEFAULT_WORKTREE,
    receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
) -> dict[str, Any]:
    plan = integrate_plan(branch, root=root)
    if confirm != INTEGRATE_CONFIRM:
        return {"schema": "work_git_change_owner.integrate.v1", "ok": False, "status": "blocked", "reason": f"pass --confirm {INTEGRATE_CONFIRM}", "plan": plan}
    if not plan.get("ok"):
        return {"schema": "work_git_change_owner.integrate.v1", "ok": False, "status": "blocked", "reason": "integration_plan_blocked", "plan": plan}
    main_root = Path(plan["main_root"])
    # Git refuses a fast-forward when the main worktree contains even a byte-
    # equivalent local edit.  The plan has already proved that every allowed
    # overlap equals the target branch blob; autostash preserves both those
    # bytes and unrelated non-overlapping edits across the fast-forward.
    # Git leaves an explicit autostash recovery state if restoration fails.
    merged = _git(main_root, "merge", "--ff-only", "--autostash", str(plan["branch"]), timeout=120)
    synchronized = sync_branch(confirm=SYNC_CONFIRM, root=main_root, receipt_root=receipt_root) if merged.get("ok") else {}
    after = snapshot(main_root)
    result = {
        "schema": "work_git_change_owner.integrate.v1",
        "ok": bool(merged.get("ok") and synchronized.get("ok")),
        "status": "completed" if merged.get("ok") and synchronized.get("ok") else "failed",
        "generated_at": now_iso(),
        "plan": plan,
        "equivalent_dirty_overlap_reconciled": list(plan.get("equivalent_dirty_overlap") or []) if merged.get("ok") else [],
        "merge": {"returncode": merged.get("returncode"), "stderr": str(merged.get("stderr") or "")[-1200:]},
        "sync": synchronized,
        "after": after,
        "branch_deleted": False,
        "worktree_removed": False,
    }
    result["receipt"] = _write_receipt(receipt_root, f"integrate-{str(plan['branch']).replace('/', '-')}", result)
    return result


def _r2_scope(permit_ref: str, *, state_root: Path | str | None = None) -> dict[str, Any]:
    import scoped_authorization as authorization

    snapshot = authorization.permit_snapshot(permit_ref, state_root=state_root)
    if not snapshot.get("ok"):
        return snapshot
    scope = snapshot.get("scope") if isinstance(snapshot.get("scope"), dict) else {}
    if snapshot.get("intent_type") not in {"task_intent", "one_time"}:
        return {"ok": False, "reason": "work_git_lifecycle_exact_intent_required"}
    return {"ok": True, "snapshot": snapshot, "scope": scope}


def _r2_permit_ref(permit_ref: str, operation_id: str, *, state_root: Path | str | None = None) -> dict[str, Any]:
    """Resolve one owner-bound permit privately when only operation_id is supplied."""

    if str(permit_ref or "").strip():
        return {"ok": True, "permit_ref": str(permit_ref)}
    import scoped_authorization as authorization

    operation = authorization.operation_snapshot(operation_id, executor="work_git_change_owner", state_root=state_root)
    if not operation.get("ok"):
        return {"ok": False, "reason": "work_git_lifecycle_operation_not_found"}
    resolved = str(operation.get("permit_ref") or "")
    if not resolved:
        return {"ok": False, "reason": "work_git_lifecycle_operation_permit_missing"}
    return {"ok": True, "permit_ref": resolved, "operation_ref": operation.get("operation_ref", "")}


def _r2_gate(
    *, permit_ref: str, operation_id: str, step: str, repo: Path, task_id: str,
    branch: str, base_head: str, declared_paths: list[str],
    workflow_semantic_hash: str, successor: dict[str, Any] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    from work_git_change_owner_process import authorize_step

    resolved = _r2_permit_ref(permit_ref, operation_id, state_root=state_root)
    if not resolved.get("ok"):
        return resolved
    permit_ref = str(resolved["permit_ref"])
    scope_result = _r2_scope(permit_ref, state_root=state_root)
    if not scope_result.get("ok"):
        return scope_result
    scope = scope_result["scope"]
    return authorize_step(
        permit_ref=permit_ref, operation_id=operation_id, step=step,
        thread_id=str(scope.get("thread_id") or ""), repository_id=_repository_id(repo),
        task_id=task_id, branch=branch, base_head=base_head,
        declared_paths=declared_paths, workflow_semantic_hash=workflow_semantic_hash,
        successor=successor,
        state_root=state_root,
    )


def _reused_lifecycle_result(gate: dict[str, Any]) -> dict[str, Any]:
    receipt = Path(str(gate.get("effect_receipt_ref") or ""))
    if not receipt.is_file():
        return {"ok": False, "status": "blocked", "reason": "work_git_lifecycle_receipt_missing", "gate": gate}
    try:
        stored = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "status": "blocked", "reason": "work_git_lifecycle_receipt_unreadable", "gate": gate}
    if not isinstance(stored, dict) or not stored.get("ok"):
        return {"ok": False, "status": "blocked", "reason": "work_git_lifecycle_receipt_unreadable", "gate": gate}
    return {**stored, "reused": True, "authorization": gate}


def _record_r2_result(
    *, operation_id: str, step: str, result: dict[str, Any], terminal: bool,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    from work_git_change_owner_process import record_step

    receipt = str(result.get("receipt") or "")
    signature = hashlib.sha256(json.dumps({
        "step": step, "ok": bool(result.get("ok")), "commit": result.get("commit", ""),
        "remote_commit": result.get("remote_commit", ""),
        "head": (result.get("after") or {}).get("head", ""),
        "changeset_digest": result.get("changeset_digest", ""),
        "declared_path_digest": result.get("declared_path_digest", ""),
        "successor_signature": result.get("successor_signature", ""),
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    if not result.get("ok"):
        from scoped_authorization import record_effect

        journal = record_effect(
            operation_id, executor="work_git_change_owner", status="effect_unknown",
            effect_receipt_ref=receipt, details={"step": step, "result_signature": signature},
            state_root=state_root,
        )
        return {**result, "authorization": journal}
    journal = record_step(
        operation_id=operation_id, step=step, effect_receipt_ref=receipt,
        success=True, result_signature=signature, terminal=terminal, state_root=state_root,
    )
    return {**result, "authorization": journal}


def prepare_r2_lifecycle(
    task_id: str, declared_paths: list[str], *, thread_id: str,
    assessment: dict[str, Any], rollout_path: Path | str, user_message_ref: str,
    operation_id: str, workflow_semantic_hash: str,
    successor: dict[str, Any] | None = None,
    root: Path | str = DEFAULT_WORKTREE, state_root: Path | str | None = None,
    receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
) -> dict[str, Any]:
    """Prepare the single current-task permit for a declared R2 change-set."""

    from work_git_change_owner_process import prepare_r2_lifecycle as prepare

    plan = start_plan(task_id, root=root)
    if not plan.get("ok"):
        result = {"ok": False, "reason": "work_git_lifecycle_start_plan_blocked", "plan": plan}
        return _record_prepare_result(result, task_id=task_id, operation_id=operation_id, receipt_root=receipt_root)
    if successor is not None:
        verified = _verified_successor_plan(successor, declared_paths, root=root)
        if not verified.get("ok"):
            result = verified
            return _record_prepare_result(result, task_id=task_id, operation_id=operation_id, receipt_root=receipt_root)
        successor = verified["plan"]
    result = prepare(
        thread_id=thread_id, repository_id=_repository_id(Path(plan["source_root"])),
        task_id=task_id, branch=str(plan["branch"]), base_head=str(plan["base_commit"]),
        declared_paths=declared_paths, assessment=assessment, rollout_path=rollout_path,
        user_message_ref=user_message_ref, operation_id=operation_id,
        workflow_semantic_hash=workflow_semantic_hash, successor=successor, state_root=state_root,
    )
    return _record_prepare_result(result, task_id=task_id, operation_id=operation_id, receipt_root=receipt_root)


def start_r2_task(
    task_id: str, declared_paths: list[str], *, permit_ref: str, operation_id: str,
    workflow_semantic_hash: str, confirm: str = "", root: Path | str = DEFAULT_WORKTREE,
    task_root: Path | str = DEFAULT_TASK_ROOT, receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
    successor: dict[str, Any] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Start a task worktree under one already prepared R2 lifecycle permit."""

    plan = start_plan(task_id, root=root, task_root=task_root)
    if not plan.get("ok"):
        return start_task(task_id, confirm=confirm, root=root, task_root=task_root, receipt_root=receipt_root)
    if successor is not None:
        verified = _verified_successor_plan(successor, declared_paths, root=root)
        if not verified.get("ok"):
            return {"schema": "work_git_change_owner.r2_start.v1", "status": "blocked", **verified}
        successor = verified["plan"]
    gate = _r2_gate(
        permit_ref=permit_ref, operation_id=operation_id, step="start",
        repo=Path(plan["source_root"]), task_id=task_id, branch=str(plan["branch"]),
        base_head=str(plan["base_commit"]), declared_paths=declared_paths,
        workflow_semantic_hash=workflow_semantic_hash, successor=successor, state_root=state_root,
    )
    if not gate.get("ok"):
        return {"schema": "work_git_change_owner.r2_start.v1", "ok": False, "status": "blocked", "reason": gate.get("reason", "authorization_blocked"), "authorization": gate, "plan": plan}
    if gate.get("skip_effect"):
        return _reused_lifecycle_result(gate)
    result = start_task(task_id, confirm=START_CONFIRM, root=root, task_root=task_root, receipt_root=receipt_root)
    return _record_r2_result(operation_id=operation_id, step="start", result=result, terminal=False, state_root=state_root)


def replay_r2_successor(
    task_id: str, declared_paths: list[str], *, successor: dict[str, Any],
    permit_ref: str, operation_id: str, workflow_semantic_hash: str, confirm: str = "",
    root: Path | str = DEFAULT_WORKTREE, receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Replay one exact successor-plan changeset under its bound R2 permit."""

    task = repository_root(root)
    if task is None:
        return {"schema": "work_git_change_owner.r2_replay.v1", "ok": False, "status": "blocked", "reason": "git_worktree_required"}
    binding = {
        key: str(successor.get(key) or "").strip()
        for key in (
            "predecessor_ref", "predecessor_commit", "old_base_head",
            "current_main_head", "successor_signature", "changeset_digest",
            "validation_receipt_signature",
        )
    }
    required = [key for key, value in binding.items() if not value]
    if (
        successor.get("schema") != "work_git_change_owner.source_drift_successor_plan.v1"
        or successor.get("eligible") is not True
        or required
    ):
        return {
            "schema": "work_git_change_owner.r2_replay.v1", "ok": False,
            "status": "blocked",
            "reason": "successor_plan_incomplete",
            "missing_fields": required,
        }
    normalized, issues = _normalize_declared_paths(task, declared_paths)
    if issues or normalized != sorted(successor.get("declared_paths") or []):
        return {
            "schema": "work_git_change_owner.r2_replay.v1", "ok": False,
            "status": "blocked", "reason": "successor_replay_declared_paths_changed",
            "path_issues": issues,
        }
    verified = _verified_successor_plan(successor, normalized, root=task)
    if not verified.get("ok"):
        return {"schema": "work_git_change_owner.r2_replay.v1", "status": "blocked", **verified}
    successor = verified["plan"]
    binding = {key: str(successor.get(key) or "").strip() for key in binding}
    branch = _stdout(_git(task, "branch", "--show-current"))
    gate = _r2_gate(
        permit_ref=permit_ref, operation_id=operation_id, step="replay",
        repo=task, task_id=task_id, branch=branch,
        base_head=binding["current_main_head"], declared_paths=normalized,
        workflow_semantic_hash=workflow_semantic_hash, successor=binding,
        state_root=state_root,
    )
    if not gate.get("ok"):
        return {"schema": "work_git_change_owner.r2_replay.v1", "ok": False, "status": "blocked", "reason": gate.get("reason", "authorization_blocked"), "authorization": gate}
    if gate.get("skip_effect"):
        return _reused_lifecycle_result(gate)
    from work_git_change_owner_replay import replay_changeset

    result = replay_changeset(
        task_root=task, repository_root=task, expected_branch=branch,
        expected_base_head=binding["current_main_head"],
        predecessor_commit=binding["predecessor_commit"],
        old_base_head=binding["old_base_head"], declared_paths=normalized,
        expected_changeset_digest=binding["changeset_digest"],
        successor_signature=binding["successor_signature"],
    )
    result["receipt"] = _write_receipt(receipt_root, f"replay-{task_id}", result)
    return _record_r2_result(
        operation_id=operation_id, step="replay", result=result,
        terminal=False, state_root=state_root,
    )


def commit_r2_change_set(
    task_id: str, changed_files: list[str], *, permit_ref: str, operation_id: str,
    workflow_semantic_hash: str, message: str, confirm: str = "",
    root: Path | str = DEFAULT_WORKTREE, receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
    successor: dict[str, Any] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Commit exactly the permit-bound paths without requesting another token."""

    plan = commit_plan(task_id, changed_files, root=root, message=message)
    if not plan.get("ok"):
        return commit_change_set(task_id, changed_files, message=message, confirm=confirm, root=root, receipt_root=receipt_root)
    if successor is not None:
        verified = _verified_successor_plan(successor, changed_files, root=root)
        if not verified.get("ok"):
            return {"schema": "work_git_change_owner.r2_commit.v1", "status": "blocked", **verified}
        successor = verified["plan"]
    resolved = _r2_permit_ref(permit_ref, operation_id, state_root=state_root)
    if not resolved.get("ok"):
        return {"schema": "work_git_change_owner.r2_commit.v1", "ok": False, "status": "blocked", "reason": resolved.get("reason", "authorization_blocked"), "authorization": resolved, "plan": plan}
    resolved_permit_ref = str(resolved["permit_ref"])
    scope_result = _r2_scope(resolved_permit_ref, state_root=state_root)
    if not scope_result.get("ok"):
        return {"schema": "work_git_change_owner.r2_commit.v1", "ok": False, "status": "blocked", "reason": scope_result.get("reason", "authorization_blocked"), "authorization": scope_result, "plan": plan}
    gate = _r2_gate(
        permit_ref=permit_ref, operation_id=operation_id, step="commit",
        repo=Path(plan["root"]), task_id=task_id, branch=str(plan["branch"]),
        base_head=str((scope_result.get("scope") or {}).get("source_signature") or ""),
        declared_paths=changed_files, workflow_semantic_hash=workflow_semantic_hash,
        successor=successor,
        state_root=state_root,
    )
    if not gate.get("ok"):
        return {"schema": "work_git_change_owner.r2_commit.v1", "ok": False, "status": "blocked", "reason": gate.get("reason", "authorization_blocked"), "authorization": gate, "plan": plan}
    if gate.get("skip_effect"):
        return _reused_lifecycle_result(gate)
    result = commit_change_set(task_id, changed_files, message=message, confirm=COMMIT_CONFIRM, root=root, receipt_root=receipt_root)
    return _record_r2_result(operation_id=operation_id, step="commit", result=result, terminal=False, state_root=state_root)


def sync_r2_branch(
    task_id: str, *, permit_ref: str, operation_id: str, workflow_semantic_hash: str,
    confirm: str = "", root: Path | str = DEFAULT_WORKTREE, receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
    successor: dict[str, Any] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Synchronize the task branch to local bare Git under the same permit."""

    plan = sync_plan(root)
    if not plan.get("ok"):
        return sync_branch(confirm=confirm, root=root, receipt_root=receipt_root)
    resolved = _r2_permit_ref(permit_ref, operation_id, state_root=state_root)
    if not resolved.get("ok"):
        return {"schema": "work_git_change_owner.r2_sync.v1", "ok": False, "status": "blocked", "reason": resolved.get("reason", "authorization_blocked"), "authorization": resolved, "plan": plan}
    scope_result = _r2_scope(str(resolved["permit_ref"]), state_root=state_root)
    if not scope_result.get("ok"):
        return {"schema": "work_git_change_owner.r2_sync.v1", "ok": False, "status": "blocked", "reason": scope_result.get("reason", "authorization_blocked"), "authorization": scope_result, "plan": plan}
    if successor is not None:
        verified = _verified_successor_plan(
            successor, list(scope_result["scope"].get("locations") or []), root=root,
        )
        if not verified.get("ok"):
            return {"schema": "work_git_change_owner.r2_sync.v1", "status": "blocked", **verified}
        successor = verified["plan"]
    gate = _r2_gate(
        permit_ref=permit_ref, operation_id=operation_id, step="sync",
        repo=Path(str(plan["root"])), task_id=task_id, branch=str(plan["branch"]),
        base_head=str(scope_result["scope"].get("source_signature") or ""),
        declared_paths=list(scope_result["scope"].get("locations") or []),
        workflow_semantic_hash=workflow_semantic_hash, successor=successor, state_root=state_root,
    )
    if not gate.get("ok"):
        return {"schema": "work_git_change_owner.r2_sync.v1", "ok": False, "status": "blocked", "reason": gate.get("reason", "authorization_blocked"), "authorization": gate, "plan": plan}
    if gate.get("skip_effect"):
        return _reused_lifecycle_result(gate)
    result = sync_branch(confirm=SYNC_CONFIRM, root=root, receipt_root=receipt_root)
    return _record_r2_result(operation_id=operation_id, step="sync", result=result, terminal=False, state_root=state_root)


def integrate_r2_task(
    task_id: str, branch: str, *, permit_ref: str, operation_id: str,
    workflow_semantic_hash: str, confirm: str = "", root: Path | str = DEFAULT_WORKTREE,
    receipt_root: Path | str = DEFAULT_RECEIPT_ROOT, successor: dict[str, Any] | None = None,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Fast-forward integrate and local-bare sync under the same R2 permit."""

    plan = integrate_plan(branch, root=root)
    if not plan.get("ok"):
        return integrate_task(branch, confirm=confirm, root=root, receipt_root=receipt_root)
    resolved = _r2_permit_ref(permit_ref, operation_id, state_root=state_root)
    if not resolved.get("ok"):
        return {"schema": "work_git_change_owner.r2_integrate.v1", "ok": False, "status": "blocked", "reason": resolved.get("reason", "authorization_blocked"), "authorization": resolved, "plan": plan}
    scope_result = _r2_scope(str(resolved["permit_ref"]), state_root=state_root)
    if not scope_result.get("ok"):
        return {"schema": "work_git_change_owner.r2_integrate.v1", "ok": False, "status": "blocked", "reason": scope_result.get("reason", "authorization_blocked"), "authorization": scope_result, "plan": plan}
    if successor is not None:
        verified = _verified_successor_plan(
            successor, list(scope_result["scope"].get("locations") or []), root=root,
        )
        if not verified.get("ok"):
            return {"schema": "work_git_change_owner.r2_integrate.v1", "status": "blocked", **verified}
        successor = verified["plan"]
    gate = _r2_gate(
        permit_ref=permit_ref, operation_id=operation_id, step="integrate",
        repo=Path(str(plan["main_root"])), task_id=task_id, branch=branch,
        base_head=str(scope_result["scope"].get("source_signature") or ""),
        declared_paths=list(scope_result["scope"].get("locations") or []),
        workflow_semantic_hash=workflow_semantic_hash, successor=successor, state_root=state_root,
    )
    if not gate.get("ok"):
        return {"schema": "work_git_change_owner.r2_integrate.v1", "ok": False, "status": "blocked", "reason": gate.get("reason", "authorization_blocked"), "authorization": gate, "plan": plan}
    if gate.get("skip_effect"):
        return _reused_lifecycle_result(gate)
    result = integrate_task(branch, confirm=INTEGRATE_CONFIRM, root=root, receipt_root=receipt_root)
    return _record_r2_result(operation_id=operation_id, step="integrate", result=result, terminal=True, state_root=state_root)


def config_plan(root: Path | str = DEFAULT_WORKTREE) -> dict[str, Any]:
    repo = repository_root(root)
    blockers: list[dict[str, Any]] = []
    if repo is None:
        blockers.append({"code": "git_worktree_required"})
        repo = Path(root).expanduser().resolve()
    bare = _origin_path(repo) if repository_root(repo) else None
    if not _is_bare_repository(bare):
        blockers.append({"code": "local_bare_origin_required", "origin": str(bare or "")})
    worktree_rows = [
        {"key": key, "expected": value, "actual": _config_value(repo, key), "current": _config_value(repo, key) == value}
        for key, value in WORKTREE_CONFIG.items()
    ]
    bare_rows = [
        {"key": key, "expected": value, "actual": _config_value(bare, key), "current": _config_value(bare, key) == value}
        for key, value in BARE_CONFIG.items()
    ] if bare else []
    common_dir = _stdout(_git(repo, "rev-parse", "--git-common-dir")) if repository_root(repo) else ""
    common_path = Path(common_dir)
    if common_dir and not common_path.is_absolute():
        common_path = repo / common_path
    return {
        "schema": "work_git_change_owner.config_plan.v1",
        "ok": not blockers,
        "generated_at": now_iso(),
        "root": str(repo),
        "bare_root": str(bare or ""),
        "worktree_config_path": str((common_path.resolve() / "config") if common_dir else ""),
        "bare_config_path": str((bare / "config") if bare else ""),
        "worktree": worktree_rows,
        "bare": bare_rows,
        "changes_required": any(not row["current"] for row in [*worktree_rows, *bare_rows]),
        "fsmonitor_enabled": False,
        "fsmonitor_reason": "unsupported_on_current_WSL_platform",
        "blockers": blockers,
        "confirmation": CONFIG_CONFIRM,
    }


def apply_config(
    *,
    confirm: str,
    root: Path | str = DEFAULT_WORKTREE,
    receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
) -> dict[str, Any]:
    plan = config_plan(root)
    if confirm != CONFIG_CONFIRM:
        return {"schema": "work_git_change_owner.config_apply.v1", "ok": False, "status": "blocked", "reason": f"pass --confirm {CONFIG_CONFIRM}", "plan": plan}
    if not plan.get("ok"):
        return {"schema": "work_git_change_owner.config_apply.v1", "ok": False, "status": "blocked", "reason": "config_plan_blocked", "plan": plan}
    backup_paths = [path for path in [plan["worktree_config_path"], plan["bare_config_path"]] if path]
    backup = create_backup(
        backup_paths,
        category="work-git-config",
        purpose="before-safe-work-git-configuration",
        remark="work-git-change-owner",
        trigger="work_git_change_owner.apply_config",
    )
    if not backup.get("ok"):
        return {"schema": "work_git_change_owner.config_apply.v1", "ok": False, "status": "blocked", "reason": "config_backup_failed", "backup": backup, "plan": plan}
    operations: list[dict[str, Any]] = []
    worktree = Path(plan["root"])
    bare = Path(plan["bare_root"])
    for key, value in WORKTREE_CONFIG.items():
        result = _git(worktree, "config", "--local", key, value)
        operations.append({"scope": "worktree", "key": key, "value": value, "ok": bool(result.get("ok"))})
    for key, value in BARE_CONFIG.items():
        result = _git(bare, "config", "--local", key, value)
        operations.append({"scope": "bare", "key": key, "value": value, "ok": bool(result.get("ok"))})
    after = config_plan(worktree)
    result = {
        "schema": "work_git_change_owner.config_apply.v1",
        "ok": bool(all(row["ok"] for row in operations) and after.get("ok") and not after.get("changes_required")),
        "status": "completed" if all(row["ok"] for row in operations) else "failed",
        "generated_at": now_iso(),
        "backup": backup,
        "operations": operations,
        "after": after,
    }
    result["receipt"] = _write_receipt(receipt_root, "config-latest", result)
    return result


def maintenance_plan(root: Path | str = DEFAULT_WORKTREE) -> dict[str, Any]:
    state = snapshot(root)
    config = config_plan(root)
    blockers: list[dict[str, Any]] = []
    if not state.get("ok"):
        blockers.append({"code": "git_worktree_required"})
    if config.get("changes_required"):
        blockers.append({"code": "safe_git_config_incomplete", "next_action": f"config-apply --confirm {CONFIG_CONFIRM}"})
    return {
        "schema": "work_git_change_owner.maintenance_plan.v1",
        "ok": not blockers,
        "generated_at": now_iso(),
        "root": state.get("root", ""),
        "bare_root": config.get("bare_root", ""),
        "tasks": list(MAINTENANCE_TASKS),
        "scope": "commit graph and object packing only; no fetch, prune, source edit, or branch mutation",
        "blockers": blockers,
        "confirmation": MAINTENANCE_CONFIRM,
    }


def run_maintenance(
    *,
    confirm: str,
    root: Path | str = DEFAULT_WORKTREE,
    receipt_root: Path | str = DEFAULT_RECEIPT_ROOT,
) -> dict[str, Any]:
    plan = maintenance_plan(root)
    if confirm != MAINTENANCE_CONFIRM:
        return {"schema": "work_git_change_owner.maintenance.v1", "ok": False, "status": "blocked", "reason": f"pass --confirm {MAINTENANCE_CONFIRM}", "plan": plan}
    if not plan.get("ok"):
        return {"schema": "work_git_change_owner.maintenance.v1", "ok": False, "status": "blocked", "reason": "maintenance_plan_blocked", "plan": plan}
    arguments = ["maintenance", "run", *(f"--task={task}" for task in MAINTENANCE_TASKS)]
    targets = [("worktree", Path(str(plan["root"]))), ("bare", Path(str(plan["bare_root"])))]
    operations = []
    for scope, target in targets:
        result = _git(target, *arguments, timeout=180)
        operations.append({"scope": scope, "root": str(target), "ok": bool(result.get("ok")), "returncode": result.get("returncode"), "stderr": str(result.get("stderr") or "")[-1200:]})
    after = snapshot(root)
    result = {
        "schema": "work_git_change_owner.maintenance.v1",
        "ok": all(item["ok"] for item in operations),
        "status": "completed" if all(item["ok"] for item in operations) else "failed",
        "generated_at": now_iso(),
        "plan": plan,
        "operations": operations,
        "after": after,
    }
    result["receipt"] = _write_receipt(receipt_root, "maintenance-latest", result)
    return result


def validate(root: Path | str = DEFAULT_WORKTREE) -> dict[str, Any]:
    state = snapshot(root)
    config = config_plan(root)
    issues: list[dict[str, Any]] = []
    if not state.get("ok"):
        issues.append({"code": "work_git_snapshot_failed", "detail": state.get("reason")})
    if not config.get("ok"):
        issues.extend(config.get("blockers") or [])
    if config.get("changes_required"):
        issues.append({"code": "safe_git_config_incomplete", "next_action": f"apply --confirm {CONFIG_CONFIRM}"})
    return {
        "schema": "work_git_change_owner.validate.v1",
        "ok": not issues,
        "generated_at": now_iso(),
        "issues": issues,
        "snapshot": state,
        "config": config,
    }


def _add_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=str(DEFAULT_WORKTREE))


def _add_r2_authorization(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--permit-ref", default="")
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--workflow-semantic-hash", required=True)
    parser.add_argument("--authorization-state-root", default="")
    parser.add_argument("--successor-plan-json", default="")


def _load_successor_plan(path: str) -> dict[str, Any] | None:
    if not str(path or "").strip():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": "invalid", "eligible": False, "reason": "successor_plan_unreadable"}
    return payload if isinstance(payload, dict) else {"schema": "invalid", "eligible": False, "reason": "successor_plan_not_object"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Governed Work Git task and change-set owner")
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = sub.add_parser("snapshot")
    _add_root(snapshot_parser)
    validate_parser = sub.add_parser("validate")
    _add_root(validate_parser)
    start_plan_parser = sub.add_parser("start-plan")
    start_plan_parser.add_argument("--task-id", required=True)
    start_plan_parser.add_argument("--task-root", default=str(DEFAULT_TASK_ROOT))
    _add_root(start_plan_parser)
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--task-id", required=True)
    start_parser.add_argument("--task-root", default=str(DEFAULT_TASK_ROOT))
    start_parser.add_argument("--confirm", default="")
    _add_root(start_parser)
    for command in ("commit-plan", "commit"):
        child = sub.add_parser(command)
        child.add_argument("--task-id", required=True)
        child.add_argument("--changed", action="append", default=[])
        child.add_argument("--message", required=True)
        if command == "commit":
            child.add_argument("--confirm", default="")
        _add_root(child)
    sync_plan_parser = sub.add_parser("sync-plan")
    _add_root(sync_plan_parser)
    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--confirm", default="")
    _add_root(sync_parser)
    for command in ("integrate-plan", "integrate"):
        child = sub.add_parser(command)
        child.add_argument("--branch", required=True)
        if command == "integrate":
            child.add_argument("--confirm", default="")
        _add_root(child)
    successor_parser = sub.add_parser("successor-plan")
    successor_parser.add_argument("--predecessor", required=True)
    successor_parser.add_argument("--declared", action="append", default=[])
    successor_parser.add_argument("--validation-receipt", action="append", default=[])
    _add_root(successor_parser)
    r2_prepare = sub.add_parser("r2-prepare")
    r2_prepare.add_argument("--task-id", required=True)
    r2_prepare.add_argument("--declared", action="append", default=[])
    r2_prepare.add_argument("--thread-id", required=True)
    r2_prepare.add_argument("--assessment-json", default="")
    r2_prepare.add_argument("--emit-assessment", action="store_true")
    r2_prepare.add_argument("--rollout-path", required=True)
    r2_prepare.add_argument("--user-message-ref", required=True)
    r2_prepare.add_argument("--operation-id", required=True)
    r2_prepare.add_argument("--workflow-semantic-hash", required=True)
    r2_prepare.add_argument("--authorization-state-root", default="")
    r2_prepare.add_argument("--successor-plan-json", default="")
    r2_prepare.add_argument("--receipt-root", default=str(DEFAULT_RECEIPT_ROOT))
    _add_root(r2_prepare)
    prepare_result_parser = sub.add_parser("prepare-result")
    prepare_result_parser.add_argument("--operation-id", required=True)
    prepare_result_parser.add_argument("--task-id", default="")
    prepare_result_parser.add_argument("--receipt-root", default=str(DEFAULT_RECEIPT_ROOT))
    _add_root(prepare_result_parser)
    r2_start = sub.add_parser("r2-start")
    r2_start.add_argument("--task-id", required=True)
    r2_start.add_argument("--declared", action="append", default=[])
    r2_start.add_argument("--task-root", default=str(DEFAULT_TASK_ROOT))
    _add_r2_authorization(r2_start)
    _add_root(r2_start)
    r2_replay = sub.add_parser("r2-replay")
    r2_replay.add_argument("--task-id", required=True)
    r2_replay.add_argument("--declared", action="append", default=[])
    _add_r2_authorization(r2_replay)
    _add_root(r2_replay)
    r2_commit = sub.add_parser("r2-commit")
    r2_commit.add_argument("--task-id", required=True)
    r2_commit.add_argument("--changed", action="append", default=[])
    r2_commit.add_argument("--message", required=True)
    _add_r2_authorization(r2_commit)
    _add_root(r2_commit)
    r2_sync = sub.add_parser("r2-sync")
    r2_sync.add_argument("--task-id", required=True)
    _add_r2_authorization(r2_sync)
    _add_root(r2_sync)
    r2_integrate = sub.add_parser("r2-integrate")
    r2_integrate.add_argument("--task-id", required=True)
    r2_integrate.add_argument("--branch", required=True)
    _add_r2_authorization(r2_integrate)
    _add_root(r2_integrate)
    config_plan_parser = sub.add_parser("config-plan")
    _add_root(config_plan_parser)
    config_parser = sub.add_parser("config-apply")
    config_parser.add_argument("--confirm", default="")
    _add_root(config_parser)
    maintenance_plan_parser = sub.add_parser("maintenance-plan")
    _add_root(maintenance_plan_parser)
    maintenance_parser = sub.add_parser("maintenance")
    maintenance_parser.add_argument("--confirm", default="")
    _add_root(maintenance_parser)
    args = parser.parse_args()
    root = Path(args.root)
    state_root = Path(args.authorization_state_root) if hasattr(args, "authorization_state_root") and args.authorization_state_root else None
    successor = _load_successor_plan(getattr(args, "successor_plan_json", ""))
    if args.command == "snapshot":
        payload = snapshot(root)
    elif args.command == "validate":
        payload = validate(root)
    elif args.command == "start-plan":
        payload = start_plan(args.task_id, root=root, task_root=args.task_root)
    elif args.command == "start":
        payload = start_task(args.task_id, confirm=args.confirm, root=root, task_root=args.task_root)
    elif args.command == "commit-plan":
        payload = commit_plan(args.task_id, args.changed, root=root, message=args.message)
    elif args.command == "commit":
        payload = commit_change_set(args.task_id, args.changed, message=args.message, confirm=args.confirm, root=root)
    elif args.command == "sync-plan":
        payload = sync_plan(root)
    elif args.command == "sync":
        payload = sync_branch(confirm=args.confirm, root=root)
    elif args.command == "integrate-plan":
        payload = integrate_plan(args.branch, root=root)
    elif args.command == "integrate":
        payload = integrate_task(args.branch, confirm=args.confirm, root=root)
    elif args.command == "successor-plan":
        payload = successor_plan(
            args.predecessor, args.declared, validation_receipts=args.validation_receipt, root=root
        )
    elif args.command == "r2-prepare":
        plan = start_plan(args.task_id, root=root)
        if args.emit_assessment:
            from work_git_change_owner_process import assessment_template
            payload = assessment_template(
                thread_id=args.thread_id, repository_id=_repository_id(Path(str(plan.get("source_root") or root))),
                task_id=args.task_id, branch=str(plan.get("branch") or ""), base_head=str(plan.get("base_commit") or ""),
                declared_paths=args.declared,
            )
        elif not args.assessment_json:
            payload = {"ok": False, "reason": "work_git_lifecycle_assessment_required", "next_action": "rerun with --emit-assessment or --assessment-json <file>"}
        else:
            try:
                assessment = json.loads(Path(args.assessment_json).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {"ok": False, "reason": "work_git_lifecycle_assessment_unreadable"}
            else:
                payload = prepare_r2_lifecycle(
                    args.task_id, args.declared, thread_id=args.thread_id, assessment=assessment,
                    rollout_path=args.rollout_path, user_message_ref=args.user_message_ref,
                    operation_id=args.operation_id, workflow_semantic_hash=args.workflow_semantic_hash,
                    successor=successor, root=root, state_root=state_root, receipt_root=args.receipt_root,
                )
        if not args.emit_assessment and not args.assessment_json:
            payload = _record_prepare_result(payload, task_id=args.task_id, operation_id=args.operation_id, receipt_root=args.receipt_root)
        elif not args.emit_assessment and args.assessment_json and not payload.get("prepare_receipt"):
            payload = _record_prepare_result(payload, task_id=args.task_id, operation_id=args.operation_id, receipt_root=args.receipt_root)
        if not args.emit_assessment:
            payload = prepare_terminal_projection(payload, task_id=args.task_id, operation_id=args.operation_id)
    elif args.command == "prepare-result":
        payload = prepare_result(args.operation_id, task_id=args.task_id, receipt_root=args.receipt_root)
    elif args.command == "r2-start":
        payload = start_r2_task(
            args.task_id, args.declared, permit_ref=args.permit_ref, operation_id=args.operation_id,
            workflow_semantic_hash=args.workflow_semantic_hash, root=root,
            task_root=args.task_root, successor=successor, state_root=state_root,
        )
    elif args.command == "r2-replay":
        payload = replay_r2_successor(
            args.task_id, args.declared, successor=successor or {}, permit_ref=args.permit_ref,
            operation_id=args.operation_id, workflow_semantic_hash=args.workflow_semantic_hash,
            root=root, state_root=state_root,
        )
    elif args.command == "r2-commit":
        payload = commit_r2_change_set(
            args.task_id, args.changed, permit_ref=args.permit_ref, operation_id=args.operation_id,
            workflow_semantic_hash=args.workflow_semantic_hash, message=args.message,
            root=root, successor=successor, state_root=state_root,
        )
    elif args.command == "r2-sync":
        payload = sync_r2_branch(
            args.task_id, permit_ref=args.permit_ref, operation_id=args.operation_id,
            workflow_semantic_hash=args.workflow_semantic_hash, root=root, successor=successor, state_root=state_root,
        )
    elif args.command == "r2-integrate":
        payload = integrate_r2_task(
            args.task_id, args.branch, permit_ref=args.permit_ref, operation_id=args.operation_id,
            workflow_semantic_hash=args.workflow_semantic_hash, root=root, successor=successor, state_root=state_root,
        )
    elif args.command == "config-plan":
        payload = config_plan(root)
    elif args.command == "config-apply":
        payload = apply_config(confirm=args.confirm, root=root)
    elif args.command == "maintenance-plan":
        payload = maintenance_plan(root)
    else:
        payload = run_maintenance(confirm=args.confirm, root=root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
