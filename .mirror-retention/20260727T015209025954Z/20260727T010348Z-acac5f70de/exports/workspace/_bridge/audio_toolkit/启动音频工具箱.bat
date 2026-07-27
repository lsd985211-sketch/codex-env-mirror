@echo off
setlocal
cd /d "%~dp0..\.."
set "PYTHONW=C:\Python314\pythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=pythonw.exe"
start "" "%PYTHONW%" "%CD%\_bridge\audio_toolkit\audio_toolkit_gui.py" %*
