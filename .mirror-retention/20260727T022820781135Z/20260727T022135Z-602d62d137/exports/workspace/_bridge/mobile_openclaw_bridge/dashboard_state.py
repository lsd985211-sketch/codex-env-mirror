#!/usr/bin/env python3
"""Read-only dashboard state assembly for the mobile OpenClaw bridge.

Owns: building the JSON payloads served by the dashboard state endpoints.
Non-goals: queue mutation, permission decisions, retry/cancel/send actions, or
HTTP routing.
State behavior: reads config JSON and the bridge SQLite database in read-only
mode; never writes bridge state.
Normal callers: mobile_dashboard.load_state facade and dashboard diagnostics.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = {"pending", "claimed", "queued_for_codex", "sent_to_codex", "processing", "waiting_confirmation"}
PLACEHOLDER_EXTERNAL_USERS = {"", "unknown", "unknown@im.wechat"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def truncate(text: Any, limit: int = 240) -> str:
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in ("token", "secret", "password", "cookie", "authorization", "context")):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value[:50]]
    if isinstance(value, str) and len(value) > 1000:
        return truncate(value, 1000)
    return value


def parse_json_text(text: Any, default: Any) -> Any:
    if text in (None, ""):
        return default
    try:
        return json.loads(str(text))
    except Exception:
        return default


def is_placeholder_external_user(external_user: Any) -> bool:
    return str(external_user or "").strip().lower() in PLACEHOLDER_EXTERNAL_USERS


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def table_exists(db: sqlite3.Connection, table: str) -> bool:
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def thread_lookup(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    threads = config.get("threads") if isinstance(config.get("threads"), dict) else {}
    for item in threads.get("items", []):
        thread_id = str(item.get("thread_id") or "")
        stable_id = str(item.get("id") or "")
        info = {
            "id": stable_id,
            "name": str(item.get("name") or stable_id or thread_id),
            "description": str(item.get("description") or ""),
        }
        if thread_id:
            lookup[thread_id] = info
        if stable_id:
            lookup[stable_id] = info
    return lookup


def row_to_task(row: sqlite3.Row, threads: dict[str, dict[str, str]], summary: bool = False) -> dict[str, Any]:
    attachments_raw = parse_json_text(row["attachments_json"], [])
    attachments = [] if summary else redact(attachments_raw)
    metadata = {} if summary else redact(parse_json_text(row["metadata_json"], {}))
    thread_id = str(row["codex_thread_id"] or "")
    thread = threads.get(thread_id, {})
    return {
        "id": row["id"],
        "source": row["source"],
        "external_user": row["external_user"],
        "external_conversation": row["external_conversation"],
        "receiver_account_id": row["receiver_account_id"],
        "command": row["command"],
        "risk_level": row["risk_level"],
        "status": row["status"],
        "push_status": row["push_status"],
        "codex_thread_id": thread_id,
        "codex_turn_id": "",
        "codex_client_message_id": "",
        "thread_name": thread.get("name", ""),
        "thread_route_id": thread.get("id", ""),
        "text": "" if summary else row["text"] or "",
        "text_preview": truncate(row["text"], 180),
        "result": "" if summary else row["result"] or "",
        "result_preview": truncate(row["result"], 220),
        "error": "" if summary else row["error"] or "",
        "error_preview": truncate(row["error"], 180),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "queued_for_codex_at": row["queued_for_codex_at"],
        "sent_to_codex_at": row["sent_to_codex_at"],
        "completed_at": row["completed_at"],
        "pushed_at": row["pushed_at"],
        "attachments": attachments,
        "attachment_count": len(attachments_raw) if isinstance(attachments_raw, list) else 0,
        "metadata": metadata,
        "active": str(row["status"] or "") in ACTIVE_STATUSES,
    }


def event_summary(event_type: str, payload: Any) -> str:
    payload = payload if isinstance(payload, dict) else {}
    mapping = {
        "task_enqueued": "任务已写入本地队列。",
        "queued_for_codex": "已选择目标 Codex 线程并进入投递队列。",
        "thread_route_selected": "已匹配该微信用户的目标线程。",
        "status_ack_delivery_queue_entered": "已向微信发送入队回执。",
        "status_ack_dispatching": "正在投递到 Codex。",
        "sent_to_codex": "已投递到 Codex，等待最终回复。",
        "codex_turn_started": "Codex turn 已创建，开始处理该任务。",
        "status_ack_dispatched": "已向微信发送“正在思考”回执。",
        "status_ack_waiting": "处理超过等待阈值，已发送一次仍在处理回执。",
        "task_done": "Codex 已生成最终回复。",
        "reply_pending": "最终回复已进入后台回发。",
        "status_ack_reply_pending_batch": "已发送积压消息批次说明。",
        "reply_sending": "最终回复正在后台发送。",
        "final_reply_weixin_accepted": "最终回复已被 OpenClaw 微信通道接受。",
        "push_result": "最终回复回发状态已写入队列。",
        "dashboard_send_enqueued": "网页控制台已代表该用户发送消息。",
        "dashboard_manual_retry": "已由网页控制台手动重试。",
        "dashboard_task_cancelled": "已由网页控制台撤回该任务。",
        "dashboard_cancel_codex_attempted": "已尝试中止该任务对应的 Codex turn。",
        "dashboard_attachment_uploaded": "网页控制台已保存附件。",
        "dashboard_weixin_direct_sent": "网页控制台已直接发送给微信用户。",
        "dashboard_weixin_direct_failed": "网页控制台直发微信用户失败。",
    }
    if event_type in mapping:
        return mapping[event_type]
    reason = payload.get("reason") or payload.get("detail") or payload.get("status")
    return f"记录事件：{event_type}" + (f"；{truncate(reason, 140)}" if reason else "。")


def row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    payload = redact(parse_json_text(row["payload_json"], {}))
    event_type = row["event_type"]
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "source": row["source"],
        "event_type": event_type,
        "summary": event_summary(str(event_type), payload),
        "payload": payload,
        "created_at": row["created_at"],
    }


def base_state(db_path: Path, config_path: Path, config: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "generated_at": utc_now(),
        "db_path": str(db_path),
        "config_path": str(config_path),
        "tasks": [],
        "users": [],
        "events": [],
        "status_counts": {},
        "runtime": {},
        "config_summary": {
            "thread_count": len((config.get("threads") or {}).get("items") or []) if isinstance(config, dict) else 0,
            "openclaw_account_id": ((config.get("openclaw") or {}).get("account_id") if isinstance(config, dict) else "") or "",
            "shadow_mode": config.get("shadow_mode") if isinstance(config, dict) else None,
            "delivery_mode": config.get("delivery_mode") if isinstance(config, dict) else None,
        },
    }


def select_tasks(db: sqlite3.Connection, task_id: str, limit: int) -> list[sqlite3.Row]:
    columns = """
        id, source, external_user, external_conversation, command, text,
        risk_level, status, codex_thread_id, result, error, push_status,
        receiver_account_id, created_at, updated_at, queued_for_codex_at,
        sent_to_codex_at, completed_at, pushed_at, attachments_json,
        metadata_json
    """
    if task_id:
        return db.execute(f"SELECT {columns} FROM mobile_tasks WHERE id=? LIMIT 1", (task_id,)).fetchall()
    return db.execute(f"SELECT {columns} FROM mobile_tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


def attach_turn_fields(db: sqlite3.Connection, tasks: list[dict[str, Any]]) -> None:
    if not tasks or not table_exists(db, "mobile_events"):
        return
    task_index = {str(task["id"]): task for task in tasks}
    task_ids = list(task_index.keys())
    placeholders = ",".join("?" for _ in task_ids)
    turn_rows = db.execute(
        f"""
        SELECT task_id, payload_json, created_at
        FROM mobile_events
        WHERE event_type='codex_turn_started'
          AND task_id IN ({placeholders})
        ORDER BY created_at ASC
        """,
        task_ids,
    ).fetchall()
    for event_row in turn_rows:
        task = task_index.get(str(event_row["task_id"]))
        if not task:
            continue
        payload = parse_json_text(event_row["payload_json"], {})
        if not isinstance(payload, dict):
            continue
        turn_id = str(payload.get("turn_id") or "")
        client_message_id = str(payload.get("client_message_id") or "")
        if turn_id:
            task["codex_turn_id"] = turn_id
        if client_message_id:
            task["codex_client_message_id"] = client_message_id
    if not table_exists(db, "mobile_runtime"):
        return
    runtime_keys = [f"codex_turn:{task_id}" for task_id in task_ids]
    runtime_placeholders = ",".join("?" for _ in runtime_keys)
    runtime_rows = db.execute(
        f"""
        SELECT key, value
        FROM mobile_runtime
        WHERE key IN ({runtime_placeholders})
        """,
        runtime_keys,
    ).fetchall()
    for runtime_row in runtime_rows:
        key = str(runtime_row["key"] or "")
        _, _, runtime_task_id = key.partition(":")
        task = task_index.get(runtime_task_id)
        turn_id = str(runtime_row["value"] or "")
        if task and turn_id:
            task["codex_turn_id"] = turn_id


def build_users(db: sqlite3.Connection, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    user_map: dict[str, dict[str, Any]] = {}
    for task in tasks:
        user_id = str(task["external_user"])
        item = user_map.setdefault(user_id, empty_user_item(user_id))
        if task["receiver_account_id"]:
            item["receiver_accounts"].add(task["receiver_account_id"])
        if task["codex_thread_id"]:
            item["thread_ids"].add(task["codex_thread_id"])
        if task["thread_name"]:
            item["thread_names"].add(task["thread_name"])
        item["counts"][task["status"]] = int(item["counts"].get(task["status"], 0)) + 1
        if task["active"]:
            item["active_count"] += 1
        if not item["latest_task_at"] or str(task["created_at"]) > item["latest_task_at"]:
            item["latest_task_at"] = task["created_at"]
            item["latest_task_id"] = task["id"]
            item["latest_status"] = task["status"]
    if table_exists(db, "mobile_users"):
        add_registered_users(db, user_map)
    users = []
    for item in user_map.values():
        if is_placeholder_external_user(item.get("external_user")):
            continue
        converted = dict(item)
        converted["receiver_accounts"] = sorted(item["receiver_accounts"])
        converted["thread_ids"] = sorted(item["thread_ids"])
        converted["thread_names"] = sorted(item["thread_names"])
        users.append(converted)
    users.sort(key=lambda item: item.get("latest_task_at") or item.get("user_updated_at") or "", reverse=True)
    return users


def empty_user_item(user_id: str) -> dict[str, Any]:
    return {
        "external_user": user_id,
        "receiver_accounts": set(),
        "thread_ids": set(),
        "thread_names": set(),
        "latest_task_at": "",
        "latest_task_id": "",
        "latest_status": "",
        "counts": {},
        "active_count": 0,
    }


def add_registered_users(db: sqlite3.Connection, user_map: dict[str, dict[str, Any]]) -> None:
    rows = db.execute("SELECT external_user, display_name, role, enabled, allow_trigger, updated_at FROM mobile_users").fetchall()
    for row in rows:
        user_id = str(row["external_user"])
        item = user_map.setdefault(user_id, empty_user_item(user_id))
        item["display_name"] = row["display_name"]
        item["role"] = row["role"]
        item["enabled"] = bool(row["enabled"])
        item["allow_trigger"] = bool(row["allow_trigger"])
        item["user_updated_at"] = row["updated_at"]


def load_events(db: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    if task_id:
        rows = db.execute(
            """
            SELECT id, task_id, source, event_type, payload_json, created_at
            FROM mobile_events
            WHERE task_id=?
            ORDER BY created_at DESC
            LIMIT 120
            """,
            (task_id,),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT id, task_id, source, event_type, payload_json, created_at
            FROM mobile_events
            ORDER BY created_at DESC
            LIMIT 160
            """
        ).fetchall()
    return [row_to_event(row) for row in rows]


def load_runtime(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    runtime = {}
    rows = db.execute("SELECT key, value, updated_at FROM mobile_runtime ORDER BY key").fetchall()
    for row in rows:
        runtime[str(row["key"])] = {
            "value": redact(parse_json_text(row["value"], row["value"])),
            "updated_at": row["updated_at"],
        }
    return runtime


def load_state(
    db_path: Path,
    config_path: Path,
    limit: int,
    task_id: str = "",
    include_events: bool = False,
    summary: bool = True,
) -> dict[str, Any]:
    config = load_json_file(config_path, {})
    threads = thread_lookup(config if isinstance(config, dict) else {})
    state = base_state(db_path, config_path, config)
    if not db_path.exists():
        state["error"] = "database not found"
        return state

    try:
        with connect_readonly(db_path) as db:
            if not table_exists(db, "mobile_tasks"):
                state["error"] = "mobile_tasks table not found"
                return state
            status_rows = db.execute(
                "SELECT status, COUNT(*) AS n FROM mobile_tasks GROUP BY status ORDER BY status"
            ).fetchall()
            state["status_counts"] = {str(row["status"]): int(row["n"]) for row in status_rows}

            tasks = [row_to_task(row, threads, summary=summary) for row in select_tasks(db, task_id, limit)]
            attach_turn_fields(db, tasks)
            state["tasks"] = tasks
            state["users"] = build_users(db, tasks)

            if include_events and table_exists(db, "mobile_events"):
                state["events"] = load_events(db, task_id)
            if include_events and table_exists(db, "mobile_runtime"):
                state["runtime"] = load_runtime(db)

            state["ok"] = True
            return state
    except sqlite3.Error as exc:
        state["error"] = f"sqlite error: {exc}"
        return state
