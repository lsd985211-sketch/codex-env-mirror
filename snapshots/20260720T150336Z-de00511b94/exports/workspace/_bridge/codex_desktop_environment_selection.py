#!/usr/bin/env python3
"""Reconcile the Codex Desktop environment selection across host and WSL.

The Desktop host and the WSL app-server read different config files.  This
owner keeps only the environment switch synchronized and uses a three-way
state value so a deliberate change on either side is not overwritten by the
other side during the next launch.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import fcntl

from shared.backup_router import create_backup


ENVIRONMENT_KEY = "runCodexInWindowsSubsystemForLinux"
STATE_SCHEMA = "codex-desktop-environment-selection.v1"
DEFAULT_HOST_CONFIG = Path("/mnt/c/Users/45543/.codex/config.toml")
DEFAULT_WSL_CONFIG = Path.home() / ".codex-app" / "config.toml"
DEFAULT_STATE_PATH = Path.home() / ".codex-app" / "state" / "desktop-environment-selection.json"
LOCK_TIMEOUT_SECONDS = 10.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, existing_mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _table_bounds(lines: list[str], table: str) -> tuple[int | None, int | None]:
    header = f"[{table}]"
    start = next((index for index, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        return None, None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    return start, end


def extract_table(text: str, table: str) -> str:
    lines = text.splitlines()
    start, end = _table_bounds(lines, table)
    if start is None or end is None:
        return ""
    return "\n".join(lines[start:end]).rstrip() + "\n"


def _toml_bool(config: dict[str, object], table: str, key: str) -> bool | None:
    value = config.get(table)
    if not isinstance(value, dict) or key not in value:
        return None
    raw = value[key]
    if not isinstance(raw, bool):
        raise ValueError(f"{table}.{key} must be a boolean")
    return raw


def set_table_bool(text: str, table: str, key: str, value: bool) -> str:
    assignment = f"{key} = {'true' if value else 'false'}"
    lines = text.splitlines()
    start, end = _table_bounds(lines, table)
    if start is None or end is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend((f"[{table}]", assignment))
    else:
        for index in range(start + 1, end):
            stripped = lines[index].strip()
            if stripped.startswith(f"{key} ") or stripped.startswith(f"{key}="):
                lines[index] = assignment
                break
        else:
            lines.insert(end, assignment)
    rendered = "\n".join(lines).rstrip() + "\n"
    tomllib.loads(rendered)
    return rendered


def _replace_table(text: str, table: str, replacement: str) -> str:
    lines = text.splitlines()
    start, end = _table_bounds(lines, table)
    replacement_lines = replacement.rstrip().splitlines()
    if start is None or end is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(replacement_lines)
    else:
        lines[start:end] = replacement_lines
    rendered = "\n".join(lines).rstrip() + "\n"
    tomllib.loads(rendered)
    return rendered


def _load_state(path: Path) -> bool | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("schema") != STATE_SCHEMA or not isinstance(payload.get("last_synced_value"), bool):
        return None
    return bool(payload["last_synced_value"])


def _select_value(host: bool, wsl: bool | None, last: bool | None) -> tuple[bool, str]:
    if last is None:
        return host, "host_initial"
    if wsl is None:
        return host, "host_missing_wsl_value"
    if host == wsl:
        return host, "already_synchronized"
    host_changed = host != last
    wsl_changed = wsl != last
    if host_changed and not wsl_changed:
        return host, "host_changed"
    if wsl_changed and not host_changed:
        return wsl, "wsl_changed"
    return host, "host_conflict_winner"


def _reconcile_environment_selection_unlocked(
    *,
    host_config: Path,
    wsl_config: Path,
    state_path: Path,
    write: bool,
    requested_value: bool | None = None,
    backup_creator: Callable[..., dict[str, object]] = create_backup,
    writer: Callable[[Path, str], None] = atomic_write_text,
) -> dict[str, object]:
    paths = (host_config, wsl_config, state_path)
    symlinks = [str(path) for path in paths if path.is_symlink()]
    if symlinks:
        return {"ok": False, "status": "symlink_rejected", "paths": symlinks, "changed": False}
    try:
        host_bytes = host_config.read_bytes()
        wsl_bytes = wsl_config.read_bytes() if wsl_config.is_file() else None
        host_text = host_bytes.decode("utf-8")
        wsl_text = wsl_bytes.decode("utf-8") if wsl_bytes is not None else ""
        host_data = tomllib.loads(host_text)
        wsl_data = tomllib.loads(wsl_text) if wsl_text else {}
        host_value = _toml_bool(host_data, "desktop", ENVIRONMENT_KEY)
        wsl_value = _toml_bool(wsl_data, "desktop", ENVIRONMENT_KEY)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError) as exc:
        return {"ok": False, "status": "config_unreadable", "error": str(exc), "changed": False}

    # The native Desktop default is Windows when the host key is absent.
    effective_host = bool(host_value) if host_value is not None else False
    last = _load_state(state_path)
    selected, source = (requested_value, "explicit_selection") if requested_value is not None else _select_value(effective_host, wsl_value, last)
    next_host = set_table_bool(host_text, "desktop", ENVIRONMENT_KEY, selected)
    host_desktop = extract_table(next_host, "desktop")
    if not extract_table(wsl_text, "desktop"):
        next_wsl = _replace_table(wsl_text, "desktop", host_desktop)
    else:
        next_wsl = set_table_bool(wsl_text, "desktop", ENVIRONMENT_KEY, selected)
    state_text = json.dumps(
        {
            "schema": STATE_SCHEMA,
            "last_synced_value": selected,
            "selection_source": source,
            "synchronized_at": now_iso(),
            "host_config": str(host_config),
            "wsl_config": str(wsl_config),
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    config_changes = {
        "host": next_host.encode("utf-8") != host_bytes,
        "wsl": wsl_bytes is None or next_wsl.encode("utf-8") != wsl_bytes,
    }
    state_changed = not state_path.is_file() or _load_state(state_path) != selected
    changed = any(config_changes.values()) or state_changed
    result: dict[str, object] = {
        "schema": STATE_SCHEMA,
        "ok": True,
        "status": "would_apply" if changed else "already_current",
        "write": write,
        "changed": changed,
        "config_changes": config_changes,
        "state_changed": state_changed,
        "host_value": effective_host,
        "wsl_value": wsl_value,
        "last_synced_value": last,
        "selected_value": selected,
        "selection_source": source,
    }
    if not write or not changed:
        return result

    backup_paths = [
        str(path)
        for path, required in ((wsl_config, config_changes["wsl"]), (host_config, config_changes["host"]))
        if required and path.is_file()
    ]
    if backup_paths:
        try:
            backup = backup_creator(
                backup_paths,
                remark="codex-desktop-environment-selection",
                purpose="Atomic host and WSL Desktop environment selection reconciliation",
                category="wsl-desktop-runtime",
            )
        except Exception as exc:
            backup = {"ok": False, "error": repr(exc)}
        if not backup.get("ok"):
            return {**result, "ok": False, "status": "backup_failed", "backup": backup}
        result["backup"] = backup

    current_host = host_config.read_bytes() if host_config.is_file() else None
    current_wsl = wsl_config.read_bytes() if wsl_config.is_file() else None
    if current_host != host_bytes or current_wsl != wsl_bytes:
        return {
            **result,
            "ok": False,
            "status": "source_changed_during_reconcile",
            "source_changes": {
                "host": current_host != host_bytes,
                "wsl": current_wsl != wsl_bytes,
            },
        }

    before_host = host_text
    before_wsl = wsl_text if wsl_bytes is not None else None
    before_state = state_path.read_text(encoding="utf-8") if state_path.is_file() else None
    written: list[str] = []
    try:
        if config_changes["wsl"]:
            writer(wsl_config, next_wsl)
            written.append("wsl")
        if config_changes["host"]:
            writer(host_config, next_host)
            written.append("host")
        if state_changed:
            writer(state_path, state_text)
            written.append("state")
    except Exception as exc:
        rollback_errors: list[str] = []
        for name, path, previous, required in (
            ("state", state_path, before_state, state_changed),
            ("host", host_config, before_host, config_changes["host"]),
            ("wsl", wsl_config, before_wsl, config_changes["wsl"]),
        ):
            if not required:
                continue
            try:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_text(path, previous)
            except Exception as rollback_exc:
                rollback_errors.append(f"{name}:{rollback_exc!r}")
        status = "write_failed_rolled_back" if not rollback_errors else "write_failed_rollback_incomplete"
        return {
            **result,
            "ok": False,
            "status": status,
            "error": repr(exc),
            "written_before_failure": written,
            "rollback_errors": rollback_errors,
        }
    return {**result, "status": "applied", "written": written}


def reconcile_environment_selection(
    *,
    host_config: Path,
    wsl_config: Path,
    state_path: Path,
    write: bool,
    requested_value: bool | None = None,
    backup_creator: Callable[..., dict[str, object]] = create_backup,
    writer: Callable[[Path, str], None] = atomic_write_text,
) -> dict[str, object]:
    if not write:
        return _reconcile_environment_selection_unlocked(
            host_config=host_config,
            wsl_config=wsl_config,
            state_path=state_path,
            write=False,
            requested_value=requested_value,
            backup_creator=backup_creator,
            writer=writer,
        )
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return {
                        "ok": False,
                        "status": "selection_operation_busy",
                        "changed": False,
                        "lock_path": str(lock_path),
                    }
                time.sleep(0.05)
        try:
            return _reconcile_environment_selection_unlocked(
                host_config=host_config,
                wsl_config=wsl_config,
                state_path=state_path,
                write=True,
                requested_value=requested_value,
                backup_creator=backup_creator,
                writer=writer,
            )
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile Codex Desktop host and WSL environment selection")
    parser.add_argument("command", choices=("plan", "apply", "validate"))
    parser.add_argument("--host-config", type=Path, default=DEFAULT_HOST_CONFIG)
    parser.add_argument("--wsl-config", type=Path, default=DEFAULT_WSL_CONFIG)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--select", choices=("wsl", "windows"), default="")
    args = parser.parse_args(argv)
    payload = reconcile_environment_selection(
        host_config=args.host_config,
        wsl_config=args.wsl_config,
        state_path=args.state_path,
        write=args.command == "apply",
        requested_value={"wsl": True, "windows": False}.get(args.select),
    )
    if args.command == "validate" and payload.get("changed"):
        payload = {**payload, "ok": False, "status": "drift_detected"}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
