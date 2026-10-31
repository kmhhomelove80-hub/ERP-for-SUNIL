"""Back up the laptop DB before any pending schema upgrade."""
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from inventory.backups import create_backup, verify_backup


class Command(BaseCommand):
    help = '노트북 시험 DB 구조 업데이트 전 검증된 백업을 만듭니다. 구조 변경은 하지 않습니다.'
    requires_system_checks = []

    def handle(self, *args, **options):
        if settings.SETTINGS_MODULE not in ['config.laptop_settings','config.laptop_real_settings'] or connection.vendor != 'sqlite':
            raise CommandError('노트북 전용 SQLite 시험 설정에서만 실행합니다.')
        database = Path(settings.DATABASES['default']['NAME'])
        if not database.is_file():
            self.stdout.write('First install: no existing trial database to back up.')
            return
        try:
            # Refuse an unrelated SQLite file rather than treating it as an ERP.
            if not MigrationRecorder(connection).has_table():
                if connection.introspection.table_names():
                    raise ValidationError('기존 ERP 시험 DB가 아닙니다. 파일을 확인하세요.')
                self.stdout.write('First install: empty trial database.')
                return
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            if not plan:
                self.stdout.write('Schema is current. No upgrade backup needed.')
                return
            target = create_backup(database.parent / 'upgrade-backups')
            verify_backup(target)
        except (ValidationError, OSError) as error:
            raise CommandError(f'사전 백업 실패. 구조 업데이트를 중단합니다. {error}') from error
        self.stdout.write(f'Pre-upgrade backup verified: {target}')
        self.stdout.write(f'Pending migration steps: {len(plan)}. Original database unchanged.')
