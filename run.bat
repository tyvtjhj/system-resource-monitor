@echo off
rem Start the monitor.
rem Interpreter order: project .venv -> py -3.10 -> py -3 -> python.
rem server.py requests Administrator rights by itself (UAC) unless --no-elevate,
rem because CPU package temperature needs the PawnIO driver.
rem Extra arguments are passed through, e.g.:  run.bat --no-elevate
cd /d "%~dp0"

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

echo [run] python: %PY%
%PY% server.py %*
set "RC=%errorlevel%"

rem If elevation succeeded, server.py exits 0 right away and this window closes.
if not "%RC%"=="0" pause
exit /b %RC%
