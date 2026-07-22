#!/usr/bin/env python3
"""MCP bridge server for the mobile OpenClaw queue.

Default transport: stdio JSON-RPC for Codex MCP clients.
Optional transport: HTTP for local health/debug probing.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mobile_openclaw_cli import (  # noqa: E402
    DEFAULT_CONFIG,
    MobileQueue,
    bridge_supplement_base_task_id,
    bridge_supplement_context_from_events,
    bridge_supplement_context_from_payload,
    bridge_supplement_key,
    bridge_supplement_task_ids,
    load_config,
    merge_bridge_supplement_payload,
    mcp_ack_payload,
    process_mcp_acked_pending_supplements,
    queue_from_config,
    task_is_supplement_context,
    task_event_exists,
    task_owns_final_reply,
    utc_now,
    valid_mcp_ack_base_owner,
)
from mobile_bridge_mcp_server_routes import load_valid_supplement  # noqa: E402

MCP_PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "mobile-openclaw-bridge"
SERVER_VERSION = "0.2.0"


def load_bridge_state(config_path: Path) -> tuple[dict[str, Any], Any]:
    config = load_config(config_path)
    config["_config_path"] = str(config_path)
    queue = queue_from_config(config)
    return config, queue


def decode_attachments(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def task_cursor(task: dict[str, Any]) -> str:
    return str(task.get("updated_at") or task.get("created_at") or task.get("id") or "")


def task_snapshot(task: dict[str, Any]) -> dict[str, Any]:
    attachments = decode_attachments(task.get("attachments_json"))
    return {
        "message_id": str(task.get("id") or ""),
        "thread_id": str(task.get("codex_thread_id") or ""),
        "source_user": str(task.get("external_user") or ""),
        "kind": "attachment" if attachments else "text",
        "text": str(task.get("text") or ""),
        "attachments": attachments,
        "status": str(task.get("status") or ""),
        "created_at": str(task.get("created_at") or ""),
        "updated_at": str(task.get("updated_at") or ""),
        "cursor": task_cursor(task),
    }


def _json_object(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def infer_supplement_base_task_id(queue: Any, thread_id: str, message_id: str) -> str:
    """Recover supplement ownership from durable events when runtime payload is gone."""
    context = bridge_supplement_context_from_events(queue, message_id, thread_id)
    return str(context.get("base_task_id") or "")


class BridgeMcpService:
    def __init__(self, config_path: Path):
        self.config_path = config_path

    def load(self) -> tuple[dict[str, Any], Any]:
        return load_bridge_state(self.config_path)

    def instructions(self) -> str:
        return (
            "For mobile final-reply tasks, use bridge.get_pending_batch to read "
            "same-thread supplements before doing substantive work and again before "
            "the final reply; ack each consumed item with bridge.ack_message. "
            "bridge.poll_updates is only for external long-poll/update loops, not "
            "for final-reply supplement pickup."
        )

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "bridge.health",
                "description": "Return bridge, queue, and config health.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "bridge.poll_updates",
                "description": (
                    "Poll a thread for new mobile messages after a cursor; for external "
                    "long-poll/update loops only. Do not use this as the final-reply "
                    "supplement check; use bridge.get_pending_batch instead."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string"},
                        "cursor": {"type": "string"},
                    },
                    "required": ["thread_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "bridge.ack_message",
                "description": (
                    "Acknowledge one supplement item after it has been incorporated into "
                    "the current mobile final reply. Use with bridge.get_pending_batch."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string"},
                        "thread_id": {"type": "string"},
                    },
                    "required": ["message_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "bridge.get_pending_batch",
                "description": (
                    "Primary tool for mobile final-reply supplement pickup. Call this "
                    "with the active thread_id immediately after mobile_ack and again "
                    "before the final mobile_result; incorporate returned items and ack "
                    "each consumed message with bridge.ack_message."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string"},
                    },
                    "required": ["thread_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "bridge.publish_supplement",
                "description": "Publish supplement items for the current thread context.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string"},
                        "items": {"type": "array"},
                        "base_message_id": {"type": "string"},
                    },
                    "required": ["thread_id", "items"],
                    "additionalProperties": False,
                },
            },
        ]

    def _json_content(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False, indent=2),
                }
            ],
            "isError": False,
        }

    def _json_error(self, message: str) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": message}],
            "isError": True,
        }

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": self.instructions(),
        }

    def tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {"tools": self.tool_specs()}

    def tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "").strip()
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        result = self.dispatch_tool(name, arguments)
        if result.get("ok", True):
            return self._json_content(result)
        return self._json_error(json.dumps(result, ensure_ascii=False, indent=2))

    def tool_health(self, queue: Any) -> dict[str, Any]:
        """Return a read-only bridge health snapshot for MCP callers."""
        return {
            "ok": True,
            "bridge": queue.health(),
            "config_path": str(self.config_path),
            "task_count": len(queue.list_tasks(20)),
            "pending_count": len(queue.list_pending(20)),
            "active_count": len(queue.list_active_codex_delivery_tasks(20)),
        }

    def tool_poll_updates(self, queue: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Return task updates for external polling; final-reply supplements use get_pending_batch."""
        thread_id = str(params.get("thread_id") or "").strip()
        cursor = str(params.get("cursor") or "").strip()
        if not thread_id:
            return {"ok": False, "reason": "thread_id is required"}
        with queue.session() as db:
            rows = db.execute(
                """
                SELECT *
                FROM mobile_tasks
                WHERE codex_thread_id=?
                  AND (
                    created_at > ?
                    OR updated_at > ?
                  )
                ORDER BY created_at ASC, updated_at ASC
                LIMIT 50
                """,
                (thread_id, cursor or "", cursor or ""),
            ).fetchall()
        items = [task_snapshot(dict(row)) for row in rows]
        next_cursor = items[-1]["cursor"] if items else cursor
        return {
            "ok": True,
            "has_more": bool(items),
            "next_cursor": next_cursor,
            "items": items,
            "is_stale": False,
            "error": "",
        }

    def tool_ack_message(self, queue: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Acknowledge one valid supplement without completing final-reply owners."""
        message_id = str(params.get("message_id") or "").strip()
        thread_id = str(params.get("thread_id") or "").strip()
        if not message_id:
            return {"ok": False, "reason": "message_id is required"}
        task = queue.get_task(message_id)
        if not task:
            return {"ok": False, "reason": "message not found", "acked": False, "already_acked": False}
        if task_owns_final_reply(queue, message_id):
            queue.add_event(
                "local",
                "mcp_ack_ignored_for_result_owner",
                {
                    "thread_id": thread_id,
                    "reason": "message owns a final reply and cannot be acknowledged as MCP supplement",
                },
                message_id,
            )
            return {
                "ok": True,
                "acked": False,
                "already_acked": False,
                "ignored": True,
                "reason": "message is a final-reply owner, not a supplement",
                "next_cursor": task_cursor(task),
            }
        ack_key = f"mcp_ack:{message_id}"
        already = bool(queue.runtime_get(ack_key))
        supplement_payload: dict[str, Any] = {}
        raw_supplement = queue.runtime_get(f"bridge_supplement:{thread_id}") if thread_id else ""
        if raw_supplement:
            try:
                parsed = json.loads(str(raw_supplement))
                supplement_payload = parsed if isinstance(parsed, dict) else {}
            except Exception:
                supplement_payload = {}
        ack_context = bridge_supplement_context_from_payload(supplement_payload, message_id)
        if not ack_context:
            ack_context = bridge_supplement_context_from_events(queue, message_id, thread_id)
        base_message_id = str(ack_context.get("base_task_id") or "")
        if not base_message_id:
            base_message_id = infer_supplement_base_task_id(queue, thread_id, message_id)
        supplement_signature = str(ack_context.get("supplement_signature") or "")
        ack_probe = {
            "base_task_id": base_message_id,
            "thread_id": str(ack_context.get("thread_id") or thread_id),
            "supplement_signature": supplement_signature,
            "ack_source": "mcp_bridge_ack_message",
        }
        valid_base_id, _base_task = valid_mcp_ack_base_owner(queue, message_id, ack_probe)
        if not valid_base_id or not task_is_supplement_context(queue, message_id):
            queue.add_event(
                "local",
                "mcp_ack_ignored_invalid_supplement",
                {
                    "thread_id": thread_id,
                    "base_task_id": base_message_id,
                    "valid_base_id": valid_base_id,
                    "is_supplement_context": task_is_supplement_context(queue, message_id),
                    "reason": "MCP ack requires a valid final-reply owner and a real supplement message",
                },
                message_id,
            )
            return {
                "ok": True,
                "acked": False,
                "already_acked": already,
                "ignored": True,
                "reason": "message is not a valid MCP supplement for an active final-reply owner",
                "next_cursor": task_cursor(task),
            }
        base_message_id = valid_base_id
        if not already:
            queue.runtime_set(
                ack_key,
                json.dumps(
                    {
                        "acked_at": task_cursor(task),
                        "thread_id": str(ack_context.get("thread_id") or thread_id),
                        "base_task_id": base_message_id,
                        "supplement_signature": supplement_signature,
                        "runtime_signature": str(ack_context.get("runtime_signature") or ""),
                        "ack_context_source": str(ack_context.get("source") or ""),
                        "ack_source": "mcp_bridge_ack_message",
                    },
                    ensure_ascii=False,
                ),
            )
            queue.add_event(
                "local",
                "mcp_message_acked",
                {
                    "thread_id": str(ack_context.get("thread_id") or thread_id),
                    "base_task_id": base_message_id,
                    "supplement_signature": supplement_signature,
                    "runtime_signature": str(ack_context.get("runtime_signature") or ""),
                    "ack_context_source": str(ack_context.get("source") or ""),
                },
                message_id,
            )
        return {"ok": True, "acked": True, "already_acked": already, "next_cursor": task_cursor(task)}

    def tool_get_pending_batch(self, queue: Any, config: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        """Return runtime-validated supplement items for the active final-reply owner."""
        thread_id = str(params.get("thread_id") or "").strip()
        if not thread_id:
            return {"ok": False, "reason": "thread_id is required"}
        supplement = load_valid_supplement(queue, thread_id, config)
        supplement_payload = _json_object(supplement)
        supplement_task_ids = bridge_supplement_task_ids(supplement_payload) if supplement_payload else []
        items: list[dict[str, Any]] = []
        if supplement_task_ids:
            payload_items = supplement_payload.get("items") if isinstance(supplement_payload.get("items"), list) else []
            payload_item_by_id: dict[str, dict[str, Any]] = {}
            for item in payload_items:
                if not isinstance(item, dict):
                    continue
                message_id = str(item.get("message_id") or "")
                if not message_id:
                    continue
                payload_item_by_id.setdefault(message_id, item)
            supplement_task_ids = [task_id for task_id in supplement_task_ids if task_id in payload_item_by_id]
            placeholders = ",".join("?" for _ in supplement_task_ids)
            if placeholders:
                with queue.session() as db:
                    rows = db.execute(
                        f"""
                        SELECT *
                        FROM mobile_tasks
                        WHERE status='pending'
                          AND id IN ({placeholders})
                        """,
                        supplement_task_ids,
                    ).fetchall()
                rows_by_id = {str(row["id"] or ""): dict(row) for row in rows}
                for task_id in supplement_task_ids:
                    row = rows_by_id.get(task_id)
                    if not row:
                        continue
                    snapshot = task_snapshot(row)
                    payload_item = payload_item_by_id.get(task_id) or {}
                    snapshot["thread_id"] = thread_id
                    payload_cursor = str(payload_item.get("cursor") or "")
                    if payload_cursor:
                        snapshot["cursor"] = payload_cursor
                    items.append(snapshot)
        has_attachment = any(bool(item.get("attachments")) for item in items)
        return {
            "ok": True,
            "batch_id": thread_id,
            "items": items,
            "count": len(items),
            "batch_kind": "thread_pending",
            "has_supplement": bool(items or supplement),
            "has_attachment": has_attachment,
            "has_new_supplement": bool(items or supplement),
            "supplement": supplement,
        }

    def tool_publish_supplement(self, queue: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Publish supplement items into the bridge runtime payload."""
        thread_id = str(params.get("thread_id") or "").strip()
        items = params.get("items") if isinstance(params.get("items"), list) else []
        base_message_id = str(params.get("base_message_id") or "").strip()
        if not thread_id:
            return {"ok": False, "reason": "thread_id is required"}
        if not items:
            return {"ok": False, "reason": "items are required"}
        if not base_message_id:
            with queue.session() as db:
                rows = db.execute(
                    """
                    SELECT id
                    FROM mobile_tasks
                    WHERE codex_thread_id=? AND status IN ('sent_to_codex','processing')
                    ORDER BY created_at ASC, updated_at ASC
                    LIMIT 20
                    """,
                    (thread_id,),
                ).fetchall()
            for row in rows:
                candidate_id = str(row["id"] or "")
                if candidate_id and task_owns_final_reply(queue, candidate_id):
                    base_message_id = candidate_id
                    break
        normalized_items: list[dict[str, Any]] = []
        cursor_values: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = {
                "message_id": str(item.get("message_id") or ""),
                "kind": str(item.get("kind") or "text"),
                "text": str(item.get("text") or ""),
                "attachments": item.get("attachments") if isinstance(item.get("attachments"), list) else [],
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
                "source_user": str(item.get("source_user") or ""),
                "thread_id": str(item.get("thread_id") or thread_id),
                "cursor": str(item.get("cursor") or item.get("updated_at") or item.get("created_at") or ""),
            }
            normalized_items.append(normalized)
            if normalized["cursor"]:
                cursor_values.append(normalized["cursor"])
        supplement_signature = "|".join(cursor_values)
        payload = {
            "base_message_id": base_message_id,
            "thread_id": thread_id,
            "items": normalized_items,
            "published_at": task_cursor(normalized_items[-1]) if normalized_items else "",
            "supplement_signature": supplement_signature,
            "supplement_source": "mcp_publish_supplement",
        }
        merge_result = merge_bridge_supplement_payload(queue, payload, "mcp_publish_supplement")
        if not merge_result.get("ok"):
            queue.add_event("local", "mcp_supplement_publish_failed", {**payload, **merge_result})
            return {
                "ok": False,
                "published": False,
                "reason": str(merge_result.get("reason") or "supplement merge failed"),
                "supplement_signature": supplement_signature,
            }
        runtime_payload = merge_result.get("payload") if isinstance(merge_result.get("payload"), dict) else payload
        queue.add_event("local", "mcp_supplement_published", runtime_payload)
        return {
            "ok": True,
            "published": bool(merge_result.get("published")),
            "duplicate": bool(merge_result.get("duplicate")),
            "cursor": cursor_values[-1] if cursor_values else "",
            "prompt_patch": True,
            "supplement_signature": supplement_signature,
            "runtime_signature": str(merge_result.get("signature") or ""),
        }

    def dispatch_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        config, queue = self.load()
        if name == "bridge.health":
            return self.tool_health(queue)

        if name == "bridge.poll_updates":
            return self.tool_poll_updates(queue, params)

        if name == "bridge.ack_message":
            return self.tool_ack_message(queue, params)

        if name == "bridge.get_pending_batch":
            return self.tool_get_pending_batch(queue, config, params)

        if name == "bridge.publish_supplement":
            return self.tool_publish_supplement(queue, params)

        return {"ok": False, "reason": f"unknown tool: {name}"}


class MCPHTTPHandler(BaseHTTPRequestHandler):
    server_version = "mobile-openclaw-mcp-http/0.2"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/health":
            service = self.server.service
            payload = service.dispatch_tool("bridge.health", {})
            payload["transport"] = "http"
            self._send_json(payload)
            return
        self._send_json({"ok": False, "reason": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/rpc":
            self._send_json({"ok": False, "reason": "not found"}, status=404)
            return
        payload = self._read_json()
        method = str(payload.get("method") or "")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        request_id = payload.get("id")
        try:
            if method == "initialize":
                result = self.server.service.initialize(params)
            elif method == "tools/list":
                result = self.server.service.tools_list(params)
            elif method == "tools/call":
                result = self.server.service.tools_call(params)
            elif method in {"ping", "notifications/initialized"}:
                result = {}
            else:
                raise ValueError(f"unknown method: {method}")
            self._send_json({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:
            self._send_json(
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}},
                status=500,
            )


class MCPHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], service: BridgeMcpService):
        super().__init__(address, MCPHTTPHandler)
        self.service = service


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mobile OpenClaw MCP bridge server")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18795)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def stale_supplement_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-mcp-stale-supp-") as temp_root:
        temp = Path(temp_root)
        db_path = temp / "queue.db"
        config_path = temp / "config.json"
        config = {"queue": {"db_path": str(db_path)}}
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        queue = MobileQueue(db_path)
        now = utc_now()
        base_id = "base-done"
        supp_id = "supp-done"
        thread_id = "thread-1"
        with queue.session() as db:
            for task_id, status, text in [
                (base_id, "pushed_to_wecom", "old completed owner"),
                (supp_id, "done", "old completed supplement"),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        "",
                        task_id,
                        "L1",
                        status,
                        "[supplement] consumed" if task_id == supp_id else "done",
                        "pushed_to_wecom" if task_id == base_id else "",
                        "backup1",
                        thread_id,
                        "{}",
                        now,
                        now,
                    ),
                )
        queue.runtime_set(
            bridge_supplement_key(thread_id),
            json.dumps(
                {
                    "base_message_id": base_id,
                    "active_task_id": base_id,
                    "thread_id": thread_id,
                    "items": [
                        {
                            "message_id": supp_id,
                            "kind": "text",
                            "text": "old completed supplement",
                            "thread_id": thread_id,
                            "cursor": now,
                        }
                    ],
                    "published_at": now,
                    "supplement_signature": "stale",
                },
                ensure_ascii=False,
            ),
        )
        service = BridgeMcpService(config_path)
        result = service.dispatch_tool("bridge.get_pending_batch", {"thread_id": thread_id})
        runtime_after = queue.runtime_get(bridge_supplement_key(thread_id))
        with queue.session() as db:
            event_count = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE event_type='mcp_stale_supplement_released'
                """
            ).fetchone()
        ok = bool(
            result.get("ok")
            and not result.get("has_supplement")
            and not result.get("supplement")
            and not runtime_after
            and int(event_count["n"] if event_count else 0) >= 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "runtime_after": runtime_after,
            "release_event_count": int(event_count["n"] if event_count else 0),
            "assertion": "MCP get_pending_batch drops stale completed bridge_supplement payloads before Codex can consume them",
        }


def orphan_supplement_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-mcp-orphan-supp-") as temp_root:
        temp = Path(temp_root)
        db_path = temp / "queue.db"
        config_path = temp / "config.json"
        config = {"queue": {"db_path": str(db_path)}}
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        queue = MobileQueue(db_path)
        now = utc_now()
        base_id = "base-invalid"
        supp_id = "supp-pending"
        thread_id = "thread-1"
        with queue.session() as db:
            for task_id, status, text in [
                (base_id, "done", "finished non-owner"),
                (supp_id, "pending", "pending supplement"),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        "",
                        task_id,
                        "L1",
                        status,
                        "",
                        "",
                        "backup1",
                        thread_id,
                        "{}",
                        now,
                        now,
                    ),
                )
        queue.runtime_set(
            bridge_supplement_key(thread_id),
            json.dumps(
                {
                    "base_message_id": base_id,
                    "active_task_id": base_id,
                    "thread_id": thread_id,
                    "items": [
                        {
                            "message_id": supp_id,
                            "kind": "text",
                            "text": "pending supplement",
                            "thread_id": thread_id,
                            "cursor": now,
                        }
                    ],
                    "published_at": now,
                    "supplement_signature": "orphan",
                },
                ensure_ascii=False,
            ),
        )
        service = BridgeMcpService(config_path)
        result = service.dispatch_tool("bridge.get_pending_batch", {"thread_id": thread_id})
        runtime_after = queue.runtime_get(bridge_supplement_key(thread_id))
        with queue.session() as db:
            event_count = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE event_type='mcp_supplement_not_ready_preserved'
                """
            ).fetchone()
        ok = bool(
            result.get("ok")
            and not result.get("has_supplement")
            and not result.get("supplement")
            and not result.get("items")
            and runtime_after
            and int(event_count["n"] if event_count else 0) >= 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "runtime_after_present": bool(runtime_after),
            "preserved_event_count": int(event_count["n"] if event_count else 0),
            "assertion": "MCP get_pending_batch preserves pending supplements with invalid hosts without exposing them as ordinary pending work",
        }


def finished_owner_orphan_promotion_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-mcp-finished-owner-supp-") as temp_root:
        temp = Path(temp_root)
        db_path = temp / "queue.db"
        config_path = temp / "config.json"
        config = {"queue": {"db_path": str(db_path)}, "trigger": {"supplement_ack_grace_seconds": 10}}
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        queue = MobileQueue(db_path)
        old = "2000-01-01T00:00:00+00:00"
        thread_id = "thread-1"
        base_id = "base-finished"
        first_id = "supp-first"
        second_id = "supp-second"
        with queue.session() as db:
            for task_id, status, text, result in [
                (base_id, "done", "finished base", "base final result"),
                (first_id, "pending", "first pending supplement", ""),
                (second_id, "pending", "second pending supplement", ""),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        "",
                        task_id,
                        "L1",
                        status,
                        result,
                        "",
                        "primary",
                        thread_id,
                        "{}",
                        old,
                        old,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-base",
                "client_message_id": "batch-base",
                "expected_task_ids": [base_id],
            },
            base_id,
        )
        payload = {
            "base_message_id": base_id,
            "active_task_id": base_id,
            "thread_id": thread_id,
            "items": [
                {"message_id": first_id, "kind": "text", "text": "first pending supplement", "thread_id": thread_id, "cursor": old},
                {"message_id": second_id, "kind": "text", "text": "second pending supplement", "thread_id": thread_id, "cursor": old},
            ],
            "published_at": old,
            "supplement_signature": "finished-owner",
        }
        queue.runtime_set(bridge_supplement_key(thread_id), json.dumps(payload, ensure_ascii=False))
        for task_id in [first_id, second_id]:
            queue.add_event(
                "local",
                "attachment_supplement_pending_published",
                {"active_task_id": base_id, "thread_id": thread_id, "signature": "finished-owner"},
                task_id,
            )
        service = BridgeMcpService(config_path)
        result = service.dispatch_tool("bridge.get_pending_batch", {"thread_id": thread_id})
        runtime_after = json.loads(str(queue.runtime_get(bridge_supplement_key(thread_id)) or "{}") or "{}")
        with queue.session() as db:
            events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id IN (?,?)
                    GROUP BY event_type
                    """,
                    (first_id, second_id),
                ).fetchall()
            }
        ok = bool(
            result.get("ok")
            and not result.get("items")
            and not result.get("has_supplement")
            and bridge_supplement_base_task_id(runtime_after) == first_id
            and bridge_supplement_task_ids(runtime_after) == [second_id]
            and not task_is_supplement_context(queue, first_id)
            and task_is_supplement_context(queue, second_id)
            and events.get("supplement_promoted_to_owner") == 1
            and events.get("supplement_rebased_to_promoted_owner") == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "runtime_base_task_id": bridge_supplement_base_task_id(runtime_after),
            "runtime_task_ids": bridge_supplement_task_ids(runtime_after),
            "first_is_supplement": task_is_supplement_context(queue, first_id),
            "second_is_supplement": task_is_supplement_context(queue, second_id),
            "events": events,
            "assertion": "MCP get_pending_batch promotes finished-base orphan supplements instead of returning them as current-turn supplements",
        }


def recently_finished_owner_supplement_pickup_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-mcp-recent-finished-supp-") as temp_root:
        temp = Path(temp_root)
        db_path = temp / "queue.db"
        config_path = temp / "config.json"
        config = {"queue": {"db_path": str(db_path)}, "trigger": {"supplement_ack_grace_seconds": 10}}
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        queue = MobileQueue(db_path)
        old = "2000-01-01T00:00:00+00:00"
        now = utc_now()
        thread_id = "thread-1"
        base_id = "base-recently-finished"
        supplement_id = "supp-recent-pickup"
        with queue.session() as db:
            for task_id, status, text, result, updated_at, completed_at in [
                (base_id, "done", "recent base", "base final result", now, now),
                (supplement_id, "pending", "pending supplement", "", old, None),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at, completed_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        "",
                        task_id,
                        "L1",
                        status,
                        result,
                        "",
                        "primary",
                        thread_id,
                        "{}",
                        old,
                        updated_at,
                        completed_at,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-base",
                "client_message_id": "batch-base",
                "expected_task_ids": [base_id],
            },
            base_id,
        )
        payload = {
            "base_message_id": base_id,
            "active_task_id": base_id,
            "thread_id": thread_id,
            "items": [
                {"message_id": supplement_id, "kind": "text", "text": "pending supplement", "thread_id": thread_id, "cursor": old}
            ],
            "published_at": old,
            "supplement_signature": "recently-finished-owner",
        }
        queue.runtime_set(bridge_supplement_key(thread_id), json.dumps(payload, ensure_ascii=False))
        service = BridgeMcpService(config_path)
        result = service.dispatch_tool("bridge.get_pending_batch", {"thread_id": thread_id})
        runtime_after = json.loads(str(queue.runtime_get(bridge_supplement_key(thread_id)) or "{}") or "{}")
        item_ids = [str(item.get("message_id") or "") for item in result.get("items") or [] if isinstance(item, dict)]
        ok = bool(
            result.get("ok")
            and item_ids == [supplement_id]
            and result.get("has_supplement")
            and bridge_supplement_base_task_id(runtime_after) == base_id
            and bridge_supplement_task_ids(runtime_after) == [supplement_id]
            and not task_event_exists(queue, supplement_id, "supplement_promoted_to_owner")
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "runtime_base_task_id": bridge_supplement_base_task_id(runtime_after),
            "runtime_task_ids": bridge_supplement_task_ids(runtime_after),
            "assertion": "MCP get_pending_batch still exposes supplements during the owner post-completion pickup window instead of promoting them immediately",
        }


def owner_not_supplement_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-mcp-owner-not-supp-") as temp_root:
        temp = Path(temp_root)
        db_path = temp / "queue.db"
        config_path = temp / "config.json"
        config = {"queue": {"db_path": str(db_path)}}
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        queue = MobileQueue(db_path)
        now = utc_now()
        base_id = "base-owner"
        owner_id = "released-owner"
        thread_id = "thread-1"
        with queue.session() as db:
            for task_id, status, text in [
                (base_id, "sent_to_codex", "active base owner"),
                (owner_id, "pending", "released owner should not be supplement"),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        "",
                        task_id,
                        "L1",
                        status,
                        "",
                        "",
                        "primary",
                        thread_id,
                        "{}",
                        now,
                        now,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-base-owner",
                "client_message_id": "batch-base-owner",
                "expected_task_ids": [base_id],
            },
            base_id,
        )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-released-owner",
                "client_message_id": "batch-released-owner",
                "expected_task_ids": [owner_id],
            },
            owner_id,
        )
        queue.runtime_set(
            bridge_supplement_key(thread_id),
            json.dumps(
                {
                    "base_message_id": base_id,
                    "active_task_id": base_id,
                    "thread_id": thread_id,
                    "items": [
                        {
                            "message_id": owner_id,
                            "kind": "text",
                            "text": "released owner should not be supplement",
                            "thread_id": thread_id,
                            "cursor": now,
                        }
                    ],
                    "published_at": now,
                    "supplement_signature": "owner-not-supplement",
                },
                ensure_ascii=False,
            ),
        )
        service = BridgeMcpService(config_path)
        batch = service.dispatch_tool("bridge.get_pending_batch", {"thread_id": thread_id})
        ack = service.dispatch_tool("bridge.ack_message", {"thread_id": thread_id, "message_id": owner_id})
        runtime_after = queue.runtime_get(bridge_supplement_key(thread_id))
        ack_runtime = queue.runtime_get(f"mcp_ack:{owner_id}")
        with queue.session() as db:
            ignored = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='mcp_ack_ignored_for_result_owner'
                """,
                (owner_id,),
            ).fetchone()
        ok = bool(
            batch.get("ok")
            and not batch.get("items")
            and not batch.get("has_supplement")
            and not runtime_after
            and ack.get("ok")
            and ack.get("ignored")
            and not ack.get("acked")
            and not ack_runtime
            and int(ignored["n"] if ignored else 0) >= 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "batch": batch,
            "ack": ack,
            "runtime_after_present": bool(runtime_after),
            "ack_runtime_present": bool(ack_runtime),
            "ignored_event_count": int(ignored["n"] if ignored else 0),
            "assertion": "MCP never exposes or acknowledges a final-reply owner as supplement context",
        }


def runtime_merge_ack_attribution_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-mcp-merge-ack-") as temp_root:
        temp = Path(temp_root)
        db_path = temp / "queue.db"
        config_path = temp / "config.json"
        config = {"queue": {"db_path": str(db_path)}}
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        queue = MobileQueue(db_path)
        service = BridgeMcpService(config_path)
        thread_id = "thread-1"
        base_id = "base-owner"
        second_id = "supp-second"
        third_id = "supp-third"
        now = utc_now()
        with queue.session() as db:
            for index, (task_id, status, text) in enumerate(
                [
                    (base_id, "sent_to_codex", "base prompt"),
                    (second_id, "pending", "second supplement"),
                    (third_id, "pending", "third supplement"),
                ]
            ):
                created = f"2026-01-01T00:00:0{index}+00:00"
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        "",
                        task_id,
                        "L1",
                        status,
                        "",
                        "",
                        "primary",
                        thread_id,
                        "{}",
                        created,
                        created,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-base-owner",
                "client_message_id": "batch-base-owner",
                "expected_task_ids": [base_id],
            },
            base_id,
        )

        publish_second = service.dispatch_tool(
            "bridge.publish_supplement",
            {
                "thread_id": thread_id,
                "base_message_id": base_id,
                "items": [
                    {
                        "message_id": second_id,
                        "kind": "text",
                        "text": "second supplement",
                        "created_at": "2026-01-01T00:00:01+00:00",
                        "updated_at": "2026-01-01T00:00:01+00:00",
                        "thread_id": thread_id,
                        "cursor": "cursor-second",
                    }
                ],
            },
        )
        publish_third = service.dispatch_tool(
            "bridge.publish_supplement",
            {
                "thread_id": thread_id,
                "base_message_id": base_id,
                "items": [
                    {
                        "message_id": third_id,
                        "kind": "text",
                        "text": "third supplement",
                        "created_at": "2026-01-01T00:00:02+00:00",
                        "updated_at": "2026-01-01T00:00:02+00:00",
                        "thread_id": thread_id,
                        "cursor": "cursor-third",
                    }
                ],
            },
        )
        runtime_after_publish = json.loads(str(queue.runtime_get(bridge_supplement_key(thread_id)) or "{}") or "{}")
        batch = service.dispatch_tool("bridge.get_pending_batch", {"thread_id": thread_id})
        ack_second = service.dispatch_tool("bridge.ack_message", {"thread_id": thread_id, "message_id": second_id})
        ack_second_payload = mcp_ack_payload(queue, second_id)
        batch_after_second_ack = service.dispatch_tool("bridge.get_pending_batch", {"thread_id": thread_id})
        ack_third = service.dispatch_tool("bridge.ack_message", {"thread_id": thread_id, "message_id": third_id})
        ack_third_payload = mcp_ack_payload(queue, third_id)
        completed = process_mcp_acked_pending_supplements(queue)
        second_task = queue.get_task(second_id) or {}
        third_task = queue.get_task(third_id) or {}
        runtime_after_complete = str(queue.runtime_get(bridge_supplement_key(thread_id)) or "")
        item_signature_map = runtime_after_publish.get("item_supplement_signatures") if isinstance(runtime_after_publish, dict) else {}
        ok = bool(
            publish_second.get("ok")
            and publish_second.get("published")
            and publish_third.get("ok")
            and publish_third.get("published")
            and bridge_supplement_task_ids(runtime_after_publish) == [second_id, third_id]
            and isinstance(item_signature_map, dict)
            and str(item_signature_map.get(second_id) or "") == "cursor-second"
            and str(item_signature_map.get(third_id) or "") == "cursor-third"
            and [str(item.get("message_id") or "") for item in batch.get("items", [])] == [second_id, third_id]
            and ack_second.get("acked")
            and str(ack_second_payload.get("base_task_id") or "") == base_id
            and str(ack_second_payload.get("supplement_signature") or "") == "cursor-second"
            and [str(item.get("message_id") or "") for item in batch_after_second_ack.get("items", [])] == [third_id]
            and ack_third.get("acked")
            and str(ack_third_payload.get("base_task_id") or "") == base_id
            and str(ack_third_payload.get("supplement_signature") or "") == "cursor-third"
            and completed.get("completed_count") == 2
            and second_task.get("status") == "done"
            and third_task.get("status") == "done"
            and str(second_task.get("result") or "").startswith("[supplement]")
            and str(third_task.get("result") or "").startswith("[supplement]")
        )
        return {
            "ok": ok,
            "temp_only": True,
            "now": now,
            "publish_second": publish_second,
            "publish_third": publish_third,
            "runtime_task_ids": bridge_supplement_task_ids(runtime_after_publish),
            "item_signature_map": item_signature_map,
            "batch_item_ids": [str(item.get("message_id") or "") for item in batch.get("items", [])],
            "batch_after_second_ack_item_ids": [
                str(item.get("message_id") or "") for item in batch_after_second_ack.get("items", [])
            ],
            "ack_second": ack_second_payload,
            "ack_third": ack_third_payload,
            "completed": completed,
            "runtime_after_complete_present": bool(runtime_after_complete),
            "assertion": "separate supplement publishes merge FIFO and ack attribution stays per message id",
        }


def pending_batch_runtime_thread_items_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-mcp-runtime-thread-items-") as temp_root:
        temp = Path(temp_root)
        db_path = temp / "queue.db"
        config_path = temp / "config.json"
        config = {"queue": {"db_path": str(db_path)}}
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        queue = MobileQueue(db_path)
        service = BridgeMcpService(config_path)
        thread_id = "thread-1"
        base_id = "base-owner"
        empty_thread_id = "supp-empty-thread"
        stale_thread_id = "supp-stale-thread"
        now = utc_now()
        with queue.session() as db:
            for task_id, status, text, task_thread_id in [
                (base_id, "sent_to_codex", "base prompt", thread_id),
                (empty_thread_id, "pending", "supplement with empty DB thread", ""),
                (stale_thread_id, "pending", "supplement with stale DB thread", "stale-thread"),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        "",
                        task_id,
                        "L1",
                        status,
                        "",
                        "",
                        "primary",
                        task_thread_id,
                        "{}",
                        now,
                        now,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-base-owner",
                "client_message_id": "batch-base-owner",
                "expected_task_ids": [base_id],
            },
            base_id,
        )
        queue.runtime_set(
            bridge_supplement_key(thread_id),
            json.dumps(
                {
                    "base_message_id": base_id,
                    "active_task_id": base_id,
                    "thread_id": thread_id,
                    "items": [
                        {
                            "message_id": empty_thread_id,
                            "kind": "text",
                            "text": "supplement with empty DB thread",
                            "thread_id": thread_id,
                            "cursor": "cursor-empty",
                        },
                        {
                            "message_id": stale_thread_id,
                            "kind": "text",
                            "text": "supplement with stale DB thread",
                            "thread_id": thread_id,
                            "cursor": "cursor-stale",
                        },
                    ],
                    "published_at": now,
                    "supplement_signature": "runtime-thread-items",
                    "item_supplement_signatures": {
                        empty_thread_id: "cursor-empty",
                        stale_thread_id: "cursor-stale",
                    },
                },
                ensure_ascii=False,
            ),
        )

        batch = service.dispatch_tool("bridge.get_pending_batch", {"thread_id": thread_id})
        ack_empty = service.dispatch_tool("bridge.ack_message", {"thread_id": thread_id, "message_id": empty_thread_id})
        ack_stale = service.dispatch_tool("bridge.ack_message", {"thread_id": thread_id, "message_id": stale_thread_id})
        completed = process_mcp_acked_pending_supplements(queue)
        item_ids = [str(item.get("message_id") or "") for item in batch.get("items", [])]
        item_threads = [str(item.get("thread_id") or "") for item in batch.get("items", [])]
        ok = bool(
            batch.get("ok")
            and item_ids == [empty_thread_id, stale_thread_id]
            and item_threads == [thread_id, thread_id]
            and ack_empty.get("acked")
            and ack_stale.get("acked")
            and completed.get("completed_count") == 2
        )
        return {
            "ok": ok,
            "temp_only": True,
            "batch_item_ids": item_ids,
            "batch_item_threads": item_threads,
            "ack_empty": ack_empty,
            "ack_stale": ack_stale,
            "completed": completed,
            "assertion": "MCP get_pending_batch returns runtime-validated supplement items even when DB codex_thread_id is empty or stale",
        }


def run_self_tests() -> dict[str, Any]:
    tests = {
        "stale_supplement_cleanup": stale_supplement_self_test(),
        "orphan_supplement_preserve": orphan_supplement_self_test(),
        "finished_owner_orphan_promotion": finished_owner_orphan_promotion_self_test(),
        "recently_finished_owner_supplement_pickup": recently_finished_owner_supplement_pickup_self_test(),
        "owner_not_supplement": owner_not_supplement_self_test(),
        "runtime_merge_ack_attribution": runtime_merge_ack_attribution_self_test(),
        "pending_batch_runtime_thread_items": pending_batch_runtime_thread_items_self_test(),
    }
    return {
        "ok": all(bool(result.get("ok")) for result in tests.values()),
        "temp_only": True,
        "tests": tests,
    }


def stdio_loop(service: BridgeMcpService) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    print(
        json.dumps(
            {
                "ok": True,
                "transport": "stdio",
                "server": SERVER_NAME,
                "version": SERVER_VERSION,
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
        flush=True,
    )
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        method = str(payload.get("method") or "")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        request_id = payload.get("id", None)
        if method in {"notifications/initialized"}:
            continue
        try:
            if method == "initialize":
                result = service.initialize(params)
            elif method == "tools/list":
                result = service.tools_list(params)
            elif method == "tools/call":
                result = service.tools_call(params)
            elif method == "ping":
                result = {}
            elif method == "shutdown":
                result = {}
            elif method == "exit":
                return 0
            else:
                raise ValueError(f"unknown method: {method}")
            if request_id is not None:
                print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False), flush=True)
        except Exception as exc:
            if request_id is not None:
                print(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test:
        print(json.dumps(run_self_tests(), ensure_ascii=False, indent=2))
        return 0
    service = BridgeMcpService(Path(args.config))
    if args.transport == "http":
        server = MCPHTTPServer((args.host, args.port), service)
        print(
            json.dumps(
                {"ok": True, "transport": "http", "host": args.host, "port": args.port, "config": str(args.config)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        return 0
    return stdio_loop(service)


if __name__ == "__main__":
    raise SystemExit(main())
