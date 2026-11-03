@echo off
rem Start the monitor as Administrator.
rem CPU package temperature (LibreHardwareMonitor) needs admin rights to read MSR.
rem Without admin the monitor still works, but CPU temp comes from ACPI thermal zone
rem (platform temperature, usually lower than the real core temperature).
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.10 server.py
) else (
    python server.py
)

pause
