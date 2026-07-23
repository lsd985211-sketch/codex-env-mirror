#!/usr/bin/env python3
"""Read-only local performance doctor for Codex/bridge workstations."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
HIDDEN_STARTUPINFO = None
if sys.platform == "win32":
    HIDDEN_STARTUPINFO = subprocess.STARTUPINFO()
    HIDDEN_STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    HIDDEN_STARTUPINFO.wShowWindow = 0
MAX_OBSERVE_SECONDS = 120.0
PROFILES = {"quick", "standard", "deep"}
AGGREGATE_WINDOWS = {
    "quick": 2,
    "standard": 3,
    "deep": 3,
}
TRANSIENT_CPU_RISK_CLASSES = {"mcp_resource", "runtime_helper", "browser_webview"}
CODEX_AUTOMATION_WORKER_KEYWORDS = {
    "codex_reporter.py worker",
    "email_scheduler.py",
    "codex_scheduler_runner.py",
}
QUICK_COMMAND_LINE_PROCESS_HINTS = {
    "bridge",
    "bun",
    "chatgpt",
    "chrome",
    "cmd",
    "codegraph",
    "codex",
    "deno",
    "edge",
    "electron",
    "java",
    "markitdown",
    "mcp",
    "node",
    "openclaw",
    "playwright",
    "powershell",
    "pwsh",
    "python",
    "uv",
    "webview",
}
CODEX_CFA_DOCUMENTS_PATH = Path.home() / "Documents" / "Codex"
NVIDIA_WRITE_HINT_PATHS = [
    Path(r"C:\ProgramData\NVIDIA Corporation\nvtopps\nct\nvlog.nvlgstg"),
    Path(r"C:\ProgramData\NVIDIA Corporation\ShadowPlay\CaptureCore.log"),
    Path(r"C:\ProgramData\NVIDIA Corporation\Drs\nvAppTimestamps"),
    Path(r"C:\ProgramData\NVIDIA Corporation\NVIDIA App\UXD\Log.nvcontainer.exe.log"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_powershell_json(script: str, timeout: int = 30) -> dict[str, Any]:
    wrapped_script = "\n".join(
        [
            "$ProgressPreference = 'SilentlyContinue'",
            "$InformationPreference = 'SilentlyContinue'",
            "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)",
            script,
        ]
    )
    encoded_command = base64.b64encode(wrapped_script.encode("utf-16le")).decode("ascii")
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-EncodedCommand",
        encoded_command,
    ]
    run_kwargs: dict[str, Any] = dict(NO_WINDOW_KW)
    if HIDDEN_STARTUPINFO is not None:
        run_kwargs["startupinfo"] = HIDDEN_STARTUPINFO
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **run_kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "items": [],
        }
    text = (proc.stdout or "").strip()
    try:
        parsed = json.loads(text) if text else []
    except json.JSONDecodeError:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "stdout": text[:2000],
            "stderr": (proc.stderr or "").strip()[:2000],
            "error": "powershell_json_parse_failed",
            "items": [],
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "items": parsed if isinstance(parsed, list) else [parsed],
        "stderr": (proc.stderr or "").strip()[:2000],
    }


def process_rows() -> list[dict[str, Any]]:
    rows = process_rows_psutil()
    if rows:
        return rows
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$all = @{}
foreach ($p in Get-CimInstance Win32_Process) {
  $all[[int]$p.ProcessId] = $p
}
$rows = foreach ($proc in $all.Values) {
  $gp = Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue
  $parent = $all[[int]$proc.ParentProcessId]
  [pscustomobject]@{
    pid = [int]$proc.ProcessId
    parent_pid = [int]$proc.ParentProcessId
    parent_name = if ($parent) { [string]$parent.Name } else { '' }
    name = [string]$proc.Name
    command_line = [string]$proc.CommandLine
    working_set_mb = if ($gp) { [math]::Round($gp.WorkingSet64 / 1MB, 1) } else { 0 }
    private_memory_mb = if ($gp) { [math]::Round($gp.PrivateMemorySize64 / 1MB, 1) } else { 0 }
    cpu_seconds = if ($gp -and $null -ne $gp.CPU) { [math]::Round($gp.CPU, 3) } else { 0 }
    start_time = if ($gp -and $gp.StartTime) { $gp.StartTime.ToString('o') } else { '' }
    sampling_backend = 'hidden_powershell'
  }
}
$rows | ConvertTo-Json -Depth 4
"""
    observed = run_powershell_json(script, timeout=25)
    return observed.get("items") if isinstance(observed.get("items"), list) else []


def quick_command_line_required(process_name: Any) -> bool:
    name = str(process_name or "").strip().lower()
    return any(hint in name for hint in QUICK_COMMAND_LINE_PROCESS_HINTS)


def process_rows_psutil(*, include_parent: bool = True, command_line_scope: str = "all") -> list[dict[str, Any]]:
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception:
        return []

    process_attrs = ["pid", "name", "create_time"]
    if include_parent:
        process_attrs.append("ppid")
    if command_line_scope == "all":
        process_attrs.extend(["cmdline", "exe"])

    raw_rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter(process_attrs):
        try:
            info = proc.info
            pid = int(info.get("pid") or 0)
            name = str(info.get("name") or "")
            if pid == 0 or name.lower() == "system idle process":
                continue
            memory = proc.memory_info()
            cpu_times = proc.cpu_times()
            try:
                io = proc.io_counters()
            except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
                io = None
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        cmdline = info.get("cmdline") if command_line_scope == "all" else None
        executable = info.get("exe") if command_line_scope == "all" else None
        if command_line_scope == "relevant" and quick_command_line_required(name):
            try:
                cmdline = proc.cmdline()
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                cmdline = None
            if not cmdline:
                try:
                    executable = proc.exe()
                except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                    executable = None
        if isinstance(cmdline, list) and cmdline:
            command_line = " ".join(str(part) for part in cmdline)
        else:
            command_line = str(executable or "")
        start_time = ""
        if info.get("create_time"):
            try:
                start_time = datetime.fromtimestamp(float(info["create_time"]), timezone.utc).isoformat()
            except (OSError, TypeError, ValueError):
                start_time = ""
        io_read = int(getattr(io, "read_bytes", 0) or 0) if io else 0
        io_write = int(getattr(io, "write_bytes", 0) or 0) if io else 0
        raw_rows.append(
            {
                "pid": pid,
                "parent_pid": int(info.get("ppid") or 0) if include_parent else 0,
                "parent_name": "",
                "name": name,
                "command_line": command_line,
                "working_set_mb": round(float(memory.rss or 0) / 1024 / 1024, 1),
                "private_memory_mb": round(float(getattr(memory, "private", memory.rss) or 0) / 1024 / 1024, 1),
                "cpu_seconds": round(float(cpu_times.user or 0) + float(cpu_times.system or 0), 3),
                "start_time": start_time,
                "io_read_bytes": io_read,
                "io_write_bytes": io_write,
                "io_data_bytes": io_read + io_write,
                "sampling_backend": "psutil",
            }
        )
    if include_parent:
        by_pid = {int(row.get("pid") or 0): row for row in raw_rows}
        for row in raw_rows:
            parent = by_pid.get(int(row.get("parent_pid") or 0))
            row["parent_name"] = str(parent.get("name") or "") if parent else ""
    return raw_rows


def process_rows_light() -> list[dict[str, Any]]:
    rows = process_rows_psutil(include_parent=False, command_line_scope="relevant")
    if rows:
        return rows
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-Process | ForEach-Object {
  [pscustomobject]@{
    pid = [int]$_.Id
    parent_pid = 0
    parent_name = ''
    name = [string]$_.ProcessName
    command_line = [string]$_.Path
    working_set_mb = [math]::Round($_.WorkingSet64 / 1MB, 1)
    private_memory_mb = [math]::Round($_.PrivateMemorySize64 / 1MB, 1)
    cpu_seconds = if ($null -ne $_.CPU) { [math]::Round($_.CPU, 3) } else { 0 }
    start_time = try { $_.StartTime.ToString('o') } catch { '' }
    sampling_backend = 'hidden_powershell'
  }
} | ConvertTo-Json -Depth 3
"""
    observed = run_powershell_json(script, timeout=15)
    return observed.get("items") if isinstance(observed.get("items"), list) else []


def disk_io_rows() -> list[dict[str, Any]]:
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_PerfFormattedData_PerfProc_Process |
  Where-Object { $_.Name -ne '_Total' -and $_.Name -ne 'Idle' } |
  Select-Object @{n='name';e={$_.Name}},
    @{n='pid';e={[int]$_.IDProcess}},
    @{n='io_read_bytes_per_sec';e={[double]$_.IOReadBytesPersec}},
    @{n='io_write_bytes_per_sec';e={[double]$_.IOWriteBytesPersec}},
    @{n='io_data_bytes_per_sec';e={[double]$_.IODataBytesPersec}},
    @{n='sampling_backend';e={'hidden_powershell'}} |
  ConvertTo-Json -Depth 3
"""
    observed = run_powershell_json(script, timeout=20)
    return observed.get("items") if isinstance(observed.get("items"), list) else []


def defender_exclusion_snapshot() -> dict[str, Any]:
    try:
        from defender_governance import snapshot as defender_snapshot

        payload = defender_snapshot()
    except Exception as exc:
        return {"ok": False, "read_ok": False, "error": f"{type(exc).__name__}: {exc}"}
    required = payload.get("required_exclusion_paths") if isinstance(payload.get("required_exclusion_paths"), list) else []
    missing = [item for item in required if isinstance(item, dict) and item.get("exists") and not item.get("excluded")]
    pref = payload.get("preferences") if isinstance(payload.get("preferences"), dict) else {}
    return {
        "ok": payload.get("ok") is True and not missing,
        "read_ok": payload.get("ok") is True,
        "required": required,
        "missing_existing_paths": missing,
        "exclusion_count": len(pref.get("exclusionPath") or []) if isinstance(pref.get("exclusionPath"), list) else 0,
        "dry_run_contract": {
            "changes_defender": False,
            "writes_files": False,
        },
    }


def defender_cfa_snapshot() -> dict[str, Any]:
    try:
        from defender_governance import snapshot as defender_snapshot

        payload = defender_snapshot()
    except Exception as exc:
        return {"ok": False, "read_ok": False, "error": f"{type(exc).__name__}: {exc}"}
    pref = payload.get("preferences") if isinstance(payload.get("preferences"), dict) else {}
    required_apps = payload.get("required_cfa_applications") if isinstance(payload.get("required_cfa_applications"), list) else []
    codex_paths = [str(item.get("path") or "") for item in required_apps if isinstance(item, dict)]
    codex_allowed = [str(item.get("path") or "") for item in required_apps if isinstance(item, dict) and item.get("allowed")]
    return {
        "ok": payload.get("ok") is True and all(bool(item.get("allowed")) for item in required_apps if isinstance(item, dict) and item.get("exists")),
        "read_ok": payload.get("ok") is True,
        "enable_controlled_folder_access": pref.get("enableControlledFolderAccess"),
        "documents_codex_path": str(CODEX_CFA_DOCUMENTS_PATH),
        "documents_codex_exists": CODEX_CFA_DOCUMENTS_PATH.exists(),
        "codex_executable_paths": codex_paths,
        "codex_allowed_applications": codex_allowed,
        "malformed_codex_cfa_entries": payload.get("malformed_codex_cfa_entries") or [],
        "dry_run_contract": {
            "changes_defender": False,
            "writes_files": False,
        },
    }


def clamp_observe_seconds(value: float) -> float:
    if value <= 0:
        return 0.0
    return min(float(value), MAX_OBSERVE_SECONDS)


def normalize_profile(value: str | None) -> str:
    profile = str(value or "standard").strip().lower()
    return profile if profile in PROFILES else "standard"


def classify_process(row: dict[str, Any]) -> str:
    text = f"{row.get('name') or ''}\n{row.get('command_line') or ''}".lower()
    name = str(row.get("name") or "").lower()
    parent_name = str(row.get("parent_name") or "").lower()
    if "performance_doctor.py" in text or "win32_perfformatteddata_perfproc_process" in text:
        return "observer_overhead"
    if name in {"powershell", "powershell.exe"} and parent_name in {"python", "python.exe"}:
        return "observer_overhead"
    if name in {"wmiprvse", "wmiprvse.exe"} and "wmiprvse.exe" in text:
        return "observer_overhead"
    if "email_scheduler.py" in text:
        return "email_scheduler"
    if "codegraph" in text or ".codegraph" in text:
        return "mcp_resource"
    if name in {"msmpeng", "msmpeng.exe"} or "msmpeng.exe" in text or "microsoft defender" in text:
        return "defender"
    if name in {"nvdisplay.container", "nvdisplay.container.exe"} or "nvdisplay.container.exe" in text:
        return "nvidia_display"
    if "omen" in text or "hp" in text or "sysinfocap" in text or "apphelpercap" in text:
        return "hp_omen"
    if name in {"codex", "codex.exe"} or "openai.codex" in text or "\\codex.exe" in text or "codex app-server" in text:
        return "codex"
    if "mobile_openclaw" in text or "openclaw" in text or "bridge_server_v2" in text:
        return "bridge"
    if "mcp" in text or "playwright" in text or "chrome-devtools" in text or "markitdown" in text:
        return "mcp_resource"
    if "chrome" in text or "msedge" in text or "webview" in text:
        return "browser_webview"
    if "python" in text or "node" in text:
        return "runtime_helper"
    return "other"


def extract_flag_value(command_line: Any, flag: str) -> str:
    text = str(command_line or "")
    needle = f"{flag}="
    pos = text.find(needle)
    if pos < 0:
        return ""
    rest = text[pos + len(needle) :].lstrip()
    if not rest:
        return ""
    if rest[0] == '"':
        end = rest.find('"', 1)
        return rest[1:end] if end > 0 else rest[1:]
    return rest.split()[0]


def webview_owner(row: dict[str, Any]) -> dict[str, Any]:
    command = str(row.get("command_line") or "")
    exe_name = extract_flag_value(command, "--webview-exe-name")
    user_data_dir = extract_flag_value(command, "--user-data-dir")
    host = exe_name or str(row.get("parent_name") or "")
    lower = f"{host}\n{user_data_dir}\n{command}".lower()
    category = "external_or_system"
    if "openai" in lower or "codex" in lower:
        category = "codex_desktop"
    elif "searchhost.exe" in lower or "client.cbs" in lower:
        category = "windows_search"
    elif "cc-switch" in lower or "com.ccswitch" in lower:
        category = "cc-switch"
    elif "clash-verge" in lower or "clash-verge-rev" in lower:
        category = "clash-verge"
    elif "dashboard" in lower or "mobile_openclaw_bridge" in lower:
        category = "bridge_dashboard"
    return {
        "host": host,
        "category": category,
        "user_data_dir": user_data_dir,
    }


def nvidia_write_hints() -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for path in NVIDIA_WRITE_HINT_PATHS:
        try:
            stat = path.stat()
        except OSError:
            continue
        hints.append(
            {
                "path": str(path),
                "bytes": int(stat.st_size),
                "last_write_time": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    return sorted(hints, key=lambda item: str(item.get("last_write_time") or ""), reverse=True)


def process_source(row: dict[str, Any]) -> dict[str, Any]:
    cls = str(row.get("class") or classify_process(row))
    if cls == "browser_webview":
        return {"kind": "webview_owner", **webview_owner(row)}
    if cls == "nvidia_display":
        return {
            "kind": "nvidia_display_container",
            "likely_sources": ["NVIDIA App telemetry/logging", "ShadowPlay/CaptureCore", "driver profile timestamp updates"],
            "recent_hint_files": nvidia_write_hints(),
        }
    if cls == "defender":
        return {
            "kind": "defender_realtime_or_scheduled_scan",
            "note": "Use defender_governance snapshot for policy drift; high CPU with policy ok usually needs path/process correlation, not more broad exclusions.",
        }
    return {}


def build_source_attribution(snap: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for key in ("top_cpu", "top_memory", "top_disk_write"):
        value = snap.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    webview_counts: dict[str, int] = {}
    webview_dirs: dict[str, list[str]] = {}
    nvidia_sources: list[dict[str, Any]] = []
    defender_hot = False
    for row in rows:
        cls = str(row.get("class") or "")
        source = row.get("source") if isinstance(row.get("source"), dict) else process_source(row)
        if cls == "browser_webview":
            category = str(source.get("category") or "unknown")
            webview_counts[category] = webview_counts.get(category, 0) + 1
            user_data_dir = str(source.get("user_data_dir") or "")
            if user_data_dir:
                bucket = webview_dirs.setdefault(category, [])
                if user_data_dir not in bucket:
                    bucket.append(user_data_dir)
        elif cls == "nvidia_display":
            if source:
                nvidia_sources.append(source)
        elif cls == "defender":
            defender_hot = True
    defender_exclusions = snap.get("defender_exclusions") if isinstance(snap.get("defender_exclusions"), dict) else {}
    defender_cfa = snap.get("defender_cfa") if isinstance(snap.get("defender_cfa"), dict) else {}
    return {
        "webview_owner_counts": webview_counts,
        "webview_user_data_dirs": {key: value[:5] for key, value in webview_dirs.items()},
        "nvidia_display_hints": nvidia_sources[:3] or [{"recent_hint_files": nvidia_write_hints()}],
        "defender": {
            "hot_in_sample": defender_hot,
            "exclusions_ok": defender_exclusions.get("ok"),
            "cfa_ok": defender_cfa.get("ok"),
            "policy_hint": "Do not rewrite Defender settings when governance is already ok; correlate with hot files/processes first.",
        },
    }


def codex_process_role(row: dict[str, Any]) -> str:
    text = f"{row.get('name') or ''}\n{row.get('command_line') or ''}".lower()
    if " app-server " in f" {text} " and "127.0.0.1:18791" in text:
        return "bridge_app_server_18791"
    if " app-server " in f" {text} " and "--analytics-default-enabled" in text:
        return "desktop_main_app_server"
    if " app-server " in f" {text} ":
        return "codex_app_server"
    if "--type=renderer" in text:
        return "desktop_renderer"
    if "codex.exe" in text:
        return "desktop_or_cli_process"
    return ""


def codex_activity_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active_rows: list[dict[str, Any]] = []
    automation_rows: list[dict[str, Any]] = []
    for row in rows:
        text = f"{row.get('name') or ''}\n{row.get('command_line') or ''}".lower()
        is_codex_exec = "codex.exe" in text and " exec" in text and "--ephemeral" in text
        if is_codex_exec:
            entry = {
                "pid": row.get("pid"),
                "name": row.get("name"),
                "class": classify_process(row),
                "command_line": trim_command(row.get("command_line")),
            }
            active_rows.append(entry)
        if any(keyword.lower() in text for keyword in CODEX_AUTOMATION_WORKER_KEYWORDS):
            automation_rows.append(
                {
                    "pid": row.get("pid"),
                    "name": row.get("name"),
                    "class": classify_process(row),
                    "command_line": trim_command(row.get("command_line")),
                }
            )
    reasons = []
    if active_rows:
        reasons.append("codex_exec_ephemeral_process_present")
    if automation_rows:
        reasons.append("codex_automation_worker_present")
    commandline_available = any(str(row.get("command_line") or "").strip() for row in rows)
    return {
        "active": bool(active_rows),
        "confidence": "standard_with_commandline" if commandline_available else "low_without_commandline",
        "current_agent_active": None,
        "background_active": bool(active_rows),
        "automation_worker_present": bool(automation_rows),
        "reason_codes": reasons,
        "active_process_count": len(active_rows),
        "automation_worker_count": len(automation_rows),
        "active_processes": active_rows[:10],
        "automation_workers": automation_rows[:10],
    }


def trim_command(value: Any) -> str:
    return " ".join(str(value or "").split())[:500]


def render_class_summary_table(class_summary: list[dict[str, Any]]) -> list[str]:
    order = [
        ("defender", "Defender/安全扫描"),
        ("codex", "Codex/主运行"),
        ("bridge", "桥接/队列"),
        ("email_scheduler", "定时邮件/执行体"),
        ("mcp_resource", "MCP/资源"),
        ("browser_webview", "浏览器/WebView"),
        ("nvidia_display", "NVIDIA/显示驱动"),
        ("hp_omen", "HP/OMEN"),
        ("observer_overhead", "采样器开销"),
        ("runtime_helper", "运行时辅助"),
        ("other", "其他"),
    ]
    by_class = {str(item.get("class") or ""): item for item in class_summary}
    lines = ["", "Class Summary:"]
    for key, label in order:
        item = by_class.get(key)
        if not item:
            continue
        pids = [str(pid) for pid in (item.get("top_pids") or []) if pid]
        pids_text = ", ".join(pids[:5]) if pids else "-"
        lines.append(
            "- {label}: count={count} cpu={cpu} ws={ws} write={write} pids={pids}".format(
                label=label,
                count=int(item.get("process_count") or 0),
                cpu=float(item.get("cpu_percent_estimate") or 0),
                ws=float(item.get("working_set_mb") or 0),
                write=float(item.get("io_write_bytes_per_sec") or 0),
                pids=pids_text,
            )
        )
    return lines


def render_top_process_table(title: str, rows: list[dict[str, Any]], value_key: str, value_label: str, limit: int = 6) -> list[str]:
    lines = ["", title]
    if not rows:
        lines.append("- none")
        return lines
    for row in rows[:limit]:
        lines.append(
            "- pid={pid} class={cls} {value_label}={value} name={name}".format(
                pid=int(row.get("pid") or 0),
                cls=str(row.get("class") or "other"),
                value_label=value_label,
                value=row.get(value_key),
                name=str(row.get("name") or "-"),
            )
        )
    return lines


def sample(observe_seconds: float = 10.0, top: int = 15, profile: str = "standard", include_policy_checks: bool = True) -> dict[str, Any]:
    profile = normalize_profile(profile)
    observe_seconds = clamp_observe_seconds(observe_seconds)
    if profile == "quick" and observe_seconds > 5:
        observe_seconds = 5.0
    top = max(1, min(int(top), 50))
    process_reader = process_rows_light if profile == "quick" else process_rows
    first = process_reader()
    first_by_pid = {int(row.get("pid") or 0): row for row in first}
    if observe_seconds > 0:
        time.sleep(observe_seconds)
    second = process_reader()
    has_io_counters = any("io_write_bytes" in row for row in second)
    disk_by_pid = (
        {}
        if profile == "quick" or has_io_counters
        else {int(row.get("pid") or 0): row for row in disk_io_rows()}
    )

    rows: list[dict[str, Any]] = []
    for row in second:
        pid = int(row.get("pid") or 0)
        previous = first_by_pid.get(pid, {})
        cpu_delta = round(float(row.get("cpu_seconds") or 0) - float(previous.get("cpu_seconds") or 0), 3)
        cpu_percent_estimate = round((cpu_delta / observe_seconds) * 100, 1) if observe_seconds > 0 else None
        disk = disk_by_pid.get(pid, {})
        io_read_bps = 0.0
        io_write_bps = 0.0
        io_data_bps = 0.0
        if observe_seconds > 0 and "io_write_bytes" in row:
            io_read_bps = round(max(0.0, float(row.get("io_read_bytes") or 0) - float(previous.get("io_read_bytes") or 0)) / observe_seconds, 1)
            io_write_bps = round(max(0.0, float(row.get("io_write_bytes") or 0) - float(previous.get("io_write_bytes") or 0)) / observe_seconds, 1)
            io_data_bps = round(max(0.0, float(row.get("io_data_bytes") or 0) - float(previous.get("io_data_bytes") or 0)) / observe_seconds, 1)
        else:
            io_read_bps = float(disk.get("io_read_bytes_per_sec") or 0)
            io_write_bps = float(disk.get("io_write_bytes_per_sec") or 0)
            io_data_bps = float(disk.get("io_data_bytes_per_sec") or 0)
        rows.append(
            {
                "pid": pid,
                "parent_pid": row.get("parent_pid"),
                "parent_name": row.get("parent_name"),
                "name": row.get("name"),
                "class": classify_process(row),
                "working_set_mb": row.get("working_set_mb"),
                "private_memory_mb": row.get("private_memory_mb"),
                "cpu_seconds": row.get("cpu_seconds"),
                "cpu_delta_seconds": cpu_delta,
                "cpu_percent_estimate": cpu_percent_estimate,
                "io_read_bytes_per_sec": io_read_bps,
                "io_write_bytes_per_sec": io_write_bps,
                "io_data_bytes_per_sec": io_data_bps,
                "start_time": row.get("start_time"),
                "command_line": trim_command(row.get("command_line")),
                "sampling_backend": row.get("sampling_backend") or ("powershell" if disk else "unknown"),
                "source": process_source({**row, "class": classify_process(row)}),
            }
        )

    by_class: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("class") or "other")
        bucket = by_class.setdefault(
            key,
            {
                "class": key,
                "process_count": 0,
                "working_set_mb": 0.0,
                "private_memory_mb": 0.0,
                "cpu_delta_seconds": 0.0,
                "cpu_percent_estimate": 0.0 if observe_seconds > 0 else None,
                "io_write_bytes_per_sec": 0.0,
                "io_data_bytes_per_sec": 0.0,
                "top_pids": [],
            },
        )
        bucket["process_count"] += 1
        bucket["working_set_mb"] = round(float(bucket["working_set_mb"]) + float(row.get("working_set_mb") or 0), 1)
        bucket["private_memory_mb"] = round(float(bucket["private_memory_mb"]) + float(row.get("private_memory_mb") or 0), 1)
        bucket["cpu_delta_seconds"] = round(float(bucket["cpu_delta_seconds"]) + float(row.get("cpu_delta_seconds") or 0), 3)
        if observe_seconds > 0:
            bucket["cpu_percent_estimate"] = round(float(bucket["cpu_percent_estimate"] or 0) + float(row.get("cpu_percent_estimate") or 0), 1)
        bucket["io_write_bytes_per_sec"] = round(float(bucket["io_write_bytes_per_sec"]) + float(row.get("io_write_bytes_per_sec") or 0), 1)
        bucket["io_data_bytes_per_sec"] = round(float(bucket["io_data_bytes_per_sec"]) + float(row.get("io_data_bytes_per_sec") or 0), 1)
        bucket["top_pids"].append(row.get("pid"))
        bucket["top_pids"] = bucket["top_pids"][:5]

    payload = {
        "schema": "performance.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "profile": profile,
        "observe_seconds": observe_seconds,
        "top": top,
        "top_cpu": sorted(rows, key=lambda item: float(item.get("cpu_delta_seconds") or 0), reverse=True)[:top],
        "top_memory": sorted(rows, key=lambda item: float(item.get("working_set_mb") or 0), reverse=True)[:top],
        "top_disk_write": sorted(rows, key=lambda item: float(item.get("io_write_bytes_per_sec") or 0), reverse=True)[:top],
        "class_summary": sorted(
            by_class.values(),
            key=lambda item: (
                -float(item.get("cpu_delta_seconds") or 0),
                -float(item.get("working_set_mb") or 0),
                str(item.get("class") or ""),
            ),
        ),
        "codex_activity_context": codex_activity_context(second),
        "dry_run_contract": {
            "writes_files": False,
            "kills_processes": False,
            "starts_processes": False,
            "changes_services": False,
            "changes_defender": False,
            "restarts_app_server": False,
        },
        "observer": {
            "process_probe": "psutil_process_iter" if second and second[0].get("sampling_backend") == "psutil" else ("get_process_light" if profile == "quick" else "cim_process_with_command_line"),
            "disk_io_probe": "psutil_io_delta" if has_io_counters else ("skipped" if profile == "quick" else "perfproc_process"),
            "powershell_fallback_used": not bool(second and second[0].get("sampling_backend") == "psutil"),
        },
    }
    if include_policy_checks and profile in {"standard", "deep"}:
        payload["defender_exclusions"] = defender_exclusion_snapshot()
        payload["defender_cfa"] = defender_cfa_snapshot()
    elif profile in {"standard", "deep"}:
        payload["defender_exclusions"] = {"ok": None, "skipped": True, "reason": "aggregate_nonfinal_window"}
        payload["defender_cfa"] = {"ok": None, "skipped": True, "reason": "aggregate_nonfinal_window"}
    else:
        payload["defender_exclusions"] = {"ok": None, "skipped": True, "reason": "quick_profile"}
        payload["defender_cfa"] = {"ok": None, "skipped": True, "reason": "quick_profile"}
    return payload


def aggregate_samples(observe_seconds: float = 5.0, top: int = 15, profile: str = "quick", windows: int | None = None) -> dict[str, Any]:
    profile = normalize_profile(profile)
    observe_seconds = clamp_observe_seconds(observe_seconds)
    if profile == "quick" and observe_seconds > 5:
        observe_seconds = 5.0
    top = max(1, min(int(top), 50))
    window_count = max(1, min(int(windows or AGGREGATE_WINDOWS.get(profile, 3)), 5))
    snapshots = [
        sample(
            observe_seconds=observe_seconds,
            top=top,
            profile=profile,
            include_policy_checks=(index == window_count - 1),
        )
        for index in range(window_count)
    ]

    process_buckets: dict[tuple[int, str, str], dict[str, Any]] = {}
    class_buckets: dict[str, dict[str, Any]] = {}
    for snap_index, snap in enumerate(snapshots):
        seen_process_keys: set[tuple[int, str, str]] = set()
        for row in snap.get("top_cpu", []) + snap.get("top_memory", []) + snap.get("top_disk_write", []):
            if not isinstance(row, dict):
                continue
            key = (int(row.get("pid") or 0), str(row.get("name") or ""), str(row.get("class") or "other"))
            seen_process_keys.add(key)
            bucket = process_buckets.setdefault(
                key,
                {
                    "pid": key[0],
                    "name": key[1],
                    "class": key[2],
                    "samples_seen": 0,
                    "hit_count": 0,
                    "cpu_values": [],
                    "memory_values": [],
                    "write_values": [],
                "command_line": row.get("command_line"),
                    "source": row.get("source"),
                "start_time": row.get("start_time"),
                    "parent_pid": row.get("parent_pid"),
                    "parent_name": row.get("parent_name"),
                },
            )
            bucket["cpu_values"].append(float(row.get("cpu_percent_estimate") or 0))
            bucket["memory_values"].append(float(row.get("working_set_mb") or 0))
            bucket["write_values"].append(float(row.get("io_write_bytes_per_sec") or 0))
            bucket["samples_seen"] = int(bucket["samples_seen"]) + 1
        for key in seen_process_keys:
            process_buckets[key]["hit_count"] = int(process_buckets[key]["hit_count"]) + 1

        for item in snap.get("class_summary", []) if isinstance(snap.get("class_summary"), list) else []:
            if not isinstance(item, dict):
                continue
            cls = str(item.get("class") or "other")
            bucket = class_buckets.setdefault(
                cls,
                {
                    "class": cls,
                    "samples_seen": 0,
                    "process_count_values": [],
                    "working_set_values": [],
                    "private_memory_values": [],
                    "cpu_values": [],
                    "write_values": [],
                    "data_values": [],
                    "top_pids": [],
                },
            )
            bucket["samples_seen"] = int(bucket["samples_seen"]) + 1
            bucket["process_count_values"].append(float(item.get("process_count") or 0))
            bucket["working_set_values"].append(float(item.get("working_set_mb") or 0))
            bucket["private_memory_values"].append(float(item.get("private_memory_mb") or 0))
            bucket["cpu_values"].append(float(item.get("cpu_percent_estimate") or 0))
            bucket["write_values"].append(float(item.get("io_write_bytes_per_sec") or 0))
            bucket["data_values"].append(float(item.get("io_data_bytes_per_sec") or 0))
            for pid in item.get("top_pids") or []:
                if pid not in bucket["top_pids"]:
                    bucket["top_pids"].append(pid)
            bucket["top_pids"] = bucket["top_pids"][:5]

    def avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 1) if values else 0.0

    def peak(values: list[float]) -> float:
        return round(max(values), 1) if values else 0.0

    rows: list[dict[str, Any]] = []
    for bucket in process_buckets.values():
        cpu_values = list(bucket.get("cpu_values") or [])
        memory_values = list(bucket.get("memory_values") or [])
        write_values = list(bucket.get("write_values") or [])
        cls = str(bucket.get("class") or "other")
        hit_count = int(bucket.get("hit_count") or 0)
        sustained = hit_count >= max(2, window_count // 2 + 1)
        transient_peak = peak(cpu_values) >= 35 and avg(cpu_values) < 25 and not sustained
        rows.append(
            {
                "pid": bucket.get("pid"),
                "parent_pid": bucket.get("parent_pid"),
                "parent_name": bucket.get("parent_name"),
                "name": bucket.get("name"),
                "class": cls,
                "hit_count": hit_count,
                "window_count": window_count,
                "sustained": sustained,
                "transient_peak": transient_peak,
                "cpu_percent_avg": avg(cpu_values),
                "cpu_percent_peak": peak(cpu_values),
                "cpu_percent_estimate": avg(cpu_values),
                "working_set_mb_avg": avg(memory_values),
                "working_set_mb_peak": peak(memory_values),
                "working_set_mb": avg(memory_values),
                "io_write_bytes_per_sec_avg": avg(write_values),
                "io_write_bytes_per_sec_peak": peak(write_values),
                "io_write_bytes_per_sec": avg(write_values),
                "start_time": bucket.get("start_time"),
                "command_line": bucket.get("command_line"),
                "source": bucket.get("source"),
                "confidence": "sustained" if sustained else "transient_single_or_sparse_window",
            }
        )

    class_summary: list[dict[str, Any]] = []
    for bucket in class_buckets.values():
        cpu_values = list(bucket.get("cpu_values") or [])
        class_summary.append(
            {
                "class": bucket.get("class"),
                "samples_seen": bucket.get("samples_seen"),
                "window_count": window_count,
                "process_count_avg": avg(list(bucket.get("process_count_values") or [])),
                "process_count": round(avg(list(bucket.get("process_count_values") or []))),
                "working_set_mb": avg(list(bucket.get("working_set_values") or [])),
                "private_memory_mb": avg(list(bucket.get("private_memory_values") or [])),
                "cpu_percent_estimate": avg(cpu_values),
                "cpu_percent_peak": peak(cpu_values),
                "io_write_bytes_per_sec": avg(list(bucket.get("write_values") or [])),
                "io_data_bytes_per_sec": avg(list(bucket.get("data_values") or [])),
                "top_pids": bucket.get("top_pids") or [],
            }
        )

    top_cpu = sorted(rows, key=lambda item: (-float(item.get("cpu_percent_avg") or 0), -float(item.get("cpu_percent_peak") or 0)))[:top]
    top_memory = sorted(rows, key=lambda item: -float(item.get("working_set_mb_peak") or 0))[:top]
    top_disk_write = sorted(rows, key=lambda item: -float(item.get("io_write_bytes_per_sec_peak") or 0))[:top]
    latest = snapshots[-1] if snapshots else {}
    activity_contexts = [
        snap.get("codex_activity_context")
        for snap in snapshots
        if isinstance(snap.get("codex_activity_context"), dict)
    ]
    active_windows = sum(1 for item in activity_contexts if item.get("active"))
    background_windows = sum(1 for item in activity_contexts if item.get("background_active"))
    latest_activity = activity_contexts[-1] if activity_contexts else {}
    codex_activity = {
        "active": active_windows > 0,
        "active_windows": active_windows,
        "background_active_windows": background_windows,
        "window_count": window_count,
        "latest": latest_activity,
    }
    return {
        "schema": "performance.aggregate_snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "profile": profile,
        "observe_seconds": observe_seconds,
        "window_count": window_count,
        "total_observe_seconds": round(observe_seconds * window_count, 1),
        "top": top,
        "top_cpu": top_cpu,
        "top_memory": top_memory,
        "top_disk_write": top_disk_write,
        "class_summary": sorted(
            class_summary,
            key=lambda item: (
                -float(item.get("cpu_percent_estimate") or 0),
                -float(item.get("working_set_mb") or 0),
                str(item.get("class") or ""),
            ),
        ),
        "snapshots": snapshots,
        "sampling_policy": {
            "mode": "multi_window",
            "purpose": "Separate sustained load from short spikes before maintenance decisions.",
            "transient_classes": sorted(TRANSIENT_CPU_RISK_CLASSES),
            "observer_overhead_class": "observer_overhead",
            "fidelity": "low_without_parent_commandline" if profile == "quick" else "standard_with_commandline",
        },
        "codex_activity_context": codex_activity,
        "observer": latest.get("observer") or {},
        "dry_run_contract": latest.get("dry_run_contract") or {},
        "defender_exclusions": latest.get("defender_exclusions"),
        "defender_cfa": latest.get("defender_cfa"),
    }


def load_resource_process_validation() -> dict[str, Any]:
    try:
        from resource_process_doctor import validate as resource_validate

        return resource_validate()
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def load_mcp_session_doctor() -> dict[str, Any]:
    try:
        from mcp_session_doctor import doctor as mcp_session_doctor
        from mcp_session_doctor import snapshot as mcp_session_snapshot

        return mcp_session_doctor(mcp_session_snapshot())
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def classify_issue(
    row: dict[str, Any],
    observe_seconds: float,
    codex_activity: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    cls = str(row.get("class") or "other")
    if cls == "observer_overhead":
        return None
    cpu_pct = float(row.get("cpu_percent_avg") or row.get("cpu_percent_estimate") or 0)
    cpu_peak = float(row.get("cpu_percent_peak") or cpu_pct)
    memory_mb = float(row.get("working_set_mb") or 0)
    write_bps = float(row.get("io_write_bytes_per_sec") or 0)
    if cpu_pct < 15 and memory_mb < 900 and write_bps < 20_000_000:
        return None

    severity = "advisory"
    if cpu_pct >= 35 or memory_mb >= 1500 or write_bps >= 60_000_000:
        severity = "risk"
    if row.get("sustained") is False and memory_mb < 1500 and write_bps < 60_000_000:
        severity = "advisory"
    if str(row.get("name") or "").lower() in {"powershell", "powershell.exe"} and memory_mb < 300 and write_bps < 60_000_000:
        severity = "advisory"
    if cls in TRANSIENT_CPU_RISK_CLASSES and not bool(row.get("sustained")):
        severity = "advisory"
    if bool(row.get("transient_peak")):
        severity = "advisory"
    code = "generic_process_load"
    action = "observe longer with performance snapshot before making changes"
    if cls == "defender":
        code = "defender_sustained_load"
        action = "check whether a known workspace/cache path is being scanned; prefer narrow exclusions, never disable real-time protection broadly"
    elif cls == "hp_omen":
        code = "hp_omen_background_load"
        action = "keep OMEN Gaming Hub ability; disable only nonessential HP/OMEN background services after backup and approval"
    elif cls == "codex":
        active = bool((codex_activity or {}).get("active"))
        role = codex_process_role(row)
        if active:
            severity = "advisory"
            code = "codex_active_workload"
            action = "Codex workload is active; do not restart or repair from this sample. Re-sample after the active turn/background job is idle."
        elif role == "bridge_app_server_18791":
            code = "codex_idle_sustained_load"
            action = "Bridge Codex app-server on 18791 is idle but still hot; allow one controlled idle restart only when bridge queues are empty, then investigate loops if load returns."
        else:
            code = "codex_idle_sustained_load"
            action = "Codex Desktop appears idle but app-server/renderer load is sustained; report and capture a fresh longer snapshot before any Desktop refresh. Do not kill the main Desktop process from maintenance."
    elif cls in {"mcp_resource", "bridge"}:
        code = "bridge_or_mcp_resource_load"
        action = "run resource-process doctor/startup-sources; only clean revalidated orphan roots or idle duplicate service instances"
    elif cls == "browser_webview":
        code = "browser_webview_load"
        source = process_source(row)
        owner = source.get("category") if isinstance(source, dict) else ""
        if owner in {"windows_search", "cc-switch", "clash-verge"}:
            severity = "advisory" if write_bps < 60_000_000 and cpu_pct < 60 else severity
            action = f"WebView belongs to {owner}; do not close from Codex maintenance. Tune or close that owning app only if the user chooses."
        elif owner == "bridge_dashboard":
            action = "Bridge dashboard WebView is hot; check dashboard polling/write cadence before closing the page."
        else:
            action = "identify owning app/tab before closing; prefer process command line/source attribution over GUI guessing"
    elif cls == "nvidia_display":
        code = "nvidia_display_write_or_load"
        action = "check NVIDIA App/ShadowPlay telemetry logs such as nvtopps/nvlog.nvlgstg and CaptureCore.log; do not disable NVIDIA Display Container unless repeated sustained evidence confirms it."
    return {
        "severity": severity,
        "code": code,
        "pid": row.get("pid"),
        "name": row.get("name"),
        "class": cls,
        "cpu_percent_estimate": cpu_pct if observe_seconds > 0 else None,
        "cpu_percent_peak": cpu_peak if observe_seconds > 0 else None,
        "hit_count": row.get("hit_count"),
        "window_count": row.get("window_count"),
        "sustained": row.get("sustained"),
        "confidence": row.get("confidence") or "single_window",
        "working_set_mb": memory_mb,
        "io_write_bytes_per_sec": write_bps,
        "manual_action": action,
        "codex_activity_context": codex_activity if cls == "codex" else None,
        "process_role": codex_process_role(row) if cls == "codex" else "",
        "source": process_source(row),
    }


def doctor(observe_seconds: float = 10.0, top: int = 15, profile: str = "standard") -> dict[str, Any]:
    profile = normalize_profile(profile)
    snap = aggregate_samples(observe_seconds=observe_seconds, top=top, profile=profile)
    codex_activity = snap.get("codex_activity_context") if isinstance(snap.get("codex_activity_context"), dict) else {}
    issues = [
        issue
        for row in snap.get("top_cpu", []) + snap.get("top_memory", []) + snap.get("top_disk_write", [])
        if (issue := classify_issue(row, float(snap.get("observe_seconds") or 0), codex_activity))
    ]
    deduped: dict[tuple[Any, str], dict[str, Any]] = {}
    for issue in issues:
        deduped[(issue.get("pid"), issue.get("code"))] = issue
    issues = sorted(
        deduped.values(),
        key=lambda item: (
            0 if item.get("severity") == "risk" else 1,
            -float(item.get("cpu_percent_estimate") or 0),
            -float(item.get("cpu_percent_peak") or 0),
            -float(item.get("working_set_mb") or 0),
        ),
    )
    resource_validation = load_resource_process_validation() if profile == "deep" else {"ok": None, "skipped": True, "reason": f"{profile}_profile"}
    mcp_session_health = load_mcp_session_doctor() if profile == "deep" else {"ok": None, "skipped": True, "reason": f"{profile}_profile"}
    defender_exclusions = snap.get("defender_exclusions") if isinstance(snap.get("defender_exclusions"), dict) else {}
    defender_cfa = snap.get("defender_cfa") if isinstance(snap.get("defender_cfa"), dict) else {}
    if defender_exclusions and defender_exclusions.get("ok") is False:
        issues.append(
            {
                "severity": "risk",
                "code": "codex_defender_exclusion_drift",
                "manual_action": "backup Defender preferences, then re-add only the missing Codex workspace/runtime exclusions",
                "details": defender_exclusions,
            }
        )
    if defender_cfa and defender_cfa.get("ok") is False:
        issues.append(
            {
                "severity": "risk",
                "code": "codex_controlled_folder_access_block_risk",
                "manual_action": "add the current Codex Desktop host executable path to ControlledFolderAccessAllowedApplications after backing up Defender preferences",
                "details": defender_cfa,
            }
        )
    if resource_validation and resource_validation.get("ok") is False:
        issues.append(
            {
                "severity": "risk",
                "code": "resource_process_validation_failed",
                "manual_action": "run resource-process repair-plan, then cleanup dry-run/apply only if orphan evidence is complete",
                "details": resource_validation,
            }
        )
    if mcp_session_health and mcp_session_health.get("ok") is False:
        transport_closed = int((mcp_session_health.get("summary") or {}).get("transport_closed_count") or 0)
        issues.append(
            {
                "severity": "risk" if transport_closed else "advisory",
                "code": "mcp_session_health_degraded",
                "manual_action": "run mcp-session repair-plan; prefer profile fallback for the current task, then refresh the Codex MCP session if transport remains closed",
                "details": mcp_session_health,
            }
        )
    return {
        "schema": "performance.doctor.v1",
        "ok": not any(issue.get("severity") in {"blocker", "risk"} for issue in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": {
            "observe_seconds": snap.get("observe_seconds"),
            "window_count": snap.get("window_count"),
            "total_observe_seconds": snap.get("total_observe_seconds"),
            "profile": profile,
            "top_cpu_process": (snap.get("top_cpu") or [{}])[0],
            "top_sustained_cpu_process": next((row for row in snap.get("top_cpu", []) if isinstance(row, dict) and row.get("sustained")), {}),
            "top_memory_process": (snap.get("top_memory") or [{}])[0],
            "top_disk_write_process": (snap.get("top_disk_write") or [{}])[0],
            "source_attribution": build_source_attribution(snap),
            "resource_process_validate_ok": resource_validation.get("ok") if isinstance(resource_validation, dict) else None,
            "mcp_session_doctor_ok": mcp_session_health.get("ok") if isinstance(mcp_session_health, dict) else None,
            "mcp_session_transport_closed_count": (
                (mcp_session_health.get("summary") or {}).get("transport_closed_count")
                if isinstance(mcp_session_health, dict) and isinstance(mcp_session_health.get("summary"), dict)
                else None
            ),
            "defender_exclusion_ok": defender_exclusions.get("ok") if isinstance(defender_exclusions, dict) else None,
            "defender_cfa_ok": defender_cfa.get("ok") if isinstance(defender_cfa, dict) else None,
            "codex_activity_context": codex_activity,
        },
        "snapshot": snap,
    }


def summary_report(
    observe_seconds: float = 10.0,
    top: int = 15,
    profile: str = "standard",
    *,
    doctor_payload: dict[str, Any] | None = None,
) -> str:
    profile = normalize_profile(profile)
    doc = doctor_payload or doctor(observe_seconds=observe_seconds, top=top, profile=profile)
    snap = doc.get("snapshot") if isinstance(doc.get("snapshot"), dict) else {}
    issues = doc.get("issues") if isinstance(doc.get("issues"), list) else []
    lines: list[str] = [
        "Performance Summary:",
        f"- generated_at: {snap.get('generated_at')}",
        f"- observe_seconds: {snap.get('observe_seconds')}",
        f"- window_count: {snap.get('window_count')}",
        f"- total_observe_seconds: {snap.get('total_observe_seconds')}",
        f"- profile: {profile}",
        f"- status: {'ok' if doc.get('ok') else 'attention'}",
        f"- issue_count: {len(issues)}",
    ]
    lines.extend(render_class_summary_table(snap.get("class_summary") if isinstance(snap.get("class_summary"), list) else []))
    lines.extend(render_top_process_table("Top CPU:", snap.get("top_cpu") if isinstance(snap.get("top_cpu"), list) else [], "cpu_percent_estimate", "cpu%", limit=6))
    lines.extend(render_top_process_table("Top Memory:", snap.get("top_memory") if isinstance(snap.get("top_memory"), list) else [], "working_set_mb", "ws_mb", limit=6))
    lines.extend(render_top_process_table("Top Disk Write:", snap.get("top_disk_write") if isinstance(snap.get("top_disk_write"), list) else [], "io_write_bytes_per_sec", "write_bps", limit=6))
    if issues:
        lines.append("")
        lines.append("Top Issues:")
        for item in issues[:6]:
            lines.append(
                "- [{severity}] {code}: {summary}".format(
                    severity=item.get("severity"),
                    code=item.get("code"),
                    summary=item.get("manual_action"),
                )
            )
    else:
        lines.extend(["", "Top Issues:", "- none"])
    return "\n".join(lines)


def repair_plan(observe_seconds: float = 10.0, top: int = 15, profile: str = "deep") -> dict[str, Any]:
    profile = normalize_profile(profile)
    doc = doctor(observe_seconds=observe_seconds, top=top, profile=profile)
    actions: list[dict[str, Any]] = []
    for issue in doc.get("issues", []):
        code = str(issue.get("code") or "")
        action: dict[str, Any] = {
            "code": f"review_{code}",
            "source_issue": issue,
            "dry_run_only": True,
            "would_mutate": "nothing",
            "requires_fresh_snapshot_before_apply": True,
        }
        if code == "resource_process_validation_failed":
            action["candidate_commands"] = [
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process repair-plan",
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process cleanup",
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process cleanup --apply",
            ]
        elif code in {"codex_active_workload", "codex_idle_sustained_load"}:
            role = str((issue.get("process_role") or ""))
            action["candidate_commands"] = [
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py maintenance summary",
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py codex-log-sqlite-health --observe-seconds 30",
            ]
            if role == "bridge_app_server_18791":
                action["candidate_commands"].append(
                    "powershell -NoProfile -ExecutionPolicy Bypass -File _bridge\\shared\\restart-bridge-appserver-if-idle.ps1 -Mode dry-run"
                )
            action["guardrails"] = [
                "for codex_active_workload, wait for active work to finish before repair",
                "restart bridge app-server only when queue is idle and the hot PID owns 127.0.0.1:18791",
                "do not restart visible Codex UI from this plan",
                "if load returns after one controlled restart, investigate retry/watch loops instead of repeated restarts",
            ]
            action["process_role"] = role
        elif code == "bridge_or_mcp_resource_load":
            action["candidate_commands"] = [
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process metrics",
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process startup-sources",
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process repair-plan",
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mcp-session doctor",
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mcp-session repair-plan",
                "python _bridge\\codegraph_health.py metrics",
                "python _bridge\\codegraph_health.py validate --json",
            ]
            action["guardrails"] = [
                "treat CodeGraph/Playwright/CDP/MarkItDown service spikes as transient unless repeated-window evidence is sustained",
                "fix duplicate launcher/session lifecycle before cleanup",
                "do not stop protected bridge or active MCP sessions",
                "run cleanup apply only for revalidated non-protected orphan root batches",
            ]
        elif code == "mcp_session_health_degraded":
            action["candidate_commands"] = [
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mcp-session doctor",
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mcp-session repair-plan --run-fallback",
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py tool-registry-health",
            ]
            action["guardrails"] = [
                "do not treat transport closed as missing config or missing process",
                "use current-task fallback when a profile provides one",
                "do not auto-kill protected bridge/Reasonix MCPs",
                "refresh the Codex MCP session only after fallback and diagnostics show the active session is stale",
            ]
        elif code == "defender_sustained_load":
            action["guardrails"] = [
                "do not disable real-time protection",
                "if defender_governance scan_policy is already ok, do not keep rewriting Defender preferences",
                "correlate MsMpEng CPU with top disk writers and recent hot files before adding exclusions",
                "only add narrow exclusions for known cache/log/build paths after evidence shows Defender is scanning them",
                "backup Defender preferences before any change",
            ]
            action["candidate_commands"] = [
                "python _bridge\\defender_governance.py snapshot",
                "python _bridge\\performance_doctor.py snapshot --observe-seconds 30 --profile standard",
            ]
        elif code == "codex_defender_exclusion_drift":
            action["guardrails"] = [
                "do not disable real-time protection",
                "do not exclude the whole user profile or Downloads directory",
                "add only missing existing paths from defender_exclusions.missing_existing_paths",
                "backup Defender preferences before any change",
            ]
        elif code == "codex_controlled_folder_access_block_risk":
            action["guardrails"] = [
                "do not disable Controlled Folder Access globally",
                "do not remove Documents from protected folders unless allow-listing fails",
                "allow only the current Codex Desktop host executable path that appears in defender_cfa.codex_executable_paths",
                "backup Defender preferences before any change",
            ]
        elif code == "hp_omen_background_load":
            action["guardrails"] = [
                "preserve OMEN Gaming Hub performance setting ability",
                "disable only nonessential services after backup",
                "validate after reboot because HP components may restore scheduled entries",
            ]
        elif code == "browser_webview_load":
            action["candidate_commands"] = [
                "python _bridge\\performance_doctor.py snapshot --observe-seconds 30 --profile standard",
            ]
            action["guardrails"] = [
                "use source.category to distinguish Codex Desktop, Windows Search, bridge dashboard, cc-switch, and clash-verge",
                "do not kill system SearchHost or external proxy clients from Codex maintenance",
                "only optimize bridge-owned dashboard polling/cache when source.category is bridge_dashboard",
                "preserve WebView user data directories that hold login/session state",
            ]
        elif code == "nvidia_display_write_or_load":
            action["candidate_commands"] = [
                "powershell -NoProfile -Command \"Get-Process NVDisplay.Container | Select Id,CPU,WorkingSet64,IOWriteBytes\"",
            ]
            action["guardrails"] = [
                "treat single-window NVIDIA writes as transient unless repeated samples show sustained growth",
                "check nvtopps/nct/nvlog.nvlgstg and ShadowPlay/CaptureCore.log before changing NVIDIA settings",
                "prefer disabling ShadowPlay instant replay/overlay over disabling NVIDIA Display Container",
                "do not stop NVIDIA Display Container from automated maintenance",
            ]
        else:
            action["candidate_commands"] = [
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py performance snapshot --observe-seconds 60",
            ]
        actions.append(action)
    return {
        "schema": "performance.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "profile": profile,
        "dry_run_contract": {
            "writes_files": False,
            "kills_processes": False,
            "starts_processes": False,
            "changes_services": False,
            "changes_defender": False,
            "restarts_app_server": False,
        },
        "doctor_ok": doc.get("ok"),
        "action_count": len(actions),
        "actions": actions,
        "next_step": "Review the highest-severity action, then apply a separate narrow repair only after a fresh snapshot confirms the same cause.",
    }


def metrics(observe_seconds: float = 5.0, top: int = 10, profile: str = "quick") -> dict[str, Any]:
    profile = normalize_profile(profile)
    snap = aggregate_samples(observe_seconds=observe_seconds, top=top, profile=profile)
    return {
        "schema": "performance.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "profile": profile,
        "observe_seconds": snap.get("observe_seconds"),
        "window_count": snap.get("window_count"),
        "total_observe_seconds": snap.get("total_observe_seconds"),
        "sampling_policy": snap.get("sampling_policy"),
        "codex_activity_context": snap.get("codex_activity_context"),
        "top_cpu": [
            {
                "pid": row.get("pid"),
                "name": row.get("name"),
                "class": row.get("class"),
                "cpu_percent_avg": row.get("cpu_percent_avg"),
                "cpu_percent_peak": row.get("cpu_percent_peak"),
                "cpu_percent_estimate": row.get("cpu_percent_estimate"),
                "hit_count": row.get("hit_count"),
                "window_count": row.get("window_count"),
                "sustained": row.get("sustained"),
                "confidence": row.get("confidence"),
                "working_set_mb": row.get("working_set_mb"),
                "source": row.get("source"),
            }
            for row in snap.get("top_cpu", [])[:5]
        ],
        "top_disk_write": [
            {
                "pid": row.get("pid"),
                "name": row.get("name"),
                "class": row.get("class"),
                "io_write_bytes_per_sec_avg": row.get("io_write_bytes_per_sec_avg"),
                "io_write_bytes_per_sec_peak": row.get("io_write_bytes_per_sec_peak"),
                "sustained": row.get("sustained"),
                "source": row.get("source"),
            }
            for row in snap.get("top_disk_write", [])[:5]
        ],
        "class_summary": snap.get("class_summary"),
        "source_attribution": build_source_attribution(snap),
        "defender_exclusions": snap.get("defender_exclusions"),
        "defender_cfa": snap.get("defender_cfa"),
        "mcp_session": (
            load_mcp_session_doctor()
            if profile in {"standard", "deep"}
            else {"ok": None, "skipped": True, "reason": "quick_profile"}
        ),
    }


def validate(observe_seconds: float = 10.0, top: int = 15, profile: str = "standard") -> dict[str, Any]:
    profile = normalize_profile(profile)
    doc = doctor(observe_seconds=observe_seconds, top=top, profile=profile)
    failures = [issue for issue in doc.get("issues", []) if issue.get("severity") in {"blocker", "risk"}]
    return {
        "schema": "performance.validate.v1",
        "ok": not failures,
        "generated_at": now_iso(),
        "profile": profile,
        "failures": failures,
        "advisory_count": sum(1 for issue in doc.get("issues", []) if issue.get("severity") == "advisory"),
        "note": "Validation is advisory for human performance work; it never stops processes or changes settings.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only workstation performance doctor")
    parser.add_argument("action", choices=["snapshot", "doctor", "repair-plan", "metrics", "validate"])
    parser.add_argument("--observe-seconds", type=float, default=10.0)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--profile", choices=sorted(PROFILES), default=None, help="quick avoids deep WMI/resource probes; deep includes all maintenance checks")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = args.profile
    if profile is None:
        profile = "quick" if args.action == "metrics" else "deep" if args.action == "repair-plan" else "standard"
    if args.action == "snapshot":
        payload = sample(args.observe_seconds, args.top, profile)
    elif args.action == "doctor":
        payload = doctor(args.observe_seconds, args.top, profile)
    elif args.action == "repair-plan":
        payload = repair_plan(args.observe_seconds, args.top, profile)
    elif args.action == "metrics":
        payload = metrics(args.observe_seconds, args.top, profile)
    else:
        payload = validate(args.observe_seconds, args.top, profile)
    if args.action == "doctor":
        print(summary_report(args.observe_seconds, args.top, profile, doctor_payload=payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
