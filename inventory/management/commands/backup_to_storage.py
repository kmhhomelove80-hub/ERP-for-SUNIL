from django.core.management.base import BaseCommand,CommandError
from django.core.exceptions import ValidationError
from inventory.backups import create_backup,copy_backup_to_storage

class Command(BaseCommand):
    help='로컬 일관 백업 후 NAS/별도 저장소에 검증된 복사본을 생성합니다.'
    def add_arguments(self,parser):
        parser.add_argument('--directory',required=True)
        parser.add_argument('--storage',required=True)
        parser.add_argument('--pg-bin')
    def handle(self,*args,**options):
        local=None
        try:
            local=create_backup(options['directory'],options['pg_bin'])
            copied=copy_backup_to_storage(local,options['storage'])
        except (ValidationError,OSError) as error:
            message=f'별도 저장소 백업 실패: {error}'
            if local:message+=f' 로컬 백업은 보존됨: {local}'
            raise CommandError(message) from error
        self.stdout.write(f'로컬 백업: {local}\n검증된 별도 복사본: {copied}')
