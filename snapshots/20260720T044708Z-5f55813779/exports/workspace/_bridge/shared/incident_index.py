#!/usr/bin/env python3
"""Derived incident families and run-level failure metrics in record_store.sqlite.

Ownership: historical incident clustering and metrics. Non-goals: changing
report files, retrying reports, or treating derived rows as business state.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_JSON_BYTES = 4 * 1024 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS incident_families (
          incident_id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
          issue_code TEXT NOT NULL DEFAULT '', exception_class TEXT NOT NULL DEFAULT '',
          execution_stage TEXT NOT NULL DEFAULT '', root_cause_class TEXT NOT NULL DEFAULT '',
          stack_signature TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
          first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
          occurrence_count INTEGER NOT NULL DEFAULT 0, fingerprint_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_incident_families_kind ON incident_families(kind, last_seen_at DESC);
        CREATE TABLE IF NOT EXISTS incident_occurrences (
          occurrence_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
          request_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '',
          occurred_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT '',
          source_path TEXT NOT NULL DEFAULT '', denominator_status TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          FOREIGN KEY(incident_id) REFERENCES incident_families(incident_id)
        );
        CREATE INDEX IF NOT EXISTS idx_incident_occurrences_run ON incident_occurrences(run_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_incident_occurrences_incident ON incident_occurrences(incident_id, occurred_at DESC);
        CREATE TABLE IF NOT EXISTS incident_runs (
          run_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, ok INTEGER NOT NULL,
          source_path TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )


def _read_payload(source_path: str, archive_path: str) -> dict[str, Any]:
    source = Path(source_path)
    try:
        if source.exists() and source.stat().st_size <= MAX_JSON_BYTES:
            return json.loads(source.read_text(encoding="utf-8"))
        archive = Path(archive_path) if archive_path else None
        if archive and archive.exists() and archive.stat().st_size <= MAX_JSON_BYTES:
            with gzip.open(archive, "rt", encoding="utf-8") as handle:
                return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return {}


def _codes(value: Any, limit: int = 50) -> list[str]:
    found: set[str] = set()
    def visit(item: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(item, dict):
            code = item.get("code")
            if isinstance(code, str) and code.strip():
                found.add(code.strip())
            for key, child in item.items():
                if key not in {"pid", "generated_at", "created_at", "updated_at", "timestamp"}:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    return sorted(found)


def _evidence(request: dict[str, Any]) -> dict[str, Any]:
    ref = request.get("evidence_raw_ref") if isinstance(request.get("evidence_raw_ref"), dict) else {}
    raw_path = Path(str(ref.get("raw_path") or ""))
    if raw_path.is_file() and raw_path.stat().st_size <= MAX_JSON_BYTES:
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
            return payload.get("evidence") if isinstance(payload.get("evidence"), dict) else payload
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return request.get("evidence") if isinstance(request.get("evidence"), dict) else {}


def incident_fingerprint(request: dict[str, Any], evidence: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    kind = str(request.get("kind") or "maintenance")
    issue_codes = _codes(evidence)
    result = request.get("result") if isinstance(request.get("result"), dict) else {}
    error_text = str(request.get("error") or result.get("codex_message") or "")
    exception_class = error_text.split(":", 1)[0] if "Error" in error_text.split(":", 1)[0] else ""
    root_cause = "main_codex_process_pressure" if kind == "codex_main_process" else str(evidence.get("reason") or request.get("reason") or "")
    semantic = {
        "owner": "codex_reporter", "kind": kind,
        "issue_codes": issue_codes, "exception_class": exception_class,
        "execution_stage": str(request.get("policy") or "report"),
        "root_cause_class": root_cause,
        "stack_signature": "",
        "title": str(request.get("title") or ""),
    }
    raw = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "inc_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24], semantic


def rebuild(conn: sqlite3.Connection, *, apply: bool) -> dict[str, Any]:
    ensure_schema(conn)
    record_rows_raw = [dict(row) for row in conn.execute(
        "SELECT kind, source_path, archive_path, created_at FROM records WHERE area='system_maintenance' AND kind IN ('execution_record','report_request')"
    ).fetchall()]
    record_rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in record_rows_raw:
        key = (str(row["kind"]), str(row["source_path"]))
        existing = record_rows_by_key.get(key)
        if existing is None or (not existing.get("archive_path") and row.get("archive_path")):
            record_rows_by_key[key] = row
    record_rows = list(record_rows_by_key.values())
    runs: dict[str, dict[str, Any]] = {}
    request_to_run: dict[str, str] = {}
    requests: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in record_rows:
        payload = _read_payload(str(row["source_path"]), str(row["archive_path"] or ""))
        if not payload:
            continue
        schema = str(payload.get("schema") or "")
        if row["kind"] == "execution_record" and schema in {"performance-maintenance-job.v1", "performance-maintenance-record.v2"}:
            trigger = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
            run_id = str(trigger.get("request_id") or Path(str(row["source_path"])).stem)
            runs[run_id] = {"run_id": run_id, "occurred_at": str(payload.get("generated_at") or row["created_at"]),
                            "ok": 1 if payload.get("ok") is True else 0, "source_path": str(row["source_path"]),
                            "metadata_json": json.dumps({"trigger": trigger}, ensure_ascii=False, sort_keys=True)}
            for report_path in payload.get("reports") if isinstance(payload.get("reports"), list) else []:
                request_to_run[str(Path(str(report_path)))] = run_id
        elif row["kind"] == "report_request":
            requests.append((row, payload))
    families: dict[str, dict[str, Any]] = {}
    occurrences: list[dict[str, Any]] = []
    for row, request in requests:
        evidence = _evidence(request)
        incident_id, semantic = incident_fingerprint(request, evidence)
        occurred_at = str(request.get("created_at") or row["created_at"])
        request_id = str(request.get("request_id") or Path(str(row["source_path"])).stem)
        run_id = str(evidence.get("maintenance_run_id") or request_to_run.get(str(Path(str(row["source_path"])))) or "")
        occurrence_id = "occ_" + hashlib.sha256((incident_id + "|" + request_id).encode("utf-8")).hexdigest()[:24]
        occurrences.append({"occurrence_id": occurrence_id, "incident_id": incident_id,
            "request_id": request_id, "run_id": run_id, "occurred_at": occurred_at,
            "status": str(request.get("status") or ""), "source_path": str(row["source_path"]),
            "denominator_status": "resolved" if run_id else "denominator_unresolved",
            "metadata_json": json.dumps({"semantic_digest": request.get("semantic_digest", "")}, ensure_ascii=False, sort_keys=True)})
        family = families.setdefault(incident_id, {"incident_id": incident_id, "owner": semantic["owner"],
            "kind": semantic["kind"], "issue_code": ",".join(semantic["issue_codes"]),
            "exception_class": semantic["exception_class"], "execution_stage": semantic["execution_stage"],
            "root_cause_class": semantic["root_cause_class"], "stack_signature": semantic["stack_signature"],
            "title": semantic["title"], "first_seen_at": occurred_at, "last_seen_at": occurred_at,
            "occurrence_count": 0, "fingerprint_json": json.dumps(semantic, ensure_ascii=False, sort_keys=True)})
        family["first_seen_at"] = min(family["first_seen_at"], occurred_at)
        family["last_seen_at"] = max(family["last_seen_at"], occurred_at)
        family["occurrence_count"] += 1
    if apply:
        conn.execute("DELETE FROM incident_occurrences")
        conn.execute("DELETE FROM incident_families")
        conn.execute("DELETE FROM incident_runs")
        if runs:
            conn.executemany("INSERT INTO incident_runs VALUES (:run_id,:occurred_at,:ok,:source_path,:metadata_json)", runs.values())
        if families:
            conn.executemany("INSERT INTO incident_families VALUES (:incident_id,:owner,:kind,:issue_code,:exception_class,:execution_stage,:root_cause_class,:stack_signature,:title,:first_seen_at,:last_seen_at,:occurrence_count,:fingerprint_json)", families.values())
        if occurrences:
            conn.executemany("INSERT INTO incident_occurrences VALUES (:occurrence_id,:incident_id,:request_id,:run_id,:occurred_at,:status,:source_path,:denominator_status,:metadata_json)", occurrences)
        conn.commit()
    return {"schema": "incident-index.rebuild.v1", "ok": True, "apply": apply,
            "run_count": len(runs), "family_count": len(families), "occurrence_count": len(occurrences),
            "unresolved_occurrence_count": sum(1 for item in occurrences if not item["run_id"])}


def metrics(conn: sqlite3.Connection, *, kind: str = "") -> dict[str, Any]:
    ensure_schema(conn)
    where = "WHERE f.kind=?" if kind else ""
    params: tuple[Any, ...] = (kind,) if kind else ()
    occurrence_count = int(conn.execute(
        f"SELECT COUNT(*) FROM incident_occurrences o JOIN incident_families f ON f.incident_id=o.incident_id {where}", params
    ).fetchone()[0])
    resolved_count = int(conn.execute(
        f"SELECT COUNT(*) FROM incident_occurrences o JOIN incident_families f ON f.incident_id=o.incident_id {where + (' AND' if where else ' WHERE')} o.denominator_status='resolved'", params
    ).fetchone()[0])
    failed_runs = int(conn.execute(
        f"SELECT COUNT(DISTINCT o.run_id) FROM incident_occurrences o JOIN incident_families f ON f.incident_id=o.incident_id {where + (' AND' if where else ' WHERE')} o.run_id<>''", params
    ).fetchone()[0])
    total_runs = int(conn.execute("SELECT COUNT(*) FROM incident_runs").fetchone()[0])
    family_count = int(conn.execute(f"SELECT COUNT(*) FROM incident_families f {where}", params).fetchone()[0])
    coverage = (resolved_count / occurrence_count) if occurrence_count else 1.0
    failure_rate = (failed_runs / total_runs) if total_runs else None
    return {"schema": "incident-index.metrics.v1", "ok": True, "generated_at": now_iso(),
            "kind": kind, "report_count": occurrence_count, "unique_incident_count": family_count,
            "failed_unique_run_count": failed_runs, "total_unique_run_count": total_runs,
            "denominator_resolved_count": resolved_count,
            "denominator_unresolved_count": occurrence_count - resolved_count,
            "denominator_coverage": round(coverage, 6),
            "failure_rate": round(failure_rate, 6) if failure_rate is not None and coverage >= 0.95 else None,
            "failure_rate_publishable": bool(total_runs and coverage >= 0.95)}


def copy_rows_from(source_path: Path, destination: sqlite3.Connection) -> dict[str, int]:
    tables = ("incident_families", "incident_occurrences", "incident_runs")
    counts = {table: 0 for table in tables}
    if not source_path.exists():
        return counts
    source = sqlite3.connect(str(source_path))
    source.row_factory = sqlite3.Row
    try:
        available = {str(row[0]) for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table not in available:
                continue
            rows = [dict(row) for row in source.execute(f"SELECT * FROM {table}").fetchall()]
            if not rows:
                continue
            columns = list(rows[0])
            destination.executemany(
                f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':'+item for item in columns)})",
                rows,
            )
            counts[table] = len(rows)
    finally:
        source.close()
    return counts
