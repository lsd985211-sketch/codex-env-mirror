$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunScript = Join-Path $ScriptDir "run-wecom-bridge.ps1"
$LogDir = Join-Path $ScriptDir "logs"
New-Item -Path $LogDir -ItemType Directory -Force | Out-Null

Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $RunScript) `
    -WorkingDirectory $ScriptDir `
    -WindowStyle Hidden

Write-Output "WeCom mobile bridge start requested."
