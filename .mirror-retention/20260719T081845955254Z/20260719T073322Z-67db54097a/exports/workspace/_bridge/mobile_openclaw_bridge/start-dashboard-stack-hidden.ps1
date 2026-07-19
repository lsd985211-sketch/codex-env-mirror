param(
  [string]$HostName = "127.0.0.1",
  [int]$Port = 18808,
  [int]$AppServerPort = 18791,
  [int]$LoginPort = 18790
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$OpenDashboard = Join-Path $Root "open-dashboard.ps1"
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path -LiteralPath $OpenDashboard)) {
  throw "Missing dashboard script: $OpenDashboard"
}

function Test-HttpOk {
  param([string]$Url)
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
    return ($response.StatusCode -eq 200)
  } catch {
    return $false
  }
}

function Get-CodexPackageVersion {
  param([string]$Path)
  if (-not $Path) { return $null }
  if ($Path -match "OpenAI\.Codex_([0-9]+(?:\.[0-9]+){1,3})_") {
    try { return [version]$Matches[1] } catch { return $null }
  }
  return $null
}

function Get-LatestCodexResourceExe {
  $windowsApps = Join-Path $env:ProgramFiles "WindowsApps"
  if (-not (Test-Path -LiteralPath $windowsApps)) { return "" }
  $matches = @(Get-ChildItem -LiteralPath $windowsApps -Directory -Filter "OpenAI.Codex_*" -ErrorAction SilentlyContinue | ForEach-Object {
    $exe = Join-Path $_.FullName "app\resources\codex.exe"
    if (Test-Path -LiteralPath $exe) {
      [pscustomobject]@{
        Path = $exe
        Version = Get-CodexPackageVersion $exe
        LastWriteTime = $_.LastWriteTimeUtc
      }
    }
  })
  $best = $matches | Sort-Object @{ Expression = { if ($_.Version) { $_.Version } else { [version]"0.0" } }; Descending = $true }, @{ Expression = "LastWriteTime"; Descending = $true } | Select-Object -First 1
  if ($best) { return [string]$best.Path }
  return ""
}

function Test-AppServerOwner {
  $connections = @()
  try {
    $connections = @(Get-NetTCPConnection -LocalAddress $HostName -LocalPort $AppServerPort -State Listen -ErrorAction Stop)
  } catch {
    $connections = @()
  }
  if ($connections.Count -ne 1) { return $false }
  $latestVersion = Get-CodexPackageVersion (Get-LatestCodexResourceExe)
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($connections[0].OwningProcess)" -ErrorAction SilentlyContinue
  if (-not $proc) { return $false }
  $path = [string]$proc.ExecutablePath
  $commandLine = [string]$proc.CommandLine
  $commandExe = (($commandLine -split "\s+")[0] -replace '^"', '' -replace '"$', '')
  if ((Split-Path -Leaf $path) -ine "codex.exe" -and (Split-Path -Leaf $commandExe) -ine "codex.exe") { return $false }
  if ($commandLine -notlike "*app-server*" -or $commandLine -notlike "*ws://${HostName}:${AppServerPort}*") { return $false }
  $ownerVersion = Get-CodexPackageVersion $path
  if ($latestVersion -and $ownerVersion -and $ownerVersion -lt $latestVersion) { return $false }
  return $true
}

$dashboardUrl = "http://${HostName}:${Port}/api/state"
$loginUrl = "http://${HostName}:${LoginPort}/api/state"
$appListening = Test-AppServerOwner

if ((Test-HttpOk $dashboardUrl) -and (Test-HttpOk $loginUrl) -and $appListening) {
  [pscustomobject]@{
    ok = $true
    already_running = $true
    dashboard_url = "http://${HostName}:${Port}/"
    app_server_port = $AppServerPort
    login_port = $LoginPort
  } | ConvertTo-Json -Depth 4
  exit 0
}

$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdout = Join-Path $LogDir "dashboard-stack-$stamp.stdout.log"
$stderr = Join-Path $LogDir "dashboard-stack-$stamp.stderr.log"
$args = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", $OpenDashboard,
  "-HostName", $HostName,
  "-Port", [string]$Port,
  "-AppServerPort", [string]$AppServerPort,
  "-LoginPort", [string]$LoginPort,
  "-NoOpen"
)

$process = Start-Process -FilePath $PowerShell `
  -ArgumentList $args `
  -WorkingDirectory $Root `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -PassThru

[pscustomobject]@{
  ok = $true
  already_running = $false
  pid = $process.Id
  dashboard_url = "http://${HostName}:${Port}/"
  app_server_port = $AppServerPort
  login_port = $LoginPort
  stdout = $stdout
  stderr = $stderr
} | ConvertTo-Json -Depth 4
