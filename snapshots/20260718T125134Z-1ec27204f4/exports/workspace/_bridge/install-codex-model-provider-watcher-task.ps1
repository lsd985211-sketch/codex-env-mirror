param(
  [string]$TaskName = "CodexModelProviderWatcher",
  [string]$Root = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager",
  [int]$RecoveryIntervalMinutes = 5,
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"
if ($RecoveryIntervalMinutes -lt 1) {
  throw "RecoveryIntervalMinutes must be at least 1."
}
$script = Join-Path $Root "_bridge\codex_model_provider_watcher.py"
if (-not (Test-Path -LiteralPath $script)) {
  throw "Watcher script not found: $script"
}

$bundledPythonw = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (Test-Path -LiteralPath $bundledPythonw) {
  $python = $bundledPythonw
} elseif (Test-Path -LiteralPath $bundledPython) {
  $python = $bundledPython
} else {
  $pythonCommand = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($null -eq $pythonCommand) {
    $pythonCommand = Get-Command python.exe -ErrorAction Stop
  }
  $python = $pythonCommand.Source
}

$argument = "`"$script`" supervise --poll-seconds 2 --debounce-seconds 1.5 --drift-check-seconds 10"
$action = New-ScheduledTaskAction -Execute $python -Argument $argument -WorkingDirectory $Root
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$recoveryTrigger = New-ScheduledTaskTrigger `
  -Once `
  -At ((Get-Date).AddMinutes(1)) `
  -RepetitionInterval (New-TimeSpan -Minutes $RecoveryIntervalMinutes) `
  -RepetitionDuration (New-TimeSpan -Days 3650)
$triggers = @($logonTrigger, $recoveryTrigger)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -RestartCount 10 `
  -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $triggers `
  -Principal $principal `
  -Settings $settings `
  -Description "Watch Codex provider/catalog changes and reconcile the Desktop model picker without modifying provider configuration." `
  -Force | Out-Null

if ($StartNow) {
  Start-ScheduledTask -TaskName $TaskName
}

[pscustomobject]@{
  ok = $true
  task_name = $TaskName
  run_level = "Limited"
  recovery_interval_minutes = $RecoveryIntervalMinutes
  multiple_instances = "IgnoreNew"
  root = $Root
  executable = $python
  action = $argument
  started = [bool]$StartNow
} | ConvertTo-Json -Depth 4
