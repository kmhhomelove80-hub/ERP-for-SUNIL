@echo off
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.12 is required. Run: py install 3.12
  pause
  exit /b 1
)
echo Close the other ERP server window before continuing.
pause
py -3.12 scripts\laptop.py --real setup
if errorlevel 1 goto failed
if exist "laptop-real-data\import-complete.txt" goto serve
py -3.12 scripts\laptop.py --real manage import_laptop_files
if errorlevel 1 goto failed
echo Full data imported>"laptop-real-data\import-complete.txt"
:serve
start "" http://127.0.0.1:8000/
py -3.12 scripts\laptop.py --real run
pause
exit /b
:failed
echo Setup or import failed. Read the error above.
pause
exit /b 1
