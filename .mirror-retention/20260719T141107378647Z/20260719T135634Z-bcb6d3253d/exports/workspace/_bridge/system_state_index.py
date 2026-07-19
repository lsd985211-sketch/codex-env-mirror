#!/usr/bin/env python3
"""SQLite-backed system state index for Codex maintenance evidence.

Ownership: this module owns the derived, queryable state index for Codex-facing
maintenance signals.
Non-goals: it does not repair business state, mutate production databases,
replace subsystem doctors, or store secrets/full raw logs.
State behavior: read-only by default; `refresh --apply` writes only to the
derived scratch SQLite database.
Caller context: Codex workflow routing, Hub/SQLite MCP readback, and maintenance
closeout where structured status is faster than scanning logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
DEFAULT_DB = BRIDGE / "data" / "sqlite" / "codex_scratch.sqlite"
MAX_PAYLOAD_BYTES = 16_000
MAX_JSONL_LINES = 250
STALE_AFTER_SECONDS = 6 * 60 * 60
RETENTION_DAYS = 14


@dataclass(frozen=True)
class SignalCommand:
    source: str
    area: str
    owner: str
    command: tuple[str, ...]
    timeout: int = 45


SIGNAL_COMMANDS: tuple[SignalCommand, ...] = (
    SignalCommand("email_scheduler.metrics", "email", "email_scheduler", ("python", "_bridge/shared/email_scheduler.py", "metrics")),
    SignalCommand("record_store.metrics", "record_store", "record_store_maintenance", ("python", "_bridge/shared/record_store_maintenance.py", "metrics")),
    SignalCommand("record_store.query", "record_store", "record_store_maintenance", ("python", "_bridge/shared/record_store_maintenance.py", "query", "--limit", "20")),
    SignalCommand("local_mcp_hub.validate", "mcp", "local_mcp_hub", ("python", "_bridge/local_mcp_hub.py", "validate")),
    SignalCommand("mcp_session.validate", "mcp", "mcp_session_doctor", ("python", "_bridge/mcp_session_doctor.py", "validate")),
    SignalCommand("network_gateway.validate", "network", "codex_network_gateway", ("python", "_bridge/codex_network_gateway.py", "validate")),
    SignalCommand("workflow.validate", "workflow", "workflow_orchestrator", ("python", "_bridge/workflow_orchestrator.py", "validate")),
    SignalCommand("code_maintainability.validate", "code", "code_maintainability", ("python", "_bridge/code_maintainability.py", "validate")),
)


JSONL_SOURCES: tuple[tuple[str, str, str, Path], ...] = (
    ("workflow.checkpoints", "workflow", "codex_workflow_entry", BRIDGE / "workflow" / "checkpoints" / "checkpoints.jsonl"),
    ("workflow.closeouts", "workflow", "codex_workflow_entry", BRIDGE / "workflow" / "closeout" / "closeouts.jsonl"),
    ("resource_broker.events", "resource_layer", "resource_broker", BRIDGE / "logs" / "resource-broker-events.jsonl"),
    ("mcp_session.observations", "mcp", "mcp_session_doctor", BRIDGE / "mobile_openclaw_bridge" / "runtime" / "mcp_session_observations.jsonl"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hidden_creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def compact_json(value: Any, limit: int = MAX_PAYLOAD_BYTES) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = json.dumps(str(value), ensure_ascii=False)
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore") + "...<truncated>"


def stable_id(*parts: Any) -> str:
    text = "|".join(str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:24]


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS system_state_events (
          event_id TEXT PRIMARY KEY,
          observed_at TEXT NOT NULL,
          source TEXT NOT NULL,
          area TEXT NOT NULL,
          owner TEXT NOT NULL,
          kind TEXT NOT NULL,
          status TEXT,
          severity TEXT,
          trace_id TEXT,
          task_id TEXT,
          message TEXT,
          payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_system_state_events_observed_at ON system_state_events(observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_system_state_events_area_status ON system_state_events(area, status, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_system_state_events_trace ON system_state_events(trace_id, observed_at);

        CREATE TABLE IF NOT EXISTS system_state_metrics (
          metric_id TEXT PRIMARY KEY,
          observed_at TEXT NOT NULL,
          source TEXT NOT NULL,
          area TEXT NOT NULL,
          owner TEXT NOT NULL,
          metric_name TEXT NOT NULL,
          value_num REAL,
          value_text TEXT,
          unit TEXT,
          severity TEXT,
          trace_id TEXT,
          payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_system_state_metrics_name ON system_state_metrics(metric_name, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_system_state_metrics_area ON system_state_metrics(area, observed_at DESC);

        CREATE TABLE IF NOT EXISTS system_state_traces (
          trace_id TEXT PRIMARY KEY,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          area TEXT NOT NULL,
          owner TEXT NOT NULL,
          source_count INTEGER NOT NULL,
          event_count INTEGER NOT NULL,
          status TEXT,
          summary TEXT,
          payload_json TEXT NOT NULL
        );
        """
    )
    conn.commit()


def run_json(command: SignalCommand) -> tuple[dict[str, Any], str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            list(command.command),
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=command.timeout,
            creationflags=hidden_creationflags(),
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "returncode": -1,
        }, "exception"
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"ok": False, "stdout_preview": (proc.stdout or "")[:1000], "stderr_preview": (proc.stderr or "")[:1000]}
    payload.setdefault("returncode", proc.returncode)
    if proc.stderr:
        payload.setdefault("stderr_preview", proc.stderr[:1000])
    return payload, "ok" if proc.returncode == 0 else "command_failed"


def severity_for(payload: dict[str, Any], status: str) -> str:
    if status != "ok" or payload.get("ok") is False:
        return "risk"
    if payload.get("issues") or payload.get("blockers"):
        return "risk"
    if payload.get("risk_count") or payload.get("advisory_count"):
        return "advisory"
    return "info"


def value_to_metric(value: Any) -> tuple[float | None, str]:
    if isinstance(value, bool):
        return (1.0 if value else 0.0), str(value).lower()
    if isinstance(value, (int, float)):
        return float(value), str(value)
    if value is None:
        return None, ""
    if isinstance(value, str):
        return None, value[:500]
    return None, compact_json(value, limit=2000)


def flatten_metrics(payload: dict[str, Any], prefix: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    if depth > 3:
        return []
    rows: list[tuple[str, Any]] = []
    for key, value in payload.items():
        if key in {"schema", "generated_at", "checks", "roots", "task", "request", "reference_tables"}:
            continue
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            rows.extend(flatten_metrics(value, name, depth + 1))
        elif isinstance(value, list):
            rows.append((f"{name}.count", len(value)))
        else:
            rows.append((name, value))
    return rows


def event_from_command(command: SignalCommand, payload: dict[str, Any], status: str, observed_at: str) -> dict[str, Any]:
    severity = severity_for(payload, status)
    trace_id = stable_id(command.source, observed_at[:16])
    return {
        "event_id": stable_id(command.source, observed_at, status, compact_json(payload, limit=2000)),
        "observed_at": observed_at,
        "source": command.source,
        "area": command.area,
        "owner": command.owner,
        "kind": "maintenance_signal",
        "status": "ok" if payload.get("ok") is not False and status == "ok" else "failed",
        "severity": severity,
        "trace_id": trace_id,
        "task_id": "",
        "message": str(payload.get("schema") or command.source),
        "payload_json": compact_json(payload),
    }


def metric_rows_from_command(command: SignalCommand, payload: dict[str, Any], observed_at: str, trace_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, value in flatten_metrics(payload):
        value_num, value_text = value_to_metric(value)
        rows.append(
            {
                "metric_id": stable_id(command.source, observed_at, name, value_text),
                "observed_at": observed_at,
                "source": command.source,
                "area": command.area,
                "owner": command.owner,
                "metric_name": name,
                "value_num": value_num,
                "value_text": value_text,
                "unit": "",
                "severity": severity_for(payload, "ok"),
                "trace_id": trace_id,
                "payload_json": compact_json({"value": value}, limit=4000),
            }
        )
    return rows


def read_recent_jsonl(path: Path, limit: int = MAX_JSONL_LINES) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def event_from_jsonl(source: str, area: str, owner: str, payload: dict[str, Any], index: int) -> dict[str, Any]:
    observed = str(payload.get("time") or payload.get("ts") or payload.get("generated_at") or payload.get("created_at") or payload.get("updated_at") or now_iso())
    trace_id = str(payload.get("trace_id") or payload.get("request_id") or payload.get("task_id") or payload.get("schedule_run_id") or payload.get("thread_id") or stable_id(source, observed))
    status = str(payload.get("status") or payload.get("outcome") or ("ok" if payload.get("ok") is not False else "failed"))
    if status in {"handoff_required", "needs_review", "degraded"}:
        severity = "advisory"
    elif status in {"failed", "blocked", "error", "dead_letter"} or payload.get("ok") is False:
        severity = "risk"
    else:
        severity = "info"
    return {
        "event_id": stable_id(source, observed, trace_id, index, compact_json(payload, limit=1000)),
        "observed_at": observed,
        "source": source,
        "area": area,
        "owner": owner,
        "kind": str(payload.get("stage") or payload.get("event") or payload.get("schema") or "jsonl_event"),
        "status": status,
        "severity": severity,
        "trace_id": trace_id,
        "task_id": str(payload.get("task_id") or payload.get("inbox_job_id") or payload.get("schedule_run_id") or ""),
        "message": str(payload.get("message") or payload.get("reason") or payload.get("schema") or source)[:1000],
        "payload_json": compact_json(payload),
    }


def upsert_event(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO system_state_events
        (event_id, observed_at, source, area, owner, kind, status, severity, trace_id, task_id, message, payload_json)
        VALUES (:event_id, :observed_at, :source, :area, :owner, :kind, :status, :severity, :trace_id, :task_id, :message, :payload_json)
        """,
        event,
    )


def upsert_metric(conn: sqlite3.Connection, metric: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO system_state_metrics
        (metric_id, observed_at, source, area, owner, metric_name, value_num, value_text, unit, severity, trace_id, payload_json)
        VALUES (:metric_id, :observed_at, :source, :area, :owner, :metric_name, :value_num, :value_text, :unit, :severity, :trace_id, :payload_json)
        """,
        metric,
    )


def rebuild_traces(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM system_state_traces")
    conn.execute(
        """
        INSERT INTO system_state_traces
        (trace_id, first_seen_at, last_seen_at, area, owner, source_count, event_count, status, summary, payload_json)
        SELECT
          trace_id,
          MIN(observed_at),
          MAX(observed_at),
          COALESCE(MAX(area), ''),
          COALESCE(MAX(owner), ''),
          COUNT(DISTINCT source),
          COUNT(*),
          CASE WHEN SUM(CASE WHEN severity='risk' THEN 1 ELSE 0 END) > 0 THEN 'risk' ELSE MAX(status) END,
          GROUP_CONCAT(DISTINCT kind),
          json_object('risk_count', SUM(CASE WHEN severity='risk' THEN 1 ELSE 0 END), 'advisory_count', SUM(CASE WHEN severity='advisory' THEN 1 ELSE 0 END))
        FROM system_state_events
        WHERE trace_id IS NOT NULL AND trace_id <> ''
        GROUP BY trace_id
        """
    )


def prune_retention(conn: sqlite3.Connection, observed_at: str) -> None:
    current = parse_iso(observed_at) or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = (current - timedelta(days=RETENTION_DAYS)).isoformat()
    conn.execute("DELETE FROM system_state_events WHERE observed_at < ?", (cutoff,))
    conn.execute("DELETE FROM system_state_metrics WHERE observed_at < ?", (cutoff,))


def refresh(*, apply: bool, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    observed_at = now_iso()
    command_events: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    jsonl_events: list[dict[str, Any]] = []
    for command in SIGNAL_COMMANDS:
        payload, status = run_json(command)
        event = event_from_command(command, payload, status, observed_at)
        command_events.append(event)
        metrics.extend(metric_rows_from_command(command, payload, observed_at, event["trace_id"]))
    for source, area, owner, path in JSONL_SOURCES:
        for index, payload in enumerate(read_recent_jsonl(path), start=1):
            jsonl_events.append(event_from_jsonl(source, area, owner, payload, index))

    if apply:
        with connect(db_path) as conn:
            init_db(conn)
            jsonl_source_names = [source for source, _area, _owner, _path in JSONL_SOURCES]
            if jsonl_source_names:
                placeholders = ",".join("?" for _ in jsonl_source_names)
                conn.execute(f"DELETE FROM system_state_events WHERE source IN ({placeholders})", jsonl_source_names)
            for event in command_events + jsonl_events:
                upsert_event(conn, event)
            for metric in metrics:
                upsert_metric(conn, metric)
            prune_retention(conn, observed_at)
            rebuild_traces(conn)
            conn.commit()
    return {
        "schema": "system_state_index.refresh.v1",
        "ok": True,
        "generated_at": observed_at,
        "apply": apply,
        "db_path": str(db_path),
        "command_event_count": len(command_events),
        "jsonl_event_count": len(jsonl_events),
        "metric_count": len(metrics),
        "sources": [command.source for command in SIGNAL_COMMANDS],
        "jsonl_sources": [str(path) for _source, _area, _owner, path in JSONL_SOURCES],
    }


def snapshot(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    exists = db_path.exists()
    tables: dict[str, int] = {}
    if exists:
        with connect(db_path) as conn:
            init_db(conn)
            for table in ("system_state_events", "system_state_metrics", "system_state_traces"):
                tables[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return {
        "schema": "system_state_index.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "db_path": str(db_path),
        "db_exists": exists,
        "tables": tables,
        "sqlite_mcp_route": "sqlite_scratch via native MCP or Hub alias",
    }


def query(db_path: Path = DEFAULT_DB, *, area: str = "", severity: str = "", limit: int = 20) -> dict[str, Any]:
    with connect(db_path) as conn:
        init_db(conn)
        where: list[str] = []
        params: list[Any] = []
        if area:
            where.append("area = ?")
            params.append(area)
        if severity:
            where.append("severity = ?")
            params.append(severity)
        sql = "SELECT observed_at, source, area, owner, kind, status, severity, trace_id, task_id, message FROM system_state_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY observed_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    return {"schema": "system_state_index.query.v1", "ok": True, "rows": rows, "count": len(rows)}


def metrics(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    snap = snapshot(db_path)
    tables = snap.get("tables") if isinstance(snap.get("tables"), dict) else {}
    latest = ""
    risk_count = 0
    if snap.get("db_exists"):
        with connect(db_path) as conn:
            init_db(conn)
            row = conn.execute("SELECT MAX(observed_at) AS latest FROM system_state_events").fetchone()
            latest = str(row["latest"] or "") if row else ""
            risk_count = int(conn.execute("SELECT COUNT(*) FROM system_state_events WHERE severity='risk'").fetchone()[0])
    return {
        "schema": "system_state_index.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "event_count": int(tables.get("system_state_events") or 0),
        "metric_count": int(tables.get("system_state_metrics") or 0),
        "trace_count": int(tables.get("system_state_traces") or 0),
        "risk_event_count": risk_count,
        "latest_event_at": latest,
        "db_path": str(db_path),
    }


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def doctor(db_path: Path = DEFAULT_DB, *, limit: int = 20) -> dict[str, Any]:
    snap = snapshot(db_path)
    checks: list[dict[str, Any]] = [
        {"name": "db_exists", "ok": bool(snap.get("db_exists")), "severity": "risk" if not snap.get("db_exists") else "info", "detail": str(db_path)},
    ]
    rows: list[dict[str, Any]] = []
    by_area: list[dict[str, Any]] = []
    latest = ""
    if snap.get("db_exists"):
        with connect(db_path) as conn:
            init_db(conn)
            latest_row = conn.execute("SELECT MAX(observed_at) AS latest FROM system_state_events").fetchone()
            latest = str(latest_row["latest"] or "") if latest_row else ""
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT observed_at, source, area, owner, kind, status, severity, trace_id, task_id, message
                    FROM system_state_events
                    WHERE severity='risk'
                    ORDER BY observed_at DESC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 100)),),
                ).fetchall()
            ]
            by_area = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT area, severity, COUNT(*) AS count
                    FROM system_state_events
                    GROUP BY area, severity
                    ORDER BY area, severity
                    """
                ).fetchall()
            ]
    latest_dt = parse_iso(latest)
    stale = True
    if latest_dt is not None:
        if latest_dt.tzinfo is None:
            latest_dt = latest_dt.replace(tzinfo=timezone.utc)
        stale = (datetime.now(timezone.utc) - latest_dt).total_seconds() > STALE_AFTER_SECONDS
    checks.append(
        {
            "name": "index_fresh",
            "ok": bool(latest) and not stale,
            "severity": "advisory" if latest and stale else ("risk" if not latest else "info"),
            "detail": latest or "no indexed events",
        }
    )
    checks.append(
        {
            "name": "risk_events_queryable",
            "ok": True,
            "severity": "advisory" if rows else "info",
            "detail": len(rows),
        }
    )
    return {
        "schema": "system_state_index.doctor.v1",
        "ok": not any(item["severity"] == "risk" for item in checks),
        "generated_at": now_iso(),
        "checks": checks,
        "risk_events": rows,
        "counts_by_area_severity": by_area,
        "db_path": str(db_path),
    }


def repair_plan(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    doc = doctor(db_path, limit=5)
    needs_refresh = any(item["name"] == "index_fresh" and not item["ok"] for item in doc.get("checks", []))
    plan: list[dict[str, Any]] = []
    if needs_refresh:
        plan.append(
            {
                "action": "refresh_derived_index",
                "command": f"{sys.executable} _bridge/system_state_index.py refresh --apply",
                "risk": "low",
                "writes": str(db_path),
                "business_state_mutation": False,
                "rollback": "delete system_state_* rows or restore scratch DB backup if needed",
            }
        )
    return {
        "schema": "system_state_index.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "needs_apply": bool(plan),
        "auto_policy": "derived_index_only_no_business_mutation",
        "plan": plan,
        "doctor": doc,
    }


def validate(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    before = snapshot(db_path)
    dry = refresh(apply=False, db_path=db_path)
    checks = [
        {"name": "schema_can_initialize", "ok": True, "detail": str(db_path)},
        {"name": "dry_run_collects_events", "ok": int(dry.get("command_event_count") or 0) >= 3, "detail": dry.get("command_event_count")},
        {"name": "dry_run_collects_metrics", "ok": int(dry.get("metric_count") or 0) >= 10, "detail": dry.get("metric_count")},
        {"name": "snapshot_readable", "ok": bool(before.get("ok")), "detail": before.get("tables", {})},
    ]
    return {
        "schema": "system_state_index.validate.v1",
        "ok": all(item["ok"] for item in checks),
        "generated_at": now_iso(),
        "checks": checks,
        "snapshot": before,
    }


def print_json(payload: dict[str, Any]) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derived SQLite index for system state events, metrics, and traces")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "metrics", "validate", "repair-plan"):
        sub.add_parser(name)
    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--limit", type=int, default=20)
    refresh_parser = sub.add_parser("refresh")
    refresh_parser.add_argument("--apply", action="store_true")
    query_parser = sub.add_parser("query")
    query_parser.add_argument("--area", default="")
    query_parser.add_argument("--severity", default="")
    query_parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    db_path = Path(args.db).expanduser().resolve()
    if args.command == "snapshot":
        payload = snapshot(db_path)
    elif args.command == "metrics":
        payload = metrics(db_path)
    elif args.command == "validate":
        payload = validate(db_path)
    elif args.command == "doctor":
        payload = doctor(db_path, limit=args.limit)
    elif args.command == "repair-plan":
        payload = repair_plan(db_path)
    elif args.command == "refresh":
        payload = refresh(apply=bool(args.apply), db_path=db_path)
    elif args.command == "query":
        payload = query(db_path, area=args.area, severity=args.severity, limit=args.limit)
    else:
        raise AssertionError(args.command)
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
