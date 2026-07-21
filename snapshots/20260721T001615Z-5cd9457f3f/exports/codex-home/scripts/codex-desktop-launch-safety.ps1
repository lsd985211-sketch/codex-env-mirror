# Ownership: serialization and read-only Codex Desktop process census for the governed launcher.
# Non-goals: launching or stopping Codex, probing CDP, changing configuration, or selecting recovery policy.
# State behavior: holds an in-process named mutex or exclusive file-lock fallback; process census is read-only.
# Caller context: dot-sourced by start-codex-desktop-elevated.ps1 before startup decisions.

$script:CodexLauncherSingletonReason = "not_attempted"
$script:CodexProcessScanLastOk = $false

function Enter-CodexLauncherSingleton {
    $mutexName = "Local\CodexDesktopGovernedLauncher"
    try {
        $createdNew = $false
        $mutex = New-Object System.Threading.Mutex($true, $mutexName, ([ref]$createdNew))
        if (-not $createdNew) {
            try {
                if (-not $mutex.WaitOne(0)) {
                    $mutex.Dispose()
                    $script:CodexLauncherSingletonReason = "named_mutex_busy"
                    return $false
                }
            } catch [System.Threading.AbandonedMutexException] {
                Write-StartLog "Recovered abandoned governed launcher mutex: $mutexName"
            }
        }
        $script:CodexLauncherMutex = $mutex
        $script:CodexLauncherSingletonReason = "named_mutex_acquired"
        return $true
    } catch {
        Write-StartLog "Governed launcher mutex unavailable; trying exclusive file-lock fallback. Error=$($_.Exception.Message)"
    }

    $fallbackPath = Join-Path $stateDir "codex-desktop-governed-launcher.lock"
    try {
        $script:CodexLauncherFileLock = [System.IO.File]::Open(
            $fallbackPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $script:CodexLauncherSingletonReason = "file_lock_fallback_acquired"
        Write-StartLog "Governed launcher acquired exclusive file-lock fallback: $fallbackPath"
        return $true
    } catch {
        $script:CodexLauncherSingletonReason = "serialization_unavailable"
        Write-StartLog "Governed launcher serialization unavailable; refusing an unprotected launch. File=$fallbackPath; Error=$($_.Exception.Message)"
        return $false
    }
}

function Get-CodexLauncherSingletonReason {
    return [string]$script:CodexLauncherSingletonReason
}

function Get-CodexDesktopProcessRecords {
    $records = @()
    $script:CodexProcessScanLastOk = $false
    try {
        $processes = Get-CimInstance -Query "SELECT ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine, CreationDate FROM Win32_Process WHERE Name='ChatGPT.exe' OR Name='Codex.exe' OR Name='codex.exe'" -ErrorAction Stop |
            Where-Object {
                $path = [string]$_.ExecutablePath
                $cmd = [string]$_.CommandLine
                $isCodexDesktopFamily =
                    ($path -match "\\OpenAI\.Codex_.*\\app\\(ChatGPT|Codex|resources\\codex)\.exe$") -or
                    ($cmd -match "\\OpenAI\.Codex_.*\\app\\(ChatGPT|Codex|resources\\codex)\.exe")
                $isBridgeAppServer =
                    ($cmd -match "\bapp-server\b") -and
                    ($cmd -match "\blisten\b") -and
                    ($cmd -match "127\.0\.0\.1:18791")
                $isCodexDesktopFamily -and -not $isBridgeAppServer
            }
        foreach ($process in $processes) {
            $records += [pscustomobject]@{
                ProcessId = [int]$process.ProcessId
                ParentProcessId = [int]$process.ParentProcessId
                Name = [string]$process.Name
                ExecutablePath = [string]$process.ExecutablePath
                CommandLine = [string]$process.CommandLine
                CreationDate = $process.CreationDate
            }
        }
        $script:CodexProcessScanLastOk = $true
    } catch {
        Write-StartLog "Codex process scan failed and cannot be treated as an empty process set: $($_.Exception.Message)"
    }
    return $records
}

function Test-CodexProcessScanReliable {
    return [bool]$script:CodexProcessScanLastOk
}

function Get-CodexProcessSummary {
    param([object[]]$Processes)
    if (-not $Processes -or $Processes.Count -eq 0) {
        return "none"
    }
    $parts = @()
    foreach ($process in $Processes) {
        $parts += "pid=$($process.ProcessId) name=$($process.Name) ppid=$($process.ParentProcessId)"
    }
    return ($parts -join "; ")
}
