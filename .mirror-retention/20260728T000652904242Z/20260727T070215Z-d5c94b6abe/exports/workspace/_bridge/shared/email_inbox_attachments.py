#!/usr/bin/env python3
"""Email-owned inbound attachment review and materialization helpers.

Ownership: the resident email scheduler owns inbound email attachment metadata,
storage, and risk gates.
Non-goals: this module does not execute attachment contents, send mail, mark IMAP
messages read, or route attachments to the generic resource layer.
State behavior: read-only by default; writes only when the caller passes apply.
Caller context: `_bridge/shared/email_scheduler.py` CLI and worker maintenance.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


RISKY_EXTENSIONS = {
    ".exe",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".js",
    ".jar",
    ".msi",
    ".scr",
    ".html",
    ".htm",
    ".zip",
    ".rar",
    ".7z",
    ".docm",
    ".xlsm",
}


def safe_filename(value: str, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:180] or fallback


def attachment_risk(filename: str) -> str:
    return "needs_review" if Path(str(filename or "").lower()).suffix in RISKY_EXTENSIONS else "metadata_only"


def attachment_dir(root: Path, inbound_message_id: str) -> Path:
    return root / safe_filename(inbound_message_id, "unknown-message")


def attachment_review(job: dict[str, Any], message: dict[str, Any] | None, attachment_root: Path) -> dict[str, Any]:
    attachments = job.get("attachments") if isinstance(job.get("attachments"), list) else []
    materialized = [
        item
        for item in attachments
        if isinstance(item, dict) and str(item.get("saved_path") or "").strip()
    ]
    inbound_id = str(job.get("inbound_message_id") or (message or {}).get("inbound_message_id") or "")
    return {
        "schema": "email_inbox_attachment_review.v1",
        "ok": True,
        "inbox_job_id": job.get("inbox_job_id", ""),
        "inbound_message_id": inbound_id,
        "owner": "email_scheduler",
        "attachment_policy": "email_owned_metadata_until_saved",
        "downstream_policy": "mail_attachment_first",
        "status": job.get("status", ""),
        "resource_class": job.get("resource_class", ""),
        "risk_level": job.get("risk_level", ""),
        "attachment_count": len(attachments),
        "materialized_count": len(materialized),
        "attachments": attachments,
        "storage_dir": str(attachment_dir(attachment_root, inbound_id)) if inbound_id else "",
        "content_available": bool(materialized),
        "next_action": "save_attachments_with_apply" if attachments and not materialized else "review_saved_attachment_paths",
        "notes": [
            "Inbound attachments are email state, not generic resource requests.",
            "Saving uses IMAP BODY.PEEK through the email scheduler and does not mark messages read.",
            "Downstream parsing can consume saved local paths only after this email-owned step completes.",
        ],
    }


def saved_attachments_ready(attachments: list[Any]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if not attachments:
        return False, ["no_attachments"]
    for index, item in enumerate(attachments, start=1):
        if not isinstance(item, dict):
            issues.append(f"attachment_{index}_metadata_invalid")
            continue
        if item.get("risk_level") == "needs_review":
            issues.append(f"attachment_{index}_needs_review")
        saved_path = str(item.get("saved_path") or "").strip()
        if not saved_path:
            issues.append(f"attachment_{index}_saved_path_missing")
            continue
        if not Path(saved_path).is_file():
            issues.append(f"attachment_{index}_file_missing:{saved_path}")
        if item.get("materialized") is not True:
            issues.append(f"attachment_{index}_not_materialized")
    return not issues, issues


def codex_context_lines(attachments: list[Any]) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(attachments, start=1):
        if not isinstance(item, dict):
            continue
        lines.append(
            " | ".join(
                [
                    f"{index}. {item.get('filename', '')}",
                    f"path={item.get('saved_path', '')}",
                    f"sha256={item.get('sha256', '')}",
                    f"content_type={item.get('content_type', '')}",
                    f"size_bytes={item.get('size_bytes', '')}",
                    f"risk={item.get('risk_level', '')}",
                ]
            )
        )
    return lines


def docx_text_preview(path: Path, limit: int) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("word/document.xml")
        root = ElementTree.fromstring(raw)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs: list[str] = []
        for paragraph in root.findall(".//w:p", namespace):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
            if text:
                paragraphs.append(text)
            if sum(len(item) for item in paragraphs) >= limit:
                break
        return "\n".join(paragraphs)[:limit], "ok"
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


def pdf_text_preview(path: Path, limit: int) -> tuple[str, str]:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        return "", f"pdf_reader_unavailable:{type(exc).__name__}"
    try:
        reader = PdfReader(str(path))
        chunks: list[str] = []
        remaining = limit
        for page in reader.pages:
            if remaining <= 0:
                break
            text = page.extract_text() or ""
            text = text.strip()
            if not text:
                continue
            clipped = text[:remaining]
            chunks.append(clipped)
            remaining -= len(clipped)
        return "\n\n".join(chunks)[:limit], "ok"
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


def attachment_text_context(attachments: list[Any], *, per_file_limit: int = 6000, total_limit: int = 10000) -> list[str]:
    lines: list[str] = []
    remaining = total_limit
    for index, item in enumerate(attachments, start=1):
        if remaining <= 0 or not isinstance(item, dict):
            break
        saved_path = Path(str(item.get("saved_path") or ""))
        if not saved_path.is_file():
            lines.append(f"{index}. {item.get('filename', '')}: attachment_text_unavailable=file_missing")
            continue
        suffix = saved_path.suffix.lower()
        if suffix == ".docx":
            text, status = docx_text_preview(saved_path, min(per_file_limit, remaining))
        elif suffix == ".pdf":
            text, status = pdf_text_preview(saved_path, min(per_file_limit, remaining))
        else:
            lines.append(f"{index}. {item.get('filename', '')}: attachment_text_unavailable=unsupported_type:{suffix}")
            continue
        if status != "ok":
            lines.append(f"{index}. {item.get('filename', '')}: attachment_text_unavailable={status}")
            continue
        if text:
            clipped = text[:remaining]
            lines.append(f"{index}. {item.get('filename', '')} 文本摘录:\n{clipped}")
            remaining -= len(clipped)
        else:
            lines.append(f"{index}. {item.get('filename', '')}: attachment_text_empty")
    return lines


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{time.time_ns()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def extract_attachments_from_raw(
    raw: bytes,
    *,
    inbound_message_id: str,
    attachment_root: Path,
    max_single_bytes: int,
    max_total_bytes: int,
    apply: bool,
) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    target_dir = attachment_dir(attachment_root, inbound_message_id)
    saved: list[dict[str, Any]] = []
    issues: list[str] = []
    total = 0
    index = 0
    for part in message.walk() if message.is_multipart() else []:
        if part.get_content_disposition() != "attachment":
            continue
        index += 1
        filename = safe_filename(part.get_filename() or "", f"attachment-{index}")
        payload = part.get_payload(decode=True) or b""
        size = len(payload)
        total += size
        if size > max_single_bytes:
            issues.append(f"attachment_too_large:{filename}")
        if total > max_total_bytes:
            issues.append("attachments_total_too_large")
        risk = attachment_risk(filename)
        digest = hashlib.sha256(payload).hexdigest()
        target = target_dir / f"{index:02d}-{filename}"
        item = {
            "filename": filename,
            "content_type": part.get_content_type(),
            "size_bytes": size,
            "sha256": digest,
            "risk_level": risk,
            "saved_path": str(target),
            "materialized": bool(apply and not issues),
        }
        saved.append(item)
        if apply and not issues:
            write_bytes_atomic(target, payload)
    if apply and not issues:
        write_json_atomic(
            target_dir / "_manifest.json",
            {
                "schema": "email_inbox_attachment_manifest.v1",
                "inbound_message_id": inbound_message_id,
                "attachment_count": len(saved),
                "total_bytes": total,
                "attachments": saved,
            },
        )
    return {
        "schema": "email_inbox_attachment_materialization.v1",
        "ok": not issues,
        "dry_run": not apply,
        "inbound_message_id": inbound_message_id,
        "owner": "email_scheduler",
        "storage_dir": str(target_dir),
        "attachment_count": len(saved),
        "total_bytes": total,
        "issues": issues,
        "attachments": saved,
    }
