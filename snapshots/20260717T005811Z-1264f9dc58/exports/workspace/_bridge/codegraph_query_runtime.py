#!/usr/bin/env python3
"""Shared non-blocking CodeGraph query runtime.

Interactive queries require a usable graph index, not a perfectly fresh one.
This module validates the SQLite index locally, returns graph analysis from the
best usable index, and coalesces status/sync work into a bounded background
refresh. Maintenance commands remain free to run strict synchronous checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from shared.process_liveness import process_is_alive as _shared_process_is_alive
except ModuleNotFoundError:
    from _bridge.shared.process_liveness import process_is_alive as _shared_process_is_alive


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "_bridge" / "runtime" / "codegraph_query_runtime"
PROJECT_CODEGRAPH_CMD = ROOT / "_bridge" / "tools" / "codegraph" / "node_modules" / ".bin" / "codegraph.cmd"
REQUIRED_TABLES = {"files", "nodes", "edges", "project_metadata", "schema_versions"}
DEFAULT_REFRESH_COOLDOWN_SECONDS = 300
STALE_REFRESH_COOLDOWN_SECONDS = 30
REFRESH_LOCK_STALE_SECONDS = 180
SYNC_SUPPRESS_SECONDS = 600
PATH_TOKEN_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s\"'`<>|]+|(?:^|[\s\"'`])(?:\.?_bridge|AGENTS\.md|web|src|tests|scripts|config)[\\/][^\s\"'`<>|]+)"
)
EXCLUDE_TOKEN_RE = re.compile(
    r"(?:exclude|excluding|ignore|without|omit|排除|忽略|不要包含)\s+[`'\"]?([A-Za-z0-9_.\\/-]+)",
    re.IGNORECASE,
)
DEFAULT_SCOPE_EXCLUDES = (
    "_bridge/resources",
    "_bridge/backups",
    "_bridge/logs",
    "_bridge/runtime",
    "node_modules",
    ".git",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds(value: Any) -> float | None:
    parsed = parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def command_creationflags(*, detached: bool = False) -> int:
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if detached:
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def pid_is_alive(pid: Any) -> bool:
    return _shared_process_is_alive(pid)


def project_runtime_paths(project_path: Path) -> tuple[Path, Path]:
    key = hashlib.sha256(str(project_path).lower().encode("utf-8")).hexdigest()[:16]
    return RUNTIME_DIR / f"{key}.state.json", RUNTIME_DIR / f"{key}.refresh.lock.json"


def codegraph_command() -> str:
    return str(PROJECT_CODEGRAPH_CMD) if PROJECT_CODEGRAPH_CMD.exists() else "codegraph"


def clean_output(value: Any, *, limit: int) -> str:
    text = str(value or "").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())[-limit:]


def run_command(command: list[str], *, project_path: Path, timeout_seconds: int) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("CODEGRAPH_NO_DAEMON", "1")
    try:
        proc = subprocess.run(
            command,
            cwd=str(project_path),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, timeout_seconds),
            creationflags=command_creationflags(),
        )
    except FileNotFoundError as exc:
        return {"ok": False, "reason": "codegraph_cli_missing", "command": command, "error": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": "timeout",
            "command": command,
            "timeout_seconds": timeout_seconds,
            "stdout": clean_output(exc.stdout, limit=4000),
            "stderr": clean_output(exc.stderr, limit=4000),
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": command,
        "stdout": clean_output(proc.stdout, limit=40000),
        "stderr": clean_output(proc.stderr, limit=8000),
    }


def parse_json_output(value: Any) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    decoder = json.JSONDecoder()
    for offset, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def inspect_index_usability(project_path: Path) -> dict[str, Any]:
    project_path = project_path.expanduser().resolve()
    db_path = project_path / ".codegraph" / "codegraph.db"
    result: dict[str, Any] = {
        "schema": "codegraph_query_runtime.index.v1",
        "ok": False,
        "project_path": str(project_path),
        "db_path": str(db_path),
        "db_exists": db_path.is_file(),
    }
    if not db_path.is_file():
        result["reason"] = "codegraph_index_missing"
        return result
    try:
        stat = db_path.stat()
        result.update({"db_size": stat.st_size, "db_mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            conn.execute("pragma query_only=on")
            quick_check = str(conn.execute("pragma quick_check(1)").fetchone()[0])
            tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table'")}
            missing_tables = sorted(REQUIRED_TABLES - tables)
            counts = {
                table: int(conn.execute(f"select count(*) from {table}").fetchone()[0])
                for table in ("files", "nodes", "edges")
                if table in tables
            }
        finally:
            conn.close()
    except Exception as exc:
        result.update({"reason": "codegraph_index_unreadable", "error": f"{type(exc).__name__}: {exc}"})
        return result
    result.update({"quick_check": quick_check, "missing_tables": missing_tables, "counts": counts})
    if quick_check.lower() != "ok":
        result["reason"] = "codegraph_index_integrity_failed"
    elif missing_tables:
        result["reason"] = "codegraph_index_schema_incomplete"
    elif counts.get("files", 0) <= 0 or counts.get("nodes", 0) <= 0:
        result["reason"] = "codegraph_index_empty"
    else:
        result.update({"ok": True, "reason": "usable_index"})
    return result


def normalize_target(token: str, project_path: Path) -> str:
    text = str(token or "").strip().strip("\"'`.,;:()[]{}")
    if not text:
        return ""
    candidate = Path(text.replace("/", os.sep))
    if candidate.is_absolute():
        try:
            return str(candidate.resolve().relative_to(project_path))
        except Exception:
            return str(candidate)
    return str(candidate)


def extract_freshness_targets(query: str, project_path: Path, explicit: Any) -> list[str]:
    raw: list[str] = []
    explicit_supplied = False
    if isinstance(explicit, list):
        explicit_items = [str(item) for item in explicit if str(item or "").strip()]
        raw.extend(explicit_items)
        explicit_supplied = bool(explicit_items)
    elif isinstance(explicit, str) and explicit.strip():
        raw.append(explicit)
        explicit_supplied = True
    if not explicit_supplied:
        raw.extend(match.group(0).strip() for match in PATH_TOKEN_RE.finditer(str(query or "")))
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        normalized = normalize_target(item, project_path)
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        output.append(normalized)
        if len(output) >= 8:
            break
    return output


def extract_scope_excludes(query: str, project_path: Path, explicit: Any, targets: list[str]) -> list[str]:
    raw: list[str] = []
    if isinstance(explicit, list):
        raw.extend(str(item) for item in explicit if str(item or "").strip())
    elif isinstance(explicit, str) and explicit.strip():
        raw.append(explicit)
    raw.extend(match.group(1) for match in EXCLUDE_TOKEN_RE.finditer(str(query or "")))
    target_keys = {normalize_target(item, project_path).replace("\\", "/").lower().strip("/") for item in targets}
    output: list[str] = []
    seen: set[str] = set()
    for item in (*DEFAULT_SCOPE_EXCLUDES, *raw):
        normalized = normalize_target(item, project_path).replace("\\", "/").lower().strip("/")
        if not normalized or normalized in seen:
            continue
        if any(normalized == target or normalized.startswith(f"{target}/") or target.startswith(f"{normalized}/") for target in target_keys):
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def assess_analysis_scope(analysis: str, targets: list[str], excludes: list[str]) -> dict[str, Any]:
    normalized_analysis = str(analysis or "").replace("\\", "/").lower()
    evidence_analysis = normalized_analysis
    if evidence_analysis.startswith("**exploration:"):
        first_section = evidence_analysis.find("\n**", len("**exploration:"))
        if first_section >= 0:
            evidence_analysis = evidence_analysis[first_section:]
    coverage_rows: list[dict[str, Any]] = []
    for target in targets:
        normalized = str(target).replace("\\", "/").lower().strip("./")
        aliases = [normalized]
        basename = Path(normalized).name.lower()
        if basename and basename not in aliases:
            aliases.append(basename)
        matched = next((alias for alias in aliases if alias and alias in evidence_analysis), "")
        coverage_rows.append({"target": target, "matched": bool(matched), "matched_alias": matched})
    contamination = [item for item in excludes if item and item in evidence_analysis]
    matched_count = sum(1 for item in coverage_rows if item["matched"])
    required_count = len(coverage_rows)
    coverage = 1.0 if not required_count else matched_count / required_count
    minimum_coverage = 1.0 if required_count <= 2 else 0.5
    ok = not contamination and (not required_count or coverage >= minimum_coverage)
    if contamination:
        reason = "excluded_path_contamination"
    elif required_count and coverage < minimum_coverage:
        reason = "target_coverage_insufficient"
    else:
        reason = "scope_accepted"
    return {
        "schema": "codegraph_query_runtime.scope_acceptance.v1",
        "ok": ok,
        "reason": reason,
        "coverage": round(coverage, 3),
        "minimum_coverage": minimum_coverage,
        "targets": coverage_rows,
        "excludes": excludes,
        "contamination": contamination,
        "acceptance_rule": "freshness and successful execution are insufficient unless explicit target coverage and exclusion constraints also pass",
    }


def tightened_scope_query(query: str, targets: list[str], excludes: list[str]) -> str:
    parts = [str(query or "").strip()]
    if targets:
        parts.append("Restrict analysis to these requested paths: " + ", ".join(targets) + ".")
    if excludes:
        parts.append("Exclude results from: " + ", ".join(excludes) + ".")
    parts.append("Every cited file must satisfy the requested path scope.")
    return " ".join(item for item in parts if item)


def inspect_target_freshness(project_path: Path, targets: list[str], index: dict[str, Any]) -> dict[str, Any]:
    db_time = parse_time(index.get("db_mtime"))
    rows: list[dict[str, Any]] = []
    for target in targets:
        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = project_path / candidate
        row: dict[str, Any] = {"target": target, "path": str(candidate.resolve())}
        try:
            stat = candidate.stat()
        except OSError as exc:
            row.update({"state": "stale", "reason": "target_missing_or_unreadable", "error": str(exc)})
            rows.append(row)
            continue
        if candidate.is_dir():
            row.update({"state": "unknown", "reason": "directory_target_not_recursively_scanned"})
        elif db_time is None:
            row.update({"state": "unknown", "reason": "index_mtime_unavailable"})
        else:
            target_time = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            row.update(
                {
                    "target_mtime": target_time.isoformat(),
                    "state": "fresh" if target_time <= db_time else "stale",
                    "reason": "target_not_newer_than_index" if target_time <= db_time else "target_newer_than_index",
                }
            )
        rows.append(row)
    states = {str(row.get("state")) for row in rows}
    if not rows:
        state, reason = "unknown", "no_explicit_target_paths"
    elif "stale" in states:
        state, reason = "stale", "one_or_more_targets_stale"
    elif "unknown" in states:
        state, reason = "unknown", "one_or_more_targets_unknown"
    else:
        state, reason = "fresh", "all_explicit_targets_fresh"
    return {
        "schema": "codegraph_query_runtime.freshness.v1",
        "state": state,
        "reason": reason,
        "targets": rows,
        "index_mtime": index.get("db_mtime"),
        "rule": "Target freshness is advisory; a valid index remains queryable while refresh runs in the background.",
    }


def acquire_refresh_lock(lock_path: Path, reason: str) -> tuple[bool, dict[str, Any]]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_json(lock_path)
    if lock_path.exists():
        lock_age = age_seconds(existing.get("started_at"))
        if pid_is_alive(existing.get("pid")) or (lock_age is not None and lock_age < REFRESH_LOCK_STALE_SECONDS):
            return False, {"state": "coalesced", "reason": "refresh_already_running", "lock": existing}
        try:
            lock_path.unlink()
        except OSError:
            return False, {"state": "coalesced", "reason": "stale_refresh_lock_not_removable", "lock": existing}
    payload = {"schema": "codegraph_query_runtime.refresh_lock.v1", "started_at": now_iso(), "pid": 0, "reason": reason}
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except FileExistsError:
        return False, {"state": "coalesced", "reason": "refresh_lock_race", "lock": read_json(lock_path)}
    return True, payload


def request_background_refresh(
    project_path: Path,
    *,
    reason: str,
    force_sync: bool = False,
    cooldown_seconds: int | None = None,
) -> dict[str, Any]:
    project_path = project_path.expanduser().resolve()
    state_path, lock_path = project_runtime_paths(project_path)
    state = read_json(state_path)
    cooldown = int(cooldown_seconds if cooldown_seconds is not None else (STALE_REFRESH_COOLDOWN_SECONDS if force_sync else DEFAULT_REFRESH_COOLDOWN_SECONDS))
    request_age = age_seconds(state.get("last_requested_at"))
    if request_age is not None and request_age < cooldown:
        return {
            "ok": True,
            "state": "coalesced",
            "reason": "refresh_cooldown_active",
            "cooldown_seconds": cooldown,
            "age_seconds": round(request_age, 3),
            "last_result": state,
        }
    acquired, lock = acquire_refresh_lock(lock_path, reason)
    if not acquired:
        return {"ok": True, **lock}
    requested_at = now_iso()
    requested_state = {
        **state,
        "schema": "codegraph_query_runtime.refresh_state.v1",
        "project_path": str(project_path),
        "last_requested_at": requested_at,
        "last_reason": reason,
        "force_sync": bool(force_sync),
        "state": "starting",
    }
    atomic_write_json(state_path, requested_state)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--refresh-worker",
        "--project-path",
        str(project_path),
        "--reason",
        reason,
    ]
    if force_sync:
        command.append("--force-sync")
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=command_creationflags(detached=True),
            start_new_session=(os.name != "nt"),
        )
    except Exception as exc:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        failed = {**requested_state, "state": "launch_failed", "error": f"{type(exc).__name__}: {exc}"}
        atomic_write_json(state_path, failed)
        return {"ok": False, "state": "launch_failed", "error": failed["error"]}
    return {"ok": True, "state": "scheduled", "pid": proc.pid, "reason": reason, "force_sync": bool(force_sync)}


def pending_counts(payload: dict[str, Any] | None) -> tuple[dict[str, int], int]:
    pending = payload.get("pendingChanges") if isinstance(payload, dict) else {}
    counts = {key: int((pending or {}).get(key) or 0) for key in ("added", "modified", "removed")}
    return counts, sum(counts.values())


def refresh_worker(project_path: Path, *, reason: str, force_sync: bool) -> int:
    project_path = project_path.expanduser().resolve()
    state_path, lock_path = project_runtime_paths(project_path)
    state = read_json(state_path)
    started_at = now_iso()
    try:
        lock = read_json(lock_path)
        atomic_write_json(lock_path, {**lock, "pid": os.getpid(), "started_at": started_at, "reason": reason})
        atomic_write_json(state_path, {**state, "state": "running", "pid": os.getpid(), "started_at": started_at})

        status = run_command(
            [codegraph_command(), "status", str(project_path), "--json"],
            project_path=project_path,
            timeout_seconds=15,
        )
        status_payload = parse_json_output(status.get("stdout")) if status.get("ok") else None
        status_known = isinstance(status_payload, dict)
        counts, total = pending_counts(status_payload)
        pending_signature = json.dumps(counts, sort_keys=True, separators=(",", ":")) if status_known else None
        last_sync_age = age_seconds(state.get("last_sync_at"))
        same_pending_recent = bool(
            status_known
            and pending_signature == str(state.get("last_pending_signature") or "")
            and last_sync_age is not None
            and last_sync_age < SYNC_SUPPRESS_SECONDS
        )
        sync_required = bool(
            force_sync
            or (status_known and total > 0)
            or (status_known and ((status_payload or {}).get("index") or {}).get("reindexRecommended"))
        )
        sync: dict[str, Any] = {
            "ok": True,
            "skipped": True,
            "reason": "no_sync_needed" if status_known else "status_unavailable_no_forced_sync",
        }
        if sync_required and same_pending_recent and not force_sync:
            sync = {
                "ok": True,
                "skipped": True,
                "reason": "same_pending_recently_synced",
                "suppress_seconds": SYNC_SUPPRESS_SECONDS,
            }
        elif sync_required:
            sync = run_command(
                [codegraph_command(), "sync", str(project_path)],
                project_path=project_path,
                timeout_seconds=45,
            )

        index = inspect_index_usability(project_path)
        sync_completed = bool(sync.get("ok") and not sync.get("skipped"))
        maintenance_ok = bool(status_known or sync_completed)
        service_ok = bool(index.get("ok"))
        if service_ok and maintenance_ok and bool(sync.get("ok")):
            final_state = "completed"
        elif service_ok:
            final_state = "degraded"
        else:
            final_state = "failed"
        completed_at = now_iso()
        result = {
            **state,
            "schema": "codegraph_query_runtime.refresh_state.v1",
            "project_path": str(project_path),
            "state": final_state,
            "ok": service_ok,
            "maintenance_ok": maintenance_ok and bool(sync.get("ok")),
            "pid": os.getpid(),
            "started_at": started_at,
            "completed_at": completed_at,
            "last_completed_at": completed_at,
            "last_reason": reason,
            "status": status,
            "pending_counts": counts if status_known else None,
            "pending_total": total if status_known else None,
            "sync": sync,
            "index": index,
        }
        if pending_signature is not None:
            result["last_pending_signature"] = pending_signature
        if sync_completed:
            result["last_sync_at"] = completed_at
        atomic_write_json(state_path, result)
        return 0 if service_ok else 1
    except Exception as exc:
        completed_at = now_iso()
        failure = {
            **state,
            "schema": "codegraph_query_runtime.refresh_state.v1",
            "project_path": str(project_path),
            "state": "failed",
            "ok": False,
            "maintenance_ok": False,
            "pid": os.getpid(),
            "started_at": started_at,
            "completed_at": completed_at,
            "last_completed_at": completed_at,
            "last_reason": reason,
            "reason": "refresh_worker_exception",
            "error": f"{type(exc).__name__}: {exc}",
        }
        try:
            atomic_write_json(state_path, failure)
        except Exception:
            pass
        return 1
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def run_codegraph_explore(query: str, project_path: Path, max_files: int, timeout_seconds: int) -> dict[str, Any]:
    return run_command(
        [
            codegraph_command(),
            "explore",
            query,
            "--path",
            str(project_path),
            "--max-files",
            str(max(1, min(max_files, 12))),
        ],
        project_path=project_path,
        timeout_seconds=max(1, min(timeout_seconds, 120)),
    )


def run_target_file_fallback(targets: list[str], project_path: Path, max_files: int, timeout_seconds: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    budget = max(1, min(max_files, 8))
    per_file_timeout = max(2, min(timeout_seconds, 20))
    for target in targets[:budget]:
        candidate = project_path / target
        if not candidate.is_file():
            rows.append({"target": target, "ok": False, "reason": "target_not_file"})
            continue
        result = run_command(
            [codegraph_command(), "node", "--path", str(project_path), "--file", str(target), "--limit", "160"],
            project_path=project_path,
            timeout_seconds=per_file_timeout,
        )
        rows.append({"target": target, **result})
    accepted = [item for item in rows if item.get("ok") and str(item.get("stdout") or "").strip()]
    return {
        "schema": "codegraph_query_runtime.target_file_fallback.v1",
        "ok": bool(accepted),
        "attempted_count": len(rows),
        "accepted_count": len(accepted),
        "rows": rows,
        "analysis": "\n\n".join(str(item.get("stdout") or "") for item in accepted),
    }


def query_codegraph(
    query: str,
    *,
    project_path: str | Path = ROOT,
    max_files: int = 8,
    timeout_seconds: int = 60,
    freshness_targets: Any = None,
    exclude_paths: Any = None,
) -> dict[str, Any]:
    text = str(query or "").strip()
    if not text:
        return {"ok": False, "reason": "query_required"}
    project = Path(project_path).expanduser().resolve()
    index = inspect_index_usability(project)
    if not index.get("ok"):
        return {"ok": False, "reason": "codegraph_index_unusable", "index": index}
    targets = extract_freshness_targets(text, project, freshness_targets)
    excludes = extract_scope_excludes(text, project, exclude_paths, targets)
    freshness = inspect_target_freshness(project, targets, index)
    refresh = request_background_refresh(
        project,
        reason=f"query_{freshness.get('state')}",
        force_sync=freshness.get("state") == "stale",
    )
    explored = run_codegraph_explore(text, project, max_files, timeout_seconds)
    if not explored.get("ok"):
        return {
            "ok": False,
            "reason": "codegraph_explore_failed",
            "index": index,
            "freshness": freshness,
            "refresh": refresh,
            "explore": explored,
        }
    scope = assess_analysis_scope(explored.get("stdout") or "", targets, excludes)
    attempts = [{"query": text, "scope": scope}]
    if not scope.get("ok"):
        refined_query = tightened_scope_query(text, targets, excludes)
        refined = run_codegraph_explore(refined_query, project, max_files, timeout_seconds)
        if refined.get("ok"):
            refined_scope = assess_analysis_scope(refined.get("stdout") or "", targets, excludes)
            attempts.append({"query": refined_query, "scope": refined_scope})
            explored = refined
            scope = refined_scope
        else:
            attempts.append({"query": refined_query, "scope": {"ok": False, "reason": "refined_explore_failed"}, "explore": refined})
    if not scope.get("ok"):
        target_fallback = run_target_file_fallback(targets, project, max_files, timeout_seconds) if targets else {"ok": False, "reason": "no_explicit_targets"}
        if target_fallback.get("ok"):
            fallback_scope = assess_analysis_scope(target_fallback.get("analysis") or "", targets, excludes)
            if fallback_scope.get("ok"):
                return {
                    "schema": "codegraph_query_runtime.result.v1",
                    "ok": True,
                    "analysis": target_fallback.get("analysis") or "",
                    "stderr": explored.get("stderr") or "",
                    "index": index,
                    "freshness": freshness,
                    "refresh": refresh,
                    "scope": fallback_scope,
                    "scope_attempts": attempts,
                    "target_file_fallback": target_fallback,
                    "degraded": True,
                    "degraded_reason": "scope_fallback_target_files",
                    "analysis_limit": "target source and dependents only; broad call-graph inference was rejected by scope acceptance",
                }
        return {
            "schema": "codegraph_query_runtime.result.v1",
            "ok": False,
            "reason": "codegraph_scope_insufficient",
            "analysis": explored.get("stdout") or "",
            "stderr": explored.get("stderr") or "",
            "index": index,
            "freshness": freshness,
            "refresh": refresh,
            "scope": scope,
            "scope_attempts": attempts,
            "target_file_fallback": target_fallback,
            "next_action": "refine_targets_or_exclusions_then_retry",
        }
    return {
        "schema": "codegraph_query_runtime.result.v1",
        "ok": True,
        "analysis": explored.get("stdout") or "",
        "stderr": explored.get("stderr") or "",
        "index": index,
        "freshness": freshness,
        "refresh": refresh,
        "scope": scope,
        "scope_attempts": attempts,
        "degraded": freshness.get("state") != "fresh",
        "degraded_reason": None if freshness.get("state") == "fresh" else f"freshness_{freshness.get('state')}",
    }


def prelaunch_check(project_path: str | Path = ROOT) -> dict[str, Any]:
    project = Path(project_path).expanduser().resolve()
    index = inspect_index_usability(project)
    if not index.get("ok"):
        return {"ok": False, "phase": "index_validation", "reason": "codegraph_index_unusable", "index": index}
    refresh = request_background_refresh(project, reason="mcp_prelaunch", force_sync=False)
    return {
        "ok": True,
        "phase": "usable_index_background_refresh",
        "index": index,
        "refresh": refresh,
        "rule": "MCP startup is blocked only by an unusable index; freshness maintenance is coalesced in the background.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Shared non-blocking CodeGraph query runtime")
    parser.add_argument("--refresh-worker", action="store_true")
    parser.add_argument("--project-path", default=str(ROOT))
    parser.add_argument("--reason", default="manual")
    parser.add_argument("--force-sync", action="store_true")
    args = parser.parse_args()
    if args.refresh_worker:
        return refresh_worker(Path(args.project_path), reason=str(args.reason), force_sync=bool(args.force_sync))
    payload = prelaunch_check(args.project_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
