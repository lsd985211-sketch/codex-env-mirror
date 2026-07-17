#!/usr/bin/env python3
"""Sidecar durable lifecycle state for structured automation tasks.

Ownership: task identity, idempotency, leases, approvals, attempts, receipts,
and recovery classification.
Non-goals: execute owner commands, send mail, create Codex threads, replace
the unified scheduler, or own another module's business state.
State behavior: isolated SQLite orchestration state; it is never auto-started.
Caller context: a future explicit dispatch bridge may use this lifecycle.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "runtime" / "persistent_task_kernel" / "tasks.sqlite"
READY_STATES = {"queued", "retry_wait"}
ACTIVE_STATES = {"leased", "acked", "executing"}
TERMINAL_STATES = {"succeeded", "dead_letter", "rejected"}
ALL_STATES = READY_STATES | ACTIVE_STATES | TERMINAL_STATES | {"waiting_approval", "recovery_required"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
          task_id TEXT PRIMARY KEY,
          idempotency_key TEXT NOT NULL UNIQUE,
          task_type TEXT NOT NULL,
          target_module TEXT NOT NULL,
          action_type TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          acceptance_json TEXT NOT NULL,
          state TEXT NOT NULL,
          approval_state TEXT NOT NULL,
          requires_approval INTEGER NOT NULL DEFAULT 0,
          priority INTEGER NOT NULL DEFAULT 0,
          attempt_count INTEGER NOT NULL DEFAULT 0,
          max_attempts INTEGER NOT NULL DEFAULT 3,
          retry_delay_seconds INTEGER NOT NULL DEFAULT 60,
          next_attempt_at TEXT NOT NULL,
          lease_owner TEXT NOT NULL DEFAULT '',
          lease_expires_at TEXT NOT NULL DEFAULT '',
          acked_at TEXT NOT NULL DEFAULT '',
          result_json TEXT NOT NULL DEFAULT '{}',
          last_error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_ready ON tasks(state, next_attempt_at, priority DESC, created_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_lease ON tasks(state, lease_expires_at);
        CREATE TABLE IF NOT EXISTS task_events (
          event_id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          event_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, event_id);
        """
    )
    return conn


def event(conn: sqlite3.Connection, task_id: str, kind: str, detail: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO task_events(task_id,event_type,event_json,created_at) VALUES(?,?,?,?)",
        (task_id, kind, dump(detail), now_iso()),
    )


def to_task(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    value["payload"] = load(value.pop("payload_json"), {})
    value["acceptance"] = load(value.pop("acceptance_json"), {})
    value["result"] = load(value.pop("result_json"), {})
    value["requires_approval"] = bool(value["requires_approval"])
    return value


def fetch(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    return to_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())


def get(task_id: str, *, db_path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    conn = connect(db_path)
    try:
        return fetch(conn, task_id)
    finally:
        conn.close()


def enqueue(
    *,
    task_id: str,
    idempotency_key: str,
    task_type: str,
    target_module: str,
    action_type: str,
    payload: dict[str, Any],
    acceptance: dict[str, Any] | None = None,
    requires_approval: bool = False,
    priority: int = 0,
    max_attempts: int = 3,
    retry_delay_seconds: int = 60,
    db_path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    if not task_id or not idempotency_key:
        return {"ok": False, "reason": "task_id_and_idempotency_key_required"}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "payload_must_be_object"}
    if max_attempts < 1:
        return {"ok": False, "reason": "max_attempts_must_be_positive"}
    now = now_iso()
    state = "waiting_approval" if requires_approval else "queued"
    approval_state = "pending" if requires_approval else "not_required"
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM tasks WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if existing is not None:
            task = to_task(existing) or {}
            same = (
                task.get("task_type") == task_type
                and task.get("target_module") == target_module
                and task.get("action_type") == action_type
                and task.get("payload") == payload
            )
            conn.execute("COMMIT")
            return {
                "ok": same,
                "duplicate": True,
                "reason": "idempotency_replay" if same else "idempotency_conflict",
                "task": task,
            }
        conn.execute(
            """INSERT INTO tasks(
                 task_id,idempotency_key,task_type,target_module,action_type,payload_json,acceptance_json,
                 state,approval_state,requires_approval,priority,max_attempts,retry_delay_seconds,
                 next_attempt_at,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                idempotency_key,
                task_type,
                target_module,
                action_type,
                dump(payload),
                dump(acceptance or {}),
                state,
                approval_state,
                int(requires_approval),
                int(priority),
                int(max_attempts),
                max(1, int(retry_delay_seconds)),
                now,
                now,
                now,
            ),
        )
        event(conn, task_id, "enqueued", {"state": state, "target_module": target_module})
        task = fetch(conn, task_id)
        conn.execute("COMMIT")
        return {"ok": True, "duplicate": False, "task": task}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def claim(*, lease_owner: str, lease_seconds: int = 300, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    if not lease_owner:
        return {"ok": False, "reason": "lease_owner_required"}
    current = datetime.now(timezone.utc)
    now = current.isoformat()
    expires = (current + timedelta(seconds=max(1, int(lease_seconds)))).isoformat()
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT * FROM tasks
               WHERE state IN ('queued','retry_wait') AND next_attempt_at <= ?
               ORDER BY priority DESC,created_at,task_id LIMIT 1""",
            (now,),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return {"ok": True, "empty": True}
        task_id = str(row["task_id"])
        conn.execute(
            """UPDATE tasks SET state='leased',lease_owner=?,lease_expires_at=?,
               attempt_count=attempt_count+1,updated_at=? WHERE task_id=?""",
            (lease_owner, expires, now, task_id),
        )
        event(conn, task_id, "leased", {"lease_owner": lease_owner, "lease_expires_at": expires})
        task = fetch(conn, task_id)
        conn.execute("COMMIT")
        return {"ok": True, "empty": False, "task": task}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def active(task: dict[str, Any], lease_owner: str, expected: set[str]) -> str:
    if task.get("state") not in expected:
        return "invalid_state"
    if task.get("lease_owner") != lease_owner:
        return "lease_owner_mismatch"
    expiry = str(task.get("lease_expires_at") or "")
    if not expiry or parse_iso(expiry) <= datetime.now(timezone.utc):
        return "lease_expired"
    return ""


def transition(
    task_id: str,
    *,
    lease_owner: str,
    expected: set[str],
    next_state: str,
    event_type: str,
    db_path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = fetch(conn, task_id)
        if task is None:
            conn.execute("COMMIT")
            return {"ok": False, "reason": "task_not_found"}
        issue = active(task, lease_owner, expected)
        if issue:
            conn.execute("COMMIT")
            return {"ok": False, "reason": issue, "task": task}
        now = now_iso()
        acked_at = now if next_state == "acked" else str(task.get("acked_at") or "")
        conn.execute(
            "UPDATE tasks SET state=?,acked_at=?,updated_at=? WHERE task_id=?",
            (next_state, acked_at, now, task_id),
        )
        event(conn, task_id, event_type, {"lease_owner": lease_owner})
        task = fetch(conn, task_id)
        conn.execute("COMMIT")
        return {"ok": True, "task": task}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def acknowledge(task_id: str, *, lease_owner: str, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    return transition(
        task_id,
        lease_owner=lease_owner,
        expected={"leased"},
        next_state="acked",
        event_type="acked",
        db_path=db_path,
    )


def begin(task_id: str, *, lease_owner: str, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    task = get(task_id, db_path=db_path)
    if task and task.get("approval_state") not in {"not_required", "approved"}:
        return {"ok": False, "reason": "approval_required", "task": task}
    return transition(
        task_id,
        lease_owner=lease_owner,
        expected={"acked"},
        next_state="executing",
        event_type="execution_started",
        db_path=db_path,
    )


def pause_for_approval(task_id: str, *, lease_owner: str, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = fetch(conn, task_id)
        if task is None:
            conn.execute("COMMIT")
            return {"ok": False, "reason": "task_not_found"}
        issue = active(task, lease_owner, {"acked"})
        if issue:
            conn.execute("COMMIT")
            return {"ok": False, "reason": issue, "task": task}
        now = now_iso()
        conn.execute(
            """UPDATE tasks SET state='waiting_approval',approval_state='pending',lease_owner='',
               lease_expires_at='',updated_at=? WHERE task_id=?""",
            (now, task_id),
        )
        event(conn, task_id, "approval_requested", {"requested_by": lease_owner})
        task = fetch(conn, task_id)
        conn.execute("COMMIT")
        return {"ok": True, "task": task}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def decide_approval(task_id: str, *, decision: str, note: str = "", db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        return {"ok": False, "reason": "invalid_approval_decision"}
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = fetch(conn, task_id)
        if task is None:
            conn.execute("COMMIT")
            return {"ok": False, "reason": "task_not_found"}
        if task.get("state") != "waiting_approval":
            conn.execute("COMMIT")
            return {"ok": False, "reason": "task_not_waiting_approval", "task": task}
        now = now_iso()
        state = "queued" if decision == "approved" else "rejected"
        conn.execute(
            "UPDATE tasks SET state=?,approval_state=?,next_attempt_at=?,last_error=?,updated_at=? WHERE task_id=?",
            (state, decision, now, "" if decision == "approved" else note, now, task_id),
        )
        event(conn, task_id, f"approval_{decision}", {"note": note})
        task = fetch(conn, task_id)
        conn.execute("COMMIT")
        return {"ok": True, "task": task}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def complete(task_id: str, *, lease_owner: str, result: dict[str, Any], db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"ok": False, "reason": "result_must_be_object"}
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = fetch(conn, task_id)
        if task is None:
            conn.execute("COMMIT")
            return {"ok": False, "reason": "task_not_found"}
        issue = active(task, lease_owner, {"executing"})
        if issue:
            conn.execute("COMMIT")
            return {"ok": False, "reason": issue, "task": task}
        now = now_iso()
        conn.execute(
            """UPDATE tasks SET state='succeeded',result_json=?,lease_owner='',lease_expires_at='',
               last_error='',updated_at=? WHERE task_id=?""",
            (dump(result), now, task_id),
        )
        event(conn, task_id, "succeeded", {"result_keys": sorted(result)})
        task = fetch(conn, task_id)
        conn.execute("COMMIT")
        return {"ok": True, "task": task}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def fail(task_id: str, *, lease_owner: str, reason: str, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = fetch(conn, task_id)
        if task is None:
            conn.execute("COMMIT")
            return {"ok": False, "reason": "task_not_found"}
        issue = active(task, lease_owner, {"acked", "executing"})
        if issue:
            conn.execute("COMMIT")
            return {"ok": False, "reason": issue, "task": task}
        terminal = int(task.get("attempt_count") or 0) >= int(task.get("max_attempts") or 1)
        now = datetime.now(timezone.utc)
        state = "dead_letter" if terminal else "retry_wait"
        retry_at = "" if terminal else (now + timedelta(seconds=int(task.get("retry_delay_seconds") or 60))).isoformat()
        conn.execute(
            """UPDATE tasks SET state=?,next_attempt_at=?,lease_owner='',lease_expires_at='',
               last_error=?,updated_at=? WHERE task_id=?""",
            (state, retry_at, reason, now.isoformat(), task_id),
        )
        event(conn, task_id, state, {"reason": reason})
        task = fetch(conn, task_id)
        conn.execute("COMMIT")
        return {"ok": True, "task": task}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def recover_expired(*, dry_run: bool = True, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    now = now_iso()
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE state IN ('leased','acked','executing') AND lease_expires_at != '' AND lease_expires_at <= ?",
            (now,),
        ).fetchall()
        actions = []
        for row in rows:
            task = to_task(row) or {}
            actions.append(
                {
                    "task_id": task["task_id"],
                    "from": task["state"],
                    "to": "recovery_required" if task["state"] == "executing" else "queued",
                }
            )
        if dry_run or not actions:
            return {"ok": True, "dry_run": dry_run, "actions": actions}
        conn.execute("BEGIN IMMEDIATE")
        for action in actions:
            message = "lease expired during execution; inspect acceptance evidence before retry" if action["to"] == "recovery_required" else ""
            conn.execute(
                "UPDATE tasks SET state=?,lease_owner='',lease_expires_at='',next_attempt_at=?,last_error=?,updated_at=? WHERE task_id=?",
                (action["to"], now, message, now, action["task_id"]),
            )
            event(conn, action["task_id"], "lease_recovered", action)
        conn.execute("COMMIT")
        return {"ok": True, "dry_run": False, "actions": actions}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def snapshot(*, limit: int = 30, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        counts = conn.execute("SELECT state,COUNT(*) AS count FROM tasks GROUP BY state").fetchall()
        tasks = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        events = conn.execute("SELECT * FROM task_events ORDER BY event_id DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
    finally:
        conn.close()
    return {
        "schema": "persistent_task_kernel.snapshot.v1",
        "ok": True,
        "db_path": str(db_path),
        "counts": {str(row["state"]): int(row["count"]) for row in counts},
        "tasks": [to_task(row) for row in tasks],
        "recent_events": [
            {
                "event_id": int(row["event_id"]),
                "task_id": str(row["task_id"]),
                "event_type": str(row["event_type"]),
                "event": load(str(row["event_json"]), {}),
                "created_at": str(row["created_at"]),
            }
            for row in events
        ],
    }


def metrics(*, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    counts = snapshot(limit=1, db_path=db_path).get("counts", {})
    return {
        "schema": "persistent_task_kernel.metrics.v1",
        "ok": True,
        "db_path": str(db_path),
        "state_counts": counts,
        "ready_count": sum(int(counts.get(state, 0)) for state in READY_STATES),
        "waiting_approval_count": int(counts.get("waiting_approval", 0)),
        "recovery_required_count": int(counts.get("recovery_required", 0)),
        "dead_letter_count": int(counts.get("dead_letter", 0)),
    }


def validate(*, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        placeholders = ",".join("?" for _ in ALL_STATES)
        invalid = conn.execute(
            f"SELECT task_id,state FROM tasks WHERE state NOT IN ({placeholders})",
            tuple(sorted(ALL_STATES)),
        ).fetchall()
    finally:
        conn.close()
    required = {
        "task_id", "idempotency_key", "state", "approval_state", "lease_owner",
        "lease_expires_at", "attempt_count", "max_attempts",
    }
    issues = []
    if required - columns:
        issues.append({"code": "missing_columns", "columns": sorted(required - columns)})
    if invalid:
        issues.append({"code": "invalid_states", "task_ids": [str(row["task_id"]) for row in invalid]})
    return {
        "schema": "persistent_task_kernel.validate.v1",
        "ok": not issues,
        "db_path": str(db_path),
        "issues": issues,
        "contract": "sidecar only; no owner command, mail delivery, Codex thread, or scheduler registration",
    }


def doctor(*, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    result = validate(db_path=db_path)
    data = metrics(db_path=db_path)
    issues = list(result["issues"])
    if data["recovery_required_count"]:
        issues.append({"code": "manual_recovery_required", "count": data["recovery_required_count"]})
    if data["dead_letter_count"]:
        issues.append({"code": "dead_letter_present", "count": data["dead_letter_count"]})
    return {
        "schema": "persistent_task_kernel.doctor.v1",
        "ok": not issues,
        "severity": "ok" if not issues else ("blocker" if not result["ok"] else "risk"),
        "issues": issues,
        "metrics": data,
    }


def repair_plan(*, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    report = doctor(db_path=db_path)
    actions = ["run behavior-eval before enabling any dispatch integration"]
    codes = {item.get("code") for item in report["issues"] if isinstance(item, dict)}
    if "manual_recovery_required" in codes:
        actions.append("inspect recovery_required acceptance evidence; do not automatically retry interrupted execution")
    if "dead_letter_present" in codes:
        actions.append("inspect dead-letter receipt before creating a new task with a new idempotency key")
    return {
        "schema": "persistent_task_kernel.repair_plan.v1",
        "ok": True,
        "dry_run": True,
        "blocked": report["severity"] == "blocker",
        "issues": report["issues"],
        "actions": actions,
    }


def behavior_evaluation() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="persistent-task-kernel-") as temp:
        db_path = Path(temp) / "kernel.sqlite"
        checks: list[dict[str, Any]] = []

        first = enqueue(task_id="idempotent-1", idempotency_key="same", task_type="fixture", target_module="fixture", action_type="noop", payload={"v": 1}, db_path=db_path)
        replay = enqueue(task_id="idempotent-2", idempotency_key="same", task_type="fixture", target_module="fixture", action_type="noop", payload={"v": 1}, db_path=db_path)
        checks.append({"name": "idempotency_replay", "ok": first["ok"] and replay.get("duplicate") and replay.get("reason") == "idempotency_replay"})

        task = (claim(lease_owner="worker-a", db_path=db_path).get("task") or {})
        ack = acknowledge(task["task_id"], lease_owner="worker-a", db_path=db_path)
        started = begin(task["task_id"], lease_owner="worker-a", db_path=db_path)
        done = complete(task["task_id"], lease_owner="worker-a", result={"receipt": "ok"}, db_path=db_path)
        checks.append({"name": "ack_before_execution", "ok": ack["ok"] and started["ok"] and done.get("task", {}).get("state") == "succeeded"})

        protected = enqueue(task_id="approval-1", idempotency_key="approval", task_type="fixture", target_module="fixture", action_type="protected", payload={}, db_path=db_path)
        protected_task = (claim(lease_owner="worker-b", db_path=db_path).get("task") or {})
        acknowledge(protected_task["task_id"], lease_owner="worker-b", db_path=db_path)
        waiting = pause_for_approval(protected_task["task_id"], lease_owner="worker-b", db_path=db_path)
        approved = decide_approval(protected_task["task_id"], decision="approved", db_path=db_path)
        checks.append({"name": "approval_pause_and_resume", "ok": protected["ok"] and waiting.get("task", {}).get("state") == "waiting_approval" and approved.get("task", {}).get("state") == "queued"})

        leased = enqueue(task_id="lease-1", idempotency_key="lease", task_type="fixture", target_module="fixture", action_type="noop", payload={}, db_path=db_path)
        leased_task = (claim(lease_owner="worker-c", db_path=db_path).get("task") or {})
        conn = connect(db_path)
        try:
            conn.execute("UPDATE tasks SET lease_expires_at=? WHERE task_id=?", ("2000-01-01T00:00:00+00:00", leased_task["task_id"]))
        finally:
            conn.close()
        recovered = recover_expired(dry_run=False, db_path=db_path)
        checks.append({"name": "expired_lease_requeues", "ok": leased["ok"] and any(item["to"] == "queued" for item in recovered["actions"])})

        uncertain = enqueue(task_id="uncertain-1", idempotency_key="uncertain", task_type="fixture", target_module="fixture", action_type="external", payload={}, db_path=db_path)
        uncertain_task = (claim(lease_owner="worker-d", db_path=db_path).get("task") or {})
        acknowledge(uncertain_task["task_id"], lease_owner="worker-d", db_path=db_path)
        begin(uncertain_task["task_id"], lease_owner="worker-d", db_path=db_path)
        conn = connect(db_path)
        try:
            conn.execute("UPDATE tasks SET lease_expires_at=? WHERE task_id=?", ("2000-01-01T00:00:00+00:00", uncertain_task["task_id"]))
        finally:
            conn.close()
        recovered = recover_expired(dry_run=False, db_path=db_path)
        checks.append({"name": "expired_execution_requires_manual_recovery", "ok": uncertain["ok"] and any(item["to"] == "recovery_required" for item in recovered["actions"])})

        dead = enqueue(task_id="dead-1", idempotency_key="dead", task_type="fixture", target_module="fixture", action_type="noop", payload={}, max_attempts=1, db_path=db_path)
        dead_task = (claim(lease_owner="worker-e", db_path=db_path).get("task") or {})
        acknowledge(dead_task["task_id"], lease_owner="worker-e", db_path=db_path)
        begin(dead_task["task_id"], lease_owner="worker-e", db_path=db_path)
        failed = fail(dead_task["task_id"], lease_owner="worker-e", reason="fixture_failure", db_path=db_path)
        checks.append({"name": "retry_exhaustion_dead_letters", "ok": dead["ok"] and failed.get("task", {}).get("state") == "dead_letter"})
        return {"schema": "persistent_task_kernel.behavior_eval.v1", "ok": all(item["ok"] for item in checks), "checks": checks}


def obj(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sidecar durable structured task lifecycle kernel")
    parser.add_argument("--db-path", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    view = sub.add_parser("snapshot")
    view.add_argument("--limit", type=int, default=30)
    for name in ("doctor", "repair-plan", "validate", "metrics", "behavior-eval"):
        sub.add_parser(name)
    queued = sub.add_parser("enqueue")
    queued.add_argument("--task-id", default=str(uuid.uuid4()))
    queued.add_argument("--idempotency-key", required=True)
    queued.add_argument("--task-type", required=True)
    queued.add_argument("--target-module", required=True)
    queued.add_argument("--action-type", required=True)
    queued.add_argument("--payload-json", default="{}")
    queued.add_argument("--acceptance-json", default="{}")
    queued.add_argument("--requires-approval", action="store_true")
    queued.add_argument("--priority", type=int, default=0)
    queued.add_argument("--max-attempts", type=int, default=3)
    queued.add_argument("--retry-delay-seconds", type=int, default=60)
    claimed = sub.add_parser("claim")
    claimed.add_argument("--lease-owner", required=True)
    claimed.add_argument("--lease-seconds", type=int, default=300)
    for name in ("ack", "start", "pause-approval", "complete", "fail"):
        action = sub.add_parser(name)
        action.add_argument("--task-id", required=True)
        action.add_argument("--lease-owner", required=True)
        if name == "complete":
            action.add_argument("--result-json", required=True)
        if name == "fail":
            action.add_argument("--reason", required=True)
    approval = sub.add_parser("decide-approval")
    approval.add_argument("--task-id", required=True)
    approval.add_argument("--decision", choices=["approved", "rejected"], required=True)
    approval.add_argument("--note", default="")
    recovery = sub.add_parser("recover-expired")
    recovery.add_argument("--apply", action="store_true")
    recovery.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    db_path = Path(args.db_path)
    if args.command == "snapshot":
        result = snapshot(limit=args.limit, db_path=db_path)
    elif args.command == "doctor":
        result = doctor(db_path=db_path)
    elif args.command == "repair-plan":
        result = repair_plan(db_path=db_path)
    elif args.command == "validate":
        result = validate(db_path=db_path)
    elif args.command == "metrics":
        result = metrics(db_path=db_path)
    elif args.command == "behavior-eval":
        result = behavior_evaluation()
    elif args.command == "enqueue":
        result = enqueue(task_id=args.task_id, idempotency_key=args.idempotency_key, task_type=args.task_type, target_module=args.target_module, action_type=args.action_type, payload=obj(args.payload_json), acceptance=obj(args.acceptance_json), requires_approval=args.requires_approval, priority=args.priority, max_attempts=args.max_attempts, retry_delay_seconds=args.retry_delay_seconds, db_path=db_path)
    elif args.command == "claim":
        result = claim(lease_owner=args.lease_owner, lease_seconds=args.lease_seconds, db_path=db_path)
    elif args.command == "ack":
        result = acknowledge(args.task_id, lease_owner=args.lease_owner, db_path=db_path)
    elif args.command == "start":
        result = begin(args.task_id, lease_owner=args.lease_owner, db_path=db_path)
    elif args.command == "pause-approval":
        result = pause_for_approval(args.task_id, lease_owner=args.lease_owner, db_path=db_path)
    elif args.command == "complete":
        result = complete(args.task_id, lease_owner=args.lease_owner, result=obj(args.result_json), db_path=db_path)
    elif args.command == "fail":
        result = fail(args.task_id, lease_owner=args.lease_owner, reason=args.reason, db_path=db_path)
    elif args.command == "decide-approval":
        result = decide_approval(args.task_id, decision=args.decision, note=args.note, db_path=db_path)
    elif not args.apply or args.confirm != "RECOVER-EXPIRED-TASKS":
        result = recover_expired(dry_run=True, db_path=db_path)
        result["apply_required"] = "--apply --confirm RECOVER-EXPIRED-TASKS"
    else:
        result = recover_expired(dry_run=False, db_path=db_path)
    print_json(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
