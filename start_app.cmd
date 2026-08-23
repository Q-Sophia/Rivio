@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_app.ps1"
if errorlevel 1 (
    echo.
    echo Startup failed. Check the message above.
    pause
)
