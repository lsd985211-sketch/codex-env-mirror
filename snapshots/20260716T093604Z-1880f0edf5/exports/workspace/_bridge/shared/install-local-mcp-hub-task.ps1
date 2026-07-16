param(
  [string]$TaskName = "CodexLocalMcpHub",
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 18881,
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$StartScript = Join-Path $Root "run-local-mcp-hub.ps1"
if (-not (Test-Path -LiteralPath $StartScript)) {
  throw "Missing local MCP hub launcher: $StartScript"
}

$wscript = "$env:SystemRoot\System32\wscript.exe"
$launcher = Join-Path $Root "run-hidden.vbs"
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$argument = "//B //Nologo `"$launcher`" `"$powershell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`" -HostAddress $HostAddress -Port $Port"
$action = New-ScheduledTaskAction -Execute $wscript -Argument $argument -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
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
  -Description "Starts the local stateless HTTP MCP hub for stable low-risk Codex tools." `
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
  url = "http://$HostAddress`:$Port/mcp"
} | ConvertTo-Json -Depth 4
