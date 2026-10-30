import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase,TestCase
from .backups import copy_backup_to_storage,verify_backup,_pg_program
from .models import Item,Stock,Allocation
from .readiness import readiness_report

class StorageCopyTests(SimpleTestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.source=self.root/'local'/'sunil-sample';self.source.mkdir(parents=True)
        self.storage=self.root/'nas';self.storage.mkdir()
        self.data=b'consistent-backup-sample'
        (self.source/'database.sqlite3').write_bytes(self.data)
        self.manifest={'format_version':1,'vendor':'sqlite','database_file':'database.sqlite3','sha256':hashlib.sha256(self.data).hexdigest(),'created_at':'2026-10-31T00:00:00Z'}
        (self.source/'manifest.json').write_text(json.dumps(self.manifest))
    def test_verified_copy_and_idempotent_repeat(self):
        target=copy_backup_to_storage(self.source,self.storage)
        self.assertEqual((target/'database.sqlite3').read_bytes(),self.data)
        verify_backup(target)
        self.assertEqual(copy_backup_to_storage(self.source,self.storage),target)
        self.assertEqual(len(list(self.storage.iterdir())),1)
    def test_offline_storage_does_not_create_directory_or_remove_local(self):
        offline=self.root/'offline'
        with self.assertRaises(ValidationError):copy_backup_to_storage(self.source,offline)
        self.assertFalse(offline.exists());verify_backup(self.source)
    def test_interrupted_copy_cleans_partial_and_keeps_local(self):
        with patch('inventory.backups.shutil.copyfile',side_effect=OSError('offline')):
            with self.assertRaises(OSError):copy_backup_to_storage(self.source,self.storage)
        self.assertEqual(list(self.storage.iterdir()),[]);verify_backup(self.source)
    def test_corruption_never_becomes_completed_backup(self):
        import shutil
        original=shutil.copyfile
        def corrupt(source,dest):
            original(source,dest)
            if Path(dest).name=='database.sqlite3':Path(dest).write_bytes(b'corrupted')
        with patch('inventory.backups.shutil.copyfile',side_effect=corrupt):
            with self.assertRaises(ValidationError):copy_backup_to_storage(self.source,self.storage)
        self.assertEqual(list(self.storage.iterdir()),[]);verify_backup(self.source)
    def test_different_existing_backup_is_not_overwritten(self):
        target=copy_backup_to_storage(self.source,self.storage)
        (target/'database.sqlite3').write_bytes(b'different')
        manifest=dict(self.manifest,sha256=hashlib.sha256(b'different').hexdigest())
        (target/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValidationError):copy_backup_to_storage(self.source,self.storage)
        self.assertEqual((target/'database.sqlite3').read_bytes(),b'different')
    def test_windows_executable_suffix_resolution(self):
        program=self.root/'pg_dump.exe';program.write_bytes(b'program-placeholder')
        with patch('inventory.backups.sys.platform','win32'):
            self.assertEqual(_pg_program('pg_dump',self.root),str(program))

class ReadinessTests(TestCase):
    def setUp(self):
        self.item=Item.objects.create(code='OPS1')
        self.stock=Stock.objects.create(item=self.item,location='A',quantity=10,reserved=2)
    def test_detects_mismatch_without_repairing_it(self):
        report=readiness_report()
        self.assertFalse(report['ready'])
        self.assertEqual(next(c['status'] for c in report['checks'] if c['key']=='reservations'),'error')
        self.stock.refresh_from_db();self.assertEqual(self.stock.reserved,2)
        Allocation.objects.create(stock=self.stock,project='호선',quantity=2)
        self.assertTrue(readiness_report()['ready'])
    def test_staff_dashboard_and_viewer_restriction(self):
        viewer=get_user_model().objects.create_user('viewer')
        self.client.force_login(viewer);self.assertEqual(self.client.get('/operations/').status_code,302)
        viewer.is_staff=True;viewer.save()
        self.assertContains(self.client.get('/operations/'),'예약 수량 대조')
    def test_missing_expected_date_is_warning(self):
        self.stock.reserved=0;self.stock.status='expected';self.stock.save()
        report=readiness_report()
        self.assertTrue(report['ready']);self.assertEqual(report['warnings'],1)
