from pathlib import Path
import tempfile
import sqlite3
import os
from unittest import skipUnless
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TransactionTestCase
from .models import Item,Stock
from .backups import create_backup,verify_backup,restore_backup

@skipUnless(connection.vendor=='sqlite','SQLite 파일 복원 검증')
class SQLiteBackupTests(TransactionTestCase):
    def test_restore_preserves_data_and_refuses_overwrite(self):
        item=Item.objects.create(code='BACKUP-001');Stock.objects.create(item=item,location='A',quantity=7)
        with tempfile.TemporaryDirectory() as directory:
            backup=create_backup(directory)
            verify_backup(backup)
            target=Path(directory)/'restored.sqlite3'
            restore_backup(backup,sqlite_target=target)
            with sqlite3.connect(target) as restored:
                self.assertEqual(restored.execute('select quantity from inventory_stock').fetchone()[0],7)
                self.assertEqual(restored.execute('select code from inventory_item').fetchone()[0],'BACKUP-001')
            with self.assertRaises(ValidationError):restore_backup(backup,sqlite_target=target)
            if os.name != "nt":self.assertEqual(target.stat().st_mode&0o777,0o600)
    def test_corruption_stops_restore_before_target_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            backup=create_backup(directory)
            database=backup/'database.sqlite3'
            with database.open('ab') as f:f.write(b'corrupted')
            target=Path(directory)/'restored.sqlite3'
            with self.assertRaises(ValidationError):restore_backup(backup,sqlite_target=target)
            self.assertFalse(target.exists())
