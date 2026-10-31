@echo off
cd /d "%~dp0"
if not exist "laptop-real-data\trial.sqlite3" (
  echo Run LAPTOP-REAL-DATA.cmd first, or copy your laptop-real-data folder here.
  pause
  exit /b 1
)
echo Stop the other ERP server window with Ctrl+C first.
echo Use a trusted Private Wi-Fi network. Do not configure router port forwarding.
pause
py -3.12 scripts\laptop.py --real setup
if errorlevel 1 goto failed
py -3.12 scripts\laptop.py --real --wifi run
pause
exit /b
:failed
echo Setup failed. Read the error above.
pause
exit /b 1
