#!/usr/bin/env python3
"""Long-lived WSL workspace lifecycle owner.

This owner manages the declarative work Git repository, its WSL execution
targets, and the Desktop project identity that points Windows UI state at the
WSL Git root. It is deliberately not a mirror publisher, host-runtime importer,
or Windows session owner.

The default commands are read-only.  Bootstrap is an explicit, separately
authorized operation and remains activation-free: it validates or prepares a
declared worktree but never changes the default WSL distribution, imports
Windows runtime state, or activates Codex configuration.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import codex_desktop_model_runtime
import developer_toolchain_owner
import wsl_interop_guard
from codex_wsl_resume_context import (
    WSL_DESKTOP_PROJECT_NAME,
    WSL_DESKTOP_PROJECT_ROOT,
    WSL_WORKSPACE_ROOT,
    ensure_wsl_desktop_project,
)
from platform_paths import host_compatibility_root
from shared.backup_router import create_backup


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DISTRIBUTION = os.environ.get("WSL_DISTRIBUTION") or os.environ.get("WSL_DISTRO_NAME") or "Codex-Wsl-Lab"
DEFAULT_USER = os.environ.get("WSL_USER") or (os.environ.get("USER", "codexlab") if os.name != "nt" else "codexlab")
if os.name == "nt":
    DEFAULT_MIRROR_ROOT = Path(os.environ.get("CODEX_ENV_MIRROR_ROOT", r"C:\Users\45543\codex-env-mirror"))
    DEFAULT_WORKTREE = os.environ.get(
        "WSL_WORKTREE",
        rf"\\wsl.localhost\{DEFAULT_DISTRIBUTION}\home\{DEFAULT_USER}\work\codex-workspace",
    )
    DEFAULT_BARE_REPO = Path(os.environ.get(
        "WSL_BARE_REPO",
        rf"C:\WSL\{DEFAULT_DISTRIBUTION}\git\codex-workspace.git",
    ))
else:
    DEFAULT_MIRROR_ROOT = Path(os.environ.get("CODEX_ENV_MIRROR_ROOT", "/mnt/c/Users/45543/codex-env-mirror"))
    DEFAULT_WORKTREE = os.environ.get("WSL_WORKTREE", str(ROOT.parent))
    DEFAULT_BARE_REPO = Path(os.environ.get(
        "WSL_BARE_REPO",
        f"/mnt/c/WSL/{DEFAULT_DISTRIBUTION}/git/codex-workspace.git",
    ))
SCHEMA = "wsl_workspace_owner.v1"
BOOTSTRAP_CONFIRM = "BOOTSTRAP-WSL-WORKSPACE"
DESKTOP_PROJECT_CONFIRM = "REGISTER-WSL-DESKTOP-PROJECT"
HOST_PROJECTION_CONFIRM = "PROJECT-WSL-HOST-COMPATIBILITY"
INTEROP_GUARD_CONFIRM = wsl_interop_guard.INSTALL_CONFIRM
HOST_PROJECTION_FILES = (
    "_bridge/platform_paths.py",
    "_bridge/codegraph_query_runtime.py",
    "_bridge/local_mcp_hub.py",
    "_bridge/local_mcp_hub_process.py",
    "_bridge/github_hub_client.py",
    "_bridge/rule_governance.py",
)
HOST_PROJECTION_MANIFEST = "_bridge/host_compatibility_projection.json"
DEFAULT_DESKTOP_GLOBAL_STATE = Path(
    os.environ.get(
        "CODEX_DESKTOP_GLOBAL_STATE",
        r"C:\Users\45543\.codex\.codex-global-state.json"
        if os.name == "nt"
        else "/mnt/c/Users/45543/.codex/.codex-global-state.json",
    )
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def host_compatibility_projection_plan(
    *,
    source_root: Path = ROOT,
    target_root: Path | None = None,
) -> dict[str, Any]:
    source = source_root.resolve()
    target = (target_root or host_compatibility_root()).resolve()
    blockers: list[dict[str, str]] = []
    if source == target or target.is_relative_to(source):
        blockers.append({"code": "projection_target_inside_source", "path": str(target)})
    target_git = _run(["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"], timeout=10)
    if target_git.get("ok") and str(target_git.get("stdout") or "").strip().lower() == "true":
        blockers.append({"code": "projection_target_git_worktree_refused", "path": str(target)})
    if not (source / "_bridge").is_dir():
        blockers.append({"code": "projection_source_bridge_missing", "path": str(source / "_bridge")})
    if not (target / "_bridge").is_dir():
        blockers.append({"code": "projection_target_bridge_missing", "path": str(target / "_bridge")})

    rows: list[dict[str, Any]] = []
    expected_files: dict[str, str] = {}
    for relative in HOST_PROJECTION_FILES:
        source_path = source / relative
        target_path = target / relative
        source_hash = _sha256_file(source_path) if source_path.is_file() else ""
        target_hash = _sha256_file(target_path) if target_path.is_file() else ""
        if not source_hash:
            blockers.append({"code": "projection_source_file_missing", "path": str(source_path)})
        expected_files[relative] = source_hash
        rows.append(
            {
                "relative_path": relative,
                "source_path": str(source_path),
                "target_path": str(target_path),
                "source_sha256": source_hash,
                "target_sha256": target_hash,
                "target_exists": target_path.is_file(),
                "current": bool(source_hash and source_hash == target_hash),
            }
        )

    manifest_path = target / HOST_PROJECTION_MANIFEST
    manifest = _read_json_object(manifest_path)
    manifest_current = bool(
        manifest.get("schema") == "wsl_workspace_owner.host_compatibility_projection.v1"
        and manifest.get("owner") == "wsl_workspace_owner"
        and manifest.get("source_root") == str(source)
        and manifest.get("target_root") == str(target)
        and manifest.get("workspace_role") == "windows_only_execution_surface"
        and manifest.get("source_authority") is False
        and manifest.get("reverse_sync_allowed") is False
        and manifest.get("files") == expected_files
    )
    changed_targets = [row["target_path"] for row in rows if not row["current"] and row["target_exists"]]
    if manifest_path.is_file() and not manifest_current:
        changed_targets.append(str(manifest_path))
    would_change = any(not row["current"] for row in rows) or not manifest_current
    return {
        "schema": f"{SCHEMA}.host_compatibility_projection_plan",
        "ok": not blockers,
        "eligible": not blockers,
        "generated_at": now_iso(),
        "source_root": str(source),
        "target_root": str(target),
        "workspace_role": "windows_only_execution_surface",
        "source_authority": False,
        "reverse_sync_allowed": False,
        "files": rows,
        "manifest_path": str(manifest_path),
        "manifest_current": manifest_current,
        "would_change": would_change,
        "backup_targets": changed_targets,
        "blockers": blockers,
        "apply_contract": {
            "confirmation": HOST_PROJECTION_CONFIRM,
            "direction": "wsl_work_git_to_windows_host_projection_only",
            "fixed_allowlist": list(HOST_PROJECTION_FILES),
            "routed_backup_before_overwrite": True,
        },
    }


def host_compatibility_projection_apply(
    *,
    confirm: str,
    source_root: Path = ROOT,
    target_root: Path | None = None,
) -> dict[str, Any]:
    plan = host_compatibility_projection_plan(source_root=source_root, target_root=target_root)
    if confirm != HOST_PROJECTION_CONFIRM:
        return {
            "schema": f"{SCHEMA}.host_compatibility_projection_apply",
            "ok": False,
            "applied": False,
            "reason": f"pass --confirm {HOST_PROJECTION_CONFIRM}",
            "plan": plan,
        }
    if not plan.get("eligible"):
        return {
            "schema": f"{SCHEMA}.host_compatibility_projection_apply",
            "ok": False,
            "applied": False,
            "reason": "projection_not_eligible",
            "plan": plan,
        }
    if not plan.get("would_change"):
        return {
            "schema": f"{SCHEMA}.host_compatibility_projection_apply",
            "ok": True,
            "applied": False,
            "reason": "already_current",
            "plan": plan,
            "backup": {"ok": True, "skipped": "no_overwrite"},
        }

    backup_targets = [str(path) for path in plan.get("backup_targets", [])]
    backup = (
        create_backup(
            backup_targets,
            category="codex-wsl-workspace",
            purpose="before-wsl-work-git-host-compatibility-projection",
            remark="wsl-host-compatibility-projection",
            trigger="wsl_workspace_owner.host_compatibility_projection_apply",
        )
        if backup_targets
        else {"ok": True, "skipped": "targets_missing_no_overwrite"}
    )
    if not backup.get("ok"):
        return {
            "schema": f"{SCHEMA}.host_compatibility_projection_apply",
            "ok": False,
            "applied": False,
            "reason": "projection_backup_failed",
            "plan": plan,
            "backup": backup,
        }

    staged: list[tuple[Path, Path]] = []
    try:
        for row in plan["files"]:
            if row["current"]:
                continue
            source_path = Path(row["source_path"])
            target_path = Path(row["target_path"])
            temporary = target_path.with_name(f".{target_path.name}.projection-{os.getpid()}.tmp")
            shutil.copy2(source_path, temporary)
            staged.append((temporary, target_path))

        if not plan["manifest_current"]:
            manifest_path = Path(plan["manifest_path"])
            manifest_temp = manifest_path.with_name(f".{manifest_path.name}.projection-{os.getpid()}.tmp")
            manifest_payload = {
                "schema": "wsl_workspace_owner.host_compatibility_projection.v1",
                "generated_at": now_iso(),
                "owner": "wsl_workspace_owner",
                "source_root": plan["source_root"],
                "target_root": plan["target_root"],
                "workspace_role": "windows_only_execution_surface",
                "source_authority": False,
                "reverse_sync_allowed": False,
                "files": {row["relative_path"]: row["source_sha256"] for row in plan["files"]},
            }
            manifest_temp.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            staged.append((manifest_temp, manifest_path))
        for temporary, destination in staged:
            temporary.replace(destination)
    except OSError as exc:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
        return {
            "schema": f"{SCHEMA}.host_compatibility_projection_apply",
            "ok": False,
            "applied": False,
            "reason": "projection_write_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "backup": backup,
        }

    after = host_compatibility_projection_plan(source_root=source_root, target_root=target_root)
    return {
        "schema": f"{SCHEMA}.host_compatibility_projection_apply",
        "ok": bool(after.get("ok") and not after.get("would_change")),
        "applied": True,
        "projected_files": [row["relative_path"] for row in plan["files"] if not row["current"]],
        "backup": backup,
        "after": after,
    }


def _path(value: str | Path) -> Path:
    return Path(str(value)).expanduser()


def _inside_wsl() -> bool:
    return os.name != "nt" and bool(
        os.environ.get("WSL_DISTRO_NAME")
        or "microsoft" in platform.release().lower()
    )


def _run(
    argv: list[str],
    *,
    timeout: int = 30,
    cwd: Path | None = None,
    output_limit: int | None = 4000,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd or ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, timeout),
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "error": {"class": type(exc).__name__, "reason": str(exc)},
        }
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": stdout if output_limit is None else stdout[:output_limit],
        "stderr": stderr if output_limit is None else stderr[:output_limit],
    }


def _json_stdout(result: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def desktop_project_snapshot(global_state_path: Path = DEFAULT_DESKTOP_GLOBAL_STATE) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": f"{SCHEMA}.desktop_project_snapshot",
        "ok": False,
        "registered": False,
        "name": WSL_DESKTOP_PROJECT_NAME,
        "desktop_root": WSL_DESKTOP_PROJECT_ROOT,
        "linux_root": WSL_WORKSPACE_ROOT,
        "global_state_path": str(global_state_path),
    }
    if not global_state_path.is_file():
        return {**result, "reason": "desktop_global_state_missing"}
    try:
        state = json.loads(global_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**result, "reason": "desktop_global_state_invalid", "error": type(exc).__name__}
    if not isinstance(state, dict):
        return {**result, "reason": "desktop_global_state_not_object"}

    projected = copy.deepcopy(state)
    projection = ensure_wsl_desktop_project(projected)
    project_id = str(projection.get("project_id") or "")
    projects = state.get("local-projects")
    project = projects.get(project_id) if isinstance(projects, dict) else None
    roots = project.get("rootPaths") if isinstance(project, dict) else None
    registered = bool(
        isinstance(project, dict)
        and project.get("name") == WSL_DESKTOP_PROJECT_NAME
        and roots == [WSL_DESKTOP_PROJECT_ROOT]
        and not projection.get("changed")
    )
    return {
        **result,
        "ok": True,
        "registered": registered,
        "project_id": project_id,
        "project": project if isinstance(project, dict) else None,
        "projection_required": bool(projection.get("changed")),
        "projection_changed_fields": list(projection.get("changed_fields") or [])[:24],
        "reason": "registered" if registered else "desktop_project_projection_required",
    }


def _desktop_project_expression() -> str:
    payload = json.dumps(
        {"root": WSL_DESKTOP_PROJECT_ROOT, "name": WSL_DESKTOP_PROJECT_NAME},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return r"""
(async () => {
  const payload = __PAYLOAD__;
  const bridge = window.electronBridge;
  if (!bridge || typeof bridge.sendMessageFromView !== 'function') {
    return {ok:false, reason:'electron_bridge_unavailable'};
  }
  await bridge.sendMessageFromView({
    type:'electron-update-workspace-root-options',
    roots:[payload.root],
  });
  await new Promise((resolve) => setTimeout(resolve, 300));
  await bridge.sendMessageFromView({
    type:'electron-rename-workspace-root-option',
    root:payload.root,
    label:payload.name,
  });
  await new Promise((resolve) => setTimeout(resolve, 900));
  const bodyText = String(document.body && document.body.innerText || '');
  return {
    ok:true,
    dispatched:true,
    visibleInDom:bodyText.includes(payload.name),
  };
})()
""".replace("__PAYLOAD__", payload)


def desktop_project_apply(
    *,
    confirm: str,
    global_state_path: Path = DEFAULT_DESKTOP_GLOBAL_STATE,
) -> dict[str, Any]:
    if confirm != DESKTOP_PROJECT_CONFIRM:
        return {
            "schema": f"{SCHEMA}.desktop_project_apply",
            "ok": False,
            "status": "blocked",
            "reason": f"pass --confirm {DESKTOP_PROJECT_CONFIRM}",
            "applied": False,
        }
    before = desktop_project_snapshot(global_state_path)
    backup = create_backup(
        [str(global_state_path)],
        category="codex-wsl-workspace",
        purpose="before-live-wsl-desktop-project-registration",
        trigger="wsl_workspace_owner.desktop_project_apply",
        remark="Before live WSL Desktop project registration",
    )
    if not backup.get("ok"):
        return {
            "schema": f"{SCHEMA}.desktop_project_apply",
            "ok": False,
            "status": "blocked",
            "reason": "desktop_global_state_backup_failed",
            "applied": False,
            "backup": backup,
        }

    port, ws_url, pages, reason = codex_desktop_model_runtime._find_codex_page()
    if not ws_url:
        return {
            "schema": f"{SCHEMA}.desktop_project_apply",
            "ok": False,
            "status": "deferred",
            "reason": reason or "desktop_not_running",
            "applied": False,
            "backup": backup,
            "next_action": "restart through the governed launcher so the offline startup projection is loaded",
        }
    client = None
    try:
        client = codex_desktop_model_runtime._CdpClient(ws_url)
        live_result = client.evaluate(_desktop_project_expression())
    except Exception as exc:
        return {
            "schema": f"{SCHEMA}.desktop_project_apply",
            "ok": False,
            "status": "failed",
            "reason": "desktop_project_ipc_failed",
            "error": type(exc).__name__,
            "applied": False,
            "backup": backup,
        }
    finally:
        if client is not None:
            client.close()

    after = desktop_project_snapshot(global_state_path)
    deadline = time.monotonic() + 5.0
    while not after.get("registered") and time.monotonic() < deadline:
        time.sleep(0.2)
        after = desktop_project_snapshot(global_state_path)
    live_result = live_result if isinstance(live_result, dict) else {}
    accepted = bool(after.get("registered") and live_result.get("visibleInDom"))
    return {
        "schema": f"{SCHEMA}.desktop_project_apply",
        "ok": accepted,
        "status": "completed" if accepted else "failed",
        "reason": "desktop_project_registered" if accepted else "desktop_project_acceptance_failed",
        "generated_at": now_iso(),
        "applied": bool(live_result.get("dispatched")),
        "cdp_port": port,
        "page_count": len(pages),
        "live_result": live_result,
        "before": before,
        "after": after,
        "backup": {
            "ok": backup.get("ok"),
            "manifest_paths": backup.get("manifest_paths", []),
        },
    }


def _unc_to_wsl_path(worktree: Path, distribution: str) -> str:
    text = str(worktree).replace("/", "\\")
    prefix = "\\\\wsl.localhost\\" + distribution + "\\"
    if text.lower().startswith(prefix.lower()):
        suffix = text[len(prefix):].replace("\\", "/")
        return "/" + suffix.lstrip("/")
    return str(worktree)


def _windows_to_wsl_path(path: Path, distribution: str) -> str:
    """Map a Windows or WSL UNC path to a Linux path without invoking wslpath."""
    unc = _unc_to_wsl_path(path, distribution)
    if unc != str(path):
        return unc
    text = str(path).replace("\\", "/")
    if len(text) >= 2 and text[1] == ":":
        return f"/mnt/{text[0].lower()}/{text[2:].lstrip('/')}"
    return text


def git_state(worktree: Path, distribution: str, user: str = DEFAULT_USER) -> dict[str, Any]:
    if not worktree.exists():
        return {"available": False, "path": str(worktree), "reason": "worktree_missing"}
    wsl = shutil.which("wsl.exe")
    linux_path = _unc_to_wsl_path(worktree, distribution)
    if os.name == "nt" and wsl and linux_path != str(worktree):
        result = _run([wsl, "-d", distribution, "-u", user, "--", "git", "-c", "safe.directory=" + linux_path, "-C", linux_path, "status", "--porcelain=v1", "--branch"])
    else:
        result = _run(["git", "-c", f"safe.directory={worktree}", "status", "--porcelain=v1", "--branch"], cwd=worktree)
    lines = str(result.get("stdout") or "").splitlines()
    return {
        "available": bool(result.get("ok")),
        "path": str(worktree),
        "branch": lines[0] if lines else "",
        "changes": lines[1:25],
        "change_count": max(0, len(lines) - 1),
        "clean": len(lines) <= 1 if result.get("ok") else False,
        "error": result.get("stderr", "") if not result.get("ok") else "",
    }


def workspace_access_state(worktree: Path) -> dict[str, Any]:
    if not worktree.exists():
        return {"ok": False, "path": str(worktree), "reason": "worktree_missing"}
    git_dir = worktree / ".git"
    stat = worktree.stat()
    return {
        "ok": bool(os.access(worktree, os.R_OK | os.W_OK | os.X_OK) and git_dir.exists() and os.access(git_dir, os.R_OK | os.W_OK | os.X_OK)),
        "path": str(worktree),
        "owner_uid": getattr(stat, "st_uid", None),
        "current_uid": os.getuid() if hasattr(os, "getuid") else None,
        "worktree_readable": os.access(worktree, os.R_OK),
        "worktree_writable": os.access(worktree, os.W_OK),
        "git_writable": git_dir.exists() and os.access(git_dir, os.W_OK),
        "daily_runtime_user": DEFAULT_USER,
        "root_required_for_daily_work": False,
    }


def _wsl_git(args: list[str], distribution: str, user: str = DEFAULT_USER, *, timeout: int = 30) -> dict[str, Any]:
    if _inside_wsl():
        return _run(["git", *args], timeout=timeout)
    wsl = shutil.which("wsl.exe")
    if not wsl:
        return {"ok": False, "stderr": "wsl_executable_missing", "stdout": ""}
    return _run([wsl, "-d", distribution, "-u", user, "--", "git", *args], timeout=timeout)


def _git_value(args: list[str], distribution: str, user: str = DEFAULT_USER) -> str:
    result = _wsl_git(args, distribution, user)
    return str(result.get("stdout") or "").strip()


def _safe_wsl_git(args: list[str], safe_path: str, distribution: str, user: str = DEFAULT_USER) -> dict[str, Any]:
    return _wsl_git(["-c", f"safe.directory={safe_path}", *args], distribution, user)


def _safe_git_value(args: list[str], safe_path: str, distribution: str, user: str = DEFAULT_USER) -> str:
    result = _safe_wsl_git(args, safe_path, distribution, user)
    return str(result.get("stdout") or "").strip()


def work_git_state(worktree: Path, bare_repo: Path, distribution: str, user: str = DEFAULT_USER) -> dict[str, Any]:
    """Return the publish boundary between the WSL worktree and bare Work Git."""
    worktree_path = _unc_to_wsl_path(worktree, distribution)
    bare_path = _windows_to_wsl_path(bare_repo, distribution)
    result: dict[str, Any] = {
        "schema": f"{SCHEMA}.work_git_state",
        "authority": "wsl_worktree_source_with_windows_bare_history_store",
        "worktree": str(worktree),
        "bare_repo": str(bare_repo),
        "worktree_linux_path": worktree_path,
        "bare_repo_linux_path": bare_path,
        "wsl_user": user,
        "available": False,
        "release_ready": False,
        "issues": [],
    }
    if not worktree.exists():
        result["issues"].append({"severity": "risk", "code": "worktree_missing", "next_action": "clone_or_attach_work_git"})
        return result
    if not bare_repo.exists():
        result["issues"].append({"severity": "risk", "code": "bare_repo_missing", "next_action": "create_or_attach_bare_work_git"})
        return result

    bare_check = _safe_wsl_git(["--git-dir", bare_path, "rev-parse", "--is-bare-repository"], bare_path, distribution, user)
    if not bare_check.get("ok") or str(bare_check.get("stdout") or "").strip().lower() != "true":
        result["issues"].append({"severity": "risk", "code": "bare_repo_not_bare_or_unreadable", "detail": str(bare_check.get("stderr") or "")[:500]})
        return result

    branch = _safe_git_value(["-C", worktree_path, "rev-parse", "--abbrev-ref", "HEAD"], worktree_path, distribution, user)
    work_head = _safe_git_value(["-C", worktree_path, "rev-parse", "HEAD"], worktree_path, distribution, user)
    bare_head = _safe_git_value(["--git-dir", bare_path, "rev-parse", f"refs/heads/{branch}"], bare_path, distribution, user) if branch and branch != "HEAD" else ""
    status = git_state(worktree, distribution, user)
    result.update({
        "available": bool(branch and work_head and bare_head),
        "branch": branch,
        "worktree_head": work_head,
        "bare_head": bare_head,
        "clean": bool(status.get("clean")),
        "change_count": status.get("change_count", 0),
        "status": status,
    })
    if not branch or branch == "HEAD":
        result["issues"].append({"severity": "risk", "code": "worktree_detached_head", "next_action": "checkout_named_work_git_branch"})
    if not status.get("clean"):
        result["issues"].append({"severity": "risk", "code": "worktree_dirty", "change_count": status.get("change_count", 0), "next_action": "review_and_commit_or_discard_changes_before_mirror_publish"})
    if not bare_head:
        result["issues"].append({"severity": "risk", "code": "bare_branch_missing", "branch": branch, "next_action": "push_named_branch_to_local_bare_repo"})
    elif work_head != bare_head:
        result["issues"].append({"severity": "risk", "code": "worktree_bare_head_mismatch", "worktree_head": work_head, "bare_head": bare_head, "next_action": "synchronize_worktree_and_bare_repo_before_mirror_publish"})
    result["release_ready"] = bool(result["available"] and result["clean"] and not result["issues"])
    result["direction"] = "wsl_worktree_to_bare_repo_to_validated_mirror"
    result["reverse_overwrite_blocked"] = True
    return result


def wsl_interop_state(distribution: str, user: str = DEFAULT_USER) -> dict[str, Any]:
    entry = Path("/proc/sys/fs/binfmt_misc/WSLInterop")
    if _inside_wsl():
        text = entry.read_text(encoding="utf-8", errors="replace") if entry.exists() else ""
        probe = _run(["/mnt/c/Windows/System32/cmd.exe", "/d", "/c", "exit", "0"], timeout=10)
    else:
        wsl = shutil.which("wsl.exe")
        if not wsl:
            return {
                "present": False,
                "enabled": False,
                "interpreter": "",
                "probe_ok": False,
                "error": "wsl_executable_missing",
            }
        probe = _run(
            [
                wsl,
                "-d",
                distribution,
                "-u",
                user,
                "--",
                "sh",
                "-lc",
                "test -e /proc/sys/fs/binfmt_misc/WSLInterop && "
                "cat /proc/sys/fs/binfmt_misc/WSLInterop && "
                "/mnt/c/Windows/System32/cmd.exe /d /c exit 0",
            ],
            timeout=15,
        )
        text = str(probe.get("stdout") or "").replace("\x00", "")
    interpreter = ""
    for line in text.splitlines():
        if line.startswith("interpreter "):
            interpreter = line.split(" ", 1)[1].strip()
            break
    present = entry.exists() if _inside_wsl() else bool(interpreter)
    return {
        "present": present,
        "enabled": present and "enabled" in text,
        "interpreter": interpreter,
        "probe_ok": bool(probe.get("ok")),
        "error": str(probe.get("stderr") or "")[:500] if not probe.get("ok") else "",
    }


def interop_guard_state(distribution: str, user: str = DEFAULT_USER) -> dict[str, Any]:
    return wsl_interop_guard.state(distribution, user)


def interop_guard_plan(distribution: str, user: str = DEFAULT_USER) -> dict[str, Any]:
    return wsl_interop_guard.plan(distribution, user)


def interop_guard_apply(
    confirm: str,
    distribution: str,
    user: str = DEFAULT_USER,
    *,
    timeout: int = 90,
) -> dict[str, Any]:
    return wsl_interop_guard.apply(confirm, distribution, user, timeout=timeout)


def wsl_state(distribution: str, user: str = DEFAULT_USER) -> dict[str, Any]:
    if _inside_wsl():
        current = os.environ.get("WSL_DISTRO_NAME") or distribution
        return {
            "available": True,
            "distribution": distribution,
            "present": current == distribution,
            "running": True,
            "known_distributions": [current],
            "error": "" if current == distribution else f"running_in:{current}",
            "default_switch_allowed": False,
            "interop": wsl_interop_state(distribution, user),
        }
    wsl = shutil.which("wsl.exe")
    if not wsl:
        return {"available": False, "distribution": distribution, "reason": "wsl_executable_missing"}
    result = _run([wsl, "--list", "--quiet"], timeout=15)
    names = [line.strip().replace("\x00", "") for line in str(result.get("stdout") or "").splitlines() if line.strip()]
    present = distribution in names
    return {
        "available": bool(result.get("ok")),
        "distribution": distribution,
        "present": present,
        "running": False,
        "known_distributions": names[:32],
        "error": result.get("stderr", "") if not result.get("ok") else "",
        "default_switch_allowed": False,
        "interop": wsl_interop_state(distribution, user) if present else {
            "present": False,
            "enabled": False,
            "interpreter": "",
            "probe_ok": False,
            "error": "distribution_not_present",
        },
    }


def _common(args: argparse.Namespace) -> dict[str, Any]:
    distribution = str(args.distribution or DEFAULT_DISTRIBUTION)
    user = str(args.user or DEFAULT_USER)
    worktree = _path(args.worktree or DEFAULT_WORKTREE)
    bare_repo = _path(args.bare_repo or DEFAULT_BARE_REPO)
    mirror_root = _path(args.mirror_root or DEFAULT_MIRROR_ROOT)
    return {
        "distribution": distribution,
        "user": user,
        "worktree": worktree,
        "bare_repo": bare_repo,
        "mirror_root": mirror_root,
    }


def snapshot(args: argparse.Namespace) -> dict[str, Any]:
    paths = _common(args)
    desktop_project = desktop_project_snapshot()
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": True,
        "generated_at": now_iso(),
        "owner": "wsl_workspace",
        "lifecycle": "active",
        "authority": "local declarative work Git repository",
        "source_mirror": str(paths["mirror_root"]),
        "paths": {key: str(value) for key, value in paths.items()},
        "platform": platform.system(),
        "wsl": wsl_state(paths["distribution"], paths["user"]),
        "interop_guard": interop_guard_state(paths["distribution"], paths["user"]),
        "git": git_state(paths["worktree"], paths["distribution"], paths["user"]),
        "workspace_access": workspace_access_state(paths["worktree"]),
        "work_git": work_git_state(paths["worktree"], paths["bare_repo"], paths["distribution"], paths["user"]),
        "desktop_project": desktop_project,
        "developer_toolchain": developer_toolchain_owner.snapshot(),
        "activation_performed": False,
        "host_runtime_imported": False,
        "default_distribution_change": False,
        "scope": {
            "long_lived_member": True,
            "long_lived_production_workspace": True,
            "primary_execution_target": True,
            "isolated_wsl_is_execution_target_only": True,
            "mirror_is_recovery_and_release_product": True,
            "work_git_is_daily_authority": True,
            "bare_repo_is_work_git_storage": True,
            "mirror_is_derived_release_product": True,
            "mirror_accepts_only_validated_work_git": True,
            "windows_native_workspace_source_role": "retired",
            "windows_native_workspace_is_source_authority": False,
            "windows_host_compatibility_projection_retained": True,
            "windows_host_compatibility_projection_reverse_sync": False,
            "desktop_project_uses_windows_unc_for_wsl_git_root": True,
        },
    }


def plan(args: argparse.Namespace) -> dict[str, Any]:
    state = snapshot(args)
    blockers: list[dict[str, Any]] = []
    if not state["wsl"].get("present"):
        blockers.append({"code": "distribution_not_provisioned", "distribution": state["wsl"].get("distribution"), "next_action": "provision_in_isolated_target_only"})
    if not state["git"].get("available"):
        blockers.append({"code": "worktree_not_available", "path": state["git"].get("path"), "next_action": "clone_or_attach_declared_work_git"})
    blockers.extend(item for item in state.get("work_git", {}).get("issues", []) if item.get("severity") == "risk")
    if not state.get("developer_toolchain", {}).get("ok"):
        blockers.append({
            "code": "developer_toolchain_incomplete",
            "missing": state.get("developer_toolchain", {}).get("missing_required", []),
            "next_action": f"python _bridge/developer_toolchain_owner.py apply --confirm {developer_toolchain_owner.INSTALL_CONFIRM}",
        })
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": not blockers,
        "generated_at": now_iso(),
        "owner": "wsl_workspace",
        "operation": "workspace_lifecycle",
        "blockers": blockers,
        "steps": [
            "verify or provision the declared non-default WSL target",
            "clone or attach the local declarative work Git repository",
            "set platform path variables for the worktree",
            "generate platform-specific Codex/MCP projections",
            "run bootstrap, owner validators, and smoke tests",
            "install or validate the version-locked user-local developer toolchain",
            "produce a handoff receipt without activating host runtime",
        ],
        "authority_flow": "one-time bootstrap: codex-env-mirror -> work Git; normal operation: WSL worktree -> Windows bare Git -> owner validation -> closeout -> mirror candidate",
        "safety": {
            "default_distribution_change": False,
            "host_runtime_import": False,
            "codex_activation": False,
            "shared_writable_state": False,
            "mirror_reverse_overwrite": False,
        },
        "snapshot": state,
    }


def _bootstrap_command(args: argparse.Namespace) -> list[str]:
    paths = _common(args)
    script = paths["worktree"] / "workspace" / "_bridge" / "bootstrap_wsl_workspace.py"
    linux_root = _unc_to_wsl_path(paths["worktree"], paths["distribution"])
    linux_script = f"{linux_root}/workspace/_bridge/bootstrap_wsl_workspace.py" if linux_root.startswith("/") else str(script)
    if os.name == "nt" and shutil.which("wsl.exe") and linux_root.startswith("/"):
        command = ["wsl.exe", "-d", paths["distribution"], "-u", paths["user"], "--", "python3", linux_script, "--root", linux_root, "--json", "--write-receipt"]
    else:
        command = ["python3", str(script), "--root", str(paths["worktree"]), "--json", "--write-receipt"]
    if args.receipt:
        command.extend(["--receipt", str(_path(args.receipt))])
    return command


def bootstrap(args: argparse.Namespace) -> dict[str, Any]:
    if str(args.confirm or "") != BOOTSTRAP_CONFIRM:
        return {
            "schema": f"{SCHEMA}.bootstrap",
            "ok": False,
            "status": "blocked",
            "generated_at": now_iso(),
            "error": {"class": "explicit_confirmation_required", "reason": f"pass --confirm {BOOTSTRAP_CONFIRM}"},
            "activation_performed": False,
            "host_runtime_imported": False,
        }
    command = _bootstrap_command(args)
    result = _run(command, timeout=int(args.timeout or 300))
    payload = _json_stdout(result)
    return {
        "schema": f"{SCHEMA}.bootstrap",
        "ok": bool(result.get("ok") and payload.get("ok", True)),
        "status": "completed" if result.get("ok") and payload.get("ok", True) else "failed",
        "generated_at": now_iso(),
        "command": command,
        "validation": payload or {"stderr": result.get("stderr", ""), "returncode": result.get("returncode")},
        "activation_performed": False,
        "host_runtime_imported": False,
        "default_distribution_change": False,
        "next_action": "handoff" if result.get("ok") else "inspect_validation_rows",
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    state = snapshot(args)
    issues: list[dict[str, Any]] = []
    if not state["wsl"].get("present"):
        issues.append({"severity": "advisory", "code": "distribution_not_provisioned", "next_action": "use an explicit isolated target"})
    interop = state["wsl"].get("interop") or {}
    if state["wsl"].get("present") and not interop.get("probe_ok"):
        issues.append({
            "severity": "risk",
            "code": "wsl_interop_unavailable",
            "detail": interop.get("error", ""),
            "next_action": "repair the WSLInterop registration before starting required Windows-backed MCP servers",
        })
    interop_guard = state.get("interop_guard") or {}
    if state["wsl"].get("present") and not interop_guard.get("ready"):
        issues.append({
            "severity": "risk",
            "code": "wsl_interop_guard_not_ready",
            "detail": {
                "files_current": bool(interop_guard.get("files_current")),
                "timer_enabled": bool(interop_guard.get("timer_enabled")),
                "timer_active": bool(interop_guard.get("timer_active")),
            },
            "next_action": f"run interop-guard-apply --confirm {INTEROP_GUARD_CONFIRM}",
        })
    if not state["git"].get("available"):
        issues.append({"severity": "risk", "code": "worktree_not_available", "next_action": "clone or attach the work Git repository"})
    if not state.get("workspace_access", {}).get("ok"):
        issues.append({
            "severity": "risk",
            "code": "worktree_not_owned_or_writable_by_runtime_user",
            "detail": state.get("workspace_access", {}),
            "next_action": "restore Work Git and .git ownership to the declared non-root runtime user",
        })
    work_git = state.get("work_git", {})
    for issue in work_git.get("issues", []):
        issues.append(issue)
    toolchain = state.get("developer_toolchain") if isinstance(state.get("developer_toolchain"), dict) else None
    if toolchain is not None and not toolchain.get("ok"):
        issues.append({
            "severity": "risk",
            "code": "developer_toolchain_incomplete",
            "missing": toolchain.get("missing_required", []),
            "next_action": f"python _bridge/developer_toolchain_owner.py apply --confirm {developer_toolchain_owner.INSTALL_CONFIRM}",
        })
    desktop_project = state.get("desktop_project", {})
    if desktop_project.get("ok") and not desktop_project.get("registered"):
        issues.append({
            "severity": "risk",
            "code": "wsl_desktop_project_not_registered",
            "detail": desktop_project.get("projection_changed_fields", []),
            "next_action": f"run desktop-project-apply --confirm {DESKTOP_PROJECT_CONFIRM} while Desktop is running, or restart through the governed launcher",
        })
    elif not desktop_project.get("ok"):
        issues.append({
            "severity": "advisory",
            "code": "desktop_project_state_unavailable",
            "detail": desktop_project.get("reason", ""),
            "next_action": "restore or initialize Desktop global state before project registration",
        })
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "status": "ok" if not any(item.get("severity") == "risk" for item in issues) else "risk",
        "generated_at": now_iso(),
        "issues": issues,
        "snapshot": state,
        "acceptance": {
            "long_lived_member": True,
            "work_git_authority": True,
            "mirror_is_recovery_and_release_product": True,
            "bare_repo_is_work_git_storage": True,
            "mirror_is_derived_release_product": True,
            "mirror_publish_requires_release_ready_work_git": True,
            "no_default_distribution_switch": True,
            "no_host_runtime_import": True,
            "desktop_project_registered": bool(desktop_project.get("registered")),
            "desktop_project_root_is_windows_unc": str(desktop_project.get("desktop_root") or "").startswith("\\\\wsl.localhost\\"),
            "developer_toolchain_ready": bool(toolchain and toolchain.get("ok")),
            "interop_guard_ready": bool(interop_guard.get("ready")),
        },
        "release_gate": {
            "release_ready": bool(work_git.get("release_ready")),
            "blocked_by": [item for item in work_git.get("issues", []) if item.get("severity") == "risk"],
            "direction": "wsl_worktree -> windows_bare_git -> codex_env_mirror",
            "reverse_overwrite_blocked": True,
        },
    }


def handoff(args: argparse.Namespace) -> dict[str, Any]:
    state = validate(args)
    return {
        "schema": f"{SCHEMA}.handoff",
        "ok": bool(state.get("ok")),
        "status": "completed" if state.get("ok") else "blocked",
        "generated_at": now_iso(),
        "owner": "wsl_workspace",
        "operation": "handoff",
        "target_distribution": state["snapshot"].get("wsl", {}).get("distribution", ""),
        "worktree": state["snapshot"].get("git", {}).get("path", ""),
        "source_snapshot": {"mirror_root": state["snapshot"].get("source_mirror", "")},
        "activation_performed": False,
        "host_runtime_imported": False,
        "validation_rows": state.get("issues", []),
        "work_git": state.get("snapshot", {}).get("work_git", {}),
        "rollback_reference": "owner-native cleanup-plan; no activation to roll back",
        "next_action": "closeout" if state.get("ok") else "resolve_validation_rows",
    }


def _delegate_mirror_export_to_wsl(args: argparse.Namespace, kind: str) -> dict[str, Any]:
    paths = _common(args)
    wsl = shutil.which("wsl.exe")
    linux_root = _unc_to_wsl_path(paths["worktree"], paths["distribution"])
    if not wsl or not linux_root.startswith("/"):
        return {
            "schema": f"{SCHEMA}.mirror_export.delegate.v1",
            "ok": False,
            "status": "blocked",
            "reason": "wsl_export_runtime_unavailable",
            "distribution": paths["distribution"],
            "worktree": str(paths["worktree"]),
        }
    linux_script = f"{linux_root}/workspace/_bridge/wsl_workspace_owner.py"
    command = [
        wsl,
        "-d",
        paths["distribution"],
        "-u",
        paths["user"],
        "--",
        "/usr/bin/env",
        f"PATH={_wsl_export_path(paths['distribution'], paths['user'])}",
        "python3",
        linux_script,
        "--distribution",
        paths["distribution"],
        "--user",
        paths["user"],
        "--worktree",
        linux_root,
        "--bare-repo",
        _windows_to_wsl_path(paths["bare_repo"], paths["distribution"]),
        "--mirror-root",
        _windows_to_wsl_path(paths["mirror_root"], paths["distribution"]),
        "mirror-export",
        "--kind",
        kind,
    ]
    operation = _run(command, timeout=int(getattr(args, "timeout", 300) or 300), output_limit=None)
    payload = _json_stdout(operation)
    if payload:
        return payload
    return {
        "schema": f"{SCHEMA}.mirror_export.delegate.v1",
        "ok": False,
        "status": "failed",
        "reason": "wsl_export_failed",
        "distribution": paths["distribution"],
        "returncode": operation.get("returncode"),
        "stderr": str(operation.get("stderr") or "")[:2000],
    }


def _wsl_export_path(
    distribution: str,
    user: str,
    *,
    runtime_root: Path | None = None,
) -> str:
    entries = [f"/home/{user}/.local/bin"]
    root = runtime_root or Path(
        os.environ.get("CODEX_WSL_RUNTIME_BIN_ROOT")
        or Path.home() / ".codex" / "bin" / "wsl"
    )
    if root.is_dir():
        for candidate in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
            if candidate.is_dir() and (candidate / "rg").is_file():
                entries.append(_windows_to_wsl_path(candidate, distribution))
    entries.extend(["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"])
    return ":".join(dict.fromkeys(entries))


def mirror_export(args: argparse.Namespace) -> dict[str, Any]:
    """Emit a read-only, reproducible projection for mirror capture.

    This is deliberately separate from ``bootstrap``. Mirror generation may
    record the long-lived WSL member, but it must never start WSL, activate the
    worktree, import host runtime state, or modify Codex configuration.
    """
    kind = str(args.kind or "").strip().lower()
    if os.name == "nt":
        return _delegate_mirror_export_to_wsl(args, kind)
    if kind == "bootstrap":
        state = validate(args)
        return {
            "schema": f"{SCHEMA}.mirror_export.bootstrap.v1",
            "ok": bool(state.get("ok")),
            "status": "completed" if state.get("ok") else "blocked",
            "generated_at": now_iso(),
            "owner": "wsl_workspace",
            "lifecycle": "active",
            "workspace_role": "long_lived_production_workspace",
            "authority": "local declarative work Git repository",
            "export_kind": "bootstrap_validation",
            "validation": state,
            "activation_performed": False,
            "host_runtime_imported": False,
            "default_distribution_change": False,
            "mirror_reverse_overwrite": False,
        }
    if kind == "handoff":
        payload = handoff(args)
        payload.update({
            "schema": f"{SCHEMA}.mirror_export.handoff.v1",
            "workspace_role": "long_lived_production_workspace",
            "export_kind": "handoff",
            "mirror_reverse_overwrite": False,
        })
        return payload
    if kind == "work-git-release":
        state = snapshot(args)
        work_git = state.get("work_git", {})
        return {
            "schema": f"{SCHEMA}.mirror_export.work_git_release.v1",
            "ok": bool(work_git.get("release_ready")),
            "status": "completed" if work_git.get("release_ready") else "blocked",
            "generated_at": now_iso(),
            "owner": "wsl_workspace",
            "workspace_role": "long_lived_production_workspace",
            "authority": "validated WSL worktree backed by Windows bare Git",
            "export_kind": "work_git_release_candidate",
            "work_git": work_git,
            "mirror_role": "derived_recovery_and_release_product",
            "direction": "wsl_worktree -> windows_bare_git -> codex_env_mirror",
            "mirror_reverse_overwrite": False,
            "windows_native_workspace_source_role": "retired",
            "windows_host_compatibility_projection_role": "windows_only_execution_surface",
        }
    if kind == "desktop-project-registration":
        state = snapshot(args)
        desktop_project = state.get("desktop_project", {})
        registered = bool(desktop_project.get("ok") and desktop_project.get("registered"))
        return {
            "schema": f"{SCHEMA}.mirror_export.desktop_project_registration.v1",
            "ok": registered,
            "status": "completed" if registered else "blocked",
            "generated_at": now_iso(),
            "owner": "wsl_workspace",
            "export_kind": "desktop_project_registration",
            "registration_method": "desktop_ipc",
            "project_id": desktop_project.get("project_id", ""),
            "name": desktop_project.get("name", ""),
            "desktop_root": desktop_project.get("desktop_root", ""),
            "linux_root": desktop_project.get("linux_root", ""),
            "registered": registered,
            "reason": desktop_project.get("reason", ""),
            "work_git_authority": True,
            "activation_performed": False,
            "host_runtime_imported": False,
            "mirror_reverse_overwrite": False,
        }
    return {
        "schema": f"{SCHEMA}.mirror_export.v1",
        "ok": False,
        "status": "blocked",
        "generated_at": now_iso(),
        "error": {
            "class": "invalid_export_kind",
            "allowed": ["bootstrap", "handoff", "work-git-release", "desktop-project-registration"],
        },
        "activation_performed": False,
        "host_runtime_imported": False,
        "default_distribution_change": False,
    }


def cleanup_plan(args: argparse.Namespace) -> dict[str, Any]:
    state = snapshot(args)
    return {
        "schema": f"{SCHEMA}.cleanup_plan",
        "ok": True,
        "generated_at": now_iso(),
        "read_only": True,
        "targets": [
            {"path": str(state["paths"]["worktree"]), "action": "remove only after explicit owner approval", "protected": True},
            {"path": str(state["paths"]["bare_repo"]), "action": "retain as the durable same-history backing store unless explicitly retired", "protected": True},
        ],
        "never_remove_automatically": ["default WSL distribution", "Windows Codex home", "mirror repository", "host runtime databases", "shared writable caches"],
        "next_action": "review target-specific cleanup before any destructive command",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Long-lived WSL workspace lifecycle owner")
    parser.add_argument("--distribution", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--worktree", default="")
    parser.add_argument("--bare-repo", default="")
    parser.add_argument("--mirror-root", default="")
    parser.add_argument("--receipt", default="")
    parser.add_argument("--timeout", type=int, default=300)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("status")
    sub.add_parser("plan")
    sub.add_parser("validate")
    sub.add_parser("handoff")
    sub.add_parser("cleanup-plan")
    sub.add_parser("desktop-project-status")
    desktop_project = sub.add_parser("desktop-project-apply")
    desktop_project.add_argument("--confirm", default="")
    sub.add_parser("host-projection-plan")
    host_projection = sub.add_parser("host-projection-apply")
    host_projection.add_argument("--confirm", default="")
    sub.add_parser("interop-guard-plan")
    interop_guard_parser = sub.add_parser("interop-guard-apply")
    interop_guard_parser.add_argument("--confirm", default="")
    export = sub.add_parser("mirror-export")
    export.add_argument(
        "--kind",
        choices=("bootstrap", "handoff", "work-git-release", "desktop-project-registration"),
        required=True,
    )
    boot = sub.add_parser("bootstrap")
    boot.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if args.command in {"snapshot", "status"}:
        payload = snapshot(args)
    elif args.command == "plan":
        payload = plan(args)
    elif args.command == "validate":
        payload = validate(args)
    elif args.command == "interop-guard-plan":
        paths = _common(args)
        payload = interop_guard_plan(paths["distribution"], paths["user"])
    elif args.command == "interop-guard-apply":
        paths = _common(args)
        payload = interop_guard_apply(
            args.confirm,
            paths["distribution"],
            paths["user"],
            timeout=int(args.timeout or 90),
        )
    elif args.command == "handoff":
        payload = handoff(args)
    elif args.command == "cleanup-plan":
        payload = cleanup_plan(args)
    elif args.command == "desktop-project-status":
        payload = desktop_project_snapshot()
    elif args.command == "desktop-project-apply":
        payload = desktop_project_apply(confirm=str(args.confirm or ""))
    elif args.command == "host-projection-plan":
        payload = host_compatibility_projection_plan()
    elif args.command == "host-projection-apply":
        payload = host_compatibility_projection_apply(confirm=str(args.confirm or ""))
    elif args.command == "mirror-export":
        payload = mirror_export(args)
    else:
        payload = bootstrap(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
