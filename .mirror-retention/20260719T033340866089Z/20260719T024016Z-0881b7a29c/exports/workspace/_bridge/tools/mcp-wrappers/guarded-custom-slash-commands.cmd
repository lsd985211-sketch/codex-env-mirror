@echo off
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0..\..\mcp_launch_guard.py" --profile slash --min-age-minutes 15 -- "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0..\..\custom_slash_commands_mcp.py" --registry "%~dp0..\..\slash_commands\commands.json" %*
