param(
  [string]$TaskName = "CodexSchedulerRunner",
  [string]$Distribution = "Codex-Wsl-Lab",
  [string]$LinuxUser = "codexlab",
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$wsl = "$env:SystemRoot\System32\wsl.exe"
if (-not (Test-Path -LiteralPath $wsl)) {
  throw "Missing WSL executable: $wsl"
}

# The retired task detached its PowerShell child from Task Scheduler. Stop only
# that fixed legacy runner before replacing the task definition so the WSL
# service can acquire the existing cross-platform scheduler lock.
$legacyLauncherMarker = "run-codex-scheduler.ps1"
$legacyRunnerMarker = "codex_scheduler_runner.py loop"
$legacyProcessNames = @("powershell.exe", "python.exe", "pythonw.exe", "wscript.exe")
Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -and
    $legacyProcessNames.Contains($_.Name.ToLowerInvariant()) -and
    ($_.CommandLine.Contains($legacyLauncherMarker) -or $_.CommandLine.Contains($legacyRunnerMarker))
  } |
  ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }

$services = @(
  "codex-app-server.service",
  "codex-local-mcp-hub.service",
  "codex-pmb-memory.service",
  "codex-maintenance-scheduler.service"
)
$serviceText = $services -join " "
$argument = "-d `"$Distribution`" -u `"$LinuxUser`" -- systemctl --user start $serviceText"
$action = New-ScheduledTaskAction -Execute $wsl -Argument $argument -WorkingDirectory "$env:SystemRoot\System32"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Principal $principal `
  -Settings $settings `
  -Description "One-shot login wake for the WSL Codex control plane; maintenance scheduling remains owned by WSL user-systemd." `
  -Force | Out-Null

if ($StartNow) {
  Start-ScheduledTask -TaskName $TaskName
}

[pscustomobject]@{
  ok = $true
  task_name = $TaskName
  mode = "wsl_control_plane_login_wake"
  start_now = [bool]$StartNow
  distribution = $Distribution
  linux_user = $LinuxUser
  services = $services
  resident_windows_scheduler = $false
} | ConvertTo-Json -Depth 4
