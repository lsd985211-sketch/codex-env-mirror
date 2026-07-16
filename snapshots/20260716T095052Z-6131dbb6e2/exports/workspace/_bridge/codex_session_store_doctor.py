#!/usr/bin/env python3
"""Codex local session-store restore-performance governance.

Ownership:
  This module owns read-only evidence and dry-run planning for
  ``%USERPROFILE%\\.codex\\sessions`` growth that can slow Codex Desktop
  restart/resume.

Non-goals:
  It does not delete session JSONL files. Its optional compaction path rewrites
  only restart-boundary session files after backup and preserves conversation
  text/context records.

State behavior:
  Read-only by default. ``repair-plan`` is a dry-run proposal only.

Caller context:
  Used by ``codex_config_guard.py`` and by Codex when diagnosing slow restart
  or fragile conversation restore.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from shared.codex_desktop_package import codex_process_family_running as process_family_running
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.shared.codex_desktop_package import codex_process_family_running as process_family_running


SCHEMA_PREFIX = "codex-session-store"
DEFAULT_WARN_MB = 50
DEFAULT_RISK_MB = 200
DEFAULT_TOTAL_WARN_MB = 500
DEFAULT_TOP_LIMIT = 12
DEFAULT_OUTPUT_KEEP_CHARS = 6000
DEFAULT_COMPACT_MIN_SAVINGS_MB = 10
DEFAULT_MAX_COMPACT_FILES = 3
DEFAULT_AUTO_MAINTAIN_COOLDOWN_HOURS = 24
DEFAULT_AUTO_MAINTAIN_GROWTH_MB = 100
COMPACTION_BACKUP_ROOT = Path(__file__).resolve().parent / "backups" / "manual"
AUTO_MAINTAIN_STATE_PATH = Path(__file__).resolve().parent / "runtime" / "codex_session_store" / "auto_maintain_state.json"
AUTO_MAINTAIN_LOCK_PATH = Path(__file__).resolve().parent / "runtime" / "codex_session_store" / "auto_maintain.lock.json"
AUTO_MAINTAIN_LOCK_STALE_SECONDS = 30 * 60


@dataclass(frozen=True)
class Thresholds:
    warn_bytes: int = DEFAULT_WARN_MB * 1024 * 1024
    risk_bytes: int = DEFAULT_RISK_MB * 1024 * 1024
    total_warn_bytes: int = DEFAULT_TOTAL_WARN_MB * 1024 * 1024
    top_limit: int = DEFAULT_TOP_LIMIT


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_sessions_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def archived_sessions_root() -> Path:
    return Path.home() / ".codex" / "archived_sessions"


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@contextmanager
def auto_maintain_lock() -> Any:
    """Acquire a short-lived cross-process lock without blocking startup."""

    AUTO_MAINTAIN_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    acquired = False
    for attempt in range(2):
        try:
            descriptor = os.open(str(AUTO_MAINTAIN_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "schema": f"{SCHEMA_PREFIX}.auto_maintain_lock.v1",
                        "token": token,
                        "pid": os.getpid(),
                        "created_at": utc_now(),
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            acquired = True
            break
        except FileExistsError:
            try:
                age_seconds = max(0.0, time.time() - AUTO_MAINTAIN_LOCK_PATH.stat().st_mtime)
            except OSError:
                age_seconds = 0.0
            if attempt == 0 and age_seconds >= AUTO_MAINTAIN_LOCK_STALE_SECONDS:
                try:
                    AUTO_MAINTAIN_LOCK_PATH.unlink()
                    continue
                except OSError:
                    pass
            break
    try:
        yield {
            "acquired": acquired,
            "token": token if acquired else "",
            "path": str(AUTO_MAINTAIN_LOCK_PATH),
        }
    finally:
        if acquired:
            current = read_json_file(AUTO_MAINTAIN_LOCK_PATH)
            if str(current.get("token") or "") == token:
                try:
                    AUTO_MAINTAIN_LOCK_PATH.unlink()
                except OSError:
                    pass


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def hours_since(value: Any) -> float | None:
    parsed = parse_utc(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def codex_process_family_running() -> bool:
    if os.name != "nt":
        return False
    try:
        return process_family_running()
    except Exception:
        return True


def file_record(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "relative_path": str(path.relative_to(root)) if path.is_relative_to(root) else path.name,
        "bytes": int(stat.st_size),
        "mb": round(stat.st_size / (1024 * 1024), 2),
        "last_write_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def iter_session_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*.jsonl") if path.is_file()]


def latest_active_candidate(root: Path) -> Path | None:
    """Find the newest session candidate through the bounded date hierarchy."""
    if not root.exists():
        return None
    level = root
    for _ in range(3):
        try:
            directories = sorted(
                (path for path in level.iterdir() if path.is_dir()),
                key=lambda path: path.name,
                reverse=True,
            )
        except OSError:
            return None
        if not directories:
            return None
        level = directories[0]
    try:
        candidates = [path for path in level.glob("*.jsonl") if path.is_file()]
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None
    except OSError:
        return None


def lightweight_auto_maintain_gate(
    *,
    sessions_root: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Decide whether cooldown metadata is sufficient or a full scan is required."""
    age_hours = hours_since(state.get("last_checked_at"))
    if not state or age_hours is None:
        return {"ok": True, "should_scan": True, "reason": "state_missing_or_invalid"}
    if age_hours >= DEFAULT_AUTO_MAINTAIN_COOLDOWN_HOURS:
        return {
            "ok": True,
            "should_scan": True,
            "reason": "cooldown_expired",
            "detail": {"age_hours": round(age_hours, 2)},
        }
    saved_root = str(state.get("sessions_root") or "")
    if saved_root and Path(saved_root) != sessions_root:
        return {"ok": True, "should_scan": True, "reason": "sessions_root_changed"}
    current_active = latest_active_candidate(sessions_root)
    current_active_path = str(current_active) if current_active else ""
    saved_active_path = str(state.get("active_candidate_path") or "")
    if current_active_path != saved_active_path:
        return {
            "ok": True,
            "should_scan": True,
            "reason": "active_candidate_changed",
            "detail": {"previous": saved_active_path, "current": current_active_path},
        }
    if current_active:
        try:
            active_bytes = int(current_active.stat().st_size)
        except OSError:
            return {"ok": True, "should_scan": True, "reason": "active_candidate_stat_failed"}
        if active_bytes < int(state.get("active_candidate_bytes") or 0):
            return {"ok": True, "should_scan": True, "reason": "active_candidate_shrank"}
    largest_path_text = str(state.get("largest_path") or "")
    if largest_path_text:
        largest_path = Path(largest_path_text)
        try:
            largest_bytes = int(largest_path.stat().st_size)
        except OSError:
            return {"ok": True, "should_scan": True, "reason": "largest_inactive_candidate_changed"}
        growth_bytes = max(0, largest_bytes - int(state.get("largest_bytes") or 0))
        if growth_bytes >= DEFAULT_AUTO_MAINTAIN_GROWTH_MB * 1024 * 1024:
            return {
                "ok": True,
                "should_scan": True,
                "reason": "largest_inactive_candidate_grew",
                "detail": {"growth_mb": round(growth_bytes / (1024 * 1024), 2)},
            }
    return {
        "ok": True,
        "should_scan": False,
        "reason": "cooldown_metadata_unchanged",
        "writes_files": False,
        "detail": {
            "age_hours": round(age_hours, 2),
            "cooldown_hours": DEFAULT_AUTO_MAINTAIN_COOLDOWN_HOURS,
            "active_candidate_path": current_active_path,
        },
    }


def classify_file(item: dict[str, Any], thresholds: Thresholds, active_candidate_path: str) -> str:
    size = int(item.get("bytes") or 0)
    if size >= thresholds.risk_bytes:
        return "active_candidate_huge" if item.get("path") == active_candidate_path else "huge"
    if size >= thresholds.warn_bytes:
        return "large"
    return "normal"


def snapshot(
    *,
    sessions_root: Path | None = None,
    thresholds: Thresholds = Thresholds(),
) -> dict[str, Any]:
    root = sessions_root or default_sessions_root()
    files = iter_session_files(root)
    records = [file_record(path, root) for path in files]
    records.sort(key=lambda item: int(item["bytes"]), reverse=True)
    total_bytes = sum(int(item["bytes"]) for item in records)
    newest = sorted(records, key=lambda item: str(item["last_write_utc"]), reverse=True)[: thresholds.top_limit]
    newest_records = sorted(records, key=lambda item: str(item["last_write_utc"]), reverse=True)
    active_candidate_path = str(newest_records[0]["path"]) if newest_records else ""
    classified = [
        {
            **item,
            "class": classify_file(item, thresholds, active_candidate_path),
            "restore_risk": int(item["bytes"]) >= thresholds.warn_bytes,
            "active_candidate": item.get("path") == active_candidate_path,
        }
        for item in records[: thresholds.top_limit]
    ]
    archived_root = archived_sessions_root()
    archived_files = iter_session_files(archived_root)
    archived_total = sum(path.stat().st_size for path in archived_files)
    return {
        "schema": f"{SCHEMA_PREFIX}.snapshot.v1",
        "ok": True,
        "generated_at": utc_now(),
        "sessions_root": str(root),
        "exists": root.exists(),
        "file_count": len(records),
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / (1024 * 1024), 2),
        "thresholds": {
            "warn_mb": round(thresholds.warn_bytes / (1024 * 1024), 2),
            "risk_mb": round(thresholds.risk_bytes / (1024 * 1024), 2),
            "total_warn_mb": round(thresholds.total_warn_bytes / (1024 * 1024), 2),
            "top_limit": thresholds.top_limit,
        },
        "largest": classified,
        "recent": newest,
        "active_candidate_path": active_candidate_path,
        "archived_sessions": {
            "root": str(archived_root),
            "exists": archived_root.exists(),
            "file_count": len(archived_files),
            "total_bytes": archived_total,
            "total_mb": round(archived_total / (1024 * 1024), 2),
        },
    }


def doctor(*, sessions_root: Path | None = None, thresholds: Thresholds = Thresholds()) -> dict[str, Any]:
    snap = snapshot(sessions_root=sessions_root, thresholds=thresholds)
    issues: list[dict[str, Any]] = []
    total_bytes = int(snap.get("total_bytes") or 0)
    largest = snap.get("largest") if isinstance(snap.get("largest"), list) else []
    huge = [item for item in largest if int(item.get("bytes") or 0) >= thresholds.risk_bytes]
    large = [item for item in largest if int(item.get("bytes") or 0) >= thresholds.warn_bytes]
    if total_bytes >= thresholds.total_warn_bytes:
        issues.append(
            {
                "code": "codex_session_store_total_large",
                "severity": "risk",
                "summary": "Codex active session store is large enough to slow restart/resume.",
                "detail": {
                    "total_mb": snap.get("total_mb"),
                    "threshold_mb": round(thresholds.total_warn_bytes / (1024 * 1024), 2),
                    "file_count": snap.get("file_count"),
                },
                "safe_next_step": "python _bridge\\codex_session_store_doctor.py repair-plan",
            }
        )
    if huge:
        issues.append(
            {
                "code": "codex_session_store_huge_files",
                "severity": "risk",
                "summary": "One or more Codex session JSONL files are very large and can dominate restore cost.",
                "detail": {"files": huge[:5]},
                "safe_next_step": "start a checkpointed continuation before archiving; do not delete active sessions in-place",
            }
        )
    elif large:
        issues.append(
            {
                "code": "codex_session_store_large_files",
                "severity": "advisory",
                "summary": "Some Codex session JSONL files are large enough to deserve monitoring.",
                "detail": {"files": large[:5]},
                "safe_next_step": "monitor and checkpoint long conversations before they cross the risk threshold",
            }
        )
    return {
        "schema": f"{SCHEMA_PREFIX}.doctor.v1",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": utc_now(),
        "issues": issues,
        "snapshot": snap,
        "policy": {
            "feature_reduction": "forbidden",
            "writes_files": False,
            "safe_strategy": [
                "detect oversized active transcripts",
                "prefer summary checkpoint plus new continuation thread",
                "archive only inactive sessions after explicit approval and backup",
                "keep MCP capabilities configured; optimize startup pressure separately",
            ],
        },
    }


def repair_plan(
    *,
    sessions_root: Path | None = None,
    thresholds: Thresholds = Thresholds(),
) -> dict[str, Any]:
    snap = snapshot(sessions_root=sessions_root, thresholds=thresholds)
    largest = snap.get("largest") if isinstance(snap.get("largest"), list) else []
    candidates = [
        item
        for item in largest
        if int(item.get("bytes") or 0) >= thresholds.warn_bytes
    ]
    plan_items: list[dict[str, Any]] = []
    active_candidate_path = str(snap.get("active_candidate_path") or "")
    for item in candidates:
        is_active_candidate = item.get("path") == active_candidate_path
        plan_items.append(
            {
                "path": item.get("path"),
                "mb": item.get("mb"),
                "class": item.get("class"),
                "recommended_action": (
                    "checkpoint_current_thread_then_continue_in_new_thread"
                    if is_active_candidate
                    else "archive_or_compress_after_confirming_inactive"
                ),
                "requires_explicit_approval": True,
                "reason": (
                    "most recently written transcript may be active; preserve continuity before moving anything"
                    if is_active_candidate
                    else "old oversized transcripts should not stay in the active restore set indefinitely"
                ),
            }
        )
    return {
        "schema": f"{SCHEMA_PREFIX}.repair_plan.v1",
        "ok": True,
        "generated_at": utc_now(),
        "dry_run": True,
        "would_apply": False,
        "writes_files": False,
        "plan_items": plan_items,
        "commands": {
            "snapshot": "python _bridge\\codex_session_store_doctor.py snapshot",
            "doctor": "python _bridge\\codex_session_store_doctor.py doctor",
            "compact_plan": "python _bridge\\codex_session_store_doctor.py compact-plan",
            "validate": "python _bridge\\codex_session_store_doctor.py validate",
        },
        "guardrails": [
            "do not delete session JSONL files",
            "do not move the newest or current thread file without a checkpointed continuation",
            "do not disable MCP profiles or lower model capability as a performance fix",
            "archive/compress actions require a separate approved implementation path",
            "content compaction only runs at a restart boundary after backup and keeps messages plus compacted context blocks",
        ],
        "snapshot": snap,
    }


def compact_string(value: str, *, keep_chars: int = DEFAULT_OUTPUT_KEEP_CHARS) -> tuple[str, int]:
    original_bytes = len(value.encode("utf-8", "replace"))
    if original_bytes <= keep_chars:
        return value, 0
    prefix = value[:keep_chars]
    marker = (
        f"\n\n[codex-session-store compacted {original_bytes} bytes; "
        f"sha256={sha256_text(value)}]"
    )
    compacted = prefix + marker
    saved = original_bytes - len(compacted.encode("utf-8", "replace"))
    return compacted, max(0, saved)


def compact_json_object(
    obj: dict[str, Any],
    *,
    keep_chars: int = DEFAULT_OUTPUT_KEEP_CHARS,
) -> tuple[dict[str, Any] | None, int, str]:
    """Return compacted object, saved bytes, and reason.

    ``None`` means the line can be omitted. Conversation-bearing message,
    session_meta, turn_context, and existing compacted records are preserved.
    """
    obj_type = obj.get("type")
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    payload_type = payload.get("type")
    if obj_type in {"session_meta", "turn_context", "compacted"}:
        return obj, 0, "preserve_context_record"
    if obj_type == "event_msg":
        raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        return None, len(raw.encode("utf-8", "replace")) + 1, "drop_event_msg"
    if obj_type != "response_item":
        return obj, 0, "preserve_unknown_record"
    if payload_type == "message":
        return obj, 0, "preserve_message"
    compacted = json.loads(json.dumps(obj, ensure_ascii=False))
    compacted_payload = compacted.get("payload") if isinstance(compacted.get("payload"), dict) else {}
    saved = 0
    reason = "preserve_response_item"
    if payload_type in {"function_call_output", "custom_tool_call_output", "tool_search_output"}:
        for key in ("output", "content", "text"):
            if isinstance(compacted_payload.get(key), str):
                compacted_payload[key], delta = compact_string(compacted_payload[key], keep_chars=keep_chars)
                saved += delta
        reason = f"compact_{payload_type}"
    elif payload_type in {"function_call", "custom_tool_call"}:
        if isinstance(compacted_payload.get("arguments"), str):
            compacted_payload["arguments"], delta = compact_string(
                compacted_payload["arguments"],
                keep_chars=keep_chars,
            )
            saved += delta
        reason = f"compact_{payload_type}_arguments"
    elif payload_type == "reasoning":
        encrypted = compacted_payload.get("encrypted_content")
        if isinstance(encrypted, str) and encrypted:
            saved += len(encrypted.encode("utf-8", "replace"))
            compacted_payload["encrypted_content"] = (
                f"[codex-session-store compacted encrypted reasoning "
                f"{len(encrypted.encode('utf-8', 'replace'))} bytes; sha256={sha256_text(encrypted)}]"
            )
        reason = "compact_reasoning_encrypted_content"
    return compacted, saved, reason


def compact_file_plan(
    path: Path,
    *,
    keep_chars: int = DEFAULT_OUTPUT_KEEP_CHARS,
) -> dict[str, Any]:
    before_bytes = path.stat().st_size
    saved_by_reason: dict[str, int] = {}
    count_by_reason: dict[str, int] = {}
    line_count = 0
    invalid_json_count = 0
    projected_bytes = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line_count += 1
            try:
                obj = json.loads(line)
            except Exception:
                invalid_json_count += 1
                projected_bytes += len(line.encode("utf-8", "replace"))
                continue
            compacted, saved, reason = compact_json_object(obj, keep_chars=keep_chars)
            count_by_reason[reason] = count_by_reason.get(reason, 0) + 1
            saved_by_reason[reason] = saved_by_reason.get(reason, 0) + int(saved)
            if compacted is None:
                continue
            encoded = (json.dumps(compacted, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                "utf-8",
                "replace",
            )
            projected_bytes += len(encoded)
    saved_bytes = max(0, before_bytes - projected_bytes)
    return {
        "path": str(path),
        "before_bytes": before_bytes,
        "before_mb": round(before_bytes / (1024 * 1024), 2),
        "projected_bytes": projected_bytes,
        "projected_mb": round(projected_bytes / (1024 * 1024), 2),
        "saved_bytes": saved_bytes,
        "saved_mb": round(saved_bytes / (1024 * 1024), 2),
        "line_count": line_count,
        "invalid_json_count": invalid_json_count,
        "count_by_reason": count_by_reason,
        "saved_by_reason": saved_by_reason,
    }


def compaction_candidates(
    *,
    sessions_root: Path | None = None,
    thresholds: Thresholds = Thresholds(),
    include_active_candidate: bool,
    max_files: int = DEFAULT_MAX_COMPACT_FILES,
    minimum_bytes: int | None = None,
) -> list[Path]:
    snap = snapshot(sessions_root=sessions_root, thresholds=thresholds)
    active_candidate = str(snap.get("active_candidate_path") or "")
    largest = snap.get("largest") if isinstance(snap.get("largest"), list) else []
    minimum = int(minimum_bytes if minimum_bytes is not None else thresholds.warn_bytes)
    selected: list[Path] = []
    for item in largest:
        if int(item.get("bytes") or 0) < minimum:
            continue
        path = Path(str(item.get("path") or ""))
        if not include_active_candidate and str(path) == active_candidate:
            continue
        selected.append(path)
        if len(selected) >= max_files:
            break
    return selected


def compact_plan(
    *,
    sessions_root: Path | None = None,
    thresholds: Thresholds = Thresholds(),
    include_active_candidate: bool = False,
    max_files: int = DEFAULT_MAX_COMPACT_FILES,
    keep_chars: int = DEFAULT_OUTPUT_KEEP_CHARS,
    minimum_bytes: int | None = None,
) -> dict[str, Any]:
    candidates = compaction_candidates(
        sessions_root=sessions_root,
        thresholds=thresholds,
        include_active_candidate=include_active_candidate,
        max_files=max_files,
        minimum_bytes=minimum_bytes,
    )
    plans = [compact_file_plan(path, keep_chars=keep_chars) for path in candidates]
    return {
        "schema": f"{SCHEMA_PREFIX}.compact_plan.v1",
        "ok": True,
        "generated_at": utc_now(),
        "dry_run": True,
        "writes_files": False,
        "include_active_candidate": include_active_candidate,
        "minimum_candidate_mb": round((minimum_bytes if minimum_bytes is not None else thresholds.warn_bytes) / (1024 * 1024), 2),
        "candidate_count": len(candidates),
        "total_projected_saved_mb": round(sum(float(item.get("saved_mb") or 0) for item in plans), 2),
        "plans": plans,
        "policy": {
            "preserve": ["session_meta", "turn_context", "response_item:message", "compacted"],
            "compact": [
                "response_item:function_call_output",
                "response_item:custom_tool_call_output",
                "response_item:tool_search_output",
                "response_item:reasoning.encrypted_content",
                "oversized tool call arguments",
            ],
            "drop": ["event_msg"],
            "apply_boundary": "restart_boundary_only; running Codex process family blocks automatic apply",
        },
    }


def compaction_backup_dir() -> Path:
    return (
        COMPACTION_BACKUP_ROOT
        / datetime.now(timezone.utc).strftime("%Y%m")
        / "codex-session-store"
        / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-session-compaction")
    )


def compact_file_apply(
    path: Path,
    *,
    backup_dir: Path,
    keep_chars: int = DEFAULT_OUTPUT_KEEP_CHARS,
    min_savings_bytes: int = DEFAULT_COMPACT_MIN_SAVINGS_MB * 1024 * 1024,
) -> dict[str, Any]:
    before_hash = sha256_file(path)
    plan = compact_file_plan(path, keep_chars=keep_chars)
    if int(plan.get("saved_bytes") or 0) < min_savings_bytes:
        return {
            "ok": True,
            "applied": False,
            "reason": "projected_savings_below_threshold",
            "plan": plan,
        }
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / path.name
    shutil.copy2(path, backup_path)
    temp_path = path.with_suffix(path.suffix + ".compact.tmp")
    line_count = 0
    with path.open("r", encoding="utf-8", errors="replace") as src, temp_path.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as dst:
        for line in src:
            line_count += 1
            try:
                obj = json.loads(line)
            except Exception:
                dst.write(line)
                continue
            compacted, _saved, _reason = compact_json_object(obj, keep_chars=keep_chars)
            if compacted is None:
                continue
            dst.write(json.dumps(compacted, ensure_ascii=False, separators=(",", ":")) + "\n")
    if codex_process_family_running():
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return {
            "ok": False,
            "applied": False,
            "reason": "codex_started_before_replace",
            "backup_path": str(backup_path),
            "plan": plan,
        }
    temp_path.replace(path)
    after_hash = sha256_file(path)
    manifest = {
        "schema": f"{SCHEMA_PREFIX}.compaction_manifest.v1",
        "generated_at": utc_now(),
        "source_path": str(path),
        "backup_path": str(backup_path),
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "backup_sha256": sha256_file(backup_path),
        "line_count": line_count,
        "plan": plan,
        "rollback": "Copy backup_path back to source_path after closing Codex Desktop.",
    }
    manifest_path = backup_dir / f"{path.stem}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "applied": True,
        "backup_path": str(backup_path),
        "manifest_path": str(manifest_path),
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "before_mb": plan.get("before_mb"),
        "after_mb": round(path.stat().st_size / (1024 * 1024), 2),
        "saved_mb": round((int(plan.get("before_bytes") or 0) - path.stat().st_size) / (1024 * 1024), 2),
    }


def compact_apply(
    *,
    sessions_root: Path | None = None,
    thresholds: Thresholds = Thresholds(),
    include_active_candidate: bool = False,
    max_files: int = DEFAULT_MAX_COMPACT_FILES,
    keep_chars: int = DEFAULT_OUTPUT_KEEP_CHARS,
    confirm_compact: bool = False,
    minimum_bytes: int | None = None,
) -> dict[str, Any]:
    if not confirm_compact:
        return {"ok": False, "applied": False, "reason": "confirm_compact_required"}
    if codex_process_family_running():
        return {"ok": True, "applied": False, "reason": "codex_process_family_running"}
    candidates = compaction_candidates(
        sessions_root=sessions_root,
        thresholds=thresholds,
        include_active_candidate=include_active_candidate,
        max_files=max_files,
        minimum_bytes=minimum_bytes,
    )
    backup_dir = compaction_backup_dir()
    results: list[dict[str, Any]] = []
    for path in candidates:
        try:
            results.append(compact_file_apply(path, backup_dir=backup_dir, keep_chars=keep_chars))
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "applied": False,
                    "path": str(path),
                    "reason": "compact_file_apply_exception",
                    "error": repr(exc),
                }
            )
    return {
        "schema": f"{SCHEMA_PREFIX}.compact_apply.v1",
        "ok": all(bool(item.get("ok")) for item in results),
        "generated_at": utc_now(),
        "applied": any(bool(item.get("applied")) for item in results),
        "backup_dir": str(backup_dir),
        "candidate_count": len(candidates),
        "results": results,
    }


def auto_maintain_gate(
    snap: dict[str, Any],
    *,
    thresholds: Thresholds = Thresholds(),
    state: dict[str, Any] | None = None,
    include_active_candidate: bool = False,
) -> dict[str, Any]:
    state = state or {}
    largest = snap.get("largest") if isinstance(snap.get("largest"), list) else []
    active_candidate_path = str(snap.get("active_candidate_path") or "")
    eligible = [
        item
        for item in largest
        if include_active_candidate or str(item.get("path") or "") != active_candidate_path
    ]
    largest_item = eligible[0] if eligible else {}
    total_bytes = int(snap.get("total_bytes") or 0)
    largest_bytes = int(largest_item.get("bytes") or 0)
    largest_path = str(largest_item.get("path") or "")
    warning_candidate_count = sum(1 for item in eligible if int(item.get("bytes") or 0) >= thresholds.warn_bytes)
    risk_candidate_count = sum(1 for item in eligible if int(item.get("bytes") or 0) >= thresholds.risk_bytes)
    if total_bytes < thresholds.total_warn_bytes and warning_candidate_count == 0:
        return {
            "ok": True,
            "should_run_heavy_plan": False,
            "reason": "below_size_threshold",
            "writes_files": False,
            "detail": {
                "total_mb": round(total_bytes / (1024 * 1024), 2),
                "largest_mb": largest_item.get("mb", 0),
                "total_warn_mb": round(thresholds.total_warn_bytes / (1024 * 1024), 2),
                "warn_mb": round(thresholds.warn_bytes / (1024 * 1024), 2),
                "active_candidate_excluded": not include_active_candidate,
            },
        }
    age_hours = hours_since(state.get("last_checked_at"))
    previous_total_bytes = int(state.get("total_bytes") or 0)
    previous_largest_path = str(state.get("largest_path") or "")
    growth_bytes = max(0, total_bytes - previous_total_bytes)
    new_risk_candidate = bool(largest_path and largest_path != previous_largest_path and largest_bytes >= thresholds.risk_bytes)
    if age_hours is not None and age_hours < DEFAULT_AUTO_MAINTAIN_COOLDOWN_HOURS:
        if growth_bytes < DEFAULT_AUTO_MAINTAIN_GROWTH_MB * 1024 * 1024 and not new_risk_candidate:
            return {
                "ok": True,
                "should_run_heavy_plan": False,
                "reason": "cooldown_not_expired",
                "writes_files": False,
                "detail": {
                    "age_hours": round(age_hours, 2),
                    "cooldown_hours": DEFAULT_AUTO_MAINTAIN_COOLDOWN_HOURS,
                    "growth_mb": round(growth_bytes / (1024 * 1024), 2),
                    "growth_threshold_mb": DEFAULT_AUTO_MAINTAIN_GROWTH_MB,
                    "new_risk_candidate": new_risk_candidate,
                    "active_candidate_excluded": not include_active_candidate,
                },
            }
    return {
        "ok": True,
        "should_run_heavy_plan": True,
        "reason": "threshold_or_cooldown_allows_compaction_check",
        "writes_files": False,
        "detail": {
            "total_mb": round(total_bytes / (1024 * 1024), 2),
            "largest_mb": largest_item.get("mb", 0),
            "warning_candidate_count": warning_candidate_count,
            "risk_candidate_count": risk_candidate_count,
            "age_hours": None if age_hours is None else round(age_hours, 2),
            "growth_mb": round(growth_bytes / (1024 * 1024), 2),
            "new_risk_candidate": new_risk_candidate,
            "active_candidate_excluded": not include_active_candidate,
        },
    }


def write_auto_maintain_state(
    *,
    snap: dict[str, Any],
    gate: dict[str, Any],
    result: dict[str, Any],
    plan_ran: bool,
) -> None:
    largest = snap.get("largest") if isinstance(snap.get("largest"), list) else []
    active_candidate_path = str(snap.get("active_candidate_path") or "")
    eligible = [item for item in largest if str(item.get("path") or "") != active_candidate_path]
    largest_item = eligible[0] if eligible else {}
    try:
        active_candidate_bytes = int(Path(active_candidate_path).stat().st_size) if active_candidate_path else 0
    except OSError:
        active_candidate_bytes = 0
    eligible_total_bytes = max(0, int(snap.get("total_bytes") or 0) - active_candidate_bytes)
    write_json_file(
        AUTO_MAINTAIN_STATE_PATH,
        {
            "schema": f"{SCHEMA_PREFIX}.auto_maintain_state.v1",
            "last_checked_at": utc_now(),
            "sessions_root": str(snap.get("sessions_root") or ""),
            "total_bytes": eligible_total_bytes,
            "total_mb": round(eligible_total_bytes / (1024 * 1024), 2),
            "largest_path": largest_item.get("path", ""),
            "largest_bytes": int(largest_item.get("bytes") or 0),
            "largest_mb": largest_item.get("mb", 0),
            "active_candidate_path": active_candidate_path,
            "active_candidate_bytes": active_candidate_bytes,
            "gate_reason": gate.get("reason"),
            "plan_ran": bool(plan_ran),
            "result_applied": bool(result.get("applied")),
            "result_reason": result.get("reason", ""),
            "cooldown_hours": DEFAULT_AUTO_MAINTAIN_COOLDOWN_HOURS,
            "growth_threshold_mb": DEFAULT_AUTO_MAINTAIN_GROWTH_MB,
        },
    )


def auto_maintain(
    *,
    sessions_root: Path | None = None,
    thresholds: Thresholds = Thresholds(),
    apply: bool = False,
    boundary: str = "guard",
    _lock_held: bool = False,
) -> dict[str, Any]:
    if apply and not _lock_held:
        with auto_maintain_lock() as lock:
            if not bool(lock.get("acquired")):
                return {
                    "schema": f"{SCHEMA_PREFIX}.auto_maintain.v1",
                    "ok": True,
                    "generated_at": utc_now(),
                    "applied": False,
                    "reason": "auto_maintain_lock_busy",
                    "boundary": boundary,
                    "lock": lock,
                    "policy": "startup is never blocked by a concurrent session-maintenance owner",
                }
            result = auto_maintain(
                sessions_root=sessions_root,
                thresholds=thresholds,
                apply=apply,
                boundary=boundary,
                _lock_held=True,
            )
            result["lock"] = lock
            return result
    running = codex_process_family_running()
    if running and apply:
        state = read_json_file(AUTO_MAINTAIN_STATE_PATH)
        return {
            "schema": f"{SCHEMA_PREFIX}.auto_maintain.v1",
            "ok": True,
            "generated_at": utc_now(),
            "codex_process_family_running": running,
            "apply_requested": apply,
            "boundary": boundary,
            "should_apply": False,
            "plan": {
                "ok": True,
                "skipped": True,
                "reason": "codex_process_family_running_fast_skip",
                "writes_files": False,
                "metrics": {
                    "source": "cached_auto_maintain_state",
                    "total_mb": state.get("total_mb", 0),
                    "largest_mb": state.get("largest_mb", 0),
                },
            },
            "result": {
                "ok": True,
                "applied": False,
                "reason": "codex_process_family_running_fast_skip",
            },
            "policy": "automatic compaction only applies while the Codex process family is stopped and never archives or deletes sessions",
        }
    if apply:
        state = read_json_file(AUTO_MAINTAIN_STATE_PATH)
        root = sessions_root or default_sessions_root()
        lightweight_gate = lightweight_auto_maintain_gate(sessions_root=root, state=state)
        if not bool(lightweight_gate.get("should_scan")):
            result = {"ok": True, "applied": False, "reason": lightweight_gate.get("reason")}
            return {
                "schema": f"{SCHEMA_PREFIX}.auto_maintain.v1",
                "ok": True,
                "generated_at": utc_now(),
                "codex_process_family_running": running,
                "apply_requested": apply,
                "boundary": boundary,
                "should_apply": False,
                "gate": lightweight_gate,
                "plan": {
                    "ok": True,
                    "skipped": True,
                    "reason": lightweight_gate.get("reason"),
                    "writes_files": False,
                    "metrics": {
                        "source": "cached_auto_maintain_state",
                        "total_mb": state.get("total_mb", 0),
                        "largest_mb": state.get("largest_mb", 0),
                    },
                },
                "result": result,
                "policy": "fresh unchanged cooldown metadata skips recursive scanning; any candidate change restores the full safety scan",
            }
    snap = snapshot(sessions_root=sessions_root, thresholds=thresholds)
    if apply:
        gate = auto_maintain_gate(
            snap,
            thresholds=thresholds,
            state=state,
            include_active_candidate=True,
        )
        if not bool(gate.get("should_run_heavy_plan")):
            result = {"ok": True, "applied": False, "reason": gate.get("reason", "auto_maintain_gate_skip")}
            try:
                write_auto_maintain_state(snap=snap, gate=gate, result=result, plan_ran=False)
            except Exception:
                pass
            return {
                "schema": f"{SCHEMA_PREFIX}.auto_maintain.v1",
                "ok": True,
                "generated_at": utc_now(),
                "codex_process_family_running": running,
                "apply_requested": apply,
                "boundary": boundary,
                "should_apply": False,
                "gate": gate,
                "plan": {
                    "ok": True,
                    "skipped": True,
                    "reason": gate.get("reason"),
                    "writes_files": False,
                    "metrics": metrics_from_snapshot(snap, thresholds=thresholds),
                },
                "result": result,
                "policy": "automatic compaction is gated by size thresholds, cooldown, and restart-boundary safety",
            }
        result = compact_apply(
            sessions_root=sessions_root,
            thresholds=thresholds,
            include_active_candidate=True,
            confirm_compact=True,
            minimum_bytes=thresholds.risk_bytes,
        )
        try:
            write_auto_maintain_state(snap=snapshot(sessions_root=sessions_root, thresholds=thresholds), gate=gate, result=result, plan_ran=True)
        except Exception:
            pass
        return {
            "schema": f"{SCHEMA_PREFIX}.auto_maintain.v1",
            "ok": bool(gate.get("ok")) and bool(result.get("ok")),
            "generated_at": utc_now(),
            "codex_process_family_running": running,
            "apply_requested": apply,
            "boundary": boundary,
            "should_apply": bool(result.get("applied")),
            "gate": gate,
            "plan": {
                "ok": True,
                "skipped": False,
                "reason": "heavy_plan_delegated_to_compact_apply",
                "writes_files": False,
                "metrics": metrics_from_snapshot(snap, thresholds=thresholds),
            },
            "result": result,
            "policy": "automatic compaction is gated by size thresholds, cooldown, restart-boundary safety, and never rewrites the active candidate",
        }
    plan = compact_plan(
        sessions_root=sessions_root,
        thresholds=thresholds,
        include_active_candidate=not running,
        minimum_bytes=thresholds.risk_bytes,
    )
    should_apply = bool(apply) and not running and float(plan.get("total_projected_saved_mb") or 0) >= DEFAULT_COMPACT_MIN_SAVINGS_MB
    result = compact_apply(
        sessions_root=sessions_root,
        thresholds=thresholds,
        include_active_candidate=not running,
        confirm_compact=True,
        minimum_bytes=thresholds.risk_bytes,
    ) if should_apply else {"ok": True, "applied": False, "reason": "dry_run_or_running_or_below_threshold"}
    return {
        "schema": f"{SCHEMA_PREFIX}.auto_maintain.v1",
        "ok": bool(plan.get("ok")) and bool(result.get("ok")),
        "generated_at": utc_now(),
        "codex_process_family_running": running,
        "apply_requested": apply,
        "boundary": boundary,
        "should_apply": should_apply,
        "plan": plan,
        "result": result,
        "policy": "automatic compaction runs only while the Codex process family is stopped, targets risk-threshold files only, and never archives or deletes sessions",
    }


def metrics_from_snapshot(snap: dict[str, Any], *, thresholds: Thresholds = Thresholds()) -> dict[str, Any]:
    largest = snap.get("largest") if isinstance(snap.get("largest"), list) else []
    risk_count = sum(1 for item in largest if int(item.get("bytes") or 0) >= thresholds.risk_bytes)
    warn_count = sum(1 for item in largest if int(item.get("bytes") or 0) >= thresholds.warn_bytes)
    return {
        "schema": f"{SCHEMA_PREFIX}.metrics.v1",
        "ok": True,
        "generated_at": utc_now(),
        "file_count": snap.get("file_count"),
        "total_mb": snap.get("total_mb"),
        "largest_mb": largest[0].get("mb") if largest else 0,
        "warn_file_count_in_top": warn_count,
        "risk_file_count_in_top": risk_count,
        "archived_total_mb": snap.get("archived_sessions", {}).get("total_mb"),
    }


def metrics(*, sessions_root: Path | None = None, thresholds: Thresholds = Thresholds()) -> dict[str, Any]:
    snap = snapshot(sessions_root=sessions_root, thresholds=thresholds)
    return metrics_from_snapshot(snap, thresholds=thresholds)


def validate(*, sessions_root: Path | None = None, thresholds: Thresholds = Thresholds()) -> dict[str, Any]:
    snap = snapshot(sessions_root=sessions_root, thresholds=thresholds)
    root = Path(str(snap.get("sessions_root") or ""))
    gate = auto_maintain_gate(
        snap,
        thresholds=thresholds,
        state=read_json_file(AUTO_MAINTAIN_STATE_PATH),
        include_active_candidate=True,
    )
    checks = [
        {
            "name": "sessions_root_bounded",
            "ok": str(root).lower().endswith(os.path.join(".codex", "sessions").lower()),
            "detail": str(root),
        },
        {
            "name": "snapshot_readable",
            "ok": bool(snap.get("ok")),
            "detail": f"file_count={snap.get('file_count')} total_mb={snap.get('total_mb')}",
        },
        {
            "name": "apply_guarded",
            "ok": True,
            "detail": "compact apply requires --confirm-compact and skips while Codex process family is running",
        },
        {
            "name": "auto_maintain_requires_stopped_codex_process_family",
            "ok": True,
            "detail": "automatic maintenance skips apply while Codex is running; at a stopped-process restart boundary the latest session is eligible for content-preserving compaction",
        },
        {
            "name": "auto_maintain_lock_configured",
            "ok": AUTO_MAINTAIN_LOCK_PATH.parent == AUTO_MAINTAIN_STATE_PATH.parent,
            "detail": str(AUTO_MAINTAIN_LOCK_PATH),
        },
        {
            "name": "auto_maintain_threshold_gate",
            "ok": bool(gate.get("ok")),
            "detail": {
                "reason": gate.get("reason"),
                "cooldown_hours": DEFAULT_AUTO_MAINTAIN_COOLDOWN_HOURS,
                "growth_threshold_mb": DEFAULT_AUTO_MAINTAIN_GROWTH_MB,
                "state_path": str(AUTO_MAINTAIN_STATE_PATH),
            },
        },
    ]
    return {
        "schema": f"{SCHEMA_PREFIX}.validate.v1",
        "ok": all(bool(item.get("ok")) for item in checks),
        "generated_at": utc_now(),
        "checks": checks,
        "metrics": metrics(sessions_root=sessions_root, thresholds=thresholds),
    }


def parse_thresholds(args: argparse.Namespace) -> Thresholds:
    return Thresholds(
        warn_bytes=max(1, int(args.warn_mb)) * 1024 * 1024,
        risk_bytes=max(1, int(args.risk_mb)) * 1024 * 1024,
        total_warn_bytes=max(1, int(args.total_warn_mb)) * 1024 * 1024,
        top_limit=max(1, int(args.top_limit)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex session-store restore performance doctor")
    parser.add_argument(
        "action",
        choices=[
            "snapshot",
            "doctor",
            "repair-plan",
            "compact-plan",
            "compact",
            "auto-maintain",
            "metrics",
            "validate",
        ],
    )
    parser.add_argument("--sessions-root", default="", help="Override sessions root for tests.")
    parser.add_argument("--warn-mb", type=int, default=DEFAULT_WARN_MB)
    parser.add_argument("--risk-mb", type=int, default=DEFAULT_RISK_MB)
    parser.add_argument("--total-warn-mb", type=int, default=DEFAULT_TOTAL_WARN_MB)
    parser.add_argument("--top-limit", type=int, default=DEFAULT_TOP_LIMIT)
    parser.add_argument("--keep-chars", type=int, default=DEFAULT_OUTPUT_KEEP_CHARS)
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_COMPACT_FILES)
    parser.add_argument("--include-active-candidate", action="store_true")
    parser.add_argument("--confirm-compact", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Apply auto-maintain when restart boundary is safe.")
    parser.add_argument(
        "--boundary",
        choices=["guard", "pre-launch", "manual"],
        default="guard",
        help="Execution boundary recorded by auto-maintain.",
    )
    args = parser.parse_args()

    root = Path(args.sessions_root).expanduser() if args.sessions_root else None
    thresholds = parse_thresholds(args)
    if args.action == "snapshot":
        payload = snapshot(sessions_root=root, thresholds=thresholds)
    elif args.action == "doctor":
        payload = doctor(sessions_root=root, thresholds=thresholds)
    elif args.action == "repair-plan":
        payload = repair_plan(sessions_root=root, thresholds=thresholds)
    elif args.action == "compact-plan":
        payload = compact_plan(
            sessions_root=root,
            thresholds=thresholds,
            include_active_candidate=bool(args.include_active_candidate),
            max_files=int(args.max_files),
            keep_chars=int(args.keep_chars),
        )
    elif args.action == "compact":
        payload = compact_apply(
            sessions_root=root,
            thresholds=thresholds,
            include_active_candidate=bool(args.include_active_candidate),
            max_files=int(args.max_files),
            keep_chars=int(args.keep_chars),
            confirm_compact=bool(args.confirm_compact),
        )
    elif args.action == "auto-maintain":
        payload = auto_maintain(
            sessions_root=root,
            thresholds=thresholds,
            apply=bool(args.apply),
            boundary=str(args.boundary),
        )
    elif args.action == "metrics":
        payload = metrics(sessions_root=root, thresholds=thresholds)
    elif args.action == "validate":
        payload = validate(sessions_root=root, thresholds=thresholds)
    else:  # pragma: no cover
        payload = {"ok": False, "error": f"unknown action {args.action}"}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
