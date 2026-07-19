#!/usr/bin/env python3
"""SQLite observability projection for resource requests.

Ownership:
  Stores queryable request/event projections in the existing record-store
  SQLite database. Resource manifests remain the business source of truth.

Non-goals:
  This is not a queue, scheduler, retry engine, or replacement for manifests.

State behavior:
  Best-effort incremental upserts plus a deterministic rebuild from manifests.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    from ..structured_task_envelope import resource_contract_from_metadata
except ImportError:
    bridge_root = Path(__file__).resolve().parents[1]
    if str(bridge_root) not in sys.path:
        sys.path.insert(0, str(bridge_root))
    from structured_task_envelope import resource_contract_from_metadata


RESOURCE_STORE_ROOT = Path(__file__).resolve().parents[1] / "resources"
RECORD_INDEX_PATH = Path(r"C:\Users\45543\Desktop\Codex资源库\文档\系统维护\索引\record_store.sqlite")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS resource_requests (
          request_id TEXT PRIMARY KEY,
          updated_at TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT '',
          ok INTEGER NOT NULL DEFAULT 0,
          intent TEXT NOT NULL DEFAULT '',
          resource_kind TEXT NOT NULL DEFAULT '',
          primary_tool TEXT NOT NULL DEFAULT '',
          owner_tool TEXT NOT NULL DEFAULT '',
          route_mode TEXT NOT NULL DEFAULT '',
          attempt_count INTEGER NOT NULL DEFAULT 0,
          error_class TEXT NOT NULL DEFAULT '',
          next_action TEXT NOT NULL DEFAULT '',
          satisfied INTEGER NOT NULL DEFAULT 0,
          satisfaction_reason TEXT NOT NULL DEFAULT '',
          result_kind TEXT NOT NULL DEFAULT '',
          artifact_path TEXT NOT NULL DEFAULT '',
          manifest_path TEXT NOT NULL DEFAULT '',
          consumed INTEGER NOT NULL DEFAULT 0,
          consumed_at TEXT NOT NULL DEFAULT '',
          consumer TEXT NOT NULL DEFAULT '',
          consumed_path TEXT NOT NULL DEFAULT '',
          no_read_needed_reason TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS resource_events (
          event_id TEXT PRIMARY KEY,
          request_id TEXT NOT NULL,
          event_time TEXT NOT NULL DEFAULT '',
          stage TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT '',
          tool TEXT NOT NULL DEFAULT '',
          owner_tool TEXT NOT NULL DEFAULT '',
          route_mode TEXT NOT NULL DEFAULT '',
          error_class TEXT NOT NULL DEFAULT '',
          message TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_resource_requests_status ON resource_requests(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_resource_requests_intent ON resource_requests(intent, updated_at);
        CREATE INDEX IF NOT EXISTS idx_resource_events_request ON resource_events(request_id, event_time);
        CREATE INDEX IF NOT EXISTS idx_resource_events_stage ON resource_events(stage, status, event_time);
        """
    )
    existing_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(resource_requests)")}
    migrations = {
        "consumed": "INTEGER NOT NULL DEFAULT 0",
        "consumed_at": "TEXT NOT NULL DEFAULT ''",
        "consumer": "TEXT NOT NULL DEFAULT ''",
        "consumed_path": "TEXT NOT NULL DEFAULT ''",
        "no_read_needed_reason": "TEXT NOT NULL DEFAULT ''",
    }
    for column, declaration in migrations.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE resource_requests ADD COLUMN {column} {declaration}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_requests_consumption "
        "ON resource_requests(status, consumed, updated_at)"
    )


def event_row(event: dict[str, Any]) -> dict[str, Any]:
    gateway = _dict(event.get("network_gateway_plan"))
    plan = _dict(gateway.get("plan"))
    owner_execution = _dict(event.get("owner_execution"))
    stable = {
        "request_id": str(event.get("request_id") or ""),
        "event_time": str(event.get("time") or ""),
        "stage": str(event.get("stage") or ""),
        "status": str(event.get("status") or ""),
        "tool": str(event.get("tool") or ""),
        "message": str(event.get("message") or ""),
    }
    event_id = hashlib.sha256(json.dumps(stable, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "event_id": event_id,
        **stable,
        "owner_tool": str(owner_execution.get("owner_tool") or event.get("owner_tool") or ""),
        "route_mode": str(plan.get("route_mode") or gateway.get("route_mode") or event.get("route_mode") or ""),
        "error_class": str(event.get("error_class") or ""),
    }


def request_row(
    *,
    request_id: str,
    request: dict[str, Any],
    receipt: dict[str, Any],
    manifest_path: str,
) -> dict[str, Any]:
    route = _dict(receipt.get("route"))
    network = _dict(receipt.get("network_summary"))
    owner = _dict(receipt.get("owner_execution"))
    satisfaction = _dict(receipt.get("satisfaction"))
    metadata = _dict(request.get("metadata"))
    contract = resource_contract_from_metadata(metadata)
    resource = _dict(contract.get("resource"))
    attempts = receipt.get("attempts") if isinstance(receipt.get("attempts"), list) else []
    events = receipt.get("progress_events") if isinstance(receipt.get("progress_events"), list) else []
    consumption = _dict(receipt.get("consumption"))
    owner_tool = str(owner.get("owner_tool") or "")
    if not owner_tool:
        for require_success in (True, False):
            for attempt in reversed(attempts):
                attempt_data = _dict(attempt)
                result = _dict(attempt_data.get("result"))
                if require_success and not result.get("ok"):
                    continue
                owner_tool = str(attempt_data.get("tool") or result.get("source") or "")
                if owner_tool:
                    break
            if owner_tool:
                break
    route_mode = str(network.get("route_mode") or network.get("preferred_route") or "")
    if not route_mode:
        for attempt in reversed(attempts):
            attempt_data = _dict(attempt)
            result = _dict(attempt_data.get("result"))
            result_metadata = _dict(result.get("metadata"))
            attempt_network = _dict(attempt_data.get("network_summary"))
            route_mode = str(
                result_metadata.get("network_route_mode")
                or attempt_network.get("route_mode")
                or attempt_network.get("preferred_route")
                or ""
            )
            if route_mode:
                break
            tool = str(attempt_data.get("tool") or result.get("source") or "")
            if attempt_data.get("executable") and tool in {
                "local_file",
                "local_parser",
                "resource_cli",
                "resource_source_strategy",
            }:
                route_mode = "local_execution"
                break
    updated_at = ""
    if events:
        updated_at = str(_dict(events[-1]).get("time") or "")
    if consumption.get("consumed_at"):
        updated_at = str(consumption.get("consumed_at"))
    return {
        "request_id": request_id,
        "updated_at": updated_at,
        "status": str(receipt.get("status") or ""),
        "ok": 1 if receipt.get("ok") else 0,
        "intent": str(route.get("intent") or request.get("intent") or contract.get("intent") or ""),
        "resource_kind": str(
            resource.get("kind")
            or metadata.get("resource_kind_hint")
            or metadata.get("resource_kind")
            or ""
        ),
        "primary_tool": str(route.get("primary_tool") or ""),
        "owner_tool": owner_tool,
        "route_mode": route_mode,
        "attempt_count": len(attempts),
        "error_class": str(receipt.get("error_class") or ""),
        "next_action": str(receipt.get("next_action") or ""),
        "satisfied": 1 if satisfaction.get("satisfied") else 0,
        "satisfaction_reason": str(satisfaction.get("reason") or ""),
        "result_kind": str(receipt.get("result_kind") or ""),
        "artifact_path": str(receipt.get("artifact_path") or ""),
        "manifest_path": manifest_path,
        "consumed": 1 if consumption.get("satisfied") else 0,
        "consumed_at": str(consumption.get("consumed_at") or ""),
        "consumer": str(consumption.get("consumer") or ""),
        "consumed_path": str(consumption.get("consumed_path") or ""),
        "no_read_needed_reason": str(consumption.get("no_read_needed_reason") or ""),
    }


def _connect(path: Path = RECORD_INDEX_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    ensure_schema(conn)
    return conn


def record_event(event: dict[str, Any], *, db_path: Path = RECORD_INDEX_PATH) -> bool:
    conn: sqlite3.Connection | None = None
    try:
        row = event_row(event)
        conn = _connect(db_path)
        with conn:
            conn.execute(
                """INSERT OR REPLACE INTO resource_events
                (event_id,request_id,event_time,stage,status,tool,owner_tool,route_mode,error_class,message)
                VALUES (:event_id,:request_id,:event_time,:stage,:status,:tool,:owner_tool,:route_mode,:error_class,:message)""",
                row,
            )
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def upsert_request(
    *,
    request_id: str,
    request: dict[str, Any],
    receipt: dict[str, Any],
    manifest_path: str,
    db_path: Path = RECORD_INDEX_PATH,
) -> bool:
    conn: sqlite3.Connection | None = None
    try:
        row = request_row(request_id=request_id, request=request, receipt=receipt, manifest_path=manifest_path)
        conn = _connect(db_path)
        with conn:
            conn.execute(
                """INSERT OR REPLACE INTO resource_requests
                (request_id,updated_at,status,ok,intent,resource_kind,primary_tool,owner_tool,route_mode,
                 attempt_count,error_class,next_action,satisfied,satisfaction_reason,result_kind,artifact_path,manifest_path,
                 consumed,consumed_at,consumer,consumed_path,no_read_needed_reason)
                VALUES (:request_id,:updated_at,:status,:ok,:intent,:resource_kind,:primary_tool,:owner_tool,:route_mode,
                 :attempt_count,:error_class,:next_action,:satisfied,:satisfaction_reason,:result_kind,:artifact_path,:manifest_path,
                 :consumed,:consumed_at,:consumer,:consumed_path,:no_read_needed_reason)""",
                row,
            )
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def rebuild_from_manifests(conn: sqlite3.Connection, *, store_root: Path = RESOURCE_STORE_ROOT) -> dict[str, int]:
    ensure_schema(conn)
    conn.execute("DELETE FROM resource_events")
    conn.execute("DELETE FROM resource_requests")
    requests = 0
    events = 0
    for manifest_path in sorted((store_root / "_requests").glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        request_id = str(manifest.get("request_id") or "")
        request = _dict(manifest.get("request"))
        receipt = _dict(manifest.get("receipt"))
        if not request_id:
            continue
        row = request_row(request_id=request_id, request=request, receipt=receipt, manifest_path=str(manifest_path))
        conn.execute(
            """INSERT OR REPLACE INTO resource_requests
            (request_id,updated_at,status,ok,intent,resource_kind,primary_tool,owner_tool,route_mode,
             attempt_count,error_class,next_action,satisfied,satisfaction_reason,result_kind,artifact_path,manifest_path,
             consumed,consumed_at,consumer,consumed_path,no_read_needed_reason)
            VALUES (:request_id,:updated_at,:status,:ok,:intent,:resource_kind,:primary_tool,:owner_tool,:route_mode,
             :attempt_count,:error_class,:next_action,:satisfied,:satisfaction_reason,:result_kind,:artifact_path,:manifest_path,
             :consumed,:consumed_at,:consumer,:consumed_path,:no_read_needed_reason)""",
            row,
        )
        requests += 1
        manifest_events = manifest.get("events") if isinstance(manifest.get("events"), list) else []
        for item in manifest_events:
            if not isinstance(item, dict):
                continue
            event_data = event_row(item)
            conn.execute(
                """INSERT OR REPLACE INTO resource_events
                (event_id,request_id,event_time,stage,status,tool,owner_tool,route_mode,error_class,message)
                VALUES (:event_id,:request_id,:event_time,:stage,:status,:tool,:owner_tool,:route_mode,:error_class,:message)""",
                event_data,
            )
            events += 1
    return {"requests": requests, "events": events}


def strategy_entries(*, limit: int = 200, db_path: Path = RECORD_INDEX_PATH) -> list[dict[str, Any]]:
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM resource_requests ORDER BY updated_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()
    return [
        {
            "ok": bool(row["ok"]),
            "intent": row["intent"],
            "resource_kind": row["resource_kind"] or "unknown",
            "decision": row["status"] or "none",
            "error": row["error_class"],
            "stored_path": row["artifact_path"],
            "metadata": {
                "stage": "terminal_receipt",
                "intent": row["intent"],
                "resource_kind": row["resource_kind"],
                "primary_tool": row["primary_tool"],
                "owner_tool": row["owner_tool"],
                "route_mode": row["route_mode"],
                "next_action": row["next_action"],
                "satisfaction_reason": row["satisfaction_reason"],
            },
        }
        for row in rows
    ]
