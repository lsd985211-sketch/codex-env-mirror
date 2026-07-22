#!/usr/bin/env python3
"""Persistent WSLInterop repair owner.

Ownership: declarative guard files, target-distribution installation, timer
state, backup-before-write, and post-install readback.
Non-goals: identifying an unlogged historical binfmt writer, changing the
default WSL distribution, or managing Hub/MCP processes.
State behavior: read-only state and plan functions; apply requires an exact
confirmation and writes only the declared root-owned guard files.
Caller context: wsl_workspace_owner is the public lifecycle facade.
"""

from __future__ import annotations

import base64
import hashlib
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from shared.backup_router import create_backup


SCHEMA = "wsl_interop_guard.v1"
INSTALL_CONFIRM = "INSTALL-WSL-INTEROP-GUARD"
INTEROP_ENTRY = Path("/proc/sys/fs/binfmt_misc/WSLInterop")
INIT_PATH = Path("/init")
WSL_EXE = Path("/mnt/c/Windows/System32/wsl.exe")
CMD_EXE = Path("/mnt/c/Windows/System32/cmd.exe")
WINDOWS_WSL_EXE = r"C:\Windows\System32\wsl.exe"
MANAGED_PATHS = {
    "script": Path("/usr/local/sbin/codex-wsl-interop-guard"),
    "service": Path("/etc/systemd/system/codex-wsl-interop-guard.service"),
    "timer": Path("/etc/systemd/system/codex-wsl-interop-guard.timer"),
    "binfmt_dropin": Path("/etc/systemd/system/systemd-binfmt.service.d/10-codex-wsl-interop.conf"),
}
MANAGED_CONTENTS = {
    "script": """#!/bin/sh
set -eu

ENTRY=/proc/sys/fs/binfmt_misc/WSLInterop
REGISTER=/proc/sys/fs/binfmt_misc/register
MAGIC=':WSLInterop:M::MZ::/init:P'

if [ -e "$ENTRY" ]; then
  exit 0
fi

i=0
while [ "$i" -lt 20 ]; do
  if [ -w "$REGISTER" ]; then
    printf '%s\\n' "$MAGIC" > "$REGISTER" 2>/dev/null || true
  fi
  if [ -e "$ENTRY" ]; then
    logger -t codex-wsl-interop-guard 'restored WSLInterop registration'
    exit 0
  fi
  i=$((i + 1))
  sleep 0.25
done

printf '%s\\n' 'WSLInterop registration is unavailable after bounded retry' >&2
exit 1
""",
    "service": """[Unit]
Description=Restore WSL Windows executable interop for Codex
After=systemd-binfmt.service
Wants=systemd-binfmt.service
ConditionPathIsMountPoint=/proc/sys/fs/binfmt_misc
ConditionPathExists=/init

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/codex-wsl-interop-guard
""",
    "timer": """[Unit]
Description=Continuously verify WSL Windows executable interop for Codex

[Timer]
OnBootSec=30s
OnUnitInactiveSec=30s
AccuracySec=5s
Unit=codex-wsl-interop-guard.service

[Install]
WantedBy=timers.target
""",
    "binfmt_dropin": """[Service]
ExecStartPost=/usr/local/sbin/codex-wsl-interop-guard
""",
}


def _inside_wsl() -> bool:
    return os.name != "nt" and bool(
        os.environ.get("WSL_DISTRO_NAME") or "microsoft" in platform.release().lower()
    )


def _run(argv: list[str], *, timeout: int = 30, cwd: Path | None = None) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, timeout),
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
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
        "stdout": completed.stdout.strip()[:8000],
        "stderr": completed.stderr.strip()[:4000],
    }


def _wsl_command(arguments: list[str], *, tolerate_missing_interop: bool) -> list[str]:
    if _inside_wsl():
        if not WSL_EXE.is_file():
            return []
        if tolerate_missing_interop and not INTEROP_ENTRY.exists():
            if not INIT_PATH.is_file() or not CMD_EXE.is_file():
                return []
            command_line = subprocess.list2cmdline([WINDOWS_WSL_EXE, *arguments])
            return [str(INIT_PATH), str(CMD_EXE), "/d", "/s", "/c", command_line]
        return [str(WSL_EXE), *arguments]
    executable = shutil.which("wsl.exe")
    return [executable, *arguments] if executable else []


def _target_run(argv: list[str], distribution: str, user: str, *, timeout: int = 30) -> dict[str, Any]:
    current_distribution = os.environ.get("WSL_DISTRO_NAME") or distribution
    if _inside_wsl() and current_distribution == distribution:
        return _run(argv, timeout=timeout)
    command = _wsl_command(
        ["-d", distribution, "-u", user, "--", *argv],
        tolerate_missing_interop=False,
    )
    if not command:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "wsl_executable_missing"}
    return _run(command, timeout=timeout)


def _root_run_script(script: str, distribution: str, *, timeout: int = 90) -> dict[str, Any]:
    if _inside_wsl() and os.geteuid() == 0 and (os.environ.get("WSL_DISTRO_NAME") or distribution) == distribution:
        return _run(["sh", "-lc", script], timeout=timeout)
    visible_script, linux_script = _write_target_script(script, distribution)
    try:
        command = _wsl_command(
            ["-d", distribution, "-u", "root", "--", "sh", linux_script],
            tolerate_missing_interop=True,
        )
        if not command:
            return {"ok": False, "returncode": None, "stdout": "", "stderr": "wsl_root_transport_unavailable"}
        command_cwd = Path("/mnt/c/Windows") if command[:2] == [str(INIT_PATH), str(CMD_EXE)] else None
        return _run(command, timeout=timeout, cwd=command_cwd)
    finally:
        try:
            visible_script.unlink()
        except OSError:
            pass


def _parse_properties(text: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def _target_file_text(path: Path, distribution: str, user: str) -> str:
    current_distribution = os.environ.get("WSL_DISTRO_NAME") or distribution
    if _inside_wsl() and current_distribution == distribution:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""
    result = _target_run(["cat", str(path)], distribution, user, timeout=10)
    return str(result.get("stdout") or "") + ("\n" if result.get("ok") else "")


def _visible_backup_path(path: Path, distribution: str) -> Path:
    if _inside_wsl():
        return path
    windows_tail = str(path).lstrip("/").replace("/", "\\")
    return Path(rf"\\wsl.localhost\{distribution}\{windows_tail}")


def _write_target_script(script: str, distribution: str) -> tuple[Path, str]:
    name = f"codex-wsl-interop-guard-{os.getpid()}-{time.time_ns()}.sh"
    linux_path = f"/tmp/{name}"
    visible_path = Path(linux_path) if _inside_wsl() else _visible_backup_path(Path(linux_path), distribution)
    descriptor = os.open(visible_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(script)
        if not script.endswith("\n"):
            handle.write("\n")
    return visible_path, linux_path


def state(distribution: str, user: str) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for key, path in MANAGED_PATHS.items():
        expected = MANAGED_CONTENTS[key]
        actual = _target_file_text(path, distribution, user)
        files.append(
            {
                "key": key,
                "path": str(path),
                "exists": bool(actual),
                "current": actual == expected,
                "expected_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
                "actual_sha256": hashlib.sha256(actual.encode("utf-8")).hexdigest() if actual else "",
            }
        )

    timer_probe = _target_run(
        [
            "systemctl",
            "show",
            "codex-wsl-interop-guard.timer",
            "--property=LoadState,ActiveState,SubState,UnitFileState,NextElapseUSecRealtime",
        ],
        distribution,
        user,
        timeout=15,
    )
    service_probe = _target_run(
        [
            "systemctl",
            "show",
            "codex-wsl-interop-guard.service",
            "--property=LoadState,ActiveState,SubState,UnitFileState,Result",
        ],
        distribution,
        user,
        timeout=15,
    )
    timer = _parse_properties(str(timer_probe.get("stdout") or ""))
    service = _parse_properties(str(service_probe.get("stdout") or ""))
    files_current = all(row["current"] for row in files)
    timer_enabled = timer.get("UnitFileState") == "enabled"
    timer_active = timer.get("ActiveState") == "active"
    return {
        "schema": f"{SCHEMA}.state",
        "ok": bool(timer_probe.get("ok") and service_probe.get("ok")),
        "distribution": distribution,
        "files": files,
        "files_current": files_current,
        "timer": timer,
        "service": service,
        "timer_enabled": timer_enabled,
        "timer_active": timer_active,
        "ready": bool(files_current and timer_enabled and timer_active),
        "repair_required": not (files_current and timer_enabled and timer_active),
        "root_transport": "wsl_root" if _wsl_command([], tolerate_missing_interop=True) else "unavailable",
    }


def plan(distribution: str, user: str) -> dict[str, Any]:
    current = state(distribution, user)
    blockers: list[dict[str, str]] = []
    if current.get("root_transport") == "unavailable" and not (_inside_wsl() and os.geteuid() == 0):
        blockers.append({"code": "wsl_root_transport_unavailable"})
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": not blockers,
        "read_only": True,
        "would_change": bool(current.get("repair_required")),
        "confirmation": INSTALL_CONFIRM,
        "blockers": blockers,
        "steps": [
            "route backups for every existing managed file",
            "atomically install the guard script, service, timer, and systemd-binfmt drop-in",
            "disable the obsolete boot-only service enablement",
            "daemon-reload, enable and start the timer, and run an immediate repair",
            "read back exact file hashes and timer state",
        ],
        "state": current,
    }


def _install_script() -> str:
    commands = [
        "set -eu",
        "install_payload() {",
        "  destination=$1; mode=$2; payload=$3",
        "  directory=$(dirname \"$destination\")",
        "  temporary=\"${destination}.tmp.$$\"",
        "  mkdir -p \"$directory\"",
        "  trap 'rm -f \"$temporary\"' EXIT HUP INT TERM",
        "  printf '%s' \"$payload\" | base64 -d > \"$temporary\"",
        "  chown root:root \"$temporary\"",
        "  chmod \"$mode\" \"$temporary\"",
        "  mv -f \"$temporary\" \"$destination\"",
        "  trap - EXIT HUP INT TERM",
        "}",
    ]
    for key, path in MANAGED_PATHS.items():
        encoded = base64.b64encode(MANAGED_CONTENTS[key].encode("utf-8")).decode("ascii")
        mode = "0755" if key == "script" else "0644"
        commands.append(f"install_payload '{path}' '{mode}' '{encoded}'")
    commands.extend(
        [
            "systemctl daemon-reload",
            "systemctl disable codex-wsl-interop-guard.service >/dev/null 2>&1 || true",
            "systemctl enable --now codex-wsl-interop-guard.timer",
            "systemctl restart codex-wsl-interop-guard.service",
        ]
    )
    return "\n".join(commands)


def apply(confirm: str, distribution: str, user: str, *, timeout: int = 90) -> dict[str, Any]:
    planned = plan(distribution, user)
    if confirm != INSTALL_CONFIRM:
        return {
            "schema": f"{SCHEMA}.apply",
            "ok": False,
            "status": "blocked",
            "reason": f"pass --confirm {INSTALL_CONFIRM}",
            "plan": planned,
        }
    if not planned.get("ok"):
        return {
            "schema": f"{SCHEMA}.apply",
            "ok": False,
            "status": "blocked",
            "reason": "interop_guard_plan_blocked",
            "plan": planned,
        }

    existing = [
        str(_visible_backup_path(Path(row["path"]), distribution))
        for row in planned["state"]["files"]
        if row.get("exists")
    ]
    backup = (
        create_backup(
            existing,
            remark="wsl-interop-guard-install",
            purpose="Before replacing root-owned WSLInterop guard files",
            category="wsl-workspace",
            trigger="wsl_workspace_owner",
        )
        if existing
        else {"ok": True, "created_count": 0, "reason": "no_existing_managed_files"}
    )
    if not backup.get("ok"):
        return {
            "schema": f"{SCHEMA}.apply",
            "ok": False,
            "status": "blocked",
            "reason": "backup_failed",
            "backup": backup,
            "plan": planned,
        }

    operation = _root_run_script(_install_script(), distribution, timeout=timeout)
    after = state(distribution, user)
    ok = bool(operation.get("ok") and after.get("ready"))
    return {
        "schema": f"{SCHEMA}.apply",
        "ok": ok,
        "status": "completed" if ok else "failed",
        "backup": backup,
        "operation": {
            "returncode": operation.get("returncode"),
            "stderr": str(operation.get("stderr") or "")[-2000:],
        },
        "after": after,
        "rollback": "restore the routed backup files, remove newly created managed paths, daemon-reload, and re-enable the prior unit only after review",
    }
