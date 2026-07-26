@echo off
setlocal
set "ROOT=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
set "CODEX_LIB=C:\Users\45543\Desktop\CODEX~1"
set "PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "GUARD=%ROOT%\_bridge\mcp_launch_guard.py"
set "NPX=C:\Program Files\nodejs\npx.cmd"
"%PYTHON%" "%GUARD%" --profile fs --min-age-minutes 15 -- "%NPX%" -y @modelcontextprotocol/server-filesystem@2026.1.14 "%ROOT%" "%CODEX_LIB%" "%USERPROFILE%\.codex\skills" "%USERPROFILE%\.codex\memories" "%USERPROFILE%\.codex\plugins" %*
