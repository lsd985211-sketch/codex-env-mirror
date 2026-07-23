#!/usr/bin/env python3
"""Shared user-systemd unit installation primitives for WSL services.

Ownership: atomic user-unit writes, pre-edit backup routing, and bounded
``systemctl --user`` execution.
Non-goals: service-specific commands, ports, permissions, health acceptance,
or Windows scheduled-task lifecycle.
State behavior: status calls are read-only; install writes one declared unit,
reloads the user manager, and enables/starts only that unit.
Caller context: narrow service owners such as the Codex app-server and local
MCP Hub retain their own confirmation, unit content, and validation contracts.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    from shared.backup_router import create_backup
except ModuleNotFoundError:  # Direct execution from the shared module directory.
    from backup_router import create_backup


def run(argv: list[str], *, timeout: int = 30) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def systemctl(*args: str, timeout: int = 30) -> dict[str, Any]:
    return run(["systemctl", "--user", *args], timeout=timeout)


def unit_status(service_name: str, path: Path) -> dict[str, Any]:
    show = systemctl(
        "show",
        service_name,
        "--property=LoadState,ActiveState,SubState,UnitFileState,ExecMainPID,Result",
    )
    enabled = systemctl("is-enabled", service_name)
    active = systemctl("is-active", service_name)
    values: dict[str, str] = {}
    for line in str(show.get("stdout") or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {
        "ok": bool(path.is_file() and active.get("ok") and values.get("ActiveState") == "active"),
        "service": service_name,
        "unit_path": str(path),
        "unit_exists": path.is_file(),
        "enabled": str(enabled.get("stdout") or "") == "enabled",
        "active": str(active.get("stdout") or "") == "active",
        "systemd": values,
    }


def install_user_unit(
    *,
    service_name: str,
    path: Path,
    content: str,
    backup_category: str,
    backup_purpose: str,
    backup_remark: str,
    backup_trigger: str,
    timeout: int = 60,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = (
        create_backup(
            [str(path)],
            category=backup_category,
            purpose=backup_purpose,
            remark=backup_remark,
            trigger=backup_trigger,
        )
        if path.exists()
        else {"ok": True, "skipped": "unit_absent"}
    )
    if not backup.get("ok"):
        return {"ok": False, "reason": "backup_failed", "backup": backup}

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

    reload_result = systemctl("daemon-reload")
    enable_result = systemctl("enable", "--now", service_name, timeout=timeout)
    return {
        "ok": bool(reload_result.get("ok") and enable_result.get("ok")),
        "backup": backup,
        "reload": reload_result,
        "enable_start": enable_result,
        "unit_path": str(path),
        "service": service_name,
    }
