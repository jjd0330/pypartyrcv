@echo off
setlocal

REM --- go to this script's directory (repo root) ---
cd /d "%~dp0"

REM --- activate venv if present ---
if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
)

REM --- start server in a new window ---
start "pyrcv webserver" cmd /k python -m pyrcv.webserver

REM --- open browser to the local server ---
start "" "http://127.0.0.1:5000/"

endlocal