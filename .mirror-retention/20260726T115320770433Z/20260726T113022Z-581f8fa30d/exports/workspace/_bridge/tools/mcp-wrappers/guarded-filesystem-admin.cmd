@echo off
setlocal
set "ROOT=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
set "PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "GUARD=%ROOT%\_bridge\mcp_launch_guard.py"
set "NPX=C:\Program Files\nodejs\npx.cmd"
"%PYTHON%" "%GUARD%" --profile fs-admin --min-age-minutes 15 -- "%NPX%" -y @modelcontextprotocol/server-filesystem@2026.1.14 C:\ %*
