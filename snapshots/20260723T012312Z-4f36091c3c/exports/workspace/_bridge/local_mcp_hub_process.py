#!/usr/bin/env python3
"""Process control helpers for the local MCP Hub.

Ownership: this module manages only the local Hub server process lifecycle.
Non-goals: it must not manage arbitrary MCP servers, rewrite scheduled tasks,
or bypass the generated affinity entry stage, forward-only fallback chain, or
target permission model.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from platform_paths import wsl_worktree_linux_root
from shared.windows_powershell import encoded_command_arguments
from shared.wsl_user_systemd import install_user_unit, systemctl, unit_status

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 18881
TASK_NAME = "CodexLocalMcpHub"
SERVICE_NAME = "codex-local-mcp-hub.service"
INSTALL_CONFIRM = "INSTALL-CODEX-LOCAL-MCP-HUB"
HUB_SOURCE_GLOBS = ("local_mcp_hub.py", "local_mcp_hub_*.py")
WSL_INTEROP_ENTRY = Path("/proc/sys/fs/binfmt_misc/WSLInterop")
WSL_INIT = Path("/init")
WINDOWS_SYSTEM_ROOT = Path("/mnt/c/Windows")
PRIMARY_BRIDGE_ROOT = Path(wsl_worktree_linux_root()) / "workspace" / "_bridge"


def hub_unit_path() -> Path:
    override = os.environ.get("CODEX_LOCAL_MCP_HUB_UNIT_PATH", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME


def hub_python_executable() -> Path:
    configured = os.environ.get("CODEX_LOCAL_MCP_HUB_PYTHON", "").strip()
    candidate = Path(configured).expanduser() if configured else Path(sys.executable)
    try:
        return candidate.resolve()
    except OSError:
        return candidate


def _unit_quote(value: Path | str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unit_path_value(value: Path | str) -> str:
    return str(value).replace("\\", "\\x5c").replace(" ", "\\x20")


def hub_unit_content(
    *,
    python: Path | None = None,
    script: Path | None = None,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
) -> str:
    python = python or hub_python_executable()
    script = script or PRIMARY_BRIDGE_ROOT / "local_mcp_hub.py"
    return "\n".join(
        [
            "[Unit]",
            "Description=Codex local MCP Hub for the WSL primary workspace",
            "After=default.target network.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={_unit_quote(python)} -B {_unit_quote(script)} serve --host {host} --port {int(port)}",
            f"WorkingDirectory={_unit_path_value(script.parent)}",
            f"Environment=HOME={Path.home()}",
            "Environment=PYTHONUNBUFFERED=1",
            "Environment=CODEX_LOCAL_MCP_HUB_MODE=wsl-user-systemd",
            "Restart=on-failure",
            "RestartSec=3s",
            "TimeoutStopSec=20s",
            "KillMode=mixed",
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


def hub_service_status() -> dict[str, Any]:
    status = unit_status(SERVICE_NAME, hub_unit_path())
    status.update(
        {
            "schema": "local_mcp_hub.service_status.v1",
            "authority": "wsl_user_systemd",
            "bind": {"host": "127.0.0.1", "port": DEFAULT_PORT, "loopback_only": True},
            "identity": {"user": os.environ.get("USER", ""), "uid": os.getuid(), "root_or_system": os.geteuid() == 0},
        }
    )
    return status


def hub_service_plan() -> dict[str, Any]:
    path = hub_unit_path()
    python = hub_python_executable()
    script = PRIMARY_BRIDGE_ROOT / "local_mcp_hub.py"
    content = hub_unit_content(python=python, script=script)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    blockers: list[dict[str, Any]] = []
    if not python.is_file() or not os.access(python, os.X_OK):
        blockers.append({"code": "linux_python_unavailable", "path": str(python)})
    if not script.is_file():
        blockers.append({"code": "hub_script_unavailable", "path": str(script)})
    if str(python).startswith("/mnt/") or str(script).startswith("/mnt/"):
        blockers.append({"code": "wsl_service_depends_on_windows_path"})
    return {
        "schema": "local_mcp_hub.service_plan.v1",
        "ok": not blockers,
        "service": SERVICE_NAME,
        "unit_path": str(path),
        "python": str(python),
        "script": str(script),
        "unit_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "installed_unit_sha256": hashlib.sha256(current.encode("utf-8")).hexdigest() if current else "",
        "would_change": current != content,
        "blockers": blockers,
        "apply_contract": {
            "confirmation": INSTALL_CONFIRM,
            "writes": [str(path)],
            "authority": "wsl_user_systemd",
            "windows_task_role": "controlled_recovery_fallback_only",
        },
    }


def wait_hub_ready(*, port: int = DEFAULT_PORT, wait_seconds: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.5, min(float(wait_seconds), 60.0))
    attempts = 0
    health: dict[str, Any] = {"ok": False, "reason": "health_wait_not_started"}
    while True:
        attempts += 1
        try:
            health = http_get_json(f"http://127.0.0.1:{int(port)}/health", timeout=1.0)
        except Exception as exc:
            health = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if health.get("ok") or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    return {"ok": bool(health.get("ok")), "attempts": attempts, "health": health}


def install_hub_service(confirm: str) -> dict[str, Any]:
    planned = hub_service_plan()
    if confirm != INSTALL_CONFIRM:
        return {
            "schema": "local_mcp_hub.service_install.v1",
            "ok": False,
            "status": "blocked",
            "reason": "explicit_confirmation_required",
            "required_confirmation": INSTALL_CONFIRM,
            "plan": planned,
        }
    if planned.get("blockers"):
        return {"schema": "local_mcp_hub.service_install.v1", "ok": False, "status": "blocked", "reason": "plan_blocked", "plan": planned}
    installed = install_user_unit(
        service_name=SERVICE_NAME,
        path=hub_unit_path(),
        content=hub_unit_content(),
        backup_category="wsl-workspace",
        backup_purpose="before-local-mcp-hub-user-unit",
        backup_remark="local-mcp-hub-user-unit",
        backup_trigger="local_mcp_hub_process.install",
    )
    ready = wait_hub_ready() if installed.get("ok") else {"ok": False, "reason": "install_failed"}
    status = hub_service_status()
    ok = bool(installed.get("ok") and ready.get("ok") and status.get("ok"))
    return {
        "schema": "local_mcp_hub.service_install.v1",
        "ok": ok,
        "status": "completed" if ok else "failed",
        "install": installed,
        "ready": ready,
        "service_status": status,
        "windows_task_role": "controlled_recovery_fallback_only",
    }


def hidden_creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def windows_console_encoding() -> str:
    return "mbcs" if os.name == "nt" else "utf-8"


def decode_windows_cli_output(value: bytes | str | None) -> str:
    if isinstance(value, str):
        return value
    raw = bytes(value or b"")
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def windows_cmd_path(value: str) -> str:
    match = re.match(r"^/mnt/([A-Za-z])/(.*)$", str(value).replace("\\", "/"))
    if not match:
        return str(value)
    suffix = match.group(2).replace("/", "\\")
    return f"{match.group(1).upper()}:\\{suffix}"


def windows_interop_command(
    executable: str,
    *arguments: str,
    interop_entry: Path = WSL_INTEROP_ENTRY,
    init_path: Path = WSL_INIT,
) -> list[str]:
    command = [executable, *arguments]
    if os.name == "nt" or interop_entry.exists() or not init_path.is_file():
        return command
    resolved = shutil.which(executable)
    if not resolved:
        return command
    if any(argument.startswith("/") for argument in arguments):
        cmd = shutil.which("cmd.exe")
        if cmd:
            command_line = subprocess.list2cmdline([windows_cmd_path(resolved), *arguments])
            return [str(init_path), cmd, "/d", "/s", "/c", command_line]
    return [str(init_path), resolved, *arguments]


def windows_interop_cwd(
    command: list[str],
    *,
    default: Path = ROOT,
    windows_system_root: Path = WINDOWS_SYSTEM_ROOT,
) -> Path:
    if (
        len(command) >= 2
        and Path(command[0]).name == "init"
        and Path(command[1]).name.casefold() == "cmd.exe"
        and windows_system_root.is_dir()
    ):
        return windows_system_root
    return default


def windows_powershell_command(script: str) -> list[str]:
    """Build a WSL-safe command for fixed owner-authored PowerShell source."""
    return windows_interop_command("powershell.exe", *encoded_command_arguments(script))


def http_get_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {"ok": False, "reason": "json_root_not_object"}


def local_hub_processes(port: int = DEFAULT_PORT) -> list[dict[str, Any]]:
    command = (
        "$items = Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*local_mcp_hub.py*' "
        " -and $_.CommandLine -match 'local_mcp_hub\\.py\\s+serve' "
        f" -and $_.CommandLine -like '*{int(port)}*' }} | "
        "Select-Object ProcessId,ParentProcessId,CommandLine; "
        "$items | ConvertTo-Json -Depth 4"
    )
    try:
        proc = subprocess.run(
            windows_powershell_command(command),
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=hidden_creationflags(),
        )
    except Exception:
        return []
    text = (proc.stdout or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    processes: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            pid = 0
        command_line = str(row.get("CommandLine") or "")
        if not pid:
            continue
        normalized_command = re.sub(r"\s+", " ", command_line.replace("/", "\\")).lower()
        if "local_mcp_hub.py serve" not in normalized_command or str(int(port)) not in normalized_command:
            continue
        processes.append({"pid": pid, "parent_pid": row.get("ParentProcessId"), "command_line": command_line})
    return processes


def start_local_hub_task(task_name: str = TASK_NAME) -> dict[str, Any]:
    command = windows_interop_command("schtasks.exe", "/Run", "/TN", task_name)
    try:
        proc = subprocess.run(
            command,
            cwd=str(windows_interop_cwd(command)),
            capture_output=True,
            timeout=10,
            creationflags=hidden_creationflags(),
        )
    except Exception as exc:
        return {"ok": False, "reason": repr(exc), "taskName": task_name}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": decode_windows_cli_output(proc.stdout)[:2000],
        "stderr": decode_windows_cli_output(proc.stderr)[:2000],
        "taskName": task_name,
    }


def stop_process(pid: int) -> dict[str, Any]:
    command = windows_interop_command("taskkill.exe", "/PID", str(int(pid)), "/F")
    try:
        proc = subprocess.run(
            command,
            cwd=str(windows_interop_cwd(command)),
            capture_output=True,
            timeout=10,
            creationflags=hidden_creationflags(),
        )
    except Exception as exc:
        return {"ok": False, "reason": repr(exc), "pid": pid}
    return {
        "ok": proc.returncode == 0,
        "pid": pid,
        "returncode": proc.returncode,
        "stdout": decode_windows_cli_output(proc.stdout)[:2000],
        "stderr": decode_windows_cli_output(proc.stderr)[:2000],
    }


def hub_runtime_state(
    port: int = DEFAULT_PORT,
    *,
    process_retry_seconds: float = 0.75,
) -> dict[str, Any]:
    service = hub_service_status()
    service_pid = int(service.get("systemd", {}).get("ExecMainPID") or 0)
    if service.get("active") and service_pid > 0:
        processes = [
            {
                "pid": service_pid,
                "parent_pid": None,
                "command_line": f"systemd:{SERVICE_NAME}",
                "authority": "wsl_user_systemd",
            }
        ]
    else:
        processes = local_hub_processes(port)
    try:
        health = http_get_json(f"http://127.0.0.1:{int(port)}/health", timeout=1.0)
    except Exception as exc:
        health = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    process_probe_attempts = 1
    retry_window = max(0.0, min(float(process_retry_seconds), 3.0))
    deadline = time.monotonic() + retry_window
    while health.get("ok") and not processes and time.monotonic() < deadline:
        time.sleep(min(0.1, max(0.01, retry_window)))
        process_probe_attempts += 1
        service = hub_service_status()
        service_pid = int(service.get("systemd", {}).get("ExecMainPID") or 0)
        processes = (
            [
                {
                    "pid": service_pid,
                    "parent_pid": None,
                    "command_line": f"systemd:{SERVICE_NAME}",
                    "authority": "wsl_user_systemd",
                }
            ]
            if service.get("active") and service_pid > 0
            else local_hub_processes(port)
        )
    if not processes:
        reason = "listener_process_visibility_missing" if health.get("ok") else "listener_process_missing"
        return {
            "schema": "local_mcp_hub.runtime.v1",
            "ok": False,
            "port": int(port),
            "processes": [],
            "process_probe_attempts": process_probe_attempts,
            "health": health,
            "reason": reason,
            "service": service,
        }
    return {
        "schema": "local_mcp_hub.runtime.v1",
        "ok": bool(health.get("ok")),
        "port": int(port),
        "processes": processes,
        "process_probe_attempts": process_probe_attempts,
        "health": health,
        "service": service,
        "authority": processes[0].get("authority", "windows_scheduled_task_fallback"),
    }


def hub_bytecode_cache_candidates(module_dir: Path | None = None) -> list[Path]:
    """Return exact bytecode paths owned by the local Hub source modules."""
    directory = (module_dir or Path(__file__).resolve().parent).resolve()
    sources: set[Path] = set()
    for pattern in HUB_SOURCE_GLOBS:
        sources.update(path.resolve() for path in directory.glob(pattern) if path.is_file())

    candidates: set[Path] = set()
    for source in sources:
        for optimization in (None, "1", "2"):
            try:
                cache_path = importlib.util.cache_from_source(str(source), optimization=optimization)
            except (NotImplementedError, ValueError):
                continue
            candidates.add(Path(cache_path).resolve())
    return sorted(candidates, key=lambda path: str(path).lower())


def clear_hub_bytecode_cache(*, module_dir: Path | None = None, dry_run: bool) -> dict[str, Any]:
    """Remove only Hub-owned bytecode caches, leaving unrelated caches untouched."""
    candidates = hub_bytecode_cache_candidates(module_dir)
    existing = [path for path in candidates if path.is_file()]
    removed: list[str] = []
    failures: list[dict[str, str]] = []
    if not dry_run:
        for path in existing:
            try:
                path.unlink()
                removed.append(str(path))
            except OSError as exc:
                failures.append({"path": str(path), "error": repr(exc)})
    return {
        "ok": not failures,
        "dry_run": bool(dry_run),
        "candidate_bytecode_cache": [str(path) for path in existing],
        "removed_bytecode_cache": removed,
        "failed_bytecode_cache": failures,
        "safety_boundary": "only importlib cache paths derived from local_mcp_hub.py and local_mcp_hub_*.py are eligible",
    }


def reload_local_hub(*, confirm_reload: bool, port: int = DEFAULT_PORT, wait_seconds: float = 5.0) -> dict[str, Any]:
    service_before = hub_service_status()
    systemd_primary = bool(service_before.get("unit_exists"))
    before = hub_runtime_state(port, process_retry_seconds=0).get("processes", [])
    bytecode_cache = clear_hub_bytecode_cache(dry_run=not confirm_reload)
    plan = {
        "schema": "local_mcp_hub.reload.v1",
        "ok": True,
        "dry_run": not confirm_reload,
        "port": int(port),
        "matched_processes": before,
        "authority": "wsl_user_systemd" if systemd_primary else "windows_scheduled_task_fallback",
        "safety_boundary": "restart only the declared WSL user unit; without that unit, stop only a Windows process matching local_mcp_hub.py serve and the target port",
        "start_route": f"systemctl --user restart {SERVICE_NAME}" if systemd_primary else f"schtasks.exe /Run /TN {TASK_NAME}",
        "bytecode_cache": bytecode_cache,
    }
    if not confirm_reload:
        plan["next_step"] = "rerun with --confirm-reload to restart the selected lifecycle authority"
        return plan
    if not bytecode_cache.get("ok"):
        plan.update(
            {
                "ok": False,
                "dry_run": False,
                "reason": "hub_bytecode_cache_cleanup_failed",
                "next_step": "resolve the reported cache deletion failure; the existing Hub listener was left running",
            }
        )
        return plan
    if systemd_primary:
        stop_results: list[dict[str, Any]] = []
        start_result = systemctl("restart", SERVICE_NAME, timeout=60)
    else:
        stop_results = [stop_process(int(item["pid"])) for item in before]
        start_result = start_local_hub_task()
    ready = wait_hub_ready(port=port, wait_seconds=wait_seconds)
    runtime_after = hub_runtime_state(port, process_retry_seconds=0.5)
    after = runtime_after.get("processes", [])
    health = ready.get("health", {})
    plan.update(
        {
            "dry_run": False,
            "stop_results": stop_results,
            "start_result": start_result,
            "after_processes": after,
            "health": health,
            "health_attempts": ready.get("attempts", 0),
            "health_wait_seconds": max(0.5, min(float(wait_seconds), 20.0)),
            "runtime": runtime_after,
            "ok": bool(start_result.get("ok") and runtime_after.get("ok") and all(item.get("ok") for item in stop_results)),
        }
    )
    return plan


def validate_hub_service() -> dict[str, Any]:
    planned = hub_service_plan()
    service = hub_service_status()
    runtime = hub_runtime_state()
    issues: list[dict[str, Any]] = []
    issues.extend({"severity": "risk", **item} for item in planned.get("blockers", []))
    if not service.get("unit_exists"):
        issues.append({"severity": "risk", "code": "hub_user_unit_missing", "next_action": f"install --confirm {INSTALL_CONFIRM}"})
    elif planned.get("installed_unit_sha256") != planned.get("unit_sha256"):
        issues.append({"severity": "risk", "code": "hub_user_unit_stale", "next_action": f"install --confirm {INSTALL_CONFIRM}"})
    if service.get("unit_exists") and not service.get("enabled"):
        issues.append({"severity": "risk", "code": "hub_user_unit_not_enabled"})
    if service.get("unit_exists") and not service.get("active"):
        issues.append({"severity": "risk", "code": "hub_user_unit_not_active"})
    if service.get("identity", {}).get("root_or_system"):
        issues.append({"severity": "risk", "code": "hub_service_running_as_root"})
    if not runtime.get("ok"):
        issues.append({"severity": "risk", "code": "hub_listener_unavailable", "detail": runtime})
    return {
        "schema": "local_mcp_hub.service_validate.v1",
        "ok": not issues,
        "status": "ok" if not issues else "risk",
        "issues": issues,
        "plan": planned,
        "service": service,
        "runtime": runtime,
        "acceptance": {
            "authority": "wsl_user_systemd",
            "loopback_only": True,
            "restart": "on-failure",
            "windows_task_role": "controlled_recovery_fallback_only",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Own the local MCP Hub process lifecycle")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status", "validate"):
        sub.add_parser(name)
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--confirm", default="")
    reload_parser = sub.add_parser("reload")
    reload_parser.add_argument("--confirm-reload", action="store_true")
    reload_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    reload_parser.add_argument("--wait-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    if args.command == "plan":
        payload = hub_service_plan()
    elif args.command == "status":
        payload = hub_service_status()
    elif args.command == "validate":
        payload = validate_hub_service()
    elif args.command == "install":
        payload = install_hub_service(str(args.confirm))
    else:
        payload = reload_local_hub(
            confirm_reload=bool(args.confirm_reload),
            port=int(args.port),
            wait_seconds=float(args.wait_seconds),
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
