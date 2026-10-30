"""Start a temporary local server, check login/CSRF, then stop it."""
import argparse
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
from pathlib import Path

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
group=parser.add_mutually_exclusive_group()
group.add_argument('--gunicorn',action='store_true')
group.add_argument('--waitress',action='store_true')
args = parser.parse_args()
with tempfile.TemporaryFile() as log:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    command = [sys.executable,'-m','gunicorn','config.wsgi:application','--bind',f'127.0.0.1:{port}','--workers','1','--threads','2'] if args.gunicorn else [sys.executable,str(root/'manage.py'),'runserver',f'127.0.0.1:{port}','--noreload']
    if args.waitress:command=[sys.executable,'-m','waitress',f'--listen=127.0.0.1:{port}','--threads=4','--max-request-body-size=12582912','config.wsgi:application']
    process = subprocess.Popen(command,cwd=root,stdout=log,stderr=log)
    try:
        for _ in range(50):
            if process.poll() is not None:
                log.seek(0)
                raise RuntimeError(log.read().decode())
            try:
                with urlopen(f'http://127.0.0.1:{port}/',timeout=1) as response:
                    body = response.read().decode()
                    assert response.status == 200
                    assert '/login/' in response.url
                    assert 'csrfmiddlewaretoken' in body
                    assert '창고 관리 로그인' in body
                print('PASS: server startup, unauthenticated redirect, login page and CSRF token')
                break
            except URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError('Server readiness timed out')
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)
