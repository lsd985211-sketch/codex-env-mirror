#!/usr/bin/env python3
"""Materialize the isolated, Linux-facing Codex runtime for Codex-Wsl-Lab.

The work Git owns templates and active capability files. The WSL home owns
credentials and databases. Windows session files are imported into an isolated
WSL projection whose working directories are translated without mutating the
Windows source or importing the rest of the Windows runtime state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CODEX_HOME = Path(os.environ.get("WSL_CODEX_HOME", str(Path.home() / ".codex-app"))).expanduser().resolve()
TEMPLATE = ROOT / "codex-home" / "config.wsl.template.toml"
NODE_WRAPPER = ROOT / "workspace" / "_bridge" / "codex_node_repl_wsl.sh"
NODE_ENTRY = Path.home() / ".local" / "bin" / "codex-node-repl"
RUNTIME_ROOT = ROOT / "workspace" / "_bridge" / "runtime" / "wsl_codex"
WINDOWS_SESSIONS = Path("/mnt/c/Users/45543/.codex/sessions")
WINDOWS_STATE_DB = Path("/mnt/c/Users/45543/.codex/state_5.sqlite")
SESSION_MANIFEST = CODEX_HOME / "session-projection-manifest.json"
SESSION_TRANSITION_ROOT = CODEX_HOME / ".session-projection-transition"
STATE_DB = CODEX_HOME / "state_5.sqlite"
DRIVE_OVERRIDES = {"w": ROOT}
SAFE_INSERT_SOURCE_FIELDS = frozenset({
    "id",
    "rollout_path",
    "created_at",
    "updated_at",
    "source",
    "model_provider",
    "cwd",
    "title",
    "tokens_used",
    "has_user_event",
    "archived",
    "archived_at",
    "cli_version",
    "first_user_message",
    "agent_nickname",
    "agent_role",
    "memory_mode",
    "model",
    "reasoning_effort",
    "created_at_ms",
    "updated_at_ms",
    "thread_source",
    "preview",
    "recency_at",
    "recency_at_ms",
    "history_mode",
})
SAFE_INSERT_SANDBOX_POLICY = '{"type":"read-only"}'
SAFE_INSERT_APPROVAL_MODE = "on-request"
PROFILE_PATH = Path.home() / ".profile"
PROFILE_START = "# >>> codex-desktop-wsl-runtime >>>"
PROFILE_END = "# <<< codex-desktop-wsl-runtime <<<"
SESSION_PROJECTION_SCHEMA = "codex-wsl-session-projection.v2"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def desktop_profile_block() -> str:
    return "\n".join([
        PROFILE_START,
        'if [ "${CODEX_INTERNAL_ORIGINATOR_OVERRIDE:-}" = "Codex Desktop" ]; then',
        '    export CODEX_HOME="$HOME/.codex-app"',
        "fi",
        PROFILE_END,
    ])


def render_profile(current: str) -> str:
    start = current.find(PROFILE_START)
    end = current.find(PROFILE_END)
    if start >= 0 and end >= start:
        end += len(PROFILE_END)
        current = current[:start].rstrip() + "\n" + current[end:].lstrip()
    return current.rstrip() + "\n\n" + desktop_profile_block() + "\n"


def managed_link_status(source: Path, target: Path) -> dict[str, object]:
    if target.is_symlink():
        linked = target.resolve() == source.resolve()
        return {
            "path": str(target),
            "source": str(source),
            "status": "linked" if linked else "conflicting_symlink",
            "target": os.readlink(target),
            "ok": linked,
        }
    if target.exists():
        return {
            "path": str(target),
            "source": str(source),
            "status": "conflicting_existing_path",
            "ok": False,
        }
    return {"path": str(target), "source": str(source), "status": "would_link", "ok": True}


def link_or_verify(source: Path, target: Path) -> dict[str, object]:
    inspected = managed_link_status(source, target)
    if inspected["status"] != "would_link":
        return inspected
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=source.is_dir())
    return {
        "path": str(target),
        "source": str(source),
        "status": "linked",
        "target": os.readlink(target),
        "ok": True,
    }


def link_skill_tree(source: Path, target: Path, *, write: bool) -> dict[str, object]:
    """Link user skills individually so Codex system skills stay runtime-local."""
    if target.is_symlink():
        if target.resolve() == source.resolve():
            if not write:
                return {
                    "path": str(target),
                    "source": str(source),
                    "status": "would_migrate_shared_tree",
                    "ok": True,
                }
            generated = source / ".system"
            staged = target.parent / ".system-migration"
            if generated.exists() and not staged.exists():
                shutil.copytree(generated, staged)
            target.unlink()
            target.mkdir(parents=True, exist_ok=True)
            if staged.exists() and not (target / ".system").exists():
                shutil.move(str(staged), str(target / ".system"))
            if generated.exists():
                shutil.rmtree(generated)
        else:
            return {
                "path": str(target),
                "source": str(source),
                "status": "conflicting_symlink",
                "ok": False,
            }
    if target.exists() and not target.is_dir():
        return {
            "path": str(target),
            "source": str(source),
            "status": "conflicting_existing_path",
            "ok": False,
        }
    conflicts: list[str] = []
    missing: list[Path] = []
    for child in sorted(source.iterdir()):
        if child.name == ".system":
            continue
        destination = target / child.name
        if destination.is_symlink() and destination.resolve() == child.resolve():
            continue
        if destination.exists() or destination.is_symlink():
            conflicts.append(child.name)
        else:
            missing.append(child)
    if conflicts:
        return {
            "path": str(target),
            "source": str(source),
            "status": "conflicting_children",
            "conflicts": conflicts[:20],
            "ok": False,
        }
    if not write:
        return {
            "path": str(target),
            "source": str(source),
            "status": "would_link_children" if missing else "linked_children",
            "linked_count": 0,
            "missing_count": len(missing),
            "ok": True,
        }
    target.mkdir(parents=True, exist_ok=True)
    linked = 0
    for child in missing:
        destination = target / child.name
        destination.symlink_to(child, target_is_directory=child.is_dir())
        linked += 1
    return {
        "path": str(target),
        "source": str(source),
        "status": "linked_children",
        "linked_count": linked,
        "ok": True,
    }


def windows_cwd_to_wsl(value: str) -> tuple[str, str]:
    """Translate a Windows session cwd without making the Windows source mutable."""
    raw = str(value or "").strip()
    if not raw:
        return str(ROOT), "fallback_workspace"
    if raw.startswith("/"):
        candidate = Path(raw)
        return (raw, "native") if candidate.is_dir() else (str(ROOT), "fallback_workspace")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("//?/"):
        normalized = normalized[4:]
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        drive = normalized[0].lower()
        if drive in DRIVE_OVERRIDES:
            return str(DRIVE_OVERRIDES[drive]), "drive_override"
        candidate = Path(f"/mnt/{drive}") / normalized[3:]
        if candidate.is_dir():
            return str(candidate), "drive_mount"
    return str(ROOT), "fallback_workspace"


def windows_file_path_to_wsl(value: str) -> Path | None:
    """Map a Windows file path for identity checks without accepting UNC fallbacks."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("/"):
        return Path(raw).resolve()
    normalized = raw.replace("\\", "/")
    if normalized.startswith("//?/"):
        normalized = normalized[4:]
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        drive = normalized[0].lower()
        return (Path(f"/mnt/{drive}") / normalized[3:]).resolve()
    return None


def safe_session_relative_path(value: str) -> Path | None:
    candidate = Path(str(value or ""))
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts or candidate.suffix != ".jsonl":
        return None
    return candidate


def safe_projection_destination(root: Path, relative: Path, *, create: bool) -> Path:
    resolved_root = root.resolve()
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"projection target contains a symlink: {current}")
        if create:
            current.mkdir(exist_ok=True)
    destination = root / relative
    if destination.is_symlink():
        raise ValueError(f"projection target is a symlink: {destination}")
    if destination.parent.exists() and not destination.parent.resolve().is_relative_to(resolved_root):
        raise ValueError(f"projection target escapes root: {destination}")
    return destination


def atomic_write_bytes(target: Path, content_writer: object) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            content_writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _session_projection_file(source: Path, target: Path) -> tuple[bool, str]:
    """Copy a JSONL session and translate every resume-context cwd."""
    translated_from = ""
    translated_to = ""

    def copy_projected(handle: object) -> None:
        nonlocal translated_from, translated_to
        with source.open("rb") as source_handle:
            for raw_line in source_handle:
                if not raw_line.strip():
                    handle.write(raw_line)
                    continue
                # Most records contain large arbitrary payloads. Only decode
                # records whose type can carry the cwd used during resume.
                if b'"session_meta"' in raw_line or b'"turn_context"' in raw_line:
                    record = json.loads(raw_line.decode("utf-8", errors="strict"))
                    if record.get("type") in {"session_meta", "turn_context"}:
                        payload = record.get("payload")
                        if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
                            original = payload["cwd"]
                            projected, _ = windows_cwd_to_wsl(original)
                            if projected != original:
                                if not translated_from:
                                    translated_from = original
                                if not translated_to:
                                    translated_to = projected
                                payload["cwd"] = projected
                                newline = b"\r\n" if raw_line.endswith(b"\r\n") else b"\n" if raw_line.endswith(b"\n") else b""
                                raw_line = json.dumps(
                                    record,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ).encode("utf-8") + newline
                handle.write(raw_line)

    atomic_write_bytes(target, copy_projected)
    return translated_to != translated_from, translated_to or ""


def _load_manifest() -> dict[str, object]:
    if not SESSION_MANIFEST.is_file():
        return {"files": {}}
    try:
        value = json.loads(SESSION_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": {}}
    if not isinstance(value, dict):
        return {"files": {}}
    # v2 reprojects all resume-context records, so a v1 manifest must not
    # allow stale targets to pass the size/mtime fast path.
    if value.get("schema") == "codex-wsl-session-projection.v1":
        return {"files": {}}
    return value


def project_sessions(*, write: bool) -> dict[str, object]:
    """Keep an isolated WSL session projection; never rewrite Windows sessions."""
    target = CODEX_HOME / "sessions"
    result: dict[str, object] = {
        "path": str(target),
        "source": str(WINDOWS_SESSIONS),
        "manifest": str(SESSION_MANIFEST),
        "status": "source_missing_optional",
        "source_count": 0,
        "projected_count": 0,
        "translated_count": 0,
        "changed": False,
        "native_files_preserved": 0,
        "fallback_cwd": str(ROOT),
        "ok": True,
    }
    if not WINDOWS_SESSIONS.is_dir():
        return result
    source_root = WINDOWS_SESSIONS.resolve()
    source_files = sorted(WINDOWS_SESSIONS.rglob("*.jsonl"))
    for source in source_files:
        if source.is_symlink() or not source.resolve().is_relative_to(source_root):
            result["status"] = "unsafe_source_path"
            result["ok"] = False
            result["error"] = str(source)
            return result
    result["source_count"] = len(source_files)
    if target.is_symlink():
        if target.resolve() != WINDOWS_SESSIONS.resolve():
            result["status"] = "conflicting_symlink"
            return result
        if not write:
            result["status"] = "would_replace_shared_symlink"
            result["changed"] = True
            return result
        SESSION_TRANSITION_ROOT.mkdir(parents=True, exist_ok=True)
        legacy = SESSION_TRANSITION_ROOT / "sessions-shared-windows"
        if legacy.exists() or legacy.is_symlink():
            legacy.unlink()
        target.rename(legacy)
        target.mkdir(parents=True, exist_ok=True)
        result["changed"] = True
        result["replaced_symlink"] = str(legacy)
    elif target.exists() and not target.is_dir():
        result["status"] = "conflicting_non_directory"
        return result
    elif write:
        target.mkdir(parents=True, exist_ok=True)
    elif not target.exists():
        result["status"] = "would_create_projection"
        result["changed"] = True
        return result

    previous = _load_manifest().get("files")
    previous_files = previous if isinstance(previous, dict) else {}
    invalid_manifest_keys = [key for key in previous_files if safe_session_relative_path(str(key)) is None]
    if invalid_manifest_keys:
        result["status"] = "manifest_invalid"
        result["ok"] = False
        result["invalid_manifest_keys"] = invalid_manifest_keys[:10]
        return result
    current_files: dict[str, dict[str, object]] = {}
    result["source_count"] = len(source_files)
    if write:
        for source in source_files:
            relative = source.relative_to(WINDOWS_SESSIONS).as_posix()
            relative_path = safe_session_relative_path(relative)
            if relative_path is None:
                result["status"] = "unsafe_source_path"
                result["ok"] = False
                result["error"] = relative
                return result
            try:
                destination = safe_projection_destination(target, relative_path, create=True)
            except ValueError as exc:
                result["status"] = "unsafe_target_path"
                result["ok"] = False
                result["error"] = str(exc)
                return result
            stat_result = source.stat()
            signature = {"size": stat_result.st_size, "mtime_ns": stat_result.st_mtime_ns}
            prior = previous_files.get(relative)
            if prior == signature and destination.is_file():
                current_files[relative] = signature
                result["projected_count"] = int(result["projected_count"]) + 1
                continue
            translated, projected_cwd = _session_projection_file(source, destination)
            final_stat = source.stat()
            final_signature = {"size": final_stat.st_size, "mtime_ns": final_stat.st_mtime_ns}
            if final_signature != signature:
                translated_again, projected_cwd_again = _session_projection_file(source, destination)
                translated = translated or translated_again
                projected_cwd = projected_cwd_again or projected_cwd
                final_stat = source.stat()
                final_signature = {"size": final_stat.st_size, "mtime_ns": final_stat.st_mtime_ns}
            current_files[relative] = final_signature
            result["projected_count"] = int(result["projected_count"]) + 1
            result["translated_count"] = int(result["translated_count"]) + int(translated)
            result["changed"] = True
            if projected_cwd and len(result.setdefault("sample_cwds", [])) < 5:
                result.setdefault("sample_cwds", []).append({"source": relative, "cwd": projected_cwd})
        for relative in set(previous_files) - set(current_files):
            stale = safe_projection_destination(target, safe_session_relative_path(str(relative)), create=False)
            if stale.is_file():
                stale.unlink()
                result["changed"] = True
        manifest = {
            "schema": SESSION_PROJECTION_SCHEMA,
            "source": str(WINDOWS_SESSIONS),
            "target": str(target),
            "generated_at": now_iso(),
            "files": current_files,
        }
        if result["changed"] or not SESSION_MANIFEST.is_file():
            SESSION_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
            content = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            atomic_write_bytes(SESSION_MANIFEST, lambda handle: handle.write(content))
    else:
        existing_count = 0
        fresh_count = 0
        for source in source_files:
            relative = source.relative_to(WINDOWS_SESSIONS).as_posix()
            relative_path = safe_session_relative_path(relative)
            if relative_path is None:
                result["status"] = "unsafe_source_path"
                result["ok"] = False
                result["error"] = relative
                return result
            try:
                destination = safe_projection_destination(target, relative_path, create=False)
            except ValueError as exc:
                result["status"] = "unsafe_target_path"
                result["ok"] = False
                result["error"] = str(exc)
                return result
            if not destination.is_file():
                continue
            existing_count += 1
            stat_result = source.stat()
            signature = {"size": stat_result.st_size, "mtime_ns": stat_result.st_mtime_ns}
            if previous_files.get(relative) == signature:
                fresh_count += 1
        result["existing_count"] = existing_count
        result["projected_count"] = fresh_count
        current = fresh_count == result["source_count"] and SESSION_MANIFEST.is_file()
        result["status"] = "projected" if current else "would_project"
        result["changed"] = not current
        return result
    result["native_files_preserved"] = sum(1 for path in target.rglob("*.jsonl") if path.relative_to(target).as_posix() not in current_files)
    result["status"] = "projected"
    return result


def project_state_db(*, write: bool) -> dict[str, object]:
    """Merge native thread-list metadata into WSL without replacing WSL policy state."""
    result: dict[str, object] = {
        "path": str(STATE_DB),
        "source": str(WINDOWS_STATE_DB),
        "status": "missing_optional",
        "rows": 0,
        "source_rows": 0,
        "source_session_count": 0,
        "source_missing_row_count": 0,
        "metadata_update_count": 0,
        "inserted_count": 0,
        "translated_count": 0,
        "local_rows_preserved": 0,
        "changed": False,
        "source_rejected_row_count": 0,
        "ok": True,
    }
    if not STATE_DB.is_file():
        return result
    session_targets: dict[str, str] = {}
    session_sources: dict[str, Path] = {}
    duplicate_ids: set[str] = set()
    if WINDOWS_SESSIONS.is_dir():
        for source in sorted(WINDOWS_SESSIONS.rglob("*.jsonl")):
            thread_id = ""
            try:
                with source.open("r", encoding="utf-8", errors="strict") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        if record.get("type") == "session_meta":
                            payload = record.get("payload")
                            if isinstance(payload, dict):
                                thread_id = str(payload.get("id") or "").strip()
                        break
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                result["status"] = "session_metadata_unreadable"
                result["error"] = f"{source}: {exc}"
                result["ok"] = False
                return result
            if not thread_id:
                result["status"] = "session_metadata_missing_id"
                result["error"] = str(source)
                result["ok"] = False
                return result
            target = CODEX_HOME / "sessions" / source.relative_to(WINDOWS_SESSIONS)
            if thread_id in session_targets and session_targets[thread_id] != str(target):
                duplicate_ids.add(thread_id)
            session_targets[thread_id] = str(target)
            session_sources[thread_id] = source.resolve()
    result["source_session_count"] = len(session_targets)
    if duplicate_ids:
        result["status"] = "duplicate_session_ids"
        result["duplicate_ids"] = sorted(duplicate_ids)[:10]
        result["ok"] = False
        return result

    text_fill_fields = ("title", "first_user_message", "preview", "thread_source", "history_mode")
    max_fields = (
        "tokens_used",
        "has_user_event",
        "updated_at",
        "updated_at_ms",
        "recency_at",
        "recency_at_ms",
    )
    min_fields = ("created_at", "created_at_ms")

    def table_columns(connection: sqlite3.Connection) -> list[tuple[str, bool, object]]:
        return [(str(row[1]), bool(row[3]), row[4]) for row in connection.execute("PRAGMA table_info(threads)")]

    def row_dict(row: sqlite3.Row) -> dict[str, object]:
        return {key: row[key] for key in row.keys()}

    def quote_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def min_present(left: object, right: object) -> object:
        values = [value for value in (left, right) if value is not None and value != 0]
        return min(values) if values else left if left is not None else right

    def max_present(left: object, right: object) -> object:
        values = [value for value in (left, right) if value is not None]
        return max(values) if values else None

    try:
        state_uri = f"file:{STATE_DB.as_posix()}?mode={'rw' if write else 'ro'}"
        connection = sqlite3.connect(state_uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        destination_schema = table_columns(connection)
        destination_columns = [name for name, _, _ in destination_schema]
        if "id" not in destination_columns:
            raise sqlite3.DatabaseError("WSL threads table has no id column")
        destination_rows = {
            str(row["id"]): row_dict(row)
            for row in connection.execute("SELECT * FROM threads")
        }
        result["rows"] = len(destination_rows)
        result["local_rows_preserved"] = len(set(destination_rows) - set(session_targets))

        source_rows: dict[str, dict[str, object]] = {}
        source_columns: list[str] = []
        if WINDOWS_STATE_DB.is_file():
            source_uri = f"file:{WINDOWS_STATE_DB.as_posix()}?mode=ro"
            source_connection = sqlite3.connect(source_uri, uri=True, timeout=5)
            source_connection.row_factory = sqlite3.Row
            source_connection.execute("PRAGMA query_only = ON")
            source_connection.execute("BEGIN")
            source_columns = [name for name, _, _ in table_columns(source_connection)]
            if "id" not in source_columns:
                raise sqlite3.DatabaseError("Windows threads table has no id column")
            rejected_rows: list[dict[str, str]] = []
            for row in source_connection.execute("SELECT * FROM threads"):
                thread_id = str(row["id"])
                if thread_id not in session_targets:
                    continue
                source_row = row_dict(row)
                reasons: list[str] = []
                if int(source_row.get("archived") or 0) != 0:
                    reasons.append("archived")
                source_rollout = windows_file_path_to_wsl(str(source_row.get("rollout_path") or ""))
                if source_rollout is None or source_rollout != session_sources[thread_id]:
                    reasons.append("rollout_path_mismatch")
                if reasons:
                    rejected_rows.append({"id": thread_id, "reason": ",".join(reasons)})
                    continue
                source_rows[thread_id] = source_row
            result["source_rows"] = len(source_rows)
            result["source_rejected_row_count"] = len(rejected_rows)
            if rejected_rows:
                result["source_rejected_rows"] = rejected_rows[:10]
            source_connection.rollback()
            source_connection.close()

        missing_source_ids = sorted(set(session_targets) - set(source_rows))
        result["source_missing_row_count"] = len(missing_source_ids)
        if missing_source_ids:
            result["source_missing_ids"] = missing_source_ids[:10]

        updates: list[tuple[str, dict[str, object]]] = []
        inserts: list[dict[str, object]] = []
        for thread_id, source in source_rows.items():
            target = destination_rows.get(thread_id)
            projected_rollout = session_targets[thread_id]
            projected_cwd, _ = windows_cwd_to_wsl(str(source.get("cwd") or ""))
            if target is None:
                inserted = {
                    column: source.get(column)
                    for column in destination_columns
                    if column in source_columns and column in SAFE_INSERT_SOURCE_FIELDS
                }
                inserted["id"] = thread_id
                inserted["rollout_path"] = projected_rollout
                inserted["cwd"] = projected_cwd
                if "sandbox_policy" in destination_columns:
                    inserted["sandbox_policy"] = source.get("sandbox_policy") or SAFE_INSERT_SANDBOX_POLICY
                if "approval_mode" in destination_columns:
                    inserted["approval_mode"] = source.get("approval_mode") or SAFE_INSERT_APPROVAL_MODE
                if "archived" in destination_columns:
                    inserted["archived"] = 0
                if "archived_at" in destination_columns:
                    inserted["archived_at"] = None
                inserts.append(inserted)
                continue

            merged: dict[str, object] = {
                "rollout_path": projected_rollout,
                "cwd": projected_cwd,
            }
            for field in text_fill_fields:
                if field in destination_columns and field in source_columns:
                    merged[field] = target.get(field) or source.get(field)
            for field in max_fields:
                if field in destination_columns and field in source_columns:
                    merged[field] = max_present(target.get(field), source.get(field))
            for field in min_fields:
                if field in destination_columns and field in source_columns:
                    merged[field] = min_present(target.get(field), source.get(field))
            changed_values = {field: value for field, value in merged.items() if target.get(field) != value}
            if changed_values:
                updates.append((thread_id, changed_values))
                result["translated_count"] = int(result["translated_count"]) + int(
                    target.get("cwd") != projected_cwd
                )

        updated_ids = {thread_id for thread_id, _ in updates}
        for thread_id, target in destination_rows.items():
            if thread_id in updated_ids:
                continue
            current_cwd = str(target.get("cwd") or "")
            projected_cwd, _ = windows_cwd_to_wsl(current_cwd)
            if projected_cwd != current_cwd:
                updates.append((thread_id, {"cwd": projected_cwd}))
                result["translated_count"] = int(result["translated_count"]) + 1

        result["metadata_update_count"] = len(updates)
        result["inserted_count"] = len(inserts)
        result["changed"] = bool(updates or inserts)

        if write and (updates or inserts):
            connection.execute("BEGIN IMMEDIATE")
            for thread_id, values in updates:
                assignments = ", ".join(f"{quote_identifier(field)} = ?" for field in values)
                connection.execute(
                    f"UPDATE threads SET {assignments} WHERE id = ?",
                    (*values.values(), thread_id),
                )
            for values in inserts:
                missing_required = [
                    name
                    for name, not_null, default in destination_schema
                    if not_null and default is None and name not in values
                ]
                if missing_required:
                    raise sqlite3.DatabaseError(
                        "Windows threads schema cannot populate WSL required columns: "
                        + ", ".join(missing_required)
                    )
                columns = list(values)
                placeholders = ", ".join("?" for _ in columns)
                connection.execute(
                    f"INSERT INTO threads ({', '.join(quote_identifier(column) for column in columns)}) "
                    f"VALUES ({placeholders})",
                    tuple(values[column] for column in columns),
                )
            connection.commit()
        connection.close()
        result["status"] = "updated" if write and result["changed"] else "would_update" if result["changed"] else "ready"
        if result["source_rejected_row_count"] or result["source_missing_row_count"]:
            result["status"] += "_with_source_gaps"
    except (OSError, sqlite3.Error) as exc:
        result["status"] = "locked_or_unreadable"
        result["error"] = str(exc)
        result["ok"] = False
    return result


def render_config() -> str:
    if not TEMPLATE.is_file():
        raise FileNotFoundError(TEMPLATE)
    if not NODE_WRAPPER.is_file():
        raise FileNotFoundError(NODE_WRAPPER)
    replacements = {
        "__WSL_WORKSPACE_ROOT__": str(ROOT),
        "__WSL_CODEX_HOME__": str(CODEX_HOME),
        "__WSL_NODE_REPL_ENTRY__": str(NODE_ENTRY),
    }
    rendered = TEMPLATE.read_text(encoding="utf-8")
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    if "<SECRET:" in rendered or "C:\\Users\\" in rendered:
        raise ValueError("WSL config contains a secret placeholder or Windows-only path")
    return rendered.rstrip() + "\n"


def materialize(*, write: bool) -> dict[str, object]:
    if write:
        CODEX_HOME.mkdir(parents=True, exist_ok=True)
        (CODEX_HOME / "sqlite").mkdir(parents=True, exist_ok=True)
    config = CODEX_HOME / "config.toml"
    rendered = render_config()
    current = config.read_text(encoding="utf-8") if config.is_file() else ""
    profile_current = PROFILE_PATH.read_text(encoding="utf-8") if PROFILE_PATH.is_file() else ""
    profile_rendered = render_profile(profile_current)
    config_changed = current != rendered
    profile_changed = profile_current != profile_rendered
    changed = config_changed or profile_changed
    links = []
    links.append(
        link_or_verify(NODE_WRAPPER, NODE_ENTRY)
        if write
        else managed_link_status(NODE_WRAPPER, NODE_ENTRY)
    )
    for name in ("AGENTS.md", "MEMORY.md", "USER_WORKING_PREFERENCES.md", "skills", "scripts", "tools", "automations"):
        source = ROOT / "codex-home" / name
        target = CODEX_HOME / name
        if source.exists():
            if name == "skills":
                links.append(link_skill_tree(source, target, write=write))
            else:
                links.append(link_or_verify(source, target) if write else managed_link_status(source, target))
    session_projection = project_sessions(write=write)
    state_projection = project_state_db(write=write)
    required_link_ok = bool(links and links[0].get("ok", True))
    state_complete = bool(
        state_projection.get("ok", True)
        and not state_projection.get("source_rejected_row_count")
        and not state_projection.get("source_missing_row_count")
    )
    degraded = bool(
        not session_projection.get("ok", True)
        or not state_complete
        or any(link.get("ok") is False for link in links[1:])
    )
    changed = changed or bool(session_projection.get("changed")) or bool(state_projection.get("changed"))
    if write and config_changed:
        config.write_text(rendered, encoding="utf-8", newline="\n")
    if write and profile_changed:
        PROFILE_PATH.write_text(profile_rendered, encoding="utf-8", newline="\n")
    return {
        "schema": "codex-wsl-runtime.v1",
        "ok": required_link_ok,
        "degraded": degraded,
        "generated_at": now_iso(),
        "write": write,
        "changed": changed,
        "root": str(ROOT),
        "codex_home": str(CODEX_HOME),
        "config": str(config),
        "config_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "desktop_profile": str(PROFILE_PATH),
        "desktop_profile_changed": profile_changed,
        "desktop_profile_sha256": hashlib.sha256(profile_rendered.encode("utf-8")).hexdigest(),
        "links": links,
        "session_projection": session_projection,
        "state_projection": state_projection,
        "secrets_imported": False,
        "windows_runtime_imported": False,
        "session_state_imported": state_complete,
        "session_continuity": "isolated_wsl_session_projection",
    }


def validate() -> dict[str, object]:
    result = materialize(write=False)
    config = CODEX_HOME / "config.toml"
    result["config_exists"] = config.is_file()
    result["config_matches_template"] = bool(config.is_file() and sha256(config) == result["config_sha256"])
    result["desktop_profile_current"] = bool(
        PROFILE_PATH.is_file()
        and sha256(PROFILE_PATH) == result["desktop_profile_sha256"]
    )
    result["node_wrapper_exists"] = NODE_WRAPPER.is_file()
    result["node_entry_ok"] = bool(
        NODE_ENTRY.is_symlink()
        and NODE_ENTRY.resolve() == NODE_WRAPPER.resolve()
        and os.access(NODE_ENTRY, os.X_OK)
    )
    result["node_repl_exists"] = Path("/mnt/c/Users/45543/.local/bin/node_repl.exe").is_file()
    session_projection = result.get("session_projection") or {}
    state_projection = result.get("state_projection") or {}
    result["session_continuity_ok"] = bool(
        session_projection.get("status") in {"projected", "source_missing_optional"}
        and session_projection.get("source_count") == session_projection.get("projected_count")
    )
    result["state_projection_ok"] = state_projection.get("status") in {"ready", "missing_optional"}
    result["required"] = [
        "config_exists",
        "config_matches_template",
        "desktop_profile_current",
        "node_wrapper_exists",
        "node_entry_ok",
        "node_repl_exists",
        "session_continuity_ok",
        "state_projection_ok",
    ]
    result["ok"] = all(bool(result[key]) for key in result["required"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize the WSL Codex runtime projection")
    parser.add_argument("command", choices=("plan", "apply", "validate"))
    args = parser.parse_args(argv)
    if args.command == "plan":
        payload = materialize(write=False)
    elif args.command == "apply":
        payload = materialize(write=True)
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
