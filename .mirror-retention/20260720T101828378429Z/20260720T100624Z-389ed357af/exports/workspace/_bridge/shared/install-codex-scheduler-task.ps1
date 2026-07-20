param(
  [string]$TaskName = "CodexSchedulerRunner",
  [int]$IntervalSeconds = 300,
  [switch]$DryRun,
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScript = Join-Path $Root "run-codex-scheduler.ps1"
if (-not (Test-Path -LiteralPath $StartScript)) {
  throw "Missing scheduler launcher: $StartScript"
}

$wscript = "$env:SystemRoot\System32\wscript.exe"
$launcher = Join-Path $Root "run-hidden.vbs"
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$dryRunArg = ""
if ($DryRun) {
  $dryRunArg = " -DryRun"
}
$argument = "//B //Nologo `"$launcher`" `"$powershell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`" -IntervalSeconds $IntervalSeconds$dryRunArg"
$action = New-ScheduledTaskAction -Execute $wscript -Argument $argument -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Principal $principal `
  -Settings $settings `
  -Description "Unified Codex scheduler runner for desktop resource-library automation and maintenance tasks." `
  -Force | Out-Null

if ($StartNow) {
  Start-ScheduledTask -TaskName $TaskName
}

[pscustomobject]@{
  ok = $true
  task_name = $TaskName
  start_now = [bool]$StartNow
  dry_run = [bool]$DryRun
  start_script = $StartScript
  interval_seconds = $IntervalSeconds
} | ConvertTo-Json -Depth 4
