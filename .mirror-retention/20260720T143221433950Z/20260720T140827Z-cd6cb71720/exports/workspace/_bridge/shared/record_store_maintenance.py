#!/usr/bin/env python3
"""Governance for local record stores.

This module inventories known record roots and proposes dry-run compaction,
indexing, and archive actions. Mutations are available only through explicit
apply commands: derived SQLite index refresh, scheduler task governance, and
reversible cold-record archiving with manifest and raw-reference stubs.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .json_cli import now_iso
    from .incident_index import copy_rows_from as copy_incident_rows
    from .incident_index import ensure_schema as ensure_incident_schema
    from .migration_ledger import copy_rows_from as copy_migration_rows
    from .migration_ledger import ensure_schema as ensure_migration_schema
    from .resource_event_store import ensure_schema as ensure_resource_event_schema
    from .resource_event_store import rebuild_from_manifests as rebuild_resource_events
except ImportError:
    from json_cli import now_iso
    from incident_index import copy_rows_from as copy_incident_rows
    from incident_index import ensure_schema as ensure_incident_schema
    from migration_ledger import copy_rows_from as copy_migration_rows
    from migration_ledger import ensure_schema as ensure_migration_schema
    from resource_event_store import ensure_schema as ensure_resource_event_schema
    from resource_event_store import rebuild_from_manifests as rebuild_resource_events


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


RESOURCE_ROOT = Path(r"C:\Users\45543\Desktop\Codex资源库")
DOC_ROOT = RESOURCE_ROOT / "文档"
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_ROOT = WORKSPACE_ROOT / "_bridge"
INDEX_ROOT = DOC_ROOT / "系统维护" / "索引"
INDEX_PATH = INDEX_ROOT / "record_store.sqlite"
ARCHIVE_ROOT = DOC_ROOT / "系统维护" / "归档" / "record-store"
SCHEDULER_RUNTIME_ROOT = DOC_ROOT / "定时模块" / "运行态" / "统一调度"
SCHEDULER_TASKS_PATH = SCHEDULER_RUNTIME_ROOT / "maintenance_tasks.json"
SCHEDULER_STATE_PATH = SCHEDULER_RUNTIME_ROOT / "scheduler-state.json"

HOT_DAYS = 7
WARM_DAYS = 90
LARGE_AREA_BYTES = 500 * 1024 * 1024
SMALL_FILE_FANOUT = 5000
OVERSIZED_RECORD_BYTES = 512 * 1024
LEGACY_OVERSIZED_ARCHIVE_BYTES = 64 * 1024
LEGACY_OVERSIZED_KINDS = {"execution_record", "report_request", "evidence_bundle"}
MAX_TOP_FILES = 12
MAX_PARSE_SAMPLES_PER_ROOT = 8
MAX_JSON_SAMPLE_BYTES = 1024 * 1024
MAX_SUMMARY_READ_BYTES = 1024 * 1024
MAX_TEXT_PREVIEW_BYTES = 4096
MAX_HASH_BYTES = 2 * 1024 * 1024
RECORD_REF_SUFFIX = ".record-ref.json"
INDEX_REFRESH_INTERVAL_SECONDS = 3600
INDEX_REFRESH_LATEST_LAG_SECONDS = 7200


RECORD_STORE_SCHEDULER_TASKS: tuple[dict[str, Any], ...] = (
    {
        "id": "record_store_index_refresh",
        "name": "全局记录索引刷新",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": INDEX_REFRESH_INTERVAL_SECONDS},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/shared/record_store_maintenance.py",
                "index",
                "--apply",
            ],
        },
        "policy": {
            "mode": "controlled-derived-index-refresh",
            "risk": "low",
            "timeout_seconds": 600,
            "retry_interval_seconds": 900,
            "max_retry_count": 2,
            "latest_lag_seconds": INDEX_REFRESH_LATEST_LAG_SECONDS,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "record_store_governance_doctor",
        "name": "全局记录存储治理检查",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 7200},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/shared/record_store_maintenance.py",
                "doctor",
            ],
        },
        "policy": {
            "mode": "read-only",
            "risk": "low",
            "timeout_seconds": 300,
            "retry_interval_seconds": 1800,
            "max_retry_count": 1,
            "latest_lag_seconds": 21600,
            "retry_exhausted_action": "record_and_continue",
        },
    },
)


@dataclass(frozen=True)
class RecordRoot:
    key: str
    area: str
    kind: str
    path: Path
    owner: str
    notes: str


RECORD_ROOTS: tuple[RecordRoot, ...] = (
    RecordRoot(
        key="system_maintenance_records",
        area="system_maintenance",
        kind="execution_record",
        path=DOC_ROOT / "系统维护" / "执行记录",
        owner="performance_maintenance_job/codex_reporter",
        notes="Large JSON maintenance run records; primary current growth hotspot.",
    ),
    RecordRoot(
        key="system_maintenance_requests",
        area="system_maintenance",
        kind="report_request",
        path=DOC_ROOT / "系统维护" / "报告请求",
        owner="codex_reporter",
        notes="Report queue request records.",
    ),
    RecordRoot(
        key="system_maintenance_evidence",
        area="system_maintenance",
        kind="evidence_bundle",
        path=DOC_ROOT / "系统维护" / "证据包",
        owner="codex_reporter",
        notes="Compact evidence bundles referenced by report requests; large raw evidence should live under raw_payload_archive.",
    ),
    RecordRoot(
        key="system_maintenance_raw_payloads",
        area="system_maintenance",
        kind="raw_payload_archive",
        path=DOC_ROOT / "系统维护" / "原始载荷",
        owner="codex_reporter",
        notes="Addressable raw payloads referenced by compact records; index summaries only, never full raw bodies.",
    ),
    RecordRoot(
        key="system_maintenance_reports",
        area="system_maintenance",
        kind="exception_report",
        path=DOC_ROOT / "系统维护" / "异常报告",
        owner="codex_reporter",
        notes="Human-readable generated reports.",
    ),
    RecordRoot(
        key="scheduler_records",
        area="scheduler",
        kind="execution_record",
        path=DOC_ROOT / "定时模块" / "执行记录",
        owner="codex_scheduler_runner/email_scheduler",
        notes="High small-file fanout; records should be indexed and periodically packed.",
    ),
    RecordRoot(
        key="mail_send_records",
        area="mail",
        kind="send_record",
        path=DOC_ROOT / "邮箱区" / "发送记录",
        owner="email_scheduler",
        notes="Currently small, included for a single global record contract.",
    ),
    RecordRoot(
        key="mail_inbox_records",
        area="mail",
        kind="inbox_record",
        path=DOC_ROOT / "邮箱区" / "收件箱",
        owner="email_scheduler",
        notes="Inbound mail mirror and queue records when present.",
    ),
    RecordRoot(
        key="resource_request_manifests",
        area="resource_layer",
        kind="resource_request_manifest",
        path=BRIDGE_ROOT / "resources" / "_requests",
        owner="resource_broker/resource_store",
        notes="Resource request manifests, previews, owner-tool attachments, and receipts. Excludes large resource cache artifacts.",
    ),
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def datetime_from_timestamp(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def month_key(ts: float) -> str:
    return datetime_from_timestamp(ts).strftime("%Y-%m")


def age_days(ts: float, now: datetime) -> float:
    return max(0.0, (now.timestamp() - ts) / 86400)


def tier_for_age(days: float) -> str:
    if days <= HOT_DAYS:
        return "hot"
    if days <= WARM_DAYS:
        return "warm"
    return "cold"


def empty_root_summary(root: RecordRoot) -> dict[str, Any]:
    return {
        "key": root.key,
        "area": root.area,
        "kind": root.kind,
        "path": str(root.path),
        "owner": root.owner,
        "notes": root.notes,
        "exists": root.path.exists(),
        "file_count": 0,
        "directory_count": 0,
        "total_size_bytes": 0,
        "total_size_mb": 0.0,
        "oldest_mtime": "",
        "newest_mtime": "",
        "extension_counts": {},
        "monthly": {},
        "tiers": {"hot": {"count": 0, "bytes": 0}, "warm": {"count": 0, "bytes": 0}, "cold": {"count": 0, "bytes": 0}},
        "largest_files": [],
        "parse_samples": [],
        "scan_error": "",
    }


def bounded_push_largest(bucket: list[dict[str, Any]], item: dict[str, Any], limit: int = MAX_TOP_FILES) -> None:
    bucket.append(item)
    bucket.sort(key=lambda row: int(row.get("size_bytes") or 0), reverse=True)
    del bucket[limit:]


def read_json_sample(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "suffix": path.suffix.lower(),
        "size_bytes": 0,
        "parseable": None,
        "top_level_type": "",
        "top_level_keys": [],
        "error": "",
    }
    try:
        size = path.stat().st_size
        result["size_bytes"] = int(size)
        if path.suffix.lower() != ".json":
            return result
        if size > MAX_JSON_SAMPLE_BYTES:
            result["parseable"] = None
            result["error"] = "sample_skipped_oversized_json"
            return result
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed = json.loads(text)
        result["parseable"] = True
        result["top_level_type"] = type(parsed).__name__
        if isinstance(parsed, dict):
            result["top_level_keys"] = sorted(str(key) for key in parsed.keys())[:30]
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        result["parseable"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def stable_record_id(path: Path) -> str:
    return hashlib.sha256(str(path).lower().encode("utf-8", errors="replace")).hexdigest()


def sha256_file(path: Path, max_bytes: int = MAX_HASH_BYTES) -> tuple[str, str]:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return "", "skipped_oversized"
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest(), "full"
    except OSError as exc:
        return "", f"error:{type(exc).__name__}"


def compact_text(value: Any, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def compact_sequence(values: Any, *, limit: int = 8, item_limit: int = 80) -> str:
    if not isinstance(values, list | tuple):
        return ""
    return ",".join(compact_text(item, item_limit) for item in values[:limit] if str(item or "").strip())


def summarize_resource_manifest(parsed: dict[str, Any], path: Path) -> tuple[str, str, str, str]:
    receipt = parsed.get("receipt") if isinstance(parsed.get("receipt"), dict) else {}
    route = receipt.get("route") if isinstance(receipt.get("route"), dict) else {}
    request = parsed.get("request") if isinstance(parsed.get("request"), dict) else {}
    guidance = receipt.get("codex_guidance") if isinstance(receipt.get("codex_guidance"), dict) else {}
    network_summary = receipt.get("network_summary") if isinstance(receipt.get("network_summary"), dict) else {}
    attempts = receipt.get("attempts") if isinstance(receipt.get("attempts"), list) else []
    attempt_tools = compact_sequence([item.get("tool") for item in attempts if isinstance(item, dict)])
    attempt_statuses = compact_sequence([item.get("status") for item in attempts if isinstance(item, dict)])
    attempt_errors = compact_sequence([item.get("error_class") for item in attempts if isinstance(item, dict)])
    request_id = compact_text(parsed.get("request_id") or receipt.get("request_id") or path.parent.name, 160)
    status = compact_text(receipt.get("status") or receipt.get("ok") or "", 80)
    primary_tool = compact_text(route.get("primary_tool") or "", 120)
    result_kind = compact_text(receipt.get("result_kind") or "", 80)
    intent = compact_text(route.get("intent") or request.get("intent") or "", 120)
    risk_flags = compact_sequence(route.get("risk_flags") or [])
    satisfied = guidance.get("resource_need_satisfied")
    satisfied_text = "unknown" if satisfied is None else str(bool(satisfied)).lower()
    guidance_reason = compact_text(guidance.get("reason") or "", 160)
    next_action = compact_text(receipt.get("next_action") or guidance.get("codex_next_action") or "", 160)
    route_mode = compact_text(network_summary.get("route_mode") or "", 120)
    summary = compact_text(
        f"resource_request {request_id}; status={status}; satisfied={satisfied_text}; satisfaction_reason={guidance_reason}; "
        f"result={result_kind}; intent={intent}; primary_tool={primary_tool}; attempts={attempt_tools}; "
        f"attempt_statuses={attempt_statuses}; attempt_errors={attempt_errors}; next_action={next_action}; "
        f"route_mode={route_mode}; risks={risk_flags}",
        1600,
    )
    return summary, status, request_id, "resource_manifest"


def summarize_json(path: Path) -> tuple[str, str, str, str]:
    try:
        if path.stat().st_size > MAX_SUMMARY_READ_BYTES:
            return path.stem, "", "", "json_skipped_oversized"
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return path.stem, "", "", "json_unparsed"
    if not isinstance(parsed, dict):
        return f"{path.stem}; type={type(parsed).__name__}", "", "", "json"
    schema = compact_text(parsed.get("schema", ""), 160)
    if schema == "resource_store.manifest.v1":
        return summarize_resource_manifest(parsed, path)
    status = compact_text(parsed.get("status") or parsed.get("state") or parsed.get("ok") or "", 80)
    related = compact_text(
        parsed.get("task_id")
        or parsed.get("id")
        or parsed.get("message_id")
        or parsed.get("run_id")
        or "",
        160,
    )
    title = compact_text(parsed.get("title") or parsed.get("subject") or parsed.get("kind") or path.stem, 240)
    keys = ",".join(sorted(str(key) for key in parsed.keys())[:12])
    summary = compact_text(f"{title}; schema={schema}; keys={keys}", 700)
    return summary, status, related, "json"


def summarize_markdown(path: Path) -> tuple[str, str, str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:MAX_TEXT_PREVIEW_BYTES]
    except OSError:
        return path.stem, "", "", "markdown_unreadable"
    lines = [line.strip("# \t") for line in text.splitlines() if line.strip()]
    title = lines[0] if lines else path.stem
    return compact_text(title, 700), "", "", "markdown"


def summarize_record(path: Path) -> tuple[str, str, str, str]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return summarize_json(path)
    if suffix in {".md", ".txt"}:
        return summarize_markdown(path)
    return path.name, "", "", suffix.lstrip(".") or "file"


def iter_files(root: Path) -> tuple[list[Path], str]:
    if not root.exists():
        return [], ""
    files: list[Path] = []
    stack = [root]
    try:
        while stack:
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            files.append(Path(entry.path))
                    except OSError:
                        continue
    except OSError as exc:
        return files, f"{type(exc).__name__}: {exc}"
    return files, ""


def root_snapshot(root: RecordRoot, now: datetime) -> dict[str, Any]:
    summary = empty_root_summary(root)
    files, scan_error = iter_files(root.path)
    summary["scan_error"] = scan_error
    summary["file_count"] = len(files)
    if root.path.exists():
        try:
            summary["directory_count"] = sum(1 for item in root.path.rglob("*") if item.is_dir())
        except OSError:
            summary["directory_count"] = 0

    ext_counts: Counter[str] = Counter()
    monthly: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "bytes": 0})
    tiers: dict[str, dict[str, int]] = {
        "hot": {"count": 0, "bytes": 0},
        "warm": {"count": 0, "bytes": 0},
        "cold": {"count": 0, "bytes": 0},
    }
    total_size = 0
    oldest = 0.0
    newest = 0.0
    largest: list[dict[str, Any]] = []
    sample_candidates: list[Path] = []

    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        size = int(stat.st_size)
        mtime = float(stat.st_mtime)
        total_size += size
        ext = path.suffix.lower() or "<none>"
        ext_counts[ext] += 1
        month = month_key(mtime)
        monthly[month]["count"] += 1
        monthly[month]["bytes"] += size
        tier = tier_for_age(age_days(mtime, now))
        tiers[tier]["count"] += 1
        tiers[tier]["bytes"] += size
        oldest = mtime if not oldest else min(oldest, mtime)
        newest = max(newest, mtime)
        rel_path = str(path.relative_to(root.path)) if path.is_relative_to(root.path) else path.name
        bounded_push_largest(
            largest,
            {
                "path": str(path),
                "relative_path": rel_path,
                "size_bytes": size,
                "size_mb": round(size / 1024 / 1024, 3),
                "mtime": datetime_from_timestamp(mtime).isoformat(),
            },
        )
        if len(sample_candidates) < MAX_PARSE_SAMPLES_PER_ROOT:
            sample_candidates.append(path)
        elif path.suffix.lower() == ".json" and any(item.suffix.lower() != ".json" for item in sample_candidates):
            for index, item in enumerate(sample_candidates):
                if item.suffix.lower() != ".json":
                    sample_candidates[index] = path
                    break

    summary["total_size_bytes"] = total_size
    summary["total_size_mb"] = round(total_size / 1024 / 1024, 2)
    summary["oldest_mtime"] = datetime_from_timestamp(oldest).isoformat() if oldest else ""
    summary["newest_mtime"] = datetime_from_timestamp(newest).isoformat() if newest else ""
    summary["extension_counts"] = dict(sorted(ext_counts.items(), key=lambda item: (-item[1], item[0])))
    summary["monthly"] = {
        key: {"count": value["count"], "bytes": value["bytes"], "mb": round(value["bytes"] / 1024 / 1024, 2)}
        for key, value in sorted(monthly.items())
    }
    summary["tiers"] = {
        key: {"count": value["count"], "bytes": value["bytes"], "mb": round(value["bytes"] / 1024 / 1024, 2)}
        for key, value in tiers.items()
    }
    summary["largest_files"] = largest
    summary["parse_samples"] = [read_json_sample(path) for path in sample_candidates]
    return summary


def snapshot() -> dict[str, Any]:
    generated = now_utc()
    roots = [root_snapshot(root, generated) for root in RECORD_ROOTS]
    total_files = sum(int(item.get("file_count") or 0) for item in roots)
    total_bytes = sum(int(item.get("total_size_bytes") or 0) for item in roots)
    cold_files = sum(int(((item.get("tiers") or {}).get("cold") or {}).get("count") or 0) for item in roots)
    cold_bytes = sum(int(((item.get("tiers") or {}).get("cold") or {}).get("bytes") or 0) for item in roots)
    return {
        "schema": "record-store.snapshot.v1",
        "ok": True,
        "generated_at": generated.isoformat(),
        "resource_root": str(RESOURCE_ROOT),
        "policy": {
            "hot_days": HOT_DAYS,
            "warm_days": WARM_DAYS,
            "large_area_bytes": LARGE_AREA_BYTES,
            "small_file_fanout": SMALL_FILE_FANOUT,
            "oversized_record_bytes": OVERSIZED_RECORD_BYTES,
            "default_scan": "explicit_allowlist_only",
            "mutation": "none",
        },
        "summary": {
            "root_count": len(roots),
            "existing_root_count": sum(1 for item in roots if item.get("exists")),
            "file_count": total_files,
            "total_size_bytes": total_bytes,
            "total_size_mb": round(total_bytes / 1024 / 1024, 2),
            "cold_candidate_count": cold_files,
            "cold_candidate_bytes": cold_bytes,
            "cold_candidate_mb": round(cold_bytes / 1024 / 1024, 2),
        },
        "roots": roots,
    }


def doctor(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    issues: list[dict[str, Any]] = []
    root_summaries: list[dict[str, Any]] = []
    index_info = inspect_index()
    for root in snap.get("roots", []):
        if not isinstance(root, dict):
            continue
        key = str(root.get("key") or "")
        path = str(root.get("path") or "")
        count = int(root.get("file_count") or 0)
        size = int(root.get("total_size_bytes") or 0)
        root_summaries.append(
            {
                "key": key,
                "area": root.get("area", ""),
                "kind": root.get("kind", ""),
                "path": path,
                "exists": bool(root.get("exists")),
                "file_count": count,
                "total_size_mb": root.get("total_size_mb", 0),
                "oldest_mtime": root.get("oldest_mtime", ""),
                "newest_mtime": root.get("newest_mtime", ""),
                "tiers": root.get("tiers", {}),
                "top_extensions": dict(list((root.get("extension_counts") or {}).items())[:5])
                if isinstance(root.get("extension_counts"), dict)
                else {},
            }
        )
        if root.get("scan_error"):
            issues.append(
                {
                    "severity": "risk",
                    "code": "record_root_scan_error",
                    "root": key,
                    "path": path,
                    "message": "Record root scan did not complete cleanly.",
                    "detail": root.get("scan_error"),
                    "manual_action": "Inspect filesystem permissions/path health before migration.",
                }
            )
        if size >= LARGE_AREA_BYTES:
            issues.append(
                {
                    "severity": "risk",
                    "code": "large_record_area",
                    "root": key,
                    "path": path,
                    "size_mb": round(size / 1024 / 1024, 2),
                    "message": "Record area is large enough to slow searches and backups.",
                    "manual_action": "Build a searchable index, then archive cold monthly shards after approval.",
                }
            )
        if count >= SMALL_FILE_FANOUT and root.get("kind") != "raw_payload_archive":
            issues.append(
                {
                    "severity": "risk",
                    "code": "small_file_fanout",
                    "root": key,
                    "path": path,
                    "file_count": count,
                    "message": "Record area has high small-file fanout and is query-unfriendly.",
                    "manual_action": "Keep hot originals, add SQLite index, and pack cold records by month.",
                }
            )
        largest = root.get("largest_files") if isinstance(root.get("largest_files"), list) else []
        oversized = [item for item in largest if int(item.get("size_bytes") or 0) >= OVERSIZED_RECORD_BYTES]
        if oversized:
            issues.append(
                {
                    "severity": "advisory",
                    "code": "oversized_record_files",
                    "root": key,
                    "path": path,
                    "count_in_top_files": len(oversized),
                    "largest_mb": oversized[0].get("size_mb"),
                    "message": "Some records are large enough that full-text scanning should avoid raw body reads.",
                    "manual_action": "Index metadata and summaries first; keep raw payload in cold archive references.",
                }
            )
        samples = root.get("parse_samples") if isinstance(root.get("parse_samples"), list) else []
        bad_samples = [item for item in samples if item.get("parseable") is False]
        if bad_samples:
            issues.append(
                {
                    "severity": "advisory",
                    "code": "unparseable_record_samples",
                    "root": key,
                    "path": path,
                    "sample_count": len(bad_samples),
                    "message": "Some sampled JSON records are not parseable as UTF-8 JSON.",
                    "manual_action": "Exclude or repair malformed samples before indexing this root.",
                }
            )
    if snap.get("summary", {}).get("file_count", 0) and not index_info.get("exists"):
        issues.append(
            {
                "severity": "advisory",
                "code": "record_index_missing",
                "message": "This first-phase framework has not built a SQLite/FTS index yet.",
                "manual_action": "After reviewing dry-run output, approve an index builder that writes only derived metadata.",
            }
        )
    return {
        "schema": "record-store.doctor.v1",
        "ok": not any(item.get("severity") in {"blocker", "risk"} for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": snap.get("summary", {}),
        "index": index_info,
        "roots": root_summaries,
        "snapshot_available_via": "python _bridge\\shared\\record_store_maintenance.py snapshot",
    }


def repair_plan(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    archive = archive_plan(snap=snap, apply=False)
    index_info = inspect_index()
    actions: list[dict[str, Any]] = []
    if not index_info.get("exists"):
        actions.extend(
            [
                {
                    "id": "create_record_store_index",
                    "mode": "proposal",
                    "would_write": False,
                    "target": str(INDEX_PATH),
                    "reason": "Create a derived metadata index with record_id, area, kind, created_at, status, summary, source_path, archive_path, sha256, size, tags, related_task_id.",
                    "safety": "Requires separate approval before any database file is created.",
                },
                {
                    "id": "create_record_store_fts",
                    "mode": "proposal",
                    "would_write": False,
                    "target": "record_store_fts(summary, tags, source_path)",
                    "reason": "Use full-text search only on bounded summaries and tags, not raw large payloads.",
                    "safety": "Requires index creation approval; no raw sensitive body indexing by default.",
                },
            ]
        )
    elif not index_info.get("ok"):
        actions.append(
            {
                "id": "repair_unreadable_record_store_index",
                "mode": "proposal",
                "would_write": False,
                "target": str(INDEX_PATH),
                "reason": str(index_info.get("error") or "existing index is not queryable"),
                "safety": "Rebuild only the derived index after preserving the existing database for diagnosis.",
            }
        )
    else:
        actions.append(
            {
                "id": "keep_record_store_index",
                "mode": "no-op",
                "would_write": False,
                "target": str(INDEX_PATH),
                "record_count": index_info.get("record_count"),
                "reason": "The derived SQLite index already exists and is queryable; plan only incremental refresh or archive work.",
            }
        )
    for root in snap.get("roots", []):
        if not isinstance(root, dict):
            continue
        key = str(root.get("key") or "")
        tiers = root.get("tiers") if isinstance(root.get("tiers"), dict) else {}
        cold = tiers.get("cold") if isinstance(tiers.get("cold"), dict) else {}
        warm = tiers.get("warm") if isinstance(tiers.get("warm"), dict) else {}
        if int(cold.get("count") or 0) > 0:
            root_archive_groups = [
                group
                for group in archive.get("groups", [])
                if isinstance(group, dict) and group.get("root") == key
            ]
            actions.append(
                {
                    "id": f"{key}_archive_cold_monthly",
                    "mode": "dry-run",
                    "would_write": False,
                    "root": key,
                    "source_path": root.get("path"),
                    "candidate_count": int(cold.get("count") or 0),
                    "candidate_mb": round(int(cold.get("bytes") or 0) / 1024 / 1024, 2),
                    "group_count": len(root_archive_groups),
                    "target_pattern": str(ARCHIVE_ROOT / key / "YYYY-MM" / "<relative-path>"),
                    "reason": "Cold originals can be moved into monthly archive shards while a small raw-reference stub remains searchable at the original path.",
                    "apply_command": "python _bridge\\shared\\record_store_maintenance.py archive --apply",
                }
            )
        if int(warm.get("count") or 0) > 0:
            actions.append(
                {
                    "id": f"{key}_index_warm_records",
                    "mode": "dry-run",
                    "would_write": False,
                    "root": key,
                    "candidate_count": int(warm.get("count") or 0),
                    "candidate_mb": round(int(warm.get("bytes") or 0) / 1024 / 1024, 2),
                    "reason": "Warm records should remain addressable through the SQLite metadata index.",
                }
            )
    actions.append(
        {
            "id": "standardize_default_scan_excludes",
            "mode": "proposal",
            "would_write": False,
            "reason": "Global searches and diagnostics should avoid cache, venv, browser profile, node_modules, archive, and generated bulk roots unless explicitly requested.",
            "patterns": [
                "**/node_modules/**",
                "**/.venv/**",
                "**/venv/**",
                "**/__pycache__/**",
                "**/cache/**",
                "**/browser-profile/**",
                "**/归档/**",
                "**/archive/**",
            ],
        }
    )
    return {
        "schema": "record-store.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "default_apply": False,
        "actions": actions,
        "dry_run_contract": {
            "writes_files": False,
            "moves_files": False,
            "deletes_files": False,
            "compresses_files": False,
            "creates_database": False,
        },
        "archive_plan": {
            "candidate_count": archive.get("candidate_count", 0),
            "candidate_mb": archive.get("candidate_mb", 0),
            "group_count": archive.get("group_count", 0),
            "archive_root": archive.get("archive_root", str(ARCHIVE_ROOT)),
        },
    }


def root_by_key(key: str) -> RecordRoot | None:
    for root in RECORD_ROOTS:
        if root.key == key:
            return root
    return None


def archive_ref_path(path: Path) -> Path:
    return path.with_name(f"{path.name}{RECORD_REF_SUFFIX}")


def should_skip_archive_candidate(path: Path) -> bool:
    return path.name.endswith(RECORD_REF_SUFFIX)


def cold_archive_candidates(now: datetime) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for root in RECORD_ROOTS:
        files, scan_error = iter_files(root.path)
        if scan_error:
            continue
        fanout_overflow = len(files) > SMALL_FILE_FANOUT
        for path in files:
            if should_skip_archive_candidate(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            days = age_days(float(stat.st_mtime), now)
            cold_candidate = tier_for_age(days) == "cold"
            legacy_oversized_candidate = (
                root.kind in LEGACY_OVERSIZED_KINDS
                and days > HOT_DAYS
                and int(stat.st_size) >= LEGACY_OVERSIZED_ARCHIVE_BYTES
            )
            legacy_scheduler_candidate = (
                root.key == "scheduler_records"
                and fanout_overflow
                and path.suffix.lower() == ".json"
            )
            fanout_candidate = fanout_overflow and (days > HOT_DAYS or legacy_scheduler_candidate)
            if not cold_candidate and not legacy_oversized_candidate and not fanout_candidate:
                continue
            try:
                relative_path = path.relative_to(root.path)
            except ValueError:
                relative_path = Path(path.name)
            digest, digest_mode = sha256_file(path)
            month = month_key(float(stat.st_mtime))
            archive_relative_path = relative_path.with_name(f"{relative_path.name}.gz")
            candidates.append(
                {
                    "root": root.key,
                    "area": root.area,
                    "kind": root.kind,
                    "owner": root.owner,
                    "source_path": str(path),
                    "relative_path": str(relative_path),
                    "archive_path": str(ARCHIVE_ROOT / root.key / month / archive_relative_path),
                    "reference_path": str(archive_ref_path(path)),
                    "month": month,
                    "size_bytes": int(stat.st_size),
                    "mtime": datetime_from_timestamp(float(stat.st_mtime)).isoformat(),
                    "sha256": digest,
                    "sha256_mode": digest_mode,
                    "candidate_reason": (
                        "cold"
                        if cold_candidate
                        else "legacy_oversized"
                        if legacy_oversized_candidate
                        else "fanout_overflow"
                    ),
                    "compression": "gzip",
                    "reference_mode": "manifest_only" if fanout_candidate else "stub",
                }
            )
    return candidates


def archive_plan(*, snap: dict[str, Any] | None = None, apply: bool = False) -> dict[str, Any]:
    del snap
    candidates = cold_archive_candidates(now_utc())
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidates:
        key = (str(item["root"]), str(item["month"]))
        group = grouped.setdefault(
            key,
            {
                "root": item["root"],
                "month": item["month"],
                "count": 0,
                "bytes": 0,
                "archive_dir": str(ARCHIVE_ROOT / str(item["root"]) / str(item["month"])),
                "manifest_path": str(ARCHIVE_ROOT / str(item["root"]) / str(item["month"]) / "manifest.json"),
            },
        )
        group["count"] += 1
        group["bytes"] += int(item["size_bytes"])
    return {
        "schema": "record-store.archive_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "apply": apply,
        "archive_root": str(ARCHIVE_ROOT),
        "candidate_count": len(candidates),
        "candidate_bytes": sum(int(item["size_bytes"]) for item in candidates),
        "candidate_mb": round(sum(int(item["size_bytes"]) for item in candidates) / 1024 / 1024, 2),
        "group_count": len(grouped),
        "groups": [
            group | {"mb": round(int(group["bytes"]) / 1024 / 1024, 2)}
            for group in sorted(grouped.values(), key=lambda row: (str(row["root"]), str(row["month"])))
        ],
        "sample_candidates": candidates[:20],
        "dry_run_contract": {
            "writes_files": False,
            "moves_files": False,
            "deletes_files": False,
            "compresses_files": False,
            "creates_database": False,
        },
    }


def write_archive_ref(source_path: Path, archived_path: Path, item: dict[str, Any]) -> Path:
    ref_path = archive_ref_path(source_path)
    payload = {
        "schema": "record-store.raw_ref.v1",
        "created_at": now_iso(),
        "summary": source_path.name,
        "source_path": str(source_path),
        "archive_path": str(archived_path),
        "size_bytes": item.get("size_bytes", 0),
        "sha256": item.get("sha256", ""),
        "sha256_mode": item.get("sha256_mode", ""),
        "record_root": item.get("root", ""),
        "month": item.get("month", ""),
        "compression": item.get("compression", "gzip"),
        "rollback": "Decompress archive_path back to source_path, verify sha256/size, then remove this reference file.",
    }
    write_json_file(ref_path, payload)
    return ref_path


def gzip_archive_file(source: Path, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    source_digest = hashlib.sha256()
    source_bytes = 0
    with source.open("rb") as src, gzip.open(temporary, "wb", compresslevel=6) as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            source_digest.update(chunk)
            source_bytes += len(chunk)
            dst.write(chunk)
    verify_digest = hashlib.sha256()
    verify_bytes = 0
    with gzip.open(temporary, "rb") as archived:
        for chunk in iter(lambda: archived.read(1024 * 1024), b""):
            verify_digest.update(chunk)
            verify_bytes += len(chunk)
    if source_bytes != verify_bytes or source_digest.hexdigest() != verify_digest.hexdigest():
        temporary.unlink(missing_ok=True)
        raise RuntimeError("gzip_archive_verification_failed")
    os.replace(temporary, target)
    return {
        "source_bytes": source_bytes,
        "archive_bytes": target.stat().st_size,
        "full_sha256": source_digest.hexdigest(),
    }


def cleanup_manifest_backed_refs_for_fanout_roots() -> int:
    fanout_roots: set[str] = set()
    for root in RECORD_ROOTS:
        files, scan_error = iter_files(root.path)
        if not scan_error and len(files) > SMALL_FILE_FANOUT:
            fanout_roots.add(root.key)
    removed = 0
    if not fanout_roots:
        return removed
    for manifest_path in ARCHIVE_ROOT.glob("*/*/manifest.json"):
        manifest = read_json_file(manifest_path, {})
        items = manifest.get("items") if isinstance(manifest, dict) and isinstance(manifest.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict) or str(item.get("root") or manifest.get("root") or "") not in fanout_roots:
                continue
            archive_text = str(item.get("archive_path") or "")
            reference_text = str(item.get("reference_path") or "")
            archive_path = Path(archive_text) if archive_text else None
            reference_path = Path(reference_text) if reference_text else None
            if archive_path is not None and reference_path is not None and archive_path.exists() and reference_path.exists():
                reference_path.unlink()
                removed += 1
    return removed


def archive_apply() -> dict[str, Any]:
    removed_preexisting_reference_stubs = cleanup_manifest_backed_refs_for_fanout_roots()
    candidates = cold_archive_candidates(now_utc())
    moved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    manifests: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        source = Path(str(item["source_path"]))
        archive_path = Path(str(item["archive_path"]))
        if not source.exists():
            errors.append({"source_path": str(source), "error": "source_missing"})
            continue
        if archive_path.exists():
            errors.append({"source_path": str(source), "archive_path": str(archive_path), "error": "archive_target_exists"})
            continue
        try:
            compression = gzip_archive_file(source, archive_path)
            ref_path = write_archive_ref(source, archive_path, item)
            try:
                source.unlink()
            except OSError:
                ref_path.unlink(missing_ok=True)
                archive_path.unlink(missing_ok=True)
                raise
            row = dict(item)
            row["archive_path"] = str(archive_path)
            row["reference_path"] = str(ref_path)
            row["archived_at"] = now_iso()
            row["archive_bytes"] = compression["archive_bytes"]
            row["full_sha256"] = compression["full_sha256"]
            manifests[(str(item["root"]), str(item["month"]))].append(row)
            moved.append(row)
        except Exception as exc:  # pragma: no cover - defensive repair path
            errors.append({"source_path": str(source), "archive_path": str(archive_path), "error": f"{type(exc).__name__}: {exc}"})
    manifest_paths: list[str] = []
    removed_reference_stubs = 0
    for (root_key, month), rows in manifests.items():
        manifest_path = ARCHIVE_ROOT / root_key / month / "manifest.json"
        existing = read_json_file(manifest_path, {"schema": "record-store.archive_manifest.v1", "items": []})
        if not isinstance(existing, dict):
            existing = {"schema": "record-store.archive_manifest.v1", "items": []}
        existing_items = existing.get("items")
        if not isinstance(existing_items, list):
            existing_items = []
        existing_items.extend(rows)
        existing.update(
            {
                "schema": "record-store.archive_manifest.v1",
                "updated_at": now_iso(),
                "root": root_key,
                "month": month,
                "items": existing_items,
            }
        )
        write_json_file(manifest_path, existing)
        manifest_paths.append(str(manifest_path))
        for row in rows:
            if str(row.get("reference_mode") or "stub") != "manifest_only":
                continue
            ref_path = Path(str(row.get("reference_path") or ""))
            archive_path = Path(str(row.get("archive_path") or ""))
            if archive_path.exists() and ref_path.exists():
                ref_path.unlink()
                removed_reference_stubs += 1
    index_result = build_index(apply=True) if moved or removed_preexisting_reference_stubs else {"ok": True, "reason": "no_archived_files"}
    return {
        "schema": "record-store.archive_result.v1",
        "ok": not errors and bool(index_result.get("ok")),
        "generated_at": now_iso(),
        "apply": True,
        "archive_root": str(ARCHIVE_ROOT),
        "moved_count": len(moved),
        "moved_mb": round(sum(int(item.get("size_bytes") or 0) for item in moved) / 1024 / 1024, 2),
        "archive_mb": round(sum(int(item.get("archive_bytes") or 0) for item in moved) / 1024 / 1024, 2),
        "manifest_paths": manifest_paths,
        "removed_reference_stub_count": removed_reference_stubs,
        "removed_preexisting_reference_stub_count": removed_preexisting_reference_stubs,
        "errors": errors[:20],
        "index_refresh": {
            "ok": index_result.get("ok", False),
            "inserted": index_result.get("inserted", 0),
            "skipped": index_result.get("skipped", 0),
        },
        "mutation": {
            "moved_source_files": bool(moved),
            "created_reference_stubs": bool(moved),
            "deleted_source_files": False,
            "compressed_source_files": bool(moved),
            "refreshed_index": bool(moved),
        },
    }


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS records (
          record_id TEXT PRIMARY KEY,
          area TEXT NOT NULL,
          kind TEXT NOT NULL,
          owner TEXT NOT NULL,
          created_at TEXT NOT NULL,
          modified_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT '',
          summary TEXT NOT NULL DEFAULT '',
          source_path TEXT NOT NULL,
          archive_path TEXT NOT NULL DEFAULT '',
          sha256 TEXT NOT NULL DEFAULT '',
          sha256_mode TEXT NOT NULL DEFAULT '',
          size_bytes INTEGER NOT NULL DEFAULT 0,
          tags TEXT NOT NULL DEFAULT '',
          related_task_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_records_area_kind_created
          ON records(area, kind, created_at);
        CREATE INDEX IF NOT EXISTS idx_records_source_path
          ON records(source_path);
        CREATE INDEX IF NOT EXISTS idx_records_related_task
          ON records(related_task_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
          record_id UNINDEXED,
          summary,
          tags,
          source_path,
          content=''
        );
        """
    )
    ensure_resource_event_schema(conn)
    ensure_migration_schema(conn)
    ensure_incident_schema(conn)


def publish_index(tmp_path: Path) -> str:
    try:
        os.replace(tmp_path, INDEX_PATH)
        return "atomic_replace"
    except PermissionError:
        source = sqlite3.connect(str(tmp_path))
        destination = sqlite3.connect(str(INDEX_PATH), timeout=30)
        try:
            destination.execute("PRAGMA busy_timeout=30000")
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        tmp_path.unlink(missing_ok=True)
        return "sqlite_online_backup"


def cleanup_stale_index_temps(*, keep: Path | None = None) -> list[str]:
    removed: list[str] = []
    cutoff = now_utc().timestamp() - 3600
    for path in INDEX_ROOT.glob(f"{INDEX_PATH.name}.tmp-*"):
        if keep is not None and path == keep:
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed.append(str(path))
        except OSError:
            continue
    return removed


def inspect_index() -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(INDEX_PATH),
        "exists": INDEX_PATH.exists(),
        "size_bytes": 0,
        "record_count": None,
        "fts_exists": False,
        "ok": False,
        "error": "",
    }
    if not INDEX_PATH.exists():
        return info
    try:
        info["size_bytes"] = INDEX_PATH.stat().st_size
        conn = sqlite3.connect(str(INDEX_PATH))
        try:
            count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            fts_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='records_fts'"
            ).fetchone()
        finally:
            conn.close()
        info["record_count"] = int(count)
        info["fts_exists"] = bool(fts_exists)
        info["ok"] = True
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def record_row(root: RecordRoot, path: Path, now: datetime) -> dict[str, Any]:
    stat = path.stat()
    modified = datetime_from_timestamp(float(stat.st_mtime))
    summary, status, related_task_id, summary_mode = summarize_record(path)
    digest, digest_mode = sha256_file(path)
    source_path = str(path)
    archive_path = ""
    indexed_size_bytes = int(stat.st_size)
    archived_ref = path.name.endswith(RECORD_REF_SUFFIX)
    if archived_ref:
        ref_payload = read_json_file(path, {})
        archive_path = str(ref_payload.get("archive_path") or "")
        source_path = str(ref_payload.get("source_path") or source_path)
        indexed_size_bytes = int(ref_payload.get("size_bytes") or indexed_size_bytes)
        digest = str(ref_payload.get("sha256") or digest)
        digest_mode = str(ref_payload.get("sha256_mode") or digest_mode)
        status = "archived"
        summary_mode = "archive_ref"
    name_tokens = compact_text(f"{path.name} {path.stem}", 300)
    if name_tokens and name_tokens not in summary:
        summary = compact_text(f"{name_tokens}; {summary}", 1600)
    tags = ",".join(
        item
        for item in [
            root.area,
            root.kind,
            path.suffix.lower().lstrip(".") or "file",
            tier_for_age(age_days(float(stat.st_mtime), now)),
            summary_mode,
            "archived" if archived_ref else "",
            path.stem,
        ]
        if item
    )
    return {
        "record_id": stable_record_id(path),
        "area": root.area,
        "kind": root.kind,
        "owner": root.owner,
        "created_at": modified.isoformat(),
        "modified_at": modified.isoformat(),
        "status": status,
        "summary": summary,
        "source_path": source_path,
        "archive_path": archive_path,
        "sha256": digest,
        "sha256_mode": digest_mode,
        "size_bytes": indexed_size_bytes,
        "tags": tags,
        "related_task_id": related_task_id,
    }


def archived_manifest_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for manifest_path in ARCHIVE_ROOT.glob("*/*/manifest.json"):
        manifest = read_json_file(manifest_path, {})
        items = manifest.get("items") if isinstance(manifest, dict) and isinstance(manifest.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            reference_text = str(item.get("reference_path") or "")
            if reference_text and Path(reference_text).exists():
                continue
            root = root_by_key(str(item.get("root") or manifest.get("root") or ""))
            if root is None:
                continue
            source_path = str(item.get("source_path") or "")
            archive_path = str(item.get("archive_path") or "")
            if not source_path or not archive_path or not Path(archive_path).exists():
                continue
            modified = str(item.get("mtime") or item.get("archived_at") or now_iso())
            rows.append(
                {
                    "record_id": stable_record_id(Path(source_path)),
                    "area": root.area,
                    "kind": root.kind,
                    "owner": root.owner,
                    "created_at": modified,
                    "modified_at": modified,
                    "status": "archived",
                    "summary": f"archived {Path(source_path).name}; manifest={manifest_path.name}",
                    "source_path": source_path,
                    "archive_path": archive_path,
                    "sha256": str(item.get("sha256") or item.get("full_sha256") or ""),
                    "sha256_mode": str(item.get("sha256_mode") or "full"),
                    "size_bytes": int(item.get("size_bytes") or 0),
                    "tags": ",".join((root.area, root.kind, "archived", "archive_manifest", str(item.get("month") or ""))),
                    "related_task_id": "",
                }
            )
    return rows


def build_index(*, apply: bool = False) -> dict[str, Any]:
    snap = snapshot()
    if not apply:
        return {
            "schema": "record-store.index_plan.v1",
            "ok": True,
            "generated_at": now_iso(),
            "apply": False,
            "target": str(INDEX_PATH),
            "would_write": True,
            "source_summary": snap.get("summary", {}),
            "dry_run_contract": {
                "writes_files": False,
                "moves_files": False,
                "deletes_files": False,
                "compresses_files": False,
                "creates_database": False,
            },
        }

    INDEX_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_path = INDEX_PATH.with_name(f"{INDEX_PATH.name}.tmp-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    if tmp_path.exists():
        tmp_path.unlink()
    generated = now_utc()
    inserted = 0
    skipped = 0
    resource_event_projection = {"requests": 0, "events": 0}
    preserved_governance_projection: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    conn = sqlite3.connect(str(tmp_path))
    try:
        create_schema(conn)
        if INDEX_PATH.exists():
            preserved_governance_projection.update(copy_migration_rows(INDEX_PATH, conn))
            preserved_governance_projection.update(copy_incident_rows(INDEX_PATH, conn))
        insert_sql = """
            INSERT OR REPLACE INTO records (
              record_id, area, kind, owner, created_at, modified_at, status,
              summary, source_path, archive_path, sha256, sha256_mode,
              size_bytes, tags, related_task_id
            ) VALUES (
              :record_id, :area, :kind, :owner, :created_at, :modified_at,
              :status, :summary, :source_path, :archive_path, :sha256,
              :sha256_mode, :size_bytes, :tags, :related_task_id
            )
        """
        fts_sql = """
            INSERT INTO records_fts(record_id, summary, tags, source_path)
            VALUES (:record_id, :summary, :tags, :source_path)
        """
        for root in RECORD_ROOTS:
            files, scan_error = iter_files(root.path)
            if scan_error:
                errors.append({"root": root.key, "path": str(root.path), "error": scan_error})
            for path in files:
                try:
                    row = record_row(root, path, generated)
                    conn.execute(insert_sql, row)
                    conn.execute(fts_sql, row)
                    inserted += 1
                except Exception as exc:  # pragma: no cover - defensive diagnostic path
                    skipped += 1
                    if len(errors) < 20:
                        errors.append({"root": root.key, "path": str(path), "error": f"{type(exc).__name__}: {exc}"})
        for row in archived_manifest_rows():
            conn.execute(insert_sql, row)
            conn.execute(fts_sql, row)
            inserted += 1
        resource_event_projection = rebuild_resource_events(conn, store_root=BRIDGE_ROOT / "resources")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()
    publish_mode = publish_index(tmp_path)
    removed_stale_temps = cleanup_stale_index_temps()
    return {
        "schema": "record-store.index_result.v1",
        "ok": not errors,
        "generated_at": now_iso(),
        "apply": True,
        "target": str(INDEX_PATH),
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "publish_mode": publish_mode,
        "removed_stale_temp_count": len(removed_stale_temps),
        "removed_stale_temps": removed_stale_temps,
        "resource_event_projection": resource_event_projection,
        "preserved_governance_projection": preserved_governance_projection,
        "source_summary": snap.get("summary", {}),
        "mutation": {
            "created_or_replaced_database": True,
            "moved_source_files": False,
            "deleted_source_files": False,
            "compressed_source_files": False,
        },
    }


def query_index(
    term: str = "",
    *,
    limit: int = 20,
    area: str = "",
    kind: str = "",
    status: str = "",
    since: str = "",
    source_contains: str = "",
) -> dict[str, Any]:
    if not INDEX_PATH.exists():
        return {
            "schema": "record-store.query.v1",
            "ok": False,
            "generated_at": now_iso(),
            "reason": "index_missing",
            "index_path": str(INDEX_PATH),
            "manual_action": "Run `python _bridge\\shared\\record_store_maintenance.py index --apply` after approval.",
        }
    conn = sqlite3.connect(str(INDEX_PATH))
    conn.row_factory = sqlite3.Row
    query_mode = "latest"
    fts_error = ""
    try:
        filters: list[str] = []
        params: list[Any] = []
        if area:
            filters.append("r.area = ?")
            params.append(area)
        if kind:
            filters.append("r.kind = ?")
            params.append(kind)
        if status:
            filters.append("r.status = ?")
            params.append(status)
        if since:
            filters.append("r.created_at >= ?")
            params.append(since)
        if source_contains:
            filters.append("r.source_path LIKE ?")
            params.append(f"%{source_contains}%")
        filter_sql = (" AND " + " AND ".join(filters)) if filters else ""
        if term.strip():
            query_mode = "fts"
            try:
                rows = conn.execute(
                    f"""
                    SELECT r.record_id, r.area, r.kind, r.created_at, r.status,
                           r.summary, r.source_path, r.size_bytes, r.tags,
                           bm25(records_fts) AS rank
                    FROM records_fts
                    JOIN records r ON r.record_id = records_fts.record_id
                    WHERE records_fts MATCH ?{filter_sql}
                    ORDER BY rank
                    LIMIT ?
                    """,
                    [term, *params, int(limit)],
                ).fetchall()
            except sqlite3.OperationalError as exc:
                rows = []
                fts_error = str(exc)
            if not rows:
                query_mode = "like_fallback"
                like_term = f"%{term}%"
                rows = conn.execute(
                    f"""
                    SELECT r.record_id, r.area, r.kind, r.created_at, r.status, r.summary,
                           source_path, size_bytes, tags, 0.0 AS rank
                    FROM records r
                    WHERE (summary LIKE ? OR tags LIKE ? OR source_path LIKE ?){filter_sql}
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    [like_term, like_term, like_term, *params, int(limit)],
                ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT r.record_id, r.area, r.kind, r.created_at, r.status, r.summary,
                       source_path, size_bytes, tags, 0.0 AS rank
                FROM records r
                WHERE 1=1{filter_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [*params, int(limit)],
            ).fetchall()
        count = conn.execute("SELECT COUNT(*) AS count FROM records").fetchone()["count"]
    finally:
        conn.close()
    rows_payload = [dict(row) for row in rows]
    return {
        "schema": "record-store.query.v1",
        "ok": True,
        "generated_at": now_iso(),
        "index_path": str(INDEX_PATH),
        "term": term,
        "limit": int(limit),
        "filters": {
            "area": area,
            "kind": kind,
            "status": status,
            "since": since,
            "source_contains": source_contains,
        },
        "query_mode": query_mode,
        "fts_error": fts_error,
        "total_indexed_records": int(count),
        "row_count": len(rows_payload),
        "query_used_index": True,
        "source_read_required": False,
        "next_action": "Use row.source_path for bounded readback only when indexed summary is insufficient; use the owning business CLI/API for any repair.",
        "rows": rows_payload,
    }


def query_resource_requests(
    *,
    request_id: str = "",
    owner_tool: str = "",
    error_class: str = "",
    satisfied: str = "",
    status: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    if not INDEX_PATH.exists():
        return {
            "schema": "record-store.resource-query.v1",
            "ok": False,
            "reason": "index_missing",
            "index_path": str(INDEX_PATH),
        }
    filters = ["area = 'resource_layer'", "kind = 'resource_request_manifest'"]
    params: list[Any] = []
    if request_id:
        filters.append("related_task_id = ?")
        params.append(request_id)
    if owner_tool:
        filters.append("(summary LIKE ? OR summary LIKE ?)")
        params.extend([f"%primary_tool={owner_tool}%", f"%attempts=%{owner_tool}%"])
    if error_class:
        filters.append("summary LIKE ?")
        params.append(f"%attempt_errors=%{error_class}%")
    satisfied_value = str(satisfied or "").strip().lower()
    if satisfied_value in {"true", "false", "unknown"}:
        filters.append("summary LIKE ?")
        params.append(f"%satisfied={satisfied_value}%")
    if status:
        filters.append("status = ?")
        params.append(status)
    conn = sqlite3.connect(str(INDEX_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT record_id, area, kind, created_at, status, summary,
                   source_path, size_bytes, tags, related_task_id
            FROM records
            WHERE {' AND '.join(filters)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, max(1, min(int(limit), 200))],
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) AS count FROM records WHERE {' AND '.join(filters)}",
            params,
        ).fetchone()["count"]
    finally:
        conn.close()
    return {
        "schema": "record-store.resource-query.v1",
        "ok": True,
        "generated_at": now_iso(),
        "index_path": str(INDEX_PATH),
        "filters": {
            "request_id": request_id,
            "owner_tool": owner_tool,
            "error_class": error_class,
            "satisfied": satisfied_value,
            "status": status,
        },
        "matched_count": int(total),
        "row_count": len(rows),
        "rows": [dict(row) for row in rows],
        "query_used_index": True,
        "source_read_required": False,
        "next_action": "Use resource_cli job status/progress/receipt for live ownership or repair; use source_path only for bounded receipt readback.",
    }


def read_json_file(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default
    return default


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def ensure_scheduler_tasks(*, apply: bool = False) -> dict[str, Any]:
    payload = read_json_file(SCHEDULER_TASKS_PATH, {"schema_version": 1, "timezone": "Asia/Shanghai", "tasks": []})
    if not isinstance(payload, dict):
        payload = {"schema_version": 1, "timezone": "Asia/Shanghai", "tasks": []}
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
        payload["tasks"] = tasks
    existing_by_id = {
        str(task.get("id") or ""): index
        for index, task in enumerate(tasks)
        if isinstance(task, dict) and task.get("id")
    }
    actions: list[dict[str, Any]] = []
    resulting_tasks = list(tasks)
    for task in RECORD_STORE_SCHEDULER_TASKS:
        task_id = str(task["id"])
        if task_id in existing_by_id:
            index = existing_by_id[task_id]
            current = resulting_tasks[index] if isinstance(resulting_tasks[index], dict) else {}
            if current == task:
                actions.append({"id": task_id, "action": "keep", "would_write": False})
            else:
                actions.append({"id": task_id, "action": "update", "would_write": True})
                resulting_tasks[index] = json.loads(json.dumps(task, ensure_ascii=False))
        else:
            actions.append({"id": task_id, "action": "insert", "would_write": True})
            resulting_tasks.append(json.loads(json.dumps(task, ensure_ascii=False)))
    changed = any(action["would_write"] for action in actions)
    if apply and changed:
        payload.setdefault("schema_version", 1)
        payload.setdefault("timezone", "Asia/Shanghai")
        payload["tasks"] = resulting_tasks
        payload["record_store_governance"] = {
            "updated_at": now_iso(),
            "mode": "index_refresh_and_doctor_only",
            "archive_apply_enabled": False,
        }
        write_json_file(SCHEDULER_TASKS_PATH, payload)
    state_actions: list[dict[str, Any]] = []
    if apply:
        state_payload = read_json_file(SCHEDULER_STATE_PATH, {"schema_version": 1, "tasks": {}})
        tasks_state = state_payload.get("tasks") if isinstance(state_payload, dict) else {}
        if not isinstance(tasks_state, dict):
            tasks_state = {}
            state_payload["tasks"] = tasks_state
        for task in RECORD_STORE_SCHEDULER_TASKS:
            task_id = str(task["id"])
            task_state = tasks_state.get(task_id)
            if isinstance(task_state, dict) and task_state.get("last_status") == "missed_latest_window":
                task_state["last_status"] = "pending_after_trigger_update"
                task_state["last_attempt_at"] = now_iso()
                task_state["retry_count"] = 0
                task_state.pop("retry_after", None)
                state_actions.append({"id": task_id, "action": "reset_missed_latest_window"})
        if state_actions:
            state_payload["schema_version"] = state_payload.get("schema_version") or 1
            state_payload["updated_at"] = now_iso()
            write_json_file(SCHEDULER_STATE_PATH, state_payload)
    return {
        "schema": "record-store.scheduler_tasks.v1",
        "ok": True,
        "generated_at": now_iso(),
        "apply": apply,
        "tasks_path": str(SCHEDULER_TASKS_PATH),
        "changed": changed,
        "actions": actions,
        "state_actions": state_actions,
        "dry_run_contract": {
            "writes_scheduler_tasks": bool(apply and changed),
            "writes_scheduler_state": bool(state_actions),
            "moves_records": False,
            "deletes_records": False,
            "compresses_records": False,
            "sends_messages": False,
        },
    }


def metrics(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    roots = snap.get("roots") if isinstance(snap.get("roots"), list) else []
    issue_data = doctor(snap)
    archive = archive_plan(snap=snap, apply=False)
    risk_count = sum(1 for item in issue_data.get("issues", []) if item.get("severity") == "risk")
    advisory_count = sum(1 for item in issue_data.get("issues", []) if item.get("severity") == "advisory")
    largest_root = max(roots, key=lambda item: int(item.get("total_size_bytes") or 0), default={})
    most_files_root = max(roots, key=lambda item: int(item.get("file_count") or 0), default={})
    return {
        "schema": "record-store.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "root_count": int(snap.get("summary", {}).get("root_count") or 0),
        "existing_root_count": int(snap.get("summary", {}).get("existing_root_count") or 0),
        "file_count": int(snap.get("summary", {}).get("file_count") or 0),
        "total_size_mb": float(snap.get("summary", {}).get("total_size_mb") or 0),
        "cold_candidate_count": int(snap.get("summary", {}).get("cold_candidate_count") or 0),
        "cold_candidate_mb": round(int(snap.get("summary", {}).get("cold_candidate_bytes") or 0) / 1024 / 1024, 2),
        "risk_count": risk_count,
        "advisory_count": advisory_count,
        "largest_root": {
            "key": largest_root.get("key", ""),
            "size_mb": largest_root.get("total_size_mb", 0),
        },
        "most_files_root": {
            "key": most_files_root.get("key", ""),
            "file_count": most_files_root.get("file_count", 0),
        },
        "archive_candidate_count": int(archive.get("candidate_count") or 0),
        "archive_candidate_mb": float(archive.get("candidate_mb") or 0),
        "archive_group_count": int(archive.get("group_count") or 0),
        "apply_supported": {
            "index": True,
            "ensure_tasks": True,
            "archive": True,
        },
        "record_write_contract": {
            "default": "summary_metadata_raw_ref",
            "large_raw_payload_policy": "store raw in addressable file and write compact summary plus raw reference",
            "index_policy": "index bounded summary/tags/path only; do not index full raw bodies",
        },
        "index_path": str(INDEX_PATH),
        "index_exists": INDEX_PATH.exists(),
        "index_refresh_interval_seconds": INDEX_REFRESH_INTERVAL_SECONDS,
        "index_refresh_latest_lag_seconds": INDEX_REFRESH_LATEST_LAG_SECONDS,
        "archive_root": str(ARCHIVE_ROOT),
        "scheduler_tasks_path": str(SCHEDULER_TASKS_PATH),
        "scheduler_tasks_installed": all(
            action.get("action") == "keep"
            for action in ensure_scheduler_tasks(apply=False).get("actions", [])
        ),
    }


def validate() -> dict[str, Any]:
    snap = snapshot()
    plan = repair_plan(snap)
    archive = archive_plan(snap=snap, apply=False)
    resource_summary = summarize_resource_manifest(
        {
            "schema": "resource_store.manifest.v1",
            "request_id": "res_validate",
            "request": {"intent": "documentation_lookup"},
            "receipt": {
                "status": "handoff_required",
                "result_kind": "metadata",
                "next_action": "refine_resource_delegation_and_retry",
                "route": {"primary_tool": "resource_router", "intent": "documentation_lookup"},
                "codex_guidance": {"resource_need_satisfied": False, "reason": "owner_result_low_relevance"},
                "network_summary": {"route_mode": "probe_selected_direct"},
                "attempts": [
                    {"tool": "context7", "status": "degraded", "error_class": "low_relevance"},
                    {"tool": "microsoftdocs", "status": "handoff_required", "error_class": ""},
                ],
            },
        },
        Path("res_validate") / "manifest.json",
    )[0]
    checks = [
        {
            "name": "resource_root_exists",
            "ok": RESOURCE_ROOT.exists(),
            "detail": str(RESOURCE_ROOT),
        },
        {
            "name": "explicit_allowlist_only",
            "ok": bool(RECORD_ROOTS) and all(isinstance(root.path, Path) for root in RECORD_ROOTS),
            "detail": f"{len(RECORD_ROOTS)} roots configured",
        },
        {
            "name": "dry_run_has_no_mutation",
            "ok": not any(action.get("would_write") for action in plan.get("actions", [])),
            "detail": "repair-plan is proposal/dry-run only",
        },
        {
            "name": "index_query_safe_when_missing",
            "ok": query_index("", limit=1).get("reason") in {"index_missing", None},
            "detail": str(INDEX_PATH),
        },
        {
            "name": "resource_query_uses_existing_index",
            "ok": query_resource_requests(limit=1).get("reason") in {"index_missing", None},
            "detail": "resource request filters reuse record_store.sqlite",
        },
        {
            "name": "maintenance_contract_present",
            "ok": True,
            "detail": "snapshot/doctor/repair-plan/validate/metrics are implemented",
        },
        {
            "name": "scheduler_tasks_governed",
            "ok": bool(ensure_scheduler_tasks(apply=False).get("actions")),
            "detail": str(SCHEDULER_TASKS_PATH),
        },
        {
            "name": "archive_plan_dry_run_safe",
            "ok": archive.get("dry_run_contract", {}).get("moves_files") is False
            and archive.get("dry_run_contract", {}).get("deletes_files") is False,
            "detail": str(ARCHIVE_ROOT),
        },
        {
            "name": "record_write_contract_present",
            "ok": metrics(snap).get("record_write_contract", {}).get("default") == "summary_metadata_raw_ref",
            "detail": "summary + metadata + raw reference",
        },
        {
            "name": "resource_manifest_observability_fields_indexed",
            "ok": all(
                token in resource_summary
                for token in (
                    "satisfied=false",
                    "satisfaction_reason=owner_result_low_relevance",
                    "attempts=context7,microsoftdocs",
                    "attempt_errors=low_relevance",
                    "next_action=refine_resource_delegation_and_retry",
                    "route_mode=probe_selected_direct",
                )
            ),
            "detail": resource_summary,
        },
    ]
    return {
        "schema": "record-store.validate.v1",
        "ok": all(item.get("ok") for item in checks),
        "generated_at": now_iso(),
        "checks": checks,
        "metrics": metrics(snap),
    }


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local record-store maintenance")
    parser.add_argument(
        "command",
        choices=["snapshot", "doctor", "repair-plan", "validate", "metrics", "index", "query", "resource-query", "ensure-tasks", "archive"],
    )
    parser.add_argument("--apply", action="store_true", help="Apply the requested controlled action.")
    parser.add_argument("--term", default="", help="FTS query term for query command.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum query rows.")
    parser.add_argument("--area", default="", help="Filter query rows by indexed area.")
    parser.add_argument("--kind", default="", help="Filter query rows by indexed kind.")
    parser.add_argument("--status", default="", help="Filter query rows by indexed status.")
    parser.add_argument("--since", default="", help="Filter query rows by created_at lower bound.")
    parser.add_argument("--source-contains", default="", help="Filter query rows by source path substring.")
    parser.add_argument("--request-id", default="", help="Filter resource-query by exact request id.")
    parser.add_argument("--owner-tool", default="", help="Filter resource-query by primary or attempted owner tool.")
    parser.add_argument("--error-class", default="", help="Filter resource-query by attempted error class.")
    parser.add_argument("--satisfied", default="", choices=("", "true", "false", "unknown"), help="Filter resource-query by satisfaction state.")
    args = parser.parse_args(argv)

    snap: dict[str, Any] | None = None
    if args.command in {"doctor", "repair-plan", "metrics"}:
        snap = snapshot()
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "doctor":
        payload = doctor(snap)
    elif args.command == "repair-plan":
        payload = repair_plan(snap)
    elif args.command == "validate":
        payload = validate()
    elif args.command == "metrics":
        payload = metrics(snap)
    elif args.command == "index":
        payload = build_index(apply=bool(args.apply))
    elif args.command == "query":
        payload = query_index(
            args.term,
            limit=args.limit,
            area=args.area,
            kind=args.kind,
            status=args.status,
            since=args.since,
            source_contains=args.source_contains,
        )
    elif args.command == "resource-query":
        payload = query_resource_requests(
            request_id=args.request_id,
            owner_tool=args.owner_tool,
            error_class=args.error_class,
            satisfied=args.satisfied,
            status=args.status,
            limit=args.limit,
        )
    elif args.command == "ensure-tasks":
        payload = ensure_scheduler_tasks(apply=bool(args.apply))
    elif args.command == "archive":
        payload = archive_apply() if args.apply else archive_plan(apply=False)
    else:  # pragma: no cover
        raise SystemExit(f"unknown command: {args.command}")
    print_json(payload)
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
