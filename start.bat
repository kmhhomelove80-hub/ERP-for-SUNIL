@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist requirements.txt goto missing
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv
  if errorlevel 1 goto python_missing
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error
.venv\Scripts\python.exe run.py
pause
exit /b
:python_missing
echo Python 3.12가 필요합니다. Python 3.12 설치 후 다시 실행하세요.
pause
exit /b
:missing
echo ZIP 전체를 먼저 압축 해제한 뒤 폴더 안의 start.bat을 실행하세요.
pause
exit /b
:error
echo 실행 준비에 실패했습니다. 위 오류 메시지를 확인해 주세요.
pause
