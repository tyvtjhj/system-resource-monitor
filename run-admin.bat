@echo off
rem Start the monitor as Administrator.
rem CPU package temperature (LibreHardwareMonitor) needs admin rights to read MSR.
rem Without admin the monitor still works, but CPU temp comes from the ACPI thermal
rem zone (platform temperature, usually lower than the real core temperature).
rem Interpreter order: project .venv -> py -3.10 -> py -3 -> python.
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    py -3.10 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=py -3.10"
)
if not defined PY (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY set "PY=python"

echo [run-admin] python: %PY%
%PY% server.py %*
pause
