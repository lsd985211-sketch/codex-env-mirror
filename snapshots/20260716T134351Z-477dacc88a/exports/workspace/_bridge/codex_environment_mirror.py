#!/usr/bin/env python3
"""Unified owner adapter for the external Codex recovery mirror.

The external mirror CLI remains the implementation authority. This adapter
standardizes lifecycle commands, confirmations, receipts, retention, and Git
commit behavior for the workspace workflow facade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from shared.process_liveness import process_is_alive as _shared_process_is_alive
except ModuleNotFoundError:
    from _bridge.shared.process_liveness import process_is_alive as _shared_process_is_alive


REFRESH_CONFIRMATION = "REFRESH-CODEX-MIRROR"
PUBLISH_CONFIRMATION = "PUBLISH-CODEX-MIRROR"
STAGE_CONFIRMATION = "STAGE-RESTORE"
INLINE_SAMPLE_LIMIT = 5
INLINE_FAILURE_BYTES = 12 * 1024
REFRESH_MAX_ATTEMPTS = 3
REFRESH_RETRY_BASE_SECONDS = 0.25
RETRYABLE_VALIDATION_ISSUES = frozenset({"source_assets_changed", "generated_source_changed"})


class MirrorOperationBusy(RuntimeError):
    def __init__(self, lock_path: Path, owner: dict[str, Any]) -> None:
        super().__init__("mirror_operation_busy")
        self.lock_path = lock_path
        self.owner = owner


def process_is_alive(pid: int) -> bool:
    return _shared_process_is_alive(pid)


def read_lock_owner(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


@contextmanager
def exclusive_operation_lock(path: Path, operation: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{time.time_ns()}"
    owner = {"pid": os.getpid(), "operation": operation, "started_at": now_iso(), "token": token}
    for _ in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = read_lock_owner(path)
            if not process_is_alive(int(existing.get("pid") or 0)):
                path.unlink(missing_ok=True)
                continue
            raise MirrorOperationBusy(path, existing)
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(owner, handle, ensure_ascii=False)
                handle.write("\n")
            break
    else:
        raise MirrorOperationBusy(path, read_lock_owner(path))
    try:
        yield owner
    finally:
        current = read_lock_owner(path)
        if current.get("token") == token:
            path.unlink(missing_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mirror_root() -> Path:
    configured = os.environ.get("CODEX_ENV_MIRROR_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else (Path.home() / "codex-env-mirror").resolve()


def mirror_cli() -> Path:
    return mirror_root() / "scripts" / "mirror_cli.py"


def runtime_root() -> Path:
    configured = os.environ.get("CODEX_ENV_MIRROR_RUNTIME_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent / "runtime" / "codex_environment_mirror"


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def write_artifact(kind: str, payload: dict[str, Any], *, identity: str = "") -> str:
    root = runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    digest_input = identity or json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:12]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"{kind}-{timestamp}-{digest}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return str(target)


def failure_receipt(schema: str, owner: dict[str, Any], *, action: str) -> dict[str, Any]:
    encoded = json.dumps(owner, ensure_ascii=False).encode("utf-8")
    receipt: dict[str, Any] = {
        "schema": schema,
        "ok": False,
        "generated_at": now_iso(),
        "action": action,
        "owner_schema": owner.get("schema", ""),
        "reason": owner.get("reason", "owner_action_failed"),
    }
    if len(encoded) <= INLINE_FAILURE_BYTES:
        receipt["owner_result"] = owner
    else:
        receipt["owner_result_artifact"] = write_artifact(f"{action}-failure", owner)
        receipt["issues"] = list(owner.get("issues", []))[:INLINE_SAMPLE_LIMIT]
    return receipt


def plan_receipt(owner: dict[str, Any]) -> dict[str, Any]:
    if not owner.get("ok"):
        return failure_receipt("codex_environment_mirror.plan.v1", owner, action="plan")
    return {
        "schema": "codex_environment_mirror.plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "mirror_root": str(mirror_root()),
        "owner_schema": owner.get("schema", ""),
        "source_count": len(owner.get("sources", [])),
        "sources": owner.get("sources", []),
        "generated_sources": owner.get("generated_sources", []),
        "asset_dispositions": owner.get("asset_dispositions", {}),
        "summary": owner.get("summary", {}),
    }


def validation_receipt(owner: dict[str, Any]) -> dict[str, Any]:
    if not owner.get("ok"):
        return failure_receipt("codex_environment_mirror.validate.v1", owner, action="validate")
    return {
        "schema": "codex_environment_mirror.validate.v1",
        "ok": True,
        "generated_at": now_iso(),
        "mirror_root": str(mirror_root()),
        "owner_schema": owner.get("schema", ""),
        "snapshot_id": owner.get("snapshot_id", ""),
        "readiness": {
            "mirror_valid": owner.get("mirror_valid", False),
            "capability_restore_ready": owner.get("capability_restore_ready", False),
            "full_state_restore_ready": owner.get("full_state_restore_ready", False),
        },
        "source_freshness": {
            "checked": owner.get("source_freshness_checked", False),
            "ok": owner.get("source_freshness_ok"),
        },
        "issues": owner.get("issues", []),
        "advisories": owner.get("advisories", {}),
        "summary": owner.get("summary", {}),
    }


def restore_plan_receipt(owner: dict[str, Any]) -> dict[str, Any]:
    if not owner.get("ok"):
        return failure_receipt("codex_environment_mirror.restore_plan.v1", owner, action="restore-plan")
    artifact = write_artifact(
        "restore-plan",
        owner,
        identity=f"{owner.get('snapshot_id', '')}|{owner.get('target_root', '')}",
    )
    actions = list(owner.get("actions", []))
    return {
        "schema": "codex_environment_mirror.restore_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "owner_schema": owner.get("schema", ""),
        "snapshot_id": owner.get("snapshot_id", ""),
        "target_root": owner.get("target_root", ""),
        "action_count": int(owner.get("action_count", len(actions))),
        "action_sample": actions[:INLINE_SAMPLE_LIMIT],
        "external_archive_gaps": owner.get("external_archive_gaps", []),
        "full_plan_artifact": artifact,
        "rule": owner.get("rule", ""),
    }


def stage_receipt(owner: dict[str, Any]) -> dict[str, Any]:
    if not owner.get("ok"):
        return failure_receipt("codex_environment_mirror.stage.v1", owner, action="stage")
    receipt = owner
    receipt_path = str(owner.get("receipt") or "")
    if receipt_path:
        try:
            receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            return failure_receipt(
                "codex_environment_mirror.stage.v1",
                {
                    **owner,
                    "ok": False,
                    "reason": "stage_receipt_unreadable",
                    "receipt_error": f"{type(exc).__name__}:{exc}",
                },
                action="stage",
            )
    if not receipt.get("ok"):
        return failure_receipt("codex_environment_mirror.stage.v1", receipt, action="stage")
    artifact = write_artifact(
        "stage-receipt",
        receipt,
        identity=f"{receipt.get('snapshot_id', '')}|{receipt.get('target_root', '')}",
    )
    assets = list(receipt.get("assets", []))
    membership_guard = dict(receipt.get("membership_guard", {}))
    return {
        "schema": "codex_environment_mirror.stage.v1",
        "ok": True,
        "generated_at": now_iso(),
        "owner_schema": owner.get("schema", ""),
        "receipt_schema": receipt.get("schema", ""),
        "snapshot_id": receipt.get("snapshot_id", ""),
        "target_root": receipt.get("target_root", ""),
        "asset_count": int(receipt.get("asset_count", len(assets))),
        "asset_sample": assets[:INLINE_SAMPLE_LIMIT],
        "hashes_verified": receipt.get("hashes_verified", False),
        "external_archive_gaps": receipt.get("external_archive_gaps", []),
        "membership_guard": {
            "source_owner_verified": membership_guard.get("source_owner_verified", False),
            "membership_export_sanitized": membership_guard.get("membership_export_sanitized", False),
            "excluded_asset_count": membership_guard.get("excluded_asset_count", 0),
            "sanitized_asset_count": membership_guard.get("sanitized_asset_count", 0),
            "registration_conflict_count": membership_guard.get("registration_conflict_count", 0),
        },
        "activation_performed": receipt.get("activation_performed", False),
        "full_receipt_artifact": artifact,
    }


def run_json(command: list[str], *, timeout: int = 300) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}
    try:
        payload = json.loads(completed.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError:
        return {
            "ok": False,
            "reason": "owner_output_not_json",
            "returncode": completed.returncode,
            "stderr_tail": completed.stderr[-2000:],
            "stdout_tail": completed.stdout[-2000:],
        }
    if completed.returncode != 0 and payload.get("ok") is not False:
        payload = {**payload, "ok": False, "returncode": completed.returncode}
    return payload


def run_mirror(args: list[str], *, timeout: int = 300) -> dict[str, Any]:
    cli = mirror_cli()
    if not cli.is_file():
        return {"ok": False, "reason": "mirror_cli_missing", "path": str(cli)}
    return run_json([sys.executable, str(cli), *args], timeout=timeout)


def affected_source_plan(changed_paths: list[str]) -> dict[str, Any]:
    if not changed_paths:
        return {"schema": "codex_environment_mirror.affected_source_plan.v1", "ok": False, "reason": "changed_path_required"}
    args = ["affected-source-plan"]
    for path in changed_paths:
        args.extend(["--changed", path])
    return run_mirror(args, timeout=180)


def compare_snapshots(left: str, right: str) -> dict[str, Any]:
    return run_mirror(["compare-snapshots", "--left", left, "--right", right], timeout=180)


def git_result(args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    root = mirror_root()
    try:
        completed = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr_tail": completed.stderr[-2000:],
    }


def redact_remote_url(value: str) -> str:
    text = str(value or "").strip()
    if "://" not in text:
        return text
    prefix, rest = text.split("://", 1)
    if "@" not in rest:
        return text
    return prefix + "://<redacted>@" + rest.rsplit("@", 1)[-1]


def latest_snapshot_id() -> str:
    latest = mirror_root() / "snapshots" / "latest.json"
    if not latest.is_file():
        return ""
    try:
        return str(json.loads(latest.read_text(encoding="utf-8-sig")).get("snapshot_id") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def latest_pointer_path() -> Path:
    return mirror_root() / "snapshots" / "latest.json"


def read_latest_pointer() -> bytes | None:
    path = latest_pointer_path()
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def pointer_snapshot_id(payload: bytes | None) -> str:
    if not payload:
        return ""
    try:
        return str(json.loads(payload.decode("utf-8-sig")).get("snapshot_id") or "")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""


def restore_latest_pointer(payload: bytes | None) -> None:
    path = latest_pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix(".json.rollback.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def committed_latest_pointer() -> bytes | None:
    result = git_result(["show", "HEAD:snapshots/latest.json"])
    if not result.get("ok") or not result.get("stdout"):
        return None
    return (str(result["stdout"]).rstrip() + "\n").encode("utf-8")


def remove_snapshot_candidate(snapshot_id: str, *, protected: set[str] | None = None) -> bool:
    if not snapshot_id or snapshot_id in (protected or set()):
        return False
    root = (mirror_root() / "snapshots").resolve()
    target = (root / snapshot_id).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"snapshot_path_escaped:{target}") from exc
    if target == root or not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def stable_previous_pointer() -> tuple[bytes | None, str, list[dict[str, Any]]]:
    current = read_latest_pointer()
    current_id = pointer_snapshot_id(current)
    committed = committed_latest_pointer()
    committed_id = pointer_snapshot_id(committed)
    actions: list[dict[str, Any]] = []
    if current_id and committed_id and current_id != committed_id:
        current_validation = run_mirror(["validate", "--live-sources", "--snapshot", current_id], timeout=300)
        if not current_validation.get("ok") and (mirror_root() / "snapshots" / committed_id).is_dir():
            removed = remove_snapshot_candidate(current_id, protected={committed_id})
            restore_latest_pointer(committed)
            actions.append({
                "code": "invalid_uncommitted_latest_reverted",
                "snapshot_id": current_id,
                "restored_snapshot_id": committed_id,
                "candidate_removed": removed,
            })
            return committed, committed_id, actions
    return current, current_id, actions


def validation_issue_codes(validation: dict[str, Any]) -> set[str]:
    return {
        str(item.get("code") or "")
        for item in validation.get("issues", [])
        if isinstance(item, dict) and item.get("code")
    }


def validation_is_retryable(validation: dict[str, Any]) -> bool:
    codes = validation_issue_codes(validation)
    return bool(codes) and codes.issubset(RETRYABLE_VALIDATION_ISSUES)


def status() -> dict[str, Any]:
    root = mirror_root()
    validation = run_mirror(["validate", "--live-sources"], timeout=180)
    git_status = git_result(["status", "--short"]) if (root / ".git").is_dir() else {"ok": False, "reason": "git_not_initialized"}
    git_head = git_result(["rev-parse", "--short", "HEAD"]) if git_status.get("ok") else {"ok": False}
    remotes = git_result(["remote"]) if git_status.get("ok") else {"ok": False}
    snapshots = sorted(path.name for path in (root / "snapshots").iterdir() if path.is_dir()) if (root / "snapshots").is_dir() else []
    return {
        "schema": "codex_environment_mirror.status.v1",
        "ok": bool(validation.get("ok")) and bool(git_status.get("ok")),
        "generated_at": now_iso(),
        "mirror_root": str(root),
        "latest_snapshot_id": latest_snapshot_id(),
        "snapshot_count": len(snapshots),
        "git": {
            "initialized": (root / ".git").is_dir(),
            "clean": git_status.get("stdout", "") == "",
            "head": git_head.get("stdout", ""),
            "remotes": [item for item in remotes.get("stdout", "").splitlines() if item],
        },
        "readiness": {
            "mirror_valid": validation.get("mirror_valid", False),
            "capability_restore_ready": validation.get("capability_restore_ready", False),
            "full_state_restore_ready": validation.get("full_state_restore_ready", False),
        },
        "source_freshness": {
            "checked": validation.get("source_freshness_checked", False),
            "ok": validation.get("source_freshness_ok"),
        },
        "issues": validation.get("issues", []),
        "advisories": validation.get("advisories", {}),
    }


def push_receipt(*, remote: str = "", branch: str = "") -> dict[str, Any]:
    remote_name = (remote or "origin").strip()
    branch_name = branch.strip()
    status_result = git_result(["status", "--short"])
    if not status_result.get("ok"):
        return {"ok": False, "reason": "git_status_failed", "detail": status_result}
    if status_result.get("stdout"):
        return {
            "ok": False,
            "reason": "git_worktree_not_clean_before_push",
            "status": status_result.get("stdout", ""),
        }
    remote_url = git_result(["remote", "get-url", remote_name])
    if not remote_url.get("ok"):
        return {"ok": False, "reason": "git_remote_missing", "remote": remote_name, "detail": remote_url}
    if not branch_name:
        current_branch = git_result(["branch", "--show-current"])
        if not current_branch.get("ok") or not current_branch.get("stdout"):
            return {"ok": False, "reason": "git_branch_unresolved", "detail": current_branch}
        branch_name = str(current_branch["stdout"]).strip()
    head = git_result(["rev-parse", "HEAD"])
    if not head.get("ok") or not head.get("stdout"):
        return {"ok": False, "reason": "git_head_unresolved", "detail": head}
    head_sha = str(head["stdout"]).strip()
    push = git_result(["push", remote_name, f"HEAD:{branch_name}"], timeout=600)
    if not push.get("ok"):
        return {
            "ok": False,
            "reason": "git_push_failed",
            "remote": remote_name,
            "branch": branch_name,
            "head": head_sha,
            "detail": push,
        }
    verify = git_result(["ls-remote", "--heads", remote_name, branch_name], timeout=120)
    remote_sha = ""
    if verify.get("ok") and verify.get("stdout"):
        remote_sha = str(verify["stdout"]).split()[0]
    if remote_sha != head_sha:
        return {
            "ok": False,
            "reason": "git_push_remote_head_mismatch",
            "remote": remote_name,
            "branch": branch_name,
            "head": head_sha,
            "remote_head": remote_sha,
            "verify": verify,
        }
    return {
        "ok": True,
        "remote": remote_name,
        "remote_url": redact_remote_url(str(remote_url.get("stdout", ""))),
        "branch": branch_name,
        "head": head_sha,
        "push": {
            "returncode": push.get("returncode"),
            "stdout": push.get("stdout", ""),
            "stderr_tail": push.get("stderr_tail", ""),
        },
        "remote_verification": {
            "ok": True,
            "remote_head": remote_sha,
        },
    }


def doctor() -> dict[str, Any]:
    root = mirror_root()
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    ) if root.is_dir() else None
    state = status()
    test_ok = bool(tests and tests.returncode == 0)
    issues = list(state.get("issues", []))
    if not test_ok:
        issues.append({"code": "mirror_unit_tests_failed", "detail": (tests.stderr[-2000:] if tests else "mirror_root_missing")})
    return {
        "schema": "codex_environment_mirror.doctor.v1",
        "ok": bool(state.get("ok")) and test_ok,
        "generated_at": now_iso(),
        "status": state,
        "tests": {"ok": test_ok, "summary": (tests.stderr or tests.stdout).strip()[-2000:] if tests else "not_run"},
        "issues": issues,
    }


def prune_superseded_snapshots(keep_snapshot_id: str) -> list[str]:
    root = mirror_root()
    snapshot_root = (root / "snapshots").resolve()
    if not snapshot_root.is_dir() or not keep_snapshot_id:
        return []
    removed: list[str] = []
    for path in snapshot_root.iterdir():
        if not path.is_dir() or path.name == keep_snapshot_id:
            continue
        target = path.resolve()
        try:
            target.relative_to(snapshot_root)
        except ValueError as exc:
            raise RuntimeError(f"snapshot_path_escaped:{target}") from exc
        shutil.rmtree(target)
        removed.append(path.name)
    return sorted(removed)


def quarantine_superseded_snapshots(keep_snapshot_id: str) -> tuple[list[str], Path | None]:
    root = mirror_root()
    snapshot_root = (root / "snapshots").resolve()
    if not snapshot_root.is_dir() or not keep_snapshot_id:
        return [], None
    quarantine = runtime_root() / "retention" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    removed: list[str] = []
    for path in snapshot_root.iterdir():
        if not path.is_dir() or path.name == keep_snapshot_id:
            continue
        target = path.resolve()
        try:
            target.relative_to(snapshot_root)
        except ValueError as exc:
            raise RuntimeError(f"snapshot_path_escaped:{target}") from exc
        quarantine.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(quarantine / path.name))
        removed.append(path.name)
    return sorted(removed), quarantine if removed else None


def restore_quarantined_snapshots(quarantine: Path | None) -> None:
    if not quarantine or not quarantine.is_dir():
        return
    snapshot_root = mirror_root() / "snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    for path in quarantine.iterdir():
        if path.is_dir():
            shutil.move(str(path), str(snapshot_root / path.name))
    shutil.rmtree(quarantine, ignore_errors=True)


def discard_quarantine(quarantine: Path | None) -> None:
    if quarantine:
        shutil.rmtree(quarantine, ignore_errors=True)


def unstage_all() -> None:
    git_result(["restore", "--staged", "."])


def commit_refresh(snapshot_id: str, *, phase: str = "capture") -> dict[str, Any]:
    add = git_result(["add", "-A"])
    if not add.get("ok"):
        return {"ok": False, "reason": "git_add_failed", "detail": add}
    staged = git_result(["diff", "--cached", "--quiet"])
    if staged.get("returncode") == 0:
        head = git_result(["rev-parse", "--short", "HEAD"])
        return {"ok": True, "committed": False, "head": head.get("stdout", "")}
    suffix = "" if phase == "capture" else f" ({phase})"
    commit = git_result(["commit", "-m", f"Refresh Codex environment mirror {snapshot_id}{suffix}"], timeout=300)
    if not commit.get("ok"):
        return {"ok": False, "reason": "git_commit_failed", "detail": commit}
    head = git_result(["rev-parse", "--short", "HEAD"])
    return {"ok": True, "committed": True, "head": head.get("stdout", "")}


def _refresh_unlocked(confirm: str, changed_paths: list[str] | None = None) -> dict[str, Any]:
    if confirm != REFRESH_CONFIRMATION:
        return {
            "schema": "codex_environment_mirror.refresh.v1",
            "ok": False,
            "reason": "confirmation_required",
            "required_confirmation": REFRESH_CONFIRMATION,
        }
    previous_pointer, previous_snapshot_id, reconciliation = stable_previous_pointer()
    plan = run_mirror(["plan"], timeout=180)
    if not plan.get("ok"):
        return {"schema": "codex_environment_mirror.refresh.v1", "ok": False, "phase": "plan", "result": plan}
    committed_snapshot_id = pointer_snapshot_id(committed_latest_pointer())
    if not changed_paths and previous_snapshot_id and previous_snapshot_id == committed_snapshot_id:
        current_validation = run_mirror(
            ["validate", "--live-sources", "--snapshot", previous_snapshot_id],
            timeout=300,
        )
        reusable = bool(
            current_validation.get("ok")
            and current_validation.get("mirror_valid")
            and current_validation.get("capability_restore_ready")
            and current_validation.get("source_freshness_checked")
            and current_validation.get("source_freshness_ok") is True
        )
        if reusable:
            head = git_result(["rev-parse", "--short", "HEAD"])
            commit = {
                "ok": bool(head.get("ok")),
                "committed": False,
                "head": head.get("stdout", ""),
            }
            return {
                "schema": "codex_environment_mirror.refresh.v1",
                "ok": True,
                "generated_at": now_iso(),
                "snapshot_id": previous_snapshot_id,
                "reused": True,
                "reason": "current_snapshot_valid_and_sources_unchanged",
                "removed_snapshots": [],
                "commit": commit,
                "retention_commit": commit,
                "attempts": [],
                "reconciliation": reconciliation,
                "readiness": {
                    "mirror_valid": True,
                    "capability_restore_ready": True,
                    "full_state_restore_ready": current_validation.get("full_state_restore_ready", False),
                },
                "source_freshness": {"checked": True, "ok": True},
                "advisories": current_validation.get("advisories", {}),
            }
    attempts: list[dict[str, Any]] = []
    snapshot: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    snapshot_id = ""
    for attempt in range(1, REFRESH_MAX_ATTEMPTS + 1):
        snapshot_args = ["snapshot", "--apply"]
        for path in changed_paths or []:
            snapshot_args.extend(["--changed", path])
        snapshot = run_mirror(snapshot_args, timeout=600)
        snapshot_id = str(snapshot.get("snapshot_id") or latest_snapshot_id())
        if not snapshot.get("ok"):
            removed = remove_snapshot_candidate(snapshot_id, protected={previous_snapshot_id})
            restore_latest_pointer(previous_pointer)
            return {
                "schema": "codex_environment_mirror.refresh.v1",
                "ok": False,
                "phase": "snapshot",
                "attempt": attempt,
                "candidate_removed": removed,
                "result": snapshot,
                "reconciliation": reconciliation,
            }
        validation = run_mirror(["validate", "--live-sources", "--snapshot", snapshot_id], timeout=300)
        if validation.get("ok"):
            attempts.append({"attempt": attempt, "snapshot_id": snapshot_id, "ok": True})
            break
        retryable = validation_is_retryable(validation)
        removed = remove_snapshot_candidate(snapshot_id, protected={previous_snapshot_id})
        restore_latest_pointer(previous_pointer)
        attempts.append({
            "attempt": attempt,
            "snapshot_id": snapshot_id,
            "ok": False,
            "retryable": retryable,
            "issue_codes": sorted(validation_issue_codes(validation)),
            "candidate_removed": removed,
        })
        if not retryable or attempt == REFRESH_MAX_ATTEMPTS:
            return {
                "schema": "codex_environment_mirror.refresh.v1",
                "ok": False,
                "phase": "validate",
                "attempts": attempts,
                "validation": validation,
                "restored_snapshot_id": previous_snapshot_id,
                "reconciliation": reconciliation,
            }
        delay = REFRESH_RETRY_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0.0, REFRESH_RETRY_BASE_SECONDS)
        time.sleep(delay)
    commit = commit_refresh(snapshot_id, phase="capture")
    if not commit.get("ok"):
        unstage_all()
        removed_candidate = remove_snapshot_candidate(snapshot_id, protected={previous_snapshot_id})
        restore_latest_pointer(previous_pointer)
        return {
            "schema": "codex_environment_mirror.refresh.v1",
            "ok": False,
            "phase": "commit",
            "snapshot_id": snapshot_id,
            "candidate_removed": removed_candidate,
            "restored_snapshot_id": previous_snapshot_id,
            "commit": commit,
            "attempts": attempts,
            "reconciliation": reconciliation,
        }
    removed, quarantine = quarantine_superseded_snapshots(snapshot_id)
    retention_commit = commit_refresh(snapshot_id, phase="retention") if removed else {"ok": True, "committed": False, "head": commit.get("head", "")}
    if not retention_commit.get("ok"):
        unstage_all()
        restore_quarantined_snapshots(quarantine)
        return {
            "schema": "codex_environment_mirror.refresh.v1",
            "ok": False,
            "phase": "retention_commit",
            "snapshot_id": snapshot_id,
            "removed_snapshots": removed,
            "commit": commit,
            "retention_commit": retention_commit,
            "attempts": attempts,
            "reconciliation": reconciliation,
        }
    discard_quarantine(quarantine)
    return {
        "schema": "codex_environment_mirror.refresh.v1",
        "ok": True,
        "generated_at": now_iso(),
        "snapshot_id": snapshot_id,
        "removed_snapshots": removed,
        "commit": commit,
        "retention_commit": retention_commit,
        "attempts": attempts,
        "reconciliation": reconciliation,
        "readiness": {
            "mirror_valid": validation.get("mirror_valid", False),
            "capability_restore_ready": validation.get("capability_restore_ready", False),
            "full_state_restore_ready": validation.get("full_state_restore_ready", False),
        },
        "source_freshness": {"checked": True, "ok": validation.get("source_freshness_ok")},
        "advisories": validation.get("advisories", {}),
    }


def refresh(confirm: str, changed_paths: list[str] | None = None) -> dict[str, Any]:
    if confirm != REFRESH_CONFIRMATION:
        return _refresh_unlocked(confirm, changed_paths)
    lock_path = runtime_root() / "locks" / "refresh.lock"
    try:
        with exclusive_operation_lock(lock_path, "refresh"):
            return _refresh_unlocked(confirm, changed_paths)
    except MirrorOperationBusy as exc:
        return {
            "schema": "codex_environment_mirror.refresh.v1",
            "ok": False,
            "reason": "mirror_operation_busy",
            "operation": "refresh",
            "lock_path": str(exc.lock_path),
            "lock_owner": exc.owner,
            "next_action": "wait_for_the_active_owner_then_run_status; do_not_start_parallel_refresh",
        }


def publish(confirm: str, *, changed_paths: list[str] | None = None, remote: str = "", branch: str = "") -> dict[str, Any]:
    if confirm != PUBLISH_CONFIRMATION:
        return {
            "schema": "codex_environment_mirror.publish.v1",
            "ok": False,
            "reason": "confirmation_required",
            "required_confirmation": PUBLISH_CONFIRMATION,
        }
    lock_path = runtime_root() / "locks" / "publish.lock"
    try:
        with exclusive_operation_lock(lock_path, "publish"):
            refreshed = refresh(REFRESH_CONFIRMATION, changed_paths)
            if not refreshed.get("ok"):
                return {
                    "schema": "codex_environment_mirror.publish.v1",
                    "ok": False,
                    "phase": "refresh",
                    "refresh": refreshed,
                }
            snapshot_id = str(refreshed.get("snapshot_id") or latest_snapshot_id())
            validation = run_mirror(["validate", "--live-sources", "--snapshot", snapshot_id], timeout=300)
            validation_ok = bool(
                validation.get("ok")
                and validation.get("mirror_valid")
                and validation.get("capability_restore_ready")
                and validation.get("source_freshness_checked")
                and validation.get("source_freshness_ok") is True
            )
            if not validation_ok:
                return {
                    "schema": "codex_environment_mirror.publish.v1",
                    "ok": False,
                    "phase": "validate",
                    "snapshot_id": snapshot_id,
                    "refresh": refreshed,
                    "validation": validation,
                }
            metadata_commit = commit_refresh(snapshot_id, phase="publish-metadata")
            if not metadata_commit.get("ok"):
                unstage_all()
                return {
                    "schema": "codex_environment_mirror.publish.v1",
                    "ok": False,
                    "phase": "metadata_commit",
                    "snapshot_id": snapshot_id,
                    "refresh": refreshed,
                    "validation": validation_receipt(validation),
                    "metadata_commit": metadata_commit,
                }
            pushed = push_receipt(remote=remote, branch=branch)
            if not pushed.get("ok"):
                return {
                    "schema": "codex_environment_mirror.publish.v1",
                    "ok": False,
                    "phase": "push",
                    "snapshot_id": snapshot_id,
                    "refresh": refreshed,
                    "validation": validation_receipt(validation),
                    "metadata_commit": metadata_commit,
                    "push": pushed,
                }
            return {
                "schema": "codex_environment_mirror.publish.v1",
                "ok": True,
                "generated_at": now_iso(),
                "snapshot_id": snapshot_id,
                "refresh": refreshed,
                "validation": validation_receipt(validation),
                "metadata_commit": metadata_commit,
                "push": pushed,
                "readiness": {
                    "mirror_valid": validation.get("mirror_valid", False),
                    "capability_restore_ready": validation.get("capability_restore_ready", False),
                    "full_state_restore_ready": validation.get("full_state_restore_ready", False),
                },
                "source_freshness": {"checked": True, "ok": validation.get("source_freshness_ok")},
                "advisories": validation.get("advisories", {}),
            }
    except MirrorOperationBusy as exc:
        return {
            "schema": "codex_environment_mirror.publish.v1",
            "ok": False,
            "reason": "mirror_operation_busy",
            "operation": "publish",
            "lock_path": str(exc.lock_path),
            "lock_owner": exc.owner,
            "next_action": "wait_for_the_active_owner_then_run_status; do_not_start_parallel_publish_or_refresh",
        }


def execute(action: str, *, target_root: str = "", confirm: str = "", changed_paths: list[str] | None = None, left_snapshot: str = "", right_snapshot: str = "", remote: str = "", branch: str = "") -> dict[str, Any]:
    if action == "status":
        return status()
    if action == "doctor":
        return doctor()
    if action == "plan":
        return plan_receipt(run_mirror(["plan"], timeout=180))
    if action == "affected-source-plan":
        return affected_source_plan(changed_paths or [])
    if action == "compare-snapshots":
        if not left_snapshot or not right_snapshot:
            return {"schema": "codex_environment_mirror.compare_snapshots.v1", "ok": False, "reason": "snapshot_ids_required"}
        return compare_snapshots(left_snapshot, right_snapshot)
    if action == "validate":
        return validation_receipt(run_mirror(["validate", "--live-sources"], timeout=300))
    if action == "refresh":
        return refresh(confirm, changed_paths)
    if action == "publish":
        return publish(confirm, changed_paths=changed_paths, remote=remote, branch=branch)
    if action == "restore-plan":
        if not target_root:
            return {"schema": "codex_environment_mirror.restore_plan.v1", "ok": False, "reason": "target_root_required"}
        owner = run_mirror(["restore-plan", "--target-root", target_root], timeout=300)
        return restore_plan_receipt(owner)
    if action == "stage":
        if not target_root:
            return {"schema": "codex_environment_mirror.stage.v1", "ok": False, "reason": "target_root_required"}
        if confirm != STAGE_CONFIRMATION:
            return {"schema": "codex_environment_mirror.stage.v1", "ok": False, "reason": "confirmation_required", "required_confirmation": STAGE_CONFIRMATION}
        owner = run_mirror(["stage", "--target-root", target_root, "--confirm", STAGE_CONFIRMATION], timeout=600)
        return stage_receipt(owner)
    return {"schema": "codex_environment_mirror.command.v1", "ok": False, "reason": "unknown_action", "action": action}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unified Codex environment mirror owner adapter")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("status", "plan", "doctor", "validate"):
        sub.add_parser(name)
    affected_parser = sub.add_parser("affected-source-plan")
    affected_parser.add_argument("--changed", action="append", default=[])
    compare_parser = sub.add_parser("compare-snapshots")
    compare_parser.add_argument("--left", required=True)
    compare_parser.add_argument("--right", required=True)
    refresh_parser = sub.add_parser("refresh")
    refresh_parser.add_argument("--confirm", default="")
    refresh_parser.add_argument("--changed", action="append", default=[])
    publish_parser = sub.add_parser("publish")
    publish_parser.add_argument("--confirm", default="")
    publish_parser.add_argument("--changed", action="append", default=[])
    publish_parser.add_argument("--remote", default="")
    publish_parser.add_argument("--branch", default="")
    restore_parser = sub.add_parser("restore-plan")
    restore_parser.add_argument("--target-root", required=True)
    stage_parser = sub.add_parser("stage")
    stage_parser.add_argument("--target-root", required=True)
    stage_parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if args.action == "affected-source-plan":
        payload = affected_source_plan(args.changed)
    elif args.action == "compare-snapshots":
        payload = compare_snapshots(args.left, args.right)
    else:
        payload = execute(
            args.action,
            target_root=getattr(args, "target_root", ""),
            confirm=getattr(args, "confirm", ""),
            changed_paths=getattr(args, "changed", []),
            left_snapshot=getattr(args, "left", ""),
            right_snapshot=getattr(args, "right", ""),
            remote=getattr(args, "remote", ""),
            branch=getattr(args, "branch", ""),
        )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
