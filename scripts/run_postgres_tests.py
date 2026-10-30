"""Run tests on an isolated local PostgreSQL cluster; always stop and remove it.

Usage: python scripts/run_postgres_tests.py /path/to/extracted-postgresql
The root must contain usr/lib/postgresql/17/bin and usr/share/postgresql/17.
No public listener or persistent database is created.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent.parent
pgroot = Path(sys.argv[1]).resolve()
bin_dir = pgroot/'usr/lib/postgresql/17/bin'
share = pgroot/'usr/share/postgresql/17'
with tempfile.TemporaryDirectory(prefix='sunil-pg-') as directory:
    work = Path(directory)
    data, sock, log = work/'data',work/'socket',work/'postgres.log'
    sock.mkdir(mode=0o700)
    subprocess.run([str(bin_dir/'initdb'),'-D',str(data),'-L',str(share),'-U','sunil_test_owner','--no-locale','--encoding=UTF8','--auth-local=trust','--auth-host=reject'],check=True,stdout=subprocess.DEVNULL)
    started = False
    try:
        result = subprocess.run([str(bin_dir/'pg_ctl'),'-D',str(data),'-l',str(log),'-o',f"-k {sock} -c listen_addresses='' -p 55432",'-w','start'],capture_output=True,text=True)
        if result.returncode:
            raise RuntimeError(result.stdout+result.stderr+(log.read_text() if log.exists() else ''))
        started = True
        subprocess.run([str(bin_dir/'createdb'),'-h',str(sock),'-p','55432','-U','sunil_test_owner','sunil_validation'],check=True)
        environment = os.environ.copy()
        environment.update(ERP_DB_NAME='sunil_validation',ERP_DB_USER='sunil_test_owner',ERP_DB_PASSWORD='',ERP_DB_HOST=str(sock),ERP_DB_PORT='55432')
        result = subprocess.run([sys.executable,str(root/'manage.py'),'test','--noinput','--verbosity','1'],cwd=root,env=environment)
        if result.returncode: raise SystemExit(result.returncode)
        subprocess.run([sys.executable,str(root/'scripts/check_pg_backup.py'),'--pg-bin',str(bin_dir)],cwd=root,env=environment,check=True)
        print('PASS: PostgreSQL isolated tests and backup restore; temporary databases removed on exit')
    finally:
        if started:
            subprocess.run([str(bin_dir/'pg_ctl'),'-D',str(data),'-m','immediate','-w','stop'],check=True,stdout=subprocess.DEVNULL)
