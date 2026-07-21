#!/usr/bin/env python3
"""Resident email scheduler for the desktop Codex resource library."""

from __future__ import annotations

import argparse
import csv
import hashlib
import imaplib
import email
import json
import mimetypes
import os
import re
import smtplib
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.header import Header, decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email import policy
from email.utils import format_datetime, formataddr, getaddresses, make_msgid, parsedate_to_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_ROOT = PROJECT_ROOT / "_bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from email_inbox_attachments import attachment_review, attachment_text_context, codex_context_lines, extract_attachments_from_raw, saved_attachments_ready

try:
    from .codex_executable import discover_codex_executable
except ImportError:
    from codex_executable import discover_codex_executable

from platform_paths import resource_library_root  # noqa: E402

RESOURCE_ROOT = resource_library_root()
MAIL_ROOT = RESOURCE_ROOT / "文档" / "邮箱区"
SCHEDULE_ROOT = RESOURCE_ROOT / "文档" / "定时模块"
MAIL_IDENTITY_TXT = MAIL_ROOT / "身份总表.txt"
MAIL_TASK_TXT = MAIL_ROOT / "邮件任务表.txt"
MAIL_INTENT_RULES_TXT = MAIL_ROOT / "邮箱意图规则.txt"
MAIL_TEMPLATE_TABLE_TXT = MAIL_ROOT / "邮件模板表.txt"
MAIL_RECIPIENT_GROUPS_TXT = MAIL_ROOT / "收件人组.txt"
MAIL_RECORDS_ROOT = MAIL_ROOT / "发送记录"
MAIL_BACKUP_ROOT = MAIL_ROOT / "备份"
MAIL_ARCHIVE_ROOT = MAIL_ROOT / "归档"
MAIL_TASK_ARCHIVE_ROOT = MAIL_ARCHIVE_ROOT / "已完成任务"
MAIL_HUMAN_OUTBOX_ROOT = MAIL_ROOT / "发件箱"
MAIL_HUMAN_DRAFT_ROOT = MAIL_ROOT / "草稿箱"
MAIL_HUMAN_INBOX_ROOT = MAIL_ROOT / "收件箱"
SCHEDULE_TASK_TXT = SCHEDULE_ROOT / "任务总表.txt"
SCHEDULE_RECORDS_ROOT = SCHEDULE_ROOT / "执行记录"
SCHEDULE_RUNTIME_ROOT = SCHEDULE_ROOT / "运行态"
EMAIL_STATE_DIR = PROJECT_ROOT / "_bridge" / "shared" / "email_scheduler_state"
EMAIL_LOG_DIR = PROJECT_ROOT / "_bridge" / "logs" / "email_scheduler"
EMAIL_LOG_DIR.mkdir(parents=True, exist_ok=True)
EMAIL_STATE_DIR.mkdir(parents=True, exist_ok=True)
LOCK_PATH = EMAIL_STATE_DIR / "email-scheduler.lock"
HEARTBEAT_PATH = EMAIL_STATE_DIR / "email-scheduler-heartbeat.json"
EMAIL_JOBS_DIR = EMAIL_STATE_DIR / "jobs"
SCHEDULE_RUNS_DIR = EMAIL_STATE_DIR / "schedule_runs"
CONTENT_JOBS_DIR = EMAIL_STATE_DIR / "content_jobs"
DRAFT_ITEMS_DIR = EMAIL_STATE_DIR / "draft_items"
OUTBOX_ITEMS_DIR = EMAIL_STATE_DIR / "outbox_items"
OUTBOX_INDEX_PATH = EMAIL_STATE_DIR / "outbox_index.json"
DELIVERY_JOBS_DIR = EMAIL_STATE_DIR / "delivery_jobs"
SMTP_RECEIPTS_DIR = EMAIL_STATE_DIR / "smtp_receipts"
INBOX_MESSAGES_DIR = EMAIL_STATE_DIR / "inbox_messages"
INBOX_ATTACHMENTS_DIR = EMAIL_STATE_DIR / "inbox_attachments"
INBOX_STATE_PATH = EMAIL_STATE_DIR / "inbox_state.json"
INBOX_JOBS_DIR = EMAIL_STATE_DIR / "inbox_jobs"
INBOX_INDEX_PATH = EMAIL_STATE_DIR / "inbox_index.json"
EMAIL_WORKER_LOCK_PATH = EMAIL_STATE_DIR / "email-worker.lock"
EMAIL_SMOKE_LOCK_PATH = EMAIL_STATE_DIR / "email-smoke.lock"
EMAIL_JOBS_DIR.mkdir(parents=True, exist_ok=True)
SCHEDULE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
CONTENT_JOBS_DIR.mkdir(parents=True, exist_ok=True)
DRAFT_ITEMS_DIR.mkdir(parents=True, exist_ok=True)
OUTBOX_ITEMS_DIR.mkdir(parents=True, exist_ok=True)
DELIVERY_JOBS_DIR.mkdir(parents=True, exist_ok=True)
SMTP_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
INBOX_MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
INBOX_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
INBOX_JOBS_DIR.mkdir(parents=True, exist_ok=True)
MAX_LOG_BYTES = 2 * 1024 * 1024
try:
    BEIJING = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    BEIJING = timezone(timedelta(hours=8))
STARTUP_BASELINE_PATH = PROJECT_ROOT / "_bridge" / "codex_startup_baseline.json"
CODEx_EXE = discover_codex_executable(startup_baseline=STARTUP_BASELINE_PATH)
DEFAULT_CODEx_TIMEOUT_SECONDS = 1800
DEFAULT_LOOP_SECONDS = 60
CODEX_MCP_PROFILE_NONE = "none"
CODEX_MCP_PROFILE_RESEARCH = "research"
CODEX_MCP_PROFILE_LOCAL_READ = "local_read"
CODEX_MCP_PROFILE_MAINTENANCE = "maintenance"
CODEX_MCP_PROFILE_BROWSER_GUI = "browser_gui"
CODEX_MCP_PROFILE_FULL = "full"
CODEX_MCP_PROFILES = {
    CODEX_MCP_PROFILE_NONE,
    CODEX_MCP_PROFILE_RESEARCH,
    CODEX_MCP_PROFILE_LOCAL_READ,
    CODEX_MCP_PROFILE_MAINTENANCE,
    CODEX_MCP_PROFILE_BROWSER_GUI,
    CODEX_MCP_PROFILE_FULL,
}
CODEX_FULL_CONFIG_PROFILES = {CODEX_MCP_PROFILE_RESEARCH, CODEX_MCP_PROFILE_FULL}
CONTENT_JOB_MAX_ATTEMPTS = 3
CONTENT_JOB_RETRY_BASE_SECONDS = 300
DELIVERY_JOB_MAX_ATTEMPTS = 3
DELIVERY_JOB_RETRY_BASE_SECONDS = 300
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENT_TOTAL_BYTES = 45 * 1024 * 1024
INBOX_DEFAULT_ACCOUNT = "3633922805@qq.com"
INBOX_POLL_LIMIT = 10
INBOX_WORKER_MAX_CODEX_JOBS = 1
MAIL_TASK_HEADERS = ["任务名", "任务类型", "触发方式", "目标", "执行动作", "状态", "责任身份", "说明"]
TASK_LINE_BREAK_ESCAPE = "␤"
INBOX_JOB_QUEUED = "queued"
INBOX_JOB_PROCESSING = "processing"
INBOX_JOB_REPLY_TASK_CREATED = "reply_task_created"
INBOX_JOB_REPLY_DRAFTED = "reply_drafted"
INBOX_JOB_NEEDS_REVIEW = "needs_review"
INBOX_JOB_FAILED = "failed"
INBOX_JOB_DEAD_LETTER = "dead_letter"
INBOX_JOB_PROCESSED = "processed"
UNIFIED_SCHEDULER_TASKS = SCHEDULE_ROOT / "运行态" / "统一调度" / "maintenance_tasks.json"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_FAILED = "failed"
STATUS_CONTENT_FAILED = "content_failed"
STATUS_CONTENT_READY = "content_ready"
STATUS_DELIVERY_QUEUED = "delivery_queued"
STATUS_DELIVERY_RUNNING = "delivery_running"
STATUS_PARTIAL_FAILED = "partial_failed"
STATUS_SENT = "sent"
STATUS_SKIPPED = "skipped"
STATUS_DONE = "done"
STATUS_DEAD_LETTER = "dead_letter"
STATUS_DRAFT = "draft"
STATUS_ARCHIVED = "archived"
OUTBOX_READY = "ready"
OUTBOX_SENT = "sent"
OUTBOX_EXPIRED = "expired"
OUTBOX_STALE = "stale"
OUTBOX_BLOCKED = "blocked"
RUNNABLE_STATUSES = {STATUS_QUEUED}
RETRYABLE_STATUSES = {STATUS_FAILED}
TERMINAL_STATUSES = {STATUS_SENT, STATUS_SKIPPED, STATUS_DONE, STATUS_DEAD_LETTER, STATUS_DRAFT, STATUS_ARCHIVED}
SMTP_HEADER_CHARSET = "utf-8"
SMTP_BODY_CHARSET = "utf-8"
SMTP_BODY_CTE = "base64"
EXISTING_RUN_STATUSES = {
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_CONTENT_FAILED,
    STATUS_CONTENT_READY,
    STATUS_DELIVERY_QUEUED,
    STATUS_DELIVERY_RUNNING,
    STATUS_SENT,
    STATUS_SKIPPED,
    STATUS_DONE,
    STATUS_PARTIAL_FAILED,
    STATUS_DEAD_LETTER,
    STATUS_DRAFT,
    STATUS_ARCHIVED,
}
EXISTING_DELIVERY_STATUSES = {STATUS_QUEUED, STATUS_RUNNING, STATUS_SENT, STATUS_SKIPPED, STATUS_ARCHIVED, STATUS_DEAD_LETTER}
SENT_MARKER_STATUSES = {STATUS_SENT}
GENERATION_REFERENCE_CHAR_LIMIT = 8000


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps({"pid": os.getpid(), "started_at": now_beijing().isoformat()}, ensure_ascii=False))
        self.handle.flush()
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


@dataclass
class Identity:
    name: str
    description: str = ""
    alias: str = ""
    scenario: str = ""
    notes: str = ""
    accounts: list[str] = field(default_factory=list)
    smtp: list[str] = field(default_factory=list)
    imap: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)

    @property
    def default_account(self) -> str:
        return self.accounts[0] if self.accounts else ""

    @property
    def smtp_host(self) -> str:
        return self.smtp[0] if len(self.smtp) >= 1 else ""

    @property
    def smtp_port(self) -> int:
        if len(self.smtp) >= 2:
            m = re.search(r"(\d+)", self.smtp[1])
            if m:
                return int(m.group(1))
        return 465

    @property
    def smtp_encryption(self) -> str:
        return self.smtp[2] if len(self.smtp) >= 3 else "SSL/TLS"

    @property
    def auth_code(self) -> str:
        return clean_labeled_value(self.smtp[3]) if len(self.smtp) >= 4 else ""

    @property
    def imap_host(self) -> str:
        return clean_labeled_value(self.imap[0]) if len(self.imap) >= 1 else infer_imap_host(self.default_account)

    @property
    def imap_port(self) -> int:
        if len(self.imap) >= 2:
            m = re.search(r"(\d+)", self.imap[1])
            if m:
                return int(m.group(1))
        return 993

    @property
    def imap_encryption(self) -> str:
        return clean_labeled_value(self.imap[2]) if len(self.imap) >= 3 else "SSL/TLS"

    @property
    def imap_auth_code(self) -> str:
        if len(self.imap) >= 4:
            return clean_labeled_value(self.imap[3])
        return self.auth_code


def now_beijing() -> datetime:
    return datetime.now(tz=BEIJING)


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BEIJING)
    return dt.astimezone(BEIJING)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.exists() else ""


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def human_mailbox_path(root: Path, item_id: str, status: str, subject: str = "") -> Path:
    month = now_beijing().strftime("%Y-%m")
    folder = root / month / slugify(status or "unknown")
    stem = slugify(f"{item_id}-{subject}"[:120])
    return folder / f"{stem}.md"


def human_mail_item_markdown(kind: str, payload: dict[str, Any]) -> str:
    if kind == "inbox":
        attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
        attachment_lines = "\n".join(
            f"- {item.get('filename', '') or '(unnamed)'} `{item.get('content_type', '')}` {item.get('size_bytes', 0)} bytes"
            for item in attachments
            if isinstance(item, dict)
        ) or "- none"
        return "\n".join(
            [
                f"# {payload.get('subject') or payload.get('inbound_message_id') or '收件'}",
                "",
                "- 类型：inbox",
                f"- 状态：{payload.get('status', '')}",
                f"- 入站ID：{payload.get('inbound_message_id', '')}",
                f"- 来源账号：{payload.get('source_account', '')}",
                f"- 发件人：{', '.join(payload.get('from', [])) if isinstance(payload.get('from'), list) else payload.get('from', '')}",
                f"- 收件人：{', '.join(payload.get('to', [])) if isinstance(payload.get('to'), list) else payload.get('to', '')}",
                f"- 入箱时间：{payload.get('received_at', '')}",
                f"- Message-ID：{payload.get('message_id_header', '')}",
                f"- 风险：{payload.get('risk_level', '')}",
                "",
                "## 附件",
                "",
                attachment_lines,
                "",
                "## 正文",
                "",
                str(payload.get("body_text") or payload.get("body_preview") or "").strip() or "(空)",
                "",
            ]
        )
    lines = [
        f"# {payload.get('subject') or payload.get('task_name') or kind}",
        "",
        f"- 类型：{kind}",
        f"- 状态：{payload.get('status', '')}",
        f"- 任务：{payload.get('task_name', '')}",
        f"- 调度ID：{payload.get('schedule_run_id', '')}",
        f"- 发件身份：{payload.get('sender_identity', '')}",
        f"- 发件账号：{payload.get('sender_account', '')}",
        f"- 收件人：{', '.join(payload.get('recipients', [])) if isinstance(payload.get('recipients'), list) else payload.get('recipients', '')}",
        f"- 计划时间：{payload.get('scheduled_at', '')}",
    ]
    if payload.get("reason") or payload.get("stale_reason") or payload.get("blocked_reason"):
        lines.append(f"- 原因：{payload.get('reason') or payload.get('stale_reason') or payload.get('blocked_reason')}")
    if payload.get("missing_fields"):
        lines.append(f"- 缺失字段：{', '.join(str(item) for item in payload.get('missing_fields', []))}")
    if payload.get("assumptions"):
        lines.append(f"- 假设：{', '.join(str(item) for item in payload.get('assumptions', []))}")
    lines.extend(["", "## 正文", "", str(payload.get("body") or "").strip() or "(空)", ""])
    return "\n".join(lines)


def sync_human_mailbox_item(kind: str, payload: dict[str, Any]) -> Path:
    if os.environ.get("EMAIL_SCHEDULER_DISABLE_HUMAN_MIRROR") == "1":
        return Path()
    if kind == "draft":
        root = MAIL_HUMAN_DRAFT_ROOT
        item_id = str(payload.get("draft_item_id") or payload.get("schedule_run_id") or "draft")
    elif kind == "inbox":
        root = MAIL_HUMAN_INBOX_ROOT
        item_id = str(payload.get("inbound_message_id") or payload.get("inbox_job_id") or "inbox")
    else:
        root = MAIL_HUMAN_OUTBOX_ROOT
        item_id = str(payload.get("outbox_item_id") or payload.get("schedule_run_id") or "outbox")
    path = human_mailbox_path(root, item_id, str(payload.get("status") or ""), str(payload.get("subject") or ""))
    for old_path in root.rglob(f"{slugify(item_id)}*.md") if root.exists() else []:
        if old_path != path:
            try:
                old_path.unlink()
            except OSError:
                pass
    write_text(path, human_mail_item_markdown(kind, payload))
    return path


def ensure_human_mailbox_roots() -> None:
    if os.environ.get("EMAIL_SCHEDULER_DISABLE_HUMAN_MIRROR") == "1":
        return
    for root, title in ((MAIL_HUMAN_OUTBOX_ROOT, "发件箱"), (MAIL_HUMAN_DRAFT_ROOT, "草稿箱"), (MAIL_HUMAN_INBOX_ROOT, "收件箱")):
        root.mkdir(parents=True, exist_ok=True)
        readme = root / "README.md"
        if not readme.exists():
            write_text(
                readme,
                f"# {title}\n\n这里是邮箱系统运行态的人工可读镜像。系统真实状态仍以 `_bridge/shared/email_scheduler_state/` 下的 JSON 为准。\n",
            )


def rotate_log_if_needed(path: Path, max_bytes: int = MAX_LOG_BYTES) -> None:
    if not path.exists() or path.stat().st_size <= max_bytes:
        return
    rotated = path.with_name(f"{path.stem}-{now_beijing().strftime('%Y%m%d-%H%M%S')}{path.suffix}")
    path.replace(rotated)


def append_scheduler_log(message: str) -> None:
    log_path = EMAIL_LOG_DIR / "email-scheduler.log"
    rotate_log_if_needed(log_path)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(f"[{now_beijing().isoformat()}] {message}\n")


def write_heartbeat(extra: dict[str, Any] | None = None) -> None:
    payload = {
        "ok": True,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "updated_at": now_beijing().isoformat(),
    }
    if extra:
        payload.update(extra)
    write_text(HEARTBEAT_PATH, json.dumps(payload, ensure_ascii=False, indent=2))


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(read_text(path))
    except Exception:
        return {}


def job_is_retry_ready(payload: dict[str, Any], now: datetime | None = None) -> bool:
    retry_after = parse_datetime(payload.get("retry_after"))
    return retry_after is None or (now or now_beijing()) >= retry_after


def mark_retry_or_dead_letter(
    payload: dict[str, Any],
    *,
    error: str,
    max_attempts: int,
    retry_base_seconds: int,
) -> dict[str, Any]:
    attempts = int(payload.get("attempt_count") or 0)
    payload["finished_at"] = now_beijing().isoformat()
    payload["last_error"] = error
    if attempts >= max_attempts:
        payload["status"] = STATUS_DEAD_LETTER
        payload["dead_letter_at"] = now_beijing().isoformat()
        payload["retry_after"] = ""
        payload["retry_exhausted"] = True
    else:
        payload["status"] = STATUS_FAILED
        payload["retry_after"] = (now_beijing() + timedelta(seconds=retry_base_seconds * attempts)).isoformat()
        payload["retry_exhausted"] = False
    return payload


def clear_failure_fields(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("last_error", "retry_after", "retry_exhausted", "dead_letter_at"):
        payload.pop(key, None)
    return payload


def is_smoke_artifact(value: Any) -> bool:
    text = str(value or "")
    return text.startswith("smoke-draft-test-") or text.startswith("smoke-multi-recipient-")


def is_smoke_stage_payload(path: Path, payload: dict[str, Any]) -> bool:
    if is_smoke_artifact(path.stem):
        return True
    for key in (
        "schedule_run_id",
        "content_job_id",
        "draft_item_id",
        "outbox_item_id",
        "delivery_job_id",
        "task_name",
    ):
        if is_smoke_artifact(payload.get(key)):
            return True
    return False


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        for attempt in range(8):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff\-]+", "-", text.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return cleaned or "item"


def clean_labeled_value(value: str) -> str:
    text = (value or "").strip()
    if "：" in text:
        return text.split("：", 1)[1].strip()
    if ":" in text:
        return text.split(":", 1)[1].strip()
    return text


def infer_imap_host(account: str) -> str:
    address = (account or "").strip().lower()
    if address.endswith("@qq.com"):
        return "imap.qq.com"
    if address.endswith("@gmail.com"):
        return "imap.gmail.com"
    if address.endswith("@outlook.com") or address.endswith("@hotmail.com") or address.endswith("@live.com"):
        return "outlook.office365.com"
    return ""


def encode_mail_header(value: str) -> str:
    return sanitize_header_value(Header(sanitize_header_value(value), SMTP_HEADER_CHARSET).encode(), limit=1000)


def sanitize_header_value(value: str, limit: int = 120) -> str:
    text = re.sub(r"[\r\n\t]+", " ", value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if text else "Codex 自动邮件"


def parse_identity_table(path: Path) -> dict[str, Identity]:
    lines = read_text(path).splitlines()
    identities: dict[str, Identity] = {}
    current: Identity | None = None
    mode: str | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## 身份 "):
            if current:
                identities[current.name] = current
            current = Identity(name="")
            mode = None
            continue
        if current is None:
            continue
        if line.startswith("### 对应账号"):
            mode = "accounts"
            continue
        if line.startswith("### 对应 SMTP"):
            mode = "smtp"
            continue
        if line.startswith("### 对应 IMAP"):
            mode = "imap"
            continue
        if line.startswith("### 发送要求"):
            mode = "requirements"
            continue
        if line.startswith("- "):
            value = line[2:].strip()
            if mode == "accounts":
                current.accounts.append(value)
            elif mode == "smtp":
                current.smtp.append(value)
            elif mode == "imap":
                current.imap.append(value)
            elif mode == "requirements":
                current.requirements.append(value)
            elif "：" in value:
                key, val = value.split("：", 1)
                key = key.strip()
                val = val.strip()
                if key in {"身份名", "备注名"}:
                    current.name = val or current.name
                elif key == "说明":
                    current.description = val
                elif key == "适用场景":
                    current.scenario = val
                elif key == "注意事项":
                    current.notes = val
            continue

    if current and current.name:
        identities[current.name] = current
    return identities


def split_task_table_blocks(text: str, headers: list[str] | None = None) -> tuple[str, list[list[str]]]:
    headers = headers or MAIL_TASK_HEADERS
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return "\t".join(headers), []
    header = lines[0]
    blocks: list[list[str]] = []
    current: list[str] = []
    min_tabs = max(1, len(header.split("\t")) - 1)
    for line in lines[1:]:
        if line.count("\t") >= min_tabs:
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return header, blocks


def parse_task_block(header: str, block: list[str]) -> dict[str, str]:
    if not block:
        return {}
    reader = csv.DictReader([header, block[0]], delimiter="\t")
    row = next(reader, {})
    parsed = {str(k).strip(): str(v or "").strip() for k, v in row.items() if k is not None}
    headers = header.split("\t")
    note_key = "说明" if "说明" in parsed else (headers[-1] if headers else "")
    if note_key and len(block) > 1:
        existing = parsed.get(note_key, "")
        parsed[note_key] = (existing + "\n" + "\n".join(block[1:])).strip()
    if note_key and parsed.get(note_key):
        parsed[note_key] = parsed[note_key].replace(TASK_LINE_BREAK_ESCAPE, "\n")
    return parsed


def parse_task_table(path: Path) -> list[dict[str, str]]:
    text = read_text(path).strip()
    if not text:
        return []
    if path.resolve() == MAIL_TASK_TXT.resolve():
        header, blocks = split_task_table_blocks(text, MAIL_TASK_HEADERS)
        return [row for row in (parse_task_block(header, block) for block in blocks) if row]
    lines = [line for line in text.splitlines() if line.strip()]
    reader = csv.DictReader(lines, delimiter="\t")
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({str(k).strip(): str(v).strip() for k, v in row.items() if k is not None})
    return rows


def parse_recipient_groups(path: Path = MAIL_RECIPIENT_GROUPS_TXT) -> dict[str, list[str]]:
    rows = parse_task_table(path)
    groups: dict[str, list[str]] = {}
    for row in rows:
        name = (row.get("组名") or "").strip()
        members = (row.get("成员") or "").strip()
        if name:
            groups[name] = [part.strip() for part in re.split(r"[，,;/]+", members) if part.strip()]
    return groups


def read_generation_reference(path: Path, limit: int = GENERATION_REFERENCE_CHAR_LIMIT) -> str:
    text = read_text(path).strip()
    return text[:limit]


def find_mail_template(template_id: str) -> dict[str, str]:
    if not template_id:
        return {}
    rows = parse_task_table(MAIL_TEMPLATE_TABLE_TXT)
    for row in rows:
        if (row.get("模板ID") or "").strip() == template_id:
            return row
    return {}


def serialize_task_row(row: dict[str, str]) -> str:
    values: list[str] = []
    for header_name in MAIL_TASK_HEADERS:
        value = str(row.get(header_name, ""))
        value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
        values.append(value.replace("\n", TASK_LINE_BREAK_ESCAPE))
    return "\t".join(values)


def append_task_row(path: Path, row: dict[str, str]) -> None:
    existing = read_text(path).strip()
    header, blocks = split_task_table_blocks(existing, MAIL_TASK_HEADERS)
    if not header:
        header = "\t".join(MAIL_TASK_HEADERS)
    task_name = row.get("任务名", "").strip()
    kept = [header]
    for block in blocks:
        parsed = parse_task_block(header, block)
        if (parsed.get("任务名") or "").strip() == task_name:
            continue
        kept.append(serialize_task_row(parsed))
    kept.append(serialize_task_row(row))
    write_text(path, "\n".join(kept) + "\n")


TIMESTAMP_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2})(?::\d{2})?")


def extract_trigger_time(text: str) -> datetime | None:
    if not text:
        return None
    match = TIMESTAMP_RE.search(text)
    if not match:
        return None
    value = f"{match.group('date')} {match.group('time')}:00"
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
    except ValueError:
        return None


def parse_intent_time(time_text: str) -> tuple[datetime | None, str, str]:
    text = (time_text or "").strip()
    if not text:
        return None, "", "missing_time"
    direct = extract_trigger_time(text)
    if direct:
        return direct, "单次", ""
    now = now_beijing()
    lowered = text.lower()
    if text in {"现在", "立即", "即刻"} or lowered == "now":
        return now, "立即", ""
    delay_match = re.search(r"(\d+)\s*(分钟|分|小时|时|minute|minutes|hour|hours)\s*后", text, re.IGNORECASE)
    if delay_match:
        amount = int(delay_match.group(1))
        unit = delay_match.group(2).lower()
        delta = timedelta(hours=amount) if unit in {"小时", "时", "hour", "hours"} else timedelta(minutes=amount)
        return now + delta, "延时", ""
    hour_match = re.search(r"(?<!\d)(\d{1,2})(?:点|:)(?:(\d{1,2}))?", text)
    if hour_match:
        hour = int(hour_match.group(1))
        minute = int(hour_match.group(2) or 0)
        base = now
        if "明天" in text:
            base = now + timedelta(days=1)
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if "今天" not in text and "明天" not in text and candidate < now:
            candidate += timedelta(days=1)
        return candidate, "单次", ""
    return None, "", f"unparsed_time: {time_text}"


def content_requires_fresh_context(content: str) -> bool:
    lowered = (content or "").lower()
    return any(
        marker in lowered
        for marker in (
            "实时",
            "最新",
            "联网",
            "搜索",
            "今日",
            "今天",
            "当前",
            "近期",
            "评析",
            "快讯",
            "新闻",
            "research",
            "latest",
            "current",
            "search",
        )
    )


def analyze_intent_fields(content: str, subject: str = "", scheduled_at: datetime | None = None) -> dict[str, Any]:
    pseudo = {"任务名": "", "执行动作": content, "说明": content}
    content_mode = detect_content_mode(pseudo)
    report_provider = detect_report_provider(pseudo) if content_mode == "command_report" else ""
    fresh_context = content_requires_fresh_context(content)
    explicit_static = any(
        marker in (content or "").lower()
        for marker in ("固定正文", "静态正文", "static")
    )
    if explicit_static:
        content_mode = "static"
    if content_mode == "static" and fresh_context and not explicit_static:
        content_mode = "codex"
    if content_mode == "command_report":
        template_id = "performance_report" if report_provider == "performance_report" else "command_report"
        codex_mcp_profile = CODEX_MCP_PROFILE_MAINTENANCE
        content_freshness = "realtime"
        priority = 10
    elif content_mode == "codex":
        template_id = "realtime_ai_review" if fresh_context else "codex_generation"
        codex_mcp_profile = CODEX_MCP_PROFILE_RESEARCH if fresh_context else CODEX_MCP_PROFILE_NONE
        content_freshness = "realtime" if fresh_context else "static"
        priority = 10 if fresh_context else 20
    elif content_mode == "attachment_only":
        template_id = "attachment_only"
        codex_mcp_profile = CODEX_MCP_PROFILE_NONE
        content_freshness = "static"
        priority = 30
    else:
        if scheduled_at and scheduled_at <= now_beijing() + timedelta(seconds=5):
            template_id = "immediate_static_send"
        elif scheduled_at:
            template_id = "scheduled_static_send"
        else:
            template_id = "fixed_notice"
        codex_mcp_profile = CODEX_MCP_PROFILE_NONE
        content_freshness = "static"
        priority = 20

    subject_text = subject.strip() or (
        "电脑性能报告" if report_provider == "performance_report" else sanitize_header_value(content[:40] or "自动邮件", 60)
    )
    return {
        "schema": "mail_intent_analysis.v1",
        "content_mode": content_mode,
        "report_provider": report_provider,
        "template_id": template_id,
        "codex_mcp_profile": codex_mcp_profile,
        "content_freshness": content_freshness,
        "content_expires_at": "",
        "priority": str(priority),
        "subject": subject_text,
        "creation_policy": "deterministic_intent_rules_v1",
        "analysis_source": "creation_time_intent_normalization",
        "fresh_context_required": fresh_context,
        "scheduled_at": scheduled_at.isoformat() if scheduled_at else "",
    }


def infer_content_mode_from_text(content: str) -> tuple[str, str, str]:
    analysis = analyze_intent_fields(content)
    return analysis["content_mode"], analysis["report_provider"], analysis["template_id"]


def normalized_intent_note_parts(analysis: dict[str, Any]) -> list[str]:
    keys = [
        "content_mode",
        "template_id",
        "subject",
        "report_provider",
        "codex_mcp_profile",
        "content_freshness",
        "content_expires_at",
        "priority",
        "creation_policy",
    ]
    parts: list[str] = []
    for key in keys:
        value = str(analysis.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    return parts


def resolve_intent_recipients(target: str, identities: dict[str, Identity]) -> list[str]:
    groups = parse_recipient_groups()
    result: list[str] = []
    for part in [item.strip() for item in re.split(r"[，,;/]+", target or "") if item.strip()]:
        if part in groups:
            result.extend(groups[part])
        else:
            result.append(part)
    if not result and target:
        result.append(target)
    # Validate that identities/groups can become addresses, but keep identity names in task target.
    unresolved = [item for item in result if not identity_by_name(identities, item) and "@" not in item and item not in groups]
    if unresolved:
        return result
    return result


def identity_for_account(identities: dict[str, Identity], account: str) -> Identity | None:
    normalized = account.strip().lower()
    if not normalized:
        return None
    for identity in identities.values():
        if any(item.strip().lower() == normalized for item in identity.accounts):
            return identity
    return None


def auto_identity_name_for_account(account: str, identities: dict[str, Identity]) -> str:
    base = account.strip()
    if not base:
        return ""
    candidate = base
    index = 2
    while candidate in identities:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def append_minimal_identity(account: str, role_hint: str, identities: dict[str, Identity]) -> str:
    existing = identity_for_account(identities, account)
    if existing:
        return existing.name
    name = auto_identity_name_for_account(account, identities)
    if not name:
        return ""
    text = read_text(MAIL_IDENTITY_TXT).rstrip()
    next_index = len(identities) + 1
    block = f"""

## 身份 {next_index}
- 身份名：{name}
- 说明：自动创建的邮箱身份
- 备注名：{name}
- 适用场景：{role_hint or '邮件往来'}
- 注意事项：自动创建；如需作为发件身份，请补齐 SMTP 配置和发送要求

### 对应账号
- {account}

### 对应 SMTP

### 发送要求
- 自动创建身份，待人工补充备注和规则
"""
    write_text(MAIL_IDENTITY_TXT, (text + block + "\n").lstrip())
    identities[name] = Identity(
        name=name,
        description="自动创建的邮箱身份",
        alias=name,
        scenario=role_hint or "邮件往来",
        notes="自动创建；如需作为发件身份，请补齐 SMTP 配置和发送要求",
        accounts=[account],
    )
    return name


def materialize_participant_identities(sender_name: str, target: str, identities: dict[str, Identity]) -> dict[str, Any]:
    created: list[dict[str, str]] = []
    resolved_sender = identity_by_name(identities, sender_name)
    if not resolved_sender and "@" in sender_name:
        name = append_minimal_identity(sender_name.strip(), "发件身份", identities)
        if name:
            created.append({"account": sender_name.strip(), "identity": name, "role": "sender"})
    for item in [part.strip() for part in re.split(r"[，,;/]+", target or "") if part.strip()]:
        if "@" not in item:
            continue
        if identity_for_account(identities, item):
            continue
        name = append_minimal_identity(item, "收件身份", identities)
        if name:
            created.append({"account": item, "identity": name, "role": "recipient"})
    return {"created": created, "count": len(created)}


def build_intent_task(
    target: str,
    content: str,
    time_text: str,
    sender_name: str = "主发送者",
    subject: str = "",
    task_name: str = "",
) -> dict[str, Any]:
    tasks, identities = load_world()
    sender = identity_by_name(identities, sender_name) or identity_by_name(identities, "主发送者")
    recipients = resolve_intent_recipients(target, identities)
    scheduled_at, trigger, time_error = parse_intent_time(time_text)
    analysis = analyze_intent_fields(content, subject=subject, scheduled_at=scheduled_at)
    content_mode = analysis["content_mode"]
    report_provider = analysis["report_provider"]
    template_id = analysis["template_id"]
    target_text = ",".join(recipients) if recipients else target
    subject_text = analysis["subject"]
    if scheduled_at:
        stable_name = task_name.strip() or f"{scheduled_at.strftime('%Y-%m-%d-%H%M')}-{slugify(subject_text)}-{slugify(target_text)}"
        schedule_text = scheduled_at.strftime("%Y-%m-%d %H:%M")
    else:
        stable_name = task_name.strip() or f"待定-{slugify(subject_text)}-{slugify(target_text)}"
        schedule_text = ""
    action = {
        "codex": "到点由 Codex 实时生成正文并发送",
        "command_report": f"到点调用报告 provider {report_provider} 生成正文并发送",
        "static": "到点发送固定/模板正文",
        "attachment_only": "到点发送附件或极简正文",
    }.get(content_mode, "到点发送邮件")
    note_parts = [
        f"北京时间 {schedule_text} 触发" if schedule_text else "触发时间未确定",
        *normalized_intent_note_parts(analysis),
    ]
    if content_mode == "static":
        note_parts.append(f"静态正文：{content}")
    else:
        note_parts.append(content)
    row = {
        "任务名": stable_name,
        "任务类型": "固定时间任务" if trigger in {"单次", "立即"} else "延时任务",
        "触发方式": trigger or "未定",
        "目标": target_text,
        "执行动作": action,
        "状态": "启用" if scheduled_at else "草稿",
        "责任身份": sender.name if sender else sender_name,
        "说明": "，".join(note_parts),
    }
    return {
        "ok": bool(scheduled_at and sender and recipients and not time_error),
        "task": row,
        "inferred": {
            "sender": sender.name if sender else "",
            "sender_account": sender.default_account if sender else "",
            "recipients": recipients,
            "scheduled_at": scheduled_at.isoformat() if scheduled_at else "",
            "trigger": trigger,
            "content_mode": content_mode,
            "report_provider": report_provider,
            "template_id": template_id,
            "subject": subject_text,
            "codex_mcp_profile": analysis["codex_mcp_profile"],
            "content_freshness": analysis["content_freshness"],
            "content_expires_at": analysis["content_expires_at"],
            "priority": analysis["priority"],
            "creation_policy": analysis["creation_policy"],
            "analysis_source": analysis["analysis_source"],
        },
        "issues": [item for item in [
            time_error,
            "" if sender else f"sender not found: {sender_name}",
            "" if recipients else f"recipient not found: {target}",
        ] if item],
    }


def intent_dry_run(target: str, content: str, time_text: str, sender_name: str, subject: str, task_name: str = "") -> dict[str, Any]:
    result = build_intent_task(target, content, time_text, sender_name=sender_name, subject=subject, task_name=task_name)
    result["dry_run"] = True
    result["writes_files"] = False
    result["sends_mail"] = False
    decision = automation_decision_for_intent(result)
    result["request_valid"] = bool(result.get("ok"))
    result["executable_authorized"] = bool(result.get("ok")) and bool(decision.get("can_auto_create"))
    result["automation_decision"] = decision
    return result


def intent_create(target: str, content: str, time_text: str, sender_name: str, subject: str, task_name: str = "") -> dict[str, Any]:
    _, identities = load_world()
    identity_materialization = materialize_participant_identities(sender_name, target, identities)
    result = build_intent_task(target, content, time_text, sender_name=sender_name, subject=subject, task_name=task_name)
    decision = automation_decision_for_intent(result)
    result["automation_decision"] = decision
    if not result["ok"]:
        result["created"] = False
        result["writes_files"] = False
        result["sends_mail"] = False
        result["identity_materialization"] = identity_materialization
        return result
    append_task_row(MAIL_TASK_TXT, result["task"])
    result["created"] = True
    result["task_table"] = str(MAIL_TASK_TXT)
    result["writes_files"] = True
    result["sends_mail"] = False
    result["identity_materialization"] = identity_materialization
    return result


def sender_can_deliver(sender_name: str, identities: dict[str, Identity]) -> tuple[bool, str]:
    sender = identity_by_name(identities, sender_name)
    if not sender:
        return False, "sender_identity_missing"
    if not sender.default_account:
        return False, "sender_account_missing"
    if not sender.smtp_host or not sender.auth_code:
        return False, "sender_smtp_missing"
    return True, ""


def automation_decision_for_intent(intent: dict[str, Any]) -> dict[str, Any]:
    _, identities = load_world()
    inferred = intent.get("inferred") if isinstance(intent.get("inferred"), dict) else {}
    content_mode = str(inferred.get("content_mode") or "").strip()
    trigger = str(inferred.get("trigger") or "").strip()
    sender_name = str(inferred.get("sender") or "").strip()
    recipients = inferred.get("recipients") if isinstance(inferred.get("recipients"), list) else []
    issues = list(intent.get("issues") if isinstance(intent.get("issues"), list) else [])
    sender_ready, sender_reason = sender_can_deliver(sender_name, identities)
    if sender_reason:
        issues.append(sender_reason)
    if not recipients:
        issues.append("recipient_missing")

    if issues or not intent.get("ok"):
        automation_class = "review_required"
        action = "block_create"
        environment_owns = False
        codex_role = "clarify_missing_fields_or_fix_mail_identity"
        can_auto_create = False
    elif content_mode in {"static", "command_report"}:
        automation_class = "environment_auto"
        action = "create_task"
        environment_owns = True
        codex_role = "none"
        can_auto_create = True
    elif content_mode == "codex":
        automation_class = "codex_deferred"
        action = "create_task_for_runtime_generation"
        environment_owns = True
        codex_role = "runtime_body_generation_or_research_only"
        can_auto_create = True
    elif content_mode == "attachment_only":
        automation_class = "review_required"
        action = "block_create"
        environment_owns = False
        codex_role = "review_attachment_paths_and_message_context"
        can_auto_create = False
        issues.append("attachment_task_requires_review")
    else:
        automation_class = "review_required"
        action = "block_create"
        environment_owns = False
        codex_role = "classify_unknown_content_mode"
        can_auto_create = False
        issues.append(f"unknown_content_mode:{content_mode}")

    return {
        "schema": "mail_intent_automation_decision.v1",
        "automation_class": automation_class,
        "action": action,
        "environment_owns_repetition": environment_owns,
        "codex_role": codex_role,
        "can_auto_create": can_auto_create,
        "can_auto_dispatch_if_due": can_auto_create and trigger in {"立即", "现在", "即刻"},
        "requires_review": automation_class == "review_required",
        "issues": list(dict.fromkeys(str(item) for item in issues if str(item).strip())),
        "policy": "environment handles deterministic mail task creation and queues; Codex is reserved for analysis, generation, research, or unclear/high-risk cases",
    }


def intent_submit(
    target: str,
    content: str,
    time_text: str,
    sender_name: str,
    subject: str,
    task_name: str = "",
    *,
    dispatch_if_due: bool = False,
    confirm_dispatch: str = "",
) -> dict[str, Any]:
    preview = intent_dry_run(target, content, time_text, sender_name, subject, task_name)
    decision = preview["automation_decision"]
    if not decision.get("can_auto_create"):
        preview["created"] = False
        preview["writes_files"] = False
        preview["sends_mail"] = False
        preview["next_step"] = "review_or_clarify_before_task_table_write"
        return preview

    created = intent_create(target, content, time_text, sender_name, subject, task_name)
    created["automation_decision"] = decision
    created["auto_submitted"] = bool(created.get("created"))
    dispatch_result: dict[str, Any] = {"skipped": True, "reason": "dispatch_not_requested"}
    if dispatch_if_due:
        if confirm_dispatch != "SEND":
            dispatch_result = {"ok": False, "skipped": True, "reason": "confirm_dispatch_SEND_required"}
        elif not decision.get("can_auto_dispatch_if_due"):
            dispatch_result = {"ok": False, "skipped": True, "reason": "not_due_immediate_or_not_auto_dispatchable"}
        else:
            dispatch_result = dispatch_due()
    created["dispatch"] = dispatch_result
    return created


def target_text_from_draft(draft: dict[str, Any]) -> str:
    recipients = draft.get("recipients") if isinstance(draft.get("recipients"), list) else []
    return ",".join(str(item).strip() for item in recipients if str(item).strip())


def draft_static_body(draft: dict[str, Any]) -> str:
    body = str(draft.get("body") or "").strip()
    if body:
        return body
    generation = draft.get("generation_result") if isinstance(draft.get("generation_result"), dict) else {}
    return str(generation.get("body_text") or "").strip()


def build_static_task_from_draft(draft: dict[str, Any], scheduled_at: datetime, trigger: str, task_name: str = "") -> dict[str, str]:
    subject = sanitize_header_value(str(draft.get("subject") or draft.get("task_name") or "草稿重发"), 80)
    body = draft_static_body(draft)
    target = target_text_from_draft(draft)
    sender = str(draft.get("sender_identity") or "主发送者").strip() or "主发送者"
    stable_name = task_name.strip() or f"{scheduled_at.strftime('%Y-%m-%d-%H%M')}-{slugify(subject)}-{slugify(target)}"
    template_id = "immediate_static_send" if trigger == "立即" else "scheduled_static_send"
    note_parts = [
        f"北京时间 {scheduled_at.strftime('%Y-%m-%d %H:%M')} 触发",
        "content_mode=static",
        f"template_id={template_id}",
        f"subject={subject}",
        f"codex_mcp_profile={CODEX_MCP_PROFILE_NONE}",
        "content_freshness=static",
        "priority=20",
        "creation_policy=draft_resend_static_v1",
        f"source_draft_item_id={draft.get('draft_item_id', '')}",
        f"静态正文：{body}",
    ]
    return {
        "任务名": stable_name,
        "任务类型": "固定时间任务",
        "触发方式": trigger or "单次",
        "目标": target,
        "执行动作": "到点发送固定/模板正文",
        "状态": "启用",
        "责任身份": sender,
        "说明": "，".join(note_parts),
    }


def validate_resend_task_row(task: dict[str, str], identities: dict[str, Identity], expected_body: str, expected_trigger: str) -> list[str]:
    issues: list[str] = []
    runtime = build_task_runtime(task, identities)
    metadata = task_metadata(task)
    if not runtime.get("mail_task"):
        issues.append("task_row_not_mail_task")
    if not runtime.get("scheduled_at"):
        issues.append("task_row_time_unparseable")
    if runtime.get("content_mode") != "static":
        issues.append(f"task_row_content_mode_not_static:{runtime.get('content_mode')}")
    if metadata.get("template_id") not in {"immediate_static_send", "scheduled_static_send"}:
        issues.append(f"task_row_template_invalid:{metadata.get('template_id')}")
    if metadata.get("codex_mcp_profile") != CODEX_MCP_PROFILE_NONE:
        issues.append(f"task_row_mcp_profile_invalid:{metadata.get('codex_mcp_profile')}")
    if not runtime.get("sender"):
        issues.append("task_row_sender_unresolved")
    if not runtime.get("recipients"):
        issues.append("task_row_recipients_unresolved")
    if extract_static_body(task).strip() != expected_body.strip():
        issues.append("task_row_body_mismatch")
    if expected_trigger == "立即" and task.get("触发方式") != "立即":
        issues.append("task_row_immediate_trigger_mismatch")
    if expected_trigger != "立即" and task.get("触发方式") == "立即":
        issues.append("task_row_scheduled_trigger_mismatch")
    return issues


def validate_resend_draft(draft: dict[str, Any], identities: dict[str, Identity]) -> list[str]:
    issues: list[str] = []
    if not draft:
        return ["draft_not_found"]
    if draft.get("status") not in {STATUS_DRAFT, STATUS_DEAD_LETTER}:
        issues.append(f"draft_status_not_resendable:{draft.get('status')}")
    if not target_text_from_draft(draft):
        issues.append("recipient_missing")
    if not draft_static_body(draft):
        issues.append("body_missing")
    sender_name = str(draft.get("sender_identity") or "主发送者").strip() or "主发送者"
    sender = identity_by_name(identities, sender_name)
    if not sender:
        issues.append(f"sender_not_found:{sender_name}")
    elif not (sender.default_account and sender.smtp_host and sender.auth_code):
        issues.append(f"sender_smtp_missing:{sender_name}")
    return issues


def archive_resubmitted_draft(draft: dict[str, Any], reason: str, result: dict[str, Any]) -> None:
    draft["status"] = STATUS_ARCHIVED
    draft["archived_at"] = now_beijing().isoformat()
    draft["archive_reason"] = reason
    draft["resubmit_result"] = result
    write_stage("draft_item", draft)
    sync_human_mailbox_item("draft", draft)


def resend_draft(draft_item_id: str, time_text: str = "", confirm_resend: str = "", task_name: str = "", dry_run: bool = False) -> dict[str, Any]:
    if confirm_resend != "YES":
        return {"ok": False, "blocked": True, "reason": "resend-draft requires --confirm-resend YES"}
    draft = read_stage("draft_item", draft_item_id)
    tasks, identities = load_world()
    issues = validate_resend_draft(draft, identities)
    scheduled_at, trigger, time_error = parse_intent_time(time_text or "立即")
    if time_error:
        issues.append(time_error)
    if issues:
        return {"ok": False, "created": False, "issues": issues, "draft_item_id": draft_item_id}
    assert scheduled_at is not None
    effective_task_name = task_name.strip()
    if not effective_task_name and trigger != "立即":
        effective_task_name = str(draft.get("task_name") or "").strip()
    task = build_static_task_from_draft(draft, scheduled_at, trigger, task_name=effective_task_name)
    task_issues = validate_resend_task_row(task, identities, draft_static_body(draft), trigger)
    if task_issues:
        return {"ok": False, "created": False, "issues": task_issues, "draft_item_id": draft_item_id, "task": task}
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "mode": "immediate_outbox" if trigger == "立即" else "scheduled_task_table",
            "draft_item_id": draft_item_id,
            "task": task,
            "scheduled_at": scheduled_at.isoformat(),
            "writes_files": False,
            "sends_mail": False,
        }
    if trigger == "立即":
        runtime = build_task_runtime(task, identities)
        schedule_run_id = runtime["record_key"]
        run_payload = {
            "schedule_run_id": schedule_run_id,
            "status": STATUS_CONTENT_READY,
            "content_mode": "static",
            "task_name": task["任务名"],
            "task": task,
            "created_at": now_beijing().isoformat(),
            "scheduled_at": runtime["scheduled_at"].isoformat() if runtime["scheduled_at"] else "",
            "recipients": runtime["recipients"],
            "source_draft_item_id": draft_item_id,
        }
        run_path = write_stage("schedule_run", run_payload)
        outbox = create_outbox_item(task, runtime, schedule_run_id, None, draft_static_body(draft))
        refresh_outbox_index()
        worker_result = start_email_worker() if has_runnable_stage_jobs() else {"ok": True, "started": False, "reason": "no_runnable_jobs"}
        result = {
            "ok": True,
            "mode": "immediate_outbox",
            "draft_item_id": draft_item_id,
            "schedule_run_id": schedule_run_id,
            "task_name": task["任务名"],
            "outbox_item": outbox,
            "worker": worker_result,
            "job_path": str(run_path),
            "writes_files": True,
            "sends_mail": False,
        }
        archive_resubmitted_draft(draft, "resubmitted_immediate_outbox", result)
        return result
    append_task_row(MAIL_TASK_TXT, task)
    result = {
        "ok": True,
        "mode": "scheduled_task_table",
        "draft_item_id": draft_item_id,
        "task_name": task["任务名"],
        "scheduled_at": scheduled_at.isoformat(),
        "trigger": trigger,
        "task_table": str(MAIL_TASK_TXT),
        "writes_files": True,
        "sends_mail": False,
    }
    archive_resubmitted_draft(draft, "resubmitted_scheduled_task", result)
    return result


def is_mail_task(task: dict[str, str]) -> bool:
    name = task.get("任务名", "")
    target = task.get("目标", "")
    action = task.get("执行动作", "")
    note = task.get("说明", "")
    combined = " ".join([name, target, action, note])
    if not any(keyword in combined for keyword in ("邮件", "发信", "发送")):
        return False
    generic_targets = ("邮箱区", "其它模块", "调度桥", "执行端")
    if target and any(marker in target for marker in generic_targets) and "@" not in target:
        return False
    return True


def schedule_is_explicit(task: dict[str, str]) -> bool:
    combined = " ".join([task.get("任务名", ""), task.get("触发方式", ""), task.get("说明", ""), task.get("执行动作", "")])
    return bool(
        TIMESTAMP_RE.search(combined)
        or re.search(r"(?<!\d)(?:每天|每日|每周|每月|单次|一次|today|tomorrow)", combined, re.IGNORECASE)
    )


def is_sent_marker_present(task_name: str, scheduled_at: datetime | None) -> bool:
    if scheduled_at is None:
        return False
    record_key = build_record_key(task_name, scheduled_at)
    for path in SMTP_RECEIPTS_DIR.glob(f"{record_key}*.json"):
        payload = read_json(path)
        if payload.get("status") in SENT_MARKER_STATUSES:
            return True
    for root in (SCHEDULE_RECORDS_ROOT, MAIL_RECORDS_ROOT):
        if not root.exists():
            continue
        for path in root.rglob(f"{record_key}*"):
            if path.is_file():
                return True
    return False


def is_recipient_sent_marker_present(task_name: str, scheduled_at: datetime | None, recipient: str) -> bool:
    if scheduled_at is None or not recipient:
        return False
    record_key = build_record_key(task_name, scheduled_at)
    target = recipient.strip().lower()
    for path in SMTP_RECEIPTS_DIR.glob(f"{record_key}*.json"):
        payload = read_json(path)
        if payload.get("status") not in SENT_MARKER_STATUSES:
            continue
        if str(payload.get("recipient", "")).strip().lower() == target:
            return True
    return False


def normalize_mail_subject(subject: str) -> str:
    return re.sub(r"\s+", "", str(subject or "")).strip().lower()


def normalize_mail_address(address: str) -> str:
    return str(address or "").strip().lower()


def task_is_superseded_by_successful_resend(task: dict[str, str], runtime: dict[str, Any]) -> bool:
    scheduled_at = runtime.get("scheduled_at")
    if not scheduled_at:
        return False
    old_run_id = runtime.get("record_key") or build_record_key(task.get("任务名", ""), scheduled_at)
    related = related_stage_jobs(str(old_run_id))
    run_payload = related.get("schedule_run") or {}
    if not run_payload:
        return False
    if run_payload.get("status") in {STATUS_SENT, STATUS_SKIPPED, STATUS_DONE, STATUS_ARCHIVED}:
        return True
    if has_runnable_related_stage_jobs(related):
        return False
    old_recipients = {normalize_mail_address(item) for item in runtime.get("recipients", []) if item}
    if not old_recipients:
        old_recipients = {
            normalize_mail_address(item)
            for item in run_payload.get("recipients", [])
            if isinstance(item, str) and item
        }
    if not old_recipients:
        return False
    old_subject = normalize_mail_subject(runtime.get("subject") or build_subject(task))
    old_sender = normalize_mail_address(
        (runtime.get("sender").default_account if runtime.get("sender") else "") or run_payload.get("sender_account", "")
    )
    if not old_subject or not old_sender:
        return False
    covered_recipients: set[str] = set()
    for path in SMTP_RECEIPTS_DIR.glob("*.json"):
        receipt = read_json(path)
        if receipt.get("status") not in SENT_MARKER_STATUSES:
            continue
        if normalize_mail_address(receipt.get("sender_account", "")) != old_sender:
            continue
        if normalize_mail_subject(receipt.get("subject", "")) != old_subject:
            continue
        sent_at = parse_datetime(receipt.get("sent_at")) or parse_datetime(receipt.get("scheduled_at"))
        if sent_at and sent_at < scheduled_at:
            continue
        recipient = normalize_mail_address(receipt.get("recipient", ""))
        if recipient in old_recipients:
            covered_recipients.add(recipient)
    return old_recipients <= covered_recipients


def build_record_key(task_name: str, scheduled_at: datetime) -> str:
    return f"{scheduled_at.strftime('%Y-%m-%d-%H%M')}-{slugify(task_name)}"


def build_artifact_key(*parts: str) -> str:
    return slugify("-".join(part for part in parts if part))


def smtp_receipt_path(task_name: str, scheduled_at: datetime | None, sender_account: str, recipient: str) -> Path:
    record_key = build_record_key(task_name, scheduled_at or now_beijing())
    return SMTP_RECEIPTS_DIR / f"{build_artifact_key(record_key, sender_account, recipient)}.json"


def write_smtp_receipt(
    *,
    task: dict[str, str],
    scheduled_at: datetime | None,
    sender: Identity,
    recipient: str,
    subject: str,
    body: str,
    send_result: dict[str, Any],
) -> Path:
    path = smtp_receipt_path(task.get("任务名", ""), scheduled_at, sender.default_account, recipient)
    payload = {
        "receipt_id": path.stem,
        "status": STATUS_SENT,
        "task_name": task.get("任务名", ""),
        "scheduled_at": scheduled_at.isoformat() if scheduled_at else "",
        "sender_identity": sender.name,
        "sender_account": sender.default_account,
        "recipient": recipient,
        "subject": subject,
        "message_id": send_result.get("message_id", ""),
        "sent_at": now_beijing().isoformat(),
        "body_preview": body[:1500],
    }
    atomic_write_json(path, payload)
    return path


def identity_by_name(identities: dict[str, Identity], name: str) -> Identity | None:
    name = (name or "").strip()
    if not name:
        return None
    if name in identities:
        return identities[name]
    for identity in identities.values():
        if name in {identity.alias, identity.description, identity.scenario}:
            return identity
    if "@" in name:
        existing = identity_for_account(identities, name)
        if existing:
            return existing
    if "@" in name:
        return Identity(name=name, accounts=[name])
    return None


def recipient_addresses(target_text: str, identities: dict[str, Identity]) -> list[str]:
    candidates = [part.strip() for part in re.split(r"[，,;/]+", target_text or "") if part.strip()]
    result: list[str] = []
    for candidate in candidates or [target_text.strip()]:
        if not candidate:
            continue
        resolved = identity_by_name(identities, candidate)
        if resolved and resolved.default_account:
            result.append(resolved.default_account)
        elif "@" in candidate:
            result.append(candidate)
    return list(dict.fromkeys(result))


def task_metadata(task: dict[str, str]) -> dict[str, str]:
    combined = " ".join([task.get("任务名", ""), task.get("执行动作", ""), task.get("说明", "")])
    metadata: dict[str, str] = {}
    keys = (
        "content_mode",
        "template_id",
        "report_provider",
        "subject",
        "codex_mcp_profile",
        "mcp_profile",
        "content_freshness",
        "content_expires_at",
        "priority",
        "creation_policy",
        "sender_identity",
        "recipient_identity",
        "attachments",
        "mail_kind",
        "reply_to_inbound_message_id",
        "reply_to_message_id_header",
        "reply_to_sender",
        "reply_to_subject",
        "thread_policy",
        "inbound_payload_ref",
    )
    for key in keys:
        match = re.search(rf"(?:^|[，,]\s*){key}\s*=\s*([^，,\n]+)", combined, re.IGNORECASE)
        if match:
            metadata[key] = match.group(1).strip()
    return metadata


def parse_attachment_paths(task: dict[str, str]) -> list[Path]:
    combined = "\n".join([task.get("任务名", ""), task.get("执行动作", ""), task.get("说明", "")])
    metadata_value = task_metadata(task).get("attachments", "")
    candidates: list[str] = []
    if metadata_value:
        candidates.append(metadata_value)
    for match in re.finditer(r"(?:附件|attachment)\s*[：:]\s*([^\n]+)", combined, re.IGNORECASE):
        candidates.append(match.group(1).strip())
    paths: list[Path] = []
    for candidate in candidates:
        for raw in re.split(r"[;；|，,]", candidate):
            text = raw.strip().strip("\"'")
            if not text:
                continue
            path = Path(os.path.expandvars(text)).expanduser()
            paths.append(path)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def validate_attachment_paths(paths: list[Path]) -> list[str]:
    issues: list[str] = []
    total = 0
    for path in paths:
        if not path.exists():
            issues.append(f"attachment_missing:{path}")
            continue
        if not path.is_file():
            issues.append(f"attachment_not_file:{path}")
            continue
        size = path.stat().st_size
        total += size
        if size > MAX_ATTACHMENT_BYTES:
            issues.append(f"attachment_too_large:{path}")
    if total > MAX_ATTACHMENT_TOTAL_BYTES:
        issues.append("attachments_total_too_large")
    return issues


def infer_content_mode_from_task_keywords(task: dict[str, str]) -> str:
    combined = " ".join([task.get("任务名", ""), task.get("执行动作", ""), task.get("说明", "")]).lower()
    if any(marker in combined for marker in ("性能报告", "performance_report", "脚本报告", "系统报告", "电脑性能报告")):
        return "command_report"
    if any(marker in combined for marker in ("无需codex", "不需要codex", "固定正文", "静态正文", "static", "template")):
        return "static"
    if any(marker in combined for marker in ("附件", "attachment_only")) and not any(marker in combined for marker in ("正文", "生成", "评析")):
        return "attachment_only"
    if any(marker in combined for marker in ("codex", "生成", "实时", "评析", "总结", "润色")):
        return "codex"
    return "static"


def resolve_content_mode(task: dict[str, str], allow_keyword_fallback: bool = True) -> tuple[str, str]:
    mode = task_metadata(task).get("content_mode", "")
    if mode in {"codex", "command_report", "static", "attachment_only"}:
        return mode, "metadata"
    if allow_keyword_fallback:
        return infer_content_mode_from_task_keywords(task), "keyword_fallback"
    return "", "missing"


def detect_content_mode(task: dict[str, str]) -> str:
    return resolve_content_mode(task, allow_keyword_fallback=True)[0]


def extract_static_body(task: dict[str, str]) -> str:
    text = task.get("说明", "").strip() or task.get("执行动作", "").strip() or task.get("任务名", "").strip()
    for marker in ("固定正文：", "静态正文：", "正文：", "body:"):
        if marker in text:
            return text.split(marker, 1)[1].strip()
    return text


def detect_report_provider(task: dict[str, str]) -> str:
    explicit = task_metadata(task).get("report_provider", "")
    if explicit:
        return explicit
    combined = " ".join([task.get("任务名", ""), task.get("执行动作", ""), task.get("说明", "")]).lower()
    if any(marker in combined for marker in ("性能报告", "performance_report", "电脑性能报告")):
        return "performance_report"
    return ""


def resolve_codex_mcp_profile(task: dict[str, str]) -> tuple[str, str]:
    metadata = task_metadata(task)
    explicit = metadata.get("codex_mcp_profile", "") or metadata.get("mcp_profile", "")
    if explicit:
        profile = explicit.strip().lower()
        if profile not in CODEX_MCP_PROFILES:
            raise ValueError(f"unsupported codex_mcp_profile: {explicit}")
        return profile, "metadata"
    combined = " ".join([task.get("任务名", ""), task.get("执行动作", ""), task.get("说明", "")]).lower()
    if any(marker in combined for marker in ("实时", "联网", "最新", "搜索", "外部资料", "research")):
        return CODEX_MCP_PROFILE_RESEARCH, "keyword_fallback"
    return CODEX_MCP_PROFILE_NONE, "default"


def build_codex_exec_command(tmp_path: Path, mcp_profile: str) -> list[str]:
    cmd = [
        CODEx_EXE,
        "exec",
        "-C",
        str(PROJECT_ROOT),
        "--output-last-message",
        str(tmp_path),
        "--ephemeral",
        "--skip-git-repo-check",
    ]
    if mcp_profile not in CODEX_FULL_CONFIG_PROFILES:
        # Keep config.toml so background generation uses the same working
        # model/provider as the desktop session; only rules are ignored to seal
        # task context.
        cmd.extend(["--ignore-rules", "--sandbox", "read-only"])
    cmd.append("-")
    return cmd


def run_report_provider(provider: str, timeout_seconds: int = 180) -> str:
    if provider == "performance_report":
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py"),
            "performance",
            "metrics",
            "--observe-seconds",
            "3",
            "--top",
            "8",
            "--profile",
            "quick",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "performance report failed")[:1000])
        return format_performance_report(proc.stdout.strip())
    raise ValueError(f"unsupported report provider: {provider}")


def format_performance_report(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    generated_at = payload.get("generated_at", "")
    profile = payload.get("profile", "")
    lines = [
        "电脑性能报告",
        "",
        f"- 生成时间：{generated_at}",
        f"- 采样模式：{profile or 'unknown'}",
        f"- 采样窗口：{payload.get('observe_seconds', '')} 秒",
        "",
        "## CPU 热点",
    ]
    for item in payload.get("top_cpu", [])[:8]:
        lines.append(
            f"- {item.get('name')} pid={item.get('pid')} "
            f"class={item.get('class')} cpu≈{item.get('cpu_percent_estimate')}% "
            f"内存≈{item.get('working_set_mb')}MB"
        )
    lines.extend(["", "## 分类汇总"])
    for item in payload.get("class_summary", [])[:8]:
        lines.append(
            f"- {item.get('class')}：进程 {item.get('process_count')} 个，"
            f"CPU≈{item.get('cpu_percent_estimate')}%，"
            f"内存≈{item.get('working_set_mb')}MB"
        )
    return "\n".join(lines).strip()


def render_task_prompt(task: dict[str, str], sender: Identity, recipient: Identity | None, scheduled_at: datetime | None) -> str:
    request = build_mail_generation_request(task, sender, recipient, scheduled_at)
    return render_mail_generation_prompt(request)


def build_mail_generation_request(
    task: dict[str, str],
    sender: Identity,
    recipient: Identity | None,
    scheduled_at: datetime | None,
    mcp_profile: str | None = None,
    mcp_profile_source: str | None = None,
) -> dict[str, Any]:
    metadata = task_metadata(task)
    resolved_profile, resolved_source = resolve_codex_mcp_profile(task)
    mcp_profile = mcp_profile or resolved_profile
    mcp_profile_source = mcp_profile_source or resolved_source
    template_id = metadata.get("template_id", "")
    content_freshness = metadata.get("content_freshness", "")
    target = task.get("目标", "").strip()
    recipient_name = recipient.name if recipient else target
    recipient_accounts = [recipient.default_account] if recipient and recipient.default_account else []
    inbound_payload_ref = metadata.get("inbound_payload_ref") or metadata.get("reply_to_inbound_message_id", "")
    inbound_payload = load_inbox_message(inbound_payload_ref) if inbound_payload_ref else {}
    return {
        "schema": "mail_generation_request.v1",
        "context_policy": "strict_mail_generation_context",
        "allowed_sources": [
            "this_request_json",
            "mail_task_table",
            "identity_table",
            "mail_intent_rules",
            "mail_template_table",
            "recipient_groups",
            "allowlisted_local_tool_outputs",
            "explicit_user_input_in_task_fields",
            "immutable_inbound_message_payload",
        ],
        "forbidden_sources": [
            "current_chat_context",
            "implicit_conversation_memory",
            "unstated_user_preferences",
            "guessed_facts",
        ],
        "reference_tables": {
            "mail_intent_rules": read_generation_reference(MAIL_INTENT_RULES_TXT),
            "mail_template_table": read_generation_reference(MAIL_TEMPLATE_TABLE_TXT),
            "recipient_groups": parse_recipient_groups(),
        },
        "selected_template": find_mail_template(template_id),
        "inbound_message": {
            "payload_ref": inbound_payload_ref,
            "source_path": str(inbox_message_path(inbound_payload_ref)) if inbound_payload_ref else "",
            "subject": inbound_payload.get("subject", ""),
            "from": inbound_payload.get("from", []),
            "to": inbound_payload.get("to", []),
            "received_at": inbound_payload.get("received_at", ""),
            "message_id_header": inbound_payload.get("message_id_header", ""),
            "body_text": inbound_payload.get("body_text", ""),
            "attachments": inbound_payload.get("attachments", []),
        } if inbound_payload_ref else {},
        "evidence_policy": {
            "profile": mcp_profile,
            "content_freshness": content_freshness,
            "live_research_allowed": mcp_profile == CODEX_MCP_PROFILE_RESEARCH or content_freshness == "realtime",
            "allowed_live_research": [
                "official statistics and market reports",
                "public web sources available through this Codex execution",
                "tool outputs produced during this generation run",
            ],
            "evidence_ids_rule": "Put source labels, urls, or tool-output ids used during this generation in used_evidence_ids.",
            "not_missing_when_live_research_allowed": [
                "allowlisted_local_tool_outputs",
                "precomputed_research_evidence",
            ],
        },
        "task": {
            "name": task.get("任务名", "").strip(),
            "trigger": task.get("触发方式", "").strip(),
            "scheduled_at": scheduled_at.isoformat() if scheduled_at else "",
            "target": target,
            "action": task.get("执行动作", "").strip(),
            "notes": task.get("说明", "").strip(),
            "metadata": metadata,
            "codex_mcp_profile": mcp_profile,
            "codex_mcp_profile_source": mcp_profile_source,
        },
        "sender": {
            "identity": sender.name,
            "account": sender.default_account,
        },
        "recipient": {
            "identity": recipient_name,
            "accounts": recipient_accounts,
        },
        "output_contract": {
            "format": "json_only",
            "required_keys": ["subject", "body_text", "used_evidence_ids", "assumptions", "missing_fields", "should_send"],
            "send_blocking_keys": ["assumptions", "missing_fields"],
        },
    }


def render_mail_generation_prompt(request: dict[str, Any]) -> str:
    return (
        "你是邮件正文生成器。只能依据下面 JSON 输入包生成结果。\n"
        "当前聊天上下文、历史对话印象、未声明偏好、猜测事实都不是事实来源。\n"
        "如果输入包没有给出某个发送所必需且无法由允许工具取得的事实，把字段名写入 missing_fields。\n"
        "如果 evidence_policy.live_research_allowed=true，你应主动用允许的联网/研究工具取得时效性证据，"
        "并把来源写入 used_evidence_ids；不要因为没有预置 allowlisted_local_tool_outputs 就阻断。\n"
        "如果你必须做假设，把假设写入 assumptions，并把 should_send 设为 false。\n"
        "只输出一个 JSON 对象，不要输出 Markdown、代码块、解释或额外文本。\n\n"
        "JSON 输入包：\n"
        f"{json.dumps(request, ensure_ascii=False, indent=2)}\n\n"
        "输出 JSON schema：\n"
        "{\n"
        '  "subject": "邮件主题",\n'
        '  "body_text": "可直接发送给人的邮件正文",\n'
        '  "used_evidence_ids": [],\n'
        '  "assumptions": [],\n'
        '  "missing_fields": [],\n'
        '  "should_send": true\n'
        "}\n"
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError("Codex generation output is not JSON")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Codex generation output must be a JSON object")
    return value


def parse_mail_generation_result(raw: str) -> dict[str, Any]:
    value = _extract_json_object(raw)
    subject = sanitize_header_value(str(value.get("subject") or ""), 120)
    body_text = str(value.get("body_text") or "").strip()
    assumptions = value.get("assumptions") if isinstance(value.get("assumptions"), list) else []
    missing_fields = value.get("missing_fields") if isinstance(value.get("missing_fields"), list) else []
    used_evidence_ids = value.get("used_evidence_ids") if isinstance(value.get("used_evidence_ids"), list) else []
    should_send = bool(value.get("should_send", False))
    if not body_text:
        missing_fields = [*missing_fields, "body_text"]
    if not subject:
        missing_fields = [*missing_fields, "subject"]
    if assumptions or missing_fields:
        should_send = False
    return {
        "schema": "mail_generation_result.v1",
        "subject": subject,
        "body_text": body_text,
        "used_evidence_ids": [str(item) for item in used_evidence_ids],
        "assumptions": [str(item) for item in assumptions],
        "missing_fields": [str(item) for item in missing_fields],
        "should_send": should_send,
        "raw_output_preview": raw[:2000],
    }


def generate_mail_body_with_codex(
    task: dict[str, str],
    sender: Identity,
    recipient: Identity | None,
    scheduled_at: datetime | None,
    timeout_seconds: int = DEFAULT_CODEx_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    mcp_profile, mcp_profile_source = resolve_codex_mcp_profile(task)
    request = build_mail_generation_request(task, sender, recipient, scheduled_at, mcp_profile, mcp_profile_source)
    prompt = render_mail_generation_prompt(request)
    raw = run_codex_exec(prompt, timeout_seconds=timeout_seconds, mcp_profile=mcp_profile)
    result = parse_mail_generation_result(raw)
    result["request"] = request
    result["codex_mcp_profile"] = mcp_profile
    result["codex_mcp_profile_source"] = mcp_profile_source
    if not result["should_send"]:
        raise MailGenerationNeedsReview(result)
    return result


class MailGenerationNeedsReview(Exception):
    def __init__(self, generation_result: dict[str, Any]) -> None:
        self.generation_result = generation_result
        super().__init__(
            "mail_generation_needs_review: "
            f"missing_fields={generation_result.get('missing_fields', [])} "
            f"assumptions={generation_result.get('assumptions', [])}"
        )


def run_codex_exec(
    prompt: str,
    timeout_seconds: int = DEFAULT_CODEx_TIMEOUT_SECONDS,
    mcp_profile: str = CODEX_MCP_PROFILE_NONE,
) -> str:
    if not CODEx_EXE or not Path(CODEx_EXE).exists():
        raise FileNotFoundError("codex.exe not found")
    if mcp_profile not in CODEX_MCP_PROFILES:
        raise ValueError(f"unsupported codex mcp profile: {mcp_profile}")

    tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt")
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        cmd = build_codex_exec_command(tmp_path, mcp_profile)
        env = os.environ.copy()
        env["CODEX_BACKGROUND_JOB"] = "email_scheduler"
        env["CODEX_MCP_PROFILE"] = mcp_profile
        proc = subprocess.run(
            cmd,
            input=prompt.encode("utf-8"),
            text=False,
            capture_output=True,
            timeout=timeout_seconds,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        last_message = ""
        if tmp_path.exists():
            last_message = tmp_path.read_text(encoding="utf-8-sig").strip()
        if proc.returncode != 0 and not last_message:
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
            raise RuntimeError(
                f"codex exec failed (exit={proc.returncode}): {stderr.strip()[:1000]}"
            )
        if not last_message:
            stdout_text = (proc.stdout or b"").decode("utf-8", errors="replace")
            stdout = stdout_text.strip().splitlines()
            last_message = stdout[-1].strip() if stdout else ""
        if not last_message:
            raise RuntimeError("codex exec returned empty last message")
        return last_message.strip()
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def build_subject(task: dict[str, str]) -> str:
    name = task.get("任务名", "").strip()
    note = task.get("说明", "").strip() or task.get("执行动作", "").strip()
    explicit_subject = task_metadata(task).get("subject", "")
    if explicit_subject:
        return sanitize_header_value(explicit_subject, 60)
    explicit = re.search(r"(?:^|[，,\s])subject\s*=\s*([^，,\n]+)", note, re.IGNORECASE)
    if explicit:
        return sanitize_header_value(explicit.group(1), 60)
    if detect_report_provider(task) == "performance_report":
        return "电脑性能报告"
    if note:
        return sanitize_header_value(note, 60)
    return sanitize_header_value(name or "Codex 自动邮件", 60)


def attach_files(msg: EmailMessage, attachments: list[Path]) -> None:
    for path in attachments:
        if not path.exists() or not path.is_file():
            continue
        mime, _ = mimetypes.guess_type(path.name)
        maintype, subtype = ("application", "octet-stream")
        if mime and "/" in mime:
            maintype, subtype = mime.split("/", 1)
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )


def send_smtp_mail(
    sender: Identity,
    recipient_addresses_list: list[str],
    subject: str,
    body: str,
    attachments: list[Path] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not recipient_addresses_list:
        raise ValueError("No recipient resolved")
    if not sender.default_account or not sender.smtp_host:
        raise ValueError(f"Sender identity incomplete: {sender.name}")

    msg = EmailMessage()
    msg["From"] = formataddr((encode_mail_header(sender.name), sender.default_account))
    msg["To"] = ", ".join(recipient_addresses_list)
    msg["Subject"] = encode_mail_header(subject)
    msg["Date"] = format_datetime(now_beijing())
    msg["Message-ID"] = make_msgid(domain=sender.default_account.split("@")[-1] if "@" in sender.default_account else None)
    for key, value in (extra_headers or {}).items():
        clean_key = re.sub(r"[^A-Za-z0-9-]", "", key)
        clean_value = sanitize_header_value(value, 1000)
        if clean_key and clean_value:
            msg[clean_key] = clean_value
    msg.set_content(body, charset=SMTP_BODY_CHARSET, cte=SMTP_BODY_CTE)
    if attachments:
        attach_files(msg, attachments)

    encryption = sender.smtp_encryption.upper()
    if ("SSL" in encryption or "TLS" in encryption) and sender.smtp_port == 465:
        with smtplib.SMTP_SSL(sender.smtp_host, sender.smtp_port, timeout=45) as smtp:
            smtp.login(sender.default_account, sender.auth_code)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(sender.smtp_host, sender.smtp_port, timeout=45) as smtp:
            if "STARTTLS" in encryption:
                smtp.starttls()
            smtp.login(sender.default_account, sender.auth_code)
            smtp.send_message(msg)

    return {
        "message_id": msg["Message-ID"],
        "subject": subject,
        "recipients": recipient_addresses_list,
    }


def write_email_record(
    task: dict[str, str],
    sender: Identity,
    recipient: Identity | None,
    subject: str,
    body: str,
    attachments: list[Path],
    send_result: dict[str, Any],
    scheduled_at: datetime | None,
    state: str,
) -> tuple[Path, Path]:
    now = now_beijing()
    month = now.strftime("%Y-%m")
    record_key = build_record_key(task.get("任务名", ""), scheduled_at or now)
    mail_dir = MAIL_RECORDS_ROOT / month
    schedule_dir = SCHEDULE_RECORDS_ROOT / month
    mail_dir.mkdir(parents=True, exist_ok=True)
    schedule_dir.mkdir(parents=True, exist_ok=True)

    mail_record = mail_dir / f"{record_key}.md"
    schedule_record = schedule_dir / f"{record_key}.md"
    attachment_text = "\n".join(f"- {item}" for item in [str(x) for x in attachments]) if attachments else "- none"
    record_content = f"""# 邮件发送记录

- 时间：{now.isoformat()}
- 任务名：{task.get('任务名', '')}
- 状态：{state}
- 发件身份：{sender.name}
- 发件账号：{sender.default_account}
- 目标身份：{recipient.name if recipient else task.get('目标', '')}
- 目标账号：{', '.join(send_result.get('recipients') or [])}
- 主题：{subject}
- 附件：
{attachment_text}
- Message-ID：{send_result.get('message_id', '')}
- 触发时间：{scheduled_at.isoformat() if scheduled_at else ''}

## 邮件正文

{body}
"""
    write_text(mail_record, record_content)
    write_text(
        schedule_record,
        f"""# 定时任务执行记录

- 时间：{now.isoformat()}
- 任务名：{task.get('任务名', '')}
- 状态：{state}
- 发件身份：{sender.name}
- 目标身份：{recipient.name if recipient else task.get('目标', '')}
- 主题：{subject}
- 触发时间：{scheduled_at.isoformat() if scheduled_at else ''}
- 邮件记录：{mail_record}
- Message-ID：{send_result.get('message_id', '')}
""",
    )
    return mail_record, schedule_record


def build_task_runtime(task: dict[str, str], identities: dict[str, Identity]) -> dict[str, Any]:
    task_name = task.get("任务名", "").strip()
    trigger = task.get("触发方式", "").strip()
    target_name = task.get("目标", "").strip()
    sender_name = task.get("责任身份", "").strip() or "主发送者"
    sender = identity_by_name(identities, sender_name)
    recipient = identity_by_name(identities, target_name)
    scheduled_at = extract_trigger_time(" ".join([task.get("说明", ""), task.get("执行动作", ""), task.get("任务名", "")]))
    subject = build_subject(task)
    recipients = recipient_addresses(target_name, identities)
    explicit_schedule = schedule_is_explicit(task)
    content_mode, content_mode_source = resolve_content_mode(task, allow_keyword_fallback=True)
    attachments = parse_attachment_paths(task)
    return {
        "task_name": task_name,
        "trigger": trigger,
        "sender": sender,
        "recipient": recipient,
        "scheduled_at": scheduled_at,
        "explicit_schedule": explicit_schedule,
        "mail_task": is_mail_task(task),
        "subject": subject,
        "recipients": recipients,
        "content_mode": content_mode,
        "content_mode_source": content_mode_source,
        "attachments": attachments,
        "attachment_issues": validate_attachment_paths(attachments),
        "metadata": task_metadata(task),
        "due": scheduled_at is not None and now_beijing() >= scheduled_at,
        "record_key": build_record_key(task_name, scheduled_at) if scheduled_at else slugify(task_name),
    }


def task_has_sent_marker(task: dict[str, str], runtime: dict[str, Any]) -> bool:
    return bool(runtime.get("scheduled_at") and is_sent_marker_present(task.get("任务名", ""), runtime["scheduled_at"]))


def task_has_sent_receipt(task_name: str) -> bool:
    if not task_name:
        return False
    for path in SMTP_RECEIPTS_DIR.glob("*.json"):
        receipt = read_json(path)
        if receipt.get("status") not in SENT_MARKER_STATUSES:
            continue
        if str(receipt.get("task_name") or "").strip() == task_name:
            return True
    return False


def task_is_actionable_due(task: dict[str, str], runtime: dict[str, Any]) -> bool:
    if not runtime.get("due") or not is_executable_mail_row(task) or not runtime.get("mail_task"):
        return False
    task_name = str(task.get("任务名") or "").strip()
    if task_has_sent_marker(task, runtime) or task_has_sent_receipt(task_name):
        return False
    schedule_run_id = str(runtime.get("record_key") or "")
    existing_run = read_stage("schedule_run", schedule_run_id) if schedule_run_id else {}
    if existing_run.get("status") in {STATUS_SENT, STATUS_SKIPPED, STATUS_DONE, STATUS_ARCHIVED, STATUS_DEAD_LETTER, STATUS_DRAFT}:
        return False
    if existing_run and task_is_superseded_by_successful_resend(task, runtime):
        return False
    return True


def is_one_time_mail_task(task: dict[str, str], runtime: dict[str, Any]) -> bool:
    if not is_executable_mail_row(task) or not runtime.get("mail_task"):
        return False
    trigger = str(task.get("触发方式") or "").strip()
    task_type = str(task.get("任务类型") or "").strip()
    if "模板" in task_type or "间隔" in task_type:
        return False
    if "每" in trigger:
        return False
    return trigger in {"立即", "现在", "即刻", "单次", "延时"} or bool(runtime.get("scheduled_at"))


def task_is_completed_one_time_mail(task: dict[str, str], identities: dict[str, Identity]) -> bool:
    runtime = build_task_runtime(task, identities)
    if not is_one_time_mail_task(task, runtime):
        return False
    task_name = str(task.get("任务名") or "").strip()
    if task_has_sent_receipt(task_name):
        return True
    schedule_run_id = str(runtime.get("record_key") or "")
    existing_run = read_stage("schedule_run", schedule_run_id) if schedule_run_id else {}
    if existing_run.get("status") == STATUS_SENT:
        return True
    if existing_run.get("status") == STATUS_ARCHIVED and str(existing_run.get("archive_reason") or "").startswith("superseded_by_successful_resend"):
        return True
    if existing_run.get("status") in {STATUS_DRAFT, STATUS_DEAD_LETTER, STATUS_CONTENT_FAILED, STATUS_PARTIAL_FAILED}:
        related = related_stage_jobs(schedule_run_id)
        if any(bool(item.get("needs_human_review")) for item in related.get("draft_items", [])):
            return True
    return task_has_sent_marker(task, runtime)


def archive_completed_one_time_tasks(identities: dict[str, Identity] | None = None) -> dict[str, Any]:
    if identities is None:
        _, identities = load_world()
    text = read_text(MAIL_TASK_TXT).strip()
    header, blocks = split_task_table_blocks(text, MAIL_TASK_HEADERS)
    if not blocks:
        return {"ok": True, "archived_count": 0, "archived_tasks": []}
    kept = [header]
    archived: list[dict[str, Any]] = []
    for block in blocks:
        task = parse_task_block(header, block)
        task_name = str(task.get("任务名") or "").strip()
        if task_name and task_is_completed_one_time_mail(task, identities):
            runtime = build_task_runtime(task, identities)
            archived.append(
                {
                    "task_name": task_name,
                    "schedule_run_id": runtime.get("record_key", ""),
                    "trigger": task.get("触发方式", ""),
                    "archived_at": now_beijing().isoformat(),
                    "block": block,
                }
            )
            continue
        kept.append(serialize_task_row(task))
    if not archived:
        return {"ok": True, "archived_count": 0, "archived_tasks": []}
    MAIL_TASK_ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    archive_path = MAIL_TASK_ARCHIVE_ROOT / f"completed-mail-tasks-{now_beijing().strftime('%Y%m%d')}.jsonl"
    with archive_path.open("a", encoding="utf-8") as handle:
        for item in archived:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    write_text(MAIL_TASK_TXT, "\n".join(kept) + "\n")
    append_scheduler_log(f"archived completed one-time mail tasks: {[item['task_name'] for item in archived]}")
    return {
        "ok": True,
        "archived_count": len(archived),
        "archived_tasks": [item["task_name"] for item in archived],
        "archive_path": str(archive_path),
    }


def archive_task_by_name(task_name: str, reason: str = "") -> dict[str, Any]:
    wanted = str(task_name or "").strip()
    if not wanted:
        return {"ok": False, "reason": "task_name_missing"}
    text = read_text(MAIL_TASK_TXT).strip()
    header, blocks = split_task_table_blocks(text, MAIL_TASK_HEADERS)
    kept = [header]
    archived: list[dict[str, Any]] = []
    for block in blocks:
        task = parse_task_block(header, block)
        current_name = str(task.get("任务名") or "").strip()
        if current_name == wanted:
            archived.append(
                {
                    "task_name": current_name,
                    "reason": reason or "manual_archive_by_task_name",
                    "archived_at": now_beijing().isoformat(),
                    "block": block,
                }
            )
            continue
        kept.append(serialize_task_row(task))
    if not archived:
        return {"ok": False, "reason": "task_not_found", "task_name": wanted}
    MAIL_TASK_ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    archive_path = MAIL_TASK_ARCHIVE_ROOT / f"manual-mail-tasks-{now_beijing().strftime('%Y%m%d')}.jsonl"
    with archive_path.open("a", encoding="utf-8") as handle:
        for item in archived:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    write_text(MAIL_TASK_TXT, "\n".join(kept) + "\n")
    append_scheduler_log(f"archived mail task by name: {wanted} reason={reason}")
    return {
        "ok": True,
        "task_name": wanted,
        "archived_count": len(archived),
        "archive_path": str(archive_path),
    }


def is_executable_mail_row(task: dict[str, str]) -> bool:
    values = list(task.values())
    task_name = values[0].strip() if len(values) > 0 else ""
    task_type = values[1].strip() if len(values) > 1 else ""
    status = values[5].strip() if len(values) > 5 else ""
    return status == "启用" and "模板" not in task_type and task_name != "自动邮件到期分发"


def dry_run_task(task: dict[str, str], identities: dict[str, Identity]) -> dict[str, Any]:
    runtime = build_task_runtime(task, identities)
    sender = runtime["sender"]
    recipient = runtime["recipient"]
    if runtime["content_mode"] == "command_report":
        prompt = f"command_report provider={detect_report_provider(task)}; no Codex prompt will be used"
    else:
        prompt = render_task_prompt(task, sender, recipient, runtime["scheduled_at"]) if sender else ""
    return {
        "ok": True,
        "task_name": runtime["task_name"],
        "subject": runtime["subject"],
        "sender": sender.name if sender else "",
        "sender_account": sender.default_account if sender else "",
        "recipient": recipient.name if recipient else task.get("目标", ""),
        "recipient_accounts": runtime["recipients"],
        "scheduled_at": runtime["scheduled_at"].isoformat() if runtime["scheduled_at"] else "",
        "explicit_schedule": runtime["explicit_schedule"],
        "mail_task": runtime["mail_task"],
        "due": runtime["due"],
        "record_key": runtime["record_key"],
        "attachments": [str(path) for path in runtime.get("attachments", [])],
        "attachment_issues": runtime.get("attachment_issues", []),
        "prompt_preview": prompt[:1200],
        "generation_context_policy": "strict_mail_generation_context" if runtime["content_mode"] == "codex" else "",
    }


def execute_task(task: dict[str, str], identities: dict[str, Identity], timeout_seconds: int = DEFAULT_CODEx_TIMEOUT_SECONDS) -> dict[str, Any]:
    runtime = build_task_runtime(task, identities)
    sender: Identity | None = runtime["sender"]
    recipient: Identity | None = runtime["recipient"]
    if not runtime["mail_task"]:
        return {"ok": True, "skipped": True, "reason": "not an email execution task"}
    if sender is None:
        return {"ok": False, "reason": f"sender identity not found: {task.get('责任身份', '')}"}
    if recipient is None and not runtime["recipients"]:
        return {"ok": False, "reason": f"recipient identity not found: {task.get('目标', '')}"}
    if runtime["scheduled_at"] and is_sent_marker_present(task.get("任务名", ""), runtime["scheduled_at"]):
        return {"ok": True, "skipped": True, "reason": "already executed"}
    if runtime.get("attachment_issues"):
        return {"ok": False, "reason": "attachment validation failed", "issues": runtime["attachment_issues"]}

    generation = generate_mail_body_with_codex(task, sender, recipient, runtime["scheduled_at"], timeout_seconds=timeout_seconds)
    generated_body = generation["body_text"]
    send_subject = generation.get("subject") or runtime["subject"]
    metadata = task_metadata(task)
    extra_headers: dict[str, str] = {}
    if metadata.get("mail_kind") == "reply" and metadata.get("thread_policy") == "preserve":
        reply_message_id = metadata.get("reply_to_message_id_header", "")
        if reply_message_id:
            extra_headers["In-Reply-To"] = reply_message_id
            extra_headers["References"] = reply_message_id
    send_result = send_smtp_mail(
        sender=sender,
        recipient_addresses_list=runtime["recipients"] or ([recipient.default_account] if recipient and recipient.default_account else []),
        subject=send_subject,
        body=generated_body,
        attachments=runtime.get("attachments", []),
        extra_headers=extra_headers,
    )
    receipt_paths = [
        write_smtp_receipt(
            task=task,
            scheduled_at=runtime["scheduled_at"],
            sender=sender,
            recipient=recipient_address,
            subject=send_subject,
            body=generated_body,
            send_result=send_result,
        )
        for recipient_address in send_result.get("recipients", [])
    ]
    mail_record, schedule_record = write_email_record(
        task=task,
        sender=sender,
        recipient=recipient,
        subject=send_subject,
        body=generated_body,
        attachments=runtime.get("attachments", []),
        send_result=send_result,
        scheduled_at=runtime["scheduled_at"],
        state="sent",
    )
    return {
        "ok": True,
        "sent": True,
        "task_name": runtime["task_name"],
        "subject": send_subject,
        "sender": sender.name,
        "sender_account": sender.default_account,
        "recipient": recipient.name if recipient else task.get("目标", ""),
        "recipient_accounts": runtime["recipients"],
        "scheduled_at": runtime["scheduled_at"].isoformat() if runtime["scheduled_at"] else "",
        "mail_record": str(mail_record),
        "schedule_record": str(schedule_record),
        "message_id": send_result.get("message_id", ""),
        "smtp_receipts": [str(path) for path in receipt_paths],
        "body_preview": generated_body[:1500],
        "generation_result": {
            "used_evidence_ids": generation.get("used_evidence_ids", []),
            "assumptions": generation.get("assumptions", []),
            "missing_fields": generation.get("missing_fields", []),
            "should_send": generation.get("should_send", False),
        },
    }


def job_path(job_id: str) -> Path:
    return EMAIL_JOBS_DIR / f"{slugify(job_id)}.json"


def stage_path(stage: str, item_id: str) -> Path:
    roots = {
        "schedule_run": SCHEDULE_RUNS_DIR,
        "content_job": CONTENT_JOBS_DIR,
        "draft_item": DRAFT_ITEMS_DIR,
        "outbox_item": OUTBOX_ITEMS_DIR,
        "delivery_job": DELIVERY_JOBS_DIR,
        "legacy_job": EMAIL_JOBS_DIR,
    }
    return roots[stage] / f"{slugify(item_id)}.json"


def find_job(record_key: str) -> dict[str, Any]:
    path = job_path(record_key)
    return read_json(path) if path.exists() else {}


def write_job(payload: dict[str, Any]) -> Path:
    payload["updated_at"] = now_beijing().isoformat()
    path = job_path(str(payload["job_id"]))
    atomic_write_json(path, payload)
    return path


def write_stage(stage: str, payload: dict[str, Any]) -> Path:
    id_key = {
        "schedule_run": "schedule_run_id",
        "content_job": "content_job_id",
        "draft_item": "draft_item_id",
        "outbox_item": "outbox_item_id",
        "delivery_job": "delivery_job_id",
        "legacy_job": "job_id",
    }[stage]
    payload["updated_at"] = now_beijing().isoformat()
    path = stage_path(stage, str(payload[id_key]))
    atomic_write_json(path, payload)
    return path


def read_stage(stage: str, item_id: str) -> dict[str, Any]:
    path = stage_path(stage, item_id)
    return read_json(path) if path.exists() else {}


def related_stage_jobs(schedule_run_id: str) -> dict[str, Any]:
    content_jobs = []
    draft_items = []
    outbox_items = []
    delivery_jobs = []
    for path in CONTENT_JOBS_DIR.glob("*.json"):
        payload = read_json(path)
        if payload.get("schedule_run_id") == schedule_run_id:
            content_jobs.append(payload)
    for path in DRAFT_ITEMS_DIR.glob("*.json"):
        payload = read_json(path)
        if payload.get("schedule_run_id") == schedule_run_id:
            draft_items.append(payload)
    for path in OUTBOX_ITEMS_DIR.glob("*.json"):
        payload = read_json(path)
        if payload.get("schedule_run_id") == schedule_run_id:
            outbox_items.append(payload)
    for path in DELIVERY_JOBS_DIR.glob("*.json"):
        payload = read_json(path)
        if payload.get("schedule_run_id") == schedule_run_id:
            delivery_jobs.append(payload)
    return {
        "schedule_run": read_stage("schedule_run", schedule_run_id),
        "content_jobs": content_jobs,
        "draft_items": draft_items,
        "outbox_items": outbox_items,
        "delivery_jobs": delivery_jobs,
    }


def outbox_item_id(schedule_run_id: str) -> str:
    return build_artifact_key(schedule_run_id, "outbox")


def draft_item_id(schedule_run_id: str) -> str:
    return build_artifact_key(schedule_run_id, "draft")


def create_draft_item(
    task: dict[str, str],
    runtime: dict[str, Any],
    schedule_run_id: str,
    content_job_id: str | None,
    generation_result: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    item_id = draft_item_id(schedule_run_id)
    existing = read_stage("draft_item", item_id)
    if existing and existing.get("status") in {STATUS_DRAFT, STATUS_ARCHIVED}:
        return {"ok": True, "created": False, "draft_item_id": item_id, "status": existing.get("status")}
    payload = {
        "draft_item_id": item_id,
        "schedule_run_id": schedule_run_id,
        "content_job_id": content_job_id,
        "status": STATUS_DRAFT,
        "reason": reason,
        "needs_human_review": True,
        "task_name": task.get("任务名", ""),
        "task": task,
        "sender_identity": runtime["sender"].name if runtime.get("sender") else "",
        "sender_account": runtime["sender"].default_account if runtime.get("sender") else "",
        "recipients": runtime["recipients"],
        "subject": generation_result.get("subject") or runtime["subject"],
        "body": generation_result.get("body_text", ""),
        "generation_result": generation_result,
        "missing_fields": generation_result.get("missing_fields", []),
        "assumptions": generation_result.get("assumptions", []),
        "scheduled_at": runtime["scheduled_at"].isoformat() if runtime["scheduled_at"] else "",
        "created_at": now_beijing().isoformat(),
    }
    path = write_stage("draft_item", payload)
    sync_human_mailbox_item("draft", payload)
    return {"ok": True, "created": True, "draft_item_id": item_id, "job_path": str(path)}


def mirror_dead_letter_to_draft(
    *,
    task: dict[str, str] | None,
    runtime: dict[str, Any] | None,
    schedule_run_id: str,
    source_stage: str,
    source_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if payload.get("status") != STATUS_DEAD_LETTER:
        return {"ok": True, "created": False, "reason": "not_dead_letter"}
    draft_id = build_artifact_key(schedule_run_id or source_id, "draft", source_stage)
    existing = read_stage("draft_item", draft_id)
    if existing and existing.get("status") in {STATUS_DRAFT, STATUS_ARCHIVED}:
        return {"ok": True, "created": False, "draft_item_id": draft_id, "status": existing.get("status")}
    task_payload = task if isinstance(task, dict) else payload.get("task") if isinstance(payload.get("task"), dict) else {}
    runtime_payload = runtime or {}
    draft_payload = {
        "draft_item_id": draft_id,
        "schedule_run_id": schedule_run_id,
        "source_stage": source_stage,
        "source_id": source_id,
        "status": STATUS_DRAFT,
        "reason": payload.get("last_error") or payload.get("reason") or "dead_letter",
        "needs_human_review": True,
        "task_name": payload.get("task_name") or task_payload.get("任务名", ""),
        "task": task_payload,
        "sender_identity": payload.get("sender_identity") or (runtime_payload.get("sender").name if runtime_payload.get("sender") else ""),
        "sender_account": payload.get("sender_account") or (runtime_payload.get("sender").default_account if runtime_payload.get("sender") else ""),
        "recipients": payload.get("recipients") or runtime_payload.get("recipients", []),
        "subject": payload.get("subject") or runtime_payload.get("subject", ""),
        "body": payload.get("body", ""),
        "generation_result": payload.get("generation_result", {}),
        "missing_fields": payload.get("missing_fields", []),
        "assumptions": payload.get("assumptions", []),
        "scheduled_at": payload.get("scheduled_at") or (runtime_payload.get("scheduled_at").isoformat() if runtime_payload.get("scheduled_at") else ""),
        "created_at": now_beijing().isoformat(),
        "source_payload": payload,
    }
    path = write_stage("draft_item", draft_payload)
    sync_human_mailbox_item("draft", draft_payload)
    return {"ok": True, "created": True, "draft_item_id": draft_id, "job_path": str(path)}


def create_outbox_item(
    task: dict[str, str],
    runtime: dict[str, Any],
    schedule_run_id: str,
    content_job_id: str | None,
    body: str,
    generation_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item_id = outbox_item_id(schedule_run_id)
    existing = read_stage("outbox_item", item_id)
    if existing and existing.get("status") in {OUTBOX_READY, OUTBOX_SENT, OUTBOX_STALE, OUTBOX_EXPIRED, OUTBOX_BLOCKED}:
        return {"ok": True, "created": False, "outbox_item_id": item_id, "status": existing.get("status")}
    payload = {
        "outbox_item_id": item_id,
        "schedule_run_id": schedule_run_id,
        "content_job_id": content_job_id,
        "status": OUTBOX_READY,
        "task_name": task.get("任务名", ""),
        "task": task,
        "sender_identity": runtime["sender"].name if runtime.get("sender") else "",
        "sender_account": runtime["sender"].default_account if runtime.get("sender") else "",
        "recipients": runtime["recipients"],
        "subject": runtime["subject"],
        "body": body,
        "attachments": [str(path) for path in runtime.get("attachments", [])],
        "generation_result": generation_result or {},
        "scheduled_at": runtime["scheduled_at"].isoformat() if runtime["scheduled_at"] else "",
        "freshness": task_metadata(task).get("content_freshness", "static"),
        "expires_at": task_metadata(task).get("content_expires_at", ""),
        "priority": int(task_metadata(task).get("priority", "20") or 20),
        "ready_at": now_beijing().isoformat(),
        "sequence": int(now_beijing().timestamp() * 1000),
        "attempt_count": int((existing or {}).get("attempt_count") or 0),
    }
    path = write_stage("outbox_item", payload)
    sync_human_mailbox_item("outbox", payload)
    return {"ok": True, "created": True, "outbox_item_id": item_id, "job_path": str(path)}


def outbox_is_sent(item: dict[str, Any]) -> bool:
    task = item.get("task") if isinstance(item.get("task"), dict) else {}
    scheduled_at = parse_datetime(item.get("scheduled_at"))
    return bool(task and scheduled_at and is_sent_marker_present(task.get("任务名", ""), scheduled_at))


def outbox_dead_letter_source(item: dict[str, Any]) -> dict[str, Any]:
    schedule_run_id = str(item.get("schedule_run_id") or "")
    if not schedule_run_id:
        return {}
    related = related_stage_jobs(schedule_run_id)
    for payload in related.get("content_jobs", []):
        if payload.get("status") == STATUS_DEAD_LETTER:
            return {"source_stage": "content_job", "source_id": payload.get("content_job_id", ""), "payload": payload}
    for payload in related.get("delivery_jobs", []):
        if payload.get("status") == STATUS_DEAD_LETTER:
            return {"source_stage": "delivery_job", "source_id": payload.get("delivery_job_id", ""), "payload": payload}
    return {}


def move_dead_letter_outbox_to_draft(item: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    source_payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    task = item.get("task") if isinstance(item.get("task"), dict) else source_payload.get("task") if isinstance(source_payload.get("task"), dict) else {}
    runtime = build_task_runtime(task, parse_identity_table(MAIL_IDENTITY_TXT)) if task else {}
    draft = mirror_dead_letter_to_draft(
        task=task,
        runtime=runtime,
        schedule_run_id=str(item.get("schedule_run_id") or source_payload.get("schedule_run_id") or ""),
        source_stage=str(source.get("source_stage") or "outbox_item"),
        source_id=str(source.get("source_id") or item.get("outbox_item_id") or ""),
        payload={**source_payload, "body": item.get("body", source_payload.get("body", "")), "subject": item.get("subject", source_payload.get("subject", ""))},
    )
    item["status"] = OUTBOX_STALE
    item["stale_at"] = now_beijing().isoformat()
    item["stale_reason"] = "related_dead_letter_moved_to_draft"
    item["draft_item"] = draft
    write_stage("outbox_item", item)
    sync_human_mailbox_item("outbox", item)
    return draft


def classify_outbox_item(item: dict[str, Any], identities: dict[str, Identity], now: datetime | None = None) -> tuple[str, str]:
    current = now or now_beijing()
    if item.get("status") == OUTBOX_SENT or outbox_is_sent(item):
        return "sent", "already_sent"
    if outbox_dead_letter_source(item):
        return "stale", "related_dead_letter"
    if item.get("status") == OUTBOX_STALE:
        return "stale", "stale"
    scheduled_at = parse_datetime(item.get("scheduled_at"))
    expires_at = parse_datetime(item.get("expires_at"))
    if expires_at and expires_at < current:
        return "expired", "expired"
    task = item.get("task") if isinstance(item.get("task"), dict) else {}
    runtime = build_task_runtime(task, identities) if task else {}
    sender = runtime.get("sender")
    if not sender or not sender.default_account or not sender.smtp_host or not sender.auth_code:
        return "blocked", "sender_smtp_missing"
    recipients = item.get("recipients") if isinstance(item.get("recipients"), list) else []
    if not recipients:
        return "blocked", "recipient_missing"
    if item.get("status") != OUTBOX_READY:
        return "blocked", f"status_{item.get('status')}"
    if scheduled_at and scheduled_at > current:
        return "future_queue", "not_due"
    return "ready_queue", "ready"


def outbox_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    scheduled_at = parse_datetime(item.get("scheduled_at")) or now_beijing()
    expires_at = parse_datetime(item.get("expires_at")) or datetime.max.replace(tzinfo=BEIJING)
    freshness = str(item.get("freshness") or "static")
    freshness_rank = {"realtime": 0, "current": 1, "static": 2}.get(freshness, 2)
    return (
        int(item.get("priority") or 20),
        freshness_rank,
        scheduled_at.isoformat(),
        expires_at.isoformat(),
        int(item.get("sequence") or 0),
    )


def build_outbox_index(apply_state_changes: bool = False, include_smoke: bool = False) -> dict[str, Any]:
    _, identities = load_world()
    ensure_human_mailbox_roots()
    index: dict[str, Any] = {
        "ok": True,
        "updated_at": now_beijing().isoformat(),
        "ready_queue": [],
        "future_queue": [],
        "blocked": [],
        "expired": [],
        "stale": [],
        "sent": [],
    }
    for path in OUTBOX_ITEMS_DIR.glob("*.json"):
        item = read_json(path)
        if not include_smoke and is_smoke_stage_payload(path, item):
            continue
        bucket, reason = classify_outbox_item(item, identities)
        entry = {
            "outbox_item_id": item.get("outbox_item_id", path.stem),
            "schedule_run_id": item.get("schedule_run_id", ""),
            "task_name": item.get("task_name", ""),
            "scheduled_at": item.get("scheduled_at", ""),
            "subject": item.get("subject", ""),
            "reason": reason,
            "sort_key": list(outbox_sort_key(item)),
        }
        if apply_state_changes and bucket == "stale" and reason == "related_dead_letter":
            source = outbox_dead_letter_source(item)
            entry["draft_item"] = move_dead_letter_outbox_to_draft(item, source)
            item = read_stage("outbox_item", str(item.get("outbox_item_id") or path.stem))
        if apply_state_changes and bucket in {"expired", "stale"} and item.get("status") != bucket:
            item["status"] = OUTBOX_EXPIRED if bucket == "expired" else OUTBOX_STALE
            item["blocked_reason"] = reason
            write_stage("outbox_item", item)
        if apply_state_changes:
            sync_human_mailbox_item("outbox", item)
        index.setdefault(bucket, []).append(entry)
    for key in ("ready_queue", "future_queue"):
        index[key].sort(key=lambda entry: entry["sort_key"])
    return index


def refresh_outbox_index(include_smoke: bool = False) -> dict[str, Any]:
    index = build_outbox_index(apply_state_changes=True, include_smoke=include_smoke)
    atomic_write_json(OUTBOX_INDEX_PATH, index)
    return index


def peek_outbox() -> dict[str, Any]:
    index = build_outbox_index()
    ready = index.get("ready_queue", [])
    if not ready:
        return {"ok": True, "available": False, "index": index}
    item_id = str(ready[0].get("outbox_item_id"))
    return {"ok": True, "available": True, "outbox_item_id": item_id, "item": read_stage("outbox_item", item_id), "index": index}


def inspect_run(schedule_run_id: str) -> dict[str, Any]:
    related = related_stage_jobs(schedule_run_id)
    return {
        "ok": bool(related["schedule_run"] or related["content_jobs"] or related["draft_items"] or related["delivery_jobs"]),
        "schedule_run_id": schedule_run_id,
        **related,
    }


def stage_job_retry_summary(jobs: list[dict[str, Any]], current: datetime | None = None) -> dict[str, Any]:
    now = current or now_beijing()
    waiting: list[str] = []
    runnable: list[str] = []
    statuses: list[str] = []
    next_retry_at: datetime | None = None
    for payload in jobs:
        status = str(payload.get("status") or "")
        statuses.append(status)
        if status in RUNNABLE_STATUSES:
            runnable.append(str(payload.get("content_job_id") or payload.get("delivery_job_id") or payload.get("job_id") or ""))
            continue
        if status not in RETRYABLE_STATUSES:
            continue
        retry_after = parse_datetime(payload.get("retry_after"))
        if retry_after is None or now >= retry_after:
            runnable.append(str(payload.get("content_job_id") or payload.get("delivery_job_id") or payload.get("job_id") or ""))
        else:
            waiting.append(str(payload.get("content_job_id") or payload.get("delivery_job_id") or payload.get("job_id") or ""))
            if next_retry_at is None or retry_after < next_retry_at:
                next_retry_at = retry_after
    return {
        "statuses": sorted(statuses),
        "runnable_count": len([item for item in runnable if item]),
        "retry_waiting_count": len([item for item in waiting if item]),
        "next_retry_at": next_retry_at.isoformat() if next_retry_at else "",
    }


def has_runnable_related_stage_jobs(related: dict[str, Any], current: datetime | None = None) -> bool:
    now = current or now_beijing()
    for payload in related.get("content_jobs", []) + related.get("delivery_jobs", []):
        status = payload.get("status")
        if status in {STATUS_QUEUED, STATUS_RUNNING}:
            return True
        if status in RETRYABLE_STATUSES and job_is_retry_ready(payload, now):
            return True
    for item in related.get("outbox_items", []):
        if item.get("status") == OUTBOX_READY:
            return True
    return False


def archive_run(schedule_run_id: str, reason: str = "") -> dict[str, Any]:
    related = related_stage_jobs(schedule_run_id)
    if not (related["schedule_run"] or related["content_jobs"] or related["draft_items"] or related["outbox_items"] or related["delivery_jobs"]):
        return {"ok": False, "reason": f"schedule run not found: {schedule_run_id}"}
    archived_at = now_beijing().isoformat()
    for payload in related["content_jobs"]:
        payload["status"] = STATUS_ARCHIVED
        payload["archived_at"] = archived_at
        payload["archive_reason"] = reason or payload.get("last_error", "")
        write_stage("content_job", payload)
    for payload in related["outbox_items"]:
        payload["status"] = OUTBOX_STALE
        payload["archived_at"] = archived_at
        payload["archive_reason"] = reason or payload.get("blocked_reason", "")
        write_stage("outbox_item", payload)
        sync_human_mailbox_item("outbox", payload)
    for payload in related["draft_items"]:
        payload["status"] = STATUS_ARCHIVED
        payload["archived_at"] = archived_at
        payload["archive_reason"] = reason or payload.get("reason", "")
        write_stage("draft_item", payload)
        sync_human_mailbox_item("draft", payload)
    for payload in related["delivery_jobs"]:
        if payload.get("status") not in {STATUS_SENT, STATUS_SKIPPED}:
            payload["status"] = STATUS_ARCHIVED
        payload["archived_at"] = archived_at
        payload["archive_reason"] = reason or payload.get("last_error", "")
        write_stage("delivery_job", payload)
    run_payload = related["schedule_run"]
    if run_payload:
        run_payload["status"] = STATUS_ARCHIVED
        run_payload["archived_at"] = archived_at
        run_payload["archive_reason"] = reason
        run_payload["content_job_status"] = STATUS_ARCHIVED if related["content_jobs"] else run_payload.get("content_job_status", "")
        run_payload["delivery_statuses"] = [STATUS_ARCHIVED] if related["delivery_jobs"] else run_payload.get("delivery_statuses", [])
        write_stage("schedule_run", run_payload)
    append_scheduler_log(f"archive_run schedule_run_id={schedule_run_id} reason={reason}")
    return {"ok": True, "schedule_run_id": schedule_run_id, "archived_at": archived_at}


def reset_run(schedule_run_id: str, stage: str, confirm_resend: str) -> dict[str, Any]:
    if confirm_resend != "YES":
        return {"ok": False, "reason": "reset requires --confirm-resend YES"}
    if stage not in {"content", "delivery", "all"}:
        return {"ok": False, "reason": "stage must be content, delivery, or all"}
    related = related_stage_jobs(schedule_run_id)
    if not (related["schedule_run"] or related["content_jobs"] or related["draft_items"] or related["delivery_jobs"]):
        return {"ok": False, "reason": f"schedule run not found: {schedule_run_id}"}
    reset_at = now_beijing().isoformat()
    touched: list[str] = []
    if stage in {"content", "all"}:
        for payload in related["content_jobs"]:
            payload["status"] = STATUS_QUEUED
            payload["retry_after"] = ""
            payload["retry_exhausted"] = False
            payload["reset_at"] = reset_at
            payload["reset_reason"] = "explicit_resend"
            write_stage("content_job", payload)
            touched.append(str(payload.get("content_job_id")))
        for payload in related["outbox_items"]:
            payload["status"] = OUTBOX_STALE
            payload["stale_at"] = reset_at
            payload["stale_reason"] = "content_reset"
            write_stage("outbox_item", payload)
            touched.append(str(payload.get("outbox_item_id")))
        for payload in related["draft_items"]:
            payload["status"] = STATUS_ARCHIVED
            payload["archived_at"] = reset_at
            payload["archive_reason"] = "content_reset"
            write_stage("draft_item", payload)
            touched.append(str(payload.get("draft_item_id")))
    if stage in {"delivery", "all"}:
        for payload in related["delivery_jobs"]:
            task = payload.get("task") if isinstance(payload.get("task"), dict) else None
            if task:
                identities = parse_identity_table(MAIL_IDENTITY_TXT)
                runtime = build_task_runtime(task, identities)
                payload["subject"] = sanitize_header_value(str(runtime.get("subject") or payload.get("subject") or "Codex 自动邮件"))
            payload["status"] = STATUS_QUEUED
            payload["retry_after"] = ""
            payload["retry_exhausted"] = False
            payload["reset_at"] = reset_at
            payload["reset_reason"] = "explicit_resend"
            write_stage("delivery_job", payload)
            touched.append(str(payload.get("delivery_job_id")))
    run_payload = related["schedule_run"]
    if run_payload:
        if stage in {"content", "all"}:
            run_payload["status"] = STATUS_QUEUED
            run_payload["content_job_status"] = STATUS_QUEUED
        elif stage == "delivery":
            run_payload["status"] = STATUS_DELIVERY_QUEUED
            run_payload["delivery_statuses"] = [STATUS_QUEUED]
        run_payload["reset_at"] = reset_at
        run_payload["reset_reason"] = "explicit_resend"
        write_stage("schedule_run", run_payload)
    append_scheduler_log(f"reset_run schedule_run_id={schedule_run_id} stage={stage}")
    return {"ok": True, "schedule_run_id": schedule_run_id, "stage": stage, "reset_at": reset_at, "touched": touched}


def create_delivery_jobs(
    task: dict[str, str],
    runtime: dict[str, Any],
    schedule_run_id: str,
    content_job_id: str | None,
    body: str | None,
    outbox_item_id_value: str | None = None,
) -> list[dict[str, Any]]:
    deliveries = []
    sender = runtime["sender"]
    recipients = runtime["recipients"]
    if not recipients:
        return [{"ok": False, "reason": "no_recipient", "schedule_run_id": schedule_run_id}]
    sender_key = sender.default_account if sender else "unknown-sender"
    for recipient in recipients:
        delivery_id = build_artifact_key(schedule_run_id, sender_key, recipient)
        existing = read_stage("delivery_job", delivery_id)
        if existing and existing.get("status") in EXISTING_DELIVERY_STATUSES:
            deliveries.append({"ok": True, "created": False, "delivery_job_id": delivery_id, "status": existing.get("status")})
            continue
        payload = {
            "delivery_job_id": delivery_id,
            "schedule_run_id": schedule_run_id,
            "content_job_id": content_job_id,
            "outbox_item_id": outbox_item_id_value,
            "status": STATUS_QUEUED,
            "task_name": task.get("任务名", ""),
            "task": task,
            "sender_identity": sender.name if sender else "",
            "sender_account": sender.default_account if sender else "",
            "recipient": recipient,
            "subject": runtime["subject"],
            "body": body,
            "attachments": [str(path) for path in runtime.get("attachments", [])],
            "scheduled_at": runtime["scheduled_at"].isoformat() if runtime["scheduled_at"] else "",
            "attempt_count": int((existing or {}).get("attempt_count") or 0),
            "created_at": now_beijing().isoformat(),
        }
        path = write_stage("delivery_job", payload)
        deliveries.append({"ok": True, "created": True, "delivery_job_id": delivery_id, "job_path": str(path)})
    return deliveries


def update_schedule_run_delivery_status(schedule_run_id: str) -> None:
    run_payload = read_stage("schedule_run", schedule_run_id)
    if not run_payload:
        return
    related = []
    for path in DELIVERY_JOBS_DIR.glob("*.json"):
        payload = read_json(path)
        if payload.get("schedule_run_id") == schedule_run_id:
            related.append(payload)
    if not related:
        return
    statuses = {item.get("status") for item in related}
    if statuses <= {STATUS_SENT, STATUS_SKIPPED}:
        run_payload = clear_failure_fields(run_payload)
        run_payload["status"] = STATUS_SENT
    elif STATUS_FAILED in statuses:
        run_payload["status"] = STATUS_PARTIAL_FAILED
    elif STATUS_RUNNING in statuses:
        run_payload["status"] = STATUS_DELIVERY_RUNNING
    else:
        run_payload["status"] = STATUS_DELIVERY_QUEUED
    run_payload["delivery_statuses"] = sorted(str(status) for status in statuses)
    write_stage("schedule_run", run_payload)


def update_schedule_run_status(schedule_run_id: str, status: str, **extra: Any) -> None:
    run_payload = read_stage("schedule_run", schedule_run_id)
    if not run_payload:
        return
    run_payload["status"] = status
    run_payload.update(extra)
    write_stage("schedule_run", run_payload)


def create_three_stage_email_job(task: dict[str, str], identities: dict[str, Identity]) -> dict[str, Any]:
    runtime = build_task_runtime(task, identities)
    schedule_run_id = runtime["record_key"]
    existing_run = read_stage("schedule_run", schedule_run_id)
    if existing_run and existing_run.get("status") in EXISTING_RUN_STATUSES:
        if existing_run.get("status") not in {STATUS_SENT, STATUS_SKIPPED, STATUS_DONE, STATUS_ARCHIVED} and task_is_superseded_by_successful_resend(task, runtime):
            archive = archive_run(schedule_run_id, reason="superseded_by_successful_resend")
            archive["created"] = False
            archive["status"] = STATUS_ARCHIVED
            archive["reason"] = "superseded_by_successful_resend"
            return archive
        status = existing_run.get("status")
        result = {"ok": True, "created": False, "schedule_run_id": schedule_run_id, "status": status, "reason": "existing_schedule_run"}
        content_job_id = build_artifact_key(schedule_run_id, "content")
        content_job = read_stage("content_job", content_job_id)
        related = related_stage_jobs(schedule_run_id)
        content_summary = stage_job_retry_summary(related["content_jobs"])
        delivery_summary = stage_job_retry_summary(related["delivery_jobs"])
        result["content_summary"] = content_summary
        result["delivery_summary"] = delivery_summary
        result["runnable_stage_jobs"] = bool(content_summary["runnable_count"] or delivery_summary["runnable_count"])
        if content_job:
            result["content_job_status"] = content_job.get("status")
            result["retry_after"] = content_job.get("retry_after", "")
            result["retry_ready"] = job_is_retry_ready(content_job)
        return result
    if runtime["scheduled_at"] and is_sent_marker_present(task.get("任务名", ""), runtime["scheduled_at"]):
        payload = {
            "schedule_run_id": schedule_run_id,
            "status": STATUS_SKIPPED,
            "reason": "already_executed",
            "content_mode": runtime["content_mode"],
            "task_name": task.get("任务名", ""),
            "created_at": now_beijing().isoformat(),
            "scheduled_at": runtime["scheduled_at"].isoformat() if runtime["scheduled_at"] else "",
        }
        path = write_stage("schedule_run", payload)
        return {"ok": True, "created": False, "schedule_run_id": schedule_run_id, "status": STATUS_SKIPPED, "job_path": str(path)}
    content_mode = runtime["content_mode"]
    run_payload = {
        "schedule_run_id": schedule_run_id,
        "status": STATUS_QUEUED,
        "content_mode": content_mode,
        "task_name": task.get("任务名", ""),
        "task": task,
        "created_at": now_beijing().isoformat(),
        "scheduled_at": runtime["scheduled_at"].isoformat() if runtime["scheduled_at"] else "",
        "recipients": runtime["recipients"],
    }
    run_path = write_stage("schedule_run", run_payload)
    if content_mode in {"codex", "command_report"}:
        content_job_id = build_artifact_key(schedule_run_id, "content")
        existing_content = read_stage("content_job", content_job_id)
        if not existing_content or existing_content.get("status") not in {STATUS_QUEUED, STATUS_RUNNING, "succeeded", STATUS_ARCHIVED, STATUS_DEAD_LETTER}:
            content_payload = {
                "content_job_id": content_job_id,
                "schedule_run_id": schedule_run_id,
                "status": STATUS_QUEUED,
                "content_mode": content_mode,
                "report_provider": detect_report_provider(task) if content_mode == "command_report" else "",
                "task_name": task.get("任务名", ""),
                "task": task,
                "created_at": now_beijing().isoformat(),
                "attempt_count": int((existing_content or {}).get("attempt_count") or 0),
            }
            write_stage("content_job", content_payload)
        return {"ok": True, "created": True, "schedule_run_id": schedule_run_id, "content_job_id": content_job_id, "stage": "content_queued", "job_path": str(run_path)}
    body = "" if content_mode == "attachment_only" else extract_static_body(task)
    outbox = create_outbox_item(task, runtime, schedule_run_id, None, body)
    refresh_outbox_index()
    run_payload["status"] = STATUS_CONTENT_READY
    run_payload["outbox_item"] = outbox
    write_stage("schedule_run", run_payload)
    return {"ok": True, "created": True, "schedule_run_id": schedule_run_id, "stage": "outbox_ready", "outbox_item": outbox, "job_path": str(run_path)}


def create_email_job(task: dict[str, str], identities: dict[str, Identity]) -> dict[str, Any]:
    return create_three_stage_email_job(task, identities)


def start_email_worker(timeout_seconds: int = DEFAULT_CODEx_TIMEOUT_SECONDS) -> dict[str, Any]:
    existing: list[str] = []
    if os.name == "nt":
        try:
            ps = (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match 'email_scheduler.py worker' } | "
                "Select-Object -First 1 -ExpandProperty ProcessId"
            )
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps],
                text=True,
                capture_output=True,
                timeout=10,
            )
            existing = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
        except Exception:
            existing = []
    if existing:
        return {"ok": True, "started": False, "reason": "worker_already_running", "pid": existing[0]}
    args = [
        sys.executable,
        str(Path(__file__).resolve()),
        "worker",
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    subprocess.Popen(
        args,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return {"ok": True, "started": True}


def has_runnable_stage_jobs(now: datetime | None = None) -> bool:
    current = now or now_beijing()
    if peek_outbox().get("available"):
        return True
    if peek_inbox().get("available"):
        return True
    for root in (CONTENT_JOBS_DIR, DELIVERY_JOBS_DIR, EMAIL_JOBS_DIR):
        for path in root.glob("*.json"):
            payload = read_json(path)
            if payload.get("status") in RUNNABLE_STATUSES:
                return True
            if payload.get("status") in RETRYABLE_STATUSES and job_is_retry_ready(payload, current):
                return True
    return False


def dispatch_due(timeout_seconds: int = DEFAULT_CODEx_TIMEOUT_SECONDS) -> dict[str, Any]:
    inbox_poll_result = poll_inbox()
    inbox_attachment_materialization = auto_materialize_inbox_attachments(apply=True)
    tasks, identities = load_world()
    archive_result = archive_completed_one_time_tasks(identities)
    if archive_result.get("archived_count"):
        tasks, identities = load_world()
    jobs = []
    for task in tasks:
        if not is_executable_mail_row(task):
            continue
        runtime = build_task_runtime(task, identities)
        if not runtime["due"] or not runtime["mail_task"]:
            continue
        jobs.append(create_email_job(task, identities))
    should_start_worker = has_runnable_stage_jobs()
    worker_result = start_email_worker(timeout_seconds=timeout_seconds) if should_start_worker else {"ok": True, "started": False, "reason": "no_runnable_jobs"}
    post_worker_archive_result = {"ok": True, "archived_count": 0, "archived_tasks": []}
    if worker_result.get("started"):
        _, refreshed_identities = load_world()
        post_worker_archive_result = archive_completed_one_time_tasks(refreshed_identities)
    result = {
        "ok": True,
        "inbox_poll": inbox_poll_result,
        "inbox_attachment_materialization": inbox_attachment_materialization,
        "task_archive": archive_result,
        "jobs": jobs,
        "worker": worker_result,
        "post_worker_task_archive": post_worker_archive_result,
    }
    write_heartbeat({"dispatch_due": result})
    append_scheduler_log(f"dispatch_due => {json.dumps(result, ensure_ascii=False)}")
    return result


def worker(timeout_seconds: int = DEFAULT_CODEx_TIMEOUT_SECONDS, max_jobs: int = 10) -> dict[str, Any]:
    lock = SingleInstanceLock(EMAIL_WORKER_LOCK_PATH)
    if not lock.acquire():
        return {"ok": True, "skipped": True, "reason": "worker_already_running"}
    results = []
    append_scheduler_log("email worker started")
    try:
        tasks, identities = load_world()
        by_name = {task.get("任务名", ""): task for task in tasks}
        inbox_processed = 0
        while len(results) < max_jobs and inbox_processed < INBOX_WORKER_MAX_CODEX_JOBS:
            peek = peek_inbox()
            if not peek.get("available"):
                break
            item = peek.get("item") if isinstance(peek.get("item"), dict) else {}
            result = process_inbox_job(item, identities)
            results.append(result)
            inbox_processed += 1
            if not result.get("ok"):
                break

        content_jobs = sorted(CONTENT_JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for path in content_jobs:
            if len(results) >= max_jobs:
                break
            payload = read_json(path)
            if payload.get("status") not in RUNNABLE_STATUSES | RETRYABLE_STATUSES:
                continue
            if payload.get("status") in RETRYABLE_STATUSES and not job_is_retry_ready(payload):
                results.append({"content_job_id": payload.get("content_job_id"), "ok": True, "skipped": True, "reason": "retry_not_due", "retry_after": payload.get("retry_after", "")})
                continue
            task = payload.get("task") if isinstance(payload.get("task"), dict) else by_name.get(payload.get("task_name", ""))
            if not task:
                payload = mark_retry_or_dead_letter(
                    payload,
                    error="task_not_found",
                    max_attempts=CONTENT_JOB_MAX_ATTEMPTS,
                    retry_base_seconds=CONTENT_JOB_RETRY_BASE_SECONDS,
                )
                write_stage("content_job", payload)
                draft = mirror_dead_letter_to_draft(
                    task=None,
                    runtime=None,
                    schedule_run_id=str(payload.get("schedule_run_id")),
                    source_stage="content_job",
                    source_id=str(payload.get("content_job_id")),
                    payload=payload,
                )
                update_schedule_run_status(str(payload.get("schedule_run_id")), "content_failed", content_job_status=payload.get("status"), last_error=payload.get("last_error", ""))
                results.append({"content_job_id": payload.get("content_job_id"), "ok": False, "reason": "task_not_found", "draft_item": draft})
                continue
            runtime = build_task_runtime(task, identities)
            sender = runtime["sender"]
            recipient = runtime["recipient"]
            if sender is None:
                payload = mark_retry_or_dead_letter(
                    payload,
                    error="sender_not_found",
                    max_attempts=CONTENT_JOB_MAX_ATTEMPTS,
                    retry_base_seconds=CONTENT_JOB_RETRY_BASE_SECONDS,
                )
                write_stage("content_job", payload)
                draft = mirror_dead_letter_to_draft(
                    task=task,
                    runtime=runtime,
                    schedule_run_id=str(payload.get("schedule_run_id")),
                    source_stage="content_job",
                    source_id=str(payload.get("content_job_id")),
                    payload=payload,
                )
                update_schedule_run_status(str(payload.get("schedule_run_id")), "content_failed", content_job_status=payload.get("status"), last_error=payload.get("last_error", ""))
                results.append({"content_job_id": payload.get("content_job_id"), "ok": False, "reason": "sender_not_found", "draft_item": draft})
                continue
            payload["status"] = STATUS_RUNNING
            payload["started_at"] = now_beijing().isoformat()
            payload["attempt_count"] = int(payload.get("attempt_count") or 0) + 1
            write_stage("content_job", payload)
            try:
                content_mode = payload.get("content_mode") or runtime["content_mode"]
                generation_result: dict[str, Any] = {}
                if content_mode == "command_report":
                    body = run_report_provider(str(payload.get("report_provider") or detect_report_provider(task)))
                else:
                    generation_result = generate_mail_body_with_codex(task, sender, recipient, runtime["scheduled_at"], timeout_seconds=timeout_seconds)
                    body = generation_result["body_text"]
                    if generation_result.get("subject"):
                        runtime["subject"] = str(generation_result["subject"])
                payload = clear_failure_fields(payload)
                payload["status"] = "succeeded"
                payload["finished_at"] = now_beijing().isoformat()
                payload["body"] = body
                payload["generation_result"] = generation_result
                write_stage("content_job", payload)
                schedule_run_id = str(payload.get("schedule_run_id"))
                outbox = create_outbox_item(task, runtime, schedule_run_id, str(payload.get("content_job_id")), body, generation_result)
                refresh_outbox_index()
                run_payload = read_stage("schedule_run", schedule_run_id)
                if run_payload:
                    run_payload = clear_failure_fields(run_payload)
                    run_payload["status"] = STATUS_CONTENT_READY
                    run_payload["outbox_item"] = outbox
                    write_stage("schedule_run", run_payload)
                results.append({"content_job_id": payload.get("content_job_id"), "ok": True, "outbox_item": outbox})
            except MailGenerationNeedsReview as exc:
                generation_result = exc.generation_result
                payload["status"] = STATUS_DRAFT
                payload["finished_at"] = now_beijing().isoformat()
                payload["last_error"] = str(exc)
                payload["generation_result"] = generation_result
                payload["retry_after"] = ""
                payload["retry_exhausted"] = True
                write_stage("content_job", payload)
                schedule_run_id = str(payload.get("schedule_run_id"))
                draft = create_draft_item(
                    task,
                    runtime,
                    schedule_run_id,
                    str(payload.get("content_job_id")),
                    generation_result,
                    str(exc),
                )
                run_payload = read_stage("schedule_run", schedule_run_id)
                if run_payload:
                    run_payload["status"] = STATUS_DRAFT
                    run_payload["content_job_status"] = STATUS_DRAFT
                    run_payload["draft_item"] = draft
                    run_payload["last_error"] = str(exc)
                    write_stage("schedule_run", run_payload)
                append_scheduler_log(f"email content job moved to draft: {payload['last_error']}")
                results.append({"content_job_id": payload.get("content_job_id"), "ok": False, "draft_item": draft, "reason": payload["last_error"]})
            except Exception as exc:
                payload = mark_retry_or_dead_letter(
                    payload,
                    error=f"{type(exc).__name__}: {exc}",
                    max_attempts=CONTENT_JOB_MAX_ATTEMPTS,
                    retry_base_seconds=CONTENT_JOB_RETRY_BASE_SECONDS,
                )
                write_stage("content_job", payload)
                draft = mirror_dead_letter_to_draft(
                    task=task,
                    runtime=runtime,
                    schedule_run_id=str(payload.get("schedule_run_id")),
                    source_stage="content_job",
                    source_id=str(payload.get("content_job_id")),
                    payload=payload,
                )
                run_status = STATUS_DEAD_LETTER if payload.get("status") == STATUS_DEAD_LETTER else STATUS_CONTENT_FAILED
                update_schedule_run_status(str(payload.get("schedule_run_id")), run_status, content_job_status=payload.get("status"), last_error=payload.get("last_error", ""), retry_after=payload.get("retry_after", ""))
                append_scheduler_log(f"email content job error: {payload['last_error']}")
                results.append({"content_job_id": payload.get("content_job_id"), "ok": False, "reason": payload["last_error"], "draft_item": draft})

        while len(results) < max_jobs:
            peek = peek_outbox()
            if not peek.get("available"):
                break
            item = peek.get("item") if isinstance(peek.get("item"), dict) else {}
            task = item.get("task") if isinstance(item.get("task"), dict) else by_name.get(str(item.get("task_name", "")))
            if not task:
                item["status"] = OUTBOX_BLOCKED
                item["blocked_reason"] = "task_not_found"
                write_stage("outbox_item", item)
                results.append({"outbox_item_id": item.get("outbox_item_id"), "ok": False, "reason": "task_not_found"})
                refresh_outbox_index()
                continue
            runtime = build_task_runtime(task, identities)
            deliveries = create_delivery_jobs(
                task,
                runtime,
                str(item.get("schedule_run_id")),
                str(item.get("content_job_id") or "") or None,
                str(item.get("body") or ""),
                str(item.get("outbox_item_id")),
            )
            run_payload = read_stage("schedule_run", str(item.get("schedule_run_id")))
            if run_payload:
                run_payload["status"] = STATUS_DELIVERY_QUEUED
                run_payload["delivery_jobs"] = deliveries
                write_stage("schedule_run", run_payload)
            results.append({"outbox_item_id": item.get("outbox_item_id"), "ok": True, "delivery_jobs": deliveries})
            break

        delivery_jobs = sorted(DELIVERY_JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for path in delivery_jobs:
            if len(results) >= max_jobs:
                break
            payload = read_json(path)
            if payload.get("status") not in RUNNABLE_STATUSES | RETRYABLE_STATUSES:
                continue
            if payload.get("status") in RETRYABLE_STATUSES and not job_is_retry_ready(payload):
                results.append({"delivery_job_id": payload.get("delivery_job_id"), "ok": True, "skipped": True, "reason": "retry_not_due", "retry_after": payload.get("retry_after", "")})
                continue
            task = payload.get("task") if isinstance(payload.get("task"), dict) else by_name.get(payload.get("task_name", ""))
            if not task:
                payload = mark_retry_or_dead_letter(
                    payload,
                    error="task_not_found",
                    max_attempts=DELIVERY_JOB_MAX_ATTEMPTS,
                    retry_base_seconds=DELIVERY_JOB_RETRY_BASE_SECONDS,
                )
                write_stage("delivery_job", payload)
                draft = mirror_dead_letter_to_draft(
                    task=None,
                    runtime=None,
                    schedule_run_id=str(payload.get("schedule_run_id")),
                    source_stage="delivery_job",
                    source_id=str(payload.get("delivery_job_id")),
                    payload=payload,
                )
                update_schedule_run_delivery_status(str(payload.get("schedule_run_id")))
                results.append({"delivery_job_id": payload.get("delivery_job_id"), "ok": False, "reason": "task_not_found", "draft_item": draft})
                continue
            runtime = build_task_runtime(task, identities)
            sender = runtime["sender"]
            if sender is None:
                payload = mark_retry_or_dead_letter(
                    payload,
                    error="sender_not_found",
                    max_attempts=DELIVERY_JOB_MAX_ATTEMPTS,
                    retry_base_seconds=DELIVERY_JOB_RETRY_BASE_SECONDS,
                )
                write_stage("delivery_job", payload)
                draft = mirror_dead_letter_to_draft(
                    task=task,
                    runtime=runtime,
                    schedule_run_id=str(payload.get("schedule_run_id")),
                    source_stage="delivery_job",
                    source_id=str(payload.get("delivery_job_id")),
                    payload=payload,
                )
                update_schedule_run_delivery_status(str(payload.get("schedule_run_id")))
                results.append({"delivery_job_id": payload.get("delivery_job_id"), "ok": False, "reason": "sender_not_found", "draft_item": draft})
                continue
            if runtime["scheduled_at"] and is_recipient_sent_marker_present(
                task.get("任务名", ""),
                runtime["scheduled_at"],
                str(payload.get("recipient") or ""),
            ):
                payload = clear_failure_fields(payload)
                payload["status"] = STATUS_SKIPPED
                payload["reason"] = "already_executed"
                write_stage("delivery_job", payload)
                results.append({"delivery_job_id": payload.get("delivery_job_id"), "ok": True, "skipped": True, "reason": "already_executed"})
                continue
            payload["status"] = STATUS_RUNNING
            payload["started_at"] = now_beijing().isoformat()
            payload["attempt_count"] = int(payload.get("attempt_count") or 0) + 1
            write_stage("delivery_job", payload)
            try:
                outbox_item = read_stage("outbox_item", str(payload.get("outbox_item_id") or "")) if payload.get("outbox_item_id") else {}
                body = outbox_item.get("body") if outbox_item else payload.get("body")
                if body is None and payload.get("content_job_id"):
                    body = read_stage("content_job", str(payload.get("content_job_id"))).get("body", "")
                attachment_values = outbox_item.get("attachments") if outbox_item else payload.get("attachments")
                attachments = [Path(str(value)) for value in attachment_values] if isinstance(attachment_values, list) else []
                attachment_issues = validate_attachment_paths(attachments)
                if attachment_issues:
                    raise RuntimeError(f"attachment validation failed: {attachment_issues}")
                metadata = task_metadata(task)
                extra_headers: dict[str, str] = {}
                if metadata.get("mail_kind") == "reply" and metadata.get("thread_policy") == "preserve":
                    reply_message_id = metadata.get("reply_to_message_id_header", "")
                    if reply_message_id:
                        extra_headers["In-Reply-To"] = reply_message_id
                        extra_headers["References"] = reply_message_id
                send_result = send_smtp_mail(
                    sender=sender,
                    recipient_addresses_list=[str(payload.get("recipient"))],
                    subject=sanitize_header_value(str(payload.get("subject") or runtime["subject"])),
                    body=str(body or ""),
                    attachments=attachments,
                    extra_headers=extra_headers,
                )
                receipt_paths = [
                    write_smtp_receipt(
                        task=task,
                        scheduled_at=runtime["scheduled_at"],
                        sender=sender,
                        recipient=str(payload.get("recipient")),
                        subject=sanitize_header_value(str(payload.get("subject") or runtime["subject"])),
                        body=str(body or ""),
                        send_result=send_result,
                    )
                ]
                recipient_identity = identity_by_name(identities, task.get("目标", ""))
                mail_record, schedule_record = write_email_record(
                    task=task,
                    sender=sender,
                    recipient=recipient_identity,
                    subject=sanitize_header_value(str(payload.get("subject") or runtime["subject"])),
                    body=str(body or ""),
                    attachments=attachments,
                    send_result=send_result,
                    scheduled_at=runtime["scheduled_at"],
                    state="sent",
                )
                payload = clear_failure_fields(payload)
                payload["status"] = STATUS_SENT
                payload["finished_at"] = now_beijing().isoformat()
                payload["message_id"] = send_result.get("message_id", "")
                payload["smtp_receipts"] = [str(path) for path in receipt_paths]
                payload["mail_record"] = str(mail_record)
                payload["schedule_record"] = str(schedule_record)
                write_stage("delivery_job", payload)
                if outbox_item:
                    outbox_item = clear_failure_fields(outbox_item)
                    outbox_item["status"] = OUTBOX_SENT
                    outbox_item["sent_at"] = now_beijing().isoformat()
                    outbox_item["message_id"] = send_result.get("message_id", "")
                    outbox_item["delivery_job_id"] = payload.get("delivery_job_id", "")
                    write_stage("outbox_item", outbox_item)
                    refresh_outbox_index()
                schedule_run_id = str(payload.get("schedule_run_id"))
                update_schedule_run_delivery_status(schedule_run_id)
                results.append({"delivery_job_id": payload.get("delivery_job_id"), "ok": True, "sent": True, "message_id": send_result.get("message_id", "")})
            except Exception as exc:
                payload = mark_retry_or_dead_letter(
                    payload,
                    error=f"{type(exc).__name__}: {exc}",
                    max_attempts=DELIVERY_JOB_MAX_ATTEMPTS,
                    retry_base_seconds=DELIVERY_JOB_RETRY_BASE_SECONDS,
                )
                write_stage("delivery_job", payload)
                draft = mirror_dead_letter_to_draft(
                    task=task,
                    runtime=runtime,
                    schedule_run_id=str(payload.get("schedule_run_id")),
                    source_stage="delivery_job",
                    source_id=str(payload.get("delivery_job_id")),
                    payload=payload,
                )
                update_schedule_run_delivery_status(str(payload.get("schedule_run_id")))
                append_scheduler_log(f"email delivery job error: {payload['last_error']}")
                results.append({"delivery_job_id": payload.get("delivery_job_id"), "ok": False, "reason": payload["last_error"], "draft_item": draft})

        jobs = sorted(EMAIL_JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for path in jobs:
            if len(results) >= max_jobs:
                break
            payload = read_json(path)
            if payload.get("status") not in RUNNABLE_STATUSES | RETRYABLE_STATUSES:
                continue
            if payload.get("status") in RETRYABLE_STATUSES and not job_is_retry_ready(payload):
                results.append({"job_id": payload.get("job_id"), "ok": True, "skipped": True, "reason": "retry_not_due", "retry_after": payload.get("retry_after", "")})
                continue
            task = payload.get("task") if isinstance(payload.get("task"), dict) else by_name.get(payload.get("task_name", ""))
            if not task:
                payload = mark_retry_or_dead_letter(
                    payload,
                    error="task_not_found",
                    max_attempts=DELIVERY_JOB_MAX_ATTEMPTS,
                    retry_base_seconds=DELIVERY_JOB_RETRY_BASE_SECONDS,
                )
                write_job(payload)
                draft = mirror_dead_letter_to_draft(
                    task=None,
                    runtime=None,
                    schedule_run_id=str(payload.get("job_id") or payload.get("schedule_run_id") or ""),
                    source_stage="legacy_job",
                    source_id=str(payload.get("job_id")),
                    payload=payload,
                )
                results.append({"job_id": payload.get("job_id"), "ok": False, "reason": "task_not_found", "draft_item": draft})
                continue
            payload["status"] = STATUS_RUNNING
            payload["started_at"] = now_beijing().isoformat()
            payload["attempt_count"] = int(payload.get("attempt_count") or 0) + 1
            write_job(payload)
            try:
                result = execute_task(task, identities, timeout_seconds=timeout_seconds)
                payload["finished_at"] = now_beijing().isoformat()
                payload["result"] = result
                if result.get("sent"):
                    payload["status"] = STATUS_SENT
                elif result.get("skipped"):
                    payload["status"] = STATUS_SKIPPED
                elif result.get("ok"):
                    payload["status"] = STATUS_DONE
                else:
                    payload = mark_retry_or_dead_letter(
                        payload,
                        error=result.get("reason", "unknown_error"),
                        max_attempts=DELIVERY_JOB_MAX_ATTEMPTS,
                        retry_base_seconds=DELIVERY_JOB_RETRY_BASE_SECONDS,
                    )
                write_job(payload)
                results.append({"job_id": payload.get("job_id"), **result})
            except Exception as exc:
                payload = mark_retry_or_dead_letter(
                    payload,
                    error=f"{type(exc).__name__}: {exc}",
                    max_attempts=DELIVERY_JOB_MAX_ATTEMPTS,
                    retry_base_seconds=DELIVERY_JOB_RETRY_BASE_SECONDS,
                )
                write_job(payload)
                draft = mirror_dead_letter_to_draft(
                    task=task if isinstance(task, dict) else None,
                    runtime=None,
                    schedule_run_id=str(payload.get("job_id") or payload.get("schedule_run_id") or ""),
                    source_stage="legacy_job",
                    source_id=str(payload.get("job_id")),
                    payload=payload,
                )
                append_scheduler_log(f"email worker job error: {payload['last_error']}")
                results.append({"job_id": payload.get("job_id"), "ok": False, "reason": payload["last_error"], "draft_item": draft})
    finally:
        lock.release()
    append_scheduler_log(f"email worker finished => {json.dumps(results, ensure_ascii=False)}")
    return {"ok": True, "results": results}


def load_world() -> tuple[list[dict[str, str]], dict[str, Identity]]:
    tasks = parse_task_table(MAIL_TASK_TXT)
    identities = parse_identity_table(MAIL_IDENTITY_TXT)
    return tasks, identities


def count_stage_statuses(root: Path, include_smoke: bool = False) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in root.glob("*.json"):
        payload = read_json(path)
        if not include_smoke and is_smoke_stage_payload(path, payload):
            continue
        status = payload.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def count_smtp_receipts() -> int:
    return sum(1 for _ in SMTP_RECEIPTS_DIR.glob("*.json"))


def count_retry_waiting(root: Path) -> int:
    waiting = 0
    current = now_beijing()
    for path in root.glob("*.json"):
        payload = read_json(path)
        if payload.get("status") in RETRYABLE_STATUSES and not job_is_retry_ready(payload, current):
            waiting += 1
    return waiting


def load_inbox_state() -> dict[str, Any]:
    payload = read_json(INBOX_STATE_PATH)
    if not payload:
        return {"schema": "email_inbox_state.v1", "accounts": {}, "updated_at": ""}
    payload.setdefault("schema", "email_inbox_state.v1")
    payload.setdefault("accounts", {})
    return payload


def save_inbox_state(state: dict[str, Any]) -> None:
    state["schema"] = "email_inbox_state.v1"
    state["updated_at"] = now_beijing().isoformat()
    atomic_write_json(INBOX_STATE_PATH, state)


def decode_message_header(value: Any) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return sanitize_header_value(str(value), 500)


def email_addresses(value: Any) -> list[str]:
    return [addr for _, addr in getaddresses([str(value or "")]) if addr]


def message_text_body(message: email.message.Message, limit: int = 20000) -> str:
    parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() != "text/plain":
                continue
            try:
                text = part.get_content()
            except Exception:
                continue
            if text:
                parts.append(str(text))
    elif message.get_content_type() == "text/plain":
        try:
            parts.append(str(message.get_content()))
        except Exception:
            pass
    body = "\n\n".join(part.strip() for part in parts if part.strip()).strip()
    return body[:limit]


def message_attachments(message: email.message.Message) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for part in message.walk() if message.is_multipart() else []:
        if part.get_content_disposition() != "attachment":
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            {
                "filename": decode_message_header(part.get_filename() or ""),
                "content_type": part.get_content_type(),
                "size_bytes": len(payload),
            }
        )
    return attachments


def attachment_risk(attachments: list[dict[str, Any]]) -> str:
    risky_ext = {
        ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar", ".msi", ".scr",
        ".html", ".htm", ".zip", ".rar", ".7z", ".docm", ".xlsm",
    }
    for item in attachments:
        filename = str(item.get("filename") or "").lower()
        if Path(filename).suffix in risky_ext:
            return "needs_review"
    return "metadata_only" if attachments else "none"


def parse_inbound_message(raw: bytes, *, account: str, folder: str, uid: str, uidvalidity: str) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    message_id = sanitize_header_value(str(message.get("Message-ID") or ""), 500)
    subject = decode_message_header(message.get("Subject"))
    received_at = ""
    try:
        received_at = parsedate_to_datetime(str(message.get("Date") or "")).isoformat()
    except Exception:
        received_at = ""
    body = message_text_body(message)
    attachments = message_attachments(message)
    content_hash = hashlib.sha256(raw).hexdigest()
    stable_key_material = "|".join([account, folder, uidvalidity, uid, message_id, content_hash])
    inbound_id = hashlib.sha256(stable_key_material.encode("utf-8")).hexdigest()[:32]
    return {
        "schema": "email_inbound_message.v1",
        "inbound_message_id": inbound_id,
        "source_account": account,
        "folder": folder,
        "uidvalidity": uidvalidity,
        "uid": uid,
        "message_id_header": message_id,
        "from": email_addresses(message.get("From")),
        "to": email_addresses(message.get("To")),
        "cc": email_addresses(message.get("Cc")),
        "subject": subject,
        "received_at": received_at,
        "body_text": body,
        "body_preview": body[:500],
        "attachments": attachments,
        "attachment_policy": "metadata_only",
        "risk_level": attachment_risk(attachments),
        "content_hash": content_hash,
        "status": "new",
        "created_at": now_beijing().isoformat(),
        "updated_at": now_beijing().isoformat(),
    }


def resolve_inbox_identity(account: str, identity_name: str = "") -> Identity | None:
    _, identities = load_world()
    if identity_name:
        return identity_by_name(identities, identity_name)
    return identity_for_account(identities, account)


def inbox_message_path(inbound_message_id: str) -> Path:
    return INBOX_MESSAGES_DIR / f"{slugify(inbound_message_id)}.json"


def inbox_snapshot() -> dict[str, Any]:
    state = load_inbox_state()
    index = read_json(INBOX_INDEX_PATH)
    messages = [read_json(path) for path in INBOX_MESSAGES_DIR.glob("*.json")]
    status_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    for item in messages:
        status = str(item.get("status") or "unknown")
        risk = str(item.get("risk_level") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
    return {
        "ok": True,
        "message_count": len(messages),
        "status_counts": status_counts,
        "risk_counts": risk_counts,
        "state_path": str(INBOX_STATE_PATH),
        "messages_dir": str(INBOX_MESSAGES_DIR),
        "jobs_dir": str(INBOX_JOBS_DIR),
        "index_path": str(INBOX_INDEX_PATH),
        "job_counts": index.get("counts", {}) if isinstance(index, dict) else {},
        "accounts": sorted(state.get("accounts", {}).keys()),
        "updated_at": state.get("updated_at", ""),
    }


def inbox_validate(account: str = "3633922805@qq.com", identity_name: str = "") -> dict[str, Any]:
    identity = resolve_inbox_identity(account, identity_name)
    issues: list[str] = []
    if not identity:
        issues.append(f"inbox identity missing for account: {account}")
    else:
        if not identity.default_account:
            issues.append("inbox identity has no account")
        if not identity.imap_host:
            issues.append(f"imap host missing for identity: {identity.name}")
        if not identity.imap_auth_code:
            issues.append(f"imap auth code missing for identity: {identity.name}")
        if "ssl" not in identity.imap_encryption.lower() and "tls" not in identity.imap_encryption.lower():
            issues.append(f"imap encryption must be SSL/TLS for identity: {identity.name}")
    return {
        "ok": not issues,
        "account": account,
        "identity": identity.name if identity else "",
        "issues": issues,
        "checks": {
            "messages_dir": str(INBOX_MESSAGES_DIR),
            "state_path": str(INBOX_STATE_PATH),
            "default_read_only": True,
            "default_mark_seen": False,
            "attachment_policy": "metadata_only",
        },
    }


def inbox_fetch(account: str = "3633922805@qq.com", identity_name: str = "", folder: str = "INBOX", limit: int = 10, apply: bool = False) -> dict[str, Any]:
    identity = resolve_inbox_identity(account, identity_name)
    validation = inbox_validate(account, identity_name)
    if not validation["ok"] or identity is None:
        return {"ok": False, "dry_run": not apply, "issues": validation["issues"], "validation": validation}
    fetched: list[dict[str, Any]] = []
    skipped_existing = 0
    state = load_inbox_state()
    account_state = state.setdefault("accounts", {}).setdefault(account, {})
    try:
        if "ssl" in identity.imap_encryption.lower() or "tls" in identity.imap_encryption.lower():
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(identity.imap_host, identity.imap_port)
        else:
            return {"ok": False, "dry_run": not apply, "issues": ["only SSL/TLS IMAP is supported"], "validation": validation}
        try:
            client.login(account, identity.imap_auth_code)
            typ, _ = client.select(folder, readonly=True)
            if typ != "OK":
                return {"ok": False, "dry_run": not apply, "issues": [f"imap folder select failed: {folder}"], "validation": validation}
            uidvalidity_resp = client.response("UIDVALIDITY")[1]
            uidvalidity = uidvalidity_resp[0].decode("ascii", errors="ignore") if uidvalidity_resp and uidvalidity_resp[0] else ""
            typ, data = client.uid("search", None, "ALL")
            if typ != "OK" or not data:
                return {"ok": False, "dry_run": not apply, "issues": ["imap uid search failed"], "validation": validation}
            uids = data[0].split()[-max(1, limit):]
            for uid_bytes in uids:
                uid = uid_bytes.decode("ascii", errors="ignore")
                typ, fetch_data = client.uid("fetch", uid, "(BODY.PEEK[] FLAGS INTERNALDATE)")
                if typ != "OK":
                    continue
                raw = b""
                for item in fetch_data:
                    if isinstance(item, tuple) and isinstance(item[1], (bytes, bytearray)):
                        raw = bytes(item[1])
                        break
                if not raw:
                    continue
                parsed = parse_inbound_message(raw, account=account, folder=folder, uid=uid, uidvalidity=uidvalidity)
                path = inbox_message_path(parsed["inbound_message_id"])
                if path.exists():
                    skipped_existing += 1
                    continue
                fetched.append(parsed)
                if apply:
                    atomic_write_json(path, parsed)
                    sync_human_mailbox_item("inbox", parsed)
            if apply:
                account_state["last_fetch_at"] = now_beijing().isoformat()
                account_state["last_folder"] = folder
                account_state["last_seen_uid"] = uids[-1].decode("ascii", errors="ignore") if uids else ""
                account_state["uidvalidity"] = uidvalidity
                save_inbox_state(state)
        finally:
            try:
                client.logout()
            except Exception:
                pass
    except Exception as exc:
        return {
            "ok": False,
            "dry_run": not apply,
            "issues": [f"imap fetch failed: {type(exc).__name__}: {exc}"],
            "validation": validation,
        }
    return {
        "ok": True,
        "dry_run": not apply,
        "account": account,
        "folder": folder,
        "limit": limit,
        "fetched_count": len(fetched),
        "skipped_existing": skipped_existing,
        "written": apply,
        "messages": [
            {
                "inbound_message_id": item["inbound_message_id"],
                "from": item["from"],
                "subject": item["subject"],
                "received_at": item["received_at"],
                "risk_level": item["risk_level"],
                "attachment_count": len(item["attachments"]),
                "status": item["status"],
            }
            for item in fetched
        ],
        "validation": validation,
    }


def sync_inbox_human_mirror() -> dict[str, Any]:
    ensure_human_mailbox_roots()
    mirrored: list[str] = []
    for path in sorted(INBOX_MESSAGES_DIR.glob("*.json")):
        payload = read_json(path)
        if not payload:
            continue
        target = sync_human_mailbox_item("inbox", payload)
        if target:
            mirrored.append(str(target))
    return {"ok": True, "mirrored_count": len(mirrored), "paths": mirrored}


def inbox_job_id(inbound_message_id: str) -> str:
    return build_artifact_key("inbox", inbound_message_id, "job")


def inbox_job_path(inbox_job_id_value: str) -> Path:
    return INBOX_JOBS_DIR / f"{slugify(inbox_job_id_value)}.json"


def load_inbox_job(inbox_job_id_value: str) -> dict[str, Any]:
    return read_json(inbox_job_path(inbox_job_id_value))


def load_inbox_message(inbound_message_id: str) -> dict[str, Any]:
    return read_json(inbox_message_path(inbound_message_id))


def inbound_needs_processing(message: dict[str, Any]) -> bool:
    subject = str(message.get("subject") or "").strip()
    if not subject:
        return False
    normalized = re.sub(r"\s+", "", subject).lower()
    reply_prefixes = ("re:", "回复:", "答复:")
    if normalized.startswith(reply_prefixes):
        return False
    return "待处理" in subject


def inbox_sequence_key(message: dict[str, Any]) -> tuple[str, int, int, str]:
    received = str(message.get("received_at") or "")
    uidvalidity = int(str(message.get("uidvalidity") or "0") or 0)
    uid = int(str(message.get("uid") or "0") or 0)
    created = str(message.get("created_at") or "")
    return (received, uidvalidity, uid, created)


def inbox_resource_class(message: dict[str, Any]) -> tuple[str, int]:
    if message.get("risk_level") == "needs_review":
        return "review_required", 100
    if message.get("attachments"):
        return "attachment_metadata", 30
    return "codex_generation", 50


def build_inbox_job(message: dict[str, Any]) -> dict[str, Any]:
    resource_class, cost_score = inbox_resource_class(message)
    status = INBOX_JOB_NEEDS_REVIEW if resource_class == "review_required" else INBOX_JOB_QUEUED
    inbound_id = str(message.get("inbound_message_id") or "")
    return {
        "schema": "email_inbox_job.v1",
        "inbox_job_id": inbox_job_id(inbound_id),
        "status": status,
        "inbound_message_id": inbound_id,
        "source_account": message.get("source_account", ""),
        "from": message.get("from", []),
        "to": message.get("to", []),
        "subject": message.get("subject", ""),
        "received_at": message.get("received_at", ""),
        "uidvalidity": message.get("uidvalidity", ""),
        "uid": message.get("uid", ""),
        "message_id_header": message.get("message_id_header", ""),
        "body_preview": message.get("body_preview", ""),
        "inbound_payload_ref": inbound_id,
        "attachments": message.get("attachments", []),
        "risk_level": message.get("risk_level", "none"),
        "mail_kind": "reply",
        "resource_class": resource_class,
        "cost_score": cost_score,
        "sequence_key": list(inbox_sequence_key(message)),
        "created_at": now_beijing().isoformat(),
        "updated_at": now_beijing().isoformat(),
    }


def refresh_inbox_index() -> dict[str, Any]:
    created_jobs: list[str] = []
    messages = [read_json(path) for path in INBOX_MESSAGES_DIR.glob("*.json")]
    for message in sorted(messages, key=inbox_sequence_key):
        inbound_id = str(message.get("inbound_message_id") or "")
        if not inbound_id or not inbound_needs_processing(message):
            continue
        job_id = inbox_job_id(inbound_id)
        path = inbox_job_path(job_id)
        if path.exists():
            continue
        job = build_inbox_job(message)
        atomic_write_json(path, job)
        created_jobs.append(job_id)
    jobs = [read_json(path) for path in INBOX_JOBS_DIR.glob("*.json")]
    queued = [job for job in jobs if job.get("status") == INBOX_JOB_QUEUED]
    review = [job for job in jobs if job.get("status") == INBOX_JOB_NEEDS_REVIEW]
    active = [job for job in jobs if job.get("status") == INBOX_JOB_PROCESSING]
    done = [job for job in jobs if job.get("status") in {INBOX_JOB_REPLY_TASK_CREATED, INBOX_JOB_REPLY_DRAFTED, INBOX_JOB_PROCESSED}]
    failed = [job for job in jobs if job.get("status") in {INBOX_JOB_FAILED, INBOX_JOB_DEAD_LETTER}]
    queued.sort(key=lambda job: tuple(job.get("sequence_key") or []))
    index = {
        "ok": True,
        "updated_at": now_beijing().isoformat(),
        "created_jobs": created_jobs,
        "queued": queued,
        "needs_review": review,
        "processing": active,
        "done": done,
        "failed": failed,
        "counts": {
            "queued": len(queued),
            "needs_review": len(review),
            "processing": len(active),
            "done": len(done),
            "failed": len(failed),
        },
        "ordering": "received_at -> uidvalidity -> uid -> created_at",
        "budget_policy": {
            "business_order": "inbox_order",
            "codex_generation_per_worker_tick": INBOX_WORKER_MAX_CODEX_JOBS,
        },
    }
    atomic_write_json(INBOX_INDEX_PATH, index)
    return index


def peek_inbox() -> dict[str, Any]:
    index = refresh_inbox_index()
    queued = index.get("queued") if isinstance(index.get("queued"), list) else []
    return {
        "ok": True,
        "available": bool(queued),
        "item": queued[0] if queued else {},
        "index": index,
    }


def poll_inbox(account: str = INBOX_DEFAULT_ACCOUNT, limit: int = INBOX_POLL_LIMIT) -> dict[str, Any]:
    fetch = inbox_fetch(account=account, limit=limit, apply=True)
    index = refresh_inbox_index()
    return {"ok": bool(fetch.get("ok")) and bool(index.get("ok")), "fetch": fetch, "index": index}


def inbox_attachment_review(inbox_job_id_value: str) -> dict[str, Any]:
    job = load_inbox_job(inbox_job_id_value)
    if not job:
        return {"ok": False, "reason": "inbox_job_not_found", "inbox_job_id": inbox_job_id_value}
    message = load_inbox_message(str(job.get("inbound_message_id") or ""))
    return attachment_review(job, message, INBOX_ATTACHMENTS_DIR)


def fetch_inbox_raw_for_job(job: dict[str, Any], identity_name: str = "") -> tuple[bytes, dict[str, Any] | None, list[str]]:
    account = str(job.get("source_account") or INBOX_DEFAULT_ACCOUNT)
    identity = resolve_inbox_identity(account, identity_name)
    validation = inbox_validate(account, identity_name)
    if not validation["ok"] or identity is None:
        return b"", identity, validation["issues"]
    folder = str(job.get("folder") or "INBOX")
    uid = str(job.get("uid") or "")
    if not uid:
        return b"", identity, ["inbox_uid_missing"]
    try:
        if "ssl" in identity.imap_encryption.lower() or "tls" in identity.imap_encryption.lower():
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(identity.imap_host, identity.imap_port)
        else:
            return b"", identity, ["only SSL/TLS IMAP is supported"]
        try:
            client.login(account, identity.imap_auth_code)
            typ, _ = client.select(folder, readonly=True)
            if typ != "OK":
                return b"", identity, [f"imap folder select failed: {folder}"]
            typ, fetch_data = client.uid("fetch", uid, "(BODY.PEEK[] FLAGS INTERNALDATE)")
            if typ != "OK":
                return b"", identity, [f"imap uid fetch failed: {uid}"]
            for item in fetch_data:
                if isinstance(item, tuple) and isinstance(item[1], (bytes, bytearray)):
                    return bytes(item[1]), identity, []
            return b"", identity, ["imap raw message missing"]
        finally:
            try:
                client.logout()
            except Exception:
                pass
    except Exception as exc:
        return b"", identity, [f"imap fetch failed: {type(exc).__name__}: {exc}"]


def save_inbox_attachments(
    inbox_job_id_value: str,
    *,
    apply: bool = False,
    identity_name: str = "",
    confirm_risky_attachments: str = "",
) -> dict[str, Any]:
    job = load_inbox_job(inbox_job_id_value)
    if not job:
        return {"ok": False, "reason": "inbox_job_not_found", "inbox_job_id": inbox_job_id_value}
    inbound_id = str(job.get("inbound_message_id") or "")
    if not inbound_id:
        return {"ok": False, "reason": "inbound_message_id_missing", "inbox_job_id": inbox_job_id_value}
    attachments = job.get("attachments") if isinstance(job.get("attachments"), list) else []
    if not attachments:
        return {"ok": False, "reason": "no_attachments", "inbox_job_id": inbox_job_id_value}
    if job.get("risk_level") == "needs_review" and confirm_risky_attachments != "confirm-risky-attachments":
        return {
            "ok": False,
            "reason": "risky_attachment_confirmation_required",
            "inbox_job_id": inbox_job_id_value,
            "required_confirmation": "confirm-risky-attachments",
            "review": inbox_attachment_review(inbox_job_id_value),
        }
    raw, _identity, issues = fetch_inbox_raw_for_job(job, identity_name)
    if issues:
        return {"ok": False, "reason": "imap_fetch_failed", "issues": issues, "inbox_job_id": inbox_job_id_value}
    materialized = extract_attachments_from_raw(
        raw,
        inbound_message_id=inbound_id,
        attachment_root=INBOX_ATTACHMENTS_DIR,
        max_single_bytes=MAX_ATTACHMENT_BYTES,
        max_total_bytes=MAX_ATTACHMENT_TOTAL_BYTES,
        apply=apply,
    )
    result = {
        **materialized,
        "inbox_job_id": inbox_job_id_value,
        "job_status": job.get("status", ""),
        "downstream_policy": "mail_attachment_first",
    }
    if apply and materialized.get("ok"):
        message = load_inbox_message(inbound_id)
        saved_attachments = materialized.get("attachments") if isinstance(materialized.get("attachments"), list) else []
        job["attachments"] = saved_attachments
        job["attachment_policy"] = "email_owned_saved"
        ready, ready_issues = saved_attachments_ready(saved_attachments)
        if ready and job.get("resource_class") == "attachment_metadata" and job.get("status") in {INBOX_JOB_NEEDS_REVIEW, INBOX_JOB_QUEUED}:
            job["status"] = INBOX_JOB_QUEUED
            job.pop("review_reason", None)
            job["codex_context_policy"] = "include_saved_inbound_attachments"
        elif ready_issues:
            job["review_reason"] = "attachment_context_not_ready:" + ",".join(ready_issues)
        job["updated_at"] = now_beijing().isoformat()
        atomic_write_json(inbox_job_path(inbox_job_id_value), job)
        result["job_status"] = job.get("status", "")
        result["codex_context_policy"] = job.get("codex_context_policy", "")
        if message:
            message["attachments"] = saved_attachments
            message["attachment_policy"] = "email_owned_saved"
            message["updated_at"] = now_beijing().isoformat()
            atomic_write_json(inbox_message_path(inbound_id), message)
        refresh_inbox_index()
        result["updated_job"] = True
    return result


def inbox_job_needs_attachment_materialization(job: dict[str, Any]) -> tuple[bool, list[str]]:
    if job.get("resource_class") != "attachment_metadata":
        return False, ["resource_class_not_attachment_metadata"]
    if job.get("status") not in {INBOX_JOB_QUEUED, INBOX_JOB_NEEDS_REVIEW}:
        return False, [f"status_{job.get('status')}"]
    if job.get("risk_level") != "metadata_only":
        return False, [f"risk_{job.get('risk_level')}"]
    attachments = job.get("attachments") if isinstance(job.get("attachments"), list) else []
    ready, issues = saved_attachments_ready(attachments)
    return not ready, issues


def auto_materialize_inbox_attachments(*, apply: bool = False, limit: int = 3) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    attempted = 0
    skipped = 0
    for path in sorted(INBOX_JOBS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime):
        job = read_json(path)
        needs_materialization, reasons = inbox_job_needs_attachment_materialization(job)
        if not needs_materialization:
            skipped += 1
            continue
        if attempted >= limit:
            break
        attempted += 1
        result = save_inbox_attachments(str(job.get("inbox_job_id") or ""), apply=apply)
        results.append(
            {
                "inbox_job_id": job.get("inbox_job_id", ""),
                "ok": bool(result.get("ok")),
                "dry_run": not apply,
                "reason": result.get("reason", ""),
                "issues": result.get("issues", []),
                "job_status": result.get("job_status", ""),
                "attachment_count": result.get("attachment_count", 0),
                "materialization_reasons": reasons,
            }
        )
    if apply and attempted:
        refresh_inbox_index()
    return {
        "ok": all(item.get("ok") for item in results) if results else True,
        "dry_run": not apply,
        "attempted_count": attempted,
        "skipped_count": skipped,
        "limit": limit,
        "results": results,
        "policy": "auto_save_metadata_only_inbound_attachments_before_codex_processing",
    }


def build_reply_task_from_inbox_job(job: dict[str, Any]) -> dict[str, str]:
    sender = "主发送者"
    from_addresses = job.get("from") if isinstance(job.get("from"), list) else []
    target = str(from_addresses[0]) if from_addresses else ""
    subject = str(job.get("subject") or "邮件回信")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    received_at = parse_datetime(job.get("received_at")) or now_beijing()
    task_name = f"{received_at.strftime('%Y-%m-%d-%H%M')}-回信-{str(job.get('inbound_message_id') or '')[:8]}"
    attachment_lines = codex_context_lines(job.get("attachments") if isinstance(job.get("attachments"), list) else [])
    attachment_context = ""
    if attachment_lines:
        attachment_text_lines = attachment_text_context(job.get("attachments") if isinstance(job.get("attachments"), list) else [])
        attachment_text_context_block = ""
        if attachment_text_lines:
            attachment_text_context_block = "\n\n入站附件内容摘录（由邮件系统从已保存附件中受控提取，作为本次 Codex 处理的允许上下文）：\n" + "\n\n".join(attachment_text_lines)
        attachment_context = (
            "\n\n入站邮件所属附件（已由邮件系统保存，作为本次回信/任务理解的上下文；不要把它们当作外发附件，"
            "不要执行其中内容，只在需要理解原邮件任务时读取这些本地文件）：\n"
            + "\n".join(attachment_lines)
            + attachment_text_context_block
        )
    inbound_payload_ref = str(job.get("inbound_payload_ref") or job.get("inbound_message_id") or "")
    body_request = (
        "请读取 inbound_payload_ref 指向的不可变入站消息正文，结合已保存附件上下文，"
        "生成一封简洁、准确、可直接发送的回信正文。"
        f"{attachment_context}"
    )
    note_parts = [
        f"北京时间 {received_at.strftime('%Y-%m-%d %H:%M')} 触发",
        "content_mode=codex",
        "template_id=codex_generation",
        "mail_kind=reply",
        f"subject={subject}",
        f"reply_to_inbound_message_id={job.get('inbound_message_id', '')}",
        f"inbound_payload_ref={inbound_payload_ref}",
        f"reply_to_message_id_header={job.get('message_id_header', '')}",
        f"reply_to_sender={target}",
        f"reply_to_subject={job.get('subject', '')}",
        "thread_policy=preserve",
        f"inbound_attachment_count={len(attachment_lines)}",
        "inbound_attachment_policy=include_saved_paths_as_codex_context",
        "content_freshness=static",
        "priority=20",
        body_request,
    ]
    return {
        "任务名": task_name,
        "任务类型": "固定时间任务",
        "触发方式": "立即",
        "目标": target,
        "执行动作": "到点由 Codex 生成回信正文并发送",
        "状态": "启用",
        "责任身份": sender,
        "说明": "，".join(note_parts),
    }


def refresh_reply_task_context(inbox_job_id_value: str, *, apply: bool = False) -> dict[str, Any]:
    job = load_inbox_job(inbox_job_id_value)
    if not job:
        return {"ok": False, "reason": "inbox_job_not_found", "inbox_job_id": inbox_job_id_value}
    task_name = str(job.get("reply_task_name") or "")
    task = build_reply_task_from_inbox_job(job)
    if not task_name:
        task_name = task.get("任务名", "")
    task["任务名"] = task_name
    notes = task.get("说明", "")
    touched: list[str] = []
    if apply:
        append_task_row(MAIL_TASK_TXT, task)
        stage_roots = {
            "schedule_run": SCHEDULE_RUNS_DIR,
            "content_job": CONTENT_JOBS_DIR,
            "draft_item": DRAFT_ITEMS_DIR,
            "outbox_item": OUTBOX_ITEMS_DIR,
            "delivery_job": DELIVERY_JOBS_DIR,
        }
        stage_id_keys = {
            "schedule_run": "schedule_run_id",
            "content_job": "content_job_id",
            "draft_item": "draft_item_id",
            "outbox_item": "outbox_item_id",
            "delivery_job": "delivery_job_id",
        }
        for stage in ("schedule_run", "content_job", "draft_item", "outbox_item", "delivery_job"):
            root = stage_roots[stage]
            for path in root.glob("*.json"):
                payload = read_json(path)
                if str(payload.get("task_name") or "") != task_name:
                    continue
                if payload.get("status") in {STATUS_SENT, STATUS_SKIPPED, STATUS_ARCHIVED}:
                    continue
                payload["task"] = task
                payload["context_refreshed_at"] = now_beijing().isoformat()
                payload["context_refresh_reason"] = "inbound_attachment_context_refreshed"
                write_stage(stage, payload)
                touched.append(str(payload.get(stage_id_keys[stage]) or path.stem))
    return {
        "ok": True,
        "dry_run": not apply,
        "inbox_job_id": inbox_job_id_value,
        "task_name": task_name,
        "attachment_excerpt_available": "文本摘录" in notes and "attachment_text_unavailable" not in notes,
        "task": task,
        "touched": touched,
    }


def process_inbox_job(job: dict[str, Any], identities: dict[str, Identity]) -> dict[str, Any]:
    job_id = str(job.get("inbox_job_id") or "")
    path = inbox_job_path(job_id)
    if job.get("status") != INBOX_JOB_QUEUED:
        return {"ok": True, "skipped": True, "reason": f"status_{job.get('status')}", "inbox_job_id": job_id}
    resource_class = str(job.get("resource_class") or "")
    attachment_ready = False
    attachment_issues: list[str] = []
    if resource_class == "attachment_metadata":
        attachment_ready, attachment_issues = saved_attachments_ready(job.get("attachments") if isinstance(job.get("attachments"), list) else [])
        if not attachment_ready:
            needs_materialization, _reasons = inbox_job_needs_attachment_materialization(job)
            if needs_materialization:
                materialized = save_inbox_attachments(job_id, apply=True)
                if materialized.get("ok"):
                    job = load_inbox_job(job_id)
                    attachment_ready, attachment_issues = saved_attachments_ready(job.get("attachments") if isinstance(job.get("attachments"), list) else [])
                else:
                    attachment_issues = materialized.get("issues") if isinstance(materialized.get("issues"), list) else [str(materialized.get("reason") or "attachment_materialization_failed")]
                    job["auto_materialize_result"] = materialized
    if resource_class != "codex_generation" and not attachment_ready:
        job["status"] = INBOX_JOB_NEEDS_REVIEW
        job["updated_at"] = now_beijing().isoformat()
        if attachment_issues:
            job["review_reason"] = "attachment_context_not_ready:" + ",".join(attachment_issues)
        else:
            job["review_reason"] = f"resource_class_{job.get('resource_class')}"
        atomic_write_json(path, job)
        return {"ok": True, "skipped": True, "needs_review": True, "inbox_job_id": job_id}
    job["status"] = INBOX_JOB_PROCESSING
    job["started_at"] = now_beijing().isoformat()
    job["updated_at"] = now_beijing().isoformat()
    atomic_write_json(path, job)
    try:
        task = build_reply_task_from_inbox_job(job)
        if not task.get("目标"):
            raise RuntimeError("reply target missing")
        append_task_row(MAIL_TASK_TXT, task)
        result = {
            "ok": True,
            "created": True,
            "stage": "mail_task_created",
            "dispatch": "deferred_to_unified_due_poll",
            "task_name": task["任务名"],
        }
        job["status"] = INBOX_JOB_REPLY_TASK_CREATED
        job["reply_task_name"] = task["任务名"]
        job["reply_job"] = result
        job["finished_at"] = now_beijing().isoformat()
        job["updated_at"] = now_beijing().isoformat()
        atomic_write_json(path, job)
        refresh_inbox_index()
        return {"ok": True, "inbox_job_id": job_id, "reply_task_name": task["任务名"], "reply_job": result}
    except Exception as exc:
        job["status"] = INBOX_JOB_FAILED
        job["last_error"] = f"{type(exc).__name__}: {exc}"
        job["finished_at"] = now_beijing().isoformat()
        job["updated_at"] = now_beijing().isoformat()
        atomic_write_json(path, job)
        refresh_inbox_index()
        return {"ok": False, "inbox_job_id": job_id, "reason": job["last_error"]}


def cleanup_smoke_artifacts(prefixes: tuple[str, ...] | None = None, older_than_seconds: int | None = None) -> list[str]:
    removed: list[str] = []
    targets = prefixes or ("smoke-draft-test-", "smoke-multi-recipient-")
    cutoff = time.time() - older_than_seconds if older_than_seconds is not None else None
    for root in (CONTENT_JOBS_DIR, DRAFT_ITEMS_DIR, OUTBOX_ITEMS_DIR, DELIVERY_JOBS_DIR, SCHEDULE_RUNS_DIR, SMTP_RECEIPTS_DIR):
        for path in root.glob("*.json"):
            if not any(prefix in path.stem for prefix in targets):
                continue
            if cutoff is not None and path.stat().st_mtime >= cutoff:
                continue
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:
                pass
    if OUTBOX_INDEX_PATH.exists():
        refresh_outbox_index()
    return removed


def metrics() -> dict[str, Any]:
    ensure_human_mailbox_roots()
    tasks, identities = load_world()
    runtime = [build_task_runtime(task, identities) for task in tasks]
    due = [item for item in runtime if item.get("due")]
    actionable_due = [item for task, item in zip(tasks, runtime) if task_is_actionable_due(task, item)]
    enabled = [task for task in tasks if task.get("状态") == "启用"]
    log_path = EMAIL_LOG_DIR / "email-scheduler.log"
    heartbeat = read_json(HEARTBEAT_PATH)
    unified_tasks = read_json(UNIFIED_SCHEDULER_TASKS)
    unified_enabled = False
    for task in unified_tasks.get("tasks", []) if isinstance(unified_tasks, dict) else []:
        if task.get("id") == "email_scheduler_run_due" and task.get("enabled"):
            unified_enabled = True
            break
    job_counts = count_stage_statuses(EMAIL_JOBS_DIR)
    schedule_run_counts = count_stage_statuses(SCHEDULE_RUNS_DIR)
    content_job_counts = count_stage_statuses(CONTENT_JOBS_DIR)
    draft_item_counts = count_stage_statuses(DRAFT_ITEMS_DIR)
    outbox_item_counts = count_stage_statuses(OUTBOX_ITEMS_DIR)
    delivery_job_counts = count_stage_statuses(DELIVERY_JOBS_DIR)
    refresh_inbox_index()
    inbox_data = inbox_snapshot()
    outbox_index = refresh_outbox_index()
    return {
        "ok": True,
        "task_count": len(tasks),
        "enabled_task_count": len(enabled),
        "identity_count": len(identities),
        "due_task_count": len(due),
        "due_task_names": [item["task_name"] for item in due],
        "actionable_due_task_count": len(actionable_due),
        "actionable_due_task_names": [item["task_name"] for item in actionable_due],
        "mail_record_root": str(MAIL_RECORDS_ROOT),
        "schedule_record_root": str(SCHEDULE_RECORDS_ROOT),
        "runtime": {
            "trigger_owner": "unified_scheduler" if unified_enabled else "email_scheduler_loop",
            "unified_scheduler_task_enabled": unified_enabled,
            "heartbeat_path": str(HEARTBEAT_PATH),
            "heartbeat": heartbeat,
            "lock_path": str(LOCK_PATH),
            "log_path": str(log_path),
            "log_bytes": log_path.stat().st_size if log_path.exists() else 0,
            "max_log_bytes": MAX_LOG_BYTES,
            "loop_interval_seconds": DEFAULT_LOOP_SECONDS,
            "jobs_dir": str(EMAIL_JOBS_DIR),
            "job_counts": job_counts,
            "schedule_runs_dir": str(SCHEDULE_RUNS_DIR),
            "content_jobs_dir": str(CONTENT_JOBS_DIR),
            "draft_items_dir": str(DRAFT_ITEMS_DIR),
            "outbox_items_dir": str(OUTBOX_ITEMS_DIR),
            "outbox_index_path": str(OUTBOX_INDEX_PATH),
            "delivery_jobs_dir": str(DELIVERY_JOBS_DIR),
            "smtp_receipts_dir": str(SMTP_RECEIPTS_DIR),
            "inbox_messages_dir": str(INBOX_MESSAGES_DIR),
            "inbox_state_path": str(INBOX_STATE_PATH),
            "schedule_run_counts": schedule_run_counts,
            "content_job_counts": content_job_counts,
            "draft_item_counts": draft_item_counts,
            "outbox_item_counts": outbox_item_counts,
            "outbox_queue_counts": {
                "ready_queue": len(outbox_index.get("ready_queue", [])),
                "future_queue": len(outbox_index.get("future_queue", [])),
                "blocked": len(outbox_index.get("blocked", [])),
                "expired": len(outbox_index.get("expired", [])),
                "stale": len(outbox_index.get("stale", [])),
                "sent": len(outbox_index.get("sent", [])),
            },
            "delivery_job_counts": delivery_job_counts,
            "smtp_receipt_count": count_smtp_receipts(),
            "inbox": inbox_data,
            "smtp_mime_policy": {
                "header_charset": SMTP_HEADER_CHARSET,
                "body_charset": SMTP_BODY_CHARSET,
                "body_cte": SMTP_BODY_CTE,
            },
            "content_retry_waiting": count_retry_waiting(CONTENT_JOBS_DIR),
            "delivery_retry_waiting": count_retry_waiting(DELIVERY_JOBS_DIR),
            "runnable_stage_jobs": has_runnable_stage_jobs(),
            "direct_send_default": "disabled",
            "statuses": {
                "runnable": sorted(RUNNABLE_STATUSES),
                "retryable": sorted(RETRYABLE_STATUSES),
                "terminal": sorted(TERMINAL_STATUSES),
                "existing_run": sorted(EXISTING_RUN_STATUSES),
            },
            "retry_policy": {
                "content_job_max_attempts": CONTENT_JOB_MAX_ATTEMPTS,
                "content_job_retry_base_seconds": CONTENT_JOB_RETRY_BASE_SECONDS,
                "delivery_job_max_attempts": DELIVERY_JOB_MAX_ATTEMPTS,
                "delivery_job_retry_base_seconds": DELIVERY_JOB_RETRY_BASE_SECONDS,
            },
        },
    }


def snapshot() -> dict[str, Any]:
    tasks, identities = load_world()
    task_rows = []
    for task in tasks:
        runtime = build_task_runtime(task, identities)
        task_rows.append(
            {
                "task_name": runtime["task_name"],
                "trigger": runtime["trigger"],
                "due": runtime["due"],
                "sender": runtime["sender"].name if runtime["sender"] else "",
                "recipient": runtime["recipient"].name if runtime["recipient"] else "",
                "scheduled_at": runtime["scheduled_at"].isoformat() if runtime["scheduled_at"] else "",
            }
        )
    return {
        "ok": True,
        "identity_names": sorted(identities.keys()),
        "tasks": task_rows,
        "inbox": inbox_snapshot(),
    }


def validate() -> dict[str, Any]:
    tasks, identities = load_world()
    issues: list[str] = []
    if not tasks:
        issues.append(f"mail task table missing or empty: {MAIL_TASK_TXT}")
    if not identities:
        issues.append(f"identity table missing or empty: {MAIL_IDENTITY_TXT}")
    inbox_check = inbox_validate()
    if not inbox_check["ok"]:
        issues.extend(f"inbox: {issue}" for issue in inbox_check["issues"])

    for task in tasks:
        runtime = build_task_runtime(task, identities)
        if not is_executable_mail_row(task) or not runtime["mail_task"]:
            continue
        if not runtime["sender"]:
            issues.append(f"sender missing for task {task.get('任务名', '')}")
        if not runtime["recipients"]:
            issues.append(f"recipient missing for task {task.get('任务名', '')}")
        if runtime["explicit_schedule"] and runtime["scheduled_at"] is None:
            issues.append(f"scheduled time missing for task {task.get('任务名', '')}")
        for issue in runtime.get("attachment_issues", []):
            issues.append(f"attachment issue for task {task.get('任务名', '')}: {issue}")

    smoke = smoke_test()
    if not smoke["ok"]:
        issues.extend(f"smoke: {issue}" for issue in smoke["issues"])

    return {
        "ok": not issues,
        "issues": issues,
        "task_count": len(tasks),
        "identity_count": len(identities),
        "inbox": inbox_check,
        "smoke": smoke,
    }


def quick_check() -> dict[str, Any]:
    tasks, identities = load_world()
    issues: list[str] = []
    if not identities:
        issues.append(f"identity table missing or empty: {MAIL_IDENTITY_TXT}")
    executable = [task for task in tasks if is_executable_mail_row(task)]
    smoke = smoke_test()
    if not smoke["ok"]:
        issues.extend(f"smoke: {issue}" for issue in smoke["issues"])
    return {
        "ok": not issues,
        "mode": "quick",
        "checks": {
            "identity_count": len(identities),
            "task_count": len(tasks),
            "executable_task_count": len(executable),
            "smoke_ok": smoke["ok"],
        },
        "issues": issues,
        "advisory": "Fast path only: use doctor/inspect-run after failures or system-level changes.",
    }


def smoke_test() -> dict[str, Any]:
    lock = SingleInstanceLock(EMAIL_SMOKE_LOCK_PATH)
    deadline = time.time() + 30
    while not lock.acquire():
        if time.time() >= deadline:
            return {
                "ok": False,
                "issues": ["email smoke test lock busy; another maintenance check is still running"],
                "checks": {"smoke_lock_acquired": False},
            }
        time.sleep(0.2)
    try:
        return smoke_test_unlocked()
    finally:
        lock.release()


def smoke_test_unlocked() -> dict[str, Any]:
    old_disable_mirror = os.environ.get("EMAIL_SCHEDULER_DISABLE_HUMAN_MIRROR")
    os.environ["EMAIL_SCHEDULER_DISABLE_HUMAN_MIRROR"] = "1"
    smoke_prefix = f"smoke-draft-test-{os.getpid()}-{int(time.time() * 1000)}"
    cleanup_smoke_artifacts(older_than_seconds=300)
    issues: list[str] = []
    bad_header = "bad\r\nBcc: injected\tvalue"
    sanitized = sanitize_header_value(bad_header)
    if any(ch in sanitized for ch in "\r\n\t"):
        issues.append("sanitize_header_value must remove CR/LF/TAB from SMTP headers")
    encoded = encode_mail_header(bad_header)
    if any(ch in encoded for ch in "\r\n"):
        issues.append("encode_mail_header must not emit raw CR/LF")

    report_task = {
        "任务名": "2026-06-29-1640-电脑性能报告-备用发送者",
        "触发方式": "固定时间任务",
        "周期": "单次",
        "目标": "备用发送者",
        "执行动作": "到点调用报告 provider performance_report 生成正文并发送",
        "状态": "启用",
        "责任身份": "主发送者",
        "说明": "北京时间 2026-06-29 16:40 触发，content_mode=command_report，template_id=performance_report，report_provider=performance_report，电脑性能报告",
    }
    if detect_content_mode(report_task) != "command_report":
        issues.append("performance report task must route to command_report")
    if detect_report_provider(report_task) != "performance_report":
        issues.append("performance report task must resolve performance_report provider")
    if build_subject(report_task) != "电脑性能报告":
        issues.append("performance report subject must be stable and human-readable")
    report_profile, report_profile_source = resolve_codex_mcp_profile(report_task)
    if report_profile != CODEX_MCP_PROFILE_NONE and report_profile != CODEX_MCP_PROFILE_MAINTENANCE:
        issues.append("performance report task must not route through research/full Codex profile")
    smoke_output_path = EMAIL_STATE_DIR / "smoke-codex-output.txt"
    none_codex_cmd = build_codex_exec_command(smoke_output_path, CODEX_MCP_PROFILE_NONE)
    maintenance_codex_cmd = build_codex_exec_command(smoke_output_path, CODEX_MCP_PROFILE_MAINTENANCE)
    research_codex_cmd = build_codex_exec_command(smoke_output_path, CODEX_MCP_PROFILE_RESEARCH)
    if "--ignore-user-config" in none_codex_cmd or "--ignore-user-config" in maintenance_codex_cmd:
        issues.append("background Codex generation must preserve user config for working model/provider")
    if "--ignore-rules" not in none_codex_cmd or "--ignore-rules" not in maintenance_codex_cmd:
        issues.append("sealed background Codex generation must ignore ambient rules")
    if "--sandbox" not in none_codex_cmd or "read-only" not in none_codex_cmd:
        issues.append("default background Codex generation must use read-only sandbox")
    if "--sandbox" not in maintenance_codex_cmd or "read-only" not in maintenance_codex_cmd:
        issues.append("maintenance background Codex generation must use read-only sandbox")
    if "--ignore-rules" in research_codex_cmd or "--sandbox" in research_codex_cmd:
        issues.append("research Codex generation must keep full configured MCP/tool profile")

    fake_identities = {
        "主发送者": Identity(name="主发送者", accounts=["sender@example.com"], smtp=["smtp.example.com", "465", "SSL/TLS", "auth"]),
        "备用发送者": Identity(name="备用发送者", accounts=["recipient@example.com"]),
    }
    template_text = read_text(MAIL_TEMPLATE_TABLE_TXT)
    for template_id in ("immediate_static_send", "scheduled_static_send", "realtime_ai_review"):
        if template_id not in template_text:
            issues.append(f"mail template table missing {template_id}")
    dry = dry_run_task(report_task, fake_identities)
    preview = dry.get("prompt_preview", "")
    if "no Codex prompt will be used" not in preview:
        issues.append("command_report dry-run must show script route instead of Codex prompt")
    if "你正在为一个自动定时邮件任务生成邮件正文" in preview:
        issues.append("command_report must not render Codex writing prompt")

    sample = json.dumps(
        {
            "generated_at": "2026-06-29T16:40:00+08:00",
            "profile": "quick",
            "observe_seconds": 3,
            "top_cpu": [{"name": "codex.exe", "pid": 1, "class": "codex", "cpu_percent_estimate": 10, "working_set_mb": 100}],
            "class_summary": [{"class": "codex", "process_count": 1, "cpu_percent_estimate": 10, "working_set_mb": 100}],
        },
        ensure_ascii=False,
    )
    formatted = format_performance_report(sample)
    if "电脑性能报告" not in formatted or "## CPU 热点" not in formatted:
        issues.append("performance report formatter must produce readable mail body")

    codex_task = {
        "任务名": "2026-06-29-1709-微信桥接系统全面分析-备用发送者",
        "执行动作": "到点由 Codex 实时生成正文并发送",
        "说明": "北京时间 2026-06-29 17:09 触发，content_mode=codex，template_id=realtime_ai_review，codex对微信桥接系统的全面分析",
    }
    if detect_content_mode(codex_task) != "codex":
        issues.append("explicit content_mode=codex must not be overridden by template_id keywords")
    codex_mode, codex_source = resolve_content_mode(codex_task, allow_keyword_fallback=True)
    if codex_mode != "codex" or codex_source != "metadata":
        issues.append("execution routing must prefer explicit content_mode metadata")

    report_intent = build_intent_task("备用发送者", "电脑性能报告", "今天16:40", "主发送者", "")
    report_meta = task_metadata(report_intent["task"])
    if report_meta.get("content_mode") != "command_report":
        issues.append("intent-created performance report must write content_mode=command_report")
    if report_meta.get("codex_mcp_profile") != CODEX_MCP_PROFILE_MAINTENANCE:
        issues.append("intent-created performance report must write codex_mcp_profile=maintenance")
    if report_meta.get("content_freshness") != "realtime":
        issues.append("intent-created performance report must write content_freshness=realtime")

    realtime_intent = build_intent_task("备用发送者", "中国AI圈实时评析", "明天12点", "主发送者", "")
    realtime_meta = task_metadata(realtime_intent["task"])
    if realtime_meta.get("content_mode") != "codex":
        issues.append("intent-created realtime analysis must write content_mode=codex")
    if realtime_meta.get("codex_mcp_profile") != CODEX_MCP_PROFILE_RESEARCH:
        issues.append("intent-created realtime analysis must write codex_mcp_profile=research")
    if realtime_meta.get("content_freshness") != "realtime":
        issues.append("intent-created realtime analysis must write content_freshness=realtime")
    online_research_intent = build_intent_task("备用发送者", "联网研究Windows代理机制后写一封报告", "明天9点", "主发送者", "研究报告")
    online_research_meta = task_metadata(online_research_intent["task"])
    if online_research_meta.get("content_mode") != "codex":
        issues.append("intent-created online research mail must write content_mode=codex")
    if online_research_meta.get("codex_mcp_profile") != CODEX_MCP_PROFILE_RESEARCH:
        issues.append("intent-created online research mail must write codex_mcp_profile=research")

    static_intent = build_intent_task("备用发送者", "固定正文：系统测试通知", "明天8点", "主发送者", "")
    static_meta = task_metadata(static_intent["task"])
    if static_meta.get("content_mode") != "static":
        issues.append("intent-created static mail must write content_mode=static")
    if static_meta.get("template_id") != "scheduled_static_send":
        issues.append("future static mail must use scheduled_static_send template")
    if static_meta.get("codex_mcp_profile") != CODEX_MCP_PROFILE_NONE:
        issues.append("intent-created static mail must write codex_mcp_profile=none")
    immediate_intent = build_intent_task("备用发送者", "固定正文：即时通知", "立即", "主发送者", "")
    immediate_meta = task_metadata(immediate_intent["task"])
    if immediate_meta.get("template_id") != "immediate_static_send":
        issues.append("immediate static mail must use immediate_static_send template")
    static_decision = automation_decision_for_intent(static_intent)
    if static_decision.get("automation_class") != "environment_auto" or not static_decision.get("can_auto_create"):
        issues.append("static mail intent should be auto-creatable by the environment")
    if static_decision.get("codex_role") != "none":
        issues.append("static mail intent should not require Codex work")
    realtime_decision = automation_decision_for_intent(realtime_intent)
    if realtime_decision.get("automation_class") != "codex_deferred" or not realtime_decision.get("can_auto_create"):
        issues.append("complete realtime analysis mail should be schedulable while deferring body generation to Codex")
    incomplete_intent = build_intent_task("备用发送者", "固定正文：缺少时间", "", "主发送者", "")
    incomplete_decision = automation_decision_for_intent(incomplete_intent)
    if incomplete_decision.get("automation_class") != "review_required" or incomplete_decision.get("can_auto_create"):
        issues.append("incomplete mail intent must stay out of auto-created task rows")

    safe_attachment_job = {
        "resource_class": "attachment_metadata",
        "status": INBOX_JOB_NEEDS_REVIEW,
        "risk_level": "metadata_only",
        "attachments": [{"filename": "example.pdf", "content_type": "application/pdf", "size_bytes": 1024}],
    }
    needs_materialization, materialization_reasons = inbox_job_needs_attachment_materialization(safe_attachment_job)
    if not needs_materialization or "attachment_1_saved_path_missing" not in materialization_reasons:
        issues.append("metadata-only inbound attachments must be auto-materialization candidates")
    risky_attachment_job = {
        "resource_class": "attachment_metadata",
        "status": INBOX_JOB_NEEDS_REVIEW,
        "risk_level": "needs_review",
        "attachments": [{"filename": "example.exe", "content_type": "application/octet-stream", "size_bytes": 1024}],
    }
    risky_needs_materialization, _ = inbox_job_needs_attachment_materialization(risky_attachment_job)
    if risky_needs_materialization:
        issues.append("risky inbound attachments must not auto-materialize without review")
    if not inbound_needs_processing({"subject": "待处理"}):
        issues.append("plain inbound 待处理 subject must create an inbox job")
    if inbound_needs_processing({"subject": "Re: 待处理"}):
        issues.append("reply subject Re: 待处理 must not create a new inbox job loop")

    fallback_task = {
        "任务名": "旧任务-实时评析",
        "执行动作": "到点由 Codex 实时生成正文并发送",
        "说明": "没有显式元数据的旧任务",
    }
    fallback_mode, fallback_source = resolve_content_mode(fallback_task, allow_keyword_fallback=True)
    if fallback_mode != "codex" or fallback_source != "keyword_fallback":
        issues.append("keyword routing should remain available only as legacy fallback")

    strict_prompt = render_task_prompt(codex_task, fake_identities["主发送者"], fake_identities["备用发送者"], now_beijing())
    if "strict_mail_generation_context" not in strict_prompt:
        issues.append("codex mail generation must use strict_mail_generation_context")
    if "current_chat_context" not in strict_prompt or "forbidden_sources" not in strict_prompt:
        issues.append("codex mail generation prompt must explicitly forbid current chat context")
    if "主题聚焦中国AI圈" in strict_prompt:
        issues.append("codex mail generation prompt must not hard-code a historical topic")
    request = build_mail_generation_request(codex_task, fake_identities["主发送者"], fake_identities["备用发送者"], now_beijing())
    if "mail_template_table" not in request.get("reference_tables", {}) or not request.get("selected_template"):
        issues.append("codex mail generation request must include template table and selected template")
    if "mail_intent_rules" not in request.get("reference_tables", {}):
        issues.append("codex mail generation request must include intent rules")
    evidence_policy = request.get("evidence_policy", {})
    if not evidence_policy.get("live_research_allowed"):
        issues.append("realtime codex mail generation must allow the generator to gather live evidence")
    if "allowlisted_local_tool_outputs" not in evidence_policy.get("not_missing_when_live_research_allowed", []):
        issues.append("live research generation must not require precomputed local evidence before generating")
    multi_task = {"任务名": "多收件人测试", "目标": "备用发送者,other@example.com", "说明": "北京时间 2099-01-01 00:00 触发，content_mode=static"}
    multi_runtime = build_task_runtime(multi_task, fake_identities)
    multi_recipient_jobs = create_delivery_jobs(
        multi_task,
        multi_runtime,
        f"smoke-multi-recipient-{os.getpid()}-{int(time.time() * 1000)}",
        None,
        "测试正文",
        "smoke-outbox",
    )
    if len([job for job in multi_recipient_jobs if job.get("created")]) != 2:
        issues.append("multi-recipient delivery must create one delivery job per recipient")
    for job in multi_recipient_jobs:
        try:
            stage_path("delivery_job", str(job.get("delivery_job_id"))).unlink(missing_ok=True)
        except Exception:
            issues.append(f"multi-recipient smoke cleanup failed: {job.get('delivery_job_id')}")
    parsed_generation = parse_mail_generation_result(
        json.dumps(
            {
                "subject": "测试主题",
                "body_text": "测试正文",
                "used_evidence_ids": ["task.notes"],
                "assumptions": [],
                "missing_fields": [],
                "should_send": True,
            },
            ensure_ascii=False,
        )
    )
    if not parsed_generation["should_send"] or parsed_generation["body_text"] != "测试正文":
        issues.append("structured mail generation parser must accept clean JSON output")
    blocked_generation = parse_mail_generation_result(
        json.dumps(
            {
                "subject": "测试主题",
                "body_text": "测试正文",
                "used_evidence_ids": [],
                "assumptions": ["缺少语气要求，假设正式"],
                "missing_fields": [],
                "should_send": True,
            },
            ensure_ascii=False,
        )
    )
    if blocked_generation["should_send"]:
        issues.append("structured mail generation parser must block outputs with assumptions")
    draft_runtime = build_task_runtime(codex_task, fake_identities)
    smoke_schedule_run_id = smoke_prefix
    smoke_content_job_id = f"{smoke_schedule_run_id}-content"
    resend_draft_payload = {
        "draft_item_id": f"{smoke_prefix}-resend-draft",
        "status": STATUS_DRAFT,
        "task_name": f"{smoke_prefix}-original-task",
        "sender_identity": "主发送者",
        "sender_account": "sender@example.com",
        "recipients": ["recipient@example.com"],
        "subject": "草稿重发测试",
        "body": "草稿重发正文",
        "scheduled_at": now_beijing().isoformat(),
    }
    immediate_task = build_static_task_from_draft(resend_draft_payload, now_beijing(), "立即")
    scheduled_task = build_static_task_from_draft(resend_draft_payload, now_beijing() + timedelta(hours=1), "单次", task_name=resend_draft_payload["task_name"])
    immediate_task_issues = validate_resend_task_row(immediate_task, fake_identities, resend_draft_payload["body"], "立即")
    scheduled_task_issues = validate_resend_task_row(scheduled_task, fake_identities, resend_draft_payload["body"], "单次")
    if immediate_task_issues:
        issues.append(f"immediate draft resend task row invalid: {immediate_task_issues}")
    if scheduled_task_issues:
        issues.append(f"scheduled draft resend task row invalid: {scheduled_task_issues}")
    if scheduled_task.get("任务名") != resend_draft_payload["task_name"]:
        issues.append("scheduled draft resend must preserve the source task name by default")
    draft = create_draft_item(
        codex_task,
        draft_runtime,
        smoke_schedule_run_id,
        smoke_content_job_id,
        blocked_generation,
        "smoke test only",
    )
    draft_path = stage_path("draft_item", draft_item_id(smoke_schedule_run_id))
    if not draft.get("ok") or not draft_path.exists():
        issues.append("draft item creation must persist review-only mail drafts")
    else:
        try:
            draft_path.unlink()
        except Exception:
            issues.append("draft item smoke cleanup failed")
    dead_payload = {
        "content_job_id": f"{smoke_schedule_run_id}-dead-content",
        "schedule_run_id": smoke_schedule_run_id,
        "status": STATUS_DEAD_LETTER,
        "task_name": codex_task["任务名"],
        "task": codex_task,
        "last_error": "smoke dead letter",
    }
    dead_draft = mirror_dead_letter_to_draft(
        task=codex_task,
        runtime=draft_runtime,
        schedule_run_id=smoke_schedule_run_id,
        source_stage="content_job",
        source_id=str(dead_payload["content_job_id"]),
        payload=dead_payload,
    )
    dead_draft_path = stage_path("draft_item", build_artifact_key(smoke_schedule_run_id, "draft", "content_job"))
    if not dead_draft.get("ok") or not dead_draft_path.exists():
        issues.append("dead-letter jobs must be mirrored into draft items")
    else:
        try:
            dead_draft_path.unlink()
        except Exception:
            issues.append("dead-letter draft smoke cleanup failed")
    outbox_dead_run_id = f"{smoke_schedule_run_id}-outbox-dead"
    outbox_dead_content_id = build_artifact_key(outbox_dead_run_id, "content")
    outbox_dead_item_id = outbox_item_id(outbox_dead_run_id)
    outbox_dead_payload = {
        "content_job_id": outbox_dead_content_id,
        "schedule_run_id": outbox_dead_run_id,
        "status": STATUS_DEAD_LETTER,
        "task_name": codex_task["任务名"],
        "task": codex_task,
        "last_error": "smoke outbox related dead letter",
    }
    write_stage("content_job", outbox_dead_payload)
    outbox_payload = {
        "outbox_item_id": outbox_dead_item_id,
        "schedule_run_id": outbox_dead_run_id,
        "content_job_id": outbox_dead_content_id,
        "status": OUTBOX_READY,
        "task_name": codex_task["任务名"],
        "task": codex_task,
        "sender_identity": "主发送者",
        "sender_account": "sender@example.com",
        "recipients": ["recipient@example.com"],
        "subject": "测试死信迁移",
        "body": "测试正文",
        "scheduled_at": now_beijing().isoformat(),
        "freshness": "static",
        "expires_at": "",
        "priority": 20,
        "ready_at": now_beijing().isoformat(),
        "sequence": int(now_beijing().timestamp() * 1000),
        "attempt_count": 0,
    }
    write_stage("outbox_item", outbox_payload)
    refresh_outbox_index(include_smoke=True)
    moved_outbox = read_stage("outbox_item", outbox_dead_item_id)
    moved_draft_id = build_artifact_key(outbox_dead_run_id, "draft", "content_job")
    moved_draft_path = stage_path("draft_item", moved_draft_id)
    if moved_outbox.get("status") != OUTBOX_STALE or not moved_draft_path.exists():
        issues.append("outbox maintenance must move related dead-letter mail to draft")
    for cleanup_path in (
        stage_path("content_job", outbox_dead_content_id),
        stage_path("outbox_item", outbox_dead_item_id),
        moved_draft_path,
    ):
        try:
            cleanup_path.unlink(missing_ok=True)
        except Exception:
            issues.append(f"outbox dead-letter smoke cleanup failed: {cleanup_path.name}")

    superseded_task = {
        "任务名": f"{smoke_prefix}-superseded",
        "触发方式": "固定时间任务",
        "周期": "单次",
        "目标": "备用发送者,other@example.com",
        "执行动作": "到点由 Codex 实时生成正文并发送",
        "状态": "启用",
        "责任身份": "主发送者",
        "说明": "北京时间 2026-01-01 00:00 触发，content_mode=codex，subject=测试覆盖归档",
    }
    superseded_runtime = build_task_runtime(superseded_task, fake_identities)
    superseded_run_id = str(superseded_runtime["record_key"])
    write_stage(
        "schedule_run",
        {
            "schedule_run_id": superseded_run_id,
            "status": STATUS_DEAD_LETTER,
            "content_mode": "codex",
            "task_name": superseded_task["任务名"],
            "task": superseded_task,
            "created_at": now_beijing().isoformat(),
            "scheduled_at": superseded_runtime["scheduled_at"].isoformat() if superseded_runtime["scheduled_at"] else "",
            "recipients": superseded_runtime["recipients"],
        },
    )
    write_stage(
        "content_job",
        {
            "content_job_id": build_artifact_key(superseded_run_id, "content"),
            "schedule_run_id": superseded_run_id,
            "status": STATUS_DEAD_LETTER,
            "task_name": superseded_task["任务名"],
            "task": superseded_task,
            "last_error": "smoke superseded dead letter",
        },
    )
    for recipient in superseded_runtime["recipients"]:
        write_smtp_receipt(
            task={"任务名": f"{smoke_prefix}-successful-resend"},
            scheduled_at=now_beijing(),
            sender=fake_identities["主发送者"],
            recipient=recipient,
            subject="测试覆盖归档",
            body="测试正文",
            send_result={"message_id": "smoke-superseded"},
        )
    superseded_result = create_three_stage_email_job(superseded_task, fake_identities)
    superseded_run = read_stage("schedule_run", superseded_run_id)
    superseded_content = read_stage("content_job", build_artifact_key(superseded_run_id, "content"))
    if superseded_result.get("status") != STATUS_ARCHIVED or superseded_run.get("status") != STATUS_ARCHIVED or superseded_content.get("status") != STATUS_ARCHIVED:
        issues.append("superseded successful resend must auto-archive old draft/dead-letter run")
    cleanup_smoke_artifacts(prefixes=(smoke_prefix,))

    result = {
        "ok": not issues,
        "issues": issues,
        "checks": {
            "smtp_header_sanitized": not any(ch in sanitized for ch in "\r\n\t"),
            "command_report_route": detect_content_mode(report_task),
            "explicit_codex_route": detect_content_mode(codex_task),
            "explicit_codex_source": codex_source,
            "legacy_fallback_source": fallback_source,
            "strict_generation_context": "strict_mail_generation_context" in strict_prompt,
            "structured_generation_parser": parsed_generation["should_send"],
            "assumption_blocks_send": not blocked_generation["should_send"],
            "dead_letter_mirrors_to_draft": dead_draft.get("ok", False),
            "outbox_dead_letter_moves_to_draft": moved_outbox.get("status") == OUTBOX_STALE,
            "superseded_successful_resend_archives_old_run": superseded_run.get("status") == STATUS_ARCHIVED,
            "immediate_draft_resend_task_valid": not immediate_task_issues,
            "scheduled_draft_resend_task_valid": not scheduled_task_issues,
            "scheduled_draft_resend_preserves_task_name": scheduled_task.get("任务名") == resend_draft_payload["task_name"],
            "inbound_metadata_attachment_auto_materialize_candidate": needs_materialization,
            "inbound_risky_attachment_requires_review": not risky_needs_materialization,
            "immediate_template_present": "immediate_static_send" in template_text,
            "scheduled_template_present": "scheduled_static_send" in template_text,
            "live_research_allowed": bool(evidence_policy.get("live_research_allowed")),
            "codex_exec_preserves_user_config": "--ignore-user-config" not in none_codex_cmd and "--ignore-user-config" not in maintenance_codex_cmd,
            "codex_exec_ignores_rules_for_sealed_profiles": "--ignore-rules" in none_codex_cmd and "--ignore-rules" in maintenance_codex_cmd,
            "codex_exec_read_only_for_sealed_profiles": "--sandbox" in none_codex_cmd and "read-only" in none_codex_cmd and "--sandbox" in maintenance_codex_cmd and "read-only" in maintenance_codex_cmd,
            "codex_exec_research_keeps_full_profile": "--ignore-rules" not in research_codex_cmd and "--sandbox" not in research_codex_cmd,
            "multi_recipient_delivery_jobs": len(multi_recipient_jobs),
            "report_provider": detect_report_provider(report_task),
            "subject": build_subject(report_task),
            "dry_run_prompt_preview": preview,
        },
    }
    if old_disable_mirror is None:
        os.environ.pop("EMAIL_SCHEDULER_DISABLE_HUMAN_MIRROR", None)
    else:
        os.environ["EMAIL_SCHEDULER_DISABLE_HUMAN_MIRROR"] = old_disable_mirror
    return result


def doctor() -> dict[str, Any]:
    val = validate()
    snapshot_data = snapshot()
    metric_data = metrics()
    issues = list(val["issues"])
    log_bytes = metric_data["runtime"]["log_bytes"]
    heartbeat = metric_data["runtime"]["heartbeat"]
    job_counts = metric_data["runtime"].get("job_counts", {})
    content_job_counts = metric_data["runtime"].get("content_job_counts", {})
    draft_item_counts = metric_data["runtime"].get("draft_item_counts", {})
    outbox_item_counts = metric_data["runtime"].get("outbox_item_counts", {})
    outbox_queue_counts = metric_data["runtime"].get("outbox_queue_counts", {})
    delivery_job_counts = metric_data["runtime"].get("delivery_job_counts", {})
    inbox_data = metric_data["runtime"].get("inbox", {})
    if log_bytes > MAX_LOG_BYTES:
        issues.append(f"scheduler log exceeds rotation threshold: {log_bytes}")
    if not heartbeat and not metric_data["runtime"].get("unified_scheduler_task_enabled"):
        issues.append("scheduler heartbeat missing; resident loop may not have started yet")
    if job_counts.get("failed", 0):
        issues.append(f"email jobs failed: {job_counts.get('failed')}")
    if content_job_counts.get("failed", 0):
        issues.append(f"email content jobs failed: {content_job_counts.get('failed')}")
    if content_job_counts.get("dead_letter", 0):
        issues.append(f"email content jobs dead-lettered: {content_job_counts.get('dead_letter')}")
    if draft_item_counts.get("draft", 0):
        issues.append(f"email draft items require human review: {draft_item_counts.get('draft')}")
    if outbox_queue_counts.get("blocked", 0):
        issues.append(f"email outbox items blocked: {outbox_queue_counts.get('blocked')}")
    if outbox_queue_counts.get("expired", 0):
        issues.append(f"email outbox items expired: {outbox_queue_counts.get('expired')}")
    if delivery_job_counts.get("failed", 0):
        issues.append(f"email delivery jobs failed: {delivery_job_counts.get('failed')}")
    if delivery_job_counts.get("dead_letter", 0):
        issues.append(f"email delivery jobs dead-lettered: {delivery_job_counts.get('dead_letter')}")
    if inbox_data.get("risk_counts", {}).get("needs_review", 0):
        issues.append(f"email inbox messages need review: {inbox_data.get('risk_counts', {}).get('needs_review')}")
    if val["ok"] and not issues:
        severity = "ok"
    else:
        severity = "risk"
    return {
        "ok": val["ok"] and not issues,
        "severity": severity,
        "summary": {
            "task_count": snapshot_data["tasks"] and len(snapshot_data["tasks"]) or 0,
            "identity_count": len(snapshot_data["identity_names"]),
            "issues": len(issues),
            "log_bytes": log_bytes,
            "heartbeat_updated_at": heartbeat.get("updated_at", ""),
            "job_counts": job_counts,
            "content_job_counts": content_job_counts,
            "draft_item_counts": draft_item_counts,
            "outbox_item_counts": outbox_item_counts,
            "outbox_queue_counts": outbox_queue_counts,
            "delivery_job_counts": delivery_job_counts,
            "inbox": inbox_data,
            "content_retry_waiting": metric_data["runtime"].get("content_retry_waiting", 0),
            "delivery_retry_waiting": metric_data["runtime"].get("delivery_retry_waiting", 0),
            "runnable_stage_jobs": metric_data["runtime"].get("runnable_stage_jobs", False),
        },
        "issues": issues,
        "advisory": (
            "Use dry-run first, then run the resident scheduler with the task table and identity table in sync."
            if not val["ok"]
            else "Scheduler tables are parseable and resident metrics are visible."
        ),
    }


def repair_plan() -> dict[str, Any]:
    val = validate()
    metric_data = metrics()
    actions = []
    if not val["ok"]:
        actions.append("repair task/identity tables until validate passes")
    if not metric_data["runtime"]["heartbeat"]:
        actions.append("start resident scheduler and wait for heartbeat")
    if metric_data["runtime"]["log_bytes"] > MAX_LOG_BYTES:
        actions.append("rotate scheduler log")
    if metric_data["runtime"].get("content_job_counts", {}).get("dead_letter", 0):
        actions.append("inspect content dead-letter jobs and explicitly reset or archive them")
    if metric_data["runtime"].get("draft_item_counts", {}).get("draft", 0):
        actions.append("inspect draft items, fill missing fields or approve assumptions, then reset-run --stage content --confirm-resend YES")
    if metric_data["runtime"].get("outbox_queue_counts", {}).get("blocked", 0):
        actions.append("inspect outbox_index blocked section and repair missing sender SMTP, recipients, or required fields")
    if metric_data["runtime"].get("outbox_queue_counts", {}).get("expired", 0):
        actions.append("inspect expired outbox items and regenerate content or archive stale tasks")
    if metric_data["runtime"].get("delivery_job_counts", {}).get("dead_letter", 0):
        actions.append("inspect delivery dead-letter jobs and explicitly reset or archive them")
    if not metric_data["runtime"].get("inbox", {}).get("ok", False):
        actions.append("inspect inbox state and run inbox-validate")
    if metric_data["runtime"].get("inbox", {}).get("risk_counts", {}).get("needs_review", 0):
        actions.append("review risky inbox attachment metadata before routing inbound mail")
    reconciliation = email_state_index("repair-plan")
    if reconciliation.get("action_count"):
        actions.append("review email state reconciliation actions; do not repair derived SQLite directly")
    actions.append("run dry-run for the due email task")
    actions.append("run the resident scheduler loop")
    return {
        "ok": True,
        "dry_run": True,
        "actions": actions,
        "blocked": not val["ok"],
        "issues": val["issues"],
        "state_reconciliation": reconciliation,
    }


def run_due(timeout_seconds: int = DEFAULT_CODEx_TIMEOUT_SECONDS, once: bool = False) -> dict[str, Any]:
    result = dispatch_due(timeout_seconds=timeout_seconds)
    result["legacy_command_redirected"] = "run-due uses dispatch-due; direct SMTP send is disabled by default"
    return result


def run_task(task_name: str, timeout_seconds: int = DEFAULT_CODEx_TIMEOUT_SECONDS, dry_run: bool = False, confirm_direct_send: str = "") -> dict[str, Any]:
    tasks, identities = load_world()
    task = next((row for row in tasks if row.get("任务名", "").strip() == task_name.strip()), None)
    if task is None:
        return {"ok": False, "reason": f"task not found: {task_name}"}
    if dry_run:
        return dry_run_task(task, identities)
    if confirm_direct_send != "YES":
        return {
            "ok": False,
            "blocked": True,
            "reason": "direct run-task send is disabled; use dispatch-due/reset-run, or pass --confirm-direct-send YES for manual emergency send",
            "dry_run": dry_run_task(task, identities),
        }
    return execute_task(task, identities, timeout_seconds=timeout_seconds)


def loop(interval_seconds: int, timeout_seconds: int) -> int:
    lock = SingleInstanceLock(LOCK_PATH)
    if not lock.acquire():
        append_scheduler_log("scheduler loop skipped because another instance holds the lock")
        return 0
    append_scheduler_log(f"scheduler loop started interval={interval_seconds} timeout={timeout_seconds}")
    try:
        while True:
            started = time.monotonic()
            try:
                result = run_due(timeout_seconds=timeout_seconds, once=True)
                write_heartbeat(
                    {
                        "interval_seconds": interval_seconds,
                        "timeout_seconds": timeout_seconds,
                        "last_run_due": result,
                    }
                )
                append_scheduler_log(f"run_due => {json.dumps(result, ensure_ascii=False)}")
            except Exception as exc:
                write_heartbeat(
                    {
                        "interval_seconds": interval_seconds,
                        "timeout_seconds": timeout_seconds,
                        "last_error": f"{type(exc).__name__}: {exc}",
                    }
                )
                append_scheduler_log(f"error: {type(exc).__name__}: {exc}")
            elapsed = time.monotonic() - started
            sleep_for = max(1, interval_seconds - int(elapsed))
            time.sleep(sleep_for)
    finally:
        lock.release()
    return 0


def print_json(data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    sys.stdout.buffer.write(payload.encode("utf-8"))


def command_catalog() -> dict[str, Any]:
    canonical = f"{sys.executable} _bridge/shared/email_scheduler.py"
    command_groups = {
        "maintenance": ["snapshot", "doctor", "repair-plan", "validate", "quick-check", "metrics", "smoke-test"],
        "inbox": [
            "inbox-snapshot",
            "inbox-refresh-index",
            "inbox-mirror",
            "peek-inbox",
            "inbox-validate",
            "inbox-fetch",
            "inbox-poll",
            "inbox-attachment-review",
            "inbox-save-attachments",
            "inbox-refresh-reply-context",
        ],
        "outbox_delivery": ["refresh-outbox-index", "peek-outbox", "dispatch-due", "worker", "inspect-run"],
        "task_intent": ["intent-dry-run", "intent-create", "intent-submit", "dry-run", "run-task", "run-due"],
        "repair_or_manual": ["archive-completed-tasks", "archive-run", "archive-task", "reset-run", "resend-draft"],
        "query": ["commands", "state-index", "state-query"],
        "daemon": ["loop"],
    }
    deprecated = {
        "mail_task_table.py": {
            "replacement": f"{canonical} commands",
            "reason": "mail task table is an input source, not the runtime queue or CLI owner",
            "use_for_status": f"{canonical} state-index --apply && {canonical} state-query --table summary",
            "use_for_creation": f"{canonical} intent-dry-run|intent-create|intent-submit",
        },
        "mail_task_table": {
            "replacement": f"{canonical} commands",
            "reason": "refer to the table file only as data; route operations through email_scheduler.py",
        },
    }
    return {
        "schema": "email_scheduler.command_catalog.v1",
        "ok": True,
        "canonical_entrypoint": canonical,
        "maintenance_facade": "python _bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py email-scheduler <snapshot|doctor|repair-plan|metrics|validate>",
        "owner": "_bridge/shared/email_scheduler.py",
        "command_groups": command_groups,
        "deprecated_aliases": deprecated,
        "state_query": {
            "index": f"{canonical} state-index --apply",
            "query": f"{canonical} state-query --table <summary|tasks|stages|inbox|receipts|identities|reconciliation>",
            "repair_plan": f"{canonical} state-index repair-plan",
            "db_path": str(EMAIL_STATE_DIR / "email_state.sqlite"),
            "source_of_truth": "mail task files and email scheduler runtime JSON remain authoritative",
        },
    }


def email_state_index(action: str, *, apply: bool = False) -> dict[str, Any]:
    import email_state_index as index

    if action == "snapshot":
        return index.snapshot()
    if action == "metrics":
        return index.metrics()
    if action == "validate":
        return index.validate()
    if action == "repair-plan":
        return index.reconciliation_plan()
    return index.refresh(apply=apply)


def email_state_query(table: str, status: str = "", limit: int = 50) -> dict[str, Any]:
    import email_state_index as index

    return index.query(table=table, status=status, limit=limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resident email scheduler for Codex resource library")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("snapshot")
    sub.add_parser("doctor")
    sub.add_parser("repair-plan")
    sub.add_parser("validate")
    sub.add_parser("quick-check")
    sub.add_parser("metrics")
    sub.add_parser("smoke-test")
    sub.add_parser("commands")
    state_index_parser = sub.add_parser("state-index")
    state_index_parser.add_argument("action", choices=["snapshot", "refresh", "metrics", "validate", "repair-plan"], default="refresh", nargs="?")
    state_index_parser.add_argument("--apply", action="store_true")
    state_query_parser = sub.add_parser("state-query")
    state_query_parser.add_argument("--table", choices=["summary", "tasks", "stages", "inbox", "receipts", "identities", "reconciliation"], default="summary")
    state_query_parser.add_argument("--status", default="")
    state_query_parser.add_argument("--limit", type=int, default=50)
    sub.add_parser("inbox-snapshot")
    sub.add_parser("inbox-refresh-index")
    sub.add_parser("inbox-mirror")
    sub.add_parser("peek-inbox")
    inbox_validate_parser = sub.add_parser("inbox-validate")
    inbox_validate_parser.add_argument("--account", default="3633922805@qq.com")
    inbox_validate_parser.add_argument("--identity", default="")

    inbox_fetch_parser = sub.add_parser("inbox-fetch")
    inbox_fetch_parser.add_argument("--account", default="3633922805@qq.com")
    inbox_fetch_parser.add_argument("--identity", default="")
    inbox_fetch_parser.add_argument("--folder", default="INBOX")
    inbox_fetch_parser.add_argument("--limit", type=int, default=10)
    inbox_fetch_parser.add_argument("--apply", action="store_true")

    inbox_poll_parser = sub.add_parser("inbox-poll")
    inbox_poll_parser.add_argument("--account", default=INBOX_DEFAULT_ACCOUNT)
    inbox_poll_parser.add_argument("--limit", type=int, default=INBOX_POLL_LIMIT)

    inbox_attachment_review_parser = sub.add_parser("inbox-attachment-review")
    inbox_attachment_review_parser.add_argument("--inbox-job-id", required=True)

    inbox_save_attachments_parser = sub.add_parser("inbox-save-attachments")
    inbox_save_attachments_parser.add_argument("--inbox-job-id", required=True)
    inbox_save_attachments_parser.add_argument("--identity", default="")
    inbox_save_attachments_parser.add_argument("--apply", action="store_true")
    inbox_save_attachments_parser.add_argument("--confirm-risky-attachments", default="")

    inbox_refresh_reply_parser = sub.add_parser("inbox-refresh-reply-context")
    inbox_refresh_reply_parser.add_argument("--inbox-job-id", required=True)
    inbox_refresh_reply_parser.add_argument("--apply", action="store_true")

    dry_run_parser = sub.add_parser("dry-run")
    dry_run_parser.add_argument("--task-name", required=True)

    run_task_parser = sub.add_parser("run-task")
    run_task_parser.add_argument("--task-name", required=True)
    run_task_parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_CODEx_TIMEOUT_SECONDS)
    run_task_parser.add_argument("--confirm-direct-send", default="")

    run_due_parser = sub.add_parser("run-due")
    run_due_parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_CODEx_TIMEOUT_SECONDS)

    dispatch_parser = sub.add_parser("dispatch-due")
    dispatch_parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_CODEx_TIMEOUT_SECONDS)

    worker_parser = sub.add_parser("worker")
    worker_parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_CODEx_TIMEOUT_SECONDS)
    worker_parser.add_argument("--max-jobs", type=int, default=10)

    inspect_parser = sub.add_parser("inspect-run")
    inspect_parser.add_argument("--schedule-run-id", required=True)

    sub.add_parser("refresh-outbox-index")
    sub.add_parser("peek-outbox")
    sub.add_parser("archive-completed-tasks")

    archive_parser = sub.add_parser("archive-run")
    archive_parser.add_argument("--schedule-run-id", required=True)
    archive_parser.add_argument("--reason", default="")

    archive_task_parser = sub.add_parser("archive-task")
    archive_task_parser.add_argument("--task-name", required=True)
    archive_task_parser.add_argument("--reason", default="")

    reset_parser = sub.add_parser("reset-run")
    reset_parser.add_argument("--schedule-run-id", required=True)
    reset_parser.add_argument("--stage", choices=["content", "delivery", "all"], required=True)
    reset_parser.add_argument("--confirm-resend", default="")

    resend_draft_parser = sub.add_parser("resend-draft")
    resend_draft_parser.add_argument("--draft-item-id", required=True)
    resend_draft_parser.add_argument("--time", default="立即")
    resend_draft_parser.add_argument("--task-name", default="")
    resend_draft_parser.add_argument("--confirm-resend", default="")
    resend_draft_parser.add_argument("--dry-run", action="store_true")

    intent_dry_parser = sub.add_parser("intent-dry-run")
    intent_dry_parser.add_argument("--to", required=True)
    intent_dry_parser.add_argument("--content", required=True)
    intent_dry_parser.add_argument("--time", required=True)
    intent_dry_parser.add_argument("--sender", default="主发送者")
    intent_dry_parser.add_argument("--subject", default="")
    intent_dry_parser.add_argument("--task-name", default="")

    intent_create_parser = sub.add_parser("intent-create")
    intent_create_parser.add_argument("--to", required=True)
    intent_create_parser.add_argument("--content", required=True)
    intent_create_parser.add_argument("--time", required=True)
    intent_create_parser.add_argument("--sender", default="主发送者")
    intent_create_parser.add_argument("--subject", default="")
    intent_create_parser.add_argument("--task-name", default="")

    intent_submit_parser = sub.add_parser("intent-submit")
    intent_submit_parser.add_argument("--to", required=True)
    intent_submit_parser.add_argument("--content", required=True)
    intent_submit_parser.add_argument("--time", required=True)
    intent_submit_parser.add_argument("--sender", default="主发送者")
    intent_submit_parser.add_argument("--subject", default="")
    intent_submit_parser.add_argument("--task-name", default="")
    intent_submit_parser.add_argument("--dispatch-if-due", action="store_true")
    intent_submit_parser.add_argument("--confirm-dispatch", default="")

    loop_parser = sub.add_parser("loop")
    loop_parser.add_argument("--interval-seconds", type=int, default=DEFAULT_LOOP_SECONDS)
    loop_parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_CODEx_TIMEOUT_SECONDS)

    args = parser.parse_args(argv)

    if args.command == "snapshot":
        print_json(snapshot())
    elif args.command == "doctor":
        print_json(doctor())
    elif args.command == "repair-plan":
        print_json(repair_plan())
    elif args.command == "validate":
        print_json(validate())
    elif args.command == "quick-check":
        print_json(quick_check())
    elif args.command == "smoke-test":
        print_json(smoke_test())
    elif args.command == "metrics":
        print_json(metrics())
    elif args.command == "inbox-snapshot":
        print_json(inbox_snapshot())
    elif args.command == "commands":
        print_json(command_catalog())
    elif args.command == "state-index":
        print_json(email_state_index(args.action, apply=args.apply))
    elif args.command == "state-query":
        print_json(email_state_query(args.table, status=args.status, limit=args.limit))
    elif args.command == "inbox-refresh-index":
        print_json(refresh_inbox_index())
    elif args.command == "inbox-mirror":
        print_json(sync_inbox_human_mirror())
    elif args.command == "peek-inbox":
        print_json(peek_inbox())
    elif args.command == "inbox-validate":
        print_json(inbox_validate(args.account, args.identity))
    elif args.command == "inbox-fetch":
        print_json(inbox_fetch(args.account, args.identity, args.folder, args.limit, apply=args.apply))
    elif args.command == "inbox-poll":
        print_json(poll_inbox(args.account, args.limit))
    elif args.command == "inbox-attachment-review":
        print_json(inbox_attachment_review(args.inbox_job_id))
    elif args.command == "inbox-save-attachments":
        print_json(
            save_inbox_attachments(
                args.inbox_job_id,
                apply=args.apply,
                identity_name=args.identity,
                confirm_risky_attachments=args.confirm_risky_attachments,
            )
        )
    elif args.command == "inbox-refresh-reply-context":
        print_json(refresh_reply_task_context(args.inbox_job_id, apply=args.apply))
    elif args.command == "dry-run":
        tasks, identities = load_world()
        task = next((row for row in tasks if row.get("任务名", "").strip() == args.task_name.strip()), None)
        if task is None:
            print_json({"ok": False, "reason": f"task not found: {args.task_name}"})
            return 1
        print_json(dry_run_task(task, identities))
    elif args.command == "run-task":
        print_json(run_task(args.task_name, timeout_seconds=args.timeout_seconds, dry_run=False, confirm_direct_send=args.confirm_direct_send))
    elif args.command == "run-due":
        print_json(run_due(timeout_seconds=args.timeout_seconds, once=True))
    elif args.command == "dispatch-due":
        print_json(dispatch_due(timeout_seconds=args.timeout_seconds))
    elif args.command == "worker":
        print_json(worker(timeout_seconds=args.timeout_seconds, max_jobs=args.max_jobs))
    elif args.command == "inspect-run":
        print_json(inspect_run(args.schedule_run_id))
    elif args.command == "refresh-outbox-index":
        print_json(refresh_outbox_index())
    elif args.command == "peek-outbox":
        print_json(peek_outbox())
    elif args.command == "archive-completed-tasks":
        print_json(archive_completed_one_time_tasks())
    elif args.command == "archive-run":
        print_json(archive_run(args.schedule_run_id, reason=args.reason))
    elif args.command == "archive-task":
        print_json(archive_task_by_name(args.task_name, reason=args.reason))
    elif args.command == "reset-run":
        print_json(reset_run(args.schedule_run_id, stage=args.stage, confirm_resend=args.confirm_resend))
    elif args.command == "resend-draft":
        print_json(resend_draft(args.draft_item_id, time_text=args.time, confirm_resend=args.confirm_resend, task_name=args.task_name, dry_run=args.dry_run))
    elif args.command == "intent-dry-run":
        print_json(intent_dry_run(args.to, args.content, args.time, args.sender, args.subject, args.task_name))
    elif args.command == "intent-create":
        print_json(intent_create(args.to, args.content, args.time, args.sender, args.subject, args.task_name))
    elif args.command == "intent-submit":
        print_json(
            intent_submit(
                args.to,
                args.content,
                args.time,
                args.sender,
                args.subject,
                args.task_name,
                dispatch_if_due=args.dispatch_if_due,
                confirm_dispatch=args.confirm_dispatch,
            )
        )
    elif args.command == "loop":
        return loop(args.interval_seconds, args.timeout_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
