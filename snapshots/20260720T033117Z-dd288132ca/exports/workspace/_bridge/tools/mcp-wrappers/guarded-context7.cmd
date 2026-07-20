@echo off
setlocal
set "ROOT=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
set "PATH=C:\Program Files\nodejs;%PATH%"
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%ROOT%\_bridge\mcp_launch_guard.py" --profile ctx7 --min-age-minutes 30 -- "C:\Program Files\nodejs\node.exe" "%ROOT%\_bridge\tools\mcp-wrappers\context7_stdio_proxy.js" %*
