#!/usr/bin/env python3
"""Reconcile the Codex Desktop environment selection across host and WSL.

The Desktop host and the WSL app-server read different config files.  This
owner keeps only the environment switch synchronized and uses a three-way
state value so a deliberate change on either side is not overwritten by the
other side during the next launch. Requests made while Desktop is running are
recorded as pending intent and consumed only at a clean startup boundary.
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
DEFAULT_HOST_STATE_PATH = Path("/mnt/c/Users/45543/.codex/state/desktop-environment-selection.json")
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


def _load_state_payload(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("schema") != STATE_SCHEMA or not isinstance(payload.get("last_synced_value"), bool):
        return None
    return payload


def _load_state(path: Path) -> bool | None:
    payload = _load_state_payload(path)
    return bool(payload["last_synced_value"]) if payload is not None else None


def _state_is_current(path: Path, selected: bool) -> bool:
    payload = _load_state_payload(path)
    return bool(
        payload is not None
        and payload.get("last_synced_value") is selected
        and payload.get("desired_value") is selected
        and payload.get("effective_value") is selected
        and payload.get("fallback_pending") is False
        and payload.get("selection_pending") is not True
    )


def _select_value(
    host: bool | None,
    wsl: bool | None,
    last: bool | None,
    *,
    state_payload: dict[str, object] | None = None,
) -> tuple[bool, str]:
    if state_payload is not None and state_payload.get("selection_pending") is True:
        desired = state_payload.get("desired_value")
        if isinstance(desired, bool):
            return desired, "pending_selection"
    # A failed WSL launch may leave the host projection at false for this
    # launch.  Do not reinterpret that runtime fallback as a new user choice.
    if (
        state_payload is not None
        and state_payload.get("fallback_pending") is True
        and state_payload.get("last_synced_value") is True
        and host is False
    ):
        return True, "fallback_recovery"
    # Provider-owned config rebuilds may omit this runtime-local key. Absence
    # is not an explicit Windows selection: preserve the last synchronized
    # value unless the WSL side itself changed.
    if host is None:
        if last is None:
            return False, "host_missing_initial"
        if wsl is None:
            return last, "host_missing_state_recovery"
        if wsl != last:
            return wsl, "wsl_changed"
        return last, "host_missing_recovery"
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
    host_state_path: Path | None = None,
    write: bool,
    requested_value: bool | None = None,
    request_only: bool = False,
    backup_creator: Callable[..., dict[str, object]] = create_backup,
    writer: Callable[[Path, str], None] = atomic_write_text,
) -> dict[str, object]:
    paths = tuple(dict.fromkeys((host_config, wsl_config, state_path, host_state_path or state_path)))
    symlinks = [str(path) for path in paths if path.is_symlink()]
    if symlinks:
        return {"ok": False, "status": "symlink_rejected", "paths": symlinks, "changed": False}
    try:
        host_bytes = host_config.read_bytes()
        wsl_bytes = wsl_config.read_bytes() if wsl_config.is_file() else None
        state_bytes = state_path.read_bytes() if state_path.is_file() else None
        host_state_bytes = (
            host_state_path.read_bytes()
            if host_state_path is not None and host_state_path.is_file()
            else None
        )
        host_text = host_bytes.decode("utf-8")
        wsl_text = wsl_bytes.decode("utf-8") if wsl_bytes is not None else ""
        before_state = state_bytes.decode("utf-8") if state_bytes is not None else None
        before_host_state = host_state_bytes.decode("utf-8") if host_state_bytes is not None else None
        host_data = tomllib.loads(host_text)
        wsl_data = tomllib.loads(wsl_text) if wsl_text else {}
        host_value = _toml_bool(host_data, "desktop", ENVIRONMENT_KEY)
        wsl_value = _toml_bool(wsl_data, "desktop", ENVIRONMENT_KEY)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError) as exc:
        return {"ok": False, "status": "config_unreadable", "error": str(exc), "changed": False}

    # The native Desktop default is Windows when the host key is absent.
    effective_host = bool(host_value) if host_value is not None else False
    state_payload = _load_state_payload(host_state_path) if host_state_path is not None else None
    if state_payload is None:
        state_payload = _load_state_payload(state_path)
    last = bool(state_payload["last_synced_value"]) if state_payload is not None else None
    selected, source = (
        (requested_value, "explicit_selection")
        if requested_value is not None
        else _select_value(host_value, wsl_value, last, state_payload=state_payload)
    )
    if request_only and requested_value is None:
        return {"ok": False, "status": "selection_required", "changed": False}
    next_host = host_text if request_only else set_table_bool(host_text, "desktop", ENVIRONMENT_KEY, selected)
    host_desktop = extract_table(next_host, "desktop")
    if request_only:
        next_wsl = wsl_text
    elif not extract_table(wsl_text, "desktop"):
        next_wsl = _replace_table(wsl_text, "desktop", host_desktop)
    else:
        next_wsl = set_table_bool(wsl_text, "desktop", ENVIRONMENT_KEY, selected)
    state_last = effective_host if request_only else selected
    state_effective = effective_host if request_only else selected
    state_source = "explicit_restart_request" if request_only else source
    state_text = json.dumps(
        {
            "schema": STATE_SCHEMA,
            "last_synced_value": state_last,
            "desired_value": selected,
            "effective_value": state_effective,
            "fallback_pending": False,
            "selection_pending": request_only,
            "selection_source": state_source,
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
    def state_matches(path: Path) -> bool:
        payload = _load_state_payload(path)
        if request_only:
            return bool(
                payload is not None
                and payload.get("last_synced_value") is state_last
                and payload.get("desired_value") is selected
                and payload.get("effective_value") is state_effective
                and payload.get("fallback_pending") is False
                and payload.get("selection_pending") is True
            )
        return _state_is_current(path, selected)

    state_changed = not state_matches(state_path)
    host_state_changed = bool(
        host_state_path is not None
        and not state_matches(host_state_path)
    )
    changed = any(config_changes.values()) or state_changed or host_state_changed
    result: dict[str, object] = {
        "schema": STATE_SCHEMA,
        "ok": True,
        "status": "would_apply" if changed else "already_current",
        "write": write,
        "changed": changed,
        "config_changes": config_changes,
        "state_changed": state_changed,
        "host_state_changed": host_state_changed,
        "host_value": effective_host,
        "host_config_value": host_value,
        "wsl_value": wsl_value,
        "last_synced_value": last,
        "selected_value": state_effective if request_only else selected,
        "desired_value": selected,
        "effective_value": state_effective,
        "fallback_pending": False,
        "selection_pending": request_only,
        "selection_source": state_source,
    }
    if not write or not changed:
        return result

    backup_paths = [
        str(path)
        for path, required in (
            (wsl_config, config_changes["wsl"]),
            (host_config, config_changes["host"]),
            (state_path, state_changed),
            (host_state_path, host_state_changed),
        )
        if required and path is not None and path.is_file()
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
    current_state = state_path.read_bytes() if state_path.is_file() else None
    current_host_state = (
        host_state_path.read_bytes()
        if host_state_path is not None and host_state_path.is_file()
        else None
    )
    if (
        current_host != host_bytes
        or current_wsl != wsl_bytes
        or current_state != state_bytes
        or current_host_state != host_state_bytes
    ):
        return {
            **result,
            "ok": False,
            "status": "source_changed_during_reconcile",
            "source_changes": {
                "host": current_host != host_bytes,
                "wsl": current_wsl != wsl_bytes,
                "state": current_state != state_bytes,
                "host_state": current_host_state != host_state_bytes,
            },
        }

    before_host = host_text
    before_wsl = wsl_text if wsl_bytes is not None else None
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
        if host_state_changed and host_state_path is not None:
            writer(host_state_path, state_text)
            written.append("host_state")
    except Exception as exc:
        rollback_errors: list[str] = []
        for name, path, previous, required in (
            ("host_state", host_state_path, before_host_state, host_state_changed),
            ("state", state_path, before_state, state_changed),
            ("host", host_config, before_host, config_changes["host"]),
            ("wsl", wsl_config, before_wsl, config_changes["wsl"]),
        ):
            if not required:
                continue
            if path is None:
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
    host_state_path: Path | None = None,
    write: bool,
    requested_value: bool | None = None,
    request_only: bool = False,
    backup_creator: Callable[..., dict[str, object]] = create_backup,
    writer: Callable[[Path, str], None] = atomic_write_text,
) -> dict[str, object]:
    if not write:
        return _reconcile_environment_selection_unlocked(
            host_config=host_config,
            wsl_config=wsl_config,
            state_path=state_path,
            host_state_path=host_state_path,
            write=False,
            requested_value=requested_value,
            request_only=request_only,
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
                host_state_path=host_state_path,
                write=True,
                requested_value=requested_value,
                request_only=request_only,
                backup_creator=backup_creator,
                writer=writer,
            )
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile Codex Desktop host and WSL environment selection")
    parser.add_argument("command", choices=("plan", "apply", "request", "validate"))
    parser.add_argument("--host-config", type=Path, default=DEFAULT_HOST_CONFIG)
    parser.add_argument("--wsl-config", type=Path, default=DEFAULT_WSL_CONFIG)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--host-state-path", type=Path, default=DEFAULT_HOST_STATE_PATH)
    parser.add_argument("--select", choices=("wsl", "windows"), default="")
    args = parser.parse_args(argv)
    if args.command == "request" and not args.select:
        parser.error("request requires --select wsl or --select windows")
    payload = reconcile_environment_selection(
        host_config=args.host_config,
        wsl_config=args.wsl_config,
        state_path=args.state_path,
        host_state_path=args.host_state_path,
        write=args.command in {"apply", "request"},
        requested_value={"wsl": True, "windows": False}.get(args.select),
        request_only=args.command == "request",
    )
    if args.command == "validate" and payload.get("changed"):
        payload = {**payload, "ok": False, "status": "drift_detected"}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
