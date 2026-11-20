@echo off
cd /d "%~dp0"
rem Find Python: "python", else the "py" launcher. Windows' Microsoft Store shortcut also answers to "python"
rem but only prints "Python was not found", so test that it really runs.
set "PY="
python -c "" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "" >nul 2>nul && set "PY=py -3"
if not defined PY (
    echo  ! Python isn't installed on this PC.
    echo    Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
    echo    then run this again.
    pause
    exit /b 1
)
title Fleet Manager - Prepare Kamatera upload
%PY% prepare_upload.py
pause
