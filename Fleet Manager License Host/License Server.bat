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
title LICENCE SERVER - KEEP THIS WINDOW OPEN (Fleet Manager)
color 1F
:start
echo ======================================================================
echo   FLEET MANAGER LICENCE SERVER  -  KEEP THIS WINDOW OPEN
echo   Closing it stops buyers activating and License Manager working.
echo ======================================================================
%PY% license_server.py %*
echo.
echo The licence server stopped. Restarting in 10 seconds...
echo (To stop it for good, close this window.)
timeout /t 10 >nul
goto start
