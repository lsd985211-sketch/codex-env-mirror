"""Codex Desktop CDP route discovery and recovery planning.

Owns: visible Codex Desktop CDP endpoint discovery, runtime endpoint cache,
CDP quick health checks, startup script contract checks, and read-only recovery
plans for the visible CDP route.
Non-goals: mobile queue mutation, result polling semantics, final reply sending,
route switching, or GUI/OCR health checks.
State behavior: endpoint discovery may update the runtime CDP endpoint cache;
quick checks and recovery plans are read-only apart from that cache authority.
Normal callers: mobile_openclaw_cli dispatch, health checks, tool-health CLI,
and CDP regression checks.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli_utils import parse_iso_datetime
from health_checks import http_json, tcp_check
from mobile_maintenance import os_port_listener_state

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
CDP_ENDPOINT_STATE = ROOT / "runtime" / "codex_cdp_endpoint.json"


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

def codex_cdp_config(config: dict[str, Any]) -> dict[str, Any]:
    trigger = config.get("trigger", {})
    start_script_value = str(trigger.get("codex_cdp_start_script") or "").strip()
    start_scripts = []
    if start_script_value:
        start_scripts.append(Path(start_script_value))
    else:
        default_start_script = Path.home() / ".codex" / "scripts" / "start-codex-desktop-elevated.ps1"
        if default_start_script.exists():
            start_scripts.append(default_start_script)
    host = str(trigger.get("codex_cdp_host") or "localhost").strip() or "localhost"
    preferred_port = int(trigger.get("codex_cdp_port") or 9229)
    endpoint = resolve_codex_cdp_endpoint(host, preferred_port)
    return {
        "host": endpoint["host"],
        "port": endpoint["port"],
        "preferred_host": host,
        "preferred_port": preferred_port,
        "endpoint_source": endpoint["source"],
        "endpoint_state": endpoint.get("state") or {},
        "node": str(trigger.get("node_path") or "node"),
        "script": Path(
            trigger.get("codex_cdp_script")
            or PROJECT_ROOT / "_tools" / "codex-cdp-tools" / "codex_cdp_send.js"
        ),
        "timeout": int(trigger.get("delivery_timeout_seconds") or 20),
        "start_timeout": max(10, int(trigger.get("codex_cdp_start_timeout_seconds") or 20)),
        "start_scripts": start_scripts,
    }


def load_codex_cdp_endpoint_state() -> dict[str, Any]:
    if not CDP_ENDPOINT_STATE.exists():
        return {}
    try:
        data = json.loads(CDP_ENDPOINT_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_codex_cdp_endpoint_state(host: str, port: int, source: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    state = {
        "host": host,
        "port": int(port),
        "source": source,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        state.update(extra)
    CDP_ENDPOINT_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CDP_ENDPOINT_STATE.with_suffix(CDP_ENDPOINT_STATE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CDP_ENDPOINT_STATE)
    return state


def cdp_discovery_cooldown_active(
    state: dict[str, Any],
    preferred_host: str,
    preferred_port: int,
    seconds: int = 30,
) -> bool:
    if str(state.get("source") or "") != "discovery-failed":
        return False
    if str(state.get("preferred_host") or "") != str(preferred_host or ""):
        return False
    try:
        state_preferred_port = int(state.get("preferred_port") or 0)
    except Exception:
        return False
    if state_preferred_port != int(preferred_port):
        return False
    parsed = parse_iso_datetime(str(state.get("verified_at") or ""))
    if not parsed:
        return False
    return (datetime.now(timezone.utc) - parsed).total_seconds() < seconds


def codex_cdp_endpoint_ready(host: str, port: int, timeout: float = 0.35) -> dict[str, Any]:
    tcp = tcp_check(int(port), host=host, timeout=timeout)
    version: dict[str, Any] = {"ok": False, "reason": "transport_not_ready"}
    if tcp.get("ok"):
        version = http_json("/json/version", int(port), host=host, timeout=timeout)
    return {
        "ok": bool(tcp.get("ok")) and bool(version.get("ok")),
        "host": host,
        "port": int(port),
        "tcp": tcp,
        "version": version,
    }


def cdp_route_quick_check(config: dict[str, Any]) -> dict[str, Any]:
    """Read-only fast CDP route probe for routine validation."""
    settings = codex_cdp_config(config)
    host = str(settings.get("host") or "localhost")
    port = int(settings.get("port") or 9229)
    timeout = max(0.1, min(2.0, float(config.get("trigger", {}).get("codex_cdp_probe_timeout_seconds") or 0.35)))
    ready = codex_cdp_endpoint_ready(host, port, timeout=timeout)
    os_state = os_port_listener_state(port)
    version = ready.get("version") or {}
    return {
        "ok": bool(ready.get("ok")),
        "host": host,
        "port": port,
        "preferred_host": str(settings.get("preferred_host") or ""),
        "preferred_port": int(settings.get("preferred_port") or 0),
        "endpoint_source": str(settings.get("endpoint_source") or ""),
        "endpoint_state": settings.get("endpoint_state") or {},
        "timeout_seconds": timeout,
        "tcp_ok": bool((ready.get("tcp") or {}).get("ok")),
        "version_ok": bool(version.get("ok")),
        "version_reason": str(version.get("reason") or version.get("error") or ""),
        "live_listeners": int(os_state.get("live_count") or 0),
        "stale_listeners": int(os_state.get("stale_count") or 0),
        "listener_count": int(os_state.get("listener_count") or 0),
        "os_port_state": os_state,
        "assertion": "visible desktop CDP route is usable only when tcp and /json/version both respond",
    }


def codex_desktop_cdp_process_ports() -> list[int]:
    """Discover remote-debugging ports from currently running Codex Desktop processes."""
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -match '^Codex(\.exe)?$' -and
    $_.CommandLine -match '--remote-debugging-port=(\d+)'
  } |
  ForEach-Object {
    if ($_.CommandLine -match '--remote-debugging-port=(\d+)') {
      [pscustomobject]@{ pid = [int]$_.ProcessId; port = [int]$Matches[1]; command_line = $_.CommandLine }
    }
  } |
  ConvertTo-Json -Depth 4
"""
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return []
        parsed = json.loads(raw)
    except Exception:
        return []
    items = parsed if isinstance(parsed, list) else [parsed]
    ports: list[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            port = int(item.get("port") or 0)
        except Exception:
            continue
        if 0 < port < 65536 and port not in ports:
            ports.append(port)
    return ports


def cdp_candidate_ports(preferred_port: int, state_port: int = 0) -> list[int]:
    raw_ports: list[Any] = [
        preferred_port,
        state_port,
        os.environ.get("CODEX_CDP_PORT"),
        *codex_desktop_cdp_process_ports(),
        9230,
        9229,
        9222,
        9223,
    ]
    ports: list[int] = []
    for item in raw_ports:
        try:
            port = int(item or 0)
        except Exception:
            continue
        if 0 < port < 65536 and port not in ports:
            ports.append(port)
    return ports


def discover_codex_cdp_endpoint(preferred_host: str, preferred_port: int, state_port: int = 0) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    process_ports = codex_desktop_cdp_process_ports()
    for port in cdp_candidate_ports(preferred_port, state_port):
        for host in dict.fromkeys([preferred_host, "localhost", "127.0.0.1"]):
            ready = codex_cdp_endpoint_ready(str(host or "localhost"), port)
            probes.append(
                {
                    "host": ready.get("host"),
                    "port": ready.get("port"),
                    "ok": bool(ready.get("ok")),
                    "tcp_ok": bool((ready.get("tcp") or {}).get("ok")),
                    "version_ok": bool((ready.get("version") or {}).get("ok")),
                    "version_reason": str((ready.get("version") or {}).get("reason") or ""),
                }
            )
            if ready.get("ok"):
                source = "codex-process-discovery" if int(ready["port"]) in process_ports else "discovered"
                state = write_codex_cdp_endpoint_state(
                    str(ready["host"]),
                    int(ready["port"]),
                    source,
                    {
                        "preferred_host": preferred_host,
                        "preferred_port": int(preferred_port),
                        "probe_count": len(probes),
                        "version": ready.get("version") or {},
                        "process_ports": process_ports,
                    },
                )
                state.update({
                    "host": str(ready["host"]),
                    "port": int(ready["port"]),
                    "source": source,
                    "preferred_host": preferred_host,
                    "preferred_port": int(preferred_port),
                    "probe_count": len(probes),
                    "version": ready.get("version") or {},
                    "process_ports": process_ports,
                })
                return {
                    "host": str(ready["host"]),
                    "port": int(ready["port"]),
                    "source": source,
                    "state": state,
                    "probes": probes,
                }
    state = write_codex_cdp_endpoint_state(
        preferred_host,
        int(process_ports[0] if process_ports else preferred_port),
        "discovery-failed",
        {
            "preferred_host": preferred_host,
            "preferred_port": int(preferred_port),
            "probe_count": len(probes),
            "process_ports": process_ports,
        },
    )
    return {
        "host": preferred_host,
        "port": int(process_ports[0] if process_ports else preferred_port),
        "source": "codex-process-discovery-unready" if process_ports else "preferred-config",
        "state": state,
        "probes": probes,
        "process_ports": process_ports,
    }


def resolve_codex_cdp_endpoint(preferred_host: str, preferred_port: int) -> dict[str, Any]:
    state = load_codex_cdp_endpoint_state()
    state_host = str(state.get("host") or "").strip()
    try:
        state_port = int(state.get("port") or 0)
    except Exception:
        state_port = 0
    if state_host and state_port > 0:
        ready = codex_cdp_endpoint_ready(state_host, state_port)
        if ready.get("ok"):
            return {"host": state_host, "port": state_port, "source": "runtime-state", "state": state}
    preferred_ready = codex_cdp_endpoint_ready(preferred_host, preferred_port)
    if preferred_ready.get("ok"):
        return {"host": preferred_host, "port": int(preferred_port), "source": "preferred-config", "state": state}
    process_ports = codex_desktop_cdp_process_ports()
    if cdp_discovery_cooldown_active(state, preferred_host, preferred_port) and not process_ports:
        return {
            "host": preferred_host,
            "port": int(preferred_port),
            "source": "preferred-config",
            "state": state,
            "discovery_skipped": "cooldown",
        }
    return discover_codex_cdp_endpoint(preferred_host, preferred_port, state_port)


def ensure_codex_cdp(config: dict[str, Any]) -> dict[str, Any]:
    settings = codex_cdp_config(config)
    host = settings["host"]
    port = settings["port"]
    probe_timeout = max(0.2, float(config.get("trigger", {}).get("codex_cdp_probe_timeout_seconds") or 0.35))
    current = tcp_check(port, host=host, timeout=probe_timeout)
    current_version: dict[str, Any] = {"ok": False, "reason": "transport_not_checked"}
    os_port_state = os_port_listener_state(port)
    if (
        not current.get("ok")
        and int(os_port_state.get("stale_count") or 0) > 0
        and int(os_port_state.get("live_count") or 0) == 0
    ):
        return {
            "ok": False,
            "started": False,
            "host": host,
            "port": port,
            "launches": [],
            "version": current_version,
            "transport_ready": False,
            "version_ready": False,
            "reason": "codex_cdp_stale_os_listener",
            "os_port_state": os_port_state,
            "manual_action": "Restart Codex Desktop or clear the stale TCP listener before retrying the visible CDP route.",
        }
    if current.get("ok"):
        current_version = http_json("/json/version", port, host=host, timeout=probe_timeout)
    if current.get("ok"):
        result = {"ok": True, "started": False, "host": host, "port": port, "version": current_version, "transport_ready": True}
        if bool(current_version.get("ok")):
            result["version_ready"] = True
        else:
            result["version_ready"] = False
            result["version_reason"] = str(current_version.get("reason") or current_version.get("error") or "")
        return result

    live_listener_count = int(os_port_state.get("live_count") or 0)
    if live_listener_count > 0:
        return {
            "ok": True,
            "started": False,
            "host": host,
            "port": port,
            "launches": [],
            "version": current_version,
            "transport_ready": True,
            "version_ready": False,
            "reason": "codex_cdp_probe_unstable_live_listener",
            "os_port_state": os_port_state,
            "probe": current,
        }

    if bool(config.get("trigger", {}).get("codex_cdp_no_start")):
        return {
            "ok": False,
            "started": False,
            "host": host,
            "port": port,
            "launches": [],
            "version": current_version,
            "transport_ready": False,
            "version_ready": False,
            "reason": "codex_cdp_transport_not_ready",
            "no_start": True,
        }

    launches: list[dict[str, Any]] = []
    for candidate in settings["start_scripts"]:
        if not candidate.exists():
            continue
        candidate_path = str(candidate).replace("'", "''")
        launch = run_powershell(
            f"$env:CODEX_CDP_PORT='{int(port)}'; & '{candidate_path}'",
            timeout=settings["start_timeout"],
        )
        launch = {"script": str(candidate), **launch}
        launches.append(launch)
        deadline = time.time() + settings["start_timeout"]
        while time.time() < deadline:
            current = tcp_check(port, host=host, timeout=probe_timeout)
            current_version = {"ok": False, "reason": "transport_not_ready"}
            if current.get("ok"):
                current_version = http_json("/json/version", port, host=host, timeout=probe_timeout)
            if current.get("ok"):
                if current_version.get("ok"):
                    write_codex_cdp_endpoint_state(
                        host,
                        port,
                        "ensure_codex_cdp",
                        {
                            "preferred_host": settings.get("preferred_host"),
                            "preferred_port": settings.get("preferred_port"),
                            "version": current_version,
                        },
                    )
                return {
                    "ok": True,
                    "started": True,
                    "host": host,
                    "port": port,
                    "version": current_version,
                    "transport_ready": True,
                    "version_ready": bool(current_version.get("ok")),
                    "launch": launch,
                    "launches": launches,
                }
            time.sleep(0.25)

    return {
        "ok": False,
        "started": bool(launches),
        "host": host,
        "port": port,
        "launches": launches,
        "version": current_version,
        "transport_ready": bool(current.get("ok")),
        "version_ready": bool(current_version.get("ok")),
        "reason": "codex_cdp_transport_not_ready" if not current.get("ok") else "codex_cdp_version_not_ready",
        "endpoint_source": settings.get("endpoint_source"),
        "preferred_host": settings.get("preferred_host"),
        "preferred_port": settings.get("preferred_port"),
    }


def check_codex_health_cdp(config: dict[str, Any]) -> dict[str, Any]:
    startup = ensure_codex_cdp(config)
    if not startup.get("ok"):
        result = dict(startup)
        result.update({"ok": False, "healthy": False, "mode": "codex-cdp"})
        return result
    settings = codex_cdp_config(config)
    command = [settings["node"], str(settings["script"]), "--host", settings["host"], "--port", str(settings["port"]), "--check-health"]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=settings["timeout"],
            cwd=str(PROJECT_ROOT / "_tools" / "codex-cdp-tools"),
        )
    except Exception as exc:
        return {"ok": False, "healthy": False, "reason": f"check-health failed: {exc}"}
    try:
        parsed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        parsed = {"ok": False, "healthy": False, "raw_stdout": proc.stdout}
    parsed["returncode"] = proc.returncode
    parsed["startup"] = startup
    parsed.setdefault("mode", "codex-cdp")
    return parsed


def netstat_port_listener_state(port: int) -> dict[str, Any]:
    """Read-only fallback listener inspection that does not depend on CIM/WMI."""
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception as exc:
        return {"ok": False, "port": int(port), "reason": str(exc), "listeners": []}

    listeners: list[dict[str, Any]] = []
    for raw_line in (proc.stdout or "").splitlines():
        line = raw_line.strip()
        if not line.upper().startswith("TCP"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_address = parts[1]
        state = parts[3].upper()
        pid_text = parts[4]
        if state != "LISTENING":
            continue
        try:
            local_port = int(local_address.rsplit(":", 1)[1])
        except Exception:
            continue
        if local_port != int(port):
            continue
        listener = {
            "local_address": local_address,
            "local_port": local_port,
            "pid": pid_text,
            "state": state,
            "process_name": "",
            "process_exists": False,
        }
        try:
            ps_proc = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f"Get-Process -Id {int(pid_text)} -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty ProcessName",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            name = (ps_proc.stdout or "").strip()
            listener["process_name"] = name
            listener["process_exists"] = bool(name)
        except Exception:
            pass
        listeners.append(listener)
    return {
        "ok": proc.returncode == 0,
        "port": int(port),
        "listener_count": len(listeners),
        "live_count": sum(1 for item in listeners if item.get("process_exists")),
        "stale_count": sum(1 for item in listeners if not item.get("process_exists")),
        "listeners": listeners,
        "returncode": proc.returncode,
        "stderr": (proc.stderr or "")[-1200:],
    }


def cdp_start_script_contract(start_script: Path, expected_port: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(start_script),
        "exists": start_script.exists(),
        "uses_codex_cdp_port_env": False,
        "has_remote_debugging_arg": False,
        "default_ports": [],
        "default_matches_expected": False,
    }
    if not start_script.exists():
        result["reason"] = "missing"
        return result
    try:
        text = start_script.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as exc:
        result["reason"] = f"read_failed: {exc}"
        return result
    defaults: list[int] = []
    for match in re.finditer(r"remoteDebuggingPort\s*=\s*(\d+)", text, flags=re.IGNORECASE):
        try:
            defaults.append(int(match.group(1)))
        except Exception:
            pass
    for match in re.finditer(r"falling back to\s+(\d+)", text, flags=re.IGNORECASE):
        try:
            defaults.append(int(match.group(1)))
        except Exception:
            pass
    result.update(
        {
            "uses_codex_cdp_port_env": "CODEX_CDP_PORT" in text,
            "has_remote_debugging_arg": "--remote-debugging-port" in text,
            "default_ports": sorted(set(defaults)),
            "default_matches_expected": int(expected_port) in set(defaults),
        }
    )
    return result


def cdp_startup_contract_check(config: dict[str, Any]) -> dict[str, Any]:
    """Read-only CDP startup contract and endpoint authority check."""
    trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
    preferred_host = str(trigger.get("codex_cdp_host") or "localhost").strip() or "localhost"
    preferred_port = int(trigger.get("codex_cdp_port") or 9229)
    state = load_codex_cdp_endpoint_state()
    configured_start_script = str(trigger.get("codex_cdp_start_script") or "").strip()
    start_script = (
        Path(configured_start_script).expanduser()
        if configured_start_script
        else Path.home() / ".codex" / "scripts" / "start-codex-desktop-elevated.ps1"
    )
    contract = cdp_start_script_contract(start_script, preferred_port)

    env_port = os.environ.get("CODEX_CDP_PORT")
    process_ports = codex_desktop_cdp_process_ports()
    candidates = cdp_candidate_ports(preferred_port, int(state.get("port") or 0) if isinstance(state, dict) else 0)
    endpoint_probes = []
    listener_states = []
    usable_endpoints = []
    for port in candidates:
        for host in dict.fromkeys([preferred_host, "localhost", "127.0.0.1"]):
            ready = codex_cdp_endpoint_ready(str(host or "localhost"), port, timeout=0.35)
            endpoint_probes.append(
                {
                    "host": ready.get("host"),
                    "port": ready.get("port"),
                    "ok": bool(ready.get("ok")),
                    "tcp_ok": bool((ready.get("tcp") or {}).get("ok")),
                    "version_ok": bool((ready.get("version") or {}).get("ok")),
                    "version_reason": str((ready.get("version") or {}).get("reason") or ""),
                }
            )
            if ready.get("ok"):
                usable_endpoints.append({"host": str(ready.get("host") or ""), "port": int(ready.get("port") or 0)})
        listener_states.append({"port": port, "netstat": netstat_port_listener_state(port)})

    issues: list[dict[str, Any]] = []
    if not contract.get("exists"):
        issues.append({"severity": "high", "code": "start_script_missing", "detail": "CDP recovery start script is missing."})
    if contract.get("exists") and not contract.get("uses_codex_cdp_port_env"):
        issues.append({"severity": "high", "code": "start_script_ignores_env", "detail": "Start script does not read CODEX_CDP_PORT."})
    if contract.get("exists") and not contract.get("default_matches_expected"):
        issues.append(
            {
                "severity": "medium",
                "code": "start_script_default_port_mismatch",
                "detail": f"Start script default ports {contract.get('default_ports')} do not include configured {preferred_port}.",
            }
        )
    if not usable_endpoints:
        issues.append({"severity": "high", "code": "no_usable_cdp_endpoint", "detail": "No candidate endpoint returned /json/version."})
    if env_port and str(env_port).strip() != str(preferred_port):
        issues.append({"severity": "medium", "code": "process_env_port_mismatch", "detail": f"CODEX_CDP_PORT={env_port}, configured={preferred_port}."})
    if process_ports and preferred_port not in process_ports:
        issues.append(
            {
                "severity": "medium",
                "code": "codex_process_port_mismatch",
                "detail": f"Running Codex Desktop declares CDP ports {process_ports}, configured={preferred_port}.",
            }
        )
    if isinstance(state, dict) and state.get("source") == "discovery-failed":
        issues.append({"severity": "low", "code": "recent_discovery_failed_cache", "detail": "Runtime endpoint cache records recent discovery failure."})

    high_count = sum(1 for item in issues if item.get("severity") == "high")
    return {
        "ok": high_count == 0,
        "read_only": True,
        "preferred": {"host": preferred_host, "port": preferred_port},
        "env": {"CODEX_CDP_PORT": env_port or ""},
        "process_ports": process_ports,
        "runtime_state": state,
        "start_script_contract": contract,
        "candidate_ports": candidates,
        "endpoint_probes": endpoint_probes,
        "usable_endpoints": usable_endpoints,
        "listener_states": listener_states,
        "issues": issues,
        "manual_boundary": "diagnostic only; does not start/stop Codex Desktop, switch routes, delete tasks, or send replies",
        "recommended_next_action": (
            "If no usable endpoint exists, use an explicitly approved controlled recovery flow: "
            "inspect current Codex Desktop instance, then restart via start-codex-desktop-elevated.ps1 with CODEX_CDP_PORT."
        ),
    }


def cdp_recovery_plan(config: dict[str, Any]) -> dict[str, Any]:
    """Read-only recovery plan for the visible CDP route."""
    contract = cdp_startup_contract_check(config)
    preferred = contract.get("preferred") if isinstance(contract.get("preferred"), dict) else {}
    start_script = Path.home() / ".codex" / "scripts" / "start-codex-desktop-elevated.ps1"
    steps = [
        "Confirm the visible-route CDP endpoint is absent or unhealthy with cdp-startup-contract-check.",
        "Inspect the current Codex Desktop instance and verify there is no live CDP listener on the configured port.",
        f"If recovery is approved, launch {start_script} with CODEX_CDP_PORT={preferred.get('port') or 9229}.",
        "Re-run cdp-route-doctor-check and cdp-visible-delivery-check after the desktop comes back.",
        "Keep backup accounts on the app-server route; do not silently switch the primary account route.",
    ]
    return {
        "ok": bool(contract.get("ok")),
        "read_only": True,
        "preferred": preferred,
        "contract": contract,
        "plan": steps,
        "apply_boundary": "No process start, stop, kill, or route switch will be executed by this command.",
    }

