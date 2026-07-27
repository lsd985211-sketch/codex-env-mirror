#!/usr/bin/env python3
"""Own the PMB daemon as one WSL user-systemd service.

Ownership: PMB daemon unit content, controlled takeover, start/restart, and
runtime acceptance for the WSL ``codexlab`` account.
Non-goals: memory reads/writes, Hub tool routing, package repair, arbitrary
process cleanup, Windows services, or a second PMB daemon.
State behavior: plan/status/validate are read-only; install writes one unit
after exact confirmation and stops only the PMB daemon registered by PMB.
Caller context: local PMB compatibility facades and the memory membership
validator; Hub calls connect to this daemon but never start it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import local_pmb_memory_platform
from local_pmb_memory_process import run_pmb_command
from platform_paths import memory_root, wsl_worktree_linux_root
from shared.wsl_user_systemd import install_user_unit, systemctl, unit_path_value, unit_status


SCHEMA = "local_pmb_memory_service.v1"
SERVICE_NAME = "codex-pmb-memory.service"
INSTALL_CONFIRM = "INSTALL-CODEX-PMB-MEMORY"
PMB_PORT = 8765
PMB_HOST = "127.0.0.1"
PMB_WORKSPACE = "mcsmanager"
PRIMARY_ROOT = Path(wsl_worktree_linux_root())
PRIMARY_BRIDGE_ROOT = PRIMARY_ROOT / "workspace" / "_bridge"
PMB_VENV = local_pmb_memory_platform.pmb_venv_root(PRIMARY_BRIDGE_ROOT)
PMB_EXECUTABLES = local_pmb_memory_platform.pmb_executables(PMB_VENV)
PMB_EXE = PMB_EXECUTABLES["pmb"]
PMB_PYTHONW = PMB_EXECUTABLES["pythonw"]
PMB_HOME = memory_root() / "pmb" / "data"
PMB_FASTEMBED_CACHE = PMB_VENV / "cache" / "fastembed"


def unit_path() -> Path:
    override = os.environ.get("CODEX_PMB_MEMORY_UNIT_PATH", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME


def _quote(value: Path | str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def pmb_environment() -> dict[str, str]:
    env = local_pmb_memory_platform.process_environment(
        os.environ,
        pmb_home=PMB_HOME,
        workspace=PMB_WORKSPACE,
        executable=PMB_EXE,
    )
    env["FASTEMBED_CACHE_PATH"] = str(PMB_FASTEMBED_CACHE)
    return env


def cache_status() -> dict[str, Any]:
    path = PMB_FASTEMBED_CACHE
    exists = path.is_dir()
    mode = (path.stat().st_mode & 0o777) if exists else None
    persistent = not str(path).startswith(("/tmp/", "/run/", "/mnt/"))
    writable = bool(exists and os.access(path, os.W_OK | os.X_OK))
    private = bool(mode is not None and mode & 0o077 == 0)
    return {
        "ok": bool(exists and persistent and writable and private),
        "path": str(path),
        "exists": exists,
        "persistent": persistent,
        "writable": writable,
        "private": private,
        "mode": f"{mode:03o}" if mode is not None else "",
    }


def ensure_cache_dir() -> dict[str, Any]:
    try:
        PMB_FASTEMBED_CACHE.mkdir(parents=True, exist_ok=True)
        PMB_FASTEMBED_CACHE.chmod(0o700)
    except OSError as exc:
        return {
            "ok": False,
            "path": str(PMB_FASTEMBED_CACHE),
            "reason": "fastembed_cache_prepare_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return cache_status()


def run_pmb(args: list[str], *, timeout: int = 60) -> dict[str, Any]:
    if not PMB_EXE.is_file():
        return {"ok": False, "reason": "pmb_executable_missing", "path": str(PMB_EXE)}
    return run_pmb_command(
        pmb_exe=PMB_EXE,
        pmb_pythonw=PMB_PYTHONW,
        args=args,
        cwd=PRIMARY_ROOT,
        env=pmb_environment(),
        timeout=timeout,
    )


def daemon_probe() -> dict[str, Any]:
    result = run_pmb(["daemon", "status"], timeout=60)
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".strip()
    lower = text.casefold()
    running = bool(result.get("ok")) and "no daemon running" not in lower and "not running" not in lower
    pid_match = re.search(r"\bpid\s+(\d+)\b", text, flags=re.IGNORECASE)
    port_match = re.search(r"\bport\s+(\d+)\b", text, flags=re.IGNORECASE)
    return {
        "ok": bool(result.get("ok")),
        "running": running,
        "warm": bool(running and ("warm=true" in lower or " ready" in lower)),
        "pid": int(pid_match.group(1)) if pid_match else None,
        "port": int(port_match.group(1)) if port_match else None,
        "preview": text[:3000],
    }


def daemon_processes() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            parts = [part.decode("utf-8", errors="replace") for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
            command = " ".join(parts)
            if "daemon" not in parts or "run" not in parts:
                continue
            if not any("pmb" in part.casefold() for part in parts):
                continue
            stat_parts = (entry / "stat").read_text(encoding="utf-8", errors="replace").split()
            rows.append(
                {
                    "pid": int(entry.name),
                    "parent_pid": int(stat_parts[3]) if len(stat_parts) > 3 else None,
                    "command": command[:2000],
                }
            )
        except (OSError, ValueError):
            continue
    return {"ok": True, "count": len(rows), "rows": sorted(rows, key=lambda item: item["pid"])}


def unit_content(*, executable: Path | None = None, fastembed_cache: Path | None = None) -> str:
    executable = executable or PMB_EXE
    fastembed_cache = fastembed_cache or PMB_FASTEMBED_CACHE
    return "\n".join(
        [
            "[Unit]",
            "Description=Codex PMB memory daemon for the WSL primary workspace",
            "After=default.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={_quote(executable)} daemon run --host {PMB_HOST} --port {PMB_PORT} --idle-exit-min 0",
            f"WorkingDirectory={unit_path_value(PRIMARY_ROOT)}",
            f"Environment=HOME={_quote(Path.home())}",
            f"Environment=PMB_HOME={_quote(PMB_HOME)}",
            f"Environment=PMB_WORKSPACE={PMB_WORKSPACE}",
            f"Environment=FASTEMBED_CACHE_PATH={_quote(fastembed_cache)}",
            "Environment=PYTHONIOENCODING=utf-8",
            "Environment=PYTHONUTF8=1",
            "Environment=CODEX_PMB_SERVICE_MODE=wsl-user-systemd",
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


def status() -> dict[str, Any]:
    service = unit_status(SERVICE_NAME, unit_path())
    daemon = daemon_probe()
    service_pid = int(service.get("systemd", {}).get("ExecMainPID") or 0)
    daemon_pid = int(daemon.get("pid") or 0)
    processes = daemon_processes()
    cache = cache_status()
    identity_matches = bool(service_pid and daemon_pid and service_pid == daemon_pid)
    return {
        **service,
        "schema": f"{SCHEMA}.status",
        "authority": "wsl_user_systemd",
        "identity": {
            "user": os.environ.get("USER", ""),
            "uid": os.getuid(),
            "root_or_system": os.geteuid() == 0,
            "service_pid": service_pid or None,
            "daemon_pid": daemon_pid or None,
            "matches": identity_matches,
        },
        "bind": {"host": PMB_HOST, "port": PMB_PORT, "loopback_only": True},
        "daemon": daemon,
        "daemon_processes": processes,
        "fastembed_cache": cache,
        "ok": bool(
            service.get("ok")
            and daemon.get("running")
            and daemon.get("warm")
            and identity_matches
            and processes.get("count") == 1
            and cache.get("ok")
        ),
    }


def plan() -> dict[str, Any]:
    path = unit_path()
    content = unit_content()
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    daemon = daemon_probe()
    service = unit_status(SERVICE_NAME, path)
    blockers: list[dict[str, Any]] = []
    if not PMB_EXE.is_file() or not os.access(PMB_EXE, os.X_OK):
        blockers.append({"code": "pmb_executable_unavailable", "path": str(PMB_EXE)})
    if str(PMB_EXE).startswith("/mnt/") or str(PMB_HOME).startswith("/mnt/") or str(PMB_FASTEMBED_CACHE).startswith("/mnt/"):
        blockers.append({"code": "pmb_service_depends_on_windows_storage"})
    if str(PMB_FASTEMBED_CACHE).startswith(("/tmp/", "/run/")):
        blockers.append({"code": "pmb_fastembed_cache_not_persistent", "path": str(PMB_FASTEMBED_CACHE)})
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": not blockers,
        "service": SERVICE_NAME,
        "unit_path": str(path),
        "unit_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "installed_unit_sha256": hashlib.sha256(current.encode("utf-8")).hexdigest() if current else "",
        "would_change": current != content,
        "fastembed_cache": {**cache_status(), "will_create": not PMB_FASTEMBED_CACHE.is_dir()},
        "takeover": {
            "required": bool(daemon.get("running") and not service.get("active")),
            "method": "pmb daemon stop, then systemd foreground daemon run",
            "registered_pid": daemon.get("pid"),
        },
        "blockers": blockers,
        "apply_contract": {
            "confirmation": INSTALL_CONFIRM,
            "writes": [str(path), str(PMB_FASTEMBED_CACHE)],
            "stops_only": "PMB registered daemon or the existing PMB systemd unit",
            "starts": SERVICE_NAME,
        },
    }


def wait_ready(*, wait_seconds: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, min(float(wait_seconds), 300.0))
    attempts = 0
    observed: dict[str, Any] = {}
    while True:
        attempts += 1
        observed = status()
        if observed.get("ok") or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    return {"ok": bool(observed.get("ok")), "attempts": attempts, "status": observed}


def ensure_service() -> dict[str, Any]:
    if not unit_path().is_file():
        return {
            "schema": f"{SCHEMA}.ensure",
            "ok": False,
            "reason": "pmb_user_unit_missing",
            "next_action": f"install --confirm {INSTALL_CONFIRM}",
        }
    cache = ensure_cache_dir()
    if not cache.get("ok"):
        return {"schema": f"{SCHEMA}.ensure", "ok": False, "reason": "fastembed_cache_unavailable", "cache": cache}
    started = systemctl("start", SERVICE_NAME, timeout=60)
    ready = wait_ready(wait_seconds=180) if started.get("ok") else {"ok": False, "reason": "systemctl_start_failed"}
    return {
        "schema": f"{SCHEMA}.ensure",
        "ok": bool(started.get("ok") and ready.get("ok")),
        "cache": cache,
        "start": started,
        "ready": ready,
    }


def restart_service() -> dict[str, Any]:
    if not unit_path().is_file():
        return {
            "schema": f"{SCHEMA}.restart",
            "ok": False,
            "reason": "pmb_user_unit_missing",
            "next_action": f"install --confirm {INSTALL_CONFIRM}",
        }
    cache = ensure_cache_dir()
    if not cache.get("ok"):
        return {"schema": f"{SCHEMA}.restart", "ok": False, "reason": "fastembed_cache_unavailable", "cache": cache}
    restarted = systemctl("restart", SERVICE_NAME, timeout=60)
    ready = wait_ready(wait_seconds=180) if restarted.get("ok") else {"ok": False, "reason": "systemctl_restart_failed"}
    return {
        "schema": f"{SCHEMA}.restart",
        "ok": bool(restarted.get("ok") and ready.get("ok")),
        "cache": cache,
        "restart": restarted,
        "ready": ready,
    }


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

    cache = ensure_cache_dir()
    if not cache.get("ok"):
        return {
            "schema": f"{SCHEMA}.install",
            "ok": False,
            "status": "failed",
            "reason": "fastembed_cache_unavailable",
            "cache": cache,
        }

    before_service = unit_status(SERVICE_NAME, unit_path())
    if before_service.get("active"):
        stopped = systemctl("stop", SERVICE_NAME, timeout=60)
    elif planned.get("takeover", {}).get("required"):
        stopped = run_pmb(["daemon", "stop"], timeout=120)
    else:
        stopped = {"ok": True, "skipped": True, "reason": "no_running_daemon"}
    if not stopped.get("ok"):
        return {"schema": f"{SCHEMA}.install", "ok": False, "status": "failed", "reason": "daemon_takeover_stop_failed", "stop": stopped}

    installed = install_user_unit(
        service_name=SERVICE_NAME,
        path=unit_path(),
        content=unit_content(),
        backup_category="memory-system",
        backup_purpose="before-pmb-user-unit",
        backup_remark="codex-pmb-memory-user-unit",
        backup_trigger="local_pmb_memory_service.install",
    )
    ready = wait_ready(wait_seconds=240) if installed.get("ok") else {"ok": False, "reason": "install_failed"}
    ok = bool(installed.get("ok") and ready.get("ok"))
    return {
        "schema": f"{SCHEMA}.install",
        "ok": ok,
        "status": "completed" if ok else "failed",
        "cache": cache,
        "takeover_stop": stopped,
        "install": installed,
        "ready": ready,
    }


def validate() -> dict[str, Any]:
    planned = plan()
    current = status()
    issues: list[dict[str, Any]] = [{"severity": "risk", **item} for item in planned.get("blockers", [])]
    if not current.get("unit_exists"):
        issues.append({"severity": "risk", "code": "pmb_user_unit_missing", "next_action": f"install --confirm {INSTALL_CONFIRM}"})
    elif planned.get("installed_unit_sha256") != planned.get("unit_sha256"):
        issues.append({"severity": "risk", "code": "pmb_user_unit_stale", "next_action": f"install --confirm {INSTALL_CONFIRM}"})
    if current.get("unit_exists") and not current.get("enabled"):
        issues.append({"severity": "risk", "code": "pmb_user_unit_not_enabled"})
    if current.get("unit_exists") and not current.get("active"):
        issues.append({"severity": "risk", "code": "pmb_user_unit_not_active"})
    if current.get("active") and not current.get("daemon", {}).get("running"):
        issues.append({"severity": "risk", "code": "pmb_daemon_not_registered"})
    if current.get("active") and current.get("daemon", {}).get("running") and not current.get("daemon", {}).get("warm"):
        issues.append({"severity": "risk", "code": "pmb_daemon_not_warm"})
    if current.get("active") and not current.get("identity", {}).get("matches"):
        issues.append({"severity": "risk", "code": "pmb_daemon_identity_mismatch"})
    if current.get("active") and current.get("daemon_processes", {}).get("count") != 1:
        issues.append({"severity": "risk", "code": "pmb_daemon_process_count_invalid", "count": current.get("daemon_processes", {}).get("count")})
    if current.get("identity", {}).get("root_or_system"):
        issues.append({"severity": "risk", "code": "pmb_service_running_as_root"})
    if not current.get("fastembed_cache", {}).get("ok"):
        issues.append(
            {
                "severity": "risk",
                "code": "pmb_fastembed_cache_unavailable",
                "path": current.get("fastembed_cache", {}).get("path", str(PMB_FASTEMBED_CACHE)),
            }
        )
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not issues,
        "status": "ok" if not issues else "risk",
        "issues": issues,
        "plan": planned,
        "service": current,
        "acceptance": {
            "authority": "wsl_user_systemd",
            "single_daemon_identity": True,
            "daemon_warm": True,
            "persistent_fastembed_cache": str(PMB_FASTEMBED_CACHE),
            "loopback_only": True,
            "idle_exit_disabled": True,
            "hub_may_start_daemon": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Own the WSL PMB user service")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status", "validate", "ensure", "restart"):
        sub.add_parser(name)
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if args.command == "plan":
        payload = plan()
    elif args.command == "status":
        payload = status()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "ensure":
        payload = ensure_service()
    elif args.command == "restart":
        payload = restart_service()
    else:
        payload = install(str(args.confirm))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
