@echo off
title ShanghaiTech GenAI2API
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-windows.ps1" %*

echo.
echo GenAI2API has stopped. Press any key to close this window.
pause >nul
