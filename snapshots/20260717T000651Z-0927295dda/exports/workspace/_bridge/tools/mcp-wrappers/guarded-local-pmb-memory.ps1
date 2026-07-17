$ErrorActionPreference = 'Stop'
$Root = 'C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager'
$env:PMB_HOME = 'C:\Users\45543\Desktop\Codex资源库\memory\pmb\data'
$env:PMB_WORKSPACE = 'mcsmanager'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

$Python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$Guard = Join-Path $Root '_bridge\mcp_launch_guard.py'
$Pmb = Join-Path $Root '_bridge\venvs\pmb-memory\Scripts\pmb.exe'

# Codex is a stdio MCP host, so use PMB's lightweight stdio proxy. Keep daemon
# lifecycle in the local governance script; PMB proxy autostart can race during
# warm-up and create duplicate daemons. --no-fallback prevents hidden heavy
# in-process servers when the daemon is unhealthy.
& $Python (Join-Path $Root '_bridge\local_pmb_memory.py') daemon-ensure 1>$null
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
& $Python $Guard --profile pmb --min-age-minutes 15 -- $Pmb mcp proxy --no-autostart --no-fallback @args
exit $LASTEXITCODE
