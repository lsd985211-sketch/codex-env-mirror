param(
    [int]$Port = 0,
    [int]$DelaySeconds = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$logDir = Join-Path $env:USERPROFILE ".codex\logs"
$logPath = Join-Path $logDir "codex-cdp-controlled-restart.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$packageHelperPath = Join-Path $env:USERPROFILE ".codex\scripts\codex-desktop-package.ps1"
if (-not (Test-Path -LiteralPath $packageHelperPath)) {
    throw "Codex Desktop package helper was not found: $packageHelperPath"
}
. $packageHelperPath

$cdpPortHelperPath = Join-Path $env:USERPROFILE ".codex\scripts\codex-cdp-port.ps1"
if (Test-Path -LiteralPath $cdpPortHelperPath) {
    . $cdpPortHelperPath
} else {
    function Resolve-CodexCdpPort {
        param(
            [object]$RequestedPort = $null,
            [switch]$Persist,
            [switch]$SetProcessEnv
        )
        $statePath = Join-Path $env:USERPROFILE ".codex\state\codex-cdp-port.txt"
        $resolved = $null
        if ($null -ne $RequestedPort -and [string]$RequestedPort -ne "0") {
            try { $resolved = [int]$RequestedPort } catch { $resolved = $null }
        }
        if ($null -eq $resolved -and -not [string]::IsNullOrWhiteSpace($env:CODEX_CDP_PORT)) {
            try { $resolved = [int]$env:CODEX_CDP_PORT } catch { $resolved = $null }
        }
        if ($null -eq $resolved -or $resolved -lt 1 -or $resolved -gt 65535) {
            $resolved = 9229
        }
        if ($SetProcessEnv) {
            $env:CODEX_CDP_PORT = [string]$resolved
        }
        [pscustomobject]@{ Port = [int]$resolved; Source = "fallback"; StatePath = [string]$statePath; DefaultPort = 9229 }
    }
}

function Write-RestartLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    [System.IO.File]::AppendAllText($logPath, "[$timestamp] $Message`r`n", [System.Text.Encoding]::UTF8)
}

function Get-CodexDesktopGuiProcesses {
    @(Get-CodexDesktopHostProcesses -MainOnly)
}

function Get-BridgeAppServerProcesses {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "codex.exe" -and
        $_.CommandLine -like "*app-server*" -and
        $_.CommandLine -like "*127.0.0.1:18791*"
    })
}

function Test-CdpVersion {
    param([int]$TargetPort)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$TargetPort/json/version" -TimeoutSec 1
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    } catch {
        return $false
    }
}

$requestedPort = if ($Port -gt 0) { $Port } else { $null }
$resolvedPort = Resolve-CodexCdpPort -RequestedPort $requestedPort -Persist -SetProcessEnv
$Port = [int]$resolvedPort.Port
Write-RestartLog "Controlled Codex Desktop CDP restart scheduled. Port=$Port; PortSource=$($resolvedPort.Source); StatePath=$($resolvedPort.StatePath); DelaySeconds=$DelaySeconds"
if ($DelaySeconds -gt 0) {
    Start-Sleep -Seconds $DelaySeconds
}

$startScript = Join-Path $env:USERPROFILE ".codex\scripts\start-codex-desktop-elevated.ps1"
if (-not (Test-Path -LiteralPath $startScript)) {
    Write-RestartLog "Start script missing: $startScript"
    exit 1
}

$bridgeServers = Get-BridgeAppServerProcesses
Write-RestartLog "Bridge app-server processes preserved: $($bridgeServers.ProcessId -join ',')"

$guiProcesses = Get-CodexDesktopGuiProcesses
Write-RestartLog "Codex Desktop GUI processes before stop: $($guiProcesses.ProcessId -join ',')"
foreach ($row in $guiProcesses) {
    try {
        $p = Get-Process -Id $row.ProcessId -ErrorAction Stop
        if ($p.MainWindowHandle -ne 0) {
            [void]$p.CloseMainWindow()
            Write-RestartLog "Requested graceful close for Codex GUI pid=$($row.ProcessId)"
        }
    } catch {
        Write-RestartLog "Graceful close skipped for pid=$($row.ProcessId): $($_.Exception.Message)"
    }
}

Start-Sleep -Seconds 4

foreach ($row in $guiProcesses) {
    try {
        $p = Get-Process -Id $row.ProcessId -ErrorAction Stop
        Stop-Process -Id $row.ProcessId -Force -ErrorAction Stop
        Write-RestartLog "Force-stopped remaining Codex GUI pid=$($row.ProcessId)"
    } catch {
        Write-RestartLog "Codex GUI pid=$($row.ProcessId) already stopped or inaccessible: $($_.Exception.Message)"
    }
}

Start-Sleep -Seconds 2
$remainingGui = Get-CodexDesktopGuiProcesses
Write-RestartLog "Codex Desktop GUI processes after stop: $($remainingGui.ProcessId -join ',')"

Write-RestartLog "Starting Codex Desktop through elevated script. CODEX_CDP_PORT=$env:CODEX_CDP_PORT"
& $startScript

$deadline = (Get-Date).AddSeconds(45)
$ready = $false
while ((Get-Date) -lt $deadline) {
    if (Test-CdpVersion -TargetPort $Port) {
        $ready = $true
        break
    }
    Start-Sleep -Milliseconds 500
}

$bridgeServersAfter = Get-BridgeAppServerProcesses
Write-RestartLog "Bridge app-server processes after restart: $($bridgeServersAfter.ProcessId -join ',')"
Write-RestartLog "CDP readiness after restart: Port=$Port; Ready=$ready"
if (-not $ready) {
    exit 2
}
exit 0
