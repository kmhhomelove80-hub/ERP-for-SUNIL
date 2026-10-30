import json
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from inventory.backup_health import backup_health


class Command(BaseCommand):
    help = '완료 백업 전체의 체크섬/최신 시각/중단 복사를 DB 연결 없이 점검합니다. 파일을 수정하지 않습니다.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('directory')
        parser.add_argument('--max-age-hours', type=int, default=26)

    def handle(self, *args, **options):
        try:
            report = backup_health(options['directory'], max_age_hours=options['max_age_hours'])
        except ValidationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        if not report['ready']:
            raise CommandError('백업 점검 실패. 파일은 수정하지 않았습니다.')
