"""Run only against the disposable cluster created by run_postgres_tests.py."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(root))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.db import connection
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from inventory.models import Item,Stock,AuditEvent
from inventory.backups import create_backup,restore_backup,copy_backup_to_storage
from django.core.exceptions import ValidationError

parser=argparse.ArgumentParser();parser.add_argument('--pg-bin',required=True);args=parser.parse_args()
if connection.vendor!='postgresql' or os.environ.get('ERP_DB_NAME')!='sunil_validation' or not os.environ.get('ERP_DB_HOST','').startswith('/tmp/sunil-pg-'):
    raise RuntimeError('This check only runs in the isolated test cluster')
call_command('migrate',verbosity=0)
actor=get_user_model().objects.create_user('backup-test')
item=Item.objects.create(code='BACKUP-PG-001');Stock.objects.create(item=item,location='A',quantity=7,reserved=2)
AuditEvent.objects.create(actor=actor,action='backup_test',reference='sample',detail={'quantity':7})
with connection.cursor() as cursor:
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    tables=[r[0] for r in cursor.fetchall()]
    counts={}
    for table in tables:
        cursor.execute('SELECT count(*) FROM '+connection.ops.quote_name(table));counts[table]=cursor.fetchone()[0]
with tempfile.TemporaryDirectory(prefix='sunil-backup-') as directory:
    backup=create_backup(Path(directory)/'local',args.pg_bin)
    storage=Path(directory)/'simulated-nas';storage.mkdir()
    backup=copy_backup_to_storage(backup,storage)
    env=os.environ.copy();env.update(PGHOST=env['ERP_DB_HOST'],PGPORT=env['ERP_DB_PORT'],PGUSER=env['ERP_DB_USER'],PGPASSWORD='')
    subprocess.run([str(Path(args.pg_bin)/'createdb'),'sunil_restored'],env=env,check=True)
    restore_env=os.environ.copy();restore_env['ERP_DB_NAME']='sunil_restored'
    subprocess.run([sys.executable,str(root/'manage.py'),'restore_database',str(backup),'--apply','--pg-bin',args.pg_bin],cwd=root,env=restore_env,check=True)
    import psycopg
    with psycopg.connect(host=env['PGHOST'],port=env['PGPORT'],user=env['PGUSER'],dbname='sunil_restored') as restored:
        from psycopg import sql
        for table,count in counts.items():
            actual=restored.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
            assert actual==count,(table,count,actual)
        assert restored.execute('SELECT quantity,reserved FROM inventory_stock').fetchone()==(7,2)
        assert restored.execute('SELECT detail FROM inventory_auditevent').fetchone()[0]['quantity']==7
    second=subprocess.run([sys.executable,str(root/'manage.py'),'restore_database',str(backup),'--apply','--pg-bin',args.pg_bin],cwd=root,env=restore_env,capture_output=True,text=True)
    assert second.returncode!=0 and '비어 있는 새 PostgreSQL DB' in second.stderr
    print('PASS: PostgreSQL dump, verified simulated-NAS copy/restore, all table counts, stock/reservation/audit data, overwrite refusal')
