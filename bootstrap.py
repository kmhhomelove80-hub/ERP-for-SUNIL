"""Windows 실행 준비. 모든 설치 명령은 인수 목록으로 실행합니다."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

def main():
    os.chdir(ROOT)
    if not (3, 12) <= sys.version_info[:2] <= (3, 14):
        print('Python 3.12~3.14가 필요합니다. Python 3.12 설치를 권장합니다.')
        print('현재 Python:', sys.version.split()[0])
        print('https://www.python.org/downloads/windows/')
        return 1
    requirements = ROOT / 'requirements.txt'
    if not requirements.is_file() or not (ROOT / 'run.py').is_file():
        print('프로그램 파일이 없습니다. ZIP 전체를 압축 해제한 뒤 다시 실행하세요.')
        return 1
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        print('첫 실행 준비: Python 가상환경을 만들고 있습니다…', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = ROOT / '.venv' / '.requirements-sha256'
    if not marker.exists() or marker.read_text().strip() != fingerprint:
        print('필요한 패키지를 설치합니다. 첫 실행에는 인터넷 연결이 필요합니다.', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
        marker.write_text(fingerprint)
    print('웹앱을 시작합니다. 종료하려면 이 창에서 Ctrl+C를 누르세요.', flush=True)
    return subprocess.call([str(python), str(ROOT / 'run.py')])

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print('실행 준비에 실패했습니다. 위의 설치 오류와 인터넷 연결을 확인하세요.')
        raise SystemExit(exc.returncode)
    except KeyboardInterrupt:
        print('\n종료했습니다.')
        raise SystemExit(0)
    except OSError as exc:
        print('파일 또는 Python 실행 오류:', exc)
        raise SystemExit(1)
