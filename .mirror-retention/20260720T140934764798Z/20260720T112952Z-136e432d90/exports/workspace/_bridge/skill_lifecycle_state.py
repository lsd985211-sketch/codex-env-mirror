#!/usr/bin/env python3
"""Persistent SQLite state for incremental skill lifecycle governance.

Ownership: store normalized skill records, fingerprints, scan runs, and change
events produced by ``skill_lifecycle_governance.py``.
Non-goals: discover, interpret, route, edit, install, or delete skills.
State behavior: write only the derived index under ``_bridge/runtime``.
Caller context: lifecycle refresh, status, history, and regression evidence.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
STATE_DB = ROOT / "_bridge" / "runtime" / "skill_lifecycle" / "skill_lifecycle.sqlite"
SCHEMA_VERSION = 2
LINEAGE_KINDS = {"FIX", "DERIVED", "CAPTURED"}
QUALITY_EVENT_KINDS = {
    "selected",
    "applied",
    "completed",
    "failed",
    "partial",
    "skipped",
    "fallback",
    "validated",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect(path: Path = STATE_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS lifecycle_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skill_state (
            path TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            name TEXT NOT NULL,
            stat_fingerprint TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_changed_at TEXT NOT NULL,
            removed_at TEXT NOT NULL DEFAULT '',
            record_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_skill_state_source_status
            ON skill_state(source, status);
        CREATE TABLE IF NOT EXISTS governance_run (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL,
            scope TEXT NOT NULL,
            bootstrap INTEGER NOT NULL,
            discovered_count INTEGER NOT NULL,
            added_count INTEGER NOT NULL,
            modified_count INTEGER NOT NULL,
            removed_count INTEGER NOT NULL,
            unchanged_count INTEGER NOT NULL,
            summary_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skill_change (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES governance_run(id) ON DELETE CASCADE,
            observed_at TEXT NOT NULL,
            change_kind TEXT NOT NULL,
            path TEXT NOT NULL,
            source TEXT NOT NULL,
            name TEXT NOT NULL,
            old_fingerprint TEXT NOT NULL,
            new_fingerprint TEXT NOT NULL,
            detail_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_skill_change_observed
            ON skill_change(observed_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS skill_quality_event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            occurred_at TEXT NOT NULL,
            skill_id TEXT NOT NULL DEFAULT '',
            skill_name TEXT NOT NULL,
            event_kind TEXT NOT NULL,
            task_kind TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL DEFAULT '',
            validation TEXT NOT NULL DEFAULT '',
            fallback TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_skill_quality_name_time
            ON skill_quality_event(skill_name, occurred_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS skill_lineage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lineage_key TEXT NOT NULL UNIQUE,
            recorded_at TEXT NOT NULL,
            evolution_kind TEXT NOT NULL,
            skill_id TEXT NOT NULL DEFAULT '',
            skill_name TEXT NOT NULL,
            parent_version TEXT NOT NULL DEFAULT '',
            child_version TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL,
            validation_evidence TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_skill_lineage_name_time
            ON skill_lineage(skill_name, recorded_at DESC, id DESC);
        """
    )
    existing_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(skill_state)").fetchall()
    }
    for column, declaration in (
        ("skill_id", "TEXT NOT NULL DEFAULT ''"),
        ("admission_state", "TEXT NOT NULL DEFAULT ''"),
        ("trust_state", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE skill_state ADD COLUMN {column} {declaration}")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_state_trust ON skill_state(trust_state, status)"
    )
    connection.execute(
        "INSERT INTO lifecycle_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    connection.commit()


def active_rows(path: Path = STATE_DB) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    connection = connect(path)
    try:
        rows = connection.execute(
            "SELECT path, source, name, stat_fingerprint, content_sha256, status, "
            "skill_id, admission_state, trust_state, record_json "
            "FROM skill_state WHERE status='active'"
        ).fetchall()
    finally:
        connection.close()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            record = json.loads(str(row["record_json"]))
        except json.JSONDecodeError:
            record = {}
        result[str(row["path"])] = {
            "path": str(row["path"]),
            "source": str(row["source"]),
            "name": str(row["name"]),
            "stat_fingerprint": str(row["stat_fingerprint"]),
            "content_sha256": str(row["content_sha256"]),
            "skill_id": str(row["skill_id"]),
            "admission_state": str(row["admission_state"]),
            "trust_state": str(row["trust_state"]),
            "record": record if isinstance(record, dict) else {},
        }
    return result


def sync_records(
    entries: Iterable[dict[str, Any]],
    *,
    path: Path = STATE_DB,
    scope: str = "all",
    sources: Iterable[str] = ("user", "system", "plugin"),
) -> dict[str, Any]:
    observed_at = now_iso()
    normalized = {str(item["path"]): dict(item) for item in entries}
    source_set = {str(value) for value in sources}
    connection = connect(path)
    try:
        existing_rows = connection.execute("SELECT * FROM skill_state").fetchall()
        existing = {str(row["path"]): row for row in existing_rows}
        bootstrap = not bool(existing_rows)
        changes: list[dict[str, Any]] = []
        unchanged_count = 0

        for item_path, item in normalized.items():
            old = existing.get(item_path)
            old_fingerprint = str(old["stat_fingerprint"]) if old else ""
            old_status = str(old["status"]) if old else ""
            new_fingerprint = str(item["stat_fingerprint"])
            if old is None or old_status == "removed":
                change_kind = "added"
            elif old_fingerprint != new_fingerprint:
                change_kind = "modified"
            else:
                change_kind = ""
                unchanged_count += 1
            if change_kind:
                changes.append(
                    {
                        "change_kind": change_kind,
                        "path": item_path,
                        "source": str(item["source"]),
                        "name": str(item["name"]),
                        "old_fingerprint": old_fingerprint,
                        "new_fingerprint": new_fingerprint,
                        "detail": {
                            "routing_eligible": bool(item.get("record", {}).get("routing_eligible", True)),
                            "flags": list(item.get("record", {}).get("flags") or []),
                        },
                    }
                )
            first_seen = str(old["first_seen_at"]) if old else observed_at
            last_changed = observed_at if change_kind else str(old["last_changed_at"])
            connection.execute(
                """
                INSERT INTO skill_state(
                    path, source, name, stat_fingerprint, content_sha256, status,
                    first_seen_at, last_seen_at, last_changed_at, removed_at, record_json,
                    skill_id, admission_state, trust_state
                ) VALUES(?, ?, ?, ?, ?, 'active', ?, ?, ?, '', ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    source=excluded.source,
                    name=excluded.name,
                    stat_fingerprint=excluded.stat_fingerprint,
                    content_sha256=excluded.content_sha256,
                    status='active',
                    last_seen_at=excluded.last_seen_at,
                    last_changed_at=excluded.last_changed_at,
                    removed_at='',
                    record_json=excluded.record_json,
                    skill_id=excluded.skill_id,
                    admission_state=excluded.admission_state,
                    trust_state=excluded.trust_state
                """,
                (
                    item_path,
                    str(item["source"]),
                    str(item["name"]),
                    new_fingerprint,
                    str(item.get("content_sha256") or ""),
                    first_seen,
                    observed_at,
                    last_changed,
                    json.dumps(item.get("record") or {}, ensure_ascii=False, sort_keys=True),
                    str(item.get("record", {}).get("skill_id") or ""),
                    str(item.get("record", {}).get("admission_state") or ""),
                    str(item.get("record", {}).get("trust_state") or ""),
                ),
            )

        for item_path, old in existing.items():
            if str(old["source"]) not in source_set or item_path in normalized or str(old["status"]) == "removed":
                continue
            changes.append(
                {
                    "change_kind": "removed",
                    "path": item_path,
                    "source": str(old["source"]),
                    "name": str(old["name"]),
                    "old_fingerprint": str(old["stat_fingerprint"]),
                    "new_fingerprint": "",
                    "detail": {},
                }
            )
            connection.execute(
                "UPDATE skill_state SET status='removed', last_seen_at=?, last_changed_at=?, removed_at=? WHERE path=?",
                (observed_at, observed_at, observed_at, item_path),
            )

        counts = {
            "added": sum(1 for item in changes if item["change_kind"] == "added"),
            "modified": sum(1 for item in changes if item["change_kind"] == "modified"),
            "removed": sum(1 for item in changes if item["change_kind"] == "removed"),
            "unchanged": unchanged_count,
        }
        summary = {
            "scope": scope,
            "bootstrap": bootstrap,
            "discovered_count": len(normalized),
            "change_count": len(changes),
            "counts": counts,
        }
        connection.execute(
            "INSERT INTO lifecycle_meta(key, value) VALUES('last_scanned_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (observed_at,),
        )
        if changes:
            cursor = connection.execute(
                """
                INSERT INTO governance_run(
                    observed_at, scope, bootstrap, discovered_count, added_count,
                    modified_count, removed_count, unchanged_count, summary_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_at, scope, int(bootstrap), len(normalized), counts["added"],
                    counts["modified"], counts["removed"], counts["unchanged"],
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                ),
            )
            run_id = int(cursor.lastrowid)
            for item in changes:
                connection.execute(
                    """
                    INSERT INTO skill_change(
                        run_id, observed_at, change_kind, path, source, name,
                        old_fingerprint, new_fingerprint, detail_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, observed_at, item["change_kind"], item["path"],
                        item["source"], item["name"], item["old_fingerprint"],
                        item["new_fingerprint"],
                        json.dumps(item["detail"], ensure_ascii=False, sort_keys=True),
                    ),
                )
        else:
            latest = connection.execute("SELECT id FROM governance_run ORDER BY id DESC LIMIT 1").fetchone()
            run_id = int(latest[0]) if latest else 0
        connection.commit()
    finally:
        connection.close()
    return {
        "schema": "skill_lifecycle_state.sync.v1",
        "ok": True,
        "state_db": str(path),
        "run_id": run_id,
        "recorded_run": bool(changes),
        "observed_at": observed_at,
        **summary,
        "changes": changes,
    }


def snapshot(path: Path = STATE_DB, *, recent_limit: int = 20) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": "skill_lifecycle_state.snapshot.v1",
            "ok": True,
            "available": False,
            "state_db": str(path),
            "active_count": 0,
            "removed_count": 0,
            "run_count": 0,
            "recent_changes": [],
        }
    connection = connect(path)
    try:
        active_count = int(connection.execute("SELECT COUNT(*) FROM skill_state WHERE status='active'").fetchone()[0])
        removed_count = int(connection.execute("SELECT COUNT(*) FROM skill_state WHERE status='removed'").fetchone()[0])
        run_count = int(connection.execute("SELECT COUNT(*) FROM governance_run").fetchone()[0])
        last_run_row = connection.execute("SELECT * FROM governance_run ORDER BY id DESC LIMIT 1").fetchone()
        last_scanned_row = connection.execute("SELECT value FROM lifecycle_meta WHERE key='last_scanned_at'").fetchone()
        recent_rows = []
        if recent_limit > 0:
            recent_rows = connection.execute(
                "SELECT observed_at, change_kind, path, source, name, detail_json "
                "FROM skill_change ORDER BY id DESC LIMIT ?",
                (min(int(recent_limit), 200),),
            ).fetchall()
    finally:
        connection.close()
    recent_changes = []
    for row in recent_rows:
        try:
            detail = json.loads(str(row["detail_json"]))
        except json.JSONDecodeError:
            detail = {}
        recent_changes.append(
            {
                "observed_at": str(row["observed_at"]),
                "change_kind": str(row["change_kind"]),
                "path": str(row["path"]),
                "source": str(row["source"]),
                "name": str(row["name"]),
                "detail": detail,
            }
        )
    last_run = dict(last_run_row) if last_run_row else {}
    if last_run:
        last_run.pop("summary_json", None)
        last_run["bootstrap"] = bool(last_run.get("bootstrap"))
    return {
        "schema": "skill_lifecycle_state.snapshot.v1",
        "ok": True,
        "available": True,
        "state_db": str(path),
        "schema_version": SCHEMA_VERSION,
        "active_count": active_count,
        "removed_count": removed_count,
        "run_count": run_count,
        "last_scanned_at": str(last_scanned_row[0]) if last_scanned_row else "",
        "last_run": last_run,
        "recent_changes": recent_changes,
    }


def record_quality_events(events: Iterable[dict[str, Any]], path: Path = STATE_DB) -> dict[str, Any]:
    normalized_events = [dict(event) for event in events]
    invalid_kinds = sorted(
        {
            str(event.get("event_kind") or "")
            for event in normalized_events
            if str(event.get("event_kind") or "") not in QUALITY_EVENT_KINDS
        }
    )
    if invalid_kinds:
        return {
            "schema": "skill_lifecycle_state.quality_events.v1",
            "ok": False,
            "error": "invalid_event_kind",
            "invalid": invalid_kinds,
            "allowed": sorted(QUALITY_EVENT_KINDS),
        }
    connection = connect(path)
    inserted = 0
    duplicate = 0
    try:
        for event in normalized_events:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO skill_quality_event(
                    event_key, occurred_at, skill_id, skill_name, event_kind,
                    task_kind, outcome, validation, fallback, notes, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event["event_key"]),
                    str(event.get("occurred_at") or now_iso()),
                    str(event.get("skill_id") or ""),
                    str(event.get("skill_name") or ""),
                    str(event.get("event_kind") or ""),
                    str(event.get("task_kind") or ""),
                    str(event.get("outcome") or ""),
                    str(event.get("validation") or ""),
                    str(event.get("fallback") or ""),
                    str(event.get("notes") or "")[:500],
                    json.dumps(event.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            if cursor.rowcount:
                inserted += 1
            else:
                duplicate += 1
        connection.commit()
    finally:
        connection.close()
    return {
        "schema": "skill_lifecycle_state.quality_events.v1",
        "ok": True,
        "state_db": str(path),
        "inserted_count": inserted,
        "duplicate_count": duplicate,
    }


def quality_summary(path: Path = STATE_DB, *, limit: int = 5000) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": "skill_lifecycle_state.quality_summary.v1",
            "ok": True,
            "record_count": 0,
            "first_recorded_at": "",
            "last_recorded_at": "",
            "skills": {},
        }
    connection = connect(path)
    try:
        window = connection.execute(
            "SELECT MIN(occurred_at) AS first_recorded_at, MAX(occurred_at) AS last_recorded_at "
            "FROM skill_quality_event"
        ).fetchone()
        rows = connection.execute(
            "SELECT skill_id, skill_name, event_kind, outcome, validation, fallback "
            "FROM skill_quality_event ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 50000)),),
        ).fetchall()
    finally:
        connection.close()
    skills: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row["skill_name"])
        item = skills.setdefault(
            name,
            {
                "skill_id": str(row["skill_id"]),
                "events": 0,
                "selected": 0,
                "applied": 0,
                "completed": 0,
                "failed": 0,
                "partial": 0,
                "skipped": 0,
                "fallback": 0,
                "validated": 0,
            },
        )
        item["events"] += 1
        kind = str(row["event_kind"])
        if kind in item:
            item[kind] += 1
        if str(row["validation"]):
            item["validated"] += 1
        if str(row["fallback"]):
            item["fallback"] += 1
    for item in skills.values():
        completed = int(item["completed"])
        failed = int(item["failed"])
        decided = completed + failed
        item["success_rate"] = round(completed / decided, 3) if decided else None
        item["ranking_signal"] = max(-2, min(2, completed - failed))
    return {
        "schema": "skill_lifecycle_state.quality_summary.v1",
        "ok": True,
        "state_db": str(path),
        "record_count": len(rows),
        "first_recorded_at": str(window["first_recorded_at"] or ""),
        "last_recorded_at": str(window["last_recorded_at"] or ""),
        "skills": skills,
    }


def record_lineage(entry: dict[str, Any], path: Path = STATE_DB) -> dict[str, Any]:
    kind = str(entry.get("evolution_kind") or "").upper()
    if kind not in LINEAGE_KINDS:
        return {
            "schema": "skill_lifecycle_state.record_lineage.v1",
            "ok": False,
            "error": "invalid_evolution_kind",
            "allowed": sorted(LINEAGE_KINDS),
        }
    connection = connect(path)
    try:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO skill_lineage(
                lineage_key, recorded_at, evolution_kind, skill_id, skill_name,
                parent_version, child_version, source, reason,
                validation_evidence, metadata_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(entry["lineage_key"]),
                str(entry.get("recorded_at") or now_iso()),
                kind,
                str(entry.get("skill_id") or ""),
                str(entry.get("skill_name") or ""),
                str(entry.get("parent_version") or ""),
                str(entry.get("child_version") or ""),
                str(entry.get("source") or ""),
                str(entry.get("reason") or ""),
                str(entry.get("validation_evidence") or ""),
                json.dumps(entry.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        connection.commit()
        inserted = bool(cursor.rowcount)
    finally:
        connection.close()
    return {
        "schema": "skill_lifecycle_state.record_lineage.v1",
        "ok": True,
        "state_db": str(path),
        "inserted": inserted,
        "duplicate": not inserted,
        "evolution_kind": kind,
    }
