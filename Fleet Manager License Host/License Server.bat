@echo off
cd /d "%~dp0"
title LICENCE SERVER - KEEP THIS WINDOW OPEN (Fleet Manager)
color 1F
:start
echo ======================================================================
echo   FLEET MANAGER LICENCE SERVER  -  KEEP THIS WINDOW OPEN
echo   Closing it stops buyers activating and License Manager working.
echo ======================================================================
python license_server.py %*
echo.
echo The licence server stopped. Restarting in 10 seconds...
echo (To stop it for good, close this window.)
timeout /t 10 >nul
goto start
