@echo off
setlocal
set "ROOT=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
set "PATH=C:\Program Files\nodejs;%PATH%"
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%ROOT%\_bridge\mcp_launch_guard.py" --profile cdev --min-age-minutes 15 -- "C:\Program Files\nodejs\npx.cmd" --registry https://registry.npmjs.org chrome-devtools-mcp@1.4.0 %*

