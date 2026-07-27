param(
  [int]$Port = 18789
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunScript = Join-Path $Root "run-openclaw-gateway-loop.ps1"
$OpenClawRuntimeRoot = if ($env:CODEX_OPENCLAW_RUNTIME_ROOT) { $env:CODEX_OPENCLAW_RUNTIME_ROOT } else { Join-Path $env:LOCALAPPDATA "Codex\openclaw" }
$OpenClawBase = Join-Path $OpenClawRuntimeRoot "clean-install"
$LogDir = Join-Path $OpenClawBase "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path -LiteralPath $RunScript)) {
  throw "Missing Gateway run script: $RunScript"
}

$RootPattern = [regex]::Escape($Root)
$ExistingSupervisor = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -in @("powershell.exe", "pwsh.exe") -and
  $_.CommandLine -match $RootPattern -and
  $_.CommandLine -match "run-openclaw-gateway-loop\.ps1"
}
if ($ExistingSupervisor) {
  Write-Output (@{
    ok = $true
    already_running = $true
    port = $Port
    running = @($ExistingSupervisor | Select-Object ProcessId, Name, CommandLine)
  } | ConvertTo-Json -Depth 5)
  exit 0
}

$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdout = Join-Path $LogDir "openclaw-gateway-launch-$stamp.stdout.log"
$stderr = Join-Path $LogDir "openclaw-gateway-launch-$stamp.stderr.log"
$args = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", $RunScript,
  "-Port", [string]$Port
)

Start-Process -FilePath $PowerShell `
  -ArgumentList $args `
  -WorkingDirectory $Root `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr

Start-Sleep -Seconds 2
$Supervisor = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -in @("powershell.exe", "pwsh.exe") -and
  $_.CommandLine -match $RootPattern -and
  $_.CommandLine -match "run-openclaw-gateway-loop\.ps1"
}
Write-Output (@{
  ok = [bool]$Supervisor
  already_running = $false
  port = $Port
  run_script = $RunScript
  stdout = $stdout
  stderr = $stderr
  running = [bool]$Supervisor
} | ConvertTo-Json -Depth 4)
