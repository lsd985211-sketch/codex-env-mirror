@echo off
setlocal
set "ROOT=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
set "PATH=C:\Program Files\nodejs;%PATH%"
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%ROOT%\_bridge\mcp_launch_guard.py" --profile pw --min-age-minutes 30 -- "C:\Program Files\nodejs\npx.cmd" --registry https://registry.npmjs.org @playwright/mcp@latest %*
