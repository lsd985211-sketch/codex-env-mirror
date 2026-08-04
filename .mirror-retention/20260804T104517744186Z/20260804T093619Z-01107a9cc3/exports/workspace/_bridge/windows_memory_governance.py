#!/usr/bin/env python3
"""Govern Windows user-mode memory with bounded, role-aware evidence.

Ownership: live process classification, compact SQLite trends, repair routing,
and an optional low-overhead monitor task.
Non-goals: killing ordinary applications, disabling services or security
software, changing drivers, or replacing kernel-pool/resource-process owners.
State behavior: live inspection is read-only; ``capture`` writes derived
summaries; schedule installation requires an explicit confirmation token.
Caller context: Codex maintenance workflows and the unified maintenance map.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "_bridge") not in sys.path:
    sys.path.insert(0, str(ROOT / "_bridge"))

from bounded_output import governed_cli_payload  # noqa: E402
from resource_library_paths import RESOURCE_LIBRARY_ROOT  # noqa: E402
from shared.windows_powershell import powershell_encoded_command  # noqa: E402


SCHEMA = "windows_memory_governance.v1"
CLASSIFIER_VERSION = "2026-07-15.service-pagefile-v2"
OUTPUT_ROOT = RESOURCE_LIBRARY_ROOT / "诊断" / "内存治理"
DB_NAME = "windows_memory_trends.sqlite"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MIB = 1024 * 1024


PROCESS_SCRIPT = r"""
$ErrorActionPreference='Stop'
$os=Get-CimInstance Win32_OperatingSystem
$mem=Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
$pagefiles=@(Get-CimInstance Win32_PageFileUsage)
$servicesByPid=@{}
Get-CimInstance Win32_Service | Where-Object {$_.ProcessId -gt 0} | ForEach-Object {
  $key=[string]$_.ProcessId
  if(-not $servicesByPid.ContainsKey($key)){$servicesByPid[$key]=@()}
  $servicesByPid[$key]+=[string]$_.Name
}
$compressedProperty=$mem.CimInstanceProperties | Where-Object {$_.Name -eq 'CompressedPageCount'} | Select-Object -First 1
$compressedPageCount=if($compressedProperty){[int64]$compressedProperty.Value}else{$null}
$pagefileAllocatedMb=[double](($pagefiles | Measure-Object -Property AllocatedBaseSize -Sum).Sum)
$pagefileCurrentMb=[double](($pagefiles | Measure-Object -Property CurrentUsage -Sum).Sum)
$pagefilePeakMb=[double](($pagefiles | Measure-Object -Property PeakUsage -Sum).Sum)
$metrics=@{}
Get-Process -ErrorAction SilentlyContinue | ForEach-Object {
  $metrics[[string]$_.Id]=@{
    working_set_bytes=[int64]$_.WorkingSet64
    private_bytes=[int64]$_.PrivateMemorySize64
    handles=[int]$_.HandleCount
  }
}
$processes=Get-CimInstance Win32_Process | ForEach-Object {
  $m=$metrics[[string]$_.ProcessId]
  [pscustomobject]@{
    pid=[int]$_.ProcessId
    parent_pid=[int]$_.ParentProcessId
    name=[string]$_.Name
    command_line=[string]$_.CommandLine
    executable_path=[string]$_.ExecutablePath
    created_at=if($_.CreationDate){([datetime]$_.CreationDate).ToString('o')}else{''}
    working_set_bytes=if($m){[int64]$m.working_set_bytes}else{0}
    private_bytes=if($m){[int64]$m.private_bytes}else{0}
    handles=if($m){[int]$m.handles}else{0}
    services=@($servicesByPid[[string]$_.ProcessId] | Sort-Object -Unique)
  }
}
[pscustomobject]@{
  captured_at=(Get-Date).ToUniversalTime().ToString('o')
  system=@{
    total_memory_mb=[math]::Round([double]$os.TotalVisibleMemorySize/1024,1)
    available_memory_mb=[int]$mem.AvailableMBytes
    committed_percent=[int]$mem.PercentCommittedBytesInUse
    committed_mb=[math]::Round([double]$mem.CommittedBytes/1MB,1)
    commit_limit_mb=[math]::Round([double]$mem.CommitLimit/1MB,1)
    pool_nonpaged_mb=[math]::Round([double]$mem.PoolNonpagedBytes/1MB,1)
    pool_paged_mb=[math]::Round([double]$mem.PoolPagedBytes/1MB,1)
    cache_mb=[math]::Round([double]$mem.CacheBytes/1MB,1)
    system_cache_resident_mb=[math]::Round([double]$mem.SystemCacheResidentBytes/1MB,1)
    standby_cache_mb=[math]::Round(([double]$mem.StandbyCacheCoreBytes+[double]$mem.StandbyCacheNormalPriorityBytes+[double]$mem.StandbyCacheReserveBytes)/1MB,1)
    free_zero_page_list_mb=[math]::Round([double]$mem.FreeAndZeroPageListBytes/1MB,1)
    modified_page_list_mb=[math]::Round([double]$mem.ModifiedPageListBytes/1MB,1)
    pool_paged_resident_mb=[math]::Round([double]$mem.PoolPagedResidentBytes/1MB,1)
    system_driver_resident_mb=[math]::Round([double]$mem.SystemDriverResidentBytes/1MB,1)
    system_code_resident_mb=[math]::Round([double]$mem.SystemCodeResidentBytes/1MB,1)
    page_faults_per_sec=[double]$mem.PageFaultsPerSec
    pages_per_sec=[double]$mem.PagesPerSec
    pages_input_per_sec=[double]$mem.PagesInputPerSec
    pages_output_per_sec=[double]$mem.PagesOutputPerSec
    page_reads_per_sec=[double]$mem.PageReadsPerSec
    page_writes_per_sec=[double]$mem.PageWritesPerSec
    pagefile_count=[int]$pagefiles.Count
    pagefile_allocated_mb=[math]::Round($pagefileAllocatedMb,1)
    pagefile_current_usage_mb=[math]::Round($pagefileCurrentMb,1)
    pagefile_peak_usage_mb=[math]::Round($pagefilePeakMb,1)
    pagefile_usage_percent=if($pagefileAllocatedMb -gt 0){[math]::Round($pagefileCurrentMb*100/$pagefileAllocatedMb,1)}else{0}
    pagefile_peak_usage_percent=if($pagefileAllocatedMb -gt 0){[math]::Round($pagefilePeakMb*100/$pagefileAllocatedMb,1)}else{0}
    compression_metric_state=if($compressedProperty){'available'}else{'unavailable'}
    compressed_memory_mb=if($compressedPageCount -ne $null){[math]::Round([double]$compressedPageCount*4096/1MB,1)}else{$null}
    last_boot_time=([datetime]$os.LastBootUpTime).ToString('o')
  }
  processes=@($processes)
} | ConvertTo-Json -Compress -Depth 5
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_powershell(script: str, *, timeout: int = 30) -> str:
    completed = subprocess.run(
        powershell_encoded_command(script, no_logo=True),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "PowerShell collection failed").strip())
    return completed.stdout.strip()


def collect_live() -> dict[str, Any]:
    payload = json.loads(_run_powershell(PROCESS_SCRIPT, timeout=40))
    if not isinstance(payload, dict):
        raise ValueError("process snapshot must be an object")
    processes = payload.get("processes")
    if isinstance(processes, dict):
        payload["processes"] = [processes]
    elif not isinstance(processes, list):
        payload["processes"] = []
    return payload


def _contains(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def classify_process(row: dict[str, Any]) -> str:
    name = str(row.get("name") or "").casefold()
    command = str(row.get("command_line") or "").casefold()
    joined = f"{name} {command}"
    if _contains(
        joined,
        (
            "mcp_launch_guard.py",
            "mcp_profile_launcher.py",
            "_mcp_server.py",
            "mcp-server",
            " mcp proxy",
            "server-filesystem",
            "playwright-mcp",
            "chrome-devtools-mcp",
            "bridge_server_v2.py",
        ),
    ):
        return "mcp_sessions"
    webview_owner_match = re.search(r"--webview-exe-name=([^\s\"]+)", command)
    webview_owner = webview_owner_match.group(1).casefold() if webview_owner_match else ""
    if name in {"chatgpt.exe", "codex.exe"} or (name == "msedgewebview2.exe" and ("codex" in command or webview_owner in {"chatgpt.exe", "codex.exe"})):
        return "codex_desktop"
    if _contains(joined, ("mobile_openclaw", "openclaw.mjs gateway", "codex_scheduler_runner", "dashboard_live_state", "local_mcp_hub.py", "pmb.cli daemon")):
        return "automation_services"
    if name in {"msmpeng.exe", "nissrv.exe", "securityhealthservice.exe", "securityhealthsystray.exe", "sensece.exe"}:
        return "security"
    if name == "msedgewebview2.exe" and _contains(webview_owner, ("clash", "mihomo", "gameviewer", "tailscale", "zerotier", "parsec", "anydesk", "teamviewer")):
        return "network_remote"
    if name == "msedgewebview2.exe" and webview_owner in {"searchhost.exe", "startmenuexperiencehost.exe", "widgets.exe"}:
        return "windows_core"
    if name == "msedgewebview2.exe" and webview_owner:
        return "user_applications"
    if name in {"chrome.exe", "msedge.exe", "firefox.exe", "msedgewebview2.exe", "brave.exe"}:
        return "browser_webview"
    if name in {"winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe", "wps.exe", "wpp.exe", "et.exe", "wpscloudsvr.exe"}:
        return "office_productivity"
    if _contains(name, ("clash", "mihomo", "gameviewer", "tailscale", "zerotier", "parsec", "anydesk", "teamviewer")):
        return "network_remote"
    if name in {
        "svchost.exe",
        "services.exe",
        "lsass.exe",
        "csrss.exe",
        "conhost.exe",
        "wininit.exe",
        "winlogon.exe",
        "dwm.exe",
        "explorer.exe",
        "wmiprvse.exe",
        "searchindexer.exe",
        "searchhost.exe",
        "startmenuexperiencehost.exe",
        "shellexperiencehost.exe",
        "runtimebroker.exe",
        "applicationframehost.exe",
        "backgroundtaskhost.exe",
        "fontdrvhost.exe",
        "audiodg.exe",
        "spoolsv.exe",
        "taskhostw.exe",
        "dllhost.exe",
        "sihost.exe",
        "ctfmon.exe",
        "chsime.exe",
        "widgets.exe",
        "smartscreen.exe",
        "useroobebroker.exe",
        "lockapp.exe",
        "textinputhost.exe",
        "registry",
        "secure system",
        "system",
    }:
        return "windows_core"
    if _contains(name, ("nvidia", "nvdisplay", "omen", "hp.", "hpsystem", "amd", "intel", "realtek", "logi")):
        return "vendor_background"
    if name in {"python.exe", "pythonw.exe", "node.exe", "pwsh.exe", "powershell.exe", "java.exe", "javaw.exe"}:
        return "runtime_workers"
    return "user_applications"


def process_family(row: dict[str, Any]) -> str:
    name = str(row.get("name") or "unknown").casefold()
    command = str(row.get("command_line") or "")
    services = row.get("services")
    if name == "svchost.exe" and isinstance(services, list):
        service_names = sorted({str(item).strip().casefold() for item in services if str(item).strip()})
        if service_names:
            return "svchost:" + "+".join(service_names[:6])
    for pattern in (r"([^\s\"]+\.py)", r"([^\s\"]+\.(?:js|mjs))"):
        match = re.search(pattern, command, flags=re.IGNORECASE)
        if match:
            return PureWindowsPath(match.group(1).replace("/", "\\")).name.casefold()
    module_match = re.search(r"(?:^|\s)-m\s+([^\s\"]+)", command, flags=re.IGNORECASE)
    if module_match:
        return f"{name.removesuffix('.exe')}-module:{module_match.group(1).casefold()}"
    type_match = re.search(r"--type=([^\s]+)", command, flags=re.IGNORECASE)
    webview_owner_match = re.search(r"--webview-exe-name=([^\s\"]+)", command, flags=re.IGNORECASE)
    if name == "msedgewebview2.exe" and webview_owner_match:
        process_type = type_match.group(1).casefold() if type_match else "main"
        return f"webview:{webview_owner_match.group(1).casefold()}:{process_type}"
    if type_match:
        return f"{name}:{type_match.group(1).casefold()}"
    if "app-server" in command.casefold():
        return f"{name}:app-server"
    return name


def summarize(snapshot: dict[str, Any]) -> dict[str, Any]:
    category_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {"process_count": 0, "working_set_mb": 0.0, "private_mb": 0.0, "handles": 0})
    family_rows: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "process_count": 0,
            "working_set_mb": 0.0,
            "private_mb": 0.0,
            "handles": 0,
            "top_pid": 0,
            "top_pid_working_set_mb": 0.0,
            "top_private_pid": 0,
            "top_pid_private_mb": 0.0,
        }
    )
    for item in snapshot.get("processes", []):
        if not isinstance(item, dict):
            continue
        category = classify_process(item)
        family = process_family(item)
        ws_mb = round(int(item.get("working_set_bytes") or 0) / MIB, 3)
        private_mb = round(int(item.get("private_bytes") or 0) / MIB, 3)
        handles = int(item.get("handles") or 0)
        category_row = category_rows[category]
        category_row["process_count"] += 1
        category_row["working_set_mb"] += ws_mb
        category_row["private_mb"] += private_mb
        category_row["handles"] += handles
        family_row = family_rows[(category, family)]
        family_row["process_count"] += 1
        family_row["working_set_mb"] += ws_mb
        family_row["private_mb"] += private_mb
        family_row["handles"] += handles
        if ws_mb > family_row["top_pid_working_set_mb"]:
            family_row["top_pid"] = int(item.get("pid") or 0)
            family_row["top_pid_working_set_mb"] = ws_mb
        if private_mb > family_row["top_pid_private_mb"]:
            family_row["top_private_pid"] = int(item.get("pid") or 0)
            family_row["top_pid_private_mb"] = private_mb
    categories = [
        {
            "category": category,
            **{key: round(value, 1) if isinstance(value, float) else value for key, value in row.items()},
            "private_residency_gap_mb": round(max(float(row["private_mb"]) - float(row["working_set_mb"]), 0.0), 1),
        }
        for category, row in category_rows.items()
    ]
    categories.sort(key=lambda row: (-float(row["working_set_mb"]), str(row["category"])))
    families = [
        {
            "category": category,
            "family": family,
            **{key: round(value, 1) if isinstance(value, float) else value for key, value in row.items()},
            "private_residency_gap_mb": round(max(float(row["private_mb"]) - float(row["working_set_mb"]), 0.0), 1),
            "private_to_working_set_ratio": round(float(row["private_mb"]) / max(float(row["working_set_mb"]), 1.0), 2),
        }
        for (category, family), row in family_rows.items()
    ]
    families.sort(key=lambda row: (-float(row["working_set_mb"]), str(row["family"])))
    private_families = sorted(families, key=lambda row: (-float(row["private_mb"]), str(row["family"])))
    handle_families = sorted(families, key=lambda row: (-int(row["handles"]), str(row["family"])))
    commit_heavy_families = [
        row
        for row in private_families
        if float(row.get("private_mb") or 0.0) >= 512.0
        and float(row.get("private_residency_gap_mb") or 0.0) >= 384.0
        and float(row.get("private_to_working_set_ratio") or 0.0) >= 2.0
    ]
    system = dict(snapshot.get("system") or {})
    total = float(system.get("total_memory_mb") or 0.0)
    available = float(system.get("available_memory_mb") or 0.0)
    system["used_percent"] = round((1.0 - available / total) * 100.0, 1) if total else 0.0
    process_totals = {
        "working_set_mb": round(sum(float(row.get("working_set_mb") or 0.0) for row in categories), 1),
        "private_mb": round(sum(float(row.get("private_mb") or 0.0) for row in categories), 1),
        "handles": sum(int(row.get("handles") or 0) for row in categories),
    }
    process_totals["private_residency_gap_mb"] = round(max(process_totals["private_mb"] - process_totals["working_set_mb"], 0.0), 1)
    system["process_working_set_mb"] = process_totals["working_set_mb"]
    system["process_private_mb"] = process_totals["private_mb"]
    system["process_handle_count"] = process_totals["handles"]
    return {
        "schema": f"{SCHEMA}.summary",
        "ok": True,
        "captured_at": snapshot.get("captured_at") or now_iso(),
        "classifier_version": CLASSIFIER_VERSION,
        "system": system,
        "process_count": len(snapshot.get("processes", [])),
        "categories": categories,
        "top_families": families[:30],
        "top_private_families": private_families[:30],
        "top_handle_families": handle_families[:30],
        "commit_heavy_families": commit_heavy_families[:20],
        "top_service_host_families": [row for row in private_families if str(row.get("family") or "").startswith("svchost:")][:20],
        "family_count": len(families),
        "process_totals": process_totals,
        "policy": {
            "ordinary_process_auto_stop": False,
            "mcp_cleanup_owner": "resource_process_doctor.py cleanup --safe-apply",
            "kernel_pool_owner": "windows_kernel_pool_diagnostics.py doctor",
            "success_retention": "summary_plus_sqlite",
            "failure_retention": "complete_inline_error_and_targeted_evidence",
            "trend_segmentation": "same_boot_and_classifier_version_only",
        },
    }


def db_path(output_root: Path) -> Path:
    return output_root / DB_NAME


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY,
            captured_at TEXT NOT NULL UNIQUE,
            total_memory_mb REAL NOT NULL,
            available_memory_mb REAL NOT NULL,
            used_percent REAL NOT NULL,
            committed_percent REAL NOT NULL,
            committed_mb REAL NOT NULL DEFAULT 0,
            commit_limit_mb REAL NOT NULL DEFAULT 0,
            pool_nonpaged_mb REAL NOT NULL,
            pool_paged_mb REAL NOT NULL,
            cache_mb REAL NOT NULL DEFAULT 0,
            system_cache_resident_mb REAL NOT NULL DEFAULT 0,
            standby_cache_mb REAL NOT NULL DEFAULT 0,
            modified_page_list_mb REAL NOT NULL DEFAULT 0,
            free_zero_page_list_mb REAL NOT NULL DEFAULT 0,
            pages_input_per_sec REAL NOT NULL DEFAULT 0,
            pages_output_per_sec REAL NOT NULL DEFAULT 0,
            page_reads_per_sec REAL NOT NULL DEFAULT 0,
            pagefile_count INTEGER NOT NULL DEFAULT 0,
            pagefile_allocated_mb REAL NOT NULL DEFAULT 0,
            pagefile_current_usage_mb REAL NOT NULL DEFAULT 0,
            pagefile_peak_usage_mb REAL NOT NULL DEFAULT 0,
            pagefile_usage_percent REAL NOT NULL DEFAULT 0,
            pagefile_peak_usage_percent REAL NOT NULL DEFAULT 0,
            compression_metric_available INTEGER NOT NULL DEFAULT 0,
            compressed_memory_mb REAL NOT NULL DEFAULT -1,
            process_working_set_mb REAL NOT NULL DEFAULT 0,
            process_private_mb REAL NOT NULL DEFAULT 0,
            process_handle_count INTEGER NOT NULL DEFAULT 0,
            process_count INTEGER NOT NULL,
            last_boot_time TEXT NOT NULL DEFAULT '',
            classifier_version TEXT NOT NULL DEFAULT 'legacy',
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS categories (
            sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            process_count INTEGER NOT NULL,
            working_set_mb REAL NOT NULL,
            private_mb REAL NOT NULL,
            handles INTEGER NOT NULL,
            PRIMARY KEY(sample_id, category)
        );
        CREATE TABLE IF NOT EXISTS families (
            sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            family TEXT NOT NULL,
            process_count INTEGER NOT NULL,
            working_set_mb REAL NOT NULL,
            private_mb REAL NOT NULL,
            handles INTEGER NOT NULL,
            top_pid INTEGER NOT NULL,
            top_pid_working_set_mb REAL NOT NULL,
            top_private_pid INTEGER NOT NULL DEFAULT 0,
            top_pid_private_mb REAL NOT NULL DEFAULT 0,
            private_residency_gap_mb REAL NOT NULL DEFAULT 0,
            private_to_working_set_ratio REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(sample_id, category, family)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_samples_time ON samples(captured_at DESC);
        CREATE INDEX IF NOT EXISTS idx_memory_families_ws ON families(sample_id, working_set_mb DESC);
        """
    )
    existing_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(samples)")}
    migrations = {
        "committed_mb": "REAL NOT NULL DEFAULT 0",
        "commit_limit_mb": "REAL NOT NULL DEFAULT 0",
        "cache_mb": "REAL NOT NULL DEFAULT 0",
        "system_cache_resident_mb": "REAL NOT NULL DEFAULT 0",
        "standby_cache_mb": "REAL NOT NULL DEFAULT 0",
        "modified_page_list_mb": "REAL NOT NULL DEFAULT 0",
        "free_zero_page_list_mb": "REAL NOT NULL DEFAULT 0",
        "pages_input_per_sec": "REAL NOT NULL DEFAULT 0",
        "pages_output_per_sec": "REAL NOT NULL DEFAULT 0",
        "page_reads_per_sec": "REAL NOT NULL DEFAULT 0",
        "pagefile_count": "INTEGER NOT NULL DEFAULT 0",
        "pagefile_allocated_mb": "REAL NOT NULL DEFAULT 0",
        "pagefile_current_usage_mb": "REAL NOT NULL DEFAULT 0",
        "pagefile_peak_usage_mb": "REAL NOT NULL DEFAULT 0",
        "pagefile_usage_percent": "REAL NOT NULL DEFAULT 0",
        "pagefile_peak_usage_percent": "REAL NOT NULL DEFAULT 0",
        "compression_metric_available": "INTEGER NOT NULL DEFAULT 0",
        "compressed_memory_mb": "REAL NOT NULL DEFAULT -1",
        "process_working_set_mb": "REAL NOT NULL DEFAULT 0",
        "process_private_mb": "REAL NOT NULL DEFAULT 0",
        "process_handle_count": "INTEGER NOT NULL DEFAULT 0",
        "last_boot_time": "TEXT NOT NULL DEFAULT ''",
        "classifier_version": "TEXT NOT NULL DEFAULT 'legacy'",
    }
    for column, definition in migrations.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE samples ADD COLUMN {column} {definition}")
    existing_family_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(families)")}
    family_migrations = {
        "top_private_pid": "INTEGER NOT NULL DEFAULT 0",
        "top_pid_private_mb": "REAL NOT NULL DEFAULT 0",
        "private_residency_gap_mb": "REAL NOT NULL DEFAULT 0",
        "private_to_working_set_ratio": "REAL NOT NULL DEFAULT 0",
    }
    for column, definition in family_migrations.items():
        if column not in existing_family_columns:
            conn.execute(f"ALTER TABLE families ADD COLUMN {column} {definition}")
    conn.commit()
    return conn


def index_summary(summary: dict[str, Any], output_root: Path) -> dict[str, Any]:
    system = dict(summary.get("system") or {})
    path = db_path(output_root)
    conn = connect(path)
    try:
        with conn:
            old = conn.execute("SELECT id FROM samples WHERE captured_at=?", (summary["captured_at"],)).fetchone()
            if old:
                conn.execute("DELETE FROM samples WHERE id=?", (int(old["id"]),))
            cursor = conn.execute(
                """INSERT INTO samples(
                    captured_at,total_memory_mb,available_memory_mb,used_percent,
                    committed_percent,committed_mb,commit_limit_mb,
                    pool_nonpaged_mb,pool_paged_mb,cache_mb,system_cache_resident_mb,
                    standby_cache_mb,modified_page_list_mb,free_zero_page_list_mb,
                    pages_input_per_sec,pages_output_per_sec,page_reads_per_sec,
                    pagefile_count,pagefile_allocated_mb,pagefile_current_usage_mb,
                    pagefile_peak_usage_mb,pagefile_usage_percent,pagefile_peak_usage_percent,
                    compression_metric_available,compressed_memory_mb,process_working_set_mb,
                    process_private_mb,process_handle_count,process_count,last_boot_time,
                    classifier_version,indexed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    summary["captured_at"],
                    float(system.get("total_memory_mb") or 0.0),
                    float(system.get("available_memory_mb") or 0.0),
                    float(system.get("used_percent") or 0.0),
                    float(system.get("committed_percent") or 0.0),
                    float(system.get("committed_mb") or 0.0),
                    float(system.get("commit_limit_mb") or 0.0),
                    float(system.get("pool_nonpaged_mb") or 0.0),
                    float(system.get("pool_paged_mb") or 0.0),
                    float(system.get("cache_mb") or 0.0),
                    float(system.get("system_cache_resident_mb") or 0.0),
                    float(system.get("standby_cache_mb") or 0.0),
                    float(system.get("modified_page_list_mb") or 0.0),
                    float(system.get("free_zero_page_list_mb") or 0.0),
                    float(system.get("pages_input_per_sec") or 0.0),
                    float(system.get("pages_output_per_sec") or 0.0),
                    float(system.get("page_reads_per_sec") or 0.0),
                    int(system.get("pagefile_count") or 0),
                    float(system.get("pagefile_allocated_mb") or 0.0),
                    float(system.get("pagefile_current_usage_mb") or 0.0),
                    float(system.get("pagefile_peak_usage_mb") or 0.0),
                    float(system.get("pagefile_usage_percent") or 0.0),
                    float(system.get("pagefile_peak_usage_percent") or 0.0),
                    1 if system.get("compression_metric_state") == "available" else 0,
                    float(system.get("compressed_memory_mb")) if system.get("compressed_memory_mb") is not None else -1.0,
                    float(system.get("process_working_set_mb") or 0.0),
                    float(system.get("process_private_mb") or 0.0),
                    int(system.get("process_handle_count") or 0),
                    int(summary.get("process_count") or 0),
                    str(system.get("last_boot_time") or ""),
                    str(summary.get("classifier_version") or CLASSIFIER_VERSION),
                    now_iso(),
                ),
            )
            sample_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO categories VALUES(?,?,?,?,?,?)",
                [
                    (sample_id, row["category"], int(row["process_count"]), float(row["working_set_mb"]), float(row["private_mb"]), int(row["handles"]))
                    for row in summary.get("categories", [])
                ],
            )
            family_index: dict[tuple[str, str], dict[str, Any]] = {}
            for collection in ("top_families", "top_private_families", "top_handle_families"):
                for row in summary.get(collection, []):
                    if isinstance(row, dict):
                        family_index[(str(row.get("category") or ""), str(row.get("family") or ""))] = row
            conn.executemany(
                """INSERT INTO families(
                    sample_id,category,family,process_count,working_set_mb,private_mb,
                    handles,top_pid,top_pid_working_set_mb,top_private_pid,
                    top_pid_private_mb,private_residency_gap_mb,private_to_working_set_ratio
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        sample_id,
                        row["category"],
                        row["family"],
                        int(row["process_count"]),
                        float(row["working_set_mb"]),
                        float(row["private_mb"]),
                        int(row["handles"]),
                        int(row["top_pid"]),
                        float(row["top_pid_working_set_mb"]),
                        int(row.get("top_private_pid") or 0),
                        float(row.get("top_pid_private_mb") or 0.0),
                        float(row.get("private_residency_gap_mb") or 0.0),
                        float(row.get("private_to_working_set_ratio") or 0.0),
                    )
                    for row in family_index.values()
                ],
            )
    finally:
        conn.close()
    return {"db_path": str(path), "sample_id": sample_id}


def capture(output_root: Path) -> dict[str, Any]:
    summary = summarize(collect_live())
    index = index_summary(summary, output_root)
    return {**summary, "indexed": True, **index}


def metrics(output_root: Path, *, limit: int = 12) -> dict[str, Any]:
    path = db_path(output_root)
    if not path.is_file():
        return {"schema": f"{SCHEMA}.metrics", "ok": True, "db_path": str(path), "sample_count": 0, "latest": {}, "categories": [], "top_families": [], "top_private_families": [], "top_handle_families": []}
    conn = connect(path)
    try:
        sample_count = int(conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0])
        latest = conn.execute("SELECT * FROM samples ORDER BY captured_at DESC LIMIT 1").fetchone()
        if not latest:
            categories: list[dict[str, Any]] = []
            families: list[dict[str, Any]] = []
        else:
            sample_id = int(latest["id"])
            categories = [dict(row) for row in conn.execute("SELECT category,process_count,working_set_mb,private_mb,handles FROM categories WHERE sample_id=? ORDER BY working_set_mb DESC", (sample_id,))]
            family_limit = max(1, min(limit, 50))
            family_columns = "category,family,process_count,working_set_mb,private_mb,handles,top_pid,top_pid_working_set_mb,top_private_pid,top_pid_private_mb,private_residency_gap_mb,private_to_working_set_ratio"
            families = [dict(row) for row in conn.execute(f"SELECT {family_columns} FROM families WHERE sample_id=? ORDER BY working_set_mb DESC LIMIT ?", (sample_id, family_limit))]
            private_families = [dict(row) for row in conn.execute(f"SELECT {family_columns} FROM families WHERE sample_id=? ORDER BY private_mb DESC LIMIT ?", (sample_id, family_limit))]
            handle_families = [dict(row) for row in conn.execute(f"SELECT {family_columns} FROM families WHERE sample_id=? ORDER BY handles DESC LIMIT ?", (sample_id, family_limit))]
    finally:
        conn.close()
    return {"schema": f"{SCHEMA}.metrics", "ok": True, "db_path": str(path), "sample_count": sample_count, "latest": dict(latest) if latest else {}, "categories": categories, "top_families": families, "top_private_families": private_families if latest else [], "top_handle_families": handle_families if latest else []}


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def trends(output_root: Path, *, hours: float = 24.0, limit: int = 48, categories: list[str] | None = None) -> dict[str, Any]:
    path = db_path(output_root)
    selected_categories = {str(item).strip() for item in (categories or []) if str(item).strip()}
    if not path.is_file():
        return {"schema": f"{SCHEMA}.trends", "ok": True, "db_path": str(path), "sample_count": 0, "samples": [], "category_deltas_mb": {}}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.1, float(hours)))
    conn = connect(path)
    try:
        raw_samples = [dict(row) for row in conn.execute("SELECT * FROM samples ORDER BY captured_at DESC LIMIT ?", (max(1, min(int(limit), 200)),))]
        in_window = [row for row in raw_samples if (_parse_time(row.get("captured_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
        reference = in_window[0] if in_window else (raw_samples[0] if raw_samples else {})
        current_boot = str(reference.get("last_boot_time") or "")
        current_classifier = str(reference.get("classifier_version") or "legacy")
        samples = [
            row
            for row in in_window
            if (not current_boot or str(row.get("last_boot_time") or "") == current_boot)
            and str(row.get("classifier_version") or "legacy") == current_classifier
        ]
        excluded_missing_segment_count = sum(1 for row in in_window if current_boot and not str(row.get("last_boot_time") or ""))
        excluded_boot_count = sum(
            1
            for row in in_window
            if current_boot
            and str(row.get("last_boot_time") or "")
            and str(row.get("last_boot_time") or "") != current_boot
        )
        excluded_classifier_count = sum(
            1
            for row in in_window
            if (not current_boot or str(row.get("last_boot_time") or "") == current_boot)
            and str(row.get("classifier_version") or "legacy") != current_classifier
        )
        samples.reverse()
        sample_ids = [int(row["id"]) for row in samples]
        category_rows: list[dict[str, Any]] = []
        if sample_ids:
            placeholders = ",".join("?" for _ in sample_ids)
            category_rows = [dict(row) for row in conn.execute(f"SELECT sample_id,category,working_set_mb,private_mb,handles,process_count FROM categories WHERE sample_id IN ({placeholders}) ORDER BY sample_id", sample_ids)]
    finally:
        conn.close()
    by_sample: dict[int, dict[str, dict[str, float | int]]] = defaultdict(dict)
    for row in category_rows:
        category = str(row.get("category") or "")
        if selected_categories and category not in selected_categories:
            continue
        by_sample[int(row["sample_id"])][category] = {
            "working_set_mb": round(float(row.get("working_set_mb") or 0.0), 1),
            "private_mb": round(float(row.get("private_mb") or 0.0), 1),
            "handles": int(row.get("handles") or 0),
            "process_count": int(row.get("process_count") or 0),
        }
    result_samples = [
        {
            "captured_at": row["captured_at"],
            "used_percent": row["used_percent"],
            "available_memory_mb": row["available_memory_mb"],
            "committed_percent": row["committed_percent"],
            "committed_mb": row.get("committed_mb", 0.0),
            "commit_limit_mb": row.get("commit_limit_mb", 0.0),
            "pool_nonpaged_mb": row["pool_nonpaged_mb"],
            "pool_paged_mb": row.get("pool_paged_mb", 0.0),
            "cache_mb": row.get("cache_mb", 0.0),
            "standby_cache_mb": row.get("standby_cache_mb", 0.0),
            "modified_page_list_mb": row.get("modified_page_list_mb", 0.0),
            "free_zero_page_list_mb": row.get("free_zero_page_list_mb", 0.0),
            "pages_input_per_sec": row.get("pages_input_per_sec", 0.0),
            "pages_output_per_sec": row.get("pages_output_per_sec", 0.0),
            "pagefile_count": row.get("pagefile_count", 0),
            "pagefile_allocated_mb": row.get("pagefile_allocated_mb", 0.0),
            "pagefile_current_usage_mb": row.get("pagefile_current_usage_mb", 0.0),
            "pagefile_peak_usage_mb": row.get("pagefile_peak_usage_mb", 0.0),
            "pagefile_usage_percent": row.get("pagefile_usage_percent", 0.0),
            "pagefile_peak_usage_percent": row.get("pagefile_peak_usage_percent", 0.0),
            "compression_metric_available": bool(row.get("compression_metric_available", 0)),
            "compressed_memory_mb": None if float(row.get("compressed_memory_mb", -1.0)) < 0 else row.get("compressed_memory_mb"),
            "process_working_set_mb": row.get("process_working_set_mb", 0.0),
            "process_private_mb": row.get("process_private_mb", 0.0),
            "process_handle_count": row.get("process_handle_count", 0),
            "category_metrics": by_sample.get(int(row["id"]), {}),
            "category_working_set_mb": {
                category: float(values.get("working_set_mb", 0.0))
                for category, values in by_sample.get(int(row["id"]), {}).items()
            },
        }
        for row in samples
    ]
    deltas: dict[str, float] = {}
    private_deltas: dict[str, float] = {}
    handle_deltas: dict[str, int] = {}
    process_deltas: dict[str, int] = {}
    if len(result_samples) >= 2:
        first = result_samples[0]["category_metrics"]
        last = result_samples[-1]["category_metrics"]
        for category in sorted(set(first) | set(last)):
            first_row = first.get(category, {})
            last_row = last.get(category, {})
            deltas[category] = round(float(last_row.get("working_set_mb", 0.0)) - float(first_row.get("working_set_mb", 0.0)), 1)
            private_deltas[category] = round(float(last_row.get("private_mb", 0.0)) - float(first_row.get("private_mb", 0.0)), 1)
            handle_deltas[category] = int(last_row.get("handles", 0)) - int(first_row.get("handles", 0))
            process_deltas[category] = int(last_row.get("process_count", 0)) - int(first_row.get("process_count", 0))
    system_delta_fields = (
        "available_memory_mb",
        "committed_mb",
        "pool_nonpaged_mb",
        "pool_paged_mb",
        "standby_cache_mb",
        "pagefile_current_usage_mb",
        "process_working_set_mb",
        "process_private_mb",
        "process_handle_count",
    )
    system_deltas: dict[str, float | int] = {}
    if len(result_samples) >= 2:
        first_sample = result_samples[0]
        last_sample = result_samples[-1]
        for field in system_delta_fields:
            delta = float(last_sample.get(field) or 0.0) - float(first_sample.get(field) or 0.0)
            system_deltas[field] = int(delta) if field == "process_handle_count" else round(delta, 1)
    return {
        "schema": f"{SCHEMA}.trends",
        "ok": True,
        "db_path": str(path),
        "window_hours": float(hours),
        "sample_count": len(result_samples),
        "segment": {"last_boot_time": current_boot, "classifier_version": current_classifier},
        "excluded_sample_count": excluded_missing_segment_count + excluded_boot_count + excluded_classifier_count,
        "excluded_by_missing_segment_metadata_count": excluded_missing_segment_count,
        "excluded_by_boot_count": excluded_boot_count,
        "excluded_by_classifier_count": excluded_classifier_count,
        "category_filter": sorted(selected_categories),
        "category_deltas_mb": deltas,
        "category_private_deltas_mb": private_deltas,
        "category_handle_deltas": handle_deltas,
        "category_process_deltas": process_deltas,
        "system_deltas": system_deltas,
        "samples": result_samples,
    }


def kernel_pool_summary() -> dict[str, Any]:
    """Read the specialized kernel-pool owner without duplicating its repair authority."""
    try:
        import windows_kernel_pool_diagnostics
        import windows_kernel_pool_governance

        payload = windows_kernel_pool_governance.doctor(windows_kernel_pool_diagnostics.OUTPUT_ROOT)
        issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
        current_nonpaged = payload.get("current_nonpaged_mb") or (payload.get("current") or {}).get("pool_nonpaged_mb")
        if current_nonpaged is None:
            current_nonpaged = next(
                (row.get("current_mb") for row in issues if isinstance(row, dict) and row.get("code") == "nonpaged_pool_high"),
                None,
            )
        return {
            "ok": bool(payload.get("ok")),
            "sample_count": int(payload.get("sample_count") or 0),
            "window_hours": payload.get("window_hours"),
            "current_nonpaged_mb": current_nonpaged,
            "rates_mb_per_hour": payload.get("rates_mb_per_hour") or {},
            "current_category_mb": payload.get("current_category_mb") or {},
            "issues": issues,
            "owner": "windows_kernel_pool_diagnostics.py",
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "issues": [], "owner": "windows_kernel_pool_diagnostics.py"}


def resource_process_summary() -> dict[str, Any]:
    try:
        import resource_process_doctor

        snapshot = resource_process_doctor.process_snapshot()
        groups = snapshot.get("groups") if isinstance(snapshot.get("groups"), list) else []
        mcp_groups = [
            group
            for group in groups
            if "mcp" in str(group.get("category") or "").casefold()
            or str(group.get("group") or "") in resource_process_doctor.SESSION_OWNED_STDIO_GROUPS
        ]
        root_count = sum(int(group.get("root_instance_count") or group.get("count") or 0) for group in groups)
        mcp_root_count = sum(int(group.get("root_instance_count") or group.get("count") or 0) for group in mcp_groups)
        mcp_working_set_mb = round(sum(float(group.get("working_set_mb") or 0.0) for group in mcp_groups), 1)
        fanout_groups = [
            {
                "group": str(group.get("group") or ""),
                "root_instance_count": int(group.get("root_instance_count") or group.get("count") or 0),
                "effective_expected_max": int(group.get("effective_expected_max") or group.get("expected_max") or 0),
            }
            for group in groups
            if int(group.get("excess") or 0) > 0
        ]
        return {
            "ok": bool(snapshot.get("ok")),
            "root_instance_count": root_count,
            "mcp_root_instance_count": mcp_root_count,
            "mcp_working_set_mb": mcp_working_set_mb,
            "mcp_instance_budget_state": resource_process_doctor.mcp_budget_state(mcp_root_count, mcp_working_set_mb),
            "fanout_group_count": len(fanout_groups),
            "fanout_groups": fanout_groups[:8],
            "owner": "resource_process_doctor.py",
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def build_pressure_layers(
    summary: dict[str, Any],
    kernel_pool: dict[str, Any],
    resource_process: dict[str, Any],
) -> dict[str, Any]:
    system = dict(summary.get("system") or {})
    categories = [row for row in summary.get("categories", []) if isinstance(row, dict)]
    largest_category = categories[0] if categories else {}
    kernel_issues = [row for row in kernel_pool.get("issues", []) if isinstance(row, dict)]
    kernel_risk = any(str(row.get("severity") or "") == "risk" for row in kernel_issues)
    used = float(system.get("used_percent") or 0.0)
    available = float(system.get("available_memory_mb") or 0.0)
    committed = float(system.get("committed_percent") or 0.0)
    if kernel_risk or float(system.get("pool_nonpaged_mb") or 0.0) >= 2048.0:
        dominant = "kernel_pool"
        owner = "windows_kernel_pool_diagnostics.py"
    elif resource_process.get("mcp_instance_budget_state") in {"advisory", "risk"}:
        dominant = "mcp_process_lifecycle"
        owner = "resource_process_doctor.py"
    elif committed >= 80.0:
        dominant = "system_commit"
        owner = "windows_memory_governance.py"
    elif used >= 85.0 or available < 2048.0:
        dominant = "user_process_residency"
        owner = "windows_memory_governance.py"
    else:
        dominant = "no_single_pressure_layer"
        owner = "windows_memory_governance.py"
    return {
        "schema": f"{SCHEMA}.pressure_layers",
        "dominant_layer": dominant,
        "next_owner": owner,
        "layers": {
            "physical": {
                "used_percent": used,
                "available_memory_mb": available,
                "process_working_set_mb": system.get("process_working_set_mb", 0.0),
                "note": "process working sets are not additive with kernel/cache residency and can share pages",
            },
            "commit": {
                "committed_percent": committed,
                "committed_mb": system.get("committed_mb", 0.0),
                "commit_limit_mb": system.get("commit_limit_mb", 0.0),
                "pagefile_count": system.get("pagefile_count", 0),
                "pagefile_allocated_mb": system.get("pagefile_allocated_mb", 0.0),
                "pagefile_current_usage_mb": system.get("pagefile_current_usage_mb", 0.0),
                "pagefile_peak_usage_mb": system.get("pagefile_peak_usage_mb", 0.0),
                "pagefile_usage_percent": system.get("pagefile_usage_percent", 0.0),
                "pagefile_peak_usage_percent": system.get("pagefile_peak_usage_percent", 0.0),
                "pages_input_per_sec": system.get("pages_input_per_sec", 0.0),
                "pages_output_per_sec": system.get("pages_output_per_sec", 0.0),
            },
            "kernel_pool": {
                "pool_nonpaged_mb": system.get("pool_nonpaged_mb", 0.0),
                "pool_paged_mb": system.get("pool_paged_mb", 0.0),
                "owner_ok": kernel_pool.get("ok"),
                "issues": kernel_issues,
                "current_category_mb": kernel_pool.get("current_category_mb") or {},
                "rates_mb_per_hour": kernel_pool.get("rates_mb_per_hour") or {},
            },
            "cache_and_standby": {
                "cache_mb": system.get("cache_mb", 0.0),
                "system_cache_resident_mb": system.get("system_cache_resident_mb", 0.0),
                "standby_cache_mb": system.get("standby_cache_mb", 0.0),
                "free_zero_page_list_mb": system.get("free_zero_page_list_mb", 0.0),
                "modified_page_list_mb": system.get("modified_page_list_mb", 0.0),
                "compression_metric_state": system.get("compression_metric_state", "unavailable"),
                "compressed_memory_mb": system.get("compressed_memory_mb"),
                "note": "cache and standby are interpreted by trend and I/O context, not treated as disposable waste",
            },
            "user_processes": {
                "process_count": summary.get("process_count", 0),
                "working_set_mb": (summary.get("process_totals") or {}).get("working_set_mb", 0.0),
                "private_mb": (summary.get("process_totals") or {}).get("private_mb", 0.0),
                "handles": (summary.get("process_totals") or {}).get("handles", 0),
                "largest_category": largest_category,
                "commit_heavy_families": summary.get("commit_heavy_families", [])[:5],
                "top_service_host_families": summary.get("top_service_host_families", [])[:5],
            },
            "mcp_process_lifecycle": resource_process,
        },
    }


def doctor(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    summary = summarize(collect_live())
    system = dict(summary.get("system") or {})
    issues: list[dict[str, Any]] = []
    used = float(system.get("used_percent") or 0.0)
    available = float(system.get("available_memory_mb") or 0.0)
    nonpaged = float(system.get("pool_nonpaged_mb") or 0.0)
    if used >= 90.0 or available < 1024.0:
        issues.append({"severity": "risk", "code": "physical_memory_pressure", "used_percent": used, "available_memory_mb": available})
    elif used >= 85.0 or available < 2048.0:
        issues.append({"severity": "advisory", "code": "physical_memory_pressure", "used_percent": used, "available_memory_mb": available})
    if nonpaged >= 2048.0:
        issues.append({"severity": "risk", "code": "kernel_nonpaged_pool_pressure", "pool_nonpaged_mb": nonpaged, "owner": "windows_kernel_pool_diagnostics.py"})
    committed = float(system.get("committed_percent") or 0.0)
    if committed >= 85.0:
        issues.append({"severity": "risk", "code": "system_commit_pressure", "committed_percent": committed, "owner": "windows_memory_governance.py"})
    elif committed >= 75.0:
        issues.append({"severity": "advisory", "code": "system_commit_pressure", "committed_percent": committed, "owner": "windows_memory_governance.py"})
    pagefile_count = int(system.get("pagefile_count") or 0)
    pagefile_usage = float(system.get("pagefile_usage_percent") or 0.0)
    if pagefile_count == 0 and committed >= 75.0:
        issues.append({"severity": "advisory", "code": "commit_without_pagefile_headroom", "committed_percent": committed, "owner": "windows_memory_governance.py"})
    elif pagefile_usage >= 90.0 and committed >= 85.0:
        issues.append({"severity": "risk", "code": "pagefile_and_commit_pressure", "pagefile_usage_percent": pagefile_usage, "committed_percent": committed, "owner": "windows_memory_governance.py"})
    elif pagefile_usage >= 75.0 and committed >= 75.0:
        issues.append({"severity": "advisory", "code": "pagefile_and_commit_pressure", "pagefile_usage_percent": pagefile_usage, "committed_percent": committed, "owner": "windows_memory_governance.py"})
    for family in summary.get("top_families", [])[:10]:
        if float(family.get("working_set_mb") or 0.0) >= 1000.0:
            issues.append({"severity": "advisory", "code": "large_user_process_family", "family": family.get("family"), "category": family.get("category"), "working_set_mb": family.get("working_set_mb")})
    for family in summary.get("commit_heavy_families", [])[:5]:
        family_name = str(family.get("family") or "")
        owner = "local_pmb_memory.py" if "pmb.cli" in family_name else "human_or_app_owner"
        issues.append({
            "severity": "advisory",
            "code": "high_private_commit_low_residency",
            "family": family_name,
            "category": family.get("category"),
            "private_mb": family.get("private_mb"),
            "working_set_mb": family.get("working_set_mb"),
            "private_residency_gap_mb": family.get("private_residency_gap_mb"),
            "private_to_working_set_ratio": family.get("private_to_working_set_ratio"),
            "top_private_pid": family.get("top_private_pid"),
            "owner": owner,
            "interpretation": "commit demand may remain high even when physical residency is low; inspect the owner before requesting restart or cache eviction",
        })
    resource_process = resource_process_summary()
    if resource_process.get("mcp_instance_budget_state") in {"advisory", "risk"}:
        issues.append({"severity": str(resource_process["mcp_instance_budget_state"]), "code": "mcp_instance_pressure", "owner": "resource_process_doctor.py", **resource_process})
    kernel_pool = kernel_pool_summary()
    kernel_owner_issues = [row for row in kernel_pool.get("issues", []) if isinstance(row, dict)]
    if any(str(row.get("severity") or "") == "risk" for row in kernel_owner_issues):
        issues.append({
            "severity": "risk",
            "code": "kernel_pool_owner_reports_risk",
            "owner": "windows_kernel_pool_diagnostics.py",
            "issue_codes": [str(row.get("code") or "") for row in kernel_owner_issues],
        })
    trend = trends(output_root, hours=24.0, limit=24) if db_path(output_root).is_file() else {"ok": True, "sample_count": 0, "category_deltas_mb": {}}
    recent_samples = [row for row in trend.get("samples", []) if isinstance(row, dict)][-3:]
    paging_samples = [row for row in recent_samples if float(row.get("pages_output_per_sec") or 0.0) > 0.0]
    if available < 2048.0 and len(recent_samples) >= 3 and len(paging_samples) >= 2:
        issues.append({
            "severity": "advisory",
            "code": "paging_under_low_available_memory",
            "observed_samples": len(paging_samples),
            "sample_window": len(recent_samples),
            "latest_pages_output_per_sec": system.get("pages_output_per_sec"),
            "available_memory_mb": available,
        })
    private_deltas = trend.get("category_private_deltas_mb") or {}
    handle_deltas = trend.get("category_handle_deltas") or {}
    process_deltas = trend.get("category_process_deltas") or {}
    for category, delta in (trend.get("category_deltas_mb") or {}).items():
        latest_category = next((row for row in summary.get("categories", []) if row.get("category") == category), {})
        corroborated = (
            float(private_deltas.get(category) or 0.0) >= 250.0
            or int(handle_deltas.get(category) or 0) >= 2000
            or int(process_deltas.get(category) or 0) >= 3
        )
        if float(delta or 0.0) >= 500.0 and float(latest_category.get("working_set_mb") or 0.0) >= 500.0 and corroborated:
            issues.append({
                "severity": "advisory",
                "code": "category_working_set_growth",
                "category": category,
                "delta_mb_24h": round(float(delta), 1),
                "private_delta_mb_24h": round(float(private_deltas.get(category) or 0.0), 1),
                "handle_delta_24h": int(handle_deltas.get(category) or 0),
                "process_delta_24h": int(process_deltas.get(category) or 0),
                "current_working_set_mb": latest_category.get("working_set_mb"),
                "owner": "windows_memory_governance.py",
            })
    pressure_layers = build_pressure_layers(summary, kernel_pool, resource_process)
    return {
        "schema": f"{SCHEMA}.doctor",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "system": system,
        "categories": summary.get("categories", []),
        "top_families": summary.get("top_families", [])[:15],
        "top_private_families": summary.get("top_private_families", [])[:15],
        "top_handle_families": summary.get("top_handle_families", [])[:15],
        "commit_heavy_families": summary.get("commit_heavy_families", [])[:10],
        "top_service_host_families": summary.get("top_service_host_families", [])[:10],
        "process_totals": summary.get("process_totals", {}),
        "resource_process": resource_process,
        "kernel_pool": kernel_pool,
        "trend_summary": {
            "sample_count": trend.get("sample_count", 0),
            "category_deltas_mb": trend.get("category_deltas_mb") or {},
            "category_private_deltas_mb": trend.get("category_private_deltas_mb") or {},
            "category_handle_deltas": trend.get("category_handle_deltas") or {},
            "category_process_deltas": trend.get("category_process_deltas") or {},
            "system_deltas": trend.get("system_deltas") or {},
        },
        "pressure_layers": pressure_layers,
        "decision": {
            "dominant_layer": pressure_layers.get("dominant_layer"),
            "next_owner": pressure_layers.get("next_owner"),
            "rule": "specialized owner evidence outranks generic process totals; cache and working-set values are contextual, not independently additive",
        },
        "issues": issues,
        "evidence": {
            "source": "live_windows_process_service_pagefile_and_memory_counters_plus_owner_sqlite_summaries",
            "raw_process_rows_persisted": False,
            "interpretation_basis": "Microsoft memory-counter, Job Object, WDDM, PoolMon, and WPR guidance; durable source receipts belong in closeout evidence",
        },
    }


def repair_plan(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    diagnosis = doctor(output_root)
    actions: list[dict[str, Any]] = []
    codes = {str(item.get("code") or "") for item in diagnosis.get("issues", [])}
    if "mcp_instance_pressure" in codes:
        actions.append({"priority": 1, "owner": "resource_process_doctor.py", "command": "python _bridge\\resource_process_doctor.py repair-plan", "apply_boundary": "cleanup --safe-apply --apply after a fresh owner plan", "auto_stop_ordinary_apps": False})
    if "kernel_nonpaged_pool_pressure" in codes:
        actions.append({"priority": 1, "owner": "windows_kernel_pool_diagnostics.py", "command": "python _bridge\\windows_kernel_pool_diagnostics.py doctor", "apply_boundary": "driver/service/firewall changes require separate evidence-led approval"})
    kernel_codes = {
        str(item.get("code") or "")
        for item in (diagnosis.get("kernel_pool") or {}).get("issues", [])
        if isinstance(item, dict)
    }
    if "graphics_kernel_pool_pressure" in kernel_codes:
        actions.append({
            "priority": 1,
            "owner": "windows_kernel_pool_diagnostics.py",
            "command": "python _bridge\\windows_kernel_pool_diagnostics.py capture --label graphics-pressure --summary-only --top 40",
            "reason": "refresh PoolMon attribution before any NVIDIA/WDDM driver action; use a single-profile memory-mode or custom-buffer WPR trace only when driver-level evidence is required",
            "forbidden": ["restart_display_driver_without_recovery_plan", "change_gpu_driver_from_process_counter_alone", "combined_gpu_pool_wpr_filemode_without_size_budget"],
        })
    if "firewall_filter_pool_growth" in kernel_codes:
        actions.append({
            "priority": 1,
            "owner": "windows_kernel_pool_diagnostics.py",
            "command": "python _bridge\\windows_kernel_pool_diagnostics.py wfp-plan",
            "reason": "plan only; apply remains fingerprint-locked, reversible, and limited to missing local artifacts",
        })
    if "system_commit_pressure" in codes:
        actions.append({
            "priority": 2,
            "owner": "windows_memory_governance.py",
            "command": "python _bridge\\windows_memory_governance.py trends --hours 24 --limit 48",
            "reason": "separate sustained private-byte growth from transient working-set and cache residency before changing applications or page-file policy",
        })
    if codes & {"pagefile_and_commit_pressure", "commit_without_pagefile_headroom"}:
        actions.append({
            "priority": 2,
            "owner": "windows_memory_governance.py",
            "command": "python _bridge\\windows_memory_governance.py trends --hours 24 --limit 48",
            "reason": "correlate commit, page-file use, modified pages, and paging I/O before any page-file configuration change",
            "forbidden": ["disable_pagefile", "resize_pagefile_from_one_sample", "treat_hard_faults_as_pagefile_faults_without_correlation"],
        })
    commit_heavy_issues = [item for item in diagnosis.get("issues", []) if item.get("code") == "high_private_commit_low_residency"]
    if any(item.get("owner") == "local_pmb_memory.py" for item in commit_heavy_issues):
        actions.append({
            "priority": 2,
            "owner": "local_pmb_memory.py",
            "command": "python _bridge\\local_pmb_memory.py doctor",
            "reason": "verify idle-exit and on-demand recovery policy for PMB commit before requesting a daemon restart",
            "forbidden": ["kill_pmb_by_pid", "disable_memory_service", "restart_current_codex_session"],
        })
    growth_categories = sorted({str(item.get("category") or "") for item in diagnosis.get("issues", []) if item.get("code") == "category_working_set_growth" and item.get("category")})
    for category in growth_categories:
        actions.append({
            "priority": 2,
            "owner": "windows_memory_governance.py",
            "command": f"python _bridge\\windows_memory_governance.py trends --hours 24 --limit 48 --category {category}",
            "reason": "review working-set, private-byte, handle, and process-count deltas for the affected category",
        })
    if "physical_memory_pressure" in codes:
        actions.append({"priority": 2, "owner": "windows_memory_governance.py", "command": "python _bridge\\windows_memory_governance.py capture", "reason": "compare role and family trends before requesting app restart or startup changes"})
    actions.append({"priority": 3, "owner": "human_or_app_owner", "command": "review persistent optional background categories only", "forbidden": ["kill_by_process_name", "disable_defender", "disable_windows_services", "restart_current_codex_session_without_explicit_request"]})
    return {"schema": f"{SCHEMA}.repair_plan", "ok": True, "generated_at": now_iso(), "diagnosis": diagnosis, "actions": actions, "guardrails": ["specialized owner remains authoritative", "one category and one variable per remediation batch", "validate functionality and memory after every applied batch", "do not clear standby/cache or trim working sets as a substitute for source attribution", "use Job Objects only for owner-controlled worker trees, never arbitrary applications or shared services", "never combine built-in GPU and Pool WPR profiles in file mode without a measured buffer and output-size budget"]}


def validate(output_root: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        summary = summarize(collect_live())
        checks.append({"name": "live_collection", "ok": summary.get("process_count", 0) > 0, "process_count": summary.get("process_count")})
        checks.append({"name": "classification_coverage", "ok": sum(int(row.get("process_count") or 0) for row in summary.get("categories", [])) == int(summary.get("process_count") or 0)})
        required_metrics = {
            "committed_mb",
            "commit_limit_mb",
            "cache_mb",
            "standby_cache_mb",
            "free_zero_page_list_mb",
            "modified_page_list_mb",
            "pages_input_per_sec",
            "pages_output_per_sec",
            "process_working_set_mb",
            "process_private_mb",
            "process_handle_count",
            "pagefile_count",
            "pagefile_allocated_mb",
            "pagefile_current_usage_mb",
            "pagefile_peak_usage_mb",
            "pagefile_usage_percent",
        }
        system = dict(summary.get("system") or {})
        checks.append({"name": "layered_system_metrics", "ok": required_metrics.issubset(system), "missing": sorted(required_metrics - set(system))})
        checks.append({"name": "compression_capability_state", "ok": system.get("compression_metric_state") in {"available", "unavailable"}, "state": system.get("compression_metric_state")})
    except Exception as exc:
        checks.append({"name": "live_collection", "ok": False, "reason": str(exc)})
    path = db_path(output_root)
    try:
        conn = connect(path)
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sample_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(samples)")}
        family_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(families)")}
        conn.close()
        required_columns = {"committed_mb", "commit_limit_mb", "cache_mb", "standby_cache_mb", "process_private_mb", "process_handle_count", "pagefile_current_usage_mb", "pagefile_usage_percent", "compression_metric_available", "last_boot_time", "classifier_version"}
        required_family_columns = {"top_private_pid", "top_pid_private_mb", "private_residency_gap_mb", "private_to_working_set_ratio"}
        checks.append({"name": "sqlite_schema", "ok": {"samples", "categories", "families"}.issubset(tables) and required_columns.issubset(sample_columns) and required_family_columns.issubset(family_columns), "missing_columns": sorted((required_columns - sample_columns) | (required_family_columns - family_columns)), "db_path": str(path)})
    except Exception as exc:
        checks.append({"name": "sqlite_schema", "ok": False, "reason": str(exc), "db_path": str(path)})
    return {"schema": f"{SCHEMA}.validate", "ok": all(bool(item.get("ok")) for item in checks), "checks": checks}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Windows user-mode memory governance")
    root.add_argument("command", choices=("capture", "metrics", "trends", "doctor", "repair-plan", "validate"))
    root.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    root.add_argument("--limit", type=int, default=12)
    root.add_argument("--hours", type=float, default=24.0)
    root.add_argument("--category", action="append", default=[])
    root.add_argument("--full", action="store_true")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "capture":
            payload = capture(args.output_root.resolve())
        elif args.command == "metrics":
            payload = metrics(args.output_root.resolve(), limit=max(1, min(args.limit, 50)))
        elif args.command == "trends":
            payload = trends(args.output_root.resolve(), hours=max(0.1, args.hours), limit=max(1, min(args.limit, 200)), categories=list(args.category or []))
        elif args.command == "doctor":
            payload = doctor(args.output_root.resolve())
        elif args.command == "repair-plan":
            payload = repair_plan(args.output_root.resolve())
        else:
            payload = validate(args.output_root.resolve())
    except Exception as exc:
        payload = {"schema": f"{SCHEMA}.error", "ok": False, "error_class": type(exc).__name__, "reason": str(exc)}
    output = governed_cli_payload(payload, full=bool(args.full), full_result_ref=f"command:python _bridge/windows_memory_governance.py {args.command} --full")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
