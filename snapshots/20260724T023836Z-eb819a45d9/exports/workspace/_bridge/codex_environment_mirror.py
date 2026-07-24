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
import ntpath
import posixpath
import random
import re
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
RELEASE_CONFIRMATION = "RELEASE-CODEX-MIRROR"
CONTRACT_REVIEW_CONFIRMATION = "REVIEW-CODEX-MIRROR-CONTRACTS"
STAGE_CONFIRMATION = "STAGE-RESTORE"
MCP_BUNDLE_ARCHIVE_ENV = "CODEX_MCP_BUNDLE_ARCHIVE_ROOT"
MCP_PUBLIC_DISTRIBUTIONS = {"github_release_asset", "github_release_asset_authorized_only"}
INLINE_SAMPLE_LIMIT = 5
INLINE_FAILURE_BYTES = 12 * 1024
FAILURE_TEXT_LIMIT = 500
FAILURE_TAIL_LIMIT = 2000
REFRESH_MAX_ATTEMPTS = 3
REFRESH_RETRY_BASE_SECONDS = 0.25
STATUS_VALIDATION_TTL_SECONDS = 180
CAPTURE_LEASE_TTL_SECONDS = 720
CAPTURE_LEASE_NAME = "capture-lease.json"
RETRYABLE_VALIDATION_ISSUES = frozenset({
    "source_assets_missing",
    "source_assets_stale",
    "source_assets_changed",
    "generated_source_changed",
})
SEMANTIC_TAG = re.compile(r"^seed-v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
WORK_GIT_SOURCE_MODE = "work_git_primary"
WORK_GIT_RELEASE_SOURCE_ID = "wsl-work-git-release-receipt"
WINDOWS_MIRROR_ROOT = Path("/mnt/c/Users/45543/codex-env-mirror")
WORK_GIT_ROOT = Path(__file__).resolve().parents[2]
MIRROR_SOURCE_READ_ONLY_ENV = {
    "CODEX_MIRROR_SOURCE_READ_ONLY": "1",
    "CODEX_MIRROR_REVERSE_OVERWRITE_BLOCKED": "1",
}


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


def capture_lease_path() -> Path:
    return mirror_root() / "runtime" / CAPTURE_LEASE_NAME


@contextmanager
def mirror_capture_lease():
    """Temporarily defer provider projection writes during one capture attempt."""
    path = capture_lease_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{time.time_ns()}"
    now = time.time()
    existing = read_lock_owner(path)
    if float(existing.get("expires_at_epoch") or 0.0) > now:
        raise MirrorOperationBusy(path, existing)
    payload = {
        "schema": "codex_environment_mirror.capture_lease.v1",
        "pid": os.getpid(),
        "token": token,
        "started_at": now_iso(),
        "expires_at_epoch": now + CAPTURE_LEASE_TTL_SECONDS,
        "purpose": "defer_provider_projection_during_capture_and_live_validation",
    }
    temporary = path.with_suffix(path.suffix + f".{token}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    try:
        yield payload
    finally:
        current = read_lock_owner(path)
        if current.get("token") == token:
            path.unlink(missing_ok=True)


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
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = (Path.home() / "codex-env-mirror", WINDOWS_MIRROR_ROOT)
    for candidate in candidates:
        if (candidate / "scripts" / "mirror_cli.py").is_file():
            return candidate.resolve()
    return candidates[0].resolve()


def mirror_cli() -> Path:
    return mirror_root() / "scripts" / "mirror_cli.py"


def runtime_root() -> Path:
    configured = os.environ.get("CODEX_ENV_MIRROR_RUNTIME_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent / "runtime" / "codex_environment_mirror"


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


_SENSITIVE_FAILURE_KEY = re.compile(
    r"(?:token|password|passwd|secret|api[_-]?key|authorization|cookie|proxy)",
    re.IGNORECASE,
)
_SENSITIVE_FAILURE_TEXT = re.compile(
    r"(?i)(bearer\s+)[^\s,;]+|((?:token|password|passwd|secret|api[_-]?key|authorization|cookie)\s*[=:]\s*)[^\s,;]+"
)


def _safe_failure_text(value: Any, *, limit: int = FAILURE_TEXT_LIMIT) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _SENSITIVE_FAILURE_TEXT.sub(
        lambda match: (match.group(1) or match.group(2) or "") + "<redacted>",
        text,
    )
    return text[:limit]


def _safe_failure_value(value: Any, *, depth: int = 0) -> Any:
    """Return a small, redacted diagnostic value for default status output."""
    if depth > 3:
        return "<truncated>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= INLINE_SAMPLE_LIMIT:
                break
            key_text = str(key)
            result[key_text] = (
                "<redacted>"
                if _SENSITIVE_FAILURE_KEY.search(key_text)
                else _safe_failure_value(item, depth=depth + 1)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_failure_value(item, depth=depth + 1) for item in list(value)[:INLINE_SAMPLE_LIMIT]]
    if isinstance(value, str):
        return _safe_failure_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_failure_text(value)


def _failure_reason(payload: dict[str, Any], action: str) -> str:
    explicit = _safe_failure_text(payload.get("reason"), limit=200)
    generic = {"", "owner_action_failed", f"{action}_failed", "validation_failed"}
    if explicit not in generic:
        return explicit
    for key in ("issues", "blockers", "failures", "actionable_failures"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("code"):
                return _safe_failure_text(item["code"], limit=200)
    return explicit or f"{action}_failed"


def failure_diagnostic(
    owner: dict[str, Any],
    *,
    action: str,
    source: str = "owner",
) -> dict[str, Any]:
    """Project a bounded, actionable failure without embedding the full owner result."""
    payload = owner if isinstance(owner, dict) else {}
    diagnostic: dict[str, Any] = {
        "source": source,
        "action": action,
        "schema": _safe_failure_text(payload.get("schema"), limit=160),
        "owner_schema": _safe_failure_text(payload.get("owner_schema"), limit=160),
        "phase": _safe_failure_text(payload.get("phase") or action, limit=120),
        "reason": _failure_reason(payload, action),
    }
    for key in ("detail", "next_action"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            diagnostic[key] = (
                _safe_failure_text(value)
                if isinstance(value, (str, int, float, bool))
                else _safe_failure_value(value)
            )
    if isinstance(payload.get("returncode"), int):
        diagnostic["returncode"] = payload["returncode"]
    for key in ("issues", "blockers", "failures", "actionable_failures"):
        value = payload.get(key)
        if value:
            diagnostic[key] = _safe_failure_value(value)
    artifact_ref = payload.get("owner_result_artifact") or payload.get("artifact_ref")
    if artifact_ref not in (None, ""):
        diagnostic["artifact_ref"] = _safe_failure_text(artifact_ref, limit=1000)
    for key in ("stderr_tail", "stdout_tail"):
        value = payload.get(key)
        if value not in (None, ""):
            diagnostic[key] = _safe_failure_text(value, limit=FAILURE_TAIL_LIMIT)
    return diagnostic


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
    receipt.update(failure_diagnostic(owner, action=action, source=action))
    if len(encoded) <= INLINE_FAILURE_BYTES:
        receipt["owner_result"] = owner
    else:
        receipt["owner_result_artifact"] = write_artifact(f"{action}-failure", owner)
        receipt["artifact_ref"] = receipt["owner_result_artifact"]
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


def mcp_bundle_readiness() -> dict[str, Any]:
    """Read the single Work Git MCP bundle owner receipt for restore gates."""
    try:
        from mcp_recovery_bundle_owner import readiness as bundle_readiness
        from mcp_recovery_bundle_owner import load_json, variables_for
        from mcp_recovery_bundle_owner import DEFAULT_MANIFEST

        manifest = load_json(DEFAULT_MANIFEST)
        archive_root = Path(os.environ.get(MCP_BUNDLE_ARCHIVE_ENV, str(mirror_root() / "artifacts" / "mcp-bundles")))
        return bundle_readiness(manifest, variables_for(manifest), archive_root)
    except (OSError, ValueError, ImportError) as exc:
        return {
            "schema": "mcp_recovery_bundle_owner.v1",
            "ok": False,
            "capability_restore_ready": False,
            "reason": "mcp_bundle_owner_unavailable",
            "detail": f"{type(exc).__name__}:{exc}",
            "blocked_missing_bundle": ["mcp-recovery-bundle-owner"],
        }


def merge_live_source_validation(snapshot_validation: dict[str, Any], live_validation: dict[str, Any]) -> dict[str, Any]:
    """Use one live-source scan plus one normal snapshot/control-plane validation."""
    merged = dict(snapshot_validation)
    merged["source_freshness_checked"] = live_validation.get("source_freshness_checked", False)
    merged["source_freshness_ok"] = live_validation.get("source_freshness_ok")
    merged["live_source_validation_reused"] = True
    snapshot_advisories = snapshot_validation.get("advisories") if isinstance(snapshot_validation.get("advisories"), dict) else {}
    live_advisories = live_validation.get("advisories") if isinstance(live_validation.get("advisories"), dict) else {}
    merged["advisories"] = {**snapshot_advisories, **live_advisories}
    live_issues = list(live_validation.get("issues", [])) if isinstance(live_validation.get("issues"), list) else []
    snapshot_issues = list(snapshot_validation.get("issues", [])) if isinstance(snapshot_validation.get("issues"), list) else []
    merged["issues"] = snapshot_issues + [
        item for item in live_issues if item not in snapshot_issues
    ]
    merged["ok"] = bool(
        snapshot_validation.get("ok")
        and live_validation.get("ok")
        and live_validation.get("source_freshness_checked")
        and live_validation.get("source_freshness_ok") is True
    )
    return merged


def reusable_validation_receipt(receipt: Any, snapshot_id: str) -> bool:
    if not isinstance(receipt, dict) or not receipt.get("ok"):
        return False
    readiness = receipt.get("readiness") if isinstance(receipt.get("readiness"), dict) else {}
    freshness = receipt.get("source_freshness") if isinstance(receipt.get("source_freshness"), dict) else {}
    return bool(
        str(receipt.get("snapshot_id") or "") == snapshot_id
        and readiness.get("mirror_valid")
        and readiness.get("capability_restore_ready")
        and freshness.get("checked")
        and freshness.get("ok") is True
        and not receipt.get("issues")
    )


def status_validation_cache_path() -> Path:
    return runtime_root() / "status-validation-latest.json"


def load_status_validation_receipt(snapshot_id: str) -> tuple[dict[str, Any], float | None]:
    path = status_validation_cache_path()
    try:
        age = max(0.0, time.time() - path.stat().st_mtime)
        if age > STATUS_VALIDATION_TTL_SECONDS:
            return {}, age
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, None
    if not reusable_validation_receipt(receipt, snapshot_id):
        return {}, age
    return receipt, age


def control_plane_validation_receipt(snapshot_id: str) -> tuple[dict[str, Any], float | None]:
    path = mirror_root() / "manifests" / "control-plane-state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        age = max(0.0, time.time() - path.stat().st_mtime)
    except (OSError, json.JSONDecodeError):
        return {}, None
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    freshness = payload.get("source_freshness") if isinstance(payload.get("source_freshness"), dict) else {}
    receipt = {
        "schema": "codex_environment_mirror.validate.v1",
        "ok": bool(
            str(snapshot.get("snapshot_id") or "") == snapshot_id
            and readiness.get("mirror_valid")
            and readiness.get("capability_restore_ready")
            and freshness.get("checked")
            and freshness.get("ok") is True
        ),
        "generated_at": payload.get("generated_at", ""),
        "snapshot_id": snapshot.get("snapshot_id", ""),
        "readiness": readiness,
        "source_freshness": freshness,
        "issues": [],
        "advisories": {"required_archive_gaps": payload.get("required_archive_gaps", [])},
        "summary": {"capture_mode": snapshot.get("capture_mode", "")},
    }
    return (receipt, age) if reusable_validation_receipt(receipt, snapshot_id) else ({}, age)


def persist_status_validation_receipt(receipt: dict[str, Any]) -> str:
    path = status_validation_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return str(path)


def compact_operation_receipt(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep CLI output small while preserving the complete owner receipt."""
    if action not in {"refresh", "publish", "release"}:
        return payload
    artifact = write_artifact(f"{action}-receipt", payload)
    summary: dict[str, Any] = {
        "schema": f"codex_environment_mirror.{action}.summary.v1",
        "ok": bool(payload.get("ok")),
        "generated_at": payload.get("generated_at", now_iso()),
        "action": action,
        "phase": payload.get("phase", "complete" if payload.get("ok") else ""),
        "reason": payload.get("reason", ""),
        "snapshot_id": payload.get("snapshot_id", ""),
        "reused": payload.get("reused", False),
        "resumed": payload.get("resumed", False),
        "elapsed_ms": payload.get("elapsed_ms"),
        "phase_timings_ms": payload.get("phase_timings_ms", {}),
        "readiness": payload.get("readiness", {}),
        "source_freshness": payload.get("source_freshness", {}),
        "issues": payload.get("issues", []),
        "advisories": payload.get("advisories", {}),
        "receipt_artifact": artifact,
        "full_output_command": f"cat {artifact}",
    }
    for key in ("source_authority", "refresh_scope", "validation", "commit", "retention_commit", "retention_cleanup_commit", "metadata_commit", "push"):
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "validation":
            summary[key] = {
                "ok": value.get("ok"),
                "snapshot_id": value.get("snapshot_id"),
                "readiness": value.get("readiness", {}),
                "source_freshness": value.get("source_freshness", {}),
                "issues": value.get("issues", []),
            }
        elif key == "push":
            summary[key] = {
                "ok": value.get("ok"),
                "reason": value.get("reason", ""),
                "remote": value.get("remote", ""),
                "branch": value.get("branch", ""),
                "head": value.get("head", ""),
                "remote_verification": value.get("remote_verification", {}),
            }
        elif key in {"commit", "retention_commit", "retention_cleanup_commit", "metadata_commit"}:
            summary[key] = {
                "ok": value.get("ok"),
                "committed": value.get("committed"),
                "head": value.get("head", ""),
                "reason": value.get("reason", ""),
            }
        elif key == "refresh_scope":
            summary[key] = {
                "ok": value.get("ok"),
                "mode": value.get("mode", ""),
                "changed_path_count": len(value.get("changed_paths", []) if isinstance(value.get("changed_paths"), list) else []),
                "fallback_reason": value.get("fallback_reason", ""),
            }
        else:
            summary[key] = {
                "ok": value.get("ok"),
                "source_mode": value.get("source_mode", ""),
                "work_git": value.get("work_git", {}),
                "issues": value.get("issues", []),
            }
    return {key: item for key, item in summary.items() if item not in (None, "", [], {})}


def restore_plan_receipt(owner: dict[str, Any]) -> dict[str, Any]:
    if not owner.get("ok"):
        return failure_receipt("codex_environment_mirror.restore_plan.v1", owner, action="restore-plan")
    artifact = write_artifact(
        "restore-plan",
        owner,
        identity=f"{owner.get('snapshot_id', '')}|{owner.get('target_root', '')}",
    )
    actions = list(owner.get("actions", []))
    bundles = mcp_bundle_readiness()
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
        "mcp_bundle_readiness": bundles,
        "capability_restore_ready": bool(owner.get("capability_restore_ready", True) and bundles.get("capability_restore_ready")),
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
    bundles = mcp_bundle_readiness()
    if not bundles.get("capability_restore_ready"):
        return failure_receipt(
            "codex_environment_mirror.stage.v1",
            {
                **receipt,
                "ok": False,
                "reason": "mcp_bundle_restore_not_ready",
                "mcp_bundle_readiness": bundles,
                "next_action": bundles.get("next_action", "build or import required MCP recovery bundles"),
            },
            action="stage",
        )
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
        "mcp_bundle_readiness": bundles,
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


def run_json(command: list[str], *, timeout: int = 300, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update({str(key): str(value) for key, value in extra_env.items() if str(value)})
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
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


def _wsl_path_to_windows(value: Path) -> str:
    raw = str(value)
    match = re.match(r"^/mnt/([a-zA-Z])(?:/(.*))?$", raw)
    if match:
        rest = str(match.group(2) or "").replace("/", "\\")
        return f"{match.group(1).upper()}:\\{rest}" if rest else f"{match.group(1).upper()}:\\"
    return raw


def _windows_owner_command(cli: Path, args: list[str]) -> tuple[list[str], dict[str, str]] | None:
    if not str(mirror_root()).startswith("/mnt/"):
        return None
    python_candidates = (
        Path("/mnt/c/Users/45543/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"),
        Path("/mnt/c/Python314/python.exe"),
    )
    python_path = next((path for path in python_candidates if path.is_file()), None)
    if python_path is None:
        return None
    command = [str(python_path), _wsl_path_to_windows(cli), *[str(item) for item in args]]
    environment = os.environ.copy()
    environment.update(MIRROR_SOURCE_READ_ONLY_ENV)
    python_dir = _wsl_path_to_windows(python_path.parent)
    windows_git_dirs = [Path("/mnt/c/Program Files/Git/cmd"), Path("/mnt/c/Program Files/Git/bin")]
    git_dirs = [_wsl_path_to_windows(path) for path in windows_git_dirs if (path / "git.exe").is_file()]
    windows_path = ";".join([python_dir, *git_dirs, r"C:\Windows\System32", r"C:\Windows"])
    environment["PATH"] = windows_path
    if git_dirs:
        environment["CODEX_MIRROR_GIT_EXE"] = git_dirs[0] + r"\git.exe"
    return command, environment


def _git_executable_and_root() -> tuple[str, str]:
    root = mirror_root()
    configured = os.environ.get("CODEX_MIRROR_GIT_EXE", "").strip()
    if configured:
        git_exe = configured
    elif str(root).startswith("/mnt/") and Path("/mnt/c/Program Files/Git/cmd/git.exe").is_file():
        git_exe = "/mnt/c/Program Files/Git/cmd/git.exe"
    else:
        git_exe = "git"
    git_root = _wsl_path_to_windows(root) if git_exe.lower().endswith(".exe") else str(root)
    return git_exe, git_root


def run_mirror(args: list[str], *, timeout: int = 300) -> dict[str, Any]:
    started = time.perf_counter()

    def annotate(payload: dict[str, Any]) -> dict[str, Any]:
        result = dict(payload)
        result.setdefault("_owner_elapsed_ms", round((time.perf_counter() - started) * 1000, 1))
        result.setdefault("_owner_operation", args[0] if args else "")
        return result

    cli = mirror_cli()
    if not cli.is_file():
        return annotate({"ok": False, "reason": "mirror_cli_missing", "path": str(cli)})
    owner_command = _windows_owner_command(cli, args)
    if owner_command is None:
        return annotate(run_json([sys.executable, str(cli), *args], timeout=timeout, extra_env=MIRROR_SOURCE_READ_ONLY_ENV))
    command, environment = owner_command
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return annotate({"ok": False, "reason": f"{type(exc).__name__}:{exc}"})
    try:
        payload = json.loads(completed.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError:
        return annotate({
            "ok": False,
            "reason": "owner_output_not_json",
            "returncode": completed.returncode,
            "stderr_tail": completed.stderr[-2000:],
            "stdout_tail": completed.stdout[-2000:],
        })
    if completed.returncode != 0 and payload.get("ok") is not False:
        payload = {**payload, "ok": False, "returncode": completed.returncode}
    return annotate(payload)


def capture_snapshot_and_live_validate(snapshot_args: list[str]) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    """Keep the provider writer deferred across one costly capture and its freshness check."""
    with mirror_capture_lease() as capture_lease:
        snapshot = run_mirror(snapshot_args, timeout=600)
        snapshot_id = str(snapshot.get("snapshot_id") or latest_snapshot_id())
        if not snapshot.get("ok"):
            return snapshot, snapshot_id, {}, capture_lease
        live_validation = snapshot.get("live_validation")
        validation = (
            live_validation
            if isinstance(live_validation, dict)
            else run_mirror(
                ["validate", "--live-sources", "--snapshot", snapshot_id, "--skip-control-plane"],
                timeout=300,
            )
        )
        return snapshot, snapshot_id, validation, capture_lease


def _expand_manifest_value(value: str, variables: dict[str, str]) -> str:
    result = str(value or "")
    for _ in range(8):
        previous = result
        result = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
            lambda match: str(variables.get(match.group(1) or match.group(2), match.group(0))),
            result,
        )
        result = os.path.expandvars(result)
        if result == previous:
            break
    return result


def _normalized_path(value: str) -> str:
    raw = str(value or "").strip()
    slash_path = raw.replace("\\", "/")
    wsl_unc = re.match(
        r"^//(?:wsl\.localhost|wsl\$)/[^/]+(?P<linux_path>/.*)?$",
        slash_path,
        flags=re.IGNORECASE,
    )
    if wsl_unc:
        return posixpath.normpath(wsl_unc.group("linux_path") or "/")
    if slash_path.startswith("/"):
        return posixpath.normpath(slash_path)
    return ntpath.normcase(ntpath.normpath(raw))


def work_git_release_gate() -> dict[str, Any]:
    """Validate the one-way Work Git authority before mirror mutation."""
    owner_script = Path(__file__).resolve().parent / "wsl_workspace_owner.py"
    receipt = run_json(
        [sys.executable, str(owner_script), "mirror-export", "--kind", "work-git-release"],
        timeout=90,
    )
    manifest_path = mirror_root() / "manifests" / "source-authorities.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schema": "codex_environment_mirror.work_git_release_gate.v1",
            "ok": False,
            "reason": "source_authority_manifest_unreadable",
            "manifest_path": str(manifest_path),
            "detail": f"{type(exc).__name__}:{exc}",
        }

    variables = {str(key): str(value) for key, value in (manifest.get("variables") or {}).items()}
    for _ in range(8):
        variables = {key: _expand_manifest_value(value, variables) for key, value in variables.items()}
    authority = manifest.get("workspace_authority") if isinstance(manifest.get("workspace_authority"), dict) else {}
    generated_ids = {
        str(item.get("id") or "")
        for item in manifest.get("generated_sources", [])
        if isinstance(item, dict)
    }
    work_git = receipt.get("work_git") if isinstance(receipt.get("work_git"), dict) else {}
    worktree = str(work_git.get("worktree") or "")
    expected_workspace = str(Path(worktree) / "workspace") if worktree else ""
    configured_workspace = str(variables.get("WORKSPACE_ROOT") or "")
    issues: list[dict[str, Any]] = []
    if not receipt.get("ok") or not work_git.get("release_ready"):
        issues.append({
            "code": "work_git_release_not_ready",
            "blocked_by": work_git.get("issues", []),
        })
    if authority.get("mode") != WORK_GIT_SOURCE_MODE:
        issues.append({
            "code": "workspace_source_mode_not_work_git_primary",
            "observed": authority.get("mode", ""),
            "expected": WORK_GIT_SOURCE_MODE,
        })
    if authority.get("mirror_reverse_overwrite") is not False:
        issues.append({"code": "mirror_reverse_overwrite_not_explicitly_blocked"})
    if authority.get("native_workspace_role") != "transition_source_only":
        issues.append({
            "code": "native_workspace_role_invalid",
            "observed": authority.get("native_workspace_role", ""),
        })
    if WORK_GIT_RELEASE_SOURCE_ID not in generated_ids:
        issues.append({"code": "work_git_release_receipt_not_captured"})
    if not expected_workspace or _normalized_path(configured_workspace) != _normalized_path(expected_workspace):
        issues.append({
            "code": "workspace_source_root_mismatch",
            "configured": configured_workspace,
            "expected": expected_workspace,
        })
    return {
        "schema": "codex_environment_mirror.work_git_release_gate.v1",
        "ok": not issues,
        "generated_at": now_iso(),
        "source_mode": authority.get("mode", ""),
        "workspace_source_root": configured_workspace,
        "native_workspace_role": authority.get("native_workspace_role", ""),
        "mirror_reverse_overwrite": authority.get("mirror_reverse_overwrite"),
        "work_git": {
            "release_ready": bool(work_git.get("release_ready")),
            "branch": work_git.get("branch", ""),
            "worktree_head": work_git.get("worktree_head", ""),
            "bare_head": work_git.get("bare_head", ""),
            "worktree": worktree,
            "bare_repo": work_git.get("bare_repo", ""),
            "wsl_user": work_git.get("wsl_user", ""),
        },
        "issues": issues,
        "owner_receipt_schema": receipt.get("schema", ""),
        "manifest_path": str(manifest_path),
    }


def affected_source_plan(changed_paths: list[str]) -> dict[str, Any]:
    if not changed_paths:
        return {"schema": "codex_environment_mirror.affected_source_plan.v1", "ok": False, "reason": "changed_path_required"}
    args = ["affected-source-plan"]
    for path in changed_paths:
        args.extend(["--changed", normalize_changed_path_for_mirror_owner(path)])
    return run_mirror(args, timeout=180)


def compare_snapshots(left: str, right: str) -> dict[str, Any]:
    return run_mirror(["compare-snapshots", "--left", left, "--right", right], timeout=180)


def git_result(args: list[str], *, timeout: int = 120, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    git_exe, git_root = _git_executable_and_root()
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update({str(key): str(value) for key, value in extra_env.items() if str(value)})
    try:
        completed = subprocess.run(
            [git_exe, "-C", git_root, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr_tail": completed.stderr[-2000:],
    }


def git_result_at(root: str, args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr_tail": completed.stderr[-2000:],
    }


def gh_result(args: list[str], *, timeout: int = 180, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items() if str(value)})
    try:
        completed = subprocess.run(
            ["gh", *args],
            cwd=mirror_root(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr_tail": completed.stderr[-2000:],
    }


def mcp_release_assets(snapshot_id: str) -> dict[str, Any]:
    """Return the public MCP archives and a sanitized hash index for a release."""
    owner = WORK_GIT_ROOT / "workspace" / "_bridge" / "mcp_recovery_bundle_owner.py"
    archive_root = Path(os.environ.get(MCP_BUNDLE_ARCHIVE_ENV, str(Path.home() / ".codex-app" / "mcp-recovery-bundles"))).expanduser()
    try:
        completed = subprocess.run(
            [sys.executable, str(owner), "--archive-root", str(archive_root), "readiness"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        )
        payload = json.loads(completed.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "mcp_bundle_readiness_unavailable", "detail": f"{type(exc).__name__}:{exc}"}
    if not isinstance(payload, dict) or not payload.get("ok") or not payload.get("bundle_plan_ready"):
        return {"ok": False, "reason": "mcp_bundle_plan_not_ready", "readiness": payload}
    manifest_path = WORK_GIT_ROOT / "workspace" / "_bridge" / "mcp_recovery_bundle_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "mcp_bundle_manifest_unreadable", "detail": f"{type(exc).__name__}:{exc}"}
    public_ids = {
        str(item.get("id")) for item in manifest.get("bundles", [])
        if isinstance(item, dict) and item.get("required") is not False
        and str(item.get("distribution") or "") in MCP_PUBLIC_DISTRIBUTIONS
    }
    entries = payload.get("bundle_index", {}).get("bundles", {})
    assets: list[dict[str, Any]] = []
    missing: list[str] = []
    for bundle_id in sorted(public_ids):
        entry = entries.get(bundle_id) if isinstance(entries, dict) else None
        archive_name = str(entry.get("archive") or "") if isinstance(entry, dict) else ""
        archive = archive_root / archive_name
        if not isinstance(entry, dict) or not archive.is_file():
            missing.append(bundle_id)
            continue
        assets.append({
            "id": bundle_id, "name": archive_name, "path": str(archive),
            "sha256": str(entry.get("sha256") or ""), "size_bytes": archive.stat().st_size,
            "platform": entry.get("platform"), "entrypoints": entry.get("entrypoints", []),
        })
    index_payload = {
        "schema": "codex_mcp_release_asset_index.v1", "snapshot_id": snapshot_id,
        "generated_at": now_iso(), "hash_algorithm": "sha256", "assets": [
            {key: item[key] for key in ("id", "name", "sha256", "size_bytes", "platform", "entrypoints")}
            for item in assets
        ],
    }
    index_path = runtime_root() / f"mcp-bundle-index-{snapshot_id}.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"ok": not missing, "snapshot_id": snapshot_id, "assets": assets, "index_path": str(index_path), "missing": missing}


def github_repo_slug(remote_url: str) -> str:
    """Return an owner/repository slug for a standard GitHub remote only."""
    matched = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", str(remote_url or "").strip(), re.IGNORECASE)
    return f"{matched.group(1)}/{matched.group(2)}" if matched else ""


def published_release_asset_catalog(remote_url: str, *, extra_env: dict[str, str] | None = None) -> dict[tuple[str, str, int], dict[str, str]]:
    """Index already-published GitHub assets by immutable content identity.

    GitHub does not deduplicate assets between Releases.  This catalog lets the
    next release retain a hash-verified pointer to an existing public archive
    instead of uploading the same large bytes again.
    """
    slug = github_repo_slug(remote_url)
    if not slug:
        return {}
    result = gh_result(["api", f"repos/{slug}/releases?per_page=100"], extra_env=extra_env)
    try:
        releases = json.loads(str(result.get("stdout") or "[]"))
    except json.JSONDecodeError:
        return {}
    catalog: dict[tuple[str, str, int], dict[str, str]] = {}
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        release_tag = str(release.get("tag_name") or "").strip()
        if not release_tag:
            continue
        for asset in release.get("assets") if isinstance(release.get("assets"), list) else []:
            if not isinstance(asset, dict):
                continue
            digest = release_asset_sha256(asset)
            name = str(asset.get("name") or "")
            size = int(asset.get("size") or 0)
            if name and digest and size >= 0:
                catalog[(name, digest, size)] = {
                    "release_tag": release_tag,
                    "release_asset_url": str(asset.get("browser_download_url") or ""),
                }
    return catalog


def prepare_release_bundle_assets(
    bundle_assets: dict[str, Any],
    *,
    tag: str,
    remote_url: str,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Write a release index that references unchanged public archives by hash."""
    catalog = published_release_asset_catalog(remote_url, extra_env=extra_env)
    annotated: list[dict[str, Any]] = []
    reused: list[dict[str, str]] = []
    upload: list[dict[str, Any]] = []
    for raw in bundle_assets.get("assets", []) if isinstance(bundle_assets.get("assets"), list) else []:
        if not isinstance(raw, dict):
            continue
        asset = dict(raw)
        key = (str(asset.get("name") or ""), str(asset.get("sha256") or ""), int(asset.get("size_bytes") or 0))
        prior = catalog.get(key)
        if prior and prior.get("release_tag") != tag:
            asset.update(prior)
            reused.append({"name": key[0], **prior})
        else:
            asset["release_tag"] = tag
            upload.append(asset)
        annotated.append(asset)
    index_payload = {
        "schema": "codex_mcp_release_asset_index.v1",
        "snapshot_id": bundle_assets.get("snapshot_id", ""),
        "generated_at": now_iso(),
        "hash_algorithm": "sha256",
        "assets": [
            {key: item.get(key) for key in ("id", "name", "sha256", "size_bytes", "platform", "entrypoints", "release_tag", "release_asset_url")}
            for item in annotated
        ],
    }
    index_path = runtime_root() / f"mcp-bundle-index-{bundle_assets.get('snapshot_id', '')}-{tag}.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {
        **bundle_assets,
        "assets": annotated,
        "index_path": str(index_path),
        "upload_assets": upload,
        "reused_release_assets": reused,
        "asset_catalog_entries": len(catalog),
    }


def release_bundle_assets_verified(
    release_view: dict[str, Any],
    bundle_assets: dict[str, Any],
    *,
    tag: str,
    remote_url: str,
    extra_env: dict[str, str] | None = None,
) -> bool:
    """Require current attachments or an immutable, hash-matching prior asset."""
    current_names = {
        value
        for asset in release_view.get("assets", []) if isinstance(release_view.get("assets"), list) and isinstance(asset, dict)
        for value in (str(asset.get("name") or ""), str(asset.get("label") or ""))
        if value
    }
    catalog: dict[tuple[str, str, int], dict[str, str]] | None = None
    for asset in bundle_assets.get("assets", []) if isinstance(bundle_assets.get("assets"), list) else []:
        if not isinstance(asset, dict):
            return False
        name = str(asset.get("name") or "")
        source_tag = str(asset.get("release_tag") or tag)
        if source_tag == tag:
            if name not in current_names or release_asset_sha256(release_asset(release_view, name)) != str(asset.get("sha256") or ""):
                return False
            continue
        if catalog is None:
            catalog = published_release_asset_catalog(remote_url, extra_env=extra_env)
        expected = (name, str(asset.get("sha256") or ""), int(asset.get("size_bytes") or 0))
        prior = catalog.get(expected)
        if not prior or str(prior.get("release_tag") or "") != source_tag:
            return False
    return True


def git_network_env_for_remote(remote_url: str) -> tuple[dict[str, str], dict[str, Any]]:
    if "github.com" not in str(remote_url or "").lower():
        return {}, {"ok": True, "used": False, "reason": "non_github_remote"}
    gateway = Path(__file__).resolve().parent / "codex_network_gateway.py"
    payload = run_json(
        [
            sys.executable,
            str(gateway),
            "plan",
            "--target-kind",
            "github",
            "--target",
            "https://github.com/",
        ],
        timeout=30,
    )
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    env = plan.get("env") if isinstance(plan.get("env"), dict) else {}
    if not payload.get("ok") or not env:
        return {}, {
            "ok": False,
            "used": False,
            "reason": "network_gateway_plan_unavailable",
            "gateway": {
                "schema": payload.get("schema", ""),
                "reason": payload.get("reason", ""),
                "issues": payload.get("issues", []),
            },
        }
    return {str(key): str(value) for key, value in env.items()}, {
        "ok": True,
        "used": True,
        "route_mode": plan.get("route_mode", ""),
        "route_reason": plan.get("route_reason", ""),
        "target_kind": plan.get("target_kind", "github"),
        "proxy_configured": bool(plan.get("proxy_url")),
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


def snapshot_json_asset(snapshot_id: str, relative_path: str) -> dict[str, Any]:
    if not snapshot_id:
        return {}
    path = mirror_root() / "snapshots" / snapshot_id / relative_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def source_authority_variables() -> dict[str, str]:
    path = mirror_root() / "manifests" / "source-authorities.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    variables = {str(key): str(value) for key, value in (manifest.get("variables") or {}).items()}
    for _ in range(8):
        variables = {key: _expand_manifest_value(value, variables) for key, value in variables.items()}
    return variables


def changed_path_for_mirror_owner(worktree: str, relative_path: str) -> str:
    variables = source_authority_variables()
    configured_work_git_root = str(variables.get("WORK_GIT_ROOT") or "").strip()
    relative = str(relative_path or "").strip().replace("/", "\\")
    if configured_work_git_root:
        return ntpath.join(configured_work_git_root, relative)
    return str((Path(worktree) / relative_path).resolve())


def normalize_changed_path_for_mirror_owner(value: str) -> str:
    """Translate a Work Git WSL path only when the delegated owner is Windows-native."""
    raw = str(value or "").strip()
    if not raw or not str(mirror_root()).startswith("/mnt/"):
        return raw
    configured_work_git_root = str(source_authority_variables().get("WORK_GIT_ROOT") or "").strip()
    if not configured_work_git_root:
        return raw
    relative_parts = tuple(part for part in raw.replace("\\", "/").split("/") if part and part != ".")
    if not raw.startswith("/") and relative_parts and relative_parts[0] in {"workspace", "codex-home"}:
        return ntpath.join(configured_work_git_root, *relative_parts)
    if not raw.startswith("/"):
        return raw
    try:
        relative = Path(raw).resolve().relative_to(WORK_GIT_ROOT.resolve())
    except (OSError, ValueError):
        return raw
    return ntpath.join(configured_work_git_root, *relative.parts)


def normalize_changed_paths_for_mirror_owner(changed_paths: list[str] | None) -> list[str] | None:
    if changed_paths is None:
        return None
    return [normalize_changed_path_for_mirror_owner(path) for path in changed_paths]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_milestone_tag() -> str:
    result = git_result(["tag", "--list", "seed-v*", "--sort=-version:refname"])
    if not result.get("ok"):
        return ""
    return next((line.strip() for line in str(result.get("stdout") or "").splitlines() if SEMANTIC_TAG.match(line.strip())), "")


def parse_semantic_tag(tag: str) -> tuple[int, int, int] | None:
    matched = SEMANTIC_TAG.match(str(tag or "").strip())
    if not matched:
        return None
    return tuple(int(matched.group(name)) for name in ("major", "minor", "patch"))


def next_semantic_tag(tag: str, bump: str) -> str:
    current = parse_semantic_tag(tag) or (0, 0, 0)
    major, minor, patch = current
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"seed-v{major}.{minor}.{patch}"


def control_plane_contract() -> dict[str, Any]:
    path = mirror_root() / "manifests" / "control-plane-contract.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "control_plane_contract_unreadable", "detail": f"{type(exc).__name__}:{exc}"}
    if payload.get("schema") != "codex_mirror.control_plane_contract.v1":
        return {"ok": False, "reason": "control_plane_contract_schema_invalid", "observed": payload.get("schema")}
    return {"ok": True, **payload}


def render_current_state(payload: dict[str, Any]) -> str:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    freshness = payload.get("source_freshness") if isinstance(payload.get("source_freshness"), dict) else {}
    milestone = payload.get("milestone") if isinstance(payload.get("milestone"), dict) else {}
    gaps = payload.get("required_archive_gaps") if isinstance(payload.get("required_archive_gaps"), list) else []
    gap_text = ", ".join(str(item) for item in gaps) if gaps else "none"
    return (
        "# Current Mirror State\n\n"
        "This file is generated by the governed mirror owner. Static contracts are updated only when their semantics change.\n\n"
        f"- Generated at: `{payload.get('generated_at', '')}`\n"
        f"- Control-plane version: `{payload.get('control_plane_version', '')}`\n"
        f"- Snapshot: `{snapshot.get('snapshot_id', '')}`\n"
        f"- Snapshot created at: `{snapshot.get('created_at', '')}`\n"
        f"- Assets: `{snapshot.get('asset_count', 0)}`\n"
        f"- Bytes: `{snapshot.get('total_bytes', 0)}`\n"
        f"- Mirror valid: `{str(bool(readiness.get('mirror_valid'))).lower()}`\n"
        f"- Capability restore ready: `{str(bool(readiness.get('capability_restore_ready'))).lower()}`\n"
        f"- Full-state restore ready: `{str(bool(readiness.get('full_state_restore_ready'))).lower()}`\n"
        f"- Live-source freshness: `{str(freshness.get('ok')).lower()}`\n"
        f"- Latest milestone: `{milestone.get('latest_tag', '') or 'none'}`\n"
        f"- Required external archive gaps: `{gap_text}`\n\n"
        "Machine-readable authority: `manifests/control-plane-state.json`.\n"
    )


def write_control_plane_state(
    snapshot_id: str,
    validation: dict[str, Any],
    *,
    milestone_tag: str = "",
) -> dict[str, Any]:
    root = mirror_root()
    contract = control_plane_contract()
    if not contract.get("ok"):
        return contract
    manifest_path = root / "snapshots" / snapshot_id / "snapshot-manifest.json"
    try:
        snapshot_manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "snapshot_manifest_unreadable", "detail": f"{type(exc).__name__}:{exc}"}
    static_files: list[dict[str, Any]] = []
    for item in contract.get("files", []):
        if not isinstance(item, dict) or item.get("role") != "static_contract":
            continue
        relative = str(item.get("path") or "").replace("\\", "/")
        target = root / Path(relative)
        if not target.is_file():
            return {"ok": False, "reason": "control_plane_static_file_missing", "path": relative}
        static_files.append({"path": relative, "role": "static_contract", "sha256": sha256_file(target)})
    readiness = validation.get("readiness") if isinstance(validation.get("readiness"), dict) else {
        "mirror_valid": validation.get("mirror_valid", False),
        "capability_restore_ready": validation.get("capability_restore_ready", False),
        "full_state_restore_ready": validation.get("full_state_restore_ready", False),
    }
    freshness = validation.get("source_freshness") if isinstance(validation.get("source_freshness"), dict) else {
        "checked": validation.get("source_freshness_checked", False),
        "ok": validation.get("source_freshness_ok"),
    }
    advisories = validation.get("advisories") if isinstance(validation.get("advisories"), dict) else {}
    payload: dict[str, Any] = {
        "schema": "codex_mirror.control_plane_state.v1",
        "generated_at": now_iso(),
        "control_plane_version": contract.get("control_plane_version", ""),
        "snapshot": {
            "snapshot_id": snapshot_id,
            "created_at": snapshot_manifest.get("created_at", ""),
            "asset_count": snapshot_manifest.get("summary", {}).get("asset_count", 0),
            "total_bytes": snapshot_manifest.get("summary", {}).get("total_bytes", 0),
            "capture_mode": snapshot_manifest.get("summary", {}).get("capture_mode", ""),
        },
        "readiness": {
            "mirror_valid": bool(readiness.get("mirror_valid")),
            "capability_restore_ready": bool(readiness.get("capability_restore_ready")),
            "full_state_restore_ready": bool(readiness.get("full_state_restore_ready")),
        },
        "source_freshness": {"checked": bool(freshness.get("checked")), "ok": freshness.get("ok")},
        "milestone": {"latest_tag": milestone_tag or latest_milestone_tag()},
        "required_archive_gaps": list(advisories.get("required_archive_gaps", [])),
        "files": static_files,
        "roles": {
            "static_contract": "changes only when semantics change",
            "generated_current_state": "regenerated transactionally for the current snapshot",
        },
    }
    state_path = root / "manifests" / "control-plane-state.json"
    current_path = root / "CURRENT.md"
    existing: dict[str, Any] = {}
    try:
        existing = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        pass
    comparable = {key: value for key, value in payload.items() if key != "generated_at"}
    existing_comparable = {key: value for key, value in existing.items() if key not in {"generated_at", "current_md_sha256"}}
    if comparable == existing_comparable and current_path.is_file() and existing.get("current_md_sha256") == sha256_file(current_path):
        return {"ok": True, "changed": False, "snapshot_id": snapshot_id, "state_path": str(state_path), "current_path": str(current_path)}
    current_text = render_current_state(payload)
    payload["current_md_sha256"] = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
    current_tmp = current_path.with_suffix(".md.tmp")
    state_tmp = state_path.with_suffix(".json.tmp")
    current_tmp.write_text(current_text, encoding="utf-8", newline="\n")
    state_tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    current_tmp.replace(current_path)
    state_tmp.replace(state_path)
    return {
        "ok": True,
        "changed": True,
        "snapshot_id": snapshot_id,
        "control_plane_version": payload["control_plane_version"],
        "latest_milestone_tag": payload["milestone"]["latest_tag"],
        "state_path": str(state_path),
        "current_path": str(current_path),
    }


def read_control_plane_files() -> dict[Path, bytes | None]:
    paths = (
        mirror_root() / "CURRENT.md",
        mirror_root() / "manifests" / "control-plane-state.json",
    )
    values: dict[Path, bytes | None] = {}
    for path in paths:
        try:
            values[path] = path.read_bytes() if path.is_file() else None
        except OSError:
            values[path] = None
    return values


def restore_control_plane_files(values: dict[Path, bytes | None]) -> None:
    for path, content in values.items():
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".rollback.tmp")
        temporary.write_bytes(content)
        temporary.replace(path)


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


def control_plane_status() -> dict[str, Any]:
    path = mirror_root() / "manifests" / "control-plane-state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "control_plane_state_unreadable", "detail": f"{type(exc).__name__}:{exc}", "path": str(path)}
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    milestone = payload.get("milestone") if isinstance(payload.get("milestone"), dict) else {}
    observed_snapshot_id = str(snapshot.get("snapshot_id") or "")
    expected_snapshot_id = latest_snapshot_id()
    result = {
        "ok": observed_snapshot_id == expected_snapshot_id,
        "version": payload.get("control_plane_version", ""),
        "generated_at": payload.get("generated_at", ""),
        "snapshot_id": observed_snapshot_id,
        "latest_milestone_tag": milestone.get("latest_tag", ""),
        "state_path": str(path),
        "current_path": str(mirror_root() / "CURRENT.md"),
    }
    if not result["ok"]:
        result.update({
            "phase": "control_plane_check",
            "reason": "control_plane_snapshot_mismatch",
            "expected_snapshot_id": expected_snapshot_id,
            "observed_snapshot_id": observed_snapshot_id,
        })
    return result


def status(*, force_fresh: bool = False) -> dict[str, Any]:
    root = mirror_root()
    snapshot_id = latest_snapshot_id()
    cached, cache_age = ({}, None) if force_fresh else load_status_validation_receipt(snapshot_id)
    if cached:
        validation = cached
        validation_state = "cached"
        validation_ref = str(status_validation_cache_path())
    else:
        control_plane_cached, control_plane_age = ({}, None) if force_fresh else control_plane_validation_receipt(snapshot_id)
        if control_plane_cached:
            validation = control_plane_cached
            validation_state = "control_plane_cached"
            cache_age = control_plane_age
            validation_ref = str(root / "manifests" / "control-plane-state.json")
        else:
            owner_validation = run_mirror(["validate", "--live-sources"], timeout=180)
            validation = validation_receipt(owner_validation)
            validation_state = "fresh"
            validation_ref = "command:python _bridge/codex_environment_mirror.py validate"
            if reusable_validation_receipt(validation, snapshot_id):
                validation_ref = persist_status_validation_receipt(validation)
    git_status = git_result(["status", "--short"]) if (root / ".git").is_dir() else {"ok": False, "reason": "git_not_initialized"}
    git_head = git_result(["rev-parse", "--short", "HEAD"]) if git_status.get("ok") else {"ok": False}
    remotes = git_result(["remote"]) if git_status.get("ok") else {"ok": False}
    snapshots = sorted(path.name for path in (root / "snapshots").iterdir() if path.is_dir()) if (root / "snapshots").is_dir() else []
    control_plane = control_plane_status()
    failures: list[dict[str, Any]] = []
    if not validation.get("ok"):
        failures.append(failure_diagnostic(validation, action="validate", source="validation"))
    if not control_plane.get("ok"):
        failures.append(failure_diagnostic(control_plane, action="control_plane", source="control_plane"))
    if not git_status.get("ok"):
        failures.append(failure_diagnostic(git_status, action="git_status", source="git"))
    validation_failure = next((item for item in failures if item.get("source") == "validation"), None)
    control_plane_failure = next((item for item in failures if item.get("source") == "control_plane"), None)
    git_failure = next((item for item in failures if item.get("source") == "git"), None)
    validation_summary = {
        "state": validation_state,
        "receipt_age_seconds": round(cache_age, 1) if validation_state in {"cached", "control_plane_cached"} and cache_age is not None else 0.0,
        "receipt_ref": validation_ref,
        "ttl_seconds": STATUS_VALIDATION_TTL_SECONDS,
        "force_fresh_command": "python _bridge/codex_environment_mirror.py status --force-fresh",
    }
    if validation_failure:
        validation_summary["failure_ref"] = validation_failure.get("artifact_ref", "")
    status_issues = list(validation.get("issues", []))
    if validation_failure and not any(
        isinstance(item, dict) and item.get("code") == validation_failure.get("reason") for item in status_issues
    ):
        status_issues.append({
            "code": validation_failure.get("reason", "mirror_status_failed"),
            "source": validation_failure.get("source", ""),
            "phase": validation_failure.get("phase", ""),
        })
    result = {
        "schema": "codex_environment_mirror.status.v1",
        "ok": not failures,
        "generated_at": now_iso(),
        "mirror_root": str(root),
        "latest_snapshot_id": snapshot_id,
        "snapshot_count": len(snapshots),
        "validation": validation_summary,
        "git": {
            "initialized": (root / ".git").is_dir(),
            "clean": git_status.get("stdout", "") == "",
            "head": git_head.get("stdout", ""),
            "remotes": [item for item in remotes.get("stdout", "").splitlines() if item],
        },
        "readiness": {
            "mirror_valid": (validation.get("readiness") or {}).get("mirror_valid", False),
            "capability_restore_ready": (validation.get("readiness") or {}).get("capability_restore_ready", False),
            "full_state_restore_ready": (validation.get("readiness") or {}).get("full_state_restore_ready", False),
        },
        "source_freshness": {
            "checked": (validation.get("source_freshness") or {}).get("checked", False),
            "ok": (validation.get("source_freshness") or {}).get("ok"),
        },
        "issues": status_issues,
        "advisories": validation.get("advisories", {}),
        "control_plane": control_plane,
    }
    if control_plane_failure:
        result["control_plane_failure"] = control_plane_failure
    if git_failure:
        result["git_failure"] = git_failure
    if failures:
        result["failure"] = failures[0]
        result["failures"] = failures
    return result


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
    network_env, network_route = git_network_env_for_remote(str(remote_url.get("stdout", "")))
    if not branch_name:
        current_branch = git_result(["branch", "--show-current"])
        if not current_branch.get("ok") or not current_branch.get("stdout"):
            return {"ok": False, "reason": "git_branch_unresolved", "detail": current_branch}
        branch_name = str(current_branch["stdout"]).strip()
    head = git_result(["rev-parse", "HEAD"])
    if not head.get("ok") or not head.get("stdout"):
        return {"ok": False, "reason": "git_head_unresolved", "detail": head}
    head_sha = str(head["stdout"]).strip()
    push = git_result(["push", remote_name, f"HEAD:{branch_name}"], timeout=600, extra_env=network_env)
    if not push.get("ok"):
        return {
            "ok": False,
            "reason": "git_push_failed",
            "remote": remote_name,
            "branch": branch_name,
            "head": head_sha,
            "network_route": network_route,
            "detail": push,
        }
    verify = git_result(["ls-remote", "--heads", remote_name, branch_name], timeout=120, extra_env=network_env)
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
            "network_route": network_route,
            "verify": verify,
        }
    return {
        "ok": True,
        "remote": remote_name,
        "remote_url": redact_remote_url(str(remote_url.get("stdout", ""))),
        "branch": branch_name,
        "head": head_sha,
        "network_route": network_route,
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


def reusable_committed_snapshot_for_publish(source_authority: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot_id = latest_snapshot_id()
    if not snapshot_id:
        return {"ok": False, "reason": "latest_snapshot_missing"}
    validation, age = control_plane_validation_receipt(snapshot_id)
    if not reusable_validation_receipt(validation, snapshot_id):
        return {
            "ok": False,
            "reason": "committed_snapshot_not_reusable",
            "snapshot_id": snapshot_id,
            "control_plane_age_seconds": age,
        }
    current_authority = source_authority if isinstance(source_authority, dict) else work_git_release_gate()
    current_work_git = current_authority.get("work_git") if isinstance(current_authority.get("work_git"), dict) else {}
    captured_release = snapshot_json_asset(snapshot_id, "exports/derived/wsl-work-git-release-receipt.json")
    captured_work_git = captured_release.get("work_git") if isinstance(captured_release.get("work_git"), dict) else {}
    current_head = str(current_work_git.get("worktree_head") or "")
    captured_head = str(captured_work_git.get("worktree_head") or "")
    if not captured_release or not captured_head or captured_head != current_head:
        return {
            "ok": False,
            "reason": "snapshot_work_git_head_stale",
            "snapshot_id": snapshot_id,
            "captured_worktree_head": captured_head,
            "current_worktree_head": current_head,
        }
    status_result = git_result(["status", "--short"])
    if not status_result.get("ok"):
        return {"ok": False, "reason": "git_status_failed", "detail": status_result}
    if status_result.get("stdout"):
        return {
            "ok": False,
            "reason": "mirror_worktree_dirty",
            "status": status_result.get("stdout", ""),
        }
    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "validation": validation,
        "control_plane_age_seconds": age,
    }


def work_git_changed_paths_since_latest(source_authority: dict[str, Any]) -> dict[str, Any]:
    snapshot_id = latest_snapshot_id()
    if not snapshot_id:
        return {
            "schema": "codex_environment_mirror.work_git_changed_paths.v1",
            "ok": False,
            "reason": "latest_snapshot_missing",
        }
    captured_release = snapshot_json_asset(snapshot_id, "exports/derived/wsl-work-git-release-receipt.json")
    captured_work_git = captured_release.get("work_git") if isinstance(captured_release.get("work_git"), dict) else {}
    current_work_git = source_authority.get("work_git") if isinstance(source_authority.get("work_git"), dict) else {}
    captured_head = str(captured_work_git.get("worktree_head") or "").strip()
    current_head = str(current_work_git.get("worktree_head") or "").strip()
    worktree = str(current_work_git.get("worktree") or "").strip()
    issues: list[dict[str, Any]] = []
    if not GIT_SHA.match(captured_head):
        issues.append({"code": "captured_work_git_head_missing_or_invalid", "captured_worktree_head": captured_head})
    if not GIT_SHA.match(current_head):
        issues.append({"code": "current_work_git_head_missing_or_invalid", "current_worktree_head": current_head})
    if not worktree:
        issues.append({"code": "work_git_worktree_missing"})
    elif not (Path(worktree) / ".git").exists():
        issues.append({"code": "work_git_worktree_not_found", "worktree": worktree})
    if issues:
        return {
            "schema": "codex_environment_mirror.work_git_changed_paths.v1",
            "ok": False,
            "reason": "work_git_delta_unavailable",
            "snapshot_id": snapshot_id,
            "issues": issues,
        }
    if captured_head == current_head:
        return {
            "schema": "codex_environment_mirror.work_git_changed_paths.v1",
            "ok": True,
            "snapshot_id": snapshot_id,
            "captured_worktree_head": captured_head,
            "current_worktree_head": current_head,
            "changed_paths": [],
            "changed_path_count": 0,
        }
    merge_base = git_result_at(worktree, ["merge-base", "--is-ancestor", captured_head, current_head], timeout=60)
    if merge_base.get("returncode") != 0:
        return {
            "schema": "codex_environment_mirror.work_git_changed_paths.v1",
            "ok": False,
            "reason": "captured_head_not_ancestor",
            "snapshot_id": snapshot_id,
            "captured_worktree_head": captured_head,
            "current_worktree_head": current_head,
            "detail": merge_base,
        }
    diff = git_result_at(
        worktree,
        ["-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=ACMRTD", f"{captured_head}..{current_head}"],
        timeout=120,
    )
    if not diff.get("ok"):
        return {
            "schema": "codex_environment_mirror.work_git_changed_paths.v1",
            "ok": False,
            "reason": "work_git_diff_failed",
            "snapshot_id": snapshot_id,
            "captured_worktree_head": captured_head,
            "current_worktree_head": current_head,
            "detail": diff,
        }
    paths = [changed_path_for_mirror_owner(worktree, line.strip()) for line in str(diff.get("stdout") or "").splitlines() if line.strip()]
    return {
        "schema": "codex_environment_mirror.work_git_changed_paths.v1",
        "ok": True,
        "snapshot_id": snapshot_id,
        "captured_worktree_head": captured_head,
        "current_worktree_head": current_head,
        "changed_paths": paths,
        "changed_path_count": len(paths),
    }


def publish_refresh_scope(changed_paths: list[str] | None, source_authority: dict[str, Any]) -> dict[str, Any]:
    explicit = [str(path) for path in (changed_paths or []) if str(path).strip()]
    delta = work_git_changed_paths_since_latest(source_authority)
    if not delta.get("ok"):
        return {
            "schema": "codex_environment_mirror.publish_refresh_scope.v1",
            "ok": True,
            "mode": "full",
            "changed_paths": [],
            "fallback_reason": delta.get("reason", "work_git_delta_unavailable"),
            "delta": delta,
        }
    derived = list(delta.get("changed_paths", []))
    combined = list(dict.fromkeys([*derived, *explicit]))
    if not combined:
        return {
            "schema": "codex_environment_mirror.publish_refresh_scope.v1",
            "ok": True,
            "mode": "unchanged",
            "changed_paths": [],
            "delta": delta,
        }
    plan = affected_source_plan(combined)
    if plan.get("ok") and plan.get("full_rebuild_required") is False:
        mode = "auto_changed_paths"
        if explicit:
            mode = "explicit_plus_work_git_delta" if derived else "explicit_changed_paths"
        return {
            "schema": "codex_environment_mirror.publish_refresh_scope.v1",
            "ok": True,
            "mode": mode,
            "changed_paths": combined,
            "delta": delta,
            "affected_source_plan": plan,
        }
    return {
        "schema": "codex_environment_mirror.publish_refresh_scope.v1",
        "ok": True,
        "mode": "full",
        "changed_paths": [],
        "fallback_reason": "affected_source_plan_requires_full_rebuild",
        "delta": delta,
        "affected_source_plan": plan,
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
    failure = state.get("failure") if isinstance(state.get("failure"), dict) else {}
    if not state.get("ok") and failure and not any(
        isinstance(item, dict) and item.get("code") == failure.get("reason") for item in issues
    ):
        issues.append({
            "code": failure.get("reason", "mirror_status_failed"),
            "source": failure.get("source", ""),
            "phase": failure.get("phase", ""),
        })
    result = {
        "schema": "codex_environment_mirror.doctor.v1",
        "ok": bool(state.get("ok")) and test_ok,
        "generated_at": now_iso(),
        "status": state,
        "tests": {"ok": test_ok, "summary": (tests.stderr or tests.stdout).strip()[-2000:] if tests else "not_run"},
        "issues": issues,
    }
    if failure:
        result["failure"] = failure
    return result


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
    quarantine = root / ".mirror-retention" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
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


def capture_commit_pathspecs(snapshot_id: str) -> list[str]:
    return [
        ".gitattributes",
        ".gitignore",
        "AGENTS.md",
        "BOOTSTRAP.md",
        "CURRENT.md",
        "MIRROR_POLICY.md",
        "README.md",
        "RESTORE.md",
        "SECURITY.md",
        "manifests",
        "scripts",
        "tests",
        f"snapshots/{snapshot_id}",
        "snapshots/latest.json",
    ]


def retention_commit_pathspecs(removed: list[str], quarantine: Path | None) -> list[str]:
    paths = [f"snapshots/{snapshot_id}" for snapshot_id in removed]
    if quarantine:
        try:
            relative = quarantine.resolve().relative_to(mirror_root().resolve()).as_posix()
        except ValueError as exc:
            raise RuntimeError(f"retention_path_escaped:{quarantine}") from exc
        paths.extend(f"{relative}/{snapshot_id}" for snapshot_id in removed)
    return paths


def commit_refresh(snapshot_id: str, *, phase: str = "capture", pathspecs: list[str] | None = None) -> dict[str, Any]:
    add_args = ["add", "-A"]
    if pathspecs is not None:
        add_args.extend(["--", *pathspecs])
    elif phase == "retention":
        add_args.extend(["--", "snapshots"])
    elif phase == "retention-cleanup":
        add_args.extend(["--", ".mirror-retention"])
    add = git_result(add_args)
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


def commit_retention_cleanup(snapshot_id: str) -> dict[str, Any]:
    retention_root = (mirror_root() / ".mirror-retention").resolve()
    try:
        retention_root.relative_to(mirror_root().resolve())
    except ValueError:
        return {"ok": False, "reason": "retention_path_escaped", "path": str(retention_root)}
    tracked = git_result(["ls-files", "--", ".mirror-retention"])
    if not tracked.get("ok"):
        return {"ok": False, "reason": "retention_index_read_failed", "detail": tracked}
    shutil.rmtree(retention_root, ignore_errors=True)
    if not tracked.get("stdout", "").strip():
        head = git_result(["rev-parse", "--short", "HEAD"])
        return {"ok": True, "committed": False, "head": head.get("stdout", "")}
    return commit_refresh(snapshot_id, phase="retention-cleanup")


def _refresh_unlocked(confirm: str, changed_paths: list[str] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    if confirm != REFRESH_CONFIRMATION:
        return {
            "schema": "codex_environment_mirror.refresh.v1",
            "ok": False,
            "reason": "confirmation_required",
            "required_confirmation": REFRESH_CONFIRMATION,
        }
    source_authority = work_git_release_gate()
    if not source_authority.get("ok"):
        return {
            "schema": "codex_environment_mirror.refresh.v1",
            "ok": False,
            "phase": "work_git_release_gate",
            "reason": "work_git_release_not_ready",
            "source_authority": source_authority,
            "next_action": "resolve the reported Work Git or source-authority issue; do not fall back to the native workspace",
        }
    previous_pointer, previous_snapshot_id, reconciliation = stable_previous_pointer()
    previous_control_plane = read_control_plane_files()
    plan = run_mirror(["plan"], timeout=180)
    if not plan.get("ok"):
        return {"schema": "codex_environment_mirror.refresh.v1", "ok": False, "phase": "plan", "result": plan, "source_authority": source_authority}
    committed_snapshot_id = pointer_snapshot_id(committed_latest_pointer())
    if not changed_paths and previous_snapshot_id and previous_snapshot_id == committed_snapshot_id:
        core_validation = run_mirror(
            ["validate", "--live-sources", "--snapshot", previous_snapshot_id, "--skip-control-plane"],
            timeout=300,
        )
        control_plane = write_control_plane_state(previous_snapshot_id, core_validation) if core_validation.get("ok") else {
            "ok": False,
            "reason": "core_validation_failed",
        }
        snapshot_validation = run_mirror(
            ["control-plane-validate", "--snapshot", previous_snapshot_id],
            timeout=120,
        ) if control_plane.get("ok") else core_validation
        current_validation = (
            merge_live_source_validation(snapshot_validation, core_validation)
            if control_plane.get("ok")
            else core_validation
        )
        reusable = bool(
            current_validation.get("ok")
            and current_validation.get("mirror_valid")
            and current_validation.get("capability_restore_ready")
            and current_validation.get("source_freshness_checked")
            and current_validation.get("source_freshness_ok") is True
        )
        if reusable:
            commit = commit_refresh(previous_snapshot_id, phase="control-plane")
            if not commit.get("ok"):
                unstage_all()
                restore_control_plane_files(previous_control_plane)
                return {
                    "schema": "codex_environment_mirror.refresh.v1",
                    "ok": False,
                    "phase": "control_plane_commit",
                    "snapshot_id": previous_snapshot_id,
                    "control_plane": control_plane,
                    "commit": commit,
                }
            retention_cleanup_commit = commit_retention_cleanup(previous_snapshot_id)
            if not retention_cleanup_commit.get("ok"):
                return {
                    "schema": "codex_environment_mirror.refresh.v1",
                    "ok": False,
                    "phase": "retention_cleanup_commit",
                    "snapshot_id": previous_snapshot_id,
                    "commit": commit,
                    "retention_cleanup_commit": retention_cleanup_commit,
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
                "retention_cleanup_commit": retention_cleanup_commit,
                "attempts": [],
                "reconciliation": reconciliation,
                "control_plane": control_plane,
                "readiness": {
                    "mirror_valid": True,
                    "capability_restore_ready": True,
                    "full_state_restore_ready": current_validation.get("full_state_restore_ready", False),
                },
                "source_freshness": {"checked": True, "ok": True},
                "advisories": current_validation.get("advisories", {}),
                "validation": validation_receipt(current_validation),
                "source_authority": source_authority,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }
    attempts: list[dict[str, Any]] = []
    snapshot: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    snapshot_validation: dict[str, Any] = {}
    snapshot_id = ""
    attempt_changed_paths = list(changed_paths or [])
    for attempt in range(1, REFRESH_MAX_ATTEMPTS + 1):
        snapshot_args = ["snapshot", "--apply"]
        for path in attempt_changed_paths:
            snapshot_args.extend(["--changed", path])
        snapshot, snapshot_id, core_validation, capture_lease = capture_snapshot_and_live_validate(snapshot_args)
        if not snapshot.get("ok"):
            if snapshot.get("reason") == "source_capture_not_quiescent" and snapshot.get("candidate_created") is False:
                return {
                    "schema": "codex_environment_mirror.refresh.v1",
                    "ok": False,
                    "phase": "source_quiescence",
                    "attempt": attempt,
                    "reason": "source_capture_not_quiescent",
                    "candidate_created": False,
                    "result": snapshot,
                    "reconciliation": reconciliation,
                    "source_authority": source_authority,
                    "next_action": "wait_for_the_declared_mutable_sources_to_stabilize_then_retry_one_refresh",
                }
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
                "source_authority": source_authority,
            }
        if core_validation.get("ok"):
            control_plane = write_control_plane_state(snapshot_id, core_validation)
            snapshot_validation = run_mirror(
                ["control-plane-validate", "--snapshot", snapshot_id],
                timeout=120,
            ) if control_plane.get("ok") else {
                "ok": False,
                "issues": [{"code": "control_plane_generation_failed", **control_plane}],
            }
            validation = (
                merge_live_source_validation(snapshot_validation, core_validation)
                if control_plane.get("ok")
                else snapshot_validation
            )
        else:
            control_plane = {"ok": False, "reason": "core_validation_failed"}
            validation = core_validation
        if validation.get("ok"):
            attempts.append({"attempt": attempt, "snapshot_id": snapshot_id, "ok": True, "capture_lease": capture_lease})
            break
        retryable = validation_is_retryable(validation)
        removed = remove_snapshot_candidate(snapshot_id, protected={previous_snapshot_id})
        restore_latest_pointer(previous_pointer)
        restore_control_plane_files(previous_control_plane)
        next_capture_mode = "full" if retryable and attempt_changed_paths else "same_scope"
        attempts.append({
            "attempt": attempt,
            "snapshot_id": snapshot_id,
            "ok": False,
            "retryable": retryable,
            "issue_codes": sorted(validation_issue_codes(validation)),
            "next_capture_mode": next_capture_mode,
            "candidate_removed": removed,
            "capture_lease": capture_lease,
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
                "source_authority": source_authority,
            }
        if next_capture_mode == "full":
            attempt_changed_paths = []
        delay = REFRESH_RETRY_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0.0, REFRESH_RETRY_BASE_SECONDS)
        time.sleep(delay)
    commit = commit_refresh(snapshot_id, phase="capture", pathspecs=capture_commit_pathspecs(snapshot_id))
    if not commit.get("ok"):
        unstage_all()
        removed_candidate = remove_snapshot_candidate(snapshot_id, protected={previous_snapshot_id})
        restore_latest_pointer(previous_pointer)
        restore_control_plane_files(previous_control_plane)
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
            "source_authority": source_authority,
        }
    removed, quarantine = quarantine_superseded_snapshots(snapshot_id)
    retention_commit = (
        commit_refresh(snapshot_id, phase="retention", pathspecs=retention_commit_pathspecs(removed, quarantine))
        if removed
        else {"ok": True, "committed": False, "head": commit.get("head", "")}
    )
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
            "source_authority": source_authority,
        }
    discard_quarantine(quarantine)
    retention_cleanup_commit = commit_retention_cleanup(snapshot_id)
    if not retention_cleanup_commit.get("ok"):
        return {
            "schema": "codex_environment_mirror.refresh.v1",
            "ok": False,
            "phase": "retention_cleanup_commit",
            "snapshot_id": snapshot_id,
            "removed_snapshots": removed,
            "commit": commit,
            "retention_commit": retention_commit,
            "retention_cleanup_commit": retention_cleanup_commit,
            "attempts": attempts,
            "reconciliation": reconciliation,
            "source_authority": source_authority,
        }
    return {
        "schema": "codex_environment_mirror.refresh.v1",
        "ok": True,
        "generated_at": now_iso(),
        "snapshot_id": snapshot_id,
        "removed_snapshots": removed,
        "commit": commit,
        "retention_commit": retention_commit,
        "retention_cleanup_commit": retention_cleanup_commit,
        "attempts": attempts,
        "reconciliation": reconciliation,
        "control_plane": control_plane,
        "readiness": {
            "mirror_valid": validation.get("mirror_valid", False),
            "capability_restore_ready": validation.get("capability_restore_ready", False),
            "full_state_restore_ready": validation.get("full_state_restore_ready", False),
        },
        "source_freshness": {"checked": True, "ok": validation.get("source_freshness_ok")},
        "advisories": validation.get("advisories", {}),
        "validation": validation_receipt(validation),
        "source_authority": source_authority,
        "phase_timings_ms": {
            "plan_owner": plan.get("_owner_elapsed_ms"),
            "snapshot_and_live_validation_owner": snapshot.get("_owner_elapsed_ms"),
            "control_plane_validation_owner": snapshot_validation.get("_owner_elapsed_ms"),
        },
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def refresh(confirm: str, changed_paths: list[str] | None = None) -> dict[str, Any]:
    normalized_changed_paths = normalize_changed_paths_for_mirror_owner(changed_paths)
    if confirm != REFRESH_CONFIRMATION:
        return _refresh_unlocked(confirm, normalized_changed_paths)
    lock_path = runtime_root() / "locks" / "refresh.lock"
    try:
        with exclusive_operation_lock(lock_path, "refresh"):
            return _refresh_unlocked(confirm, normalized_changed_paths)
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
    started = time.perf_counter()
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
            source_authority = work_git_release_gate()
            if not source_authority.get("ok"):
                return {
                    "schema": "codex_environment_mirror.publish.v1",
                    "ok": False,
                    "phase": "work_git_release_gate",
                    "reason": "work_git_release_not_ready",
                    "source_authority": source_authority,
                    "next_action": "resolve the reported Work Git or source-authority issue before mirror publish",
                }
            refresh_scope = publish_refresh_scope(changed_paths, source_authority)
            effective_changed_paths = list(refresh_scope.get("changed_paths", []))
            # Explicit changed paths describe what a refresh should capture;
            # they do not invalidate a committed snapshot that already proves
            # it covers the current Work Git HEAD. This permits an explicit
            # refresh followed by publish without capturing the same source a
            # second time.
            committed_candidate = reusable_committed_snapshot_for_publish(source_authority)
            if committed_candidate.get("ok"):
                snapshot_id = str(committed_candidate.get("snapshot_id") or "")
                pushed = push_receipt(remote=remote, branch=branch)
                if not pushed.get("ok"):
                    return {
                        "schema": "codex_environment_mirror.publish.v1",
                        "ok": False,
                        "phase": "push",
                        "snapshot_id": snapshot_id,
                        "resumed": True,
                        "reason": "committed_snapshot_push_failed",
                        "source_authority": source_authority,
                        "refresh_scope": refresh_scope,
                        "validation": committed_candidate.get("validation", {}),
                        "push": pushed,
                    }
                return {
                    "schema": "codex_environment_mirror.publish.v1",
                    "ok": True,
                    "generated_at": now_iso(),
                    "snapshot_id": snapshot_id,
                    "resumed": True,
                    "reason": (
                        "committed_snapshot_reused_after_explicit_refresh"
                        if effective_changed_paths
                        else "committed_snapshot_reused_for_push"
                    ),
                    "source_authority": source_authority,
                    "refresh_scope": refresh_scope,
                    "validation": committed_candidate.get("validation", {}),
                    "validation_reused_from_control_plane": True,
                    "push": pushed,
                    "readiness": (committed_candidate.get("validation") or {}).get("readiness", {}),
                    "source_freshness": (committed_candidate.get("validation") or {}).get("source_freshness", {}),
                    "advisories": (committed_candidate.get("validation") or {}).get("advisories", {}),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            refreshed = refresh(REFRESH_CONFIRMATION, effective_changed_paths)
            if not refreshed.get("ok"):
                return {
                    "schema": "codex_environment_mirror.publish.v1",
                    "ok": False,
                    "phase": "refresh",
                    "refresh_scope": refresh_scope,
                    "refresh": refreshed,
                }
            snapshot_id = str(refreshed.get("snapshot_id") or latest_snapshot_id())
            validation = refreshed.get("validation") if isinstance(refreshed.get("validation"), dict) else {}
            validation_reused = reusable_validation_receipt(validation, snapshot_id)
            if not validation_reused:
                validation = validation_receipt(
                    run_mirror(["validate", "--live-sources", "--snapshot", snapshot_id], timeout=300)
                )
            validation_ok = reusable_validation_receipt(validation, snapshot_id)
            if not validation_ok:
                return {
                    "schema": "codex_environment_mirror.publish.v1",
                    "ok": False,
                    "phase": "validate",
                    "snapshot_id": snapshot_id,
                    "refresh_scope": refresh_scope,
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
                    "refresh_scope": refresh_scope,
                    "refresh": refreshed,
                    "validation": validation,
                    "metadata_commit": metadata_commit,
                }
            pushed = push_receipt(remote=remote, branch=branch)
            if not pushed.get("ok"):
                return {
                    "schema": "codex_environment_mirror.publish.v1",
                    "ok": False,
                    "phase": "push",
                    "snapshot_id": snapshot_id,
                    "refresh_scope": refresh_scope,
                    "refresh": refreshed,
                    "validation": validation,
                    "metadata_commit": metadata_commit,
                    "push": pushed,
                }
            return {
                "schema": "codex_environment_mirror.publish.v1",
                "ok": True,
                "generated_at": now_iso(),
                "snapshot_id": snapshot_id,
                "refresh_scope": refresh_scope,
                "refresh": refreshed,
                "validation": validation,
                "validation_reused_from_refresh": validation_reused,
                "metadata_commit": metadata_commit,
                "push": pushed,
                "readiness": {
                    "mirror_valid": validation.get("readiness", {}).get("mirror_valid", False),
                    "capability_restore_ready": validation.get("readiness", {}).get("capability_restore_ready", False),
                    "full_state_restore_ready": validation.get("readiness", {}).get("full_state_restore_ready", False),
                },
                "source_freshness": validation.get("source_freshness", {}),
                "advisories": validation.get("advisories", {}),
                "phase_timings_ms": {
                    **(refreshed.get("phase_timings_ms") or {}),
                    "publish_total": round((time.perf_counter() - started) * 1000, 1),
                },
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
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


def release_plan() -> dict[str, Any]:
    current_tag = latest_milestone_tag()
    base = current_tag or ""
    committed = git_result(["-c", "core.quotepath=false", "diff", "--name-only", f"{base}..HEAD"]) if base else git_result(["ls-files"])
    unstaged = git_result(["-c", "core.quotepath=false", "diff", "--name-only"])
    staged = git_result(["-c", "core.quotepath=false", "diff", "--cached", "--name-only"])
    untracked = git_result(["-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard"])
    dirty = git_result(["status", "--short"])
    paths: set[str] = set()
    for result in (committed, unstaged, staged, untracked):
        paths.update(line.strip().replace("\\", "/") for line in str(result.get("stdout") or "").splitlines() if line.strip())
    generated_metadata = {"CURRENT.md", "manifests/control-plane-state.json", "manifests/contract-review-state.json"}
    non_snapshot = sorted(path for path in paths if not path.startswith("snapshots/") and path not in generated_metadata)
    reviewed_impact = ""
    receipt_path = mirror_root() / "manifests" / "contract-review-state.json"
    try:
        reviewed_receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        if reviewed_receipt.get("control_plane_fingerprint") == control_plane_fingerprint():
            candidate_impact = str(reviewed_receipt.get("release_impact") or "")
            if candidate_impact in {"patch", "minor", "major"}:
                reviewed_impact = candidate_impact
    except (OSError, json.JSONDecodeError):
        pass
    if not non_snapshot:
        bump = "none"
        reason = "snapshot_only_or_no_change"
    elif reviewed_impact:
        bump = reviewed_impact
        reason = "codex_reviewed_semantic_impact"
    elif any(path.startswith("manifests/schemas/") or path == "manifests/restore-order.json" for path in non_snapshot):
        bump = "minor"
        reason = "potential_breaking_change_requires_codex_review"
    elif all(path.endswith(".md") or path.startswith("tests/") for path in non_snapshot):
        bump = "patch"
        reason = "documentation_or_tests_only"
    else:
        bump = "minor"
        reason = "control_plane_or_capability_changed"
    candidate = next_semantic_tag(current_tag, bump) if bump != "none" else ""
    return {
        "schema": "codex_environment_mirror.release_plan.v1",
        "ok": all(result.get("ok") for result in (committed, unstaged, staged, untracked, dirty)),
        "generated_at": now_iso(),
        "current_tag": current_tag,
        "release_recommended": bump != "none",
        "recommended_bump": bump,
        "recommended_tag": candidate,
        "reason": reason,
        "changed_path_count": len(paths),
        "non_snapshot_change_count": len(non_snapshot),
        "non_snapshot_changes": non_snapshot[:50],
        "snapshot_change_count": sum(1 for path in paths if path.startswith("snapshots/")),
        "generated_metadata_change_count": sum(1 for path in paths if path in generated_metadata),
        "git_clean": not bool(dirty.get("stdout")),
    }


def control_plane_fingerprint() -> str:
    contract = control_plane_contract()
    rows: list[dict[str, str]] = []
    if not contract.get("ok"):
        return ""
    for item in contract.get("files", []):
        if not isinstance(item, dict) or item.get("role") != "static_contract":
            continue
        relative = str(item.get("path") or "").replace("\\", "/")
        target = mirror_root() / Path(relative)
        if not target.is_file():
            return ""
        rows.append({"path": relative, "sha256": sha256_file(target)})
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def contract_review_plan() -> dict[str, Any]:
    milestone = release_plan()
    changes = milestone.get("non_snapshot_changes") if isinstance(milestone.get("non_snapshot_changes"), list) else []
    stable_docs = ["AGENTS.md", "README.md", "MIRROR_POLICY.md", "BOOTSTRAP.md", "RESTORE.md", "SECURITY.md"]
    docs_only = bool(changes) and all(str(path).endswith(".md") or str(path).startswith("tests/") for path in changes)
    if not changes:
        required: list[str] = []
    elif docs_only:
        required = sorted({str(path) for path in changes if str(path) in stable_docs} or {"README.md"})
    else:
        required = stable_docs
    fingerprint = control_plane_fingerprint()
    receipt_path = mirror_root() / "manifests" / "contract-review-state.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        receipt = {}
    decisions = receipt.get("decisions") if isinstance(receipt.get("decisions"), list) else []
    reviewed = {
        str(item.get("path") or "")
        for item in decisions
        if isinstance(item, dict) and item.get("disposition") in {"updated", "compatible"}
    }
    current = bool(
        fingerprint
        and receipt.get("schema") == "codex_mirror.contract_review_state.v1"
        and receipt.get("control_plane_fingerprint") == fingerprint
        and set(required).issubset(reviewed)
    )
    return {
        "schema": "codex_environment_mirror.contract_review_plan.v1",
        "ok": bool(milestone.get("ok")) and bool(fingerprint),
        "generated_at": now_iso(),
        "control_plane_fingerprint": fingerprint,
        "required_review_files": required,
        "changed_control_plane_files": changes,
        "review_current": current,
        "receipt_path": str(receipt_path),
        "allowed_dispositions": ["updated", "compatible"],
        "codex_action": "read each required contract against actual changes; edit when semantics changed; otherwise record compatible; validate before recording",
        "release_plan": milestone,
    }


def contract_review(confirm: str, *, decisions: list[str], summary: str = "", release_impact: str = "", remote: str = "", branch: str = "") -> dict[str, Any]:
    if confirm != CONTRACT_REVIEW_CONFIRMATION:
        return {
            "schema": "codex_environment_mirror.contract_review.v1",
            "ok": False,
            "reason": "confirmation_required",
            "required_confirmation": CONTRACT_REVIEW_CONFIRMATION,
        }
    plan = contract_review_plan()
    parsed: list[dict[str, str]] = []
    for value in decisions:
        path, separator, disposition = str(value).partition("=")
        if separator and disposition in {"updated", "compatible"}:
            parsed.append({"path": path.replace("\\", "/").strip(), "disposition": disposition})
    reviewed = {item["path"] for item in parsed}
    missing = sorted(set(plan.get("required_review_files", [])) - reviewed)
    if not plan.get("ok") or missing:
        return {
            "schema": "codex_environment_mirror.contract_review.v1",
            "ok": False,
            "reason": "contract_review_incomplete",
            "missing_review_files": missing,
            "plan": plan,
        }
    impact = release_impact or str(plan.get("release_plan", {}).get("recommended_bump") or "")
    if impact not in {"patch", "minor", "major"}:
        return {
            "schema": "codex_environment_mirror.contract_review.v1",
            "ok": False,
            "reason": "release_impact_required",
            "allowed_release_impacts": ["patch", "minor", "major"],
        }
    payload = {
        "schema": "codex_mirror.contract_review_state.v1",
        "reviewed_at": now_iso(),
        "reviewed_by": "codex",
        "control_plane_fingerprint": plan.get("control_plane_fingerprint"),
        "baseline_tag": plan.get("release_plan", {}).get("current_tag", ""),
        "proposed_tag": plan.get("release_plan", {}).get("recommended_tag", ""),
        "changed_control_plane_files": plan.get("changed_control_plane_files", []),
        "decisions": parsed,
        "release_impact": impact,
        "summary": summary,
        "validation": "all required stable contracts read against current control-plane changes",
    }
    path = mirror_root() / "manifests" / "contract-review-state.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)
    commit = commit_refresh(latest_snapshot_id(), phase="contract-review")
    if not commit.get("ok"):
        unstage_all()
        return {"schema": "codex_environment_mirror.contract_review.v1", "ok": False, "phase": "commit", "commit": commit}
    pushed = push_receipt(remote=remote, branch=branch)
    if not pushed.get("ok"):
        return {"schema": "codex_environment_mirror.contract_review.v1", "ok": False, "phase": "push", "commit": commit, "push": pushed}
    verified = contract_review_plan()
    return {
        "schema": "codex_environment_mirror.contract_review.v1",
        "ok": bool(verified.get("review_current")),
        "generated_at": now_iso(),
        "receipt_path": str(path),
        "reviewed_files": sorted(reviewed),
        "commit": commit,
        "push": pushed,
        "verification": verified,
    }


def release_notes(tag: str, plan: dict[str, Any], validation: dict[str, Any]) -> str:
    snapshot_id = str(validation.get("snapshot_id") or latest_snapshot_id())
    readiness = validation.get("readiness") if isinstance(validation.get("readiness"), dict) else {}
    advisories = validation.get("advisories") if isinstance(validation.get("advisories"), dict) else {}
    changes = plan.get("non_snapshot_changes") if isinstance(plan.get("non_snapshot_changes"), list) else []
    change_lines = "\n".join(f"- `{item}`" for item in changes) or "- Snapshot-only milestone"
    gaps = advisories.get("required_archive_gaps") if isinstance(advisories.get("required_archive_gaps"), list) else []
    gap_lines = "\n".join(f"- `{item}`" for item in gaps) or "- None"
    return (
        f"# {tag}\n\n"
        "Governed Codex environment recovery milestone.\n\n"
        "## Recovery State\n\n"
        f"- Snapshot: `{snapshot_id}`\n"
        f"- Mirror valid: `{str(bool(readiness.get('mirror_valid'))).lower()}`\n"
        f"- Capability restore ready: `{str(bool(readiness.get('capability_restore_ready'))).lower()}`\n"
        f"- Full-state restore ready: `{str(bool(readiness.get('full_state_restore_ready'))).lower()}`\n\n"
        "## Control-Plane Changes\n\n"
        f"{change_lines}\n\n"
        "## External Archive Gaps\n\n"
        f"{gap_lines}\n\n"
        "The attached `snapshot-manifest.json` is the machine-readable asset and hash receipt for this milestone.\n"
    )


def release_view_payload(result: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(result.get("stdout") or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def release_has_manifest_attachment(view: dict[str, Any]) -> bool:
    return release_has_required_attachments(view, {"snapshot-manifest.json"})


def release_asset_matches(asset: dict[str, Any], expected: str) -> bool:
    return expected in {
        str(asset.get("name") or ""),
        str(asset.get("label") or ""),
    }


def release_asset(view: dict[str, Any], expected: str) -> dict[str, Any]:
    assets = view.get("assets") if isinstance(view.get("assets"), list) else []
    return next(
        (
            asset
            for asset in assets
            if isinstance(asset, dict) and release_asset_matches(asset, expected)
        ),
        {},
    )


def release_asset_sha256(asset: dict[str, Any]) -> str:
    digest = str(asset.get("digest") or "")
    return digest.removeprefix("sha256:") if digest.startswith("sha256:") else ""


def release_has_required_attachments(view: dict[str, Any], expected: set[str]) -> bool:
    assets = view.get("assets") if isinstance(view.get("assets"), list) else []
    names = {
        value
        for asset in assets
        if isinstance(asset, dict)
        for value in (str(asset.get("name") or ""), str(asset.get("label") or ""))
        if value
    }
    return expected.issubset(names)


def git_file_bytes(ref: str, path: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=mirror_root(),
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}
    return {
        "ok": completed.returncode == 0,
        "content": completed.stdout,
        "stderr_tail": completed.stderr[-FAILURE_TAIL_LIMIT:].decode("utf-8", errors="replace"),
    }


def remote_tag_commit(remote: str, tag: str, local_tag_commit: str, *, extra_env: dict[str, str] | None = None) -> str:
    """Resolve an immutable remote tag across servers that omit peeled refs."""
    remote = git_result(["ls-remote", "--tags", remote, f"refs/tags/{tag}"], extra_env=extra_env)
    if not remote.get("ok"):
        return ""
    entries = [line.split(None, 1) for line in str(remote.get("stdout") or "").splitlines() if line.strip()]
    for entry in entries:
        if len(entry) == 2 and entry[1] == f"refs/tags/{tag}^{{}}":
            return entry[0]
    remote_ref = next((entry[0] for entry in entries if len(entry) == 2 and entry[1] == f"refs/tags/{tag}"), "")
    local_ref = git_result(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"])
    local_ref_sha = str(local_ref.get("stdout") or "").strip()
    # An equal annotated-tag object hash authenticates its immutable target; a
    # lightweight tag has the commit SHA in both fields.
    return local_tag_commit if remote_ref and remote_ref == local_ref_sha else ""


def tagged_release_snapshot(tag: str) -> dict[str, Any]:
    latest_result = git_file_bytes(tag, "snapshots/latest.json")
    try:
        latest = json.loads(bytes(latest_result.get("content") or b"").decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        latest = {}
    snapshot_id = str(latest.get("snapshot_id") or "") if isinstance(latest, dict) else ""
    if not latest_result.get("ok") or not snapshot_id:
        return {"ok": False, "reason": "tagged_snapshot_pointer_unreadable", "tag": tag}
    relative_path = f"snapshots/{snapshot_id}/snapshot-manifest.json"
    manifest_result = git_file_bytes(tag, relative_path)
    manifest_bytes = bytes(manifest_result.get("content") or b"")
    if not manifest_result.get("ok") or not manifest_bytes:
        return {
            "ok": False,
            "reason": "tagged_snapshot_manifest_unreadable",
            "tag": tag,
            "snapshot_id": snapshot_id,
        }
    return {
        "ok": True,
        "tag": tag,
        "snapshot_id": snapshot_id,
        "manifest_bytes": manifest_bytes,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


def release_index_matches(path: Path, snapshot_id: str, bundle_assets: dict[str, Any]) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or str(payload.get("snapshot_id") or "") != snapshot_id:
        return False
    actual = {
        str(item.get("name") or ""): (
            str(item.get("sha256") or ""),
            int(item.get("size_bytes") or 0),
        )
        for item in payload.get("assets", [])
        if isinstance(item, dict)
    }
    expected = {
        str(item.get("name") or ""): (
            str(item.get("sha256") or ""),
            int(item.get("size_bytes") or 0),
        )
        for item in bundle_assets.get("assets", [])
        if isinstance(item, dict)
    }
    return bool(expected) and actual == expected


def release_upload_timeout(files: list[str]) -> int:
    total_bytes = 0
    for item in files:
        try:
            total_bytes += Path(item.rsplit("#", 1)[0]).stat().st_size
        except OSError:
            continue
    # Budget for a conservative 1 MiB/s route plus API setup/finalization.
    return max(300, min(3600, 180 + (total_bytes + (1024 * 1024 - 1)) // (1024 * 1024)))


def resume_ancestor_release_draft(
    tag: str,
    *,
    remote: str = "",
) -> dict[str, Any]:
    """Resume an immutable draft when mirror-only commits advanced main after tagging."""
    remote_name = (remote or "origin").strip()
    head = git_result(["rev-parse", "HEAD"])
    head_sha = str(head.get("stdout") or "").strip()
    local_tag = git_result(["rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"])
    tag_sha = str(local_tag.get("stdout") or "").strip()
    ancestor = git_result(["merge-base", "--is-ancestor", f"refs/tags/{tag}", "HEAD"])
    if (
        not head.get("ok")
        or not local_tag.get("ok")
        or not GIT_SHA.match(head_sha)
        or not GIT_SHA.match(tag_sha)
        or tag_sha == head_sha
        or not ancestor.get("ok")
    ):
        return {
            "ok": False,
            "reason": "existing_tag_not_resumable_ancestor",
            "tag": tag,
            "tag_head": tag_sha,
            "expected_head": head_sha,
            "terminal": True,
        }
    remote_url = git_result(["remote", "get-url", remote_name])
    if not remote_url.get("ok"):
        return {"ok": False, "reason": "git_remote_missing", "terminal": True}
    network_env, network_route = git_network_env_for_remote(str(remote_url.get("stdout") or ""))
    remote_tag_sha = remote_tag_commit(remote_name, tag, tag_sha, extra_env=network_env)
    if remote_tag_sha != tag_sha:
        return {
            "ok": False,
            "reason": "remote_tag_head_mismatch",
            "tag": tag,
            "remote_tag_head": remote_tag_sha,
            "expected_tag_head": tag_sha,
            "terminal": True,
        }
    view_result = gh_result(
        ["release", "view", tag, "--json", "tagName,isDraft,url,targetCommitish,name,assets"],
        extra_env=network_env,
    )
    view = release_view_payload(view_result)
    if not view_result.get("ok") or view.get("tagName") != tag or view.get("isDraft") is not True:
        return {
            "ok": False,
            "reason": "ancestor_tag_release_not_draft",
            "tag": tag,
            "release": view,
            "terminal": True,
        }
    tagged_snapshot = tagged_release_snapshot(tag)
    if not tagged_snapshot.get("ok"):
        return {**tagged_snapshot, "terminal": True}
    snapshot_id = str(tagged_snapshot["snapshot_id"])
    bundle_assets = mcp_release_assets(snapshot_id)
    if not bundle_assets.get("ok"):
        return {
            "ok": False,
            "reason": bundle_assets.get("reason", "mcp_release_assets_not_ready"),
            "mcp_release_assets": bundle_assets,
            "terminal": True,
        }

    resume_root = runtime_root() / "release-resume" / tag
    if resume_root.exists():
        shutil.rmtree(resume_root)
    resume_root.mkdir(parents=True, exist_ok=True)
    manifest_path = resume_root / "snapshot-manifest.json"
    manifest_path.write_bytes(bytes(tagged_snapshot["manifest_bytes"]))

    manifest_asset = release_asset(view, "snapshot-manifest.json")
    if manifest_asset and release_asset_sha256(manifest_asset) != tagged_snapshot["manifest_sha256"]:
        return {
            "ok": False,
            "reason": "draft_manifest_does_not_match_tag",
            "tag": tag,
            "snapshot_id": snapshot_id,
            "terminal": True,
        }
    index_asset = release_asset(view, "mcp-bundle-index.json")
    if index_asset:
        index_name = str(index_asset.get("name") or "")
        downloaded = gh_result(
            ["release", "download", tag, "--pattern", index_name, "--dir", str(resume_root), "--clobber"],
            timeout=300,
            extra_env=network_env,
        )
        downloaded_index = resume_root / index_name
        if not downloaded.get("ok") or not release_index_matches(downloaded_index, snapshot_id, bundle_assets):
            return {
                "ok": False,
                "reason": "draft_mcp_index_does_not_match_tagged_bundle",
                "tag": tag,
                "snapshot_id": snapshot_id,
                "terminal": True,
            }

    missing_files: list[str] = []
    if not manifest_asset:
        missing_files.append(f"{manifest_path}#snapshot-manifest.json")
    if not index_asset:
        missing_files.append(f"{bundle_assets['index_path']}#mcp-bundle-index.json")
    for item in bundle_assets.get("assets", []):
        expected_name = str(item.get("name") or "")
        asset = release_asset(view, expected_name)
        if not asset:
            missing_files.append(f"{item['path']}#{expected_name}")
            continue
        if release_asset_sha256(asset) != str(item.get("sha256") or ""):
            return {
                "ok": False,
                "reason": "draft_mcp_asset_hash_mismatch",
                "tag": tag,
                "asset": expected_name,
                "terminal": True,
            }
    if missing_files:
        uploaded = gh_result(
            ["release", "upload", tag, *missing_files, "--clobber"],
            timeout=release_upload_timeout(missing_files),
            extra_env=network_env,
        )
        if not uploaded.get("ok"):
            return {
                "ok": False,
                "phase": "upload_release_assets",
                "reason": "ancestor_draft_upload_failed",
                "detail": uploaded,
            }
    published = gh_result(["release", "edit", tag, "--draft=false"], timeout=180, extra_env=network_env)
    if not published.get("ok"):
        return {"ok": False, "phase": "publish_release", "detail": published}
    verified = gh_result(
        ["release", "view", tag, "--json", "tagName,isDraft,url,targetCommitish,name,assets"],
        extra_env=network_env,
    )
    release_view = release_view_payload(verified)
    required = {"snapshot-manifest.json", "mcp-bundle-index.json", *(item["name"] for item in bundle_assets.get("assets", []))}
    if (
        not verified.get("ok")
        or release_view.get("tagName") != tag
        or release_view.get("isDraft") is not False
        or not release_has_required_attachments(release_view, required)
        or release_asset_sha256(release_asset(release_view, "snapshot-manifest.json")) != tagged_snapshot["manifest_sha256"]
        or any(
            release_asset_sha256(release_asset(release_view, str(item["name"]))) != str(item["sha256"])
            for item in bundle_assets.get("assets", [])
        )
    ):
        return {
            "ok": False,
            "phase": "verify_release",
            "reason": "resumed_release_verification_failed",
            "release_view": release_view,
        }
    return {
        "schema": "codex_environment_mirror.release.v1",
        "ok": True,
        "phase": "resumed_existing_draft",
        "generated_at": now_iso(),
        "tag": tag,
        "snapshot_id": snapshot_id,
        "head": tag_sha,
        "current_head": head_sha,
        "tag_created": False,
        "release_created": False,
        "reused": True,
        "resumed": True,
        "release": release_view,
        "network_route": network_route,
        "manifest_attachment": str(manifest_path),
        "mcp_release_assets": bundle_assets,
    }


def existing_release_for_current_state(
    tag: str,
    *,
    remote: str = "",
    branch: str = "",
) -> dict[str, Any]:
    """Read back a completed milestone before starting expensive release validation."""
    snapshot_id = latest_snapshot_id()
    control_plane = control_plane_status()
    validation, _ = control_plane_validation_receipt(snapshot_id)
    if (
        not control_plane.get("ok")
        or control_plane.get("latest_milestone_tag") != tag
        or not reusable_validation_receipt(validation, snapshot_id)
    ):
        return {"ok": False, "reason": "control_plane_not_current_for_tag"}

    head = git_result(["rev-parse", "HEAD"])
    head_sha = str(head.get("stdout") or "").strip()
    if not head.get("ok") or not GIT_SHA.match(head_sha):
        return {"ok": False, "reason": "git_head_unresolved", "detail": head}
    remote_name = (remote or "origin").strip()
    remote_url = git_result(["remote", "get-url", remote_name])
    if not remote_url.get("ok"):
        return {"ok": False, "reason": "git_remote_missing", "detail": remote_url}
    branch_name = branch.strip()
    if not branch_name:
        current_branch = git_result(["branch", "--show-current"])
        branch_name = str(current_branch.get("stdout") or "").strip()
    if not branch_name:
        return {"ok": False, "reason": "git_branch_unresolved"}
    network_env, network_route = git_network_env_for_remote(str(remote_url.get("stdout") or ""))
    local_tag = git_result(["rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"])
    local_tag_sha = str(local_tag.get("stdout") or "").strip()
    if local_tag.get("ok") and local_tag_sha != head_sha:
        return {
            "ok": False,
            "reason": "existing_tag_points_elsewhere",
            "tag": tag,
            "tag_head": local_tag_sha,
            "expected_head": head_sha,
            "terminal": True,
        }
    remote_branch = git_result(["ls-remote", "--heads", remote_name, branch_name], extra_env=network_env)
    remote_head = str(remote_branch.get("stdout") or "").split()[0] if remote_branch.get("stdout") else ""
    remote_tag_sha = remote_tag_commit(remote_name, tag, local_tag_sha, extra_env=network_env)
    if remote_tag_sha and remote_tag_sha != head_sha:
        return {
            "ok": False,
            "reason": "remote_tag_head_mismatch",
            "tag": tag,
            "remote_tag_head": remote_tag_sha,
            "expected_head": head_sha,
            "terminal": True,
        }
    view_result = gh_result(
        ["release", "view", tag, "--json", "tagName,isDraft,url,targetCommitish,name,assets"],
        extra_env=network_env,
    )
    view = release_view_payload(view_result)
    if not (
        remote_head == head_sha
        and remote_tag_sha == head_sha
        and view_result.get("ok")
        and view.get("tagName") == tag
        and view.get("isDraft") is False
        and release_has_manifest_attachment(view)
    ):
        return {"ok": False, "reason": "existing_release_not_current"}
    return {
        "schema": "codex_environment_mirror.release.v1",
        "ok": True,
        "phase": "already_released",
        "reason": "existing_release_matches_current_state",
        "generated_at": now_iso(),
        "tag": tag,
        "snapshot_id": snapshot_id,
        "head": head_sha,
        "reused": True,
        "resumed": True,
        "tag_created": False,
        "release_created": False,
        "release": view,
        "control_plane": control_plane,
        "validation": validation,
        "push": {
            "ok": True,
            "reason": "remote_branch_already_current",
            "remote": remote_name,
            "branch": branch_name,
            "head": head_sha,
            "remote_verification": {"ok": True, "remote_head": remote_head},
        },
        "network_route": network_route,
        "manifest_attachment": "snapshot-manifest.json",
    }


def release(
    confirm: str,
    *,
    tag: str,
    title: str = "",
    remote: str = "",
    branch: str = "",
) -> dict[str, Any]:
    if confirm != RELEASE_CONFIRMATION:
        return {
            "schema": "codex_environment_mirror.release.v1",
            "ok": False,
            "reason": "confirmation_required",
            "required_confirmation": RELEASE_CONFIRMATION,
        }
    if not parse_semantic_tag(tag):
        return {"schema": "codex_environment_mirror.release.v1", "ok": False, "reason": "semantic_tag_invalid", "tag": tag}
    lock_path = runtime_root() / "locks" / "release.lock"
    try:
        with exclusive_operation_lock(lock_path, "release"):
            status_result = git_result(["status", "--short"])
            if not status_result.get("ok") or status_result.get("stdout"):
                return {
                    "schema": "codex_environment_mirror.release.v1",
                    "ok": False,
                    "phase": "preflight",
                    "reason": "git_worktree_not_clean",
                    "status": status_result.get("stdout", ""),
                }
            existing = existing_release_for_current_state(tag, remote=remote, branch=branch)
            if existing.get("ok") or existing.get("terminal"):
                if existing.get("reason") != "existing_tag_points_elsewhere":
                    return existing
                resumed = resume_ancestor_release_draft(tag, remote=remote)
                if resumed.get("ok") or resumed.get("terminal"):
                    return resumed
                return resumed
            plan = release_plan()
            review = contract_review_plan()
            if review.get("required_review_files") and not review.get("review_current"):
                return {
                    "schema": "codex_environment_mirror.release.v1",
                    "ok": False,
                    "phase": "contract_review",
                    "reason": "codex_contract_review_required",
                    "contract_review": review,
                }
            snapshot_id = latest_snapshot_id()
            core = run_mirror(["validate", "--live-sources", "--snapshot", snapshot_id, "--skip-control-plane"], timeout=300)
            if not core.get("ok"):
                return {"schema": "codex_environment_mirror.release.v1", "ok": False, "phase": "core_validate", "validation": core}
            control_plane = write_control_plane_state(snapshot_id, core, milestone_tag=tag)
            if not control_plane.get("ok"):
                return {"schema": "codex_environment_mirror.release.v1", "ok": False, "phase": "control_plane", "control_plane": control_plane}
            # The core check above already performed the expensive live-source
            # scan.  Validate only the newly written control-plane state, then
            # merge both receipts instead of scanning every source twice.
            control_owner = run_mirror(["control-plane-validate", "--snapshot", snapshot_id], timeout=120)
            validation = validation_receipt(merge_live_source_validation(control_owner, core))
            if not reusable_validation_receipt(validation, snapshot_id):
                return {
                    "schema": "codex_environment_mirror.release.v1",
                    "ok": False,
                    "phase": "validate",
                    "control_plane": control_plane,
                    "validation": validation,
                }
            bundle_assets = mcp_release_assets(snapshot_id)
            if not bundle_assets.get("ok"):
                return {
                    "schema": "codex_environment_mirror.release.v1",
                    "ok": False,
                    "phase": "mcp_release_assets",
                    "reason": bundle_assets.get("reason", "mcp_release_assets_not_ready"),
                    "mcp_release_assets": bundle_assets,
                }
            metadata_commit = commit_refresh(snapshot_id, phase=f"release-{tag}")
            if not metadata_commit.get("ok"):
                unstage_all()
                return {
                    "schema": "codex_environment_mirror.release.v1",
                    "ok": False,
                    "phase": "metadata_commit",
                    "metadata_commit": metadata_commit,
                }
            pushed = push_receipt(remote=remote, branch=branch)
            if not pushed.get("ok"):
                return {"schema": "codex_environment_mirror.release.v1", "ok": False, "phase": "push_branch", "push": pushed}
            remote_name = str(pushed.get("remote") or remote or "origin")
            remote_url = git_result(["remote", "get-url", remote_name])
            network_env, network_route = git_network_env_for_remote(str(remote_url.get("stdout", "")))
            bundle_assets = prepare_release_bundle_assets(
                bundle_assets,
                tag=tag,
                remote_url=str(remote_url.get("stdout", "")),
                extra_env=network_env,
            )
            head = git_result(["rev-parse", "HEAD"])
            head_sha = str(head.get("stdout") or "").strip()
            local_tag = git_result(["rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"])
            tag_created = False
            if local_tag.get("ok"):
                if str(local_tag.get("stdout") or "").strip() != head_sha:
                    return {
                        "schema": "codex_environment_mirror.release.v1",
                        "ok": False,
                        "phase": "tag",
                        "reason": "existing_tag_points_elsewhere",
                        "tag": tag,
                        "tag_head": local_tag.get("stdout", ""),
                        "expected_head": head_sha,
                    }
            else:
                created = git_result(["tag", "-a", tag, "-m", title or f"Codex environment mirror {tag}"])
                if not created.get("ok"):
                    return {"schema": "codex_environment_mirror.release.v1", "ok": False, "phase": "tag", "detail": created}
                tag_created = True
            remote_tag_sha = remote_tag_commit(remote_name, tag, head_sha, extra_env=network_env)
            if not remote_tag_sha:
                tag_push = git_result(["push", remote_name, f"refs/tags/{tag}"], timeout=300, extra_env=network_env)
                if not tag_push.get("ok"):
                    return {"schema": "codex_environment_mirror.release.v1", "ok": False, "phase": "push_tag", "detail": tag_push}
                remote_tag_sha = remote_tag_commit(remote_name, tag, head_sha, extra_env=network_env)
            if remote_tag_sha != head_sha:
                return {
                    "schema": "codex_environment_mirror.release.v1",
                    "ok": False,
                    "phase": "verify_tag",
                    "reason": "remote_tag_head_mismatch",
                    "remote_tag_head": remote_tag_sha,
                    "expected_head": head_sha,
                }
            view = gh_result(["release", "view", tag, "--json", "tagName,isDraft,url,targetCommitish,name,assets"], extra_env=network_env)
            release_created = False
            notes_path = runtime_root() / f"release-notes-{tag}.md"
            notes_path.parent.mkdir(parents=True, exist_ok=True)
            notes_path.write_text(release_notes(tag, plan, validation), encoding="utf-8", newline="\n")
            manifest_path = mirror_root() / "snapshots" / snapshot_id / "snapshot-manifest.json"
            release_files = [f"{manifest_path}#snapshot-manifest.json", f"{bundle_assets['index_path']}#mcp-bundle-index.json"]
            release_files.extend(f"{item['path']}#{item['name']}" for item in bundle_assets.get("upload_assets", []))
            upload_timeout = release_upload_timeout(release_files)
            if not view.get("ok"):
                created = gh_result([
                    "release", "create", tag, *release_files,
                    "--title", title or f"Codex Environment Mirror {tag}",
                    "--notes-file", str(notes_path), "--draft", "--verify-tag",
                ], timeout=upload_timeout, extra_env=network_env)
                if not created.get("ok"):
                    return {"schema": "codex_environment_mirror.release.v1", "ok": False, "phase": "create_release", "detail": created}
                release_created = True
            else:
                current_view = release_view_payload(view)
                current_names = {
                    str(asset.get("name") or "")
                    for asset in current_view.get("assets", [])
                    if isinstance(asset, dict)
                }
                missing_files = [item for item in release_files if item.rsplit("#", 1)[-1] not in current_names]
                if missing_files:
                    uploaded = gh_result(
                        ["release", "upload", tag, *missing_files, "--clobber"],
                        timeout=release_upload_timeout(missing_files),
                        extra_env=network_env,
                    )
                    if not uploaded.get("ok"):
                        return {"schema": "codex_environment_mirror.release.v1", "ok": False, "phase": "upload_release_assets", "detail": uploaded}
            current = gh_result(["release", "view", tag, "--json", "isDraft"], extra_env=network_env)
            current_payload = release_view_payload(current)
            if current_payload.get("isDraft") is True:
                published = gh_result(["release", "edit", tag, "--draft=false"], timeout=180, extra_env=network_env)
                if not published.get("ok"):
                    return {"schema": "codex_environment_mirror.release.v1", "ok": False, "phase": "publish_release", "detail": published}
            verified = gh_result(["release", "view", tag, "--json", "tagName,isDraft,url,targetCommitish,name,assets"], extra_env=network_env)
            release_view = release_view_payload(verified)
            if (
                not verified.get("ok")
                or release_view.get("tagName") != tag
                or release_view.get("isDraft") is not False
                or not release_has_manifest_attachment(release_view)
                or not release_has_required_attachments(
                    release_view,
                    {"mcp-bundle-index.json", *(item["name"] for item in bundle_assets.get("upload_assets", []))},
                )
                or not release_bundle_assets_verified(
                    release_view,
                    bundle_assets,
                    tag=tag,
                    remote_url=str(remote_url.get("stdout", "")),
                    extra_env=network_env,
                )
            ):
                return {
                    "schema": "codex_environment_mirror.release.v1",
                    "ok": False,
                    "phase": "verify_release",
                    "detail": verified,
                    "release_view": release_view,
                }
            return {
                "schema": "codex_environment_mirror.release.v1",
                "ok": True,
                "generated_at": now_iso(),
                "tag": tag,
                "snapshot_id": snapshot_id,
                "head": head_sha,
                "tag_created": tag_created,
                "release_created": release_created,
                "release": release_view,
                "release_plan": plan,
                "contract_review": review,
                "control_plane": control_plane,
                "validation": validation,
                "push": pushed,
                "network_route": network_route,
                "manifest_attachment": str(manifest_path),
                "mcp_release_assets": bundle_assets,
            }
    except MirrorOperationBusy as exc:
        return {
            "schema": "codex_environment_mirror.release.v1",
            "ok": False,
            "reason": "mirror_operation_busy",
            "operation": "release",
            "lock_path": str(exc.lock_path),
            "lock_owner": exc.owner,
            "next_action": "wait_for_the_active_owner_then_run_status; do_not_start_parallel_release_or_publish",
        }


def execute(action: str, *, target_root: str = "", confirm: str = "", changed_paths: list[str] | None = None, left_snapshot: str = "", right_snapshot: str = "", remote: str = "", branch: str = "", tag: str = "", title: str = "", release_impact: str = "", force_fresh: bool = False) -> dict[str, Any]:
    if action == "status":
        return status(force_fresh=force_fresh)
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
        return compact_operation_receipt(action, refresh(confirm, changed_paths))
    if action == "publish":
        return compact_operation_receipt(action, publish(confirm, changed_paths=changed_paths, remote=remote, branch=branch))
    if action == "release-plan":
        return release_plan()
    if action == "contract-review-plan":
        return contract_review_plan()
    if action == "contract-review":
        return contract_review(confirm, decisions=changed_paths or [], summary=title, release_impact=release_impact, remote=remote, branch=branch)
    if action == "release":
        return compact_operation_receipt(action, release(confirm, tag=tag, title=title, remote=remote, branch=branch))
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
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--force-fresh", action="store_true")
    for name in ("plan", "doctor", "validate", "release-plan", "contract-review-plan"):
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
    release_parser = sub.add_parser("release")
    release_parser.add_argument("--confirm", default="")
    release_parser.add_argument("--tag", required=True)
    release_parser.add_argument("--title", default="")
    release_parser.add_argument("--remote", default="")
    release_parser.add_argument("--branch", default="")
    review_parser = sub.add_parser("contract-review")
    review_parser.add_argument("--confirm", default="")
    review_parser.add_argument("--decision", action="append", default=[])
    review_parser.add_argument("--summary", default="")
    review_parser.add_argument("--release-impact", choices=("patch", "minor", "major"), default="")
    review_parser.add_argument("--remote", default="")
    review_parser.add_argument("--branch", default="")
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
            changed_paths=getattr(args, "decision", getattr(args, "changed", [])),
            left_snapshot=getattr(args, "left", ""),
            right_snapshot=getattr(args, "right", ""),
            remote=getattr(args, "remote", ""),
            branch=getattr(args, "branch", ""),
            tag=getattr(args, "tag", ""),
            title=getattr(args, "summary", getattr(args, "title", "")),
            release_impact=getattr(args, "release_impact", ""),
            force_fresh=bool(getattr(args, "force_fresh", False)),
        )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
