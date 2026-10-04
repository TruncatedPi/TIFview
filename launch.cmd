@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Run the setup instructions in README.md first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m tifview %*
