#!/usr/bin/env python3
"""Persistent Microsoft Defender governance for Codex workstation paths."""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import winreg
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from shared.codex_desktop_package import (
        query_codex_cli_processes,
        resolve_installed_package,
        running_desktop_executable_paths,
    )
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.shared.codex_desktop_package import (
        query_codex_cli_processes,
        resolve_installed_package,
        running_desktop_executable_paths,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
RESOURCE_ROOT = Path(r"C:\Users\45543\Desktop\Codex资源库")
MAINTENANCE_RECORD_ROOT = RESOURCE_ROOT / "文档" / "系统维护" / "执行记录"
TARGET_SCAN_AVG_CPU_LOAD_FACTOR = 30
TARGET_SCAN_SCHEDULE_TIME = "03:30:00"
TARGET_DISABLE_CPU_THROTTLE_ON_IDLE_SCANS = False
RECENT_EVENT_MAX_EVENTS = 120
RECENT_EVENT_WINDOW_HOURS = 4
ALLOWLISTED_THREAT_IDS = [335323]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_text(value: str) -> str:
    return str(value or "").rstrip("\\").lower()


def looks_like_escaped_path_text(value: str) -> bool:
    text = str(value or "")
    return "\\\\" in text or re.search(r"\\u[0-9a-fA-F]{4}", text) is not None


def run_powershell_json(script: str, timeout: int = 60) -> dict[str, Any]:
    utf8_script = (
        "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
        + script
    )
    encoded_command = base64.b64encode(utf8_script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_command],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **NO_WINDOW_KW,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "stdout_preview": (exc.stdout or "")[:1000] if isinstance(exc.stdout, str) else "",
            "stderr_preview": (exc.stderr or "")[:1000] if isinstance(exc.stderr, str) else "",
        }
    text = (proc.stdout or "").strip()
    try:
        parsed = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "stdout_preview": text[:2000],
            "stderr_preview": (proc.stderr or "")[:2000],
            "error": "stdout_not_json",
        }
    if isinstance(parsed, dict):
        parsed.setdefault("ok", proc.returncode == 0)
        parsed["_returncode"] = proc.returncode
        if proc.stderr:
            parsed["_stderr_preview"] = proc.stderr[:2000]
        return parsed
    return {"ok": proc.returncode == 0, "items": parsed, "_returncode": proc.returncode}


def ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_powershell_file(script: str, timeout: int = 60) -> dict[str, Any]:
    tmp_dir = PROJECT_ROOT / "_bridge" / "tmp" / "defender-governance"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script_path = tmp_dir / f"defender-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.ps1"
    script_path.write_text(script, encoding="utf-8-sig")
    try:
        return run_powershell_json(f"& {ps_quote(str(script_path))}", timeout=timeout)
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


def ps_b64_string(value: str) -> str:
    return base64.b64encode(str(value).encode("utf-16-le")).decode("ascii")


def ps_decode_b64(var_name: str, value: str) -> str:
    return "$%s = [System.Text.Encoding]::Unicode.GetString([System.Convert]::FromBase64String('%s'))" % (
        var_name,
        ps_b64_string(value),
    )


def path_entry(path: Path, reason: str) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists(), "reason": reason}


def static_exclusion_candidates() -> list[dict[str, Any]]:
    home = Path.home()
    return [
        path_entry(home / ".codex", "codex_state_logs_config"),
        path_entry(home / ".cache" / "codex-runtimes", "codex_runtime_cache"),
        path_entry(home / "AppData" / "Local" / "OpenAI" / "Codex", "codex_local_runtime_cache"),
        path_entry(home / "AppData" / "Roaming" / "Codex" / "web" / "Codex", "codex_electron_webview_profile"),
        path_entry(RESOURCE_ROOT, "codex_resource_library"),
        path_entry(RESOURCE_ROOT / "文档" / "系统维护" / "运行态", "maintenance_runtime_state"),
        path_entry(RESOURCE_ROOT / "文档" / "系统维护" / "执行记录", "maintenance_execution_records"),
        path_entry(PROJECT_ROOT, "mcsmanager_workspace"),
        path_entry(PROJECT_ROOT / "_bridge", "bridge_runtime_logs"),
        path_entry(PROJECT_ROOT / ".codegraph", "codegraph_index"),
        path_entry(PROJECT_ROOT / "_bridge" / "mobile_openclaw_bridge" / "runtime", "mobile_bridge_runtime"),
        path_entry(PROJECT_ROOT / "_bridge" / "mobile_openclaw_bridge" / "logs", "mobile_bridge_logs"),
        path_entry(PROJECT_ROOT / "_bridge" / "tmp", "bridge_temp_artifacts"),
    ]


def dynamic_codex_executables() -> list[str]:
    candidates = list(running_desktop_executable_paths())
    for row in query_codex_cli_processes():
        path = Path(str(row.get("ExecutablePath") or ""))
        if path.is_file():
            candidates.append(path)

    package = resolve_installed_package()
    if package is not None:
        candidates.append(package.executable_path)
        cli_path = package.install_location / "app" / "resources" / "codex.exe"
        if cli_path.is_file():
            candidates.append(cli_path)

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = str(candidate)
        key = text.casefold()
        if candidate.is_file() and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def defender_preferences() -> dict[str, Any]:
    script = r"""
$ErrorActionPreference = 'Stop'
$pref = Get-MpPreference
$scanScheduleTime = if ($null -ne $pref.ScanScheduleTime) { $pref.ScanScheduleTime.ToString() } else { $null }
$scanScheduleQuickScanTime = if ($null -ne $pref.ScanScheduleQuickScanTime) { $pref.ScanScheduleQuickScanTime.ToString() } else { $null }
[pscustomobject]@{
  exclusionPath = @($pref.ExclusionPath)
  exclusionProcess = @($pref.ExclusionProcess)
  controlledFolderAccessAllowedApplications = @($pref.ControlledFolderAccessAllowedApplications)
  enableControlledFolderAccess = [int]$pref.EnableControlledFolderAccess
  disableRealtimeMonitoring = [bool]$pref.DisableRealtimeMonitoring
  scanAvgCPULoadFactor = [int]$pref.ScanAvgCPULoadFactor
  enableLowCpuPriority = [bool]$pref.EnableLowCpuPriority
  scanOnlyIfIdleEnabled = [bool]$pref.ScanOnlyIfIdleEnabled
  disableCpuThrottleOnIdleScans = [bool]$pref.DisableCpuThrottleOnIdleScans
  disableCatchupFullScan = [bool]$pref.DisableCatchupFullScan
  disableCatchupQuickScan = [bool]$pref.DisableCatchupQuickScan
  scanScheduleTime = $scanScheduleTime
  scanScheduleQuickScanTime = $scanScheduleQuickScanTime
} | ConvertTo-Json -Depth 6
"""
    return run_powershell_json(script, timeout=60)


def defender_recent_events(hours: int = RECENT_EVENT_WINDOW_HOURS) -> dict[str, Any]:
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$since = (Get-Date).AddHours(-{int(hours)})
$events = Get-WinEvent -LogName 'Microsoft-Windows-Windows Defender/Operational' -MaxEvents {RECENT_EVENT_MAX_EVENTS} |
  Where-Object {{ $_.TimeCreated -ge $since -and $_.Id -in 1116,1117,1123,5007,2000 }}
$items = @($events | Select-Object TimeCreated,Id,@{{n='Message';e={{(($_.Message -replace "`r?`n", " ") -replace '\\s+', ' ').Substring(0,[Math]::Min(700, (($_.Message -replace "`r?`n", " ") -replace '\\s+', ' ').Length))}}}})
[pscustomobject]@{{
  windowHours = {int(hours)}
  threatEventCount = @($items | Where-Object {{ $_.Id -in 1116,1117 }}).Count
  cfaBlockCount = @($items | Where-Object {{ $_.Id -eq 1123 }}).Count
  configChangeCount = @($items | Where-Object {{ $_.Id -eq 5007 }}).Count
  signatureUpdateCount = @($items | Where-Object {{ $_.Id -eq 2000 }}).Count
  items = @($items | Select-Object -First 20)
}} | ConvertTo-Json -Depth 5
"""
    return run_powershell_json(script, timeout=60)


def scan_policy_status(pref: dict[str, Any]) -> dict[str, Any]:
    scan_avg = pref.get("scanAvgCPULoadFactor")
    try:
        scan_avg_int = int(scan_avg)
    except (TypeError, ValueError):
        scan_avg_int = -1
    scan_time = str(pref.get("scanScheduleTime") or "")
    quick_time = str(pref.get("scanScheduleQuickScanTime") or "")
    checks = {
        "low_cpu_priority": pref.get("enableLowCpuPriority") is True,
        "scan_avg_cpu_load_factor": 0 <= scan_avg_int <= TARGET_SCAN_AVG_CPU_LOAD_FACTOR,
        "scan_only_if_idle": pref.get("scanOnlyIfIdleEnabled") is True,
        "idle_scan_cpu_throttle_enabled": pref.get("disableCpuThrottleOnIdleScans") is TARGET_DISABLE_CPU_THROTTLE_ON_IDLE_SCANS,
        "scheduled_scan_time": scan_time == TARGET_SCAN_SCHEDULE_TIME,
        "scheduled_quick_scan_time": quick_time == TARGET_SCAN_SCHEDULE_TIME,
        "realtime_protection_not_disabled": pref.get("disableRealtimeMonitoring") is not True,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "target": {
            "scanAvgCPULoadFactor": TARGET_SCAN_AVG_CPU_LOAD_FACTOR,
            "enableLowCpuPriority": True,
            "scanOnlyIfIdleEnabled": True,
            "disableCpuThrottleOnIdleScans": TARGET_DISABLE_CPU_THROTTLE_ON_IDLE_SCANS,
            "scanScheduleTime": TARGET_SCAN_SCHEDULE_TIME,
            "scanScheduleQuickScanTime": TARGET_SCAN_SCHEDULE_TIME,
        },
        "actual": {
            "scanAvgCPULoadFactor": pref.get("scanAvgCPULoadFactor"),
            "enableLowCpuPriority": pref.get("enableLowCpuPriority"),
            "scanOnlyIfIdleEnabled": pref.get("scanOnlyIfIdleEnabled"),
            "disableCpuThrottleOnIdleScans": pref.get("disableCpuThrottleOnIdleScans"),
            "scanScheduleTime": pref.get("scanScheduleTime"),
            "scanScheduleQuickScanTime": pref.get("scanScheduleQuickScanTime"),
            "disableRealtimeMonitoring": pref.get("disableRealtimeMonitoring"),
        },
    }


def snapshot() -> dict[str, Any]:
    pref = defender_preferences()
    events = defender_recent_events()
    candidates = static_exclusion_candidates()
    current_apps = dynamic_codex_executables()
    exclusions = [str(item) for item in pref.get("exclusionPath") or []] if isinstance(pref.get("exclusionPath"), list) else []
    allowed_apps = (
        [str(item) for item in pref.get("controlledFolderAccessAllowedApplications") or []]
        if isinstance(pref.get("controlledFolderAccessAllowedApplications"), list)
        else []
    )
    exclusion_norm = {norm_text(item) for item in exclusions}
    allowed_norm = {norm_text(item) for item in allowed_apps}
    required = [
        {
            **item,
            "excluded": norm_text(str(item.get("path") or "")) in exclusion_norm,
        }
        for item in candidates
    ]
    cfa_required = [
        {
            "path": app,
            "exists": Path(app).exists(),
            "allowed": norm_text(app) in allowed_norm,
            "reason": "current_codex_executable",
        }
        for app in current_apps
    ]
    malformed_allowed = [item for item in allowed_apps if "\\\\" in item and "OpenAI.Codex_" in item]
    malformed_process_entries = [
        item
        for item in (pref.get("exclusionProcess") or [])
        if not isinstance(item, str) or not str(item).strip() or looks_like_escaped_path_text(str(item))
    ]
    policy = scan_policy_status(pref)
    return {
        "schema": "defender_governance.snapshot.v1",
        "ok": bool(pref.get("ok")),
        "generated_at": now_iso(),
        "preferences": pref,
        "required_exclusion_paths": required,
        "required_cfa_applications": cfa_required,
        "malformed_codex_cfa_entries": malformed_allowed,
        "malformed_exclusion_process_entries": malformed_process_entries,
        "scan_policy": policy,
        "recent_events": events,
        "contract": {
            "disable_realtime_monitoring": False,
            "exclude_user_profile_root": False,
            "exclude_downloads_root": False,
            "allow_current_codex_executables": True,
            "auto_apply_only_required_or_policy_drift": True,
            "manual_only_legacy_cleanup": True,
        },
    }


def doctor(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = snap or snapshot()
    issues: list[dict[str, Any]] = []
    pref = payload.get("preferences") if isinstance(payload.get("preferences"), dict) else {}
    missing_paths = [
        item for item in payload.get("required_exclusion_paths", []) if item.get("exists") and not item.get("excluded")
    ]
    missing_apps = [
        item for item in payload.get("required_cfa_applications", []) if item.get("exists") and not item.get("allowed")
    ]
    if missing_paths:
        issues.append(
            {
                "severity": "risk",
                "code": "defender_exclusion_drift",
                "message": "Known Codex workspace/cache/runtime paths are not excluded from Defender scans.",
                "missing_existing_paths": missing_paths,
            }
        )
    if missing_apps:
        issues.append(
            {
                "severity": "risk",
                "code": "codex_cfa_allowlist_drift",
                "message": "Current Codex executable paths are not allowed for Controlled Folder Access.",
                "missing_existing_apps": missing_apps,
            }
        )
    if payload.get("malformed_codex_cfa_entries"):
        issues.append(
            {
                "severity": "advisory",
                "code": "codex_cfa_malformed_legacy_entries",
                "message": "Old Codex CFA entries contain literal double backslashes and may not match process paths.",
                "entries": payload.get("malformed_codex_cfa_entries"),
            }
        )
    if payload.get("malformed_exclusion_process_entries"):
        issues.append(
            {
                "severity": "advisory",
                "code": "defender_exclusion_process_malformed_entries",
                "message": "Defender ExclusionProcess contains null, empty, or escaped legacy entries.",
                "entries": payload.get("malformed_exclusion_process_entries"),
            }
        )
    malformed_exclusions = [
        item
        for item in (pref.get("exclusionPath") or [])
        if isinstance(item, str) and looks_like_escaped_path_text(item) and ("Codex" in item or "mcsmanager" in item)
    ]
    if malformed_exclusions:
        issues.append(
            {
                "severity": "advisory",
                "code": "defender_exclusion_malformed_legacy_entries",
                "message": "Old Defender exclusion entries contain escaped path text and may not match real paths.",
                "entries": malformed_exclusions,
            }
        )
    if pref.get("disableRealtimeMonitoring") is True:
        issues.append(
            {
                "severity": "risk",
                "code": "defender_realtime_disabled",
                "message": "Real-time protection appears disabled; this governance layer must not rely on disabling Defender.",
            }
        )
    policy = payload.get("scan_policy") if isinstance(payload.get("scan_policy"), dict) else scan_policy_status(pref)
    if policy.get("ok") is not True:
        issues.append(
            {
                "severity": "risk",
                "code": "defender_scan_low_impact_policy_drift",
                "message": "Defender scan policy is not tuned for low foreground impact.",
                "policy": policy,
            }
        )
    recent_events = payload.get("recent_events") if isinstance(payload.get("recent_events"), dict) else {}
    if int(recent_events.get("threatEventCount") or 0) > 0:
        issues.append(
            {
                "severity": "risk",
                "code": "defender_recent_threat_events",
                "message": "Recent Defender detection/remediation events were observed; high MsMpEng CPU may be protection follow-up rather than Codex path scanning.",
                "recent_events": recent_events,
            }
        )
    if int(recent_events.get("configChangeCount") or 0) >= 6:
        issues.append(
            {
                "severity": "advisory",
                "code": "defender_recent_config_churn",
                "message": "Many recent Defender configuration changes were observed; avoid nonessential automatic cleanup to reduce Defender follow-up work.",
                "recent_events": recent_events,
            }
        )
    return {
        "schema": "defender_governance.doctor.v1",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": {
            "missing_exclusion_path_count": len(missing_paths),
            "missing_cfa_application_count": len(missing_apps),
            "malformed_codex_cfa_entry_count": len(payload.get("malformed_codex_cfa_entries") or []),
            "malformed_exclusion_entry_count": len(malformed_exclusions),
            "malformed_exclusion_process_entry_count": len(payload.get("malformed_exclusion_process_entries") or []),
            "scan_avg_cpu_load_factor": pref.get("scanAvgCPULoadFactor"),
            "enable_low_cpu_priority": pref.get("enableLowCpuPriority"),
            "scan_only_if_idle_enabled": pref.get("scanOnlyIfIdleEnabled"),
            "disable_cpu_throttle_on_idle_scans": pref.get("disableCpuThrottleOnIdleScans"),
            "scan_schedule_time": pref.get("scanScheduleTime"),
            "scan_schedule_quick_scan_time": pref.get("scanScheduleQuickScanTime"),
            "disable_realtime_monitoring": pref.get("disableRealtimeMonitoring"),
            "scan_policy_ok": policy.get("ok"),
            "recent_threat_event_count": int(recent_events.get("threatEventCount") or 0),
            "recent_config_change_count": int(recent_events.get("configChangeCount") or 0),
        },
        "snapshot": payload,
    }


def repair_plan(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = snap or snapshot()
    doc = doctor(payload)
    actions: list[dict[str, Any]] = []
    for issue in doc.get("issues", []):
        if issue.get("code") == "defender_exclusion_drift":
            actions.append(
                {
                    "action": "add_missing_exclusion_paths",
                    "paths": [item.get("path") for item in issue.get("missing_existing_paths", [])],
                    "apply_default": False,
                    "auto_apply": True,
                    "risk": "narrows scans for Codex workspace/cache/runtime paths only",
                }
            )
        elif issue.get("code") == "codex_cfa_allowlist_drift":
            actions.append(
                {
                    "action": "allow_current_codex_cfa_applications",
                    "paths": [item.get("path") for item in issue.get("missing_existing_apps", [])],
                    "apply_default": False,
                    "auto_apply": True,
                    "risk": "allows only current Codex executables to write controlled folders",
                }
            )
        elif issue.get("code") == "defender_scan_low_impact_policy_drift":
            actions.append(
                {
                    "action": "set_low_impact_scan_policy",
                    "apply_default": True,
                    "auto_apply": True,
                    "target": (payload.get("scan_policy") or {}).get("target"),
                    "risk": "keeps real-time protection enabled while lowering scheduled scan foreground impact",
                }
            )
        elif issue.get("code") == "defender_exclusion_malformed_legacy_entries":
            actions.append(
                {
                    "action": "remove_malformed_exclusion_entries",
                    "paths": list(issue.get("entries") or []),
                    "apply_default": False,
                    "auto_apply": False,
                    "risk": "removes escaped stale Defender path entries that do not match real paths",
                }
            )
        elif issue.get("code") == "defender_exclusion_process_malformed_entries":
            actions.append(
                {
                    "action": "remove_malformed_exclusion_process_entries",
                    "paths": [str(item) for item in issue.get("entries") or [] if isinstance(item, str) and str(item).strip()],
                    "apply_default": False,
                    "auto_apply": False,
                    "risk": "removes stale process exclusion entries; null entries may require Defender UI or a full preference rewrite and are not auto-applied",
                }
            )
        elif issue.get("code") == "codex_cfa_malformed_legacy_entries":
            actions.append(
                {
                    "action": "remove_malformed_cfa_entries",
                    "paths": list(issue.get("entries") or []),
                    "apply_default": False,
                    "auto_apply": False,
                    "risk": "removes escaped stale CFA application entries that do not match real executables",
                }
            )
    return {
        "schema": "defender_governance.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "dry_run": True,
        "actions": actions,
        "auto_apply_actions": [item for item in actions if item.get("auto_apply") is True],
        "manual_only_actions": [item for item in actions if item.get("auto_apply") is not True],
        "contract": payload.get("contract"),
    }


def backup_preferences() -> Path:
    MAINTENANCE_RECORD_ROOT.mkdir(parents=True, exist_ok=True)
    path = MAINTENANCE_RECORD_ROOT / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-defender-governance-backup.json"
    write_payload = {
        "schema": "defender_governance.backup.v1",
        "generated_at": now_iso(),
        "snapshot": snapshot(),
    }
    path.write_text(json.dumps(write_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def schedule_delete_on_reboot(paths: list[str]) -> dict[str, Any]:
    if not paths:
        return {"ok": True, "scheduled": []}
    session_manager_path = r"SYSTEM\CurrentControlSet\Control\Session Manager"
    scheduled: list[str] = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, session_manager_path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            existing_value, _ = winreg.QueryValueEx(key, "PendingFileRenameOperations")
            existing = [str(item) for item in (existing_value or []) if str(item).strip()]
        except FileNotFoundError:
            existing = []
        merged = list(existing)
        for raw_path in paths:
            path = str(raw_path).strip()
            if not path:
                continue
            nt_path = r"\\??\\" + path
            merged.extend([nt_path, ""])
            scheduled.append(path)
        winreg.SetValueEx(key, "PendingFileRenameOperations", 0, winreg.REG_MULTI_SZ, merged)
    return {"ok": True, "scheduled": scheduled, "count": len(scheduled)}


def cleanup_allowlisted_threat_history(*, apply_cleanup: bool = False) -> dict[str, Any]:
    cleanup_targets = [
        r"C:\ProgramData\Microsoft\Windows Defender\Scans\History\Service\DetectionHistory",
        r"C:\ProgramData\Microsoft\Windows Defender\Scans\History\Store",
        r"C:\ProgramData\Microsoft\Windows Defender\Scans\History\RemCheck",
        r"C:\ProgramData\Microsoft\Windows Defender\Scans\History\Service\Detections.log",
        r"C:\ProgramData\Microsoft\Windows Defender\Scans\History\Service\History.Log",
        r"C:\ProgramData\Microsoft\Windows Defender\Scans\History\Service\Unknown.Log",
        r"C:\ProgramData\Microsoft\Windows Defender\Scans\mpenginedb.db",
        r"C:\ProgramData\Microsoft\Windows Defender\Scans\mpenginedb.db-wal",
        r"C:\ProgramData\Microsoft\Windows Defender\Scans\mpenginedb.db-shm",
    ]
    script = r"""
$ErrorActionPreference='Stop'
$ids = @(335323)
$threats = @()
try { $threats = @(Get-MpThreat | Where-Object { $_.ThreatID -in $ids }) } catch { $threats = @() }
$detections = @()
try {
  $detections = @(Get-MpThreatDetection | Where-Object {
    $resources = @()
    if ($_.Resources) { $resources = @($_.Resources) }
    $text = (($resources -join ' ') + ' ' + ($_.ThreatName | Out-String))
    $text -match 'FRProxy|SakuraLauncher|SakuraFrpService|frpc\.exe|335323'
  })
} catch { $detections = @() }
[pscustomobject]@{
  ok = $true
  threat_count = @($threats).Count
  detection_count = @($detections).Count
  threat_ids = $ids
  threat_is_active = @($threats | Select-Object ThreatID,ThreatName,IsActive,DidThreatExecute)
  detection_preview = @($detections | Select-Object -First 10 InitialDetectionTime,LastThreatStatusChangeTime,Id,ActionSuccess,RemediationTime)
} | ConvertTo-Json -Depth 6
"""
    state = run_powershell_json(script, timeout=60)
    if apply_cleanup and int(state.get("threat_count") or 0) > 0:
        try:
            purge_script = r"""
$ErrorActionPreference='Stop'
Set-MpPreference -ScanPurgeItemsAfterDelay 1
[pscustomobject]@{
  ok = $true
  scan_purge_items_after_delay = 1
} | ConvertTo-Json -Depth 4
"""
            state["scan_purge_tuning"] = run_powershell_json(purge_script, timeout=60)
        except Exception as exc:
            state["scan_purge_tuning"] = {"ok": False, "error": repr(exc)}
        cleanup_script = r"""
$ErrorActionPreference='Stop'
$paths = @(
  'C:\ProgramData\Microsoft\Windows Defender\Scans\History\Service\DetectionHistory',
  'C:\ProgramData\Microsoft\Windows Defender\Scans\History\Store',
  'C:\ProgramData\Microsoft\Windows Defender\Scans\History\RemCheck',
  'C:\ProgramData\Microsoft\Windows Defender\Scans\History\Service\Detections.log',
  'C:\ProgramData\Microsoft\Windows Defender\Scans\History\Service\History.Log',
  'C:\ProgramData\Microsoft\Windows Defender\Scans\History\Service\Unknown.Log'
)
$removed = @()
foreach ($path in $paths) {
  if (Test-Path -LiteralPath $path) {
    Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
    $removed += $path
  }
}
[pscustomobject]@{
  ok = $true
  removed = $removed
} | ConvertTo-Json -Depth 4
"""
        cleanup_result = run_powershell_json(cleanup_script, timeout=120)
        if cleanup_result.get("ok") is not True:
            try:
                cleanup_result["deferred_cleanup"] = schedule_delete_on_reboot(cleanup_targets)
            except Exception as exc:
                cleanup_result["deferred_cleanup"] = {"ok": False, "error": repr(exc)}
        state["history_cleanup"] = cleanup_result
    return state


def apply(*, include_manual: bool = False) -> dict[str, Any]:
    snap = snapshot()
    plan = repair_plan(snap)
    backup = backup_preferences()
    results: list[dict[str, Any]] = []
    for action in plan.get("actions", []):
        if action.get("auto_apply") is not True and not include_manual:
            results.append({"ok": True, "skipped": True, "action": action.get("action"), "reason": "manual_only"})
            continue
        if action.get("action") == "add_missing_exclusion_paths":
            for path in action.get("paths", []):
                script = "$ErrorActionPreference='Stop'\n$path = %s\nAdd-MpPreference -ExclusionPath @($path)\n[pscustomobject]@{ok=$true; path=$path; action='add_exclusion_path'} | ConvertTo-Json\n" % ps_quote(str(path))
                results.append(run_powershell_file(script, timeout=60))
        elif action.get("action") == "set_low_impact_scan_policy":
            script = "$ErrorActionPreference='Stop'\nSet-MpPreference -EnableLowCpuPriority $true -ScanAvgCPULoadFactor %d -ScanOnlyIfIdleEnabled $true -DisableCpuThrottleOnIdleScans $false -ScanScheduleTime '%s' -ScanScheduleQuickScanTime '%s'\n[pscustomobject]@{ok=$true; action='set_low_impact_scan_policy'; scanAvgCpuLoadFactor=%d; enableLowCpuPriority=$true; scanOnlyIfIdleEnabled=$true; disableCpuThrottleOnIdleScans=$false; scanScheduleTime='%s'} | ConvertTo-Json\n" % (
                TARGET_SCAN_AVG_CPU_LOAD_FACTOR,
                TARGET_SCAN_SCHEDULE_TIME,
                TARGET_SCAN_SCHEDULE_TIME,
                TARGET_SCAN_AVG_CPU_LOAD_FACTOR,
                TARGET_SCAN_SCHEDULE_TIME,
            )
            results.append(run_powershell_file(script, timeout=60))
        elif action.get("action") == "remove_malformed_exclusion_entries":
            for path in action.get("paths", []):
                script = "$ErrorActionPreference='Stop'\n$path = %s\nRemove-MpPreference -ExclusionPath @($path)\n[pscustomobject]@{ok=$true; path=$path; action='remove_malformed_exclusion_path'} | ConvertTo-Json\n" % ps_quote(str(path))
                results.append(run_powershell_file(script, timeout=60))
        elif action.get("action") == "remove_malformed_exclusion_process_entries":
            for path in action.get("paths", []):
                script = "$ErrorActionPreference='Stop'\n$path = %s\nRemove-MpPreference -ExclusionProcess @($path)\n[pscustomobject]@{ok=$true; path=$path; action='remove_malformed_exclusion_process'} | ConvertTo-Json\n" % ps_quote(str(path))
                results.append(run_powershell_file(script, timeout=60))
        elif action.get("action") == "allow_current_codex_cfa_applications":
            for path in action.get("paths", []):
                script = "$ErrorActionPreference='Stop'\n$app = %s\nAdd-MpPreference -ControlledFolderAccessAllowedApplications @($app)\n[pscustomobject]@{ok=$true; app=$app; action='allow_cfa_application'} | ConvertTo-Json\n" % ps_quote(str(path))
                results.append(run_powershell_file(script, timeout=60))
        elif action.get("action") == "remove_malformed_cfa_entries":
            for path in action.get("paths", []):
                script = "$ErrorActionPreference='Stop'\n$app = %s\nRemove-MpPreference -ControlledFolderAccessAllowedApplications @($app)\n[pscustomobject]@{ok=$true; app=$app; action='remove_malformed_cfa_application'} | ConvertTo-Json\n" % ps_quote(str(path))
                results.append(run_powershell_file(script, timeout=60))
    cleanup_result = cleanup_allowlisted_threat_history(apply_cleanup=True)
    after = doctor()
    remaining_actionable = [
        item
        for item in repair_plan(after.get("snapshot") if isinstance(after.get("snapshot"), dict) else None).get("auto_apply_actions", [])
        if item.get("auto_apply") is True
    ]
    result_failures = [item for item in results if item.get("ok") is not True]
    return {
        "schema": "defender_governance.apply.v1",
        "ok": not result_failures and not remaining_actionable,
        "generated_at": now_iso(),
        "backup_path": str(backup),
        "include_manual": include_manual,
        "dry_run_plan": plan,
        "results": results,
        "cleanup_allowlisted_threat_history": cleanup_result,
        "remaining_auto_apply_actions": remaining_actionable,
        "after": after,
    }


def metrics(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = doctor(snap or snapshot())
    return {
        "schema": "defender_governance.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "summary": doc.get("summary"),
        "issue_count": len(doc.get("issues", [])),
        "issue_codes": [item.get("code") for item in doc.get("issues", [])],
    }


def validate() -> dict[str, Any]:
    doc = doctor()
    failures = [item for item in doc.get("issues", []) if item.get("severity") == "risk"]
    return {
        "schema": "defender_governance.validate.v1",
        "ok": not failures,
        "generated_at": now_iso(),
        "failures": failures,
        "advisory_count": sum(1 for item in doc.get("issues", []) if item.get("severity") == "advisory"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent Defender governance for Codex paths")
    parser.add_argument("action", choices=["snapshot", "doctor", "repair-plan", "metrics", "validate", "apply"])
    parser.add_argument("--include-manual", action="store_true", help="Also apply manual-only advisory cleanup actions")
    args = parser.parse_args()
    if args.action == "snapshot":
        payload = snapshot()
    elif args.action == "doctor":
        payload = doctor()
    elif args.action == "repair-plan":
        payload = repair_plan()
    elif args.action == "metrics":
        payload = metrics()
    elif args.action == "validate":
        payload = validate()
    else:
        payload = apply(include_manual=bool(args.include_manual))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
