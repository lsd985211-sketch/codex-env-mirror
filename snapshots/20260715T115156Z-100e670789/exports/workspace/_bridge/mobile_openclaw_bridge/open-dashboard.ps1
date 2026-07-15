param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 18808,
    [int]$AppServerPort = 18791,
    [int]$LoginPort = 18790,
    [int]$LiveWatcherSyncMs = 10000,
    [switch]$NoOpen,
    [switch]$StartAppServer
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $Root)
$Url = "http://${HostName}:${Port}/"
$LoginUrl = "http://${HostName}:${LoginPort}/"
$LiveState = Join-Path $Root "runtime\dashboard_live_state.json"
$DashboardActivity = Join-Path $Root "runtime\dashboard_activity.json"
$LaunchLog = Join-Path $Root "runtime\dashboard_open_last.log"
$DashboardRuns = Join-Path $Root "runtime\dashboard-runs"
$OpenClawBase = Join-Path $ProjectRoot "_tools\openclaw-codex\clean-install"
$OpenClawHome = Join-Path $OpenClawBase "home"
$OpenClawStateDir = Join-Path $OpenClawBase "state"
$LoginArtifacts = Join-Path $OpenClawBase "login-artifacts"
$LoginServerScript = Join-Path $LoginArtifacts "weixin-login-slot-server.mjs"
$LoginRuns = Join-Path $OpenClawBase "login-runs"
$PythonCandidates = @(
    "C:\Users\45543\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe",
    "C:\Python314\python.exe",
    "python"
)
$NodeCandidates = @(
    "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_tools\openclaw-codex\node24\node-v24.17.0-win-x64\node.exe",
    "C:\Program Files\nodejs\node.exe",
    "node"
)
$BrowserCandidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
) | Select-Object -Unique
$CodexPackagePrefix = "OpenAI.Codex_"

function Write-LaunchLog {
    param([string]$Message)
    try {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LaunchLog) | Out-Null
        $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"), $Message
        Add-Content -LiteralPath $LaunchLog -Value $line -Encoding UTF8
    } catch {
        # Opening the dashboard should not fail just because logging failed.
    }
}

trap {
    Write-LaunchLog ("ERROR: " + $_.Exception.Message)
    throw
}

function Get-CodexPackageVersion {
    param([string]$Path)
    if (-not $Path) { return $null }
    if ($Path -match "OpenAI\.Codex_([0-9]+(?:\.[0-9]+){1,3})_") {
        try { return [version]$Matches[1] } catch { return $null }
    }
    return $null
}

function Resolve-ExecutableCandidate {
    param(
        [string[]]$Candidates,
        [string[]]$CommandNames = @()
    )

    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if ($CommandNames -contains $candidate) {
            return $candidate
        }
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Get-SafeLeafName {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ""
    }
    try {
        return [System.IO.Path]::GetFileName($Path.Trim('"'))
    } catch {
        return ""
    }
}

function Get-LatestCodexResourceExe {
    $windowsApps = Join-Path $env:ProgramFiles "WindowsApps"
    $matches = @()
    if (Test-Path -LiteralPath $windowsApps) {
        $matches = @(Get-ChildItem -LiteralPath $windowsApps -Directory -Filter "${CodexPackagePrefix}*" -ErrorAction SilentlyContinue | ForEach-Object {
            $exe = Join-Path $_.FullName "app\resources\codex.exe"
            if (Test-Path -LiteralPath $exe) {
                [pscustomobject]@{
                    Path = $exe
                    Version = Get-CodexPackageVersion $exe
                    LastWriteTime = $_.LastWriteTimeUtc
                }
            }
        })
    }
    $best = $matches | Sort-Object @{ Expression = { if ($_.Version) { $_.Version } else { [version]"0.0" } }; Descending = $true }, @{ Expression = "LastWriteTime"; Descending = $true } | Select-Object -First 1
    if ($best) { return [string]$best.Path }
    return "codex"
}

$CodexCandidates = @(
    (Get-LatestCodexResourceExe),
    "codex"
)

function Test-Dashboard {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "${Url}api/state" -TimeoutSec 2
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Start-DashboardServer {
    if (Test-Dashboard) {
        return $true
    }

    $python = Resolve-ExecutableCandidate -Candidates $PythonCandidates -CommandNames @("python")

    if (-not $python) {
        Write-LaunchLog "start_dashboard failed: No Python executable found for mobile dashboard."
        return $false
    }

    New-Item -ItemType Directory -Force -Path $DashboardRuns | Out-Null
    $runId = (Get-Date -Format "yyyyMMdd-HHmmss") + "-mobile-dashboard"
    $stdout = Join-Path $DashboardRuns "$runId.stdout.log"
    $stderr = Join-Path $DashboardRuns "$runId.stderr.log"

    Write-LaunchLog "starting dashboard server: $python mobile_dashboard.py --host $HostName --port $Port"
    Start-Process `
        -FilePath $python `
        -ArgumentList @("mobile_dashboard.py", "--host", $HostName, "--port", [string]$Port, "--live-state", $LiveState, "--dashboard-activity", $DashboardActivity, "--login-host", $HostName, "--login-port", [string]$LoginPort) `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden | Out-Null

    $deadline = (Get-Date).AddSeconds(8)
    while ((Get-Date) -lt $deadline) {
        if (Test-Dashboard) { return $true }
        Start-Sleep -Milliseconds 400
    }

    $stderrPreview = ""
    try {
        if (Test-Path -LiteralPath $stderr) {
            $stderrPreview = ((Get-Content -LiteralPath $stderr -Tail 8 -Encoding UTF8) -join " ")
        }
    } catch {
        $stderrPreview = $_.Exception.Message
    }
    Write-LaunchLog "start_dashboard failed: dashboard did not listen on http://${HostName}:${Port}/ in time. stderr=$stderrPreview"
    return $false
}

function Open-DashboardUrl {
    param([string]$TargetUrl)
    $browser = Resolve-ExecutableCandidate -Candidates $BrowserCandidates

    if ($browser) {
        Write-LaunchLog "opening dashboard with Chrome: $browser $TargetUrl"
        Start-Process `
            -FilePath $browser `
            -ArgumentList @("--new-window", $TargetUrl) `
            -WindowStyle Normal
        return
    }

    Write-LaunchLog "Chrome not found; opening dashboard through default URL handler: $TargetUrl"
    Start-Process $TargetUrl
}

function Test-LiveWatcher {
    $procs = @(Get-LiveWatcherProcesses)
    return ($procs.Count -eq 1)
}

function Get-LiveWatcherProcesses {
    $needle = "codex_app_live_watch.js"
    $outputNeedle = [System.IO.Path]::GetFullPath($LiveState)
    $portNeedle = "--port $AppServerPort"
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match "^(node|node.exe)$" `
            -and $_.CommandLine -like "*$needle*" `
            -and $_.CommandLine -like "*$outputNeedle*" `
            -and ($_.CommandLine -like "*$portNeedle*" -or $AppServerPort -eq 18791)
    })
}

function Repair-LiveWatcherProcesses {
    $procs = @(Get-LiveWatcherProcesses)
    if ($procs.Count -le 1) {
        return
    }
    foreach ($proc in $procs) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 400
}

function Get-AppServerListeners {
    try {
        return @(Get-NetTCPConnection -LocalAddress $HostName -LocalPort $AppServerPort -State Listen -ErrorAction Stop)
    } catch {
        return @()
    }
}

function Get-AppServerOwnerReport {
    $connections = @(Get-AppServerListeners)
    $owners = @()
    $latestExe = Get-LatestCodexResourceExe
    $latestVersion = Get-CodexPackageVersion $latestExe
    foreach ($connection in $connections) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
        $ownerVersion = if ($proc) { Get-CodexPackageVersion $proc.ExecutablePath } else { $null }
        $commandLine = if ($proc) { [string]$proc.CommandLine } else { "" }
        $path = if ($proc) { [string]$proc.ExecutablePath } else { "" }
        $commandExe = (($commandLine -split "\s+")[0] -replace '^"', '' -replace '"$', '')
        $isCodexExecutable = (
            (Get-SafeLeafName $path) -ieq "codex.exe" -or
            (Get-SafeLeafName $commandExe) -ieq "codex.exe"
        )
        $isCodexAppServer = (
            $isCodexExecutable -and
            $commandLine -like "*app-server*" -and
            $commandLine -like "*ws://${HostName}:${AppServerPort}*"
        )
        $versionKnown = $null -ne $ownerVersion
        $versionOk = $true
        if ($latestVersion) {
            $versionOk = ($null -ne $ownerVersion -and $ownerVersion -ge $latestVersion)
        }
        $owners += [pscustomobject]@{
            pid = [int]$connection.OwningProcess
            path = $path
            command_line = $commandLine
            version = if ($ownerVersion) { $ownerVersion.ToString() } else { "" }
            version_known = [bool]$versionKnown
            is_codex_app_server = [bool]$isCodexAppServer
            version_ok = [bool]$versionOk
            healthy = [bool]($isCodexAppServer -and $versionOk)
        }
    }
    [pscustomobject]@{
        listening = ($owners.Count -gt 0)
        healthy = ($owners.Count -eq 1 -and [bool]$owners[0].healthy)
        latest_exe = $latestExe
        latest_version = if ($latestVersion) { $latestVersion.ToString() } else { "" }
        owners = $owners
    }
}

function Test-AppServer {
    return [bool](Get-AppServerOwnerReport).healthy
}

function Stop-UnhealthyAppServerListeners {
    $report = Get-AppServerOwnerReport
    if (-not $report.listening -or $report.healthy) { return }
    foreach ($owner in $report.owners) {
        if ($owner.command_line -like "*app-server*" -and $owner.command_line -like "*ws://${HostName}:${AppServerPort}*") {
            Stop-Process -Id $owner.pid -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 500
}

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action,
        [switch]$Required
    )

    try {
        & $Action
        return $true
    } catch {
        Write-LaunchLog ("{0} failed: {1}" -f $Name, $_.Exception.Message)
        if ($Required) {
            throw
        }
        return $false
    }
}

function Test-LoginServer {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "${LoginUrl}api/state" -TimeoutSec 2
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Start-LoginServer {
    if (Test-LoginServer) {
        return
    }

    $node = Resolve-ExecutableCandidate -Candidates $NodeCandidates -CommandNames @("node")

    if (-not $node) {
        return
    }
    if (-not (Test-Path -LiteralPath $LoginServerScript)) {
        return
    }

    New-Item -ItemType Directory -Force -Path $LoginRuns | Out-Null
    $runId = (Get-Date -Format "yyyyMMdd-HHmmss") + "-dashboard-login"
    $stdout = Join-Path $LoginRuns "$runId-weixin-login-slot-server.stdout.log"
    $stderr = Join-Path $LoginRuns "$runId-weixin-login-slot-server.stderr.log"
    $previousOpenClawHome = $env:OPENCLAW_HOME
    $previousOpenClawStateDir = $env:OPENCLAW_STATE_DIR

    try {
        $env:OPENCLAW_HOME = $OpenClawHome
        $env:OPENCLAW_STATE_DIR = $OpenClawStateDir
        $loginCommand = 'start "" /b "{0}" "{1}" --port {2} --timeout-ms 480000 > "{3}" 2> "{4}"' -f $node, $LoginServerScript, $LoginPort, $stdout, $stderr
        Start-Process `
            -FilePath "cmd.exe" `
            -ArgumentList @("/d", "/c", $loginCommand) `
            -WorkingDirectory $OpenClawBase `
            -WindowStyle Hidden | Out-Null
    } finally {
        $env:OPENCLAW_HOME = $previousOpenClawHome
        $env:OPENCLAW_STATE_DIR = $previousOpenClawStateDir
    }

    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (Test-LoginServer) { return }
        Start-Sleep -Milliseconds 300
    }
}

function Start-AppServer {
    if (Test-AppServer) {
        return
    }
    Stop-UnhealthyAppServerListeners
    if (Test-AppServer) {
        return
    }

    $codex = Resolve-ExecutableCandidate -Candidates $CodexCandidates -CommandNames @("codex")

    if (-not $codex) {
        throw "No Codex executable found for app-server."
    }

    Start-Process `
        -FilePath $codex `
        -ArgumentList @("app-server", "--listen", "ws://${HostName}:${AppServerPort}") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden

    $deadline = (Get-Date).AddSeconds(8)
    while ((Get-Date) -lt $deadline) {
        if (Test-AppServer) { return }
        Start-Sleep -Milliseconds 250
    }

    throw "Codex app-server did not listen on ws://${HostName}:${AppServerPort} in time."
}

$dashboardReady = Start-DashboardServer
$openedEarly = $false
if (-not $NoOpen -and $dashboardReady) {
    Invoke-Step -Name "open_dashboard_url" -Action { Open-DashboardUrl -TargetUrl $Url } | Out-Null
    $openedEarly = $true
} elseif (-not $dashboardReady) {
    Write-LaunchLog "dashboard not opened because health check failed: $Url"
}

Invoke-Step -Name "start_login_server" -Action { Start-LoginServer } | Out-Null

if ($StartAppServer) {
    Invoke-Step -Name "start_app_server" -Action { Start-AppServer } | Out-Null
    Repair-LiveWatcherProcesses

    if (-not (Test-LiveWatcher)) {
        $node = Resolve-ExecutableCandidate -Candidates $NodeCandidates -CommandNames @("node")

        if ($node) {
            Start-Process `
                -FilePath $node `
                -ArgumentList @("codex_app_live_watch.js", "--host", $HostName, "--port", [string]$AppServerPort, "--output", $LiveState, "--active-file", $DashboardActivity, "--active-window-ms", "90000", "--inactive-heartbeat-write-ms", "300000", "--sync-ms", [string]$LiveWatcherSyncMs, "--heartbeat-write-ms", "30000", "--idle-sync-ms", "30000", "--idle-after-ms", "120000", "--turn-limit", "6") `
                -WorkingDirectory $Root `
                -WindowStyle Hidden
        }
    }
} else {
    Write-LaunchLog "skipped codex app-server startup; pass -StartAppServer to enable live Codex watcher"
}

if (-not $NoOpen -and -not $openedEarly -and (Test-Dashboard)) {
    Open-DashboardUrl -TargetUrl $Url
}

$dashboardFinal = Test-Dashboard
[pscustomobject]@{
    ok = $dashboardFinal
    url = $Url
    dashboard = $dashboardFinal
    app_server = Test-AppServer
    app_server_owner = Get-AppServerOwnerReport
    login_server = Test-LoginServer
    live_watcher = Test-LiveWatcher
    opened = (-not [bool]$NoOpen -and $dashboardFinal)
} | ConvertTo-Json -Depth 4
