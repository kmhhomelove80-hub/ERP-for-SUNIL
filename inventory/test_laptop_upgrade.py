from io import StringIO
from pathlib import Path
from unittest.mock import patch
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, override_settings
from django.db import connection
from unittest import skipUnless


@skipUnless(connection.vendor=='sqlite','SQLite 노트북 업데이트 검증')
class LaptopUpgradeTests(SimpleTestCase):
    @override_settings(SETTINGS_MODULE='config.settings')
    def test_normal_settings_refused_without_database_access(self):
        with self.assertRaisesMessage(CommandError,'노트북 전용'):
            call_command('prepare_laptop_upgrade')

    @override_settings(SETTINGS_MODULE='config.laptop_real_settings')
    def test_first_install_does_not_create_database_or_backup(self):
        with patch('inventory.management.commands.prepare_laptop_upgrade.Path') as path:
            path.return_value.is_file.return_value=False
            with patch('inventory.management.commands.prepare_laptop_upgrade.create_backup') as backup:
                out=StringIO();call_command('prepare_laptop_upgrade',stdout=out)
                backup.assert_not_called()
                self.assertIn('First install',out.getvalue())

    @override_settings(SETTINGS_MODULE='config.laptop_real_settings')
    def test_existing_pending_schema_backup_must_verify_before_returning(self):
        module='inventory.management.commands.prepare_laptop_upgrade.'
        with patch(module+'Path') as path,patch(module+'MigrationRecorder') as recorder,patch(module+'MigrationExecutor') as executor,patch(module+'create_backup') as backup,patch(module+'verify_backup') as verify:
            path.return_value.is_file.return_value=True
            recorder.return_value.has_table.return_value=True
            executor.return_value.migration_plan.return_value=['pending']
            backup.return_value=Path('/tmp/upgrade-backup')
            out=StringIO();call_command('prepare_laptop_upgrade',stdout=out)
            backup.assert_called_once();verify.assert_called_once_with(backup.return_value)
            self.assertIn('verified',out.getvalue())
            backup.side_effect=OSError('no space')
            with self.assertRaisesMessage(CommandError,'구조 업데이트를 중단'):
                call_command('prepare_laptop_upgrade',stdout=StringIO())
