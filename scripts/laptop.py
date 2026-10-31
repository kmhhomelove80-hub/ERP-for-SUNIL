"""Python 3.12 laptop trial launcher. No production services or shared files."""
import argparse
import os
import socket
from ipaddress import ip_address, ip_network
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / '.laptop-venv'
REAL = False
WIFI_IP = None
PYTHON = VENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def run(args, *, check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith('ERP_')}
    env.pop('SUNIL_LAPTOP_WIFI_IP',None)
    env.pop('SUNIL_LAPTOP_WIFI_REAL',None)
    env['DJANGO_SETTINGS_MODULE'] = 'config.laptop_real_settings' if REAL else 'config.laptop_settings'
    if WIFI_IP:
        env['DJANGO_SETTINGS_MODULE']='config.laptop_wifi_settings'
        env['SUNIL_LAPTOP_WIFI_IP']=WIFI_IP
        env['SUNIL_LAPTOP_WIFI_REAL']='1' if REAL else '0'
    return subprocess.run([str(PYTHON), *args], cwd=ROOT, env=env, check=check)


def private_ipv4(value):
    try:
        address=ip_address(value)
        return address.version==4 and any(address in ip_network(network) for network in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'])
    except ValueError:
        return False


def choose_wifi_ip(explicit=None):
    if explicit:
        if not private_ipv4(explicit): raise ValueError('Use a private Wi-Fi IPv4 address from ipconfig.')
        return explicit
    addresses=sorted({row[4][0] for row in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET) if private_ipv4(row[4][0])})
    if not addresses:
        raise ValueError('No private IPv4 found. Check Wi-Fi, then use --wifi-ip with ipconfig IPv4.')
    if len(addresses)==1:return addresses[0]
    print('Choose the Wi-Fi IPv4 address matching ipconfig:')
    for index,value in enumerate(addresses,1):print(f'{index}: {value}')
    selected=int(input('Number: '))
    if not 1<=selected<=len(addresses):raise ValueError('Invalid address selection.')
    return addresses[selected-1]


def main():
    global REAL, WIFI_IP
    parser = argparse.ArgumentParser(description='SUNIL ERP laptop trial')
    parser.add_argument('--wifi',action='store_true',help='Temporary private Wi-Fi access (run only)')
    parser.add_argument('--wifi-ip',help='Private IPv4 shown by Windows ipconfig')
    parser.add_argument('--real', action='store_true', help='Separate trial using original Excel files')
    parser.add_argument('action', choices=['setup', 'run', 'manage'])
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error('Install Python 3.12 and run this script with Python 3.12.')
    if (args.wifi or args.wifi_ip) and args.action != 'run':
        parser.error('Wi-Fi options are only for run.')
    REAL = args.real
    (ROOT / ('laptop-real-data' if REAL else 'laptop-data')).mkdir(exist_ok=True)
    if args.action == 'setup':
        if not PYTHON.exists():
            venv.EnvBuilder(with_pip=True).create(VENV)
        run(['-m', 'pip', 'install', '-r', 'requirements.txt'])
        run(['manage.py', 'prepare_laptop_upgrade'])
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
        host='127.0.0.1'
        if args.wifi or args.wifi_ip:
            host=choose_wifi_ip(args.wifi_ip)
            WIFI_IP=host
        print(f'Open http://{host}:8000/ on your laptop or phone. Stop: Ctrl+C.', flush=True)
        command=['manage.py','runserver',f'{host}:8000','--noreload']
        if WIFI_IP:
            # Django's flag serves static assets with DEBUG=False. It does not
            # disable TLS checks or change the selected private bind address.
            command.append('--insecure')
        run(command)
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
    except (ValueError,OSError) as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
