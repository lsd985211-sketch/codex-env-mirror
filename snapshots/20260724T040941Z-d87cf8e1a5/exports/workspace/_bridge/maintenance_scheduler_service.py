#!/usr/bin/env python3
"""Own the unified maintenance scheduler as a WSL user-systemd service.

Ownership: scheduler unit lifecycle, heartbeat acceptance, and conversion of
the legacy Windows resident task into a one-shot WSL login wake action.
Non-goals: task definitions, task approvals, arbitrary Windows commands,
business-owner state, or a second scheduler database.
State behavior: plan/status/validate are read-only; install writes one user
unit; install-windows-wake rewrites only the fixed CodexSchedulerRunner task
after the WSL service is accepted.
Caller context: scheduler system membership and the typed Windows execution
agent; the existing codex_scheduler_runner remains the execution authority.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platform_paths import host_accessible_path, host_compatibility_root, wsl_worktree_linux_root
from shared import codex_scheduler_runner
from shared.backup_router import create_backup as create_routed_backup
from shared.windows_powershell import encoded_command_arguments
from shared.wsl_user_systemd import install_user_unit, unit_path_value, unit_status
import windows_execution_agent


SCHEMA = "maintenance_scheduler_service.v1"
SERVICE_NAME = "codex-maintenance-scheduler.service"
INSTALL_CONFIRM = "INSTALL-CODEX-MAINTENANCE-SCHEDULER"
WINDOWS_WAKE_CONFIRM = "INSTALL-CODEX-WSL-CONTROL-PLANE-WAKE"
WINDOWS_TASK_NAME = "CodexSchedulerRunner"
DEFAULT_INTERVAL_SECONDS = 300
PRIMARY_ROOT = Path(wsl_worktree_linux_root())
SCHEDULER_SCRIPT = PRIMARY_ROOT / "workspace" / "_bridge" / "shared" / "codex_scheduler_runner.py"
WINDOWS_WAKE_INSTALLER = host_compatibility_root() / "_bridge" / "shared" / "install-codex-scheduler-task.ps1"


def unit_path() -> Path:
    override = os.environ.get("CODEX_MAINTENANCE_SCHEDULER_UNIT_PATH", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME


def _quote(value: Path | str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def python_executable() -> Path:
    configured = os.environ.get("CODEX_MAINTENANCE_SCHEDULER_PYTHON", "").strip()
    candidate = Path(configured).expanduser() if configured else Path(sys.executable)
    try:
        return candidate.resolve()
    except OSError:
        return candidate


def unit_content(*, python: Path | None = None, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> str:
    python = python or python_executable()
    interval = max(30, int(interval_seconds))
    return "\n".join(
        [
            "[Unit]",
            "Description=Codex unified maintenance scheduler for the WSL primary workspace",
            "After=default.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={_quote(python)} {_quote(SCHEDULER_SCRIPT)} loop --interval-seconds {interval}",
            f"WorkingDirectory={unit_path_value(PRIMARY_ROOT / 'workspace')}",
            f"Environment=HOME={_quote(Path.home())}",
            "Environment=PYTHONIOENCODING=utf-8",
            "Environment=PYTHONUTF8=1",
            "Environment=CODEX_SCHEDULER_SERVICE_MODE=wsl-user-systemd",
            "Restart=on-failure",
            "RestartSec=5s",
            "TimeoutStopSec=30s",
            "KillMode=control-group",
            "UMask=0077",
            "NoNewPrivileges=yes",
            "PrivateTmp=yes",
            "StandardOutput=journal",
            "StandardError=journal",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def _read_heartbeat() -> dict[str, Any]:
    path = codex_scheduler_runner.HEARTBEAT_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "path": str(path), "reason": f"{type(exc).__name__}: {exc}"}
    if not isinstance(payload, dict):
        return {"ok": False, "path": str(path), "reason": "heartbeat_root_not_object"}
    updated = str(payload.get("updated_at") or payload.get("generated_at") or "")
    age_seconds: float | None = None
    try:
        parsed = datetime.fromisoformat(updated)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        pass
    return {
        "ok": bool(updated),
        "path": str(path),
        "updated_at": updated,
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "service_mode": payload.get("service_mode"),
        "loop_pid": payload.get("loop_pid"),
        "payload": payload,
    }


def _windows_task_row() -> dict[str, Any]:
    snapshot = windows_execution_agent.snapshot()
    rows = snapshot.get("inventory", {}).get("tasks", []) if isinstance(snapshot.get("inventory"), dict) else []
    return next((dict(row) for row in rows if isinstance(row, dict) and row.get("task_name") == WINDOWS_TASK_NAME), {})


def windows_wake_status() -> dict[str, Any]:
    row = _windows_task_row()
    action = f"{row.get('execute', '')} {row.get('arguments', '')}".casefold()
    converted = bool(
        row
        and "wsl.exe" in action
        and SERVICE_NAME.casefold() in action
        and "run-codex-scheduler.ps1" not in action
        and str(row.get("run_level") or "").casefold() == "limited"
    )
    return {
        "ok": converted,
        "task_name": WINDOWS_TASK_NAME,
        "exists": bool(row),
        "converted": converted,
        "row": row,
        "resident_windows_loop": "run-codex-scheduler.ps1" in action,
        "role": "login_wake_only" if converted else "legacy_or_missing",
    }


def _service_status() -> dict[str, Any]:
    service = unit_status(SERVICE_NAME, unit_path())
    heartbeat = _read_heartbeat()
    service_pid = int(service.get("systemd", {}).get("ExecMainPID") or 0)
    loop_pid = int(heartbeat.get("loop_pid") or 0)
    return {
        **service,
        "schema": f"{SCHEMA}.status",
        "authority": "wsl_user_systemd",
        "heartbeat": heartbeat,
        "identity": {
            "user": os.environ.get("USER", ""),
            "uid": os.getuid(),
            "root_or_system": os.geteuid() == 0,
            "service_pid": service_pid or None,
            "loop_pid": loop_pid or None,
            "matches": bool(service_pid and loop_pid and service_pid == loop_pid),
        },
        "ok": bool(service.get("ok") and heartbeat.get("ok") and service_pid and loop_pid == service_pid),
    }


def status() -> dict[str, Any]:
    return {**_service_status(), "windows_wake": windows_wake_status()}


def plan() -> dict[str, Any]:
    path = unit_path()
    content = unit_content()
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    python = python_executable()
    blockers: list[dict[str, Any]] = []
    if not python.is_file() or not os.access(python, os.X_OK):
        blockers.append({"code": "linux_python_unavailable", "path": str(python)})
    if not SCHEDULER_SCRIPT.is_file():
        blockers.append({"code": "scheduler_script_unavailable", "path": str(SCHEDULER_SCRIPT)})
    if str(python).startswith("/mnt/") or str(SCHEDULER_SCRIPT).startswith("/mnt/"):
        blockers.append({"code": "scheduler_service_depends_on_windows_path"})
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": not blockers,
        "service": SERVICE_NAME,
        "unit_path": str(path),
        "python": str(python),
        "script": str(SCHEDULER_SCRIPT),
        "unit_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "installed_unit_sha256": hashlib.sha256(current.encode("utf-8")).hexdigest() if current else "",
        "would_change": current != content,
        "blockers": blockers,
        "apply_contract": {
            "confirmation": INSTALL_CONFIRM,
            "writes": [str(path)],
            "task_authority": "shared/codex_scheduler_runner.py",
            "windows_conversion_after_acceptance": WINDOWS_WAKE_CONFIRM,
        },
    }


def wait_ready(*, wait_seconds: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, min(float(wait_seconds), 120.0))
    attempts = 0
    observed: dict[str, Any] = {}
    while True:
        attempts += 1
        observed = _service_status()
        if observed.get("ok") or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    return {"ok": bool(observed.get("ok")), "attempts": attempts, "status": observed}


def install(confirm: str) -> dict[str, Any]:
    planned = plan()
    if confirm != INSTALL_CONFIRM:
        return {
            "schema": f"{SCHEMA}.install",
            "ok": False,
            "status": "blocked",
            "reason": "explicit_confirmation_required",
            "required_confirmation": INSTALL_CONFIRM,
            "plan": planned,
        }
    if planned.get("blockers"):
        return {"schema": f"{SCHEMA}.install", "ok": False, "status": "blocked", "reason": "plan_blocked", "plan": planned}
    installed = install_user_unit(
        service_name=SERVICE_NAME,
        path=unit_path(),
        content=unit_content(),
        backup_category="wsl-workspace",
        backup_purpose="before-maintenance-scheduler-user-unit",
        backup_remark="codex-maintenance-scheduler-user-unit",
        backup_trigger="maintenance_scheduler_service.install",
    )
    ready = wait_ready(wait_seconds=45) if installed.get("ok") else {"ok": False, "reason": "install_failed"}
    ok = bool(installed.get("ok") and ready.get("ok"))
    return {
        "schema": f"{SCHEMA}.install",
        "ok": ok,
        "status": "completed" if ok else "failed",
        "install": installed,
        "ready": ready,
        "next_action": f"install-windows-wake --confirm {WINDOWS_WAKE_CONFIRM}" if ok else "repair service before Windows task conversion",
    }


def windows_wake_plan() -> dict[str, Any]:
    service = status()
    service_plan = plan()
    installer = WINDOWS_WAKE_INSTALLER
    systemd = service.get("systemd", {})
    task = service.get("windows_wake", {})
    handoff_waiting = bool(
        service.get("unit_exists")
        and service.get("enabled")
        and service.get("active")
        and systemd.get("LoadState") == "loaded"
        and service_plan.get("installed_unit_sha256") == service_plan.get("unit_sha256")
        and not service_plan.get("blockers")
        and task.get("resident_windows_loop")
        and not service.get("identity", {}).get("matches")
    )
    return {
        "schema": f"{SCHEMA}.windows_wake_plan",
        "ok": bool((service.get("ok") or handoff_waiting) and installer.is_file()),
        "service_ready": bool(service.get("ok")),
        "handoff_waiting": handoff_waiting,
        "installer": str(installer),
        "installer_exists": installer.is_file(),
        "task": task,
        "confirmation": WINDOWS_WAKE_CONFIRM,
        "writes": [f"Windows scheduled task:{WINDOWS_TASK_NAME}"],
        "acceptance": "limited at-logon action invokes wsl.exe and no Windows scheduler loop",
    }


def _backup_windows_task() -> tuple[dict[str, Any], str]:
    powershell = windows_execution_agent.powershell_path()
    script = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
        f"Export-ScheduledTask -TaskName '{WINDOWS_TASK_NAME}'"
    )
    try:
        completed = subprocess.run(
            [str(powershell), *encoded_command_arguments(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": "windows_task_export_failed", "stderr": f"{type(exc).__name__}: {exc}"}, ""
    xml = (completed.stdout or "").strip()
    if completed.returncode != 0 or not xml.startswith("<?xml"):
        return {
            "ok": False,
            "reason": "windows_task_export_failed",
            "returncode": completed.returncode,
            "stderr": (completed.stderr or "").strip()[:2000],
        }, ""
    staging = Path.home() / ".codex-app" / "runtime" / "maintenance-scheduler-service" / f"{WINDOWS_TASK_NAME}.xml"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_text(xml, encoding="utf-8")
    backup = create_routed_backup(
        [str(staging)],
        category="scheduler",
        purpose="before-windows-scheduler-task-conversion",
        remark="codex-scheduler-windows-task",
        trigger="maintenance_scheduler_service.install_windows_wake",
    )
    staging.unlink(missing_ok=True)
    return backup, xml


def _restore_windows_task(xml: str) -> dict[str, Any]:
    encoded_xml = base64.b64encode(xml.encode("utf-16le")).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop';"
        f"$xml=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{encoded_xml}'));"
        f"Register-ScheduledTask -TaskName '{WINDOWS_TASK_NAME}' -Xml $xml -Force | Out-Null;"
        f"Start-ScheduledTask -TaskName '{WINDOWS_TASK_NAME}'"
    )
    try:
        completed = subprocess.run(
            [str(windows_execution_agent.powershell_path()), *encoded_command_arguments(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "returncode": None, "stderr": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stderr": (completed.stderr or "").strip()[:2000],
    }


def install_windows_wake(confirm: str) -> dict[str, Any]:
    planned = windows_wake_plan()
    if confirm != WINDOWS_WAKE_CONFIRM:
        return {
            "schema": f"{SCHEMA}.windows_wake_install",
            "ok": False,
            "status": "blocked",
            "reason": "explicit_confirmation_required",
            "required_confirmation": WINDOWS_WAKE_CONFIRM,
            "plan": planned,
        }
    if not planned.get("ok"):
        return {"schema": f"{SCHEMA}.windows_wake_install", "ok": False, "status": "blocked", "reason": "wsl_service_or_projection_not_ready", "plan": planned}
    backup, original_xml = _backup_windows_task()
    if not backup.get("ok"):
        return {"schema": f"{SCHEMA}.windows_wake_install", "ok": False, "status": "blocked", "reason": "windows_task_backup_failed", "backup": backup}
    powershell = windows_execution_agent.powershell_path()
    installer = host_accessible_path(WINDOWS_WAKE_INSTALLER, platform_name="nt")
    try:
        completed = subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(installer),
                "-TaskName",
                WINDOWS_TASK_NAME,
                "-StartNow",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        operation = {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "").strip()[:8000],
            "stderr": (completed.stderr or "").strip()[:4000],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        operation = {"ok": False, "returncode": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    after = windows_wake_status()
    ready = wait_ready(wait_seconds=60) if operation.get("ok") and after.get("ok") else {"ok": False, "reason": "conversion_failed"}
    ok = bool(operation.get("ok") and after.get("ok") and ready.get("ok"))
    rollback = {"ok": True, "applied": False}
    if not ok:
        rollback = {**_restore_windows_task(original_xml), "applied": True}
    return {
        "schema": f"{SCHEMA}.windows_wake_install",
        "ok": ok,
        "status": "completed" if ok else "failed",
        "operation": operation,
        "backup": backup,
        "after": after,
        "ready": ready,
        "rollback": rollback,
    }


def validate() -> dict[str, Any]:
    planned = plan()
    current = status()
    issues: list[dict[str, Any]] = [{"severity": "risk", **item} for item in planned.get("blockers", [])]
    if not current.get("unit_exists"):
        issues.append({"severity": "risk", "code": "scheduler_user_unit_missing", "next_action": f"install --confirm {INSTALL_CONFIRM}"})
    elif planned.get("installed_unit_sha256") != planned.get("unit_sha256"):
        issues.append({"severity": "risk", "code": "scheduler_user_unit_stale", "next_action": f"install --confirm {INSTALL_CONFIRM}"})
    if current.get("unit_exists") and not current.get("enabled"):
        issues.append({"severity": "risk", "code": "scheduler_user_unit_not_enabled"})
    if current.get("unit_exists") and not current.get("active"):
        issues.append({"severity": "risk", "code": "scheduler_user_unit_not_active"})
    if current.get("active") and not current.get("identity", {}).get("matches"):
        issues.append({"severity": "risk", "code": "scheduler_loop_identity_mismatch"})
    if current.get("identity", {}).get("root_or_system"):
        issues.append({"severity": "risk", "code": "scheduler_service_running_as_root"})
    if not current.get("windows_wake", {}).get("ok"):
        issues.append({"severity": "risk", "code": "windows_scheduler_loop_not_converted", "next_action": f"install-windows-wake --confirm {WINDOWS_WAKE_CONFIRM}"})
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not issues,
        "status": "ok" if not issues else "risk",
        "issues": issues,
        "plan": planned,
        "service": current,
        "acceptance": {
            "authority": "wsl_user_systemd",
            "task_authority": "codex_scheduler_runner",
            "windows_resident_loop": False,
            "windows_task_role": "login_wake_only",
            "second_scheduler_created": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Own the WSL maintenance scheduler service")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status", "validate", "windows-wake-plan"):
        sub.add_parser(name)
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--confirm", default="")
    wake_parser = sub.add_parser("install-windows-wake")
    wake_parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if args.command == "plan":
        payload = plan()
    elif args.command == "status":
        payload = status()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "windows-wake-plan":
        payload = windows_wake_plan()
    elif args.command == "install":
        payload = install(str(args.confirm))
    else:
        payload = install_windows_wake(str(args.confirm))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
