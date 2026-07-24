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

$Python = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
  $Python = (Get-Command python -ErrorAction Stop).Source
}

function Convert-FromJsonSafe {
  param([string]$Text)
  if (-not $Text) { return $null }
  return $Text | ConvertFrom-Json
}

function Invoke-CapturedNative {
  param(
    [string]$FilePath,
    [string[]]$Arguments
  )
  $stdoutPath = [System.IO.Path]::GetTempFileName()
  $stderrPath = [System.IO.Path]::GetTempFileName()
  try {
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Wait -PassThru -NoNewWindow `
      -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    return [pscustomobject]@{
      exit_code = [int]$process.ExitCode
      stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -LiteralPath $stdoutPath -Raw } else { "" }
      stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw } else { "" }
    }
  } finally {
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
  }
}

function Get-BridgeSummary {
  $result = Invoke-CapturedNative -FilePath $Python -Arguments @($Cli, "maintenance", "metrics")
  $summary = Convert-FromJsonSafe $result.stdout
  if ($null -eq $summary) {
    throw "maintenance metrics returned no parseable JSON (exit=$($result.exit_code)): $($result.stderr)"
  }
  return $summary
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

if ($Mode -eq "apply") {
  if ($Confirm -ne "restart-idle-bridge-appserver") {
    throw "Apply mode requires -Confirm restart-idle-bridge-appserver"
  }
} elseif ($Mode -ne "dry-run") {
  throw "Mode must be dry-run or apply"
}

$snapshot = Get-BridgeSummary
$queueMetrics = if ($snapshot.metrics -and $snapshot.metrics.queue) { $snapshot.metrics.queue } else { $snapshot.queue }
$pending = Get-IntValue $queueMetrics "pending"
$queued = Get-IntValue $queueMetrics "queued_for_codex"
$sent = Get-IntValue $queueMetrics "sent_to_codex"
$processing = Get-IntValue $queueMetrics "processing"
$active = Get-IntValue $queueMetrics "active"
$supplementWaiting = Get-IntValue $queueMetrics "supplement_waiting_mcp_ack"
$baseIdle = ($pending -eq 0 -and $queued -eq 0 -and $sent -eq 0 -and $processing -eq 0 -and $active -eq 0 -and $supplementWaiting -eq 0)
$recentActivity = [pscustomobject]@{
  ok = [bool]$snapshot.ok
  block_restart = -not [bool]$snapshot.ok
  reason = if ($snapshot.ok) { "owner_metrics_healthy" } else { "owner_metrics_reported_risk" }
  issue_codes = @($snapshot.issue_codes)
}
$recentActivityBlocksRestart = [bool]$recentActivity.block_restart
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
    $cleanupResult = Invoke-CapturedNative -FilePath $Python -Arguments @($ResourceDoctor, "cleanup", "--apply")
    $results += [pscustomobject]@{
      action = "resource-process-cleanup-after-restart"
      exit_code = $cleanupResult.exit_code
      output = $cleanupResult.stdout
    }
    $validateResult = Invoke-CapturedNative -FilePath $Python -Arguments @($ResourceDoctor, "validate")
    $results += [pscustomobject]@{
      action = "resource-process-validate-after-cleanup"
      exit_code = $validateResult.exit_code
      output = $validateResult.stdout
    }
  }
}

$ownersAfter = @(Get-AppServerOwner)
$payload = [pscustomobject]@{
  schema = "bridge_appserver_idle_restart.v2"
  execution_affinity = "windows_host"
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
    writes_business_state = $false
    write_scope = "stdout_receipt_only"
    sends_messages = $false
    changes_bridge_state = $false
  }
}

$payload | ConvertTo-Json -Depth 8
