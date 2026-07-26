$ErrorActionPreference = "Stop"

$packageHelper = Join-Path $env:USERPROFILE ".codex\scripts\codex-desktop-package.ps1"
if (-not (Test-Path -LiteralPath $packageHelper)) {
    throw "Codex Desktop package helper was not found: $packageHelper"
}
. $packageHelper

$logDir = Join-Path $env:USERPROFILE ".codex\logs"
$log = Join-Path $logDir "codex-elevated-start.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-LaunchLog {
    param([string] $Message)
    $stamp = (Get-Date).ToUniversalTime().ToString("o")
    Add-Content -LiteralPath $log -Value "[$stamp] $Message" -Encoding UTF8
}

try {
    $exe = Resolve-CurrentCodexDesktopExe
    if ([string]::IsNullOrWhiteSpace($exe)) {
        Write-LaunchLog "OpenAI Codex Desktop executable was not found"
        exit 3
    }

    Write-LaunchLog "starting Codex Desktop: $exe"
    Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe)
    exit 0
}
catch {
    Write-LaunchLog ("launch failed: " + $_.Exception.Message)
    exit 9
}
