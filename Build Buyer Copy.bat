@echo off
setlocal EnableExtensions
title Fleet Manager Pro - Build the public copy
cd /d "%~dp0"

echo ============================================================
echo   Fleet Manager Pro - build the copy everyone downloads
echo ============================================================
echo.
echo  Fleet Manager Pro is free: there is one copy for everyone, with no
echo  licence key. This builds it (Build Output\For Everyone and its .zip),
echo  then makes the installer (Build Output\Installers\Public).
echo.
echo  To ship it to everyone as an update, use
echo  "Build and Publish Update (All Buyers).bat" instead.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo  ! Python isn't installed or isn't on PATH. Install Python, then try again.
    goto :end
)

echo  This takes a few minutes.
set "GO="
set /p "GO=Start? (Y/N) [Y]: "
if /i "%GO%"=="N" goto :end

echo.
python "Build Pro Buyer Copy.py"
if errorlevel 1 (
    echo.
    echo  ! The build failed - see the messages above.
    goto :end
)
python "Build Installer.py"
if errorlevel 1 (
    echo.
    echo  ! The installer couldn't be made - see the messages above.
    goto :end
)

echo.
echo ============================================================
echo   Done. Fleet Manager Pro Setup.exe is in
echo     Build Output\Installers\Public
echo ============================================================
start "" explorer "%~dp0..\Build Output"

:end
echo.
pause
endlocal
