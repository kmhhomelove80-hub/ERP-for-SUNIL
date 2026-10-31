"""Python 3.12 laptop trial launcher. No production services or shared files."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / '.laptop-venv'
REAL = False
PYTHON = VENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def run(args, *, check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith('ERP_')}
    env['DJANGO_SETTINGS_MODULE'] = 'config.laptop_real_settings' if REAL else 'config.laptop_settings'
    return subprocess.run([str(PYTHON), *args], cwd=ROOT, env=env, check=check)


def main():
    global REAL
    parser = argparse.ArgumentParser(description='SUNIL ERP laptop trial')
    parser.add_argument('--real', action='store_true', help='Separate trial using original Excel files')
    parser.add_argument('action', choices=['setup', 'run', 'manage'])
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error('Install Python 3.12 and run this script with Python 3.12.')
    REAL = args.real
    (ROOT / ('laptop-real-data' if REAL else 'laptop-data')).mkdir(exist_ok=True)
    if args.action == 'setup':
        if not PYTHON.exists():
            venv.EnvBuilder(with_pip=True).create(VENV)
        run(['-m', 'pip', 'install', '-r', 'requirements.txt'])
        run(['manage.py', 'migrate', '--noinput'])
        if not REAL:
            run(['manage.py', 'seed_laptop_demo'])
        result = run(['manage.py', 'shell', '-c',
             "from django.contrib.auth import get_user_model; import sys; sys.exit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 3)"], check=False)
        if result.returncode == 3:
            print('Create your local administrator account. Password input is hidden.', flush=True)
            run(['manage.py', 'createsuperuser'])
        elif result.returncode:
            raise subprocess.CalledProcessError(result.returncode, result.args)
        print('Setup complete. Local trial database only.', flush=True)
    elif args.action == 'run':
        if not PYTHON.exists():
            parser.error('Run setup first.')
        print('Open http://127.0.0.1:8000/ in your browser. Stop: Ctrl+C.', flush=True)
        run(['manage.py', 'runserver', '127.0.0.1:8000', '--noreload'])
    else:
        if not PYTHON.exists() or not args.arguments:
            parser.error('Run setup first; then supply a management command.')
        run(['manage.py', *args.arguments])


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        print('Command failed. Check the error above; do not share passwords.', file=sys.stderr)
        sys.exit(error.returncode)
    except KeyboardInterrupt:
        pass
