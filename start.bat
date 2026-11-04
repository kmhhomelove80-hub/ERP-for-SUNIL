@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
if errorlevel 1 goto path_error
if not exist "bootstrap.py" goto missing
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 goto py312
py -3.13 -c "import sys" >nul 2>&1
if not errorlevel 1 goto py313
py -3.14 -c "import sys" >nul 2>&1
if not errorlevel 1 goto py314
python "bootstrap.py"
goto done
:py312
py -3.12 "bootstrap.py"
goto done
:py313
py -3.13 "bootstrap.py"
goto done
:py314
py -3.14 "bootstrap.py"
goto done
:missing
echo Please extract the entire ZIP before running start.bat.
goto done
:path_error
echo Unable to open the application folder.
:done
pause
endlocal
