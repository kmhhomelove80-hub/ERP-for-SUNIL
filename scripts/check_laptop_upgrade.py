"""Exercise 0010 -> current laptop upgrade and restore using disposable data."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
for key in list(os.environ):
    if key.startswith('ERP_'):os.environ.pop(key)
os.environ['DJANGO_SETTINGS_MODULE']='config.laptop_real_settings'

with tempfile.TemporaryDirectory(prefix='sunil-upgrade-') as directory:
    from django.conf import settings
    settings.BASE_DIR=Path(directory)
    data=Path(directory)/'laptop-real-data';data.mkdir()
    settings.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':data/'trial.sqlite3'}}
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.contrib.auth.hashers import make_password
    from inventory.backups import verify_backup,restore_backup
    old_target=('inventory','0010_allocation_assigned_details_allocation_cancelled_at_and_more')
    call_command('migrate','inventory','0010',verbosity=0)
    apps=MigrationExecutor(connection).loader.project_state([old_target]).apps
    User=apps.get_model('auth','User');Item=apps.get_model('inventory','Item');Stock=apps.get_model('inventory','Stock')
    user=User.objects.create(username='upgrade-check',is_staff=True,is_superuser=True,password=make_password('synthetic-upgrade-password'))
    item=Item.objects.create(code='UPGRADE-CODE',name='가상 업데이트 검증')
    stock=Stock.objects.create(item=item,location='ORIGINAL',quantity=12,reserved=4)
    order=apps.get_model('inventory','PickingOrder').objects.create(actor=user,project='업데이트 전 호선',payload={'rows':[{'code':item.code,'quantity':4}]})
    apps.get_model('inventory','Allocation').objects.create(stock=stock,order=order,project=order.project,quantity=4,assigned_details={'code':item.code,'location':stock.location})
    receipt=apps.get_model('inventory','Receipt').objects.create(stock=stock,request_id=uuid.uuid4(),quantity=12,actor=user,payload={'location':stock.location})
    apps.get_model('inventory','AuditEvent').objects.create(actor=user,action='upgrade_fixture',reference=str(order.pk),detail={'quantity':12})
    call_command('prepare_laptop_upgrade')
    backups=list((data/'upgrade-backups').glob('sunil-*'));assert len(backups)==1
    _,manifest=verify_backup(backups[0]);assert manifest['vendor']=='sqlite'
    call_command('migrate',verbosity=0)
    from django.contrib.auth import get_user_model
    from inventory.models import Stock,Allocation,Receipt,Substitution,SubstitutionSettlement
    assert get_user_model().objects.get(username='upgrade-check').check_password('synthetic-upgrade-password')
    assert Stock.objects.get().quantity==12 and Stock.objects.get().reserved==4
    assert Allocation.objects.get().quantity==4 and Allocation.objects.get().assigned_details['location']=='ORIGINAL'
    assert Receipt.objects.get().arrival_stock_id is None
    assert not Substitution.objects.exists() and not SubstitutionSettlement.objects.exists()
    call_command('prepare_laptop_upgrade')
    assert len(list((data/'upgrade-backups').glob('sunil-*')))==1
    restored=Path(directory)/'restored-old.sqlite3'
    restore_backup(backups[0],sqlite_target=restored)
    with sqlite3.connect(restored) as db:
        assert db.execute('select quantity,reserved from inventory_stock').fetchone()==(12,4)
        columns=[row[1] for row in db.execute('pragma table_info(inventory_receipt)')]
        assert 'arrival_stock_id' not in columns
        assert db.execute("select count(*) from sqlite_master where name='inventory_substitution'").fetchone()[0]==0
        assert db.execute('pragma integrity_check').fetchone()[0]=='ok'
    connection.close()
    print('PASS: old laptop data/account/transactions preserved, verified pre-upgrade snapshot, new schema, no redundant backup, old-schema restore')
