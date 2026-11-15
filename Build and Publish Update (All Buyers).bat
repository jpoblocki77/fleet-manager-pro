@echo off
setlocal EnableExtensions
title Fleet Manager Pro - Build and Publish Update to ALL buyers
cd /d "%~dp0"

echo ============================================================
echo   Fleet Manager Pro - ship this version to EVERY buyer
echo ============================================================
echo.
for /f "usebackq delims=" %%V in ("Fleet Manager Pro\VERSION") do set "VER=%%V"
echo  This rebuilds every buyer's copy at version  %VER%  and publishes
echo  each one to the licence server, so every buyer sees "Update available".
echo.
echo  BEFORE you run this:
echo    1. Bump  Fleet Manager Pro\VERSION  (e.g. 1.3 -^> 1.4) - otherwise the
echo       server won't treat the rebuild as newer and nobody gets the update.
echo    2. Make sure the licence server (license_server.py) is running.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo  ! Python isn't installed or isn't on PATH. Install Python, then try again.
    goto :end
)

set /p "NOTES=Release notes for buyers (optional, one line): "
echo.
if defined NOTES (
    python "Build and Publish Update (All Buyers).py" --notes "%NOTES%"
) else (
    python "Build and Publish Update (All Buyers).py"
)

:end
echo.
pause
endlocal
