@echo off
rem Start the monitor. server.py requests Administrator rights by itself (UAC),
rem because CPU package temperature needs the PawnIO driver.
rem To skip elevation, run:  py -3.10 server.py --no-elevate
rem The interpreter matters: PATH python may be 3.13 without flask/psutil,
rem dependencies live in Python 3.10, so prefer py -3.10.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.10 server.py
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python server.py
    goto :end
)

echo [ERROR] Python not found. Install Python 3.10+ and add it to PATH.
pause
exit /b 1

:end
rem If elevation succeeded, server.py exits 0 right away and this window closes.
if %errorlevel% neq 0 pause
