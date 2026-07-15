@echo off
setlocal
set "ROOT=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
set "CODEX_LIB=C:\Users\45543\Desktop\CODEX~1"
set "PMB_HOME=%CODEX_LIB%\memory\pmb\data"
set "PMB_WORKSPACE=mcsmanager"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "GUARD=%ROOT%\_bridge\mcp_launch_guard.py"
set "PMB=%ROOT%\_bridge\venvs\pmb-memory\Scripts\pmb.exe"
"%PYTHON%" "%ROOT%\_bridge\local_pmb_memory.py" daemon-ensure >nul
if not %ERRORLEVEL% EQU 0 exit /b %ERRORLEVEL%
"%PYTHON%" "%GUARD%" --profile pmb --min-age-minutes 15 -- "%PMB%" mcp proxy --no-autostart --no-fallback %*
