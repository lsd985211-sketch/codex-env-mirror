param(
  [int]$IntervalSeconds = 300,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $Root "codex_scheduler_runner.py"
if (-not (Test-Path -LiteralPath $Script)) {
  throw "Missing scheduler runner: $Script"
}

$BundledPythonw = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if ($env:CODEX_SCHEDULER_PYTHON -and (Test-Path -LiteralPath $env:CODEX_SCHEDULER_PYTHON)) {
  $Python = $env:CODEX_SCHEDULER_PYTHON
} elseif (Test-Path -LiteralPath $BundledPythonw) {
  $Python = $BundledPythonw
} elseif (Test-Path -LiteralPath $BundledPython) {
  $Python = $BundledPython
} elseif (Get-Command pythonw.exe -ErrorAction SilentlyContinue) {
  $Python = (Get-Command pythonw.exe -ErrorAction Stop).Source
} else {
  $Python = (Get-Command python -ErrorAction Stop).Source
}

$Existing = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -in @("python.exe", "pythonw.exe") -and
    $_.CommandLine -match [regex]::Escape($Script) -and
    $_.CommandLine -match " loop"
  }
if ($Existing) {
  Write-Output "codex scheduler already running"
  return
}

$argsList = @(
  $Script,
  "loop",
  "--interval-seconds", "$IntervalSeconds"
)
if ($DryRun) {
  $argsList += "--dry-run"
}

Start-Process -FilePath $Python -ArgumentList $argsList -WindowStyle Hidden
Write-Output "codex scheduler started"
