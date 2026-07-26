$ErrorActionPreference = 'Stop'

$Root = 'C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager'
$Python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$Guard = Join-Path $Root '_bridge\mcp_launch_guard.py'
$Npx = 'C:\Program Files\nodejs\npx.cmd'
$AllowedRoots = @(
  'C:\'
)

& $Python $Guard --profile fs-admin --min-age-minutes 15 -- $Npx -y '@modelcontextprotocol/server-filesystem@2026.1.14' @AllowedRoots @args
exit $LASTEXITCODE
