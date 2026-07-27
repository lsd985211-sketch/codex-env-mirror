#!/usr/bin/env python3
"""Append-only migration ledger in the shared record-store database.

Ownership: migration plans and lifecycle events. Non-goals: moving or deleting
domain files. Domain owners record intent before mutation and append outcomes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / "Desktop" / "Codex\u8d44\u6e90\u5e93" / "\u6587\u6863" / "\u7cfb\u7edf\u7ef4\u62a4" / "\u7d22\u5f15" / "record_store.sqlite"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_migration_id(*, domain: str, owner: str, source_path: str, target_path: str, reason: str) -> str:
    text = "|".join((domain, owner, source_path, target_path, reason))
    return "mig_" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:20]


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS migration_operations (
          migration_id TEXT PRIMARY KEY, domain TEXT NOT NULL, owner TEXT NOT NULL,
          source_path TEXT NOT NULL, target_path TEXT NOT NULL,
          source_sha256 TEXT NOT NULL DEFAULT '', target_sha256 TEXT NOT NULL DEFAULT '',
          reason TEXT NOT NULL, backup_manifest TEXT NOT NULL DEFAULT '',
          rollback_action TEXT NOT NULL DEFAULT '', planned_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_migration_operations_domain
          ON migration_operations(domain, planned_at DESC);
        CREATE TABLE IF NOT EXISTS migration_events (
          event_id TEXT PRIMARY KEY, migration_id TEXT NOT NULL, status TEXT NOT NULL,
          recorded_at TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
          source_sha256 TEXT NOT NULL DEFAULT '', target_sha256 TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          FOREIGN KEY(migration_id) REFERENCES migration_operations(migration_id)
        );
        CREATE INDEX IF NOT EXISTS idx_migration_events_migration
          ON migration_events(migration_id, recorded_at DESC);
        DROP VIEW IF EXISTS migration_current_status;
        CREATE VIEW migration_current_status AS
        SELECT o.*,
          COALESCE((SELECT e.status FROM migration_events e WHERE e.migration_id=o.migration_id ORDER BY e.recorded_at DESC, e.rowid DESC LIMIT 1), 'planned') AS status,
          COALESCE((SELECT e.recorded_at FROM migration_events e WHERE e.migration_id=o.migration_id ORDER BY e.recorded_at DESC, e.rowid DESC LIMIT 1), o.planned_at) AS status_at,
          COALESCE(
            (SELECT e.source_sha256 FROM migration_events e WHERE e.migration_id=o.migration_id AND e.source_sha256<>'' ORDER BY e.recorded_at DESC, e.rowid DESC LIMIT 1),
            o.source_sha256
          ) AS effective_source_sha256,
          COALESCE(
            (SELECT e.target_sha256 FROM migration_events e WHERE e.migration_id=o.migration_id AND e.target_sha256<>'' ORDER BY e.recorded_at DESC, e.rowid DESC LIMIT 1),
            o.target_sha256
          ) AS effective_target_sha256,
          COALESCE(
            (SELECT e.metadata_json FROM migration_events e WHERE e.migration_id=o.migration_id ORDER BY e.recorded_at DESC, e.rowid DESC LIMIT 1),
            '{}'
          ) AS latest_event_metadata_json
        FROM migration_operations o;
        """
    )


def create_operation(*, domain: str, owner: str, source_path: str, target_path: str, reason: str, source_sha256: str = "", target_sha256: str = "", backup_manifest: str = "", rollback_action: str = "", metadata: dict[str, Any] | None = None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    migration_id = stable_migration_id(domain=domain, owner=owner, source_path=source_path, target_path=target_path, reason=reason)
    row = {"migration_id": migration_id, "domain": domain, "owner": owner,
           "source_path": source_path, "target_path": target_path,
           "source_sha256": source_sha256, "target_sha256": target_sha256,
           "reason": reason, "backup_manifest": backup_manifest,
           "rollback_action": rollback_action, "planned_at": now_iso(),
           "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)}
    with closing(connect(db_path)) as conn:
        ensure_schema(conn)
        existing = conn.execute("SELECT * FROM migration_operations WHERE migration_id=?", (migration_id,)).fetchone()
        if existing is not None:
            return {"ok": True, "created": False, "migration_id": migration_id, "operation": dict(existing)}
        conn.execute("""INSERT INTO migration_operations VALUES (
          :migration_id,:domain,:owner,:source_path,:target_path,:source_sha256,
          :target_sha256,:reason,:backup_manifest,:rollback_action,:planned_at,:metadata_json)""", row)
        conn.commit()
    return {"ok": True, "created": True, "migration_id": migration_id, "operation": row}


def append_event(migration_id: str, status: str, *, actor: str = "", detail: str = "", source_sha256: str = "", target_sha256: str = "", metadata: dict[str, Any] | None = None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    event = {"event_id": "migevt_" + uuid.uuid4().hex, "migration_id": migration_id,
             "status": status, "recorded_at": now_iso(), "actor": actor, "detail": detail,
             "source_sha256": source_sha256, "target_sha256": target_sha256,
             "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)}
    with closing(connect(db_path)) as conn:
        ensure_schema(conn)
        if conn.execute("SELECT 1 FROM migration_operations WHERE migration_id=?", (migration_id,)).fetchone() is None:
            return {"ok": False, "reason": "migration_not_found", "migration_id": migration_id}
        conn.execute("""INSERT INTO migration_events VALUES (
          :event_id,:migration_id,:status,:recorded_at,:actor,:detail,
          :source_sha256,:target_sha256,:metadata_json)""", event)
        conn.commit()
    return {"ok": True, "event": event}


def snapshot(*, db_path: Path = DEFAULT_DB, domain: str = "", limit: int = 100) -> dict[str, Any]:
    if not db_path.exists():
        return {"schema": "migration-ledger.snapshot.v1", "ok": True, "db_exists": False, "operations": []}
    with closing(connect(db_path)) as conn:
        ensure_schema(conn)
        sql = "SELECT * FROM migration_current_status"
        params: list[Any] = []
        if domain:
            sql += " WHERE domain=?"
            params.append(domain)
        sql += " ORDER BY planned_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 100), 1000)))
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        event_count = int(conn.execute("SELECT COUNT(*) FROM migration_events").fetchone()[0])
    return {"schema": "migration-ledger.snapshot.v1", "ok": True, "generated_at": now_iso(),
            "db_path": str(db_path), "db_exists": True, "operation_count": len(rows),
            "event_count": event_count, "operations": rows}


def validate(*, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    with closing(connect(db_path)) as conn:
        ensure_schema(conn)
        orphan_count = int(conn.execute("SELECT COUNT(*) FROM migration_events e LEFT JOIN migration_operations o ON o.migration_id=e.migration_id WHERE o.migration_id IS NULL").fetchone()[0])
        terminal_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM migration_current_status WHERE status IN ('applied','verified')"
            ).fetchall()
        ]
    evidence_issues: list[dict[str, Any]] = []
    for row in terminal_rows:
        metadata: dict[str, Any] = {}
        try:
            decoded = json.loads(str(row.get("latest_event_metadata_json") or "{}"))
            metadata = decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            metadata = {}
        missing: list[str] = []
        if not str(row.get("effective_source_sha256") or "").strip():
            missing.append("source_sha256")
        if not str(row.get("effective_target_sha256") or "").strip():
            missing.append("target_sha256")
        rollback_evidence = any(
            str(value or "").strip()
            for value in (
                row.get("backup_manifest"),
                row.get("rollback_action"),
                metadata.get("backup_manifest"),
                metadata.get("rollback_action"),
            )
        )
        if not rollback_evidence:
            missing.append("backup_or_rollback_evidence")
        if missing:
            evidence_issues.append(
                {
                    "migration_id": row.get("migration_id"),
                    "status": row.get("status"),
                    "missing": missing,
                }
            )
    checks = [
        {"name": "operations_table", "ok": True},
        {"name": "events_table", "ok": True},
        {"name": "current_status_view", "ok": True},
        {"name": "no_orphan_events", "ok": orphan_count == 0, "detail": orphan_count},
        {
            "name": "terminal_migrations_have_verifiable_evidence",
            "ok": not evidence_issues,
            "detail": evidence_issues,
        },
    ]
    return {"schema": "migration-ledger.validate.v1", "ok": all(item["ok"] for item in checks), "checks": checks}


def copy_rows_from(source_path: Path, destination: sqlite3.Connection) -> dict[str, int]:
    """Copy ledger rows into a freshly rebuilt record-store database."""
    counts = {"migration_operations": 0, "migration_events": 0}
    if not source_path.exists():
        return counts
    source = sqlite3.connect(str(source_path))
    source.row_factory = sqlite3.Row
    try:
        tables = {str(row[0]) for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in counts:
            if table not in tables:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append-only migration ledger")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--domain", default="")
    snap.add_argument("--limit", type=int, default=100)
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    db_path = Path(args.db).expanduser().resolve()
    payload = snapshot(db_path=db_path, domain=args.domain, limit=args.limit) if args.command == "snapshot" else validate(db_path=db_path)
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
