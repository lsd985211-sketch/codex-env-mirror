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
from pathlib import Path
from typing import Any, Iterable


SAFE_PROTOCOL_ID = re.compile(r"^[A-Za-z0-9_-]+$")
ROLLOUT_THREAD_ID = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def _valid_id(value: str) -> bool:
    return bool(value and SAFE_PROTOCOL_ID.fullmatch(value))


def is_usable_owned_result_text(value: str) -> bool:
    """Reject empty and known UI truncation placeholders without rejecting short replies."""
    text = str(value or "").strip()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    return re.fullmatch(r"(?:\.{1,12}|…{1,6}|。{1,6})", compact) is None


def _default_session_roots() -> list[Path]:
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return [codex_home / "sessions"]


def _candidate_files_with_rg(begin_marker: str, roots: list[Path], max_files: int) -> list[Path]:
    rg = shutil.which("rg")
    existing = [root for root in roots if root.exists()]
    if not rg or not existing:
        return []
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
        return []
    if completed.returncode not in {0, 1}:
        return []
    paths = [Path(line.strip()) for line in completed.stdout.splitlines() if line.strip()]
    paths.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    return paths[:max_files]


def _candidate_files_bounded(roots: list[Path], max_files: int) -> list[Path]:
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        stack = [root]
        while stack and len(candidates) < max_files:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir(), key=lambda item: item.name, reverse=True)
            except OSError:
                continue
            directories = [item for item in entries if item.is_dir()]
            stack.extend(reversed(directories))
            candidates.extend(item for item in entries if item.is_file() and item.suffix == ".jsonl")
    candidates.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    return candidates[:max_files]


def _message_text(record: dict[str, Any]) -> tuple[str, str]:
    record_type = str(record.get("type") or "")
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    if record_type == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant":
        content = payload.get("content") if isinstance(payload.get("content"), list) else []
        parts = [str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "output_text"]
        return "\n".join(part for part in parts if part), "response_item.assistant"
    if record_type == "event_msg" and payload.get("type") == "agent_message":
        return str(payload.get("message") or ""), "event_msg.agent_message"
    return "", ""


def _extract_exact_result(text: str, task_id: str, result_code: str, ack_code: str) -> str:
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
    return body if is_usable_owned_result_text(body) else ""


def _thread_id_from_rollout(path: Path) -> str:
    match = ROLLOUT_THREAD_ID.search(path.stem)
    return match.group(1) if match else ""


def _iter_records(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, raw in enumerate(handle, start=1):
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
    max_files: int = 256,
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
    begin_marker = f"[[mobile_result_begin:{task}:{result}]]"
    files = _candidate_files_with_rg(begin_marker, roots, limit)
    search_mode = "rg_fixed_string"
    if not files:
        files = _candidate_files_bounded(roots, limit)
        search_mode = "bounded_recent_files"

    matches: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for path in files:
        resolved_path = str(path.resolve())
        if resolved_path in seen_files:
            continue
        seen_files.add(resolved_path)
        for line_number, record in _iter_records(path):
            text, source_kind = _message_text(record)
            if not text or begin_marker not in text:
                continue
            stripped = _extract_exact_result(text, task, result, ack)
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
                    "timestamp": str(record.get("timestamp") or ""),
                    "thread_id": _thread_id_from_rollout(path),
                }
            )

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
