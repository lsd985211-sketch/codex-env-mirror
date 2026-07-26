@echo off
setlocal
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0..\..\mcp_launch_guard.py" --profile sqlite-bridge-ro --min-age-minutes 15 -- "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0..\..\sqlite_mcp_server.py" --db "%~dp0..\..\mobile_openclaw_bridge\mobile_openclaw_bridge.db" --permissions "list,read" --readonly %*
