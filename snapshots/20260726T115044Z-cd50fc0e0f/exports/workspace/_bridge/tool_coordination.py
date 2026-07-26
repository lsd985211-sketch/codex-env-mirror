#!/usr/bin/env python3
"""Coordination contract for slash-command templates and scratch SQLite.

This layer is intentionally small. It records normalized work packages and
evidence in the scratch database, but it does not become the source of truth
for mail, scheduler, bridge, memory, or maintenance subsystems.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRATCH_DB = ROOT / "_bridge" / "data" / "sqlite" / "codex_scratch.sqlite"
SLASH_REGISTRY = ROOT / "_bridge" / "slash_commands" / "commands.json"
POLICY_PATH = ROOT / "_bridge" / "filesystem_mcp_policy.json"

SCHEMA_VERSION = 1

DDL = [
    """
    CREATE TABLE IF NOT EXISTS coordination_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_key TEXT UNIQUE NOT NULL,
        source TEXT NOT NULL,
        target_module TEXT NOT NULL,
        intent TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        payload_json TEXT NOT NULL DEFAULT '{}',
        evidence_ref TEXT NOT NULL DEFAULT '',
        next_action TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS coordination_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_key TEXT NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'info',
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS coordination_artifacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_key TEXT NOT NULL,
        artifact_type TEXT NOT NULL,
        path_or_ref TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS coordination_kv (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_coordination_tasks_status ON coordination_tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_coordination_tasks_target ON coordination_tasks(target_module)",
    "CREATE INDEX IF NOT EXISTS idx_coordination_events_task ON coordination_events(task_key)",
    "CREATE INDEX IF NOT EXISTS idx_coordination_artifacts_task ON coordination_artifacts(task_key)",
]

REQUIRED_TABLES = {
    "coordination_tasks",
    "coordination_events",
    "coordination_artifacts",
    "coordination_kv",
}

VALID_STATUSES = {
    "draft",
    "ready",
    "running",
    "blocked",
    "done",
    "failed",
    "archived",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    SCRATCH_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(SCRATCH_DB), timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=3000")
    return db


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def table_names(db: sqlite3.Connection) -> set[str]:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row["name"]) for row in rows}


def count_table(db: sqlite3.Connection, table: str) -> int:
    return int(db.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"])


def init_schema() -> dict[str, Any]:
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        for ddl in DDL:
            db.execute(ddl)
        for index_sql in INDEXES:
            db.execute(index_sql)
        db.execute(
            """
            INSERT INTO coordination_kv(key, value_json, updated_at)
            VALUES('schema_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
            """,
            (json.dumps({"version": SCHEMA_VERSION}, ensure_ascii=False), now_iso()),
        )
        db.commit()
    return {"ok": True, "schema": "tool_coordination.init_schema.v1", "generated_at": now_iso()}


def parse_json_object(value: str, field_name: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return payload


def record_task(
    task_key: str,
    source: str,
    target_module: str,
    intent: str,
    status: str,
    payload: dict[str, Any],
    evidence_ref: str,
    next_action: str,
) -> dict[str, Any]:
    if not task_key.strip():
        return {"ok": False, "reason": "task_key_required"}
    if status not in VALID_STATUSES:
        return {"ok": False, "reason": "invalid_status", "status": status, "valid_statuses": sorted(VALID_STATUSES)}
    init_schema()
    now = now_iso()
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO coordination_tasks(
                task_key, source, target_module, intent, status,
                payload_json, evidence_ref, next_action, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_key) DO UPDATE SET
                source=excluded.source,
                target_module=excluded.target_module,
                intent=excluded.intent,
                status=excluded.status,
                payload_json=excluded.payload_json,
                evidence_ref=excluded.evidence_ref,
                next_action=excluded.next_action,
                updated_at=excluded.updated_at
            """,
            (
                task_key.strip(),
                source.strip() or "codex",
                target_module.strip() or "general",
                intent.strip(),
                status,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                evidence_ref.strip(),
                next_action.strip(),
                now,
                now,
            ),
        )
        db.commit()
    return {
        "ok": True,
        "schema": "tool_coordination.record_task.v1",
        "task_key": task_key.strip(),
        "status": status,
        "rowcount": cursor.rowcount,
        "generated_at": now_iso(),
    }


def record_event(task_key: str, event_type: str, severity: str, detail: dict[str, Any]) -> dict[str, Any]:
    if not task_key.strip():
        return {"ok": False, "reason": "task_key_required"}
    if not event_type.strip():
        return {"ok": False, "reason": "event_type_required"}
    if severity not in {"debug", "info", "advisory", "risk", "blocker", "error"}:
        return {"ok": False, "reason": "invalid_severity", "severity": severity}
    init_schema()
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO coordination_events(task_key, event_type, severity, detail_json, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                task_key.strip(),
                event_type.strip(),
                severity,
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
                now_iso(),
            ),
        )
        db.commit()
    return {
        "ok": True,
        "schema": "tool_coordination.record_event.v1",
        "task_key": task_key.strip(),
        "event_type": event_type.strip(),
        "rowcount": cursor.rowcount,
        "lastrowid": cursor.lastrowid,
        "generated_at": now_iso(),
    }


def record_artifact(task_key: str, artifact_type: str, path_or_ref: str, summary: str) -> dict[str, Any]:
    if not task_key.strip():
        return {"ok": False, "reason": "task_key_required"}
    if not artifact_type.strip():
        return {"ok": False, "reason": "artifact_type_required"}
    if not path_or_ref.strip():
        return {"ok": False, "reason": "path_or_ref_required"}
    init_schema()
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO coordination_artifacts(task_key, artifact_type, path_or_ref, summary, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (task_key.strip(), artifact_type.strip(), path_or_ref.strip(), summary.strip(), now_iso()),
        )
        db.commit()
    return {
        "ok": True,
        "schema": "tool_coordination.record_artifact.v1",
        "task_key": task_key.strip(),
        "artifact_type": artifact_type.strip(),
        "rowcount": cursor.rowcount,
        "lastrowid": cursor.lastrowid,
        "generated_at": now_iso(),
    }


def slash_registry_summary() -> dict[str, Any]:
    if not SLASH_REGISTRY.exists():
        return {"exists": False, "command_count": 0, "commands": []}
    try:
        payload = json.loads(SLASH_REGISTRY.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"exists": True, "ok": False, "error": f"{type(exc).__name__}: {exc}", "command_count": 0}
    commands = payload.get("commands") if isinstance(payload, dict) else []
    items = commands if isinstance(commands, list) else []
    return {
        "exists": True,
        "ok": True,
        "schema": payload.get("schema") if isinstance(payload, dict) else "",
        "command_count": len([item for item in items if isinstance(item, dict)]),
        "commands": [
            {
                "name": item.get("name"),
                "category": item.get("category", ""),
                "target_module": item.get("target_module", ""),
                "variables": item.get("variables", []),
            }
            for item in items
            if isinstance(item, dict)
        ],
    }


def policy_summary() -> dict[str, Any]:
    if not POLICY_PATH.exists():
        return {"exists": False}
    try:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"exists": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    return {
        "exists": True,
        "ok": True,
        "sqlite_scratch_default": bool((profiles.get("sqlite-scratch") or {}).get("default_for_sqlite")),
        "sqlite_bridge_ro_default": bool((profiles.get("sqlite-bridge-ro") or {}).get("default_for_sqlite")),
    }


def snapshot() -> dict[str, Any]:
    db_exists = SCRATCH_DB.exists()
    tables: list[str] = []
    counts: dict[str, int] = {}
    schema_version: dict[str, Any] | None = None
    if db_exists:
        with connect() as db:
            table_set = table_names(db)
            tables = sorted(table_set)
            for table in sorted(REQUIRED_TABLES & table_set):
                counts[table] = count_table(db, table)
            if "coordination_kv" in table_set:
                row = db.execute("SELECT value_json FROM coordination_kv WHERE key='schema_version'").fetchone()
                if row:
                    try:
                        schema_version = json.loads(str(row["value_json"]))
                    except json.JSONDecodeError:
                        schema_version = {"raw": str(row["value_json"])}
    return {
        "schema": "tool_coordination.snapshot.v1",
        "generated_at": now_iso(),
        "workspace": str(ROOT),
        "scratch_db": {
            "path": str(SCRATCH_DB),
            "exists": db_exists,
            "tables": tables,
            "counts": counts,
            "schema_version": schema_version,
        },
        "slash_registry": slash_registry_summary(),
        "policy": policy_summary(),
        "contract": {
            "slash_executes_shell": False,
            "scratch_is_authoritative_production_state": False,
            "bridge_db_writable_by_default": False,
        },
    }


def doctor() -> dict[str, Any]:
    snap = snapshot()
    issues: list[dict[str, str]] = []
    scratch = snap["scratch_db"]
    if not scratch["exists"]:
        issues.append({"severity": "risk", "code": "scratch_db_missing", "message": "Scratch SQLite database does not exist."})
    missing = sorted(REQUIRED_TABLES - set(scratch.get("tables") or []))
    if missing:
        issues.append({"severity": "advisory", "code": "coordination_schema_missing", "message": f"Missing coordination tables: {', '.join(missing)}"})
    if not snap["slash_registry"].get("ok", False):
        issues.append({"severity": "risk", "code": "slash_registry_invalid", "message": "Slash command registry is missing or invalid."})
    if not snap["policy"].get("sqlite_scratch_default", False):
        issues.append({"severity": "risk", "code": "sqlite_scratch_not_default", "message": "sqlite-scratch is not marked as the default SQLite work profile."})
    if snap["policy"].get("sqlite_bridge_ro_default", False):
        issues.append({"severity": "blocker", "code": "bridge_db_marked_default", "message": "Bridge DB profile must not be the default SQLite write target."})
    severities = {issue["severity"] for issue in issues}
    status = "unhealthy" if "blocker" in severities else ("degraded" if "risk" in severities else "ok")
    return {
        "schema": "tool_coordination.doctor.v1",
        "generated_at": now_iso(),
        "status": status,
        "issues": issues,
        "summary": {
            "scratch_tables": len(scratch.get("tables") or []),
            "slash_commands": snap["slash_registry"].get("command_count", 0),
            "coordination_tables_ready": not missing,
        },
    }


def repair_plan() -> dict[str, Any]:
    doc = doctor()
    codes = {issue["code"] for issue in doc["issues"]}
    actions: list[dict[str, Any]] = []
    if "scratch_db_missing" in codes or "coordination_schema_missing" in codes:
        actions.append(
            {
                "id": "init_coordination_scratch_schema",
                "mode": "approved_safe_apply",
                "mutates": True,
                "target": str(SCRATCH_DB),
                "command": "python _bridge\\tool_coordination.py init-schema",
                "guardrails": ["scratch_database_only", "does_not_touch_production_databases"],
            }
        )
    if "slash_registry_invalid" in codes:
        actions.append(
            {
                "id": "repair_slash_registry",
                "mode": "manual_or_approved",
                "mutates": True,
                "target": str(SLASH_REGISTRY),
                "guardrails": ["backup_before_edit", "no_execution_fields"],
            }
        )
    return {
        "schema": "tool_coordination.repair_plan.v1",
        "generated_at": now_iso(),
        "dry_run": True,
        "actions": actions,
        "doctor_issues": doc["issues"],
    }


def validate() -> dict[str, Any]:
    snap = snapshot()
    tables = set(snap["scratch_db"].get("tables") or [])
    checks = [
        {
            "name": "scratch_db_exists",
            "ok": bool(snap["scratch_db"]["exists"]),
            "detail": str(SCRATCH_DB),
        },
        {
            "name": "coordination_tables_exist",
            "ok": REQUIRED_TABLES.issubset(tables),
            "detail": sorted(REQUIRED_TABLES - tables),
        },
        {
            "name": "slash_registry_ok",
            "ok": bool(snap["slash_registry"].get("ok", False)),
            "detail": snap["slash_registry"].get("command_count", 0),
        },
        {
            "name": "sqlite_default_boundary",
            "ok": bool(snap["policy"].get("sqlite_scratch_default")) and not bool(snap["policy"].get("sqlite_bridge_ro_default")),
            "detail": snap["policy"],
        },
    ]
    failed = [check for check in checks if not check["ok"]]
    return {"schema": "tool_coordination.validate.v1", "generated_at": now_iso(), "ok": not failed, "checks": checks}


def metrics() -> dict[str, Any]:
    snap = snapshot()
    counts = snap["scratch_db"].get("counts") or {}
    return {
        "schema": "tool_coordination.metrics.v1",
        "generated_at": now_iso(),
        "scratch_db_exists": snap["scratch_db"]["exists"],
        "coordination_tasks": counts.get("coordination_tasks", 0),
        "coordination_events": counts.get("coordination_events", 0),
        "coordination_artifacts": counts.get("coordination_artifacts", 0),
        "slash_command_count": snap["slash_registry"].get("command_count", 0),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Slash/SQLite tool coordination maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "doctor", "repair-plan", "validate", "metrics", "init-schema"):
        subparsers.add_parser(command)
    task_parser = subparsers.add_parser("record-task")
    task_parser.add_argument("--task-key", required=True)
    task_parser.add_argument("--source", default="codex")
    task_parser.add_argument("--target-module", default="general")
    task_parser.add_argument("--intent", required=True)
    task_parser.add_argument("--status", default="ready")
    task_parser.add_argument("--payload-json", default="{}")
    task_parser.add_argument("--evidence-ref", default="")
    task_parser.add_argument("--next-action", default="")
    event_parser = subparsers.add_parser("record-event")
    event_parser.add_argument("--task-key", required=True)
    event_parser.add_argument("--event-type", required=True)
    event_parser.add_argument("--severity", default="info")
    event_parser.add_argument("--detail-json", default="{}")
    artifact_parser = subparsers.add_parser("record-artifact")
    artifact_parser.add_argument("--task-key", required=True)
    artifact_parser.add_argument("--artifact-type", required=True)
    artifact_parser.add_argument("--path-or-ref", required=True)
    artifact_parser.add_argument("--summary", default="")
    parser.add_argument("--json", action="store_true", help="Emit JSON; currently always true")
    args = parser.parse_args(argv)

    try:
        if args.command == "snapshot":
            payload = snapshot()
        elif args.command == "doctor":
            payload = doctor()
        elif args.command == "repair-plan":
            payload = repair_plan()
        elif args.command == "validate":
            payload = validate()
        elif args.command == "metrics":
            payload = metrics()
        elif args.command == "init-schema":
            payload = init_schema()
        elif args.command == "record-task":
            payload = record_task(
                args.task_key,
                args.source,
                args.target_module,
                args.intent,
                args.status,
                parse_json_object(args.payload_json, "payload-json"),
                args.evidence_ref,
                args.next_action,
            )
        elif args.command == "record-event":
            payload = record_event(
                args.task_key,
                args.event_type,
                args.severity,
                parse_json_object(args.detail_json, "detail-json"),
            )
        elif args.command == "record-artifact":
            payload = record_artifact(args.task_key, args.artifact_type, args.path_or_ref, args.summary)
        else:  # pragma: no cover
            parser.error(f"unsupported command: {args.command}")
    except ValueError as exc:
        payload = {"ok": False, "reason": str(exc), "generated_at": now_iso()}

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
