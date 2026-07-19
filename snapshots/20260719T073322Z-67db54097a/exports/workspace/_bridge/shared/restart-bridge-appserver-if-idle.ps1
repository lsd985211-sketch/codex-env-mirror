param(
  [string]$HostName = "127.0.0.1",
  [int]$AppServerPort = 18791,
  [string]$Mode = "dry-run",
  [string]$Confirm = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BridgeRoot = Join-Path $Root "mobile_openclaw_bridge"
$Cli = Join-Path $BridgeRoot "mobile_openclaw_cli.py"
$OpenDashboard = Join-Path $BridgeRoot "open-dashboard.ps1"
$ResourceDoctor = Join-Path $Root "resource_process_doctor.py"
$ReportDir = Join-Path $Root "logs\bridge_appserver_governance"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

$Python = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
  $Python = (Get-Command python -ErrorAction Stop).Source
}

function Convert-FromJsonSafe {
  param([string]$Text)
  if (-not $Text) { return $null }
  return $Text | ConvertFrom-Json
}

function Get-BridgeSummary {
  $raw = & $Python $Cli maintenance metrics
  if ($LASTEXITCODE -ne 0) {
    throw "maintenance metrics failed"
  }
  return Convert-FromJsonSafe ($raw -join "`n")
}

function Get-AppServerOwner {
  $connections = @()
  try {
    $connections = @(Get-NetTCPConnection -LocalAddress $HostName -LocalPort $AppServerPort -State Listen -ErrorAction Stop)
  } catch {
    $connections = @()
  }
  $owners = @()
  foreach ($connection in $connections) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
    $owners += [pscustomobject]@{
      pid = [int]$connection.OwningProcess
      name = if ($proc) { [string]$proc.Name } else { "" }
      command_line = if ($proc) { [string]$proc.CommandLine } else { "" }
    }
  }
  return $owners
}

function Get-IntValue {
  param(
    [object]$Object,
    [string]$Name
  )
  if ($null -eq $Object) { return 0 }
  $prop = $Object.PSObject.Properties[$Name]
  if ($null -eq $prop -or $null -eq $prop.Value) { return 0 }
  try { return [int]$prop.Value } catch { return 0 }
}

function Get-RecentBridgeActivity {
  param([int]$Minutes = 10)
  $db = Join-Path $BridgeRoot "mobile_openclaw_bridge.db"
  if (-not (Test-Path -LiteralPath $db)) {
    return [pscustomobject]@{
      ok = $true
      block_restart = $false
      reason = "bridge_db_missing"
      minutes = $Minutes
      active_count = 0
      recent_event_count = 0
      recent_events = @()
    }
  }
  $probe = @'
import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

db_path = sys.argv[1]
minutes = int(sys.argv[2])
cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
active_statuses = ("pending", "queued_for_codex", "sent_to_codex", "processing")
event_types = (
    "queued_for_codex",
    "thread_route_selected",
    "sent_to_codex",
    "codex_turn_started",
    "active_poll_observation",
    "active_recovery_retry_scheduled",
    "recovery_protocol_violation_no_owned_result",
    "app_server_repair_continuation_started",
)
try:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    active = [
        dict(row)
        for row in con.execute(
            "SELECT id, status, receiver_account_id, codex_thread_id, updated_at, sent_to_codex_at "
            "FROM mobile_tasks "
            "WHERE status IN (?,?,?,?) "
            "ORDER BY updated_at DESC "
            "LIMIT 12",
            active_statuses,
        )
    ]
    placeholders = ",".join(["?"] * len(event_types))
    event_sql = (
        "SELECT task_id, event_type, created_at "
        "FROM mobile_events "
        "WHERE created_at >= ? "
        f"AND event_type IN ({placeholders}) "
        "ORDER BY id DESC "
        "LIMIT 20"
    )
    recent = [
        dict(row)
        for row in con.execute(event_sql, (cutoff.isoformat(), *event_types))
    ]
    print(json.dumps({
        "ok": True,
        "block_restart": bool(active or recent),
        "reason": "active_or_recent_bridge_activity" if (active or recent) else "none",
        "minutes": minutes,
        "active_count": len(active),
        "recent_event_count": len(recent),
        "active_tasks": active,
        "recent_events": recent,
    }, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({
        "ok": False,
        "block_restart": True,
        "reason": "bridge_activity_probe_failed",
        "minutes": minutes,
        "error": str(exc),
        "active_count": 0,
        "recent_event_count": 0,
        "recent_events": [],
    }, ensure_ascii=False))
'@
  $raw = $probe | & $Python - $db $Minutes
  return Convert-FromJsonSafe ($raw -join "`n")
}

if ($Mode -eq "apply") {
  if ($Confirm -ne "restart-idle-bridge-appserver") {
    throw "Apply mode requires -Confirm restart-idle-bridge-appserver"
  }
} elseif ($Mode -ne "dry-run") {
  throw "Mode must be dry-run or apply"
}

$snapshot = Get-BridgeSummary
$queueMetrics = $snapshot.metrics.queue
$pending = Get-IntValue $queueMetrics "pending"
$queued = Get-IntValue $queueMetrics "queued_for_codex"
$sent = Get-IntValue $queueMetrics "sent_to_codex"
$processing = Get-IntValue $queueMetrics "processing"
$active = Get-IntValue $queueMetrics "active"
$supplementWaiting = Get-IntValue $queueMetrics "supplement_waiting_mcp_ack"
$baseIdle = ($pending -eq 0 -and $queued -eq 0 -and $sent -eq 0 -and $processing -eq 0 -and $active -eq 0 -and $supplementWaiting -eq 0)
$recentActivity = Get-RecentBridgeActivity -Minutes 10
$recentActivityBlocksRestart = (-not ($recentActivity -and $recentActivity.ok)) -or [bool]($recentActivity -and $recentActivity.block_restart)
$idle = ($baseIdle -and -not $recentActivityBlocksRestart)
$ownersBefore = @(Get-AppServerOwner)
$wouldRestart = $idle -and $ownersBefore.Count -eq 1
$results = @()

if ($Mode -eq "apply" -and $wouldRestart) {
  Stop-Process -Id $ownersBefore[0].pid -Force -ErrorAction Stop
  Start-Sleep -Milliseconds 800
  $raw = powershell -NoProfile -ExecutionPolicy Bypass -File $OpenDashboard -HostName $HostName -AppServerPort $AppServerPort -NoOpen -StartAppServer
  $results += [pscustomobject]@{
    action = "open-dashboard-restart-stack"
    output = ($raw -join "`n")
  }
  if (Test-Path -LiteralPath $ResourceDoctor) {
    $cleanupRaw = & $Python $ResourceDoctor cleanup --apply
    $results += [pscustomobject]@{
      action = "resource-process-cleanup-after-restart"
      output = ($cleanupRaw -join "`n")
    }
    $validateRaw = & $Python $ResourceDoctor validate
    $results += [pscustomobject]@{
      action = "resource-process-validate-after-cleanup"
      output = ($validateRaw -join "`n")
    }
  }
}

$ownersAfter = @(Get-AppServerOwner)
$payload = [pscustomobject]@{
  ok = if ($Mode -eq "apply") { $wouldRestart -and $ownersAfter.Count -eq 1 } else { $true }
  mode = $Mode
  applied = ($Mode -eq "apply" -and $wouldRestart)
  idle = $idle
  base_idle = $baseIdle
  idle_block_reason = if ($recentActivityBlocksRestart) { "recent_bridge_activity" } else { "" }
  recent_bridge_activity = $recentActivity
  would_restart = $wouldRestart
  app_server_port = $AppServerPort
  queue = [pscustomobject]@{
    pending = $pending
    queued_for_codex = $queued
    sent_to_codex = $sent
    processing = $processing
    active = $active
    supplement_waiting_mcp_ack = $supplementWaiting
  }
  owners_before = $ownersBefore
  owners_after = $ownersAfter
  results = $results
  dry_run_contract = [pscustomobject]@{
    kills_processes = ($Mode -eq "apply" -and $wouldRestart)
    starts_processes = ($Mode -eq "apply" -and $wouldRestart)
    writes_files = $false
    sends_messages = $false
    changes_bridge_state = $false
  }
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$report = Join-Path $ReportDir "bridge-appserver-idle-restart-$stamp.json"
$payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $report -Encoding UTF8
$payload | Add-Member -NotePropertyName report -NotePropertyValue $report
$payload | ConvertTo-Json -Depth 8
