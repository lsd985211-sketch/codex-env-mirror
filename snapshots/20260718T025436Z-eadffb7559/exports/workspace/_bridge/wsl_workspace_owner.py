#!/usr/bin/env python3
"""Long-lived WSL workspace lifecycle owner.

This owner manages the declarative work Git repository and its WSL execution
targets as a reusable production work-environment capability. It is
deliberately not a mirror publisher, host-runtime importer, or Windows session
owner.

The default commands are read-only.  Bootstrap is an explicit, separately
authorized operation and remains activation-free: it validates or prepares a
declared worktree but never changes the default WSL distribution, imports
Windows runtime state, or activates Codex configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(value: str | Path) -> Path:
    return Path(str(value)).expanduser()


def _inside_wsl() -> bool:
    return os.name != "nt" and bool(
        os.environ.get("WSL_DISTRO_NAME")
        or "microsoft" in platform.release().lower()
    )


def _run(argv: list[str], *, timeout: int = 30, cwd: Path | None = None) -> dict[str, Any]:
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
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip()[:4000],
        "stderr": result.stderr.strip()[:4000],
    }


def _json_stdout(result: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


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
        "authority": "wsl_worktree_and_windows_bare_git",
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


def wsl_state(distribution: str) -> dict[str, Any]:
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
        "wsl": wsl_state(paths["distribution"]),
        "git": git_state(paths["worktree"], paths["distribution"], paths["user"]),
        "work_git": work_git_state(paths["worktree"], paths["bare_repo"], paths["distribution"], paths["user"]),
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
            "native_workspace_is_transition_source_only": True,
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
    if not state["git"].get("available"):
        issues.append({"severity": "risk", "code": "worktree_not_available", "next_action": "clone or attach the work Git repository"})
    work_git = state.get("work_git", {})
    for issue in work_git.get("issues", []):
        issues.append(issue)
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


def mirror_export(args: argparse.Namespace) -> dict[str, Any]:
    """Emit a read-only, reproducible projection for mirror capture.

    This is deliberately separate from ``bootstrap``. Mirror generation may
    record the long-lived WSL member, but it must never start WSL, activate the
    worktree, import host runtime state, or modify Codex configuration.
    """
    kind = str(args.kind or "").strip().lower()
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
            "native_workspace_role": "transition_source_only",
        }
    return {
        "schema": f"{SCHEMA}.mirror_export.v1",
        "ok": False,
        "status": "blocked",
        "generated_at": now_iso(),
        "error": {"class": "invalid_export_kind", "allowed": ["bootstrap", "handoff", "work-git-release"]},
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
            {"path": str(state["paths"]["bare_repo"]), "action": "retain as local Git authority unless explicitly retired", "protected": True},
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
    export = sub.add_parser("mirror-export")
    export.add_argument("--kind", choices=("bootstrap", "handoff", "work-git-release"), required=True)
    boot = sub.add_parser("bootstrap")
    boot.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if args.command in {"snapshot", "status"}:
        payload = snapshot(args)
    elif args.command == "plan":
        payload = plan(args)
    elif args.command == "validate":
        payload = validate(args)
    elif args.command == "handoff":
        payload = handoff(args)
    elif args.command == "cleanup-plan":
        payload = cleanup_plan(args)
    elif args.command == "mirror-export":
        payload = mirror_export(args)
    else:
        payload = bootstrap(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
