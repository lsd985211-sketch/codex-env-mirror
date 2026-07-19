@echo off
setlocal
set "ROOT=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%ROOT%\_bridge\mcp_launch_guard.py" --profile mid --min-age-minutes 30 -- "C:\Python314\python.exe" -m uv tool run markitdown-mcp@0.0.1a4 %*
