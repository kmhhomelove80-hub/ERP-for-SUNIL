@echo off
cd /d "%~dp0"
if not exist ".laptop-venv\Scripts\python.exe" (
  echo Run LAPTOP-REAL-DATA.cmd setup first.
  pause
  exit /b 1
)
if not exist "laptop-real-data\trial.sqlite3" (
  echo Full-data trial database was not found in this folder.
  pause
  exit /b 1
)
py -3.12 scripts\laptop.py --real manage backup_database --directory laptop-real-data\manual-backups
if errorlevel 1 (
  echo Backup failed. Read the error above.
  pause
  exit /b 1
)
echo Backup complete. Folder: laptop-real-data\manual-backups
pause
