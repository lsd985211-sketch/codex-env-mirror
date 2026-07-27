#!/usr/bin/env python3
"""Derived SQLite query surface for resident email scheduler state.

Ownership: email_scheduler remains the business-state owner. This module only
builds a queryable derived index from existing mail task tables and runtime JSON.
Non-goals: sending mail, marking inbox items read, repairing queues, or treating
SQLite as the source of truth.
State behavior: read-only by default; `refresh --apply` writes only the derived
SQLite index under email_scheduler_state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import email_scheduler as scheduler


DEFAULT_DB = scheduler.EMAIL_STATE_DIR / "email_state.sqlite"
MAX_JSON_BYTES = 12000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: Any) -> str:
    text = "|".join(str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:24]


def compact_json(value: Any, limit: int = MAX_JSON_BYTES) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = json.dumps(str(value), ensure_ascii=False)
    data = text.encode("utf-8", errors="replace")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", errors="ignore") + "...<truncated>"


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
        CREATE TABLE IF NOT EXISTS email_identities (
          identity_name TEXT PRIMARY KEY,
          default_account TEXT,
          account_count INTEGER NOT NULL,
          smtp_configured INTEGER NOT NULL,
          imap_configured INTEGER NOT NULL,
          payload_json TEXT NOT NULL,
          indexed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS email_task_rows (
          task_name TEXT PRIMARY KEY,
          task_type TEXT,
          trigger_type TEXT,
          target TEXT,
          action TEXT,
          status TEXT,
          owner_identity TEXT,
          due INTEGER NOT NULL,
          scheduled_at TEXT,
          sender TEXT,
          recipient TEXT,
          payload_json TEXT NOT NULL,
          indexed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_email_task_rows_status ON email_task_rows(status, due, scheduled_at);

        CREATE TABLE IF NOT EXISTS email_stage_items (
          stage TEXT NOT NULL,
          item_id TEXT NOT NULL,
          schedule_run_id TEXT,
          task_name TEXT,
          status TEXT,
          created_at TEXT,
          updated_at TEXT,
          scheduled_at TEXT,
          subject TEXT,
          sender TEXT,
          recipients_json TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          indexed_at TEXT NOT NULL,
          PRIMARY KEY(stage, item_id)
        );
        CREATE INDEX IF NOT EXISTS idx_email_stage_items_status ON email_stage_items(stage, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_email_stage_items_run ON email_stage_items(schedule_run_id);

        CREATE TABLE IF NOT EXISTS email_inbox_messages (
          message_id TEXT PRIMARY KEY,
          account TEXT,
          subject TEXT,
          from_text TEXT,
          received_at TEXT,
          lifecycle_status TEXT NOT NULL DEFAULT 'new',
          has_attachments INTEGER NOT NULL,
          attachment_count INTEGER NOT NULL,
          payload_json TEXT NOT NULL,
          indexed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_email_inbox_messages_received ON email_inbox_messages(received_at DESC);

        CREATE TABLE IF NOT EXISTS email_smtp_receipts (
          receipt_id TEXT PRIMARY KEY,
          schedule_run_id TEXT,
          task_name TEXT,
          status TEXT,
          sender TEXT,
          recipients_json TEXT NOT NULL,
          subject TEXT,
          sent_at TEXT,
          payload_json TEXT NOT NULL,
          indexed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_email_smtp_receipts_sent ON email_smtp_receipts(sent_at DESC);

        CREATE TABLE IF NOT EXISTS email_reconciliation (
          reconciliation_id TEXT PRIMARY KEY,
          group_key TEXT NOT NULL,
          classification TEXT NOT NULL,
          severity TEXT NOT NULL,
          task_id TEXT NOT NULL DEFAULT '',
          schedule_run_id TEXT NOT NULL DEFAULT '',
          content_job_id TEXT NOT NULL DEFAULT '',
          outbox_item_id TEXT NOT NULL DEFAULT '',
          inbound_message_id TEXT NOT NULL DEFAULT '',
          rfc_message_id TEXT NOT NULL DEFAULT '',
          smtp_receipt_id TEXT NOT NULL DEFAULT '',
          source_count INTEGER NOT NULL,
          source_refs_json TEXT NOT NULL,
          details_json TEXT NOT NULL,
          indexed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_email_reconciliation_class
          ON email_reconciliation(classification, severity, group_key);

        """
    )
    inbox_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(email_inbox_messages)").fetchall()}
    if "lifecycle_status" not in inbox_columns:
        conn.execute("ALTER TABLE email_inbox_messages ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'new'")
    conn.executescript(
        """
        DROP VIEW IF EXISTS email_status_summary;
        CREATE VIEW email_status_summary AS
        SELECT 'task' AS area, COALESCE(status, '') AS status, COUNT(*) AS count FROM email_task_rows GROUP BY status
        UNION ALL
        SELECT stage AS area, COALESCE(status, '') AS status, COUNT(*) AS count FROM email_stage_items GROUP BY stage, status
        UNION ALL
        SELECT 'inbox_message' AS area, COALESCE(lifecycle_status, 'new') AS status, COUNT(*) AS count FROM email_inbox_messages GROUP BY lifecycle_status
        UNION ALL
        SELECT 'smtp_receipt' AS area, COALESCE(status, '') AS status, COUNT(*) AS count FROM email_smtp_receipts GROUP BY status;
        """
    )
    conn.commit()


def payload_id(payload: dict[str, Any], fallback: str) -> str:
    for key in (
        "schedule_run_id",
        "content_job_id",
        "draft_item_id",
        "outbox_item_id",
        "delivery_job_id",
        "job_id",
        "inbox_job_id",
        "message_id",
        "receipt_id",
        "id",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return fallback


def stage_item_id(stage: str, payload: dict[str, Any], fallback: str) -> str:
    id_keys = {
        "schedule_run": ("schedule_run_id",),
        "content_job": ("content_job_id", "job_id"),
        "draft_item": ("draft_item_id",),
        "outbox_item": ("outbox_item_id",),
        "delivery_job": ("delivery_job_id",),
        "legacy_job": ("job_id",),
        "inbox_job": ("inbox_job_id", "job_id"),
    }.get(stage, ())
    for key in id_keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return payload_id(payload, fallback)


def inbox_lifecycle_status(job: dict[str, Any], sent_task_names: set[str]) -> str:
    if not job:
        return "new"
    status = str(job.get("status") or "").strip()
    reply_task_name = str(job.get("reply_task_name") or "").strip()
    if reply_task_name and reply_task_name in sent_task_names:
        return "replied"
    if status in {
        scheduler.INBOX_JOB_FAILED,
        scheduler.INBOX_JOB_DEAD_LETTER,
        scheduler.INBOX_JOB_NEEDS_REVIEW,
        scheduler.INBOX_JOB_REPLY_DRAFTED,
    }:
        return "failed/review"
    if status in {scheduler.INBOX_JOB_REPLY_TASK_CREATED, scheduler.INBOX_JOB_PROCESSED}:
        return "processed"
    return "processing"


def recipients_json(payload: dict[str, Any]) -> str:
    value = payload.get("recipients") or payload.get("recipient_accounts") or payload.get("to") or []
    if isinstance(value, str):
        value = [value] if value else []
    return compact_json(value, limit=3000)


def payload_dict(row: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(str(row.get("payload_json") or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def first_id(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def build_reconciliation(rows: dict[str, list[dict[str, Any]]], indexed_at: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    message_to_run: dict[str, str] = {}
    delivery_to_run: dict[str, str] = {}
    for row in rows.get("email_stage_items", []):
        payload = payload_dict(row)
        run_id = str(row.get("schedule_run_id") or payload.get("schedule_run_id") or "")
        message_id = first_id(payload, "message_id", "message_id_header", "reply_to_message_id_header")
        if run_id and message_id:
            message_to_run[message_id] = run_id
        if run_id and str(row.get("stage") or "") == "delivery_job":
            delivery_to_run[str(row.get("item_id") or "")] = run_id
    for row in rows.get("email_stage_items", []):
        payload = payload_dict(row)
        run_id = str(row.get("schedule_run_id") or payload.get("schedule_run_id") or "")
        inbound_id = first_id(payload, "inbound_message_id", "reply_to_inbound_message_id")
        group_key = "run:" + run_id if run_id else ("inbound:" + inbound_id if inbound_id else f"stage:{row.get('stage')}:{row.get('item_id')}")
        groups.setdefault(group_key, []).append({"source": str(row.get("stage") or "stage"), "id": str(row.get("item_id") or ""), "status": str(row.get("status") or ""), "payload": payload})
    for row in rows.get("email_smtp_receipts", []):
        payload = payload_dict(row)
        message_id = first_id(payload, "message_id", "message_id_header")
        receipt_id = str(row.get("receipt_id") or payload.get("receipt_id") or "")
        run_id = str(row.get("schedule_run_id") or payload.get("schedule_run_id") or message_to_run.get(message_id) or delivery_to_run.get(receipt_id) or "")
        group_key = "run:" + run_id if run_id else "receipt:" + str(row.get("receipt_id") or "")
        groups.setdefault(group_key, []).append({"source": "smtp_receipt", "id": str(row.get("receipt_id") or ""), "status": str(row.get("status") or "sent"), "payload": payload})
    for row in rows.get("email_inbox_messages", []):
        payload = payload_dict(row)
        inbound_id = first_id(payload, "inbound_message_id", "message_id", "id") or str(row.get("message_id") or "")
        groups.setdefault("inbound:" + inbound_id, []).append({"source": "inbox_message", "id": inbound_id, "status": str(payload.get("status") or "new"), "payload": payload})

    result: list[dict[str, Any]] = []
    for group_key, items in sorted(groups.items()):
        sources = {item["source"] for item in items}
        statuses = {item["status"] for item in items if item["status"]}
        receipt = next((item for item in items if item["source"] == "smtp_receipt"), None)
        schedule_run = next((item for item in items if item["source"] == "schedule_run"), None)
        archived_chain = bool(schedule_run and schedule_run["status"] == "archived") or any(str(item["payload"].get("archive_reason") or "") for item in items)
        sent_stage = any(item["source"] in {"outbox_item", "delivery_job", "schedule_run"} and item["status"] in {"sent", "succeeded", "delivered"} for item in items)
        stale_stage = any(item["source"] in {"outbox_item", "delivery_job"} and item["status"] in {"ready", "stale", "queued", "retry", "failed", "dead_letter"} for item in items)
        downstream = bool(sources & {"content_job", "draft_item", "outbox_item", "delivery_job"})
        if group_key.startswith("run:") and downstream and schedule_run is None:
            classification, severity, reason = "missing_source", "risk", "downstream stage references a missing schedule run"
        elif receipt and group_key.startswith("receipt:"):
            classification, severity, reason = "orphan_file", "risk", "SMTP receipt cannot be linked to a schedule run"
        elif sent_stage and receipt is None:
            classification, severity, reason = "missing_receipt", "risk", "sent stage has no matching SMTP receipt"
        elif receipt and stale_stage and not archived_chain:
            classification, severity, reason = "stale_mirror", "advisory", "delivery receipt exists while an earlier stage remains non-final"
        elif "sent" in statuses and statuses & {"failed", "dead_letter"} and receipt is None:
            classification, severity, reason = "contradictory_state", "risk", "same chain contains sent and failed states without a receipt"
        elif group_key.startswith("inbound:") and "inbox_job" in sources and "inbox_message" not in sources:
            classification, severity, reason = "missing_source", "risk", "inbox job references a missing inbound message"
        else:
            classification, severity, reason = "valid_overlap", "ok", "linked records are coherent or independently valid"
        merged = {key: first_id(item["payload"], key) for key in ("task_id", "schedule_run_id", "content_job_id", "outbox_item_id", "inbound_message_id") for item in items if first_id(item["payload"], key)}
        first_payload = items[0]["payload"] if items else {}
        schedule_run_id = group_key[4:] if group_key.startswith("run:") else str(merged.get("schedule_run_id") or "")
        rfc_message_id = next((first_id(item["payload"], "message_id", "message_id_header") for item in items if first_id(item["payload"], "message_id", "message_id_header")), "")
        receipt_id = receipt["id"] if receipt else ""
        result.append({
            "reconciliation_id": stable_id(group_key, classification), "group_key": group_key,
            "classification": classification, "severity": severity,
            "task_id": str(merged.get("task_id") or first_payload.get("task_name") or ""),
            "schedule_run_id": schedule_run_id, "content_job_id": str(merged.get("content_job_id") or ""),
            "outbox_item_id": str(merged.get("outbox_item_id") or ""),
            "inbound_message_id": str(merged.get("inbound_message_id") or ""),
            "rfc_message_id": rfc_message_id, "smtp_receipt_id": receipt_id,
            "source_count": len(items), "source_refs_json": compact_json([{"source": item["source"], "id": item["id"], "status": item["status"]} for item in items]),
            "details_json": compact_json({"reason": reason, "sources": sorted(sources), "statuses": sorted(statuses)}),
            "indexed_at": indexed_at,
        })
    return result


def stage_rows(stage: str, root: Path, indexed_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted(root.glob("*.json")):
        payload = scheduler.read_json(path)
        if scheduler.is_smoke_stage_payload(path, payload):
            continue
        item_id = stage_item_id(stage, payload, path.stem)
        rows.append(
            {
                "stage": stage,
                "item_id": item_id,
                "schedule_run_id": str(payload.get("schedule_run_id") or ""),
                "task_name": str(payload.get("task_name") or payload.get("source_task_name") or ""),
                "status": str(payload.get("status") or ""),
                "created_at": str(payload.get("created_at") or payload.get("queued_at") or ""),
                "updated_at": str(payload.get("updated_at") or payload.get("finished_at") or ""),
                "scheduled_at": str(payload.get("scheduled_at") or payload.get("not_before") or ""),
                "subject": str(payload.get("subject") or payload.get("mail_subject") or ""),
                "sender": str(payload.get("sender") or payload.get("sender_name") or payload.get("from") or ""),
                "recipients_json": recipients_json(payload),
                "payload_json": compact_json(payload),
                "indexed_at": indexed_at,
            }
        )
    return rows


def collect(indexed_at: str) -> dict[str, list[dict[str, Any]]]:
    tasks, identities = scheduler.load_world()
    identity_rows = [
        {
            "identity_name": identity.name,
            "default_account": identity.default_account,
            "account_count": len(identity.accounts),
            "smtp_configured": 1 if identity.smtp else 0,
            "imap_configured": 1 if identity.imap else 0,
            "payload_json": compact_json(
                {
                    "name": identity.name,
                    "description": identity.description,
                    "default_account": identity.default_account,
                    "account_count": len(identity.accounts),
                    "smtp_configured": bool(identity.smtp),
                    "imap_configured": bool(identity.imap),
                }
            ),
            "indexed_at": indexed_at,
        }
        for identity in identities.values()
    ]
    task_rows: list[dict[str, Any]] = []
    for task in tasks:
        runtime = scheduler.build_task_runtime(task, identities)
        task_rows.append(
            {
                "task_name": str(task.get("任务名") or ""),
                "task_type": str(task.get("任务类型") or ""),
                "trigger_type": str(task.get("触发方式") or ""),
                "target": str(task.get("目标") or ""),
                "action": str(task.get("执行动作") or ""),
                "status": str(task.get("状态") or ""),
                "owner_identity": str(task.get("责任身份") or ""),
                "due": 1 if runtime.get("due") else 0,
                "scheduled_at": runtime["scheduled_at"].isoformat() if runtime.get("scheduled_at") else "",
                "sender": runtime["sender"].name if runtime.get("sender") else "",
                "recipient": runtime["recipient"].name if runtime.get("recipient") else "",
                "payload_json": compact_json(task),
                "indexed_at": indexed_at,
            }
        )
    stages = []
    for stage, root in (
        ("schedule_run", scheduler.SCHEDULE_RUNS_DIR),
        ("content_job", scheduler.CONTENT_JOBS_DIR),
        ("draft_item", scheduler.DRAFT_ITEMS_DIR),
        ("outbox_item", scheduler.OUTBOX_ITEMS_DIR),
        ("delivery_job", scheduler.DELIVERY_JOBS_DIR),
        ("legacy_job", scheduler.EMAIL_JOBS_DIR),
        ("inbox_job", scheduler.INBOX_JOBS_DIR),
    ):
        stages.extend(stage_rows(stage, root, indexed_at))
    inbox_jobs_by_message: dict[str, dict[str, Any]] = {}
    for path in sorted(scheduler.INBOX_JOBS_DIR.glob("*.json")) if scheduler.INBOX_JOBS_DIR.exists() else []:
        job = scheduler.read_json(path)
        inbound_id = str(job.get("inbound_message_id") or "").strip()
        if inbound_id:
            inbox_jobs_by_message[inbound_id] = job
    sent_task_names: set[str] = set()
    for path in sorted(scheduler.SMTP_RECEIPTS_DIR.glob("*.json")) if scheduler.SMTP_RECEIPTS_DIR.exists() else []:
        receipt = scheduler.read_json(path)
        if str(receipt.get("status") or "sent") == scheduler.STATUS_SENT:
            task_name = str(receipt.get("task_name") or "").strip()
            if task_name:
                sent_task_names.add(task_name)
    inbox_rows = []
    for path in sorted(scheduler.INBOX_MESSAGES_DIR.glob("*.json")) if scheduler.INBOX_MESSAGES_DIR.exists() else []:
        payload = scheduler.read_json(path)
        attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
        message_id = str(payload.get("inbound_message_id") or payload.get("message_id") or path.stem)
        inbox_rows.append(
            {
                "message_id": message_id,
                "account": str(payload.get("account") or ""),
                "subject": str(payload.get("subject") or ""),
                "from_text": str(payload.get("from") or payload.get("from_text") or ""),
                "received_at": str(payload.get("received_at") or payload.get("date") or ""),
                "lifecycle_status": inbox_lifecycle_status(inbox_jobs_by_message.get(message_id, {}), sent_task_names),
                "has_attachments": 1 if attachments else 0,
                "attachment_count": len(attachments),
                "payload_json": compact_json(payload),
                "indexed_at": indexed_at,
            }
        )
    receipt_rows = []
    for path in sorted(scheduler.SMTP_RECEIPTS_DIR.glob("*.json")) if scheduler.SMTP_RECEIPTS_DIR.exists() else []:
        payload = scheduler.read_json(path)
        receipt_rows.append(
            {
                "receipt_id": str(payload.get("receipt_id") or path.stem),
                "schedule_run_id": str(payload.get("schedule_run_id") or ""),
                "task_name": str(payload.get("task_name") or ""),
                "status": str(payload.get("status") or "sent"),
                "sender": str(payload.get("sender") or payload.get("sender_name") or ""),
                "recipients_json": recipients_json(payload),
                "subject": str(payload.get("subject") or ""),
                "sent_at": str(payload.get("sent_at") or payload.get("created_at") or ""),
                "payload_json": compact_json(payload),
                "indexed_at": indexed_at,
            }
        )
    rows = {
        "email_identities": identity_rows,
        "email_task_rows": task_rows,
        "email_stage_items": stages,
        "email_inbox_messages": inbox_rows,
        "email_smtp_receipts": receipt_rows,
    }
    rows["email_reconciliation"] = build_reconciliation(rows, indexed_at)
    return rows


def replace_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    conn.execute(f"DELETE FROM {table}")
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join(":" + column for column in columns)
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        rows,
    )


def refresh(*, apply: bool, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    indexed_at = now_iso()
    rows = collect(indexed_at)
    if apply:
        with closing(connect(db_path)) as conn:
            init_db(conn)
            for table, table_rows in rows.items():
                replace_rows(conn, table, table_rows)
            conn.commit()
    return {
        "schema": "email_state_index.refresh.v1",
        "ok": True,
        "generated_at": indexed_at,
        "apply": apply,
        "db_path": str(db_path),
        "tables": {table: len(table_rows) for table, table_rows in rows.items()},
        "business_state_mutation": False,
    }


def snapshot(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    exists = db_path.exists()
    tables: dict[str, int] = {}
    latest = ""
    if exists:
        with closing(connect(db_path)) as conn:
            init_db(conn)
            for table in ("email_identities", "email_task_rows", "email_stage_items", "email_inbox_messages", "email_smtp_receipts", "email_reconciliation"):
                tables[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            latest_values = [
                str(row[0] or "")
                for row in conn.execute(
                    """
                    SELECT MAX(indexed_at) FROM email_identities
                    UNION ALL SELECT MAX(indexed_at) FROM email_task_rows
                    UNION ALL SELECT MAX(indexed_at) FROM email_stage_items
                    UNION ALL SELECT MAX(indexed_at) FROM email_inbox_messages
                    UNION ALL SELECT MAX(indexed_at) FROM email_smtp_receipts
                    """
                ).fetchall()
            ]
            latest = max((value for value in latest_values if value), default="")
    return {
        "schema": "email_state_index.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "db_path": str(db_path),
        "db_exists": exists,
        "latest_indexed_at": latest,
        "tables": tables,
        "source_of_truth": "email_scheduler files and runtime JSON",
    }


def query(db_path: Path = DEFAULT_DB, *, table: str = "summary", status: str = "", limit: int = 50) -> dict[str, Any]:
    allowed = {
        "summary": "SELECT area, status, count FROM email_status_summary ORDER BY area, status",
        "tasks": "SELECT task_name, status, due, scheduled_at, sender, recipient, target FROM email_task_rows",
        "stages": "SELECT stage, item_id, schedule_run_id, task_name, status, updated_at, subject FROM email_stage_items",
        "inbox": "SELECT message_id, account, subject, from_text, received_at, lifecycle_status, has_attachments, attachment_count FROM email_inbox_messages",
        "receipts": "SELECT receipt_id, schedule_run_id, task_name, status, sender, subject, sent_at FROM email_smtp_receipts",
        "identities": "SELECT identity_name, default_account, account_count, smtp_configured, imap_configured FROM email_identities",
        "reconciliation": "SELECT reconciliation_id, group_key, classification, severity, task_id, schedule_run_id, content_job_id, outbox_item_id, inbound_message_id, rfc_message_id, smtp_receipt_id, source_count, details_json, indexed_at FROM email_reconciliation",
    }
    if table not in allowed:
        return {"schema": "email_state_index.query.v1", "ok": False, "reason": f"unsupported table: {table}", "allowed": sorted(allowed)}
    with closing(connect(db_path)) as conn:
        init_db(conn)
        sql = allowed[table]
        params: list[Any] = []
        if status and table in {"tasks", "stages", "receipts", "inbox"}:
            sql += " WHERE lifecycle_status = ?" if table == "inbox" else " WHERE status = ?"
            params.append(status)
        if table != "summary":
            sql += " ORDER BY 1 DESC LIMIT ?"
            params.append(max(1, min(int(limit or 50), 500)))
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    return {
        "schema": "email_state_index.query.v1",
        "ok": True,
        "db_path": str(db_path),
        "table": table,
        "count": len(rows),
        "rows": rows,
    }


def metrics(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    snap = snapshot(db_path)
    rows = query(db_path, table="summary").get("rows", []) if snap.get("db_exists") else []
    reconciliation_counts: dict[str, int] = {}
    if snap.get("db_exists"):
        with closing(connect(db_path)) as conn:
            init_db(conn)
            reconciliation_counts = {str(row[0]): int(row[1]) for row in conn.execute("SELECT classification, COUNT(*) FROM email_reconciliation GROUP BY classification")}
    return {
        "schema": "email_state_index.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "db_path": str(db_path),
        "db_exists": snap.get("db_exists"),
        "latest_indexed_at": snap.get("latest_indexed_at", ""),
        "tables": snap.get("tables", {}),
        "summary": rows,
        "reconciliation_counts": reconciliation_counts,
        "reconciliation_attention_count": sum(count for key, count in reconciliation_counts.items() if key != "valid_overlap"),
    }


def reconciliation_plan(*, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    indexed_at = now_iso()
    rows = collect(indexed_at).get("email_reconciliation", [])
    actions: list[dict[str, Any]] = []
    for row in rows:
        classification = str(row.get("classification") or "")
        if classification == "valid_overlap":
            continue
        run_id = str(row.get("schedule_run_id") or "")
        if classification == "stale_mirror" and run_id:
            command = ["python", "_bridge/shared/email_scheduler.py", "inspect-run", "--schedule-run-id", run_id]
        elif classification == "missing_receipt" and run_id:
            command = ["python", "_bridge/shared/email_scheduler.py", "inspect-run", "--schedule-run-id", run_id]
        else:
            command = ["python", "_bridge/shared/email_scheduler.py", "state-index", "--apply"]
        actions.append({"classification": classification, "severity": row.get("severity"),
                        "group_key": row.get("group_key"), "apply": False,
                        "owner_command": command,
                        "reason": json.loads(str(row.get("details_json") or "{}" )).get("reason", "")})
    return {"schema": "email_state_index.repair_plan.v1", "ok": True,
            "generated_at": indexed_at, "dry_run": True, "action_count": len(actions),
            "actions": actions,
            "contract": {"sends_mail": False, "deletes_mail": False,
                         "mutates_business_state": False, "direct_sql_repair": False}}


def validate(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    dry = refresh(apply=False, db_path=db_path)
    checks = [
        {"name": "dry_run_collects_tasks", "ok": int(dry["tables"].get("email_task_rows") or 0) >= 0, "detail": dry["tables"].get("email_task_rows")},
        {"name": "dry_run_collects_identities", "ok": int(dry["tables"].get("email_identities") or 0) >= 0, "detail": dry["tables"].get("email_identities")},
        {"name": "schema_initializes", "ok": True, "detail": str(db_path)},
        {"name": "reconciliation_collects", "ok": int(dry["tables"].get("email_reconciliation") or 0) >= 0, "detail": dry["tables"].get("email_reconciliation")},
    ]
    with closing(connect(db_path)) as conn:
        init_db(conn)
        checks.append({"name": "summary_view_queryable", "ok": True, "detail": len(conn.execute("SELECT * FROM email_status_summary LIMIT 5").fetchall())})
    return {
        "schema": "email_state_index.validate.v1",
        "ok": all(item["ok"] for item in checks),
        "generated_at": now_iso(),
        "checks": checks,
        "dry_run": dry,
        "snapshot": snapshot(db_path),
    }


def print_json(payload: dict[str, Any]) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derived SQLite index for email scheduler state")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "metrics", "validate", "repair-plan"):
        sub.add_parser(name)
    refresh_parser = sub.add_parser("refresh")
    refresh_parser.add_argument("--apply", action="store_true")
    query_parser = sub.add_parser("query")
    query_parser.add_argument("--table", choices=["summary", "tasks", "stages", "inbox", "receipts", "identities", "reconciliation"], default="summary")
    query_parser.add_argument("--status", default="")
    query_parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    db_path = Path(args.db).expanduser().resolve()
    if args.command == "snapshot":
        payload = snapshot(db_path)
    elif args.command == "metrics":
        payload = metrics(db_path)
    elif args.command == "validate":
        payload = validate(db_path)
    elif args.command == "repair-plan":
        payload = reconciliation_plan(db_path=db_path)
    elif args.command == "refresh":
        payload = refresh(apply=bool(args.apply), db_path=db_path)
    elif args.command == "query":
        payload = query(db_path, table=args.table, status=args.status, limit=args.limit)
    else:
        payload = {"ok": False, "reason": f"unknown command: {args.command}"}
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
