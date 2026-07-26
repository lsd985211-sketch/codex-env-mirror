#!/usr/bin/env python3
"""Bounded, read-only diagnostics for Codex plugin and native-addon failures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from shared.process_liveness import find_unsafe_zero_signal_probes


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
DEFAULT_LOG_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "Codex" / "Logs"
WER_ROOTS = tuple(
    path
    for path in (
        Path(os.environ.get("ProgramData", "")) / "Microsoft" / "Windows" / "WER" / "ReportArchive",
        Path(os.environ.get("ProgramData", "")) / "Microsoft" / "Windows" / "WER" / "ReportQueue",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "WER" / "ReportArchive",
    )
    if str(path)
)
TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")
CODEX_VERSION_RE = re.compile(r"OpenAI\.Codex_([^_\\/]+)", re.IGNORECASE)
SERIALPORT_PATH_RE = re.compile(r"(?:\\\\\?\\)?[A-Za-z]:\\[^\r\n]*serialport\.node", re.IGNORECASE)
APP_SERVER_INTERRUPT_CODE = "3221225786"
APP_SERVER_INTERRUPT_HEX = "0xC000013A"
NATIVE_INIT_EXCEPTION = "c06d007f"
MAX_LOG_FILES = 16
MAX_LOG_TAIL_BYTES = 1024 * 1024
MAX_WER_REPORTS = 300


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def event_time(line: str, fallback: datetime) -> str:
    match = TIMESTAMP_RE.match(line)
    parsed = parse_timestamp(match.group(1)) if match else None
    return (parsed or fallback).isoformat()


def read_tail(path: Path, max_bytes: int = MAX_LOG_TAIL_BYTES) -> list[str]:
    with path.open("rb") as handle:
        size = path.stat().st_size
        handle.seek(max(0, size - max_bytes))
        raw = handle.read(max_bytes)
    if size > max_bytes:
        raw = raw.split(b"\n", 1)[-1]
    return raw.decode("utf-8", errors="replace").splitlines()


def _event(kind: str, occurred_at: str, source: Path, **details: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "occurred_at": occurred_at,
        "source": str(source),
        "source_kind": "wer" if source.name.casefold() == "report.wer" else "desktop_log",
        **details,
    }


def classify_log_lines(lines: Iterable[str], source: Path, fallback: datetime) -> list[dict[str, Any]]:
    rows = list(lines)
    events: list[dict[str, Any]] = []
    for index, line in enumerate(rows):
        lowered = line.lower()
        occurred_at = event_time(line, fallback)
        if "app_server_connection.closed" in lowered and f"code={APP_SERVER_INTERRUPT_CODE}" in lowered:
            events.append(
                _event(
                    "external_appserver_interrupt",
                    occurred_at,
                    source,
                    exit_code=APP_SERVER_INTERRUPT_CODE,
                    exit_code_hex=APP_SERVER_INTERRUPT_HEX,
                    cause="external_control_event",
                    note="The embedded last stderr message is context, not causal proof.",
                )
            )
            continue
        if "[desktop-notifications][unhandled-rejection]" in lowered and "dll) initialization routine failed" in lowered:
            addon_path = ""
            if index + 1 < len(rows):
                match = SERIALPORT_PATH_RE.search(rows[index + 1])
                addon_path = match.group(0) if match else ""
            events.append(
                _event(
                    "native_addon_initialization_failure",
                    occurred_at,
                    source,
                    addon_path=addon_path,
                    component="@worklouder/device-kit-oai -> @serialport/bindings-cpp",
                    remediation_owner="Codex Desktop package publisher",
                )
            )
            continue
        if "featured plugin fetch failed" in lowered and "401 unauthorized" in lowered:
            events.append(
                _event(
                    "remote_catalog_auth_unavailable",
                    occurred_at,
                    source,
                    status=401,
                    impact="featured remote catalog unavailable; local plugin capability remains independent",
                )
            )
            continue
        if "plugin" in lowered and "icon" in lowered and ("invalid" in lowered or "rejected" in lowered or "must not contain" in lowered):
            events.append(
                _event(
                    "plugin_metadata_rejected",
                    occurred_at,
                    source,
                    impact="affected metadata entry is rejected without proving a process crash",
                )
            )
    return events


def recent_log_files(log_root: Path, since: datetime, limit: int) -> list[Path]:
    if not log_root.is_dir():
        return []
    candidates: list[Path] = []
    for path in log_root.rglob("*.log"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if modified >= since:
            candidates.append(path)
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[:limit]


def parse_wer(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-16", errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        if "EventType" not in values:
            raise UnicodeError
    except (OSError, UnicodeError):
        try:
            for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
        except OSError:
            return {}
    return values


def wer_event_time(values: dict[str, str], fallback: datetime) -> str:
    try:
        ticks = int(values.get("EventTime", "0"))
        unix_seconds = (ticks - 116444736000000000) / 10_000_000
        return datetime.fromtimestamp(unix_seconds, timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return fallback.isoformat()


def recent_wer_events(since: datetime, limit: int) -> list[dict[str, Any]]:
    reports: list[tuple[float, Path]] = []
    for root in WER_ROOTS:
        if not root.is_dir():
            continue
        try:
            directories = root.iterdir()
        except OSError:
            continue
        for directory in directories:
            if not directory.is_dir() or not directory.name.casefold().startswith("appcrash_chatgpt.exe"):
                continue
            report = directory / "Report.wer"
            try:
                modified = report.stat().st_mtime
            except OSError:
                continue
            if datetime.fromtimestamp(modified, timezone.utc) >= since:
                reports.append((modified, report))
    events: list[dict[str, Any]] = []
    for modified, report in sorted(reports, reverse=True)[:limit]:
        values = parse_wer(report)
        exception = values.get("Sig[6].Value", "").casefold().removeprefix("0x")
        if values.get("EventType", "").upper() != "APPCRASH" or exception != NATIVE_INIT_EXCEPTION:
            continue
        app_path = values.get("AppPath", "") or values.get("UI[2]", "")
        version_match = CODEX_VERSION_RE.search(app_path)
        events.append(
            _event(
                "native_addon_initialization_failure",
                wer_event_time(values, datetime.fromtimestamp(modified, timezone.utc)),
                report,
                exception_code=f"0x{exception}",
                fault_module=values.get("Sig[3].Value", ""),
                app_version=version_match.group(1) if version_match else "",
                bucket_id=values.get("Response.BucketId", ""),
                remediation_owner="Codex Desktop package publisher",
            )
        )
    return events


def addon_fingerprint(events: list[dict[str, Any]]) -> dict[str, Any]:
    paths = [str(item.get("addon_path") or "") for item in events if item.get("addon_path")]
    if not paths:
        return {"path": "", "exists": False, "sha256": ""}
    path = Path(paths[0].removeprefix("\\\\?\\"))
    if not path.is_file():
        return {"path": str(path), "exists": False, "sha256": ""}
    digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    return {"path": str(path), "exists": True, "sha256": digest}


def aggregate(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event["kind"])].append(event)
    rows: list[dict[str, Any]] = []
    policies = {
        "external_appserver_interrupt": "Local process owners must use the shared side-effect-free liveness guard.",
        "native_addon_initialization_failure": "Do not patch signed app files; update Codex Desktop and re-probe this version-scoped defect.",
        "remote_catalog_auth_unavailable": "Treat as an API-key authentication limitation, not a local plugin crash.",
        "plugin_metadata_rejected": "Repair only the affected metadata owner; do not disable unrelated plugins.",
    }
    severities = {
        "external_appserver_interrupt": "risk",
        "native_addon_initialization_failure": "upstream_risk",
        "remote_catalog_auth_unavailable": "advisory",
        "plugin_metadata_rejected": "advisory",
    }
    for kind, items in sorted(grouped.items()):
        times = sorted(str(item.get("occurred_at") or "") for item in items)
        source_counts: dict[str, int] = defaultdict(int)
        for item in items:
            source_counts[str(item.get("source_kind") or "unknown")] += 1
        rows.append(
            {
                "code": kind,
                "severity": severities.get(kind, "advisory"),
                "evidence_record_count": len(items),
                "source_counts": dict(sorted(source_counts.items())),
                "first_seen": times[0] if times else "",
                "last_seen": times[-1] if times else "",
                "evidence_refs": list(dict.fromkeys(str(item.get("source") or "") for item in items))[:3],
                "policy": policies.get(kind, "Investigate through the owning runtime surface."),
            }
        )
    return rows


def snapshot(*, since_hours: int = 24, max_log_files: int = MAX_LOG_FILES, max_wer_reports: int = MAX_WER_REPORTS) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, min(since_hours, 24 * 30)))
    log_files = recent_log_files(DEFAULT_LOG_ROOT, since, max(1, min(max_log_files, 64)))
    events: list[dict[str, Any]] = []
    read_errors: list[str] = []
    for path in log_files:
        try:
            fallback = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            events.extend(classify_log_lines(read_tail(path), path, fallback))
        except OSError as exc:
            read_errors.append(f"{path}:{type(exc).__name__}")
    wer_events = recent_wer_events(since, max(1, min(max_wer_reports, 1000)))
    events.extend(wer_events)
    return {
        "schema": "codex_plugin_runtime.snapshot.v1",
        "ok": not read_errors,
        "generated_at": now_iso(),
        "window": {"since": since.isoformat(), "hours": since_hours},
        "sources": {"log_files_read": len(log_files), "wer_reports_matched": len(wer_events), "read_errors": read_errors[:10]},
        "limits": {
            "max_log_files": max_log_files,
            "max_log_tail_bytes_per_file": MAX_LOG_TAIL_BYTES,
            "max_wer_reports": max_wer_reports,
            "wer_limit_reached": len(wer_events) >= max_wer_reports,
        },
        "addon": addon_fingerprint(events),
        "findings": aggregate(events),
        "event_count": len(events),
        "safety": {
            "signed_package_modified": False,
            "plugins_disabled": False,
            "config_modified": False,
            "diagnostic_read_only": True,
        },
    }


def doctor(**kwargs: Any) -> dict[str, Any]:
    payload = snapshot(**kwargs)
    payload["schema"] = "codex_plugin_runtime.doctor.v1"
    payload["status"] = "degraded" if payload["findings"] else "healthy"
    payload["completion_note"] = (
        "Diagnostic health is separate from runtime health; upstream findings do not disable plugins or block startup."
    )
    return payload


def validate() -> dict[str, Any]:
    unsafe = find_unsafe_zero_signal_probes(BRIDGE)
    return {
        "schema": "codex_plugin_runtime.validate.v1",
        "ok": not unsafe,
        "generated_at": now_iso(),
        "checks": [
            {"name": "desktop_log_root", "ok": DEFAULT_LOG_ROOT.is_dir(), "detail": str(DEFAULT_LOG_ROOT)},
            {"name": "unsafe_zero_signal_probe", "ok": not unsafe, "actionable_rows": unsafe[:20]},
            {"name": "signed_package_untouched", "ok": True, "detail": "diagnostic has no package mutation path"},
        ],
    }


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Codex plugin/runtime failure diagnostics")
    parser.add_argument("action", choices=("snapshot", "doctor", "validate"))
    parser.add_argument("--since-hours", type=int, default=24)
    parser.add_argument("--max-log-files", type=int, default=MAX_LOG_FILES)
    parser.add_argument("--max-wer-reports", type=int, default=MAX_WER_REPORTS)
    args = parser.parse_args()
    if args.action == "validate":
        payload = validate()
    else:
        function = snapshot if args.action == "snapshot" else doctor
        payload = function(
            since_hours=args.since_hours,
            max_log_files=args.max_log_files,
            max_wer_reports=args.max_wer_reports,
        )
    emit(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
