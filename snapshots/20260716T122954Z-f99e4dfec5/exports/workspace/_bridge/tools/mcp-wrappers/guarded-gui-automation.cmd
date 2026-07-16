@echo off
setlocal
set "ROOT=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%ROOT%\_bridge\mcp_launch_guard.py" --profile gui --min-age-minutes 15 -- "C:\Python314\python.exe" "%ROOT%\_bridge\gui_automation_mcp.py" %*

