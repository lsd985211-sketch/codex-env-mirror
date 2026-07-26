#!/usr/bin/env python3
"""Local dashboard for the OpenClaw Weixin mobile bridge."""

from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
WECOM_BRIDGE = PROJECT_ROOT / "_bridge" / "mobile_wecom_bridge"
if str(WECOM_BRIDGE) not in sys.path:
    sys.path.insert(0, str(WECOM_BRIDGE))
BRIDGE_ROOT = PROJECT_ROOT / "_bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from mobile_queue import MobileQueue  # noqa: E402
import permission_policy  # noqa: E402
from resource_fetcher import acquire_bytes_resource, append_resource_log  # noqa: E402
from dashboard_state import load_state as load_dashboard_state  # noqa: E402
from dashboard_weixin_delivery import send_dashboard_weixin_direct  # noqa: E402
from audio_toolkit.audio_toolkit import default_work_root  # noqa: E402
from shared.windows_runtime_assets import (  # noqa: E402
    openclaw_install_root,
    openclaw_node_path,
    openclaw_state_path,
)

DEFAULT_DB = ROOT / "mobile_openclaw_bridge.db"
DEFAULT_CONFIG = ROOT / "config.local.json"
DEFAULT_LIVE_STATE = ROOT / "runtime" / "dashboard_live_state.json"
DEFAULT_DASHBOARD_ACTIVITY = ROOT / "runtime" / "dashboard_activity.json"
DEFAULT_ATTACHMENTS_DIR = ROOT / "attachments" / "dashboard"
DEFAULT_AUDIO_TOOLKIT = PROJECT_ROOT / "_bridge" / "audio_toolkit" / "audio_toolkit.py"
DEFAULT_LOGIN_HOST = "127.0.0.1"
DEFAULT_LOGIN_PORT = 18790
OPENCLAW_BASE = openclaw_install_root()
OPENCLAW_HOME = OPENCLAW_BASE / "home"
OPENCLAW_STATE_DIR = OPENCLAW_BASE / "state"
DEFAULT_LOGIN_SCRIPT = OPENCLAW_BASE / "login-artifacts" / "weixin-login-slot-server.mjs"
DEFAULT_LOGIN_RUNS = OPENCLAW_BASE / "login-runs"
DEFAULT_LOGIN_TIMEOUT_MS = 480000
DEFAULT_LOGIN_START_WAIT_SECONDS = 10.0
DEFAULT_NODE_CANDIDATES = [
    openclaw_node_path(),
    Path("C:/Program Files/nodejs/node.exe"),
    "node",
]
RESOURCE_LOG = ROOT / "logs" / "resource-fetcher.jsonl"
SENSITIVE_KEY_PARTS = ("token", "secret", "password", "cookie", "authorization", "context")
ACTIVE_STATUSES = {"pending", "claimed", "queued_for_codex", "sent_to_codex", "processing", "waiting_confirmation"}
RETRYABLE_STATUSES = {"pending", "failed", "push_failed", "codex_timeout"}
CANCELLABLE_STATUSES = {"pending", "queued_for_codex", "sent_to_codex", "processing"}
PLACEHOLDER_EXTERNAL_USERS = {"", "unknown", "unknown@im.wechat"}
MAX_JSON_BODY_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
ASR_TIMEOUT_SECONDS = 300
AUDIO_TRANSCRIBE_EXTENSIONS = {
    ".aac",
    ".amr",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".weba",
    ".webm",
    ".wma",
}
LOGIN_PROXY_MAX_BODY_BYTES = 2 * 1024 * 1024
LOGIN_PROXY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json_file_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        tmp.replace(path)
    except PermissionError:
        shutil.copyfile(tmp, path)
        tmp.unlink(missing_ok=True)


def queue_from_config(config: dict[str, Any], db_path: Path) -> MobileQueue:
    queue_config = dict(config)
    queue_config.setdefault("queue", {})
    queue_config["queue"] = dict(queue_config.get("queue") or {})
    queue_config["queue"]["db_path"] = str(db_path)
    return MobileQueue(db_path, config=queue_config)


ACCOUNT_FILE_SUFFIXES = (".context-tokens.json", ".media-tokens.json")
FIXED_ACCOUNT_SLOTS = ("primary", "backup1", "backup2", "backup3", "backup4")


def openclaw_state_dir(config: dict[str, Any]) -> Path:
    return Path(
        config.get("openclaw", {}).get("state_dir")
        or openclaw_state_path()
    )


def permission_account_map(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    accounts_dir = openclaw_state_dir(config) / "openclaw-weixin" / "accounts"
    candidate_ids = list(FIXED_ACCOUNT_SLOTS)
    if accounts_dir.exists():
        for path in sorted(accounts_dir.glob("*.json")):
            if any(path.name.endswith(suffix) for suffix in ACCOUNT_FILE_SUFFIXES):
                continue
            if path.stem not in candidate_ids:
                candidate_ids.append(path.stem)
    result: dict[str, dict[str, str]] = {}
    for account_id in candidate_ids:
        try:
            payload = json.loads((accounts_dir / f"{account_id}.json").read_text(encoding="utf-8-sig"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        result[account_id] = {
            "user_id": str(payload.get("userId") or "").strip(),
            "token_present": "yes" if str(payload.get("token") or "").strip() else "no",
        }
    return result


def dashboard_permission_actor(config: dict[str, Any], account_map: dict[str, dict[str, str]]) -> str:
    """Dashboard actions are performed by the local admin operator, not by the target Weixin user."""
    return permission_policy.primary_admin_user(config, account_map)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_filename(value: str, fallback: str = "attachment") -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-()[] " else "_" for ch in str(value or "").strip())
    safe = safe.strip(" .")
    return safe[:120] or fallback


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    if length > MAX_JSON_BODY_BYTES:
        raise ValueError("request body too large")
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8-sig") or "{}")


def rewrite_login_html(body: bytes) -> bytes:
    text = body.decode("utf-8", errors="replace")
    replacements = {
        "'/api/": "'/login/api/",
        '"/api/': '"/login/api/',
        "href=\"/": "href=\"/login/",
        "src=\"/": "src=\"/login/",
        "action=\"/": "action=\"/login/",
        "qr.src = '/qr.png": "qr.src = '/login/qr.png",
        'qr.src = "/qr.png': 'qr.src = "/login/qr.png',
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    return text.encode("utf-8")


def content_type_is_html(headers: dict[str, str]) -> bool:
    return "text/html" in headers.get("content-type", "").lower()


def resolve_executable(candidates: list[Path | str]) -> str:
    for candidate in candidates:
        if isinstance(candidate, Path):
            if candidate.exists():
                return str(candidate)
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return ""


def is_local_host(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def tcp_port_open(host: str, port: int, timeout: float = 0.7) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def login_service_healthy(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        conn = http.client.HTTPConnection(host, int(port), timeout=timeout)
        conn.request("GET", "/api/state", headers={"Cache-Control": "no-store"})
        response = conn.getresponse()
        response.read()
        return 200 <= response.status < 500
    except Exception:
        return False
    finally:
        try:
            conn.close()  # type: ignore[possibly-undefined]
        except Exception:
            pass


def task_row(db: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT id, status, external_user, external_conversation, receiver_account_id,
               codex_thread_id, text, metadata_json, attachments_json
        FROM mobile_tasks
        WHERE id=?
        """,
        (task_id,),
    ).fetchone()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row else {}


def truncate(text: Any, limit: int = 240) -> str:
    value = "" if text is None else str(text)
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(part in lower for part in SENSITIVE_KEY_PARTS):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value[:50]]
    if isinstance(value, str):
        return truncate(value, 1000)
    return value


def parse_json_text(text: Any, default: Any) -> Any:
    if text is None or text == "":
        return default
    try:
        return json.loads(str(text))
    except Exception:
        return default


def is_placeholder_external_user(external_user: Any) -> bool:
    return str(external_user or "").strip().lower() in PLACEHOLDER_EXTERNAL_USERS


def mark_dashboard_activity(path: Path, source: str) -> None:
    try:
        now = datetime.now(timezone.utc)
        write_json_file_atomic(
            path,
            {
                "schema": "dashboard.activity.v1",
                "last_seen_at": now.isoformat(),
                "last_seen_epoch_ms": int(now.timestamp() * 1000),
                "source": source,
            },
        )
    except Exception as exc:
        try:
            write_json_file_atomic(
                path.with_name(f"{path.stem}.error.json"),
                {
                    "schema": "dashboard.activity.error.v1",
                    "source": source,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            pass


def load_live_state(path: Path) -> dict[str, Any]:
    data = load_json_file(path, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("ok", False)
    data.setdefault("mode", "codex-app-server-live-watch")
    data.setdefault("connected", False)
    data.setdefault("threads", {})
    data.setdefault("generated_at", "")
    data.setdefault("heartbeat_at", "")
    data.setdefault("last_error", "live state not available")
    data["path"] = str(path)
    generated_at = str(data.get("heartbeat_at") or data.get("generated_at") or "")
    try:
        generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - generated).total_seconds()
    except Exception:
        age_seconds = 999999.0
    data["age_seconds"] = round(age_seconds, 1)
    if age_seconds > 10:
        data["connected"] = False
        data["ok"] = False
        data["last_error"] = f"live state stale: {round(age_seconds, 1)}s old"
    return data


def load_task_runtime(db_path: Path, task_id: str) -> dict[str, str]:
    runtime: dict[str, str] = {}
    if not db_path.exists():
        return runtime
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        keys = [f"codex_turn:{task_id}", f"codex_batch:{task_id}", f"codex_expected_ids:{task_id}"]
        rows = db.execute(
            f"SELECT key, value FROM mobile_runtime WHERE key IN ({','.join('?' for _ in keys)})",
            keys,
        ).fetchall()
        for row in rows:
            runtime[str(row["key"])] = str(row["value"] or "")
    return runtime


def dashboard_enqueue(
    db_path: Path,
    config_path: Path,
    text: str,
    external_user: str,
    receiver_account_id: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = load_json_file(config_path, {})
    if not isinstance(config, dict):
        config = {}
    if not external_user or is_placeholder_external_user(external_user):
        return {"ok": False, "error": "请选择一个有效微信用户"}
    text = str(text or "").strip()
    if not text and not attachments:
        return {"ok": False, "error": "消息或附件不能为空"}
    queue = queue_from_config(config, db_path)
    account_map = permission_account_map(config)
    permission_actor = dashboard_permission_actor(config, account_map)
    decision = permission_policy.decide(config, permission_actor, "dashboard_send", receiver_account_id, account_map)
    if not decision.allowed:
        queue.add_event(
            "dashboard",
            "dashboard_permission_rejected",
            {
                "action": "dashboard_send",
                "dashboard_actor": permission_actor,
                "target_external_user": external_user,
                "receiver_account_id": receiver_account_id,
                "permission": decision.to_dict(),
            },
        )
        return {"ok": False, "error": f"权限不足：{decision.reason}", "permission": decision.to_dict()}
    metadata = {
        "msg_id": f"dashboard-web:{utc_now()}:{sha256_text(external_user + text)[:8]}",
        "transport": "dashboard-web",
        "receiver_account_id": receiver_account_id,
        "attachment_count": len(attachments or []),
        "dashboard_proxy_user": external_user,
    }
    result = queue.enqueue(
        text or "[attachment-message] Received attachment(s) from dashboard.",
        source="dashboard-web",
        external_user=external_user,
        external_conversation=external_user,
        metadata=metadata,
        attachments=attachments or [],
    )
    queue.add_event(
        "dashboard",
        "dashboard_send_enqueued",
        {"external_user": external_user, "receiver_account_id": receiver_account_id, "result": result},
        str(result.get("id") or ""),
    )
    result["ok"] = True
    return result


def dashboard_retry(db_path: Path, config_path: Path, task_id: str) -> dict[str, Any]:
    config = load_json_file(config_path, {})
    if not isinstance(config, dict):
        config = {}
    queue = queue_from_config(config, db_path)
    now = utc_now()
    with queue.session() as db:
        row = task_row(db, task_id)
        if not row:
            return {"ok": False, "error": "task not found"}
        account_map = permission_account_map(config)
        permission_actor = dashboard_permission_actor(config, account_map)
        decision = permission_policy.decide(
            config,
            permission_actor,
            "dashboard_retry",
            str(row["receiver_account_id"] or ""),
            account_map,
        )
        if not decision.allowed:
            queue.add_event(
                "dashboard",
                "dashboard_permission_rejected",
                {
                    "action": "dashboard_retry",
                    "dashboard_actor": permission_actor,
                    "target_external_user": str(row["external_user"] or ""),
                    "task_id": task_id,
                    "permission": decision.to_dict(),
                },
                task_id,
            )
            return {"ok": False, "error": f"权限不足：{decision.reason}", "permission": decision.to_dict()}
        status = str(row["status"] or "")
        if status == "pushed_to_wecom":
            return {"ok": False, "error": "已成功回发的任务不能原地重试，请复制为新任务"}
        if status not in RETRYABLE_STATUSES and status != "cancelled":
            return {"ok": False, "error": f"当前状态不适合手动重试：{status}"}
        db.execute(
            """
            UPDATE mobile_tasks
            SET status='pending', error='', push_status='', queued_for_codex_at=NULL,
                sent_to_codex_at=NULL, completed_at=NULL, pushed_at=NULL, updated_at=?
            WHERE id=?
            """,
            (now, task_id),
        )
    for key in (
        f"delivery_retry:{task_id}",
        f"thread_recovery:{task_id}",
        f"codex_turn:{task_id}",
        f"codex_batch:{task_id}",
        f"codex_expected_ids:{task_id}",
    ):
        queue.runtime_delete(key)
    queue.add_event("dashboard", "dashboard_manual_retry", {"retried_at": now}, task_id)
    return {"ok": True, "task_id": task_id, "status": "pending"}


def dashboard_notify_weixin(
    queue: MobileQueue,
    config: dict[str, Any],
    task: dict[str, Any],
    text: str,
    event_type: str,
) -> dict[str, Any]:
    try:
        from mobile_openclaw_cli import send_status_ack_sync  # type: ignore

        return send_status_ack_sync(queue, task, text, config, event_type)
    except Exception as exc:
        result = {"ok": False, "reason": str(exc)}
        queue.add_event("dashboard", f"{event_type}_failed", result, str(task.get("id") or ""))
        return result


def dashboard_send_to_weixin(
    db_path: Path,
    config_path: Path,
    text: str,
    external_user: str,
    receiver_account_id: str = "",
    attachments: list[dict[str, Any]] | None = None,
    record_to_chat: bool = False,
) -> dict[str, Any]:
    config = load_json_file(config_path, {})
    if not isinstance(config, dict):
        config = {}
    if not external_user or is_placeholder_external_user(external_user):
        return {"ok": False, "error": "请选择一个有效微信用户"}
    text = str(text or "").strip()
    attachments = [item for item in (attachments or []) if isinstance(item, dict)]
    if not text and not attachments:
        return {"ok": False, "error": "发送内容不能为空"}
    queue = queue_from_config(config, db_path)
    account_map = permission_account_map(config)
    permission_actor = dashboard_permission_actor(config, account_map)
    decision = permission_policy.decide(config, permission_actor, "dashboard_send_to_weixin", receiver_account_id, account_map)
    if not decision.allowed:
        queue.add_event(
            "dashboard",
            "dashboard_permission_rejected",
            {
                "action": "dashboard_send_to_weixin",
                "dashboard_actor": permission_actor,
                "target_external_user": external_user,
                "receiver_account_id": receiver_account_id,
                "permission": decision.to_dict(),
            },
        )
        return {"ok": False, "error": f"权限不足：{decision.reason}", "permission": decision.to_dict()}
    direct_id = f"dashboard-direct:{sha256_text(external_user + receiver_account_id + utc_now())[:16]}"
    return send_dashboard_weixin_direct(
        queue=queue,
        config=config,
        text=text,
        external_user=external_user,
        receiver_account_id=receiver_account_id,
        attachments=attachments,
        record_to_chat=record_to_chat,
        direct_id=direct_id,
    )


def dashboard_retry_with_notify(
    db_path: Path,
    config_path: Path,
    task_id: str,
    notify_weixin: bool = True,
) -> dict[str, Any]:
    config = load_json_file(config_path, {})
    if not isinstance(config, dict):
        config = {}
    queue = queue_from_config(config, db_path)
    task_before: dict[str, Any] = {}
    with queue.session() as db:
        task_before = row_dict(task_row(db, task_id))
    result = dashboard_retry(db_path, config_path, task_id)
    if result.get("ok") and notify_weixin and task_before:
        notice = "这条任务已由电脑端手动重试，正在重新排队处理。"
        result["notify"] = dashboard_notify_weixin(
            queue,
            config,
            task_before,
            notice,
            "dashboard_retry_notice_sent",
        )
    return result


def dashboard_cancel(db_path: Path, config_path: Path, task_id: str) -> dict[str, Any]:
    config = load_json_file(config_path, {})
    if not isinstance(config, dict):
        config = {}
    queue = queue_from_config(config, db_path)
    runtime = load_task_runtime(db_path, task_id)
    turn_id = runtime.get(f"codex_turn:{task_id}", "")
    cancel_result: dict[str, Any] = {"attempted": False}
    with queue.session() as db:
        row = task_row(db, task_id)
        if not row:
            return {"ok": False, "error": "task not found"}
        account_map = permission_account_map(config)
        permission_actor = dashboard_permission_actor(config, account_map)
        decision = permission_policy.decide(
            config,
            permission_actor,
            "dashboard_cancel",
            str(row["receiver_account_id"] or ""),
            account_map,
        )
        if not decision.allowed:
            queue.add_event(
                "dashboard",
                "dashboard_permission_rejected",
                {
                    "action": "dashboard_cancel",
                    "dashboard_actor": permission_actor,
                    "target_external_user": str(row["external_user"] or ""),
                    "task_id": task_id,
                    "permission": decision.to_dict(),
                },
                task_id,
            )
            return {"ok": False, "error": f"权限不足：{decision.reason}", "permission": decision.to_dict()}
        status = str(row["status"] or "")
        thread_id = str(row["codex_thread_id"] or "")
        if status not in CANCELLABLE_STATUSES:
            return {"ok": False, "error": f"当前状态不可撤回：{status}"}
    if turn_id:
        try:
            from mobile_openclaw_cli import cancel_codex_generation  # type: ignore

            cancel_result = cancel_codex_generation(config, thread_id, turn_id)
            cancel_result["attempted"] = True
        except Exception as exc:
            cancel_result = {"ok": False, "cancelled": False, "attempted": True, "reason": str(exc)}
        queue.add_event(
            "dashboard",
            "dashboard_cancel_codex_attempted",
            {"thread_id": thread_id, "turn_id": turn_id, "cancel": cancel_result},
            task_id,
        )
    ok, message = queue.cancel(task_id)
    for key in (
        f"delivery_retry:{task_id}",
        f"thread_recovery:{task_id}",
        f"codex_turn:{task_id}",
        f"codex_batch:{task_id}",
        f"codex_expected_ids:{task_id}",
    ):
        queue.runtime_delete(key)
    queue.add_event("dashboard", "dashboard_task_cancelled", {"message": message, "cancel": cancel_result}, task_id)
    return {"ok": ok, "task_id": task_id, "message": message, "cancel": cancel_result}


def dashboard_cancel_with_notify(
    db_path: Path,
    config_path: Path,
    task_id: str,
    notify_weixin: bool = True,
) -> dict[str, Any]:
    config = load_json_file(config_path, {})
    if not isinstance(config, dict):
        config = {}
    queue = queue_from_config(config, db_path)
    with queue.session() as db:
        task_before = row_dict(task_row(db, task_id))
    result = dashboard_cancel(db_path, config_path, task_id)
    if result.get("ok") and notify_weixin and task_before:
        notice = "这条任务已由电脑端撤回；如果 Codex 正在处理，系统已尝试只中止该任务对应的对话。"
        result["notify"] = dashboard_notify_weixin(
            queue,
            config,
            task_before,
            notice,
            "dashboard_cancel_notice_sent",
        )
    return result


def load_state(
    db_path: Path,
    config_path: Path,
    limit: int,
    task_id: str = "",
    include_events: bool = False,
    summary: bool = True,
) -> dict[str, Any]:
    return load_dashboard_state(
        db_path,
        config_path,
        limit,
        task_id=task_id,
        include_events=include_events,
        summary=summary,
    )


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>微信桥接对话</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --panel-soft: #fafaf8;
      --line: #e6e3dc;
      --line-strong: #d7d2c8;
      --text: #202124;
      --muted: #6f6b63;
      --muted-2: #918b82;
      --accent: #111111;
      --soft-hover: #f1f0ec;
      --selected: #ebe9e2;
      --ok: #1f7a45;
      --warn: #9a5b00;
      --bad: #b3261e;
      --bubble-user: #f3f2ee;
      --bubble-assistant: #ffffff;
      --shadow: 0 1px 2px rgba(20, 20, 20, 0.06);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      font-family: "Inter", "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      font-size: 14px;
      color: var(--text);
      background: var(--bg);
      letter-spacing: 0;
    }
    .app {
      height: 100vh;
      display: grid;
      grid-template-columns: 292px minmax(420px, 1fr) 360px;
      overflow: hidden;
    }
    aside, .conversation, .inspector {
      min-width: 0;
      min-height: 0;
      background: var(--panel);
    }
    aside {
      border-right: 1px solid var(--line);
      display: flex;
      flex-direction: column;
      background: var(--panel-soft);
    }
    .conversation {
      display: flex;
      flex-direction: column;
      background: var(--bg);
    }
    .inspector {
      border-left: 1px solid var(--line);
      display: flex;
      flex-direction: column;
    }
    .topbar {
      min-height: 52px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 0 14px;
      border-bottom: 1px solid var(--line);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 9px;
      min-width: 0;
      font-weight: 650;
    }
    .mark {
      width: 25px;
      height: 25px;
      border-radius: 7px;
      display: grid;
      place-items: center;
      background: #111;
      color: #fff;
      font-size: 13px;
      flex: none;
    }
    .title-main {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      display: flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
    }
    .dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--muted-2);
      display: inline-block;
    }
    .dot.ok { background: var(--ok); }
    .dot.warn { background: var(--warn); }
    .dot.bad { background: var(--bad); }
    .search {
      padding: 10px;
      border-bottom: 1px solid var(--line);
    }
    input {
      width: 100%;
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 10px;
      font: inherit;
      background: #fff;
      color: var(--text);
      outline: none;
    }
    input:focus { border-color: var(--line-strong); box-shadow: 0 0 0 2px rgba(0,0,0,0.03); }
    select {
      height: 32px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 8px;
      font: inherit;
      background: #fff;
      color: var(--text);
      outline: none;
    }
    select:focus { border-color: var(--line-strong); box-shadow: 0 0 0 2px rgba(0,0,0,0.03); }
    textarea {
      width: 100%;
      min-height: 52px;
      max-height: 160px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 9px;
      padding: 10px;
      font: inherit;
      line-height: 1.45;
      background: #fff;
      color: var(--text);
      outline: none;
    }
    textarea:focus { border-color: var(--line-strong); box-shadow: 0 0 0 2px rgba(0,0,0,0.03); }
    button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 8px;
      height: 32px;
      padding: 0 10px;
      font: inherit;
      cursor: pointer;
      white-space: nowrap;
    }
    button:hover { background: var(--soft-hover); }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    button.danger { color: var(--bad); border-color: #efc2bd; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .scroll { overflow: auto; min-height: 0; }
    .thread {
      margin: 4px 8px;
      padding: 9px 10px;
      border-radius: 9px;
      cursor: pointer;
      border: 1px solid transparent;
    }
    .thread:hover { background: var(--soft-hover); }
    .thread.selected {
      background: var(--selected);
      border-color: var(--line);
    }
    .thread-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-width: 0;
    }
    .thread-name {
      font-weight: 620;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .thread-preview {
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      overflow-wrap: anywhere;
    }
    .thread-foot {
      margin-top: 7px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: var(--muted-2);
      font-size: 11px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      height: 21px;
      padding: 0 7px;
      border-radius: 999px;
      background: #fff;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 11px;
      flex: none;
    }
    .pill.ok { color: var(--ok); border-color: #c8e1d1; background: #f5fbf7; }
    .pill.warn { color: var(--warn); border-color: #ead3a8; background: #fff9ed; }
    .pill.bad { color: var(--bad); border-color: #efc2bd; background: #fff5f3; }
    .chat-header {
      min-height: 58px;
      padding: 8px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.82);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .chat-title {
      min-width: 0;
    }
    .chat-title h1 {
      margin: 0;
      font-size: 16px;
      font-weight: 650;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .chat-title p {
      margin: 3px 0 0;
      color: var(--muted);
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .chat-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .chat {
      overflow: auto;
      min-height: 0;
      padding: 20px 24px 18px;
      contain: content;
    }
    .day {
      text-align: center;
      color: var(--muted-2);
      font-size: 11px;
      margin: 4px 0 16px;
    }
    .turn {
      max-width: 820px;
      margin: 0 auto 18px;
      display: grid;
      grid-template-columns: 32px minmax(0, 1fr);
      gap: 10px;
    }
    .avatar {
      width: 30px;
      height: 30px;
      border-radius: 9px;
      display: grid;
      place-items: center;
      font-size: 12px;
      font-weight: 650;
      color: #fff;
      background: #111;
      box-shadow: var(--shadow);
    }
    .avatar.user { background: #6b6258; }
    .bubble {
      border-radius: 12px;
      border: 1px solid var(--line);
      background: var(--bubble-assistant);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .bubble.user { background: var(--bubble-user); }
    .bubble-head {
      min-height: 32px;
      padding: 7px 11px 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
    }
    .bubble-body {
      padding: 7px 11px 11px;
      line-height: 1.58;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .bubble-tools {
      display: flex;
      justify-content: flex-end;
      flex-wrap: wrap;
      gap: 6px;
      padding: 0 9px 8px;
    }
    .bubble-tool {
      height: 24px;
      padding: 0 7px;
      border-radius: 7px;
      color: var(--muted);
      font-size: 11px;
    }
    .bubble-empty { color: var(--muted-2); font-style: italic; }
    .bubble.outbound {
      border-color: rgba(58, 114, 255, 0.22);
      background: linear-gradient(180deg, #ffffff 0%, #f7faff 100%);
    }
    .bubble.outbound .bubble-head span:first-child {
      color: var(--accent);
    }
    .bubble.outbound .bubble-body {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .task-row {
      max-width: 820px;
      margin: -4px auto 16px;
      padding-left: 42px;
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .task-chip {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 7px;
      padding: 5px 7px;
      color: var(--muted);
      font-size: 11px;
      cursor: pointer;
    }
    .task-chip.selected { color: var(--text); border-color: var(--line-strong); background: var(--selected); }
    .flow-panel {
      max-width: 820px;
      margin: -8px auto 16px;
      padding-left: 42px;
    }
    .flow-inner {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .flow-title {
      padding: 8px 11px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      border-bottom: 1px solid var(--line);
    }
    .flow-steps {
      padding: 8px 11px 10px;
      display: grid;
      gap: 7px;
    }
    .flow-step {
      display: grid;
      grid-template-columns: minmax(88px, 140px) 1fr;
      gap: 10px;
      color: var(--text);
      font-size: 12px;
      line-height: 1.45;
    }
    .flow-step time {
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }
    .flow-native-items {
      display: grid;
      gap: 6px;
      margin-top: 7px;
    }
    .flow-native-item {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      padding: 7px 8px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .flow-native-meta {
      margin-bottom: 3px;
      color: var(--muted);
      font-size: 11px;
    }
    .composer {
      border-top: 1px solid var(--line);
      background: rgba(255,255,255,0.9);
      padding: 10px 18px 12px;
    }
    .composer-inner {
      max-width: 862px;
      margin: 0 auto;
    }
    .composer-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 7px;
    }
    .composer-actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-top: 8px;
    }
    .composer-send-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex: none;
    }
    .composer-tool-actions {
      display: flex;
      align-items: center;
      gap: 7px;
      flex-wrap: wrap;
      min-width: 0;
    }
    button.voice-active {
      color: #fff;
      background: var(--bad);
      border-color: var(--bad);
    }
    .composer-record-toggle {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      user-select: none;
      white-space: nowrap;
    }
    .composer-record-toggle input {
      margin: 0;
      accent-color: var(--accent);
    }
    .attachment-list {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      min-width: 0;
    }
    .attachment-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      max-width: 280px;
      border: 1px solid var(--line);
      background: var(--panel-soft);
      border-radius: 8px;
      padding: 5px 7px;
      color: var(--muted);
      font-size: 12px;
    }
    .attachment-chip span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .inspector .topbar { background: var(--panel-soft); }
    .detail {
      padding: 14px;
      overflow: auto;
      min-height: 0;
    }
    .detail h2 {
      margin: 0 0 12px;
      font-size: 14px;
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .kv {
      display: grid;
      grid-template-columns: 92px minmax(0, 1fr);
      gap: 7px 9px;
      margin-bottom: 14px;
      font-size: 12px;
    }
    .kv div:nth-child(odd) { color: var(--muted); }
    .kv div:nth-child(even) { overflow-wrap: anywhere; }
    .section-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 620;
      margin: 14px 0 7px;
    }
    .detail-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin: 0 0 14px;
    }
    .pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      border: 1px solid var(--line);
      border-radius: 9px;
      padding: 10px;
      background: var(--panel-soft);
      line-height: 1.5;
      font-size: 12px;
    }
    .event {
      border: 1px solid var(--line);
      border-radius: 9px;
      padding: 8px;
      margin: 7px 0;
      background: #fff;
    }
    .event-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: var(--muted);
      font-size: 11px;
    }
    details.raw-event {
      margin-top: 8px;
    }
    details.raw-event summary {
      cursor: pointer;
      color: var(--muted);
      font-size: 11px;
    }
    .empty {
      color: var(--muted);
      padding: 22px 14px;
      text-align: center;
    }
    @media (max-width: 1080px) {
      .app { grid-template-columns: 260px minmax(360px, 1fr); }
      .inspector { display: none; }
    }
    @media (max-width: 760px) {
      .app { grid-template-columns: 1fr; }
      aside { display: none; }
      .chat { padding: 14px 12px 24px; }
      .turn { grid-template-columns: 28px minmax(0, 1fr); }
    }
  </style>
</head>
<body>
  <!-- 应用骨架：左侧对话列表 / 中间消息流与输入区 / 右侧任务细节与控制 -->
  <div class="app">
    <aside>
      <div class="topbar">
        <div class="brand"><div class="mark">C</div><div class="title-main">微信桥接</div></div>
      </div>
      <div class="search"><input id="userFilter" aria-label="搜索对话、账号或线程" placeholder="搜索对话、账号或线程"></div>
      <div id="users" class="scroll"></div>
    </aside>
    <main class="conversation">
      <div class="chat-header">
        <div class="chat-title">
          <h1 id="chatName">微信桥接对话</h1>
          <p id="chatMeta">读取中</p>
        </div>
        <div class="chat-actions">
          <span class="meta"><span id="healthDot" class="dot"></span><span id="health">连接中</span></span>
          <span class="meta"><span id="liveDot" class="dot"></span><span id="live">实时观察连接中</span></span>
          <button id="loginPageButton" type="button" title="在当前面板内打开微信账号二维码登录">账号登录</button>
          <select id="refreshProfile" aria-label="刷新频率">
            <option value="resource">省资源</option>
            <option value="balanced">平衡</option>
            <option value="realtime">实时</option>
          </select>
          <button id="refresh" type="button">刷新</button>
        </div>
      </div>
      <div class="search"><input id="taskFilter" aria-label="搜索消息、结果、状态或 task_id" placeholder="搜索消息、结果、状态或 task_id"></div>
      <div id="chat" class="chat"></div>
      <form id="composer" class="composer">
        <div class="composer-inner">
          <div class="composer-meta">
            <span id="composerTarget">请选择左侧微信用户</span>
            <span id="composerStatus"></span>
          </div>
          <textarea id="composerText" aria-label="代选中微信用户发送消息" placeholder="代该微信用户发送消息。Enter 发送，Shift+Enter 换行"></textarea>
          <div class="composer-actions">
            <div class="composer-tool-actions">
              <input id="attachmentInput" type="file" multiple hidden>
              <input id="voiceFileInput" type="file" accept="audio/*,.amr,.m4a,.webm,.weba,.opus" hidden>
              <button id="attachButton" type="button">添加附件</button>
              <button id="voiceInputButton" type="button" title="使用浏览器语音识别，把识别文字填入输入框">语音输入</button>
              <button id="voiceFileButton" type="button" title="上传音频文件，用本地语音识别转成文字">音频转文字</button>
              <button id="clearComposer" type="button">&#28165;&#31354;</button>
            </div>
            <div class="attachment-list" id="attachmentList"></div>
            <div class="composer-send-actions">
              <label class="composer-record-toggle"><input id="recordToChat" type="checkbox"> 记录到对话流</label>
              <button id="sendToWeixinButton" type="button">发送给微信用户</button>
              <button id="sendButton" class="primary" type="submit">代该用户发送</button>
            </div>
          </div>
        </div>
      </form>
    </main>
    <section class="inspector">
      <div class="topbar">
        <div class="brand"><div class="title-main">任务细节</div></div>
        <span class="pill">控制台</span>
      </div>
      <div id="detail" class="detail"></div>
    </section>
  </div>
  <script>
    // 页面配置：保持数据与布局解耦，后续调整只改这里或 CSS 变量。
    const DASHBOARD_CONFIG = {
      refreshProfiles: {
        resource: {label: "省资源", refreshMs: 5000, hiddenRefreshMs: 30000, detailRefreshMs: 8000, liveRefreshMs: 5000},
        balanced: {label: "平衡", refreshMs: 3000, hiddenRefreshMs: 15000, detailRefreshMs: 5000, liveRefreshMs: 3000},
        realtime: {label: "实时", refreshMs: 1200, hiddenRefreshMs: 6000, detailRefreshMs: 2000, liveRefreshMs: 1200}
      },
      defaultRefreshProfile: "resource",
      detailEventLimit: 32,
      chatTaskRenderLimit: 20,
      userIdPreviewLength: 24,
      scrollIdleMs: 260,
      voiceSlowWarnMs: 1000,
      voiceNoResultWarnMs: 3000,
      voiceStatusTickMs: 250,
      voiceSilenceStopMs: 1200,
      voiceMaxRecordMs: 60000,
      voiceSpeechThreshold: 0.035
    };

    let state = null;
    let liveState = null;
    let selectedUser = "";
    let selectedTask = "";
    let stateLoading = false;
    let detailLoading = false;
    let liveLoading = false;
    const taskDetails = new Map();
    let isUserScrolling = false;
    let scrollTimer = null;
    let pendingAttachments = [];
    let rawEventPayloads = new Map();
    const expandedFlows = new Set();
    let refreshProfile = localStorage.getItem("mobileDashboard.refreshProfile") || DASHBOARD_CONFIG.defaultRefreshProfile;
    let refreshTimers = [];
    let lastStateSignature = "";
    let lastLiveSignature = "";
    let lastDetailSignature = "";
    let voiceRecording = false;
    let voiceTranscribing = false;
    let voiceBaseText = "";
    let voiceStartedAt = 0;
    let voiceStatusTimer = null;
    let voiceMediaRecorder = null;
    let voiceMediaStream = null;
    let voiceAudioContext = null;
    let voiceAnalyser = null;
    let voiceAudioSource = null;
    let voiceChunks = [];
    let voiceLastSpeechAt = 0;
    let voiceSpeechDetected = false;
    let voiceStopRequested = false;
    const MediaRecorderApi = window.MediaRecorder || null;

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const includes = (value, needle) => String(value ?? "").toLowerCase().includes(String(needle ?? "").toLowerCase());
    const fmt = (value) => value ? String(value).replace("T", " ").replace(/\.\d+.*/, "") : "-";
    const shortTime = (value) => {
      if (!value) return "-";
      const s = String(value).replace("T", " ");
      return s.slice(5, 16);
    };
    const statusClass = (status) => {
      if (["done", "pushed_to_wecom"].includes(status)) return "ok";
      if (["failed", "push_failed", "rejected", "cancelled", "codex_timeout"].includes(status)) return "bad";
      if (["pending", "claimed", "queued_for_codex", "sent_to_codex", "processing", "waiting_confirmation", "reply_sending"].includes(status)) return "warn";
      return "";
    };
    const statusText = (status) => ({
      pending: "待处理",
      claimed: "已领取",
      queued_for_codex: "已入队",
      sent_to_codex: "思考中",
      processing: "处理中",
      waiting_confirmation: "待确认",
      reply_sending: "发送中",
      done: "已完成",
      pushed_to_wecom: "已回发",
      push_failed: "回发失败",
      failed: "失败",
      rejected: "已拒绝",
      codex_timeout: "异常",
      cancelled: "已取消"
    }[status] || status || "-");
    const pill = (text, cls = "") => `<span class="pill ${cls}">${esc(text || "-")}</span>`;
    const activeStatuses = new Set(["pending", "queued_for_codex", "sent_to_codex", "processing"]);
    const retryableStatuses = new Set(["pending", "failed", "push_failed", "codex_timeout", "cancelled"]);
    const cancellableStatuses = new Set(["pending", "queued_for_codex", "sent_to_codex", "processing"]);

    function stableSignature(value) {
      const normalize = (item) => {
        if (Array.isArray(item)) return item.map(normalize);
        if (!item || typeof item !== "object") return item;
        return Object.keys(item).sort().reduce((acc, key) => {
          acc[key] = normalize(item[key]);
          return acc;
        }, {});
      };
      return JSON.stringify(normalize(value));
    }

    function taskSignature(task) {
      if (!task) return "";
      return stableSignature({
        id: task.id,
        status: task.status,
        codex_turn_id: task.codex_turn_id,
        push_status: task.push_status,
        updated_at: task.updated_at,
        completed_at: task.completed_at,
        pushed_at: task.pushed_at,
        result_preview: task.result_preview,
        result: task.result,
        error: task.error,
        attachment_count: task.attachment_count
      });
    }

    function stateSignature(nextState) {
      return stableSignature({
        ok: nextState?.ok,
        error: nextState?.error,
        users: (nextState?.users || []).map((user) => ({
          external_user: user.external_user,
          display_name: user.display_name,
          task_count: user.task_count,
          active_count: user.active_count,
          receiver_accounts: user.receiver_accounts,
          thread_names: user.thread_names
        })),
        tasks: (nextState?.tasks || []).map(taskSignature)
      });
    }

    function detailSignature(detail) {
      const fullTask = (detail?.tasks || [])[0] || null;
      return stableSignature({
        ok: detail?.ok,
        task: taskSignature(fullTask),
        codex_turn_id: fullTask?.codex_turn_id,
        events: (detail?.events || []).map((event) => ({
          id: event.id,
          task_id: event.task_id,
          event_type: event.event_type,
          summary: event.summary,
          created_at: event.created_at
        }))
      });
    }

    function refreshConfig() {
      return DASHBOARD_CONFIG.refreshProfiles[refreshProfile] || DASHBOARD_CONFIG.refreshProfiles[DASHBOARD_CONFIG.defaultRefreshProfile];
    }

    function renderRefreshProfile() {
      const select = $("refreshProfile");
      if (!select) return;
      if (!DASHBOARD_CONFIG.refreshProfiles[refreshProfile]) refreshProfile = DASHBOARD_CONFIG.defaultRefreshProfile;
      select.value = refreshProfile;
      select.title = `主状态 ${refreshConfig().refreshMs / 1000}s，详情 ${refreshConfig().detailRefreshMs / 1000}s，实时 ${refreshConfig().liveRefreshMs / 1000}s`;
    }

    async function postJson(url, payload) {
      const res = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload || {})
      });
      const data = await res.json().catch(() => ({ok: false, error: "响应不是 JSON"}));
      if (!res.ok || (!data.ok && !data.recoverable)) throw new Error(data.error || data.message || `请求失败：${res.status}`);
      return data;
    }

    function chatScrollState() {
      const el = $("chat");
      return {
        top: el ? el.scrollTop : 0,
        height: el ? el.scrollHeight : 0,
      };
    }

    function restoreChatScroll(before) {
      const el = $("chat");
      if (!el || !before) return;
      const delta = el.scrollHeight - before.height;
      el.scrollTop = Math.max(0, before.top + delta);
    }

    function mergeFullTask(fullTask) {
      if (!fullTask || !fullTask.id) return fullTask;
      taskDetails.set(fullTask.id, fullTask);
      const idx = (state?.tasks || []).findIndex((t) => t.id === fullTask.id);
      if (idx >= 0) state.tasks[idx] = {...state.tasks[idx], ...fullTask};
      return fullTask;
    }

    async function fetchTaskDetail(taskId) {
      if (!taskId) return null;
      const cached = taskDetails.get(taskId);
      if (cached) return cached;
      const res = await fetch(`/api/task?id=${encodeURIComponent(taskId)}`, {cache: "no-store"});
      const detail = await res.json();
      if (!detail.ok) return null;
      const fullTask = (detail.tasks || [])[0];
      return mergeFullTask(fullTask);
    }

    function attachmentCopyText(task) {
      const attachments = Array.isArray(task.attachments) ? task.attachments : [];
      if (!attachments.length) return "";
      return attachments.map((a, i) => {
        const name = a.name || a.filename || a.fileName || "附件";
        const mime = a.mime || a.content_type || a.type || "";
        const size = a.size || a.size_bytes || "";
        const path = a.local_path || a.path || a.url || "";
        return `${i + 1}. ${name}${mime ? `\n类型：${mime}` : ""}${size ? `\n大小：${size}` : ""}${path ? `\n路径：${path}` : ""}`;
      }).join("\n\n");
    }

    function taskRuntimeValue(taskId, keyPrefix) {
      const key = `${keyPrefix}:${taskId}`;
      const item = state?.runtime?.[key];
      if (!item) return "";
      return String(item.value ?? item ?? "");
    }

    function liveThreadState(threadId) {
      const live = threadId ? liveState?.threads?.[threadId] : null;
      const ageSeconds = Number(liveState?.age_seconds ?? 999999);
      const fresh = Boolean(liveState?.ok && liveState?.connected && Number.isFinite(ageSeconds) && ageSeconds <= 10);
      return {
        live,
        fresh,
        stale: Boolean(live) && !fresh,
        ageSeconds,
      };
    }

    function nativeCodexEvents(task) {
      const threadState = liveThreadState(task.codex_thread_id);
      const live = threadState.fresh ? threadState.live : null;
      const all = Array.isArray(live?.native_events) ? live.native_events.slice() : [];
      const turnId = taskRuntimeValue(task.id, "codex_turn") || String(task.codex_turn_id || "");
      if (!turnId) {
        return {live, turnId: "", events: []};
      }
      const filtered = turnId
        ? all.filter((e) => e.turn_id === turnId || (!e.turn_id && e.method !== "thread/status/changed"))
        : all.filter((e) => e.method !== "thread/status/changed");
      const fallback = [];
      if (live?.delta_preview) {
        fallback.push({
          at: live.updated_at || liveState?.generated_at || "",
          method: "live/delta_preview",
          turn_id: live.active_turn_id || turnId,
          summary: live.delta_preview,
        });
      }
      if (live?.final_preview) {
        fallback.push({
          at: live.updated_at || liveState?.generated_at || "",
          method: "live/final_preview",
          turn_id: live.final_turn_id || turnId,
          summary: live.final_preview,
        });
      }
      if (live?.token_usage) {
        fallback.push({
          at: live.updated_at || liveState?.generated_at || "",
          method: "live/token_usage",
          turn_id: live.active_turn_id || live.final_turn_id || turnId,
          summary: JSON.stringify(live.token_usage),
        });
      }
      return {
        live,
        turnId,
        events: (filtered.length ? filtered : fallback).slice(-12),
      };
    }

    function renderNativeCodexItem(item) {
      if (!item) return "";
      const duration = item.duration_ms ? `${item.duration_ms}ms` : "";
      const meta = [item.type, item.phase, item.server, item.title, duration].filter(Boolean).join(" · ");
      const text = item.text || "";
      return `<div class="flow-native-item">
        ${meta ? `<div class="flow-native-meta">${esc(meta)}</div>` : ""}
        ${text ? `<div>${esc(text)}</div>` : ""}
      </div>`;
    }

    function renderFlowPanel(task) {
      if (!expandedFlows.has(task.id)) return "";
      const native = nativeCodexEvents(task);
      const nativeSteps = native.events.length
        ? native.events.map((e) => {
            const items = Array.isArray(e.items) ? e.items.map(renderNativeCodexItem).join("") : renderNativeCodexItem(e.item);
            return `<div class="flow-step"><time>${esc(fmt(e.at))}</time><div><strong>${esc(e.method || "Codex")}</strong><br>${esc(e.summary || e.status || "Codex 原生事件。")}${items ? `<div class="flow-native-items">${items}</div>` : ""}</div></div>`;
          }).join("")
        : `<div class="empty">当前没有捕获到该任务的 Codex 原生可见细节。实时观察只记录在线后收到的新 turn/item 事件；历史任务可能只有最终回复和桥接诊断。</div>`;
      return `<div class="flow-panel" data-flow-panel="${esc(task.id)}">
        <div class="flow-inner">
          <div class="flow-title"><span>Codex 原生可见细节</span><span>${native.turnId ? `turn ${esc(native.turnId).slice(0, 8)}` : "未绑定 turn"}</span></div>
          <div class="flow-steps">${nativeSteps}</div>
        </div>
      </div>`;
    }

    async function copyText(value, button) {
      const text = String(value || "");
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        if (button) {
          const old = button.textContent;
          button.textContent = "已复制";
          setTimeout(() => { button.textContent = old; }, 900);
        }
      } catch (_) {
        const area = document.createElement("textarea");
        area.value = text;
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
        if (button) {
          const old = button.textContent;
          button.textContent = "已复制";
          setTimeout(() => { button.textContent = old; }, 900);
        }
      }
    }

    async function load() {
      if (stateLoading) return;
      stateLoading = true;
      try {
        const res = await fetch("/api/state", {cache: "no-store"});
        const nextState = await res.json();
        if (state && selectedTask && taskDetails.has(selectedTask)) {
          const cachedTask = taskDetails.get(selectedTask);
          const idx = (nextState.tasks || []).findIndex((t) => t.id === selectedTask);
          if (idx >= 0) nextState.tasks[idx] = {...nextState.tasks[idx], ...cachedTask};
          nextState.events = state.events || [];
        }
        const nextSignature = stateSignature(nextState);
        state = nextState;
        const ok = Boolean(state.ok);
        $("health").textContent = ok ? "health ok" : `异常：${state.error || "unknown"}`;
        $("healthDot").className = `dot ${ok ? "ok" : "bad"}`;
        preserveSelection();
        if (nextSignature !== lastStateSignature) {
          lastStateSignature = nextSignature;
          render();
        }
      } catch (err) {
        $("health").textContent = `连接失败`;
        $("healthDot").className = "dot bad";
      } finally {
        stateLoading = false;
      }
    }

    async function loadLive() {
      if (liveLoading) return;
      liveLoading = true;
      try {
        const res = await fetch("/api/live", {cache: "no-store"});
        const nextLiveState = await res.json();
        const nextSignature = stableSignature(nextLiveState);
        liveState = nextLiveState;
        if (nextSignature !== lastLiveSignature) {
          lastLiveSignature = nextSignature;
          renderLive();
        }
      } catch (_) {
        liveState = {ok: false, connected: false, last_error: "实时观察连接失败", threads: {}};
        lastLiveSignature = stableSignature(liveState);
        renderLive();
      } finally {
        liveLoading = false;
      }
    }

    function preserveSelection() {
      const users = state?.users || [];
      if (!selectedUser && users[0]) selectedUser = users[0].external_user;
      if (selectedUser && !users.some((u) => u.external_user === selectedUser) && users[0]) selectedUser = users[0].external_user;
      const tasks = filteredTasks();
      if (!selectedTask && tasks[0]) selectedTask = tasks[0].id;
      if (selectedTask && !tasks.some((t) => t.id === selectedTask) && tasks[0]) selectedTask = tasks[0].id;
    }

    function userTitle(user) {
      const shortId = (user.external_user || "").slice(0, DASHBOARD_CONFIG.userIdPreviewLength);
      return user.display_name || `微信用户 ${shortId}`;
    }

    function renderUsers() {
      const filter = $("userFilter").value.trim();
      const users = (state?.users || []).filter((u) => {
        const hay = [u.external_user, u.display_name, ...(u.receiver_accounts || []), ...(u.thread_names || [])].join(" ");
        return !filter || includes(hay, filter);
      });
      $("users").innerHTML = users.length ? users.map((u) => {
        const selected = selectedUser === u.external_user ? " selected" : "";
        const account = (u.receiver_accounts || [])[0] || "-";
        const thread = (u.thread_names || [])[0] || "-";
        const active = Number(u.active_count || 0);
        return `<div class="thread${selected}" data-user="${esc(u.external_user)}">
          <div class="thread-head">
            <div class="thread-name">${esc(userTitle(u))}</div>
            ${pill(statusText(u.latest_status), statusClass(u.latest_status))}
          </div>
          <div class="thread-preview">${esc(thread)} · ${esc(account)}</div>
          <div class="thread-foot"><span>${esc(u.external_user.slice(0, DASHBOARD_CONFIG.userIdPreviewLength))}</span><span>${active ? `${active} active` : shortTime(u.latest_task_at)}</span></div>
        </div>`;
      }).join("") : `<div class="empty">没有对话</div>`;
      document.querySelectorAll("[data-user]").forEach((el) => {
        el.onclick = () => {
          selectedUser = el.getAttribute("data-user") || "";
          const tasks = filteredTasks();
          selectedTask = tasks[0]?.id || "";
          render();
          if (selectedTask) loadTaskEvents(selectedTask);
        };
      });
    }

    function filteredTasks() {
      const filter = $("taskFilter")?.value.trim() || "";
      return (state?.tasks || []).filter((t) => {
        if (selectedUser && t.external_user !== selectedUser) return false;
        const hay = [t.id, t.external_user, t.receiver_account_id, t.status, t.push_status, t.text_preview, t.result_preview, t.error_preview, t.thread_name].join(" ");
        return !filter || includes(hay, filter);
      });
    }

    function renderHeader() {
      const user = (state?.users || []).find((u) => u.external_user === selectedUser);
      const tasks = filteredTasks();
      $("chatName").textContent = user ? userTitle(user) : "微信桥接对话";
      const account = user ? ((user.receiver_accounts || [])[0] || "-") : "-";
      const thread = user ? ((user.thread_names || [])[0] || "-") : "-";
      const threadId = user && user.thread_ids ? user.thread_ids[0] : "";
      const threadState = liveThreadState(threadId);
      const live = threadState.fresh ? threadState.live : null;
      const liveSuffix = live?.active_turn_status
        ? ` · ${liveStatusText(live)}`
        : threadState.stale
          ? " · 实时状态待刷新"
          : "";
      $("chatMeta").textContent = `${account} · ${thread} · ${tasks.length} 条记录${liveSuffix}`;
      renderComposerTarget(user, account, thread);
    }

    function liveStatusText(live) {
      if (!live) return "";
      if (live.active_turn_status === "streaming") return "实时：正在输出";
      if (live.active_turn_status) return `实时：${live.active_turn_status}`;
      if (live.status) return `线程：${live.status}`;
      return "";
    }

    function renderLive() {
      const ageSeconds = Number(liveState?.age_seconds ?? 999999);
      const fresh = Boolean(liveState?.ok && liveState?.connected && Number.isFinite(ageSeconds) && ageSeconds <= 10);
      const connected = Boolean(liveState?.ok && liveState?.connected);
      const stale = connected && !fresh;
      $("live").textContent = connected
        ? stale
          ? `实时观察在线 · ${liveState.watched_thread_count || 0} 线程 · 状态待刷新`
          : `实时观察在线 · ${liveState.watched_thread_count || 0} 线程`
        : `实时观察离线`;
      $("liveDot").className = `dot ${connected ? (stale ? "warn" : "ok") : "bad"}`;
      if (state) renderHeader();
    }

    function renderChat() {
      if (isUserScrolling) return;
      const scrollBefore = chatScrollState();
      const allTasks = filteredTasks().slice().reverse();
      const overflowCount = Math.max(0, allTasks.length - DASHBOARD_CONFIG.chatTaskRenderLimit);
      const tasks = allTasks.slice(-DASHBOARD_CONFIG.chatTaskRenderLimit);
      if (!tasks.length) {
        $("chat").innerHTML = `<div class="empty">没有匹配消息</div>`;
        return;
      }
      let lastDay = "";
      const html = [];
      if (overflowCount) {
        html.push(`<div class="day">已隐藏较早 ${overflowCount} 条记录，可用搜索定位</div>`);
      }
      for (const t of tasks) {
        const day = String(t.created_at || "").slice(0, 10);
        if (day && day !== lastDay) {
          html.push(`<div class="day">${esc(day)}</div>`);
          lastDay = day;
        }
        const selected = selectedTask === t.id ? " selected" : "";
        const flowOpen = expandedFlows.has(t.id);
        const outboundText = taskOutboundSummary(t);
        const outboundVisible = Boolean(t.metadata?.record_to_chat);
        html.push(`<div class="turn" data-task="${esc(t.id)}">
          <div class="avatar user">微</div>
          <div class="bubble user">
            <div class="bubble-head"><span>微信</span><span>${shortTime(t.created_at)}</span></div>
            <div class="bubble-body">${esc(t.text || t.text_preview || "(空)")}</div>
            <div class="bubble-tools">
              <button class="bubble-tool" type="button" data-toggle-flow="${esc(t.id)}">${flowOpen ? "折叠流程" : "展开流程"}</button>
              <button class="bubble-tool" type="button" data-copy-text="${esc(t.id)}">复制文本</button>
              ${t.attachment_count ? `<button class="bubble-tool" type="button" data-copy-attachments="${esc(t.id)}">复制附件</button>` : ""}
            </div>
          </div>
        </div>`);
        if (outboundVisible) {
          html.push(`<div class="turn" data-task="${esc(t.id)}">
            <div class="avatar">D</div>
            <div class="bubble outbound">
              <div class="bubble-head"><span>直发微信</span><span>${pill(statusText(t.push_status || t.status), statusClass(t.push_status || t.status))}</span></div>
              <div class="bubble-body">${esc(outboundText)}</div>
              <div class="bubble-tools">
                <button class="bubble-tool" type="button" data-toggle-flow="${esc(t.id)}">${flowOpen ? "折叠流程" : "展开流程"}</button>
              </div>
            </div>
          </div>`);
        }
        html.push(`<div class="turn" data-task="${esc(t.id)}">
          <div class="avatar">C</div>
          <div class="bubble">
            <div class="bubble-head"><span>Codex</span><span>${pill(statusText(t.status), statusClass(t.status))}</span></div>
            <div class="bubble-body ${t.result || t.result_preview ? "" : "bubble-empty"}">${esc(t.result || t.result_preview || "等待最终回复")}</div>
            <div class="bubble-tools">
              <button class="bubble-tool" type="button" data-toggle-flow="${esc(t.id)}">${flowOpen ? "折叠流程" : "展开流程"}</button>
              <button class="bubble-tool" type="button" data-copy-result="${esc(t.id)}">复制文本</button>
            </div>
          </div>
        </div>`);
        html.push(renderFlowPanel(t));
        html.push(`<div class="task-row">
          <button class="task-chip${selected}" type="button" data-task-chip="${esc(t.id)}">${esc(t.id)} · ${esc(statusText(t.status))} · ${esc(shortTime(t.updated_at))}</button>
        </div>`);
      }
      $("chat").innerHTML = html.join("");
      restoreChatScroll(scrollBefore);
      document.querySelectorAll("[data-task], [data-task-chip]").forEach((el) => {
        el.onclick = () => {
          selectedTask = el.getAttribute("data-task") || el.getAttribute("data-task-chip") || "";
          render();
          loadTaskEvents(selectedTask);
        };
      });
      document.querySelectorAll("[data-copy-text], [data-copy-result], [data-copy-attachments]").forEach((el) => {
        el.onclick = async (event) => {
          event.stopPropagation();
          const id = el.getAttribute("data-copy-text") || el.getAttribute("data-copy-result") || el.getAttribute("data-copy-attachments") || "";
          let task = (state?.tasks || []).find((item) => item.id === id) || taskDetails.get(id);
          if (!task) return;
          if (el.hasAttribute("data-copy-attachments")) {
            task = await fetchTaskDetail(id) || task;
            copyText(attachmentCopyText(task), el);
          } else if (el.hasAttribute("data-copy-result")) {
            task = await fetchTaskDetail(id) || task;
            copyText(task.result || task.result_preview || "", el);
          } else {
            task = await fetchTaskDetail(id) || task;
            copyText(task.text || task.text_preview || "", el);
          }
        };
      });
      document.querySelectorAll("[data-toggle-flow]").forEach((el) => {
        el.onclick = async (event) => {
          event.stopPropagation();
          const id = el.getAttribute("data-toggle-flow") || "";
          if (!id) return;
          selectedTask = id;
          if (expandedFlows.has(id)) {
            expandedFlows.delete(id);
            render();
            return;
          }
          expandedFlows.add(id);
          await loadTaskEvents(id);
          render();
        };
      });
    }

    function taskOutboundSummary(task) {
      const attachments = Array.isArray(task?.attachments) ? task.attachments : [];
      const metadata = task?.metadata || {};
      const recordText = metadata.chat_record_text || task?.text || task?.text_preview || "";
      const prefix = metadata.record_to_chat ? "已记录到对话流：\n" : "";
      if (attachments.length) {
        const names = attachments.map((a) => a?.name || a?.local_path || "附件").join("、");
        return `${prefix}${recordText || "附件直发"}${recordText ? "\n" : ""}${names ? `附件：${names}` : ""}`.trim();
      }
      return `${prefix}${recordText || "直发微信内容"}`.trim();
    }

    function renderComposerTarget(user, account, thread) {
      const valid = Boolean(user && user.external_user && user.external_user.toLowerCase() !== "unknown");
      $("composerTarget").textContent = valid
        ? `代表 ${userTitle(user)} 发送 · ${account || "-"} · ${thread || "-"}`
        : "请选择左侧微信用户";
      $("composerText").disabled = !valid;
      $("sendButton").disabled = !valid;
      $("sendToWeixinButton").disabled = !valid;
      $("attachButton").disabled = !valid;
      const voiceFileButton = $("voiceFileButton");
      if (voiceFileButton) voiceFileButton.disabled = !valid;
      const voiceButton = $("voiceInputButton");
      if (voiceButton) {
        voiceButton.disabled = !valid || !MediaRecorderApi || !navigator.mediaDevices?.getUserMedia;
        voiceButton.title = MediaRecorderApi && navigator.mediaDevices?.getUserMedia
          ? "录音后用本地语音识别转成文字"
          : "当前浏览器不支持麦克风录音，可使用音频转文字";
      }
      const recordBox = $("recordToChat");
      if (recordBox) recordBox.disabled = !valid;
    }

    function renderRecordToggle() {
      const box = $("recordToChat");
      if (!box) return;
      if (box.checked === undefined) box.checked = false;
    }

    function renderAttachments() {
      $("attachmentList").innerHTML = pendingAttachments.length ? pendingAttachments.map((a, i) => `
        <span class="attachment-chip"><span>${esc(a.name || a.local_path || "附件")}</span><button type="button" data-remove-attachment="${i}">移除</button></span>
      `).join("") : "";
      document.querySelectorAll("[data-remove-attachment]").forEach((el) => {
        el.onclick = () => {
          const idx = Number(el.getAttribute("data-remove-attachment"));
          pendingAttachments.splice(idx, 1);
          renderAttachments();
        };
      });
    }

    function clearComposer() {
      stopVoiceInput();
      $("composerText").value = "";
      pendingAttachments = [];
      renderAttachments();
      $("composerStatus").textContent = "";
    }

    function updateVoiceButton() {
      const button = $("voiceInputButton");
      if (!button) return;
      if (voiceTranscribing) {
        button.textContent = "转写中...";
      } else if (voiceRecording && voiceStartedAt) {
        const elapsed = Math.max(0, (performance.now() - voiceStartedAt) / 1000);
        button.textContent = `停止录音 ${elapsed.toFixed(1)}s`;
      } else {
        button.textContent = "语音输入";
      }
      button.classList.toggle("voice-active", voiceRecording || voiceTranscribing);
    }

    function cleanupVoiceAudio() {
      clearVoiceStatusTimer();
      if (voiceMediaStream) {
        for (const track of voiceMediaStream.getTracks()) track.stop();
      }
      if (voiceAudioContext) {
        try { voiceAudioContext.close(); } catch (_) {}
      }
      voiceMediaRecorder = null;
      voiceMediaStream = null;
      voiceAudioContext = null;
      voiceAnalyser = null;
      voiceAudioSource = null;
    }

    function resetVoiceSession() {
      cleanupVoiceAudio();
      voiceRecording = false;
      voiceTranscribing = false;
      voiceStartedAt = 0;
      voiceChunks = [];
      voiceLastSpeechAt = 0;
      voiceSpeechDetected = false;
      voiceStopRequested = false;
      updateVoiceButton();
    }

    function voiceMimeType() {
      const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/wav"];
      for (const item of candidates) {
        try {
          if (!MediaRecorderApi || MediaRecorderApi.isTypeSupported?.(item)) return item;
        } catch (_) {}
      }
      return "";
    }

    function voiceFileExtension(mime) {
      if (String(mime || "").includes("mp4")) return "m4a";
      if (String(mime || "").includes("wav")) return "wav";
      return "webm";
    }

    function currentVoiceLevel() {
      if (!voiceAnalyser) return 0;
      const data = new Uint8Array(voiceAnalyser.fftSize);
      voiceAnalyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const value of data) {
        const normalized = (value - 128) / 128;
        sum += normalized * normalized;
      }
      return Math.sqrt(sum / data.length);
    }

    function renderVoiceStatus(force = false) {
      if (!voiceRecording || !voiceStartedAt) return;
      const now = performance.now();
      const elapsedMs = now - voiceStartedAt;
      const elapsed = (elapsedMs / 1000).toFixed(1);
      const level = currentVoiceLevel();
      if (level >= DASHBOARD_CONFIG.voiceSpeechThreshold) {
        voiceSpeechDetected = true;
        voiceLastSpeechAt = now;
      }
      updateVoiceButton();
      if (voiceSpeechDetected) {
        const silenceMs = now - voiceLastSpeechAt;
        $("composerStatus").textContent = `录音中 ${elapsed}s · 检测到语音`;
        if (silenceMs >= DASHBOARD_CONFIG.voiceSilenceStopMs && elapsedMs >= 800) {
          stopVoiceInput("auto_silence");
        }
      } else if (elapsedMs >= DASHBOARD_CONFIG.voiceNoResultWarnMs) {
        $("composerStatus").textContent = `录音中 ${elapsed}s · 还未检测到明显语音`;
      } else if (force || elapsedMs >= DASHBOARD_CONFIG.voiceSlowWarnMs) {
        $("composerStatus").textContent = `录音中 ${elapsed}s`;
      }
      if (elapsedMs >= DASHBOARD_CONFIG.voiceMaxRecordMs) {
        stopVoiceInput("max_duration");
      }
    }

    function clearVoiceStatusTimer() {
      if (!voiceStatusTimer) return;
      clearInterval(voiceStatusTimer);
      voiceStatusTimer = null;
    }

    async function transcribeRecordedVoice(blob, mime) {
      voiceTranscribing = true;
      updateVoiceButton();
      $("composerStatus").textContent = "录音完成，正在本地转文字...";
      const ext = voiceFileExtension(mime);
      const file = new File([blob], `dashboard-voice-${Date.now()}.${ext}`, {type: mime || "audio/webm"});
      const data = await transcribeAudioFile(file);
      const text = String(data.text || "").trim();
      if (!text) {
        $("composerStatus").textContent = "录音已转写，但没有识别到内容";
        return;
      }
      const box = $("composerText");
      const current = box.value.trim();
      box.value = current ? `${current}\n${text}` : text;
      $("composerStatus").textContent = `语音输入完成：${text.length} 字`;
    }

    function stopVoiceInput(reason = "manual") {
      if (!voiceRecording && !voiceMediaRecorder) return;
      voiceStopRequested = true;
      clearVoiceStatusTimer();
      $("composerStatus").textContent = reason === "auto_silence" ? "检测到静音，正在结束录音..." : "正在结束录音...";
      updateVoiceButton();
      try {
        if (voiceMediaRecorder && voiceMediaRecorder.state !== "inactive") voiceMediaRecorder.stop();
      } catch (_) {
        resetVoiceSession();
      }
    }

    async function toggleVoiceInput() {
      const user = (state?.users || []).find((u) => u.external_user === selectedUser);
      if (!user) {
        $("composerStatus").textContent = "请先选择左侧微信用户";
        return;
      }
      if (voiceRecording) {
        stopVoiceInput("manual");
        return;
      }
      if (!MediaRecorderApi || !navigator.mediaDevices?.getUserMedia) {
        $("composerStatus").textContent = "当前浏览器不支持麦克风录音";
        return;
      }
      resetVoiceSession();
      try {
        voiceMediaStream = await navigator.mediaDevices.getUserMedia({audio: true});
        const AudioContextApi = window.AudioContext || window.webkitAudioContext;
        if (AudioContextApi) {
          voiceAudioContext = new AudioContextApi();
          voiceAnalyser = voiceAudioContext.createAnalyser();
          voiceAnalyser.fftSize = 1024;
          voiceAudioSource = voiceAudioContext.createMediaStreamSource(voiceMediaStream);
          voiceAudioSource.connect(voiceAnalyser);
        }
        const mime = voiceMimeType();
        voiceMediaRecorder = new MediaRecorderApi(voiceMediaStream, mime ? {mimeType: mime} : undefined);
        voiceChunks = [];
        voiceBaseText = $("composerText").value.trim();
        voiceMediaRecorder.ondataavailable = (event) => {
          if (event.data && event.data.size > 0) voiceChunks.push(event.data);
        };
        voiceMediaRecorder.onerror = (event) => {
          const message = event?.error?.message || "录音失败";
          resetVoiceSession();
          $("composerStatus").textContent = message;
        };
        voiceMediaRecorder.onstop = async () => {
          const chunks = voiceChunks.slice();
          const recordedMime = voiceMediaRecorder?.mimeType || mime || "audio/webm";
          cleanupVoiceAudio();
          voiceRecording = false;
          updateVoiceButton();
          if (!chunks.length) {
            resetVoiceSession();
            $("composerStatus").textContent = "没有录到音频";
            return;
          }
          try {
            await transcribeRecordedVoice(new Blob(chunks, {type: recordedMime}), recordedMime);
          } catch (err) {
            $("composerStatus").textContent = err.message || "语音转文字失败";
          } finally {
            resetVoiceSession();
          }
        };
        voiceMediaRecorder.start(250);
        voiceRecording = true;
        voiceStartedAt = performance.now();
        voiceLastSpeechAt = voiceStartedAt;
        voiceStatusTimer = setInterval(() => renderVoiceStatus(), DASHBOARD_CONFIG.voiceStatusTickMs);
        updateVoiceButton();
        renderVoiceStatus(true);
      } catch (err) {
        resetVoiceSession();
        $("composerStatus").textContent = err?.message || "无法访问麦克风";
      }
    }

    async function uploadFiles(files) {
      const uploaded = [];
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        const res = await fetch("/api/upload-attachment", {method: "POST", body: form});
        const data = await res.json().catch(() => ({ok: false, error: "上传响应不是 JSON"}));
        if (!res.ok || !data.ok) throw new Error(data.error || `附件上传失败：${file.name}`);
        uploaded.push(data.attachment);
      }
      return uploaded;
    }

    async function transcribeAudioFile(file) {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/transcribe-audio", {method: "POST", body: form});
      const data = await res.json().catch(() => ({ok: false, error: "语音识别响应不是 JSON"}));
      if (!res.ok || !data.ok) throw new Error(data.error || `音频转文字失败：${file.name}`);
      return data;
    }

    async function handleVoiceFileInput(file) {
      if (!file) return;
      const user = (state?.users || []).find((u) => u.external_user === selectedUser);
      if (!user) {
        $("composerStatus").textContent = "请先选择左侧微信用户";
        return;
      }
      const voiceFileButton = $("voiceFileButton");
      if (voiceFileButton) voiceFileButton.disabled = true;
      $("composerStatus").textContent = "音频转文字中...";
      try {
        const data = await transcribeAudioFile(file);
        const text = String(data.text || "").trim();
        if (!text) {
          $("composerStatus").textContent = "音频转文字完成，但没有识别到内容";
          return;
        }
        const box = $("composerText");
        const current = box.value.trim();
        box.value = current ? `${current}\n${text}` : text;
        $("composerStatus").textContent = `音频转文字完成：${text.length} 字`;
      } catch (err) {
        $("composerStatus").textContent = err.message || "音频转文字失败";
      } finally {
        if (voiceFileButton) voiceFileButton.disabled = false;
      }
    }

    async function sendFromComposer() {
      const user = (state?.users || []).find((u) => u.external_user === selectedUser);
      if (!user) return;
      const text = $("composerText").value.trim();
      if (!text && !pendingAttachments.length) return;
      $("sendButton").disabled = true;
      $("composerStatus").textContent = "发送中...";
      try {
        const account = (user.receiver_accounts || [])[0] || "";
        const result = await postJson("/api/send", {
          text,
          external_user: user.external_user,
          receiver_account_id: account,
          attachments: pendingAttachments
        });
        $("composerText").value = "";
        pendingAttachments = [];
        renderAttachments();
        $("composerStatus").textContent = `已入队：${result.id || ""}`;
        await load();
      } catch (err) {
        $("composerStatus").textContent = err.message || "发送失败";
      } finally {
        $("sendButton").disabled = false;
      }
    }

    async function sendComposerToWeixin() {
      const user = (state?.users || []).find((u) => u.external_user === selectedUser);
      if (!user) return;
      const text = $("composerText").value.trim();
      if (!text && !pendingAttachments.length) return;
      const recordToChat = Boolean($("recordToChat")?.checked);
      $("sendToWeixinButton").disabled = true;
      $("composerStatus").textContent = "正在直接发送到微信...";
      try {
        const account = (user.receiver_accounts || [])[0] || "";
        const result = await postJson("/api/send-to-weixin", {
          text,
          external_user: user.external_user,
          receiver_account_id: account,
          attachments: pendingAttachments,
          record_to_chat: recordToChat
        });
        $("composerText").value = "";
        pendingAttachments = [];
        renderAttachments();
        $("composerStatus").textContent = result.status_message || `已提交微信发送通道：${result.id || ""}`;
        await load();
      } catch (err) {
        $("composerStatus").textContent = err.message || "发送到微信失败";
      } finally {
        $("sendToWeixinButton").disabled = false;
      }
    }

    async function loadTaskEvents(taskId) {
      if (!taskId) return;
      if (detailLoading) return;
      detailLoading = true;
      try {
        const res = await fetch(`/api/task?id=${encodeURIComponent(taskId)}`, {cache: "no-store"});
        const detail = await res.json();
        if (state && detail.ok) {
          const nextSignature = `${taskId}:${detailSignature(detail)}`;
          const fullTask = (detail.tasks || [])[0];
          if (fullTask) {
            mergeFullTask(fullTask);
          }
          state.events = detail.events || state.events || [];
          if (nextSignature !== lastDetailSignature) {
            lastDetailSignature = nextSignature;
            render();
          }
        }
      } catch (_) {
      } finally {
        detailLoading = false;
      }
    }

    function renderDetail() {
      if (isUserScrolling) return;
      const task = (state?.tasks || []).find((t) => t.id === selectedTask);
      if (!task) {
        $("detail").innerHTML = `<div class="empty">选择一条消息查看任务细节</div>`;
        return;
      }
      const events = (state?.events || []).filter((e) => e.task_id === task.id).slice(0, DASHBOARD_CONFIG.detailEventLimit);
      const canRetry = retryableStatuses.has(task.status || "");
      const canCancel = cancellableStatuses.has(task.status || "");
      rawEventPayloads = new Map();
      $("detail").innerHTML = `
        <h2>${esc(task.id)}</h2>
        <div class="detail-actions">
          <button id="retryTask" type="button" ${canRetry ? "" : "disabled"}>手动重试</button>
          <button id="cancelTask" class="danger" type="button" ${canCancel ? "" : "disabled"}>撤回并中止</button>
          <button id="copyTask" type="button">复制任务信息</button>
        </div>
        <div class="kv">
          <div>状态</div><div>${pill(statusText(task.status), statusClass(task.status))} ${pill(statusText(task.push_status || "未回发"), statusClass(task.push_status))}</div>
          <div>微信用户</div><div>${esc(task.external_user)}</div>
          <div>接收账号</div><div>${esc(task.receiver_account_id || "-")}</div>
          <div>线程</div><div>${esc(task.thread_name || "-")}<br>${esc(task.codex_thread_id || "-")}</div>
          <div>Turn</div><div>${esc(task.codex_turn_id || taskRuntimeValue(task.id, "codex_turn") || "-")}</div>
          <div>风险</div><div>${esc(task.risk_level || "-")} · ${esc(task.command || "-")}</div>
          <div>创建</div><div>${fmt(task.created_at)}</div>
          <div>入队</div><div>${fmt(task.queued_for_codex_at)}</div>
          <div>投递</div><div>${fmt(task.sent_to_codex_at)}</div>
          <div>完成</div><div>${fmt(task.completed_at)}</div>
          <div>回发</div><div>${fmt(task.pushed_at)}</div>
          <div>附件</div><div>${task.attachment_count || 0}</div>
        </div>
        <div class="section-label">手机消息</div>
        <div class="pre">${esc(task.text || task.text_preview || "(空)")}</div>
        <div class="section-label">最终回复</div>
        <div class="pre">${esc(task.result || task.result_preview || "(暂无)")}</div>
        ${task.error ? `<div class="section-label">错误</div><div class="pre">${esc(task.error)}</div>` : ""}
        <div class="section-label">处理步骤</div>
        ${events.length ? events.map((e) => `<div class="event">
          <div class="event-title"><span>${esc(e.event_type)}</span><span>${fmt(e.created_at)}</span></div>
          <div>${esc(e.summary || "记录事件。")}</div>
          <details class="raw-event" data-event-id="${esc(e.id)}"><summary>原始事件数据</summary><div class="pre raw-placeholder">展开后加载</div></details>
        </div>`).join("") : `<div class="empty">暂无事件</div>`}
      `;
      for (const e of events) rawEventPayloads.set(String(e.id), e.payload);
      document.querySelectorAll("details.raw-event").forEach((el) => {
        el.addEventListener("toggle", () => {
          if (!el.open) return;
          const box = el.querySelector(".pre");
          if (!box || box.dataset.loaded === "1") return;
          const payload = rawEventPayloads.get(String(el.getAttribute("data-event-id"))) || {};
          box.textContent = JSON.stringify(payload, null, 2);
          box.dataset.loaded = "1";
        }, {once: false});
      });
      const retryButton = $("retryTask");
      const cancelButton = $("cancelTask");
      const copyButton = $("copyTask");
      if (retryButton) retryButton.onclick = async () => {
        if (!confirm("确认将该任务改回 pending 并重新投递？已成功回发的任务不会被重试。")) return;
        try {
          await postJson("/api/retry", {task_id: task.id});
          await loadTaskEvents(task.id);
          await load();
        } catch (err) {
          alert(err.message || "重试失败");
        }
      };
      if (cancelButton) cancelButton.onclick = async () => {
        if (!confirm("确认撤回该任务？如果它对应的 Codex turn 可确认归属，系统会尝试只中止这一条。")) return;
        try {
          await postJson("/api/cancel", {task_id: task.id});
          await loadTaskEvents(task.id);
          await load();
        } catch (err) {
          alert(err.message || "撤回失败");
        }
      };
      if (copyButton) copyButton.onclick = async () => {
        await navigator.clipboard.writeText(JSON.stringify(task, null, 2));
      };
    }

    function render() {
      if (!state) return;
      renderUsers();
      renderHeader();
      renderRefreshProfile();
      if (!isUserScrolling) {
        renderChat();
        renderDetail();
      }
    }

    $("refresh").onclick = () => { load(); loadLive(); if (selectedTask) loadTaskEvents(selectedTask); };
    $("loginPageButton").onclick = () => { window.location.href = "/login/"; };
    $("refreshProfile").onchange = (event) => {
      refreshProfile = event.target.value;
      if (!DASHBOARD_CONFIG.refreshProfiles[refreshProfile]) refreshProfile = DASHBOARD_CONFIG.defaultRefreshProfile;
      localStorage.setItem("mobileDashboard.refreshProfile", refreshProfile);
      lastStateSignature = "";
      lastLiveSignature = "";
      lastDetailSignature = "";
      renderRefreshProfile();
      scheduleRefreshTimers();
    };
    $("userFilter").oninput = render;
    $("taskFilter").oninput = () => { selectedTask = ""; preserveSelection(); render(); };
    $("composer").onsubmit = (event) => { event.preventDefault(); sendFromComposer(); };
    $("sendToWeixinButton").onclick = sendComposerToWeixin;
    $("voiceInputButton").onclick = toggleVoiceInput;
    $("voiceFileButton").onclick = () => $("voiceFileInput").click();
    $("composerText").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendFromComposer();
      }
    });
    $("attachButton").onclick = () => $("attachmentInput").click();
    $("clearComposer").onclick = clearComposer;
    $("attachmentInput").onchange = async (event) => {
      const files = Array.from(event.target.files || []);
      if (!files.length) return;
      $("composerStatus").textContent = "上传附件中...";
      try {
        pendingAttachments.push(...await uploadFiles(files));
        renderAttachments();
        $("composerStatus").textContent = "附件已就绪";
      } catch (err) {
        $("composerStatus").textContent = err.message || "附件上传失败";
      } finally {
        event.target.value = "";
      }
    };
    $("voiceFileInput").onchange = async (event) => {
      const file = Array.from(event.target.files || [])[0];
      try {
        await handleVoiceFileInput(file);
      } finally {
        event.target.value = "";
      }
    };
    $("chat").addEventListener("scroll", () => {
      isUserScrolling = true;
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => {
        isUserScrolling = false;
        renderChat();
      }, DASHBOARD_CONFIG.scrollIdleMs);
    }, {passive: true});
    function scheduleRefreshTimers() {
      refreshTimers.forEach((timer) => clearInterval(timer));
      refreshTimers = [];
      const cfg = refreshConfig();
      refreshTimers.push(setInterval(() => {
        if (document.hidden) return;
        load();
      }, cfg.refreshMs));
      refreshTimers.push(setInterval(() => {
        if (!document.hidden) return;
        load();
      }, cfg.hiddenRefreshMs));
      refreshTimers.push(setInterval(() => {
        if (!document.hidden && selectedTask) loadTaskEvents(selectedTask);
      }, cfg.detailRefreshMs));
      refreshTimers.push(setInterval(() => {
        if (!document.hidden) loadLive();
      }, cfg.liveRefreshMs));
    }
    renderRefreshProfile();
    scheduleRefreshTimers();
    load();
    loadLive();
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "MobileOpenClawDashboard/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    @property
    def app(self) -> "DashboardServer":
        return self.server  # type: ignore[return-value]

    def send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_multipart_file(self) -> tuple[Any | None, int]:
        content_type = self.headers.get("Content-Type") or ""
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            raise ValueError("empty upload")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("upload too large")
        if "multipart/form-data" not in content_type:
            raise ValueError("multipart/form-data required")
        raw = self.rfile.read(length)
        message = BytesParser(policy=email_policy).parsebytes(
            b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
        )
        for part in message.iter_parts():
            if part.get_param("name", header="content-disposition") == "file":
                if not part.get_filename():
                    raise ValueError("missing file")
                return part, length
        raise ValueError("missing file")

    def proxy_login(self, parsed: Any) -> None:
        login_ready = self.app.ensure_login_service()
        if not login_ready:
            self.send_json(
                {
                    "ok": False,
                    "error": "login service unavailable",
                    "detail": self.app.login_last_error or "unable to start login service",
                },
                502,
            )
            return
        target_path = parsed.path[len("/login") :] or "/"
        if not target_path.startswith("/"):
            target_path = "/" + target_path
        if parsed.query:
            target_path = f"{target_path}?{parsed.query}"
        length = int(self.headers.get("Content-Length") or "0")
        if length > LOGIN_PROXY_MAX_BODY_BYTES:
            self.send_json({"ok": False, "error": "login proxy body too large"}, 413)
            return
        body = self.rfile.read(length) if length > 0 else None
        headers: dict[str, str] = {}
        for key, value in self.headers.items():
            lower = key.lower()
            if lower in LOGIN_PROXY_HOP_HEADERS or lower == "host":
                continue
            headers[key] = value
        headers["Host"] = f"{self.app.login_host}:{self.app.login_port}"
        conn = http.client.HTTPConnection(self.app.login_host, self.app.login_port, timeout=15)
        try:
            conn.request(self.command, target_path, body=body, headers=headers)
            response = conn.getresponse()
            response_body = response.read()
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            if content_type_is_html(response_headers):
                response_body = rewrite_login_html(response_body)
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                lower = key.lower()
                if lower in LOGIN_PROXY_HOP_HEADERS or lower in {"content-length", "content-encoding"}:
                    continue
                if lower == "location" and value.startswith("/"):
                    value = "/login" + value
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"login service unavailable: {exc}"}, 502)
        finally:
            conn.close()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/login" or parsed.path.startswith("/login/"):
                self.proxy_login(parsed)
                return
            if parsed.path == "/api/send":
                payload = read_json_body(self)
                result = dashboard_enqueue(
                    self.app.db_path,
                    self.app.config_path,
                    str(payload.get("text") or ""),
                    str(payload.get("external_user") or ""),
                    str(payload.get("receiver_account_id") or ""),
                    payload.get("attachments") if isinstance(payload.get("attachments"), list) else [],
                )
                self.send_json(result, 200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/send-to-weixin":
                payload = read_json_body(self)
                result = dashboard_send_to_weixin(
                    self.app.db_path,
                    self.app.config_path,
                    str(payload.get("text") or ""),
                    str(payload.get("external_user") or ""),
                    str(payload.get("receiver_account_id") or ""),
                    payload.get("attachments") if isinstance(payload.get("attachments"), list) else [],
                    bool(payload.get("record_to_chat", False)),
                )
                self.send_json(result, 200 if (result.get("ok") or result.get("recoverable")) else 400)
                return
            if parsed.path == "/api/retry":
                payload = read_json_body(self)
                result = dashboard_retry_with_notify(
                    self.app.db_path,
                    self.app.config_path,
                    str(payload.get("task_id") or ""),
                    bool(payload.get("notify_weixin", True)),
                )
                self.send_json(result, 200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/cancel":
                payload = read_json_body(self)
                result = dashboard_cancel_with_notify(
                    self.app.db_path,
                    self.app.config_path,
                    str(payload.get("task_id") or ""),
                    bool(payload.get("notify_weixin", True)),
                )
                self.send_json(result, 200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/upload-attachment":
                result = self.handle_upload()
                self.send_json(result, 200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/transcribe-audio":
                result = self.handle_transcribe_audio()
                self.send_json(result, 200 if result.get("ok") else 400)
                return
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        self.send_json({"ok": False, "error": f"not found: {html.escape(parsed.path)}"}, 404)

    def handle_upload(self) -> dict[str, Any]:
        try:
            file_part, _length = self.read_multipart_file()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if file_part is None:
            return {"ok": False, "error": "missing file"}
        day = datetime.now().strftime("%Y%m%d")
        target_dir = self.app.attachments_dir / day
        target_dir.mkdir(parents=True, exist_ok=True)
        original = safe_filename(str(file_part.get_filename() or ""), "attachment")
        result = acquire_bytes_resource(
            source="mobile-dashboard-upload",
            data=file_part.get_payload(decode=True) or b"",
            target_dir=target_dir,
            name=original,
            max_bytes=MAX_UPLOAD_BYTES,
            metadata={"content_type": str(file_part.get_content_type() or "")},
        )
        append_resource_log(RESOURCE_LOG, result)
        if not result.ok:
            return {"ok": False, "error": result.error, "size": result.size}
        attachment = {
            "type": str(file_part.get_content_type() or "application/octet-stream"),
            "name": original,
            "mime": str(file_part.get_content_type() or ""),
            "size": result.size,
            "local_path": result.local_path,
            "stored_path": result.stored_path,
            "sha256": result.sha256,
            "resource_cache_hit": result.cache_hit,
            "source": "dashboard-web",
        }
        return {"ok": True, "attachment": attachment}

    def handle_transcribe_audio(self) -> dict[str, Any]:
        try:
            file_part, _length = self.read_multipart_file()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if file_part is None:
            return {"ok": False, "error": "missing file"}
        original = safe_filename(str(file_part.get_filename() or ""), "audio")
        suffix = Path(original).suffix.lower()
        mime = str(file_part.get_content_type() or "")
        if suffix not in AUDIO_TRANSCRIBE_EXTENSIONS and not mime.startswith("audio/"):
            return {"ok": False, "error": "unsupported audio file type"}
        if not DEFAULT_AUDIO_TOOLKIT.exists():
            return {"ok": False, "error": f"audio toolkit not found: {DEFAULT_AUDIO_TOOLKIT}"}

        day = datetime.now().strftime("%Y%m%d")
        target_dir = self.app.attachments_dir / day / "voice-input"
        target_dir.mkdir(parents=True, exist_ok=True)
        result = acquire_bytes_resource(
            source="mobile-dashboard-voice-input",
            data=file_part.get_payload(decode=True) or b"",
            target_dir=target_dir,
            name=original,
            max_bytes=MAX_UPLOAD_BYTES,
            metadata={"content_type": mime, "purpose": "voice-input-transcription"},
        )
        append_resource_log(RESOURCE_LOG, result)
        if not result.ok or not result.local_path:
            return {"ok": False, "error": result.error or "audio upload failed", "size": result.size}

        source_path = Path(result.local_path)
        transcript_dir = default_work_root() / "dashboard-transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        output_path = transcript_dir / f"{source_path.stem}.zh.txt"
        command = [
            sys.executable,
            str(DEFAULT_AUDIO_TOOLKIT),
            "transcribe-zh",
            str(source_path),
            "--output",
            str(output_path),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                timeout=ASR_TIMEOUT_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"audio transcription timed out after {ASR_TIMEOUT_SECONDS}s"}
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            if len(detail) > 1200:
                detail = detail[:1200] + "..."
            return {"ok": False, "error": detail or f"audio transcription failed with code {completed.returncode}"}
        text = output_path.read_text(encoding="utf-8", errors="replace").strip() if output_path.exists() else ""
        return {
            "ok": True,
            "text": text,
            "attachment": {
                "name": original,
                "mime": mime,
                "size": result.size,
                "local_path": result.local_path,
                "stored_path": result.stored_path,
                "sha256": result.sha256,
            },
            "transcript_path": str(output_path),
            "cache_hit": "cache_hit" in (completed.stdout or ""),
        }

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/login" or parsed.path.startswith("/login/"):
            self.proxy_login(parsed)
            return
        if parsed.path == "/":
            mark_dashboard_activity(self.app.dashboard_activity_path, "page")
            self.send_html(HTML_PAGE)
            return
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return
        if parsed.path == "/api/state":
            mark_dashboard_activity(self.app.dashboard_activity_path, "api_state")
            data = load_state(self.app.db_path, self.app.config_path, self.app.limit, include_events=False, summary=True)
            self.send_json(data, 200 if data.get("ok") else 500)
            return
        if parsed.path == "/api/live":
            mark_dashboard_activity(self.app.dashboard_activity_path, "api_live")
            data = load_live_state(self.app.live_state_path)
            self.send_json(data, 200)
            return
        if parsed.path == "/api/task":
            params = parse_qs(parsed.query)
            task_id = (params.get("id") or [""])[0]
            if not task_id:
                self.send_json({"ok": False, "error": "missing id"}, 400)
                return
            data = load_state(self.app.db_path, self.app.config_path, self.app.limit, task_id=task_id, include_events=True, summary=False)
            self.send_json(data, 200 if data.get("ok") else 500)
            return
        self.send_json({"ok": False, "error": f"not found: {html.escape(parsed.path)}"}, 404)


class DashboardServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[DashboardHandler],
        db_path: Path,
        config_path: Path,
        limit: int,
        live_state_path: Path,
        dashboard_activity_path: Path,
        attachments_dir: Path,
        login_host: str,
        login_port: int,
        login_script: Path,
        login_runs_dir: Path,
        login_timeout_ms: int,
        login_start_wait_seconds: float,
    ):
        super().__init__(server_address, handler)
        self.db_path = db_path
        self.config_path = config_path
        self.limit = limit
        self.live_state_path = live_state_path
        self.dashboard_activity_path = dashboard_activity_path
        self.attachments_dir = attachments_dir
        self.login_host = login_host
        self.login_port = login_port
        self.login_script = login_script
        self.login_runs_dir = login_runs_dir
        self.login_timeout_ms = login_timeout_ms
        self.login_start_wait_seconds = login_start_wait_seconds
        self.login_start_lock = threading.Lock()
        self.login_process: subprocess.Popen[Any] | None = None
        self.login_last_error = ""

    def ensure_login_service(self) -> bool:
        if login_service_healthy(self.login_host, self.login_port):
            self.login_last_error = ""
            return True
        if not is_local_host(self.login_host):
            self.login_last_error = f"remote login host is not started locally: {self.login_host}:{self.login_port}"
            return False
        with self.login_start_lock:
            if login_service_healthy(self.login_host, self.login_port):
                self.login_last_error = ""
                return True
            if tcp_port_open(self.login_host, self.login_port):
                self.login_last_error = f"port {self.login_port} is occupied but login service did not answer /api/state"
                return False
            return self.start_login_service()

    def start_login_service(self) -> bool:
        node = resolve_executable(DEFAULT_NODE_CANDIDATES)
        if not node:
            self.login_last_error = "node executable not found"
            return False
        if not self.login_script.exists():
            self.login_last_error = f"login script not found: {self.login_script}"
            return False
        self.login_runs_dir.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-dashboard-login-on-demand"
        stdout_path = self.login_runs_dir / f"{run_id}.stdout.log"
        stderr_path = self.login_runs_dir / f"{run_id}.stderr.log"
        args = [
            node,
            str(self.login_script),
            "--port",
            str(self.login_port),
            "--timeout-ms",
            str(max(60000, self.login_timeout_ms)),
        ]
        try:
            stdout = stdout_path.open("wb")
            stderr = stderr_path.open("wb")
            env = {
                **os.environ,
                "OPENCLAW_HOME": str(OPENCLAW_HOME),
                "OPENCLAW_STATE_DIR": str(OPENCLAW_STATE_DIR),
            }
            self.login_process = subprocess.Popen(
                args,
                cwd=str(OPENCLAW_BASE),
                env=env,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            self.login_last_error = f"failed to start login service: {exc}"
            return False

        deadline = time.monotonic() + max(1.0, self.login_start_wait_seconds)
        while time.monotonic() < deadline:
            if login_service_healthy(self.login_host, self.login_port):
                self.login_last_error = ""
                return True
            if self.login_process and self.login_process.poll() is not None:
                self.login_last_error = (
                    f"login service exited early with code {self.login_process.returncode}; "
                    f"stderr: {stderr_path}"
                )
                return False
            time.sleep(0.25)
        self.login_last_error = f"login service did not become ready within {self.login_start_wait_seconds:.1f}s"
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only local dashboard for the OpenClaw Weixin mobile bridge.")
    parser.add_argument("--host", default="127.0.0.1", help="bind host, defaults to 127.0.0.1")
    parser.add_argument("--port", type=int, default=18808, help="bind port, defaults to 18808")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="bridge config path")
    parser.add_argument("--live-state", type=Path, default=DEFAULT_LIVE_STATE, help="live app-server watcher state path")
    parser.add_argument(
        "--dashboard-activity",
        type=Path,
        default=DEFAULT_DASHBOARD_ACTIVITY,
        help="dashboard activity heartbeat path",
    )
    parser.add_argument("--attachments-dir", type=Path, default=DEFAULT_ATTACHMENTS_DIR, help="dashboard upload attachment directory")
    parser.add_argument("--login-host", default=DEFAULT_LOGIN_HOST, help="login service host for /login proxy")
    parser.add_argument("--login-port", type=int, default=DEFAULT_LOGIN_PORT, help="login service port for /login proxy")
    parser.add_argument("--login-script", type=Path, default=DEFAULT_LOGIN_SCRIPT, help="login service node script for on-demand startup")
    parser.add_argument("--login-runs-dir", type=Path, default=DEFAULT_LOGIN_RUNS, help="login service log directory")
    parser.add_argument("--login-timeout-ms", type=int, default=DEFAULT_LOGIN_TIMEOUT_MS, help="login service timeout after page heartbeat")
    parser.add_argument(
        "--login-start-wait-seconds",
        type=float,
        default=DEFAULT_LOGIN_START_WAIT_SECONDS,
        help="seconds to wait for on-demand login service startup",
    )
    parser.add_argument("--limit", type=int, default=120, help="recent task limit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = DashboardServer(
        (args.host, args.port),
        DashboardHandler,
        args.db,
        args.config,
        max(20, args.limit),
        args.live_state,
        args.dashboard_activity,
        args.attachments_dir,
        args.login_host,
        args.login_port,
        args.login_script,
        args.login_runs_dir,
        args.login_timeout_ms,
        args.login_start_wait_seconds,
    )
    print(f"mobile dashboard: http://{args.host}:{args.port}/")
    print(f"login proxy: http://{args.host}:{args.port}/login/ -> http://{args.login_host}:{args.login_port}/")
    print(f"db: {args.db}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
