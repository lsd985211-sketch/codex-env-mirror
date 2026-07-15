param(
  [string]$TaskName = "CodexModelProviderWatcher",
  [string]$Root = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager",
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"
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

$argument = "`"$script`" watch --poll-seconds 2 --debounce-seconds 1.5"
$action = New-ScheduledTaskAction -Execute $python -Argument $argument -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
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
  -Trigger $trigger `
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
  root = $Root
  executable = $python
  action = $argument
  started = [bool]$StartNow
} | ConvertTo-Json -Depth 4
