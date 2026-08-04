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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

try:
    from ..structured_task_envelope import resource_contract_from_metadata
except ImportError:
    from structured_task_envelope import resource_contract_from_metadata

from platform_paths import resource_library_root  # noqa: E402

RESOURCE_STORE_ROOT = Path(__file__).resolve().parents[1] / "resources"
RECORD_INDEX_PATH = (
    resource_library_root() / "文档" / "系统维护" / "索引" / "record_store.sqlite"
)


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
          ,acceptance_signature TEXT NOT NULL DEFAULT ''
          ,strategy_signature TEXT NOT NULL DEFAULT ''
          ,shadow_lane_count INTEGER NOT NULL DEFAULT 0
          ,duplicate_candidate_count INTEGER NOT NULL DEFAULT 0
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
        CREATE TABLE IF NOT EXISTS resource_transfers (
          transfer_id TEXT PRIMARY KEY,
          request_id TEXT NOT NULL,
          operation_id TEXT NOT NULL,
          execution_signature TEXT NOT NULL UNIQUE,
          state TEXT NOT NULL,
          generation INTEGER NOT NULL DEFAULT 0,
          lease_owner TEXT NOT NULL DEFAULT '',
          lease_expires_at TEXT NOT NULL DEFAULT '',
          plan_json TEXT NOT NULL DEFAULT '{}',
          receipt_json TEXT NOT NULL DEFAULT '{}',
          error_class TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_resource_transfers_ready ON resource_transfers(state, lease_expires_at, updated_at);
        CREATE TABLE IF NOT EXISTS resource_transfer_events (
          event_id INTEGER PRIMARY KEY AUTOINCREMENT,
          transfer_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          event_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_resource_transfer_events ON resource_transfer_events(transfer_id, event_id);
        """
    )
    existing_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(resource_requests)")
    }
    migrations = {
        "consumed": "INTEGER NOT NULL DEFAULT 0",
        "consumed_at": "TEXT NOT NULL DEFAULT ''",
        "consumer": "TEXT NOT NULL DEFAULT ''",
        "consumed_path": "TEXT NOT NULL DEFAULT ''",
        "no_read_needed_reason": "TEXT NOT NULL DEFAULT ''",
        "acceptance_signature": "TEXT NOT NULL DEFAULT ''",
        "strategy_signature": "TEXT NOT NULL DEFAULT ''",
        "shadow_lane_count": "INTEGER NOT NULL DEFAULT 0",
        "duplicate_candidate_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, declaration in migrations.items():
        if column not in existing_columns:
            conn.execute(
                f"ALTER TABLE resource_requests ADD COLUMN {column} {declaration}"
            )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resource_requests_consumption "
        "ON resource_requests(status, consumed, updated_at)"
    )


def _transfer_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _transfer_load(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _transfer_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    value["plan"] = _transfer_load(str(value.pop("plan_json") or "{}"))
    value["receipt"] = _transfer_load(str(value.pop("receipt_json") or "{}"))
    return value


def _transfer_event(
    conn: sqlite3.Connection, transfer_id: str, event_type: str, detail: dict[str, Any]
) -> None:
    conn.execute(
        "INSERT INTO resource_transfer_events(transfer_id,event_type,event_json,created_at) VALUES(?,?,?,?)",
        (
            transfer_id,
            event_type,
            json.dumps(detail, ensure_ascii=False, sort_keys=True),
            _transfer_now(),
        ),
    )


def submit_transfer(
    plan: dict[str, Any], *, db_path: Path = RECORD_INDEX_PATH
) -> dict[str, Any]:
    """Persist or reuse one transfer identity in the existing record store."""
    transfer_id = str(plan.get("transfer_id") or "")
    signature = str(plan.get("execution_signature") or "")
    if not transfer_id or not signature:
        return {"ok": False, "reason": "transfer_identity_required"}
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = _transfer_row(
            conn.execute(
                "SELECT * FROM resource_transfers WHERE execution_signature=?",
                (signature,),
            ).fetchone()
        )
        if existing:
            conn.execute("COMMIT")
            return {"ok": True, "reused": True, "transfer": existing}
        now = _transfer_now()
        conn.execute(
            """INSERT INTO resource_transfers(transfer_id,request_id,operation_id,execution_signature,state,generation,plan_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                transfer_id,
                str(plan.get("request_id") or ""),
                str(plan.get("operation_id") or ""),
                signature,
                "queued",
                0,
                json.dumps(plan, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        _transfer_event(
            conn, transfer_id, "submitted", {"execution_signature": signature}
        )
        transfer = _transfer_row(
            conn.execute(
                "SELECT * FROM resource_transfers WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
        )
        conn.execute("COMMIT")
        return {"ok": True, "reused": False, "transfer": transfer}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_transfer(
    transfer_id: str, *, db_path: Path = RECORD_INDEX_PATH
) -> dict[str, Any] | None:
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return _transfer_row(
            conn.execute(
                "SELECT * FROM resource_transfers WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
        )
    finally:
        conn.close()


def transition_transfer(
    transfer_id: str,
    *,
    expected: set[str],
    state: str,
    detail: dict[str, Any] | None = None,
    receipt: dict[str, Any] | None = None,
    error_class: str = "",
    expected_generation: int | None = None,
    db_path: Path = RECORD_INDEX_PATH,
) -> dict[str, Any]:
    from resource_transfer_contract import validate_transition

    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _transfer_row(
            conn.execute(
                "SELECT * FROM resource_transfers WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
        )
        if not current:
            conn.execute("COMMIT")
            return {"ok": False, "reason": "transfer_not_found"}
        if current["state"] not in expected:
            conn.execute("COMMIT")
            return {
                "ok": False,
                "reason": "transfer_state_conflict",
                "transfer": current,
            }
        if expected_generation is not None and int(current["generation"]) != int(
            expected_generation
        ):
            conn.execute("COMMIT")
            return {
                "ok": False,
                "reason": "transfer_generation_fenced",
                "transfer": current,
            }
        if not validate_transition(str(current["state"]), state):
            conn.execute("COMMIT")
            return {
                "ok": False,
                "reason": "transfer_transition_invalid",
                "transfer": current,
            }
        now = _transfer_now()
        keep_lease = state == "running"
        lease_owner = str(current.get("lease_owner") or "") if keep_lease else ""
        lease_expiry = str(current.get("lease_expires_at") or "") if keep_lease else ""
        conn.execute(
            "UPDATE resource_transfers SET state=?,generation=generation+1,receipt_json=?,error_class=?,lease_owner=?,lease_expires_at=?,updated_at=? WHERE transfer_id=?",
            (
                state,
                json.dumps(
                    receipt or current["receipt"], ensure_ascii=False, sort_keys=True
                ),
                error_class,
                lease_owner,
                lease_expiry,
                now,
                transfer_id,
            ),
        )
        _transfer_event(conn, transfer_id, state, detail or {})
        updated = _transfer_row(
            conn.execute(
                "SELECT * FROM resource_transfers WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
        )
        conn.execute("COMMIT")
        return {"ok": True, "transfer": updated}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def claim_transfer(
    transfer_id: str,
    *,
    lease_owner: str,
    lease_seconds: int = 300,
    db_path: Path = RECORD_INDEX_PATH,
) -> dict[str, Any]:
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _transfer_row(
            conn.execute(
                "SELECT * FROM resource_transfers WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
        )
        if not current or current["state"] != "queued":
            conn.execute("COMMIT")
            return {"ok": False, "reason": "transfer_not_ready", "transfer": current}
        expiry = (
            datetime.now(timezone.utc) + timedelta(seconds=max(1, int(lease_seconds)))
        ).isoformat()
        now = _transfer_now()
        conn.execute(
            "UPDATE resource_transfers SET state='leased',lease_owner=?,lease_expires_at=?,generation=generation+1,updated_at=? WHERE transfer_id=?",
            (lease_owner, expiry, now, transfer_id),
        )
        _transfer_event(
            conn,
            transfer_id,
            "leased",
            {"lease_owner": lease_owner, "lease_expires_at": expiry},
        )
        updated = _transfer_row(
            conn.execute(
                "SELECT * FROM resource_transfers WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
        )
        conn.execute("COMMIT")
        return {"ok": True, "transfer": updated}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def transfer_fence(
    transfer_id: str, *, generation: int, db_path: Path = RECORD_INDEX_PATH
) -> dict[str, Any]:
    current = get_transfer(transfer_id, db_path=db_path)
    if not current:
        return {"ok": False, "reason": "transfer_not_found"}
    active = current["state"] == "running" and int(current["generation"]) == int(
        generation
    )
    return {
        "ok": active,
        "active": active,
        "state": current["state"],
        "generation": current["generation"],
        "reason": "" if active else "transfer_generation_fenced",
    }


def convergence_candidates(
    *, db_path: Path = RECORD_INDEX_PATH, limit: int = 20
) -> list[dict[str, Any]]:
    """Return only transfer rows that need owner reconciliation."""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        now = _transfer_now()
        rows = conn.execute(
            """SELECT * FROM resource_transfers
               WHERE (state IN ('leased','running') AND lease_expires_at != '' AND lease_expires_at <= ?)
                  OR state = 'cancelling'
               ORDER BY updated_at,transfer_id LIMIT ?""",
            (now, max(1, min(int(limit), 100))),
        ).fetchall()
        return [_transfer_row(row) or {} for row in rows]
    finally:
        conn.close()


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
    event_id = hashlib.sha256(
        json.dumps(stable, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "event_id": event_id,
        **stable,
        "owner_tool": str(
            owner_execution.get("owner_tool") or event.get("owner_tool") or ""
        ),
        "route_mode": str(
            plan.get("route_mode")
            or gateway.get("route_mode")
            or event.get("route_mode")
            or ""
        ),
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
    attempts = (
        receipt.get("attempts") if isinstance(receipt.get("attempts"), list) else []
    )
    events = (
        receipt.get("progress_events")
        if isinstance(receipt.get("progress_events"), list)
        else []
    )
    consumption = _dict(receipt.get("consumption"))
    acceptance_contract = _dict(receipt.get("acceptance_contract"))
    strategy_shadow = _dict(receipt.get("strategy_shadow"))
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
        "intent": str(
            route.get("intent") or request.get("intent") or contract.get("intent") or ""
        ),
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
        "acceptance_signature": str(acceptance_contract.get("signature") or ""),
        "strategy_signature": str(strategy_shadow.get("signature") or ""),
        "shadow_lane_count": int(strategy_shadow.get("lane_count") or 0),
        "duplicate_candidate_count": int(
            strategy_shadow.get("duplicate_candidate_count") or 0
        ),
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
        row = request_row(
            request_id=request_id,
            request=request,
            receipt=receipt,
            manifest_path=manifest_path,
        )
        conn = _connect(db_path)
        with conn:
            conn.execute(
                """INSERT OR REPLACE INTO resource_requests
                (request_id,updated_at,status,ok,intent,resource_kind,primary_tool,owner_tool,route_mode,
                 attempt_count,error_class,next_action,satisfied,satisfaction_reason,result_kind,artifact_path,manifest_path,
                 consumed,consumed_at,consumer,consumed_path,no_read_needed_reason,
                 acceptance_signature,strategy_signature,shadow_lane_count,duplicate_candidate_count)
                VALUES (:request_id,:updated_at,:status,:ok,:intent,:resource_kind,:primary_tool,:owner_tool,:route_mode,
                 :attempt_count,:error_class,:next_action,:satisfied,:satisfaction_reason,:result_kind,:artifact_path,:manifest_path,
                 :consumed,:consumed_at,:consumer,:consumed_path,:no_read_needed_reason,
                 :acceptance_signature,:strategy_signature,:shadow_lane_count,:duplicate_candidate_count)""",
                row,
            )
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def rebuild_from_manifests(
    conn: sqlite3.Connection, *, store_root: Path = RESOURCE_STORE_ROOT
) -> dict[str, int]:
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
        row = request_row(
            request_id=request_id,
            request=request,
            receipt=receipt,
            manifest_path=str(manifest_path),
        )
        conn.execute(
            """INSERT OR REPLACE INTO resource_requests
            (request_id,updated_at,status,ok,intent,resource_kind,primary_tool,owner_tool,route_mode,
             attempt_count,error_class,next_action,satisfied,satisfaction_reason,result_kind,artifact_path,manifest_path,
             consumed,consumed_at,consumer,consumed_path,no_read_needed_reason,
             acceptance_signature,strategy_signature,shadow_lane_count,duplicate_candidate_count)
            VALUES (:request_id,:updated_at,:status,:ok,:intent,:resource_kind,:primary_tool,:owner_tool,:route_mode,
             :attempt_count,:error_class,:next_action,:satisfied,:satisfaction_reason,:result_kind,:artifact_path,:manifest_path,
             :consumed,:consumed_at,:consumer,:consumed_path,:no_read_needed_reason,
             :acceptance_signature,:strategy_signature,:shadow_lane_count,:duplicate_candidate_count)""",
            row,
        )
        requests += 1
        manifest_events = (
            manifest.get("events") if isinstance(manifest.get("events"), list) else []
        )
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


def strategy_entries(
    *, limit: int = 200, db_path: Path = RECORD_INDEX_PATH
) -> list[dict[str, Any]]:
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
                "acceptance_signature": row["acceptance_signature"],
                "strategy_signature": row["strategy_signature"],
                "shadow_lane_count": row["shadow_lane_count"],
                "duplicate_candidate_count": row["duplicate_candidate_count"],
            },
        }
        for row in rows
    ]
