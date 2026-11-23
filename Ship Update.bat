@echo off
setlocal EnableExtensions
title Fleet Manager Pro - Ship Update
cd /d "%~dp0"

echo ============================================================
echo   Fleet Manager Pro - ship this version to EVERYONE
echo ============================================================
echo.
for /f "usebackq delims=" %%V in ("Fleet Manager Pro\VERSION") do set "VER=%%V"
echo  Version to ship:  %VER%
echo.
echo  This builds Fleet Manager Pro once, publishes it as an update for
echo  everyone, makes the installer and packs the zip for the cloud server.
echo  It takes about 10 minutes.
echo.
echo  BEFORE you run this: bump  Fleet Manager Pro\VERSION  and run the tests,
echo  otherwise nobody is offered the update.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo  ! Python isn't installed or isn't on PATH. Install Python, then try again.
    goto :end
)

set "NOTES="
set /p "NOTES=What's new (one line, shown to everyone): "
echo.
python "Ship Update.py" --notes "%NOTES%"
if not errorlevel 1 start "" explorer "%~dp0..\Build Output"

:end
echo.
pause
endlocal
