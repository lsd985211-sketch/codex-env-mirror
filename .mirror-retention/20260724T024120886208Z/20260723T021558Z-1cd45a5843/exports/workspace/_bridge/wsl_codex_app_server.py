#!/usr/bin/env python3
"""Own the optional user-level Codex app-server service inside WSL.

Ownership: WSL user systemd and the declared ``codexlab`` runtime account.
Non-goals: Windows Desktop app-server, Windows SYSTEM services, token
materialization, TCP exposure, or replacing the Desktop/CDP primary route.
State behavior: plans are read-only; apply writes one user unit atomically and
uses systemd to enable/start it. The unit keeps credentials in CODEX_HOME and
exposes only a user-runtime Unix socket.
Caller context: ``wsl_workspace_owner`` lifecycle and validation facade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.wsl_user_systemd import install_user_unit, run as _run, systemctl


SCHEMA = "wsl_codex_app_server.v1"
INSTALL_CONFIRM = "INSTALL-CODEX-APP-SERVER"
SERVICE_NAME = "codex-app-server.service"
DEFAULT_CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex-app"))).expanduser()
DEFAULT_WORKSPACE = Path(os.environ.get("CODEX_APP_SERVER_WORKSPACE", "/home/codexlab/work/codex-workspace")).expanduser()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unit_path() -> Path:
    override = os.environ.get("CODEX_APP_SERVER_UNIT_PATH")
    return Path(override).expanduser() if override else Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME


def socket_path() -> Path:
    return Path(os.environ.get("CODEX_APP_SERVER_SOCKET", str(Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / "codex-app-server.sock"))).expanduser()


def codex_executable() -> Path:
    configured = os.environ.get("CODEX_APP_SERVER_EXECUTABLE", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
    else:
        # Prefer the Linux installation.  WSL PATH commonly puts a Windows
        # shim first; persisting that path would make the user service depend
        # on host state and can silently select the Desktop runtime.
        linux_candidates = (Path("/usr/bin/codex"), Path("/usr/local/bin/codex"), Path("/bin/codex"))
        candidate = next((path for path in linux_candidates if path.is_file() and os.access(path, os.X_OK)), Path(shutil.which("codex") or "/usr/bin/codex") )
    try:
        return candidate.resolve()
    except OSError:
        return candidate


def unit_content(*, executable: Path | None = None, codex_home: Path = DEFAULT_CODEX_HOME, workspace: Path = DEFAULT_WORKSPACE) -> str:
    executable = executable or codex_executable()
    # %t is expanded by systemd to the current user's runtime directory.
    return "\n".join(
        [
            "[Unit]",
            "Description=Codex app-server for the WSL primary workspace",
            "After=default.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={executable} app-server --listen unix://%t/codex-app-server.sock",
            f"WorkingDirectory={workspace}",
            f"Environment=HOME={Path.home()}",
            f"Environment=CODEX_HOME={codex_home}",
            "Environment=CODEX_APP_SERVER_MODE=wsl-user-systemd",
            "Restart=on-failure",
            "RestartSec=5s",
            "TimeoutStopSec=20s",
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
    path = unit_path()
    show = systemctl("show", SERVICE_NAME, "--property=LoadState,ActiveState,SubState,UnitFileState,ExecMainPID,Result")
    enabled = systemctl("is-enabled", SERVICE_NAME)
    active = systemctl("is-active", SERVICE_NAME)
    values: dict[str, str] = {}
    for line in str(show.get("stdout") or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {
        "schema": f"{SCHEMA}.status",
        "ok": bool(path.is_file() and active.get("ok") and values.get("ActiveState") == "active"),
        "generated_at": now_iso(),
        "service": SERVICE_NAME,
        "unit_path": str(path),
        "unit_exists": path.is_file(),
        "enabled": str(enabled.get("stdout") or "") == "enabled",
        "active": str(active.get("stdout") or "") == "active",
        "socket_path": str(socket_path()),
        "socket_exists": socket_path().exists(),
        "systemd": values,
        "identity": {"user": os.environ.get("USER", ""), "uid": os.getuid(), "codex_home": str(DEFAULT_CODEX_HOME)},
        "boundary": {"transport": "unix_socket", "tcp_exposed": False, "windows_desktop_owner_replaced": False, "root_or_system": os.geteuid() == 0},
    }


def plan(*, workspace: Path = DEFAULT_WORKSPACE, codex_home: Path = DEFAULT_CODEX_HOME) -> dict[str, Any]:
    path = unit_path()
    executable = codex_executable()
    content = unit_content(executable=executable, codex_home=codex_home, workspace=workspace)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": bool(executable.is_file() and os.access(executable, os.X_OK)),
        "generated_at": now_iso(),
        "service": SERVICE_NAME,
        "unit_path": str(path),
        "executable": str(executable),
        "executable_sha256": _sha256(executable) if executable.is_file() else "",
        "codex_home": str(codex_home),
        "workspace": str(workspace),
        "socket_path": str(socket_path()),
        "unit_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "installed_unit_sha256": hashlib.sha256(current.encode("utf-8")).hexdigest() if current else "",
        "would_change": current != content,
        "blockers": [] if executable.is_file() and os.access(executable, os.X_OK) else [{"code": "codex_executable_unavailable", "path": str(executable)}],
        "apply_contract": {"confirmation": INSTALL_CONFIRM, "writes": [str(path)], "transport": "user_systemd_unix_socket", "tcp_exposed": False, "root_required": False},
    }


def validate(*, workspace: Path = DEFAULT_WORKSPACE, codex_home: Path = DEFAULT_CODEX_HOME) -> dict[str, Any]:
    planned = plan(workspace=workspace, codex_home=codex_home)
    current = status()
    issues: list[dict[str, Any]] = []
    if planned.get("blockers"):
        issues.extend(planned["blockers"])
    if not current.get("unit_exists"):
        issues.append({"severity": "risk", "code": "codex_app_server_unit_missing", "next_action": f"run install --confirm {INSTALL_CONFIRM}"})
    elif planned.get("installed_unit_sha256") != planned.get("unit_sha256"):
        issues.append({"severity": "risk", "code": "codex_app_server_unit_stale", "next_action": f"run install --confirm {INSTALL_CONFIRM}"})
    if current.get("unit_exists") and not current.get("enabled"):
        issues.append({"severity": "risk", "code": "codex_app_server_not_enabled", "next_action": f"run install --confirm {INSTALL_CONFIRM}"})
    if current.get("unit_exists") and not current.get("active"):
        issues.append({"severity": "risk", "code": "codex_app_server_not_active", "next_action": "inspect systemctl --user status codex-app-server.service"})
    if current.get("boundary", {}).get("root_or_system"):
        issues.append({"severity": "risk", "code": "codex_app_server_running_as_root", "next_action": "run the service as the declared WSL user"})
    return {"schema": f"{SCHEMA}.validate", "ok": not any(item.get("severity") == "risk" for item in issues), "status": "ok" if not issues else "risk", "generated_at": now_iso(), "issues": issues, "plan": planned, "status_snapshot": current, "acceptance": {"user_systemd": True, "unix_socket_only": True, "tcp_exposed": False, "windows_desktop_route_unchanged": True, "codex_home_isolated": str(codex_home) == str(DEFAULT_CODEX_HOME)}}


def install(confirm: str, *, workspace: Path = DEFAULT_WORKSPACE, codex_home: Path = DEFAULT_CODEX_HOME) -> dict[str, Any]:
    planned = plan(workspace=workspace, codex_home=codex_home)
    if confirm != INSTALL_CONFIRM:
        return {"schema": f"{SCHEMA}.install", "ok": False, "status": "blocked", "reason": "explicit_confirmation_required", "required_confirmation": INSTALL_CONFIRM, "plan": planned}
    if planned.get("blockers"):
        return {"schema": f"{SCHEMA}.install", "ok": False, "status": "blocked", "reason": "plan_blocked", "plan": planned}
    path = unit_path()
    content = unit_content(executable=Path(planned["executable"]), codex_home=codex_home, workspace=workspace)
    installed = install_user_unit(
        service_name=SERVICE_NAME,
        path=path,
        content=content,
        backup_category="wsl-workspace",
        backup_purpose="before-codex-app-server-user-unit",
        backup_remark="codex-app-server-user-unit",
        backup_trigger="wsl_codex_app_server.install",
    )
    if not installed.get("ok"):
        return {
            "schema": f"{SCHEMA}.install",
            "ok": False,
            "status": "blocked" if installed.get("reason") == "backup_failed" else "failed",
            "reason": installed.get("reason") or "systemd_install_failed",
            "install": installed,
        }
    result = validate(workspace=workspace, codex_home=codex_home)
    return {
        "schema": f"{SCHEMA}.install",
        "ok": bool(installed.get("ok") and result.get("ok")),
        "status": "completed" if result.get("ok") else "failed",
        "generated_at": now_iso(),
        "install": installed,
        "validation": result,
    }


def stop() -> dict[str, Any]:
    result = systemctl("stop", SERVICE_NAME)
    return {"schema": f"{SCHEMA}.stop", "ok": result.get("ok"), "service": SERVICE_NAME, "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Own the WSL user-level Codex app-server service")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--codex-home", default=str(DEFAULT_CODEX_HOME))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    sub.add_parser("status")
    sub.add_parser("validate")
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--confirm", default="")
    sub.add_parser("stop")
    args = parser.parse_args(argv)
    workspace, codex_home = Path(args.workspace).expanduser(), Path(args.codex_home).expanduser()
    if args.command == "plan":
        payload = plan(workspace=workspace, codex_home=codex_home)
    elif args.command == "status":
        payload = status()
    elif args.command == "validate":
        payload = validate(workspace=workspace, codex_home=codex_home)
    elif args.command == "install":
        payload = install(args.confirm, workspace=workspace, codex_home=codex_home)
    else:
        payload = stop()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
