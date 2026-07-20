Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

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
        if (-not [string]::IsNullOrWhiteSpace($env:CODEX_CDP_PORT)) {
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

$cdpPort = Resolve-CodexCdpPort -SetProcessEnv
$remoteDebuggingPort = [int]$cdpPort.Port

function Write-Check {
    param(
        [string]$Name,
        [bool]$Pass,
        [string]$Detail
    )
    $status = if ($Pass) { "PASS" } else { "FAIL" }
    [pscustomobject]@{
        Check = $Name
        Status = $status
        Detail = $Detail
    }
}

function Write-Info {
    param(
        [string]$Name,
        [string]$Detail
    )
    [pscustomobject]@{
        Check = $Name
        Status = "INFO"
        Detail = $Detail
    }
}

function Get-TokenElevation {
    param([int]$ProcessId)
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        $source = @"
using System;
using System.Runtime.InteropServices;
public static class CodexAdminModeTokenCheck {
  [DllImport("advapi32.dll", SetLastError=true)] public static extern bool OpenProcessToken(IntPtr ProcessHandle, UInt32 DesiredAccess, out IntPtr TokenHandle);
  [DllImport("advapi32.dll", SetLastError=true)] public static extern bool GetTokenInformation(IntPtr TokenHandle, int TokenInformationClass, IntPtr TokenInformation, int TokenInformationLength, out int ReturnLength);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool CloseHandle(IntPtr hObject);
  public const UInt32 TOKEN_QUERY = 0x0008;
  public static int GetElevation(IntPtr processHandle) {
    IntPtr token;
    if (!OpenProcessToken(processHandle, TOKEN_QUERY, out token)) return -1;
    try {
      IntPtr ptr = Marshal.AllocHGlobal(4);
      try {
        int returned;
        if (!GetTokenInformation(token, 20, ptr, 4, out returned)) return -2;
        return Marshal.ReadInt32(ptr);
      } finally { Marshal.FreeHGlobal(ptr); }
    } finally { CloseHandle(token); }
  }
}
"@
        if (-not ("CodexAdminModeTokenCheck" -as [type])) {
            Add-Type -TypeDefinition $source
        }
        $value = [CodexAdminModeTokenCheck]::GetElevation($process.Handle)
        if ($value -eq 1) { return "Yes" }
        if ($value -eq 0) { return "No" }
        return "Unknown($value)"
    } catch {
        return "Unknown($($_.Exception.Message))"
    }
}

function Get-CodexDesktopExe {
    Resolve-CurrentCodexDesktopExe
}

function Get-CodexLoginMode {
    $command = Get-Command codex -ErrorAction SilentlyContinue
    if ($null -eq $command -or [string]::IsNullOrWhiteSpace([string]$command.Source)) {
        return [pscustomobject]@{ Authenticated = $false; Mode = "cli_unavailable" }
    }

    $statusOutput = & $command.Source login status 2>&1
    $statusExit = $LASTEXITCODE
    $statusText = ($statusOutput | Out-String)
    if ($statusExit -ne 0) {
        return [pscustomobject]@{ Authenticated = $false; Mode = "not_authenticated" }
    }
    if ($statusText -match '(?i)API\s*key|API\s*密钥') {
        return [pscustomobject]@{ Authenticated = $true; Mode = "api_key" }
    }
    if ($statusText -match '(?i)ChatGPT|chatgpt\.com') {
        return [pscustomobject]@{ Authenticated = $true; Mode = "chatgpt" }
    }
    return [pscustomobject]@{ Authenticated = $true; Mode = "authenticated_unknown" }
}

function Get-CodexWhamAuthClassification {
    param(
        [int]$ProcessId,
        [string]$AuthMode
    )

    $logRoot = Join-Path $env:LOCALAPPDATA "Codex\Logs"
    $logFile = if (Test-Path -LiteralPath $logRoot) {
        Get-ChildItem -LiteralPath $logRoot -Recurse -File -Filter "*-$ProcessId-*.log" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
    } else {
        $null
    }
    if ($null -eq $logFile) {
        return [pscustomobject]@{ Count = 0; Classification = "current_process_log_not_found" }
    }

    $matches = @(Select-String -LiteralPath $logFile.FullName -Pattern @(
        'desktop_fetch_auth_401.*(?:/wham/tasks/list|/wham/usage)',
        'routePattern=/wham/(?:tasks/list|usage).*status=401'
    ) -ErrorAction SilentlyContinue)
    if ($matches.Count -eq 0) {
        return [pscustomobject]@{ Count = 0; Classification = "none_observed" }
    }

    $classification = switch ($AuthMode) {
        "api_key" { "nonfatal_chatgpt_account_endpoints_unavailable_in_api_key_mode" }
        "chatgpt" { "chatgpt_account_token_unavailable_check_login_state" }
        default { "authentication_mode_unknown_check_login_state" }
    }
    [pscustomobject]@{ Count = $matches.Count; Classification = $classification }
}

$results = New-Object System.Collections.Generic.List[object]

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$integrity = (whoami /groups | Select-String "High Mandatory Level|Medium Mandatory Level|Low Mandatory Level" | Select-Object -First 1).Line.Trim()
$results.Add((Write-Check "current-shell-admin" $isAdmin "User=$($identity.Name); Integrity=$integrity"))

$userConfigPath = Join-Path $env:USERPROFILE ".codex\config.toml"
$projectConfigPath = Join-Path (Get-Location) ".codex\config.toml"
$userConfig = if (Test-Path -LiteralPath $userConfigPath) { Get-Content -Raw -LiteralPath $userConfigPath } else { "" }
$projectConfig = if (Test-Path -LiteralPath $projectConfigPath) { Get-Content -Raw -LiteralPath $projectConfigPath } else { "" }
$effectiveConfig = $userConfig + "`n" + $projectConfig
$configPass = $effectiveConfig -match 'approval_policy\s*=\s*"never"' -and
    $effectiveConfig -match 'sandbox_mode\s*=\s*"danger-full-access"' -and
    $effectiveConfig -match '\[windows\]' -and
    $effectiveConfig -match 'sandbox\s*=\s*"elevated"'
$configDetail = "UserConfig=$userConfigPath; ProjectConfig=$projectConfigPath; Requires effective sandbox_mode=danger-full-access, approval_policy=never, [windows].sandbox=elevated"
$results.Add((Write-Check "codex-agent-sandbox-effective-config" $configPass $configDetail))

$taskXmlText = & schtasks /query /tn CodexDesktopElevatedAtLogon /xml 2>$null
$taskPass = $false
$taskDetail = "Task not found"
if ($taskXmlText) {
    try {
        [xml]$taskXml = $taskXmlText
        $runLevel = [string]$taskXml.Task.Principals.Principal.RunLevel
        $command = [string]$taskXml.Task.Actions.Exec.Command
        $arguments = [string]$taskXml.Task.Actions.Exec.Arguments
        $taskPass = $runLevel -eq "HighestAvailable" -and $arguments -match "start-codex-desktop-elevated.ps1"
        $taskDetail = "RunLevel=$runLevel; Command=$command; Arguments=$arguments"
    } catch {
        $taskDetail = "Task XML parse failed: $($_.Exception.Message)"
    }
}
$results.Add((Write-Check "scheduled-task" $taskPass $taskDetail))

$startScript = Join-Path $env:USERPROFILE ".codex\scripts\start-codex-desktop-elevated.ps1"
$triggerScript = Join-Path $env:USERPROFILE ".codex\scripts\run-codex-desktop-elevated-task.cmd"
$results.Add((Write-Check "start-script-exists" (Test-Path -LiteralPath $startScript) $startScript))
$results.Add((Write-Check "trigger-script-exists" (Test-Path -LiteralPath $triggerScript) $triggerScript))

$codexExe = Get-CodexDesktopExe
$layers = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
$runasTargets = @()
if ($codexExe) {
    $runasTargets += $codexExe
    $runasTargets += (Join-Path (Split-Path -Parent $codexExe) "resources\codex.exe")
}
$missingRunas = @()
if (Test-Path -LiteralPath $layers) {
    $props = Get-ItemProperty -LiteralPath $layers
    foreach ($target in $runasTargets) {
        $value = $props.PSObject.Properties[$target].Value
        if ($value -ne "~ RUNASADMIN") {
            $missingRunas += $target
        }
    }
} else {
    $missingRunas = $runasTargets
}
$results.Add((Write-Check "runasadmin-main-targets" ($missingRunas.Count -eq 0 -and $runasTargets.Count -gt 0) "Missing=$($missingRunas -join '; ')"))

$portOwners = @(Get-NetTCPConnection -LocalPort $remoteDebuggingPort -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
$stalePortOwners = New-Object System.Collections.Generic.List[int]
$portDetails = if ($portOwners) {
    ($portOwners | ForEach-Object {
        $p = Get-Process -Id $_ -ErrorAction SilentlyContinue
        if ($null -eq $p) {
            $stalePortOwners.Add([int]$_)
            "pid=$_ stale"
        } else {
            "pid=$_ name=$($p.ProcessName) elevated=$(Get-TokenElevation -ProcessId $_)"
        }
    }) -join "; "
} else {
    "not listening"
}
$portPass = $portOwners.Count -le 1 -or ($portOwners.Count -gt 0 -and $stalePortOwners.Count -eq $portOwners.Count)
$results.Add((Write-Check "port-$remoteDebuggingPort" $portPass "Source=$($cdpPort.Source); $portDetails"))

$desktopProcesses = Get-CodexDesktopHostProcesses -MainOnly
$appServerProcesses = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -ieq "codex.exe" -and
    $_.ExecutablePath -like "*\OpenAI.Codex_*\app\resources\codex.exe" -and
    $_.CommandLine -match "app-server"
}
$main = $desktopProcesses | Where-Object { $_.CommandLine -like "*--remote-debugging-port=$remoteDebuggingPort*" } | Select-Object -First 1
$appServer = $appServerProcesses | Select-Object -First 1
$mainPid = if ($main) { [string]$main.ProcessId } else { "Missing" }
$appServerPid = if ($appServer) { [string]$appServer.ProcessId } else { "Missing" }
$mainElevation = if ($main) { Get-TokenElevation -ProcessId $main.ProcessId } else { "Missing" }
$serverElevation = if ($appServer) { Get-TokenElevation -ProcessId $appServer.ProcessId } else { "Missing" }
$results.Add((Write-Check "codex-main-elevated" ($mainElevation -eq "Yes") "Pid=$mainPid; Elevated=$mainElevation"))
$results.Add((Write-Check "codex-app-server-elevated" ($serverElevation -eq "Yes") "Pid=$appServerPid; Elevated=$serverElevation"))

$loginMode = Get-CodexLoginMode
$results.Add((Write-Info "desktop-auth-mode" "Authenticated=$($loginMode.Authenticated); Mode=$($loginMode.Mode); Secret values are not logged"))

try {
    $userDataDir = Resolve-CodexDesktopUserDataDir
    $cookieDbPath = Join-Path $userDataDir "Default\Network\Cookies"
    $results.Add((Write-Info "desktop-profile-binding" "UserDataDir=$userDataDir; ProfilePresent=$(Test-Path -LiteralPath $userDataDir); CookieDbPresent=$(Test-Path -LiteralPath $cookieDbPath); Cookie presence is informational only and does not prove authentication health or startup success"))
} catch {
    $results.Add((Write-Info "desktop-profile-binding" "Profile resolution unavailable: $($_.Exception.Message)"))
}

if ($main) {
    $whamAuth = Get-CodexWhamAuthClassification -ProcessId ([int]$main.ProcessId) -AuthMode ([string]$loginMode.Mode)
    $results.Add((Write-Info "desktop-wham-auth" "Observed401=$($whamAuth.Count); Classification=$($whamAuth.Classification); /wham task and usage failures are account-feature diagnostics, not Desktop startup verdicts"))
} else {
    $results.Add((Write-Info "desktop-wham-auth" "Skipped because the current Desktop main process was not found"))
}

$results | Format-Table -AutoSize

if ($results.Status -contains "FAIL") {
    exit 1
}

