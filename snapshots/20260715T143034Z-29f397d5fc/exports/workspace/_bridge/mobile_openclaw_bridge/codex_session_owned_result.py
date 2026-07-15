"""Exact owned-result recovery from the local Codex session store.

Owns: bounded, read-only discovery and parsing of exact mobile-result protocol
blocks persisted in Codex rollout JSONL files.
Non-goals: queue mutation, thread-route selection, Weixin sending, retries, or
task completion.
State behavior: reads session JSONL files only and returns poll-shaped evidence.
Normal callers: active mobile-result recovery and explicit one-task audits.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SAFE_PROTOCOL_ID = re.compile(r"^[A-Za-z0-9_-]+$")
ROLLOUT_THREAD_ID = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
DEFAULT_MAX_RESULT_CHARS = 60_000
DEFAULT_MAX_RECORD_CHARS = 1_000_000
DEFAULT_MAX_MATCHES = 16


def _valid_id(value: str) -> bool:
    return bool(value and SAFE_PROTOCOL_ID.fullmatch(value))


def is_usable_owned_result_text(value: str) -> bool:
    """Reject empty and known UI truncation placeholders without rejecting short replies."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return False
    compact = "".join(
        char for char in text if not char.isspace() and unicodedata.category(char) != "Cf"
    ).lower()
    if re.fullmatch(r"[.…。]+", compact):
        return False
    if re.fullmatch(r"[\[\(]?(?:truncated|loading[-_]?shimmer|ellipsis)[\]\)]?", compact):
        return False
    if re.fullmatch(r"[.…。]*(?:\[?truncated\]?)[.…。]*", compact):
        return False
    return True


def _parse_timestamp(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _default_session_roots() -> list[Path]:
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return [codex_home / "sessions"]


def _candidate_files_with_rg(begin_marker: str, roots: list[Path], max_files: int) -> tuple[str, list[Path]]:
    rg = shutil.which("rg")
    existing = [root for root in roots if root.exists()]
    if not rg or not existing:
        return "unavailable", []
    command = [rg, "-l", "--fixed-strings", "--glob", "*.jsonl", begin_marker, *map(str, existing)]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable", []
    if completed.returncode == 1:
        return "not_found", []
    if completed.returncode != 0:
        return "unavailable", []
    paths = [Path(line.strip()) for line in completed.stdout.splitlines() if line.strip()]
    paths.sort(key=_safe_mtime, reverse=True)
    return "matched", paths[:max_files]


def _candidate_files_bounded(roots: list[Path], max_files: int) -> list[Path]:
    candidates: list[Path] = []
    visited_dirs: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        stack = [root]
        while stack and len(candidates) < max_files:
            current = stack.pop()
            try:
                resolved_current = str(current.resolve())
            except OSError:
                continue
            if resolved_current in visited_dirs:
                continue
            visited_dirs.add(resolved_current)
            try:
                entries = sorted(current.iterdir(), key=lambda item: item.name, reverse=True)
            except OSError:
                continue
            directories = [item for item in entries if item.is_dir()]
            stack.extend(reversed(directories))
            candidates.extend(item for item in entries if item.is_file() and item.suffix == ".jsonl")
    candidates.sort(key=_safe_mtime, reverse=True)
    return candidates[:max_files]


def _record_message(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Return text, source kind, role, phase, and durable turn id when present."""
    record_type = str(record.get("type") or "")
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    if record_type == "response_item" and payload.get("type") == "message":
        content = payload.get("content") if isinstance(payload.get("content"), list) else []
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {"input_text", "output_text"}
        ]
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        metadata = metadata if isinstance(metadata, dict) else {}
        return (
            "\n".join(part for part in parts if part),
            f"response_item.{str(payload.get('role') or '')}",
            str(payload.get("role") or ""),
            str(payload.get("phase") or ""),
            str(metadata.get("turn_id") or ""),
        )
    if record_type == "event_msg" and payload.get("type") in {"agent_message", "user_message"}:
        role = "assistant" if payload.get("type") == "agent_message" else "user"
        return (
            str(payload.get("message") or ""),
            f"event_msg.{str(payload.get('type') or '')}",
            role,
            str(payload.get("phase") or ""),
            str(payload.get("turn_id") or ""),
        )
    return "", "", "", "", ""


def _delegation_matches(text: str, task_id: str, result_code: str, ack_code: str) -> bool:
    begin = f"[[mobile_result_begin:{task_id}:{result_code}]]"
    task_marker = f"[[mobile_task_id:{task_id}]]"
    end = f"[[mobile_result_end:{task_id}:{result_code}]]"
    if begin not in text or task_marker not in text or end not in text:
        return False
    return not ack_code or f"[[mobile_ack:{task_id}:{ack_code}]]" in text


def _extract_exact_result(text: str, task_id: str, result_code: str, ack_code: str, max_result_chars: int) -> str:
    begin = f"[[mobile_result_begin:{task_id}:{result_code}]]"
    task_marker = f"[[mobile_task_id:{task_id}]]"
    end = f"[[mobile_result_end:{task_id}:{result_code}]]"
    value = text.strip()
    if not value.startswith(begin) or not value.endswith(end):
        return ""
    body = value[len(begin) : -len(end)]
    if task_marker not in body:
        return ""
    body = body.replace(task_marker, "")
    if ack_code:
        body = body.replace(f"[[mobile_ack:{task_id}:{ack_code}]]", "")
    body = body.strip()
    if len(body) > max_result_chars:
        return ""
    return body if is_usable_owned_result_text(body) else ""


def _thread_id_from_rollout(path: Path) -> str:
    match = ROLLOUT_THREAD_ID.search(path.stem)
    return match.group(1) if match else ""


def _iter_records(path: Path, max_record_chars: int) -> Iterable[tuple[int, dict[str, Any]]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            line_number = 0
            while True:
                raw = handle.readline(max_record_chars + 1)
                if not raw:
                    break
                line_number += 1
                if len(raw) > max_record_chars and not raw.endswith("\n"):
                    while raw and not raw.endswith("\n"):
                        raw = handle.readline(max_record_chars + 1)
                    continue
                if "mobile_result_begin:" not in raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("type") != "compacted":
                    yield line_number, record
    except OSError:
        return


def find_owned_result(
    task_id: str,
    result_code: str,
    ack_code: str = "",
    created_at: str = "",
    expected_turn_id: str = "",
    max_files: int = 256,
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
    max_record_chars: int = DEFAULT_MAX_RECORD_CHARS,
    session_roots: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    """Find one non-empty exact owned result and return durable source evidence."""
    task = str(task_id or "").strip()
    result = str(result_code or "").strip()
    ack = str(ack_code or "").strip()
    if not _valid_id(task) or not _valid_id(result) or (ack and not _valid_id(ack)):
        return {"ok": False, "healthy": True, "reason": "invalid_protocol_identifier", "newText": None}

    roots = [Path(item) for item in (session_roots or _default_session_roots())]
    date_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(created_at or ""))
    if date_match:
        year, month, day = date_match.groups()
        dated = [root / year / month / day for root in roots]
        roots = dated + roots
    limit = max(1, min(int(max_files or 256), 2048))
    result_limit = max(1, min(int(max_result_chars or DEFAULT_MAX_RESULT_CHARS), DEFAULT_MAX_RESULT_CHARS))
    record_limit = max(1024, min(int(max_record_chars or DEFAULT_MAX_RECORD_CHARS), DEFAULT_MAX_RECORD_CHARS))
    begin_marker = f"[[mobile_result_begin:{task}:{result}]]"
    search_status, files = _candidate_files_with_rg(begin_marker, roots, limit)
    search_mode = "rg_fixed_string"
    if search_status == "unavailable":
        files = _candidate_files_bounded(roots, limit)
        search_mode = "bounded_recent_files"
    elif search_status == "not_found":
        return {
            "ok": True,
            "healthy": True,
            "reason": "owned_result_not_found",
            "newText": None,
            "result_complete": False,
            "search_mode": search_mode,
            "searched_files": 0,
            "created_at_hint": str(created_at or ""),
        }

    matches: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    created_at_dt = _parse_timestamp(created_at)
    expected_turn = str(expected_turn_id or "").strip()
    for path in files:
        resolved_path = str(path.resolve())
        if resolved_path in seen_files:
            continue
        seen_files.add(resolved_path)
        delegations: list[dict[str, Any]] = []
        for line_number, record in _iter_records(path, record_limit):
            text, source_kind, role, phase, record_turn_id = _record_message(record)
            if not text or begin_marker not in text:
                continue
            record_timestamp = str(record.get("timestamp") or "")
            record_timestamp_dt = _parse_timestamp(record_timestamp)
            if role == "user" and _delegation_matches(text, task, result, ack):
                if not created_at_dt or (record_timestamp_dt and record_timestamp_dt >= created_at_dt):
                    delegations.append(
                        {
                            "line": line_number,
                            "timestamp": record_timestamp,
                            "turn_id": record_turn_id,
                        }
                    )
                continue
            if role != "assistant" or phase != "final_answer" or not delegations:
                continue
            if expected_turn and expected_turn != "cdp-visible-turn" and record_turn_id and record_turn_id != expected_turn:
                continue
            stripped = _extract_exact_result(text, task, result, ack, result_limit)
            if not stripped:
                continue
            digest = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
            matches.append(
                {
                    "text": stripped,
                    "sha256": digest,
                    "source_file": resolved_path,
                    "source_line": line_number,
                    "source_kind": source_kind,
                    "timestamp": record_timestamp,
                    "thread_id": _thread_id_from_rollout(path),
                    "turn_id": record_turn_id,
                    "delegation_line": int(delegations[-1]["line"]),
                    "delegation_timestamp": str(delegations[-1]["timestamp"]),
                }
            )
            if len(matches) > DEFAULT_MAX_MATCHES:
                return {
                    "ok": False,
                    "healthy": True,
                    "reason": "too_many_owned_result_candidates",
                    "newText": None,
                    "result_complete": False,
                    "search_mode": search_mode,
                }

    by_hash: dict[str, list[dict[str, Any]]] = {}
    for match in matches:
        by_hash.setdefault(str(match["sha256"]), []).append(match)
    if len(by_hash) > 1:
        return {
            "ok": False,
            "healthy": True,
            "reason": "ambiguous_owned_results",
            "newText": None,
            "result_complete": False,
            "candidate_hashes": sorted(by_hash),
            "candidate_count": len(matches),
            "search_mode": search_mode,
        }
    if not by_hash:
        return {
            "ok": True,
            "healthy": True,
            "reason": "owned_result_not_found",
            "newText": None,
            "result_complete": False,
            "search_mode": search_mode,
            "searched_files": len(files),
            "created_at_hint": str(created_at or ""),
        }

    identical = next(iter(by_hash.values()))
    identical.sort(key=lambda item: (str(item.get("timestamp") or ""), int(item.get("source_line") or 0)))
    chosen = identical[-1]
    evidence = {key: value for key, value in chosen.items() if key != "text"}
    return {
        "ok": True,
        "healthy": True,
        "mode": "codex-session-owned-result",
        "session_store_recovery": True,
        "durable_history_recovery": True,
        "newText": chosen["text"],
        "result_complete": True,
        "ack_seen": False,
        "ownership_mismatch": False,
        "ownership": {
            "required": True,
            "protocol": "mobile_result_boundary_v2",
            "valid": True,
            "matched_task_id": task,
            "matched_result_code": result,
            "result_complete": True,
            "stripped_text": chosen["text"],
        },
        "source": evidence,
        "duplicate_copies": len(identical),
        "search_mode": search_mode,
        "searched_files": len(files),
    }
