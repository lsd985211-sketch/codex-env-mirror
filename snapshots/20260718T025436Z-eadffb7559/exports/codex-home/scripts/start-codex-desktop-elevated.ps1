Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$packageHelperPath = Join-Path $env:USERPROFILE ".codex\scripts\codex-desktop-package.ps1"
if (-not (Test-Path -LiteralPath $packageHelperPath)) {
    throw "Codex Desktop package helper was not found: $packageHelperPath"
}
. $packageHelperPath

$logDir = Join-Path $env:USERPROFILE ".codex\logs"
$logPath = Join-Path $logDir "codex-desktop-elevated-start.log"
$stateDir = Join-Path $env:USERPROFILE ".codex\state"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$scriptStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$phaseStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$deepDiag = $env:CODEX_STARTUP_DEEP_DIAG -eq "1"
$dryRun = $env:CODEX_STARTUP_DRY_RUN -eq "1"
$script:CodexConfigPreflightCache = $null
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
        $port = $null
        if ($null -ne $RequestedPort) {
            try { $port = [int]$RequestedPort } catch { $port = $null }
        }
        if ($null -eq $port -and -not [string]::IsNullOrWhiteSpace($env:CODEX_CDP_PORT)) {
            try { $port = [int]$env:CODEX_CDP_PORT } catch { $port = $null }
        }
        if ($null -eq $port -or $port -lt 1 -or $port -gt 65535) {
            $port = 9229
        }
        if ($SetProcessEnv) {
            $env:CODEX_CDP_PORT = [string]$port
        }
        [pscustomobject]@{ Port = [int]$port; Source = "fallback"; StatePath = [string]$statePath; DefaultPort = 9229 }
    }
}

function Write-StartLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    [System.IO.File]::AppendAllText($logPath, "[$timestamp] $Message`r`n", [System.Text.Encoding]::UTF8)
}

function Write-PhaseLog {
    param([string]$Message)
    Write-StartLog "$Message; phaseMs=$($phaseStopwatch.ElapsedMilliseconds); totalMs=$($scriptStopwatch.ElapsedMilliseconds)"
    $phaseStopwatch.Restart()
}

$launchSafetyPath = Join-Path $env:USERPROFILE ".codex\scripts\codex-desktop-launch-safety.ps1"
if (-not (Test-Path -LiteralPath $launchSafetyPath)) {
    Write-StartLog "Governed launcher safety helper is missing; refusing an unprotected launch: $launchSafetyPath"
    exit 8
}
try {
    . $launchSafetyPath
} catch {
    Write-StartLog "Governed launcher safety helper failed to load; refusing an unprotected launch. Error=$($_.Exception.Message)"
    exit 8
}

function Get-EnvInt {
    param(
        [string]$Name,
        [int]$Default
    )
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    try {
        return [int]$value
    } catch {
        Write-StartLog "Invalid $Name value '$value'; using $Default."
        return $Default
    }
}

function Get-ObjectPropertyValue {
    param(
        $Object,
        [string]$Name,
        $Default = $null
    )
    if ($null -eq $Object) { return $Default }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
}

function Invoke-StartupLogRetention {
    try {
        $maxPreflightFiles = Get-EnvInt -Name "CODEX_STARTUP_PREFLIGHT_LOG_KEEP" -Default 40
        if ($maxPreflightFiles -lt 10) { $maxPreflightFiles = 10 }
        $preflightFiles = @(
            Get-ChildItem -LiteralPath $logDir -File -Filter "codex-config-preflight-*.log" -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTimeUtc -Descending
        )
        foreach ($file in @($preflightFiles | Select-Object -Skip $maxPreflightFiles)) {
            Remove-Item -LiteralPath $file.FullName -Force -ErrorAction SilentlyContinue
        }
        $maxMainLogBytes = 5MB
        if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt $maxMainLogBytes) {
            $archivePath = Join-Path $logDir "codex-desktop-elevated-start.previous.log"
            Move-Item -LiteralPath $logPath -Destination $archivePath -Force
        }
    } catch {
        Write-StartLog "Startup log retention failed without blocking launch: $($_.Exception.Message)"
    }
}

function Test-CooldownMarker {
    param(
        [string]$Path,
        [int]$CooldownSeconds
    )
    if ($CooldownSeconds -le 0) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    try {
        $age = (New-TimeSpan -Start (Get-Item -LiteralPath $Path).LastWriteTimeUtc -End (Get-Date).ToUniversalTime()).TotalSeconds
        return $age -lt $CooldownSeconds
    } catch {
        return $false
    }
}

function Touch-Marker {
    param([string]$Path)
    try {
        Set-Content -LiteralPath $Path -Value (Get-Date -Format "o") -Encoding UTF8
    } catch {
        Write-StartLog "Failed to update marker $Path`: $($_.Exception.Message)"
    }
}

function Start-ShortcutSelfRepairAsync {
    if ($dryRun) {
        Write-StartLog "Shortcut self-repair skipped by dry run."
        return
    }
    $repairScript = Join-Path $env:USERPROFILE ".codex\scripts\repair-codex-admin-shortcuts.ps1"
    if (-not (Test-Path -LiteralPath $repairScript)) {
        Write-StartLog "Shortcut repair script missing: $repairScript"
        return
    }

    try {
        $repairOut = Join-Path $logDir "codex-admin-shortcut-repair-startup.log"
        $repairErr = Join-Path $logDir "codex-admin-shortcut-repair-startup.err.log"
        $process = Start-Process `
            -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
            -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", $repairScript) `
            -WorkingDirectory (Split-Path -Parent $repairScript) `
            -WindowStyle Hidden `
            -PassThru `
            -RedirectStandardOutput $repairOut `
            -RedirectStandardError $repairErr
        Write-StartLog "Shortcut self-repair launched asynchronously. Pid=$($process.Id); Output=$repairOut; Error=$repairErr"
    } catch {
        Write-StartLog "Shortcut self-repair launch failed: $($_.Exception.Message)"
    }
}

function Resolve-CodexStartupPython {
    $pythonCandidates = @(
        "C:\Python314\python.exe",
        (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"),
        "python"
    )
    foreach ($candidate in $pythonCandidates) {
        if ($candidate -eq "python") {
            return $candidate
        }
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function ConvertTo-CmdQuoted {
    param([string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Get-CodexConfigPreflightSignature {
    $paths = @(
        (Join-Path $env:USERPROFILE ".codex\config.toml"),
        (Join-Path $env:USERPROFILE ".codex\.codex-global-state.json"),
        "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\.codex\config.toml",
        "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\codex_startup_baseline.json"
    )
    $parts = foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path) {
            $item = Get-Item -LiteralPath $path
            "$path|$($item.Length)|$($item.LastWriteTimeUtc.Ticks)"
        } else {
            "$path|missing"
        }
    }
    return ($parts -join "||")
}

function Invoke-CodexConfigPreflightSync {
    if ($dryRun) {
        Write-StartLog "Codex config preflight skipped by dry run."
        return @{ Ok = $true; Applied = $false; NeedsRestart = $false; Skipped = $true }
    }

    $workspace = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
    $guardScript = Join-Path $workspace "_bridge\codex_config_guard.py"
    if (-not (Test-Path -LiteralPath $guardScript)) {
        Write-StartLog "Codex config preflight missing guard script: $guardScript"
        return @{ Ok = $false; Applied = $false; NeedsRestart = $false; Error = "missing_guard_script" }
    }

    $inputSignature = Get-CodexConfigPreflightSignature
    if (
        $null -ne $script:CodexConfigPreflightCache -and
        $script:CodexConfigPreflightCache.Signature -eq $inputSignature
    ) {
        Write-StartLog "Codex config preflight reused the successful in-process result because protected inputs are unchanged."
        return $script:CodexConfigPreflightCache.Result
    }

    $pythonExe = Resolve-CodexStartupPython
    if (-not $pythonExe) {
        Write-StartLog "Codex config preflight failed: no Python executable was found."
        return @{ Ok = $false; Applied = $false; NeedsRestart = $false; Error = "missing_python" }
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    try {
        $timeoutSeconds = Get-EnvInt -Name "CODEX_STARTUP_PREFLIGHT_TIMEOUT_SECONDS" -Default 20
        if ($timeoutSeconds -lt 1) {
            $timeoutSeconds = 1
        }
        $runId = [guid]::NewGuid().ToString("N")
        $preflightOut = Join-Path $logDir "codex-config-preflight-$runId.out.log"
        $preflightErr = Join-Path $logDir "codex-config-preflight-$runId.err.log"
        $guardCommand = (ConvertTo-CmdQuoted $pythonExe) + " " + (ConvertTo-CmdQuoted $guardScript) + " run-once --apply --phase pre-start-static > " + (ConvertTo-CmdQuoted $preflightOut) + " 2> " + (ConvertTo-CmdQuoted $preflightErr)
        $cmdLine = '/d /s /c "' + $guardCommand + '"'
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $env:ComSpec
        $startInfo.Arguments = $cmdLine
        $startInfo.WorkingDirectory = $workspace
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        $started = $process.Start()
        if (-not $started) {
            Write-StartLog "Codex config preflight failed: process did not start."
            return @{ Ok = $false; Applied = $false; NeedsRestart = $false; Error = "guard_start_failed" }
        }
        if (-not $process.WaitForExit($timeoutSeconds * 1000)) {
            $killOutput = ""
            try {
                $killOutput = (& taskkill.exe /PID $process.Id /T /F 2>&1) -join [Environment]::NewLine
            } catch {
                $killOutput = $_.Exception.Message
            }
            Write-StartLog "Codex config preflight timed out after ${timeoutSeconds}s; stopped timed-out guard process tree to avoid late config/catalog writes. Pid=$($process.Id); Taskkill=$killOutput; Output=$preflightOut; Error=$preflightErr"
            return @{ Ok = $false; Applied = $false; NeedsRestart = $false; TimedOut = $true; Error = "guard_timeout_stopped" }
        }
        $exitCode = $process.ExitCode
        $stdout = ""
        $stderr = ""
        if (Test-Path -LiteralPath $preflightOut) {
            $stdoutRaw = Get-Content -LiteralPath $preflightOut -Raw -Encoding UTF8
            if (-not [string]::IsNullOrEmpty($stdoutRaw)) {
                $stdout = $stdoutRaw.Trim()
            }
        }
        if (Test-Path -LiteralPath $preflightErr) {
            $stderrRaw = Get-Content -LiteralPath $preflightErr -Raw -Encoding UTF8
            if (-not [string]::IsNullOrEmpty($stderrRaw)) {
                $stderr = $stderrRaw.Trim()
            }
        }
        $text = $stdout
        if ($exitCode -ne 0) {
            Write-StartLog "Codex config preflight failed. ExitCode=$exitCode; Output=$text; Error=$stderr"
            return @{ Ok = $false; Applied = $false; NeedsRestart = $false; Error = "guard_nonzero" }
        }
        $result = $text | ConvertFrom-Json -ErrorAction Stop
        $applied = [bool](Get-ObjectPropertyValue -Object $result -Name "applied" -Default $false)
        $needsRestart = [bool](Get-ObjectPropertyValue -Object $result -Name "needs_codex_restart" -Default $false)
        $beforeOk = $false
        $afterOk = $false
        if ($null -ne $result.before) { $beforeOk = [bool]$result.before.ok }
        if ($null -ne $result.after) { $afterOk = [bool]$result.after.ok }
        Write-StartLog "Codex config preflight completed. Ok=$([bool]$result.ok); Applied=$applied; NeedsRestart=$needsRestart; BeforeOk=$beforeOk; AfterOk=$afterOk"
        $preflightResult = @{ Ok = [bool]$result.ok; Applied = $applied; NeedsRestart = $needsRestart; BeforeOk = $beforeOk; AfterOk = $afterOk }
        if ([bool]$result.ok) {
            $script:CodexConfigPreflightCache = [pscustomobject]@{
                Signature = Get-CodexConfigPreflightSignature
                Result = $preflightResult
            }
            foreach ($completedLog in @($preflightOut, $preflightErr)) {
                Remove-Item -LiteralPath $completedLog -Force -ErrorAction SilentlyContinue
            }
        }
        return $preflightResult
    } catch {
        Write-StartLog "Codex config preflight failed: $($_.Exception.Message); Line=$($_.InvocationInfo.ScriptLineNumber); Statement=$($_.InvocationInfo.Line)"
        return @{ Ok = $false; Applied = $false; NeedsRestart = $false; Error = $_.Exception.Message }
    }
}

function Invoke-CodexCatalogReasoningPreflight {
    if ($dryRun) {
        Write-StartLog "Codex catalog reasoning preflight skipped by dry run."
        return @{ Ok = $true; Applied = $false; Skipped = $true }
    }

    $workspace = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
    $runtimeScript = Join-Path $workspace "_bridge\codex_desktop_model_runtime.py"
    $backupRouter = Join-Path $workspace "_bridge\shared\backup_router.py"
    $catalogPath = Join-Path $env:USERPROFILE ".codex\cc-switch-model-catalog.json"

    if (-not (Test-Path -LiteralPath $runtimeScript)) {
        Write-StartLog "Codex catalog reasoning preflight missing runtime script: $runtimeScript"
        return @{ Ok = $false; Applied = $false; Error = "missing_runtime_script" }
    }
    if (-not (Test-Path -LiteralPath $catalogPath)) {
        Write-StartLog "Codex catalog reasoning preflight skipped because catalog is absent: $catalogPath"
        return @{ Ok = $true; Applied = $false; Skipped = $true; Reason = "catalog_absent" }
    }

    $pythonExe = Resolve-CodexStartupPython
    if (-not $pythonExe) {
        Write-StartLog "Codex catalog reasoning preflight failed: no Python executable was found."
        return @{ Ok = $false; Applied = $false; Error = "missing_python" }
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    try {
        Push-Location -LiteralPath $workspace
        $planText = & $pythonExe $runtimeScript catalog-reasoning-plan --catalog-path $catalogPath 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-StartLog "Codex catalog reasoning preflight plan failed. ExitCode=$LASTEXITCODE; Output=$($planText -join [Environment]::NewLine)"
            return @{ Ok = $false; Applied = $false; Error = "catalog_reasoning_plan_failed" }
        }
        $plan = ($planText -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
        if (-not [bool]$plan.would_apply) {
            Write-StartLog "Codex catalog reasoning preflight completed. RepairNeeded=False; Catalog=$catalogPath"
            return @{ Ok = [bool]$plan.ok; Applied = $false; RepairNeeded = $false }
        }

        if (Test-Path -LiteralPath $backupRouter) {
            $backupText = & $pythonExe $backupRouter create $catalogPath --category codex-config --purpose backup-before-startup-catalog-reasoning-apply --trigger codex-desktop-startup-preflight --remark "Before startup catalog reasoning preflight apply" 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-StartLog "Codex catalog reasoning preflight backup failed. ExitCode=$LASTEXITCODE; Output=$($backupText -join [Environment]::NewLine)"
                return @{ Ok = $false; Applied = $false; RepairNeeded = $true; Error = "catalog_reasoning_backup_failed" }
            }
        } else {
            Write-StartLog "Codex catalog reasoning preflight backup router missing: $backupRouter"
            return @{ Ok = $false; Applied = $false; RepairNeeded = $true; Error = "missing_backup_router" }
        }

        $applyText = & $pythonExe $runtimeScript catalog-reasoning-apply --catalog-path $catalogPath 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-StartLog "Codex catalog reasoning preflight apply failed. ExitCode=$LASTEXITCODE; Output=$($applyText -join [Environment]::NewLine)"
            return @{ Ok = $false; Applied = $false; RepairNeeded = $true; Error = "catalog_reasoning_apply_failed" }
        }
        $apply = ($applyText -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
        Write-StartLog "Codex catalog reasoning preflight completed. RepairNeeded=True; Applied=$([bool]$apply.applied); Touched=$($apply.touched_count); Catalog=$catalogPath"
        return @{ Ok = [bool]$apply.ok; Applied = [bool]$apply.applied; RepairNeeded = $true; Touched = $apply.touched_count }
    } catch {
        Write-StartLog "Codex catalog reasoning preflight failed: $($_.Exception.Message); Line=$($_.InvocationInfo.ScriptLineNumber); Statement=$($_.InvocationInfo.Line)"
        return @{ Ok = $false; Applied = $false; Error = $_.Exception.Message }
    } finally {
        try { Pop-Location } catch { }
    }
}

function Set-GitHubMcpBearerTokenFromVault {
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_PERSONAL_ACCESS_TOKEN)) {
        Write-StartLog "GitHub MCP bearer token already present in launcher process environment."
        return @{ Ok = $true; Source = "process_env"; Injected = $false }
    }

    $workspace = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
    $vaultScript = Join-Path $workspace "_bridge\secret_vault.py"
    if (-not (Test-Path -LiteralPath $vaultScript)) {
        Write-StartLog "GitHub MCP token injection skipped: Secret Vault script missing."
        return @{ Ok = $false; Source = "secret_vault"; Injected = $false; Error = "missing_secret_vault_script" }
    }

    $pythonExe = Resolve-CodexStartupPython
    if (-not $pythonExe) {
        Write-StartLog "GitHub MCP token injection skipped: no Python executable was found."
        return @{ Ok = $false; Source = "secret_vault"; Injected = $false; Error = "missing_python" }
    }

    try {
        $token = & $pythonExe $vaultScript get --alias github.token --allow-print 2>$null
        $token = [string]$token
        if ([string]::IsNullOrWhiteSpace($token)) {
            Write-StartLog "GitHub MCP token injection skipped: github.token is empty or unavailable."
            return @{ Ok = $false; Source = "secret_vault"; Injected = $false; Error = "empty_or_missing_github_token" }
        }
        $env:GITHUB_PERSONAL_ACCESS_TOKEN = $token.Trim()
        Write-StartLog "GitHub MCP bearer token injected from Secret Vault into launcher process environment."
        return @{ Ok = $true; Source = "secret_vault:github.token"; Injected = $true }
    } catch {
        Write-StartLog "GitHub MCP token injection failed: $($_.Exception.Message)"
        return @{ Ok = $false; Source = "secret_vault"; Injected = $false; Error = $_.Exception.Message }
    }
}

function Start-CodexStartupBaselineRepairAsync {
    if ($dryRun) {
        Write-StartLog "Codex startup baseline repair skipped by dry run."
        return
    }

    $workspace = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
    $repairScript = Join-Path $workspace "_bridge\codex_config_guard.py"
    if (-not (Test-Path -LiteralPath $repairScript)) {
        Write-StartLog "Codex startup baseline guard script missing: $repairScript"
        return
    }

    $pythonExe = Resolve-CodexStartupPython
    if (-not $pythonExe) {
        Write-StartLog "Codex startup baseline repair skipped: no Python executable was found."
        return
    }

    $repairLog = Join-Path $logDir "codex-startup-baseline-repair.log"
    $repairErr = Join-Path $logDir "codex-startup-baseline-repair.err.log"
    $startupDelaySeconds = Get-EnvInt -Name "CODEX_BASELINE_REPAIR_STARTUP_DELAY_SECONDS" -Default 10
    if ($startupDelaySeconds -lt 0) {
        $startupDelaySeconds = 0
    }
    try {
        $process = Start-Process `
            -FilePath $pythonExe `
            -ArgumentList @($repairScript, "run-once", "--apply", "--phase", "post-start", "--startup-delay-seconds", "$startupDelaySeconds") `
            -WorkingDirectory (Split-Path -Parent $repairScript) `
            -WindowStyle Hidden `
            -PassThru `
            -RedirectStandardOutput $repairLog `
            -RedirectStandardError $repairErr
        Write-StartLog "Codex post-start config guard launched asynchronously. Pid=$($process.Id); StartupDelaySeconds=$startupDelaySeconds; Output=$repairLog; Error=$repairErr"
    } catch {
        Write-StartLog "Codex startup baseline repair launch failed: $($_.Exception.Message)"
    }
}

function Invoke-CodexSessionStorePreLaunchMaintenance {
    if ($dryRun) {
        Write-StartLog "Codex session-store pre-launch maintenance skipped by dry run."
        return @{ Ok = $true; Applied = $false; Skipped = $true; Reason = "dry_run" }
    }
    if ($env:CODEX_SESSION_PRELAUNCH_MAINTENANCE -eq "0") {
        Write-StartLog "Codex session-store pre-launch maintenance disabled by CODEX_SESSION_PRELAUNCH_MAINTENANCE=0."
        return @{ Ok = $true; Applied = $false; Skipped = $true; Reason = "disabled_by_environment" }
    }

    $workspace = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
    $prelaunchScript = Join-Path $workspace "_bridge\codex_prelaunch_maintenance.py"
    if (-not (Test-Path -LiteralPath $prelaunchScript)) {
        Write-StartLog "Codex session-store pre-launch maintenance skipped: adapter missing at $prelaunchScript"
        return @{ Ok = $false; Applied = $false; Error = "missing_prelaunch_adapter" }
    }
    $pythonExe = Resolve-CodexStartupPython
    if (-not $pythonExe) {
        Write-StartLog "Codex session-store pre-launch maintenance skipped: no Python executable was found."
        return @{ Ok = $false; Applied = $false; Error = "missing_python" }
    }

    try {
        $timeoutSeconds = Get-EnvInt -Name "CODEX_SESSION_PRELAUNCH_TIMEOUT_SECONDS" -Default 180
        if ($timeoutSeconds -lt 1) {
            $timeoutSeconds = 1
        }
        Push-Location $workspace
        $output = & $pythonExe $prelaunchScript run --workspace $workspace --timeout-seconds $timeoutSeconds 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-StartLog "Codex session-store pre-launch maintenance returned exit code $LASTEXITCODE. Startup will continue; Output=$($output -join ' ')"
            return @{ Ok = $false; Applied = $false; Error = "maintenance_exit_$LASTEXITCODE" }
        }
        $result = ($output -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
        if (-not [bool]$result.startup_permitted) {
            Write-StartLog "Codex pre-launch adapter returned an invalid fail-open receipt. Startup will continue. Outcome=$($result.outcome); Reason=$($result.reason)"
            return @{ Ok = $false; Applied = $false; Error = "startup_not_permitted_receipt" }
        }
        Write-StartLog "Codex session-store pre-launch maintenance completed. MaintenanceOk=$([bool]$result.maintenance_ok); Applied=$([bool]$result.applied); Outcome=$($result.outcome); Reason=$($result.reason); TimeoutSeconds=$timeoutSeconds"
        return @{ Ok = [bool]$result.maintenance_ok; Applied = [bool]$result.applied; Reason = [string]$result.reason; Error = [string]$result.outcome }
    } catch {
        Write-StartLog "Codex session-store pre-launch maintenance failed: $($_.Exception.Message). Startup will continue."
        return @{ Ok = $false; Applied = $false; Error = $_.Exception.Message }
    } finally {
        try { Pop-Location } catch { }
    }
}

function Invoke-CodexDesktopStatsigAllowlistSync {
    param([bool]$ReloadIfChanged = $true)

    if ($dryRun) {
        Write-StartLog "Codex Desktop Statsig allowlist sync skipped by dry run."
        return @{ Ok = $true; Applied = $false; Skipped = $true }
    }

    $workspace = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
    $runtimeScript = Join-Path $workspace "_bridge\codex_desktop_model_runtime.py"
    $catalogPath = Join-Path $env:USERPROFILE ".codex\cc-switch-model-catalog.json"

    if (-not (Test-Path -LiteralPath $runtimeScript)) {
        Write-StartLog "Codex Desktop Statsig allowlist sync skipped: runtime script missing: $runtimeScript"
        return @{ Ok = $false; Applied = $false; Error = "missing_runtime_script" }
    }
    if (-not (Test-Path -LiteralPath $catalogPath)) {
        Write-StartLog "Codex Desktop Statsig allowlist sync skipped: catalog absent: $catalogPath"
        return @{ Ok = $true; Applied = $false; Skipped = $true; Reason = "catalog_absent" }
    }

    $pythonExe = Resolve-CodexStartupPython
    if (-not $pythonExe) {
        Write-StartLog "Codex Desktop Statsig allowlist sync skipped: no Python executable was found."
        return @{ Ok = $false; Applied = $false; Error = "missing_python" }
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    try {
        Push-Location -LiteralPath $workspace
        $syncWaitSeconds = Get-EnvInt -Name "CODEX_STATSIG_ALLOWLIST_SYNC_WAIT_SECONDS" -Default 30
        if ($syncWaitSeconds -lt 0) {
            $syncWaitSeconds = 0
        }
        $args = @($runtimeScript, "statsig-allowlist-apply", "--catalog-path", $catalogPath, "--wait-seconds", "$syncWaitSeconds")
        if ($ReloadIfChanged) {
            $args += "--reload-if-changed"
        }
        $syncText = & $pythonExe @args 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-StartLog "Codex Desktop Statsig allowlist sync did not complete. ExitCode=$LASTEXITCODE; Output=$($syncText -join [Environment]::NewLine)"
            return @{ Ok = $false; Applied = $false; Error = "statsig_allowlist_sync_failed" }
        }
        $sync = ($syncText -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
        $result = $sync.result
        $changed = $false
        $applied = $false
        $added = @()
        $removed = @()
        if ($null -ne $result) {
            $changed = [bool]$result.changed
            $applied = [bool]$result.applied
            if ($null -ne $result.added) { $added = @($result.added) }
            if ($null -ne $result.removed) { $removed = @($result.removed) }
        }
        Write-StartLog "Codex Desktop Statsig allowlist sync completed. Ok=$([bool]$sync.ok); Changed=$changed; Applied=$applied; WaitSeconds=$syncWaitSeconds; Added=$($added -join ','); Removed=$($removed -join ',')"
        return @{ Ok = [bool]$sync.ok; Applied = $applied; Changed = $changed; Added = $added; Removed = $removed }
    } catch {
        Write-StartLog "Codex Desktop Statsig allowlist sync failed: $($_.Exception.Message); Line=$($_.InvocationInfo.ScriptLineNumber); Statement=$($_.InvocationInfo.Line)"
        return @{ Ok = $false; Applied = $false; Error = $_.Exception.Message }
    } finally {
        try { Pop-Location } catch { }
    }
}

function Invoke-CodexDesktopModelListBridgeShim {
    if ($dryRun) {
        Write-StartLog "Codex Desktop model-list bridge shim skipped by dry run."
        return @{ Ok = $true; Applied = $false; Skipped = $true }
    }

    $workspace = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
    $runtimeScript = Join-Path $workspace "_bridge\codex_desktop_model_runtime.py"
    $catalogPath = Join-Path $env:USERPROFILE ".codex\cc-switch-model-catalog.json"

    if (-not (Test-Path -LiteralPath $runtimeScript)) {
        Write-StartLog "Codex Desktop model-list bridge shim skipped: runtime script missing: $runtimeScript"
        return @{ Ok = $false; Applied = $false; Error = "missing_runtime_script" }
    }
    if (-not (Test-Path -LiteralPath $catalogPath)) {
        Write-StartLog "Codex Desktop model-list bridge shim skipped: catalog absent: $catalogPath"
        return @{ Ok = $true; Applied = $false; Skipped = $true; Reason = "catalog_absent" }
    }

    $pythonExe = Resolve-CodexStartupPython
    if (-not $pythonExe) {
        Write-StartLog "Codex Desktop model-list bridge shim skipped: no Python executable was found."
        return @{ Ok = $false; Applied = $false; Error = "missing_python" }
    }

    $waitSeconds = Get-EnvInt -Name "CODEX_MODEL_LIST_BRIDGE_SHIM_WAIT_SECONDS" -Default 30

    function Get-ShimPropertyValue {
        param(
            $Object,
            [Parameter(Mandatory = $true)][string]$Name,
            $Default = $null
        )
        if ($null -eq $Object) { return $Default }
        $property = $Object.PSObject.Properties[$Name]
        if ($null -eq $property) { return $Default }
        return $property.Value
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    try {
        Push-Location -LiteralPath $workspace
        $appServerText = & $pythonExe $runtimeScript appserver-model-shim-apply --catalog-path $catalogPath --wait-seconds $waitSeconds 2>&1
        $appServerExitCode = $LASTEXITCODE
        $appServerShim = if ($appServerExitCode -eq 0) { ($appServerText -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop } else { $null }
        $appServerOk = [bool](Get-ShimPropertyValue -Object $appServerShim -Name "ok" -Default $false)
        $appServerModelCount = [int](Get-ShimPropertyValue -Object $appServerShim -Name "model_count" -Default 0)
        if ($appServerOk -and $appServerModelCount -gt 0) {
            $appServerApplied = [bool](Get-ShimPropertyValue -Object $appServerShim -Name "applied" -Default $false)
            Write-StartLog "Codex Desktop app-server model shim completed; legacy bridge skipped. Applied=$appServerApplied; Models=$appServerModelCount; WaitSeconds=$waitSeconds"
            return @{ Ok = $true; Applied = $appServerApplied; AppServerOk = $true; LegacyOk = $false; LegacySkipped = $true; ModelCount = $appServerModelCount }
        }

        $shimText = & $pythonExe $runtimeScript model-list-bridge-shim-apply --catalog-path $catalogPath --wait-seconds $waitSeconds 2>&1
        $legacyExitCode = $LASTEXITCODE
        if ($appServerExitCode -ne 0 -and $legacyExitCode -ne 0) {
            Write-StartLog "Codex Desktop model-list shims did not complete. AppServerExitCode=$appServerExitCode; AppServerOutput=$($appServerText -join [Environment]::NewLine); LegacyExitCode=$legacyExitCode; LegacyOutput=$($shimText -join [Environment]::NewLine)"
            return @{ Ok = $false; Applied = $false; Error = "model_list_bridge_shim_failed" }
        }
        $legacyShim = if ($legacyExitCode -eq 0) { ($shimText -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop } else { $null }
        $legacyOk = [bool](Get-ShimPropertyValue -Object $legacyShim -Name "ok" -Default $false)
        $ok = $appServerOk -or $legacyOk
        $applied = [bool](Get-ShimPropertyValue -Object $appServerShim -Name "applied" -Default $false) -or [bool](Get-ShimPropertyValue -Object $legacyShim -Name "applied" -Default $false)
        $modelCount = Get-ShimPropertyValue -Object $appServerShim -Name "model_count" -Default (Get-ShimPropertyValue -Object $legacyShim -Name "model_count" -Default 0)
        Write-StartLog "Codex Desktop model-list shims completed. Ok=$ok; AppServerOk=$appServerOk; LegacyOk=$legacyOk; Applied=$applied; Models=$modelCount; WaitSeconds=$waitSeconds"
        return @{ Ok = $ok; Applied = $applied; AppServerOk = $appServerOk; LegacyOk = $legacyOk; ModelCount = $modelCount }
    } catch {
        Write-StartLog "Codex Desktop model-list bridge shim failed: $($_.Exception.Message); Line=$($_.InvocationInfo.ScriptLineNumber); Statement=$($_.InvocationInfo.Line)"
        return @{ Ok = $false; Applied = $false; Error = $_.Exception.Message }
    } finally {
        try { Pop-Location } catch { }
    }
}

function Start-CodexDesktopStatsigAllowlistSyncAsync {
    if ($dryRun) {
        Write-StartLog "Codex Desktop Statsig allowlist stabilizer skipped by dry run."
        return
    }

    $workspace = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
    $runtimeScript = Join-Path $workspace "_bridge\codex_desktop_model_runtime.py"
    $catalogPath = Join-Path $env:USERPROFILE ".codex\cc-switch-model-catalog.json"
    if (-not (Test-Path -LiteralPath $runtimeScript)) {
        Write-StartLog "Codex Desktop Statsig allowlist stabilizer skipped: runtime script missing: $runtimeScript"
        return
    }
    if (-not (Test-Path -LiteralPath $catalogPath)) {
        Write-StartLog "Codex Desktop Statsig allowlist stabilizer skipped: catalog absent: $catalogPath"
        return
    }

    $pythonExe = Resolve-CodexStartupPython
    if (-not $pythonExe) {
        Write-StartLog "Codex Desktop Statsig allowlist stabilizer skipped: no Python executable was found."
        return
    }

    $stabilizeDelays = $env:CODEX_STATSIG_ALLOWLIST_STABILIZE_DELAYS
    if ([string]::IsNullOrWhiteSpace($stabilizeDelays)) {
        $legacyDelaySeconds = Get-EnvInt -Name "CODEX_STATSIG_ALLOWLIST_DELAYED_SYNC_DELAY_SECONDS" -Default -1
        if ($legacyDelaySeconds -ge 0) {
            $stabilizeDelays = "$legacyDelaySeconds"
        } else {
            $stabilizeDelays = "20,60,120"
        }
    }
    $waitSeconds = Get-EnvInt -Name "CODEX_STATSIG_ALLOWLIST_STABILIZE_WAIT_SECONDS" -Default -1
    if ($waitSeconds -lt 0) {
        $waitSeconds = Get-EnvInt -Name "CODEX_STATSIG_ALLOWLIST_DELAYED_SYNC_WAIT_SECONDS" -Default 45
    }
    if ($waitSeconds -lt 0) { $waitSeconds = 0 }
    $stableRequired = Get-EnvInt -Name "CODEX_STATSIG_ALLOWLIST_STABLE_REQUIRED" -Default 2
    if ($stableRequired -lt 1) { $stableRequired = 1 }
    $summaryLog = Join-Path $logDir "codex-statsig-allowlist-stabilizer.log"
    $jsonlLog = Join-Path $logDir "codex-statsig-allowlist-stabilizer.jsonl"
    $syncErr = Join-Path $logDir "codex-statsig-allowlist-stabilizer.err.log"

    try {
        $process = Start-Process `
            -FilePath $pythonExe `
            -ArgumentList @($runtimeScript, "statsig-allowlist-stabilize", "--catalog-path", $catalogPath, "--wait-seconds", "$waitSeconds", "--stabilize-delays", $stabilizeDelays, "--stable-required", "$stableRequired", "--log-jsonl", $jsonlLog, "--reload-if-changed") `
            -WorkingDirectory $workspace `
            -WindowStyle Hidden `
            -PassThru `
            -RedirectStandardOutput $summaryLog `
            -RedirectStandardError $syncErr
        Write-StartLog "Codex Desktop Statsig allowlist stabilizer launched asynchronously. Pid=$($process.Id); Delays=$stabilizeDelays; WaitSeconds=$waitSeconds; StableRequired=$stableRequired; Output=$summaryLog; Jsonl=$jsonlLog; Error=$syncErr"
    } catch {
        Write-StartLog "Codex Desktop Statsig allowlist stabilizer launch failed: $($_.Exception.Message)"
    }
}

function Invoke-CodexDesktopModelRuntimeReconcile {
    param(
        [bool]$ReloadImmediately = $true,
        [string]$Context = "Codex Desktop"
    )

    $modelListBridgeShim = Invoke-CodexDesktopModelListBridgeShim
    Write-PhaseLog "Desktop model-list bridge shim phase finished"
    if (-not [bool]$modelListBridgeShim.Ok) {
        Write-StartLog "$Context model-list bridge shim failed. Model picker may keep stale or incomplete choices until native list-models-for-host works or the shim is applied manually."
    }

    if ($ReloadImmediately) {
        $statsigAllowlistSync = Invoke-CodexDesktopStatsigAllowlistSync -ReloadIfChanged $true
        Write-PhaseLog "Desktop Statsig allowlist sync phase finished"
        if (-not [bool]$statsigAllowlistSync.Ok) {
            Write-StartLog "$Context Statsig allowlist sync failed. Model picker may keep stale filtered models until manual repair or next restart."
        }
    }

    Start-CodexDesktopStatsigAllowlistSyncAsync
    Write-PhaseLog "Delayed Desktop Statsig allowlist sync launch phase finished"
}

function Start-CodexModelProviderWatcherAsync {
    if ($dryRun) {
        Write-StartLog "Codex model provider watcher skipped by dry run."
        return
    }

    $workspace = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
    $watcherScript = Join-Path $workspace "_bridge\codex_model_provider_watcher.py"
    if (-not (Test-Path -LiteralPath $watcherScript)) {
        Write-StartLog "Codex model provider watcher script missing: $watcherScript"
        return
    }
    $pythonExe = Resolve-CodexStartupPython
    if (-not $pythonExe) {
        Write-StartLog "Codex model provider watcher skipped: no Python executable was found."
        return
    }
    try {
        $process = Start-Process `
            -FilePath $pythonExe `
            -ArgumentList @($watcherScript, "supervise", "--poll-seconds", "2", "--debounce-seconds", "1.5", "--drift-check-seconds", "10") `
            -WorkingDirectory $workspace `
            -WindowStyle Hidden `
            -PassThru
        Write-StartLog "Codex model provider watcher supervisor launch requested. Pid=$($process.Id); child lock prevents duplicate active watchers."
    } catch {
        Write-StartLog "Codex model provider watcher launch failed: $($_.Exception.Message)"
    }
}

function Get-TokenElevation {
    param([int]$ProcessId)
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        $source = @"
using System;
using System.Runtime.InteropServices;
public static class CodexTokenCheck {
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
        if (-not ("CodexTokenCheck" -as [type])) {
            Add-Type -TypeDefinition $source
        }
        $value = [CodexTokenCheck]::GetElevation($process.Handle)
        if ($value -eq 1) { return "Yes" }
        if ($value -eq 0) { return "No" }
        return "Unknown($value)"
    } catch {
        return "Unknown($($_.Exception.Message))"
    }
}

function Get-PortOwnerSummary {
    param(
        [int]$Port,
        [bool]$Deep = $false
    )
    if (-not $Deep) {
        $listener = $null
        try {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
            $listener.Start()
            return "free"
        } catch {
            return "in-use"
        } finally {
            if ($null -ne $listener) {
                $listener.Stop()
            }
        }
    }
    $owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    if (-not $owners) {
        return "free"
    }
    $parts = @()
    $stale = @()
    foreach ($owner in $owners) {
        try {
            $p = Get-Process -Id $owner -ErrorAction Stop
            if ($Deep) {
                $parts += "pid=$owner name=$($p.ProcessName) elevated=$(Get-TokenElevation -ProcessId $owner)"
            } else {
                $parts += "pid=$owner name=$($p.ProcessName)"
            }
        } catch {
            $stale += "pid=$owner"
        }
    }
    if ($parts.Count -eq 0) {
        if ($stale.Count -gt 0) {
            return "stale-listener " + ($stale -join "; ")
        }
        return "free"
    }
    if ($stale.Count -gt 0) {
        $parts += "stale-listener " + ($stale -join "; ")
    }
    return ($parts -join "; ")
}

function Test-CodexCdpVersionReady {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 2
    )
    $urls = @(
        "http://localhost:$Port/json/version",
        "http://[::1]:$Port/json/version",
        "http://127.0.0.1:$Port/json/version"
    )
    foreach ($url in $urls) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $TimeoutSeconds -ErrorAction Stop
            if ($response.StatusCode -eq 200 -and $response.Content -match '"Protocol-Version"') {
                Write-StartLog "Existing Codex CDP is ready at $url"
                return $true
            }
        } catch {
            Write-StartLog "Existing Codex CDP probe failed at $url`: $($_.Exception.Message)"
        }
    }
    return $false
}

function Wait-CodexCdpVersionReady {
    param(
        [int]$Port,
        [int]$MaxWaitSeconds = 30,
        [int]$ProbeTimeoutSeconds = 2
    )
    if ($MaxWaitSeconds -lt 1) {
        $MaxWaitSeconds = 1
    }
    $deadline = (Get-Date).AddSeconds($MaxWaitSeconds)
    $attempt = 0
    do {
        $attempt += 1
        if (Test-CodexCdpVersionReady -Port $Port -TimeoutSeconds $ProbeTimeoutSeconds) {
            Write-StartLog "Codex CDP became ready. RemoteDebuggingPort=$Port; Attempts=$attempt; MaxWaitSeconds=$MaxWaitSeconds"
            return $true
        }
        Start-Sleep -Milliseconds 750
    } while ((Get-Date) -lt $deadline)
    Write-StartLog "Codex CDP did not become ready within ${MaxWaitSeconds}s. RemoteDebuggingPort=$Port; Attempts=$attempt"
    return $false
}

function Get-CodexDesktopUserDataDir {
    Resolve-CodexDesktopUserDataDir
}

function Get-CachedCodexDesktopExe {
    $cachePath = Join-Path $stateDir "codex-desktop-exe.path"
    if (-not (Test-Path -LiteralPath $cachePath)) {
        return $null
    }
    try {
        $cached = (Get-Content -LiteralPath $cachePath -Encoding UTF8 -TotalCount 1).Trim()
        if (-not [string]::IsNullOrWhiteSpace($cached) -and (Test-Path -LiteralPath $cached)) {
            return $cached
        }
    } catch {
        Write-StartLog "Codex executable cache read failed: $($_.Exception.Message)"
    }
    return $null
}

function Set-CachedCodexDesktopExe {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }
    $cachePath = Join-Path $stateDir "codex-desktop-exe.path"
    try {
        Set-Content -LiteralPath $cachePath -Value $Path -Encoding UTF8
    } catch {
        Write-StartLog "Codex executable cache write failed: $($_.Exception.Message)"
    }
}

function Get-LatestCodexDesktopExe {
    $package = Get-AppxPackage -Name "OpenAI.Codex" |
        Sort-Object -Property @{ Expression = { [version]$_.Version }; Descending = $true } |
        Select-Object -First 1

    if ($null -ne $package) {
        $candidate = Resolve-CodexDesktopEntrypointFromInstallLocation -InstallLocation ([string]$package.InstallLocation)
        if (-not [string]::IsNullOrWhiteSpace($candidate)) {
            return $candidate
        }
    }

    $fallback = Get-ChildItem -LiteralPath "C:\Program Files\WindowsApps" -Directory -Filter "OpenAI.Codex_*" -ErrorAction SilentlyContinue |
        Sort-Object -Property Name -Descending |
        ForEach-Object { Resolve-CodexDesktopEntrypointFromInstallLocation -InstallLocation $_.FullName } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -First 1

    if ($null -ne $fallback) {
        return $fallback
    }

    throw "OpenAI Codex Desktop executable was not found."
}

function Get-CodexDesktopExe {
    $latest = Get-LatestCodexDesktopExe
    $cached = Get-CachedCodexDesktopExe
    if (-not [string]::IsNullOrWhiteSpace($cached) -and $cached -eq $latest) {
        Write-StartLog "Using current cached Codex Desktop executable: $cached"
        return $cached
    }
    if (-not [string]::IsNullOrWhiteSpace($cached) -and $cached -ne $latest) {
        Write-StartLog "Refreshing stale Codex executable cache. Cached=$cached; Latest=$latest"
    } else {
        Write-StartLog "Writing Codex executable cache: $latest"
    }
    Set-CachedCodexDesktopExe -Path $latest
    return $latest
}

function Test-CodexCdpBackedByElevatedProcess {
    param([int]$Port)
    $owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    if (-not $owners) {
        Write-StartLog "Existing Codex CDP elevation check failed: no listener owner for port $Port"
        return $false
    }
    foreach ($owner in $owners) {
        try {
            $process = Get-Process -Id $owner -ErrorAction Stop
            $path = [string]$process.Path
            if ($path -notmatch "\\OpenAI\.Codex_.*\\app\\(ChatGPT|Codex|resources\\codex)\.exe$") {
                Write-StartLog "Existing CDP owner is not Codex Desktop. Pid=$owner; Path=$path"
                continue
            }
            $elevation = Get-TokenElevation -ProcessId $owner
            Write-StartLog "Existing Codex CDP owner elevation. Pid=$owner; Path=$path; Elevated=$elevation"
            if ($elevation -eq "Yes") {
                return $true
            }
        } catch {
            Write-StartLog "Existing Codex CDP owner elevation check failed. Pid=$owner; Error=$($_.Exception.Message)"
        }
    }
    return $false
}

function Wait-CodexProcessQuiescence {
    param(
        [int]$RemoteDebuggingPort,
        [int]$MaxWaitSeconds = 20
    )
    if ($MaxWaitSeconds -lt 1) {
        $MaxWaitSeconds = 1
    }
    $deadline = (Get-Date).AddSeconds($MaxWaitSeconds)
    $attempt = 0
    do {
        $attempt += 1
        if (Test-CodexCdpVersionReady -Port $RemoteDebuggingPort -TimeoutSeconds 1) {
            $processes = @(Get-CodexDesktopProcessRecords)
            if (-not (Test-CodexProcessScanReliable)) {
                return @{ State = "process_scan_failed"; Attempts = $attempt; Processes = @() }
            }
            return @{ State = "cdp_ready"; Attempts = $attempt; Processes = $processes }
        }
        $processes = @(Get-CodexDesktopProcessRecords)
        if (-not (Test-CodexProcessScanReliable)) {
            return @{ State = "process_scan_failed"; Attempts = $attempt; Processes = @() }
        }
        if ($processes.Count -eq 0) {
            return @{ State = "quiescent"; Attempts = $attempt; Processes = @() }
        }
        Start-Sleep -Milliseconds 750
    } while ((Get-Date) -lt $deadline)
    $processes = @(Get-CodexDesktopProcessRecords)
    if (-not (Test-CodexProcessScanReliable)) {
        return @{ State = "process_scan_failed"; Attempts = $attempt; Processes = @() }
    }
    return @{ State = "still_running"; Attempts = $attempt; Processes = $processes }
}

function Stop-StaleCodexDesktopProcesses {
    param([object[]]$Processes)
    $stopped = @()
    $failed = @()
    foreach ($process in ($Processes | Sort-Object -Property ProcessId -Unique)) {
        try {
            Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop
            $stopped += [int]$process.ProcessId
        } catch {
            $failed += "pid=$($process.ProcessId):$($_.Exception.Message)"
        }
    }
    return @{ Stopped = $stopped; Failed = $failed }
}

function Ensure-CodexRestartBoundaryClean {
    param([int]$RemoteDebuggingPort)
    $processes = @(Get-CodexDesktopProcessRecords)
    if (-not (Test-CodexProcessScanReliable)) {
        Write-StartLog "Codex restart boundary cannot be proven because process census failed."
        return @{ Ok = $false; Action = "process_scan_failed"; ProcessCount = 0 }
    }
    if ($processes.Count -eq 0) {
        Write-StartLog "Codex restart boundary clean: no existing Codex Desktop process family."
        return @{ Ok = $true; Action = "none"; ProcessCount = 0 }
    }

    $waitSeconds = Get-EnvInt -Name "CODEX_STALE_CODEX_EXIT_WAIT_SECONDS" -Default 20
    Write-StartLog "Codex processes exist while CDP is unavailable; waiting for clean exit or CDP recovery. MaxWaitSeconds=$waitSeconds; Processes=$(Get-CodexProcessSummary -Processes $processes)"
    $wait = Wait-CodexProcessQuiescence -RemoteDebuggingPort $RemoteDebuggingPort -MaxWaitSeconds $waitSeconds
    if ($wait.State -eq "process_scan_failed") {
        Write-StartLog "Codex restart boundary wait stopped because process census became unavailable."
        return @{ Ok = $false; Action = "process_scan_failed"; ProcessCount = 0 }
    }
    if ($wait.State -eq "cdp_ready") {
        Write-StartLog "Codex restart boundary recovered: CDP became ready while waiting. Attempts=$($wait.Attempts)"
        return @{ Ok = $true; Action = "cdp_recovered"; ProcessCount = @($wait.Processes).Count }
    }
    if ($wait.State -eq "quiescent") {
        Write-StartLog "Codex restart boundary recovered: old Codex process family exited before relaunch. Attempts=$($wait.Attempts)"
        return @{ Ok = $true; Action = "old_processes_exited"; ProcessCount = 0 }
    }

    $remaining = @($wait.Processes)
    $allowCleanup = ($env:CODEX_ALLOW_STALE_CODEX_CLEANUP -eq "1") -and ($env:CODEX_DISABLE_STALE_CODEX_CLEANUP -ne "1") -and (-not $dryRun)
    if (-not $allowCleanup) {
        Write-StartLog "Codex restart boundary still dirty; automatic force cleanup requires explicit CODEX_ALLOW_STALE_CODEX_CLEANUP=1. Processes=$(Get-CodexProcessSummary -Processes $remaining)"
        return @{ Ok = $false; Action = "cleanup_requires_explicit_opt_in"; ProcessCount = $remaining.Count }
    }

    Write-StartLog "Stopping stale Codex process family before relaunch. Processes=$(Get-CodexProcessSummary -Processes $remaining)"
    $stop = Stop-StaleCodexDesktopProcesses -Processes $remaining
    Start-Sleep -Seconds 2
    $after = @(Get-CodexDesktopProcessRecords)
    if (-not (Test-CodexProcessScanReliable)) {
        Write-StartLog "Stale Codex cleanup verification failed because process census is unavailable."
        return @{ Ok = $false; Action = "cleanup_verification_scan_failed"; ProcessCount = 0; Stopped = $stop.Stopped; Failed = $stop.Failed }
    }
    if ($after.Count -gt 0) {
        Write-StartLog "Stale Codex cleanup incomplete. Stopped=$($stop.Stopped -join ','); Failed=$($stop.Failed -join '; '); Remaining=$(Get-CodexProcessSummary -Processes $after)"
        return @{ Ok = $false; Action = "cleanup_incomplete"; ProcessCount = $after.Count; Stopped = $stop.Stopped; Failed = $stop.Failed }
    }
    Write-StartLog "Stale Codex process family cleaned before relaunch. Stopped=$($stop.Stopped -join ',')"
    return @{ Ok = $true; Action = "stale_processes_stopped"; ProcessCount = 0; Stopped = $stop.Stopped; Failed = $stop.Failed }
}


function Ensure-CodexRunAsAdminLayers {
    param([string]$CodexExe)
    $targets = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($CodexExe)) {
        $targets.Add($CodexExe)
        $resourceExe = Join-Path (Split-Path -Parent $CodexExe) "resources\codex.exe"
        if (Test-Path -LiteralPath $resourceExe) {
            $targets.Add($resourceExe)
        }
    }

    $layerPath = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
    if (-not (Test-Path -LiteralPath $layerPath)) {
        New-Item -Path $layerPath -Force | Out-Null
    }
    $props = $null
    try {
        $props = Get-ItemProperty -LiteralPath $layerPath -ErrorAction SilentlyContinue
    } catch {
        $props = $null
    }
    foreach ($target in $targets) {
        try {
            $current = $null
            if ($null -ne $props -and $null -ne $props.PSObject.Properties[$target]) {
                $current = $props.PSObject.Properties[$target].Value
            }
            if ($current -eq "~ RUNASADMIN") {
                Write-StartLog "RUNASADMIN layer already present: $target"
                continue
            }
            New-ItemProperty -LiteralPath $layerPath -Name $target -Value "~ RUNASADMIN" -PropertyType String -Force | Out-Null
            Write-StartLog "Wrote RUNASADMIN layer: $target"
        } catch {
            Write-StartLog "Failed to ensure RUNASADMIN layer for $target`: $($_.Exception.Message)"
        }
    }
}
Write-StartLog "Launcher entered. DryRun=$dryRun; DeepDiag=$deepDiag; CODEX_CDP_PORT=$env:CODEX_CDP_PORT"
if (-not (Enter-CodexLauncherSingleton)) {
    $singletonReason = Get-CodexLauncherSingletonReason
    if ($singletonReason -eq "named_mutex_busy") {
        Write-StartLog "Duplicate governed launcher invocation ignored because another launcher instance is active."
        exit 0
    }
    Write-StartLog "Governed launcher stopped because serialization could not be established. Reason=$singletonReason"
    exit 8
}
Invoke-StartupLogRetention
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Start-ShortcutSelfRepairAsync
Write-PhaseLog "Shortcut self-repair launch phase finished"
$configPreflight = Invoke-CodexConfigPreflightSync
Write-PhaseLog "Config preflight phase finished"
if (-not [bool]$configPreflight.Ok) {
    if ($env:CODEX_STARTUP_PREFLIGHT_FAIL_CLOSED -eq "1") {
        Write-StartLog "Codex Desktop startup blocked because config preflight failed and CODEX_STARTUP_PREFLIGHT_FAIL_CLOSED=1."
        exit 5
    }
    Write-StartLog "Codex Desktop startup continuing in degraded mode because config preflight failed. MCP/plugin config may be incomplete until guard repairs it and Codex is relaunched. Set CODEX_STARTUP_PREFLIGHT_FAIL_CLOSED=1 only when strict startup blocking is desired."
}
Start-CodexModelProviderWatcherAsync
Write-PhaseLog "Model provider watcher phase finished"
$codexExe = Get-CodexDesktopExe
Write-PhaseLog "Codex executable resolution phase finished"
Ensure-CodexRunAsAdminLayers -CodexExe $codexExe
Write-PhaseLog "RUNASADMIN layer phase finished"
$userDataDir = Get-CodexDesktopUserDataDir
$cookieDbPath = Join-Path $userDataDir "Default\Network\Cookies"
$legacyUserDataDir = Join-Path $env:APPDATA "Codex\web\Codex"
New-Item -ItemType Directory -Force -Path $userDataDir | Out-Null
Write-StartLog "Resolved canonical Codex Desktop profile. UserDataDir=$userDataDir; CookieDbPresent=$(Test-Path -LiteralPath $cookieDbPath) (informational only, not an authentication-health or startup-success signal); LegacyDirectProfile=$legacyUserDataDir; LegacyProfilePresent=$(Test-Path -LiteralPath $legacyUserDataDir)"
Write-PhaseLog "Canonical Desktop profile resolution phase finished"
$cdpPort = Resolve-CodexCdpPort -Persist -SetProcessEnv
$remoteDebuggingPort = [int]$cdpPort.Port
Write-StartLog "Resolved Codex CDP port. RemoteDebuggingPort=$remoteDebuggingPort; Source=$($cdpPort.Source); StatePath=$($cdpPort.StatePath); DefaultPort=$($cdpPort.DefaultPort)"
$arguments = @(
  "--remote-debugging-port=$remoteDebuggingPort",
  "--remote-allow-origins=http://127.0.0.1:$remoteDebuggingPort",
  "--user-data-dir=`"$userDataDir`""
)

$versionProbe = Join-Path $env:TEMP "codex-cdp-version-probe.json"
if (Test-Path -LiteralPath $versionProbe) {
  Remove-Item -LiteralPath $versionProbe -Force -ErrorAction SilentlyContinue
}

$beforePort = Get-PortOwnerSummary -Port $remoteDebuggingPort -Deep $deepDiag
Write-PhaseLog "Port precheck phase finished"
$existingCdpReady = $false
if ($beforePort -ne "free") {
    $existingCdpReady = Test-CodexCdpVersionReady -Port $remoteDebuggingPort
}
if ($existingCdpReady) {
    if (Test-CodexCdpBackedByElevatedProcess -Port $remoteDebuggingPort) {
        Write-StartLog "Codex Desktop startup skipped because existing CDP endpoint is already usable and elevated. RemoteDebuggingPort=$remoteDebuggingPort; PortBefore=$beforePort"
        if ([bool]$configPreflight.Applied -or [bool]$configPreflight.NeedsRestart) {
            Write-StartLog "Existing Codex Desktop may be using stale config because startup preflight applied changes or requested restart. Close and relaunch Codex Desktop to load repaired MCP/plugin config."
        }
        Invoke-CodexDesktopModelRuntimeReconcile -ReloadImmediately $true -Context "Existing Codex Desktop"
        Start-CodexStartupBaselineRepairAsync
        Write-PhaseLog "Baseline repair phase finished"
        Write-StartLog "Launcher finished without new Start-Process; totalMs=$($scriptStopwatch.ElapsedMilliseconds)"
        exit 0
    }
    Write-StartLog "Codex Desktop elevated startup blocked because port $remoteDebuggingPort is already owned by a non-elevated Codex process. Close the current Codex window, then start Codex Current Admin again. RemoteDebuggingPort=$remoteDebuggingPort; PortBefore=$beforePort"
    exit 2
}
if ($beforePort -ne "free") {
    $existingWaitSeconds = Get-EnvInt -Name "CODEX_EXISTING_CDP_READY_WAIT_SECONDS" -Default 30
    Write-StartLog "CDP port is occupied but version endpoint is not ready yet. Waiting instead of starting a duplicate Codex process. RemoteDebuggingPort=$remoteDebuggingPort; PortBefore=$beforePort; MaxWaitSeconds=$existingWaitSeconds"
    if (Wait-CodexCdpVersionReady -Port $remoteDebuggingPort -MaxWaitSeconds $existingWaitSeconds) {
        if (Test-CodexCdpBackedByElevatedProcess -Port $remoteDebuggingPort) {
            Write-StartLog "Existing Codex Desktop recovered during wait; startup skipped. RemoteDebuggingPort=$remoteDebuggingPort"
            Invoke-CodexDesktopModelRuntimeReconcile -ReloadImmediately $false -Context "Recovered Codex Desktop"
            Start-CodexStartupBaselineRepairAsync
            Write-PhaseLog "Baseline repair phase finished"
            Write-StartLog "Launcher finished without new Start-Process after existing CDP recovery; totalMs=$($scriptStopwatch.ElapsedMilliseconds)"
            exit 0
        }
        Write-StartLog "Existing Codex CDP recovered but is not backed by an elevated Codex process. RemoteDebuggingPort=$remoteDebuggingPort; PortBefore=$beforePort"
        exit 2
    }
    Write-StartLog "CDP port is still occupied and not healthy after wait; entering safe occupied-port recovery before deciding whether to block startup. RemoteDebuggingPort=$remoteDebuggingPort; PortBefore=$beforePort"
    $blockedPortRecovery = Ensure-CodexRestartBoundaryClean -RemoteDebuggingPort $remoteDebuggingPort
    Write-PhaseLog "Blocked port recovery phase finished"
    if (-not [bool]$blockedPortRecovery.Ok) {
        Write-StartLog "Codex Desktop startup blocked because the occupied CDP port could not be recovered safely. Action=$($blockedPortRecovery.Action); ProcessCount=$($blockedPortRecovery.ProcessCount); RemoteDebuggingPort=$remoteDebuggingPort; PortBefore=$beforePort"
        exit 4
    }
    if ($blockedPortRecovery.Action -eq "cdp_recovered") {
        if (Test-CodexCdpBackedByElevatedProcess -Port $remoteDebuggingPort) {
            Write-StartLog "Existing Codex Desktop recovered during occupied-port recovery and is elevated. RemoteDebuggingPort=$remoteDebuggingPort"
            Invoke-CodexDesktopModelRuntimeReconcile -ReloadImmediately $true -Context "Recovered Codex Desktop"
            Start-CodexStartupBaselineRepairAsync
            Write-PhaseLog "Baseline repair phase finished"
            Write-StartLog "Launcher finished after occupied-port CDP recovery; totalMs=$($scriptStopwatch.ElapsedMilliseconds)"
            exit 0
        }
        Write-StartLog "Codex Desktop startup blocked because recovered CDP is not backed by an elevated Codex process. RemoteDebuggingPort=$remoteDebuggingPort"
        exit 2
    }
    $beforePort = Get-PortOwnerSummary -Port $remoteDebuggingPort -Deep $deepDiag
    if ($beforePort -ne "free") {
        Write-StartLog "Codex Desktop startup blocked because the CDP port is still occupied after safe recovery. RemoteDebuggingPort=$remoteDebuggingPort; PortAfterRecovery=$beforePort"
        exit 3
    }
    Write-StartLog "Occupied CDP port recovered safely; continuing with fresh Codex Desktop launch. RemoteDebuggingPort=$remoteDebuggingPort"
}

$restartBoundary = Ensure-CodexRestartBoundaryClean -RemoteDebuggingPort $remoteDebuggingPort
Write-PhaseLog "Restart boundary phase finished"
if (-not [bool]$restartBoundary.Ok) {
    Write-StartLog "Codex Desktop startup blocked because stale Codex process cleanup did not complete. Action=$($restartBoundary.Action); ProcessCount=$($restartBoundary.ProcessCount)"
    exit 4
}
if ($restartBoundary.Action -eq "cdp_recovered") {
    if (Test-CodexCdpBackedByElevatedProcess -Port $remoteDebuggingPort) {
        Write-StartLog "Codex Desktop startup skipped because existing Codex recovered during restart-boundary wait and is elevated. RemoteDebuggingPort=$remoteDebuggingPort"
        Invoke-CodexDesktopModelRuntimeReconcile -ReloadImmediately $true -Context "Recovered Codex Desktop"
        Start-CodexStartupBaselineRepairAsync
        Write-PhaseLog "Baseline repair phase finished"
        Write-StartLog "Launcher finished after restart-boundary CDP recovery; totalMs=$($scriptStopwatch.ElapsedMilliseconds)"
        exit 0
    }
    Write-StartLog "Codex Desktop startup blocked because recovered CDP is not backed by an elevated Codex process. RemoteDebuggingPort=$remoteDebuggingPort"
    exit 2
}

$sessionStoreMaintenance = Invoke-CodexSessionStorePreLaunchMaintenance
Write-PhaseLog "Session-store pre-launch maintenance phase finished"
if (-not [bool]$sessionStoreMaintenance.Ok) {
    Write-StartLog "Codex Desktop startup continuing because session-store maintenance is best-effort. Error=$($sessionStoreMaintenance.Error)"
}

$finalConfigPreflight = Invoke-CodexConfigPreflightSync
Write-PhaseLog "Final config preflight after restart boundary phase finished"
if (-not [bool]$finalConfigPreflight.Ok) {
    if ($env:CODEX_STARTUP_PREFLIGHT_FAIL_CLOSED -eq "1") {
        Write-StartLog "Codex Desktop startup blocked because final config preflight failed and CODEX_STARTUP_PREFLIGHT_FAIL_CLOSED=1."
        exit 5
    }
    Write-StartLog "Codex Desktop startup continuing in degraded mode because final config preflight failed after restart boundary. MCP/plugin config or process-manager state may still be incomplete until the next repair."
}

$catalogReasoningPreflight = Invoke-CodexCatalogReasoningPreflight
Write-PhaseLog "Catalog reasoning preflight phase finished"
if (-not [bool]$catalogReasoningPreflight.Ok) {
    if ($env:CODEX_STARTUP_PREFLIGHT_FAIL_CLOSED -eq "1") {
        Write-StartLog "Codex Desktop startup blocked because catalog reasoning preflight failed and CODEX_STARTUP_PREFLIGHT_FAIL_CLOSED=1."
        exit 6
    }
    Write-StartLog "Codex Desktop startup continuing in degraded mode because catalog reasoning preflight failed. Model picker reasoning options may be incomplete until the catalog is repaired."
}

$githubMcpToken = Set-GitHubMcpBearerTokenFromVault
Write-PhaseLog "GitHub MCP token injection phase finished"
if (-not [bool]$githubMcpToken.Ok) {
    Write-StartLog "Codex Desktop startup continuing without GitHub MCP bearer token. Native GitHub MCP may return Auth required; Hub/gh fallback remains available."
}

Write-StartLog "Starting Codex Desktop. User=$($identity.Name); IsAdmin=$isAdmin; RemoteDebuggingPort=$remoteDebuggingPort; PortBefore=$beforePort; Exe=$codexExe; Args=$($arguments -join ' ')"
$startedProcess = $null
if ($dryRun) {
    Write-StartLog "Dry run enabled; Start-Process skipped."
} else {
    $startedProcess = Start-Process -FilePath $codexExe -ArgumentList $arguments -WorkingDirectory (Split-Path -Parent $codexExe) -PassThru
}
Write-PhaseLog "Start-Process phase finished"
$afterPort = Get-PortOwnerSummary -Port $remoteDebuggingPort -Deep $deepDiag
$startedPid = "dry-run"
$startedElevation = "Skipped(deep diagnostics disabled)"
if ($null -ne $startedProcess) {
    $startedPid = [string]$startedProcess.Id
    if ($deepDiag) {
        $startedElevation = Get-TokenElevation -ProcessId $startedProcess.Id
    }
}
$newProcessWaitSeconds = Get-EnvInt -Name "CODEX_NEW_CDP_READY_WAIT_SECONDS" -Default 45
$newVersionReady = $false
if (-not $dryRun) {
    $newVersionReady = Wait-CodexCdpVersionReady -Port $remoteDebuggingPort -MaxWaitSeconds $newProcessWaitSeconds
}
Write-StartLog "Start requested. StartedPid=$startedPid; StartedProcessElevation=$startedElevation; RemoteDebuggingPort=$remoteDebuggingPort; PortAfter=$afterPort; VersionReady=$newVersionReady; VersionWaitSeconds=$newProcessWaitSeconds"
if ($newVersionReady) {
    Invoke-CodexDesktopModelRuntimeReconcile -ReloadImmediately $true -Context "Codex Desktop startup"
} else {
    Write-StartLog "Codex Desktop Statsig allowlist sync skipped because CDP did not become ready within startup wait."
    Write-PhaseLog "Desktop Statsig allowlist sync skip phase finished"
}
Start-CodexStartupBaselineRepairAsync
Write-PhaseLog "Baseline repair phase finished"
Write-StartLog "Launcher finished; totalMs=$($scriptStopwatch.ElapsedMilliseconds)"
