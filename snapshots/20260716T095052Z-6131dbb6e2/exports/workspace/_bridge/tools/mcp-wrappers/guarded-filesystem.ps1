$ErrorActionPreference = "Stop"

$Root = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
$Python = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Guard = Join-Path $Root "_bridge\mcp_launch_guard.py"
$Npx = "C:\Program Files\nodejs\npx.cmd"
$AllowedRoots = @(
  $Root,
  (Join-Path $env:USERPROFILE "Desktop\CODEX~1"),
  (Join-Path $env:USERPROFILE ".codex\skills"),
  (Join-Path $env:USERPROFILE ".codex\memories"),
  (Join-Path $env:USERPROFILE ".codex\plugins")
)

$LaunchArgs = @(
  $Guard,
  "--profile",
  "fs",
  "--min-age-minutes",
  "15",
  "--",
  $Npx,
  "-y",
  "@modelcontextprotocol/server-filesystem@2026.1.14"
)
$LaunchArgs += $AllowedRoots
$LaunchArgs += $args

& $Python @LaunchArgs
exit $LASTEXITCODE
