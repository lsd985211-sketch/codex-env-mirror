@echo off
setlocal
set "ROOT=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
set "CODEGRAPH_NO_DAEMON=1"
set "CODEGRAPH_WATCH_DEBOUNCE_MS=2000"
set "CODEGRAPH_NODE=%ROOT%\_bridge\tools\codegraph\node_modules\@colbymchenry\codegraph-win32-x64\node.exe"
set "CODEGRAPH_ENTRY=%ROOT%\_bridge\tools\codegraph\node_modules\@colbymchenry\codegraph-win32-x64\lib\dist\bin\codegraph.js"
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%ROOT%\_bridge\mcp_launch_guard.py" --profile cg --min-age-minutes 15 -- "%CODEGRAPH_NODE%" --liftoff-only "%CODEGRAPH_ENTRY%" serve --mcp --path "%ROOT%" %*

