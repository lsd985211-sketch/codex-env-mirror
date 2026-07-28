param(
  [string]$TaskName = "CodexConfigGuard",
  [string]$Root = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager",
  [int]$IntervalMinutes = 5,
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$script = Join-Path $Root "_bridge\codex_config_guard.py"

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
$argument = "`"$script`" run-once --apply"

$action = New-ScheduledTaskAction -Execute $python -Argument $argument -WorkingDirectory $Root
$repeatTrigger = New-ScheduledTaskTrigger `
  -Once `
  -At ((Get-Date).AddMinutes(1)) `
  -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
  -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $repeatTrigger `
  -Principal $principal `
  -Settings $settings `
  -Force | Out-Null

if ($StartNow) {
  Start-ScheduledTask -TaskName $TaskName
}

[pscustomobject]@{
  ok = $true
  task_name = $TaskName
  run_level = "Limited"
  interval_minutes = $IntervalMinutes
  launch_boundary_owner = "start-codex-desktop-elevated.ps1"
  logon_trigger = $false
  root = $Root
  executable = $python
  action = $argument
} | ConvertTo-Json -Depth 4
