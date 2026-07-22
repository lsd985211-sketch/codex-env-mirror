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
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=text,
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
    if main_staged:
        blockers.append({"code": "main_has_staged_changes", "paths": main_staged})
    if overlap:
        blockers.append({"code": "task_changes_overlap_dirty_main", "paths": overlap})
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
    merged = _git(main_root, "merge", "--ff-only", str(plan["branch"]), timeout=120)
    synchronized = sync_branch(confirm=SYNC_CONFIRM, root=main_root, receipt_root=receipt_root) if merged.get("ok") else {}
    after = snapshot(main_root)
    result = {
        "schema": "work_git_change_owner.integrate.v1",
        "ok": bool(merged.get("ok") and synchronized.get("ok")),
        "status": "completed" if merged.get("ok") and synchronized.get("ok") else "failed",
        "generated_at": now_iso(),
        "plan": plan,
        "merge": {"returncode": merged.get("returncode"), "stderr": str(merged.get("stderr") or "")[-1200:]},
        "sync": synchronized,
        "after": after,
        "branch_deleted": False,
        "worktree_removed": False,
    }
    result["receipt"] = _write_receipt(receipt_root, f"integrate-{str(plan['branch']).replace('/', '-')}", result)
    return result


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
