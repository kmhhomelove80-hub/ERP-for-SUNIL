@echo off
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.12 from python.org with Python Launcher enabled.
  pause
  exit /b 1
)
py -3.12 scripts\laptop.py setup
if errorlevel 1 (
  echo Setup failed. Read the error above.
  pause
  exit /b 1
)
start "" http://127.0.0.1:8000/
py -3.12 scripts\laptop.py run
pause
