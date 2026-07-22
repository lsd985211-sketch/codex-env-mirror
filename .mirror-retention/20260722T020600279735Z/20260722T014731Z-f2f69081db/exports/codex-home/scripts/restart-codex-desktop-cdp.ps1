param(
    [int]$Port = 0,
    [int]$DelaySeconds = 0,
    [int]$WaitSeconds = 15
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$logDir = Join-Path $env:USERPROFILE ".codex\logs"
$logPath = Join-Path $logDir "codex-cdp-controlled-restart.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-RefreshLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    [System.IO.File]::AppendAllText($logPath, "[$timestamp] $Message`r`n", [System.Text.Encoding]::UTF8)
}

function Resolve-CodexRefreshPython {
    $candidates = @(
        "C:\Python314\python.exe",
        (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"),
        "python"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -eq "python" -or (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    return $null
}

function Get-CodexMainProcessIds {
    @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $_.Name -eq "ChatGPT.exe" -and
                $_.CommandLine -notlike "*--type=*" -and
                $_.ExecutablePath -like "*\OpenAI.Codex_*\app\ChatGPT.exe"
            } |
            Select-Object -ExpandProperty ProcessId |
            Sort-Object -Unique
    )
}

function Test-CodexCdpVersion {
    param([int]$TargetPort)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$TargetPort/json/version" -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    } catch {
        return $false
    }
}

$portHelper = Join-Path $env:USERPROFILE ".codex\scripts\codex-cdp-port.ps1"
if (-not (Test-Path -LiteralPath $portHelper)) {
    Write-RefreshLog "Codex Desktop refresh blocked: CDP port helper missing at $portHelper"
    exit 1
}
. $portHelper

$requestedPort = if ($Port -gt 0) { $Port } else { $null }
$resolvedPort = Resolve-CodexCdpPort -RequestedPort $requestedPort -Persist -SetProcessEnv
$Port = [int]$resolvedPort.Port
if ($DelaySeconds -gt 0) {
    Start-Sleep -Seconds $DelaySeconds
}
if ($WaitSeconds -lt 1) {
    $WaitSeconds = 1
}

$beforePids = @(Get-CodexMainProcessIds)
if ($beforePids.Count -eq 0 -or -not (Test-CodexCdpVersion -TargetPort $Port)) {
    Write-RefreshLog "Codex Desktop refresh blocked: a running CDP-backed Desktop main process is required. Port=$Port; Pids=$($beforePids -join ',')"
    exit 2
}

$workspace = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
$runtimeScript = Join-Path $workspace "_bridge\codex_desktop_model_runtime.py"
$pythonExe = Resolve-CodexRefreshPython
if (-not $pythonExe -or -not (Test-Path -LiteralPath $runtimeScript)) {
    Write-RefreshLog "Codex Desktop refresh blocked: projected runtime owner or Python is missing. Owner=$runtimeScript; Python=$pythonExe"
    exit 3
}

$runId = [guid]::NewGuid().ToString("N")
$refreshOut = Join-Path $logDir "codex-desktop-refresh-$runId.out.log"
$refreshErr = Join-Path $logDir "codex-desktop-refresh-$runId.err.log"
try {
    $process = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @($runtimeScript, "page-reload", "--wait-seconds", "$WaitSeconds", "--json") `
        -WorkingDirectory $workspace `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput $refreshOut `
        -RedirectStandardError $refreshErr
    $null = $process.Handle
    if (-not $process.WaitForExit(($WaitSeconds + 5) * 1000)) {
        & taskkill.exe /PID $process.Id /T /F 2>&1 | Out-Null
        Write-RefreshLog "Codex Desktop refresh owner timed out. Pid=$($process.Id); WaitSeconds=$WaitSeconds"
        exit 4
    }
    $exitCode = $process.ExitCode
    $output = if (Test-Path -LiteralPath $refreshOut) { Get-Content -LiteralPath $refreshOut -Raw -Encoding UTF8 } else { "" }
    $stderr = if (Test-Path -LiteralPath $refreshErr) { Get-Content -LiteralPath $refreshErr -Raw -Encoding UTF8 } else { "" }
    if ([string]::IsNullOrWhiteSpace($output)) {
        Write-RefreshLog "Codex Desktop refresh owner returned empty output. ExitCode=$exitCode; Error=$($stderr.Trim())"
        exit 5
    }
    $result = $output | ConvertFrom-Json -ErrorAction Stop
    $accepted = $exitCode -eq 0 -and [bool]$result.ok -and [bool]$result.requested -and -not [bool]$result.skipped
    if (-not $accepted) {
        Write-RefreshLog "Codex Desktop refresh owner did not satisfy acceptance. ExitCode=$exitCode; Ok=$([bool]$result.ok); Requested=$([bool]$result.requested); Skipped=$([bool]$result.skipped); Reason=$([string]$result.reason)"
        exit 6
    }

    Start-Sleep -Milliseconds 750
    $afterPids = @(Get-CodexMainProcessIds)
    $sameProcess = ($beforePids -join ",") -eq ($afterPids -join ",")
    $cdpReady = Test-CodexCdpVersion -TargetPort $Port
    if (-not $sameProcess -or -not $cdpReady) {
        Write-RefreshLog "Codex Desktop refresh failed readback. SameProcess=$sameProcess; CdpReady=$cdpReady; BeforePids=$($beforePids -join ','); AfterPids=$($afterPids -join ','); Port=$Port"
        exit 7
    }

    Remove-Item -LiteralPath $refreshOut, $refreshErr -Force -ErrorAction SilentlyContinue
    Write-RefreshLog "Process-preserving Codex Desktop refresh completed. Port=$Port; MainPids=$($afterPids -join ','); SameProcess=True; CdpReady=True"
    exit 0
} catch {
    Write-RefreshLog "Codex Desktop refresh failed: $($_.Exception.Message)"
    exit 8
}
