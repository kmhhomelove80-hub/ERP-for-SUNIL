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
# Preserve a complete loan -> production receipt -> repayment chain, not only
# empty new tables, when checking the backup's foreign keys and audit records.
import uuid
from inventory.picking import parse_paste,plan_picking,confirm_picking
from inventory.substitutions import reserve_substitution,dispatch_substitution,settle_substitution,confirm_company_erp
from inventory.services import receive_stock
actor.is_staff=True;actor.save(update_fields=['is_staff'])
request_item=Item.objects.create(code='BACKUP-REQUEST-A')
actual_item=Item.objects.create(code='BACKUP-ACTUAL-B')
actual_stock=Stock.objects.create(item=actual_item,location='LOAN-OLD',quantity=3)
rows=parse_paste('BACKUP-REQUEST-A\t3')
order=confirm_picking(request_id=uuid.uuid4(),project='backup-loan',rows=rows,preview=plan_picking(rows),allow_shortage=True,actor=actor,substitution_draft=True)
loan=reserve_substitution(request_id=uuid.uuid4(),order_id=order.pk,requested=request_item.code,stock_id=actual_stock.pk,quantity=3,reason='synthetic equivalent product',actor=actor)
dispatch_substitution(substitution_id=loan.pk,shipped_at=timezone.now(),product_confirmed=True,actor=actor)
receipt=receive_stock(item_id=request_item.pk,location='LOAN-NEW',quantity=3,status='received',expected_date=None,sender='',request_id=uuid.uuid4(),actor=actor)
settle_substitution(request_id=uuid.uuid4(),substitution_id=loan.pk,receipt_id=receipt.pk,quantity=3,note='synthetic production repayment',product_confirmed=True,actor=actor)
confirm_company_erp(substitution_id=loan.pk,confirmed=True,note='synthetic ERP confirmation',actor=actor)
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
        assert restored.execute('SELECT quantity,reserved FROM inventory_stock WHERE item_id=%s',(item.pk,)).fetchone()==(7,2)
        assert restored.execute("SELECT detail FROM inventory_auditevent WHERE action='backup_test'").fetchone()[0]['quantity']==7
        assert restored.execute("SELECT a.state,s.erp_confirmed_at IS NOT NULL FROM inventory_substitution s JOIN inventory_allocation a ON a.id=s.allocation_id").fetchone()==('settled',True)
        assert restored.execute('SELECT quantity,location FROM inventory_substitutionsettlement').fetchone()==(3,'LOAN-NEW')
    second=subprocess.run([sys.executable,str(root/'manage.py'),'restore_database',str(backup),'--apply','--pg-bin',args.pg_bin],cwd=root,env=restore_env,capture_output=True,text=True)
    assert second.returncode!=0 and '비어 있는 새 PostgreSQL DB' in second.stderr
    print('PASS: PostgreSQL dump, verified simulated-NAS copy/restore, all table counts, stock/reservation/audit and complete substitution/repayment chain, overwrite refusal')
