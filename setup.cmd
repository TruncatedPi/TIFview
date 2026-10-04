@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_windows.ps1" %*
if errorlevel 1 (
    echo TIFview setup failed. See the message above.
    pause
    exit /b 1
)
