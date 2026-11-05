@echo off
rem Start the monitor.
rem Interpreter order: project .venv -> py -3.10 -> py -3 -> python.
rem Extra arguments are passed through to server.py.
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

if not "%RC%"=="0" pause
exit /b %RC%
