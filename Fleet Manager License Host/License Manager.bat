@echo off
cd /d "%~dp0"
title Fleet Manager - License Manager
python license_tool.py %*
pause
