"""Codex app-server route and client helpers for the mobile bridge.

Owns: app-server route configuration, Codex executable discovery, app-server
listener ownership checks, controlled listener start/stop/restart, client
process calls, and thread create/inspect/sync wrappers.
Non-goals: mobile queue mutation, Weixin reply sending, permission decisions,
owned-result parsing, worker scheduling, or repair-continuation policy.
State behavior: may start or stop only the configured Codex app-server listener
and may create/inspect Codex threads through the app-server client; it does not
write bridge queue state.
Normal callers: mobile_openclaw_cli facade, worker dispatch preparation,
account onboarding, health checks, and app-server regression checks.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from health_checks import tcp_check

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]


def app_server_turn_materialization_grace_seconds(config: dict[str, Any]) -> int:
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    return max(5, int(trigger.get("app_server_turn_materialization_grace_seconds") or 20))


def app_server_config(config: dict[str, Any]) -> dict[str, Any]:
    trigger = config.get("trigger", {})
    return {
        "host": str(trigger.get("codex_app_server_host") or "127.0.0.1"),
        "port": int(trigger.get("codex_app_server_port") or 18791),
        "node": str(trigger.get("node_path") or "node"),
        "codex": str(trigger.get("codex_path") or trigger.get("codex_cli_path") or "codex"),
        "script": Path(
            trigger.get("codex_app_server_script")
            or PROJECT_ROOT / "_tools" / "codex-app-server-tools" / "codex_app_server_client.js"
        ),
        "timeout": int(trigger.get("delivery_timeout_seconds") or 20),
    }


def run_powershell(script: str, timeout: int = 20) -> dict[str, Any]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    try:
        proc = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-3000:],
        "stderr": (proc.stderr or "")[-3000:],
    }


def resolve_codex_app_server_executable(config: dict[str, Any]) -> dict[str, Any]:
    settings = app_server_config(config)
    configured = str(settings.get("codex") or "").strip()
    candidates: list[tuple[str, Path]] = []
    if configured and configured.lower() != "codex":
        candidates.append(("config", Path(configured)))
    env_path = str(os.environ.get("CODEX_CLI_PATH") or "").strip()
    if env_path:
        candidates.append(("env:CODEX_CLI_PATH", Path(env_path)))
    codex_config = Path.home() / ".codex" / "config.toml"
    if codex_config.exists():
        try:
            text = codex_config.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"(?m)^\s*CODEX_CLI_PATH\s*=\s*['\"]([^'\"]+)['\"]", text)
            if match:
                candidates.append(("codex-config", Path(match.group(1))))
        except Exception:
            pass
    try:
        proc = subprocess.run(
            ["where.exe", "codex"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line:
                candidates.append(("where", Path(line)))
    except Exception:
        pass
    if sys.platform.startswith("win"):
        windows_apps = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WindowsApps"
        try:
            matches = sorted(
                windows_apps.glob(r"OpenAI.Codex_*\app\resources\codex.exe"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(("windowsapps", path) for path in matches)
        except Exception:
            pass
    seen: set[str] = set()
    checked: list[dict[str, str]] = []
    for source, path in candidates:
        raw = str(path)
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        exists = path.exists()
        checked.append({"source": source, "path": raw, "exists": str(bool(exists)).lower()})
        if exists:
            return {"ok": True, "path": raw, "source": source, "checked": checked}
    if configured:
        return {"ok": False, "path": configured, "source": "unresolved", "checked": checked}
    return {"ok": False, "path": "codex", "source": "unresolved", "checked": checked}


def codex_package_version_from_path(path: str) -> tuple[int, ...]:
    match = re.search(r"OpenAI\.Codex_([0-9]+(?:\.[0-9]+){1,3})_", str(path or ""))
    if not match:
        return ()
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return ()


def codex_app_server_owner_report(config: dict[str, Any]) -> dict[str, Any]:
    settings = app_server_config(config)
    host = str(settings["host"] or "127.0.0.1")
    port = int(settings["port"])
    listen = f"ws://{host}:{port}"
    ps_listen = listen.replace("'", "''")
    ps_host = host.replace("'", "''")
    executable = resolve_codex_app_server_executable(config)
    latest_path = str(executable.get("path") or "")
    latest_version = codex_package_version_from_path(latest_path)
    latest_version_text = ".".join(str(part) for part in latest_version)
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$hostName = '{ps_host}'
$port = {port}
$listen = '{ps_listen}'
$connections = @(Get-NetTCPConnection -LocalAddress $hostName -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
$owners = foreach ($connection in $connections) {{
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
  [pscustomobject]@{{
    pid = [int]$connection.OwningProcess
    path = if ($proc) {{ [string]$proc.ExecutablePath }} else {{ '' }}
    command_line = if ($proc) {{ [string]$proc.CommandLine }} else {{ '' }}
  }}
}}
$owners | ConvertTo-Json -Depth 4
"""
    result = run_powershell(script, timeout=8)
    try:
        parsed = json.loads(str(result.get("stdout") or "[]"))
    except Exception:
        parsed = []
    if isinstance(parsed, dict):
        owners: list[dict[str, Any]] = [parsed]
    elif isinstance(parsed, list):
        owners = [item for item in parsed if isinstance(item, dict)]
    else:
        owners = []

    normalized: list[dict[str, Any]] = []
    for owner in owners:
        path = str(owner.get("path") or "")
        command_line = str(owner.get("command_line") or "")
        owner_version = codex_package_version_from_path(path)
        is_codex_executable = (
            path.lower().endswith("\\codex.exe")
            or bool(re.search(r"(^|[\\\"' ])codex\.exe([\\\"' ]|$)", command_line, re.IGNORECASE))
        )
        is_codex_app_server = is_codex_executable and "app-server" in command_line and listen in command_line
        version_ok = True
        if latest_version and owner_version:
            version_ok = owner_version >= latest_version
        normalized.append(
            {
                "pid": owner.get("pid"),
                "path": path,
                "command_line": command_line,
                "version": ".".join(str(part) for part in owner_version),
                "is_codex_app_server": is_codex_app_server,
                "version_ok": version_ok,
                "healthy": bool(is_codex_app_server and version_ok),
            }
        )
    healthy = len(normalized) == 1 and bool(normalized[0].get("healthy"))
    return {
        "ok": bool(result.get("ok")),
        "host": host,
        "port": port,
        "listen": listen,
        "listening": bool(normalized),
        "healthy": healthy,
        "latest_executable": executable,
        "latest_version": latest_version_text,
        "owners": normalized,
        "reason": "" if healthy else ("not_listening" if not normalized else "app_server_owner_unhealthy"),
    }


def ensure_codex_app_server(config: dict[str, Any]) -> dict[str, Any]:
    settings = app_server_config(config)
    host = settings["host"]
    port = settings["port"]
    current = tcp_check(port, host=host)
    if current.get("ok"):
        owner = codex_app_server_owner_report(config)
        if owner.get("healthy"):
            return {"ok": True, "started": False, "host": host, "port": port, "owner": owner}
        stopped = stop_codex_app_server_listener(config, "app_server_owner_unhealthy")
        if int(stopped.get("stopped_count") or 0) <= 0:
            return {
                "ok": False,
                "started": False,
                "host": host,
                "port": port,
                "reason": "app_server_owner_unhealthy",
                "owner": owner,
                "stopped": stopped,
            }

    executable = resolve_codex_app_server_executable(config)
    if not executable.get("ok"):
        return {
            "ok": False,
            "started": False,
            "host": host,
            "port": port,
            "reason": "codex executable not found",
            "executable": executable,
        }
    command = [
        str(executable.get("path") or settings["codex"]),
        "app-server",
        "--listen",
        f"ws://{host}:{port}",
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(command, **kwargs)
    except Exception as exc:
        return {"ok": False, "started": False, "host": host, "port": port, "reason": str(exc), "executable": executable}

    for _ in range(30):
        time.sleep(0.2)
        check = tcp_check(port, host=host)
        owner = codex_app_server_owner_report(config) if check.get("ok") else {}
        if check.get("ok") and owner.get("healthy"):
            return {"ok": True, "started": True, "pid": proc.pid, "host": host, "port": port, "executable": executable, "owner": owner}
    return {"ok": False, "started": True, "pid": proc.pid, "host": host, "port": port, "reason": "app-server did not listen in time", "executable": executable}


def stop_codex_app_server_listener(config: dict[str, Any], reason: str = "") -> dict[str, Any]:
    """Stop only the bridge-owned app-server listener for the configured port."""
    settings = app_server_config(config)
    host = str(settings["host"] or "127.0.0.1")
    port = int(settings["port"])
    listen = f"ws://{host}:{port}"
    ps_listen = listen.replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Continue'
$listen = '{ps_listen}'
$targets = Get-CimInstance Win32_Process | Where-Object {{
  $_.Name -ieq 'codex.exe' -and
  $_.CommandLine -match 'app-server' -and
  $_.CommandLine -like "*$listen*"
}} | Select-Object ProcessId,ParentProcessId,Name,CommandLine
$stopped = @()
foreach ($target in $targets) {{
  try {{
    Stop-Process -Id $target.ProcessId -Force -ErrorAction Stop
    $stopped += [pscustomobject]@{{ process_id = $target.ProcessId; name = $target.Name; ok = $true }}
  }} catch {{
    $stopped += [pscustomobject]@{{ process_id = $target.ProcessId; name = $target.Name; ok = $false; error = $_.Exception.Message }}
  }}
}}
[pscustomobject]@{{
  ok = $true
  listen = $listen
  stopped_count = @($stopped | Where-Object {{ $_.ok }}).Count
  stopped = $stopped
}} | ConvertTo-Json -Depth 6
"""
    result = run_powershell(script, timeout=20)
    try:
        parsed = json.loads(str(result.get("stdout") or "{}"))
    except Exception:
        parsed = {"ok": False, "raw": result}
    if not isinstance(parsed, dict):
        parsed = {"ok": False, "raw": result}
    parsed["reason"] = reason
    return parsed


def restart_codex_app_server_for_mcp(config: dict[str, Any], reason: str = "mcp_transport_closed") -> dict[str, Any]:
    stopped = stop_codex_app_server_listener(config, reason)
    time.sleep(0.5)
    started = ensure_codex_app_server(config)
    return {
        "ok": bool(started.get("ok")),
        "reason": reason,
        "stopped": stopped,
        "started": started,
    }


def run_codex_app_server_client(
    config: dict[str, Any],
    args: list[str],
    prompt: str = "",
    timeout_extra_seconds: int = 0,
) -> dict[str, Any]:
    settings = app_server_config(config)
    script = settings["script"]
    if not script.exists():
        return {"ok": False, "healthy": False, "reason": f"codex app-server client not found: {script}"}
    command = [
        settings["node"],
        str(script),
        "--host",
        settings["host"],
        "--port",
        str(settings["port"]),
        "--timeout-ms",
        str(max(1000, settings["timeout"] * 1000)),
        *args,
    ]
    timeout_seconds = settings["timeout"] + timeout_extra_seconds
    if "--dispatch" in args:
        timeout_seconds = max(
            timeout_seconds,
            settings["timeout"] + app_server_turn_materialization_grace_seconds(config) + 15,
        )
    try:
        proc = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return {"ok": False, "healthy": False, "reason": f"codex app-server client failed: {exc}"}
    try:
        parsed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        parsed = {"ok": False, "raw_stdout": (proc.stdout or "")[-2000:]}
    parsed["returncode"] = proc.returncode
    if proc.stderr:
        parsed["stderr"] = proc.stderr[-2000:]
    return parsed


def create_codex_thread_app_server(config: dict[str, Any], thread_name: str) -> dict[str, Any]:
    startup = ensure_codex_app_server(config)
    if not startup.get("ok"):
        result = dict(startup)
        result.update({"ok": False, "mode": "codex-app-server"})
        return result
    args = [
        "--create-thread",
        "--cwd",
        str(PROJECT_ROOT),
    ]
    if thread_name:
        args.extend(["--thread-name", thread_name])
    parsed = run_codex_app_server_client(config, args, timeout_extra_seconds=20)
    parsed["startup"] = startup
    parsed.setdefault("mode", "codex-app-server")
    return parsed


def inspect_codex_thread_app_server(
    config: dict[str, Any],
    thread_id: str,
    thread_name: str = "",
    stabilize_name: bool = False,
    light: bool = False,
) -> dict[str, Any]:
    thread_id = str(thread_id or "").strip()
    if not thread_id:
        return {"ok": False, "healthy": False, "reason": "thread_id is required"}
    startup = ensure_codex_app_server(config)
    if not startup.get("ok"):
        result = dict(startup)
        result.update({"ok": False, "healthy": False, "mode": "codex-app-server"})
        return result
    args = [
        "--inspect-thread",
        "--thread-id",
        thread_id,
        "--cwd",
        str(PROJECT_ROOT),
    ]
    if stabilize_name and thread_name:
        args.extend(["--thread-name", thread_name])
    if light:
        args.append("--light-inspect")
    parsed = run_codex_app_server_client(config, args, timeout_extra_seconds=20)
    parsed["startup"] = startup
    parsed.setdefault("mode", "codex-app-server")
    parsed.setdefault("thread_id", thread_id)
    return parsed


def inspect_codex_thread_for_dispatch(
    config: dict[str, Any],
    thread_id: str,
    thread_name: str = "",
) -> dict[str, Any]:
    light_probe = inspect_codex_thread_app_server(config, thread_id, thread_name, light=True)
    status_type = str((light_probe.get("listed_status") or {}).get("type") or "").lower()
    if not bool(light_probe.get("ok")) or not bool(light_probe.get("listed")):
        full_probe = inspect_codex_thread_app_server(
            config,
            thread_id,
            thread_name,
            stabilize_name=bool(thread_name),
            light=False,
        )
        if bool(full_probe.get("ok")):
            return full_probe
    if bool(light_probe.get("ok")) and bool(light_probe.get("listed")) and status_type in {"notloaded", "loading", "unloaded"}:
        return light_probe
    return light_probe


def desktop_sync_check_app_server(
    config: dict[str, Any],
    thread_id: str,
    expected_task_ids: list[str] | None = None,
) -> dict[str, Any]:
    thread_id = str(thread_id or "").strip()
    if not thread_id:
        return {"ok": False, "healthy": False, "reason": "thread_id is required"}
    startup = ensure_codex_app_server(config)
    if not startup.get("ok"):
        result = dict(startup)
        result.update({"ok": False, "healthy": False, "mode": "codex-app-server"})
        return result
    task_ids = [str(item).strip() for item in (expected_task_ids or []) if str(item).strip()]
    args = [
        "--sync-check",
        "--thread-id",
        thread_id,
        "--cwd",
        str(PROJECT_ROOT),
    ]
    if task_ids:
        args.extend(["--expected-task-ids", ",".join(task_ids)])
    parsed = run_codex_app_server_client(config, args, timeout_extra_seconds=20)
    parsed["startup"] = startup
    parsed.setdefault("mode", "codex-app-server")
    parsed.setdefault("thread_id", thread_id)
    parsed.setdefault("expected_task_ids", task_ids)
    return parsed
