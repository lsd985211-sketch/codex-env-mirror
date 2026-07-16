Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$packageHelperPath = Join-Path $env:USERPROFILE ".codex\scripts\codex-desktop-package.ps1"
if (-not (Test-Path -LiteralPath $packageHelperPath)) {
    throw "Codex Desktop package helper was not found: $packageHelperPath"
}
. $packageHelperPath

$logDir = Join-Path $env:USERPROFILE ".codex\logs"
$logPath = Join-Path $logDir "codex-admin-shortcut-repair.log"
$stateDir = Join-Path $env:USERPROFILE ".codex\state"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

function Write-RepairLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    [System.IO.File]::AppendAllText($logPath, "[$timestamp] $Message`r`n", [System.Text.Encoding]::UTF8)
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
        Write-RepairLog "Codex executable cache read failed: $($_.Exception.Message)"
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
        Write-RepairLog "Codex executable cache write failed: $($_.Exception.Message)"
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
        Write-RepairLog "Using current cached Codex Desktop executable: $cached"
        return $cached
    }
    if (-not [string]::IsNullOrWhiteSpace($cached) -and $cached -ne $latest) {
        Write-RepairLog "Refreshing stale Codex executable cache. Cached=$cached; Latest=$latest"
    } else {
        Write-RepairLog "Writing Codex executable cache: $latest"
    }
    Set-CachedCodexDesktopExe -Path $latest
    return $latest
}

function Set-ShortcutRunAs {
    param([string]$Path)
    [byte[]]$bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 22) {
        throw "Shortcut is too small or invalid: $Path"
    }
    $bytes[21] = $bytes[21] -bor 0x20
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function Test-ShortcutRunAs {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    [byte[]]$bytes = [System.IO.File]::ReadAllBytes($Path)
    return $bytes.Length -gt 21 -and (($bytes[21] -band 0x20) -ne 0)
}

function Ensure-DesktopAdminShortcut {
    param(
        [string]$ShortcutPath,
        [string]$StartScript,
        [string]$CodexExe
    )

    $runHidden = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\shared\run-hidden.vbs"
    if (-not (Test-Path -LiteralPath $runHidden)) {
        throw "Hidden launcher wrapper was not found: $runHidden"
    }
    $target = "$env:SystemRoot\System32\wscript.exe"
    $powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = "//B //Nologo `"$runHidden`" `"$powershell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""
    $workingDirectory = Split-Path -Parent $StartScript
    $icon = "$CodexExe,0"
    $needsRepair = $true

    if (Test-Path -LiteralPath $ShortcutPath) {
        $ws = New-Object -ComObject WScript.Shell
        $existing = $ws.CreateShortcut($ShortcutPath)
        $needsRepair = $existing.TargetPath -ne $target -or
            $existing.Arguments -ne $arguments -or
            $existing.WorkingDirectory -ne $workingDirectory -or
            $existing.IconLocation -ne $icon -or
            $existing.WindowStyle -ne 7 -or
            -not (Test-ShortcutRunAs -Path $ShortcutPath)
    }

    if ($needsRepair) {
        $ws = New-Object -ComObject WScript.Shell
        $shortcut = $ws.CreateShortcut($ShortcutPath)
        $shortcut.TargetPath = $target
        $shortcut.Arguments = $arguments
        $shortcut.WorkingDirectory = $workingDirectory
        $shortcut.IconLocation = $icon
        $shortcut.WindowStyle = 7
        $shortcut.Description = "Start Codex Desktop through the stable elevated launcher."
        $shortcut.Save()
        Set-ShortcutRunAs -Path $ShortcutPath
        Write-RepairLog "Repaired desktop admin shortcut: $ShortcutPath"
    }
}

function Disable-LegacyStartupTaskShortcut {
    param(
        [string]$ShortcutPath
    )

    if (-not (Test-Path -LiteralPath $ShortcutPath)) {
        Write-RepairLog "Legacy startup task shortcut already absent: $ShortcutPath"
        return $false
    }

    $archiveRoot = Join-Path $env:USERPROFILE ".codex\disabled-startup-shortcuts"
    New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $target = Join-Path $archiveRoot ("Codex Elevated.$stamp.lnk")
    Move-Item -LiteralPath $ShortcutPath -Destination $target -Force
    Write-RepairLog "Disabled legacy startup task shortcut: $ShortcutPath -> $target"
    return $true
}

function Test-ScheduledTaskLogonTrigger {
    param([string]$TaskName)
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        foreach ($trigger in $task.Triggers) {
            if ($trigger.CimClass.CimClassName -eq "MSFT_TaskLogonTrigger" -and $trigger.Enabled) {
                return $true
            }
        }
    } catch {
        Write-RepairLog "Scheduled task check failed for $TaskName`: $($_.Exception.Message)"
    }
    return $false
}

function Ensure-CodexRunAsAdminLayers {
    param([string]$CodexExe)

    $checked = 0
    $written = 0
    $targets = New-Object System.Collections.Generic.List[string]
    $targets.Add($CodexExe)
    $resourceExe = Join-Path (Split-Path -Parent $CodexExe) "resources\codex.exe"
    if (Test-Path -LiteralPath $resourceExe) {
        $targets.Add($resourceExe)
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
        $checked += 1
        $current = $null
        if ($null -ne $props -and $null -ne $props.PSObject.Properties[$target]) {
            $current = $props.PSObject.Properties[$target].Value
        }
        if ($current -eq "~ RUNASADMIN") {
            continue
        }
        New-ItemProperty -LiteralPath $layerPath -Name $target -Value "~ RUNASADMIN" -PropertyType String -Force | Out-Null
        $written += 1
    }
    Write-RepairLog "RUNASADMIN layer check completed. Checked=$checked; Written=$written"
}

function Ensure-CodexDesktopScheduledTaskHiddenWrapper {
    param([string]$StartScript)
    $taskName = "CodexDesktopElevatedAtLogon"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-RepairLog "Scheduled task not found; hidden wrapper check skipped: $taskName"
        return $false
    }

    $runHidden = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\shared\run-hidden.vbs"
    if (-not (Test-Path -LiteralPath $runHidden)) {
        Write-RepairLog "Hidden wrapper missing; scheduled task check skipped: $runHidden"
        return $false
    }

    $execute = "$env:SystemRoot\System32\wscript.exe"
    $arguments = "//B //Nologo `"$runHidden`" `"$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""
    $currentAction = $task.Actions | Select-Object -First 1
    $alreadyHidden = $currentAction.Execute -eq $execute -and $currentAction.Arguments -eq $arguments
    if ($alreadyHidden) {
        Write-RepairLog "Scheduled task already uses hidden wrapper: $taskName"
        return $true
    }

    $action = New-ScheduledTaskAction -Execute $execute -Argument $arguments
    Set-ScheduledTask -TaskName $taskName -Action $action | Out-Null
    Write-RepairLog "Scheduled task updated to hidden wrapper: $taskName"
    return $true
}

$startScript = Join-Path $env:USERPROFILE ".codex\scripts\start-codex-desktop-elevated.ps1"
if (-not (Test-Path -LiteralPath $startScript)) {
    throw "Start script missing: $startScript"
}

$codexExe = Get-CodexDesktopExe
Ensure-CodexRunAsAdminLayers -CodexExe $codexExe
Ensure-CodexDesktopScheduledTaskHiddenWrapper -StartScript $startScript | Out-Null
Ensure-DesktopAdminShortcut -ShortcutPath (Join-Path ([Environment]::GetFolderPath("Desktop")) "Codex Current Admin.lnk") -StartScript $startScript -CodexExe $codexExe
Ensure-DesktopAdminShortcut -ShortcutPath (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\Codex.lnk") -StartScript $startScript -CodexExe $codexExe
$startupShortcutPath = Join-Path ([Environment]::GetFolderPath("Startup")) "Codex Elevated.lnk"
$startupShortcutDisabled = $false
if (Test-ScheduledTaskLogonTrigger -TaskName "CodexDesktopElevatedAtLogon") {
    $startupShortcutDisabled = Disable-LegacyStartupTaskShortcut -ShortcutPath $startupShortcutPath
} else {
    Write-RepairLog "Startup shortcut left unchanged because CodexDesktopElevatedAtLogon has no enabled logon trigger."
}

[pscustomobject]@{
    Ok = $true
    CodexExe = $codexExe
    DesktopShortcutRunAs = Test-ShortcutRunAs -Path (Join-Path ([Environment]::GetFolderPath("Desktop")) "Codex Current Admin.lnk")
    StartMenuShortcutRunAs = Test-ShortcutRunAs -Path (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\Codex.lnk")
    StartupShortcut = $startupShortcutPath
    StartupShortcutDisabled = $startupShortcutDisabled
    Log = $logPath
}
