param(
  [string]$TaskName = "MobileOpenClawDashboardStack",
  [string]$HostName = "127.0.0.1",
  [int]$Port = 18808,
  [int]$AppServerPort = 18791,
  [int]$LoginPort = 18790,
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScript = Join-Path $Root "start-dashboard-stack-hidden.ps1"
if (-not (Test-Path -LiteralPath $StartScript)) {
  throw "Missing dashboard stack start script: $StartScript"
}

$wscript = "$env:SystemRoot\System32\wscript.exe"
$launcher = Join-Path $Root "..\shared\run-hidden.vbs"
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$argument = "//B //Nologo `"$launcher`" `"$powershell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`" -HostName $HostName -Port $Port -AppServerPort $AppServerPort -LoginPort $LoginPort"
$action = New-ScheduledTaskAction -Execute $wscript -Argument $argument -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
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
  -Description "Start the Weixin bridge dashboard stack: Codex app-server, login service, dashboard, and live watcher." `
  -Force | Out-Null

if ($StartNow) {
  Start-ScheduledTask -TaskName $TaskName
}

[pscustomobject]@{
  ok = $true
  task_name = $TaskName
  start_now = [bool]$StartNow
  start_script = $StartScript
  dashboard_port = $Port
  app_server_port = $AppServerPort
  login_port = $LoginPort
} | ConvertTo-Json -Depth 4
