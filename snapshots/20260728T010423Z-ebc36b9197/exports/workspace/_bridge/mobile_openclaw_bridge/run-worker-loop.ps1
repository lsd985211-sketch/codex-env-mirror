param(
  [int]$IntervalSeconds = 1,
  [int]$Limit = 5,
  [int]$RestartDelaySeconds = 5,
  [ValidateSet("summary", "full", "quiet")]
  [string]$LogMode = "summary",
  [int]$KeepRecentWorkerLogs = 12,
  [long]$MaxWorkerStdoutBytes = 20971520,
  [long]$MaxArchiveBytes = 67108864
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Cli = Join-Path $Root "mobile_openclaw_cli.py"
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$StdoutLog = Join-Path $LogDir "worker-loop-$stamp.stdout.log"
$StderrLog = Join-Path $LogDir "worker-loop-$stamp.stderr.log"
$LifecycleLog = Join-Path $LogDir "worker-loop-$stamp.lifecycle.log"

function Write-Life([string]$Message) {
  Add-Content -LiteralPath $LifecycleLog -Encoding UTF8 -Value ((Get-Date -Format o) + " " + $Message)
}

function Invoke-WorkerLogRetention {
  param(
    [string]$Directory,
    [int]$KeepRecent,
    [long]$MaxStdoutBytes,
    [long]$MaxArchiveTotalBytes
  )
  $archive = Join-Path $Directory "archive"
  New-Item -ItemType Directory -Force -Path $archive | Out-Null
  $logs = @(Get-ChildItem -LiteralPath $Directory -File -Filter "worker-loop-*.*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
  $index = 0
  foreach ($log in $logs) {
    $index += 1
    if ($index -le $KeepRecent -and $log.Length -le $MaxStdoutBytes) {
      continue
    }
    $target = Join-Path $archive $log.Name
    Move-Item -LiteralPath $log.FullName -Destination $target -Force
  }
  $archived = @(Get-ChildItem -LiteralPath $archive -File -Filter "worker-loop-*.*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
  $archiveIndex = 0
  $archiveBytes = 0L
  foreach ($log in $archived) {
    $archiveIndex += 1
    $archiveBytes += [long]$log.Length
    $isOversizedStdout = $log.Name -like "*.stdout.log" -and $log.Length -gt $MaxStdoutBytes
    $isPastCountLimit = $archiveIndex -gt ([Math]::Max($KeepRecent * 4, 24))
    $isPastByteLimit = $archiveBytes -gt $MaxArchiveTotalBytes
    if ($isOversizedStdout -or $isPastCountLimit -or $isPastByteLimit) {
      Remove-Item -LiteralPath $log.FullName -Force
    }
  }
}

try {
  $BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  if ($env:MOBILE_OPENCLAW_PYTHON -and (Test-Path -LiteralPath $env:MOBILE_OPENCLAW_PYTHON)) {
    $Python = $env:MOBILE_OPENCLAW_PYTHON
  } elseif (Test-Path -LiteralPath $BundledPython) {
    $Python = $BundledPython
  } else {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $Python = $pythonCommand.Source
  }
  Invoke-WorkerLogRetention -Directory $LogDir -KeepRecent $KeepRecentWorkerLogs -MaxStdoutBytes $MaxWorkerStdoutBytes -MaxArchiveTotalBytes $MaxArchiveBytes
  Write-Life "starting worker supervisor interval=$IntervalSeconds limit=$Limit restartDelay=$RestartDelaySeconds logMode=$LogMode python=$Python"
  Write-Life "stdout=$StdoutLog"
  Write-Life "stderr=$StderrLog"
  $run = 0
  while ($true) {
    $run += 1
    Write-Life "worker-loop run=$run starting"
    & $Python $Cli worker-loop --interval $IntervalSeconds --limit $Limit --log-mode $LogMode 1>> $StdoutLog 2>> $StderrLog
    $exitCode = $LASTEXITCODE
    Write-Life "worker-loop run=$run exited code=$exitCode; restarting after ${RestartDelaySeconds}s"
    Start-Sleep -Seconds ([Math]::Max(1, $RestartDelaySeconds))
  }
} catch {
  Write-Life ("worker supervisor failed: " + $_.Exception.Message)
  Add-Content -LiteralPath $StderrLog -Encoding UTF8 -Value $_.ScriptStackTrace
  exit 1
}
