from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import hashlib
import json
import tempfile
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase

from .backup_health import backup_health
from .backups import verify_backup


class BackupHealthTests(SimpleTestCase):
    now = datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc)

    def backup(self, root, name='sunil-good', age=2):
        folder = Path(root) / name
        folder.mkdir()
        data = b'checksum test fixture, not a database'
        (folder / 'database.dump').write_bytes(data)
        (folder / 'manifest.json').write_text(json.dumps({
            'format_version': 1, 'vendor': 'postgresql',
            'database_file': 'database.dump', 'sha256': hashlib.sha256(data).hexdigest(),
            'created_at': (self.now - timedelta(hours=age)).isoformat(),
        }))
        return folder

    def test_recent_storage_checks_without_database_or_file_changes(self):
        with tempfile.TemporaryDirectory() as root:
            self.backup(root)
            self.backup(root, 'sunil-older', age=50)
            before = {str(p): p.read_bytes() for p in Path(root).rglob('*') if p.is_file()}
            report = backup_health(root, now=self.now)
            self.assertTrue(report['ready'])
            self.assertEqual(report['valid_count'], 2)
            self.assertEqual(report['age_hours'], 2)
            self.assertEqual(before, {str(p): p.read_bytes() for p in Path(root).rglob('*') if p.is_file()})

    def test_old_missing_empty_and_future_backups_fail(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(backup_health(root, now=self.now)['ready'])
            self.assertFalse(backup_health(Path(root)/'offline', now=self.now)['ready'])
            folder = self.backup(root, age=27)
            self.assertFalse(backup_health(root, now=self.now)['ready'])
            manifest_path = folder/'manifest.json'
            data = json.loads(manifest_path.read_text())
            data['created_at'] = (self.now + timedelta(hours=1)).isoformat()
            manifest_path.write_text(json.dumps(data))
            self.assertEqual(backup_health(root, now=self.now)['invalid'], ['sunil-good'])
            with self.assertRaises(ValidationError): backup_health(root, max_age_hours=0)

    def test_corruption_and_interrupted_copy_fail_even_with_recent_good_backup(self):
        with tempfile.TemporaryDirectory() as root:
            self.backup(root)
            damaged = self.backup(root, 'sunil-damaged')
            (damaged/'database.dump').write_bytes(b'damaged')
            (Path(root)/'.sunil-partial-test').mkdir()
            report = backup_health(root, now=self.now)
            self.assertFalse(report['ready'])
            self.assertEqual(report['valid_count'], 1)
            self.assertEqual(report['invalid'], ['sunil-damaged'])
            self.assertEqual(report['partial_count'], 1)

    def test_malformed_manifest_is_reported_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.backup(root)
            for malformed in [b'[]', b'null', b'12', b'"text"', b'\xff']:
                (folder/'manifest.json').write_bytes(malformed)
                with self.assertRaises(ValidationError): verify_backup(folder)
                self.assertFalse(backup_health(root, now=self.now)['ready'])

    def test_command_json_and_nonzero_failure(self):
        with tempfile.TemporaryDirectory() as root:
            self.backup(root)
            out = StringIO()
            with patch('inventory.backup_health.datetime') as clock:
                clock.now.return_value = self.now
                clock.fromisoformat.side_effect = datetime.fromisoformat
                call_command('check_backups', root, stdout=out)
            self.assertTrue(json.loads(out.getvalue())['ready'])
            out = StringIO()
            with self.assertRaises(CommandError):
                call_command('check_backups', str(Path(root)/'offline'), stdout=out)
            self.assertFalse(json.loads(out.getvalue())['ready'])
