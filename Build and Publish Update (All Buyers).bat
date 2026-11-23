@echo off
setlocal EnableExtensions
title Fleet Manager Pro - Build and Publish Update to ALL buyers
cd /d "%~dp0"

echo ============================================================
echo   Fleet Manager Pro - ship this version to EVERYONE
echo ============================================================
echo.
for /f "usebackq delims=" %%V in ("Fleet Manager Pro\VERSION") do set "VER=%%V"
echo  This builds the public copy at version  %VER%  once and publishes it
echo  to the Fleet Manager server for everyone (and every older buyer copy),
echo  so every copy sees "Update available".
echo.
echo  BEFORE you run this:
echo    1. Bump  Fleet Manager Pro\VERSION  (e.g. 1.3 -^> 1.4) - otherwise the
echo       server won't treat the rebuild as newer and nobody gets the update.
echo    2. Make sure the Fleet Manager server (license_server.py) is running.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo  ! Python isn't installed or isn't on PATH. Install Python, then try again.
    goto :end
)

set /p "NOTES=Release notes (optional, one line): "
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
