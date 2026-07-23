@echo off
setlocal
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0..\..\mcp_launch_guard.py" --profile sqlite-scratch --min-age-minutes 15 -- "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0..\..\sqlite_mcp_server.py" --db "%~dp0..\..\data\sqlite\codex_scratch.sqlite" --permissions "list,read,create,update,delete,ddl,transaction,utility" %*
