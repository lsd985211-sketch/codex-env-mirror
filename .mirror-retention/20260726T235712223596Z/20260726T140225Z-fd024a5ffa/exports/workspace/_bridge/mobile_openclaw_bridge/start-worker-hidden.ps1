param(
  [int]$IntervalSeconds = 1,
  [int]$Limit = 5,
  [ValidateSet("summary", "full", "quiet")]
  [string]$LogMode = "summary"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunScript = Join-Path $Root "run-worker-loop.ps1"
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-WorkerProcessRows {
  try {
    return @(Get-CimInstance Win32_Process)
  } catch {
    $cimError = $_.Exception.Message
    try {
      $rows = @(Get-WmiObject Win32_Process)
      foreach ($row in $rows) {
        $row | Add-Member -NotePropertyName "ProcessProbeFallback" -NotePropertyValue "wmi" -Force
        $row | Add-Member -NotePropertyName "ProcessProbeCimError" -NotePropertyValue $cimError -Force
      }
      return $rows
    } catch {
      throw "Failed to enumerate processes through CIM or WMI. CIM: $cimError; WMI: $($_.Exception.Message)"
    }
  }
}

$RootPattern = [regex]::Escape($Root)
$Existing = Get-WorkerProcessRows | Where-Object {
  $_.Name -in @("powershell.exe", "pwsh.exe", "python.exe", "pythonw.exe") -and
  $_.CommandLine -match $RootPattern -and
  (
    $_.CommandLine -match "mobile_openclaw_cli\.py worker-loop" -or
    ($_.CommandLine -match "\s-File\s+.*run-worker-loop\.ps1" -and $_.CommandLine -notmatch "start-worker-hidden\.ps1")
  )
}
if ($Existing) {
  Write-Output (@{
    ok = $true
    already_running = $true
    interval_seconds = $IntervalSeconds
    limit = $Limit
    running = @($Existing | Select-Object ProcessId, Name, CommandLine)
  } | ConvertTo-Json -Depth 5)
  exit 0
}

$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$WorkerArgs = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", $RunScript,
  "-IntervalSeconds", [string]$IntervalSeconds,
  "-Limit", [string]$Limit,
  "-LogMode", $LogMode
)

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdout = Join-Path $LogDir "worker-launch-$stamp.stdout.log"
$stderr = Join-Path $LogDir "worker-launch-$stamp.stderr.log"

Start-Process -FilePath $PowerShell `
  -ArgumentList $WorkerArgs `
  -WorkingDirectory $Root `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr

Write-Output (@{
  ok = $true
  already_running = $false
  interval_seconds = $IntervalSeconds
  limit = $Limit
  log_mode = $LogMode
  run_script = $RunScript
  stdout = $stdout
  stderr = $stderr
} | ConvertTo-Json -Depth 4)
