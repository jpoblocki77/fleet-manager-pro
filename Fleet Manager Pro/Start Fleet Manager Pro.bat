@echo off
title Fleet Manager Pro
cd /d "%~dp0"
where python >nul 2>nul || (echo Python is not installed. Get it from https://www.python.org/downloads/ & pause & exit /b 1)
python -c "import flask, waitress, cryptography" 2>nul || (echo Installing Flask, Waitress and cryptography... & python -m pip install -r requirements.txt || (pause & exit /b 1))
python run.py %*
pause
