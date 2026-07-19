$ErrorActionPreference = 'Stop'

$Root = 'C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager'
$Python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$Guard = Join-Path $Root '_bridge\mcp_launch_guard.py'
$Server = Join-Path $Root '_bridge\sqlite_mcp_server.py'
$Db = Join-Path $Root '_bridge\data\sqlite\codex_scratch.sqlite'
$Permissions = 'list,read,create,update,delete,ddl,transaction,utility'

& $Python $Guard --profile sqlite-scratch --min-age-minutes 15 -- $Python $Server --db $Db --permissions $Permissions @args
exit $LASTEXITCODE
