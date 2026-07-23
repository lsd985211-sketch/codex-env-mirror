@echo off
REM === Minecraft Fabric Client Launcher (Batch Wrapper) ===
REM Usage: launch-mc.bat [-saveName "WorldName"] [-server host:port] [-username PlayerName] [--menu] [-ram 4G]

setlocal enabledelayedexpansion

set INSTDIR=C:\Users\45543\Downloads\HMCL-3.9.1\.minecraft\versions\3c3u
set JAVA=C:\Program Files\BellSoft\LibericaJDK-25\bin\javaw.exe
set MCROOT=C:\Users\45543\Downloads\HMCL-3.9.1\.minecraft
set SCRIPT=%~dp0launch-mc.ps1

set MODE=menu
set SAVENAME=
set SERVER=
set USERNAME=
set RAM=4G

:parse
if "%~1"=="" goto done
if /I "%~1"=="-saveName" (
    set MODE=single
    set SAVENAME=%~2
    shift
) else if /I "%~1"=="-server" (
    set MODE=multi
    set SERVER=%~2
    shift
) else if /I "%~1"=="-username" (
    set USERNAME=%~2
    shift
) else if /I "%~1"=="--menu" (
    set MODE=menu
) else if /I "%~1"=="-ram" (
    set RAM=%~2
    shift
)
shift
goto parse
:done

if "%MODE%"=="single" (
    powershell -ExecutionPolicy Bypass -File "%SCRIPT%" -instanceDir "%INSTDIR%" -javaPath "%JAVA%" -minecraftDir "%MCROOT%" -saveName "%SAVENAME%" -ram "%RAM%"
) else if "%MODE%"=="multi" (
    powershell -ExecutionPolicy Bypass -File "%SCRIPT%" -instanceDir "%INSTDIR%" -javaPath "%JAVA%" -minecraftDir "%MCROOT%" -server "%SERVER%" -username "%USERNAME%" -ram "%RAM%"
) else (
    powershell -ExecutionPolicy Bypass -File "%SCRIPT%" -instanceDir "%INSTDIR%" -javaPath "%JAVA%" -minecraftDir "%MCROOT%" -ram "%RAM%"
)
