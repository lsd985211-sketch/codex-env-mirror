param(
  [string]$TaskName = "MobileOpenClawBridgeWorker",
  [int]$IntervalSeconds = 1,
  [int]$Limit = 5,
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScript = Join-Path $Root "start_worker_hidden.py"
if (-not (Test-Path -LiteralPath $StartScript)) {
  throw "Missing start script: $StartScript"
}

$BundledPythonw = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if (Test-Path -LiteralPath $BundledPythonw) {
  $python = $BundledPythonw
} elseif (Get-Command pythonw.exe -ErrorAction SilentlyContinue) {
  $python = (Get-Command pythonw.exe -ErrorAction Stop).Source
} else {
  throw "pythonw.exe is required for the no-window worker task"
}
$argument = "`"$StartScript`" --interval-seconds $IntervalSeconds --limit $Limit"
$action = New-ScheduledTaskAction -Execute $python -Argument $argument -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Principal $principal `
  -Settings $settings `
  -Description "Poll OpenClaw Weixin mobile queue and deliver allowed tasks to Codex Desktop." `
  -Force | Out-Null

if ($StartNow) {
  Start-ScheduledTask -TaskName $TaskName
}

[pscustomobject]@{
  ok = $true
  task_name = $TaskName
  run_level = "Limited"
  start_now = [bool]$StartNow
  start_script = $StartScript
  execute = $python
  interval_seconds = $IntervalSeconds
  limit = $Limit
} | ConvertTo-Json -Depth 4
