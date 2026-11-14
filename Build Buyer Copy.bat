@echo off
setlocal EnableExtensions
title Fleet Manager Pro - Build a Buyer Copy
cd /d "%~dp0"

echo ============================================================
echo   Fleet Manager Pro - Build a copy for a buyer
echo ============================================================
echo.
echo  Each copy you sell is built for ONE purchase key. The key is hidden
echo  inside the program, and the copy's pages only unlock with that
echo  buyer's licence, so a leaked copy can be traced back to the buyer.
echo.
echo  Before you start:
echo    1. Open License Manager and choose  2) New purchase  to get the
echo       buyer's key (FMP-XXXX-XXXX-XXXX-XXXX).
echo    2. Make sure the licence server PC has the latest license_server.py,
echo       or new copies can't unlock their pages.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo  ! Python isn't installed or isn't on PATH. Install Python, then try again.
    goto :end
)

:askkey
set "KEY="
set /p "KEY=Buyer's purchase key (or type STOCK for a copy with no buyer): "
if "%KEY%"=="" goto :askkey
if /i "%KEY%"=="STOCK" (
    set "BUYERARG="
    set "WHO=no buyer (stock copy - can't be traced to anyone)"
    goto :askversion
)
python -c "import re,sys; sys.exit(0 if re.fullmatch(r'FMP-[A-Z0-9]{4}(-[A-Z0-9]{4}){3}', sys.argv[1].strip().upper()) else 1)" "%KEY%"
if errorlevel 1 (
    echo  ! That doesn't look like a purchase key. It should look like FMP-ABCD-EFGH-IJKL-MNOP.
    echo.
    goto :askkey
)
for /f "usebackq delims=" %%K in (`python -c "import sys; print(sys.argv[1].strip().upper())" "%KEY%"`) do set "KEY=%%K"
set "BUYERARG=--buyer %KEY%"
set "WHO=%KEY%"

:askversion
echo.
echo  Which version?
echo    1) Standard (no .exe) - recommended, antivirus-friendly
echo    2) Single program (.exe)
echo    3) Both
set "VER="
set /p "VER=Choose 1, 2 or 3 [1]: "
if "%VER%"=="" set "VER=1"
if not "%VER%"=="1" if not "%VER%"=="2" if not "%VER%"=="3" (
    echo  ! Please type 1, 2 or 3.
    goto :askversion
)

echo.
echo  Building for: %WHO%
if "%VER%"=="1" echo  Version:      Standard (no .exe)
if "%VER%"=="2" echo  Version:      Single program (.exe)
if "%VER%"=="3" echo  Version:      Both
echo  This takes a few minutes per version.
set "GO="
set /p "GO=Start? (Y/N) [Y]: "
if /i "%GO%"=="N" goto :end

if "%VER%"=="1" call :build "Build Pro Buyer Copy (no exe).py"
if "%VER%"=="2" call :build "Build Pro Buyer Copy.py"
if "%VER%"=="3" (
    call :build "Build Pro Buyer Copy (no exe).py"
    if not errorlevel 1 call :build "Build Pro Buyer Copy.py"
)
if errorlevel 1 (
    echo.
    echo  ! The build failed - see the messages above.
    goto :end
)

echo.
echo ============================================================
echo   Done. Send the buyer the .zip shown above (in Build Output).
echo   Your private list of who got which copy:
echo     Development\Buyer Builds (PRIVATE).csv
echo ============================================================
start "" explorer "%~dp0..\Build Output"
goto :end

:build
echo.
echo ---- %~1 ----
python %1 %BUYERARG%
exit /b %errorlevel%

:end
echo.
pause
endlocal
