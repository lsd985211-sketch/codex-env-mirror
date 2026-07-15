#!/usr/bin/env python3
"""SQLite queue and safety policy for the WeCom mobile bridge."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


RISK_L0 = "L0"
RISK_L1 = "L1"
RISK_L2 = "L2"
RISK_L3 = "L3"

STATUSES = {
    "pending",
    "waiting_confirmation",
    "waiting_capability_passphrase",
    "queued_for_codex",
    "sent_to_codex",
    "processing",
    "done",
    "pushed_to_wecom",
    "push_failed",
    "codex_timeout",
    "failed",
    "cancelled",
    "rejected",
    # Compatibility with v0.1 CLI/tests.
    "claimed",
    "executing",
}

CLOSED_STATUSES = {"done", "pushed_to_wecom", "push_failed", "failed", "cancelled", "rejected", "codex_timeout"}
DEFAULT_MAX_INPUT_CHARS = 2000
DEFAULT_CONFIRM_TTL_SECONDS = 300
DEFAULT_MAX_CONFIRMATION_FAILURES = 3
DEFAULT_COOLDOWN_SECONDS = 10
DEFAULT_MAX_ATTACHMENTS = 10
DEFAULT_EVENT_NOISE_GUARD_SECONDS = 600
NOISY_EVENT_TYPES = {
    "active_poll_observation",
    "active_recovery_retry_scheduled",
    "attachment_supplement_host_rejected",
    "codex_cdp_start_probe_cooldown",
    "dispatch_scan_gate_deferred_continue",
    "delivery_group_member_result_poll_skipped",
    "delivery_retry_scheduled",
    "followup_redelivery_supplement_publish_failed",
    "followup_triggered_waiting_redelivery_deferred",
    "mcp_ack_ignored_missing_base_owner",
    "pending_visible_cdp_unconfirmed_result_not_ready",
    "pre_redelivery_owned_result_deferred",
    "published_supplement_primary_dispatch_suppressed",
    "recovery_active_route_lease_released",
    "status_ack_delivery_queue_entered_suppressed",
    "status_ack_delivery_retry_waiting_suppressed",
    "status_ack_reply_pending_batch_suppressed",
    "status_ack_visible_cdp_busy_suppressed",
    "status_ack_visible_cdp_probe_failed_suppressed",
    "thread_delivery_busy",
    "thread_delivery_probe_failed",
    "thread_delivery_visible_cdp_busy",
    "thread_delivery_visible_cdp_probe_failed",
    "thread_delivery_visible_cdp_stale_os_listener",
    "thread_recovery_marked",
}
DEFAULT_L3_TERMS = [
    "delete",
    "remove",
    "rm ",
    "rmdir",
    "format",
    "wipe",
    "clear database",
    "drop table",
    "ban ",
    "kick ",
    "封禁",
    "踢出",
    "删除",
    "清空",
    "权限",
    "提权",
    "注册表",
    "清库",
    "重置存档",
]
DEFAULT_L2_TERMS = [
    "start server",
    "stop server",
    "restart server",
    "run script",
    "powershell",
    "cmd",
    "modify",
    "edit",
    "write",
    "deploy",
    "启动服务器",
    "关闭服务器",
    "重启服务器",
    "运行脚本",
    "修改",
    "写入",
    "部署",
    "启动",
    "关闭",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_text(text: str, limit: int = 120) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def event_noise_guard_seconds(config: dict[str, Any] | None = None) -> int:
    config = config or {}
    value = config.get("queue", {}).get("event_noise_guard_seconds")
    if value is None:
        value = config.get("openclaw", {}).get("event_noise_guard_seconds")
    try:
        return max(0, int(value if value is not None else DEFAULT_EVENT_NOISE_GUARD_SECONDS))
    except Exception:
        return DEFAULT_EVENT_NOISE_GUARD_SECONDS


def noisy_event_signature(event_type: str, payload: dict[str, Any]) -> str:
    payload = payload or {}
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    visible_state = payload.get("visible_state") if isinstance(payload.get("visible_state"), dict) else {}
    startup = visible_state.get("startup") if isinstance(visible_state.get("startup"), dict) else {}
    signature_payload = {
        "event_type": event_type,
        "reason": payload.get("reason") or detail.get("reason"),
        "thread_id": payload.get("thread_id") or detail.get("thread_id"),
        "status_event_type": payload.get("event_type"),
        "visible_error": visible_state.get("error"),
        "visible_reason": visible_state.get("reason"),
        "startup_reason": startup.get("reason"),
        "startup_host": startup.get("host"),
        "startup_port": startup.get("port"),
    }
    return sha256_text(json.dumps(signature_payload, ensure_ascii=False, sort_keys=True))


def normalize_attachments(attachments: Any) -> list[dict[str, Any]]:
    if attachments is None:
        return []
    if isinstance(attachments, dict):
        attachments = [attachments]
    if not isinstance(attachments, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(attachments[:DEFAULT_MAX_ATTACHMENTS]):
        if not isinstance(item, dict):
            item = {"value": str(item)}
        safe_item = redact_value(item)
        if isinstance(safe_item, dict):
            safe_item.setdefault("index", index)
            normalized.append(safe_item)
    return normalized


@dataclass(frozen=True)
class Classification:
    command: str
    risk_level: str
    requires_confirmation: bool
    allowed_phone_direct: bool
    reason: str


def normalize_command(text: str) -> tuple[str, str]:
    clean = (text or "").strip()
    if not clean:
        return "", ""
    if not clean.startswith("/"):
        return "/ask", clean
    first, _, rest = clean.partition(" ")
    return first.lower(), rest.strip()


def load_risk_terms(config: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
    config = config or {}
    path_value = str(config.get("safety", {}).get("risk_rules_path") or "").strip()
    if not path_value:
        return DEFAULT_L3_TERMS, DEFAULT_L2_TERMS
    try:
        rules = json.loads(Path(path_value).read_text(encoding="utf-8-sig"))
        l3_terms = [str(item).lower() for item in rules.get("l3_terms", []) if str(item).strip()]
        l2_terms = [str(item).lower() for item in rules.get("l2_terms", []) if str(item).strip()]
        return l3_terms or DEFAULT_L3_TERMS, l2_terms or DEFAULT_L2_TERMS
    except Exception:
        return DEFAULT_L3_TERMS, DEFAULT_L2_TERMS


def classify_text(text: str, config: dict[str, Any] | None = None) -> Classification:
    command, rest = normalize_command(text)
    haystack = f"{command} {rest}".lower()
    l3_terms, l2_terms = load_risk_terms(config)
    if any(term in haystack for term in l3_terms):
        return Classification(command or "/ask", RISK_L3, True, False, "high-risk task requires confirmation secret")

    if any(term in haystack for term in l2_terms):
        return Classification(command or "/ask", RISK_L2, False, False, "bounded state-changing task queued for agent processing")

    if command in {"/start", "/help", "/status", "/tasks", "/result"}:
        return Classification(command, RISK_L0, False, True, "read-only status command")

    if command in {"/ask", "/report", "/analyze", "/memory"}:
        return Classification(command, RISK_L1, False, False, "analysis task queued for agent processing")

    if command in {"/confirm", "/cancel"}:
        return Classification(command, RISK_L1, False, True, "queue control command")

    return Classification(command or "/ask", RISK_L1, False, False, "default analysis task")


def build_fingerprint(source: str, external_user: str, text: str, metadata: dict[str, Any] | None) -> str:
    metadata = metadata or {}
    msg_id = str(metadata.get("msg_id") or "")
    create_time = str(metadata.get("create_time") or "")
    receiver_account_id = str(metadata.get("receiver_account_id") or metadata.get("account_id") or "")
    if msg_id:
        raw = f"{source}|{receiver_account_id}|{external_user}|{msg_id}|{create_time}"
    else:
        raw = f"{source}|{receiver_account_id}|{external_user}|{create_time}|{text.strip()}"
    return sha256_text(raw)


class MobileQueue:
    def __init__(self, db_path: str | Path, config: dict[str, Any] | None = None) -> None:
        self.db_path = Path(db_path)
        self.root = self.db_path.parent
        self.config = config or {}
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def session(self) -> sqlite3.Connection:
        db = self.connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _init_db(self) -> None:
        db = sqlite3.connect(self.db_path, timeout=15)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            if self._needs_tasks_migration(db):
                self._migrate_tasks_table(db)
            else:
                self._create_schema(db)
            self._migrate_optional_columns(db)
            self._migrate_users_table(db)
            self._repair_events_foreign_key(db)
            db.commit()
        finally:
            db.close()

    def _needs_tasks_migration(self, db: sqlite3.Connection) -> bool:
        exists = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mobile_tasks'"
        ).fetchone()
        if not exists:
            return False
        columns = {row[1] for row in db.execute("PRAGMA table_info(mobile_tasks)").fetchall()}
        required = {
            "text_sha256",
            "message_fingerprint",
            "confirmation_secret_hash",
            "confirmation_expires_at",
            "confirmed_at",
            "trigger_attempts",
            "codex_thread_id",
            "queued_for_codex_at",
            "sent_to_codex_at",
            "push_status",
            "pushed_at",
        }
        if not required.issubset(columns):
            return True
        schema_row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='mobile_tasks'"
        ).fetchone()
        schema_sql = str(schema_row[0] if schema_row else "")
        return "waiting_capability_passphrase" not in schema_sql

    def _create_schema(self, db: sqlite3.Connection) -> None:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_users (
                id              TEXT PRIMARY KEY,
                source          TEXT NOT NULL,
                external_user   TEXT NOT NULL,
                display_name    TEXT DEFAULT '',
                role            TEXT NOT NULL DEFAULT 'user',
                enabled         INTEGER NOT NULL DEFAULT 1,
                allow_trigger   INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                UNIQUE(source, external_user)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_tasks (
                id                      TEXT PRIMARY KEY,
                source                  TEXT NOT NULL,
                receiver_account_id     TEXT DEFAULT '',
                external_user           TEXT NOT NULL,
                external_conversation   TEXT DEFAULT '',
                command                 TEXT NOT NULL,
                text                    TEXT NOT NULL,
                text_sha256             TEXT NOT NULL DEFAULT '',
                message_fingerprint     TEXT NOT NULL DEFAULT '',
                risk_level              TEXT NOT NULL,
                status                  TEXT NOT NULL,
                requires_confirmation   INTEGER NOT NULL DEFAULT 0,
                confirmation_token      TEXT DEFAULT '',
                confirmation_secret_hash TEXT DEFAULT '',
                confirmation_expires_at TEXT,
                confirmed_at            TEXT,
                trigger_attempts        INTEGER NOT NULL DEFAULT 0,
                codex_thread_id         TEXT DEFAULT '',
                queued_for_codex_at     TEXT,
                sent_to_codex_at        TEXT,
                claimed_by              TEXT DEFAULT '',
                result                  TEXT DEFAULT '',
                error                   TEXT DEFAULT '',
                push_status             TEXT DEFAULT '',
                pushed_at               TEXT,
                metadata_json           TEXT NOT NULL DEFAULT '{}',
                attachments_json        TEXT NOT NULL DEFAULT '[]',
                created_at              TEXT NOT NULL,
                updated_at              TEXT NOT NULL,
                claimed_at              TEXT,
                completed_at            TEXT,
                CHECK(status IN (
                    'pending','waiting_confirmation','waiting_capability_passphrase','queued_for_codex','sent_to_codex',
                    'processing','done','pushed_to_wecom','push_failed','codex_timeout',
                    'failed','cancelled','rejected','claimed','executing'
                ))
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id         TEXT,
                source          TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                payload_json    TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES mobile_tasks(id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_runtime (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
            """
        )
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_tasks_fingerprint ON mobile_tasks(message_fingerprint)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_mobile_tasks_status_created ON mobile_tasks(status, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_mobile_events_task ON mobile_events(task_id, created_at)")
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mobile_events_noise_guard
            ON mobile_events(task_id, source, event_type, created_at)
            """
        )

    def _migrate_optional_columns(self, db: sqlite3.Connection) -> None:
        exists = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mobile_tasks'"
        ).fetchone()
        if not exists:
            return
        columns = {row[1] for row in db.execute("PRAGMA table_info(mobile_tasks)").fetchall()}
        if "attachments_json" not in columns:
            db.execute("ALTER TABLE mobile_tasks ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'")
        if "receiver_account_id" not in columns:
            try:
                db.execute("ALTER TABLE mobile_tasks ADD COLUMN receiver_account_id TEXT DEFAULT ''")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def _migrate_tasks_table(self, db: sqlite3.Connection) -> None:
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup_table = f"mobile_tasks_v01_backup_{suffix}"
        db.execute(f"ALTER TABLE mobile_tasks RENAME TO {backup_table}")
        self._create_schema(db)
        old_rows = db.execute(f"SELECT * FROM {backup_table}").fetchall()
        old_columns = [row[1] for row in db.execute(f"PRAGMA table_info({backup_table})").fetchall()]
        now = utc_now()
        for row in old_rows:
            data = dict(zip(old_columns, row))
            text = data.get("text", "")
            metadata = json.loads(data.get("metadata_json") or "{}")
            fingerprint = build_fingerprint(data.get("source", ""), data.get("external_user", ""), text, metadata)
            db.execute(
                """
                INSERT OR IGNORE INTO mobile_tasks(
                    id, source, receiver_account_id, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status,
                    requires_confirmation, confirmation_token, claimed_by, result, error,
                    metadata_json, created_at, updated_at, claimed_at, completed_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("id"),
                    data.get("source"),
                    data.get("receiver_account_id") or metadata.get("receiver_account_id") or metadata.get("account_id") or "",
                    data.get("external_user"),
                    data.get("external_conversation", ""),
                    data.get("command", "/ask"),
                    text,
                    sha256_text(text),
                    fingerprint,
                    data.get("risk_level", RISK_L1),
                    data.get("status", "pending"),
                    data.get("requires_confirmation", 0),
                    data.get("confirmation_token", ""),
                    data.get("claimed_by", ""),
                    data.get("result", ""),
                    data.get("error", ""),
                    data.get("metadata_json", "{}"),
                    data.get("created_at", now),
                    data.get("updated_at", now),
                    data.get("claimed_at"),
                    data.get("completed_at"),
                ),
            )

    def _mobile_events_fk_target(self, db: sqlite3.Connection) -> str:
        exists = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mobile_events'"
        ).fetchone()
        if not exists:
            return ""
        for row in db.execute("PRAGMA foreign_key_list(mobile_events)").fetchall():
            try:
                if str(row[3]) == "task_id":
                    return str(row[2])
            except Exception:
                continue
        return ""

    def _repair_events_foreign_key(self, db: sqlite3.Connection) -> None:
        task_table = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mobile_tasks'"
        ).fetchone()
        if not task_table:
            return
        events_table = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mobile_events'"
        ).fetchone()
        if not events_table:
            self._create_schema(db)
            return
        if self._mobile_events_fk_target(db) == "mobile_tasks":
            return

        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        repair_table = f"mobile_events_fk_repair_{suffix}"
        db.execute(
            f"""
            CREATE TABLE {repair_table} (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id         TEXT,
                source          TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                payload_json    TEXT NOT NULL DEFAULT '{{}}',
                created_at      TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES mobile_tasks(id)
            )
            """
        )
        db.execute(
            f"""
            INSERT INTO {repair_table}(id, task_id, source, event_type, payload_json, created_at)
            SELECT id, task_id, source, event_type, payload_json, created_at
            FROM mobile_events
            """
        )
        db.execute("DROP TABLE mobile_events")
        db.execute(f"ALTER TABLE {repair_table} RENAME TO mobile_events")
        self._create_schema(db)

    def _migrate_users_table(self, db: sqlite3.Connection) -> None:
        exists = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mobile_users'"
        ).fetchone()
        if not exists:
            self._create_schema(db)
            return
        columns = {row[1] for row in db.execute("PRAGMA table_info(mobile_users)").fetchall()}
        if "allow_trigger" not in columns:
            db.execute("ALTER TABLE mobile_users ADD COLUMN allow_trigger INTEGER NOT NULL DEFAULT 0")

    def pause_file(self) -> Path:
        return self.root / "PAUSE"

    def is_paused(self) -> bool:
        return self.pause_file().exists() or bool(self.config.get("safety", {}).get("paused", False))

    def shadow_mode(self) -> bool:
        return bool(self.config.get("safety", {}).get("shadow_mode", True))

    def max_input_chars(self) -> int:
        return int(self.config.get("safety", {}).get("max_input_chars", DEFAULT_MAX_INPUT_CHARS))

    def allowed_users(self) -> set[str]:
        users = self.config.get("security", {}).get("allowed_users", [])
        return {str(user) for user in users if str(user).strip()}

    def confirmation_secret_hash(self) -> str:
        configured = str(self.config.get("security", {}).get("confirmation_secret_hash", "") or "")
        if configured:
            return configured
        env_name = str(self.config.get("security", {}).get("confirmation_secret_env", "") or "")
        secret = os.environ.get(env_name, "") if env_name else ""
        return sha256_text(secret) if secret else ""

    def confirmation_ttl_seconds(self) -> int:
        return int(self.config.get("security", {}).get("confirmation_ttl_seconds", DEFAULT_CONFIRM_TTL_SECONDS))

    def max_confirmation_failures(self) -> int:
        return int(self.config.get("security", {}).get("max_confirmation_failures", DEFAULT_MAX_CONFIRMATION_FAILURES))

    def cooldown_seconds(self) -> int:
        return int(self.config.get("trigger", {}).get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))

    def ensure_user(self, source: str, external_user: str, display_name: str = "", allow_trigger: bool | None = None) -> str:
        user_id = f"{source}:{external_user}"
        now = utc_now()
        if allow_trigger is None:
            allowed = self.allowed_users()
            allow_trigger = not allowed or external_user in allowed
        with self.session() as db:
            db.execute(
                """
                INSERT INTO mobile_users(
                    id, source, external_user, display_name, allow_trigger, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, external_user) DO UPDATE SET
                    display_name=excluded.display_name,
                    allow_trigger=excluded.allow_trigger,
                    updated_at=excluded.updated_at
                """,
                (user_id, source, external_user, display_name, 1 if allow_trigger else 0, now, now),
            )
        return user_id

    def add_event(self, source: str, event_type: str, payload: dict[str, Any], task_id: str | None = None) -> None:
        safe_payload = redact_payload(payload)
        with self.session() as db:
            guard_seconds = event_noise_guard_seconds(self.config)
            if guard_seconds and event_type in NOISY_EVENT_TYPES:
                now_dt = datetime.now(timezone.utc)
                threshold = (now_dt - timedelta(seconds=guard_seconds)).isoformat()
                signature = noisy_event_signature(event_type, safe_payload)
                row = db.execute(
                    """
                    SELECT id, created_at FROM mobile_events
                    WHERE COALESCE(task_id, '')=COALESCE(?, '')
                      AND source=?
                      AND event_type=?
                      AND created_at>=?
                      AND json_extract(payload_json, '$.noise_signature')=?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (task_id, source, event_type, threshold, signature),
                ).fetchone()
                if row:
                    marker_key = f"event_noise_guard:{row['id']}"
                    marker_raw = db.execute("SELECT value FROM mobile_runtime WHERE key=?", (marker_key,)).fetchone()
                    try:
                        marker = json.loads(marker_raw["value"]) if marker_raw else {}
                    except Exception:
                        marker = {}
                    marker["event_type"] = event_type
                    marker["task_id"] = task_id or ""
                    marker["source"] = source
                    marker["signature"] = signature
                    marker["suppressed_count"] = int(marker.get("suppressed_count") or 0) + 1
                    marker["first_event_id"] = row["id"]
                    marker["first_seen_at"] = marker.get("first_seen_at") or row["created_at"]
                    marker["last_seen_at"] = now_dt.isoformat()
                    db.execute(
                        """
                        INSERT INTO mobile_runtime(key, value, updated_at)
                        VALUES(?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                        """,
                        (marker_key, json.dumps(marker, ensure_ascii=False), now_dt.isoformat()),
                    )
                    return
                safe_payload = dict(safe_payload)
                safe_payload["noise_signature"] = signature
                safe_payload["noise_guard_seconds"] = guard_seconds
            db.execute(
                """
                INSERT INTO mobile_events(task_id, source, event_type, payload_json, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (task_id, source, event_type, json.dumps(safe_payload, ensure_ascii=False), utc_now()),
            )

    def runtime_get(self, key: str) -> str:
        with self.session() as db:
            row = db.execute("SELECT value FROM mobile_runtime WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def runtime_set(self, key: str, value: str) -> None:
        now = utc_now()
        with self.session() as db:
            db.execute(
                """
                INSERT INTO mobile_runtime(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, now),
            )

    def runtime_delete(self, key: str) -> None:
        with self.session() as db:
            db.execute("DELETE FROM mobile_runtime WHERE key=?", (key,))

    def enqueue(
        self,
        text: str,
        source: str = "wecom",
        external_user: str = "",
        external_conversation: str = "",
        metadata: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        text = text or ""
        metadata = metadata or {}
        normalized_attachments = normalize_attachments(attachments)
        external_user = external_user or "unknown"
        now = utc_now()
        receiver_account_id = str(metadata.get("receiver_account_id") or metadata.get("account_id") or "").strip()

        allowed = self.is_user_allowed(external_user)
        if len(text) > self.max_input_chars():
            classification = Classification("/ask", RISK_L3, False, False, "input too long")
            status = "rejected"
        else:
            classification = classify_text(text, self.config)
            if not allowed:
                status = "rejected"
            elif classification.requires_confirmation:
                status = "waiting_confirmation"
            else:
                status = "pending"

        task_id = uuid4().hex[:12]
        secret_hash = self.confirmation_secret_hash()
        token = uuid4().hex[:8] if classification.requires_confirmation and allowed else ""
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(seconds=self.confirmation_ttl_seconds())).isoformat()
            if token
            else None
        )
        fingerprint = build_fingerprint(source, external_user, text, metadata)
        metadata_json = {
            "classification_reason": classification.reason,
            "auth": "verified" if allowed else "unverified",
            "receiver_account_id": receiver_account_id,
            "text_preview": short_text(text),
            **redact_payload(metadata),
        }

        self.ensure_user(source, external_user, allow_trigger=allowed)
        with self.session() as db:
            existing = db.execute(
                "SELECT id, status, risk_level FROM mobile_tasks WHERE message_fingerprint=?",
                (fingerprint,),
            ).fetchone()
            if existing:
                self.add_event(source, "duplicate_ignored", {"existing_id": existing["id"]}, existing["id"])
                return {
                    "id": existing["id"],
                    "duplicate": True,
                    "risk_level": existing["risk_level"],
                    "status": existing["status"],
                    "reason": "duplicate message fingerprint",
                }

            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, receiver_account_id, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status,
                    requires_confirmation, confirmation_token, confirmation_secret_hash,
                    confirmation_expires_at, metadata_json, attachments_json, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    source,
                    receiver_account_id,
                    external_user,
                    external_conversation,
                    classification.command,
                    text,
                    sha256_text(text),
                    fingerprint,
                    classification.risk_level,
                    status,
                    1 if classification.requires_confirmation else 0,
                    token,
                    secret_hash,
                    expires_at,
                    json.dumps(metadata_json, ensure_ascii=False),
                    json.dumps(normalized_attachments, ensure_ascii=False),
                    now,
                    now,
                ),
            )

        reason = classification.reason
        if not allowed:
            reason = "sender not in allowed_users"
        self.add_event(
            source,
            "task_enqueued",
            {
                "status": status,
                "risk_level": classification.risk_level,
                "reason": reason,
                "attachment_count": len(normalized_attachments),
            },
            task_id,
        )
        return {
            "id": task_id,
            "command": classification.command,
            "risk_level": classification.risk_level,
            "status": status,
            "requires_confirmation": classification.requires_confirmation,
            "confirmation_token": token,
            "confirmation_expires_at": expires_at,
            "auth": "verified" if allowed else "unverified",
            "reason": reason,
        }

    def is_user_allowed(self, external_user: str) -> bool:
        allowed = self.allowed_users()
        if not allowed or external_user in allowed:
            return True
        resolver = self.config.get("_is_external_user_allowed")
        if callable(resolver):
            try:
                return bool(resolver(external_user))
            except Exception:
                return False
        return False

    def list_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.session() as db:
            rows = db.execute(
                """
                SELECT id, source, external_user, command, risk_level, status, text,
                       receiver_account_id, attachments_json, result, error, push_status, created_at, updated_at, completed_at
                FROM mobile_tasks
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_pending(self, limit: int = 10, preferred_task_id: str = "") -> list[dict[str, Any]]:
        with self.session() as db:
            if preferred_task_id:
                row = db.execute(
                    """
                    SELECT id, source, external_user, external_conversation, command, risk_level, status, text,
                           receiver_account_id, attachments_json, metadata_json, created_at, updated_at
                    FROM mobile_tasks
                    WHERE status='pending' AND id=?
                    """,
                    (preferred_task_id,),
                ).fetchone()
                if row:
                    return [dict(row)]
            rows = db.execute(
                """
                SELECT id, source, external_user, external_conversation, command, risk_level, status, text,
                       receiver_account_id, attachments_json, metadata_json, created_at, updated_at
                FROM mobile_tasks
                WHERE status='pending'
                ORDER BY
                    CASE risk_level WHEN 'L0' THEN 0 WHEN 'L1' THEN 1 WHEN 'L2' THEN 2 ELSE 3 END,
                    created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_active_codex_delivery_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.session() as db:
            rows = db.execute(
                """
                SELECT id, source, external_user, command, risk_level, status, text,
                       codex_thread_id, receiver_account_id, queued_for_codex_at,
                       sent_to_codex_at, attachments_json, created_at, updated_at
                FROM mobile_tasks
                WHERE status IN ('queued_for_codex','sent_to_codex','processing')
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_sent_to_codex(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.session() as db:
            rows = db.execute(
                """
                SELECT id, source, external_user, command, risk_level, status, text,
                       receiver_account_id, attachments_json, created_at, updated_at
                FROM mobile_tasks
                WHERE status='sent_to_codex'
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.session() as db:
            row = db.execute("SELECT * FROM mobile_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def claim(self, task_id: str, agent: str) -> tuple[bool, str]:
        now = utc_now()
        with self.session() as db:
            row = db.execute("SELECT id, status FROM mobile_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return False, "task not found"
            if row["status"] != "pending":
                return False, f"task is not pending: {row['status']}"
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='claimed', claimed_by=?, claimed_at=?, updated_at=?
                WHERE id=?
                """,
                (agent, now, now, task_id),
            )
        self.add_event("local", "task_claimed", {"agent": agent}, task_id)
        return True, "claimed"

    def _task_route_lease_expired(self, task_row: sqlite3.Row | dict[str, Any], now: datetime | None = None) -> bool:
        def field(name: str) -> Any:
            if isinstance(task_row, sqlite3.Row):
                return task_row[name]
            return task_row.get(name)

        status = str(field("status") or "").strip().lower()
        if status not in {"sent_to_codex", "processing"}:
            return False
        if not str(field("codex_thread_id") or "").strip():
            return True
        now = now or datetime.now(timezone.utc)
        sent_at = parse_iso(str(field("sent_to_codex_at") or field("updated_at") or ""))
        if not sent_at:
            return True
        lease_seconds = int(self.config.get("trigger", {}).get("active_slot_release_after_seconds") or 90)
        return now >= sent_at + timedelta(seconds=max(30, lease_seconds))

    def queue_for_codex(self, task_ids: list[str], thread_id: str, lock_scope: str = "global") -> tuple[bool, str]:
        if self.is_paused():
            return False, "bridge is paused by PAUSE file or config"
        now = utc_now()
        queued_ids: list[str] = []
        lock_scope = (lock_scope or "global").strip().lower()
        with self.session() as db:
            active_rows = db.execute(
                """
                SELECT id, status, codex_thread_id, sent_to_codex_at, updated_at
                FROM mobile_tasks
                WHERE status IN ('queued_for_codex','sent_to_codex','processing')
                """
            ).fetchall()
            active_rows = [row for row in active_rows if not self._task_route_lease_expired(row, datetime.now(timezone.utc))]
            if lock_scope == "thread":
                active_rows = [row for row in active_rows if str(row["codex_thread_id"] or "") == str(thread_id or "")]
            if active_rows:
                return False, f"another mobile task is already active in {lock_scope} scope"

            cooldown_key = "last_codex_trigger_at"
            if lock_scope == "thread" and thread_id:
                cooldown_key = f"last_codex_trigger_at:{thread_id}"
            last = db.execute("SELECT value FROM mobile_runtime WHERE key=?", (cooldown_key,)).fetchone()
            last_dt = parse_iso(last["value"] if last else None)
            if last_dt and datetime.now(timezone.utc) - last_dt < timedelta(seconds=self.cooldown_seconds()):
                return False, "trigger cooldown is active"

            rows = db.execute(
                f"SELECT id, status FROM mobile_tasks WHERE id IN ({','.join('?' for _ in task_ids)})",
                task_ids,
            ).fetchall()
            if len(rows) != len(task_ids):
                return False, "one or more tasks were not found"
            if any(row["status"] != "pending" for row in rows):
                return False, "one or more tasks are not pending"

            for tid in task_ids:
                db.execute(
                    """
                    UPDATE mobile_tasks
                    SET status='queued_for_codex', codex_thread_id=?, queued_for_codex_at=?,
                        trigger_attempts=trigger_attempts+1, updated_at=?
                    WHERE id=?
                    """,
                    (thread_id, now, now, tid),
                )
                queued_ids.append(tid)
            db.execute(
                """
                INSERT INTO mobile_runtime(key, value, updated_at)
                VALUES('last_codex_trigger_at', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (now, now),
            )
            if cooldown_key != "last_codex_trigger_at":
                db.execute(
                    """
                    INSERT INTO mobile_runtime(key, value, updated_at)
                    VALUES(?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (cooldown_key, now, now),
                )
        for tid in queued_ids:
            self.add_event("local", "queued_for_codex", {"thread_id": thread_id}, tid)
        return True, "queued_for_codex"

    def mark_sent_to_codex(self, task_ids: list[str]) -> None:
        now = utc_now()
        with self.session() as db:
            for tid in task_ids:
                db.execute(
                    """
                    UPDATE mobile_tasks
                    SET status='sent_to_codex', sent_to_codex_at=?, updated_at=?
                    WHERE id=? AND status='queued_for_codex'
                    """,
                    (now, now, tid),
                )
        for tid in task_ids:
            self.add_event("local", "sent_to_codex", {"sent_at": now}, tid)

    def mark_processing(self, task_id: str, agent: str = "codex") -> tuple[bool, str]:
        now = utc_now()
        with self.session() as db:
            row = db.execute("SELECT status FROM mobile_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return False, "task not found"
            if row["status"] not in {"sent_to_codex", "queued_for_codex", "pending", "claimed"}:
                return False, f"cannot mark processing from {row['status']}"
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='processing', claimed_by=?, claimed_at=COALESCE(claimed_at, ?), updated_at=?
                WHERE id=?
                """,
                (agent, now, now, task_id),
            )
        self.add_event("local", "task_processing", {"agent": agent}, task_id)
        return True, "processing"

    def confirm(self, task_id: str, secret: str) -> tuple[bool, str]:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self.session() as db:
            row = db.execute(
                """
                SELECT id, status, confirmation_token, confirmation_secret_hash,
                       confirmation_expires_at, risk_level
                FROM mobile_tasks WHERE id=?
                """,
                (task_id,),
            ).fetchone()
            if not row:
                return False, "task not found"
            if row["status"] != "waiting_confirmation":
                return False, f"task is not waiting for confirmation: {row['status']}"
            expires = parse_iso(row["confirmation_expires_at"])
            if expires and now_dt > expires:
                db.execute(
                    "UPDATE mobile_tasks SET status='failed', error=?, updated_at=?, completed_at=? WHERE id=?",
                    ("confirmation expired", now, now, task_id),
                )
                return False, "confirmation expired"
            expected_hash = row["confirmation_secret_hash"] or self.confirmation_secret_hash()
            if not expected_hash:
                return False, "confirmation secret is not configured"
            if sha256_text(secret) != expected_hash:
                self.add_event("wecom", "confirmation_failed", {"reason": "secret mismatch"}, task_id)
                return False, "confirmation secret mismatch"
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='pending', confirmed_at=?, updated_at=?
                WHERE id=?
                """,
                (now, now, task_id),
            )
        self.add_event("wecom", "task_confirmed", {"confirmed_at": now}, task_id)
        return True, "confirmed"

    def latest_waiting_confirmation(self, external_user: str = "") -> dict[str, Any] | None:
        sql = """
            SELECT id, source, external_user, command, risk_level, status, text,
                   receiver_account_id, confirmation_expires_at, created_at, updated_at
            FROM mobile_tasks
            WHERE status='waiting_confirmation'
        """
        params: list[Any] = []
        if external_user:
            sql += " AND external_user=?"
            params.append(external_user)
        sql += " ORDER BY created_at DESC LIMIT 1"
        with self.session() as db:
            row = db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def confirm_latest(self, secret: str, external_user: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        task = self.latest_waiting_confirmation(external_user)
        if not task:
            return False, "no task is waiting for confirmation", None
        ok, message = self.confirm(str(task["id"]), secret)
        if ok:
            return True, message, task
        if "mismatch" in message:
            with self.session() as db:
                failures = db.execute(
                    """
                    SELECT COUNT(*) AS n FROM mobile_events
                    WHERE task_id=? AND event_type='confirmation_failed'
                    """,
                    (task["id"],),
                ).fetchone()["n"]
                if failures >= self.max_confirmation_failures():
                    now = utc_now()
                    db.execute(
                        """
                        UPDATE mobile_tasks
                        SET status='cancelled', error=?, updated_at=?, completed_at=?
                        WHERE id=? AND status='waiting_confirmation'
                        """,
                        ("confirmation failed too many times", now, now, task["id"]),
                    )
                    message = "confirmation failed too many times; task cancelled"
            if message.startswith("confirmation failed too many times"):
                self.add_event(
                    "wecom",
                    "confirmation_cancelled",
                    {"reason": "too many failures"},
                    str(task["id"]),
                )
        return False, message, task

    def cancel(self, task_id: str) -> tuple[bool, str]:
        now = utc_now()
        with self.session() as db:
            row = db.execute("SELECT id, status FROM mobile_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return False, "task not found"
            if row["status"] in CLOSED_STATUSES:
                return False, f"task already closed: {row['status']}"
            db.execute(
                "UPDATE mobile_tasks SET status='cancelled', updated_at=?, completed_at=? WHERE id=?",
                (now, now, task_id),
            )
        self.add_event("wecom", "task_cancelled", {"cancelled_at": now}, task_id)
        return True, "cancelled"

    def complete(self, task_id: str, result: str, status: str = "done") -> None:
        if status not in {"done", "failed"}:
            raise ValueError("status must be done or failed")
        now = utc_now()
        with self.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status=?, result=?, updated_at=?, completed_at=?
                WHERE id=?
                """,
                (status, result, now, now, task_id),
            )
        self.add_event("local", f"task_{status}", {"result_length": len(result)}, task_id)

    def mark_pushed(self, task_id: str, ok: bool, detail: str = "") -> None:
        now = utc_now()
        push_status = "pushed_to_wecom" if ok else "push_failed"
        with self.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status=CASE
                        WHEN status IN ('done','failed') OR COALESCE(result, '') <> '' THEN ?
                        ELSE status
                    END,
                    push_status=?,
                    error=CASE WHEN ? THEN error ELSE ? END,
                    pushed_at=?, updated_at=?
                WHERE id=?
                """,
                (push_status, push_status, 1 if ok else 0, detail, now, now, task_id),
            )
        self.add_event("wecom", "push_result", {"ok": ok, "push_status": push_status, "detail": detail}, task_id)

    def mark_reply_pending(self, task_id: str, detail: str = "") -> None:
        now = utc_now()
        with self.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET push_status='reply_pending',
                    updated_at=?
                WHERE id=?
                """,
                (now, task_id),
            )
        self.add_event("wecom", "reply_pending", {"detail": detail}, task_id)

    def expire_stale_codex_tasks(self) -> int:
        """Deprecated compatibility hook.

        Mobile bridge tasks are no longer closed by a wall-clock timeout. The
        worker now treats Codex/CDP health as the source of truth: unhealthy
        delivery is cancelled and returned to pending; healthy delivery keeps
        waiting for a final result.
        """
        return 0

    def health(self) -> dict[str, Any]:
        with self.session() as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            events_fk_target = self._mobile_events_fk_target(db)
            foreign_key_issues = db.execute("PRAGMA foreign_key_check").fetchall()
            counts = {
                row["status"]: row["n"]
                for row in db.execute("SELECT status, COUNT(*) AS n FROM mobile_tasks GROUP BY status").fetchall()
            }
            users = db.execute("SELECT COUNT(*) FROM mobile_users WHERE allow_trigger=1 AND enabled=1").fetchone()[0]
            schema_ok = events_fk_target in {"", "mobile_tasks"} and not foreign_key_issues
        return {
            "ok": integrity == "ok" and schema_ok,
            "integrity_check": integrity,
            "schema_ok": schema_ok,
            "mobile_events_fk_target": events_fk_target,
            "foreign_key_check_count": len(foreign_key_issues),
            "paused": self.is_paused(),
            "shadow_mode": self.shadow_mode(),
            "allowed_user_count": users,
            "status_counts": counts,
            "db_path": str(self.db_path),
        }


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_payload(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value[:DEFAULT_MAX_ATTACHMENTS]]
    if isinstance(value, str) and len(value) > 500:
        return value[:200] + "...<truncated>"
    return value


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    secret_keys = {"secret", "access_token", "token", "encodingaeskey", "encoding_aes_key", "encrypt"}
    for key, value in payload.items():
        key_lower = str(key).lower()
        if key_lower in secret_keys or "secret" in key_lower or "token" in key_lower:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = redact_value(value)
    return redacted
