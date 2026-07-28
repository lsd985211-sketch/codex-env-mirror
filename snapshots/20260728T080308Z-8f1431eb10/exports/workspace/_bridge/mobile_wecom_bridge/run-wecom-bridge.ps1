$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Config = Join-Path $ScriptDir "config.local.json"
$Example = Join-Path $ScriptDir "config.example.json"

if (-not (Test-Path -LiteralPath $Config)) {
    Copy-Item -LiteralPath $Example -Destination $Config
    Write-Output "Created config.local.json from config.example.json. Fill environment variables before real WeCom use."
}

$Python = "python"
$Args = @(
    (Join-Path $ScriptDir "wecom_bridge_server.py"),
    "--config",
    $Config
)

Write-Output "Starting WeCom mobile bridge with config: $Config"
& $Python @Args
